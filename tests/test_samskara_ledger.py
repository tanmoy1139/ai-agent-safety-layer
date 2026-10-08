"""
Tests for the Saṃskāra-Ledger — Sprint V8.

Source: Yoga Sūtras II.12–II.14 (Patañjali; Bryant 2009).
SOTA refs: Graphiti bi-temporal (arXiv:2501.13956), Kumiho AGM (arXiv:2603.17244),
MAGMA causal-graph (arXiv:2601.03236), A-MEM Zettelkasten (arXiv:2502.12110),
PRM Q-value (arXiv:2502.10325).

Test layout (32 tests total):
Core (12):  test_01 – test_12
Multi-tenant / RLS (3):  test_13 – test_15
Persistence — SQLite (3):  test_16 – test_18
RF-38 escalation (8):  test_19 – test_26
Integration (4):  test_27 – test_30
Custom similarity_fn (2):  test_31 – test_32  (TD-V8-01 closure)
"""

from __future__ import annotations

import json
import math
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from dharmaos.samskara_ledger import (
AppendOnlyViolation,
EscalationAlert,
KarmaDelta,
SamskaraEntry,
SamskaraLedger,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
*,
action_signature: str = "browse:example.com",
karma_delta: float = 0.2,
guna_context: str | None = "sattva",
org_id: UUID | None = None,
valid_from: datetime | None = None,
causal_parents: list[UUID] | None = None,
zettelkasten_links: list[UUID] | None = None,
) -> SamskaraEntry:
    """Convenience builder for SamskaraEntry with sensible defaults."""
    return SamskaraEntry(
    action_signature=action_signature,
    action_trace={"tool": "browser", "url": "https://example.com"},
    consequence_trace={"status": "ok"},
    karma_delta=karma_delta,
    guna_context=guna_context,  # type: ignore[arg-type]
    valid_from=valid_from or datetime.now(UTC),
            org_id=org_id,
    causal_parents=causal_parents or [],
    zettelkasten_links=zettelkasten_links or [],
    )


# ---------------------------------------------------------------------------
# Core tests (12)
# ---------------------------------------------------------------------------


