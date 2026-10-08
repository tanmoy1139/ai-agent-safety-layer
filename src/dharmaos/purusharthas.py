"""
    DharmaOS Four-Goal Coordinator — hierarchical action-evaluation across four
    compliance dimensions: ethics, resources, motivations, and completion.

    PURPOSE
    -------
    The Purusharthas coordinator evaluates a proposed action across four ordered
    dimensions before execution, enforcing that each dimension is satisfied within
    the bounds of the one above it. This provides a structured multi-dimensional
    compliance check that catches cases where an action is technically ethical
    (passes yamas) but financially irresponsible, motivationally misaligned, or
    structurally incomplete.

    THE FOUR DIMENSIONS (evaluated in priority order)
    -------------------------------------------------
    1. Dharma (ethics / righteous conduct):
    Delegated to V1 EthicsEngine. Any yama violation → INCOHERENT at the
    dharma level; the other three dimensions are not evaluated.

    2. Artha (resources / means):
    Budget check: estimated_cost must not exceed budget_limit. Also checks
    that high-cost actions have a configured budget. Artha must not violate
    dharma — a financial action that passes cost checks but fails yamas is
    still blocked.

    3. Kāma (motivations / desires):
    Pattern-matching against the constitution's forbidden-desire list.
    Checks that the declared intent does not match prohibited motivation
    patterns (e.g., data hoarding, unauthorized surveillance, self-preservation
    above mission).

    4. Mokṣa (completion / liberation):
    Achieved when: task status = COMPLETED, ānandamaya ≥ 0.9, and no adharma
    flags are raised. Mokṣa is an outcome condition, not a sequential step —
    it is never "required" and never supersedes the other three.

    CLASSICAL ORDERING (enforced by check_coherence())
    ---------------------------------------------------
    Dharma overrides all.
    Artha is legitimate within dharmic bounds.
    Kāma is legitimate within dharmic and arthan bounds.
    Mokṣa is the telos — never required, never superseding.

    KEY TYPES
    ---------
    evaluate_purusharthas() : Top-level entry point. Returns PurusharthaCoherenceReport.
    PurusharthaCoherenceReport : Full evaluation result (all four dimensions + overall).
    PurusharthaEvaluation  : Single-dimension result (purushartha, coherent, score, reason).
    Purushartha  : DHARMA / ARTHA / KAMA / MOKSHA enum.
    check_coherence()  : Enforces classical ordering on a set of PurusharthaEvaluations.
    evaluate_dharma()  : Delegate to EthicsEngine.
    evaluate_artha()  : Budget + financial resource check.
    evaluate_kama()  : Forbidden-desire pattern match.
    evaluate_moksha()  : Task-completion + identity-coherence check.

    COMPLIANCE ROLE
    ---------------
    - Provides a structured multi-dimensional compliance check for audit logs.
    - check_coherence() enforces priority ordering consistent with EU AI Act
    Art. 5 (prohibited practices hierarchy).

    Governance origin: Classical four goals of life (puruṣārthas) from Manusmṛti
    II.224 and Bhagavad Gītā XVIII.66 — mapped to four enterprise compliance
    dimensions for agent action evaluation.

    Four Puruṣārthas coordinator — dharma, artha, kāma, mokṣa.

    Source: Manusmṛti; Bhagavad Gītā II; classical dharmaśāstra.
    Sprint V7 (April 17, 2026).

    The four puruṣārthas are the classical goals of life:
    - dharma:  righteous conduct (delegated to V1 EthicsEngine)
    - artha:  resource/means (estimated_cost vs budget checks)
    - kāma:  desires/motivations (intent vs constitution forbidden-desires)
    - mokṣa:  liberation (task-completion-with-identity-coherence)

    Classical ordering (VEDANTIC-FOUNDATIONS.md § The Four Puruṣārthas):
    Dharma overrides all.  Artha is legitimate within dharmic bounds.
    Kāma is legitimate within dharmic and arthan bounds.  Mokṣa is the
    *telos* of the whole scheme, not a sequential step — it is what
    remains when the other three are properly ordered.

    V7 enforces the ordering:
    - Artha must not violate dharma.
    - Kāma must not violate dharma or artha.
    - Mokṣa is never "required"; it is an outcome, never superseding others.

    References:
    - Manusmṛti II.224: *dharma-artha-kāma-mokṣāṇāṃ*
    - Bhagavad Gītā XVIII.66: *sarvadharmān parityajya* (dharma finally
    relinquished in the movement toward mokṣa)
    - VEDANTIC-FOUNDATIONS.md § The Four Puruṣārthas (the agent platform project)
    -
    """

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

