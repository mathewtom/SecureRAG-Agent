"""Placeholder `search_documents` tool.

The body raises NotImplementedError because this tool is never invoked
through LangChain's default dispatch. `AuthenticatedToolNode`
intercepts the tool call, pulls `user_id` from state, and calls
`MeridianRetriever.search(query=..., user_id=...)` directly.

Exposing the body as NotImplementedError prevents a future refactor
from accidentally calling the unprotected path.
"""

from langchain_core.tools import tool


@tool
def search_documents(query: str) -> str:
    """Search the Meridian knowledge base for documents relevant to a
    natural-language query.

    Args:
        query: the search query, in natural language.
    """
    raise NotImplementedError(
        "search_documents must be invoked via AuthenticatedToolNode; "
        "direct calls bypass the runtime user_id injection."
    )
