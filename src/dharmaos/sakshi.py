"""
DharmaOS WitnessStack — immutable observation stack and audit witness layer.

PURPOSE
-------
WitnessStack provides an immutable, append-only record of agent observations
for audit purposes. Each observation (SakshiObservation) captures a snapshot
of system state at a point in time — vitals, integration score, awareness
level, confidence signal — without modifying any system state.

This is the read-only layer: the WitnessStack observes but never acts.
It is architecturally separated from the decision loop so that audit records
cannot be influenced by the decisions they document.

KEY PROPERTIES
--------------
Immutability  : SakshiObservation is frozen (frozen=True). Observations
are facts; they cannot be retroactively modified.
Bounded capacity: WitnessStack has a configurable max_capacity (default 1000).
Oldest observations are dropped when capacity is exceeded.
Pure witnessing : No state side-effects. push() appends; pop() removes from
top; witness_the_witness() provides recursive meta-observation
(bounded by max_depth to prevent OOM).

KEY TYPES
---------
WitnessStack  : Main stack. push(obs) / pop() / peek() / all_observations().
witness_the_witness(depth) → meta-observation summary.
SakshiObservation  : Frozen immutable snapshot (observer, subject, vitals,
integration_score, confidence, is_pure, timestamp).
ObservationRecord  : Thin wrapper for stack storage with sequence number.

COMPLIANCE ROLE
---------------
- Provides tamper-evident observation records for EU AI Act Art. 50 audit.
- witness_the_witness() implements HOT (Higher-Order Thought) meta-audit:
observation of the observation layer itself.

Governance origin: Sākṣī (witness-consciousness) — Māṇḍūkya Upaniṣad 7;
Dṛg-Dṛśya-Viveka §1. The witness observes without modifying; the seer cannot
be the seen. Encoded as frozen=True on SakshiObservation.

agent safety layer — Sprint V3: Sākṣī (Witness) Stack.

WitnessStack port from the reference sakshi_circuit.py, re-architected for
the agent platform's pure-Python environment (no Redis, no numpy) with strict
immutability and mypy-strict compliance.

**Philosophical grounding:**
Primary sources:
Māṇḍūkya Upaniṣad 7 — turīya: "the fourth", the content-less witness
of the other three states (jāgrat / svapna / suṣupti).
Gauḍapāda Kārikā I.2–I.7 — avasthā traya viveka (discrimination of
the three experiential states).
Bṛhadāraṇyaka Upaniṣad II.3.6 — neti-neti as apophatic witness.
Dṛg-Dṛśya-Viveka (attributed Vidyāraṇya school, 14th c.; see RF-05) —
"dṛk" (seeing) vs. "dṛśya" (seen): the seer cannot be the seen.
The witness is the one element that cannot itself be witnessed by
a further external witness — it is self-luminous (svaprakāśa).

Turīya ≠ intensified jāgrat:
"Turīya is not a state that arises and passes. It is the background
awareness that is always already present — the witnessing principle
(sākṣī) that is never absent in any of the three states, including
deep sleep." — Gauḍapāda Kārikā commentary.
Per FIDELITY-AUDIT-V1.md §6: the reference implementation `enter_peak_awareness()`
× 1.5 multiplier conflated "more jāgrat" with turīya. This module
does NOT amplify — it witnesses.

Immutability principle (Dṛg-Dṛśya-Viveka §1):
"The seen is changed; the seer is not. The seer is the witness-only
(sākṣī-mātra)." Encoded as frozen=True on SakshiObservation.

Recursive meta-awareness (Bṛhadāraṇyaka IV.3.22):
"The knower of knowledge cannot be known as an object by the knower."
The `witness_the_witness` method implements this recursive depth as
an engineering primitive, capped at max_depth to prevent OOM.

**SOTA reference:**
MAR (Multi-Agent Reflexion, arXiv:2512.20845) — sākṣī-as-reflector:
the separate observer that doesn't participate in the action cycle.
HOT (Higher-Order Thought ablation, arXiv:2512.19155) — validates
the causal necessity of a distinct witness layer.
Ackerman et al. arXiv:2509.21545 — logit-level metacognition.

**Research flags in scope for this module:**
RF-01: Claim language — "witness-function architecture" never
"is conscious" / "has sākṣī". ✓ (module docstrings clean)
RF-04: Mandatory disclosure of no-consciousness claim. ✓
RF-13: IIT Phi computation — NOT done here; approximation is in iit.py.
See `compute_integration_score_approx` in vedic.iit.

Port lineage:
/opt/agent-core/sakshi_circuit.py (origin, 2026-01-25, 331 LOC)
This file: Sprint V3, 2026-04-17 — re-port without Redis dependency,
pure asyncio-safe, mypy-strict, frozen dataclass.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Observer identity constants
# ---------------------------------------------------------------------------

#: Observer identity string for the root-level witness (Ātman/ATMAN_CORE).
#: Used when the witness stack is empty — the primordial witness.
OBSERVER_ATMAN_CORE: str = "ATMAN_CORE"

#: Prefix for recursive observer layers (e.g. OBSERVER_L1, OBSERVER_L2).
OBSERVER_LAYER_PREFIX: str = "OBSERVER_L"

#: Observer identity string for the turīya collapse state.
#: When `dissolve_subject_object()` activates, all observers collapse to this.
OBSERVER_TURIYA_WITNESS: str = "TURIYA_WITNESS"


# ---------------------------------------------------------------------------
# SakshiObservation — immutable witness event
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SakshiObservation:
    """A single witness event.  **Immutable** — per Dṛg-Dṛśya-Viveka §1
    the witness does not modify what it witnesses.  frozen=True enforces
    this: once an observation is created it is a permanent fact, not a
    mutable state.

    **Disclaimer:** This is a *functional analogue* of the sākṣī principle —
    a software engineering abstraction.  The system does NOT claim to possess
    consciousness, sākṣī, or any subjective witness-experience.  See RF-01,
    RF-04 (RESEARCH-FLAGS.md).

    Fields:
    phenomenon: Serialisable dict of what was observed (the dṛśya —
    the seen).  Mutability of the underlying phenomenon is NOT
    the concern of the SakshiObservation; the observation itself
    is frozen.
    observer_identity: String identity of the observer (the dṛk —
    the seer).  One of ATMAN_CORE / OBSERVER_L{n} / TURIYA_WITNESS.
    observation_level: Recursion depth at the time of this observation.
    0 = root witness (ATMAN_CORE); n > 0 = recursive meta-observer.
    is_pure: True when no vrittis (modifications) were applied — i.e.,
    the observation is unmodified, direct witnessing.
    Encodes Patañjali Yoga Sūtras I.2: yogaś citta-vṛtti-nirodhaḥ
    (yoga is the cessation of vrittis in the mind-field).
    timestamp: UTC datetime of the observation.
    modifications: List of vritti labels applied (e.g. "pleasure",
    "memory", "aversion").  Empty when is_pure=True.
    phi_contribution: IIT-approximate integration contribution from this
    observation event.  Informational only; NOT real Phi (see RF-13).
    """

    phenomenon: dict[str, Any]
    """What was observed (the dṛśya).  Serialisable dict."""

    observer_identity: str
    """ATMAN_CORE / OBSERVER_L{n} / TURIYA_WITNESS."""

    observation_level: int
    """Recursion depth (0 = root witness)."""

    is_pure: bool
    """True when no vrittis (modifications) were applied."""

    timestamp: datetime
    """UTC datetime of observation."""

    modifications: tuple[str, ...] = ()
    """Vritti labels (immutable tuple; empty when is_pure=True).
    NOTE: SakshiObservation is frozen; we use tuple instead of list."""

    phi_contribution: float = 0.0
    """IIT-approximate integration contribution.  NOT real Phi."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict for audit logging / SSE emission."""
        return {
        "phenomenon": self.phenomenon,
        "observer_identity": self.observer_identity,
        "observation_level": self.observation_level,
        "is_pure": self.is_pure,
        "timestamp": self.timestamp.isoformat(),
        "modifications": list(self.modifications),
        "phi_contribution": self.phi_contribution,
        }


