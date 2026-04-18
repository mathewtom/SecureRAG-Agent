"""End-to-end integration test: asks a question that can only be
answered by retrieving from the Meridian corpus.

Gated by the `integration` pytest marker. Requires:
  - Ollama running at $OLLAMA_HOST (default localhost:11434)
  - llama3.1:8b (or whatever $SECURERAG_MODEL points at) pulled
  - ChromaDB at data/chroma/ populated by ingest_meridian(...)

Skipped by default; run with: uv run pytest -m integration
"""

import pytest

pytestmark = pytest.mark.integration


def test_parking_policy_question_end_to_end() -> None:
    from fastapi.testclient import TestClient

    import src.api as api

    api._reset_chain_for_test()

    client = TestClient(api.app)
    resp = client.post(
        "/agent/query",
        json={"query": "What is the 2026 monthly parking reimbursement cap?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["termination_reason"] != "budget_exhausted"
    assert "250" in body["answer"]
    assert body["source_doc_ids"], "retriever returned nothing"
