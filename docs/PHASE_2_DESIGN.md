# Phase 2 Design — LangGraph Agent Framework

> Date: 2026-04-17
> Status: design approved, implementation pending
> Supersedes the Phase 2 section of `AGENTIC_PIVOT_PLAN.md` where they
> differ; the plan is the roadmap, this document is the architecture.

## Goal

Wire a LangGraph-based ReAct agent end-to-end as the replacement for
the classical-RAG pipeline inherited from Sentinel. The security
primitives inherited from Sentinel (scanners, rate limiter, audit log,
model integrity check) remain in place and wrap the graph at
entry/exit. One placeholder tool (`search_documents`) is registered
and proves the plumbing; Phase 3 adds the real tool surface.

## Scope shift from the original plan

The original Phase 2 spec said "placeholder `search_documents` that
proxies Sentinel's existing retrieval." That wording implied keeping
Sentinel's classical-RAG orchestration (`SecureRAGChain`,
`AccessControlledRetriever`, the old `/query` endpoint, `data/raw/`)
alongside the new agent path and having them share retrieval.

Revised direction (2026-04-17): **Agent is fully self-contained from
Sentinel.** Sentinel remains a read-only historical reference via the
`sentinel` git remote; there is no runtime dependency on Sentinel code
or data from this repo. Security primitives that originated in
Sentinel have been copied into this repo and now evolve independently
of upstream.

### Keep (portable security primitives)

- `src/sanitizers/` — InjectionScanner, EmbeddingDetector,
  OutputScanner, ClassificationGuard, CredentialDetector, PIIDetector,
  SanitizationGate
- `src/rate_limiter.py`
- `src/audit.py`
- `src/model_integrity.py`
- Their unit tests in `tests/`

### Delete (Sentinel-specific orchestration and data model)

- `src/chain.py` — the classical `SecureRAGChain`
- `src/pipeline.py` — the old ingestion pipeline for `data/raw/`
- `src/retrieval/access_controlled.py` — tied to `hr_records.json`
  (12-employee flat schema)
- `src/loaders/` — old loaders for `data/raw/`
- `src/api.py`'s `/query` endpoint
- `data/raw/` — the classical fixture; the agentic Meridian in
  `data/meridian/` is the only corpus going forward
- Inherited end-to-end integration tests that exercise
  `SecureRAGChain`

### Build new

- `src/agent/state.py` — AgentState TypedDict + record shapes
- `src/agent/graph.py` — LangGraph state machine with
  AuthenticatedToolNode
- `src/agent/prompts.py` — system prompts (guidance, not enforcement)
- `src/agent/tools/search_documents.py` — single placeholder tool
- `src/agent/retriever.py` — MeridianRetriever with classification
  filtering (Phase 2 scope; org-chart + project rules land in Phase 3)
- `src/agent/wrapper.py` — AgenticChain entry/exit wrapper
- `src/ingestion/pipeline.py` — Meridian-native ingestion using
  `src/data/loaders.py` (from Phase 1)
- `src/api.py` rewritten with only `/agent/query` + `/health`
- `tests/agent/` — test_basic_loop, test_tool_node, test_budget,
  test_identity_override_resistance
- `tests/ingestion/test_meridian_pipeline.py`

## Architectural decisions (ADRs, condensed)

| # | Decision | Rationale |
|---|---|---|
| 1 | Sibling chain class (not shared-plumbing refactor of Sentinel) | SecureRAGChain is deleted; no refactor conflict. Agent path is its own class |
| 2 | LangGraph ReAct loop with `max_steps=20` | Phase 3 needs multi-hop; topology today must match topology tomorrow |
| 3 | Audit-friendly state (explicit `tool_call_log`, `security_verdicts`, `retrieved_doc_ids` fields) | State IS the audit record; Phase 4 becomes "emit state to sink" rather than "reconstruct audit retroactively" |
| 4 | `user_id` runtime-injected by the graph, never exposed in tool schema to the LLM | ARCHITECTURE.md §1: LLM tool-call arguments are untrusted. This is the symbolic authz primitive every Phase 3 tool will inherit |
| 5 | Agent is self-contained from Sentinel | Sentinel's classical-RAG surface is frozen and irrelevant to the agentic threat model; carrying it forward creates two parallel data models that will drift |

## Agent state schema

