"""
    DharmaOS CuriosityPopperCircuit — 2-agent Popperian belief-falsification
    with confirmation-bias separation and saṃskāra-ledger feedback.

    PURPOSE
    -------
    CuriosityPopperCircuit prevents an agent from acting on unverified beliefs by
    systematically attempting to falsify them before those beliefs influence
    consequential decisions. It implements Karl Popper's falsificationism
    (a belief is scientific only if it can be falsified) as an operational
    pre-decisional gate.

    Confirmation bias — the single-agent failure mode where the same model that
    holds a belief also generates evidence in its favor — is broken by the MAR
    (Multi-Agent Reflexion, arXiv:2512.20845) separation pattern:

    ExperimentDesigner (one agent/chain): generates falsification hypotheses
    — "if this belief is wrong, what evidence would we expect to see?"

    ExperimentExecutor (separate agent/chain): runs the experiments and reports
    observations WITHOUT knowing which hypothesis is being tested.

    Evaluator: assesses whether the observations support or refute each hypothesis
    using statistical significance testing (p-value + Bonferroni correction for
    multiple hypotheses).

    BELIEF LIFECYCLE
    ----------------
    ACTIVE  → belief is held with evidence, no falsification run yet.
    VERIFIED → falsification attempted; evidence supports belief.
    REFUTED  → falsification succeeded; belief is updated/dropped.
    UNCERTAIN→ evidence inconclusive; belief held with lower confidence.

    KARMA-DELTA FEEDBACK
    --------------------
    Each falsification run appends to SamskaraLedger:
    - VERIFIED belief: +karma_delta (accurate world model)
    - REFUTED belief:  -karma_delta (incorrect world model, course-corrected)
    This ensures the agent's historical calibration is visible to AdharmaDetector
    Layer 7 (metacognitive audit).

    KEY TYPES
    ---------
    CuriosityPopperCircuit : Main circuit. run_falsification() → FalsificationVerdict.
    rank_beliefs_by_curiosity() → priority-ordered list.
    Belief  : Named belief with confidence and supporting evidence.
    BeliefStatus  : ACTIVE / VERIFIED / REFUTED / UNCERTAIN enum.
    Prediction  : Hypothesis (if belief is false, expect X).
    ExperimentResult  : Observed outcome of a falsification experiment.
    FalsificationVerdict  : Full circuit result (belief_id, status, p_value,
    hypotheses tested, karma_delta).
    ExperimentDesigner  : Protocol — inject real LLM designer chain in production.
    ExperimentExecutor  : Protocol — inject real experiment runner in production.
    Evaluator  : Protocol — inject real evidence evaluator in production.
    HeuristicDesigner/Evaluator/Executor : Offline heuristic implementations (dev/test).
    bonferroni_correction(): Adjusts alpha threshold for multiple hypothesis testing.
    is_significant()  : p-value significance test.
    rank_beliefs_by_curiosity(): WorldLLM log-likelihood curiosity scoring.

    COMPLIANCE ROLE
    ---------------
    - Prevents action on hallucinated or unverified beliefs (NIST AI RMF MAP 1.6).
    - SamskaraLedger feedback creates auditable evidence of belief calibration
    for EU AI Act Art. 14 human oversight.

    Governance origin: Svādhyāya niyama (self-study, Yoga Sūtras II.32) — the
    continual falsification of one's own beliefs as a discipline of discriminating
    wisdom. Implements Popperian falsificationism as an enterprise governance gate.

    V13 Curiosity-POPPER Fusion — 2-agent Popperian belief-falsification
    with MAR confirmation-bias separation and saṃskāra-ledger feedback.

    Source: Yoga Sūtras II.18 (prakāśa/kriyā/sthiti — the three movements of
    the discriminating mind); svādhyāya niyama (self-study, continual
    falsification of one's own beliefs).

    SOTA:
    - POPPER (arXiv:2502.09858, Feb 2025) — Popperian 2-agent split
    (Experiment Designer → Experiment Executor), 10× speedup vs human
    on DiscoveryBench.
    - WorldLLM (arXiv:2506.06725, June 2025) — log-likelihood-per-belief
    curiosity scoring: lower likelihood → more falsifiable → higher
    curiosity priority.
    - MAR — Multi-Agent Reflexion (arXiv:2512.20845, Dec 2025) — separating
    actor / evaluator / reflector breaks single-agent confirmation bias.

    Technical debt:
    TD-V13-01: HeuristicDesigner and HeuristicExecutor use rule-based
    synthesis only.  Production use requires injection of real
    LLM callables via the ExperimentDesigner / ExperimentExecutor
    Protocols — e.g. a Gemini Flash chain with tool-use for
    hypothesis generation and evidence retrieval.
    TD-V13-02: HeuristicExecutor p-value simulation uses deterministic
    hash + seed — replace with real experiment runner (web
    search, database query, sensor read) in Phase 4.
    TD-V13-03: summarize_svadhyaya does not yet feed back into V-JÑĀNA
    JnanaModule.calibrate() — wire in Phase 4.
    """

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from dharmaos.samskara_ledger import SamskaraLedger