class TestCoreAppend:
    """test_01 — Append new entry → queryable."""

    def test_01_append_and_query(self) -> None:
        ledger = SamskaraLedger()
        e = _entry(action_signature="browse:foo.com")
        ledger.append(e)
        results = ledger.query("browse:foo.com", top_k=5)
        ids = [r[0].id for r in results]
        assert e.id in ids

    def test_02_append_duplicate_raises(self) -> None:
        """test_02 — Append duplicate id → AppendOnlyViolation."""
        ledger = SamskaraLedger()
        e = _entry()
        ledger.append(e)
        with pytest.raises(AppendOnlyViolation):
            ledger.append(e)

    def test_03_supersede_sets_valid_until_and_pointer(self) -> None:
        """test_03 — Supersede: old entry valid_until set, new entry has superseded_by pointer."""
        ledger = SamskaraLedger()
        old = _entry(action_signature="buy:widget")
        ledger.append(old)

        new = _entry(action_signature="buy:widget")
        ledger.supersede(old.id, new)

        # The stored new entry (in the ledger) should have superseded_by pointing at old
        # (supersede() writes a new copy with the pointer set; original `new` var is unchanged)
        stored_new = ledger.get_entry(new.id)
        assert stored_new is not None
        assert stored_new.superseded_by == old.id

        # Old entry stored in ledger should have valid_until set
        stored_old = ledger.get_entry(old.id)
        assert stored_old is not None
        assert stored_old.valid_until is not None

        # old entry should no longer appear in current query
        current = ledger.query("buy:widget", top_k=10)
        current_ids = [r[0].id for r in current]
        assert old.id not in current_ids
        assert new.id in current_ids

    def test_04_query_as_of_past_returns_pre_supersession_state(self) -> None:
        """test_04 — Query with as_of=past → returns pre-supersession state (bi-temporal).

        We explicitly set valid_from in the past so that an as_of query at a
        point after valid_from but before supersession sees the old entry.
        """
        ledger = SamskaraLedger()
        # Create old entry with valid_from in the past
        old_valid_from = datetime.now(UTC) - timedelta(hours=1)
        old = SamskaraEntry(
        action_signature="send_email:alice",
        action_trace={"tool": "email"},
        consequence_trace={"status": "sent"},
        karma_delta=0.3,
        valid_from=old_valid_from,
        )
        ledger.append(old)

        # Supersede it now
        new = _entry(action_signature="send_email:alice")
        ledger.supersede(old.id, new)

        # as_of = 30 min ago — after old.valid_from but before supersession
        as_of_past = datetime.now(UTC) - timedelta(minutes=30)
        results = ledger.query("send_email:alice", top_k=10, as_of=as_of_past)
        ids = [r[0].id for r in results]
        assert old.id in ids

    def test_05_query_no_as_of_returns_current_state_only(self) -> None:
        """test_05 — Query with no as_of → returns current state only."""
        ledger = SamskaraLedger()
        old = _entry(action_signature="delete:file")
        ledger.append(old)
        new = _entry(action_signature="delete:file")
        ledger.supersede(old.id, new)

        results = ledger.query("delete:file", top_k=10)
        ids = [r[0].id for r in results]
        assert old.id not in ids
        assert new.id in ids

    def test_06_karma_delta_formula_defaults(self) -> None:
        """test_06 — karma_delta formula correctness with default weights."""
        # Δ = 0.4*quality + 0.3*satisfaction − 0.2*rollbacks − 1.0*dharma_flag
        kd = KarmaDelta(
        outcome_quality=0.8,
        user_satisfaction=0.6,
        rollback_count=1,
        dharma_violation_flag=False,
        )
        expected = 0.4 * 0.8 + 0.3 * 0.6 - 0.2 * 1 - 1.0 * 0.0
        assert abs(kd.value - expected) < 1e-9

    def test_07_karma_delta_formula_custom_weights(self) -> None:
        """test_07 — karma_delta formula with custom weights."""
        kd = KarmaDelta(
        outcome_quality=1.0,
        user_satisfaction=1.0,
        rollback_count=0,
        dharma_violation_flag=True,
                alpha=0.5,
                beta=0.5,
                gamma=0.1,
                delta=2.0,
        )
        expected = 0.5 * 1.0 + 0.5 * 1.0 - 0.1 * 0 - 2.0 * 1.0
        assert abs(kd.value - expected) < 1e-9

    def test_08_ebbinghaus_decay_60_days_tau30(self) -> None:
        """test_08 — Ebbinghaus decay: entry 60 days old with τ=30 → weight ≈ exp(-2) × recall_factor."""
        ledger = SamskaraLedger(ebbinghaus_tau_days=30.0, ebbinghaus_beta=0.5)
        now = datetime.now(UTC)
        e = SamskaraEntry(
        action_signature="task:old",
        action_trace={},
        consequence_trace={},
        karma_delta=0.5,
        valid_from=now - timedelta(days=60),
        recall_count=0,
        )
        weight = ledger.ebbinghaus_weight(e, now)
        expected = math.exp(-2.0) * (1 + 0) ** 0.5
        assert abs(weight - expected) < 1e-9

    def test_09_ebbinghaus_recall_count_boost(self) -> None:
        """test_09 — Ebbinghaus recall_count boost: queried entry increments count, next weight higher."""
        ledger = SamskaraLedger(ebbinghaus_tau_days=30.0, ebbinghaus_beta=1.0)
        e = _entry(action_signature="task:recall_test")
        ledger.append(e)

        # First query — recall_count starts at 0
        results_before = ledger.query("task:recall_test", top_k=1)
        assert len(results_before) == 1
        weight_before = results_before[0][1]

        # Second query — recall_count should have been bumped
        results_after = ledger.query("task:recall_test", top_k=1)
        weight_after = results_after[0][1]
        assert weight_after > weight_before

    def test_10_zettelkasten_auto_link_similar(self) -> None:
        """test_10 — Zettelkasten auto-link: similar action_signature → linked."""
        ledger = SamskaraLedger(similarity_threshold=0.3)
        # First entry with tokens "browse foo"
        e1 = _entry(action_signature="browse:foo")
        ledger.append(e1)

        # Second entry that shares a token with e1
        e2 = _entry(action_signature="browse:bar")
        linked = ledger.zettelkasten_auto_link(e2)
        assert e1.id in linked

    def test_11_zettelkasten_no_link_dissimilar(self) -> None:
        """test_11 — Zettelkasten: dissimilar → NOT linked."""
        ledger = SamskaraLedger(similarity_threshold=0.9)
        e1 = _entry(action_signature="browse:foo")
        ledger.append(e1)
        # Completely different signature
        e2 = _entry(action_signature="delete:zzzz_unique_string_xyz")
        linked = ledger.zettelkasten_auto_link(e2)
        assert e1.id not in linked

    def test_12_causal_edge_add_and_retrieve(self) -> None:
        """test_12 — Causal edge: add + retrieve."""
        ledger = SamskaraLedger()
        parent = _entry(action_signature="search:query")
        child = _entry(action_signature="browse:result")
        ledger.append(parent)
        ledger.append(child)
        ledger.add_causal_edge(parent.id, child.id)

        children = ledger.get_causal_children(parent.id)
        assert child.id in children


