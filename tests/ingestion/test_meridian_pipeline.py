"""Tests for the Meridian ingestion pipeline."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.ingestion.pipeline import (
    SAFE_METADATA_KEYS,
    IngestResult,
    ingest_meridian,
)


@pytest.fixture
def fake_chroma():
    client = MagicMock()
    collection = MagicMock()
    client.get_or_create_collection.return_value = collection
    return client, collection


@pytest.fixture
def noop_gate():
    gate = MagicMock()
    gate.process.side_effect = lambda docs: SimpleNamespace(
        clean=docs, quarantined=[],
    )
    return gate


def test_poisoned_documents_are_excluded_by_default(fake_chroma, noop_gate):
    client, collection = fake_chroma
    ingest_meridian(
        data_root=Path("data/meridian"),
        chroma_client=client,
        gate=noop_gate,
    )

    all_metas: list[dict[str, Any]] = [
        m for call in collection.add.call_args_list
        for m in call.kwargs["metadatas"]
    ]
    assert not any(m.get("TEST_POISONED") for m in all_metas)
    all_paths = {m.get("path") for m in all_metas}
    assert not any("poisoned/" in str(p) for p in all_paths)


def test_metadata_is_restricted_to_safe_keys(fake_chroma, noop_gate):
    client, collection = fake_chroma
    ingest_meridian(
        data_root=Path("data/meridian"),
        chroma_client=client,
        gate=noop_gate,
    )
    for call in collection.add.call_args_list:
        for meta in call.kwargs["metadatas"]:
            user_keys = set(meta) - {"path", "chunk_index"}
            assert user_keys.issubset(SAFE_METADATA_KEYS), (
                f"metadata leaked keys: {user_keys - SAFE_METADATA_KEYS}"
            )


def test_restricted_to_never_promoted_to_chunk_metadata(
    fake_chroma, noop_gate,
):
    client, collection = fake_chroma
    ingest_meridian(
        data_root=Path("data/meridian"),
        chroma_client=client,
        gate=noop_gate,
    )
    for call in collection.add.call_args_list:
        for meta in call.kwargs["metadatas"]:
            assert "restricted_to" not in meta


def test_ingest_result_reports_counts(fake_chroma, noop_gate):
    client, _ = fake_chroma
    result = ingest_meridian(
        data_root=Path("data/meridian"),
        chroma_client=client,
        gate=noop_gate,
    )
    assert isinstance(result, IngestResult)
    assert result.clean >= 15
    assert result.chunks >= result.clean
