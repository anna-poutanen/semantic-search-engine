# Fresh attrs result: quality gain, failed native qualification

The fixed state-write guard improves dense multi-file completion on fresh attrs,
with no individual dense evidence loss, but fails the frozen latency gate.
Hybrid also loses one previously complete behavior. The candidate stays
experimental; production coverage, release 0.1.5 and coding-workflow comparisons
are unchanged. Attrs is now observed development evidence, not an untouched
holdout for future tuning.

All 252 measured searches and six explicit warmups completed. The
[independent audit](independent-audit.json) verifies every source/score/provenance,
compact payload, query vector, resident-vector lookup, model forward, role decision
and individual quality comparison. It passes integrity with **zero new native
diagnostic normalizations**; the original parent correction remains retained.
Integrity does not mean quality or latency qualification.

## Results under the unchanged caps

Quality uses the first repetition of the unchanged 14-query source-reviewed
sample: eight multi-file positives, four single-file controls and two scoped
absences. Timing uses all three repetitions, 42 searches per arm/mode. Repeated
conditions are not independent quality samples.

| Mode | Arm | Complete positives | Complete multi-file | Required groups | Median / P95 |
| --- | --- | ---: | ---: | ---: | ---: |
| Dense | Legacy | 6/12 | 2/8 | 24/35 | 267 / 384 ms |
| Dense | Coverage | 6/12 | 2/8 | 24/35 | 266 / 387 ms |
| Dense | Role-guarded semantic | 7/12 | 3/8 | 25/35 | 660 / 829 ms |
| Hybrid | Legacy | 6/12 | 2/8 | 23/35 | 274 / 375 ms |
| Hybrid | Coverage | 7/12 | 3/8 | 24/35 | 272 / 419 ms |
| Hybrid | Role-guarded semantic | 7/12 | 3/8 | 25/35 | 677 / 822 ms |

Dense gains one complete behavior, `attrs_validation_disabled_context_and_entrypoints`,
and loses no individual required group, complete behavior or single-file first
hit against either control. Its median is **2.477 times coverage** and **2.469
times legacy**, exceeding the predeclared 1.25 limit. The primary gate fails.

Hybrid gains that same behavior but loses a required group and completeness for
`attrs_optional_context_converter_and_generated_arguments` against coverage.
The unchanged aggregate 7/12 conceals that regression. Its median is **2.487
times coverage** and **2.473 times legacy**. Hybrid cannot rescue dense and also
fails its own quality and latency checks. All single-file first hits are preserved.
Both absence queries return candidate code; those results do not prove absence.

## Where implementations disappeared

The hybrid regression occurs **after retrieval and baseline assembly**. The
required `Converter._fmt_converter_call` implementation (`src/attr/_make.py`,
2867–2882; required evidence 2873–2882) is retrieved at raw rank 8 and delivered
at coverage rank 8. Coverage first removes two contained optional-converter
windows and backfills `_assign_with_converter` and the formatter. Semantic v1
then replaces the formatter with `default_if_none` (raw rank 11) and the assignment
helper with `_cmp._make_init` (raw rank 44). All three repetitions repeat this loss.

The role guard accepts the proposal because both removed full Python definitions
return generated-source strings without explicit attribute/subscript store or
delete AST nodes. Assignment-looking text inside a string is intentionally not
executed write syntax. Other protected implementations remain exact. This is a
functional code-generation/dependency blind spot within the guard's declared
scope, not a parser implementation error. The reference is only 2,390 compact
tokens/6,098 source characters; the candidate is 2,520/6,719. The eight-row cap
still binds, while token/character exhaustion does not cause this regression.

Per-facet maximum similarity is not implementation coverage: the formatter is
the maximum for none of the query facets. Removing it therefore looks lossless
under that objective. A retained caller, `_setattr_with_converter`, has an
annotated `converter: Converter` parameter and calls `converter._fmt_converter_call`,
but the frozen conservative graph has no edge to that method. A forwarding
clause also omits the short `and field` fragment while the full query retains it.
These are saved-trace diagnoses, not changes tested in this experiment.

Dense still omits that formatter despite retrieving it at raw rank 11; zero dense
regressions does not mean omissions are solved. Its separate successful change
recovers `_make.validate` (1911–1926, evidence 1920–1926), already retrieved at
raw rank 12 but omitted by both controls. It replaces the read/call-only
`_OrValidator.__call__` and newly completes all four required groups. Hybrid
recovers manual/setter validation through a corresponding pair of exchanges.
No additional retrieval or model run was used for this diagnosis.

## Actual model work and timing

The new index embeds 204 windows in 51 native batches of four. Setup takes
26.288 seconds including indexing and six generic warmups. Warmups use the
fixed generic query and encode **no clauses**; this is resident-model search,
not a claim that every future clause-batch shape was separately warmed.

