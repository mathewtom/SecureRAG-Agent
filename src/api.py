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

    Kept as a module-level hook so tests can monkey-patch it to inject
    a mock chain without spinning up ChromaDB + Ollama. The real
    implementation is wired in Task 12.
    """
    raise NotImplementedError(
        "_build_chain must be implemented in Task 12 or monkey-patched "
        "in tests."
    )
