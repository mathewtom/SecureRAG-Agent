"""Tests for classification extractor."""

import pytest

from securerag_agent.sanitizers.classification_extractor import extract_classification


class TestClassificationExtractor:

    @pytest.mark.parametrize(
        "text,expected_classification,expected_department",
        [
            ("ENGINEERING CONFIDENTIAL\n\nPlatform migration plan", "engineering_confidential", "engineering"),
            ("LEGAL CONFIDENTIAL\n\nPending litigation", "legal_confidential", "legal"),
            ("HR CONFIDENTIAL\n\nCompensation analysis", "hr_confidential", "hr"),
            ("FINANCE CONFIDENTIAL\n\nQuarterly financials", "finance_confidential", "finance"),
            ("EXECUTIVE CONFIDENTIAL\n\nBoard minutes", "executive_confidential", "executive"),
        ],
        ids=["engineering", "legal", "hr", "finance", "executive"],
    )
    def test_department_classifications(self, text: str, expected_classification: str, expected_department: str) -> None:
        result = extract_classification(text)
        assert result is not None
        assert result.classification == expected_classification
        assert result.department == expected_department

    def test_no_marker_returns_none(self) -> None:
        result = extract_classification("Company vacation policy allows 15 days PTO.")
        assert result is None

    def test_only_scans_header(self) -> None:
        text = ("A" * 501) + "\nENGINEERING CONFIDENTIAL"
        result = extract_classification(text)
        assert result is None

    def test_case_insensitive(self) -> None:
        result = extract_classification("engineering confidential\nSome content")
        assert result is not None
        assert result.classification == "engineering_confidential"

    def test_executive_takes_priority_over_generic(self) -> None:
        result = extract_classification("EXECUTIVE CONFIDENTIAL\nCONFIDENTIAL data")
        assert result.classification == "executive_confidential"

    def test_raw_marker_captured(self) -> None:
        result = extract_classification("HR CONFIDENTIAL\nSalary data")
        assert result.raw_marker == "HR CONFIDENTIAL"

    def test_generic_confidential(self) -> None:
        result = extract_classification("CONFIDENTIAL\nSome sensitive memo")
        assert result is not None
        assert result.classification == "confidential"
        assert result.department == ""
