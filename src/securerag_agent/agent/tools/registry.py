"""Tool registry for AuthenticatedToolNode.

Each tool is registered as (name, handler) where the handler signature is

    handler(args: dict[str, Any], *, user_id: str) -> Any

The handler is responsible for the tool's per-call authorization in
addition to the runtime-injected `user_id` already enforced by
AuthenticatedToolNode. Phase 3 tools (lookup_employee,
get_approval_chain, etc.) implement their authorization rules inside
the handler body.

Registering a new tool is two edits: import the handler factory from the
tool module in `_build_chain`, and add an entry to the handlers dict.
There is no separate dispatch table to keep in sync.
"""

from __future__ import annotations

from typing import Any, Protocol


class ToolHandler(Protocol):
    """A tool handler takes the LLM-supplied args plus a runtime-injected
    user_id and returns whatever payload the tool produces.

    Handlers MUST NOT read user_id from args (the dispatcher already
    strips it). They MUST enforce per-tool authorization in code,
    not in prompt instructions.
    """

    def __call__(self, args: dict[str, Any], *, user_id: str) -> Any: ...


ToolRegistry = dict[str, ToolHandler]


def make_search_documents_handler(retriever: Any) -> ToolHandler:
    """Bind a MeridianRetriever into the search_documents handler."""
    def handler(args: dict[str, Any], *, user_id: str) -> Any:
        return retriever.search(query=args["query"], user_id=user_id)
    return handler
