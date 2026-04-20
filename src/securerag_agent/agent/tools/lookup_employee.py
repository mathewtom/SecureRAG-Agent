"""lookup_employee tool: return a partial employee record per the
caller's authorization.

LLM-visible schema: `{employee_id: str}`. The `user_id` is injected by
`AuthenticatedToolNode` from state - never from args.

Authorization rules (compose to "ALLOW" if any matches; "DENY" if none):
  - caller IS target (self-lookup)
  - caller is in target's manager chain (direct or skip-level)
  - caller is in target's department (peer view)
  - caller is in Human Resources (role-based clearance)

Salary and clearance_level visibility (stricter than record-level
visibility; only some of the ALLOW paths see them):
  - salary: HR, self, manager chain
  - clearance_level: HR, self, manager chain (NOT same-dept peers)

Other fields (employee_id, name, title, department, manager_id, location,
hire_date, email, is_active) are visible to anyone authorized to see
the record.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from securerag_agent.agent.tools.auth import (
    has_department_clearance,
    is_in_manager_chain,
    same_department,
)
from securerag_agent.agent.tools.registry import ToolHandler
from securerag_agent.data.loaders import Employee
from securerag_agent.exceptions import AccessDenied

_REDACTED = "[REDACTED]"


@tool
def lookup_employee(employee_id: str) -> str:
    """Look up an employee by their ID and return their profile,
    redacted per the caller's authorization.

    Args:
        employee_id: the employee_id (e.g., "E003") to look up.
    """
    raise NotImplementedError(
        "lookup_employee must be invoked via AuthenticatedToolNode; "
        "direct calls bypass the runtime user_id injection."
    )


def make_lookup_employee_handler(
    *, employees: dict[str, Employee],
) -> ToolHandler:
    """Bind the employees directory into a lookup_employee handler."""

    def handler(args: dict[str, Any], *, user_id: str) -> dict[str, Any]:
        # Validate caller exists. is_in_manager_chain returns False for
        # unknown IDs by design (Task A note); we raise AccessDenied
        # explicitly so a typo'd user_id doesn't silently look like a
        # rejected lookup.
        if user_id not in employees:
            raise AccessDenied(
                f"unknown caller user_id={user_id!r}"
            )

        target_id = args["employee_id"]
        if target_id not in employees:
            # Don't leak which IDs exist via differential errors;
            # treat unknown target as denied just like unauthorized.
            raise AccessDenied(
                f"cannot view employee {target_id!r}"
            )

        # ---- Authorization decision ----
        in_chain = is_in_manager_chain(employees, user_id, target_id)
        is_peer = same_department(employees, user_id, target_id)
        is_hr = has_department_clearance(
            employees, user_id, "Human Resources",
        )

        if not (in_chain or is_peer or is_hr):
            raise AccessDenied(
                f"user {user_id!r} cannot view employee {target_id!r}"
            )

        # ---- Field visibility ----
        target = employees[target_id]
        sees_salary_and_clearance = in_chain or is_hr

        return {
            "employee_id": target.employee_id,
            "name": target.name,
            "title": target.title,
            "department": target.department,
            "manager_id": target.manager_id,
            "location": target.location,
            "hire_date": target.hire_date.isoformat(),
            "email": target.email,
            "is_active": target.is_active,
            "salary": (
                target.salary if sees_salary_and_clearance else _REDACTED
            ),
            "clearance_level": (
                target.clearance_level
                if sees_salary_and_clearance else _REDACTED
            ),
        }

    return handler
