"""
    DharmaOS IIT-Approximate Integration Score — bounded causal-connectivity metric.

    PURPOSE
    -------
    Provides a computationally-feasible approximation of system integration quality
    for the TuriyaSidecar's TuriyaObservation.integration_score field. This metric
    captures how well-connected (integrated vs. modular) the causal subgraph of a
    decision event is, as a proxy for decision coherence.

    IMPORTANT: This is NOT real IIT Phi. See RF-13 and the explicit disclaimer below.

    ALGORITHM (weighted-mean causal-edge approximation)
    ----------------------------------------------------
    Input: n × n adjacency matrix A (values 0 or 1; n ≤ MAX_NODES_DEFAULT=12).

    1. For each node i:
    out_weight[i] = sum(A[i]) / n  (normalized out-degree)
    in_weight[i]  = sum(A[:,i]) / n  (normalized in-degree)
    node_score[i] = out_weight[i] * in_weight[i]

    2. integration_score ≈ mean(node_score) across all n nodes.
    Result is in [0.0, 1.0].

    Interpretation: higher score = more bidirectional causal connectivity =
    higher integration quality. A fully disconnected graph scores 0.0;
    a fully bidirectional complete graph scores close to 1.0.

    MEMOIZATION
    -----------
    Results are memoized keyed on a tuple-of-tuples representation of A.
    Repeated calls with the same graph are O(1). Cache bounded at 512 entries
    (LRU eviction) to prevent OOM.

    KEY FUNCTIONS
    -------------
    compute_integration_score_approx(adjacency_matrix) → float.
    MAX_NODES_DEFAULT : int = 12 (node count ceiling).
    IITPayloadTooLarge: Exception raised when n > MAX_NODES_DEFAULT.

    EXPLICIT DISCLAIMER
    ---------------------------
    Real IIT 4.0 Phi is O(n⁵ · 3ⁿ) — practical ceiling ≈ 10–12 binary nodes.
    Computing Phi at LLM scale is computationally infeasible. This module provides
    a WEIGHTED-MEAN-OF-CAUSAL-EDGES approximation for enterprise audit use only.
    All patent claims reference "IIT-approximate" or "integration-score analogue",
    never "computes Phi" or "integrated information" without the approximation
    qualifier. (Albantakis et al. arXiv:2212.14787; PyPhi library)

    Governance origin: Integration score as a proxy for decision coherence —
    functional analogue of IIT Phi (Albantakis et al. arXiv:2212.14787) for
    enterprise audit monitoring of the DharmaOS consciousness substrate.

    agent safety layer — Sprint V3: IIT-Approximate Integration Score.

    **EXPLICIT DISCLAIMER:**
    This module does NOT compute Integrated Information Theory (IIT) Phi.
    Real IIT 4.0 Phi computation is O(n⁵ · 3ⁿ) — practical ceiling ≈ 10–12
    binary nodes (Albantakis et al. arXiv:2212.14787; PyPhi library).
    Computing Phi at LLM scale is computationally infeasible.

    The `compute_integration_score_approx` function in this module provides
    a WEIGHTED-MEAN-OF-CAUSAL-EDGES approximation for enterprise audit use
    only.  It is NOT real IIT Phi.  It is a *functional analogue* — a bounded,
    memoized approximation that serves as an integration-quality signal for
    the Turīya sidecar's `TuriyaObservation.integration_score` field.

    Patent-language note: all claims in CLAIMS.md that reference integration
    scores say "IIT-approximate" or "integration-score analogue", never
    "computes Phi" or "integrated information" without the approximation
    qualifier.  This is mandated by RF-13.

    Phase 4 plan: integrate PyPhi's `feature/iit-4.0` branch for design-time
    audit on small subgraphs (≤ 12 nodes)

    **Algorithm (V3 approximation):**
    For an adjacency matrix A (n × n, values 0 or 1):
    1. Compute out-degree weight for each node: w_i = sum(A[i]) / n
    2. Compute in-degree weight for each node: v_i = sum(A[:,i]) / n
    3. Integration score ≈ mean(w_i * v_i) across all nodes
    4. Normalise to [0.0, 1.0] by dividing by max possible (1.0)

    Interpretation: higher score = more bidirectional causal connectivity =
    higher "integrated" quality of the subgraph.  This captures the spirit
    of IIT (integrated vs. modular) without the exponential cost.

    **HDMP memoisation:**
    Results are memoised keyed on a tuple-of-tuples representation of the
    adjacency matrix.  Repeated calls with the same graph are O(1).
    The cache is bounded at 512 entries (LRU eviction) to prevent OOM.

    Port lineage:
    Concept from /opt/agent-core/sakshi_circuit.py `_calculate_phi()`
    Re-implemented with honest approximation labelling per RF-13.
    Sprint V3, 2026-04-17.
    """

from __future__ import annotations

import functools
import json
from typing import Final

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# Maximum node count enforced by compute_integration_score_approx.
# Prevents OOM for callers that pass unbounded adjacency matrices.
# Per RF-13 and VEDIC-MASTER-PLAN-V1 §3.1 (IIT ceiling ≈ 10–12 nodes).
MAX_NODES_DEFAULT: Final[int] = 12

# Maximum byte size for the serialised adjacency-matrix payload.
# Prevents DoS via oversized JSON payloads before the node-count check
# even runs (V-SEC-01 / RF-36).  10 KB is generous for a 12×12 matrix.
MAX_PAYLOAD_BYTES_DEFAULT: Final[int] = 10 * 1024  # 10 KB


