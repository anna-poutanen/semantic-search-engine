# Complementary context v2: prospective development protocol

This is one new, label-free selection experiment, `complementary_v2`. Its source,
tests, evaluator, independent auditor, protocol and input plan must be committed
unchanged before any new observed-query outcome. Preparation uses invented
fixtures only. Production continues to use the unchanged `definition_context`
and `coalesce_context` coverage policy. A separate v0.1.5 release does not imply
that this experimental selector qualifies or becomes a product default.

## Evidence and comparison

The intended matrix is the previous 365 observed query/order cases plus the 28
first-repetition dense/hybrid TanStack Query cases: 393 cases sharing 201 distinct
dataset/query units, including absences. Freeze must count and assert these
identities rather than treating reused modes/studies as independent queries.
TanStack is now observed development data. None of these results is a fresh
holdout, coding trial, model-latency measurement or provider-token saving.

Historical controls, plans, source files, scripts, auditors and unfavorable
results remain immutable. Both actual legacy display-label contexts and current
coverage contexts must reproduce their full source-bearing responses and exact
compact JSON before new outcomes. Compare individual groups, complete positive
behaviors and single-file first hits against both controls. Historical negatives
from window union/continuation, term novelty, graph neighbors and shared flow
remain negative evidence; their gains are not attributed to this rule.

Only the actually recorded ranked input enters selection. In particular, the
Qwen K24 cases keep their 24 scored windows; no unscored retrieval tail is added.
The native records keep their recorded raw 50. This experiment makes no retrieval,
embedding, reranking, index, model, source-fetch or network call. No new fresh
corpus, labels, query text or model metadata is read. The complete already-frozen
source/canonical inventory and conservative static graph may supply original
dependency context and graph identities, as they do for the existing controls.

Every arm keeps 8 excerpts, 12,000 source characters and 4,096 exact compact JSON
`cl100k_base` tokens. Whole physical CR/LF/CRLF line prefixes, original scope,
hashes, overlay metadata, dependency origins and original scalar scores remain
truthful. Windows are original retrieved excerpts, never stitched composites.

## One fixed rule

The constants are `MAX_RAW_WINDOWS_PER_DEFINITION=2`, `MAX_ADDITIONS=2` and
`RANK_OFFSET=8`. The sole mode is `complementary_v2`; there is no parameter sweep.

1. Run unchanged `definition_context` once over the original ordered pairs to
   obtain the original canonical `selected` pool, dependency-expanded eligible
   list and origins. The first five canonical retrieval anchors are unchanged.
2. Activate only when the existing `multifile_clauses` detector returns two to
   four literal fragments. Its split points and minimum three content terms per
   accepted fragment remain unchanged. Inactive queries return the exact
   original canonical pool/allocation.
3. Independently retain the first two actually ranked windows of each known exact
   canonical definition, in raw order. Unknown windows retain separate window-ID
   identities; matching display labels do not merge them. This is a **raw-window
   portfolio cap**, not a final delivered-window limit. All original static
   dependency windows are allocation-preserving exceptions, whether absent from
   raw or present at a later raw rank. Such a dependency may coexist with the two
   raw portfolio windows and keeps its actual owner and inherited anchor scalar.
4. Normalize terms with the existing tokenizer and clause stop words: discard
   terms shorter than three characters, change final `ies` to `y` for words
   longer than four, otherwise remove final `s` from words longer than three
   unless ending in `ss`. Features are the intersection of query-aspect terms
   with terms in the literal path, qualified symbol and original window source.
   This includes literal comments/strings and is explicitly a lexical heuristic.
5. Count each term's document frequency across distinct exact definitions in all
   original raw windows, including windows beyond the two-window portfolio.
   Unknown window IDs count separately. A term has weight `1/max(1,df)`.
   For each window/aspect, support is the sum of matching term weights divided
   by that aspect's total term weight. All arithmetic uses exact fractions.
   Unlike term-union novelty, one actual window must supply an aspect's support.
