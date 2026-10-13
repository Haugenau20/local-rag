# Local RAG

Local RAG will search selected folders on a Windows computer and answer questions using models running on that same computer. Document text, embeddings, queries, and answers stay local during normal operation.

Supported inputs are text-based PDF, Word (`.docx`), UTF-8 Markdown, and plain text. Citations retain PDF pages, Word paragraphs/table cells, or source-text line spans. Updates and deletions preserve complete document revisions.

## Project status

Version 0.2 implements native Windows indexing for all four formats, exact citation spans, atomic document updates, persistent jobs, and safe full-generation rebuilds. SQLite stores metadata and test vectors together. PDF/Word parsing uses pinned pypdf, python-docx, lxml, and typing-extensions dependencies on Python 3.12.

The only embedder is a deterministic CPU test provider. Its vectors validate pipeline behavior; they do not provide meaningful semantic search. Model inference, answers, OCR, and the browser interface are later milestones. GPU work requires separate explicit user approval; see [AGENTS.md](AGENTS.md).

- [Document service design](docs/plans/2026-10-13-local-document-rag-design.md)
- [Model runtime contract](docs/model-runtime-contract.md)
- [Implementation milestones](docs/implementation-plan.md)
- [PDF and Word extraction support](docs/extraction-formats.md)

## Related repositories

- [local-model-runtime](https://github.com/Haugenau20/local-model-runtime) owns model serving and GPU lifecycle management through Docker on WSL2.
- [ai_rag](https://github.com/Haugenau20/ai_rag) is a reference for reviewed components and regression cases.

Local RAG runs natively on Windows and calls the runtime through local HTTP endpoints. It does not need CUDA libraries or permission to manage Docker.

## Target end to end milestone

Choose a folder containing a small document collection, index its Markdown and text files, ask a question, and receive an answer with file and line citations. Verify that the same workflow works with outbound internet access blocked after dependencies and model files have been installed.

## Install and configure

From this repository in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

Building the package can install the pinned setuptools and wheel build tools. The parsing dependencies are CPU-only and do not include model runtimes, downloads, network clients, or GPU libraries. Installation may download Python packages; normal indexing reads local files only. For offline installation, download the application wheel and pinned dependencies first, then use `pip install --no-index --find-links <wheel-folder> local-rag`.

Copy [config.example.json](config.example.json) to a location outside the repository and replace `YOUR_USER` and the source folder. Put `data_dir` outside every configured source and outside the source repository. Relative paths are resolved against the configuration file's folder. Sources require unique stable IDs; reusing an ID for a different root is rejected.

## Index and inspect

```powershell
.\.venv\Scripts\python.exe -m local_rag --config C:\path\to\local-rag.json index --test-embeddings
.\.venv\Scripts\python.exe -m local_rag --config C:\path\to\local-rag.json status
.\.venv\Scripts\python.exe -m local_rag --config C:\path\to\local-rag.json inspect --source notes --path example.md
```

`inspect` returns the indexed revision, excerpts, heading paths, a format-specific `locator`, and 0-based character offsets into normalized extracted text. Each excerpt is exactly the extracted text slice `[start_offset:end_offset]`. PDF locators identify physical page numbers and page labels; Word locators identify body/header/footer paragraphs or table cells. Word page numbers are not guessed. Text locators use 1-based inclusive source lines; line fields on PDF/Word excerpts refer to extracted text rather than binary-file lines. Citations are marked stale when the original file changed or is unavailable.

Scanned/image-only documents report `ocr_required`; encrypted, corrupt, unsupported, and oversized documents report an explicit extraction error. Those failures preserve the previous indexed revision. See [format support and limits](docs/extraction-formats.md).

Re-running `index` skips unchanged content. Confirmed deletions and empty documents clear old chunks. Read failures and incomplete source scans preserve previous content and appear in `status`. Symlinks and junctions are not followed. Only one indexing writer can run per data directory.

If the embedding or extraction/chunking profile changes, use `index --test-embeddings --rebuild`. Rebuilds include every configured source and activate only after all succeed. Failed rebuilds retain the previous generation. Retired and failed generations remain in the database; automatic storage cleanup is deferred. Removing a source from configuration does not delete its old index during incremental runs; a full rebuild creates the configured set.

Upgrading from 0.1 migrates the citation schema without deleting indexed content. The extraction profile changed, so existing indexes remain inspectable but require an explicit rebuild before the next indexing run. No rebuild is performed automatically.

Commands return JSON. Exit codes are 0 for success, 1 for a completed indexing run with failures, and 2 for configuration or command errors. An interrupted process leaves its last complete document revisions intact; the next writer marks unfinished jobs interrupted.

## Test and run the synthetic pilot

```powershell
.\.venv\Scripts\python.exe -m pip install ".[test]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools\pilot.py --documents 50
.\.venv\Scripts\python.exe tools\format_pilot.py
```

The tests and pilots use synthetic temporary folders. The test extra adds ReportLab and Pillow for fixture generation. Mixed-format indexing is tested with network connections and subprocess creation forbidden. The storage pilot measures persistence and exact CPU vector scans; the format pilot verifies eight expected passage/citation references from [the synthetic collection](examples/format-gold-set.json). Neither evaluates model or retrieval quality.

With the parsing/test dependencies installed, tests can also run without installing the package itself:

```powershell
$env:PYTHONPATH = "$PWD\src"
py -3.12 -m unittest discover -s tests -v
```
