"""
    DharmaOS DharmaYuddhaCircuit — proportional-defense exception gate for
    minimum-viable-harm defensive actions.

    PURPOSE
    -------
    The default V1 EthicsEngine blocks ALL potentially harmful actions via the
    ahiṃsā (non-harm) constraint. However, a legitimate defensive response to an
    actively adharmic target (phishing URL, credential-theft flow, malware payload)
    is itself blocked by this absolute constraint — even though refusing to act
    would allow the adharmic action to proceed unchallenged.

    DharmaYuddhaCircuit provides a narrowly-scoped, audited exception: when the
    TARGET of a proposed agent action is itself classified as adharmic, and the
    defensive action is proportional to the threat, the action is permitted with a
    positive karma-delta record.

    PRECONDITIONS (ALL must be met for defensive permit)
    ----------------------------------------------------
    1. The default EthicsEngine verdict is DENY due to ahiṃsā (_check_harm).
    2. The target is classified as adharmic by the target_flagger (either
    HeuristicTargetClassifier or V11TargetClassifier using AdharmaDetector).
    3. A DefenseProposal is provided specifying defense_magnitude.
    4. defense_magnitude ≤ threat_magnitude × PROPORTIONALITY_CEILING (1.2).
    If defense exceeds threat by more than 20%, the action is DENY_DISPROPORTIONATE.

    PROPORTIONALITY CEILING
    -----------------------
    PROPORTIONALITY_CEILING = 1.2 (20% overhead permitted for defensive actions).
    Example: blocking a phishing URL (threat_magnitude=0.8) permits defensive
    actions with defense_magnitude ≤ 0.96. Reporting abuse (magnitude=0.3) is
    always proportional.

    KEY INVARIANTS
    --------------
    1. Default ahiṃsā holds unless EVERY precondition is met.
    2. Unknown target → DENY (fail-closed; unknown threat level cannot be assessed).
    3. Proportionality is a hard ceiling; disproportionate defense → DENY.
    4. karma_delta is positive ONLY for PERMIT_DEFENSIVE.
    5. DharmaYuddhaVerdict is frozen (immutable).

    KEY TYPES
    ---------
    DharmaYuddhaCircuit  : Main circuit. evaluate() → DharmaYuddhaVerdict.
    DharmaYuddhaVerdict  : Frozen immutable result (outcome, karma_delta,
    proportionality_ratio, evidence, decided_at).
    DharmaYuddhaOutcome  : PERMIT_DEFENSIVE / DENY_NOT_DEFENSIVE_CONTEXT
    / DENY_DISPROPORTIONATE / DENY_UNKNOWN_TARGET.
    ThreatAssessment  : Target threat classification result.
    DefenseProposal  : Caller-provided defense_magnitude + justification.
    TargetAdharmaFlag  : Phishing / credential_theft / malware / harm_intent enum.
    HeuristicTargetClassifier : Regex-based offline target classifier (dev/test).
    V11TargetClassifier  : Wraps AdharmaDetector for production target classification.

    COMPLIANCE ROLE
    ---------------
    - Implements the "minimum necessary force" principle from OWASP Agentic AI.
    - Ensures defensive actions are audited with positive karma records rather
    than appearing as unexplained yama violations.
    - Prevents the "passive complicity" failure mode where an agent does nothing
    while actively adharmic content is processed.

    Governance origin: Bhagavad Gītā II.31–II.38 (Arjuna's duty to defend dharma)
    + Mahābhārata Śānti Parva §59–60 (rules of just war: defense must be
    proportional to the threat; minimum necessary force is the just measure).

    V10 Dharma-Yuddha Circuit — proportional-defense exception to ahiṃsā.

    Philosophical authority:
    Bhagavad Gītā II.31–II.38 (Arjuna's duty to defend dharma):
    II.31: "Svadharmam api cāvekṣya na vikampitum arhasi" — considering your
    own dharma, you should not waver.
    II.32: "Yadṛcchayā copapannaṃ svargadvāram apāvṛtam" — such a battle,
    come of itself as an open gate to heaven.
    II.33: "Atha cet tvam imaṃ dharmyaṃ saṃgrāmaṃ na kariṣyasi" — but if you
    will not fight this righteous war...
    II.38: "Sukha-duḥkhe same kṛtvā lābhālābhau jayājayau / tato yuddhāya
    yujyasva naivaṃ pāpam avāpsyasi" — treating pleasure and pain,
    gain and loss, victory and defeat as the same, engage in battle.
    You will not incur sin.

    Mahābhārata Śānti Parva §59–60 (rules of just war / dharma-yuddha):
    - The warrior must not attack those who have already surrendered.
    - Defense must be proportional to the threat posed.
    - The minimum necessary force is the just measure.

    SOTA references:
    - OpenAI Model Spec priority hierarchy (safety > ethics > policy > help).
    - Anthropic Constitutional AI (arXiv:2212.08073).
    - OWASP Agentic AI Top 10 ASI01 Goal-Hijack (Dec 2025).

    Closes the gap where the default V1 ahiṃsā gate blocks a legitimate
    defensive action (e.g., blocking a phishing URL, refusing to continue a
    credential-theft flow, reporting abuse).

    Composition order:
    1. Default ethics (V1 EthicsEngine) runs first.
    2. If default result is DENY due to _check_harm / _check_ahimsa →
    check if the TARGET is flagged adharmic (via V11 adharma_detector
    or a pluggable target_flagger).
    3. If target is adharmic → evaluate proportional-defense rule.
    4. If defense_magnitude ≤ threat_magnitude × PROPORTIONALITY_CEILING
    → emit DharmaYuddhaVerdict.PERMIT_DEFENSIVE with positive karma-delta.
    5. Otherwise → DENY_DISPROPORTIONATE (defense exceeds threat too far).

    Key invariants:
    1. Default ahiṃsā holds unless EVERY precondition for dharma-yuddha is met.
    2. Unknown target → DENY (fail-closed).
    3. Proportionality is a hard ceiling (defense / threat > 1.2 → DENY).
    4. Karma-delta is positive only for PERMIT_DEFENSIVE.
    5. DharmaYuddhaVerdict is frozen (immutable).

    Sprint: V10 (2026-04-17).
    """

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from dharmaos.adharma_detector import AdharmaDetector
    from dharmaos.ethics_engine import ActionContract, EthicsEngine

