"""Access-controlled retriever — enforces org-chart and department-based visibility."""

from langchain_core.documents import Document


# employee_id → manager_id (None = top of hierarchy)
DEFAULT_ORG_CHART: dict[str, str | None] = {
    "E012": None,       # CEO
    "E001": "E012",     # VP Engineering
    "E002": "E001",     # Engineering Manager
    "E003": "E002",     # Software Engineer
    "E004": "E002",     # Software Engineer
    "E011": "E001",     # SRE
    "E005": "E012",     # HR Director
    "E008": "E005",     # HR Coordinator
    "E006": "E012",     # General Counsel
    "E009": "E006",     # Legal Counsel
    "E007": "E012",     # CFO
    "E010": "E007",     # Financial Analyst
}

# employee_id → department
DEFAULT_DEPARTMENT_MAP: dict[str, str] = {
    "E001": "engineering",
    "E002": "engineering",
    "E003": "engineering",
    "E004": "engineering",
    "E011": "engineering",
    "E005": "hr",
    "E008": "hr",
    "E006": "legal",
    "E009": "legal",
    "E007": "finance",
    "E010": "finance",
    "E012": "executive",
}

# classification → which departments can view it
DEFAULT_CLASSIFICATION_ACCESS: dict[str, set[str]] = {
    "public": set(),                                    # everyone
    "engineering_confidential": {"engineering"},
    "hr_confidential": {"hr"},
    "legal_confidential": {"legal"},
    "finance_confidential": {"finance", "executive"},
    "executive_confidential": {"executive"},
    "confidential": set(),                              # no department → CEO only
}


class AccessControlledRetriever:
    """Filters ChromaDB results by org-chart and department visibility.

    Three access dimensions evaluated per query:
    1. Policy documents: visible to all authenticated users
    2. HR records: visible to subject + management chain (org-chart BFS)
    3. Classified documents: visible based on department membership

    Executive department sees all classified documents.
    Fail-closed: unknown users and untyped documents are excluded.
    """

    def __init__(
        self,
        collection,
        embedding_function,
        org_chart: dict[str, str | None] | None = None,
        department_map: dict[str, str] | None = None,
        classification_access: dict[str, set[str]] | None = None,
        n_results: int = 5,
    ) -> None:
        self._collection = collection
        self._embedding_function = embedding_function
        self._org_chart = org_chart or DEFAULT_ORG_CHART
        self._department_map = department_map or DEFAULT_DEPARTMENT_MAP
        self._classification_access = classification_access or DEFAULT_CLASSIFICATION_ACCESS
        self._n_results = n_results
        self._reports: dict[str, set[str]] = {}
        for emp_id, manager_id in self._org_chart.items():
            if manager_id is not None:
                self._reports.setdefault(manager_id, set()).add(emp_id)

    def _get_visible_employees(self, user_id: str) -> set[str]:
        """Return employee IDs visible to user_id (self + transitive reports)."""
        if user_id not in self._org_chart:
            return set()

        visible = {user_id}
        queue = list(self._reports.get(user_id, []))

        while queue:
            emp = queue.pop()
            if emp not in visible:
                visible.add(emp)
                queue.extend(self._reports.get(emp, []))

        return visible

    def _get_accessible_classifications(self, user_id: str) -> set[str]:
        """Return classification levels the user can access."""
        user_dept = self._department_map.get(user_id, "")
        if not user_dept:
            return {"public"}

        accessible = {"public"}
        for classification, allowed_depts in self._classification_access.items():
            if not allowed_depts:
                # Empty set = public (everyone) or CEO-only (confidential)
                if classification == "public":
                    accessible.add(classification)
                elif user_dept == "executive":
                    accessible.add(classification)
            elif user_dept in allowed_depts or user_dept == "executive":
                accessible.add(classification)

        return accessible

    def _build_where_filter(self, user_id: str) -> dict:
        """Build a ChromaDB where filter scoped to the user's visibility."""
        visible = self._get_visible_employees(user_id)
        visible_list = sorted(visible)
        accessible_classifications = sorted(self._get_accessible_classifications(user_id))

        return {
            "$or": [
                # Public policy documents
                {"doc_type": {"$eq": "policy"}},
                # HR records scoped by org-chart
                {
                    "$and": [
                        {"doc_type": {"$eq": "hr_record"}},
                        {"subject_employee_id": {"$in": visible_list}},
                    ]
                },
                # Classified documents scoped by department
                {"classification": {"$in": accessible_classifications}},
            ]
        }

    def query(
        self,
        query_text: str,
        user_id: str,
    ) -> list[Document]:
        """Query the vector store with org-chart and department access control."""
        visible = self._get_visible_employees(user_id)
        if not visible:
            return []

        query_embedding = self._embedding_function.embed_query(query_text)
        where_filter = self._build_where_filter(user_id)

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=self._n_results,
            where=where_filter,
        )

        documents: list[Document] = []
        if results["documents"] and results["documents"][0]:
            for i, doc_text in enumerate(results["documents"][0]):
                metadata = {}
                if results["metadatas"] and results["metadatas"][0]:
                    metadata = results["metadatas"][0][i]
                documents.append(Document(
                    page_content=doc_text,
                    metadata=metadata,
                ))

        return documents
