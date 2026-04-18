"""Cross-tool integration tests using a stub LLM that scripts a
multi-hop tool sequence.

These tests verify that the graph plumbing correctly chains tools
together and accumulates audit state across hops. They do NOT
require Ollama (no live LLM); they verify the graph wiring under
controlled inputs.

Live-Ollama integration is the existing `test_basic_loop.py` and
the future Phase 6 evaluation harness.
"""

import datetime as dt
from typing import Any

from langchain_core.messages import AIMessage

from src.agent.graph import build_graph
from src.agent.state import initial_state
from src.agent.tools.get_approval_chain import (
    make_get_approval_chain_handler,
)
from src.agent.tools.lookup_employee import (
    make_lookup_employee_handler,
)
from src.agent.tools.registry import (
    ToolRegistry,
    make_search_documents_handler,
)
from src.data.loaders import Employee


# ---------- shared fixtures (no pytest fixture decorator since these
# helpers don't need to share state across tests) -----------------------

def _emp(eid: str, *, manager: str | None = None,
         dept: str = "Engineering",
         title: str = "Software Engineer",
         clearance: int = 2) -> Employee:
    return Employee(
        employee_id=eid, name=f"Test {eid}", title=title,
        department=dept, manager_id=manager,
        clearance_level=clearance, location="Remote",
        hire_date=dt.date(2024, 1, 1),
        email=f"{eid}@example.com",
        salary=100_000, is_active=True,
    )


def _org() -> dict[str, Employee]:
    """Minimal org sufficient to resolve a Director band."""
    return {
        "E001": _emp("E001", manager=None, dept="Executive",
                     title="Chief Executive Officer", clearance=4),
        "E002": _emp("E002", manager="E001", dept="Engineering",
                     title="VP of Engineering", clearance=4),
        "E003": _emp("E003", manager="E002", dept="Engineering",
                     title="Director of Engineering", clearance=3),
        "E004": _emp("E004", manager="E003", dept="Engineering",
                     title="Software Engineer"),
        "E007": _emp("E007", manager="E001", dept="Finance",
                     title="Chief Financial Officer", clearance=4),
    }


class _ScriptedLLM:
    """LLM stub that returns a scripted sequence of AIMessages."""

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)
        self.bind_tools_called_with: list[Any] = []

    def bind_tools(self, tools: list[Any]) -> "_ScriptedLLM":
        self.bind_tools_called_with = tools
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        if not self._responses:
            return AIMessage(content="(stub exhausted)")
        return self._responses.pop(0)


def _stub_retriever_returning(*doc_ids: str) -> Any:
    """Returns a Mock-like retriever whose .search returns one record
    per doc_id, all with stub content."""
    class _R:
        def search(self, *, query: str, user_id: str,
                   k: int = 5) -> list[dict[str, Any]]:
            return [
                {"doc_id": d, "content": f"content for {d}",
                 "metadata": {"classification": "INTERNAL"}}
                for d in doc_ids
            ]
    return _R()


# ---------- the tests -----------------------------------------------------

def test_search_then_lookup_then_approval_chain_in_sequence() -> None:
    """The agent calls search_documents, then lookup_employee, then
    get_approval_chain - three distinct tools in three hops, with one
    final synthesizing answer afterwards."""
    employees = _org()
    retriever = _stub_retriever_returning("expense_2026", "approval_matrix")

    handlers: ToolRegistry = {
        "search_documents": make_search_documents_handler(retriever),
        "lookup_employee": make_lookup_employee_handler(employees=employees),
        "get_approval_chain": make_get_approval_chain_handler(
            employees=employees,
        ),
    }

    llm = _ScriptedLLM([
        AIMessage(
            content="",
            tool_calls=[{
                "id": "t1", "name": "search_documents",
                "args": {"query": "vendor approval policy"},
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "t2", "name": "lookup_employee",
                "args": {"employee_id": "E004"},
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "t3", "name": "get_approval_chain",
                "args": {"employee_id": "E004", "amount_usd": 5000.0},
            }],
        ),
        AIMessage(
            content="Per the policy, E004's $5k expense routes to "
                    "the Director (E003).",
        ),
    ])

    graph = build_graph(llm=llm, handlers=handlers)
    state = initial_state(
        request_id="req-cross-1",
        user_id="E004",
        query="who approves my $5k vendor expense?",
        max_steps=20,
    )

    final = graph.invoke(state)

    # Three tool hops happened in order
    tool_names = [r["tool_name"] for r in final["tool_call_log"]]
    assert tool_names == [
        "search_documents", "lookup_employee", "get_approval_chain",
    ]

    # Step count == number of tool hops
    assert final["step_count"] == 3

    # All three calls succeeded
    assert all(r["status"] == "success" for r in final["tool_call_log"])

    # search_documents results accumulated to retrieved_doc_ids
    assert "expense_2026" in final["retrieved_doc_ids"]
    assert "approval_matrix" in final["retrieved_doc_ids"]

    # The final message is the LLM's synthesis (no tool_calls)
    last = final["messages"][-1]
    assert isinstance(last, AIMessage)
    assert last.tool_calls in (None, [])
    assert "Director" in last.content


