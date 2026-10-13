# Local behavioral code search

Describe a behavior and receive relevant implementations with verified paths,
symbols, line numbers, and bounded source excerpts. The CLI and implemented MCP
`search_code` tool share a local Python backend; a VS Code extension follows.

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
4. **Persistent index (core complete; larger real-model profiling remains):**
   durable SQLite FTS5 keyword retrieval, pinned sqlite-vec exact exhaustive
   cosine retrieval, reusable document/model vector caching, atomic source
   snapshots, deletion/rename/branch handling, and stale-source rejection are
   implemented. A debounced watcher observes saved Python bytes and Git refs;
   request cancellation stops queued work and checks active model work between
   batches/scores. Real Qwen/MCP saved-edit verification and a
   [10,000-window synthetic CPU profile](../results/index-profile-2026-10-12/report.md)
   pass; large real-codebase
   indexing/search latency and memory still need measurement with the real model.
5. **MCP (adapter and first paired coding pilot complete):** one stdio
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
6. **VS Code (isolated draft):** search command, plaintext result previews, verified
   saved-file navigation, indexing status and cancellation are packaged locally
   and pass 17 CPU checks. Real Development Host testing is being prepared.
   Main integration, real-model editor workflows and temporary unsaved-buffer
   overlays remain.
7. **Beta/release (in preparation):** a fast Mac installer can omit optional H2O.
   TypeScript/JavaScript extraction and conservative static references pass an
   independent 259-test CPU gate in an isolated branch. Source-authored Express
   development and Zod holdout queries are fixed before ranking output.
   Language retrieval, installation/editor integration, a reproducible demo and
   external beta users remain.

Python is implemented first; TypeScript/JavaScript is the next language scope.
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

Isolated editor and language drafts can proceed alongside that study without
changing its runtime. After the study and audits, integrate those drafts and run
the [prospective language evaluation](language-retrieval-protocol.md), with real
Qwen inference serialized. Verify actual editor search, preview, navigation and
saved edits before preparing a portfolio demo. Unsaved overlays, broader real
coding tasks and external beta users remain later gates. Token savings have not
been established; moving to product integration does not imply that target was
met.
