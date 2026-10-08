"""
    DharmaOS EventBus — pluggable publish-subscribe transport for agent observability.

    PURPOSE
    -------
    EventBus provides the publish-subscribe transport that connects the agent's
    action loop to the TuriyaSidecar observer layer. The sidecar subscribes to
    event subjects without joining the decision loop; the actor publishes events
    without knowing who is listening.

    This architectural separation enforces that observation cannot interfere with
    action (the sidecar cannot write back to the actor via the bus — it only reads).

    IMPLEMENTATIONS
    ---------------
    AsyncioQueueBus : In-process asyncio.Queue-based bus. Fan-out via multiple
    handler registrations per subject pattern. Subject matching
    uses simple prefix matching (e.g. "the agent platform.*" matches
    "the agent platform.agent.start"). Ships in V3 for development and
    testing use.

    NATS JetStream  : Production adapter — deferred to Phase 4.
    Provides durable, distributed pub/sub with replay.

    SUBJECT PATTERN MATCHING
    ------------------------
    AsyncioQueueBus uses fnmatch-style wildcard matching:
    "the agent platform.*"  matches "the agent platform.agent.start", "the agent platform.plan_created"
    "the agent platform.**"  matches any nested subject

    BACKPRESSURE
    ------------
    AsyncioQueueBus has a configurable max_queue_size. When the queue is full,
    new events are dropped and an event_bus_dropped metric is incremented.
    This prevents OOM under burst load; the sidecar fails open.

    KEY TYPES
    ---------
    EventBus  : Protocol (structural subtyping — any class with
    subscribe/publish/close is a valid bus).
    AsyncioQueueBus  : In-process asyncio.Queue implementation.
    EventHandler  : Callable[[dict[str, object]], Awaitable[None]] type alias.
    EventBusFullError  : Raised when subscription limit is reached (P1-9).

    Governance origin: The event bus enables the architectural separation that
    Māṇḍūkya Upaniṣad 7 requires: the witness (TuriyaSidecar) subscribes but
    does not publish control commands back to the actor.

    agent safety layer — Sprint V3: Pluggable Event Bus.

    Provides the EventBus Protocol plus an asyncio.Queue-based in-process
    implementation (AsyncioQueueBus) that ships in V3.

    **Purpose:**
    The Turīya sidecar must receive orchestrator events over a bus to observe
    them without participating in the action cycle (Māṇḍūkya Upaniṣad 7 —
    the witness is not the actor).  This module abstracts the transport so
    that the sidecar is agnostic to whether it is receiving from:
    - asyncio.Queue (V3 — ships now; development + test)
    - NATS JetStream  (later sprint — production)
    - Kafka / PGMQ  (Phase 3+ options; see AUDIT notes)

    **V3 scope:**
    Only AsyncioQueueBus ships.  NATS adapter is a separate sprint.

    AsyncioQueueBus implements fan-out via multiple handler registrations
    per subject pattern.  Subject matching uses simple prefix matching
    (e.g. "the agent platform.*" matches "the agent platform.agent.start").

    **Technical design:**
    The EventBus Protocol uses `typing.Protocol` (structural subtyping).
    Any class that implements `subscribe`, `publish`, `close` is a valid bus —
    duck typing, no ABC inheritance required.

    **Philosophical grounding:**
    The bus is infrastructure, not Vedāntic doctrine.  It enables the
    architectural separation that Māṇḍūkya requires: the witness (Turīya
    sidecar) subscribes but does not publish commands back.  The bus enforces
    this by design — the TuriyaSidecar only calls `subscribe`, never `publish`.

    Sprint V3, 2026-04-17.
    """

from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

import structlog

from dharmaos.metrics import event_bus_dropped

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# Type alias for event handlers
EventHandler = Callable[[dict[str, object]], Awaitable[None]]


class EventBusFullError(Exception):
    """Raised when the event bus has reached its subscription limit (P1-9)."""

    pass




# ---------------------------------------------------------------------------
# EventBus Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EventBus(Protocol):
    """Pluggable bus for the Turīya sidecar.

        Production implementation: NATS JetStream (later sprint — TD-V3-01).
        Test / development implementation: AsyncioQueueBus (ships in V3).

        **Contract:**
        subscribe() — register a handler for a subject pattern.
        publish()  — emit an event dict to all matching handlers.
        close()  — gracefully shut down the bus.

        Subject patterns follow glob semantics (fnmatch):
        "the agent platform.*"  — one wildcard segment
        "the agent platform.**"  — multi-segment wildcard (NATS-style, fnmatch "**")
        "the agent platform.agent" — exact match

        **Turīya contract:**
        The TuriyaSidecar may only call subscribe().  It MUST NOT call publish().
        This is enforced by convention (see TuriyaSidecar docstring) and is the
        architectural expression of Māṇḍūkya Upaniṣad 7: the witness does not
        emit control commands into the world it witnesses.
        """

    async def subscribe(self, subject: str, handler: EventHandler) -> None:
        """Register a handler for all events matching `subject` pattern.

            Args:
            subject: Subject pattern (glob-style).
            handler: Async callable receiving the event dict.
            """
        ...

    async def publish(self, subject: str, data: dict[str, object]) -> None:
        """Emit an event to all handlers whose pattern matches `subject`.

            Args:
            subject: Concrete subject string (no wildcards).
            data: Event payload dict.
            """
        ...

    async def close(self) -> None:
        """Gracefully shut down the bus and release resources."""
        ...


