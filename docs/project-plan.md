# Local behavioral code search

Describe a behavior and receive relevant implementations with verified paths,
symbols, line numbers, and bounded source excerpts. The CLI and implemented MCP
`search_code` tool and VS Code extension share a local Python backend.

This follows the agreed plan in **Choose a project for YC roles** on October 12,
2026. Use existing model weights; training and fine-tuning are outside the plan.

## Sequence

1. **Model experiment (complete):** H2O-Lightning-4B q8 on the M4, real-code
   relevance judgments, decoys, memory and cold/warm latency. Compare candidate
   ordering before and after reranking. Publish failures as well as successes.
2. **Core CLI (complete):** syntax-aware symbols, Qwen3-Embedding-0.6B candidate retrieval,
   lexical retrieval, hybrid ranking, H2O shortlist scoring, exact locations.
3. **Retrieval quality (first benchmark complete; known misses remain):** symbol
   deduplication, whole-line character/token budgets, conservative static
   dependency context, and an optional calibrated weak-match policy are
   implemented. The frozen source-reviewed set now has 106 queries across five
   repositories, including multi-file AND behaviors and a Rich repository
   holdout. Dense context delivers complete evidence for 83/88 positive queries
   overall and 18/20 held-out positives, including only 6/10 multi-file behaviors
   overall. The optional rejection policy rejects 3/4 absent Rich cases and 1/20
   real behaviors. [Results and failures](../results/retrieval-quality-2026-10-12/findings.md)
   preserve the [frozen protocol](retrieval-quality-protocol.md).
4. **Persistent index (core and real-model validation complete at tested scale):**
   durable SQLite FTS5 keyword retrieval, pinned sqlite-vec exact exhaustive
   cosine retrieval, reusable document/model vector caching, atomic source
   snapshots, deletion/rename/branch handling, and stale-source rejection are
   implemented. A debounced watcher observes supported saved source and Git refs;
   request cancellation stops queued work and checks active model work between
   batches/scores. Real Qwen/MCP saved-edit verification and a
   [10,000-window synthetic CPU profile](../results/index-profile-2026-10-12/report.md)
   pass. The [real Qwen language study](../results/language-quality-2026-10-13/findings.md)
   indexes 107 Zod production files into 1,802 windows in 108.23 seconds;
   reopening takes 2.06 seconds and reuses every document vector without inference.
   Actual VS Code saved-edit, watcher-before-query, rename, deletion and invalid
   source checks also pass. Larger repositories remain unmeasured.
5. **MCP (adapter and repeated multi-file study complete):** one stdio
   `search_code` tool uses the resident service with strict schemas, cancellation,
   eight-result context, and a default 4,096-token compact `cl100k_base` JSON cap.
   Six paired repairs now have provider-reported usage, completion time, frozen
   checks, patches, and independent review. All selected checks pass, but none
   of the MCP-assigned agents used the tool. Both Click fixes suppress atomic
   commit failures in a supplemental probe. The
   [availability pilot](../results/coding-workflows-2026-10-12/report.md) therefore
   does not demonstrate a retrieval benefit; the local context counter remains
   separate from actual agent usage.
   A [separate MCP-first follow-up](../results/coding-workflows-mcp-first-2026-10-12/report.md)
   runs three more pairs with identical conditional search-first prompts.
   All three MCP agents use retrieval; both conditions pass the ten-method gate,
   and both Click patches retain the commit-error defect. MCP uses 7.0% more
   total tokens and 14.8% more agent time over these reused tasks. Its condition
   median token count is 6.8% lower; that is not the aggregate workload change.
6. **VS Code (integrated; actual saved and dirty workflows pass):** search,
   plaintext captured previews, verified navigation, status and cancellation
   pass in the actual VS Code host with resident Qwen. Explicit unsaved searches
   bind all supplied buffers and reject navigation after any input changes;
   ordinary searches remain saved-source searches. Request-only overlay indexes
   leave the saved index unchanged. The [host receipts](../results/editor-workflows-2026-10-13/findings.md)
   separate integration checks from retrieval quality. Separate
   [native guard checks](../results/editor-acceptance-2026-10-13/findings.md)
   observe actual false trust, multi-folder removal and offline errors with an
   authored HTTP fixture. Ordinary editor-profile activation remains untested.
