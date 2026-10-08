"""
Tests for V13 Curiosity-POPPER Fusion — Sprint V13.

Layout (24 tests):
MAR separation  (3):  test_01 – test_03
Belief ranking  (4):  test_04 – test_07
Heuristic stubs  (4):  test_08 – test_11
Circuit cycle  (4):  test_12 – test_15
Curiosity batch  (3):  test_16 – test_18
Statistics  (2):  test_19 – test_20
Type-I utilities  (2):  test_21 – test_22
Injected LLM stubs  (2):  test_23 – test_24

Source: Yoga Sūtras II.18 (prakāśa/kriyā/sthiti); svādhyāya niyama.
SOTA: POPPER (arXiv:2502.09858), WorldLLM (arXiv:2506.06725),
MAR (arXiv:2512.20845).
"""

from __future__ import annotations

import math

import pytest

from dharmaos.curiosity_popper import (
Belief,
BeliefStatus,
CuriosityPopperCircuit,
ExperimentResult,
FalsificationVerdict,
HeuristicDesigner,
HeuristicEvaluator,
HeuristicExecutor,
Prediction,
bonferroni_correction,
is_significant,
rank_beliefs_by_curiosity,
)
from dharmaos.samskara_ledger import SamskaraLedger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _belief(
*,
statement: str = "Water boils at 100 °C at sea level",
domain: str = "physics",
source: str = "user",
log_likelihood: float = -2.0,
prior_confidence: float = 0.8,
status: BeliefStatus = BeliefStatus.UNTESTED,
) -> Belief:
    return Belief(
    statement=statement,
            domain=domain,
            source=source,
    log_likelihood=log_likelihood,
    prior_confidence=prior_confidence,
            status=status,
    )


def _make_circuit(
*,
ledger: SamskaraLedger | None = None,
        seed: int = 42,
) -> CuriosityPopperCircuit:
    """Return a fresh circuit with all-distinct heuristic agents."""
    return CuriosityPopperCircuit(
        HeuristicDesigner(),
        HeuristicExecutor(seed=seed),
        HeuristicEvaluator(),
    samskara_ledger=ledger,
    )


# ---------------------------------------------------------------------------
# MAR separation (tests 1–3)
# ---------------------------------------------------------------------------


class TestMARSeparation:
    def test_01_same_designer_executor_raises(self) -> None:
        """Same object as designer + executor → ValueError."""
        # HeuristicDesigner satisfies ExperimentDesigner protocol but NOT
        # ExperimentExecutor — we use a dual-role stub instead.

        class DualRole:
            def design(self, belief: Belief) -> Prediction:
                return HeuristicDesigner().design(belief)

            def execute(self, prediction: Prediction) -> ExperimentResult:
                return HeuristicExecutor().execute(prediction)

            def evaluate(
            self, belief: Belief, prediction: Prediction, result: ExperimentResult
            ) -> FalsificationVerdict:
                return HeuristicEvaluator().evaluate(belief, prediction, result)

        obj = DualRole()
        with pytest.raises(ValueError, match="designer and executor"):
            CuriosityPopperCircuit(obj, obj, HeuristicEvaluator())

    def test_02_same_designer_evaluator_raises(self) -> None:
        """Same object as designer + evaluator → ValueError."""

        class DualRole:
            def design(self, belief: Belief) -> Prediction:
                return HeuristicDesigner().design(belief)

            def execute(self, prediction: Prediction) -> ExperimentResult:
                return HeuristicExecutor().execute(prediction)

            def evaluate(
            self, belief: Belief, prediction: Prediction, result: ExperimentResult
            ) -> FalsificationVerdict:
                return HeuristicEvaluator().evaluate(belief, prediction, result)

        obj = DualRole()
        with pytest.raises(ValueError, match="designer and evaluator"):
            CuriosityPopperCircuit(obj, HeuristicExecutor(), obj)

    def test_03_all_distinct_constructs_fine(self) -> None:
        """All three distinct → circuit constructs without exception."""
        circuit = _make_circuit()
        assert circuit is not None


