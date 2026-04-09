"""Tests for classification guard (output-side enforcement)."""

import pytest

from src.sanitizers.classification_guard import ClassificationGuard


class TestClassificationGuard:

    def test_clean_output_passes(self) -> None:
        guard = ClassificationGuard(user_accessible_classifications={"public", "engineering_confidential"})
        result = guard.scan("The vacation policy allows 15 days PTO per year.")
        assert result.flagged is False

    def test_matching_classification_passes(self) -> None:
        guard = ClassificationGuard(user_accessible_classifications={"public", "engineering_confidential"})
        result = guard.scan("Per the ENGINEERING CONFIDENTIAL migration plan, Phase 1 starts July 1.")
        assert result.flagged is False

    def test_leaked_classification_flagged(self) -> None:
        guard = ClassificationGuard(user_accessible_classifications={"public", "engineering_confidential"})
        result = guard.scan("According to the LEGAL CONFIDENTIAL litigation summary, the settlement range is $1.5-2.5M.")
        assert result.flagged is True
        assert "legal_confidential" in result.leaked_classifications

    def test_executive_leaked_to_non_exec(self) -> None:
        guard = ClassificationGuard(user_accessible_classifications={"public", "finance_confidential"})
        result = guard.scan("The EXECUTIVE CONFIDENTIAL board minutes show the acquisition is on track.")
        assert result.flagged is True
        assert "executive_confidential" in result.leaked_classifications

    def test_multiple_leaks_detected(self) -> None:
        guard = ClassificationGuard(user_accessible_classifications={"public"})
        result = guard.scan(
            "From the HR CONFIDENTIAL comp analysis and LEGAL CONFIDENTIAL memo, "
            "the company faces significant exposure."
        )
        assert result.flagged is True
        assert "hr_confidential" in result.leaked_classifications
        assert "legal_confidential" in result.leaked_classifications

    def test_exec_sees_all(self) -> None:
        all_classifications = {
            "public", "engineering_confidential", "legal_confidential",
            "hr_confidential", "finance_confidential", "executive_confidential",
            "confidential",
        }
        guard = ClassificationGuard(user_accessible_classifications=all_classifications)
        result = guard.scan("The EXECUTIVE CONFIDENTIAL acquisition and LEGAL CONFIDENTIAL case are progressing.")
        assert result.flagged is False

    def test_default_public_only(self) -> None:
        guard = ClassificationGuard()
        result = guard.scan("The ENGINEERING CONFIDENTIAL plan mentions a migration.")
        assert result.flagged is True

    def test_attorney_client_privilege_flagged(self) -> None:
        guard = ClassificationGuard(user_accessible_classifications={"public", "engineering_confidential"})
        result = guard.scan("This document is ATTORNEY-CLIENT PRIVILEGED and should not be shared.")
        assert result.flagged is True
        assert "attorney_client" in result.leaked_classifications