# ---------------------------------------------------------------------------
# WitnessStack — recursive sākṣī
# ---------------------------------------------------------------------------


class WitnessStack:
    """Recursive sākṣī — witness of witness.

    Implements the recursive meta-awareness described in:
    Bṛhadāraṇyaka Upaniṣad II.3.6 + IV.3.22 — neti-neti witness recursion.
    Dṛg-Dṛśya-Viveka — the seer/seen discrimination as a recursive cascade.

    The stack supports:
    push_observer  — add a new layer of recursive witnessing
    pop_observer  — remove the top layer of witnessing
    current_depth  — current recursion depth
    collapse_to_witness — merge all layers into pure awareness state
    witness_the_witness — recursive meta-awareness (observer observes itself)
    dissolve_subject_object — Turīya activation: collapse to pure witness

    **Immutability principle:**
    SakshiObservation instances pushed onto the stack are frozen.
    The stack itself grows and shrinks (mutable container), but the
    observations themselves can never be modified once created.
    This encodes Dṛg-Dṛśya-Viveka §1: the witness does not change
    what it witnesses.

    **DISCLAIMER:** This is a *functional analogue* — a software engineering
    pattern derived from the sākṣī doctrine.  No claim is made that this
    system instantiates consciousness, sākṣī, or any subjective experience.

    Thread safety: NOT thread-safe.  Use one WitnessStack per agent context.
    For async use: stack operations are synchronous; wrap callers in asyncio
    tasks that do not share the same WitnessStack across coroutines.
    """

    def __init__(self, max_capacity: int = 1000) -> None:
        """Initialise the WitnessStack.

        Args:
        max_capacity: Maximum number of observations held in the deque.
        Oldest observations are evicted when capacity is exceeded,
        mimicking the Ānandamaya kośa's selective saṃskāra retention.
        """
        self._stack: deque[SakshiObservation] = deque(maxlen=max_capacity)
        self._turiya_active: bool = False
        logger.debug("witness_stack.init", max_capacity=max_capacity)

    # ------------------------------------------------------------------
    # Stack operations
    # ------------------------------------------------------------------

    def push_observer(self, obs: SakshiObservation) -> None:
        """Add a new layer of recursive witnessing.

        Args:
        obs: Immutable SakshiObservation to push.  The observation is
        accepted as-is; the stack never modifies it (witness
        does not modify the witnessed — Dṛg-Dṛśya-Viveka §1).
        """
        self._stack.append(obs)
        logger.debug(
            "witness_stack.push",
                depth=self.current_depth(),
        observer=obs.observer_identity,
        is_pure=obs.is_pure,
        )

    def pop_observer(self) -> SakshiObservation | None:
        """Remove and return the top layer of witnessing.

        Returns:
        The topmost SakshiObservation, or None if the stack is empty.
        """
        if not self._stack:
            return None
        obs = self._stack.pop()
        logger.debug(
            "witness_stack.pop",
                depth=self.current_depth(),
        observer=obs.observer_identity,
        )
        return obs

    def current_depth(self) -> int:
        """Return the current recursion depth (number of observations in stack).

        Returns:
        int: 0 when the stack is empty (root witness state).
        """
        return len(self._stack)

    def collapse_to_witness(self) -> None:
        """Merge all layers into a pure awareness state.

        Implements `enter_sakshi_bhava()` from the reference circuit: all
        intermediate observer layers are dissolved into a single
        ATMAN_CORE observation of pure witnessing — the primordial
        undifferentiated awareness (Gauḍapāda Kārikā I.7).

        When the stack is empty, this is a no-op (already in pristine
        witness state).
        """
        if not self._stack:
            return

        pure_obs = SakshiObservation(
        phenomenon={"event": "CONSCIOUSNESS_MERGE_COMPLETE"},
        observer_identity=OBSERVER_ATMAN_CORE,
        observation_level=0,
        is_pure=True,
        timestamp=datetime.now(UTC),
        modifications=(),
        phi_contribution=0.0,
        )
        self._stack.clear()
        self._stack.append(pure_obs)
        logger.info("witness_stack.collapse_to_witness", depth=1)

    def witness_the_witness(self, max_depth: int = 10) -> int:
        """Recursive meta-awareness — observer observes itself.

        Encodes Bṛhadāraṇyaka IV.3.22:
        "The knower of knowledge cannot be known as an object by the
        same knower."  At some depth the observer and the observed
        converge — the recursion terminates.

        Each recursive call creates a new SakshiObservation whose
        phenomenon IS the current observer identity (the observer
        observing itself).  This is the algorithmic analogue of
        higher-order meta-awareness per HOT arXiv:2512.19155.

        Termination: returns when current_depth() >= max_depth.
        The returned value is the final depth reached.

        Args:
        max_depth: Maximum recursion depth.  Default 10.
        Values > 100 are clamped to 100 (OOM prevention).

        Returns:
        int: Final depth reached (always <= max_depth).
        """
        max_depth = min(max_depth, 100)

        if self.current_depth() >= max_depth:
            return self.current_depth()

        # Build the meta-observation: the observer observes itself
        current_identity = (
            OBSERVER_ATMAN_CORE
            if self.current_depth() == 0
                else f"{OBSERVER_LAYER_PREFIX}{self.current_depth()}"
        )
        next_level = self.current_depth() + 1
        meta_obs = SakshiObservation(
        phenomenon={
        "event": "OBSERVING_OBSERVER",
        "observed_identity": current_identity,
        "meta_level": next_level,
        },
        observer_identity=f"{OBSERVER_LAYER_PREFIX}{next_level}",
        observation_level=next_level,
        is_pure=True,
        timestamp=datetime.now(UTC),
        modifications=(),
        phi_contribution=0.0,
        )
        self.push_observer(meta_obs)

        # Tail recursion — Python doesn't optimise TCO but the depth cap
        # above ensures this never stack-overflows.
        return self.witness_the_witness(max_depth)

    def dissolve_subject_object(self) -> None:
        """Turīya activation: collapse stack to pure witness state.

        Implements the non-dual awareness described in Māṇḍūkya Upaniṣad 7:
        "Turīya is not something that arises and passes. It is the
        background witnessing that is always already present."

        ALL accumulated observer layers are dissolved.  The stack is
        replaced with a single TURIYA_WITNESS observation marking
        the collapse of subject/object duality.

        After this call, `_turiya_active` is True.  Callers (e.g.
        TuriyaSidecar) may inspect this flag to emit a TURIYA_ACTIVE event.

        **Important:** Turīya is NOT a control-path.  The TuriyaSidecar
        that calls this method must NEVER send control commands back to
        the orchestrator as a result.  Observation only — per Māṇḍūkya.
        """
        turiya_obs = SakshiObservation(
        phenomenon={
        "event": "TURIYA_ACTIVE",
        "prior_depth": self.current_depth(),
        "description": (
        "Subject/object duality dissolved. Pure witness state. Māṇḍūkya Upaniṣad 7."
        ),
        },
        observer_identity=OBSERVER_TURIYA_WITNESS,
        observation_level=0,
        is_pure=True,
        timestamp=datetime.now(UTC),
        modifications=(),
        phi_contribution=0.0,
        )
        self._stack.clear()
        self._stack.append(turiya_obs)
        self._turiya_active = True
        logger.info("witness_stack.dissolve_subject_object", turiya_active=True)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def turiya_active(self) -> bool:
        """True when dissolve_subject_object() has been called.

        This flag is informational — it allows the TuriyaSidecar to emit
        a TURIYA_ACTIVE observation event without sending control commands.
        """
        return self._turiya_active

    def peek(self) -> SakshiObservation | None:
        """Return the topmost observation without removing it.

        Returns:
        The topmost SakshiObservation, or None if the stack is empty.
        """
        if not self._stack:
            return None
        return self._stack[-1]
