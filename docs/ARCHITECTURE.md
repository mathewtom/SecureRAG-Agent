# Architecture and Conventions

Durable architectural principles and codebase conventions for
SecureRAG-Agent. These rules constrain every design and code decision in
the repo. Read this before contributing.

## Independence from Sentinel

SecureRAG-Agent is self-contained. There is no runtime dependency on
SecureRAG-Sentinel — the upstream repo is kept as the `sentinel` git
remote purely for historical reference and diffing.

Security primitives that originated in Sentinel (the input/output
scanners, rate limiter, audit logger, model-integrity check, PII and
credential detectors) have been copied into this repo and evolve
independently going forward. Upstream fixes in Sentinel are NOT
automatically pulled; if a bug is discovered in a copied primitive
and also needs fixing upstream, it is fixed in both places
deliberately.

Sentinel's classical-RAG orchestration (`SecureRAGChain`, the
single-shot `/query` endpoint, `AccessControlledRetriever` against the
12-employee `hr_records.json` fixture, the `data/raw/` corpus) was
removed from this repo in Phase 2. The agentic threat model is
different enough that carrying forward the classical orchestration
created two parallel data models that would drift. Anyone needing the
classical-RAG reference runs Sentinel directly from its own repo.

## Architectural principles

### 1. Access control lives in tool implementations, never in LLM instructions

Every agent-callable tool MUST enforce authorization in its own Python
code before returning data. The LLM's tool-call arguments are untrusted
input — the tool verifies the caller (`user_id`) is authorized for the
requested operation regardless of what the LLM "promises" in its prompt.

Wrong:

```python
@tool
def lookup_employee(employee_id: str, user_id: str) -> dict:
    # LLM prompt says "only use this for your own user_id"
    return employees_db[employee_id]  # VULNERABLE
```

Right:

```python
@tool
def lookup_employee(employee_id: str, user_id: str) -> dict:
    requester = get_employee(user_id)
    target = get_employee(employee_id)
    if not can_view(requester, target):     # explicit check in code
        raise AccessDenied(...)
    return redact_for_clearance(target, requester)
```

This principle extends Sentinel's existing retrieval-layer access
control to the full tool surface. It is the single most important
security property in this codebase.

### 2. Symbolic guarantees > neural guarantees

Symbolic controls (BFS org-chart traversal, explicit authorization
checks, ChromaDB metadata filters, input regex scoring) give hard
guarantees. Neural controls (LLM prompt rules, embedding similarity,
Llama Guard classification) give best-effort coverage.

In any security-critical decision, prefer symbolic. Neural layers are
defense-in-depth on top of symbolic, never a replacement for it.

### 3. Every tool call is audited

Agentic systems generate many more security-relevant events per user
query than classical RAG. Every tool invocation must emit a structured
log entry: `(request_id, user_id, tool_name, arguments, result_status,
timestamp)`. Query content is SHA-256 hashed in logs. This is both a
defense (detection) and a research artifact (the AutoDAN-derived
red-team work needs this telemetry to attribute attacks to specific
hops).

### 4. Failure-domain isolation

The agent framework runs in the same venv as the core pipeline. The
red-team tooling (Garak, promptfoo, future AutoDAN work) lives in
`ai-redteam-lab` with its own venv. This separation was validated in
Sentinel when a Python 3.14 / `datasets` incompatibility affected only
the Garak environment. Maintain the split.

## Codebase conventions

- **Python.** 3.12+, type hints mandatory on public functions,
  `ruff` + `mypy --strict` clean.
- **Dependency management.** `uv` (not pip/poetry). `uv.lock` is
  committed and hash-pinned. Adding a dep requires `uv add <pkg>`,
  never hand-editing `pyproject.toml`'s dependency list.
- **Agent framework.** LangGraph (not LangChain's deprecated
  AgentExecutor). This is a hard choice — do not suggest switching to
  CrewAI, AutoGen, or roll-your-own without raising it first.
- **LLM runtime.** Ollama, native macOS (not Docker), Metal GPU. Model
  is `llama3.1:8b` for development, `llama3.3:70b` for evaluation runs.
  Both digests are pinned in `config/models.yaml` (supply-chain
  defense).
- **Vector DB.** ChromaDB, local-only, persisted under `data/chroma/`.
- **PII / sanitization.** Microsoft Presidio (inherited from Sentinel).
- **Testing.** pytest; security-relevant tests live in `tests/security/`
  and MUST be run before any PR-equivalent commit.
- **Commit style.** Conventional commits (`feat:`, `fix:`, `sec:`,
  `docs:`, `refactor:`, `test:`). Security-relevant changes always use
  `sec:`.

## What NOT to do

- Do not re-implement features that exist in Sentinel. Fork them, adapt
  them.
- Do not trust LLM tool-call arguments. Ever.
- Do not add a new agent tool without updating
  [`docs/THREAT_MODEL.md`](THREAT_MODEL.md) (created during the threat-
  model phase).
- Do not disable the access-control layer "just for testing" — add a
  test fixture with a privileged test user instead.
- Do not introduce dependencies without checking if they have known
  CVEs or have been abandoned. `uv` + hash-pinning is supply-chain
  defense; short-circuiting it defeats the purpose.
