"""FastAPI application for SecureRAG-Agent.

This is a minimal stub during Phase 2 implementation. The full
/agent/query surface is wired in Task 11.
"""

from fastapi import FastAPI

app = FastAPI(title="SecureRAG-Agent")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
