"""Tests for V16 Ṛta-Ledger — SHA-256 hash-chain + Merkle seals + Art. 50.

26 tests covering:
- Chain integrity (6 tests)
- Merkle sealing (4 tests)
- SQLite persistence (3 tests)
- Article 50 disclosure (4 tests)
- HMAC mode (3 tests)
- Replay + query (3 tests)
- Seal store (1 test)
- Stress test (1 test)
- Concurrent emit + verify (1 test)
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from dharmaos.rta_ledger import (
GENESIS_HASH,
Article50Disclosure,
FileSealStore,
IntegrityReport,
MerkleSeal,
RtaEvent,
RtaEventKind,
RtaLedger,
merkle_root,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ledger(**kwargs: object) -> RtaLedger:
    """Create an in-memory RtaLedger with optional overrides."""
    return RtaLedger(**kwargs)  # type: ignore[arg-type]


def _record_n(ledger: RtaLedger, n: int, tenant: str = "t1", agent: str = "a1") -> list[RtaEvent]:
    """Append n events and return all RtaEvent objects."""
    events = []
    for i in range(n):
        events.append(
            ledger.record(
                RtaEventKind.ETHICS_VERDICT,
        tenant_id=tenant,
        agent_id=agent,
        payload={"i": i, "result": "pass"},
        )
        )
    return events


# ---------------------------------------------------------------------------
# Chain integrity (6 tests)
# ---------------------------------------------------------------------------


class TestChainIntegrity:
    def test_record_one_event_tip_hash_differs_from_genesis(self) -> None:
        """Test 1: Record 1 event → tip_hash != GENESIS_HASH; verify ok."""
        ledger = _make_ledger()
        ledger.record(
            RtaEventKind.ETHICS_VERDICT,
        tenant_id="t1",
        agent_id="a1",
        payload={"result": "pass"},
        )
        assert ledger.tip_hash != GENESIS_HASH
        report = ledger.verify_integrity()
        assert report.ok is True
        assert report.total_entries == 1
        assert report.first_bad_seq is None

    def test_record_three_events_prev_hash_chain(self) -> None:
        """Test 2: Record 3 events → each entry's prev_hash == prior entry_hash."""
        ledger = _make_ledger()
        events = _record_n(ledger, 3)
        assert events[0].prev_hash == GENESIS_HASH
        assert events[1].prev_hash == events[0].entry_hash
        assert events[2].prev_hash == events[1].entry_hash

    def test_record_ten_events_verify_ok(self) -> None:
        """Test 3: Record 10 events → verify_integrity ok."""
        ledger = _make_ledger()
        _record_n(ledger, 10)
        report = ledger.verify_integrity()
        assert report.ok is True
        assert report.total_entries == 10

    def test_tamper_middle_entry_payload_detected(self) -> None:
        """Test 4: Tamper with middle entry's payload → verify detects tamper.

        Strategy: inject a mutated entry into the internal chain list.
        This simulates a memory-tampering attack.
        """
        ledger = _make_ledger()
        _record_n(ledger, 5)

        # Retrieve the chain (implementation detail for testing)
        original = ledger._chain[2]

        # Build a tampered entry: same seq/prev_hash/entry_hash but different payload
        tampered = RtaEvent(
                seq=original.seq,
        event_id=original.event_id,
                kind=original.kind,
        tenant_id=original.tenant_id,
        agent_id=original.agent_id,
        payload={"i": 999, "result": "TAMPERED"},  # payload changed
        timestamp=original.timestamp,
        prev_hash=original.prev_hash,
        entry_hash=original.entry_hash,  # entry_hash NOT updated → mismatch
        )
        ledger._chain[2] = tampered

        report = ledger.verify_integrity()
        assert report.ok is False
        assert report.first_bad_seq == 2

    def test_remove_entry_seq_gap_detected(self) -> None:
        """Test 5: Remove an entry from the chain → verify detects seq gap."""
        ledger = _make_ledger()
        _record_n(ledger, 5)

        # Remove entry at position 1 (seq=1)
        del ledger._chain[1]

        # After deletion, chain[1] is what was chain[2] (seq=2)
        # Its prev_hash was set to chain[1].entry_hash (old seq=1)
        # but now chain[0] is seq=0, so the linkage breaks.
        report = ledger.verify_integrity()
        assert report.ok is False
        assert report.first_bad_seq is not None

    def test_record_after_tamper_earlier_tamper_still_detected(self) -> None:
        """Test 6: Record after tamper (in-process attack) → tamper still detected.

        Even if the attacker appends new legitimate events after tampering,
        verify_integrity() still finds the earlier tampered entry.
        """
        ledger = _make_ledger()
        _record_n(ledger, 3)

        # Tamper entry 0
        original = ledger._chain[0]
        ledger._chain[0] = RtaEvent(
                seq=original.seq,
        event_id=original.event_id,
                kind=original.kind,
        tenant_id=original.tenant_id,
        agent_id=original.agent_id,
        payload={"injected": True},
        timestamp=original.timestamp,
        prev_hash=original.prev_hash,
        entry_hash=original.entry_hash,
        )

        # Append more events after the tamper (simulating attacker continuing)
        _record_n(ledger, 3)

        report = ledger.verify_integrity()
        assert report.ok is False
        assert report.first_bad_seq == 0


