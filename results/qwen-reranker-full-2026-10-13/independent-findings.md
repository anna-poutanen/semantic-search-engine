# Full Qwen reranker independent audit

All 1,728 planned condition attempts completed across 144 unique queries in eight pinned repositories. The resident scorer performed 43,200 fresh benchmark pair forwards plus one declared synthetic warmup. The embedding model performed 1,728 query calls plus eight separately excluded repository warmups. The audit checks every literal source slice, original full evidence span, actual tokenizer/template input, yes/no scalar score, forward receipt, unchanged packer/dependency expansion and exact compact token cap. Both root-owned process groups stopped, the listener closed and the private authentication token was removed.

Quality below uses the 144 first-pass queries once: 118 present behaviors and 26 scoped absent features. The 136 previously observed queries are development evidence; Koa contributes eight fresh retrieval queries (six present and two absent). Warm distributions use all three actual passes, with 432 measured requests per condition. Those intervals cover the in-process pipeline and helper HTTP hop, including instrumentation; they are not latency for the full production HTTP endpoint.

| Condition | Complete / 118 | Multi-file / 21 | Groups / 148 | Development / 112 | Fresh Koa / 6 | Warm median / P95 (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| dense | 107 | 11 | 134 | 101 | 6 | 0.283 / 0.675 |
| dense_qwen | 110 | 13 | 139 | 104 | 6 | 7.345 / 16.592 |
| hybrid | 103 | 13 | 130 | 98 | 5 | 0.290 / 0.856 |
| hybrid_qwen | 111 | 14 | 141 | 105 | 6 | 9.461 / 17.049 |

| Repository | Present | Dense | Dense + Qwen | Hybrid | Hybrid + Qwen |
| --- | ---: | ---: | ---: | ---: | ---: |
| flask | 12 | 12 | 12 | 11 | 12 |
| requests | 16 | 16 | 16 | 16 | 16 |
| click | 20 | 17 | 19 | 18 | 19 |
| starlette | 20 | 20 | 19 | 17 | 19 |
| rich | 20 | 18 | 18 | 18 | 19 |
| express | 12 | 10 | 12 | 11 | 12 |
| zod | 12 | 8 | 8 | 7 | 8 |
| koa | 6 | 6 | 6 | 5 | 6 |

Each repository cell counts queries whose entire originally declared evidence interval is delivered. OR alternatives are allowed within a required group; every separate group remains required. Python's original exact-symbol metric is independently verified as a separate compatibility diagnostic. The primary source-span metric can credit enclosing windows, so these totals cannot be substituted for historical symbol-dependent percentages.

For `dense`, Qwen produces 4 complete-evidence gains and 1 regressions among matched first-pass positive cases. There are 0 unmatched candidate-pool comparisons. Every gain, regression and group/rank change is retained by query ID in `independent-audit.json`.

For `hybrid`, Qwen produces 10 complete-evidence gains and 2 regressions among matched first-pass positive cases. There are 0 unmatched candidate-pool comparisons. Every gain, regression and group/rank change is retained by query ID in `independent-audit.json`.

Absent queries are retained outside positive accuracy denominators: `dense` {'candidates': 26}; `dense_qwen` {'candidates': 26}; `hybrid` {'candidates': 26}; `hybrid_qwen` {'candidates': 26}. The experiment fits no abstention threshold; plausible returned code does not establish that an absent feature exists.

The auditor verified 13,479 delivered source excerpts, 2,785 unique literal source contents and 288 stored candidate pools. There were 0 within-pair and 0 repeated candidate identity/source mismatches; 0 scored source prefixes were clipped. Ordered candidate identity, retrieval-score, vector-byte, reranker-score and compact-payload drift are reported independently. Static dependencies can deliver evidence outside the raw candidate pool. Exact full-row and compact-payload reconstruction verifies that expansion and clipping obey the unchanged packer.

`independent-audit.json` contains per-repository and language quality, all warm component distributions and sample counts, compact token usage, setup/cache reuse and actual model counters, allocator memory, all paired changes, stability receipts and artifact hashes. Model verification/load and indexing/setup are excluded from warm samples. MLX memory receipts are allocator observations, not operating-system RSS.

The original BF16 weights are bound by root's retained full-byte verification and unchanged file/stat witnesses. This CPU audit rehashes metadata, tokenizers, source and runtime identities without loading weights or repeating inference. Numerical parity with PyTorch, calibrated absence confidence, coding correctness and paid-agent token usage remain untested here. Production defaults remain unchanged.
