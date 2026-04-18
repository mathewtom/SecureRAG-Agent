"""Tests for the /agent/query endpoint and exception mapping.

Uses FastAPI's TestClient and monkeypatches `_build_chain` to inject
a mock AgenticChain - this keeps the test fast (no Ollama, no Chroma).
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_mock_chain(monkeypatch: pytest.MonkeyPatch):
    mock_chain = MagicMock()

    import src.api
    monkeypatch.setattr(src.api, "_build_chain",
                        lambda: mock_chain)
    src.api._reset_chain_for_test()

    client = TestClient(src.api.app)
    return client, mock_chain


def test_health(app_with_mock_chain: tuple[TestClient, Any]):
    client, _ = app_with_mock_chain
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_agent_query_happy_path(app_with_mock_chain: tuple[TestClient, Any]):
    client, chain = app_with_mock_chain
    chain.invoke.return_value = {
        "request_id": "r1",
        "answer": "Parking is $250/month.",
        "source_doc_ids": ["expense_2026"],
        "termination_reason": "answered",
    }
    resp = client.post("/agent/query",
                       json={"query": "parking?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Parking is $250/month."
    assert body["source_doc_ids"] == ["expense_2026"]


def test_agent_query_returns_429_on_rate_limit(
    app_with_mock_chain: tuple[TestClient, Any],
):
    from src.rate_limiter import RateLimitExceeded
    client, chain = app_with_mock_chain
    chain.invoke.side_effect = RateLimitExceeded("too many", 60.0)
    resp = client.post("/agent/query", json={"query": "q"})
    assert resp.status_code == 429


def test_agent_query_returns_400_on_input_block(
    app_with_mock_chain: tuple[TestClient, Any],
):
    from src.exceptions import QueryBlocked
    client, chain = app_with_mock_chain
    chain.invoke.side_effect = QueryBlocked(
        "injection score too high", {"layer": "injection_scan"},
    )
    resp = client.post("/agent/query", json={"query": "q"})
    assert resp.status_code == 400


def test_agent_query_returns_422_on_output_flag(
    app_with_mock_chain: tuple[TestClient, Any],
):
    from src.exceptions import OutputFlagged
    client, chain = app_with_mock_chain
    chain.invoke.side_effect = OutputFlagged(["classification leak"])
    resp = client.post("/agent/query", json={"query": "q"})
    assert resp.status_code == 422


def test_agent_query_returns_422_on_budget_exhausted(
    app_with_mock_chain: tuple[TestClient, Any],
):
    from src.exceptions import BudgetExhausted
    client, chain = app_with_mock_chain
    chain.invoke.side_effect = BudgetExhausted(max_steps=20)
    resp = client.post("/agent/query", json={"query": "q"})
    assert resp.status_code == 422


def test_agent_query_validation_rejects_long_query(
    app_with_mock_chain: tuple[TestClient, Any],
):
    client, _ = app_with_mock_chain
    resp = client.post("/agent/query",
                       json={"query": "x" * 10_000})
    assert resp.status_code == 422  # FastAPI validation error
