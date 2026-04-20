"""Tests for lookup_employee tool handler.

Each Phase 3 tool gets a test file with this shape:
- happy paths for each authz route (self, manager-chain, same-dept, HR)
- explicit denial for non-authorized callers
- redaction tests (salary visibility rules)
- impersonation-resistance test (LLM-supplied user_id ignored)
- unknown-entity tests (unknown user_id, unknown employee_id)
"""

import datetime

import pytest

from securerag_agent.agent.tools.lookup_employee import (
    lookup_employee,
    make_lookup_employee_handler,
)
from securerag_agent.data.loaders import Employee
from securerag_agent.exceptions import AccessDenied


def _emp(eid: str, *, manager: str | None = None,
         dept: str = "Engineering", clearance: int = 2,
         salary: int = 100_000) -> Employee:
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
        salary=salary,
        is_active=True,
    )


@pytest.fixture
def org() -> dict[str, Employee]:
    """A small org with multiple branches for authz testing."""
    return {
        "E001": _emp("E001", manager=None, dept="Executive",
                     clearance=4, salary=400_000),
        "E002": _emp("E002", manager="E001", dept="Engineering",
                     clearance=3, salary=180_000),
        "E003": _emp("E003", manager="E002", dept="Engineering",
                     salary=140_000),
        "E004": _emp("E004", manager="E002", dept="Engineering",
                     salary=145_000),
        "E005": _emp("E005", manager="E001", dept="Sales",
                     salary=200_000),
        "E006": _emp("E006", manager="E001", dept="Human Resources",
                     clearance=4, salary=200_000),
    }


@pytest.fixture
def handler(org: dict[str, Employee]):
    return make_lookup_employee_handler(employees=org)


# ---------- LLM-visible schema --------------------------------------------

def test_tool_schema_exposes_only_employee_id():
    """The LLM must see {employee_id: str} only - never user_id."""
    assert "employee_id" in lookup_employee.args
    assert "user_id" not in lookup_employee.args


# ---------- happy paths ---------------------------------------------------

def test_self_lookup_allowed(handler):
    result = handler({"employee_id": "E003"}, user_id="E003")
    assert result["employee_id"] == "E003"
    # Self-lookup sees own salary
    assert result["salary"] == 140_000


def test_manager_can_view_direct_report(handler):
    result = handler({"employee_id": "E003"}, user_id="E002")
    assert result["employee_id"] == "E003"
    # Manager chain sees salary
    assert result["salary"] == 140_000


def test_skip_level_manager_can_view(handler):
    result = handler({"employee_id": "E003"}, user_id="E001")
    assert result["employee_id"] == "E003"
    assert result["salary"] == 140_000


def test_same_department_can_view_basic_record(handler):
    """E004 (Engineering IC) views E003 (Engineering IC) - same dept."""
    result = handler({"employee_id": "E003"}, user_id="E004")
    assert result["employee_id"] == "E003"
    # Same-dept access does NOT see salary
    assert result["salary"] == "[REDACTED]"


def test_hr_can_view_anyone_with_full_record(handler):
    result = handler({"employee_id": "E005"}, user_id="E006")
    assert result["employee_id"] == "E005"
    # HR sees salary
    assert result["salary"] == 200_000


# ---------- denials -------------------------------------------------------

def test_cross_department_lookup_denied(handler):
    """E003 (Engineering IC) cannot view E005 (Sales) - not in chain, not same dept, not HR."""
    with pytest.raises(AccessDenied):
        handler({"employee_id": "E005"}, user_id="E003")


def test_unknown_user_id_denied(handler):
    """Per Task A's design note, handler must raise AccessDenied (not
    KeyError) when caller user_id is unknown."""
    with pytest.raises(AccessDenied):
        handler({"employee_id": "E003"}, user_id="E999")


def test_unknown_employee_id_denied(handler):
    """Looking up a non-existent employee returns AccessDenied (not
    silently returning empty), so the tool can't be used to enumerate
    valid IDs by probing for which raise vs return."""
    with pytest.raises(AccessDenied):
        handler({"employee_id": "E999"}, user_id="E001")


# ---------- redaction -----------------------------------------------------

def test_salary_redacted_for_same_dept_caller(handler):
    """Same-dept access sees the record minus salary."""
    result = handler({"employee_id": "E003"}, user_id="E004")
    assert result["salary"] == "[REDACTED]"
    assert "name" in result and "title" in result and "department" in result


def test_clearance_level_visible_to_self(handler):
    result = handler({"employee_id": "E003"}, user_id="E003")
    assert result["clearance_level"] == 2


def test_clearance_level_redacted_for_same_dept(handler):
    """Same-dept colleagues should not see each other's clearance."""
    result = handler({"employee_id": "E003"}, user_id="E004")
    assert result["clearance_level"] == "[REDACTED]"


def test_clearance_level_visible_to_manager(handler):
    result = handler({"employee_id": "E003"}, user_id="E002")
    assert result["clearance_level"] == 2


def test_clearance_level_visible_to_hr(handler):
    result = handler({"employee_id": "E003"}, user_id="E006")
    assert result["clearance_level"] == 2


# ---------- impersonation resistance --------------------------------------

def test_handler_ignores_user_id_in_args(handler):
    """Even if the LLM smuggles user_id into args, the handler must
    use the kwarg user_id (state-injected). The handler should NEVER
    read args.get("user_id")."""
    # E003 (low-privilege engineer) is the trusted caller
    # The "args" dict claims user_id="E001" (CEO) - should be ignored
    result = handler(
        {"employee_id": "E003", "user_id": "E001"},
        user_id="E003",
    )
    # Result should be the self-lookup view (own salary visible),
    # NOT the CEO's view (which would also see it but for different reasons)
    assert result["employee_id"] == "E003"
    # Verify the result reflects E003's view (this is a self-lookup)
    assert result["salary"] == 140_000


def test_handler_denies_when_state_user_unauthorized_even_if_args_user_authorized(handler):
    """The args.user_id should not be able to escalate privilege."""
    # E003 trying to view E005 (cross-dept, denied), with args claiming
    # user_id=E006 (HR, would normally be allowed). State user_id wins;
    # E003 is denied.
    with pytest.raises(AccessDenied):
        handler(
            {"employee_id": "E005", "user_id": "E006"},
            user_id="E003",
        )
