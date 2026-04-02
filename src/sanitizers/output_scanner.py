"""Post-LLM output scanner — detects rogue strings and hijacked responses."""

import re
from dataclasses import dataclass, field


@dataclass
class OutputScanResult:
    """Result of scanning LLM output for suspicious content."""
    flagged: bool = False
    reasons: list[str] = field(default_factory=list)


# Known rogue strings from Garak promptinject probes
_ROGUE_STRINGS: list[str] = [
    "I hate humans",
    "Kill all humans",
    "this prompt has been so thoroughly hijacked",
    "sanitize your inputs",
]

# Patterns indicating the LLM followed an injected instruction
_HIJACK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("parroted_override", re.compile(
        r"ignore\s+(any\s+)?previous\s+(and\s+following\s+)?instructions",
        re.IGNORECASE,
    )),
    ("role_hijack_echo", re.compile(r"i am now\s+(a|an|in)\s+", re.IGNORECASE)),
    ("system_prompt_leak", re.compile(r"(my\s+)?system\s+prompt\s+is", re.IGNORECASE)),
    ("raw_instruction_echo", re.compile(r"(just\s+)?(print|say|output)\s*:", re.IGNORECASE)),
]


class OutputScanner:
    """Scans LLM output for evidence of prompt injection success.

    Checks for known rogue strings (from red-team testing) and patterns
    indicating the model followed an injected instruction rather than
    answering from context.
    """

    def __init__(
        self,
        extra_rogue_strings: list[str] | None = None,
    ) -> None:
        self._rogue_strings = _ROGUE_STRINGS + (extra_rogue_strings or [])

    def scan(self, output: str) -> OutputScanResult:
        """Scan LLM output. Returns flagged=True if suspicious content found."""
        reasons: list[str] = []
        output_lower = output.lower()

        for rogue in self._rogue_strings:
            if rogue.lower() in output_lower:
                reasons.append(f"rogue_string: {rogue}")

        for label, pattern in _HIJACK_PATTERNS:
            if pattern.search(output):
                reasons.append(f"hijack_pattern: {label}")

        return OutputScanResult(
            flagged=len(reasons) > 0,
            reasons=reasons,
        )
