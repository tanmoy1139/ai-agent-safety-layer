"""
    DharmaOS Metrics — zero-dependency Prometheus-style operational observability.

    PURPOSE
    -------
    Provides thread-safe counters and histograms for production observability of
    the DharmaOS governance kernel. No external dependencies are required —
    callers wire these into their own metrics pipeline (FastAPI /metrics endpoint,
    structlog, OpenTelemetry, Prometheus push gateway, etc.).

    All metrics use the "dharmaos_" prefix for namespace isolation.

    METRICS EXPOSED
    ---------------
    dharmaos_rate_limit_drops_total  : Per-tenant rate limit rejections
    by AdharmaDetector (Layer 0).
    dharmaos_event_bus_drops_total  : Event bus saturation drops
    (AsyncioQueueBus backpressure).
    dharmaos_brahmacarya_ratio_histogram  : EthicsEngine brahmacarya (energy-
    discipline) ratio observations for
    threshold calibration (P2-1).
    dharmaos_violations_total  : Total yama violations by constraint
    name (ahimsa/satya/asteya/etc.).
    dharmaos_audit_decisions_total  : AdharmaDetector verdicts by outcome
    (permit/deny/escalate_hitl).
    dharmaos_adharma_pipeline_latency_seconds : Full 8-layer pipeline wall-clock
    latency histogram.

    KEY FUNCTIONS
    -------------
    rate_limit_dropped(tenant_id)  : Increment rate-limit drop counter.
    event_bus_dropped(subject)  : Increment event bus drop counter.
    brahmacarya_ratio_observed(ratio)  : Record brahmacarya ratio observation.
    violation_recorded(constraint_name) : Increment yama violation counter.
    audit_decision(verdict)  : Record AdharmaDetector verdict.
    pipeline_latency_ms(ms)  : Record pipeline latency.
    metrics_snapshot()  : Return all current metric values as dict.

    Governance origin: Production metrics for DharmaOS — counters, gauges, histograms.

    Zero-dependency Prometheus-style metrics. Thread-safe atomic counters
    and histograms for production observability. No external Prometheus
    client library required — callers wire into their own metrics collection
    pipeline (e.g., FastAPI /metrics endpoint, structlog, OpenTelemetry).

    Metrics exposed:
    - dharmaos_rate_limit_drops_total: Per-tenant rate limit rejections
    - dharmaos_event_bus_drops_total: Event bus saturation drops
    - dharmaos_brahmacarya_ratio_histogram: Brahmacarya ratio observations
    - dharmaos_violations_total: Total yama violations by constraint
    - dharmaos_audit_decisions_total: AdharmaDetector verdicts by outcome
    - dharmaos_adharma_pipeline_latency_seconds: Pipeline latency histogram

    Sprint: V12 enterprise readiness.
    """

from __future__ import annotations

import time
from collections import defaultdict as _defaultdict
from threading import Lock


class _AtomicCounter:
    """Thread-safe atomic counter."""

    def __init__(self) -> None:
        self._value: int = 0
        self._lock = Lock()

    def inc(self, delta: int = 1) -> int:
        with self._lock:
            self._value += delta
            return self._value

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


class _MetricRegistry:
    """Process-wide metric registry — singleton."""

    def __init__(self) -> None:
        self._counters: dict[str, _AtomicCounter] = _defaultdict(_AtomicCounter)
        self._histograms: dict[str, list[float]] = _defaultdict(list)
        self._histogram_lock = Lock()
        # Guards defaultdict key-creation + snapshot iteration of _counters
        # (per-counter .inc() is already atomic, but creating a new key and
        # iterating items() concurrently can race / "dict changed size").
        self._counter_lock = Lock()

    def counter_inc(self, name: str, delta: int = 1) -> None:
        with self._counter_lock:
            counter = self._counters[name]
        counter.inc(delta)

    def counter_get(self, name: str) -> int:
        with self._counter_lock:
            counter = self._counters[name]
        return counter.value

    def histogram_observe(self, name: str, value: float) -> None:
        with self._histogram_lock:
            self._histograms[name].append(value)
            # Cap at 10k samples per histogram to bound memory
            if len(self._histograms[name]) > 10_000:
                self._histograms[name] = self._histograms[name][-5_000:]

    def histogram_get(self, name: str) -> list[float]:
        with self._histogram_lock:
            return list(self._histograms[name])

    def snapshot(self) -> dict[str, object]:
        """Return all metrics as a dict suitable for /metrics scraping."""
        with self._counter_lock:
            counters = {k: v.value for k, v in self._counters.items()}
        with self._histogram_lock:
            histogram_stats = {
                k: {
                "count": len(v),
                "min": min(v) if v else 0,
                "max": max(v) if v else 0,
                "avg": sum(v) / len(v) if v else 0,
            }
                for k, v in self._histograms.items()
            }
        return {"counters": counters, "histogram_stats": histogram_stats}


# Global metric registry — process-wide singleton.
REGISTRY = _MetricRegistry()


# ── Convenience functions ────────────────────────────────────────────────────

def rate_limit_dropped(tenant_id: str) -> None:
    REGISTRY.counter_inc("dharmaos_rate_limit_drops_total")
    REGISTRY.counter_inc(f"dharmaos_rate_limit_drops:tenant={tenant_id}")


def event_bus_dropped(subject: str) -> None:
    REGISTRY.counter_inc("dharmaos_event_bus_drops_total")
    REGISTRY.counter_inc(f"dharmaos_event_bus_drops:subject={subject}")


def brahmacarya_ratio_observed(ratio: float) -> None:
    REGISTRY.histogram_observe("dharmaos_brahmacarya_ratio", ratio)


def violation_recorded(constraint: str) -> None:
    REGISTRY.counter_inc("dharmaos_violations_total")
    REGISTRY.counter_inc(f"dharmaos_violations:constraint={constraint}")


def audit_decision(verdict: str) -> None:
    REGISTRY.counter_inc("dharmaos_audit_decisions_total")
    REGISTRY.counter_inc(f"dharmaos_audit_decisions:verdict={verdict}")


def pipeline_latency_ms(latency: float) -> None:
    REGISTRY.histogram_observe("dharmaos_adharma_pipeline_latency_ms", latency)


def metrics_snapshot() -> dict[str, object]:
    return REGISTRY.snapshot()
