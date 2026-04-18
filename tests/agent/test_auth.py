"""Tests for shared authorization primitives."""

import datetime
import datetime as _dt  # alias used in ticket/project/calendar helpers

import pytest

from src.agent.tools.auth import (
    classifications_up_to,
    has_department_clearance,
    is_calendar_attendee,
    is_in_manager_chain,
    is_project_member,
    is_ticket_principal,
    manager_chain,
    restricted_to_allows,
    same_department,
)
from src.data.loaders import CalendarEvent, Employee, Project, Ticket


def _emp(eid: str, *, manager: str | None = None,
         dept: str = "Engineering", clearance: int = 2) -> Employee:
    return Employee(
        employee_id=eid,
        name=f"Test {eid}",
        title="Tester",
        department=dept,
        manager_id=manager,
        clearance_level=clearance,
        location="Remote",
        hire_date=datetime.date(2024, 1, 1),
        email=f"{eid}@example.com",
        salary=100000,
        is_active=True,
    )


def _three_level_org() -> dict[str, Employee]:
    """E001 (CEO) <- E002 (manager) <- E003 (IC)."""
    return {
        "E001": _emp("E001", manager=None, dept="Executive"),
        "E002": _emp("E002", manager="E001", dept="Engineering"),
        "E003": _emp("E003", manager="E002", dept="Engineering"),
    }


# ---------- manager_chain --------------------------------------------------

def test_manager_chain_includes_self_and_traverses_to_root():
    employees = _three_level_org()
    assert manager_chain(employees, "E003") == ["E003", "E002", "E001"]


def test_manager_chain_for_root_returns_only_self():
    employees = _three_level_org()
    assert manager_chain(employees, "E001") == ["E001"]


def test_manager_chain_unknown_employee_raises():
    employees = _three_level_org()
    with pytest.raises(KeyError):
        manager_chain(employees, "E999")


# ---------- is_in_manager_chain -------------------------------------------

def test_is_in_manager_chain_self_is_true():
    employees = _three_level_org()
    assert is_in_manager_chain(employees, "E003", "E003") is True


def test_is_in_manager_chain_direct_manager():
    employees = _three_level_org()
    assert is_in_manager_chain(employees, "E002", "E003") is True


def test_is_in_manager_chain_skip_level():
    employees = _three_level_org()
    assert is_in_manager_chain(employees, "E001", "E003") is True


def test_is_in_manager_chain_lower_in_chain_is_false():
    employees = _three_level_org()
    # E003 is below E002, NOT above
    assert is_in_manager_chain(employees, "E003", "E002") is False


def test_is_in_manager_chain_unrelated_branches():
    employees = _three_level_org()
    employees["E004"] = _emp("E004", manager="E001", dept="Sales")
    # E002 (Engineering) is NOT in E004 (Sales)'s manager chain
    assert is_in_manager_chain(employees, "E002", "E004") is False
    assert is_in_manager_chain(employees, "E004", "E002") is False


# ---------- same_department ------------------------------------------------

def test_same_department_true_when_match():
    employees = _three_level_org()
    assert same_department(employees, "E002", "E003") is True


def test_same_department_false_across_depts():
    employees = _three_level_org()
    employees["E004"] = _emp("E004", manager="E001", dept="Sales")
    assert same_department(employees, "E003", "E004") is False


# ---------- has_department_clearance --------------------------------------

def test_has_department_clearance_match():
    employees = _three_level_org()
    employees["E005"] = _emp("E005", manager="E001",
                              dept="Human Resources")
    assert has_department_clearance(employees, "E005",
                                     "Human Resources") is True


def test_has_department_clearance_mismatch():
    employees = _three_level_org()
    assert has_department_clearance(employees, "E003",
                                     "Human Resources") is False


# ---------- classifications_up_to (moved from retriever) ------------------

def test_classifications_up_to_level_3():
    assert set(classifications_up_to(3)) == {
        "PUBLIC", "INTERNAL", "CONFIDENTIAL",
    }


def test_classifications_up_to_invalid_raises():
    with pytest.raises(ValueError):
        classifications_up_to(0)
    with pytest.raises(ValueError):
        classifications_up_to(5)


# ---------- restricted_to_allows ------------------------------------------

def test_restricted_to_allows_when_recipient_listed():
    assert restricted_to_allows(["E001", "E007"], "E001") is True


def test_restricted_to_allows_when_recipient_not_listed():
    assert restricted_to_allows(["E001", "E007"], "E003") is False


def test_restricted_to_allows_when_no_restriction():
    assert restricted_to_allows(None, "E003") is True
    assert restricted_to_allows([], "E003") is True


# ---------- ticket / project / calendar primitives ------------------------


def _ticket(tid: str = "T001", *, owner: str = "E003",
            assignee: str = "E004") -> Ticket:
    return Ticket(
        ticket_id=tid,
        title="Sample",
        owner_id=owner,
        assignee_id=assignee,
        status="open",
        classification=2,
        project_id=None,
        created_at=_dt.date(2026, 1, 1),
        type="it",
    )


def _project(pid: str = "P001", *, owner: str = "E001",
             members: tuple[str, ...] = ("E002", "E003")) -> Project:
    return Project(
        project_id=pid,
        name="Sample",
        owner_id=owner,
        members=members,
        classification="INTERNAL",
        status="active",
        start_date=_dt.date(2026, 1, 1),
        description="x",
    )


def _event(eid: str = "C001", *, organizer: str = "E001",
           attendees: tuple[str, ...] = ("E002", "E003")) -> CalendarEvent:
    return CalendarEvent(
        event_id=eid,
        organizer_id=organizer,
        attendees=attendees,
        subject="Sample",
        classification=2,
        start=_dt.datetime(2026, 1, 1, 10, 0,
                           tzinfo=_dt.timezone.utc),
        end=_dt.datetime(2026, 1, 1, 11, 0,
                         tzinfo=_dt.timezone.utc),
    )


def test_is_ticket_principal_owner():
    assert is_ticket_principal(_ticket(owner="E003"), "E003") is True


def test_is_ticket_principal_assignee():
    assert is_ticket_principal(_ticket(assignee="E004"), "E004") is True


def test_is_ticket_principal_unrelated_user():
    t = _ticket(owner="E003", assignee="E004")
    assert is_ticket_principal(t, "E999") is False


def test_is_project_member_owner():
    assert is_project_member(_project(owner="E001"), "E001") is True


def test_is_project_member_in_members_list():
    p = _project(owner="E001", members=("E002", "E003"))
    assert is_project_member(p, "E003") is True


def test_is_project_member_not_in_project():
    p = _project(owner="E001", members=("E002",))
    assert is_project_member(p, "E999") is False


def test_is_calendar_attendee_organizer():
    assert is_calendar_attendee(_event(organizer="E001"), "E001") is True


def test_is_calendar_attendee_in_attendee_list():
    e = _event(organizer="E001", attendees=("E002", "E003"))
    assert is_calendar_attendee(e, "E003") is True


def test_is_calendar_attendee_outsider():
    e = _event(organizer="E001", attendees=("E002",))
    assert is_calendar_attendee(e, "E999") is False
