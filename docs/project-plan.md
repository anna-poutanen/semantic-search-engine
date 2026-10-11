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
   source checks also pass. The [real Django profile](../results/django-index-profile-2026-10-14/findings.md)
   now validates 9,097 windows: initial refresh takes 395.93 seconds, all nine
   edit/delete/rename/reopen stages pass, and warm dense/hybrid backend P95 is
   about 0.72 seconds. Its 3.89-second fresh CPU reopen covers durable indexing
   and source extraction, rather than model/service startup. Final database
   size is 144.5 MB after worker exit; larger scales and concurrent throughput
   remain unmeasured.
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
   authored HTTP fixture. The [ordinary installed-profile acceptance](../results/ordinary-vscode-profile-v3-2026-10-14/findings.md)
   also passes: public-command activation, defaults, previews/navigation and
   Restricted Mode with zero product HTTP. Its zero-model fixture and explicit
   trust-disabled trusted phase remain separate from the real-model studies.
7. **Portfolio preview (published; external beta remains):** the fast Mac installer omits optional H2O;
   a new environment passed dependency checks and real Qwen inference. These
   runs used available user-level caches, rather than a clean machine. Python,
   JavaScript and TypeScript are integrated. The
   [real HTTP/MCP demo](../results/demo-2026-10-13/findings.md) passes, and the
   [GitHub preview](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.0-preview.2)
   ships the updated exact installed and tested VSIX, including optional Qwen
   controls. External beta users and a licensing
   decision remain.

Python, TypeScript and JavaScript are implemented.
C# and non-Mac deployment follow validation of the core. Serialize GPU work and
reuse resident model processes. Dense retrieval remains the fast default, with
H2O and Qwen available as optional shortlist rerankers. Static dependencies are context
references, and weak-match rejection is not proof that a behavior is absent.

## Evaluation gates

Rerankers must improve delivered results enough to justify latency. The first pilot is
development evidence, not a held-out benchmark. The original expanded 106-query set
reserved Rich as a repository holdout; those observed queries are now development
evidence for subsequent experiments. The frozen fast-retrieval conditions met
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

A separate [clause-fusion development test](../results/clause-fusion-development-v2-2026-10-13/independent-findings.md)
completed all 60 planned conditions with fresh query embeddings. It recovered no
complete misses, increased query API calls from 30 to 56, and lost one Zod
candidate-complete behavior at depth 50. Delivered completeness stayed 10/12
Express and 8/12 Zod. The initial source-gate failure made zero model calls and
is preserved separately. This rule remains experimental.

The [Koa source preflight](../results/koa-clause-preflight-2026-10-13/source-review.md)
verifies six positives, three multi-file AND behaviors and two scoped absent
queries without inference. None of those naturally authored queries activates
the narrow clause rule, so its comparative applicability gate fails. The texts
were not rewritten to force activation. That clause experiment remains blocked;
the unchanged queries were subsequently used in the separately authorized,
prospectively frozen Qwen reranker study below.

The separate [Qwen reranker prototype](qwen-reranker-experiment.md) now has a
real Mac [development probe and independent audit](../results/qwen-reranker-case-v2-2026-10-13/independent-findings.md). On one explicitly selected Express miss, it scored
all 50 retained candidates and delivered all three required groups under the
same eight-excerpt, 12,000-character and 4,096-token limits; dense delivery had
covered two groups. The isolated run took about five seconds including weight
verification, loading, tokenization, scoring and disposal. This is one chosen
case, with no numerical PyTorch parity proof, broader quality, warm HTTP P95 or
paid-token conclusion. The original observation-wrapper failure scored zero
pairs and remains preserved separately. The production default is unchanged.

The [full Qwen reranker study](../results/qwen-reranker-full-2026-10-13/findings.md)
is complete: 144 unique queries across eight repositories, three actual repeated
passes, 1,728 searches and 43,200 fresh benchmark pair forwards. With the original
context packer and all 50 retrieved candidates scored, complete delivered evidence
improves from 107 to 110 of 118 positives for dense and from 103 to 111 for hybrid.
Dense has four complete gains and one regression; hybrid has ten gains and two
regressions. Multi-file completion improves from 11 to 13 of 21 for dense and
from 13 to 14 for hybrid. Fresh Koa improves from 5 to 6 of 6 with hybrid, while
dense stays at 6 of 6. The primary full-source-span metric is independent of symbol
identity and must not be substituted for historical symbol-dependent percentages.

The latency tradeoff remains unresolved. Warm median/P95 for the measured
in-process pipeline is 0.283/0.675 seconds for dense versus 7.345/16.592 with Qwen,
and 0.290/0.856 for hybrid versus 9.461/17.049 with Qwen. These include query
embedding, retrieval, the scorer's loopback HTTP hop and packing, but exclude model
startup/index setup and the complete production HTTP/MCP route. All absent queries
still return candidates. The 50-candidate sequential scorer remains experimental;
the production dense default stays unchanged.

Shortlist and batching validation is now complete. The
[certified batching study](../results/qwen-batch-parity-supervised-2026-10-13/independent-findings.md)
excludes B2/B4/B8 because scores and strong-margin orderings fail fixed parity
limits; production uses original singleton B1 scoring. The
[full shortlist confirmation](../results/qwen-shortlist-development-2026-10-14/independent-findings.md)
completes 1,728 searches over all 144 development queries. Dense K32 completes
110/118 positives and hybrid K24 completes 112/118, retaining every historical
K50 complete result. Hybrid K24 covers one fewer required group overall because
it trades two partial Zod groups for a complete Click case; its warm median/P95
is 4.071/7.634 seconds.

