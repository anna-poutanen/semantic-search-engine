Semantic Search v0.1.4 adds automatic resumable indexing to the regular Mac release.

Download **semantic-search-mac-0.1.4.zip**, unzip it, open Terminal in that folder
and run `bash "Get Started.command"`. The ZIP includes MIT-licensed app source,
guided setup, source preflight, offline diagnostics, local feedback, MCP
configuration support and **Semantic Search.vsix**. The launcher installs its
dedicated runtime and Qwen embedding weights separately.

Indexing now saves each completed Qwen embedding batch to SQLite. If indexing is
interrupted, repeat the same own-repository command from the same extracted app
folder:

```bash
bash "Get Started.command" --repo "/absolute/path/to/repo" --source-root src
```

Keep the repository, source root, exclusions and cache location unchanged.
Compatible completed documents are reused; only missing or changed documents
need inference. Checkpoints are saved even without progress output, and no
resume flag is needed. A model batch interrupted before its checkpoint may run
again. The toy walkthrough creates a fresh repository each time, so resume is
best observed with the same own-repository command.

Partial checkpoints never replace the active source/keyword/dense snapshot.
That snapshot is published together only after the complete refresh passes
source, vector and cancellation checks. Compatible older vectors remain valid:
this feature changes neither the SQLite schema nor the embedding fingerprint.
Retrieval defaults and the pinned model are unchanged.

The runtime supports Apple Silicon Macs. Existing
[real Qwen interruption/restart evidence](https://github.com/anna-poutanen/semantic-search-engine/blob/main/results/resumable-indexing-2026-10-17/findings.md)
checks durability and vector parity on the development Mac; it does not establish
retrieval improvements, indexing-speed gains or behavior on another Mac.
Independent Mac beta testing and broader retrieval/workflow validation remain.
The VS Code extension remains marked as an early product in its manifest; this
GitHub release is regular, not a prerelease.

[Getting started](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/getting-started.md)
· [Resumable indexing](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/resumable-indexing.md)
· [Measured limits](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/release-preview.md)

Use **SHA256SUMS.txt** to verify the ZIP and VSIX.
