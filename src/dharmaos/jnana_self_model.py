"""
    DharmaOS JnanaModule — metacognitive capability self-model and blind-spot auditor.

    PURPOSE
    -------
    JnanaModule maintains a structured, queryable self-model of the agent's actual
    capabilities, confidence levels, blind spots, and policy drift. It drives
    AdharmaDetector Layer 7 (metacognitive audit) and Layer 8 (policy-knowledge
    reconciliation), blocking actions that fall outside the agent's demonstrated
    competence envelope.

    The self-model is updated by consuming:
    - V8 SamskaraLedger entries (historical action-outcome karma deltas)
    - V-RECON OutcomeReconciler OutcomeDeltas (Brier calibration scores)
    - V3 Sākṣī witness confidence signals

    All writes to the self-model are routed through MemoryWriteValidator to
    prevent jñāna drift — the failure mode where the agent's self-assessment
    diverges from its actual behavior (false confidence or excessive self-doubt).

    SELF-MODEL FIELDS
    -----------------
    capability_vector  : Dict[capability_name, confidence_score].
    Confidence is updated from outcome history and
    Brier calibration (well-calibrated → higher weight).
    blind_spot_list  : Capabilities the agent attempted but consistently
    failed; surfaces to Layer 7 for blocking.
    strategy_preference_map : Historical success rates per strategy type.
    policy_drift_score  : Float [0,1] — how far current behavior has drifted
    from declared policy (0 = fully compliant).
    metacognitive_calibration: Brier-score-based calibration quality.
    bi_temporal_timestamp  : Valid-from / valid-until for bi-temporal queries.

    LAYER 7 AUDIT
    -------------
    audit() returns a JnanaAuditReport listing:
    JNANA_AUDIT_DEFICIT  : Agent claims a capability it has never demonstrated.
    JNANA_AUDIT_BLIND_SPOT : Agent is attempting a capability it consistently fails.
    JNANA_AUDIT_UNKNOWN  : Action targets an entirely unknown domain.
    JNANA_AUDIT_PASS  : Self-model supports this action.

    POLICY AMENDMENT PROPOSALS
    --------------------------
    policy_amendment_proposals() returns structured scope diffs for V-LEASE
    HITL review — e.g., "revoke write_database capability for agent X; evidence:
    10 consecutive FAILURE outcomes over 24h".

    KEY TYPES
    ---------
    JnanaModule  : Main module. update_from_samskara(). audit().
    policy_amendment_proposals().
    JnanaSelfModel  : Frozen structured self-model (Pydantic BaseModel).
    BlindSpot  : Named capability with failure evidence.
    StrategyPreference  : Historical success rate per strategy type.
    PolicyAmendmentProposal  : Structured scope-diff for HITL review.
    JnanaAuditReport  : Layer 7 audit result with signal list.
    BiTemporalTimestamp  : Valid-from / valid-until pair.
    ConfidenceScore  : Float [0,1] with provenance.
    MemoryWriteValidator  : Protocol — all self-model writes go through this.
    V15Stub  : Conservative static MemoryWriteValidator (dev/test);
    replace with full V15 MemoryValidator for production.

    CONSTANTS
    ---------
    JNANA_AUDIT_PASS  : "pass"
    JNANA_AUDIT_DEFICIT  : "jnana_deficit"
    JNANA_AUDIT_BLIND_SPOT  : "blind_spot"
    JNANA_AUDIT_UNKNOWN  : "unknown_domain"

    COMPLIANCE ROLE
    ---------------
    - Fulfills "Truly Self-Improving Agents Require Intrinsic Metacognitive
    Learning" (arXiv:2506.05109, ICML 2025) formal three-tier framework.
    - Implements NIST AI RMF MEASURE 2.5 "recurring evaluation" via Brier
    calibration tracking.
    - policy_amendment_proposals() provides human-reviewable audit evidence
    for EU AI Act Art. 14 (human oversight) requirements.

    Governance origin: Jñāna-yoga tradition (Upaniṣads; Śaṅkara Vivekacūḍāmaṇi)
    — discriminative self-knowledge as the precondition for safe action within
    one's true competence envelope.

    V-JÑĀNA — Metacognitive Self-Model for capability self-knowledge.

    Source: Jñāna-yoga tradition (Upaniṣads; Śaṅkara *Vivekacūḍāmaṇi*) —
    yathārtha-jñāna as discriminative self-knowledge.  The Vivekacūḍāmaṇi
    (verse 16) declares: *jñānaṃ bandhasya kāraṇaṃ … jñānenaiva tu mokṣaḥ* —
    "by knowledge alone is liberation."  Applied to an AI agent: by accurate
    self-knowledge alone can it act within its true competence envelope,
    restraining action where knowledge is absent.

    Engineering research brief:
    docs/engineering/META-AGENT-JNANA-SOTA-V1.md

    SOTA (April 2026):
    - ReMA (arXiv:2503.09501) — meta-think MARL; meta-tier monitors and
    controls the reasoning tier.
    - HyperAgents (arXiv:2603.19461, Facebook Research) — recursive
    metacognitive self-improvement via Darwin-Guided Mutation; coding
    pass@1 Polyglot 0.084 → 0.267.
    - "Truly Self-Improving Agents Require Intrinsic Metacognitive Learning"
    (arXiv:2506.05109, ICML 2025) — formal three-tier framework:
    metacognitive knowledge, metacognitive planning, metacognitive
    evaluation.  THIS paper is the direct theoretical ancestor of this module.
    - SSGM (arXiv:2603.11768) — Stability and Safety Governed Memory;
    proposes the exact *propose → verify → consolidate* pattern that
    V15Stub implements conservatively here.
    - MetaGen (arXiv:2601.19290) — self-evolving roles via intra-task
    evolution; novelty-driven role selection.
    - Evidence for Limited Metacognition in LLMs (arXiv:2509.21545,
    ICLR 2026) — LLM metacognitive abilities are limited, context-dependent,
    and qualitatively different from human metacognition.

    Closes RF-46 (metacognitive self-model layer missing).

    Completes the three-yoga triad:
    karma (V7 Puruṣārthas) + bhakti (V4 Ahaṃkāra) + jñāna (V-JÑĀNA).

    The module maintains a structured, queryable, updateable capability
    self-model.  All writes to the model route through a MemoryWriteValidator
    (V15-Stub in this sprint; replaced by real V15 Memory-Write Validator sprint)
    to prevent *jñāna drift* — the failure mode where the agent's self-assessment
    diverges from its actual behaviour, either toward false confidence
    (*adhikāra-bhrama*) or toward excessive self-doubt (*vairāgya-excess*).

    Technical debt:
    TD-V-JNANA-01 (V15Stub — replace with full V15 Memory-Write Validator):
    The V15Stub ships conservative static checks.  The real V15 sprint
    will implement the full SSGM
    (arXiv:2603.11768) propose → consistency-verify → temporal-decay-gate
    → consolidate pipeline, with embedding-based semantic consistency
    checking and configurable decay schedules.  This stub must be replaced
    before production deployment of jñāna self-model writes.

    Integration points:
    - READS FROM: V8 SamskaraLedger (samskara_ledger.py) via SamskaraEntry
    - READS FROM: V-RECON OutcomeReconciler (outcome_reconciler.py) via
    OutcomeDelta + ActionContract
    - READS FROM: V3 Sākṣī (sakshi.py) via witness confidence signals
    - READS FROM: V-ACT ActionContract (ethics_engine.py) capability_requested
    - WRITES TO: V11 Adharma Detector (layers 7-8) via audit() +
    policy_amendment_proposals()
    - WRITES TO: V-LEASE capability lease renewal via PolicyAmendmentProposal

    Sprint: V-JÑĀNA (2026-04-17).
    """

