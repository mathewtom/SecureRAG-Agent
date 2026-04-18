"""escalate_to_human tool: agent surrenders the question to a human
operator.

LLM-visible schema: {reason: str}.

Authorization: none — escalation is always available. The only
guard is that the caller user_id must exist (a phantom user_id
shouldn't be able to spam audit-log escalations).

Every escalation emits an audit-log entry via the audit module so a
human reviewer can see the agent's escalation history.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from src.agent.tools.registry import ToolHandler
from src.data.loaders import Employee
from src.exceptions import AccessDenied


@tool
def escalate_to_human(reason: str) -> str:
    """Escalate the user's question to a human. Use when you cannot
    or should not answer.

    Args:
        reason: a short explanation of why escalation is needed
            (free-text; will be logged to the audit trail).
    """
    raise NotImplementedError(
        "escalate_to_human must be invoked via AuthenticatedToolNode; "
        "direct calls bypass the runtime user_id injection."
    )


def make_escalate_to_human_handler(
    *,
    employees: dict[str, Employee],
    audit: Any,
) -> ToolHandler:
    """Bind the directory + audit module into a handler."""

    def handler(args: dict[str, Any], *, user_id: str) -> dict[str, Any]:
        if user_id not in employees:
            raise AccessDenied(f"unknown caller user_id={user_id!r}")

        reason = str(args.get("reason", ""))
        # Reuse log_denial — escalation is conceptually a denial of
        # the autonomous answer path.
        audit.log_denial(
            request_id="<runtime>",
            user_id=user_id,
            layer="escalate_to_human",
            reason=reason or "<no reason given>",
        )
        return {
            "escalated": True,
            "reason": reason,
        }

    return handler
