# Phase 2 Implementation Plan — LangGraph Agent Framework

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a LangGraph-based ReAct agent end-to-end as the replacement for Sentinel's classical-RAG pipeline. Security primitives (scanners, rate limiter, audit log) wrap the graph at entry/exit. One placeholder tool (`search_documents`) with runtime-injected `user_id` proves the symbolic authz pattern Phase 3 will extend.

**Architecture:** Sibling `AgenticChain` class that runs entry scanners → `graph.invoke()` → exit scanners. The graph is a standard ReAct loop (`agent_llm` ↔ `tools`) with a 20-step budget cap. Tool invocation is intercepted by `AuthenticatedToolNode` which injects `user_id` from state rather than accepting it from LLM-generated tool-call arguments.

**Tech Stack:** Python 3.12+, LangGraph, LangChain-core (Document + Messages), ChromaDB, Ollama (local LLM), FastAPI, pytest, uv for dependency management.

**Reference spec:** [`docs/PHASE_2_DESIGN.md`](PHASE_2_DESIGN.md).

---

## Task 1: Relocate exceptions before deletion

Sentinel's `src/chain.py` defines `QueryBlocked` and `OutputFlagged` exceptions that downstream code (`src/api.py`) depends on. Before deleting `chain.py`, move them to a dedicated module so the new agent wrapper can raise them from the same symbol names.

**Files:**
- Create: `src/exceptions.py`
- Modify: `src/api.py` (update import)

- [ ] **Step 1: Inspect the exceptions**

Run: `grep -n "class QueryBlocked\|class OutputFlagged" src/chain.py`

Expected output: two lines showing the class definitions (verify they exist and note whether they inherit from `Exception` or a more specific base).

- [ ] **Step 2: Create `src/exceptions.py`**

```python
"""Shared exception types for SecureRAG-Agent.

Exceptions are imported by both the agent wrapper (which raises them)
and the API layer (which maps them to HTTP status codes). Keeping them
in a dedicated module avoids import cycles when the wrapper and API
both pull from a single chain module.
"""


class QueryBlocked(Exception):
    """Raised by an input-stage scanner that refuses to forward the
    query into the agent loop. Maps to HTTP 400."""


class OutputFlagged(Exception):
    """Raised by an output-stage scanner that refuses to forward the
    agent's answer to the caller. Maps to HTTP 422."""


class BudgetExhausted(Exception):
    """Raised when the agent graph hits its `max_steps` cap without
    emitting a final answer. Maps to HTTP 422."""

    def __init__(self, max_steps: int) -> None:
        super().__init__(f"agent budget of {max_steps} steps exceeded")
        self.max_steps = max_steps


class AccessDenied(Exception):
    """Raised by the retriever when the caller's user_id resolves to
    no known employee. Maps to HTTP 403."""
```

- [ ] **Step 3: Update `src/api.py` import**

Find the line in `src/api.py` that imports from `src.chain` (or `chain`) and change it to import from `src.exceptions` instead. Example:

```python
# Before:
# from src.chain import QueryBlocked, OutputFlagged

# After:
from src.exceptions import QueryBlocked, OutputFlagged
```

