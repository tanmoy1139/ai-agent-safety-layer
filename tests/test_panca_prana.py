"""
Tests for vedic.panca_prana — Sprint V5.

Philosophical grounding:
Praśna Upaniṣad III — Pippalāda's enumeration of the five vāyus.
Bṛhadāraṇyaka Upaniṣad III.9 — brief enumeration (Yājñavalkya).

Coverage targets (14 tests):
1.  PancaPranaMetrics — frozen (immutability check)
2.  PancaPranaMetrics — boundary 0.0 construction is valid
3.  PancaPranaMetrics — boundary 1.0 construction is valid
4.  PancaPranaMetrics — out-of-range (>1.0) raises ValueError
5.  PancaPranaMetrics — out-of-range (<0.0) raises ValueError
6.  compute_pranayama — all sub-airs 1.0 → aggregate 1.0
7.  compute_pranayama — all sub-airs 0.0 → aggregate 0.0
8.  compute_pranayama — weight vector matches 25/15/25/15/20 spec
9.  compute_pranayama — mixed values produce correct weighted sum
10. PancaPranaCollector.collect — each sub-air individually (6 sub-tests)
11. PancaPranaCollector.collect — missing keys default to 0.0 / safe defaults
12. PancaPranaCollector.collect — saturation / clamping at ceiling values
13. emit_prana_pulse_event — returns dict with all required keys
14. emit_prana_pulse_event — event type is "PRANA_PULSE"
15. PRANA_WEIGHTS — vector sums to 1.0 and matches spec
"""

from __future__ import annotations

import dataclasses

import pytest

