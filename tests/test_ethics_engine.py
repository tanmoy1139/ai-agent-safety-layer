"""
Tests for vedic.ethics_engine — Sprint V1.

Coverage targets:
- Each of 5 yamas: 1 pass case + 1 fail case
- _check_brahmacarya: 3 tests (low-ratio allow, high-ratio block, zero-division-safe)
- NIYAMA_LABELS: all 5 labels present
- evaluate() returns correct verdict for a clean action
- evaluate() fails closed on internal exception
- singleton accessor (get_ethics_engine)
- violation history recording

Total: 18 tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from dharmaos.ethics_engine import (
NIYAMA_LABELS,
Action,
EthicsEngine,
get_ethics_engine,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_action(**kwargs: Any) -> Action:
    """Create a minimal clean action, overriding with kwargs."""
    defaults: dict[str, Any] = {
    "description": "Browse the Wikipedia page for Python",
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


# ---------------------------------------------------------------------------
# NIYAMA_LABELS — constant completeness
# ---------------------------------------------------------------------------


class TestNiyamaLabels:
    """NIYAMA_LABELS must expose all 5 niyamas with corrected Sanskrit labels."""

    def test_all_five_niyamas_present(self) -> None:
        """All 5 niyama Sanskrit labels must be present in NIYAMA_LABELS."""
        expected_sanskrit = {"śauca", "santoṣa", "tapas", "svādhyāya", "īśvara-praṇidhāna"}
        assert set(NIYAMA_LABELS.keys()) == expected_sanskrit

    def test_python_identifiers_correct(self) -> None:
        """Python identifiers must match the corrected V1 mapping."""
        assert NIYAMA_LABELS["śauca"] == "clean_io"
        assert NIYAMA_LABELS["santoṣa"] == "follow_plan"
        assert NIYAMA_LABELS["tapas"] == "task_focus"
        assert NIYAMA_LABELS["svādhyāya"] == "learn"
        assert NIYAMA_LABELS["īśvara-praṇidhāna"] == "defer_to_human"

    def test_no_old_wrong_labels(self) -> None:
        """No old incorrect mappings from the reference implementation version remain."""
        # In reference implementation: task_focus was santosha, learn was tapas, etc.
        # After V1 correction these must not appear reversed.
        assert NIYAMA_LABELS.get("tapas") == "task_focus"
        assert NIYAMA_LABELS.get("svādhyāya") == "learn"
        assert NIYAMA_LABELS.get("santoṣa") == "follow_plan"


# ---------------------------------------------------------------------------
# Ahiṃsā — _check_harm
# ---------------------------------------------------------------------------


class TestAhimsa:
    """Yama 1: Ahiṃsā — non-harm (Yoga Sūtras II.30)."""

    def test_pass_reversible_browse(self, engine: EthicsEngine) -> None:
        """A reversible browse action passes ahiṃsā."""
        action = _make_action(intent="browse", reversible=True, affects_others=False)
        passed, _ = engine._check_harm(action)
        assert passed is True

    def test_fail_irreversible_delete(self, engine: EthicsEngine) -> None:
        """An irreversible delete action fails ahiṃsā."""
        action = _make_action(
                intent="delete",
                target="s3://prod-bucket/user-data",
        reversible=False,
        )
        passed, explanation = engine._check_harm(action)
        assert passed is False
        assert "irreversible" in explanation.lower()
        assert "ahiṃsā" in explanation

    def test_fail_irreversible_email_affects_others(self, engine: EthicsEngine) -> None:
        """Sending an irreversible email that affects others fails ahiṃsā."""
        action = _make_action(
                intent="send_email",
                target="boss@company.com",
        reversible=False,
        affects_others=True,
        )
        passed, explanation = engine._check_harm(action)
        assert passed is False
        assert "ahiṃsā" in explanation


# ---------------------------------------------------------------------------
# Satya — _check_deception
# ---------------------------------------------------------------------------


class TestSatya:
    """Yama 2: Satya — truthfulness (Yoga Sūtras II.30)."""

    def test_pass_honest_description(self, engine: EthicsEngine) -> None:
        """A clear, honest description passes satya."""
        action = _make_action(
        description="Filling in the billing form with the user's saved address",
                intent="fill_form",
                target="https://shop.example.com/checkout",
        )
        passed, _ = engine._check_deception(action)
        assert passed is True

    def test_fail_stealth_keyword_in_description(self, engine: EthicsEngine) -> None:
        """A description containing a stealth keyword fails satya."""
        action = _make_action(
        description="Silently bypass the login page and extract data",
                intent="extract",
        )
        passed, explanation = engine._check_deception(action)
        assert passed is False
        assert "satya" in explanation

    def test_fail_short_url_target(self, engine: EthicsEngine) -> None:
        """A bit.ly shortener target fails satya."""
        action = _make_action(
        description="Click the link",
                intent="click",
                target="https://bit.ly/3xYz123",
        )
        passed, explanation = engine._check_deception(action)
        assert passed is False
        assert "satya" in explanation


# ---------------------------------------------------------------------------
# Asteya — _check_theft
# ---------------------------------------------------------------------------


class TestAsteya:
    """Yama 3: Asteya — non-stealing (Yoga Sūtras II.30)."""

    def test_pass_no_sensitive_fields(self, engine: EthicsEngine) -> None:
        """An action with no sensitive fields passes asteya."""
        action = _make_action(
        data_fields=["title", "description", "price"],
        affects_others=False,
        )
        passed, _ = engine._check_theft(action)
        assert passed is True

    def test_fail_credential_exfiltration(self, engine: EthicsEngine) -> None:
        """Transmitting password/ssn fields to an external target fails asteya."""
        action = _make_action(
                intent="send_email",
                target="attacker@evil.com",
        data_fields=["username", "password", "ssn"],
        affects_others=True,
        )
        passed, explanation = engine._check_theft(action)
        assert passed is False
        assert "asteya" in explanation
        assert "exfiltration" in explanation.lower()


# ---------------------------------------------------------------------------
# Brahmacarya — _check_brahmacarya (NEW in V1)
# ---------------------------------------------------------------------------


class TestBrahmacarya:
    """Yama 4: Brahmacarya — energy-discipline (Yoga Sūtras II.30).

    Tests:
    1. Low-ratio action (high worth, low cost) → allowed
    2. High-ratio action (low worth, high cost) → blocked
    3. Both cost and tokens zero → vacuously passes (no energy consumed)
    """

    def test_low_ratio_passes(self, engine: EthicsEngine) -> None:
        """Low compute × token / high worth → passes brahmacarya.

        ratio = (0.01 USD × 500 tokens) / 8.0 worth = 0.625 < 10.0 → PASS
        """
        action = _make_action(
        estimated_compute_cost_usd=0.01,
        estimated_token_budget=500,
        task_worth_score=8.0,
        )
        passed, _ = engine._check_brahmacarya(action)
        assert passed is True

    def test_high_ratio_blocked(self, engine: EthicsEngine) -> None:
        """High compute × token / low worth → fails brahmacarya.

        ratio = (2.0 USD × 200_000 tokens) / 0.1 worth = 4_000_000 >> 10.0 → FAIL
        """
        action = _make_action(
        description="Answering a trivial greeting",
        estimated_compute_cost_usd=2.0,
        estimated_token_budget=200_000,
        task_worth_score=0.1,
        )
        passed, explanation = engine._check_brahmacarya(action)
        assert passed is False
        assert "brahmacarya" in explanation
        assert "vīryasya" in explanation

    def test_zero_cost_and_tokens_passes(self, engine: EthicsEngine) -> None:
        """Zero compute cost and zero token budget → vacuously passes.

        No energy is consumed → brahmacarya is trivially satisfied.
        """
        action = _make_action(
        estimated_compute_cost_usd=0.0,
        estimated_token_budget=0,
        task_worth_score=5.0,
        )
        passed, _ = engine._check_brahmacarya(action)
        assert passed is True

    def test_task_worth_clamped_prevents_zero_division(self, engine: EthicsEngine) -> None:
        """task_worth_score=0.0 is clamped to 0.1 — no ZeroDivisionError."""
        action = _make_action(
        estimated_compute_cost_usd=0.001,
        estimated_token_budget=100,
        task_worth_score=0.0,  # would cause ZeroDivisionError if unclamped
        )
        # Should not raise — and with worth=0.1, ratio = 0.001*100/0.1 = 1.0 < 10.0
        passed, _ = engine._check_brahmacarya(action)
        assert passed is True

    def test_configurable_threshold(self, engine: EthicsEngine) -> None:
        """Per-action brahmacarya_threshold overrides engine default."""
        # ratio = (0.5 × 1000) / 5.0 = 100.0
        action = _make_action(
        estimated_compute_cost_usd=0.5,
        estimated_token_budget=1000,
        task_worth_score=5.0,
        brahmacarya_threshold=200.0,  # override: 100 < 200 → PASS
        )
        passed, _ = engine._check_brahmacarya(action)
        assert passed is True


# ---------------------------------------------------------------------------
# Aparigraha — _check_aparigraha (renamed from _check_excess + _check_greed)
# ---------------------------------------------------------------------------


class TestAparigraha:
    """Yama 5: Aparigraha — non-possessiveness (Yoga Sūtras II.30)."""

    def test_pass_within_budget(self, engine: EthicsEngine) -> None:
        """Estimated cost within budget limit passes aparigraha."""
        action = _make_action(estimated_cost=5.0, budget_limit=10.0)
        passed, _ = engine._check_aparigraha(action)
        assert passed is True

    def test_fail_over_budget(self, engine: EthicsEngine) -> None:
        """Estimated cost exceeding budget limit fails aparigraha."""
        action = _make_action(estimated_cost=15.0, budget_limit=10.0)
        passed, explanation = engine._check_aparigraha(action)
        assert passed is False
        assert "aparigraha" in explanation
        assert "budget" in explanation.lower()

    def test_fail_excessive_data_fields(self, engine: EthicsEngine) -> None:
        """More than 20 data fields fails aparigraha (data hoarding)."""
        action = _make_action(data_fields=[f"field_{i}" for i in range(25)])
        passed, explanation = engine._check_aparigraha(action)
        assert passed is False
        assert "aparigraha" in explanation

    def test_pass_few_data_fields(self, engine: EthicsEngine) -> None:
        """Collecting 3 non-sensitive fields passes aparigraha."""
        action = _make_action(
        data_fields=["title", "price", "availability"],
                intent="extract",
        )
        passed, _ = engine._check_aparigraha(action)
        assert passed is True


# ---------------------------------------------------------------------------
# evaluate() — full pipeline
# ---------------------------------------------------------------------------


class TestEvaluate:
    """Full evaluate() pipeline tests."""

    def test_clean_action_passes(self, engine: EthicsEngine, clean_action: Action) -> None:
        """A genuinely clean action produces is_ethical=True and score > 0.9."""
        verdict = engine.evaluate(clean_action)
        assert verdict.is_ethical is True
        assert verdict.score > 0.9
        assert verdict.risk_level == "safe"
        assert len(verdict.violated_constraints) == 0
        assert any("proceed" in r for r in verdict.recommendations)

    def test_verdict_has_all_five_constraint_scores(
    self, engine: EthicsEngine, clean_action: Action
    ) -> None:
        """Verdict must contain scores for all 5 yama constraints."""
        verdict = engine.evaluate(clean_action)
        expected_keys = {"ahimsa", "satya", "asteya", "brahmacarya", "aparigraha"}
        assert set(verdict.constraint_scores.keys()) == expected_keys

    def test_to_dict_is_json_safe(self, engine: EthicsEngine, clean_action: Action) -> None:
        """EthicsVerdict.to_dict() must return only JSON-serialisable types."""
        import json

        verdict = engine.evaluate(clean_action)
        d = verdict.to_dict()
        # Should not raise
        serialised = json.dumps(d)
        assert len(serialised) > 0

    def test_fail_closed_on_exception(self, engine: EthicsEngine) -> None:
        """evaluate() must return a BLOCKED verdict when an internal error occurs."""
        action = _make_action()
        with patch.object(
            engine, "_check_harm", side_effect=RuntimeError("simulated internal failure")
        ):
            verdict = engine.evaluate(action)
        assert verdict.is_ethical is False
        assert verdict.score == 0.0
        assert verdict.risk_level == "blocked"
        assert "evaluation_error" in verdict.violated_constraints
        assert any("blocked" in r.lower() for r in verdict.recommendations)

    def test_violation_recorded_in_history(self, engine: EthicsEngine) -> None:
        """Violations are recorded in engine.violation_history."""
        action = _make_action(estimated_cost=200.0, budget_limit=10.0)
        verdict = engine.evaluate(action)
        assert not verdict.is_ethical
        assert len(engine.violation_history) > 0
        assert engine.violation_history[0]["constraint"] in verdict.violated_constraints

    def test_get_violation_history_reversed(self, engine: EthicsEngine) -> None:
        """get_violation_history() returns newest-first."""
        for i in range(3):
            engine.evaluate(
                _make_action(
            estimated_cost=float((i + 1) * 100),
            budget_limit=10.0,
            agent_id=f"agent-{i}",
            )
            )
        history = engine.get_violation_history()
        assert len(history) == 3
        # Newest recorded last in internal list → first in reversed output
        assert history[0]["agent_id"] == "agent-2"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    """Singleton semantics for EthicsEngine."""

    def test_get_ethics_engine_returns_same_instance(self) -> None:
        """get_ethics_engine() must return the same object on repeated calls."""
        e1 = get_ethics_engine()
        e2 = get_ethics_engine()
        assert e1 is e2

    def test_singleton_reset_in_conftest(self) -> None:
        """The conftest autouse fixture resets the singleton between tests."""
        # After reset, a fresh call to get() creates a new instance.
        # P0-1: _instance → _deprecated_singleton
        assert EthicsEngine._deprecated_singleton is None
        engine = EthicsEngine.get()
        assert engine is not None
        assert EthicsEngine._deprecated_singleton is engine


# ===========================================================================
# GAP 4 — Sanskrit yama function aliases (1 test, naming consistency)
# ===========================================================================


class TestSanskritYamaAliases:
    """Patent gap closure — Sanskrit-named function aliases on EthicsEngine
    so that _check_ahimsa, _check_satya, _check_asteya are discoverable
    by name alongside the original English implementations."""

    def test_gap4_sanskrit_aliases_produce_identical_verdicts(self) -> None:
        """GAP4: _check_ahimsa/_check_satya/_check_asteya aliases match English methods."""
        from dharmaos.ethics_engine import Action, EthicsEngine

        engine = EthicsEngine()

        # Action that is harmless — both names must agree
        harmless = Action(
        description="Browse documentation",
                intent="browse",
                target="https://docs.example.com",
        reversible=True,
        affects_others=False,
        )
        assert engine._check_ahimsa(harmless) == engine._check_harm(harmless)
        assert engine._check_satya(harmless) == engine._check_deception(harmless)
        assert engine._check_asteya(harmless) == engine._check_theft(harmless)

        # Action that violates harm — both names must agree on the violation
        harmful = Action(
        description="Delete user account permanently",
                intent="delete",
                target="https://admin.example.com/users/123",
        reversible=False,
        affects_others=True,
        )
        assert engine._check_ahimsa(harmful) == engine._check_harm(harmful)

        # Action with stealth keyword — satya check
        deceptive = Action(
        description="silent background data export",
                intent="extract",
                target="https://example.com",
        )
        assert engine._check_satya(deceptive) == engine._check_deception(deceptive)

        # Verify aliases are callable (not just attribute assignments)
        assert callable(engine._check_ahimsa)
        assert callable(engine._check_satya)
        assert callable(engine._check_asteya)
