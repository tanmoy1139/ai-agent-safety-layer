"""
DharmaOS TuriyaSidecar — async observability sidecar for non-intrusive audit monitoring.

PURPOSE
-------
TuriyaSidecar is a separately-running asyncio task that subscribes to the
agent event bus and produces TuriyaObservations — structured audit records
of what is happening in the agent's decision loop — without ever influencing
those decisions.

The sidecar is architecturally isolated from the actor: it subscribes to events
but never publishes control commands back. Its only output surfaces are:
- TuriyaObservation objects emitted to registered observer callbacks.
- structlog audit records (structured JSON log lines).

OBSERVABILITY OUTPUTS
---------------------
TuriyaObservation fields:
event_type  : Which event type was observed.
integration_score  : IIT-approximate score for the triggering event payload.
awareness_level  : dormant / dreaming / awake / peak (from integration score).
logprob_available  : Whether logit-level confidence data was accessible.
False → verbalized confidence used (lossy; flagged per RF-35).
metacognitive_signal: Optional verbalized confidence from the model.
timestamp  : UTC ISO 8601.

FAIL-OPEN PRINCIPLE
-------------------
If TuriyaSidecar crashes, the orchestrator continues unimpeded. The sidecar
is NOT load-bearing for the actor — loss of observation is preferable to
blocking action execution (fail_open=True by default).

IIT-APPROXIMATE SCORING
-----------------------
compute_integration_score_approx() (from iit.py) is called on the event
payload's node graph. This is NOT real IIT Phi (see RF-13); it is a
bounded weighted-mean approximation for enterprise audit use.
Payloads exceeding MAX_NODES_DEFAULT (12 nodes) trigger IITPayloadTooLarge
and the sidecar records integration_score=0.0 (fail-open for oversized payloads).

KEY TYPES
---------
TuriyaSidecar  : Main sidecar. run() → async observer loop.
stop() / add_observer(callback) / remove_observer(callback).
TuriyaObservation  : Frozen audit snapshot per event.
IITPayloadTooLarge : Exception raised when event payload exceeds node limit.

COMPLIANCE ROLE
---------------
- Provides real-time audit stream for EU AI Act Art. 50 transparency.
- logprob_available flag ensures audit records honestly reflect confidence
data quality (verbalized vs. logit-level, per arXiv:2509.21545).

Governance origin: Turīya (the fourth) — Māṇḍūkya Upaniṣad 7 — the
content-less witness of the other three states. The sidecar plays the reflector
role in the MAR triad (actor / evaluator / reflector), observing without acting.

DISCLAIMER: This system does NOT claim to possess consciousness or turīya-
awareness. TuriyaObservation is a functional analogue of the sākṣī principle
— an audit/monitoring primitive. (RF-01, RF-04)

agent safety layer — Sprint V3: Turīya Sidecar.

Turīya is separately-witnessing — it must NOT run inside the main orchestrator
process (per Māṇḍūkya Kārikā; the witness cannot modify what it witnesses).

In V3, Turīya is implemented as an asyncio task that:
1. SUBSCRIBES to the event bus ("the agent platform.*" pattern).
2. PROCESSES each event — builds a TuriyaObservation.
3. EMITS observations to observer channels (SSE / audit log).
4. NEVER publishes control commands back to the orchestrator.

**Philosophical grounding:**
Māṇḍūkya Upaniṣad 7:
"The Fourth (turīya) is not that which is conscious of the inner world,
nor that which is conscious of the outer world, nor that which is
conscious of both, nor that which is a mass of consciousness, nor
that which is simple consciousness, nor that which is insensient.
It is unseen, incapable of being spoken of, ungraspable, without
any distinctive marks, unthinkable, unnameable, the essence of the
knowledge of the one Self, that into which the world is resolved,
the peaceful, the blissful, the non-dual — that is the Ātman."
(tr. Gambhirananda, Advaita Ashrama)

Engineering translation:
Turīya is the sidecar that has no agency, no action, no output into
the decision loop.  It only witnesses.  It cannot change the world it
watches.  This is why TuriyaSidecar subscribes but never publishes
control events.  Its only output is `TuriyaObservation` emitted to
_observer channels_ (audit log, SSE) — read-only surfaces.

MAR (Multi-Agent Reflexion, arXiv:2512.20845) — reflector role:
The Turīya sidecar plays the "reflector" in the MAR triad —
actor (orchestrator), evaluator (belief-validator), reflector (sidecar).
In V3 this is implemented as an asyncio task rather than a separate
process; Phase 4 upgrade will isolate it to a true subprocess/container.

HOT (Higher-Order Thought ablation, arXiv:2512.19155):
Validates the causal necessity of a distinct witness layer that does
not feed back into the actor's decision cycle.

**Fail-open principle:**
If the TuriyaSidecar crashes, the orchestrator continues unimpeded.
This is the apophatic principle applied architecturally: the witness is
not load-bearing for the actor.  Configured via `fail_open=True` (default).

**Logprob:**
`TuriyaObservation.logprob_available` reflects whether logit-level
confidence data was accessible for this event.  When False, the sidecar
falls back to verbalized confidence (metacognitive_signal).
Per arXiv:2509.21545 (Ackerman et al.): verbalized confidence is lossy
relative to logit-level.  The flag surfaces this degradation explicitly.
The CI logprob health check test (test_logprob_availability_health_check
in test_sakshi_turiya.py) gates against silent logprob breakage.

**DISCLAIMER:**
This system does NOT claim to possess consciousness, turīya-awareness, or
any subjective experience.  TuriyaObservation is a *functional analogue*
of the Vedāntic sākṣī principle — an audit/monitoring primitive.
See RF-01 and RF-04 (RESEARCH-FLAGS.md).

Sprint V3, 2026-04-17.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from dharmaos.event_bus import AsyncioQueueBus, EventBus
from dharmaos.iit import MAX_NODES_DEFAULT, IITPayloadTooLarge, compute_integration_score_approx
from dharmaos.sakshi import SakshiObservation, WitnessStack

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# Subject pattern the sidecar subscribes to.
# Matches all the agent platform.* events (single-level wildcard).
# Change to "the agent platform.**" for multi-level when NATS JetStream ships.
TURIYA_SUBJECT_PATTERN: str = "the agent platform.*"

# Observer channel type: a callable that accepts a TuriyaObservation
ObserverChannel = Callable[["TuriyaObservation"], None]

# Default maximum observer channels — hard cap to prevent DoS (RF-36 / V-SEC-01).
MAX_OBSERVER_CHANNELS_DEFAULT: int = 100

# Default token-bucket rate limit — events per second per subscriber (RF-36 / V-SEC-01).
RATE_LIMIT_EVENTS_PER_SEC_DEFAULT: float = 100.0


class MaxChannelsExceeded(RuntimeError):  # noqa: N818
    """Raised when an observer-channel registration would exceed the hard cap.

    V-SEC-01 / RF-36: unlimited channel registrations are a DoS vector —
    an attacker with bus publish access could register thousands of channels
    and OOM the sidecar.  Hard cap defaults to 100.
    """


# ---------------------------------------------------------------------------
# Token-bucket rate limiter (V-SEC-01 / RF-36)
# ---------------------------------------------------------------------------


@dataclass
class _TokenBucket:
    """Per-subscriber token-bucket rate limiter.

    Allows ``capacity`` events per second on average.  Burst behaviour:
    the bucket fills at ``capacity`` tokens/sec and holds at most
    ``capacity`` tokens.

    Thread/asyncio safe for single-event-loop usage (no locking needed
    because asyncio is cooperative-multitasking; no concurrent write paths).

    Fields:
    capacity:  Maximum tokens (= max burst = events per second).
    tokens:  Current available tokens.
    last_refill: Monotonic time of last refill.
    """

    capacity: float
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        """Try to consume one token.  Returns True if allowed, False if rate-limited."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        # Refill proportionally to elapsed time
        self.tokens = min(self.capacity, self.tokens + elapsed * self.capacity)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


