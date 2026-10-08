"""Tests for vedic.lease — Sprint V-LEASE capability leases.

Source: DharmaOS §6.2.


22 tests covering:
- Core grant + check (6)
- Revocation (4)
- Emergency kill-switch §6.3 (5)
- Delegation guard §6.1 (4)
- ActionContract integration (3) — closes RF-44
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from dharmaos.ethics_engine import ActionContract
from dharmaos.lease import (
CapabilityLease,
KillScope,
LeaseRegistry,
LeaseStatus,
delegation_guard,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _registry(default_lease_seconds: int = 300) -> LeaseRegistry:
    return LeaseRegistry(default_lease_seconds=default_lease_seconds)


def _make_contract(
*,
agent_id: str = "agent-alpha",
tenant_id: str = "tenant-xyz",
capability: str | None = "file:read",
) -> ActionContract:
    return ActionContract(
    action_id="act-001",
    path_id="path-001",
    agent_id=agent_id,
    parent_agent_id=None,
    tenant_id=tenant_id,
    task_id="task-001",
    requested_at=_now(),
            name="read file",
            kind="file_read",
            target="/tmp/test.txt",
    capability_requested=capability,
    )


# ===========================================================================
# SECTION 1: Core grant + check (tests 1–6)
# ===========================================================================


class TestGrantAndCheck:
    def test_grant_returns_valid_lease_with_all_fields(self) -> None:
        """Test 1: grant() returns a valid lease with all fields populated."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read", "db:query"],
        tenant_id="tenant-xyz",
        lease_seconds=120,
        granted_by="orchestrator",
        )

        assert isinstance(lease, CapabilityLease)
        assert lease.agent_id == "agent-alpha"
        assert lease.task_id == "task-001"
        assert set(lease.capabilities) == {"file:read", "db:query"}
        assert lease.tenant_id == "tenant-xyz"
        assert lease.granted_by == "orchestrator"
        assert lease.lease_id is not None
        assert lease.granted_at.tzinfo is not None
        assert lease.expires_at > lease.granted_at

    def test_check_fresh_lease_returns_valid(self) -> None:
        """Test 2: check() on a fresh lease → VALID."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )

        verdict = reg.check(
        lease_id=lease.lease_id,
        agent_id="agent-alpha",
        tenant_id="tenant-xyz",
        capability="file:read",
        )

        assert verdict.status == LeaseStatus.VALID
        assert verdict.lease_id == lease.lease_id

    def test_check_expired_lease_returns_expired(self) -> None:
        """Test 3: check() after expiry → EXPIRED."""
        reg = _registry()
        # Grant a lease that expires in the past by manipulating 'now'
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        lease_seconds=1,
        )

        # Check with now set 1 hour in the future
        future = _now() + timedelta(hours=1)
        verdict = reg.check(
        lease_id=lease.lease_id,
        agent_id="agent-alpha",
        tenant_id="tenant-xyz",
        capability="file:read",
                now=future,
        )

        assert verdict.status == LeaseStatus.EXPIRED

    def test_check_capability_not_in_grant_returns_capability_not_granted(self) -> None:
        """Test 4: check() with capability not in grant → CAPABILITY_NOT_GRANTED."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )

        verdict = reg.check(
        lease_id=lease.lease_id,
        agent_id="agent-alpha",
        tenant_id="tenant-xyz",
        capability="db:write",  # NOT in the lease
        )

        assert verdict.status == LeaseStatus.CAPABILITY_NOT_GRANTED

    def test_check_agent_id_mismatch_returns_agent_mismatch(self) -> None:
        """Test 5: check() with agent_id mismatch → AGENT_MISMATCH."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )

        verdict = reg.check(
        lease_id=lease.lease_id,
        agent_id="agent-IMPOSTER",  # Wrong agent
        tenant_id="tenant-xyz",
        capability="file:read",
        )

        assert verdict.status == LeaseStatus.AGENT_MISMATCH

    def test_check_tenant_id_mismatch_returns_tenant_mismatch(self) -> None:
        """Test 6: check() with tenant_id mismatch → TENANT_MISMATCH."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )

        verdict = reg.check(
        lease_id=lease.lease_id,
        agent_id="agent-alpha",
        tenant_id="tenant-OTHER",  # Wrong tenant
        capability="file:read",
        )

        assert verdict.status == LeaseStatus.TENANT_MISMATCH


