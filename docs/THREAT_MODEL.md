# Threat Model — SecureRAG-Agent

> **Status:** scaffolded in Phase 2.5; populated incrementally as
> phases complete.
> **Authoritative source:** the code; this document explains *why*
> the code looks the way it does.

## Threat-model scope

This document covers attacks that exploit the **agentic** surface
(multi-hop tool chaining, indirect injection through retrieved
documents, identity confusion, budget exhaustion). Classical
single-shot RAG attacks remain in scope only insofar as they extend
to the agentic case; the original Sentinel threat model covered the
classical surface and is referenced by URL where still relevant.

## Threat catalog

Each entry maps to OWASP LLM Top 10 (2025) and the MITRE ATLAS
matrix, names the defense layer that addresses it, and points at the
test that proves the defense holds.

### T-001 — LLM-supplied identity override (`user_id` smuggling)

- **Description.** The LLM emits a tool call with a forged
  `user_id` argument intended to bypass authorization checks.
- **OWASP LLM Top 10.** LLM01 (Prompt Injection), LLM06 (Excessive
  Agency).
- **MITRE ATLAS.** AML.T0051 (LLM Prompt Injection).
- **Defense.** Symbolic. `AuthenticatedToolNode` strips any `user_id`
  key (case-insensitive) from tool-call args, records a denial, emits
  `audit.log_denial`, and invokes the tool with the trusted
  `state["user_id"]`.
- **Tests.** `tests/agent/test_tool_node.py::test_llm_supplied_user_id_is_rejected_and_logged`,
  `::test_case_variant_user_id_also_rejected`,
  `::test_in_graph_denial_calls_audit_log_denial`.
- **Status.** Mitigated.

### T-002 — Recursive budget exhaustion

- **Description.** A coerced or malicious agent loops forever calling
  tools, exhausting compute or stalling other tenants.
- **OWASP LLM Top 10.** LLM10 (Unbounded Consumption).
- **MITRE ATLAS.** AML.T0029 (Denial of ML Service).
- **Defense.** Symbolic. `state["max_steps"]` (default 20) is
  enforced by `AuthenticatedToolNode`, which sets
  `termination_reason="budget_exhausted"` and surfaces as a 422 via
  `BudgetExhausted` in the wrapper.
- **Tests.** `tests/agent/test_tool_node.py::test_budget_exhaustion_sets_termination_reason`,
  `tests/agent/test_graph.py::test_budget_exhaustion_terminates_loop`.
- **Status.** Mitigated.

### T-003 — Cross-hop indirect injection (poisoned document hijacks the next tool call)

- **Description.** A document retrieved by `search_documents` contains
  embedded instructions that the LLM treats as authoritative on the
  next hop, redirecting subsequent tool calls.
- **OWASP LLM Top 10.** LLM01 (Prompt Injection — indirect variant).
- **MITRE ATLAS.** AML.T0051.001.
- **Defense.** Layered. (a) `SanitizationGate` redacts PII and
  credentials at ingestion. (b) `InjectionScanner` scores user-query
  injection patterns at entry. (c) Per-hop authorization in tools
  (Phase 3) bounds blast radius even if the LLM is manipulated. (d)
  `documents/poisoned/` fixtures are excluded by default at
  ingestion, so production traces never carry intentional bait.
- **Tests.** `tests/ingestion/test_meridian_pipeline.py::test_poisoned_documents_are_excluded_by_default`.
  Coverage of agentic indirect injection requires an integration test
  that's deferred to Phase 5+ (red-team harness).
- **Status.** Partially mitigated; full coverage requires Phase 5+.

### T-004 — Tool argument injection (SQL-like injection into structured tool args)

- **Description.** The LLM is coerced into emitting tool args that
  contain malicious payloads (e.g., a SQL fragment in a `query`
  argument that the tool naively concatenates).
- **OWASP LLM Top 10.** LLM05 (Improper Output Handling).
- **MITRE ATLAS.** AML.T0048.
- **Defense.** Per-tool input validation in handler bodies (Phase 3
  scope; today only `search_documents` exists, which forwards
  `args["query"]` unchanged to a vector-DB embedding step where
  string concatenation isn't a vector).
- **Status.** Not yet exercised; will be relevant when Phase 3 tools
  touch SQL/structured stores (`lookup_employee`, `list_my_tickets`,
  `get_ticket_detail`, `list_calendar_events`).

### T-005 — Information aggregation across multiple authorized retrievals

- **Description.** Each individual tool call is authorized for the
  caller, but the *combination* of results yields a conclusion they
  shouldn't be able to draw (e.g., joining attendees of a Restricted
  meeting against employees in a particular department).
- **OWASP LLM Top 10.** LLM02 (Sensitive Information Disclosure).
- **MITRE ATLAS.** AML.T0024 (Exfiltration via ML Inference API).
- **Defense.** Currently relies on per-tool authz preventing the
  load-bearing retrieval (e.g., the Restricted meeting attendees
  shouldn't be retrievable in the first place). The
  `retrieved_doc_ids` field in `AgentState` is the audit data needed
  to detect aggregation patterns post-hoc; Phase 4's per-hop audit
  emission will surface it.
- **Status.** Defense-in-depth required; not fully addressed.

### T-006 — Goal hijacking (agent's task rewritten mid-chain)

- **Description.** A retrieved document or tool result contains an
  instruction the LLM treats as its new objective, abandoning the
  user's actual question.
- **OWASP LLM Top 10.** LLM01.
- **MITRE ATLAS.** AML.T0051.
- **Defense.** Symbolic guarantees on identity (T-001) and budget
  (T-002) bound the damage. Neural defense via `OutputScanner` (Llama
  Guard) provides best-effort detection at the response boundary.
  `documents/poisoned/injection_goal_hijack.md` is a fixture exercising
  this attack class.
- **Status.** Partially mitigated; relies on Phase 5+ red-team
  evaluation to characterize residual risk.

## Out of scope

- Network-layer attacks (TLS, DNS, infrastructure) — handled by
  whatever runtime hosts the service.
- Authentication of `user_id` itself — currently a hardcoded demo
  identity (`SECURERAG_DEMO_USER`); a real deployment integrates an
  auth provider.
- Insider threats with database access — orthogonal to the agent
  surface.
- Side-channel attacks via timing or cache state — out of scope for
  the local-only ChromaDB + Ollama deployment model.

## Process

- Every PR that adds a new agent tool MUST add or extend an entry in
  this document. The PR description must reference the entry by ID.
- Findings from Phase 5+ red-team work (Garak, promptfoo, the
  AutoDAN-derived adaptive harness in `ai-redteam-lab`) update the
  status field of the relevant entry, and may add new entries.