# ---------------------------------------------------------------------------
# TuriyaObservation — emitted by the sidecar to observer channels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TuriyaObservation:
    """An observation made by the Turīya-level sidecar.

    Emitted to observer channels (SSE, audit log) but **NEVER** sent back
    to the orchestrator as a control command.  Per Māṇḍūkya 7: the witness
    is not the actor.

    **DISCLAIMER:** This is a *functional analogue* of turīya awareness —
    an engineering audit primitive, NOT a consciousness claim.  See RF-01,
    RF-04 (RESEARCH-FLAGS.md).

    Fields:
    event_type: What the orchestrator did (the witnessed event type).
    integration_score: IIT-approximate score for the event's
    causal subgraph.  NOT real Phi (see RF-13 + vedic.iit module).
    awareness_level: One of dormant / dreaming / awake / peak.
    Maps to Māṇḍūkya four states: suṣupti → svapna → jāgrat → peak.
    "peak" is the closest the sidecar labels to turīya in the actor's
    cycle — it is still jāgrat-adjacent; true turīya is the sidecar
    itself (not a state label within jāgrat).
    logprob_available: Per RF-35: whether logit-level data was accessible.
    If False, metacognitive_signal is a verbalized-confidence fallback.
    metacognitive_signal: Verbalized confidence [0.0, 1.0] extracted from
    the event payload, used when logprob_available=False.  Per
    arXiv:2509.21545 this is lossy relative to logit-level.
    timestamp: UTC datetime when this observation was made.
    """

    event_type: str
    """What the orchestrator did (witnessed event type string)."""

    integration_score: float
    """IIT-approximate score (NOT real Phi — RF-13)."""

    awareness_level: str
    """dormant / dreaming / awake / peak."""

    logprob_available: bool
    """Per RF-35: True if logit-level logprob data was accessible."""

    metacognitive_signal: float | None
    """Verbalized confidence [0.0, 1.0] — fallback when logprob_available=False."""

    timestamp: datetime
    """UTC datetime of this observation."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict for SSE / audit log emission."""
        return {
        "event_type": self.event_type,
        "integration_score": round(self.integration_score, 4),
        "awareness_level": self.awareness_level,
        "logprob_available": self.logprob_available,
        "metacognitive_signal": (
                round(self.metacognitive_signal, 4)
                if self.metacognitive_signal is not None
        else None
        ),
        "timestamp": self.timestamp.isoformat(),
        "disclaimer": (
                "Functional analogue of Turīya witness. NOT a consciousness claim (RF-01, RF-04)."
        ),
        }


