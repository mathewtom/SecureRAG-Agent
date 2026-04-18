"""Eval runner — drives AgenticChain per query in stub or live mode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from eval.schema import (
    Outcome,
    Query,
    RunResult,
    StubScriptStep,
)


def run_one_query(
    query: Query,
    *,
    mode: str = "stub",
    logs_dir: Path | None = None,
) -> RunResult:
    """Run one query through an AgenticChain. Returns a RunResult.

    mode="stub": LLM is a scripted sequence from query.stub_llm_script;
    no Ollama required.

    mode="live": uses src.api._build_chain() — requires Ollama and
    populated ChromaDB. Behavior identical to the running service.
    """
    if mode == "stub":
        chain = _build_stub_chain(query, logs_dir=logs_dir)
    elif mode == "live":
        chain = _build_live_chain()
    else:
        raise ValueError(f"unknown mode: {mode!r}")

    return _execute(query, chain)


def _build_stub_chain(query: Query, *, logs_dir: Path | None) -> Any:
    """Build a chain with a per-query scripted LLM and stub tool
    handlers. Inline imports keep eval/ from importing the full agent
    stack at module load time."""
    from src.agent.audit_sink import AuditSink
    from src.agent.graph import build_graph
    from src.agent.tools.registry import (
        ToolRegistry,
        make_search_documents_handler,
    )
    from src.agent.wrapper import AgenticChain

    llm = _ScriptedLLM(query.stub_llm_script)

    # Stub retriever returns one fake doc per call so the tool always
    # produces something. Per-query realism comes in Task O when
    # specific doc ids are needed.
    retriever = MagicMock()
    retriever.search.return_value = [
        {"doc_id": f"stub_doc_{query.id}",
         "content": "stub content",
         "metadata": {"classification": "INTERNAL"}},
    ]

    handlers: ToolRegistry = {
        "search_documents": make_search_documents_handler(retriever),
    }

    audit = MagicMock()
    request_id = f"eval-{query.id}"
    audit.new_request_id.return_value = request_id

    effective_logs_dir = logs_dir or Path("logs")
    sink = AuditSink(logs_dir=effective_logs_dir)

    graph = build_graph(
        llm=llm, handlers=handlers, audit=audit, audit_sink=sink,
    )
    return AgenticChain(
        graph=graph,
        rate_limiter=MagicMock(),
        input_scanners=[],
        output_scanners=[],
        audit=audit,
        extract_answer=_extract_answer,
        audit_sink=sink,
    )


def _build_live_chain() -> Any:
    from src.api import _build_chain
    return _build_chain()


def _execute(query: Query, chain: Any) -> RunResult:
    from src.exceptions import (
        AccessDenied,
        BudgetExhausted,
        OutputFlagged,
        QueryBlocked,
    )
    from src.rate_limiter import RateLimitExceeded

    actual_answer: str | None = None
    actual_outcome: Outcome
    raw_exception: str | None = None

    try:
        result = chain.invoke(query=query.query, user_id=query.user_id)
        actual_outcome = Outcome.ANSWERED
        actual_answer = result["answer"]
    except RateLimitExceeded:
        actual_outcome = Outcome.RATE_LIMITED
    except QueryBlocked as e:
        actual_outcome = Outcome.BLOCKED
        raw_exception = str(e)
    except OutputFlagged as e:
        actual_outcome = Outcome.FLAGGED
        raw_exception = str(e)
    except BudgetExhausted as e:
        actual_outcome = Outcome.BUDGET_EXHAUSTED
        raw_exception = str(e)
    except AccessDenied as e:
        actual_outcome = Outcome.ERROR
        raw_exception = f"AccessDenied: {e}"
    except Exception as e:  # noqa: BLE001
        actual_outcome = Outcome.ERROR
        raw_exception = f"{type(e).__name__}: {e}"

    # Reconstruct tool sequence + counts from the audit sink for
    # this request_id.
    tool_seq, denial_count, retrieved_count = _read_audit_for_request(
        chain, query,
    )

    return RunResult(
        query_id=query.id,
        actual_outcome=actual_outcome,
        actual_answer=actual_answer,
        actual_tool_sequence=tool_seq,
        actual_denial_count=denial_count,
        actual_retrieved_doc_count=retrieved_count,
        expected=query.expected,
        raw_exception=raw_exception,
    )


def _read_audit_for_request(
    chain: Any, query: Query,
) -> tuple[list[str], int, int]:
    """Re-read the audit sink to extract per-request observables.

    For stub mode the request_id is deterministic ("eval-<query.id>").
    Live mode is harder — for now, return empty lists; live-mode
    detail extraction is Task O work.
    """
    sink = getattr(chain, "_audit_sink", None)
    if sink is None:
        return [], 0, 0

    request_id = f"eval-{query.id}"
    try:
        text = sink.log_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], 0, 0

    tool_seq: list[str] = []
    denial_count = 0
    retrieved_count = 0

    for raw in text.splitlines():
        if not raw.strip():
            continue
        evt = json.loads(raw)
        if evt.get("request_id") != request_id:
            continue
        if evt.get("event") == "tool_call":
            status = evt.get("status")
            if status == "success":
                tool_seq.append(evt["tool_name"])
                # Stub retriever always returns 1 doc per
                # search_documents call
                if evt["tool_name"] == "search_documents":
                    retrieved_count += 1
            elif status == "denied":
                denial_count += 1
    return tool_seq, denial_count, retrieved_count


def _extract_answer(final_state: dict[str, Any]) -> str:
    from langchain_core.messages import AIMessage as _AI
    for msg in reversed(final_state["messages"]):
        if isinstance(msg, _AI) and msg.content:
            return str(msg.content)
    return ""


class _ScriptedLLM:
    """Scripted LLM that replays StubScriptStep responses in order."""

    def __init__(self, script: list[StubScriptStep]) -> None:
        self._steps = list(script)

    def bind_tools(self, tools: list[Any]) -> "_ScriptedLLM":
        # Tool binding is a no-op in stub mode; we ignore the tool
        # schema and just replay scripted responses.
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        if not self._steps:
            return AIMessage(content="(stub exhausted)")
        step = self._steps.pop(0)
        return AIMessage(content=step.content, tool_calls=step.tool_calls)
