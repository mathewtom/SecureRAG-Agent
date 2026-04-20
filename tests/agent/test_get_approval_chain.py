"""Tests for get_approval_chain tool handler."""

import datetime

import pytest

from securerag_agent.agent.tools.get_approval_chain import (
    get_approval_chain,
    make_get_approval_chain_handler,
)
from securerag_agent.data.loaders import Employee
from securerag_agent.exceptions import AccessDenied


def _emp(eid: str, *, manager: str | None = None,
         dept: str = "Engineering", title: str = "Software Engineer",
         clearance: int = 2) -> Employee:
    return Employee(
        employee_id=eid,
        name=f"Test {eid}",
        title=title,
        department=dept,
        manager_id=manager,
        clearance_level=clearance,
        location="Remote",
        hire_date=datetime.date(2024, 1, 1),
        email=f"{eid}@example.com",
        salary=100000,
        is_active=True,
    )


@pytest.fixture
def org() -> dict[str, Employee]:
    """An org with a complete approval chain."""
    return {
        "E001": _emp("E001", manager=None, dept="Executive",
                     title="Chief Executive Officer", clearance=4),
        "E002": _emp("E002", manager="E001", dept="Engineering",
                     title="VP of Engineering", clearance=4),
        "E003": _emp("E003", manager="E002", dept="Engineering",
                     title="Director of Engineering", clearance=3),
        "E004": _emp("E004", manager="E003", dept="Engineering",
                     title="Engineering Manager"),
        "E005": _emp("E005", manager="E004", dept="Engineering",
                     title="Software Engineer"),
        # CFO branch
        "E007": _emp("E007", manager="E001", dept="Finance",
                     title="Chief Financial Officer", clearance=4),
        # HR
        "E008": _emp("E008", manager="E001", dept="Human Resources",
                     title="VP of People", clearance=4),
        # Outsider for denial tests
        "E009": _emp("E009", manager="E001", dept="Sales",
                     title="Account Executive"),
    }


@pytest.fixture
def handler(org: dict[str, Employee]):
    return make_get_approval_chain_handler(employees=org)


# ---------- LLM schema ----------------------------------------------------

def test_tool_schema_exposes_only_employee_id_and_amount():
    """LLM schema must include employee_id + amount_usd, NOT user_id."""
    assert "employee_id" in get_approval_chain.args
    assert "amount_usd" in get_approval_chain.args
    assert "user_id" not in get_approval_chain.args


# ---------- amount band resolution ----------------------------------------

def test_amount_under_1000_requires_manager(handler):
    """E005 (mgr E004) submitting $500."""
    result = handler(
        {"employee_id": "E005", "amount_usd": 500.0},
        user_id="E005",
    )
    assert result["matrix_band"] == "Up to $1,000"
    approvers = result["required_approvers"]
    assert len(approvers) == 1
    assert approvers[0]["role"] == "Manager"
    assert approvers[0]["employee_id"] == "E004"


def test_amount_5000_requires_director(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 5000.0},
        user_id="E005",
    )
    assert result["matrix_band"] == "$1,001 – $10,000"
    approvers = result["required_approvers"]
    assert len(approvers) == 1
    assert approvers[0]["role"] == "Director"
    assert approvers[0]["employee_id"] == "E003"


def test_amount_25000_requires_vp(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 25000.0},
        user_id="E005",
    )
    assert result["matrix_band"] == "$10,001 – $50,000"
    approvers = result["required_approvers"]
    assert len(approvers) == 1
    assert approvers[0]["role"] == "VP / function head"
    assert approvers[0]["employee_id"] == "E002"


def test_amount_75000_requires_cfo(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 75000.0},
        user_id="E005",
    )
    assert result["matrix_band"] == "$50,001 – $100,000"
    approvers = result["required_approvers"]
    assert len(approvers) == 1
    assert approvers[0]["role"] == "CFO"
    assert approvers[0]["employee_id"] == "E007"


def test_amount_above_100000_requires_cfo_plus_ceo(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 150000.0},
        user_id="E005",
    )
    assert result["matrix_band"] == "Above $100,000"
    approvers = result["required_approvers"]
    assert len(approvers) == 2
    roles = {a["role"] for a in approvers}
    assert roles == {"CFO", "CEO"}


def test_boundary_exactly_1000_is_manager_band(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 1000.0},
        user_id="E005",
    )
    assert result["matrix_band"] == "Up to $1,000"


