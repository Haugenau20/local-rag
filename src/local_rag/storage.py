"""SQLite revisions and exact CPU vector scans share one transaction boundary."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
from pathlib import Path
import sqlite3
import struct


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class StoredDocument:
    source_id: str
    path: str
    revision: str
    content_hash: str
    chunk_count: int
    last_error: str | None


@dataclass(frozen=True)
class StoredChunk:
    chunk_id: str
    source_id: str
    path: str
    revision: str
    text: str
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int
    heading_path: tuple[str, ...]
    embedding: list[float]
    locator: dict


@dataclass(frozen=True)
class VectorHit:
    chunk: StoredChunk
    score: float


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        has_meta = self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        if has_meta:
            old = self.connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if old and old[0] not in {"1", "2"}:
                self.close()
                raise ValueError("unsupported index schema version")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, root TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS generations (
                id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL,
                profile TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                generation INTEGER NOT NULL REFERENCES generations(id),
                source_id TEXT NOT NULL REFERENCES sources(id), path TEXT NOT NULL,
                revision TEXT NOT NULL, content_hash TEXT NOT NULL,
                indexed_at TEXT NOT NULL, last_error TEXT,
                PRIMARY KEY (generation, source_id, path)
            );
            CREATE TABLE IF NOT EXISTS chunks (
                generation INTEGER NOT NULL, source_id TEXT NOT NULL, path TEXT NOT NULL,
                ordinal INTEGER NOT NULL, chunk_id TEXT NOT NULL, revision TEXT NOT NULL,
                text TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
                start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
                heading_path TEXT NOT NULL, embedding BLOB NOT NULL,
                PRIMARY KEY (generation, source_id, path, ordinal),
                FOREIGN KEY (generation, source_id, path)
                    REFERENCES documents(generation, source_id, path) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS document_errors (
                generation INTEGER NOT NULL, source_id TEXT NOT NULL, path TEXT NOT NULL,
                message TEXT NOT NULL,
                PRIMARY KEY (generation, source_id, path)
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, generation INTEGER NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
                indexed INTEGER NOT NULL DEFAULT 0, unchanged INTEGER NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
                errors TEXT NOT NULL DEFAULT '[]'
            );
        """)
        self.connection.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', '1')")
        version = self.connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        if version == "1":
            with self.transaction():
                self.connection.execute("ALTER TABLE chunks ADD COLUMN locator TEXT NOT NULL DEFAULT '{}'")
                self.connection.execute("UPDATE meta SET value='2' WHERE key='schema_version'")

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def register_sources(self, sources):
        with self.transaction():
            for source in sources:
                old = self.connection.execute("SELECT root FROM sources WHERE id=?", (source.id,)).fetchone()
                if old and Path(old[0]) != source.root:
                    raise ValueError(f"source {source.id!r} previously used another root; use a new source ID")
                self.connection.execute("INSERT OR IGNORE INTO sources VALUES (?, ?)", (source.id, str(source.root)))

    def active_generation(self):
        row = self.connection.execute("SELECT value FROM meta WHERE key='active_generation'").fetchone()
        return int(row[0]) if row else None

    def source_root(self, source_id):
        row = self.connection.execute("SELECT root FROM sources WHERE id=?", (source_id,)).fetchone()
        return Path(row[0]) if row else None

    def generation_profile(self, generation=None):
        generation = self.active_generation() if generation is None else generation
        row = self.connection.execute("SELECT fingerprint,profile FROM generations WHERE id=?", (generation,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def new_generation(self, profile):
        with self.transaction():
            return self.connection.execute(
                "INSERT INTO generations(fingerprint,profile,status,created_at) VALUES (?,?,?,?)",
                (profile.fingerprint, json.dumps(asdict(profile)), "building", timestamp()),
            ).lastrowid

    def activate_generation(self, generation):
        with self.transaction():
            self.connection.execute("UPDATE generations SET status='retired' WHERE status='active'")
            self.connection.execute("UPDATE generations SET status='active' WHERE id=?", (generation,))
            self.connection.execute("INSERT OR REPLACE INTO meta VALUES ('active_generation',?)", (str(generation),))

    def fail_generation(self, generation):
        self.connection.execute("UPDATE generations SET status='failed' WHERE id=?", (generation,))

    def document(self, generation, source_id, path):
        return self.connection.execute("SELECT * FROM documents WHERE generation=? AND source_id=? AND path=?",
                                       (generation, source_id, path)).fetchone()

    def tracked_paths(self, generation, source_id):
        return {r[0] for r in self.connection.execute("""
            SELECT path FROM documents WHERE generation=? AND source_id=?
            UNION SELECT path FROM document_errors WHERE generation=? AND source_id=?
        """, (generation, source_id, generation, source_id))}

    def replace_document(self, generation, source_id, path, document, chunks, vectors, fingerprint):
        revision = hashlib.sha256(f"{source_id}\0{path}\0{document.content_hash}\0{fingerprint}".encode()).hexdigest()
        with self.transaction():
            self.connection.execute("DELETE FROM documents WHERE generation=? AND source_id=? AND path=?", (generation, source_id, path))
            self.connection.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,NULL)",
                                    (generation, source_id, path, revision, document.content_hash, timestamp()))
            for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
                cid = hashlib.sha256(f"{revision}:{i}".encode()).hexdigest()
                self.connection.execute("""INSERT INTO chunks
                    (generation,source_id,path,ordinal,chunk_id,revision,text,start_line,end_line,
                     start_offset,end_offset,heading_path,embedding,locator)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (generation, source_id, path, i, cid, revision, chunk.text, chunk.start_line,
                     chunk.end_line, chunk.start_offset, chunk.end_offset, json.dumps(chunk.heading_path),
                     struct.pack(f"<{len(vector)}d", *vector), json.dumps(chunk.locator)))
            self.connection.execute("DELETE FROM document_errors WHERE generation=? AND source_id=? AND path=?", (generation, source_id, path))

    def record_error(self, generation, source_id, path, message):
        with self.transaction():
            self.connection.execute("INSERT OR REPLACE INTO document_errors VALUES (?,?,?,?)", (generation, source_id, path, message))
            self.connection.execute("UPDATE documents SET last_error=? WHERE generation=? AND source_id=? AND path=?",
                                    (message, generation, source_id, path))

    def clear_error(self, generation, source_id, path):
        with self.transaction():
            self.connection.execute("DELETE FROM document_errors WHERE generation=? AND source_id=? AND path=?", (generation, source_id, path))
            self.connection.execute("UPDATE documents SET last_error=NULL WHERE generation=? AND source_id=? AND path=?",
                                    (generation, source_id, path))

    def delete_document(self, generation, source_id, path):
        with self.transaction():
            self.connection.execute("DELETE FROM documents WHERE generation=? AND source_id=? AND path=?", (generation, source_id, path))
            self.connection.execute("DELETE FROM document_errors WHERE generation=? AND source_id=? AND path=?", (generation, source_id, path))

    def list_documents(self):
        rows = self.connection.execute("""
            SELECT d.source_id,d.path,d.revision,d.content_hash,d.last_error,COUNT(c.ordinal) AS chunk_count
            FROM documents d LEFT JOIN chunks c
                ON c.generation=d.generation AND c.source_id=d.source_id AND c.path=d.path
            WHERE d.generation=? GROUP BY d.source_id,d.path ORDER BY d.source_id,d.path
        """, (self.active_generation(),))
        return [StoredDocument(**dict(row)) for row in rows]

    @staticmethod
    def _chunk(row):
        blob = row["embedding"]
        return StoredChunk(row["chunk_id"], row["source_id"], row["path"], row["revision"], row["text"],
                           row["start_line"], row["end_line"], row["start_offset"], row["end_offset"],
                           tuple(json.loads(row["heading_path"])), list(struct.unpack(f"<{len(blob)//8}d", blob)),
                           json.loads(row["locator"]) or {"kind": "text", "start_line": row["start_line"], "end_line": row["end_line"]})

    def chunks(self, source_id, path):
        rows = self.connection.execute("SELECT * FROM chunks WHERE generation=? AND source_id=? AND path=? ORDER BY ordinal",
                                       (self.active_generation(), source_id, path))
        return [self._chunk(row) for row in rows]

    def vector_search(self, vector, *, fingerprint, limit=10):
        from .embeddings import validate_vectors
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        active = self.generation_profile()
        if not active or fingerprint != active[0]:
            raise ValueError("query profile does not match the active index")
        validate_vectors([vector], 1, active[1]["dimensions"])
        query_norm = math.sqrt(sum(v * v for v in vector))
        def candidates():
            for row in self.connection.execute("SELECT * FROM chunks WHERE generation=?", (self.active_generation(),)):
                chunk = self._chunk(row)
                norm = math.sqrt(sum(v * v for v in chunk.embedding))
                score = sum(a*b for a,b in zip(vector, chunk.embedding)) / (query_norm * norm) if query_norm and norm else 0.0
                yield VectorHit(chunk, score)
        return heapq.nlargest(limit, candidates(), key=lambda hit: hit.score)

    def start_job(self, generation):
        return self.connection.execute("INSERT INTO jobs(generation,started_at,status) VALUES (?,?,'running')",
                                       (generation, timestamp())).lastrowid

    def finish_job(self, job, report, status):
        self.connection.execute("UPDATE jobs SET finished_at=?,status=?,indexed=?,unchanged=?,deleted=?,failed=?,errors=? WHERE id=?",
                                (timestamp(), status, report.indexed, report.unchanged, report.deleted, report.failed,
                                 json.dumps(report.errors), job))

    def recover_jobs(self):
        self.connection.execute("UPDATE jobs SET status='interrupted',finished_at=? WHERE status='running'", (timestamp(),))
        self.connection.execute("UPDATE generations SET status='failed' WHERE status='building'")

    def jobs(self):
        return [dict(row) | {"errors": json.loads(row["errors"])} for row in self.connection.execute("SELECT * FROM jobs ORDER BY id")]

    def status(self):
        active = self.active_generation()
        count = self.connection.execute("SELECT COUNT(*) FROM documents WHERE generation=?", (active,)).fetchone()[0]
        chunks = self.connection.execute("SELECT COUNT(*) FROM chunks WHERE generation=?", (active,)).fetchone()[0]
        errors = [dict(r) for r in self.connection.execute("SELECT source_id,path,message FROM document_errors WHERE generation=?", (active,))]
        return {"active_generation": active, "profile": self.generation_profile(), "document_count": count,
                "chunk_count": chunks, "document_errors": errors, "jobs": self.jobs()}
