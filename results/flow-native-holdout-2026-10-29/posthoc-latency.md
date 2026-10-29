# Post hoc latency and implementation assessment

This is a descriptive analysis of the completed, fixed native experiment. It is
**outside the canonical independent audit scope**. No frozen implementation,
protocol, plan or result was changed. No new model, index, retrieval, candidate
or timing experiment was run. TanStack Query is now observed development
evidence; this analysis does not preserve or renew its holdout status.

The native freeze and execution are commit
`0d2f3817ae76bc29cfe3b4383dc079180fae42b2`. Bindings used here:

| Artifact | SHA-256 |
|---|---|
| `plan.json` | `5dbd18f4a63b96359de54ecc8e89ab888673218905b413adf41954689ecfbcf3` |
| `run-started.json` | `e6c401e9e5cbaae2062d74cc49f4fe0d447e93aa23364ecc4858e941258a0dd2` |
| `searches.jsonl.gz` | `844e347569cf688330549f578780ff0ee3a50497b5edbf837095be2034fa6d6e` |
| `summary.json` | `d57120576bb2216c293798a0d28ba5adf1497a21c37d84c9252f56bcb62eada7` |

## Actual warm latency

Each cell below contains the median / P95 resident backend wall time in
milliseconds, over 42 actual requests per arm/mode. The three repetitions reuse
14 queries and are timing/stability measurements, not independent quality cases.

| Retrieval | Legacy | Coverage control | Shared flow | Flow / legacy median | Flow / coverage median |
|---|---:|---:|---:|---:|---:|
| Dense | 240.3 / 267.1 | 245.6 / 284.6 | 328.5 / 441.7 | 1.367 | 1.337 |
| Hybrid | 245.7 / 357.9 | 257.8 / 299.1 | 353.6 / 425.5 | 1.439 | 1.371 |

Both modes fail the frozen 1.25 median-ratio limit against **both** controls.
The setup, first document-embedding call and six explicit warmups took 34.83
seconds outside these measured requests. All 252 measured requests used one
actual query embedding and zero document-embedding calls. All arms reused the
same 331 indexed windows from 23 saved source files. Raw ranks/scores, vectors
and repeated compact payloads remained stable according to the saved receipts.

## Admission and fallback overhead

The existing summary groups candidate requests by actual flow admission. The
following paired deltas compare the same query, retrieval mode and repetition
with the measured coverage control. The narrower rows are derived directly
from the raw ledger.

| Retrieval / state | Calls | Candidate median, ms | Median paired overhead vs coverage, ms |
|---|---:|---:|---:|
| Dense: admitted | 12 | 338.5 | 78.1 |
| Dense: inactive or rejected base plan | 30 | 326.5 | 85.8 |
| Hybrid: admitted | 15 | 379.4 | 119.4 |
| Hybrid: inactive or blocked, combined | 27 | 342.1 | 85.4 |
| Hybrid: shared-flow capacity block | 3 | 342.1 | 97.8 |
| Hybrid: no literal multi-clause activation | 3 | 316.7 | 28.8 |
| Hybrid: other rejected base plan | 21 | 352.0 | 93.5 |

For dense, the 30 unadmitted requests contain three without literal activation
and 27 other base-plan fallbacks. Their combined candidate median is 326.5 ms;
the three without activation alone have a 295.5 ms median and a 55.4 ms paired
overhead. There is no dense case rejected before any admission by the added
shared-flow capacity rule. A plan may retain an admitted prefix when a later
candidate is blocked; it remains in the admitted cohort.

Dense has 12 admitted plans, nine accepted source guards and nine changed
compact contexts. Hybrid has 15 admitted plans, 12 accepted guards and 12 changed
contexts. The remaining three admitted proposals in each mode fall back because
protected source is not preserved. In total, **57 of 84 candidate requests are
unadmitted**, yet each still executes the flow planner, base helper, source guard
and lazy coverage reference. Their captured proposal and reference are identical
in all 57 actual source-context views: full rows and provenance, query/scope,
match status, character totals, budget flags, exact token usage and
`_selection_backfill` diagnostics. Operational timing/index fields are excluded
from that equality, as declared by the frozen protocol.

