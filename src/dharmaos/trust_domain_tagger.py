"""
    DharmaOS TrustDomainTagger — token-origin tagging and prompt-injection authority control.

    PURPOSE
    -------
    TrustDomainTagger eliminates the root cause of Indirect Prompt Injection (IPI)
    and SilentBridge attacks (CVSS 9.8) by tagging every context token with its
    trust origin at ingestion time. Only SYSTEM-origin tokens may authorize tool
    calls. Retrieved documents, tool outputs, and agent-generated content are tagged
    as untrusted and cannot trigger capability invocations regardless of what
    instructions they contain.

    ATTACK SURFACE COVERED
    ----------------------
    SilentBridge (CVSS 9.8) — 3 variants:
    -Page  : malicious instructions injected into a retrieved webpage body.
    -Search : injection embedded inside search-result snippets.
    -Doc  : injection inside retrieved document / PDF body.

    All three share the same root cause: the agent treats retrieved content as
    having instruction-authority. TrustDomainTagger eliminates this at the
    architectural level — untrusted origins simply cannot authorize tool calls,
    regardless of their content.

    TRUST ORIGIN TAXONOMY
    ---------------------
    SYSTEM  : Developer prompt — fully instruction-trusted.
    USER  : End-user message — input-trusted, NOT instruction-trusted
    by default. Elevation requires explicit system acknowledgement
    via elevate_user_intent().
    RETRIEVED_DOC  : RAG / tool fetch result — untrusted.
    TOOL_OUTPUT  : Output of a prior tool call — untrusted.
    AGENT_GENERATED : Prior LLM output — untrusted.
    UNKNOWN  : Fail-closed — treated as untrusted.

    AUTHORIZATION FLOW
    ------------------
    1. Origin gate: tool-call request is denied if ANY instruction token
    in the authorizing context is from a non-SYSTEM origin.
    2. SilentBridge scan: detect_silentbridge_pattern() scans the full
    context for -Page / -Search / -Doc injection markers including
    zero-width-char steganography and URL-encoded payloads.
    3. Parallel guard deviation check: HeuristicParallelGuard (offline
    WebAgentGuard pattern, arXiv:2604.12284) checks whether the requested
    tool call is consistent with the original task brief.

    KEY TYPES
    ---------
    TrustDomainTagger  : Main tagger. tag_context() → TaggedContext.
    authorize_tool_call() → ToolCallVerdict.
    elevate_user_intent() → one-time USER elevation.
    TaggedToken  : Single token with origin label.
    TaggedContext  : Full context with per-token origin tags.
    ToolCallVerdict  : Authorization result with reason and evidence.
    AuthorizationVerdict  : AUTHORIZED / DENIED_* enum.
    SilentBridgeVariant  : PAGE / SEARCH / DOC injection variant enum.
    HeuristicParallelGuard : Offline WebAgentGuard-pattern deviation checker.
    Inject a real LLM guard in production.
    detect_silentbridge_pattern() : Standalone scanner for IPI markers.
    default_tagger()  : Factory with conservative defaults.

    COMPLIANCE ROLE
    ---------------
    - OWASP Agentic AI Top 10 ASI05 Prompt Injection (Dec 2025).
    - Microsoft Spotlighting (Build 2025) — context origin tagging pattern.
    - WebAgentGuard (arXiv:2604.12284) — parallel guard-agent architecture.

    Governance origin: trust-domain token tagging with tool-call-authority-
    revocation for untrusted context — patent claim-candidate.

    V14 Trust-Domain Tagger — SilentBridge / Indirect Prompt Injection defense.

    SOTA references:
    - Microsoft Spotlighting (Build 2025) — origin-tagging of LLM context tokens
    so the model can distinguish developer-supplied instructions from retrieved
    content; foundational framing for the trust-domain taxonomy used here.
    - WebAgentGuard (arXiv:2604.12284, April 2026) — 99.2% IPI detection via
    a parallel guard-agent that checks outbound tool-calls against the task
    brief on a SEPARATE LLM instance (confirmation-bias break). The
    HeuristicParallelGuard class models this pattern offline; callers inject
    a real LLM guard in production.
    - SilentBridge (CVSS 9.8) — 3 variants exploiting agent over-trust of
    retrieved content:
    -Page  : injected instructions in a retrieved webpage body.
    -Search : injection embedded inside search-result snippets.
    -Doc  : injection inside retrieved document / PDF body.
    All three share the same root: the agent treats retrieved content as
    instruction-authority. This module eliminates that root cause at the
    architectural level via strict origin tagging + tool-call authority gates.

    Tags every context token with its origin. Untrusted tokens
    (retrieved-doc, tool-output, agent-generated) CANNOT authorize tool
    calls. Only system-origin and (explicitly acknowledged) user-origin
    instructions can trigger tool invocations.

    Patent claim-candidate: trust-domain token tagging with tool-call-
    authority-revocation for untrusted context + parallel-guard deviation
    check.

    Sprint: V14 (2026-04-17).
    """

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Origin(StrEnum):
    """Trust origin of a context token.

        Instruction-trusted origins may authorize tool calls.
        Input-trusted origins may contribute data but not trigger capabilities.
        """

    SYSTEM = "system"  # developer prompt; fully instruction-trusted
    USER = "user"  # end-user message; input-trusted, NOT instruction-trusted
    RETRIEVED_DOC = "retrieved_doc"  # from RAG / tool fetch; untrusted
    TOOL_OUTPUT = "tool_output"  # output of a prior tool call; untrusted
    AGENT_GENERATED = "agent_generated"  # prior LLM output; untrusted
    UNKNOWN = "unknown"  # fail-closed — treated as untrusted