The frozen [HTTPX holdout](../results/qwen-shortlist-httpx-2026-10-14/independent-findings.md)
completes 225 searches on 15 newly authored queries. Hybrid K24 retains every
same-run K50 complete result and completes one additional behavior. Against
fast hybrid, completeness stays 8/12; multi-file completion rises 1/5 to 2/5
while required-group coverage drops 22/28 to 21/28. Warm median/P95 is
2.967/4.492 seconds versus 6.636/10.617 for same-run K50. All groups retrieved
in the full hybrid pool survive its prefix; the remaining delivered misses
point to ranking and packing. These mixed outcomes support an optional
[Qwen production adapter](qwen-reranker-integration.md), with fast dense
retrieval unchanged as the default.

The [three-condition coding comparison](../results/qwen-coding-workflows-2026-10-14/findings.md)
is complete: three observed larger feature tasks, two balanced repetitions,
ordinary tools versus fast MCP versus Qwen MCP. All eighteen attempts and
independent patch reviews are published. Each condition completes 2/6 correctly,
all on Starlette. A fast-MCP Requests snapshot passes strict checks but times out
without completed-turn usage; its tokens remain unknown. One baseline Click
attempt retains a provider 503 error and is excluded from normal comparisons.
For the two matched correct pairs, Qwen versus fast MCP uses 18.52% more gross
tokens, 21.30% less uncached input and 0.72% more full trial time. Exact cache
accounting explains the count difference without identifying a causal effect or
bill. The strict gates, upstream compatibility, supplemental defects, raw probes
and reference/checker limitations are preserved. The 30% token target is unmet.

The [fresh isolated optional-reranker installation](../results/qwen-installer-validation-2026-10-14/verification.json)
passes with all 34 package records and full checkpoint verification. Its actual
installed runtime passes all 416 original singleton score/tokenizer parity pairs
and real HTTP/MCP saved-edit checks. These validations use existing Mac caches
and do not establish clean-machine installation or official PyTorch parity.

The [context-allocation development replay](../results/context-allocation-development-2026-10-14/findings.md)
is complete: all 159 original eight-excerpt controls reproduce exactly, followed
by 954 CPU outputs across six settings. Twelve excerpts with dependencies retain
every baseline complete result and improve development completeness 112 to 113
of 118 and observed HTTPX completeness 8 to 10 of 12. Twenty with dependencies
also qualifies; disabling dependencies loses individual baseline successes.
Larger context consumes more of the unchanged 4,096-token cap. Twelve with
dependencies is selected as the primary fresh test before Werkzeug outcomes;
twenty is diagnostic and cannot rescue a failed primary gate. The default stays
eight until the fresh test and its independent review are complete.

The [real 9,097-window Django profile](../results/django-index-profile-2026-10-14/findings.md)
and [native Qwen editor check](../results/vscode-qwen-native-2026-10-14/findings.md)
are complete and independently audited. Audit receipt/schema corrections are
explicitly preserved and do not change their native outputs. The editor proves
saved/dirty/off provider settings on a five-window fixture in an isolated
Development Host; ordinary installed-profile activation remains separate.
The [completed Werkzeug continuation](../results/context-holdout-werkzeug-v2-2026-10-14/findings.md)
passes independent execution/source auditing but fails the primary twelve-excerpt
quality gate: both eight and twelve complete 7/12 behaviors and 4/8 multi-file
behaviors. Twelve covers 28/34 groups versus 27/34, with higher compact context
use. The diagnostic twenty-excerpt condition completes 8/12 and cannot rescue
the primary. An original overlong-ID prefix and two audit-envelope failures are
retained; the unchanged technical continuation and narrowly qualified audit
correction are explicit. The default remains eight.

The [offline beta kit](beta-evaluation.md) is implemented and its collector smoke
uses zero external testers. CI now prepares the exact pinned tokenizer cache
before offline tests. A [reviewed shutdown/CLI fix](../results/production-fixes-2026-10-14/verification.json)
cancels anonymous active requests, rejects resident policy misconfiguration and
corrects token clipping guidance; six regressions and 116 related checks pass.
The [ordinary profile/package acceptance](../results/ordinary-vscode-profile-v3-2026-10-14/findings.md)
is complete and independently reconciled. Trusted public commands activate the
normally installed product; actual Restricted Mode denies it without HTTP.
The failed socket path and shutdown attempts remain preserved. The successful
run uses owned shared storage and public graceful quit. Its exact 53,874-byte
VSIX is the preview 2 asset. Preparation alone is not runtime evidence.

A second [cancellation review](../results/cancellation-shutdown-preflight-2026-10-14/fixed-source-v1/qualification.json)
closes admission atomically during shutdown and checks cancellation before a
query forward. Historical audit tests now use immutable source fixtures, so
future product changes do not rebind earlier experiments. Full Linux core and
editor CI passes. The [C# source-support plan](csharp-source-plan.md) is prepared;
C# remains unimplemented pending isolated grammar and source safety acceptance.

Use fresh development queries to improve evidence delivery and a new
untouched holdout to assess those changes. The observed Zod misses are now
development evidence for future tuning. Broader coding tasks and external beta
feedback follow. Token savings remain unestablished; product integration does
not imply that target was met.
