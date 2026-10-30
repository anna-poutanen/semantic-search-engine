# Semantic final-context preparation

The next multi-file experiment is implemented for **CPU preparation only**. It
has not been evaluated with real Qwen features or observed query outcomes. The
backend still uses the coverage/backfill policy shipped in v0.1.5; release assets
are unchanged. The earlier [joint-window rule](complementary-context-v2.md) still
fails its individual zero-loss gate.

That failure exposed two weaknesses: rare literal query words can favor the wrong
implementation, and later backfill can undo an earlier allocation. The prepared
`semantic_final_v1` rule starts with the actual final coverage response, then
admits at most two full retrieved windows using equal-weight semantic support
for the original query and its existing literal clauses. It never runs another
backfill pass. It protects anchors, dependencies, source owners and clipped rows;
clipped source receives no semantic credit from an unseen full-window tail.
The unchanged caps are eight excerpts, 12,000 source characters and 4,096 compact
JSON tokens. Similarity is a heuristic, not proof of required behavior.

## Implemented

- A pure feature boundary binds captured float32 vectors to the pinned model,
  exact query instruction/facets, document wrapper, source hash, location and
  saved/buffer provenance. Missing or invalid features return exact coverage.
- A terminal research adapter performs one actual coverage pass, gives a feature
  provider defensive copies, then runs the selector once. Provider failures
  propagate. Production routing does not install this hook.
- An additive `QwenEmbedder.embed_queries` API retains the existing prompt,
  ordering, token limits, padded-token batching and cancellation. Existing
  single-query/document code and index model metadata are unchanged.
- An independent standard-library auditor reconstructs features, allocation,
  protections, budgets and final compact payload without importing the selector.
  Toy backend tests cover saved source, buffer overlays, cancellation and stale
  source checks; no real embedding model is loaded.
- A guarded, offline readiness CLI exercises the complete terminal hook using
  invented source and unit vectors. CI retains its receipt alongside the full
  CPU test suite and installed lexical CLI smoke.

The [fixed rule and evaluation protocol](semantic-context-protocol.md) specifies
all tie-breaks, fallback cases, budget trials and future quality/latency gates.

## Preparation evidence

The [local validation receipt](../results/semantic-preparation-2026-10-30/validation.json)
records **239 passing affected CPU tests**, including the existing embedding
tests, plus lint, source invariants and the readiness check. The
[readiness receipt](../results/semantic-preparation-2026-10-30/readiness.json)
records one coverage call, one invented feature call and one semantic call. It
appends a window, then exchanges another, yielding eight literal rows and 884
compact tokens. Independent reconstruction passes and original ranking scalars
are preserved. These are invented fixture outcomes, not retrieval-quality or
native-performance results. The earlier receipts are retained separately. The
first correction replaces an assigned lambda with a function. A subsequent
source review expands imported helper hashes and explicitly labels zero
model/corpus activity as source-reviewed claims; only denied import/socket
attempts have measured counters. The final receipt binds the corrected source.
These corrections do not alter the invented allocation result.

The Mac was on battery, and the user explicitly requested code preparation only.
There were **zero real model calls, real-codebase indexing/retrieval calls,
observed candidate outcomes or reserved attrs reads** in this phase. The local
full suite was deferred to GitHub CI; this report does not treat focused tests
as a full-suite run. No native latency, coding correctness, completion-time or
provider-token comparison has been performed for this rule.

To reproduce the invented check after installing the development dependencies
and preparing the verified tokenizer cache:

```bash
python scripts/check_semantic_preparation.py \
  --tokenizer-cache .cache/tokenizers \
  --output results/local/semantic-readiness-new.json
```

The output path must be new. The fresh CLI process rejects specified model,
index and backend imports and patches socket connection/name-resolution entry
points. These narrow guards are not a generic filesystem or network sandbox;
source review separately verifies that the invented path reads no observed or
reserved corpus and invokes no real model or retriever. The separate backend
tests intentionally use a toy CPU index with stub embeddings.

## Remaining work after model testing resumes

1. Implement the live feature provider and a prospectively frozen capture/replay
   harness. Bind actual model/runtime/tokenizer identities, original sources,
   raw pools, references and every query/document forward. A vector payload alone
   does not prove that the claimed model produced it. Old and current model
   metadata fingerprints differ; never relabel old cached vectors as new hits.
2. Preserve the original full-query retrieval vector across arms. Batch only
   additional clauses or capture and disclose numerical parity first. One API
   call may require multiple actual forwards under padded-token limits. Measure
   first-query facet cost separately from repeated-query feature-cache hits.
3. Freeze and commit all code and inputs before outcomes. Reproduce all 786
   original control responses, then evaluate the 393 observed cases representing
   201 distinct query units. Require zero individual evidence losses against
   both legacy and current coverage and at least one additional distinct complete
   multi-file behavior over coverage. Aggregate totals cannot conceal losses.
4. Only a qualifying rule proceeds to the reserved, source-reviewed attrs corpus
   and actual warm native timing. Keep the same raw pools across arms and require
   a warm median no greater than 1.25 times either control alongside the quality
   gate. The reserved corpus remains unqueried until that development gate passes.
5. Only after retrieval qualifies, rerun larger coding workflows with independent
   correctness checks and actual provider usage. Production promotion and another
   release require that separate evidence.

The live provider, observed replay and native timing harness are still to be
implemented. This preparation is not a completed quality study or a release.
