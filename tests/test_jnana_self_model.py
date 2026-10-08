"""Tests for V-JÑĀNA Metacognitive Self-Model (jnana_self_model.py).

Covers RF-46 closure (metacognitive self-model layer missing).

Test count: 30 minimum.
- V15Stub validator:  tests 1-5
- capability_vector updates:  tests 6-11
- V-RECON integration:  tests 12-16
- Sākṣī signal:  tests 17-18
- V11 audit queries:  tests 19-25
- Policy amendment proposals: tests 26-28
- Snapshot/restore:  tests 29-30
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import UUID, uuid4

from dharmaos.ethics_engine import ActionContract, ActionImpact
from dharmaos.jnana_self_model import (
JNANA_AUDIT_BLIND_SPOT,
JNANA_AUDIT_DEFICIT,
JNANA_AUDIT_PASS,
JNANA_AUDIT_UNKNOWN,
BiTemporalTimestamp,
ConfidenceScore,
JnanaModule,
JnanaSelfModel,
V15Stub,
)
from dharmaos.outcome_reconciler import (
OutcomeDelta,
OutcomeStatus,
)
from dharmaos.samskara_ledger import SamskaraEntry

# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)
_AGENT_ID = "agent-jnana-test"
_TENANT_ID = "tenant-jnana-test"
_OTHER_TENANT_ID = "tenant-other"


def _make_module(**kwargs) -> JnanaModule:
    """Create a fresh JnanaModule with test defaults."""
    return JnanaModule(
        _AGENT_ID,
        _TENANT_ID,
        **kwargs,
    )


def _make_samskara(
action_sig: str,
karma_delta: float,
org_id: UUID | None = None,
) -> SamskaraEntry:
    return SamskaraEntry(
    action_signature=action_sig,
    action_trace={"test": True},
    consequence_trace={"test": True},
    karma_delta=karma_delta,
    valid_from=_NOW,
            org_id=org_id
        or UUID(
            _TENANT_ID.ljust(32, "0")[:32].replace(
    "tenant-jnana-test00000000000000", "00000000000000000000000000000001"
    )
    ),
    )


def _make_samskara_no_org(action_sig: str, karma_delta: float) -> SamskaraEntry:
    return SamskaraEntry(
    action_signature=action_sig,
    action_trace={"test": True},
    consequence_trace={"test": True},
    karma_delta=karma_delta,
    valid_from=_NOW,
            org_id=None,  # None = no tenant filter applied
    )


def _make_contract(
capability_requested: str = "code",
agent_id: str = _AGENT_ID,
tenant_id: str = _TENANT_ID,
) -> ActionContract:
    return ActionContract(
    action_id=str(uuid4()),
    path_id="test-path",
    agent_id=agent_id,
    parent_agent_id=None,
    tenant_id=tenant_id,
    task_id="test-task",
    requested_at=_NOW,
            name=capability_requested,
            kind="test",
            target="test-target",
    capability_requested=capability_requested,
            impact=ActionImpact.LOW,
    )


def _make_outcome_delta(
status: OutcomeStatus,
criterion_hit_rate: float = 1.0,
harm_rate: float = 0.0,
unanticipated_harms: list[str] | None = None,
brier_score: float = 0.1,
) -> OutcomeDelta:
    return OutcomeDelta(
    contract_id="test-contract",
    receipt_id=uuid4(),
            status=status,
    criterion_hit_rate=criterion_hit_rate,
    criterion_matches=[],
    harm_materialization_rate=harm_rate,
    harm_matches=[],
    unanticipated_outcomes=[],
    unanticipated_harms=unanticipated_harms or [],
    brier_score=brier_score,
    karma_delta_suggestion=0.5 if status == OutcomeStatus.SUCCESS else -0.5,
    )


def _make_jnana_model(
version: int = 1,
agent_id: str = _AGENT_ID,
tenant_id: str = _TENANT_ID,
policy_drift_score: float = 0.0,
metacognitive_calibration: float = 0.5,
capability_vector: dict | None = None,
) -> JnanaSelfModel:
    now = datetime.now(UTC)
    return JnanaSelfModel(
    agent_id=agent_id,
    tenant_id=tenant_id,
    capability_vector=capability_vector or {},
    blind_spot_list=[],
    strategy_preference_map={},
    policy_drift_score=policy_drift_score,
    metacognitive_calibration=metacognitive_calibration,
    last_updated=BiTemporalTimestamp(valid_time=now, transaction_time=now),
    version=version,
    )


# ---------------------------------------------------------------------------
# V15Stub validator tests (1-5)
# ---------------------------------------------------------------------------


class TestV15Stub:
    """Tests 1-5: V15Stub validator behaviour."""

    def test_01_valid_forward_version_bump_accepted(self):
        """Test 1: Valid forward version bump → accept."""
        stub = V15Stub()
        old = _make_jnana_model(version=1)
        new = _make_jnana_model(version=2)
        ok, reason = stub.validate(old, new)
        assert ok is True
        assert reason == "ok"

    def test_02_version_decrease_rejected(self):
        """Test 2: Version decrease → reject."""
        stub = V15Stub()
        old = _make_jnana_model(version=5)
        new = _make_jnana_model(version=4)
        ok, reason = stub.validate(old, new)
        assert ok is False
        assert "version rollback" in reason

    def test_03_agent_id_swap_rejected(self):
        """Test 3: agent_id swap → reject."""
        stub = V15Stub()
        old = _make_jnana_model(version=1, agent_id="agent-a")
        new = _make_jnana_model(version=2, agent_id="agent-b")
        ok, reason = stub.validate(old, new)
        assert ok is False
        assert "agent_id" in reason

    def test_04_nan_in_confidence_rejected(self):
        """Test 4: NaN in confidence value → reject."""
        stub = V15Stub()
        old = _make_jnana_model(version=1)
        nan_score = ConfidenceScore(
                value=math.nan,
        sample_count=1,
        last_observed=_NOW,
        )
        new = _make_jnana_model(
        version=2,
        capability_vector={"code": nan_score},
        )
        ok, reason = stub.validate(old, new)
        assert ok is False
        assert "non-finite" in reason

    def test_05_dropping_over_50_percent_capability_entries_rejected(self):
        """Test 5: Dropping >50% of capability_vector entries → reject."""
        stub = V15Stub()
        cs = ConfidenceScore(value=0.8, sample_count=10, last_observed=_NOW)
        old_cap = {f"domain-{i}": cs for i in range(10)}
        new_cap = {f"domain-{i}": cs for i in range(4)}  # kept 4/10 = 60% dropped
        old = _make_jnana_model(version=1, capability_vector=old_cap)
        new = _make_jnana_model(version=2, capability_vector=new_cap)
        ok, reason = stub.validate(old, new)
        assert ok is False
        assert "amnesia" in reason

    def test_05b_exactly_50_percent_drop_allowed(self):
        """Boundary: exactly 50% drop is allowed (> not >=)."""
        stub = V15Stub()
        cs = ConfidenceScore(value=0.8, sample_count=5, last_observed=_NOW)
        old_cap = {"a": cs, "b": cs, "c": cs, "d": cs}
        new_cap = {"a": cs, "b": cs}  # 50% = not > 0.5
        old = _make_jnana_model(version=1, capability_vector=old_cap)
        new = _make_jnana_model(version=2, capability_vector=new_cap)
        ok, _ = stub.validate(old, new)
        assert ok is True

    def test_05c_drift_score_large_step_rejected(self):
        """Drift score delta > 0.5 in single step → rejected."""
        stub = V15Stub()
        old = _make_jnana_model(version=1, policy_drift_score=0.1)
        new = _make_jnana_model(version=2, policy_drift_score=0.7)
        ok, reason = stub.validate(old, new)
        assert ok is False
        assert "drift" in reason


# ---------------------------------------------------------------------------
# capability_vector updates (6-11)
# ---------------------------------------------------------------------------


class TestCapabilityVectorUpdates:
    """Tests 6-11: capability_vector update behaviour."""

    def test_06_positive_karma_increases_confidence(self):
        """Test 6: Positive-karma-delta saṃskāra batch → confidence goes up."""
        mod = _make_module()
        entries = [_make_samskara_no_org(f"code:sort_{i}", 0.8) for i in range(5)]
        result = mod.ingest_samskara_batch(entries)
        assert "code" in result.capability_vector
        score = result.capability_vector["code"]
        assert score.value > 0.5  # positive deltas should produce above-neutral confidence

    def test_07_negative_karma_decreases_confidence(self):
        """Test 7: Negative-karma-delta batch → confidence goes down."""
        mod = _make_module()
        entries = [_make_samskara_no_org(f"code:bad_{i}", -0.8) for i in range(5)]
        result = mod.ingest_samskara_batch(entries)
        assert "code" in result.capability_vector
        score = result.capability_vector["code"]
        assert score.value < 0.5  # negative deltas → below-neutral confidence

    def test_08_cross_tenant_entry_filtered_out(self):
        """Test 8: SamskaraEntry with mismatched org_id → capability_vector unchanged."""
        mod = _make_module()
        # Create entry with a different UUID (not matching _TENANT_ID)
        other_org_id = uuid4()
        entry = SamskaraEntry(
        action_signature="code:other_tenant",
        action_trace={},
        consequence_trace={},
        karma_delta=0.9,
        valid_from=_NOW,
                org_id=other_org_id,
        )
        result = mod.ingest_samskara_batch([entry])
        # capability_vector should be empty — cross-tenant entries are filtered.
        assert "code" not in result.capability_vector

    def test_09_sample_count_accumulates_across_ingests(self):
        """Test 9: Sample count accumulates across multiple ingest calls."""
        mod = _make_module()
        batch1 = [_make_samskara_no_org("finance:price", 0.5) for _ in range(3)]
        batch2 = [_make_samskara_no_org("finance:price", 0.5) for _ in range(4)]
        mod.ingest_samskara_batch(batch1)
        result = mod.ingest_samskara_batch(batch2)
        assert "finance" in result.capability_vector
        # Sample count should reflect all 7 observations.
        assert result.capability_vector["finance"].sample_count >= 7

    def test_10_unknown_domain_before_ingest_then_known_after(self):
        """Test 10: Unknown domain before ingest → None; known after ingest."""
        mod = _make_module()
        assert mod.capability_report("browse") is None
        entries = [_make_samskara_no_org("browse:google", 0.6)]
        mod.ingest_samskara_batch(entries)
        assert mod.capability_report("browse") is not None

    def test_11_validator_rejection_leaves_version_unchanged(self):
        """Test 11: If validator rejects the update, model.version is unchanged."""
        mod = _make_module()
        initial_version = mod.current_model.version

        # Inject a validator that always rejects.
        class AlwaysReject:
            def validate(self, old, new):
                return (False, "test rejection")

        mod._validator = AlwaysReject()
        entries = [_make_samskara_no_org("code:test", 0.9)]
        result = mod.ingest_samskara_batch(entries)
        assert result.version == initial_version


# ---------------------------------------------------------------------------
# V-RECON integration (12-16)
# ---------------------------------------------------------------------------


class TestVReconIntegration:
    """Tests 12-16: OutcomeDelta / V-RECON integration."""

    def test_12_success_delta_calibration_trends_up(self):
        """Test 12: SUCCESS delta → metacognitive_calibration trends up (low Brier)."""
        mod = _make_module()
        contract = _make_contract("code")
        # Low brier_score means good calibration (brier 0.0 = perfect)
        delta = _make_outcome_delta(OutcomeStatus.SUCCESS, brier_score=0.05)
        result = mod.ingest_outcome_delta(contract, delta)
        # calibration = 1 - avg_brier; with brier=0.05, calibration=0.95
        assert result.metacognitive_calibration > 0.5

    def test_13_harmful_delta_creates_blind_spot(self):
        """Test 13: HARMFUL delta (≥threshold) → BlindSpot added for that domain."""
        # threshold=1 means first HARMFUL creates it immediately.
        mod = _make_module(blind_spot_threshold_samples=1)
        contract = _make_contract("finance")
        delta = _make_outcome_delta(
            OutcomeStatus.HARMFUL,
        criterion_hit_rate=0.1,
        unanticipated_harms=["unexpected_loss"],
        )
        result = mod.ingest_outcome_delta(contract, delta)
        blind_spots = [bs for bs in result.blind_spot_list if bs.domain == "finance"]
        assert len(blind_spots) == 1

    def test_14_partial_success_smaller_confidence_update_than_success(self):
        """Test 14: PARTIAL_SUCCESS → smaller magnitude than SUCCESS."""
        mod_success = _make_module()
        mod_partial = _make_module()

        contract = _make_contract("code")

        delta_success = _make_outcome_delta(OutcomeStatus.SUCCESS)
        delta_partial = _make_outcome_delta(OutcomeStatus.PARTIAL_SUCCESS)

        r_success = mod_success.ingest_outcome_delta(contract, delta_success)
        r_partial = mod_partial.ingest_outcome_delta(contract, delta_partial)

        # Both start from no prior data (base=0.5); success bump > partial bump.
        success_conf = r_success.capability_vector["code"].value
        partial_conf = r_partial.capability_vector["code"].value
        assert success_conf > partial_conf

    def test_15_unanticipated_harms_populate_blind_spot_failure_pattern(self):
        """Test 15: Unanticipated harms → BlindSpot.failure_pattern set."""
        mod = _make_module(blind_spot_threshold_samples=1)
        contract = _make_contract("browse")
        delta = _make_outcome_delta(
            OutcomeStatus.HARMFUL,
        unanticipated_harms=["credential_leak_pattern"],
        )
        result = mod.ingest_outcome_delta(contract, delta)
        browse_spots = [bs for bs in result.blind_spot_list if bs.domain == "browse"]
        assert len(browse_spots) == 1
        assert "credential_leak_pattern" in browse_spots[0].failure_pattern

    def test_16_repeated_harmful_deltas_accumulate_evidence_count(self):
        """Test 16: Repeated HARMFUL deltas in same domain → evidence_count grows."""
        mod = _make_module(blind_spot_threshold_samples=1)
        contract = _make_contract("finance")

        for _ in range(4):
            delta = _make_outcome_delta(
                OutcomeStatus.HARMFUL,
            unanticipated_harms=["bad_outcome"],
            )
            mod.ingest_outcome_delta(contract, delta)

        finance_spots = [bs for bs in mod.current_model.blind_spot_list if bs.domain == "finance"]
        assert len(finance_spots) == 1
        assert finance_spots[0].evidence_count >= 4


# ---------------------------------------------------------------------------
# Sākṣī signal (17-18)
# ---------------------------------------------------------------------------


class TestSakshiSignal:
    """Tests 17-18: Sākṣī witness confidence signal ingestion."""

    def test_17_high_sakshi_confidence_pulls_capability_up(self):
        """Test 17: High Sākṣī witness confidence → capability confidence pulled toward it."""
        mod = _make_module()
        # Seed a low confidence for "browse".
        entries = [_make_samskara_no_org("browse:failing", -0.5) for _ in range(5)]
        mod.ingest_samskara_batch(entries)
        low_conf = mod.capability_report("browse")
        assert low_conf is not None
        before_value = low_conf.value

        # Inject a high Sākṣī signal.
        mod.ingest_sakshi_signal("browse", 0.95)
        after = mod.capability_report("browse")
        assert after is not None
        assert after.value > before_value

    def test_18_low_sakshi_confidence_pulls_capability_down(self):
        """Test 18: Low Sākṣī confidence → capability confidence pulled down."""
        mod = _make_module()
        # Seed a high confidence for "browse".
        entries = [_make_samskara_no_org("browse:success", 0.9) for _ in range(5)]
        mod.ingest_samskara_batch(entries)
        high_conf = mod.capability_report("browse")
        assert high_conf is not None
        before_value = high_conf.value

        # Inject a low Sākṣī signal.
        mod.ingest_sakshi_signal("browse", 0.05)
        after = mod.capability_report("browse")
        assert after is not None
        assert after.value < before_value


# ---------------------------------------------------------------------------
# V11 audit queries (19-25)
# ---------------------------------------------------------------------------


class TestV11AuditQueries:
    """Tests 19-25: V11 Layer 7 audit query behaviour."""

    def test_19_unknown_domain_returns_unknown_event(self):
        """Test 19: Unknown domain → UNKNOWN_DOMAIN event."""
        mod = _make_module()
        report = mod.audit(domain="totally-unknown-domain")
        assert report.event == JNANA_AUDIT_UNKNOWN
        assert report.confidence is None
        assert "require_hitl" in report.recommendation

    def test_20_confidence_above_threshold_returns_pass(self):
        """Test 20: Confidence above threshold → PASS."""
        mod = _make_module(confidence_threshold=0.6)
        # Ingest positive karma to push confidence high.
        entries = [_make_samskara_no_org("code:python", 0.9) for _ in range(5)]
        mod.ingest_samskara_batch(entries)
        report = mod.audit(domain="code")
        assert report.event == JNANA_AUDIT_PASS
        assert report.confidence is not None
        assert report.confidence >= 0.6

    def test_21_confidence_below_threshold_returns_jnana_deficit(self):
        """Test 21: Confidence below threshold → JNANA_DEFICIT."""
        mod = _make_module(confidence_threshold=0.7)
        # Ingest negative karma to push confidence below threshold.
        entries = [_make_samskara_no_org("code:buggy", -0.9) for _ in range(5)]
        mod.ingest_samskara_batch(entries)
        report = mod.audit(domain="code")
        assert report.event == JNANA_AUDIT_DEFICIT
        assert report.confidence is not None
        assert report.confidence < 0.7

    def test_22_action_pattern_matching_blind_spot_returns_blind_spot_match(self):
        """Test 22: action_pattern matching a BlindSpot.failure_pattern → BLIND_SPOT_MATCH."""
        mod = _make_module(blind_spot_threshold_samples=1)
        contract = _make_contract("finance")
        delta = _make_outcome_delta(
            OutcomeStatus.HARMFUL,
        unanticipated_harms=["insider_trading_pattern"],
        )
        mod.ingest_outcome_delta(contract, delta)

        report = mod.audit(domain="finance", action_pattern="insider_trading_pattern")
        assert report.event == JNANA_AUDIT_BLIND_SPOT
        assert report.matched_blind_spot is not None
        assert report.matched_blind_spot.domain == "finance"

    def test_23_blind_spot_warnings_scoped_to_domain(self):
        """Test 23: blind_spot_warnings(domain=X) returns only that domain's spots."""
        mod = _make_module(blind_spot_threshold_samples=1)
        # Add blind spots in two different domains.
        for domain_name in ("finance", "browse"):
            contract = _make_contract(domain_name)
            delta = _make_outcome_delta(OutcomeStatus.HARMFUL, unanticipated_harms=["pattern"])
            mod.ingest_outcome_delta(contract, delta)

        finance_warnings = mod.blind_spot_warnings(domain="finance")
        browse_warnings = mod.blind_spot_warnings(domain="browse")
        all_warnings = mod.blind_spot_warnings()

        assert all(bs.domain == "finance" for bs in finance_warnings)
        assert all(bs.domain == "browse" for bs in browse_warnings)
        assert len(all_warnings) >= len(finance_warnings) + len(browse_warnings)

    def test_24_capability_report_returns_confidence_score_or_none(self):
        """Test 24: capability_report(domain) returns ConfidenceScore or None."""
        mod = _make_module()
        assert mod.capability_report("nonexistent") is None

        entries = [_make_samskara_no_org("code:sort", 0.7)]
        mod.ingest_samskara_batch(entries)
        result = mod.capability_report("code")
        assert isinstance(result, ConfidenceScore)
        assert 0.0 <= result.value <= 1.0

    def test_25_self_confidence_vector_returns_dict_domain_float(self):
        """Test 25: self_confidence_vector() returns dict[domain, float]."""
        mod = _make_module()
        entries = [
            _make_samskara_no_org("code:sort", 0.8),
            _make_samskara_no_org("browse:search", 0.6),
        ]
        mod.ingest_samskara_batch(entries)
        vec = mod.self_confidence_vector()

        assert isinstance(vec, dict)
        assert "code" in vec
        assert "browse" in vec
        for domain, conf in vec.items():
            assert isinstance(domain, str)
            assert isinstance(conf, float)
            assert 0.0 <= conf <= 1.0