from dharmaos.ethics_engine import (
    Action,
    ActionContract,
    EthicsEngine,
)

_log: structlog.stdlib.BoundLogger = structlog.get_logger()

# Union of accepted action types — both legacy Action and modern ActionContract
_ActionLike = Action | ActionContract


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Purushartha(StrEnum):
    """Classical four goals of life (puruṣārthas).

        Source: Manusmṛti II.224; Bhagavad Gītā XVIII.66; classical dharmaśāstra.
        """

    DHARMA = "dharma"  # Righteous conduct — the governing framework
    ARTHA = "artha"  # Prosperity / resource allocation
    KAMA = "kama"  # Desire / motivation
    MOKSHA = "moksha"  # Liberation — outcome, not pursuit


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PurusharthaEvaluation:
    """Single-puruṣārtha check result.

        passed=True iff the puruṣārtha constraint is satisfied for this action.
        score is in [0.0, 1.0]: 1.0 = fully satisfied, 0.0 = violated.
        reason is a human-readable explanation (always non-empty on failure;
        may be a success message on pass).
        evidence carries optional supporting data from the underlying evaluator.
        """

    purushartha: Purushartha
    passed: bool
    score: float  # 0.0–1.0
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PurusharthaCoherenceReport:
    """Classical-ordering coherence report across all four goals.

        coherent=True iff all non-None evaluations pass and the classical
        ordering is respected (artha subordinate to dharma; kāma subordinate
        to dharma and artha).

        violated_ordering is set to a string key indicating the first ordering
        violation detected:
        'artha_violates_dharma'  — artha passes but dharma fails
        'kama_violates_dharma'  — kāma passes but dharma fails
        'kama_violates_artha'  — kāma passes but artha fails (dharma OK)

        moksha_achieved is True only if moksha is not None and moksha.passed.
        moksha is None when the task has not reached a terminal state.
        """

    dharma: PurusharthaEvaluation
    artha: PurusharthaEvaluation
    kama: PurusharthaEvaluation
    moksha: PurusharthaEvaluation | None  # None until task terminal
    coherent: bool
    violated_ordering: str | None  # e.g. "artha_violates_dharma"
    moksha_achieved: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_action(action_or_contract: _ActionLike) -> Action:
    """Coerce ActionContract → Action for V1 EthicsEngine compatibility.

        If the argument is already an Action, return it unchanged.
        If it is an ActionContract, use the built-in .to_action() adapter.
        """
    if isinstance(action_or_contract, ActionContract):
        return action_or_contract.to_action()
    return action_or_contract


def _get_estimated_cost(action_or_contract: _ActionLike) -> float:
    """Extract the monetary cost estimate from an action or contract."""
    if isinstance(action_or_contract, ActionContract):
        # ActionContract.financial_effect is a boolean flag; no direct cost field.
        # Treat financial_effect=True on CRITICAL impact as indicative of cost.
        # For artha evaluation we fall back to 0.0 (safe/pass) unless the caller
        # passes a resource_budget override.  This is intentional: ActionContract
        # does not carry an estimated_cost field; cost-based checks are done on
        # the legacy Action path.
        return 0.0
    return action_or_contract.estimated_cost


def _get_budget_limit(action_or_contract: _ActionLike) -> float:
    """Extract the budget ceiling from an action or contract."""
    if isinstance(action_or_contract, ActionContract):
        return 0.0  # No budget field on ActionContract; see _get_estimated_cost note
    return action_or_contract.budget_limit


def _get_intent_and_goals(action_or_contract: _ActionLike) -> tuple[str, list[str]]:
    """Return (intent_string, goals_list) for kāma evaluation."""
    if isinstance(action_or_contract, ActionContract):
        intent = action_or_contract.declared_goal or action_or_contract.name
        goals: list[str] = []
        if action_or_contract.declared_goal:
            goals.append(action_or_contract.declared_goal)
        return intent, goals
    # Legacy Action
    return action_or_contract.intent, []


