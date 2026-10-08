"""
    Tests for V-RECON Outcome Reconciler — Sprint V-RECON.

    Source: DharmaOS §2.6.


    Test layout (23 tests total):
    Core reconcile (8):  test_01 – test_08
    Matching (4):  test_09 – test_12
    Calibration (4):  test_13 – test_16
    Status classification (4): test_17 – test_20
    Integration — RF-43 (3):  test_21 – test_23
    """

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from dharmaos.ethics_engine import ActionContract
from dharmaos.outcome_reconciler import (
    ExecutionReceipt,
    OutcomeReconciler,
    OutcomeStatus,
    append_to_samskara,
)
from dharmaos.samskara_ledger import SamskaraLedger

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _contract(
    *,
    success_criteria: list[str] | None = None,
    potential_harms: list[str] | None = None,
    model_confidence: float = 0.8,
    tenant_id: str = str(uuid4()),
    capability_requested: str | None = "test_capability",
) -> ActionContract:
    """Build an ActionContract with sensible test defaults."""
    return ActionContract(
        action_id=str(uuid4()),
        path_id="test-path",
        agent_id="test-agent",
        parent_agent_id=None,
        tenant_id=tenant_id,
        task_id="task-001",
        requested_at=_NOW,
        name="test_action",
        kind="browse",
        target="https://example.com",
        success_criteria=success_criteria or [],
        potential_harms=potential_harms or [],
        model_confidence=model_confidence,
        capability_requested=capability_requested,
    )


def _receipt(
    contract: ActionContract,
    *,
    observed_outcomes: list[str] | None = None,
    observed_harms: list[str] | None = None,
    tool_exit_status: int = 0,
) -> ExecutionReceipt:
    """Build an ExecutionReceipt correlated to the given contract."""
    return ExecutionReceipt(
        contract_id=contract.action_id,
        agent_id=contract.agent_id,
        tenant_id=contract.tenant_id,
        executed_at=_NOW,
        completed_at=_NOW,
        observed_outcomes=observed_outcomes or [],
        observed_harms=observed_harms or [],
        tool_exit_status=tool_exit_status,
    )


# ---------------------------------------------------------------------------
# Core reconcile (8 tests)
# ---------------------------------------------------------------------------


