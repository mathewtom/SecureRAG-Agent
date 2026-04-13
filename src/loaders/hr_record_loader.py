"""HR record loader — yields one Document per employee from a JSON file."""

import json
from pathlib import Path

from langchain_core.document_loaders import BaseLoader
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


def _build_manager_chain(
    employee_id: str,
    org_chart: dict[str, str | None],
) -> list[str]:
    """Walk up the org chart to root, returning the full chain."""
    chain: list[str] = []
    current = employee_id
    visited: set[str] = set()

    while current and current not in visited:
        chain.append(current)
        visited.add(current)
        current = org_chart.get(current)

    return chain


class HRRecordLoader(BaseLoader):
    """Loads HR JSON and yields one Document per employee.

    Stamps each document with subject_employee_id, doc_type, and manager_chain.
    """

    def __init__(
        self,
        file_path: str | Path,
        org_chart: dict[str, str | None] | None = None,
    ) -> None:
        self.file_path = Path(file_path)
        self.org_chart = org_chart or DEFAULT_ORG_CHART

    def lazy_load(self):
        """Yield one Document per employee record in the JSON file."""
        with open(self.file_path, "r", encoding="utf-8") as f:
            records = json.load(f)

        for record in records:
            employee_id = record.get("employee_id", "UNKNOWN")
            manager_chain = _build_manager_chain(employee_id, self.org_chart)

            content_parts = []
            for key, value in record.items():
                if key != "employee_id":
                    content_parts.append(f"{key}: {value}")
            page_content = "\n".join(content_parts)

            yield Document(
                page_content=page_content,
                metadata={
                    "subject_employee_id": employee_id,
                    "doc_type": "hr_record",
                    "manager_chain": ",".join(manager_chain),
                    "source": str(self.file_path),
                },
            )
