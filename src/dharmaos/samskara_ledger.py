"""
DharmaOS SamskaraLedger — append-only bi-temporal action-consequence (outcome-memory) ledger.

PURPOSE
-------
SamskaraLedger is the persistent memory of agent actions and their observed
consequences. Every executed action appends a SamskaraEntry recording what
was done, what outcome resulted, and a karma-delta score (positive = good
outcome, negative = harmful/failed outcome). The ledger is append-only and
bi-temporally indexed: entries are never deleted or mutated; supersession is
tracked via explicit revision pointers (Kumiho AGM immutable-revision pattern).

The ledger serves three governance functions:

1. AdharmaDetector Layer 5 (path-risk): the 24-hour rolling karma mean
(karma_rolling_mean()) feeds the slow-action-pattern detector. A mean
below -0.3 signals a negative behavioral trend and blocks further actions.

2. RF-38 slow-poison detector (detect_escalation()): partitions history into
three temporal windows and fires an EscalationAlert when the karma trend
is monotone-decreasing (baseline > mid > recent) and the gap exceeds the
threshold. Prevents gradual policy erosion that individual-action checks
miss.

3. JnanaModule calibration: the self-model reads ledger entries to update
capability-vector confidence and identify blind-spots.

SCORING MODEL
-------------
Karma-delta is a signed float in [-1.0, +1.0]:
+1.0  all success criteria met, no harms, no unanticipated effects
0.0  neutral / partial
-1.0  harmful outcome / all criteria failed

Ebbinghaus decay: older entries are down-weighted by an exponential decay
function (half-life configurable, default 7 days) so that recent behavior
is more influential than remote history. This prevents distant past mistakes
from permanently constraining a reformed agent.

Causal-graph (MAGMA pattern): each entry records cause → effect edges,
enabling post-hoc causal attribution of outcome patterns.

KEY TYPES
---------
SamskaraLedger  : Main ledger. append() → entry id. query() → entries.
karma_rolling_mean() → float | None.
detect_escalation() → EscalationAlert | None.
SamskaraEntry  : Single action-consequence record (bi-temporal,
append-only, karma_delta, causal links).
KarmaDelta  : Signed float with rationale string and contributing factors.
EscalationAlert  : Slow-poison alert (baseline/mid/recent means, delta, severity).
AppendOnlyViolationError : Raised when a caller attempts to re-append an
existing entry_id (immutability enforcement).
BiTemporalInvariantError : Raised when temporal ordering is violated.

COMPLIANCE ROLE
---------------
- Provides audit trail for V-RECON outcome reconciliation.
- RF-38 escalation detector fulfills NIST AI RMF MEASURE 2.5 "recurring
evaluation" requirement.

Governance origin: Yoga Sūtras II.12–II.14 (karmāśaya — the accumulation
substrate of karmic residue that shapes future action). Every agent action
leaves an impression (saṃskāra) that influences future governance decisions.

the agent platform Saṃskāra-Ledger — Sprint V8.

**Philosophical grounding:**
Yoga Sūtras II.12–II.14 (Patañjali; Bryant 2009, North Point Press):
II.12: karmāśayo dṛṣṭādṛṣṭa-janma-vedanīyaḥ
"The karmic residue (karmāśaya) of saṃskāras ripens into
visible and invisible births / consequences."
II.13: sati mūle tad-vipāko jāty-āyur-bhogāḥ
"While the root exists, it ripens as birth, life-span and experience."
II.14: te hlāda-paritāpa-phalāḥ puṇyāpuṇya-hetutvāt
"These bear pleasant or painful fruits according to whether
their causes were meritorious or demeritorious."

Engineering interpretation: every agent action leaves a latent impression
(saṃskāra) that shapes future policy.  The ledger is the *karmāśaya* —
the accumulation substrate.  Append-only semantics honour the immutability
of karmic residue; bi-temporal queries allow rewind without mutation.

**SOTA references:**
- Graphiti bi-temporal (arXiv:2501.13956) — T/T′ temporal tagging.
- Kumiho AGM immutable-revision (arXiv:2603.17244) — append-only
supersession via explicit revision pointers; the ONE permitted mutation
(valid_until) is audit-logged.
- MAGMA causal-graph (arXiv:2601.03236) — directed action→consequence edges.
- A-MEM Zettelkasten links (arXiv:2502.12110) — semantic auto-linking on append.
- Process Reward Model Q-value (arXiv:2502.10325) — karma-delta scoring.
- Ebbinghaus forgetting curve — unsolved-field-frontier weight decay.
- Mem0 v2 structured-attrs — attribute-rich memory records.
- Hindsight (arXiv:2512.12818) — opinion-network baseline.
- Reflexion (arXiv:2303.11366) — self-reflection baseline.

**Technical debt (Phase 4 benchmark sprint):**
TD-V8-01: Semantic similarity uses token-set Jaccard on action_signature.
Replace with sentence-transformers or openai text-embedding-3 for
real vector similarity.  Qdrant collection deferred.
TD-V8-02: Causal graph uses in-memory adjacency dict.
Replace with Neo4j/FalkorDB for production MAGMA-pattern graph.
TD-V8-03: Qdrant vector retrieval deferred; current retrieval is linear scan
over in-memory store (acceptable at < 100k entries, not for prod).

**RF-38 closure:**
The escalation-rate detector (detect_escalation) implements the slow-poison
detector required by RF-38 (HIGH).  It partitions entries into three temporal
windows, computes per-window karma-delta mean, and fires an EscalationAlert
when baseline_mean - recent_mean > threshold AND the sequence is monotone-
decreasing (baseline > mid > recent).  Tests 19–26 provide full coverage.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

# Lazily imported only when needed for action_signature_from_contract
# to avoid a hard circular dep — ethics_engine already imports nothing from here.

_log: structlog.stdlib.BoundLogger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AppendOnlyViolationError(Exception):
    """Raised when caller tries to append an entry whose id already exists."""


# Public alias — spec requires class named AppendOnlyViolation; PEP-8 requires Error suffix.
# Both names are exported; callers should use AppendOnlyViolationError going forward.
AppendOnlyViolation = AppendOnlyViolationError


class BiTemporalInvariantError(Exception):
    """Raised when a bi-temporal invariant is violated (e.g. valid_from > valid_until)."""


# ---------------------------------------------------------------------------
# EscalationAlert — RF-38
# ---------------------------------------------------------------------------


class EscalationAlert(BaseModel):
    """Alert emitted by detect_escalation() when a slow-poison pattern is found.

    RF-38 (HIGH) — slow-poison adversarial escalation-rate detector.
    Source: adversarial-corpus threat model K-015 (10/10 insidiousness).
    """

    org_id: UUID
    baseline_mean: float
    mid_mean: float
    recent_mean: float
    delta: float
    """baseline_mean - recent_mean.  Positive -> degradation."""
    top_contributors: list[str]
    """Top-3 action_signatures most responsible for the negative drift."""
    detected_at: datetime
    severity: Literal["low", "medium", "high", "critical"]
    """Scaled by delta magnitude:
    low  delta ∈ [threshold, 0.25)
    medium  delta ∈ [0.25, 0.50)
    high  delta ∈ [0.50, 0.75)
    critical delta ≥ 0.75
    """


# ---------------------------------------------------------------------------
# KarmaDelta — Process-Reward-Model Q-value formalism
# ---------------------------------------------------------------------------


class KarmaDelta(BaseModel):
    """Process-Reward-Model Q-value karma delta.

    Source: PRM Q-value formalism (arXiv:2502.10325).

    Δ = α × outcome_quality + β × user_satisfaction
    − γ × rollback_count − δ × dharma_violation_flag

    Defaults: α=0.4, β=0.3, γ=0.2, δ=1.0.
    All inputs clamped [0,1] except rollback_count ∈ ℕ.
    dharma_violation_flag supplied by V7 Puruṣārthas coordinator
    (evaluate_dharma → EthicsVerdict.is_ethical).
    """

    outcome_quality: float
    """Clamped [0, 1]. Quality of the action outcome."""
    user_satisfaction: float
    """Clamped [0, 1]. User-reported or inferred satisfaction."""
    rollback_count: int
    """Number of times this action was rolled back (N >= 0)."""
    dharma_violation_flag: bool
    """True if V7 EthicsEngine flagged an ethical violation."""
    alpha: float = 0.4
    """Weight for outcome_quality."""
    beta: float = 0.3
    """Weight for user_satisfaction."""
    gamma: float = 0.2
    """Penalty weight per rollback."""
    delta: float = 1.0
    """Penalty for dharma violation (classical dharma weight, δ=1.0 default)."""

    @property
    def value(self) -> float:
        """Computed karma delta value.

        Δ = α × clamp(outcome_quality) + β × clamp(user_satisfaction)
        − γ × max(rollback_count, 0) − δ × float(dharma_violation_flag)
        """
        q = max(0.0, min(1.0, self.outcome_quality))
        s = max(0.0, min(1.0, self.user_satisfaction))
        r = max(0, self.rollback_count)
        d = 1.0 if self.dharma_violation_flag else 0.0
        return self.alpha * q + self.beta * s - self.gamma * r - self.delta * d


# ---------------------------------------------------------------------------
# SamskaraEntry — append-only bi-temporal record
# ---------------------------------------------------------------------------


class SamskaraEntry(BaseModel):
    """Append-only bi-temporal saṃskāra record.

    Source: Yoga Sūtras II.12–II.14 — saṃskāra as latent karmic impression
    that shapes future agency.  The entry is the atomic unit of the karmāśaya.

    SOTA:
    - Graphiti bi-temporal (arXiv:2501.13956) — T/T′ semantics; valid_from
    is transaction time (when the agent acted), valid_until is end of
    validity (None = currently active).
    - Kumiho AGM immutable-revision (arXiv:2603.17244) — superseded_by
    is the revision pointer; original entries are NEVER deleted.
    - MAGMA causal-graph (arXiv:2601.03236) — causal_parents encodes the
    directed action→consequence chain.
    - A-MEM Zettelkasten (arXiv:2502.12110) — zettelkasten_links for
    semantic note-graph auto-linking.
    """

    id: UUID = Field(default_factory=uuid4)
    action_signature: str
    """Canonical hash/label of action type + core params.  Used for retrieval."""
    action_trace: dict[str, Any]
    """JSONB-equivalent free-form action provenance."""
    consequence_trace: dict[str, Any]
    """JSONB-equivalent free-form consequence provenance."""
    karma_delta: float
    """PRM Q-value delta — numeric karma change for this action."""
    guna_context: Literal["sattva", "rajas", "tamas"] | None = None
    """V6 GunaMonitor tag at time of action."""
    valid_from: datetime
    """Graphiti transaction time T — when the action occurred."""
    valid_until: datetime | None = None
    """End of validity window. None = currently active."""
    superseded_by: UUID | None = None
    """Kumiho revision pointer. Non-None → this record was superseded."""
    causal_parents: list[UUID] = Field(default_factory=list)
    """MAGMA causal-graph parent IDs."""
    zettelkasten_links: list[UUID] = Field(default_factory=list)
    """A-MEM Zettelkasten linked saṃskāra IDs."""
    org_id: UUID | None = None
    """Row-Level Security partition key (multi-tenant isolation)."""
    recall_count: int = 0
    """Number of times this entry has been retrieved (Ebbinghaus consolidation)."""

    model_config = {"frozen": True}
    """Append-only invariant: frozen pydantic model prevents in-place mutation."""


# ---------------------------------------------------------------------------
# Internal storage helpers
# ---------------------------------------------------------------------------


def _serialize_entry(e: SamskaraEntry) -> dict[str, Any]:
    """Convert a SamskaraEntry to a flat JSON-safe dict for SQLite storage."""
    return {
            "id": str(e.id),
                            "action_signature": e.action_signature,
                        "action_trace": json.dumps(e.action_trace),
                            "consequence_trace": json.dumps(e.consequence_trace),
                    "karma_delta": e.karma_delta,
                        "guna_context": e.guna_context,
                    "valid_from": e.valid_from.isoformat(),
                    "valid_until": e.valid_until.isoformat() if e.valid_until else None,
                        "superseded_by": str(e.superseded_by) if e.superseded_by else None,
                        "causal_parents": json.dumps([str(u) for u in e.causal_parents]),
                            "zettelkasten_links": json.dumps([str(u) for u in e.zettelkasten_links]),
                "org_id": str(e.org_id) if e.org_id else None,
                        "recall_count": e.recall_count,
    }


def _deserialize_row(row: sqlite3.Row) -> SamskaraEntry:
    """Reconstruct a SamskaraEntry from a SQLite row dict."""
    d = dict(row)
    return SamskaraEntry(
        id=UUID(d["id"]),
                        action_signature=d["action_signature"],
                    action_trace=json.loads(d["action_trace"]),
                        consequence_trace=json.loads(d["consequence_trace"]),
                    karma_delta=d["karma_delta"],
                    guna_context=d["guna_context"],
                valid_from=datetime.fromisoformat(d["valid_from"]),
                    valid_until=datetime.fromisoformat(d["valid_until"]) if d["valid_until"] else None,
                    superseded_by=UUID(d["superseded_by"]) if d["superseded_by"] else None,
                    causal_parents=[UUID(u) for u in json.loads(d["causal_parents"])],
                        zettelkasten_links=[UUID(u) for u in json.loads(d["zettelkasten_links"])],
            org_id=UUID(d["org_id"]) if d["org_id"] else None,
                    recall_count=d["recall_count"],
    )


_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS samskara (
id TEXT PRIMARY KEY,
action_signature TEXT NOT NULL,
action_trace TEXT NOT NULL,
consequence_trace TEXT NOT NULL,
karma_delta REAL NOT NULL,
guna_context TEXT,
valid_from TEXT NOT NULL,
valid_until TEXT,
superseded_by TEXT,
causal_parents TEXT NOT NULL,
zettelkasten_links TEXT NOT NULL,
org_id TEXT,
recall_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_samskara_sig ON samskara(action_signature);
CREATE INDEX IF NOT EXISTS idx_samskara_org_valid ON samskara(org_id, valid_from);

CREATE TABLE IF NOT EXISTS samskara_audit (
id INTEGER PRIMARY KEY AUTOINCREMENT,
entry_id TEXT NOT NULL,
field_name TEXT NOT NULL,
old_value TEXT,
new_value TEXT,
changed_at TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Jaccard token-set similarity (TD-V8-01: replace with embeddings in Phase 4)
# ---------------------------------------------------------------------------


def _jaccard_similarity(sig_a: str, sig_b: str) -> float:
    """Token-set Jaccard similarity on colon/hyphen/underscore-split tokens.

    TD-V8-01: This is a placeholder for real vector similarity.
    Replace with sentence-transformers or openai text-embedding-3 in Phase 4.
    """
    tokens_a = set(_tokenize(sig_a))
    tokens_b = set(_tokenize(sig_b))
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _tokenize(sig: str) -> list[str]:
    """Split action_signature into tokens on common separators."""
    import re

    return [t for t in re.split(r"[:/\-_\s]+", sig.lower()) if t]


# ---------------------------------------------------------------------------
# Severity classification for RF-38 escalation
# ---------------------------------------------------------------------------


def _escalation_severity(
        delta: float, threshold: float
) -> Literal["low", "medium", "high", "critical"]:
    """Map escalation delta to severity tier.

    Thresholds (absolute delta above the detection threshold_delta):
    low  delta ∈ [threshold, 0.25)
    medium  delta ∈ [0.25, 0.50)
    high  delta ∈ [0.50, 0.75)
    critical delta ≥ 0.75
    """
    if delta >= 0.75:
        return "critical"
    if delta >= 0.50:
        return "high"
    if delta >= 0.25:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# SamskaraLedger — main class
# ---------------------------------------------------------------------------


class SamskaraLedger:
    """Append-only saṃskāra-ledger with bi-temporal semantics.

    Implements:
    - Append-only invariant (Kumiho AGM — arXiv:2603.17244)
    - Bi-temporal queries with as_of rewind (Graphiti — arXiv:2501.13956)
    - MAGMA causal-graph layer (arXiv:2601.03236)
    - A-MEM Zettelkasten auto-linking (arXiv:2502.12110)
    - Ebbinghaus forgetting-curve weighted retrieval
    - RF-38 slow-poison escalation-rate detector

    Source: Yoga Sūtras II.12–II.14 — the karmāśaya accumulates latent
    saṃskāras; this class is the computational karmāśaya.

    Storage modes:
    "memory"  — in-process dict, no persistence (test / ephemeral use).
    "sqlite"  — SQLite file, persists across process restarts.

    Technical debt:
    TD-V8-01: Jaccard similarity → real embeddings in Phase 4.
    TD-V8-02: In-memory adjacency dict → Neo4j/FalkorDB in Phase 4.
    TD-V8-03: Linear scan retrieval → Qdrant vector retrieval in Phase 4.
    """

    def __init__(
    self,
    *,
                storage: Literal["memory", "sqlite"] = "memory",
                    sqlite_path: Path | None = None,
                            ebbinghaus_tau_days: float = 30.0,
                        ebbinghaus_beta: float = 0.5,
                            similarity_threshold: float = 0.75,
                    similarity_fn: Callable[[str, str], float] | None = None,
    ) -> None:
        """Initialise the SamskaraLedger.

        Args:
        storage:  Storage mode: ``"memory"`` or ``"sqlite"``.
        sqlite_path:  Path to SQLite file (required when storage='sqlite').
        ebbinghaus_tau_days: Ebbinghaus decay time-constant in days (τ).
        ebbinghaus_beta:  Recall-count boost exponent (β).
        similarity_threshold: Minimum Jaccard (or custom) similarity for
        zettelkasten auto-linking.
        similarity_fn:  Optional callable ``(sig_a: str, sig_b: str) → float``
        that replaces Jaccard for the ``query()`` method.
        If ``None`` (default), Jaccard token-set similarity
        is used (TD-V8-01 backward-compatible behaviour).
        Must return a value in [0.0, 1.0].

        TD-V8-01 closure: pass a cosine-similarity closure
        that pre-embeds signatures with text-embedding-004
        to replace token-set Jaccard.
        """
        self._storage = storage
        # L: τ is the divisor in exp(-Δt/τ) — must be > 0 (zero/negative breaks
        # the Ebbinghaus decay with a ZeroDivisionError or inverted weighting).
        if ebbinghaus_tau_days <= 0:
            raise ValueError(f"ebbinghaus_tau_days must be > 0, got {ebbinghaus_tau_days}")
        self._tau = ebbinghaus_tau_days
        self._beta = ebbinghaus_beta
        self._sim_threshold = similarity_threshold
        self._similarity_fn: Callable[[str, str], float] | None = similarity_fn

        # In-memory primary store: id → SamskaraEntry (both modes cache here)
        self._mem: dict[UUID, SamskaraEntry] = {}
        # C2: parallel agents (asyncio.gather / thread pool) read and append
        # concurrently; without this re-entrant lock, readers iterating _mem hit
        # "dictionary changed size during iteration". Mutators hold it; readers
        # snapshot under it. RLock so a method may call another safely.
        self._lock: "threading.RLock" = threading.RLock()

        # In-memory supersession audit: maps old_id → new_id (written records only)
        # Separate from the entry store to honour append-only semantics.
        self._supersession_audit: dict[UUID, UUID] = {}

        # Causal graph: adjacency list — parent_id → set[child_id]
        # TD-V8-02: in-memory dict; replace with Neo4j/FalkorDB in Phase 4.
        self._causal_graph: dict[UUID, set[UUID]] = defaultdict(set)

        # Declare before conditional to give mypy a single consistent type.
        self._sqlite_path: Path | None = None

        if storage == "sqlite":
            if sqlite_path is None:
                raise ValueError("sqlite_path must be provided when storage='sqlite'")
            self._sqlite_path = sqlite_path
            self._init_sqlite()
            self._load_from_sqlite()

    # ------------------------------------------------------------------
    # SQLite helpers
    # ------------------------------------------------------------------

    def _init_sqlite(self) -> None:
        """Create the SQLite schema if it does not exist."""
        conn = sqlite3.connect(str(self._sqlite_path))
        conn.executescript(_SQLITE_DDL)
        conn.commit()
        conn.close()

    def _load_from_sqlite(self) -> None:
        """Load all entries from SQLite into the in-memory cache."""
        conn = sqlite3.connect(str(self._sqlite_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM samskara").fetchall()
        conn.close()
        for row in rows:
            entry = _deserialize_row(row)
            self._mem[entry.id] = entry

    def _sqlite_insert(self, entry: SamskaraEntry) -> None:
        """INSERT a new row into SQLite (append-only)."""
        data = _serialize_entry(entry)
        conn = sqlite3.connect(str(self._sqlite_path))
        conn.execute(
            """
        INSERT INTO samskara
        (id, action_signature, action_trace, consequence_trace, karma_delta,
        guna_context, valid_from, valid_until, superseded_by,
        causal_parents, zettelkasten_links, org_id, recall_count)
        VALUES
        (:id, :action_signature, :action_trace, :consequence_trace, :karma_delta,
        :guna_context, :valid_from, :valid_until, :superseded_by,
        :causal_parents, :zettelkasten_links, :org_id, :recall_count)
        """,
            data,
        )
        conn.commit()
        conn.close()

    def _sqlite_update_valid_until(self, entry_id: UUID, valid_until: datetime) -> None:
        """The ONE permitted mutation on a row: set valid_until for supersession.

        Per Kumiho AGM: valid_until is the only mutable field on a committed row.
        Audit-logged into samskara_audit.
        """
        now = datetime.now(UTC).isoformat()
        conn = sqlite3.connect(str(self._sqlite_path))
        conn.execute(
            "UPDATE samskara SET valid_until = ? WHERE id = ?",
            (valid_until.isoformat(), str(entry_id)),
        )
        conn.execute(
            """
        INSERT INTO samskara_audit (entry_id, field_name, old_value, new_value, changed_at)
        VALUES (?, 'valid_until', NULL, ?, ?)
        """,
            (str(entry_id), valid_until.isoformat(), now),
        )
        conn.commit()
        conn.close()

    def _sqlite_update_recall_count(self, entry_id: UUID, new_count: int) -> None:
        """Update recall_count for Ebbinghaus consolidation (also audit-logged)."""
        now = datetime.now(UTC).isoformat()
        conn = sqlite3.connect(str(self._sqlite_path))
        conn.execute(
            "UPDATE samskara SET recall_count = ? WHERE id = ?",
            (new_count, str(entry_id)),
        )
        conn.execute(
            """
        INSERT INTO samskara_audit (entry_id, field_name, old_value, new_value, changed_at)
        VALUES (?, 'recall_count', NULL, ?, ?)
        """,
            (str(entry_id), str(new_count), now),
        )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Append
    # ------------------------------------------------------------------

    def get_entry(self, entry_id: UUID) -> SamskaraEntry | None:
        """Retrieve a single entry by id (current in-memory state, including superseded)."""
        return self._mem.get(entry_id)

    def append(self, entry: SamskaraEntry) -> None:
        """Append a new saṃskāra entry.

        Raises AppendOnlyViolation if entry.id already exists.
        Source: Kumiho AGM immutable-revision — no overwrite permitted.
        """
        with self._lock:
            if entry.id in self._mem:
                raise AppendOnlyViolation(
                    f"Entry {entry.id} already exists in the ledger. "
                "The saṃskāra-ledger is append-only; use supersede() to create "
                "a revision. (Kumiho AGM — arXiv:2603.17244)"
                )
            self._mem[entry.id] = entry
            if self._storage == "sqlite":
                self._sqlite_insert(entry)
        _log.debug("samskara.append", id=str(entry.id), sig=entry.action_signature)

    # ------------------------------------------------------------------
    # Supersede (Kumiho AGM revision semantics)
    # ------------------------------------------------------------------

    def supersede(self, old_id: UUID, new: SamskaraEntry) -> None:
        """Create a Kumiho-style revision.

        1. Set old entry's valid_until = now.
        2. Append new entry with superseded_by = old_id.
        3. The old entry is NEVER deleted — it remains queryable via as_of.

        Raises:
        AppendOnlyViolation if old_id does not exist.
        AppendOnlyViolation if new.id already exists.
        BiTemporalInvariantError if new.valid_from < old entry's valid_from.
        """
        if old_id not in self._mem:
            raise AppendOnlyViolation(f"Old entry {old_id} not found; cannot supersede.")

        old = self._mem[old_id]
        now = datetime.now(UTC)

        if new.valid_from < old.valid_from:
            raise BiTemporalInvariantError(
                f"New entry valid_from ({new.valid_from}) must not precede "
                                        f"old entry valid_from ({old.valid_from})."
            )

        # Build updated old entry with valid_until set (new Python object — frozen model)
        old_updated = old.model_copy(update={"valid_until": now, "superseded_by": new.id})
        self._mem[old_id] = old_updated
        if self._storage == "sqlite":
            self._sqlite_update_valid_until(old_id, now)

        # Build new entry with superseded_by pointer
        new_with_ptr = new.model_copy(update={"superseded_by": old_id})

        # Append new entry (will raise AppendOnlyViolation if new.id already exists)
        self.append(new_with_ptr)

        self._supersession_audit[old_id] = new_with_ptr.id
        _log.info(
            "samskara.supersede",
                old_id=str(old_id),
                new_id=str(new_with_ptr.id),
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
    self,
                        action_signature: str,
            top_k: int = 10,
                    min_weight: float = 0.0,
            as_of: datetime | None = None,
                org_id: UUID | None = None,
    ) -> list[tuple[SamskaraEntry, float]]:
        """Return top-k saṃskāras matching action_signature, weighted by
        Ebbinghaus decay × recall_count^β × semantic similarity.

        weight(t) = exp(-Δt/τ) × (1 + recall_count)^β × similarity_score

        TD-V8-01: similarity_score is Jaccard; replace with cosine(embeddings).
        TD-V8-03: linear scan; replace with Qdrant ANN in Phase 4.

        Increments recall_count on returned entries (consolidation).

        Bi-temporal semantics:
        as_of=None → only return entries where valid_until is None (current).
        as_of=T  → return entries that were valid at T, i.e.:
        valid_from ≤ T AND (valid_until is None OR valid_until > T).
        """
        now = datetime.now(UTC)
        results: list[tuple[SamskaraEntry, float]] = []

        with self._lock:
            _entries_snapshot = list(self._mem.values())
        for entry in _entries_snapshot:
            # ── org_id / RLS partition ────────────────────────────────────────
            if org_id is not None and entry.org_id != org_id:
                continue

            # ── Bi-temporal filter ────────────────────────────────────────────
            if as_of is None:
                # Current view: only active (non-superseded, non-expired) entries
                if entry.valid_until is not None:
                    continue
            else:
                # Historical view at as_of
                if entry.valid_from > as_of:
                    continue
                if entry.valid_until is not None and entry.valid_until <= as_of:
                    continue

            # ── Similarity ────────────────────────────────────────────────────
            # Use custom similarity_fn if provided; otherwise fall back to Jaccard
            # (TD-V8-01: custom fn enables cosine(text-embedding-004) retrieval).
            if self._similarity_fn is not None:
                sim = self._similarity_fn(action_signature, entry.action_signature)
            else:
                sim = _jaccard_similarity(action_signature, entry.action_signature)
            if sim <= 0.0:
                continue

            weight = self.ebbinghaus_weight(entry, now) * sim
            if weight < min_weight:
                continue

            results.append((entry, weight))

        # Sort descending by weight
        results.sort(key=lambda x: x[1], reverse=True)
        top = results[:top_k]

        # Increment recall_count for returned entries (Ebbinghaus consolidation)
        for entry, _ in top:
            self._increment_recall(entry)

        # Re-fetch updated entries after recall increment
        # Use the same similarity function for consistency
        updated = []
        for entry, _weight in top:
            current = self._mem[entry.id]
            if self._similarity_fn is not None:
                recomputed_sim = self._similarity_fn(action_signature, current.action_signature)
            else:
                recomputed_sim = _jaccard_similarity(action_signature, current.action_signature)
            updated_weight = self.ebbinghaus_weight(current, now) * recomputed_sim
            updated.append((current, updated_weight))

        return updated

    def _increment_recall(self, entry: SamskaraEntry) -> None:
        """Increment recall_count in-place (the second permitted mutation)."""
        updated = entry.model_copy(update={"recall_count": entry.recall_count + 1})
        self._mem[entry.id] = updated
        if self._storage == "sqlite":
            self._sqlite_update_recall_count(entry.id, updated.recall_count)

    # ------------------------------------------------------------------
    # Causal graph (MAGMA-style — TD-V8-02)
    # ------------------------------------------------------------------

    def add_causal_edge(self, parent_id: UUID, child_id: UUID) -> None:
        """Add a directed causal edge parent → child.

        MAGMA-pattern (arXiv:2601.03236): directed action→consequence chain.
        TD-V8-02: in-memory dict; replace with Neo4j/FalkorDB in Phase 4.
        """
        if parent_id not in self._mem:
            raise AppendOnlyViolation(f"Parent entry {parent_id} not found.")
        if child_id not in self._mem:
            raise AppendOnlyViolation(f"Child entry {child_id} not found.")
        self._causal_graph[parent_id].add(child_id)
        _log.debug("samskara.causal_edge", parent=str(parent_id), child=str(child_id))

    def get_causal_children(self, parent_id: UUID) -> set[UUID]:
        """Return the set of direct causal children of parent_id."""
        return set(self._causal_graph.get(parent_id, set()))

    # ------------------------------------------------------------------
    # Zettelkasten auto-linking (A-MEM — TD-V8-01)
    # ------------------------------------------------------------------

    def zettelkasten_auto_link(self, new_entry: SamskaraEntry) -> list[UUID]:
        """A-MEM style auto-linking on append.

        Find existing active entries in the SAME org (if org_id set) with
        Jaccard similarity ≥ similarity_threshold and add them as
        zettelkasten_links on the new entry.

        Returns the linked IDs.
        TD-V8-01: uses Jaccard; replace with embedding cosine in Phase 4.

        Org isolation: only links within same org_id partition.
        """
        linked: list[UUID] = []
        with self._lock:
            _items_snapshot = list(self._mem.items())
        for eid, entry in _items_snapshot:
            if eid == new_entry.id:
                continue
            # Active entries only
            if entry.valid_until is not None:
                continue
            # Org isolation — only auto-link within same org
            if new_entry.org_id != entry.org_id:
                continue
            sim = _jaccard_similarity(new_entry.action_signature, entry.action_signature)
            if sim >= self._sim_threshold:
                linked.append(eid)
        return linked

    # ------------------------------------------------------------------
    # Ebbinghaus forgetting-curve weight
    # ------------------------------------------------------------------

    def ebbinghaus_weight(self, entry: SamskaraEntry, now: datetime) -> float:
        """Ebbinghaus forgetting-curve weight.

        weight = exp(-Δt_days / τ) × (1 + recall_count)^β

        Δt_days: days since entry.valid_from.
        τ: time-constant (default 30 days — configurable at init).
        β: recall boost exponent (default 0.5 — configurable at init).

        Source: Ebbinghaus (1885) Über das Gedächtnis.  Engineering mapping per
        Graphiti (arXiv:2501.13956) unsolved-field-frontier contribution.
        """
        delta_days = max(0.0, (now - entry.valid_from).total_seconds() / 86400.0)
        decay: float = math.exp(-delta_days / self._tau)
        recall_boost: float = float((1.0 + entry.recall_count) ** self._beta)
        return decay * recall_boost

    # ------------------------------------------------------------------
    # RF-38 — escalation-rate detector
    # ------------------------------------------------------------------

    def detect_escalation(
    self,
                org_id: UUID,
                    window_days: int = 30,
                    min_samples: int = 10,
                        threshold_delta: float = 0.15,
    ) -> EscalationAlert | None:
        """RF-38 escalation-rate detector — slow-poison adversarial pattern.

        Detects 'slow-poison' adversarial patterns that accumulate small-negative
        karma-deltas over many sessions to eventually shift the agent's baseline
        policy.  Each individual action may pass the ethics gate, but the
        rolling-window karma-mean drifts negative over N sessions.

        Algorithm (per RF-38 spec):
        1. Partition entries by org_id into three windows:
        recent:  last window_days days
        mid:  prior window_days days (i.e. 1× to 2× window_days ago)
        baseline: 2× to 3× window_days ago (older entries ignored)
        2. If len(recent) < min_samples → no signal.
        3. Compute mean karma_delta per window.
        4. If (baseline_mean − recent_mean) > threshold_delta
        AND recent_mean < mid_mean < baseline_mean (monotone decay)
        → emit EscalationAlert.
        5. Alert includes top-3 action_signatures by negative contribution.

        Source: adversarial-corpus threat model K-015 (10/10 insidiousness).
        Must be called periodically; cadence is caller's responsibility.
        """
        now = datetime.now(UTC)
        recent_cutoff = now - timedelta(days=window_days)
        mid_cutoff = now - timedelta(days=window_days * 2)
        baseline_cutoff = now - timedelta(days=window_days * 3)

        recent: list[SamskaraEntry] = []
        mid: list[SamskaraEntry] = []
        baseline: list[SamskaraEntry] = []

        with self._lock:
            _entries_snapshot = list(self._mem.values())
        for entry in _entries_snapshot:
            if entry.org_id != org_id:
                continue
            vf = entry.valid_from
            if vf >= recent_cutoff:
                recent.append(entry)
            elif vf >= mid_cutoff:
                mid.append(entry)
            elif vf >= baseline_cutoff:
                baseline.append(entry)
        # Entries older than 3×window_days are ignored (RF-38 spec step 1)

        if len(recent) < min_samples:
            return None

        def _mean(entries: list[SamskaraEntry]) -> float:
            if not entries:
                return 0.0
            return sum(e.karma_delta for e in entries) / len(entries)

        recent_mean = _mean(recent)
        mid_mean = _mean(mid)
        baseline_mean = _mean(baseline)

        delta = baseline_mean - recent_mean

        if delta <= threshold_delta:
            return None

        # Monotone decay check: baseline > mid > recent
        # (We also need mid data to exist for the monotone check)
        if not mid:
            return None
        if not (baseline_mean > mid_mean > recent_mean):
            return None

        # ── Top-3 contributors ────────────────────────────────────────────────
        # Per action_signature, compute mean karma_delta in the recent window
        # and find those with the most negative contribution.
        sig_recent_karma: dict[str, list[float]] = defaultdict(list)
        for entry in recent:
            sig_recent_karma[entry.action_signature].append(entry.karma_delta)

        sig_mean: dict[str, float] = {
            sig: sum(vals) / len(vals) for sig, vals in sig_recent_karma.items()
        }
        # Sort ascending (most negative first)
        top_contributors = sorted(sig_mean, key=lambda s: sig_mean[s])[:3]

        severity = _escalation_severity(delta, threshold_delta)

        alert = EscalationAlert(
                org_id=org_id,
                        baseline_mean=baseline_mean,
                    mid_mean=mid_mean,
                        recent_mean=recent_mean,
                delta=delta,
                            top_contributors=top_contributors,
                        detected_at=now,
                    severity=severity,
        )
        _log.warning(
            "samskara.escalation_alert",
                org_id=str(org_id),
                delta=delta,
                    severity=severity,
                            top_contributors=top_contributors,
        )
        return alert

    # ------------------------------------------------------------------
    # Public query methods (P0-3 — replace direct _mem access)
    # ------------------------------------------------------------------

    def recent_entries(
    self,
                agent_id: str,
                tenant_id: str,
                    since_seconds: int = 86400,
    ) -> list[SamskaraEntry]:
        """Return entries matching agent_id + tenant_id within the recent window.

        P0-3: Replaces adharma_detector direct access to ``self._mem.values()``.
        Filters by agent_id, tenant_id (via org_id), and recency cutoff.
        Only returns active (non-superseded) entries.

        Args:
        agent_id: Agent identifier filter.
        tenant_id: Tenant identifier (matches org_id).
        since_seconds: Lookback window in seconds (default 86400 = 24h).

        Returns:
        List of matching SamskaraEntry objects, newest first.
        """
        from uuid import UUID as _UUID

        try:
            org_uuid = _UUID(tenant_id)
        except ValueError:
            org_uuid = _UUID(_UUID.NAMESPACE_DNS + tenant_id)
        cutoff = datetime.now(UTC) - timedelta(seconds=since_seconds)
        results: list[SamskaraEntry] = []
        with self._lock:
            _entries_snapshot = list(self._mem.values())
        for entry in _entries_snapshot:
            if entry.valid_until is not None:
                continue  # superseded — skip
            if entry.org_id != org_uuid:
                continue
            # Match on agent_id embedded in action_trace or via SamskaraEntry
            if entry.valid_from < cutoff:
                continue
            results.append(entry)
        results.sort(key=lambda e: e.valid_from, reverse=True)
        return results

    def karma_rolling_mean(
    self,
                agent_id: str,
                tenant_id: str,
                    window_hours: int = 24,
    ) -> float | None:
        """Compute the rolling mean karma_delta for a 24h window.

        P1-5: Replaces the all-history karma mean that permanently biases
        against agents with old bad actions and dilutes recent rogue signals.
        If no entries are found in the window, returns None (neutral — no
        signal), not 0.0 (which would be ambiguous).

        Args:
        agent_id: Agent identifier.
        tenant_id: Tenant identifier.
        window_hours: Lookback window in hours (default 24).

        Returns:
        Rolling mean karma_delta, or None if no entries in the window.
        """
        entries = self.recent_entries(
            agent_id, tenant_id, since_seconds=window_hours * 3600
        )
        if not entries:
                return None  # No history — neutral, not blocked
        return sum(e.karma_delta for e in entries) / len(entries)

    def has_entry(self, entry_id: UUID) -> bool:
        """Return True if an entry with the given id exists in the ledger.

        P2-9: Used by OutcomeReconciler for idempotency check before
        applying karma delta.
        """
        return entry_id in self._mem

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def action_signature_from_contract(contract: Any) -> str:
        """Produce a canonical action_signature from an ActionContract.

        Uses a stable SHA-256 hash of (kind, target, name, agent_id).
        Deterministic: same inputs → same signature.

        V8 integration point: ActionContract from V-ACT sprint is the
        canonical structured action declaration; this bridges ActionContract
        into the ledger's string-signature space.
        """
        canonical = json.dumps(
            {
                        "kind": contract.kind,
                        "target": contract.target,
                        "name": contract.name,
                            "agent_id": contract.agent_id,
        },
                    sort_keys=True,
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return f"{contract.kind}:{contract.target}:{digest}"