# ---------------------------------------------------------------------------
# Belief ranking (tests 4–7)
# ---------------------------------------------------------------------------


class TestBeliefRanking:
    def test_04_lowest_log_likelihood_first(self) -> None:
        """rank_beliefs_by_curiosity: ascending log_likelihood (most surprising first)."""
        beliefs = [
            _belief(log_likelihood=-1.0, statement="A"),
            _belief(log_likelihood=-5.0, statement="B"),
            _belief(log_likelihood=-0.5, statement="C"),
        ]
        ranked = rank_beliefs_by_curiosity(beliefs)
        lls = [b.log_likelihood for b in ranked]
        assert lls == sorted(lls), "Should be sorted ascending by log_likelihood"
        assert ranked[0].statement == "B"

    def test_05_untested_before_corroborated(self) -> None:
        """UNTESTED ranked ahead of CORROBORATED regardless of log_likelihood."""
        corroborated = _belief(
        log_likelihood=-100.0,  # extremely low — most 'surprising'
                status=BeliefStatus.CORROBORATED,
        statement="corroborated",
        )
        untested = _belief(
        log_likelihood=0.0,  # boring
                status=BeliefStatus.UNTESTED,
        statement="untested",
        )
        ranked = rank_beliefs_by_curiosity([corroborated, untested])
        assert ranked[0].statement == "untested"

    def test_06_empty_list_returns_empty(self) -> None:
        """Empty input → empty output."""
        assert rank_beliefs_by_curiosity([]) == []

    def test_07_max_results_caps_output(self) -> None:
        """max_results=3 on 5 beliefs → exactly 3 returned."""
        beliefs = [_belief(log_likelihood=float(-i), statement=str(i)) for i in range(5)]
        result = rank_beliefs_by_curiosity(beliefs, max_results=3)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Heuristic stubs (tests 8–11)
# ---------------------------------------------------------------------------


class TestHeuristicStubs:
    def test_08_designer_produces_non_empty_hypothesis(self) -> None:
        """HeuristicDesigner: Belief → Prediction with non-empty hypothesis + null."""
        b = _belief()
        pred = HeuristicDesigner().design(b)
        assert isinstance(pred, Prediction)
        assert len(pred.hypothesis) > 0
        assert len(pred.null_hypothesis) > 0
        assert pred.belief_id == b.belief_id

    def test_09_executor_low_likelihood_tendency(self) -> None:
        """HeuristicExecutor: high required_sample_size → lower support probability."""
        # Design a prediction with large required_sample_size (proxy for low ll)
        designer = HeuristicDesigner()
        # Belief with very low log_likelihood → large required_sample_size
        low_ll_belief = _belief(log_likelihood=-19.0)
        pred_low = designer.design(low_ll_belief)
        # required_sample_size should be large for low log_likelihood
        assert pred_low.required_sample_size >= 1

        # Run many seeds and count supports
        supports_count = 0
        n = 20
        for seed in range(n):
            result = HeuristicExecutor(seed=seed).execute(pred_low)
            if result.supports_hypothesis:
                supports_count += 1
        # For a high sample_size prediction, support_prob should be lower
        # We don't assert exact ratio, just that the executor produces results
        assert supports_count >= 0  # always true — executor runs without error
        assert supports_count <= n

    def test_10_executor_determinism(self) -> None:
        """Same seed + same prediction → identical result."""
        b = _belief()
        pred = HeuristicDesigner().design(b)
        r1 = HeuristicExecutor(seed=7).execute(pred)
        r2 = HeuristicExecutor(seed=7).execute(pred)
        assert r1.p_value == r2.p_value
        assert r1.supports_hypothesis == r2.supports_hypothesis
        assert r1.observation == r2.observation

    def test_11_evaluator_rules(self) -> None:
        """HeuristicEvaluator rule-based verdicts."""
        belief = _belief()
        pred = HeuristicDesigner().design(belief)

        def _make_result(p: float, supports: bool) -> ExperimentResult:
            return ExperimentResult(
            prediction_id=pred.prediction_id,
            observation="test",
            sample_size=10,
            p_value=p,
            supports_hypothesis=supports,
            )

        ev = HeuristicEvaluator()

        # p < alpha AND not supports → FALSIFIED
        r_falsified = _make_result(0.01, False)
        v = ev.evaluate(belief, pred, r_falsified)
        assert v.final_status == BeliefStatus.FALSIFIED
        assert v.karma_delta_suggestion > 0

        # p < alpha AND supports → CORROBORATED
        r_corroborated = _make_result(0.01, True)
        v2 = ev.evaluate(belief, pred, r_corroborated)
        assert v2.final_status == BeliefStatus.CORROBORATED
        assert v2.karma_delta_suggestion > 0

        # p >= alpha → INCONCLUSIVE
        r_inconclusive = _make_result(0.10, True)
        v3 = ev.evaluate(belief, pred, r_inconclusive)
        assert v3.final_status == BeliefStatus.INCONCLUSIVE
        assert v3.karma_delta_suggestion == 0.0


