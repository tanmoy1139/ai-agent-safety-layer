# DharmaOS Integration Guide

How to wire the DharmaOS governance kernel into any agent stack. This supersedes
the framework-specific wiring playbook — it is **framework-agnostic** and uses the
drop-in SDK (`dharmaos.integrate.DharmaOSKernel`).

---

## 1. Install

```bash
pip install -e .
# or pin a sealed release:
# pip install ai-agent-safety-layer
import dharmaos                       # 172 exports; import name is `dharmaos`
```

Runtime deps are just `pydantic` + `structlog`. Redis / NATS / a content classifier
are **optional** — the kernel degrades gracefully without them.

## 2. The one object you need

```python
from dharmaos.integrate import DharmaOSKernel, GovernanceDenied

gov = DharmaOSKernel(
    tenant="acme-bank",                      # multi-tenant scope
    audit_db="data/dharmaos_audit.db",       # tamper-evident chain (sqlite/WAL); None = in-memory
    domain="financial",                      # domain overlay set
)
```

Set the audit signing key in production (never the ephemeral dev key):
`export DHARMAOS_AUDIT_HMAC_KEY=...` and `export DHARMAOS_ENV=production`.

## 3. Gate an action

```python
d = gov.authorize(
    agent="screening_agent",
    action="sanctions_screen",               # read/analyze actions PERMIT; writes escalate
    goal="Screen the customer party against sanctions and PEP lists",  # plain business purpose
    case_id="C-1001",
    prompt=optional_llm_prompt,
)
if not d.allowed:                            # fail-closed on DENY
    handle_refusal(d.reason)                 # d.verdict, d.reason, d.halted_at_layer, d.requires_hitl
result = run_the_action()
gov.record_output(agent="screening_agent", case_id="C-1001",
                  prompt=optional_llm_prompt, output=result)   # EU AI Act Art. 50
```

Or wrap a callable so it's governed before it runs (raises `GovernanceDenied` on a hard block):

```python
screen = gov.governed(screen_fn, agent="screening_agent", action="sanctions_screen")
```

### Semantics
- **Reads/analyses** (default) PERMIT under a per-action capability lease.
- **State-changing actions** (names containing persist/write/submit/file_report/override/approve/delete/store, or `write=True`) escalate to human sign-off.
- **DENY** → fail-closed. **Internal error** → fail-open for reads only; writes/high-impact fail-closed.

## 4. Calibration notes (built into the SDK — here for when you build contracts by hand)

The 8-layer Adharma gate is strict by design. The SDK already handles these; respect them if you bypass it:
- `financial_effect=False` for read/screen/analyze actions (only set True for actual money movement, which requires an `amount` in `params`).
- `declared_goal` must read as a **business purpose**, never "run/execute &lt;tool&gt;" (the code-execution overlay blocks that).
- A **capability lease** must be granted before the gate (Layer 1) — the SDK grants/revokes one per call.
- Pin a stable `DHARMAOS_AUDIT_HMAC_KEY` so the chain verifies across restarts.

## 5. Framework adapters

**Plain async / sync** — call `gov.authorize(...)` (sync) from anywhere.

**FastAPI dependency:**
```python
def governed(action: str):
    def dep(case_id: str, agent: str = "api"):
        d = gov.authorize(agent=agent, action=action, case_id=case_id, raise_on_deny=True)
        return d
    return Depends(dep)
```

**LangGraph node:**
```python
def guarded_node(action):
    def deco(fn):
        async def wrapped(state):
            gov.authorize(agent=state["agent"], action=action,
                          case_id=state["case_id"], raise_on_deny=True)
            return await fn(state)
        return wrapped
    return deco
```

**Single tool chokepoint (recommended):** if your platform already funnels every tool call
through one gate (e.g. a `PolicyGate.call_tool`), call `gov.authorize(...)` there once — it then
governs every agent at a single, auditable seam. See the FINTRAC reference wiring
(`apps/api/services/dharmaos_wire/`).

## 6. Audit & verification

```python
report = gov.verify_audit()        # {"ok": bool, "total_entries": int, ...} — verify via the LIVE service
gov.metrics()                      # Prometheus-style counters snapshot
```

The audit chain is tamper-evident (SHA-256 + Merkle + HMAC). Verify **in-process / through the
running service** (it holds the chain + key); a cold external reader needs the same HMAC key.

## 7. Coexistence with existing governance

DharmaOS is **additive defense-in-depth** — it does not replace operational controls (allowlists,
content filters, rate limiters, your own audit log). Recommended layering: cheap allowlist →
business-rule policy → **DharmaOS deep gate** (ethics + 8-layer + lease + crypto audit). Pair it
with a trained content/jailbreak classifier (e.g. Llama Guard) for content safety; DharmaOS owns
action-governance + compliance.

## 8. Production checklist

- [ ] `DHARMAOS_ENV=production` + `DHARMAOS_AUDIT_HMAC_KEY` / `DHARMAOS_TRUST_SIGN_KEY` / `DHARMAOS_MEMORY_SIGN_KEY` set (no default keys).
- [ ] `audit_db` on durable storage; nightly `verify_audit()`.
- [ ] Tenant id wired from your auth context (drives `tenant_to_org_id` isolation).
- [ ] HITL handler wired for `requires_hitl` / escalations.
- [ ] Feature-flag the wire so it can be disabled without a code change.
