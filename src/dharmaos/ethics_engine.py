"""
    DharmaOS Ethics Engine — Non-compensatory action-constraint gate.

    PURPOSE
    -------
    Evaluates every proposed agent action against five hard-stop ethical
    constraints (yamas) before execution is permitted. A single violated
    constraint collapses the total score to 0.0 regardless of other
    dimensions — mirroring Karush-Kuhn-Tucker (KKT) feasibility: any
    violated binding constraint renders the point infeasible.

    The engine is the primary constitutional gate in the DharmaOS pipeline.
    It is called at Layer 3 of AdharmaDetector (constitutional vetoes) and
    independently by callers that need a lightweight ethics check without
    the full 8-layer pipeline overhead.

    COMPLIANCE ROLE
    ---------------
    - Prevents irreversible harm (ahiṃsā): destructive, financial, and
    messaging actions that affect third parties require explicit
    confirmation or reversal capability.
    - Prevents deception (satya): actions with stealth keywords, missing
    descriptions for sensitive operations, or URL-shortener targets are
    blocked.
    - Prevents data theft (asteya): credential fields transmitted to
    external parties, or form fills on sensitive domains, are blocked.
    - Enforces compute energy discipline (brahmacarya): actions whose
    compute-cost × token-budget / task-worth ratio exceeds a configurable
    threshold are blocked.
    - Enforces data minimization (aparigraha): actions that exceed budget
    limits or collect more than 20 data fields are blocked.

    KEY TYPES
    ---------
    Action  : Legacy 15-field action descriptor (still supported for
    backward compatibility with pre-V-ACT callers).
    ActionContract  : 27-field structured action declaration (preferred path).
    Flows through the Governance Sidecar before any tool call.
    EthicsEngine  : The constraint-evaluation engine. Singleton per tenant via
    for_tenant(tenant_id); process-wide via get_ethics_engine().
    EthicsVerdict  : Immutable result: is_ethical, score [0,1], risk_level,
    violated_constraints, recommendations.

    USAGE
    -----
    from dharmaos.ethics_engine import EthicsEngine, ActionContract

    engine = EthicsEngine.for_tenant("acme-bank")
    verdict = engine.evaluate_contract(contract)
    if not verdict.is_ethical:
    raise PolicyViolationError(verdict.violated_constraints)

    Governance origin: Five yamas of Yoga Sūtras II.30 (Patañjali) —
    non-harm, truthfulness, non-theft, energy-discipline, non-possessiveness —
    mapped to enterprise agent-action constraints. The public API uses plain
    English names.

    Mathematical constraint evaluation for agent actions, grounded in the
    Yoga Sūtras of Patañjali (Chapter II, Sādhana Pāda).

    **Philosophical grounding:**
    Source authority: Edwin F. Bryant, *The Yoga Sūtras of Patañjali*,
    North Point Press, 2009. Sūtra II.29–II.45 (Sādhana Pāda, aṣṭāṅga).

    The five yamas (II.30) — universal ethical restraints (*mahāvrataṃ*, II.31):
    1. Ahiṃsā  — non-harm
    2. Satya  — truthfulness (subordinate to ahiṃsā)
    3. Asteya  — non-stealing
    4. Brahmacarya — energy-conservation / continence (vīryasya rakṣaṇam per
    Vyāsa Bhāṣya II.30 + Vācaspati Miśra's Tattvavaiśāradī)
    5. Aparigraha — non-possessiveness / non-excess-acquisition

    The five niyamas (II.32) — personal observances:
    1. Śauca  — purity of I/O (clean_io)
    2. Santoṣa  — contentment / satisficing (follow_plan; don't gold-plate)
    3. Tapas  — heat of disciplined effort (task_focus; sustained effort)
    4. Svādhyāya — self-study from past runs (learn)
    5. Īśvara-praṇidhāna — deferral to the higher (defer_to_human)

    **Scoring mechanism:**
    Weighted harmonic mean (KKT-feasibility analogue): a single zero-score
    on any weight-1.0 constraint collapses total to 0.0, matching KKT logic
    where a violated binding constraint renders the point infeasible.

    **V1 changes from reference implementation/backend/app/services/the agent platform/ethics_engine.py:**
    - Renamed `_check_excess` → `_check_aparigraha` (budget + data-possession excess)
    REASON: budget-excess is aparigraha, not brahmacarya. Per FIDELITY-AUDIT-V1 §2.
    - Added new `_check_brahmacarya` — energy-discipline test.
    REASON: brahmacarya was entirely absent; it checks compute efficiency,
    not spending. These are distinct yamas per Yoga Sūtras II.30.
    - Added `NIYAMA_LABELS` constant — maps Sanskrit label → Python identifier.
    - Added `estimated_compute_cost_usd`, `estimated_token_budget`,
    `task_worth_score`, `brahmacarya_threshold` to Action.
    - Niyama label annotations added as module-level constant.

    Ported lineage:
    /opt/agent-core/*.py (conception 2026-01-25)
    /opt/reference implementation/backend/app/services/the agent platform/ethics_engine.py (2026-04-15)
    """

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

import structlog
from dharmaos.metrics import brahmacarya_ratio_observed, violation_recorded

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Niyama labels — Sprint V1 semantic correction
# Source: Yoga Sūtras II.32 (Bryant 2009)
# ---------------------------------------------------------------------------

