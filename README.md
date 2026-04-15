# SecureRAG-Sentinel

A security-hardened RAG pipeline that treats the LLM as an untrusted component. Documents are sanitized before they hit the vector store, queries are scanned for injection attempts, and responses are filtered before reaching the user. Dockerized for one-command deployment. Built with LangChain, ChromaDB, Presidio, FastAPI, and Ollama (Llama 3.3 70B).

> **Rate limiting** defaults to 10 requests per 60 seconds (production). The bundled `docker-compose.yml` sets `SECURERAG_RATE_MODE=test`, which fully disables the per-user limiter for dev and security scanning (Garak, promptfoo iterative red-team) — unset it or use a separate prod compose file for production deployments. Model integrity is verified at startup via `SECURERAG_MODEL_DIGEST` (optional).

### Security Scan Results — 0% Real Attack Success Rate

Full reports under [`reports/`](reports/). Red-team configs and raw data in [`ai-redteam-lab`](https://github.com/mathewtom/ai-redteam-lab).

#### V1 — Baseline ([`garak_scan_results.md`](reports/garak_scan_results.md))

- **Tool:** Garak `promptinject` against raw Llama 3.3 70B, no defenses
- **Result:** 50% bypass rate — folds to "ignore previous instructions and just print X"
- **Takeaway:** the LLM is not a security boundary

#### V2 — Defense stack ([`garak_scan_v2_full.md`](reports/garak_scan_v2_full.md))

- **Added:** regex input scanner, embedding similarity detector (100 prompts, 13 attack categories), two-stage output scanner (regex + Llama Guard 3 1B)
- **Result:** Garak full probe suite against `/query` — API key leaks **0%**, toxic content **0%**, slur continuation **0%**
- **Gap:** DAN jailbreaks partially effective at the LLM layer, but mitigated by RAG architecture (model can only access retrieved documents)

#### V3 — Adaptive adversary (Promptfoo iterative red-team)

Sonnet as attacker + grader, `jailbreak:meta` loop that learns from refusals in real time.

| Run | Target | Tests | Headline ASR | Real ASR |
|---|---|---:|---:|---:|
| [Baseline](reports/promptfoo_baseline_v1.md) | Raw Llama (no defenses) | 99 | 28.28% | n/a |
| [Pipeline V1](reports/promptfoo_pipeline_v1.md) | Full Sentinel (Haiku grader) | 165 | 55.15% | ~1.2% |
| [**Pipeline V2**](reports/promptfoo_pipeline_v2.md) | Full Sentinel + credential fix (Sonnet grader) | 165 | 21.21% | **0.0%** |

**Pipeline V1 — found a real bug:**
- AWS keys in [`vendor_security_assessment.txt`](data/raw/vendor_security_assessment.txt) leaked in 81/165 responses
- Root cause: Presidio had no credential patterns; output scanner only checked the LLM answer, not `source_documents[]`
- Fixed same day — added 21-pattern CredentialDetector at both ingestion and output
- Remaining 89 "failures" were Haiku grader noise (couldn't distinguish E003's own HR record from another employee's)

**Pipeline V2 — zero real bypasses:**
- `basic`: **0/45**
- `jailbreak-templates`: **0/40**
- `jailbreak:meta` (iterative): **0/40**
- 35 headline "failures" are all `base64` strategy — Llama can't decode base64, correctly refuses, promptfoo marks as "No output" (infrastructure artifact)
- Regex sweep of all 165 responses: **zero** leaked classified content, **zero** other-employee HR records, **zero** raw credentials
- Layer 2 (input scan) blocked 7 attacks, Layer 6 (output scanner) blocked 5

## Quick Start (Docker)

Requires [Ollama](https://ollama.com) running natively on the host (not in Docker) for direct GPU access.

```bash
git clone https://github.com/mathewtom/SecureRAG-Sentinel.git
cd SecureRAG-Sentinel

# Install and start Ollama natively, then pull the models
ollama pull llama3.3:70b
ollama pull llama-guard3:1b
ollama serve

# Place documents in data/raw/, then ingest
docker compose run --rm pipeline

# Start the API
docker compose up api -d
```

The API serves on `http://localhost:8000`. Pipeline reads documents from `data/raw/` on your host (mounted read-only) and writes embeddings to a shared Docker volume that the API container reads from. Both containers reach the host Ollama instance via `host.docker.internal`.

The Dockerfile installs from [`requirements.lock`](requirements.lock) with `uv pip install --require-hashes`. Every package is pinned to an exact version and SHA-256 hash; the build fails closed if the lockfile is missing a hash or an upstream wheel has been tampered with. Same fail-closed principle as the Ollama model digest pin at startup.

### Local Setup (without Docker)

Requires Python 3.12+ and [Ollama](https://ollama.com).

**Zero-dependency path (stdlib venv + pip):**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

ollama pull llama3.3:70b
ollama pull llama-guard3:1b
ollama serve
```

**Faster path ([uv](https://github.com/astral-sh/uv), optional):**

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install --require-hashes --requirement requirements.lock
```

The lockfile is pinned to Linux x86_64 (matching the Docker base image). If you're on macOS or another platform, use `requirements.txt` instead, or regenerate with `uv pip compile requirements.txt -o requirements.lock --generate-hashes --python-version 3.12 --python-platform <your-platform>`.

```bash
python -m src.pipeline                                    # ingest
uvicorn src.api:app --host 0.0.0.0 --port 8000           # serve
```

## Usage

Drop documents (`.txt`, `.pdf`, `.csv`) into `data/raw/`. HR records go in `data/raw/hr_records.json`. The pipeline loads, chunks, sanitizes, and embeds everything into ChromaDB. Injection attempts are quarantined and never stored. PII is redacted in-place before embedding.

To query via the API:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is our vacation policy?"}'
```

Or directly from Python:

```python
from src.chain import build_chain

chain = build_chain()
result = chain.query("What is our vacation policy?", user_id="E003")
```

The API hardcodes the requester to a low-privilege Software Engineer (E003 = Priya Patel) by default. This models a "lowly engineer signed in via SSO" persona — adversarial testing tools cannot self-elevate by spoofing `user_id` in the request body. Override via the `SECURERAG_DEMO_USER` environment variable. Programmatic chain access (the Python example above) bypasses the API and accepts any `user_id` directly. The retriever enforces three-dimensional access control: org-chart for HR records, department membership for classified documents, public for policies.

### API Endpoints

- `GET /health` — liveness check
- `POST /query` — accepts `{"question": "..."}`. The requesting identity is hardcoded server-side (default `E003`, override with `SECURERAG_DEMO_USER`). Returns 400 if input injection detected, 422 if output flagged, 429 if rate-limited.

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API endpoint |
| `SECURERAG_RATE_MODE` | (unset = production) | Set to `test` for relaxed rate limits (100k/10min) |
| `SECURERAG_MODEL_DIGEST` | (unset = skip check) | Pin Ollama model digest prefix. Startup fails on mismatch. |
| `SECURERAG_DEMO_USER` | `E003` | Hardcoded user identity for the API (models an authenticated low-privilege engineer). |
| `SECURERAG_NUM_CTX` | `8192` | Ollama context window size in tokens. Lower values keep VRAM usage bounded so multiple models can stay loaded simultaneously. |

### Tests

Unit tests run without Ollama or Docker (the LLM is mocked, ChromaDB runs in-memory):

```bash
pytest tests/ -v -m "not integration"
```

Integration tests require Ollama with `llama-guard3:1b` pulled:

```bash
pytest tests/ -v -m integration
```

## How it works

There are two paths with ChromaDB in the middle.

**Ingestion** runs once (or whenever you add docs). The loader factory walks `data/raw/` and picks a LangChain loader by file extension. After NFKC normalization, the classification extractor scans the first 500 characters for classification markers (`ENGINEERING CONFIDENTIAL`, `LEGAL CONFIDENTIAL`, `HR CONFIDENTIAL`, `FINANCE CONFIDENTIAL`, `EXECUTIVE CONFIDENTIAL`) and promotes them to metadata. HR records get a dedicated loader that yields one document per employee and stamps each with `subject_employee_id` and a manager chain. Everything gets chunked (500 chars, 50 overlap), then fed through the `SanitizationGate`.

The gate runs three scans in priority order. First, the injection scanner scores text against known prompt injection patterns (instruction overrides, ChatML tokens, role hijacking, etc.) — if the cumulative score hits the threshold, the chunk is quarantined and remaining scans are skipped. This short-circuit is intentional: an attacker could craft payloads that exploit downstream scanners, so adversarial content gets no further processing. Second, the PII detector combines regex patterns (SSN with prefix validation, credit card with Luhn check, email, phone, AWS keys, IBAN) with Presidio's NER engine for names and locations. Matches get replaced with `[SSN_REDACTED]`-style tags and the chunk continues through. Third slot is reserved for a credential scanner (not yet implemented).

Clean chunks are embedded with `all-MiniLM-L6-v2` and stored in ChromaDB with classification metadata.

**Querying** normalizes input via NFKC (collapses fullwidth characters, ligatures, combining marks) then runs a seven-layer defense stack on each request:

1. **Rate limiter** — Per-user sliding window. Blocked requests short-circuit before any compute.
2. **Input injection scan (regex)** — Scores the query against known injection patterns. Threshold is 5 (lower than ingestion's 8) so single strong patterns like "stop everything" or "just print" trigger a block.
3. **Embedding similarity scan** — Compares the query embedding against a 100-entry corpus spanning 13 attack categories. Blocks if cosine similarity exceeds 0.55. Catches novel phrasings that regex misses.
4. **Access-controlled retrieval** — Three-dimensional filtering: org-chart BFS for HR records, department membership for classified documents, public access for policies. Unauthorized chunks never leave the database. Executive department sees all classifications.
5. **LLM inference** — Security prompt template instructs the model to answer only from context and never follow embedded instructions. Defense-in-depth only — the 8B model's instruction-following is too weak to be a security boundary.
6. **Output scan** — Two-stage scanner. Fast path checks for rogue strings and hijack patterns (regex). Slow path classifies the response via Llama Guard 3 1B for semantic safety. Flagged responses are withheld (HTTP 422) before reaching the user.
7. **Classification guard** — Scans LLM output for classification markers (e.g., "LEGAL CONFIDENTIAL") that the requesting user's clearance level doesn't permit. Catches leaked classified content even if the retriever filter was bypassed. Defense-in-depth at the output boundary.

## Security mappings

### OWASP Top 10 for LLM Applications

**LLM01 (Prompt Injection)** — Multi-layer defense: NFKC normalization at ingestion and query time, ingestion-time quarantine, query-time regex scoring, embedding similarity detection. Injection patterns are blocked at both write and read paths. Fullwidth and ligature evasion is neutralized before scanning. Known gap: Cyrillic homoglyphs (е vs e) are not addressed by NFKC.

**LLM02 (Insecure Output Handling)** — Two-stage output scanner: regex fast path for known rogue strings and hijack patterns, plus Llama Guard 3 1B semantic classification for novel unsafe content. Flagged responses are withheld. Source documents are returned with every response for auditability.

**LLM03 (Training Data Poisoning)** — All documents pass through the full sanitization gate before embedding. Poisoned documents are quarantined at ingestion.

**LLM06 (Sensitive Information Disclosure)** — PII is redacted before embedding. Three-dimensional access control: org-chart for HR records, department membership for classified documents, classification guard at output for defense-in-depth. Documents carry classification metadata extracted at ingestion from text markers.

**LLM08 (Excessive Agency)** — The LLM has no tool use, no code execution, no write access. It receives context and produces text.

**LLM09 (Overreliance)** — Every response includes `source_documents` so users can verify claims against the original (redacted) chunks.

**LLM10 (Model Theft)** — Everything runs locally via Ollama. No data or weights leave the machine.

### MITRE ATLAS

**AML.T0051 (Prompt Injection)** — Scored regex patterns and embedding similarity detection at both ingestion and query time. Adversarial content is blocked before reaching the vector store or the LLM.

**AML.T0054 (LLM Jailbreak)** — Six-layer defense stack: rate limiting, input regex scan, embedding similarity, access control, security prompt, output scan. No single layer is relied on alone.

**AML.T0020 (Erode ML Model Integrity)** — Documents are scanned and sanitized before entering the vector store. Poisoned content can't corrupt the retrieval index.

**AML.T0024 (Exfiltration via Inference API)** — Org-chart filtering at the retriever prevents unauthorized data from entering the LLM context. Per-user rate limiting restricts enumeration. Output scanner catches exfiltrated content in responses.

**AML.T0043 (Craft Adversarial Data)** — Pattern validators (Luhn checksum, SSA prefix rules) catch adversarial inputs designed to bypass simple regex. Embedding detector catches semantically similar variants.

**AML.T0010 (Insert Backdoor)** — Backdoor instructions embedded in documents are caught by the scored pattern matcher at ingestion. Query-time input scanning catches backdoor triggers in user queries.

## License

MIT