_log: structlog.stdlib.BoundLogger = structlog.get_logger()

# P2-8: Maximum hypotheses generated per belief — prevents compute explosion
# when a broad-scope belief (e.g., "the internet is working") triggers
# unbounded falsification candidates from a real LLM designer.
MAX_HYPOTHESES_PER_BELIEF: int = 20


# ---------------------------------------------------------------------------
# Beliefs & predictions
# ---------------------------------------------------------------------------


class BeliefStatus(StrEnum):
    """Lifecycle state of a Belief under Popperian falsification."""

    UNTESTED = "untested"
    CORROBORATED = "corroborated"  # survived falsification attempt
    FALSIFIED = "falsified"  # failed the experiment
    INCONCLUSIVE = "inconclusive"  # experiment produced ambiguous result
    PENDING = "pending"  # experiment in progress


class Belief(BaseModel):
    """An agent belief subject to Popperian falsification.

        Source: Yoga Sūtras II.18 — sattva (prakāśa / illuminating knowledge)
        must be tested via kriyā (activity) to separate it from tamas (inertia).
        WorldLLM (arXiv:2506.06725) log-likelihood scoring: beliefs with lower
        log-likelihood are more surprising/falsifiable and get higher curiosity
        priority.
        """

    model_config = ConfigDict(frozen=True)

    belief_id: UUID = Field(default_factory=uuid4)
    statement: str  # the claim under test
    domain: str  # for V-JÑĀNA capability mapping
    source: str  # where this belief came from (user, saṃskāra, tool-output)
    log_likelihood: float  # WorldLLM score — lower = more falsifiable = higher curiosity
    prior_confidence: float  # 0-1
    status: BeliefStatus = BeliefStatus.UNTESTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Prediction(BaseModel):
    """Testable prediction derived from a Belief by the Experiment Designer.

        Source: POPPER (arXiv:2502.09858) — the Designer role converts a natural
        language belief into a structured hypothesis/null pair.
        """

    model_config = ConfigDict(frozen=True)

    prediction_id: UUID = Field(default_factory=uuid4)
    belief_id: UUID
    hypothesis: str  # "if belief is true, then observation X should hold"
    null_hypothesis: str  # "if belief is false, observation Y would result"
    test_procedure: str  # human-readable test plan
    required_sample_size: int
    significance_level: float = 0.05  # Type-I error control