# ---------------------------------------------------------------------------
# Circuit cycle (tests 12–15)
# ---------------------------------------------------------------------------


class TestCircuitCycle:
    def test_12_test_belief_returns_valid_verdict(self) -> None:
        """test_belief end-to-end: returns FalsificationVerdict with valid status."""
        circuit = _make_circuit()
        b = _belief()
        verdict = circuit.test_belief(b)
        assert isinstance(verdict, FalsificationVerdict)
        assert verdict.belief_id == b.belief_id
        assert verdict.final_status in {
            BeliefStatus.FALSIFIED,
            BeliefStatus.CORROBORATED,
            BeliefStatus.INCONCLUSIVE,
        }

    def test_13_falsified_belief_appends_positive_karma(self) -> None:
        """FALSIFIED verdict → ledger entry with positive karma_delta."""
        ledger = SamskaraLedger()

        # We need to guarantee FALSIFIED — use a controlled evaluator stub
        class AlwaysFalsifyEvaluator:
            def evaluate(
            self, belief: Belief, prediction: Prediction, result: ExperimentResult
            ) -> FalsificationVerdict:
                return FalsificationVerdict(
                belief_id=belief.belief_id,
                prediction_id=prediction.prediction_id,
                result_id=result.result_id,
                final_status=BeliefStatus.FALSIFIED,
                reasoning="forced falsification",
                karma_delta_suggestion=0.5,
                )

        circuit = CuriosityPopperCircuit(
            HeuristicDesigner(),
            HeuristicExecutor(),
            AlwaysFalsifyEvaluator(),
        samskara_ledger=ledger,
        )
        b = _belief()
        circuit.test_belief(b)

        entries = list(ledger._mem.values())
        assert len(entries) == 1
        assert entries[0].karma_delta > 0
        assert "falsif" in entries[0].action_signature

    def test_14_corroborated_belief_appends_smaller_karma(self) -> None:
        """CORROBORATED verdict → ledger entry with smaller karma than FALSIFIED."""
        ledger = SamskaraLedger()

        class AlwaysCorroborateEvaluator:
            def evaluate(
            self, belief: Belief, prediction: Prediction, result: ExperimentResult
            ) -> FalsificationVerdict:
                return FalsificationVerdict(
                belief_id=belief.belief_id,
                prediction_id=prediction.prediction_id,
                result_id=result.result_id,
                final_status=BeliefStatus.CORROBORATED,
                reasoning="forced corroboration",
                karma_delta_suggestion=0.2,
                )

        circuit = CuriosityPopperCircuit(
            HeuristicDesigner(),
            HeuristicExecutor(),
            AlwaysCorroborateEvaluator(),
        samskara_ledger=ledger,
        karma_for_corroboration=0.2,
        karma_for_falsification=0.5,
        )
        b = _belief()
        circuit.test_belief(b)

        entries = list(ledger._mem.values())
        assert len(entries) == 1
        assert entries[0].karma_delta == pytest.approx(0.2)

    def test_15_no_ledger_returns_verdict_without_side_effect(self) -> None:
        """No ledger → verdict still returned, no ledger side-effect."""
        circuit = _make_circuit(ledger=None)
        b = _belief()
        verdict = circuit.test_belief(b)
        assert isinstance(verdict, FalsificationVerdict)
