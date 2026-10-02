# Native Werkzeug context allocation: primary gate fails

The completed, independently audited run delivers complete evidence for **7/12
positive behaviors and 4/8 multi-file behaviors** with either eight or twelve
excerpts. Twelve retains every baseline success and raises supporting-group
coverage from **27/34 to 28/34**, but does not recover a complete behavior. The
prospectively selected primary improvement gate fails in all three passes.
The product stays at eight excerpts, with dense retrieval and reranking off.

Twenty excerpts recovers the entity-tag behavior: **8/12 complete, 5/8 multi-file,
29/34 groups**. It was diagnostic, cannot rescue the failed primary gate, and is
not selected as a new default after observing these results.

## Executed protocol

Werkzeug 3.1.3 commit `6389612fd1ee1bd93579eed5026e8fd471d04abd` contributes
52 production Python files and 1,025 independently reconstructed windows.
Fifteen source-authored queries comprise twelve positives, eight genuine
multi-file AND behaviors and three scoped absent behaviors. Queries, executable
spans, source hashes, the 8/12/20 conditions and the primary gate were frozen
before the initial native attempt. Every condition uses original BF16 Qwen
singleton reranking of the first 24 hybrid candidates, static dependencies,
12,000 source characters and 4,096 compact `cl100k_base` tokens. This experiment
compares output allocation; it contains no fast-only Werkzeug condition.

The original attempt completed three warmups and four measured searches before
an overlong transport request ID was rejected. Its raw prefix and independent
cleanup/transport audit remain in the [original attempt](../context-holdout-werkzeug-2026-10-14)
and [repair packet](../context-holdout-transport-repair-2026-10-14). Only IDs were
shortened; queries, gold, conditions and order stayed unchanged. This complete
run is an unchanged technical continuation of a prospectively frozen holdout
that became partly observed during that failed prefix, not another untouched
repository experiment. No prefix outcome was used to tune or select settings.

The continuation ran **12:26:18–12:38:31 UTC on October 14** with a new corpus
checkout and empty index. It completed three untimed HTTP warmups and 135
measured requests, rotating all three conditions across each query in three
passes. Each configuration has 45 measured HTTP observations. All source-credit
outcomes were identical across the three passes.

## Results

| Excerpt cap | Complete behaviors | Multi-file complete | Required groups | Warm HTTP median / P95 | Median compact tokens |
|---|---:|---:|---:|---:|---:|
| 8 baseline | 7/12 | 4/8 | 27/34 | 4.152 / 8.897 s | 3,106 |
| 12 primary | 7/12 | 4/8 | 28/34 | 4.037 / 9.574 s | 3,827 |
| 20 diagnostic | 8/12 | 5/8 | 29/34 | 4.160 / 9.737 s | 4,033 |

Latency includes the actual loopback search HTTP request, fresh query embedding,
hybrid retrieval, reranker HTTP/native work, packing and observation overhead.
It excludes model startup and initial indexing. The same 24 pairs are scored
regardless of excerpt cap, so increasing the cap is not a shortlist-speed test.
These finite sample distributions do not establish a generally faster condition.
Every scoped absent query still returns candidates; rejection was off.

Twelve excerpts delivers a median ten actual excerpts and clips tokens in 9/45
requests; twenty also has median ten and clips tokens in 21/45. The source or
context budget is exhausted in 15/45, 33/45 and 45/45 requests respectively.
Larger configured caps often cannot fit more complete source under the unchanged
token/character limits. Compact token counts are local output accounting,
not coding-agent usage, correctness, billing or savings.

A [post-output stage-visibility analysis](posthoc-delivery-diagnostics.json)
checks all 108 measured positive cases without inference. Full 50-window hybrid
pools contain every required executable span for all twelve positives. The
24-item scored prefix contains all spans for eleven; multipart decoding loses
one implementation at full-pool rank 30. Other incomplete deliveries already
have their required full windows in the scored prefix. This identifies ranking,
shortlist and bounded delivery as future investigation areas; it does not prove
which individual component caused each miss or justify tuning on this holdout.
Werkzeug is now observed development evidence.

## Integrity and retained audit failures

The [completed independent certificate](independent-audit-corrected.json)
passes the full inventory, original source/runtime/checkpoint bindings, actual
query-vector hashes, independent FTS5/cosine/RRF ordering, every pair's literal
tokenizer input, gold credit, exact compact caps and clean owned shutdown.
There are **3,313 native forwards**: one scorer warmup plus 138×24 pair forwards,
with 6,626 alternating native event rows. The embedding runtime processes 1,025
documents and 138 queries in **395 forwards**. No saved query vectors or scores
are substituted. Worker and scorer groups exit without forced termination,
listeners/authentication files are gone, and no signal interrupts the run.

The [first audit attempt](independent-audit.json) started immediately before the
worker's terminal receipt was written and failed on that missing file. The
[completed original audit](independent-audit-completed.json) then reached the
latency comparison and failed because its numerical-summary envelope omitted
`samples_s`. All five numerical fields and all ordered samples were already
identical. A separately committed and [142-test-qualified correction](../context-holdout-auditor-correction-2026-10-14/qualification.json)
adds the exact ordered 45-sample inventory and preserves every original guard,
quality gate and numerical definition. It was made after native outcomes;
the original auditor, both failed receipts and native outputs are unchanged.
The successful certificate uses that correction, without a native rerun.

The [frozen plan](frozen-plan.json), [parent receipt](parent-result.json),
[worker receipt](worker-result.json) and compressed HTTP/search/embedding/native
ledgers preserve the measured evidence. Later product fixes should be assessed
separately from this source-bound experiment. Broader quality, throughput,
clean-machine installation and general coding/token benefits remain unproven.
