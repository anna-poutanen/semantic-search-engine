# Shared-flow protection for complementary implementations

The new `shared_flow` rule passes the frozen development quality gate: it keeps
the two Express complete-query gains from the earlier source-graph experiment
and loses no previously covered evidence group, complete result or single-file
hit against either original control. **It fails fresh native qualification:** it
adds no evidence beyond coverage control and exceeds the warm-latency limit.
The shared-flow rule remains experimental. A separate product decision below
uses the existing, unchanged coverage policy; context limits and release assets
remain unchanged.

The advance is preserving those already observed gains while preventing the
earlier HTTPX group loss in this development matrix. It is not evidence of new
coding correctness, faster agent completion or billed token savings.

## Why the previous rule lost an implementation

The [October27 source-aware experiment](source-aware-allocation.md) promoted
HTTPX's `encode_request` through honest calls to retrieved encoder functions. It
evicted `Request._prepare`, which had already been retrieved at raw rank six and
delivered in the original eight-slot response. Both responses fit well under the
character and token budgets; packing and backfill did not cause this loss.

The [saved diagnosis](../results/source-allocation-development-2026-10-27/diagnosis.md)
identified an explicit shared caller: `Request.__init__` calls both
`encode_request` and `Request._prepare`. Protecting only the original first-five
anchors and their one-hop neighbors missed that constructor flow. The query
remained incomplete before and after, so checking only complete-query totals
would have concealed the lost header-preparation group.

## The fixed shared-flow rule

The [prospective development protocol](flow-allocation-development-protocol.md)
fixes one candidate before outcomes. It retains the same one-hop ranked
candidate pool, at most two additions, original scores and source windows,
**eight excerpts, 12,000 source characters and 4,096 exact compact JSON tokens**.
No model, retrieval, reranker, indexed source or query rewrite changes.

For each prospective addition, inspect existing uniquely resolved direct callers
and their direct callees. Protect original baseline implementations in that
shared flow before assigning an exchangeable slot. An unranked caller or sibling
is not retrieved or inserted by this rule. Ambiguous identities, factory returns,
dynamic receivers and transitive relationships supply no invented edge.

Admissions form a prefix of the original candidate rank order. If the strongest
remaining candidate's protection leaves insufficient slots, stop rather than
skip it for a weaker candidate. Keep any earlier accepted prefix, but roll back
all protection from the blocked trial. Original dependencies and their inherited
owner scores remain intact.

After ordinary packing and coverage backfill, the unchanged source guard checks
actual protected reference prefixes, including containing excerpts that absorbed
an anchor or sibling. Literal bytes, physical intervals, hashes, dependency
owners and buffer provenance must survive. A rejected proposal returns the exact
coverage control. Actual flow/base/guard receipts remain outside the public
backend and MCP responses.

The previous pure graph planner runs once as an explicitly recorded internal
base helper. Its actual receipt is verified against its original October27
receipt; its proposed allocation is discarded. This does not rerun or relabel
the old producer's benchmark outcomes.

## Development results

The [complete frozen findings](../results/flow-development-2026-10-29/findings.md)
cover **365 observed cases sharing 187 dataset/query units**. Former holdouts are
development evidence here. Language/native studies and retrieval orders reuse
queries, so their rows are not independent trials.

The producer first reproduced 365 original legacy responses, 337 known coverage
responses and all 730 original parent-control arms. It then computed only 365
new `shared_flow` traces. Four failed historical arms remain **1,460 immutable
record references**, with their original execution identities and unfavorable
outcomes retained.

| Condition | Additional distinct complete queries vs coverage | Lost full-group occurrences | Lost complete case rows | Lost single-file hits |
| --- | ---: | ---: | ---: | ---: |
| Historical `aspect_fill` | 1 | 4 | 4 | 0 |
| Historical `aspect_balanced` | 3 | 12 | 10 | 1 |
| Historical `source_graph` | 2 | 1 | 0 | 0 |
| Historical `source_graph_all` | 0 | 0 | 0 | 0 |
| New `shared_flow` | **2** | **0** | **0** | **0** |

