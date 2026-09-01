# Local behavioral code search

Describe a behavior and receive relevant implementations with verified paths,
symbols, line numbers, and bounded source excerpts. A VS Code extension and one
MCP `search_code` tool will share a local Python backend.

This follows the agreed plan in **Choose a project for YC roles** on October 12,
2026. Use existing model weights; training and fine-tuning are outside the plan.

## Sequence

1. **Model experiment (current):** H2O-Lightning-4B q8 on the M4, real-code
   relevance judgments, decoys, memory and cold/warm latency. Compare candidate
   ordering before and after reranking. Publish failures as well as successes.
2. **Core CLI:** syntax-aware symbols, Qwen3-Embedding-0.6B candidate retrieval,
   lexical retrieval, hybrid ranking, H2O shortlist scoring, exact locations.
3. **Retrieval quality:** context selection, duplicate removal, output budgets,
   evaluated multi-file behavior and absent features.
4. **Persistent index:** SQLite FTS5 and pinned sqlite-vec, incremental updates,
   deletion/rename/branch handling, source hashes, caching and cancellation.
5. **MCP:** small stdio adapter, shared service, budgeted excerpts; paired agent
   tasks with provider-reported tokens and correctness checks.
6. **VS Code:** search command, result previews, exact navigation, indexing/model
   status, temporary overlay for unsaved edits.
7. **Beta/release:** external users, reliable installation, language expansion,
   reproducible benchmark and recognizable real-repository demos.

Start with Python and TypeScript/JavaScript. C# and non-Mac deployment follow
validation of the core. Serialize GPU work and share one model process.

## Evaluation gates

H2O must improve delivered results enough to justify latency. The first pilot is
development evidence, not a held-out benchmark. Later, review roughly 100 queries
across several repositories and reserve a held-out set. Compare lexical,
embedding, hybrid, and hybrid-plus-H2O retrieval. Measure candidate coverage,
useful evidence within the output budget, stale results, memory, and cold/warm
latency. Initial aspirations: 90% candidate coverage, warm p95 below five seconds,
30% lower median paid-agent tokens with comparable completion. None is a measured
claim until demonstrated. Local inference has no API token charge, but tool calls,
returned excerpts, and follow-up reads still consume the agent's tokens.
