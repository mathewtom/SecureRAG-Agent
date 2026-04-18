"""LangGraph building blocks for the agent.

Phase 2 scope: AuthenticatedToolNode (this file) plus the ReAct
state-graph wiring (added in Task 8).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from src.agent.prompts import SYSTEM_PROMPT
from src.agent.state import AgentState, ToolCallRecord, ToolStatus
from src.agent.tools import (
    get_approval_chain,
    get_ticket_detail,
    list_my_tickets,
    lookup_employee,
    search_documents,
)
from src.agent.tools.registry import ToolRegistry


class AuthenticatedToolNode:
    """LangGraph node that dispatches LLM-requested tool calls through a
    handler registry with a runtime-injected `user_id`.

    Key invariant: the LLM CANNOT set `user_id`. Any `user_id` present
    in tool-call args is ignored; the call proceeds with
    `state["user_id"]` and a denial record is appended to
    `tool_call_log` tagged `status=ToolStatus.DENIED` and reason
    `"llm_supplied_user_id_rejected"`.
    """

    def __init__(
        self,
        *,
        handlers: ToolRegistry,
        audit: Any | None = None,
    ) -> None:
        self._handlers = handlers
        self._audit = audit

    def __call__(self, state: AgentState) -> dict[str, Any]:
        if not state["messages"]:
            return {}
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return {}

        new_messages: list[ToolMessage] = []
        new_records: list[ToolCallRecord] = []
        new_doc_ids: list[str] = []
        next_step = state["step_count"] + 1

        for tc in last.tool_calls:
            name = tc["name"]
            # Normalize to lowercase keys: protects against case-variant
            # identity injection like {"USER_ID": "E012"}, {"User_Id": ...}.
            raw_args = {
                k.lower(): v for k, v in (tc.get("args") or {}).items()
            }

            if "user_id" in raw_args:
                new_records.append(_denial_record(
                    step_index=next_step - 1,
                    tool_name=name,
                    args=raw_args,
                    reason="llm_supplied_user_id_rejected",
                ))
                if self._audit is not None:
                    self._audit.log_denial(
                        request_id=state["request_id"],
                        user_id=state["user_id"],
                        layer="authenticated_tool_node",
                        reason="llm_supplied_user_id_rejected",
                    )
                raw_args.pop("user_id")

            start = time.perf_counter()
            error_reason: str | None = None
            try:
                result = self._invoke(name, raw_args,
                                      user_id=state["user_id"])
                status = ToolStatus.SUCCESS
                doc_ids = [r["doc_id"] for r in result] \
                    if isinstance(result, list) else []
                new_doc_ids.extend(doc_ids)
                content = _serialize_result(result)
            except Exception as e:
                status = ToolStatus.ERROR
                content = f"tool error [{type(e).__name__}]: {e}"
                error_reason = f"{type(e).__name__}: {e}"
                if self._audit is not None:
                    self._audit.log_denial(
                        request_id=state["request_id"],
                        user_id=state["user_id"],
                        layer="authenticated_tool_node",
                        reason=f"tool_invocation_failed: {type(e).__name__}",
                    )
            duration_ms = int((time.perf_counter() - start) * 1000)

            new_messages.append(ToolMessage(
                content=content,
                tool_call_id=tc["id"],
            ))
            new_records.append(ToolCallRecord(
                step_index=next_step - 1,
                tool_name=name,
                args_sha256=_args_hash(raw_args),
                status=status,
                duration_ms=duration_ms,
                reason=None if status == ToolStatus.SUCCESS else error_reason,
            ))

        budget_exhausted = next_step >= state["max_steps"]
        update: dict[str, Any] = {
            "messages": new_messages,
            "tool_call_log": new_records,
            "retrieved_doc_ids": new_doc_ids,
            "step_count": next_step,
        }
        if budget_exhausted:
            update["termination_reason"] = "budget_exhausted"
        return update

    def _invoke(self, name: str, args: dict[str, Any], *, user_id: str) -> Any:
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(f"unknown tool: {name!r}")
        return handler(args, user_id=user_id)


def _args_hash(args: dict[str, Any]) -> str:
    payload = json.dumps(args, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _denial_record(
    *, step_index: int, tool_name: str, args: dict[str, Any], reason: str,
) -> ToolCallRecord:
    return ToolCallRecord(
        step_index=step_index,
        tool_name=tool_name,
        args_sha256=_args_hash(args),
        status=ToolStatus.DENIED,
        duration_ms=0,
        reason=reason,
    )


def _serialize_result(result: Any) -> str:
    if isinstance(result, list):
        return json.dumps(result, default=str)
    return str(result)


# ---------- ReAct graph wiring (Task 8) -----------------------------

def build_graph(*, llm: Any, handlers: ToolRegistry, audit: Any | None = None) -> Any:
    """Build the LangGraph ReAct state machine.

    Parameters
    ----------
    llm
        Any object implementing `bind_tools(list) -> self` and
        `invoke(messages) -> AIMessage`. Real code passes an
        Ollama-backed LangChain chat model; tests pass a stub.
    handlers
        Registry mapping tool name to its authorized handler callable.
        See ``src.agent.tools.registry`` for the handler protocol and
        factory functions.
    audit
        Optional audit module. When provided, denial and error events
        inside the tool node emit structured log entries via
        ``audit.log_denial``.

    The budget cap is not a graph parameter - the wrapper seeds
    `state["max_steps"]` and `AuthenticatedToolNode` enforces it by
    setting `termination_reason="budget_exhausted"` when the next step
    would cross the cap.
    """
    llm_with_tools = llm.bind_tools([
        search_documents,
        lookup_employee,
        get_approval_chain,
        list_my_tickets,
        get_ticket_detail,
    ])

    def agent_llm_node(state: AgentState) -> dict[str, Any]:
        messages = _prepend_system(state["messages"])
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    tools_node = AuthenticatedToolNode(handlers=handlers, audit=audit)

    graph: StateGraph[AgentState] = StateGraph(AgentState)
    graph.add_node("agent_llm", agent_llm_node)
    graph.add_node("tools", tools_node)
    graph.set_entry_point("agent_llm")
    graph.add_conditional_edges(
        "agent_llm", _route_after_llm,
        {"tools": "tools", "end": END},
    )
    graph.add_edge("tools", "agent_llm")
    return graph.compile()


def _route_after_llm(state: AgentState) -> str:
    if state.get("termination_reason") == "budget_exhausted":
        return "end"
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "end"


def _prepend_system(messages: list[Any]) -> list[Any]:
    if messages and isinstance(messages[0], SystemMessage):
        return messages
    return [SystemMessage(content=SYSTEM_PROMPT), *messages]
