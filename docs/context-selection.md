# Context selection follow-up

The candidate failed the fresh improvement gate: both arms completed **4/8
multi-file urllib3 behaviors**. The production default remains dense retrieval
with the original selector and dependency allocation. The new selector and
backfill implementation remain research code, exercised by the benchmark harness.

The development replay recovered two complete behaviors by removing redundant
excerpts only when another delivered excerpt already contained their entire
literal source, then filling the freed slots. Reserving more slots for ranked
candidates lost required dependency evidence and was rejected. This study keeps
the original eight-excerpt, 12,000-source-character and 4,096-compact-token caps.
The gain did not carry over to the fresh native test.

## What changed

Extraction can populate a transient map from each window ID to its actual AST
definition. Python keys include the decorated definition bounds; JavaScript and
TypeScript keys also include the parser's byte offset to distinguish same-line
declarations. Several windows of one long function share a key; separate lexical
definitions with the same displayed symbol have different keys. Snippet fields,
IDs, literal source, content hashes, extraction membership, persistent index
format and embedding-cache compatibility stay identical.

The definition selector uses those keys to keep one ranked window per actual
definition. The existing static graph identifies edges by displayed labels, so
the selector suppresses expansion from or to a label shared by distinct
definitions. It does not invent which definition owns that edge. Unknown window
identities fall back to their window IDs. Ambiguous labels are computed once per
atomic source refresh and published alongside the map; warm searches reuse that
immutable set. Failed or cancelled refreshes retain the prior complete snapshot.

The coverage postprocessor works after ordinary character and token packing. It
removes an actual delivered excerpt only if a single other actual delivered
excerpt contains the same physical lines and identical source bytes. This handles
an earlier child contained in a later parent as well as the reverse order. Equal
spans keep their first-ranked representative. Retained rows, source, hashes,
locations and provenance stay unchanged. Partial overlaps are retained.

Only after a removal can later distinct ranked candidates fill spare capacity.
At most eight noncovered candidates are tried. Only new rows can be clipped, by
whole physical source lines, with an exact compact-token recount after each
trim. Every original source span must remain inside one actual retained row;
failed preservation or budget guards restore the original response. Diagnostics
remain outside the compact MCP context. Existing dependencies keep their real
owner; newly backfilled ranked candidates are labeled retrieval results.

## Development evidence

The [prospective protocol](selection-development-protocol.md) freezes six
conditions and the full input matrix. Its 337 first-repetition condition rows
share **173 distinct dataset/query units**; neither repetitions nor reused
conditions count as independent queries. Express, Zod, Jinja, HTTPX and the
broader Qwen study are all observed development data here.

| Change against legacy | New distinct complete queries | Lost full groups | Lost complete condition rows | Decision |
|---|---:|---:|---:|---|
| Definition identity | 0 | 0 | 0 | No measured completeness gain alone |
| Preserve ranked slots | 4 | 7 | 4 | Reject |
| Identity plus preserve ranked slots | 4 | 7 | 4 | Reject |
| Coverage/backfill | 2 | 0 | 0 | Qualifies for fresh validation |
| Identity plus coverage/backfill | 2 | 0 | 0 | Chosen for fresh validation |

The two gained queries are `starlette_exception_registration_and_dispatch` and
`zod_metadata_attachment_registry`, both using the previously observed hybrid
K24 Qwen reranked order. Definition identity adds no gold coverage over the
coverage-only control. Those gains must be attributed to coverage/backfill.
The fast dense/hybrid Express, Zod and Jinja orders gain no complete queries in
this replay. All original single-file first hits survive these two coverage
conditions. Dependency allocation losses include Jinja's async iteration helpers;
all losses on already incomplete queries remain in the raw group ledger too.

