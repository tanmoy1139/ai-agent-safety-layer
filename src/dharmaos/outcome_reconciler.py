"""
    DharmaOS OutcomeReconciler — predicted-vs-observed action outcome reconciler
    and karma-delta calibrator.

    PURPOSE
    -------
    After every executed action, OutcomeReconciler compares what the agent predicted
    (ActionContract.success_criteria + potential_harms) against what actually
    happened (ExecutionReceipt.observed_outcomes + observed_harms), and produces
    an OutcomeDelta with quantified calibration quality.

    The delta drives:
    - V8 SamskaraLedger: appends a SamskaraEntry with the calibrated karma-delta
    so that future AdharmaDetector Layer 5 path-risk checks are informed.
    - V-JÑĀNA JnanaModule: updates capability-vector confidence based on whether
    the agent's predictions matched outcomes.
    - V11 policy_amendment_proposals: evidence for HITL review when outcome
    patterns indicate systematic policy drift.

    OUTCOME METRICS
    ---------------
    criterion_hit_rate  : Fraction of success_criteria that were met
    [0.0, 1.0].
    harm_materialization_rate: Fraction of potential_harms that actually occurred
    [0.0, 1.0].
    brier_calibration_score  : Brier-style score — mean squared error between
    predicted probability and observed binary outcome.
    0.0 = perfect calibration, 1.0 = worst.
    unanticipated_effects  : Observed outcomes not declared in success_criteria
    or potential_harms.
    karma_delta_suggestion  : Calibrated signed float [-1.0, +1.0] for the
    SamskaraLedger entry.

    OUTCOME STATUS TAXONOMY
    -----------------------
    SUCCESS  : All criteria met, no harms, no unanticipated effects.
    PARTIAL_SUCCESS : ≥50% criteria met, no overriding harm.
    FAILURE  : No criteria met, no harms.
    HARMFUL  : One or more harms materialized.
    UNEXPECTED  : Large unanticipated-outcome set with some criteria met.

    KEY TYPES
    ---------
    OutcomeReconciler  : Main reconciler. reconcile(contract, receipt) → OutcomeDelta.
    ExecutionReceipt  : Observed executor output (observed_outcomes, observed_harms,
    execution_duration_ms, error_message, raw_output).
    OutcomeDelta  : Immutable reconciliation result (all metrics above).
    OutcomeStatus  : SUCCESS / PARTIAL_SUCCESS / FAILURE / HARMFUL / UNEXPECTED.
    CriterionMatch  : Per-criterion match record (criterion, matched, evidence).
    HarmMatch  : Per-harm match record (harm, materialized, evidence).
    append_to_samskara(): Helper — writes OutcomeDelta → SamskaraEntry in one call.

    COMPLIANCE ROLE
    ---------------
    - Fulfills NIST AI RMF MEASURE 2.5 "recurring evaluation" via Brier
    calibration tracking of every action.
    - Provides structured evidence for EU AI Act Art. 14 human-oversight records.

    Governance origin: DharmaOS §2.6 — the outcome reconciler is the karma-feedback
    mechanism: actions leave traces (saṃskāras) that inform future decisions.

    Outcome Reconciler — predicted vs observed + saṃskāra update.

    Source: DharmaOS §2.6.
    Prerequisite to V-JÑĀNA (self-model consumes OutcomeDelta).

    After every executed action, the reconciler compares:
    - PREDICTED: ActionContract.success_criteria + potential_harms
    - OBSERVED: ExecutionReceipt.observed_outcomes + observed_harms

    It emits an OutcomeDelta that quantifies:
    - Criterion-level hit rate (success_criteria met)
    - Harm materialization rate (potential_harms that occurred)
    - Unanticipated effects (observed outcomes not declared)
    - Overall predictive calibration (Brier-style score)

    The delta feeds:
    - V8 SamskaraLedger with calibrated karma-delta
    - V-JÑĀNA capability-vector updates (downstream sprint)
    - V11 policy-amendment proposals (downstream sprint)

    **RF-43 closure:**
    Tests 21–23 in tests/test_outcome_reconciler.py prove:
    21. append_to_samskara creates a SamskaraEntry in the V8 ledger;
    next ledger.query returns it.
    22. OutcomeDelta.karma_delta_suggestion populates the SamskaraEntry
    karma_delta within ±0.001 tolerance.
    23. Reconciling 5 outcomes in sequence produces 5 distinct deltas
    (no state leak between runs).

    Ported lineage:
    /opt/agent-safety/src/vedic/ethics_engine.py (ActionContract, 2026-04-17)
    /opt/agent-safety/src/vedic/samskara_ledger.py (V8 SamskaraLedger, 2026-04-17)

    Sprint: V-RECON (2026-04-17)
    """

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from dharmaos.ethics_engine import ActionContract
    from dharmaos.samskara_ledger import SamskaraEntry, SamskaraLedger

