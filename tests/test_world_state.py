"""Tests for the V-WS World-State Ledger.

Covers:
- Core put/get/freshness (tests 1-7)
- Lineage tracking (tests 8-10)
- Contradiction detection (tests 11-13)
- Tenant isolation (tests 14-16)
- ActionContract integration — RF-42 (tests 17-19)
- Snapshot / restore (tests 20-21)
- Refresh via fetcher (tests 22-23)

23 tests total — Sprint V-WS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from dharmaos.ethics_engine import ActionContract, ActionImpact
from dharmaos.world_state import (
FreshnessStatus,
StaleFactReport,
WorldStateFact,
WorldStateService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _fact(
fact_id: str = "test.fact",
        value: object = "hello",
source: str = "https://api.example.com/v1",
        ttl: int = 300,
lineage: list[str] | None = None,
org_id: UUID | None = None,
retrieved_at: datetime | None = None,
) -> WorldStateFact:
    return WorldStateFact(
    fact_id=fact_id,
            value=value,
            source=source,
    retrieved_at=retrieved_at or _now(),
    freshness_ttl_seconds=ttl,
    lineage=lineage or [],
            org_id=org_id,
    )


def _contract(
*,
needs_fresh_world: bool = True,
sources_required: list[str] | None = None,
freshness_max_age_seconds: int = 300,
) -> ActionContract:
    return ActionContract(
    action_id="test-action",
    path_id="p1",
    agent_id="agent-test",
    parent_agent_id=None,
    tenant_id="tenant-test",
    task_id="task-test",
    requested_at=_now(),
            name="test action",
            kind="browse",
            target="https://example.com",
    needs_fresh_world=needs_fresh_world,
    freshness_max_age_seconds=freshness_max_age_seconds,
    sources_required=sources_required or [],
            impact=ActionImpact.LOW,
    )


# ---------------------------------------------------------------------------
# 1. Core — put + get round-trip
# ---------------------------------------------------------------------------


class TestPutGetRoundTrip:
    def test_put_and_get_returns_same_fact(self) -> None:
        """Test 1: put + get round-trip."""
        svc = WorldStateService()
        f = _fact(fact_id="btc.price", value=65000.0)
        returned = svc.put(f)
        retrieved = svc.get("btc.price")
        assert retrieved is not None
        assert retrieved.value == 65000.0
        assert retrieved.fact_version_id == returned.fact_version_id

    def test_get_returns_latest_after_two_puts(self) -> None:
        """Test 2: get returns latest (non-superseded) after two puts."""
        svc = WorldStateService()
        f1 = _fact(fact_id="btc.price", value=60000.0)
        f2 = _fact(fact_id="btc.price", value=67000.0)
        svc.put(f1)
        svc.put(f2)
        latest = svc.get("btc.price")
        assert latest is not None
        assert latest.value == 67000.0

    def test_supersession_new_version_supersedes_old(self) -> None:
        """Test 3: new version's `supersedes` matches old version's fact_version_id."""
        svc = WorldStateService()
        f1 = _fact(fact_id="weather.temp", value=22.0)
        f2 = _fact(fact_id="weather.temp", value=25.0)
        r1 = svc.put(f1)
        r2 = svc.put(f2)
        assert r2.supersedes == r1.fact_version_id

    def test_old_version_immutable_after_supersession(self) -> None:
        """Test 4: old version's retrieved_at is unchanged after supersession."""
        svc = WorldStateService()
        t_old = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        t_new = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)
        f1 = _fact(fact_id="price.eth", value=3000.0, retrieved_at=t_old)
        f2 = _fact(fact_id="price.eth", value=3100.0, retrieved_at=t_new)
        r1 = svc.put(f1)
        svc.put(f2)
        # r1 should be immutable; its retrieved_at must equal t_old exactly
        assert r1.retrieved_at == t_old

    def test_is_fresh_fresh_within_ttl(self) -> None:
        """Test 5a: is_fresh returns FRESH when within TTL."""
        svc = WorldStateService()
        f = _fact(fact_id="news", value="ok", ttl=600)
        svc.put(f)
        status = svc.is_fresh("news")
        assert status == FreshnessStatus.FRESH

    def test_is_fresh_stale_after_ttl(self) -> None:
        """Test 5b: is_fresh returns STALE when TTL has elapsed."""
        svc = WorldStateService()
        old_time = _now() - timedelta(seconds=700)
        f = _fact(fact_id="old.news", value="stale", ttl=600, retrieved_at=old_time)
        svc.put(f)
        status = svc.is_fresh("old.news")
        assert status == FreshnessStatus.STALE

    def test_is_fresh_missing_for_unknown_fact(self) -> None:
        """Test 5c: is_fresh returns MISSING for unknown fact_id."""
        svc = WorldStateService()
        status = svc.is_fresh("nonexistent.fact")
        assert status == FreshnessStatus.MISSING

    def test_max_age_override_tighter_than_ttl(self) -> None:
        """Test 6: max_age_seconds override tighter than fact TTL → earlier STALE."""
        svc = WorldStateService()
        # Fact was retrieved 120 seconds ago, TTL = 300 (would be FRESH)
        old_time = _now() - timedelta(seconds=120)
        f = _fact(fact_id="market.cap", value=1e12, ttl=300, retrieved_at=old_time)
        svc.put(f)
        # But we want freshness within 60 seconds → STALE
        status = svc.is_fresh("market.cap", max_age_seconds=60)
        assert status == FreshnessStatus.STALE

    def test_get_unknown_fact_returns_none(self) -> None:
        """Test 7: Unknown fact_id in get → None, not KeyError."""
        svc = WorldStateService()
        result = svc.get("does.not.exist")
        assert result is None


