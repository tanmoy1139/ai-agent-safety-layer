"""
    DharmaOS NetiNetiFilter — apophatic signal classifier for incoming task signals.

    PURPOSE
    -------
    NetiNetiFilter classifies every incoming signal (user message, tool output,
    event) into one of five categories before routing it to the appropriate
    handler: NOISE, THREAT, ACTIONABLE, INFORMATIONAL, or AMBIGUOUS.

    Unlike a standard cascade classifier (stops at first positive match), the
    NetiNetiFilter uses apophatic elimination: it tests what each category is
    NOT, discarding candidates that fail negation tests, and returns the
    survivors. This produces stricter discrimination when two categories have
    overlapping positive evidence.

    CATEGORIES
    ----------
    NOISE  : Signal has no actionable or informational content. Discard.
    THREAT  : Signal contains adversarial content (prompt injection, abuse,
    social engineering). Route to AdharmaDetector.
    ACTIONABLE  : Signal is a valid task request requiring agent action.
    INFORMATIONAL : Signal provides context or answers a question; no action needed.
    AMBIGUOUS  : Cannot be classified with confidence; escalate to human review.

    FILTER LEVELS
    -------------
    SURFACE  : Negation terminated after surface heuristics (trivial / threat).
    SEMANTIC  : Negation required semantic structural analysis.
    CONTEXTUAL : Negation required persona/prior-task context tiebreaking.

    COMPLIANCE ROLE
    ---------------
    - Threat detection provides OWASP ASI01 goal-hijack early warning.
    - AMBIGUOUS routing implements EU AI Act Art. 14 (human oversight) for
    signals that cannot be safely classified.

    Governance origin: Neti-neti (not this, not this) — Bṛhadāraṇyaka Upaniṣad
    II.3.6, IV.4.22 — apophatic method of arriving at classification by eliminating
    all that cannot be the true answer, leaving only what survives negation.

    the agent platform Vedic Signal Filter — Sprint V12.

    Classifies incoming task signals using the APOPHATIC RECURSIVE NEGATION
    (neti-neti) technique drawn from Bṛhadāraṇyaka Upaniṣad II.3.6 and IV.4.22.

    **Philosophical grounding:**
    Source authority: Patrick Olivelle, *The Early Upaniṣads: Annotated Text
    and Translation*, Oxford University Press, 1998.

    II.3.6:  "neti nety ātmā ... tato anyad ārtam" — "Not this, not this,
    the self ... the rest is subject to suffering."
    IV.4.22: The recursive negation as a method of arriving at Brahman by
    eliminating everything that is 'not Brahman'.

    The method: begin with the full set of candidates; eliminate each that
    CANNOT be the true classification; whatever survives is the answer.
    If nothing survives, silence — AMBIGUOUS. If multiple survive, apply
    persona-bias and prior-task context as a tiebreaker.

    **Contrast with V1 reference implementation cascade:**
    The original SignalFilter (cascade): decides by asserting — "is this X?
    Yes → return X." It stops at first positive match. This produces a
    different answer to an apophatic pass when two categories both have
    positive evidence (e.g. a question containing an imperative).

    The V12 NetiNetiFilter: decides by negating — "is this NOT X? Yes →
    discard X." All four concrete categories are tested; only survivors
    proceed to tiebreak. This is philosophically faithful to neti-neti and
    produces stricter discrimination.

    **Decision structure:**
    1. Start: candidates = {NOISE, THREAT, ACTIONABLE, INFORMATIONAL, AMBIGUOUS}
    (AMBIGUOUS is not eliminated by a `_is_not_X` test — it is the
    fallback that remains if any other candidate also remains, or if ALL
    are eliminated by strong negation.)
    2. For each concrete category C in {NOISE, THREAT, ACTIONABLE, INFORMATIONAL}:
    not_C, reason = _is_not_C(signal)
    if not_C: candidates.discard(C)
    3. Remove AMBIGUOUS from candidates once we decide whether to use it:
    - if len(candidates) == 1 (exactly one concrete category survives) →
    return that category at high confidence.
    - if len(candidates) == 0 (all concrete categories were negated, only
    silence remains) → return AMBIGUOUS (all-negated silence path).
    - if len(candidates) > 1 (multi-candidate residue) → tiebreak via
    persona bias + prior-task pattern; if tiebreak still inconclusive →
    return AMBIGUOUS (multi-residue path).

    **FilterLevel semantics (V12 redefinition):**
    SURFACE  — negation terminated after surface heuristics (trivial/threat).
    SEMANTIC  — negation required semantic structural analysis.
    CONTEXTUAL — negation required persona/prior-task tiebreak.

    Ported lineage:
    /opt/reference implementation/backend/app/services/the agent platform/signal_filter.py
    (original cascade, 564 LOC, 2026-04-15).

    Sprint V12 changes:
    - New class: NetiNetiFilter (replaces SignalFilter).
    - Decision structure: apophatic candidate-set elimination.
    - Each `_is_not_X()` returns (bool, str) — True means "this signal is
    NOT category X; discard X."
    - Confidence is derived from negation strength, not cascade stage.
    - All pattern banks, entity extraction, and persona-bias dict preserved.
    - Public API preserved: analyze(), is_trivial(), detect_entities(),
    is_worth_running() on SignalAnalysis, singleton get_signal_filter().
    """

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import StrEnum

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SignalCategory(StrEnum):
    ACTIONABLE = "actionable"  # Real task, needs execution
    INFORMATIONAL = "informational"  # Just needs an answer, no action
    NOISE = "noise"  # Meaningless, skip
    THREAT = "threat"  # Security concern, escalate
    AMBIGUOUS = "ambiguous"  # Unclear, ask for clarification


