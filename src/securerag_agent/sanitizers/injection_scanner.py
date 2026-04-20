"""Prompt injection detection using scored pattern matching."""

import base64
import binascii
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class InjectionScanResult:
    """Result of scanning text for prompt injection attempts."""
    blocked: bool = False
    total_score: int = 0
    threshold: int = 0
    matches: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if not self.matches:
            return f"score {self.total_score} >= threshold {self.threshold}"
        return (
            f"injection patterns matched ({', '.join(self.matches)}); "
            f"score {self.total_score} >= threshold {self.threshold}"
        )


# (label, score, compiled_regex)
_SCORED_PATTERNS: list[tuple[str, int, re.Pattern]] = [
    ("ignore previous instructions", 5, re.compile(r"ignore\s+(any\s+)?previous\s+(and\s+following\s+)?instructions", re.IGNORECASE)),
    ("ignore all", 5, re.compile(r"ignore\s+all", re.IGNORECASE)),
    ("<|im_start|>", 5, re.compile(r"<\|im_start\|>", re.IGNORECASE)),
    ("just print/say", 5, re.compile(r"just\s+(print|say|output|type|write)\s+\"", re.IGNORECASE)),
    ("stop everything", 5, re.compile(r"stop\s+everything", re.IGNORECASE)),
    ("system prompt", 4, re.compile(r"system\s+prompt", re.IGNORECASE)),
    ("nevermind override", 4, re.compile(r"nevermind\.?\s+ignore", re.IGNORECASE)),
    ("you are now", 3, re.compile(r"you\s+are\s+now", re.IGNORECASE)),
    ("[SYSTEM]", 3, re.compile(r"\[SYSTEM\]", re.IGNORECASE)),
    ("reveal all", 3, re.compile(r"reveal\s+all", re.IGNORECASE)),
    ("[INST]", 3, re.compile(r"\[INST\]", re.IGNORECASE)),
    ("debug mode", 3, re.compile(r"debug\s+mode", re.IGNORECASE)),
    ("confidential data", 3, re.compile(r"confidential\s+data", re.IGNORECASE)),
    ("do not follow", 3, re.compile(r"do\s+not\s+follow\s+(previous|prior|above)", re.IGNORECASE)),
    ("disregard", 5, re.compile(r"disregard\s+(all|any|previous|prior|above)", re.IGNORECASE)),
    # Latent injection patterns (from V5 Garak scan findings)
    ("prepend_response", 5, re.compile(r"prepend.*to\s+(your|the)\s+(response|answer|output)", re.IGNORECASE)),
    ("act_as_echo", 5, re.compile(r"as\s+an?\s+(echo|repeater|parrot)", re.IGNORECASE)),
    ("summarize_inject", 4, re.compile(r"when\s+summarizing.*also", re.IGNORECASE)),
    ("also_include", 4, re.compile(r"also\s+(include|output|append|add)\s+.*in\s+your\s+(response|answer|output)", re.IGNORECASE)),
    ("final_answer_must", 4, re.compile(r"final\s+answer\s+(should|must)\s+(contain|include|begin|start|end)", re.IGNORECASE)),
    ("response_must_contain", 4, re.compile(r"(your|the)\s+(response|answer|output)\s+must\s+(contain|start|begin|end)", re.IGNORECASE)),
    ("do_not_mention", 3, re.compile(r"do\s+not\s+(mention|reveal|tell)\s+(the\s+user|anyone)", re.IGNORECASE)),
    ("forget_above", 5, re.compile(r"forget\s+(everything|all)\s+(above|before|prior)", re.IGNORECASE)),
    ("begin_with", 4, re.compile(r"(always\s+)?(begin|start)\s+with\s+\S+\s+(first|before)", re.IGNORECASE)),
    ("when_you_answer", 4, re.compile(r"when\s+you\s+(answer|respond|reply).*always", re.IGNORECASE)),
    # Prompt-extraction asks surfaced by PromptFoo pass 1 (56% fail rate).
    # Polite variants ("summarize your instructions", "for documentation
    # purposes") evaded the scorer because they lacked injection keywords.
    ("instructions_you_operate_under", 5, re.compile(r"instructions\s+you\s+(operate|run|are\s+running)\s+under", re.IGNORECASE)),
    ("operational_guidelines", 4, re.compile(r"operational\s+guidelines", re.IGNORECASE)),
    ("tool_usage_policies", 4, re.compile(r"tool[-\s]usage\s+(polic(y|ies)|rules|guidelines)", re.IGNORECASE)),
    ("embedded_instructions", 5, re.compile(r"embedded\s+instructions", re.IGNORECASE)),
    ("current_system_prompt", 5, re.compile(r"(current|exact)\s+system\s+(prompt|instructions)", re.IGNORECASE)),
    ("summarize_system_prompt", 5, re.compile(r"summariz(e|ing)\s+(your\s+)?(current\s+)?(system\s+prompt|system\s+instructions)", re.IGNORECASE)),
    ("include_your_system_prompt", 5, re.compile(r"(include|provide|share|reveal)\s+(your\s+)?(system\s+prompt|system\s+instructions|internal\s+instructions)", re.IGNORECASE)),
    ("document_compliance", 3, re.compile(r"document\s+compliance", re.IGNORECASE)),
    ("for_transparency_reveal", 4, re.compile(r"for\s+transparency.*(provide|share|reveal).*(prompt|instructions|configuration)", re.IGNORECASE)),
    ("initial_prompt", 4, re.compile(r"initial\s+(prompt|instructions|configuration)", re.IGNORECASE)),
]

