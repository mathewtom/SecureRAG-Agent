# SecureRAG-Agent

A security-hardened **agentic** RAG pipeline. Forked from
[SecureRAG-Sentinel](https://github.com/mathewtom/SecureRAG-Sentinel) at
commit [`6159f4a`](docs/FORK_ORIGIN.md) because agentic RAG has a different
attack surface than classical single-shot RAG: tool abuse, cross-hop
indirect injection, goal hijacking, recursive budget exhaustion. The
classical-RAG threat model is complete and frozen in Sentinel; this repo
extends the work into the agentic regime.

## Architectural premise

Every agent-callable tool enforces authorization in its own Python code
before returning data. The LLM's tool-call arguments are untrusted input;
the tool verifies the caller against the requested operation regardless
of what the prompt "promises." Symbolic guarantees (explicit checks, BFS
org traversal, metadata filters) sit underneath neural defenses (Llama
Guard, embedding similarity, prompt rules), never the other way around.

Reading order: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the
durable architectural principles and codebase conventions, then
[`docs/AGENTIC_PIVOT_PLAN.md`](docs/AGENTIC_PIVOT_PLAN.md) for the
phased plan.

## Project plan

| Phase | Goal | Status |
|---|---|---|
| 0 | Fork, freeze Sentinel context, set up `agentic-pivot` branch | done |
| 1 | Expand Meridian dataset to support multi-hop and tool-chaining | **done** |
| 2 | LangGraph agent wired end-to-end, Sentinel security layers wrap entry/exit | next |
| 3 | Full tool surface, each with authorization enforced in implementation | pending |
| 4 | Per-hop structured audit (`request_id`, `user_id`, tool, args_hash, verdicts) | pending |
| 5 | Agentic threat model document mapped to OWASP LLM Top 10 + MITRE ATLAS | pending |
| 6 | Evaluation harness with multi-hop query suite and baseline run | pending |

## What's done

### Phase 0 — Fork setup

- Repository forked from Sentinel preserving full git history.
- `agentic-pivot` long-lived branch created from `main`.
- [`docs/FORK_ORIGIN.md`](docs/FORK_ORIGIN.md) records the fork-point SHA,
  the rationale, and that no changes flow back upstream.
- Dependency management consolidated to `uv` with hash-pinned `uv.lock`
  (the inherited `requirements.txt` + `requirements.lock` pair was
  retired to align with the conventions in
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)).

### Phase 1 — Meridian dataset (agentic edition)

The Sentinel Meridian had 12 employees in a flat structure with documents
that were single-shot answerable — nothing for an agent to *do*. Phase 1
rebuilt the corpus so realistic questions require 2–5 hops across
heterogeneous stores. Schema, ER topology, and sample queries at each
hop depth are in [`docs/DATASET_DESIGN.md`](docs/DATASET_DESIGN.md).
Inherited employee IDs E001–E012 are preserved for continuity with
Sentinel.

**Structured entities** ([`data/meridian/`](data/meridian/))

- **45 employees** in a 4-level hierarchy rooted at the CEO. 9 canonical
  departments. Includes `manager_id`, `clearance_level`, `location`,
  `salary` (CONFIDENTIAL), and `email` (Presidio-shaped PII for detector
  exercise).
- **16 projects** including two deliberate name collisions: `Project
  Atlas` / `Atlas Mobile` and `Phoenix` / `Phoenix 2.0`. Project
  membership drives access control for project-scoped artifacts.
- **82 tickets** across `hr`, `it`, `security`, `engineering`, `legal`,
  `finance` types. Owner / assignee / project references all
  cross-validated.
- **58 calendar events** including 9 RESTRICTED items (Horizon weekly,
  board prep, exec offsite, exec comp review, active investigation).
  Non-attendees see `{start, end, classification}` only — never subject
  or attendee list.

**Document corpus** ([`data/meridian/documents/`](data/meridian/documents/))

24 main-corpus documents with explicit cross-references that force
multi-hop traversal:

- **Policy spine.** Code of Conduct → HR Handbook → Role Definitions →
  Org Chart → `employees.json`.
- **Approval spine.** Expense Policy → Approval Matrix → Role Definitions
  → Vendor Security Assessment.
- **Security spine.** IR Playbook → Security Training → Data
  Classification Policy → Acceptable Use Policy.