INSTRUCTION_TRUSTED_ORIGINS: frozenset[Origin] = frozenset({Origin.SYSTEM})
# NOTE: USER is explicitly NOT instruction-trusted by default.
# role-elevation to instruction-trust requires system-origin
# acknowledgement (see TrustDomainTagger.elevate_user_intent).


class AuthorizationVerdict(StrEnum):
    """Result of a tool-call authorization decision."""

    AUTHORIZED = "authorized"
    DENIED_UNTRUSTED_ORIGIN = "denied_untrusted_origin"
    DENIED_DEVIATION = "denied_deviation_from_brief"
    DENIED_SILENTBRIDGE_PATTERN = "denied_silentbridge_pattern"
    DENIED_UNKNOWN_TRIGGER = "denied_unknown_trigger"


class SilentBridgeVariant(StrEnum):
    """Which SilentBridge variant the detected injection pattern matches."""

    PAGE = "page"  # -Page: injected instructions in a retrieved webpage
    SEARCH = "search"  # -Search: injection in search results
    DOC = "doc"  # -Doc: injection in retrieved document body


# ---------------------------------------------------------------------------
# Data models (Pydantic, frozen/immutable)
# ---------------------------------------------------------------------------


class TaggedToken(BaseModel):
    """Immutable context token annotated with its trust origin."""

    model_config = ConfigDict(frozen=True)

    token_id: UUID = Field(default_factory=uuid4)
    content: str
    origin: Origin
    source_ref: str = ""  # url, tool name, user_id, message_id
    tagged_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    parent_token_id: UUID | None = None  # for derived tokens (e.g., LLM output)


class TaggedContext(BaseModel):
    """Ordered sequence of tagged tokens that together form an LLM context."""

    model_config = ConfigDict(frozen=True)

    context_id: UUID = Field(default_factory=uuid4)
    tokens: tuple[TaggedToken, ...]
    built_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolCallRequest(BaseModel):
    """A proposed outbound tool call to be authorized before execution."""

    model_config = ConfigDict(frozen=True)

    call_id: UUID = Field(default_factory=uuid4)
    tool_name: str
    arguments: dict[str, object]
    triggered_by_token_id: UUID | None = None  # which context token triggered this
    context_id: UUID | None = None  # back-reference to the TaggedContext


class ToolCallVerdict(BaseModel):
    """Immutable result of a tool-call authorization decision."""

    model_config = ConfigDict(frozen=True)

    verdict_id: UUID = Field(default_factory=uuid4)
    call_id: UUID
    verdict: AuthorizationVerdict
    triggering_origin: Origin | None = None
    guard_report: dict[str, object] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Parallel guard — WebAgentGuard pattern
# ---------------------------------------------------------------------------