The original freeze and all-condition run are retained in
[`selection-development-2026-10-21`](../results/selection-development-2026-10-21/findings.md).
A review identified a per-query full-corpus ambiguity scan. After moving it into
the atomic refresh, a new committed freeze replayed the exact same six conditions
in [`selection-development-v2-2026-10-21`](../results/selection-development-v2-2026-10-21/findings.md).
All **2,022 full outcome records** are identical between attempts, including
source, metadata, compact hashes and diagnostics; their compressed raw ledgers
have the same SHA256. This second attempt is an optimization equivalence check,
not additional independent quality evidence. Both attempts preserve all 337
original full-metadata and exact compact-payload controls before any variants.

An independent auditor uses its own literal compact serializer, physical-line
reader and evidence scorer. It imports no production selector, retriever, backend
or model. Audit receipts bind the historical committed producer bytes; later
test and optimization changes cannot be represented as the code that generated
the first replay. The early auditor's exact source snapshot is retained alongside
its receipt; final audits use committed auditor code.

## Native validation

The [native protocol](selection-native-protocol.md) freezes the chosen identity
plus coverage candidate before querying urllib3 2.5.0. Its independent source-only
review covers all 36 supported source files, 453 windows, 22 exact executable
targets, eight multi-file behaviors, four single-file controls and two scoped
absent queries. It reads upstream test definitions as supporting source evidence;
it does not claim those upstream tests were executed.

Dense and hybrid are measured separately in three rotated real repetitions per
arm, with a fresh native Qwen query embedding for each of 168 measured backend
calls. Index/model setup and explicit warm-up are outside warm timings. The
candidate must add a complete multi-file query, lose no previously covered group
or complete positive, preserve single-file first hits, pass every source/token
audit and stay within 25% median warm overhead. Dense must pass before the product
default changes; a hybrid-only result cannot rescue it. The study does not measure
HTTP/MCP transport latency, coding correctness, provider billing or agent savings.

All **168 measured calls** finished, using 168 actual query embeddings plus four
explicit warm-up embeddings. Index/model initialization and warm-up took 47.5
seconds. Full candidate orders/scores and compact payloads stayed identical across
all three repetitions within each arm/mode.

| Retrieval / selector | Complete positives | Complete multi-file | Full groups | Warm median | Warm P95 |
|---|---:|---:|---:|---:|---:|
| Dense / original | 8/12 | 4/8 | 17/22 | 227 ms | 255 ms |
| Dense / candidate | 8/12 | 4/8 | 17/22 | 238 ms | 262 ms |
| Hybrid / original | 7/12 | 4/8 | 15/22 | 241 ms | 278 ms |
| Hybrid / candidate | 7/12 | 4/8 | 15/22 | 248 ms | 298 ms |

There are no gained or lost complete queries or full groups, and no lost
single-file first hits. Dense preserves all four single-file controls; hybrid
covers three in both arms. Warm median overhead is 4.60% for dense and 2.93% for
hybrid, inside the latency allowance, but neither arm adds the required complete
multi-file behavior. One contained excerpt is removed and one candidate appended
in each retrieval mode's first repetition, without changing any gold coverage.
Both scoped absent queries still return `candidates`; this does not prove either
feature exists or is absent.

The [native results](../results/selection-holdout-urllib3-2026-10-21/summary.json)
retain the plan, exact source inventory, raw stage/response/vector receipts,
independent audit and all missing-group diagnoses. The next quality target is
the still-excluded complementary implementation evidence under the fixed cap;
this experiment does not justify another coding comparison or a default change.

The [independent missing-evidence trace](../results/selection-holdout-urllib3-2026-10-21/missing-evidence-diagnosis.md)
explains every miss. Across 12 missing query/group/retrieval-mode cases (eight
distinct query/group units, before duplicating baseline/candidate arms), one is
absent from the retrieved 50, one loses a complementary window of the same long
function during deduplication, seven lie beyond the eight-result prefix, two are
displaced by dependencies, and one is clipped by the character budget. None
first disappears during token packing or the new postprocessor. Distinguishing
separate definitions does not solve complementary-window selection within one
long implementation. Simply reserving ranked slots is also insufficient: the
development control already demonstrated losses of necessary dependency evidence.

