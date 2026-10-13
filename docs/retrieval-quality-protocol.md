# Frozen retrieval quality stage

[quality_manifest.json](../benchmarks/quality_manifest.json) records the exact datasets, SHA-256 hashes, upstream commits, source roots, ignored corpus locations, and licenses. The set contains **106 developer-authored, source-reviewed behavior queries**: 88 present behaviors and 18 absent features across five Python repositories. Ten present queries require multiple implementation groups from different files. It expands the original pilots while reserving one entire repository for the final quality check.

| Repository | Role | Present / absent | Multi-file AND cases |
|---|---|---|---|
| Flask | Existing development pilot | 12 / 2 | 0 |
| Requests | Existing source-reviewed pilot, now development evidence | 16 / 4 | 0 |
| Click 8.1.8 | Development and calibration | 20 / 4 | 3 |
| Starlette 0.45.3 | Development and calibration | 20 / 4 | 3 |
| Rich 13.9.4 | Repository-level holdout | 20 / 4 | 4 |

The original Flask and Requests files remain unchanged. Requests retains its original query-level `test` field, but its already observed outputs must not be presented as a fresh repository holdout. Rich's dataset-level `benchmark_role` and every query's `split` are `repository_holdout`. Click and Starlette have dataset-level role `development`; each divides its 20 present queries into 10 `development` and 10 `calibration` queries by alternating source-review order, with two absent queries per split. That supplies 20 present and four absent calibration queries across the two repositories. These assignments were made before their retrieval outputs.

This is an authored sample, not a representative workload or a claim that any repository was absent from model pretraining. The labels were made by reading source; repository holdout here means exclusion from retrieval/scoring/abstention choices before the final evaluation, not human blindness to source. Twenty positive cases in one held-out repository cannot establish broad generalization.

## Source labels and multi-file semantics

Every present target names a repository-relative `path`, an AST-qualified executable `symbol`, and an inclusive one-based `evidence_lines` interval. Source review checks that these intervals demonstrate the requested behavior and that extraction at **160 lines with 40-line overlap** produces a source-faithful window containing each full interval. The pinned new corpora produce 474 Click, 472 Starlette, and 873 Rich windows. Relevant upstream tests were read where available, but were not executed during authoring. Independent review occurs before model evaluation; these extraction checks perform no ranking or inference.

Existing `targets` retains its OR meaning: one delivered window containing any target's complete evidence interval satisfies the query. New compound queries instead use `target_groups`:

```json
{
  "kind": "present",
  "target_groups": [
    [{"path": "package/first.py", "symbol": "First.operation", "evidence_lines": [20, 30]}],
    [{"path": "package/second.py", "symbol": "second_operation", "evidence_lines": [40, 45]}]
  ]
}
```

Targets within one group are OR alternatives; **every group is required**. Current compound labels use singleton groups for separate implementations, rather than enumerating support-only helpers. A partial group match is not a whole-query hit. A query uses either `targets` or `target_groups`, never both. Absent queries use `targets: []`; they are scoped to the configured source root, excluding dependencies, external extensions, tests/examples, and application-specific code. Notes explain nearby but insufficient behavior.

Delivered evidence must match the target's path/symbol and contain the entire interval in the actual returned excerpt. A lower-ranked or deduplicated-away window cannot satisfy the hit; neither can an interval lost to token or source truncation. Location/hash metadata verifies source fidelity but does not substitute for executable source. Gold targets, groups, intervals, notes, and split labels must never enter candidate retrieval or H2O prompts.

## Freeze and evaluation order

1. Verify dataset hashes and checkout commits from the manifest, and review labels against pinned source without examining rankings. The current license copies and copyright notices are in [CORPUS_NOTICES.md](../benchmarks/CORPUS_NOTICES.md).
2. Save `frozen-config.json` before any new development model inference, recording model revisions, query/document formatting, extraction, retrieval modes/depth, candidate and result limits, deduplication, tokenizer and token-budget packing, timeout/cancellation policy, and the abstention rule family. The predeclared conditions and policy below are fixed before Rich retrieval.
3. Exercise implementation on development queries and calibrate rejection only on the declared calibration subset. Save the chosen threshold, calibration outcomes, configuration, dataset/source hashes, and selection rule as a concrete artifact before evaluating Rich. Four negative calibration examples are a small diagnostic, not enough to justify a universal safe rejection boundary.
4. Run the frozen conditions on Rich once. Do not use its positives or negatives to choose a threshold, prompt, shortlist, budget, or fusion setting and then describe those same results as held out. Any subsequent Rich-guided change turns it into development evidence and needs a fresh holdout for an independent follow-up.
5. Report development/calibration/holdout separately, and show raw query-level evidence and failures alongside aggregates. Preserve inference/latency provenance and corpus licensing when distributing artifacts.

