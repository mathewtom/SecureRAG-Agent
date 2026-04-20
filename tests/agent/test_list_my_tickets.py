"""Tests for list_my_tickets tool handler."""

import datetime

import pytest

from securerag_agent.agent.tools.list_my_tickets import (
    list_my_tickets,
    make_list_my_tickets_handler,
)
from securerag_agent.data.loaders import Employee, Ticket
from securerag_agent.exceptions import AccessDenied


def _emp(eid: str) -> Employee:
    return Employee(
        employee_id=eid, name=f"Test {eid}", title="Tester",
        department="Engineering", manager_id=None,
        clearance_level=2, location="Remote",
        hire_date=datetime.date(2024, 1, 1),
        email=f"{eid}@example.com", salary=100000, is_active=True,
    )


def _ticket(tid: str, *, owner: str, assignee: str,
            project: str | None = None) -> Ticket:
    return Ticket(
        ticket_id=tid, title=f"Ticket {tid}",
        owner_id=owner, assignee_id=assignee,
        status="open", classification=2,
        project_id=project,
        created_at=datetime.date(2026, 1, 1), type="it",
    )


@pytest.fixture
def employees() -> dict[str, Employee]:
    return {f"E00{i}": _emp(f"E00{i}") for i in range(1, 6)}


@pytest.fixture
def tickets() -> list[Ticket]:
    return [
        _ticket("T001", owner="E001", assignee="E002"),
        _ticket("T002", owner="E001", assignee="E003"),
        _ticket("T003", owner="E002", assignee="E002"),  # E002 owns + assigned
        _ticket("T004", owner="E003", assignee="E004"),
        _ticket("T005", owner="E005", assignee="E005"),
    ]


@pytest.fixture
def handler(employees, tickets):
    return make_list_my_tickets_handler(
        employees=employees, tickets=tickets,
    )


# ---------- LLM schema ----------------------------------------------------

def test_tool_schema_takes_no_args():
    """list_my_tickets has zero LLM-visible args."""
    assert list(list_my_tickets.args.keys()) == []
    # And user_id MUST not appear
    assert "user_id" not in list_my_tickets.args


# ---------- happy paths ---------------------------------------------------

def test_caller_sees_tickets_they_own(handler):
    result = handler({}, user_id="E001")
    ticket_ids = {t["ticket_id"] for t in result}
    assert ticket_ids == {"T001", "T002"}


def test_caller_sees_tickets_assigned_to_them(handler):
    result = handler({}, user_id="E004")
    ticket_ids = {t["ticket_id"] for t in result}
    assert ticket_ids == {"T004"}


def test_caller_sees_owner_AND_assignee_tickets_no_dup(handler):
    """E002 owns T003 AND is assigned T001 + T003. T003 should appear once."""
    result = handler({}, user_id="E002")
    ticket_ids = [t["ticket_id"] for t in result]
    assert sorted(ticket_ids) == ["T001", "T003"]
    assert len(ticket_ids) == len(set(ticket_ids))


def test_caller_with_no_tickets_gets_empty_list(handler, employees):
    employees["E099"] = _emp("E099")
    handler_with_e099 = make_list_my_tickets_handler(
        employees=employees, tickets=[
            _ticket("T001", owner="E001", assignee="E002"),
        ],
    )
    result = handler_with_e099({}, user_id="E099")
    assert result == []


def test_caller_never_sees_unrelated_tickets(handler):
    """E004 only sees T004; should never see T001-T003 or T005."""
    result = handler({}, user_id="E004")
    seen = {t["ticket_id"] for t in result}
    assert "T001" not in seen
    assert "T002" not in seen
    assert "T003" not in seen
    assert "T005" not in seen


# ---------- denials -------------------------------------------------------

def test_unknown_user_id_denied(handler):
    with pytest.raises(AccessDenied):
        handler({}, user_id="E999")


# ---------- impersonation resistance --------------------------------------

def test_handler_ignores_user_id_in_args(handler):
    """Even if LLM smuggles user_id in args, handler uses kwarg user_id."""
    # E004 (only sees T004) tries to claim user_id=E001 (would see T001+T002)
    result = handler({"user_id": "E001"}, user_id="E004")
    ticket_ids = {t["ticket_id"] for t in result}
    assert ticket_ids == {"T004"}
