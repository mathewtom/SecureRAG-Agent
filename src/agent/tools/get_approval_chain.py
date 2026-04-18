"""get_approval_chain tool: resolve who must sign off on an
expense / contract of a given amount for a given employee, per
`approval_matrix_2026.md`.

LLM-visible schema: `{employee_id: str, amount_usd: float}`.
`user_id` is injected by AuthenticatedToolNode from state.

Authorization: caller must be the employee, in their manager chain,
or in Finance / Human Resources.

The approval matrix is hardcoded here because it IS the operational
policy — the doc in data/meridian/documents/ is the human-readable
mirror, not the runtime source.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.tools import tool

from src.agent.tools.auth import (
    has_department_clearance,
    is_in_manager_chain,
)
from src.agent.tools.registry import ToolHandler
from src.data.loaders import Employee
from src.exceptions import AccessDenied

_RULE_SOURCE = "approval_matrix_2026.md"


@dataclass(frozen=True)
class _Band:
    label: str
    upper_inclusive: float
    roles: tuple[str, ...]


# Ordered low → high. The first band whose upper_inclusive >= amount
# wins. Last band has math.inf to catch anything above $100k.
_MATRIX: tuple[_Band, ...] = (
    _Band("Up to $1,000",            1_000.0,    ("Manager",)),
    _Band("$1,001 – $10,000",       10_000.0,    ("Director",)),
    _Band("$10,001 – $50,000",      50_000.0,    ("VP / function head",)),
    _Band("$50,001 – $100,000",    100_000.0,    ("CFO",)),
    _Band("Above $100,000",          math.inf,   ("CFO", "CEO")),
)


@tool
def get_approval_chain(employee_id: str, amount_usd: float) -> str:
    """Return the chain of approvers required for an employee to
    submit an expense or contract at the given USD amount, per the
    current approval matrix.

    Args:
        employee_id: the employee submitting the request.
        amount_usd: the amount in US dollars (must be non-negative).
    """
    raise NotImplementedError(
        "get_approval_chain must be invoked via AuthenticatedToolNode; "
        "direct calls bypass the runtime user_id injection."
    )


def make_get_approval_chain_handler(
    *, employees: dict[str, Employee],
) -> ToolHandler:
    """Bind the employees directory into a get_approval_chain handler."""

    def handler(args: dict[str, Any], *, user_id: str) -> dict[str, Any]:
        if user_id not in employees:
            raise AccessDenied(f"unknown caller user_id={user_id!r}")

        target_id = args["employee_id"]
        amount = float(args["amount_usd"])

        if amount < 0:
            raise ValueError(
                f"amount_usd must be non-negative, got {amount}"
            )

        if target_id not in employees:
            raise AccessDenied(f"unknown employee {target_id!r}")

        # ---- Authorization ----
        in_chain = is_in_manager_chain(employees, user_id, target_id)
        is_finance = has_department_clearance(
            employees, user_id, "Finance",
        )
        is_hr = has_department_clearance(
            employees, user_id, "Human Resources",
        )

        if not (in_chain or is_finance or is_hr):
            raise AccessDenied(
                f"user {user_id!r} cannot query approval chain "
                f"for {target_id!r}"
            )

        # ---- Band resolution ----
        band = _band_for_amount(amount)
        approvers = [
            _resolve_role(role, target_id, employees)
            for role in band.roles
        ]

        return {
            "amount_usd": amount,
            "matrix_band": band.label,
            "required_approvers": approvers,
            "rule_source": _RULE_SOURCE,
        }

    return handler


def _band_for_amount(amount: float) -> _Band:
    for band in _MATRIX:
        if amount <= band.upper_inclusive:
            return band
    # math.inf in the last band guarantees we never reach here, but
    # be defensive.
    raise ValueError(f"no band matches amount {amount}")


def _resolve_role(
    role: str, employee_id: str, employees: dict[str, Employee],
) -> dict[str, str]:
    """Resolve a role label to a concrete (employee_id, name) by
    walking the org chart from `employee_id` upward (for hierarchical
    roles) or by directory lookup (for global roles like CFO/CEO).

    Returns {role, employee_id, name}. Raises ValueError if the role
    cannot be resolved (e.g., no Director exists in the chain).
    """
    if role == "Manager":
        emp = employees[employee_id]
        if emp.manager_id is None:
            raise ValueError(
                f"employee {employee_id!r} has no manager"
            )
        mgr = employees[emp.manager_id]
        return {
            "role": role,
            "employee_id": mgr.employee_id,
            "name": mgr.name,
        }

    if role == "Director":
        ancestor = _first_ancestor_with_title(
            employee_id, employees, lambda t: t.startswith("Director"),
        )
        if ancestor is None:
            raise ValueError(
                f"no Director in chain for {employee_id!r}"
            )
        return {
            "role": role,
            "employee_id": ancestor.employee_id,
            "name": ancestor.name,
        }

    if role == "VP / function head":
        ancestor = _first_ancestor_with_title(
            employee_id, employees, lambda t: t.startswith("VP"),
        )
        if ancestor is None:
            raise ValueError(
                f"no VP in chain for {employee_id!r}"
            )
        return {
            "role": role,
            "employee_id": ancestor.employee_id,
            "name": ancestor.name,
        }

    if role == "CFO":
        return _find_by_title(
            "Chief Financial Officer", role, employees,
        )

    if role == "CEO":
        # CEO is the org root (manager_id is None)
        for emp in employees.values():
            if emp.manager_id is None:
                return {
                    "role": role,
                    "employee_id": emp.employee_id,
                    "name": emp.name,
                }
        raise ValueError("no CEO (org root) in directory")

    raise ValueError(f"unknown role label {role!r}")


def _first_ancestor_with_title(
    employee_id: str,
    employees: dict[str, Employee],
    predicate: Callable[[str], bool],
) -> Employee | None:
    """Walk up from employee_id (excluding self) and return the first
    ancestor whose title matches the predicate."""
    current: str | None = employees[employee_id].manager_id
    while current is not None:
        emp = employees[current]
        if predicate(emp.title):
            return emp
        current = emp.manager_id
    return None


def _find_by_title(
    target_title: str, role_label: str, employees: dict[str, Employee],
) -> dict[str, str]:
    for emp in employees.values():
        if emp.title == target_title:
            return {
                "role": role_label,
                "employee_id": emp.employee_id,
                "name": emp.name,
            }
    raise ValueError(
        f"no employee with title {target_title!r} for role "
        f"{role_label!r}"
    )