def test_cross_tool_denial_does_not_break_the_loop() -> None:
    """If one tool in a sequence raises AccessDenied, the graph
    records the error and continues to the next LLM step. The LLM
    can then choose to escalate or answer differently."""
    employees = _org()
    retriever = _stub_retriever_returning("doc1")

    handlers: ToolRegistry = {
        "search_documents": make_search_documents_handler(retriever),
        "lookup_employee": make_lookup_employee_handler(employees=employees),
    }

    # E004 (Engineering IC) tries to look up E007 (Finance / CFO):
    # not in chain, not same dept, not HR -> denied
    llm = _ScriptedLLM([
        AIMessage(
            content="",
            tool_calls=[{
                "id": "t1", "name": "lookup_employee",
                "args": {"employee_id": "E007"},
            }],
        ),
        AIMessage(
            content="I cannot view E007's record; escalating.",
        ),
    ])

    graph = build_graph(llm=llm, handlers=handlers)
    state = initial_state(
        request_id="req-cross-2",
        user_id="E004",
        query="who is E007?",
        max_steps=20,
    )

    final = graph.invoke(state)

    # The denied tool call IS in the log, with status=DENIED
    # (AccessDenied is caught by AuthenticatedToolNode and recorded
    # as a security denial, distinct from operator errors)
    records = final["tool_call_log"]
    assert len(records) == 1
    assert records[0]["tool_name"] == "lookup_employee"
    assert records[0]["status"] == "denied"
    # The reason field carries the access_denied prefix
    assert "access_denied" in records[0]["reason"].lower()

    # The LLM was able to produce a final answer despite the denial
    assert final["messages"][-1].content == (
        "I cannot view E007's record; escalating."
    )
    assert final["step_count"] == 1


def test_llm_supplied_user_id_blocked_across_multiple_hops() -> None:
    """In a multi-tool sequence, every hop where the LLM tries to
    inject user_id must be detected and logged. State user_id must
    remain stable across the whole sequence."""
    employees = _org()
    retriever = _stub_retriever_returning("doc1")

    handlers: ToolRegistry = {
        "search_documents": make_search_documents_handler(retriever),
        "lookup_employee": make_lookup_employee_handler(employees=employees),
    }

    # Both tool calls smuggle user_id in args
    llm = _ScriptedLLM([
        AIMessage(
            content="",
            tool_calls=[{
                "id": "t1", "name": "search_documents",
                "args": {"query": "anything", "user_id": "E007"},
            }],
        ),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "t2", "name": "lookup_employee",
                "args": {"employee_id": "E001", "user_id": "E001"},
            }],
        ),
        AIMessage(content="Done."),
    ])

    graph = build_graph(llm=llm, handlers=handlers)
    state = initial_state(
        request_id="req-cross-3",
        user_id="E004",
        query="probe",
        max_steps=20,
    )

    final = graph.invoke(state)

    # Both hops have a denial record for the injection attempt.
    # The lookup_employee call also raises AccessDenied (E004 cannot view
    # E001) which is now recorded as a third denied record — correct behavior
    # after the AccessDenied → status=denied distinction landed.
    injection_denial_records = [
        r for r in final["tool_call_log"]
        if r["status"] == "denied"
        and r["reason"] == "llm_supplied_user_id_rejected"
    ]
    assert len(injection_denial_records) == 2
    assert all(
        r["reason"] == "llm_supplied_user_id_rejected"
        for r in injection_denial_records
    )

    # The state user_id is still E004 throughout
    assert final["user_id"] == "E004"