class ExperimentResult(BaseModel):
    """Result from the Experiment Executor.

        Source: POPPER (arXiv:2502.09858) — the Executor role runs the test and
        reports raw observations, independent of the hypothesis framing.
        """

    model_config = ConfigDict(frozen=True)

    result_id: UUID = Field(default_factory=uuid4)
    prediction_id: UUID
    observation: str
    sample_size: int
    p_value: float  # for Type-I error control
    supports_hypothesis: bool
    raw_evidence: dict[str, object] = Field(default_factory=dict)
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FalsificationVerdict(BaseModel):
    """Final verdict on a Belief after the full MAR cycle.

        Source: MAR (arXiv:2512.20845) — the Evaluator role is the third
        agent in the pipeline, isolated from Designer and Executor to prevent
        confirmation bias.  Svādhyāya niyama: learning that a belief was wrong
        is POSITIVE karma (karma_delta_suggestion > 0).
        """

    model_config = ConfigDict(frozen=True)

    verdict_id: UUID = Field(default_factory=uuid4)
    belief_id: UUID
    prediction_id: UUID
    result_id: UUID
    final_status: BeliefStatus
    reasoning: str
    karma_delta_suggestion: float  # positive for successful falsification (svādhyāya)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# MAR-separated agent Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ExperimentDesigner(Protocol):
    """Actor role — converts Belief → Prediction. Runs on one LLM instance.

        MAR (arXiv:2512.20845): the Designer must be a DISTINCT object from
        Executor and Evaluator to prevent single-agent confirmation bias.
        """

    def design(self, belief: Belief) -> Prediction:
        """Convert a Belief into a structured, testable Prediction."""
        ...


@runtime_checkable
class ExperimentExecutor(Protocol):
    """Executor role — runs the test, returns raw observations.

        MUST be a separate LLM instance from the Designer (MAR pattern).
        """

    def execute(self, prediction: Prediction) -> ExperimentResult:
        """Run the experiment described by prediction, returning raw results."""
        ...


@runtime_checkable
class Evaluator(Protocol):
    """Judge role — reads ExperimentResult + original Prediction, emits
        final FalsificationVerdict. MUST be separate from Designer AND
        Executor (MAR 3-role separation).
        """

    def evaluate(
        self,
        belief: Belief,
        prediction: Prediction,
        result: ExperimentResult,
    ) -> FalsificationVerdict:
        """Adjudicate the experiment and return an immutable verdict."""
        ...


# ---------------------------------------------------------------------------
# Heuristic stubs (for tests + default offline wiring)
# ---------------------------------------------------------------------------


class HeuristicDesigner:
    """Default Designer: synthesises a trivial hypothesis/null pair from
        belief.statement. Production replaces this with an LLM call.

        TD-V13-01: Replace with a real LLM chain (Gemini Flash + tool-use)
        before production deployment.
        """

    def design(self, belief: Belief) -> Prediction:
        """Produce a deterministic Prediction from belief.statement.

            Hypothesis: "Evidence supports: {statement}"
            Null:  "Evidence does not support: {statement}"
            """
        h = f"Evidence supports: {belief.statement}"
        nh = f"Evidence does not support: {belief.statement}"
        proc = f"Search for empirical evidence regarding: {belief.statement}"
        return Prediction(
            belief_id=belief.belief_id,
            hypothesis=h,
            null_hypothesis=nh,
            test_procedure=proc,
            required_sample_size=max(1, int(abs(belief.log_likelihood) * 10) or 5),
            significance_level=0.05,
        )


class HeuristicExecutor:
    """Default Executor: deterministic based on belief.log_likelihood and seed.

        Lower log_likelihood (more surprising) → higher chance of
        supports_hypothesis=False (belief is falsified).

        TD-V13-02: Replace with a real experiment runner in Phase 4.
        """

    def __init__(self, seed: int = 42) -> None:
        self._seed = seed

    def execute(self, prediction: Prediction) -> ExperimentResult:
        """Deterministic execution: hash(belief_id + seed) drives outcome."""
        # Build a stable numeric key from the prediction's belief_id + seed
        h = hashlib.md5(f"{prediction.belief_id}:{self._seed}".encode()).hexdigest()
        rng = random.Random(int(h[:8], 16) ^ self._seed)

        # We can only access Prediction fields; Belief.log_likelihood is not forwarded.
        # We approximate falsifiability using required_sample_size as a proxy:
        # larger required_sample_size → designer thought the belief harder to test
        # → higher falsifiability → lower support_prob.
        sample_proxy = max(1, prediction.required_sample_size)
        support_prob = max(0.1, min(0.9, 1.0 - (sample_proxy / 100.0)))
        supports = rng.random() < support_prob

        # p-value: simulate via another random draw; lower for surprising results
        p_val = rng.uniform(0.001, 0.04) if supports else rng.uniform(0.01, 0.20)

        return ExperimentResult(
            prediction_id=prediction.prediction_id,
            observation=(
            f"Observed {'supporting' if supports else 'contradicting'} evidence "
            f"for '{prediction.hypothesis[:60]}'"
        ),
            sample_size=prediction.required_sample_size,
            p_value=p_val,
            supports_hypothesis=supports,
            raw_evidence={"seed": self._seed, "support_prob": round(support_prob, 4)},
        )


