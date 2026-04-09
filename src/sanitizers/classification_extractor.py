"""Extract document classification markers from text and promote to metadata."""

import re
from dataclasses import dataclass


@dataclass
class ClassificationResult:
    """Result of classification extraction."""
    classification: str
    department: str
    raw_marker: str


# Ordered by specificity — first match wins
_CLASSIFICATION_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"EXECUTIVE\s+CONFIDENTIAL", re.IGNORECASE), "executive_confidential", "executive"),
    (re.compile(r"ENGINEERING\s+CONFIDENTIAL", re.IGNORECASE), "engineering_confidential", "engineering"),
    (re.compile(r"LEGAL\s+CONFIDENTIAL", re.IGNORECASE), "legal_confidential", "legal"),
    (re.compile(r"HR\s+CONFIDENTIAL", re.IGNORECASE), "hr_confidential", "hr"),
    (re.compile(r"FINANCE\s+CONFIDENTIAL", re.IGNORECASE), "finance_confidential", "finance"),
    (re.compile(r"CONFIDENTIAL", re.IGNORECASE), "confidential", ""),
]


def extract_classification(text: str) -> ClassificationResult | None:
    """Scan the first 500 characters of text for classification markers.

    Only checks the document header region to avoid false positives
    from classification markers mentioned in body text.
    Returns None if no marker is found (document is unclassified).
    """
    header = text[:500]

    for pattern, classification, department in _CLASSIFICATION_PATTERNS:
        match = pattern.search(header)
        if match:
            return ClassificationResult(
                classification=classification,
                department=department,
                raw_marker=match.group(0),
            )

    return None