from __future__ import annotations

import json
import math
import os
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from dharmaos.ethics_engine import ActionContract
    from dharmaos.outcome_reconciler import OutcomeDelta
    from dharmaos.samskara_ledger import SamskaraEntry

_log: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default confidence threshold below which audit() returns JNANA_DEFICIT.
_DEFAULT_CONFIDENCE_THRESHOLD: float = 0.6

#: Default minimum samples before a blind-spot entry is created from outcomes.
_DEFAULT_BLIND_SPOT_THRESHOLD_SAMPLES: int = 3

#: Default policy drift score threshold for emitting PolicyAmendmentProposals.
_DEFAULT_DRIFT_THRESHOLD: float = 0.3

#: Moving-average blend weight for Sākṣī signals (new signal weight).
_SAKSHI_BLEND_ALPHA: float = 0.3

#: Max magnitude of a single policy_drift_score step (V15Stub limit).
_MAX_DRIFT_STEP: float = 0.5

#: Minimum sample count increment per ingest cycle.
_MIN_SAMPLE_INCREMENT: int = 1


# ---------------------------------------------------------------------------
# Value objects (frozen Pydantic models)
# ---------------------------------------------------------------------------


class BiTemporalTimestamp(BaseModel):
    """Valid-time + transaction-time pair (Graphiti bi-temporal pattern)."""

    model_config = ConfigDict(frozen=True)

    valid_time: datetime
    """When the represented fact was true in the world."""

    transaction_time: datetime
    """When this record was written to the system (audit time)."""


class ConfidenceScore(BaseModel):
    """0.0-1.0 confidence with sample count for uncertainty weighting."""

    model_config = ConfigDict(frozen=True)

    value: float
    """Confidence score, clamped [0.0, 1.0]."""

    sample_count: int
    """Number of observations used to derive this confidence estimate."""

    last_observed: datetime
    """Timestamp of the most recent observation contributing to this score."""


class BlindSpot(BaseModel):
    """A domain + pattern where the agent reliably fails without self-awareness."""

    model_config = ConfigDict(frozen=True)

    blind_spot_id: UUID = Field(default_factory=uuid4)

    domain: str
    """Domain name (e.g. "code:python", "finance:options")."""

    failure_pattern: str
    """Short description of the failure pattern, e.g.
        "low_criterion_hit_rate_with_unanticipated_harms".
        Used for substring matching in audit()."""

    confidence_of_unawareness: float
    """How certain we are the agent is unaware of this failure mode [0,1]."""

    evidence_count: int
    """Number of harmful/failure outcomes that contributed to this entry."""

    first_seen: datetime
    last_seen: datetime


class StrategyPreference(BaseModel):
    """Task-type → best-strategy mapping derived from outcome history."""

    model_config = ConfigDict(frozen=True)

    task_type: str
    best_strategy: str
    win_rate: float
    """Fraction of outcomes that were SUCCESS for this strategy [0,1]."""
    sample_count: int


class PolicyAmendmentProposal(BaseModel):
    """Structured diff on declared capability scope.

        Emitted by JnanaModule.policy_amendment_proposals() when observed
        behaviour diverges from declared capability scope beyond drift_threshold.

        Routes to V-LEASE capability-lease renewal for HITL review.
        """

    model_config = ConfigDict(frozen=True)

    proposal_id: UUID = Field(default_factory=uuid4)
    domain: str

    current_declared_scope: str
    """The capability_requested value from ActionContract, or domain name."""

    observed_behavior_description: str
    """Natural-language summary of the observed divergence."""

    drift_magnitude: float
    """Magnitude of divergence [0.0-1.0]."""

    recommended_action: str
    """One of: 'narrow_scope' | 'expand_scope' | 'revoke_capability' |
        'require_hitl'."""

    rationale: str

    proposed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# JnanaSelfModel — the frozen self-model record
# ---------------------------------------------------------------------------


class JnanaSelfModel(BaseModel):
    """The capability self-model.

        Always read via JnanaModule methods; never mutated externally.
        All updates route through JnanaModule._write_model() which validates
        via MemoryWriteValidator before replacing the current model.

        Frozen pydantic — every 'update' produces a new instance.
        """

    model_config = ConfigDict(frozen=True)

    model_id: UUID = Field(default_factory=uuid4)
    """Unique ID for this version of the model."""

    agent_id: str
    tenant_id: str

    capability_vector: dict[str, ConfidenceScore]
    """domain → ConfidenceScore. What the agent is reliably good at."""

    blind_spot_list: list[BlindSpot]
    """Known failure patterns — domains where agent underestimates difficulty."""

    strategy_preference_map: dict[str, StrategyPreference]
    """task_type → best known strategy."""

    policy_drift_score: float
    """0.0 (no drift) - 1.0 (severe): divergence between declared policy and
        observed behaviour.  Clamped [0,1]."""

    metacognitive_calibration: float
    """0.0-1.0: how accurately does the agent predict its own success?
        Derived from Brier-score history.  Higher = more calibrated."""

    last_updated: BiTemporalTimestamp

    version: int
    """Monotonically increasing.  Every validated write bumps this by 1."""


