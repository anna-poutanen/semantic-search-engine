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

This release also fixes cancellation during the automatic demo's MCP process
startup, ensuring the adapter's cleanup is armed before an interrupt is handled.

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

Qualification passed a fresh-folder Python/package/model download, six real
HTTP/MCP toy searches and actual own-repository interruption/resume on 358 Flask
windows. After termination, the same launcher reused 24 saved documents and
embedded only the 334 remaining; a later restart reused all 358. Generated MCP
configuration, source/budget checks, diagnostics, feedback and the exact VSIX's
isolated CLI installation also passed. The local Python suite reports 2,566
passed and 10 skipped.
See [release qualification](https://github.com/anna-poutanen/semantic-search-engine/blob/main/results/release-0.1.4-2026-10-17/findings.md)
for receipts, preserved failures and limits.
