"""Shared authorization primitives for agent tools.

These are pure functions: deterministic, no I/O, no global state.
Each is independently testable. Every Phase 3 tool's handler imports
the primitives it needs and composes them into the tool's specific
authorization rule.

Why pure functions, not a class:
- Every tool needs a different combination of these checks
- Sharing a class would couple tools to a single configuration shape
- Testing pure functions on small employee dicts is trivial
"""

from __future__ import annotations

from src.data.loaders import CalendarEvent, Employee, Project, Ticket

_CLASSIFICATION_ORDER = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


def manager_chain(
    employees: dict[str, Employee], employee_id: str,
) -> list[str]:
    """Return the chain `[employee_id, manager, manager.manager, ..., root]`.

    Raises KeyError if `employee_id` is not in `employees`. Stops at
    the root employee (manager_id is None) — the dataset's referential
    integrity tests guarantee no cycles.
    """
    chain: list[str] = []
    current: str | None = employee_id
    while current is not None:
        chain.append(current)
        emp = employees[current]
        current = emp.manager_id
    return chain


def is_in_manager_chain(
    employees: dict[str, Employee],
    requester_id: str,
    target_id: str,
) -> bool:
    """True iff `requester_id` is `target_id` or anywhere above
    `target_id` in the management hierarchy.

    Equivalent to "requester is the target or one of the target's
    direct or skip-level managers." Returns False if either ID is
    unknown.
    """
    if requester_id not in employees or target_id not in employees:
        return False
    return requester_id in manager_chain(employees, target_id)


def same_department(
    employees: dict[str, Employee],
    requester_id: str,
    target_id: str,
) -> bool:
    """True iff both employees exist and share a department."""
    req = employees.get(requester_id)
    tgt = employees.get(target_id)
    if req is None or tgt is None:
        return False
    return req.department == tgt.department


def has_department_clearance(
    employees: dict[str, Employee],
    requester_id: str,
    department: str,
) -> bool:
    """True iff `requester_id` belongs to the named department.

    Used for role-based clearance checks (e.g., HR can view all
    employees regardless of management chain; Finance can see all
    approval-related fields).
    """
    req = employees.get(requester_id)
    if req is None:
        return False
    return req.department == department


def classifications_up_to(clearance_level: int) -> list[str]:
    """Return the classification tiers a caller at `clearance_level`
    is permitted to see. Inclusive: level 2 sees PUBLIC + INTERNAL.

    Moved from `src/agent/retriever.py`; the retriever now re-exports
    from here.
    """
    if not 1 <= clearance_level <= 4:
        raise ValueError(
            f"clearance_level must be 1-4, got {clearance_level}"
        )
    return _CLASSIFICATION_ORDER[:clearance_level]


def restricted_to_allows(
    restricted_to: list[str] | None,
    user_id: str,
) -> bool:
    """True iff the artifact's `restricted_to` recipient list permits
    the user.

    A `None` or empty list means "not restricted" (anyone passing the
    classification check may view). A non-empty list means "only
    these recipients" — used by RESTRICTED-tier docs like Horizon
    briefing, executive comp analysis, board minutes.
    """
    if not restricted_to:
        return True
    return user_id in restricted_to


# ---------- ticket / project / calendar visibility primitives -------------


def is_ticket_principal(ticket: Ticket, user_id: str) -> bool:
    """True iff `user_id` is the ticket's owner or assignee.

    The tool surface uses this to gate per-ticket detail access:
    `get_ticket_detail` requires owner / assignee / project-member;
    `list_my_tickets` filters the visible set down to tickets where
    this returns True.
    """
    return user_id in (ticket.owner_id, ticket.assignee_id)


def is_project_member(project: Project, user_id: str) -> bool:
    """True iff `user_id` is the project owner or in the members
    list. Used by `get_ticket_detail` for project-scoped tickets."""
    return user_id == project.owner_id or user_id in project.members


def is_calendar_attendee(event: CalendarEvent, user_id: str) -> bool:
    """True iff `user_id` is the event organizer or in attendees.

    `list_calendar_events` uses this to decide whether to return the
    full event (subject + attendee list) vs. a busy placeholder
    (start, end, classification only) to non-attendees.
    """
    return user_id == event.organizer_id or user_id in event.attendees
