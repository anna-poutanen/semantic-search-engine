Semantic Search v0.1.3 adds source preflight to the regular Mac release.

Download **semantic-search-mac-0.1.3.zip**, unzip it, open Terminal in that folder
and run `bash "Get Started.command"`. The ZIP includes MIT-licensed app source,
guided setup, offline diagnostics, local feedback, MCP configuration support and
**Semantic Search.vsix**. The launcher installs its dedicated runtime and Qwen
embedding weights separately.

Own-repository sessions now inspect saved source before embedding model setup.
Invalid included source stops setup early with file/line diagnostics and explicit
exclusion suggestions. Minified filenames and unusually large rendered windows
produce warnings; no file is automatically excluded. Choose exclusions for your
intended scope, then rerun the check.

Inspect without starting search:

```bash
bash "Get Started.command" --preflight --repo "/absolute/path/to/repo" --source-root src
```

The launcher may install the managed runtime, packages and tokenizer cache before
preflight. Inspection itself does not download embedding weights, run inference,
create an index or start a service. Existing developer environments can use
`semantic-search inspect`, including bounded JSON diagnostics. `--doctor` remains
an offline environment check without repository source inspection.

Retrieval defaults are unchanged. The runtime supports Apple Silicon Macs;
qualification uses the existing development Mac, not an independent machine.
Independent Mac beta testing and broader retrieval/workflow validation remain.
The VS Code extension remains marked as an early product in its own manifest;
this GitHub release is regular, not a prerelease.

[Getting started](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/getting-started.md)
· [Source preflight](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/source-preflight.md)
· [Measured limits](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/release-preview.md)

Use **SHA256SUMS.txt** to verify the ZIP and VSIX.

Validation includes actual fresh-folder Python/package/model downloads and six
real HTTP/MCP toy searches, own-repository Flask search with persistent-vector
reuse across restarts, generated MCP configuration checks, diagnostics and local
feedback collection. The exact VSIX passes its tests and isolated CLI installation.
See the [release qualification](https://github.com/anna-poutanen/semantic-search-engine/blob/main/results/release-0.1.3-2026-10-17/findings.md)
for receipts, preserved harness corrections and tested limits.