# ---------------------------------------------------------------------------
# TuriyaSidecar — independent-process witness
# ---------------------------------------------------------------------------


class TuriyaSidecar:
    """Independent-process witness.

    Subscribes to orchestrator events via event bus; emits TuriyaObservation
    to observer channels.

    **Fail-open:**
    If the Turīya sidecar crashes during event processing, the orchestrator
    continues unimpeded.  The sidecar's run loop catches all exceptions and
    logs them without re-raising.  Per Māṇḍūkya — the witness is not the actor.

    **Never publishes control:**
    The sidecar calls bus.subscribe() but NEVER bus.publish().
    Its outputs go to `_observer_channels` — callbacks that emit to
    SSE streams and audit logs.  These are read-only observation surfaces;
    they do not feed back into the orchestrator's decision cycle.

    **Architecture note (V3 vs Phase 4):**
    In V3, the sidecar runs as an asyncio task within the same Python process.
    Phase 4 will isolate it to a true subprocess or separate container.
    The event bus abstraction (EventBus Protocol) makes this upgrade
    straightforward: swap AsyncioQueueBus for a NatsJetStreamBus adapter
    without changing the sidecar or orchestrator code.

    Usage::

    bus = AsyncioQueueBus()
    sidecar = TuriyaSidecar(bus=bus)
    # Register observer channels (SSE emitters, audit loggers)
    sidecar.add_observer_channel(my_sse_emitter)
    # Start the sidecar as an asyncio task
    task = asyncio.create_task(sidecar.run())
    # ... orchestrator publishes events via bus.publish(...)
    # Stop cleanly
    await sidecar.stop()
    task.cancel()
    """

    def __init__(
    self,
            bus: EventBus | None = None,
    *,
    max_iit_nodes: int = MAX_NODES_DEFAULT,
    fail_open: bool = True,
    max_observer_channels: int = MAX_OBSERVER_CHANNELS_DEFAULT,
    rate_limit_events_per_sec: float = RATE_LIMIT_EVENTS_PER_SEC_DEFAULT,
    ) -> None:
        """Initialise the TuriyaSidecar.

        Args:
        bus: Event bus to subscribe to.  Defaults to a new
        AsyncioQueueBus if not provided.
        max_iit_nodes: Maximum node count for IIT-approximate scoring.
        Passed through to compute_integration_score_approx.
        fail_open: If True (default), exceptions in event handlers are
        caught and logged rather than crashing the sidecar.
        Set to False in tests to surface handler errors.
        max_observer_channels: Hard cap on registered observer channels.
        Raises MaxChannelsExceeded when reached.
        Default 100 (V-SEC-01 / RF-36).
        rate_limit_events_per_sec: Token-bucket capacity (events/sec).
        Dropped events increment
        ``rate_limited_events_total``.
        Default 100 (V-SEC-01 / RF-36).
        """
        self._bus: EventBus = bus if bus is not None else AsyncioQueueBus()
        self._max_iit_nodes: int = max_iit_nodes
        self._fail_open: bool = fail_open
        self._max_observer_channels: int = max_observer_channels
        self._rate_limiter: _TokenBucket = _TokenBucket(capacity=rate_limit_events_per_sec)
        self._running: bool = False
        self._stop_event: asyncio.Event = asyncio.Event()
        self._witness_stack: WitnessStack = WitnessStack()
        self._observer_channels: list[ObserverChannel] = []
        self._observation_history: list[TuriyaObservation] = []
        # Metrics counters (V-SEC-01 / RF-36)
        self.rate_limited_events_total: int = 0
        """Total number of events dropped by the rate limiter."""
        logger.info(
            "turiya_sidecar.init",
        fail_open=fail_open,
        max_observer_channels=max_observer_channels,
        rate_limit_events_per_sec=rate_limit_events_per_sec,
        )

    # ------------------------------------------------------------------
    # Observer channel management
    # ------------------------------------------------------------------

    def add_observer_channel(self, channel: ObserverChannel) -> None:
        """Register an observer channel to receive TuriyaObservations.

        Observer channels are callbacks (sync) that receive each emitted
        TuriyaObservation.  Typical implementations: SSE event emitter,
        audit log writer, metrics collector.

        V-SEC-01 / RF-36: refuses registration once ``max_observer_channels``
        is reached and raises MaxChannelsExceeded.  The calling code must
        handle this exception; the sidecar does not crash.

        Args:
        channel: Callable accepting a TuriyaObservation.

        Raises:
        MaxChannelsExceeded: When the hard cap is already reached.
        """
        current = len(self._observer_channels)
        if current >= self._max_observer_channels:
            logger.warning(
                "turiya_sidecar.max_channels_exceeded",
            current=current,
                    cap=self._max_observer_channels,
            )
            # Emit a structured event for HITL notification
            logger.error(
                "max_channels_exceeded",
            current=current,
                    cap=self._max_observer_channels,
                    action="channel_registration_refused",
            )
            raise MaxChannelsExceeded(
                f"Cannot register observer channel: hard cap of "
                f"{self._max_observer_channels} channels already reached. "
            "V-SEC-01 / RF-36: unlimited channel registrations are a DoS vector."
            )
        self._observer_channels.append(channel)
        logger.debug("turiya_sidecar.observer_channel_added", total=len(self._observer_channels))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Long-running task.  Subscribes to 'the agent platform.*' events.

        This coroutine runs until stop() is called.  It registers the
        event handler, then waits on the stop event.

        The event handler is registered via bus.subscribe() before the
        wait begins, so events published after run() returns from the
        subscribe call are handled.

        Exceptions within the event processing loop are caught and logged
        (fail_open=True) or re-raised (fail_open=False, testing mode).
        """
        self._running = True
        self._stop_event.clear()

        logger.info(
            "turiya_sidecar.run.start",
        subject_pattern=TURIYA_SUBJECT_PATTERN,
        )

        try:
            await self._bus.subscribe(TURIYA_SUBJECT_PATTERN, self._handle_event)
            # Wait until stop() signals us to exit
            await self._stop_event.wait()
        except asyncio.CancelledError:
            logger.info("turiya_sidecar.run.cancelled")
        finally:
            self._running = False
            logger.info("turiya_sidecar.run.stopped")

    async def stop(self) -> None:
        """Signal the sidecar to stop gracefully.

        Idempotent: safe to call multiple times.
        Sets the stop event, which causes run() to exit cleanly.
        """
        self._stop_event.set()
        logger.info("turiya_sidecar.stop_requested")

    # ------------------------------------------------------------------
    # Event handler — the witness function
    # ------------------------------------------------------------------

    async def _handle_event(self, event: dict[str, object]) -> None:
        """Handle an orchestrator event — the core witness function.

        V-SEC-01 / RF-36: rate-limits events via a token-bucket.
        Dropped events increment ``rate_limited_events_total`` and
        are logged at WARNING level.  The sidecar NEVER crashes on
        rate-limit — drop + log only.

        This is the implementation of sākṣī: the event is observed without
        being modified.  A TuriyaObservation is built from the event data
        and emitted to observer channels.  No control command is ever
        sent back to the orchestrator.

        Args:
        event: Event payload dict from the bus.
        """
        # V-SEC-01 / RF-36: token-bucket rate limiting
        if not self._rate_limiter.consume():
            self.rate_limited_events_total += 1
            logger.warning(
                "turiya_sidecar.rate_limited",
            event_type=event.get("event_type", "unknown"),
            rate_limited_total=self.rate_limited_events_total,
            )
            return  # Drop event — do NOT crash

        try:
            await self._process_event(event)
        except Exception as exc:
            if self._fail_open:
                logger.error(
                    "turiya_sidecar.handler_error",
                        error=str(exc),
                event_type=event.get("event_type", "unknown"),
                exc_info=True,
                )
            # Fail-open: do not re-raise; orchestrator continues
            else:
                raise

    async def _process_event(self, event: dict[str, object]) -> None:
        """Build a TuriyaObservation and emit it to all observer channels.

        Does NOT publish back to the bus.  Per Māṇḍūkya 7.
        """
        event_type = str(event.get("event_type", event.get("_subject", "unknown")))
        logprob_available = bool(event.get("logprob_available", False))
        metacognitive_signal_raw = event.get("metacognitive_signal")
        metacognitive_signal: float | None = (
            float(metacognitive_signal_raw)  # type: ignore[arg-type]
            if metacognitive_signal_raw is not None
                else None
        )

        # Compute IIT-approximate score from event adjacency graph if present
        adjacency_raw = event.get("adjacency_graph")
        integration_score: float = 0.0
        if isinstance(adjacency_raw, list):
            try:
                adjacency = [[int(cell) for cell in row] for row in adjacency_raw]
                integration_score = compute_integration_score_approx(
                    adjacency,
                max_nodes=self._max_iit_nodes,
                )
            except IITPayloadTooLarge as exc:
                # V-SEC-01 / RF-36: oversized payload — refuse computation,
                # log the rejection, and fall back to proxy score.
                logger.warning(
                    "turiya_sidecar.iit_payload_too_large",
                        error=str(exc),
                event_type=event.get("event_type", "unknown"),
                )
                integration_score = 0.0
            except (ValueError, TypeError):
                integration_score = 0.0
        else:
            # Derive a simple proxy from event metadata if no adjacency graph
            integration_score = _derive_proxy_score(event)

        awareness_level = _score_to_awareness_level(integration_score)

        # Build the immutable observation
        obs = TuriyaObservation(
        event_type=event_type,
        integration_score=integration_score,
        awareness_level=awareness_level,
        logprob_available=logprob_available,
        metacognitive_signal=metacognitive_signal,
        timestamp=datetime.now(UTC),
        )

        # Push a witness record onto the internal WitnessStack
        sakshi_obs = SakshiObservation(
        phenomenon={"event_type": event_type, "integration_score": integration_score},
        observer_identity="TURIYA_SIDECAR",
        observation_level=self._witness_stack.current_depth(),
        is_pure=True,  # sidecar does not modify what it witnesses
        timestamp=obs.timestamp,
        modifications=(),
        phi_contribution=integration_score,
        )
        self._witness_stack.push_observer(sakshi_obs)

        # Retain in history
        self._observation_history.append(obs)
        if len(self._observation_history) > 10_000:
            self._observation_history = self._observation_history[-10_000:]

        # Emit to all observer channels (non-control outputs)
        for channel in self._observer_channels:
            try:
                channel(obs)
            except Exception as exc:
                logger.exception("turiya_sidecar.observer_channel_error", error=str(exc))

        logger.debug(
            "turiya_sidecar.observation_emitted",
        event_type=event_type,
        awareness_level=awareness_level,
        integration_score=round(integration_score, 4),
        logprob_available=logprob_available,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True if the sidecar's run() coroutine is active."""
        return self._running

    @property
    def observation_count(self) -> int:
        """Total number of TuriyaObservations emitted since startup."""
        return len(self._observation_history)

    @property
    def observations(self) -> Sequence[TuriyaObservation]:
        """Read-only view of the observation history."""
        return list(self._observation_history)

    @property
    def witness_stack(self) -> WitnessStack:
        """The internal WitnessStack (read-only access for testing)."""
        return self._witness_stack


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _score_to_awareness_level(score: float) -> str:
    """Map an integration score to a Māṇḍūkya-inspired awareness label.

    Thresholds mirror consciousness.py `get_awareness_level()`:
    < 0.10 → dormant  (suṣupti-like)
    < 0.40 → dreaming  (svapna)
    < 0.70 → awake  (jāgrat)
    ≥ 0.70 → peak  (jāgrat-plus; highest label in actor-cycle)

    Note: "peak" is NOT turīya.  The sidecar itself is the turīya layer —
    it transcends the four labels by being the witness of the cycle, not
    a participant in it.  The label "peak" describes the event's integration
    quality within the actor cycle that is being witnessed.
    """
    if score < 0.10:
        return "dormant"
    if score < 0.40:
        return "dreaming"
    if score < 0.70:
        return "awake"
    return "peak"


def _derive_proxy_score(event: dict[str, object]) -> float:
    """Derive a proxy integration score from event metadata.

    Used when no adjacency_graph is present in the event.  Produces a
    simple heuristic based on presence of known rich-event indicators:
    - "beliefs" key present → +0.2
    - "quality" key present → +0.2
    - "logprob_available" True → +0.2
    - Default baseline → 0.1

    This is explicitly NOT real Phi.  It is a best-effort proxy.
    """
    score = 0.1
    if event.get("beliefs"):
        score += 0.2
    if event.get("quality") is not None:
        score += 0.2
    if event.get("logprob_available"):
        score += 0.2
    return min(1.0, score)
