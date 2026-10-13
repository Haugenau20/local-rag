# SQLite storage for the indexing pilot

Milestone 1 stores document metadata, complete active revisions, citation spans, jobs, and binary vectors in SQLite. Exact vector scans run on the CPU through `Store.vector_search`. This is a pilot choice for correctness and installation simplicity, not a commitment to exact scans for a large document library.

## Alternatives

A separate vector database would add an installation/service dependency and a second persistence boundary before document indexing behavior is established. A SQLite vector extension would retain the shared database, but introduces binary packaging and platform compatibility work. Standard SQLite with exact scans keeps the first implementation dependency-free and makes failed-update rollback easy to test.

## Validation

Python 3.12.0 and its bundled SQLite 3.42.0 passed the indexing, citation, persistence, rollback, rebuild, and vector compatibility cases on Windows. A wheel built offline using setuptools 70.2.0 and wheel 0.45.1.

The synthetic pilot on 13 October 2026 used 200 Markdown documents, 200 chunks, and 32-dimensional deterministic test vectors:

| Operation | Observed time |
|---|---|
| Initial indexing | 1.5319 seconds |
| Reopened unchanged indexing | 0.3584 seconds |
| Exact vector scan returning five results | 0.0028 seconds |

The closed database occupied 212,992 bytes. These single-run measurements establish that the small pilot is usable. They do not predict real model inference time, semantic search quality, or performance with a large corpus and higher-dimensional vectors.

## Limits and revisit conditions

Exact vector scanning costs O(chunks × dimensions) per query. Retired and failed generations are retained, so repeated rebuilds grow disk usage. Vector acceleration and explicit generation cleanup need separate behavior tests and measurements before daily use with a large library.

Keep callers dependent on the storage component's operations rather than SQLite queries. Moving vectors to another backend must retain document revision visibility, failed-update preservation, embedding-profile compatibility, and citation identity.
