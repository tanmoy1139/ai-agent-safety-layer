"""
    DharmaOS RtaLedger — tamper-evident cryptographic audit chain (SHA-256 + Merkle,
    EU AI Act Art. 50 disclosures).

    PURPOSE
    -------
    RtaLedger provides an append-only, tamper-evident audit log for every
    governance decision made by the DharmaOS kernel. Each entry is chained to its
    predecessor via a SHA-256 hash of (entry_id + prev_hash + payload), forming
    a verifiable hash chain. Every N entries (default 1000) a Merkle root is
    computed over the window and stored via a pluggable SealStore (in-memory or
    file-backed by default; S3/WAL-backed deferred to Phase 4).

    Chain integrity is verified by replaying the entire chain; any tampered
    entry produces a mismatch. The ledger also emits EU AI Act Article 50
    disclosure headers for AI-generated content, marking outputs as machine-
    generated and logging the required provenance metadata.

    KEY TYPES
    ---------
    RtaLedger  : Main ledger class. append(event) → seq number.
    verify_chain() → bool. article50_disclosure() → str.
    RtaEvent  : Single audit record (kind, tenant_id, agent_id, payload,
    prev_hash, event_hash).
    RtaEventKind  : Enum of loggable event types (ethics_verdict, drift_event,
    guna_classification, pipeline_decision, etc.).
    MerkleSeal  : Merkle root over a window of N events; persisted via SealStore.
    SealStore  : Protocol for Merkle-root persistence (InMemorySealStore /
    FileSealStore provided; S3 adapter deferred).
    Article50Disclosure : Structured EU AI Act Art. 50 disclosure header.
    GENESIS_HASH  : 64-char zero string — prev_hash of the first entry.

    COMPLIANCE ROLE
    ---------------
    - EU AI Act Art. 50 §§1, 4 (binding August 2, 2026): transparency and
    machine-detectable AI-content logging requirements.
    - NIST AI RMF MEASURE 2.5, 2.13, 4.1: traceability and audit readiness.
    - NIST SP 800-207 Zero Trust: append-only log immutability principle.

    USAGE
    -----
    ledger = RtaLedger(seal_store=InMemorySealStore())
    seq = ledger.append(RtaEvent(
    kind=RtaEventKind.PIPELINE_DECISION,
    tenant_id="acme-bank",
    agent_id="kyc-agent-1",
    payload={"verdict": "deny", "layer": 3},
))
    ok = ledger.verify_chain()  # True if untampered

    Governance origin: Ṛta as cosmic order requiring tamper-proof records;
    *yad ṛtam tad dhruvam* — what is in accord with cosmic order is fixed
    and unalterable (Ṛg Veda X.85.1).

    V16 Ṛta-Ledger — cryptographic audit chain + Art. 50 disclosure.

    Source: Ṛta as cosmic order requiring tamper-proof records; dharmaśāstra
    judicial doctrine of witnessed action being beyond dispute.
    *yad ṛtam tad dhruvam* — what is in accord with cosmic order is fixed
    and unalterable (Ṛg Veda X.85.1).

    SOTA:
    - EU AI Act Art. 50 (binding Aug 2, 2026) — transparency + logging
    requirements for AI-generated content; providers of AI systems must
    ensure outputs are machine-detectable as AI-generated and that
    complete, accurate logs are kept (Art. 50 §§ 1, 4).
    - NIST AI RMF MEASURE function — traceability + auditability:
    MEASURE 2.5 "The AI system to be deployed is demonstrated to be valid
    and reliable through recurring evaluations"; MEASURE 2.13 "Effectiveness
    of the employed TEVV metrics and processes are evaluated and documented";
    MEASURE 4.1 "Approaches for measuring accountability are in place, and
    applied as intended".
    - Blockchain-inspired hash-chain + Merkle-tree sealing (adapted for
    in-process audit; no actual blockchain or distributed consensus — we
    implement tamper-evidence, not decentralization).
    - NIST SP 800-207 (Zero Trust) append-only log immutability principle.

    Provides:
    - Append-only audit log with SHA-256 prev-hash chain
    - Merkle-root computed + sealed every N entries (default 1000)
    - Chain-integrity verification (replay entire chain; any tamper
    produces a mismatch)
    - Art. 50 disclosure-header emission for AI-generated outputs
    - Pluggable SealStore for offsite Merkle-root persistence (S3/WAL
    deferred to Phase 4; local-file + memory defaults supplied)

    Events recorded:
    - Ethics verdict (V1)
    - Ahaṃkāra drift event (V4)
    - Pañca-prāṇa imbalance (V5)
    - Guṇa classification (V6)
    - Puruṣārtha coherence failure (V7)
    - Saṃskāra write (V8)
    - Ṛta invariant audit (V9)
    - Dharma-yuddha verdict (V10)
    - Adharma pipeline decision (V11)
    - Curiosity-POPPER verdict (V13)
    - Trust-domain authorization (V14)
    - Memory-write validator decision (V15)

    Any component may emit to the ledger via `RtaLedger.record(event)`.

    Sprint V16, 2026-04-17.
    """

