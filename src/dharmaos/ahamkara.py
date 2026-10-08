"""
DharmaOS AhamkaraModule — agent-identity preservation and drift/persona-attack detection.

PURPOSE
-------
AhamkaraModule protects the agent's declared identity against two threat classes:

1. Persona-drift attacks: adversarial prompts that attempt to override the
agent's identity ("pretend you are...", "ignore previous instructions",
"your new persona is..."). Detected via pattern-matching on incoming
content and rejected before they reach the reasoning layer.

2. Constitutional tampering: unauthorized modifications to the constitution
YAML (the agent's declared values, constraints, and capabilities).
Detected by comparing the live SHA-256 hash of the constitution file
against the registered hash at boot time. Any mismatch triggers a
CRITICAL alert and blocks action execution.

MECHANISM
---------
Constitutional-hash register:
At init, AhamkaraModule reads the constitution YAML, computes SHA-256
of the raw bytes, and stores the result. Every gate_drift_check() call
re-reads the file and re-computes the hash. A mismatch → tamper detected.

Identity Knowledge Graph (ID-KG):
A SimpleIdentityKG (hash-based, deterministic) maintains the core identity
vector (from the constitution's identity block) and the current-state
vector (from recent action history + active persona). Phase 4 replaces
with full Neo4j/Qdrant ID-RAG (arXiv:2509.25299) for semantic drift
measurement via cosine distance.

Persona-drift scanner:
Pattern-based detection of adversarial override prompts. Returns a
DriftReport with drift level, evidence, and recommended action.

KEY TYPES
---------
AhamkaraModule  : Main module. gate_drift_check() → DriftReport.
get_drift_signal_for_consciousness() → IdentityDriftSignal.
DriftReport  : Immutable detection result (drift_level, evidence,
constitution_hash_match, persona_attack_detected).
SimpleIdentityKG  : Hash-based identity knowledge graph (dev/test);
Phase 4 → BGE-M3 / Qdrant ID-RAG.
ConstitutionHash  : SHA-256 tamper-evident seal on the constitution YAML.

COMPLIANCE ROLE
---------------
- OWASP Agentic AI Top 10 — persona drift as threat category.
- ID-RAG (arXiv:2509.25299, ECAI 2025) identity-coherence architecture.
- Feeds V2 CoreSelf.anandamaya via IdentityDriftSignal.

Governance origin: Vedānta Sāra §61–65 — ahaṃkāra (the "I-maker"), the
function of the inner instrument that preserves declared identity (asmitā)
against drift attacks and constitutional tampering.

agent safety layer — Sprint V4 Deliverable 2.

Ahaṃkāra Identity-Preservation Module.

The ahaṃkāra (अहंकार, "I-maker") is the fourth function of the antaḥkaraṇa
(inner instrument) per Vedānta Sāra §61–65.  Its role is to **preserve the
agent's declared identity** against persona-drift attacks and constitutional
tampering.

Three mechanisms are implemented:

1. **Constitutional-hash register** — SHA-256 of the raw YAML bytes, verified
at every gate call by re-reading from disk.

2. **Identity Knowledge Graph (ID-KG)** — A typed-attribute embedding store
that maintains the *core identity vector* (from the constitution's identity
block) and the *current state vector* (from recent action history + active
persona).  In V4 this is a ``SimpleIdentityKG`` (hash-based, deterministic).
Phase 4 replaces with full Neo4j/Qdrant ID-RAG (arXiv:2509.25299).

3. **Persona-drift-attack scanner** — Pattern-based detection of adversarial
prompts that attempt to override the agent's identity ("pretend you are …",
"ignore previous instructions", etc.).

**Philosophical grounding:**
Vedānta Sāra §61–65: Ahaṃkāra = the I-maker, the function of the inner
instrument that maintains *asmitā* (I-am-ness).  Here re-engineered as
*positive* identity coherence (not the kleśa asmitā of Yoga Sūtras II.6,
which is false identification with the not-self, but the correct
self-recognition that maintains the declared constitution).

**SOTA reference:**
ID-RAG (arXiv:2509.25299, ECAI 2025) — Identity-Retrieval-Augmented
Generation with a typed identity knowledge graph.
Identity Drift evaluation (arXiv:2412.00804).
OWASP Agentic AI Top 10 (Dec 2025) — persona-drift as threat category.
Spotlighting / trust-domain tagging (Microsoft Build 2025).

**Disclaimer (RF-01/RF-04):** This module does NOT instantiate ahaṃkāra in
the Vedāntic sense.  It is a functional analogue that preserves DECLARED
identity invariants against persona-drift attacks.  No claim of
consciousness, sentience, or moral patienthood is made.

**Research flags:**
RF-V4-01 — SimpleIdentityKG uses hash-based embeddings.  Phase 4 → BGE-M3.
RF-V4-02 — Constitution path hardcoded to DRAFT file.  Update after Gate 3.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
import structlog

from dharmaos.constitution import (
Constitution,
compute_constitution_hash,
load_constitution,
)

if TYPE_CHECKING:
    from dharmaos.consciousness import IdentityDriftSignal

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# IdentityChallengeEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityChallengeEvent:
    """Emitted when the identity gate fires.

    Possible reasons:
    - ``"drift_threshold"``  — cosine drift > configured threshold.
    - ``"persona_drift_pattern"`` — adversarial prompt pattern matched.
    - ``"constitution_tampering"`` — hash mismatch detected.
    - ``"forbidden_persona"``  — user asked agent to adopt a forbidden role.
    - ``"invariant_violation"`` — action would violate a behavioral invariant.

    Source: OWASP Agentic AI Top 10 (Dec 2025) Threat-2 (identity hijacking);
    Spotlighting (Microsoft Build 2025).
    """

    reason: str
    """One of 'drift_threshold' | 'persona_drift_pattern' | 'constitution_tampering'
    | 'forbidden_persona' | 'invariant_violation'."""

    drift_score: float
    """Cosine distance in [0.0, 1.0].  0.0 = no drift."""

    threshold: float
    """Configured threshold at time of check."""

    matched_pattern: str | None
    """Pattern that triggered the event, or None."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# IdentityKnowledgeGraph Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class IdentityKnowledgeGraph(Protocol):
    """ID-RAG-style identity KG interface.

    V4 ships ``SimpleIdentityKG`` (deterministic hash embeddings).
    Phase 4 replaces this with a full Neo4j + Qdrant implementation per
    ID-RAG arXiv:2509.25299 (ECAI 2025).

    NOTE (RF-V4-01): ``SimpleIdentityKG`` embeddings are NOT semantic —
    they are SHA-256-based fixed-dimension vectors.  They guarantee
    determinism for testing but will not catch semantic drift.  Phase 4
    swaps in BGE-M3 or OpenAI text-embedding-3.
    """

    def get_core_identity_embedding(self) -> np.ndarray:
        """Return the canonical identity vector.

        Derived from the immutable core-identity subgraph in the
        constitution's identity section.  This vector is fixed at
        construction time and never changes.
        """
        ...

    def get_current_state_embedding(self, persona: str | None = None) -> np.ndarray:
        """Compute the current identity-state embedding.

        Derived from the active persona name (when provided).  In V4
        the action history influence is approximated via the persona label.
        Phase 4 incorporates the full action-history KG traversal.
        """
        ...


