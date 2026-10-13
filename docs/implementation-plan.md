# Local RAG implementation milestones

Build a working document assistant in small increments. Each milestone has a visible result and an acceptance gate; completing a scaffold alone does not complete a milestone.

## Milestone 0 Define the boundary

Record the native Windows and WSL2 service split, document scope, local-operation requirements, and runtime contract. Capture unresolved choices without making them prerequisites for unrelated work.

Deliverables: the design, runtime contract, and repository README. These initial documents are ready for review; their detailed implementation proposals remain adjustable.

## Milestone 1 Build reliable document indexing

Implemented on 13 October 2026. The package provides `index`, `status`, and `inspect` commands, a deterministic CPU test embedder, SQLite revision/vector persistence, source boundary checks, exact Markdown/text citation spans, job recovery, writer locking, and safe full-generation rebuilds. See the [README](../README.md) for installation and usage.

The acceptance suite covers 52 cases. In the restricted Windows test run, 51 passed and one symbolic-link creation test was skipped because the host denied that operation; the real Windows junction boundary test passed. Packaging was built without network access. The synthetic pilot verified indexing and reopening 200 documents. See [the storage decision](decisions/0001-sqlite-pilot-storage.md) for measurements and limits.

No models, GPU workloads, model services, or Docker/WSL startup are part of this milestone. Future GPU execution requires separate explicit user approval.

Set up a native Python package with reproducible dependencies and tests. Python 3.12 is the initial candidate; verify dependency compatibility before pinning it. Select the vector backend using persistence, revision, Windows installation, and pilot performance requirements. Keep user data outside the repository.

Implement explicitly configured source folders, Markdown/text extraction, exact line locators, section-aware chunks, content hashes, document revisions, and observable indexing jobs. Start with a deterministic test embedding provider so pipeline behavior does not depend on the GPU.

Write failing behavior tests before production implementation. Required regressions include empty files clearing old chunks, temporary read failures retaining old revisions, unavailable roots preserving indexes, confirmed deletions clearing results, source IDs isolating identical filenames, interrupted updates preserving complete revisions, and embedding-profile incompatibility requiring a rebuild.

Gate: index, update, delete, close, reopen, and inspect a synthetic document collection with exact source locations and consistent persisted state.

## Milestone 2 Bring up the local runtime

In `local-model-runtime`, verify Docker Desktop's WSL2 engine and GPU access from a container. Compare a small set of backend/model combinations using synthetic documents and the contract. Record model revisions, tokenizer files, limits, license terms, VRAM use, cold-start time, and latency.

Gate: local embedding and generation endpoints pass contract tests; start, status, warm-up, unloading, and stop behavior are documented. Model files persist across container restarts. No user documents are needed for backend selection.

Milestones 1 and 2 can proceed independently after the contract is established.

Milestone 2 remains pending. Its GPU verification and any inference workload require explicit user approval before execution.

## Milestone 3 Answer document questions locally

Connect real embeddings, vector search, corpus-wide lexical search, evidence selection, and local generation. Provide CLI index, search, ask, and status operations. Validate citation identifiers and show original passages and locations. Preserve useful search results when generation is unavailable.

Create at least 20 non-empty synthetic document questions, covering exact terms, paraphrases, multiple sources, contradictions, and absent answers. Measure retrieval recall, citation validity, answer grounding, and latency. An empty evaluation corpus must fail rather than pass.

Gate: the Markdown/text assistant returns grounded answers with file/line citations and completes the same workflow with outbound networking blocked after provisioning.

## Milestone 4 Add PDF and Word

The CPU-only extraction adapters were implemented early on 13 October 2026, before model-runtime integration. Version 0.2 supports PDF page citations and DOCX heading/paragraph/table/header/footer citations, preserves old revisions on extraction errors, and migrates existing citation storage without data deletion. See [supported formats and limits](extraction-formats.md).

The combined suite contains 92 cases; the restricted Windows run passed 91 with one symbolic-link creation test skipped. The mixed-format pilot verifies eight persisted passage/location references. OCR, model inference, and semantic answer evaluation remain pending. GPU execution still requires separate explicit user approval.

Add extraction adapters for text-based PDF and `.docx`. Preserve page references for PDF and heading/paragraph references for Word. Report encrypted, unsupported, scanned, or corrupt documents explicitly. Evaluate OCR separately using representative scanned PDFs.

Gate: supported formats remain traceable to the correct source locations, and extraction failures do not remove an existing good document revision.

## Milestone 5 Improve retrieval and daily use

Benchmark a local reranker against the existing evaluation set. Add a local browser interface, source controls, status and error views, citation navigation, background refresh, and a launcher for both services. Windows file notifications are an optimization; successful periodic reconciliation remains the source of truth. Add MCP when its client workflow is defined.

Gate: measured retrieval improvements, repeatable start/stop behavior, visible progress, and no lost or stale documents after edits and restarts.

## Reference project review

Review `ai_rag` components only when a milestone needs them. Reuse code after establishing behavior tests and checking model/source assumptions. Preserve the original repository and unmerged branches until the replacement is useful. Avoid copying Jira, Bitbucket, corporate proxy configuration, model-specific tokenizer assumptions, or historical generated files into the new application.