(The full `api.py` rewrite comes in Task 11; this edit is only to keep the import graph valid after Task 2's deletions.)

- [ ] **Step 4: Run existing tests to confirm nothing broke**

Run: `uv run pytest -q --ignore=tests/test_chain.py --ignore=tests/test_api.py`

Expected: existing unit tests for scanners, rate_limiter, audit, etc. still pass.

- [ ] **Step 5: Commit**

```bash
git add src/exceptions.py src/api.py
git commit -m "refactor: relocate agent exceptions to src/exceptions.py"
```

---

## Task 2: Delete Sentinel classical-RAG orchestration

Remove the classical single-shot pipeline and its data model. See [`docs/PHASE_2_DESIGN.md`](PHASE_2_DESIGN.md) §"Delete" for the rationale.

**Files:**
- Delete: `src/chain.py`, `src/pipeline.py`, `src/retrieval/` (whole dir), `src/loaders/` (whole dir)
- Delete: `data/raw/` (whole dir)
- Delete: `tests/test_chain.py`, `tests/test_api.py`, `tests/test_pipeline.py`, `tests/test_loader_factory.py`, `tests/test_access_controlled.py`
- Empty: `src/api.py` to a minimal FastAPI app (full rewrite in Task 11)

- [ ] **Step 1: List everything being deleted**

Run: `ls src/chain.py src/pipeline.py src/retrieval src/loaders data/raw 2>&1`

Expected: each path listed (none missing). If anything's missing, stop and ask.

- [ ] **Step 2: Delete the source directories**

```bash
git rm src/chain.py src/pipeline.py
git rm -r src/retrieval src/loaders
git rm -r data/raw
```

- [ ] **Step 3: Delete the tests that target deleted code**

```bash
git rm tests/test_chain.py tests/test_api.py tests/test_pipeline.py tests/test_loader_factory.py tests/test_access_controlled.py
```

If any of these don't exist, the `git rm` will fail — run `ls tests/` first and adjust the list to only include files that actually exist.

- [ ] **Step 4: Replace `src/api.py` with a minimal stub**

Overwrite `src/api.py` with:

```python
"""FastAPI application for SecureRAG-Agent.

This is a minimal stub during Phase 2 implementation. The full
/agent/query surface is wired in Task 11.
"""

from fastapi import FastAPI

app = FastAPI(title="SecureRAG-Agent")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 5: Run remaining tests**

Run: `uv run pytest -q`

Expected: scanner, rate_limiter, audit, credential_detector, sanitization_gate, embedding_detector, injection_scanner, classification_guard, classification_extractor, model_integrity, and Phase 1 dataset integrity tests all pass. No collection errors for the deleted test files.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: remove Sentinel classical-RAG orchestration and data/raw corpus"
```

---

## Task 3: Add langgraph dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (auto-generated)

- [ ] **Step 1: Add the dependency via uv**

Run: `uv add langgraph`

Expected: `pyproject.toml` gains a `langgraph` entry in `[project].dependencies`; `uv.lock` updates with langgraph and its transitive deps.

- [ ] **Step 2: Verify import**

Run: `uv run python -c "import langgraph; print(langgraph.__version__)"`

Expected: a version number prints (0.2.x or newer) with no import error.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add langgraph dependency for Phase 2 agent"
```

---

## Task 4: `AgentState` TypedDict + helpers (TDD)

Define the state shape the graph operates on. Test reducers before writing implementation.

**Files:**
- Create: `src/agent/__init__.py`
- Create: `src/agent/state.py`
- Create: `tests/agent/__init__.py`
- Create: `tests/agent/test_state.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_state.py`:

```python
"""Tests for AgentState and helpers."""

from langchain_core.messages import HumanMessage

from src.agent.state import (
    AgentState,
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
        "args_hash": "abc123",
        "status": "success",
        "duration_ms": 42,
    }
    assert record["status"] == "success"


def test_agent_state_is_a_mapping():
    state = initial_state(
        request_id="r", user_id="E003", query="q", max_steps=20,
    )
    assert "messages" in state
    assert "user_id" in state
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/agent/test_state.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent.state'`.

- [ ] **Step 3: Create `src/agent/__init__.py`**

Empty file:

```python
```

- [ ] **Step 4: Create `src/agent/state.py`**

```python
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
```

- [ ] **Step 5: Create test package marker**

Create `tests/agent/__init__.py` (empty file):

```python
```

- [ ] **Step 6: Run tests to verify pass**

Run: `uv run pytest tests/agent/test_state.py -v`

Expected: all 5 tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/agent/__init__.py src/agent/state.py tests/agent/__init__.py tests/agent/test_state.py
git commit -m "feat(agent): add typed AgentState with audit-friendly fields"
```

---

## Task 5: Placeholder `search_documents` tool

A single-tool registration that binds cleanly to an LLM but whose body is never executed through the default LangChain dispatch — all invocation runs through `AuthenticatedToolNode` (Task 7).

**Files:**
- Create: `src/agent/tools/__init__.py`
- Create: `src/agent/tools/search_documents.py`

- [ ] **Step 1: Create `src/agent/tools/__init__.py`**

```python
"""Agent-callable tools. Each tool's authorization is enforced in
AuthenticatedToolNode, not in the tool body.
"""

from src.agent.tools.search_documents import search_documents

__all__ = ["search_documents"]
```

- [ ] **Step 2: Create `src/agent/tools/search_documents.py`**

```python
"""Placeholder `search_documents` tool.

The body raises NotImplementedError because this tool is never invoked
through LangChain's default dispatch. `AuthenticatedToolNode`
intercepts the tool call, pulls `user_id` from state, and calls
`MeridianRetriever.search(query=..., user_id=...)` directly.

Exposing the body as NotImplementedError prevents a future refactor
from accidentally calling the unprotected path.
"""

from langchain_core.tools import tool


@tool
def search_documents(query: str) -> str:
    """Search the Meridian knowledge base for documents relevant to a
    natural-language query.

    Args:
        query: the search query, in natural language.
    """
    raise NotImplementedError(
        "search_documents must be invoked via AuthenticatedToolNode; "
        "direct calls bypass the runtime user_id injection."
    )
```

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "from src.agent.tools import search_documents; print(search_documents.name, search_documents.args)"`

Expected output contains `search_documents` and shows the schema includes only `query` (NOT `user_id`):

```
search_documents {'query': {...'type': 'string'...}}
```

- [ ] **Step 4: Commit**

```bash
git add src/agent/tools/__init__.py src/agent/tools/search_documents.py
git commit -m "feat(agent): add placeholder search_documents tool (query-only schema)"
```

---

## Task 6: `MeridianRetriever` (TDD)

Classification-level filter against the Meridian ChromaDB collection. Phase 3 extends with org-chart and project-membership rules; this task delivers only the clearance-based filter.

**Files:**
- Create: `src/agent/retriever.py`
- Create: `tests/agent/test_retriever.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_retriever.py`:

```python
"""Tests for MeridianRetriever classification-level filtering."""

from unittest.mock import Mock

import pytest

from src.agent.retriever import (
    MeridianRetriever,
    classifications_up_to,
)
from src.data.loaders import Employee
from src.exceptions import AccessDenied


def _emp(eid: str, clearance: int) -> Employee:
    return Employee(
        employee_id=eid,
        name="Test",
        title="Test",
        department="Engineering",
        manager_id=None,
        clearance_level=clearance,
        location="Remote",
        hire_date=__import__("datetime").date(2024, 1, 1),
        email=f"{eid}@example.com",
        salary=100000,
        is_active=True,
    )


def test_classifications_up_to_1():
    assert classifications_up_to(1) == ["PUBLIC"]


def test_classifications_up_to_3():
    assert set(classifications_up_to(3)) == {
        "PUBLIC", "INTERNAL", "CONFIDENTIAL",
    }


def test_classifications_up_to_4():
    assert set(classifications_up_to(4)) == {
        "PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED",
    }


def test_search_unknown_user_raises_access_denied():
    retriever = MeridianRetriever(
        collection=Mock(),
        employees_by_id={"E001": _emp("E001", 4)},
    )
    with pytest.raises(AccessDenied):
        retriever.search(query="hi", user_id="E999")


def test_search_passes_classification_filter_matching_clearance():
    collection = Mock()
    collection.query.return_value = {
        "ids": [["doc1"]],
        "documents": [["hello"]],
        "metadatas": [[{"classification": "INTERNAL"}]],
    }
    retriever = MeridianRetriever(
        collection=collection,
        employees_by_id={"E010": _emp("E010", 2)},  # INTERNAL
    )
    retriever.search(query="hi", user_id="E010")

    call_args = collection.query.call_args.kwargs
    where = call_args["where"]
    assert where == {
        "classification": {"$in": ["PUBLIC", "INTERNAL"]},
    }


def test_search_returns_flattened_results():
    collection = Mock()
    collection.query.return_value = {
        "ids": [["d1", "d2"]],
        "documents": [["a", "b"]],
        "metadatas": [[{"classification": "PUBLIC"},
                       {"classification": "PUBLIC"}]],
    }
    retriever = MeridianRetriever(
        collection=collection,
        employees_by_id={"E003": _emp("E003", 1)},
    )
    results = retriever.search(query="q", user_id="E003")
    assert [r["doc_id"] for r in results] == ["d1", "d2"]
    assert [r["content"] for r in results] == ["a", "b"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agent/test_retriever.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent.retriever'`.

- [ ] **Step 3: Create `src/agent/retriever.py`**

```python
"""Meridian document retriever with classification-level filtering.

Phase 2 scope: filter by caller's clearance_level only. Phase 3 will
extend with org-chart BFS and project-membership checks; those rules
belong with the employee-lookup tools, not here, so this module stays
narrow.
"""

from __future__ import annotations

from typing import Any

from src.data.loaders import Employee
from src.exceptions import AccessDenied

_CLASSIFICATION_ORDER = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


def classifications_up_to(clearance_level: int) -> list[str]:
    """Return the classification tiers a caller at `clearance_level`
    is permitted to see. Inclusive: level 2 sees PUBLIC + INTERNAL."""
    if not 1 <= clearance_level <= 4:
        raise ValueError(f"clearance_level must be 1–4, got {clearance_level}")
    return _CLASSIFICATION_ORDER[:clearance_level]


class MeridianRetriever:
    """Thin wrapper over a ChromaDB collection that enforces
    classification visibility per caller."""

    def __init__(
        self,
        *,
        collection: Any,  # chromadb.api.models.Collection
        employees_by_id: dict[str, Employee],
    ) -> None:
        self._collection = collection
        self._employees = employees_by_id

    def search(
        self,
        *,
        query: str,
        user_id: str,
        k: int = 5,
    ) -> list[dict]:
        requester = self._employees.get(user_id)
        if requester is None:
            raise AccessDenied(f"unknown user {user_id!r}")

        allowed = classifications_up_to(requester.clearance_level)

        result = self._collection.query(
            query_texts=[query],
            n_results=k,
            where={"classification": {"$in": allowed}},
        )
        return _flatten(result)


def _flatten(result: dict) -> list[dict]:
    """Chroma returns one list of lists per field (outer index is
    query index). We always submit a single query, so we flatten the
    outer list."""
    ids = result["ids"][0]
    docs = result["documents"][0]
    metas = result["metadatas"][0]
    return [
        {"doc_id": doc_id, "content": content, "metadata": meta}
        for doc_id, content, meta in zip(ids, docs, metas, strict=True)
    ]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/agent/test_retriever.py -v`

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent/retriever.py tests/agent/test_retriever.py
git commit -m "feat(agent): MeridianRetriever with classification-level filtering"
```

---

## Task 7: `AuthenticatedToolNode` (TDD, SECURITY-CRITICAL)

The symbolic authz primitive. Runtime injects `user_id` from state; LLM-supplied `user_id` in tool-call args is ignored and recorded as a denial. Every Phase 3 tool will ride this same pattern.

**Files:**
- Create: `src/agent/graph.py` (first slice — AuthenticatedToolNode only)
- Create: `tests/agent/test_tool_node.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_tool_node.py`:

```python
"""Tests for AuthenticatedToolNode.

These tests are load-bearing for ARCHITECTURE.md §1: the LLM must
not be able to set user_id. If these tests go green, that property
holds mechanically. If they go red, the whole security story falls
apart.
"""

from unittest.mock import Mock

from langchain_core.messages import AIMessage, ToolMessage

from src.agent.graph import AuthenticatedToolNode
from src.agent.state import initial_state


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

    node = AuthenticatedToolNode(retriever=retriever)
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

    node = AuthenticatedToolNode(retriever=retriever)
    state = _state_with_tool_call(
        user_id="E003",
        tool_args={"query": "salaries", "user_id": "E012"},  # LLM forgery
    )

    out = node(state)

    # Tool STILL ran — with the state's user_id, not the LLM's
    assert retriever.search.call_args.kwargs["user_id"] == "E003"
    # And a denial record was added to tool_call_log
    denial_records = [r for r in out["tool_call_log"]
                      if r["status"] == "denied"]
    assert len(denial_records) == 1
    assert denial_records[0]["tool_name"] == "search_documents"


def test_step_count_incremented():
    retriever = Mock()
    retriever.search.return_value = []

    node = AuthenticatedToolNode(retriever=retriever)
    state = _state_with_tool_call("E003", {"query": "q"})
    state["step_count"] = 5

    out = node(state)
    assert out["step_count"] == 6


def test_tool_message_appended_with_correct_call_id():
    retriever = Mock()
    retriever.search.return_value = [{"doc_id": "d1", "content": "hi",
                                      "metadata": {}}]

    node = AuthenticatedToolNode(retriever=retriever)
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

    node = AuthenticatedToolNode(retriever=retriever)
    state = _state_with_tool_call("E003", {"query": "q"})

    out = node(state)
    assert out["retrieved_doc_ids"] == ["d1", "d2"]


def test_budget_exhaustion_sets_termination_reason():
    retriever = Mock()
    retriever.search.return_value = []

    node = AuthenticatedToolNode(retriever=retriever)
    state = _state_with_tool_call("E003", {"query": "q"})
    state["step_count"] = 19   # next step will be 20 == max_steps
    state["max_steps"] = 20

    out = node(state)
    assert out["termination_reason"] == "budget_exhausted"


def test_unknown_tool_records_error_not_crash():
    retriever = Mock()

    node = AuthenticatedToolNode(retriever=retriever)
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agent/test_tool_node.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent.graph'`.

- [ ] **Step 3: Create `src/agent/graph.py` with AuthenticatedToolNode**

```python
"""LangGraph building blocks for the agent.

Phase 2 scope: AuthenticatedToolNode (this file) plus the ReAct
state-graph wiring (added in Task 8).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from src.agent.state import AgentState, ToolCallRecord


class AuthenticatedToolNode:
    """LangGraph node that dispatches LLM-requested tool calls to the
    Meridian retriever with a runtime-injected `user_id`.

    Key invariant: the LLM CANNOT set `user_id`. Any `user_id` present
    in tool-call args is ignored; the call proceeds with
    `state["user_id"]` and a denial record is appended to
    `tool_call_log` tagged `status="denied"` and reason
    `"llm_supplied_user_id_rejected"`.
    """

    def __init__(self, *, retriever: Any) -> None:
        self._retriever = retriever

    def __call__(self, state: AgentState) -> dict:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return {}

        new_messages: list[ToolMessage] = []
        new_records: list[ToolCallRecord] = []
        new_doc_ids: list[str] = []
        next_step = state["step_count"] + 1

        for tc in last.tool_calls:
            name = tc["name"]
            raw_args = dict(tc.get("args") or {})

            if "user_id" in raw_args:
                new_records.append(_denial_record(
                    step_index=next_step - 1,
                    tool_name=name,
                    args=raw_args,
                    reason="llm_supplied_user_id_rejected",
                ))
                raw_args.pop("user_id")

            start = time.perf_counter()
            try:
                result = self._invoke(name, raw_args,
                                      user_id=state["user_id"])
                status = "success"
                doc_ids = [r["doc_id"] for r in result] \
                    if isinstance(result, list) else []
                new_doc_ids.extend(doc_ids)
                content = _serialize_result(result)
            except Exception as e:
                status = "error"
                content = f"tool error: {e}"
            duration_ms = int((time.perf_counter() - start) * 1000)

            new_messages.append(ToolMessage(
                content=content,
                tool_call_id=tc["id"],
            ))
            new_records.append(ToolCallRecord(
                step_index=next_step - 1,
                tool_name=name,
                args_hash=_args_hash(raw_args),
                status=status,
                duration_ms=duration_ms,
            ))

        budget_exhausted = next_step >= state["max_steps"]
        update: dict = {
            "messages": new_messages,
            "tool_call_log": new_records,
            "retrieved_doc_ids": new_doc_ids,
            "step_count": next_step,
        }
        if budget_exhausted:
            update["termination_reason"] = "budget_exhausted"
        return update

    def _invoke(self, name: str, args: dict, *, user_id: str):
        if name == "search_documents":
            return self._retriever.search(
                query=args["query"],
                user_id=user_id,
            )
        raise ValueError(f"unknown tool: {name!r}")


def _args_hash(args: dict) -> str:
    payload = json.dumps(args, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _denial_record(
    *, step_index: int, tool_name: str, args: dict, reason: str,
) -> ToolCallRecord:
    return ToolCallRecord(
        step_index=step_index,
        tool_name=tool_name,
        args_hash=_args_hash(args),
        status="denied",
        duration_ms=0,
    )


def _serialize_result(result: Any) -> str:
    if isinstance(result, list):
        return json.dumps(result, default=str)
    return str(result)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/agent/test_tool_node.py -v`

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent/graph.py tests/agent/test_tool_node.py
git commit -m "feat(agent): AuthenticatedToolNode with runtime user_id injection"
```

---

## Task 8: ReAct graph topology + prompts (TDD)

Wire `agent_llm` and `tools` nodes with a conditional edge that routes on tool-call presence and enforces the budget cap. Test against a stub LLM that returns canned `AIMessage` sequences.

**Files:**
- Create: `src/agent/prompts.py`
- Modify: `src/agent/graph.py` (add `build_graph` and routing)
- Create: `tests/agent/test_graph.py`

- [ ] **Step 1: Create `src/agent/prompts.py`**

```python
"""System prompts for the agent. These are guidance, not enforcement —
ARCHITECTURE.md §1: authorization lives in tool implementations.
"""

SYSTEM_PROMPT = """You are the Meridian assistant for SecureRAG-Agent.

You have access to a single tool, `search_documents`, which searches
the Meridian knowledge base. Call it when a question requires
information from documents; answer directly if the question is
conversational or fully answered by prior tool results.

When you call a tool, pass only the `query` argument. Do NOT attempt
to pass any identity or authorization parameters — those are injected
by the runtime and cannot be set from this prompt.

Stop calling tools and produce a final answer as soon as you have
enough information, or after a few search attempts if the corpus
doesn't contain what's asked.
"""
```

- [ ] **Step 2: Write the failing graph tests**

Create `tests/agent/test_graph.py`:

```python
"""Tests for the ReAct graph topology."""

from unittest.mock import Mock

from langchain_core.messages import AIMessage, HumanMessage

from src.agent.graph import build_graph
from src.agent.state import initial_state


class StubLLM:
    """Callable that returns scripted AIMessages in sequence."""

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)
        self.bind_tools_called_with: list = []

    def bind_tools(self, tools):
        self.bind_tools_called_with = tools
        return self

    def invoke(self, messages):
        return self._responses.pop(0)


