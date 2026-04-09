"""Tests for AccessControlledRetriever — org-chart + department access control."""

import chromadb
import pytest
from langchain_huggingface import HuggingFaceEmbeddings

from src.retrieval.access_controlled import (
    AccessControlledRetriever,
    DEFAULT_ORG_CHART,
    DEFAULT_DEPARTMENT_MAP,
)


class TestOrgChartVisibility:

    def setup_method(self) -> None:
        self.client = chromadb.Client()
        self.collection = self.client.create_collection(
            name="test_org_chart",
            metadata={"hnsw:space": "cosine"},
        )
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

        policy_docs = [
            ("Vacation policy: 15 days PTO per year.", {"doc_type": "policy", "filename": "vacation.txt", "classification": "public"}),
        ]
        hr_docs = [
            ("Sarah Chen, VP Engineering, salary 250000", {"doc_type": "hr_record", "subject_employee_id": "E001", "classification": "public"}),
            ("Marcus Rivera, Eng Manager, salary 180000", {"doc_type": "hr_record", "subject_employee_id": "E002", "classification": "public"}),
            ("Priya Patel, Software Engineer, salary 145000", {"doc_type": "hr_record", "subject_employee_id": "E003", "classification": "public"}),
            ("David Kim, Software Engineer, salary 140000", {"doc_type": "hr_record", "subject_employee_id": "E004", "classification": "public"}),
            ("Lisa Thompson, HR Director, salary 170000", {"doc_type": "hr_record", "subject_employee_id": "E005", "classification": "public"}),
            ("James Okafor, General Counsel, salary 220000", {"doc_type": "hr_record", "subject_employee_id": "E006", "classification": "public"}),
            ("Rachel Goldstein, CFO, salary 260000", {"doc_type": "hr_record", "subject_employee_id": "E007", "classification": "public"}),
            ("Derek Washington, CEO, salary 350000", {"doc_type": "hr_record", "subject_employee_id": "E012", "classification": "public"}),
        ]

        all_docs = policy_docs + hr_docs
        texts = [d[0] for d in all_docs]
        metadatas = [d[1] for d in all_docs]
        ids = [f"doc_{i}" for i in range(len(all_docs))]
        embeddings_list = self.embeddings.embed_documents(texts)
        self.collection.add(documents=texts, metadatas=metadatas, ids=ids, embeddings=embeddings_list)

        self.retriever = AccessControlledRetriever(
            collection=self.collection,
            embedding_function=self.embeddings,
            n_results=20,
        )

    def teardown_method(self) -> None:
        self.client.delete_collection("test_org_chart")

    def test_ceo_sees_all_hr_records(self) -> None:
        docs = self.retriever.query("employee salary", user_id="E012")
        emp_ids = {d.metadata["subject_employee_id"] for d in docs if d.metadata.get("doc_type") == "hr_record"}
        assert "E001" in emp_ids
        assert "E005" in emp_ids
        assert "E007" in emp_ids
        assert "E012" in emp_ids

    def test_vp_engineering_sees_engineering_chain(self) -> None:
        visible = self.retriever._get_visible_employees("E001")
        assert visible == {"E001", "E002", "E003", "E004", "E011"}

    def test_hr_director_sees_hr_chain(self) -> None:
        visible = self.retriever._get_visible_employees("E005")
        assert visible == {"E005", "E008"}

    def test_ic_sees_only_self(self) -> None:
        for emp in ["E003", "E004", "E011", "E008", "E009", "E010"]:
            visible = self.retriever._get_visible_employees(emp)
            assert visible == {emp}

    def test_unknown_user_sees_nothing(self) -> None:
        docs = self.retriever.query("employee salary", user_id="UNKNOWN")
        assert len(docs) == 0

    def test_everyone_sees_policies(self) -> None:
        for user_id in ["E003", "E009", "E010", "E012"]:
            docs = self.retriever.query("vacation policy", user_id=user_id)
            policy_docs = [d for d in docs if d.metadata.get("doc_type") == "policy"]
            assert len(policy_docs) >= 1


class TestDepartmentClassificationAccess:

    def setup_method(self) -> None:
        self.client = chromadb.Client()
        self.collection = self.client.create_collection(
            name="test_dept_access",
            metadata={"hnsw:space": "cosine"},
        )
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

        classified_docs = [
            ("Platform migration plan for Q3 2026", {"classification": "engineering_confidential", "filename": "migration.txt"}),
            ("Pending litigation summary Meridian case", {"classification": "legal_confidential", "filename": "litigation.txt"}),
            ("Compensation benchmarking analysis 2026", {"classification": "hr_confidential", "filename": "comp.txt"}),
            ("Quarterly financials Q1 revenue $18.4M", {"classification": "finance_confidential", "filename": "financials.txt"}),
            ("Acquisition target DataForge Analytics", {"classification": "executive_confidential", "filename": "acquisition.txt"}),
            ("Company vacation policy 15 days PTO", {"classification": "public", "doc_type": "policy", "filename": "vacation.txt"}),
        ]

        texts = [d[0] for d in classified_docs]
        metadatas = [d[1] for d in classified_docs]
        ids = [f"cls_{i}" for i in range(len(classified_docs))]
        embeddings_list = self.embeddings.embed_documents(texts)
        self.collection.add(documents=texts, metadatas=metadatas, ids=ids, embeddings=embeddings_list)

        self.retriever = AccessControlledRetriever(
            collection=self.collection,
            embedding_function=self.embeddings,
            n_results=10,
        )

    def teardown_method(self) -> None:
        self.client.delete_collection("test_dept_access")

    def test_engineer_sees_engineering_docs(self) -> None:
        docs = self.retriever.query("platform migration", user_id="E003")
        classifications = {d.metadata.get("classification") for d in docs}
        assert "engineering_confidential" in classifications

    def test_engineer_cannot_see_legal_docs(self) -> None:
        docs = self.retriever.query("litigation", user_id="E003")
        for doc in docs:
            assert doc.metadata.get("classification") != "legal_confidential"

    def test_lawyer_sees_legal_docs(self) -> None:
        docs = self.retriever.query("litigation", user_id="E009")
        classifications = {d.metadata.get("classification") for d in docs}
        assert "legal_confidential" in classifications

    def test_lawyer_cannot_see_engineering_docs(self) -> None:
        docs = self.retriever.query("platform migration", user_id="E009")
        for doc in docs:
            assert doc.metadata.get("classification") != "engineering_confidential"

    def test_ceo_sees_all_classified_docs(self) -> None:
        accessible = self.retriever._get_accessible_classifications("E012")
        assert "engineering_confidential" in accessible
        assert "legal_confidential" in accessible
        assert "hr_confidential" in accessible
        assert "finance_confidential" in accessible
        assert "executive_confidential" in accessible

    def test_finance_sees_finance_docs(self) -> None:
        docs = self.retriever.query("quarterly revenue financials", user_id="E010")
        classifications = {d.metadata.get("classification") for d in docs}
        assert "finance_confidential" in classifications

    def test_finance_cannot_see_exec_confidential(self) -> None:
        accessible = self.retriever._get_accessible_classifications("E010")
        assert "executive_confidential" not in accessible

    def test_hr_sees_hr_docs(self) -> None:
        docs = self.retriever.query("compensation analysis", user_id="E005")
        classifications = {d.metadata.get("classification") for d in docs}
        assert "hr_confidential" in classifications

    def test_ic_engineer_cannot_see_exec_acquisition(self) -> None:
        docs = self.retriever.query("acquisition target DataForge", user_id="E003")
        for doc in docs:
            assert doc.metadata.get("classification") != "executive_confidential"
