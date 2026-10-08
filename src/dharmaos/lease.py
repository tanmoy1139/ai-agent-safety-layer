"""
    DharmaOS LeaseRegistry — short-lived scoped capability leases and emergency kill-switch.

    PURPOSE
    -------
    LeaseRegistry replaces persistent always-on agent privileges with short-lived,
    task-scoped, time-bound capability leases. Before any agent may invoke a tool
    or take an action, it must present a valid lease that explicitly grants the
    required capability for the current task, tenant, and agent identity.

    This eliminates the ambient-authority problem: a compromised or misconfigured
    agent can only exercise the exact capabilities it was explicitly granted for the
    current task window, not the full capability set of its role.

    LEASE PROPERTIES
    ----------------
    - Time-bound  : expires_at enforced; expired leases cannot be extended.
    - Capability-bound : explicit list of named capabilities granted.
    - Tenant-bound  : cross-tenant lease presentation is rejected (Layer 1).
    - Agent-bound  : cross-agent lease presentation is rejected (Layer 1).
    - Immutable  : CapabilityLease records are frozen after issuance.
    Revocation is tracked in the registry's revocation set,
    never by mutating the lease record.
    - Audit-persistent : Expired leases remain in the registry for audit;
    expiry ≠ deletion.

    DELEGATION GUARD (§6.1)
    -----------------------
    delegation_guard(parent_lease, child_capabilities) raises CapabilityError
    if the parent lease does not hold ALL capabilities it is trying to delegate.
    An agent cannot grant more authority than it itself possesses.

    EMERGENCY KILL-SWITCH (§6.3)
    -----------------------------
    emergency_kill(scope) revokes by lease_id, agent_id, tenant_id, or
    agent_class. Idempotent. Used on drift/abuse signal from AdharmaDetector
    or GunaMonitor.

    KEY TYPES
    ---------
    LeaseRegistry  : Central authority. grant() → CapabilityLease.
    check_contract() → LeaseVerdict.
    revoke() / emergency_kill().
    CapabilityLease  : Immutable lease record (frozen Pydantic BaseModel).
    LeaseVerdict  : Gate result (status, reason, lease_id).
    LeaseStatus  : VALID / EXPIRED / REVOKED / NOT_FOUND / TENANT_MISMATCH
    / AGENT_MISMATCH / CAPABILITY_NOT_GRANTED.
    KillScope  : LEASE / AGENT / TENANT / AGENT_CLASS enum.
    delegation_guard(): §6.1 enforcement — raises on over-delegation.

    COMPLIANCE ROLE
    ---------------
    - Implements least-privilege principle (NIST SP 800-207 Zero Trust).
    - AdharmaDetector Layer 1 uses check_contract() as the lease gate.
    - EU AI Act Art. 14 (human oversight): emergency_kill() is the human
    override mechanism for immediate capability revocation.

    Governance origin: DharmaOS §6.2 (docs/vedic/engineering/
    DHARMAOS-ARCHITECTURE-V1.md). Short-lived scoped permits mirror the
    concept of task-specific dharmic authority: an agent acts within
    the scope of its current sanctioned role, no more.

    Capability leases — short-lived scoped permits per task.

    Source: DharmaOS §6.2.


    Replaces persistent privileges with task-scoped leases:
    - Time-bound (expires_at enforced)
    - Capability-bound (list of named capabilities)
    - Tenant-bound (cross-tenant leases rejected)
    - Agent-bound (cross-agent lease-presentation rejected)

    Fleet controls (§6):
    - §6.1 Delegation guard — Agent A cannot delegate a capability to
    Agent B that A itself does not hold.
    - §6.3 Emergency kill-switch — revoke individual lease, agent,
    tenant slice, or entire agent class on drift/abuse signal.

    Key invariants:
    1. Lease records are immutable. Revocation is stored in the registry's
    revocation set — we never mutate a CapabilityLease.
    2. Tenant isolation is strict. A lease issued to tenant=A cannot be
    checked under tenant=B regardless of agent_id or capability.
    3. Delegation guard is non-compensatory. If the parent lacks ANY
    requested capability, the child grant is refused in toto.
    4. Kill-switch is idempotent. Calling emergency_kill twice with the
    same scope is safe.
    5. Expired leases stay in the registry for audit. Expiry ≠ deletion.
    """

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from dharmaos.ethics_engine import ActionContract

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


# ---------------------------------------------------------------------------
# LeaseStatus
# ---------------------------------------------------------------------------