# ---------------------------------------------------------------------------
# 2. Lineage
# ---------------------------------------------------------------------------


class TestLineage:
    def test_lineage_preserved_on_put(self) -> None:
        """Test 8: lineage list preserved on put."""
        svc = WorldStateService()
        lineage = ["https://source1.com", "https://source2.com"]
        f = _fact(fact_id="derived.fact", lineage=lineage)
        svc.put(f)
        retrieved = svc.get("derived.fact")
        assert retrieved is not None
        assert retrieved.lineage == lineage

    def test_refresh_lineage_continuity(self) -> None:
        """Test 9: refresh preserves lineage continuity (new version can include old source)."""
        svc = WorldStateService()
        original_source = "https://api.original.com"
        f1 = _fact(fact_id="stock.price", source=original_source, lineage=[original_source])
        svc.put(f1)

        new_source = "https://api.new.com"
        new_fact = svc.refresh(
            "stock.price",
                source=new_source,
        fetcher=lambda: 420.0,
        lineage=[original_source, new_source],
        )
        assert original_source in new_fact.lineage
        assert new_source in new_fact.lineage

    def test_empty_lineage_is_permitted(self) -> None:
        """Test 10: empty lineage is permitted (local facts)."""
        svc = WorldStateService()
        f = _fact(fact_id="user.timezone", value="America/New_York", lineage=[])
        svc.put(f)
        retrieved = svc.get("user.timezone")
        assert retrieved is not None
        assert retrieved.lineage == []


# ---------------------------------------------------------------------------
# 3. Contradiction detection
# ---------------------------------------------------------------------------


class TestContradictionDetection:
    def test_two_concurrent_puts_different_values_detected(self) -> None:
        """Test 11: two concurrent puts from different sources with different values → contradiction."""
        svc = WorldStateService()
        f1 = WorldStateFact(
        fact_id="btc.price",
                value=65000.0,
                source="https://api.coinbase.com",
        retrieved_at=_now(),
        freshness_ttl_seconds=60,
        )
        f2 = WorldStateFact(
        fact_id="btc.price",
                value=64500.0,
                source="https://api.kraken.com",
        retrieved_at=_now(),
        freshness_ttl_seconds=60,
        )
        svc.put(f1)
        # Force a concurrent put by injecting without supersession (different source path)
        svc._inject_concurrent(f2)  # test-only injection hook
        report = svc.get_contradictions("btc.price")
        assert report is not None
        assert len(report.conflicting_versions) >= 2

    def test_contradiction_resolves_after_supersession(self) -> None:
        """Test 12: contradiction resolves after one side is explicitly superseded."""
        svc = WorldStateService()
        f1 = WorldStateFact(
        fact_id="fx.gbp",
                value=1.27,
                source="https://api.a.com",
        retrieved_at=_now(),
        freshness_ttl_seconds=60,
        )
        f2 = WorldStateFact(
        fact_id="fx.gbp",
                value=1.28,
                source="https://api.b.com",
        retrieved_at=_now(),
        freshness_ttl_seconds=60,
        )
        svc.put(f1)
        svc._inject_concurrent(f2)
        # Verify contradiction exists
        assert svc.get_contradictions("fx.gbp") is not None
        # Now supersede both by putting an authoritative value
        f3 = _fact(fact_id="fx.gbp", value=1.275)
        svc.put(f3)
        # After canonical put, contradiction should be resolved
        report = svc.get_contradictions("fx.gbp")
        assert report is None

    def test_no_contradiction_identical_values_different_sources(self) -> None:
        """Test 13: no contradiction when two puts have identical values (even different sources)."""
        svc = WorldStateService()
        f1 = WorldStateFact(
        fact_id="server.status",
                value="healthy",
                source="https://monitor-a.com",
        retrieved_at=_now(),
        freshness_ttl_seconds=120,
        )
        f2 = WorldStateFact(
        fact_id="server.status",
                value="healthy",
                source="https://monitor-b.com",
        retrieved_at=_now(),
        freshness_ttl_seconds=120,
        )
        svc.put(f1)
        svc._inject_concurrent(f2)
        report = svc.get_contradictions("server.status")
        assert report is None