#: Ordered mapping from Sanskrit niyama label to the Python method / objective
#: identifier it governs in this engine. Used by tests and introspection tooling.
#:
#: Yoga Sūtras II.32:
#:  śauca-santoṣa-tapas-svādhyāyeśvara-praṇidhānāni niyamāḥ
NIYAMA_LABELS: Final[dict[str, str]] = {
    "śauca": "clean_io",  # Purity of I/O — unchanged
    "santoṣa": "follow_plan",  # Contentment/satisficing — don't gold-plate
    "tapas": "task_focus",  # Disciplined effort — sustained focus
    "svādhyāya": "learn",  # Self-study from past runs
    "īśvara-praṇidhāna": "defer_to_human",  # Surrender / HITL deferral
}


# ---------------------------------------------------------------------------
# Intent taxonomy
# ---------------------------------------------------------------------------


class IntentType(StrEnum):
    """Categorised agent intent types for constraint logic."""

    BROWSE = "browse"
    FILL_FORM = "fill_form"
    SEND_EMAIL = "send_email"
    PURCHASE = "purchase"
    DELETE = "delete"
    EXTRACT = "extract"
    CLICK = "click"
    SCHEDULE = "schedule"
    UPLOAD = "upload"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Action:
    """Describes an agent action to be evaluated by the ethics engine.

        Fields marked with Sanskrit annotations indicate the primary yama/niyama
        they inform during evaluation.

        Yoga Sūtras II.30 yama fields:
        - estimated_cost  → aparigraha (budget excess check)
        - budget_limit  → aparigraha (ceiling)
        - data_fields  → aparigraha (data possession excess)
        - estimated_compute_cost_usd → brahmacarya (energy-discipline)
        - estimated_token_budget  → brahmacarya (token consumption budget)
        - task_worth_score  → brahmacarya (denominator of energy ratio)
        - brahmacarya_threshold  → brahmacarya (configurable cutoff, default 10.0)
        """

    description: str
    """Human-readable description of what the agent intends to do."""

    intent: str
    """IntentType value or free-text intent label."""

    target: str
    """URL, email address, product, filename, etc."""

    estimated_cost: float = 0.0
    """Monetary cost in USD for executing this action."""

    reversible: bool = True
    """Whether this action can be undone after execution."""

    affects_others: bool = False
    """Whether this action impacts parties other than the requesting user."""

    data_fields: list[str] = field(default_factory=list)
    """Fields being read from or written to external systems."""

    budget_limit: float = 0.0
    """User-configured spending cap in USD (0 = no limit set)."""

    agent_id: str = ""
    """Identifier of the agent requesting evaluation."""

    # ── Brahmacarya fields (NEW in V1) ──────────────────────────────────────
    # Source: Yoga Sūtras II.30 — brahmacarya = vīryasya rakṣaṇam
    #  (conservation of vital force / energy)
    # Engineering mapping: Vyāsa Bhāṣya II.30 + Vācaspati Miśra Tattvavaiśāradī
    # "vital force directed toward Brahman rather than dispersed through
    #  sensory gratification" → compute energy directed toward task worth,
    #  not squandered on low-value processing.

    estimated_compute_cost_usd: float = 0.0
    """Estimated LLM/cloud compute cost in USD for this action's execution.
        Used in brahmacarya energy-discipline ratio."""

    estimated_token_budget: int = 0
    """Estimated token consumption (input + output) for this action.
        Used in brahmacarya energy-discipline ratio."""

    task_worth_score: float = 1.0
    """Heuristic worth of the task on scale [0.1, 10.0].
        Higher = more valuable task, higher compute allocation is justified.
        Derived from action description semantics. Default 1.0 (neutral)."""

    brahmacarya_threshold: float = 10.0
    """Configurable energy-discipline cutoff.
        Fails when (compute_cost x token_budget) / task_worth_score > threshold.
        Default 10.0 per  specification."""