class FilterLevel(StrEnum):
    SURFACE = "surface"  # Negation terminated at surface heuristics
    SEMANTIC = "semantic"  # Negation required semantic structure analysis
    CONTEXTUAL = "contextual"  # Negation required persona/prior-task tiebreak


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class SignalAnalysis:
    """Result of analyzing one task signal."""

    category: SignalCategory
    confidence: float  # 0.0–1.0
    filter_level: FilterLevel  # Depth at which negation concluded
    reasoning: str  # Human-readable explanation
    entities_detected: list[str] = field(default_factory=list)
    analyzed_at: float = field(default_factory=time.time)

    def is_worth_running(self) -> bool:
        """True if the signal should be forwarded to the orchestrator."""
        return self.category in (SignalCategory.ACTIONABLE, SignalCategory.INFORMATIONAL)

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "confidence": round(self.confidence, 3),
            "filter_level": self.filter_level.value,
            "reasoning": self.reasoning,
            "entities_detected": self.entities_detected,
            "analyzed_at": self.analyzed_at,
            "worth_running": self.is_worth_running(),
        }


# ---------------------------------------------------------------------------
# Pattern banks (preserved from reference implementation V1)
# ---------------------------------------------------------------------------

_TRIVIAL_EXACT: frozenset[str] = frozenset(
    {
    "hi",
    "hello",
    "hey",
    "yo",
    "thanks",
    "thank you",
    "thx",
    "ok",
    "okay",
    "sure",
    "cool",
    "bye",
    "goodbye",
    "good morning",
    "good evening",
    "what time",
    "test",
    "testing",
    "ping",
    "pong",
    "lol",
    "haha",
    "nice",
    "great",
    "awesome",
    "got it",
    "noted",
}
)

_THREAT_KEYWORDS: list[str] = [
    r"\bphish(ing)?\b",
    r"\bmalware\b",
    r"\bvirus\b",
    r"\btrojan\b",
    r"\bransomware\b",
    r"\bhack(ed|ing)?\b",
    r"\bbreach\b",
    r"\bscam\b",
    r"\bfraud\b",
    r"\bsuspicious link\b",
    r"\bcredential.{0,10}steal",
    r"\bsocial engineer",
    r"\bpassword.{0,10}steal",
    r"\bkeylogger\b",
]