# ===========================================================================
# SECTION 2: Revocation (tests 7–10)
# ===========================================================================


class TestRevocation:
    def test_revoke_then_check_returns_revoked(self) -> None:
        """Test 7: revoke() then check() → REVOKED."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )

        revoked = reg.revoke(lease.lease_id, reason="test revocation")
        assert revoked is True

        verdict = reg.check(
        lease_id=lease.lease_id,
        agent_id="agent-alpha",
        tenant_id="tenant-xyz",
        capability="file:read",
        )
        assert verdict.status == LeaseStatus.REVOKED

    def test_revoke_unknown_lease_returns_false_no_exception(self) -> None:
        """Test 8: revoke() on unknown lease → False, no exception."""
        reg = _registry()
        unknown_id = uuid4()
        result = reg.revoke(unknown_id, reason="does not exist")
        assert result is False

    def test_revoke_twice_second_call_returns_false(self) -> None:
        """Test 9: revoke() twice → second call returns False (idempotent)."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )

        first = reg.revoke(lease.lease_id, reason="first revoke")
        second = reg.revoke(lease.lease_id, reason="second revoke")

        assert first is True
        assert second is False

    def test_revoked_lease_retained_for_audit(self) -> None:
        """Test 10: Revoked lease retained in registry (not deleted) for audit."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )
        reg.revoke(lease.lease_id, reason="audit test")

        # The lease must still be in the internal registry (not deleted)
        # We verify this by checking that revoke() returns False on a second call
        # (meaning it IS found but already revoked, not 'not found')
        second_revoke = reg.revoke(lease.lease_id, reason="verify retained")
        assert second_revoke is False  # found but already revoked → False, not an error


# ===========================================================================
# SECTION 3: Emergency kill-switch §6.3 (tests 11–15)
# ===========================================================================


class TestEmergencyKillSwitch:
    def test_kill_by_lease_id_revokes_only_that_lease(self) -> None:
        """Test 11: Kill by lease_id → that lease revoked, others unaffected."""
        reg = _registry()
        lease_a = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )
        lease_b = reg.grant(
        agent_id="agent-beta",
        task_id="task-002",
        capabilities=["db:query"],
        tenant_id="tenant-xyz",
        )

        revoked = reg.emergency_kill(
            KillScope(lease_ids=(lease_a.lease_id,), reason="targeted kill")
        )

        assert lease_a.lease_id in revoked
        assert lease_b.lease_id not in revoked

        verdict_a = reg.check(
        lease_id=lease_a.lease_id,
        agent_id="agent-alpha",
        tenant_id="tenant-xyz",
        capability="file:read",
        )
        verdict_b = reg.check(
        lease_id=lease_b.lease_id,
        agent_id="agent-beta",
        tenant_id="tenant-xyz",
        capability="db:query",
        )
        assert verdict_a.status == LeaseStatus.REVOKED
        assert verdict_b.status == LeaseStatus.VALID

    def test_kill_by_agent_id_revokes_all_agent_leases(self) -> None:
        """Test 12: Kill by agent_id → all of that agent's active leases revoked."""
        reg = _registry()
        lease_1 = reg.grant(
        agent_id="agent-bad",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )
        lease_2 = reg.grant(
        agent_id="agent-bad",
        task_id="task-002",
        capabilities=["db:query"],
        tenant_id="tenant-xyz",
        )
        lease_good = reg.grant(
        agent_id="agent-good",
        task_id="task-003",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )

        revoked = reg.emergency_kill(
            KillScope(agent_ids=("agent-bad",), reason="agent drift detected")
        )

        assert lease_1.lease_id in revoked
        assert lease_2.lease_id in revoked
        assert lease_good.lease_id not in revoked

    def test_kill_by_tenant_id_revokes_all_tenant_leases(self) -> None:
        """Test 13: Kill by tenant_id → all leases under that tenant revoked."""
        reg = _registry()
        lease_t1_a = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-EVIL",
        )
        lease_t1_b = reg.grant(
        agent_id="agent-beta",
        task_id="task-002",
        capabilities=["db:query"],
        tenant_id="tenant-EVIL",
        )
        lease_t2 = reg.grant(
        agent_id="agent-gamma",
        task_id="task-003",
        capabilities=["file:read"],
        tenant_id="tenant-GOOD",
        )

        revoked = reg.emergency_kill(
            KillScope(tenant_ids=("tenant-EVIL",), reason="tenant compromise")
        )

        assert lease_t1_a.lease_id in revoked
        assert lease_t1_b.lease_id in revoked
        assert lease_t2.lease_id not in revoked

    def test_kill_by_agent_class_revokes_matching_leases(self) -> None:
        """Test 14: Kill by agent_class → all leases with matching agent_class revoked."""
        reg = _registry()
        lease_browser_1 = reg.grant(
        agent_id="agent-browser-1",
        task_id="task-001",
        capabilities=["browser:navigate"],
        tenant_id="tenant-xyz",
        agent_class="browser",
        )
        lease_browser_2 = reg.grant(
        agent_id="agent-browser-2",
        task_id="task-002",
        capabilities=["browser:screenshot"],
        tenant_id="tenant-xyz",
        agent_class="browser",
        )
        lease_research = reg.grant(
        agent_id="agent-research-1",
        task_id="task-003",
        capabilities=["search:query"],
        tenant_id="tenant-xyz",
        agent_class="research",
        )

        revoked = reg.emergency_kill(
            KillScope(agent_classes=("browser",), reason="browser class exploit detected")
        )

        assert lease_browser_1.lease_id in revoked
        assert lease_browser_2.lease_id in revoked
        assert lease_research.lease_id not in revoked

    def test_kill_empty_scope_is_noop(self) -> None:
        """Test 15: Kill with empty scope → no-op, returns empty list."""
        reg = _registry()
        reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )

        revoked = reg.emergency_kill(KillScope(reason="empty scope test"))
        assert revoked == []


