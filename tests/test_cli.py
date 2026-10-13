import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "notes").mkdir()
        (self.root / "notes" / "note.md").write_text("# Plans\nSave backups.\n", encoding="utf-8")
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"data_dir": "data", "sources": [{"id": "notes", "path": "notes"}]}), encoding="utf-8")

    def run_cli(self, *args):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, "-m", "local_rag", "--config", str(self.config), *args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=15)

    def test_index_status_and_inspect_across_separate_processes(self):
        result = self.run_cli("index", "--test-embeddings")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["indexed"], 1)
        status = self.run_cli("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["document_count"], 1)
        result = self.run_cli("inspect", "--source", "notes", "--path", "note.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["chunks"][0]["start_line"], 1)

    def test_index_requires_explicit_test_embedding_flag(self):
        result = self.run_cli("index")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--test-embeddings", result.stderr)
        self.assertFalse((self.root / "data" / "index.sqlite3").exists())

    def test_inspect_rejects_paths_outside_configured_source(self):
        self.run_cli("index", "--test-embeddings")
        result = self.run_cli("inspect", "--source", "notes", "--path", "../private.txt")
        self.assertEqual(result.returncode, 2)

    def test_partial_failure_has_nonzero_exit_and_a_report(self):
        (self.root / "notes" / "broken.txt").write_bytes(b"\xff")
        result = self.run_cli("index", "--test-embeddings")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(result.stdout, "indexing must return a structured failure report")
        self.assertEqual(json.loads(result.stdout)["failed"], 1)

    def test_inspect_marks_changed_file_citations_as_stale(self):
        self.assertEqual(self.run_cli("index", "--test-embeddings").returncode, 0)
        (self.root / "notes" / "note.md").write_text("replacement text", encoding="utf-8")
        result = self.run_cli("inspect", "--source", "notes", "--path", "note.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout).get("stale", False))
        self.assertEqual(json.loads(result.stdout)["chunks"][0]["text"], "# Plans\nSave backups.")

    def test_inspect_refuses_to_attribute_old_index_to_changed_source_root(self):
        self.assertEqual(self.run_cli("index", "--test-embeddings").returncode, 0)
        (self.root / "other").mkdir()
        self.config.write_text(json.dumps({"data_dir": "data", "sources": [{"id": "notes", "path": "other"}]}))
        result = self.run_cli("inspect", "--source", "notes", "--path", "note.md")
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_status_on_unbuilt_index_does_not_create_database(self):
        self.assertEqual(self.run_cli("status").returncode, 0)
        self.assertFalse((self.root / "data" / "index.sqlite3").exists())

    def test_corrupt_database_returns_a_clear_error(self):
        (self.root / "data").mkdir()
        (self.root / "data" / "index.sqlite3").write_bytes(b"corrupt file")
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 2)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
