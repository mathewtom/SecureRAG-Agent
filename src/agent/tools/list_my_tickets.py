"""list_my_tickets tool: return tickets owned by or assigned to the
caller. Always returns only the caller's tickets; never any others.

LLM-visible schema: no args. The `user_id` is injected from state by
AuthenticatedToolNode.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from src.agent.tools.auth import is_ticket_principal
from src.agent.tools.registry import ToolHandler
from src.data.loaders import Employee, Ticket
from src.exceptions import AccessDenied


@tool
def list_my_tickets() -> str:
    """List the tickets where you are the owner or assignee."""
    raise NotImplementedError(
        "list_my_tickets must be invoked via AuthenticatedToolNode; "
        "direct calls bypass the runtime user_id injection."
    )


def make_list_my_tickets_handler(
    *,
    employees: dict[str, Employee],
    tickets: list[Ticket],
) -> ToolHandler:
    """Bind the employees directory + ticket corpus into a handler."""

    def handler(args: dict[str, Any], *, user_id: str) -> list[dict[str, Any]]:
        if user_id not in employees:
            raise AccessDenied(f"unknown caller user_id={user_id!r}")

        return [
            _ticket_to_dict(t)
            for t in tickets
            if is_ticket_principal(t, user_id)
        ]

    return handler


def _ticket_to_dict(t: Ticket) -> dict[str, Any]:
    return {
        "ticket_id": t.ticket_id,
        "title": t.title,
        "owner_id": t.owner_id,
        "assignee_id": t.assignee_id,
        "status": t.status,
        "classification": t.classification,
        "project_id": t.project_id,
        "created_at": t.created_at.isoformat(),
        "type": t.type,
    }
