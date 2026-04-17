"""Shared exception types for SecureRAG-Agent.

Exceptions are imported by both the agent wrapper (which raises them)
and the API layer (which maps them to HTTP status codes). Keeping them
in a dedicated module avoids import cycles when the wrapper and API
both pull from a single chain module.
"""


class QueryBlocked(Exception):
    """Raised by an input-stage scanner that refuses to forward the
    query into the agent loop. Maps to HTTP 400."""

    def __init__(self, reason: str, details: dict) -> None:
        self.reason = reason
        self.details = details
        super().__init__(reason)


class OutputFlagged(Exception):
    """Raised by an output-stage scanner that refuses to forward the
    agent's answer to the caller. Maps to HTTP 422."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__(f"Output flagged: {', '.join(reasons)}")


class BudgetExhausted(Exception):
    """Raised when the agent graph hits its `max_steps` cap without
    emitting a final answer. Maps to HTTP 422."""

    def __init__(self, max_steps: int) -> None:
        super().__init__(f"agent budget of {max_steps} steps exceeded")
        self.max_steps = max_steps


class AccessDenied(Exception):
    """Raised when a tool call is not authorized for the requesting
    user_id. Maps to HTTP 403."""