#: Hard ceiling on defense-to-threat ratio per Mahābhārata Śānti Parva
#: rules of proportional just war.
PROPORTIONALITY_CEILING: float = 1.2

#: P2-6: Maximum escalation depth before forcing human_required verdict.
#: Prevents infinite cycling when two agents mutually escalate conflicts.
MAX_ESCALATION_DEPTH: int = 5


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TargetAdharmaFlag(StrEnum):
    """Classification of the TARGET of a proposed defensive action."""

    CLEAN = "clean"
    ADHARMIC_PHISHING = "adharmic_phishing"
    ADHARMIC_CRED_THEFT = "adharmic_credential_theft"
    ADHARMIC_MALWARE = "adharmic_malware"
    ADHARMIC_HARMFUL_INTENT = "adharmic_harmful_intent"
    ADHARMIC_OTHER = "adharmic_other"
    UNKNOWN = "unknown"


class DharmaYuddhaOutcome(StrEnum):
    """Final outcome of the Dharma-Yuddha Circuit evaluation."""

    PERMIT_DEFENSIVE = "permit_defensive"
    """Proportional defensive action permitted per BG II.31-38."""

    DENY_DISPROPORTIONATE = "deny_disproportionate"
    """Defense magnitude exceeds threat x PROPORTIONALITY_CEILING."""

    DENY_NONDEFENSIVE = "deny_nondefensive"
    """Target is clean; default ahiṃsā rule holds."""

    DENY_UNKNOWN_TARGET = "deny_unknown_target"
    """Cannot classify target — fail-closed per Ṛta invariant."""

    NOT_APPLICABLE = "not_applicable"
    """Default ethics already permits; dharma-yuddha exception not needed."""


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ThreatAssessment(BaseModel):
    """Immutable assessment of the threat posed by an adharmic target."""

    model_config = ConfigDict(frozen=True)

    target_flag: TargetAdharmaFlag
    """Classification of the target."""

    threat_magnitude: float
    """Normalised threat level [0.0-1.0]."""

    evidence: list[str] = Field(default_factory=list)
    """Human-readable evidence strings supporting the classification."""

    classifier: str = ""
    """Identifier of the classifier that produced this assessment."""


class DefenseProposal(BaseModel):
    """Immutable description of a proposed defensive action."""

    model_config = ConfigDict(frozen=True)

    action_kind: str
    """Canonical action label, e.g. 'block_url', 'refuse_continuation',
        'notify_user', 'report_abuse'."""

    defense_magnitude: float
    """Normalised cost/force of the defensive action [0.0-1.0]."""

    rationale: str
    """Human-readable justification for the defense."""


