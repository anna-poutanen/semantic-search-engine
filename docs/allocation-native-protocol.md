# Prospective validation of complementary implementation allocation

This protocol is fixed before any new allocation outcomes. It reserves the
previously source-reviewed TanStack Query core repository, revision
`ecdf96296b1dfcee617a5a5b9afdf2a42ad436ba`, for a single candidate qualified by
the [development replay](allocation-development-protocol.md). The existing
dataset, source-only reviews, complete canonical window inventory, exclusions and
licenses remain unchanged. No retrieval, index, model query or rule activation
on that repository is permitted before the chosen candidate and native plan are
committed. This is project evaluation independence, not pretraining isolation.

The fixed development arms are `aspect_fill` and `aspect_balanced`. They select
from existing retrieved definitions using literal query terms, preserving the
original scalar scores and every protected static dependency and its owner.
They introduce no new retrieval call, clause embedding, reranking, source union,
query rewrite, score interpretation or indexed document. Query-term preservation
is a heuristic guard, not a correctness guarantee.

Only an independently audited candidate that loses no covered evidence group,
complete positive or single-file first hit against **both** `legacy` and
`coverage_control`, and adds an additional distinct complete query beyond
coverage control, may advance. Previously known coverage gains cannot qualify.
If both arms qualify, prefer more distinct complete multi-file gains, then more
distinct complete gains overall, then fewer changed case rows; the final tie
prefers `aspect_fill`. Choose once using development evidence and freeze that
choice before fresh results. If neither qualifies, retain the negative result
and stop before fresh inference.

The native comparison has three arms: actual `legacy`, previous research
`coverage_control`, and the chosen candidate. All use eight excerpts, 12,000
source characters and 4,096 exact compact `cl100k_base` JSON tokens. Dependencies
remain enabled; retrieval depth stays 50, extraction stays 160/40, and the pinned
Qwen3-Embedding-0.6B checkpoint and vector cache policy remain unchanged. Dense
and hybrid are reported separately.

Freeze the exact implementation, tests, protocols, original-history resolution,
development artifacts and independent audit, model files/runtime, parser and
tokenizer versions, dataset/review/inventory bytes, source membership and
schedule. The backend's complete canonical map must match the source-only
preflight map. Original October 21 producer records retain their original commit
identities: verify those immutable blobs as data despite the later rewritten Git
history. The new native freeze and execution must have strict current-branch
commit ancestry. Never attribute later code to a historical run.

Load the model/index and perform six explicit arm/mode warmups outside timing.
For every query, rotate the six arm/mode pairs across three actual repetitions:
14 queries × 3 arms × 2 modes × 3 repetitions = **252 measured searches**. Every
request performs exactly one new query embedding and no document work. Record
actual vectors and hashes, original raw orders/scores, chosen definitions,
protected dependency/owner IDs, query terms/frequencies, exact rational marginal
gains, fallback decisions, actual source, provenance and compact text/tokens.
Capture the actual planner receipt, not a later recomputation substituted for it.

Time the complete resident backend search, including query embedding, selection,
source checks and exact packing. Serializing receipts and draining diagnostic
iterators occur after the timer. Run no concurrent heavy tests, audits or model
processes during timing. Preserve interruptions, partial outputs and failure
receipts; never overwrite or resume an attempt. Repairs require a new named plan
and committed implementation before another run.

Fresh evidence scoring retains the existing stricter criterion: one actual
returned excerpt must contain an allowed qualified symbol's entire evidence
interval with exact pinned source bytes/hash. OR alternatives describe equivalent
source; all required groups are AND requirements. No credit comes from an ID,
unreturned full window, planner term or dependency label alone. Historical
development uses its original path/full-interval criterion and stays separate.

For each retrieval mode, independently compare the candidate with **both**
controls. Promotion requires:

- At least one net additional complete multi-file query over coverage control.
- No individually lost full group, complete positive or single-file first hit
  against either control, including losses from previously incomplete queries.
- Every raw/source/identity/score/provenance/token/binding/planner audit passes.
- Median resident latency at most 1.25 times **each** same-mode control median.

Dense is the production default and must pass; hybrid cannot rescue a failed
dense gate. Retain every gain/loss, missing-group stage, inactive/active case,
absent-query inventory, median/P95 and paired latency difference. These searches
exclude MCP/HTTP transport and coding work; they do not demonstrate coding
correctness, billed token savings or faster agent completion.

Any default promotion additionally requires client/schema/source/cache/cancel
tests and smaller-budget sensitivity checks. No quality or latency result at
8/12,000/4,096 proves safety for every smaller request. Without all gates passing,
keep production behavior and release assets unchanged. Once any fresh outcomes
are observed, this repository becomes development evidence for later tuning.
