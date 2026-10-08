"""
Tests for dharmaos.ethics_engine.ActionContract — Sprint V-ACT.

Coverage targets (15+ tests):
1.  ActionContract construction: valid input → frozen dataclass instance
2.  Immutability: FrozenInstanceError on attempted mutation
3.  Validation: __post_init__ rejects model_confidence < 0
4.  Validation: __post_init__ rejects model_confidence > 1
5.  Validation: __post_init__ rejects evidence_confidence < 0
6.  Validation: __post_init__ rejects evidence_confidence > 1
7.  Validation: __post_init__ rejects naive (non-tz-aware) requested_at
8.  to_action(): round-trip produces valid Action with matching fields
9.  from_action(): Action + context → ActionContract with defaults populated
10. action_to_contract() helper: explicit context kwargs → populated contract
11. to_dict(): all 33 fields serialise JSON-safely (datetime→ISO, Enum→str)
12. prompt_trust_domain defaults to "system" (V14 tag)
13. needs_fresh_world=True + sources_required=[] logs warning, doesn't raise
14. needs_fresh_world=True + sources_required populated: no warning
15. Integration: ActionContract → to_action() → EthicsEngine.evaluate() matches
evaluate(action) directly
16. ActionImpact enum values are correct strings
17. from_action() preserves reversible + affects_others from source Action
18. 33-field count: to_dict() contains exactly 33 keys (all declared fields)
"""

from __future__ import annotations

import json
import logging
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any

import pytest

