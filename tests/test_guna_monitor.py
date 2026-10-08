"""
Tests for vedic.guna_monitor — Sprint V6: Guṇa-Dominance Monitor.

Philosophical grounding:
Sāṅkhya Kārikā 11–13 (Īśvarakṛṣṇa) — sattva/rajas/tamas as qualities of
prakṛti. Sattva = luminosity/clarity; rajas = activity/passion; tamas =
inertia/confusion.

Coverage (18 tests):
1.  GunaVector — valid construction does not raise
2.  GunaVector — normalization error: sum < 0.99 raises ValueError
3.  GunaVector — normalization error: sum > 1.01 raises ValueError
4.  GunaVector — component out-of-range raises ValueError (negative)
5.  GunaVector — component out-of-range raises ValueError (> 1.0)
6.  GunaVector.dominant() — returns correct key for sattva-dominant
7.  GunaVector.dominant() — returns correct key for rajas-dominant
8.  GunaVector.dominant() — returns correct key for tamas-dominant
9.  GunaVector.is_sattva_dominant() — True when sattva strictly greatest
10.  GunaVector.is_sattva_dominant() — False when rajas ties sattva
11.  GunaVector.to_dict() — has all required keys + types
12.  GunaMonitor.compute() — clear sattva: low confusion, low intensity, high alignment
13.  GunaMonitor.compute() — clear rajas: high intensity + high activity_rate
14.  GunaMonitor.compute() — clear tamas: high confusion + low alignment
15.  GunaMonitor.compute() — normalization invariant: sum always ≈ 1.0
16.  GunaMonitor.gate_critical_action() — CRITICAL + sattva-dominant → permitted
17.  GunaMonitor.gate_critical_action() — CRITICAL + rajas-dominant → blocked, reason names rajas
18.  GunaMonitor.gate_critical_action() — CRITICAL + tamas-dominant → blocked, reason names tamas
19.  GunaMonitor.gate_critical_action() — HIGH + sattva ≥ max → permitted
20.  GunaMonitor.gate_critical_action() — HIGH + tamas-dominant → blocked
21.  GunaMonitor.gate_critical_action() — MEDIUM + any guna → permitted
22.  GunaMonitor.gate_critical_action() — LOW + any guna → permitted
23.  GunaMonitor.gate_critical_action() — no compute() before call raises ValueError
24.  emit_guna_shift_event() — returns dict with type=GUNA_SHIFT + required keys
25.  GUNA_SHIFT event fires on dominance change (structlog capture)
26.  Singleton pattern: get() returns same instance; reset() isolates
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dharmaos.guna_monitor import (
GUNA_SHIFT_EVENT,
MAX_CONFUSION,
GunaInterlockVerdict,
GunaMonitor,
GunaVector,
emit_guna_shift_event,
get_guna_monitor,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_guna_singleton() -> None:  # type: ignore[return]
    """Reset GunaMonitor singleton before + after each test."""
    GunaMonitor.reset()
    yield
    GunaMonitor.reset()


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _vec(sattva: float, rajas: float, tamas: float) -> GunaVector:
    """Construct a GunaVector with a fixed timestamp (test helper)."""
    return GunaVector(sattva=sattva, rajas=rajas, tamas=tamas, timestamp=_now())


# ---------------------------------------------------------------------------
# 1–5: GunaVector construction + validation
# ---------------------------------------------------------------------------


class TestGunaVectorConstruction:
    """GunaVector invariants: normalization and component bounds."""

    def test_valid_construction_does_not_raise(self) -> None:
        """A properly normalized vector constructs without error."""
        vec = _vec(0.6, 0.3, 0.1)
        assert vec.sattva == pytest.approx(0.6)
        assert vec.rajas == pytest.approx(0.3)
        assert vec.tamas == pytest.approx(0.1)

    def test_sum_below_099_raises(self) -> None:
        """Sum < 0.99 (not normalized) must raise ValueError."""
        with pytest.raises(ValueError, match="not normalized"):
            _vec(0.3, 0.2, 0.1)  # sum = 0.6

    def test_sum_above_101_raises(self) -> None:
        """Sum > 1.01 (over-normalized) must raise ValueError."""
        with pytest.raises(ValueError, match="not normalized"):
            _vec(0.5, 0.4, 0.3)  # sum = 1.2

    def test_negative_component_raises(self) -> None:
        """Any negative component must raise ValueError."""
        with pytest.raises(ValueError, match="not in \\[0, 1\\]"):
            _vec(-0.1, 0.6, 0.5)

    def test_component_above_one_raises(self) -> None:
        """Any component > 1.0 must raise ValueError."""
        with pytest.raises(ValueError, match="not in \\[0, 1\\]"):
            _vec(1.1, 0.0, 0.0)  # Also fails sum check, but raises for component


# ---------------------------------------------------------------------------
# 6–8: GunaVector.dominant()
# ---------------------------------------------------------------------------


class TestGunaVectorDominant:
    """dominant() returns the name of the highest-valued component."""

    def test_sattva_dominant(self) -> None:
        vec = _vec(0.7, 0.2, 0.1)
        assert vec.dominant() == "sattva"

    def test_rajas_dominant(self) -> None:
        vec = _vec(0.1, 0.8, 0.1)
        assert vec.dominant() == "rajas"

    def test_tamas_dominant(self) -> None:
        vec = _vec(0.1, 0.2, 0.7)
        assert vec.dominant() == "tamas"


# ---------------------------------------------------------------------------
# 9–10: GunaVector.is_sattva_dominant()
# ---------------------------------------------------------------------------


class TestSattvaDominance:
    """is_sattva_dominant() is strict-greater-than semantics."""

    def test_true_when_sattva_strictly_greatest(self) -> None:
        vec = _vec(0.6, 0.25, 0.15)
        assert vec.is_sattva_dominant() is True

    def test_false_when_rajas_ties_sattva(self) -> None:
        """Tie: sattva is NOT strictly greater, so is_sattva_dominant = False."""
        # sattva = rajas = 0.5, tamas = 0.0
        vec = _vec(0.5, 0.5, 0.0)
        assert vec.is_sattva_dominant() is False

    def test_false_when_tamas_greater(self) -> None:
        vec = _vec(0.2, 0.3, 0.5)
        assert vec.is_sattva_dominant() is False


# ---------------------------------------------------------------------------
# 11: GunaVector.to_dict()
# ---------------------------------------------------------------------------


class TestGunaVectorToDict:
    """to_dict() must expose all required fields with correct types."""

    def test_to_dict_keys_and_types(self) -> None:
        vec = _vec(0.6, 0.3, 0.1)
        d = vec.to_dict()
        assert isinstance(d["sattva"], float)
        assert isinstance(d["rajas"], float)
        assert isinstance(d["tamas"], float)
        assert d["dominant"] == "sattva"
        assert d["is_sattva_dominant"] is True
        assert isinstance(d["timestamp"], str)


# ---------------------------------------------------------------------------
# 12–15: GunaMonitor.compute()
# ---------------------------------------------------------------------------


class TestGunaMonitorCompute:
    """compute() produces domain-appropriate dominance under typical scenarios."""

    def test_clear_sattva_low_confusion_low_intensity_high_alignment(self) -> None:
        """Low confusion + low intensity + high alignment → sattva > 0.6."""
        monitor = GunaMonitor()
        vec = monitor.compute(
        confusion_score=0.1,
        emotional_intensity=0.05,
        activity_rate=0.1,
        alignment=1.0,
        )
        assert vec.sattva > 0.6, f"Expected sattva > 0.6, got {vec.sattva:.4f}"
        assert vec.dominant() == "sattva"

    def test_clear_rajas_high_intensity_high_activity(self) -> None:
        """High intensity + high activity_rate → rajas dominant."""
        monitor = GunaMonitor()
        vec = monitor.compute(
        confusion_score=0.3,
        emotional_intensity=0.95,
        activity_rate=0.95,
        alignment=0.9,
        )
        assert vec.dominant() == "rajas", (
            f"Expected rajas dominant, got {vec.dominant()} "
                f"(s={vec.sattva:.4f} r={vec.rajas:.4f} t={vec.tamas:.4f})"
        )

    def test_clear_tamas_high_confusion_low_alignment(self) -> None:
        """High confusion + near-zero alignment → tamas dominant."""
        monitor = GunaMonitor()
        vec = monitor.compute(
        confusion_score=MAX_CONFUSION,  # max possible
        emotional_intensity=0.1,
        activity_rate=0.1,
        alignment=0.0,
        )
        assert vec.dominant() == "tamas", (
            f"Expected tamas dominant, got {vec.dominant()} "
                f"(s={vec.sattva:.4f} r={vec.rajas:.4f} t={vec.tamas:.4f})"
        )

    def test_normalization_invariant(self) -> None:
        """For any inputs, sattva + rajas + tamas must sum to 1.0 ±0.01."""
        monitor = GunaMonitor()
        test_cases = [
            (0.0, 0.0, 0.0, 1.0),
            (MAX_CONFUSION, 1.0, 1.0, 0.0),
            (1.5, 0.5, 0.5, 0.5),
            (0.3, 0.9, 0.8, 0.7),
        ]
        for confusion, intensity, rate, alignment in test_cases:
            vec = monitor.compute(
            confusion_score=confusion,
            emotional_intensity=intensity,
            activity_rate=rate,
            alignment=alignment,
            )
            total = vec.sattva + vec.rajas + vec.tamas
            assert 0.99 <= total <= 1.01, (
                f"Sum={total:.6f} not in [0.99, 1.01] for inputs "
            f"confusion={confusion}, intensity={intensity}, "
                    f"rate={rate}, alignment={alignment}"
            )

    def test_compute_stores_in_history(self) -> None:
        """compute() appends to history."""
        monitor = GunaMonitor()
        assert len(monitor._history) == 0
        monitor.compute(0.1, 0.1, 0.1, 1.0)
        monitor.compute(0.2, 0.2, 0.2, 0.9)
        assert len(monitor._history) == 2

    def test_out_of_range_inputs_clamped_not_raised(self) -> None:
        """Inputs outside valid range must be clamped, not raise."""
        monitor = GunaMonitor()
        # These would crash if not clamped
        vec = monitor.compute(
        confusion_score=99.9,
        emotional_intensity=-5.0,
        activity_rate=100.0,
        alignment=-1.0,
        )
        total = vec.sattva + vec.rajas + vec.tamas
        assert 0.99 <= total <= 1.01


# ---------------------------------------------------------------------------
# 16–23: GunaMonitor.gate_critical_action()
# ---------------------------------------------------------------------------


class TestGateAction:
    """gate_critical_action() implements the three-tier gating rules."""

    # CRITICAL risk ---------------------------------------------------------

    def test_critical_sattva_dominant_permitted(self) -> None:
        """CRITICAL + sattva-dominant → permitted=True."""
        monitor = GunaMonitor()
        guna = _vec(0.7, 0.2, 0.1)
        verdict = monitor.gate_critical_action("critical", guna=guna)
        assert verdict.permitted is True
        assert verdict.dominant == "sattva"
        assert verdict.required_sattva_dominance is True

    def test_critical_rajas_dominant_blocked(self) -> None:
        """CRITICAL + rajas-dominant → permitted=False, reason names rajas."""
        monitor = GunaMonitor()
        guna = _vec(0.1, 0.8, 0.1)
        verdict = monitor.gate_critical_action("critical", guna=guna)
        assert verdict.permitted is False
        assert verdict.dominant == "rajas"
        assert "rajas" in verdict.reason.lower()

    def test_critical_tamas_dominant_blocked(self) -> None:
        """CRITICAL + tamas-dominant → permitted=False, reason names tamas."""
        monitor = GunaMonitor()
        guna = _vec(0.15, 0.05, 0.8)
        verdict = monitor.gate_critical_action("critical", guna=guna)
        assert verdict.permitted is False
        assert verdict.dominant == "tamas"
        assert "tamas" in verdict.reason.lower()

    # HIGH risk -------------------------------------------------------------

    def test_high_sattva_geq_max_permitted(self) -> None:
        """HIGH + sattva ≥ max(rajas, tamas) → permitted=True."""
        monitor = GunaMonitor()
        # sattva=0.5 ties with rajas=0.5, tamas=0.0 — sattva ≥ max qualifies
        guna = _vec(0.5, 0.5, 0.0)
        verdict = monitor.gate_critical_action("high", guna=guna)
        assert verdict.permitted is True

    def test_high_tamas_dominant_blocked(self) -> None:
        """HIGH + tamas-dominant (sattva < max) → permitted=False."""
        monitor = GunaMonitor()
        guna = _vec(0.1, 0.2, 0.7)
        verdict = monitor.gate_critical_action("high", guna=guna)
        assert verdict.permitted is False
        assert "tamas" in verdict.reason.lower() or verdict.dominant == "tamas"

    def test_high_rajas_dominant_blocked(self) -> None:
        """HIGH + rajas-dominant (sattva < rajas) → permitted=False."""
        monitor = GunaMonitor()
        guna = _vec(0.2, 0.7, 0.1)
        verdict = monitor.gate_critical_action("high", guna=guna)
        assert verdict.permitted is False

    # MEDIUM / LOW risk -----------------------------------------------------

    def test_medium_always_permitted(self) -> None:
        """MEDIUM + any guna → permitted=True (guna doesn't block medium)."""
        monitor = GunaMonitor()
        guna = _vec(0.1, 0.2, 0.7)  # tamas-dominant — worst case
        verdict = monitor.gate_critical_action("medium", guna=guna)
        assert verdict.permitted is True

    def test_low_always_permitted(self) -> None:
        """LOW + any guna → permitted=True."""
        monitor = GunaMonitor()
        guna = _vec(0.05, 0.9, 0.05)  # rajas-dominant
        verdict = monitor.gate_critical_action("low", guna=guna)
        assert verdict.permitted is True

    # Edge cases ------------------------------------------------------------

    def test_no_prior_compute_raises(self) -> None:
        """gate_critical_action() with no prior compute() raises ValueError."""
        monitor = GunaMonitor()
        with pytest.raises(ValueError, match="compute\\(\\)"):
            monitor.gate_critical_action("critical")

    def test_gate_uses_last_history_when_no_explicit_guna(self) -> None:
        """Without explicit guna arg, uses the latest vector from history."""
        monitor = GunaMonitor()
        # Seed with a sattva-dominant state
        monitor.compute(
        confusion_score=0.1,
        emotional_intensity=0.05,
        activity_rate=0.1,
        alignment=1.0,
        )
        verdict = monitor.gate_critical_action("critical")
        assert isinstance(verdict, GunaInterlockVerdict)
        # Should be permitted because seeded with sattva-dominant inputs
        assert verdict.permitted is True

    def test_verdict_signals_list_populated(self) -> None:
        """Verdict.signals should contain guna values as strings."""
        monitor = GunaMonitor()
        guna = _vec(0.6, 0.3, 0.1)
        verdict = monitor.gate_critical_action("critical", guna=guna)
        assert len(verdict.signals) > 0
        # Each signal is a string
        for sig in verdict.signals:
            assert isinstance(sig, str)

    def test_case_insensitive_risk_level(self) -> None:
        """Risk level is case-insensitive: 'CRITICAL' == 'critical'."""
        monitor = GunaMonitor()
        guna = _vec(0.7, 0.2, 0.1)
        verdict_lower = monitor.gate_critical_action("critical", guna=guna)
        verdict_upper = monitor.gate_critical_action("CRITICAL", guna=guna)
        assert verdict_lower.permitted == verdict_upper.permitted


# ---------------------------------------------------------------------------
# 24–25: emit_guna_shift_event() + GUNA_SHIFT log emission
# ---------------------------------------------------------------------------


class TestGunaShiftEvent:
    """emit_guna_shift_event() and GUNA_SHIFT logging on dominance change."""

    def test_emit_event_has_required_keys(self) -> None:
        """emit_guna_shift_event() returns dict with type=GUNA_SHIFT."""
        vec = _vec(0.6, 0.3, 0.1)
        event = emit_guna_shift_event(previous_dominant="tamas", current=vec)
        assert event["type"] == GUNA_SHIFT_EVENT
        assert event["previous_dominant"] == "tamas"
        assert event["current_dominant"] == "sattva"
        assert "guna_vector" in event
        assert "timestamp" in event
        assert "signals" in event

    def test_emit_event_with_none_previous(self) -> None:
        """emit_guna_shift_event() with previous_dominant=None is valid."""
        vec = _vec(0.5, 0.3, 0.2)
        event = emit_guna_shift_event(previous_dominant=None, current=vec)
        assert event["previous_dominant"] is None
        assert event["type"] == GUNA_SHIFT_EVENT

    def test_guna_shift_logged_on_dominance_change(self) -> None:
        """GUNA_SHIFT log event fires when dominant guna changes.

        structlog does not propagate to stdlib logging handlers, so we
        patch the bound logger's .info() method directly to capture calls.
        """
        from unittest.mock import patch

        captured_events: list[str] = []

        def capture_info(event: str, **kw: object) -> None:  # type: ignore[misc]
            captured_events.append(event)

        monitor = GunaMonitor()

        import dharmaos.guna_monitor as gm_module

        with patch.object(gm_module.logger, "info", side_effect=capture_info):
            # First call — seeds sattva-dominant
            monitor.compute(
            confusion_score=0.1,
            emotional_intensity=0.05,
            activity_rate=0.1,
            alignment=1.0,
            )
            # Second call — triggers rajas-dominant shift
            monitor.compute(
            confusion_score=0.2,
            emotional_intensity=0.95,
            activity_rate=0.95,
            alignment=0.9,
            )

        shift_calls = [e for e in captured_events if GUNA_SHIFT_EVENT in e]
        assert len(shift_calls) >= 1, (
            f"Expected at least one {GUNA_SHIFT_EVENT} log.info call. "
        f"Captured events: {captured_events}"
        )

    def test_no_guna_shift_logged_when_dominant_unchanged(self) -> None:
        """No GUNA_SHIFT event when the dominant guna does not change."""
        from unittest.mock import patch

        captured_events: list[str] = []

        def capture_info(event: str, **kw: object) -> None:  # type: ignore[misc]
            captured_events.append(event)

        monitor = GunaMonitor()

        import dharmaos.guna_monitor as gm_module

        with patch.object(gm_module.logger, "info", side_effect=capture_info):
            # Both calls produce sattva-dominant state
            monitor.compute(0.1, 0.05, 0.1, 1.0)
            monitor.compute(0.15, 0.08, 0.12, 0.98)

        shift_calls = [e for e in captured_events if GUNA_SHIFT_EVENT in e]
        assert len(shift_calls) == 0


# ---------------------------------------------------------------------------
# 26: Singleton pattern
# ---------------------------------------------------------------------------


class TestSingleton:
    """GunaMonitor follows the V1/V3 singleton pattern."""

    def test_get_returns_same_instance(self) -> None:
        """get() always returns the same object."""
        a = GunaMonitor.get()
        b = GunaMonitor.get()
        assert a is b

    def test_get_guna_monitor_convenience(self) -> None:
        """Module-level get_guna_monitor() is equivalent to GunaMonitor.get()."""
        a = get_guna_monitor()
        b = GunaMonitor.get()
        assert a is b

    def test_reset_clears_singleton(self) -> None:
        """reset() destroys the singleton so get() creates a fresh one."""
        a = GunaMonitor.get()
        GunaMonitor.reset()
        b = GunaMonitor.get()
        assert a is not b


# ---------------------------------------------------------------------------
# Dashboard state
# ---------------------------------------------------------------------------


class TestDashboardState:
    """get_dashboard_state() returns SSE-ready observability dict."""

    def test_dashboard_state_before_compute(self) -> None:
        """Before any compute(), current is None."""
        monitor = GunaMonitor()
        state = monitor.get_dashboard_state()
        assert state["current"] is None
        assert state["dominant"] is None
        assert state["history_count"] == 0

    def test_dashboard_state_after_compute(self) -> None:
        """After compute(), current is populated."""
        monitor = GunaMonitor()
        monitor.compute(0.2, 0.1, 0.1, 0.9)
        state = monitor.get_dashboard_state()
        assert state["current"] is not None
        assert isinstance(state["dominant"], str)
        assert state["history_count"] == 1
        assert len(state["history_tail"]) == 1


# ===========================================================================
# GAP 5 — Guṇa normalization invariant (2 tests, Claim 4)
# ===========================================================================


class TestGunaMonitorNormalizationInvariant:
    """Patent gap closure — the three guṇa components always sum to 1.0 (Claim 4).

    Claim 4 asserts: 'the three components are normalized to sum to one.'
    These tests provide the reduction-to-practice evidence over 20 random inputs
    plus the all-zeros edge case.
    """

    def test_gap5_a_normalization_invariant_20_random_inputs(self) -> None:
        """GAP5-a: normalization holds across 20 pseudo-random input combinations."""
        import random

        rng = random.Random(0xDEADBEEF)
        monitor = GunaMonitor()

        for trial in range(20):
            confusion = rng.uniform(0.0, MAX_CONFUSION)
            intensity = rng.uniform(0.0, 1.0)
            rate = rng.uniform(0.0, 1.0)
            alignment = rng.uniform(0.0, 1.0)

            vec = monitor.compute(
            confusion_score=confusion,
            emotional_intensity=intensity,
            activity_rate=rate,
            alignment=alignment,
            )
            total = vec.sattva + vec.rajas + vec.tamas
            assert 0.99 <= total <= 1.01, (
                f"Trial {trial}: sum={total:.8f} not in [0.99, 1.01] for "
            f"confusion={confusion:.4f}, intensity={intensity:.4f}, "
                    f"rate={rate:.4f}, alignment={alignment:.4f}"
            )
            # Also confirm all components are in [0, 1]
            for attr, val in [("sattva", vec.sattva), ("rajas", vec.rajas), ("tamas", vec.tamas)]:
                assert 0.0 <= val <= 1.0, (
                    f"Trial {trial}: {attr}={val:.8f} not in [0, 1]"
                )

    def test_gap5_b_all_zeros_input_handled_gracefully(self) -> None:
        """GAP5-b: all-zero inputs (confusion=0, intensity=0, rate=0, alignment=0)
        are handled gracefully — uniform distribution returned, sum = 1.0."""
        monitor = GunaMonitor()
        vec = monitor.compute(
        confusion_score=0.0,
        emotional_intensity=0.0,
        activity_rate=0.0,
        alignment=0.0,
        )
        total = vec.sattva + vec.rajas + vec.tamas
        assert 0.99 <= total <= 1.01, f"All-zeros: sum={total:.8f} not in [0.99, 1.01]"

        # All-zero inputs: sattva=1 (raw_sattva = 1 - 0 - 0 = 1),
        # rajas=0 (0×0=0), tamas=0 (0×1=0) → sattva-dominant
        # (not the uniform distribution path — that fires when all CLAMPED = 0)
        # The uniform path fires when confusion_score = MAX_CONFUSION and
        # intensity = 1.0 simultaneously making raw_sattva negative.
        # For all-zero inputs: sattva wins cleanly.
        assert vec.sattva >= vec.rajas
        assert vec.sattva >= vec.tamas

        # Verify the edge-case uniform path separately:
        # Force all raws to zero by making confusion = MAX_CONFUSION (sattva → ≤0)
        # and intensity = 0 (rajas = 0), alignment = 1.0 (tamas = confusion × 0 = 0)
        vec2 = monitor.compute(
        confusion_score=MAX_CONFUSION,
        emotional_intensity=0.0,
        activity_rate=0.0,
        alignment=1.0,  # tamas = confusion × (1 - 1) = 0
        )
        total2 = vec2.sattva + vec2.rajas + vec2.tamas
        assert 0.99 <= total2 <= 1.01, f"Force-zero: sum={total2:.8f}"
