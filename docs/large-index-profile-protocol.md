# Prospective Django persistent-index profile

This profile measures durable indexing and exact-search behavior on a larger real codebase. The fixed Django 5.2 source produces **9,097 implementation windows from 883 production Python files**, about 5.05 times the previous largest real corpus of 1,802 windows. The preparation receipt is `results/django-index-preflight-2026-10-14/source-preflight.json`; the unchanged queries, spans, settings, and expected mutation counts are in `benchmarks/django_index_profile.json`.

Preparation performs source extraction only. No Django code, model, embedding, database, search, or measured timing has run. Commit this protocol, manifest, source inventories, preflight authoring scripts, and their hashes before executing the profile. A new runner and its independent checker must also be reviewed and committed before collecting outcomes. Do not run native work while the eighteen coding trials remain active.

## Fixed source and membership

Use the official [Django repository](https://github.com/django/django) at annotated tag `5.2`, tag object `6b995cff697d35d13ab876f2d0b19bc6fc835d8e`, peeled commit `9e7cc2b628fe8fd3895986af9b7fc9525034c1b0`, and the tree recorded in the preflight receipt. Django [announced the release on April 2, 2025](https://www.djangoproject.com/weblog/2025/apr/02/django-52-released/). This is a historical source fixture; it is not a claim that 5.2 is the current patched release.

The original isolated checkout is `.cache/corpora/django-5.2-index-profile`. Keep it clean throughout. Create a separate, fresh local Git clone for execution, retaining tracked membership and the pinned commit. Never modify the original source fixture. Record the execution clone path, `HEAD`, tracked membership, and every saved file hash at each stage.

Index only tracked `.py` files under `django/`. Explicit manifest globs exclude the supported JavaScript/TypeScript extensions; other extensions are ineligible. Tests, docs, and scripts outside that root are excluded by membership. Python runtime helpers inside `django/test/`, migrations, GIS implementations, and other eligible production package files remain included. Decode using Python encoding cookies and parse using the existing Python AST extractor. Use 160 physical lines per window, 40 overlapping lines, and `saved-implementations-v3`. The extractor omits overloads, placeholder-only definitions, and windows without body statements. It preserves decorators, signatures, original text, and inclusive one-based locations for eligible implementations.

There are 157,865 production Python physical lines and 5,644,244 saved source bytes. The 9,097 windows contain 4,860,123 source bytes including overlap and have 9,097 unique rendered document hashes. The inventories enumerate every file and window, including Python files yielding no implementation window. There are no parse failures or silently excluded Python files. Fail before measurement if any inventory, extractor source, model metadata, or expected count differs.

## Genuine first indexing and native evidence

Use the existing original BF16 `Qwen/Qwen3-Embedding-0.6B` checkpoint at revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 1,024 dimensions, document batch size four, and the production formatting, tokenization, pooling, normalization, and length-sorted batching. A verified cached checkpoint is allowed and must be labeled as such. Neither document vectors nor a prior index may be imported. Choose a fresh execution directory and database, refuse existing result/index paths, assert an empty embeddings table, and require all 9,097 unique documents to be genuinely embedded in the initial build.

Use a recording embedder/index wrapper in the new runner; do not edit production implementations. Record each real document/query input hash, actual tokenizer length and truncation, batch order, and native batch start/completion counts. Capture the original float32 document output matrix before SQLite conversion in the ignored execution directory and publish its shape, dtype, and SHA-256. Bind checkpoint files, their verified hashes, embedding provenance, producer sources, Python/native package closure, and executed runner/checker sources to the run.

Report schema creation, complete `SearchBackend.refresh()` indexing, and the document-embedding subphase separately. Initial indexing includes first model loading and inference; record initialization time as a nested subphase where measurable. Do not subtract overlapping nested phases or call this a cold checkpoint download. All benchmark queries occur after first indexing, so they cannot precompute first-build document vectors. Require finite nonzero vectors with the expected shape and exact source/document bindings before accepting the initial index.

Capture process RSS baseline and peak with the correct macOS byte units, and MLX active/peak/cache memory separately. Record database, WAL, and SHM sizes at declared live and closed checkpoints. Distinguish retained cached vectors from active windows. Report resident model memory, index disk usage, and corpus/checkpoint sizes separately; do not present a cumulative `ru_maxrss` value as a per-stage allocation. Record wall time, monotonic time, power source, and sleep/clock discontinuity checks. Preserve an interrupted or invalid attempt instead of silently resuming its timings.

## Reopen and saved-source transitions

Run the exact sequence in the manifest against the execution copy. Watchers are disabled: these are explicit saved-source refreshes, so watcher debounce is outside the timings. Validate the independently extracted active ID/source set, vector reuse, embedding input hashes, table counts, and source proof after every transition.

| Stage | Active windows | New embeddings | Reused windows | Deleted active IDs | Cached documents after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Initial index | 9,097 | 9,097 | 0 | 0 | 9,097 |
| Fresh-process unchanged reopen | 9,097 | 0 | 9,097 | 0 | 9,097 |
| One saved line edit | 9,097 | 1 | 9,096 | 0 | 9,098 |
| Delete the edited module | 9,084 | 0 | 9,084 | 13 | 9,098 |
| Restore the edited module | 9,097 | 0 | 9,097 | 0 | 9,098 |
| Rename the edited module | 9,097 | 13 | 9,084 | 13 | 9,111 |
| Rename it back | 9,097 | 0 | 9,097 | 13 | 9,111 |
| Restore the pristine module | 9,097 | 0 | 9,097 | 0 | 9,111 |
| Final fresh-process reopen | 9,097 | 0 | 9,097 | 0 | 9,111 |

The saved edit appends the frozen comment to the one `return pickle.loads(pickled)` line in `LocMemCache.get`, preserving line count and behavior. Its new rendered document hash must be genuinely embedded once; the other 9,096 documents must remain byte-identical. Deletion removes all thirteen active windows of `django/core/cache/backends/locmem.py` while retaining their cached vectors. Restore the frozen edited bytes. Use tracked `git mv` to rename it to `django/core/cache/backends/locmem_profile_copy.py`, then back. The document format includes its path, so all thirteen renamed documents require new embeddings. Renaming back and restoring pristine bytes reuse historical vectors. Do not describe a rename as zero inference.

Both reopen checks run in a fresh CPU process using the exact frozen embedding metadata and an embedder whose document and query methods raise if called. Require zero model imports/native work, zero inference calls, all document vectors reused, exact cached float32 values, and source/FTS/dense bindings unchanged. Record fresh-process reopen/setup and refresh times separately. A second object in the existing native process may be recorded as a separate resident-process observation but cannot substitute for the fresh-process check. No old deleted or renamed ID may remain in the active source, FTS, or dense tables.

## Search timings and independent numerical checks

Measure two declared workloads, keeping their samples separate:

1. **Store searches with fixed real query vectors:** native-embed each of the eight unchanged query texts once outside these timings. Measure `SQLiteIndex.search_dense`, `search_lexical`, and the existing hybrid RRF combination. Raw top-k is ten; hybrid fetch depth is fifty from each retriever and RRF constant is sixty. Each operation includes its normal SQLite connection and extension loading where applicable. These timings exclude model inference, corpus hashing, independent verification, and context packing.
2. **Resident end-to-end searches:** use `SearchBackend.search` with `bm25`, `dense`, or `hybrid`; no reranking, dependencies, or overlays; fifty candidates, five results, 12,000 source characters, and a 2,000-token payload cap. Retain actual query embedding, saved-source hashing, retrieval, source verification, and whole-line context packing inside the timer. BM25 must produce no query embeddings; dense and hybrid must produce actual query embeddings. First-use observations are recorded separately.

For each workload/provider, record the first use, three complete untimed eight-query warmup sweeps, then ten measured eight-query sweeps: eighty measured samples per provider. Use the manifest query order rotated by sweep number and rotate provider order as well; freeze the exact schedule in the runner plan. Report every raw sample, count, median, P95 using NumPy's linear percentile method, minimum, and maximum. Do not blend providers, store-only and end-to-end measurements, first-use and warm samples, or an interrupted attempt.

Independently derive normalized float32 stored document/query vectors from the captured original matrix and real query vectors without calling production normalization/ranking helpers. Accumulate cosine scores in float64 with NumPy and break exact score ties by snippet ID. Check all eight fresh-index dense top-ten lists against SQLite, require identical ID order and maximum absolute score error at most `3e-6`, and retain actual matrices/query/vector hashes. Inspect active vector blobs and cached document bindings against the expected inputs. Verification and NumPy matrix scans are outside query timings. Preserve a rank disagreement even if it falls within a numerical near-tie band; report the band as a diagnostic, not an automatic pass. This is an exact cosine scan across 9,097 active vectors, **not an approximate nearest-neighbor index**.

## Source-authored retrieval examples and reporting

The eight unchanged texts are four literal/behavioral pairs for cache reads, page-number validation, timestamp signature age, and transaction callback registration. Labels were authored from pinned source before any Django ranking results. Each target is a single complete local implementation span; external callees are outside the claimed proof. Record candidate top-ten coverage and delivered five-result coverage independently for BM25, dense, and hybrid, with full verified source bytes and location hashes. A target name or partial body is insufficient. Record payload token count, source characters, truncation, actual defaults, and source scope for every delivered response. The quality observations are outside timing samples.

Retain all eight examples and every declared stage, including failures. Do not tune queries, labels, thresholds, extraction, or retrieval settings after viewing results. Any necessary implementation repair or protocol amendment starts a separately identified attempt with the prior failure preserved. A complete report includes inventory, first-build/reopen/change counters, model and vector provenance, disk/memory observations, raw latency distributions, numerical parity, source-example results, and cleanup proofs. Independently audit the final artifacts and runtime bindings before committing outcome claims.

These eight selected source-authored examples are a smoke check, not a general retrieval benchmark, a pretraining holdout, an abstention test, or evidence about coding-agent correctness/token savings. A single machine/repository profile does not establish production concurrency or sublinear/ANN scaling. No Django service, database backend, external network workflow, or application test suite is executed by this profile.
