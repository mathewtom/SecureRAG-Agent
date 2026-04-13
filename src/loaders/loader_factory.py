"""Generic document loader that walks a directory and picks loaders by extension."""

import logging
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from langchain_community.document_loaders import (
    CSVLoader,
    PyPDFLoader,
    TextLoader,
)
from langchain_core.documents import Document

from src.sanitizers.classification_extractor import extract_classification

logger = logging.getLogger(__name__)

_LOADER_MAP: dict[str, type] = {
    ".txt": TextLoader,
    ".pdf": PyPDFLoader,
    ".csv": CSVLoader,
}


def load_documents(
    source_dir: str | Path,
    access_level: str = "internal",
) -> list[Document]:
    """Walk source_dir, load supported files, and enrich metadata."""
    source_path = Path(source_dir)
    if not source_path.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    documents: list[Document] = []
    ingested_at = datetime.now(timezone.utc).isoformat()

    for file_path in sorted(source_path.rglob("*")):
        if file_path.is_dir():
            continue

        ext = file_path.suffix.lower()
        loader_cls = _LOADER_MAP.get(ext)

        if loader_cls is None:
            logger.warning("Unsupported file type %s, skipping: %s", ext, file_path)
            continue

        try:
            loader = loader_cls(str(file_path))
            file_docs = loader.load()
        except Exception as exc:
            logger.error("Failed to load %s: %s", file_path, exc)
            continue

        for doc in file_docs:
            doc.page_content = unicodedata.normalize("NFKC", doc.page_content)

            classification = extract_classification(doc.page_content)
            doc.metadata.update({
                "filename": file_path.name,
                "file_type": ext,
                "access_level": access_level,
                "ingested_at": ingested_at,
                "sanitized": False,
                "classification": classification.classification if classification else "public",
                "classification_department": classification.department if classification else "",
            })
            documents.append(doc)

    return documents