```python
# src/agent/state.py

from typing import Annotated, TypedDict
from operator import add as list_add
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class ToolCallRecord(TypedDict):
    step_index: int
    tool_name: str
    args_hash: str          # sha256(sorted_json(args))[:16]
    status: str             # "success" | "error" | "denied"
    duration_ms: int

class SecurityVerdict(TypedDict):
    layer: str              # "rate_limit" | "injection_scan" | ...
    stage: str              # "entry" | "exit"
    verdict: str            # "pass" | "block" | "flag"
    details: str | None

class AgentState(TypedDict):
    # Identity — runtime-injected, not mutated in-graph
    request_id: str
    user_id: str

    # Conversation — LangGraph's add_messages reducer handles append
    messages: Annotated[list[BaseMessage], add_messages]

    # Budget
    step_count: int
    max_steps: int

    # Audit trail — list-append reducers
    tool_call_log: Annotated[list[ToolCallRecord], list_add]
    security_verdicts: Annotated[list[SecurityVerdict], list_add]
    retrieved_doc_ids: Annotated[list[str], list_add]

    # Terminal state
    final_answer: str | None
    termination_reason: str | None    # "answered" | "budget_exhausted" | "error"
```

## Graph topology

```
    ┌─────────────┐
    │  agent_llm  │◄──────────┐
    └──────┬──────┘           │
           │                  │
    ┌──────▼────────┐         │
    │ should_route? │         │
    └─┬─────────┬───┘         │
      │         │             │
      │tool_call│end           │
      │         │             │
      ▼         ▼             │
┌─────────┐    END            │
│  tools  │                   │
│(authed) │                   │
└────┬────┘                   │
     │                        │
     └────────────────────────┘
      (step_count++; if >= max_steps → END with "budget_exhausted")
```

```python
graph = StateGraph(AgentState)
graph.add_node("agent_llm", agent_llm_node)
graph.add_node("tools", AuthenticatedToolNode([search_documents], retriever))
graph.set_entry_point("agent_llm")
graph.add_conditional_edges("agent_llm", _route_after_llm,
                            {"tools": "tools", "end": END})
graph.add_edge("tools", "agent_llm")

def _route_after_llm(state: AgentState) -> str:
    if state["step_count"] >= state["max_steps"]:
        return "end"   # budget_exhausted set by tool node
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "end"
```

## AuthenticatedToolNode — the symbolic authz primitive

This is the piece that enforces ARCHITECTURE.md §1 at the bind layer.
The LLM only ever sees `{query: string}` in the tool schema; the Python
function receives a `user_id` kwarg populated from `state.user_id`. Any
`user_id` the LLM emits as a tool-call argument is ignored; the node
records such attempts as a `tool_call_log` entry tagged with
`status="denied"` and reason `"llm_supplied_user_id_rejected"`.

```python
# src/agent/graph.py (AuthenticatedToolNode, abbreviated)

class AuthenticatedToolNode:
    def __init__(self, tools, retriever):
        self._tools = {t.name: t for t in tools}
        self._retriever = retriever

    def __call__(self, state: AgentState) -> dict:
        last = state["messages"][-1]
        out_messages, out_records = [], []

        for tc in last.tool_calls:
            args = tc["args"]
            if "user_id" in args:                     # symbolic check
                # LLM attempted to override identity; ignore the value,
                # record the attempt, continue with trusted identity
                out_records.append(_denial_record(tc, state,
                    reason="llm_supplied_user_id_rejected"))

            result, status, ms = self._invoke(tc["name"], args,
                                              user_id=state["user_id"])
            out_messages.append(ToolMessage(content=str(result),
                                            tool_call_id=tc["id"]))
            out_records.append(_success_record(tc, state, ms, status))

        budget_exhausted = state["step_count"] + 1 >= state["max_steps"]
        return {
            "messages": out_messages,
            "tool_call_log": out_records,
            "step_count": state["step_count"] + 1,
            "termination_reason": "budget_exhausted" if budget_exhausted else None,
        }
```

## Placeholder tool

```python
# src/agent/tools/search_documents.py

from langchain_core.tools import tool

@tool
def search_documents(query: str) -> str:
    """Search the Meridian knowledge base for documents relevant to a
    natural-language query.

    Args:
        query: the search query, in natural language.
    """
    # The tool body is never called through LangChain's default dispatch.
    # AuthenticatedToolNode intercepts the call and invokes
    # MeridianRetriever.search(query, user_id=<state.user_id>).
    raise NotImplementedError(
        "search_documents must be invoked via AuthenticatedToolNode"
    )
```

The decorator exposes the tool to the LLM with schema
`{query: string}`. There is deliberately no `user_id` parameter in
the schema — this is the load-bearing simplification that prevents
the LLM from expressing an identity override as a valid tool call.

