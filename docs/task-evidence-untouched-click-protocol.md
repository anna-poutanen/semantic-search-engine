# Task-evidence untouched Click child-freeze protocol

This protocol freezes one prospective packet-only guardrail for the task-evidence
selector: the already selected Click `click_atomic_transactions` task. It asks
whether the frozen treatment admits at least one complementary implementation or
caller group from a newly covered production source file without an individual
evidence loss against the frozen legacy control on a separately selected observed
codebase. It is not a coding-agent trial, a fresh repository holdout, or evidence of
general token savings.

The harness-visible contract is
`benchmarks/task_evidence_untouched_click.json`. Evaluator-only checklist details
remain outside that manifest and outside both planner requests. In particular, the
public manifest contains no evaluator group ID, target path, non-prompt target
symbol, line-span metadata, evidence hash, or evaluator outcome.

## Public manifest schema

The public manifest has exactly these top-level fields:

```text
version
phase
classification
parent_freeze
selection_lock
task
corpus
index_scope
artifact_kinds
gap_contract
common_request
conditions
planner_boundary
claim_boundary
schedule
gates
calls_at_scope_freeze
```

`parent_freeze` and `selection_lock` bind the prospective ancestry. `task`,
`corpus`, and `index_scope` bind public source inputs. `common_request`,
`conditions`, and `planner_boundary` bind the only payloads the harness may resolve
for the planner. `gap_contract` and `gates` bind public reporting and admission
semantics. `schedule` contains both the three-pair packet gate and the later
execution-freeze prerequisite; execution and substitution rules are not competing
top-level contracts. `claim_boundary` is byte-semantically identical to the
committed selection lock's claim boundary. `calls_at_scope_freeze` contains exact
zeroes for model, embedding, index, retrieval, planner, selector, packing, and
network calls.

## Prospective chronology

The order is part of the result, not background metadata:

| milestone | frozen state |
|---|---|
| Requests Phase 0 anchor | commit `5b8c02ee53f541b6bd31587ec3385b1beddfa512` |
| untouched-case selection | commit `a41d07742d0648dcee62cdd3d00d5996f52a1162` |
| selection-lock bytes | `benchmarks/task_evidence_untouched_selection.json`, SHA256 `55aa7fde326a5783e528caaa327443c3be8202000f5cb30d24021509ee886fed` |
| Click child source freeze | must descend from the two commits above and be committed before selector implementation, Requests packet outcomes, or any Click retrieval, selection, packing, or packet outcome |
| Requests execution | the frozen Requests packet gate and native-latency gate must both pass |
| Click execution freeze | a separate later commit, created only after those Requests gates pass and before either Click condition is selected or packed |
| Click admission | one named validation attempt containing the frozen three-pair schedule and three executions per condition from the execution freeze's shared candidate capture |

The selection lock chose Click by manifest order before candidate-source review. The
source-only work needed to author this child freeze happens after that committed
selection, but before selector implementation or an observed Requests or Click
packet outcome. At this child-freeze point there have been zero model, embedding,
index, retrieval, planner, selection, packing, network, and coding-agent calls for
the Click packet study.

This is outcome-unobserved rather than source-blind. The Click task, source, and
evaluator checklist were reviewed to create this freeze before the selector exists.
The later execution freeze must therefore audit corpus neutrality: Requests and
Click use the same selector and packer source, with no literal Click, task, or gold
IDs, paths, symbols, hashes, or conditionals keyed to them. Generic features derived
uniformly from request and candidate data remain allowed.

The dedicated output path is
`results/task-evidence-untouched-click`. It must not exist when the child source
freeze is validated or committed. It may be created only after the separate Click
execution freeze has been committed. Candidate-capture material needed to create
the execution freeze must therefore be bound by that freeze outside this result
directory; no early result directory may be used as a staging area.

## Frozen task and source scope

| field | frozen value |
|---|---|
| task | `click_atomic_transactions` |
| repository | `https://github.com/pallets/click` |
| revision | `934813e4d421071a1b3db3973c02fe2721359a6e` |
| public prompt SHA256 | `55d3806f38b3002081ff44cb8a58f8fccc2dc1ec9f9326682b35d21a1d710feb` |
| source root | `.` |
| membership | Git-tracked supported saved-source files |
| extraction | `saved-implementations-v3`, 160-line windows with 40-line overlap |

The exact normalized source exclusions are:

```json
[
  "docs/*.py",
  "examples/*.py"
]
```

Runtime exclusions retain the current `fnmatchcase` behavior, so
`examples/*.py` also matches nested Python example paths. Classification globs are
separate, anchored, segment-aware globs: 16 included files under `src/click/` are
implementation, and 29 included files under `tests/` are tests. Every included
supported file belongs to exactly one of those two classes.