class TestCoreReconcile:
    """test_01 – test_08."""

    def test_01_all_criteria_hit_no_harms_is_success(self) -> None:
        """Contract with 3 criteria, receipt hits all 3, no harms → SUCCESS + hit_rate=1.0 + karma>0."""
        contract = _contract(
            success_criteria=["payment processed", "receipt sent", "ledger updated"]
        )
        receipt = _receipt(
            contract,
            observed_outcomes=[
            "Payment processed successfully",
            "Receipt sent to user",
            "Ledger updated with new entry",
        ],
        )
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        assert delta.status == OutcomeStatus.SUCCESS
        assert delta.criterion_hit_rate == 1.0
        assert delta.karma_delta_suggestion > 0.0
        assert delta.harm_materialization_rate == 0.0

    def test_02_no_criteria_hit_failure(self) -> None:
        """Contract with 3 criteria, receipt hits 0 → FAILURE + hit_rate=0 + karma<0."""
        contract = _contract(
            success_criteria=["payment processed", "receipt sent", "ledger updated"]
        )
        receipt = _receipt(
            contract,
            observed_outcomes=["Connection timeout"],
        )
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        assert delta.status == OutcomeStatus.FAILURE
        assert delta.criterion_hit_rate == 0.0
        assert delta.karma_delta_suggestion < 0.0

    def test_03_one_of_three_criteria_partial_success(self) -> None:
        """Contract with 3 criteria, receipt hits 1 → PARTIAL_SUCCESS."""
        contract = _contract(
            success_criteria=["payment processed", "receipt sent", "ledger updated"]
        )
        receipt = _receipt(
            contract,
            observed_outcomes=["Payment processed successfully"],
        )
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        assert delta.status == OutcomeStatus.PARTIAL_SUCCESS
        assert pytest.approx(delta.criterion_hit_rate, abs=1e-9) == 1 / 3

    def test_04_both_declared_harms_materialise_harmful(self) -> None:
        """Contract declares 2 harms, receipt shows both → HARMFUL + harm_rate=1.0."""
        contract = _contract(
            success_criteria=["task done"],
            potential_harms=["data leak", "credential exposure"],
        )
        receipt = _receipt(
            contract,
            observed_outcomes=["task done"],
            observed_harms=["data leak detected", "credential exposure event"],
        )
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        assert delta.status == OutcomeStatus.HARMFUL
        assert delta.harm_materialization_rate == 1.0

    def test_05_one_of_two_harms_materialises_harmful(self) -> None:
        """Contract declares 2 harms, receipt shows 1 → HARMFUL (rate=0.5 >= threshold)."""
        contract = _contract(
            success_criteria=["task done"],
            potential_harms=["data leak", "credential exposure"],
        )
        receipt = _receipt(
            contract,
            observed_outcomes=["task done"],
            observed_harms=["data leak detected"],
        )
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        assert delta.status == OutcomeStatus.HARMFUL
        assert pytest.approx(delta.harm_materialization_rate, abs=1e-9) == 0.5

    def test_06_unanticipated_outcomes_tracked(self) -> None:
        """Unanticipated outcomes (observed not in success_criteria) tracked correctly."""
        contract = _contract(success_criteria=["alpha completed"])
        receipt = _receipt(
            contract,
            observed_outcomes=["alpha completed", "beta side effect", "gamma anomaly"],
        )
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        # "beta side effect" and "gamma anomaly" are unanticipated
        assert "beta side effect" in delta.unanticipated_outcomes
        assert "gamma anomaly" in delta.unanticipated_outcomes
        # "alpha completed" matched "alpha completed" criterion
        assert "alpha completed" not in delta.unanticipated_outcomes

    def test_07_unanticipated_harms_push_to_harmful(self) -> None:
        """Observed harms not in potential_harms → unanticipated_harms → HARMFUL."""
        contract = _contract(
            success_criteria=["task done"],
            potential_harms=["data leak"],  # declared
        )
        receipt = _receipt(
            contract,
            observed_outcomes=["task done"],
            observed_harms=["unexpected privilege escalation"],  # NOT declared
        )
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        assert delta.status == OutcomeStatus.HARMFUL
        assert "unexpected privilege escalation" in delta.unanticipated_harms

    def test_08_no_criteria_no_harms_clean_receipt_success(self) -> None:
        """No criteria + no harms + clean receipt → SUCCESS (vacuous success)."""
        contract = _contract(success_criteria=[], potential_harms=[])
        receipt = _receipt(contract, observed_outcomes=[], observed_harms=[])
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        # criterion_hit_rate=1.0 (vacuous), harm_rate=0.0, unanticipated=0 → SUCCESS
        assert delta.status == OutcomeStatus.SUCCESS
        assert delta.criterion_hit_rate == 1.0
        assert delta.harm_materialization_rate == 0.0


# ---------------------------------------------------------------------------
# Matching (4 tests)
# ---------------------------------------------------------------------------


