# Flask implementation-search development pilot

`flask_queries.json` contains 12 behavior queries with implementation targets and two queries for features absent from the corpus. The corpus is [Pallets Flask](https://github.com/pallets/flask), pinned to commit `d086db856be187255b8ec61ef409357393020f32`. Only Python implementation files under `src/flask` are in scope; tests, documentation, dependencies, and extensions are outside it.

This is a small, developer-authored development pilot. Queries and gold targets were written by reading the pinned source before inspecting retrieval outputs. It is not held out, representative of real users, or sufficient evidence of general code-search quality. Natural-language queries often share concepts and vocabulary with source comments and docstrings. If indexed chunks include those, a hit may reflect prose as well as executable code. Record that setting alongside results.

## Labels

Each query has a stable `id`, a natural-language `query`, `kind`, `targets`, and source-review `notes`. A target is the repository-relative Python file `path` plus its AST-qualified `symbol`, such as `Flask.make_response`. Qualification follows nested classes/functions within a file, without a package prefix. Targets and decoys were checked against the pinned source's Python AST.

Each gold target also has `evidence_lines: [start, end]`, an inclusive, one-based range in the pinned file containing executable code that demonstrates the requested behavior. These ranges were chosen from source without inspecting model outputs and exclude type-only overloads and docstring-only regions. A retrieved window must match the target and contain the entire evidence interval to qualify as an implementation hit. The evidence annotation must not be included in retrieval input. For the separate curated scoring probe, select the earliest target window containing the whole evidence region. All 12 golds have such a window with a 160-line maximum and 40-line overlap. Some behaviors span several branches, especially response conversion; keep the retrieved code available for semantic inspection as well as checking this mechanical evidence gate.

The targets identify direct implementations of the requested behavior. A wrapper that merely forwards to another function, a registration API that records work for later, or a supporting helper is not automatically relevant. Each present query includes two `decoys`: plausible related implementations that do not fully satisfy the specific request. They are diagnostic annotations and should never be passed to the retrieval model.

`stream_with_context` has type-only overload declarations and an executable definition with the same symbol. Index the executable definition; do not count matching an overload stub as finding its implementation. Its nested generator is contained in that implementation and need not be returned as a separate target. The extractor also excludes unimplemented stubs. Before inspecting model outputs, the HEAD-fallback query's unimplemented `View.dispatch_request` decoy was therefore replaced with `View.as_view`, a callable-construction implementation that delegates dispatch without implementing method selection or HEAD fallback.

The two `absent` cases ask for built-in CSRF-token validation and request-rate limiting. Source review and searches for CSRF/forgery and rate-limit/throttle terminology found no such implementation within `src/flask`. These labels make no claim about Flask extensions. They intentionally have no targets.

## Reading results

For the 12 `present` queries, inspect the target's rank and whether a plausible but wrong neighbor outranks it. Report retrieval metrics only over those present queries, with the corpus revision, chunking rules, model revision, quantization, and document/query formatting recorded. Keep a compact table of actual ranked symbols so the metric can be audited.

A separate relevance-scoring check can compare the labeled implementation with its two decoys using a fixed ordinal rubric: 0 = unrelated, 1 = context, 2 = supporting or partial implementation, and 3 = direct implementation. The labels select candidates for that diagnostic only; end-to-end retrieval must search the entire indexed corpus without injecting the golds or decoys.

For `absent` queries, inspect their highest similarity scores and returned implementations separately. Nearest-neighbor retrieval always returns something; an unrelated top hit does not prove that an implementation exists. This pilot does not establish a rejection threshold, and the two negative cases should not be averaged into positive retrieval accuracy.

Freeze this file for the first experiment. If failures prompt revised queries or labels, retain the original results and describe the later changes as development iteration. A future quality evaluation should use independently authored, unseen queries across additional repositories and manually reviewed relevance judgments.
