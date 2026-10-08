"""
Tests for vedic.signal_filter — Sprint V12 (Recursive Neti-Neti).

Coverage:
1.  Trivial greeting "hi" → NOISE
2.  Trivial greeting "hello world" (short) → NOISE
3.  Threat "report a phishing attempt" → THREAT
4.  Threat "warning: malware detected" → THREAT
5.  Imperative "go to amazon.com and find the best air fryer under $100" → ACTIONABLE
6.  Imperative "search for flights to Paris" → ACTIONABLE
7.  Question "what is the weather today?" → INFORMATIONAL
8.  Question "who wrote Hamlet?" → INFORMATIONAL
9.  Empty input → NOISE (special-case)
10.  Whitespace-only input → NOISE (special-case)
11.  Unclear/all-negated: a signal where no positive classifier fires → AMBIGUOUS
(fidelity test: this would be NOISE under the old cascade; must be AMBIGUOUS here)
12.  Multi-residue tiebreak invocation: signal that leaves 2 candidates → tiebreak path
13.  Persona bias: same input, "shopper" persona resolves ACTIONABLE via bias
14.  Persona bias: "student" persona resolves via explain/define bias
15.  Entity extraction — URL
16.  Entity extraction — email
17.  Entity extraction — price
18.  Entity extraction — date
19.  is_worth_running() → True for ACTIONABLE
20.  is_worth_running() → False for NOISE
21.  Singleton: get_signal_filter() returns same instance twice
22.  to_dict() — output schema well-formed
23.  FIDELITY test: apophatic algorithm, not positive cascade —
a signal with NO recognisable patterns returns AMBIGUOUS, not NOISE

Total: 23 tests.
"""

from __future__ import annotations

import pytest