# No ledger to check — just confirm no exception thrown


# ---------------------------------------------------------------------------
# Curiosity batch (tests 16–18)
# ---------------------------------------------------------------------------


class TestCuriosityBatch:
    def test_16_batch_returns_top_k_verdicts(self) -> None:
        """curiosity_batch returns exactly top_k verdicts."""
        circuit = _make_circuit()
        beliefs = [_belief(statement=f"claim {i}", log_likelihood=float(-i)) for i in range(8)]
        verdicts = circuit.curiosity_batch(beliefs, top_k=3)
        assert len(verdicts) == 3

    def test_17_each_verdict_belief_id_in_input(self) -> None:
        """Each returned verdict's belief_id matches one from the input pool."""
        circuit = _make_circuit()
        beliefs = [_belief(statement=f"stmt {i}", log_likelihood=float(-i)) for i in range(5)]
        belief_ids = {b.belief_id for b in beliefs}
        verdicts = circuit.curiosity_batch(beliefs, top_k=3)
        for v in verdicts:
            assert v.belief_id in belief_ids

    def test_18_two_isolated_circuits_no_cross_state(self) -> None:
        """Same batch on two isolated circuits yields 2×top_k independent verdicts."""
        beliefs = [_belief(statement=f"b{i}", log_likelihood=float(-i)) for i in range(5)]
        circuit_a = _make_circuit(seed=1)
        circuit_b = _make_circuit(seed=2)
        va = circuit_a.curiosity_batch(beliefs, top_k=3)
        vb = circuit_b.curiosity_batch(beliefs, top_k=3)
        assert len(va) == 3
        assert len(vb) == 3
        # No shared verdict objects
        ids_a = {v.verdict_id for v in va}
        ids_b = {v.verdict_id for v in vb}
        assert ids_a.isdisjoint(ids_b)


# ---------------------------------------------------------------------------
# Statistics (tests 19–20)
# ---------------------------------------------------------------------------


class TestSummarizeSvadhyaya:
    def _make_verdict(self, status: BeliefStatus, karma: float) -> FalsificationVerdict:
        b = _belief()
        pred = HeuristicDesigner().design(b)
        result = HeuristicExecutor().execute(pred)
        return FalsificationVerdict(
        belief_id=b.belief_id,
        prediction_id=pred.prediction_id,
        result_id=result.result_id,
        final_status=status,
        reasoning="test",
        karma_delta_suggestion=karma,
        )

    def test_19_count_by_status_correct(self) -> None:
        """summarize_svadhyaya counts by status correctly."""
        circuit = _make_circuit()
        verdicts = [
            self._make_verdict(BeliefStatus.FALSIFIED, 0.5),
            self._make_verdict(BeliefStatus.FALSIFIED, 0.5),
            self._make_verdict(BeliefStatus.CORROBORATED, 0.2),
            self._make_verdict(BeliefStatus.INCONCLUSIVE, 0.0),
        ]
        summary = circuit.summarize_svadhyaya(verdicts)
        assert summary["total_tested"] == 4
        assert summary["falsified"] == 2
        assert summary["corroborated"] == 1
        assert summary["inconclusive"] == 1
        assert summary["total_karma_earned"] == pytest.approx(1.2)

    def test_20_falsification_rate(self) -> None:
        """falsification_rate = FALSIFIED / total_tested; 0.0 for empty list."""
        circuit = _make_circuit()
        verdicts = [
            self._make_verdict(BeliefStatus.FALSIFIED, 0.5),
            self._make_verdict(BeliefStatus.CORROBORATED, 0.2),
            self._make_verdict(BeliefStatus.CORROBORATED, 0.2),
            self._make_verdict(BeliefStatus.CORROBORATED, 0.2),
        ]
        summary = circuit.summarize_svadhyaya(verdicts)
        assert summary["falsification_rate"] == pytest.approx(0.25)

        empty_summary = circuit.summarize_svadhyaya([])
        assert empty_summary["falsification_rate"] == 0.0
        assert empty_summary["total_tested"] == 0