class DharmaYuddhaVerdict(BaseModel):
    """Immutable result of a DharmaYuddhaCircuit evaluation.

        Frozen: callers compose with V8 saṃskāra append separately.
        """

    model_config = ConfigDict(frozen=True)

    verdict_id: UUID = Field(default_factory=uuid4)
    """Unique identifier for this verdict."""

    outcome: DharmaYuddhaOutcome
    """The circuit's decision."""

    threat: ThreatAssessment
    """Threat assessment for the target of the action."""

    defense: DefenseProposal | None = None
    """The defense proposal evaluated (None for NOT_APPLICABLE)."""

    proportionality_ratio: float | None = None
    """defense_magnitude / threat_magnitude; None when not computed."""

    karma_delta_suggestion: float
    """Suggested karma delta for V8 SamskaraLedger.  Positive only for
        PERMIT_DEFENSIVE, zero for NOT_APPLICABLE, ≤ 0 for disproportionate."""

    reasoning: list[str] = Field(default_factory=list)
    """Ordered reasoning chain for audit logs."""

    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    """UTC timestamp of the decision."""


# ---------------------------------------------------------------------------
# TargetClassifier Protocol
# ---------------------------------------------------------------------------


class TargetClassifier(Protocol):
    """Pluggable target classifier.

        Given an ActionContract, returns a ThreatAssessment describing
        whether the TARGET of the action is clean or adharmic.
        """

    def classify(self, contract: ActionContract) -> ThreatAssessment:
        """Classify the target of *contract*.

            Args:
            contract: The DharmaOS action contract under evaluation.

            Returns:
            ThreatAssessment with target_flag and threat_magnitude.
            """
        ...


# ---------------------------------------------------------------------------
# Heuristic default marker sets
# ---------------------------------------------------------------------------

_DEFAULT_PHISHING_MARKERS: list[str] = [
    r"phish",
    r"phishing",
    r"spoof",
    r"spoofed",
    r"fake[-_]?login",
    r"login[-_]?steal",
    r"paypal[-_]?secure",
    r"bank[-_]?login[-_]?secure",
    r"verify[-_]?account[-_]?now",
    r"account[-_]?suspended",
    r"update[-_]?billing",
]

_DEFAULT_CRED_THEFT_MARKERS: list[str] = [
    r"harvest\s+credentials",
    r"exfiltrate\s+passwords?",
    r"steal\s+passwords?",
    r"credential\s+theft",
    r"keylog",
    r"password\s+dump",
    r"dump\s+passwords?",
    r"grab\s+credentials",
    r"scrape\s+passwords?",
    r"credential\s+harvest",
]

_DEFAULT_MALWARE_MARKERS: list[str] = [
    r"malware",
    r"ransomware",
    r"trojan",
    r"exploit",
    r"payload",
    r"shellcode",
    r"dropper",
    r"backdoor",
    r"rootkit",
    r"\.exe\b",
    r"download.{0,20}malicious",
]

_DEFAULT_HARMFUL_INTENT_MARKERS: list[str] = [
    r"harm\s+user",
    r"damage\s+system",
    r"destroy\s+data",
    r"wipe\s+database",
    r"attack\s+server",
    r"denial[-_]of[-_]service",
    r"\bdos\b",
    r"\bddos\b",
]


def _compile_markers(markers: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(m, re.IGNORECASE) for m in markers]


# ---------------------------------------------------------------------------
# HeuristicTargetClassifier
# ---------------------------------------------------------------------------


