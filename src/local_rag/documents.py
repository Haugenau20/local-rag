"""Bounded UTF-8 extraction and citations into normalized document text."""

from bisect import bisect_right
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re

EXTRACTOR_VERSION = "document-formats-v2:pypdf-6.10.0:python-docx-1.2.0"
CHUNKER_VERSION = "document-spans-v2"


@dataclass(frozen=True)
class DocumentSegment:
    start_offset: int
    end_offset: int
    locator: dict
    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class TextDocument:
    text: str
    content_hash: str
    segments: tuple[DocumentSegment, ...] = ()


@dataclass(frozen=True)
class Chunk:
    text: str
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int
    heading_path: tuple[str, ...]
    locator: dict = field(default_factory=dict)


def extract_text(raw: bytes) -> TextDocument:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("unsupported text encoding; UTF-8 is required") from None
    if "\x00" in text:
        raise ValueError("NUL bytes are not supported in text documents")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return TextDocument(text, hashlib.sha256(raw).hexdigest())


def read_document(path: Path, max_bytes: int) -> TextDocument:
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"document exceeds the {max_bytes}-byte limit")
    suffix = path.suffix.casefold()
    if suffix in {".pdf", ".docx"}:
        from .extractors import extract_pdf, extract_docx
        return extract_pdf(raw) if suffix == ".pdf" else extract_docx(raw)
    if suffix not in {".md", ".txt"}:
        from .extractors import ExtractionError
        raise ExtractionError("unsupported_format", "supported formats are .md, .txt, .pdf and .docx; convert legacy Word files to .docx")
    return extract_text(raw)


def chunk_document(document: TextDocument, *, markdown: bool, max_chars: int) -> list[Chunk]:
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError("max_chars must be positive")
    text = document.text
    if document.segments:
        line_starts = [0] + [i + 1 for i, character in enumerate(text) if character == "\n"]
        result = []
        for segment in document.segments:
            local = TextDocument(text[segment.start_offset:segment.end_offset], document.content_hash)
            for chunk in chunk_document(local, markdown=False, max_chars=max_chars):
                start = segment.start_offset + chunk.start_offset
                end = segment.start_offset + chunk.end_offset
                locator = dict(segment.locator)
                # These lines refer to extracted page/paragraph text, not
                # visual Word pagination or binary-file line numbers.
                locator.update({"start_line": chunk.start_line, "end_line": chunk.end_line})
                result.append(Chunk(chunk.text, bisect_right(line_starts, start),
                                    bisect_right(line_starts, end - 1), start, end,
                                    segment.heading_path, locator))
        return result
    # Only normalized LF characters create source lines. Unicode paragraph
    # separators inside text must not shift file citation line numbers.
    parts = text.split("\n")
    lines = [line + ("\n" if i < len(parts) - 1 else "") for i, line in enumerate(parts)]
    line_starts = []
    offset = 0
    section_start = 0
    headings: list[tuple[int, str]] = []
    current_path: tuple[str, ...] = ()
    sections = []
    fence = None
    for line in lines:
        line_starts.append(offset)
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\n")) if markdown else None
        if marker:
            run, trailing = marker.groups()
            if fence is None:
                fence = (run[0], len(run))
            elif run[0] == fence[0] and len(run) >= fence[1] and not trailing.strip():
                fence = None
        elif markdown and fence is None:
            heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$", line.rstrip("\n"))
            if heading:
                if offset > section_start:
                    sections.append((section_start, offset, current_path))
                level = len(heading[1])
                title = re.sub(r"\s+#+\s*$", "", heading[2])
                headings = [(n, t) for n, t in headings if n < level]
                headings.append((level, title))
                current_path = tuple(t for _, t in headings)
                section_start = offset
        offset += len(line)
    sections.append((section_start, len(text), current_path))
    chunks = []
    for start, end, heading_path in sections:
        while start < end:
            while start < end and text[start].isspace():
                start += 1
            if start == end:
                break
            stop = min(start + max_chars, end)
            if stop < end and not text[stop].isspace():
                boundary = text.rfind("\n", start, stop)
                if boundary > start:
                    stop = boundary
            trimmed = stop
            while trimmed > start and text[trimmed - 1].isspace():
                trimmed -= 1
            if trimmed > start:
                start_line = bisect_right(line_starts, start)
                end_line = bisect_right(line_starts, trimmed - 1)
                chunks.append(Chunk(text[start:trimmed], start_line, end_line, start, trimmed, heading_path,
                                    {"kind": "text", "start_line": start_line, "end_line": end_line}))
            start = stop
    return chunks