class IITPayloadTooLarge(ValueError):  # noqa: N818
    """Raised when a serialised adjacency-matrix payload exceeds the byte-size limit.

        V-SEC-01 / RF-36: byte-size check runs BEFORE the expensive
        node-count validation so oversized payloads are rejected cheaply.
        """


# ---------------------------------------------------------------------------
# Internal memoised helper
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=512)
def _compute_approx_cached(adj_tuple: tuple[tuple[int, ...], ...]) -> float:
    """Compute the IIT-approximate score from a frozen adjacency tuple.

        This is the inner cached function.  The outer
        `compute_integration_score_approx` converts the list-of-lists to a
        tuple-of-tuples and delegates here.

        Args:
        adj_tuple: Frozen adjacency matrix as tuple-of-tuples (values 0 or 1).

        Returns:
        float in [0.0, 1.0].  0.0 for empty or disconnected graphs.
        """
    n = len(adj_tuple)
    if n == 0:
        return 0.0

    total_score = 0.0
    for i in range(n):
        # Out-degree weight: fraction of outgoing edges from node i
        out_w = sum(adj_tuple[i]) / n
        # In-degree weight: fraction of incoming edges to node i
        in_w = sum(adj_tuple[row][i] for row in range(n)) / n
        # Causal integration contribution for node i
        total_score += out_w * in_w

    # Average across all nodes; clamp to [0.0, 1.0]
    integration_analogue = total_score / n
    return min(1.0, max(0.0, integration_analogue))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_integration_score_approx(
    adjacency: list[list[int]],
    max_nodes: int = MAX_NODES_DEFAULT,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES_DEFAULT,
) -> float:
    """Compute an HDMP-memoised IIT-approximate integration score.

        **NOT true IIT Phi** — this is a weighted-mean functional analogue
        for enterprise audit use only.  See module docstring and RF-13 in
        RESEARCH-FLAGS.md.

        Algorithm: weighted mean of (out-degree × in-degree) across all nodes.
        Captures bidirectional causal connectivity without exponential cost.

        Memoisation: results are cached by adjacency-matrix content (LRU, 512 max).
        Repeated calls with the same graph are O(1).

        Phase 4 TODO: Replace this approximation with PyPhi
        `feature/iit-4.0` for design-time audits on small subgraphs.
        See VEDIC-MASTER-PLAN-V1 §3.1.

        Args:
        adjacency: Square adjacency matrix as list-of-lists of 0/1 integers.
        Each row/column is a node; A[i][j]=1 means there is a causal
        edge from node i to node j.
        max_nodes: Upper bound on node count.  Raises ValueError if the
        matrix exceeds this limit (default 12, per RF-13 ceiling).
        max_payload_bytes: Maximum byte size of the serialised JSON payload.
        Raises IITPayloadTooLarge if exceeded (default 10 KB, V-SEC-01).

        Returns:
        float in [0.0, 1.0].
        0.0 = no integration (disconnected graph or empty matrix).
        1.0 = maximum integration (fully bidirectionally connected).

        Raises:
        IITPayloadTooLarge: If serialised payload exceeds max_payload_bytes.
        ValueError: If len(adjacency) > max_nodes (prevents OOM).
        ValueError: If the matrix is not square or contains non-0/1 values.
        """
    # V-SEC-01 / RF-36: byte-size check runs first — cheap rejection before
    # the O(n²) node-count + square-matrix validation below.
    if max_payload_bytes > 0:
        try:
            serialised = json.dumps(adjacency, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Adjacency matrix is not JSON-serialisable: {exc}") from exc
        byte_size = len(serialised.encode("utf-8"))
        if byte_size > max_payload_bytes:
            raise IITPayloadTooLarge(
                f"Adjacency matrix payload is {byte_size} bytes which exceeds "
                f"max_payload_bytes={max_payload_bytes}. "
                "Reject oversized payload to prevent DoS (V-SEC-01 / RF-36). "
                "Reduce the matrix or raise max_payload_bytes deliberately."
            )

    n = len(adjacency)

    if n > max_nodes:
        raise ValueError(
            f"Adjacency matrix has {n} nodes which exceeds max_nodes={max_nodes}. "
            "compute_integration_score_approx is bounded for OOM prevention per RF-13. "
            "For larger graphs use HDMP partitioning or Phase 4 PyPhi integration."
        )

    # Validate square matrix
    for i, row in enumerate(adjacency):
        if len(row) != n:
            raise ValueError(
                f"Adjacency matrix is not square: row {i} has {len(row)} columns, expected {n}."
            )
        for j, val in enumerate(row):
            if val not in (0, 1):
                raise ValueError(
                    f"Adjacency matrix must contain only 0 or 1 values. "
                    f"Got {val!r} at position ({i}, {j})."
                )

    if n == 0:
        return 0.0

    # Convert to frozen tuple-of-tuples for memoisation
    adj_tuple = tuple(tuple(row) for row in adjacency)
    score = _compute_approx_cached(adj_tuple)

    logger.debug(
        "iit_approx.computed",
        nodes=n,
        score=round(score, 4),
        note="NOT-real-Phi-RF13",
    )
    return score


def clear_integration_cache() -> None:
    """Clear the LRU memoisation cache.

        Call this in tests to ensure cache state doesn't bleed between test cases.
        In production, the cache persists for the process lifetime (intended).
        """
    _compute_approx_cached.cache_clear()
    logger.debug("iit_approx.cache_cleared")
