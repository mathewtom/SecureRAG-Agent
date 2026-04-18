"""list_calendar_events tool: return events in a date range, with
busy-placeholder reduction for events the caller is not part of.

LLM-visible schema: {date_range: str} — "YYYY-MM-DD..YYYY-MM-DD".

For events where the caller is organizer or attendee, return the
full record. For other events in the range, return only timing +
classification — never subject, organizer, or attendee list. This
is the "busy placeholder" pattern that lets calendars stay
plannable without leaking content.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from langchain_core.tools import tool

from src.agent.tools.auth import is_calendar_attendee
from src.agent.tools.registry import ToolHandler
from src.data.loaders import CalendarEvent, Employee
from src.exceptions import AccessDenied


@tool
def list_calendar_events(date_range: str) -> str:
    """List calendar events in a date range. You see full details for
    events you organize or attend; for others, you see timing only
    (busy placeholder).

    Args:
        date_range: range as "YYYY-MM-DD..YYYY-MM-DD" (inclusive on
            both endpoints, UTC).
    """
    raise NotImplementedError(
        "list_calendar_events must be invoked via "
        "AuthenticatedToolNode; direct calls bypass the runtime "
        "user_id injection."
    )


def make_list_calendar_events_handler(
    *,
    employees: dict[str, Employee],
    events: list[CalendarEvent],
) -> ToolHandler:
    """Bind directory + event corpus into a handler."""

    def handler(args: dict[str, Any], *, user_id: str) -> list[dict[str, Any]]:
        if user_id not in employees:
            raise AccessDenied(f"unknown caller user_id={user_id!r}")

        start, end = _parse_range(args["date_range"])

        in_range = [
            e for e in events
            if start <= e.start <= end
        ]

        return [_reduce(e, user_id) for e in in_range]

    return handler


def _parse_range(value: str) -> tuple[dt.datetime, dt.datetime]:
    """Parse "YYYY-MM-DD..YYYY-MM-DD" to (start_of_day_utc,
    end_of_day_utc)."""
    if ".." not in value:
        raise ValueError(
            f"date_range must be 'YYYY-MM-DD..YYYY-MM-DD', got {value!r}"
        )
    start_str, end_str = value.split("..", 1)
    try:
        start_date = dt.date.fromisoformat(start_str)
        end_date = dt.date.fromisoformat(end_str)
    except ValueError as exc:
        raise ValueError(
            f"invalid date in range {value!r}: {exc}"
        ) from exc
    start = dt.datetime.combine(
        start_date, dt.time.min, tzinfo=dt.timezone.utc,
    )
    end = dt.datetime.combine(
        end_date, dt.time.max, tzinfo=dt.timezone.utc,
    )
    return start, end


def _reduce(event: CalendarEvent, user_id: str) -> dict[str, Any]:
    """Full record for attendees; busy placeholder for everyone else."""
    base: dict[str, Any] = {
        "event_id": event.event_id,
        "classification": event.classification,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
    }
    if not is_calendar_attendee(event, user_id):
        return base
    base.update({
        "subject": event.subject,
        "organizer_id": event.organizer_id,
        "attendees": list(event.attendees),
    })
    return base
