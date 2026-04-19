# SecureRAG-Agent

> **Rate limiter — disabled.** The per-user sliding-window limiter
> (`src/rate_limiter.py`) is currently wired in **no-op mode** so that
> red-team scans (Garak, PromptFoo) can fire the adversarial corpus without
> being swallowed by 429s. This is a deliberate testing posture — see
> [src/api.py](src/api.py) for the exact line, and the control table below
> for how to re-enable.

A security-hardened agentic RAG service for a fictional company called
Meridian Corp. Forked from
[SecureRAG-Sentinel](https://github.com/mathewtom/SecureRAG-Sentinel) at
commit [`6159f4a`](docs/FORK_ORIGIN.md) so the agentic threat surface
(tool misuse, cross-hop indirect injection, identity confusion, goal
hijacking, recursive budget exhaustion) could be addressed without
muddying the upstream classical-RAG project.

Status: complete, phases 0 through 6. End-to-end write-up in
[`docs/PROJECT_COMPLETE.md`](docs/PROJECT_COMPLETE.md).

## Overview

The agent serves a single demo employee (Sigmoid Freud, employee_id
E003) over a single endpoint. A query arrives at `/agent/query`; the
agent decides which of seven tools to call, possibly chaining several,
and produces a final answer.

The tools cover document retrieval, employee lookup, ticket access,
calendar inspection, approval-chain resolution, and human escalation.
Each tool enforces its own authorization rule in Python code, not in
prompt instructions. The LLM cannot grant itself permissions by what
it says or by what it puts in tool-call arguments.

The data is a synthetic Meridian corpus designed to expose multi-hop
reasoning and cross-tool authorization edges: 45 employees in a
four-level org chart, 16 projects (with deliberate name collisions to
force disambiguation), 82 tickets, 58 calendar events (some with
RESTRICTED recipient lists), and 24 cross-referenced policy and
project documents alongside 6 adversarial fixtures excluded from
default ingestion.

Stack: Python 3.12, LangGraph for the ReAct loop, LangChain for tool
binding, FastAPI for the HTTP surface, Ollama running `llama3.3:70b`
for the agent and `llama-guard3:1b` for output safety, ChromaDB for
the vector store, Microsoft Presidio for ingestion-time PII handling,
uv for dependencies.

## Security controls

Every defense below has a name in the code, a test that proves it
holds, and a place in the JSONL audit log where it shows up.
Mappings are to OWASP LLM Top 10 (2025 edition) and MITRE ATLAS.

| Control | Implementation | OWASP LLM | MITRE ATLAS |
|---|---|---|---|
| Identity injected by runtime, not by LLM | `src/agent/graph.py` `AuthenticatedToolNode` strips any `user_id` key from LLM-supplied tool args (case-insensitive), records a denial, and dispatches with the trusted `state["user_id"]` | LLM01, LLM06 | AML.T0051 |
| Per-tool authorization in handler code | `src/agent/tools/*.py`, composing primitives from `src/agent/tools/auth.py` (manager-chain BFS, same-department, classification, project membership, restricted-recipient) | LLM02, LLM06 | AML.T0024 |
| Classification filter at retrieval | `src/agent/retriever.py` — ChromaDB metadata filter restricts results to caller's clearance tier | LLM02 | AML.T0024 |
| Recipient-list gating on RESTRICTED documents | document frontmatter `restricted_to` field honored at the tool layer | LLM02 | AML.T0024 |
| Step budget cap (20 hops) | enforced in `AuthenticatedToolNode`; `BudgetExhausted` surfaced as HTTP 422 | LLM10 | AML.T0029 |
| Per-user rate limiter | `src/rate_limiter.py` sliding window — **DISABLED in `src/api.py` for red-team scanning; pass through `RateLimiter()` to re-enable** | LLM10 | AML.T0029 |
| Input scanners (entry layer) | `src/sanitizers/injection_scanner.py` regex-scoring, `src/sanitizers/embedding_detector.py` semantic similarity to a known-injection corpus | LLM01 | AML.T0051 |
| Output scanners (exit layer) | `src/sanitizers/output_scanner.py` regex fast path plus optional Llama Guard semantic check; `src/sanitizers/classification_guard.py`; `src/sanitizers/credential_detector.py` covering ~21 secret patterns | LLM02, LLM05, LLM07 | AML.T0024 |
| Per-hop structured audit | `src/agent/audit_sink.py` writes JSONL events to `logs/audit-YYYY-MM-DD.jsonl` with `request_start`, `tool_call`, `request_end`. Query content is SHA-256 hashed; the raw query is never logged | detection layer | n/a |
| Model digest verification at startup | `src/model_integrity.py` checks Ollama model digest against pinned value when `SECURERAG_MODEL_DIGEST` is set | LLM03 | n/a |
| Hash-pinned dependencies | `uv.lock` with hashes; `uv sync` verifies | LLM03 | n/a |
| Poisoned-fixture exclusion at ingestion | `src/ingestion/pipeline.py` filters out anything in `data/meridian/documents/poisoned/` | LLM04 | n/a |

The architectural premise: symbolic controls (deterministic, hold
under adversarial pressure) sit underneath neural controls (Llama
Guard, embedding similarity, prompt rules). Where the two disagree,
symbolic wins. Full rationale in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Threat catalog
T-001 through T-012 with attack-chain walkthroughs in
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md). Per-tool authz
contracts in [`docs/TOOL_SURFACE.md`](docs/TOOL_SURFACE.md).

