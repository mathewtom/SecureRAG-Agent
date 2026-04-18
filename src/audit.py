"""Structured JSON audit logger for denial-path events."""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("securerag.audit")

_HASH_PREFIX_LEN = 12


def _question_hash(question: str) -> str:
    """SHA-256 prefix of the question. Never logs raw content."""
    return hashlib.sha256(question.encode()).hexdigest()[:_HASH_PREFIX_LEN]


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def log_denial(
    *,
    request_id: str,
    user_id: str,
    layer: str,
    reason: str,
    question_hash: str | None = None,
    question: str | None = None,
    details: dict | None = None,
) -> dict:
    """Emit a structured JSON log entry for a denial event. Returns the record."""
    if question_hash is None and question is not None:
        question_hash = _question_hash(question)

    record = {
        "event": "denial",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "user_id": user_id,
        "layer": layer,
        "reason": reason,
        "question_sha256_prefix": question_hash or "",
    }
    if details:
        record["details"] = details

    logger.warning(json.dumps(record, separators=(",", ":")))
    return record


def log_verdict(
    request_id: str,
    user_id: str,
    layer: str,
    stage: str,
    result: Any,
) -> None:
    """Emit a structured JSON log entry for each scanner verdict (entry or exit)."""
    entry = {
        "event": "scanner_verdict",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "user_id": user_id,
        "layer": layer,
        "stage": stage,
        "verdict": (
            "block" if getattr(result, "blocked", False)
            else "flag" if getattr(result, "flagged", False)
            else "pass"
        ),
        "reason": getattr(result, "reason", None),
    }
    logger.info(json.dumps(entry, separators=(",", ":")))


def log_budget_exhausted(
    request_id: str,
    user_id: str,
    step_count: int,
) -> None:
    """Emit a structured JSON log entry when the agent hits its step budget."""
    entry = {
        "event": "budget_exhausted",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "user_id": user_id,
        "step_count": step_count,
    }
    logger.warning(json.dumps(entry, separators=(",", ":")))