def test_direct_answer_no_tool_call_terminates():
    llm = StubLLM([AIMessage(content="The answer is 42.")])
    retriever = Mock()
    graph = build_graph(llm=llm, retriever=retriever)

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
    graph = build_graph(llm=llm, retriever=retriever)

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

    # LLM keeps asking for more tool calls, would run forever
    def always_tool_call():
        return AIMessage(
            content="",
            tool_calls=[{
                "id": f"t_{id(object())}",
                "name": "search_documents",
                "args": {"query": "x"},
            }],
        )

    class LoopingLLM(StubLLM):
        def invoke(self, messages):
            return always_tool_call()

    llm = LoopingLLM([])
    graph = build_graph(llm=llm, retriever=retriever)

    state = initial_state(
        request_id="r", user_id="E003", query="q", max_steps=3,
    )
    final = graph.invoke(state, config={"recursion_limit": 50})

    assert final["step_count"] == 3
    assert final["termination_reason"] == "budget_exhausted"


def test_bind_tools_called_with_search_documents():
    llm = StubLLM([AIMessage(content="done")])
    retriever = Mock()
    build_graph(llm=llm, retriever=retriever)

    names = [t.name for t in llm.bind_tools_called_with]
    assert names == ["search_documents"]
```

- [ ] **Step 3: Extend `src/agent/graph.py` with `build_graph` and routing**

Append to `src/agent/graph.py`:

```python
from langchain_core.messages import AIMessage as _AIMessage  # noqa
from langgraph.graph import END, StateGraph