The first saved Click/Starlette drafts received four source-based wording qualifications during independent review before inference: missing paths are rejected only when existence is required; a cached Starlette body is replayed before the consumed-stream guard; WebSocket OSError conversion applies while connected; and the Range parser merges only the first intersecting collected interval rather than guaranteeing complete transitive coalescing. Their gold targets and evidence ranges did not change. The manifest hashes reflect the clarified wording. Later label corrections require a new hash and reason; they must be reported separately from model failures.

## Predeclared methods and abstention

The first comparison uses SQLite FTS5 lexical retrieval, Qwen dense retrieval from SQLite vectors, and equal-weight FTS5/dense RRF with constant 60 and input depth 50. Each retrieves at most 50 windows, deduplicates by exact path/qualified symbol while retaining the first ranked window, and returns the first five distinct symbols available within that depth. A fourth `dense_context` condition takes the first five dense anchors, adds at most two explicit statically resolved callees per anchor, and returns at most eight results. If callees do not fill the remaining slots, it fills them with subsequent ranked anchors. This behavior is included in the frozen implementation hashes; any gain over dense-five can come from the additional anchor slots as well as dependencies. Expansion must follow source references without gold-label injection. H2O is not part of these four conditions; earlier H2O experiments remain separate evidence.

Delivered context has both a **12,000 source-character limit** and **4,096 text tokens measured with `cl100k_base`**. Pin the tokenizer/runtime and pack against the exact serialized context. Candidate coverage is diagnosed separately using the first 50 full windows from the same per-request query vector. Every fresh dense query recomputes its embedding; no cross-request query embedding cache is allowed.

For the predeclared abstention rule, calibrate only the Click/Starlette rows labeled `calibration` (20 present, four absent). Set a cosine threshold to the minimum positive calibration score so all positive calibration queries are retained; reject a query only when its score is strictly below that threshold. Save the precise score definition, threshold, selected rows, and source/configuration hashes before any Rich retrieval. The rule is a ranking-based rejection heuristic, not proof that a requested behavior is absent; its negative calibration results must be reported.

Report Rich outcomes both without abstention and under the frozen policy. Derived policy outcomes can reuse identical query results only when identified as such; fresh timing for the configured context/policy variant requires actual searches with that policy applied. Do not infer fresh request latency from cached result filtering.

## Measurements

Report present-query hit@1/@3/@5, reciprocal rank, and candidate coverage separately from **complete delivered evidence coverage**. For a compound query, hit@k means every required group is evidenced within the first k delivered results; reciprocal rank is based on the earliest prefix that completes all groups. Also report group coverage and compound-query success separately so one recovered file does not mask a missing implementation. Show unbudgeted candidate coverage and retained coverage after ranking, deduplication, result limits, abstention, and source/token packing.

Compare declared token budgets using a named, pinned tokenizer on the exact serialized context a consumer receives, including its metadata and formatting. Report actual delivered tokens, source characters, truncation/omission, and complete evidence coverage. Character budgets are not token budgets. Abstention counts need both present-query false rejection and absent-query false acceptance; non-abstained accuracy alone can improve by rejecting difficult positives. The 18 absent cases stay out of positive accuracy denominators.

Measure startup/indexing separately from warmed resident searches. Fresh latency measurements must recompute query embeddings and every H2O call rather than replay judgment timings; exact-input quality reuse must be labeled and traced to its original run. Preserve failures, timeouts, cancellations, returned sources/hashes, and actual wall-clock samples. Follow the [shortlist protocol](shortlist-protocol.md) for scored-prefix-only reranking and fixed RRF behavior. Do not claim paid-agent token savings solely from local H2O usage counts or context packing; that would need a measured consumer workflow.
