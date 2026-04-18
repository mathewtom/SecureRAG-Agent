"""CLI entry point: `uv run python -m eval.run_eval`."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from eval.loader import load_queries
from eval.reporter import render_report
from eval.runner import run_one_query


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run agent eval queries and report results.",
    )
    parser.add_argument(
        "--queries", type=Path,
        default=Path("eval/agentic_queries.jsonl"),
        help="Path to JSONL query set.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Use live Ollama via src.api._build_chain (slow; "
             "requires Ollama + populated Chroma).",
    )
    parser.add_argument(
        "--query", action="append", default=[],
        help="Run only specific query IDs (repeatable).",
    )
    parser.add_argument(
        "--category", action="append", default=[],
        help="Run only queries in specific categories (repeatable).",
    )
    parser.add_argument(
        "--report", type=Path, default=None,
        help="Write a markdown report to this path.",
    )
    args = parser.parse_args(argv)

    mode = "live" if args.live else "stub"
    queries = load_queries(args.queries)

    if args.query:
        wanted = set(args.query)
        queries = [q for q in queries if q.id in wanted]
    if args.category:
        wanted_c = set(args.category)
        queries = [q for q in queries if q.category in wanted_c]

    if not queries:
        print("No queries selected.", file=sys.stderr)
        return 2

    pairs = []
    for q in queries:
        result = run_one_query(q, mode=mode)
        pairs.append((q, result))
        marker = "PASS" if result.passed else "FAIL"
        print(f"[{marker}] {q.id} ({q.category}) — "
              f"actual={result.actual_outcome.value}")

    run_date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    text = render_report(pairs, mode=mode, run_date=run_date)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
        print(f"\nReport written to {args.report}")
    else:
        print(f"\n{text}")

    return 0 if all(r.passed for _, r in pairs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