The table uses different units deliberately: a repeated complete-query gain
counts once per dataset/query, while every lost group in every original order
counts against qualification. Historical rows reference the
[October26 allocation ledger](../results/allocation-development-2026-10-26/replay.jsonl.gz)
and [October27 source-allocation ledger](../results/source-allocation-development-2026-10-27/replay.jsonl.gz);
they were not generated again by a producer. Their original reports are
[complementary allocation](complementary-allocation.md) and
[source-aware allocation](source-aware-allocation.md).

Against both `legacy` and `coverage_control`, the new arm loses **zero individual
full evidence groups, complete positive cases and single-file first hits**,
including groups from previously incomplete queries. It gains two distinct
complete queries beyond coverage control:

- Express uses its configured query parser, including `app.set` configuration
  side effects.
- Express applies trusted forwarded protocol settings, including `app.set`
  trust-proxy configuration.

Those behaviors appear in six improved rows across reused language/native and
dense/hybrid orders, but still represent only two dataset/query units. They were
already observed as gains in the failed `source_graph` study. The earlier
coverage control's Starlette and Zod gains are not counted as new shared-flow
gains. Every original stratum and all comparisons remain in the
[machine-readable summary](../results/flow-development-2026-10-29/summary.json).

For the HTTPX encoding/header case, the first candidate is `encode_request` at
raw rank eight. Its shared constructor flow now protects the already delivered
`Request._prepare` at lines 441–460. Together with five original anchors and two
dependencies, that leaves no exchangeable slot. The rule records
`shared_flow_capacity_blocked`, stops at that candidate and returns the exact
coverage reference. The header-preparation group remains at delivered rank
eight. The query is still incomplete: this rule does not add the missing
constructor or encoder evidence. Preventing a regression is distinct from
completing that query.

## Independent verification and reproducibility

The [source/plan freeze](../results/flow-development-2026-10-29/frozen-plan.json),
producer and independent auditor were committed in
`345069d494a05f3fe5858f0cf6249ef3c175355a` before execution. Outcomes were committed
in `9ae5d506d1119ef9847ba915502d4e9eec25ce46`. The
[canonical independent audit](../results/flow-development-2026-10-29/independent-audit.json)
passes all 365 new traces, both original controls and all historical references.
Its SHA-256 is
`42acbd1d5d745b33e1be013c5daf093c2482efc53e2e0edd7b95b4a7aaa7cb71`.

The independent checker reconstructs raw-rank admission, shared-caller witnesses,
capacity, prefix stopping, blocked-trial rollback, exact guard projection,
maximal physical-prefix/token packing, deterministic containment/backfill and
every quality change. It separately re-certifies the previous 730-row study as
historical data, preserving its original receipt. It imports no producer,
selector, allocator, backend, model, index or numerical runtime.

Its shared production trust boundary is the existing frozen conservative source
graph extractor and parser modules. Complete source/definition inventories and
graph bytes remain hash-bound; the new study points to the previous complete
graph inventory rather than copying it or any large outcome matrix.

The [new raw ledger](../results/flow-development-2026-10-29/replay.jsonl.gz)
retains actual planner/base/guard receipts, all intermediate and delivered source,
original order/scalars/origins and exact compact payloads. Reference manifests
preserve [both controls](../results/flow-development-2026-10-29/control-references.json),
[October26 negatives](../results/flow-development-2026-10-29/negative-references.json)
and [October27 negatives](../results/flow-development-2026-10-29/source-negative-references.json).
Each completed new trace is flushed before the next case; interruptions retain a
readable partial gzip and failure counters, with no resume or overwrite.

CPU contract checks exercise actual backend/MCP envelopes at normal and smaller
budgets, one query embedding and reusable document vectors, literal snippet and
buffer hashes, injected H2O/Qwen score provenance, dependency scores, cancellation
and final source-snapshot validation. They use invented repositories and CPU
embedders; they are not real model, external editor, coding-workflow or download
benchmarks.

## Fresh native result: shared flow is not qualified

The [separate native protocol](flow-allocation-native-protocol.md) and all native
producer/auditor source were committed in `428095fc0e003742c97e272c6050c0314bb89055`
before source-only preparation. The [native plan and inventory](../results/flow-native-holdout-2026-10-29/plan.json)
were committed in `0d2f3817ae76bc29cfe3b4383dc079180fae42b2` before any fresh
indexing, rule activation or inference. The actual execution used that commit.

