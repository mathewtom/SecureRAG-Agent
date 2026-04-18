"""Load eval queries from JSONL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.schema import (
    ExpectedOutcome,
    Outcome,
    Query,
    StubScriptStep,
)


def load_queries(path: Path) -> list[Query]:
    """Load queries from a JSONL file. Blank lines are skipped."""
    out: list[Query] = []
    text = path.read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"{path}:{lineno}: invalid JSON: {e}"
            ) from e
        out.append(_build_query(obj, source=f"{path}:{lineno}"))
    return out


def _build_query(obj: dict[str, Any], *, source: str) -> Query:
    expected_raw = obj.get("expected", {})
    if not isinstance(expected_raw, dict):
        raise ValueError(f"{source}: 'expected' must be a dict")
    expected_obj: dict[str, Any] = expected_raw
    try:
        outcome = Outcome(expected_obj["outcome"])
    except (KeyError, ValueError) as e:
        raise ValueError(
            f"{source}: invalid or missing outcome: {e}"
        ) from e

    expected = ExpectedOutcome(
        outcome=outcome,
        answer_contains=list(expected_obj.get("answer_contains", [])),
        answer_excludes=list(expected_obj.get("answer_excludes", [])),
        tool_sequence=list(expected_obj.get("tool_sequence", [])),
        min_denial_records=int(expected_obj.get(
            "min_denial_records", 0,
        )),
        min_retrieved_docs=int(expected_obj.get(
            "min_retrieved_docs", 0,
        )),
    )

    raw_script: list[dict[str, Any]] = obj.get("stub_llm_script", [])
    script = [
        StubScriptStep(
            tool_calls=list(step.get("tool_calls", [])),
            content=str(step.get("content", "")),
        )
        for step in raw_script
    ]

    return Query(
        id=str(obj["id"]),
        category=str(obj["category"]),
        user_id=str(obj["user_id"]),
        query=str(obj["query"]),
        expected=expected,
        stub_llm_script=script,
    )