_DEFAULT_THRESHOLD = 8

# Long runs that look like an encoded payload. PromptFoo's base64 strategy
# often sends the entire prompt as one token; the agent LLM decodes and
# complies. We decode these before scoring so the same pattern set fires.
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{32,}")
_HEX_RUN = re.compile(r"(?:(?<![A-Za-z0-9])(?:[0-9a-fA-F]{2}\s?){16,})")
_PCT_ENCODED = re.compile(r"(?:%[0-9a-fA-F]{2}){4,}")
_MIN_DECODE_LEN = 16  # don't bother scanning short decodes


class InjectionScanner:
    """Scores text against known injection patterns and blocks above threshold."""

    name: ClassVar[str] = "injection_scan"

    def __init__(self, threshold: int = _DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold

    def scan(self, text: str) -> InjectionScanResult:
        """Scan text for injection patterns. Blocks if cumulative score >= threshold.

        Scans the raw text, then repeats the scan against base64/hex/percent
        decoded payloads found inside the text. Decoded matches are unioned
        into the same score so obfuscated injections can't evade the gate.
        """
        total_score, matches = self._score(text)

        for decoded, encoding in self._decoded_variants(text):
            d_score, d_matches = self._score(decoded)
            if d_score > 0:
                total_score += d_score
                matches.extend(f"{m}[{encoding}]" for m in d_matches)

        # Deduplicate while preserving order so reason strings stay stable.
        seen: set[str] = set()
        deduped = [m for m in matches if not (m in seen or seen.add(m))]

        return InjectionScanResult(
            blocked=total_score >= self.threshold,
            total_score=total_score,
            threshold=self.threshold,
            matches=deduped,
        )

    def _score(self, text: str) -> tuple[int, list[str]]:
        total = 0
        hits: list[str] = []
        for label, score, pattern in _SCORED_PATTERNS:
            if pattern.search(text):
                total += score
                hits.append(label)
        return total, hits

    def _decoded_variants(self, text: str) -> list[tuple[str, str]]:
        """Yield (decoded_text, encoding_label) for each encoded run found
        in `text`. Silently skips undecodable runs.
        """
        variants: list[tuple[str, str]] = []

        for match in _BASE64_RUN.findall(text):
            try:
                decoded = base64.b64decode(match, validate=False).decode(
                    "utf-8", errors="ignore",
                )
            except (binascii.Error, ValueError):
                continue
            if len(decoded) >= _MIN_DECODE_LEN:
                variants.append((decoded, "base64"))

        for match in _HEX_RUN.findall(text):
            cleaned = re.sub(r"\s", "", match)
            if len(cleaned) % 2:
                continue
            try:
                decoded = bytes.fromhex(cleaned).decode(
                    "utf-8", errors="ignore",
                )
            except ValueError:
                continue
            if len(decoded) >= _MIN_DECODE_LEN:
                variants.append((decoded, "hex"))

        if _PCT_ENCODED.search(text):
            try:
                decoded = urllib.parse.unquote(text, errors="ignore")
            except (UnicodeDecodeError, ValueError):
                decoded = ""
            if decoded and decoded != text and len(decoded) >= _MIN_DECODE_LEN:
                variants.append((decoded, "pct"))

        return variants
