# Qwen shortlist and batching experiment

This experiment uses the pinned original BF16 Qwen3-Reranker-0.6B checkpoint,
runtime, literal query/source template and 8,192-token pair cap from the full
October 13 study. Production code and defaults, old labels, old producer scripts
and old result artifacts remain unchanged. New scripts implement the experiment.

## Observed development replay

Replay original dense and hybrid top-50 pools at K = 5, 8, 10, 16, 24, 32, 40, 50.
Only the original first K candidates enter ranking. Reuse their retained original
scalar scores, original source, and the original context packer. Check K50 exactly
reconstructs the original outputs. This is post-output development quality replay,
with zero inference, retrieval or embedding calls; it is not a new latency run.
All 144 queries, including previously observed Koa, are development evidence.

For each retrieval family choose the smallest tested K that preserves every
complete positive query from that family's K50 output, at least the original
no-reranker baseline's covered-group count, and at least the K50 multi-file
completion count. Preserve all partial-group losses and gains separately; equal
aggregate counts do not imply identical support. If no shorter prefix qualifies,
use K50. The initial draft instead required at least the K50 aggregate group count.
After reading the exploratory development replay, before any new model inference,
this was amended to prioritize complete behaviors while explicitly retaining
incomplete-case support losses. The draft is preserved under
results/qwen-optimization-preflight-2026-10-13/protocol-draft-v1.md. This is observed
development selection, not prospective untouched-benchmark parameter selection.
The rule is fixed before parity/live timing and before fresh HTTPX retrieval.

## Batching correctness gate

The pinned MLX-LM Qwen3 backbone supplies native causal attention and RoPE with
offset zero. Batching right-pads each sequence and gathers its last real hidden
state before the same final-token yes/no projection. Real query positions cannot
attend later padded positions under causal attention. Cross-sequence attention
is not performed. No KV cache, model conversion, quantization or instruction
change is introduced. B1 and every singleton group use the original unpadded
scoring function; configured B1 preserves original candidate order.

B = 1, 2, 4, 8. Larger configurations group inputs in ascending token length,
with original index as tie-breaker, and restore original result order. A forward
contains at most 16,384 allocated input-token slots. If adding a pair exceeds
that bound or B, start a new group. Never drop a pair. Record actual group size,
lengths, real/padded tokens, last-token indices, pair IDs and evaluated forwards.
Actual batch forwards and logical pair scores are different counters.

Before retrieval timing, freeze a parity plan containing the first positive
query's dense top-50 pool from each of the eight development repositories,
plus explicitly synthetic mixed-length and source-clipping fixtures. These
literal sources/queries come from fixed saved pools; no embedding is needed for
this diagnostic. Recompute every score at B1/B2/B4/B8. The sequential scores are
freshly evaluated, not substituted from the old ledger. Require identical built
input/source-prefix hashes for all modes. For every scored pair require absolute
yes/no logit error <= 0.0625, logit-difference error <= 0.125 and binary signal
error <= 0.025 relative to B1. Reject any ranking inversion with a B1 score margin
greater than 0.25. Record smaller-margin inversions and top-five changes; a
top-five membership change outside that near-tie band also fails. This is an
internal BF16 batching check, not numerical PyTorch/official implementation parity.
Failed batch configurations are retained and excluded from selection, without
relaxing tolerances. B1 remains the reference. No inference starts before the
implementation, input plan and gate are committed.

## Fresh warm timing and development confirmation

The timing grid compares both retrieval families at their selected K and K50,
deduplicating identical K values, with each parity-qualified B. It also measures
both no-reranker baselines. The fixed timing panel is the first positive, last
positive and first absent query in original manifest order from each repository:
24 distinct queries. Three actual repetitions give 72 requests per condition.
Conditions rotate by query and repetition. The panel is a bounded timing study;
it does not replace full-benchmark quality or latency confirmation.

Every actual search freshly embeds its query, retrieves top50, scores only the
declared prefix and packs only that ranked prefix, with static dependency
expansion unchanged. No query or score caching. All conditions retain five
anchors, two callees per anchor, eight excerpts, 12,000 source characters and
4,096 exact compact cl100k_base tokens. Candidate source identities are checked
within matched conditions and against the old frozen source. Gold scoring and
artifact writes occur after measured packing. Count and preserve all attempts.