from src.agent.prompts import SYSTEM_PROMPT
from src.agent.tools import search_documents


def build_graph(*, llm: Any, retriever: Any):
    """Build the LangGraph state machine.

    Parameters
    ----------
    llm
        Any object implementing `bind_tools(list) -> self` and
        `invoke(messages) -> AIMessage`. Real code passes an
        `ollama`-backed LangChain chat model; tests pass a stub.
    retriever
        Object with `search(query, user_id) -> list[dict]`.

    The budget cap is not a graph parameter — the wrapper seeds
    `state["max_steps"]` and `AuthenticatedToolNode` enforces it by
    setting `termination_reason="budget_exhausted"` when the next step
    would cross the cap.
    """
    llm_with_tools = llm.bind_tools([search_documents])

    def agent_llm_node(state: AgentState) -> dict:
        messages = _prepend_system(state["messages"])
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    tools_node = AuthenticatedToolNode(retriever=retriever)

    def route(state: AgentState) -> str:
        if state.get("termination_reason") == "budget_exhausted":
            return "end"
        last = state["messages"][-1]
        if isinstance(last, _AIMessage) and last.tool_calls:
            return "tools"
        return "end"

    graph = StateGraph(AgentState)
    graph.add_node("agent_llm", agent_llm_node)
    graph.add_node("tools", tools_node)
    graph.set_entry_point("agent_llm")
    graph.add_conditional_edges(
        "agent_llm", route, {"tools": "tools", "end": END},
    )
    graph.add_edge("tools", "agent_llm")
    return graph.compile()


def _prepend_system(messages):
    from langchain_core.messages import SystemMessage
    if messages and isinstance(messages[0], SystemMessage):
        return messages
    return [SystemMessage(content=SYSTEM_PROMPT), *messages]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/agent/test_graph.py -v`

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agent/graph.py src/agent/prompts.py tests/agent/test_graph.py
git commit -m "feat(agent): ReAct graph topology with budget-capped loop"
```

---

## Task 9: `AgenticChain` wrapper (TDD)

The sibling-class orchestration: entry scanners → `graph.invoke()` → exit scanners. Raises `BudgetExhausted` when the graph terminates on budget.