## Where the extra time appears

These are medians of recorded backend phase timings, in milliseconds. Packaging
includes selection, source checks, ordinary packing, postprocessing and the lazy
reference; it is not a function-level profiler. Phase medians need not sum to the
wall-time median.

| Retrieval / arm | Index/source refresh | Query embedding | Retrieval | Packaging |
|---|---:|---:|---:|---:|
| Dense legacy | 58.1 | 52.6 | 5.8 | 118.1 |
| Dense coverage | 56.6 | 44.2 | 5.9 | 128.7 |
| Dense shared flow | 56.0 | 48.4 | 6.0 | 216.7 |
| Hybrid legacy | 57.7 | 51.5 | 13.2 | 124.1 |
| Hybrid coverage | 57.1 | 46.6 | 13.3 | 139.1 |
| Hybrid shared flow | 58.7 | 49.0 | 13.2 | 223.1 |

Median **paired** candidate-minus-coverage phase differences are:

| Retrieval | Index/source refresh | Query embedding | Retrieval | Packaging |
|---|---:|---:|---:|---:|
| Dense | +0.5 | +1.6 | +0.1 | +81.8 |
| Hybrid | +1.4 | +3.8 | +0.0 | +84.7 |

The overhead is concentrated in packaging, including cases with no source change.
Source inspection explains a plausible contributor. The ordinary backend path
performs a source snapshot during refresh, a confirmation while building its
response, and a final confirmation. The candidate additionally builds the lazy
reference, which performs another confirmation. For this Git checkout, each
snapshot enumerates tracked files, reads and hashes the 23 included files, and
queries Git revision/branch identity. Thus the current candidate path performs
four snapshots instead of three, including an additional three Git subprocesses.
This count follows the frozen code; individual snapshot durations or subprocess
CPU costs were not separately profiled.

The candidate also builds the base and outer flow receipts, serializes/copies the
projected guard plan, coalesces both responses and copies the guard's chosen
result. All are real measured costs. Diagnostic iterator draining and receipt
serialization occur after the timer and do not explain the measured difference.
The data do not isolate an exact cost for each copy, graph pass or source check.

## Bounded optimization to investigate separately

A new implementation could add a narrow **no-admission fast path**. After the
actual planner establishes that its eligible sequence and dependency provenance
are the unchanged definition baseline, run ordinary packing and coverage
postprocessing once and return that source-context result. It need not construct,
pack and coalesce a second identical reference merely to choose it. The saved
57/57 equality is supporting evidence for this opportunity, not a substitute for
an equivalence proof or a measured improvement.

Keep the ordinary refresh/prepack/final saved-source checks, cancellation and
snapshot rejection behavior. Do not remove the real reference from admitted
proposals or weaken protected-source checks to improve a benchmark. Do not record
a cached or skipped reference as a newly executed packing pass. A revised profile
must declare which hooks actually run and time that new path truthfully.

Before measuring such an optimization, freeze it separately and require exact
source-context equivalence against the saved development and now-observed native
responses, including smaller budgets, containment/backfill, inherited scores,
dependency owners, buffer provenance, cancellation and concurrent saved-source
changes. Preserve earlier results. Then measure a new attempt rather than editing
this experiment. The current receipts do not establish that the optimization
would meet the 1.25 latency gate, nor quantify a counterfactual median.

## The quality requirement remains unmet

Shared flow preserves all individual groups on this sample, but adds **zero**
complete queries, multi-file completions or required groups beyond coverage in
both modes. Dense coverage and shared flow each complete 6/12 positives, 2/8
multi-file queries and 19/33 required groups. Hybrid completes 5/12, 1/8 and
19/33 respectively. The nine dense and 12 hybrid changed contexts supply no
additional scored implementation evidence.

Dense recovers one multi-file behavior over legacy, but the coverage control
already supplies it; it is not an incremental shared-flow result. Eliminating
latency overhead alone therefore cannot pass the frozen quality gate. A new
quality rule must recover additional complete implementation flows beyond
coverage while preserving every existing group. It requires a new development
freeze and later validation on another independently authored codebase. This
observed TanStack run cannot be reused as a fresh test after tuning. Production
defaults remain unchanged.
