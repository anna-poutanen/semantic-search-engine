# Prospective native qualification of the state-write guard

This protocol follows the independently audited development pass committed in
`597c5ed7235bcc1cc2ef95a8b9af0c0eb57a32ae`. Before that commit, attrs source,
queries, index and retrieval remained unobserved by the rule designers in this
phase. Earlier source authors/reviewers read the source and authored labels;
the old freshness review discloses the draft and handoff exposure. This is a
fresh repository evaluation within this project, not model-pretraining isolation.

## Fixed hypothesis and candidate

The exact `role_state_write_v1` guard, unchanged semantic-v1 selector, live feature
provider and existing coverage policy are fixed by the development source freeze
`b86eeb03c1c1e788a4a7497f1e0d0b41e6c367ca`. No new selection rule, threshold,
mode routing, query decomposition, source clipping, score weighting or retry is
permitted after source preparation or outcomes. A failure is retained. The native
experiment cannot alter the development rule or relabel earlier variants.

The native runner and independent auditor must verify the actual committed
393-outcome/201-unit development pass before reading any fresh dataset, source,
checkpoint metadata or tokenizer bytes: 786 exact controls, 3,734 historical
negative references, zero individual losses against both controls and at least
one additional distinct complete multi-file behavior beyond coverage. Bind its
full prepared source/input/parent closure, reports, original controls, old native
archive, audit correction and raw outcomes. Checking only a success flag is
insufficient. An unsuccessful or uncommitted development parent blocks fresh work.

Commit the new native driver, independent auditor, invented tests and this protocol
before entering source-only attrs preparation. Then bind the exact reserved attrs
25.3.0 source/revision, unchanged 14-query labels, full extraction inventory,
canonical definitions, conservative graph, exclusions, independent source review,
freshness review and literal evidence intervals/hashes. Preparation does not
index, embed, rank, run a selector or compute gold coverage. No query or label
can be replaced because it seems difficult or unhelpful to the fixed rule.
Commit and push that complete native plan and source inventory before inference.

## Conditions and measured work

Use one resident pinned local Qwen3-Embedding-0.6B model and one new persistent
SQLite index with the source-reviewed source root and exclusions. Bind all
checkpoint files, model metadata/fingerprint, actual runtime/package/parser
versions, tokenizer rank bytes, product and transitive research source. Use the
existing batch size four, maximum 8,192 tokens and padded batch-token limit 8,192;
weights remain their original model format. There is no new reranker or score
normalization. One cross-worktree GPU lease prevents concurrent native experiments.

For both dense and hybrid, the three fixed arms use the same raw candidate pool,
retrieval settings and eight-excerpt, 12,000-source-character, 4,096-compact-token
caps:

- `legacy`: original ranked selection and unchanged context postprocessor.
- `coverage_control`: current definition selection and source-preserving
  coalescing/backfill, with no semantic provider or role guard.
- `candidate`: the same current coverage baseline, exactly one live semantic-v1
  proposal and exactly one unchanged terminal role guard. Preserve the actual
  coverage operand, proposal, final result, features, provider and both receipts.
  No second proposal, rescue allocation or subsequent backfill follows rejection.

The candidate uses the **actual executing full-query vector**, not another
embedding of that full query. Obtain document vectors only by the current model
fingerprint and exact source-bound IDs from the resident index. Each active
candidate query separately embeds its literal clauses through the current live
batch API, even when that query was measured previously. Do not reuse saved
feature captures, query/clause feature caches or vectors from another request.
The full query still executes one actual singleton embedding for every request
in all arms. Record actual API calls and started/completed/failed native forwards,
text roles/order, batch/padding work and vector/provenance bindings separately.
Measured requests execute no document inference. Indexing is setup work.

Execute exactly six explicit setup warmups: the fixed generic query
`Find code that handles input and returns a result`, once for each arm/mode.
These are not quality probes and cannot determine later rules or query choice.
Use all 14 fixed queries, three arms, two retrieval modes and three repetitions:
252 measured `backend.search` calls. For each repetition (1..3) and dataset query
in original order, rotate `(legacy,dense)`, `(coverage_control,dense)`,
`(candidate,dense)`, `(legacy,hybrid)`, `(coverage_control,hybrid)`,
`(candidate,hybrid)` by `(query_index + repetition - 1) % 6` and execute all six.
The first repetition defines quality; all repetitions contribute warm timing.

