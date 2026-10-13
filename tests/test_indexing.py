import importlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch


class IndexingTests(unittest.TestCase):
    def setUp(self):
        try:
            self.config = importlib.import_module("local_rag.config")
            self.indexing = importlib.import_module("local_rag.indexing")
            self.embedding = importlib.import_module("local_rag.embeddings")
        except ModuleNotFoundError:
            self.fail("Persistent document indexing is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.notes = self.root / "notes"
        self.notes.mkdir()
        self.config_file = self.root / "config.json"
        self.write_config()

    def write_config(self, sources=None, **extra):
        data = {"data_dir": "data", "sources": sources or [{"id": "notes", "path": "notes"}], "max_chars": 48, "max_file_bytes": 10000}
        data.update(extra)
        self.config_file.write_text(json.dumps(data), encoding="utf-8")

    def app(self, embedder=None):
        provider = embedder or self.embedding.DeterministicEmbedder(dimensions=16)
        return self.indexing.Indexer(self.config.Settings.load(self.config_file), embedder=provider)

    def write_note(self, text="# Plans\nKeep a backup.\n", name="note.md"):
        path = self.notes / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_index_close_reopen_and_inspect_exact_citations(self):
        self.write_note()
        with self.app() as app:
            report = app.index()
            self.assertEqual((report.indexed, report.failed), (1, 0))
            revision = app.store.list_documents()[0].revision
        with self.app() as app:
            docs = app.store.list_documents()
            self.assertEqual((docs[0].source_id, docs[0].path), ("notes", "note.md"))
            chunks = app.store.chunks("notes", "note.md")
            self.assertEqual(chunks[0].text, "# Plans\nKeep a backup.")
            self.assertEqual((chunks[0].start_line, chunks[0].end_line), (1, 2))
            self.assertEqual(chunks[0].revision, revision)
            self.assertEqual(len(chunks[0].embedding), 16)

    def test_unchanged_file_preserves_revision_and_skips_embedding(self):
        self.write_note()
        with self.app() as app:
            app.index()
            revision = app.store.list_documents()[0].revision
            class MustNotEmbed(self.embedding.DeterministicEmbedder):
                def embed(self, texts):
                    raise AssertionError("unchanged document must not be re-embedded")
            app.embedder = MustNotEmbed(dimensions=16)
            report = app.index()
            self.assertEqual((report.unchanged, report.failed), (1, 0))
            self.assertEqual(app.store.list_documents()[0].revision, revision)

    def test_edit_replaces_old_text_and_changes_revision(self):
        path = self.write_note("old text")
        with self.app() as app:
            app.index()
            old = app.store.list_documents()[0].revision
            path.write_text("new text", encoding="utf-8")
            app.index()
            self.assertNotEqual(app.store.list_documents()[0].revision, old)
            self.assertEqual([c.text for c in app.store.chunks("notes", "note.md")], ["new text"])

    def test_empty_file_clears_old_chunks_and_remains_tracked(self):
        path = self.write_note()
        with self.app() as app:
            app.index()
            path.write_text("", encoding="utf-8")
            self.assertEqual(app.index().indexed, 1)
            self.assertEqual(app.store.chunks("notes", "note.md"), [])
            self.assertEqual(app.store.list_documents()[0].chunk_count, 0)
            self.assertEqual(app.index().unchanged, 1)

    def test_confirmed_deletion_removes_document_and_chunks(self):
        path = self.write_note()
        with self.app() as app:
            app.index()
            path.unlink()
            self.assertEqual(app.index().deleted, 1)
            self.assertEqual(app.store.list_documents(), [])
            self.assertEqual(app.store.chunks("notes", "note.md"), [])

    def test_read_failure_preserves_previous_revision_and_reports_error(self):
        self.write_note()
        with self.app() as app:
            app.index()
            old = app.store.list_documents()[0].revision
            with patch("local_rag.indexing.read_document", side_effect=PermissionError("temporarily locked")):
                report = app.index()
            self.assertEqual((report.failed, report.deleted), (1, 0))
            self.assertEqual(app.store.list_documents()[0].revision, old)
            self.assertTrue(app.store.list_documents()[0].last_error)
            self.assertTrue(app.store.chunks("notes", "note.md"))
            self.assertEqual(app.index().unchanged, 1)
            self.assertIsNone(app.store.list_documents()[0].last_error)

    def test_missing_source_root_does_not_delete_documents(self):
        self.write_note()
        with self.app() as app:
            app.index()
            self.notes.rename(self.root / "away")
            report = app.index()
            self.assertEqual(report.deleted, 0)
            self.assertGreater(report.failed, 0)
            self.assertEqual(len(app.store.list_documents()), 1)

    def test_partial_scan_does_not_confirm_deletions(self):
        path = self.write_note()
        with self.app() as app:
            app.index()
            path.unlink()
            scan = self.indexing.ScanResult(files=(), complete=False, errors=("unreadable subdirectory",))
            with patch("local_rag.indexing.discover", return_value=scan):
                report = app.index()
            self.assertEqual(report.deleted, 0)
            self.assertGreater(report.failed, 0)
            self.assertEqual(len(app.store.list_documents()), 1)

    def test_failed_embedding_keeps_complete_previous_revision(self):
        path = self.write_note("old passage")
        with self.app() as app:
            app.index()
            old = app.store.list_documents()[0].revision
            path.write_text("replacement passage", encoding="utf-8")
            class Broken(self.embedding.DeterministicEmbedder):
                def embed(self, texts):
                    raise RuntimeError("synthetic failure")
            app.embedder = Broken(dimensions=16)
            self.assertEqual(app.index().failed, 1)
            self.assertEqual(app.store.list_documents()[0].revision, old)
            self.assertEqual(app.store.chunks("notes", "note.md")[0].text, "old passage")

    def test_interrupted_database_replace_rolls_back_previous_revision(self):
        path = self.write_note("old passage")
        with self.app() as app:
            app.index()
            old = app.store.list_documents()[0].revision
            app.store.connection.execute("CREATE TRIGGER abort_chunk BEFORE INSERT ON chunks BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END")
            path.write_text("new passage", encoding="utf-8")
            self.assertEqual(app.index().failed, 1)
            self.assertEqual(app.store.list_documents()[0].revision, old)
            self.assertEqual(app.store.chunks("notes", "note.md")[0].text, "old passage")

    def test_two_sources_isolate_identical_filenames(self):
        other = self.root / "other"
        other.mkdir()
        self.write_note("source one")
        (other / "note.md").write_text("source two", encoding="utf-8")
        self.write_config(sources=[{"id": "notes", "path": "notes"}, {"id": "other", "path": "other"}])
        with self.app() as app:
            self.assertEqual(app.index().indexed, 2)
            self.assertEqual(app.store.chunks("notes", "note.md")[0].text, "source one")
            self.assertEqual(app.store.chunks("other", "note.md")[0].text, "source two")

    def test_model_profile_change_requires_rebuild(self):
        self.write_note()
        with self.app() as app:
            app.index()
        with self.app(self.embedding.DeterministicEmbedder(dimensions=8)) as app:
            with self.assertRaises(self.indexing.ProfileMismatch):
                app.index()
            self.assertEqual(len(app.store.chunks("notes", "note.md")[0].embedding), 16)
            self.assertEqual(app.index(rebuild=True).failed, 0)
            self.assertEqual(len(app.store.chunks("notes", "note.md")[0].embedding), 8)

    def test_rebuild_updates_every_source_not_only_the_first(self):
        other = self.root / "other"
        other.mkdir()
        self.write_note("first")
        (other / "note.txt").write_text("second", encoding="utf-8")
        self.write_config(sources=[{"id": "notes", "path": "notes"}, {"id": "other", "path": "other"}])
        with self.app() as app:
            app.index()
        with self.app(self.embedding.DeterministicEmbedder(dimensions=8)) as app:
            app.index(rebuild=True)
            self.assertEqual(len(app.store.chunks("notes", "note.md")[0].embedding), 8)
            self.assertEqual(len(app.store.chunks("other", "note.txt")[0].embedding), 8)

    def test_failed_rebuild_keeps_previous_generation_active(self):
        self.write_note()
        with self.app() as app:
            app.index()
            old = app.store.list_documents()[0].revision
        class Broken(self.embedding.DeterministicEmbedder):
            def embed(self, texts):
                raise RuntimeError("synthetic failure")
        with self.app(Broken(dimensions=8)) as app:
            self.assertGreater(app.index(rebuild=True).failed, 0)
            self.assertEqual(app.store.list_documents()[0].revision, old)
            self.assertEqual(len(app.store.chunks("notes", "note.md")[0].embedding), 16)

    def test_invalid_vectors_are_rejected_before_replacing_old_content(self):
        path = self.write_note("old")
        class Invalid(self.embedding.DeterministicEmbedder):
            def embed(self, texts):
                return [[math.nan] * 16 for _ in texts]
        with self.app() as app:
            app.index()
            path.write_text("new", encoding="utf-8")
            app.embedder = Invalid(dimensions=16)
            self.assertEqual(app.index().failed, 1)
            self.assertEqual(app.store.chunks("notes", "note.md")[0].text, "old")

    def test_file_size_limit_preserves_previous_content(self):
        path = self.write_note("old")
        with self.app() as app:
            app.index()
            path.write_text("x" * 10001, encoding="utf-8")
            self.assertEqual(app.index().failed, 1)
            self.assertEqual(app.store.chunks("notes", "note.md")[0].text, "old")

    def test_invalid_utf8_preserves_previous_content(self):
        path = self.write_note("old")
        with self.app() as app:
            app.index()
            path.write_bytes(b"\xff\xfe")
            self.assertEqual(app.index().failed, 1)
            self.assertEqual(app.store.chunks("notes", "note.md")[0].text, "old")

    def test_unicode_paths_and_uppercase_extensions_are_indexed(self):
        self.write_note("blåbær", "Sørens noter.TXT")
        with self.app() as app:
            self.assertEqual(app.index().indexed, 1)
            self.assertEqual(app.store.chunks("notes", "Sørens noter.TXT")[0].text, "blåbær")

    def test_unsupported_formats_and_excluded_directories_are_not_read(self):
        (self.notes / "binary.pdf").write_bytes(b"not a document")
        (self.notes / ".git").mkdir()
        (self.notes / ".git" / "hidden.txt").write_text("hidden")
        with self.app() as app:
            self.assertEqual(app.index().indexed, 0)
            self.assertEqual(app.store.list_documents(), [])

    def test_symlink_outside_source_is_not_indexed(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside content")
        try:
            (self.notes / "link.txt").symlink_to(outside)
        except OSError:
            self.skipTest("host does not permit creating a symlink")
        with self.app() as app:
            self.assertEqual(app.index().indexed, 0)
            self.assertEqual(app.store.list_documents(), [])

    def test_data_directory_inside_source_is_rejected(self):
        self.write_config(data_dir="notes/data")
        with self.assertRaises(ValueError):
            self.config.Settings.load(self.config_file)

    def test_duplicate_source_ids_are_rejected(self):
        self.write_config(sources=[{"id": "notes", "path": "notes"}, {"id": "notes", "path": "other"}])
        with self.assertRaises(ValueError):
            self.config.Settings.load(self.config_file)

    def test_reusing_source_id_for_different_root_is_rejected(self):
        self.write_note()
        with self.app() as app:
            app.index()
        (self.root / "other").mkdir()
        self.write_config(sources=[{"id": "notes", "path": "other"}])
        with self.app() as app:
            with self.assertRaises(ValueError):
                app.index()

    def test_indexing_works_with_network_and_process_creation_forbidden(self):
        self.write_note()
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")), patch("subprocess.Popen", side_effect=AssertionError("process creation forbidden")):
            with self.app() as app:
                self.assertEqual((app.index().indexed, len(app.store.list_documents())), (1, 1))

    def test_vector_scan_reopens_and_rejects_incompatible_profile(self):
        self.write_note("backup instructions")
        with self.app() as app:
            app.index()
        with self.app() as app:
            vector = app.embedder.embed(["backup instructions"])[0]
            hits = app.store.vector_search(vector, fingerprint=app.profile.fingerprint, limit=3)
            self.assertEqual(hits[0].chunk.path, "note.md")
            self.assertAlmostEqual(hits[0].score, 1.0, places=5)
            with self.assertRaises(ValueError):
                app.store.vector_search(vector, fingerprint="wrong", limit=3)

    def test_job_outcomes_are_persisted(self):
        self.write_note()
        with self.app() as app:
            app.index()
            jobs = app.store.jobs()
            self.assertEqual((jobs[-1]["status"], jobs[-1]["indexed"]), ("completed", 1))
        with self.app() as app:
            self.assertEqual(len(app.store.jobs()), 1)

    def test_source_disappearing_after_scan_cannot_confirm_deletions(self):
        self.write_note()
        with self.app() as app:
            app.index()
            self.notes.rename(self.root / "away")
            with patch("local_rag.indexing.discover", return_value=self.indexing.ScanResult((), True, ())):
                report = app.index()
            self.assertEqual(report.deleted, 0)
            self.assertGreater(report.failed, 0)
            self.assertEqual(len(app.store.list_documents()), 1)

    def test_interrupting_rebuild_preserves_active_revision_and_records_job(self):
        self.write_note()
        with self.app() as app:
            app.index()
            old = app.store.list_documents()[0].revision
        class Interrupted(self.embedding.DeterministicEmbedder):
            def embed(self, texts):
                raise KeyboardInterrupt()
        with self.app(Interrupted(dimensions=8)) as app:
            with self.assertRaises(KeyboardInterrupt):
                app.index(rebuild=True)
            self.assertEqual(app.store.list_documents()[0].revision, old)
            self.assertEqual(app.store.jobs()[-1]["status"], "interrupted")

    def test_second_writer_is_rejected_while_index_lock_is_held(self):
        self.write_note()
        with self.app() as app:
            with self.indexing.writer_lock(self.root / "data" / "index.lock"):
                with self.assertRaisesRegex(RuntimeError, "another indexing job"):
                    app.index()

    def test_changed_chunking_profile_requires_rebuild(self):
        self.write_note()
        with self.app() as app:
            app.index()
        self.write_config(max_chars=16)
        with self.app() as app:
            with self.assertRaises(self.indexing.ProfileMismatch):
                app.index()
            self.assertEqual(app.index(rebuild=True).failed, 0)
            self.assertTrue(all(len(c.text) <= 16 for c in app.store.chunks("notes", "note.md")))

    def test_wrong_vector_count_preserves_previous_document(self):
        path = self.write_note("old")
        class WrongCount(self.embedding.DeterministicEmbedder):
            def embed(self, texts):
                return []
        with self.app() as app:
            app.index()
            path.write_text("new", encoding="utf-8")
            app.embedder = WrongCount(dimensions=16)
            self.assertEqual(app.index().failed, 1)
            self.assertEqual(app.store.chunks("notes", "note.md")[0].text, "old")

    def test_removed_source_configuration_keeps_existing_index(self):
        other = self.root / "other"
        other.mkdir()
        self.write_note("first")
        (other / "note.txt").write_text("second")
        self.write_config(sources=[{"id": "notes", "path": "notes"}, {"id": "other", "path": "other"}])
        with self.app() as app:
            app.index()
        self.write_config()
        with self.app() as app:
            self.assertEqual(app.index().deleted, 0)
            self.assertEqual(len(app.store.list_documents()), 2)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_windows_junction_does_not_escape_selected_folder(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.txt").write_text("external document", encoding="utf-8")
        link = self.notes / "linked"
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, timeout=10)
        if result.returncode:
            self.skipTest("host does not permit creating a junction")
        with self.app() as app:
            self.assertEqual(app.index().indexed, 0)
            self.assertEqual(app.store.list_documents(), [])
        self.assertEqual((outside / "private.txt").read_text(), "external document")

    def test_interrupted_job_is_recovered_when_the_next_writer_starts(self):
        self.write_note()
        with self.app() as app:
            app.index()
            app.store.start_job(app.store.active_generation())
        with self.app() as app:
            app.index()
            self.assertEqual([job["status"] for job in app.store.jobs()], ["completed", "interrupted", "completed"])

    def test_deleting_a_never_indexed_invalid_file_clears_its_current_error(self):
        path = self.notes / "invalid.txt"
        path.write_bytes(b"\xff")
        with self.app() as app:
            self.assertEqual(app.index().failed, 1)
            self.assertEqual(len(app.store.status()["document_errors"]), 1)
            path.unlink()
            report = app.index()
            self.assertEqual((report.failed, report.deleted), (0, 0))
            self.assertEqual(app.store.status()["document_errors"], [])


if __name__ == "__main__":
    unittest.main()
