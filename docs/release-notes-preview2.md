# Portfolio preview 2

Local behavioral search for saved Python, JavaScript and TypeScript source,
with CLI, HTTP, MCP and VS Code access. Dense retrieval with reranking off and
an eight-excerpt context limit remain the defaults. Optional Qwen and H2O
reranking require separate local setup.

## Verified editor package

The asset `semantic-search-local-0.1.0.vsix` is **53,874 bytes**, SHA-256
`e798b21af047e1aca6b13d4d6e14aa5c6ec0069225eb5cdd611c9f93724aed60`.
It is the exact VSIX installed in the completed ordinary-profile acceptance,
including the optional Qwen provider controls. The extension manifest remains
version `0.1.0`; this release replaces the earlier preview's contents. Use
**Extensions: Install from VSIX**, accept the update/replacement, and reload
VS Code. CLI installation over the old preview can use `code --install-extension
/path/to/semantic-search-local-0.1.0.vsix --force`.

In two fresh ordinary VS Code 1.141.0 profiles, normally installed extensions
run in Production mode. The trusted test observes inactive-to-active activation
through public connect, default saved search, captured plaintext preview,
verified source navigation and actionable optional-Qwen setup failure. A separate
Restricted Mode profile observes actual false trust, inactive product commands
and zero product HTTP. Owned Code/extension-host groups quit gracefully; test
profiles and shared data are removed. These checks use a zero-model authored
HTTP fixture; its 128-token field is a placeholder. Trusted mode explicitly
passes `--disable-workspace-trust`, so the actual trust-prompt UX is not tested.
[Acceptance and retained failures](../results/ordinary-vscode-profile-v3-2026-10-14/findings.md)

Separate [real optional-Qwen editor evidence](../results/vscode-qwen-native-2026-10-14/findings.md)
checks saved/dirty source and provider settings with resident models in a
Development Host. The package installs no model, Python runtime or backend;
start the service separately for the same source root. Follow the
[preview guide](release-preview.md), [Mac setup](../README.md),
[MCP guide](mcp.md) and [optional-Qwen setup](qwen-reranker-setup.md).

## Evidence and product limits

- The [real Django profile](../results/django-index-profile-2026-10-14/findings.md)
  validates 9,097 production windows and nine persistence/refresh stages. Initial
  indexing takes 395.93 seconds; fresh CPU reopen reuses vectors in 3.89 seconds,
  excluding model/service startup. Warm dense/hybrid in-process backend P95 is
  about 0.72 seconds without reranking or HTTP transport.
- The [completed Werkzeug context comparison](../results/context-holdout-werkzeug-v2-2026-10-14/findings.md)
  fails the primary twelve-excerpt improvement gate: eight and twelve each
  deliver 7/12 complete behaviors and 4/8 multi-file behaviors. Twenty is
  diagnostic and does not change the default.
- The [18 coding attempts](../results/qwen-coding-workflows-2026-10-14/findings.md)
  complete 2/6 correctly in each condition. On the two matched correct pairs,
  Qwen MCP uses 18.52% more gross tokens than fast MCP, 21.30% less uncached input
  and 0.72% more full trial time. These observed tasks, missing receipts and
  supplemental defects establish no general token-saving or correctness gain.
- Shutdown cancellation now tracks anonymous requests, atomically stops new
  admission and checks cancellation before query embedding. Cancellation remains
  cooperative between native operations. Resident CLI policy misconfiguration
  is rejected, and truncation output distinguishes source and token limits.
- An [offline beta handoff and feedback collector](beta-evaluation.md) is ready.
  It records allowlisted local feedback and uploads nothing. No external beta
  user is included in the existing evidence.

Cached installer validation is from one existing Apple Silicon Mac, rather than
a clean machine. C#, non-Mac model inference, larger-scale throughput and broad
workflow benefits remain outside the demonstrated support. The preview is on
GitHub, with no Marketplace publication. The package remains `UNLICENSED`; this
release does not add a distribution-license grant.
