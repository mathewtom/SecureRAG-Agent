"""Agent-callable tools. Each tool's authorization is enforced in
AuthenticatedToolNode, not in the tool body.
"""

from src.agent.tools.search_documents import search_documents

__all__ = ["search_documents"]
