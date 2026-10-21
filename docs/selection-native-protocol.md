# Native response-selection validation

This protocol is fixed before any retrieval, indexing or model execution on the
new urllib3 corpus. It follows the separately committed
[development replay](selection-development-protocol.md). Only a candidate that
retains every baseline-covered full evidence group and complete positive in that
replay, preserves single-file first hits, and adds a distinct complete query may
advance. Record the chosen condition and reason in the native frozen plan before
any fresh outcomes; do not try several conditions on the holdout and choose a
winner afterward. Retain all rejected development conditions.

The fresh repository is urllib3 2.5.0 at
`aaab4eccc10c965897540b21e15f11859d0b62e7`, all saved supported source under
`src/urllib3`, with no source exclusions. The
[source-only author review](selection-holdout-source-review.md) and independently
authored `results/selection-preflight-2026-10-21/source-review.json` bind the
14 queries, 22 exact executable evidence targets, complete 36-file source
inventory, 453 eligible windows, upstream tests read, scoped absence judgments
and license notices. Neither review used rankings, selection activations or
models. This is repository-level evaluation independence, not model-pretraining
isolation or an independent author sample. Once outcomes are visible, this set
becomes observed development evidence. No gold repair or tuning on those
outcomes can preserve its fresh status.

Compare the unchanged `legacy` selection/packing control with exactly the
development-qualified candidate. Use the same resident pinned
Qwen3-Embedding-0.6B model, same corpus/index, query text, candidate depth 50,
retrieval order, static graph, and source extraction (160 physical lines with
40-line overlap). Run dense and hybrid separately. Each response is bounded by
8 excerpts, 12,000 actual source characters and 4,096 exact compact JSON
`cl100k_base` tokens. Dependencies remain enabled. No H2O/Qwen reranking,
multifile clause retrieval, query rewriting, larger shortlist or context cap is
introduced. Definition identity is transient AST metadata and must not change
stored snippet identities, source or embedding-cache compatibility.

The harness freezes source, labels, review, all producer modules, tests, this
protocol, model metadata and weight hashes, tokenizer bytes, runtime versions,
settings, corpus identity and candidate before execution. Commit those unchanged
files and the plan first. A run must reject changed bindings, uncommitted files
or preexisting run/failure receipts. Claim one attempt before any model work;
retain failed attempts and use a new plan/directory for any repair. Source-only
freeze and synthetic fixture tests produce no holdout rankings.

Index/model initialization and explicit warm-up happen outside timed measured
requests. Measure the complete resident backend search call with a monotonic
clock, including the query embedding, retrieval, selection, source guards and
exact context packing. Each of the 14 queries is requested in both arms and
both retrieval modes for three actual repetitions, with deterministic rotated
arm/mode order. Report all 168 measured calls, per-mode medians/P95 and paired
differences; do not treat repetitions as independent queries. Do not call this
HTTP/MCP transport latency, startup latency, coding completion time or provider
token billing. If the laptop sleeps or an execution fault occurs, preserve the
attempt and disclose or repeat it under a new frozen attempt.

Retain the full candidate source identities and scalar order, canonical
definition mapping, selected and eligible stages, dependency provenance,
already-budgeted response before postprocessing, final returned rows, exact
compact text/tokens, source/character counts, query/arm/repetition and timings.
All measured query embeddings must be actual calls. Verify repeated rankings
and delivered evidence; distinguish real nondeterminism from metadata timing.
The independent auditor must not import the producer, selector, backend,
retriever or model. It checks literal pinned source bytes, inclusive physical
line bounds, hashes, complete evidence-group OR/AND coverage, compact formatting
and exact token counts, limits, schedules, binding history and every gain/loss.
Coverage/backfill additionally must preserve each preprocessed actual excerpt
inside one retained actual excerpt and leave retained old rows unchanged.

Report dense and hybrid separately. For each mode, the prospective quality and
latency gate requires:

- At least one net additional complete multi-file query out of eight.
- No individually lost baseline-covered full evidence group, complete positive,
  or single-file first hit, including partial-query group regressions.
- Every raw response passes source/provenance/budget audits.
- Candidate warm median backend latency is no more than 25% above the same-run
  control median for that mode.

Dense is the product default, so a hybrid-only improvement cannot qualify a
default change. A failed gate leaves the existing default intact. Publish all
gains, regressions, absent-query outputs and containment activation counts even
when a gate fails. If results justify a change, any later coding comparison must
use stronger behavioral checks and separately measured actual usage; source
coverage alone is not coding correctness or demonstrated token savings.
