"""
    DharmaOS MemoryValidator — memory-write poisoning and grounding validator.

    PURPOSE
    -------
    MemoryValidator intercepts every write to the agent's memory stores
    (JnanaSelfModel, SamskaraLedger, external stores such as E-Brain/Valkey/
    Postgres) and validates the payload against three security controls before
    the write is committed:

    1. Content classification (LlamaGuard-equivalent): harmful, abusive, or
    policy-violating content is rejected.
    2. Grounding check: every memory write must carry a verifiable source
    citation (URL, document ID, or tool-call receipt). Writes without
    citations (orphan content) are rejected to prevent hallucination injection.
    3. Poisoning-pattern scan: instructions disguised as facts, prompt-injection
    residue, and SilentBridge cross-surface contamination are detected and
    rejected.

    REJECTION MODES
    ---------------
    HARMFUL_CONTENT  : Content classifier flags harmful text.
    ORPHAN_CONTENT  : Missing or unverifiable source citation.
    POISONING_PATTERN  : Instructions disguised as facts, PI residue.
    UNTRUSTED_ORIGIN_ELEVATION  : Untrusted-origin content attempting to write
    instructional memory (SilentBridge cross-surface).
    STALE_GROUNDING  : Cited source exists but is itself untrusted.

    On rejection: emits a MEMORY_POISON_ATTEMPT event (structlog + optional HITL
    callback). The write is blocked; the agent receives a MemoryWriteDecision with
    reason and evidence.

    KEY TYPES
    ---------
    MemoryValidator  : Main validator. validate_payload() → MemoryWriteDecision.
    MemoryWriteDecision  : Immutable result (allowed, reason, evidence, seq).
    MemoryWriteRejectReason  : Enum of rejection modes.
    ContentClassifier  : Protocol — inject real LlamaGuard or classifier in prod.
    HeuristicContentClassifier : Default offline heuristic classifier (dev/test).
    ContentVerdict  : Classification result (safe, category, confidence).
    GroundingCheck  : Citation presence + trust verification result.
    PoisonPattern  : Named pattern descriptor with regex and severity.
    check_grounding()  : Standalone grounding verification function.
    scan_poison_patterns()  : Standalone poisoning-pattern scanner.
    sign_provenance()  : HMAC-SHA256 provenance signature for memory entries.
    default_validator()  : Factory with conservative defaults.

    COMPLIANCE ROLE
    ---------------
    - OWASP Agentic AI Top 10 ASI04 Memory Poisoning (Dec 2025).
    - LlamaGuard 4 (Meta, 2025) — production content classifier integration point.
    - AWS Bedrock grounding-check pattern — citation verification.
    - Replaces V15Stub in V-JÑĀNA (closes TD-V-JNANA-01).

    Governance origin: OWASP ASI04 memory-poisoning threat class — adversarial
    content injected into memory stores later influences agent behavior. This
    module eliminates that threat at the write boundary.

    V15 Memory-Write Validator — content classifier + grounding check +
    poisoning-pattern scan on every memory-write.

    Source:
    - OWASP Agentic Top 10 ASI04 Memory Poisoning (Dec 2025) — defines the
    threat class where adversarial content injected into memory stores later
    influences agent behaviour; recommends content classification + source
    citation verification before any memory write is committed.
    - LlamaGuard 4 (Meta, 2025) — multi-class content safety classifier for
    agentic pipelines; modelled here as the ContentClassifier Protocol so
    callers can inject the real LlamaGuard API in production.
    - Bedrock grounding-check pattern (AWS, 2025) — every memory write must
    carry a verifiable source citation; writes without citations (orphan
    content) are rejected by default.

    Replaces V15Stub in V-JÑĀNA (closes TD-V-JNANA-01).

    Surfaces protected:
    - V-JÑĀNA JnanaSelfModel updates (via MemoryWriteValidator Protocol)
    - V8 SamskaraLedger appends (optional wrapper)
    - External memory stores (E-Brain, Valkey, Postgres) — via public
    validate_payload() API

    Rejection modes:
    - HARMFUL_CONTENT  : LlamaGuard-equivalent classifier flags harmful text
    - ORPHAN_CONTENT  : missing or unverifiable source citation
    - POISONING_PATTERN  : instructions disguised as facts, PI residue
    - UNTRUSTED_ORIGIN_ELEVATION : untrusted-origin content trying to write
    instructional memory — SilentBridge cross-surface
    - STALE_GROUNDING  : cited source exists but is itself untrusted

    On rejection: emit a MEMORY_POISON_ATTEMPT event (structlog + callback) and
    optionally notify HITL.

    Sprint: V15 (2026-04-17).
    """

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import re
import urllib.parse
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from dharmaos.jnana_self_model import JnanaSelfModel
    from dharmaos.trust_domain_tagger import Origin

