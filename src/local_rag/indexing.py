"""Incremental document ingestion with conservative deletion and atomic revisions."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path

from .config import Settings
from .documents import chunk_document, read_document
from .embeddings import validate_vectors
from .storage import Store

EXTENSIONS = {".md", ".txt", ".pdf", ".docx", ".doc"}
EXCLUDED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}


class ProfileMismatch(ValueError):
    pass


@dataclass(frozen=True)
class ScanResult:
    files: tuple[Path, ...]
    complete: bool
    errors: tuple[str, ...]


@dataclass
class IndexReport:
    indexed: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0
    errors: list[dict] = field(default_factory=list)
    job_id: int | None = None
    generation_activated: bool = False


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def discover(root: Path) -> ScanResult:
    files = []
    errors = []
    try:
        if not root.is_dir() or root.resolve(strict=True) != root:
            raise OSError("source root is unavailable or its canonical location changed")
        def onerror(exc):
            errors.append(f"directory could not be enumerated: {getattr(exc, 'filename', root)}")
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
            dirs[:] = sorted(d for d in dirs if d.casefold() not in EXCLUDED_DIRS and not is_link(Path(directory) / d))
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix.casefold() in EXTENSIONS:
                    files.append(path)
    except OSError as exc:
        errors.append(str(exc))
    return ScanResult(tuple(files), not errors, tuple(errors))


@contextmanager
def writer_lock(path: Path):
    """OS-owned locks release automatically if the indexing process exits."""
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("another indexing job is running for this data directory") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Indexer:
    def __init__(self, settings: Settings, *, embedder):
        self.settings = settings
        self.embedder = embedder
        self.profile = embedder.profile(settings.max_chars)
        self.store = Store(settings.data_dir / "index.sqlite3")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.store.close()

    def index(self, *, source_id=None, rebuild=False):
        selected = (self.settings.source(source_id),) if source_id else self.settings.sources
        if rebuild and source_id:
            raise ValueError("a generation rebuild must include every configured source")
        with writer_lock(self.settings.data_dir / "index.lock"):
            self.store.recover_jobs()
            self.store.register_sources(self.settings.sources)
            old_generation = self.store.active_generation()
            active_profile = self.store.generation_profile()
            if active_profile and active_profile[0] != self.profile.fingerprint and not rebuild:
                raise ProfileMismatch("index profile changed; run index --rebuild --test-embeddings to build a replacement generation")
            generation = self.store.new_generation(self.profile) if rebuild or old_generation is None else old_generation
            if old_generation is None and not rebuild:
                self.store.activate_generation(generation)
            report = IndexReport(job_id=self.store.start_job(generation))
            try:
                for source in selected:
                    self._index_source(source, generation, report)
                if rebuild:
                    if report.failed:
                        self.store.fail_generation(generation)
                    else:
                        self.store.activate_generation(generation)
                        report.generation_activated = True
                self.store.finish_job(report.job_id, report, "partial" if report.failed else "completed")
            except BaseException:
                if rebuild:
                    self.store.fail_generation(generation)
                self.store.finish_job(report.job_id, report, "interrupted")
                raise
            return report

    def _error(self, source, generation, path, message, report, code=None):
        message = message[:300]
        report.failed += 1
        report.errors.append({"source_id": source.id, "path": path, "error": message, "error_code": code})
        if path:
            self.store.record_error(generation, source.id, path, message)

    def _index_source(self, source, generation, report):
        scan = discover(source.root)
        seen = set()
        for message in scan.errors:
            self._error(source, generation, None, message, report)
        for path in scan.files:
            relative = path.relative_to(source.root).as_posix()
            seen.add(relative)
            try:
                if is_link(path):
                    raise ValueError("symlinks and junctions are not indexed")
                canonical = path.resolve(strict=True)
                if not canonical.is_relative_to(source.root):
                    raise ValueError("document is outside its configured source")
                document = read_document(canonical, self.settings.max_file_bytes)
                previous = self.store.document(generation, source.id, relative)
                if previous and previous["content_hash"] == document.content_hash:
                    self.store.clear_error(generation, source.id, relative)
                    report.unchanged += 1
                    continue
                chunks = chunk_document(document, markdown=path.suffix.casefold() == ".md", max_chars=self.settings.max_chars)
                vectors = self.embedder.embed([chunk.text for chunk in chunks]) if chunks else []
                validate_vectors(vectors, len(chunks), self.profile.dimensions)
                self.store.replace_document(generation, source.id, relative, document, chunks, vectors, self.profile.fingerprint)
                report.indexed += 1
            except Exception as exc:
                self._error(source, generation, relative, f"{type(exc).__name__}: {exc}", report, getattr(exc, "code", None))
        if scan.complete:
            try:
                if not source.root.is_dir() or source.root.resolve(strict=True) != source.root:
                    raise OSError("source root became unavailable before deletion reconciliation")
            except OSError as exc:
                self._error(source, generation, None, str(exc), report)
                return
            for relative in self.store.tracked_paths(generation, source.id) - seen:
                was_indexed = self.store.document(generation, source.id, relative) is not None
                self.store.delete_document(generation, source.id, relative)
                report.deleted += int(was_indexed)