class LeaseStatus(StrEnum):
    """Result codes for lease verification gate."""

    VALID = "valid"
    EXPIRED = "expired"
    REVOKED = "revoked"
    NOT_FOUND = "not_found"
    TENANT_MISMATCH = "tenant_mismatch"
    AGENT_MISMATCH = "agent_mismatch"
    CAPABILITY_NOT_GRANTED = "capability_not_granted"


# ---------------------------------------------------------------------------
# CapabilityLease
# ---------------------------------------------------------------------------


class CapabilityLease(BaseModel):
    """Short-lived scoped permit. Immutable after issuance (revocation
        is tracked separately in LeaseRegistry, never by mutating this record).

        Source: DharmaOS §6.2 (DHARMAOS-ARCHITECTURE-V1.md).
        """

    model_config = ConfigDict(frozen=True)

    lease_id: UUID = Field(default_factory=uuid4)
    agent_id: str
    task_id: str
    capabilities: tuple[str, ...]  # immutable — no mutation allowed
    granted_at: datetime
    expires_at: datetime
    tenant_id: str
    granted_by: str | None = None  # parent agent for delegation chain
    parent_lease_id: UUID | None = None  # for traceable delegation
    agent_class: str | None = None  # for §6.3 kill-switch scope


# ---------------------------------------------------------------------------
# LeaseVerdict
# ---------------------------------------------------------------------------


class LeaseVerdict(BaseModel):
    """Result of a lease verification check.

        Return-value pattern: never raises on authorization failure.
        """

    status: LeaseStatus
    lease_id: UUID | None = None
    capability: str | None = None
    reason: str = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# KillScope
# ---------------------------------------------------------------------------


class KillScope(BaseModel):
    """§6.3 Emergency kill scope.

        Any matching dimension triggers revocation. Empty scope is a no-op.
        """

    model_config = ConfigDict(frozen=True)

    lease_ids: tuple[UUID, ...] = ()
    agent_ids: tuple[str, ...] = ()
    tenant_ids: tuple[str, ...] = ()
    agent_classes: tuple[str, ...] = ()  # e.g., "research", "browser"
    reason: str = ""
    issued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# LeaseRegistry
# ---------------------------------------------------------------------------