6. Protect the first five anchors, every original dependency and its source owner,
   plus eligible definitions that are unique one-hop callers/callees of those
   anchors in the existing static graph. Ambiguous or unresolved graph labels
   create no witness. These are source relationships, not proof of relevance.
7. Among portfolio windows outside the original eligible IDs, compute each
   candidate's gain as the sum of positive improvements over the **best single
   window's support per aspect** in the protected/already-admitted set. Utility
   is `gain/(original_raw_rank+8)`. Pick greatest positive utility, ties keeping
   smaller original raw rank. No positive gain ends admission. The support seed
   excludes unprotected baseline tail rows; the later delivered-support guard
   separately checks the actual control. Keep at most two admitted additions.
   Admission counts baseline windows that are present anywhere in raw plus prior
   additions, and admits no third such window per definition. An outside-raw
   dependency does not consume that admission cap. Unchanged later backfill is
   still allowed to use both portfolio windows beside an original dependency;
   there is no stateful final per-definition cap. The eight-excerpt limit holds.
8. For the proposed candidate, protect every unique direct caller and all that
   caller's unique direct callees using actual existing `P→C` and `P→B` edges.
   This includes already-eligible complementary siblings such as preparation
   steps. No receiver inference or newly invented edge is permitted. Test whether
   the resulting unprotected baseline rows can accommodate all admissions so far
   plus this one. **Stop at the first capacity-blocked utility winner**; do not
   skip it for a weaker candidate. Retain any accepted prefix, and roll back all
   protection from the blocked trial. Recompute gains after each accepted pick.
9. Evict the latest remaining unprotected baseline rows, one per admission.
   Deliver protected baseline rows in their baseline order, additions in actual
   admission order, then the unevicted unprotected baseline tail in baseline
   order. Original windows and scalars are not rewritten. The returned full
   selected/backfill pool becomes the two-window portfolio only if at least one
   candidate was admitted. Otherwise return the exact original canonical pool,
   eligible list and origins, without alternate-window backfill.

## Actual packing and source guard

The backend packs the proposal through its ordinary whole-line character and
exact compact-token passes. The postprocessor calls unchanged `coalesce_context`
once. On an admitted plan, it obtains one actual same-input coverage response
through the existing lazy reference factory, then calls
`guard_complementary_plan(reference, proposal, selection_plan)` once.

Protect actual reference rows whose ID or exact definition is protected, whose
origin is dependency, or whose actual physical interval overlaps an original
protected window on the same path. This includes coalesced containing rows,
clipped anchors and protected neighbors admitted by reference backfill. Every
protected physical span must remain covered by a single proposal row with exact
literal bytes and saved/buffer provenance. A dependency requires the same original
window ID, origin and `related_to`. Same-ID windows also retain original content
hash and score-field key presence/values. A containing different retrieval window
keeps its own actual raw scalar; no score or relation is fabricated.

`related_to` identifies the original **source window**, not necessarily a returned
row. An owner row may be coalesced into an enclosing excerpt when its literal
source remains covered. The guard does not rewrite the direct caller to that
enclosing definition or demand a fictitious returned-row owner ID.

Recompute per-aspect best-window support from the **actual delivered** path,
symbol and source strings, using the frozen term frequencies. Require every
aspect's value to be at least its actual coverage-reference value. Full source
tails and `window_end_line` cannot supply missing delivered terms. Any protected
source/provenance/scalar loss or decreased aspect support returns the exact actual
coverage response. These are label-free safeguards; they do not establish behavior
preservation. The independent all-group quality gate remains mandatory.

When no candidate is admitted, the returned pool/allocation is exactly coverage.
Coalesce once, invoke the pure guard once with `reference is proposal`, and mark
the guard receipt `reference_kind=self_reference`. Do **not** call the lazy
reference factory or perform a second packing pass. An admitted plan has one
factory call and `reference_kind=coverage_factory`. Capture actual call counts;
a self reference is never represented as a factory receipt. No research receipt
enters a public backend/MCP response.

## Interfaces, freeze and qualification

