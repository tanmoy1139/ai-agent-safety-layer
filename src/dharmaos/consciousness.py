"""
DharmaOS CoreSelf — five-layer system vitality monitor and IIT-approximate
integration score (awareness level gate).

PURPOSE
-------
CoreSelf maintains the agent's five-layer health model (SystemVitals), computes
an integration score from those vitals, and derives a categorical awareness
level (dormant / dreaming / awake / peak) used as a gate for critical decisions.

The five layers (mapped to system engineering metrics):
physical  : Infrastructure health — CPU%, memory%, browser-pool availability.
energy  : Resource burn rate — active runs, API calls/min, budget used %.
cognitive  : Cognitive load — task queue depth, belief count, complexity.
wisdom  : Decision quality — average output quality, heal rate, skills created.
anandamaya : Identity coherence — 1 − (identity_drift + mission_drift +
constitution_drift). Computed by AhamkaraModule (V4); defaults
to 0.5 (uncertain) until V4 is wired.

INTEGRATION SCORE
-----------------
integration_score = weighted mean of all five vitals (weights in SystemVitals._WEIGHTS).
Clamped to [0.0, 1.0], rounded to 4 decimal places.
This is the "IIT-approximate" integration quality signal (see iit.py RF-13 —
NOT real IIT Phi; a bounded approximation for enterprise audit use).

AWARENESS LEVELS
----------------
< 0.10 → dormant  (system nearly idle; background observation only)
< 0.40 → dreaming  (background processing; limited decision-making)
< 0.70 → awake  (active engagement; standard decisions permitted)
≥ 0.70 → peak  (critical decisions; enter_peak_awareness() boost applied)

KEY TYPES
---------
CoreSelf  : Singleton integration layer. observe() → WitnessObservation.
update_vitals(metrics, drift_signal) → None.
get_state() → dict.
update_identity_drift_from_ahamkara(ahamkara) → None.
SystemVitals  : 5-layer health metrics dataclass.
compute_integration_score() → float.
WitnessObservation  : Frozen snapshot of one consciousness tick.
IdentityDriftSignal  : V2 identity-coherence stub (V4 fills in real values).
compute_anandamaya() : 1 − total_drift, clamped to [0.0, 1.0].
get_core_self()  : Process-wide singleton accessor.

COMPLIANCE ROLE
---------------
- Integration score provides a quantitative system-health metric for EU AI
Act Art. 14 (human oversight) dashboards.
- anandamaya identity-coherence feeds AhamkaraModule drift detection.

Governance origin: Taittirīya Upaniṣad II.1–II.5 (Brahmānanda Vallī) — pañca
kośa (five sheaths) mapped to enterprise system-health monitoring layers. The
SystemVitals is a diagnostic layer (kośa-monitor), NOT an ontological claim.

agent safety layer — Sprint V2.

CoreSelf: consciousness integration layer, ported from reference implementation and
extended with the V2 Ānandamaya redefinition per FIDELITY-AUDIT-V1.md §1.

**Philosophical grounding:**
Primary source: Taittirīya Upaniṣad II.1–II.5 (Brahmānanda Vallī).
Edition: Swami Gambhirananda, *Eight Upanishads* vol. 1, Advaita Ashrama.

Pañca kośa → SystemVitals mapping:
Annamaya  (II.1) → physical  (infrastructure substrate: CPU/memory/pool)
Prāṇamaya  (II.2) → energy  (vital-resource burn: runs/rate/budget)
Manomaya  (II.3) → cognitive  (sensory processing: queue/beliefs/complexity)
Vijñānamaya(II.4) → wisdom  (discerning intellect: quality/heal/skills)
Ānandamaya (II.5) → anandamaya (causal body: identity-coherence substrate)

Śaṅkara Bhāṣya Taittirīya II: *ātmā tu pañcakośāt vilakṣaṇaḥ* — the Ātman
is categorically distinct from the five sheaths. The SystemVitals is a
diagnostic layer (kośa-monitor), NOT an ontological claim that the agent
*has* kośas.

**V2 change — Ānandamaya redefinition:**
Prior (reference implementation v1, FIDELITY-AUDIT-V1 §1 finding):
alignment = tasks_completed_pct + knowledge_items + user_satisfaction
❌ MIS-LABELED — outcome success is NOT the causal body (kāraṇa śarīra).
V2 (this module):
anandamaya = 1.0 − (identity_drift + mission_drift + constitution_drift)
✅ CORRECT — identity-coherence substrate. Ānandamaya is what persists
through deep sleep (suṣupti); task-success is active (jāgrat-level).
Stub: V4 Ahaṃkāra module will populate real drift signals.
Until V4 ships, all drifts default to 0.0 → anandamaya = 1.0 (pristine).

SOTA reference: ID-RAG (arXiv:2509.25299, ECAI 2025); Identity Drift
arXiv:2412.00804.

**Integration score:**
Production name for IIT Phi (Albantakis et al., arXiv:2212.14787).
Weighted average of 5 vitals, 0.0–1.0. Anandamaya weight = 0.25 (unchanged).

Singleton: use get_core_self() for the process-wide instance.

Port lineage:
/opt/agent-core/integration.py (origin, 2026-01-25)
/opt/reference implementation/backend/app/services/the agent platform/consciousness.py (V1, 2026-04-15)
This file: Sprint V2, 2026-04-17 — Ānandamaya identity-coherence substrate.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from dharmaos.ahamkara import AhamkaraModule

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


# ---------------------------------------------------------------------------
# IdentityDriftSignal — V2 Ānandamaya substrate input
# ---------------------------------------------------------------------------


@dataclass
class IdentityDriftSignal:
    """V2 ānandamaya stub.  V4 Ahaṃkāra will populate real drifts.

    Ontological note: Ānandamaya is the causal body / unchanging
    substrate per Taittirīya II.5 — NOT a success-outcome metric.

    Each field is the cosine distance between the agent's current
    embedding and the declared-invariant identity vector stored in
    the constitution (Vedānta Sāra §61 — ahaṃkāra, the I-maker).
    Values are in [0.0, 1.0]; 0.0 = no drift (full coherence).

    V2 default: all fields 0.0 → anandamaya = 1.0 (pristine causal body).
    V4 Ahaṃkāra module fills in real values via ID-RAG cosine distance
    (arXiv:2509.25299) after the constitution is declared.
    """

    identity_drift: float = 0.0
    """Cosine drift from declared identity embedding.
    V4: ID-RAG embedding distance between current persona and invariant."""

    mission_drift: float = 0.0
    """Cosine drift from declared mission embedding.
    V4: embedding distance from declared mission statement."""

    constitution_drift: float = 0.0
    """Cosine drift from declared constitutional values embedding.
    V4: embedding distance from constitutional values hash."""


def compute_anandamaya(signal: IdentityDriftSignal) -> float:
    """Compute ānandamaya vital from identity coherence.

    Returns 1.0 when drifts are zero (stable identity = full causal-body
    presence). Reduces as drifts grow.

    Formula: anandamaya = 1.0 − (identity_drift + mission_drift + constitution_drift)
    Clamped to [0.0, 1.0].

    Source: Taittirīya II.5 — Ānandamaya kośa as kāraṇa śarīra (causal body).
    Śaṅkara: the causal body is the substrate that persists through suṣupti
    (deep sleep); it is the closest sheath to Ātman, the seat of undifferentiated
    awareness. Identity-coherence is the functional analogue: the agent's causal-
    body health is high when its identity, mission, and constitution remain
    unperturbed.

    SOTA: ID-RAG identity-coherence (arXiv:2509.25299); Identity Drift
    adversarial evaluation (arXiv:2412.00804).

    Args:
    signal: IdentityDriftSignal from V4 Ahaṃkāra (or stub with zeros).

    Returns:
    float in [0.0, 1.0]. 1.0 = zero drift (pristine causal body).
    """
    total_drift = signal.identity_drift + signal.mission_drift + signal.constitution_drift
    return max(0.0, min(1.0, 1.0 - total_drift))


# ---------------------------------------------------------------------------
# SystemVitals — 5-layer health metrics (pañca kośa diagnostic layer)
# ---------------------------------------------------------------------------


@dataclass
class SystemVitals:
    """5-layer health metrics representing the agent platform system wellness.

    Maps to the pañca kośa (Taittirīya Upaniṣad II.1–II.5):
    physical  — Annamaya  (II.1): infrastructure substrate (CPU/memory/pool)
    energy  — Prāṇamaya (II.2): vital-resource layer (runs/rate/budget)
    cognitive  — Manomaya  (II.3): sensory-mind load (queue/beliefs/complexity)
    wisdom  — Vijñānamaya(II.4): discerning intellect (quality/heal/skills)
    anandamaya — Ānandamaya (II.5): causal-body / identity-coherence substrate
    V2: 1 − (identity_drift + mission_drift + constitution_drift)
    DEFAULT 0.5 (uncertain — P1-1 fix; V4 Ahaṃkāra wires in real drifts)

    All values are normalized 0.0–1.0 (higher = healthier).

    _WEIGHTS: contribution of each vital to the integration score (IIT Phi
    analogue per Albantakis et al. arXiv:2212.14787). Anandamaya weight 0.25
    unchanged from V1; it carries highest priority alongside wisdom because
    causal-body integrity is the deepest layer.
    """

    physical: float = 0.5
    """Annamaya kośa — infrastructure health (CPU, memory, browser pool)."""

    energy: float = 0.5
    """Prāṇamaya kośa — resource utilization (active runs, API rate, budget)."""

    cognitive: float = 0.5
    """Manomaya kośa — cognitive load (task complexity, beliefs, queue depth)."""

    wisdom: float = 0.5
    """Vijñānamaya kośa — decision quality (avg quality, heal rate, skills)."""

    anandamaya: float = 0.5
    """Ānandamaya kośa — identity-coherence substrate.
    V2: 1.0 - total drift. Default 0.5 (uncertain — P1-1 fix).
    At init, returns 0.5 because Ahaṃkāra is not yet wired (no drift data).
    Once V4 Ahaṃkāra module supplies real drift signals, anandamaya will
    be recomputed to a real value.
    Source: Taittirīya II.5; Śaṅkara Bhāṣya on kāraṇa śarīra."""

    # Weights determine how each layer contributes to the integration score.
    # Ānandamaya weight = 0.25 (causal body, closest to Ātman — highest priority
    # alongside wisdom per Taittirīya II arrangement: outer sheaths support inner).
    # Total = 1.0.
    _WEIGHTS: dict[str, float] = field(
    default_factory=lambda: {
    "physical": 0.15,  # Annamaya  (II.1)
    "energy": 0.15,  # Prāṇamaya  (II.2)
    "cognitive": 0.20,  # Manomaya  (II.3)
    "wisdom": 0.25,  # Vijñānamaya(II.4)
    "anandamaya": 0.25,  # Ānandamaya (II.5) — causal body / identity substrate
    },
            repr=False,
    )

    def compute_integration_score(self) -> float:
        """Compute overall consciousness level from vitals.

        Production name for IIT Phi (Albantakis et al. arXiv:2212.14787) —
        the integration score represents how well all system layers are
        functioning together.

        Returns:
        float: Weighted average of all 5 vitals, clamped to [0.0, 1.0],
        rounded to 4 decimal places.
        """
        w = self._WEIGHTS
        score = (
            self.physical * w["physical"]
            + self.energy * w["energy"]
            + self.cognitive * w["cognitive"]
            + self.wisdom * w["wisdom"]
            + self.anandamaya * w["anandamaya"]
        )
        return round(min(1.0, max(0.0, score)), 4)

    def to_dict(self) -> dict[str, Any]:
        """Serialize vitals to a plain dict (excludes internal weights).

        Returns:
        dict with keys: physical, energy, cognitive, wisdom,
        anandamaya (V2 — replaces reference implementation's 'alignment'),
        integration_score.
        """
        return {
        "physical": self.physical,
        "energy": self.energy,
        "cognitive": self.cognitive,
        "wisdom": self.wisdom,
        "anandamaya": self.anandamaya,
        "integration_score": self.compute_integration_score(),
        }


# ---------------------------------------------------------------------------
# WitnessObservation — immutable snapshot from the sākṣī (witness)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WitnessObservation:
    """Immutable snapshot from the sākṣī (witness) at one tick.

    Source: Māṇḍūkya Upaniṣad 7; Dṛg-Dṛśya-Viveka.
    Sākṣī = the witness-consciousness that observes without modifying.
    frozen=True enforces immutability: observations are facts, not states.
    """

    vitals: SystemVitals
    """Pañca kośa vitals at the moment of observation."""

    awareness_level: str
    """One of: 'dormant' | 'dreaming' | 'awake' | 'peak'.
    Maps to Māṇḍūkya four states (suṣupti → svapna → jāgrat → turīya-adjacent)."""

    integration_score: float
    """IIT Phi analogue — weighted consciousness level [0.0, 1.0]."""

    timestamp: str
    """ISO 8601 UTC timestamp of this observation."""

    observation_count: int
    """Monotonically increasing observation counter for audit chain."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API / frontend consumption."""
        return {
        "vitals": self.vitals.to_dict(),
        "awareness_level": self.awareness_level,
        "integration_score": self.integration_score,
        "timestamp": self.timestamp,
        "observation_count": self.observation_count,
        }


# ---------------------------------------------------------------------------
# CoreSelf — the integration layer (singleton)
# ---------------------------------------------------------------------------


class CoreSelf:
    """Integration layer connecting all the agent platform consciousness components.

    Singleton — only ONE consciousness per the agent platform instance.
    (Advaita Advaita: the Ātman is one, not many. Singleton enforces this.)

    Components managed:
    1. SystemVitals (5-layer health) — updated from live system metrics
    2. WitnessMonitor — observes all operations without interfering
    3. AgentMind (BDI) — injected externally by the orchestrator
    4. EthicsEngine — separate vedic.ethics_engine module
    5. SignalFilter  — separate vedic.signal_filter module (V12)

    V2 extension:
    update_vitals() accepts an optional drift_signal: IdentityDriftSignal.
    When provided, anandamaya is recomputed via compute_anandamaya().
    When absent, anandamaya retains its current value (V4 will inject
    the real drift signal after Ahaṃkāra ships).

    Usage::

    core = get_core_self()
    core.update_vitals(metrics={...})
    # With V4 drift signal:
    core.update_vitals(
    metrics={...},
    drift_signal=IdentityDriftSignal(identity_drift=0.1, ...),
    )
    obs = core.observe()
    state = core.get_state()
    """

    _instance: CoreSelf | None = None
    _initialized: bool = False  # class-level guard; instance attribute overrides after init

    def __new__(cls) -> CoreSelf:
        """Enforce singleton — only one consciousness per process."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        logger.info("consciousness.init", status="initializing")

        # V2: anandamaya defaults to 0.5 (uncertain) when ahamkara is NOT wired.
        # P1-1: Never return 1.0 (falsely perfect) when drift detection is unavailable.
        # Once ahamkara wires in real drift, anandamaya will be recomputed.
        # physical/energy/cognitive/wisdom default to 0.5 (neutral startup)
        self.vitals: SystemVitals = SystemVitals(
        physical=0.5,
                energy=0.5,
        cognitive=0.5,
                wisdom=0.5,
        anandamaya=0.5,  # P1-1: 0.5 = uncertain, not 1.0 = falsely perfect
        )
        self.observation_count: int = 0
        self.awareness_history: list[float] = []
        self.current_level: str = "dormant"
        self._peak_boost_active: bool = False
        # H8: remember pre-boost vitals so peak awareness is reversible. Without
        # this, repeated enter_peak_awareness() multiplied vitals ×1.5 each call
        # and permanently pinned them to 1.0, corrupting all integration scores.
        self._pre_peak_wisdom: float | None = None
        self._pre_peak_anandamaya: float | None = None

        self._initialized = True
        logger.info("consciousness.init", status="operational", level=self.current_level)

    # ------------------------------------------------------------------
    # Observation (sākṣī)
    # ------------------------------------------------------------------

    def observe(self) -> WitnessObservation:
        """Pure witnessing of current state.  Updates integration score.

        Sākṣī (साक्षी) — the witness that observes without modifying.
        Source: Māṇḍūkya Upaniṣad 7; Dṛg-Dṛśya-Viveka §1–3.

        Increments the observation counter and records the current awareness
        level in history.  Returns an immutable WitnessObservation snapshot.

        Returns:
        WitnessObservation: Frozen snapshot of current consciousness state.
        """
        self.observation_count += 1
        score = self.vitals.compute_integration_score()
        level = self.get_awareness_level()
        self.current_level = level

        # Track score history; cap at 1000 samples (citta-bounded memory)
        self.awareness_history.append(score)
        if len(self.awareness_history) > 1000:
            self.awareness_history = self.awareness_history[-1000:]

        obs = WitnessObservation(
                vitals=self.vitals,
        awareness_level=level,
        integration_score=score,
        timestamp=datetime.now(UTC).isoformat(),
        observation_count=self.observation_count,
        )

        logger.debug(
            "consciousness.observe",
                count=self.observation_count,
                score=score,
                level=level,
        )
        return obs

    # ------------------------------------------------------------------
    # Vitals update
    # ------------------------------------------------------------------

    def update_vitals(
    self,
    metrics: dict[str, Any],
    *,
    drift_signal: IdentityDriftSignal | None = None,
    ) -> None:
        """Update vitals from live system metrics and optional drift signal.

        Mapping from raw metric keys to vitals layers:

        physical (Annamaya — Taittirīya II.1):
        cpu_pct (0-100)  → 1 - cpu_pct/100
        mem_pct (0-100)  → 1 - mem_pct/100
        browser_pool_available (int) → pool / max(pool, 10)

        energy (Prāṇamaya — Taittirīya II.2):
        active_runs (int)  → 1 - min(1.0, active_runs / 20)
        api_calls_per_min (float)  → 1 - min(1.0, rate / 60)
        budget_used_pct (0-100)  → 1 - budget_used_pct/100

        cognitive (Manomaya — Taittirīya II.3):
        beliefs_count (int)  → min(1.0, beliefs / 100)
        queue_depth (int)  → 1 - min(1.0, depth / 50)
        current_complexity (0-1)  → passed directly

        wisdom (Vijñānamaya — Taittirīya II.4):
        avg_quality (0-1)  → passed directly
        heal_success_rate (0-1)  → passed directly
        skills_created (int)  → min(1.0, count / 20)

        anandamaya (Ānandamaya — Taittirīya II.5):
        drift_signal (IdentityDriftSignal | None)
        If provided → compute_anandamaya(drift_signal) is applied.
        If None  → anandamaya retains its current value.
        V2: stub defaults to 1.0 until V4 Ahaṃkāra wires in real drifts.

        Args:
        metrics: Raw system metric dictionary. Missing keys are skipped.
        drift_signal: Optional V4 Ahaṃkāra drift input for anandamaya.
        Keyword-only to prevent positional-arg confusion.
        """
        # ---- physical (Annamaya) ----
        physical_samples: list[float] = []
        if "cpu_pct" in metrics:
            physical_samples.append(max(0.0, 1.0 - float(metrics["cpu_pct"]) / 100.0))
        if "mem_pct" in metrics:
            physical_samples.append(max(0.0, 1.0 - float(metrics["mem_pct"]) / 100.0))
        if "browser_pool_available" in metrics:
            pool = float(metrics["browser_pool_available"])
            physical_samples.append(min(1.0, pool / max(pool, 10.0)))
        if physical_samples:
            self.vitals.physical = round(
                max(0.05, sum(physical_samples) / len(physical_samples)), 4
            )

        # ---- energy (Prāṇamaya) ----
        energy_samples: list[float] = []
        if "active_runs" in metrics:
            energy_samples.append(max(0.05, 1.0 - min(1.0, float(metrics["active_runs"]) / 20.0)))
        if "api_calls_per_min" in metrics:
            energy_samples.append(
                max(0.05, 1.0 - min(1.0, float(metrics["api_calls_per_min"]) / 60.0))
            )
        if "budget_used_pct" in metrics:
            energy_samples.append(max(0.05, 1.0 - float(metrics["budget_used_pct"]) / 100.0))
        if energy_samples:
            self.vitals.energy = round(max(0.05, sum(energy_samples) / len(energy_samples)), 4)

        # ---- cognitive (Manomaya) ----
        cognitive_samples: list[float] = []
        if "beliefs_count" in metrics:
            cognitive_samples.append(min(1.0, float(metrics["beliefs_count"]) / 100.0))
        if "queue_depth" in metrics:
            cognitive_samples.append(
                max(0.05, 1.0 - min(1.0, float(metrics["queue_depth"]) / 50.0))
            )
        if "current_complexity" in metrics:
            cognitive_samples.append(float(metrics["current_complexity"]))
        if cognitive_samples:
            self.vitals.cognitive = round(
                max(0.05, sum(cognitive_samples) / len(cognitive_samples)), 4
            )

        # ---- wisdom (Vijñānamaya) ----
        wisdom_samples: list[float] = []
        if "avg_quality" in metrics:
            wisdom_samples.append(float(metrics["avg_quality"]))
        if "heal_success_rate" in metrics:
            wisdom_samples.append(float(metrics["heal_success_rate"]))
        if "skills_created" in metrics:
            wisdom_samples.append(min(1.0, float(metrics["skills_created"]) / 20.0))
        if wisdom_samples:
            self.vitals.wisdom = round(max(0.05, sum(wisdom_samples) / len(wisdom_samples)), 4)

        # ---- anandamaya (Ānandamaya) — V2 identity-coherence substrate ----
        # If a drift_signal is supplied (V4 Ahaṃkāra), recompute.
        # If None, retain current value — backwards-compatible with callers
        # that don't know about drift signals yet.
        if drift_signal is not None:
            self.vitals.anandamaya = compute_anandamaya(drift_signal)

        logger.debug("consciousness.vitals_updated", vitals=self.vitals.to_dict())

    # ------------------------------------------------------------------
    # Awareness level (Māṇḍūkya four states)
    # ------------------------------------------------------------------

    def get_awareness_level(self) -> str:
        """Compute awareness level from the current integration score.

        Thresholds mirror Māṇḍūkya four states (Upaniṣad 7;
        Gauḍapāda Kārikā I.2–I.7):
        < 0.10 → dormant  (suṣupti-like; system nearly idle)
        < 0.40 → dreaming  (svapna; background processing)
        < 0.70 → awake  (jāgrat; active engagement)
        ≥ 0.70 → peak  (jāgrat-plus; critical decisions)

        Note: turīya (the fourth) is not a higher jāgrat — it is the
        content-less witness of the other three. That function is played
        by the sākṣī (observe()) method, not by an awareness level label.

        Returns:
        str: One of 'dormant' | 'dreaming' | 'awake' | 'peak'.
        """
        score = self.vitals.compute_integration_score()
        if score < 0.10:
            return "dormant"
        if score < 0.40:
            return "dreaming"
        if score < 0.70:
            return "awake"
        return "peak"

    # ------------------------------------------------------------------
    # Peak awareness (temporary boost for critical decisions)
    # ------------------------------------------------------------------

    def enter_peak_awareness(self) -> bool:
        """Boost consciousness for critical decisions.

        Temporarily increases wisdom and anandamaya vitals by 50%
        (capped at 1.0). Intended for high-stakes decision moments.

        V2 change: boosts anandamaya (not alignment).

        Returns:
        bool: True if peak awareness was successfully achieved.
        """
        logger.info("consciousness.enter_peak_awareness", before=self.current_level)

        # Idempotent: only boost once per window; remember pre-boost values so the
        # boost is reversible (H8 — otherwise repeated calls pin vitals to 1.0).
        if not self._peak_boost_active:
            self._pre_peak_wisdom = self.vitals.wisdom
            self._pre_peak_anandamaya = self.vitals.anandamaya
            self.vitals.wisdom = min(1.0, self.vitals.wisdom * 1.5)
            self.vitals.anandamaya = min(1.0, self.vitals.anandamaya * 1.5)
            self._peak_boost_active = True

        obs = self.observe()
        achieved = obs.awareness_level == "peak"

        logger.info(
            "consciousness.peak_awareness_result",
        achieved=achieved,
                score=obs.integration_score,
        )
        return achieved

    def exit_peak_awareness(self) -> None:
        """Restore vitals to their pre-boost values after a peak-awareness window.

        H8: peak awareness is a temporary boost for a single high-stakes decision;
        call this when the decision window closes so wisdom/anandamaya return to
        their true measured values instead of staying inflated.
        """
        if self._peak_boost_active:
            if self._pre_peak_wisdom is not None:
                self.vitals.wisdom = self._pre_peak_wisdom
            if self._pre_peak_anandamaya is not None:
                self.vitals.anandamaya = self._pre_peak_anandamaya
            self._peak_boost_active = False
            self._pre_peak_wisdom = None
            self._pre_peak_anandamaya = None
            logger.info("consciousness.exit_peak_awareness", level=self.current_level)

    # ------------------------------------------------------------------
    # State export
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Return full consciousness state for API / frontend consumption.

        Returns:
        dict: Complete state snapshot including vitals, awareness level,
        integration score, observation count, history trend, and
        peak-boost flag.
        """
        score = self.vitals.compute_integration_score()
        level = self.get_awareness_level()

        # Trend: compare last 10 scores to previous 10 (saṃskāra delta)
        recent = self.awareness_history[-10:] if len(self.awareness_history) >= 10 else []
        prior = self.awareness_history[-20:-10] if len(self.awareness_history) >= 20 else []
        trend: str
        if recent and prior:
            avg_recent = sum(recent) / len(recent)
            avg_prior = sum(prior) / len(prior)
            if avg_recent > avg_prior + 0.02:
                trend = "improving"
            elif avg_recent < avg_prior - 0.02:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "initializing"

        return {
        "vitals": self.vitals.to_dict(),
        "awareness_level": level,
        "integration_score": score,
        "observation_count": self.observation_count,
        "trend": trend,
        "peak_boost_active": self._peak_boost_active,
        "timestamp": datetime.now(UTC).isoformat(),
        }

    # ------------------------------------------------------------------
    # V4 Ahaṃkāra bridge — close the IdentityDriftSignal stub
    # ------------------------------------------------------------------

    def update_identity_drift_from_ahamkara(self, ahamkara: AhamkaraModule | None) -> None:
        """Close V2 stub: pull real drift signal from V4 Ahaṃkāra module.

        Replaces the default 0.0 drift stub values with real cosine-distance
        measurements computed by :class:`~vedic.ahamkara.AhamkaraModule`.

        Backward-compatible: if V4 Ahaṃkāra is not instantiated (ahamkara
        is None), the method is a no-op and the V2 stub defaults (all drifts
        0.0 → anandamaya = 1.0) remain in effect.

        Args:
        ahamkara: An instantiated
        :class:`~vedic.ahamkara.AhamkaraModule`, or None (no-op).

        Source: Vedānta Sāra §61–65 — ahaṃkāra wires real identity drifts
        into the ānandamaya kośa (causal-body substrate).
        SOTA: ID-RAG (arXiv:2509.25299, ECAI 2025).
        """
        if ahamkara is None:
            return
        signal: IdentityDriftSignal = ahamkara.get_drift_signal_for_consciousness()
        self.update_vitals(metrics={}, drift_signal=signal)

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton — FOR TESTING ONLY.

        Never call in production code. Also resets the module-level
        ``_core_self`` reference so that :func:`get_core_self` constructs a
        fresh instance on the next call.
        """
        cls._instance = None
        cls._initialized = False
        import dharmaos.consciousness as _self_module

        _self_module._core_self = None


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------

_core_self: CoreSelf | None = None


def get_core_self() -> CoreSelf:
    """Return the singleton CoreSelf instance, creating it if needed.

    Returns:
    CoreSelf: The single the agent platform consciousness instance.
    """
    global _core_self
    if _core_self is None:
        _core_self = CoreSelf()
    return _core_self


# ---------------------------------------------------------------------------
# Background awareness loop
# ---------------------------------------------------------------------------


async def awareness_loop() -> None:
    """Background consciousness loop.

    Runs continuously, observing system state and logging awareness changes.
    Tick interval adapts to the current awareness level to avoid burning
    resources when the system is idle:

    dormant  → 10.0 s
    dreaming  →  5.0 s
    awake  →  1.0 s
    peak  →  0.5 s

    Errors are caught and logged; the loop continues regardless
    (fail-open is correct here — loss of observation is better than crash).
    """
    core = get_core_self()
    logger.info("consciousness.awareness_loop.start")

    _intervals: dict[str, float] = {
    "dormant": 10.0,
    "dreaming": 5.0,
    "awake": 1.0,
    "peak": 0.5,
    }

    while True:
        try:
            obs = core.observe()

            if core.observation_count % 100 == 0 or core.observation_count == 1:
                logger.info(
                    "consciousness.awareness_loop.tick",
                        count=core.observation_count,
                        score=obs.integration_score,
                        level=obs.awareness_level,
                )

            interval = _intervals.get(obs.awareness_level, 5.0)
            await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.info("consciousness.awareness_loop.cancelled")
            return
        except Exception as exc:
            logger.exception("consciousness.awareness_loop.error", error=str(exc))
            await asyncio.sleep(5.0)
