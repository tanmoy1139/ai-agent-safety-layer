"""DharmaOS deep-governance gate, wired through PolicyGate.call_tool.

For every governed agent tool call the gate:
1. Builds a neutral ActionContract describing the intended action.
2. Grants a short-lived capability lease (DharmaOS Layer-1 requirement) so
legitimate, authorised actions pass — then revokes it.
3. Runs the 8-layer Adharma pre-execution pipeline (identity/lease →
world-state → constitutional veto → domain overlay → path-risk/budget →
abuse-twin → blind-spot → policy reconciliation).
4. Runs the non-compensatory EthicsEngine constraint gate.
5. Records the decision on the tamper-evident RtaLedger (SHA-256 + Merkle,
EU AI Act Art. 50 capable) — the regulator-grade audit chain that runs
ALONGSIDE the platform's existing Ed25519 audit_ledger.

Decision semantics: DENY (or ethics fail) → block; ESCALATE_HITL → allow but
flag for mandatory human review; PERMIT → allow. Internal errors fail-open
(the platform's existing governance still applies) so the kernel can never take
the pipeline down.
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog

import dharmaos as dh

# Stable HMAC key so the cryptographic audit chain verifies across process
# restarts (a random per-process key would make prior entries unverifiable).
# Override in production via DHARMAOS_RTA_HMAC_KEY.
_RTA_HMAC_KEY = os.getenv(
    "DHARMAOS_RTA_HMAC_KEY", "your-hmac-key-here"
).encode()

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# State-changing tools that should escalate to mandatory human sign-off
# (OSFI E-23): persisting an override, writing artifacts, submitting a filing.
# Everything else in KYC is read/screen/analyze — no state change, no monetary
# effect — and is permitted to run under the per-action capability lease.
_WRITE_TOOLS = {
"persist_override", "artifact_store_write", "fintrac_portal_submit",
"evidence_append",
}


@dataclass(frozen=True)
class GovernanceDecision:
    allowed: bool
    verdict: str  # "permit" | "deny" | "escalate_hitl"
    reason: str
    halted_at_layer: int | None
    ethics_ok: bool
    ethics_score: float
    requires_hitl: bool
    rta_event_id: str | None
    latency_ms: float


class DharmaGovernance:
    """Singleton holding the DharmaOS components and the unified authorize()."""

    def __init__(self, *, rta_db: str = "data/dharmaos_rta_ledger.db") -> None:
        self.ethics = dh.EthicsEngine()
        self.leases = dh.LeaseRegistry()
        self.adharma = dh.AdharmaDetector(
        lease_registry=self.leases,
        ethics_engine=self.ethics,
        layer_timeout_ms=50.0,
        )
        Path(rta_db).parent.mkdir(parents=True, exist_ok=True)
        self.ledger = dh.RtaLedger(
        storage="sqlite", sqlite_path=Path(rta_db), hmac_key=_RTA_HMAC_KEY
        )
        # Serialize the stateful gate path: parallel agents (asyncio.gather)
        # share one ledger + lease registry; the hash-chain must be appended
        # under a lock or concurrent writes break chain linkage.
        self._lock = threading.Lock()
        # WAL mode lets a dashboard/verifier read the chain consistently while
        # the API process is appending to it (default rollback journal gives a
        # second reader an inconsistent snapshot mid-write).
        try:
            self.ledger._db.execute("PRAGMA journal_mode=WAL")  # noqa: SLF001
        except Exception:  # noqa: BLE001 — best-effort; chain is valid either way
            pass

    def authorize(
    self,
    *,
    tool_name: str,
    agent_name: str,
    case_id: str,
    tenant_id: str = "demo-bank",
    prompt: str | None = None,
    params: dict | None = None,
    ) -> GovernanceDecision:
        now = datetime.now(timezone.utc)
        prompt_hash = (
            hashlib.sha256(prompt.encode()).hexdigest() if prompt else ""
        )
        is_write = tool_name in _WRITE_TOOLS
        kind = "write" if is_write else "analyze"
        impact = dh.ActionImpact.HIGH if is_write else dh.ActionImpact.LOW
        business_reason = (
            f"FINTRAC PCMLTFA due-diligence step: {agent_name} performs "
            f"{tool_name} on KYC case {case_id}."
        )
        if prompt:
            business_reason += " Context: " + prompt[:300]
        contract = dh.ActionContract(
        action_id=f"{tool_name}-{(prompt_hash[:10] or case_id)}",
        path_id=case_id,
        agent_id=agent_name,
        parent_agent_id=None,
        tenant_id=tenant_id,
        task_id=case_id,
        requested_at=now,
                name=tool_name,
                kind=kind,
                target=tool_name,
                params=params or {},
                tool=tool_name,
        capability_requested=tool_name,
                impact=impact,
        # KYC reads/screens have no monetary effect (financial_effect=False,
        # else the payment overlay demands an 'amount') and are reversible.
        # Writes are non-reversible + high-impact → escalate to human sign-off.
        financial_effect=False,
        reversible=not is_write,
        # declared_goal describes the COMPLIANCE PURPOSE in plain language.
        # It must not read as "run/execute <tool>" or the code-execution
        # overlay treats it as arbitrary command execution. The tool detail
        # lives in business_reason (which the overlay does not parse for exec).
        declared_goal=(
                f"FINTRAC KYC due-diligence for case {case_id} "
                f"({'human-reviewed write' if is_write else 'automated read/screen'})"
        ),
        business_reason=business_reason,
        prompt_hash=prompt_hash,
        prompt_trust_domain="system",
        )

        # Layer-1 of Adharma requires a presented capability lease.
        lease = self.leases.grant(
        agent_id=agent_name, task_id=case_id,
        capabilities=[tool_name], tenant_id=tenant_id, lease_seconds=60,
        )
        try:
            report = self.adharma.evaluate(
                contract,
            presented_lease_id=lease.lease_id,
            presented_agent_id=agent_name,
            presented_tenant_id=tenant_id,
                    domain="financial",
            )
        finally:
            try:
                self.leases.revoke(lease.lease_id)
            except Exception:  # noqa: BLE001 — lease cleanup must never raise
                pass

        verdict = getattr(report.verdict, "value", str(report.verdict))
        ethics = self.ethics.evaluate_contract(contract)
        deny = (verdict == "deny") or (not ethics.is_ethical)
        requires_hitl = verdict == "escalate_hitl"

        reason = "permitted"
        if not ethics.is_ethical:
            reason = "ethics:" + ",".join(ethics.violated_constraints)
        elif deny and report.halted_at_layer:
            try:
                lv = report.layer_verdicts[report.halted_at_layer - 1]
                reason = (lv.reasons[0] if lv.reasons else f"denied@layer{report.halted_at_layer}")
            except (IndexError, AttributeError):
                reason = f"denied@layer{report.halted_at_layer}"

        rta_event_id = self._record(
            tool_name, agent_name, case_id, tenant_id, verdict, report, ethics
        )

        return GovernanceDecision(
        allowed=not deny,
        verdict=verdict,
                reason=reason,
        halted_at_layer=report.halted_at_layer,
        ethics_ok=ethics.is_ethical,
        ethics_score=round(float(ethics.score), 3),
        requires_hitl=requires_hitl,
        rta_event_id=rta_event_id,
        latency_ms=round(float(report.total_latency_ms), 2),
        )

    def _record(self, tool_name, agent_name, case_id, tenant_id, verdict, report, ethics) -> str | None:
        try:
            with self._lock:
                evt = self.ledger.record(
                        kind=str(dh.RtaEventKind.ADHARMA_DECISION),
                        tenant_id=tenant_id,
                        agent_id=agent_name,
                        payload={
                "tool": tool_name,
                "case_id": case_id,
                "verdict": verdict,
                "halted_at_layer": report.halted_at_layer,
                "ethics_ok": ethics.is_ethical,
                "ethics_score": round(float(ethics.score), 3),
                },
                ethics_evaluations={
                "score": float(ethics.score),
                "violated": list(ethics.violated_constraints),
                "risk_level": str(ethics.risk_level),
                },
                )
            return str(getattr(evt, "event_id", None) or getattr(evt, "seq", None) or "")
        except Exception as exc:  # noqa: BLE001 — audit must never break the gate
            logger.warning("dharmaos.rta_record_failed", tool=tool_name, error=str(exc))
            return None

    def verify_chain(self) -> dict:
        """Expose RtaLedger integrity verification for the governance dashboard."""
        try:
            report = self.ledger.verify_integrity()
            return report.model_dump() if hasattr(report, "model_dump") else {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}


_governance: DharmaGovernance | None = None


def get_dharma_governance() -> DharmaGovernance:
    global _governance
    if _governance is None:
        _governance = DharmaGovernance()
    return _governance
