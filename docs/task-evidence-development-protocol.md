# Task-evidence Phase 0 development protocol

This is a prospective, narrow development study on the already observed Requests
`requests_response_limit` task. It asks whether task-aware evidence selection can
admit complementary code that the existing selector or packer omits, while
preserving useful evidence and the same retrieval and context limits. The task,
repository, reference implementation, and checks have all been inspected during
development. This is not a fresh holdout, and a successful result is not evidence
of general agent-token savings.

Phase 0 is an experiment, not a new product interface. It changes no CLI, HTTP,
MCP, VS Code, index, retrieval, or default runtime behavior. Interface work is a
separate decision after this protocol's gates are met.

## Frozen task and indexed scope

The task source is the exact `requests_response_limit` entry in
`benchmarks/coding_workflows_multifile.json`:

| field | frozen value |
|---|---|
| repository | `https://github.com/psf/requests` |
| revision | `611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60` |
| public prompt SHA256 | `986ec59cc684a47862967a146108795d8a9a5950da0dc893779936c70fb04b3a` |
| source root for this study | `.` |
| tracked membership | the pinned revision's Git-tracked files |

The study intentionally overrides the older workflow manifest's
`src/requests` source root with `.` in both conditions. This makes the tracked
Python tests eligible for indexing. It does not silently make documentation or
configuration searchable when their extensions or source structures are unsupported
or explicitly excluded below.

The exact normalized `source_exclusions` are:

```json
[
  "docs/*.py",
  "setup.py"
]
```

There is no exclusion for `tests/**`. The two exclusions keep Python documentation
helpers and `setup.py` out of this implementation-and-tests packet. The freeze
records the included-file inventory, file hashes, extractor fingerprint, and
snippet inventory; the later execution freeze must additionally bind the relation,
dense-index, and ordered dense top-50 candidate inventories. Markdown, TOML, INI,
YAML, data files, and definition-bearing source regions not emitted by the current
extractor remain honest non-indexable evidence. Root scope must not be reported as
proof that those artifacts were searched.

Preliminary development inspection found required information in module-, class-,
and type-only regions for which the current function-window extraction can provide
no complete excerpt. Those requirements remain in evaluator gold. They may not be
deleted, weakened, or relabelled as irrelevant merely to make the packet score
complete.

The predeclared key-evidence checklist has 17 indexed singleton groups and four
structurally unsupported singleton groups. It is a stable scoring target for this
narrow study, not a claim that every useful code or test region has been enumerated.
The indexed groups require 16 distinct original extraction windows because two
redirect groups share one method window. Sixteen is already greater than the
eight-result cap. The full original windows total 25,272 source characters, also
above the source cap, although later line-preserving clipping means that figure is a
diagnostic rather than a minimum lower bound. No token lower bound is needed for the
already conclusive result-cap proof; exact compact token accounting remains an
execution gate. This is a feasibility statement about the predeclared indexable
key-evidence set; the four structurally unsupported groups are reported separately
and are not misclassified as packing failures.

## Harness manifest, planner input, and evaluator-only gold

`benchmarks/task_evidence_requests.json` is visible to the research harness, not
to the planner. The harness resolves and serializes one exact backend request per
condition. The treatment request is:

```json
{
  "query": "<the exact public prompt bound by the SHA256 above>",
  "retrieval": "dense",
  "rerank": false,
  "rerank_provider": "h2o",
  "candidates": 50,
  "max_results": 8,
  "source_budget": 12000,
  "token_budget": 4096,
  "include_dependencies": true,
  "overlays": [],
  "needed_evidence": [
    "implementation",
    "callers",
    "tests",
    "behavioral_constraints"
  ]
}
```

The control request is identical in every field except that it omits
`needed_evidence`. The manifest freezes both canonical request serializations and
their hashes.
The experimental selector sees the resolved request, common candidate and relation
inventories, and nothing else from the harness manifest. It does not receive the
benchmark task ID, evidence-group IDs, target paths, symbols, line spans, source
hashes, expected ranks, reference patch, checker implementation, schedule, gates,
or checker outcomes. Public API names and constraints already present in the bound
task prompt are not hidden.

Evaluator-only gold contains:

- predeclared key-evidence groups and their importance;
- OR alternatives within a group and AND requirements across groups;
- exact repository-relative paths, symbols, inclusive physical line spans, and
  SHA256 values that resolve and verify source bytes from the pinned revision;
