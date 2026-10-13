# PDF and Word extraction support

The indexer reads local files with CPU parsing libraries. It does not launch Word, LibreOffice, OCR tools, model servers, or containers. Source documents are read-only inputs. Document revisions are replaced only after extraction, chunking, vector validation, and storage succeed.

## Supported content and citations

| Format | Extracted content | Citation locator |
|---|---|---|
| Markdown and UTF-8 text | Normalized text and Markdown heading sections | Source lines and character offsets |
| Text-based PDF | Extractable page text, including existing OCR text layers | Physical page number, page label, and offsets into extracted text |
| Word DOCX | Body paragraphs, heading hierarchy, tables/nested tables, hyperlink display text, defined headers and footers | Document part, paragraph number, section/variant for headers and footers, table/row/cell for tables |

PDF chunks never cross page boundaries. Blank pages keep their physical positions without producing chunks. PDF reading order follows the parser's text extraction; multi-column layouts, positioned tables, figures, and mathematical layout are not reconstructed. Physical page numbers start at 1. A printed page label such as `i` is separate from physical page 1.

Word paragraphs are numbered from 1 within their document part, including empty and heading paragraphs. Main-body paragraph numbers exclude paragraphs inside tables. Table citations include a `table_path` for nested tables and a paragraph number within the cell. Merged cells are extracted once at their first row/cell position. Defined header/footer variants are cited to their originating section; linked copies are not duplicated. Word pagination depends on rendering, so no page numbers are invented.

Offsets are zero-based Python character indices into the concatenated extracted text, with an exclusive end. Extracted units are separated by two newlines; chunks stay within one PDF page or Word paragraph. Line fields on binary-format chunks describe extracted text, not physical lines in the binary file. The `locator` is the authoritative source reference.

## Explicit failure cases

- `ocr_required`: an image-only PDF page or Word body has no extractable text. A PDF containing both readable pages and a scanned page fails as a whole to avoid silently losing the scanned content. No OCR is started.
- `pdf_no_text`: a PDF page has drawn content but no extractable text. Truly blank pages are allowed.
- `pdf_encrypted`: the PDF is encrypted. Supply a separately unlocked copy; no password guessing or automatic decryption is attempted.
- `unsupported_docx_container`: the DOCX input uses an OLE container, as password-protected or legacy Word files may do. Supply an unencrypted modern DOCX copy.
- `unsupported_docx_content`: text boxes, imported HTML/content, content controls, pending tracked changes, footnote/endnote references, or native Word equations need a simplified copy. They are explicitly rejected rather than silently omitted.
- `invalid_pdf` or `invalid_docx`: the file cannot be parsed as the declared format.
- `unsupported_format`: legacy `.doc` is detected but not converted. Other non-selected file extensions are ignored by discovery.
- `document_limit`: the file or extracted structure exceeds a bound.

Pictures within otherwise readable documents are not OCRed. Their embedded text is outside this text-extraction batch. Word comments, automatic list numbering, and rendered field values are not synthesized. PDF text extraction cannot verify the accuracy of an existing OCR layer.

Errors appear in the indexing report, document status, and persisted job outcomes. New reports include `error_code`; stored error messages also include that code. A failed update retains the previous text, citation locators, vectors, and revision. A supported document that becomes truly empty clears its old chunks. A full-generation rebuild with any extraction failure leaves the previous generation active.

## Resource bounds

The source-file limit is configurable through `max_file_bytes` and defaults to 5 MiB. Additional format guards currently live in `local_rag.extractors`:

- At most 1,000 PDF pages and 8 MiB decoded content per page before text extraction.
- At most 20 MiB total expanded DOCX archive members and 16 levels of nested tables.
- At most 2,000,000 extracted characters per document.

The PDF guard checks decoded content; decoding still uses pypdf's own codec bounds. Parsing runs in process. These limits are not process isolation or a wall-clock timeout for arbitrary hostile files.

## Dependencies and offline operation

The tested application pins pypdf 6.10.0, python-docx 1.2.0, lxml 6.1.1, and typing-extensions 4.16.0. Fixture generation additionally pins ReportLab 4.4.9, Pillow 12.3.0, and charset-normalizer 3.5.2 through the test extra. A clean Python 3.12 environment can be installed from the downloaded wheel collection without network access.

The extraction profile records the parser/chunker revision. Version 0.2 adds persisted citation JSON with a transactional SQLite schema 1-to-2 migration. Existing text chunks retain their old locations. Indexing after the profile change requires an explicit full rebuild; inspection of previous content remains available.

## Validation

Tests use synthetic files for repeated PDF passages, blank and scanned pages, encryption, corrupt files, page labels, Word headings/styles, tables and merged cells, Unicode, headers/footers, archive limits, and unsupported constructs. They also cover failed-update preservation, schema migration, CLI citation output, and mixed-format indexing with network connections and subprocess creation forbidden.

`tools/format_pilot.py` creates four temporary documents, reopens their persisted index, and verifies eight nonempty question/passage/citation references. It checks extraction and source locations, not semantic retrieval or answer generation.

Parser behavior references: [pypdf text extraction](https://pypdf.readthedocs.io/en/6.10.0/user/extract-text.html), [python-docx document API](https://python-docx.readthedocs.io/en/latest/api/document.html), and [Word styles](https://python-docx.readthedocs.io/en/latest/user/styles-using.html).
