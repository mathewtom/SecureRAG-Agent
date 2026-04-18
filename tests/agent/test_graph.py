"""Tests for the ReAct graph topology."""

from typing import Any
from unittest.mock import Mock

from langchain_core.messages import AIMessage, HumanMessage

from src.agent.graph import build_graph
from src.agent.state import initial_state
from src.agent.tools.registry import make_search_documents_handler


def _handlers_for(retriever: Mock) -> dict[str, Any]:
    return {"search_documents": make_search_documents_handler(retriever)}


class StubLLM:
    """Callable that returns scripted AIMessages in sequence."""

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)
        self.bind_tools_called_with: list[Any] = []

    def bind_tools(self, tools: list[Any]) -> "StubLLM":
        self.bind_tools_called_with = tools
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        return self._responses.pop(0)


def test_direct_answer_no_tool_call_terminates():
    llm = StubLLM([AIMessage(content="The answer is 42.")])
    retriever = Mock()
    graph = build_graph(llm=llm, handlers=_handlers_for(retriever))

    state = initial_state(
        request_id="r", user_id="E003", query="what is the answer?",
        max_steps=20,
    )
    final = graph.invoke(state)

    assert final["messages"][-1].content == "The answer is 42."
    retriever.search.assert_not_called()
    assert final["step_count"] == 0


def test_single_tool_call_then_answer():
    retriever = Mock()
    retriever.search.return_value = [
        {"doc_id": "expense_2026", "content": "parking $250/month",
         "metadata": {"classification": "INTERNAL"}},
    ]
    llm = StubLLM([
        AIMessage(
            content="",
            tool_calls=[{
                "id": "t1",
                "name": "search_documents",
                "args": {"query": "parking reimbursement"},
            }],
        ),
        AIMessage(content="Parking reimbursement is $250/month."),
    ])
    graph = build_graph(llm=llm, handlers=_handlers_for(retriever))

    state = initial_state(
        request_id="r", user_id="E003",
        query="what is the parking reimbursement?", max_steps=20,
    )
    final = graph.invoke(state)

    assert final["step_count"] == 1
    assert final["retrieved_doc_ids"] == ["expense_2026"]
    assert "$250/month" in final["messages"][-1].content


def test_budget_exhaustion_terminates_loop():
    retriever = Mock()
    retriever.search.return_value = []

    class LoopingLLM(StubLLM):
        def invoke(self, messages: list[Any]) -> AIMessage:
            return AIMessage(
                content="",
                tool_calls=[{
                    "id": f"t_{id(object())}",
                    "name": "search_documents",
                    "args": {"query": "x"},
                }],
            )

    llm = LoopingLLM([])
    graph = build_graph(llm=llm, handlers=_handlers_for(retriever))

    state = initial_state(
        request_id="r", user_id="E003", query="q", max_steps=3,
    )
    final = graph.invoke(state, config={"recursion_limit": 50})

    assert final["step_count"] == 3
    assert final["termination_reason"] == "budget_exhausted"


def test_bind_tools_called_with_search_documents():
    llm = StubLLM([AIMessage(content="done")])
    retriever = Mock()
    build_graph(llm=llm, handlers=_handlers_for(retriever))

    names = [t.name for t in llm.bind_tools_called_with]
    assert names == ["search_documents"]


def test_system_prompt_prepended_to_messages_for_llm():
    """The agent_llm node should prepend SYSTEM_PROMPT before invoking the LLM."""
    captured: list[list[Any]] = []

    class CapturingLLM(StubLLM):
        def invoke(self, messages: list[Any]) -> AIMessage:
            captured.append(messages)
            return AIMessage(content="done")

    llm = CapturingLLM([])
    retriever = Mock()
    graph = build_graph(llm=llm, handlers=_handlers_for(retriever))

    state = initial_state(
        request_id="r", user_id="E003", query="hi", max_steps=20,
    )
    graph.invoke(state)

    from langchain_core.messages import SystemMessage
    from src.agent.prompts import SYSTEM_PROMPT
    assert len(captured) == 1
    sent = captured[0]
    assert isinstance(sent[0], SystemMessage)
    assert sent[0].content == SYSTEM_PROMPT
    assert isinstance(sent[1], HumanMessage)
    assert sent[1].content == "hi"