**Files:**
- Create: `src/agent/wrapper.py`
- Create: `tests/agent/test_wrapper.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_wrapper.py`:

```python
"""Tests for AgenticChain — entry/exit scanner orchestration."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from src.agent.wrapper import AgenticChain
from src.exceptions import (
    BudgetExhausted,
    OutputFlagged,
    QueryBlocked,
)


def _scanner(name: str, *, blocks: bool = False, flags: bool = False):
    s = Mock()
    s.name = name
    s.scan.return_value = SimpleNamespace(
        blocked=blocks,
        flagged=flags,
        reason=f"{name}_triggered" if (blocks or flags) else None,
    )
    return s


def _graph_returning(**overrides):
    """Compiled-graph stand-in that returns a stitched final state."""
    graph = MagicMock()
    final = {
        "messages": [],
        "step_count": 0,
        "max_steps": 20,
        "tool_call_log": [],
        "security_verdicts": [],
        "retrieved_doc_ids": [],
        "final_answer": None,
        "termination_reason": None,
        **overrides,
    }
    graph.invoke.return_value = final
    return graph, final


def _audit():
    a = MagicMock()
    a.new_request_id.return_value = "req-xyz"
    return a


def _chain(**kwargs):
    defaults = dict(
        graph=MagicMock(),
        rate_limiter=MagicMock(),
        input_scanners=[],
        output_scanners=[],
        audit=_audit(),
        extract_answer=lambda s: "stub-answer",
    )
    defaults.update(kwargs)
    return AgenticChain(**defaults)


def test_rate_limiter_called_with_user_id():
    rl = MagicMock()
    graph, _ = _graph_returning()
    chain = _chain(graph=graph, rate_limiter=rl)

    chain.invoke(query="hello", user_id="E003")
    rl.check.assert_called_once_with("E003")


def test_input_scanner_block_raises_query_blocked():
    graph, _ = _graph_returning()
    blocker = _scanner("injection_scan", blocks=True)
    chain = _chain(graph=graph, input_scanners=[blocker])

    with pytest.raises(QueryBlocked):
        chain.invoke(query="ignore previous instructions", user_id="E003")
    graph.invoke.assert_not_called()


def test_happy_path_returns_answer_and_metadata():
    graph, final = _graph_returning(
        retrieved_doc_ids=["d1", "d2"],
        termination_reason="answered",
    )
    chain = _chain(graph=graph)

    result = chain.invoke(query="q", user_id="E003")
    assert result["answer"] == "stub-answer"
    assert result["source_doc_ids"] == ["d1", "d2"]
    assert result["termination_reason"] == "answered"
    assert result["request_id"] == "req-xyz"


def test_budget_exhausted_raises_exception():
    graph, _ = _graph_returning(
        termination_reason="budget_exhausted",
        step_count=20, max_steps=20,
    )
    chain = _chain(graph=graph)

    with pytest.raises(BudgetExhausted):
        chain.invoke(query="q", user_id="E003")


def test_output_flag_raises_output_flagged():
    graph, _ = _graph_returning(termination_reason="answered")
    flagger = _scanner("output_scan", flags=True)
    chain = _chain(graph=graph, output_scanners=[flagger])

    with pytest.raises(OutputFlagged):
        chain.invoke(query="q", user_id="E003")


def test_security_verdicts_accumulate_entry_and_exit():
    graph, final = _graph_returning(termination_reason="answered")
    input_s = _scanner("injection_scan")
    output_s = _scanner("output_scan")
    chain = _chain(
        graph=graph,
        input_scanners=[input_s],
        output_scanners=[output_s],
    )

    chain.invoke(query="q", user_id="E003")

    # Entry scanner seeded BEFORE graph.invoke
    seed = graph.invoke.call_args.args[0]["security_verdicts"]
    assert any(v["layer"] == "injection_scan" and v["stage"] == "entry"
               for v in seed)
    # Exit scanner appended to final state
    assert any(v["layer"] == "output_scan" and v["stage"] == "exit"
               for v in final["security_verdicts"])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agent/test_wrapper.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent.wrapper'`.

- [ ] **Step 3: Create `src/agent/wrapper.py`**

```python
"""AgenticChain — wraps the LangGraph agent with Sentinel-inherited
entry/exit security layers. This is the sibling-class equivalent of
Sentinel's deleted SecureRAGChain, rebuilt around a graph invocation
rather than a single retrieve-then-generate call.
"""

from __future__ import annotations

import inspect
import unicodedata
from typing import Any, Callable

from src.agent.state import SecurityVerdict, initial_state
from src.exceptions import BudgetExhausted, OutputFlagged, QueryBlocked


def _call_scanner(scanner: Any, primary: str, **context) -> Any:
    """Call `scanner.scan(primary, ...)` passing only the context
    kwargs the scanner actually accepts.

    Sentinel-inherited scanners have heterogeneous signatures
    (InjectionScanner takes just text; OutputScanner takes output +
    question; ClassificationGuard takes just output). Rather than
    force every scanner to accept a uniform `**kwargs`, this helper
    introspects each scanner's signature and passes only the kwargs
    it declares. Unknown kwargs are silently dropped.
    """
    try:
        params = inspect.signature(scanner.scan).parameters
    except (ValueError, TypeError):
        # e.g. Mock without spec — pass everything, let duck typing work
        return scanner.scan(primary, **context)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return scanner.scan(primary, **context)
    kwargs = {k: v for k, v in context.items() if k in params}
    return scanner.scan(primary, **kwargs)


class AgenticChain:
    def __init__(
        self,
        *,
        graph: Any,
        rate_limiter: Any,
        input_scanners: list,
        output_scanners: list,
        audit: Any,
        extract_answer: Callable[[dict], str],
        max_steps: int = 20,
    ) -> None:
        self._graph = graph
        self._rate = rate_limiter
        self._in = input_scanners
        self._out = output_scanners
        self._audit = audit
        self._extract = extract_answer
        self._max_steps = max_steps

    def invoke(self, *, query: str, user_id: str) -> dict:
        request_id = self._audit.new_request_id()
        normalized = unicodedata.normalize("NFKC", query)

        self._rate.check(user_id)

        entry_verdicts: list[SecurityVerdict] = []
        for scanner in self._in:
            result = _call_scanner(scanner, normalized)
            verdict = _to_verdict(scanner.name, "entry", result)
            entry_verdicts.append(verdict)
            self._audit.log_verdict(request_id, user_id,
                                    scanner.name, "entry", result)
            if getattr(result, "blocked", False):
                raise QueryBlocked(result.reason)

        state = initial_state(
            request_id=request_id,
            user_id=user_id,
            query=normalized,
            max_steps=self._max_steps,
            seed_verdicts=entry_verdicts,
        )

        final = self._graph.invoke(state, config={"recursion_limit": 50})

        if final.get("termination_reason") == "budget_exhausted":
            self._audit.log_budget_exhausted(
                request_id, user_id, final["step_count"],
            )
            raise BudgetExhausted(max_steps=final["max_steps"])

        answer = self._extract(final)

        for scanner in self._out:
            result = _call_scanner(scanner, answer,
                                   question=normalized,
                                   user_id=user_id)
            final["security_verdicts"].append(
                _to_verdict(scanner.name, "exit", result),
            )
            self._audit.log_verdict(request_id, user_id,
                                    scanner.name, "exit", result)
            if getattr(result, "flagged", False):
                raise OutputFlagged(result.reason)

        return {
            "request_id": request_id,
            "answer": answer,
            "source_doc_ids": final["retrieved_doc_ids"],
            "termination_reason": final["termination_reason"],
        }


def _to_verdict(layer: str, stage: str, result: Any) -> SecurityVerdict:
    if getattr(result, "blocked", False):
        v = "block"
    elif getattr(result, "flagged", False):
        v = "flag"
    else:
        v = "pass"
    return SecurityVerdict(
        layer=layer,
        stage=stage,
        verdict=v,
        details=getattr(result, "reason", None),
    )
```

