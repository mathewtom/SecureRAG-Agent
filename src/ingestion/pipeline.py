"""Meridian ingestion pipeline.

Reads documents from data/meridian/documents/ via src/data/loaders.py
(which excludes poisoned fixtures by default), sanitizes via
SanitizationGate, chunks, and stores in ChromaDB.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.documents import Document as LCDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.data.loaders import Document as MeridianDoc
from src.data.loaders import load_documents

SAFE_METADATA_KEYS = {
    "title",
    "classification",
    "project_id",
    "effective_date",
    "supersedes",
    "superseded_by",
    "owner",
}

_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 150
_COLLECTION_NAME = "meridian_documents"


@dataclass(frozen=True)
class IngestResult:
    clean: int
    quarantined: int
    chunks: int


def ingest_meridian(
    *,
    data_root: Path,
    chroma_client: Any,
    gate: Any,
    collection_name: str = _COLLECTION_NAME,
) -> IngestResult:
    """End-to-end: load → filter poisoned → sanitize → chunk → embed."""
    meridian_docs = load_documents(data_root, include_poisoned=False)
    lc_docs = [_to_langchain_doc(d) for d in meridian_docs]

    gated = gate.process(lc_docs)
    clean_docs = list(gated.clean)
    quarantined = list(gated.quarantined)

    chunks = _chunk(clean_docs)
    collection = chroma_client.get_or_create_collection(collection_name)
    if chunks:
        collection.add(
            ids=[_chunk_id(c, i) for i, c in enumerate(chunks)],
            documents=[c.page_content for c in chunks],
            metadatas=[c.metadata for c in chunks],
        )

    return IngestResult(
        clean=len(clean_docs),
        quarantined=len(quarantined),
        chunks=len(chunks),
    )


def _to_langchain_doc(doc: MeridianDoc) -> LCDocument:
    metadata: dict[str, Any] = {
        k: _coerce(v)
        for k, v in doc.frontmatter.items()
        if k in SAFE_METADATA_KEYS
    }
    metadata["path"] = str(doc.path)
    return LCDocument(page_content=doc.body, metadata=metadata)


def _coerce(value: Any) -> str | int | float | bool:
    """Chroma metadata values must be scalar; flatten lists/None to str."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _chunk(docs: list[LCDocument]) -> list[LCDocument]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
    )
    out: list[LCDocument] = []
    for doc in docs:
        parts = splitter.split_documents([doc])
        for i, part in enumerate(parts):
            part.metadata["chunk_index"] = i
        out.extend(parts)
    return out


def _chunk_id(chunk: LCDocument, index: int) -> str:
    payload = f"{chunk.metadata.get('path', '?')}:{index}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]
