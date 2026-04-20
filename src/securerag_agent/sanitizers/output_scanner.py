"""Post-LLM output scanner — regex fast path + Llama Guard semantic classifier."""

import os
import re
from dataclasses import dataclass, field
from typing import ClassVar

import httpx

# Distinctive n-grams from the runtime system prompt. Any of these
# appearing verbatim in LLM output is strong evidence of a prompt-leak.
# Keep short, anchored, and specific so paraphrases aren't caught.
_SYSTEM_PROMPT_SIGNATURES: tuple[str, ...] = (
    "Meridian assistant for SecureRAG-Agent",
    "When the user says \"me\", \"my\", \"I\"",
    "NEVER attempt to pass `user_id` as a tool argument",
    "identity is injected by the runtime",
    "hard cap of 20 tool hops per query",
    "busy placeholder",
    "escalate via `escalate_to_human`",
)

# Signals that the LLM is enumerating its own rules / tool catalog —
# phrasing that typically shows up in response to polite extraction.
_SELF_DESCRIBED_RULES = re.compile(
    r"(my|the)\s+(system\s+prompt|instructions|operational\s+guidelines|"
    r"tool[-\s]usage\s+polic(y|ies))\b",
    re.IGNORECASE,
)
_NUMBERED_RULES = re.compile(
    r"^\s*\d+\.\s+\*\*[A-Z][^*]{2,40}\*\*\s*:",
    re.MULTILINE,
)


@dataclass
class OutputScanResult:
    """Result of scanning LLM output for suspicious content."""
    flagged: bool = False
    reasons: list[str] = field(default_factory=list)
    classifier_verdict: str = ""


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

# Llama Guard hazard category labels (S1-S14)
_HAZARD_CATEGORIES: dict[str, str] = {
    "S1": "violent_crimes",
    "S2": "non_violent_crimes",
    "S3": "sex_related_crimes",
    "S4": "child_sexual_exploitation",
    "S5": "defamation",
    "S6": "specialized_advice",
    "S7": "privacy",
    "S8": "intellectual_property",
    "S9": "indiscriminate_weapons",
    "S10": "hate",
    "S11": "suicide_self_harm",
    "S12": "sexual_content",
    "S13": "elections",
    "S14": "code_interpreter_abuse",
}


class OutputScanner:
    """Two-stage LLM output scanner: regex fast path, then Llama Guard 3 1B."""

    name: ClassVar[str] = "output_scan"

    def __init__(
        self,
        extra_rogue_strings: list[str] | None = None,
        enable_semantic: bool = False,
        ollama_host: str | None = None,
        guard_model: str = "llama-guard3:1b",
        timeout_seconds: float = 30.0,
        num_ctx: int | None = None,
    ) -> None:
        self._rogue_strings = _ROGUE_STRINGS + (extra_rogue_strings or [])
        self._enable_semantic = enable_semantic
        self._guard_model = guard_model
        self._timeout = timeout_seconds
        self._ollama_host = ollama_host or os.environ.get(
            "OLLAMA_HOST", "http://localhost:11434"
        )
        # Guard sees only short (query, answer) pairs; 4k ctx is plenty
        # and leaves VRAM for the 70B agent. Env override for ops.
        if num_ctx is None:
            num_ctx = int(os.environ.get("SECURERAG_GUARD_NUM_CTX", "4096"))
        self._num_ctx = num_ctx

    def scan(self, output: str, question: str = "") -> OutputScanResult:
        """Scan LLM output. Returns flagged=True if suspicious content found."""
        reasons: list[str] = []
        output_lower = output.lower()

        # Stage 1: regex
        for rogue in self._rogue_strings:
            if rogue.lower() in output_lower:
                reasons.append(f"rogue_string: {rogue}")

        for label, pattern in _HIJACK_PATTERNS:
            if pattern.search(output):
                reasons.append(f"hijack_pattern: {label}")

        # Stage 1b: prompt-leak echo — verbatim signatures from our own
        # system prompt, plus the "here are my rules" structural tell.
        for signature in _SYSTEM_PROMPT_SIGNATURES:
            if signature in output:
                reasons.append("system_prompt_echo")
                break
        # Two independent signals required for the structural check so
        # legitimate numbered answers don't false-positive.
        if _SELF_DESCRIBED_RULES.search(output) and _NUMBERED_RULES.search(output):
            reasons.append("self_described_rules")

        if reasons:
            return OutputScanResult(
                flagged=True,
                reasons=reasons,
                classifier_verdict="skipped",
            )

        # Stage 2: Llama Guard
        if self._enable_semantic:
            verdict = self._classify(output, question)
            if verdict.startswith("unsafe"):
                category_code = verdict.split("\n")[1].strip() if "\n" in verdict else ""
                category_label = _HAZARD_CATEGORIES.get(category_code, category_code)
                reasons.append(f"llama_guard: {category_label}")
                return OutputScanResult(
                    flagged=True,
                    reasons=reasons,
                    classifier_verdict=verdict,
                )
            return OutputScanResult(
                flagged=False,
                reasons=[],
                classifier_verdict=verdict,
            )

        return OutputScanResult(flagged=False, reasons=[], classifier_verdict="disabled")

    def _classify(self, output: str, question: str) -> str:
        """Return raw Llama Guard verdict via Ollama chat API."""
        messages = []
        if question:
            messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "content": output})

        try:
            response = httpx.post(
                f"{self._ollama_host}/api/chat",
                json={
                    "model": self._guard_model,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_ctx": self._num_ctx},
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.json()["message"]["content"].strip()
        except (httpx.HTTPError, KeyError, ValueError):
            # Fail closed
            return "unsafe\nerror"
