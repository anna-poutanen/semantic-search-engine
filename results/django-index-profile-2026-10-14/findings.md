# Real Django persistent-index profile

The independently audited run indexes **9,097 implementation windows** from
883 tracked Django 5.2 Python files at commit
`9e7cc2b628fe8fd3895986af9b7fc9525034c1b0`. A fresh database genuinely embeds every
initial document with the existing original BF16 Qwen embedding model. Initial
backend refresh takes **395.93 seconds**. All nine planned mutation/reopen stages
pass with exact source, cached-vector, FTS and active-vector checks.

| Operation | Newly embedded windows | Reused active windows | Timed operation seconds |
|---|---:|---:|---:|
| Initial refresh | 9,097 | 0 | 395.93 |
| Fresh CPU process reopen | 0 | 9,097 | 3.89 |
| Saved one-window edit | 1 | 9,096 | 6.08 |
| Delete 13-window file | 0 | 9,084 | 6.01 |
| Restore edited file | 0 | 9,097 | 6.03 |
| Tracked rename | 13 | 9,084 | 6.49 |
| Rename back | 0 | 9,097 | 6.18 |
| Restore pristine file | 0 | 9,097 | 6.05 |
| Final fresh CPU process reopen | 0 | 9,097 | 3.83 |

The fresh reopen is the durable SQLite index/source-extraction layer in a new
CPU-only process, including process launch. Its embedder throws on inference.
It is **not resident model or full service startup**, so it must not be directly
substituted for the earlier Zod service-reopen timing.

Each retrieval family has 80 measured warm samples per layer, following separate
first-use and warmup calls: 630 observations total, 480 measured. Store timings
use already captured query vectors; backend timings include fresh query
embedding for dense/hybrid, source consistency checks and five-excerpt packing
under a 2,000-token cap. These are in-process backend calls, excluding HTTP/MCP
transport, reranking, initial indexing and correctness instrumentation outside
the query timer.

| Layer / retrieval | Warm median seconds | Warm P95 seconds |
|---|---:|---:|
| Store BM25 | 0.00791 | 0.01168 |
| Store dense | 0.02184 | 0.02309 |
| Store hybrid | 0.03079 | 0.03429 |
| Backend BM25 | 0.59616 | 0.62566 |
| Backend dense | 0.65797 | 0.71817 |
| Backend hybrid | 0.68360 | 0.71729 |

The ledger reconciles **9,111 document inputs, 234 query inputs and 2,514 actual
embedding forwards**, with no failed or missing forward. Eight independent
float64 NumPy comparisons reproduce exact SQL top-ten ID order; maximum score
error is `9.973158475728994e-7`, below the unchanged `3e-6` limit. Eight small
source-authored examples deliver full definitions for 8/8 dense and 7/8
BM25/hybrid queries. These examples are not a general relevance benchmark or
multi-file holdout.

Cumulative process RSS peaks at 2,581,118,976 bytes (2.40 GiB); MLX reports a
1,912,030,434-byte peak (1.78 GiB). After model release it reports 1,032 active
bytes and zero cached bytes. AC power, no sleep events and the fixed clock guard
pass. The owned worker exits normally with no forced kill or interruption.

The database is **144,465,920 bytes after worker exit** (137.77 MiB), with no
WAL/SHM files. The original worker observation records 286,447,664 bytes before
exit, when its SQLite observation connection still keeps WAL/SHM present. These
are different observation points. The final audit compares every retained page
and all other header bytes with the exact final backup, exposing the SQLite
backup counters separately. Removed sidecar bytes cannot be retrospectively
hashed. Disk sizes exclude checkpoints, execution backups and evidence files.

The original independent audit rejects a redacted-command hash; the first
corrected audit rejects the pre-exit/post-exit disk comparison. Both failures
and immutable source preparations are retained. The
[final corrected audit](independent-audit-disk-scope-correction.json) passes all
native, numerical, mutation, source and lifecycle checks. The
[qualified correction history](../django-index-audit-actual-correction-2026-10-14/v2/qualification.json)
changes only audit command restoration and physical observation scope, without
rerunning the model, changing the numerical gate or modifying the original
executed source. This is a cached-filesystem single-Mac profile of one real
repository, not cold storage, ANN or concurrent throughput testing.
