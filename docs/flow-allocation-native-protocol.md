# Prospective native validation of shared-flow allocation

This profile is conditional on the separately frozen
[shared-flow development study](flow-allocation-development-protocol.md).
Before reading any fresh dataset, source, query, gold or checkpoint metadata,
the producer must verify a qualifying **committed** development summary and its
actual independent audit. That audit must use
`candidate_planner_or_producer_modules_imported: false`; an older audit field is
not substituted. All 365 legacy controls, 337 prior coverage controls, 730
original control arms and 1,460 historical negative references must remain bound.
There must be no individual full-group, complete-positive or single-file-hit loss
against either control and at least one additional distinct complete query
against coverage. A qualified flag alone cannot advance the candidate.

Only `shared_flow` may advance. Its exact implementation and prefix-stop rule
are fixed before fresh preparation. The old planners, guards and historical
records remain unchanged. No condition name is aliased to a previous union or
allocation condition. This profile has its own native loop and capture hooks;
the previous union loop's preservation assumptions do not apply to a valid
shared-flow guard fallback.

## Frozen source and experiment

Use the previously authored, independently source-reviewed TanStack Query core
repository sample, with the complete production-source and excluded-file
inventories, canonical definition map, parser pins, evidence hashes and license
notices from the existing source-only review. Source-only preparation begins only
after the development gate above and after the native producer, tests, protocol,
independent auditor and its tests are committed unchanged. The producer checks
that prerequisite before reading fresh data or checkpoint metadata.
No fresh retrieval, rule activation or outcomes
may be examined before the native plan and all bound bytes are committed.
The sample contains 14 queries: eight multi-file positives, four single-file
controls and two scoped absences. Every required OR/AND evidence group retains
the authored qualified-symbol, full physical-span and exact-byte/hash criterion.
Historical path/span development metrics remain separate.

Freeze the actual checkpoint weights/config/provenance/tokenizer hashes;
embedding, parser and runtime metadata; source and exclusion membership; full
snippet/definition inventory; complete conservative static graph; dataset and
source-review/validator artifacts; qualified development results/audit and their
source blobs; implementation/tests; this protocol; independent native auditor
and its tests; extraction/index policy; caps; schedule and gates. The native
`source-inventory.json` contains `inventory`, `definition_keys`, and `graph_edges`
records with `caller` and ordered `targets`. `binding.graph_sha256` hashes the
canonical newline-terminated graph JSON. The resident backend's graph and
canonical map must exactly match this frozen inventory.

Three arms are fixed: actual production `legacy`, previous research
`coverage_control`, and `shared_flow`. Dense and hybrid are separate modes.
All use the same resident pinned Qwen embedding model and saved index, 160/40
extraction, 50 raw candidates, static dependencies, eight excerpts, 12,000 source
characters and 4,096 exact compact JSON `cl100k_base` tokens. No query rewriting,
new retrieval, rescoring, additional model call or source union is introduced.

For every query and each of three repetitions, rotate the six arm/mode pairs by
`(query_index + repetition - 1) % 6`. This yields **252 measured searches**.
Perform exactly one generic warmup per arm/mode beforehand: six additional
actual query embeddings. Retain each warmup identity and actual query/document
counter delta; initial indexing may encode documents, measured requests may not.

## Measurement and actual receipts

Time the complete resident `backend.search` call, including its fresh query
embedding, source checks, actual selector, source-plan copies, exact packing,
lazy reference packing and guard. Setup, checkpoint hashing, six warmups,
diagnostic iterator draining, deep copies and report serialization are outside
the measured interval. The lightweight capture wrappers retain live references;
they do not duplicate receipts or consume unused candidate streams in the timer.
The underlying real planner and guard's normal copy costs remain measured.

Every measured candidate must execute one actual shared-flow planner, one reused
base planner, one original source guard and one same-input coverage reference.
The controls execute none of these candidate hooks. Capture the actual outer
flow receipt, actual base receipt, projected plan carried through the backend,
reference/proposal operands, guard decision and final source. Require exact plan
identities, rather than constructing a replacement receipt after the call.

The backend updates operational timing fields after its guard returns. Compare
the chosen response's exact compact payload, full result rows, source/provenance,
character/token/budget fields and `_selection_backfill` diagnostics. Operational
timing/index metadata are excluded from this equality. A rejected candidate
returns the coverage reference; it need not preserve source from the rejected
proposal. Check that the reference equals the **separately measured** same-query
coverage control in both compact text and this complete source-context view.

Each measured row records actual query and document embedding counter deltas:
exactly one and zero respectively. Retain every query vector and byte hash,
original raw rank/score order, selected/eligible stages and origins, complete
canonical map and ambiguity set, pre/postprocessor rows, consumed and unused
candidate streams, full guard operands/receipts, compact bytes/hash/tokens,
qualified evidence ranks and monotonic wall time. Vectors must remain identical
across both modes, all arms and repetitions for the same literal query. Raw
rank/score orders must remain identical across arms/repetitions in each mode;
compact payloads must remain identical across repetitions in each arm/mode.

Report setup separately and same-mode median/P95, paired deltas against both
controls, clause activation, actual admission, guard acceptance and changed
compact contexts separately. Latency is resident search latency, not HTTP/MCP
transport, coding completion time or provider-billed token usage. Avoid concurrent
heavy tests, audits or model work during actual measurements. Retain and disclose
sleep, interruption and failures.

## Attempt and qualification

Require unchanged committed bindings and strict freeze/execution ancestry before
claiming one attempt and before model/index work. Preserve `run-started.json`,
every completed trace flushed before the next request, and any failure receipt.
Never overwrite, retry or resume an attempted output directory. Setup failures
are claimed attempts too. Repairs require a separately named frozen attempt with
the earlier evidence retained. An independent committed auditor reconstructs
canonical and legacy dependency allocation, shared-flow decisions, ordinary
packing/backfill and source-guard choices without importing a producer or
selector. The frozen conservative graph extractor is the declared shared parser
boundary. It independently verifies all literal sources, scores/provenance,
exact tokens, inventories, schedules, actual counter/vector receipts, commit
bindings, every qualified evidence group and all quality/latency gates.

For each mode, the candidate must preserve every individual full evidence group,
complete positive and single-file first hit against **both** controls, including
already incomplete queries. It must add at least one **net complete multi-file
query** beyond coverage, and its warm median must be at most 1.25 times each
control's same-mode median. Dense is primary; hybrid cannot rescue failed dense
quality or latency. Report every regression and both gates even when they fail.
No automatic default change or release follows this test. Promotion also needs
client/schema/cache/cancellation checks and context-budget sensitivity validation.
Once observed, this codebase becomes development evidence for later changes.

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmark_flow_allocation.py \
  --dataset PATH --corpus PATH --model PATH --source-review PATH \
  --definition-inventory PATH --development-study QUALIFIED_COMMITTED_PARENT \
  --output NEW_ATTEMPT --freeze
# Review and commit all unchanged native bindings and the plan first.
# Run the same arguments with --run after the committed independent auditor exists.
```
