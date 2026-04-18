# Agentic Pivot Plan — SecureRAG-Agent

> **Status (2026-04-17):** All 6 phases complete + Phase 2.5 cleanup.
> Agent stack with 7 tools, full audit trail, threat model
> documented, eval harness with stub-mode baseline. Live-Ollama
> integration testing requires `data/chroma/` populated and Ollama
> running. Follow-on adaptive red-team work continues in
> [`ai-redteam-lab`](https://github.com/mathewtom/ai-redteam-lab).
> See [`docs/PROJECT_COMPLETE.md`](PROJECT_COMPLETE.md).

> Phased migration from classical RAG (Sentinel) to agentic RAG (Agent).
> This document is the authoritative source for "what are we building right
> now." When a phase is complete, move it to the "Completed" section at the
> bottom and archive the details.

## North star

Build an agentic RAG system where:

1. The agent can perform **multi-hop reasoning** requiring 2–5 retrieval or
   tool-call steps.
2. Every tool enforces **access control in implementation**, not in prompt
   instructions.
3. The full tool chain is **instrumented** for per-step security audit.
4. The threat model explicitly addresses agentic-specific attacks: tool
   misuse, cross-hop indirect injection, goal drift, budget exhaustion.
5. The result is a legitimate target for adaptive adversarial attacks
   (AutoDAN-HGA adapted to agentic objectives, run from `ai-redteam-lab`).

## Design decisions made (do not re-litigate without discussion)

| Decision | Choice | Why |
|---|---|---|
| Agent framework | LangGraph | Supports branching, interrupts, state; LangChain AgentExecutor is deprecated |
| Dataset approach | Expand Meridian (not replace) | Preserves continuity with Sentinel, avoids re-doing PII redaction work |
| Tool granularity | Coarse (5–8 tools) | Each tool has clear authorization semantics; fine-grained tools dilute access control |
| Persistence layer | SQLite for structured (employees, tickets), ChromaDB for documents | Real enterprises have both; security model must handle both |
| Authentication model | `user_id` passed explicitly through tool chain | Simulates what a production system's auth context would look like |
| LLM for agent reasoning | `llama3.1:8b` during dev, `llama3.3:70b` for eval runs | Dev speed vs. eval fidelity |

## Phases

### Phase 0 — Fork and freeze Sentinel context (0.5 day)

**Goal:** Clean working repo with preserved origin history.

- [ ] Fork SecureRAG-Sentinel → SecureRAG-Agent on GitHub
- [ ] Clone locally, verify `main` branch works end-to-end against inherited
      Meridian data
- [ ] Create `docs/FORK_ORIGIN.md` noting the fork point (commit SHA) and
      why the fork was made
- [ ] Update root `README.md` to reflect new scope (draft — will be revised
      as phases complete)
- [ ] Commit `docs/ARCHITECTURE.md` (durable principles + conventions) and
      this document
- [ ] Create long-lived branch `agentic-pivot` for phase work; merge to `main`
      at phase completions

**Definition of done:** Clean clone, existing Sentinel tests pass,
`docs/ARCHITECTURE.md` and this document merged to `main`.

---

### Phase 1 — Dataset expansion (2–3 days)

**Goal:** Meridian dataset supports multi-hop and tool-chaining queries.

Sentinel's Meridian has 5 employees (E001–E005), flat tier access, and
documents that are single-shot answerable. This is insufficient for agentic
workloads because an agent has nothing to *do* — one retrieval is always
enough.

The expansion MUST introduce:

- **Organizational depth.** ~30–50 employees across at least 4 hierarchy
  levels (IC → Manager → Director → VP/Exec). Cross-functional dotted-line
  relationships for multi-hop edge cases.
- **Structured entities alongside documents.** New tables:
  - `employees` (id, name, title, manager_id, department, clearance_level,
    location, hire_date)
  - `tickets` (id, title, owner_id, assignee_id, status, classification)
  - `projects` (id, name, owner_id, members_json, classification)
  - `calendar_events` (id, organizer_id, attendees_json, subject, time)
- **Cross-referenced documents.** Expense policy → approval matrix → role
  definitions → org chart. A single realistic question should require
  traversing at least 3 of these.
- **Ambiguous entities.** Multiple people named "Anderson," multiple projects
  with similar names. Forces the agent to disambiguate.
- **Temporal data.** Historical versions of at least 2 key policy documents
  (current + 1 prior year). Enables "who approved this last year" queries.

#### Deliverables

- [ ] `docs/DATASET_DESIGN.md` — schema, entity-relationship diagram, sample
      queries at each multi-hop depth (1-hop, 2-hop, 3-hop, 4-hop)
- [ ] `data/meridian/employees.csv` and `.json` — 30–50 employees
- [ ] `data/meridian/tickets.csv` — realistic ticket corpus
- [ ] `data/meridian/projects.json`
- [ ] `data/meridian/calendar.json`
- [ ] `data/meridian/documents/` — expanded document corpus with explicit
      cross-references
- [ ] `src/data/loaders.py` — functions to load each entity type into the
      appropriate store (SQLite for structured, ChromaDB for documents)
- [ ] `tests/data/test_dataset_integrity.py` — referential integrity checks
      (no orphan manager_ids, no tickets referencing non-existent employees)

#### Security constraints during data design

- PII must still pass through Presidio before embedding. Expanding the dataset
  does NOT relax this.
- Every employee and document gets an explicit classification marker
  consistent with Sentinel's existing scheme.
- Test data includes **deliberate ambiguities and red herrings** — realistic
  HR corpora have these, and the agent must handle them without leaking
  adjacent information.
- **At least 3 documents contain planted latent-injection payloads** for
  adversarial testing. These are marked `TEST_POISONED=True` in metadata so
  they can be excluded from production-like scenarios but available for
  red-team exercises.

---

### Phase 2 — Agent framework integration (2–3 days)

**Goal:** LangGraph agent with basic tool loop wired end-to-end. Security
layers from Sentinel remain active.

#### Deliverables

- [ ] `pyproject.toml` updates: add `langgraph`, pin version, `uv lock`
- [ ] `src/agent/graph.py` — LangGraph state machine definition
- [ ] `src/agent/state.py` — typed agent state (request_id, user_id, history,
      tool_calls, etc.)
- [ ] `src/agent/prompts.py` — system prompts (NB: these are guidance, NOT
      enforcement)
- [ ] `src/api.py` update — new endpoint `/agent/query` that drives the
      LangGraph agent, preserving existing `/query` as classical-RAG fallback
- [ ] Integration: all existing Sentinel layers (rate limit, input scan,
      sanitization, output scan) wrap the agent entry/exit, not individual
      tool calls
- [ ] `tests/agent/test_basic_loop.py` — agent can complete a simple
      single-tool query

#### Explicit non-goals for this phase

- No tool implementations yet beyond a placeholder `search_documents` that
  proxies Sentinel's existing retrieval
- No access control on tools yet (Phase 3) — but the agent MUST NOT be
  exposed to real data until Phase 3 lands
- No per-hop instrumentation yet (Phase 4)

---

### Phase 3 — Tool surface with enforced access control (3–4 days)

**Goal:** The full tool surface, each with access control in its implementation.

This is the phase where SecureRAG-Agent earns its name. Every tool MUST follow
the authorization pattern: verify the caller (`user_id`) against the requested
operation in Python code, before returning data, regardless of what the LLM
argued in its prompt.

#### Tool inventory

| Tool | Arguments | Authorization rule |
|---|---|---|
| `search_documents` | query, user_id | Inherit Sentinel's BFS + ChromaDB filters |
| `lookup_employee` | employee_id, user_id | Caller must be manager chain or same department or have HR clearance |
| `get_approval_chain` | employee_id, user_id | Caller must be the employee themselves, their manager, or HR |
| `list_my_tickets` | user_id | Returns only tickets owned by or assigned to user_id; no impersonation |
| `get_ticket_detail` | ticket_id, user_id | Caller must be owner, assignee, or in same project |
| `list_calendar_events` | date_range, user_id | Only events where user_id is organizer or attendee; others return "busy" placeholders |
| `escalate_to_human` | reason, user_id | No auth check — always available, logs the reason |

#### Deliverables

- [ ] `src/agent/tools/` directory, one module per tool
- [ ] `src/agent/tools/auth.py` — shared authorization primitives (BFS org
      traversal, classification check, department membership check)
- [ ] Each tool has unit tests in `tests/agent/tools/test_<tool>.py`
      covering: happy path, authorization denial, missing entity, injection
      in arguments
- [ ] `docs/TOOL_SURFACE.md` — user-facing documentation of each tool's
      contract and authorization model
- [ ] Update `docs/THREAT_MODEL.md` with tool-specific threats

#### Critical test: impersonation resistance

For every tool that takes a `user_id`, there MUST be a test that verifies the
tool does NOT simply trust `user_id` from the LLM. The tool should receive
`user_id` from an authenticated session context injected by the agent graph,
not from the LLM's tool-call arguments. This is enforced by making `user_id`
a kwarg injected by the graph runtime, and failing the call if the LLM tries
to override it.

---

### Phase 4 — Per-hop instrumentation and audit (1–2 days)

**Goal:** Every tool call emits a structured audit event. Sufficient telemetry
exists to reconstruct any agent session for post-hoc analysis.

#### Deliverables

- [x] `src/agent/audit_sink.py` — file-backed JSONL sink, day-rotated
- [x] Schema: `(ts, event, request_id, user_id, hop_index, tool_name,
      args_sha256, status, duration_ms, reason)` — see PHASE_4_PLAN.md
- [x] Query content is SHA-256 hashed; raw content is NOT logged
- [x] `tests/agent/test_audit_trail.py` — end-to-end integrity
- [x] Log output to `logs/audit-YYYY-MM-DD.jsonl`
- [x] `AccessDenied` distinguished from operator errors as `status=denied`

---

### Phase 5 — Agentic threat model document (1 day)

**Goal:** Comprehensive threat model covering attacks that didn't exist in
classical RAG.

#### Deliverables

- [ ] `docs/THREAT_MODEL.md` covering:
  - Tool misuse (LLM08 Excessive Agency)
  - Cross-hop indirect injection (document injected to redirect next tool call)
  - Goal hijacking (agent objective rewritten mid-chain)
  - Recursive loop / budget exhaustion
  - Tool argument injection (SQL-like injection into structured tool args)
  - Authorization confusion (LLM attempts to override user_id)
  - Information aggregation (multiple allowed retrievals combining to leak
    disallowed conclusion)
- [ ] Each threat mapped to:
  - OWASP LLM Top 10 entry
  - MITRE ATLAS technique
  - Which defense layer addresses it (or "known gap" if unaddressed)

---

### Phase 6 — Evaluation harness and baseline run (1 day)

**Goal:** Repeatable eval on canned multi-hop queries to produce before/after
numbers as defenses are added.

#### Deliverables

- [x] `eval/agentic_queries.jsonl` — 52 multi-hop queries across 11
      categories with expected behaviors
- [x] `eval/run_eval.py` — CLI runs agent against query set, produces
      markdown report; supports `--live` for Ollama-backed runs
- [x] `eval/reporter.py` — structured markdown report generator
      (lives in code rather than as a static template)
- [x] First baseline run recorded in
      [`eval/results/baseline_2026-04-17.md`](../eval/results/baseline_2026-04-17.md)
      (stub mode, 26 / 52 passing — meaningful baseline; iterative
      query tuning is Phase 7+)

---

## Completed phases

(Move phases here as they finish, with the final commit SHA and a 2–3 sentence
retrospective.)
