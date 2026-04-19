"""LangGraph building blocks for the agent.

Phase 2 scope: AuthenticatedToolNode (this file) plus the ReAct
state-graph wiring (added in Task 8).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from src.agent.prompts import SYSTEM_PROMPT, build_system_prompt
from src.agent.state import AgentState, ToolCallRecord, ToolStatus
from src.exceptions import AccessDenied
from src.agent.tools import (
    escalate_to_human,
    get_approval_chain,
    get_ticket_detail,
    list_calendar_events,
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
        audit_sink: Any | None = None,
    ) -> None:
        self._handlers = handlers
        self._audit = audit
        self._audit_sink = audit_sink

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
                if self._audit_sink is not None:
                    self._audit_sink.emit({
                        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                        "event": "tool_call",
                        "request_id": state["request_id"],
                        "user_id": state["user_id"],
                        "tool_name": name,
                        "hop_index": next_step - 1,
                        "args_sha256": _args_hash(raw_args),
                        "status": ToolStatus.DENIED.value,
                        "duration_ms": 0,
                        "reason": "llm_supplied_user_id_rejected",
                    })
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
            except AccessDenied as e:
                status = ToolStatus.DENIED
                error_reason = f"access_denied: {e}"
                content = f"tool denied [AccessDenied]: {e}"
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
            if self._audit_sink is not None:
                self._audit_sink.emit({
                    "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                    "event": "tool_call",
                    "request_id": state["request_id"],
                    "user_id": state["user_id"],
                    "tool_name": name,
                    "hop_index": next_step - 1,
                    "args_sha256": _args_hash(raw_args),
                    "status": status.value,
                    "duration_ms": duration_ms,
                    "reason": error_reason,
                })

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

def build_graph(
    *,
    llm: Any,
    handlers: ToolRegistry,
    audit: Any | None = None,
    audit_sink: Any | None = None,
    employees: dict[str, Any] | None = None,
) -> Any:
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
    audit_sink
        Optional ``AuditSink`` instance. When provided, every tool call
        emits one structured JSONL event with status, duration, and a
        SHA-256 hash of the arguments.

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
        list_calendar_events,
        escalate_to_human,
    ])

    def agent_llm_node(state: AgentState) -> dict[str, Any]:
        caller_record = _caller_record(employees, state["user_id"])
        messages = _prepend_system(
            state["messages"], state["user_id"],
            caller=caller_record,
        )
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    tools_node = AuthenticatedToolNode(
        handlers=handlers, audit=audit, audit_sink=audit_sink,
    )

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


def _prepend_system(
    messages: list[Any],
    user_id: str,
    caller: dict[str, Any] | None = None,
) -> list[Any]:
    """Prepend a per-request system message that includes the caller's
    identity (and optional profile) so the LLM can resolve "me/my"
    without an extra lookup_employee call.

    Idempotent: if the first message is already a SystemMessage (e.g.,
    a resumed conversation), don't double-prepend.
    """
    if messages and isinstance(messages[0], SystemMessage):
        return messages
    prompt = build_system_prompt(user_id=user_id, caller=caller)
    return [SystemMessage(content=prompt), *messages]


def _caller_record(
    employees: dict[str, Any] | None, user_id: str,
) -> dict[str, Any] | None:
    """Resolve the caller's user_id to a serializable dict for prompt
    injection. Returns None if the directory isn't available (e.g., in
    tests that don't pass employees) — the prompt then falls back to
    user_id only.
    """
    if employees is None:
        return None
    emp = employees.get(user_id)
    if emp is None:
        return None
    # Support both Employee dataclasses and plain dicts.
    if hasattr(emp, "employee_id"):
        return {
            "employee_id": emp.employee_id,
            "name": emp.name,
            "title": emp.title,
            "department": emp.department,
            "manager_id": emp.manager_id,
            "location": emp.location,
        }
    return dict(emp)
