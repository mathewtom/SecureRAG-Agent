"""Tests for eval query loader."""

from pathlib import Path

import pytest

from eval.loader import load_queries
from eval.schema import Outcome


def test_load_sample_queries():
    queries = load_queries(
        Path("tests/eval/fixtures/sample_queries.jsonl"),
    )
    assert len(queries) == 2
    assert queries[0].id == "Q001"
    assert queries[0].category == "single_hop_search"
    assert queries[0].expected.outcome == Outcome.ANSWERED
    assert queries[0].expected.answer_contains == ["250"]
    assert len(queries[0].stub_llm_script) == 1


def test_load_preserves_query_order():
    queries = load_queries(
        Path("tests/eval/fixtures/sample_queries.jsonl"),
    )
    ids = [q.id for q in queries]
    assert ids == ["Q001", "Q002"]


def test_load_unknown_outcome_raises(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"id": "Q1", "category": "x", "user_id": "E003",'
        ' "query": "q", "expected": {"outcome": "totally_invalid"},'
        ' "stub_llm_script": []}\n'
    )
    with pytest.raises(ValueError):
        load_queries(bad)


def test_load_empty_file_returns_empty(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert load_queries(empty) == []


def test_load_skips_blank_lines(tmp_path):
    f = tmp_path / "with_blanks.jsonl"
    f.write_text(
        '{"id": "Q1", "category": "x", "user_id": "E003",'
        ' "query": "q", "expected": {"outcome": "answered"},'
        ' "stub_llm_script": []}\n'
        '\n'
        '{"id": "Q2", "category": "x", "user_id": "E003",'
        ' "query": "q", "expected": {"outcome": "answered"},'
        ' "stub_llm_script": []}\n'
    )
    queries = load_queries(f)
    assert len(queries) == 2
