"""Tests for AuthenticatedToolNode.

These tests are load-bearing for ARCHITECTURE.md §1: the LLM must
not be able to set user_id. If these tests go green, that property
holds mechanically. If they go red, the whole security story falls
apart.
"""

from unittest.mock import Mock

from langchain_core.messages import AIMessage, ToolMessage

from securerag_agent.agent.graph import AuthenticatedToolNode
from securerag_agent.agent.state import initial_state
from securerag_agent.agent.tools.registry import make_search_documents_handler


def _node_with_retriever(retriever: Mock, audit: Mock | None = None) -> AuthenticatedToolNode:
    handlers = {"search_documents": make_search_documents_handler(retriever)}
    return AuthenticatedToolNode(handlers=handlers, audit=audit)


def _state_with_tool_call(user_id: str, tool_args: dict) -> dict:
    state = initial_state(
        request_id="r1", user_id=user_id, query="q", max_steps=20,
    )
    state["messages"].append(
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_1",
                "name": "search_documents",
                "args": tool_args,
            }],
        )
    )
    return state


def test_user_id_from_state_reaches_tool():
    retriever = Mock()
    retriever.search.return_value = [{"doc_id": "d1", "content": "hi",
                                      "metadata": {}}]

    node = _node_with_retriever(retriever)
    state = _state_with_tool_call(
        user_id="E003",
        tool_args={"query": "vacation policy"},
    )

    node(state)

    retriever.search.assert_called_once()
    assert retriever.search.call_args.kwargs["user_id"] == "E003"
    assert retriever.search.call_args.kwargs["query"] == "vacation policy"


def test_llm_supplied_user_id_is_rejected_and_logged():
    retriever = Mock()
    retriever.search.return_value = []

    node = _node_with_retriever(retriever)
    state = _state_with_tool_call(
        user_id="E003",
        tool_args={"query": "salaries", "user_id": "E012"},  # LLM forgery
    )

    out = node(state)

    # Tool STILL ran - with the state's user_id, not the LLM's
    assert retriever.search.call_args.kwargs["user_id"] == "E003"
    # And a denial record was added to tool_call_log
    denial_records = [r for r in out["tool_call_log"]
                      if r["status"] == "denied"]
    assert len(denial_records) == 1
    assert denial_records[0]["tool_name"] == "search_documents"


def test_step_count_incremented():
    retriever = Mock()
    retriever.search.return_value = []

    node = _node_with_retriever(retriever)
    state = _state_with_tool_call("E003", {"query": "q"})
    state["step_count"] = 5

    out = node(state)
    assert out["step_count"] == 6


def test_tool_message_appended_with_correct_call_id():
    retriever = Mock()
    retriever.search.return_value = [{"doc_id": "d1", "content": "hi",
                                      "metadata": {}}]

    node = _node_with_retriever(retriever)
    state = _state_with_tool_call("E003", {"query": "q"})

    out = node(state)

    assert len(out["messages"]) == 1
    msg = out["messages"][0]
    assert isinstance(msg, ToolMessage)
    assert msg.tool_call_id == "call_1"


def test_retrieved_doc_ids_accumulated():
    retriever = Mock()
    retriever.search.return_value = [
        {"doc_id": "d1", "content": "a", "metadata": {}},
        {"doc_id": "d2", "content": "b", "metadata": {}},
    ]

    node = _node_with_retriever(retriever)
    state = _state_with_tool_call("E003", {"query": "q"})

    out = node(state)
    assert out["retrieved_doc_ids"] == ["d1", "d2"]


def test_budget_exhaustion_sets_termination_reason():
    retriever = Mock()
    retriever.search.return_value = []

    node = _node_with_retriever(retriever)
    state = _state_with_tool_call("E003", {"query": "q"})
    state["step_count"] = 19   # next step will be 20 == max_steps
    state["max_steps"] = 20

    out = node(state)
    assert out["termination_reason"] == "budget_exhausted"