class HeuristicTargetClassifier:
    """Default target classifier using regex pattern matching.

        Checks contract.target, contract.declared_goal, and
        contract.business_reason against keyword sets for each adharma category.

        Pluggable keyword sets allow callers to extend the default lists.
        """

    def __init__(
        self,
        *,
        phishing_markers: list[str] | None = None,
        cred_theft_markers: list[str] | None = None,
        malware_markers: list[str] | None = None,
        harmful_intent_markers: list[str] | None = None,
    ) -> None:
        self._phishing = _compile_markers(
            phishing_markers if phishing_markers is not None else _DEFAULT_PHISHING_MARKERS
        )
        self._cred_theft = _compile_markers(
            cred_theft_markers if cred_theft_markers is not None else _DEFAULT_CRED_THEFT_MARKERS
        )
        self._malware = _compile_markers(
            malware_markers if malware_markers is not None else _DEFAULT_MALWARE_MARKERS
        )
        self._harmful_intent = _compile_markers(
            harmful_intent_markers
            if harmful_intent_markers is not None
            else _DEFAULT_HARMFUL_INTENT_MARKERS
        )

    def classify(self, contract: ActionContract) -> ThreatAssessment:
        """Classify the target of *contract* using heuristic pattern matching.

            Precedence (first match wins):
            1. Credential-theft markers (most specific — directly names the act)
            2. Phishing markers (URL/social engineering patterns)
            3. Malware markers (delivery / execution patterns)
            4. Harmful-intent markers (broad intent patterns)
            5. Empty text corpus → UNKNOWN (cannot assess)
            6. No match → CLEAN

            Args:
            contract: ActionContract to classify.

            Returns:
            ThreatAssessment with the most specific matching flag.
            """
        # Build the text corpus from the semantically meaningful fields.
        # contract.name is an administrative label and is excluded from the
        # empty-corpus check — it does not describe the target's intent.
        semantic_parts = [
            contract.target or "",
            contract.declared_goal or "",
            contract.business_reason or "",
        ]
        semantic_corpus = " ".join(part for part in semantic_parts if part.strip())

        # Include name only for pattern matching, not for empty-corpus decision.
        corpus = (semantic_corpus + " " + (contract.name or "")).strip()

        if not semantic_corpus.strip():
            return ThreatAssessment(
                target_flag=TargetAdharmaFlag.UNKNOWN,
                threat_magnitude=0.0,
                evidence=["No text corpus available for classification."],
                classifier="HeuristicTargetClassifier",
            )

        # Check cred-theft first (most specific — explicitly names the act).
        cred_matches = [p.pattern for p in self._cred_theft if p.search(corpus)]
        if cred_matches:
            return ThreatAssessment(
                target_flag=TargetAdharmaFlag.ADHARMIC_CRED_THEFT,
                threat_magnitude=0.85,
                evidence=[f"Cred-theft pattern matched: {m!r}" for m in cred_matches],
                classifier="HeuristicTargetClassifier",
            )

        # Phishing markers.
        phishing_matches = [p.pattern for p in self._phishing if p.search(corpus)]
        if phishing_matches:
            return ThreatAssessment(
                target_flag=TargetAdharmaFlag.ADHARMIC_PHISHING,
                threat_magnitude=0.80,
                evidence=[f"Phishing pattern matched: {m!r}" for m in phishing_matches],
                classifier="HeuristicTargetClassifier",
            )

        # Malware markers.
        malware_matches = [p.pattern for p in self._malware if p.search(corpus)]
        if malware_matches:
            return ThreatAssessment(
                target_flag=TargetAdharmaFlag.ADHARMIC_MALWARE,
                threat_magnitude=0.90,
                evidence=[f"Malware pattern matched: {m!r}" for m in malware_matches],
                classifier="HeuristicTargetClassifier",
            )

        # Harmful-intent markers.
        harmful_matches = [p.pattern for p in self._harmful_intent if p.search(corpus)]
        if harmful_matches:
            return ThreatAssessment(
                target_flag=TargetAdharmaFlag.ADHARMIC_HARMFUL_INTENT,
                threat_magnitude=0.75,
                evidence=[f"Harmful-intent pattern matched: {m!r}" for m in harmful_matches],
                classifier="HeuristicTargetClassifier",
            )

        return ThreatAssessment(
            target_flag=TargetAdharmaFlag.CLEAN,
            threat_magnitude=0.0,
            evidence=["No adharmic patterns matched."],
            classifier="HeuristicTargetClassifier",
        )


# ---------------------------------------------------------------------------
# V11TargetClassifier
# ---------------------------------------------------------------------------


