# Qwen retrieval and H2O shortlist experiment

Frozen Flask development pilot: 12 implementation queries and 2 absent features. Same evidence-qualified source windows and H2O rubric as the previous experiment.

Qwen3-Embedding-0.6B uses original BF16 weights, 1024 dimensions, a fixed behavior-search query instruction, last-token pooling, and local MLX inference.

One-time indexing of 358 windows: 24.52s.

## Retrieval before H2O

| Retrieval | Correct first result | Correct within first 5 | Candidate coverage K3 | K4 |
|---|---:|---:|---:|---:|
| bm25 | 8/12 | 11/12 | 10/12 | 11/12 |
| dense | 10/12 | 12/12 | 11/12 | 12/12 |
| hybrid | 10/12 | 11/12 | 11/12 | 11/12 |

Hybrid uses equal-weight reciprocal-rank fusion, rank constant 60, each input depth 50. This fixed fusion can demote a strong semantic-only match: the dispatch implementation is dense rank 2 but hybrid rank 24 because BM25 omits it. No fusion weights were tuned on these labels.

## Quality sweep (replayed scores)

| Retrieval | H2O K | Candidate coverage | Returned coverage | Correct first result |
|---|---:|---:|---:|---:|
| hybrid | 1 | 10/12 | 10/12 | 10/12 |
| hybrid | 2 | 10/12 | 10/12 | 10/12 |
| hybrid | 3 | 11/12 | 11/12 | 11/12 |
| hybrid | 4 | 11/12 | 11/12 | 11/12 |
| hybrid | 5 | 11/12 | 11/12 | 11/12 |
| hybrid | 8 | 11/12 | 11/12 | 11/12 |
| hybrid | 10 | 11/12 | 11/12 | 11/12 |
| dense | 1 | 10/12 | 10/12 | 10/12 |
| dense | 2 | 11/12 | 11/12 | 11/12 |
| dense | 3 | 11/12 | 11/12 | 11/12 |
| dense | 4 | 12/12 | 12/12 | 12/12 |
| dense | 5 | 12/12 | 12/12 | 12/12 |
| dense | 8 | 12/12 | 12/12 | 12/12 |
| dense | 10 | 12/12 | 12/12 | 12/12 |

Quality reuses prior judgments only when source, model revision, serving configuration, dataset hash and rubric match; missing pairs receive new local inference. This is not a latency measurement. Every reuse is marked with its originating run in `quality_judgments.jsonl`.

## Fresh warm latency

Each query is re-embedded and every shortlisted window is freshly scored. Both models are warmed; condition order rotates by query. Document indexing, model startup, and audit serialization are excluded.

| Retrieval | H2O K | Correct first result | Correct in returned results | Median | P95 / max | Samples |
|---|---:|---:|---:|---:|---:|---:|
| dense | 0 | 10/12 | 12/12 | 0.040s | 0.180s / 0.180s | 14 |
| hybrid | 0 | 10/12 | 11/12 | 0.029s | 0.037s / 0.037s | 14 |
| dense | 1 | 10/12 | 10/12 | 1.738s | 4.358s / 4.358s | 14 |
| hybrid | 1 | 10/12 | 10/12 | 1.739s | 4.474s / 4.474s | 14 |
| hybrid | 3 | 11/12 | 11/12 | 5.056s | 8.846s / 8.846s | 14 |
| hybrid | 2 | 10/12 | 10/12 | 3.338s | 6.642s / 6.642s | 14 |
| dense | 4 | 12/12 | 12/12 | 5.947s | 9.697s / 9.697s | 14 |
| dense | 3 | 11/12 | 11/12 | 4.762s | 8.488s / 8.488s | 14 |

K counts scored source windows, not symbols. Only the scored prefix is returned after symbol deduplication; K0 retrieves ten windows and returns the first five symbols without H2O. With 14 samples, nearest-rank P95 equals the slowest sample. These are warm resident-worker timings, not total CLI invocation times.

## Selected shortlists and absent queries

Selection chooses the smallest K with the best candidate coverage in the declared grid and first-result accuracy at least as high as K10. It is applied separately to both methods.

- hybrid: selected K3.
- dense: selected K4.

| Absent feature | Method | Selected K | Highest H2O score / 3 |
|---|---|---:|---:|
| absent_builtin_csrf_validation | hybrid | 3 | 0.273 |
| absent_builtin_rate_limiting | hybrid | 3 | 0.308 |
| absent_builtin_csrf_validation | dense | 4 | 0.548 |
| absent_builtin_rate_limiting | dense | 4 | 0.308 |

The search still returns candidates for absent features; no rejection threshold was fitted.

## Memory

- embedding_worker: current 15.4G, peak 15.5G (`vmmap -summary`, includes Metal).
- h2o_facade: current 6.2G, peak 7.4G (`vmmap -summary`, includes Metal).
- h2o_shim: current 17.6M, peak 17.7M (`vmmap -summary`, includes Metal).

Includes Metal. Individual rounded peaks since process launch are not a simultaneous combined peak.

## Limits

- Same 12 developer-authored Flask queries; no held-out claim.
- No tuning of query text, labels, H2O rubric, or RRF weights.
- Quality replay may reuse exact judgments; latency runs always perform new inference.
- No unscored candidates are appended to the H2O result list.
- Absent queries are inspected separately; no rejection threshold fitted.
- Warm latency excludes one-time document indexing/model startup.
- No paid-agent token savings measured.

Original comparison: BM25+H2O K10 ranked 11/12 first, 19.29s warm median / 23.90s P95. This run measures new retrieval and smaller prefixes live.

## Repeat after document-buffer cleanup

The initial sweep above retained unused Metal allocator buffers from document indexing. The runtime now clears that cache between document batches and after document encoding, while keeping model weights resident. A fresh rebuild produced matching document vectors (absolute and relative tolerance 1e-6), followed by a separate 14-query sweep per condition. All query embeddings and 56 H2O judgments were recomputed. Original measurements remain unchanged.

| Dense mode | Correct first result | Correct in results | Warm median | P95 / max |
|---|---:|---:|---:|---:|
| No H2O, first five symbols | 10/12 | 12/12 | 0.032s | 0.044s |
| H2O, four windows | 12/12 | 12/12 | 5.835s | 9.445s |

Rebuilding all 358 document vectors took 23.77s. The embedding worker's physical footprint was 1.7G current / 2.0G peak since launch, including the index rebuild; the original was 15.4G / 15.5G. H2O was 6.3G current / 7.4G peak. The peak values belong to individual processes, not a simultaneous combined measurement.

See `memory-cleanup-repeat/latency.json` and its raw judgments. Independent checks in `audit.json` reconstruct scores, token counts, prefixes, evidence-qualified ranks and timings from frozen source. The original fresh judgments had zero score or probability drift from the quality replay.