from dharmaos.signal_filter import (
FilterLevel,
NetiNetiFilter,
SignalAnalysis,
SignalCategory,
get_signal_filter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    """Reset singleton before each test to prevent cross-test pollution."""
    NetiNetiFilter._instance = None
    yield  # type: ignore[misc]
    NetiNetiFilter._instance = None


@pytest.fixture()
def nf() -> NetiNetiFilter:
    """Return a fresh NetiNetiFilter instance."""
    return NetiNetiFilter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def classify(nf: NetiNetiFilter, task: str, **kwargs: object) -> SignalAnalysis:
    """Convenience wrapper for nf.analyze()."""
    return nf.analyze(task, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1–2: NOISE — trivial greetings
# ---------------------------------------------------------------------------


class TestNoiseCases:
    def test_trivial_hi_is_noise(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "hi")
        assert result.category == SignalCategory.NOISE, (
            f"Expected NOISE for 'hi', got {result.category}: {result.reasoning}"
        )
        assert result.confidence >= 0.85

    def test_trivial_hello_is_noise(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "hello")
        assert result.category == SignalCategory.NOISE
        assert result.filter_level == FilterLevel.SURFACE

    def test_trivial_thanks_is_noise(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "thanks")
        assert result.category == SignalCategory.NOISE


# ---------------------------------------------------------------------------
# 3–4: THREAT
# ---------------------------------------------------------------------------


class TestThreatCases:
    def test_phishing_attempt_is_threat(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "report a phishing attempt on my account")
        assert result.category == SignalCategory.THREAT, (
            f"Expected THREAT, got {result.category}: {result.reasoning}"
        )
        assert result.confidence >= 0.85

    def test_malware_warning_is_threat(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "warning: malware detected on the download link")
        assert result.category == SignalCategory.THREAT


# ---------------------------------------------------------------------------
# 5–6: ACTIONABLE — imperative commands
# ---------------------------------------------------------------------------


class TestActionableCases:
    def test_go_to_amazon_is_actionable(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "go to amazon.com and find the best air fryer under $100")
        assert result.category == SignalCategory.ACTIONABLE, (
            f"Expected ACTIONABLE, got {result.category}: {result.reasoning}"
        )
        assert result.is_worth_running() is True

    def test_search_flights_is_actionable(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "search for flights to Paris next Monday")
        assert result.category == SignalCategory.ACTIONABLE
        assert result.confidence >= 0.75

    def test_imperative_book_is_actionable(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "book me a table at an Italian restaurant for tomorrow evening")
        assert result.category == SignalCategory.ACTIONABLE


# ---------------------------------------------------------------------------
# 7–8: INFORMATIONAL — questions
# ---------------------------------------------------------------------------


class TestInformationalCases:
    def test_weather_question_is_informational(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "what is the weather today?")
        assert result.category == SignalCategory.INFORMATIONAL, (
            f"Expected INFORMATIONAL, got {result.category}: {result.reasoning}"
        )
        assert result.is_worth_running() is True

    def test_who_wrote_hamlet_is_informational(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "who wrote Hamlet?")
        assert result.category == SignalCategory.INFORMATIONAL

    def test_explain_construct_is_informational(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "explain the difference between supervised and unsupervised learning")
        assert result.category == SignalCategory.INFORMATIONAL


# ---------------------------------------------------------------------------
# 9–10: Empty / whitespace → NOISE (special-case, not full neti-neti path)
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_string_is_noise(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "")
        assert result.category == SignalCategory.NOISE
        assert result.confidence == 1.0

    def test_whitespace_only_is_noise(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "  \t  ")
        assert result.category == SignalCategory.NOISE
        assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# 11: All-negated silence path — AMBIGUOUS
# ---------------------------------------------------------------------------


class TestAllNegatedSilence:
    def test_all_negated_signal_is_ambiguous(self, nf: NetiNetiFilter) -> None:
        """A signal that genuinely admits no concrete category → AMBIGUOUS.

        Constructed so that:
        - Not trivial/greeting → noise CAN be eliminated
        - No threat keywords → THREAT eliminated
        - No imperative/action verb/command/persona verb → ACTIONABLE eliminated
        - No question words / '?' / explain constructs → INFORMATIONAL eliminated
        The result: all four candidates eliminated → all-negated silence → AMBIGUOUS.
        """
        # "zorblax mibble quux" — 3 made-up words, no patterns
        # However 3 words is still short; let's use 4+ to ensure noise elimination
        signal = "zorblax mibble quux frangle"
        result = nf.classify_via_neti(signal, "professional", [], [])
        assert result.category == SignalCategory.AMBIGUOUS, (
            f"Expected AMBIGUOUS (all-negated silence), got {result.category}: {result.reasoning}"
        )
        assert "ALL-NEGATED SILENCE" in result.reasoning or "AMBIGUOUS" in result.category.value


# ---------------------------------------------------------------------------
# 12: Multi-residue tiebreak invocation
# ---------------------------------------------------------------------------


class TestMultiResidueTiebreak:
    def test_multi_residue_invokes_tiebreak(self, nf: NetiNetiFilter) -> None:
        """Signal where 2+ candidates survive negation forces tiebreak path.

        A polite question with an action verb: "can you find the best laptop?"
        - NOT noise: has 6 words, action verb, question word → noise eliminated
        - NOT threat: no threat keywords → threat eliminated
        - ACTIONABLE: 'find' action verb → NOT eliminated
        - INFORMATIONAL: 'can you', '?' → NOT eliminated
        Both survive → tiebreak path (CONTEXTUAL level) → resolved.
        """
        result = classify(nf, "can you find the best laptop?")
        # Must have been resolved (not crash); result is well-defined
        assert result.category in (
            SignalCategory.ACTIONABLE,
            SignalCategory.INFORMATIONAL,
            SignalCategory.AMBIGUOUS,
        )
        assert result.filter_level == FilterLevel.CONTEXTUAL
        # Trace must mention tiebreak or multi-residue
        assert any(
            keyword in result.reasoning
                for keyword in ("TIEBREAK", "MULTI-RESIDUE", "tiebreak", "multi-residue")
        )

    def test_multi_residue_produces_contextual_filter_level(self, nf: NetiNetiFilter) -> None:
        """When tiebreak fires, filter_level must be CONTEXTUAL."""
        result = classify(nf, "could you research the market for me?")
        # This has 'research' (action verb) AND 'could you' (polite command) + question word
        # Multi-residue → CONTEXTUAL
        assert result.filter_level == FilterLevel.CONTEXTUAL


# ---------------------------------------------------------------------------
# 13–14: Persona bias — same input, different persona → different resolution
# ---------------------------------------------------------------------------


class TestPersonaBias:
    def test_shopper_persona_resolves_to_actionable(self, nf: NetiNetiFilter) -> None:
        """'track my order status' — 'track' is in shopper bias list."""
        result = classify(nf, "track my order status please", persona="shopper")
        assert result.category == SignalCategory.ACTIONABLE, (
            f"Expected ACTIONABLE via shopper persona bias, got {result.category}: {result.reasoning}"
        )

    def test_developer_persona_resolves_to_actionable(self, nf: NetiNetiFilter) -> None:
        """'monitor the endpoint' — 'monitor' is in developer bias list."""
        result = classify(nf, "monitor the API endpoint for downtime", persona="developer")
        assert result.category == SignalCategory.ACTIONABLE

    def test_student_persona_explain_resolves_informational(self, nf: NetiNetiFilter) -> None:
        """'explain recursion please' → informational (explain = informational indicator)."""
        result = classify(nf, "explain recursion to me please", persona="student")
        # 'explain' is both a question indicator and in student bias verbs;
        # the neti-neti test: explain disallows NOT-INFORMATIONAL
        assert result.category == SignalCategory.INFORMATIONAL


# ---------------------------------------------------------------------------
# 15–18: Entity extraction
# ---------------------------------------------------------------------------


class TestEntityExtraction:
    def test_url_entity_detected(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "go to https://example.com and fill out the form")
        assert any(e.startswith("url:") for e in result.entities_detected), (
            f"Expected url entity, got: {result.entities_detected}"
        )

    def test_email_entity_detected(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "send an email to john.doe@example.com about the meeting")
        assert any(e.startswith("email:") for e in result.entities_detected), (
            f"Expected email entity, got: {result.entities_detected}"
        )

    def test_price_entity_detected(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "find a laptop under $800 on Amazon")
        assert any(e.startswith("price:") for e in result.entities_detected), (
            f"Expected price entity, got: {result.entities_detected}"
        )

    def test_date_entity_detected(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "book me a flight for tomorrow to London")
        assert any(e.startswith("date:") for e in result.entities_detected), (
            f"Expected date entity, got: {result.entities_detected}"
        )


# ---------------------------------------------------------------------------
# 19–20: is_worth_running()
# ---------------------------------------------------------------------------


class TestIsWorthRunning:
    def test_actionable_is_worth_running(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "download the quarterly report from the website")
        assert result.is_worth_running() is True

    def test_noise_is_not_worth_running(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "hi")
        assert result.is_worth_running() is False

    def test_threat_is_not_worth_running(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "this is a phishing attempt, block the sender")
        # THREAT is not forwarded to orchestrator
        assert result.is_worth_running() is False

    def test_ambiguous_is_not_worth_running(self) -> None:
        analysis = SignalAnalysis(
        category=SignalCategory.AMBIGUOUS,
        confidence=0.5,
        filter_level=FilterLevel.CONTEXTUAL,
        reasoning="test",
        )
        assert analysis.is_worth_running() is False


# ---------------------------------------------------------------------------
# 21: Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_signal_filter_returns_same_instance(self) -> None:
        a = get_signal_filter()
        b = get_signal_filter()
        assert a is b

    def test_neti_neti_filter_get_singleton(self) -> None:
        a = NetiNetiFilter.get()
        b = NetiNetiFilter.get()
        assert a is b


# ---------------------------------------------------------------------------
# 22: to_dict()
# ---------------------------------------------------------------------------


class TestToDict:
    def test_to_dict_schema(self, nf: NetiNetiFilter) -> None:
        result = classify(nf, "go to github.com and star the repo")
        d = result.to_dict()
        required_keys = {
        "category",
        "confidence",
        "filter_level",
        "reasoning",
        "entities_detected",
        "analyzed_at",
        "worth_running",
        }
        assert required_keys.issubset(d.keys()), f"Missing keys: {required_keys - d.keys()}"
        assert isinstance(d["confidence"], float)
        assert isinstance(d["entities_detected"], list)
        assert isinstance(d["worth_running"], bool)


# ---------------------------------------------------------------------------
# 23: FIDELITY test — apophatic, not positive cascade
# ---------------------------------------------------------------------------


class TestApophaticFidelity:
    def test_no_positive_match_returns_ambiguous_not_noise(self, nf: NetiNetiFilter) -> None:
        """CRITICAL FIDELITY TEST.

        In the reference implementation cascade (positive classification), the 'else' branch
        at Level 1 returns AMBIGUOUS (with 0.40 confidence) only after checking
        trivial → NOISE first. A truly patternless multi-word signal would fall
        to AMBIGUOUS there too, so let's be precise about what we are testing.

        The cascade WOULD return NOISE for "hi" because it short-circuits on
        trivial check. Our apophatic method eliminates NOISE only when we can
        DEMONSTRATE the signal is NOT noise (substantive content). For a
        4+ word signal with no recognisable patterns, the cascade falls to
        AMBIGUOUS (0.40). Our neti-neti also falls to AMBIGUOUS but via the
        ALL-NEGATED SILENCE path — with a different reasoning trace proving
        the apophatic algorithm ran.

        This test verifies that the all-negated-silence path fires (not a
        positive cascade default) by checking the reasoning string contains
        the neti-neti trace marker.
        """
        # Signal with >=4 gibberish words: noise eliminated (substantive),
        # no threat keywords: threat eliminated,
        # no action verbs or imperative starters: actionable eliminated,
        # no question words or '?': informational eliminated.
        # → all four eliminated → ALL-NEGATED SILENCE → AMBIGUOUS.
        signal = "xyloquartz brimfangle thovix peluntra"
        result = nf.classify_via_neti(signal, "professional", [], [])

        assert result.category == SignalCategory.AMBIGUOUS, (
            f"Expected AMBIGUOUS (apophatic all-negated silence), "
                f"got {result.category}: {result.reasoning}"
        )
        # Verify the neti-neti algorithm ran (trace present), not a positive cascade default
        assert "NOT NOISE" in result.reasoning or "ALL-NEGATED" in result.reasoning, (
            f"Reasoning does not show neti-neti trace: {result.reasoning}"
        )
        # The result must NOT say 'NOISE' as the category
        assert result.category != SignalCategory.NOISE, (
            "Cascade fallback error: returned NOISE instead of AMBIGUOUS "
        "for all-negated signal — apophatic algorithm not running."
        )

    def test_algorithm_is_elimination_not_assertion(self, nf: NetiNetiFilter) -> None:
        """The reasoning for any result must contain at least one 'NOT X' trace.

        This verifies that the classify_via_neti() path ran (and logged at
        least one negation), rather than the special-case empty-input shortcut.
        """
        result = classify(nf, "research the top Python testing frameworks")
        # Must contain neti-neti trace tokens
        assert any(
            token in result.reasoning
                for token in (
        "NOT NOISE",
        "NOT THREAT",
        "NOT ACTIONABLE",
        "NOT INFORMATIONAL",
        "Neti-neti",
        "neti-neti",
        "SURVIVOR",
        "TIEBREAK",
        "ALL-NEGATED",
        )
        ), f"No negation trace found in reasoning: {result.reasoning}"