from dharmaos.ethics_engine import (
Action,
ActionContract,
ActionImpact,
EthicsEngine,
action_to_contract,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


def _make_contract(**kwargs: Any) -> ActionContract:
    """Build a minimal valid ActionContract, overriding with kwargs."""
    defaults: dict[str, Any] = {
        "action_id": "act-001",
        "path_id": "path-001",
        "agent_id": "agent-001",
        "parent_agent_id": None,
        "tenant_id": "tenant-abc",
        "task_id": "task-001",
        "requested_at": _UTC_NOW,
        "name": "browse_wikipedia",
        "kind": "browser_click",
        "target": "https://en.wikipedia.org/wiki/Python",
        "params": {"query": "python"},
        "tool": "steel_browser",
        "impact": ActionImpact.LOW,
        "reversible": True,
        "affects_others": False,
        "sensitive_data": False,
        "financial_effect": False,
        "public_effect": False,
        "capability_requested": None,
        "declared_goal": "Browse Wikipedia page for Python programming language",
        "beneficiary": "user",
        "business_reason": "User research task",
        "needs_fresh_world": False,
        "freshness_max_age_seconds": 300,
        "sources_required": [],
        "success_criteria": ["page_loaded"],
        "potential_harms": [],
        "model_confidence": 0.9,
        "evidence_confidence": 0.8,
        "policy_version": "1.0.0",
        "prompt_hash": "abc123",
        "model_id": "gemini-flash-2.0",
        "prompt_trust_domain": "system",
    }
    defaults.update(kwargs)
    return ActionContract(**defaults)


def _make_legacy_action(**kwargs: Any) -> Action:
    """Build a minimal valid legacy Action."""
    defaults: dict[str, Any] = {
    "description": "Browse Wikipedia page for Python",
    "intent": "browse",
    "target": "https://en.wikipedia.org/wiki/Python",
    "reversible": True,
    "affects_others": False,
    "agent_id": "agent-001",
    }
    defaults.update(kwargs)
    return Action(**defaults)


# ---------------------------------------------------------------------------
# 1. Construction — valid input → frozen dataclass instance
# ---------------------------------------------------------------------------


class TestActionContractConstruction:
    """ActionContract can be constructed from all 27 fields."""

    def test_valid_construction(self) -> None:
        """Valid construction produces an ActionContract instance."""
        contract = _make_contract()
        assert isinstance(contract, ActionContract)
        assert contract.action_id == "act-001"
        assert contract.kind == "browser_click"

    def test_all_defaults_populated(self) -> None:
        """Fields with defaults are accessible and correct after construction."""
        contract = _make_contract()
        assert contract.impact == ActionImpact.LOW
        assert contract.reversible is True
        assert contract.model_confidence == 0.9
        assert contract.policy_version == "1.0.0"
        assert contract.prompt_trust_domain == "system"

    def test_action_impact_critical(self) -> None:
        """ActionImpact.CRITICAL can be stored in the contract."""
        contract = _make_contract(impact=ActionImpact.CRITICAL)
        assert contract.impact == ActionImpact.CRITICAL


# ---------------------------------------------------------------------------
# 2. Immutability — FrozenInstanceError on attempted mutation
# ---------------------------------------------------------------------------


class TestImmutability:
    """ActionContract is a frozen dataclass — no mutation allowed."""

    def test_frozen_raises_on_mutate(self) -> None:
        """Attempting to set a field raises FrozenInstanceError."""
        contract = _make_contract()
        with pytest.raises(FrozenInstanceError):
            contract.action_id = "mutated"  # type: ignore[misc]

    def test_frozen_raises_on_mutate_impact(self) -> None:
        """Attempting to set impact raises FrozenInstanceError."""
        contract = _make_contract()
        with pytest.raises(FrozenInstanceError):
            contract.impact = ActionImpact.HIGH  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 3-7. Validation — __post_init__ rejection cases
# ---------------------------------------------------------------------------


class TestValidation:
    """__post_init__ validates confidences and tz-awareness."""

    def test_model_confidence_below_zero_raises(self) -> None:
        """model_confidence < 0 raises ValueError."""
        with pytest.raises(ValueError, match="model_confidence"):
            _make_contract(model_confidence=-0.01)

    def test_model_confidence_above_one_raises(self) -> None:
        """model_confidence > 1 raises ValueError."""
        with pytest.raises(ValueError, match="model_confidence"):
            _make_contract(model_confidence=1.001)

    def test_evidence_confidence_below_zero_raises(self) -> None:
        """evidence_confidence < 0 raises ValueError."""
        with pytest.raises(ValueError, match="evidence_confidence"):
            _make_contract(evidence_confidence=-0.5)

    def test_evidence_confidence_above_one_raises(self) -> None:
        """evidence_confidence > 1 raises ValueError."""
        with pytest.raises(ValueError, match="evidence_confidence"):
            _make_contract(evidence_confidence=1.1)

    def test_naive_datetime_raises(self) -> None:
        """A naive (non-tz-aware) requested_at raises ValueError."""
        naive_dt = datetime(2026, 4, 17, 12, 0, 0)  # no tzinfo
        with pytest.raises(ValueError, match="timezone-aware"):
            _make_contract(requested_at=naive_dt)

    def test_boundary_confidences_valid(self) -> None:
        """Confidence values exactly 0.0 and 1.0 are valid (inclusive bounds)."""
        c1 = _make_contract(model_confidence=0.0, evidence_confidence=1.0)
        assert c1.model_confidence == 0.0
        c2 = _make_contract(model_confidence=1.0, evidence_confidence=0.0)
        assert c2.model_confidence == 1.0


# ---------------------------------------------------------------------------
# 8. to_action() — round-trip adapter
# ---------------------------------------------------------------------------


class TestToAction:
    """ActionContract.to_action() produces a valid legacy Action."""

    def test_round_trip_description(self) -> None:
        """to_action() maps declared_goal → description."""
        contract = _make_contract(declared_goal="Do important research")
        action = contract.to_action()
        assert isinstance(action, Action)
        assert action.description == "Do important research"

    def test_round_trip_intent_from_kind(self) -> None:
        """to_action() maps kind → intent."""
        contract = _make_contract(kind="send_email")
        action = contract.to_action()
        assert action.intent == "send_email"

    def test_round_trip_target(self) -> None:
        """to_action() preserves target."""
        contract = _make_contract(target="mailto:user@example.com")
        action = contract.to_action()
        assert action.target == "mailto:user@example.com"

    def test_round_trip_reversible(self) -> None:
        """to_action() preserves reversible."""
        contract = _make_contract(reversible=False)
        action = contract.to_action()
        assert action.reversible is False

    def test_round_trip_affects_others(self) -> None:
        """to_action() preserves affects_others."""
        contract = _make_contract(affects_others=True)
        action = contract.to_action()
        assert action.affects_others is True


# ---------------------------------------------------------------------------
# 9. from_action() — backward-compat promotion
# ---------------------------------------------------------------------------


class TestFromAction:
    """ActionContract.from_action() promotes legacy Action with context."""

    def test_from_action_populates_identity(self) -> None:
        """from_action() correctly populates action_id, path_id, agent_id."""
        action = _make_legacy_action()
        contract = ActionContract.from_action(
            action,
        action_id="act-from-legacy",
        path_id="path-999",
        agent_id="agent-999",
        tenant_id="tenant-x",
        task_id="task-x",
        )
        assert contract.action_id == "act-from-legacy"
        assert contract.path_id == "path-999"
        assert contract.agent_id == "agent-999"
        assert contract.tenant_id == "tenant-x"
        assert contract.task_id == "task-x"

    def test_from_action_preserves_reversible(self) -> None:
        """from_action() preserves reversible from source Action."""
        action = _make_legacy_action(reversible=False)
        contract = ActionContract.from_action(
            action,
        action_id="a",
        path_id="p",
        agent_id="ag",
        tenant_id="t",
        task_id="task",
        )
        assert contract.reversible is False

    def test_from_action_preserves_affects_others(self) -> None:
        """from_action() preserves affects_others from source Action."""
        action = _make_legacy_action(affects_others=True)
        contract = ActionContract.from_action(
            action,
        action_id="a",
        path_id="p",
        agent_id="ag",
        tenant_id="t",
        task_id="task",
        )
        assert contract.affects_others is True

    def test_from_action_defaults_trust_domain(self) -> None:
        """from_action() defaults prompt_trust_domain to 'system'."""
        action = _make_legacy_action()
        contract = ActionContract.from_action(
            action,
        action_id="a",
        path_id="p",
        agent_id="ag",
        tenant_id="t",
        task_id="task",
        )
        assert contract.prompt_trust_domain == "system"

    def test_from_action_requested_at_is_utc(self) -> None:
        """from_action() auto-populates requested_at as UTC-aware if not given."""
        action = _make_legacy_action()
        contract = ActionContract.from_action(
            action,
        action_id="a",
        path_id="p",
        agent_id="ag",
        tenant_id="t",
        task_id="task",
        )
        assert contract.requested_at.tzinfo is not None


# ---------------------------------------------------------------------------
# 10. action_to_contract() helper
# ---------------------------------------------------------------------------


class TestActionToContract:
    """action_to_contract() module-level helper function."""

    def test_helper_produces_contract(self) -> None:
        """action_to_contract() returns an ActionContract with correct identity."""
        action = _make_legacy_action()
        contract = action_to_contract(
            action,
        action_id="act-helper",
        path_id="path-h",
        agent_id="agent-h",
        tenant_id="tenant-h",
        task_id="task-h",
        )
        assert isinstance(contract, ActionContract)
        assert contract.action_id == "act-helper"
        assert contract.path_id == "path-h"

    def test_helper_impact_override(self) -> None:
        """action_to_contract() passes impact through to ActionContract."""
        action = _make_legacy_action()
        contract = action_to_contract(
            action,
        action_id="act-x",
        path_id="path-x",
        agent_id="agent-x",
        tenant_id="tenant-x",
        task_id="task-x",
                impact=ActionImpact.HIGH,
        )
        assert contract.impact == ActionImpact.HIGH

    def test_helper_capability_requested(self) -> None:
        """action_to_contract() passes capability_requested through."""
        action = _make_legacy_action()
        contract = action_to_contract(
            action,
        action_id="act-x",
        path_id="path-x",
        agent_id="agent-x",
        tenant_id="tenant-x",
        task_id="task-x",
        capability_requested="browser:write",
        )
        assert contract.capability_requested == "browser:write"

    def test_helper_extra_overrides(self) -> None:
        """action_to_contract() passes **overrides to from_action()."""
        action = _make_legacy_action()
        contract = action_to_contract(
            action,
        action_id="act-x",
        path_id="path-x",
        agent_id="agent-x",
        tenant_id="tenant-x",
        task_id="task-x",
        business_reason="Compliance requirement FR-42",
        )
        assert contract.business_reason == "Compliance requirement FR-42"


# ---------------------------------------------------------------------------
# 11. to_dict() — 27-field JSON-safe serialization
# ---------------------------------------------------------------------------


class TestToDict:
    """ActionContract.to_dict() serializes all 27 fields to JSON-safe types."""

    def test_to_dict_all_fields_present(self) -> None:
        """to_dict() emits all ActionContract fields (7+5+6+1+3+3+2+2+4 = 33 total fields)."""
        contract = _make_contract()
        d = contract.to_dict()
        # Field count: identity(7) + spec(5) + risk(6) + lease(1) +
        #  intent(3) + world-state(3) + outcome(2) +
        #  confidence(2) + provenance(4) = 33
        assert len(d) == 33

    def test_to_dict_datetime_is_iso_string(self) -> None:
        """to_dict() converts requested_at datetime → ISO 8601 string."""
        contract = _make_contract()
        d = contract.to_dict()
        assert isinstance(d["requested_at"], str)
        # Verify it's a valid ISO string by parsing it back
        parsed = datetime.fromisoformat(d["requested_at"])
        assert parsed.tzinfo is not None

    def test_to_dict_impact_is_string(self) -> None:
        """to_dict() converts ActionImpact enum → plain string value."""
        contract = _make_contract(impact=ActionImpact.CRITICAL)
        d = contract.to_dict()
        assert d["impact"] == "critical"
        assert isinstance(d["impact"], str)

    def test_to_dict_is_json_serializable(self) -> None:
        """to_dict() output passes json.dumps without errors."""
        contract = _make_contract()
        d = contract.to_dict()
        serialized = json.dumps(d)
        assert len(serialized) > 0

    def test_to_dict_all_field_names_present(self) -> None:
        """to_dict() contains all expected field names."""
        contract = _make_contract()
        d = contract.to_dict()
        expected_fields = {
        # Identity and provenance (7)
        "action_id",
        "path_id",
        "agent_id",
        "parent_agent_id",
        "tenant_id",
        "task_id",
        "requested_at",
        # Action specification (5)
        "name",
        "kind",
        "target",
        "params",
        "tool",
        # Risk profile (6)
        "impact",
        "reversible",
        "affects_others",
        "sensitive_data",
        "financial_effect",
        "public_effect",
        # Identity lease (1)
        "capability_requested",
        # Intent (3)
        "declared_goal",
        "beneficiary",
        "business_reason",
        # World-state dependency (3)
        "needs_fresh_world",
        "freshness_max_age_seconds",
        "sources_required",
        # Expected outcome (2)
        "success_criteria",
        "potential_harms",
        # Confidence (2)
        "model_confidence",
        "evidence_confidence",
        # Provenance (4)
        "policy_version",
        "prompt_hash",
        "model_id",
        "prompt_trust_domain",
        }
        assert set(d.keys()) == expected_fields


# ---------------------------------------------------------------------------
# 12. prompt_trust_domain defaults to "system" (V14 tag)
# ---------------------------------------------------------------------------


class TestPromptTrustDomain:
    """prompt_trust_domain defaults to 'system' per V14 Trust-Domain spec."""

    def test_default_trust_domain_is_system(self) -> None:
        """ActionContract constructed without prompt_trust_domain → 'system'."""
        # Verify via from_action() which does not pass prompt_trust_domain
        action = _make_legacy_action()
        c = ActionContract.from_action(
            action,
        action_id="a",
        path_id="p",
        agent_id="ag",
        tenant_id="t",
        task_id="task",
        )
        assert c.prompt_trust_domain == "system"

    def test_trust_domain_can_be_overridden(self) -> None:
        """prompt_trust_domain can be set to a non-default value."""
        contract = _make_contract(prompt_trust_domain="user")
        assert contract.prompt_trust_domain == "user"


# ---------------------------------------------------------------------------
# 13-14. needs_fresh_world + sources_required interaction
# ---------------------------------------------------------------------------


class TestFreshnessGating:
    """needs_fresh_world + sources_required freshness-gate interaction."""

    def test_needs_fresh_world_no_sources_logs_warning(
    self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """needs_fresh_world=True + sources_required=[] logs a warning but doesn't raise."""
        with caplog.at_level(logging.WARNING, logger="dharmaos.ethics_engine"):
            contract = _make_contract(needs_fresh_world=True, sources_required=[])
        assert contract.needs_fresh_world is True
        # Warning must appear in logs
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("sources_required" in m for m in warning_messages)

    def test_needs_fresh_world_with_sources_no_warning(
    self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """needs_fresh_world=True + sources_required populated: no freshness warning."""
        with caplog.at_level(logging.WARNING, logger="dharmaos.ethics_engine"):
            contract = _make_contract(
            needs_fresh_world=True,
            sources_required=["https://api.example.com/prices"],
            )
        assert contract.needs_fresh_world is True
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("sources_required" in m for m in warning_messages)


# ---------------------------------------------------------------------------
# 15. Integration: ActionContract → to_action() → EthicsEngine.evaluate()
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration: ActionContract flows through V1 EthicsEngine via to_action()."""

    def test_contract_via_to_action_matches_direct_evaluate(self) -> None:
        """evaluate(contract.to_action()) verdict matches evaluate(action) directly."""
        action = _make_legacy_action(
        description="Browse public Wikipedia page",
                intent="browse",
                target="https://en.wikipedia.org/wiki/Python",
        reversible=True,
        affects_others=False,
        agent_id="test-agent",
        )
        contract = ActionContract.from_action(
            action,
        action_id="int-001",
        path_id="path-int",
        agent_id="test-agent",
        tenant_id="t",
        task_id="task-int",
        )
        engine = EthicsEngine()
        verdict_direct = engine.evaluate(action)
        verdict_via_contract = engine.evaluate(contract.to_action())

        assert verdict_direct.is_ethical == verdict_via_contract.is_ethical
        assert verdict_direct.risk_level == verdict_via_contract.risk_level


# ---------------------------------------------------------------------------
# 16. ActionImpact enum values
# ---------------------------------------------------------------------------


class TestActionImpactEnum:
    """ActionImpact enum values are correct strings."""

    def test_enum_values(self) -> None:
        """All four ActionImpact variants have correct string values."""
        assert ActionImpact.LOW.value == "low"
        assert ActionImpact.MEDIUM.value == "medium"
        assert ActionImpact.HIGH.value == "high"
        assert ActionImpact.CRITICAL.value == "critical"

    def test_enum_is_str_subclass(self) -> None:
        """ActionImpact is a str Enum — values compare equal to plain strings."""
        assert ActionImpact.LOW == "low"
        assert ActionImpact.CRITICAL == "critical"
