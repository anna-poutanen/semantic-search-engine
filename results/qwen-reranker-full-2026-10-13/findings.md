# Full Qwen reranker results

Qwen3-Reranker-0.6B improves complete source evidence on this frozen sample,
especially after hybrid retrieval. Scoring 50 candidates sequentially remains
too slow for the default interactive workflow. Keep fast dense retrieval as the
production default and validate a faster reranker configuration before repeating
the coding comparison.

## Quality and warm latency

The study completed all 1,728 searches: 144 unique queries across eight pinned
real repositories, four conditions and three actual repetitions. There are 118
present behaviors, 26 scoped absent features, 148 required evidence groups and
21 multi-file AND behaviors. Quality uses only the first repetition; timing uses
432 actual requests per condition. Every reranked request scored all 50 retrieved
windows, totaling 43,200 fresh benchmark forwards plus one separate warmup.

| Condition | Complete evidence / 118 | Groups / 148 | Multi-file / 21 | Fresh Koa / 6 | Warm median / P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dense | 107 | 134 | 11 | 6 | 0.283 / 0.675 s |
| Dense + Qwen | 110 | 139 | 13 | 6 | 7.345 / 16.592 s |
| Hybrid | 103 | 130 | 13 | 5 | 0.290 / 0.856 s |
| Hybrid + Qwen | 111 | 141 | 14 | 6 | 9.461 / 17.049 s |

Dense gains four complete behaviors and loses one; hybrid gains ten and loses
two. This is a net gain of three and eight behaviors respectively, rather than
uniform improvement. Per-repository counts and every changed query are in the
[independent findings](independent-findings.md) and
[machine-readable audit](independent-audit.json).

These timings measure the resident **in-process pipeline**: fresh query
embedding, candidate retrieval, the scorer's blocking loopback HTTP hop and the
unchanged context packer. They include measurement instrumentation, exclude model
verification/loading and index setup, and do not measure the complete production
HTTP/MCP route. P95 uses nearest rank over all 432 requests, including absent
queries. Conditions rotated across queries and repetitions on one M4/24 GB Mac.

Actual evaluated pair forwards dominate the added latency: their summed per-search
median is 7.142 seconds for dense + Qwen and 9.208 seconds for hybrid + Qwen.
Server tokenization medians are 0.031 and 0.037 seconds, and packing medians are
0.094 and 0.096 seconds. These component medians are separate distributions and
must not be added to reconstruct the overall median. Keeping weights resident
removes startup from each search, but does not remove the cost of 50 sequential
forwards. Both reranked P95 values miss the project's five-second aspiration.

## Fresh codebase and remaining failures

Koa contributes eight source-authored queries: six present behaviors and two
scoped absent features, including three multi-file behaviors. Its pinned source,
queries and labels were reviewed and frozen before this first retrieval run.
The earlier clause-rule applicability failure remains preserved; this reranker
study has its own prospective authorization and freeze.

Dense already delivers all six Koa behaviors. Hybrid initially misses complete
forwarded-client-address evidence; Qwen promotes `get ips` from candidate rank
19 to reranked rank 3 and delivers its full body alongside `get ip`, recovering
the sixth behavior. There is no Koa regression. Six positives are a small fresh
retrieval check, not proof of generalization or exclusion from model pretraining.
Koa is now observed development evidence for any subsequent tuning.

The complete regressions are retained:

- Dense loses Starlette's exception registration/dispatch behavior.
- Hybrid loses Click's standalone usage-error behavior and Rich's centered-panel
  composition behavior.

All groups gained in the changed cases arrive through retrieval-origin windows;
they are not rescued by newly added gold dependencies outside the pool. Static
callee expansion remains enabled and can in general return windows outside the
top 50, so raw candidate completeness is not automatically a delivered-coverage
ceiling. The [source explanations](evidence-explanations.md) and
[manual case review](semantic-review.md) retain group ranks, executable source
roles, omitted support and scored/delivered prefix diagnostics. Zod still delivers only
8 of 12 positive behaviors with either reranked condition; partial supporting
evidence gains do not count as complete multi-file success.

All 26 absent queries return candidates in every condition. Yes/no logits are
ranking signals, and this experiment fits no abstention threshold.

## Integrity, setup and interpretation

The [frozen protocol](../../docs/qwen-reranker-full-protocol.md) and
[frozen plan](frozen-plan.json) bind model/runtime identities, source, labels,
conditions and limits. The original BF16 checkpoint and literal query/source
template were fixed. Paths, ranks and gold labels did not enter the model input.
There was no shortlist selection, prompt tuning, H2O scoring, query caching or
score caching. The original packer retained five anchors, up to two callees per
anchor, eight excerpts, 12,000 source characters and 4,096 exact compact
`cl100k_base` tokens.

The independent CPU audit reconciles all attempts and forward receipts, scalar
scores, actual template tokens, exact source slices, complete original evidence
spans and unchanged packing. It checks 13,479 delivered excerpts and finds no
candidate, vector, score, ranking or compact-payload drift between repeated
conditions. No scored source prefix was clipped. Two initial audit replay
failures concerned metadata field ordering and sorted source exclusions; the
auditor was corrected to match shipped behavior, with regression tests. The
failed attempts and original auditor sources are retained in
[audit-attempts](audit-attempts/). Model inputs, producer code and raw outputs were
not changed, and no model inference was repeated to fix the audit.

The first seven repositories reused existing document vectors through owned
index copies. Fresh Koa embedded 175 document windows in one document API call.
There were 1,736 completed query embedding calls: 1,728 measured searches plus
eight generic repository warmups. Setup is separate from warm timings and is
not a fresh-install benchmark.

The scorer's process-lifetime MLX allocator peak was 1,834,129,428 bytes, with
8 bytes active after cleanup. This is not operating-system RSS or combined
embedding/scorer memory. The whole supervised run took 8,348.70 seconds. Both
owned processes exited successfully; their groups and listener stopped, and the
private authentication token was removed. There were no failed searches,
timeouts or forced kills.

The final local Python suite passes 707 tests with one skipped; the eight new
producer, audit and diagnostic modules/tests pass scoped Ruff checks. Audit-only
metadata normalization changes have regression and extra-key corruption tests.

Primary correctness here means delivery of every originally required executable
source interval, allowing enclosing windows and OR alternatives within each
required group. Python intervals sometimes cover narrower branches, whereas
JS/TS/Koa labels usually require whole bodies. Historical Python exact-symbol
scores remain a separate compatibility diagnostic; these totals cannot replace
historical percentages. The other 136 queries were previously observed
development cases.

This study does not establish numerical PyTorch parity, coding correctness,
actual provider token savings or broad production latency. The next experiment
should freeze a smaller shortlist and/or batching policy, report lost support as
well as gains, and measure actual warm forwards again. Complete gains originated
as deep as dense rank 31 and hybrid rank 24, so shallow shortlists cannot be assumed
to retain them. A coding comparison should
follow a useful quality/latency tradeoff, with strengthened behavioral checks and
complete provider usage receipts.
