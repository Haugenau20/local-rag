"""Local PDF/Word extraction with explicit format limits and source locators."""

import hashlib
from io import BytesIO
import re
import zipfile

from .documents import DocumentSegment, TextDocument

MAX_PDF_PAGES = 1000
MAX_PDF_PAGE_CONTENT_BYTES = 8 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_CHARS = 2_000_000
MAX_TABLE_DEPTH = 16


class ExtractionError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message}")


def _compose(raw, units):
    parts, segments = [], []
    length = 0
    for text, locator, headings in units:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if "\x00" in text:
            raise ExtractionError("invalid_document_text", "extraction produced NUL characters")
        if parts:
            parts.append("\n\n")
            length += 2
        start = length
        length += len(text)
        if length > MAX_EXTRACTED_CHARS:
            raise ExtractionError("document_limit", "extracted text exceeds the configured format limit")
        parts.append(text)
        segments.append(DocumentSegment(start, length, locator, tuple(headings)))
    return TextDocument("".join(parts), hashlib.sha256(raw).hexdigest(), tuple(segments))


def _has_images(resources, depth=0, visited=None):
    if not resources or depth > 16:
        return False
    visited = set() if visited is None else visited
    resources = resources.get_object()
    objects = resources.get("/XObject")
    if not objects:
        return False
    for item in objects.get_object().values():
        item = item.get_object()
        if id(item) in visited:
            continue
        visited.add(id(item))
        if item.get("/Subtype") == "/Image":
            return True
        if item.get("/Subtype") == "/Form" and _has_images(item.get("/Resources"), depth + 1, visited):
            return True
    return False


def extract_pdf(raw):
    from pypdf import PdfReader
    class TextOnlyReader(PdfReader):
        def _handle_encryption(self, password):
            # pypdf 6.10 otherwise tries an empty password in its constructor.
            # Reject first, without invoking optional crypto backends.
            raise ExtractionError("pdf_encrypted", "encrypted PDFs require a separately supplied unlocked copy")
    try:
        reader = TextOnlyReader(BytesIO(raw), strict=True)
        count = len(reader.pages)
        if count > MAX_PDF_PAGES:
            raise ExtractionError("document_limit", f"PDF has more than {MAX_PDF_PAGES} pages")
        if count == 0:
            raise ExtractionError("invalid_pdf", "PDF has no pages")
        labels = reader.page_labels
        def units():
            for number, page in enumerate(reader.pages, 1):
                content = page.get_contents()
                if content is not None and len(content.get_data()) > MAX_PDF_PAGE_CONTENT_BYTES:
                    raise ExtractionError("document_limit", f"PDF page {number} has an oversized content stream")
                text = page.extract_text() or ""
                if not text.strip():
                    operations = content.operations if content is not None else ()
                    if _has_images(page.get("/Resources")) or any(op == b"INLINE IMAGE" for _, op in operations):
                        raise ExtractionError("ocr_required", f"PDF page {number} contains images without extractable text; OCR is not run automatically")
                    if any(op in {b"Do", b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"sh"} for _, op in operations):
                        raise ExtractionError("pdf_no_text", f"PDF page {number} has drawn content without extractable text")
                yield text, {"kind": "pdf", "page": number, "page_label": labels[number - 1]}, ()
        return _compose(raw, units())
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("invalid_pdf", "PDF could not be parsed or its text could not be extracted") from None


def _heading_level(paragraph):
    values = paragraph._p.xpath("./w:pPr/w:outlineLvl/@w:val")
    style = paragraph.style
    visited = set()
    while not values and style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        values = style.element.xpath("./w:pPr/w:outlineLvl/@w:val")
        if values:
            break
        match = re.fullmatch(r"Heading ([1-9])", style.name or "", re.IGNORECASE)
        if match:
            return int(match[1])
        style = style.base_style
    if values and values[0].isdigit() and 0 <= int(values[0]) < 9:
        return int(values[0]) + 1
    return None


def extract_docx(raw):
    if raw.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ExtractionError("unsupported_docx_container", "password-protected or legacy OLE Word files require an unencrypted .docx copy")
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if sum(info.file_size for info in entries) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise ExtractionError("document_limit", "DOCX expanded archive exceeds the format limit")
            if any(info.flag_bits & 1 for info in entries):
                raise ExtractionError("docx_encrypted", "encrypted DOCX archives are not supported")
            if len({info.filename for info in entries}) != len(entries):
                raise ExtractionError("invalid_docx", "DOCX archive contains duplicate member names")
            if "word/document.xml" not in archive.namelist():
                raise ExtractionError("invalid_docx", "archive does not contain a Word document body")
        document = Document(BytesIO(raw))
        unsupported = ".//w:txbxContent | .//w:altChunk | .//w:sdt | .//w:ins | .//w:del | .//w:footnoteReference | .//w:endnoteReference | .//m:oMath | .//m:oMathPara"
        def validate_story(element):
            if element.xpath(unsupported):
                raise ExtractionError("unsupported_docx_content", "text boxes, imported content, controls, pending tracked changes, footnotes/endnotes, and equations require a simplified document copy")
        validate_story(document.element.body)
        headings = []
        def paragraphs(container, base_locator, inherited, table_path=()):
            paragraph_number, table_number = 0, 0
            local_headings = list(inherited)
            for block in container.iter_inner_content():
                if isinstance(block, Paragraph):
                    paragraph_number += 1
                    level = _heading_level(block)
                    if level is not None and block.text.strip():
                        local_headings = [(n, title) for n, title in local_headings if n < level]
                        local_headings.append((level, block.text.strip()))
                    locator = dict(base_locator, paragraph=paragraph_number)
                    yield block.text, locator, tuple(title for _, title in local_headings)
                elif isinstance(block, Table):
                    table_number += 1
                    if len(table_path) >= MAX_TABLE_DEPTH:
                        raise ExtractionError("document_limit", "DOCX tables exceed the nesting limit")
                    seen_cells = set()
                    for row_number, row in enumerate(block.rows, 1):
                        for cell_number, cell in enumerate(row.cells, 1):
                            if cell._tc in seen_cells:
                                continue
                            seen_cells.add(cell._tc)
                            position = {"table": table_number, "row": row_number, "cell": cell_number}
                            path = table_path + (position,)
                            locator = dict(base_locator, **position, paragraph_scope="table_cell", table_path=list(path))
                            yield from paragraphs(cell, locator, local_headings, path)
        def units():
            yield from paragraphs(document, {"kind": "docx", "part": "body"}, headings)
            visited = set()
            for number, section in enumerate(document.sections, 1):
                for part, attribute, variant in (
                    ("header", "header", "default"), ("header", "first_page_header", "first"),
                    ("header", "even_page_header", "even"), ("footer", "footer", "default"),
                    ("footer", "first_page_footer", "first"), ("footer", "even_page_footer", "even"),
                ):
                    story = getattr(section, attribute)
                    if story.is_linked_to_previous or story.part.partname in visited:
                        continue
                    visited.add(story.part.partname)
                    validate_story(story._element)
                    locator = {"kind": "docx", "part": part, "section": number, "variant": variant}
                    yield from paragraphs(story, locator, ())
        result = _compose(raw, units())
        body_has_text = any(result.text[s.start_offset:s.end_offset].strip()
                            for s in result.segments if s.locator.get("part") == "body")
        if not body_has_text and document.element.body.xpath(".//w:drawing | .//w:pict"):
            raise ExtractionError("ocr_required", "Word document contains pictures without extractable text; OCR is not run automatically")
        return result
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("invalid_docx", "DOCX could not be parsed or its body text could not be extracted") from None
