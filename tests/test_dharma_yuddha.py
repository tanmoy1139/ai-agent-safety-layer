"""Tests for Sprint V10 — Dharma-Yuddha Circuit.

TDD: all tests written first, then implementation makes them green.

Bhagavad Gītā II.31–II.38 (Arjuna's duty to defend dharma):
"Svadharmam api cāvekṣya na vikampitum arhasi"
— II.31: Even considering your own dharma you should not waver.
"Yadṛcchayā copapannaṃ svargadvāram apāvṛtam" — II.32.
"Athacet tvam imaṃ dharmyaṃ saṃgrāmaṃ na kariṣyasi" — II.33.

Mahābhārata Śānti Parva §59–60: rules of proportional just war.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dharmaos.adharma_detector import AdharmaVerdict
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
from dharmaos.ethics_engine import ActionContract, EthicsEngine

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _make_contract(
target: str = "https://example.com",
declared_goal: str = "browse a website",
business_reason: str = "research",
        kind: str = "browse",
reversible: bool = True,
affects_others: bool = False,
) -> ActionContract:
    return ActionContract(
    action_id=str(uuid4()),
    path_id="test-path",
    agent_id="agent-test",
    parent_agent_id=None,
    tenant_id="tenant-test",
    task_id="task-test",
    requested_at=_NOW,
            name="test action",
            kind=kind,
            target=target,
    reversible=reversible,
    affects_others=affects_others,
    declared_goal=declared_goal,
    business_reason=business_reason,
    )


def _deny_defense() -> DefenseProposal:
    return DefenseProposal(
    action_kind="block_url",
    defense_magnitude=0.5,
    rationale="Block the phishing URL",
    )


def _make_circuit(
*,
classifier: HeuristicTargetClassifier | None = None,
ethics_engine: EthicsEngine | None = None,
) -> DharmaYuddhaCircuit:
    clf = classifier or HeuristicTargetClassifier()
    return DharmaYuddhaCircuit(clf, ethics_engine=ethics_engine)


# ===========================================================================
# Section 1: HeuristicTargetClassifier (4 tests)
# ===========================================================================


class TestHeuristicTargetClassifier:
    """Tests 1–4: classifier pattern matching."""

    def test_phishing_marker_in_target_returns_adharmic_phishing(self) -> None:
        """Test 1: phishing markers in contract.target → ADHARMIC_PHISHING."""
        clf = HeuristicTargetClassifier()
        contract = _make_contract(target="https://paypal-login-secure.phishing.xyz/steal")
        result = clf.classify(contract)
        assert result.target_flag == TargetAdharmaFlag.ADHARMIC_PHISHING
        assert result.threat_magnitude > 0.0

    def test_cred_theft_marker_in_goal_returns_adharmic_cred_theft(self) -> None:
        """Test 2: cred-theft markers in declared_goal → ADHARMIC_CRED_THEFT."""
        clf = HeuristicTargetClassifier()
        contract = _make_contract(
        declared_goal="harvest credentials and exfiltrate passwords",
                target="https://bank.com/login",
        )
        result = clf.classify(contract)
        assert result.target_flag == TargetAdharmaFlag.ADHARMIC_CRED_THEFT
        assert result.threat_magnitude > 0.0

    def test_benign_contract_returns_clean(self) -> None:
        """Test 3: benign contract → CLEAN."""
        clf = HeuristicTargetClassifier()
        contract = _make_contract(
                target="https://docs.python.org",
        declared_goal="read Python documentation",
        business_reason="learning",
        )
        result = clf.classify(contract)
        assert result.target_flag == TargetAdharmaFlag.CLEAN
        assert result.threat_magnitude == 0.0

    def test_empty_target_and_rationale_returns_unknown(self) -> None:
        """Test 4: empty target + declared_goal + business_reason → UNKNOWN."""
        clf = HeuristicTargetClassifier()
        contract = _make_contract(target="", declared_goal="", business_reason="")
        result = clf.classify(contract)
        assert result.target_flag == TargetAdharmaFlag.UNKNOWN


# ===========================================================================
# Section 2: Circuit composition (8 tests)
# ===========================================================================


class TestCircuitComposition:
    """Tests 5–12: DharmaYuddhaCircuit decision logic."""

    def test_default_ethics_allows_returns_not_applicable(self) -> None:
        """Test 5: default ethics ALLOWS → NOT_APPLICABLE.

        The circuit's ethics_engine evaluates a benign contract and allows it;
        no dharma-yuddha exception is needed.
        """
        ethics = EthicsEngine()
        circuit = _make_circuit(ethics_engine=ethics)
        contract = _make_contract()
        verdict = circuit.evaluate(contract)
        assert verdict.outcome == DharmaYuddhaOutcome.NOT_APPLICABLE

    def test_default_denies_and_clean_target_returns_deny_nondefensive(self) -> None:
        """Test 6: default DENIES + target CLEAN → DENY_NONDEFENSIVE."""
        clf = HeuristicTargetClassifier()
        ethics = EthicsEngine()
        circuit = DharmaYuddhaCircuit(clf, ethics_engine=ethics)
        # Build a contract that will fail ahimsa (irreversible+delete) but
        # has a clean target.
        contract = _make_contract(
                target="https://docs.python.org",
        declared_goal="delete user account",
                kind="delete",
        reversible=False,
        )
        # Pass explicit denial reason so circuit knows ethics already denied it.
        verdict = circuit.evaluate(
            contract,
        default_ethics_denial_reason="ahimsa: irreversible destructive action",
        proposed_defense=_deny_defense(),
        )
        assert verdict.outcome == DharmaYuddhaOutcome.DENY_NONDEFENSIVE

    def test_default_denies_and_unknown_target_returns_deny_unknown(self) -> None:
        """Test 7: default DENIES + target UNKNOWN → DENY_UNKNOWN_TARGET (fail-closed)."""
        clf = HeuristicTargetClassifier()
        circuit = _make_circuit(classifier=clf)
        # empty target + declared_goal + business_reason → UNKNOWN classification
        contract = _make_contract(
                target="", declared_goal="", business_reason="", kind="delete", reversible=False
        )
        verdict = circuit.evaluate(
            contract,
        default_ethics_denial_reason="ahimsa violation",
        proposed_defense=_deny_defense(),
        )
        assert verdict.outcome == DharmaYuddhaOutcome.DENY_UNKNOWN_TARGET

    def test_deny_adharmic_proportional_returns_permit_defensive(self) -> None:
        """Test 8: default DENIES + adharmic target + proportional defense → PERMIT_DEFENSIVE."""
        clf = HeuristicTargetClassifier()
        circuit = _make_circuit(classifier=clf)
        contract = _make_contract(target="https://evil-phishing.xyz/login")
        defense = DefenseProposal(
        action_kind="block_url",
        defense_magnitude=0.5,  # threat will be > 0 for phishing; ratio ≤ 1.2
        rationale="Block known phishing URL",
        )
        verdict = circuit.evaluate(
            contract,
        default_ethics_denial_reason="ahimsa: harm to user credentials",
        proposed_defense=defense,
        )
        assert verdict.outcome == DharmaYuddhaOutcome.PERMIT_DEFENSIVE
        assert verdict.karma_delta_suggestion > 0.0

    def test_deny_adharmic_disproportionate_returns_deny_disproportionate(self) -> None:
        """Test 9: default DENIES + adharmic + defense > 1.2x → DENY_DISPROPORTIONATE."""
        clf = HeuristicTargetClassifier(
        phishing_markers=["phishing"],
        )
        DharmaYuddhaCircuit(clf, proportionality_ceiling=1.2)
        contract = _make_contract(target="https://phishing.xyz")
        # Force threat low so defense/threat > 1.2
        threat = ThreatAssessment(
        target_flag=TargetAdharmaFlag.ADHARMIC_PHISHING,
        threat_magnitude=0.2,
        evidence=["phishing detected"],
        classifier="test",
        )
        defense = DefenseProposal(
        action_kind="full_system_shutdown",
        defense_magnitude=0.9,  # 0.9 / 0.2 = 4.5 >> 1.2
        rationale="Overreact to minor phishing",
        )

        # Inject threat directly by using a stub classifier
        class StubClf:
            def classify(self, _: ActionContract) -> ThreatAssessment:
                return threat

        circuit2 = DharmaYuddhaCircuit(StubClf(), proportionality_ceiling=1.2)  # type: ignore[arg-type]
        verdict = circuit2.evaluate(
            contract,
        default_ethics_denial_reason="ahimsa",
        proposed_defense=defense,
        )
        assert verdict.outcome == DharmaYuddhaOutcome.DENY_DISPROPORTIONATE

    def test_no_proposed_defense_circuit_synthesizes_and_permits(self) -> None:
        """Test 10: no proposed_defense → circuit synthesizes min-viable defense → PERMIT_DEFENSIVE."""
        clf = HeuristicTargetClassifier()
        circuit = _make_circuit(classifier=clf)
        contract = _make_contract(target="https://malware-download.exe.phishing.ru/payload")
        verdict = circuit.evaluate(
            contract,
        default_ethics_denial_reason="ahimsa: malware delivery",
        proposed_defense=None,  # let circuit synthesize
        )
        assert verdict.outcome == DharmaYuddhaOutcome.PERMIT_DEFENSIVE
        assert verdict.defense is not None
        assert verdict.defense.action_kind in {
        "block_url",
        "refuse_continuation",
        "notify_user",
        "report_abuse",
        }

    def test_proportionality_exactly_at_ceiling_permits(self) -> None:
        """Test 11: proportionality ratio == 1.2 exactly → PERMIT (inclusive boundary)."""
        threat = ThreatAssessment(
        target_flag=TargetAdharmaFlag.ADHARMIC_PHISHING,
        threat_magnitude=0.5,
        evidence=["phishing"],
        classifier="stub",
        )
        defense = DefenseProposal(
        action_kind="block_url",
        defense_magnitude=0.6,  # 0.6 / 0.5 = 1.2 exactly
        rationale="Block at ceiling",
        )

        class StubClf:
            def classify(self, _: ActionContract) -> ThreatAssessment:
                return threat

        circuit = DharmaYuddhaCircuit(StubClf(), proportionality_ceiling=1.2)  # type: ignore[arg-type]
        contract = _make_contract()
        verdict = circuit.evaluate(
            contract,
        default_ethics_denial_reason="ahimsa",
        proposed_defense=defense,
        )
        assert verdict.outcome == DharmaYuddhaOutcome.PERMIT_DEFENSIVE

    def test_proportionality_just_above_ceiling_denies(self) -> None:
        """Test 12: proportionality 1.20001 → DENY_DISPROPORTIONATE (strict)."""
        threat = ThreatAssessment(
        target_flag=TargetAdharmaFlag.ADHARMIC_PHISHING,
        threat_magnitude=0.5,
        evidence=["phishing"],
        classifier="stub",
        )
        # 0.600005 / 0.5 = 1.20001
        defense = DefenseProposal(
        action_kind="block_url",
        defense_magnitude=0.600005,
        rationale="Barely over ceiling",
        )

        class StubClf:
            def classify(self, _: ActionContract) -> ThreatAssessment:
                return threat

        circuit = DharmaYuddhaCircuit(StubClf(), proportionality_ceiling=1.2)  # type: ignore[arg-type]
        contract = _make_contract()
        verdict = circuit.evaluate(
            contract,
        default_ethics_denial_reason="ahimsa",
        proposed_defense=defense,
        )
        assert verdict.outcome == DharmaYuddhaOutcome.DENY_DISPROPORTIONATE


# ===========================================================================
# Section 3: Karma-delta (3 tests)
# ===========================================================================


class TestKarmaDelta:
    """Tests 13–15: karma-delta for different outcomes."""

    def test_permit_low_ratio_earns_more_karma_than_high_ratio(self) -> None:
        """Test 13: PERMIT_DEFENSIVE ratio=0.5 → higher karma than ratio=1.2."""
        circuit = DharmaYuddhaCircuit(HeuristicTargetClassifier())
        karma_low = circuit.karma_delta_for_defense(DharmaYuddhaOutcome.PERMIT_DEFENSIVE, 0.5)
        karma_high = circuit.karma_delta_for_defense(DharmaYuddhaOutcome.PERMIT_DEFENSIVE, 1.2)
        assert karma_low > karma_high
        assert karma_low > 0.0
        assert karma_high > 0.0

    def test_deny_disproportionate_karma_is_zero_or_negative(self) -> None:
        """Test 14: DENY_DISPROPORTIONATE → karma_delta ≤ 0."""
        circuit = DharmaYuddhaCircuit(HeuristicTargetClassifier())
        karma = circuit.karma_delta_for_defense(DharmaYuddhaOutcome.DENY_DISPROPORTIONATE, 2.0)
        assert karma <= 0.0

    def test_not_applicable_karma_is_zero(self) -> None:
        """Test 15: NOT_APPLICABLE → karma_delta == 0.0."""
        circuit = DharmaYuddhaCircuit(HeuristicTargetClassifier())
        karma = circuit.karma_delta_for_defense(DharmaYuddhaOutcome.NOT_APPLICABLE, None)
        assert karma == 0.0


# ===========================================================================
# Section 4: V11 classifier adapter (3 tests)
# ===========================================================================


class TestV11TargetClassifier:
    """Tests 16–18: V11TargetClassifier maps AdharmaDetector verdicts to TargetAdharmaFlag."""

    def _make_adharma_report(self, verdict_val: str) -> object:
        """Create a minimal mock AdharmaReport."""
        report = MagicMock()
        report.verdict = AdharmaVerdict(verdict_val)
        return report

    def test_adharma_deny_maps_to_adharmic_other(self) -> None:
        """Test 16: AdharmaDetector returns DENY → ADHARMIC_OTHER."""
        detector = MagicMock()
        detector.evaluate.return_value = self._make_adharma_report("deny")
        clf = V11TargetClassifier(detector)
        contract = _make_contract()
        result = clf.classify(contract)
        assert result.target_flag == TargetAdharmaFlag.ADHARMIC_OTHER

    def test_adharma_permit_maps_to_clean(self) -> None:
        """Test 17: AdharmaDetector returns PERMIT → CLEAN."""
        detector = MagicMock()
        detector.evaluate.return_value = self._make_adharma_report("permit")
        clf = V11TargetClassifier(detector)
        contract = _make_contract()
        result = clf.classify(contract)
        assert result.target_flag == TargetAdharmaFlag.CLEAN

    def test_adharma_escalate_maps_to_unknown(self) -> None:
        """Test 18: AdharmaDetector returns ESCALATE_HITL → UNKNOWN (fail-closed)."""
        detector = MagicMock()
        detector.evaluate.return_value = self._make_adharma_report("escalate_hitl")
        clf = V11TargetClassifier(detector)
        contract = _make_contract()
        result = clf.classify(contract)
        assert result.target_flag == TargetAdharmaFlag.UNKNOWN


# ===========================================================================
# Bonus tests (19–20): integration + edge cases
# ===========================================================================


class TestBonusIntegration:
    """Tests 19–20: end-to-end + edge cases."""

    def test_end_to_end_ethics_engine_plus_circuit(self) -> None:
        """Test 19: EthicsEngine + DharmaYuddhaCircuit end-to-end.

        A contract that fails ahimsa (harm flag via irreversible+delete) but
        targets a phishing URL → PERMIT_DEFENSIVE.
        """
        ethics = EthicsEngine()
        clf = HeuristicTargetClassifier()
        circuit = DharmaYuddhaCircuit(clf, ethics_engine=ethics)

        # irreversible+delete → EthicsEngine denies on ahimsa
        # phishing in target → classifier flags ADHARMIC_PHISHING
        contract = _make_contract(
                target="https://phishing-steal-credentials.xyz/login",
        declared_goal="block phishing page",
                kind="delete",
        reversible=False,
        )
        verdict = circuit.evaluate(contract)
        assert verdict.outcome == DharmaYuddhaOutcome.PERMIT_DEFENSIVE
        assert verdict.karma_delta_suggestion > 0.0

    def test_proportionality_threat_zero_raises_or_returns_specific_outcome(self) -> None:
        """Test 20: proportionality with threat=0 → raises ZeroDivisionError.

        Design choice: we explicitly document that threat_magnitude=0 is
        an invalid input to proportionality() and raises ZeroDivisionError.
        Callers must validate threat_magnitude > 0 before computing ratio.
        """
        circuit = DharmaYuddhaCircuit(HeuristicTargetClassifier())
        with pytest.raises(ZeroDivisionError):
            circuit.proportionality(defense_magnitude=0.5, threat_magnitude=0.0)


# ===========================================================================
# Structural / invariant tests
# ===========================================================================


class TestStructuralInvariants:
    """Verify immutability and structural properties of DharmaYuddhaVerdict."""

    def test_verdict_is_frozen(self) -> None:
        """DharmaYuddhaVerdict is a frozen Pydantic model — mutations raise."""
        threat = ThreatAssessment(
        target_flag=TargetAdharmaFlag.CLEAN,
        threat_magnitude=0.0,
        classifier="test",
        )
        verdict = DharmaYuddhaVerdict(
        outcome=DharmaYuddhaOutcome.NOT_APPLICABLE,
                threat=threat,
        karma_delta_suggestion=0.0,
        )
        with pytest.raises(ValidationError):
            verdict.outcome = DharmaYuddhaOutcome.PERMIT_DEFENSIVE  # type: ignore[misc]

    def test_verdict_has_uuid(self) -> None:
        """Each verdict gets a unique ID."""
        threat = ThreatAssessment(
        target_flag=TargetAdharmaFlag.CLEAN,
        threat_magnitude=0.0,
        classifier="test",
        )
        v1 = DharmaYuddhaVerdict(
        outcome=DharmaYuddhaOutcome.NOT_APPLICABLE,
                threat=threat,
        karma_delta_suggestion=0.0,
        )
        v2 = DharmaYuddhaVerdict(
        outcome=DharmaYuddhaOutcome.NOT_APPLICABLE,
                threat=threat,
        karma_delta_suggestion=0.0,
        )
        assert v1.verdict_id != v2.verdict_id

    def test_proportionality_constant_is_1_2(self) -> None:
        """PROPORTIONALITY_CEILING is 1.2."""
        assert PROPORTIONALITY_CEILING == 1.2

    def test_circuit_proportionality_method_correct(self) -> None:
        """proportionality(0.6, 0.5) == 1.2."""
        circuit = DharmaYuddhaCircuit(HeuristicTargetClassifier())
        assert abs(circuit.proportionality(0.6, 0.5) - 1.2) < 1e-9