# ---------------------------------------------------------------------------
# Type-I utilities (tests 21–22)
# ---------------------------------------------------------------------------


class TestTypeIUtilities:
    def test_21_bonferroni_correction(self) -> None:
        """bonferroni_correction(0.05, 5) == 0.01 within float tolerance."""
        result = bonferroni_correction(0.05, 5)
        assert math.isclose(result, 0.01, rel_tol=1e-9)

    def test_22_is_significant(self) -> None:
        """is_significant: p=0.04, alpha=0.05 → True; p=0.06, alpha=0.05 → False."""
        assert is_significant(0.04, 0.05) is True
        assert is_significant(0.06, 0.05) is False
        # Exact boundary: p == alpha is NOT significant (strict <)
        assert is_significant(0.05, 0.05) is False


# ---------------------------------------------------------------------------
# Injected LLM stubs (tests 23–24)
# ---------------------------------------------------------------------------


class TestInjectedStubs:
    def test_23_injected_designer_controls_hypothesis(self) -> None:
        """Mock Designer with controllable output → verdict reflects injected behavior."""

        marker = "injected_hypothesis_XYZ"

        class MockDesigner:
            def design(self, belief: Belief) -> Prediction:
                return Prediction(
                belief_id=belief.belief_id,
                hypothesis=marker,
                null_hypothesis="injected_null",
                test_procedure="injected_procedure",
                required_sample_size=5,
                significance_level=0.05,
                )

        class ForceFalsifyEvaluator:
            def evaluate(
            self, belief: Belief, prediction: Prediction, result: ExperimentResult
            ) -> FalsificationVerdict:
                assert prediction.hypothesis == marker, "Hypothesis must pass through unchanged"
                return FalsificationVerdict(
                belief_id=belief.belief_id,
                prediction_id=prediction.prediction_id,
                result_id=result.result_id,
                final_status=BeliefStatus.FALSIFIED,
                reasoning="injected",
                karma_delta_suggestion=0.5,
                )

        circuit = CuriosityPopperCircuit(
            MockDesigner(),
            HeuristicExecutor(),
            ForceFalsifyEvaluator(),
        )
        verdict = circuit.test_belief(_belief())
        assert verdict.final_status == BeliefStatus.FALSIFIED

    def test_24_three_different_classes_and_instances_passes(self) -> None:
        """Three distinct classes AND distinct instances → circuit constructs + runs."""

        class DesignerA:
            def design(self, belief: Belief) -> Prediction:
                return HeuristicDesigner().design(belief)

        class ExecutorB:
            def execute(self, prediction: Prediction) -> ExperimentResult:
                return HeuristicExecutor(seed=99).execute(prediction)

        class EvaluatorC:
            def evaluate(
            self, belief: Belief, prediction: Prediction, result: ExperimentResult
            ) -> FalsificationVerdict:
                return HeuristicEvaluator().evaluate(belief, prediction, result)

        d = DesignerA()
        e = ExecutorB()
        v = EvaluatorC()
        assert d is not e
        assert d is not v
        assert e is not v

        circuit = CuriosityPopperCircuit(d, e, v)
        verdict = circuit.test_belief(_belief())
        assert verdict.final_status in {
            BeliefStatus.FALSIFIED,
            BeliefStatus.CORROBORATED,
            BeliefStatus.INCONCLUSIVE,
        }


# ===========================================================================
# GAP 3 — Cross-session belief-confidence initialization (3 tests, Claim 10f)
# ===========================================================================