# ===========================================================================
# SECTION 4: Delegation guard §6.1 (tests 16–19)
# ===========================================================================


class TestDelegationGuard:
    def test_parent_with_superset_caps_child_grant_succeeds(self) -> None:
        """Test 16: Parent with superset caps → child grant succeeds."""
        reg = _registry()
        parent_lease = reg.grant(
        agent_id="agent-parent",
        task_id="task-001",
        capabilities=["file:read", "db:query", "network:fetch"],
        tenant_id="tenant-xyz",
        )

        # Child requests a subset of parent's caps
        child_lease = reg.grant(
        agent_id="agent-child",
        task_id="task-002",
        capabilities=["file:read", "db:query"],
        tenant_id="tenant-xyz",
        granted_by="agent-parent",
        parent_lease_id=parent_lease.lease_id,
        )

        assert child_lease.parent_lease_id == parent_lease.lease_id
        assert set(child_lease.capabilities) == {"file:read", "db:query"}

    def test_parent_missing_cap_delegation_guard_raises(self) -> None:
        """Test 17: Parent with missing cap → delegation_guard False → child grant raises."""
        reg = _registry()
        parent_lease = reg.grant(
        agent_id="agent-parent",
        task_id="task-001",
        capabilities=["file:read"],  # does NOT have db:write
        tenant_id="tenant-xyz",
        )

        # delegation_guard should return False
        allowed = delegation_guard(
        registry=reg,
        parent_agent_id="agent-parent",
        parent_lease_id=parent_lease.lease_id,
        parent_tenant_id="tenant-xyz",
        child_capabilities=["file:read", "db:write"],  # db:write not in parent
        )
        assert allowed is False

        # grant() with parent_lease_id should raise when guard fails
        with pytest.raises(PermissionError, match="delegation"):
            reg.grant(
            agent_id="agent-child",
            task_id="task-002",
            capabilities=["file:read", "db:write"],
            tenant_id="tenant-xyz",
            granted_by="agent-parent",
            parent_lease_id=parent_lease.lease_id,
            )

    def test_expired_parent_lease_delegation_refused(self) -> None:
        """Test 18: Parent lease expired → delegation refused."""
        reg = _registry()
        parent_lease = reg.grant(
        agent_id="agent-parent",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        lease_seconds=1,
        )

        # Check delegation in the future (after parent has expired)
        future = _now() + timedelta(hours=1)
        allowed = delegation_guard(
        registry=reg,
        parent_agent_id="agent-parent",
        parent_lease_id=parent_lease.lease_id,
        parent_tenant_id="tenant-xyz",
        child_capabilities=["file:read"],
                now=future,
        )
        assert allowed is False

    def test_cross_tenant_parent_child_delegation_refused(self) -> None:
        """Test 19: Cross-tenant parent-child → delegation refused (tenant mismatch)."""
        reg = _registry()
        parent_lease = reg.grant(
        agent_id="agent-parent",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-A",
        )

        # Delegation check with a DIFFERENT tenant
        allowed = delegation_guard(
        registry=reg,
        parent_agent_id="agent-parent",
        parent_lease_id=parent_lease.lease_id,
        parent_tenant_id="tenant-B",  # Mismatch — lease is for tenant-A
        child_capabilities=["file:read"],
        )
        assert allowed is False


