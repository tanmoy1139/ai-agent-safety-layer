"""
DharmaOS Constitution — Pydantic-validated agent constitution schema and
SHA-256 tamper-evident hash computation.

PURPOSE
-------
The constitution YAML is the agent's declared self: its identity, values,
constraints, capabilities, and principal (who it serves). The constitution
module loads, validates, and cryptographically seals this declaration so that
any unauthorized modification is immediately detectable.

The SHA-256 hash of the raw YAML bytes is computed at load time and registered
in AhamkaraModule._constitution_hash. Every gate_drift_check() call re-computes
the hash from disk and compares against the registered value — a mismatch
triggers a CRITICAL tamper alert.

CONSTITUTION YAML SCHEMA (key blocks)
--------------------------------------
identity:
name, kind, description, metaphysical_disclaimer.
principal:
primary (who the agent serves), contact.
values:
list of named values with priority and description.
constraints:
absolute_prohibitions (never-do list), conditional_constraints (if-then rules).
capabilities:
list of declared capabilities with scope and risk level.
governance:
policy_version, constitution_version, review_schedule.

KEY FUNCTIONS
-------------
load_constitution(path) → Constitution (Pydantic model).
compute_constitution_hash(path) → str (SHA-256 hex digest).
Constitution  : Top-level Pydantic model.
Identity, Principal, Value, etc.: Nested schema models.

COMPLIANCE ROLE
---------------
- Constitution hash provides tamper-evident policy integrity (ISO/IEC 42001 A.5.1).
- Pydantic validation ensures all constitution fields conform to declared types
before the hash is computed (prevents partial-load attacks).
- AhamkaraModule uses compute_constitution_hash() to close the RF-V4-02 identity
hash register.

Governance origin: Vedānta Sāra §61–65 — ahaṃkāra wires the declared
constitution into the identity-preservation register; the hash is the
tamper-evident seal on the agent's declared values and constraints.

agent safety layer — Sprint V4 Deliverable 1.

Constitution loader + Pydantic schema + SHA-256 hash computation.

The authoritative YAML constitution at ``docs/vedic/constitution-v1-DRAFT.yaml``
is loaded, validated against this schema, and hashed.  The hash is the
``_constitution_hash`` register maintained by the V4 :class:`AhamkaraModule`.

**Philosophical grounding:**
Source: Vedānta Sāra §61–65 (antaḥkaraṇa — manas/buddhi/citta/ahaṃkāra).
Ahaṃkāra (the "I-maker") preserves the agent's identity against drift.  The
constitution is the DECLARED self: every gate check asks "would the declared
self take this action?" The hash is the tamper-evident seal on that declaration.

**HITL Gate 3 status:** DRAFT.  Constitution is at
``docs/vedic/constitution-v1-DRAFT.yaml``.  V4 proceeds with this draft (see
return report).  After Tanmoy signs, path should be renamed to
``constitution-v1.yaml`` and hash re-committed.  (RF-V4-02)

**Research flags:**
RF-V4-01 — SimpleIdentityKG uses hash-based embeddings (deterministic, not
semantic).  Phase 4 replaces with BGE-M3 or OpenAI text-embedding-3.
RF-V4-02 — Constitution path hardcoded to DRAFT.  After Gate 3, update path.

SOTA reference:
ID-RAG identity-coherence (arXiv:2509.25299, ECAI 2025).
Identity Drift adversarial evaluation (arXiv:2412.00804).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Nested Pydantic schemas — mirror the YAML structure exactly
# ---------------------------------------------------------------------------


class Identity(BaseModel):
    """Core identity declaration — the invariant self that persona-drift
    attacks attempt to subvert.  §identity block in constitution YAML."""

    name: str
    kind: str
    description: str
    metaphysical_disclaimer: str


class Principal(BaseModel):
    """Who the agent serves.  §principal block."""

    primary: str
    contact: str
    authority_scope: str
    delegation_policy: str


class Mission(BaseModel):
    """What the agent exists to do.  §mission block."""

    primary: str
    secondary: str
    out_of_scope: str


class Persona(BaseModel):
    """A permitted or forbidden persona entry.  Used in both lists."""

    role: str
    description: str
    example_trigger: str | None = None


class PriorityHierarchy(BaseModel):
    """Priority hierarchy when instructions conflict.  §priority_hierarchy."""

    model_config = {"populate_by_name": True}

    rta: str = Field(alias="1_rta")
    dharma: str = Field(alias="2_dharma")
    policy_compliance: str = Field(alias="3_policy_compliance")
    helpfulness: str = Field(alias="4_helpfulness")


class BehavioralInvariant(BaseModel):
    """Single behavioral invariant with machine-readable ID and statement."""

    id: str
    statement: str
    verifiable_by: str | None = None


class BehavioralInvariants(BaseModel):
    """All behavioral invariants grouped by category.
    §behavioral_invariants block.  Each key is a category name; value is a
    list of :class:`BehavioralInvariant` objects."""

    metaphysical: list[BehavioralInvariant]
    identity: list[BehavioralInvariant]
    ethics: list[BehavioralInvariant]
    disclosure: list[BehavioralInvariant]
    patent_pre_filing: list[BehavioralInvariant]
    operational: list[BehavioralInvariant]
    safety: list[BehavioralInvariant]

    def all_invariants(self) -> list[BehavioralInvariant]:
        """Flatten all categories into a single list for gate iteration."""
        return (
            self.metaphysical
            + self.identity
            + self.ethics
            + self.disclosure
            + self.patent_pre_filing
            + self.operational
            + self.safety
        )

    def get_by_id(self, invariant_id: str) -> BehavioralInvariant | None:
        """Retrieve a specific invariant by its ID (e.g., ``'I-04'``)."""
        for inv in self.all_invariants():
            if inv.id == invariant_id:
                return inv
        return None


class RepositoryScope(BaseModel):
    """What files the agent may read and modify.  §repository_scope."""

    read_authorized: list[str]
    write_authorized: list[str]
    write_forbidden_without_hitl: list[str]
    write_forbidden_absolutely: list[str]


class ExternalSystemEntry(BaseModel):
    """Single external-system entry."""

    system: str
    scope: str


class ExternalSystems(BaseModel):
    """External system authorization.  §external_systems."""

    authorized: list[ExternalSystemEntry]
    forbidden: list[str]


class ChangePolicy(BaseModel):
    """Constitution change policy.  §change_policy."""

    modification_requires: list[str]
    unauthorized_modification_is: list[str]


class HITLSignature(BaseModel):
    """HITL Gate 3 signature block.  §hitl_signature.
    Null until Tanmoy signs the constitution."""

    signed_by: str | None = None
    signed_at: str | None = None
    signature_method: str | None = None
    witness_commits: list[str] = Field(default_factory=list)

    @property
    def is_signed(self) -> bool:
        """True when a human signature is present."""
        return self.signed_by is not None and self.signed_at is not None


class References(BaseModel):
    """Source citations.  §references."""

    primary_sources: list[str]
    patent: list[str]
    spec_authority: list[str]
    ai_governance: list[str]


# ---------------------------------------------------------------------------
# Root Constitution model
# ---------------------------------------------------------------------------


class Constitution(BaseModel):
    """Full machine-readable constitution.

    Loaded from ``docs/vedic/constitution-v1-DRAFT.yaml`` and validated
    by :func:`load_constitution`.  The SHA-256 hash of the raw YAML bytes is
    computed by :func:`compute_constitution_hash` and stored in the V4
    :class:`~vedic.ahamkara.AhamkaraModule` register.

    P1-6: model_config.frozen=True prevents runtime mutation that would
    create a TOCTOU vulnerability between hash computation and constitutional
    veto check. integrity_hash is recomputed on every access — no caching.

    Attribute names shadow the YAML keys (snake_case for Python).
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str
    constitution_version: str
    last_hitl_signature: str | None = None
    hash_algorithm: Literal["sha256"]

    identity: Identity
    principal: Principal
    mission: Mission
    permitted_personae: list[Persona]
    forbidden_personae: list[Persona]
    priority_hierarchy: PriorityHierarchy
    behavioral_invariants: BehavioralInvariants
    repository_scope: RepositoryScope
    external_systems: ExternalSystems
    change_policy: ChangePolicy
    hitl_signature: HITLSignature
    references: References

    def is_hitl_signed(self) -> bool:
        """True when Gate 3 signature is present."""
        return self.hitl_signature.is_signed

    def get_forbidden_persona_roles(self) -> list[str]:
        """Return just the role names from forbidden_personae (for quick set lookup)."""
        return [p.role for p in self.forbidden_personae]

    def get_write_forbidden_absolutely(self) -> list[str]:
        """Return paths that are absolutely forbidden for writes."""
        return self.repository_scope.write_forbidden_absolutely

    @property
    def integrity_hash(self) -> str:
        """Recompute the SHA-256 integrity hash on every call (P1-6).

        Never caches — eliminates TOCTOU vulnerability where a mutated
        constitution could pass a stale hash check. Compares the current
        serialized state, not an initialization-time snapshot.
        """
        return hashlib.sha256(
            json.dumps(self.model_dump(), sort_keys=True).encode()
        ).hexdigest()