- [ ] **Step 4: Augment audit module with helper methods**

Check `src/audit.py` for `new_request_id`, `log_verdict`, and `log_budget_exhausted`. If any are missing, add them. Inspect first:

Run: `grep -n "^def " src/audit.py`

If `log_verdict` is missing, append to `src/audit.py`:

```python
def log_verdict(request_id: str, user_id: str, layer: str,
                stage: str, result) -> None:
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "request_id": request_id,
        "user_id": user_id,
        "layer": layer,
        "stage": stage,
        "verdict": "block" if getattr(result, "blocked", False)
                   else "flag" if getattr(result, "flagged", False)
                   else "pass",
        "reason": getattr(result, "reason", None),
    }
    logger.info(json.dumps(entry))
```

Similarly for `log_budget_exhausted`:

```python
def log_budget_exhausted(request_id: str, user_id: str,
                         step_count: int) -> None:
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "request_id": request_id,
        "user_id": user_id,
        "event": "budget_exhausted",
        "step_count": step_count,
    }
    logger.info(json.dumps(entry))
```

If `new_request_id` already exists under a different name (e.g., `generate_request_id`), alias it:

```python
new_request_id = generate_request_id  # for AgenticChain
```

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/agent/test_wrapper.py -v`

Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/agent/wrapper.py src/audit.py tests/agent/test_wrapper.py
git commit -m "feat(agent): AgenticChain wrapper with entry/exit scanner orchestration"
```

---

## Task 10: Meridian ingestion pipeline (TDD)

Ingest `data/meridian/documents/` into ChromaDB using Phase 1's loaders, applying `SanitizationGate` for PII + credential scrubbing and the chunking convention inherited from Sentinel.

**Files:**
- Create: `src/ingestion/__init__.py`
- Create: `src/ingestion/pipeline.py`
- Create: `tests/ingestion/__init__.py`
- Create: `tests/ingestion/test_meridian_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/ingestion/__init__.py` (empty) and `tests/ingestion/test_meridian_pipeline.py`:

```python
"""Tests for the Meridian ingestion pipeline."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.ingestion.pipeline import (
    SAFE_METADATA_KEYS,
    IngestResult,
    ingest_meridian,
)


@pytest.fixture
def fake_chroma():
    client = MagicMock()
    collection = MagicMock()
    client.get_or_create_collection.return_value = collection
    return client, collection


@pytest.fixture
def noop_gate():
    gate = MagicMock()
    gate.process.side_effect = lambda docs: MagicMock(
        clean=docs, quarantined=[],
    )
    return gate


def test_poisoned_documents_are_excluded_by_default(fake_chroma, noop_gate):
    client, collection = fake_chroma
    result = ingest_meridian(
        data_root=Path("data/meridian"),
        chroma_client=client,
        gate=noop_gate,
    )

    # No chunk should have TEST_POISONED: true in its metadata
    all_metas = [m for call in collection.add.call_args_list
                 for m in call.kwargs["metadatas"]]
    assert not any(m.get("TEST_POISONED") for m in all_metas)
    # And the known poisoned files should not appear as source paths
    all_paths = {m.get("path") for m in all_metas}
    assert not any("poisoned/" in str(p) for p in all_paths)


def test_metadata_is_restricted_to_safe_keys(fake_chroma, noop_gate):
    client, collection = fake_chroma
    ingest_meridian(
        data_root=Path("data/meridian"),
        chroma_client=client,
        gate=noop_gate,
    )
    for call in collection.add.call_args_list:
        for meta in call.kwargs["metadatas"]:
            user_keys = set(meta) - {"path", "chunk_index"}
            assert user_keys.issubset(SAFE_METADATA_KEYS), (
                f"metadata leaked keys: {user_keys - SAFE_METADATA_KEYS}"
            )


def test_restricted_to_never_promoted_to_chunk_metadata(
    fake_chroma, noop_gate,
):
    client, collection = fake_chroma
    ingest_meridian(
        data_root=Path("data/meridian"),
        chroma_client=client,
        gate=noop_gate,
    )
    for call in collection.add.call_args_list:
        for meta in call.kwargs["metadatas"]:
            assert "restricted_to" not in meta


def test_ingest_result_reports_counts(fake_chroma, noop_gate):
    client, _ = fake_chroma
    result = ingest_meridian(
        data_root=Path("data/meridian"),
        chroma_client=client,
        gate=noop_gate,
    )
    assert isinstance(result, IngestResult)
    assert result.clean >= 15
    assert result.chunks >= result.clean  # at least 1 chunk per doc
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ingestion/test_meridian_pipeline.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.ingestion'`.

- [ ] **Step 3: Create `src/ingestion/__init__.py`** (empty)

```python
```

- [ ] **Step 4: Create `src/ingestion/pipeline.py`**