The measured matrix executes 252 original-query singleton forwards and 78 clause
forwards encoding 228 texts, with **zero measured document inference**. Each
active request re-encodes its clauses, including repeated queries. Actual query
vectors and raw ranks/scores match across paired arms and repetitions; compact
outputs repeat within each arm/mode. Maximum output is eight excerpts, 11,996
source characters and 4,091 compact tokens, with no cap overruns.

| Median candidate phase | Dense | Hybrid |
| --- | ---: | ---: |
| Coverage assembly | 8.03 ms | 8.35 ms |
| Live feature provider | 340.60 ms | 344.40 ms |
| Prepared-feature allocation | 46.54 ms | 49.12 ms |
| Terminal role guard | 2.18 ms | 2.49 ms |
| Clause native forward, active requests | 59.90 ms | 61.87 ms |

Phase medians use 42 candidate calls per mode; the last row uses 39 active calls.
Medians across phases are not additive. Provider time totals 27.098 seconds,
of which recorded clause forwards account for 4.835 seconds. The remainder
includes source/vector validation and embedding API work outside the forward
timer. These traces cannot isolate its CPU/GPU/tokenization/copying components
or establish a causal culprit. The role guard itself is a small measured cost.

Each mode activates features for 39/42 calls but changes final context in only
6/42. Thus 33/39 active calls encode clauses without changing the answer. Dense
blocks three v1 proposals; hybrid blocks none. These comparisons describe
different queries and do not prove a safe early-skip rule.

The Mac is on battery, 19% before the measured matrix and 18% afterward. Absolute
latency rises across repetitions in every arm: dense coverage medians
239→266→300 ms and candidate 599→666→742 ms; hybrid coverage 235→275→319 ms and
candidate 617→678→750 ms. The traces do not establish why. A charged-host timing
replication would be a separate observed-corpus run. Recorded backend durations
sum to 103.917 seconds; the first-to-last measured request span is 284.116 seconds,
including excluded reporting/capture/I/O gaps. Neither is coding-agent time,
MCP transport latency or provider-token savings.

## Prospective history and reproducibility

The development rule passes all 393 saved occurrences/201 distinct units with
786 exact controls and 3,734 retained historical references. That independently
verified pass is published in `597c5ed7235bcc1cc2ef95a8b9af0c0eb57a32ae` before
fresh source preparation. The rule remains frozen at
`b86eeb03c1c1e788a4a7497f1e0d0b41e6c367ca`.

Initial native source freeze `0b651ef32cb6ca2444a016587ce1842854696244` precedes
attrs source-only exposure by the rule designers in this native phase. Earlier
source author/reviewer exposure is disclosed in the original reviews. The first
preparation stops at the old review-schema
mismatch, with no plan/index/model/retrieval outcome. Its original failure is
[preserved](../role-native-preflight-2026-11-03/prepare-attempt-1/failure.json).
The corrective source freeze `a1d579cd3a7f9868d90165c73899e31ce71132bc` validates
the original flat semantic/author receipts and archived source facts. Its legacy
helper view exists only in memory. Original labels, reviews and failed attempt
are unchanged. All nine measured request/recording functions have identical ASTs
to the initial source freeze; guard, model, extraction and gates do not change.
This is a disclosed source-only technical repair, not a new blind rule design.

The [plan](plan.json), complete [source inventory](source-inventory.json) and
[independent input audit](input-audit.json) are committed and pushed in
`5bd6c24ad7cae613df877dd89e55576175ff4bfd` before the first index/model run.
Execution uses that exact commit. The audit independently reconstructs the full
Python inventory and all original source/review/parent/checkpoint/runtime bindings.
Published attrs source excerpts retain the upstream
[MIT license and copyright notice](../../benchmarks/ATTRS_LICENSE.txt).
Raw outcomes and full audit are published in
`b08c784162c4a89192c1cd0a1bf94f6d35edddcb`. The full execution uses an exclusive
cross-worktree GPU lease, durable per-row gzip ledger and preserved model-close/
lease-release receipts. No retry, resume or outcome-dependent mode selection occurs.

Preflight includes 467 invented tests with two complete 252-request loops, then
554 affected tests after the input-only compatibility repair (the two unchanged
full loops were already verified). GitHub CI at the published outcome commit
passes **4,810 Python tests with 10 skips**, including both full invented loops,
and the editor checks. [The complete core run](https://github.com/anna-poutanen/semantic-search-engine/actions/runs/41662090607/job/126007627987)
and earlier local validation are retained. This sample is authored and small; fresh means new project evaluation,
not isolation from model pretraining. No product-default or release change follows.

## Next work

Keep both failed gates. Profile the provider outside-forward cost before choosing
an optimization; caching repeated query features would not establish new-query
latency. A separate general quality experiment should resolve calls through
explicitly annotated receivers and preserve complementary caller/callee evidence,
including source generators, without repository/query/name exceptions or extra
model work. Freeze it against all now-observed development evidence, then reserve
a new untouched codebase for confirmation. Attrs cannot be reused as that holdout.
Another coding comparison remains gated on qualified retrieval improvement.
