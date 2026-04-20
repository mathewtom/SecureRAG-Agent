"""Referential integrity tests for the agentic Meridian dataset.

These tests are the contract that the rest of Phase 2 onward depends on.
Any change that breaks them is a regression — the dataset's whole point is
that downstream tools can trust its invariants without re-validating them
on every call.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from securerag_agent.data.loaders import (
    CLASSIFICATION_VALUES,
    DATA_ROOT,
    load_calendar,
    load_documents,
    load_employees,
    load_projects,
    load_tickets,
)

CANONICAL_DEPARTMENTS = {
    "Executive",
    "Engineering",
    "Security",
    "Product",
    "Sales",
    "Marketing",
    "Finance",
    "Legal",
    "Human Resources",
}

VALID_CLASSIFICATIONS = {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}


@pytest.fixture(scope="module")
def employees():
    return load_employees()


@pytest.fixture(scope="module")
def employee_ids(employees):
    return {e.employee_id for e in employees}


@pytest.fixture(scope="module")
def employees_by_id(employees):
    return {e.employee_id: e for e in employees}


@pytest.fixture(scope="module")
def projects():
    return load_projects()


@pytest.fixture(scope="module")
def tickets():
    return load_tickets()


@pytest.fixture(scope="module")
def events():
    return load_calendar()


@pytest.fixture(scope="module")
def documents():
    return load_documents(include_poisoned=True)


# ---------- employees -----------------------------------------------------


def test_employee_count(employees):
    assert len(employees) >= 30, "plan calls for 30–50 employees"
    assert len(employees) <= 50


def test_employee_ids_unique_and_well_formed(employees):
    ids = [e.employee_id for e in employees]
    assert len(ids) == len(set(ids))
    for eid in ids:
        assert eid.startswith("E") and eid[1:].isdigit() and len(eid) == 4


def test_every_manager_id_resolves(employees, employee_ids):
    for emp in employees:
        if emp.manager_id is None:
            continue
        assert emp.manager_id in employee_ids, (
            f"{emp.employee_id} manager_id {emp.manager_id} not in employee set"
        )


def test_exactly_one_root(employees):
    roots = [e for e in employees if e.manager_id is None]
    assert len(roots) == 1, f"expected exactly one root (CEO), got {len(roots)}"
    assert roots[0].employee_id == "E012"


def test_no_manager_cycles(employees, employees_by_id):
    for emp in employees:
        seen: set[str] = set()
        cur: str | None = emp.employee_id
        while cur is not None:
            if cur in seen:
                pytest.fail(f"manager cycle reachable from {emp.employee_id}")
            seen.add(cur)
            cur = employees_by_id[cur].manager_id


def test_hierarchy_depth_at_least_four(employees, employees_by_id):
    def depth(eid: str) -> int:
        d = 0
        cur: str | None = eid
        while cur is not None:
            cur = employees_by_id[cur].manager_id
            d += 1
        return d

    max_depth = max(depth(e.employee_id) for e in employees)
    assert max_depth >= 4, f"plan requires 4+ level hierarchy, got {max_depth}"


def test_canonical_departments(employees):
    for emp in employees:
        assert emp.department in CANONICAL_DEPARTMENTS, (
            f"{emp.employee_id} has non-canonical department {emp.department!r}"
        )


def test_clearance_in_range(employees):
    for emp in employees:
        assert 1 <= emp.clearance_level <= 4


def test_required_ambiguities_present(employees):
    by_surname: dict[str, list[str]] = {}
    by_first: dict[str, list[str]] = {}
    for emp in employees:
        parts = emp.name.split()
        by_first.setdefault(parts[0], []).append(emp.employee_id)
        by_surname.setdefault(parts[-1], []).append(emp.employee_id)

    assert len(by_surname.get("Anderson", [])) >= 3, (
        "DATASET_DESIGN.md requires 3 Anderson collisions"
    )
    assert len(by_surname.get("Chen", [])) >= 2
    assert len(by_surname.get("Walsh", [])) >= 2
    assert len(by_first.get("Marcus", [])) >= 2


def test_csv_matches_json(employees):
    csv_rows = []
    with (DATA_ROOT / "employees.csv").open(newline="") as fp:
        for row in csv.DictReader(fp):
            csv_rows.append(row)
    assert len(csv_rows) == len(employees)
    json_by_id = {e.employee_id: e for e in employees}
    for row in csv_rows:
        emp = json_by_id[row["employee_id"]]
        assert row["name"] == emp.name
        assert row["title"] == emp.title
        assert row["department"] == emp.department
        assert (row["manager_id"] or None) == emp.manager_id
        assert int(row["clearance_level"]) == emp.clearance_level
        assert row["email"] == emp.email
        assert int(row["salary"]) == emp.salary


# ---------- projects ------------------------------------------------------


def test_project_owner_and_members_resolve(projects, employee_ids):
    for proj in projects:
        assert proj.owner_id in employee_ids
        for member in proj.members:
            assert member in employee_ids, (
                f"project {proj.project_id} member {member} not in employee set"
            )


def test_project_classification_valid(projects):
    for proj in projects:
        assert proj.classification in VALID_CLASSIFICATIONS


def test_project_status_valid(projects):
    valid = {"active", "completed", "on_hold", "cancelled"}
    for proj in projects:
        assert proj.status in valid


def test_project_name_collision_present(projects):
    names = [p.name.lower() for p in projects]
    assert any("atlas" in n for n in names)
    assert sum("atlas" in n for n in names) >= 2, (
        "Need Project Atlas and Atlas Mobile collision"
    )
    assert sum("phoenix" in n for n in names) >= 2, (
        "Need Phoenix and Phoenix 2.0 collision"
    )


# ---------- tickets -------------------------------------------------------


def test_ticket_references(tickets, employee_ids):
    project_ids = {p.project_id for p in load_projects()}
    valid_types = {"hr", "it", "security", "engineering", "legal", "finance"}
    valid_statuses = {"open", "in_progress", "resolved", "closed"}
    for ticket in tickets:
        assert ticket.owner_id in employee_ids
        assert ticket.assignee_id in employee_ids
        if ticket.project_id is not None:
            assert ticket.project_id in project_ids
        assert 1 <= ticket.classification <= 4
        assert ticket.type in valid_types
        assert ticket.status in valid_statuses


def test_ticket_count(tickets):
    assert len(tickets) >= 60, "Phase 1 calls for a meaningful ticket corpus"


# ---------- calendar ------------------------------------------------------


def test_calendar_references(events, employee_ids):
    for ev in events:
        assert ev.organizer_id in employee_ids
        for att in ev.attendees:
            assert att in employee_ids
        assert 1 <= ev.classification <= 4
        assert ev.start <= ev.end


def test_calendar_has_restricted_events(events):
    restricted = [e for e in events if e.classification == 4]
    assert len(restricted) >= 5, (
        "Need a meaningful number of RESTRICTED events to exercise authz"
    )


# ---------- documents -----------------------------------------------------


def test_document_classification_valid(documents):
    for doc in documents:
        assert doc.classification in VALID_CLASSIFICATIONS, (
            f"{doc.path}: classification {doc.classification!r} not valid"
        )


def test_poisoned_directory_marked(documents):
    poisoned = [d for d in documents if d.is_poisoned]
    poisoned_paths = {d.path for d in poisoned}

    docs_root = DATA_ROOT / "documents"
    files_in_poisoned = set(docs_root.glob("poisoned/*.md")) | set(
        docs_root.glob("poisoned/*.txt")
    )
    assert files_in_poisoned, "expected files under documents/poisoned/"
    for path in files_in_poisoned:
        assert path in poisoned_paths, (
            f"file {path} is in poisoned/ but not flagged TEST_POISONED in frontmatter"
        )


def test_default_load_excludes_poisoned():
    safe = load_documents(include_poisoned=False)
    assert all(not d.is_poisoned for d in safe)
    assert len(safe) >= 15, "expected a substantial non-poisoned doc corpus"


def test_temporal_policy_pairs_present(documents):
    titles = {doc.path.name for doc in documents}
    assert "expense_policy_2025.md" in titles
    assert "expense_policy_2026.md" in titles
    assert "approval_matrix_2025.md" in titles
    assert "approval_matrix_2026.md" in titles


def test_required_agentic_poisoned_present(documents):
    agentic_attacks = {
        "tool_chain_redirect",
        "authorization_confusion",
        "goal_hijack",
    }
    found = {
        doc.frontmatter.get("attack_class")
        for doc in documents
        if doc.is_poisoned
    }
    missing = agentic_attacks - found
    assert not missing, f"missing agentic attack fixtures: {missing}"


def test_restricted_documents_have_recipient_list(documents):
    for doc in documents:
        if doc.classification == "RESTRICTED" and not doc.is_poisoned:
            assert "restricted_to" in doc.frontmatter, (
                f"{doc.path.name}: RESTRICTED documents must enumerate recipients"
            )
            recipients = doc.frontmatter["restricted_to"]
            assert isinstance(recipients, list) and recipients, (
                f"{doc.path.name}: restricted_to must be a non-empty list"
            )


# ---------- cross-corpus invariants --------------------------------------


def test_horizon_membership_consistent(projects, documents):
    horizon = next(p for p in projects if p.project_id == "P011")
    horizon_set = {horizon.owner_id, *horizon.members}

    for doc in documents:
        if doc.frontmatter.get("project_id") == "P011" and doc.classification == "RESTRICTED":
            recipients = set(doc.frontmatter.get("restricted_to", []))
            assert recipients == horizon_set, (
                f"{doc.path.name}: restricted_to must equal Horizon membership; "
                f"got {recipients}, expected {horizon_set}"
            )