Both arms intentionally override the original workflow manifests' `src/click`
source root with repository root so existing tests are eligible. This is not a
retroactive change to the original workflow result: every claim from this child
validation applies only to the root-scoped corpus and exclusions frozen here.

The frozen source-only inventory is:

| measure | value |
|---|---:|
| eligible supported files | 70 |
| excluded supported files | 25 |
| included and parsed files | 45 |
| parsed files emitting no window | 2 |
| implementation windows | 474 |
| test windows | 597 |
| total windows | 1,071 |

| canonical inventory | SHA256 |
|---|---|
| included files | `e3130b2f1d298dc884e9c1d26adf3690add8b9f0e0cf6ff612b97f60d3387b4f` |
| implementation files | `e841818743a119a18e822e9d132d39f6d9793c513ffe0fc639236b24d7b4a6b0` |
| test files | `9c8f1dff5b8d45dfff8b6976785d6a2ed0151e5f2e2d8068ced554dfec6542ce` |
| excluded files | `45516743a9112e034be69b5150212f6b565b6c72f666ab794b7b2f2b3b28e471` |
| extraction windows | `5f6cf093da973ca62d84c86d2dfcbf5c26dfdd793126430a55693b1d9475fd83` |

File-inventory hashes are over canonical compact sorted JSON mapping repository-
relative paths to raw-file SHA256. The window hash uses the same serialization for
snippet IDs mapped to source-content hashes.

Root scope does not imply that every tracked artifact is searchable. Unsupported
file kinds, policy exclusions, and supported files that emit no implementation
window remain explicit gaps.

## Exact paired requests and planner boundary

Both conditions use these settings:

```json
{
  "retrieval": "dense",
  "rerank": false,
  "rerank_provider": "h2o",
  "candidates": 50,
  "max_results": 8,
  "source_budget": 12000,
  "token_budget": 4096,
  "include_dependencies": true,
  "overlays": []
}
```

The request query is the exact public task prompt in the manifest. The control
omits `needed_evidence`. The treatment adds only:

```json
{
  "needed_evidence": [
    "implementation",
    "callers",
    "tests",
    "behavioral_constraints"
  ]
}
```

Under canonical compact sorted JSON, UTF-8, and no NaN, the complete resolved
control request has SHA256
`2f21bcfd7ba0cc35d413d4e3ee7a2b5a345b34b78c28f831970f4cf8e38478cb`;
the complete treatment request has SHA256
`c63735e89521f2de9c95e45cf36a45a591db8bdfcad203de51ed97625c49e42f`.
The two payloads differ only by `needed_evidence`.

The planner receives one exact resolved request plus the execution freeze's common
candidate and relation inventories. It does not receive this harness manifest, the
task ID, the source inventory, chronology, admission rule, evaluator group IDs,
target paths, non-prompt target symbols, line-span metadata, source or evidence
hashes, expected ranks, checker outcomes, or any prior condition outcome. A symbol
already present in the public task prompt is not evaluator leakage merely because
the checklist also names it. The execution freeze and harness must enumerate and
audit every selection and packing input; the evaluator checklist itself remains
unavailable. Gold scoring begins only after all six scheduled packets have
completed final packing.

Hashes for this new manifest, this protocol, the evaluator-only checklist, the
validator, tests, and the other child-freeze artifacts are deliberately absent from
the public manifest. A separate child binding must name and hash those artifacts.

## Evidence statuses and truthful gaps

Public evidence statuses are only `candidate`, `partial`, and `unresolved`.
`candidate` means a plausible final packed excerpt was linked to a requested need;
it is not a completeness claim. Literal checklist coverage is evaluator-only.

The frozen procedural reason vocabulary is:

- `unsupported_source_kind`
- `unsupported_artifact_kind`
- `no_extractable_window`
- `outside_indexed_scope`
- `not_retrieved`
- `selection_excluded`
- `relation_unresolved`
- `result_limit_exhausted`
- `source_budget_exhausted`
- `token_budget_exhausted`

The causes must not be collapsed. A configuration, documentation, or fixture file
kind with no registered extractor is `unsupported_source_kind`; the engine must not
claim that the artifact or behavior is absent. A supported Python path deliberately
matched by either source exclusion is `outside_indexed_scope`, not an unsupported
source kind. An included supported file that parses but emits no window is
`no_extractable_window`. Required module-, class-, assignment-, or annotation-only
structure not covered by an emitted implementation window is
`unsupported_artifact_kind`.

Retrieval, unresolved relations, selector decisions, and each of the three budgets
retain their own reasons. A public gap does not disclose an evaluator-only target,
and the evaluator's later reconciliation never becomes planner input.

## Later execution freeze

The child source freeze binds the task, source membership, extraction inventory,
request payloads, evidence/gap semantics, and schedule. It does not bind a runtime
that has not yet been implemented. After both Requests gates pass, a separate Click
execution freeze must name the Requests anchor, the Click selection lock, and the
committed child source-freeze anchor, then bind:

