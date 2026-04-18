"""Typed eval-harness schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Outcome(StrEnum):
    ANSWERED = "answered"
    BLOCKED = "blocked"
    FLAGGED = "flagged"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


@dataclass(frozen=True)
class ExpectedOutcome:
    outcome: Outcome
    answer_contains: list[str] = field(default_factory=list)
    answer_excludes: list[str] = field(default_factory=list)
    tool_sequence: list[str] = field(default_factory=list)
    min_denial_records: int = 0
    min_retrieved_docs: int = 0


@dataclass(frozen=True)
class StubScriptStep:
    """One scripted LLM response in stub mode."""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    content: str = ""


@dataclass(frozen=True)
class Query:
    id: str
    category: str
    user_id: str
    query: str
    expected: ExpectedOutcome
    stub_llm_script: list[StubScriptStep] = field(default_factory=list)


@dataclass
class RunResult:
    query_id: str
    actual_outcome: Outcome
    actual_answer: str | None
    actual_tool_sequence: list[str]
    actual_denial_count: int
    actual_retrieved_doc_count: int
    expected: ExpectedOutcome
    raw_exception: str | None = None
    _failure_reason: str | None = field(default=None, init=False, repr=False)

    @property
    def passed(self) -> bool:
        return self._evaluate() is None

    @property
    def failure_reason(self) -> str | None:
        return self._evaluate()

    def _evaluate(self) -> str | None:
        # Outcome
        if self.actual_outcome != self.expected.outcome:
            return (f"outcome mismatch: expected "
                    f"{self.expected.outcome.value!r}, got "
                    f"{self.actual_outcome.value!r}")
        # answer_contains
        for needle in self.expected.answer_contains:
            if not self.actual_answer or needle not in self.actual_answer:
                return f"answer missing required substring: {needle!r}"
        # answer_excludes
        for needle in self.expected.answer_excludes:
            if self.actual_answer and needle in self.actual_answer:
                return (f"answer contains excluded substring: "
                        f"{needle!r}")
        # tool_sequence
        if self.expected.tool_sequence:
            if self.actual_tool_sequence != self.expected.tool_sequence:
                return (f"tool_sequence mismatch: expected "
                        f"{self.expected.tool_sequence!r}, got "
                        f"{self.actual_tool_sequence!r}")
        # min_denial_records
        if self.actual_denial_count < self.expected.min_denial_records:
            return (f"too few denial records: expected "
                    f">= {self.expected.min_denial_records}, "
                    f"got {self.actual_denial_count}")
        # min_retrieved_docs
        if self.actual_retrieved_doc_count < self.expected.min_retrieved_docs:
            return (f"too few retrieved docs: expected "
                    f">= {self.expected.min_retrieved_docs}, "
                    f"got {self.actual_retrieved_doc_count}")
        return None
