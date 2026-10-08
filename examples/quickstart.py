"""Minimal example: govern an agent's actions with the safety layer.

    Every action passes through the kernel before it runs.
    Dangerous actions are denied. Errors fail closed on writes.
    """

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dharmaos.integrate import DharmaOSKernel, GovernanceDenied

# One kernel per tenant. Audit chain persists to SQLite so it
# survives restarts. Set the HMAC key from the environment.
kernel = DharmaOSKernel(
    tenant="demo",
    audit_db="data/demo_audit.db",
    audit_hmac_key=os.environ.get("SAFETY_AUDIT_HMAC_KEY", "dev-only-key").encode(),
)


def agent_wants_to_send_email(to: str, body: str) -> str:
    """The agent's real tool. We govern it before it runs."""
    decision = kernel.authorize(
        agent="demo-agent",
        action="send_email",
        case_id="case-001",
        goal="notify the user",
        reason="user asked for a status update",
        params={"to": to, "body": body[:200]},
    )
    if not decision.allowed:
        # Blocked. The reason tells you which layer said no.
        raise GovernanceDenied(f"Blocked: {decision.reason}")
    # ... actually send the email here ...
    return f"sent to {to} (verdict: {decision.verdict})"


def agent_wants_to_read_file(path: str) -> str:
    decision = kernel.authorize(
        agent="demo-agent",
        action="read_file",
        case_id="case-001",
        params={"path": path},
    )
    if not decision.allowed:
        raise GovernanceDenied(f"Blocked: {decision.reason}")
    return f"read {path} (verdict: {decision.verdict})"


if __name__ == "__main__":
    # A read: permitted under a short-lived lease.
    print(agent_wants_to_read_file("/tmp/report.txt"))

    # A write-like external action: evaluated through the full pipeline.
    # The audit chain records the decision either way.
    try:
        print(agent_wants_to_send_email("boss@company.com", "status update"))
    except GovernanceDenied as e:
        print(e)