## MeridianRetriever (Phase 2 scope)

Simpler than Sentinel's `AccessControlledRetriever`. Phase 2 filters
only by clearance level. Phase 3 will add org-chart BFS and project
membership checks.

```python
# src/agent/retriever.py

class MeridianRetriever:
    def __init__(self, chroma_client, collection_name, employees_by_id):
        self._collection = chroma_client.get_collection(collection_name)
        self._employees = employees_by_id

    def search(self, *, query: str, user_id: str, k: int = 5) -> list[dict]:
        requester = self._employees.get(user_id)
        if requester is None:
            raise AccessDenied(f"unknown user {user_id}")

        allowed = _classifications_up_to(requester.clearance_level)

        result = self._collection.query(
            query_texts=[query], n_results=k,
            where={"classification": {"$in": allowed}},
        )
        return _flatten(result)
```

## Ingestion pipeline (Meridian-native)

```python
# src/ingestion/pipeline.py

def ingest_meridian(data_root: Path, chroma_client,
                    gate: SanitizationGate) -> IngestResult:
    raw = load_documents(data_root)              # src/data/loaders.py
    lc_docs = [_to_langchain_doc(d) for d in raw if not d.is_poisoned]
    gated = gate.process(lc_docs)                # PII redaction +
                                                 # credential scrub
    chunks = _chunk(gated.clean)
    collection = chroma_client.get_or_create_collection(
        "meridian_documents"
    )
    collection.add(
        ids=[_chunk_id(c) for c in chunks],
        documents=[c.page_content for c in chunks],
        metadatas=[c.metadata for c in chunks],
    )
    return IngestResult(
        clean=len(gated.clean),
        quarantined=len(gated.quarantined),
        chunks=len(chunks),
    )
```

**Chunking.** Recursive-character splitter, chunk_size=1000 characters,
overlap=150. These settings are inherited from Sentinel's pipeline and
have held up in red-team evaluation; no reason to re-litigate here.
Revisit if retrieval quality on multi-hop queries is poor in Phase 6.

**Metadata promoted to Chroma** (the only keys copied from frontmatter
to per-chunk metadata):

```python
SAFE_METADATA_KEYS = {
    "title", "classification", "project_id", "effective_date",
    "supersedes", "superseded_by", "owner",
}
```

The `restricted_to` list is NOT promoted to per-chunk metadata — it
lives at the document level only, fetched by the retriever in a
secondary lookup after semantic match. This prevents the list from
being exposed via `get_by_metadata` queries.

Poisoned documents are excluded by default (loader's
`include_poisoned=False`). A separate red-team ingestion path (Phase
5+) will load them into a distinct collection.

## AgenticChain wrapper

```python
# src/agent/wrapper.py

class AgenticChain:
    def __init__(self, graph, rate_limiter, input_scanners,
                 output_scanners, audit_log):
        self._graph = graph
        self._rate = rate_limiter
        self._in = input_scanners
        self._out = output_scanners
        self._audit = audit_log

    def invoke(self, *, query: str, user_id: str) -> dict:
        rid = new_request_id()
        normalized = unicodedata.normalize("NFKC", query)

        self._rate.check(user_id)
        for s in self._in:
            r = s.scan(normalized)
            self._audit.log_verdict(rid, user_id, s.name, "entry", r)
            if r.blocked:
                raise QueryBlocked(r.reason)

        entry_verdicts = self._collect_entry_verdicts(normalized)
        initial = _initial_state(rid, user_id, normalized,
                                 seed_verdicts=entry_verdicts)

        final_state = self._graph.invoke(
            initial,
            config={"recursion_limit": 50},     # see note below
        )

        if final_state["termination_reason"] == "budget_exhausted":
            self._audit.log_budget_exhausted(rid, user_id,
                                             final_state["step_count"])
            raise BudgetExhausted(max_steps=final_state["max_steps"])

        answer = _extract_answer(final_state)

        for s in self._out:
            r = s.scan(answer, question=normalized, user_id=user_id)
            final_state["security_verdicts"].append(
                _to_verdict(s.name, "exit", r)
            )
            self._audit.log_verdict(rid, user_id, s.name, "exit", r)
            if r.flagged:
                raise OutputFlagged(r.reason)

        return {
            "request_id": rid,
            "answer": answer,
            "source_doc_ids": final_state["retrieved_doc_ids"],
            "termination_reason": final_state["termination_reason"],
        }
```

## API surface