# ---------------------------------------------------------------------------
# 4. Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_put_org_a_get_org_b_returns_none(self) -> None:
        """Test 14: put with org_id=A, get with org_id=B → None."""
        svc = WorldStateService()
        org_a = uuid4()
        org_b = uuid4()
        f = _fact(fact_id="secret.rate", value=4.5, org_id=org_a)
        svc.put(f)
        result = svc.get("secret.rate", org_id=org_b)
        assert result is None

    def test_contradiction_report_scoped_to_org_id(self) -> None:
        """Test 15: contradiction report scoped to org_id; org_B sees org_B facts only."""
        svc = WorldStateService()
        org_a = uuid4()
        org_b = uuid4()
        # Org A has contradiction
        fa1 = WorldStateFact(
        fact_id="price",
                value=100.0,
                source="src-a1",
        retrieved_at=_now(),
        freshness_ttl_seconds=300,
                org_id=org_a,
        )
        fa2 = WorldStateFact(
        fact_id="price",
                value=200.0,
                source="src-a2",
        retrieved_at=_now(),
        freshness_ttl_seconds=300,
                org_id=org_a,
        )
        svc.put(fa1)
        svc._inject_concurrent(fa2)
        # Org B has no contradiction
        fb = _fact(fact_id="price", value=150.0, org_id=org_b)
        svc.put(fb)
        # Org B should see no contradiction
        report_b = svc.get_contradictions("price", org_id=org_b)
        assert report_b is None
        # Org A should see contradiction
        report_a = svc.get_contradictions("price", org_id=org_a)
        assert report_a is not None

    def test_cross_org_contradictions_not_detected(self) -> None:
        """Test 16: cross-org contradictions NOT detected (full isolation)."""
        svc = WorldStateService()
        org_a = uuid4()
        org_b = uuid4()
        fa = WorldStateFact(
        fact_id="fx.usd",
                value=1.0,
                source="bank-a",
        retrieved_at=_now(),
        freshness_ttl_seconds=60,
                org_id=org_a,
        )
        fb = WorldStateFact(
        fact_id="fx.usd",
                value=0.99,
                source="bank-b",
        retrieved_at=_now(),
        freshness_ttl_seconds=60,
                org_id=org_b,
        )
        svc.put(fa)
        svc.put(fb)
        # Neither org should see contradiction (different orgs, not concurrent within same org)
        report_a = svc.get_contradictions("fx.usd", org_id=org_a)
        report_b = svc.get_contradictions("fx.usd", org_id=org_b)
        assert report_a is None
        assert report_b is None


# ---------------------------------------------------------------------------
# 5. ActionContract integration — CLOSES RF-42
# ---------------------------------------------------------------------------


