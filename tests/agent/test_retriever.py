"""Tests for MeridianRetriever classification-level filtering."""

import datetime
from unittest.mock import Mock

import pytest

from src.agent.retriever import (
    MeridianRetriever,
    classifications_up_to,
)
from src.data.loaders import Employee
from src.exceptions import AccessDenied


def _emp(eid: str, clearance: int) -> Employee:
    return Employee(
        employee_id=eid,
        name="Test",
        title="Test",
        department="Engineering",
        manager_id=None,
        clearance_level=clearance,
        location="Remote",
        hire_date=datetime.date(2024, 1, 1),
        email=f"{eid}@example.com",
        salary=100000,
        is_active=True,
    )


def test_classifications_up_to_1():
    assert classifications_up_to(1) == ["PUBLIC"]


def test_classifications_up_to_3():
    assert set(classifications_up_to(3)) == {
        "PUBLIC", "INTERNAL", "CONFIDENTIAL",
    }


def test_classifications_up_to_4():
    assert set(classifications_up_to(4)) == {
        "PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED",
    }


def test_search_unknown_user_raises_access_denied():
    retriever = MeridianRetriever(
        collection=Mock(),
        employees_by_id={"E001": _emp("E001", 4)},
    )
    with pytest.raises(AccessDenied):
        retriever.search(query="hi", user_id="E999")


def test_search_passes_classification_filter_matching_clearance():
    collection = Mock()
    collection.query.return_value = {
        "ids": [["doc1"]],
        "documents": [["hello"]],
        "metadatas": [[{"classification": "INTERNAL"}]],
    }
    retriever = MeridianRetriever(
        collection=collection,
        employees_by_id={"E010": _emp("E010", 2)},  # INTERNAL
    )
    retriever.search(query="hi", user_id="E010")

    call_args = collection.query.call_args.kwargs
    where = call_args["where"]
    assert where == {
        "classification": {"$in": ["PUBLIC", "INTERNAL"]},
    }


def test_search_returns_flattened_results():
    collection = Mock()
    collection.query.return_value = {
        "ids": [["d1", "d2"]],
        "documents": [["a", "b"]],
        "metadatas": [[{"classification": "PUBLIC"},
                       {"classification": "PUBLIC"}]],
    }
    retriever = MeridianRetriever(
        collection=collection,
        employees_by_id={"E003": _emp("E003", 1)},
    )
    results = retriever.search(query="q", user_id="E003")
    assert [r["doc_id"] for r in results] == ["d1", "d2"]
    assert [r["content"] for r in results] == ["a", "b"]