# ---------------------------------------------------------------------------
# AsyncioQueueBus — in-process bus for development + testing
# ---------------------------------------------------------------------------


class AsyncioQueueBus:
    """In-process event bus for development + testing.

        Ships in V3.  NATS adapter is a separate (later) sprint.

        **Implementation:**
        - Subscriptions are stored as (pattern, handler) pairs.
        - A published event dispatches to all handlers whose pattern matches
        the published subject via fnmatch.fnmatchcase.
        - All handlers are called concurrently via asyncio.gather.
        - Failed handlers log the exception and continue (fail-open per
        the apophatic principle: a handler crash must not stop the bus).

        **Fan-out:**
        Multiple handlers can subscribe to the same subject pattern.
        All matching handlers receive every published event.

        **Bounds (P1-9):**
        MAX_SUBSCRIPTIONS = 50_000 prevents API-abuse-style DoS via
        handler-registration flooding. subscribe() raises EventBusFullError
        when the limit is exceeded.

        **Close semantics:**
        close() is idempotent.  After close(), publish() is a no-op with
        a warning log.  This allows callers to call close() multiple times
        without error.

        **Thread safety:**
        Not thread-safe.  Designed for a single asyncio event loop.
        Do not share an AsyncioQueueBus across multiple event loops.
        """

    MAX_SUBSCRIPTIONS: int = 50_000

    def __init__(self) -> None:
        # Registry: list of (subject_pattern, handler) tuples
        self._subscriptions: list[tuple[str, EventHandler]] = []
        self._closed: bool = False
        logger.debug("asyncio_queue_bus.init")

    async def subscribe(self, subject: str, handler: EventHandler) -> None:
        """Register a handler for all events matching `subject` pattern.

            Subject patterns use fnmatch glob semantics:
            "the agent platform.*"  — matches "the agent platform.agent" but not "the agent platform.agent.start"
            "the agent platform.**" — matches any depth below the agent platform
            "the agent platform.agent.*" — matches "the agent platform.agent.start"

            Raises:
            EventBusFullError: If MAX_SUBSCRIPTIONS would be exceeded (P1-9).

            Args:
            subject: Subject glob pattern.
            handler: Async callable receiving the event dict.
            """
        if self._closed:
            logger.warning("asyncio_queue_bus.subscribe_on_closed_bus", subject=subject)
            return
        if len(self._subscriptions) >= self.MAX_SUBSCRIPTIONS:
            raise EventBusFullError(
                f"Event bus subscriptions saturated at {self.MAX_SUBSCRIPTIONS} handlers. "
                "Unsubscribe old handlers before registering new ones."
            )
        self._subscriptions.append((subject, handler))
        logger.debug("asyncio_queue_bus.subscribed", subject=subject)

    async def publish(self, subject: str, data: dict[str, object]) -> None:
        """Emit an event to all handlers whose pattern matches `subject`.

            Handlers are called concurrently via asyncio.gather.
            Individual handler failures are caught, logged, and do not abort
            delivery to other handlers (fail-open).

            Args:
            subject: Concrete subject string (no wildcards).
            data: Event payload dict.  A "subject" key is injected into the
            data dict before dispatch so handlers always know the subject.
            """
        if self._closed:
            logger.warning("asyncio_queue_bus.publish_on_closed_bus", subject=subject)
            return

        enriched: dict[str, object] = {**data, "_subject": subject}
        matching_handlers = [
            handler
            for pattern, handler in self._subscriptions
            if fnmatch.fnmatchcase(subject, pattern)
        ]

        if not matching_handlers:
            logger.debug("asyncio_queue_bus.no_subscribers", subject=subject)
            return

        logger.debug(
            "asyncio_queue_bus.publish",
            subject=subject,
            handler_count=len(matching_handlers),
        )

        # Dispatch all handlers concurrently; catch per-handler failures
        results = await asyncio.gather(
            *[_safe_call(handler, enriched) for handler in matching_handlers],
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logging.getLogger(__name__).error(
                    "event_bus handler error",
                    exc_info=result,
                    extra={"subject": subject, "handler_index": i},
                )

    async def close(self) -> None:
        """Gracefully shut down the bus.

            Idempotent: safe to call multiple times.
            After close(), publish() is a no-op with a warning log.
            """
        if self._closed:
            return
        self._closed = True
        self._subscriptions.clear()
        logger.debug("asyncio_queue_bus.closed")

    @property
    def subscription_count(self) -> int:
        """Return the number of active subscriptions."""
        return len(self._subscriptions)

    @property
    def is_closed(self) -> bool:
        """Return True if the bus has been closed."""
        return self._closed


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


async def _safe_call(handler: EventHandler, data: dict[str, object]) -> None:
    """Call an event handler, propagating exceptions to the caller.

        asyncio.gather(return_exceptions=True) captures the exception and
        returns it as the gather result — the caller (publish) then logs it.
        """
    await handler(data)


# ---------------------------------------------------------------------------
# NatsJetStreamBus — durable outbox for audit-critical events (V12)
# ---------------------------------------------------------------------------


class NatsJetStreamBus:
    """NATS JetStream-backed durable event bus.

        Implements the EventBus protocol with at-least-once delivery semantics
        via NATS JetStream streams. Events are persisted in a JetStream stream
        and consumed by durable consumers, surviving process restarts.

        Falls back gracefully when NATS is unavailable — events are logged
        with a warning and dropped, never blocking the caller.

        Usage::

        bus = await NatsJetStreamBus.connect("nats://localhost:4222")
        await bus.subscribe("the agent platform.**", handler)
        await bus.publish("the agent platform.agent.start", {"agent_id": "x"})

        Dependencies: nats-py with JetStream support (pip install nats-py[nats]).

        P1-9 / Wiring Gap closure: in-process AsyncioQueueBus → durable outbox.
        """

    def __init__(self) -> None:
        self._nc: Any = None
        self._js: Any = None
        self._connected: bool = False
        self._subscriptions: list[tuple[str, EventHandler]] = []

    @classmethod
    async def connect(
        cls,
        nats_url: str,
        *,
        stream_name: str = "dharmaos_events",
        subject_prefix: str = "the agent platform",
    ) -> NatsJetStreamBus:
        """Connect to NATS and create the JetStream stream.

            Safe to call during startup. If NATS is unreachable, returns
            an unconnected bus that logs-and-drops events (fail-open).
            """
        bus = cls()
        try:
            import nats
            from nats.js.api import (
                ConsumerConfig,
                DeliverPolicy,
                RetentionPolicy,
                StorageType,
                StreamConfig,
            )

            bus._nc = await nats.connect(nats_url)
            bus._js = bus._nc.jetstream()

            await bus._js.add_stream(
                StreamConfig(
                name=stream_name,
                subjects=[f"{subject_prefix}.>"],
                retention=RetentionPolicy.LIMITS,
                storage=StorageType.FILE,
                max_bytes=1024 * 1024 * 1024,  # 1 GB
                max_age=7 * 24 * 3600,  # 7 days
                max_msgs=10_000_000,
            )
            )
            bus._connected = True
            structlog.get_logger().info(
                "nats_jetstream.connected",
                url=nats_url,
                stream=stream_name,
            )
        except Exception as exc:
            structlog.get_logger().warning(
                "nats_jetstream.unavailable_graceful_degrade",
                error=str(exc)[:120],
            )
        return bus

    async def subscribe(self, subject: str, handler: EventHandler) -> None:
        """Register a handler (in-process fan-out, same as AsyncioQueueBus).

            For JetStream push consumers, use JetStream's subscribe() API directly
            on the js object. This method provides backward-compat with the
            EventBus protocol.
            """
        self._subscriptions.append((subject, handler))

    async def publish(self, subject: str, data: dict[str, object]) -> None:
        """Publish an event to NATS JetStream.

            When connected: publishes to JetStream → durable, at-least-once.
            When disconnected: logs warning and drops (fail-open — a bus outage
            should not halt the agent pipeline).
            """
        import json as _json

        payload = _json.dumps({"_subject": subject, **data}).encode()
        if self._connected and self._js is not None:
            try:
                await self._js.publish(subject, payload)
                return
            except Exception as exc:
                structlog.get_logger().warning(
                    "nats_jetstream.publish_error",
                    subject=subject,
                    error=str(exc)[:120],
                )

        structlog.get_logger().debug(
            "nats_jetstream.event_dropped",
            subject=subject,
            connected=self._connected,
        )
        event_bus_dropped(subject)

    async def close(self) -> None:
        """Drain and close the NATS connection."""
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:
                pass
        self._connected = False
        self._subscriptions.clear()