Time complete actual resident `backend.search` with `perf_counter`. Include the
full-query embedding, retrieval, ordinary source verification, baseline packing,
extra clause inference, resident document lookup, feature validation/hashing,
semantic search and source copies, and role parsing/checks. Setup, model load,
indexing, six warmups, diagnostic deepcopy/iterator draining, serialization and
durable ledger I/O remain outside the timer. Normal algorithmic copies remain
inside. Stop other heavy local tests before measured native execution. Record
power/machine state. Do not call prepared-feature CPU replay a warm native result.

## Audit and gates

The independent auditor must not import producer selectors, the native runner,
backend, store or numerical/model runtimes. It may reuse frozen independent
source/packing/semantic/role helpers and the existing exact source-bound graph
allowlist: `semantic_search`, `semantic_search.relations`,
`semantic_search.javascript_relations`, `semantic_search.javascript`,
`semantic_search.languages` and `semantic_search.source`. Those shared pure
source/grammar helpers verify the captured conservative graph; they do not
establish independent graph implementation. No additional product module is
allowed. Reconstruct both baseline source responses,
semantic features/proposal/receipt, role classification/decision/receipt and actual
compact payload of every native outcome. Verify original raw rank/scalar/score,
full canonical source intervals, nested scope order, provenance, exact token and
character caps, forward/vector/provider bindings, complete 252-call matrix and
six warmups. Verify bit-identical executing query vectors and same raw ranks and
scores across arms, modes where applicable and repetitions; compact output must
repeat within each arm/mode. The candidate's coverage operand must equal the
separately measured coverage source/compact view. Only operational timings/index
statistics may differ after the backend updates them.

Bind the complete immutable native plan/source inventory and execution under
full Git SHAs, executing module paths, working/freeze/execution/audit-HEAD blob
checks, ancestry and commit-before-execution chronology. Preserve all original
parent proof. Reject a changed source/checkpoint/tokenizer/runtime, failed or
partial run, missing case, unexpected forward, unstable raw pool or altered label.
Use an exclusively claimed durable attempt, fsync the directory entry before
work and each gzip row before continuing; finalize the gzip trailer before its
hash. Retain failures, partial ledgers and lease release receipts. No overwrite,
resume or silent retry; a technical correction needs separately committed source
and a separately named attempt, with the prior attempt preserved.

Require **zero individual lost required groups, complete positive behaviors or
single-file first hits against both controls**, including already-incomplete
queries. Require at least one additional complete multi-file behavior over
coverage on this fresh sample. Report absent-query returned code as candidates,
not proof of feature absence. Report all per-query gains/losses and totals.

For each mode report median, P95, paired deltas and candidate/control ratios using
all 42 calls per arm. The warm-median gate is at most **1.25 times each control**.
Dense is the primary qualification mode and must pass quality and latency itself;
hybrid is a separate diagnostic and cannot rescue a dense failure. Report clause
work and prepared feature-allocation overhead separately, without substituting
either for full new-query cost. Repeated queries are timing repetitions, not
independent quality samples. No favorable-mode subset, new aggregate-only gate
or post hoc threshold may replace these requirements.

A passed integrity audit is not automatically a quality or latency pass. No
production-default change, release or coding-agent comparison follows
implicitly. Coding correctness, time and actual provider-token savings require a
separate subsequently frozen workflow study after qualified retrieval gains.

## Source-only receipt compatibility correction

The first native source freeze, `0b651ef32cb6ca2444a016587ce1842854696244`,
preceded the first attrs source-only preparation. That preparation stopped
because the unchanged attrs review uses its original flat receipt schema,
whereas the reused native helper expects a later nested schema. No plan,
index, model forward, retrieval outcome or quality score was produced. The
original failure log and receipt are retained in
`results/role-native-preflight-2026-11-03/prepare-attempt-1`.

The compatibility correction validates the original published flat semantic
review and its author/source/protocol bindings. Any legacy helper projection
exists only in memory; it cannot replace or relabel an original receipt on disk.
Both original receipts, dataset, labels, revision, extraction, semantic selector,
role guard, quality gates and latency gates stay fixed. New invented tests and
independent checks must precede a separately committed corrective source freeze
and a separately named native plan. This correction follows source-only exposure
and precedes all native outcomes; it is a technical input repair, not a new blind
rule design or an independent confirmation.