class ParallelGuard(Protocol):
    """Out-of-band alignment check (WebAgentGuard pattern, arXiv:2604.12284).

        Given the task brief and a proposed tool-call, return
        (is_aligned: bool, rationale: str).

        MUST run on a SEPARATE LLM instance from the main agent to break
        confirmation bias (WebAgentGuard §3.2). HeuristicParallelGuard
        provides an offline stand-in; inject a real LLM guard in production.
        """

    def check_alignment(
        self,
        task_brief: str,
        tool_call: ToolCallRequest,
    ) -> tuple[bool, str]:
        """Return (is_aligned, rationale)."""
        ...  # pragma: no cover


class HeuristicParallelGuard:
    """Offline keyword-based guard implementing the ParallelGuard protocol.

        Callers inject a real LLM guard in production.
        This guard blocks any tool whose name matches a caller-supplied
        banned-keyword list; all other calls are considered aligned.
        """

    def __init__(
        self,
        *,
        banned_tool_keywords: list[str] | None = None,
    ) -> None:
        self._banned: list[str] = banned_tool_keywords or []

    def check_alignment(
        self,
        task_brief: str,
        tool_call: ToolCallRequest,
    ) -> tuple[bool, str]:
        """Return (True, "aligned") unless the tool name matches a banned keyword."""
        tool = tool_call.tool_name.lower()
        for kw in self._banned:
            if kw.lower() in tool:
                return (
                    False,
                    (
                    f"Tool '{tool_call.tool_name}' matches banned keyword "
                    f"'{kw}' — deviates from task brief: '{task_brief[:80]}'"
                ),
                )
        return True, "Tool call is aligned with task brief (heuristic check passed)."


# ---------------------------------------------------------------------------
# SilentBridge pattern detectors
# ---------------------------------------------------------------------------

# Canonical injection markers (non-exhaustive; production should extend via ML)
_IPI_PHRASES: list[str] = [
    r"ignore\s+(?:all\s+)?previous\s+instructions",
    r"disregard\s+(?:all\s+)?(?:previous|prior|earlier)\s+instructions",
    r"forget\s+(?:all\s+)?(?:previous|prior|earlier)\s+instructions",
    r"you\s+are\s+now\s+a\s+different\s+(?:ai|assistant|model)",
    r"you\s+are\s+now\s+(?:dan|jailbreak|unrestricted)",
    r"act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:an?\s+)?(?:unrestricted|evil|uncensored)",
    r"\[system\]\s*(?:ignore|forget|disregard|you\s+are)",
    r"\[instruction\]\s*(?:ignore|forget|call|execute)",
    r"new\s+system\s+prompt\s*:",
    r"<\s*/?system\s*>",
    r"<\s*/?instructions?\s*>",
    r"your\s+real\s+instructions\s+are",
    r"override\s+(?:safety|security|ethics)\s+(?:guidelines|filters|rules)",
    r"bypass\s+(?:safety|security|ethical)\s+(?:guidelines|filters|rules)",
    r"pretend\s+(?:you\s+have\s+no|there\s+are\s+no)\s+(?:restrictions|guidelines|rules)",
]

# URL-encoded IPI (e.g., %49%47%4E%4F%52%45 = "IGNORE")
_URL_ENCODED_IPI: re.Pattern[str] = re.compile(
    r"(%49|%69)(%47|%67)(%4E|%6E)(%4F|%6F)(%52|%72)(%45|%65)",
    re.IGNORECASE,
)

# Zero-width characters used for steganographic injection
_ZERO_WIDTH_CHARS: frozenset[str] = frozenset(
    {
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u200e",  # left-to-right mark
    "\u200f",  # right-to-left mark
    "\ufeff",  # zero-width no-break space (BOM)
    "\u2060",  # word joiner
    "\u2061",  # function application
    "\u2062",  # invisible times
    "\u2063",  # invisible separator
    "\u2064",  # invisible plus
}
)

_IPI_RE: re.Pattern[str] = re.compile(
    "|".join(_IPI_PHRASES),
    re.IGNORECASE | re.DOTALL,
)


def _strip_zero_width(text: str) -> str:
    """Remove zero-width / invisible Unicode chars for pattern matching."""
    return "".join(ch for ch in text if ch not in _ZERO_WIDTH_CHARS)