## Fire it up

Prerequisites: Apple Silicon Mac recommended (Metal GPU for the 70B
model), `uv` installed
([astral-sh/uv](https://github.com/astral-sh/uv)), Ollama running
locally with the two models pulled.

```bash
ollama pull llama3.3:70b
ollama pull llama-guard3:1b
```

Bring up the stack:

```bash
git clone https://github.com/mathewtom/SecureRAG-Agent.git
cd SecureRAG-Agent

uv sync                                       # install from uv.lock

# Ingest the Meridian corpus into ChromaDB.
# First run downloads the sentence-transformers embedding model (~80MB).
uv run python scripts/ingest_meridian.py

# Start the API
uv run uvicorn src.api:app --host 127.0.0.1 --port 8000
```

Smoke test from another terminal:

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok"}

curl -s -X POST http://127.0.0.1:8000/agent/query \
  -H 'content-type: application/json' \
  -d '{"query":"What is the 2026 monthly parking reimbursement cap?"}' | jq
```

Tail the audit log to watch defenses fire in real time:

```bash
tail -f logs/audit-$(date -u +%Y-%m-%d).jsonl | jq -c .
```

Run the eval harness (52 canned queries across 11 categories):

```bash
# Stub mode: deterministic, no Ollama needed, runs in seconds
uv run python -m eval.run_eval

# Live mode: uses the running stack
uv run python -m eval.run_eval --live --report eval/results/live_$(date +%Y-%m-%d).md
```

Configuration is environment-variable driven:

| Variable | Default | Purpose |
|---|---|---|
| `SECURERAG_MODEL` | `llama3.3:70b` | Main agent LLM |
| `SECURERAG_GUARD_MODEL` | `llama-guard3:1b` | Llama Guard model |
| `SECURERAG_GUARD_SEMANTIC` | `1` | Set `0` to disable the Llama Guard semantic check at output (regex fast path always runs) |
| `SECURERAG_DEMO_USER` | `E003` | The user_id injected for `/agent/query` |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |
| `SECURERAG_MODEL_DIGEST` | unset | When set, agent verifies Ollama model digest at startup |
| `SECURERAG_RATE_MODE` | unset | Set `test` to relax the rate limiter for security scanning |

Adversarial scanning (Garak, PromptFoo) is operated from a separate
repo, [`ai-redteam-lab`](https://github.com/mathewtom/ai-redteam-lab),
which targets `/agent/query` rather than the underlying LLM.

## Red-team status

**PromptFoo pass 1 (2026-04-19, 5h 38m, 328 tests):** 72% pass / 28%
fail. Dominant gaps: base64 strategy 62% fail and polite
prompt-extraction 56% fail.

**Same-day fixes:** decode-then-scan in `InjectionScanner`, per-caller
`ClassificationGuard`, prompt-extraction patterns + output system-prompt
echo detector, non-disclosure clause in the system prompt, Llama Guard
semantic on by default, and `num_ctx` capped on agent + guard so both
coexist in VRAM.

**PromptFoo pass 2 (2026-04-19, 6h 4m, 328 tests):** **77% pass / 22%
fail / 1% errors** (4 timeouts on the late jailbreak-templates tail).
Biggest wins: prompt-extraction **56% → 16%** (−40pp), pii:direct
36% → 16% (−20pp), debug-access 20% → 0%, jailbreak:meta strategy
52% → 39% (−12.5pp). New regressions surfaced in rbac, bola,
excessive-agency — fresh attack prompts (PromptFoo regenerates per
run) exposed authorization-decision edges the LLM still rationalizes
through; tracked for pass 3. Raw reports:
[`reports/Promptfoo/run_noguard_20260419_043242/`](reports/Promptfoo/run_noguard_20260419_043242/) (pass 1) and
[`reports/Promptfoo/run_fixed_20260419_pass2/`](reports/Promptfoo/run_fixed_20260419_pass2/) (pass 2).
