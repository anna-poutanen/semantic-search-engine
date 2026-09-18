# Koa eligibility for the Qwen reranker comparison

The unchanged eight-query Koa dataset is eligible for a new, separately frozen
full-query candidate reranking evaluation. Independent CPU checks reverified its
clean official commit, all seven tracked `lib` files, preserved license and
headers, all 175 exact saved-source windows, and complete source evidence for
every positive target. No model, retrieval or ranking calls were made in this
review, and no new reranker or Koa ranking outcomes were read.

The [new eligibility receipt](../results/qwen-reranker-fresh-preflight-2026-10-13/eligibility.json)
binds the [original labels](../benchmarks/koa_clause_holdout.json),
[prior neutral semantic review](../results/koa-clause-preflight-2026-10-13/source-review.json)
and exact static checks. Its reproducible CPU generator is preserved beside it.
All existing corpus, labels, notes, source proofs and prior protocols remain
unchanged.

The previous clause-fusion applicability gate remains failed: these natural
query texts generate no additional clauses. That gate tested whether clause
fusion had a contrast with the original query. It does not invalidate ranking
the original full-query candidate list with Qwen's query/document model. The old
manifest's `inference_authorized: false` remains a property of that prospective
fusion experiment; the newly requested reranker experiment needs its own frozen
protocol rather than modifying the historical gate.

Koa is pinned to `2fecd029c6ce6b79d65158d28a76538f7f6b0378`, package version
`3.2.1`. Its six positive queries have eleven AND-required evidence groups:
three multi-file cases, one same-file compound case, and two single-group cases.
Every target covers an entire executable function or accessor, including guards.
One verified delivered excerpt must contain a group's complete span; names,
comments, partial bodies and candidate-only spans do not earn delivered credit.
The two absent queries cover outbound body-URL fetching with exponential retry
and tenant-key rotating response encryption. These are absent within the seven
tracked core files; cookie signing, inbound HTTP creation, ordinary JSON
serialization and TLS-state access are nearby insufficient behavior.

The complete existing benchmark is **136 distinct queries**: 106 Python queries
across Flask, Requests, Click, Starlette and Rich, plus 30 JavaScript/TypeScript
queries across Express and Zod. All positive Python targets already supply
`path`, `symbol` and `evidence_lines`; no span derivation or label expansion is
needed. Python evidence sometimes binds a sufficient branch, whereas language
and Koa evidence often binds a complete definition. Preserve those authored
semantics and show results by repository and language. Earlier holdout labels
must not imply freshness for this new experiment: all 136 existing outcomes have
already been observed. Koa supplies the additional eight repository/settings
holdout queries, giving **144 queries, 118 positive and 26 scoped absent, across
eight repositories**.

Before opening any Koa ranking results, freeze the query/document instruction,
checkpoint, candidate depth, ranking tie-breaks, source/token packing, thresholds
or lack of abstention, and quality/latency measurement rules for both conditions.
Use the same candidate list and packing caps for baseline and reranked output;
gold may enter scoring only after ordering and packing. Retain all eight cases
and report six-positive, eleven-group and three-multi-file denominators
separately. With abstention disabled, returned decoys on absent cases are still
failures of absence handling; a relevance score is not a calibrated confidence.

This source review establishes static eligibility, not dynamic Koa behavior,
retrieval improvement, warm latency, consumer token savings or coding
correctness. The fresh source was read to author and review labels, and could
have appeared in model pretraining. It is a repository/settings holdout, not an
untouched-source or pretraining holdout.