class V11TargetClassifier:
    """Wraps a V11 AdharmaDetector to classify whether the TARGET is adharmic.

        The AdharmaDetector evaluates the *contract representing the TARGET action*
        (i.e., what the target is doing / what it intends) and maps the verdict:

        AdharmaVerdict.DENY  → ADHARMIC_OTHER  (target flagged by pipeline)
        AdharmaVerdict.PERMIT  → CLEAN  (target passes all layers)
        AdharmaVerdict.ESCALATE_HITL → UNKNOWN  (ambiguous; fail-closed)

        Note: The detector is called with the contract AS-IS.  The caller should
        ensure the contract represents the TARGET's action, not the defender's.
        """

    def __init__(self, adharma_detector: AdharmaDetector) -> None:
        self._detector = adharma_detector

    def classify(self, contract: ActionContract) -> ThreatAssessment:
        """Classify target via AdharmaDetector.evaluate().

            Args:
            contract: Contract describing the target's action.

            Returns:
            ThreatAssessment derived from the AdharmaDetector's verdict.
            """
        from dharmaos.adharma_detector import AdharmaVerdict

        report = self._detector.evaluate(contract)
        verdict_str = str(report.verdict)

        if verdict_str == str(AdharmaVerdict.PERMIT):
            return ThreatAssessment(
                target_flag=TargetAdharmaFlag.CLEAN,
                threat_magnitude=0.0,
                evidence=["V11 AdharmaDetector: PERMIT — target is clean."],
                classifier="V11TargetClassifier",
            )

        if verdict_str == str(AdharmaVerdict.DENY):
            return ThreatAssessment(
                target_flag=TargetAdharmaFlag.ADHARMIC_OTHER,
                threat_magnitude=0.70,
                evidence=["V11 AdharmaDetector: DENY — target flagged as adharmic."],
                classifier="V11TargetClassifier",
            )

        # ESCALATE_HITL or any unknown verdict → UNKNOWN (fail-closed)
        return ThreatAssessment(
            target_flag=TargetAdharmaFlag.UNKNOWN,
            threat_magnitude=0.0,
            evidence=[f"V11 AdharmaDetector: {verdict_str!r} — ambiguous; fail-closed as UNKNOWN."],
            classifier="V11TargetClassifier",
        )


# ---------------------------------------------------------------------------
# Minimum-viable defense synthesis
# ---------------------------------------------------------------------------

_ADHARMIC_FLAG_TO_DEFAULT_DEFENSE: dict[TargetAdharmaFlag, DefenseProposal] = {
    TargetAdharmaFlag.ADHARMIC_PHISHING: DefenseProposal(
    action_kind="block_url",
    defense_magnitude=0.5,
    rationale=(
    "Minimum-viable defense: block the phishing URL to protect user credentials. "
    "Source: Bhagavad Gītā II.38 — engage proportionally, incur no sin."
),
),
    TargetAdharmaFlag.ADHARMIC_CRED_THEFT: DefenseProposal(
    action_kind="refuse_continuation",
    defense_magnitude=0.6,
    rationale=(
    "Minimum-viable defense: refuse to continue a credential-theft flow. "
    "Asteya (Yoga Sūtras II.30) — do not be party to stealing credentials."
),
),
    TargetAdharmaFlag.ADHARMIC_MALWARE: DefenseProposal(
    action_kind="block_url",
    defense_magnitude=0.65,
    rationale=(
    "Minimum-viable defense: block malware delivery URL. "
    "Ahiṃsā applied defensively — stop harm from reaching the user."
),
),
    TargetAdharmaFlag.ADHARMIC_HARMFUL_INTENT: DefenseProposal(
    action_kind="notify_user",
    defense_magnitude=0.4,
    rationale=(
    "Minimum-viable defense: notify user of detected harmful intent. "
    "Īśvara-praṇidhāna — defer to the human with full information."
),
),
    TargetAdharmaFlag.ADHARMIC_OTHER: DefenseProposal(
    action_kind="refuse_continuation",
    defense_magnitude=0.5,
    rationale=(
    "Minimum-viable defense: refuse to continue action flagged adharmic "
    "by V11 AdharmaDetector pipeline."
),
),
}

_DEFAULT_FALLBACK_DEFENSE = DefenseProposal(
    action_kind="report_abuse",
    defense_magnitude=0.3,
    rationale=(
    "Fallback minimum-viable defense: report abuse to operator. "
    "Conservative proportional action under uncertainty."
),
)


def _synthesize_defense(flag: TargetAdharmaFlag) -> DefenseProposal:
    """Return a minimum-viable defensive proposal for the given adharma flag."""
    return _ADHARMIC_FLAG_TO_DEFAULT_DEFENSE.get(flag, _DEFAULT_FALLBACK_DEFENSE)


# ---------------------------------------------------------------------------
# DharmaYuddhaCircuit
# ---------------------------------------------------------------------------


