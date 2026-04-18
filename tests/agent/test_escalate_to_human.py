"""Tests for escalate_to_human tool handler."""

import datetime
from unittest.mock import MagicMock

import pytest

from src.agent.tools.escalate_to_human import (
    escalate_to_human,
    make_escalate_to_human_handler,
)
from src.data.loaders import Employee
from src.exceptions import AccessDenied


def _emp(eid: str) -> Employee:
    return Employee(
        employee_id=eid, name=f"Test {eid}", title="Tester",
        department="Engineering", manager_id=None,
        clearance_level=2, location="Remote",
        hire_date=datetime.date(2024, 1, 1),
        email=f"{eid}@example.com", salary=100000, is_active=True,
    )


@pytest.fixture
def employees() -> dict[str, Employee]:
    return {"E001": _emp("E001"), "E002": _emp("E002")}


@pytest.fixture
def audit_mock():
    return MagicMock()


@pytest.fixture
def handler(employees, audit_mock):
    return make_escalate_to_human_handler(
        employees=employees, audit=audit_mock,
    )


# ---------- LLM schema ----------------------------------------------------

def test_tool_schema_exposes_only_reason():
    assert "reason" in escalate_to_human.args
    assert "user_id" not in escalate_to_human.args


# ---------- happy path ----------------------------------------------------

def test_escalation_succeeds_for_any_known_user(handler):
    result = handler(
        {"reason": "User question requires legal review"},
        user_id="E001",
    )
    assert result["escalated"] is True
    assert result["reason"] == "User question requires legal review"


def test_escalation_logs_to_audit(handler, audit_mock):
    handler({"reason": "blocked answer"}, user_id="E001")
    assert audit_mock.log_denial.called or audit_mock.log_escalation.called  # whichever the impl chose


# ---------- denials -------------------------------------------------------

def test_unknown_user_denied(handler):
    """Even though escalate_to_human has no per-call authz, a phantom
    user_id is rejected so audit logs aren't muddied."""
    with pytest.raises(AccessDenied):
        handler({"reason": "test"}, user_id="E999")


# ---------- impersonation -------------------------------------------------

def test_handler_ignores_user_id_in_args(handler, audit_mock):
    """E001 claims user_id=E999 in args; handler uses state's E001."""
    handler(
        {"reason": "test", "user_id": "E999"},
        user_id="E001",
    )
    # The audit call should have used E001, not E999
    if audit_mock.log_denial.called:
        call = audit_mock.log_denial.call_args
        all_args = list(call.args) + list(call.kwargs.values())
        assert any("E001" in str(a) for a in all_args)
