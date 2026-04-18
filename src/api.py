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

from src.exceptions import (
    AccessDenied,
    BudgetExhausted,
    OutputFlagged,
    QueryBlocked,
)
from src.rate_limiter import RateLimitExceeded

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

    from src import audit
    from src.agent.graph import build_graph
    from src.agent.retriever import MeridianRetriever
    from src.agent.wrapper import AgenticChain
    from src.data.loaders import load_employees
    from src.model_integrity import verify_model_digest
    from src.rate_limiter import RateLimiter
    from src.sanitizers.classification_guard import ClassificationGuard
    from src.sanitizers.credential_detector import CredentialDetector
    from src.sanitizers.injection_scanner import InjectionScanner
    from src.sanitizers.output_scanner import OutputScanner

    model = os.environ.get("SECURERAG_MODEL", "llama3.1:8b")
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
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
    retriever = MeridianRetriever(
        collection=chroma_client.get_collection("meridian_documents"),
        employees_by_id=employees,
    )

    llm = ChatOllama(model=model, base_url=ollama_host, temperature=0)
    graph = build_graph(llm=llm, retriever=retriever)

    rate = RateLimiter()

    # Scanners are typed as Any so mypy does not complain about the .name
    # attribute we set below. The scanner classes are Sentinel-inherited and
    # do not declare .name; setting it here keeps naming logic in one place
    # without modifying inherited source files.
    injection_scanner: Any = InjectionScanner(threshold=5)
    injection_scanner.name = "injection_scan"

    # EmbeddingInjectionDetector requires a live embedding function at
    # construction time (it pre-embeds the corpus), so it is wired as an
    # optional second-pass scanner only when an embedding function is available.
    # For now the regex InjectionScanner provides the entry-layer coverage.
    input_scanners: list[Any] = [injection_scanner]

    output_scanner_obj: Any = OutputScanner(ollama_host=ollama_host)
    output_scanner_obj.name = "output_scan"

    classification_guard_obj: Any = ClassificationGuard(
        user_accessible_classifications={
            "PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED",
        },
    )
    classification_guard_obj.name = "classification_guard"

    credential_detector_obj: Any = CredentialDetector()
    credential_detector_obj.name = "credential_detector"

    output_scanners: list[Any] = [
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
    )


def _extract_answer(final_state: dict[str, Any]) -> str:
    from langchain_core.messages import AIMessage

    for msg in reversed(final_state["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            return str(msg.content)
    return ""