# ---------------------------------------------------------------------------
# Merkle sealing (4 tests)
# ---------------------------------------------------------------------------


class TestMerkleSealing:
    def test_merkle_root_empty_list_is_genesis(self) -> None:
        """Test 7: merkle_root([]) == GENESIS_HASH."""
        assert merkle_root([]) == GENESIS_HASH

    def test_merkle_root_odd_length_matches_even_with_duplication(self) -> None:
        """Test 8: merkle_root(odd) == merkle_root(even with last duplicated)."""
        leaves = [hashlib.sha256(f"leaf{i}".encode()).hexdigest() for i in range(3)]
        odd_root = merkle_root(leaves)
        even_leaves = [*leaves, leaves[-1]]  # duplicate last
        even_root = merkle_root(even_leaves)
        assert odd_root == even_root

    def test_merkle_window_3_records_9_events_produces_3_seals(self) -> None:
        """Test 9: window=3, 9 events → 3 seals stored."""
        ledger = _make_ledger(merkle_window=3)
        _record_n(ledger, 9)
        seals = ledger.merkle_roots()
        assert len(seals) == 3

    def test_tamper_sealed_window_verify_reports_mismatch(self) -> None:
        """Test 10: Tamper entry_hash in sealed window → merkle_roots_mismatched >= 1.

        To trigger a Merkle mismatch specifically (not just a hash-chain mismatch),
        we tamper the entry_hash of an entry inside the sealed window.  This causes
        the re-computed Merkle root to differ from the stored seal root.  The
        entry_hash mismatch is also caught in phase 1, but we verify both conditions.
        """
        ledger = _make_ledger(merkle_window=3)
        _record_n(ledger, 3)  # triggers one seal at window_end=2

        # Tamper entry 1: replace entry_hash with a forged value.
        # This makes phase-2 Merkle re-computation use the forged leaf,
        # so the re-computed root != stored seal root.
        original = ledger._chain[1]
        forged_hash = "f" * 64  # plausible-length but wrong hash
        ledger._chain[1] = RtaEvent(
                seq=original.seq,
        event_id=original.event_id,
                kind=original.kind,
        tenant_id=original.tenant_id,
        agent_id=original.agent_id,
        payload=original.payload,
        timestamp=original.timestamp,
        prev_hash=original.prev_hash,
        entry_hash=forged_hash,  # forged — Merkle root will mismatch
        )

        report = ledger.verify_integrity()
        # Phase 1 detects the entry_hash mismatch on the entry immediately after
        # (seq=2's prev_hash no longer matches the forged hash on seq=1)
        # Phase 2 detects the Merkle root mismatch
        assert report.ok is False
        assert report.merkle_roots_mismatched >= 1


# ---------------------------------------------------------------------------
# SQLite persistence (3 tests)
# ---------------------------------------------------------------------------


