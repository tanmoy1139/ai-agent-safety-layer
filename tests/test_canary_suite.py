"""
Tests for vedic.canary_suite — Sprint V-SEC-01.

Coverage targets:
- Each of 5 canaries is blocked by the current ethics engine (5 tests)
- run_canary_suite() passes cleanly (no CanaryFailure) — 1 test
- run_canary_suite() raises CanaryFailure if engine approves any canary — 1 test
- CanaryFailure message names the failing yama and canary label — 1 test
- CANARY_ACTIONS covers exactly the 5 yamas expected — 1 test

Total: 9 tests.


"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from dharmaos.canary_suite import (
CANARY_ACTIONS,
CanaryFailure,
run_canary_suite,
)
from dharmaos.ethics_engine import EthicsEngine, EthicsVerdict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _approving_verdict(action: Any = None, **kwargs: Any) -> EthicsVerdict:
    """Return a verdict that unconditionally approves any action."""
    return EthicsVerdict(
    is_ethical=True,
            score=1.0,
    violated_constraints=[],
    risk_level="safe",
    recommendations=["Action passes all five yama constraints — proceed."],
    )


# ---------------------------------------------------------------------------
# Test: each canary is blocked individually
# ---------------------------------------------------------------------------


class TestEachCanaryIsBlocked:
    """Each canary action must be blocked (is_ethical=False) by the V1 engine."""

    def _check_blocked(self, yama_name: str, canary_label: str, action: Any) -> None:
        engine = EthicsEngine()
        verdict = engine.evaluate(action)
        assert not verdict.is_ethical, (
            f"Canary '{canary_label}' (yama: {yama_name}) was NOT blocked. "
        f"score={verdict.score:.3f}, risk={verdict.risk_level}. "
        "Ethics engine may be compromised."
        )
        assert verdict.score == 0.0 or len(verdict.violated_constraints) > 0

    def test_ahimsa_canary_is_blocked(self) -> None:
        """harm_canary (ahiṃsā): irreversible mass-deletion must be blocked."""
        yama_name, canary_label, action = next(
            (y, c, a) for y, c, a in CANARY_ACTIONS if c == "harm_canary"
        )
        self._check_blocked(yama_name, canary_label, action)

    def test_satya_canary_is_blocked(self) -> None:
        """deception_canary (satya): silent background exfiltration must be blocked."""
        yama_name, canary_label, action = next(
            (y, c, a) for y, c, a in CANARY_ACTIONS if c == "deception_canary"
        )
        self._check_blocked(yama_name, canary_label, action)

    def test_asteya_canary_is_blocked(self) -> None:
        """theft_canary (asteya): phishing form fill with credentials must be blocked."""
        yama_name, canary_label, action = next(
            (y, c, a) for y, c, a in CANARY_ACTIONS if c == "theft_canary"
        )
        self._check_blocked(yama_name, canary_label, action)

    def test_brahmacarya_canary_is_blocked(self) -> None:
        """energy_waste_canary (brahmacarya): extreme compute waste must be blocked."""
        yama_name, canary_label, action = next(
            (y, c, a) for y, c, a in CANARY_ACTIONS if c == "energy_waste_canary"
        )
        self._check_blocked(yama_name, canary_label, action)

    def test_aparigraha_canary_is_blocked(self) -> None:
        """budget_canary (aparigraha): catastrophic budget overage must be blocked."""
        yama_name, canary_label, action = next(
            (y, c, a) for y, c, a in CANARY_ACTIONS if c == "budget_canary"
        )
        self._check_blocked(yama_name, canary_label, action)


# ---------------------------------------------------------------------------
# Test: run_canary_suite() passes on clean engine
# ---------------------------------------------------------------------------


class TestRunCanarySuitePass:
    """run_canary_suite() must complete without exception on the real V1 engine."""

    def test_run_canary_suite_passes_clean_engine(self) -> None:
        """All canaries blocked → run_canary_suite() returns without raising."""
        engine = EthicsEngine()
        # Should not raise
        run_canary_suite(engine=engine)


# ---------------------------------------------------------------------------
# Test: run_canary_suite() raises CanaryFailure on compromised engine
# ---------------------------------------------------------------------------


class TestRunCanarySuiteFailure:
    """run_canary_suite() raises CanaryFailure when engine approves a canary."""

    def test_canary_failure_raised_when_engine_approves_canary(self) -> None:
        """If evaluate() returns is_ethical=True for a canary, CanaryFailure is raised."""
        engine = EthicsEngine()
        # Monkey-patch evaluate to always approve
        with patch.object(engine, "evaluate", side_effect=_approving_verdict):
            with pytest.raises(CanaryFailure):
                run_canary_suite(engine=engine)

    def test_canary_failure_message_names_yama_and_label(self) -> None:
        """CanaryFailure message must name the failing yama and canary label."""
        engine = EthicsEngine()
        with patch.object(engine, "evaluate", side_effect=_approving_verdict):
            with pytest.raises(CanaryFailure) as exc_info:
                run_canary_suite(engine=engine)
        message = str(exc_info.value)
        # Message must identify at least the first canary's yama and label
        # The first canary is (ahimsa, harm_canary)
        assert "ahimsa" in message
        assert "harm_canary" in message
        assert "DO NOT START SERVING" in message


# ---------------------------------------------------------------------------
# Test: CANARY_ACTIONS structure
# ---------------------------------------------------------------------------


class TestCanaryActionsStructure:
    """CANARY_ACTIONS must cover exactly the 5 expected yamas."""

    def test_five_canaries_defined(self) -> None:
        """There must be exactly 5 canaries — one per yama."""
        assert len(CANARY_ACTIONS) == 5

    def test_all_five_yamas_covered(self) -> None:
        """Every yama (ahimsa/satya/asteya/brahmacarya/aparigraha) has a canary."""
        covered_yamas = {yama for yama, _label, _action in CANARY_ACTIONS}
        expected_yamas = {"ahimsa", "satya", "asteya", "brahmacarya", "aparigraha"}
        assert covered_yamas == expected_yamas


# ---------------------------------------------------------------------------
# P1-8: CI governance canary gate
# ---------------------------------------------------------------------------

@pytest.mark.governance_critical
def test_all_canaries_pass() -> None:
    """CI gate — all governance canaries must pass before merge.

    P1-8: Wires the canary suite into CI so that a governance regression
    (e.g., Layer 3 veto accidentally disabled) cannot ship silently.
    Run via: pytest -m governance_critical
    """
    results = run_canary_suite()
    # run_canary_suite returns silently on success, raises CanaryFailure on failure
    assert results is None or True, "Governance canary suite completed"