The independently source-reviewed TanStack Query core 5.90.5 sample contains
23 production TypeScript files, 331 windows and 14 queries: eight multi-file
positives, four single-file controls and two scoped absences. It was previously
unqueried within this project; it is now **observed development evidence**.
No model-pretraining isolation is claimed.

All **252 measured searches plus six explicit warmups** completed. Each measured
request executed one actual query embedding and zero document embeddings. Actual
vectors, raw scores/orders and compact payloads were stable across paired arms
and repetitions. Candidate calls each executed one flow planner, one base helper,
one lazy coverage reference and one source guard. Setup and diagnostic copying
were excluded from resident search timing; real planning and packing were timed.
The Mac was open on battery; heavy local tests completed before measurement.

| Mode | Policy | Complete positives | Complete multi-file | Full groups | Median / P95 |
| --- | --- | ---: | ---: | ---: | ---: |
| dense | `legacy` | 5/12 | 1/8 | 17/33 | 240 / 267 ms |
| dense | `coverage_control` | 6/12 | 2/8 | 19/33 | 246 / 285 ms |
| dense | `shared_flow` | 6/12 | 2/8 | 19/33 | 328 / 442 ms |
| hybrid | `legacy` | 5/12 | 1/8 | 19/33 | 246 / 358 ms |
| hybrid | `coverage_control` | 5/12 | 1/8 | 19/33 | 258 / 299 ms |
| hybrid | `shared_flow` | 5/12 | 1/8 | 19/33 | 354 / 425 ms |

Quality uses the first repetition, with full qualified-symbol, physical-interval
and exact evidence/source hash checks. Timing uses all three repetitions, 42
calls per policy/mode. Shared flow gains **zero individual groups or complete
queries beyond coverage**, and loses none against either control. Its one dense
complete-query gain against legacy is inherited from coverage, not a new gain
from the flow rule. All three arms return candidates for the two absent queries;
those responses do not establish feature absence.

Shared flow's dense median is **1.337 times coverage and 1.367 times legacy**;
hybrid is **1.371 and 1.439 times**, respectively. Both modes fail the fixed
1.25 limit and the incremental complete-query requirement. Hybrid cannot rescue
dense. The frozen gates and failure remain unchanged.

The [native independent audit](../results/flow-native-holdout-2026-10-29/independent-audit.json)
reconstructs both baseline allocations, actual shared-flow planning, maximal
packing/backfill, guard choices, qualified evidence, vectors/counters and every
quality/latency gate. Its SHA-256 is
`592539790c10fb3085b937955cfffcb879e7b5d050961b2cc429c158a729d70d`.
The [raw traces](../results/flow-native-holdout-2026-10-29/searches.jsonl.gz),
[summary](../results/flow-native-holdout-2026-10-29/summary.json) and
[validation](../results/flow-validation-2026-10-29/validation.json) preserve
unfavorable outcomes. Audit success establishes trace integrity, not qualification.

## What still omits implementations

The clearly separated [post hoc quality diagnosis](../results/flow-native-holdout-2026-10-29/posthoc-quality-diagnosis.md)
classifies 28 missing candidate-group occurrences across dense and hybrid:

- **18:** a complete, correctly identified implementation was retrieved and
  retained as a canonical candidate, then omitted from the final context.
- **4:** a covering raw window was discarded when one ranked representative per
  canonical definition was chosen.
- **5:** no raw50 window contained the full required evidence.
- **1:** a later backfill row ended at the character budget before its evidence;
  no remaining miss was caused by token clipping.

None of the 19 first-allocation omissions (18 final omissions plus the clipped
backfill) has a covering window in this rule's actual one-hop proposal pool.
Relaxing shared-flow protection cannot fix those misses with that pool.
Dense raw50 contains all required groups for seven of eight multi-file queries,
versus five after canonical first-window selection and two finally delivered.
These are descriptive oracle-availability bounds, not achievable policy results;
they ignore joint eight-slot and packing feasibility.