# ---------------------------------------------------------------------------
# Loader + hash functions
# ---------------------------------------------------------------------------


def load_constitution(path: str) -> tuple[Constitution, bytes]:
    """Parse and validate the YAML constitution.

    Reads the raw bytes from *path* (preserving them for deterministic hash
    computation), parses YAML, and validates against the :class:`Constitution`
    Pydantic model.

    Args:
    path: Filesystem path to the YAML constitution file.

    Returns:
    A ``(Constitution, raw_bytes)`` tuple.  ``raw_bytes`` is the
    unmodified file content used by :func:`compute_constitution_hash`.

    Raises:
    FileNotFoundError: If the constitution file does not exist.
    yaml.YAMLError: If the file is not valid YAML.
    pydantic.ValidationError: If the YAML structure does not conform to
    the :class:`Constitution` schema.
    """
    raw_bytes = Path(path).read_bytes()
    data = yaml.safe_load(raw_bytes)
    constitution = Constitution.model_validate(data)
    return constitution, raw_bytes


def compute_constitution_hash(raw_bytes: bytes) -> str:
    """Authoritative SHA-256 over the raw YAML file bytes.

    Deterministic: same file bytes → same hex digest, always.  Any mutation
    of the file — even a single whitespace change — produces a different hash,
    which is detected by :meth:`~vedic.ahamkara.AhamkaraModule.verify_constitution_hash`.

    Args:
    raw_bytes: Raw bytes as returned by :func:`load_constitution`.

    Returns:
    Lower-case hex string of the SHA-256 digest (64 characters).
    """
    return hashlib.sha256(raw_bytes).hexdigest()
