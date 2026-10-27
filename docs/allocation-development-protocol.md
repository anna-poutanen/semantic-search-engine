# Complementary implementation allocation: development protocol

This prospectively fixed CPU experiment allocates the existing eight context
slots among different implementation definitions. It follows the negative
same-definition window experiment. It changes no model, retrieval candidate
order, reranker shortlist, score, source window, context limit or default.
The fresh TanStack Query dataset remains excluded until a candidate qualifies,
is independently audited and is frozen for a separate native experiment.

## Observed inputs and immutable controls

The study reuses **365 observed first-repetition rows**, sharing **187 distinct
dataset/query units**: 90 language-quality Express/Zod rows; 30 native Express,
30 native Zod and 28 native Jinja rows; 144 Qwen development and 15 Qwen HTTPX
rows using the previously scored `hybrid_k24_b1` order; and 28 native urllib3
legacy baseline rows. These are observed development data, including every
former holdout. Shared query/retrieval rows are not independent observations.

`scripts/replay_allocation.py` imports the existing CPU loader, physical-source
packing, offline tokenizer guards and reporting helpers from
`scripts/replay_windows.py`. A scoped profile sets the four conditions below
only for the new call. The historical scripts, ledgers, plans and audit receipts
remain unchanged. Original raw orders and finite scalar scores are preserved;
the Qwen order gains no unscored retrieval tail. Complete source files,
extraction inventories, canonical AST definitions and graph identities must
reproduce the pinned original receipts before evaluation.

The immutable original input anchor remains
`5d1e80062638e19101acf3b9e8543066590fd1c5`. The immediately preceding observed
window study is `results/window-development-2026-10-21`. Its original execution
was `387b652d613fd548401ef5933df67a1742dda596`; its saved independent audit is
retained as historical proof. Current Git history was rewritten after that run.
The new provenance receipt verifies every original producer, input and frozen
plan/gate blob against the **original** freeze and execution commits, verifies
the original auditor's committed source hashes, and checks that the original
freeze preceded its recorded run. It records whether the old execution is an
ancestor of current history without requiring that relationship. No replacement
commit is attributed to the old execution and no old producer source is run.

All consumed historical artifacts must still match their original independent
audit hashes and current committed bytes. The new study separately requires its
own prospective freeze to be committed before execution and to be an ancestor of
its own execution. A historical rewrite never relaxes these new-run conditions.

## Four conditions fixed before new outcomes

Every arm uses **8 excerpts, 12,000 source characters and 4,096 exact compact
JSON cl100k tokens**, with the same dependency expansion and physical-line
prefix packing.

1. `legacy`: the original display-label selection and unchanged postprocessing.
2. `coverage_control`: the existing canonical-definition selection followed by
   source-preserving covered-row removal and bounded ranked backfill. Its
   previously observed gains are already known and do not count as new gains.
3. `aspect_fill`: for queries accepted by the existing literal 2–4-clause
   detector, protect the first five canonical retrieval anchors, every original
   dependency, and every original dependency owner. Retain protected rows in
   baseline order. Fill the remaining **original eligible count** by source/query
   term novelty. The complete original selected list remains in raw order for
   the same postprocessor.
4. `aspect_balanced`: use the same rule and guards, protecting the first three
   anchors rather than five. Every original dependency and its owner remains
   protected, even when that leaves no exchangeable slots.

For each candidate, terms come from the existing normalized tokenization of its
literal path, qualified symbol and complete original source window, intersected
with normalized query terms. Query term document frequency counts distinct
canonical definitions across **all** original raw windows, including later
windows of the same definition. A proposed candidate's novel terms are terms
not yet represented by protected and already chosen rows. Their exact rational
gain is the sum of `1 / definition_frequency`; utility divides this gain by the
candidate's original raw rank. Choose greatest positive utility, breaking ties
by raw rank; once no positive utility remains, fill in original rank order.
The scored representative remains the first raw window of each definition.

