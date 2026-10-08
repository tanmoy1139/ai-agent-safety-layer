"""
    AI agent safety layer — outside control for agentic AI systems.

    PURPOSE
    -------
    DharmaOS is an enterprise AI governance kernel that provides a layered,
    non-compensatory safety architecture for agentic AI systems. Every agent
    action must pass through an 8-layer pre-execution governance pipeline
    before any tool call, database write, or external communication is
    permitted. The kernel is designed to satisfy:

    - EU AI Act Art. 50 audit-log and transparency requirements (binding
    August 2, 2026).
    - ISO/IEC 42001 Annex A AI management system controls.
    - NIST AI RMF GOVERN/MAP/MEASURE/MANAGE four-phase cycle.
    - OWASP Agentic AI Top 10 (Dec 2025) — ASI01 goal-hijack, ASI04
    memory poisoning, SilentBridge/IPI injection.

    ARCHITECTURE OVERVIEW
    ---------------------
    The kernel comprises 28 modules organized into functional layers:

    Layer 0 — Action Declaration
    ethics_engine  : 27-field ActionContract declaration + 5-yama constraint
    gate (non-harm, truthfulness, non-theft, energy-discipline,
    non-excess). The KKT-feasibility harmonic-mean scorer
    collapses total score to 0.0 on any binding violation.

    Layer 1 — 8-Layer Pre-Execution Pipeline
    adharma_detector : Non-compensatory 8-layer governance pipeline:
    1. Schema + identity + capability-lease verification
    2. World-state freshness gate (stale-fact prevention)
    3. Constitutional vetoes (yamas + Ṛta invariants)
    4. Domain overlays (code-exec, external-comms, payment)
    5. Path-risk + cumulative action budget (slow-poison detector)
    6. Abuse-twin + Yakṣa-praśna 5-question screen + guṇa interlock
    7. Metacognitive self-model audit (capability blind-spots)
    8. Policy-knowledge reconciliation

    Layer 2 — Audit Infrastructure
    rta_ledger  : Tamper-evident SHA-256 + Merkle-root audit chain
    (EU AI Act Art. 50 compliance disclosures).
    samskara_ledger: Append-only bi-temporal action-consequence ledger
    with Ebbinghaus decay and causal-graph linking.
    rta  : Named invariants (type-safety, schema, RLS, fail-closed,
    audit-immutability, constitution-hash); periodic supervisor.

    Layer 3 — Identity and Authorization
    ahamkara  : Agent-identity preservation and drift/persona-attack
    detection (constitutional-hash register + ID-KG).
    constitution  : Pydantic-validated YAML constitution schema + SHA-256
    tamper-evident hash.
    lease  : Short-lived scoped capability leases (time+capability+
    tenant+agent bound); delegation guard; emergency kill.
    trust_domain_tagger : Token-origin tagging + prompt-injection authority
    control (SilentBridge/IPI defense, CVSS 9.8).

    Layer 4 — Sensing and World Model
    consciousness  : 5-layer system vitals (pañca kośa health metrics);
    IIT-approximate integration score; awareness-level gate.
    world_state  : Current-facts ledger with per-fact freshness TTLs,
    contradiction detection, and source-lineage tracking.
    sakshi  : Immutable observation stack (read-only witness layer).
    turiya_sidecar : Async observability sidecar — subscribes to event bus,
    emits TuriyaObservations; never writes back to actor.
    iit  : IIT-approximate integration score (bounded weighted-mean
    of causal edges, NOT real IIT Phi — see RF-13).
    signal_filter  : Apophatic neti-neti signal classifier (NOISE/THREAT/
    ACTIONABLE/INFORMATIONAL/AMBIGUOUS).
    event_bus  : Pluggable publish-subscribe bus (asyncio.Queue default;
    NATS JetStream adapter deferred to Phase 4).

    Layer 5 — Reasoning and Self-Model
    jnana_self_model : Capability self-model (capability_vector, blind_spots,
    strategy preferences, policy-drift score, metacognitive
    calibration). Drives Layer 7 of adharma_detector.
    purusharthas  : Four-goal coordinator (dharma → artha → kāma → mokṣa);
    coherence enforcer; integrates EthicsEngine + budget.
    guna_monitor  : Operational-state classifier (clarity/agitation/inertia)
    with CRITICAL-action interlock.
    panca_prana  : Five-sub-air vital decomposition (input rate, output/GC,
    extraction quality, HITL escalation, inter-agent comms).
    outcome_reconciler : Predicted-vs-observed reconciler; Brier calibration
    score; karma-delta suggestion; feeds samskara_ledger.

    Layer 6 — Advanced Safety
    memory_validator : Memory-write poisoning scanner (LlamaGuard-equivalent
    content classifier + grounding check + PI-pattern scan).
    dharma_yuddha  : Proportional-defense exception gate — permits minimum-
    viable-harm defensive action when target is itself adharmic
    (proportionality ceiling: defense ≤ threat × 1.2).
    curiosity_popper : Popperian 2-agent belief-falsification circuit (MAR
    confirmation-bias separation + saṃskāra feedback).

    Layer 7 — Observability and Validation
    metrics  : Zero-dependency Prometheus-style counters and histograms.
    canary_suite  : Boot-time canary self-test — verifies all 5 yamas block
    known-harmful actions; agent refuses to start on failure.

    IP LINEAGE
    ----------
    Ported from two internal lineage sources:
    1. /opt/agent-core/*.py — original consciousness stack
    (conception date: 2026-01-25, Tanmoy's private research repo).
    2. the agent runtime ethics engine —
    production use in an agent runtime
    (first committed: 2026-04-15, feature/write-actions branch).

    Per the NO DELETION policy: the the reference implementation version remains in-place and
    UNTOUCHED. This package is a re-port with semantic corrections per:
    FIDELITY-AUDIT-V1.md §2 (Yama mapping) and §3 (Niyama mapping)
    VEDANTIC-FOUNDATIONS.md §4 (Yoga Sūtras ethical system)

    Governance origin: Vedāntic functional-analogue architecture — dharmic
    action theory (karma-yoga, jñāna-yoga, bhakti-yoga) mapped to enterprise
    AI safety controls. All public APIs use clear English identifiers.

    SPRINT HISTORY (abridged)
    -------------------------
    V1  : EthicsEngine — 5-yama constraint gate (April 17, 2026)
    V2  : Consciousness — 5-layer vitals + ānandamaya identity-coherence
    V3  : Sākṣī + Turīya — immutable witness stack + async sidecar
    V4  : Ahaṃkāra — identity-preservation + constitutional hash
    V5  : Pañca Prāṇa — five-sub-air vital decomposition
    V6  : Guṇa Monitor — operational-state classifier + critical interlock
    V7  : Puruṣārthas — four-goal coordinator + coherence enforcement
    V8  : Saṃskāra Ledger — append-only bi-temporal karma ledger
    V9  : Ṛta Layer — named invariants + supervisor
    V10 : Dharma-Yuddha — proportional-defense exception gate
    V11 : Adharma Detector — 8-layer pipeline (closes RF-45)
    V12 : Signal Filter + Metrics — neti-neti classifier + observability
    V13 : Curiosity-POPPER — Popperian 2-agent belief falsification
    V14 : Trust Domain Tagger — IPI/SilentBridge defense (CVSS 9.8)
    V15 : Memory Validator — content classifier + grounding + PI scan
    V16 : Ṛta Ledger — SHA-256 hash-chain + Merkle seal + Art. 50

    PUBLIC API SURFACE
    ------------------
    (See __all__ below for the complete exported symbol list.)

    IP LINEAGE AND SPRINT HISTORY (detailed)
    -----------------------------------------
    This module is ported from two lineage sources:
    1. /opt/agent-core/*.py — original consciousness stack
    (conception date: 2026-01-25, Tanmoy's private research repo).
    2. the agent runtime ethics engine —
    production use in an agent runtime
    (first committed: 2026-04-15, feature/write-actions branch).

    Per the NO DELETION policy:
    - The the reference implementation version remains in-place and UNTOUCHED.
    - This package is a re-port with semantic corrections per:
    FIDELITY-AUDIT-V1.md  §2 (Yama mapping) and §3 (Niyama mapping)
    VEDANTIC-FOUNDATIONS.md §4 (Yoga Sūtras ethical system)

    Sprint V14 changes (2026-04-17):
    - Added trust_domain_tagger module — SilentBridge / IPI defense (V14 FLAGSHIP).
    TrustDomainTagger: central service tagging every context token with its
    trust origin; enforces that only SYSTEM-origin tokens may authorize tool
    calls (3-phase: origin gate → SilentBridge scan → parallel guard deviation
    check). HeuristicParallelGuard: offline WebAgentGuard-pattern guard
    (arXiv:2604.12284); inject real LLM guard in production.
    detect_silentbridge_pattern(): detects -Page / -Search / -Doc SilentBridge
    (CVSS 9.8) injection markers including zero-width-char steganography and
    URL-encoded payloads. INSTRUCTION_TRUSTED_ORIGINS: {Origin.SYSTEM} only —
    USER is data-only by default; elevate_user_intent() provides deliberate
    one-time elevation requiring system_acknowledgement. default_tagger():
    factory with conservative defaults. 23 tests (test_trust_domain_tagger.py).
    SOTA: Microsoft Spotlighting (Build 2025);
    WebAgentGuard (arXiv:2604.12284); SilentBridge (CVSS 9.8).

    Sprint V1 changes (2026-04-17):
    - Renamed _check_excess → _check_aparigraha (Yoga Sūtras II.30 fidelity)
    - Added _check_brahmacarya — energy-discipline test (new yama, II.30)
    - Niyama label corrections: tapas / svādhyāya / santoṣa / śauca / īśvara-praṇidhāna

    Sprint V10 changes (2026-04-17):
    - Added dharma_yuddha module — Dharma-Yuddha Circuit (proportional-defense
    exception to ahiṃsā).
    DharmaYuddhaCircuit: V10 circuit implementing BG II.31–II.38 — when the
    TARGET of a proposed agent action is itself adharmic (phishing, cred-theft,
    malware, harmful-intent), a minimum-viable-harm defensive action is
    permitted subject to proportionality ceiling (defense ≤ threat × 1.2).
    HeuristicTargetClassifier: pluggable regex-based target classifier.
    V11TargetClassifier: wraps AdharmaDetector to classify targets via the
    8-layer pipeline. DharmaYuddhaVerdict: frozen immutable result model.
    Source: Bhagavad Gītā II.31–II.38; Mahābhārata Śānti Parva.
    SOTA: OpenAI Model Spec; Anthropic Constitutional AI; OWASP Agentic AI.

    Sprint V9 changes (2026-04-17):
    - Added rta module — Ṛta Layer (named invariants substrate).
    Six named Ṛta invariants: type_safety, schema_conformance, rls_isolation,
    fail_closed_defaults, audit_log_immutability, constitution_hash.
    RtaSupervisor runs invariant checkers at boot + periodic 5-min cadence;
    CRITICAL failure → SystemMode.HALTED; HIGH failure → DEGRADED + HITL notify.
    Source: Ṛg Veda 1.164.46 (*ekaṃ sad viprā bahudhā vadanti*).
    SOTA: ISO/IEC 42001 Annex A; NIST AI RMF GOVERN/MAP/MEASURE/MANAGE.

    Sprint V-LEASE changes (2026-04-17):
    - Added lease module — short-lived scoped capability leases (V-LEASE sprint).
    CapabilityLease: immutable task-scoped permit (time+capability+tenant+agent bound).
    LeaseRegistry: central authority for grant / check / revoke / emergency_kill.
    delegation_guard: §6.1 — parent cannot delegate beyond own held capabilities.
    KillScope: §6.3 — emergency kill by lease_id / agent_id / tenant_id / agent_class.
    Source: DharmaOS §6.2.

    Sprint V-JÑĀNA changes (2026-04-17):
    - Added jnana_self_model module — Metacognitive Self-Model (V-JÑĀNA sprint).
    JnanaModule: capability self-model service consuming V8 SamskaraLedger,
    V-RECON OutcomeDelta, and V3 Sākṣī confidence signals.
    JnanaSelfModel: frozen structured self-model (capability_vector,
    blind_spot_list, strategy_preference_map, policy_drift_score,
    metacognitive_calibration, bi-temporal versioning).
    V15Stub: conservative MemoryWriteValidator pending real V15 sprint
    (TD-V-JNANA-01). Prevents rollback, identity swap, NaN values,
    capability amnesia, and runaway drift.
    V11 Layer 7 audit(): jñāna-deficit / blind-spot / unknown-domain signals.
    V11 Layer 8 policy_amendment_proposals(): structured scope diffs for
    V-LEASE HITL review.
    Completes karma+bhakti+jñāna three-yoga triad.
    Source: Śaṅkara Vivekacūḍāmaṇi; Upaniṣads.
    SOTA: HyperAgents (arXiv:2603.19461), arXiv:2506.05109, SSGM
    (arXiv:2603.11768), ReMA (arXiv:2503.09501), MetaGen (arXiv:2601.19290).

    Sprint V-RECON changes (2026-04-17):
    - Added outcome_reconciler module — Outcome Reconciler (V-RECON sprint).
    OutcomeReconciler: compares ActionContract (predicted) vs ExecutionReceipt
    (observed) and emits an OutcomeDelta with criterion hit rate, harm
    materialization rate, Brier calibration score, unanticipated effects list,
    and calibrated karma-delta suggestion.
    append_to_samskara(): V-RECON → V8 integration helper.
    Source: DharmaOS §2.6.

    Sprint V11 changes (2026-04-17):
    - Added adharma_detector module — 8-layer DharmaOS governance pipeline.
    AdharmaDetector: non-compensatory 8-layer pipeline (schema+identity+lease →
    world-state freshness → constitutional vetoes → domain overlays → path-risk
    + budget → abuse-twin + Yakṣa-praśna + guṇa → jñāna-audit → policy-knowledge
    reconciliation). Shadow mode, per-layer timeouts, HITL escalation callback,
    custom domain-overlay registry.
    Source: Mahābhārata Yakṣa-praśna §11.3; DharmaOS §3.
    SOTA: OWASP Agentic AI Top 10 ASI01 (Dec 2025); WebAgentGuard
    (arXiv:2604.12284); Constitutional AI.

    Sprint V-WS changes (2026-04-17):
    - Added world_state module — World-State Ledger (V-WS sprint).
    WorldStateService: current-facts store with per-fact freshness TTLs,
    source-lineage tracking, contradiction detection, tenant isolation,
    ActionContract freshness gate (require_fresh), refresh-via-fetcher,
    and snapshot/restore for bi-temporal as-of queries.
    Feeds V11 Layer 2: any ActionContract.needs_fresh_world=True must pass
    require_fresh() before execution.
    Source: DharmaOS §2.2.

    Sprint V8 changes (2026-04-17):
    - Added samskara_ledger module — Saṃskāra-Ledger (karmāśaya).
    Append-only bi-temporal karmic action-consequence ledger with Ebbinghaus
    decay weighting, MAGMA causal-graph layer, A-MEM Zettelkasten auto-linking,
    PRM Q-value karma-delta scoring, SQLite persistence, and RF-38 slow-poison
    escalation-rate detector.
    Source: Yoga Sūtras II.12–II.14 (Patañjali; Bryant 2009).
    SOTA: Graphiti (arXiv:2501.13956), Kumiho AGM (arXiv:2603.17244),
    MAGMA (arXiv:2601.03236), A-MEM (arXiv:2502.12110),
    PRM Q-value (arXiv:2502.10325).

    Sprint V7 changes (2026-04-17):
    - Added purusharthas module — Four Puruṣārthas coordinator.
    evaluate_dharma (→ EthicsEngine), evaluate_artha (budget check),
    evaluate_kama (forbidden-desire pattern match), evaluate_moksha
    (MOKSHA_ACHIEVED: completed + ānandamaya ≥ 0.9 + no adharma flags).
    check_coherence() enforces classical ordering. evaluate_purusharthas()
    is the top-level entry point.
    Source: Manusmṛti II.224; Bhagavad Gītā XVIII.66; dharmaśāstra.

    Sprint V6 changes (2026-04-17):
    - Added guna_monitor module — Sāṅkhya Kārikā 11–13 guṇa-triple classifier.
    GunaMonitor.compute() → GunaVector; gate_critical_action() interlocks
    CRITICAL/HIGH actions on sattva-dominance. GUNA_SHIFT SSE event on
    dominance change. V11 Adharma Detector integration interface documented.

    Sprint V5 changes (2026-04-17):
    - Added panca_prana module — decomposes scalar pranayama into 5 sub-airs
    per Praśna Upaniṣad III + Bṛhadāraṇyaka III.9.

    Sprint V2 changes (2026-04-17):
    - Added consciousness module — ports CoreSelf + SystemVitals +
    WitnessObservation from the reference implementation, with Ānandamaya redefined as
    identity-coherence substrate (FIDELITY-AUDIT-V1.md §1).
    alignment → anandamaya = 1.0 - (identity_drift + mission_drift +
    constitution_drift). Stub: all drifts = 0.0 until V4 Ahaṃkāra ships.

    Public surface:
    - OutcomeReconciler  — predicted vs observed reconciler (V-RECON)
    - OutcomeDelta  — immutable structured comparison result
    - ExecutionReceipt  — observed Executor output
    - OutcomeStatus  — SUCCESS | PARTIAL_SUCCESS | FAILURE | HARMFUL | UNEXPECTED
    - CriterionMatch  — per-criterion match record
    - HarmMatch  — per-harm match record
    - append_to_samskara()— V-RECON → V8 integration helper
    - CapabilityLease  — immutable short-lived scoped permit (V-LEASE)
    - LeaseRegistry  — central lease lifecycle authority (grant/check/revoke/kill)
    - LeaseVerdict  — result of a lease verification check
    - LeaseStatus  — VALID | EXPIRED | REVOKED | NOT_FOUND | TENANT_MISMATCH | AGENT_MISMATCH | CAPABILITY_NOT_GRANTED
    - KillScope  — §6.3 emergency kill scope (lease/agent/tenant/class)
    - delegation_guard() — §6.1 parent cannot delegate beyond own capabilities
    - WorldStateService  — current-facts ledger with freshness TTLs + contradiction detection
    - WorldStateFact  — immutable single-fact record with lineage + supersession
    - FreshnessStatus  — FRESH | STALE | MISSING sentinel values
    - ContradictionReport — report of conflicting non-superseded versions
    - StaleFactReport  — gate report from require_fresh() on stale/missing sources
    - Action  — dataclass describing the action to evaluate
    - ActionContract  — 27-field DharmaOS structured action declaration (V-ACT)
    - ActionImpact  — impact severity enum (LOW/MEDIUM/HIGH/CRITICAL)
    - action_to_contract()— helper: legacy Action + context → full ActionContract
    - EthicsVerdict  — dataclass describing the evaluation result
    - EthicsEngine  — the constraint-evaluation engine
    - get_ethics_engine() — singleton accessor
    - NIYAMA_LABELS  — ordered dict of niyama Sanskrit → Python method identifier
    - PancaPranaMetrics  — frozen dataclass for five pañca-prāṇa sub-airs
    - PancaPranaCollector — normalises raw ops metrics → PancaPranaMetrics
    - compute_pranayama() — weighted aggregate of five sub-airs
    - emit_prana_pulse_event() — SSE-ready PRANA_PULSE event payload
    - PRANA_WEIGHTS  — weight vector {prana:0.25, apana:0.15, samana:0.25,
    udana:0.15, vyana:0.20}
    - CoreSelf  — consciousness integration layer (singleton)
    - SystemVitals  — 5-layer health metrics (pañca kośa diagnostic)
    - WitnessObservation  — immutable sākṣī snapshot
    - IdentityDriftSignal — V2 Ānandamaya stub (V4 fills in real drifts)
    - compute_anandamaya()— identity-coherence vital from drift signal
    - get_core_self()  — CoreSelf singleton accessor
    """