- the expected availability of each alternative under the frozen extraction and
  scope (`indexed` or `unsupported_artifact_kind`);
- a predeclared set of implementation/caller groups eligible for the complementary
  admission gate.

The separate freeze binding, rather than the gold checklist, binds the reference
probe, independent checker, and compatibility manifest.

Gold stays outside the planner process, search request, indexed Requests checkout,
and coding-agent checkout. For the packet gate, the evaluator may use gold only
after both conditions have completed final packing. For agent-result grading, it may
use gold and the frozen checker only after that trial has ended. Gold is never a
planner, retrieval, selection, or packing input. This is an evaluation boundary, not
a security claim about the development repository that contains the existing
benchmark materials.

Before running, the freeze validator must serialize both complete requests, match
their frozen hashes, and prove that evaluator-only keys and sentinel gold values do
not cross that boundary. A task key used by the harness for bookkeeping is not a
planner input.

## Conditions and retrieval parity

There are two conditions:

1. `legacy_control`: the current dense retrieval, selection, dependency expansion, and
   packing path. No evidence request is supplied to selection.
2. `task_evidence`: the same ordered retrieved candidates followed by the
   experimental task-evidence selector and packer. The only additional semantic
   input is the frozen public request above.

Both conditions use the following exact settings:

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

With reranking disabled, the backend's retrieval prefix is the same ordered dense
top 50 in both conditions. Both conditions use the same pinned source
bytes, extraction, embeddings, index, query vector, abstention policy, relation
graph, and exact compact `cl100k_base` token accounting. The experiment may choose
and pack only evidence present in that common candidate and statically resolved
dependency inventory. It cannot issue another retrieval, change scores, introduce
a reranker or model call, use corpus-specific rules, or read evaluator gold.

The character cap is 12,000 actual delivered source characters, not full-window
characters. The token cap is 4,096 tokens for the exact compact response envelope,
not an estimate of provider tokens. Whole physical source lines, inclusive line
bounds, exact delivered bytes, source hashes, truncation, origin, and relation
provenance must be retained in both conditions.

## Evidence status and gap reporting

A requested evidence kind is never marked complete merely because retrieval
returned a plausible candidate. The public experimental response uses only
`candidate`, `partial`, or `unresolved`. `candidate` means that at least one
plausible final packed excerpt is linked to the need; it is not a completeness
claim. Complete literal gold-group coverage is a separate evaluator-only result,
computed after final packing.

Public unresolved entries contain an evidence kind, status, and a procedural reason
known to the engine. The frozen reason vocabulary is:

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

The engine must not invent a path or claim that no implementation exists when its
index cannot establish that fact. A gap caused by an unsupported artifact kind or
missing extractable window says so. A budget gap identifies the candidate that was
available before packing, the applicable cap, and whether the eight-result,
12,000-character, or 4,096-token limit excluded it. Configuration requested from
an index that cannot parse its file kind remains explicitly unresolved.

The evaluator separately reconciles every missing gold group to the first factual
cause in the frozen pipeline. That receipt can use gold paths and spans, but it is
never returned to the planner or coding agent. Candidate status and evaluator gold
coverage must remain separate fields.

Gold scoring happens only after final packing. One actual returned excerpt must
contain the entire exact evidence span. OR alternatives satisfy one group; all
required groups are ANDed. Combining incomplete fragments, matching a symbol name,
returning an untruncated window bound without its bytes, or finding a candidate that
packing removed does not count. Every scored excerpt is checked against pinned
source bytes, inclusive physical line bounds, source SHA256, and independently
recounted compact tokens.

## Paired schedule

The coding-agent schedule is predeclared now but remains blocked until the Requests
packet gate, native-latency gate, and a separately frozen untouched-codebase
validation all pass. There are four paired repetitions and eight total trials.
Order is balanced and frozen before any new coding-agent outcome is observed:

| repetition | first | second |
|---:|---|---|
| 1 | `legacy_control` | `task_evidence` |
| 2 | `task_evidence` | `legacy_control` |
| 3 | `legacy_control` | `task_evidence` |
| 4 | `task_evidence` | `legacy_control` |

Each trial receives a fresh Requests checkout, conversation, and condition-specific
packet. Each pair uses the same immutable corpus and index snapshot. Model, effort,
service tier, sandbox, ordinary tools, common instructions, timeout, Python
environment, and test guidance are frozen before repetition one and identical
within every pair. The task-evidence condition adds only its packet; the control is
not prevented from using ordinary repository search and reads.