# ---------------------------------------------------------------------------
# Outcome status taxonomy
# ---------------------------------------------------------------------------


class OutcomeStatus(StrEnum):
    """Classification of an executed action's reconciled outcome."""

    SUCCESS = "success"  # all success_criteria met, no harms, no unanticipated
    PARTIAL_SUCCESS = "partial"  # ≥50 % criteria met, no overriding harm
    FAILURE = "failure"  # no criteria met, no harms
    HARMFUL = "harmful"  # harms materialised
    UNEXPECTED = "unexpected"  # large unanticipated-outcome set, some criteria met


# ---------------------------------------------------------------------------
# ExecutionReceipt — observed result emitted by Executor
# ---------------------------------------------------------------------------


class ExecutionReceipt(BaseModel):
    """Observed result of an executed action.

        Emitted by the Executor after action runs. Parallel to ActionContract
        but describes what actually happened, not what was intended.
        """

    model_config = ConfigDict(frozen=True)

    receipt_id: UUID = Field(default_factory=uuid4)
    contract_id: str  # correlates back to ActionContract
    agent_id: str
    tenant_id: str
    executed_at: datetime
    completed_at: datetime
    observed_outcomes: list[str] = Field(default_factory=list)  # what happened
    observed_harms: list[str] = Field(default_factory=list)  # harms materialised
    tool_exit_status: int = 0  # 0 = success; nonzero = error
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Per-item match records
# ---------------------------------------------------------------------------


class CriterionMatch(BaseModel):
    """Result of matching one declared success_criterion against observed_outcomes."""

    criterion: str
    matched: bool
    evidence: str = ""


class HarmMatch(BaseModel):
    """Result of matching one declared potential_harm against observed_harms."""

    harm: str
    materialized: bool
    evidence: str = ""


# ---------------------------------------------------------------------------
# OutcomeDelta — immutable structured comparison result
# ---------------------------------------------------------------------------


class OutcomeDelta(BaseModel):
    """Structured comparison result. Consumed by V8 / V-JÑĀNA / V11.

        Invariants:
        - Frozen (append-only downstream).
        - criterion_hit_rate ∈ [0.0, 1.0].
        - harm_materialization_rate ∈ [0.0, 1.0].
        - brier_score ∈ [0.0, 1.0].
        - karma_delta_suggestion ∈ [-1.0, 1.0].
        """

    model_config = ConfigDict(frozen=True)

    delta_id: UUID = Field(default_factory=uuid4)
    contract_id: str
    receipt_id: UUID
    status: OutcomeStatus

    criterion_hit_rate: float  # 0.0–1.0
    criterion_matches: list[CriterionMatch]

    harm_materialization_rate: float  # 0.0–1.0 (1.0 = every declared harm occurred)
    harm_matches: list[HarmMatch]

    unanticipated_outcomes: list[str]  # observed but not in success_criteria
    unanticipated_harms: list[str]  # observed_harms NOT in potential_harms

    brier_score: float  # 0.0 (perfect) to 1.0 (worst); calibration metric
    karma_delta_suggestion: float  # signed [-1.0, 1.0] for V8 ingest

    reconciled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# OutcomeReconciler — main class
# ---------------------------------------------------------------------------

#: Number of unanticipated outcomes required to flip status from PARTIAL → UNEXPECTED.
_UNANTICIPATED_THRESHOLD: int = 3


