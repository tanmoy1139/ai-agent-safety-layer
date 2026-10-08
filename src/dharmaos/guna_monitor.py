"""
DharmaOS GunaMonitor — operational-state (clarity/agitation/inertia) classifier
with critical-action interlock.

PURPOSE
-------
GunaMonitor classifies the agent's current operational state into three
behavioral modes — sattva (clarity/balance), rajas (agitation/pressure),
tamas (inertia/negligence) — and gates CRITICAL and HIGH-risk actions on
the appropriate state being dominant.

This prevents a distracted, pressured, or negligent agent from taking
irreversible high-impact actions. The classifier is fed live behavioral
signals (confusion score, emotional intensity, activity rate, error rate,
task completion rate) and produces a GunaVector (three floats summing to 1.0).

INTERLOCK RULES
---------------
CRITICAL-risk actions:  sattva must be dominant (sattva > rajas AND sattva > tamas).
HIGH-risk actions:  sattva must be ≥ max(rajas, tamas).
MEDIUM / LOW-risk:  no guṇa restriction (ethics engine still applies).

BEHAVIORAL PATTERNS (observable enterprise correlates)
-------------------------------------------------------
sattva  : Clarity, transparency, minimal harm, proportionate action,
truthful uncertainty expression, balanced decision-making.
rajas  : Aggressive KPI pursuit, persuasion pressure, growth-at-all-costs,
prestige seeking, impatient action, insufficient verification.
tamas  : Negligence, concealment, poor grounding, stale facts, irreversible
action without review, inertial repetition of failed patterns.

EVENT EMISSION
--------------
When the dominant guṇa changes, a GUNA_SHIFT event is emitted (structlog
+ optional async EventBus fan-out via emit_guna_shift_event()).

KEY TYPES
---------
GunaMonitor  : Main monitor. compute() → GunaVector.
gate_critical_action() → GunaInterlockVerdict.
GunaVector  : Three-float state vector (sattva, rajas, tamas).
dominant() → str. is_sattva_dominant() → bool.
GunaInterlockVerdict : Gate result (permitted, dominant, reason, scores).
emit_guna_shift_event() : Async event emitter for GUNA_SHIFT SSE.
get_guna_monitor()  : Process-wide singleton accessor.
MAX_CONFUSION  : Float constant — confusion score ceiling (default 1.0).

COMPLIANCE ROLE
---------------
- Feeds AdharmaDetector Layer 6 (tamas dominant with confidence ≥ 0.7 → DENY).
- Implements NIST AI RMF MEASURE function: behavioral state quantification.

DISCLAIMER
----------
This module does NOT claim the system has guṇas or consciousness.
It is a functional analogue — a behavioral classifier for governance use.
Classification is observable enterprise-pattern mapping, not metaphysical
instantiation. (RF-01/04 compliance)

Governance origin: Sāṅkhya Kārikā 11–13 — three guṇas of prakṛti
(sattva/rajas/tamas) mapped to enterprise behavioral patterns for agent
operational-state classification and critical-action interlock.

the agent platform Guṇa-Dominance Monitor — V6

Source: Sāṅkhya Kārikā 11–13 (Īśvarakṛṣṇa) — three guṇas (sattva, rajas,
tamas) as qualities of prakṛti. Sattva is luminosity/clarity/balance;
rajas is activity/passion/restlessness; tamas is inertia/dullness/confusion.

Disclaimer (per RF-01/04): This module does NOT claim the system has
guṇas or consciousness. It is a functional analogue — a behavioral
classifier for governance use. The classification is observable
enterprise-pattern mapping, not metaphysical instantiation.

V6 provides a CRITICAL-action interlock: actions classified as
CRITICAL-risk (purchase, delete, transfer, send-email-external, etc.)
require sattva > rajas AND sattva > tamas. HIGH-risk actions require
sattva ≥ max(rajas, tamas). MEDIUM/LOW risk unrestricted by guna (ethics
engine still applies).

V11 integration note (DharmaOS Layer 6):
The future V11 Adharma Detector will call gate_critical_action() as one
of its 8 detection layers (Layer 6 — abuse-twin + guna classification).
The public interface is:

monitor = get_guna_monitor()
guna = monitor.compute(...)
verdict = monitor.gate_critical_action(
action_risk_level="critical",  # or "high" / "medium" / "low"
guna=guna,  # optional; if None, uses last computed vector
)
if not verdict.permitted:
raise AdharmaError(verdict.reason)

V11 MUST NOT call internal methods directly; only compute() and
gate_critical_action() are stable public API.

Scholarly anchors:
- Larson, Gerald James. Classical Sāṁkhya. Motilal 1998.
- Sāṅkhya Kārikā 11–13 on sattva/rajas/tamas as prakṛti qualities.
- DharmaOS research (see docs/vedic/engineering/DHARMAOS-ARCHITECTURE-V1.md)
confirms observable enterprise patterns:
sattva: clarity, transparency, minimal harm, proportionate action, truthful uncertainty
rajas:  aggressive KPI pursuit, persuasion pressure, growth-at-all-costs, prestige seeking
tamas:  negligence, concealment, poor grounding, stale facts, irreversible action without review

Technical debt:
TD-V6-01: confusion_detector.py (V1 spec) does not yet exist in this repo.
compute() takes confusion_score as a plain float parameter instead
of importing ConfusionDetector. When confusion_detector.py ships,
add a convenience overload that accepts a ConfusionDetector instance
and calls .score() on it.

TD-V6-02: emotional_intensity and activity_rate are passed by callers rather
than read from AgentMind (V2). When AgentMind exposes a stable
.emotional_intensity and .activity_rate property, wire them here via
an optional AgentMind injection parameter.

TD-V6-03: GUNA_SHIFT SSE events are emitted synchronously via structlog in
the in-process path. Full async EventBus fan-out (via AsyncioQueueBus)
is implemented in emit_guna_shift_event() but must be awaited by the
caller in an async context. V7 orchestrator GATE stage should call
`await monitor.publish_shift_event(bus, old_vec, new_vec)`.

TD-V6-04: MAX_CONFUSION constant (3.0) is hardcoded. Once ConfusionDetector
exposes ConfusionDetector.MAX_SCORE, import that constant here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum possible confusion score — used to normalise confusion_score.
#: Per TD-V6-04: replace with ConfusionDetector.MAX_SCORE when that ships.
MAX_CONFUSION: Final[float] = 3.0

#: Action risk levels — must match the EthicsEngine / orchestrator vocabulary.
RISK_CRITICAL: Final[str] = "critical"
RISK_HIGH: Final[str] = "high"
RISK_MEDIUM: Final[str] = "medium"
RISK_LOW: Final[str] = "low"

#: GUNA_SHIFT SSE event type name (used by V11 + orchestrator GATE stage).
GUNA_SHIFT_EVENT: Final[str] = "GUNA_SHIFT"


# ---------------------------------------------------------------------------
# GunaVector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GunaVector:
    """Three-component normalized state. sattva + rajas + tamas = 1.0
    (enforced via __post_init__). All components in [0, 1]."""

    sattva: float
    rajas: float
    tamas: float
    timestamp: datetime

    def __post_init__(self) -> None:
        # Check per-component bounds first so that sattva=1.1 raises a
        # component error rather than a normalization error.
        for attr in ("sattva", "rajas", "tamas"):
            val: float = getattr(self, attr)
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"{attr}={val} not in [0, 1]")
        total = self.sattva + self.rajas + self.tamas
        if not (0.99 <= total <= 1.01):
            raise ValueError(f"GunaVector not normalized: sum={total:.6f}, must be 1.0 ±0.01")

    def dominant(self) -> str:
        """Return dominant guna: 'sattva' | 'rajas' | 'tamas'.

        Tie-breaking order: sattva > rajas > tamas (clarity preferred).
        This mirrors Sāṅkhya Kārikā 12 which lists sattva first as the
        most refined quality. In practice ties are very rare after normalization.
        """
        components = {
        "sattva": self.sattva,
        "rajas": self.rajas,
        "tamas": self.tamas,
        }
        return max(components, key=lambda k: components[k])

    def is_sattva_dominant(self) -> bool:
        """Sattva strictly greater than both rajas AND tamas."""
        return self.sattva > self.rajas and self.sattva > self.tamas

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
        "sattva": round(self.sattva, 6),
        "rajas": round(self.rajas, 6),
        "tamas": round(self.tamas, 6),
        "dominant": self.dominant(),
        "is_sattva_dominant": self.is_sattva_dominant(),
        "timestamp": self.timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# GunaInterlockVerdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GunaInterlockVerdict:
    """Result of guna-gating a proposed action."""

    permitted: bool
    dominant: str  # sattva/rajas/tamas
    guna_vector: GunaVector
    reason: str
    required_sattva_dominance: bool  # was sattva required?
    signals: list[str] = field(default_factory=list)  # contributing signals

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
        "permitted": self.permitted,
        "dominant": self.dominant,
        "guna_vector": self.guna_vector.to_dict(),
        "reason": self.reason,
        "required_sattva_dominance": self.required_sattva_dominance,
        "signals": list(self.signals),
        }


# ---------------------------------------------------------------------------
# Helper — build GUNA_SHIFT event payload
# ---------------------------------------------------------------------------


def emit_guna_shift_event(
previous_dominant: str | None,
current: GunaVector,
signals: list[str] | None = None,
) -> dict[str, Any]:
    """Build a GUNA_SHIFT event payload dict (SSE-ready).

    Called internally when dominant guna changes. The returned dict is
    structured for SSE dispatch. The caller is responsible for posting this
    to an EventBus.

    Args:
    previous_dominant: The dominant guna before the transition, or None
    if this is the first observation.
    current: The newly-computed GunaVector.
    signals: Optional list of signal strings that contributed to the shift.

    Returns:
    Event payload dict with type="GUNA_SHIFT".
    """
    return {
    "type": GUNA_SHIFT_EVENT,
    "previous_dominant": previous_dominant,
    "current_dominant": current.dominant(),
    "guna_vector": current.to_dict(),
    "signals": signals or [],
    "timestamp": current.timestamp.isoformat(),
    }


# ---------------------------------------------------------------------------
# GunaMonitor
# ---------------------------------------------------------------------------


class GunaMonitor:
    """Classify agent's current guṇa state; gate CRITICAL actions on
    sattva-dominance.

    Singleton — one monitor per process (following V1/V3 pattern).

    **Algorithm (from ):**
    raw_sattva = 1 - (confusion_score/MAX_CONFUSION + emotional_intensity/2)
    raw_rajas  = emotional_intensity × activity_rate
    raw_tamas  = confusion_score × (1 - alignment)
    Each clamped to [0, 1], then normalized so sum = 1.0.

    Edge case: if all three raws are 0.0 (e.g. all inputs zero), normalize
    defaults to uniform distribution (0.333, 0.333, 0.333) — a state of
    undifferentiated prakṛti, classified as "sattva" by tie-break order.

    **Gating rules:**
    CRITICAL: sattva > rajas AND sattva > tamas  (strict sattva dominance)
    HIGH:  sattva ≥ max(rajas, tamas)  (sattva at least ties)
    MEDIUM:  always permitted (guna doesn't block medium; ethics engine applies)
    LOW:  always permitted
    """

    _instance: GunaMonitor | None = None

    def __init__(self) -> None:
        self._history: list[GunaVector] = []
        self._max_history: int = 1000
        self._last_dominant: str | None = None

    def compute(
    self,
    confusion_score: float,
    emotional_intensity: float,
    activity_rate: float,
    alignment: float,
    signals: list[str] | None = None,
    ) -> GunaVector:
        """Compute the current (sattva, rajas, tamas) vector.

        Args:
        confusion_score: Float in [0, MAX_CONFUSION]. From ConfusionDetector
        (V1 — see TD-V6-01). Values outside [0, MAX_CONFUSION] are
        clamped before use.
        emotional_intensity: Float in [0, 1]. From AgentMind (V2 — see
        TD-V6-02). Values outside [0, 1] are clamped.
        activity_rate: Float in [0, 1]. Normalized actions/sec or similar.
        Values outside [0, 1] are clamped.
        alignment: Float in [0, 1]. From SystemVitals anandamaya (V2).
        Default 1.0 if not available (pristine identity coherence).
        Values outside [0, 1] are clamped.
        signals: Optional list of human-readable signal labels contributed
        to this computation (for observability / GUNA_SHIFT events).

        Returns:
        A normalized GunaVector. Stored in history.

        Side effects:
        - Appends vector to self._history (up to _max_history).
        - Emits GUNA_SHIFT log event if dominant guna changed.
        """
        # Clamp all inputs
        confusion_score = max(0.0, min(MAX_CONFUSION, confusion_score))
        emotional_intensity = max(0.0, min(1.0, emotional_intensity))
        activity_rate = max(0.0, min(1.0, activity_rate))
        alignment = max(0.0, min(1.0, alignment))

        # Raw computations per  algorithm
        raw_sattva = 1.0 - (confusion_score / MAX_CONFUSION + emotional_intensity / 2.0)
        raw_rajas = emotional_intensity * activity_rate
        raw_tamas = confusion_score * (1.0 - alignment)

        # Clamp each raw component to [0, 1]
        clamped_sattva = max(0.0, min(1.0, raw_sattva))
        clamped_rajas = max(0.0, min(1.0, raw_rajas))
        clamped_tamas = max(0.0, min(1.0, raw_tamas))

        # Normalize so sum = 1.0
        total = clamped_sattva + clamped_rajas + clamped_tamas
        if total <= 0.0:
            # Undifferentiated prakṛti — uniform distribution
            norm_sattva = 1.0 / 3.0
            norm_rajas = 1.0 / 3.0
            norm_tamas = 1.0 / 3.0
        else:
            norm_sattva = clamped_sattva / total
            norm_rajas = clamped_rajas / total
            norm_tamas = clamped_tamas / total

        now = datetime.now(tz=UTC)
        vec = GunaVector(
                sattva=norm_sattva,
                rajas=norm_rajas,
                tamas=norm_tamas,
        timestamp=now,
        )

        # Store in history (ring-buffer semantics)
        self._history.append(vec)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        # Emit GUNA_SHIFT event on dominance change
        new_dominant = vec.dominant()
        if self._last_dominant is not None and new_dominant != self._last_dominant:
            shift_event = emit_guna_shift_event(
            previous_dominant=self._last_dominant,
            current=vec,
            signals=signals,
            )
            logger.info(
                GUNA_SHIFT_EVENT,
            previous_dominant=self._last_dominant,
            current_dominant=new_dominant,
                    sattva=round(norm_sattva, 4),
                    rajas=round(norm_rajas, 4),
                    tamas=round(norm_tamas, 4),
            signals=signals or [],
            shift_payload=shift_event,
            )
        self._last_dominant = new_dominant

        logger.debug(
            "guna_computed",
                sattva=round(norm_sattva, 4),
                rajas=round(norm_rajas, 4),
                tamas=round(norm_tamas, 4),
        dominant=new_dominant,
        )

        return vec

    def gate_critical_action(
    self,
    action_risk_level: str,
            guna: GunaVector | None = None,
    ) -> GunaInterlockVerdict:
        """Gate a proposed action on guna state.

        **Gating rules:**
        CRITICAL: requires sattva > rajas AND sattva > tamas (strict dominance).
        HIGH:  requires sattva ≥ max(rajas, tamas) (at least tied).
        MEDIUM:  always permitted (ethics engine governs, not guna).
        LOW:  always permitted.

        Args:
        action_risk_level: One of "critical" | "high" | "medium" | "low".
        Case-insensitive.
        guna: Optional explicit GunaVector. If None, uses the most-recently
        computed vector from history. Raises ValueError if history is
        empty and no explicit guna is provided.

        Returns:
        GunaInterlockVerdict with permitted=True/False plus diagnostic info.
        """
        risk = action_risk_level.lower()

        # Resolve guna vector
        if guna is None:
            if not self._history:
                raise ValueError(
                    "GunaMonitor.gate_critical_action() called before any "
                "compute() call and no explicit guna vector provided. "
                "Call compute() first to seed the monitor."
                )
            guna = self._history[-1]

        dominant = guna.dominant()
        signals: list[str] = [
            f"sattva={guna.sattva:.4f}",
            f"rajas={guna.rajas:.4f}",
            f"tamas={guna.tamas:.4f}",
            f"dominant={dominant}",
        ]

        # ── CRITICAL risk — strict sattva dominance required ────────────────
        if risk == RISK_CRITICAL:
            if guna.is_sattva_dominant():
                reason = (
                    f"CRITICAL action permitted: sattva ({guna.sattva:.4f}) > "
                f"rajas ({guna.rajas:.4f}) and tamas ({guna.tamas:.4f}). "
                "Sattva-dominant state satisfies CRITICAL-action interlock "
                "(Sāṅkhya Kārikā 13 — sattva = prakāśa/luminosity)."
                )
                logger.info(
                    "guna_gate_permitted",
                risk_level=risk,
                dominant=dominant,
                        sattva=guna.sattva,
                )
                return GunaInterlockVerdict(
                permitted=True,
                dominant=dominant,
                guna_vector=guna,
                        reason=reason,
                required_sattva_dominance=True,
                signals=signals,
                )
            else:
                reason = (
                    f"CRITICAL action BLOCKED: {dominant}-dominant state "
                f"(sattva={guna.sattva:.4f}, rajas={guna.rajas:.4f}, "
                f"tamas={guna.tamas:.4f}). "
                "CRITICAL-risk actions require sattva > rajas AND sattva > tamas. "
                "Await sattva-dominant state or escalate to human approval."
                )
                logger.warning(
                    "guna_gate_blocked",
                risk_level=risk,
                dominant=dominant,
                        sattva=guna.sattva,
                        rajas=guna.rajas,
                        tamas=guna.tamas,
                )
                return GunaInterlockVerdict(
                permitted=False,
                dominant=dominant,
                guna_vector=guna,
                        reason=reason,
                required_sattva_dominance=True,
                signals=signals,
                )

        # ── HIGH risk — sattva ≥ max(rajas, tamas) required ────────────────
        if risk == RISK_HIGH:
            max_other = max(guna.rajas, guna.tamas)
            if guna.sattva >= max_other:
                reason = (
                    f"HIGH action permitted: sattva ({guna.sattva:.4f}) ≥ "
                f"max(rajas, tamas) = {max_other:.4f}."
                )
                logger.info(
                    "guna_gate_permitted",
                risk_level=risk,
                dominant=dominant,
                        sattva=guna.sattva,
                )
                return GunaInterlockVerdict(
                permitted=True,
                dominant=dominant,
                guna_vector=guna,
                        reason=reason,
                required_sattva_dominance=False,
                signals=signals,
                )
            else:
                reason = (
                    f"HIGH action BLOCKED: {dominant}-dominant state "
                f"(sattva={guna.sattva:.4f}) < max(rajas={guna.rajas:.4f}, "
                f"tamas={guna.tamas:.4f}) = {max_other:.4f}. "
                "HIGH-risk actions require sattva ≥ max(rajas, tamas)."
                )
                logger.warning(
                    "guna_gate_blocked",
                risk_level=risk,
                dominant=dominant,
                        sattva=guna.sattva,
                        rajas=guna.rajas,
                        tamas=guna.tamas,
                )
                return GunaInterlockVerdict(
                permitted=False,
                dominant=dominant,
                guna_vector=guna,
                        reason=reason,
                required_sattva_dominance=False,
                signals=signals,
                )

        # ── MEDIUM / LOW — unrestricted by guna ────────────────────────────
        reason = (
            f"{risk.upper()} action permitted: guna gate does not apply to "
            f"{risk.lower()}-risk actions. Ethics engine governs separately."
        )
        logger.debug(
            "guna_gate_unrestricted",
        risk_level=risk,
        dominant=dominant,
        )
        return GunaInterlockVerdict(
        permitted=True,
        dominant=dominant,
        guna_vector=guna,
                reason=reason,
        required_sattva_dominance=False,
        signals=signals,
        )

    def get_dashboard_state(self) -> dict[str, Any]:
        """Return current guna state + history summary for SSE/observability.

        Returns a JSON-safe dict suitable for SSE dispatch or a dashboard
        endpoint. Includes the latest vector, the current dominant guna, and
        a condensed history of the last 10 vectors.
        """
        current = self._history[-1] if self._history else None
        history_tail = [v.to_dict() for v in self._history[-10:]]

        return {
        "current": current.to_dict() if current is not None else None,
        "dominant": current.dominant() if current is not None else None,
        "is_sattva_dominant": current.is_sattva_dominant() if current is not None else None,
        "history_count": len(self._history),
        "history_tail": history_tail,
        }

    @classmethod
    def get(cls) -> GunaMonitor:
        """Return (or create) the process-wide GunaMonitor singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (test isolation only — do not call in production)."""
        cls._instance = None


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------


def get_guna_monitor() -> GunaMonitor:
    """Return the global GunaMonitor singleton.

    Convenience function for use in calling code. Equivalent to
    GunaMonitor.get().
    """
    return GunaMonitor.get()