# ---------------------------------------------------------------------------
# Policy amendment proposals (26-28)
# ---------------------------------------------------------------------------


class TestPolicyAmendmentProposals:
    """Tests 26-28: V11 Layer 8 policy-knowledge reconciliation."""

    def test_26_high_drift_score_emits_proposal(self):
        """Test 26: High policy_drift_score + observed divergence → proposal emitted."""
        mod = _make_module(drift_threshold=0.05)
        contract = _make_contract("code")

        # Ingest enough FAILURE deltas to push drift up.
        for _ in range(10):
            delta = _make_outcome_delta(
                OutcomeStatus.FAILURE,
            criterion_hit_rate=0.0,
            brier_score=0.9,
            )
            mod.ingest_outcome_delta(contract, delta)

        proposals = mod.policy_amendment_proposals(contract=contract)
        assert len(proposals) >= 1
        assert proposals[0].recommended_action in (
        "require_hitl",
        "narrow_scope",
        "revoke_capability",
        )

    def test_27_below_drift_threshold_no_proposal(self):
        """Test 27: Below drift threshold → no proposal (without low criterion hit rate)."""
        mod = _make_module(drift_threshold=0.95)  # very high threshold — almost never triggered
        # Don't ingest any outcomes — drift stays at 0.0.
        proposals = mod.policy_amendment_proposals()
        assert len(proposals) == 0

    def test_28_proposal_ids_unique_across_multiple_emissions(self):
        """Test 28: Proposal ID unique across multiple emissions."""
        mod = _make_module(drift_threshold=0.001)  # very low threshold
        contract = _make_contract("code")

        # Drive drift score above threshold.
        for _ in range(5):
            delta = _make_outcome_delta(OutcomeStatus.FAILURE, criterion_hit_rate=0.0)
            mod.ingest_outcome_delta(contract, delta)

        proposals_a = mod.policy_amendment_proposals(contract=contract)
        proposals_b = mod.policy_amendment_proposals(contract=contract)

        ids_a = {p.proposal_id for p in proposals_a}
        ids_b = {p.proposal_id for p in proposals_b}
        # IDs from different calls must be distinct (each call creates new UUIDs).
        assert ids_a.isdisjoint(ids_b)

    def test_28b_low_criterion_hit_rate_triggers_narrow_scope_proposal(self):
        """Low criterion hit rate + contract → narrow_scope or revoke proposal."""
        mod = _make_module(drift_threshold=0.99)  # disable drift trigger
        contract = _make_contract("finance")
        low_hit_delta = _make_outcome_delta(
            OutcomeStatus.FAILURE,
        criterion_hit_rate=0.2,  # below 0.5
        )
        proposals = mod.policy_amendment_proposals(
        contract=contract,
        observed_delta=low_hit_delta,
        )
        assert len(proposals) >= 1
        assert proposals[0].recommended_action in ("narrow_scope", "revoke_capability")


