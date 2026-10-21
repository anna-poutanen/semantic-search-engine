# Complementary implementation windows: development protocol

This is a prospective CPU replay of five fixed response-assembly conditions on
already observed retrieval orders. It changes neither models nor retrieval,
reranking, scoring, prompts, thresholds or context limits. The purpose is to test
whether complementary physical windows from the same actual implementation can
improve source-complete evidence within the existing budget. The fresh TanStack
Query dataset is excluded from this replay and may only validate a condition
chosen and frozen after development review.

## Fixed inputs and counting units

The runner reuses [replay_selection.py](../scripts/replay_selection.py) helpers
without modifying their historical loaders, literal legacy control, tokenizer
guard, offline import/network guard or evidence/change reporting. It adds the
first-repetition **baseline legacy** rows of the independently audited native
urllib3 study. All of these sources and outcomes are observed development data.

| Observed study subset | First-repetition rows |
|---|---:|
| Language-quality Express/Zod, original BM25/dense/hybrid | 90 |
| Native Express, dense/hybrid | 30 |
| Native Zod, dense/hybrid | 30 |
| Native Jinja, dense/hybrid | 28 |
| Qwen shortlist development, hybrid_k24_b1 | 144 |
| Qwen shortlist HTTPX, hybrid_k24_b1 | 15 |
| Native urllib3 legacy baseline, dense/hybrid | 28 |
| **Total** | **365** |

These rows share **187 distinct dataset/query units**. Conditions and repeated
studies reuse query units; 365 rows are not 365 independent queries. Quality
metrics and every change are stratified by study subset, dataset and original
retrieval/reranker condition. The Qwen rows replay the existing scored K=24
ranking, without adding an unscored retrieval tail. All other rows preserve
their recorded original candidate order and scalar scores.

The immutable input anchor is project commit
`5d1e80062638e19101acf3b9e8543066590fd1c5`. Every consumed historical audit,
manifest, plan, query dataset, source-content receipt, candidate pool and search
ledger must agree with its saved audit/plan hash and its committed bytes at that
anchor. Every pinned source file is read from its ignored local checkout,
checked against the source hash matrix, and parsed with the pinned CPU grammar.
Extraction must reproduce the historical complete inventory and canonical
definition mapping. The urllib3 addition also verifies its native source
inventory, complete definition-key hash, saved 50-candidate rows and source
metadata against the independent native audit. No source path may escape its
pinned corpus.

## Five conditions fixed before outcomes

Every condition uses **8 excerpts, 12,000 source characters and 4,096 exact
compact JSON tokens with cl100k_base**. Dependency expansion and source
membership remain the same; the new conditions are experiments on context
assembly, not automatic default changes.

1. `legacy`: the original display-label selector and unchanged postprocessor.
   Keep the first window for a path/symbol, take the original first five anchors,
   add up to two resolved static callees per anchor, and backfill the remaining
   eligible prefix up to eight.
2. `coverage_control`: the existing definition-aware selector plus the prior
   source-preserving coalescing/backfill postprocessor. It keeps the first window
   of each actual AST definition, suppresses ambiguous graph display-label
   expansions, removes actual delivered source wholly contained by another
   returned row, and tries bounded ranked backfill. This is the prior best
   **research** condition. Its previously observed two complete-query gains
   cannot count as new gains for this experiment.
3. `connected_union`: at each actual definition's first original rank, join its
   first-ranked representative with the first later raw ranked window from that
   same definition that adds source through consistent overlap or adjacency.
   Use one companion only. Preserve the representative's original scalar and
   rank, keep the original dependency allocation, run ordinary source/token
   packing, then run the same coverage postprocessor. Growing an early excerpt
   can remove later delivered evidence under unchanged caps. Those losses are
   part of the result and must remain visible.
4. `guarded_continuation`: use the definition selector and prior coverage
   postprocessor, then try to extend actual delivered retrieval prefixes forward
   with one connected raw ranked window of the same exact definition. There are
   at most eight eligible extension trials across the response. Trim only newly
   appended complete physical lines to fit remaining source/token capacity;
   never remove prior actual source. A clipped first window may extend through
   its own complete original ranked window. Dependency parent IDs change only
   when their original anchor becomes a verified composite.
5. `guarded_connected_union`: render the connected/coalesced proposal and a
   coverage-control reference from the same original pairs. Accept the proposal
   only if every actual source interval from the reference is literally
   contained by one actual proposal row with identical saved/buffer provenance.
   Otherwise return the reference unchanged, with only a guard diagnostic. This
   allows bidirectional connected unions without accepting context-budget
   crowding that drops reference source. The reference is obtained through a
   lazy rendering factory: it performs no new retrieval or model calls. CPU
   replay may reuse its already rendered coverage-control response; a later
   native benchmark must time the actual reference packing work. The condition
   was added before any freeze or new variant outcome, from source geometry and
   the source-preservation requirement rather than gold feedback.

Canonical definitions are AST identities, including the JavaScript/TypeScript
node offset where needed. Matching path/symbol labels, adjacent sibling
definitions or merely similar text do not establish a valid union. An unknown
definition never authorizes joining. No diversity heuristic, lexical query
rewrite, code summarization, invented source, gap filling or new scoring is
introduced.

## Source, score and budget receipts

A composite is transient response context; it does not change an indexed
Snippet, stored document, embedding or retrieval score. Its assembly receipt
contains both original member IDs, full source bounds/hashes, used physical
prefix bounds/hashes, the complete definition key, both original ranks, and the
original score-anchor ID. The new retrieval scalar is inherited from that
anchor. H2O/Qwen score fields are null: the assembled source was not newly judged.
Continuation also records its inherited prior score metadata.