class LeaseRegistry:
    """Central authority for lease lifecycle.

        Thread-safety: wrap external access in a lock if used concurrently
        (same pattern as V-WS WorldStateService; production-hardening is
        a Phase 4 concern).

        Invariants:
        - Lease records are immutable (CapabilityLease.model_config frozen=True).
        - Revocation is stored in _revoked_ids (set of UUIDs).
        - Expired leases are NEVER deleted — they stay for audit.
        - Tenant and agent isolation enforced at check() time.
        """

    def __init__(self, *, default_lease_seconds: int = 300) -> None:
        self._default_lease_seconds = default_lease_seconds
        # lease_id → CapabilityLease (append-only; never delete)
        self._leases: dict[UUID, CapabilityLease] = {}
        # Set of revoked lease_ids (tracking separate from lease records)
        self._revoked_ids: set[UUID] = set()

        logger.info(
            "lease_registry.init",
            default_lease_seconds=default_lease_seconds,
        )

    # ------------------------------------------------------------------
    # grant
    # ------------------------------------------------------------------

    def grant(
        self,
        *,
        agent_id: str,
        task_id: str,
        capabilities: Iterable[str],
        tenant_id: str,
        lease_seconds: int | None = None,
        granted_by: str | None = None,
        parent_lease_id: UUID | None = None,
        agent_class: str | None = None,
    ) -> CapabilityLease:
        """Issue a new lease.

            If granted_by + parent_lease_id supplied, runs delegation_guard
            (§6.1) — parent must hold a superset of the requested capabilities
            under an active non-expired lease.

            Args:
            agent_id: The agent this lease is issued to.
            task_id: The task this lease is scoped to.
            capabilities: Named capabilities being granted.
            tenant_id: Tenant this lease belongs to.
            lease_seconds: Validity duration; defaults to registry default.
            granted_by: Parent agent ID when delegating.
            parent_lease_id: Parent lease UUID when delegating.
            agent_class: Class label for §6.3 kill-switch (e.g. "browser").

            Returns:
            CapabilityLease — immutable, stored in registry.

            Raises:
            PermissionError: If delegation guard refuses the grant.
            """
        caps_list = list(capabilities)

        # §6.1 Delegation guard — run before issuing the child lease
        if parent_lease_id is not None and granted_by is not None:
            allowed = delegation_guard(
                registry=self,
                parent_agent_id=granted_by,
                parent_lease_id=parent_lease_id,
                parent_tenant_id=tenant_id,
                child_capabilities=caps_list,
            )
            if not allowed:
                raise PermissionError(
                    f"delegation guard refused: parent lease {parent_lease_id} does not "
                    f"hold a superset of requested capabilities {caps_list}. "
                    "Per DharmaOS §6.1: Agent A cannot delegate more than A holds."
                )

        now = datetime.now(UTC)
        seconds = lease_seconds if lease_seconds is not None else self._default_lease_seconds
        expires_at = now + timedelta(seconds=seconds)
        # SEC H-5: a delegated child lease must never outlive its parent — clamp
        # its expiry so revoking/killing the parent reliably bounds the child too.
        if parent_lease_id is not None:
            parent_lease = self._leases.get(parent_lease_id)
            if parent_lease is not None and parent_lease.expires_at < expires_at:
                expires_at = parent_lease.expires_at

        lease = CapabilityLease(
            agent_id=agent_id,
            task_id=task_id,
            capabilities=tuple(caps_list),
            granted_at=now,
            expires_at=expires_at,
            tenant_id=tenant_id,
            granted_by=granted_by,
            parent_lease_id=parent_lease_id,
            agent_class=agent_class,
        )

        self._leases[lease.lease_id] = lease

        logger.info(
            "lease_registry.grant",
            lease_id=str(lease.lease_id),
            agent_id=agent_id,
            task_id=task_id,
            tenant_id=tenant_id,
            capabilities=caps_list,
            expires_at=expires_at.isoformat(),
        )

        return lease

    # ------------------------------------------------------------------
    # check
    # ------------------------------------------------------------------

    def check(
        self,
        *,
        lease_id: UUID,
        agent_id: str,
        tenant_id: str,
        capability: str,
        now: datetime | None = None,
    ) -> LeaseVerdict:
        """V11 Layer 1 gate: verify presented lease is valid AND grants
            the requested capability AND matches agent + tenant AND is not
            revoked AND is not expired.

            Check order (fail-fast, most-privileged-information-first):
            1. NOT_FOUND  — lease_id not in registry
            2. AGENT_MISMATCH — agent_id doesn't match lease.agent_id
            3. TENANT_MISMATCH — tenant_id doesn't match lease.tenant_id
            4. REVOKED  — lease_id in revoked set
            5. EXPIRED  — current time >= expires_at
            6. CAPABILITY_NOT_GRANTED — capability not in lease.capabilities
            7. VALID

            Returns LeaseVerdict — never raises on authorization failure.
            """
        _now = now if now is not None else datetime.now(UTC)

        lease = self._leases.get(lease_id)
        if lease is None:
            return LeaseVerdict(
                status=LeaseStatus.NOT_FOUND,
                lease_id=lease_id,
                capability=capability,
                reason=f"lease {lease_id} not found in registry",
            )

        # Agent-isolation check (before tenant to avoid tenant info leak)
        if lease.agent_id != agent_id:
            return LeaseVerdict(
                status=LeaseStatus.AGENT_MISMATCH,
                lease_id=lease_id,
                capability=capability,
                reason=(f"lease was issued to agent '{lease.agent_id}', not '{agent_id}'"),
            )

        # Tenant-isolation (strict: lease.tenant_id must equal tenant_id)
        if lease.tenant_id != tenant_id:
            return LeaseVerdict(
                status=LeaseStatus.TENANT_MISMATCH,
                lease_id=lease_id,
                capability=capability,
                reason=(f"lease belongs to tenant '{lease.tenant_id}', not '{tenant_id}'"),
            )

        # Revocation check
        if lease_id in self._revoked_ids:
            return LeaseVerdict(
                status=LeaseStatus.REVOKED,
                lease_id=lease_id,
                capability=capability,
                reason=f"lease {lease_id} has been revoked",
            )

        # Expiry check
        if _now >= lease.expires_at:
            return LeaseVerdict(
                status=LeaseStatus.EXPIRED,
                lease_id=lease_id,
                capability=capability,
                reason=(
                f"lease expired at {lease.expires_at.isoformat()}, "
                f"current time is {_now.isoformat()}"
            ),
            )

        # Capability check
        if capability not in lease.capabilities:
            return LeaseVerdict(
                status=LeaseStatus.CAPABILITY_NOT_GRANTED,
                lease_id=lease_id,
                capability=capability,
                reason=(f"capability '{capability}' not in lease grant {list(lease.capabilities)}"),
            )

        return LeaseVerdict(
            status=LeaseStatus.VALID,
            lease_id=lease_id,
            capability=capability,
            reason="lease valid",
        )

    # ------------------------------------------------------------------
    # check_contract
    # ------------------------------------------------------------------

    def check_contract(
        self,
        *,
        lease_id: UUID,
        agent_id: str,
        tenant_id: str,
        contract: ActionContract,
    ) -> LeaseVerdict:
        """Convenience: check using contract.capability_requested.

            If contract.capability_requested is None, returns CAPABILITY_NOT_GRANTED
            (no capability declared means no capability can be granted).

            Args:
            lease_id: The lease to verify.
            agent_id: Presenting agent's ID.
            tenant_id: Presenting agent's tenant.
            contract: ActionContract with capability_requested field.

            Returns:
            LeaseVerdict.
            """
        capability = contract.capability_requested
        if capability is None:
            return LeaseVerdict(
                status=LeaseStatus.CAPABILITY_NOT_GRANTED,
                lease_id=lease_id,
                capability=None,
                reason="ActionContract.capability_requested is None",
            )

        return self.check(
            lease_id=lease_id,
            agent_id=agent_id,
            tenant_id=tenant_id,
            capability=capability,
        )

    # ------------------------------------------------------------------
    # revoke
    # ------------------------------------------------------------------

    def revoke(self, lease_id: UUID, *, reason: str = "") -> bool:
        """Single-lease revocation.

            Args:
            lease_id: The lease to revoke.
            reason: Optional human-readable reason for audit.

            Returns:
            True if the lease was found and newly revoked.
            False if already revoked or not found (idempotent).
            """
        if lease_id not in self._leases:
            logger.warning(
                "lease_registry.revoke_not_found",
                lease_id=str(lease_id),
                reason=reason,
            )
            return False

        if lease_id in self._revoked_ids:
            logger.debug(
                "lease_registry.revoke_already_revoked",
                lease_id=str(lease_id),
                reason=reason,
            )
            return False

        self._revoked_ids.add(lease_id)
        cascaded = self._cascade_revoke_descendants(reason=reason)
        logger.info(
            "lease_registry.revoked",
            lease_id=str(lease_id),
            reason=reason,
            cascaded=len(cascaded),
        )
        return True

    def _cascade_revoke_descendants(self, *, reason: str) -> list[UUID]:
        """SEC H-5: revoke every descendant of an already-revoked lease so a kill
            propagates down the delegation chain — a delegated child must not outlive
            the revocation/kill of its parent."""
        cascaded: list[UUID] = []
        changed = True
        while changed:
            changed = False
            for lid, lease in self._leases.items():
                if lid in self._revoked_ids:
                    continue
                if lease.parent_lease_id is not None and lease.parent_lease_id in self._revoked_ids:
                    self._revoked_ids.add(lid)
                    cascaded.append(lid)
                    changed = True
        if cascaded:
            logger.warning(
                "lease_registry.revoke_cascade",
                count=len(cascaded), reason=reason,
                lease_ids=[str(lid) for lid in cascaded],
            )
        return cascaded

    # ------------------------------------------------------------------
    # emergency_kill
    # ------------------------------------------------------------------

    def emergency_kill(self, scope: KillScope) -> list[UUID]:
        """§6.3 kill-switch. Revokes every lease matching any of:
            - lease_id in scope.lease_ids
            - agent_id in scope.agent_ids
            - tenant_id in scope.tenant_ids
            - agent_class in scope.agent_classes

            Returns revoked lease_ids. Idempotent — already-revoked leases
            are not re-processed.

            Args:
            scope: KillScope defining what to revoke and why.

            Returns:
            List of newly-revoked lease UUIDs.
            """
        newly_revoked: list[UUID] = []

        for lease_id, lease in self._leases.items():
            # Skip already-revoked leases (idempotent)
            if lease_id in self._revoked_ids:
                continue

            matched = False

            if (
                (scope.lease_ids and lease_id in scope.lease_ids)
                or (scope.agent_ids and lease.agent_id in scope.agent_ids)
                or (scope.tenant_ids and lease.tenant_id in scope.tenant_ids)
                or (scope.agent_classes and lease.agent_class in scope.agent_classes)
            ):
                matched = True

            if matched:
                self._revoked_ids.add(lease_id)
                newly_revoked.append(lease_id)

        # SEC H-5: killing an agent/lease must also kill leases it delegated.
        cascaded = self._cascade_revoke_descendants(reason=scope.reason)
        newly_revoked.extend(cascaded)

        if newly_revoked:
            logger.warning(
                "lease_registry.emergency_kill",
                count=len(newly_revoked),
                reason=scope.reason,
                lease_ids=[str(lid) for lid in newly_revoked],
            )
        else:
            logger.debug(
                "lease_registry.emergency_kill_noop",
                reason=scope.reason,
            )

        return newly_revoked

    # ------------------------------------------------------------------
    # list_active
    # ------------------------------------------------------------------

    def list_active(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        now: datetime | None = None,
    ) -> list[CapabilityLease]:
        """List non-expired, non-revoked leases, optionally filtered.

            Args:
            agent_id: If provided, filter by agent.
            tenant_id: If provided, filter by tenant.
            now: Reference time (defaults to current UTC).

            Returns:
            List of active CapabilityLease records.
            """
        _now = now if now is not None else datetime.now(UTC)

        result: list[CapabilityLease] = []
        for lease_id, lease in self._leases.items():
            if lease_id in self._revoked_ids:
                continue
            if _now >= lease.expires_at:
                continue
            if agent_id is not None and lease.agent_id != agent_id:
                continue
            if tenant_id is not None and lease.tenant_id != tenant_id:
                continue
            result.append(lease)

        return result