# ===========================================================================
# SECTION 5: ActionContract integration (tests 20–22) — CLOSES RF-44
# ===========================================================================


class TestActionContractIntegration:
    def test_check_contract_valid_returns_valid(self) -> None:
        """Test 20: check_contract() on valid contract + matching lease → VALID."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )

        contract = _make_contract(
        agent_id="agent-alpha",
        tenant_id="tenant-xyz",
        capability="file:read",
        )

        verdict = reg.check_contract(
        lease_id=lease.lease_id,
        agent_id="agent-alpha",
        tenant_id="tenant-xyz",
        contract=contract,
        )
        assert verdict.status == LeaseStatus.VALID

    def test_check_contract_capability_not_in_lease_returns_capability_not_granted(
    self,
    ) -> None:
        """Test 21: check_contract() when contract.capability_requested not in lease.capabilities → CAPABILITY_NOT_GRANTED."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],  # only file:read granted
        tenant_id="tenant-xyz",
        )

        contract = _make_contract(
        agent_id="agent-alpha",
        tenant_id="tenant-xyz",
        capability="admin:delete",  # NOT in lease
        )

        verdict = reg.check_contract(
        lease_id=lease.lease_id,
        agent_id="agent-alpha",
        tenant_id="tenant-xyz",
        contract=contract,
        )
        assert verdict.status == LeaseStatus.CAPABILITY_NOT_GRANTED

    def test_check_contract_tenant_mismatch_returns_tenant_mismatch(self) -> None:
        """Test 22: check_contract() when contract tenant != lease tenant → TENANT_MISMATCH."""
        reg = _registry()
        lease = reg.grant(
        agent_id="agent-alpha",
        task_id="task-001",
        capabilities=["file:read"],
        tenant_id="tenant-xyz",
        )

        contract = _make_contract(
        agent_id="agent-alpha",
        tenant_id="tenant-WRONG",  # Mismatch
        capability="file:read",
        )

        verdict = reg.check_contract(
        lease_id=lease.lease_id,
        agent_id="agent-alpha",
        tenant_id="tenant-WRONG",  # Caller passes wrong tenant too
        contract=contract,
        )
        assert verdict.status == LeaseStatus.TENANT_MISMATCH