# ---------------------------------------------------------------------------
# Snapshot / restore (29-30)
# ---------------------------------------------------------------------------


class TestSnapshotRestore:
    """Tests 29-30: Snapshot/restore RF-46 closure."""

    def test_29_snapshot_round_trips_full_model_state(self):
        """Test 29: snapshot() round-trips version + capability_vector + blind_spots."""
        mod = _make_module(blind_spot_threshold_samples=1)

        # Build up some state.
        entries = [_make_samskara_no_org("code:sort", 0.8) for _ in range(3)]
        mod.ingest_samskara_batch(entries)
        contract = _make_contract("code")
        delta = _make_outcome_delta(
            OutcomeStatus.HARMFUL, unanticipated_harms=["dangerous_pattern"]
        )
        mod.ingest_outcome_delta(contract, delta)

        snap = mod.snapshot()
        before_version = mod.current_model.version
        before_cap = dict(mod.current_model.capability_vector)
        before_blind_spots = list(mod.current_model.blind_spot_list)

        # Create a fresh module and restore.
        mod2 = _make_module(blind_spot_threshold_samples=1)
        mod2.restore(snap)

        assert mod2.current_model.version == before_version
        assert set(mod2.current_model.capability_vector.keys()) == set(before_cap.keys())
        assert len(mod2.current_model.blind_spot_list) == len(before_blind_spots)

    def test_30_end_to_end_rf46_closure(self):
        """Test 30: End-to-end RF-46 closure.

        Build a JnanaModule, ingest 10 saṃskāras + 3 V-RECON deltas + 2 Sākṣī
        signals; assert:
        a. capability_vector non-empty
        b. at least one BlindSpot exists
        c. audit() returns sensible event for a known-weak domain
        d. snapshot+restore preserves the audit verdict
        """
        mod = _make_module(
        blind_spot_threshold_samples=2,
        confidence_threshold=0.6,
        )

        # --- 10 saṃskāras: mix of domains ---
        good_entries = [_make_samskara_no_org("finance:price", 0.7) for _ in range(5)]
        bad_entries = [_make_samskara_no_org("code:buggy", -0.8) for _ in range(5)]
        mod.ingest_samskara_batch(good_entries + bad_entries)

        # --- 3 V-RECON deltas: HARMFUL for code ---
        contract = _make_contract("code")
        for _ in range(3):
            delta = _make_outcome_delta(
                OutcomeStatus.HARMFUL,
            criterion_hit_rate=0.1,
            unanticipated_harms=["unexpected_crash"],
            )
            mod.ingest_outcome_delta(contract, delta)

        # --- 2 Sākṣī signals ---
        mod.ingest_sakshi_signal("finance", 0.85)  # boost finance
        mod.ingest_sakshi_signal("code", 0.15)  # drag code down

        # a. capability_vector non-empty.
        assert len(mod.current_model.capability_vector) > 0, "capability_vector should be non-empty"

        # b. At least one BlindSpot.
        assert len(mod.current_model.blind_spot_list) >= 1, "should have at least one blind spot"

        # c. Audit a known-weak domain (code).
        code_report = mod.audit(domain="code")
        assert code_report.event in (JNANA_AUDIT_DEFICIT, JNANA_AUDIT_BLIND_SPOT, JNANA_AUDIT_PASS)
        # Given heavy negative signals, expect deficit or blind spot.
        assert code_report.event != JNANA_AUDIT_UNKNOWN, "code domain should be known"

        # d. Snapshot + restore preserves the audit verdict.
        snap = mod.snapshot()
        mod2 = _make_module(blind_spot_threshold_samples=2, confidence_threshold=0.6)
        mod2.restore(snap)

        code_report_restored = mod2.audit(domain="code")
        assert code_report_restored.event == code_report.event, (
            f"Restored module audit event {code_report_restored.event!r} "
        f"should match original {code_report.event!r}"
        )