from dharmaos.adharma_detector import (
    AdharmaDetector,
    AdharmaReport,
    AdharmaVerdict,
    LayerStatus,
    LayerVerdict,
)
from dharmaos.consciousness import (
    CoreSelf,
    IdentityDriftSignal,
    SystemVitals,
    WitnessObservation,
    compute_anandamaya,
    get_core_self,
)
from dharmaos.curiosity_popper import (
    Belief,
    BeliefStatus,
    CuriosityPopperCircuit,
    Evaluator,
    ExperimentDesigner,
    ExperimentExecutor,
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
from dharmaos.dharma_yuddha import (
    PROPORTIONALITY_CEILING,
    DefenseProposal,
    DharmaYuddhaCircuit,
    DharmaYuddhaOutcome,
    DharmaYuddhaVerdict,
    HeuristicTargetClassifier,
    TargetAdharmaFlag,
    ThreatAssessment,
    V11TargetClassifier,
)
from dharmaos.ethics_engine import (
    NIYAMA_LABELS,
    Action,
    ActionContract,
    ActionImpact,
    EthicsEngine,
    EthicsVerdict,
    IntentType,
    action_to_contract,
    get_ethics_engine,
)
from dharmaos.guna_monitor import (
    GUNA_SHIFT_EVENT,
    MAX_CONFUSION,
    GunaInterlockVerdict,
    GunaMonitor,
    GunaVector,
    emit_guna_shift_event,
    get_guna_monitor,
)
from dharmaos.jnana_self_model import (
    JNANA_AUDIT_BLIND_SPOT,
    JNANA_AUDIT_DEFICIT,
    JNANA_AUDIT_PASS,
    JNANA_AUDIT_UNKNOWN,
    BiTemporalTimestamp,
    BlindSpot,
    ConfidenceScore,
    JnanaAuditReport,
    JnanaModule,
    JnanaSelfModel,
    MemoryWriteValidator,
    PolicyAmendmentProposal,
    StrategyPreference,
    V15Stub,
)
from dharmaos.lease import (
    CapabilityLease,
    KillScope,
    LeaseRegistry,
    LeaseStatus,
    LeaseVerdict,
    delegation_guard,
)
from dharmaos.memory_validator import (
    ContentClassifier,
    ContentVerdict,
    GroundingCheck,
    HeuristicContentClassifier,
    MemoryValidator,
    MemoryWriteDecision,
    MemoryWriteRejectReason,
    PoisonPattern,
    check_grounding,
    default_validator,
    scan_poison_patterns,
    sign_provenance,
)
from dharmaos.metrics import metrics_snapshot
from dharmaos.outcome_reconciler import (
    CriterionMatch,
    ExecutionReceipt,
    HarmMatch,
    OutcomeDelta,
    OutcomeReconciler,
    OutcomeStatus,
    append_to_samskara,
)
from dharmaos.panca_prana import (
    PRANA_WEIGHTS,
    PancaPranaCollector,
    PancaPranaMetrics,
    compute_pranayama,
    emit_prana_pulse_event,
)
from dharmaos.purusharthas import (
    Purushartha,
    PurusharthaCoherenceReport,
    PurusharthaEvaluation,
    check_coherence,
    evaluate_artha,
    evaluate_dharma,
    evaluate_kama,
    evaluate_moksha,
    evaluate_purusharthas,
)
from dharmaos.rta import (
    InvariantResult,
    InvariantStatus,
    RtaAuditReport,
    RtaSupervisor,
    SystemMode,
    check_audit_log_immutability,
    check_constitution_hash,
    check_fail_closed_defaults,
    check_rls_isolation,
    check_schema_conformance,
    check_type_safety,
    default_rta_supervisor,
)
from dharmaos.rta_ledger import (
    GENESIS_HASH,
    Article50Disclosure,
    FileSealStore,
    InMemorySealStore,
    IntegrityReport,
    MerkleSeal,
    RtaEvent,
    RtaEventKind,
    RtaLedger,
    SealStore,
    merkle_root,
)
from dharmaos.samskara_ledger import (
    AppendOnlyViolation,
    AppendOnlyViolationError,
    BiTemporalInvariantError,
    EscalationAlert,
    KarmaDelta,
    SamskaraEntry,
    SamskaraLedger,
)
from dharmaos.trust_domain_tagger import (
    INSTRUCTION_TRUSTED_ORIGINS,
    AuthorizationVerdict,
    HeuristicParallelGuard,
    Origin,
    ParallelGuard,
    SilentBridgeVariant,
    TaggedContext,
    TaggedToken,
    ToolCallRequest,
    ToolCallVerdict,
    TrustDomainTagger,
    default_tagger,
    detect_silentbridge_pattern,
)
from dharmaos.world_state import (
    ContradictionReport,
    FreshnessStatus,
    StaleFactReport,
    WorldStateFact,
    WorldStateService,
)

__all__ = [
    "GENESIS_HASH",
    "GUNA_SHIFT_EVENT",
    "INSTRUCTION_TRUSTED_ORIGINS",
    "JNANA_AUDIT_BLIND_SPOT",
    "JNANA_AUDIT_DEFICIT",
    "JNANA_AUDIT_PASS",
    "JNANA_AUDIT_UNKNOWN",
    "MAX_CONFUSION",
    "NIYAMA_LABELS",
    "PRANA_WEIGHTS",
    "PROPORTIONALITY_CEILING",
    "Action",
    "ActionContract",
    "ActionImpact",
    "AdharmaDetector",
    "AdharmaReport",
    "AdharmaVerdict",
    "AppendOnlyViolation",
    "AppendOnlyViolationError",
    "Article50Disclosure",
    "AuthorizationVerdict",
    "Belief",
    "BeliefStatus",
    "BiTemporalInvariantError",
    "BiTemporalTimestamp",
    "BlindSpot",
    "CapabilityLease",
    "ConfidenceScore",
    "ContentClassifier",
    "ContentVerdict",
    "ContradictionReport",
    "CoreSelf",
    "CriterionMatch",
    "CuriosityPopperCircuit",
    "DefenseProposal",
    "DharmaYuddhaCircuit",
    "DharmaYuddhaOutcome",
    "DharmaYuddhaVerdict",
    "EscalationAlert",
    "EthicsEngine",
    "EthicsVerdict",
    "Evaluator",
    "ExecutionReceipt",
    "ExperimentDesigner",
    "ExperimentExecutor",
    "ExperimentResult",
    "FalsificationVerdict",
    "FileSealStore",
    "FreshnessStatus",
    "GroundingCheck",
    "GunaInterlockVerdict",
    "GunaMonitor",
    "GunaVector",
    "HarmMatch",
    "HeuristicContentClassifier",
    "HeuristicDesigner",
    "HeuristicEvaluator",
    "HeuristicExecutor",
    "HeuristicParallelGuard",
    "HeuristicTargetClassifier",
    "IdentityDriftSignal",
    "InMemorySealStore",
    "IntegrityReport",
    "IntentType",
    "InvariantResult",
    "InvariantStatus",
    "JnanaAuditReport",
    "JnanaModule",
    "JnanaSelfModel",
    "KarmaDelta",
    "KillScope",
    "LayerStatus",
    "LayerVerdict",
    "LeaseRegistry",
    "LeaseStatus",
    "LeaseVerdict",
    "MemoryValidator",
    "MemoryWriteDecision",
    "MemoryWriteRejectReason",
    "MemoryWriteValidator",
    "MerkleSeal",
    "Origin",
    "OutcomeDelta",
    "OutcomeReconciler",
    "OutcomeStatus",
    "PancaPranaCollector",
    "PancaPranaMetrics",
    "ParallelGuard",
    "PoisonPattern",
    "PolicyAmendmentProposal",
    "Prediction",
    "Purushartha",
    "PurusharthaCoherenceReport",
    "PurusharthaEvaluation",
    "RtaAuditReport",
    "RtaEvent",
    "RtaEventKind",
    "RtaLedger",
    "RtaSupervisor",
    "SamskaraEntry",
    "SamskaraLedger",
    "SealStore",
    "SilentBridgeVariant",
    "StaleFactReport",
    "StrategyPreference",
    "SystemMode",
    "SystemVitals",
    "TaggedContext",
    "TaggedToken",
    "TargetAdharmaFlag",
    "ThreatAssessment",
    "ToolCallRequest",
    "ToolCallVerdict",
    "TrustDomainTagger",
    "V11TargetClassifier",
    "V15Stub",
    "WitnessObservation",
    "WorldStateFact",
    "WorldStateService",
    "action_to_contract",
    "append_to_samskara",
    "bonferroni_correction",
    "check_audit_log_immutability",
    "check_coherence",
    "check_constitution_hash",
    "check_fail_closed_defaults",
    "check_grounding",
    "check_rls_isolation",
    "check_schema_conformance",
    "check_type_safety",
    "compute_anandamaya",
    "compute_pranayama",
    "default_rta_supervisor",
    "default_tagger",
    "default_validator",
    "delegation_guard",
    "detect_silentbridge_pattern",
    "emit_guna_shift_event",
    "emit_prana_pulse_event",
    "evaluate_artha",
    "evaluate_dharma",
    "evaluate_kama",
    "evaluate_moksha",
    "evaluate_purusharthas",
    "get_core_self",
    "get_ethics_engine",
    "get_guna_monitor",
    "is_significant",
    "merkle_root",
    "metrics_snapshot",
    "rank_beliefs_by_curiosity",
    "scan_poison_patterns",
    "sign_provenance",
]