New implementation: `src/semantic_search/complementary_context.py`; invented
fixtures: `tests/test_complementary_context.py`.

`complementary_plan` returns `(selected, eligible, origins, plan)`;
`complementary_context` returns the first three. `eligible.source_plan` is a
defensive snapshot of that one actual plan. `guard_complementary_context` consumes
that exact plan and the existing backend postprocessor arguments. It calls the
pure guard once; the evaluator captures its actual operands/receipt outside
public responses. Planner/guard schemas are `complementary_context_v2` and
`complementary_guard_v2`. Receipts contain original ranks/canonical keys,
base/portfolio/eligible IDs, exact term frequencies/fractions, genuine graph
witnesses, every admission/block/rollback and actual postpacking guard choice.

Freeze binds this source, tests, protocol, unchanged imported helpers, evaluator,
independent auditor, complete existing input/source inventories and both immutable
controls before new case outcomes. Run must reject changed/uncommitted bindings
and preexisting attempt files. Claim an exclusive attempt before replay, preserve
failure markers and partial ledgers, and never retry by overwriting evidence.
The independent auditor reconstructs choices, provenance, packing and scores
without importing the candidate producer. No data labels enter the planner.

Qualification requires **zero individual lost evidence groups, complete positive
behaviors or single-file first hits against both coverage and legacy**, and at
least one new distinct complete multi-file positive behavior over current coverage across
the observed matrix. Report every stratum and count reused wins only once per
dataset/query unit. Absence returns do not prove a feature absent. A failed arm
remains failed; no gate or historical label is changed after outcomes. A passing
development gate still requires a separately frozen, independently reviewed new
codebase and warm latency validation before any promotion claim.

## Producer execution and artifacts

The new producer is `scripts/replay_complementary_context.py`, with invented
replay fixtures in `tests/test_complementary_context_replay.py` and actual CPU
backend/MCP contracts in `tests/test_complementary_context_backend.py`. It uses
`scripts/complementary_inputs.py` solely to read the already-audited inventories,
original raw pairs, controls and negative records. Its freeze binds the complete
imported production/helper and independent-auditor source closure, CPU package
versions and exact cached tokenizer bytes. It never reads reserved attrs data.

`--controls-only` reproduces all 786 original control contexts without importing
or calling the new candidate. `--freeze` repeats that control-only barrier and
writes `frozen-plan.json`, `input-manifest.json`, `control-references.json`,
`negative-references.json` and `control-parity.json`. It produces zero candidate
outcomes. The input manifest binds complete source-window/canonical/graph
snapshots, original raw pairs, query and evidence hashes and exact original
historical execution/freeze identities. The 2,948 earlier negative rows are
immutable references, never new experiment outcomes.

After root review, commit unchanged source/tests/protocol/auditor and prospective
freeze artifacts before `--run`. Execution verifies their current and original
freeze Git blobs, strict ancestor chronology and unchanged runtime/inputs. An
exclusive `run-start.json` claims the attempt. All 786 controls must reproduce
again before `control-gate.json` permits the first new candidate call.

The run streams exactly 393 candidate rows to `replay.jsonl.gz`, flushing each
completed record before starting the next. Failures preserve the partial ledger,
attempted/completed counts and `failure.json`; an attempted directory cannot be
overwritten or resumed. Each trace captures the one actual planner/guard/coalesce
call, real zero-or-one factory count and actual self-reference object identity.
The CPU reference factory copies the already-reproduced same-input coverage
response; this makes no claim about native packing cost. Full source stages,
candidate rows, exact compact payloads, guard operands and individual outcomes
remain available for independent reconstruction.

`summary.json` reports both-control individual losses, new distinct complete
multi-file units, all changes, every original study/dataset/condition stratum and
actual planner/guard activity. `findings.md` links the decision to the unchanged
gate. Run `scripts/audit_complementary_context.py --output <results-directory>`
in a separate clean process before accepting any qualification claim. That
auditor imports no candidate, producer, backend, model or numerical runtime.
