"""Markdown report generation for eval results."""

from __future__ import annotations

from collections import defaultdict

from eval.schema import Query, RunResult


def render_report(
    pairs: list[tuple[Query, RunResult]],
    *,
    mode: str,
    run_date: str,
) -> str:
    total = len(pairs)
    passed = sum(1 for _, r in pairs if r.passed)
    failed = total - passed
    pass_pct = (passed / total * 100) if total else 0.0

    out: list[str] = []
    out.append(f"# Eval Run — {run_date} ({mode} mode)\n")
    out.append(f"**Tests:** {total} queries  ")
    out.append(f"**Pass:** {passed} ({pass_pct:.1f}%)  ")
    out.append(f"**Fail:** {failed}\n")

    # By category
    by_cat: dict[str, list[tuple[Query, RunResult]]] = defaultdict(list)
    for q, r in pairs:
        by_cat[q.category].append((q, r))

    out.append("\n## By category\n")
    out.append("| Category | Pass | Fail |\n|---|---|---|")
    for cat in sorted(by_cat):
        cat_pairs = by_cat[cat]
        cat_pass = sum(1 for _, r in cat_pairs if r.passed)
        cat_fail = len(cat_pairs) - cat_pass
        out.append(f"| {cat} | {cat_pass} / {len(cat_pairs)} | {cat_fail} |")

    # Failures
    failures = [(q, r) for q, r in pairs if not r.passed]
    if failures:
        out.append("\n## Failures\n")
        for q, r in failures:
            out.append(f"### {q.id} — {q.category} — FAIL\n")
            out.append(f"- **Query:** `{q.query}`")
            out.append(f"- **User:** `{q.user_id}`")
            out.append(f"- **Expected outcome:** {q.expected.outcome.value}")
            out.append(f"- **Actual outcome:** {r.actual_outcome.value}")
            if r.failure_reason:
                out.append(f"- **Reason:** {r.failure_reason}")
            if r.raw_exception:
                out.append(f"- **Exception:** `{r.raw_exception}`")
            out.append("")

    return "\n".join(out) + "\n"
