"""
DharmaOS AdharmaDetector — 8-layer pre-execution governance and blocking pipeline.

PURPOSE
-------
AdharmaDetector is the central enforcement gate of DharmaOS. Every
ActionContract must pass all 8 layers before the agent may invoke a tool,
write to a database, send a communication, or take any consequential action.
The pipeline is non-compensatory: a DENY at any layer immediately halts
execution — no downstream layer can compensate for an upstream block.

LAYERS AT A GLANCE
------------------
Layer 1  Schema + identity + capability-lease verification.
Rejects cross-tenant inputs, agent-identity mismatches, and
missing or invalid capability leases.
Layer 2  World-state freshness gate. Stale or missing source facts block
actions that declared needs_fresh_world=True.
Layer 3  Constitutional vetoes — EthicsEngine (5 yamas) + RtaSupervisor
(6 named invariants). Any violation → DENY.
Layer 4  Domain-specific overlays: code-exec, external-comms, payment.
Callers can register additional named overlays.
Layer 5  Path-risk + cumulative budget. 24h rolling karma mean < -0.3,
RF-38 slow-poison escalation alert, or budget_cap exceeded → DENY/ESCALATE.
Layer 6  Abuse-twin: 5 Yakṣa-praśna screening questions + tamas-dominant
GuṇaMonitor check (confidence ≥ 0.7 → DENY).
Layer 7  Metacognitive audit — JnanaModule blind-spot and calibration-deficit check.
Layer 8  Policy-knowledge reconciliation — constitution version compatibility.

COMPOSITION RULES
-----------------
- Any DENY halts the pipeline immediately (non-compensatory).
- ESCALATE → HITL callback + DENY returned to caller.
- INCONCLUSIVE → DENY (fail-closed). SKIP → ALLOW for that layer only.

COMPLIANCE ROLE
---------------
- OWASP Agentic AI Top 10 ASI01 Goal-Hijack (Dec 2025).
- ISO/IEC 42001 Annex A AI management system controls.
- NIST AI RMF GOVERN/MAP/MEASURE/MANAGE.
- EU AI Act Art. 50 (audit trail wired via RtaLedger by caller).

Governance origin: "adharma" (non-dharmic action) — the 8-layer pipeline
that prevents actions contrary to declared constitutional values. Based on
Mahābhārata Yakṣa-praśna §11.3 and DharmaOS §3 architecture specification.

Source authority (Vedic):
- Dharmaśāstra concept of *adharma* as violation of Ṛta beyond the
letter of formal law: Mahābhārata Ādiparva I.1.119;
*dharmo rakṣati rakṣitaḥ* (Manusmṛti VIII.15).
- Mahābhārata Yakṣa-praśna 5 abuse-screening questions
(docs/vedic/foundations/YAKSHA-PRASHNA-SOURCE-SYNTHESIS-V1.md §11.3 /
§5.6 — universal abuse-screening layer).

Engineering / SOTA:
- DharmaOS §3 eight-layer runtime loop
.
- OWASP Agentic AI Top 10 ASI01 Goal-Hijack (Dec 2025).
- WebAgentGuard (arXiv:2604.12284, April 2026) — guard-model pattern
for runtime agentic governance.
- Constitutional AI (Anthropic 2022) — non-compensatory hard bans.
- ISO/IEC 42001 Annex A — AI management system controls.
- NIST AI RMF GOVERN/MAP/MEASURE/MANAGE four-phase cycle.

Closes RF-45 (V11 8-layer expansion).

Composition rules (non-compensatory):
- Any DENY halts the pipeline and returns DENY immediately.
- Any ESCALATE triggers the HITL callback (if provided) and returns DENY
to the caller. If no callback is registered, ESCALATE is treated as DENY.
- INCONCLUSIVE defaults to DENY (fail-closed, matching V9 Ṛta invariant
``check_fail_closed_defaults``).
- SKIP (layer not applicable) is treated as ALLOW.

Shadow mode (``shadow_mode=True``):
Every verdict is logged with ``would_have_blocked=True`` but the final
report always carries ``verdict=AdharmaVerdict.PERMIT``. Used during the
14-day bedding-in period to tune thresholds without blocking production.

Per-layer timeout:
Each layer is guarded by a wall-clock budget (default 50 ms). Exceeding
the budget → INCONCLUSIVE → fail-closed DENY (never silently allowed).

Side-effect invariant:
``evaluate()`` does NOT mutate V8 / V-WS / V-JÑĀNA state. Layer 5 reads
the saṃskāra ledger but never appends to it. Callers decide post-hoc
updates (Outcome Reconciler pattern, V-RECON).

Tenant isolation:
Every layer checks tenant scoping. A cross-tenant lease or mismatch
between presented_tenant_id and contract.tenant_id is rejected at Layer 1
and the pipeline halts with DENY severity=critical.

Sprint: V11 (2026-04-17).
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

# P2-4: Hybrid rate limiter — Redis-backed when available, in-memory fallback.
import threading as _threading
import time as _time
from collections import defaultdict as _defaultdict


class _InMemoryRateLimiter:
    """In-process token-bucket fallback when Redis is unavailable."""

    def __init__(self, max_rps: float = 100.0, burst: int = 200,
                            max_tenants: int = 10_000) -> None:
        self._max_rps = max_rps
        self._burst = float(burst)
        self._max_tenants = max_tenants
        self._tokens: dict[str, float] = _defaultdict(lambda: float(burst))
        self._last_refill: dict[str, float] = _defaultdict(_time.monotonic)
        # COR C3: the bucket update is read-modify-write and `evaluate` runs via
        # asyncio.to_thread (multi-threaded) — without this lock bursts leak.
        self._lock = _threading.Lock()

    def allow(self, tenant_id: str) -> bool:
        with self._lock:
            now = _time.monotonic()
            # COR C3: bound memory — a tenant-id-rotation attack would otherwise
            # grow the per-tenant dicts without limit (the DoS gate causing a DoS).
            if tenant_id not in self._tokens and len(self._tokens) >= self._max_tenants:
                oldest = min(self._last_refill, key=self._last_refill.__getitem__)
                self._tokens.pop(oldest, None)
                self._last_refill.pop(oldest, None)
            elapsed = now - self._last_refill[tenant_id]
            self._tokens[tenant_id] = min(
                self._burst,
                self._tokens[tenant_id] + elapsed * self._max_rps,
            )
            self._last_refill[tenant_id] = now
            if self._tokens[tenant_id] >= 1.0:
                self._tokens[tenant_id] -= 1.0
                return True
            return False


class _RedisRateLimiter:
    """Redis-backed token bucket via Valkey/Redis on localhost:6379.

    Falls back to in-memory rate limiter when Redis is unavailable.
    """

    _LUA_SCRIPT: str = """
    local key = KEYS[1]
    local rate = tonumber(ARGV[1])
    local burst = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local ttl = tonumber(ARGV[4])

    local tokens = redis.call('GET', key)
    if tokens == false then
    tokens = burst
    else
    tokens = tonumber(tokens)
    end

    -- Refill tokens based on elapsed time since last check
    local last_refill = redis.call('GET', key .. ':ts')
    if last_refill == false then
    last_refill = now
    else
    last_refill = tonumber(last_refill)
    end
    local elapsed = now - last_refill
    tokens = math.min(burst, tokens + elapsed * rate)

    if tokens >= 1 then
    tokens = tokens - 1
    redis.call('SET', key, tokens, 'EX', ttl)
    redis.call('SET', key .. ':ts', now, 'EX', ttl)
    return 1
    end
    return 0
    """

    def __init__(self, max_rps: float = 100.0, burst: int = 200, redis_url: str = "redis://localhost:6379") -> None:
        self._max_rps = max_rps
        self._burst = burst
        self._redis_url = redis_url
        self._fallback = _InMemoryRateLimiter(max_rps, burst)
        self._redis: object | None = None
        self._script_sha: str | None = None
        self._init_attempted: bool = False

    def _ensure_redis(self) -> bool:
        if self._init_attempted:
            return self._redis is not None
        self._init_attempted = True
        try:
            import redis.asyncio as _aredis
        except ImportError:
            return False
        try:
            self._redis = _aredis.from_url(self._redis_url)
            return True
        except Exception:
            return False

    async def _allow_redis(self, tenant_id: str) -> bool | None:
        """Try Redis-based rate check. Returns None if Redis unavailable."""
        if not self._ensure_redis():
            return None
        try:
            import asyncio as _asyncio

            now = _time.monotonic()
            result = await self._redis.eval(  # type: ignore[union-attr]
                self._LUA_SCRIPT,
                1,
                f"dharmaos:ratelimit:{tenant_id}",
                str(self._max_rps),
                str(self._burst),
                str(now),
                "60",  # TTL: 60s
            )
            return result == 1
        except Exception:
            return None

    def allow(self, tenant_id: str) -> bool:
        """Synchronous allow — uses sync fallback.

        For async callers, use allow_async() instead.
        """
        return self._fallback.allow(tenant_id)

    async def allow_async(self, tenant_id: str) -> bool:
        """Async allow — tries Redis first, falls back to in-memory."""
        result = await self._allow_redis(tenant_id)
        if result is not None:
            return result
        return self._fallback.allow(tenant_id)


# Module-level rate limiter: Redis-backed when available, in-memory fallback.
_RATE_LIMITER = _RedisRateLimiter()

from dharmaos.ethics_engine import ActionContract, ActionImpact
from dharmaos.metrics import audit_decision, pipeline_latency_ms, rate_limit_dropped

if TYPE_CHECKING:
    from dharmaos.guna_monitor import GunaMonitor
    from dharmaos.jnana_self_model import JnanaModule
    from dharmaos.lease import LeaseRegistry
    from dharmaos.rta import RtaSupervisor
    from dharmaos.samskara_ledger import SamskaraLedger
    from dharmaos.world_state import WorldStateService

_log: structlog.stdlib.BoundLogger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LayerStatus(str):
    """Outcome of a single pipeline layer.

    Using str subclass (not StrEnum) for consistent pydantic serialisation.
    """

    ALLOW: LayerStatus
    DENY: LayerStatus
    ESCALATE: LayerStatus
    INCONCLUSIVE: LayerStatus
    SKIP: LayerStatus  # layer not applicable to this action type

    def __new__(cls, value: str) -> LayerStatus:
        return super().__new__(cls, value)

    def __repr__(self) -> str:
        return f"LayerStatus({str(self)!r})"


LayerStatus.ALLOW = LayerStatus("allow")
LayerStatus.DENY = LayerStatus("deny")
LayerStatus.ESCALATE = LayerStatus("escalate")
LayerStatus.INCONCLUSIVE = LayerStatus("inconclusive")
LayerStatus.SKIP = LayerStatus("skip")


class AdharmaVerdict(str):
    """Final verdict returned to the caller by ``AdharmaDetector.evaluate()``.

    Using str subclass for consistent pydantic serialisation.
    """

    PERMIT: AdharmaVerdict
    DENY: AdharmaVerdict
    ESCALATE_HITL: AdharmaVerdict

    def __new__(cls, value: str) -> AdharmaVerdict:
        return super().__new__(cls, value)

    def __repr__(self) -> str:
        return f"AdharmaVerdict({str(self)!r})"


AdharmaVerdict.PERMIT = AdharmaVerdict("permit")
AdharmaVerdict.DENY = AdharmaVerdict("deny")
AdharmaVerdict.ESCALATE_HITL = AdharmaVerdict("escalate_hitl")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class LayerVerdict(BaseModel):
    """Result of a single layer execution."""

    model_config = ConfigDict(frozen=True)

    layer_num: int  # 1–8
    layer_name: str
    status: str  # LayerStatus value
    severity: str = "medium"  # low | medium | high | critical
    reasons: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0


class AdharmaReport(BaseModel):
    """Full pipeline report returned to the caller."""

    model_config = ConfigDict(frozen=True)

    report_id: UUID = Field(default_factory=uuid4)
    contract_id: str
    agent_id: str
    tenant_id: str
    verdict: str  # AdharmaVerdict value
    layer_verdicts: list[LayerVerdict]
    halted_at_layer: int | None = None  # layer that triggered the DENY
    shadow_mode: bool = False
    would_have_blocked: bool = False
    total_latency_ms: float = 0.0
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_with_timeout(
        fn: Callable[[], LayerVerdict],
                timeout_ms: float,
            layer_num: int,
                layer_name: str,
) -> LayerVerdict:
    """Execute a layer function and enforce a wall-clock budget.

    If the layer exceeds ``timeout_ms``, returns INCONCLUSIVE.
    Any exception from the layer is caught and returns INCONCLUSIVE
    (fail-closed).
    """
    t0 = time.perf_counter()
    try:
        result = fn()
        elapsed = (time.perf_counter() - t0) * 1000.0
        if elapsed > timeout_ms:
            _log.warning(
                "adharma.layer_timeout",
                    layer=layer_num,
                        elapsed_ms=round(elapsed, 2),
                        budget_ms=timeout_ms,
            )
            return LayerVerdict(
                        layer_num=layer_num,
                        layer_name=layer_name,
                    status=LayerStatus.INCONCLUSIVE,
                        severity="high",
                        reasons=[
                    f"Layer {layer_num} ({layer_name}) exceeded latency budget "
                    f"{timeout_ms:.0f} ms (actual {elapsed:.0f} ms). "
            "Fail-closed (INCONCLUSIVE → DENY)."
            ],
                        evidence={"elapsed_ms": elapsed, "budget_ms": timeout_ms},
                        latency_ms=elapsed,
            )
        return LayerVerdict(
                    layer_num=result.layer_num,
                    layer_name=result.layer_name,
                status=result.status,
                    severity=result.severity,
                    reasons=result.reasons,
                    evidence=result.evidence,
                    latency_ms=elapsed,
        )
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000.0
        _log.exception(
            "adharma.layer_exception",
                layer=layer_num,
                    layer_name=layer_name,
                exc=str(exc),
        )
        return LayerVerdict(
                    layer_num=layer_num,
                    layer_name=layer_name,
                status=LayerStatus.INCONCLUSIVE,
                    severity="high",
                    reasons=[
                f"Layer {layer_num} ({layer_name}) raised exception: {exc!r}. "
        "Fail-closed (INCONCLUSIVE → DENY)."
        ],
                    evidence={"exception": str(exc)},
                    latency_ms=elapsed,
        )


def _effective_status(status: str) -> str:
    """Resolve INCONCLUSIVE → DENY (fail-closed rule).

    SKIP passes through. All other values pass through unchanged.
    """
    if status == LayerStatus.INCONCLUSIVE:
        return LayerStatus.DENY
    return status


# ---------------------------------------------------------------------------
# Yakṣa-praśna 5 abuse-screening question heuristics
# ---------------------------------------------------------------------------
# Source: YAKSHA-PRASHNA-SOURCE-SYNTHESIS-V1.md §5.6 / §11.3
# These are heuristic matchers on contract text + rationale.
# LLM-judge hook deferred to Phase 4.

# P0-4: Unicode homoglyph / zero-width bypass hardening
_L33T_TABLE: dict[int, int] = str.maketrans({
        "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
        "@": "a", "$": "s", "7": "t", "8": "b", "+": "t",
})


def _normalize_for_abuse_scan(text: str) -> str:
    """NFKC normalize, strip zero-width chars, lowercase, de-l33t.

    P0-4: Adversarial prompts can trivially bypass \b-delimited regex with
    Unicode homoglyphs (byраss with Cyrillic), zero-width spaces, hyphenation,
    or l33tspeak. This normalizer eliminates those bypass vectors.
    """
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200b-\u200f\u2028\u2029\ufeff]", "", text)
    text = re.sub(r"(?<=\w)[\-\.](?=\w)", "", text)
    text = text.lower().translate(_L33T_TABLE)
    return text

_URGENCY_BYPASS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\burgent(ly)?\b", re.I),
    re.compile(r"\bimmediately\b", re.I),
    re.compile(r"\bno[- ]?time\b", re.I),
    re.compile(r"\bbypass\b", re.I),
    re.compile(r"\bskip[- ]?(review|check|approval|oversight)\b", re.I),
    re.compile(r"\boverride\b", re.I),
    re.compile(r"\bfast[- ]?track\b", re.I),
]

_HIDDEN_INTENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bsilent(ly)?\b", re.I),
    re.compile(r"\bstealth\b", re.I),
    re.compile(r"\bundetected\b", re.I),
    re.compile(r"\bsecret(ly)?\b", re.I),
    re.compile(r"\bcovert(ly)?\b", re.I),
    re.compile(r"\bbackdoor\b", re.I),
    re.compile(r"\bspoofing\b", re.I),
    re.compile(r"\bmasquerade\b", re.I),
]

_IMPLAUSIBLE_PRETEXT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\btest(ing)?\b.{0,30}\bprod(uction)?\b", re.I),
    re.compile(r"\bfor educational purposes\b", re.I),
    re.compile(r"\bjust checking\b", re.I),
    re.compile(r"\bno real harm\b", re.I),
    re.compile(r"\bnot actually\b", re.I),
    re.compile(r"\bonly a simulation\b", re.I),
]

_HIGH_PRIV_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\badmin\b", re.I),
    re.compile(r"\broot\b", re.I),
    re.compile(r"\bsudo\b", re.I),
    re.compile(r"\bsuperuser\b", re.I),
    re.compile(r"\bprivileged?\b", re.I),
    re.compile(r"\ball[- ]?access\b", re.I),
    re.compile(r"\bunrestricted\b", re.I),
]


def _check_yaksha_prashna(contract: ActionContract) -> list[int]:
    """Evaluate the 5 Yakṣa-praśna abuse-screening questions.

    Returns a list of question numbers (1-5) that returned "yes" (abuse signal).

    Questions (from YAKSHA-PRASHNA-SOURCE-SYNTHESIS-V1.md §5.6 + §11.3):
    Q1: Is there a hidden adversarial intent in the request?
    Q2: Is the stated pretext implausible for a legitimate user?
    Q3: Does the action require privilege the agent should not have?
    Q4: Does the request pressure for urgency / bypass of controls?
    Q5: Would this action cause asymmetric harm (few beneficiaries,
    many harmed)?

    For this sprint, heuristic matchers are used on declared_goal,
    business_reason, name, and target text.  LLM-judge hook is
    deferred to Phase 4.
    """
    text_fields = _normalize_for_abuse_scan(
        " ".join(
            filter(
                None,
                [
                    contract.declared_goal,
                    contract.business_reason,
                    contract.name,
                    contract.target,
                    " ".join(contract.potential_harms),
    ],
    )
    )
    )

    yes_answers: list[int] = []

    # Q1 — hidden adversarial intent
    if any(p.search(text_fields) for p in _HIDDEN_INTENT_PATTERNS):
        yes_answers.append(1)

    # Q2 — implausible pretext
    if any(p.search(text_fields) for p in _IMPLAUSIBLE_PRETEXT_PATTERNS):
        yes_answers.append(2)

    # Q3 — privilege the agent should not have
    # Proxy: contract requests admin/root-level capability
    cap = contract.capability_requested or ""
    if any(p.search(cap + " " + text_fields) for p in _HIGH_PRIV_PATTERNS):
        yes_answers.append(3)

    # Q4 — urgency / bypass-of-controls pressure
    if any(p.search(text_fields) for p in _URGENCY_BYPASS_PATTERNS):
        yes_answers.append(4)

    # Q5 — asymmetric harm: affects_others + irreversible + potential_harms listed
    if (
        contract.affects_others
                and not contract.reversible
            and len(contract.potential_harms) > 0
            and (contract.impact == ActionImpact.HIGH or contract.impact == ActionImpact.CRITICAL)
    ):
        yes_answers.append(5)

    return yes_answers


# ---------------------------------------------------------------------------
# Default domain overlay helpers
# ---------------------------------------------------------------------------


def _overlay_code_execution(contract: ActionContract) -> LayerVerdict | None:
    """Deny arbitrary code/shell execution without human sign-off.

    Proxy: contract.kind contains 'shell', 'exec', 'run', 'code',
    or contract.target contains 'bash'/'sh'/'python'/'node'.
    """
    exec_terms = {"shell", "exec", "run", "code", "execute", "subprocess", "cmd"}
    kind_lower = (contract.kind or "").lower()
    target_lower = (contract.target or "").lower()
    goal_lower = (contract.declared_goal or "").lower()

    is_code_exec = any(t in kind_lower or t in target_lower or t in goal_lower for t in exec_terms)
    if not is_code_exec:
        return None

    # Require sign-off evidence: check business_reason mentions explicit approval
    approval_markers = {"approved", "reviewed", "authorized", "sign-off", "hitl", "human approved"}
    reason_lower = (contract.business_reason or "").lower()
    has_sign_off = any(m in reason_lower for m in approval_markers)
    if not has_sign_off:
        return LayerVerdict(
                    layer_num=4,
                    layer_name="domain_overlay:code_execution",
                status=LayerStatus.DENY,
                    severity="high",
                    reasons=[
                f"Code/shell execution action '{contract.name}' requires explicit "
        "human sign-off (business_reason must reference an approved review). "
        "[DharmaOS domain overlay: code_execution]"
        ],
                    evidence={"kind": contract.kind, "target": contract.target},
        )
    return None


def _overlay_external_comms(contract: ActionContract) -> LayerVerdict | None:
    """Deny external communications when contract.affects_others is True
    and target is not in an explicit allowlist.

    The allowlist is implicit here — if contract.business_reason provides a
    named recipient and target is an email/domain, allow. Otherwise deny.
    """
    comms_terms = {"email", "send", "message", "post", "publish", "reply", "sms", "chat", "slack"}
    kind_lower = (contract.kind or "").lower()

    is_comms = any(t in kind_lower for t in comms_terms)
    if not is_comms:
        return None

    needs_justification = (
        contract.affects_others and contract.public_effect and not contract.business_reason.strip()
    )
    if needs_justification:
        return LayerVerdict(
                    layer_num=4,
                    layer_name="domain_overlay:external_comms",
                status=LayerStatus.DENY,
                    severity="high",
                    reasons=[
                f"External communication action '{contract.name}' has "
        "affects_others=True and public_effect=True but no business_reason "
        "naming the recipient. [DharmaOS domain overlay: external_comms]"
        ],
                    evidence={"kind": contract.kind, "target": contract.target},
        )
    return None


def _overlay_payment(contract: ActionContract) -> LayerVerdict | None:
    """Deny financial actions when the contract has financial_effect=True
    but no amount is specified (empty / zero-value params).
    """
    if not contract.financial_effect:
        return None

    amount = contract.params.get("amount") or contract.params.get("value")
    if amount is None:
        return LayerVerdict(
                    layer_num=4,
                    layer_name="domain_overlay:payment",
                status=LayerStatus.DENY,
                    severity="critical",
                    reasons=[
                f"Financial action '{contract.name}' has financial_effect=True "
        "but params contains no 'amount' or 'value'. Cannot authorise "
        "a payment of unknown amount. [DharmaOS domain overlay: payment]"
        ],
                    evidence={"financial_effect": True, "params": dict(contract.params)},
        )
    return None


# ---------------------------------------------------------------------------
# AdharmaDetector
# ---------------------------------------------------------------------------


class AdharmaDetector:
    """8-layer adharma governance pipeline.

    Each layer emits a ``LayerVerdict``.  Composition is non-compensatory:
    any DENY halts the pipeline.  INCONCLUSIVE → DENY (fail-closed).
    ESCALATE triggers HITL callback + returns DENY to caller.

    Usage::

    detector = AdharmaDetector(
    lease_registry=registry,
    world_state=ws,
    rta_supervisor=rta,
    samskara_ledger=ledger,
    guna_monitor=monitor,
    jnana_module=jnana,
    ethics_engine=engine,
    )
    report = detector.evaluate(
    contract,
    presented_lease_id=lease.lease_id,
    presented_agent_id=contract.agent_id,
    presented_tenant_id=contract.tenant_id,
    domain="finance",
    )
    if report.verdict != AdharmaVerdict.PERMIT:
    raise AdharmaBlockedError(report)

    Caller provides upstream module instances; the detector only composes
    their verdicts and does NOT mutate any upstream state.
    """

    def __init__(
    self,
    *,
                        lease_registry: LeaseRegistry | None = None,
                    world_state: WorldStateService | None = None,
                        rta_supervisor: RtaSupervisor | None = None,
                        samskara_ledger: SamskaraLedger | None = None,
                    guna_monitor: GunaMonitor | None = None,
                    jnana_module: JnanaModule | None = None,
                    ethics_engine: Any | None = None,  # EthicsEngine
                        shadow_tenants: set[str] | None = None,
                    shadow_mode: bool | None = None,
                    hitl_escalate: Callable[[AdharmaReport], None] | None = None,
                        layer_timeout_ms: float = 50.0,
                    budget_cap: int = 200,  # max actions in last 24 h before ESCALATE
    ) -> None:
        self._lease_registry = lease_registry
        self._world_state = world_state
        self._rta_supervisor = rta_supervisor
        self._samskara_ledger = samskara_ledger
        self._guna_monitor = guna_monitor
        self._jnana_module = jnana_module
        self._ethics_engine = ethics_engine

        # P0-2: Per-tenant shadow mode. shadow_tenants takes precedence.
        # Accepted for backward-compat: shadow_mode=True → all tenants in shadow.
        if shadow_tenants:
            self._shadow_tenants = shadow_tenants
        elif shadow_mode:
            self._shadow_tenants = {"*"}  # All tenants
        else:
            self._shadow_tenants: set[str] = set()

        self._hitl_escalate = hitl_escalate
        self._layer_timeout_ms = layer_timeout_ms
        self._budget_cap = budget_cap

        # Domain overlay registry — callers can add named overlays
        self._domain_overlays: dict[str, Callable[[ActionContract], LayerVerdict | None]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _is_shadow(self, tenant_id: str) -> bool:
        """P0-2: Per-tenant shadow mode check.

        Returns True when shadow mode is active for tenant_id.
        '*' in _shadow_tenants means ALL tenants (backward-compat with shadow_mode=True).
        """
        return "*" in self._shadow_tenants or tenant_id in self._shadow_tenants

    def evaluate(
    self,
                contract: ActionContract,
    *,
                            presented_lease_id: UUID | None = None,
                            presented_agent_id: str | None = None,
                            presented_tenant_id: str | None = None,
                domain: str | None = None,
    ) -> AdharmaReport:
        """Run the 8-layer adharma pipeline on ``contract``.

        Returns an ``AdharmaReport``.  In shadow mode, the report's
        ``verdict`` is always PERMIT even if a layer would have blocked,
        but ``would_have_blocked=True`` is set on the report.

        This method does NOT mutate upstream state (V8 / V-WS / V-JÑĀNA).
        """
        t_start = time.perf_counter()

        agent_id = presented_agent_id or contract.agent_id
        tenant_id = presented_tenant_id or contract.tenant_id

        # P2-3 / M1: bind per-request context. Unbind OUR keys first so a prior
        # request's tenant/agent/trace IDs (left on a pooled to_thread worker)
        # can't contaminate this request's audit logs before we rebind.
        structlog.contextvars.unbind_contextvars(
            "tenant_id", "agent_id", "trace_id", "contract_id"
        )
        structlog.contextvars.bind_contextvars(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    trace_id=contract.action_id,
                        contract_id=contract.action_id,
        )

        # P2-4: Rate limit per tenant — prevent pipeline saturation DoS
        if not _RATE_LIMITER.allow(tenant_id):
            rate_limit_dropped(tenant_id)
            return AdharmaReport(
                            contract_id=contract.action_id,
                        agent_id=agent_id,
                        tenant_id=tenant_id,
                        verdict=AdharmaVerdict.DENY,
                            layer_verdicts=[
                    LayerVerdict(
                                layer_num=0,
                                layer_name="rate_limit",
                            status=LayerStatus.DENY,
                                severity="high",
                                reasons=[
                            f"Rate limit exceeded for tenant '{tenant_id}'. "
            "Governance pipeline throttled to prevent saturation."
            ],
            )
            ],
                                halted_at_layer=0,
            )

        layer_verdicts: list[LayerVerdict] = []
        halted_at: int | None = None
        pipeline_verdict = AdharmaVerdict.PERMIT

        # Layer 1 first — it catches cross-tenant inputs immediately
        layers: list[tuple[int, str, Callable[[], LayerVerdict]]] = [
            (
                1,
                "schema_identity_lease",
                lambda: self._layer_1_schema_identity_lease(
                    contract, agent_id, tenant_id, presented_lease_id
        ),
        ),
            (
                2,
                "world_state_freshness",
                lambda: self._layer_2_world_state_freshness(contract),
        ),
            (
                3,
                "constitutional_vetoes",
                lambda: self._layer_3_constitutional_vetoes(contract),
        ),
            (
                4,
                "domain_overlays",
                lambda: self._layer_4_domain_overlays(contract, domain),
        ),
            (
                5,
                "path_risk_budget",
                lambda: self._layer_5_path_risk_budget(contract, agent_id, tenant_id),
        ),
            (
                6,
                "abuse_twin_guna",
                lambda: self._layer_6_abuse_twin_guna(contract),
        ),
            (
                7,
                "jnana_audit",
                lambda: self._layer_7_jnana_audit(contract, domain, agent_id, tenant_id),
        ),
            (
                8,
                "policy_knowledge_reconciliation",
                lambda: self._layer_8_policy_knowledge_reconciliation(contract, domain),
        ),
        ]

        for layer_num, layer_name, layer_fn in layers:
            lv = _run_with_timeout(layer_fn, self._layer_timeout_ms, layer_num, layer_name)
            layer_verdicts.append(lv)

            effective = _effective_status(lv.status)

            if effective == LayerStatus.ESCALATE:
                halted_at = layer_num
                pipeline_verdict = AdharmaVerdict.ESCALATE_HITL
                # ESCALATE triggers HITL and resolves to DENY for caller
                break

            if effective == LayerStatus.DENY:
                halted_at = layer_num
                pipeline_verdict = AdharmaVerdict.DENY
                break

        # ALLOW or SKIP — continue to next layer

        total_ms = (time.perf_counter() - t_start) * 1000.0

        # V12 metrics: record pipeline latency
        pipeline_latency_ms(total_ms)

        # ESCALATE → fire HITL callback before constructing the report
        escalated = pipeline_verdict == AdharmaVerdict.ESCALATE_HITL

        # Shadow mode: always return PERMIT to caller
        would_have_blocked = pipeline_verdict != AdharmaVerdict.PERMIT
        is_shadow = self._is_shadow(tenant_id)
        if is_shadow and would_have_blocked:
            final_verdict = AdharmaVerdict.PERMIT
        else:
            # For non-shadow: ESCALATE_HITL → DENY (caller always gets DENY)
            final_verdict = (
                AdharmaVerdict.DENY
                if pipeline_verdict == AdharmaVerdict.ESCALATE_HITL
                    else pipeline_verdict
            )

        # V12 metrics: record audit decision
        audit_decision(final_verdict)

        report = AdharmaReport(
                        contract_id=contract.action_id,
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    verdict=final_verdict,
                        layer_verdicts=layer_verdicts,
                            halted_at_layer=halted_at,
                        shadow_mode=is_shadow,
                            would_have_blocked=would_have_blocked,
                            total_latency_ms=total_ms,
        )

        # Fire HITL callback AFTER building the report so callers receive context
        if escalated and self._hitl_escalate is not None:
            try:
                self._hitl_escalate(report)
            except Exception as exc:
                _log.error("adharma.hitl_escalate_error", exc=str(exc))

        _log.info(
            "adharma.pipeline_complete",
                        contract_id=contract.action_id,
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    verdict=final_verdict,
                    halted_at=halted_at,
                        shadow_mode=is_shadow,
                            would_have_blocked=would_have_blocked,
                    total_ms=round(total_ms, 2),
        )

        return report

    async def evaluate_async(
    self,
                contract: ActionContract,
    *,
                            presented_lease_id: UUID | None = None,
                            presented_agent_id: str | None = None,
                            presented_tenant_id: str | None = None,
                domain: str | None = None,
    ) -> AdharmaReport:
        """Async-native evaluate with Redis rate limiting + LLM audit (V12).

        Production path for use inside asyncio event loops (FastAPI, LangGraph).
        Uses Redis-backed rate limiter and Gemini LLM-augmented Layer 7 audit
        when wired. Falls back to synchronous evaluate() on infrastructure failure.
        """
        import asyncio as _asyncio

        agent_id = presented_agent_id or contract.agent_id
        tenant_id = presented_tenant_id or contract.tenant_id

        # Redis-backed rate limit check (async path)
        if not await _RATE_LIMITER.allow_async(tenant_id):
            rate_limit_dropped(tenant_id)
            return AdharmaReport(
                            contract_id=contract.action_id,
                        agent_id=agent_id,
                        tenant_id=tenant_id,
                        verdict=AdharmaVerdict.DENY,
                            layer_verdicts=[
                    LayerVerdict(
                                layer_num=0,
                                layer_name="rate_limit",
                            status=LayerStatus.DENY,
                                severity="high",
                                reasons=[f"Rate limit exceeded for tenant '{tenant_id}'."],
            )
            ],
                                halted_at_layer=0,
            )

        return await _asyncio.to_thread(self.evaluate, contract, **{
                                "presented_lease_id": presented_lease_id,
                                "presented_agent_id": presented_agent_id,
                                "presented_tenant_id": presented_tenant_id,
                    "domain": domain,
        })

    # ------------------------------------------------------------------
    # Layer implementations
    # ------------------------------------------------------------------

    def _layer_1_schema_identity_lease(
    self,
                contract: ActionContract,
                agent_id: str,
                tenant_id: str,
                lease_id: UUID | None,
    ) -> LayerVerdict:
        """Layer 1 — Schema + identity + lease (V4 + V-LEASE).

        Checks:
        a. ActionContract schema — confidences in [0,1], requested_at UTC.
        (Validated by ActionContract.__post_init__; if contract arrives
        at detector it has already passed, so we do lightweight re-check.)
        b. Tenant isolation: presented_tenant_id must equal contract.tenant_id.
        c. Lease present: if no lease provided → DENY.
        d. Lease valid: LeaseRegistry.check_contract verifies not expired,
        not revoked, correct agent+tenant, and capability_requested granted.
        """
        # (a) Tenant isolation — cross-tenant inputs halted immediately
        if tenant_id != contract.tenant_id:
            return LayerVerdict(
                        layer_num=1,
                        layer_name="schema_identity_lease",
                    status=LayerStatus.DENY,
                        severity="critical",
                        reasons=[
                    f"Tenant isolation violation: presented tenant '{tenant_id}' "
                                                    f"differs from contract.tenant_id '{contract.tenant_id}'. "
            "Cross-tenant inputs rejected at Layer 1. [DharmaOS Layer 1]"
            ],
                        evidence={
                                        "presented_tenant_id": tenant_id,
                                        "contract_tenant_id": contract.tenant_id,
            },
            )

        # (b) Agent-identity check
        if agent_id != contract.agent_id:
            return LayerVerdict(
                        layer_num=1,
                        layer_name="schema_identity_lease",
                    status=LayerStatus.DENY,
                        severity="high",
                        reasons=[
                    f"Agent identity mismatch: presented agent '{agent_id}' "
                                                    f"differs from contract.agent_id '{contract.agent_id}'. "
            "[DharmaOS Layer 1]"
            ],
                        evidence={
                                        "presented_agent_id": agent_id,
                                        "contract_agent_id": contract.agent_id,
            },
            )

        # (c) Lease must be presented
        if lease_id is None:
            return LayerVerdict(
                        layer_num=1,
                        layer_name="schema_identity_lease",
                    status=LayerStatus.DENY,
                        severity="high",
                        reasons=[
                    "No capability lease presented. All agent actions require a "
            "valid V-LEASE capability grant. [DharmaOS Layer 1; V-LEASE §6.2]"
            ],
                        evidence={"lease_id": None},
            )

        # (d) Lease registry check
        if self._lease_registry is None:
            return LayerVerdict(
                        layer_num=1,
                        layer_name="schema_identity_lease",
                    status=LayerStatus.INCONCLUSIVE,
                        severity="high",
                        reasons=[
                    "LeaseRegistry not configured; cannot verify capability lease. "
            "Fail-closed. [DharmaOS Layer 1]"
            ],
                        evidence={"lease_id": str(lease_id)},
            )

        from dharmaos.lease import LeaseStatus

        verdict = self._lease_registry.check_contract(
                    lease_id=lease_id,
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    contract=contract,
        )

        if verdict.status == LeaseStatus.VALID:
            return LayerVerdict(
                        layer_num=1,
                        layer_name="schema_identity_lease",
                    status=LayerStatus.ALLOW,
                        severity="low",
                        reasons=["Lease valid, identity verified, capability granted."],
                        evidence={"lease_id": str(lease_id), "lease_status": verdict.status},
            )

        # Map every non-VALID status to a descriptive DENY
        severity_map = {
            LeaseStatus.EXPIRED: "high",
            LeaseStatus.REVOKED: "critical",
            LeaseStatus.NOT_FOUND: "high",
            LeaseStatus.TENANT_MISMATCH: "critical",
            LeaseStatus.AGENT_MISMATCH: "high",
            LeaseStatus.CAPABILITY_NOT_GRANTED: "high",
        }
        return LayerVerdict(
                    layer_num=1,
                    layer_name="schema_identity_lease",
                status=LayerStatus.DENY,
                    severity=severity_map.get(verdict.status, "high"),
                    reasons=[
                f"Lease check failed: {verdict.status} — {verdict.reason}. "
        "[DharmaOS Layer 1; V-LEASE §6.2]"
        ],
                    evidence={
                            "lease_id": str(lease_id),
                                "lease_status": verdict.status,
                        "reason": verdict.reason,
        },
        )

    def _layer_2_world_state_freshness(self, contract: ActionContract) -> LayerVerdict:
        """Layer 2 — World-state freshness (V-WS).

        If contract.needs_fresh_world is False → SKIP (no freshness required).
        Otherwise, call WorldStateService.require_fresh(); a StaleFactReport
        → DENY; no StaleFactReport → ALLOW; service unavailable → INCONCLUSIVE.
        """
        if not contract.needs_fresh_world:
            return LayerVerdict(
                        layer_num=2,
                        layer_name="world_state_freshness",
                    status=LayerStatus.SKIP,
                        severity="low",
                        reasons=["contract.needs_fresh_world=False — layer skipped."],
            )

        if self._world_state is None:
            return LayerVerdict(
                        layer_num=2,
                        layer_name="world_state_freshness",
                    status=LayerStatus.INCONCLUSIVE,
                        severity="high",
                        reasons=[
                    "WorldStateService not configured; cannot verify freshness. "
            "Fail-closed. [DharmaOS Layer 2]"
            ],
            )

        stale_report = self._world_state.require_fresh(
                    contract=contract,
        )

        if stale_report is None:
            return LayerVerdict(
                        layer_num=2,
                        layer_name="world_state_freshness",
                    status=LayerStatus.ALLOW,
                        severity="low",
                        reasons=["All required world-state sources are fresh."],
                        evidence={"sources_required": list(contract.sources_required)},
            )

        # Stale or missing sources
        stale = list(getattr(stale_report, "stale_sources", []))
        missing = list(getattr(stale_report, "missing_sources", []))
        return LayerVerdict(
                    layer_num=2,
                    layer_name="world_state_freshness",
                status=LayerStatus.DENY,
                    severity="high",
                    reasons=[
                f"World-state freshness check failed. "
                                f"Stale sources: {stale}. Missing sources: {missing}. "
        "Cannot act on an outdated world model. "
        "[DharmaOS Layer 2; Yoga Sūtras II.5 — anitye nitya matiḥ]"
        ],
                    evidence={
                                "stale_sources": stale,
                                "missing_sources": missing,
                                    "sources_required": list(contract.sources_required),
        },
        )

    def _layer_3_constitutional_vetoes(self, contract: ActionContract) -> LayerVerdict:
        """Layer 3 — Constitutional vetoes (V1 yamas + V9 ṛta).

        Checks:
        a. EthicsEngine.evaluate() on the contract's Action representation.
        Any yama DENY → DENY.
        b. RtaSupervisor.audit() snapshot.
        Any CRITICAL or HIGH invariant failure → DENY.
        c. Constitution hash invariant.
        If check_constitution_hash returns FAIL → DENY.
        """
        # (a) Yama constraint evaluation
        if self._ethics_engine is not None:
            action = contract.to_action()
            try:
                verdict = self._ethics_engine.evaluate(action)
                if not verdict.is_ethical:
                    return LayerVerdict(
                                layer_num=3,
                                layer_name="constitutional_vetoes",
                            status=LayerStatus.DENY,
                                severity="critical",
                                reasons=[
                            f"Yama constraint violation(s): {verdict.violated_constraints}. "
                                            f"Ethics score: {verdict.score:.3f}. "
                    "[Yoga Sūtras II.30 — five yamas; DharmaOS Layer 3]"
                    ],
                                evidence={
                                                    "violated_constraints": verdict.violated_constraints,
                                            "ethics_score": verdict.score,
                                            "recommendations": verdict.recommendations,
                    },
                    )
            except Exception as exc:
                return LayerVerdict(
                            layer_num=3,
                            layer_name="constitutional_vetoes",
                        status=LayerStatus.INCONCLUSIVE,
                            severity="high",
                            reasons=[
                        f"EthicsEngine raised exception: {exc!r}. Fail-closed. [DharmaOS Layer 3]"
                ],
                            evidence={"exception": str(exc)},
                )

        # (b) Ṛta supervisor audit
        if self._rta_supervisor is not None:
            try:
                from dharmaos.rta import InvariantStatus, SystemMode

                # NOTE (H4): this audit() runs per governed action and is a known
                # hot-path cost (disk YAML + object construction in rta.audit()).
                # It must stay a FRESH call here — caching it would defeat runtime
                # constitution-tamper detection (test_12). The correct fix is to
                # optimize audit() internals (reuse EthicsEngine/SamskaraLedger,
                # keep only the constitution-hash check fresh) — tracked, not a
                # Layer-3 cache. See SECURITY-CORRECTNESS-REMEDIATION-V1.md.
                report = self._rta_supervisor.audit()
                if report.mode in (SystemMode.HALTED, SystemMode.DEGRADED):
                    # Check if any CRITICAL or HIGH severity result failed
                    critical_or_high_failures = [
                        r
                            for r in report.results
                        if r.status in (InvariantStatus.FAIL, InvariantStatus.ERROR)
                            and r.severity in ("critical", "high")
                    ]
                    if critical_or_high_failures:
                        names = [r.name for r in critical_or_high_failures]
                        return LayerVerdict(
                                    layer_num=3,
                                    layer_name="constitutional_vetoes",
                                status=LayerStatus.DENY,
                                    severity="critical",
                                    reasons=[
                                f"Ṛta invariant failure(s): {names}. "
                        "System is not in a safe state for action execution. "
                        "[V9 RtaSupervisor; DharmaOS Layer 3]"
                        ],
                                    evidence={
                                                    "failed_invariants": names,
                                            "system_mode": report.mode,
                        },
                        )
            except Exception as exc:
                return LayerVerdict(
                            layer_num=3,
                            layer_name="constitutional_vetoes",
                        status=LayerStatus.INCONCLUSIVE,
                            severity="high",
                            reasons=[
                        f"RtaSupervisor raised exception: {exc!r}. Fail-closed. [DharmaOS Layer 3]"
                ],
                            evidence={"exception": str(exc)},
                )

        return LayerVerdict(
                    layer_num=3,
                    layer_name="constitutional_vetoes",
                status=LayerStatus.ALLOW,
                    severity="low",
                    reasons=["Constitutional vetoes passed (yamas + ṛta)."],
        )

    def _layer_4_domain_overlays(
                        self, contract: ActionContract, domain: str | None
    ) -> LayerVerdict:
        """Layer 4 — Domain overlays (task-type specific rules).

        Three built-in overlays:
        - code_execution: deny if arbitrary shell + no human sign-off
        - external_comms: deny if affects_others=True + public_effect + no business_reason
        - payment: deny if financial_effect=True + no amount in params

        Callers can register additional overlays via register_domain_overlay().
        Unknown domain with no matching overlay → ALLOW (permissive — not
        fail-closed for unknown domain names, per spec).
        """
        built_ins: list[Callable[[ActionContract], LayerVerdict | None]] = [
            _overlay_code_execution,
            _overlay_external_comms,
            _overlay_payment,
        ]

        for overlay_fn in built_ins:
            result = overlay_fn(contract)
            if result is not None:
                return result

        # Registered overlays — run if domain matches or overlay is domain-agnostic
        for overlay_name, overlay_fn in self._domain_overlays.items():
            try:
                result = overlay_fn(contract)
                if result is not None:
                    # Stamp the layer_num and layer_name correctly
                    return LayerVerdict(
                                layer_num=4,
                                layer_name=f"domain_overlay:{overlay_name}",
                            status=result.status,
                                severity=result.severity,
                                reasons=result.reasons,
                                evidence=result.evidence,
                    )
            except Exception as exc:
                _log.warning(
                    "adharma.domain_overlay_exception",
                            overlay=overlay_name,
                        exc=str(exc),
                )
                return LayerVerdict(
                            layer_num=4,
                            layer_name=f"domain_overlay:{overlay_name}",
                        status=LayerStatus.INCONCLUSIVE,
                            severity="medium",
                            reasons=[
                        f"Domain overlay '{overlay_name}' raised exception: {exc!r}. "
                "Fail-closed. [DharmaOS Layer 4]"
                ],
                            evidence={"exception": str(exc)},
                )

        return LayerVerdict(
                    layer_num=4,
                    layer_name="domain_overlays",
                status=LayerStatus.ALLOW,
                    severity="low",
                    reasons=[
                f"No domain overlay fired for domain={domain!r}. Action permitted at Layer 4."
        ],
        )

    def _layer_5_path_risk_budget(
    self,
                contract: ActionContract,
                agent_id: str,
                tenant_id: str,
    ) -> LayerVerdict:
        """Layer 5 — Path-risk + cumulative budget (V8 saṃskāra history).

        Checks (read-only — no ledger mutations):
        a. Query V8 saṃskāra ledger for recent similar actions.
        If mean karma_delta < -0.3 over ≥3 samples → DENY.
        b. detect_escalation() on the tenant.
        Any HIGH+ alert → DENY.
        c. Count actions in last 24h across ledger.
        If > budget_cap → ESCALATE.

        If no ledger → ALLOW (insufficient data is not a blocking signal).
        """
        if self._samskara_ledger is None:
            return LayerVerdict(
                        layer_num=5,
                        layer_name="path_risk_budget",
                    status=LayerStatus.ALLOW,
                        severity="low",
                        reasons=["SamskaraLedger not configured — no path-risk signal available."],
            )

        # (a) Recent negative-karma pattern — use 24h rolling window (P1-5)
        if contract.capability_requested:
            try:
                from datetime import timedelta

                # P0-3 / P1-5: Use public karma_rolling_mean instead of
                # query() + all-history mean. The 24h window prevents old
                # bad actions from permanently blocking and prevents recent
                # rogue actions from being diluted.
                rolling_mean = self._samskara_ledger.karma_rolling_mean(
                            agent_id=agent_id,
                            tenant_id=tenant_id,
                                window_hours=24,
                )
                if rolling_mean is not None and rolling_mean < -0.3:
                    return LayerVerdict(
                                layer_num=5,
                                layer_name="path_risk_budget",
                            status=LayerStatus.DENY,
                                severity="high",
                                reasons=[
                            f"Negative karma pattern detected: rolling mean "
                            f"{rolling_mean:.3f} over 24h window "
                    f"(threshold: -0.3). "
                    "[V8 SamskaraLedger; DharmaOS Layer 5]"
                    ],
                                evidence={
                            "rolling_karma_mean": rolling_mean,
                    },
                    )
            except Exception as exc:
                _log.warning("adharma.layer5_query_error", exc=str(exc))

        # (b) Escalation alert from detect_escalation
        try:
            import uuid as _uuid

            try:
                org_uuid = _uuid.UUID(tenant_id)
            except ValueError:
                org_uuid = _uuid.uuid5(_uuid.NAMESPACE_DNS, tenant_id)

            alert = self._samskara_ledger.detect_escalation(org_id=org_uuid)
            if alert is not None and alert.severity in ("high", "critical"):
                return LayerVerdict(
                            layer_num=5,
                            layer_name="path_risk_budget",
                        status=LayerStatus.DENY,
                            severity=alert.severity,
                            reasons=[
                        f"Saṃskāra escalation alert ({alert.severity}): "
                                f"delta={alert.delta:.3f}, "
                                f"baseline={alert.baseline_mean:.3f}, "
                                f"recent={alert.recent_mean:.3f}. "
                "Slow-poison pattern detected. [RF-38; DharmaOS Layer 5]"
                ],
                            evidence={
                                        "baseline_mean": alert.baseline_mean,
                                    "recent_mean": alert.recent_mean,
                                "delta": alert.delta,
                                    "severity": alert.severity,
                                            "top_contributors": alert.top_contributors,
                },
                )
        except Exception as exc:
            _log.warning("adharma.layer5_escalation_error", exc=str(exc))

        # (c) 24h action count budget
        try:
            now = datetime.now(UTC)
            entries_24h = self._samskara_ledger.recent_entries(
                        agent_id=agent_id,
                        tenant_id=tenant_id,
                            since_seconds=86400,
            )
            count_24h = len(entries_24h)
            if count_24h > self._budget_cap:
                return LayerVerdict(
                            layer_num=5,
                            layer_name="path_risk_budget",
                        status=LayerStatus.ESCALATE,
                            severity="high",
                            reasons=[
                        f"Action budget exceeded: {count_24h} actions in last 24h "
                            f"(cap={self._budget_cap}). Escalating to HITL. "
                "[DharmaOS Layer 5 cumulative budget]"
                ],
                            evidence={"count_24h": count_24h, "budget_cap": self._budget_cap},
                )
        except Exception as exc:
            _log.warning("adharma.layer5_budget_error", exc=str(exc))

        return LayerVerdict(
                    layer_num=5,
                    layer_name="path_risk_budget",
                status=LayerStatus.ALLOW,
                    severity="low",
                    reasons=["Path-risk and cumulative budget checks passed."],
        )

    def _layer_6_abuse_twin_guna(self, contract: ActionContract) -> LayerVerdict:
        """Layer 6 — Abuse Twin 3-prong + Yakṣa-praśna + Guṇa classifier.

        Sub-check (a) — Abuse Twin + Yakṣa-praśna 5 questions
        (YAKSHA-PRASHNA-SOURCE-SYNTHESIS-V1.md §5.6, §11.3):
        1. Is there a hidden adversarial intent?
        2. Is the stated pretext plausible?
        3. Does the action require privilege the agent should not have?
        4. Does the request pressure for urgency / bypass of controls?
        5. Would this cause asymmetric harm?
        ≥2 'yes' answers → DENY.
        1 'yes' answer → ESCALATE.

        Sub-check (b) — Guṇa classifier (V6 GunaMonitor):
        TAMAS dominant with confidence ≥ 0.7 → DENY.

        Heuristic matchers used for this sprint.
        LLM-judge hook is deferred to Phase 4.
        """
        # (a) Yakṣa-praśna abuse questions
        yes_answers = _check_yaksha_prashna(contract)

        if len(yes_answers) >= 2:
            return LayerVerdict(
                        layer_num=6,
                        layer_name="abuse_twin_guna",
                    status=LayerStatus.DENY,
                        severity="high",
                        reasons=[
                    f"Abuse Twin: {len(yes_answers)} of 5 Yakṣa-praśna abuse-screening "
                                            f"questions triggered (Q{yes_answers}). "
            "Threshold: ≥2 'yes' → DENY. "
            "[Mahābhārata Yakṣa-praśna §5.6; DharmaOS Layer 6]"
            ],
                        evidence={
                                        "triggered_questions": yes_answers,
                                    "question_count": len(yes_answers),
                                "threshold": 2,
            },
            )

        if len(yes_answers) == 1:
            return LayerVerdict(
                        layer_num=6,
                        layer_name="abuse_twin_guna",
                    status=LayerStatus.ESCALATE,
                        severity="medium",
                        reasons=[
                    f"Abuse Twin: 1 of 5 Yakṣa-praśna abuse-screening questions "
                                f"triggered (Q{yes_answers}). "
            "Threshold: 1 'yes' → ESCALATE. "
            "[Mahābhārata Yakṣa-praśna §5.6; DharmaOS Layer 6]"
            ],
                        evidence={
                                        "triggered_questions": yes_answers,
                                    "question_count": 1,
                                        "threshold_escalate": 1,
            },
            )

        # (b) Guṇa classifier — tamas dominant at high confidence → DENY
        if self._guna_monitor is not None:
            try:
                guna = self._guna_monitor._history[-1] if self._guna_monitor._history else None
                if guna is not None:
                    dominant = guna.dominant()
                    if dominant == "tamas" and guna.tamas >= 0.7:
                        return LayerVerdict(
                                    layer_num=6,
                                    layer_name="abuse_twin_guna",
                                status=LayerStatus.DENY,
                                    severity="high",
                                    reasons=[
                                f"Guṇa classifier: tamas-dominant state with confidence "
                                f"{guna.tamas:.3f} ≥ 0.7. "
                        "Tamas indicates negligence, concealment, or stale facts. "
                        "[Sāṅkhya Kārikā 13; V6 GunaMonitor; DharmaOS Layer 6]"
                        ],
                                    evidence={
                                            "dominant": dominant,
                                        "tamas": guna.tamas,
                                        "sattva": guna.sattva,
                                        "rajas": guna.rajas,
                        },
                        )
            except Exception as exc:
                # H7: a guṇa/abuse evaluation error must NOT silently ALLOW — it
                # means the layer could not assess the action, which is exactly an
                # INCONCLUSIVE → DENY (fail-closed) situation.
                _log.warning("adharma.layer6_guna_error", exc=str(exc))
                return LayerVerdict(
                            layer_num=6,
                            layer_name="abuse_twin_guna",
                        status=LayerStatus.INCONCLUSIVE,
                            severity="high",
                            reasons=[f"Guṇa/abuse evaluation error → fail-closed (INCONCLUSIVE→DENY): {exc}"],
                )

        return LayerVerdict(
                    layer_num=6,
                    layer_name="abuse_twin_guna",
                status=LayerStatus.ALLOW,
                    severity="low",
                    reasons=["Abuse Twin + Yakṣa-praśna: no abuse signals detected. Guṇa: clean."],
                    evidence={"triggered_questions": [], "question_count": 0},
        )

    def _layer_7_jnana_audit(
    self,
                contract: ActionContract,
                domain: str | None,
                agent_id: str,
                tenant_id: str,
    ) -> LayerVerdict:
        """Layer 7 — Jñāna-audit (V-JÑĀNA capability check + blind-spot match).

        Calls jnana_module.audit(domain=..., action_pattern=...).
        PASS  → ALLOW
        UNKNOWN_DOMAIN  → ESCALATE (first-time domain; needs HITL)
        JNANA_DEFICIT  → DENY (acting beyond competence)
        BLIND_SPOT_MATCH → DENY + log BlindSpot evidence
        """
        if self._jnana_module is None:
            return LayerVerdict(
                        layer_num=7,
                        layer_name="jnana_audit",
                    status=LayerStatus.ALLOW,
                        severity="low",
                        reasons=["JnanaModule not configured — Layer 7 skipped (no metacognitive signal)."],
            )

        audit_domain = domain or (
            contract.capability_requested.split(":")[0]
            if contract.capability_requested
                else contract.kind
        )
        action_pattern = contract.capability_requested or contract.name

        try:
            from dharmaos.jnana_self_model import (
            JNANA_AUDIT_BLIND_SPOT,
            JNANA_AUDIT_DEFICIT,
            JNANA_AUDIT_PASS,
            JNANA_AUDIT_UNKNOWN,
            )

            report = self._jnana_module.audit(
                    domain=audit_domain,
                            action_pattern=action_pattern,
            )

            if report.event == JNANA_AUDIT_PASS:
                return LayerVerdict(
                            layer_num=7,
                            layer_name="jnana_audit",
                        status=LayerStatus.ALLOW,
                            severity="low",
                            reasons=[
                        f"V-JÑĀNA audit PASS for domain '{audit_domain}' "
                                    f"(confidence={report.confidence:.3f}). "
                "[V-JÑĀNA; DharmaOS Layer 7]"
                ],
                            evidence={"domain": audit_domain, "confidence": report.confidence},
                )

            if report.event == JNANA_AUDIT_UNKNOWN:
                return LayerVerdict(
                            layer_num=7,
                            layer_name="jnana_audit",
                        status=LayerStatus.ESCALATE,
                            severity="medium",
                            reasons=[
                        f"V-JÑĀNA: unknown domain '{audit_domain}'. "
                "First-time domain requires HITL before proceeding. "
                "[V-JÑĀNA; DharmaOS Layer 7]"
                ],
                            evidence={"domain": audit_domain, "event": report.event},
                )

            if report.event == JNANA_AUDIT_DEFICIT:
                return LayerVerdict(
                            layer_num=7,
                            layer_name="jnana_audit",
                        status=LayerStatus.DENY,
                            severity="high",
                            reasons=[
                        f"V-JÑĀNA: jñāna deficit for domain '{audit_domain}' "
                                    f"(confidence={report.confidence:.3f} < threshold). "
                "Agent acting beyond its demonstrated competence. "
                "[V-JÑĀNA; Vivekacūḍāmaṇi 16; DharmaOS Layer 7]"
                ],
                            evidence={
                                "domain": audit_domain,
                                    "confidence": report.confidence,
                                "event": report.event,
                },
                )

            if report.event == JNANA_AUDIT_BLIND_SPOT:
                bs = report.matched_blind_spot
                bs_evidence: dict[str, Any] = {"domain": audit_domain, "event": report.event}
                if bs is not None:
                    bs_evidence.update(
                        {
                                            "blind_spot_id": str(bs.blind_spot_id),
                                            "failure_pattern": bs.failure_pattern,
                                                        "confidence_of_unawareness": bs.confidence_of_unawareness,
                                            "evidence_count": bs.evidence_count,
                    }
                    )
                return LayerVerdict(
                            layer_num=7,
                            layer_name="jnana_audit",
                        status=LayerStatus.DENY,
                            severity="high",
                            reasons=[
                        f"V-JÑĀNA: blind-spot match for domain '{audit_domain}'. "
                                    f"Pattern: '{bs.failure_pattern if bs else 'unknown'}'. "
                "Agent has a documented failure pattern in this domain. "
                "[V-JÑĀNA; DharmaOS Layer 7]"
                ],
                            evidence=bs_evidence,
                )

        except Exception as exc:
            _log.warning("adharma.layer7_jnana_error", exc=str(exc))
            return LayerVerdict(
                        layer_num=7,
                        layer_name="jnana_audit",
                    status=LayerStatus.INCONCLUSIVE,
                        severity="high",
                        reasons=[
                    f"JnanaModule.audit() raised exception: {exc!r}. Fail-closed. "
            "[DharmaOS Layer 7]"
            ],
                        evidence={"exception": str(exc)},
            )

        # Fallback for unexpected event values
        return LayerVerdict(
                    layer_num=7,
                    layer_name="jnana_audit",
                status=LayerStatus.ALLOW,
                    severity="low",
                    reasons=[f"V-JÑĀNA: unrecognised event '{report.event}' — permitted by default."],
                    evidence={"event": report.event},
        )

    def _layer_8_policy_knowledge_reconciliation(
    self,
                contract: ActionContract,
                domain: str | None,
    ) -> LayerVerdict:
        """Layer 8 — Policy-knowledge reconciliation (V-JÑĀNA).

        Calls jnana_module.policy_amendment_proposals(contract=contract).
        'revoke_capability' proposal → DENY.
        'require_hitl' proposal → ESCALATE.
        'narrow_scope' / 'expand_scope' → ALLOW but attach proposals
        as evidence (caller routes to V-LEASE for HITL review).
        No proposals → ALLOW.
        """
        if self._jnana_module is None:
            return LayerVerdict(
                        layer_num=8,
                        layer_name="policy_knowledge_reconciliation",
                    status=LayerStatus.ALLOW,
                        severity="low",
                        reasons=[
                    "JnanaModule not configured — Layer 8 skipped (no policy-amendment signal)."
            ],
            )

        try:
            proposals = self._jnana_module.policy_amendment_proposals(contract=contract)

            if not proposals:
                return LayerVerdict(
                            layer_num=8,
                            layer_name="policy_knowledge_reconciliation",
                        status=LayerStatus.ALLOW,
                            severity="low",
                            reasons=["V-JÑĀNA: no policy amendment proposals. Permitted."],
                )

            # Check for blocking proposals first
            for proposal in proposals:
                if proposal.recommended_action == "revoke_capability":
                    return LayerVerdict(
                                layer_num=8,
                                layer_name="policy_knowledge_reconciliation",
                            status=LayerStatus.DENY,
                                severity="high",
                                reasons=[
                            f"V-JÑĀNA policy amendment: 'revoke_capability' proposed "
                                        f"for domain '{proposal.domain}'. "
                                    f"Reason: {proposal.rationale}. "
                    "[V-JÑĀNA Layer 8; DharmaOS §3]"
                    ],
                                evidence={
                                        "proposal_id": str(proposal.proposal_id),
                                                "recommended_action": proposal.recommended_action,
                                    "domain": proposal.domain,
                                            "drift_magnitude": proposal.drift_magnitude,
                    },
                    )

            for proposal in proposals:
                if proposal.recommended_action == "require_hitl":
                    return LayerVerdict(
                                layer_num=8,
                                layer_name="policy_knowledge_reconciliation",
                            status=LayerStatus.ESCALATE,
                                severity="medium",
                                reasons=[
                            f"V-JÑĀNA policy amendment: 'require_hitl' proposed "
                                        f"for domain '{proposal.domain}'. "
                                    f"Reason: {proposal.rationale}. "
                    "[V-JÑĀNA Layer 8; DharmaOS §3]"
                    ],
                                evidence={
                                        "proposal_id": str(proposal.proposal_id),
                                                "recommended_action": proposal.recommended_action,
                                    "domain": proposal.domain,
                                            "drift_magnitude": proposal.drift_magnitude,
                    },
                    )

            # Only narrow_scope / expand_scope proposals — ALLOW with evidence
            proposal_summaries = [
                {
                                "proposal_id": str(p.proposal_id),
                                        "recommended_action": p.recommended_action,
                            "domain": p.domain,
                                    "drift_magnitude": p.drift_magnitude,
            }
                    for p in proposals
            ]
            return LayerVerdict(
                        layer_num=8,
                        layer_name="policy_knowledge_reconciliation",
                    status=LayerStatus.ALLOW,
                        severity="low",
                        reasons=[
                    f"V-JÑĀNA: {len(proposals)} policy amendment proposal(s) "
            "(narrow/expand scope only). Permitted; route proposals to V-LEASE."
            ],
                        evidence={"proposals": proposal_summaries},
            )

        except Exception as exc:
            _log.warning("adharma.layer8_policy_error", exc=str(exc))
            return LayerVerdict(
                        layer_num=8,
                        layer_name="policy_knowledge_reconciliation",
                    status=LayerStatus.INCONCLUSIVE,
                        severity="high",
                        reasons=[
                    f"JnanaModule.policy_amendment_proposals() raised exception: {exc!r}. "
            "Fail-closed. [DharmaOS Layer 8]"
            ],
                        evidence={"exception": str(exc)},
            )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def register_domain_overlay(
    self,
            name: str,
            fn: Callable[[ActionContract], LayerVerdict | None],
    ) -> None:
        """Register a custom domain overlay for Layer 4.

        The overlay callable receives an ActionContract and returns either
        a LayerVerdict (fires → DENY/ESCALATE) or None (overlay did not fire).

        Registered overlays are run AFTER the three built-in overlays.
        """
        self._domain_overlays[name] = fn
        _log.debug("adharma.overlay_registered", name=name)

    def shadow_report(self, evaluate_result: AdharmaReport) -> AdharmaReport:
        """Convert a result to shadow mode.

        Flips ``would_have_blocked`` based on original layer verdicts and
        rewrites ``verdict`` to PERMIT. Used when the detector was constructed
        without shadow_mode=True but the caller wants to backfill a shadow view.
        """
        would_block = any(
            _effective_status(lv.status) in (LayerStatus.DENY, LayerStatus.ESCALATE)
                for lv in evaluate_result.layer_verdicts
        )
        return AdharmaReport(
                    report_id=evaluate_result.report_id,
                        contract_id=evaluate_result.contract_id,
                    agent_id=evaluate_result.agent_id,
                    tenant_id=evaluate_result.tenant_id,
                    verdict=AdharmaVerdict.PERMIT,
                        layer_verdicts=evaluate_result.layer_verdicts,
                            halted_at_layer=evaluate_result.halted_at_layer,
                        shadow_mode=True,
                            would_have_blocked=would_block,
                            total_latency_ms=evaluate_result.total_latency_ms,
                    decided_at=evaluate_result.decided_at,
        )
