"""Phase 4 audit-trail integrity tests.

Verifies, end-to-end:
- Every tool call produces an audit event in the sink file
- Events are ordered by ts within a request
- The file is append-only (re-running adds, doesn't truncate)
- request_start + request_end bracket the tool-call events of a request
- Multiple requests are interleaved correctly by request_id
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from securerag_agent.agent.audit_sink import AuditSink
from securerag_agent.agent.graph import build_graph
from securerag_agent.agent.wrapper import AgenticChain
from securerag_agent.agent.tools.registry import (
    ToolRegistry, make_search_documents_handler,
)


class _ScriptedLLM:
    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)

    def bind_tools(self, tools: list[Any]) -> "_ScriptedLLM":
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        return self._responses.pop(0) if self._responses \
            else AIMessage(content="(stub exhausted)")


def _build_chain_for_test(sink: AuditSink, llm: _ScriptedLLM) -> AgenticChain:
    retriever = MagicMock()
    retriever.search.return_value = [
        {"doc_id": "doc1", "content": "x", "metadata": {}},
    ]
    handlers: ToolRegistry = {
        "search_documents": make_search_documents_handler(retriever),
    }
    audit = MagicMock()
    audit.new_request_id.side_effect = ["req-001", "req-002", "req-003"]

    graph = build_graph(
        llm=llm, handlers=handlers, audit=audit, audit_sink=sink,
    )
    return AgenticChain(
        graph=graph,
        rate_limiter=MagicMock(),
        input_scanners=[],
        output_scanners=[],
        audit=audit,
        extract_answer=lambda s: "stub-answer",
        audit_sink=sink,
    )


def _scripted_search_then_answer() -> _ScriptedLLM:
    return _ScriptedLLM([
        AIMessage(
            content="",
            tool_calls=[{
                "id": "t1", "name": "search_documents",
                "args": {"query": "q"},
            }],
        ),
        AIMessage(content="Done."),
    ])


# ---------- per-request integrity -----------------------------------------

def test_one_request_produces_start_toolcall_end(tmp_path: Path):
    sink = AuditSink(logs_dir=tmp_path)
    chain = _build_chain_for_test(sink, _scripted_search_then_answer())

    chain.invoke(query="q", user_id="E003")

    events = [json.loads(line)
              for line in sink.log_path().read_text().splitlines()]
    # Exactly: request_start, tool_call, request_end (in that order)
    assert [e["event"] for e in events] == [
        "request_start", "tool_call", "request_end",
    ]
    # All three share the same request_id
    rids = {e["request_id"] for e in events}
    assert rids == {"req-001"}


def test_events_ordered_by_ts_within_a_request(tmp_path: Path):
    sink = AuditSink(logs_dir=tmp_path)
    chain = _build_chain_for_test(sink, _scripted_search_then_answer())

    chain.invoke(query="q", user_id="E003")

    events = [json.loads(line)
              for line in sink.log_path().read_text().splitlines()]
    timestamps = [e["ts"] for e in events]
    assert timestamps == sorted(timestamps)


# ---------- multi-request integrity ---------------------------------------

def test_multiple_requests_keep_separate_request_ids(tmp_path: Path):
    sink = AuditSink(logs_dir=tmp_path)
    llm = _ScriptedLLM([
        # Request 1
        AIMessage(content="", tool_calls=[{
            "id": "t1", "name": "search_documents",
            "args": {"query": "q1"},
        }]),
        AIMessage(content="A1"),
        # Request 2
        AIMessage(content="", tool_calls=[{
            "id": "t2", "name": "search_documents",
            "args": {"query": "q2"},
        }]),
        AIMessage(content="A2"),
    ])
    chain = _build_chain_for_test(sink, llm)

    chain.invoke(query="q1", user_id="E003")
    chain.invoke(query="q2", user_id="E004")

    events = [json.loads(line)
              for line in sink.log_path().read_text().splitlines()]
    # 6 events total: 3 per request
    assert len(events) == 6
    rids = [e["request_id"] for e in events]
    assert rids[:3] == ["req-001", "req-001", "req-001"]
    assert rids[3:] == ["req-002", "req-002", "req-002"]


# ---------- append-only ---------------------------------------------------

def test_re_invoking_appends_does_not_truncate(tmp_path: Path):
    sink_1 = AuditSink(logs_dir=tmp_path)
    chain_1 = _build_chain_for_test(sink_1,
                                    _scripted_search_then_answer())
    chain_1.invoke(query="q", user_id="E003")

    line_count_after_first = len(
        sink_1.log_path().read_text().splitlines()
    )

    # Fresh chain instance, same logs dir
    sink_2 = AuditSink(logs_dir=tmp_path)
    chain_2 = _build_chain_for_test(sink_2,
                                    _scripted_search_then_answer())
    chain_2.invoke(query="q", user_id="E003")

    line_count_after_second = len(
        sink_2.log_path().read_text().splitlines()
    )

    assert line_count_after_second == 2 * line_count_after_first


# ---------- query content protection --------------------------------------

def test_raw_query_not_in_audit_log(tmp_path: Path):
    sink = AuditSink(logs_dir=tmp_path)
    chain = _build_chain_for_test(sink, _scripted_search_then_answer())

    secret_query = "what is the CEO salary"
    chain.invoke(query=secret_query, user_id="E003")

    log_text = sink.log_path().read_text()
    # The raw query MUST NOT appear in the audit log
    assert "CEO salary" not in log_text
    assert "what is the" not in log_text
    # But query_sha256 should be present in request_start
    assert "query_sha256" in log_text