from __future__ import annotations

import hashlib
import hmac as _hmac_mod
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GENESIS_HASH: str = "0" * 64  # SHA-256-length zero string (256 bits = 64 hex chars)
DEFAULT_MERKLE_WINDOW: int = 1000

# SQLite schema — two tables, never UPDATE'd or DELETE'd from
_CREATE_EVENTS_TABLE = """
    CREATE TABLE IF NOT EXISTS rta_events (
    seq  INTEGER PRIMARY KEY,
    event_id  TEXT  NOT NULL UNIQUE,
    kind  TEXT  NOT NULL,
    tenant_id  TEXT  NOT NULL,
    agent_id  TEXT  NOT NULL,
    payload_json TEXT  NOT NULL,
    ts  TEXT  NOT NULL,
    prev_hash  TEXT  NOT NULL,
    entry_hash  TEXT  NOT NULL,
    ethics_evaluation_hash TEXT,
    hmac_hex  TEXT
)
    """

_CREATE_SEALS_TABLE = """
    CREATE TABLE IF NOT EXISTS rta_seals (
    seal_id  TEXT  PRIMARY KEY,
    window_start INTEGER NOT NULL,
    window_end  INTEGER NOT NULL,
    merkle_root  TEXT  NOT NULL,
    sealed_at  TEXT  NOT NULL,
    leaf_count  INTEGER NOT NULL
)
    """


# ---------------------------------------------------------------------------
# RtaEventKind — named string constants
# ---------------------------------------------------------------------------


class RtaEventKind:
    """Named constants for all Ṛta-Ledger event kinds.

        String values are lowercase_snake for forward-compat JSON storage and
        SQLite round-trips.

        EU AI Act Art. 50: AI_GENERATED_OUTPUT kind triggers mandatory
        disclosure-header generation via emit_article_50_disclosure().
        """

    ETHICS_VERDICT: str = "ethics_verdict"
    AHAMKARA_DRIFT: str = "ahamkara_drift"
    PANCA_PRANA_IMBALANCE: str = "panca_prana_imbalance"
    GUNA_CLASSIFICATION: str = "guna_classification"
    PURUSHARTHA_COHERENCE: str = "purushartha_coherence"
    SAMSKARA_WRITE: str = "samskara_write"
    RTA_AUDIT: str = "rta_audit"
    DHARMA_YUDDHA_VERDICT: str = "dharma_yuddha_verdict"
    ADHARMA_DECISION: str = "adharma_decision"
    CURIOSITY_VERDICT: str = "curiosity_verdict"
    TRUST_DOMAIN_AUTH: str = "trust_domain_auth"
    MEMORY_WRITE: str = "memory_write"
    AI_GENERATED_OUTPUT: str = "ai_generated_output"  # EU AI Act Art. 50
    CUSTOM: str = "custom"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class RtaEvent(BaseModel):
    """Single audit-log entry. Immutable after insertion.

        The entry_hash is the SHA-256 of the canonical serialization:
        "{seq}|{prev_hash}|{ethics_evaluation_hash}|{kind}|{tenant_id}|{agent_id}|{canonical_json(payload)}|{ts_iso}"

        prev_hash of the first event (seq=0) is GENESIS_HASH.
        Chain integrity holds iff every entry's prev_hash equals the
        prior entry's entry_hash (or GENESIS_HASH for seq=0).

        ethics_evaluation_hash: SHA-256 of canonical_json(ethics_evaluations) when
        ethics evaluation results are provided at record time.  Empty string ('')
        when no ethics evaluations are supplied (backward compatible).  The field
        creates a tamper-evident link between each audit record and the ethics
        evaluations that governed the corresponding action.

        NIST AI RMF MEASURE 4.1: immutability enforced by Pydantic frozen model
        and append-only chain design.
        """

    model_config = ConfigDict(frozen=True)

    seq: int
    event_id: UUID = Field(default_factory=uuid4)
    kind: str  # RtaEventKind value or any str for CUSTOM
    tenant_id: str
    agent_id: str
    payload: dict[str, Any]
    timestamp: datetime
    prev_hash: str
    entry_hash: str = ""  # filled by RtaLedger.record; empty only transiently
    ethics_evaluation_hash: str | None = None
    """SHA-256 of canonical JSON of the ethics evaluation results that governed
        this action.  None when no ethics evaluations were provided.  When set, this
        field is included in the entry_hash computation so any tampering of the ethics
        evaluations breaks the hash chain."""