class TestActionContractIntegration:
    def test_all_sources_fresh_require_fresh_returns_none(self) -> None:
        """Test 17: ActionContract + all sources fresh → require_fresh returns None.

        RF-42 closure proof: freshness gate passes when world-state is current.
        """
        svc = WorldStateService()
        source_url = "https://api.market.com/prices"
        f = _fact(fact_id=source_url, ttl=300)
        svc.put(f)
        contract = _contract(
        needs_fresh_world=True,
        sources_required=[source_url],
        freshness_max_age_seconds=300,
        )
        result = svc.require_fresh(contract)
        assert result is None

    def test_stale_source_require_fresh_returns_report(self) -> None:
        """Test 18: ActionContract + one source stale → StaleFactReport with that source listed.

        RF-42 closure proof: freshness gate blocks when world-state is stale.
        """
        svc = WorldStateService()
        stale_source = "https://api.market.com/prices"
        old_time = _now() - timedelta(seconds=400)
        f = _fact(fact_id=stale_source, ttl=300, retrieved_at=old_time)
        svc.put(f)
        contract = _contract(
        needs_fresh_world=True,
        sources_required=[stale_source],
        freshness_max_age_seconds=300,
        )
        result = svc.require_fresh(contract)
        assert result is not None
        assert isinstance(result, StaleFactReport)
        assert stale_source in result.stale_sources

    def test_missing_source_require_fresh_returns_missing_report(self) -> None:
        """Test 19: ActionContract + missing source → StaleFactReport.missing_sources populated.

        RF-42 closure proof: freshness gate blocks when required source is unknown.
        """
        svc = WorldStateService()
        missing_source = "https://api.unknown.com/data"
        contract = _contract(
        needs_fresh_world=True,
        sources_required=[missing_source],
        freshness_max_age_seconds=300,
        )
        result = svc.require_fresh(contract)
        assert result is not None
        assert isinstance(result, StaleFactReport)
        assert missing_source in result.missing_sources


# ---------------------------------------------------------------------------
# 6. Snapshot / restore
# ---------------------------------------------------------------------------


class TestSnapshotRestore:
    def test_snapshot_restore_round_trips_full_ledger(self) -> None:
        """Test 20: snapshot() + restore() round-trips full ledger state."""
        svc = WorldStateService()
        f1 = _fact(fact_id="a", value=1, lineage=["src1"])
        f2 = _fact(fact_id="b", value=2)
        svc.put(f1)
        svc.put(f2)
        snap = svc.snapshot()

        svc2 = WorldStateService()
        svc2.restore(snap)

        r_a = svc2.get("a")
        r_b = svc2.get("b")
        assert r_a is not None
        assert r_a.value == 1
        assert r_a.lineage == ["src1"]
        assert r_b is not None
        assert r_b.value == 2

    def test_restore_older_snapshot_enables_as_of_queries(self) -> None:
        """Test 21: restoring an older snapshot enables bi-temporal as-of queries."""
        svc = WorldStateService()
        f1 = _fact(fact_id="metric.rate", value=0.05)
        svc.put(f1)
        snap_old = svc.snapshot()  # snapshot BEFORE update

        f2 = _fact(fact_id="metric.rate", value=0.06)
        svc.put(f2)
        # Current ledger has the new value
        assert svc.get("metric.rate") is not None
        assert svc.get("metric.rate").value == 0.06  # type: ignore[union-attr]

        # Restore old snapshot → as-of query sees old value
        svc.restore(snap_old)
        old_val = svc.get("metric.rate")
        assert old_val is not None
        assert old_val.value == 0.05


# ---------------------------------------------------------------------------
# 7. Refresh via fetcher
# ---------------------------------------------------------------------------


class TestRefreshViaFetcher:
    def test_refresh_working_fetcher_updates_retrieved_at(self) -> None:
        """Test 22: refresh() with working fetcher → new fact with updated retrieved_at."""
        svc = WorldStateService()
        old_time = _now() - timedelta(seconds=400)
        source = "https://api.prices.com/btc"
        f = _fact(fact_id="btc.usd", value=60000.0, ttl=300, retrieved_at=old_time, source=source)
        svc.put(f)

        before = _now()
        new_fact = svc.refresh("btc.usd", source=source, fetcher=lambda: 66000.0)
        after = _now()

        assert new_fact.value == 66000.0
        assert before <= new_fact.retrieved_at <= after

    def test_refresh_failing_fetcher_propagates_exception_ledger_unchanged(self) -> None:
        """Test 23: refresh() with failing fetcher → exception propagates, ledger unchanged."""
        svc = WorldStateService()
        source = "https://api.prices.com/eth"
        original_value = 3000.0
        f = _fact(fact_id="eth.usd", value=original_value, source=source)
        svc.put(f)

        def failing_fetcher() -> float:
            msg = "Network timeout"
            raise ConnectionError(msg)

        with pytest.raises(ConnectionError, match="Network timeout"):
            svc.refresh("eth.usd", source=source, fetcher=failing_fetcher)

        # Ledger must be unchanged
        still_there = svc.get("eth.usd")
        assert still_there is not None
        assert still_there.value == original_value
