"""Typed agent state and record shapes."""

from __future__ import annotations

from operator import add as _list_add
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages


class ToolCallRecord(TypedDict):
    step_index: int
    tool_name: str
    args_hash: str
    status: Literal["success", "error", "denied"]
    duration_ms: int


class SecurityVerdict(TypedDict):
    layer: str
    stage: Literal["entry", "in_graph", "exit"]
    verdict: Literal["pass", "block", "flag"]
    details: str | None


class AgentState(TypedDict):
    request_id: str
    user_id: str

    messages: Annotated[list[BaseMessage], add_messages]

    step_count: int
    max_steps: int

    tool_call_log: Annotated[list[ToolCallRecord], _list_add]
    security_verdicts: Annotated[list[SecurityVerdict], _list_add]
    retrieved_doc_ids: Annotated[list[str], _list_add]

    final_answer: str | None
    termination_reason: str | None


def initial_state(
    *,
    request_id: str,
    user_id: str,
    query: str,
    max_steps: int,
    seed_verdicts: list[SecurityVerdict] | None = None,
) -> AgentState:
    """Construct an initial `AgentState` with the human query seeded
    as the first message."""
    return AgentState(
        request_id=request_id,
        user_id=user_id,
        messages=[HumanMessage(content=query)],
        step_count=0,
        max_steps=max_steps,
        tool_call_log=[],
        security_verdicts=list(seed_verdicts or []),
        retrieved_doc_ids=[],
        final_answer=None,
        termination_reason=None,
    )
