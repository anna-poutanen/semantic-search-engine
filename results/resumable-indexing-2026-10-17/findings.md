# Resumable indexing verification

Completed native Qwen embedding batches now survive an interrupted refresh.
Repeating the same indexing command reuses compatible document/model vectors;
the active source, keyword and dense index still publish together only after
complete validation. This is an unreleased source-checkout feature after
v0.1.3, with no schema or embedding-fingerprint change.

## Real-model process termination

The [verification receipt](verification.json) records four serial, fresh Python
workers using the existing pinned Qwen3-Embedding-0.6B checkpoint on an Apple M4
Mac with 24 GiB memory and macOS 27.0.1. Hugging Face offline settings were
enabled; no new model download was needed. The qualification driver at execution
was commit `9dbe0e0`; a subsequent path-validation guard prevents nesting private
work files inside the public receipt directory.

The fixture samples twelve evenly spaced original source windows, across ten
files, from Flask commit `d086db856be187255b8ec61ef409357393020f32`.
Production extraction finds 358 windows using 160-line windows and 40-line
overlap. The receipt includes saved-file and document hashes, exact paths,
symbols, line spans and actual native batch membership. All selected text was
verified against saved physical source; source bytes and Git state stayed
unchanged throughout the run. Flask's [license](../../benchmarks/FLASK_LICENSE.txt)
applies to the upstream source used in this check.

| Stage | Reused documents | New documents embedded | Active windows after stage |
| --- | ---: | ---: | ---: |
| Seed a previous complete index | 0 | 1 | 1 |
| Refresh twelve windows; SIGKILL after the first durable batch | 1 | 4 | 1 |
| Restart in a fresh process and complete | 5 | 7 | 12 |
| Independent uninterrupted baseline after the same one-window seed | 1 | 11 | 12 |

The parent read SQLite through an independent connection while the worker was
paused, then sent SIGKILL. Cache entries for all four completed documents were
already visible. Hashes of all four active tables (`snippets`, `snippets_fts`,
`active_vectors`, `metadata`) matched the seeded snapshot both before and after
termination. The fresh worker's actual model requests excluded every previously
saved document and covered all seven remaining documents exactly once.

The interrupted and resumed native batch schedule matched the uninterrupted
baseline. Both the returned 12-by-1,024 float64 matrix and persisted active
float32 matrix were **bitwise identical** to the baseline, with maximum absolute
difference zero. Raw arrays and databases remain in ignored local work storage;
the committed receipts contain their hashes and source-free worker logs.

## Automated coverage

The complete local Python suite passed: **2,564 passed, 10 skipped** in 83.52
seconds. The feature adds **55 tests** across callback behavior, adversarial
store inputs and backend integration. Coverage includes cancellation, native
failure, source edits, model isolation, progress disabled, partial or mismatched
callbacks, mutable callback buffers, concurrent cache writers, dimension
conflicts, rollback after cancellation/source edits during final SQL writes and
fresh-process SIGKILL recovery. Independent review found no blocking issues.

Run the suite from a source checkout with its test dependencies and verified
tokenizer cache available:

```sh
PYTHONPATH="$PWD/src" TIKTOKEN_CACHE_DIR="$PWD/.cache/tokenizers" \
  .venv/bin/python -m pytest -q
```

Reproduce the native check using a local Flask checkout at the recorded commit,
the pinned local model and new or empty, non-overlapping work/output directories:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python scripts/verify_resumable_indexing.py \
  --repo /absolute/path/to/flask --source-root src/flask \
  --model-path /absolute/path/to/qwen3-embedding-0.6b \
  --work-dir "$PWD/.cache/resume-proof" \
  --output-dir "$PWD/results/local/resume-proof"
```

Each worker has a bounded timeout; the parent terminates owned workers on
failure. The private work directory contains source fixtures, SQLite databases
and arrays; it must remain separate from public receipts.

## Scope

This establishes completed-batch durability and unchanged vectors for this
fixture on one Mac. It does not measure retrieval quality, end-to-end coding
benefit, large-repository throughput, independent-machine setup, power-loss
recovery or every possible termination point. Descriptive worker/forward times
in the raw receipts are not a latency comparison. The twelve sampled windows
are not a rerun of the full retrieval benchmark.

An interrupted in-flight batch can run again. Resume requires the same cache
location and matching rendered documents/model settings; changed implementations
need new inference. Source validation still rejects stale results even when a
previous complete snapshot exists. See [the resume contract](../../docs/resumable-indexing.md).
