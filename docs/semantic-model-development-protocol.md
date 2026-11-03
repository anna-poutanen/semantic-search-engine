# Prospective real-model semantic development study

Authorized November 2, 2026: the user explicitly unpaused model testing. This
ends the earlier code-preparation-only restriction for the following study. It
does not change the fixed `semantic_final_v1` rule, product default or release.
No new candidate outcomes may be inspected until this protocol, all producer/
auditor source and tests, model/runtime binding, prepared input inventory and
the complete control barrier are committed and pushed.

## Question and fixed sample

Can pinned Qwen semantic support select complementary full implementations from
the existing ranked pool while preserving every individually covered required
implementation against both original legacy and current coverage controls?

Use exactly the existing `complementary_inputs` matrix: 393 observed case
occurrences, 201 distinct `(dataset, query_id)` units, 322 positive occurrences
and 71 absent occurrences. Preserve original dataset/mode/study distinctions,
raw window order and every original retrieval/reranker scalar. This is reused
development evidence, not 393 independent queries or a new held-out test.
Preserve all 2,948 historical negative references and original Git identities.
No labels, groups, cases, thresholds, ties or context caps may change afterward.
The complete condition inventory is `bm25` (30), `dense` (102), `hybrid` (102)
and `hybrid_k24_b1` (159); the last is explicitly hybrid retrieval with the
original scored 24-window prefix. Unknown conditions must fail closed.

Before feature capture, reconstruct all 786 original legacy/coverage controls
and exact compact payloads from their frozen source, graphs and raw ranks.
Every response/source/hash/token comparison must pass. Preserve original
pre-coalescing packed rows and the actual once-coalesced final coverage response
for each case. Candidate replay starts there and performs no later backfill.
The preparation barrier's coalescing work is historical preparation, not a new
coalesce during each candidate replay.

## Historical features and live-request distinction

These traces do not supply retained original query vectors. The study therefore
captures **fresh historical query-feature encodings**; none is reported as reuse
of an original retrieval vector. Saved raw pools are not re-retrieved or
reranked. This isolates allocation using new features on old candidate pools.
It is separate from the live provider's exact executing-query-vector reuse.

Match the current provider's activation policy: BM25 is inactive, and empty or
abstained source pools and queries without two to four existing literal clauses
receive exact coverage without new features. Other existing modes are eligible.
For an eligible case, facets are the exact original full query followed by the
unchanged literal clauses, with equal weight. No paraphrases or label-derived
queries are allowed.

Deduplicate query requests only when full query and ordered facets are exact.
Encode each full query as one singleton `embed_query`, then its additional
clauses with one `embed_queries` call. Do not batch full queries with clauses or
batch clauses across distinct queries. Process query request IDs in stable
lexicographic order. Reuse these exact captured features across repeated offline
case occurrences and report the actual distinct request and forward counts.
Such reuse is not a native first-query latency measurement.

Collect every required full raw and full reference window through the existing
feature boundary, including delivered dependencies outside the raw pool. A
clipped reference prefix receives no full-window support. Render documents using
the exact `Path`/`Symbol`/`Source` wrapper. Deduplicate only identical rendered
document hashes/text; retain every original dataset/window/source/provenance
binding. Sort document request IDs lexicographically and encode that fixed list
once with existing stable ascending-token-length batches. Do not substitute
historical cache rows or relabel an older model fingerprint as a current hit.

The model is original unquantized `Qwen/Qwen3-Embedding-0.6B`, revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 1,024 dimensions, the existing query
instruction, last valid-token pooling and float32 normalization. Use batch size
four, the existing per-input 8,192-token truncation and total 8,192 padded-token
budget. Bind exact current package metadata, Python/runtime identity and every
checkpoint file's bytes/hash before execution. The actual weights SHA256 must
equal the recorded pinned Hub LFS etag; provenance text alone is insufficient.
Model/path/runtime drift invalidates the attempt before inference.

## Capture and independent audit

The root agent alone runs model work under an exclusive advisory lock in the
Git common directory. No other model/service/index task may share that runtime.
Keep source/tests unchanged throughout capture and replay. Record the prospective
freeze commit and execution ancestry, all prepared source/artifact hashes,
model binding, verified offline tokenizer and explicit request inventories.

Claim a new attempt directory before model loading. Refuse existing paths and
changed/dirty or uncommitted bindings. Preserve run-start, every actual forward
start/completion/failure, native input/prompt/token hashes and lengths, scheduled
indices/padding, output shapes, float32 vectors and durable completed checkpoints.
A started callback alone does not prove model execution. Keep partial ledgers
and failure receipts; never overwrite or silently retry an attempted directory.
Any repaired attempt needs a new named path and separately committed bindings.

Store the prepared JSON compactly while preserving insertion order. Keep unique
native vectors in the immutable capture catalog; each outcome binds its complete
per-case features using SHA256 of UTF-8 JSON with sorted keys, no whitespace,
literal Unicode and no nonfinite numbers. Store outcomes in a lossless gzip JSONL
ledger, flushing the compressor and fsyncing the underlying file after each row.
The auditor rebuilds the features from that catalog and verifies each digest.
These storage choices do not change any request, feature, outcome or quality gate.
The earlier, fully passing CPU control preparation is preserved with its exact
source in `initial-control-preparation.tar.gz`; it produced no model features or
candidate outcomes and was superseded only for these storage changes.

Complete feature capture before producing candidate outcomes. Capture binds
fresh singleton/full-query and within-query clause work separately from full
document work. No extra retrieval, raw tail, source stitching or prefix inference
is allowed. Offline replay uses the fixed semantic allocator and original
precoalesce/reference operands, at most two admissions/eight exact budget trials,
eight excerpts, 12,000 characters and 4,096 exact compact `cl100k_base` tokens.
Actual replay model/retrieval/coalesce work is zero and must be reported as such.

The separately committed standard-library auditor independently checks source/
model/runtime/checkpoint bindings, request/vector/forward membership, all 786
controls and all 393 outcomes. It reconstructs semantic features and final
allocation using the existing independent reconstruction, never the producer
selector/feature calculations. It verifies literal final payload, provenance,
protected source, original scalars, exact tokens, and every individual required
group/complete behavior/single-file first hit against both controls.

## Unchanged gates and reporting

Require zero individual lost required groups, complete positive behaviors or
single-file first hits against **each** control, including already-incomplete
queries. Require at least one additional distinct complete multi-file behavior
over coverage. Report every gain/loss and its exact source, both aggregate and
individual gates, activation/admission/fallback rates, and distinct units versus
reused case occurrences. An unchanged total cannot hide an individual loss.
Absent cases remain in the record; similarity or a returned decoy is not proof
that a behavior exists or is absent.

Feature-capture, pure allocation and audit timings are operational diagnostics,
not native end-to-end warm search latency. Do not sum/replay them to claim paid
tokens, coding completion time, latency savings or a qualified live setup.

A failed development gate leaves the reserved attrs codebase unqueried. Only a
qualifying rule proceeds to a separately committed attrs/native plan using actual
live-request vectors and resident document lookup. That phase must preserve the
same raw pool across arms, pass its individual quality gate and keep warm median
at most 1.25 times each control. It has not been authorized to bypass development
qualification. No automatic promotion, release or coding comparison follows.