7. **Portfolio preview (published; external beta remains):** the fast Mac installer omits optional H2O;
   a new environment passed dependency checks and real Qwen inference. These
   runs used available user-level caches, rather than a clean machine. Python,
   JavaScript and TypeScript are integrated. The
   [real HTTP/MCP demo](../results/demo-2026-10-13/findings.md) passes, and the
   [GitHub preview](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.0-preview.1)
   ships the exact installed and tested VSIX. External beta users and a licensing
   decision remain.

Python, TypeScript and JavaScript are implemented.
C# and non-Mac deployment follow validation of the core. Serialize GPU work and
reuse resident model processes. Dense retrieval remains the fast default, with
H2O available as an optional shortlist reranker. Static dependencies are context
references, and weak-match rejection is not proof that a behavior is absent.

## Evaluation gates

H2O must improve delivered results enough to justify latency. The first pilot is
development evidence, not a held-out benchmark. The expanded 106-query set now
reserves Rich as a repository holdout. The frozen fast-retrieval conditions meet
the initial 90% candidate-coverage and sub-five-second warm latency aspirations
on this authored sample: held-out dense coverage is 19/20 at depth 50, with
175 ms P95 HTTP for dense context. These are sample-specific outcomes. H2O
comparisons remain the separate Flask/Requests pilots. Measure candidate coverage,
useful evidence within the output budget, stale results, memory, and cold/warm
latency. Initial aspirations: 90% candidate coverage, warm p95 below five seconds,
30% lower median paid-agent tokens with comparable completion. Actual usage is
now measured, but this target is not established. Local inference has no API token charge, but tool calls,
returned excerpts, and follow-up reads still consume the agent's tokens.

## Next milestone

The [workflow investigation](../results/workflow-investigation-2026-10-13/findings.md)
records repeated source reads, cache differences and runtime confounds without
claiming an exact causal token attribution. The MCP-first guidance and optional
single-text payload are implemented. The new
[multi-file protocol](multifile-workflow-protocol.md) freezes four fresh features,
two paired repetitions, 41 selected check methods and original upstream checks.
The runtime preflight resolves the observed AnyIO/Trio dependency mismatch;
collaboration is explicitly disabled for the trials. Separate transport checks
verify an actual single-copy MCP call and loopback HTTP.

The [16-attempt study](../results/multifile-workflows-2026-10-13/findings.md)
and independent [integrity audit](../results/multifile-audit-2026-10-13/audit.json)
are complete. Three matched normally completed pairs use 9.23% more gross tokens
and 18.62% more uncached input with MCP, with 2.20% less agent time and 1.59% more
full trial time. Six receipts are unavailable because of three timeouts and
three provider-capacity failures; full-workload savings remain unknown.
Supplemental [patch review](../results/multifile-workflows-2026-10-13/patch-review.md)
finds six distinct defects across seven attempts, including three formally
completed-correct attempts and five holes shared by the references. These
post-output findings leave the frozen scores unchanged. All attempts, patches,
actual receipts and missing-data limits are preserved; token savings are unmet.

The frozen [language evaluation](../results/language-quality-2026-10-13/findings.md)
is complete: 30 distinct source-authored queries, 270 timed HTTP requests, and
independent source/token audits. Dense retrieval delivers complete evidence for
10/12 Express and 8/12 Zod behaviors. It finds all needed candidates for 11/12
Zod behaviors at depth 50, but delivers complete evidence for none of its four
multi-file behaviors under the fixed eight-excerpt budget. Express is development
evidence; Zod holds out retrieval parameter selection, after parser eligibility
was inspected. All absent queries return decoys without an abstention policy.

The reproducible demo and verified local preview are published. An offline
[packing experiment](../results/context-packing-development-2026-10-13/gold-scoring.md)
skipped contained excerpts and backfilled spare capacity. It changed 28/90
delivered outputs and improved three evidence ranks, but recovered no complete
behavior and lost none. The default packing policy remains unchanged.

Use fresh development queries to improve evidence delivery and a new
untouched holdout to assess those changes. The observed Zod misses are now
development evidence for future tuning. Broader coding tasks and external beta
feedback follow. Token savings remain unestablished; product integration does
not imply that target was met.
