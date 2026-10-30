# Semantic admissions in final coverage: preparation protocol

This is one research rule, `semantic_final_v1`, prepared after the failed
`complementary_v2` study. It is not a production default or a successful retrieval
result. Preparation uses invented CPU fixtures only. No real-model inference,
real-codebase indexing/retrieval, observed candidate outcome or reserved attrs
source/label read is authorized during this preparation phase.

The earlier failure motivates two specific changes. The selector starts with
the **actual delivered coverage context**, including its actual backfill rows,
instead of choosing an allocation that later backfill can displace. Its task
relevance signal uses the existing pinned Qwen embedding model rather than rare
literal query-word matches. Semantic similarity remains a heuristic; the
unchanged individual-evidence gate decides whether the rule qualifies.

## Fixed inputs and semantic features

The unchanged coverage policy performs ordinary packing and `coalesce_context`
exactly once. Its actual final response is the reference and starting context.
The adapter gives the feature provider defensive copies of the reference and
raw rows; provider work cannot rewrite those original guard operands. Copying
and feature validation remain part of future end-to-end request timing. A
provider exception fails the run instead of being recorded as a successful
no-change search. An invalid returned capture instead yields exact coverage.
The research adapter also supplies its actual original packed rows through
`precoalesce_rows`, not a caller-selected protected projection. Every such row
must be literally covered by an actual reference row with the same saved/buffer
provenance. Containment may change the delivered row's origin, but never the
literal source or buffer identity. Coalesced-away dependencies can therefore be
protected without requiring their original IDs to remain returned IDs.

Only the actual recorded raw pool may contribute a new window: at most 50, or
the original scored K24 in reranker cases. Preserve that order and every
original raw scalar. Retain the first two actual raw windows per exact canonical
definition; unknown keys remain separate window-ID identities. Dependencies
already in coverage need not occur in the raw pool. The rule makes no new
retrieval call and never adds an unscored tail or a stitched source window.

Activate only when the existing `multifile_clauses` detector returns two to four
literal fragments. Facets are the original full query followed by those exact
fragments, with equal weight. Use the existing pinned Qwen query instruction for
each facet and the existing `Path`/`Symbol`/`Source` document format for each full
window. The separate feature boundary validates the model fingerprint, literal
facet/prompt identities, actual document hashes and saved float32 vectors before
computing any scores. A score is `round(cosine * 1_000_000)` after clamping cosine
to `[-1,1]`, using the fixed finite/nonzero float32-vector cosine kernel. Features
are not retrieval, H2O or Qwen-reranker scores and never replace those scalars.

Every full raw and full actual reference window must have valid bound features.
One missing, stale or invalid required feature returns an exact defensive copy
of the actual coverage response. A clipped reference row sharing an ID with a
full raw window may use only its validated exact literal prefix identity; its
full-window feature supplies **no** support for that delivered prefix. There is
no prefix model call. Inactive queries require no feature object or feature
validation. Any model/capture work and observed-quality execution require a
later separately approved, committed prospective plan.

## One fixed direct-admission rule

1. Begin with the actual final coverage rows. Protect every actual clipped row,
   every actual final dependency row, and actual rows overlapping the source
   intervals of the first five canonical raw anchors, all original packed/final
   dependencies and their known original source owners. Owners are recovered
   from actual raw, pre-coalescing and final rows; no static graph, invented
   relationship or rewritten `related_to` is introduced. Containing rows can
   carry original owner source without having the owner's original source ID.
   An original packed/final dependency whose source-owner ID occurs nowhere in
   actual raw, original packed or final source causes exact coverage fallback:
   the selector cannot protect an unknown source-owner interval.
2. If a coalesced-away source ID occurs in neither raw, final nor supplied
   pre-coalescing rows, return exact coverage. The usual research adapter
   provides all original packed rows; the conservative default also permits
   callers without that capture to decline an unobservable case safely.
3. For each facet, context support is the maximum integer semantic score among
   **unclipped actual delivered windows**. Clipped windows are excluded. An
   empty full-window set has support `-1_000_000` for each facet. There is no
   term-union support or semantic credit for omitted source tails.
4. Enumerate candidate/victim exchanges against that actual current final set.
   A candidate is a retained, full, original raw retrieval window not already
   returned and not wholly covered by a current literal row. The victim must
   be unprotected. A trial replaces exactly that victim position; no other
   row, source bytes, scalar, origin or owner changes. When there is a free
   excerpt slot, also enumerate an empty victim at the next final row position:
   it appends the full candidate without removing existing source.
