"""Small synthetic CPU-only persistence and vector-scan pilot; no models."""

import argparse
import json
from pathlib import Path
import tempfile
import time

from local_rag.config import Settings
from local_rag.embeddings import DeterministicEmbedder
from local_rag.indexing import Indexer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.documents <= 2000:
        parser.error("documents must be between 1 and 2000")
    with tempfile.TemporaryDirectory(prefix="local-rag-pilot-") as directory:
        root = Path(directory)
        notes = root / "notes"
        notes.mkdir()
        for i in range(args.documents):
            (notes / f"note-{i:04}.md").write_text(f"# Note {i}\nSynthetic backup instructions for document {i}.\n", encoding="utf-8")
        config = root / "config.json"
        config.write_text(json.dumps({"data_dir": "data", "sources": [{"id": "pilot", "path": "notes"}]}))
        provider = DeterministicEmbedder()
        with Indexer(Settings.load(config), embedder=provider) as app:
            start = time.perf_counter()
            report = app.index()
            indexed_s = time.perf_counter() - start
            if report.failed or report.indexed != args.documents:
                raise RuntimeError(f"pilot indexing failed: {report}")
        with Indexer(Settings.load(config), embedder=provider) as app:
            start = time.perf_counter()
            unchanged = app.index()
            unchanged_s = time.perf_counter() - start
            if unchanged.unchanged != args.documents or unchanged.failed:
                raise RuntimeError("pilot persistence check failed")
            query = provider.embed(["backup instructions"])[0]
            start = time.perf_counter()
            hits = app.store.vector_search(query, fingerprint=app.profile.fingerprint, limit=5)
            scan_s = time.perf_counter() - start
            count = app.store.status()["chunk_count"]
        print(json.dumps({"provider": "deterministic-test (no inference)", "documents": args.documents,
                          "chunks": count, "dimensions": provider.dimensions,
                          "index_seconds": round(indexed_s, 4), "unchanged_seconds": round(unchanged_s, 4),
                          "vector_scan_seconds": round(scan_s, 4), "results": len(hits),
                          "database_bytes": (root / "data" / "index.sqlite3").stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
