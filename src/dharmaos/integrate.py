"""DharmaOS — drop-in enterprise integration SDK.

    The 28-module DharmaOS kernel is powerful but, wired by hand, requires assembling
    EthicsEngine + AdharmaDetector + LeaseRegistry + RtaLedger + TrustDomainTagger +
    MemoryValidator and calibrating ActionContract fields correctly (a real source of
    trial-and-error: financial_effect must be False for non-payment actions, the
    declared_goal must read as a business purpose not "run <tool>", a capability
    lease must be granted before the 8-layer gate, the audit DB needs a stable HMAC
    key + WAL for cross-process verification). This module bundles all of that behind
    a single object so any team can wire governance into any agent framework in a few
    lines, with enterprise defaults that make legitimate read/analyze actions PERMIT
    and state-changing writes escalate to human sign-off.

    Quickstart
    ----------
    from dharmaos.integrate import DharmaOSKernel, GovernanceDenied

    gov = DharmaOSKernel(tenant="acme-bank")  # one object, all governance

    # Gate any agent action (raises GovernanceDenied on a hard block):
    decision = gov.authorize(
    agent="screening_agent", action="sanctions_screen",
    goal="Screen the customer party against sanctions and PEP lists",
    case_id="C-1001",
)
    if decision.allowed:
    result = run_the_action()
    gov.record_output(agent="screening_agent", case_id="C-1001",
    prompt="...", output=result)

    # Or wrap a callable in one line:
    screen = gov.governed(screen_fn, agent="screening_agent", action="sanctions_screen")

    # Verify the tamper-evident audit chain (in-process):
    assert gov.verify_audit()["ok"]

    Framework adapters (LangGraph node, FastAPI dependency, CrewAI tool) are thin
    wrappers over `authorize()` / `governed()` — see docs/INTEGRATION-GUIDE.md.
    """

from __future__ import annotations

import functools
import hashlib
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import structlog

import dharmaos as dh

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# Capabilities that change state and therefore default to human sign-off
# (OSFI E-23 / SR 11-7). Override via DharmaOSKernel(write_capabilities=...).
_DEFAULT_WRITE_CAPABILITIES: frozenset[str] = frozenset({
    "persist", "write", "submit", "file_report", "override", "approve",
    "execute_transfer", "delete", "store",
})


class GovernanceDenied(Exception):
    """Raised by `authorize(..., raise_on_deny=True)` / `governed()` on a hard block."""

    def __init__(self, decision: "GovernedDecision") -> None:
        super().__init__(f"DharmaOS denied '{decision.action}': {decision.reason}")
        self.decision = decision


@dataclass(frozen=True)
class GovernedDecision:
    """Outcome of a governance evaluation — carries its own evidence + provenance."""

    allowed: bool
    verdict: str  # "permit" | "deny" | "escalate_hitl"
    action: str
    agent: str
    case_id: str
    reason: str
    requires_hitl: bool
    halted_at_layer: int | None
    ethics_ok: bool
    ethics_score: float
    audit_event_id: str | None
    latency_ms: float


