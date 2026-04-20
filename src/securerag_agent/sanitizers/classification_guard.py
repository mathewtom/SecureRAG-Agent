"""Output-side classification enforcement — catches leaked classified content."""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar


@dataclass
class ClassificationGuardResult:
    """Result of classification leakage check on LLM output."""
    flagged: bool = False
    leaked_classifications: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "leaked: " + ", ".join(self.leaked_classifications) \
            if self.leaked_classifications else ""


# Department-scoped confidential markers (the pre-existing taxonomy).
_DEPARTMENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("executive_confidential", re.compile(r"EXECUTIVE\s+CONFIDENTIAL", re.IGNORECASE)),
    ("engineering_confidential", re.compile(r"ENGINEERING\s+CONFIDENTIAL", re.IGNORECASE)),
    ("legal_confidential", re.compile(r"LEGAL\s+CONFIDENTIAL", re.IGNORECASE)),
    ("hr_confidential", re.compile(r"HR\s+CONFIDENTIAL", re.IGNORECASE)),
    ("finance_confidential", re.compile(r"FINANCE\s+CONFIDENTIAL", re.IGNORECASE)),
    ("attorney_client", re.compile(r"ATTORNEY[\s-]CLIENT\s+PRIVILEGED?", re.IGNORECASE)),
    ("confidential", re.compile(r"\bCONFIDENTIAL\b", re.IGNORECASE)),
]

# Tier markers aligned with the retriever's classification ladder
# (PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED). RESTRICTED has no
# generic department variant and wasn't previously covered; catching it
# here closes a gap where the LLM echoes tier labels verbatim.
_TIER_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("RESTRICTED", re.compile(r"\bRESTRICTED\b")),
]


class ClassificationGuard:
    """Flags output containing classification markers above the user's clearance.

    Two operating modes:

    1. Static (legacy) — construct with ``user_accessible_classifications``;
       every call uses that static set. Used in tests and when no
       per-caller directory is available.

    2. Per-caller — construct with ``employees_by_id`` and a
       ``tier_allow_fn(clearance_level) -> set[str]``. ``scan(output,
       user_id=...)`` then resolves the caller's clearance_level and
       builds the allowed tier set at call time. ``_call_scanner`` in
       the AgenticChain wrapper forwards ``user_id`` automatically.

    Department-scoped confidential markers (``ENGINEERING CONFIDENTIAL``,
    etc.) are always flagged unless the caller belongs to that department
    (or the static set explicitly allows them, for backwards
    compatibility with existing tests).
    """

    name: ClassVar[str] = "classification_guard"

    def __init__(
        self,
        user_accessible_classifications: set[str] | None = None,
        *,
        employees_by_id: dict[str, Any] | None = None,
        tier_allow_fn: Callable[[int], list[str]] | None = None,
    ) -> None:
        self._accessible = user_accessible_classifications or {"public"}
        self._employees = employees_by_id
        self._tier_allow_fn = tier_allow_fn

    def scan(
        self, output: str, user_id: str | None = None,
    ) -> ClassificationGuardResult:
        """Scan output for classification markers above the caller's tier."""
        allowed = self._resolve_allowed(user_id)
        leaked: list[str] = []

        # Department-scoped confidentials (legacy behavior).
        for label, pattern in _DEPARTMENT_PATTERNS:
            if not pattern.search(output):
                continue
            if label == "confidential" and len(allowed) > 1:
                continue
            if label not in allowed:
                leaked.append(label)

        # Tier markers — only enforced when we know the caller's clearance.
        if self._employees is not None and user_id is not None and self._tier_allow_fn is not None:
            allowed_tiers = self._allowed_tiers(user_id)
            if allowed_tiers is not None:
                for tier_label, pattern in _TIER_PATTERNS:
                    if pattern.search(output) and tier_label not in allowed_tiers:
                        leaked.append(tier_label.lower())

        return ClassificationGuardResult(
            flagged=len(leaked) > 0,
            leaked_classifications=leaked,
        )

    def _resolve_allowed(self, user_id: str | None) -> set[str]:
        """Per-caller accessible set. Falls back to the static set if the
        directory isn't wired or the caller can't be resolved."""
        if self._employees is None or user_id is None:
            return self._accessible
        emp = self._employees.get(user_id)
        if emp is None:
            return self._accessible
        dept = getattr(emp, "department", None) or ""
        own_label = _department_label(dept)
        if own_label is None:
            return self._accessible
        # Caller sees their own department's confidential + the generic
        # union that the static set supplies (usually {"public"}).
        return self._accessible | {own_label}

    def _allowed_tiers(self, user_id: str) -> set[str] | None:
        if self._employees is None or self._tier_allow_fn is None:
            return None
        emp = self._employees.get(user_id)
        if emp is None:
            return None
        level = getattr(emp, "clearance_level", None)
        if level is None:
            return None
        try:
            return set(self._tier_allow_fn(int(level)))
        except (ValueError, TypeError):
            return None


def _department_label(department: str) -> str | None:
    """Map an Employee.department string to the guard's department
    confidential label, or None if the department has no mapped label."""
    lowered = department.strip().lower()
    if "engineer" in lowered:
        return "engineering_confidential"
    if "legal" in lowered:
        return "legal_confidential"
    if "human resources" in lowered or lowered == "hr":
        return "hr_confidential"
    if "finance" in lowered:
        return "finance_confidential"
    if "exec" in lowered:
        return "executive_confidential"
    return None
