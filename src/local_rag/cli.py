"""CPU-only indexing and inspection. No model endpoint is configured."""

import argparse
from dataclasses import asdict
import json
import sqlite3
import sys

from .config import Settings, validate_relative_path
from .documents import read_document
from .embeddings import DeterministicEmbedder
from .indexing import Indexer
from .storage import Store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local document indexing pilot; no model or GPU execution")
    parser.add_argument("--config", required=True, help="JSON source configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="index Markdown, text, PDF and Word using explicit CPU test vectors")
    index.add_argument("--test-embeddings", action="store_true", help="use deterministic test vectors, not semantic embeddings")
    index.add_argument("--rebuild", action="store_true", help="build and atomically activate a complete new generation")
    index.add_argument("--source", help="index one configured source (cannot be combined with --rebuild)")
    commands.add_parser("status", help="show document counts, failures, and indexing jobs")
    inspect = commands.add_parser("inspect", help="show document excerpts and exact citation locations")
    inspect.add_argument("--source", required=True)
    inspect.add_argument("--path", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "index" and not args.test_embeddings:
            raise ValueError("this milestone requires --test-embeddings; real model inference is not implemented")
        settings = Settings.load(args.config)
        if args.command == "index":
            with Indexer(settings, embedder=DeterministicEmbedder()) as app:
                report = app.index(source_id=args.source, rebuild=args.rebuild)
            print(json.dumps(asdict(report), ensure_ascii=True, indent=2))
            return 1 if report.failed else 0
        if args.command == "inspect":
            source = settings.source(args.source)
            path = validate_relative_path(args.path)
            if not (source.root / path).resolve().is_relative_to(source.root):
                raise ValueError("document path is outside its configured source")
        database = settings.data_dir / "index.sqlite3"
        if not database.exists():
            if args.command == "status":
                print(json.dumps({"document_count": 0, "chunk_count": 0, "jobs": [], "indexed": False}))
                return 0
            raise ValueError("index has not been built")
        store = Store(database)
        try:
            if args.command == "status":
                result = store.status()
            else:
                if store.source_root(args.source) != source.root:
                    raise ValueError("configured source root differs from the indexed source; use its original configuration")
                docs = [asdict(doc) for doc in store.list_documents() if doc.source_id == args.source and doc.path == path]
                if not docs:
                    raise ValueError("document is not indexed")
                chunks = []
                for chunk in store.chunks(args.source, path):
                    item = asdict(chunk)
                    item.pop("embedding")
                    chunks.append(item)
                try:
                    current = read_document(source.root / path, settings.max_file_bytes)
                    citation_status = "current" if current.content_hash == docs[0]["content_hash"] else "changed"
                except (ValueError, OSError):
                    citation_status = "unavailable"
                result = {"document": docs[0], "file_path": str(source.root / path), "chunks": chunks,
                          "stale": citation_status != "current", "citation_status": citation_status}
            print(json.dumps(result, ensure_ascii=True, indent=2))
            return 0
        finally:
            store.close()
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