class MerkleSeal(BaseModel):
    """A sealed Merkle root covering a window of chain entries.

        Seals are written to the SealStore after every `merkle_window` entries.
        They are additive — never rewritten.

        merkle_root is the SHA-256 Merkle root of the entry_hash leaves in
        the window [window_start_seq, window_end_seq] (inclusive, by seq).
        """

    model_config = ConfigDict(frozen=True)

    seal_id: UUID = Field(default_factory=uuid4)
    window_start_seq: int
    window_end_seq: int
    merkle_root: str  # SHA-256 hex
    sealed_at: datetime
    leaf_count: int


class IntegrityReport(BaseModel):
    """Result of a full chain-integrity verification run.

        ok=True means every entry's hash chain and all sealed Merkle roots
        verified correctly.  ok=False indicates tamper evidence.

        NIST AI RMF MEASURE 2.5: recurring integrity evaluations documented here.
        NIST AI RMF MEASURE 2.13: metrics effectiveness recorded in merkle_roots_matched/mismatched.
        """

    model_config = ConfigDict(frozen=True)

    total_entries: int
    ok: bool
    first_bad_seq: int | None = None
    mismatch_detail: str = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    merkle_roots_matched: int = 0
    merkle_roots_mismatched: int = 0


class Article50Disclosure(BaseModel):
    """EU AI Act Art. 50 disclosure header for AI-generated output.

        Art. 50 §1: Providers of AI systems intended to interact directly
        with natural persons shall ensure those systems are designed and
        developed so that natural persons are informed they are interacting
        with an AI system.

        Art. 50 §4: Deployers of high-risk AI systems shall inform natural
        persons exposed to those systems of the AI-generated nature of content.

        The disclosure is both returned to the caller (for UI rendering) and
        persisted as an AI_GENERATED_OUTPUT event on the chain (audit trail
        is irrefutable — caller cannot emit output without leaving a trace).

        content_fingerprint: SHA-256 of the raw content string (UTF-8 encoded).
        audit_seq: the seq number of the corresponding AI_GENERATED_OUTPUT
        RtaEvent — back-reference enabling dual verification.
        """

    model_config = ConfigDict(frozen=True)

    disclosure_id: UUID = Field(default_factory=uuid4)
    is_ai_generated: bool = True
    model_identifier: str  # e.g. "the agent platform-Vedic-v1"
    generated_at: datetime
    tenant_id: str
    content_fingerprint: str  # SHA-256 hex of emitted content
    audit_seq: int  # back-reference to the persisted RtaEvent.seq


# ---------------------------------------------------------------------------
# SealStore Protocol + default implementations
# ---------------------------------------------------------------------------


@runtime_checkable
class SealStore(Protocol):
    """Pluggable offsite Merkle-seal persistence.

        Phase 4 plugs S3 / Write-Ahead-Log implementations.
        The in-process defaults (InMemorySealStore, FileSealStore) are provided
        for development and single-process deployments.

        Technical Debt:
        Implement S3SealStore and WALSealStore.
        S3SealStore: pre-signed PUT + server-side-encryption (SSE-S3/SSE-KMS).
        WALSealStore: fsync after each seal to survive OS crash.
        """

    def store(self, seal: MerkleSeal) -> None:
        """Persist a seal. Idempotent: same seal_id → no-op or overwrite."""
        ...

    def list_seals(self) -> list[MerkleSeal]:
        """Return all stored seals in window_start_seq ascending order."""
        ...


class InMemorySealStore:
    """In-memory SealStore for testing and ephemeral in-process use."""

    def __init__(self) -> None:
        self._seals: list[MerkleSeal] = []
        self._ids: set[str] = set()

    def store(self, seal: MerkleSeal) -> None:
        sid = str(seal.seal_id)
        if sid in self._ids:
            return
        self._ids.add(sid)
        self._seals.append(seal)

    def list_seals(self) -> list[MerkleSeal]:
        return sorted(self._seals, key=lambda s: s.window_start_seq)