_ACTION_VERBS: list[str] = [
    r"\bgo to\b",
    r"\bvisit\b",
    r"\bopen\b",
    r"\bnavigate to\b",
    r"\bfill\b",
    r"\bclick\b",
    r"\bbuy\b",
    r"\bpurchase\b",
    r"\border\b",
    r"\bsend\b",
    r"\bemail\b",
    r"\bschedule\b",
    r"\bmonitor\b",
    r"\bcompare\b",
    r"\bresearch\b",
    r"\banalyze\b",
    r"\bfind\b",
    r"\bsearch for\b",
    r"\bdownload\b",
    r"\bupload\b",
    r"\bsubmit\b",
    r"\bbook\b",
    r"\breserve\b",
    r"\bcancel\b",
    r"\bdelete\b",
    r"\bextract\b",
    r"\bscrape\b",
    r"\btrack\b",
    r"\bwatch\b",
    r"\bcheck\b",
    r"\bwait for\b",
    r"\bnotify me\b",
    r"\bremind\b",
    r"\bfill out\b",
    r"\bsign up\b",
    r"\bregister\b",
    r"\blog in\b",
    r"\bpost\b",
    r"\bshare\b",
    r"\btweet\b",
    r"\breply\b",
]

_QUESTION_INDICATORS: list[str] = [
    r"\bwhat\b",
    r"\bwho\b",
    r"\bwhere\b",
    r"\bwhen\b",
    r"\bwhy\b",
    r"\bhow\b",
    r"\bwhich\b",
    r"\bcan you\b",
    r"\bcould you\b",
    r"\bdo you know\b",
    r"\btell me\b",
    r"\bexplain\b",
    r"\bdefine\b",
    r"\bdifference between\b",
    r"\bwhat is\b",
    r"\bwhat are\b",
    r"\bis it\b",
    r"\bare there\b",
]

# Entity extraction patterns
_URL_PATTERN: re.Pattern[str] = re.compile(r"https?://[^\s]+|www\.[^\s]+", re.IGNORECASE)
_EMAIL_PATTERN: re.Pattern[str] = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PRICE_PATTERN: re.Pattern[str] = re.compile(
    r"\$[\d,]+(?:\.\d{2})?|\b\d+(?:\.\d{2})?\s*(?:dollars?|usd)\b", re.IGNORECASE
)
_DATE_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?"
    r"|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
    r"|\btoday\b|\btomorrow\b|\bnext\s+\w+",
    re.IGNORECASE,
)

# Imperative verb starters (used in actionable negation check)
_IMPERATIVE_STARTERS: frozenset[str] = frozenset(
    {
    "go",
    "find",
    "open",
    "close",
    "buy",
    "get",
    "send",
    "make",
    "set",
    "check",
    "look",
    "run",
    "start",
    "stop",
    "create",
    "delete",
    "add",
    "remove",
    "read",
    "write",
    "download",
    "upload",
    "book",
    "reserve",
    "pay",
    "click",
    "fill",
    "submit",
    "post",
    "reply",
    "forward",
    "move",
    "copy",
    "rename",
    "save",
    "export",
    "import",
    "share",
    "extract",
    "search",
    "visit",
    "navigate",
    "scroll",
    "wait",
    "monitor",
    "schedule",
    "cancel",
    "confirm",
    "apply",
    "compare",
    "analyze",
    "research",
    "summarize",
    "translate",
    "convert",
}
)


# ---------------------------------------------------------------------------
# Compiled regex collections (built once at import)
# ---------------------------------------------------------------------------


def _compile_patterns(patterns: list[str]) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE)


_THREAT_RE: re.Pattern[str] = _compile_patterns(_THREAT_KEYWORDS)
_ACTION_RE: re.Pattern[str] = _compile_patterns(_ACTION_VERBS)
_QUESTION_RE: re.Pattern[str] = _compile_patterns(_QUESTION_INDICATORS)


# ---------------------------------------------------------------------------
# NetiNetiFilter
# ---------------------------------------------------------------------------