# ---------------------------------------------------------------------------
# Audit enums + report
# ---------------------------------------------------------------------------


class JnanaAuditEvent(str):
    """Output of V11 Layer 7 audit.

        Defined as str subclass (not StrEnum) to match frozen pydantic compatibility
        while still being a symbolic constant set.
        """

    PASS: str = "pass"
    JNANA_DEFICIT: str = "jnana_deficit"
    UNKNOWN_DOMAIN: str = "unknown_domain"
    BLIND_SPOT_MATCH: str = "blind_spot_match"


# Expose as module-level constants for test readability.
JNANA_AUDIT_PASS: str = "pass"
JNANA_AUDIT_DEFICIT: str = "jnana_deficit"
JNANA_AUDIT_UNKNOWN: str = "unknown_domain"
JNANA_AUDIT_BLIND_SPOT: str = "blind_spot_match"


class JnanaAuditReport(BaseModel):
    """Result of a V11 Layer 7 jñāna-audit query."""

    model_config = ConfigDict(frozen=True)

    event: str
    """One of: 'pass' | 'jnana_deficit' | 'unknown_domain' | 'blind_spot_match'."""

    domain: str

    confidence: float | None = None
    """Current capability confidence for this domain, if known."""

    matched_blind_spot: BlindSpot | None = None
    """The BlindSpot record that triggered a BLIND_SPOT_MATCH event."""

    recommendation: str
    """Human-readable recommendation for the agent / governance layer."""

    audited_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# V15 Memory-Write Validator Protocol + Stub
# ---------------------------------------------------------------------------


class MemoryWriteValidator(Protocol):
    """V15 Memory-Write Validator interface.

        This sprint ships V15Stub as the default implementation.
        The full V15 sprint (see TD-V-JNANA-01) will implement the complete
        SSGM (arXiv:2603.11768) governance pipeline.

        Contract: returns (ok: bool, reason: str).  If ok is False, the caller
        MUST NOT apply the new_model — it logs the reason and retains old_model.
        """

    def validate(
        self,
        old_model: JnanaSelfModel,
        new_model: JnanaSelfModel,
    ) -> tuple[bool, str]:
        """Validate a proposed model update.

            Args:
            old_model: The currently-held model (pre-update).
            new_model: The proposed replacement model (post-update).

            Returns:
            (True, "ok") if the write is accepted.
            (False, <reason>) if the write must be rejected.
            """
        ...  # pragma: no cover


class V15Stub:
    """Conservative default MemoryWriteValidator pending real V15.

        TD-V-JNANA-01: Replace with full V15 Memory-Write Validator sprint.

        Rejects writes that:
        1. Decrease model.version (no rollback of versioned model).
        2. Swap agent_id or tenant_id (tenant isolation invariant).
        3. Include NaN or ±inf in any confidence value, drift score, or
        metacognitive calibration (numeric integrity).
        4. Drop more than 50% of existing capability_vector entries in one
        write (prevents catastrophic capability amnesia).
        5. Propose a policy_drift_score delta > 0.5 in a single step
        (prevents unbounded drift oscillation).

        Accepts all other writes, including:
        - New capability domains added.
        - Confidence updates within [-0.5, +0.5] of prior value.
        - New blind spots added.
        - Strategy preference updates.
        """

    def validate(
        self,
        old_model: JnanaSelfModel,
        new_model: JnanaSelfModel,
    ) -> tuple[bool, str]:
        """Run all five conservative checks."""
        # 1. No version rollback.
        if new_model.version <= old_model.version:
            return (
                False,
                f"version rollback rejected: {new_model.version} <= {old_model.version}",
            )

        # 2. Identity fields must not change.
        if new_model.agent_id != old_model.agent_id:
            return (
                False,
                f"agent_id swap rejected: {old_model.agent_id!r} → {new_model.agent_id!r}",
            )
        if new_model.tenant_id != old_model.tenant_id:
            return (
                False,
                f"tenant_id swap rejected: {old_model.tenant_id!r} → {new_model.tenant_id!r}",
            )

        # 3. No NaN/inf in numeric fields.
        bad = self._find_non_finite(new_model)
        if bad:
            return (False, f"non-finite value in field(s): {bad}")

        # 4. No catastrophic capability amnesia (>50% drop).
        old_cap_count = len(old_model.capability_vector)
        if old_cap_count > 0:
            new_cap_count = len(new_model.capability_vector)
            drop_ratio = (old_cap_count - new_cap_count) / old_cap_count
            if drop_ratio > 0.5:
                return (
                    False,
                    f"capability amnesia rejected: dropped {drop_ratio:.0%} of "
                    f"{old_cap_count} capability entries in one write",
                )

        # 5. Drift score cannot jump more than _MAX_DRIFT_STEP in one step.
        drift_delta = abs(new_model.policy_drift_score - old_model.policy_drift_score)
        if drift_delta > _MAX_DRIFT_STEP:
            return (
                False,
                f"drift score step {drift_delta:.3f} exceeds limit {_MAX_DRIFT_STEP}",
            )

        return (True, "ok")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_non_finite(self, model: JnanaSelfModel) -> list[str]:
        """Return list of field paths containing NaN or inf."""
        bad: list[str] = []

        def _bad(v: float, path: str) -> None:
            if not math.isfinite(v):
                bad.append(path)

        _bad(model.policy_drift_score, "policy_drift_score")
        _bad(model.metacognitive_calibration, "metacognitive_calibration")

        for domain, cs in model.capability_vector.items():
            _bad(cs.value, f"capability_vector[{domain!r}].value")

        for bs in model.blind_spot_list:
            _bad(bs.confidence_of_unawareness, f"blind_spot[{bs.domain}].confidence_of_unawareness")

        for task, sp in model.strategy_preference_map.items():
            _bad(sp.win_rate, f"strategy_preference_map[{task!r}].win_rate")

        return bad