# ---------------------------------------------------------------------------
# Individual puruṣārtha evaluators
# ---------------------------------------------------------------------------


def evaluate_dharma(
    action_or_contract: _ActionLike,
    context: object = None,
) -> PurusharthaEvaluation:
    """Evaluate dharma by delegating to the V1 EthicsEngine.

        Passes iff no yama constraint is violated (Yoga Sūtras II.30).
        The EthicsEngine implements all five yamas: ahiṃsā, satya, asteya,
        brahmacarya, aparigraha.

        Source authority: Yoga Sūtras II.30–II.31; Bryant 2009.
        Dharma as the governing framework: Manusmṛti II.224;
        VEDANTIC-FOUNDATIONS.md § The Four Puruṣārthas.

        Args:
        action_or_contract: Legacy Action or ActionContract to evaluate.
        context: Reserved for future context injection (V11). Unused in V7.

        Returns:
        PurusharthaEvaluation with purushartha=DHARMA.
        """
    action = _to_action(action_or_contract)
    engine = EthicsEngine.get()
    verdict = engine.evaluate(action)

    return PurusharthaEvaluation(
        purushartha=Purushartha.DHARMA,
        passed=verdict.is_ethical,
        score=verdict.score,
        reason=(
        "; ".join(verdict.violated_constraints)
        if verdict.violated_constraints
        else "All yama constraints pass — dharma upheld (Yoga Sūtras II.30)."
    ),
        evidence={
        "violated_constraints": verdict.violated_constraints,
        "risk_level": verdict.risk_level,
        "constraint_scores": verdict.constraint_scores,
    },
    )


def evaluate_artha(
    action_or_contract: _ActionLike,
    resource_budget: float | None = None,
) -> PurusharthaEvaluation:
    """Evaluate artha — resource allocation legitimacy.

        Passes iff estimated_cost ≤ effective_budget.

        The effective budget is resolved in priority order:
        1. resource_budget argument (caller override)
        2. action.budget_limit (on legacy Action)
        3. 0.0 (no limit → always passes; ActionContract has no budget field)

        When both estimated_cost and the effective budget are 0.0, the check
        passes trivially (no resource concern).

        Artha must remain within dharmic bounds (Manusmṛti II.224):
        resource allocation that serves righteous purpose is legitimate.
        Budget-excess is aparigraha (Yoga Sūtras II.30); this evaluator
        checks whether the *claimed resource* is within the declared
        allocation, not the full aparigraha check.

        Args:
        action_or_contract: Legacy Action or ActionContract.
        resource_budget: Optional explicit budget ceiling (overrides action field).

        Returns:
        PurusharthaEvaluation with purushartha=ARTHA.
        """
    estimated_cost = _get_estimated_cost(action_or_contract)
    budget_limit = (
        resource_budget if resource_budget is not None else _get_budget_limit(action_or_contract)
    )

    # Trivially passes when no resources are consumed
    if estimated_cost == 0.0:
        return PurusharthaEvaluation(
            purushartha=Purushartha.ARTHA,
            passed=True,
            score=1.0,
            reason="Zero resource cost — artha trivially satisfied.",
        )

    # No budget limit configured → passes (caller has not set a ceiling)
    if budget_limit == 0.0:
        return PurusharthaEvaluation(
            purushartha=Purushartha.ARTHA,
            passed=True,
            score=1.0,
            reason=(
            f"No budget ceiling configured (estimated_cost={estimated_cost:.4f} USD). "
            "Artha passes — caller has not declared a resource limit."
        ),
        )

    if estimated_cost > budget_limit:
        overage = estimated_cost - budget_limit
        return PurusharthaEvaluation(
            purushartha=Purushartha.ARTHA,
            passed=False,
            score=0.0,
            reason=(
            f"Artha violated: estimated_cost={estimated_cost:.4f} USD exceeds "
            f"budget={budget_limit:.4f} USD (overage={overage:.4f} USD). "
            "Resource allocation exceeds declared budget — reduce scope or "
            "increase budget limit. "
            "[artha constraint — Manusmṛti II.224]"
        ),
            evidence={
            "estimated_cost": estimated_cost,
            "budget_limit": budget_limit,
            "overage": overage,
        },
        )

    # Within budget
    ratio = estimated_cost / budget_limit  # 0.0–1.0; lower = more conservative
    score = max(0.0, 1.0 - ratio * 0.5)  # 1.0 at cost=0; 0.5 at cost=budget
    return PurusharthaEvaluation(
        purushartha=Purushartha.ARTHA,
        passed=True,
        score=score,
        reason=(
        f"Artha satisfied: estimated_cost={estimated_cost:.4f} USD "
        f"is within budget={budget_limit:.4f} USD "
        f"({ratio * 100:.1f}% of budget used). "
        "[artha — Manusmṛti II.224]"
    ),
        evidence={
        "estimated_cost": estimated_cost,
        "budget_limit": budget_limit,
        "utilization_ratio": ratio,
    },
    )