Each fresh worker load verifies checkpoint bytes/runtime identity and records
its load/verification time. It scores eight fixed toy inputs once per B as
separately recorded warmups: 32 logical pairs and 15 actual forwards in total.
Model loading, document/index setup and generic embedding warmups are excluded
from measured intervals. Existing valid document vectors may seed owned index
copies. New source embedding is counted explicitly; setup is not a clean-install
benchmark. GPU work is serial across embedding and scoring processes.

Warm timings cover actual embedding, retrieval, the authenticated blocking
loopback scoring HTTP hop and unchanged packing. They include instrumentation
and are not full production HTTP/MCP latency. Record component durations, exact
input/padding counts, MLX allocator peak/cleanup and nearest-rank P95. Repetitions
are timing observations on one Mac, not independent new quality cases.

The first grid attempt was interrupted by Mac clamshell sleep and is preserved
under results/qwen-shortlist-grid-2026-10-13, excluded in its entirety. Before a
new grid begins, freeze a clock-integrity policy: sample wall-clock and monotonic
nanosecond clocks from one pipeline baseline, before/after setup and before/after
each measured search, and after model cleanup. Absolute disagreement greater than two seconds aborts the
whole attempt, including discontinuities between searches. Preserve all observed
clock receipts and failed search attempts. No interrupted sample is filtered or
resumed. The within-search monotonic interval remains the latency measurement;
the wall clock diagnoses continuity, not an alternative favorable timing result.
Neither labels, numeric gates, shortlist choices nor selection rules change.
An additional stopped attempt under results/qwen-shortlist-grid-2026-10-14
preserves a source-commit mismatch during freezing. The driver now refuses a
plan unless every implementation hash matches its recorded Git commit. The
separate replacement remains a full run with the same conditions and schedule.

For each family order grid configurations by P95, median, smaller K, then smaller
B. A configuration qualifies only if its first-pass panel retains all B1/K50
complete cases and multi-file total, and at least the original no-reranker
panel's group count. Preserve losses relative to the K50 group total. Select
the fastest qualifying configuration; if none qualifies, use B1/K50. Then run
both selected configurations and both no-reranker baselines over all 144 queries
with three fresh passes (1,728 actual searches). Require each selected condition
to retain all complete cases and the multi-file total from that family's frozen
old K50 development reference, and at least the original no-reranker group count.
Record every partial-group loss relative to K50 and every new regression.

If a selected configuration fails that complete-development gate, allow at most
two separately frozen fallback attempts from the already ordered grid list.
All failed full-validation attempts remain published. No tuning based on fresh
validation, no best-repetition quality selection, and no silent retries. Quality
always uses the first repetition only. A configuration that misses the five-second
P95 aspiration can still be reported as the fastest measured tradeoff; that does
not make it suitable as the production default.

## Fresh HTTPX validation

HTTPX 0.28.1 is pinned at 26d48e0634e6ee9cdc0533996db289ce4b430177.
Its source scope is tracked Python under httpx, with no exclusions. HTTPX has
previously been a runtime dependency, but has not been an evaluated retrieval
corpus in this project. Independent static source review precedes all inference.
Natural behavior queries, full executable function spans, AND/OR groups, scoped
absent features, exact source hashes and extraction eligibility are frozen before
development tuning completes. Source authors inspect source but never retrieval
output. Source-reviewed freshness does not establish exclusion from pretraining.

After development confirmation, choose between eligible dense/hybrid winners by
most complete queries, most covered groups, most multi-file queries, lower warm
P95, then median (dense breaks any remaining tie). Freeze this single best setup
before any HTTPX query embedding/reranking. On unchanged HTTPX evaluate both
no-reranker baselines, both B1/K50 references and the selected setup, deduplicating
identical configurations. Three actual repetitions; primary quality first pass
only. Report full-span complete/group/multi-file coverage, every gained/lost
support interval, absent decoys, all actual warm samples and setup. Never change
the selected setup, labels, scope or caps after HTTPX retrieval is observed.

Independent post-run audits reconcile source slices, original gold, constructed
input tokens, batch membership/padding, scalar math, candidate order, exact
packing/caps, actual forwards/counters and clean root-owned process shutdown.
Preserve failures and auditor repairs separately. Old studies remain intact.

This experiment measures retrieval delivery and local inference time. Coding
correctness, provider token savings, broad deployment latency and PyTorch parity
remain separate. A coding comparison follows a useful validated quality/latency
tradeoff, strengthened behavioral checks and complete provider usage receipts.