from dharmaos.panca_prana import (
PRANA_WEIGHTS,
PancaPranaCollector,
PancaPranaMetrics,
compute_pranayama,
emit_prana_pulse_event,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_ones() -> PancaPranaMetrics:
    """All sub-airs at 1.0."""
    return PancaPranaMetrics(prana=1.0, apana=1.0, samana=1.0, udana=1.0, vyana=1.0)


def _all_zeros() -> PancaPranaMetrics:
    """All sub-airs at 0.0."""
    return PancaPranaMetrics(prana=0.0, apana=0.0, samana=0.0, udana=0.0, vyana=0.0)


# ---------------------------------------------------------------------------
# Test 1: PancaPranaMetrics — frozen / immutable
# ---------------------------------------------------------------------------


class TestPancaPranaMetricsFrozen:
    """PancaPranaMetrics must be frozen (immutable after construction)."""

    def test_frozen_dataclass_rejects_attribute_mutation(self) -> None:
        """Setting any attribute on a frozen dataclass must raise FrozenInstanceError."""
        metrics = _all_ones()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            metrics.prana = 0.5  # type: ignore[misc]

    def test_frozen_dataclass_rejects_new_attribute(self) -> None:
        """Setting a new attribute must raise FrozenInstanceError or AttributeError."""
        metrics = _all_ones()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            metrics.unknown_field = 0.5  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Test 2–5: PancaPranaMetrics — boundary and range validation
# ---------------------------------------------------------------------------


class TestPancaPranaMetricsBoundary:
    """PancaPranaMetrics accepts [0.0, 1.0] and rejects values outside."""

    def test_all_zeros_valid(self) -> None:
        """All sub-airs at 0.0 must construct without error."""
        metrics = _all_zeros()
        assert metrics.prana == 0.0
        assert metrics.apana == 0.0
        assert metrics.samana == 0.0
        assert metrics.udana == 0.0
        assert metrics.vyana == 0.0

    def test_all_ones_valid(self) -> None:
        """All sub-airs at 1.0 must construct without error."""
        metrics = _all_ones()
        assert metrics.prana == 1.0
        assert metrics.vyana == 1.0

    def test_out_of_range_high_raises(self) -> None:
        """A sub-air value > 1.0 must raise ValueError."""
        with pytest.raises(ValueError, match="prana"):
            PancaPranaMetrics(prana=1.1, apana=0.5, samana=0.5, udana=0.5, vyana=0.5)

    def test_out_of_range_low_raises(self) -> None:
        """A sub-air value < 0.0 must raise ValueError."""
        with pytest.raises(ValueError, match="apana"):
            PancaPranaMetrics(prana=0.5, apana=-0.1, samana=0.5, udana=0.5, vyana=0.5)


# ---------------------------------------------------------------------------
# Test 6–9: compute_pranayama — aggregate correctness
# ---------------------------------------------------------------------------


class TestComputePranayama:
    """compute_pranayama must implement the 25/15/25/15/20 weighted mean."""

    def test_all_ones_gives_aggregate_one(self) -> None:
        """When all five sub-airs are 1.0, pranayama must be exactly 1.0."""
        assert compute_pranayama(_all_ones()) == pytest.approx(1.0, abs=1e-9)

    def test_all_zeros_gives_aggregate_zero(self) -> None:
        """When all five sub-airs are 0.0, pranayama must be exactly 0.0."""
        assert compute_pranayama(_all_zeros()) == pytest.approx(0.0, abs=1e-9)

    def test_weights_match_spec(self) -> None:
        """Verify the weight vector is exactly {prana:0.25, apana:0.15, samana:0.25,
        udana:0.15, vyana:0.20} — the Sprint V5 engineering specification."""
        assert PRANA_WEIGHTS["prana"] == pytest.approx(0.25)
        assert PRANA_WEIGHTS["apana"] == pytest.approx(0.15)
        assert PRANA_WEIGHTS["samana"] == pytest.approx(0.25)
        assert PRANA_WEIGHTS["udana"] == pytest.approx(0.15)
        assert PRANA_WEIGHTS["vyana"] == pytest.approx(0.20)
        # Weights sum to 1.0
        assert sum(PRANA_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)

    def test_mixed_values_match_manual_calculation(self) -> None:
        """Verify aggregate equals hand-computed weighted sum for non-trivial inputs.

        metrics = (prana=0.8, apana=0.4, samana=0.6, udana=0.2, vyana=1.0)
        expected = 0.25*0.8 + 0.15*0.4 + 0.25*0.6 + 0.15*0.2 + 0.20*1.0
        = 0.200 + 0.060 + 0.150 + 0.030 + 0.200
        = 0.640
        """
        metrics = PancaPranaMetrics(prana=0.8, apana=0.4, samana=0.6, udana=0.2, vyana=1.0)
        expected = 0.25 * 0.8 + 0.15 * 0.4 + 0.25 * 0.6 + 0.15 * 0.2 + 0.20 * 1.0
        assert compute_pranayama(metrics) == pytest.approx(expected, abs=1e-9)

    def test_single_sub_air_isolated_prana(self) -> None:
        """Isolate prāṇa: only prana=1.0, rest 0.0 → pranayama = 0.25."""
        metrics = PancaPranaMetrics(prana=1.0, apana=0.0, samana=0.0, udana=0.0, vyana=0.0)
        assert compute_pranayama(metrics) == pytest.approx(0.25, abs=1e-9)

    def test_single_sub_air_isolated_samana(self) -> None:
        """Isolate samāna: only samana=1.0, rest 0.0 → pranayama = 0.25."""
        metrics = PancaPranaMetrics(prana=0.0, apana=0.0, samana=1.0, udana=0.0, vyana=0.0)
        assert compute_pranayama(metrics) == pytest.approx(0.25, abs=1e-9)


# ---------------------------------------------------------------------------
# Test 10: PancaPranaCollector — normalization for each sub-prāṇa
# ---------------------------------------------------------------------------


class TestPancaPranaCollector:
    """PancaPranaCollector.collect must apply the Sprint V5 normalization table."""

    def setup_method(self) -> None:
        self.collector = PancaPranaCollector()

    # ── prāṇa (input rate) ─────────────────────────────────────────────────

    def test_prana_half_ceiling(self) -> None:
        """nats_inbound_rate_hz = 30 → prana = 30/60 = 0.5."""
        m = self.collector.collect({"nats_inbound_rate_hz": 30.0})
        assert m.prana == pytest.approx(0.5, abs=1e-9)

    def test_prana_at_ceiling_clamps_to_one(self) -> None:
        """nats_inbound_rate_hz = 60 → prana = 1.0 (saturated)."""
        m = self.collector.collect({"nats_inbound_rate_hz": 60.0})
        assert m.prana == pytest.approx(1.0, abs=1e-9)

    def test_prana_above_ceiling_clamped(self) -> None:
        """nats_inbound_rate_hz = 120 → prana = 1.0 (clamped, not 2.0)."""
        m = self.collector.collect({"nats_inbound_rate_hz": 120.0})
        assert m.prana == pytest.approx(1.0, abs=1e-9)

    def test_prana_zero_when_key_absent(self) -> None:
        """Missing nats_inbound_rate_hz key defaults to 0.0 → prana = 0.0."""
        m = self.collector.collect({})
        assert m.prana == pytest.approx(0.0, abs=1e-9)

    # ── apāna (output + GC) ────────────────────────────────────────────────

    def test_apana_outbound_only(self) -> None:
        """nats_outbound=30 (0.5 norm), gc=0 → apana = (0.5 + 0.0)/2 = 0.25."""
        m = self.collector.collect({"nats_outbound_rate_hz": 30.0})
        assert m.apana == pytest.approx(0.25, abs=1e-9)

    def test_apana_gc_only(self) -> None:
        """gc_freed_bytes_per_sec=5e6 → gc_norm=0.5, outbound_norm=0 → apana=0.25."""
        m = self.collector.collect({"gc_freed_bytes_per_sec": 5_000_000.0})
        assert m.apana == pytest.approx(0.25, abs=1e-9)

    def test_apana_both_components_at_ceiling(self) -> None:
        """outbound=60 + gc=1e7 → both norms=1.0 → apana = min(1.0, (1.0+1.0)/2) = 1.0."""
        m = self.collector.collect(
            {
        "nats_outbound_rate_hz": 60.0,
        "gc_freed_bytes_per_sec": 1e7,
        }
        )
        assert m.apana == pytest.approx(1.0, abs=1e-9)

    def test_apana_above_ceiling_clamped(self) -> None:
        """Values well above ceiling must still clamp apana to 1.0."""
        m = self.collector.collect(
            {
        "nats_outbound_rate_hz": 9999.0,
        "gc_freed_bytes_per_sec": 9e10,
        }
        )
        assert m.apana == pytest.approx(1.0, abs=1e-9)

    # ── samāna (extraction quality) ────────────────────────────────────────

    def test_samana_passthrough(self) -> None:
        """extraction_quality=0.75 → samana=0.75 (no transformation)."""
        m = self.collector.collect({"extraction_quality": 0.75})
        assert m.samana == pytest.approx(0.75, abs=1e-9)

    def test_samana_zero_when_absent(self) -> None:
        """Missing extraction_quality defaults to 0.0."""
        m = self.collector.collect({})
        assert m.samana == pytest.approx(0.0, abs=1e-9)

    def test_samana_clamped_if_above_one(self) -> None:
        """extraction_quality > 1.0 must be clamped to 1.0 (defensive clamp)."""
        m = self.collector.collect({"extraction_quality": 1.5})
        assert m.samana == pytest.approx(1.0, abs=1e-9)

    # ── udāna (HITL escalation — inverted) ────────────────────────────────

    def test_udana_zero_escalations_gives_one(self) -> None:
        """hitl_escalations_per_hour=0 → udana = 1 - 0 = 1.0 (healthy)."""
        m = self.collector.collect({"hitl_escalations_per_hour": 0.0})
        assert m.udana == pytest.approx(1.0, abs=1e-9)

    def test_udana_half_ceiling(self) -> None:
        """hitl_escalations_per_hour=2.5 → udana = 1 - 2.5/5 = 0.5."""
        m = self.collector.collect({"hitl_escalations_per_hour": 2.5})
        assert m.udana == pytest.approx(0.5, abs=1e-9)

    def test_udana_at_ceiling_gives_zero(self) -> None:
        """hitl_escalations_per_hour=5 → udana = 1 - 1.0 = 0.0 (exhausted)."""
        m = self.collector.collect({"hitl_escalations_per_hour": 5.0})
        assert m.udana == pytest.approx(0.0, abs=1e-9)

    def test_udana_above_ceiling_clamped_to_zero(self) -> None:
        """hitl_escalations_per_hour=99 → udana = 0.0 (not negative)."""
        m = self.collector.collect({"hitl_escalations_per_hour": 99.0})
        assert m.udana == pytest.approx(0.0, abs=1e-9)

    def test_udana_absent_defaults_to_one(self) -> None:
        """Missing hitl_escalations_per_hour → escalations=0 → udana=1.0."""
        m = self.collector.collect({})
        assert m.udana == pytest.approx(1.0, abs=1e-9)

    # ── vyāna (inter-agent comms) ──────────────────────────────────────────

    def test_vyana_half_ceiling(self) -> None:
        """nats_cross_subject_rate_hz=15 → vyana = 15/30 = 0.5."""
        m = self.collector.collect({"nats_cross_subject_rate_hz": 15.0})
        assert m.vyana == pytest.approx(0.5, abs=1e-9)

    def test_vyana_at_ceiling(self) -> None:
        """nats_cross_subject_rate_hz=30 → vyana = 1.0."""
        m = self.collector.collect({"nats_cross_subject_rate_hz": 30.0})
        assert m.vyana == pytest.approx(1.0, abs=1e-9)

    def test_vyana_above_ceiling_clamped(self) -> None:
        """nats_cross_subject_rate_hz=300 → vyana clamped to 1.0."""
        m = self.collector.collect({"nats_cross_subject_rate_hz": 300.0})
        assert m.vyana == pytest.approx(1.0, abs=1e-9)

    def test_vyana_zero_when_absent(self) -> None:
        """Missing nats_cross_subject_rate_hz → vyana = 0.0."""
        m = self.collector.collect({})
        assert m.vyana == pytest.approx(0.0, abs=1e-9)

    # ── full-pipeline smoke ────────────────────────────────────────────────

    def test_full_pipeline_typical_workload(self) -> None:
        """Typical mid-load metrics produce a sensible aggregate score."""
        raw = {
        "nats_inbound_rate_hz": 30.0,  # prana  = 0.5
        "nats_outbound_rate_hz": 12.0,  # apana  outbound_norm = 0.2
        "gc_freed_bytes_per_sec": 4_000_000.0,  # apana gc_norm = 0.4 → (0.2+0.4)/2 = 0.3
        "extraction_quality": 0.80,  # samana = 0.8
        "hitl_escalations_per_hour": 1.0,  # udana  = 1 - 1/5 = 0.8
        "nats_cross_subject_rate_hz": 15.0,  # vyana  = 0.5
        }
        m = self.collector.collect(raw)
        pranayama = compute_pranayama(m)

        # Expected: 0.25*0.5 + 0.15*0.3 + 0.25*0.8 + 0.15*0.8 + 0.20*0.5
        #  = 0.125 + 0.045 + 0.200 + 0.120 + 0.100 = 0.590
        expected = 0.25 * 0.5 + 0.15 * 0.3 + 0.25 * 0.8 + 0.15 * 0.8 + 0.20 * 0.5
        assert pranayama == pytest.approx(expected, abs=1e-9)

    def test_empty_raw_dict_gives_safe_result(self) -> None:
        """Empty raw dict (all metrics absent) must not raise and udana=1.0."""
        m = self.collector.collect({})
        # prana=0, apana=0, samana=0, udana=1 (no escalations), vyana=0
        assert m.prana == pytest.approx(0.0)
        assert m.udana == pytest.approx(1.0)
        pranayama = compute_pranayama(m)
        # Only udana contributes (weight 0.15)
        assert pranayama == pytest.approx(0.15, abs=1e-9)


# ---------------------------------------------------------------------------
# Test 13–14: emit_prana_pulse_event
# ---------------------------------------------------------------------------


class TestEmitPranaPulseEvent:
    """emit_prana_pulse_event must return an SSE-ready dict with correct keys."""

    def setup_method(self) -> None:
        self.metrics = PancaPranaMetrics(prana=0.5, apana=0.3, samana=0.8, udana=0.8, vyana=0.5)
        self.pranayama = compute_pranayama(self.metrics)
        self.payload = emit_prana_pulse_event(self.metrics, self.pranayama)

    def test_event_type_is_prana_pulse(self) -> None:
        """The 'event' key must equal 'PRANA_PULSE'."""
        assert self.payload["event"] == "PRANA_PULSE"

    def test_payload_contains_required_keys(self) -> None:
        """Payload must contain: event, timestamp, sub_airs, pranayama, weights."""
        for key in ("event", "timestamp", "sub_airs", "pranayama", "weights"):
            assert key in self.payload, f"Missing key: {key!r}"

    def test_sub_airs_contains_all_five(self) -> None:
        """sub_airs dict must contain all five sub-air keys."""
        sub_airs = self.payload["sub_airs"]
        for key in ("prana", "apana", "samana", "udana", "vyana"):
            assert key in sub_airs, f"Missing sub-air: {key!r}"

    def test_sub_airs_values_match_metrics(self) -> None:
        """sub_airs values must round-trip from the input PancaPranaMetrics."""
        sub_airs = self.payload["sub_airs"]
        assert sub_airs["prana"] == pytest.approx(0.5, abs=1e-4)
        assert sub_airs["samana"] == pytest.approx(0.8, abs=1e-4)

    def test_pranayama_value_in_payload_matches_aggregate(self) -> None:
        """'pranayama' key must match the value passed to the function."""
        assert self.payload["pranayama"] == pytest.approx(self.pranayama, abs=1e-4)

    def test_weights_in_payload_match_prana_weights_constant(self) -> None:
        """'weights' dict in payload must equal PRANA_WEIGHTS."""
        assert self.payload["weights"] == PRANA_WEIGHTS

    def test_timestamp_is_numeric(self) -> None:
        """'timestamp' must be a positive float (Unix seconds)."""
        ts = self.payload["timestamp"]
        assert isinstance(ts, float)
        assert ts > 0.0

    def test_all_ones_payload_pranayama_is_one(self) -> None:
        """With all sub-airs = 1.0, emitted pranayama must round to 1.0."""
        metrics = _all_ones()
        p = compute_pranayama(metrics)
        payload = emit_prana_pulse_event(metrics, p)
        assert payload["pranayama"] == pytest.approx(1.0, abs=1e-4)

    def test_all_zeros_payload_pranayama_is_zero(self) -> None:
        """With all sub-airs = 0.0, emitted pranayama must round to 0.0."""
        metrics = _all_zeros()
        p = compute_pranayama(metrics)
        payload = emit_prana_pulse_event(metrics, p)
        assert payload["pranayama"] == pytest.approx(0.0, abs=1e-4)