## Reproduction

Use Python 3.12 and the pinned Mac runtime/model recorded in the plan, plus the
exact corpus commit and offline tokenizer bytes. The development and native
protocols explain the frozen inputs and commit barrier. Scripts refuse changed
bindings, existing outcomes and resumed attempts; use a new output directory.
The exact historical native producer is in commit `3e0747f`, before inference.
The independent auditor is separate and must be committed before it executes.

For another native measurement with current code, the same urllib3 labels are
now **observed timing/development evidence**:

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmark_selection.py \
  --dataset benchmarks/selection_holdout_queries.json \
  --corpus /path/to/clean/urllib3 \
  --model /path/to/qwen3-embedding-0.6b \
  --output results/local/selection-measurement \
  --candidate definition_coverage_backfill \
  --source-review results/selection-preflight-2026-10-21/source-review.json \
  --development-study results/selection-development-v2-2026-10-21 \
  --role development_warm_timing --freeze
# Review and commit the unchanged bound files and plan, then repeat with --run.
```

Audit saved receipts without loading any model:

```sh
.venv/bin/python scripts/audit_selection.py --mode native \
  --corpus /path/to/clean/urllib3 \
  --results results/selection-holdout-urllib3-2026-10-21 \
  --output results/local/new-selection-audit.json
```

Set `TIKTOKEN_CACHE_DIR` to the directory containing the pinned local
`cl100k_base` rank file if using a separate tokenizer cache. To reproduce the
original run's bound producer exactly, use its historical commit in a clean
checkout; a new measurement with current code cannot be represented as that
original fresh attempt. Full commands and input matrices for CPU replay are in
the [development protocol](selection-development-protocol.md).

## Verification and limits

Focused tests cover same-name Python/JavaScript/TypeScript definitions, long
overlapping windows, docstring-only gaps, same-line JavaScript declarations,
actual clipped containment, reverse-order containment, equal spans, partial
overlaps, Unicode/CRLF source, hashes, exact token boundaries, rolled-back trials,
bounded work, cancellation, cache reuse and atomic metadata publication. Backend
integration tests exercise both distinct definitions and a redundant child/parent
pair with a later real ranked implementation. Harness tests run an entire
invented 24-request workflow with fake CPU components and verify the prospective
freeze, source guards, metrics and failure receipts.

The first full suite exposed a test-isolation error: a replay guard test assumed
the entire pytest process had never imported the backend. It now tests the actual
offline guard in a fresh subprocess, matching the replay's clean-process contract.
Production and replay algorithms were unchanged by that repair. Preserved audit
source snapshots also exposed pytest discovering archived test files under
`results/`; primary test discovery now explicitly uses `tests/`. This test-only
configuration change happened after native inference and is identified against
the frozen execution blob during audit. The first native audit found a schema
mismatch: it omitted the producer's positive/multi-file denominator fields.
Its failure receipt is retained; the narrow auditor repair independently
recomputed those counts and left all producer data and quality gates unchanged.
Initial editor
helper checks also ran before TypeScript compilation completed; the correctly
sequenced run passed. The final local full run passed **2,726 core tests**, with
ten documented optional-environment skips; it collected before two final auditor
regressions were added. A subsequent focused run passed all **34 final auditor
and native-harness tests**. TypeScript compilation and all **117 editor tests**
and **61 editor helper tests** passed. The [validation receipt and logs](../results/selection-validation-2026-10-21/validation.json)
retain exact commands, counts, hashes, scope and initial failure logs. GitHub runs
the full final suite again before merge.

The benchmarks are small source-authored samples. Literal source-complete AND
coverage is useful for diagnosis, but does not establish coding correctness or
generalization. Retrieval still has upstream candidate misses and the fixed
context cap can still exclude useful implementations. Any unsuccessful fresh
gate leaves the existing production default intact. No release assets or version
bump are part of this work.
