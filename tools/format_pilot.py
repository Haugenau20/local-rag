"""Verify a synthetic mixed-format collection with CPU test vectors only."""

import json
from pathlib import Path
import sys
import tempfile

from local_rag.config import Settings
from local_rag.embeddings import DeterministicEmbedder
from local_rag.indexing import Indexer


def main():
    repository = Path(__file__).resolve().parents[1]
    # Fixture generation is test tooling, not a dependency of the application.
    sys.path.insert(0, str(repository / "tests"))
    from format_fixtures import docx_bytes, pdf_bytes
    gold = json.loads((repository / "examples" / "format-gold-set.json").read_text(encoding="utf-8"))
    queries = gold.get("queries", [])
    if not queries or any(not q.get("query", "").strip() for q in queries):
        raise ValueError("ground truth must contain nonempty questions")
    with tempfile.TemporaryDirectory(prefix="local-rag-formats-") as directory:
        root = Path(directory)
        notes = root / "notes"
        notes.mkdir()
        (notes / "maintenance.pdf").write_bytes(pdf_bytes(["Backups run nightly.", "", "Restore from the latest verified backup."]))
        def meeting(document):
            document.add_heading("Planning", level=1)
            document.add_paragraph("Review the plan on Tuesday.")
            table = document.add_table(rows=1, cols=2)
            table.cell(0, 0).text = "Owner"
            table.cell(0, 1).text = "Søren"
            document.add_heading("Retention", level=2)
            document.add_paragraph("Keep offline copies for 30 days.")
        (notes / "meeting.docx").write_bytes(docx_bytes(meeting))
        (notes / "office.txt").write_text("Office hours are Monday and Wednesday.\n", encoding="utf-8")
        (notes / "operations.md").write_text("# Operations\nUpdates are stored in the local index.\n", encoding="utf-8")
        config = root / "config.json"
        config.write_text(json.dumps({"data_dir": "data", "sources": [{"id": "fixture", "path": "notes"}]}))
        with Indexer(Settings.load(config), embedder=DeterministicEmbedder()) as app:
            report = app.index()
            if report.failed or report.indexed != 4:
                raise RuntimeError(f"mixed-format indexing failed: {report}")
        with Indexer(Settings.load(config), embedder=DeterministicEmbedder()) as app:
            for query in queries:
                candidates = app.store.chunks("fixture", query["path"])
                match = any(query["answer_contains"] in chunk.text and
                            all(chunk.locator.get(key) == value for key, value in query["locator"].items())
                            for chunk in candidates)
                if not match:
                    raise RuntimeError(f"missing ground-truth text or citation: {query['id']}")
            status = app.store.status()
            print(json.dumps({"provider": "deterministic-test (no inference)", "documents": status["document_count"],
                              "chunks": status["chunk_count"], "verified_questions": len(queries),
                              "formats": ["markdown", "text", "pdf", "docx"],
                              "validation": "persisted extraction text and citation locations, not retrieval quality"}, indent=2))


if __name__ == "__main__":
    main()
