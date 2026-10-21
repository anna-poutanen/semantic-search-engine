# Response-selection development protocol

This is a prospective CPU development replay of already observed candidate orders. Express, Zod, Jinja and HTTPX are all observed for this work, including inputs originally called holdouts. No part of this replay is fresh holdout evidence. The selector cannot receive query IDs, benchmark labels, query kinds, target paths, evidence spans, or evidence ranks.

The fixed input matrix is:

| observed study | retained order | first-repetition rows | unique dataset/query units within study |
|---|---|---:|---:|
| language-quality-2026-10-13 | Express/Zod BM25, dense, hybrid top50 | 90 | 30 |
| multifile-express-2026-10-19 | dense/hybrid top50 | 30 | 15 |
| multifile-zod-2026-10-19 | dense/hybrid top50 | 30 | 15 |
| multifile-jinja-2026-10-19 | dense/hybrid top50 | 28 | 14 |
| qwen-shortlist-development-2026-10-14 | observed hybrid K24 batch1 scalar ranking | 144 | 144 |
| qwen-shortlist-httpx-2026-10-14 | observed hybrid K24 batch1 scalar ranking | 15 | 15 |

The 337 rows reuse queries across conditions and studies. Report the global distinct `(dataset, query_id)` count separately; do not call these 337 independent queries, use conditions to inflate the sample size, pool former holdouts into a new holdout, or calculate an independence-based significance claim. Keep results separate by original study, dataset and retrieval/ranker condition. The experimental multifile retrieval rows and later repetitions are excluded prospectively. No new retriever, embedding, reranker, model or GPU work is allowed.

All six conditions use at most eight excerpts, 12,000 actual delivered source characters and 4,096 exact compact JSON `cl100k_base` tokens. Source extraction remains 160 physical lines with 40-line overlap, using the pinned saved bytes and membership. The extraction inventory, snippet IDs, paths, line spans, text and content hashes must match the saved inventory. Definition identity is an ephemeral `window ID -> canonical AST definition key` map; it changes neither the snippet schema nor persistent index format. Windows from one definition share its key; different lexical definitions with the same displayed `(path, symbol)` retain separate keys. Unmapped windows fall back to their IDs, never a shared unknown label.

The six independent conditions are frozen together before any variant outcome is computed:

1. `legacy`: original first-ranked window per displayed `(path, symbol)`, eligible prefix of eight, first five anchors, up to two deterministic depth-one callees per anchor, then eligible ranked backfill to eight.
2. `definition`: canonical definition dedup with the original dependency allocation. The existing graph is keyed by displayed labels and can conflate separate definitions, so this condition suppresses dependency expansion from or to ambiguous `(path, symbol)` graph labels. That safeguard is part of definition identity and is reported as such.
3. `rank_preserving`: original display-label dedup, retain the first eight ranked definitions, append deterministic callees of the first five anchors only when fewer than eight ranked definitions leave spare slots.
4. `combined`: canonical definition dedup and the ambiguity safeguard, plus ranked capacity preservation and dependencies only in spare slots.
5. `legacy_coverage_backfill`: original legacy selection and packing followed by the bounded source-preserving postprocessor below. This isolates coverage removal/backfill from definition identity.
6. `definition_coverage_backfill`: definition selection with the original dependency allocation and packing, followed by the same postprocessor.

There are no additional scoring features, fitted thresholds, query-specific rules, retrieval calls or extra hyperparameter conditions. Every condition first uses the original two-pass packing: whole physical source lines clipped to the shared character allowance, followed by the exact compact JSON tokenizer cap. The first four conditions stop there. Selection and any declared postprocessing finish before labels are used to score delivered evidence.

Only the two coverage conditions then remove an original delivered row when its actual delivered literal source span is fully contained in another actually returned row. This includes an earlier child covered by a later parent, which the prior context-packing replay's later-prefix-only containment check could not remove. Equal bounds retain the first row. Each removed original span must remain contained by one actual retained row, with identical source bytes. Retained old rows remain exactly unchanged, including bounds, hashes, origin and provenance; old source is never trimmed to make room. The postprocessor may then append candidates from the full stable display-distinct or canonical-definition-distinct ranked list into spare slots, clipping only new candidates by whole physical lines under both remaining caps. It considers at most eight new noncovered candidate trials. Fully covered candidates spend no trial. No partial overlap trimming or rewriting is allowed. The replay independently verifies every original actual delivered source span remains covered after postprocessing, and verifies all final source/hash/token caps. A coverage condition that loses any original covered group is a failure.

`--freeze` verifies pinned input artifact hashes against the prior successful audit receipts, pinned corpus bytes, literal evidence hashes and extraction inventories. It reconstructs all 337 legacy controls with an independent literal implementation and requires exact compact strings plus complete result metadata, actual source bytes/hashes, character totals, token counts and flags. It writes only a plan and baseline gate; it does not compute the observed variant outcomes. Any mismatch aborts before a freeze is written.

Review and commit the script, synthetic tests, this protocol, all bound source modules, `frozen-plan.json` and `baseline-gate.json` before `--run`. The plan binds the code, protocol, tests, input artifacts, corpus file hashes, package versions, tokenizer bytes, matrix, budgets and selector functions. The run refuses a changed/uncommitted bound file and an output directory with prior run or failure artifacts. Recheck every original baseline across all inputs before computing or publishing any variant. A failed attempt retains its failure receipt and cannot be resumed or overwritten. Any code or configuration repair needs a new freeze directory and commit.

Reporting scores OR alternatives within each evidence group and AND across all required groups. A group is covered only when one actual delivered excerpt contains the entire pinned executable evidence span. Combining incomplete fragments, a matching symbol name, candidate eligibility, full untruncated window bounds, or a first hit cannot establish complete evidence. Check every delivered excerpt against exact source bytes, inclusive physical line bounds and SHA256, and independently recount its compact tokens after packing. All complete-query gains/losses and every full-group gain/loss remain visible, including dependency-only evidence losses and losses on queries that were already incomplete. Keep absent-query statuses and excerpt inventories.

A variant qualifies only when every observed row retains every baseline-covered full group and every baseline-complete positive, no single-file first hit is lost, and at least one additional distinct dataset/query is complete in an observed condition. Report query IDs separately from condition rows so duplicated gains cannot inflate the unit count. Report all five variants independently, including when none qualifies; do not select or change a default automatically. Compare definition coverage/backfill with both legacy and the legacy coverage control. This gate identifies further development candidates, not generalized superiority.

Execution uses a clean offline CPU process, pinned parser/tokenizer versions and verified local tokenizer bytes. Imports of model, numerical, index, backend and reranker runtimes and network connections are blocked. Read saved scalar scores and ranked source identities only. No changes to original study artifacts or old replay scripts are allowed. Literal source-complete coverage is not coding correctness, provider billing, latency, memory or agent token savings.

Commands after the implementation has been reviewed and synthetic tests pass:

```sh
/Users/annapoutanen/personal-projects/semantic-search-engine/.venv/bin/python scripts/replay_selection.py --corpus-root /Users/annapoutanen/personal-projects/semantic-search-engine/.cache/corpora --output results/selection-development-2026-10-21 --freeze
# Commit all bound producer files and the two new prospective freeze artifacts.
/Users/annapoutanen/personal-projects/semantic-search-engine/.venv/bin/python scripts/replay_selection.py --corpus-root /Users/annapoutanen/personal-projects/semantic-search-engine/.cache/corpora --output results/selection-development-2026-10-21 --run
```