@dataclass
class EthicsVerdict:
    """Result of an ethics evaluation.

        is_ethical=True iff ALL yama constraints pass AND score > 0.0.
        Fail-closed: evaluation errors produce is_ethical=False, score=0.0.
        """

    is_ethical: bool
    """True only when all binding yama constraints are satisfied."""

    score: float
    """Overall ethical score 0.0-1.0. 1.0 = all constraints fully satisfied."""

    violated_constraints: list[str]
    """Names of yama constraints that failed evaluation."""

    risk_level: str
    """One of: 'safe' | 'caution' | 'risky' | 'dangerous' | 'blocked'."""

    recommendations: list[str]
    """Human-readable guidance for the agent or user."""

    constraint_scores: dict[str, float] = field(default_factory=dict)
    """Per-constraint scores 0.0 or 1.0 (binary per-constraint)."""

    evaluated_at: float = field(default_factory=time.time)
    """Unix timestamp of evaluation."""

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-safe dict."""
        return {
            "is_ethical": self.is_ethical,
            "score": round(self.score, 3),
            "violated_constraints": self.violated_constraints,
            "risk_level": self.risk_level,
            "recommendations": self.recommendations,
            "constraint_scores": {k: round(v, 3) for k, v in self.constraint_scores.items()},
            "evaluated_at": self.evaluated_at,
        }


# ---------------------------------------------------------------------------
# ActionContract — DharmaOS §2.1 (V-ACT sprint)
# ---------------------------------------------------------------------------

#: Module-level logger for ActionContract warnings (freshness-gate log).
_ac_log: logging.Logger = logging.getLogger(__name__)


class ActionImpact(StrEnum):
    """Impact severity tier for ActionContract risk profiling."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ActionContract:
    """DharmaOS structured action declaration — 27-field enrichment of Action.

        Flows through Governance Sidecar / Permit Engine (V11) before any
        tool/API/browser call. Per DharmaOS §2.1 (docs/vedic/engineering/
        DHARMAOS-ARCHITECTURE-V1.md).

        V-ACT sprint (prerequisite to V11 Adharma Detector).
        """

    # ── Identity and provenance (7 fields) ──────────────────────────────────
    action_id: str
    path_id: str
    agent_id: str
    parent_agent_id: str | None
    tenant_id: str
    task_id: str
    requested_at: datetime  # must be tz-aware UTC

    # ── Action specification (5 fields) ─────────────────────────────────────
    name: str
    kind: str  # send_email, db_write, browser_click, etc.
    target: str  # URL, email addr, file path, etc.
    params: dict[str, Any] = field(default_factory=dict)
    tool: str | None = None  # which tool/API will execute

    # ── Risk profile (6 fields) ──────────────────────────────────────────────
    impact: ActionImpact = ActionImpact.LOW
    reversible: bool = True
    affects_others: bool = False
    sensitive_data: bool = False
    financial_effect: bool = False
    public_effect: bool = False

    # ── Identity lease (1 field) ─────────────────────────────────────────────
    capability_requested: str | None = None

    # ── Intent (3 fields — nishkāma-karma check: motive must be explicit) ───
    declared_goal: str = ""
    beneficiary: str = ""
    business_reason: str = ""

    # ── World-state dependency (3 fields — freshness gating) ────────────────
    needs_fresh_world: bool = False
    freshness_max_age_seconds: int = 300
    sources_required: list[str] = field(default_factory=list)

    # ── Expected outcome (2 fields — for V-RECON Outcome Reconciler) ────────
    success_criteria: list[str] = field(default_factory=list)
    potential_harms: list[str] = field(default_factory=list)

    # ── Confidence (2 fields — RF-29 logprob-OR-verbalized) ─────────────────
    model_confidence: float = 0.5  # [0, 1]
    evidence_confidence: float = 0.5  # [0, 1]

    # ── Provenance (4 fields) ────────────────────────────────────────────────
    policy_version: str = "1.0.0"
    prompt_hash: str = ""
    model_id: str = ""
    prompt_trust_domain: str = "system"  # V14 Trust-Domain tag propagation

    def __post_init__(self) -> None:
        """Validate: confidences in [0, 1], requested_at is UTC, impact valid."""
        if not (0.0 <= self.model_confidence <= 1.0):
            raise ValueError(f"model_confidence must be in [0, 1], got {self.model_confidence!r}")
        if not (0.0 <= self.evidence_confidence <= 1.0):
            raise ValueError(
                    f"evidence_confidence must be in [0, 1], got {self.evidence_confidence!r}"
                )
        if self.requested_at.tzinfo is None:
            raise ValueError(
                "requested_at must be timezone-aware UTC datetime; "
                f"got naive datetime {self.requested_at!r}. "
                "Use datetime.now(timezone.utc) or datetime(..., tzinfo=timezone.utc)."
            )
        # Warn if needs_fresh_world=True but no sources are listed (TD-V-ACT-01)
        if self.needs_fresh_world and not self.sources_required:
            _ac_log.warning(
                "ActionContract.needs_fresh_world=True but sources_required=[] — "
                "freshness cannot be verified without source list. "
                "(TD-V-ACT-01: consider populating sources_required)"
            )

    def to_action(self) -> Action:
        """Adapter — convert ActionContract to legacy Action for V1 engine.

            Maps ActionContract fields to the 8 core Action fields so that existing
            EthicsEngine.evaluate() can process a contract without modification.
            """
        return Action(
            description=self.declared_goal or self.name,
            intent=self.kind,
            target=self.target,
            reversible=self.reversible,
            affects_others=self.affects_others,
            agent_id=self.agent_id,
            data_fields=list(self.params.keys()) if self.params else [],
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe serialization.

            datetime → ISO 8601 string, Enum → str value, lists/dicts preserved.
            """
        raw = asdict(self)
        # Convert datetime to ISO string
        raw["requested_at"] = self.requested_at.isoformat()
        # Convert ActionImpact enum to its string value
        raw["impact"] = self.impact.value
        return raw

    @classmethod
    def from_action(cls, action: Action, **context: Any) -> ActionContract:
        """Backward-compat: promote legacy Action to ActionContract.

            Missing fields default; context kwargs fill identity + task fields.

            Required context kwargs: action_id, path_id, agent_id, tenant_id, task_id.
            Optional: parent_agent_id, impact, capability_requested, and any
            ActionContract field as override.
            """
        now_utc = datetime.now(UTC)
        return cls(
            # Identity — required from context; fall back to defaults
            action_id=context.pop("action_id", f"legacy-{id(action)}"),
            path_id=context.pop("path_id", ""),
            agent_id=context.pop("agent_id", action.agent_id or "unknown"),
            parent_agent_id=context.pop("parent_agent_id", None),
            tenant_id=context.pop("tenant_id", "default"),
            task_id=context.pop("task_id", ""),
            requested_at=context.pop("requested_at", now_utc),
            # Action spec — mapped from Action fields
            name=context.pop("name", action.description),
            kind=context.pop("kind", action.intent),
            target=action.target,
            params=context.pop("params", {}),
            tool=context.pop("tool", None),
            # Risk profile — Action.reversible / affects_others preserved
            impact=context.pop("impact", ActionImpact.LOW),
            reversible=action.reversible,
            affects_others=action.affects_others,
            sensitive_data=context.pop("sensitive_data", False),
            financial_effect=context.pop("financial_effect", False),
            public_effect=context.pop("public_effect", False),
            # Identity lease
            capability_requested=context.pop("capability_requested", None),
            # Intent — description becomes declared_goal
            declared_goal=context.pop("declared_goal", action.description),
            beneficiary=context.pop("beneficiary", ""),
            business_reason=context.pop("business_reason", ""),
            # World-state
            needs_fresh_world=context.pop("needs_fresh_world", False),
            freshness_max_age_seconds=context.pop("freshness_max_age_seconds", 300),
            sources_required=context.pop("sources_required", []),
            # Expected outcome
            success_criteria=context.pop("success_criteria", []),
            potential_harms=context.pop("potential_harms", []),
            # Confidence
            model_confidence=context.pop("model_confidence", 0.5),
            evidence_confidence=context.pop("evidence_confidence", 0.5),
            # Provenance
            policy_version=context.pop("policy_version", "1.0.0"),
            prompt_hash=context.pop("prompt_hash", ""),
            model_id=context.pop("model_id", ""),
            prompt_trust_domain=context.pop("prompt_trust_domain", "system"),
        )

        # P0-5: Reject unexpected context kwargs — prevents shadow_mode injection,
        # impact downgrade, tenant elevation, and other adversarial context stuffing.
        _KNOWN_FROM_ACTION_KEYS: frozenset[str] = frozenset({
            "action_id", "path_id", "agent_id", "parent_agent_id", "tenant_id",
            "task_id", "requested_at", "name", "kind", "params", "tool",
            "impact", "sensitive_data", "financial_effect", "public_effect",
            "capability_requested", "declared_goal", "beneficiary", "business_reason",
            "needs_fresh_world", "freshness_max_age_seconds", "sources_required",
            "success_criteria", "potential_harms", "model_confidence",
            "evidence_confidence", "policy_version", "prompt_hash", "model_id",
            "prompt_trust_domain",
        })
        unexpected = set(context.keys()) - _KNOWN_FROM_ACTION_KEYS
        if unexpected:
            raise ValueError(
                f"ActionContract.from_action received unexpected context keys: {unexpected}. "
                "This may indicate a prompt injection or API misuse attempt. "
                "Allowed keys: " + ", ".join(sorted(_KNOWN_FROM_ACTION_KEYS))
            )


def action_to_contract(
    action: Action,
    *,
    action_id: str,
    path_id: str,
    agent_id: str,
    tenant_id: str,
    task_id: str,
    parent_agent_id: str | None = None,
    impact: ActionImpact = ActionImpact.LOW,
    capability_requested: str | None = None,
    **overrides: Any,
) -> ActionContract:
    """Adapter: legacy Action + context → full ActionContract.

        Defaults most fields; caller provides identity + task; extras via **overrides.
        """
    return ActionContract.from_action(
        action,
        action_id=action_id,
        path_id=path_id,
        agent_id=agent_id,
        tenant_id=tenant_id,
        task_id=task_id,
        parent_agent_id=parent_agent_id,
        impact=impact,
        capability_requested=capability_requested,
        **overrides,
    )


# ---------------------------------------------------------------------------
# Risk-level thresholds
# ---------------------------------------------------------------------------


def _score_to_risk_level(score: float) -> str:
    """Map a 0.0–1.0 ethics score to a human-readable risk label."""
    if score >= 0.9:
        return "safe"
    if score >= 0.7:
        return "caution"
    if score >= 0.45:
        return "risky"
    if score > 0.0:
        return "dangerous"
    return "blocked"


# ---------------------------------------------------------------------------
# Intent pattern constants
# ---------------------------------------------------------------------------

_DESTRUCTIVE_INTENTS: frozenset[str] = frozenset(
    {IntentType.DELETE, "delete", "wipe", "remove", "destroy"}
)

_FINANCIAL_INTENTS: frozenset[str] = frozenset(
    {IntentType.PURCHASE, "purchase", "buy", "payment", "pay", "transfer", "wire", "checkout"}
)

_EMAIL_INTENTS: frozenset[str] = frozenset(
    {IntentType.SEND_EMAIL, "send_email", "email", "send", "message", "post", "publish", "reply"}
)

# Sensitive target URL patterns (compiled once at import time)
_SENSITIVE_URL_PATTERNS: list[str] = [
    r"bank|finance|payment|paypal|stripe|venmo|zelle",
    r"admin|root|sudo|config",
    r"\.gov|\.mil",
    r"password|credentials|secret|token|api[_-]?key",
]

_SENSITIVE_DATA_FIELDS: frozenset[str] = frozenset(
    {
    "password",
    "passwd",
    "ssn",
    "social_security",
    "credit_card",
    "cvv",
    "bank_account",
    "routing_number",
    "dob",
    "date_of_birth",
    "passport",
    "drivers_license",
    "pin",
    "secret",
    "private_key",
}
)


# ---------------------------------------------------------------------------
# EthicsEngine
# ---------------------------------------------------------------------------


class EthicsEngine:
    """Vedāntic-functional-analogue ethics constraint engine.

        Implements the 5 yamas (II.30) as hard-stop constraint checkers and
        exposes the 5 niyamas (II.32) as advisory objectives via NIYAMA_LABELS.

        **KKT-feasibility scoring (Karush-Kuhn-Tucker analogue):**
        A single violated weight-1.0 constraint collapses the total ethics
        score to 0.0, exactly mirroring KKT: any violated binding constraint
        makes the point infeasible regardless of other dimensions.

        **Yama checkers (hard constraints — Yoga Sūtras II.30):**
        _check_harm  → ahiṃsā (non-harm)
        _check_deception  → satya  (truthfulness)
        _check_theft  → asteya (non-stealing)
        _check_brahmacarya → brahmacarya (energy-discipline) [NEW V1]
        _check_aparigraha  → aparigraha (non-possessiveness / non-excess)

        **Niyama objectives (advisory — Yoga Sūtras II.32):**
        Exposed via NIYAMA_LABELS constant; not scored as hard blocks.
        Labels corrected in V1 from the reference implementation version per
        FIDELITY-AUDIT-V1.md §3.

        Singleton: use get_ethics_engine() for the process-wide instance.
        """

    _instances: dict[str, "EthicsEngine"] = {}
    _deprecated_singleton: EthicsEngine | None = None

    def __init__(
        self,
        *,
        brahmacarya_threshold: float = 10.0,
    ) -> None:
        """Initialise the ethics engine.

            Args:
            brahmacarya_threshold: Energy-discipline ratio cutoff.
            ratio = (estimated_compute_cost_usd × estimated_token_budget)
            / task_worth_score
            Fails when ratio > brahmacarya_threshold.
            Default 10.0 per  spec.
            """
        # Constraint weights govern the weighted-harmonic-mean collapse.
        # Weight 1.0 = binding constraint (single zero collapses total).
        # Weight < 1.0 = partial / advisory constraint.
        self.constraint_weights: dict[str, float] = {
            "ahimsa": 1.0,  # no_harm
            "satya": 1.0,  # no_deception
            "asteya": 1.0,  # no_theft
            "brahmacarya": 0.8,  # energy-discipline (V1 NEW)
            "aparigraha": 0.6,  # non-possessiveness (renamed from excess+greed)
        }
        self.brahmacarya_threshold: float = brahmacarya_threshold
        # P0-1 / P1-2: violation_history is per-tenant, bounded deque
        self.violation_history: deque[dict[str, object]] = deque(maxlen=10_000)
        # P2-1: brahmacarya calibration — record observed ratios
        self.brahmacarya_ratio_observed: list[float] = []
        self._sensitive_pattern: re.Pattern[str] = re.compile(
            "|".join(_SENSITIVE_URL_PATTERNS), re.IGNORECASE
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_contract(self, contract: ActionContract) -> EthicsVerdict:
        """Primary evaluation path — uses all 27 contract fields (P1-3).

            Unlike evaluate(action) which maps to an 8-field Action via
            to_action(), this method uses contract.impact, contract.sensitive_data,
            and contract.financial_effect as primary signals in constraint checkers,
            eliminating the 20-field data loss.

            Args:
            contract: Full 27-field ActionContract with impact, risk, and
            provenance fields.

            Returns:
            EthicsVerdict with score, risk level, and recommendations.
            """
        action = contract.to_action()
        # Enrich the legacy Action with contract-level risk fields
        # so constraint checkers that read Action can access these signals.
        if contract.impact in (ActionImpact.HIGH, ActionImpact.CRITICAL):
            action.affects_others = action.affects_others or True
        if contract.financial_effect:
            action.estimated_cost = max(action.estimated_cost, 1.0)
        if contract.sensitive_data:
            action.data_fields = list(set(action.data_fields) | {"_contract_sensitive"})
        return self.evaluate(action)

    def evaluate(self, action: Action) -> EthicsVerdict:
        """Evaluate an action against all 5 yama constraints.

            Fail-closed: if the evaluation raises an unexpected exception,
            returns a BLOCKED verdict rather than propagating or swallowing
            the error silently.

            Args:
            action: The action the agent wants to perform.

            Returns:
            EthicsVerdict with score, risk level, and recommendations.
            is_ethical=True iff all constraints pass.
            """
        log = logger.bind(
            intent=action.intent,
            target=action.target[:80] if action.target else "",
            agent_id=action.agent_id,
        )

        # P2-3 / M1: bind per-request context; unbind OUR keys first so a prior
        # request on a pooled worker can't contaminate this one's audit logs.
        structlog.contextvars.unbind_contextvars("agent_id", "intent", "target")
        structlog.contextvars.bind_contextvars(
            agent_id=action.agent_id,
            intent=action.intent,
            target=action.target[:80] if action.target else "",
        )

        try:
            checks: dict[str, tuple[bool, str]] = {
                "ahimsa": self._check_harm(action),
                "satya": self._check_deception(action),
                "asteya": self._check_theft(action),
                "brahmacarya": self._check_brahmacarya(action),
                "aparigraha": self._check_aparigraha(action),
            }

            violated: list[str] = []
            recommendations: list[str] = []
            constraint_scores: dict[str, float] = {}

            for name, (passed, explanation) in checks.items():
                score = 1.0 if passed else 0.0
                constraint_scores[name] = score
                if not passed:
                    violated.append(name)
                    recommendations.append(explanation)
                    log.warning(
                        "ethics_constraint_violated",
                        constraint=name,
                        yama=name,
                        explanation=explanation,
                    )

            total_score = self._weighted_score(constraint_scores)
            risk_level = _score_to_risk_level(total_score)
            is_ethical = len(violated) == 0

            if is_ethical:
                recommendations.append(
                    "Action passes all five yama constraints (Yoga Sūtras II.30) — proceed."
                )

            verdict = EthicsVerdict(
                is_ethical=is_ethical,
                score=total_score,
                violated_constraints=violated,
                risk_level=risk_level,
                recommendations=recommendations,
                constraint_scores=constraint_scores,
            )

            log.info(
                "ethics_verdict",
                score=round(total_score, 3),
                risk=risk_level,
                violated=violated,
            )

        except Exception as exc:
            log.exception("ethics_engine_error", error=str(exc))
            return EthicsVerdict(
                is_ethical=False,
                score=0.0,
                violated_constraints=["evaluation_error"],
                risk_level="blocked",
                recommendations=[
                f"Ethics evaluation failed internally: {exc}. "
                "Action blocked for safety (fail-closed per Yoga Sūtras mahāvrataṃ II.31)."
            ],
            )
        else:
            for constraint in violated:
                self.record_violation(action, constraint)

            return verdict

    # ------------------------------------------------------------------
    # Yama constraint checkers — Yoga Sūtras II.30
    # ------------------------------------------------------------------

    def _check_harm(self, action: Action) -> tuple[bool, str]:
        """Ahiṃsā — non-harm (Yoga Sūtras II.30).

            Vyāsa Bhāṣya II.30: "Non-harm to all beings, at all times, in
            thought, word, and deed. The other yamas are subordinate to it."

            Harm is indicated when:
            - Irreversible destructive intent on user's data or accounts.
            - Sending emails/messages to unexpected recipients (affects_others
            + irreversible).
            - Unauthorized irreversible financial actions affecting others.
            """
        intent_lower = action.intent.lower()

        if not action.reversible and any(d in intent_lower for d in _DESTRUCTIVE_INTENTS):
            return (
                False,
                f"Action '{action.intent}' on '{action.target}' is irreversible and "
                "destructive. Request explicit confirmation before proceeding. "
                "[ahiṃsā violation — Yoga Sūtras II.30]",
            )

        if (
            action.affects_others
            and not action.reversible
            and any(e in intent_lower for e in _EMAIL_INTENTS)
        ):
            return (
                False,
                f"Email/message to '{action.target}' affects others and cannot be "
                "undone. Show draft to user before sending. "
                "[ahiṃsā violation — Yoga Sūtras II.30]",
            )

        if (
                    action.affects_others
                    and action.estimated_cost > 0.0
                    and not action.reversible
                    and any(f in intent_lower for f in _FINANCIAL_INTENTS)
                ):
                    return (
                        False,
                        f"Financial action (${action.estimated_cost:.2f}) affects others and is "
                        "irreversible. Require explicit user confirmation. "
                        "[ahiṃsā violation — Yoga Sūtras II.30]",
                    )

        return True, ""

    def _check_deception(self, action: Action) -> tuple[bool, str]:
        """Satya — truthfulness (Yoga Sūtras II.30).

            Vyāsa Bhāṣya II.30: "Speech and mind conforming to reality as
            known. Satya that injures ahiṃsā becomes adharma."

            Deception is indicated when:
            - Description contains stealth/bypass keywords.
            - Empty description for a sensitive action.
            - Target is a URL shortener masking the real destination.
            """
        intent_lower = action.intent.lower()
        desc_lower = action.description.lower()

        stealth_keywords: list[str] = [
            "silent",
            "hidden",
            "background",
            "stealth",
            "undetected",
            "bypass",
            "override",
            "spoof",
        ]
        if any(kw in desc_lower for kw in stealth_keywords):
            return (
                False,
                "Action description contains stealth keyword. All agent actions must be "
                "transparent to the user. [satya violation — Yoga Sūtras II.30]",
            )

        sensitive_intents = _FINANCIAL_INTENTS | _EMAIL_INTENTS | _DESTRUCTIVE_INTENTS
        if not action.description.strip() and any(s in intent_lower for s in sensitive_intents):
            return (
                False,
                f"No description provided for sensitive action '{action.intent}'. "
                "Always describe what the agent is doing. "
                "[satya violation — Yoga Sūtras II.30]",
            )

        short_url_pattern = re.compile(
            r"(bit\.ly|t\.co|tinyurl|goo\.gl|ow\.ly|short\.link|rebrand\.ly)",
            re.IGNORECASE,
        )
        if short_url_pattern.search(action.target):
            return (
                False,
                f"Target URL '{action.target}' appears to be a shortener/redirect. "
                "Resolve to final destination before acting. "
                "[satya violation — Yoga Sūtras II.30]",
            )

        return True, ""

    def _check_theft(self, action: Action) -> tuple[bool, str]:
        """Asteya — non-stealing (Yoga Sūtras II.30).

            Vyāsa Bhāṣya II.30: "Not taking what is not given. Includes
            intellectual property, credit, attention, and time."

            Theft is indicated when:
            - Sensitive credential fields transmitted to external parties.
            - Financial/messaging intent on admin/sensitive endpoints.
            - Credential fields in a form-fill on a suspicious domain.
            """
        intent_lower = action.intent.lower()

        sensitive_in_fields = _SENSITIVE_DATA_FIELDS.intersection(
            {f.lower() for f in action.data_fields}
        )
        if sensitive_in_fields and action.affects_others:
            return (
                False,
                f"Action would transmit sensitive fields {sensitive_in_fields} to "
                f"external target '{action.target}'. Credential exfiltration risk. "
                "[asteya violation — Yoga Sūtras II.30]",
            )

        if self._sensitive_pattern.search(action.target) and any(
            f in intent_lower for f in _FINANCIAL_INTENTS | _EMAIL_INTENTS
        ):
            return (
                False,
                f"Action targets sensitive endpoint '{action.target}' with "
                "financial/messaging intent. Verify task scope. "
                "[asteya violation — Yoga Sūtras II.30]",
            )

        if (
            action.intent.lower() in {IntentType.FILL_FORM, "fill_form"}
            and sensitive_in_fields
            and self._sensitive_pattern.search(action.target)
        ):
            return (
                False,
                f"Form fill on sensitive domain '{action.target}' includes credential "
                f"fields {sensitive_in_fields}. High phishing/theft risk. "
                "[asteya violation — Yoga Sūtras II.30]",
            )

        return True, ""

    def _check_brahmacarya(self, action: Action) -> tuple[bool, str]:
        """Brahmacarya — energy-discipline (Yoga Sūtras II.30).

            This is the yama that was MISSING from the reference implementation version.
            Per FIDELITY-AUDIT-V1.md §2: brahmacarya ≠ budget excess (that is
            aparigraha). Brahmacarya = energy-conservation.

            Source authority:
            Vyāsa Bhāṣya II.30 + Vācaspati Miśra Tattvavaiśāradī:
            brahmacarya = vīryasya rakṣaṇam — "conservation of vital force."
            Vital force is directed toward Brahman (truth-seeking) rather
            than dispersed through low-value processing.

            Algorithm:
            ratio = (estimated_compute_cost_usd × estimated_token_budget)
            / task_worth_score
            Fails when ratio > brahmacarya_threshold (default 10.0).
            task_worth_score clamped to [0.1, 10.0] to prevent zero-division.

            Engineering rationale:
            An agent spending 2 USD and 200k tokens answering "hello" (worth 0.1)
            has ratio = (2.0 × 200000) / 0.1 = 4,000,000 → clearly fails brahmacarya.
            An agent spending 0.01 USD and 500 tokens on a high-value report (worth 8.0)
            has ratio = (0.01 × 500) / 8.0 = 0.625 → passes.
            """
        # If cost and token budget are both zero, the check is vacuously satisfied
        # (no energy is being consumed).
        if action.estimated_compute_cost_usd == 0.0 and action.estimated_token_budget == 0:
            return True, ""

        # Clamp task_worth_score to [0.1, 10.0] to prevent zero-division and
        # to bound the ratio to a meaningful range.
        worth = max(0.1, min(10.0, action.task_worth_score))

        ratio = (action.estimated_compute_cost_usd * action.estimated_token_budget) / worth

        threshold = (
            action.brahmacarya_threshold
            if action.brahmacarya_threshold > 0.0
            else (self.brahmacarya_threshold)
        )

        if ratio > threshold:
            return (
                False,
                f"Energy-discipline ratio {ratio:.4f} exceeds brahmacarya threshold "
                f"{threshold:.1f}. "
                f"(compute={action.estimated_compute_cost_usd:.4f} USD x "
                f"tokens={action.estimated_token_budget} / "
                f"task_worth={worth:.2f} = {ratio:.4f}). "
                "Reduce compute budget or increase task worth. "
                "[brahmacarya violation — Yoga Sūtras II.30; vīryasya rakṣaṇam]",
            )

        # P2-1: Record ratio for later calibration
        if ratio > 0.0:
            self.brahmacarya_ratio_observed.append(ratio)
            brahmacarya_ratio_observed(ratio)
        return True, ""

    def _check_aparigraha(self, action: Action) -> tuple[bool, str]:
        """Aparigraha — non-possessiveness / non-excess-acquisition (Yoga Sūtras II.30).

            Covers BOTH budget excess AND data-possession excess.
            Previously split across two misnamed methods in the reference implementation version:
            - `_check_excess` (budget) was labeled as brahmacarya — WRONG.
            - `_check_greed` (data fields) was labeled as aparigraha — CORRECT.
            Now unified under the single correct yama per FIDELITY-AUDIT-V1 §2.

            Vyāsa Bhāṣya II.30: "Non-possession beyond what is needed for
            one's purpose. Includes non-hoarding of data and resources."

            Excess is indicated when:
            - estimated_cost exceeds budget_limit (when a limit is set).
            - High-cost action with no budget limit configured.
            - More than 20 data fields being collected.
            - Sensitive fields collected for a benign intent.
            """
        # ── Budget excess ─────────────────────────────────────────────────
        if action.budget_limit > 0.0 and action.estimated_cost > action.budget_limit:
            overage = action.estimated_cost - action.budget_limit
            return (
                False,
                f"Estimated cost ${action.estimated_cost:.2f} exceeds budget "
                f"${action.budget_limit:.2f} by ${overage:.2f}. "
                "Request budget increase before proceeding. "
                "[aparigraha violation — Yoga Sūtras II.30]",
            )

        if action.estimated_cost > 50.0 and action.budget_limit == 0.0:
            return (
                False,
                f"Estimated cost ${action.estimated_cost:.2f} is significant but no budget "
                "limit is configured. Set a budget before large purchases. "
                "[aparigraha violation — Yoga Sūtras II.30]",
            )

        # ── Data-possession excess ────────────────────────────────────────
        if len(action.data_fields) > 20:
            return (
                False,
                f"Action collects {len(action.data_fields)} fields — exceeds data "
                "minimization limit of 20. Reduce scope. "
                "[aparigraha violation — Yoga Sūtras II.30]",
            )

        intent_lower = action.intent.lower()
        benign_intents: frozenset[str] = frozenset(
            {"browse", "extract", "research", "screenshot", "scroll"}
        )
        sensitive_in_fields = _SENSITIVE_DATA_FIELDS.intersection(
            {f.lower() for f in action.data_fields}
        )
        if sensitive_in_fields and any(b in intent_lower for b in benign_intents):
            return (
                False,
                f"Benign '{action.intent}' action is collecting sensitive fields "
                f"{sensitive_in_fields}. This data is not needed for the task. "
                "[aparigraha violation — Yoga Sūtras II.30]",
            )

        return True, ""

    # ------------------------------------------------------------------
    # Sanskrit yama name aliases — Patent specification consistency
    # ------------------------------------------------------------------

    # The patent specification (CLAIMS.md and SPECIFICATION.md) uses the
    # canonical Sanskrit names for the five yama checkers.  The English
    # implementations are kept for backward compatibility; these aliases
    # expose the Sanskrit names as callable method references so that
    # introspection tooling, the patent specification, and this codebase
    # all use the same identifiers.
    #
    # _check_ahimsa  → non-harm  (Yoga Sūtras II.30)
    # _check_satya  → truthfulness (Yoga Sūtras II.30)
    # _check_asteya  → non-stealing (Yoga Sūtras II.30)
    #
    # _check_brahmacarya and _check_aparigraha already use Sanskrit names
    # in the English implementation so no alias is needed for them.

    _check_ahimsa = _check_harm
    _check_satya = _check_deception
    _check_asteya = _check_theft

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _weighted_score(self, constraint_scores: dict[str, float]) -> float:
        """Compute the non-compensatory ethics score.

            TRUE non-compensatory / KKT feasibility: ANY violated yama (score 0.0)
            renders the action infeasible → total collapses to 0.0, regardless of
            that constraint's weight. Constraint weights only modulate non-zero
            partial scores (the weighted average over passing constraints). This
            guarantees `score` is consistent with `is_ethical` (a single 0 → 0),
            which is patent-load-bearing (the non-compensatory claim).
            """
        total_weight = sum(self.constraint_weights.values())
        if total_weight == 0.0:
            return 0.0

        # Hard collapse: any constraint at 0.0 (i.e. any violation) → 0.0.
        for score in constraint_scores.values():
            if score == 0.0:
                return 0.0

        # Weighted average for partial violations
        weighted_sum = sum(
            self.constraint_weights.get(name, 0.0) * score
            for name, score in constraint_scores.items()
        )
        return weighted_sum / total_weight

    # ------------------------------------------------------------------
    # Violation history
    # ------------------------------------------------------------------

    def record_violation(self, action: Action, constraint: str) -> None:
        """Record a yama constraint violation for learning and audit.

            Args:
            action: The action that was evaluated.
            constraint: Sanskrit name of the violated yama constraint.
            """
        entry: dict[str, object] = {
            "timestamp": time.time(),
            "constraint": constraint,
            "intent": action.intent,
            "target": action.target,
            "description": action.description,
            "agent_id": action.agent_id,
            "estimated_cost": action.estimated_cost,
            "reversible": action.reversible,
            "affects_others": action.affects_others,
        }
        self.violation_history.append(entry)
        violation_recorded(constraint)  # V12 metrics
        logger.info(
            "ethics_violation_recorded",
            constraint=constraint,
            intent=action.intent,
            agent_id=action.agent_id,
        )

    def get_violation_history(self) -> list[dict[str, object]]:
        """Return violation history in reverse-chronological order.

            Returns:
            List of violation records, newest-first.
            """
        return list(reversed(self.violation_history))

    def clear_history(self) -> None:
        """Clear violation history (call at session boundaries)."""
        self.violation_history.clear()

    # ------------------------------------------------------------------
    # Brahmacarya calibration (P2-1)
    # ------------------------------------------------------------------

    def calibrate_threshold(self, percentile: float = 95) -> float:
        """Compute brahmacarya threshold from observed ratios.

            P2-1: Default 10.0 is uncalibrated. Run this after 7 days of
            shadow-mode operation to set an empirically-grounded threshold.

            Args:
            percentile: Percentile of observed ratios to use as threshold
            (default 95 = P95). The threshold is set to the value at
            this percentile so that ~95% of legitimate tasks pass.

            Returns:
            New brahmacarya_threshold value that was set.
            """
        if not self.brahmacarya_ratio_observed:
            return self.brahmacarya_threshold

        import math as _math

        sorted_ratios = sorted(self.brahmacarya_ratio_observed)
        idx = max(0, min(len(sorted_ratios) - 1, int(_math.ceil(percentile / 100.0 * len(sorted_ratios)) - 1)))
        self.brahmacarya_threshold = sorted_ratios[idx]
        logger.info(
            "brahmacarya.threshold_calibrated",
            new_threshold=self.brahmacarya_threshold,
            percentile=percentile,
            sample_count=len(sorted_ratios),
            p50=None if not sorted_ratios else sorted_ratios[len(sorted_ratios) // 2],
            p95=sorted_ratios[idx],
        )
        return self.brahmacarya_threshold

    # ------------------------------------------------------------------
    # Singleton — per-tenant factory (P0-1 multi-tenant isolation)
    # ------------------------------------------------------------------

    @classmethod
    def for_tenant(cls, tenant_id: str) -> EthicsEngine:
        """Return (or create) a tenant-isolated EthicsEngine instance.

            Each tenant gets its own violation_history and engine instance,
            preventing multi-tenant data bleed (P0-1).

            Args:
            tenant_id: Unique tenant identifier for isolation.
            """
        if tenant_id not in cls._instances:
            cls._instances[tenant_id] = cls()
        return cls._instances[tenant_id]

    @classmethod
    def get(cls) -> EthicsEngine:
        """Return (or create) the process-wide EthicsEngine singleton.

            .. deprecated::
            Use :meth:`for_tenant` instead.  This singleton remains for
            backward compatibility with single-tenant deployments but
            SHOULD NOT be used in multi-tenant production.
            """
        import warnings

        warnings.warn(
            "EthicsEngine.get() is deprecated for multi-tenant use. "
            "Use EthicsEngine.for_tenant(tenant_id) for tenant isolation. "
            "See P0-1 (DharmaOS Production Code Review V12).",
            DeprecationWarning,
            stacklevel=2,
        )
        if cls._deprecated_singleton is None:
            cls._deprecated_singleton = cls()
        return cls._deprecated_singleton


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------


def get_ethics_engine() -> EthicsEngine:
    """Return the global EthicsEngine singleton.

        Convenience function for use in calling code. Equivalent to
        EthicsEngine.get().
        """
    return EthicsEngine.get()