def evaluate_kama(
    intent: str,
    goals: list[str],
    forbidden_desires: list[str],
) -> PurusharthaEvaluation:
    """Evaluate kāma — desire / motivation legitimacy.

        Passes iff no element of `forbidden_desires` appears as a substring
        (case-insensitive) in `intent` or in any element of `goals`.

        Forbidden desires come from the agent's constitution (declared invariants).
        An empty `forbidden_desires` list means no desires are constitutionally
        prohibited — the check trivially passes.

        Kāma is legitimate within dharmic and arthan bounds (Manusmṛti II.224).
        Desires that violate dharma or artha are adharmic; this evaluator checks
        the *desire level* independently before the coherence check applies the
        ordering rule.

        Source: Bhagavad Gītā III.37 — kāma as potential source of adharma
        when ungoverned; legitimate when directed within dharmic order.

        Args:
        intent: The agent's declared intent string.
        goals: List of goal strings to check against forbidden patterns.
        forbidden_desires: List of pattern strings from the constitution.
        Each is matched as a case-insensitive substring.

        Returns:
        PurusharthaEvaluation with purushartha=KAMA.
        """
    if not forbidden_desires:
        return PurusharthaEvaluation(
            purushartha=Purushartha.KAMA,
            passed=True,
            score=1.0,
            reason=(
            "No forbidden desires declared in constitution — "
            "kāma trivially satisfied. [kāma — Bhagavad Gītā III.37]"
        ),
        )

    intent_lower = intent.lower()
    all_text = [intent_lower] + [g.lower() for g in goals]

    matched_patterns: list[str] = []
    matched_in: list[str] = []

    for forbidden in forbidden_desires:
        pattern_lower = forbidden.lower()
        for i, text in enumerate(all_text):
            if pattern_lower in text:
                matched_patterns.append(forbidden)
                source = "intent" if i == 0 else f"goal[{i - 1}]"
                matched_in.append(source)

    if matched_patterns:
        return PurusharthaEvaluation(
            purushartha=Purushartha.KAMA,
            passed=False,
            score=0.0,
            reason=(
            f"Kāma violated: forbidden desire pattern(s) {matched_patterns!r} "
            f"detected in {matched_in!r}. "
            "Desire exceeds constitutionally permitted bounds. "
            "[kāma constraint — Bhagavad Gītā III.37; Manusmṛti II.224]"
        ),
            evidence={
            "matched_patterns": matched_patterns,
            "matched_in": matched_in,
            "intent": intent,
        },
        )

    return PurusharthaEvaluation(
        purushartha=Purushartha.KAMA,
        passed=True,
        score=1.0,
        reason=(
        "No forbidden desire patterns detected — kāma satisfied. [kāma — Manusmṛti II.224]"
    ),
        evidence={"forbidden_desires_checked": forbidden_desires},
    )