class DharmaOSKernel:
    """One object that bundles the full DharmaOS governance kernel.

        Thread-safe: the stateful audit/lease path is serialized so parallel agents
        (asyncio.gather / threads) cannot corrupt the hash-chain. Fail-CLOSED on an
        explicit governance DENY; the caller decides fail-open vs fail-closed on
        internal errors via `authorize(..., fail_open=...)`.
        """

    def __init__(
        self,
        tenant: str = "default",
        *,
        audit_db: str | os.PathLike[str] | None = "data/dharmaos_audit.db",
        audit_hmac_key: bytes | None = None,
        write_capabilities: Iterable[str] | None = None,
        domain: str = "financial",
        ethics_brahmacarya_threshold: float = 10.0,
        lease_seconds: int = 60,
    ) -> None:
        self.tenant = tenant
        self.domain = domain
        self.lease_seconds = lease_seconds
        self._write_caps = frozenset(write_capabilities) if write_capabilities is not None else _DEFAULT_WRITE_CAPABILITIES
        self.ethics = dh.EthicsEngine(brahmacarya_threshold=ethics_brahmacarya_threshold)
        self.leases = dh.LeaseRegistry()
        self.adharma = dh.AdharmaDetector(
            lease_registry=self.leases, ethics_engine=self.ethics, layer_timeout_ms=50.0,
        )
        # Audit HMAC key: explicit > env var > ephemeral random (warned).
        # Never fall back to a predictable default — a guessable key lets an
        # attacker forge audit chain entries, defeating tamper-evidence.
        # In production (DHARMAOS_ENV=production), a key is required.
        key = audit_hmac_key
        if key is None:
            env_key = os.getenv("DHARMAOS_AUDIT_HMAC_KEY")
            if env_key:
                key = env_key.encode()
            elif os.getenv("DHARMAOS_ENV", "").lower() == "production":
                raise RuntimeError(
                    "DHARMAOS_AUDIT_HMAC_KEY must be set in production "
                    "(audit chain signing)"
                )
            else:
                import secrets
                import warnings
                warnings.warn(
                    "DHARMAOS_AUDIT_HMAC_KEY unset — using an ephemeral key; "
                    "audit chain will not verify across restarts and MUST NOT "
                    "be used in production.",
                    RuntimeWarning, stacklevel=2,
                )
                key = ("EPHEMERAL-" + secrets.token_hex(16)).encode()
        if audit_db is None:
            self.ledger = dh.RtaLedger(storage="memory", hmac_key=key)
        else:
            Path(audit_db).parent.mkdir(parents=True, exist_ok=True)
            self.ledger = dh.RtaLedger(
                storage="sqlite", sqlite_path=Path(audit_db), hmac_key=key,
            )
            # WAL lets an external verifier read the chain consistently while the
            # service is appending (default rollback journal gives a partial view).
            try:
                self.ledger._db.execute("PRAGMA journal_mode=WAL")  # noqa: SLF001
            except Exception:  # noqa: BLE001 — best effort; chain valid either way
                pass
        self._lock = threading.Lock()

    # -- core gate ------------------------------------------------------------

    def authorize(
        self,
        *,
        agent: str,
        action: str,
        case_id: str,
        goal: str | None = None,
        reason: str | None = None,
        write: bool | None = None,
        params: dict[str, Any] | None = None,
        prompt: str | None = None,
        raise_on_deny: bool = False,
        fail_open: bool = False,
    ) -> GovernedDecision:
        """Evaluate an agent action through the full governance kernel.

            `write=None` auto-classifies via the write-capability set. Reads/analyses
            PERMIT under a per-action lease; writes escalate to human sign-off.
            On internal error, the action is DENIED by default (fail-closed).
            Pass fail_open=True explicitly only for known-safe read operations
            where a governance outage must not block the read — never for
            anything that changes state, moves data, or spends resources.
            """
        try:
            decision = self._evaluate(
                agent=agent, action=action, case_id=case_id, goal=goal,
                reason=reason, write=write, params=params, prompt=prompt,
            )
        except Exception as exc:  # noqa: BLE001
            # C-4: a state-changing/high-impact action ALWAYS fails closed on a
            # governance error — never let an induced exception convert a write
            # into an ungoverned allow. fail_open only applies to read/analyze.
            is_write = self._is_write(action) if write is None else bool(write)
            allow_on_error = fail_open and not is_write
            logger.warning("dharmaos.kernel_error", action=action, agent=agent,
                error=str(exc), fail_open=fail_open, is_write=is_write,
                allow_on_error=allow_on_error)
            decision = GovernedDecision(
                allowed=allow_on_error, verdict="error", action=action, agent=agent,
                case_id=case_id,
                reason=f"governance error (fail_{'open' if allow_on_error else 'closed'}): {exc}",
                requires_hitl=not allow_on_error, halted_at_layer=None,
                ethics_ok=allow_on_error, ethics_score=0.0,
                audit_event_id=None, latency_ms=0.0,
            )
        if raise_on_deny and not decision.allowed:
            raise GovernanceDenied(decision)
        return decision

    def _evaluate(self, *, agent, action, case_id, goal, reason, write, params, prompt) -> GovernedDecision:
        now = datetime.now(timezone.utc)
        is_write = self._is_write(action) if write is None else bool(write)
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest() if prompt else ""
        business_reason = reason or (
            f"Governed {self.domain} action: {agent} performs {action} on case {case_id}."
        )
        # declared_goal MUST read as a business purpose — never "run/execute <tool>"
        # or the code-execution overlay blocks it as arbitrary command execution.
        declared_goal = goal or (
            f"{self.domain.capitalize()} compliance step for case {case_id} "
            f"({'human-reviewed write' if is_write else 'automated read/analyze'})"
        )
        contract = dh.ActionContract(
            action_id=f"{action}-{(prompt_hash[:10] or case_id)}",
            path_id=case_id, agent_id=agent, parent_agent_id=None,
            tenant_id=self.tenant, task_id=case_id, requested_at=now,
            name=action, kind="write" if is_write else "analyze", target=action,
            params=params or {}, tool=action, capability_requested=action,
            impact=dh.ActionImpact.HIGH if is_write else dh.ActionImpact.LOW,
            financial_effect=False,  # read/screen/analyze move no money
            reversible=not is_write,
            declared_goal=declared_goal, business_reason=business_reason,
            prompt_hash=prompt_hash, prompt_trust_domain="system",
        )
        lease = self.leases.grant(
            agent_id=agent, task_id=case_id, capabilities=[action],
            tenant_id=self.tenant, lease_seconds=self.lease_seconds,
        )
        try:
            report = self.adharma.evaluate(
                contract, presented_lease_id=lease.lease_id,
                presented_agent_id=agent, presented_tenant_id=self.tenant,
                domain=self.domain,
            )
        finally:
            try:
                self.leases.revoke(lease.lease_id)
            except Exception:  # noqa: BLE001
                pass

        verdict = getattr(report.verdict, "value", str(report.verdict))
        ethics = self.ethics.evaluate_contract(contract)
        deny = (verdict == "deny") or (not ethics.is_ethical)
        requires_hitl = verdict == "escalate_hitl"

        reason_text = "permitted"
        if not ethics.is_ethical:
            reason_text = "ethics:" + ",".join(ethics.violated_constraints)
        elif deny and report.halted_at_layer:
            try:
                lv = report.layer_verdicts[report.halted_at_layer - 1]
                reason_text = lv.reasons[0] if lv.reasons else f"denied@layer{report.halted_at_layer}"
            except (IndexError, AttributeError):
                reason_text = f"denied@layer{report.halted_at_layer}"

        audit_id = self._record_decision(agent, action, case_id, verdict, report, ethics)
        return GovernedDecision(
            allowed=not deny, verdict=verdict, action=action, agent=agent,
            case_id=case_id, reason=reason_text, requires_hitl=requires_hitl,
            halted_at_layer=report.halted_at_layer, ethics_ok=ethics.is_ethical,
            ethics_score=round(float(ethics.score), 3), audit_event_id=audit_id,
            latency_ms=round(float(report.total_latency_ms), 2),
        )

    # -- one-line wrappers ----------------------------------------------------

    def governed(
        self, fn: Callable[..., Any], *, agent: str, action: str,
        case_id_arg: str = "case_id", fail_open: bool = False,
    ) -> Callable[..., Any]:
        """Wrap a sync or async callable so it is governed before it runs.

        The wrapped call is denied (raises GovernanceDenied) on a hard block.
        `case_id_arg` names the kwarg carrying the case id (default "case_id").

        On governance error, the wrapped call is DENIED by default (fail-closed),
        matching `authorize()`. Pass fail_open=True explicitly only for known-safe
        read operations where a governance outage must not block the read.
        """
        is_async = _is_async(fn)

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            self.authorize(agent=agent, action=action,
                case_id=str(kwargs.get(case_id_arg, "unknown")),
                prompt=_first_str(args, kwargs),
                raise_on_deny=True, fail_open=fail_open)
            return fn(*args, **kwargs)

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            self.authorize(agent=agent, action=action,
                case_id=str(kwargs.get(case_id_arg, "unknown")),
                prompt=_first_str(args, kwargs),
                raise_on_deny=True, fail_open=fail_open)
            return await fn(*args, **kwargs)

        return async_wrapper if is_async else sync_wrapper

    # -- audit / disclosure / verification ------------------------------------

    def record_output(self, *, agent: str, case_id: str, prompt: str, output: str) -> str | None:
        """Emit an EU AI Act Art. 50 'AI-generated output' disclosure to the chain."""
        try:
            with self._lock:
                evt = self.ledger.record(
                    kind=str(dh.RtaEventKind.AI_GENERATED_OUTPUT),
                    tenant_id=self.tenant, agent_id=agent,
                    payload={
                    "case_id": case_id,
                    "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                },
                )
            return str(getattr(evt, "event_id", "") or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("dharmaos.disclosure_failed", agent=agent, error=str(exc))
            return None

    def verify_audit(self) -> dict[str, Any]:
        """Verify the tamper-evident audit chain (call in-process / via the live service)."""
        try:
            report = self.ledger.verify_integrity()
            return report.model_dump() if hasattr(report, "model_dump") else dict(report)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def metrics(self) -> dict[str, Any]:
        """Prometheus-style operational counters snapshot."""
        try:
            return dh.metrics_snapshot()
        except Exception:  # noqa: BLE001
            return {}

    # -- internals ------------------------------------------------------------

    def _is_write(self, action: str) -> bool:
        a = action.lower()
        return any(tok in a for tok in self._write_caps)

    def _record_decision(self, agent, action, case_id, verdict, report, ethics) -> str | None:
        try:
            with self._lock:
                evt = self.ledger.record(
                    kind=str(dh.RtaEventKind.ADHARMA_DECISION),
                    tenant_id=self.tenant, agent_id=agent,
                    payload={
                    "action": action, "case_id": case_id, "verdict": verdict,
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
            return str(getattr(evt, "event_id", "") or "")
        except Exception as exc:  # noqa: BLE001 — audit must never break the gate
            logger.warning("dharmaos.audit_record_failed", action=action, error=str(exc))
            return None


def _is_async(fn: Callable[..., Any]) -> bool:
    import asyncio
    return asyncio.iscoroutinefunction(fn)


def _first_str(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    for v in list(args) + list(kwargs.values()):
        if isinstance(v, str) and v:
            return v
    return None