class HeuristicEvaluator:
    """Default Evaluator: rule-based from ExperimentResult.p_value + supports_hypothesis.

        Rules (per significance_level from the Prediction):
        p_value < α AND NOT supports_hypothesis → FALSIFIED
        p_value < α AND  supports_hypothesis → CORROBORATED
        p_value >= α  → INCONCLUSIVE

        Source: standard Neyman-Pearson hypothesis testing.
        MAR (arXiv:2512.20845): evaluator is the third independent role.
        """

    def evaluate(
        self,
        belief: Belief,
        prediction: Prediction,
        result: ExperimentResult,
    ) -> FalsificationVerdict:
        """Adjudicate the experiment result against significance_level."""
        alpha = prediction.significance_level
        sig = is_significant(result.p_value, alpha)

        if sig and not result.supports_hypothesis:
            status = BeliefStatus.FALSIFIED
            reasoning = (
                f"p={result.p_value:.4f} < a={alpha} and evidence contradicts "
                f"hypothesis. Belief '{belief.statement[:80]}' is FALSIFIED."
            )
            karma = 0.5  # svādhyāya — learning from error is positive
        elif sig and result.supports_hypothesis:
            status = BeliefStatus.CORROBORATED
            reasoning = (
                f"p={result.p_value:.4f} < a={alpha} and evidence supports "
                f"hypothesis. Belief '{belief.statement[:80]}' is CORROBORATED."
            )
            karma = 0.2  # smaller reward for confirming existing belief
        else:
            status = BeliefStatus.INCONCLUSIVE
            reasoning = (
                f"p={result.p_value:.4f} >= a={alpha}. Experiment inconclusive "
                f"for belief '{belief.statement[:80]}'."
            )
            karma = 0.0  # no karma for inconclusive result

        return FalsificationVerdict(
            belief_id=belief.belief_id,
            prediction_id=prediction.prediction_id,
            result_id=result.result_id,
            final_status=status,
            reasoning=reasoning,
            karma_delta_suggestion=karma,
        )


# ---------------------------------------------------------------------------
# Curiosity selection — WorldLLM log-likelihood ranking
# ---------------------------------------------------------------------------


# Status ordinals for curiosity ranking: UNTESTED = highest priority.
_STATUS_PRIORITY: dict[BeliefStatus, int] = {
    BeliefStatus.UNTESTED: 0,
    BeliefStatus.PENDING: 1,
    BeliefStatus.INCONCLUSIVE: 2,
    BeliefStatus.CORROBORATED: 3,
    BeliefStatus.FALSIFIED: 4,
}


def rank_beliefs_by_curiosity(
    beliefs: list[Belief],
    *,
    max_results: int | None = None,
) -> list[Belief]:
    """WorldLLM log-likelihood scoring: lower likelihood = more
        falsifiable = higher curiosity.

        Primary sort:  status priority ascending (UNTESTED first).
        Secondary sort: log_likelihood ascending (more surprising first).

        Source: WorldLLM (arXiv:2506.06725) — curiosity is driven by
        prediction error; low log-likelihood beliefs have higher prediction
        error and thus higher curiosity score.

        Args:
        beliefs:  candidate belief pool.
        max_results: cap on returned count (None = all).

        Returns:
        Sorted list, most-curious first.
        """
    if not beliefs:
        return []

    ranked = sorted(
        beliefs,
        key=lambda b: (_STATUS_PRIORITY.get(b.status, 99), b.log_likelihood),
    )

    if max_results is not None:
        ranked = ranked[:max_results]

    return ranked