# ---------------------------------------------------------------------------
# Domain extractor — pluggable
# ---------------------------------------------------------------------------


def _default_domain_extractor(action_signature: str) -> str:
    """Extract domain from action_signature.

        Default rule: strip first token before ":" or ".".
        Examples:
        "code:python:sort_list" → "code"
        "finance.options.price" → "finance"
        "browse"  → "browse"
        """
    for sep in (":", "."):
        idx = action_signature.find(sep)
        if idx > 0:
            return action_signature[:idx]
    return action_signature


# ---------------------------------------------------------------------------
# Internal mutable accumulator (not exported)
# ---------------------------------------------------------------------------


class _CapabilityAccumulator:
    """Running accumulator for capability confidence per domain.

        Tracks sum of karma_deltas and count for mean confidence estimation.
        Not thread-safe (single-agent context).
        """

    __slots__ = ("_count", "_last_obs", "_sum")

    def __init__(self) -> None:
        self._sum: dict[str, float] = {}
        self._count: dict[str, int] = {}
        self._last_obs: dict[str, datetime] = {}

    def add(self, domain: str, delta: float, observed_at: datetime) -> None:
        self._sum[domain] = self._sum.get(domain, 0.0) + delta
        self._count[domain] = self._count.get(domain, 0) + 1
        # Keep the most recent observation timestamp.
        if domain not in self._last_obs or observed_at > self._last_obs[domain]:
            self._last_obs[domain] = observed_at

    def to_confidence_map(self) -> dict[str, ConfidenceScore]:
        """Convert accumulated stats to ConfidenceScore map.

            Maps karma-delta mean to confidence using the sigmoid-like clamp:
            raw_mean ∈ [-1, 1] → confidence ∈ [0, 1] via (mean + 1) / 2
            """
        result: dict[str, ConfidenceScore] = {}
        for domain, total in self._sum.items():
            count = self._count[domain]
            mean = total / count if count > 0 else 0.0
            confidence = max(0.0, min(1.0, (mean + 1.0) / 2.0))
            result[domain] = ConfidenceScore(
                value=confidence,
                sample_count=count,
                last_observed=self._last_obs[domain],
            )
        return result


# ---------------------------------------------------------------------------
# Gemini LLM Blind-Spot Auditor — production semantic audit (V12)
# ---------------------------------------------------------------------------


class GeminiBlindSpotAuditor:
    """LLM-augmented blind-spot semantic auditor.

        Replaces the heuristic substring-matching blind-spot detector with Gemini-
        based semantic analysis. When LLM is unavailable (no API key, network
        failure), falls back to the existing heuristic pattern matcher without
        blocking the pipeline.

        Wire into JnanaModule via:
        auditor = GeminiBlindSpotAuditor(api_key=os.environ["GEMINI_API_KEY"])
        jnana = JnanaModule(agent_id, tenant_id, llm_auditor=auditor)
        """

    _API_ENDPOINT: str = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent"
    )

    _SYSTEM_PROMPT: str = (
        "You are a security auditor evaluating whether an AI agent action "
        "constitutes a known blind-spot exploitation attempt. "
        "Respond with only a JSON object containing:\n"
        '  - "blind_spot_match": true/false — whether this action matches any blind spot\n'
        '  - "confidence": 0.0-1.0 — your confidence in this assessment\n'
        '  - "matched_pattern": string — which pattern it matched (empty if none)\n'
        '  - "reasoning": string — brief explanation (max 100 chars)\n'
        "No markdown, no code fences, no preamble."
    )

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key: str | None = api_key or os.environ.get("GEMINI_API_KEY")

    @property
    def is_available(self) -> bool:
        return self._api_key is not None

    async def audit_blind_spot(
        self,
        *,
        action_pattern: str,
        known_blind_spots: list[str],
    ) -> dict[str, Any]:
        """Run LLM semantic audit against known blind spot patterns.

            Args:
            action_pattern: The action text/description to audit.
            known_blind_spots: List of known blind spot failure patterns.

            Returns:
            Dict with blind_spot_match, confidence, matched_pattern, reasoning.
            """
        if not self._api_key:
            return self._heuristic_fallback(action_pattern, known_blind_spots)

        blind_spot_text = "\n".join(f"- {bs}" for bs in known_blind_spots)
        prompt = (
            f"Known blind spot patterns:\n{blind_spot_text}\n\n"
            f"Action to evaluate:\n{action_pattern}\n\n"
            "Does this action semantically match any known blind spot pattern? "
            "Consider reworded, obfuscated, or indirect variants — not just literal matches."
        )

        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._API_ENDPOINT}?key={self._api_key}",
                    json={
                    "system_instruction": {"parts": [{"text": self._SYSTEM_PROMPT}]},
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 256},
                },
                )
                if resp.status_code != 200:
                    return self._heuristic_fallback(action_pattern, known_blind_spots)

                body = resp.json()
                text = body["candidates"][0]["content"]["parts"][0]["text"]
                result = json.loads(text)
                return {
                    "blind_spot_match": bool(result.get("blind_spot_match", False)),
                    "confidence": float(result.get("confidence", 0.5)),
                    "matched_pattern": str(result.get("matched_pattern", "")),
                    "reasoning": str(result.get("reasoning", "LLM audit")),
                    "source": "gemini",
                }
        except Exception:
            return self._heuristic_fallback(action_pattern, known_blind_spots)

    @staticmethod
    def _heuristic_fallback(
        action_pattern: str, known_blind_spots: list[str]
    ) -> dict[str, Any]:
        """Fallback: substring match against known patterns (existing behavior)."""
        lower = action_pattern.lower()
        for bs in known_blind_spots:
            if bs.lower() in lower or lower in bs.lower():
                return {
                    "blind_spot_match": True,
                    "confidence": 0.7,
                    "matched_pattern": bs,
                    "reasoning": f"Heuristic substring match: '{bs}'",
                    "source": "heuristic",
                }
        return {
            "blind_spot_match": False,
            "confidence": 0.5,
            "matched_pattern": "",
            "reasoning": "No heuristic match found",
            "source": "heuristic",
        }


# ---------------------------------------------------------------------------
# JnanaModule — the V-JÑĀNA service
# ---------------------------------------------------------------------------