The proposal must preserve every query term represented by the original
eligible selection. Otherwise return the exact original definition control.
Also return the control for inactive clause detection, no free slots, no novel
query terms or an exhausted candidate pool. Term preservation is only a lexical
guard: it does not establish implementation or evidence preservation. The
benchmark must retain and reject actual evidence regressions independently.

No file-diversity bonus, query rewrite, summary, invented source, new model score
or new retrieval is introduced. Returned original snippets, scores, dependency
owners, source hashes and qualified definitions remain verifiable. The planner
receives no gold IDs, query IDs, evidence intervals, query kinds or benchmark
outcomes.

## Prospective freeze and trace receipts

Freeze recomputes only the **365 original literal legacy controls** and the
**337 previously observed coverage controls**. It binds source/tests/protocol,
CPU runtime, complete source matrix, original artifact hashes, explicit original
Git blob provenance, and immutable parent control references. The new
`source-fixtures.json` is a hash-bound pointer to the original complete 7 MB
source inventory. The previous 97 MB five-arm matrix is not copied.

`control-references.json` identifies both original controls for each case by
one-based line number in the original gzip ledger, SHA-256 of sorted-key compact
UTF-8 JSON without a newline, full response hash and exact compact payload hash.
`historical-provenance.json` preserves the original producer/auditor commit IDs
and all verified blob bindings. Commit all prospective source, tests, protocol,
plan, pointers and gates unchanged before `--run`.

Run first repeats the 365/337 barriers, then reproduces the actual **730 parent
control arms**, requiring identical full returned rows and compact payloads,
before evaluating either new arm. The new gzip ledger stores only **730 new
candidate traces**. Each references both immutable controls and retains raw
ranked, canonical selected and eligible source/score stages, dependency origins,
definition keys, pre/postprocessing responses, backfill candidates, exact
compact JSON and token count, gold reporting changes, and the actual pure
planner receipt captured from its one execution. The receipt records clause
activation, term features/frequencies, protected IDs, rational gains/utilities,
chosen IDs, proposal terms and every fallback reason. It stays outside the
public compact response.

All calls run under the existing offline import/network guard. No model,
numerical runtime, index, retrieval, reranking, GPU or network work is allowed.
Each attempt has exclusive output files. Preserve failures and partial traces;
never resume or overwrite an attempted run. A repaired attempt requires a new
freeze, reviewed bindings and output directory.

## Qualification and independent validation

Report metrics and every group/complete-query change by study, dataset and
original retrieval/reranker condition. Historical development retains its
path plus complete physical evidence interval scoring; do not pool it with the
stricter qualified-symbol/hash scoring planned for fresh TanStack validation.

Qualification requires **zero lost full evidence groups, complete positive rows
or single-file first hits against both controls**, plus at least **one additional
distinct complete dataset/query unit against `coverage_control`**. Partial-query
group losses count. Previously known coverage gains cannot qualify a new arm.
An absent scoped query has no positive labels and remains part of the report.

An independent committed CPU auditor must reconstruct original raw candidates,
source/definition inventories, literal compact payloads and exact token caps;
verify the original record references and new prospective execution chain;
recompute planner features, protections, rational choices and guards without
importing the planner or producer; and independently reproduce every metric,
gain, regression and qualification. Do not run fresh inference for an
unqualified candidate. No automatic production default or release follows this
experiment. Source-evidence coverage does not establish coding correctness,
native warm latency, billing tokens, user benefit or generalization.

```sh
PYTHONPATH=src .venv/bin/python scripts/replay_allocation.py \
  --corpus-root .cache/corpora --output results/CHOOSE-NEW-ATTEMPT --freeze
# Commit all unchanged prospective bindings and generated freeze artifacts.
PYTHONPATH=src .venv/bin/python scripts/replay_allocation.py \
  --corpus-root .cache/corpora --output results/CHOOSE-NEW-ATTEMPT --run
```

Run from the source checkout; point `--corpus-root` and the Python executable to
the actual pinned local installations when using an isolated worktree.