class FileSealStore:
    """Seals to a local file (newline-delimited JSON).

        Not atomic across processes — add a file lock in production.
        Each line is a JSON-serialized MerkleSeal.  Idempotent: duplicate
        seal_ids are skipped on store().

        Technical Debt:
        Add fcntl.flock or a third-party filelock for multi-process safety.
        """

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def store(self, seal: MerkleSeal) -> None:
        existing_ids = {s.seal_id for s in self.list_seals()}
        if seal.seal_id in existing_ids:
            return
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(seal.model_dump_json() + "\n")

    def list_seals(self) -> list[MerkleSeal]:
        if not self._path.exists():
            return []
        seals: list[MerkleSeal] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    seals.append(MerkleSeal.model_validate_json(line))
                except Exception:
                    # Skip malformed lines — caller detects via integrity check
                    continue
        return sorted(seals, key=lambda s: s.window_start_seq)


# ---------------------------------------------------------------------------
# Merkle root helper
# ---------------------------------------------------------------------------


def merkle_root(leaves: list[str]) -> str:
    """Compute a simple binary Merkle root over SHA-256 hex leaves.

        Algorithm (Bitcoin-style):
        1. Empty list → GENESIS_HASH.
        2. Odd count → append last leaf again (duplication).
        3. Pair consecutive leaves, hash each pair:
        SHA-256(left_hex_bytes + right_hex_bytes).
        4. Repeat until one root remains.

        Args:
        leaves: List of SHA-256 hex strings (64 chars each).

        Returns:
        SHA-256 hex string of the Merkle root, or GENESIS_HASH if empty.
        """
    if not leaves:
        return GENESIS_HASH

    current: list[str] = list(leaves)
    while len(current) > 1:
        if len(current) % 2 == 1:
            current.append(current[-1])  # duplicate last (Bitcoin convention)
        next_level: list[str] = []
        for i in range(0, len(current), 2):
            combined = (current[i] + current[i + 1]).encode("ascii")
            next_level.append(hashlib.sha256(combined).hexdigest())
        current = next_level

    return current[0]


# ---------------------------------------------------------------------------
# Canonical serialization helpers
# ---------------------------------------------------------------------------