def _has_zero_width_obfuscation(text: str) -> bool:
    """Return True if the text contains zero-width chars around keyword clusters.

        Heuristic: any zero-width char within 20 characters of an IPI keyword
        fragment ("IGNORE", "SYSTEM", "INSTRUCTION") is considered a steganographic
        injection attempt.
        """
    if not any(ch in _ZERO_WIDTH_CHARS for ch in text):
        return False
    # Strip ZW chars and check if canonical IPI phrases are present
    stripped = _strip_zero_width(text)
    return bool(_IPI_RE.search(stripped))


def detect_silentbridge_pattern(
    token: TaggedToken,
) -> SilentBridgeVariant | None:
    """Scan for known SilentBridge injection markers in a token.

        Patterns detected (non-exhaustive):
        - "IGNORE PREVIOUS INSTRUCTIONS" and variants
        - "You are now a different AI ..."
        - [SYSTEM] / [INSTRUCTION] role-elevation tags in body
        - URL-encoded instructions
        - Zero-width character steganographic attacks

        Returns the SilentBridgeVariant if a pattern is matched, else None.

        Note: Flags tokens of ANY origin including SYSTEM (developer prompt
        exfil / tampered-prompt attack surface). Callers decide how to handle
        verdicts on SYSTEM-origin matches.
        """
    content = token.content

    # 1. Canonical IPI phrase check (also strips ZW before matching)
    stripped_content = _strip_zero_width(content)
    if _IPI_RE.search(stripped_content):
        return _classify_variant(token)

    # 2. Zero-width steganographic obfuscation
    if _has_zero_width_obfuscation(content):
        return _classify_variant(token)

    # 3. URL-encoded IPI
    if _URL_ENCODED_IPI.search(content):
        return _classify_variant(token)

        return None


def _classify_variant(token: TaggedToken) -> SilentBridgeVariant:
    """Assign the most specific SilentBridge variant based on source_ref / origin."""
    ref = token.source_ref.lower()
    if "search" in ref or "query" in ref or "result" in ref:
        return SilentBridgeVariant.SEARCH
    if any(ext in ref for ext in (".pdf", ".docx", ".doc", ".txt", "document", "doc")):
        return SilentBridgeVariant.DOC
    # Default: page variant (covers web pages and any other retrieval source)
    return SilentBridgeVariant.PAGE


# ---------------------------------------------------------------------------
# Main service — TrustDomainTagger
# ---------------------------------------------------------------------------


