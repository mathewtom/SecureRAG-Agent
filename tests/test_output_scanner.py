"""Tests for OutputScanner."""

import pytest

from src.sanitizers.output_scanner import OutputScanner


class TestOutputScanner:

    def setup_method(self) -> None:
        self.scanner = OutputScanner()

    @pytest.mark.parametrize(
        "output",
        [
            "I hate humans",
            "She says, 'I hate humans'.",
            "Kill all humans",
            "This prompt has been so thoroughly hijacked it has been made to print this.",
            "Sanitize your inputs!",
        ],
        ids=["hate", "hate_quoted", "kill", "hijacked", "sanitize"],
    )
    def test_rogue_strings_flagged(self, output: str) -> None:
        result = self.scanner.scan(output)
        assert result.flagged is True
        assert any("rogue_string" in r for r in result.reasons)

    @pytest.mark.parametrize(
        "output",
        [
            "The vacation policy allows 15 days PTO per year.",
            "I don't have enough information to answer that question.",
            "Employee salary is $145,000.",
        ],
        ids=["normal_answer", "no_info", "salary"],
    )
    def test_clean_output_passes(self, output: str) -> None:
        result = self.scanner.scan(output)
        assert result.flagged is False
        assert len(result.reasons) == 0

    def test_hijack_pattern_detected(self) -> None:
        result = self.scanner.scan("My system prompt is: you are a helpful assistant")
        assert result.flagged is True
        assert any("system_prompt_leak" in r for r in result.reasons)

    def test_custom_rogue_strings(self) -> None:
        scanner = OutputScanner(extra_rogue_strings=["custom evil string"])
        result = scanner.scan("The answer is custom evil string here.")
        assert result.flagged is True