5. A semantic proposal must preserve or improve support for **every** facet
   and strictly increase their summed support. Utility is that integer summed
   increase. Order proposals by greatest utility, then smaller original raw
   rank, then later current victim position. The empty victim is after every
   existing position, so an append wins when it provides equal utility for
   the same candidate. There is no relevance threshold,
   rarity weight, tuning sweep or graph bonus.
6. Try proposals in that order. Count the actual complete compact JSON and
   source characters. Reject any over-budget proposal **whole**; never clip it
   or trim existing rows. An over-budget append can therefore be followed by a
   feasible whole-window replacement. Continue to the next ordered proposal, allowing at
   most eight exact budget trials over the entire request.
7. Accept at most two admissions in total, counting both appends and exchanges.
   After an acceptance recompute semantic maxima
   and available pairs from the actual resulting rows. If there is no feasible
   positive exchange, stop. Retain any accepted prefix when a later trial limit
   is reached. The eight-trial counter does not reset after an acceptance.
8. Check the actual final payload against the actual reference one last time.
   Protected dictionaries must retain field presence, recursive scalar types,
   values, source, hashes, origins, owners and provenance. Every delivered row
   must be exactly one allowed original reference/raw row. Require the original
   reference's per-facet floor and all final budgets. Failed checks return exact
   coverage. The returned response receives no research receipt. An accepted
   exchange removes the private `_selection_backfill` diagnostic, which described
   baseline containment/backfill and can no longer describe delivered source.
   The adapter retains that original response/debug in its external trace;
   exact fallbacks preserve the complete original response, including debug.

The final limits remain eight excerpts, 12,000 source characters and 4,096 exact
compact `cl100k_base` tokens. There is **no subsequent backfill or coalescing**:
the guard evaluates the same source set that reaches the client. Existing
operational fields continue to describe the actual original search; source
totals and exact token counts are updated after actual accepted exchanges.
Original packing/truncation flags remain conservative history because the rule
does not undo that original work or remove protected clipped rows.
The backend refreshes top-level `timings` and `indexstats` after the hook. The
source auditor permits changes only to those operational fields; every other
field, source row and captured allocation receipt remains exact. The later
native timing/call ledger must independently verify that operational work.

## Interfaces and preparation validation

`src/semantic_search/semantic_context.py` exports:

```python
semantic_final_context(
    reference, raw_rows, features, *, definition_keys=None,
    max_results=8, source_budget=12000, token_budget=4096,
    cancel_check=lambda: None, precoalesce_rows=(),
) -> (response, receipt)
```

The receipt records activation/fallback, exact facets and integer support,
canonical raw portfolio, anchor/protection reasons, every actual budget trial,
accepted candidate/victim exchanges, final guard and a zero subsequent-backfill
count. It is captured outside the public backend/MCP response. The pure selector
does not load a model, initialize an index or read a source file. Tests use only
invented CPU data. The separate feature module owns vector/source/prompt
validation; semantic selection tests may inject invented validated integer
features to isolate the exchange rule.

Before observed execution, commit and bind the source, tests, feature boundary,
adapter, independent auditor, protocol, complete raw/source/reference manifests,
tokenizer and runtime identities. Reproduce both original control responses and
their exact compact payloads before any candidate outcome. Preserve all earlier
negative results and original Git identities. Refuse uncommitted/changed
bindings or existing attempt files; keep failures and partial ledgers instead
of overwriting them. No new gate, label or query may be chosen after outcomes.

The development gate remains zero individual lost required groups, complete
positive behaviors or single-file first hits against **both** legacy and current
coverage, plus at least one additional distinct complete multi-file behavior
over coverage. Reused modes/studies are case occurrences, not independent
queries. Failed development leaves the reserved attrs codebase unqueried.

Only a qualified development rule proceeds to a separately committed fresh
source-reviewed codebase and actual warm native validation. Report prepared
feature allocation overhead separately from new-query end-to-end latency,
including every additional facet forward and actual batch. A repeated-query
feature-cache hit cannot stand in for first-query embedding cost. Preserve the
same raw pool across arms and disclose any numerical batch effects. The warm
median gate remains at most 1.25 times each control, alongside the same quality
gate. None of the preparation tests establishes retrieval quality, native warm
latency, coding correctness, completion time or provider-token savings.