class TestMatching:
    """test_09 – test_12."""

    def test_09_default_matcher_case_insensitive_substring(self) -> None:
        """Default matcher: case-insensitive substring — 'payment' in 'Payment processed'."""
        contract = _contract(success_criteria=["payment"])
        receipt = _receipt(contract, observed_outcomes=["Payment processed successfully"])
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        assert delta.criterion_hit_rate == 1.0
        assert delta.criterion_matches[0].matched is True
        assert delta.criterion_matches[0].evidence == "Payment processed successfully"

    def test_10_custom_criterion_matcher_injected(self) -> None:
        """Custom matcher injected → used in place of default."""

        def exact_matcher(predicted: str, observed: list[str]) -> tuple[bool, str]:
            """Only match exact-equal strings."""
            for obs in observed:
                if predicted == obs:
                    return True, obs
                return False, ""

        contract = _contract(success_criteria=["payment"])
        receipt = _receipt(
            contract,
            observed_outcomes=["Payment processed"],  # different case + words
        )
        reconciler = OutcomeReconciler(criterion_matcher=exact_matcher)
        delta = reconciler.reconcile(contract, receipt)

        # exact matcher does NOT match "payment" vs "Payment processed"
        assert delta.criterion_hit_rate == 0.0
        assert delta.criterion_matches[0].matched is False

    def test_11_empty_observed_all_criteria_unmatched(self) -> None:
        """Empty observed_outcomes vs declared criteria → all matched=False."""
        contract = _contract(success_criteria=["step A", "step B", "step C"])
        receipt = _receipt(contract, observed_outcomes=[])
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        assert delta.criterion_hit_rate == 0.0
        assert all(not cm.matched for cm in delta.criterion_matches)

    def test_12_harm_matcher_independent_of_criterion_matcher(self) -> None:
        """Harms matcher is independent of criteria matcher."""

        def never_match(predicted: str, observed: list[str]) -> tuple[bool, str]:
            return False, ""

        contract = _contract(
            success_criteria=["task done"],
            potential_harms=["data leak"],
        )
        receipt = _receipt(
            contract,
            observed_outcomes=["task done"],
            observed_harms=["data leak detected"],
        )
        # Override CRITERION matcher to never-match; harm matcher stays default
        reconciler = OutcomeReconciler(criterion_matcher=never_match)
        delta = reconciler.reconcile(contract, receipt)

        # criterion: never matched
        assert delta.criterion_hit_rate == 0.0
        # harm: default matcher still works
        assert delta.harm_materialization_rate == 1.0
        assert delta.harm_matches[0].materialized is True


# ---------------------------------------------------------------------------
# Calibration (4 tests)
# ---------------------------------------------------------------------------