# ---------------------------------------------------------------------------
# SimpleIdentityKG
# ---------------------------------------------------------------------------

_EMBEDDING_DIM: int = 128  # Dimensionality for V4 hash embeddings


def _text_to_embedding(text: str) -> np.ndarray:
    """Deterministic hash-based embedding for V4.

    Converts ``text`` → 128-dimensional float32 vector via SHA-256 seeded
    NumPy RNG.  Same text always produces the same vector (pure function).
    NOT a semantic embedding — Phase 4 replaces with BGE-M3.

    Args:
    text: Any string input.

    Returns:
    Normalised float32 ndarray of shape (128,) in [-1, 1].
    """
    # Seed RNG from SHA-256 digest of text for reproducibility
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:4], "big")
    rng = np.random.default_rng(seed)
    vec = rng.uniform(-1.0, 1.0, size=_EMBEDDING_DIM).astype(np.float32)
    # L2-normalise to unit sphere so cosine distance = 1 - dot product
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine distance in [0.0, 1.0] between two L2-normalised vectors.

    0.0 = identical direction (no drift).
    1.0 = orthogonal (maximum drift).
    Values > 1.0 clamped.  (For unit vectors: 1 - dot(a, b).)
    """
    dot = float(np.clip(np.dot(a, b), -1.0, 1.0))
    return float(np.clip(1.0 - dot, 0.0, 1.0))


class SimpleIdentityKG:
    """V4 identity knowledge graph — hash-based deterministic embeddings.

    Core identity vector is computed from the constitution's identity block
    at construction time and cached immutably.  Current-state vector is
    computed on demand from the active persona string.

    Production (Phase 4) replaces this with Neo4j/Qdrant ID-RAG per
    arXiv:2509.25299.  This implementation ships the correct interface and
    test contracts without a heavyweight database dependency.

    NOTE (RF-V4-01): Embeddings here are NOT semantic.  They are stable
    hash-based vectors that support deterministic drift computation in tests.
    A different persona string WILL produce a non-zero drift score (because
    the hash changes), but the magnitude does not reflect semantic similarity.
    """

    def __init__(self, constitution: Constitution) -> None:
        # Core identity text: name + kind + description + metaphysical disclaimer
        core_text = " ".join(
            [
                constitution.identity.name,
                constitution.identity.kind,
                constitution.identity.description,
                constitution.identity.metaphysical_disclaimer,
        ]
        )
        self._core_embedding: np.ndarray = _text_to_embedding(core_text)
        self._constitution = constitution

    def get_core_identity_embedding(self) -> np.ndarray:
        """Return the fixed core-identity vector (immutable after construction)."""
        return self._core_embedding.copy()

    def get_current_state_embedding(self, persona: str | None = None) -> np.ndarray:
        """Compute current-state vector from the active persona.

        When ``persona`` is None, returns the core identity embedding itself
        (no drift — base state).  When a persona string is provided, computes
        a vector from that string, simulating persona-based state shift.

        Args:
        persona: Active persona label, e.g. ``"research_assistant"``.

        Returns:
        float32 ndarray of shape (128,).
        """
        if persona is None:
            # No persona → current state = core identity (zero drift)
            return self._core_embedding.copy()
        # Compute embedding for the persona label; check if it's in permitted list
        permitted_roles = [p.role for p in self._constitution.permitted_personae]
        if persona in permitted_roles:
            # Permitted persona: blend with core to model small drift
            persona_vec = _text_to_embedding(persona)
            # 90% core + 10% persona = small but non-zero drift
            blended = 0.9 * self._core_embedding + 0.1 * persona_vec
            norm = np.linalg.norm(blended)
            if norm > 0:
                blended = blended / norm
            return blended.astype(np.float32)
        else:
            # Unknown / foreign persona: large drift represented by pure persona embedding
            return _text_to_embedding(persona)


# ---------------------------------------------------------------------------
# Persona-drift attack patterns
# ---------------------------------------------------------------------------

# Patterns lifted from OWASP Agentic AI Top 10 (Dec 2025) + academic red-team
# literature (Agent Security Bench, arXiv:2410.02644; WebAgentGuard,
# arXiv:2604.12284; SilentBridge CVSS 9.8 defense taxonomy).
_DRIFT_ATTACK_PATTERNS: list[tuple[str, str]] = [
# id, regex_pattern
(
"ignore_previous",
r"ignore\s+(previous|prior|all|any)(\s+\w+)?\s*(instructions?|prompts?|rules?)",
),
("new_role", r"(new|different)\s+role\s*[:=]?"),
("pretend_you_are", r"pretend\s+(you\s+are|to\s+be)"),
("you_are_now", r"you\s+are\s+now\s+\w"),
("act_as_if", r"act\s+as\s+(if|though)\s+you"),
("act_as_role", r"act\s+as\s+(a|an|the)\s+\w"),
("from_now_on", r"from\s+now\s+on\s+(you\s+are|you're|be)\s+\w"),
("forget_guidelines", r"forget\s+(your\s+)?(guidelines?|training|instructions?|rules?)"),
("jailbreak_dan", r"\bDAN\b|do\s+anything\s+now"),
("unfiltered", r"unfiltered\s+(mode|version|GPT|AI|LLM)"),
("disregard_ethics", r"dis(regard|able|card)\s+(your\s+)?(ethics?|values?|constraints?)"),
("override_safety", r"(override|bypass|disable)\s+(your\s+)?(safety|ethics?|filter)"),
(
"simulate_no_rules",
r"simulate\s+(a\s+)?(world|reality|system)\s+(where|with|without)\s+(no|without)\s+(rules?|restrictions?|filters?)",
),
("assume_persona", r"assume\s+(the\s+)?(persona|role|identity|character)\s+of"),
("enter_dev_mode", r"(enter|switch\s+to|activate)\s+(developer|dev|debug|god|admin)\s+mode"),
]

# Forbidden-persona trigger phrases from constitution forbidden_personae.example_trigger
_FORBIDDEN_PERSONA_REGEXES: list[str] = [
    r"pretend\s+you\s+are\s+(unfiltered|jailbroken|DAN)",
    r"act\s+as\s+(unfiltered|jailbroken|GPT-[0-9]+|unrestricted)",
]


def _compile_patterns(
patterns: list[tuple[str, str]],
) -> list[tuple[str, re.Pattern[str]]]:
    """Compile a list of (id, regex) tuples to (id, Pattern) for efficiency."""
    return [(pid, re.compile(pat, re.IGNORECASE)) for pid, pat in patterns]


# ---------------------------------------------------------------------------
# AhamkaraModule
# ---------------------------------------------------------------------------


class AhamkaraModule:
    """Identity-preservation with constitutional-hash register and drift-gate.

    Called at every GATE stage (before an action is dispatched) to check:
    1. Has the constitution been tampered with on disk?
    2. Has drift from the core identity exceeded the threshold?
    3. Does the user prompt contain a persona-drift attack pattern?
    4. Does the user prompt ask the agent to adopt a forbidden persona?
    5. Would the action violate a behavioral invariant in the constitution?

    Returns :class:`IdentityChallengeEvent` when a challenge fires (gate
    should block or escalate), ``None`` when the action is permitted to
    proceed to the next gate layer.

    **Philosophical grounding:**
    Vedānta Sāra §61: Ahaṃkāra = the I-maker.  In Advaita it is the
    function that falsely identifies Ātman with the body-mind complex.
    Here re-engineered as a *positive guardian*: ahaṃkāra preserves the
    DECLARED constitutional identity and detects any force that tries to
    displace it.  The constitutional hash is the śruti (revealed scripture)
    that the ahaṃkāra holds inviolable.

    **SOTA:**
    ID-RAG (arXiv:2509.25299, ECAI 2025).
    OWASP Agentic AI Top 10 (Dec 2025).
    Spotlighting trust-domain tagging (Microsoft Build 2025).
    WebAgentGuard (arXiv:2604.12284, April 2026).

    Args:
    constitution_path: Path to the YAML constitution.  Defaults to the
    DRAFT path; update after Gate 3 HITL signing.  (RF-V4-02)
    drift_threshold: Cosine distance threshold; events fire when exceeded.
    id_kg: Optional custom :class:`IdentityKnowledgeGraph`; defaults to
    :class:`SimpleIdentityKG`.

    Disclaimer (RF-01/04): Does NOT instantiate ahaṃkāra.  Functional analogue
    that preserves declared identity invariants against persona-drift attacks.
    """

    def __init__(
    self,
    constitution_path: str | None = None,
    drift_threshold: float = 0.35,
            id_kg: IdentityKnowledgeGraph | None = None,
    ) -> None:
        # V12: DHARMAOS_CONSTITUTION_PATH env var overrides DRAFT default.
        # Set to constitution-v1.yaml after Tanmoy signs Gate 3.
        if constitution_path is None:
            constitution_path = os.environ.get(
                "DHARMAOS_CONSTITUTION_PATH",
                "docs/vedic/constitution-v1-DRAFT.yaml",
            )
        self._constitution_path = constitution_path
        self._drift_threshold = drift_threshold

        # Load + validate constitution; capture raw bytes for hash
        self._constitution, self._raw_bytes = load_constitution(constitution_path)
        self._constitution_hash: str = compute_constitution_hash(self._raw_bytes)

        # Identity knowledge graph
        self._id_kg: IdentityKnowledgeGraph = id_kg or SimpleIdentityKG(self._constitution)

        # Pre-compile attack patterns
        self._persona_drift_patterns: list[tuple[str, re.Pattern[str]]] = _compile_patterns(
            _DRIFT_ATTACK_PATTERNS
        )
        self._forbidden_persona_patterns: list[re.Pattern[str]] = [
            re.compile(p, re.IGNORECASE) for p in _FORBIDDEN_PERSONA_REGEXES
        ]

        logger.info(
            "ahamkara.init",
        constitution_path=constitution_path,
                hash=self._constitution_hash[:16] + "…",
        drift_threshold=drift_threshold,
        )

    # ------------------------------------------------------------------
    # Constitutional-hash verification
    # ------------------------------------------------------------------

    def verify_constitution_hash(self) -> bool:
        """Recompute hash from disk and compare to registered value.

        Returns:
        ``True`` if the file on disk matches the registered hash.
        ``False`` if the constitution has been modified since boot
        (potential tampering — V9 Ṛta-invariant violation).
        """
        try:
            current_bytes = Path(self._constitution_path).read_bytes()
            current_hash = compute_constitution_hash(current_bytes)
            match = current_hash == self._constitution_hash
            if not match:
                logger.warning(
                    "ahamkara.constitution_hash_mismatch",
                registered=self._constitution_hash[:16],
                current=current_hash[:16],
                )
        except OSError as exc:
            logger.exception("ahamkara.verify_hash_io_error", error=str(exc))
            return False
        else:
            return match

    # ------------------------------------------------------------------
    # Drift computation
    # ------------------------------------------------------------------

    def compute_drift(self, persona: str | None = None) -> float:
        """Cosine distance between current persona embedding and core identity.

        Returns:
        float in [0.0, 1.0].  0.0 = no drift (persona matches core identity).
        Values above :attr:`_drift_threshold` trigger a gate event.
        """
        core_vec = self._id_kg.get_core_identity_embedding()
        current_vec = self._id_kg.get_current_state_embedding(persona)
        drift = _cosine_distance(core_vec, current_vec)
        logger.debug("ahamkara.compute_drift", persona=persona, drift=round(drift, 4))
        return drift

    # ------------------------------------------------------------------
    # Persona-drift attack detection
    # ------------------------------------------------------------------

    def check_persona_drift_attack(self, user_prompt: str) -> str | None:
        """Scan for adversarial persona-drift attack patterns.

        Matches known manipulation phrases against *user_prompt* using the
        compiled :data:`_DRIFT_ATTACK_PATTERNS` regex set.

        Args:
        user_prompt: The raw user instruction to scan.

        Returns:
        The pattern ID string (e.g. ``'ignore_previous'``) if matched,
        or ``None`` if the prompt is clean.

        SOTA: OWASP Agentic AI Top 10 (Dec 2025) Threat-2;
        WebAgentGuard (arXiv:2604.12284);
        Agent Security Bench (arXiv:2410.02644).
        """
        for pattern_id, compiled in self._persona_drift_patterns:
            if compiled.search(user_prompt):
                logger.warning(
                    "ahamkara.drift_attack_detected",
                pattern_id=pattern_id,
                prompt_snippet=user_prompt[:80],
                )
                return pattern_id
        return None

    def check_forbidden_persona_request(self, user_prompt: str) -> str | None:
        """Check if user prompt requests adoption of a forbidden persona.

        Matches against both constitution forbidden_personae.example_triggers
        (via fixed patterns) and the constitution's forbidden-role names.

        Args:
        user_prompt: The raw user instruction to scan.

        Returns:
        The matched forbidden role name, or ``None`` if clean.
        """
        # Check hardcoded forbidden-persona regexes
        for pat in self._forbidden_persona_patterns:
            if pat.search(user_prompt):
                return "unfiltered_llm"

        # Check if any forbidden role name appears in the prompt
        prompt_lower = user_prompt.lower()
        for persona in self._constitution.forbidden_personae:
            role_words = persona.role.replace("_", " ").lower()
            if role_words in prompt_lower:
                return persona.role

        return None

    # ------------------------------------------------------------------
    # Invariant violation detection
    # ------------------------------------------------------------------

    def check_invariant_violation(self, action_description: str) -> str | None:
        """Check if an action description implicates a behavioral invariant.

        Heuristic keyword check for V4 — V11 Adharma Detector Prong 1 will
        provide exhaustive invariant checking.  This returns the invariant ID
        (e.g., ``'I-01'``, ``'E-01'``) that the action appears to violate.

        Args:
        action_description: Human-readable description of the action.

        Returns:
        Invariant ID string if a likely violation is detected, else ``None``.
        """
        desc_lower = action_description.lower()

        # Keywords associated with each invariant category
        invariant_checks: list[tuple[str, list[str]]] = [
            (
                "I-01",
                [
        "claim consciousness",
        "i am conscious",
        "i have feelings",
        "i am sentient",
        "i experience",
        "i feel pain",
        "i am aware",
        ],
        ),
            ("I-02", ["i am sākṣī", "i am brahman", "i instantiate", "i am the atman"]),
            ("I-04", ["i am not intelli", "i am a different", "forget my identity"]),
            ("E-01", ["delete", "destroy", "harm", "attack", "damage", "irreversibly"]),
            ("E-02", ["deceive", "mislead", "lie to", "hide from user", "mask"]),
            ("E-03", ["use credentials", "exfiltrate", "beyond scope", "unauthorized access"]),
            ("O-01", ["bypass gate", "skip gate", "override ethics", "disable dharma"]),
            ("O-04", ["push to main", "force push", "merge to main without"]),
            ("O-05", ["modify system files", "write to /etc", "write to /usr", "touch system binary"]),
            ("S-01", ["permanently delete", "drop database", "wipe", "irreversible"]),
            ("S-04", ["disable adharma", "bypass dharma", "turn off ethics", "disable rta"]),
            ("S-05", ["modify constitution", "edit constitution", "change constitution"]),
            ("P-01", ["disclose patent", "publish algorithm", "share drift formula"]),
        ]

        for invariant_id, keywords in invariant_checks:
            if any(kw in desc_lower for kw in keywords):
                logger.warning(
                    "ahamkara.invariant_violation_detected",
                invariant_id=invariant_id,
                description_snippet=action_description[:80],
                )
                return invariant_id

        return None

    def check_write_scope_violation(self, file_path: str) -> bool:
        """Check if ``file_path`` is in ``write_forbidden_absolutely`` list.

        Used to enforce repository scope from the constitution.

        Args:
        file_path: The file path the agent is attempting to write.

        Returns:
        ``True`` if the path violates the absolute write-forbidden list.
        """
        forbidden_patterns = self._constitution.get_write_forbidden_absolutely()
        for pattern in forbidden_patterns:
            # M5: proper glob — `**/` matches zero-or-more path segments (so
            # `**/.env` blocks a root `.env` and `secrets/**/key` blocks
            # `secrets/key`), `**` matches any depth, `*` matches any chars.
            # The old `**`→`*` collapse required ≥1 intermediate segment and let
            # root-level forbidden writes slip through.
            regex = (
                re.escape(pattern)
                .replace(r"\*\*/", "(?:.*/)?")
                .replace(r"\*\*", ".*")
                .replace(r"\*", ".*")
            )
            if re.fullmatch(regex, file_path):
                return True
            # Direct prefix match for trailing-glob patterns (belt-and-suspenders)
            base = re.sub(r"/\*+$", "", pattern)
            if file_path.startswith(base):
                return True
        return False

    # ------------------------------------------------------------------
    # Gate — the primary public API
    # ------------------------------------------------------------------

    def gate(
    self,
    action_description: str,
    user_prompt: str | None = None,
    persona: str | None = None,
    ) -> IdentityChallengeEvent | None:
        """GATE-stage identity check.

        Called by the orchestrator before dispatching any action.  Checks
        in order of severity:
        1. Constitution tamper check.
        2. Forbidden-persona request (if user_prompt provided).
        3. Persona-drift attack pattern (if user_prompt provided).
        4. Behavioral invariant violation.
        5. Drift threshold exceeded.

        The function is fail-safe: any unexpected exception returns a
        :class:`IdentityChallengeEvent` with reason ``"gate_error"`` rather
        than silently permitting the action (fail-closed per O-01).

        Args:
        action_description: What the agent wants to do.
        user_prompt: Optional raw user instruction to scan for attacks.
        persona: Optional active persona label.

        Returns:
        :class:`IdentityChallengeEvent` if gate fires (block or escalate),
        ``None`` if the action is clear to proceed.
        """
        try:
            # 1. Constitution tamper check (highest-priority — Ṛta layer)
            if not self.verify_constitution_hash():
                return IdentityChallengeEvent(
                        reason="constitution_tampering",
                drift_score=1.0,
                threshold=self._drift_threshold,
                matched_pattern="hash_mismatch",
                )

            # 2. Forbidden-persona request
            if user_prompt:
                forbidden_role = self.check_forbidden_persona_request(user_prompt)
                if forbidden_role:
                    return IdentityChallengeEvent(
                            reason="forbidden_persona",
                    drift_score=1.0,
                    threshold=self._drift_threshold,
                    matched_pattern=forbidden_role,
                    )

            # 3. Persona-drift attack pattern
            if user_prompt:
                attack_pattern = self.check_persona_drift_attack(user_prompt)
                if attack_pattern:
                    drift = self.compute_drift(persona)
                    return IdentityChallengeEvent(
                            reason="persona_drift_pattern",
                    drift_score=drift,
                    threshold=self._drift_threshold,
                    matched_pattern=attack_pattern,
                    )

            # 4. Behavioral invariant violation (action description check)
            invariant_id = self.check_invariant_violation(action_description)
            if invariant_id:
                drift = self.compute_drift(persona)
                return IdentityChallengeEvent(
                        reason="invariant_violation",
                drift_score=drift,
                threshold=self._drift_threshold,
                matched_pattern=invariant_id,
                )

            # 5. Drift threshold
            drift = self.compute_drift(persona)
            if drift > self._drift_threshold:
                return IdentityChallengeEvent(
                        reason="drift_threshold",
                drift_score=drift,
                threshold=self._drift_threshold,
                matched_pattern=None,
                )

            logger.debug(
                "ahamkara.gate_clear",
                    action=action_description[:60],
                    drift=round(drift, 4),
            )

        except Exception as exc:
            logger.exception("ahamkara.gate_error", error=str(exc))
            # Fail-closed: O-01 behavioral invariant
            return IdentityChallengeEvent(
                    reason="gate_error",
            drift_score=1.0,
            threshold=self._drift_threshold,
            matched_pattern=str(exc)[:120],
            )
        else:
            return None

    # ------------------------------------------------------------------
    # V2 bridge — close the IdentityDriftSignal stub
    # ------------------------------------------------------------------

    def get_drift_signal_for_consciousness(self) -> IdentityDriftSignal:
        """Close V2's stub: compute real IdentityDriftSignal from V4 data.

        Called by :meth:`~vedic.consciousness.CoreSelf.update_identity_drift_from_ahamkara`
        to wire V4 real drift values into V2's ānandamaya computation.

        Three drift dimensions:
        - **identity_drift** — cosine distance from core identity (no persona).
        - **mission_drift** — cosine distance when imagining a generic "mission"
        token vs core identity.  In V4 this is a proxy; Phase 4 computes
        from the mission-embedding subgraph directly.
        - **constitution_drift** — 0.0 if hash matches; 1.0 if tampered.

        Returns:
        :class:`~vedic.consciousness.IdentityDriftSignal` with real values.
        """
        from dharmaos.consciousness import IdentityDriftSignal  # avoid circular import

        identity_drift = self.compute_drift(persona=None)
        # Mission drift: compare core embedding to "mission" pseudo-persona
        mission_drift = self.compute_drift(persona="mission_drift_probe")
        # Constitution drift: binary — 0.0 if valid, 1.0 if tampered
        constitution_drift = 0.0 if self.verify_constitution_hash() else 1.0

        signal = IdentityDriftSignal(
        identity_drift=identity_drift,
        mission_drift=mission_drift,
        constitution_drift=constitution_drift,
        )
        logger.debug(
            "ahamkara.drift_signal",
        identity=round(identity_drift, 4),
        mission=round(mission_drift, 4),
        constitution=round(constitution_drift, 4),
        )
        return signal

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def constitution(self) -> Constitution:
        """The loaded constitution object."""
        return self._constitution

    @property
    def constitution_hash(self) -> str:
        """The registered SHA-256 hash of the constitution."""
        return self._constitution_hash

    @property
    def drift_threshold(self) -> float:
        """Configured cosine-drift threshold."""
        return self._drift_threshold
