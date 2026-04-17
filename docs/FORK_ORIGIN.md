# Fork Origin

## Fork point

- **Upstream repository:** https://github.com/mathewtom/SecureRAG-Sentinel
- **Fork-point commit SHA:** `6159f4a0e0381f47f7beace8ef6350befb84b59c`
- **Date of fork:** 2026-04-17

## Why the fork was made

SecureRAG-Sentinel's classical-RAG threat model is complete and frozen as a
standalone portfolio piece. The agentic pivot exists because agentic RAG has a
fundamentally different attack surface — tool abuse, cross-hop indirect
injection, goal hijacking, recursive budget exhaustion — that classical RAG
does not expose. OWASP LLM Top 10 entries that are dormant in classical RAG
(LLM08 Excessive Agency, tool-chain prompt injection) become live in agentic
deployments. The existing Meridian dataset (5 employees, flat tiers, single-shot
answerable) cannot express multi-hop or tool-chaining behavior, so the dataset
has to grow for agentic workloads to exist at all. A follow-on research effort
(`ai-redteam-lab/autodan/`) will adapt AutoDAN-HGA to attack agentic-specific
objectives, and that work requires agentic targets to attack.

## Relationship to upstream

Sentinel's `main` branch is frozen as the classical-RAG reference
implementation. Changes do NOT flow back upstream. This repo tracks upstream
only as a read-only reference (remote `sentinel`) for diffing and
cross-referencing; no pushes, no PRs, no cherry-picks from Agent into Sentinel.