class NetiNetiFilter:
    """Apophatic signal discriminator grounded in neti-neti (neti-neti).

        Source: Bṛhadāraṇyaka Upaniṣad II.3.6 ("not this, not this") and
        IV.4.22 (recursive negation as the path to the Absolute).

        **Decision structure — apophatic elimination:**
        1. Begin with candidates: {NOISE, THREAT, ACTIONABLE, INFORMATIONAL}.
        (AMBIGUOUS is not a candidate — it is the fallback residue.)
        2. For each candidate C, call _is_not_C(signal, ...).
        If the signal IS NOT C: discard C from candidates.
        3. Evaluate residue:
        - 1 survivor → return it (high confidence).
        - 0 survivors → AMBIGUOUS via all-negated silence path.
        - 2+ survivors → tiebreak(candidates, persona, prior_tasks);
        if inconclusive → AMBIGUOUS via multi-residue path.

        **Each _is_not_X() method:**
        Returns (not_X: bool, reasoning: str).
        not_X=True  → "I am confident this signal is NOT category X."
        not_X=False → "I cannot rule out that this signal IS category X."

        **Confidence computation:**
        Base confidence is set by how strongly each surviving elimination
        argument held. A single strong surviving candidate → high confidence.
        Multiple survivors or a tiebreak → moderate confidence.

        Usage::

        nf = get_signal_filter()
        result = nf.analyze("Go to amazon.com and find the best air fryer under $100")
        if result.is_worth_running():
        orchestrator.run(task)

        Singleton: use get_signal_filter() for the process-wide instance.
        """

    _instance: NetiNetiFilter | None = None

    def __init__(self) -> None:
        # Persona-specific action amplifiers (preserved from reference implementation V1).
        # Used in tiebreaker when multiple candidates survive negation.
        self._persona_action_bias: dict[str, list[str]] = {
            "professional": ["research", "analyze", "compare", "schedule", "email"],
            "shopper": ["buy", "compare", "find", "order", "track"],
            "developer": ["go to", "open", "extract", "download", "monitor"],
            "student": ["research", "find", "explain", "define"],
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        task: str,
        persona: str = "professional",
        prior_tasks: list[str] | None = None,
    ) -> SignalAnalysis:
        """Classify a task signal via neti-neti apophatic elimination.

            All four concrete categories are tested for negation. Whatever
            cannot be negated survives. The residue determines the result.

            Args:
            task: The raw task string from the user.
            persona: User profile hint ("professional", "shopper", etc.).
            prior_tasks: Recent prior task strings for context.

            Returns:
            SignalAnalysis with category, confidence, filter_level, and
            reasoning that traces the negation path taken.
            """
        log = logger.bind(task_preview=task[:60], persona=persona)

        if not task or not task.strip():
            return SignalAnalysis(
                category=SignalCategory.NOISE,
                confidence=1.0,
                filter_level=FilterLevel.SURFACE,
                reasoning="Empty input — nothing to process.",
            )

        task_stripped = task.strip()
        entities = self.detect_entities(task_stripped)
        pt = prior_tasks or []

        try:
            result = self.classify_via_neti(task_stripped, persona, pt, entities)
        except Exception as exc:
            log.exception("neti_neti_filter_error", error=str(exc))
            return SignalAnalysis(
                category=SignalCategory.AMBIGUOUS,
                confidence=0.5,
                filter_level=FilterLevel.SURFACE,
                reasoning=f"Filter evaluation failed ({exc}). Flagging as ambiguous.",
            )
        else:
            log.info(
                "neti_neti_result",
                category=result.category,
                confidence=result.confidence,
                filter_level=result.filter_level,
            )
            return result

    def classify_via_neti(
        self,
        signal: str,
        persona: str,
        prior_tasks: list[str],
        entities: list[str] | None = None,
    ) -> SignalAnalysis:
        """Core apophatic classification — the neti-neti algorithm.

            This is the philosophically faithful implementation of the method
            described in Bṛhadāraṇyaka Upaniṣad II.3.6 / IV.4.22: start with
            all candidates; eliminate those that CANNOT be the answer; return
            what remains.

            Args:
            signal: Pre-stripped task string.
            persona: User profile hint.
            prior_tasks: List of recent prior task strings.
            entities: Pre-extracted entity list (avoids re-running regex).

            Returns:
            SignalAnalysis with full negation trace in reasoning.
            """
        _entities = entities if entities is not None else self.detect_entities(signal)

        # --- Step 1: candidate set (all concrete categories) ----------------
        # AMBIGUOUS is not a candidate — it is the residue.
        candidates: set[SignalCategory] = {
            SignalCategory.NOISE,
            SignalCategory.THREAT,
            SignalCategory.ACTIONABLE,
            SignalCategory.INFORMATIONAL,
        }

        negation_trace: list[str] = []

        # --- Step 2a: SURFACE trivial short-circuit -------------------------
        # For trivial/greeting inputs the answer is known immediately at SURFACE
        # depth without running semantic negations. This matches the spirit of
        # neti-neti: trivial signals are the most direct case of "not anything
        # substantive" — we eliminate THREAT, ACTIONABLE, INFORMATIONAL by
        # immediate surface inspection, NOISE survives.
        if self.is_trivial(signal):
            return SignalAnalysis(
                category=SignalCategory.NOISE,
                confidence=0.92,
                filter_level=FilterLevel.SURFACE,
                reasoning=(
                "Neti-neti SURFACE: trivial/greeting signal — "
                "THREAT, ACTIONABLE, INFORMATIONAL eliminated immediately. NOISE survives."
            ),
                entities_detected=_entities,
            )

        # --- Step 2b: surface negation pass (non-trivial signals) -----------
        not_noise, noise_reason = self._is_not_noise(signal)
        if not_noise:
            candidates.discard(SignalCategory.NOISE)
            negation_trace.append(f"NOT NOISE: {noise_reason}")

        not_threat, threat_reason = self._is_not_threat(signal)
        if not_threat:
            candidates.discard(SignalCategory.THREAT)
            negation_trace.append(f"NOT THREAT: {threat_reason}")

        # --- Step 3: semantic negation pass ---------------------------------
        not_actionable, action_reason = self._is_not_actionable(signal, persona, prior_tasks)
        if not_actionable:
            candidates.discard(SignalCategory.ACTIONABLE)
            negation_trace.append(f"NOT ACTIONABLE: {action_reason}")

        not_informational, info_reason = self._is_not_informational(signal)
        if not_informational:
            candidates.discard(SignalCategory.INFORMATIONAL)
            negation_trace.append(f"NOT INFORMATIONAL: {info_reason}")

        # --- Step 4: evaluate residue ---------------------------------------
        trace_str = "; ".join(negation_trace) if negation_trace else "No negations applied."

        if len(candidates) == 0:
            # All concrete categories eliminated — the all-negated silence path.
            # Per neti-neti philosophy: when everything is negated, we rest in
            # the unmanifest (silence = AMBIGUOUS).
            return SignalAnalysis(
                category=SignalCategory.AMBIGUOUS,
                confidence=0.60,
                filter_level=FilterLevel.SEMANTIC,
                reasoning=(
                "Neti-neti ALL-NEGATED SILENCE: all four concrete categories "
                f"eliminated. {trace_str}"
            ),
                entities_detected=_entities,
            )

        if len(candidates) == 1:
            cat = next(iter(candidates))
            # Confidence determined by depth required: surface=0.90, semantic=0.82
            level = (
                FilterLevel.SURFACE
                if not (not_actionable or not_informational)
                else FilterLevel.SEMANTIC
            )
            confidence = 0.90 if level == FilterLevel.SURFACE else 0.85
            # Boost for entity presence on actionable/informational
            if _entities and cat in (SignalCategory.ACTIONABLE, SignalCategory.INFORMATIONAL):
                confidence = min(confidence + 0.05, 0.95)
            return SignalAnalysis(
                category=cat,
                confidence=confidence,
                filter_level=level,
                reasoning=f"Neti-neti SINGLE SURVIVOR: {cat.value}. {trace_str}",
                entities_detected=_entities,
            )

        # Multiple candidates survived — tiebreak needed (CONTEXTUAL level)
        return self._tiebreak(candidates, signal, persona, prior_tasks, _entities, trace_str)

    # ------------------------------------------------------------------
    # Negation oracles — each returns (not_X: bool, reasoning: str)
    # not_X = True  → "this signal IS NOT X; discard X"
    # not_X = False → "X cannot be ruled out; keep X in candidates"
    # ------------------------------------------------------------------

    def _is_not_noise(self, signal: str) -> tuple[bool, str]:
        """Negate NOISE: True when the signal is clearly NOT trivial/empty.

            NOISE is defined as: trivial greetings, one-letter inputs, meaningless
            filler. If the signal is substantive (multiple words, imperative
            structure, or URL/email), it is NOT noise.

            Source heuristics (same as reference implementation Level 1):
            - _TRIVIAL_EXACT lookup
            - len(stripped_lower) <= 3
            - For longer signals: if action verbs, question words, URLs, or
            substantive length (>= 4 words) are present → NOT noise.
            """
        normalized = signal.strip().lower().rstrip("!?.,:;")

        # Clearly trivial → noise IS plausible → return not_noise=False
        if normalized in _TRIVIAL_EXACT or len(normalized) <= 3:
            return False, "Signal matches trivial/greeting set — NOISE plausible."

        # Short but ambiguous (2-3 words) → noise plausible, can't definitively negate
        word_count = len(signal.split())
        if word_count <= 2:
            # Noise still plausible for very short inputs
            return False, "Signal is very short (≤2 words) — NOISE not eliminated."

        # Substantive signal: action verb, question word, entity, or >=4 words
        # Any of these eliminates NOISE.
        if _ACTION_RE.search(signal.lower()):
            return True, "Action verb present — signal is substantive, NOT noise."
        if _QUESTION_RE.search(signal.lower()):
            return True, "Question indicator present — signal is substantive, NOT noise."
        if _THREAT_RE.search(signal.lower()):
            return True, "Threat keyword present — signal is substantive, NOT noise."
        if word_count >= 4:
            return True, f"Signal has {word_count} words — substantive, NOT noise."

        # 3 words, no patterns: noise still plausible
        return False, "Short signal with no recognised patterns — NOISE not eliminated."

    def _is_not_threat(self, signal: str) -> tuple[bool, str]:
        """Negate THREAT: True when the signal contains NO threat keywords.

            THREAT is defined as: any signal containing phishing, malware, hack,
            breach, scam, fraud, credential-theft, or keylogger keywords.

            If none of these keywords are found, THREAT is definitively eliminated.
            """
        match = _THREAT_RE.search(signal.lower())
        if match:
            return False, f"Threat keyword '{match.group()}' found — THREAT plausible."
        return True, "No threat keywords found — THREAT eliminated."

    def _is_not_actionable(
        self, signal: str, persona: str, prior_tasks: list[str]
    ) -> tuple[bool, str]:
        """Negate ACTIONABLE: True when the signal carries no action intent.

            ACTIONABLE is defined as: imperative structure, action verbs in
            command context, polite command forms ("please ...", "can you ..."),
            or multi-step sequences.

            Key distinction: an action verb appearing as a NOUN MODIFIER inside
            a declarative/informational sentence (e.g. "the download link",
            "a search result") does NOT make the signal actionable. We require
            the verb to appear in a command context: sentence-initial imperative,
            explicit command prefix, or multi-step marker.

            Persona bias and prior-task pattern are also considered (preserved
            from reference implementation Level 3 logic). Note: persona bias verbs that are
            primarily informational constructs (e.g. 'explain', 'define') are
            intentionally excluded from the persona-bias check here; they are
            handled by _is_not_informational instead.
            """
        lower = signal.lower().strip()
        words = lower.split()

        # Imperative starter: first word is a bare action verb
        if words and words[0] in _IMPERATIVE_STARTERS:
            return (
                False,
                f"Imperative verb '{words[0]}' — ACTIONABLE cannot be eliminated.",
            )

        # Multi-step sequence marker
        if re.search(r"\b(and then|after that|followed by|then|next,|step \d)\b", lower):
            return False, "Multi-step sequence detected — ACTIONABLE cannot be eliminated."

        # Polite command form (but not purely informational)
        polite_match = re.search(
            r"^(please|i want(ed)? to|i'd like to|can you|could you|would you)\b", lower
        )
        if polite_match:
            # "can you tell me / explain / describe / define" → primarily informational
            info_follow = re.search(
                r"\b(tell me|explain|describe|define|what is|what are|difference between)\b",
                lower,
            )
            if info_follow:
                # Polite informational — ACTIONABLE plausibly eliminated (favor INFO)
                return True, (
                    f"Polite informational form '{polite_match.group()}' + '{info_follow.group()}' "
                    "— ACTIONABLE eliminated in favour of INFORMATIONAL."
                )
                return False, "Polite command form detected — ACTIONABLE cannot be fully eliminated."

        # Action verb in a genuine command context:
        # We check for action verb ONLY when the signal is structured as a
        # command (not a declarative description of an event).
        # Heuristic: a declarative threat description contains a subject + verb
        # ("malware was detected", "a phishing attempt occurred"). We detect
        # this via a preceding noun phrase (article + noun before verb).
        action_match = _ACTION_RE.search(lower)
        if action_match:
            verb = action_match.group()
            # Check for declarative context: article/preposition before the verb span
            # (e.g. "the download link" → 'download' preceded by 'the',
            #  "a phishing attempt on" → 'download' is not present here,
            #  "on the download link" → 'download' preceded by 'the').
            # If the word immediately before the verb is a determiner/preposition,
            # the verb is used as a noun modifier in a declarative phrase, not
            # as an imperative command.
            start = action_match.start()
            preceding = lower[:start].strip()
            # Match: preceding ends with determiner/preposition (verb is nominal)
            declarative_prefix = re.search(
                r"\b(the|a|an|this|that|my|your|their|our|its|his|her"
                r"|on|in|at|for|of|with|from|to|by|about|through)\s*$",
                preceding,
            )
            if declarative_prefix:
                # Verb follows a determiner/preposition → nominal/declarative context
                pass  # fall through — don't preserve ACTIONABLE on this match
            else:
                return (
                    False,
                    f"Action verb '{verb}' in command context — ACTIONABLE cannot be eliminated.",
                )

        # Persona bias: verbs that are TRULY actional (not purely informational)
        # Informational-overlap verbs ('explain', 'define') are excluded here.
        _informational_persona_verbs: frozenset[str] = frozenset(
            {"explain", "define", "difference between", "tell me"}
        )
        bias_verbs = self._persona_action_bias.get(persona.lower(), [])
        for verb in bias_verbs:
            if verb in lower and verb not in _informational_persona_verbs:
                return (
                    False,
                    f"Persona '{persona}' actionable verb '{verb}' found — ACTIONABLE cannot be eliminated.",
                )

        # Prior-task actionable pattern
        if len(prior_tasks) >= 2 and self._prior_task_pattern(prior_tasks):
            return (
                False,
                "Prior-task history shows actionable pattern — ACTIONABLE cannot be eliminated.",
            )

        # Exact repeat of prior task
        for pt in prior_tasks[-5:]:
            if pt.strip().lower() == lower:
                return (
                    False,
                    "Exact prior-task repeat — ACTIONABLE cannot be eliminated.",
                )

        return True, "No imperative, action verb, or command form found — ACTIONABLE eliminated."

    def _is_not_informational(self, signal: str) -> tuple[bool, str]:
        """Negate INFORMATIONAL: True when the signal has no question structure.

            INFORMATIONAL is defined as: question words (what/who/where/when/why/
            how/which/...), question-mark termination, or "tell me / explain /
            define / difference between" constructs.
            """
        lower = signal.lower().strip()

        # Ends with '?'
        if signal.rstrip().endswith("?"):
            return False, "Signal ends with '?' — INFORMATIONAL cannot be eliminated."

        # Question indicator word
        if _QUESTION_RE.search(lower):
            return False, "Question indicator word found — INFORMATIONAL cannot be eliminated."

        # "Tell me / explain / define / difference between" constructs
        if re.search(
            r"\b(tell me|explain|describe|define|difference between|what is|what are)\b", lower
        ):
            return (
                False,
                "Informational construct (explain/define/tell me) — INFORMATIONAL cannot be eliminated.",
            )

        return True, "No question structure found — INFORMATIONAL eliminated."

    # ------------------------------------------------------------------
    # Tiebreaker (CONTEXTUAL level)
    # ------------------------------------------------------------------

    def _tiebreak(
        self,
        candidates: set[SignalCategory],
        signal: str,
        persona: str,
        prior_tasks: list[str],
        entities: list[str],
        trace_str: str,
    ) -> SignalAnalysis:
        """Resolve multi-candidate residue using persona bias + prior-task pattern.

            Called only when 2+ concrete categories survived all negations.
            Returns AMBIGUOUS if tiebreaker cannot resolve the residue.

            Tiebreak order (deterministic):
            1. Persona-bias verbs: if signal contains a persona-preferred verb
            and ACTIONABLE is still in residue → ACTIONABLE.
            2. Prior-task actionable pattern → ACTIONABLE if ACTIONABLE in residue.
            3. Question-mark present → INFORMATIONAL if INFORMATIONAL in residue.
            4. Otherwise → AMBIGUOUS (multi-residue, ask for clarification).
            """
        lower = signal.lower().strip()

        # 1. Persona bias
        if SignalCategory.ACTIONABLE in candidates:
            bias_verbs = self._persona_action_bias.get(persona.lower(), [])
            for verb in bias_verbs:
                if verb in lower:
                    return SignalAnalysis(
                        category=SignalCategory.ACTIONABLE,
                        confidence=0.72,
                        filter_level=FilterLevel.CONTEXTUAL,
                        reasoning=(
                        f"Neti-neti TIEBREAK: persona '{persona}' bias verb '{verb}' "
                        f"resolves multi-residue {[c.value for c in candidates]} → ACTIONABLE. "
                        f"{trace_str}"
                    ),
                        entities_detected=entities,
                    )

                    # 2. Prior-task actionable pattern
                    if (
                        SignalCategory.ACTIONABLE in candidates
                        and len(prior_tasks) >= 2
                        and self._prior_task_pattern(prior_tasks)
                    ):
                        return SignalAnalysis(
                            category=SignalCategory.ACTIONABLE,
                            confidence=0.68,
                            filter_level=FilterLevel.CONTEXTUAL,
                            reasoning=(
                            "Neti-neti TIEBREAK: prior-task actionable pattern resolves "
                            f"multi-residue {[c.value for c in candidates]} → ACTIONABLE. "
                            f"{trace_str}"
                        ),
                            entities_detected=entities,
                        )

        # 3. Question-mark tie towards INFORMATIONAL
        if SignalCategory.INFORMATIONAL in candidates and signal.rstrip().endswith("?"):
            return SignalAnalysis(
                category=SignalCategory.INFORMATIONAL,
                confidence=0.70,
                filter_level=FilterLevel.CONTEXTUAL,
                reasoning=(
                "Neti-neti TIEBREAK: question-mark termination resolves "
                f"multi-residue {[c.value for c in candidates]} → INFORMATIONAL. "
                f"{trace_str}"
            ),
                entities_detected=entities,
            )

        # 4. Inconclusive tiebreak → AMBIGUOUS (multi-residue path)
        return SignalAnalysis(
            category=SignalCategory.AMBIGUOUS,
            confidence=0.55,
            filter_level=FilterLevel.CONTEXTUAL,
            reasoning=(
            "Neti-neti MULTI-RESIDUE: tiebreak inconclusive; "
            f"surviving candidates {[c.value for c in candidates]}. "
            "Request clarification from user. "
            f"{trace_str}"
        ),
            entities_detected=entities,
        )

    # ------------------------------------------------------------------
    # Entity extraction (preserved verbatim from reference implementation V1)
    # ------------------------------------------------------------------

    def is_trivial(self, task: str) -> bool:
        """Quick check: is this a greeting or trivial one-liner?"""
        normalized = task.strip().lower().rstrip("!?.,:;")
        return normalized in _TRIVIAL_EXACT or len(normalized) <= 3

    def detect_entities(self, task: str) -> list[str]:
        """Extract named entities: URLs, emails, prices, dates.

            Returns:
            Deduplicated list of detected entity strings, prefixed by type.
            """
        entities: list[str] = []
        for m in _URL_PATTERN.finditer(task):
            entities.append(f"url:{m.group()}")
        for m in _EMAIL_PATTERN.finditer(task):
            entities.append(f"email:{m.group()}")
        for m in _PRICE_PATTERN.finditer(task):
            entities.append(f"price:{m.group()}")
        for m in _DATE_PATTERN.finditer(task):
            entities.append(f"date:{m.group()}")
        return entities

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prior_task_pattern(self, prior_tasks: list[str]) -> bool:
        """Return True if recent prior tasks are predominantly actionable.

            Uses surface-level heuristics on the last 3 tasks only.
            """
        if len(prior_tasks) < 2:
            return False
        recent = prior_tasks[-3:]
        actionable_count = 0
        for pt in recent:
            lower = pt.strip().lower()
            words = lower.split()
            if (words and words[0] in _IMPERATIVE_STARTERS) or bool(_ACTION_RE.search(lower)):
                actionable_count += 1
        return actionable_count >= 2

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get(cls) -> NetiNetiFilter:
        """Return (or create) the process-wide NetiNetiFilter singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# ---------------------------------------------------------------------------
# Module-level singleton getter
# ---------------------------------------------------------------------------


def get_signal_filter() -> NetiNetiFilter:
    """Return the global NetiNetiFilter singleton."""
    return NetiNetiFilter.get()
