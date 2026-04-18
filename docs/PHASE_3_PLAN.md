# Phase 3 Implementation Plan — Full Tool Surface

> **Goal:** Six new agent-callable tools, each with authorization
> enforced in Python-side handler code, never in LLM instructions.
> Sets the contract that every future tool follows.

**Architecture:** Each tool is a `@tool`-decorated function (LLM-facing
schema only) plus a registry entry mapping its name to a
`handler(args, *, user_id)` callable. The handler enforces tool-specific
authorization using shared primitives in `src/agent/tools/auth.py`,
then dispatches to the appropriate data store (employees dict,
ChromaDB, in-memory tickets/projects/calendar from Phase 1).

**Reference:** [`docs/AGENTIC_PIVOT_PLAN.md`](AGENTIC_PIVOT_PLAN.md)
Phase 3, [`docs/PHASE_2_DESIGN.md`](PHASE_2_DESIGN.md) §"Tool surface",
[`docs/THREAT_MODEL.md`](THREAT_MODEL.md) for OWASP / ATLAS mappings.

---

## Tool inventory

| Tool | Args (LLM-visible schema) | Authorization rule |
|---|---|---|
| `search_documents` (existing, extend) | `{query: str}` | classification ≤ caller clearance + restricted_to honored |
| `lookup_employee` | `{employee_id: str}` | caller in manager chain OR same dept OR HR |
| `get_approval_chain` | `{employee_id: str, amount_usd: float}` | caller is employee, in manager chain, or in Finance |
| `list_my_tickets` | `{}` | always returns only tickets where caller is owner or assignee |
| `get_ticket_detail` | `{ticket_id: str}` | caller is owner, assignee, or in same project |
| `list_calendar_events` | `{date_range: str}` | only events where caller is organizer or attendee; others = busy placeholder |
| `escalate_to_human` | `{reason: str}` | no auth check — always available, audit-logged |

Every tool's Python handler MUST raise `AccessDenied` (already in
`src/exceptions.py`) when the caller lacks authorization. The
`AuthenticatedToolNode` already surfaces this as a `tool_call_log`
record with `status=ERROR` and now an `audit.log_denial` entry
(per Phase 2.5 #1).

## Two-part split

### Part 1 — Foundation + identity-flavored tools

1. **`src/agent/tools/auth.py`** — shared authorization primitives.
   Pure functions, deterministic, no I/O. Each is independently
   testable and reusable across tools.

   - `manager_chain(employees, employee_id) -> list[str]` — IDs from
     `employee_id` up to the root (CEO).
   - `is_in_manager_chain(employees, requester_id, target_id) -> bool`
     — true iff `requester_id` appears in `target_id`'s manager chain
     (or is the target).
   - `same_department(employees, requester_id, target_id) -> bool`
   - `has_department_clearance(employees, requester_id, dept_name) -> bool`
   - `restricted_to_allows(restricted_to: list[str] | None, user_id) -> bool`
     — true if `restricted_to` is None/empty (unrestricted) or `user_id`
     in the list.
   - `classifications_up_to(clearance_level)` — moved from
     `src/agent/retriever.py` to be shared. Retriever re-imports.

2. **`src/agent/tools/lookup_employee.py`** — `@tool`-decorated stub
   (raises `NotImplementedError` like `search_documents`) +
   `make_lookup_employee_handler(employees)` factory. Handler returns
   redacted employee record (salary stripped unless caller is HR).

3. **`src/agent/tools/get_approval_chain.py`** — same pattern.
   Handler walks the approval matrix thresholds (hardcoded mapping
   to `approval_matrix_2026.md` bands) and resolves role names to
   employee IDs by walking the org chart from the named employee
   upward.

4. Wire both new tools into `_build_chain` (one registry entry +
   one `bind_tools` addition each — exactly what Phase 2.5 #2 made
   trivial).

5. Tests for `auth.py` primitives, both new tools' happy/denied
   paths, and an explicit impersonation-resistance test for each
   (LLM forges `user_id`; tool still receives state's `user_id`).

### Part 2 — Operational tools + docs

6. `list_my_tickets`, `get_ticket_detail`, `list_calendar_events`,
   `escalate_to_human` — same module-per-tool pattern.
7. `list_calendar_events` includes the "busy placeholder" reduction:
   non-attendees on RESTRICTED events get `{start, end, classification}`
   only — never subject or attendee list.
8. `docs/TOOL_SURFACE.md` covering all 7 tools (the original
   `search_documents` plus the 6 new).
9. Update `docs/THREAT_MODEL.md` with per-tool entries (T-007 onward
   if needed) covering tool-specific attack surface.
10. Final cross-tool integration test: a single multi-hop query that
    exercises `search_documents` + `lookup_employee` +
    `get_approval_chain` in sequence.

## Conventions every tool follows

- Module path: `src/agent/tools/<tool_name>.py`.
- Exports: the `@tool`-decorated function (LLM schema) and a
  `make_<tool>_handler(*, …deps…)` factory returning the closure that
  `AuthenticatedToolNode` invokes.
- Tool body raises `NotImplementedError` (the same defensive pattern
  `search_documents` uses) — handlers never go through default
  LangChain dispatch.
- Handler signature: `(args: dict[str, Any], *, user_id: str) -> Any`.
- Handler MUST call an `auth.*` primitive (or its own check) before
  any data fetch. Every authz path raises `AccessDenied` with a clear
  reason.
- Handler MUST NOT read `user_id` from `args` (defense-in-depth: even
  though `AuthenticatedToolNode` strips it, handlers should never
  look there for identity).
- Tests for each tool MUST include:
  - happy path
  - authorization denial
  - missing entity (returns `AccessDenied` or empty)
  - LLM-supplied `user_id` rejection (the impersonation test)

## Out-of-scope for Phase 3

- The wider authz primitives needed for Phase 3 are scoped above; do
  not pre-build primitives for hypothetical Phase 4+ tools.
- Per-hop audit emission (Phase 4).
- Live integration test against Ollama (deferred to Phase 6 eval
  harness).