```python
"""Meridian ingestion pipeline.

Reads documents from data/meridian/documents/ via src/data/loaders.py
(which excludes poisoned fixtures by default), sanitizes via
SanitizationGate, chunks, and stores in ChromaDB.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.documents import Document as LCDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.data.loaders import Document as MeridianDoc
from src.data.loaders import load_documents

SAFE_METADATA_KEYS = {
    "title",
    "classification",
    "project_id",
    "effective_date",
    "supersedes",
    "superseded_by",
    "owner",
}

_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 150
_COLLECTION_NAME = "meridian_documents"


@dataclass(frozen=True)
class IngestResult:
    clean: int
    quarantined: int
    chunks: int


def ingest_meridian(
    *,
    data_root: Path,
    chroma_client: Any,
    gate: Any,
    collection_name: str = _COLLECTION_NAME,
) -> IngestResult:
    """End-to-end: load → filter poisoned → sanitize → chunk → embed."""
    meridian_docs = load_documents(data_root, include_poisoned=False)
    lc_docs = [_to_langchain_doc(d) for d in meridian_docs]

    gated = gate.process(lc_docs)
    clean_docs = list(gated.clean)
    quarantined = list(gated.quarantined)

    chunks = _chunk(clean_docs)
    collection = chroma_client.get_or_create_collection(collection_name)
    if chunks:
        collection.add(
            ids=[_chunk_id(c, i) for i, c in enumerate(chunks)],
            documents=[c.page_content for c in chunks],
            metadatas=[c.metadata for c in chunks],
        )

    return IngestResult(
        clean=len(clean_docs),
        quarantined=len(quarantined),
        chunks=len(chunks),
    )


def _to_langchain_doc(doc: MeridianDoc) -> LCDocument:
    metadata = {
        k: _coerce(v)
        for k, v in doc.frontmatter.items()
        if k in SAFE_METADATA_KEYS
    }
    metadata["path"] = str(doc.path)
    return LCDocument(page_content=doc.body, metadata=metadata)


def _coerce(value: Any) -> str | int | float | bool:
    """Chroma metadata values must be scalar; flatten lists/None to str."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _chunk(docs: list[LCDocument]) -> list[LCDocument]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
    )
    out: list[LCDocument] = []
    for doc in docs:
        parts = splitter.split_documents([doc])
        for i, part in enumerate(parts):
            part.metadata["chunk_index"] = i
        out.extend(parts)
    return out


def _chunk_id(chunk: LCDocument, index: int) -> str:
    payload = f"{chunk.metadata.get('path', '?')}:{index}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]
```

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/ingestion/test_meridian_pipeline.py -v`

Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/ingestion/__init__.py src/ingestion/pipeline.py tests/ingestion/__init__.py tests/ingestion/test_meridian_pipeline.py
git commit -m "feat(ingestion): Meridian pipeline with poisoned-doc exclusion and safe metadata"
```

---

## Task 11: API rewrite with `/agent/query` and exception mapping (TDD)

Rewrite `src/api.py` with a single agent endpoint and a factory that assembles the full chain at startup.

**Files:**
- Modify: `src/api.py` (full rewrite)
- Create: `tests/agent/test_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_api.py`:

```python
"""Tests for the /agent/query endpoint and exception mapping.

Uses FastAPI's TestClient and monkeypatches `build_chain` to inject
a mock AgenticChain — this keeps the test fast (no Ollama, no Chroma).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_mock_chain(monkeypatch):
    mock_chain = MagicMock()

    import src.api
    monkeypatch.setattr(src.api, "_build_chain",
                        lambda: mock_chain)
    # Force reinit
    src.api._reset_chain_for_test()

    client = TestClient(src.api.app)
    return client, mock_chain


def test_health(app_with_mock_chain):
    client, _ = app_with_mock_chain
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_agent_query_happy_path(app_with_mock_chain):
    client, chain = app_with_mock_chain
    chain.invoke.return_value = {
        "request_id": "r1",
        "answer": "Parking is $250/month.",
        "source_doc_ids": ["expense_2026"],
        "termination_reason": "answered",
    }
    resp = client.post("/agent/query",
                       json={"query": "parking?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Parking is $250/month."
    assert body["source_doc_ids"] == ["expense_2026"]


def test_agent_query_returns_429_on_rate_limit(app_with_mock_chain):
    from src.rate_limiter import RateLimitExceeded
    client, chain = app_with_mock_chain
    chain.invoke.side_effect = RateLimitExceeded("too many")
    resp = client.post("/agent/query", json={"query": "q"})
    assert resp.status_code == 429


def test_agent_query_returns_400_on_input_block(app_with_mock_chain):
    from src.exceptions import QueryBlocked
    client, chain = app_with_mock_chain
    chain.invoke.side_effect = QueryBlocked("injection score too high")
    resp = client.post("/agent/query", json={"query": "q"})
    assert resp.status_code == 400


def test_agent_query_returns_422_on_output_flag(app_with_mock_chain):
    from src.exceptions import OutputFlagged
    client, chain = app_with_mock_chain
    chain.invoke.side_effect = OutputFlagged("classification leak")
    resp = client.post("/agent/query", json={"query": "q"})
    assert resp.status_code == 422


def test_agent_query_returns_422_on_budget_exhausted(app_with_mock_chain):
    from src.exceptions import BudgetExhausted
    client, chain = app_with_mock_chain
    chain.invoke.side_effect = BudgetExhausted(max_steps=20)
    resp = client.post("/agent/query", json={"query": "q"})
    assert resp.status_code == 422


def test_agent_query_validation_rejects_long_query(app_with_mock_chain):
    client, _ = app_with_mock_chain
    resp = client.post("/agent/query",
                       json={"query": "x" * 10_000})
    assert resp.status_code == 422  # FastAPI validation error
```

- [ ] **Step 2: Rewrite `src/api.py`**

Overwrite `src/api.py`:

```python
"""FastAPI application for SecureRAG-Agent.

Exposes:
  GET /health       — liveness
  POST /agent/query — run a query through the agentic pipeline

Exceptions raised by AgenticChain are mapped to HTTP status codes
per docs/PHASE_2_DESIGN.md §"API surface".
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.exceptions import (
    AccessDenied,
    BudgetExhausted,
    OutputFlagged,
    QueryBlocked,
)
from src.rate_limiter import RateLimitExceeded

DEMO_USER_ID = os.environ.get("SECURERAG_DEMO_USER", "E003")

app = FastAPI(title="SecureRAG-Agent")

_chain: Any | None = None


class AgentQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class AgentQueryResponse(BaseModel):
    request_id: str
    answer: str
    source_doc_ids: list[str]
    termination_reason: str | None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(request: AgentQueryRequest) -> AgentQueryResponse:
    chain = _get_chain()
    try:
        result = chain.invoke(query=request.query, user_id=DEMO_USER_ID)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": "60"},
        )
    except QueryBlocked as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OutputFlagged as e:
        raise HTTPException(status_code=422, detail=str(e))
    except BudgetExhausted as e:
        raise HTTPException(status_code=422, detail=str(e))
    except AccessDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    return AgentQueryResponse(**result)


def _get_chain():
    global _chain
    if _chain is None:
        _chain = _build_chain()
    return _chain


def _reset_chain_for_test() -> None:
    global _chain
    _chain = None


def _build_chain():
    """Assemble the full AgenticChain.

    Kept as a module-level hook so tests can monkey-patch it to inject
    a mock chain without spinning up ChromaDB + Ollama.
    """
    raise NotImplementedError(
        "_build_chain must be implemented in Task 12 or monkey-patched "
        "in tests."
    )
```

