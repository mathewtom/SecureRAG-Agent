"""Meridian document retriever with classification-level filtering.

Phase 2 scope: filter by caller's clearance_level only. Phase 3 will
extend with org-chart BFS and project-membership checks; those rules
belong with the employee-lookup tools, not here, so this module stays
narrow.
"""

from __future__ import annotations

from typing import Any

from src.agent.tools.auth import classifications_up_to  # re-export
from src.data.loaders import Employee
from src.exceptions import AccessDenied

__all__ = ["MeridianRetriever", "classifications_up_to"]


class MeridianRetriever:
    """Thin wrapper over a ChromaDB collection that enforces
    classification visibility per caller."""

    def __init__(
        self,
        *,
        collection: Any,  # chromadb.api.models.Collection
        employees_by_id: dict[str, Employee],
    ) -> None:
        self._collection = collection
        self._employees = employees_by_id

    def search(
        self,
        *,
        query: str,
        user_id: str,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        requester = self._employees.get(user_id)
        if requester is None:
            raise AccessDenied(f"unknown user {user_id!r}")

        allowed = classifications_up_to(requester.clearance_level)

        result = self._collection.query(
            query_texts=[query],
            n_results=k,
            where={"classification": {"$in": allowed}},
        )
        return _flatten(result)


def _flatten(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Chroma returns one list of lists per field (outer index is
    query index). We always submit a single query, so we flatten the
    outer list."""
    ids = result["ids"][0]
    docs = result["documents"][0]
    metas = result["metadatas"][0]
    return [
        {"doc_id": doc_id, "content": content, "metadata": meta}
        for doc_id, content, meta in zip(ids, docs, metas, strict=True)
    ]
