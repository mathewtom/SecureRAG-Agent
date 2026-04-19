"""One-shot ingestion CLI for the Meridian corpus.

Usage:
    uv run python scripts/ingest_meridian.py

What it does:
- Loads documents from data/meridian/documents/ (excluding poisoned/ by default)
- Runs them through SanitizationGate (PII redaction + credential scrub)
- Chunks via RecursiveCharacterTextSplitter (1000 chars, 150 overlap)
- Embeds with ChromaDB's default model (sentence-transformers all-MiniLM-L6-v2)
- Persists to data/chroma/

Re-run safely: ChromaDB's `add()` is idempotent for repeated chunk IDs
(the SHA-256 prefix derived from doc path + chunk index).

Optional flags:
    --include-poisoned    Include data/meridian/documents/poisoned/ fixtures.
                          Use ONLY for red-team testing; never for prod ingest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `src.*` imports resolvable when running this script directly.
# pytest does this automatically via rootdir; standalone scripts don't.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb  # noqa: E402

from src.ingestion.pipeline import ingest_meridian  # noqa: E402
from src.sanitizers.gate import SanitizationGate  # noqa: E402

DATA_ROOT = Path("data/meridian")
CHROMA_DIR = Path("data/chroma")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-poisoned",
        action="store_true",
        help="DANGEROUS — include red-team poisoned fixtures",
    )
    parser.add_argument(
        "--collection",
        default="meridian_documents",
        help="ChromaDB collection name (default: meridian_documents)",
    )
    args = parser.parse_args(argv)

    if args.include_poisoned:
        print("WARNING: ingesting poisoned fixtures — red-team mode only.",
              file=sys.stderr)
        # The Phase-1 loader excludes poisoned by default; toggling that off
        # would require a wider refactor than this script handles. For now,
        # poisoned ingestion is a known gap — flag it and bail.
        print("Poisoned ingest path not yet implemented; bailing.",
              file=sys.stderr)
        return 2

    print(f"Loading documents from {DATA_ROOT}/documents/ ...")
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    gate = SanitizationGate()

    result = ingest_meridian(
        data_root=DATA_ROOT,
        chroma_client=chroma_client,
        gate=gate,
        collection_name=args.collection,
    )

    print(f"\nIngest complete:")
    print(f"  Clean documents:      {result.clean}")
    print(f"  Quarantined documents: {result.quarantined}")
    print(f"  Total chunks embedded: {result.chunks}")
    print(f"  ChromaDB collection:   {args.collection!r} at {CHROMA_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