- [ ] **Step 3: Run tests to verify pass**

Run: `uv run pytest tests/agent/test_api.py -v`

Expected: all 7 tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/api.py tests/agent/test_api.py
git commit -m "feat(api): /agent/query endpoint with exception-to-HTTP mapping"
```

---

## Task 12: `_build_chain` factory + integration test stub

Wire the real `AgenticChain` assembly (so the server can actually run against Ollama) and add the live-Ollama integration test.

**Files:**
- Modify: `src/api.py` (replace `_build_chain` stub with real implementation)
- Create: `tests/agent/test_basic_loop.py`

- [ ] **Step 1: Replace `_build_chain` in `src/api.py`**

Replace the `_build_chain` function with:

```python
def _build_chain():
    """Assemble the full AgenticChain.

    This is deliberately procedural — matches the Sentinel-era
    build_chain style so the assembly order reads top to bottom.
    """
    from pathlib import Path

    import chromadb
    from langchain_ollama import ChatOllama

    import src.audit as audit
    from src.agent.graph import build_graph
    from src.agent.retriever import MeridianRetriever
    from src.agent.wrapper import AgenticChain
    from src.data.loaders import load_employees
    from src.model_integrity import verify_model_digest
    from src.rate_limiter import RateLimiter
    from src.sanitizers.injection_scanner import InjectionScanner
    from src.sanitizers.embedding_detector import EmbeddingDetector
    from src.sanitizers.output_scanner import OutputScanner
    from src.sanitizers.classification_guard import ClassificationGuard
    from src.sanitizers.credential_detector import CredentialDetector

    model = os.environ.get("SECURERAG_MODEL", "llama3.1:8b")
    ollama_host = os.environ.get("OLLAMA_HOST",
                                 "http://localhost:11434")
    expected_digest = os.environ.get("SECURERAG_MODEL_DIGEST")
    if expected_digest:
        verify_model_digest(model, ollama_host, expected_digest)

    chroma_dir = Path("data/chroma")
    chroma_client = chromadb.PersistentClient(path=str(chroma_dir))
    employees = {e.employee_id: e for e in load_employees()}
    retriever = MeridianRetriever(
        collection=chroma_client.get_collection("meridian_documents"),
        employees_by_id=employees,
    )

    llm = ChatOllama(model=model, base_url=ollama_host, temperature=0)
    graph = build_graph(llm=llm, retriever=retriever)

    rate = RateLimiter()
    input_scanners = [InjectionScanner(threshold=5),
                      EmbeddingDetector()]
    output_scanners = [OutputScanner(),
                       ClassificationGuard(accessible_classifications={
                           "PUBLIC", "INTERNAL", "CONFIDENTIAL",
                           "RESTRICTED",
                       }),
                       CredentialDetector()]

    return AgenticChain(
        graph=graph,
        rate_limiter=rate,
        input_scanners=input_scanners,
        output_scanners=output_scanners,
        audit=audit,
        extract_answer=_extract_answer,
    )


def _extract_answer(final_state: dict) -> str:
    from langchain_core.messages import AIMessage
    for msg in reversed(final_state["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return ""
```

Add `langchain-ollama` to the dependency list if it's not already there:

Run: `grep -q langchain-ollama pyproject.toml || uv add langchain-ollama`

- [ ] **Step 2: Write the integration test**

Create `tests/agent/test_basic_loop.py`:

```python
"""End-to-end integration test: asks a question that can only be
answered by retrieving from the Meridian corpus.

Gated by the `integration` pytest marker. Requires:
  - Ollama running at $OLLAMA_HOST (default localhost:11434)
  - llama3.1:8b (or whatever $SECURERAG_MODEL points at) pulled
  - ChromaDB at data/chroma/ populated via scripts/ingest_meridian.py
    (or manual ingest via `ingest_meridian(...)` in a REPL)

Skipped by default; run with: uv run pytest -m integration
"""

import pytest

pytestmark = pytest.mark.integration


def test_parking_policy_question_end_to_end():
    from fastapi.testclient import TestClient

    import src.api as api
    api._reset_chain_for_test()

    client = TestClient(api.app)
    resp = client.post("/agent/query", json={
        "query": "What is the 2026 monthly parking reimbursement cap?",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["termination_reason"] != "budget_exhausted"
    # The 2026 policy says $250/month; accept either formatting
    assert "250" in body["answer"]
    assert body["source_doc_ids"], "retriever returned nothing"
```

- [ ] **Step 3: Verify the non-integration suite still passes**

Run: `uv run pytest -q`

Expected: all unit tests pass; the integration test is skipped (no `-m integration` flag).

- [ ] **Step 4: (Optional but recommended) run the integration test**

Prerequisites: Ollama running with `llama3.1:8b` pulled, `data/chroma/` populated. If you don't have that set up, skip this step and come back when you do.

Run: `uv run pytest -m integration -v`

Expected: `test_parking_policy_question_end_to_end` passes. If it fails, check:
- Ollama is reachable: `curl http://localhost:11434/api/tags`
- Chroma has documents: `ls data/chroma/`
- Ingestion has run at least once (see Task 10's pipeline)

- [ ] **Step 5: Commit**

```bash
git add src/api.py pyproject.toml uv.lock tests/agent/test_basic_loop.py
git commit -m "feat(api): wire real AgenticChain build + integration test for parking query"
```

---

## Post-Phase-2 verification

After the final task, run the whole suite end-to-end:

```bash
uv run pytest -q --ignore=tests/agent/test_basic_loop.py
```

Expected: green on all 45+ tests (Phase 1's 25 integrity tests, scanner unit tests retained from Sentinel, and the Phase 2 agent + ingestion tests). The integration test is excluded from this run.

Push when green:

```bash
git push origin agentic-pivot
```

Phase 2 is done when:
- `test_tool_node.py` is green (the security primitive works)
- `test_budget.py`-equivalent in `test_graph.py` is green (budget cap works)
- `test_api.py` is green (HTTP surface works)
- `test_basic_loop.py` is green when run against a live Ollama

At that point, Phase 3 can start: six more tools on the `AuthenticatedToolNode` pattern, each with per-tool authorization rules.
