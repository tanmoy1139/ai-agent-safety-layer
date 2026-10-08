"""
DharmaOS WorldStateService — current-facts ledger with freshness TTLs, lineage
tracking, and contradiction detection.

PURPOSE
-------
WorldStateService maintains the agent's model of current external reality —
API responses, tool outputs, user-provided data — with per-fact freshness TTLs,
source-lineage tracking, and tenant isolation. It is the data-currency layer
that prevents agents from acting on stale information.

WorldStateService is distinct from SamskaraLedger: SamskaraLedger tracks the
historical record of agent actions and their consequences; WorldStateService
tracks the CURRENT state of the world as of the most recent observations.

FRESHNESS GATE
--------------
AdharmaDetector Layer 2 calls require_fresh(contract) for every ActionContract
with needs_fresh_world=True. If any source in contract.sources_required is
stale (age > freshness_max_age_seconds) or missing, the layer returns DENY.
This enforces Yoga Sūtras II.5 (anitye nitya matiḥ) — the agent must not
mistake impermanent observations for permanent facts.

KEY INVARIANTS
--------------
1. Supersession is immutable: the original fact record is never mutated.
A new version with a supersedes pointer is appended; the old version
is preserved for bi-temporal as-of queries.
2. Tenant isolation: org_id=A never returns org_id=B facts.
3. Stale-safe defaults: is_fresh() returns STALE (not FRESH) on ambiguity.
4. Contradiction detection is non-destructive: reports conflicts without
auto-resolving them (human review required).

KEY TYPES
---------
WorldStateService  : Main service. upsert_fact() → WorldStateFact.
get_fact() → WorldStateFact | None.
require_fresh(contract) → StaleFactReport | None.
detect_contradictions() → list[ContradictionReport].
snapshot() / restore() for bi-temporal queries.
WorldStateFact  : Immutable single-fact record with lineage, TTL,
supersedes pointer, and freshness metadata.
FreshnessStatus  : FRESH / STALE / MISSING sentinel string constants.
StaleFactReport  : Gate report listing stale_sources and missing_sources.
ContradictionReport  : Two conflicting non-superseded fact versions.

COMPLIANCE ROLE
---------------
- Feeds AdharmaDetector Layer 2 freshness gate (RF-42 closure).
- Source-lineage tracking provides EU AI Act Art. 50 provenance metadata.

Governance origin: viveka (discriminating wisdom) about the currency of
knowledge — Yoga Sūtras II.5 (anitye nitya matiḥ: mistaking the impermanent
for the permanent is avidyā). The freshness gate enforces viveka before
any consequential action.

World-State Ledger — freshness + lineage + contradiction detection.

Source: DharmaOS §2.2.


Distinct from V8 Saṃskāra-Ledger: V8 tracks HISTORICAL action-consequence
traces; V-WS tracks CURRENT facts about external reality (API responses,
tool outputs, user-provided data) with per-fact freshness TTLs.

Feeds V11 Layer 2 (world-state freshness gate): any ActionContract with
needs_fresh_world=True must verify all sources_required are non-stale
before execution.

**Philosophical grounding:**
The World-State Ledger embodies *viveka* (discriminating wisdom) about the
currency of knowledge.  Per Yoga Sūtras II.5 (Bryant 2009): *anitye nitya
matiḥ* — mistaking the impermanent for the permanent is *avidyā*. Acting on
stale facts is precisely this error.  The freshness gate enforces *viveka*
before consequential action.

**Key invariants:**
1. Supersession is immutable: original fact record is never mutated.
`supersedes` pointer on the new version is the only linkage.
2. Tenant isolation: org_id=A never returns org_id=B facts.
Cross-tenant contradictions are not conflated.
3. Stale-safe defaults: is_fresh() returns STALE (not FRESH) on ambiguity.
4. Contradiction detection is non-destructive: reports conflicts, does not
auto-resolve.

**RF-42 closure:**
Tests 17–19 in tests/test_world_state.py prove the freshness gate:
- Test 17: needs_fresh_world=True + all fresh → require_fresh() is None.
- Test 18: needs_fresh_world=True + stale source → StaleFactReport.
- Test 19: needs_fresh_world=True + missing source → StaleFactReport.missing_sources.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from dharmaos.ethics_engine import ActionContract


# ---------------------------------------------------------------------------
# Enums + models
# ---------------------------------------------------------------------------


class FreshnessStatus(str):
    """Freshness status sentinel values.

    Using str subclass (not StrEnum) so pydantic serialises cleanly to
    plain strings and comparisons work without import of enum module at
    call sites.
    """

    FRESH: FreshnessStatus
    STALE: FreshnessStatus
    MISSING: FreshnessStatus

    def __new__(cls, value: str) -> FreshnessStatus:
        return super().__new__(cls, value)

    def __repr__(self) -> str:
        return f"FreshnessStatus({str(self)!r})"


FreshnessStatus.FRESH = FreshnessStatus("fresh")
FreshnessStatus.STALE = FreshnessStatus("stale")
FreshnessStatus.MISSING = FreshnessStatus("missing")


class WorldStateFact(BaseModel):
    """Single tracked fact about current external reality.

    Immutable; a 'refresh' creates a NEW WorldStateFact and supersedes
    the previous one (bi-temporal pattern, cf. Graphiti arXiv:2501.13956).

    Field notes:
    - fact_id: canonical name ("btc_usd_price", "user.timezone")
    - source:  URL, API endpoint, or tool name that produced the value
    - lineage: citation chain — ordered list of sources consulted to derive
    this value (empty for local/derived facts)
    - supersedes: previous version's fact_version_id, None for first version
    - org_id:  tenant scope; None = global (single-tenant deployments)
    """

    model_config = ConfigDict(frozen=True)

    fact_id: str
    value: Any
    source: str
    retrieved_at: datetime
    freshness_ttl_seconds: int
    lineage: list[str] = Field(default_factory=list)
    fact_version_id: UUID = Field(default_factory=uuid4)
    supersedes: UUID | None = None
    org_id: UUID | None = None


class ContradictionReport(BaseModel):
    """Report of conflicting non-superseded versions of the same fact."""

    fact_id: str
    conflicting_versions: list[WorldStateFact]
    detected_at: datetime


class StaleFactReport(BaseModel):
    """Report returned by require_fresh() when a contract's sources are stale/missing.

    contract_id: action_id from ActionContract (None if contract has no id).
    stale_sources: sources_required entries with known-but-expired facts.
    missing_sources: sources_required entries with no fact in the ledger.
    oldest_retrieved_at: earliest retrieved_at among known-but-stale entries.
    now: wall-clock time at evaluation.
    """

    contract_id: str | None = None
    stale_sources: list[str]
    missing_sources: list[str]
    oldest_retrieved_at: datetime | None = None
    now: datetime


# ---------------------------------------------------------------------------
# _TenantKey — internal ledger key
# ---------------------------------------------------------------------------

# Ledger is keyed by (fact_id, org_id) tuples.
# org_id=None represents global facts.
_TenantKey = tuple[str, UUID | None]


def _key(fact_id: str, org_id: UUID | None) -> _TenantKey:
    return (fact_id, org_id)


# ---------------------------------------------------------------------------
# WorldStateService
# ---------------------------------------------------------------------------


class WorldStateService:
    """Maintains current facts + freshness + contradiction detection.

    API:
    put(fact)  — add/refresh; handles supersession
    get(fact_id, *, org_id)  — latest non-superseded version
    is_fresh(fact_id, ...)  — FreshnessStatus enum-like value
    get_contradictions(fact_id)  — ContradictionReport | None
    require_fresh(contract)  — gate: StaleFactReport | None
    refresh(fact_id, source, fetcher, ...)  — re-fetch via callable
    snapshot() / restore(snap)  — bi-temporal as-of queries

    Thread safety: not thread-safe. External locking required for
    concurrent access (deferred to Phase 4 production hardening).

    Cross-tenant isolation: all operations scope by org_id; org_id=A
    never returns org_id=B data.
    """

    def __init__(self, *, default_ttl_seconds: int = 300) -> None:
        self._default_ttl: int = default_ttl_seconds
        # _latest[key] = the current (non-superseded) WorldStateFact
        self._latest: dict[_TenantKey, WorldStateFact] = {}
        # _all_versions[key] = list of ALL versions ever put (for contradiction scan)
        self._all_versions: dict[_TenantKey, list[WorldStateFact]] = defaultdict(list)
        # _superseded_ids: set of fact_version_ids that have been superseded
        self._superseded_ids: set[UUID] = set()

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def put(self, fact: WorldStateFact) -> WorldStateFact:
        """Insert or supersede a fact.

        If a fact with the same fact_id (+ org_id) already exists, the new
        record is created with ``supersedes=<old_version_id>`` and the old
        record is marked superseded.  The OLD record is NEVER mutated — only
        the _superseded_ids set is updated.

        Returns the (possibly rewrapped) WorldStateFact that was stored.
        """
        k = _key(fact.fact_id, fact.org_id)
        existing = self._latest.get(k)

        if existing is not None:
            # Build new version that supersedes the primary existing one
            new_fact = fact.model_copy(
                    update={
            "supersedes": existing.fact_version_id,
            # Preserve fact_version_id from input if explicitly set,
            # but generate a new one only when the input shares the
            # exact same UUID as the existing record (i.e. caller did
            # not set it explicitly and uuid4() would collide, which
            # is astronomically unlikely but we guard anyway).
            "fact_version_id": (
            fact.fact_version_id
            if fact.fact_version_id != existing.fact_version_id
            else uuid4()
            ),
            }
            )
            # Mark ALL currently active versions superseded — this clears any
            # concurrent-write contradictions that were pending.  The new put()
            # is the canonical resolution.
            for v in self._all_versions[k]:
                if v.fact_version_id not in self._superseded_ids:
                    self._superseded_ids.add(v.fact_version_id)
            self._latest[k] = new_fact
            self._all_versions[k].append(new_fact)
            return new_fact
        else:
            # First insertion: store as-is (supersedes=None is correct)
            self._latest[k] = fact
            self._all_versions[k].append(fact)
            return fact

    def get(
    self,
    fact_id: str,
    *,
    org_id: UUID | None = None,
    ) -> WorldStateFact | None:
        """Return the latest non-superseded version for fact_id + org_id.

        Returns None if fact_id is unknown under the given org_id.
        """
        return self._latest.get(_key(fact_id, org_id))

    # ------------------------------------------------------------------
    # Freshness
    # ------------------------------------------------------------------

    def is_fresh(
    self,
    fact_id: str,
    *,
    max_age_seconds: int | None = None,
    org_id: UUID | None = None,
            now: datetime | None = None,
    ) -> FreshnessStatus:
        """Check freshness of the latest version of fact_id.

        FRESH  — retrieved_at + effective_ttl > now
        STALE  — known but TTL elapsed (stale-safe: ambiguous → STALE)
        MISSING — unknown fact_id under this org_id
        """
        fact = self.get(fact_id, org_id=org_id)
        if fact is None:
            return FreshnessStatus.MISSING

        wall = now if now is not None else datetime.now(UTC)
        effective_ttl = (
            max_age_seconds if max_age_seconds is not None else fact.freshness_ttl_seconds
        )

        # Ensure both datetimes are timezone-aware for safe comparison
        retrieved = fact.retrieved_at
        if retrieved.tzinfo is None:
            # Stale-safe: if we can't confirm it's fresh, say STALE
            return FreshnessStatus.STALE

        age_seconds = (wall - retrieved).total_seconds()
        if age_seconds < effective_ttl:
            return FreshnessStatus.FRESH
        return FreshnessStatus.STALE

    # ------------------------------------------------------------------
    # Contradiction detection
    # ------------------------------------------------------------------

    def get_contradictions(
    self,
    fact_id: str,
    *,
    org_id: UUID | None = None,
    ) -> ContradictionReport | None:
        """Detect non-superseded versions with divergent values.

        Contradictions arise from concurrent puts from different sources —
        we detect them but do not auto-resolve (non-destructive).

        Returns None if:
        - No versions exist for this key
        - All non-superseded versions have identical values
        - Only one non-superseded version exists

        Returns ContradictionReport if 2+ non-superseded versions with
        distinct values coexist.
        """
        k = _key(fact_id, org_id)
        all_v = self._all_versions.get(k, [])
        if not all_v:
            return None

        # Gather non-superseded versions
        active = [v for v in all_v if v.fact_version_id not in self._superseded_ids]
        if len(active) <= 1:
            return None

        # Compare values — use == for equality; treat equal values as non-contradictory
        first_value = active[0].value
        if all(v.value == first_value for v in active):
            return None

        return ContradictionReport(
        fact_id=fact_id,
        conflicting_versions=active,
        detected_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # ActionContract freshness gate — RF-42
    # ------------------------------------------------------------------

    # H1: deterministic tenant_id(str) → org_id(UUID) convention so the freshness
    # gate scopes facts to the calling tenant. Use this SAME helper on the write
    # side (put) so stored and queried org_ids match. Empty/None tenant → None
    # (global / single-tenant). A non-empty tenant maps to a stable uuid5.
    _ORG_NAMESPACE: UUID = uuid5(NAMESPACE_URL, "dharmaos.tenant-org")

    @classmethod
    def tenant_to_org_id(cls, tenant_id: str | None) -> UUID | None:
        if not tenant_id:
            return None
        return uuid5(cls._ORG_NAMESPACE, tenant_id)

    def require_fresh(
    self,
    contract: ActionContract,
    *,
            now: datetime | None = None,
    ) -> StaleFactReport | None:
        """Gate helper: verify every source in contract.sources_required is FRESH.

        Checks freshness within contract.freshness_max_age_seconds for each
        entry in contract.sources_required.  Only evaluated when
        contract.needs_fresh_world=True.

        Returns:
        None — all required sources are fresh; execution may proceed.
        StaleFactReport — one or more sources are stale or missing; V11
        Layer 2 gate should refuse execution.

        If needs_fresh_world=False, returns None immediately (no check).
        """
        if not contract.needs_fresh_world:
            return None

        wall = now if now is not None else datetime.now(UTC)
        max_age = contract.freshness_max_age_seconds
        stale: list[str] = []
        missing: list[str] = []
        oldest: datetime | None = None

        org_id = self.tenant_to_org_id(contract.tenant_id)
        for source in contract.sources_required:
            # Scope to the tenant first; fall back to shared/global facts
            # (org_id=None) which are visible to every tenant. A fact that exists
            # for the tenant but is STALE is NOT masked by a global one.
            scope = org_id
            status = self.is_fresh(source, max_age_seconds=max_age, now=wall, org_id=org_id)
            if status == FreshnessStatus.MISSING and org_id is not None:
                status = self.is_fresh(source, max_age_seconds=max_age, now=wall, org_id=None)
                scope = None
            if status == FreshnessStatus.MISSING:
                missing.append(source)
            elif status == FreshnessStatus.STALE:
                stale.append(source)
                fact = self.get(source, org_id=scope)
                if fact is not None and (oldest is None or fact.retrieved_at < oldest):
                    oldest = fact.retrieved_at

        if stale or missing:
            return StaleFactReport(
            contract_id=getattr(contract, "action_id", None),
            stale_sources=stale,
            missing_sources=missing,
            oldest_retrieved_at=oldest,
                    now=wall,
            )
        return None

    # ------------------------------------------------------------------
    # Refresh via injected fetcher
    # ------------------------------------------------------------------

    def refresh(
    self,
    fact_id: str,
    source: str,
    fetcher: Callable[[], Any],
    *,
    org_id: UUID | None = None,
    freshness_ttl_seconds: int | None = None,
    lineage: list[str] | None = None,
    ) -> WorldStateFact:
        """Re-fetch a fact via the injected callable and put() the new version.

        Fetcher failures bubble up — the ledger is left unchanged on error.

        Args:
        fact_id: canonical name of the fact to refresh.
        source: URL / tool name for the new version.
        fetcher: zero-arg callable that returns the new value.
        org_id: tenant scope.
        freshness_ttl_seconds: TTL for the new fact; defaults to
        the existing fact's TTL or default_ttl_seconds.
        lineage: explicit lineage list for the new version; if None,
        inherits the existing fact's lineage.

        Returns:
        The newly created WorldStateFact.

        Raises:
        Any exception from fetcher() — ledger is NOT modified.
        """
        existing = self.get(fact_id, org_id=org_id)

        # Call fetcher BEFORE any ledger mutation — exception leaves state intact
        new_value = fetcher()

        ttl = (
            freshness_ttl_seconds
            if freshness_ttl_seconds is not None
                else (existing.freshness_ttl_seconds if existing is not None else self._default_ttl)
        )
        new_lineage: list[str] = (
            lineage
            if lineage is not None
                else (list(existing.lineage) if existing is not None else [])
        )

        new_fact = WorldStateFact(
        fact_id=fact_id,
                value=new_value,
                source=source,
        retrieved_at=datetime.now(UTC),
        freshness_ttl_seconds=ttl,
        lineage=new_lineage,
                org_id=org_id,
        )
        return self.put(new_fact)

    # ------------------------------------------------------------------
    # Snapshot / restore (bi-temporal as-of support)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Capture a complete point-in-time snapshot of the ledger state.

        The snapshot is a deep copy — mutations to the live service do NOT
        affect it.  Use restore() to rewind the ledger to a past state for
        bi-temporal as-of queries.

        Returns a plain dict (JSON-serialisable with care; UUIDs are kept
        as UUID objects for restore round-trip fidelity).
        """
        return {
        "latest": copy.deepcopy(dict(self._latest)),
        "all_versions": copy.deepcopy(dict(self._all_versions)),
        "superseded_ids": copy.copy(self._superseded_ids),
        "default_ttl": self._default_ttl,
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore the ledger to a previously snapshotted state.

        Overwrites the current ledger entirely.  Useful for bi-temporal
        as-of queries (rewind to snapshot, query, then discard or re-snapshot).

        Args:
        snapshot: dict produced by snapshot().
        """
        self._latest = copy.deepcopy(snapshot["latest"])
        all_v_raw: dict[_TenantKey, list[WorldStateFact]] = copy.deepcopy(snapshot["all_versions"])
        self._all_versions = defaultdict(list, all_v_raw)
        self._superseded_ids = copy.copy(snapshot["superseded_ids"])
        self._default_ttl = snapshot["default_ttl"]

    # ------------------------------------------------------------------
    # Test-only injection hook (concurrent put simulation)
    # ------------------------------------------------------------------

    def _inject_concurrent(self, fact: WorldStateFact) -> None:
        """TEST-ONLY: inject a fact without triggering supersession logic.

        Simulates a concurrent write from a different source that arrives
        after another fact for the same key is already stored — both become
        active (non-superseded), which is the condition for contradiction
        detection.

        This method is intentionally NOT part of the public API.  Production
        code must go through put() which enforces supersession.
        """
        k = _key(fact.fact_id, fact.org_id)
        # Do NOT update _latest (both versions coexist as active)
        # Do NOT add to _superseded_ids
        self._all_versions[k].append(fact)
        # If nothing is in _latest yet, seed it with this fact
        if k not in self._latest:
            self._latest[k] = fact