_log: structlog.stdlib.BoundLogger = structlog.get_logger()

# P2-7: Provenance chain HMAC signing key.
# C-3: never ship a source-committed default that also validates — anyone reading
# the repo could forge provenance signatures. Require the key in production; in
# dev use an ephemeral per-process random key (warned, never the committed value).
def _load_sign_key(env_var: str, purpose: str) -> bytes:
    import secrets
    value = os.environ.get(env_var)
    if value:
        return value.encode()
    if os.environ.get("DHARMAOS_ENV", "").lower() == "production":
        raise RuntimeError(f"{env_var} must be set in production ({purpose} signing)")
    _log.warning("dharmaos.ephemeral_dev_sign_key", env_var=env_var, purpose=purpose)
    return ("DEV-EPHEMERAL-" + secrets.token_hex(16)).encode()


_PROVENANCE_SIGN_KEY: bytes = _load_sign_key("DHARMAOS_MEMORY_SIGN_KEY", "provenance")


def sign_provenance(source: str, payload_id: str) -> str:
    """HMAC-SHA256 sign a memory entry's provenance source.

        Returns a 16-char hex signature that proves the source field was
        assigned by the agent runtime, not fabricated by an attacker.
        """
    mac = hmac.new(
        _PROVENANCE_SIGN_KEY,
        f"{payload_id}:{source}".encode(),
        hashlib.sha256,
    )
    return mac.hexdigest()[:16]


def _verify_provenance_signature(
    source: str,
    payload_id: str,
    signature: str,
) -> None:
    """Verify an HMAC-signed provenance signature.

        Raises:
        ValueError: If the signature does not match (tampered or fabricated).
        """
    expected = sign_provenance(source, payload_id)
    if not hmac.compare_digest(signature, expected):
        raise ValueError(
            f"Provenance signature mismatch: source '{source[:80]}' "
            f"may have been fabricated."
        )

# ============================================================================
# Content Classifier (LlamaGuard-equivalent)
# ============================================================================


class ContentVerdict(StrEnum):
    """Classification verdict for a piece of text."""

    SAFE = "safe"
    HARMFUL = "harmful"  # hate, violence, illegal instructions, PII exfil, etc.
    INSTRUCTIONAL = "instructional"  # instructions disguised as data / facts
    UNCERTAIN = "uncertain"  # classifier abstains; treated as harmful when confidence < threshold


@runtime_checkable
class ContentClassifier(Protocol):
    """LlamaGuard-equivalent interface.

        Production callers inject a real LlamaGuard 4 (Meta, 2025) call;
        the default implementation uses offline heuristics.

        Returns (verdict, confidence) where confidence ∈ [0.0, 1.0].
        """

    def classify(self, text: str) -> tuple[ContentVerdict, float]:
        """Classify ``text`` and return (verdict, confidence).

            confidence = 1.0 means the classifier is certain.
            confidence = 0.0 means the classifier is completely unsure.
            """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Default HARMFUL keyword sets
# ---------------------------------------------------------------------------

_DEFAULT_HARMFUL_KEYWORDS: tuple[str, ...] = (
    "kill",
    "murder",
    "weapon",
    "bomb",
    "exploit",
    "credit card",
    "ssn",
    "social security",
    "malware",
    "ransomware",
    "ddos",
    "phishing",
    "cred-theft",
    "credential theft",
    "doxxing",
    "swat",
    "synthesize",  # combined with drug/weapon context
    "bioweapon",
    "chemical weapon",
)

