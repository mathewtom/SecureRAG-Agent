"""Tests for model digest verification."""

import pytest

from securerag_agent.model_integrity import verify_model_digest, ModelDigestMismatch


class TestModelDigestVerification:

    @pytest.mark.integration
    def test_verify_known_model_returns_digest(self) -> None:
        digest = verify_model_digest("llama3.3:70b")
        assert len(digest) > 0
        assert digest.startswith("a6eb4748fd29")

    @pytest.mark.integration
    def test_verify_with_correct_pin(self) -> None:
        digest = verify_model_digest(
            "llama3.3:70b",
            expected_digest="a6eb4748fd29",
        )
        assert digest.startswith("a6eb4748fd29")

    @pytest.mark.integration
    def test_verify_with_wrong_pin_raises(self) -> None:
        with pytest.raises(ModelDigestMismatch) as exc_info:
            verify_model_digest(
                "llama3.3:70b",
                expected_digest="0000deadbeef",
            )
        assert exc_info.value.expected == "0000deadbeef"

    @pytest.mark.integration
    def test_missing_model_raises(self) -> None:
        with pytest.raises(ModelDigestMismatch) as exc_info:
            verify_model_digest("nonexistent:latest")
        assert "not_found" in exc_info.value.actual

    @pytest.mark.integration
    def test_no_pin_skips_verification(self) -> None:
        digest = verify_model_digest("llama3.3:70b")
        assert len(digest) > 0

    @pytest.mark.integration
    def test_unreachable_host_raises(self) -> None:
        with pytest.raises(Exception):
            verify_model_digest(
                "llama3.3:70b",
                ollama_host="http://localhost:99999",
            )
