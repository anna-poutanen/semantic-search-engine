# Requests: resident search pipeline

Queries and development/test assignments were frozen before seeing retrieval results. Returned hits require the actual delivered excerpt to contain the full executable evidence interval.

| Mode | Split | Present queries | Correct first | Within returned results (≤5) | Median HTTP | P95 |
|---|---|---:|---:|---:|---:|---:|
| bm25:0 | all | 16 | 10/16 | 15/16 | 0.044s | 0.051s |
| bm25:0 | development | 8 | 6/8 | 7/8 | 0.041s | 0.052s |
| bm25:0 | test | 8 | 4/8 | 8/8 | 0.045s | 0.051s |
| dense:0 | all | 16 | 13/16 | 16/16 | 0.089s | 0.118s |
| dense:0 | development | 8 | 7/8 | 8/8 | 0.087s | 0.193s |
| dense:0 | test | 8 | 6/8 | 8/8 | 0.091s | 0.118s |
| hybrid:0 | all | 16 | 12/16 | 16/16 | 0.068s | 0.086s |
| hybrid:0 | development | 8 | 6/8 | 8/8 | 0.071s | 0.098s |
| hybrid:0 | test | 8 | 6/8 | 8/8 | 0.068s | 0.086s |
| dense:4 | all | 16 | 15/16 | 15/16 | 6.362s | 8.685s |
| dense:4 | development | 8 | 8/8 | 8/8 | 5.981s | 11.121s |
| dense:4 | test | 8 | 7/8 | 7/8 | 6.886s | 8.685s |
| hybrid:3 | all | 16 | 16/16 | 16/16 | 4.917s | 7.527s |
| hybrid:3 | development | 8 | 8/8 | 8/8 | 4.543s | 7.844s |
| hybrid:3 | test | 8 | 8/8 | 8/8 | 5.227s | 6.768s |

Initial setup and indexing: 15.38s. Every measured query embedding and H2O score is new; GPU work is serialized. Condition order rotates per query. Source freshness checks, JSON serialization, and loopback round-trip are included. Raw audit disk writes are outside the measured interval.

Latency includes absent queries: 20 samples per configuration overall, 10 per split. Nearest-rank P95 is the 19th of 20 overall and the maximum of 10 within a split. Quality denominators contain only present queries.

H2O checkpoint: `netprojects/h2o-lightning-4b-mlx-q8` at `a1048d02d9e713ba9f7481efa5c3df7ec8a2adaa`. The managed MLX runtime uses 8-bit affine quantization and the FP32 decision head.

## Memory

- search_service: current 1.7G, peak 2.0G (`vmmap -summary`, includes Metal).
- h2o_facade: current 6.3G, peak 7.4G (`vmmap -summary`, includes Metal).
- h2o_shim: current 17.2M, peak 17.3M (`vmmap -summary`, includes Metal).

Includes Metal. Individual rounded peaks since process launch are not a simultaneous combined peak.

## Limits

- 20 authored queries in one additional repository, not representative quality.
- Test split was frozen before retrieval; no K, weights, or prompt tuning in this run.
- Latency includes HTTP JSON response and source freshness checks; excludes setup.
- Character budget is not a token budget; no agent token savings measured.
- Absent queries still return candidates; no rejection threshold fitted.

## Audit and live updates

An independent CPU audit reconstructed all 100 searches and 140 fresh judgments from pinned source, persisted vectors, returned excerpts, and the official shim tokenizer. It verified evidence-qualified ranks, scores, input tokens, bounds, budgets, and all timing summaries. See `audit.json`.

The dense K4 miss is `requests_streamed_upload_length`: its evidence window ranked fifth and was excluded before scoring. No shortlist sizes, weights, or prompts were adjusted after this test. Ten returned excerpts were truncated across nine budget-constrained searches; hit metrics use their actual delivered lines.

The separate `service-smoke.json` records an actual CLI service process: one edited window embedded with two reused, deletion and line shifts without new embeddings, invalid saved Python rejected, bounded excerpts, and durable vector reuse after restart. Both processes handled SIGTERM and exited successfully. This three-function smoke test verifies integration, not retrieval quality. Reproduce with:

```sh
.venv/bin/python scripts/smoke_service.py \
  --work-dir .cache/service-smoke-repeat \
  --output results/local/service-smoke-repeat.json
```