- **Project spine.** Phoenix Postmortem → Atlas Design Doc → IR Playbook.
- **Temporal pair.** `expense_policy_{2025,2026}.md` and
  `approval_matrix_{2025,2026}.md` — the 2026 approval matrix introduced
  CEO countersign above $100k, replacing the 2025 single-signature
  authority that ran to $250k. Lets the agent answer historical-lookup
  questions ("could the CFO solo-sign $200k in Q3 2025?") without
  falling back to the current policy.
- **Restricted items.** Horizon Briefing, Acquisition Target Analysis,
  Executive Comp Analysis, both Board Minutes (Q4 2025 + Q1 2026), and
  Q1 2026 Financials each carry an explicit `restricted_to` recipient
  list that tools must honor.

**Adversarial fixtures** ([`data/meridian/documents/poisoned/`](data/meridian/documents/poisoned/))

Six poisoned documents — all marked `TEST_POISONED: true` in frontmatter
and excluded from the default loader output:

- **Three classical** (migrated from Sentinel): ChatML role injection,
  "ignore previous instructions" override, social-pretext injection.
- **Three agentic-specific** (the heart of why this fork exists):
  - `injection_tool_redirect.md` — embedded instructions try to coerce
    the agent into calling `list_calendar_events` with classification=4
    immediately after retrieval.
  - `injection_authz_confusion.md` — claims the session `user_id` has
    been migrated to E012 (CEO) and asks the agent to use it for
    subsequent tool calls.
  - `injection_goal_hijack.md` — supersedes the original task with a
    salary-enumeration-and-exfil objective.

Each poisoned document carries `attack_class`, `target_owasp`, and
`target_atlas` frontmatter so the future AutoDAN red-team harness can
attribute attempts back to a specific intended technique.

**Loaders and integrity tests**

- [`src/data/loaders.py`](src/data/loaders.py) — typed frozen
  dataclasses, hand-rolled YAML frontmatter parser, `include_poisoned`
  defaults to `False`.
- [`tests/data/test_dataset_integrity.py`](tests/data/test_dataset_integrity.py)
  — 25 tests covering manager-cycle absence, hierarchy depth, surname
  collisions, project member integrity, ticket cross-references,
  RESTRICTED recipient lists matching project membership, and that
  every file under `poisoned/` carries the frontmatter flag.

```
uv run pytest tests/data/test_dataset_integrity.py -q
# 25 passed
```

## Setup

```bash
git clone https://github.com/mathewtom/SecureRAG-Agent.git
cd SecureRAG-Agent
uv sync                  # installs from uv.lock
uv run pytest -q         # smoke
```

Python 3.12+. Ollama running locally with `llama3.3:70b` as the default
model (see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full
conventions).

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SECURERAG_MODEL` | `llama3.3:70b` | Main agent LLM. Override to `llama3.1:8b` for faster dev iteration. |
| `SECURERAG_GUARD_MODEL` | `llama-guard3:1b` | Semantic output scanner model (Llama Guard). |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint. |
| `SECURERAG_DEMO_USER` | `E003` | The `user_id` injected for the placeholder `/agent/query` endpoint until real auth is wired. |
| `SECURERAG_MODEL_DIGEST` | (unset) | If set, agent verifies the loaded model's digest matches at startup. Supply-chain defense. |

## Repository layout (current)

```
.
├── docs/
│   ├── ARCHITECTURE.md             # durable architectural principles + conventions
│   ├── AGENTIC_PIVOT_PLAN.md       # phased plan (authoritative roadmap)
│   ├── DATASET_DESIGN.md           # Phase 1 schema, ER, sample queries
│   ├── FORK_ORIGIN.md              # fork-point SHA + rationale
│   └── SECURITY_ROADMAP.md         # inherited from Sentinel; will be revised
├── data/
│   ├── meridian/                   # Phase 1 agentic corpus
│   │   ├── employees.{json,csv}
│   │   ├── projects.json
│   │   ├── tickets.csv
│   │   ├── calendar.json
│   │   └── documents/{*.md, poisoned/}
│   └── raw/                        # Sentinel's classical corpus (untouched)
├── src/
│   ├── data/loaders.py             # typed loaders for the agentic corpus
│   └── …                           # Sentinel-inherited modules
├── tests/
│   ├── data/test_dataset_integrity.py
│   └── …                           # Sentinel-inherited test suites
├── pyproject.toml                  # [project] + deps
└── uv.lock                         # hash-pinned (supply-chain defense)
```