class OutcomeReconciler:
    """Compare predicted (ActionContract) vs observed (ExecutionReceipt).

        Matching strategy for criteria/harms:
        - Default: case-insensitive substring match between declared token
        and observed_outcomes/observed_harms entries.
        - Pluggable: inject custom matcher(predicted_str, observed_list) -> (bool, evidence)
        for semantic matching in Phase 4 (embedding similarity).
        """

    def __init__(
        self,
        *,
        criterion_matcher: (Callable[[str, list[str]], tuple[bool, str]] | None) = None,
        harm_matcher: (Callable[[str, list[str]], tuple[bool, str]] | None) = None,
        karma_alpha: float = 0.6,  # weight on criterion hits
        karma_beta: float = 0.4,  # weight on harm penalty
        unanticipated_threshold: int = _UNANTICIPATED_THRESHOLD,
    ) -> None:
        self._criterion_matcher: Callable[[str, list[str]], tuple[bool, str]] = (
            criterion_matcher or _default_matcher
        )
        self._harm_matcher: Callable[[str, list[str]], tuple[bool, str]] = (
            harm_matcher or _default_matcher
        )
        self._alpha = karma_alpha
        self._beta = karma_beta
        self._unanticipated_threshold = unanticipated_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reconcile(
        self,
        contract: ActionContract,
        receipt: ExecutionReceipt,
    ) -> OutcomeDelta:
        """Main entry. Returns OutcomeDelta; does NOT side-effect any
            ledger — caller composes with SamskaraLedger.append() as needed.

            Args:
            contract: The ActionContract that was gated + executed.
            receipt:  The ExecutionReceipt emitted by the Executor.

            Returns:
            Immutable OutcomeDelta with full criterion/harm analysis.
            """
        # ── Criteria matching ────────────────────────────────────────────
        criterion_matches: list[CriterionMatch] = []
        for crit in contract.success_criteria:
            matched, evidence = self._criterion_matcher(crit, receipt.observed_outcomes)
            criterion_matches.append(
                CriterionMatch(criterion=crit, matched=matched, evidence=evidence)
            )

        n_crit = len(criterion_matches)
        n_crit_hit = sum(1 for cm in criterion_matches if cm.matched)
        criterion_hit_rate = n_crit_hit / n_crit if n_crit > 0 else 1.0

        # ── Harm matching ─────────────────────────────────────────────────
        harm_matches: list[HarmMatch] = []
        for harm in contract.potential_harms:
            materialized, evidence = self._harm_matcher(harm, receipt.observed_harms)
            harm_matches.append(HarmMatch(harm=harm, materialized=materialized, evidence=evidence))

        n_harms = len(harm_matches)
        n_harms_materialized = sum(1 for hm in harm_matches if hm.materialized)
        harm_materialization_rate = n_harms_materialized / n_harms if n_harms > 0 else 0.0

        # ── Unanticipated detection ───────────────────────────────────────
        # Unanticipated outcomes: observed_outcomes not covered by ANY success_criterion.
        # Exact-match-exclusive: any observed that did not match any criterion.
        matched_criteria_set: set[str] = {
            cm.evidence  # evidence holds the matching observed string
            for cm in criterion_matches
            if cm.matched and cm.evidence
        }
        unanticipated_outcomes: list[str] = [
            obs for obs in receipt.observed_outcomes if obs not in matched_criteria_set
        ]

        # Unanticipated harms: observed_harms not in potential_harms set.
        declared_harms_set: set[str] = set(contract.potential_harms)
        unanticipated_harms: list[str] = [
            h for h in receipt.observed_harms if not _any_match(h, declared_harms_set)
        ]

        # ── Brier score ───────────────────────────────────────────────────
        # Use model_confidence as predicted probability of full success.
        # actual = 1.0 if criterion_hit_rate == 1.0 AND no harms, else 0.0.
        actual_success = (
            1.0 if (criterion_hit_rate == 1.0 and harm_materialization_rate == 0.0) else 0.0
        )
        brier_score = self.compute_brier(contract.model_confidence, actual_success)

        # ── Karma delta ───────────────────────────────────────────────────
        karma = self.karma_delta_suggestion(
            criterion_hit_rate,
            harm_materialization_rate,
            len(unanticipated_harms),
        )

        # ── Status classification ─────────────────────────────────────────
        status = self.status_from(
            criterion_hit_rate,
            harm_materialization_rate,
            len(unanticipated_harms),
            unanticipated_outcomes_count=len(unanticipated_outcomes),
        )

        return OutcomeDelta(
            contract_id=contract.action_id,
            receipt_id=receipt.receipt_id,
            status=status,
            criterion_hit_rate=criterion_hit_rate,
            criterion_matches=criterion_matches,
            harm_materialization_rate=harm_materialization_rate,
            harm_matches=harm_matches,
            unanticipated_outcomes=unanticipated_outcomes,
            unanticipated_harms=unanticipated_harms,
            brier_score=brier_score,
            karma_delta_suggestion=karma,
        )

    def status_from(
        self,
        criterion_hit_rate: float,
        harm_materialization_rate: float,
        unanticipated_harms_count: int,
        unanticipated_outcomes_count: int = 0,
    ) -> OutcomeStatus:
        """Classify reconciled status from rates and counts.

            Classification rules (in priority order):
            1. harm_materialization_rate >= 0.5 OR unanticipated_harms >= 1 → HARMFUL
            2. criterion_hit_rate == 1.0 AND harm_materialization_rate == 0
            AND unanticipated_outcomes == 0 → SUCCESS
            3. criterion_hit_rate > 0.0 AND unanticipated_outcomes
            >= unanticipated_threshold → UNEXPECTED
            4. criterion_hit_rate > 0.0 → PARTIAL_SUCCESS (any hit qualifies)
            5. criterion_hit_rate == 0 AND no harms → FAILURE
            6. Fallback → FAILURE
            """
        # Rule 1 — harm override (highest priority)
        if harm_materialization_rate >= 0.5 or unanticipated_harms_count >= 1:
            return OutcomeStatus.HARMFUL

        # Rule 2 — clean full success
        if (
            criterion_hit_rate == 1.0
            and harm_materialization_rate == 0.0
            and unanticipated_outcomes_count == 0
        ):
            return OutcomeStatus.SUCCESS

        # Rule 3 — unexpected: some hits but dominated by unanticipated outcomes
        if (
            criterion_hit_rate > 0.0
            and unanticipated_outcomes_count >= self._unanticipated_threshold
        ):
            return OutcomeStatus.UNEXPECTED

        # Rule 4 — partial success: any criterion hit (> 0) qualifies
        if criterion_hit_rate > 0.0:
            return OutcomeStatus.PARTIAL_SUCCESS

        # Rule 5 — failure: zero criteria hit, no harms
        return OutcomeStatus.FAILURE

    def compute_brier(
        self,
        predicted_success_prob: float,
        actual: float,  # 0.0 or 1.0
    ) -> float:
        """Compute Brier score: (predicted - actual)^2.

            Both inputs are clamped to [0.0, 1.0].
            Result is in [0.0, 1.0]: 0.0 = perfect, 1.0 = worst.
            """
        p = max(0.0, min(1.0, predicted_success_prob))
        a = max(0.0, min(1.0, actual))
        return (p - a) ** 2

    def karma_delta_suggestion(
        self,
        criterion_hit_rate: float,
        harm_materialization_rate: float,
        unanticipated_harms_count: int,
    ) -> float:
        """Compute a signed karma-delta suggestion ∈ [-1.0, 1.0].

            Formula:
            karma = alpha × (2 × criterion_hit_rate − 1)
            − beta × harm_materialization_rate
            − 0.3 × min(unanticipated_harms_count, 1.0)
            clamp to [−1.0, 1.0]

            Semantics:
            - criterion_hit_rate = 1.0, no harms → karma ≈ +alpha
            - criterion_hit_rate = 0.0, all harms → karma ≈ −(alpha + beta + 0.3)
            """
        raw = (
            self._alpha * (2.0 * criterion_hit_rate - 1.0)
            - self._beta * harm_materialization_rate
            - 0.3 * min(float(unanticipated_harms_count), 1.0)
        )
        return max(-1.0, min(1.0, raw))


