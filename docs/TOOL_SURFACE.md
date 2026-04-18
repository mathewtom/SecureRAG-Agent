# Tool Surface

> The seven agent-callable tools, their LLM-visible schemas, their
> authorization rules, and the data they expose. This document is
> the operational reference; the security rationale lives in
> [`THREAT_MODEL.md`](THREAT_MODEL.md).

## Conventions every tool follows

- LLM-visible schema NEVER includes `user_id`. Identity is injected
  by `AuthenticatedToolNode` from agent state.
- Tool body raises `NotImplementedError`. The runtime path goes
  through `make_<tool>_handler(...)` factories registered in
  `src/api.py`'s `_build_chain`.
- Handler signature: `(args: dict[str, Any], *, user_id: str) -> Any`.
- Unknown caller `user_id` → `AccessDenied` (never `KeyError`).
- Unknown target ID → `AccessDenied` (treated identically to
  unauthorized; prevents differential probing).
- LLM-supplied `user_id` (case-insensitive) is stripped at the
  dispatcher and logged as a denial in `tool_call_log` AND emitted
  to `audit.log_denial`.

## 1. `search_documents`

| Field | Value |
|---|---|
| LLM schema | `{query: str}` |
| Implementation | [`src/agent/tools/search_documents.py`](../src/agent/tools/search_documents.py) |
| Handler | classification filter via `MeridianRetriever` + ChromaDB |
| Authorization | classification ≤ caller `clearance_level` |
| Failure modes | unknown caller → `AccessDenied` |
| Out of scope | `restricted_to` per-document recipient lists (deferred — currently only classification gating) |

## 2. `lookup_employee`

| Field | Value |
|---|---|
| LLM schema | `{employee_id: str}` |
| Implementation | [`src/agent/tools/lookup_employee.py`](../src/agent/tools/lookup_employee.py) |
| Authorization (allow if any) | self / target's manager chain / same department / HR |
| Salary visibility | self / manager chain / HR only (others see `"[REDACTED]"`) |
| Clearance visibility | same as salary |
| Other fields visible to authorized caller | name, title, department, manager_id, location, hire_date, email, is_active |
| Failure modes | unknown caller → `AccessDenied`; unknown target → `AccessDenied` (no differential leakage); cross-dept non-HR caller → `AccessDenied` |

## 3. `get_approval_chain`

| Field | Value |
|---|---|
| LLM schema | `{employee_id: str, amount_usd: float}` |
| Implementation | [`src/agent/tools/get_approval_chain.py`](../src/agent/tools/get_approval_chain.py) |
| Authorization | self / manager chain / Finance / HR |
| Returns | `{amount_usd, matrix_band, required_approvers: [{role, employee_id, name}], rule_source}` |
| Bands implemented (from `approval_matrix_2026.md §Expense reports`) | ≤$1k Manager / $1,001–$10k Director / $10,001–$50k VP / $50,001–$100k CFO / >$100k CFO + CEO countersign |
| Out of scope | Vendor & SaaS contracts band, Headcount band, Settlements band — those tables exist in the matrix doc but this tool doesn't compute them. The `rule_source` field narrows to `"approval_matrix_2026.md §Expense reports"` so downstream LLM citations stay accurate. Phase 4+ may add specific tools per band table. |
| Failure modes | unknown caller / target → `AccessDenied`; negative amount → `ValueError`; org chart can't satisfy a role (no Director ancestor; no CFO in directory) → `ValueError` (surfaces as `tool error` in the audit log, not a security failure) |

## 4. `list_my_tickets`

| Field | Value |
|---|---|
| LLM schema | `{}` (no args) |
| Implementation | [`src/agent/tools/list_my_tickets.py`](../src/agent/tools/list_my_tickets.py) |
| Authorization | known caller required |
| Returns | list of ticket records where caller is owner OR assignee |
| Empty result | known caller with no tickets returns `[]` (not `AccessDenied`) |
| Failure modes | unknown caller → `AccessDenied` |

## 5. `get_ticket_detail`

| Field | Value |
|---|---|
| LLM schema | `{ticket_id: str}` |
| Implementation | [`src/agent/tools/get_ticket_detail.py`](../src/agent/tools/get_ticket_detail.py) |
| Authorization (allow if any) | ticket owner / assignee / project member (when `ticket.project_id` is set) |
| Failure modes | unknown caller / ticket → `AccessDenied`; ticket references a missing project → falls back to owner+assignee gate (graceful degradation, no crash) |

## 6. `list_calendar_events`

| Field | Value |
|---|---|
| LLM schema | `{date_range: str}` (format `"YYYY-MM-DD..YYYY-MM-DD"`, UTC inclusive both endpoints) |
| Implementation | [`src/agent/tools/list_calendar_events.py`](../src/agent/tools/list_calendar_events.py) |
| Authorization | known caller required |
| Returns | ALL events in range; reduced shape for non-attendees |
| Full record (organizer or attendee) | `{event_id, organizer_id, attendees, subject, classification, start, end}` |
| Busy placeholder (non-attendee) | `{event_id, classification, start, end}` only |
| Why ALL events are returned | Calendar planning requires knowledge that a slot is busy; the placeholder scheme exposes the *existence* + *timing* of meetings without leaking content |
| Failure modes | unknown caller → `AccessDenied`; malformed `date_range` → `ValueError` |

## 7. `escalate_to_human`

| Field | Value |
|---|---|
| LLM schema | `{reason: str}` |
| Implementation | [`src/agent/tools/escalate_to_human.py`](../src/agent/tools/escalate_to_human.py) |
| Authorization | none (always available — escalation is a release valve) |
| Returns | `{escalated: True, reason}` |
| Side effect | emits `audit.log_denial(layer="escalate_to_human", reason=...)` so a human reviewer can see all escalations |
| Failure modes | unknown caller → `AccessDenied` (defensive: prevents phantom-user audit-log noise) |

## Adding a new tool

The Phase 2.5 registry pattern reduces tool addition to a known recipe:

1. Create `src/agent/tools/<name>.py` with:
   - A `@tool`-decorated function exposing only the LLM-relevant args (NEVER `user_id`)
   - The function body raises `NotImplementedError` (defensive: prevents accidental direct dispatch)
   - A `make_<name>_handler(*, …deps…)` factory returning a closure with signature `(args, *, user_id) -> Any`
2. Add the export to `src/agent/tools/__init__.py`.
3. Add the `@tool` to `bind_tools([...])` in `src/agent/graph.py` so the LLM sees the schema.
4. Add a `"<name>": make_<name>_handler(...)` entry to the `handlers` dict in `_build_chain` in `src/api.py`.
5. Write a test file `tests/agent/test_<name>.py` covering: happy path; authorization denial; unknown caller; unknown target; LLM-supplied `user_id` rejection.
6. Add a `## N. <name>` row in this document.
7. Add an entry to `THREAT_MODEL.md` for any new attack surface the tool exposes.
