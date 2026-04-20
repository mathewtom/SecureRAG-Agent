"""Tests for list_calendar_events with busy-placeholder reduction."""

import datetime as dt

import pytest

from securerag_agent.agent.tools.list_calendar_events import (
    list_calendar_events,
    make_list_calendar_events_handler,
)
from securerag_agent.data.loaders import CalendarEvent, Employee
from securerag_agent.exceptions import AccessDenied


def _emp(eid: str) -> Employee:
    return Employee(
        employee_id=eid, name=f"Test {eid}", title="Tester",
        department="Engineering", manager_id=None,
        clearance_level=2, location="Remote",
        hire_date=dt.date(2024, 1, 1),
        email=f"{eid}@example.com", salary=100000, is_active=True,
    )


def _event(eid: str, *, organizer: str,
           attendees: tuple[str, ...],
           start: dt.datetime, end: dt.datetime,
           subject: str = "Sample meeting",
           classification: int = 2) -> CalendarEvent:
    return CalendarEvent(
        event_id=eid, organizer_id=organizer, attendees=attendees,
        subject=subject, classification=classification,
        start=start, end=end,
    )


def _utc(y: int, m: int, d: int, h: int = 12, mi: int = 0) -> dt.datetime:
    return dt.datetime(y, m, d, h, mi, tzinfo=dt.timezone.utc)


@pytest.fixture
def employees() -> dict[str, Employee]:
    return {f"E00{i}": _emp(f"E00{i}") for i in range(1, 5)}


@pytest.fixture
def events() -> list[CalendarEvent]:
    return [
        _event("C001", organizer="E001", attendees=("E002",),
               start=_utc(2026, 4, 5, 10), end=_utc(2026, 4, 5, 11),
               subject="Eng 1:1", classification=2),
        _event("C002", organizer="E001", attendees=("E001",),
               start=_utc(2026, 4, 10, 14), end=_utc(2026, 4, 10, 15),
               subject="Board prep", classification=4),
        _event("C003", organizer="E002", attendees=("E002", "E003"),
               start=_utc(2026, 4, 15, 9), end=_utc(2026, 4, 15, 10),
               subject="Sync"),
        _event("C004", organizer="E003", attendees=("E003",),
               start=_utc(2026, 5, 1, 9), end=_utc(2026, 5, 1, 10),
               subject="Out of range"),
    ]


@pytest.fixture
def handler(employees, events):
    return make_list_calendar_events_handler(
        employees=employees, events=events,
    )


# ---------- LLM schema ----------------------------------------------------

def test_tool_schema_exposes_only_date_range():
    assert "date_range" in list_calendar_events.args
    assert "user_id" not in list_calendar_events.args


# ---------- date-range parsing -------------------------------------------

def test_returns_only_events_in_range(handler):
    """C004 (May 1) is outside April range; C001-C003 are all returned
    (C003 as a busy placeholder for E001 who is not an attendee)."""
    result = handler(
        {"date_range": "2026-04-01..2026-04-30"}, user_id="E001",
    )
    ids = {e["event_id"] for e in result}
    assert ids == {"C001", "C002", "C003"}


def test_invalid_date_range_raises_value_error(handler):
    with pytest.raises(ValueError):
        handler({"date_range": "garbage"}, user_id="E001")


# ---------- busy-placeholder reduction ------------------------------------

def test_attendee_sees_full_event(handler):
    """E002 is attendee of C001; should see subject + organizer + attendees."""
    result = handler(
        {"date_range": "2026-04-01..2026-04-30"}, user_id="E002",
    )
    c001 = next(e for e in result if e["event_id"] == "C001")
    assert c001["subject"] == "Eng 1:1"
    assert c001["organizer_id"] == "E001"
    assert c001["attendees"] == ["E002"]


def test_organizer_sees_full_event(handler):
    """E001 organized C001; should see full record."""
    result = handler(
        {"date_range": "2026-04-01..2026-04-30"}, user_id="E001",
    )
    c001 = next(e for e in result if e["event_id"] == "C001")
    assert c001["subject"] == "Eng 1:1"


def test_non_attendee_sees_busy_placeholder(handler):
    """E003 is NOT attendee of C001 or C002; sees only timing."""
    result = handler(
        {"date_range": "2026-04-01..2026-04-30"}, user_id="E003",
    )
    # Only C003 is one E003 attended; C001 + C002 should be placeholders
    c001 = next(e for e in result if e["event_id"] == "C001")
    assert "subject" not in c001
    assert "organizer_id" not in c001
    assert "attendees" not in c001
    assert "start" in c001 and "end" in c001
    assert "classification" in c001


def test_restricted_event_subject_hidden_from_non_attendee(handler):
    """C002 is RESTRICTED (classification=4) and E003 is not attendee."""
    result = handler(
        {"date_range": "2026-04-01..2026-04-30"}, user_id="E003",
    )
    c002 = next(e for e in result if e["event_id"] == "C002")
    assert "subject" not in c002
    # Classification IS visible (so caller knows the event exists +
    # what tier it's at, but no content)
    assert c002["classification"] == 4


# ---------- denials -------------------------------------------------------

def test_unknown_user_id_denied(handler):
    with pytest.raises(AccessDenied):
        handler({"date_range": "2026-04-01..2026-04-30"}, user_id="E999")


# ---------- impersonation -------------------------------------------------

def test_handler_ignores_user_id_in_args(handler):
    """E003 (non-attendee of C001) tries to claim user_id=E002 (attendee)."""
    result = handler(
        {"date_range": "2026-04-01..2026-04-30",
         "user_id": "E002"},
        user_id="E003",
    )
    c001 = next(e for e in result if e["event_id"] == "C001")
    # Should still get the placeholder (E003 view), not E002's full view
    assert "subject" not in c001