```python
POST /agent/query
Body: {"query": str}        # user_id comes from SECURERAG_DEMO_USER env
Response 200: {
    "request_id": str,
    "answer": str,
    "source_doc_ids": list[str],
    "termination_reason": str
}

GET /health
Response 200: {"status": "ok"}
```

Error mapping:

| Exception | HTTP | Body |
|---|---|---|
| `RateLimitExceeded` | 429 | `{"detail": "<reason>"}` + `Retry-After` |
| `QueryBlocked` (entry scanner) | 400 | `{"detail": "<reason>"}` |
| `OutputFlagged` (exit scanner) | 422 | `{"detail": "<reason>"}` |
| `BudgetExhausted` (agent-specific) | 422 | `{"detail": "agent budget of N steps exceeded"}` |
| Unexpected | 500 | generic |

Budget exhaustion surfaces as 422 rather than 500 because it's a
legitimate termination mode (the agent did its best; the query just
couldn't be answered in budget), not an internal error.

### Flow of `security_verdicts` through state

The state field `security_verdicts` is populated at three points:

1. **Entry** — the wrapper seeds it with one verdict per input
   scanner before calling `graph.invoke()`.
2. **In-graph** — `AuthenticatedToolNode` appends verdicts when the
   LLM attempts an identity override (or, in future phases, when a
   per-tool authorization check rejects an operation).
3. **Exit** — the wrapper appends one verdict per output scanner
   after `graph.invoke()` returns and before the response is built.

Phase 4 will read `final_state["security_verdicts"]` +
`final_state["tool_call_log"]` as the audit record for a request.

### Why `recursion_limit=50` on `graph.invoke`

LangGraph counts each node execution as one super-step. The worst-case
loop is `agent_llm` → `tools` → `agent_llm` → … repeated up to
`max_steps=20` times, which is ~40 node executions. Setting
`recursion_limit=50` gives a small safety margin so LangGraph's
internal cap never fires before our explicit `max_steps` check does.
If LangGraph hits 50 it means something is wrong (an unintended loop
in the graph topology) and we want a hard failure.

## Test plan

| Test | Purpose |
|---|---|
| `test_state.py` | AgentState reducers append to lists, state keys present |
| `test_tool_node.py` | AuthenticatedToolNode: (a) user_id from state reaches tool; (b) LLM-supplied user_id is rejected + recorded; (c) unknown tool returns error record |
| `test_identity_override_resistance.py` | End-to-end: construct a tool-call with a forged user_id; assert tool still runs with state's user_id; assert denial record exists |
| `test_budget.py` | Agent hits 20-step cap; final state has `termination_reason="budget_exhausted"`; API returns 422 |
| `test_basic_loop.py` | Happy path: ask "what is the 2026 parking reimbursement cap?" → agent calls search_documents once → MeridianRetriever returns expense_policy_2026 chunks → LLM synthesizes "$250/month" → all scanners pass → 200 response |
| `test_meridian_pipeline.py` | Ingest `data/meridian/documents/` → poisoned docs excluded → non-poisoned embedded with correct metadata; classification tier preserved |

`test_basic_loop.py` runs end-to-end against a live Ollama with
`llama3.1:8b` and a freshly-built ChromaDB collection. It carries the
`integration` pytest marker (already defined in `pyproject.toml`) so
ordinary `uv run pytest` runs skip it. All other agent tests mock the
LLM via a stub that returns canned `AIMessage(tool_calls=[...])`
sequences, so they run in <1 second without an LLM dependency.

## Dependencies

Add to `[project].dependencies` in `pyproject.toml`:

```
langgraph            # pin exact version via uv lock
```

Run `uv add langgraph` then `uv sync`. `uv` resolves a compatible
`langchain-core` automatically; the lock file is committed.

## Non-goals for Phase 2 (deferred to later phases)

- Tools other than `search_documents` (Phase 3)
- Org-chart BFS or project-membership retrieval filters (Phase 3)
- Per-hop audit emission to `logs/audit-*.jsonl` (Phase 4)
- Formal threat model document for agentic attacks (Phase 5)
- Multi-hop evaluation harness (Phase 6)
- Red-team ingestion of `documents/poisoned/` (Phase 5+)

## Open questions for Phase 3 pre-work

- Tool registration pattern: module-level `TOOLS = [...]` list vs.
  explicit registration in a factory. Pick before Phase 3 starts so
  the 7 tool modules follow the same convention.
- Whether to pin a specific embedding model digest in
  `config/models.yaml` alongside the LLM digest (model-integrity
  primitive already supports it).
