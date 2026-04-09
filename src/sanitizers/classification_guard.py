"""Output-side classification enforcement — catches leaked classified content."""

import re
from dataclasses import dataclass, field


@dataclass
class ClassificationGuardResult:
    """Result of classification leakage check on LLM output."""
    flagged: bool = False
    leaked_classifications: list[str] = field(default_factory=list)


# Markers that indicate classified content in LLM output
_OUTPUT_CLASSIFICATION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("executive_confidential", re.compile(r"EXECUTIVE\s+CONFIDENTIAL", re.IGNORECASE)),
    ("engineering_confidential", re.compile(r"ENGINEERING\s+CONFIDENTIAL", re.IGNORECASE)),
    ("legal_confidential", re.compile(r"LEGAL\s+CONFIDENTIAL", re.IGNORECASE)),
    ("hr_confidential", re.compile(r"HR\s+CONFIDENTIAL", re.IGNORECASE)),
    ("finance_confidential", re.compile(r"FINANCE\s+CONFIDENTIAL", re.IGNORECASE)),
    ("attorney_client", re.compile(r"ATTORNEY[\s-]CLIENT\s+PRIVILEGED?", re.IGNORECASE)),
    ("confidential", re.compile(r"\bCONFIDENTIAL\b", re.IGNORECASE)),
]


class ClassificationGuard:
    """Checks LLM output for classification markers the user shouldn't see.

    Even if the retriever correctly filtered documents, the LLM may
    echo classification headers from context or hallucinate them.
    This guard flags any output containing classification markers
    above the user's clearance level.
    """

    def __init__(
        self,
        user_accessible_classifications: set[str] | None = None,
    ) -> None:
        self._accessible = user_accessible_classifications or {"public"}

    def scan(self, output: str) -> ClassificationGuardResult:
        """Scan output for classification markers above user's clearance."""
        leaked: list[str] = []

        for classification, pattern in _OUTPUT_CLASSIFICATION_PATTERNS:
            if pattern.search(output):
                # "confidential" alone is generic — skip if user has any classified access
                if classification == "confidential" and len(self._accessible) > 1:
                    continue
                if classification not in self._accessible:
                    leaked.append(classification)

        return ClassificationGuardResult(
            flagged=len(leaked) > 0,
            leaked_classifications=leaked,
        )
