# Smaller Qwen shortlist development replay

Exploratory post-output observed-development quality replay; no new inference or latency

All 144 first-pass queries from eight now-observed repositories are retained. Original no-reranker conditions remain separate. Only original first-K candidates are sorted by the earlier observed scalar scores. Source extraction, original labels, five anchors, static callee expansion, eight excerpts, 12,000 source characters and the exact 4,096-token compact budget remain unchanged.

| Retrieval | Prefix | Complete / 118 | Groups / 148 | Multi-file / 21 | Gains / losses vs baseline | Gains / losses vs Qwen50 |
|---|---:|---:|---:|---:|---:|---:|
| dense | no reranker | 107 | 134 | 11 | — | — |
| dense | 5 | 105 | 132 | 10 | 0 / 2 | 1 / 6 |
| dense | 8 | 107 | 135 | 11 | 0 / 0 | 1 / 4 |
| dense | 10 | 107 | 135 | 11 | 0 / 0 | 1 / 4 |
| dense | 16 | 108 | 136 | 12 | 2 / 1 | 0 / 2 |
| dense | 24 | 109 | 137 | 13 | 3 / 1 | 0 / 1 |
| dense | 32 | 110 | 140 | 13 | 4 / 1 | 0 / 0 |
| dense | 40 | 110 | 140 | 13 | 4 / 1 | 0 / 0 |
| dense | 50 | 110 | 139 | 13 | 4 / 1 | 0 / 0 |
| hybrid | no reranker | 103 | 130 | 13 | — | — |
| hybrid | 5 | 100 | 127 | 11 | 0 / 3 | 2 / 13 |
| hybrid | 8 | 105 | 133 | 13 | 3 / 1 | 2 / 8 |
| hybrid | 10 | 107 | 135 | 14 | 5 / 1 | 2 / 6 |
| hybrid | 16 | 109 | 136 | 15 | 7 / 1 | 1 / 3 |
| hybrid | 24 | 112 | 140 | 15 | 10 / 1 | 1 / 0 |
| hybrid | 32 | 112 | 140 | 15 | 10 / 1 | 1 / 0 |
| hybrid | 40 | 111 | 139 | 14 | 10 / 2 | 0 / 0 |
| hybrid | 50 | 111 | 141 | 14 | 10 / 2 | 0 / 0 |

Every original baseline and Qwen50 result reproduces the actual earlier compact text, source order and full delivered rows exactly (288 baseline and 288 Qwen50 checks). Every smaller-prefix result is independently checked against literal pinned source, physical spans, source hashes, score provenance, dependency anchors and exact token/character/excerpt caps.

`replay.json` retains all per-repository metrics and query-level gains, regressions, group and rank changes. `cases.jsonl.gz` retains each unique query/prefix's evidence ranks, partial gold-line diagnostics, delivered bounds/hashes and exact compact digest; literal sources remain in the unchanged original study.

Retained pair token and physical-source-length distributions are measured inputs from the earlier scores. They describe workload size and source retention; they do not measure shorter-shortlist or batch latency.

- Scores and source prefixes were observed in the earlier sequential 50-pair study; this replay is post-output development selection, not a new model or retrieval run.
- No replay execution time, shortened sum of old pair times, or token count is reported as warm latency; actual warm experiments must measure the selected shortlist and batching independently.
- Koa was fresh in the earlier study but is observed development now; a new repository must validate the final selected setup without tuning.
- Complete evidence needs a single delivered excerpt covering each original declared full span; partial union gold-line coverage is a separate diagnostic.
- Gold-line weighting picks the best covered OR alternative, ties by label order, and aggregates physical line counts per logical group; overlapping logical groups can count a line twice.
- Candidate coverage is not a delivered-coverage ceiling because unchanged static callee expansion may introduce sources outside the prefix.
- No threshold/absence calibration, numerical batched parity, coding correctness, or provider token savings is established.