No retries, resumed conversations, substituted trials, or discarded failures are
allowed. Timeouts, infrastructure errors, unsuccessful patches, unavailable usage,
and missing timing receipts remain in the eight-trial inventory and denominator.
Warm index/model setup is recorded separately from agent time.

## Gates

The manifest's ten canonical gates must pass before coding-agent trials begin:

1. `source_freeze_integrity`: the pinned revision, exact exclusions and membership,
   extractor fingerprint, counts, hashes, and planner boundary match the freeze.
2. `retrieval_parity`: the requests are identical except for `needed_evidence`, and
   both conditions use identical source, ordered candidate IDs and scores, relation
   graph, tokenizer, and budgets.
3. `legacy_path_equality`: the control compact response is byte-for-byte identical
   to the unchanged legacy path.
4. `complementary_gain`: `task_evidence` adds at least one evaluator-predeclared
   complete indexed implementation or caller group from a newly covered source
   file. Here, *newly covered* means that the path is absent from the control's
   covered required groups; it does not claim that no control excerpt mentioned the
   path.
5. `no_control_coverage_loss`: treatment loses no required group covered by control
   and preserves each excerpt establishing that coverage byte-for-byte.
6. `exact_budgets_and_source`: each packet stays at or below eight excerpts, 12,000
   delivered source characters, and 4,096 exact compact tokens, and every excerpt
   matches frozen source bytes, physical line bounds, and hashes.
7. `truthful_gap_reconciliation`: every missing gold group has an accurate evaluator
   cause, and every requested public need has a non-overclaiming status or gap.
8. `deterministic_replay`: all three executions per condition produce identical
   selected rows and normalized payloads from the same frozen inputs. Timing and
   request identifiers are recorded but excluded from normalized equality.
9. `native_latency`: the frozen native schedule has a treatment/control median
   wall-time ratio no greater than 1.25.
10. `planner_gold_isolation`: neither resolved planner request contains an
    evaluator-only key or sentinel value.

The packet gate uses three paired replays in the frozen order
`legacy_control`/`task_evidence`, `task_evidence`/`legacy_control`, then
`legacy_control`/`task_evidence`. Each condition therefore executes three times
against the same shared retrieval capture. All three normalized outputs per
condition must agree; the first pair alone cannot establish determinism.

The native latency run uses one immutable warm resident index, disables query-result
caching, and requires one fresh query embedding per call. Run six unmeasured
alternating warmups, then 42 measured searches per condition as 42 pairs, alternating
condition order so each condition runs first in 21 pairs. Time monotonic wall clock
only around `backend.search`, including embedding, selection, and packing; exclude
index/model setup, warmups, receipt serialization, and evaluator scoring. The gate
is the median of 42 treatment durations divided by the median of 42 control
durations. Candidate IDs, scores, and query-vector bytes must still match across
each pair.

Synthetic tests must cover public/gold isolation, exact scope and exclusions,
retrieval parity, full-span OR/AND scoring, fragment rejection, result/character/
token exhaustion, unsupported source kinds, missing extractor windows, relation
failures, complementary admission without a control-coverage loss, deterministic
ties, and legacy-path equality when the research mode is absent. The real Requests
packet is an integration fixture, not a replacement for these tests.

Before selector implementation or any Requests packet outcome, select and commit the
untouched repository, task, checklist, scope, budgets, and selection rule. After the
Requests packet and latency gates pass, execute that already frozen untouched-codebase
admission validation. Do not begin coding-agent trials unless it passes without
corpus-specific rules or an individual coverage loss. This observed Requests task is
not a substitute for that guardrail; choosing the untouched case after seeing a
Requests outcome invalidates the chronology and requires a new named freeze.

After that prerequisite passes, an agent result is `correct` only when the frozen
independent Requests response-limit checker and all frozen original compatibility
commands pass against the candidate patch. Never grade from the agent's final text,
its test-pass claim, or tests it wrote. Original tracked tests and test configuration
are restored in the evaluator copy as in the existing multi-file workflow protocol.

Promotion from Phase 0 requires all of the following:

- `task_evidence` is completed-correct in all four repetitions;
- it is never worse than its paired control on completed-correct status;
- at least three pairs have both conditions completed-correct and complete usage
  and timing receipts;
- across those qualified pairs, the median of paired treatment/control ratios is at
  most 0.90 for gross hosted tokens and at most 0.90 for agent time; and
