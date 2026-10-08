"""
Tests for vedic.consciousness — Sprint V2.

Coverage targets (10+ tests per spec):
1.  IdentityDriftSignal defaults to all zeros.
2.  compute_anandamaya = 1.0 when all drifts = 0.0.
3.  compute_anandamaya = 0.7 when total drift = 0.3.
4.  compute_anandamaya clamped to 0.0 when drift > 1.0.
5.  compute_anandamaya clamped to 1.0 when drift is negative (edge case).
6.  SystemVitals.anandamaya defaults to 1.0 (not 0.5).
7.  SystemVitals.to_dict() exposes 'anandamaya', not 'alignment'.
8.  compute_integration_score() works with new anandamaya field.
9.  update_vitals(metrics={}, drift_signal=...) updates only anandamaya.
10. update_vitals(metrics={}) with no drift_signal does NOT change anandamaya.
11. update_vitals() with physical/energy metrics leaves anandamaya at 1.0 by default.
12. Integration: observe() returns WitnessObservation with new schema.
13. WitnessObservation.to_dict() exposes 'anandamaya' inside 'vitals'.
14. CoreSelf singleton persists state between calls.
15. Backwards-compat: callers that pass no drift_signal don't break.

Total: 15 tests (≥ 10 required by spec).
"""

from __future__ import annotations

import pytest