The composite ID is `context:connected_windows:` followed by SHA-256 of UTF-8
canonical JSON containing kind, definition key, member descriptors sorted by
ID/used bounds/hash, union start/end and full union content hash. Canonical JSON
uses sorted keys, compact separators, no NaN and no extra newline. Score/rank
orientation does not change a literal union's identity. The row's `content_hash`
and `window_end_line` identify the full intended union; `source`, `end_line`,
`source_hash` and `truncated` truthfully identify the actual delivered prefix.

The replay verifies composites independently from pinned physical source and
original inventory members. It never calls the producer's join or identity
helper to check that helper's output. It checks exact definition membership,
connected bounds without gaps, overlapping bytes, original ranks and inherited
score, deterministic ID, full-union hash, actual clipped bytes and truthful
metadata. Dependencies pointing at a composite must resolve to its original
ranked score anchor and remain an allowed original static callee. All retained
old rows remain exactly equal through coalescing and forward continuation except
for a truthful verified composite parent-ID mapping. The guarded connected
condition may instead change truthful bounds/IDs, but must contain every actual
coverage-control source interval literally in one new row. Several disjoint
fragments do not satisfy either source-preservation guard.

Whole-line character clipping and exact compact token clipping remain the
original two-pass algorithm. Token counts include changed dependency parent IDs
and the self-reported count's fixed point. Assembly/debug receipts remain
outside compact MCP text, and do not make the returned source free. The exact
compact payload, its SHA-256 and independently recounted token total are saved
for each outcome.

## Freeze before any new variant

[replay_windows.py](../scripts/replay_windows.py) provides separate `--freeze`
and `--run` commands. Freeze is allowed to compute only immutable controls:

- All **365** literal legacy controls must exactly reproduce the saved compact
  payload and complete result metadata, source bytes/hashes, character totals,
  token usage and budget flags.
- All **337** saved prior-best `definition_coverage_backfill` responses from
  `selection-development-v2-2026-10-21` must exactly reproduce, including the full
  response, compact hash and delivered evidence ranks. This catches a changed
  comparison control before any new selector is evaluated.

The freeze emits `frozen-plan.json`, `baseline-gate.json`,
`prior-coverage-gate.json` and `source-fixtures.json`. The last file records every
fixture's full raw Snippet inventory, complete canonical definition mapping and
pinned source matrix before new outcomes. Its hash is bound by the plan and
checked again against reconstructed source during execution.

The plan binds the runner, tests, protocol, production selector/assembly/packing
modules, historical helpers, every input, runtime and source hash matrix. Commit
all those bytes and the four freeze artifacts unchanged before `--run`. The
runner rejects changed code, uncommitted bound files, altered runtime or inputs,
and any existing run/failure artifacts. It never overwrites or resumes an
attempt. A failed attempt remains evidence; do not delete it and reuse its path.

Execution first reconstructs the exact frozen source snapshot and both complete
control barriers. Only after writing `control-gate.json` with zero new variant
rows before the gate can it invoke any new condition. An attempted new
condition is counted before invocation, so a failure cannot disguise prior
activation. The import guard forbids model, index, embedding and numerical
runtimes, while socket operations are refused. The warmed exact cl100k rank
cache and pinned parser/tokenizer package versions are required; downloads are
forbidden.

Example, using a prepared offline environment and a new result directory:

```sh
python scripts/replay_windows.py --corpus-root /absolute/path/to/.cache/corpora \
  --output results/window-development-2026-10-21 --freeze
# Review and commit the unchanged implementation, tests, protocol and freeze.
python scripts/replay_windows.py --corpus-root /absolute/path/to/.cache/corpora \
  --output results/window-development-2026-10-21 --run
```

## Decision and retained evidence

Each new condition must satisfy the gate independently against **both**
`legacy` and `coverage_control`:

- Lose no previously covered complete evidence group in any observed row,
  including a group in a query that was already incomplete.
- Lose no baseline-complete positive query/condition row.
- Lose no single-file control's first complete evidence hit.
- Gain at least one **additional distinct complete dataset/query unit compared
  with coverage_control**. Repeating a gain in dense/hybrid or another study
  counts once, and the coverage control's previously known gains do not count.

Historical evidence semantics remain unchanged: OR within an evidence group,
AND across every required group, with the entire physical evidence interval
inside one actual returned excerpt. Symbol/ID presence alone never counts as
complete source. Gold query IDs, kinds, evidence and outcomes remain reporting
data and are not passed to selection or postprocessing.

Every gain and regression against both controls is retained, together with
study/dataset/retrieval strata, raw ranked pairs, selected and eligible pairs,
canonical definitions and origins, full backfill and raw-ranked rows, the
pre-postprocessor and final responses, exact compact text, assembly receipts
and absent-query inventories. The output `summary.json` records a separate
qualification for each new condition, including loss counts against both
controls and the list/count of additional distinct complete queries over the
coverage control. Only the guarded connected outcome additionally retains the
actual `reference_response` and post-coalescing `proposal_response`, captured
while delegating to the production source guard unchanged. An independent
auditor can verify both the decision and the exact same-input coverage
reference. Its `_window_guard`
diagnostic is excluded from compact text; production returns no second reference
payload. `findings.md` is a readable view of those complete results.

A qualified development condition still requires independent audit, a committed
native plan and prospective validation on the fresh repository. CPU replay
cannot establish native warm latency, coding correctness, provider token usage,
agent speed or generalization. If no fixed candidate qualifies, retain the
negative result and keep defaults unchanged.