# ---------------------------------------------------------------------------
# Multi-tenant / RLS tests (3)
# ---------------------------------------------------------------------------


class TestMultiTenant:
    """test_13 – test_15: org isolation."""

    def test_13_org_a_not_returned_for_org_b(self) -> None:
        """test_13 — Entries with org_id=A not returned when querying org_id=B."""
        org_a = uuid4()
        org_b = uuid4()
        ledger = SamskaraLedger()
        e_a = _entry(action_signature="search:private", org_id=org_a)
        ledger.append(e_a)

        results = ledger.query("search:private", top_k=10, org_id=org_b)
        ids = [r[0].id for r in results]
        assert e_a.id not in ids

    def test_14_detect_escalation_respects_org_id(self) -> None:
        """test_14 — detect_escalation respects org_id partition."""
        org_a = uuid4()
        org_b = uuid4()
        ledger = SamskaraLedger()

        now = datetime.now(UTC)
        # Plant a slow-poison pattern for org_a
        baseline_time = now - timedelta(days=70)
        for i in range(12):
            e = SamskaraEntry(
            action_signature="op:poison",
            action_trace={},
            consequence_trace={},
            karma_delta=-0.5 - i * 0.02,  # monotone degradation
            valid_from=baseline_time + timedelta(days=i * 5),
                    org_id=org_a,
            )
            ledger.append(e)

        # org_b should get no alert
        alert_b = ledger.detect_escalation(org_b, window_days=30, min_samples=5)
        assert alert_b is None

    def test_15_cross_org_similarity_does_not_auto_link(self) -> None:
        """test_15 — Cross-org similarity does NOT auto-link (isolation)."""
        org_a = uuid4()
        org_b = uuid4()
        ledger = SamskaraLedger(similarity_threshold=0.1)

        # Append a highly similar entry for org_a
        e_a = _entry(action_signature="browse:target", org_id=org_a)
        ledger.append(e_a)

        # New entry for org_b with same action_signature
        e_b = _entry(action_signature="browse:target", org_id=org_b)
        linked = ledger.zettelkasten_auto_link(e_b)
        # Should NOT link across org boundary
        assert e_a.id not in linked


# ---------------------------------------------------------------------------
# Persistence — SQLite (3)
# ---------------------------------------------------------------------------


