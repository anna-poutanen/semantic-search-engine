# Independently audited multi-file retrieval comparison

Role: **development**. 15 unique queries; 135 warm requests across three conditions and three repetitions. Quality uses only repetition one.

| Condition | Complete positives | Complete multi-file | Complete single-file | Groups | Warm median | Warm p95 |
|---|---:|---:|---:|---:|---:|---:|
| dense | 8/12 | 0/4 | 8/8 | 13/19 | 0.3400s | 0.5875s |
| multifile | 9/12 | 1/4 | 8/8 | 14/19 | 0.3577s | 0.6017s |
| hybrid | 7/12 | 0/4 | 7/8 | 11/19 | 0.3605s | 0.4821s |

Frozen gate: **passed**. Net complete multi-file gain +1; warm median overhead +5.2%. Previously complete single-file losses: [].

Complete gains: ['zod_metadata_attachment_registry'].

Complete losses: [].

All group regressions, including partial cases: [].

Every saved retriever-output candidate and delivered excerpt was checked against the pinned source and extraction inventory; group coverage, stage losses, exact compact tokens, request schedule and summary metrics were independently recomputed.

- dense: missing-group stages {'upstream': 1, 'deduplication': 1, 'result_cap': 4}; repeat delivered-rank changes 0; absent queries with returned candidates 3.
- multifile: missing-group stages {'upstream': 2, 'result_cap': 3}; repeat delivered-rank changes 0; absent queries with returned candidates 3.
- hybrid: missing-group stages {'result_cap': 5, 'upstream': 1, 'deduplication': 1, 'dependency_displacement': 1}; repeat delivered-rank changes 0; absent queries with returned candidates 3.

- No producer, ranking, selection, reference-graph, backend or native model helper is imported by this auditor.
- Benchmark implementation identity refers to exact planned bytes verified at the frozen Git commit. Matching live bytes may be read; changed or missing live files use hash-verified committed blobs for identity checks only, never execution. This does not claim a later working tree produced the saved model results.
- Source-window extraction is reconstructed with the current source-only extraction helpers and must exactly match the frozen inventory SHA; current helper hashes are recorded separately.
- For multifile, the saved retrieved/ranked stages are the post-merge retriever output, not the union of dense and clause-FTS component lists. Its upstream losses mean absent from the merged candidate pool; component recall is not measured by these receipts.
- Saved window/source/score provenance, stage transitions and source coverage are checked; the auditor does not independently rerun ranking or rebuild static graph edges.
- Quality denominators count each labeled query once using repetition one; repeats supply stability and warm timing samples only.
- Backend warm timings include source checks, one query embedding, packing and lightweight selector capture; exclude transport, cold setup, post-request diagnostic replay and serialization.
- Small source-authored sample, one Mac; no external adoption, coding correctness, billing or speedup claim.
- Absent queries report returned candidates, not a proof of feature absence or false-positive precision.