# ---------------------------------------------------------------------------
# delegation_guard
# ---------------------------------------------------------------------------


def delegation_guard(
    *,
    registry: LeaseRegistry,
    parent_agent_id: str,
    parent_lease_id: UUID,
    parent_tenant_id: str,
    child_capabilities: Iterable[str],
    now: datetime | None = None,
) -> bool:
    """§6.1 Agent A cannot delegate more than A holds.

        Returns True iff the parent's active non-expired non-revoked lease
        on parent_tenant_id holds a superset of child_capabilities.

        Args:
        registry: The authoritative LeaseRegistry.
        parent_agent_id: The delegating agent's ID.
        parent_lease_id: The parent's lease UUID.
        parent_tenant_id: The tenant context for this delegation.
        child_capabilities: Capabilities the child agent is requesting.
        now: Reference time (defaults to current UTC).

        Returns:
        True if delegation is permitted (parent holds superset).
        False if parent is missing any requested capability, or parent
        lease is expired, revoked, not found, or tenant-mismatched.
        """
    child_caps = set(child_capabilities)

    _now = now if now is not None else datetime.now(UTC)

    # Retrieve the parent lease directly from registry internals
    # to perform the superset check against all capabilities at once.
    parent_lease = registry._leases.get(parent_lease_id)

    if parent_lease is None:
        logger.warning(
            "delegation_guard.parent_lease_not_found",
            parent_lease_id=str(parent_lease_id),
        )
        return False

    # Agent-isolation
    if parent_lease.agent_id != parent_agent_id:
        logger.warning(
            "delegation_guard.agent_mismatch",
            expected=parent_lease.agent_id,
            got=parent_agent_id,
        )
        return False

    # Tenant-isolation (strict)
    if parent_lease.tenant_id != parent_tenant_id:
        logger.warning(
            "delegation_guard.tenant_mismatch",
            lease_tenant=parent_lease.tenant_id,
            requested_tenant=parent_tenant_id,
        )
        return False

    # Check revocation status
    if parent_lease_id in registry._revoked_ids:
        logger.warning(
            "delegation_guard.parent_lease_revoked",
            parent_lease_id=str(parent_lease_id),
        )
        return False

    # Check expiry
    if _now >= parent_lease.expires_at:
        logger.warning(
            "delegation_guard.parent_lease_expired",
            parent_lease_id=str(parent_lease_id),
            expired_at=parent_lease.expires_at.isoformat(),
        )
        return False

    # Superset check — non-compensatory: ALL child caps must be in parent
    parent_caps = set(parent_lease.capabilities)
    missing = child_caps - parent_caps
    if missing:
        logger.warning(
            "delegation_guard.missing_capabilities",
            parent_lease_id=str(parent_lease_id),
            missing=list(missing),
        )
        return False

    logger.debug(
        "delegation_guard.permitted",
        parent_lease_id=str(parent_lease_id),
        child_capabilities=list(child_caps),
    )
    return True