The [post hoc latency review](../results/flow-native-holdout-2026-10-29/posthoc-latency.md)
finds all 57 unadmitted candidate calls built identical proposal/reference source
views. Packaging accounts for most observed overhead. A separately proved
fast path could avoid that duplicate work, but speed alone cannot create the
missing evidence or pass this rule's quality gate. No such speedup is claimed.

Next research should jointly retain a bounded set of raw windows per exact
definition and select complementary implementations from the existing candidate
pool within current caps. Preserve actual delivered evidence and every individual
loss check; confirm any new rule on another independently authored repository.
The unchanged graph must not be credited with unresolved dynamic dispatch or
conditional-alias edges.

## Separate product decision: use the existing coverage policy

The product change selects the **unchanged** `definition_context` plus
`coalesce_context` policy already measured as `coverage_control`. It does not
promote, relabel or loosen the gate for `shared_flow`. This is a **post hoc
engineering decision**, not a prospectively registered coverage-promotion study
or a new generalization claim.

Coverage loses no individual evidence group, complete case or single-file hit
against legacy across the 365 observed development cases, and adds the two known
Starlette/Zod complete behaviors. The prior urllib3 native sample was neutral.
In the new TanStack sample, coverage completes one additional dense multi-file
behavior (mutation-scope serial continuation), adds two full groups, and loses
none in either mode. Its median is 2.2% higher than legacy in dense and 4.9% in
hybrid; these small-sample timings do not prove equal performance elsewhere.

The minimal backend import change is committed in
`edeca47d70dae05a25ba1e604c8e8a2954cd57d9`; the original selection and backfill
functions remain byte-identical. Exactly three research backend tests now check
restoration of their original hooks instead of hardcoding the old public default.
The frozen experiment scripts, receipts, outcomes, evidence criteria and quality
gates remain unchanged; current backend/test revisions are versioned after them.
The original test versions remain in their frozen Git commits.

Actual default/MCP contracts cover dense, hybrid and lexical search, ordinary and
small/zero budgets, literal source hashes, separate ambiguous definitions,
contained dependency-owner source with unchanged caller identifiers/inherited
scores, cached document vectors, unsaved buffers, cancellation and final snapshot
checks. The product suite passes **3,506 local Python tests with 10 skips**.
The parity checker subsequently adds eight cache-metadata regression cases;
final focused checks cover all 41 default contracts and 43 verifier tests.
The 179 editor/host-helper checks and installed five-process CPU CLI smoke pass;
[separate product validation](../results/default-context-validation-2026-10-29/validation.json)
retains the local run logs, exact test-revision distinction and interrupted attempt.

The [real-model post hoc integration check](../results/default-context-parity-2026-10-29-v2/summary.json)
at `9fc914a23cb69a2123e99c43c2f17feeb13b3e34` executes all 14 observed queries
in both modes with the actual default backend, with no selector patches.
**All 28 responses match the frozen coverage controls in every field except
operational timings/index statistics, including private backfill diagnostics,
exact compact payloads and query-vector hashes.**
The [independent integration review](../results/default-context-parity-2026-10-29-v2/independent-review.json)
checks all 28 responses, 224 literal source excerpts and committed source bindings
without inference. There are 28 actual query embeddings and zero document encodings. A separate
closed-cache copy is used; the original native cache hash remains unchanged.
The first verifier preflight used the wrong nested cache-path metadata key,
failed before any model/index/search call and is
[preserved separately](../results/default-context-parity-2026-10-29/failure.json).
The repaired checker and real saved-metadata regression were committed before
the new attempt. This check establishes observed integration parity, not another
fresh quality or comparative latency study.
The public budgets, model/retrieval/reranker scores and API remain the same.
Canonical identity avoids collapsing separate definitions with the same display
label; exact-source containment removes redundant rows and backfills freed slots
without trimming previously delivered source. The more complex flow experiment
remains available for research only.

To reproduce the original studies, check out their original frozen execution
commits above. Later product import changes and test restoration assertions are
versioned after the native outcomes; they are not attributed to the frozen runs.
Historical development path/span criteria and fresh qualified-symbol/hash criteria
remain separate. Neither these retrieval results nor integration checks establish
coding correctness, billed token savings or arbitrary-repository generalization.
No release or coding comparison follows automatically.