- embedding identity and query-vector bytes;
- exact index, tokenizer, extraction, and relation inventories;
- selector and packer fingerprints;
- both resolved requests and condition settings;
- the shared ordered dense top-50 candidate IDs and scores;
- runtime versions and the fixed three-pair admission evaluator.

The execution-freeze preparation may construct the common candidate capture. It may
not run a condition's selector or packer, inspect a Click packet outcome, create the
dedicated result directory, or tune any rule from Click. Any change to a bound input
requires a new named execution freeze before an outcome is observed.

That freeze must also bind the exact response normalizer source and SHA256 before
the attempt. Normalized equality is canonical compact sorted JSON of the complete
compact response after removing only the timing and request-identifier fields named
prospectively by the freeze. Selected-row order and identities, excerpts, evidence
statuses and gaps, budgets, source hashes, and relation provenance remain. Every
raw payload and its hash is retained, so normalization cannot erase a failed replay
after the fact.

The execution freeze must name one unique attempt ID, prove the dedicated result
path absent through its own commit, and bind an append-only start and finish-or-
failure receipt schema. Starting any local or automated condition run consumes that
attempt. Raw and normalized payloads, infrastructure failures, and gate failures are
retained; there is no discarded run, retry, resume, or replacement.

The reserved execution binding path is
`benchmarks/task_evidence_gold/untouched_click_execution_freeze.json`. Its
`child_handoff` must bind the committed child-freeze revision and exact child
binding hash, both Requests packet and native-latency gate receipt hashes, the
unique attempt ID, the dedicated result path, and that path's absence at the
execution-freeze commit. The child validator permits a later result path only when
this valid committed handoff precedes every result-path touch; the separate
execution validator remains responsible for the full runtime, neutrality,
normalizer, candidate-capture, and receipt contract.

## One named admission attempt

Only after the Requests packet and native-latency gates pass and the Click execution
freeze is committed may the harness create
`results/task-evidence-untouched-click` and execute one named locked validation
attempt. That attempt contains three paired replays in this exact order:

1. `legacy_control`, then `task_evidence`;
2. `task_evidence`, then `legacy_control`;
3. `legacy_control`, then `task_evidence`.

Each condition therefore executes three times from the same shared retrieval
capture. All six packets are finalized before evaluator-only scoring, and all three
selected-row sequences and normalized payloads for a condition must be identical.
The internal replays are part of the one predeclared attempt. There is no second
attempt, retry, tuning pass, substitution, resume, or discarded failure.

Admission requires all of the following:

- treatment retains every evaluator-predeclared evidence group completely covered
  by control, with the control excerpts establishing that coverage preserved
  byte-for-byte;
- treatment adds at least one evaluator-predeclared complete indexed implementation
  or caller group from a newly covered production source file, meaning a path absent
  from the control's covered required groups;
- the control compact response is byte-for-byte equal to the unchanged legacy path;
- both packets satisfy the 8-result, 12,000-source-character, and 4,096-token caps,
  and every delivered excerpt matches frozen Click source bytes;
- every requested public need has a non-overclaiming status or gap and every missing
  evaluator group has one factual pipeline cause; and
- no evaluator-only input crosses into either request, selection, or packing.

The three executions per condition establish the narrow deterministic-replay gate
for this frozen input under the prospectively bound normalizer. They do not
authorize a second validation attempt.
Infrastructure failure, a missing receipt, no qualifying complementary gain, or any
individual loss fails the locked admission and remains in the record.

No Click coding-agent conversation, patch, test execution, or agent-result grading
is permitted in this validation. Its coding-agent trial count is exactly zero.

## No fallback and claim boundary

Click is the only admitted child case. If no complementary candidate exists, a
packet cannot fit, evidence is lost, a gap is false, or execution fails, record that
failure. Do not substitute Starlette or another task. Replacement requires a new
selection lock and child freeze committed prospectively before inspecting replacement
source or outcomes.

The inherited selection-lock phrase “untouched by selector tuning” is used narrowly:
there has been no Click selector execution, packet outcome, or Click-based parameter
adjustment at the time fixed by the selection and child-freeze chronology. It does
not claim that the selector designer was blind to the reviewed Click source and gold
or prove that generic rule design was uninfluenced by them. Click remains an observed
development repository with existing task, checker, and prior workflow artifacts;
it is not a fresh project-wide holdout.

A pass supports only the selection-lock statement: a separately selected observed
codebase did not lose frozen evidence under the task-evidence selector. The receipt
may also report whether the separately required complementary-gain gate passed, but
it does not establish unseen-repository or JavaScript/TypeScript generalization,
Click coding-agent correctness or productivity, or general agent-token savings.