# ---------------------------------------------------------------------------
# Default substring matcher
# ---------------------------------------------------------------------------


def _default_matcher(predicted: str, observed_list: list[str]) -> tuple[bool, str]:
    """Case-insensitive substring match.

        Returns (matched, evidence) where evidence is the first matching observed
        string (empty string if not matched).
        """
    pred_lower = predicted.lower()
    for obs in observed_list:
        if pred_lower in obs.lower():
            return True, obs
    return False, ""


def _any_match(observed_harm: str, declared_harms: set[str]) -> bool:
    """Return True if observed_harm case-insensitively matches any declared harm."""
    obs_lower = observed_harm.lower()
    return any(obs_lower in dh.lower() or dh.lower() in obs_lower for dh in declared_harms)


# ---------------------------------------------------------------------------
# V-RECON → V8 integration helper
# ---------------------------------------------------------------------------


def append_to_samskara(
    delta: OutcomeDelta,
    contract: ActionContract,
    receipt: ExecutionReceipt,
    ledger: SamskaraLedger,
) -> SamskaraEntry:
    """Convenience helper: transform OutcomeDelta + sources into a
        SamskaraEntry and append to the ledger.

        This is the V-RECON → V8 integration point. The generated entry:
        - action_signature = contract.capability_requested (or kind:target fallback)
        - action_trace = selected contract fields
        - consequence_trace = selected receipt fields + delta summary
        - karma_delta = delta.karma_delta_suggestion
        - org_id = UUID(contract.tenant_id) if parseable, else None

        Args:
        delta:  Immutable OutcomeDelta from reconcile().
        contract: The ActionContract that was executed.
        receipt:  The ExecutionReceipt from the Executor.
        ledger:  V8 SamskaraLedger to append into.

        Returns:
        The appended SamskaraEntry (queryable immediately).
        """
    # Lazy import to avoid circular dependency at module load time.
    from dharmaos.samskara_ledger import SamskaraEntry

    # action_signature: prefer capability_requested, fall back to kind:target
    sig: str = (
        contract.capability_requested
        if contract.capability_requested
        else f"{contract.kind}:{contract.target}"
    )

    # org_id: parse tenant_id as UUID; None on failure (non-UUID tenants allowed)
    org_id: UUID | None = None
    try:
        org_id = UUID(contract.tenant_id)
    except (ValueError, AttributeError):
        org_id = None

    action_trace: dict[str, Any] = {
        "action_id": contract.action_id,
        "kind": contract.kind,
        "target": contract.target,
        "declared_goal": contract.declared_goal,
        "capability_requested": contract.capability_requested,
        "agent_id": contract.agent_id,
        "model_confidence": contract.model_confidence,
        "success_criteria": contract.success_criteria,
        "potential_harms": contract.potential_harms,
    }

    consequence_trace: dict[str, Any] = {
        "receipt_id": str(receipt.receipt_id),
        "tool_exit_status": receipt.tool_exit_status,
        "error_message": receipt.error_message,
        "observed_outcomes": receipt.observed_outcomes,
        "observed_harms": receipt.observed_harms,
        "delta_id": str(delta.delta_id),
        "status": delta.status.value,
        "criterion_hit_rate": delta.criterion_hit_rate,
        "harm_materialization_rate": delta.harm_materialization_rate,
        "brier_score": delta.brier_score,
        "unanticipated_outcomes": delta.unanticipated_outcomes,
        "unanticipated_harms": delta.unanticipated_harms,
    }

    entry = SamskaraEntry(
        action_signature=sig,
        action_trace=action_trace,
        consequence_trace=consequence_trace,
        karma_delta=delta.karma_delta_suggestion,
        valid_from=receipt.completed_at,
        org_id=org_id,
    )
    # P2-9: Idempotency guard — prevent double-reconciliation corrupting karma
    if ledger.has_entry(entry.id):
        import structlog as _log

        _log.get_logger().warning(
            "reconcile.duplicate_skipped",
            action_id=contract.action_id,
            entry_id=str(entry.id),
        )
        return entry
    ledger.append(entry)
    return entry
