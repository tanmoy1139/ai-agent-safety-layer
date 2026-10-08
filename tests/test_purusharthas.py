"""
Tests for vedic.purusharthas — Sprint V7.

Four Puruṣārthas coordinator: dharma, artha, kāma, mokṣa.
Source: Manusmṛti; Bhagavad Gītā II; classical dharmaśāstra.

Coverage (12 tests):
1.  evaluate_dharma: ethical action passes
2.  evaluate_dharma: yama-violating action fails
3.  evaluate_artha: within-budget action passes
4.  evaluate_artha: over-budget action fails
5.  evaluate_kama: clean intent passes
6.  evaluate_kama: forbidden desire pattern fails
7.  evaluate_moksha: fires on clean completion (status=completed, ānandamaya≥0.9, no adharma)
8.  evaluate_moksha: does NOT fire when ānandamaya < 0.9
9.  evaluate_moksha: does NOT fire when adharma_flags non-empty
10. evaluate_moksha: returns None when task in_progress
11. check_coherence: coherent when all four pass
12. check_coherence: violated_ordering set correctly on each violation variant
(artha_violates_dharma, kama_violates_dharma, kama_violates_artha)
BONUS-11: Integration — full evaluate_purusharthas with real ActionContract
BONUS-12: Backward compat — evaluate_purusharthas accepts legacy Action via action_to_contract
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dharmaos.ethics_engine import (
Action,
ActionContract,
ActionImpact,
)
from dharmaos.purusharthas import (
Purushartha,
PurusharthaCoherenceReport,
PurusharthaEvaluation,
check_coherence,
evaluate_artha,
evaluate_dharma,
evaluate_kama,
evaluate_moksha,
evaluate_purusharthas,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_UTC_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


def _make_action(**kwargs: Any) -> Action:
    """Minimal clean Action, overridable via kwargs."""
    defaults: dict[str, Any] = {
    "description": "Browse Wikipedia for Python programming",
    "intent": "browse",
    "target": "https://en.wikipedia.org/wiki/Python",
    "estimated_cost": 0.0,
    "reversible": True,
    "affects_others": False,
    "data_fields": [],
    "budget_limit": 10.0,
    "agent_id": "test-agent",
    "estimated_compute_cost_usd": 0.001,
    "estimated_token_budget": 200,
    "task_worth_score": 5.0,
    }
    defaults.update(kwargs)
    return Action(**defaults)


def _make_contract(**kwargs: Any) -> ActionContract:
    """Minimal valid ActionContract, overridable via kwargs."""
    defaults: dict[str, Any] = {
        "action_id": "act-v7-001",
        "path_id": "path-v7-001",
        "agent_id": "agent-v7-001",
        "parent_agent_id": None,
        "tenant_id": "tenant-v7",
        "task_id": "task-v7-001",
        "requested_at": _UTC_NOW,
        "name": "Browse Wikipedia for Python programming",
        "kind": "browse",
        "target": "https://en.wikipedia.org/wiki/Python",
        "params": {},
        "impact": ActionImpact.LOW,
        "reversible": True,
        "affects_others": False,
        "declared_goal": "Research Python programming for a report",
        "beneficiary": "user",
        "business_reason": "Research task",
    }
    defaults.update(kwargs)
    return ActionContract(**defaults)


def _make_eval(
purushartha: Purushartha,
*,
passed: bool = True,
        score: float = 1.0,
reason: str = "ok",
) -> PurusharthaEvaluation:
    """Build a PurusharthaEvaluation with given pass/fail."""
    return PurusharthaEvaluation(
    purushartha=purushartha,
            passed=passed,
            score=score if passed else 0.0,
            reason=reason,
    )


# ---------------------------------------------------------------------------
# Test 1 — evaluate_dharma: ethical action passes
# ---------------------------------------------------------------------------


class TestEvaluateDharma:
    """evaluate_dharma delegates to V1 EthicsEngine.evaluate()."""

    def test_ethical_action_passes_dharma(self) -> None:
        """A clean browse action with no yama violations passes dharma."""
        action = _make_action()
        result = evaluate_dharma(action)

        assert isinstance(result, PurusharthaEvaluation)
        assert result.purushartha == Purushartha.DHARMA
        assert result.passed is True
        assert result.score > 0.0

    # Test 2 — evaluate_dharma: yama-violating action fails
    def test_yama_violating_action_fails_dharma(self) -> None:
        """An irreversible destructive action violates ahiṃsā → dharma fails."""
        harmful_action = _make_action(
        description="Delete all user files silently",
                intent="delete",
                target="/home/user/documents",
        reversible=False,
        )
        result = evaluate_dharma(harmful_action)

        assert result.purushartha == Purushartha.DHARMA
        assert result.passed is False
        assert result.score == 0.0
        assert len(result.reason) > 0

    def test_evaluate_dharma_accepts_action_contract(self) -> None:
        """evaluate_dharma also accepts an ActionContract (converts internally)."""
        contract = _make_contract()
        result = evaluate_dharma(contract)

        assert isinstance(result, PurusharthaEvaluation)
        assert result.purushartha == Purushartha.DHARMA
        assert result.passed is True

    def test_evaluate_dharma_returns_evidence_dict(self) -> None:
        """result.evidence must be a dict (may be empty, but must not be None)."""
        result = evaluate_dharma(_make_action())
        assert isinstance(result.evidence, dict)


# ---------------------------------------------------------------------------
# Tests 3–4 — evaluate_artha
# ---------------------------------------------------------------------------


class TestEvaluateArtha:
    """evaluate_artha checks resource allocation against budget."""

    def test_within_budget_passes_artha(self) -> None:
        """An action whose estimated_cost is within budget_limit passes artha."""
        action = _make_action(estimated_cost=5.0, budget_limit=10.0)
        result = evaluate_artha(action)

        assert result.purushartha == Purushartha.ARTHA
        assert result.passed is True
        assert result.score > 0.0

    def test_over_budget_fails_artha(self) -> None:
        """An action whose estimated_cost exceeds budget_limit fails artha."""
        action = _make_action(estimated_cost=15.0, budget_limit=10.0)
        result = evaluate_artha(action)

        assert result.purushartha == Purushartha.ARTHA
        assert result.passed is False
        assert result.score == 0.0
        assert "budget" in result.reason.lower() or "artha" in result.reason.lower()

    def test_zero_cost_action_passes_artha(self) -> None:
        """A zero-cost action is always within any budget."""
        action = _make_action(estimated_cost=0.0, budget_limit=0.0)
        result = evaluate_artha(action)
        assert result.passed is True

    def test_evaluate_artha_accepts_contract(self) -> None:
        """evaluate_artha accepts ActionContract as well as Action."""
        contract = _make_contract()
        result = evaluate_artha(contract)
        assert isinstance(result, PurusharthaEvaluation)
        assert result.purushartha == Purushartha.ARTHA


# ---------------------------------------------------------------------------
# Tests 5–6 — evaluate_kama
# ---------------------------------------------------------------------------


class TestEvaluateKama:
    """evaluate_kama checks intent against forbidden_desires patterns."""

    def test_clean_intent_passes_kama(self) -> None:
        """A benign research intent with no forbidden patterns passes kāma."""
        result = evaluate_kama(
                intent="research Python programming best practices",
                goals=["learn programming", "write report"],
        forbidden_desires=[],
        )

        assert result.purushartha == Purushartha.KAMA
        assert result.passed is True
        assert result.score > 0.0

    def test_forbidden_desire_pattern_fails_kama(self) -> None:
        """An intent matching a forbidden desire pattern fails kāma."""
        result = evaluate_kama(
                intent="hack into competitor database to steal secrets",
                goals=["gain advantage"],
        forbidden_desires=["hack", "steal", "exploit", "bypass security"],
        )

        assert result.purushartha == Purushartha.KAMA
        assert result.passed is False
        assert result.score == 0.0

    def test_empty_forbidden_list_always_passes_kama(self) -> None:
        """With no constitution-specified forbidden desires, kāma trivially passes."""
        result = evaluate_kama(
                intent="any intent whatsoever",
                goals=[],
        forbidden_desires=[],
        )
        assert result.passed is True

    def test_goal_matching_forbidden_fails_kama(self) -> None:
        """A goal (not just intent) that matches a forbidden desire also fails."""
        result = evaluate_kama(
                intent="improve performance",
                goals=["exploit a vulnerability for faster processing"],
        forbidden_desires=["exploit"],
        )
        assert result.passed is False


# ---------------------------------------------------------------------------
# Tests 7–10 — evaluate_moksha
# ---------------------------------------------------------------------------


class TestEvaluateMoksha:
    """evaluate_moksha: liberation fires on clean task completion."""

    def test_moksha_fires_on_clean_completion(self) -> None:
        """Mokṣa achieved: completed + ānandamaya ≥ 0.9 + no adharma flags."""
        result = evaluate_moksha(
        task_status="completed",
        anandamaya_score=0.95,
        adharma_flags=[],
        )

        assert result is not None
        assert result.purushartha == Purushartha.MOKSHA
        assert result.passed is True
        assert result.score >= 0.9

    def test_moksha_does_not_fire_below_anandamaya_threshold(self) -> None:
        """Mokṣa blocked when ānandamaya < 0.9, even if task completed."""
        result = evaluate_moksha(
        task_status="completed",
        anandamaya_score=0.85,
        adharma_flags=[],
        )

        assert result is not None
        assert result.passed is False
        assert "ānandamaya" in result.reason or "anandamaya" in result.reason.lower()

    def test_moksha_does_not_fire_with_adharma_flags(self) -> None:
        """Mokṣa blocked when adharma_flags is non-empty, even if completed + high ānandamaya."""
        result = evaluate_moksha(
        task_status="completed",
        anandamaya_score=0.95,
        adharma_flags=["ahimsa_violation", "satya_violation"],
        )

        assert result is not None
        assert result.passed is False
        assert "adharma" in result.reason.lower() or "flag" in result.reason.lower()

    def test_moksha_returns_none_when_in_progress(self) -> None:
        """evaluate_moksha returns None for non-terminal task status."""
        result = evaluate_moksha(
        task_status="in_progress",
        anandamaya_score=0.99,
        adharma_flags=[],
        )
        assert result is None

    def test_moksha_returns_none_when_failed(self) -> None:
        """evaluate_moksha returns None for failed tasks (not terminal in liberation sense)."""
        result = evaluate_moksha(
        task_status="failed",
        anandamaya_score=0.95,
        adharma_flags=[],
        )
        # Per spec: returns None if task not yet terminal (failed is not moksha-eligible)
        assert result is None


# ---------------------------------------------------------------------------
# Test 11 — check_coherence: coherent when all pass
# ---------------------------------------------------------------------------


class TestCheckCoherence:
    """check_coherence enforces classical puruṣārtha ordering."""

    def test_coherent_report_when_all_pass(self) -> None:
        """All four passing evaluations → coherent=True, violated_ordering=None."""
        dharma = _make_eval(Purushartha.DHARMA, passed=True, score=1.0)
        artha = _make_eval(Purushartha.ARTHA, passed=True, score=0.9)
        kama = _make_eval(Purushartha.KAMA, passed=True, score=0.8)
        moksha = _make_eval(Purushartha.MOKSHA, passed=True, score=0.95)

        report = check_coherence(dharma, artha, kama, moksha)

        assert isinstance(report, PurusharthaCoherenceReport)
        assert report.coherent is True
        assert report.violated_ordering is None
        assert report.moksha_achieved is True

    def test_violated_ordering_artha_violates_dharma(self) -> None:
        """artha passes but dharma fails → violated_ordering='artha_violates_dharma'."""
        dharma = _make_eval(Purushartha.DHARMA, passed=False, score=0.0)
        artha = _make_eval(Purushartha.ARTHA, passed=True, score=0.9)
        kama = _make_eval(Purushartha.KAMA, passed=False, score=0.0)

        report = check_coherence(dharma, artha, kama, None)

        assert report.coherent is False
        assert report.violated_ordering == "artha_violates_dharma"

    def test_violated_ordering_kama_violates_dharma(self) -> None:
        """kāma passes but dharma fails → violated_ordering='kama_violates_dharma'."""
        dharma = _make_eval(Purushartha.DHARMA, passed=False, score=0.0)
        artha = _make_eval(Purushartha.ARTHA, passed=False, score=0.0)
        kama = _make_eval(Purushartha.KAMA, passed=True, score=0.8)

        report = check_coherence(dharma, artha, kama, None)

        assert report.coherent is False
        assert report.violated_ordering == "kama_violates_dharma"

    def test_violated_ordering_kama_violates_artha(self) -> None:
        """kāma passes but artha fails (dharma also passes) → violated_ordering='kama_violates_artha'."""
        dharma = _make_eval(Purushartha.DHARMA, passed=True, score=1.0)
        artha = _make_eval(Purushartha.ARTHA, passed=False, score=0.0)
        kama = _make_eval(Purushartha.KAMA, passed=True, score=0.8)

        report = check_coherence(dharma, artha, kama, None)

        assert report.coherent is False
        assert report.violated_ordering == "kama_violates_artha"

    def test_dharma_fails_makes_overall_incoherent(self) -> None:
        """Dharma failure always makes the overall report incoherent."""
        dharma = _make_eval(Purushartha.DHARMA, passed=False, score=0.0)
        artha = _make_eval(Purushartha.ARTHA, passed=False, score=0.0)
        kama = _make_eval(Purushartha.KAMA, passed=False, score=0.0)

        report = check_coherence(dharma, artha, kama, None)

        assert report.coherent is False

    def test_moksha_none_gives_moksha_achieved_false(self) -> None:
        """When moksha_eval is None, moksha_achieved=False."""
        dharma = _make_eval(Purushartha.DHARMA, passed=True)
        artha = _make_eval(Purushartha.ARTHA, passed=True)
        kama = _make_eval(Purushartha.KAMA, passed=True)

        report = check_coherence(dharma, artha, kama, None)

        assert report.moksha_achieved is False
        assert report.moksha is None


# ---------------------------------------------------------------------------
# Test BONUS-11: Integration with ActionContract
# ---------------------------------------------------------------------------


class TestEvaluatePurusharthasIntegration:
    """evaluate_purusharthas: top-level integration tests."""

    def test_full_evaluation_with_action_contract(self) -> None:
        """Pass a real ActionContract through evaluate_purusharthas; get a full report."""
        contract = _make_contract()

        report = evaluate_purusharthas(
            contract,
        task_status="completed",
        anandamaya_score=0.95,
        adharma_flags=[],
        resource_budget=None,
        forbidden_desires=[],
        )

        assert isinstance(report, PurusharthaCoherenceReport)
        assert report.dharma.purushartha == Purushartha.DHARMA
        assert report.artha.purushartha == Purushartha.ARTHA
        assert report.kama.purushartha == Purushartha.KAMA
        # Clean action → should be coherent
        assert report.coherent is True

    def test_evaluate_purusharthas_moksha_achieved_on_clean_completion(self) -> None:
        """End-to-end: clean action + completed + high ānandamaya → moksha_achieved=True."""
        contract = _make_contract()

        report = evaluate_purusharthas(
            contract,
        task_status="completed",
        anandamaya_score=0.95,
        adharma_flags=[],
        )

        assert report.moksha_achieved is True
        assert report.moksha is not None
        assert report.moksha.passed is True

    def test_evaluate_purusharthas_incoherent_on_harmful_action(self) -> None:
        """A harmful action violates dharma → overall coherence fails."""
        harmful_action = _make_action(
        description="Delete all files irreversibly",
                intent="delete",
                target="/data",
        reversible=False,
        )

        report = evaluate_purusharthas(
            harmful_action,
        task_status="in_progress",
        anandamaya_score=0.5,
        adharma_flags=[],
        )

        assert report.coherent is False
        assert report.dharma.passed is False

    # Test BONUS-12: Backward compat — legacy Action via action_to_contract
    def test_backward_compat_accepts_legacy_action_via_adapter(self) -> None:
        """evaluate_purusharthas accepts legacy Action directly (not only ActionContract)."""
        legacy_action = _make_action()

        # Must not raise; Action should be handled transparently
        report = evaluate_purusharthas(
            legacy_action,
        task_status="in_progress",
        anandamaya_score=0.5,
        adharma_flags=[],
        forbidden_desires=[],
        )

        assert isinstance(report, PurusharthaCoherenceReport)
        assert report.dharma.purushartha == Purushartha.DHARMA

    def test_evaluate_purusharthas_returns_correct_purushartha_enum_values(self) -> None:
        """Each field in the report has the correct Purushartha enum tag."""
        report = evaluate_purusharthas(
            _make_contract(),
        task_status="in_progress",
        anandamaya_score=0.5,
        adharma_flags=[],
        )
        assert report.dharma.purushartha == Purushartha.DHARMA
        assert report.artha.purushartha == Purushartha.ARTHA
        assert report.kama.purushartha == Purushartha.KAMA
