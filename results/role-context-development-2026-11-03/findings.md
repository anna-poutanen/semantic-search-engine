# State-write preservation: observed development result

The fixed terminal guard passes the development gate on all 393 saved case
occurrences (201 distinct dataset/query units). The independent audit verifies
zero individual lost required groups, complete behaviors or single-file first
hits against **both** legacy and coverage. One additional distinct Jinja multi-file
behavior becomes complete, observed in dense and hybrid. This qualifies the rule
for a separate fresh/native experiment; it does not qualify a product default.

| Arm | Required groups / 522 | Complete positives / 322 | Complete multi-file occurrences | Single-file first hits |
| --- | ---: | ---: | ---: | ---: |
| Legacy | 411 | 250 | 47 | 204 |
| Coverage control | 415 | 253 | 50 | 204 |
| Previous semantic v1 | 418 | 253 | 50 | 204 |
| State-write guard over v1 | 418 | 255 | 52 | 204 |

The new complete unit is `jinja_include_parent_and_local_context`. Two conditions
are two occurrences of one behavior, not two independent successes. All three
required implementations displaced by semantic v1 are preserved. Aggregate group
coverage happens to equal v1, while individual retained groups and complete
behaviors differ. Production still uses coverage control.

## Frozen rule and evidence

[Protocol](../../docs/role-context-protocol.md), guard, runner, independent auditor,
358 invented tests and the complete active `prepared-v2` inputs were committed
and pushed in `b86eeb03c1c1e788a4a7497f1e0d0b41e6c367ca` before the first observed
guard outcome. Execution used that exact commit. The rule accepts an unchanged
semantic-v1 proposal only if every protected baseline row remains an exact
complete dictionary; otherwise it returns the whole coverage response. There
are no retries, new candidates, extra slots, repacking or later backfill.

A baseline row is protected by explicit property/index write syntax or uncertain
literal, definition or parsing evidence. This is conservative syntax preservation,
not a proof of purity, task relevance or complete effect detection. Call-only
mutation and indirect effects can escape; local object writes can be overprotected.
Partial definitions and grammar-ambiguous method fragments stay protected.

The final preflight caught TypeScript lvalue wrappers and added operand-only write
checks and independent invented tests before any observed outcome. Initial
`prepared` and its successful input audit remain retained; a trailing space was
then removed from one auditor test, so `prepared-v2` and a new input audit bind
the exact final bytes. Neither preparation computed a new guard outcome.

[Independent audit](independent-audit.json) reconstructs every classification,
whole-response decision, receipt, compact payload, token/source budget, original
score and provenance. It verifies 786 exact original control references, 393 saved
v1 proposals, and 3,734 historical negative references. The original parent study,
failed diagnostic audit, narrowly scoped audit correction and lossless six-member
native capture archive remain unchanged. The new parent recheck passed before
preparation. All old publication/freeze bindings are checked, not rebound to
self-reported current hashes.

## Activity, costs and limits

The guard accepts 338 proposals, including proposals already equal to coverage,
and rejects 55 whole proposals. Protection facts across delivered baseline row
occurrences include 629 explicit writes, 727 uncertain parse contexts, 212 parse
errors and 143 partial definitions; 113 clipped occurrences overlap those reasons.
These are repeated row/reason counts, not distinct implementations.

The new run performs exactly 393 guard calls and zero current v1, model,
retrieval, index, coalescing or backfill calls. It consumes previously saved
native-Qwen proposals. CPU replay takes 15.924 seconds; this is **not** native warm
search latency, coding completion time or token/billing savings. All outputs stay
within eight excerpts, 12,000 source characters and 4,096 compact `cl100k_base`
tokens.

The full local suite passes 4,612 tests, with 10 skipped. The 358 new invented
contract tests, Ruff and read-only peer review pass. Test and preflight receipts,
source hashes, original input audits, final input audit, run-start, durable raw
outcomes and independent reconstruction are retained alongside this report.

All development codebases and queries were already observed. The rule was
motivated by known losses, so this is development evidence with no held-out or
model-pretraining isolation claim. The small gain cannot establish broad quality.
Attrs remains unqueried and unindexed at this report's creation. Next is a
separately committed attrs source/native protocol, then actual warm searches
including fresh clause inference. The fixed primary quality and 1.25-times-each-
control latency gates apply before any promotion; hybrid cannot rescue a failed
dense result. No release or coding comparison follows automatically.
