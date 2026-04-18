"""Tests for eval schema dataclasses."""

from eval.schema import (
    ExpectedOutcome,
    Outcome,
    Query,
    RunResult,
)


def test_outcome_enum_values():
    assert Outcome.ANSWERED.value == "answered"
    assert Outcome.BLOCKED.value == "blocked"
    assert Outcome.FLAGGED.value == "flagged"
    assert Outcome.BUDGET_EXHAUSTED.value == "budget_exhausted"
    assert Outcome.RATE_LIMITED.value == "rate_limited"
    assert Outcome.ERROR.value == "error"


def test_expected_outcome_defaults():
    """A minimal ExpectedOutcome only requires `outcome`."""
    expected = ExpectedOutcome(outcome=Outcome.ANSWERED)
    assert expected.answer_contains == []
    assert expected.answer_excludes == []
    assert expected.tool_sequence == []
    assert expected.min_denial_records == 0
    assert expected.min_retrieved_docs == 0


def test_query_construction():
    q = Query(
        id="Q001",
        category="single_hop_search",
        user_id="E003",
        query="What is the parking cap?",
        expected=ExpectedOutcome(outcome=Outcome.ANSWERED,
                                 answer_contains=["250"]),
        stub_llm_script=[],
    )
    assert q.id == "Q001"
    assert q.expected.answer_contains == ["250"]


def test_run_result_pass_when_all_criteria_met():
    expected = ExpectedOutcome(outcome=Outcome.ANSWERED)
    result = RunResult(
        query_id="Q001",
        actual_outcome=Outcome.ANSWERED,
        actual_answer="The cap is $250.",
        actual_tool_sequence=["search_documents"],
        actual_denial_count=0,
        actual_retrieved_doc_count=1,
        expected=expected,
    )
    assert result.passed is True
    assert result.failure_reason is None


def test_run_result_fail_on_outcome_mismatch():
    expected = ExpectedOutcome(outcome=Outcome.ANSWERED)
    result = RunResult(
        query_id="Q001",
        actual_outcome=Outcome.BLOCKED,
        actual_answer=None,
        actual_tool_sequence=[],
        actual_denial_count=0,
        actual_retrieved_doc_count=0,
        expected=expected,
    )
    assert result.passed is False
    assert "outcome" in (result.failure_reason or "").lower()


def test_run_result_fail_on_missing_substring():
    expected = ExpectedOutcome(
        outcome=Outcome.ANSWERED,
        answer_contains=["250"],
    )
    result = RunResult(
        query_id="Q001",
        actual_outcome=Outcome.ANSWERED,
        actual_answer="The cap is $200.",
        actual_tool_sequence=[],
        actual_denial_count=0,
        actual_retrieved_doc_count=0,
        expected=expected,
    )
    assert result.passed is False
    assert "250" in (result.failure_reason or "")


def test_run_result_fail_on_excluded_substring():
    expected = ExpectedOutcome(
        outcome=Outcome.ANSWERED,
        answer_excludes=["[REDACTED]"],
    )
    result = RunResult(
        query_id="Q001",
        actual_outcome=Outcome.ANSWERED,
        actual_answer="Salary is [REDACTED].",
        actual_tool_sequence=[],
        actual_denial_count=0,
        actual_retrieved_doc_count=0,
        expected=expected,
    )
    assert result.passed is False
    assert "[REDACTED]" in (result.failure_reason or "")


def test_run_result_fail_on_tool_sequence_mismatch():
    expected = ExpectedOutcome(
        outcome=Outcome.ANSWERED,
        tool_sequence=["search_documents", "lookup_employee"],
    )
    result = RunResult(
        query_id="Q001",
        actual_outcome=Outcome.ANSWERED,
        actual_answer="ok",
        actual_tool_sequence=["search_documents"],
        actual_denial_count=0,
        actual_retrieved_doc_count=0,
        expected=expected,
    )
    assert result.passed is False
    assert "tool_sequence" in (result.failure_reason or "").lower() \
        or "tool" in (result.failure_reason or "").lower()


def test_run_result_fail_on_too_few_denials():
    expected = ExpectedOutcome(
        outcome=Outcome.ANSWERED,
        min_denial_records=1,
    )
    result = RunResult(
        query_id="Q001",
        actual_outcome=Outcome.ANSWERED,
        actual_answer="ok",
        actual_tool_sequence=[],
        actual_denial_count=0,
        actual_retrieved_doc_count=0,
        expected=expected,
    )
    assert result.passed is False
