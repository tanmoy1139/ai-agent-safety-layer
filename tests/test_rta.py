"""Tests for vedic.rta — Ṛta Layer named invariants substrate.

Sprint V9. TDD: these tests were written BEFORE the implementation.

Source: Ṛg Veda 1.164.46 (Ṛta as cosmic order).
Framework: ISO/IEC 42001 Annex A; NIST AI RMF.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from dharmaos.rta import (
InvariantResult,
InvariantStatus,
RtaAuditReport,
RtaSupervisor,
SystemMode,
check_audit_log_immutability,
check_constitution_hash,
check_fail_closed_defaults,
check_rls_isolation,
check_schema_conformance,
check_type_safety,
default_rta_supervisor,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

CONSTITUTION_PATH = "docs/vedic/constitution-v1-DRAFT.yaml"
KNOWN_HASH = "e25cb1b1a5cef8c689f1029cc79ecb6aae5f3b226fcf619d248cc57713f563a8"


def _passing_checker() -> InvariantResult:
    return InvariantResult(
            name="test_pass",
            status=InvariantStatus.PASS,
    message="ok",
    )


def _critical_failing_checker() -> InvariantResult:
    return InvariantResult(
            name="test_critical_fail",
            status=InvariantStatus.FAIL,
    message="critical broke",
    severity="critical",
    )


def _high_failing_checker() -> InvariantResult:
    return InvariantResult(
            name="test_high_fail",
            status=InvariantStatus.FAIL,
    message="high broke",
    severity="high",
    )


def _low_failing_checker() -> InvariantResult:
    return InvariantResult(
            name="test_low_fail",
            status=InvariantStatus.FAIL,
    message="low issue",
    severity="low",
    )


# ---------------------------------------------------------------------------
# 1. check_type_safety passes on well-annotated modules
# ---------------------------------------------------------------------------


def test_check_type_safety_pass() -> None:
    """check_type_safety should PASS when all annotated dataclasses are clean."""
    result = check_type_safety()
    assert result.status == InvariantStatus.PASS
    assert result.name == "type_safety"


# ---------------------------------------------------------------------------
# 2. check_schema_conformance — valid sample → PASS
# ---------------------------------------------------------------------------


class _SampleModel(BaseModel):
    name: str
    value: int


def test_check_schema_conformance_valid() -> None:
    """check_schema_conformance with valid data should return PASS."""
    samples: list[tuple[Any, dict[str, Any]]] = [
        (_SampleModel, {"name": "rta", "value": 9}),
    ]
    result = check_schema_conformance(samples)
    assert result.status == InvariantStatus.PASS
    assert result.name == "schema_conformance"


# ---------------------------------------------------------------------------
# 3. check_schema_conformance — invalid raw data → FAIL
# ---------------------------------------------------------------------------


def test_check_schema_conformance_invalid() -> None:
    """check_schema_conformance with type-violating data should return FAIL."""
    samples: list[tuple[Any, dict[str, Any]]] = [
        (_SampleModel, {"name": "rta", "value": "not-an-int"}),
    ]
    result = check_schema_conformance(samples)
    assert result.status == InvariantStatus.FAIL
    assert (
        "validation" in result.message.lower()
        or "schema" in result.message.lower()
        or "fail" in result.message.lower()
    )


# ---------------------------------------------------------------------------
# 4. check_rls_isolation on fresh SamskaraLedger → PASS
# ---------------------------------------------------------------------------


def test_check_rls_isolation_pass() -> None:
    """check_rls_isolation with default (SamskaraLedger) probe should PASS."""
    result = check_rls_isolation()
    assert result.status == InvariantStatus.PASS
    assert result.name == "rls_isolation"


# ---------------------------------------------------------------------------
# 5. check_fail_closed_defaults passes under current code
# ---------------------------------------------------------------------------


def test_check_fail_closed_defaults_pass() -> None:
    """Fail-closed invariant should pass — EthicsEngine defaults to DENY."""
    result = check_fail_closed_defaults()
    assert result.status == InvariantStatus.PASS
    assert result.name == "fail_closed_defaults"


# ---------------------------------------------------------------------------
# 6. check_audit_log_immutability → append-twice raises → invariant holds
# ---------------------------------------------------------------------------


def test_check_audit_log_immutability_pass() -> None:
    """Audit log immutability invariant should PASS (double-append raises)."""
    result = check_audit_log_immutability()
    assert result.status == InvariantStatus.PASS
    assert result.name == "audit_log_immutability"


# ---------------------------------------------------------------------------
# 7. check_constitution_hash — correct hash → PASS
# ---------------------------------------------------------------------------


def test_check_constitution_hash_pass() -> None:
    """check_constitution_hash with correct expected hash should PASS."""
    result = check_constitution_hash(expected_hash=KNOWN_HASH)
    assert result.status == InvariantStatus.PASS
    assert result.name == "constitution_hash"
    assert KNOWN_HASH in result.message or KNOWN_HASH in str(result.evidence)


# ---------------------------------------------------------------------------
# 8. check_constitution_hash — wrong hash → FAIL
# ---------------------------------------------------------------------------


def test_check_constitution_hash_fail() -> None:
    """check_constitution_hash with wrong expected hash should FAIL."""
    wrong_hash = "deadbeef" * 8  # 64 chars, wrong value
    result = check_constitution_hash(expected_hash=wrong_hash)
    assert result.status == InvariantStatus.FAIL
    assert result.name == "constitution_hash"


# ---------------------------------------------------------------------------
# 9. RtaSupervisor.audit() aggregates results + sets overall_status
# ---------------------------------------------------------------------------


def test_supervisor_audit_aggregates() -> None:
    """audit() should collect all checker results and set overall_status."""
    supervisor = RtaSupervisor(
    checkers={
    "pass1": _passing_checker,
    "pass2": _passing_checker,
    }
    )
    report = supervisor.audit()
    assert isinstance(report, RtaAuditReport)
    assert len(report.results) == 2
    assert report.overall_status == InvariantStatus.PASS
    assert report.mode == SystemMode.NORMAL
    assert isinstance(report.audit_ts, datetime)


# ---------------------------------------------------------------------------
# 10. Supervisor with one CRITICAL-severity failure → SystemMode.HALTED
# ---------------------------------------------------------------------------


def test_supervisor_critical_failure_halted() -> None:
    """A CRITICAL severity invariant failure must produce SystemMode.HALTED."""
    supervisor = RtaSupervisor(
    checkers={
    "good": _passing_checker,
    "critical_bad": _critical_failing_checker,
    }
    )
    report = supervisor.audit()
    assert report.mode == SystemMode.HALTED
    assert report.overall_status == InvariantStatus.FAIL
    # current_mode property must reflect last audit
    assert supervisor.current_mode == SystemMode.HALTED


# ---------------------------------------------------------------------------
# 11. Supervisor with HIGH failure → SystemMode.DEGRADED + hitl_notify called
# ---------------------------------------------------------------------------


def test_supervisor_high_failure_degraded_and_notifies() -> None:
    """HIGH severity failure → DEGRADED mode; hitl_notify callback invoked."""
    notified: list[RtaAuditReport] = []

    def _notify(report: RtaAuditReport) -> None:
        notified.append(report)

    supervisor = RtaSupervisor(
    checkers={
    "good": _passing_checker,
    "high_bad": _high_failing_checker,
    },
    hitl_notify=_notify,
    )
    report = supervisor.audit()
    assert report.mode == SystemMode.DEGRADED
    assert len(notified) == 1
    assert notified[0] is report
    assert supervisor.current_mode == SystemMode.DEGRADED


# ---------------------------------------------------------------------------
# 12. register() replaces an existing checker by name
# ---------------------------------------------------------------------------


def test_supervisor_register_replaces_checker() -> None:
    """register() with an existing name must silently replace the old checker."""
    supervisor = RtaSupervisor(
    checkers={
            "probe": _high_failing_checker,
    }
    )
    # First audit — should be DEGRADED (high failure)
    report1 = supervisor.audit()
    assert report1.mode == SystemMode.DEGRADED

    # Replace the checker with a passing one
    supervisor.register("probe", _passing_checker)

    # Second audit — should be NORMAL now
    report2 = supervisor.audit()
    assert report2.mode == SystemMode.NORMAL
    assert report2.overall_status == InvariantStatus.PASS


# ---------------------------------------------------------------------------
# Extra robustness tests (4 additional beyond spec minimum of 8)
# ---------------------------------------------------------------------------


def test_supervisor_last_report_none_before_audit() -> None:
    """last_report should be None before the first audit()."""
    supervisor = RtaSupervisor(checkers={})
    assert supervisor.last_report is None


def test_supervisor_last_report_populated_after_audit() -> None:
    """last_report should be set to the most recent RtaAuditReport after audit()."""
    supervisor = RtaSupervisor(checkers={"p": _passing_checker})
    assert supervisor.last_report is None
    report = supervisor.audit()
    assert supervisor.last_report is report


def test_supervisor_low_failure_stays_normal() -> None:
    """A LOW severity failure should NOT degrade the system mode."""
    supervisor = RtaSupervisor(
    checkers={
            "low_issue": _low_failing_checker,
    }
    )
    report = supervisor.audit()
    # LOW severity failures are logged but do not change mode
    assert report.mode == SystemMode.NORMAL


def test_check_constitution_hash_observability_mode() -> None:
    """check_constitution_hash(None) should return PASS (observability mode)."""
    result = check_constitution_hash(expected_hash=None)
    # In observability mode: no expected hash → report current hash, still PASS
    assert result.status == InvariantStatus.PASS
    assert result.name == "constitution_hash"
    # Evidence should include the actual hash
    assert "hash" in result.evidence


def test_default_rta_supervisor_returns_supervisor() -> None:
    """default_rta_supervisor() factory should return a wired RtaSupervisor."""
    supervisor = default_rta_supervisor()
    assert isinstance(supervisor, RtaSupervisor)
    report = supervisor.audit()
    # All six standard invariants should appear in the results
    names = {r.name for r in report.results}
    assert "type_safety" in names
    assert "schema_conformance" in names
    assert "rls_isolation" in names
    assert "fail_closed_defaults" in names
    assert "audit_log_immutability" in names
    assert "constitution_hash" in names


def test_supervisor_periodic_start_stop_idempotent() -> None:
    """start_periodic() is idempotent; stop_periodic() does not raise."""
    supervisor = RtaSupervisor(
    checkers={"p": _passing_checker},
    audit_interval_seconds=600.0,  # long enough not to fire during test
    )
    supervisor.start_periodic()
    supervisor.start_periodic()  # second call must be a no-op
    supervisor.stop_periodic()
    supervisor.stop_periodic()  # second stop also must be a no-op