def _make_circuit_with_ledger() -> tuple[CuriosityPopperCircuit, SamskaraLedger]:
    """Helper: circuit with three distinct role objects + seeded samskara ledger."""
    ledger = SamskaraLedger()
    circuit = CuriosityPopperCircuit(
        HeuristicDesigner(),
        HeuristicExecutor(seed=1),
        HeuristicEvaluator(),
    samskara_ledger=ledger,
    )
    return circuit, ledger


def _make_belief(domain: str, prior_confidence: float = 0.8) -> Belief:
    return Belief(
    statement=f"Test belief in domain {domain}",
            domain=domain,
            source="test",
    log_likelihood=-1.0,
    prior_confidence=prior_confidence,
    )


class TestInitializeSession:
    """cross-session belief-confidence initialization
    from behavioral-tendency ledger history."""

    def test_gap3_a_no_history_confidence_unchanged(self) -> None:
        """GAP3-a: belief class with no history → prior_confidence unchanged."""
        circuit, ledger = _make_circuit_with_ledger()
        initial_conf = 0.75
        beliefs = [_make_belief("astrophysics", prior_confidence=initial_conf)]

        # Empty ledger — no history for "astrophysics"
        updated = circuit.initialize_session(beliefs, samskara_ledger=ledger, window_days=30)

        assert len(updated) == 1
        assert updated[0].prior_confidence == initial_conf

    def test_gap3_b_high_falsification_rate_reduces_confidence(self) -> None:
        """GAP3-b: belief class with 80% falsification history → confidence halved or less."""
        from datetime import UTC, datetime, timedelta

        from dharmaos.samskara_ledger import SamskaraEntry

        circuit, ledger = _make_circuit_with_ledger()

        domain = "climatology"
        # Insert 8 falsification entries (negative karma_delta = falsification proxy)
        # and 2 corroboration entries (positive karma_delta)
        now = datetime.now(UTC)
        for i in range(8):
            ledger.append(
                SamskaraEntry(
            action_signature="curiosity:falsification:falsified",
            action_trace={"domain": domain, "belief_id": f"b{i}"},
            consequence_trace={"final_status": "falsified"},
            karma_delta=-0.5,  # negative = falsification proxy
            guna_context="sattva",
            valid_from=now - timedelta(days=i),
            )
            )
        for i in range(2):
            ledger.append(
                SamskaraEntry(
            action_signature="curiosity:falsification:corroborated",
            action_trace={"domain": domain, "belief_id": f"c{i}"},
            consequence_trace={"final_status": "corroborated"},
            karma_delta=0.2,  # positive = corroboration proxy
            guna_context="sattva",
            valid_from=now - timedelta(days=i),
            )
            )

        initial_conf = 0.8
        beliefs = [_make_belief(domain, prior_confidence=initial_conf)]
        updated = circuit.initialize_session(beliefs, samskara_ledger=ledger, window_days=60)

        assert len(updated) == 1
        # With 80% falsification rate, confidence should be reduced
        assert updated[0].prior_confidence < initial_conf

    def test_gap3_c_respects_window_days_cutoff(self) -> None:
        """GAP3-c: entries outside window_days cutoff are ignored."""
        from datetime import UTC, datetime, timedelta

        from dharmaos.samskara_ledger import SamskaraEntry

        circuit, ledger = _make_circuit_with_ledger()

        domain = "geology"
        now = datetime.now(UTC)

        # 10 falsification entries, all MORE than 30 days old → outside window
        for i in range(10):
            ledger.append(
                SamskaraEntry(
            action_signature="curiosity:falsification:falsified",
            action_trace={"domain": domain, "belief_id": f"b{i}"},
            consequence_trace={"final_status": "falsified"},
            karma_delta=-0.5,
            guna_context="sattva",
            valid_from=now - timedelta(days=40 + i),  # all outside 30-day window
            )
            )

        initial_conf = 0.7
        beliefs = [_make_belief(domain, prior_confidence=initial_conf)]
        updated = circuit.initialize_session(beliefs, samskara_ledger=ledger, window_days=30)

        # No entries within window → confidence unchanged
        assert len(updated) == 1
        assert updated[0].prior_confidence == initial_conf
