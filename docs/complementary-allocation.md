# Complementary implementations within the context budget

Two new selectors can recover previously omitted implementations, but both lose
existing evidence and fail the frozen promotion gate. **Production defaults and
v0.1.4 release assets remain unchanged.** The source-reviewed TanStack Query
repository remains unobserved; no native timing or coding comparison follows a
failed development gate.

This study follows the [negative window-joining experiment](complementary-windows.md).
It changes which definitions occupy the existing eight result slots, while
retaining 12,000 source characters and 4,096 exact compact JSON tokens. Models,
retrieval orders, scored shortlists, source windows and scalar scores stay fixed.

## The allocation rules

Both rules use the existing detector for two to four literal query fragments.
They compute query terms from original paths, qualified symbols and source, then
prefer candidates covering terms not yet represented. A term's weight is its
inverse frequency across distinct retrieved definitions; the exact rational gain
is divided by the original raw rank. Ties keep original rank. This allocation
utility is separate from the original retrieval/reranker score.

- `aspect_fill` protects the first five canonical retrieval anchors and every
  original dependency and its owner, then fills the remaining original slots.
- `aspect_balanced` protects the first three anchors and the same dependency
  context, allowing more exchanges among retrieval results.

Each proposal must retain every query term represented by the original eligible
selection. Inactive queries, exhausted slots, absent novelty or failed term
preservation return the exact previous definition control. The full canonical
candidate list remains in original order for existing containment/backfill.
Original snippets and dependency provenance are unchanged. Planner receipts stay
outside the public response; no extra model or retrieval call is introduced.

The [prospective protocol](allocation-development-protocol.md) fixes these rules,
inputs, controls and gates before outcomes. Lexical term preservation is explicitly
a heuristic; it cannot establish preservation of implementation behavior.

## Results

The matrix contains **365 observed query/order cases sharing 187 dataset/query
units**. Both original controls were reproduced before either new selector ran:
365 legacy responses, 337 previously saved coverage responses, and all 730
immediately preceding parent control arms. Reused studies and retrieval modes are
not independent queries. Every result remains stratified in the
[complete findings](../results/allocation-development-2026-10-26/findings.md).

| Candidate | Additional distinct complete queries vs coverage control | Lost full-group occurrences vs coverage control | Lost complete case rows | Gate |
|---|---:|---:|---:|---|
| `aspect_fill` | 1 | 4 | 4 | Fail |
| `aspect_balanced` | 3 | 12 | 10 | Fail |

The table uses different counting units deliberately: a repeated gain counts once
per dataset/query, while every group loss in every original condition counts
against promotion. Losses on already incomplete queries also count. Both arms
fail against the actual legacy control as well; the prior coverage control's two
known gains are not new results for this study.

New complete-query gains over coverage control:

| Behavior | Original order | Newly delivered implementation | Candidate |
|---|---|---|---|
| Jinja rejects invalid bytecode and recompiles | Dense | `BaseLoader.load`, raw rank 16 | Both |
| Jinja include uses parent/local context | Dense | `CodeGenerator.visit_Include`, raw rank 11 | Balanced |
| urllib3 checks redirect origin before header removal | Dense | `HTTPConnectionPool.is_same_host`, raw rank 6 | Balanced |

The Jinja include query gains complete evidence in dense but loses a required
group in hybrid. A favorable mode does not erase another mode's regression.
These are historical source-evidence results, not new coding trials or measured
token savings.

## Why the guards did not preserve behavior

The separately hash-bound [post hoc analysis](../results/allocation-development-2026-10-26/activation-analysis.md)
describes the fixed results. Its derived counts are outside the canonical audit.
Both rules activate on 329/365 cases. Fill changes final compact context in 146
cases; balanced changes it in 272. These are actual context changes, rather than
merely counting accepted plans.

All four fill losses discard Express `app.set`, originally raw rank eight, while
retaining its query terms elsewhere. Examples include unrelated `res.jsonp` for
“empty,” `res.location` for “url,” and `res.download` for “may.” These responses
have room under both budgets. Query-word presence and rarity do not establish
that the code implements the requested behavior.

Across both arms' 16 candidate-specific lost-group occurrences, 13 remove an
originally eligible implementation, one consumes an existing backfill opportunity,
and two retain the implementation but move it later until character packing clips
required lines. All losses pass the lexical guard; original dependencies remain
protected.

The two packing losses show why a prepacking guard is insufficient:

- Flask `full_dispatch_request` moves from slot four to eight and ends at line
  1011, before required lines 1012–1019. The response uses 11,991 characters and
  3,544 tokens.
- urllib3 `HTTPConnection.request` moves from slot four to seven and ends at
  line 465, leaving the required interval 460–513 incomplete. The response uses
  12,000 characters and 3,612 tokens.

Neither hits the token limit. Full-window query terms cannot protect evidence
that is absent from the actual delivered prefix.

## Verification and reproduction

The [independent audit](../results/allocation-development-2026-10-26/independent-audit.json)
passes on its first attempt. It checks **730 new outcomes and 730 immutable
control references**, original source/definition inventories, scores, dependency
owners, exact compact tokens, rational planner choices, all group changes and
both-control gates. It imports no producer, selector, backend, model or numerical
runtime and makes no network calls.

The new [raw candidate ledger](../results/allocation-development-2026-10-26/replay.jsonl.gz)
stores both new arms with complete source/stage/planner receipts. Controls refer
to exact original records by line number and content hash; the previous 97 MB
matrix and complete source inventory are not duplicated. Plans, gates, source
pointers, original-history resolution and every unfavorable outcome are retained.

The source/plan freeze is commit `8c5f3f1`; execution and committed auditor are
`443b890`. The historical October 21 records retain their original execution
identities despite later rewritten Git history. Original blobs are verified as
data, and the new freeze/execution follows strict current-branch ancestry. The
retained remote `codex/complementary-windows` branch contains original history;
fetch it when reproducing from a checkout that lacks those objects. No historical
producer is executed or relabeled as current source.

Local validation passes **3,017 core tests, 10 skipped**, **118 editor tests**,
and **61 profile/storage helper tests**. New integration checks exercise a later
cross-file result, one query embedding per request, document-vector reuse,
cancellation, exact source hashes and strict MCP envelopes at normal and smaller
budgets. The prospective native harness has 18 synthetic tests; it has made no
native experiment calls. Logs and receipts are in
[validation](../results/allocation-validation-2026-10-26/validation.json).

The [separate native protocol](allocation-native-protocol.md) is ready for a
candidate that qualifies. It requires a fresh multi-file gain, no evidence loss,
and median latency within 25% of **both** controls. Neither current rule may
advance to that holdout.

## Next quality target

Use source structure and query-specific relationships to distinguish an actual
implementation from incidental query words, and verify protected evidence after
packing. Broad file diversity, blanket removal of module-level snippets, and
preserving term sets each fail to establish that distinction. A future rule must
be separately frozen, retain these negative controls, and pass the same all-group
gates before spending the fresh repository.