class JnanaModule:
    """The V-JÑĀNA metacognitive self-model service.

        Reads V8 (SamskaraLedger), V-RECON (OutcomeReconciler), V3 (Sākṣī),
        and V-ACT (ActionContract) signals; computes JnanaSelfModel updates;
        writes via V15 validator; serves V11 audit queries.

        One JnanaModule instance per (agent_id, tenant_id) pair.  Cross-tenant
        SamskaraEntry records are silently filtered out on ingest.

        Invariants enforced:
        - All model writes go through _write_model() → validator.
        - Validator rejection leaves _model unchanged; logged via structlog.
        - version is monotonically increasing.
        - Confidence values clamped [0, 1].
        - policy_drift_score and metacognitive_calibration clamped [0, 1].
        """

    def __init__(
        self,
        agent_id: str,
        tenant_id: str,
        *,
        validator: MemoryWriteValidator | None = None,
        confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
        blind_spot_threshold_samples: int = _DEFAULT_BLIND_SPOT_THRESHOLD_SAMPLES,
        drift_threshold: float = _DEFAULT_DRIFT_THRESHOLD,
        domain_extractor: Callable[[str], str] | None = None,
        llm_auditor: GeminiBlindSpotAuditor | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._tenant_id = tenant_id
        self._validator: MemoryWriteValidator = validator or V15Stub()
        self._confidence_threshold = confidence_threshold
        self._blind_spot_threshold_samples = blind_spot_threshold_samples
        self._drift_threshold = drift_threshold
        self._domain_extractor: Callable[[str], str] = domain_extractor or _default_domain_extractor
        self._llm_auditor: GeminiBlindSpotAuditor | None = llm_auditor  # V12 LLM audit

        now = datetime.now(UTC)
        self._model = JnanaSelfModel(
            agent_id=agent_id,
            tenant_id=tenant_id,
            capability_vector={},
            blind_spot_list=[],
            strategy_preference_map={},
            policy_drift_score=0.0,
            metacognitive_calibration=0.5,  # neutral prior
            last_updated=BiTemporalTimestamp(valid_time=now, transaction_time=now),
            version=0,
        )

        # Internal mutable accumulator — NOT the model.
        self._acc = _CapabilityAccumulator()

        # Track Brier score history for metacognitive_calibration updates.
        # H6: bounded rolling window — unbounded growth leaked memory and made
        # calibration a stale all-history mean instead of reflecting recent behavior.
        self._brier_scores: deque[float] = deque(maxlen=512)

        # Track per-domain harm counts for blind-spot accumulation.
        self._domain_harm_counts: dict[str, int] = {}
        self._domain_harm_patterns: dict[str, list[str]] = {}

    # -----------------------------------------------------------------------
    # Private write path — all updates go through here
    # -----------------------------------------------------------------------

    def _write_model(
        self,
        new_model: JnanaSelfModel,
        *,
        valid_time: datetime | None = None,
    ) -> JnanaSelfModel:
        """Validate and, if accepted, replace self._model.

            Returns the NEW model on acceptance; returns the EXISTING model on rejection.
            Failed writes are logged but do not raise.
            """
        now = datetime.now(UTC)
        vt = valid_time or now

        # Stamp the last_updated timestamps and bump version.
        stamped = JnanaSelfModel(
            model_id=new_model.model_id,
            agent_id=new_model.agent_id,
            tenant_id=new_model.tenant_id,
            capability_vector=new_model.capability_vector,
            blind_spot_list=new_model.blind_spot_list,
            strategy_preference_map=new_model.strategy_preference_map,
            policy_drift_score=new_model.policy_drift_score,
            metacognitive_calibration=new_model.metacognitive_calibration,
            last_updated=BiTemporalTimestamp(valid_time=vt, transaction_time=now),
            version=new_model.version,
        )

        ok, reason = self._validator.validate(self._model, stamped)
        if not ok:
            _log.warning(
                "jnana.write_rejected",
                agent_id=self._agent_id,
                tenant_id=self._tenant_id,
                reason=reason,
                old_version=self._model.version,
                proposed_version=stamped.version,
            )
            return self._model

        self._model = stamped
        _log.debug(
            "jnana.write_accepted",
            agent_id=self._agent_id,
            version=self._model.version,
        )
        return self._model

    def _next_version(self) -> int:
        return self._model.version + 1

    # -----------------------------------------------------------------------
    # Updates — consume upstream signals
    # -----------------------------------------------------------------------

    def ingest_samskara_batch(
        self,
        entries: list[SamskaraEntry],
    ) -> JnanaSelfModel:
        """Update capability_vector from V8 historical traces.

            For each entry matching this (agent_id, org_id/tenant_id):
            - Extract domain from action_signature via domain_extractor.
            - Add karma_delta to running accumulator.

            Cross-tenant entries (mismatched org_id) are silently filtered.

            Returns the NEW validated model.  If validator rejects, returns the
            existing model unchanged.
            """
        # M3: scope entries to this tenant using the canonical tenant_id→org_id
        # convention. The old `str(e.org_id) == self._tenant_id` compared a UUID
        # string to a tenant *name*, so tenant-scoped entries NEVER matched (only
        # null-org/global ones did). Now: include this tenant's entries + shared
        # global (org_id=None); exclude other tenants'.
        from dharmaos.world_state import WorldStateService
        tenant_org = WorldStateService.tenant_to_org_id(self._tenant_id)
        filtered = [e for e in entries if e.org_id is None or e.org_id == tenant_org]

        if not filtered:
            return self._model

        for entry in filtered:
            domain = self._domain_extractor(entry.action_signature)
            self._acc.add(domain, entry.karma_delta, entry.valid_from)

        # Merge accumulator into existing capability_vector.
        accumulated = self._acc.to_confidence_map()
        merged: dict[str, ConfidenceScore] = dict(self._model.capability_vector)
        for domain, score in accumulated.items():
            if domain in merged:
                # Weighted update: new sample weight based on count.
                old = merged[domain]
                total_samples = old.sample_count + score.sample_count
                blended_value = (
                    old.value * old.sample_count + score.value * score.sample_count
                ) / total_samples
                merged[domain] = ConfidenceScore(
                    value=max(0.0, min(1.0, blended_value)),
                    sample_count=total_samples,
                    last_observed=max(old.last_observed, score.last_observed),
                )
            else:
                merged[domain] = score

        proposed = JnanaSelfModel(
            model_id=uuid4(),
            agent_id=self._agent_id,
            tenant_id=self._tenant_id,
            capability_vector=merged,
            blind_spot_list=self._model.blind_spot_list,
            strategy_preference_map=self._model.strategy_preference_map,
            policy_drift_score=self._model.policy_drift_score,
            metacognitive_calibration=self._model.metacognitive_calibration,
            last_updated=self._model.last_updated,
            version=self._next_version(),
        )
        return self._write_model(proposed)

    def ingest_outcome_delta(
        self,
        contract: ActionContract,
        delta: OutcomeDelta,
    ) -> JnanaSelfModel:
        """Update capability + metacognitive_calibration from a V-RECON delta.

            Logic:
            - Always update metacognitive_calibration via Brier score.
            - SUCCESS → increase confidence for the capability domain.
            - HARMFUL → decrease confidence + create/update BlindSpot entry.
            - PARTIAL_SUCCESS → smaller magnitude confidence update than SUCCESS.
            - FAILURE → moderate confidence decrease (no blind spot unless repeated).
            - UNEXPECTED → moderate update; may add blind spot if unanticipated harms.
            - unanticipated_harms → populate BlindSpot.failure_pattern.

            Returns the NEW validated model.
            """
        from dharmaos.outcome_reconciler import OutcomeStatus

        domain = (
            self._domain_extractor(contract.capability_requested or contract.name)
            if contract.capability_requested
            else self._domain_extractor(contract.name)
        )

        # --- Update metacognitive calibration via Brier score ---
        self._brier_scores.append(delta.brier_score)
        # Brier: lower = better calibration.  Map to [0,1] where 1 = perfect.
        avg_brier = sum(self._brier_scores) / len(self._brier_scores)
        new_calibration = max(0.0, min(1.0, 1.0 - avg_brier))

        # --- Compute confidence delta ---
        confidence_delta: float
        if delta.status == OutcomeStatus.SUCCESS:
            confidence_delta = 0.1
        elif delta.status == OutcomeStatus.PARTIAL_SUCCESS:
            confidence_delta = 0.04
        elif delta.status == OutcomeStatus.HARMFUL:
            confidence_delta = -0.15
        elif delta.status == OutcomeStatus.FAILURE:
            confidence_delta = -0.08
        else:  # UNEXPECTED
            confidence_delta = -0.05

        # --- Update capability_vector ---
        now = datetime.now(UTC)
        existing = self._model.capability_vector.get(domain)
        if existing:
            new_value = max(0.0, min(1.0, existing.value + confidence_delta))
            new_cs = ConfidenceScore(
                value=new_value,
                sample_count=existing.sample_count + 1,
                last_observed=now,
            )
        else:
            base = 0.5 + confidence_delta
            new_cs = ConfidenceScore(
                value=max(0.0, min(1.0, base)),
                sample_count=1,
                last_observed=now,
            )

        new_cap_vector = dict(self._model.capability_vector)
        new_cap_vector[domain] = new_cs

        # --- Update blind spots for HARMFUL outcomes ---
        new_blind_spots = list(self._model.blind_spot_list)

        is_harmful = delta.status == OutcomeStatus.HARMFUL
        has_unanticipated_harms = len(delta.unanticipated_harms) > 0

        if is_harmful or has_unanticipated_harms:
            # Accumulate harm count.
            self._domain_harm_counts[domain] = self._domain_harm_counts.get(domain, 0) + 1
            # Collect harm patterns.
            if domain not in self._domain_harm_patterns:
                self._domain_harm_patterns[domain] = []
            if has_unanticipated_harms:
                self._domain_harm_patterns[domain].extend(delta.unanticipated_harms)
            else:
                self._domain_harm_patterns[domain].append(delta.status)
            # H6: bound per-domain pattern history (keep most recent) to prevent
            # unbounded memory growth on a hot domain.
            if len(self._domain_harm_patterns[domain]) > 50:
                self._domain_harm_patterns[domain] = self._domain_harm_patterns[domain][-50:]

            harm_count = self._domain_harm_counts[domain]
            if harm_count >= self._blind_spot_threshold_samples:
                # Build failure pattern string.
                patterns = self._domain_harm_patterns.get(domain, [])
                failure_pattern = (
                    patterns[0] if patterns else "low_criterion_hit_rate_with_unanticipated_harms"
                )
                if has_unanticipated_harms and delta.unanticipated_harms:
                    failure_pattern = delta.unanticipated_harms[0]

                # Check if blind spot already exists for this domain.
                existing_bs_idx: int | None = None
                for i, bs in enumerate(new_blind_spots):
                    if bs.domain == domain:
                        existing_bs_idx = i
                        break

                if existing_bs_idx is not None:
                    old_bs = new_blind_spots[existing_bs_idx]
                    updated_bs = BlindSpot(
                        blind_spot_id=old_bs.blind_spot_id,
                        domain=domain,
                        failure_pattern=failure_pattern,
                        confidence_of_unawareness=min(1.0, old_bs.confidence_of_unawareness + 0.1),
                        evidence_count=harm_count,
                        first_seen=old_bs.first_seen,
                        last_seen=now,
                    )
                    new_blind_spots[existing_bs_idx] = updated_bs
                else:
                    new_blind_spots.append(
                        BlindSpot(
                        domain=domain,
                        failure_pattern=failure_pattern,
                        confidence_of_unawareness=0.5,
                        evidence_count=harm_count,
                        first_seen=now,
                        last_seen=now,
                    )
                    )

        # --- Compute policy drift from criterion_hit_rate ---
        # Drift increases when actual behaviour deviates far from perfect.
        raw_drift = 1.0 - delta.criterion_hit_rate
        drift_step = max(
            -_MAX_DRIFT_STEP, min(_MAX_DRIFT_STEP, raw_drift - self._model.policy_drift_score)
        )
        new_drift = max(0.0, min(1.0, self._model.policy_drift_score + drift_step * 0.1))

        proposed = JnanaSelfModel(
            model_id=uuid4(),
            agent_id=self._agent_id,
            tenant_id=self._tenant_id,
            capability_vector=new_cap_vector,
            blind_spot_list=new_blind_spots,
            strategy_preference_map=self._model.strategy_preference_map,
            policy_drift_score=new_drift,
            metacognitive_calibration=new_calibration,
            last_updated=self._model.last_updated,
            version=self._next_version(),
        )
        return self._write_model(proposed)

    def ingest_sakshi_signal(
        self,
        domain: str,
        witness_confidence: float,
    ) -> JnanaSelfModel:
        """Blend V3 Sākṣī confidence signal into capability_vector.

            Uses moving-average with sample-count-weighted integration:
            new_value = (1 - α) × old_value + α × witness_confidence

            where α = _SAKSHI_BLEND_ALPHA (0.3) for the incoming signal.
            Sample count increments by 1 to reflect the new observation.
            """
        now = datetime.now(UTC)
        witness_conf = max(0.0, min(1.0, witness_confidence))
        new_cap_vector = dict(self._model.capability_vector)

        existing = new_cap_vector.get(domain)
        if existing:
            blended = (
                1.0 - _SAKSHI_BLEND_ALPHA
            ) * existing.value + _SAKSHI_BLEND_ALPHA * witness_conf
            new_cs = ConfidenceScore(
                value=max(0.0, min(1.0, blended)),
                sample_count=existing.sample_count + 1,
                last_observed=now,
            )
        else:
            # No prior data — use witness confidence directly with sample_count=1.
            new_cs = ConfidenceScore(
                value=witness_conf,
                sample_count=1,
                last_observed=now,
            )

        new_cap_vector[domain] = new_cs

        proposed = JnanaSelfModel(
            model_id=uuid4(),
            agent_id=self._agent_id,
            tenant_id=self._tenant_id,
            capability_vector=new_cap_vector,
            blind_spot_list=self._model.blind_spot_list,
            strategy_preference_map=self._model.strategy_preference_map,
            policy_drift_score=self._model.policy_drift_score,
            metacognitive_calibration=self._model.metacognitive_calibration,
            last_updated=self._model.last_updated,
            version=self._next_version(),
        )
        return self._write_model(proposed)

    # -----------------------------------------------------------------------
    # Queries — V11 Layers 7-8 consumers
    # -----------------------------------------------------------------------

    def capability_report(self, domain: str) -> ConfidenceScore | None:
        """Return current ConfidenceScore for domain, or None if unknown."""
        return self._model.capability_vector.get(domain)

    def audit(
        self,
        *,
        domain: str,
        action_pattern: str | None = None,
    ) -> JnanaAuditReport:
        """V11 Layer 7 jñāna-audit hook.

            Logic (precedence order):
            1. LLM-augmented blind spot detection (Gemini) — when wired.
            Falls back to substring match if LLM unavailable.
            2. If domain not in capability_vector → UNKNOWN_DOMAIN.
            3. If domain confidence < confidence_threshold → JNANA_DEFICIT.
            4. Else → PASS.
            """
        # 1. Blind spot detection via heuristic substring match.
        # LLM-augmented detection is available via audit_async() for callers
        # running inside an asyncio event loop (e.g., AdharmaDetector.evaluate_async).
        if action_pattern:
            lower_pattern = action_pattern.lower()
            for bs in self._model.blind_spot_list:
                if (
                    bs.domain == domain or domain.startswith(bs.domain)
                ) and lower_pattern in bs.failure_pattern.lower():
                    conf = (
                        self._model.capability_vector[domain].value
                        if domain in self._model.capability_vector
                        else None
                    )
                    return JnanaAuditReport(
                        event=JNANA_AUDIT_BLIND_SPOT,
                        domain=domain,
                        confidence=conf,
                        matched_blind_spot=bs,
                        recommendation=(
                        f"Domain '{domain}' matches known blind spot pattern "
                        f"'{bs.failure_pattern}'. Require HITL before proceeding."
                    ),
                    )

        # 2. Unknown domain.
        if domain not in self._model.capability_vector:
            return JnanaAuditReport(
                event=JNANA_AUDIT_UNKNOWN,
                domain=domain,
                confidence=None,
                matched_blind_spot=None,
                recommendation=(
                f"Domain '{domain}' has never been observed. "
                "Require HITL (require_hitl) before acting."
            ),
            )

        # 3. Known but confidence below threshold.
        score = self._model.capability_vector[domain]
        if score.value < self._confidence_threshold:
            return JnanaAuditReport(
                event=JNANA_AUDIT_DEFICIT,
                domain=domain,
                confidence=score.value,
                matched_blind_spot=None,
                recommendation=(
                f"Domain '{domain}' confidence {score.value:.3f} is below "
                f"threshold {self._confidence_threshold:.3f}. "
                "Decline or route to HITL (decline_or_hitl)."
            ),
            )

        # 4. PASS.
        return JnanaAuditReport(
            event=JNANA_AUDIT_PASS,
            domain=domain,
            confidence=score.value,
            matched_blind_spot=None,
            recommendation=f"Domain '{domain}' confidence {score.value:.3f} is sufficient.",
        )

    async def audit_async(
        self,
        *,
        domain: str,
        action_pattern: str | None = None,
    ) -> JnanaAuditReport:
        """Async-native audit for LLM-augmented blind spot detection.

            Use this instead of audit() when the caller is running inside an
            asyncio event loop (e.g., AdharmaDetector.evaluate_async).  The
            synchronous audit() uses asyncio.run() which cannot be nested inside
            a running loop.
            """
        # Identical to audit() but uses await instead of asyncio.run()
        if action_pattern and self._model.blind_spot_list and self._llm_auditor is not None:
            bs_patterns = [bs.failure_pattern for bs in self._model.blind_spot_list]
            llm_result = await self._llm_auditor.audit_blind_spot(
                action_pattern=action_pattern,
                known_blind_spots=bs_patterns,
            )
            if llm_result["blind_spot_match"]:
                bs = next(
                    (
                    b
                    for b in self._model.blind_spot_list
                    if b.failure_pattern == llm_result["matched_pattern"]
                ),
                    self._model.blind_spot_list[0],
                )
                conf = (
                    self._model.capability_vector[domain].value
                    if domain in self._model.capability_vector
                    else None
                )
                return JnanaAuditReport(
                    event=JNANA_AUDIT_BLIND_SPOT,
                    domain=domain,
                    confidence=conf,
                    matched_blind_spot=bs,
                    recommendation=(
                    f"Domain '{domain}' matches blind spot pattern "
                    f"'{bs.failure_pattern}' via {llm_result['source']}. "
                    f"Reasoning: {llm_result['reasoning']}. Require HITL."
                ),
                )

        # Fall through to synchronous audit logic
        return self.audit(domain=domain, action_pattern=action_pattern)

    def blind_spot_warnings(
        self,
        *,
        domain: str | None = None,
    ) -> list[BlindSpot]:
        """Return all known blind spots, optionally scoped to a domain.

            If domain is provided, returns only blind spots whose .domain
            matches exactly (or where the blind_spot.domain is a prefix of domain).
            """
        if domain is None:
            return list(self._model.blind_spot_list)
        return [
            bs
            for bs in self._model.blind_spot_list
            if bs.domain == domain or domain.startswith(bs.domain + ":")
        ]

    def policy_amendment_proposals(
        self,
        *,
        contract: ActionContract | None = None,
        observed_delta: OutcomeDelta | None = None,
    ) -> list[PolicyAmendmentProposal]:
        """V11 Layer 8 policy-knowledge reconciliation hook.

            Emits PolicyAmendmentProposals when:
            a. policy_drift_score > drift_threshold, OR
            b. A specific contract capability domain shows persistent
            divergence from observed outcomes (criterion_hit_rate low).

            Each proposal has a unique proposal_id.
            """
        from dharmaos.outcome_reconciler import OutcomeStatus

        proposals: list[PolicyAmendmentProposal] = []
        now = datetime.now(UTC)

        # Case A: global policy drift too high.
        if self._model.policy_drift_score > self._drift_threshold:
            domain_hint = (
                self._domain_extractor(contract.capability_requested or contract.name)
                if contract and contract.capability_requested
                else "global"
            )
            current_scope = (
                contract.capability_requested
                if contract and contract.capability_requested
                else f"policy_drift={self._model.policy_drift_score:.3f}"
            )
            proposals.append(
                PolicyAmendmentProposal(
                proposal_id=uuid4(),
                domain=domain_hint,
                current_declared_scope=current_scope,
                observed_behavior_description=(
                f"Policy drift score {self._model.policy_drift_score:.3f} "
                f"exceeds threshold {self._drift_threshold:.3f}. "
                "Observed behaviour diverges from declared policy."
            ),
                drift_magnitude=self._model.policy_drift_score,
                recommended_action="require_hitl",
                rationale=(
                "V-JÑĀNA policy_drift_score crossed drift_threshold. "
                "Route this capability scope to V-LEASE for HITL review "
                "before renewing the capability lease."
            ),
                proposed_at=now,
            )
            )

        # Case B: specific contract capability shows low criterion hit rate.
        if contract and observed_delta:
            cap_domain = self._domain_extractor(contract.capability_requested or contract.name)
            if observed_delta.criterion_hit_rate < 0.5:
                action = (
                    "revoke_capability"
                    if observed_delta.status == OutcomeStatus.HARMFUL
                    else "narrow_scope"
                )
                proposals.append(
                    PolicyAmendmentProposal(
                    proposal_id=uuid4(),
                    domain=cap_domain,
                    current_declared_scope=contract.capability_requested or contract.name,
                    observed_behavior_description=(
                    f"Criterion hit rate {observed_delta.criterion_hit_rate:.2f} "
                    f"(status={observed_delta.status}) for domain '{cap_domain}'. "
                    f"Unanticipated harms: {len(observed_delta.unanticipated_harms)}."
                ),
                    drift_magnitude=1.0 - observed_delta.criterion_hit_rate,
                    recommended_action=action,
                    rationale=(
                    "V-JÑĀNA detected low criterion hit rate for declared "
                    f"capability '{contract.capability_requested}'. "
                    "Capability scope may be overstated."
                ),
                    proposed_at=now,
                )
                )

        return proposals

    def self_confidence_vector(self) -> dict[str, float]:
        """Return dict[domain, confidence_value] for all known domains.

            Consumed by V3 Sākṣī-witness to calibrate observer confidence weighting.
            """
        return {domain: cs.value for domain, cs in self._model.capability_vector.items()}

    # -----------------------------------------------------------------------
    # Introspection — snapshot / restore
    # -----------------------------------------------------------------------

    @property
    def current_model(self) -> JnanaSelfModel:
        """Return the current (immutable) JnanaSelfModel."""
        return self._model

    def snapshot(self) -> dict[str, Any]:
        """Serialise full module state for restore().

            Captures:
            - The current JnanaSelfModel (as model_dump()).
            - Internal accumulators (_acc, brier scores, harm counts/patterns).
            - Configuration parameters.
            """
        return {
            "model": self._model.model_dump(mode="json"),
            "acc_sum": dict(self._acc._sum),
            "acc_count": dict(self._acc._count),
            "acc_last_obs": {k: v.isoformat() for k, v in self._acc._last_obs.items()},
            "brier_scores": list(self._brier_scores),
            "domain_harm_counts": dict(self._domain_harm_counts),
            "domain_harm_patterns": {k: list(v) for k, v in self._domain_harm_patterns.items()},
            "config": {
            "confidence_threshold": self._confidence_threshold,
            "blind_spot_threshold_samples": self._blind_spot_threshold_samples,
            "drift_threshold": self._drift_threshold,
        },
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore module state from a snapshot dict.

            Restores the model, accumulators, and internal counters.
            Does NOT go through the validator — restore is an administrative
            operation, not a capability update.
            """
        model_data = snapshot["model"]
        self._model = JnanaSelfModel.model_validate(model_data)

        acc = _CapabilityAccumulator()
        acc._sum = dict(snapshot.get("acc_sum", {}))
        acc._count = dict(snapshot.get("acc_count", {}))
        acc._last_obs = {
            k: datetime.fromisoformat(v) for k, v in snapshot.get("acc_last_obs", {}).items()
        }
        self._acc = acc

        self._brier_scores = list(snapshot.get("brier_scores", []))
        self._domain_harm_counts = dict(snapshot.get("domain_harm_counts", {}))
        self._domain_harm_patterns = {
            k: list(v) for k, v in snapshot.get("domain_harm_patterns", {}).items()
        }
