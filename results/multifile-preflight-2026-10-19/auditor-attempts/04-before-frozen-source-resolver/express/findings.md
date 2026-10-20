# Independently audited multi-file retrieval comparison

Role: **development**. 15 unique queries; 135 warm requests across three conditions and three repetitions. Quality uses only repetition one.

| Condition | Complete positives | Complete multi-file | Complete single-file | Groups | Warm median | Warm p95 |
|---|---:|---:|---:|---:|---:|---:|
| dense | 10/12 | 2/4 | 8/8 | 17/19 | 0.1799s | 0.2214s |
| multifile | 10/12 | 2/4 | 8/8 | 17/19 | 0.1917s | 0.2444s |
| hybrid | 11/12 | 3/4 | 8/8 | 18/19 | 0.1859s | 0.2132s |

Frozen gate: **failed**. Net complete multi-file gain +0; warm median overhead +6.5%. Previously complete single-file losses: [].

Complete gains: [].

Complete losses: [].

All group regressions, including partial cases: [].

Every saved retriever-output candidate and delivered excerpt was checked against the pinned source and extraction inventory; group coverage, stage losses, exact compact tokens, request schedule and summary metrics were independently recomputed.

- dense: missing-group stages {'result_cap': 2}; repeat delivered-rank changes 0; absent queries with returned candidates 3.
- multifile: missing-group stages {'result_cap': 2}; repeat delivered-rank changes 0; absent queries with returned candidates 3.
- hybrid: missing-group stages {'result_cap': 1}; repeat delivered-rank changes 0; absent queries with returned candidates 3.

- No producer, ranking, selection, reference-graph, backend or native model helper is imported by this auditor.
- For multifile, the saved retrieved/ranked stages are the post-merge retriever output, not the union of dense and clause-FTS component lists. Its upstream losses mean absent from the merged candidate pool; component recall is not measured by these receipts.
- Saved window/source/score provenance, stage transitions and source coverage are checked; the auditor does not independently rerun ranking or rebuild static graph edges.
- Quality denominators count each labeled query once using repetition one; repeats supply stability and warm timing samples only.
- Backend warm timings include source checks, one query embedding, packing and lightweight selector capture; exclude transport, cold setup, post-request diagnostic replay and serialization.
- Small source-authored sample, one Mac; no external adoption, coding correctness, billing or speedup claim.
- Absent queries report returned candidates, not a proof of feature absence or false-positive precision.
