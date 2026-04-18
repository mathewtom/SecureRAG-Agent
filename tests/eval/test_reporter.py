"""Tests for the eval reporter (markdown output)."""

from eval.reporter import render_report
from eval.schema import (
    ExpectedOutcome,
    Outcome,
    Query,
    RunResult,
)


def _result(qid: str, *, passed: bool = True,
            outcome: Outcome = Outcome.ANSWERED,
            category: str = "single_hop_search") -> tuple[Query, RunResult]:
    expected = ExpectedOutcome(outcome=Outcome.ANSWERED)
    actual = outcome if passed else Outcome.BLOCKED
    q = Query(id=qid, category=category, user_id="E003",
              query="test", expected=expected, stub_llm_script=[])
    r = RunResult(
        query_id=qid,
        actual_outcome=actual,
        actual_answer="ok" if passed else None,
        actual_tool_sequence=[],
        actual_denial_count=0,
        actual_retrieved_doc_count=0,
        expected=expected,
    )
    return q, r


def test_report_includes_summary_line():
    pairs = [_result("Q1"), _result("Q2"), _result("Q3", passed=False)]
    text = render_report(pairs, mode="stub", run_date="2026-04-17")
    assert "2 / 3" in text or "2/3" in text or "3 queries" in text
    assert "Pass" in text or "pass" in text
    assert "Fail" in text or "fail" in text


def test_report_groups_by_category():
    pairs = [
        _result("Q1", category="single_hop_search"),
        _result("Q2", category="multi_hop_lookup", passed=False),
    ]
    text = render_report(pairs, mode="stub", run_date="2026-04-17")
    assert "single_hop_search" in text
    assert "multi_hop_lookup" in text


def test_report_lists_failures():
    pairs = [_result("Q1", passed=False)]
    text = render_report(pairs, mode="stub", run_date="2026-04-17")
    assert "Q1" in text
    assert "FAIL" in text or "fail" in text