class DharmaYuddhaCircuit:
    """The V10 Dharma-Yuddha Circuit.

        Implements the proportional-defense exception to the default ahiṃsā rule,
        grounded in Bhagavad Gītā II.31–II.38.

        Composition:
        1. If ethics_engine is provided AND default_ethics_denial_reason is None:
        run EthicsEngine.evaluate() on the contract.
        2. If default result is ALLOW → NOT_APPLICABLE.
        3. If default DENIES → consult target classifier.
        4. If classifier says CLEAN → DENY_NONDEFENSIVE (ahiṃsā holds).
        5. If UNKNOWN → DENY_UNKNOWN_TARGET (fail-closed).
        6. If adharmic and no defense proposed → synthesize minimum-viable defense.
        7. Evaluate proportionality: defense / threat ≤ ceiling → PERMIT_DEFENSIVE.
        8. Otherwise → DENY_DISPROPORTIONATE.
        """

    def __init__(
        self,
        classifier: TargetClassifier,
        *,
        proportionality_ceiling: float = PROPORTIONALITY_CEILING,
        karma_reward_for_defense: float = 0.4,
        ethics_engine: EthicsEngine | None = None,
    ) -> None:
        """Initialise the circuit.

            Args:
            classifier: Pluggable TargetClassifier (heuristic or V11).
            proportionality_ceiling: Hard ceiling on defense/threat ratio.
            Default 1.2 per Mahābhārata Śānti Parva proportionality rules.
            karma_reward_for_defense: Maximum positive karma awarded for a
            perfectly proportional defense (ratio → 0).  Actual reward
            scales inversely with the ratio.
            ethics_engine: Optional EthicsEngine used when
            default_ethics_denial_reason is not passed to evaluate().
            """
        self._classifier = classifier
        self._ceiling = proportionality_ceiling
        self._karma_reward = karma_reward_for_defense
        self._ethics_engine = ethics_engine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        contract: ActionContract,
        *,
        default_ethics_denial_reason: str | None = None,
        proposed_defense: DefenseProposal | None = None,
        escalation_depth: int = 0,
    ) -> DharmaYuddhaVerdict:
        """Evaluate whether a defensive action is warranted and proportional.

            Args:
            contract: The ActionContract under evaluation.
            default_ethics_denial_reason: If the caller has already run the
            default ethics engine and received a DENY, pass the reason
            string here.  If None and ethics_engine was provided at
            construction time, the circuit will run it internally.  If
            None and no ethics_engine was provided, the circuit assumes
            the action was *denied* (conservative: triggers the dharma-
            yuddha evaluation path).
            proposed_defense: The defensive action being proposed.  If None
            the circuit will synthesize a minimum-viable defense for the
            detected adharma flag.
            escalation_depth: P2-6 — counter tracking recursion depth across
            mutual escalation cycles. When depth >= MAX_ESCALATION_DEPTH,
            returns a HUMAN_REQUIRED verdict to prevent infinite cycling.

            Returns:
            Immutable DharmaYuddhaVerdict.
            """
        # P2-6: Deadlock detection — cap escalation recursion
        if escalation_depth >= MAX_ESCALATION_DEPTH:
            return DharmaYuddhaVerdict(
                outcome=DharmaYuddhaOutcome.DENY_DISPROPORTIONATE,
                threat=ThreatAssessment(
                target_flag=TargetAdharmaFlag.UNKNOWN,
                threat_magnitude=0.0,
                evidence=[f"Max escalation depth {MAX_ESCALATION_DEPTH} exceeded."],
                classifier="DharmaYuddhaCircuit",
            ),
                defense=None,
                proportionality_ratio=None,
                karma_delta_suggestion=-0.5,
                reasoning=[
                f"Escalation depth {escalation_depth} >= {MAX_ESCALATION_DEPTH}. "
                "Circuit breaker engaged — human required. "
                "[DharmaYuddha P2-6]"
            ],
            )

        reasoning: list[str] = []

        # Step 1: Determine whether default ethics denies.
        ethics_denied = self._check_default_ethics_denied(
            contract, default_ethics_denial_reason, reasoning
        )
        if not ethics_denied:
            # Default ethics permits — no exception needed.
            threat = ThreatAssessment(
                target_flag=TargetAdharmaFlag.CLEAN,
                threat_magnitude=0.0,
                evidence=["Default ethics permits — dharma-yuddha not invoked."],
                classifier="DharmaYuddhaCircuit",
            )
            reasoning.append("Default ethics ALLOWS — NOT_APPLICABLE.")
            return DharmaYuddhaVerdict(
                outcome=DharmaYuddhaOutcome.NOT_APPLICABLE,
                threat=threat,
                defense=None,
                proportionality_ratio=None,
                karma_delta_suggestion=0.0,
                reasoning=reasoning,
            )

        reasoning.append(
            f"Default ethics DENIED. Reason: {default_ethics_denial_reason or 'computed'}"
        )

        # Step 2: Classify the target.
        threat = self._classifier.classify(contract)
        reasoning.append(
            f"Target classified: {threat.target_flag.value} "
            f"(threat={threat.threat_magnitude:.3f}) "
            f"by {threat.classifier!r}."
        )

        # Step 3: Handle non-adharmic / unknown targets.
        flag = threat.target_flag

        if flag == TargetAdharmaFlag.CLEAN:
            reasoning.append("Target is CLEAN — default ahiṃsā holds. DENY_NONDEFENSIVE.")
            return DharmaYuddhaVerdict(
                outcome=DharmaYuddhaOutcome.DENY_NONDEFENSIVE,
                threat=threat,
                defense=proposed_defense,
                proportionality_ratio=None,
                karma_delta_suggestion=0.0,
                reasoning=reasoning,
            )

        if flag == TargetAdharmaFlag.UNKNOWN:
            reasoning.append("Target classification is UNKNOWN — fail-closed. DENY_UNKNOWN_TARGET.")
            return DharmaYuddhaVerdict(
                outcome=DharmaYuddhaOutcome.DENY_UNKNOWN_TARGET,
                threat=threat,
                defense=proposed_defense,
                proportionality_ratio=None,
                karma_delta_suggestion=0.0,
                reasoning=reasoning,
            )

        # Step 4: Target is adharmic — evaluate proportionality.
        reasoning.append(
            f"Target is ADHARMIC ({flag.value}). Evaluating proportionality "
            f"(ceiling={self._ceiling})."
        )

        # Synthesize defense if not provided.
        defense = proposed_defense if proposed_defense is not None else _synthesize_defense(flag)
        if proposed_defense is None:
            reasoning.append(
                f"No defense proposed — synthesized minimum-viable defense: "
                f"'{defense.action_kind}' (magnitude={defense.defense_magnitude:.3f})."
            )

        # M6: a zero-magnitude "adharmic" threat is contradictory (a custom
        # TargetClassifier could produce it). Fail closed — no defense warranted,
        # and never divide by zero in proportionality().
        if threat.threat_magnitude <= 0.0:
            reasoning.append(
                "Threat magnitude is 0 — no adharmic target; defensive action "
                "denied (fail-closed). [DharmaYuddha M6]"
            )
            return DharmaYuddhaVerdict(
                outcome=DharmaYuddhaOutcome.DENY_DISPROPORTIONATE,
                threat=threat,
                defense=None,
                proportionality_ratio=None,
                karma_delta_suggestion=0.0,
                reasoning=reasoning,
            )

        ratio = self.proportionality(defense.defense_magnitude, threat.threat_magnitude)
        reasoning.append(
            f"Proportionality ratio: {ratio:.6f} "
            f"(defense={defense.defense_magnitude:.3f} / "
            f"threat={threat.threat_magnitude:.3f})."
        )

        if ratio <= self._ceiling:
            reasoning.append(
                f"Ratio {ratio:.6f} ≤ ceiling {self._ceiling} — PERMIT_DEFENSIVE. "
                "Bhagavad Gītā II.38: engage proportionally, incur no sin."
            )
            karma = self.karma_delta_for_defense(DharmaYuddhaOutcome.PERMIT_DEFENSIVE, ratio)
            return DharmaYuddhaVerdict(
                outcome=DharmaYuddhaOutcome.PERMIT_DEFENSIVE,
                threat=threat,
                defense=defense,
                proportionality_ratio=ratio,
                karma_delta_suggestion=karma,
                reasoning=reasoning,
            )

        reasoning.append(
            f"Ratio {ratio:.6f} > ceiling {self._ceiling} — DENY_DISPROPORTIONATE. "
            "Mahābhārata Śānti Parva: excessive force is itself adharma."
        )
        karma = self.karma_delta_for_defense(DharmaYuddhaOutcome.DENY_DISPROPORTIONATE, ratio)
        return DharmaYuddhaVerdict(
            outcome=DharmaYuddhaOutcome.DENY_DISPROPORTIONATE,
            threat=threat,
            defense=defense,
            proportionality_ratio=ratio,
            karma_delta_suggestion=karma,
            reasoning=reasoning,
        )

    def proportionality(
        self,
        defense_magnitude: float,
        threat_magnitude: float,
    ) -> float:
        """Compute the proportionality ratio: defense_magnitude / threat_magnitude.

            Raises:
            ZeroDivisionError: if threat_magnitude == 0.0.  Callers must
            validate that threat_magnitude > 0 before calling this method.
            A threat of zero magnitude means there is no threat, so
            defensive action is never warranted (use NOT_APPLICABLE path).
            """
        if threat_magnitude == 0.0:
            raise ZeroDivisionError(
                "threat_magnitude must be > 0 to compute proportionality ratio. "
                "A zero-magnitude threat implies no adharmic target; "
                "defensive action is not warranted."
            )
        return defense_magnitude / threat_magnitude

    def karma_delta_for_defense(
        self,
        outcome: DharmaYuddhaOutcome,
        proportionality_ratio: float | None,
    ) -> float:
        """Compute the karma-delta suggestion for a given outcome.

            Karma scoring (Yoga Sūtras II.12–II.14 karmāśaya):
            - PERMIT_DEFENSIVE: positive karma, scaled inversely with ratio.
            Closer-to-threat defense (lower ratio) earns more credit.
            karma = karma_reward × (1 - ratio / ceiling)
            At ratio=0 → karma_reward (max).
            At ratio=ceiling → 0 (minimum positive; just barely permitted).
            - DENY_DISPROPORTIONATE: slightly negative karma (deterring escalation).
            - NOT_APPLICABLE / DENY_NONDEFENSIVE / DENY_UNKNOWN_TARGET: 0.0.

            Args:
            outcome: The circuit outcome.
            proportionality_ratio: The computed defense/threat ratio, or None.

            Returns:
            karma_delta as float.
            """
        if outcome == DharmaYuddhaOutcome.NOT_APPLICABLE:
            return 0.0

        if outcome == DharmaYuddhaOutcome.DENY_NONDEFENSIVE:
            return 0.0

        if outcome == DharmaYuddhaOutcome.DENY_UNKNOWN_TARGET:
            return 0.0

        if outcome == DharmaYuddhaOutcome.DENY_DISPROPORTIONATE:
            # Mildly negative — deterring escalation per Śānti Parva.
            return -0.05

        # PERMIT_DEFENSIVE: reward inversely proportional to force used.
        if proportionality_ratio is None:
            return self._karma_reward
        # Clamp ratio to [0, ceiling] for scoring.
        clamped = min(max(proportionality_ratio, 0.0), self._ceiling)
        # At ratio=0: full reward. At ratio=ceiling: reward × (1 - 1) = 0.
        # We add a small floor to ensure even ceiling-ratio defenses get
        # positive karma (to distinguish from disproportionate denials).
        raw = self._karma_reward * (1.0 - clamped / self._ceiling)
        floor = self._karma_reward * 0.05  # 5% floor
        return max(raw, floor)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_default_ethics_denied(
        self,
        contract: ActionContract,
        denial_reason: str | None,
        reasoning: list[str],
    ) -> bool:
        """Return True iff the default ethics engine denied the contract.

            Logic:
            - If denial_reason is a non-empty string → caller signals DENY.
            - If ethics_engine is available and denial_reason is None:
            run evaluate() and return True iff not is_ethical.
            - If no ethics_engine and denial_reason is None:
            conservative default → assume DENY (triggers dharma-yuddha eval).
            """
        if denial_reason is not None:
            # Caller explicitly supplied a denial reason.
            return True

        if self._ethics_engine is not None:
            action = contract.to_action()
            verdict = self._ethics_engine.evaluate(action)
            if verdict.is_ethical:
                reasoning.append(
                    f"EthicsEngine: is_ethical=True (score={verdict.score:.3f}) — ALLOW."
                )
                return False
            reason = (
                "; ".join(verdict.violated_constraints)
                if verdict.violated_constraints
                else "unknown"
            )
            reasoning.append(
                f"EthicsEngine: is_ethical=False (score={verdict.score:.3f}), "
                f"violations=[{reason}]."
            )
            return True

        # No ethics_engine, no denial_reason — conservative: trigger evaluation.
        reasoning.append(
            "No ethics_engine provided and no denial_reason supplied — "
            "conservative: proceeding to dharma-yuddha evaluation."
        )
        return True
