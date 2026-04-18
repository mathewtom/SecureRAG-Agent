"""Tests for get_ticket_detail tool handler."""

import datetime

import pytest

from src.agent.tools.get_ticket_detail import (
    get_ticket_detail,
    make_get_ticket_detail_handler,
)
from src.data.loaders import Employee, Project, Ticket
from src.exceptions import AccessDenied


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


def _project(pid: str, *, owner: str,
             members: tuple[str, ...]) -> Project:
    return Project(
        project_id=pid, name=f"Project {pid}",
        owner_id=owner, members=members,
        classification="INTERNAL", status="active",
        start_date=datetime.date(2026, 1, 1),
        description="x",
    )


@pytest.fixture
def employees() -> dict[str, Employee]:
    return {f"E00{i}": _emp(f"E00{i}") for i in range(1, 7)}


@pytest.fixture
def tickets() -> dict[str, Ticket]:
    return {
        "T001": _ticket("T001", owner="E001", assignee="E002"),
        "T002": _ticket("T002", owner="E001", assignee="E002",
                        project="P001"),
        "T003": _ticket("T003", owner="E005", assignee="E005"),
    }


@pytest.fixture
def projects() -> dict[str, Project]:
    return {
        "P001": _project("P001", owner="E001",
                         members=("E002", "E003", "E004")),
    }


@pytest.fixture
def handler(employees, tickets, projects):
    return make_get_ticket_detail_handler(
        employees=employees, tickets=tickets, projects=projects,
    )


# ---------- LLM schema ----------------------------------------------------

def test_tool_schema_exposes_only_ticket_id():
    assert "ticket_id" in get_ticket_detail.args
    assert "user_id" not in get_ticket_detail.args


# ---------- happy paths ---------------------------------------------------

def test_owner_can_view_ticket(handler):
    result = handler({"ticket_id": "T001"}, user_id="E001")
    assert result["ticket_id"] == "T001"
    assert result["owner_id"] == "E001"


def test_assignee_can_view_ticket(handler):
    result = handler({"ticket_id": "T001"}, user_id="E002")
    assert result["ticket_id"] == "T001"


def test_project_member_can_view_project_scoped_ticket(handler):
    """T002 belongs to P001 with members E002, E003, E004. E003 is
    not owner / assignee but is a project member."""
    result = handler({"ticket_id": "T002"}, user_id="E003")
    assert result["ticket_id"] == "T002"
    assert result["project_id"] == "P001"


def test_project_owner_can_view_project_scoped_ticket(handler):
    """Project owner E001 (also ticket owner here) can view T002."""
    result = handler({"ticket_id": "T002"}, user_id="E001")
    assert result["ticket_id"] == "T002"


# ---------- denials -------------------------------------------------------

def test_unrelated_caller_denied(handler):
    """E006 is not owner, not assignee, not in project — denied."""
    with pytest.raises(AccessDenied):
        handler({"ticket_id": "T001"}, user_id="E006")


def test_non_member_denied_on_project_ticket(handler):
    """E005 is not owner / assignee of T002 nor a P001 member."""
    with pytest.raises(AccessDenied):
        handler({"ticket_id": "T002"}, user_id="E005")


def test_unknown_ticket_denied(handler):
    with pytest.raises(AccessDenied):
        handler({"ticket_id": "T999"}, user_id="E001")


def test_unknown_user_denied(handler):
    with pytest.raises(AccessDenied):
        handler({"ticket_id": "T001"}, user_id="E999")


def test_unknown_project_id_treated_as_no_project_scope(handler, employees, tickets):
    """If a ticket references a project_id not in the projects dict,
    fall back to owner/assignee-only authz (don't crash)."""
    tickets["T010"] = _ticket(
        "T010", owner="E001", assignee="E002", project="P999",
    )
    h = make_get_ticket_detail_handler(
        employees=employees, tickets=tickets, projects={},
    )
    # Owner can still view
    assert h({"ticket_id": "T010"}, user_id="E001") is not None
    # Outsider still denied
    with pytest.raises(AccessDenied):
        h({"ticket_id": "T010"}, user_id="E006")


# ---------- impersonation resistance --------------------------------------

def test_handler_ignores_user_id_in_args(handler):
    """E006 (denied) tries to claim user_id=E001 (would be allowed)."""
    with pytest.raises(AccessDenied):
        handler(
            {"ticket_id": "T001", "user_id": "E001"},
            user_id="E006",
        )