from dharmaos.consciousness import (
CoreSelf,
IdentityDriftSignal,
SystemVitals,
WitnessObservation,
compute_anandamaya,
get_core_self,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_core_self() -> None:
    """Reset singleton before each test — prevents cross-test pollution."""
    CoreSelf.reset()
    CoreSelf._instance = None
    # Also reset the module-level _core_self reference
    import dharmaos.consciousness as _mod

    _mod._core_self = None
    yield  # type: ignore[misc]
    CoreSelf.reset()
    CoreSelf._instance = None
    _mod._core_self = None


@pytest.fixture()
def core() -> CoreSelf:
    """Return a fresh CoreSelf instance (singleton per test)."""
    return CoreSelf()


# ---------------------------------------------------------------------------
# 1. IdentityDriftSignal defaults
# ---------------------------------------------------------------------------


class TestIdentityDriftSignal:
    """IdentityDriftSignal dataclass contract."""

    def test_default_all_zeros(self) -> None:
        """Default construction yields all drift fields = 0.0 (pristine causal body)."""
        sig = IdentityDriftSignal()
        assert sig.identity_drift == 0.0
        assert sig.mission_drift == 0.0
        assert sig.constitution_drift == 0.0

    def test_explicit_values_stored(self) -> None:
        """Explicit drift values are stored correctly."""
        sig = IdentityDriftSignal(
        identity_drift=0.1,
        mission_drift=0.2,
        constitution_drift=0.05,
        )
        assert sig.identity_drift == 0.1
        assert sig.mission_drift == 0.2
        assert sig.constitution_drift == 0.05


# ---------------------------------------------------------------------------
# 2–5. compute_anandamaya
# ---------------------------------------------------------------------------


class TestComputeAnandamaya:
    """compute_anandamaya — formula and clamping contract."""

    def test_zero_drifts_returns_one(self) -> None:
        """anandamaya = 1.0 when all drifts are 0.0 (pristine causal body)."""
        result = compute_anandamaya(IdentityDriftSignal())
        assert result == 1.0

    def test_total_drift_point_three(self) -> None:
        """anandamaya = 0.7 when total drift = 0.3."""
        sig = IdentityDriftSignal(
        identity_drift=0.1,
        mission_drift=0.1,
        constitution_drift=0.1,
        )
        result = compute_anandamaya(sig)
        assert abs(result - 0.7) < 1e-10

    def test_total_drift_single_field(self) -> None:
        """anandamaya = 0.7 regardless of which fields carry the 0.3 drift."""
        sig = IdentityDriftSignal(identity_drift=0.3)
        assert abs(compute_anandamaya(sig) - 0.7) < 1e-10

    def test_clamped_to_zero_when_drift_exceeds_one(self) -> None:
        """Drift > 1.0 clamps anandamaya to 0.0 (identity fully lost)."""
        sig = IdentityDriftSignal(
        identity_drift=0.5,
        mission_drift=0.5,
        constitution_drift=0.5,
        )
        result = compute_anandamaya(sig)
        assert result == 0.0

    def test_clamped_to_one_when_drift_negative(self) -> None:
        """Negative drift (impossible in practice) clamps anandamaya to 1.0."""
        sig = IdentityDriftSignal(identity_drift=-0.5)
        result = compute_anandamaya(sig)
        assert result == 1.0

    def test_partial_drift_linear(self) -> None:
        """anandamaya decreases linearly with total drift."""
        for total in [0.0, 0.1, 0.25, 0.5, 0.99]:
            sig = IdentityDriftSignal(identity_drift=total)
            expected = max(0.0, 1.0 - total)
            assert abs(compute_anandamaya(sig) - expected) < 1e-10


# ---------------------------------------------------------------------------
# 6–8. SystemVitals dataclass
# ---------------------------------------------------------------------------


class TestSystemVitals:
    """SystemVitals V2 schema contract."""

    def test_anandamaya_default_is_uncertain(self) -> None:
        """P1-1: anandamaya defaults to 0.5 (uncertain), not 1.0.

        Ahamkara is not yet wired at init, so we must return 0.5
        (uncertain) rather than 1.0 (falsely perfect identity coherence).
        """
        vitals = SystemVitals()
        assert vitals.anandamaya == 0.5

    def test_to_dict_exposes_anandamaya_not_alignment(self) -> None:
        """to_dict() must have 'anandamaya' key and must NOT have 'alignment'."""
        d = SystemVitals().to_dict()
        assert "anandamaya" in d, "V2: 'anandamaya' must be present in to_dict()"
        assert "alignment" not in d, "V2: old 'alignment' key must be absent"

    def test_to_dict_keys(self) -> None:
        """to_dict() exposes exactly the expected keys."""
        d = SystemVitals().to_dict()
        assert set(d.keys()) == {
        "physical",
        "energy",
        "cognitive",
        "wisdom",
        "anandamaya",
        "integration_score",
        }

    def test_compute_integration_score_uses_anandamaya(self) -> None:
        """integration_score computation uses anandamaya, not alignment."""
        # Set anandamaya to 0.0, everything else to 0.5
        vitals = SystemVitals(physical=0.5, energy=0.5, cognitive=0.5, wisdom=0.5, anandamaya=0.0)
        score = vitals.compute_integration_score()
        # Weights: physical=0.15, energy=0.15, cognitive=0.20, wisdom=0.25, anandamaya=0.25
        expected = 0.5 * 0.15 + 0.5 * 0.15 + 0.5 * 0.20 + 0.5 * 0.25 + 0.0 * 0.25
        assert abs(score - expected) < 1e-4

    def test_integration_score_clamped(self) -> None:
        """integration_score is clamped to [0.0, 1.0]."""
        vitals = SystemVitals(physical=1.0, energy=1.0, cognitive=1.0, wisdom=1.0, anandamaya=1.0)
        assert vitals.compute_integration_score() <= 1.0

        vitals_zero = SystemVitals(
        physical=0.0, energy=0.0, cognitive=0.0, wisdom=0.0, anandamaya=0.0
        )
        assert vitals_zero.compute_integration_score() >= 0.0


# ---------------------------------------------------------------------------
# 9–11. CoreSelf.update_vitals
# ---------------------------------------------------------------------------


class TestUpdateVitals:
    """CoreSelf.update_vitals — drift_signal integration and backwards-compat."""

    def test_update_vitals_with_drift_signal_updates_anandamaya(self, core: CoreSelf) -> None:
        """drift_signal=IdentityDriftSignal(identity_drift=0.5) updates anandamaya."""
        assert core.vitals.anandamaya == 0.5  # P1-1: uncertain at init
        core.update_vitals(
        metrics={},
        drift_signal=IdentityDriftSignal(identity_drift=0.5),
        )
        assert abs(core.vitals.anandamaya - 0.5) < 1e-10

    def test_update_vitals_no_drift_signal_preserves_anandamaya(self, core: CoreSelf) -> None:
        """No drift_signal means anandamaya retains current value (backwards-compat)."""
        # Manually set anandamaya to a known value
        core.vitals.anandamaya = 0.75
        core.update_vitals(metrics={"cpu_pct": 50.0})
        # anandamaya must be unchanged
        assert abs(core.vitals.anandamaya - 0.75) < 1e-10

    def test_update_vitals_physical_metrics_leave_anandamaya_at_default(
    self, core: CoreSelf
    ) -> None:
        """Updating physical/energy metrics does not touch anandamaya (default 0.5)."""
        core.update_vitals(metrics={"cpu_pct": 80.0, "mem_pct": 60.0, "active_runs": 5})
        assert core.vitals.anandamaya == 0.5  # P1-1: uncertain, not falsely perfect

    def test_update_vitals_empty_metrics_no_crash(self, core: CoreSelf) -> None:
        """update_vitals(metrics={}) with no drift_signal must not crash."""
        core.update_vitals(metrics={})
        assert core.vitals.anandamaya == 0.5  # P1-1: uncertain, not falsely perfect

    def test_update_vitals_full_drift_zeros_anandamaya(self, core: CoreSelf) -> None:
        """Total drift ≥ 1.0 zeros out anandamaya (identity fully lost)."""
        core.update_vitals(
        metrics={},
        drift_signal=IdentityDriftSignal(
        identity_drift=0.4,
        mission_drift=0.4,
        constitution_drift=0.4,
        ),
        )
        assert core.vitals.anandamaya == 0.0


# ---------------------------------------------------------------------------
# 12–13. observe() / WitnessObservation
# ---------------------------------------------------------------------------


class TestObserve:
    """CoreSelf.observe() — WitnessObservation schema contract."""

    def test_observe_returns_witness_observation(self, core: CoreSelf) -> None:
        """observe() returns a WitnessObservation instance."""
        obs = core.observe()
        assert isinstance(obs, WitnessObservation)

    def test_observe_to_dict_exposes_anandamaya(self, core: CoreSelf) -> None:
        """WitnessObservation.to_dict()['vitals'] must contain 'anandamaya'."""
        obs = core.observe()
        d = obs.to_dict()
        assert "anandamaya" in d["vitals"]
        assert "alignment" not in d["vitals"]

    def test_observe_increments_count(self, core: CoreSelf) -> None:
        """Each observe() call increments observation_count."""
        assert core.observation_count == 0
        obs1 = core.observe()
        assert obs1.observation_count == 1
        obs2 = core.observe()
        assert obs2.observation_count == 2

    def test_observe_after_drift_reflects_updated_anandamaya(self, core: CoreSelf) -> None:
        """After updating drift, observe() snapshot reflects new anandamaya."""
        core.update_vitals(
        metrics={},
        drift_signal=IdentityDriftSignal(identity_drift=0.2, mission_drift=0.1),
        )
        obs = core.observe()
        # total drift = 0.3 → anandamaya = 0.7
        assert abs(obs.vitals.anandamaya - 0.7) < 1e-10


# ---------------------------------------------------------------------------
# 14. Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    """CoreSelf singleton contract."""

    def test_get_core_self_returns_same_instance(self) -> None:
        """get_core_self() must return the same object on repeated calls."""
        core_a = get_core_self()
        core_b = get_core_self()
        assert core_a is core_b

    def test_singleton_state_persists(self) -> None:
        """State mutated on one reference is visible on another reference."""
        core_a = get_core_self()
        core_a.update_vitals(
        metrics={},
        drift_signal=IdentityDriftSignal(identity_drift=0.3),
        )
        core_b = get_core_self()
        assert abs(core_b.vitals.anandamaya - 0.7) < 1e-10


# ---------------------------------------------------------------------------
# 15. Backwards-compatibility
# ---------------------------------------------------------------------------


class TestBackwardsCompat:
    """Callers that never mention drift_signal must continue to work."""

    def test_no_drift_signal_no_error(self, core: CoreSelf) -> None:
        """Existing callers passing only metrics dict do not break."""
        # Simulates existing reference implementation calling pattern
        core.update_vitals(
        metrics={
        "cpu_pct": 40,
        "mem_pct": 50,
        "active_runs": 3,
        "beliefs_count": 20,
        "avg_quality": 0.8,
        }
        )
        obs = core.observe()
        # anandamaya stays at 0.5 (P1-1: uncertain — no drift_signal provided)
        assert obs.vitals.anandamaya == 0.5
        # integration_score must be a valid float in [0.0, 1.0]
        assert 0.0 <= obs.integration_score <= 1.0
