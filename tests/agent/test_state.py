"""Tests for AgentState and helpers."""

from langchain_core.messages import HumanMessage

from securerag_agent.agent.state import (
    SecurityVerdict,
    ToolCallRecord,
    initial_state,
)


def test_initial_state_minimal_fields():
    state = initial_state(
        request_id="r1",
        user_id="E003",
        query="hello",
        max_steps=20,
    )
    assert state["request_id"] == "r1"
    assert state["user_id"] == "E003"
    assert state["step_count"] == 0
    assert state["max_steps"] == 20
    assert state["tool_call_log"] == []
    assert state["security_verdicts"] == []
    assert state["retrieved_doc_ids"] == []
    assert state["final_answer"] is None
    assert state["termination_reason"] is None


def test_initial_state_seeds_messages_with_human_query():
    state = initial_state(
        request_id="r1",
        user_id="E003",
        query="what is the vacation policy?",
        max_steps=20,
    )
    assert len(state["messages"]) == 1
    msg = state["messages"][0]
    assert isinstance(msg, HumanMessage)
    assert msg.content == "what is the vacation policy?"


def test_initial_state_accepts_seed_verdicts():
    verdict: SecurityVerdict = {
        "layer": "injection_scan",
        "stage": "entry",
        "verdict": "pass",
        "details": None,
    }
    state = initial_state(
        request_id="r1",
        user_id="E003",
        query="hi",
        max_steps=20,
        seed_verdicts=[verdict],
    )
    assert state["security_verdicts"] == [verdict]


def test_tool_call_record_shape():
    record: ToolCallRecord = {
        "step_index": 0,
        "tool_name": "search_documents",
        "args_sha256": "abc123",
        "status": "success",
        "duration_ms": 42,
        "reason": None,
    }
    assert record["status"] == "success"


def test_agent_state_is_a_mapping():
    state = initial_state(
        request_id="r", user_id="E003", query="q", max_steps=20,
    )
    assert "messages" in state
    assert "user_id" in state
