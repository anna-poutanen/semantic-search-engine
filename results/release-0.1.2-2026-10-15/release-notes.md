Semantic Search v0.1.2 is available as a regular release.

Download **semantic-search-mac-0.1.2.zip**, unzip it, open Terminal in that folder
and run `bash "Get Started.command"`. The download includes the MIT-licensed app
source, guided setup, diagnostics, feedback collector, MCP configuration support
and **Semantic Search.vsix**. The launcher installs the dedicated runtime and
Qwen embedding weights separately.

Own-repository searches support configurable result/context budgets, visible
indexing progress, separate startup and request timeouts, explicit source
exclusions and persistent embedding reuse.

This release promotes the application qualified in `v0.1.2-preview.1`. Runtime
files and the VSIX are unchanged. Only the ZIP's guide and manifest changed to
point to the regular release; all 35 bundle tests passed. The recorded Django,
MCP, embedding parity, installed extension and CI checks remain applicable.

The runtime currently supports Apple Silicon Macs. Independent Mac beta testing
remains to do; existing retrieval and hardware limitations are documented.

[Getting started](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/getting-started.md)
· [Measured limits](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/release-preview.md)
· [Qualification](https://github.com/anna-poutanen/semantic-search-engine/blob/main/results/beta-hardening-2026-10-14/findings.md)

Use **SHA256SUMS.txt** to verify the ZIP and VSIX.
