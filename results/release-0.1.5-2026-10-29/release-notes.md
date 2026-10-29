Semantic Search v0.1.5 improves the source excerpts selected by the default local search.

Download **semantic-search-mac-0.1.5.zip**, unzip it, open Terminal in that folder
and run `bash "Get Started.command"`. The MIT-licensed download includes the
guided Mac launcher, source preflight, offline diagnostics, local feedback,
MCP configuration support and **Semantic Search.vsix**. Setup installs its local
runtime and Qwen embedding weights separately. Use **SHA256SUMS.txt** to verify
the ZIP and standalone VSIX.

The default now distinguishes separate definitions that share a displayed name,
removes contained duplicate excerpts and fills spare capacity with additional
literal source while preserving previously delivered evidence. Dense retrieval,
reranking off, eight excerpts, 12,000 source characters and the 4,096-token
compact response cap stay unchanged. Compatible existing document vectors remain
reusable; resumable indexing from v0.1.4 is retained.

This packages the already tested coverage policy selected by an explicitly
post hoc engineering decision. On one small authored TanStack Query sample,
dense multi-file completeness is 2/8 versus 1/8 for the previous policy, with no
observed required-group loss. Multi-file misses remain. The more complex
shared-flow selector failed its incremental quality/latency gate and remains
experimental; no new selection experiment is promoted here.

This release adds no generation or H2O model change and establishes no general
coding correctness, paid-token savings, arbitrary-codebase relevance or independent
Mac beta result. Optional rerankers still add latency and require separate setup.
The GitHub release is regular; the extension manifest continues to mark the early
product as a preview. Marketplace publication is not part of this release.

[Getting started](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/getting-started.md)
· [Measured coverage decision](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/shared-flow-allocation.md)
· [Release qualification plan](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/release-0.1.5-plan.md)

Qualification results will be recorded before publication; this candidate text
does not claim that new assets or a fresh setup have passed yet.
