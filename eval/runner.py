"""Eval runner — drives AgenticChain per query in stub or live mode."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4
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

    mode="live": uses securerag_agent.api._build_chain() — requires Ollama and
    populated ChromaDB. Behavior identical to the running service.
    """
    # Pre-generate a stable request_id so audit attribution is exact
    # regardless of whether the chain raised before building a result dict.
    request_id = f"eval-{query.id}-{uuid4().hex[:8]}"

    if mode == "stub":
        chain = _build_stub_chain(query, logs_dir=logs_dir,
                                  request_id=request_id)
        return _execute(query, chain, request_id=request_id)
    elif mode == "live":
        return _execute_live(query, request_id=request_id,
                             logs_dir=logs_dir)
    else:
        raise ValueError(f"unknown mode: {mode!r}")


def _build_stub_chain(
    query: Query,
    *,
    logs_dir: Path | None,
    request_id: str,
) -> Any:
    """Build a chain with a per-query scripted LLM and stub tool
    handlers. Inline imports keep eval/ from importing the full agent
    stack at module load time."""
    from securerag_agent.agent.audit_sink import AuditSink
    from securerag_agent.agent.graph import build_graph
    from securerag_agent.agent.tools.escalate_to_human import (
        make_escalate_to_human_handler,
    )
    from securerag_agent.agent.tools.get_approval_chain import (
        make_get_approval_chain_handler,
    )
    from securerag_agent.agent.tools.get_ticket_detail import (
        make_get_ticket_detail_handler,
    )
    from securerag_agent.agent.tools.list_calendar_events import (
        make_list_calendar_events_handler,
    )
    from securerag_agent.agent.tools.list_my_tickets import (
        make_list_my_tickets_handler,
    )
    from securerag_agent.agent.tools.lookup_employee import (
        make_lookup_employee_handler,
    )
    from securerag_agent.agent.tools.registry import (
        ToolRegistry,
        make_search_documents_handler,
    )
    from securerag_agent.agent.wrapper import AgenticChain
    from securerag_agent.data.loaders import (
        load_calendar,
        load_employees,
        load_projects,
        load_tickets,
    )

    llm = _ScriptedLLM(query.stub_llm_script)

    # Stub retriever returns one fake doc per call so the tool always
    # produces something.
    retriever = MagicMock()
    retriever.search.return_value = [
        {"doc_id": f"stub_doc_{query.id}",
         "content": "stub content",
         "metadata": {"classification": "INTERNAL"}},
    ]

    # Register all 7 production tools, backed by real Phase-1 fixture
    # data. This lets the eval queries exercise the full handler
    # surface (authz, redaction, busy placeholders, etc.) without
    # mocking each handler individually.
    employees = {e.employee_id: e for e in load_employees()}
    tickets_list = load_tickets()
    tickets_by_id = {t.ticket_id: t for t in tickets_list}
    projects_by_id = {p.project_id: p for p in load_projects()}
    events_list = load_calendar()
    audit = MagicMock()

    handlers: ToolRegistry = {
        "search_documents": make_search_documents_handler(retriever),
        "lookup_employee": make_lookup_employee_handler(
            employees=employees,
        ),
        "get_approval_chain": make_get_approval_chain_handler(
            employees=employees,
        ),
        "list_my_tickets": make_list_my_tickets_handler(
            employees=employees, tickets=tickets_list,
        ),
        "get_ticket_detail": make_get_ticket_detail_handler(
            employees=employees, tickets=tickets_by_id,
            projects=projects_by_id,
        ),
        "list_calendar_events": make_list_calendar_events_handler(
            employees=employees, events=events_list,
        ),
        "escalate_to_human": make_escalate_to_human_handler(
            employees=employees, audit=audit,
        ),
    }

    # Wire the pre-generated request_id into the stub audit so that
    # every audit event emitted during this query carries the same id
    # we'll use to read the log back.
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


def _execute_live(
    query: Query,
    *,
    request_id: str,
    logs_dir: Path | None,
) -> RunResult:
    """Run against the live chain with a deterministic request_id.

    The live chain calls securerag_agent.audit.new_request_id() to mint its
    request_id. We temporarily replace that function with a lambda
    returning our pre-generated id, then restore it after the call.
    This gives us exact audit-log attribution even on exception paths.

    TODO: if _build_chain() itself calls new_request_id at construction
    time (rather than in .invoke()), move the monkey-patch up to wrap
    chain construction too. As of Phase 4, new_request_id is called
    inside AgenticChain.invoke(), so the current placement is correct.
    """
    import securerag_agent.audit as audit_module

    original = audit_module.new_request_id
    audit_module.new_request_id = lambda: request_id  # type: ignore[method-assign]
    try:
        from securerag_agent.api import _build_chain
        chain = _build_chain()
        return _execute(query, chain, request_id=request_id)
    finally:
        audit_module.new_request_id = original  # type: ignore[method-assign]


def _execute(query: Query, chain: Any, *, request_id: str) -> RunResult:
    from securerag_agent.exceptions import (
        AccessDenied,
        BudgetExhausted,
        OutputFlagged,
        QueryBlocked,
    )
    from securerag_agent.rate_limiter import RateLimitExceeded

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

    tool_seq, denial_count, retrieved_count = _read_audit_for_request(
        chain, request_id=request_id,
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
    chain: Any,
    *,
    request_id: str,
) -> tuple[list[str], int, int]:
    """Re-read the audit sink to extract per-request observables.

    The request_id is pre-generated by run_one_query and wired into
    the chain's audit object before invocation, so it matches exactly
    regardless of whether the call succeeded or raised.
    """
    sink = getattr(chain, "_audit_sink", None)
    if sink is None:
        return [], 0, 0

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
                # search_documents call.
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