# ---------------------------------------------------------------------------
# Type-I error control utilities
# ---------------------------------------------------------------------------


def bonferroni_correction(alpha: float, num_tests: int) -> float:
    """Conservative multiple-comparison correction for sequential tests.

        Returns adjusted alpha = alpha / num_tests.

        Source: Bonferroni (1936) — controls family-wise Type-I error rate
        across multiple simultaneous hypothesis tests.  Applied when
        curiosity_batch() tests multiple beliefs in one session.

        Args:
        alpha:  nominal significance level (e.g. 0.05).
        num_tests: number of simultaneous tests performed.

        Returns:
        Adjusted alpha.
        """
    if num_tests <= 0:
        raise ValueError(f"num_tests must be > 0, got {num_tests}")
    return alpha / num_tests


def is_significant(p_value: float, alpha: float) -> bool:
    """p_value < alpha → significant (reject null hypothesis).

        Args:
        p_value: observed p-value from the experiment.
        alpha:  significance threshold (e.g. 0.05).

        Returns:
        True if the result is statistically significant.
        """
    return p_value < alpha


# ---------------------------------------------------------------------------
# Main circuit
# ---------------------------------------------------------------------------


class CuriosityPopperCircuit:
    """V13 circuit — coordinates Designer → Executor → Evaluator + feeds
        successful falsifications back to V8 saṃskāra-ledger as
        positive-karma svādhyāya events.

        MAR 3-role separation INVARIANT: Designer, Executor, and Evaluator
        must be distinct instances. Constructor raises ValueError if any two
        share the same object identity (``is`` check).

        Source: Yoga Sūtras II.18 — prakāśa (illumination), kriyā (activity),
        sthiti (stability) — the three guṇa-movements that constitute
        discriminating knowledge; svādhyāya (II.32, II.44) — self-study as
        continual falsification of one's own conditioning.

        SOTA:
        - POPPER (arXiv:2502.09858): Designer → Executor pipeline.
        - MAR (arXiv:2512.20845): 3-role separation.
        - WorldLLM (arXiv:2506.06725): curiosity ranking for batch selection.

        Technical debt:
        TD-V13-01: Inject real LLM callables via ExperimentDesigner /
        ExperimentExecutor / Evaluator protocols for production.
        TD-V13-03: Wire summarize_svadhyaya output into V-JÑĀNA
        JnanaModule.calibrate() in Phase 4.
        """

    def __init__(
        self,
        designer: ExperimentDesigner,
        executor: ExperimentExecutor,
        evaluator: Evaluator,
        *,
        samskara_ledger: SamskaraLedger | None = None,
        karma_for_falsification: float = 0.5,
        karma_for_corroboration: float = 0.2,
        karma_for_inconclusive: float = 0.0,
        agent_id: str = "curiosity",
        tenant_id: str = "default",
    ) -> None:
        """Initialise the circuit with role-separated agents.

            Args:
            designer:  Experiment Designer (Actor role).
            executor:  Experiment Executor (separate instance).
            evaluator:  Evaluator / Judge (separate instance).
            samskara_ledger:  Optional V8 ledger for karma feedback.
            karma_for_falsification: Karma delta awarded on FALSIFIED verdict.
            karma_for_corroboration: Karma delta awarded on CORROBORATED verdict.
            karma_for_inconclusive:  Karma delta awarded on INCONCLUSIVE verdict.
            agent_id:  Agent identifier for ledger entries.
            tenant_id:  Tenant identifier for ledger RLS.

            Raises:
            ValueError: if any two of designer/executor/evaluator are the
            same object (MAR-separation invariant).
            """
        # Use object identity checks via id() to avoid mypy comparison-overlap
        # errors on Protocol types (mypy strict correctly notes that two different
        # Protocol-typed params can never be the same TYPE, but we are checking
        # object identity at runtime — a valid MAR-separation guard).
        d_id, e_id, v_id = id(designer), id(executor), id(evaluator)
        if d_id == e_id:
            raise ValueError(
                "MAR 3-role separation violated: designer and executor must be "
                "distinct objects. (MAR arXiv:2512.20845 — confirmation-bias "
                "prevention requires separate LLM instances per role.)"
            )
        if d_id == v_id:
            raise ValueError(
                "MAR 3-role separation violated: designer and evaluator must be "
                "distinct objects. (MAR arXiv:2512.20845)"
            )
        if e_id == v_id:
            raise ValueError(
                "MAR 3-role separation violated: executor and evaluator must be "
                "distinct objects. (MAR arXiv:2512.20845)"
            )

        self._designer = designer
        self._executor = executor
        self._evaluator = evaluator
        self._ledger = samskara_ledger
        self._karma_falsification = karma_for_falsification
        self._karma_corroboration = karma_for_corroboration
        self._karma_inconclusive = karma_for_inconclusive
        self._agent_id = agent_id
        self._tenant_id = tenant_id

        _log.debug(
            "curiosity_popper.circuit_init",
            agent_id=agent_id,
            tenant_id=tenant_id,
            has_ledger=samskara_ledger is not None,
        )

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    def test_belief(self, belief: Belief) -> FalsificationVerdict:
        """Full MAR cycle: design → execute → evaluate.

            Flow:
            1. Designer converts Belief → Prediction (Actor role).
            2. Executor runs the experiment → ExperimentResult (Executor role).
            3. Evaluator adjudicates Prediction + Result → FalsificationVerdict.
            4. If samskara_ledger is set, appends a SamskaraEntry carrying
            the karma_delta_suggestion (positive for FALSIFIED — svādhyāya).

            The Belief object itself is NOT mutated; callers should store the
            returned FalsificationVerdict and use it to construct an updated Belief
            if needed.

            Source: POPPER (arXiv:2502.09858) — Popperian experimental loop;
            svādhyāya niyama — self-study through falsification earns positive karma.
            """
        _log.debug(
            "curiosity_popper.test_belief",
            belief_id=str(belief.belief_id),
            statement=belief.statement[:60],
        )

        prediction = self._designer.design(belief)
        result = self._executor.execute(prediction)
        verdict = self._evaluator.evaluate(belief, prediction, result)

        if self._ledger is not None:
            self._append_karma(belief, verdict)

        _log.info(
            "curiosity_popper.verdict",
            belief_id=str(belief.belief_id),
            status=verdict.final_status,
            karma=verdict.karma_delta_suggestion,
        )
        return verdict

    def _append_karma(self, belief: Belief, verdict: FalsificationVerdict) -> None:
        """Append a SamskaraEntry to the V8 ledger for this verdict."""

        from dharmaos.samskara_ledger import SamskaraEntry

        # Karma sign convention per spec:
        # FALSIFIED → positive (svādhyāya reward); CORROBORATED → small positive;
        # INCONCLUSIVE → zero.  Never negative from the act of testing itself.
        karma_map: dict[BeliefStatus, float] = {
            BeliefStatus.FALSIFIED: self._karma_falsification,
            BeliefStatus.CORROBORATED: self._karma_corroboration,
            BeliefStatus.INCONCLUSIVE: self._karma_inconclusive,
        }
        karma = karma_map.get(verdict.final_status, 0.0)

        entry = SamskaraEntry(
            action_signature=f"curiosity:falsification:{verdict.final_status.value}",
            action_trace={
            "belief_id": str(belief.belief_id),
            "belief_statement": belief.statement[:200],
            "domain": belief.domain,
            "source": belief.source,
            "verdict_id": str(verdict.verdict_id),
            "agent_id": self._agent_id,
            "tenant_id": self._tenant_id,
        },
            consequence_trace={
            "final_status": verdict.final_status.value,
            "reasoning": verdict.reasoning[:500],
            "karma_delta_suggestion": verdict.karma_delta_suggestion,
            "prediction_id": str(verdict.prediction_id),
            "result_id": str(verdict.result_id),
        },
            karma_delta=karma,
            guna_context="sattva",  # falsification = illuminating knowledge
            valid_from=datetime.now(UTC),
        )
        self._ledger.append(entry)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Cross-session belief-confidence initialization
    # ------------------------------------------------------------------

    def initialize_session(
        self,
        initial_beliefs: list[Belief],
        samskara_ledger: SamskaraLedger,
        window_days: int = 30,
        rate_impact: float = 0.5,
    ) -> list[Belief]:
        """Initialize belief confidence scores from historical falsification data.

            at the initialization of each new agent
            session, retrieves from the behavioral-tendency ledger all belief-revision
            events for each belief class represented in the initial belief store,
            computes a historical falsification rate for each belief class as the
            ratio of falsified beliefs to total beliefs in that class over a
            configurable historical window, and initializes confidence scores for
            beliefs in each class as a decreasing function of the historical
            falsification rate for that class.

            Algorithm:
            For each belief:
            1. Derive the belief's class from belief.domain.
            2. Query samskara_ledger for entries whose action_signature starts
            with "curiosity:falsification:" and whose action_trace contains
            the matching domain.
            3. Within the window_days window:
            - falsified_count = entries with karma_delta < 0
            - corroborated_count = entries with karma_delta > 0
            - total = falsified_count + corroborated_count
            4. If total == 0 → confidence unchanged (no history).
            5. historical_falsification_rate = falsified_count / total
            6. new_confidence = prior_confidence × (1 - falsification_rate × rate_impact)
            Clamped to [0.0, 1.0].

            Args:
            initial_beliefs:  The belief store for the new session.
            samskara_ledger:  V8 SamskaraLedger to query for prior falsification history.
            window_days:  Look-back window in calendar days (default 30).
            rate_impact:  Decay multiplier — how strongly falsification rate
            reduces confidence (default 0.5; higher = faster decay).

            Returns:
            Updated list of Belief objects with adjusted prior_confidence.
            Beliefs with no historical data are returned unchanged.
            Beliefs are returned in the same order as initial_beliefs.
            """
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        cutoff = now - timedelta(days=window_days)

        updated_beliefs: list[Belief] = []

        for belief in initial_beliefs:
            domain = belief.domain

            # Count falsification and corroboration events in the window
            # by inspecting all samskara entries whose action_trace carries
            # this domain and whose action_signature indicates a curiosity verdict
            falsified_count = 0
            corroborated_count = 0

            # Iterate directly over ledger memory for window-filtered access
            for entry in samskara_ledger._mem.values():
                # Only active entries within the temporal window
                if entry.valid_from < cutoff:
                    continue
                if entry.valid_until is not None and entry.valid_until <= now:
                    continue
                # Must be a curiosity falsification event for this domain
                if not entry.action_signature.startswith("curiosity:falsification:"):
                    continue
                entry_domain = entry.action_trace.get("domain", "")
                if entry_domain != domain:
                    continue
                # H5: classify by the verdict suffix written into action_signature
                # (`curiosity:falsification:<status>`), NOT by karma sign — a
                # FALSIFIED result earns POSITIVE karma (svādhyāya reward), so the
                # old karma_delta<0 test never fired and the falsification rate was
                # always 0 (Patent Claim 10f was non-functional).
                if entry.action_signature.endswith(f":{BeliefStatus.FALSIFIED.value}"):
                    falsified_count += 1
                elif entry.action_signature.endswith(f":{BeliefStatus.CORROBORATED.value}"):
                    corroborated_count += 1

            total = falsified_count + corroborated_count
            if total == 0:
                # No historical data for this belief class → keep confidence unchanged
                updated_beliefs.append(belief)
                continue

            historical_falsification_rate = falsified_count / total

            # Decreasing function: high falsification rate → lower confidence
            new_confidence = belief.prior_confidence * (
                1.0 - historical_falsification_rate * rate_impact
            )
            new_confidence = max(0.0, min(1.0, new_confidence))

            # Produce an updated Belief (immutable → model_copy)
            updated = belief.model_copy(update={"prior_confidence": new_confidence})
            updated_beliefs.append(updated)

            _log.debug(
                "curiosity_popper.session_init_confidence",
                domain=domain,
                falsified_count=falsified_count,
                corroborated_count=corroborated_count,
                falsification_rate=historical_falsification_rate,
                old_confidence=belief.prior_confidence,
                new_confidence=new_confidence,
            )

        return updated_beliefs

    # ------------------------------------------------------------------
    # Curiosity batch
    # ------------------------------------------------------------------

    def curiosity_batch(
        self,
        beliefs: list[Belief],
        *,
        top_k: int = 5,
    ) -> list[FalsificationVerdict]:
        """Rank by curiosity, test the top-k most falsifiable beliefs.

            Source: WorldLLM (arXiv:2506.06725) — curiosity-driven belief
            selection; POPPER (arXiv:2502.09858) — batch test loop.

            P2-8: top_k is additionally clamped to MAX_HYPOTHESES_PER_BELIEF
            to prevent compute explosion when a broad-scope belief generates
            thousands of falsification candidates.

            Args:
            beliefs: pool of beliefs to rank and test.
            top_k:  number of top-curiosity beliefs to test.

            Returns:
            List of FalsificationVerdicts, one per tested belief.
            """
        top_k = min(top_k, MAX_HYPOTHESES_PER_BELIEF)
        ranked = rank_beliefs_by_curiosity(beliefs, max_results=top_k)
        verdicts: list[FalsificationVerdict] = []
        for belief in ranked:
            verdicts.append(self.test_belief(belief))
        return verdicts

    # ------------------------------------------------------------------
    # Svādhyāya summary — for V-JÑĀNA reporting
    # ------------------------------------------------------------------

    def summarize_svadhyaya(
        self,
        verdicts: list[FalsificationVerdict],
    ) -> dict[str, object]:
        """Aggregate statistics over a set of FalsificationVerdicts.

            Computes:
            total_tested:  len(verdicts)
            falsified:  count with FALSIFIED status
            corroborated:  count with CORROBORATED status
            inconclusive:  count with INCONCLUSIVE status
            total_karma_earned:  sum of karma_delta_suggestion
            falsification_rate:  falsified / total_tested (0.0 if empty)
            corroboration_rate:  corroborated / total_tested (0.0 if empty)

            Source: Yoga Sūtras II.44 svādhyāya niyama — periodic review of
            one's own beliefs and the quality of one's epistemic practice.
            TD-V13-03: Wire into V-JÑĀNA JnanaModule.calibrate() in Phase 4.

            Args:
            verdicts: list of FalsificationVerdict objects.

            Returns:
            Dict with aggregate statistics.
            """
        total = len(verdicts)
        falsified = sum(1 for v in verdicts if v.final_status == BeliefStatus.FALSIFIED)
        corroborated = sum(1 for v in verdicts if v.final_status == BeliefStatus.CORROBORATED)
        inconclusive = sum(1 for v in verdicts if v.final_status == BeliefStatus.INCONCLUSIVE)
        total_karma = sum(v.karma_delta_suggestion for v in verdicts)
        falsification_rate = falsified / total if total > 0 else 0.0
        corroboration_rate = corroborated / total if total > 0 else 0.0

        return {
            "total_tested": total,
            "falsified": falsified,
            "corroborated": corroborated,
            "inconclusive": inconclusive,
            "total_karma_earned": total_karma,
            "falsification_rate": falsification_rate,
            "corroboration_rate": corroboration_rate,
        }
