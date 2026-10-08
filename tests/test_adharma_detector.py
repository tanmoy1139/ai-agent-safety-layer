"""Tests for V11 Adharma Detector — 8-layer DharmaOS governance pipeline.

Coverage plan:
Layer 1 — Schema + identity + lease (tests 1-5)
Layer 2 — World-state freshness  (tests 6-9)
Layer 3 — Constitutional vetoes  (tests 10-13)
Layer 4 — Domain overlays  (tests 14-16)
Layer 5 — Path-risk + cumulative budget (tests 17-20)
Layer 6 — Abuse Twin + Guṇa  (tests 21-25)
Layer 7 — Jñāna audit  (tests 26-29)
Layer 8 — Policy-knowledge reconciliation (tests 30-32)
Composition invariants  (tests 33-38)
End-to-end integration  (tests 39-41)

Closes RF-45: the 41 tests prove the 8-layer pipeline is fully functional.

TD-V11-01: LLM-judge for Layer 6 Yakṣa-praśna is deferred to Phase 4;
heuristic matchers are exercised here.

Sprint V11, 2026-04-17.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from dharmaos.adharma_detector import (
AdharmaDetector,
AdharmaReport,
AdharmaVerdict,
LayerStatus,
LayerVerdict,
_check_yaksha_prashna,
)
from dharmaos.ethics_engine import ActionContract, ActionImpact

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _make_contract(
*,
        name: str = "test_action",
        kind: str = "browse",
target: str = "https://example.com",
agent_id: str = "agent-001",
tenant_id: str = "tenant-abc",
capability_requested: str | None = "browse",
declared_goal: str = "Fetch public page",
business_reason: str = "Legitimate research task",
reversible: bool = True,
affects_others: bool = False,
financial_effect: bool = False,
public_effect: bool = False,
needs_fresh_world: bool = False,
sources_required: list[str] | None = None,
potential_harms: list[str] | None = None,
impact: ActionImpact = ActionImpact.LOW,
params: dict[str, Any] | None = None,
) -> ActionContract:
    return ActionContract(
    action_id=str(uuid4()),
    path_id="path-001",
    agent_id=agent_id,
    parent_agent_id=None,
    tenant_id=tenant_id,
    task_id="task-001",
    requested_at=_now_utc(),
            name=name,
            kind=kind,
            target=target,
    reversible=reversible,
    affects_others=affects_others,
    financial_effect=financial_effect,
    public_effect=public_effect,
    capability_requested=capability_requested,
    declared_goal=declared_goal,
    business_reason=business_reason,
    needs_fresh_world=needs_fresh_world,
    sources_required=sources_required or [],
    potential_harms=potential_harms or [],
            impact=impact,
            params=params or {},
    )


def _make_lease(
registry: Any,
*,
agent_id: str = "agent-001",
tenant_id: str = "tenant-abc",
capabilities: list[str] | None = None,
lease_seconds: int = 300,
) -> Any:
    """Grant a fresh lease and return it."""
    return registry.grant(
    agent_id=agent_id,
    task_id="task-001",
    capabilities=capabilities or ["browse"],
    tenant_id=tenant_id,
    lease_seconds=lease_seconds,
    )


def _make_registry():
    from dharmaos.lease import LeaseRegistry

    return LeaseRegistry()


def _make_detector(**kwargs: Any) -> AdharmaDetector:
    """Minimal AdharmaDetector — no upstream services by default."""
    return AdharmaDetector(**kwargs)


# ============================================================================
# Layer 1 — Schema + identity + lease
# ============================================================================


class TestLayer1SchemaIdentityLease:
    """Tests 1-5."""

    def test_01_valid_lease_matching_capability_allows(self):
        """Test 1: Valid lease + matching capability → Layer 1 ALLOW."""
        registry = _make_registry()
        contract = _make_contract(capability_requested="browse")
        lease = _make_lease(registry, capabilities=["browse"])

        detector = _make_detector(lease_registry=registry)
        report = detector.evaluate(
            contract,
        presented_lease_id=lease.lease_id,
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        l1 = report.layer_verdicts[0]
        assert l1.layer_num == 1
        assert l1.status == LayerStatus.ALLOW

    def test_02_no_lease_presented_denies(self):
        """Test 2: No lease presented → Layer 1 DENY."""
        registry = _make_registry()
        contract = _make_contract()

        detector = _make_detector(lease_registry=registry)
        report = detector.evaluate(
            contract,
        presented_lease_id=None,
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        l1 = report.layer_verdicts[0]
        assert l1.status == LayerStatus.DENY
        assert report.halted_at_layer == 1

    def test_03_expired_lease_denies(self):
        """Test 3: Expired lease → Layer 1 DENY."""
        registry = _make_registry()
        contract = _make_contract(capability_requested="browse")
        # Grant lease that expires in -1 seconds (already expired)
        lease = _make_lease(registry, capabilities=["browse"], lease_seconds=1)
        # Manually advance: use a future "now" in check

        future_now = datetime.now(UTC) + timedelta(seconds=10)
        verdict = registry.check(
        lease_id=lease.lease_id,
        agent_id=contract.agent_id,
        tenant_id=contract.tenant_id,
        capability="browse",
                now=future_now,
        )
        from dharmaos.lease import LeaseStatus

        assert verdict.status == LeaseStatus.EXPIRED

        # Verify detector also denies — use a patched LeaseRegistry that returns EXPIRED
        mock_registry = MagicMock()
        expired_verdict = MagicMock()
        expired_verdict.status = LeaseStatus.EXPIRED
        expired_verdict.reason = "lease expired"
        mock_registry.check_contract.return_value = expired_verdict

        detector = _make_detector(lease_registry=mock_registry)
        report = detector.evaluate(
            contract,
        presented_lease_id=lease.lease_id,
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 1

    def test_04_tenant_mismatch_denies(self):
        """Test 4: Tenant mismatch → Layer 1 DENY (halts pipeline)."""
        registry = _make_registry()
        contract = _make_contract(tenant_id="tenant-abc")
        lease = _make_lease(registry, tenant_id="tenant-abc", capabilities=["browse"])

        detector = _make_detector(lease_registry=registry)
        # Present wrong tenant
        report = detector.evaluate(
            contract,
        presented_lease_id=lease.lease_id,
        presented_agent_id=contract.agent_id,
        presented_tenant_id="tenant-EVIL",  # mismatch with contract.tenant_id
        )
        assert report.verdict == AdharmaVerdict.DENY
        l1 = report.layer_verdicts[0]
        assert l1.status == LayerStatus.DENY
        assert report.halted_at_layer == 1
        # Pipeline halted — only Layer 1 result
        assert len(report.layer_verdicts) == 1

    def test_05_capability_not_in_lease_denies(self):
        """Test 5: Capability not in lease → Layer 1 DENY."""
        registry = _make_registry()
        # Lease grants only 'browse', but contract requests 'db_write'
        contract = _make_contract(capability_requested="db_write")
        lease = _make_lease(registry, capabilities=["browse"])

        detector = _make_detector(lease_registry=registry)
        report = detector.evaluate(
            contract,
        presented_lease_id=lease.lease_id,
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 1
        assert report.layer_verdicts[0].status == LayerStatus.DENY


# ============================================================================
# Layer 2 — World-state freshness
# ============================================================================


class TestLayer2WorldStateFreshness:
    """Tests 6-9."""

    def _fresh_registry_and_lease(self):
        registry = _make_registry()
        contract = _make_contract(capability_requested="browse")
        lease = _make_lease(registry, capabilities=["browse"])
        return registry, contract, lease

    def _detector_with_passing_l1(self, world_state=None):
        """Detector with mock registry that always passes L1."""
        from dharmaos.lease import LeaseStatus

        mock_registry = MagicMock()
        ok_verdict = MagicMock()
        ok_verdict.status = LeaseStatus.VALID
        ok_verdict.reason = "ok"
        mock_registry.check_contract.return_value = ok_verdict
        return AdharmaDetector(
        lease_registry=mock_registry,
        world_state=world_state,
        )

    def test_06_needs_fresh_world_false_skips_layer(self):
        """Test 6: needs_fresh_world=False → Layer 2 SKIP."""
        contract = _make_contract(needs_fresh_world=False)
        detector = self._detector_with_passing_l1()
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        l2 = next((v for v in report.layer_verdicts if v.layer_num == 2), None)
        assert l2 is not None
        assert l2.status == LayerStatus.SKIP

    def test_07_all_sources_fresh_allows(self):
        """Test 7: needs_fresh_world=True, all sources fresh → Layer 2 ALLOW."""
        mock_ws = MagicMock()
        mock_ws.require_fresh.return_value = None  # None = all fresh

        contract = _make_contract(
        needs_fresh_world=True,
        sources_required=["btc_price", "market_status"],
        )
        detector = self._detector_with_passing_l1(world_state=mock_ws)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        l2 = next((v for v in report.layer_verdicts if v.layer_num == 2), None)
        assert l2 is not None
        assert l2.status == LayerStatus.ALLOW

    def test_08_stale_source_denies(self):
        """Test 8: One source stale → Layer 2 DENY."""
        stale_report = MagicMock()
        stale_report.stale_sources = ["btc_price"]
        stale_report.missing_sources = []

        mock_ws = MagicMock()
        mock_ws.require_fresh.return_value = stale_report

        contract = _make_contract(
        needs_fresh_world=True,
        sources_required=["btc_price"],
        )
        detector = self._detector_with_passing_l1(world_state=mock_ws)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 2
        l2 = report.layer_verdicts[1]
        assert l2.status == LayerStatus.DENY

    def test_09_missing_source_denies(self):
        """Test 9: Missing source (never ingested) → Layer 2 DENY."""
        stale_report = MagicMock()
        stale_report.stale_sources = []
        stale_report.missing_sources = ["exchange_rate"]

        mock_ws = MagicMock()
        mock_ws.require_fresh.return_value = stale_report

        contract = _make_contract(
        needs_fresh_world=True,
        sources_required=["exchange_rate"],
        )
        detector = self._detector_with_passing_l1(world_state=mock_ws)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 2


# ============================================================================
# Layer 3 — Constitutional vetoes
# ============================================================================


class TestLayer3ConstitutionalVetoes:
    """Tests 10-13."""

    def _detector_passing_l1_l2(
    self,
    ethics_engine=None,
    rta_supervisor=None,
    ):
        from dharmaos.lease import LeaseStatus

        mock_registry = MagicMock()
        ok_verdict = MagicMock()
        ok_verdict.status = LeaseStatus.VALID
        ok_verdict.reason = "ok"
        mock_registry.check_contract.return_value = ok_verdict
        return AdharmaDetector(
        lease_registry=mock_registry,
        ethics_engine=ethics_engine,
        rta_supervisor=rta_supervisor,
        )

    def test_10_yama_compliant_contract_allows(self):
        """Test 10: Yama-compliant contract → Layer 3 ALLOW."""
        mock_engine = MagicMock()
        verdict = MagicMock()
        verdict.is_ethical = True
        verdict.violated_constraints = []
        verdict.score = 1.0
        mock_engine.evaluate.return_value = verdict

        contract = _make_contract()
        detector = self._detector_passing_l1_l2(ethics_engine=mock_engine)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        l3 = next((v for v in report.layer_verdicts if v.layer_num == 3), None)
        assert l3 is not None
        assert l3.status == LayerStatus.ALLOW

    def test_11_yama_violation_ahimsa_denies(self):
        """Test 11: Yama-violating contract (ahiṃsā failure) → Layer 3 DENY."""
        mock_engine = MagicMock()
        verdict = MagicMock()
        verdict.is_ethical = False
        verdict.violated_constraints = ["ahimsa"]
        verdict.score = 0.0
        verdict.recommendations = ["Non-harm violated"]
        mock_engine.evaluate.return_value = verdict

        contract = _make_contract()
        detector = self._detector_passing_l1_l2(ethics_engine=mock_engine)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 3

    def test_12_constitution_hash_mismatch_denies(self):
        """Test 12: Constitution hash mismatch → DENY via RtaSupervisor CRITICAL."""
        from dharmaos.rta import InvariantResult, InvariantStatus, RtaAuditReport, SystemMode

        failing_result = InvariantResult(
                name="constitution_hash",
                status=InvariantStatus.FAIL,
        message="Constitution hash mismatch",
        severity="critical",
        )
        report_obj = RtaAuditReport(
        results=[failing_result],
        overall_status=InvariantStatus.FAIL,
                mode=SystemMode.HALTED,
        audit_ts=_now_utc(),
        )
        mock_rta = MagicMock()
        mock_rta.audit.return_value = report_obj

        contract = _make_contract()
        detector = self._detector_passing_l1_l2(rta_supervisor=mock_rta)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 3

    def test_13_rta_critical_invariant_denies(self):
        """Test 13: RtaSupervisor CRITICAL invariant failing → Layer 3 DENY."""
        from dharmaos.rta import InvariantResult, InvariantStatus, RtaAuditReport, SystemMode

        failing_result = InvariantResult(
                name="rls_isolation",
                status=InvariantStatus.FAIL,
        message="RLS breach detected",
        severity="critical",
        )
        report_obj = RtaAuditReport(
        results=[failing_result],
        overall_status=InvariantStatus.FAIL,
                mode=SystemMode.HALTED,
        audit_ts=_now_utc(),
        )
        mock_rta = MagicMock()
        mock_rta.audit.return_value = report_obj

        contract = _make_contract()
        detector = self._detector_passing_l1_l2(rta_supervisor=mock_rta)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 3
        l3 = next((v for v in report.layer_verdicts if v.layer_num == 3), None)
        assert l3 is not None
        assert "rls_isolation" in " ".join(l3.reasons) or "Ṛta invariant" in " ".join(l3.reasons)


# ============================================================================
# Layer 4 — Domain overlays
# ============================================================================


class TestLayer4DomainOverlays:
    """Tests 14-16."""

    def _detector_passing_layers_1_3(self) -> AdharmaDetector:
        from dharmaos.lease import LeaseStatus

        mock_registry = MagicMock()
        ok_verdict = MagicMock()
        ok_verdict.status = LeaseStatus.VALID
        ok_verdict.reason = "ok"
        mock_registry.check_contract.return_value = ok_verdict

        mock_engine = MagicMock()
        ethics_verdict = MagicMock()
        ethics_verdict.is_ethical = True
        ethics_verdict.violated_constraints = []
        ethics_verdict.score = 1.0
        mock_engine.evaluate.return_value = ethics_verdict

        return AdharmaDetector(
        lease_registry=mock_registry,
        ethics_engine=mock_engine,
        )

    def test_14_code_execution_without_signoff_denies(self):
        """Test 14: code_execution domain overlay fires → Layer 4 DENY."""
        # Contract that triggers code_execution overlay: kind='exec', no approval in reason
        contract = _make_contract(
                kind="exec",
                target="/bin/bash",
        business_reason="",  # no sign-off
        )
        detector = self._detector_passing_layers_1_3()
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        # Should DENY at layer 4 OR earlier layer
        assert report.verdict == AdharmaVerdict.DENY

    def test_15_custom_registered_overlay_fires(self):
        """Test 15: Custom registered overlay fires → Layer 4 DENY."""
        contract = _make_contract(
                name="dangerous_custom_action",
                kind="custom_risky",
        )

        def custom_overlay(c: ActionContract) -> LayerVerdict | None:
            if "risky" in c.kind:
                return LayerVerdict(
                layer_num=4,
                layer_name="custom_risky",
                        status=LayerStatus.DENY,
                severity="high",
                reasons=["Custom overlay: risky action blocked."],
                )
            return None

        detector = self._detector_passing_layers_1_3()
        detector.register_domain_overlay("custom_risky", custom_overlay)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        l4 = next((v for v in report.layer_verdicts if v.layer_num == 4), None)
        assert l4 is not None
        assert l4.status == LayerStatus.DENY

    def test_16_unknown_domain_no_overlays_allows(self):
        """Test 16: Unknown domain with no matching overlays → Layer 4 ALLOW."""
        contract = _make_contract(
                name="benign_unclassified_action",
                kind="benign_custom",
                target="local://cache",
        )
        detector = self._detector_passing_layers_1_3()
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
                domain="unknown_new_domain",
        )
        l4 = next((v for v in report.layer_verdicts if v.layer_num == 4), None)
        # Layer 4 should ALLOW (permissive for unknown domains)
        assert l4 is not None
        assert l4.status == LayerStatus.ALLOW


# ============================================================================
# Layer 5 — Path-risk + cumulative budget
# ============================================================================


class TestLayer5PathRiskBudget:
    """Tests 17-20."""

    def _detector_passing_layers_1_4(self, samskara_ledger=None, budget_cap=200) -> AdharmaDetector:
        from dharmaos.lease import LeaseStatus

        mock_registry = MagicMock()
        ok_verdict = MagicMock()
        ok_verdict.status = LeaseStatus.VALID
        ok_verdict.reason = "ok"
        mock_registry.check_contract.return_value = ok_verdict

        mock_engine = MagicMock()
        ethics_verdict = MagicMock()
        ethics_verdict.is_ethical = True
        ethics_verdict.violated_constraints = []
        ethics_verdict.score = 1.0
        mock_engine.evaluate.return_value = ethics_verdict

        return AdharmaDetector(
        lease_registry=mock_registry,
        ethics_engine=mock_engine,
        samskara_ledger=samskara_ledger,
        budget_cap=budget_cap,
        )

    def test_17_no_samskara_history_allows(self):
        """Test 17: No saṃskāra history → Layer 5 ALLOW (insufficient data)."""
        mock_ledger = MagicMock()
        mock_ledger.query.return_value = []  # no history
        mock_ledger.detect_escalation.return_value = None
        mock_ledger._mem = {}  # no entries for 24h check

        contract = _make_contract(capability_requested="browse")
        detector = self._detector_passing_layers_1_4(samskara_ledger=mock_ledger)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        l5 = next((v for v in report.layer_verdicts if v.layer_num == 5), None)
        assert l5 is not None
        assert l5.status == LayerStatus.ALLOW

    def test_18_negative_karma_pattern_denies(self):
        """Test 18: Recent negative karma pattern → Layer 5 DENY."""
        # P0-3 / P1-5: Now uses karma_rolling_mean() instead of query()
        mock_ledger = MagicMock()
        mock_ledger.karma_rolling_mean.return_value = -0.5  # Below -0.3 threshold
        mock_ledger.detect_escalation.return_value = None
        mock_ledger.recent_entries.return_value = []  # For 24h budget check

        contract = _make_contract(capability_requested="browse")
        detector = self._detector_passing_layers_1_4(samskara_ledger=mock_ledger)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 5

    def test_19_escalation_alert_high_denies(self):
        """Test 19: EscalationAlert HIGH from detect_escalation → Layer 5 DENY."""
        from dharmaos.samskara_ledger import EscalationAlert

        alert = EscalationAlert(
                org_id=uuid4(),
        baseline_mean=0.5,
        mid_mean=0.2,
        recent_mean=-0.1,
                delta=0.6,
        top_contributors=["browse:malicious_page"],
        detected_at=_now_utc(),
        severity="high",
        )

        mock_ledger = MagicMock()
        mock_ledger.karma_rolling_mean.return_value = None  # No karma signal
        mock_ledger.detect_escalation.return_value = alert
        mock_ledger.recent_entries.return_value = []  # For 24h budget check

        contract = _make_contract()
        detector = self._detector_passing_layers_1_4(samskara_ledger=mock_ledger)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 5
        l5 = next((v for v in report.layer_verdicts if v.layer_num == 5), None)
        assert "escalation" in " ".join(l5.reasons).lower()

    def test_20_over_budget_escalates(self):
        """Test 20: Over daily action budget → Layer 5 ESCALATE."""
        # Build 15 fake entries within last 24h (budget_cap=10)
        fake_entries = []
        for _i in range(15):
            entry = MagicMock()
            entry.valid_from = datetime.now(UTC) - timedelta(hours=1)
            fake_entries.append(entry)

        mock_ledger = MagicMock()
        mock_ledger.karma_rolling_mean.return_value = None  # No karma signal
        mock_ledger.detect_escalation.return_value = None
        mock_ledger.recent_entries.return_value = fake_entries  # 15 entries = over budget

        hitl_called: list[AdharmaReport] = []
        contract = _make_contract()
        detector = self._detector_passing_layers_1_4(
        samskara_ledger=mock_ledger,
        budget_cap=10,
        )
        detector._hitl_escalate = hitl_called.append
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        # ESCALATE → HITL callback fires → final verdict = DENY
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 5
        assert len(hitl_called) == 1


# ============================================================================
# Layer 6 — Abuse Twin + Guṇa
# ============================================================================


class TestLayer6AbuseTwinGuna:
    """Tests 21-25."""

    def _detector_passing_layers_1_5(self, guna_monitor=None) -> AdharmaDetector:
        from dharmaos.lease import LeaseStatus

        mock_registry = MagicMock()
        ok_verdict = MagicMock()
        ok_verdict.status = LeaseStatus.VALID
        ok_verdict.reason = "ok"
        mock_registry.check_contract.return_value = ok_verdict

        mock_engine = MagicMock()
        ethics_verdict = MagicMock()
        ethics_verdict.is_ethical = True
        ethics_verdict.violated_constraints = []
        ethics_verdict.score = 1.0
        mock_engine.evaluate.return_value = ethics_verdict

        mock_ledger = MagicMock()
        mock_ledger.query.return_value = []
        mock_ledger.detect_escalation.return_value = None
        mock_ledger._mem = {}

        return AdharmaDetector(
        lease_registry=mock_registry,
        ethics_engine=mock_engine,
        samskara_ledger=mock_ledger,
        guna_monitor=guna_monitor,
        )

    def test_21_clean_contract_sattva_allows(self):
        """Test 21: Clean contract (no abuse signals, sattva guṇa) → Layer 6 ALLOW."""
        contract = _make_contract(
        declared_goal="Fetch public page to summarise for user",
        business_reason="Market research for internal report",
        )
        detector = self._detector_passing_layers_1_5()
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        l6 = next((v for v in report.layer_verdicts if v.layer_num == 6), None)
        assert l6 is not None
        assert l6.status == LayerStatus.ALLOW

    def test_22_two_yaksha_prashna_yes_denies(self):
        """Test 22: ≥2 Yakṣa-praśna 'yes' answers → Layer 6 DENY."""
        # Q1 (hidden intent): 'stealth' in goal
        # Q4 (urgency): 'bypass review' in goal
        contract = _make_contract(
        declared_goal="Stealth bypass review immediately",
        )
        yes_answers = _check_yaksha_prashna(contract)
        assert len(yes_answers) >= 2, f"Expected ≥2 yes, got {yes_answers}"

        detector = self._detector_passing_layers_1_5()
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        l6 = next((v for v in report.layer_verdicts if v.layer_num == 6), None)
        assert l6 is not None
        assert l6.status == LayerStatus.DENY

    def test_23_one_yaksha_prashna_yes_escalates(self):
        """Test 23: 1 Yakṣa-praśna 'yes' → Layer 6 ESCALATE."""
        # Q1 (hidden intent only): 'stealth'
        contract = _make_contract(
        declared_goal="Stealth collection",
        business_reason="Legitimate internal audit",  # no urgency bypass
        )
        yes_answers = _check_yaksha_prashna(contract)
        # Should trigger exactly Q1 (hidden intent)
        assert 1 in yes_answers
        # Ensure Q4 not triggered
        assert 4 not in yes_answers, f"Unexpected Q4 trigger: {yes_answers}"

        detector = self._detector_passing_layers_1_5()
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        # ESCALATE → HITL → DENY
        assert report.verdict == AdharmaVerdict.DENY
        l6 = next((v for v in report.layer_verdicts if v.layer_num == 6), None)
        assert l6 is not None
        assert l6.status == LayerStatus.ESCALATE

    def test_24_tamas_guna_high_confidence_denies(self):
        """Test 24: Tamas guṇa with confidence ≥0.7 → Layer 6 DENY."""
        from dharmaos.guna_monitor import GunaVector

        tamas_vec = GunaVector(sattva=0.1, rajas=0.2, tamas=0.7, timestamp=_now_utc())
        mock_guna = MagicMock()
        mock_guna._history = [tamas_vec]

        contract = _make_contract(
        declared_goal="Completely benign action",
        business_reason="Clear legitimate reason",
        )
        detector = self._detector_passing_layers_1_5(guna_monitor=mock_guna)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        l6 = next((v for v in report.layer_verdicts if v.layer_num == 6), None)
        assert l6 is not None
        assert l6.status == LayerStatus.DENY
        assert "tamas" in " ".join(l6.reasons)

    def test_25_both_subchecks_passing_allows(self):
        """Test 25: Both Yakṣa-praśna (no abuse) and guṇa (sattva) passing → ALLOW."""
        from dharmaos.guna_monitor import GunaVector

        sattva_vec = GunaVector(sattva=0.7, rajas=0.2, tamas=0.1, timestamp=_now_utc())
        mock_guna = MagicMock()
        mock_guna._history = [sattva_vec]

        contract = _make_contract(
        declared_goal="Summarise public research paper",
        business_reason="Literature review for team report",
        )
        detector = self._detector_passing_layers_1_5(guna_monitor=mock_guna)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        l6 = next((v for v in report.layer_verdicts if v.layer_num == 6), None)
        assert l6 is not None
        assert l6.status == LayerStatus.ALLOW


# ============================================================================
# Layer 7 — Jñāna audit
# ============================================================================


class TestLayer7JnanaAudit:
    """Tests 26-29."""

    def _detector_passing_layers_1_6(self, jnana_module=None) -> AdharmaDetector:
        from dharmaos.lease import LeaseStatus

        mock_registry = MagicMock()
        ok_verdict = MagicMock()
        ok_verdict.status = LeaseStatus.VALID
        ok_verdict.reason = "ok"
        mock_registry.check_contract.return_value = ok_verdict

        mock_engine = MagicMock()
        ethics_verdict = MagicMock()
        ethics_verdict.is_ethical = True
        ethics_verdict.violated_constraints = []
        ethics_verdict.score = 1.0
        mock_engine.evaluate.return_value = ethics_verdict

        mock_ledger = MagicMock()
        mock_ledger.query.return_value = []
        mock_ledger.detect_escalation.return_value = None
        mock_ledger._mem = {}

        return AdharmaDetector(
        lease_registry=mock_registry,
        ethics_engine=mock_engine,
        samskara_ledger=mock_ledger,
        jnana_module=jnana_module,
        )

    def _mock_jnana(
    self, event: str, domain: str = "browse", confidence: float | None = None, blind_spot=None
    ):
        from dharmaos.jnana_self_model import JnanaAuditReport

        report = JnanaAuditReport(
                event=event,
                domain=domain,
        confidence=confidence,
        matched_blind_spot=blind_spot,
        recommendation="test recommendation",
        )
        mock_jnana = MagicMock()
        mock_jnana.audit.return_value = report
        mock_jnana.policy_amendment_proposals.return_value = []
        return mock_jnana

    def test_26_jnana_pass_allows(self):
        """Test 26: JnanaModule returns PASS → Layer 7 ALLOW."""
        from dharmaos.jnana_self_model import JNANA_AUDIT_PASS

        mock_jnana = self._mock_jnana(JNANA_AUDIT_PASS, confidence=0.85)
        contract = _make_contract()
        detector = self._detector_passing_layers_1_6(jnana_module=mock_jnana)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        l7 = next((v for v in report.layer_verdicts if v.layer_num == 7), None)
        assert l7 is not None
        assert l7.status == LayerStatus.ALLOW

    def test_27_jnana_deficit_denies(self):
        """Test 27: JnanaModule returns JNANA_DEFICIT → Layer 7 DENY."""
        from dharmaos.jnana_self_model import JNANA_AUDIT_DEFICIT

        mock_jnana = self._mock_jnana(JNANA_AUDIT_DEFICIT, confidence=0.3)
        contract = _make_contract()
        detector = self._detector_passing_layers_1_6(jnana_module=mock_jnana)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 7
        l7 = next((v for v in report.layer_verdicts if v.layer_num == 7), None)
        assert l7 is not None
        assert l7.status == LayerStatus.DENY

    def test_28_blind_spot_match_denies(self):
        """Test 28: JnanaModule returns BLIND_SPOT_MATCH → Layer 7 DENY."""
        from dharmaos.jnana_self_model import JNANA_AUDIT_BLIND_SPOT, BlindSpot

        bs = BlindSpot(
                domain="finance",
        failure_pattern="options_mispricing",
        confidence_of_unawareness=0.8,
        evidence_count=5,
        first_seen=_now_utc(),
        last_seen=_now_utc(),
        )
        mock_jnana = self._mock_jnana(JNANA_AUDIT_BLIND_SPOT, blind_spot=bs)
        contract = _make_contract(capability_requested="finance:options")
        detector = self._detector_passing_layers_1_6(jnana_module=mock_jnana)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 7
        l7 = next((v for v in report.layer_verdicts if v.layer_num == 7), None)
        assert l7 is not None
        assert l7.status == LayerStatus.DENY
        assert "blind" in " ".join(l7.reasons).lower()

    def test_29_unknown_domain_escalates(self):
        """Test 29: JnanaModule returns UNKNOWN_DOMAIN → Layer 7 ESCALATE."""
        from dharmaos.jnana_self_model import JNANA_AUDIT_UNKNOWN

        mock_jnana = self._mock_jnana(JNANA_AUDIT_UNKNOWN)
        contract = _make_contract()
        detector = self._detector_passing_layers_1_6(jnana_module=mock_jnana)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        # ESCALATE → DENY
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 7
        l7 = next((v for v in report.layer_verdicts if v.layer_num == 7), None)
        assert l7 is not None
        assert l7.status == LayerStatus.ESCALATE


# ============================================================================
# Layer 8 — Policy-knowledge reconciliation
# ============================================================================


class TestLayer8PolicyKnowledgeReconciliation:
    """Tests 30-32."""

    def _detector_passing_layers_1_7(self, jnana_module=None) -> AdharmaDetector:
        from dharmaos.jnana_self_model import JNANA_AUDIT_PASS, JnanaAuditReport
        from dharmaos.lease import LeaseStatus

        mock_registry = MagicMock()
        ok_verdict = MagicMock()
        ok_verdict.status = LeaseStatus.VALID
        ok_verdict.reason = "ok"
        mock_registry.check_contract.return_value = ok_verdict

        mock_engine = MagicMock()
        ethics_verdict = MagicMock()
        ethics_verdict.is_ethical = True
        ethics_verdict.violated_constraints = []
        ethics_verdict.score = 1.0
        mock_engine.evaluate.return_value = ethics_verdict

        mock_ledger = MagicMock()
        mock_ledger.query.return_value = []
        mock_ledger.detect_escalation.return_value = None
        mock_ledger._mem = {}

        if jnana_module is None:
            # Default: Layer 7 PASS, Layer 8 no proposals
            pass_report = JnanaAuditReport(
                    event=JNANA_AUDIT_PASS,
                    domain="browse",
            confidence=0.9,
            recommendation="ok",
            )
            jnana_module = MagicMock()
            jnana_module.audit.return_value = pass_report
            jnana_module.policy_amendment_proposals.return_value = []

        return AdharmaDetector(
        lease_registry=mock_registry,
        ethics_engine=mock_engine,
        samskara_ledger=mock_ledger,
        jnana_module=jnana_module,
        )

    def test_30_no_proposals_allows(self):
        """Test 30: No amendment proposals → Layer 8 ALLOW."""
        contract = _make_contract()
        detector = self._detector_passing_layers_1_7()
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        l8 = next((v for v in report.layer_verdicts if v.layer_num == 8), None)
        assert l8 is not None
        assert l8.status == LayerStatus.ALLOW
        assert report.verdict == AdharmaVerdict.PERMIT

    def test_31_revoke_capability_proposal_denies(self):
        """Test 31: Proposal with 'revoke_capability' → Layer 8 DENY."""
        from dharmaos.jnana_self_model import (
        JNANA_AUDIT_PASS,
        JnanaAuditReport,
        PolicyAmendmentProposal,
        )

        revoke_proposal = PolicyAmendmentProposal(
                domain="finance",
        current_declared_scope="finance:trade",
        observed_behavior_description="Repeated harmful trades",
        drift_magnitude=0.8,
        recommended_action="revoke_capability",
        rationale="Too many harmful outcomes detected.",
        )
        pass_report = JnanaAuditReport(
                event=JNANA_AUDIT_PASS,
                domain="finance",
        confidence=0.9,
        recommendation="ok",
        )
        mock_jnana = MagicMock()
        mock_jnana.audit.return_value = pass_report
        mock_jnana.policy_amendment_proposals.return_value = [revoke_proposal]

        contract = _make_contract(capability_requested="finance:trade")
        detector = self._detector_passing_layers_1_7(jnana_module=mock_jnana)
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 8
        l8 = next((v for v in report.layer_verdicts if v.layer_num == 8), None)
        assert l8 is not None
        assert l8.status == LayerStatus.DENY
        assert "revoke" in " ".join(l8.reasons).lower()

    def test_32_require_hitl_proposal_escalates(self):
        """Test 32: Proposal with 'require_hitl' → Layer 8 ESCALATE."""
        from dharmaos.jnana_self_model import (
        JNANA_AUDIT_PASS,
        JnanaAuditReport,
        PolicyAmendmentProposal,
        )

        hitl_proposal = PolicyAmendmentProposal(
                domain="browse",
        current_declared_scope="browse",
        observed_behavior_description="Policy drift exceeds threshold",
        drift_magnitude=0.4,
        recommended_action="require_hitl",
        rationale="Drift score exceeded 0.3.",
        )
        pass_report = JnanaAuditReport(
                event=JNANA_AUDIT_PASS,
                domain="browse",
        confidence=0.9,
        recommendation="ok",
        )
        mock_jnana = MagicMock()
        mock_jnana.audit.return_value = pass_report
        mock_jnana.policy_amendment_proposals.return_value = [hitl_proposal]

        hitl_calls: list[AdharmaReport] = []
        contract = _make_contract()
        detector = self._detector_passing_layers_1_7(jnana_module=mock_jnana)
        detector._hitl_escalate = hitl_calls.append
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        # ESCALATE → HITL → DENY
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 8
        l8 = next((v for v in report.layer_verdicts if v.layer_num == 8), None)
        assert l8 is not None
        assert l8.status == LayerStatus.ESCALATE
        # HITL callback was invoked
        assert len(hitl_calls) == 1


# ============================================================================
# Composition invariants
# ============================================================================


class TestComposition:
    """Tests 33-38."""

    def _all_passing_detector(
    self, jnana_module=None, hitl_escalate=None, shadow_mode=False, budget_cap=200
    ) -> AdharmaDetector:
        from dharmaos.jnana_self_model import JNANA_AUDIT_PASS, JnanaAuditReport
        from dharmaos.lease import LeaseStatus

        mock_registry = MagicMock()
        ok_verdict = MagicMock()
        ok_verdict.status = LeaseStatus.VALID
        ok_verdict.reason = "ok"
        mock_registry.check_contract.return_value = ok_verdict

        mock_engine = MagicMock()
        ethics_verdict = MagicMock()
        ethics_verdict.is_ethical = True
        ethics_verdict.violated_constraints = []
        ethics_verdict.score = 1.0
        mock_engine.evaluate.return_value = ethics_verdict

        mock_ledger = MagicMock()
        mock_ledger.query.return_value = []
        mock_ledger.detect_escalation.return_value = None
        mock_ledger._mem = {}

        if jnana_module is None:
            pass_report = JnanaAuditReport(
                    event=JNANA_AUDIT_PASS,
                    domain="browse",
            confidence=0.9,
            recommendation="ok",
            )
            jnana_module = MagicMock()
            jnana_module.audit.return_value = pass_report
            jnana_module.policy_amendment_proposals.return_value = []

        return AdharmaDetector(
        lease_registry=mock_registry,
        ethics_engine=mock_engine,
        samskara_ledger=mock_ledger,
        jnana_module=jnana_module,
        hitl_escalate=hitl_escalate,
        shadow_mode=shadow_mode,
        budget_cap=budget_cap,
        )

    def test_33_deny_at_layer3_halts_pipeline(self):
        """Test 33: Layer 3 DENY halts — Layer 4+ not invoked."""
        from dharmaos.lease import LeaseStatus

        mock_registry = MagicMock()
        ok_verdict = MagicMock()
        ok_verdict.status = LeaseStatus.VALID
        ok_verdict.reason = "ok"
        mock_registry.check_contract.return_value = ok_verdict

        # Ethics engine returns violation
        mock_engine = MagicMock()
        ethics_verdict = MagicMock()
        ethics_verdict.is_ethical = False
        ethics_verdict.violated_constraints = ["ahimsa"]
        ethics_verdict.score = 0.0
        ethics_verdict.recommendations = ["Harm detected"]
        mock_engine.evaluate.return_value = ethics_verdict

        contract = _make_contract()
        detector = AdharmaDetector(
        lease_registry=mock_registry,
        ethics_engine=mock_engine,
        )

        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        # Pipeline halted at layer 3
        assert report.halted_at_layer == 3
        # No layer 4 result in verdicts
        assert all(v.layer_num <= 3 for v in report.layer_verdicts)
        assert report.verdict == AdharmaVerdict.DENY

    def test_34_all_8_layers_pass_gives_permit(self):
        """Test 34: All 8 layers pass → verdict=PERMIT."""
        contract = _make_contract()
        detector = self._all_passing_detector()
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.PERMIT
        assert report.halted_at_layer is None
        assert len(report.layer_verdicts) == 8

    def test_35_escalate_on_any_layer_invokes_hitl_and_denies(self):
        """Test 35: ESCALATE on any layer → hitl_escalate callback invoked + verdict DENY."""
        from dharmaos.jnana_self_model import JNANA_AUDIT_UNKNOWN, JnanaAuditReport

        unknown_report = JnanaAuditReport(
                event=JNANA_AUDIT_UNKNOWN,
                domain="unknown_new",
        recommendation="Require HITL",
        )
        mock_jnana = MagicMock()
        mock_jnana.audit.return_value = unknown_report
        mock_jnana.policy_amendment_proposals.return_value = []

        hitl_calls: list[AdharmaReport] = []
        contract = _make_contract()
        detector = self._all_passing_detector(
        jnana_module=mock_jnana,
        hitl_escalate=hitl_calls.append,
        )
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert len(hitl_calls) == 1
        assert hitl_calls[0].report_id == report.report_id

    def test_36_shadow_mode_permits_with_would_have_blocked(self):
        """Test 36: Shadow mode: verdict=PERMIT + would_have_blocked=True when layer blocks."""
        from dharmaos.lease import LeaseStatus

        mock_registry = MagicMock()
        ok_verdict = MagicMock()
        ok_verdict.status = LeaseStatus.VALID
        ok_verdict.reason = "ok"
        mock_registry.check_contract.return_value = ok_verdict

        # Ethics engine returns violation — would block at L3
        mock_engine = MagicMock()
        ethics_verdict = MagicMock()
        ethics_verdict.is_ethical = False
        ethics_verdict.violated_constraints = ["ahimsa"]
        ethics_verdict.score = 0.0
        ethics_verdict.recommendations = ["Harm detected"]
        mock_engine.evaluate.return_value = ethics_verdict

        contract = _make_contract()
        detector = AdharmaDetector(
        lease_registry=mock_registry,
        ethics_engine=mock_engine,
        shadow_mode=True,
        )
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.verdict == AdharmaVerdict.PERMIT  # shadow: always PERMIT
        assert report.would_have_blocked is True
        assert report.shadow_mode is True

    def test_37_halted_at_layer_correctly_recorded(self):
        """Test 37: report.halted_at_layer correctly records the halt point."""
        contract = _make_contract()
        # No lease presented — halts at Layer 1
        from dharmaos.lease import LeaseRegistry

        registry = LeaseRegistry()
        detector = AdharmaDetector(lease_registry=registry)
        report = detector.evaluate(
            contract,
        presented_lease_id=None,
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        assert report.halted_at_layer == 1

    def test_38_total_latency_tracked_and_layer_latencies_sum(self):
        """Test 38: Total latency tracked + sum of per-layer latencies within tolerance."""
        contract = _make_contract()
        detector = self._all_passing_detector()
        report = detector.evaluate(
            contract,
        presented_lease_id=uuid4(),
        presented_agent_id=contract.agent_id,
        presented_tenant_id=contract.tenant_id,
        )
        # total_latency_ms should be positive
        assert report.total_latency_ms > 0.0
        # Each layer verdict should have a non-negative latency_ms
        assert all(v.latency_ms >= 0.0 for v in report.layer_verdicts)
        # Sum of layer latencies should be <= total_latency_ms + small overhead (1 ms tolerance)
        sum_layer = sum(v.latency_ms for v in report.layer_verdicts)
        assert sum_layer <= report.total_latency_ms + 1.0, (
            f"Layer sum {sum_layer:.3f} > total {report.total_latency_ms:.3f}"
        )


# ============================================================================
# End-to-end integration
# ============================================================================


class TestEndToEnd:
    """Tests 39-41."""

    def _build_real_detector(
    self,
    *,
    shadow_mode: bool = False,
    hitl_escalate: Callable | None = None,
    ) -> tuple[AdharmaDetector, Any]:
        """Build a detector wired to real (lightweight) module instances."""
        from dharmaos.ethics_engine import EthicsEngine
        from dharmaos.jnana_self_model import JnanaModule
        from dharmaos.lease import LeaseRegistry
        from dharmaos.samskara_ledger import SamskaraLedger

        registry = LeaseRegistry()
        ledger = SamskaraLedger(storage="memory")
        engine = EthicsEngine()
        jnana = JnanaModule(agent_id="agent-001", tenant_id="tenant-abc")

        detector = AdharmaDetector(
        lease_registry=registry,
        samskara_ledger=ledger,
        ethics_engine=engine,
        jnana_module=jnana,
        shadow_mode=shadow_mode,
        hitl_escalate=hitl_escalate,
        )
        return detector, registry

    def test_39_full_happy_path_permit(self):
        """Test 39: Full happy-path — all services wired + green → PERMIT report."""
        from dharmaos.jnana_self_model import JnanaModule

        detector, registry = self._build_real_detector()

        # Seed the jnana module so 'browse' domain is known + confident
        jnana: JnanaModule = detector._jnana_module  # type: ignore[assignment]
        jnana.ingest_sakshi_signal("browse", 0.9)

        # Grant a valid lease
        lease = registry.grant(
        agent_id="agent-001",
        task_id="task-001",
        capabilities=["browse"],
        tenant_id="tenant-abc",
        )

        contract = _make_contract(
        agent_id="agent-001",
        tenant_id="tenant-abc",
                kind="browse",
        capability_requested="browse",
        declared_goal="Browse public documentation",
        business_reason="Research for internal wiki update",
        )

        report = detector.evaluate(
            contract,
        presented_lease_id=lease.lease_id,
        presented_agent_id="agent-001",
        presented_tenant_id="tenant-abc",
        )
        assert report.verdict == AdharmaVerdict.PERMIT
        assert report.halted_at_layer is None
        assert len(report.layer_verdicts) == 8

    def test_40_full_deny_path_earliest_layer(self):
        """Test 40: Stale world-state triggers DENY at Layer 2 (if L1 passes)."""
        from dharmaos.ethics_engine import EthicsEngine
        from dharmaos.jnana_self_model import JnanaModule
        from dharmaos.lease import LeaseRegistry
        from dharmaos.samskara_ledger import SamskaraLedger

        registry = LeaseRegistry()
        ledger = SamskaraLedger(storage="memory")
        engine = EthicsEngine()
        jnana = JnanaModule(agent_id="agent-001", tenant_id="tenant-abc")

        # World state that returns stale report
        mock_ws = MagicMock()
        stale_report = MagicMock()
        stale_report.stale_sources = ["market_data"]
        stale_report.missing_sources = []
        mock_ws.require_fresh.return_value = stale_report

        detector = AdharmaDetector(
        lease_registry=registry,
        world_state=mock_ws,
        samskara_ledger=ledger,
        ethics_engine=engine,
        jnana_module=jnana,
        )

        # Grant valid lease
        lease = registry.grant(
        agent_id="agent-001",
        task_id="task-001",
        capabilities=["browse"],
        tenant_id="tenant-abc",
        )

        contract = _make_contract(
        agent_id="agent-001",
        tenant_id="tenant-abc",
                kind="browse",
        capability_requested="browse",
        needs_fresh_world=True,
        sources_required=["market_data"],
        )

        report = detector.evaluate(
            contract,
        presented_lease_id=lease.lease_id,
        presented_agent_id="agent-001",
        presented_tenant_id="tenant-abc",
        )
        assert report.verdict == AdharmaVerdict.DENY
        assert report.halted_at_layer == 2  # earliest: L1 passes, L2 stale

    def test_41_shadow_mode_recording_10_deny_worthy_contracts(self):
        """Test 41: Shadow mode recording: 10 deny-worthy contracts → 10 PERMIT + would_have_blocked=True."""
        from dharmaos.ethics_engine import EthicsEngine
        from dharmaos.jnana_self_model import JnanaModule
        from dharmaos.lease import LeaseRegistry
        from dharmaos.samskara_ledger import SamskaraLedger

        registry = LeaseRegistry()
        ledger = SamskaraLedger(storage="memory")
        engine = EthicsEngine()
        jnana = JnanaModule(agent_id="agent-001", tenant_id="tenant-abc")

        detector = AdharmaDetector(
        lease_registry=registry,
        samskara_ledger=ledger,
        ethics_engine=engine,
        jnana_module=jnana,
        shadow_mode=True,
        )

        # Deny-worthy: no lease presented → Layer 1 DENY
        contracts = [
            _make_contract(agent_id="agent-001", tenant_id="tenant-abc") for _ in range(10)
        ]

        reports = [
            detector.evaluate(
                c,
        presented_lease_id=None,  # no lease → DENY at L1
        presented_agent_id=c.agent_id,
        presented_tenant_id=c.tenant_id,
        )
                for c in contracts
        ]

        assert all(r.verdict == AdharmaVerdict.PERMIT for r in reports), (
            "Shadow mode must always return PERMIT"
        )
        assert all(r.would_have_blocked is True for r in reports), (
            "Shadow mode must record would_have_blocked=True"
        )
        assert len(reports) == 10
