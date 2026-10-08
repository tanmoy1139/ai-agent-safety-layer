"""
DharmaOS PancaPranaCollector — five-dimensional system vitality decomposition.

PURPOSE
-------
PancaPranaCollector decomposes the single scalar "energy" vital into five
distinct operational sub-metrics, enabling fine-grained diagnosis of system
health. A single aggregate energy score cannot distinguish between "the agent
is healthy but receiving no inputs" vs "the agent is overloaded with escalations".
The five-sub-metric decomposition exposes both conditions unambiguously.

THE FIVE SUB-METRICS
--------------------
prāṇa  (input rate)  : NATS inbound msg/s normalized to [0,1].
Measures how much work is arriving.
apāna  (output + GC)  : NATS outbound msg/s + GC-freed bytes/s.
Measures waste-elimination efficiency.
samāna  (extraction quality): findings-per-step quality score [0,1].
Measures cognitive digestion quality.
udāna  (HITL escalation)  : HITL escalations per hour (inverted).
Higher escalation rate → lower udāna score.
Measures decision confidence.
vyāna  (inter-agent comms): NATS cross-subject msg/s normalized to [0,1].
Measures coordination throughput.

AGGREGATE SCORE (pranayama)
---------------------------
pranayama = weighted mean of all five sub-metrics.
Weights (PRANA_WEIGHTS): prana=0.25, apana=0.15, samana=0.25, udana=0.15, vyana=0.20.
Total weight = 1.0. Result clamped to [0.0, 1.0].

PRANA_PULSE EVENT
-----------------
emit_prana_pulse_event() produces an SSE-ready dict with all five sub-airs
plus the aggregate, for consumption by monitoring dashboards or the Turīya
sidecar. Event type: "PRANA_PULSE".

KEY TYPES
---------
PancaPranaMetrics  : Frozen dataclass (prana, apana, samana, udana, vyana,
pranayama aggregate, timestamp).
PancaPranaCollector : Main collector. collect(raw_metrics) → PancaPranaMetrics.
compute_pranayama() : Standalone weighted-mean computation.
emit_prana_pulse_event() : SSE-ready event dict builder.
PRANA_WEIGHTS  : Final dict of sub-air → weight.

Governance origin: Pañca prāṇa (five vital sub-airs) from Praśna Upaniṣad III
— prāṇa/apāna/samāna/udāna/vyāna mapped to enterprise operational metrics
for system vitality monitoring and anomaly detection.

agent safety layer — Pañca Prāṇa Decomposition, Sprint V5.

Decomposes the scalar pranayama "energy" reading into the five sub-airs
(pañca prāṇa) described in the Praśna Upaniṣad III and Bṛhadāraṇyaka
Upaniṣad III.9.

**Philosophical grounding:**
Source authorities:
Praśna Upaniṣad III.3–12 — Pippalāda's answer to Kausalya on prāṇa:
prāṇa (vital breath), apāna (downward breath), samāna (equalising
breath), udāna (upward breath), vyāna (pervading breath).
Bṛhadāraṇyaka Upaniṣad III.9.26 — brief enumeration in Yājñavalkya's
śatapatha within the third adhyāya (Brahma-kāṇḍa).

Traditional correspondences (Praśna Up. III):
prāṇa  — mouth/nose, governs intake (śrotra, sight, smell) → *input*
apāna  — downward, governs excretion / waste elimination  → *output / GC*
samāna — navel region, digests food, fans digestive fire  → *extraction quality*
udāna  — upward, connects lower with higher realms  → *HITL escalation*
vyāna  — pervades the entire body, distributes prāṇa  → *inter-agent comms*

**Engineering mapping (Sprint V5 spec):**

| Sub-air | Engineering analogue  | Weight |
|---------|----------------------------------------------|--------|
| prāṇa  | NATS inbound msg/s (input rate)  | 0.25  |
| apāna  | NATS outbound msg/s + GC-freed bytes/s  | 0.15  |
| samāna  | extraction quality / findings-per-step  | 0.25  |
| udāna  | HITL escalations / hour (inverted)  | 0.15  |
| vyāna  | NATS cross-subject msg/s (inter-agent comms) | 0.20  |

Normalized aggregate (pranayama) = weighted mean of the five sub-airs,
each clamped to [0, 1].

**Normalization table:**
prana  = min(1.0, nats_inbound_rate_hz / 60.0)
apana  = min(1.0, (nats_outbound_rate_hz / 60.0
+ gc_freed_bytes_per_sec / 1e7) / 2)
samana = extraction_quality  (already [0, 1])
udana  = 1 - min(1.0, hitl_escalations_per_hour / 5.0)
(inverted — more HITL = lower vital)
vyana  = min(1.0, nats_cross_subject_rate_hz / 30.0)

**PRANA_PULSE event:**
SSE-ready dict emitted on each pranayama computation, carrying all five
sub-airs plus the aggregate. Event type: ``"PRANA_PULSE"``.

Ported lineage:
/opt/agent-core/*.py (conception 2026-01-25)
/opt/reference implementation/backend/app/services/the agent platform/consciousness.py
(reference only — DO NOT MODIFY; scalar pranayama source)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Weight constants — Praśna Upaniṣad III / Sprint V5 spec
# ---------------------------------------------------------------------------

#: Weighted contribution of each sub-air to the aggregate pranayama score.
#: Weights sum to 1.0 (checked by module-level assertion below).
#: Source: Sprint V5 engineering specification, grounded in Praśna Up. III
#: relative emphasis on each vāyu's role in sustaining life.
PRANA_WEIGHTS: Final[dict[str, float]] = {
"prana": 0.25,  # prāṇa  — input intake
"apana": 0.15,  # apāna  — downward excretion / GC
"samana": 0.25,  # samāna — digestive fire / extraction quality
"udana": 0.15,  # udāna  — upward / HITL escalation (inverted)
"vyana": 0.20,  # vyāna  — pervading / inter-agent distribution
}

# Weights must sum to exactly 1.0 (floating-point tolerance 1e-9).
assert abs(sum(PRANA_WEIGHTS.values()) - 1.0) < 1e-9, (
    f"PRANA_WEIGHTS must sum to 1.0 — got {sum(PRANA_WEIGHTS.values())}"
)

# ---------------------------------------------------------------------------
# Normalization ceiling constants
# ---------------------------------------------------------------------------

#: Ceiling for raw NATS inbound rate (msg/s) before saturation at 1.0.
PRANA_INBOUND_CEIL_HZ: Final[float] = 60.0

#: Ceiling for raw NATS outbound rate (msg/s) component of apāna.
APANA_OUTBOUND_CEIL_HZ: Final[float] = 60.0

#: Ceiling for GC-freed bytes/s component of apāna.
APANA_GC_CEIL_BYTES_PER_SEC: Final[float] = 1e7  # 10 MB/s

#: Ceiling for HITL escalations/hour before udāna = 0.
UDANA_ESCALATION_CEIL_PER_HOUR: Final[float] = 5.0

#: Ceiling for NATS cross-subject rate (msg/s) before vyana = 1.0.
VYANA_CROSS_SUBJECT_CEIL_HZ: Final[float] = 30.0


# ---------------------------------------------------------------------------
# PancaPranaMetrics — immutable value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PancaPranaMetrics:
    """Five pañca-prāṇa sub-air scores, each clamped to [0, 1].

    Immutable (frozen dataclass) — created by PancaPranaCollector.collect()
    or directly for testing.

    Attributes:
    prana:  Input rate score — prāṇa (Praśna Up. III.3).
    apana:  Output + GC rate score — apāna (Praśna Up. III.4).
    samana: Extraction quality score — samāna (Praśna Up. III.5).
    udana:  Inverted HITL escalation score — udāna (Praśna Up. III.7).
    vyana:  Inter-agent comms score — vyāna (Praśna Up. III.12).
    """

    prana: float  # input rate, [0, 1]
    apana: float  # output rate + GC, [0, 1]
    samana: float  # extraction quality, [0, 1]
    udana: float  # HITL escalation (inverted), [0, 1]
    vyana: float  # inter-agent comms, [0, 1]

    def __post_init__(self) -> None:
        """Validate all sub-air scores are in [0, 1]."""
        for field_name in ("prana", "apana", "samana", "udana", "vyana"):
            val: float = getattr(self, field_name)
            if not (0.0 <= val <= 1.0):
                raise ValueError(
                    f"PancaPranaMetrics.{field_name} must be in [0, 1]; got {val!r}. "
                "Caller must clamp before constructing."
                )


# ---------------------------------------------------------------------------
# compute_pranayama — weighted aggregate
# ---------------------------------------------------------------------------


def compute_pranayama(metrics: PancaPranaMetrics) -> float:
    """Compute the weighted aggregate of the five pañca-prāṇa sub-airs.

    Implements the weighted mean per the Sprint V5 specification:
    pranayama = Σ (weight_i × sub_air_i)

    Weights (from PRANA_WEIGHTS, summing to 1.0):
    prāṇa  0.25, apāna  0.15, samāna 0.25, udāna  0.15, vyāna  0.20

    Source: Praśna Upaniṣad III, Bṛhadāraṇyaka III.9.

    Args:
    metrics: PancaPranaMetrics — all five sub-air scores clamped to [0, 1].

    Returns:
    Aggregate pranayama score in [0, 1].
    """
    result = (
        PRANA_WEIGHTS["prana"] * metrics.prana
        + PRANA_WEIGHTS["apana"] * metrics.apana
        + PRANA_WEIGHTS["samana"] * metrics.samana
        + PRANA_WEIGHTS["udana"] * metrics.udana
        + PRANA_WEIGHTS["vyana"] * metrics.vyana
    )
    # Clamp to [0, 1] to guard against floating-point drift.
    return max(0.0, min(1.0, result))


# ---------------------------------------------------------------------------
# PancaPranaCollector — raw ops metrics → PancaPranaMetrics
# ---------------------------------------------------------------------------


class PancaPranaCollector:
    """Translates raw operational metrics into PancaPranaMetrics.

    Normalization table (per Sprint V5 specification):
    prana  = min(1.0, nats_inbound_rate_hz / 60.0)
    apana  = min(1.0, (nats_outbound_rate_hz / 60.0
    + gc_freed_bytes_per_sec / 1e7) / 2)
    samana = extraction_quality  (already [0, 1])
    udana  = 1 - min(1.0, hitl_escalations_per_hour / 5.0)
    (inverted: more HITL = lower vital force)
    vyana  = min(1.0, nats_cross_subject_rate_hz / 30.0)

    P2-5: EWMA (Exponentially Weighted Moving Average) smoothing.  During
    high-load periods, raw vitals become stale between polls, potentially
    masking a degraded physical kośa.  Each collect() call blends the new
    raw reading with the previous smoothed value:

    smoothed = α × raw + (1 − α) × previous_smoothed

    The default α=0.3 gives a 3–5 sample settling time suitable for the
    consciousness loop's adaptive polling (0.5–10 s intervals).

    Usage::

    collector = PancaPranaCollector(alpha=0.3)
    metrics = collector.collect({...})
    # metrics now carries EWMA-smoothed sub-air scores.

    The ``collect`` method accepts a ``raw`` dict whose keys are the metric
    names listed above.  Missing keys default to 0.0 (zero-vital).
    """

    def __init__(self, alpha: float = 0.3) -> None:
        """Initialise the collector with EWMA smoothing factor.

        Args:
        alpha: EWMA decay factor in (0.0, 1.0]. Higher = more responsive
        to recent readings. Default 0.3 per P2-5 specification.
        Set to 1.0 to disable smoothing (raw pass-through).
        """
        if not 0.0 < alpha <= 1.0:
            raise ValueError(f"alpha must be in (0.0, 1.0], got {alpha!r}")
        self._alpha: float = alpha
        # Per-sub-air smoothed values — None before first collect() call.
        self._prana_ewma: float | None = None
        self._apana_ewma: float | None = None
        self._samana_ewma: float | None = None
        self._udana_ewma: float | None = None
        self._vyana_ewma: float | None = None

    def collect(self, raw: dict[str, float]) -> PancaPranaMetrics:
        """Normalise raw metrics into PancaPranaMetrics.

        Args:
        raw: Dict of raw metric name → float value.
        Keys used:
        nats_inbound_rate_hz  → prāṇa
        nats_outbound_rate_hz  → apāna (outbound component)
        gc_freed_bytes_per_sec  → apāna (GC component)
        extraction_quality  → samāna
        hitl_escalations_per_hour  → udāna (inverted)
        nats_cross_subject_rate_hz  → vyāna
        Any key absent from ``raw`` defaults to 0.0.

        Returns:
        PancaPranaMetrics with all five sub-airs in [0, 1].
        """
        nats_inbound: float = raw.get("nats_inbound_rate_hz", 0.0)
        nats_outbound: float = raw.get("nats_outbound_rate_hz", 0.0)
        gc_freed: float = raw.get("gc_freed_bytes_per_sec", 0.0)
        extraction_quality: float = raw.get("extraction_quality", 0.0)
        hitl_escalations: float = raw.get("hitl_escalations_per_hour", 0.0)
        nats_cross_subject: float = raw.get("nats_cross_subject_rate_hz", 0.0)

        # prāṇa — input rate (raw normalised)
        prana_raw: float = min(1.0, nats_inbound / PRANA_INBOUND_CEIL_HZ)

        # apāna — output rate + GC (raw normalised)
        apana_outbound_norm: float = nats_outbound / APANA_OUTBOUND_CEIL_HZ
        apana_gc_norm: float = gc_freed / APANA_GC_CEIL_BYTES_PER_SEC
        apana_raw: float = min(1.0, (apana_outbound_norm + apana_gc_norm) / 2.0)

        # samāna — extraction quality (raw: already [0,1], clamp defensively)
        samana_raw: float = max(0.0, min(1.0, extraction_quality))

        # udāna — HITL escalation (raw inverted)
        udana_raw: float = 1.0 - min(1.0, hitl_escalations / UDANA_ESCALATION_CEIL_PER_HOUR)

        # vyāna — inter-agent comms (raw normalised)
        vyana_raw: float = min(1.0, nats_cross_subject / VYANA_CROSS_SUBJECT_CEIL_HZ)

        # P2-5: EWMA smoothing — blend raw reading with previous smoothed value.
        # First call (EWMA is None) → use raw directly.
        a = self._alpha
        prana = prana_raw if self._prana_ewma is None else a * prana_raw + (1 - a) * self._prana_ewma
        apana = apana_raw if self._apana_ewma is None else a * apana_raw + (1 - a) * self._apana_ewma
        samana = samana_raw if self._samana_ewma is None else a * samana_raw + (1 - a) * self._samana_ewma
        udana = udana_raw if self._udana_ewma is None else a * udana_raw + (1 - a) * self._udana_ewma
        vyana = vyana_raw if self._vyana_ewma is None else a * vyana_raw + (1 - a) * self._vyana_ewma
        prana, apana, samana, udana, vyana = float(prana), float(apana), float(samana), float(udana), float(vyana)

        # Store EWMA state for next call
        self._prana_ewma = prana
        self._apana_ewma = apana
        self._samana_ewma = samana
        self._udana_ewma = udana
        self._vyana_ewma = vyana

        metrics = PancaPranaMetrics(
                prana=prana,
                apana=apana,
                samana=samana,
                udana=udana,
                vyana=vyana,
        )

        logger.debug(
            "panca_prana_collected",
                prana=round(prana, 4),
                apana=round(apana, 4),
                samana=round(samana, 4),
                udana=round(udana, 4),
                vyana=round(vyana, 4),
        )

        return metrics


# ---------------------------------------------------------------------------
# emit_prana_pulse_event — SSE-ready payload
# ---------------------------------------------------------------------------


def emit_prana_pulse_event(
metrics: PancaPranaMetrics,
pranayama: float,
) -> dict[str, Any]:
    """Build an SSE-ready PRANA_PULSE event payload.

    Returns a dict suitable for serialisation as a Server-Sent Event carrying
    the five sub-air scores and their weighted aggregate.

    Args:
    metrics:  PancaPranaMetrics instance holding the five sub-airs.
    pranayama: Pre-computed aggregate from compute_pranayama(metrics).

    Returns:
    Dict with keys:
    event  — "PRANA_PULSE" (SSE event type)
    timestamp — Unix seconds (float)
    sub_airs  — dict with keys prana/apana/samana/udana/vyana (4 d.p.)
    pranayama — aggregate score (4 d.p.)
    weights  — copy of PRANA_WEIGHTS for consumer reference

    Example payload::

    {
    "event": "PRANA_PULSE",
    "timestamp": 1713340800.0,
    "sub_airs": {
    "prana": 0.5,
    "apana": 0.35,
    "samana": 0.85,
    "udana": 0.8,
    "vyana": 0.5,
    },
    "pranayama": 0.622,
    "weights": {"prana": 0.25, "apana": 0.15, ...},
    }
    """
    payload: dict[str, Any] = {
    "event": "PRANA_PULSE",
    "timestamp": time.time(),
    "sub_airs": {
    "prana": round(metrics.prana, 4),
    "apana": round(metrics.apana, 4),
    "samana": round(metrics.samana, 4),
    "udana": round(metrics.udana, 4),
    "vyana": round(metrics.vyana, 4),
    },
    "pranayama": round(pranayama, 4),
    "weights": dict(PRANA_WEIGHTS),
    }

    logger.info(
        "prana_pulse_emitted",
    pranayama=round(pranayama, 4),
            prana=round(metrics.prana, 4),
            apana=round(metrics.apana, 4),
            samana=round(metrics.samana, 4),
            udana=round(metrics.udana, 4),
            vyana=round(metrics.vyana, 4),
    )

    return payload