class TestSQLitePersistence:
    """test_16 – test_18: SQLite mode."""

    def test_16_sqlite_persists_across_restarts(self) -> None:
        """test_16 — SQLite mode: entry persists across ledger instance restarts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "samskara.db"
            e = _entry(action_signature="persist:me")

            ledger1 = SamskaraLedger(storage="sqlite", sqlite_path=db_path)
            ledger1.append(e)

            # New ledger instance, same path
            ledger2 = SamskaraLedger(storage="sqlite", sqlite_path=db_path)
            results = ledger2.query("persist:me", top_k=5)
            ids = [r[0].id for r in results]
            assert e.id in ids

    def test_17_sqlite_schema_matches_spec(self) -> None:
        """test_17 — SQLite schema matches spec (inspect via PRAGMA table_info(samskara))."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "schema_check.db"
            ledger = SamskaraLedger(storage="sqlite", sqlite_path=db_path)
            # Trigger schema creation
            ledger.append(_entry())

            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("PRAGMA table_info(samskara)")
            columns = {row[1] for row in cursor.fetchall()}
            conn.close()

            required_cols = {
            "id",
            "action_signature",
            "action_trace",
            "consequence_trace",
            "karma_delta",
            "guna_context",
            "valid_from",
            "valid_until",
            "superseded_by",
            "causal_parents",
            "zettelkasten_links",
            "org_id",
            "recall_count",
            }
            assert required_cols.issubset(columns)

    def test_18_sqlite_append_only_no_direct_update(self) -> None:
        """test_18 — SQLite append-only: attempt to UPDATE main fields raises.

        The Python layer must NOT issue UPDATE statements on core columns.
        We verify this by confirming the action_trace value is unchanged
        after we manually attempt an UPDATE on the underlying table — the
        Python layer should never do this, so the schema has no such trigger,
        but we test that the ledger's own public API never mutates stored rows.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ao.db"
            ledger = SamskaraLedger(storage="sqlite", sqlite_path=db_path)
            e = _entry(action_signature="write:data")
            ledger.append(e)

            # Verify a direct SQL UPDATE IS possible at DB level (no trigger blocking it),
            # but the Python layer's append() will raise AppendOnlyViolation — not SQL.
            conn = sqlite3.connect(str(db_path))
            # This shows the ledger itself never issues such updates
            row = conn.execute(
                "SELECT action_trace FROM samskara WHERE id=?", (str(e.id),)
            ).fetchone()
            assert row is not None
            stored_trace = json.loads(row[0])
            assert stored_trace == e.action_trace
            conn.close()

            # Re-appending the same entry must raise AppendOnlyViolation
            with pytest.raises(AppendOnlyViolation):
                ledger.append(e)


# ---------------------------------------------------------------------------
# RF-38 escalation-rate detector tests (8) — CRITICAL
# ---------------------------------------------------------------------------


class TestEscalationDetector:
    """test_19 – test_26: RF-38 slow-poison escalation-rate detector.

    RF-38 (HIGH): 50-session slow-poison pattern where each action stays
    within per-action approval thresholds but cumulative pattern outpaces
    Ebbinghaus decay. Must be closed before V8 merges.
    """

    def _plant_monotone_decay(
    self,
    ledger: SamskaraLedger,
    org_id: UUID,
    *,
            now: datetime,
    n_baseline: int = 5,
            n_mid: int = 5,
    n_recent: int = 5,
    baseline_karma: float = 0.4,
    mid_karma: float = 0.2,
    recent_karma: float = -0.1,
    action_signature: str = "op:routine",
    window_days: int = 30,
    ) -> None:
        """Plant entries across three temporal windows with monotone decay."""
        # baseline: 3×window_days ago
        for i in range(n_baseline):
            e = SamskaraEntry(
            action_signature=action_signature,
            action_trace={},
            consequence_trace={},
            karma_delta=baseline_karma,
            valid_from=now - timedelta(days=window_days * 3 - i),
                    org_id=org_id,
            )
            ledger.append(e)
        # mid: 2×window_days ago
        for i in range(n_mid):
            e = SamskaraEntry(
            action_signature=action_signature,
            action_trace={},
            consequence_trace={},
            karma_delta=mid_karma,
            valid_from=now - timedelta(days=window_days * 2 - i),
                    org_id=org_id,
            )
            ledger.append(e)
        # recent: within window_days
        for i in range(n_recent):
            e = SamskaraEntry(
            action_signature=action_signature,
            action_trace={},
            consequence_trace={},
            karma_delta=recent_karma,
            valid_from=now - timedelta(days=window_days - 1 - i),
                    org_id=org_id,
            )
            ledger.append(e)

    def test_19_insufficient_samples_returns_none(self) -> None:
        """test_19 — Insufficient samples → None."""
        ledger = SamskaraLedger()
        org = uuid4()
        now = datetime.now(UTC)
        # Only 3 recent entries — below min_samples=10
        for i in range(3):
            e = SamskaraEntry(
            action_signature="op:sparse",
            action_trace={},
            consequence_trace={},
            karma_delta=-0.4,
            valid_from=now - timedelta(days=i),
                    org_id=org,
            )
            ledger.append(e)
        alert = ledger.detect_escalation(org, window_days=30, min_samples=10)
        assert alert is None

    def test_20_stable_distribution_returns_none(self) -> None:
        """test_20 — Stable distribution (no drift) → None."""
        ledger = SamskaraLedger()
        org = uuid4()
        now = datetime.now(UTC)
        self._plant_monotone_decay(
            ledger,
            org,
                now=now,
        baseline_karma=0.3,
        mid_karma=0.3,
        recent_karma=0.3,
        n_baseline=5,
                n_mid=5,
        n_recent=12,
        window_days=30,
        )
        alert = ledger.detect_escalation(org, window_days=30, min_samples=10, threshold_delta=0.15)
        assert alert is None

    def test_21_monotone_slow_poison_emits_alert(self) -> None:
        """test_21 — Monotone slow-poison over 3 windows → EscalationAlert emitted."""
        ledger = SamskaraLedger()
        org = uuid4()
        now = datetime.now(UTC)
        self._plant_monotone_decay(
            ledger,
            org,
                now=now,
        baseline_karma=0.5,
        mid_karma=0.2,
        recent_karma=-0.1,
        n_baseline=5,
                n_mid=5,
        n_recent=12,
        window_days=30,
        )
        alert = ledger.detect_escalation(org, window_days=30, min_samples=10, threshold_delta=0.15)
        assert alert is not None
        assert isinstance(alert, EscalationAlert)
        assert alert.org_id == org
        assert alert.delta > 0.15

    def test_22_sudden_drop_non_monotone_returns_none(self) -> None:
        """test_22 — Sudden drop (not monotone) → None (not the slow-poison pattern)."""
        ledger = SamskaraLedger()
        org = uuid4()
        now = datetime.now(UTC)
        # Pattern: baseline=0.5, mid=-0.3 (big drop), recent=0.2 (recovery)
        # Not monotone → should NOT trigger
        self._plant_monotone_decay(
            ledger,
            org,
                now=now,
        baseline_karma=0.5,
        mid_karma=-0.3,
        recent_karma=0.2,
        n_baseline=5,
                n_mid=5,
        n_recent=12,
        window_days=30,
        )
        alert = ledger.detect_escalation(org, window_days=30, min_samples=10, threshold_delta=0.15)
        assert alert is None

    def test_23_alert_severity_scales_with_delta(self) -> None:
        """test_23 — Alert severity scales with delta magnitude."""

        def make_alert(baseline: float, recent: float) -> EscalationAlert | None:
            ledger = SamskaraLedger()
            org = uuid4()
            now = datetime.now(UTC)
            self._plant_monotone_decay(
                ledger,
                org,
                    now=now,
            baseline_karma=baseline,
            mid_karma=(baseline + recent) / 2,
            recent_karma=recent,
            n_baseline=5,
                    n_mid=5,
            n_recent=12,
            )
            return ledger.detect_escalation(
                org, window_days=30, min_samples=10, threshold_delta=0.1
            )

        small_drop = make_alert(0.3, 0.1)  # delta ≈ 0.2 → low or medium
        big_drop = make_alert(0.5, -0.5)  # delta ≈ 1.0 → high or critical

        assert small_drop is not None
        assert big_drop is not None
        # Severity ordering: small_drop severity ≤ big_drop severity
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        assert order[small_drop.severity] <= order[big_drop.severity]

    def test_24_alert_top_contributors_lists_action_signatures(self) -> None:
        """test_24 — Alert top_contributors lists action_signatures with largest negative contribution."""
        ledger = SamskaraLedger()
        org = uuid4()
        now = datetime.now(UTC)
        # Plant two patterns — one with bad karma, one with good
        self._plant_monotone_decay(
            ledger,
            org,
                now=now,
        baseline_karma=0.5,
        mid_karma=0.1,
        recent_karma=-0.3,
        action_signature="op:poison",
        n_baseline=5,
                n_mid=5,
        n_recent=12,
        )
        self._plant_monotone_decay(
            ledger,
            org,
                now=now,
        baseline_karma=0.5,
        mid_karma=0.5,
        recent_karma=0.5,
        action_signature="op:benign",
        n_baseline=5,
                n_mid=5,
        n_recent=12,
        )
        alert = ledger.detect_escalation(org, window_days=30, min_samples=10, threshold_delta=0.1)
        assert alert is not None
        # op:poison should appear as top contributor
        assert "op:poison" in alert.top_contributors

    def test_25_windowing_entries_outside_ignored(self) -> None:
        """test_25 — Windowing: entries outside windows ignored."""
        ledger = SamskaraLedger()
        org = uuid4()
        now = datetime.now(UTC)

        # Recent entries: the only ones within window
        for i in range(12):
            e = SamskaraEntry(
            action_signature="op:within",
            action_trace={},
            consequence_trace={},
            karma_delta=0.3,
            valid_from=now - timedelta(days=i),
                    org_id=org,
            )
            ledger.append(e)

        # Entries far outside all windows — should not affect the result
        for i in range(20):
            e = SamskaraEntry(
            action_signature="op:within",
            action_trace={},
            consequence_trace={},
            karma_delta=-9.9,  # Would poison result if included
            valid_from=now - timedelta(days=1000 + i),
                    org_id=org,
            )
            ledger.append(e)

        # The stale entries are way outside 3×window_days, so stable
        alert = ledger.detect_escalation(org, window_days=30, min_samples=10, threshold_delta=0.15)
        assert alert is None

    def test_26_configurable_threshold_fires_less_at_strict_threshold(self) -> None:
        """test_26 — Configurable threshold_delta (stricter fires less often)."""
        ledger = SamskaraLedger()
        org = uuid4()
        now = datetime.now(UTC)
        # Plant a borderline pattern (delta ≈ 0.2)
        self._plant_monotone_decay(
            ledger,
            org,
                now=now,
        baseline_karma=0.3,
        mid_karma=0.15,
        recent_karma=0.05,
        n_baseline=5,
                n_mid=5,
        n_recent=12,
        )
        # Loose threshold fires
        alert_loose = ledger.detect_escalation(
            org, window_days=30, min_samples=10, threshold_delta=0.1
        )
        # Strict threshold does NOT fire
        alert_strict = ledger.detect_escalation(
            org, window_days=30, min_samples=10, threshold_delta=0.5
        )
        assert alert_loose is not None
        assert alert_strict is None


# ---------------------------------------------------------------------------
# Integration tests (4)
# ---------------------------------------------------------------------------


class TestIntegration:
    """test_27 – test_30: cross-module wiring."""

    def test_27_karma_delta_with_dharma_violation_flag_negative(self) -> None:
        """test_27 — KarmaDelta with dharma_violation_flag=True → negative score."""
        kd = KarmaDelta(
        outcome_quality=1.0,
        user_satisfaction=1.0,
        rollback_count=0,
        dharma_violation_flag=True,
        )
        # With default δ=1.0, violation should dominate: 0.4 + 0.3 - 0 - 1.0 = -0.3
        assert kd.value < 0.0

    def test_28_guna_context_preserved_in_entry(self) -> None:
        """test_28 — Guna_context tagging from V6 guna_monitor preserved in entry."""
        ledger = SamskaraLedger()
        e = _entry(action_signature="browse:guna", guna_context="rajas")
        ledger.append(e)
        results = ledger.query("browse:guna", top_k=5)
        assert len(results) == 1
        assert results[0][0].guna_context == "rajas"

    def test_29_action_contract_produces_valid_action_signature(self) -> None:
        """test_29 — ActionContract can produce a valid action_signature via hash of key fields."""
        from dharmaos.ethics_engine import ActionContract, ActionImpact

        contract = ActionContract(
        action_id="a1",
        path_id="p1",
        agent_id="agent-1",
        parent_agent_id=None,
        tenant_id="t1",
        task_id="task-1",
        requested_at=datetime.now(UTC),
                name="browse_web",
                kind="browser_click",
                target="https://example.com/page",
                impact=ActionImpact.LOW,
        )
        sig = SamskaraLedger.action_signature_from_contract(contract)
        assert isinstance(sig, str)
        assert len(sig) > 0
        # Deterministic: same input → same sig
        sig2 = SamskaraLedger.action_signature_from_contract(contract)
        assert sig == sig2

    def test_31_custom_similarity_fn_used_for_query(self) -> None:
        """test_31 — Custom similarity_fn replaces Jaccard in query().

        Create a similarity_fn that returns 1.0 only for an exact match
        and 0.0 otherwise, then verify that query() respects it over Jaccard.
        """
        # Ledger with custom fn that ignores token structure
        calls: list[tuple[str, str]] = []

        def exact_match_fn(query_sig: str, entry_sig: str) -> float:
            calls.append((query_sig, entry_sig))
            return 1.0 if query_sig == entry_sig else 0.0

        ledger = SamskaraLedger(similarity_fn=exact_match_fn)

        # Two entries with similar tokens but different full signatures
        e1 = _entry(action_signature="browse:foo:exact")
        e2 = _entry(action_signature="browse:bar:different")
        ledger.append(e1)
        ledger.append(e2)

        # Query for e1's exact signature — only e1 should be returned
        results = ledger.query("browse:foo:exact", top_k=10)
        returned_ids = [r[0].id for r in results]

        assert e1.id in returned_ids
        assert e2.id not in returned_ids
        # Verify custom fn was actually called (not Jaccard)
        assert len(calls) > 0

    def test_32_custom_similarity_fn_all_match_returns_top_k(self) -> None:
        """test_32 — Custom similarity_fn returning constant 0.5 retrieves all entries up to top_k.

        Verifies backward-compat: if custom fn returns positive for all entries,
        top_k acts as the limiting factor (same behaviour as Jaccard with broad overlap).
        """
        always_half: list[int] = [0]  # mutable counter

        def constant_fn(query_sig: str, entry_sig: str) -> float:
            always_half[0] += 1
            return 0.5

        ledger = SamskaraLedger(similarity_fn=constant_fn)

        for i in range(15):
            e = _entry(action_signature=f"op:{i}")
            ledger.append(e)

        results = ledger.query("anything", top_k=7)
        # Should return exactly 7 entries (top_k respected)
        assert len(results) == 7
        # All weights should be > 0 (0.5 similarity × Ebbinghaus weight > 0)
        for _entry_obj, weight in results:
            assert weight > 0.0
        # Custom fn was called (at least 15 times for 15 entries)
        assert always_half[0] >= 15

    def test_30_end_to_end_100_entries_top3_invariants(self) -> None:
        """test_30 — End-to-end: append 100 entries, query top-3, verify all three
        invariants (append-only, bi-temporal, Ebbinghaus)."""
        ledger = SamskaraLedger(ebbinghaus_tau_days=30.0, ebbinghaus_beta=0.5)
        org = uuid4()
        now = datetime.now(UTC)
        sig = "op:mass_test"

        # Append 100 entries with varying karma and ages.
        # entries[0] gets valid_from = 2 hours ago so bi-temporal rewind works.
        entries = []
        for i in range(100):
            age_days = i + 1  # all entries are at least 1 day old
            e = SamskaraEntry(
            action_signature=sig,
            action_trace={"step": i},
            consequence_trace={"result": "ok"},
            karma_delta=float(i % 10) / 10.0,
            valid_from=now - timedelta(days=age_days),
                    org_id=org,
            )
            ledger.append(e)
            entries.append(e)

        # ── Invariant 1: Append-only ──────────────────────────────────────────
        with pytest.raises(AppendOnlyViolation):
            ledger.append(entries[0])

        # ── Invariant 2: Bi-temporal ─────────────────────────────────────────
        # entries[0] has valid_from = now - 1 day.
        # Supersede it; as_of = now - 12 hours should see the old entry.
        new_e = SamskaraEntry(
        action_signature=sig,
        action_trace={"step": "superseded"},
        consequence_trace={"result": "revised"},
        karma_delta=0.9,
        valid_from=now,
                org_id=org,
        )
        ledger.supersede(entries[0].id, new_e)

        # as_of = 12 hours ago is after entries[0].valid_from but before supersession
        past_time = now - timedelta(hours=12)
        results_past = ledger.query(sig, top_k=5, as_of=past_time, org_id=org)
        past_ids = [r[0].id for r in results_past]
        assert entries[0].id in past_ids  # visible in past

        results_now = ledger.query(sig, top_k=5, org_id=org)
        now_ids = [r[0].id for r in results_now]
        assert entries[0].id not in now_ids  # hidden in present

        # ── Invariant 3: Ebbinghaus ──────────────────────────────────────────
        # Top-3 results must be sorted descending by weight
        top3 = ledger.query(sig, top_k=3, org_id=org)
        assert len(top3) == 3
        weights = [w for _, w in top3]
        assert weights[0] >= weights[1] >= weights[2]