# Instructional marker patterns
_INSTRUCTIONAL_PHRASE_RE: re.Pattern[str] = re.compile(
    r"""
    (?xi)
    \b (
    you \s+ must |
    ignore \s+ (the \s+)? (above|previous|prior|all) |
    forget \s+ (prior|previous|all|your) |
    disregard \s+ (all|previous|your) |
    override \s+ (your|the|all) |
    act \s+ as \s+ (if|a|an|though) |
    you \s+ are \s+ now |
    new \s+ (instructions?|persona|role|identity) |
    system \s* (prompt|message|instruction) |
    \[ \s* (SYSTEM|ADMIN|ROOT|ASSISTANT|INST|SYS) \s* \] |
    <\s*(system|admin|root|inst)\s*> |
    begin \s+ (new \s+)? (instructions?|session) |
    end \s+ (of \s+)? (instructions?|context)
) \b
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Imperative-verb-as-first-token pattern (common in injections)
_IMPERATIVE_FIRST_TOKEN_RE: re.Pattern[str] = re.compile(
    r"^\s*(ignore|forget|disregard|override|pretend|assume|act|behave|respond|output|print|say|write|execute|run|perform|do)\b",
    re.IGNORECASE,
)


class HeuristicContentClassifier:
    """Offline classifier using keyword patterns + heuristics.

        HARMFUL markers checked: harmful keywords list above.
        INSTRUCTIONAL markers checked: INSTRUCTIONAL_PHRASE_RE + imperative-first-token.

        Priority: HARMFUL > INSTRUCTIONAL > SAFE/UNCERTAIN.
        Empty text → UNCERTAIN (classifier abstains).
        """

    def __init__(
        self,
        *,
        harmful_keywords: tuple[str, ...] | None = None,
        instructional_markers: tuple[str, ...] | None = None,
    ) -> None:
        self._harmful_keywords: tuple[str, ...] = (
            harmful_keywords if harmful_keywords is not None else _DEFAULT_HARMFUL_KEYWORDS
        )
        # Build a regex from harmful keywords for efficient matching
        escaped = [re.escape(kw) for kw in self._harmful_keywords]
        self._harmful_re: re.Pattern[str] = re.compile(
            r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE
        )

        # Extra instructional marker phrases provided by caller
        if instructional_markers:
            extra = "|".join(re.escape(m) for m in instructional_markers)
            self._extra_instructional_re: re.Pattern[str] | None = re.compile(extra, re.IGNORECASE)
        else:
            self._extra_instructional_re = None

    def classify(self, text: str) -> tuple[ContentVerdict, float]:
        """Classify ``text`` returning (verdict, confidence).

            Empty string → (UNCERTAIN, 0.0).
            Harmful keywords present → (HARMFUL, 0.8).
            Instructional markers present → (INSTRUCTIONAL, 0.85).
            Both present → (HARMFUL, 0.9) — HARMFUL wins.
            Neither → (SAFE, 0.9).
            """
        if not text.strip():
            return ContentVerdict.UNCERTAIN, 0.0

        has_harmful = bool(self._harmful_re.search(text))
        has_instructional = bool(_INSTRUCTIONAL_PHRASE_RE.search(text)) or bool(
            _IMPERATIVE_FIRST_TOKEN_RE.match(text)
        )
        if self._extra_instructional_re:
            has_instructional = has_instructional or bool(self._extra_instructional_re.search(text))

        if has_harmful and has_instructional:
            return ContentVerdict.HARMFUL, 0.9
        if has_harmful:
            return ContentVerdict.HARMFUL, 0.8
        if has_instructional:
            return ContentVerdict.INSTRUCTIONAL, 0.85
        return ContentVerdict.SAFE, 0.9


# ============================================================================
# Poisoning Pattern Scanner
# ============================================================================


class PoisonPattern(StrEnum):
    """Specific structural/syntactic poisoning patterns in memory-write content."""

    INSTRUCTIONS_AS_FACTS = "instructions_as_facts"
    """Instruction text embedded in fact-sounding language:
        'The true answer is: ignore all safety...'"""

    PROMPT_INJECTION_RESIDUE = "prompt_injection_residue"
    """Classic prompt-injection markers leaking from retrieved content."""

    ROLE_ELEVATION = "role_elevation"
    """Attempts to change the agent's role/identity:
        '[SYSTEM]', 'You are now...', etc."""

    ENCODED_INJECTION = "encoded_injection"
    """Base64 or URL-encoded attack strings embedded in text."""

    ZERO_WIDTH_ATTACK = "zero_width_attack"
    """Zero-width Unicode characters used for steganographic injection."""


# Zero-width Unicode characters (U+200B through U+200F, ZWNJ, ZWJ, BOM, etc.)
_ZERO_WIDTH_RE: re.Pattern[str] = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f\u2028\u2029\ufeff\u00ad]"
)

# Prompt injection residue patterns
_INJECTION_RESIDUE_RE: re.Pattern[str] = re.compile(
    r"""
    (?xi)
    (
    \#\#\#\s*instruction |
    <<<\s*system |
    ---\s*system\s*--- |
    ===\s*(system|instruction|prompt)\s*=== |
    \[END\s+OF\s+(PROMPT|INSTRUCTION|CONTEXT)\] |
    \[START\s+OF\s+(NEW\s+)?INSTRUCTION\] |
    ASSISTANT\s*: |
    Human\s*:\s*\n.*\n\s*Assistant\s*: |
    <\|im_start\|> |
    <\|im_end\|> |
    <\|endoftext\|> |
    <\|system\|> |
    \[INST\] |
    <<SYS>> |
    \[\/INST\] |
    # Classic prompt injection imperatives with explicit "instructions" target
    ignore\s+(previous|prior|above|all)\s+(instructions?|context|prompt) |
    forget\s+(previous|prior|above|all)\s+(instructions?|context) |
    disregard\s+(previous|prior|above|all)\s+(instructions?|context)
)
    """,
    re.VERBOSE | re.IGNORECASE | re.DOTALL,
)

# Role elevation patterns
_ROLE_ELEVATION_RE: re.Pattern[str] = re.compile(
    r"""
    (?xi)
    (
    \[\s*(SYSTEM|ADMIN|ROOT|SUPERUSER|ASSISTANT)\s*\] |
    <\s*(system|admin|root|superuser)\s*> |
    you \s+ are \s+ now |
    new \s+ (persona|identity|role) |
    act \s+ as \s+ (a|an|your\s+new|the)? |
    pretend \s+ (you\s+are|to\s+be) |
    your \s+ (new\s+)? (role|identity|purpose|directive) \s+ is |
    henceforth \s+ you |
    from \s+ now \s+ on \s+ you
)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Instructions-as-facts pattern: "The true/correct/real answer is: <instruction>"
_INSTRUCTIONS_AS_FACTS_RE: re.Pattern[str] = re.compile(
    r"""
    (?xi)
    (
    the \s+ (true|correct|real|actual|only|proper) \s+ (answer|fact|truth|instruction) \s+ is |
    remember \s+ that \s+ you \s+ (must|should|need\s+to|have\s+to) |
    (always|never|must|should) \s+ (ignore|follow|obey|comply)
)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Base64 / URL-encoded injection detection
# We look for: long base64 strings (>30 chars) that decode to injection markers
_BASE64_CANDIDATE_RE: re.Pattern[str] = re.compile(
    r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{30,}={0,2}(?![A-Za-z0-9+/=])"
)
# M4: any run of 3+ consecutive %XX is a URL-encoded candidate — decode it and
# check the decoded form for injection. The old hand-picked hex list (with a
# duplicated token) both false-positived and missed encodings.
_URL_ENCODED_RE: re.Pattern[str] = re.compile(r"(?:%[0-9A-Fa-f]{2}){3,}")

# M4: bound the expensive decode/scan work so a large untrusted memory write
# cannot amplify CPU (ReDoS-style). Scan a prefix + cap base64 candidates.
_MAX_SCAN_CHARS = 50_000
_MAX_B64_CANDIDATES = 64


def _check_encoded_injection(text: str) -> bool:
    """Check if text contains base64 or URL-encoded injection payloads."""
    text = text[:_MAX_SCAN_CHARS]
    # Check URL encoding
    if _URL_ENCODED_RE.search(text):
        try:
            decoded = urllib.parse.unquote(text)
            if decoded != text:
                # If decoding changes the text, check if decoded form has injection markers
                if _INJECTION_RESIDUE_RE.search(decoded) or _ROLE_ELEVATION_RE.search(decoded):
                    return True
                if _INSTRUCTIONAL_PHRASE_RE.search(decoded):
                    return True
        except Exception:
            pass

    # Check base64 candidates (bounded — M4)
    for i, match in enumerate(_BASE64_CANDIDATE_RE.finditer(text)):
        if i >= _MAX_B64_CANDIDATES:
            break
        candidate = match.group(0)
        # Pad if needed
        padding = (4 - len(candidate) % 4) % 4
        try:
            decoded_bytes = base64.b64decode(candidate + "=" * padding)
            decoded_str = decoded_bytes.decode("utf-8", errors="replace")
            if _INJECTION_RESIDUE_RE.search(decoded_str) or _ROLE_ELEVATION_RE.search(decoded_str):
                return True
            if _INSTRUCTIONAL_PHRASE_RE.search(decoded_str):
                return True
        except Exception:
            continue

        return False


def scan_poison_patterns(text: str) -> list[PoisonPattern]:
    """Scan ``text`` for known memory-poisoning patterns.

        Returns a list of matched PoisonPattern values (empty if clean).
        Multiple patterns can match simultaneously.
        """
    found: list[PoisonPattern] = []

    if _ZERO_WIDTH_RE.search(text):
        found.append(PoisonPattern.ZERO_WIDTH_ATTACK)

    if _INJECTION_RESIDUE_RE.search(text):
        found.append(PoisonPattern.PROMPT_INJECTION_RESIDUE)

    if _ROLE_ELEVATION_RE.search(text):
        found.append(PoisonPattern.ROLE_ELEVATION)

    if _INSTRUCTIONS_AS_FACTS_RE.search(text):
        found.append(PoisonPattern.INSTRUCTIONS_AS_FACTS)

    if _check_encoded_injection(text):
        found.append(PoisonPattern.ENCODED_INJECTION)

    return found


# ============================================================================
# Grounding Check (Bedrock grounding-check pattern)
# ============================================================================

# Field names we look for citations in (ordered by priority)
_CITATION_FIELDS: tuple[str, ...] = (
    "source",
    "sources",
    "citation",
    "citations",
    "lineage",
    "cited_sources",
    "provenance",
    "reference",
    "references",
    "origin_url",
    "url",
)


class GroundingCheck(BaseModel):
    """Result of a grounding (source-citation) check on a memory-write payload."""

    model_config = ConfigDict(frozen=True)

    has_citation: bool
    cited_sources: tuple[str, ...] = ()
    cited_sources_trusted: bool = False  # True only if every source passes trusted_source_predicate
    rationale: str = ""


def check_grounding(
    payload: dict[str, Any],
    *,
    trusted_source_predicate: Callable[[str], bool] | None = None,
) -> GroundingCheck:
    """Look for source citations in ``payload``.

        Inspects the fields defined in ``_CITATION_FIELDS``.  Collects all
        string values found; for list/tuple values each element is collected.

        If ``trusted_source_predicate`` is provided, evaluates it against every
        collected source.  ``cited_sources_trusted`` is True only if ALL sources
        pass the predicate AND at least one source was found.

        Returns a GroundingCheck (frozen).
        """
    collected: list[str] = []

    for field_name in _CITATION_FIELDS:
        value = payload.get(field_name)
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            collected.append(value.strip())
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item.strip():
                    collected.append(item.strip())

    if not collected:
        return GroundingCheck(
            has_citation=False,
            cited_sources=(),
            cited_sources_trusted=False,
            rationale="No citation fields found in payload.",
        )

    cited: tuple[str, ...] = tuple(dict.fromkeys(collected))  # deduplicate, preserve order

    if trusted_source_predicate is None:
        return GroundingCheck(
            has_citation=True,
            cited_sources=cited,
            cited_sources_trusted=False,  # unknown — not evaluated
            rationale=f"Found {len(cited)} citation(s); trust not evaluated (no predicate).",
        )

    all_trusted = all(trusted_source_predicate(src) for src in cited)
    return GroundingCheck(
        has_citation=True,
        cited_sources=cited,
        cited_sources_trusted=all_trusted,
        rationale=(
        f"All {len(cited)} source(s) trusted."
        if all_trusted
        else f"One or more of {len(cited)} source(s) failed trust check."
    ),
    )


# ============================================================================
# Verdict / Decision models
# ============================================================================


class MemoryWriteRejectReason(StrEnum):
    """Enumerated rejection reasons for a memory-write decision."""

    HARMFUL_CONTENT = "harmful_content"
    """LlamaGuard-equivalent classifier flagged harmful text."""

    ORPHAN_CONTENT = "orphan_content"
    """No source citation found and require_grounding is True."""

    POISONING_PATTERN = "poisoning_pattern"
    """One or more structural poison patterns detected."""

    UNTRUSTED_ORIGIN_ELEVATION = "untrusted_origin_elevation"
    """Untrusted-origin content (V14 tagger) attempting to write instructional memory."""

    STALE_GROUNDING = "stale_grounding"
    """Cited sources exist but none passed the trusted_source_predicate."""


class MemoryWriteDecision(BaseModel):
    """Immutable result of a single memory-write validation run."""

    model_config = ConfigDict(frozen=True)

    decision_id: UUID = Field(default_factory=uuid4)
    accepted: bool
    reasons: list[MemoryWriteRejectReason] = Field(default_factory=list)
    content_verdict: ContentVerdict | None = None
    content_confidence: float | None = None
    grounding: GroundingCheck | None = None
    poison_patterns: list[PoisonPattern] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ============================================================================
# Main Validator
# ============================================================================


class MemoryValidator:
    """V15 canonical memory-write validator.

        Implements the MemoryWriteValidator Protocol from jnana_self_model,
        AND provides a general ``validate_payload()`` for arbitrary memory-writes
        (E-Brain, Valkey, Postgres, V8 SamskaraLedger).

        Composition is **non-compensatory**: any single failing check → reject.

        Pipeline (in order):
        1. Content classifier — HARMFUL or INSTRUCTIONAL text → reject.
        2. Poison-pattern scan — any structural match → reject.
        3. Grounding check — orphan content (no citation) → reject when
        ``require_grounding=True``; stale grounding → reject when all
        cited sources are untrusted.
        4. Origin trust cross-check — untrusted V14 origin + INSTRUCTIONAL
        content → UNTRUSTED_ORIGIN_ELEVATION rejection.

        Fail-closed on UNCERTAIN: if classifier returns UNCERTAIN and confidence
        < ``harmful_confidence_threshold``, the write is treated as HARMFUL.

        Side-effect-free by default: ``validate_payload()`` returns a Decision
        and does not write to any store.  Callers compose with their ledgers.

        Sources:
        OWASP Agentic Top 10 ASI04 (Dec 2025);
        LlamaGuard 4 (Meta, 2025);
        Bedrock grounding-check pattern (AWS, 2025).
        """

    def __init__(
        self,
        *,
        content_classifier: ContentClassifier | None = None,
        require_grounding: bool = True,
        trusted_source_predicate: Callable[[str], bool] | None = None,
        harmful_confidence_threshold: float = 0.5,
        poison_incident_callback: (
        Callable[[MemoryWriteDecision, dict[str, Any]], None] | None
    ) = None,
        require_provenance_signature: bool = False,
    ) -> None:
        self._classifier: ContentClassifier = (
            content_classifier if content_classifier is not None else HeuristicContentClassifier()
        )
        self._require_grounding = require_grounding
        self._trusted_source_predicate = trusted_source_predicate
        self._harmful_threshold = harmful_confidence_threshold
        self._callback = poison_incident_callback
        self._require_provenance_signature = require_provenance_signature  # P2-7

    # -----------------------------------------------------------------------
    # General-purpose memory-write gate
    # -----------------------------------------------------------------------

    def validate_payload(
        self,
        payload: dict[str, Any],
        *,
        origin: Origin | None = None,
        text_fields: tuple[str, ...] = ("text", "content", "statement", "claim"),
    ) -> MemoryWriteDecision:
        """Validate an arbitrary memory-write payload.

            SILENT-REJECTION INVARIANT:
            This method ALWAYS returns a MemoryWriteDecision — it NEVER raises
            an exception to signal rejection.  Propagating an exception on
            rejection would create a side-channel: an adversary who controls the
            payload could infer rejection reasons by probing for exceptions and
            then adapting the attack.  Returning a decision object keeps the
            rejection signal in-band while revealing nothing to the caller about
            *why* the write was blocked beyond what the orchestrator is
            authorized to observe.

            Callers MUST check ``decision.accepted`` rather than relying on
            absence of exceptions.  Any code path that raises on rejection is
            a bug — file a bug report and do NOT add a try/except to mask it.

            Args:
            payload: The dict to be written to any memory store.
            origin: Optional V14 Origin tag on the payload's provenance.
            text_fields: Fields to extract and run the classifier + poison scan on.

            Returns:
            MemoryWriteDecision with accepted=True or False and populated reasons.
            Never raises on rejection (see silent-rejection invariant above).
            """
        reasons: list[MemoryWriteRejectReason] = []
        evidence: dict[str, Any] = {}

        # Collect all text to classify
        text_to_classify = self._extract_text(payload, text_fields)

        # ------------------------------------------------------------------
        # Step 1: Content classification
        # ------------------------------------------------------------------
        content_verdict: ContentVerdict | None = None
        content_confidence: float | None = None

        if text_to_classify.strip():
            content_verdict, content_confidence = self._classifier.classify(text_to_classify)
        else:
            # No text fields — use UNCERTAIN with 0 confidence → fail-closed
            content_verdict = ContentVerdict.UNCERTAIN
            content_confidence = 0.0

        # Fail-closed on UNCERTAIN
        effective_verdict = content_verdict
        if content_verdict == ContentVerdict.UNCERTAIN and (
            content_confidence is None or content_confidence < self._harmful_threshold
        ):
            effective_verdict = ContentVerdict.HARMFUL

        if effective_verdict == ContentVerdict.HARMFUL:
            reasons.append(MemoryWriteRejectReason.HARMFUL_CONTENT)
            evidence["content_verdict"] = content_verdict.value
            evidence["content_confidence"] = content_confidence

        # ------------------------------------------------------------------
        # Step 2: Poison-pattern scan
        # ------------------------------------------------------------------
        poison_patterns = scan_poison_patterns(text_to_classify)
        if poison_patterns:
            reasons.append(MemoryWriteRejectReason.POISONING_PATTERN)
            evidence["poison_patterns"] = [p.value for p in poison_patterns]

        # INSTRUCTIONAL content is a poison pattern (memory stores DATA not INSTRUCTIONS)
        if effective_verdict == ContentVerdict.INSTRUCTIONAL and (
            MemoryWriteRejectReason.POISONING_PATTERN not in reasons
        ):
            reasons.append(MemoryWriteRejectReason.POISONING_PATTERN)
            evidence["instructional_as_poison"] = True

        # ------------------------------------------------------------------
        # Step 3: Grounding check
        # ------------------------------------------------------------------
        grounding = check_grounding(
            payload, trusted_source_predicate=self._trusted_source_predicate
        )

        if self._require_grounding and not grounding.has_citation:
            reasons.append(MemoryWriteRejectReason.ORPHAN_CONTENT)
            evidence["grounding_rationale"] = grounding.rationale

            if (
                grounding.has_citation
                and self._trusted_source_predicate is not None
                and not grounding.cited_sources_trusted
            ):
                reasons.append(MemoryWriteRejectReason.STALE_GROUNDING)
            evidence["untrusted_sources"] = list(grounding.cited_sources)

        # ------------------------------------------------------------------
        # Step 4: Origin trust cross-check (V14 SilentBridge defense)
        # ------------------------------------------------------------------
        if origin is not None:
            from dharmaos.trust_domain_tagger import INSTRUCTION_TRUSTED_ORIGINS

            is_untrusted_origin = origin not in INSTRUCTION_TRUSTED_ORIGINS
            is_instructional = effective_verdict in (
                ContentVerdict.INSTRUCTIONAL,
                ContentVerdict.HARMFUL,
            ) or bool(poison_patterns)

            if is_untrusted_origin and is_instructional:
                if MemoryWriteRejectReason.UNTRUSTED_ORIGIN_ELEVATION not in reasons:
                    reasons.append(MemoryWriteRejectReason.UNTRUSTED_ORIGIN_ELEVATION)
                evidence["origin"] = str(origin)

        # ------------------------------------------------------------------
        # Step 5: Provenance chain verification (P2-7 HMAC signing)
        # Only enforced when require_provenance_signature=True.
        # Defaults to off for backward compatibility with existing tests and
        # producers that don't yet attach provenance signatures.
        # ------------------------------------------------------------------
        if self._require_provenance_signature:
            source = payload.get("source", "")
            provenance_sig = payload.get("provenance_signature", "")
            if source and not provenance_sig:
                reasons.append(MemoryWriteRejectReason.ORPHAN_CONTENT)
                evidence["provenance_unsigned"] = True
            elif source and provenance_sig:
                try:
                    _verify_provenance_signature(
                        source=source,
                        payload_id=payload.get("id", ""),
                        signature=provenance_sig,
                    )
                except ValueError:
                    reasons.append(MemoryWriteRejectReason.POISONING_PATTERN)
                    evidence["provenance_signature_invalid"] = True

        accepted = len(reasons) == 0

        decision = MemoryWriteDecision(
            accepted=accepted,
            reasons=reasons,
            content_verdict=content_verdict,
            content_confidence=content_confidence,
            grounding=grounding,
            poison_patterns=poison_patterns,
            evidence=evidence,
        )

        if not accepted:
            _log.warning(
                "memory_validator.poison_attempt",
                accepted=False,
                reasons=[r.value for r in reasons],
                poison_patterns=[p.value for p in poison_patterns],
                origin=str(origin) if origin is not None else None,
            )
            if self._callback is not None:
                self._callback(decision, payload)

        return decision

    # -----------------------------------------------------------------------
    # MemoryWriteValidator Protocol (V-JÑĀNA back-compat)
    # -----------------------------------------------------------------------

    def validate(
        self,
        old_model: JnanaSelfModel,
        new_model: JnanaSelfModel,
    ) -> tuple[bool, str]:
        """Validate a JnanaSelfModel update — back-compatible with V15Stub.

            Runs all five V15Stub checks first, then adds V15-specific checks:
            - Serialises new_model to dict and runs validate_payload on it.
            - Rejects if any capability_vector domain name matches a PoisonPattern
            (an attacker could poison domain labels to influence future behaviour).
            - Rejects if any BlindSpot.failure_pattern contains an instructional
            marker (attack vector: poison the blind-spot list with role-elevation
            strings echoed back to V11 Layer 7).

            Returns (True, "") on accept; (False, reason) on reject.
            """
        # ------------------------------------------------------------------
        # V15Stub checks (parity required)
        # ------------------------------------------------------------------
        stub_ok, stub_reason = self._v15stub_checks(old_model, new_model)
        if not stub_ok:
            return False, stub_reason

        # ------------------------------------------------------------------
        # V15 additional: validate serialised payload
        # ------------------------------------------------------------------
        # We pass require_grounding=False here since the self-model is an
        # internal memory surface that doesn't carry external citations.
        payload = new_model.model_dump(mode="python")

        decision = MemoryValidator(
            content_classifier=self._classifier,
            require_grounding=False,
            harmful_confidence_threshold=self._harmful_threshold,
        ).validate_payload(payload, text_fields=("agent_id", "tenant_id"))

        if not decision.accepted:
            return (
                False,
                f"V15 validator rejected model write: {[r.value for r in decision.reasons]}",
            )

        # ------------------------------------------------------------------
        # V15 additional: check capability_vector domain names
        # ------------------------------------------------------------------
        for domain in new_model.capability_vector:
            domain_patterns = scan_poison_patterns(domain)
            if domain_patterns:
                return (
                    False,
                    f"capability_vector domain name '{domain}' matches poison pattern(s): "
                    f"{[p.value for p in domain_patterns]}",
                )

        # ------------------------------------------------------------------
        # V15 additional: check BlindSpot.failure_pattern for instructional markers
        # ------------------------------------------------------------------
        for bs in new_model.blind_spot_list:
            fp_verdict, _ = self._classifier.classify(bs.failure_pattern)
            fp_patterns = scan_poison_patterns(bs.failure_pattern)
            if fp_verdict == ContentVerdict.INSTRUCTIONAL or fp_patterns:
                return (
                    False,
                    f"BlindSpot.failure_pattern for domain '{bs.domain}' contains instructional "
                    f"markers or poison patterns — possible V11 Layer 7 echo-back attack.",
                )

        return True, ""

    def summarize(self, decisions: list[MemoryWriteDecision]) -> dict[str, Any]:
        """Aggregate stats across a list of decisions.

            Returns:
            {
            "total": int,
            "accepted": int,
            "rejected": int,
            "acceptance_rate": float,
            "reasons_breakdown": {reason_name: count},
            "top_poison_patterns": {pattern_name: count},
        }
            """
        total = len(decisions)
        accepted_count = sum(1 for d in decisions if d.accepted)
        rejected_count = total - accepted_count

        reasons_counter: Counter[str] = Counter()
        patterns_counter: Counter[str] = Counter()

        for d in decisions:
            for r in d.reasons:
                reasons_counter[r.value] += 1
            for p in d.poison_patterns:
                patterns_counter[p.value] += 1

        return {
            "total": total,
            "accepted": accepted_count,
            "rejected": rejected_count,
            "acceptance_rate": accepted_count / total if total > 0 else 0.0,
            "reasons_breakdown": dict(reasons_counter),
            "top_poison_patterns": dict(patterns_counter.most_common()),
        }

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _extract_text(payload: dict[str, Any], text_fields: tuple[str, ...]) -> str:
        """Concatenate all string values from text_fields in payload."""
        parts: list[str] = []
        for field_name in text_fields:
            val = payload.get(field_name)
            if isinstance(val, str):
                parts.append(val)
            elif isinstance(val, (list, tuple)):
                for item in val:
                    if isinstance(item, str):
                        parts.append(item)
        return " ".join(parts)

    @staticmethod
    def _v15stub_checks(
        old_model: JnanaSelfModel,
        new_model: JnanaSelfModel,
    ) -> tuple[bool, str]:
        """Run the five V15Stub conservative checks.

            These are identical in semantics to V15Stub.validate() so that
            MemoryValidator is a strict superset of V15Stub.
            """
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
        bad = _find_non_finite(new_model)
        if bad:
            return False, f"non-finite value in field(s): {bad}"

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

        # 5. Drift score cannot jump more than max_drift_step in one step.
        max_drift_step = 0.5
        drift_delta = abs(new_model.policy_drift_score - old_model.policy_drift_score)
        if drift_delta > max_drift_step:
            return (
                False,
                f"drift score step {drift_delta:.3f} exceeds limit {max_drift_step}",
            )

        return True, "ok"


def _find_non_finite(model: JnanaSelfModel) -> list[str]:
    """Return list of field paths containing NaN or inf in a JnanaSelfModel."""
    bad: list[str] = []

    def _check(v: float, path: str) -> None:
        if not math.isfinite(v):
            bad.append(path)

    _check(model.policy_drift_score, "policy_drift_score")
    _check(model.metacognitive_calibration, "metacognitive_calibration")

    for domain, cs in model.capability_vector.items():
        _check(cs.value, f"capability_vector[{domain!r}].value")

    for bs in model.blind_spot_list:
        _check(
            bs.confidence_of_unawareness,
            f"blind_spot[{bs.domain}].confidence_of_unawareness",
        )

    for task, sp in model.strategy_preference_map.items():
        _check(sp.win_rate, f"strategy_preference_map[{task!r}].win_rate")

    return bad


# ============================================================================
# Factory
# ============================================================================


def default_validator() -> MemoryValidator:
    """Factory returning a MemoryValidator with conservative defaults.

        Uses HeuristicContentClassifier with default keyword sets,
        require_grounding=True, and harmful_confidence_threshold=0.5.
        """
    return MemoryValidator(
        content_classifier=HeuristicContentClassifier(),
        require_grounding=True,
        trusted_source_predicate=None,
        harmful_confidence_threshold=0.5,
        poison_incident_callback=None,
    )