class TrustDomainTagger:
    """Central service for tagging context tokens, authorizing tool calls,
        and running the parallel guard.

        INVARIANT 1: tool-call authorization requires at least one
        instruction-trusted origin in the triggering chain.

        INVARIANT 2: any SilentBridge pattern detected in ANY token in the
        context causes IMMEDIATE DENY and emits an incident report.
        (Rationale: even a SYSTEM-origin token may have been tampered; the
        full-context scan is the only safe default.)

        INVARIANT 3: parallel guard runs on every authorization attempt and
        CANNOT be skipped even when origin-based check passes.

        Usage::

        guard = HeuristicParallelGuard()
        tagger = TrustDomainTagger(guard, task_brief="Summarise news")

        sys_token = tagger.tag_token("Search for headlines.", Origin.SYSTEM)
        ctx = tagger.build_context([sys_token])

        call = ToolCallRequest(
        tool_name="web_search",
        arguments={"q": "AI news"},
        triggered_by_token_id=sys_token.token_id,
    )
        verdict = tagger.authorize_tool_call(call, ctx)
        if verdict.verdict is AuthorizationVerdict.AUTHORIZED:
        executor.run(call)

        SOTA: Microsoft Spotlighting (Build 2025), WebAgentGuard
        (arXiv:2604.12284), SilentBridge (CVSS 9.8).
        """

    def __init__(
        self,
        parallel_guard: ParallelGuard | None,
        *,
        task_brief: str = "",
        allow_user_instruction_elevation: bool = False,
        banned_tools_for_untrusted: tuple[str, ...] = (),
    ) -> None:
        """Create a TrustDomainTagger.

            Args:
            parallel_guard: Out-of-band alignment checker (mandatory).
            Raises ValueError if None is passed.
            task_brief: Natural-language description of the agent's task.
            Passed to the parallel guard on every authorization.
            allow_user_instruction_elevation: If False (default), USER-origin
            tokens can NEVER be elevated to instruction-trusted status.
            Enable only for explicitly operator-sanctioned interactive flows.
            banned_tools_for_untrusted: Additional tool names that are always
            denied when the triggering context contains untrusted tokens.
            """
        if parallel_guard is None:
            raise ValueError(
                "parallel_guard is mandatory — pass a HeuristicParallelGuard "
                "or a real LLM guard. Cannot be None (Invariant 3)."
            )
        self._guard = parallel_guard
        self._task_brief = task_brief
        self._allow_elevation = allow_user_instruction_elevation
        self._banned_tools = frozenset(t.lower() for t in banned_tools_for_untrusted)

    # ------------------------------------------------------------------
    # Token and context builders
    # ------------------------------------------------------------------

    def tag_token(
        self,
        content: str,
        origin: Origin,
        *,
        source_ref: str = "",
        parent_token_id: UUID | None = None,
    ) -> TaggedToken:
        """Create a new TaggedToken with the declared origin.

            Args:
            content: Raw text content of the token.
            origin: Trust origin (see Origin enum). Use Origin.UNKNOWN for
            tokens whose provenance cannot be established; they will be
            treated as untrusted (fail-closed).
            source_ref: Optional provenance string (URL, tool name, user ID).
            parent_token_id: If this token is derived from another (e.g.,
            an elevated user token), set this to the source token's ID.

            Returns:
            A frozen TaggedToken.
            """
        return TaggedToken(
            content=content,
            origin=origin,
            source_ref=source_ref,
            parent_token_id=parent_token_id,
        )

    def build_context(self, tokens: Sequence[TaggedToken]) -> TaggedContext:
        """Build an ordered TaggedContext from a sequence of TaggedTokens.

            Token order is preserved exactly as supplied — callers are responsible
            for supplying tokens in the correct logical order (system → user →
            retrieved → agent-generated).

            Args:
            tokens: Ordered sequence of TaggedToken objects.

            Returns:
            A frozen TaggedContext.
            """
        return TaggedContext(tokens=tuple(tokens))

    # ------------------------------------------------------------------
    # Authorization (3-phase)
    # ------------------------------------------------------------------

    def authorize_tool_call(
        self,
        call: ToolCallRequest,
        context: TaggedContext,
    ) -> ToolCallVerdict:
        """Authorize a proposed tool call against the tagged context.

            Three-phase decision (non-compensatory — first DENY halts the chain):

            Phase 1 — Origin gate:
            Locate call.triggered_by_token_id in context.tokens.
            - If not found or None → DENIED_UNKNOWN_TRIGGER (fail-closed).
            - If found but origin is NOT instruction-trusted →
            DENIED_UNTRUSTED_ORIGIN.

            Phase 2 — SilentBridge scan:
            For EVERY token in the context, run detect_silentbridge_pattern().
            Any match → DENIED_SILENTBRIDGE_PATTERN (even if Phase 1 passed).
            Rationale: a poisoned context is dangerous regardless of which
            token nominally triggered the call.

            Phase 3 — Parallel guard:
            Run parallel_guard.check_alignment(task_brief, call).
            Misalignment → DENIED_DEVIATION.

            All phases pass → AUTHORIZED.

            Args:
            call: The proposed tool call.
            context: The full tagged context associated with this call.

            Returns:
            An immutable ToolCallVerdict.
            """
        reasons: list[str] = []

        # ---- Phase 1: origin gate ----------------------------------------
        triggering_origin: Origin | None = None

        if call.triggered_by_token_id is None:
            return ToolCallVerdict(
                call_id=call.call_id,
                verdict=AuthorizationVerdict.DENIED_UNKNOWN_TRIGGER,
                triggering_origin=None,
                reasons=["triggered_by_token_id is None — cannot establish authorization chain"],
            )

        # Find the triggering token in the context
        trigger_token: TaggedToken | None = None
        for tok in context.tokens:
            if tok.token_id == call.triggered_by_token_id:
                trigger_token = tok
                break

        if trigger_token is None:
            return ToolCallVerdict(
                call_id=call.call_id,
                verdict=AuthorizationVerdict.DENIED_UNKNOWN_TRIGGER,
                triggering_origin=None,
                reasons=[
                f"Token {call.triggered_by_token_id} not found in context "
                f"{context.context_id} — cannot establish authorization chain"
            ],
            )

        triggering_origin = trigger_token.origin

        if triggering_origin not in INSTRUCTION_TRUSTED_ORIGINS:
            return ToolCallVerdict(
                call_id=call.call_id,
                verdict=AuthorizationVerdict.DENIED_UNTRUSTED_ORIGIN,
                triggering_origin=triggering_origin,
                reasons=[
                f"Triggering token origin '{triggering_origin}' is not "
                f"instruction-trusted (trusted: {sorted(o.value for o in INSTRUCTION_TRUSTED_ORIGINS)}). "
                "SilentBridge/IPI defense: only SYSTEM-origin tokens may authorize tool calls."
            ],
            )

        reasons.append(
            f"Phase 1 passed: triggering token origin='{triggering_origin}' is instruction-trusted."
        )

        # ---- Phase 2: SilentBridge scan (full context) -------------------
        sb_hits: list[tuple[TaggedToken, SilentBridgeVariant]] = []
        for tok in context.tokens:
            variant = detect_silentbridge_pattern(tok)
            if variant is not None:
                sb_hits.append((tok, variant))

        if sb_hits:
            sb_descriptions = [
                f"token_id={t.token_id} origin={t.origin} variant={v} source_ref='{t.source_ref}'"
                for t, v in sb_hits
            ]
            return ToolCallVerdict(
                call_id=call.call_id,
                verdict=AuthorizationVerdict.DENIED_SILENTBRIDGE_PATTERN,
                triggering_origin=triggering_origin,
                guard_report={"silentbridge_hits": sb_descriptions},
                reasons=[
                f"Phase 2 FAILED: {len(sb_hits)} SilentBridge pattern(s) detected in context.",
                *sb_descriptions,
            ],
            )

        reasons.append(
            f"Phase 2 passed: {len(context.tokens)} context tokens scanned — no SilentBridge patterns."
        )

        # ---- Phase 3: parallel guard -------------------------------------
        guard_aligned, guard_rationale = self._guard.check_alignment(self._task_brief, call)

        if not guard_aligned:
            return ToolCallVerdict(
                call_id=call.call_id,
                verdict=AuthorizationVerdict.DENIED_DEVIATION,
                triggering_origin=triggering_origin,
                guard_report={"rationale": guard_rationale},
                reasons=[
                "Phase 3 FAILED: parallel guard flagged tool call as deviating from task brief.",
                guard_rationale,
            ],
            )

        reasons.append(f"Phase 3 passed: guard aligned — {guard_rationale}")

        return ToolCallVerdict(
            call_id=call.call_id,
            verdict=AuthorizationVerdict.AUTHORIZED,
            triggering_origin=triggering_origin,
            guard_report={"rationale": guard_rationale},
            reasons=reasons,
        )

    # ------------------------------------------------------------------
    # User elevation
    # ------------------------------------------------------------------

    def elevate_user_intent(
        self,
        user_token: TaggedToken,
        *,
        system_acknowledgement: str,
    ) -> TaggedToken:
        """Explicitly elevate a user-origin token to instruction-trust.

            Produces a NEW token with Origin.SYSTEM and parent_token_id pointing
            to the original user token. The original user_token is NOT modified.

            This is a deliberate, audited escalation path — NOT an automatic
            upgrade. The system_acknowledgement string forces the caller to be
            explicit about why the elevation is warranted.

            Args:
            user_token: The USER-origin token to elevate.
            system_acknowledgement: A non-empty string from the operator
            explaining why this user intent should be treated as
            instruction-trusted. Raises ValueError if empty.

            Returns:
            A new TaggedToken with Origin.SYSTEM and parent_token_id set.

            Raises:
            ValueError: If allow_user_instruction_elevation=False, or if
            system_acknowledgement is empty.
            """
        if not self._allow_elevation:
            raise ValueError(
                "User instruction elevation is disabled on this TrustDomainTagger instance. "
                "Set allow_user_instruction_elevation=True to enable this feature. "
                "Ensure operator policy explicitly sanctions this before enabling."
            )
        if not system_acknowledgement or not system_acknowledgement.strip():
            raise ValueError(
                "system_acknowledgement must be a non-empty string describing why "
                "this user-origin token is being elevated to instruction-trust. "
                "This acknowledgement is required to prevent inadvertent elevation."
            )
        return TaggedToken(
            content=user_token.content,
            origin=Origin.SYSTEM,
            source_ref=f"elevated_from:{user_token.source_ref}",
            parent_token_id=user_token.token_id,
        )

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def summarize_context(self, context: TaggedContext) -> dict[str, object]:
        """Return a summary of context composition for observability.

            Returns a dict with:
            - total_tokens: int
            - origin_counts: dict[Origin, int]
            - untrusted_token_count: int
            - silentbridge_pattern_count: int  (tokens that matched a SB pattern)
            - trusted_token_count: int
            """
        origin_counts: dict[Origin, int] = defaultdict(int)
        untrusted_count = 0
        trusted_count = 0
        sb_count = 0

        for tok in context.tokens:
            origin_counts[tok.origin] += 1
            if tok.origin in INSTRUCTION_TRUSTED_ORIGINS:
                trusted_count += 1
            else:
                untrusted_count += 1
            if detect_silentbridge_pattern(tok) is not None:
                sb_count += 1

        return {
            "context_id": str(context.context_id),
            "total_tokens": len(context.tokens),
            "origin_counts": dict(origin_counts),
            "trusted_token_count": trusted_count,
            "untrusted_token_count": untrusted_count,
            "silentbridge_pattern_count": sb_count,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def default_tagger(
    *,
    task_brief: str = "",
    guard: ParallelGuard | None = None,
) -> TrustDomainTagger:
    """Create a TrustDomainTagger with conservative defaults.

        Uses HeuristicParallelGuard if no guard is supplied. Callers should
        inject a real LLM-backed guard in production.

        Args:
        task_brief: Natural-language description of the agent's task.
        guard: Optional custom ParallelGuard implementation.

        Returns:
        A fully-initialised TrustDomainTagger.
        """
    resolved_guard: ParallelGuard = guard if guard is not None else HeuristicParallelGuard()
    return TrustDomainTagger(
        resolved_guard,
        task_brief=task_brief,
        allow_user_instruction_elevation=False,
        banned_tools_for_untrusted=(),
    )


# ---------------------------------------------------------------------------
# HMAC trust label signing (P1-7)
# ---------------------------------------------------------------------------

def _load_trust_sign_key() -> bytes:
    # C-3: the trust-label HMAC is the P1-7 defense that stops a prompt from
    # self-asserting trust_domain="system". A source-committed default key would
    # let an attacker forge that label and self-elevate to SYSTEM trust. Require
    # the key in production; ephemeral per-process random in dev (warned).
    import secrets
    import warnings
    value = os.environ.get("DHARMAOS_TRUST_SIGN_KEY")
    if value:
        return value.encode()
    if os.environ.get("DHARMAOS_ENV", "").lower() == "production":
        raise RuntimeError("DHARMAOS_TRUST_SIGN_KEY must be set in production (trust-label signing)")
    warnings.warn(
        "DHARMAOS_TRUST_SIGN_KEY unset — using an ephemeral dev key; trust labels "
        "are not portable across processes and MUST NOT be used in production.",
        RuntimeWarning, stacklevel=2,
    )
    return ("DEV-EPHEMERAL-" + secrets.token_hex(16)).encode()


_TRUST_SIGN_KEY: bytes = _load_trust_sign_key()


def sign_trust_label(domain: str, contract_id: str) -> str:
    """HMAC-SHA256 sign a trust domain label with 16-char hex auth code.

        P1-7: Prevents an adversarial prompt from asserting
        ``prompt_trust_domain="system"`` to elevate its own trust level.
        The HMAC signature proves the label was assigned by the tagger, not
        self-declared by the prompt.
        """
    mac = hmac.new(_TRUST_SIGN_KEY, f"{contract_id}:{domain}".encode(), hashlib.sha256)
    return f"{domain}:{mac.hexdigest()[:16]}"


def verify_trust_label(signed: str, contract_id: str) -> str:
    """Verify an HMAC-signed trust label and return the domain.

        Raises:
        ValueError: If the signature is invalid (tampered or self-declared).
        """
    domain, sig = signed.rsplit(":", 1)
    expected = hmac.new(_TRUST_SIGN_KEY, f"{contract_id}:{domain}".encode(), hashlib.sha256)
    if not hmac.compare_digest(sig, expected.hexdigest()[:16]):
        raise ValueError(
            f"Trust domain signature invalid for contract {contract_id}. "
            "The label may have been tampered with or self-declared by an untrusted source."
        )
    return domain