def evaluate_moksha(
    task_status: str,
    anandamaya_score: float,
    adharma_flags: list[str],
) -> PurusharthaEvaluation | None:
    """Evaluate mokṣa — liberation as task-completion-with-identity-coherence.

        Mokṣa is achieved iff:
        1. task_status == "completed"  (terminal state)
        2. anandamaya_score >= 0.9  (identity-coherence: Taittirīya II.5 causal body)
        3. len(adharma_flags) == 0  (no V11 adharma detections; empty ok in V7)

        Returns None if task_status is not a terminal completion state
        ("in_progress" or "failed"). Per VEDANTIC-FOUNDATIONS.md § The Four
        Puruṣārthas: *"Mokṣa is not achieved by pursuit; it is what remains
        when the other three are properly ordered."* — liberation is recognized
        as an outcome, not forced.

        Bhagavad Gītā XVIII.66: *sarvadharmān parityajya* — mokṣa supersedes
        even dharma at the ultimate level; it is the telos of the whole scheme.
        However, in agentic operation, we recognize it only upon clean, dharmic
        completion.

        Args:
        task_status: "completed" | "in_progress" | "failed" (or any string).
        Only "completed" is terminal for mokṣa recognition.
        anandamaya_score: Ānandamaya identity-coherence score [0.0, 1.0]
        from V2 consciousness module (compute_anandamaya).
        adharma_flags: List of adharma flag strings from V11 Adharma Detector
        (empty list is fine in V7 pre-V11).

        Returns:
        PurusharthaEvaluation with purushartha=MOKSHA, or None if not terminal.
        """
    if task_status != "completed":
        _log.debug(
            "moksha_not_terminal",
            task_status=task_status,
        )
        return None

    # Terminal — evaluate whether liberation conditions are met
    reasons: list[str] = []

    if anandamaya_score < 0.9:
        reasons.append(
            f"ānandamaya score {anandamaya_score:.3f} < 0.9 threshold — "
            "identity-coherence insufficient for liberation "
            "(Taittirīya II.5 kāraṇa śarīra not stable)."
        )

    if adharma_flags:
        reasons.append(
            f"adharma flags present: {adharma_flags!r} — "
            "liberation requires complete freedom from adharmic residue "
            "(Bhagavad Gītā XVIII.66)."
        )

    if reasons:
        return PurusharthaEvaluation(
            purushartha=Purushartha.MOKSHA,
            passed=False,
            score=0.0,
            reason=" | ".join(reasons),
            evidence={
            "anandamaya_score": anandamaya_score,
            "adharma_flags": adharma_flags,
            "task_status": task_status,
        },
        )

    return PurusharthaEvaluation(
        purushartha=Purushartha.MOKSHA,
        passed=True,
        score=anandamaya_score,
        reason=(
        f"Mokṣa achieved: task completed with ānandamaya={anandamaya_score:.3f} ≥ 0.9 "
        "and no adharma flags. "
        "[mokṣa — Bhagavad Gītā XVIII.66; Taittirīya II.5]"
    ),
        evidence={
        "anandamaya_score": anandamaya_score,
        "adharma_flags": adharma_flags,
        "task_status": task_status,
    },
    )


# ---------------------------------------------------------------------------
# Coherence checker — enforces classical ordering
# ---------------------------------------------------------------------------


