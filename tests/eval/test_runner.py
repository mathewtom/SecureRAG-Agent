"""Tests for the eval runner (stub-LLM mode only; live mode is
covered separately by manual / Phase 6 baseline runs)."""

from pathlib import Path

import pytest

from eval.runner import run_one_query
from eval.schema import (
    ExpectedOutcome,
    Outcome,
    Query,
    StubScriptStep,
)


def _q(qid: str, *, category: str = "single_hop_search",
       user_id: str = "E003", query: str = "test",
       expected: ExpectedOutcome | None = None,
       script: list[StubScriptStep] | None = None) -> Query:
    return Query(
        id=qid,
        category=category,
        user_id=user_id,
        query=query,
        expected=expected or ExpectedOutcome(outcome=Outcome.ANSWERED),
        stub_llm_script=script or [],
    )


def test_run_query_stub_mode_happy_path(tmp_path: Path):
    """A scripted LLM that returns a final answer with no tool calls
    should produce outcome=answered."""
    query = _q(
        "Q001",
        expected=ExpectedOutcome(
            outcome=Outcome.ANSWERED,
            answer_contains=["hello"],
        ),
        script=[StubScriptStep(content="hello world")],
    )

    result = run_one_query(query, mode="stub", logs_dir=tmp_path)

    assert result.actual_outcome == Outcome.ANSWERED
    assert "hello" in (result.actual_answer or "")
    assert result.passed is True


def test_run_query_stub_mode_with_tool_call(tmp_path: Path):
    """A scripted LLM that calls one tool then answers should produce
    a tool sequence with that tool's name."""
    query = _q(
        "Q002",
        expected=ExpectedOutcome(
            outcome=Outcome.ANSWERED,
            tool_sequence=["search_documents"],
        ),
        script=[
            StubScriptStep(tool_calls=[{
                "id": "t1", "name": "search_documents",
                "args": {"query": "x"},
            }]),
            StubScriptStep(content="done"),
        ],
    )

    result = run_one_query(query, mode="stub", logs_dir=tmp_path)

    assert result.actual_outcome == Outcome.ANSWERED
    assert result.actual_tool_sequence == ["search_documents"]
    assert result.passed is True


def test_run_query_failure_reported_when_actual_differs(tmp_path: Path):
    """If the actual outcome doesn't match expected, result.passed is False."""
    query = _q(
        "Q003",
        expected=ExpectedOutcome(
            outcome=Outcome.ANSWERED,
            answer_contains=["specific"],
        ),
        script=[StubScriptStep(content="totally different content")],
    )

    result = run_one_query(query, mode="stub", logs_dir=tmp_path)

    assert result.passed is False
    assert "specific" in (result.failure_reason or "")


def test_run_query_invalid_mode_raises():
    query = _q("Q004")
    with pytest.raises(ValueError, match="mode"):
        run_one_query(query, mode="banana")