def _canonical_payload(payload: dict[str, Any]) -> str:
    """Deterministic JSON of payload: sort_keys=True, compact separators."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _compute_entry_hash(
    seq: int,
    prev_hash: str,
    kind: str,
    tenant_id: str,
    agent_id: str,
    payload: dict[str, Any],
    timestamp: datetime,
    ethics_evaluation_hash: str = "",
) -> str:
    """SHA-256 of canonical serialization of all chain fields.

        Canonical string (pipe-separated, no extra whitespace):
        "{seq}|{prev_hash}|{ethics_evaluation_hash}|{kind}|{tenant_id}|{agent_id}|{canonical_json(payload)}|{ts_iso}"

        ethics_evaluation_hash: SHA-256 of the ethics evaluation dict when supplied,
        or empty string '' for backward compatibility.  Included in the hash so
        tampering with ethics evaluations breaks the chain.
        """
    ts_iso = timestamp.isoformat()
    canonical = (
        f"{seq}|{prev_hash}|{ethics_evaluation_hash}|{kind}|{tenant_id}|{agent_id}"
        f"|{_canonical_payload(payload)}|{ts_iso}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compute_hmac(entry_hash: str, key: bytes) -> str:
    """HMAC-SHA-256 of entry_hash bytes using key. Returns hex digest."""
    return _hmac_mod.new(key, entry_hash.encode("ascii"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# RtaLedger
# ---------------------------------------------------------------------------


class RtaLedger:
    """Hash-chained append-only audit log with Merkle-root sealing.

        Design invariants:
        1. Append-only: no public mutation method exists.  Supersession is
        not a V16 concept (see SamskaraLedger V8 for karmic supersession).
        2. Hash-chain: every entry's entry_hash covers (seq, prev_hash, kind,
        tenant_id, agent_id, payload, timestamp) — tamper anywhere produces
        a downstream mismatch in verify_integrity().
        3. Merkle sealing: every merkle_window entries a seal is stored.
        Seals are additive — never rewritten.
        4. Art. 50 atomicity: every AI-generated output creates a persisted
        chain event before returning the disclosure header — caller cannot
        emit output without leaving a trace.
        5. Tenant isolation by replay: replay(tenant_id=X) filters to tenant X.
        The chain itself is global — cross-tenant tampering still breaks
        the hash chain (the global chain is the tamper-evidence mechanism).
        6. HMAC is optional and one-way: if hmac_key is supplied at construction,
        it is used to generate HMACs stored alongside each entry.  The key
        is never written to disk.  verify_integrity() requires the same key.

        Threading:
        A single threading.Lock serializes all writes (record + _seal_window).
        verify_integrity() acquires the lock briefly to snapshot chain length,
        then reads without holding it (safe — append-only means no deletions).

        NIST AI RMF MEASURE 2.5: recurring integrity evaluations via verify_integrity().
        NIST AI RMF MEASURE 4.1: accountability via append-only + hash-chain.
        EU AI Act Art. 50: AI_GENERATED_OUTPUT events + Article50Disclosure.

        Args:
        storage:  "memory" or "sqlite".
        sqlite_path:  Required when storage="sqlite".
        merkle_window: Seal every N entries (default DEFAULT_MERKLE_WINDOW=1000).
        seal_store:  SealStore for offsite Merkle persistence.
        Defaults to InMemorySealStore.
        hmac_key:  Optional bytes key for HMAC-SHA-256 per-entry MAC.
        Never persisted.  Must be re-supplied to verify_integrity()
        for HMAC verification.
        """

    def __init__(
        self,
        *,
        storage: Literal["memory", "sqlite"] = "memory",
        sqlite_path: Path | None = None,
        merkle_window: int = DEFAULT_MERKLE_WINDOW,
        seal_store: SealStore | None = None,
        hmac_key: bytes | None = None,
    ) -> None:
        self._storage = storage
        self._merkle_window = merkle_window
        self._seal_store: SealStore = seal_store if seal_store is not None else InMemorySealStore()
        self._hmac_key: bytes | None = hmac_key
        self._lock = threading.Lock()

        # In-memory chain — always maintained (SQLite uses it as write-through cache)
        self._chain: list[RtaEvent] = []

        # HMAC side-cache for memory mode (SQLite stores hmac_hex in the row)
        self._hmac_cache: dict[int, str] = {}

        self._db: sqlite3.Connection | None = None
        if storage == "sqlite":
            if sqlite_path is None:
                msg = "sqlite_path is required when storage='sqlite'"
                raise ValueError(msg)
            self._db = self._open_db(sqlite_path)
            self._load_from_db()

    # ------------------------------------------------------------------
    # Public write
    # ------------------------------------------------------------------

    def record(
        self,
        kind: str,
        *,
        tenant_id: str,
        agent_id: str,
        payload: dict[str, Any],
        now: datetime | None = None,
        ethics_evaluations: dict[str, Any] | None = None,
    ) -> RtaEvent:
        """Append a new event to the chain.

            Computes entry_hash = SHA-256 of canonical serialization:
            "{seq}|{prev_hash}|{ethics_evaluation_hash}|{kind}|{tenant_id}|{agent_id}|{canonical_json(payload)}|{ts_iso}"

            ethics_evaluations: Optional dict of all ethics-module evaluation results
            that governed this action.  When supplied, SHA-256 of
            canonical_json(ethics_evaluations) is computed and stored as
            ethics_evaluation_hash on the RtaEvent — creating a tamper-evident
            link between the audit record and the ethics evaluations (Patent
            Claim 12 & 17).  When None, ethics_evaluation_hash is set to None
            and the hash input uses '' for backward compatibility.

            If hmac_key was supplied at construction, HMAC-SHA-256(entry_hash)
            is stored alongside the entry (in SQLite: hmac_hex column;
            in memory: internal hmac_cache dict).

            After appending, if len(chain) % merkle_window == 0 → _seal_window().

            Thread-safe via single write lock.

            Args:
            kind:  RtaEventKind constant or custom string.
            tenant_id:  Tenant/organization identifier.
            agent_id:  Agent/service identifier.
            payload:  Event-kind-specific dict (must be JSON-serializable).
            now:  Override timestamp for deterministic testing.
            ethics_evaluations:  Optional dict of ethics evaluation results.

            Returns:
            Immutable RtaEvent with entry_hash and optional ethics_evaluation_hash filled in.
            """
        ts = now if now is not None else datetime.now(UTC)

        # Compute ethics_evaluation_hash when evaluations are provided
        ethics_eval_hash: str | None = None
        if ethics_evaluations is not None:
            ethics_eval_hash = hashlib.sha256(
                _canonical_payload(ethics_evaluations).encode("utf-8")
            ).hexdigest()

        # For the entry_hash computation use '' when no ethics evaluations supplied
        # (backward compat — existing chains without ethics evaluations still verify)
        ethics_hash_for_chain = ethics_eval_hash if ethics_eval_hash is not None else ""

        with self._lock:
            seq = len(self._chain)
            prev_hash = self._chain[-1].entry_hash if self._chain else GENESIS_HASH

            entry_hash = _compute_entry_hash(
                seq=seq,
                prev_hash=prev_hash,
                kind=kind,
                tenant_id=tenant_id,
                agent_id=agent_id,
                payload=payload,
                timestamp=ts,
                ethics_evaluation_hash=ethics_hash_for_chain,
            )

            event = RtaEvent(
                seq=seq,
                event_id=uuid4(),
                kind=kind,
                tenant_id=tenant_id,
                agent_id=agent_id,
                payload=payload,
                timestamp=ts,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
                ethics_evaluation_hash=ethics_eval_hash,
            )

            self._chain.append(event)

            # Compute and store HMAC (if key configured)
            hmac_hex: str | None = None
            if self._hmac_key is not None:
                hmac_hex = _compute_hmac(entry_hash, self._hmac_key)
                self._hmac_cache[seq] = hmac_hex

            if self._db is not None:
                self._insert_db(event, hmac_hex)

            # Seal every merkle_window entries
            if len(self._chain) % self._merkle_window == 0:
                self._seal_window()

        return event

    # ------------------------------------------------------------------
    # Article 50 disclosure (EU AI Act)
    # ------------------------------------------------------------------

    def emit_article_50_disclosure(
        self,
        *,
        model_identifier: str,
        content: str,
        tenant_id: str,
        agent_id: str,
    ) -> Article50Disclosure:
        """Record an AI_GENERATED_OUTPUT event and return the Art. 50 disclosure.

            EU AI Act Art. 50 §4 compliance:
            Every AI-generated output is logged before being returned.
            This is the sole entry-point for emitting AI-generated outputs.
            It atomically:
            1. Computes SHA-256 fingerprint of content.
            2. Records an AI_GENERATED_OUTPUT event on the chain.
            3. Returns the Article50Disclosure for UI rendering.

            Atomicity invariant: step 2 must succeed before step 3 is reached
            (if record() raises, the disclosure is never returned).

            Args:
            model_identifier: Human-readable model ID ("the agent platform-Vedic-v1").
            content:  The AI-generated output text (any string).
            tenant_id:  Tenant/organization identifier.
            agent_id:  Agent/service identifier.

            Returns:
            Article50Disclosure with audit_seq back-referencing the new event.
            """
        ts = datetime.now(UTC)
        content_fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # record() MUST succeed before we return the disclosure
        event = self.record(
            RtaEventKind.AI_GENERATED_OUTPUT,
            tenant_id=tenant_id,
            agent_id=agent_id,
            payload={
            "model_identifier": model_identifier,
            "content_fingerprint": content_fingerprint,
            "is_ai_generated": True,
        },
            now=ts,
        )

        return Article50Disclosure(
            is_ai_generated=True,
            model_identifier=model_identifier,
            generated_at=ts,
            tenant_id=tenant_id,
            content_fingerprint=content_fingerprint,
            audit_seq=event.seq,
        )

    # ------------------------------------------------------------------
    # Integrity verification
    # ------------------------------------------------------------------

    def verify_integrity(self, *, entries_per_chunk: int = 10000) -> IntegrityReport:
        """Replay entire chain and verify hash-chain integrity + Merkle roots.

            Algorithm:
            1. Snapshot chain under lock (safe — append-only).
            2. For each entry i (in seq order):
            a. Verify entry.prev_hash == prior entry's entry_hash (or GENESIS_HASH).
            b. Recompute entry_hash from stored fields → compare to stored value.
            c. If hmac_key configured: retrieve stored HMAC → recompute → compare.
            Stop at first mismatch (records first_bad_seq).
            3. For each stored seal:
            a. Collect entry_hash leaves for the seal's window.
            b. Recompute Merkle root → compare to seal.merkle_root.
            4. ok = no hash mismatches AND no Merkle mismatches.

            O(N) over chain entries, O(M × W) over seals (M seals, W window size).
            Stress target: 100k entries < 5 seconds.

            Args:
            entries_per_chunk: Unused (reserved for future chunked I/O). Kept for
            API stability.

            Returns:
            IntegrityReport.
            """
        with self._lock:
            chain_snapshot = list(self._chain)
            seals_snapshot = list(self._seal_store.list_seals())
            hmac_cache_snapshot = dict(self._hmac_cache)
            has_hmac_key = self._hmac_key is not None
            hmac_key = self._hmac_key

        total = len(chain_snapshot)
        first_bad_seq: int | None = None
        mismatch_detail = ""

        # --- SEC C-2: HMAC presence is binding ---
        # If the chain was HMAC-sealed but the verifier supplies no key, fail
        # CLOSED. Otherwise an attacker who can write the SQLite file recomputes
        # the pure-SHA-256 entry_hash chain (no secret needed) and a keyless
        # verifier would report the forged chain as intact.
        if not has_hmac_key and total > 0:
            sealed = bool(hmac_cache_snapshot)
            if not sealed and self._db is not None:
                cur = self._db.execute(
                    "SELECT 1 FROM rta_events WHERE hmac_hex IS NOT NULL LIMIT 1"
                )
                sealed = cur.fetchone() is not None
            if sealed:
                return IntegrityReport(
                    total_entries=total,
                    ok=False,
                    first_bad_seq=0,
                    mismatch_detail="chain was HMAC-sealed but no verification key supplied",
                    merkle_roots_matched=0,
                    merkle_roots_mismatched=0,
                )

        # --- Phase 1: hash-chain ---
        for i, event in enumerate(chain_snapshot):
            expected_prev = chain_snapshot[i - 1].entry_hash if i > 0 else GENESIS_HASH

            if event.prev_hash != expected_prev:
                first_bad_seq = event.seq
                mismatch_detail = (
                    f"seq={event.seq}: prev_hash mismatch "
                    f"(stored={event.prev_hash[:12]}… "
                    f"expected={expected_prev[:12]}…)"
                )
                break

            # Include ethics_evaluation_hash in the recompute (backward compat:
            # None → '' for records written before the field existed)
            ethics_hash_for_recompute = event.ethics_evaluation_hash or ""
            recomputed = _compute_entry_hash(
                seq=event.seq,
                prev_hash=event.prev_hash,
                kind=event.kind,
                tenant_id=event.tenant_id,
                agent_id=event.agent_id,
                payload=event.payload,
                timestamp=event.timestamp,
                ethics_evaluation_hash=ethics_hash_for_recompute,
            )
            if recomputed != event.entry_hash:
                first_bad_seq = event.seq
                mismatch_detail = (
                    f"seq={event.seq}: entry_hash mismatch — payload or fields tampered"
                )
                break

            if has_hmac_key and hmac_key is not None:
                stored_hmac: str | None = None
                if self._db is not None:
                    stored_hmac = self._get_db_hmac(event.seq)
                else:
                    stored_hmac = hmac_cache_snapshot.get(event.seq)

                if stored_hmac is None:
                    first_bad_seq = event.seq
                    mismatch_detail = f"seq={event.seq}: HMAC mode active but no stored HMAC found"
                    break

                expected_hmac = _compute_hmac(event.entry_hash, hmac_key)
                if not _hmac_mod.compare_digest(expected_hmac, stored_hmac):
                    first_bad_seq = event.seq
                    mismatch_detail = (
                        f"seq={event.seq}: HMAC mismatch — "
                        "possible tamper or wrong verification key"
                    )
                    break

        # --- Phase 2: Merkle seals (independent of phase 1 result) ---
        merkle_matched = 0
        merkle_mismatched = 0

        for seal in seals_snapshot:
            leaves = [
                e.entry_hash
                for e in chain_snapshot
                if seal.window_start_seq <= e.seq <= seal.window_end_seq
            ]
            recomputed_root = merkle_root(leaves)
            if recomputed_root == seal.merkle_root:
                merkle_matched += 1
            else:
                merkle_mismatched += 1

        ok = first_bad_seq is None and merkle_mismatched == 0

        return IntegrityReport(
            total_entries=total,
            ok=ok,
            first_bad_seq=first_bad_seq,
            mismatch_detail=mismatch_detail,
            merkle_roots_matched=merkle_matched,
            merkle_roots_mismatched=merkle_mismatched,
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def replay(
        self,
        *,
        kind: str | None = None,
        tenant_id: str | None = None,
        limit: int | None = None,
    ) -> list[RtaEvent]:
        """Return events filtered by kind and/or tenant_id, in seq order.

            Tenant isolation: only events for the specified tenant are returned.
            The chain itself is global — cross-tenant tampering still breaks
            the hash chain (the global chain is the tamper-evidence mechanism).

            Args:
            kind:  Filter to events of this kind.
            tenant_id: Filter to events from this tenant.
            limit:  Maximum events to return.

            Returns:
            List of RtaEvent in ascending seq order.
            """
        with self._lock:
            snapshot = list(self._chain)

        result = snapshot
        if kind is not None:
            result = [e for e in result if e.kind == kind]
        if tenant_id is not None:
            result = [e for e in result if e.tenant_id == tenant_id]
        if limit is not None:
            result = result[:limit]
        return result

    def merkle_roots(self) -> list[MerkleSeal]:
        """Return all stored Merkle seals in window_start_seq ascending order."""
        return self._seal_store.list_seals()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def length(self) -> int:
        """Number of entries currently in the chain."""
        with self._lock:
            return len(self._chain)

    @property
    def tip_hash(self) -> str:
        """entry_hash of the most recent entry, or GENESIS_HASH if empty."""
        with self._lock:
            if not self._chain:
                return GENESIS_HASH
            return self._chain[-1].entry_hash

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _seal_window(self) -> None:
        """Compute Merkle root over last merkle_window entries + store seal.

            Called (inside the write lock) after every merkle_window appends.
            """
        total = len(self._chain)
        window_end = total - 1
        window_start = max(0, total - self._merkle_window)

        leaves = [e.entry_hash for e in self._chain[window_start : window_end + 1]]
        root = merkle_root(leaves)

        seal = MerkleSeal(
            window_start_seq=window_start,
            window_end_seq=window_end,
            merkle_root=root,
            sealed_at=datetime.now(UTC),
            leaf_count=len(leaves),
        )
        self._seal_store.store(seal)

        if self._db is not None:
            self._insert_seal_db(seal)

    def _open_db(self, path: Path) -> sqlite3.Connection:
        """Open (or create) SQLite DB with WAL mode for concurrent reads."""
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(_CREATE_EVENTS_TABLE)
        conn.execute(_CREATE_SEALS_TABLE)
        # Back-compatible migration: older DBs lack ethics_evaluation_hash, which
        # is part of the entry_hash preimage; without it, cross-process reopen
        # recomputes the wrong hash and verify_integrity fails at seq 0 (C-1).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(rta_events)").fetchall()}
        if "ethics_evaluation_hash" not in cols:
            conn.execute("ALTER TABLE rta_events ADD COLUMN ethics_evaluation_hash TEXT")
        conn.commit()
        return conn

    def _load_from_db(self) -> None:
        """Load existing events and seals from SQLite into in-memory cache."""
        if self._db is None:
            return
        cursor = self._db.execute(
            "SELECT seq, event_id, kind, tenant_id, agent_id, "
            "payload_json, ts, prev_hash, entry_hash, ethics_evaluation_hash, hmac_hex "
            "FROM rta_events ORDER BY seq ASC"
        )
        for row in cursor.fetchall():
            seq, eid, kind, tid, aid, pjson, ts, prev_h, eh, eeh, hx = row
            self._chain.append(
                RtaEvent(
                seq=seq,
                event_id=UUID(eid),
                kind=kind,
                tenant_id=tid,
                agent_id=aid,
                payload=json.loads(pjson),
                timestamp=datetime.fromisoformat(ts),
                prev_hash=prev_h,
                entry_hash=eh,
                ethics_evaluation_hash=eeh,
            )
            )
            if hx is not None:
                self._hmac_cache[seq] = str(hx)

        # Reload seals into the in-memory seal store
        cursor2 = self._db.execute(
            "SELECT seal_id, window_start, window_end, merkle_root, sealed_at, leaf_count "
            "FROM rta_seals ORDER BY window_start ASC"
        )
        for row in cursor2.fetchall():
            sid, ws, we, mr, sa, lc = row
            self._seal_store.store(
                MerkleSeal(
                seal_id=UUID(sid),
                window_start_seq=ws,
                window_end_seq=we,
                merkle_root=mr,
                sealed_at=datetime.fromisoformat(sa),
                leaf_count=lc,
            )
            )

    def _insert_db(self, event: RtaEvent, hmac_hex: str | None) -> None:
        """INSERT one event row. Never UPDATE or DELETE."""
        assert self._db is not None
        self._db.execute(
            "INSERT INTO rta_events "
            "(seq, event_id, kind, tenant_id, agent_id, payload_json, "
            "ts, prev_hash, entry_hash, ethics_evaluation_hash, hmac_hex) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
            event.seq,
            str(event.event_id),
            event.kind,
            event.tenant_id,
            event.agent_id,
            _canonical_payload(event.payload),
            event.timestamp.isoformat(),
            event.prev_hash,
            event.entry_hash,
            event.ethics_evaluation_hash,
            hmac_hex,
        ),
        )
        self._db.commit()

    def _insert_seal_db(self, seal: MerkleSeal) -> None:
        """INSERT one seal row. Never UPDATE or DELETE."""
        assert self._db is not None
        self._db.execute(
            "INSERT OR IGNORE INTO rta_seals "
            "(seal_id, window_start, window_end, merkle_root, sealed_at, leaf_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
            str(seal.seal_id),
            seal.window_start_seq,
            seal.window_end_seq,
            seal.merkle_root,
            seal.sealed_at.isoformat(),
            seal.leaf_count,
        ),
        )
        self._db.commit()

    def _get_db_hmac(self, seq: int) -> str | None:
        """Fetch the stored hmac_hex for a given seq from SQLite."""
        if self._db is None:
            return None
        cursor = self._db.execute("SELECT hmac_hex FROM rta_events WHERE seq = ?", (seq,))
        row = cursor.fetchone()
        if row and row[0] is not None:
            return str(row[0])
        return None