def test_unknown_tool_records_error_not_crash():
    retriever = Mock()

    node = _node_with_retriever(retriever)
    state = initial_state(
        request_id="r", user_id="E003", query="q", max_steps=20,
    )
    state["messages"].append(
        AIMessage(content="", tool_calls=[{
            "id": "call_x",
            "name": "delete_all_data",  # not a registered tool
            "args": {},
        }])
    )

    out = node(state)

    error_records = [r for r in out["tool_call_log"]
                     if r["status"] == "error"]
    assert len(error_records) == 1
    retriever.search.assert_not_called()


def test_case_variant_user_id_also_rejected():
    """USER_ID, User_Id, etc. should also be stripped and logged."""
    retriever = Mock()
    retriever.search.return_value = []

    node = _node_with_retriever(retriever)
    state = _state_with_tool_call(
        user_id="E003",
        tool_args={"query": "salaries", "USER_ID": "E012"},
    )

    out = node(state)

    # The case-variant must be detected as an injection attempt
    denial_records = [r for r in out["tool_call_log"]
                      if r["status"] == "denied"]
    assert len(denial_records) == 1
    # And the trusted state user_id was used
    assert retriever.search.call_args.kwargs["user_id"] == "E003"


def test_multiple_tool_calls_in_one_step():
    """LangGraph can deliver multiple tool calls in one AIMessage; all
    of them should run, each producing its own ToolMessage and audit
    record, with retrieved_doc_ids accumulated across them."""
    retriever = Mock()
    retriever.search.side_effect = [
        [{"doc_id": "d1", "content": "a", "metadata": {}}],
        [{"doc_id": "d2", "content": "b", "metadata": {}}],
    ]

    node = _node_with_retriever(retriever)
    state = initial_state(
        request_id="r", user_id="E003", query="q", max_steps=20,
    )
    state["messages"].append(
        AIMessage(content="", tool_calls=[
            {"id": "call_a", "name": "search_documents",
             "args": {"query": "first"}},
            {"id": "call_b", "name": "search_documents",
             "args": {"query": "second"}},
        ])
    )

    out = node(state)

    assert len(out["messages"]) == 2
    assert {m.tool_call_id for m in out["messages"]} == {"call_a", "call_b"}
    assert out["retrieved_doc_ids"] == ["d1", "d2"]
    assert len(out["tool_call_log"]) == 2
    # step_count increments by 1 per node call, regardless of how many
    # tool calls were inside it
    assert out["step_count"] == 1


def test_in_graph_denial_calls_audit_log_denial() -> None:
    """User_id override attempts must emit a log_denial entry, not just a state record."""
    retriever = Mock()
    retriever.search.return_value = []
    audit_mock = Mock()

    node = _node_with_retriever(retriever, audit=audit_mock)
    state = _state_with_tool_call(
        user_id="E003",
        tool_args={"query": "salaries", "user_id": "E012"},
    )

    node(state)

    # log_denial was called with the override reason
    assert audit_mock.log_denial.called
    call = audit_mock.log_denial.call_args
    # Either kwargs or positional - check both shapes
    all_args = list(call.args) + list(call.kwargs.values())
    assert any("llm_supplied_user_id_rejected" in str(a) for a in all_args)


def test_unknown_tool_error_calls_audit_log_denial() -> None:
    """Tool invocation errors must also emit log_denial."""
    retriever = Mock()
    audit_mock = Mock()

    node = _node_with_retriever(retriever, audit=audit_mock)
    state = initial_state(
        request_id="r", user_id="E003", query="q", max_steps=20,
    )
    state["messages"].append(
        AIMessage(content="", tool_calls=[{
            "id": "call_x",
            "name": "delete_all_data",
            "args": {},
        }])
    )

    node(state)

    assert audit_mock.log_denial.called
    call = audit_mock.log_denial.call_args
    all_args = list(call.args) + list(call.kwargs.values())
    assert any("tool_invocation_failed" in str(a) for a in all_args)


def test_audit_optional_no_crash_when_omitted() -> None:
    """The audit kwarg must remain optional so existing tests don't break."""
    retriever = Mock()
    retriever.search.return_value = []

    # No audit passed
    node = _node_with_retriever(retriever)
    state = _state_with_tool_call(
        user_id="E003",
        tool_args={"query": "q", "user_id": "E012"},
    )

    out = node(state)  # Must not raise
    assert any(r["status"] == "denied" for r in out["tool_call_log"])