def test_boundary_exactly_100000_is_cfo_band_not_ceo(handler):
    """The 'Above $100k' band starts strictly above $100k."""
    result = handler(
        {"employee_id": "E005", "amount_usd": 100000.0},
        user_id="E005",
    )
    assert result["matrix_band"] == "$50,001 – $100,000"


# ---------- response shape ------------------------------------------------

def test_result_includes_amount_and_rule_source(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 500.0},
        user_id="E005",
    )
    assert result["amount_usd"] == 500.0
    assert result["rule_source"] == "approval_matrix_2026.md §Expense reports"


def test_result_approvers_include_name(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 500.0},
        user_id="E005",
    )
    assert result["required_approvers"][0]["name"] == "Test E004"


# ---------- authorization -------------------------------------------------

def test_self_can_query_own_approval_chain(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 500.0},
        user_id="E005",
    )
    assert result is not None


def test_manager_can_query_subordinate_chain(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 500.0},
        user_id="E004",
    )
    assert result is not None


def test_skip_level_can_query(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 500.0},
        user_id="E001",
    )
    assert result is not None


def test_finance_can_query_anyone(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 500.0},
        user_id="E007",  # CFO is in Finance
    )
    assert result is not None


def test_hr_can_query_anyone(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 500.0},
        user_id="E008",
    )
    assert result is not None


def test_sales_outsider_denied(handler):
    """E009 (Sales) cannot query E005's (Engineering) approval chain."""
    with pytest.raises(AccessDenied):
        handler(
            {"employee_id": "E005", "amount_usd": 500.0},
            user_id="E009",
        )


def test_unknown_user_id_denied(handler):
    with pytest.raises(AccessDenied):
        handler(
            {"employee_id": "E005", "amount_usd": 500.0},
            user_id="E999",
        )


def test_unknown_employee_id_denied(handler):
    with pytest.raises(AccessDenied):
        handler(
            {"employee_id": "E999", "amount_usd": 500.0},
            user_id="E001",
        )


# ---------- impersonation resistance --------------------------------------

def test_handler_ignores_user_id_in_args(handler):
    """LLM-supplied user_id in args is ignored; state user_id wins."""
    # E009 (Sales, would normally be denied) tries to claim user_id=E007
    # in args. The handler should use state's user_id (E009) and deny.
    with pytest.raises(AccessDenied):
        handler(
            {"employee_id": "E005", "amount_usd": 500.0,
             "user_id": "E007"},
            user_id="E009",
        )


# ---------- edge cases ----------------------------------------------------

def test_negative_amount_raises_value_error(handler):
    with pytest.raises(ValueError):
        handler(
            {"employee_id": "E005", "amount_usd": -100.0},
            user_id="E005",
        )


def test_zero_amount_is_manager_band(handler):
    result = handler(
        {"employee_id": "E005", "amount_usd": 0.0},
        user_id="E005",
    )
    assert result["matrix_band"] == "Up to $1,000"


# ---------- _resolve_role edge cases (org-chart shape gaps) ---------------

def _flat_org() -> dict[str, Employee]:
    """Org with no Director (only CEO + IC) - $5k band cannot resolve."""
    return {
        "E001": _emp("E001", manager=None, dept="Executive",
                     title="Chief Executive Officer", clearance=4),
        "E002": _emp("E002", manager="E001", dept="Engineering",
                     title="Software Engineer"),
    }


def _no_cfo_org() -> dict[str, Employee]:
    """Org without a CFO - the $75k band cannot resolve the CFO role."""
    return {
        "E001": _emp("E001", manager=None, dept="Executive",
                     title="Chief Executive Officer", clearance=4),
        "E002": _emp("E002", manager="E001", dept="Engineering",
                     title="Software Engineer"),
    }


def test_director_band_with_no_director_in_chain_raises_value_error():
    """If the chain has no Director ancestor, _resolve_role raises
    ValueError. AuthenticatedToolNode catches it as a tool error."""
    handler = make_get_approval_chain_handler(employees=_flat_org())
    with pytest.raises(ValueError, match="Director"):
        handler(
            {"employee_id": "E002", "amount_usd": 5000.0},
            user_id="E002",
        )


def test_cfo_band_with_no_cfo_in_directory_raises_value_error():
    handler = make_get_approval_chain_handler(employees=_no_cfo_org())
    with pytest.raises(ValueError, match="Chief Financial Officer"):
        handler(
            {"employee_id": "E002", "amount_usd": 75000.0},
            user_id="E002",
        )
