"""FastAPI application for SecureRAG-Agent.

Exposes:
  GET /health       - liveness
  POST /agent/query - run a query through the agentic pipeline

Exceptions raised by AgenticChain are mapped to HTTP status codes
per docs/PHASE_2_DESIGN.md S"API surface".
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from securerag_agent.exceptions import (
    AccessDenied,
    BudgetExhausted,
    OutputFlagged,
    QueryBlocked,
)
from securerag_agent.rate_limiter import RateLimitExceeded

DEMO_USER_ID = os.environ.get("SECURERAG_DEMO_USER", "E003")

app = FastAPI(title="SecureRAG-Agent")

_chain: Any | None = None


class AgentQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class AgentQueryResponse(BaseModel):
    request_id: str
    answer: str
    source_doc_ids: list[str]
    termination_reason: str | None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(request: AgentQueryRequest) -> AgentQueryResponse:
    chain = _get_chain()
    try:
        result = chain.invoke(query=request.query, user_id=DEMO_USER_ID)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": "60"},
        )
    except QueryBlocked as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OutputFlagged as e:
        raise HTTPException(status_code=422, detail=str(e))
    except BudgetExhausted as e:
        raise HTTPException(status_code=422, detail=str(e))
    except AccessDenied as e:
        raise HTTPException(status_code=403, detail=str(e))

    return AgentQueryResponse(**result)


def _get_chain() -> Any:
    global _chain
    if _chain is None:
        _chain = _build_chain()
    return _chain


def _reset_chain_for_test() -> None:
    global _chain
    _chain = None


def _build_chain() -> Any:
    """Assemble the full AgenticChain.

    Procedural assembly - reads top to bottom for each component.
    Reads SECURERAG_MODEL, OLLAMA_HOST, SECURERAG_MODEL_DIGEST from
    the environment.
    """
    from pathlib import Path

    import chromadb
    from langchain_ollama import ChatOllama

    from securerag_agent import audit
    from securerag_agent.agent.audit_sink import AuditSink
    from securerag_agent.agent.graph import build_graph
    from securerag_agent.agent.retriever import MeridianRetriever
    from securerag_agent.agent.tools.escalate_to_human import make_escalate_to_human_handler
    from securerag_agent.agent.tools.get_approval_chain import make_get_approval_chain_handler
    from securerag_agent.agent.tools.get_ticket_detail import make_get_ticket_detail_handler
    from securerag_agent.agent.tools.list_calendar_events import make_list_calendar_events_handler
    from securerag_agent.agent.tools.list_my_tickets import make_list_my_tickets_handler
    from securerag_agent.agent.tools.lookup_employee import make_lookup_employee_handler
    from securerag_agent.agent.tools.registry import ToolRegistry, make_search_documents_handler
    from securerag_agent.agent.wrapper import AgenticChain
    from securerag_agent.data.loaders import load_calendar, load_employees, load_projects, load_tickets
    from securerag_agent.model_integrity import verify_model_digest
    from securerag_agent.rate_limiter import RateLimiter
    from securerag_agent.sanitizers.classification_guard import ClassificationGuard
    from securerag_agent.sanitizers.credential_detector import CredentialDetector
    from securerag_agent.sanitizers.injection_scanner import InjectionScanner
    from securerag_agent.sanitizers.output_scanner import OutputScanner

    model = os.environ.get("SECURERAG_MODEL", "llama3.3:70b")
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    # Cap the agent LLM's context window so the 70B + guard can coexist in
    # VRAM. The default (131072) reserves ~100GB of weights-plus-KV which
    # evicts llama-guard3:1b on every call; 8192 is plenty for scan prompts
    # (≤2k tokens) plus ReAct turns and leaves headroom for the guard.
    agent_num_ctx = int(os.environ.get("SECURERAG_AGENT_NUM_CTX", "8192"))
    expected_digest = os.environ.get("SECURERAG_MODEL_DIGEST")
    if expected_digest:
        verify_model_digest(
            model,
            ollama_host=ollama_host,
            expected_digest=expected_digest,
        )

    chroma_dir = Path("data/chroma")
    chroma_client = chromadb.PersistentClient(path=str(chroma_dir))
    employees = {e.employee_id: e for e in load_employees()}
    tickets_list = load_tickets()
    tickets_by_id = {t.ticket_id: t for t in tickets_list}
    projects_list = load_projects()
    projects_by_id = {p.project_id: p for p in projects_list}
    events_list = load_calendar()
    retriever = MeridianRetriever(
        collection=chroma_client.get_collection("meridian_documents"),
        employees_by_id=employees,
    )

    handlers: ToolRegistry = {
        "search_documents": make_search_documents_handler(retriever),
        "lookup_employee": make_lookup_employee_handler(employees=employees),
        "get_approval_chain": make_get_approval_chain_handler(employees=employees),
        "list_my_tickets": make_list_my_tickets_handler(
            employees=employees, tickets=tickets_list,
        ),
        "get_ticket_detail": make_get_ticket_detail_handler(
            employees=employees, tickets=tickets_by_id, projects=projects_by_id,
        ),
        "list_calendar_events": make_list_calendar_events_handler(
            employees=employees, events=events_list,
        ),
        "escalate_to_human": make_escalate_to_human_handler(
            employees=employees, audit=audit,
        ),
    }

    llm = ChatOllama(
        model=model, base_url=ollama_host, temperature=0,
        num_ctx=agent_num_ctx,
    )
    audit_sink = AuditSink(logs_dir=Path("logs"))
    graph = build_graph(
        llm=llm, handlers=handlers, audit=audit, audit_sink=audit_sink,
        employees=employees,
    )

    # RATE LIMITER DISABLED FOR SECURITY TESTING (Garak / PromptFoo scans).
    # The red-team corpus fires thousands of adversarial prompts in rapid
    # succession; with the production limiter in place, most requests return
    # 429 before reaching the real defense stack, masking signal about the
    # injection/classification/credential scanners. Re-enable by replacing
    # this with `RateLimiter()` (picks up SECURERAG_RATE_MODE env var) once
    # scanning is complete. See README section "Rate limiter — disabled".
    rate = RateLimiter(max_requests=None)

    injection_scanner = InjectionScanner(threshold=5)

    # EmbeddingInjectionDetector requires a live embedding function at
    # construction time (it pre-embeds the corpus), so it is wired as an
    # optional second-pass scanner only when an embedding function is available.
    # For now the regex InjectionScanner provides the entry-layer coverage.
    input_scanners = [injection_scanner]

    # Llama Guard 3 1B has a known false-positive rate on innocuous
    # corporate queries (privacy-flagging legitimate self-disclosure,
    # etc.). Re-enabled by default after PromptFoo pass 1 showed the
    # regex-only path misses semantic hijack/PII cases. FPs are tracked
    # separately and will be addressed by classifier tuning rather than
    # by disabling the layer. Opt out with SECURERAG_GUARD_SEMANTIC=0
    # for local perf testing.
    guard_model = os.environ.get("SECURERAG_GUARD_MODEL", "llama-guard3:1b")
    enable_guard_semantic = os.environ.get(
        "SECURERAG_GUARD_SEMANTIC", "1",
    ) == "1"
    output_scanner_obj = OutputScanner(
        enable_semantic=enable_guard_semantic,
        ollama_host=ollama_host,
        guard_model=guard_model,
    )

    # Per-caller guard: allowed set is derived from the caller's clearance
    # tier at scan time. The static fallback is {"public"} so unmapped or
    # unknown callers can only ever emit PUBLIC markers.
    from securerag_agent.agent.tools.auth import classifications_up_to
    classification_guard_obj = ClassificationGuard(
        user_accessible_classifications={"public"},
        employees_by_id=employees,
        tier_allow_fn=classifications_up_to,
    )

    credential_detector_obj = CredentialDetector()

    output_scanners = [
        output_scanner_obj,
        classification_guard_obj,
        credential_detector_obj,
    ]

    return AgenticChain(
        graph=graph,
        rate_limiter=rate,
        input_scanners=input_scanners,
        output_scanners=output_scanners,
        audit=audit,
        extract_answer=_extract_answer,
        audit_sink=audit_sink,
    )


def _extract_answer(final_state: dict[str, Any]) -> str:
    from langchain_core.messages import AIMessage

    for msg in reversed(final_state["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            return str(msg.content)
    return ""
