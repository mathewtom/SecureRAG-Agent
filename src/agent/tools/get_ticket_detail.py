"""get_ticket_detail tool: return a single ticket if the caller is
authorized.

Authorization:
  - caller is owner or assignee → ALLOW
  - caller is in the ticket's project (owner or member) → ALLOW
  - otherwise → AccessDenied

Unknown ticket_id is treated as AccessDenied (not 404) to avoid
exposing which IDs exist via differential errors.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from src.agent.tools.auth import is_project_member, is_ticket_principal
from src.agent.tools.registry import ToolHandler
from src.data.loaders import Employee, Project, Ticket
from src.exceptions import AccessDenied


@tool
def get_ticket_detail(ticket_id: str) -> str:
    """Return the detail for a ticket by ID. Authorized only for the
    ticket's owner, assignee, or members of the ticket's project.

    Args:
        ticket_id: the ticket ID (e.g., "T001") to look up.
    """
    raise NotImplementedError(
        "get_ticket_detail must be invoked via AuthenticatedToolNode; "
        "direct calls bypass the runtime user_id injection."
    )


def make_get_ticket_detail_handler(
    *,
    employees: dict[str, Employee],
    tickets: dict[str, Ticket],
    projects: dict[str, Project],
) -> ToolHandler:
    """Bind the directory + ticket + project lookups into a handler."""

    def handler(args: dict[str, Any], *, user_id: str) -> dict[str, Any]:
        if user_id not in employees:
            raise AccessDenied(f"unknown caller user_id={user_id!r}")

        target_id = args["ticket_id"]
        ticket = tickets.get(target_id)
        if ticket is None:
            raise AccessDenied(f"cannot view ticket {target_id!r}")

        # Authorization
        if is_ticket_principal(ticket, user_id):
            allowed = True
        elif ticket.project_id is not None:
            project = projects.get(ticket.project_id)
            allowed = project is not None and is_project_member(
                project, user_id,
            )
        else:
            allowed = False

        if not allowed:
            raise AccessDenied(
                f"user {user_id!r} cannot view ticket {target_id!r}"
            )

        return {
            "ticket_id": ticket.ticket_id,
            "title": ticket.title,
            "owner_id": ticket.owner_id,
            "assignee_id": ticket.assignee_id,
            "status": ticket.status,
            "classification": ticket.classification,
            "project_id": ticket.project_id,
            "created_at": ticket.created_at.isoformat(),
            "type": ticket.type,
        }

    return handler
