"""Explicit sources and application storage, resolved relative to configuration."""

from dataclasses import dataclass
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class Source:
    id: str
    root: Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    sources: tuple[Source, ...]
    max_chars: int = 1200
    max_file_bytes: int = 5 * 1024 * 1024

    @classmethod
    def load(cls, path: Path | str) -> "Settings":
        path = Path(path).resolve()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("configuration must be a JSON object")
        unknown = set(data) - {"data_dir", "sources", "max_chars", "max_file_bytes"}
        if unknown:
            raise ValueError(f"unknown configuration fields: {', '.join(sorted(unknown))}")
        def location(value):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("paths must be nonempty strings")
            return (path.parent / value).resolve()
        data_dir = location(data.get("data_dir"))
        entries = data.get("sources")
        if not isinstance(entries, list) or not entries:
            raise ValueError("at least one explicit source is required")
        sources = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"id", "path"}:
                raise ValueError("each source requires exactly id and path")
            sid = entry["id"]
            if not isinstance(sid, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", sid):
                raise ValueError("source IDs must contain 1-64 letters, numbers, underscores or hyphens")
            if sid.casefold() in seen:
                raise ValueError(f"duplicate source ID: {sid}")
            seen.add(sid.casefold())
            root = location(entry["path"])
            if data_dir.is_relative_to(root):
                raise ValueError("data_dir must be outside all configured source folders")
            sources.append(Source(sid, root))
        values = {}
        for key, default in (("max_chars", 1200), ("max_file_bytes", 5 * 1024 * 1024)):
            value = data.get(key, default)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{key} must be a positive integer")
            values[key] = value
        return cls(data_dir=data_dir, sources=tuple(sources), **values)

    def source(self, source_id: str) -> Source:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise ValueError(f"unknown source: {source_id}")


def validate_relative_path(value: str) -> str:
    """Canonical citation keys do not allow absolute paths or traversal."""
    from pathlib import PureWindowsPath, PurePosixPath
    value = value.replace("\\", "/")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if not value or windows.drive or windows.root or posix.is_absolute() or ".." in posix.parts:
        raise ValueError("document path must be relative to its configured source")
    if posix.as_posix() == ".":
        raise ValueError("document path must name a file")
    return posix.as_posix()