- median full-trial elapsed time, including task-evidence packet construction but
  excluding common frozen setup, regresses by no more than 10%.

Report all four pairs, all packet inventories and gaps, individual checker results,
gross/cached/output token fields, agent time, packet time, full-trial time, and every
missing receipt. Report unfiltered per-condition medians and all-trial totals beside
the outcome-qualified paired ratios, so the both-correct subset is not mistaken for
the whole study. Do not compute a complete-inventory token total when usage is
missing. A failed gate is a recorded development result, not a reason to tune on a
trial and rerun it under the same study name.

## Freeze and runtime integrity

The files added in this change remain a working-tree freeze candidate until a matching
commit anchors them. Before an anchor exists, the validator requires the union of
every committed and uncommitted path touched after `project_base_revision` to stay
inside the Phase 0 artifact closure. This candidate-only rule prevents selector work,
retrieval, or packet outcomes from being placed ahead of the prospective freeze.

A commit becomes the freeze anchor only when all of the following hold at that exact
commit:

- the binding file's bytes exactly match the binding being validated;
- every artifact named by the binding exists with exactly its bound SHA256;
- the union of paths touched from `project_base_revision` through that commit is
  contained in the bound artifact closure; and
- the commit is an ancestor of the current selector or result revision.

Once that anchor exists, later commits may add or change selector, harness, result,
or other implementation paths outside the protected closure. The validator no longer
requires every post-base path to remain in the closure. It instead requires that no
bound artifact or binding path was touched after the anchor and that the current
worktree has no change to a protected path. This is the handoff from
`freeze_candidate` to `committed_freeze_ancestor`: it preserves the prospective
bytes while allowing the next milestone to proceed. Regenerating hashes or touching
a bound artifact after the anchor does not preserve this study and requires a new
named freeze.

The required push/PR job is `.github/workflows/task-evidence-freeze.yml`, job
`requests-source-freeze`. It prepares a detached, clean checkout at the pinned
Requests revision, runs the standalone validator, and then runs
`tests/test_task_evidence_freeze.py`. A local source-freeze review must run the
equivalent standalone validation against its pinned clean checkout:

```console
.venv/bin/python scripts/validate_task_evidence_freeze.py --corpus /absolute/path/to/pinned/requests-checkout
```

The required CI job and standalone command are mandatory. Neither may be skipped,
and the validator may not be replaced by unit tests or run against a dirty or
different-revision corpus. It performs the source-only scope, planner-boundary,
gold-span, feasibility, and binding checks without embedding, retrieval, planner,
model, network, or index calls. The workflow fetches complete project history and
validates the checked-out push or pull-request head. Consequently, ordinary selector
work after the committed anchor does not block required CI; CI fails only if no valid
anchor can be reconstructed or a protected artifact was touched afterward.

The Phase 0 anchor binds this protocol; the public prompt and request; evaluator gold;
the Requests revision, source inventory, exclusions, and extractor; checker and
reference hashes; compatibility commands; the prospective schedule; and the freeze
validation. Before packet execution, a separate execution freeze must name this
ancestor and additionally bind the embedding, index, tokenizer, relation, selector,
and packer fingerprints; the two condition settings; ordered candidate inventory;
model/runtime settings; and execution tests. A change to either freeze's protected
inputs requires a new named freeze and four new pairs.

Freeze artifacts keep the harness-visible `benchmarks/task_evidence_requests.json`
separate from evaluator-only
`benchmarks/task_evidence_gold/requests_response_limit.json`. Trial artifacts record
planner inputs,
normalized packets, candidate and selection receipts, gap receipts, patches, usage,
timings, and independent grading. Published artifacts may include evaluator scores
after the run, but a later replay must still enforce the same planner boundary.

Phase 0 runs through an explicitly enabled research harness or postprocessor. With
that mode absent, the existing runtime path, defaults, schema, selected source,
packing behavior, and normalized compact response must remain unchanged. No
`needed_evidence` argument is added to the production MCP, HTTP, CLI, or VS Code
contract in this phase. Existing deterministic fields are compared to frozen legacy
fixtures; inherently variable timing and request metadata are compared only for
shape.

A passing study supports one narrow statement: on this observed Requests change,
the frozen task-evidence packet helped the frozen agent workflow under the stated
caps. It does not establish a universal checklist, correctness on unseen tasks,
causality for any one selector component, or the project's broader token-savings
goal. Those claims require new tasks and a separately frozen evaluation.