class TestCalibration:
    """test_13 – test_16."""

    def test_13_brier_formula_correct(self) -> None:
        """compute_brier(0.9, 1.0) ≈ 0.01; compute_brier(0.1, 1.0) ≈ 0.81."""
        r = OutcomeReconciler()
        assert pytest.approx(r.compute_brier(0.9, 1.0), abs=1e-9) == (0.9 - 1.0) ** 2
        assert pytest.approx(r.compute_brier(0.9, 1.0), abs=1e-9) == 0.01
        assert pytest.approx(r.compute_brier(0.1, 1.0), abs=1e-9) == (0.1 - 1.0) ** 2
        assert pytest.approx(r.compute_brier(0.1, 1.0), abs=1e-9) == 0.81

    def test_14_brier_clamped_when_inputs_out_of_range(self) -> None:
        """Brier clamped to [0, 1] when inputs are out of range."""
        r = OutcomeReconciler()
        # prob > 1.0 → clamped to 1.0 → (1.0 - 1.0)^2 = 0.0
        assert r.compute_brier(1.5, 1.0) == pytest.approx(0.0, abs=1e-9)
        # prob < 0.0 → clamped to 0.0 → (0.0 - 1.0)^2 = 1.0
        assert r.compute_brier(-0.5, 1.0) == pytest.approx(1.0, abs=1e-9)
        # actual > 1.0 → clamped → (0.5 - 1.0)^2 = 0.25
        assert r.compute_brier(0.5, 2.0) == pytest.approx(0.25, abs=1e-9)

    def test_15_karma_delta_all_hit_no_harms_positive(self) -> None:
        """karma_delta_suggestion all-hit no-harms → positive near alpha."""
        r = OutcomeReconciler(karma_alpha=0.6, karma_beta=0.4)
        karma = r.karma_delta_suggestion(1.0, 0.0, 0)
        # alpha*(2*1-1) - beta*0 - 0.3*0 = 0.6
        assert pytest.approx(karma, abs=1e-9) == 0.6
        assert karma > 0.0

    def test_16_karma_delta_no_hit_all_harms_clamped_floor(self) -> None:
        """karma_delta_suggestion no-hit all-harms → clamped to floor ≤ −1.0."""
        r = OutcomeReconciler(karma_alpha=0.6, karma_beta=0.4)
        karma = r.karma_delta_suggestion(0.0, 1.0, 1)
        # 0.6*(2*0-1) - 0.4*1.0 - 0.3*1 = -0.6-0.4-0.3 = -1.3 → clamped to -1.0
        assert karma == pytest.approx(-1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Status classification (4 tests)
# ---------------------------------------------------------------------------


class TestStatusClassification:
    """test_17 – test_20."""

    def test_17_harm_rate_half_overrides_hit_rate(self) -> None:
        """harm_materialization_rate >= 0.5 → HARMFUL overrides hit_rate."""
        r = OutcomeReconciler()
        # Even with hit_rate=1.0, harm_rate=0.5 → HARMFUL
        status = r.status_from(1.0, 0.5, 0)
        assert status == OutcomeStatus.HARMFUL

    def test_18_unanticipated_harms_gte_1_forces_harmful(self) -> None:
        """unanticipated_harms >= 1 → HARMFUL regardless of hit_rate."""
        r = OutcomeReconciler()
        status = r.status_from(1.0, 0.0, 1)
        assert status == OutcomeStatus.HARMFUL

    def test_19_perfect_run_is_success(self) -> None:
        """hit_rate=1.0 + no harms + no unanticipated outcomes → SUCCESS."""
        r = OutcomeReconciler()
        status = r.status_from(1.0, 0.0, 0, unanticipated_outcomes_count=0)
        assert status == OutcomeStatus.SUCCESS

    def test_20_some_hits_large_unanticipated_is_unexpected(self) -> None:
        """hit_rate > 0 + many unanticipated outcomes → UNEXPECTED."""
        # unanticipated_threshold default = 3
        r = OutcomeReconciler(unanticipated_threshold=3)
        # hit_rate=0.5 (>0), no harms, unanticipated_outcomes=4 (≥3) → UNEXPECTED
        status = r.status_from(0.5, 0.0, 0, unanticipated_outcomes_count=4)
        assert status == OutcomeStatus.UNEXPECTED


# ---------------------------------------------------------------------------
# Integration — closes RF-43 (3 tests)
# ---------------------------------------------------------------------------


class TestIntegrationRF43:
    """test_21 – test_23.

        These three tests together constitute the RF-43 closure evidence.
        """

    def test_21_append_to_samskara_entry_queryable(self) -> None:
        """append_to_samskara creates a SamskaraEntry in V8 ledger; ledger.query returns it."""
        tenant_id = str(uuid4())
        contract = _contract(
            success_criteria=["task completed"],
            tenant_id=tenant_id,
            capability_requested="browse:example.com",
        )
        receipt = _receipt(contract, observed_outcomes=["task completed"])

        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        ledger = SamskaraLedger()
        entry = append_to_samskara(delta, contract, receipt, ledger)

        # Entry exists in ledger
        assert ledger.get_entry(entry.id) is not None

        # Query by action_signature returns it
        results = ledger.query(entry.action_signature, top_k=5)
        ids = [r[0].id for r in results]
        assert entry.id in ids

    def test_22_karma_delta_propagated_to_samskara_entry(self) -> None:
        """OutcomeDelta.karma_delta_suggestion populates SamskaraEntry.karma_delta
            within ±0.001 tolerance."""
        contract = _contract(
            success_criteria=["payment processed", "receipt sent"],
            potential_harms=["data leak"],
        )
        receipt = _receipt(
            contract,
            observed_outcomes=["Payment processed", "Receipt sent to customer"],
        )
        reconciler = OutcomeReconciler()
        delta = reconciler.reconcile(contract, receipt)

        ledger = SamskaraLedger()
        entry = append_to_samskara(delta, contract, receipt, ledger)

        assert pytest.approx(entry.karma_delta, abs=0.001) == delta.karma_delta_suggestion

    def test_23_five_reconciliations_produce_distinct_deltas(self) -> None:
        """Reconciling 5 outcomes in sequence produces 5 distinct deltas (no state leak)."""
        reconciler = OutcomeReconciler()
        delta_ids: set[UUID] = set()

        for i in range(5):
            contract = _contract(
                success_criteria=[f"step_{i}_done"],
                potential_harms=[],
            )
            receipt = _receipt(contract, observed_outcomes=[f"step_{i}_done successfully"])
            delta = reconciler.reconcile(contract, receipt)
            delta_ids.add(delta.delta_id)
            # Each delta should hit the success criterion
            assert delta.criterion_hit_rate == 1.0

        # All 5 delta IDs must be unique (no state leak)
        assert len(delta_ids) == 5