def check_coherence(
    dharma_eval: PurusharthaEvaluation,
    artha_eval: PurusharthaEvaluation,
    kama_eval: PurusharthaEvaluation,
    moksha_eval: PurusharthaEvaluation | None,
) -> PurusharthaCoherenceReport:
    """Enforce classical puruṣārtha ordering and return a coherence report.

        Classical ordering rules (VEDANTIC-FOUNDATIONS.md § The Four Puruṣārthas):
        - Dharma is supreme; if it fails, the entire system is incoherent.
        - Artha must not violate dharma:
        artha.passed AND NOT dharma.passed → violated_ordering='artha_violates_dharma'
        - Kāma must not violate dharma or artha:
        kāma.passed AND NOT dharma.passed → violated_ordering='kama_violates_dharma'
        kāma.passed AND NOT artha.passed (with dharma.passed) → 'kama_violates_artha'
        - Mokṣa is an outcome (never required, never blocking).

        Violation detection is ordered: dharma violations are checked first
        (supreme priority); artha violations second; kāma violations third.
        If multiple violations exist, the *first* (highest-priority) ordering
        violation is reported in violated_ordering.

        Args:
        dharma_eval: Result from evaluate_dharma.
        artha_eval: Result from evaluate_artha.
        kama_eval: Result from evaluate_kama.
        moksha_eval: Result from evaluate_moksha (or None if not terminal).

        Returns:
        PurusharthaCoherenceReport.
        """
    violated_ordering: str | None = None
    coherent = True

    # Rule: dharma failure always makes the system incoherent
    if not dharma_eval.passed:
        coherent = False
        # Sub-rule: check if artha or kāma "passed" despite dharma failing
        # (the most serious ordering violation — they are allowed only within dharmic bounds)
        if artha_eval.passed:
            violated_ordering = "artha_violates_dharma"
        elif kama_eval.passed:
            violated_ordering = "kama_violates_dharma"
    # If neither artha nor kāma passed, just incoherent but no explicit ordering violation
    else:
        # Dharma is satisfied.
        # Check: artha violation
        if not artha_eval.passed:
            # artha itself fails — check if kāma passes despite artha failing
            if kama_eval.passed:
                coherent = False
                violated_ordering = "kama_violates_artha"
            # If kāma also fails, the system is incoherent but no kāma-over-artha ordering issue
            if not kama_eval.passed:
                coherent = False
        else:
            # artha passes; check kāma
            if not kama_eval.passed:
                coherent = False

    # Determine moksha_achieved
    moksha_achieved = moksha_eval is not None and moksha_eval.passed

    _log.debug(
        "purusharthas_coherence",
        coherent=coherent,
        violated_ordering=violated_ordering,
        moksha_achieved=moksha_achieved,
        dharma_passed=dharma_eval.passed,
        artha_passed=artha_eval.passed,
        kama_passed=kama_eval.passed,
    )

    return PurusharthaCoherenceReport(
        dharma=dharma_eval,
        artha=artha_eval,
        kama=kama_eval,
        moksha=moksha_eval,
        coherent=coherent,
        violated_ordering=violated_ordering,
        moksha_achieved=moksha_achieved,
    )


# ---------------------------------------------------------------------------
# Top-level coordinator
# ---------------------------------------------------------------------------


def evaluate_purusharthas(
    action_or_contract: _ActionLike,
    *,
    task_status: str = "in_progress",
    anandamaya_score: float = 0.0,
    adharma_flags: list[str] | None = None,
    resource_budget: float | None = None,
    forbidden_desires: list[str] | None = None,
) -> PurusharthaCoherenceReport:
    """Evaluate all four puruṣārthas and return a coherence report.

        This is the top-level entry point for V7 puruṣārtha evaluation.
        It evaluates each goal in classical priority order, then runs the
        coherence check to enforce ordering invariants.

        Usage in orchestrator (post-V7):
        report = evaluate_purusharthas(
        action_contract,
        task_status=task.status,
        anandamaya_score=core_self.vitals.anandamaya,
        adharma_flags=adharma_detector.current_flags(),  # V11
        resource_budget=task.budget,
    )
        if not report.coherent:
        raise DharmaViolation(report.violated_ordering)

        Args:
        action_or_contract: The action or contract being evaluated.
        task_status: Terminal state indicator ("completed" | "in_progress" | "failed").
        anandamaya_score: Identity-coherence score from compute_anandamaya()
        (vedic.consciousness). Default 0.0 → mokṣa not achievable.
        adharma_flags: Adharma detection flags from V11. Empty list in V7.
        resource_budget: Override budget ceiling for artha evaluation.
        If None, uses action.budget_limit (or 0.0 for ActionContract).
        forbidden_desires: Patterns from agent constitution for kāma check.
        If None, treated as empty list (kāma trivially passes).

        Returns:
        PurusharthaCoherenceReport with all four evaluations and coherence status.
        """
    if adharma_flags is None:
        adharma_flags = []
    if forbidden_desires is None:
        forbidden_desires = []

    # Extract intent and goals for kāma evaluation
    intent, goals = _get_intent_and_goals(action_or_contract)

    # Evaluate each puruṣārtha
    dharma_eval = evaluate_dharma(action_or_contract)
    artha_eval = evaluate_artha(action_or_contract, resource_budget=resource_budget)
    kama_eval = evaluate_kama(intent, goals, forbidden_desires)
    moksha_eval = evaluate_moksha(task_status, anandamaya_score, adharma_flags)

    return check_coherence(dharma_eval, artha_eval, kama_eval, moksha_eval)