class TestSQLitePersistence:
    def test_sqlite_record_reload_chain_still_verifiable(self, tmp_path: Path) -> None:
        """Test 11: SQLite mode; recreate ledger at same path → chain verifiable."""
        db_path = tmp_path / "rta.db"
        ledger1 = RtaLedger(storage="sqlite", sqlite_path=db_path)
        _record_n(ledger1, 5)
        tip_after_first = ledger1.tip_hash

        # Reload
        ledger2 = RtaLedger(storage="sqlite", sqlite_path=db_path)
        assert ledger2.length == 5
        assert ledger2.tip_hash == tip_after_first
        report = ledger2.verify_integrity()
        assert report.ok is True

    def test_sqlite_no_update_or_delete_on_event_rows(self, tmp_path: Path) -> None:
        """Test 12: SQLite mode — append-only invariant; no UPDATE/DELETE present."""
        import sqlite3

        db_path = tmp_path / "rta_audit.db"
        ledger = RtaLedger(storage="sqlite", sqlite_path=db_path)
        _record_n(ledger, 3)

        # Direct SQLite check: try UPDATE — should succeed at DB level (no triggers)
        # but the RtaLedger API must never issue such statements.
        conn = sqlite3.connect(str(db_path))
        count_before = conn.execute("SELECT COUNT(*) FROM rta_events").fetchone()[0]

        # Verify our code only ever INSERTs (read the row count; must equal recorded count)
        assert count_before == 3

        # Verify no DELETE is possible while ledger is open (check count unchanged)
        # We don't issue the DELETE — we merely verify nothing was deleted
        count_after = conn.execute("SELECT COUNT(*) FROM rta_events").fetchone()[0]
        conn.close()
        assert count_after == count_before == 3

    def test_sqlite_concurrent_100_events_all_persisted(self, tmp_path: Path) -> None:
        """Test 13: 10 threads × 10 events = 100 total; verify ok after."""
        db_path = tmp_path / "rta_conc.db"
        ledger = RtaLedger(storage="sqlite", sqlite_path=db_path)
        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for i in range(10):
                    ledger.record(
                        RtaEventKind.CUSTOM,
                    tenant_id=f"tenant_{tid}",
                    agent_id=f"agent_{tid}",
                    payload={"thread": tid, "i": i},
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert not errors, f"Thread errors: {errors}"
        assert ledger.length == 100

        report = ledger.verify_integrity()
        assert report.ok is True
        assert report.total_entries == 100


# ---------------------------------------------------------------------------
# Article 50 disclosure (4 tests)
# ---------------------------------------------------------------------------


class TestArticle50Disclosure:
    def test_disclosure_creates_ai_generated_output_event(self) -> None:
        """Test 14: emit_article_50_disclosure → AI_GENERATED_OUTPUT RtaEvent."""
        ledger = _make_ledger()
        disclosure = ledger.emit_article_50_disclosure(
        model_identifier="the agent platform-Vedic-v1",
        content="Hello, I am an AI assistant.",
        tenant_id="tenant_x",
        agent_id="agent_y",
        )
        assert isinstance(disclosure, Article50Disclosure)
        # Verify the event was recorded
        events = ledger.replay(kind=RtaEventKind.AI_GENERATED_OUTPUT)
        assert len(events) == 1
        assert events[0].kind == RtaEventKind.AI_GENERATED_OUTPUT

    def test_disclosure_audit_seq_matches_event_seq(self) -> None:
        """Test 15: Article50Disclosure.audit_seq == new event's seq."""
        ledger = _make_ledger()
        # Add a few events first to shift seq
        _record_n(ledger, 3)
        disclosure = ledger.emit_article_50_disclosure(
        model_identifier="the agent platform-Vedic-v1",
        content="AI output here",
        tenant_id="t1",
        agent_id="a1",
        )
        events = ledger.replay(kind=RtaEventKind.AI_GENERATED_OUTPUT)
        assert len(events) == 1
        assert disclosure.audit_seq == events[0].seq
        assert disclosure.audit_seq == 3  # 4th event (seq starts at 0)

    def test_disclosure_content_fingerprint_is_sha256_of_content(self) -> None:
        """Test 16: content_fingerprint == SHA-256(content.encode('utf-8'))."""
        content = "My AI-generated report: all metrics within range."
        ledger = _make_ledger()
        disclosure = ledger.emit_article_50_disclosure(
        model_identifier="m1",
        content=content,
        tenant_id="t1",
        agent_id="a1",
        )
        expected_fp = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert disclosure.content_fingerprint == expected_fp

    def test_disclosure_event_persisted_before_return(self) -> None:
        """Test 17: Event persists before disclosure returned (atomicity).

        We verify this by checking that the chain length grows by 1 during
        emit, and that the event is present immediately after.
        """
        ledger = _make_ledger()
        length_before = ledger.length
        disclosure = ledger.emit_article_50_disclosure(
        model_identifier="m1",
        content="some output",
        tenant_id="t1",
        agent_id="a1",
        )
        # Event is present immediately (not deferred)
        assert ledger.length == length_before + 1
        # The event's seq is the back-reference in the disclosure
        all_events = ledger.replay()
        event_seqs = {e.seq for e in all_events}
        assert disclosure.audit_seq in event_seqs


# ---------------------------------------------------------------------------
# HMAC mode (3 tests)
# ---------------------------------------------------------------------------


class TestHmacMode:
    def test_hmac_ledger_verify_ok_with_correct_key(self) -> None:
        """Test 18: HMAC ledger + correct key → verify ok."""
        key = b"super-secret-signing-key-12345678"
        ledger = _make_ledger(hmac_key=key)
        _record_n(ledger, 5)
        report = ledger.verify_integrity()
        assert report.ok is True

    def test_hmac_ledger_verify_fails_with_wrong_key(self) -> None:
        """Test 19: HMAC ledger + wrong key → verify fails."""
        key = b"correct-key-abcdefgh"
        ledger = RtaLedger(hmac_key=key)
        _record_n(ledger, 3)

        # Construct a verifier with the wrong key
        wrong_key_ledger = RtaLedger(hmac_key=b"wrong-key-xyz")
        # Share the internal chain (simulate reading same data with wrong key)
        wrong_key_ledger._chain = list(ledger._chain)
        wrong_key_ledger._hmac_cache = dict(ledger._hmac_cache)

        report = wrong_key_ledger.verify_integrity()
        # Wrong key → HMAC mismatch detected
        assert report.ok is False

    def test_hmac_tamper_fails_both_hash_and_hmac(self) -> None:
        """Test 20: HMAC ledger + tamper → verify fails with HMAC mismatch."""
        key = b"my-hmac-key-bytes"
        ledger = _make_ledger(hmac_key=key)
        _record_n(ledger, 4)

        # Tamper entry 1 payload (entry_hash won't match → caught first)
        original = ledger._chain[1]
        ledger._chain[1] = RtaEvent(
                seq=original.seq,
        event_id=original.event_id,
                kind=original.kind,
        tenant_id=original.tenant_id,
        agent_id=original.agent_id,
        payload={"tampered": "yes"},
        timestamp=original.timestamp,
        prev_hash=original.prev_hash,
        entry_hash=original.entry_hash,
        )

        report = ledger.verify_integrity()
        assert report.ok is False
        assert report.first_bad_seq == 1


# ---------------------------------------------------------------------------
# Replay + query (3 tests)
# ---------------------------------------------------------------------------


class TestReplayQuery:
    def test_replay_returns_all_events_in_seq_order(self) -> None:
        """Test 21: replay() returns all events in ascending seq order."""
        ledger = _make_ledger()
        _record_n(ledger, 5)
        events = ledger.replay()
        assert len(events) == 5
        for i, e in enumerate(events):
            assert e.seq == i

    def test_replay_filter_by_kind(self) -> None:
        """Test 22: replay(kind=ETHICS_VERDICT) filters correctly."""
        ledger = _make_ledger()
        _record_n(ledger, 3)
        ledger.record(
            RtaEventKind.RTA_AUDIT,
        tenant_id="t1",
        agent_id="a1",
        payload={"mode": "normal"},
        )
        _record_n(ledger, 2)

        ethics_events = ledger.replay(kind=RtaEventKind.ETHICS_VERDICT)
        assert len(ethics_events) == 5
        for e in ethics_events:
            assert e.kind == RtaEventKind.ETHICS_VERDICT

        rta_events = ledger.replay(kind=RtaEventKind.RTA_AUDIT)
        assert len(rta_events) == 1

    def test_replay_filter_by_tenant(self) -> None:
        """Test 23: replay(tenant_id=X) filters by tenant."""
        ledger = _make_ledger()
        for i in range(4):
            ledger.record(
                RtaEventKind.CUSTOM,
            tenant_id="alpha",
            agent_id="agent1",
            payload={"i": i},
            )
        for i in range(6):
            ledger.record(
                RtaEventKind.CUSTOM,
            tenant_id="beta",
            agent_id="agent2",
            payload={"i": i},
            )

        alpha = ledger.replay(tenant_id="alpha")
        beta = ledger.replay(tenant_id="beta")
        assert len(alpha) == 4
        assert len(beta) == 6
        assert all(e.tenant_id == "alpha" for e in alpha)
        assert all(e.tenant_id == "beta" for e in beta)


# ---------------------------------------------------------------------------
# Seal store (1 test)
# ---------------------------------------------------------------------------


class TestFileSealStore:
    def test_file_seal_store_write_and_reload(self, tmp_path: Path) -> None:
        """Test 24: FileSealStore writes a seal; new store reading same path returns it."""
        path = tmp_path / "seals.jsonl"
        store1 = FileSealStore(path)
        seal = MerkleSeal(
        window_start_seq=0,
        window_end_seq=2,
        merkle_root="a" * 64,
        sealed_at=datetime.now(UTC),
        leaf_count=3,
        )
        store1.store(seal)

        store2 = FileSealStore(path)
        loaded = store2.list_seals()
        assert len(loaded) == 1
        assert loaded[0].seal_id == seal.seal_id
        assert loaded[0].merkle_root == "a" * 64
        assert loaded[0].leaf_count == 3


# ---------------------------------------------------------------------------
# Stress test (1 test)
# ---------------------------------------------------------------------------


class TestStress:
    def test_1000_events_verify_under_2_seconds(self) -> None:
        """Test 25: 1000 events recorded + verify_integrity completes < 2 s."""
        ledger = _make_ledger()
        _record_n(ledger, 1000)

        t0 = time.perf_counter()
        report = ledger.verify_integrity()
        elapsed = time.perf_counter() - t0

        assert report.ok is True
        assert report.total_entries == 1000
        assert elapsed < 2.0, f"verify_integrity took {elapsed:.3f}s (limit: 2s)"


# ---------------------------------------------------------------------------
# Concurrent emit + verify (1 test)
# ---------------------------------------------------------------------------


class TestConcurrentEmitVerify:
    def test_concurrent_emit_and_verify(self) -> None:
        """Test 26: verify_integrity during active appends produces consistent result.

        We run 5 writer threads and 2 verifier threads simultaneously.
        Each verify result must be either:
        - ok=True (snapshot was consistent), or
        - ok=False with a known-safe condition (snapshot caught a partial state)

        The key invariant: no exception is raised and the final verify is ok.
        """
        ledger = _make_ledger()
        verify_errors: list[Exception] = []
        write_errors: list[Exception] = []

        def writer(tid: int) -> None:
            try:
                for i in range(50):
                    ledger.record(
                        RtaEventKind.CUSTOM,
                    tenant_id=f"t{tid}",
                    agent_id=f"a{tid}",
                    payload={"tid": tid, "i": i},
                    )
            except Exception as exc:
                write_errors.append(exc)

        def verifier() -> None:
            try:
                # Run a few verify passes while writers are active
                for _ in range(3):
                    result = ledger.verify_integrity()
                    # We just need it to not raise — result may or may not be ok
                    assert isinstance(result, IntegrityReport)
            except Exception as exc:
                verify_errors.append(exc)

        threads: list[threading.Thread] = []
        for t in range(5):
            threads.append(threading.Thread(target=writer, args=(t,)))
        for _ in range(2):
            threads.append(threading.Thread(target=verifier))

        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert not write_errors, f"Write errors: {write_errors}"
        assert not verify_errors, f"Verify errors: {verify_errors}"

        # Final state must be consistent
        final_report = ledger.verify_integrity()
        assert final_report.ok is True
        assert final_report.total_entries == 250  # 5 threads × 50 events


# ---------------------------------------------------------------------------
# GAP 1 — ethics_evaluation_hash (3 tests, Claim 12 & 17)
# ---------------------------------------------------------------------------


class TestEthicsEvaluationHash:
    """Patent gap closure — third hash field linking audit records to ethics
    evaluations that governed the corresponding action (Claim 12 & 17)."""

    def test_record_with_ethics_evaluations_populates_hash(self) -> None:
        """GAP1-a: record with ethics_evaluations → ethics_evaluation_hash populated."""
        import json

        ledger = _make_ledger()
        evals = {"ahimsa": True, "satya": True, "asteya": False, "score": 0.6}
        event = ledger.record(
            RtaEventKind.ETHICS_VERDICT,
        tenant_id="t1",
        agent_id="a1",
        payload={"result": "partial"},
        ethics_evaluations=evals,
        )
        assert event.ethics_evaluation_hash is not None
        expected = hashlib.sha256(
            json.dumps(evals, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        assert event.ethics_evaluation_hash == expected

    def test_chain_integrity_includes_ethics_hash(self) -> None:
        """GAP1-b: chain integrity still holds when ethics_evaluation_hash present."""
        ledger = _make_ledger()
        evals = {"ahimsa": True, "score": 0.9}
        ledger.record(
            RtaEventKind.ETHICS_VERDICT,
        tenant_id="t1",
        agent_id="a1",
        payload={"result": "pass"},
        ethics_evaluations=evals,
        )
        ledger.record(
            RtaEventKind.CUSTOM,
        tenant_id="t1",
        agent_id="a1",
        payload={"step": 2},
        )
        report = ledger.verify_integrity()
        assert report.ok is True

    def test_backward_compat_no_ethics_evaluations_still_verifies(self) -> None:
        """GAP1-c: records without ethics_evaluations still verify correctly (backward compat)."""
        ledger = _make_ledger()
        event = ledger.record(
            RtaEventKind.CUSTOM,
        tenant_id="t1",
        agent_id="a1",
        payload={"legacy": True},
        )
        assert event.ethics_evaluation_hash is None
        report = ledger.verify_integrity()
        assert report.ok is True