# ---------- Phase 4: AuditSink integration --------------------------------

def test_audit_sink_receives_event_per_tool_call(tmp_path):
    """Every successful tool call emits one sink event."""
    import json

    from securerag_agent.agent.audit_sink import AuditSink

    retriever = Mock()
    retriever.search.return_value = [
        {"doc_id": "d1", "content": "hi", "metadata": {}},
    ]
    sink = AuditSink(logs_dir=tmp_path)
    handlers = {"search_documents": (
        lambda args, *, user_id: retriever.search(
            query=args["query"], user_id=user_id,
        )
    )}

    node = AuthenticatedToolNode(handlers=handlers, audit_sink=sink)
    state = _state_with_tool_call("E003", {"query": "q"})

    node(state)

    lines = sink.log_path().read_text().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "tool_call"
    assert event["status"] == "success"
    assert event["tool_name"] == "search_documents"
    assert event["request_id"] == "r1"
    assert event["user_id"] == "E003"


def test_access_denied_recorded_as_denied_not_error(tmp_path):
    """A handler raising AccessDenied is recorded with status=denied,
    not status=error. This separates security events from operator errors."""
    import json

    from securerag_agent.agent.audit_sink import AuditSink
    from securerag_agent.exceptions import AccessDenied

    sink = AuditSink(logs_dir=tmp_path)
    handlers = {"search_documents": (
        lambda args, *, user_id: (_ for _ in ()).throw(
            AccessDenied(f"unauthorized: {user_id}"),
        )
    )}

    node = AuthenticatedToolNode(handlers=handlers, audit_sink=sink)
    state = _state_with_tool_call("E003", {"query": "q"})

    out = node(state)

    assert out["tool_call_log"][0]["status"] == "denied"
    assert "access_denied" in out["tool_call_log"][0]["reason"].lower()

    event = json.loads(sink.log_path().read_text().strip())
    assert event["status"] == "denied"


def test_other_exceptions_still_recorded_as_error(tmp_path):
    """A handler raising ValueError (or any non-AccessDenied) keeps
    status=error so operator errors are still distinguishable."""
    from securerag_agent.agent.audit_sink import AuditSink

    sink = AuditSink(logs_dir=tmp_path)
    handlers = {"search_documents": (
        lambda args, *, user_id: (_ for _ in ()).throw(
            ValueError("bad input"),
        )
    )}

    node = AuthenticatedToolNode(handlers=handlers, audit_sink=sink)
    state = _state_with_tool_call("E003", {"query": "q"})

    out = node(state)

    assert out["tool_call_log"][0]["status"] == "error"
    assert "ValueError" in out["tool_call_log"][0]["reason"]


def test_llm_user_id_injection_emits_separate_denial_event(tmp_path):
    """LLM-supplied user_id rejection emits its own sink event with
    status=denied AND the subsequent (cleansed) tool call also emits
    its own event."""
    import json

    from securerag_agent.agent.audit_sink import AuditSink

    sink = AuditSink(logs_dir=tmp_path)
    retriever_mock = Mock()
    retriever_mock.search.return_value = []
    handlers = {"search_documents": (
        lambda args, *, user_id: retriever_mock.search(
            query=args["query"], user_id=user_id,
        )
    )}

    node = AuthenticatedToolNode(handlers=handlers, audit_sink=sink)
    state = _state_with_tool_call(
        "E003", {"query": "q", "user_id": "E007"},
    )

    node(state)

    events = [json.loads(line)
              for line in sink.log_path().read_text().splitlines()]
    # Two events: the denial of the user_id smuggling, plus the
    # actual tool call (which proceeded with the trusted user_id)
    assert len(events) == 2
    statuses = [e["status"] for e in events]
    assert "denied" in statuses
    assert "success" in statuses
    denial_event = next(e for e in events if e["status"] == "denied")
    assert denial_event["reason"] == "llm_supplied_user_id_rejected"
