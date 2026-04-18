# Phase 4 Implementation Plan — Per-Hop Audit Sink

> **Goal:** Emit every tool invocation and scanner verdict as a
> structured JSONL event, day-rotated to `logs/audit-YYYY-MM-DD.jsonl`.
> Sufficient telemetry to reconstruct any agent session post-hoc, and
> the foundation the Phase 5+ red-team harness will attribute attacks
> against.

**Architecture:** A dedicated `AuditSink` at `src/agent/audit_sink.py`
owns file I/O. `AuthenticatedToolNode` emits one event per tool call
(success / denied / error). `AgenticChain` brackets requests with
`request_start` and `request_end` events. Python's `logging` usage in
`src/audit.py` stays as the stream-oriented logger for legacy cases;
the new sink is append-only file I/O.

**Scope:**

1. **`src/agent/audit_sink.py`** — `AuditSink` class with two methods:
   `emit(event: dict) -> None` and `log_path() -> Path`. Writes one
   JSON line per event, day-rotated by UTC date. Creates `logs/` lazily.
2. **Distinguish `AccessDenied` from generic errors** in
   `AuthenticatedToolNode` (the Phase 3 carryover). Status becomes
   `denied` for `AccessDenied`, `error` for anything else.
3. **Session boundary events** — `AgenticChain.invoke` emits
   `request_start` before scanner entry and `request_end` after
   termination with outcome (`answered` / `blocked` / `flagged` /
   `budget_exhausted` / `error`).
4. **`tests/agent/test_audit_trail.py`** — verify every tool call
   produces an event; events are ordered by `ts`; file is append-only
   (re-invoking the chain appends, doesn't truncate); request_start +
   request_end bracket the tool-call events of that request_id.
5. **`.gitignore` update** — exclude `logs/`.

**Event schema (single shape for tool calls, a couple of extra fields
for session boundaries):**

```json
{
  "ts": "2026-04-17T14:23:45.678Z",
  "event": "tool_call" | "request_start" | "request_end",
  "request_id": "abc123def456",
  "user_id": "E003",
  "tool_name": "search_documents",
  "hop_index": 0,
  "args_sha256": "a1b2c3d4e5f67890",
  "status": "success" | "denied" | "error",
  "duration_ms": 42,
  "reason": null
}
```

`request_start` events include only `ts`, `event`, `request_id`,
`user_id`, `query_sha256` (so the request is traceable without
logging the raw query). `request_end` events include `ts`, `event`,
`request_id`, `outcome`, `step_count`.

**Non-goals:**
- Multi-turn session IDs (no multi-turn sessions exist yet; when they
  do, `session_id` gets a dedicated field — for Phase 4, `request_id`
  is the correlation key).
- Log rotation beyond day-boundary file-per-day (size-based rotation
  is out of scope).
- Structured alerting / streaming to a SIEM (Phase 7+ operational
  concern).
- Retroactive audit for Phase 2/3 events that already landed via
  `log_verdict` / `log_denial` — those stay in Python's `logging`
  stream; Phase 4 adds the NEW per-hop sink without disturbing them.

**Out of scope for this phase**: Phase 2.5's existing
`audit.log_verdict` and `audit.log_denial` calls continue to work
unchanged (they stream to Python's logging system). The new `AuditSink`
is a separate file-backed persistence layer for the richer per-hop
state that Phase 4 formalizes. Consolidation of the two paths is a
Phase 7+ cleanup if needed.
