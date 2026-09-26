# Optional Qwen reranking in VS Code

The extension connects to your already running local search service. Its default
is fast dense retrieval with no reranking. Qwen reranking is an experimental
option: it improves some implementation queries, takes longer, and has mixed
results on a fresh codebase. Select it when the extra ranking work is useful for
your repository. The extension never downloads a model or starts a Python
process.

| Search choice | Workspace settings | Local prerequisite |
| --- | --- | --- |
| Fast retrieval (default) | `rerank: false` | Ordinary search service and embeddings |
| H2O reranking | `rerank: true`, `rerankProvider: "h2o"` | Search service plus the separately running H2O service |
| Qwen reranking | `rerank: true`, `rerankProvider: "qwen"` | Search service started with Qwen enabled |

The existing settings remain unchanged until you choose them. Selecting Qwen
does not enable reranking or change the retrieval family or candidate count.
`candidates` controls how many retrieved windows the optional reranker scores.
The default stays four for compatibility; the measured Qwen hybrid configuration
uses 24. Returned result and source/token budgets remain separate settings.

## Set up and start the service

First install the ordinary backend and embeddings using the
[backend guide](backend-api.md). On an Apple Silicon Mac, follow the
[optional Qwen installer guide](qwen-reranker-setup.md). Run the setup from the
search-engine checkout, separate from the repository you want to search:

```sh
cd /absolute/semantic-search-engine
python3 scripts/setup_qwen_reranker.py --install
python3 scripts/setup_qwen_reranker.py --verify-weights
```

Start the service explicitly in a terminal. Replace each example path with its
actual absolute path, and choose the source directory inside the target
repository. The reranker uses its separate installed interpreter and checkpoint:

```sh
/absolute/semantic-search-engine/.venv/bin/semantic-search serve \
  --repo /absolute/target-repository --source-root src \
  --enable-qwen-reranker \
  --qwen-reranker-python /absolute/semantic-search-engine/.cache/reranker-experiment/.venv/bin/python \
  --qwen-reranker-checkpoint /absolute/semantic-search-engine/.cache/models/qwen3-reranker-0.6b-original
```

Keep the virtual environment's `bin/python` path; replacing it with its resolved
base-interpreter symlink loses the intended environment. If the installer used
alternate destinations, pass those exact interpreter/checkpoint paths. The
installer and service validate the required environment and checkpoint. These
paths belong to the service setup, so the extension has no unused model-path
settings. Startup validates and warms the optional worker before the service is
ready; a missing or incompatible runtime must be fixed in that terminal.

The backend owns the Qwen worker, serializes search/model work, and enforces
cleanup before further embedding work after a worker failure. The extension
uses that same resident service for saved and dirty-buffer searches. Closing a
VS Code window does not shut down a service you started in your terminal.

## Choose the workspace settings

Open the target repository as a trusted local workspace. In a multi-root
workspace, apply these settings to the folder indexed by the selected service.
Folder settings are stored in that folder's `.vscode/settings.json`, as described
in the [VS Code workspace guide](https://code.visualstudio.com/docs/editing/workspaces/workspaces).
Use VS Code's normal Settings UI, or edit that folder's `.vscode/settings.json`:

```json
{
  "semanticSearch.serviceUrl": "http://127.0.0.1:8750",
  "semanticSearch.retrieval": "hybrid",
  "semanticSearch.rerank": true,
  "semanticSearch.rerankProvider": "qwen",
  "semanticSearch.candidates": 24
}
```

Run **Semantic Search: Connect / Check Service**, then **Find Implementation**.
The same settings apply when you explicitly choose dirty tracked buffers. To
return to fast search, turn `semanticSearch.rerank` off; the saved provider choice
has no effect while reranking is disabled. Changing settings takes effect on
the next search. A different service URL typed during Connect is session-only.

Programmatic searches use the HTTP API field name `rerank_provider` in their
`options` object, while the VS Code setting uses `rerankProvider`:

```js
await vscode.commands.executeCommand('semanticSearch.find', {
  query: 'Where is a retry delay calculated?',
  options: { retrieval: 'hybrid', rerank: true, rerank_provider: 'qwen', candidates: 24 }
});
```

## Troubleshooting and validation status

An unconfigured Qwen service produces an actionable error: finish the separate
setup, restart the service with `--enable-qwen-reranker`, or disable reranking.
An older service that rejects the provider option must be updated. The extension
does not fall back to another scorer. It also rejects a Qwen reranked response
that omits Qwen's provider identity. Finite Qwen ranking scores are retained in
the response data; they are ranking signals, not correctness probabilities.
Source verification, dirty-buffer bindings and cancellation retain their
existing checks.

The provider change has TypeScript and CPU loopback HTTP coverage for defaults,
legacy H2O requests, saved/dirty Qwen requests, response identity, malformed
scores and service errors. Those fixtures perform no inference. Earlier actual
Development Host checks validated Qwen embeddings and source workflows without
Qwen reranking. A normal editor/native Qwen reranker check and updated VSIX
packaging remain separate gates; see the
[prospective validation receipt](../results/vscode-qwen-preflight-2026-10-14/plan.json).

For a later actual Development Host check, use a fresh mixed-language fixture
and an externally started Qwen-enabled service, then run the extension's
explicit optional case from `extensions/vscode`:

```sh
npm run host:smoke -- --run --qwen-reranker \
  --repo /absolute/new-mixed-fixture \
  --url http://127.0.0.1:8765 \
  --output /absolute/new-qwen-host-receipt.json
```

This prospective case changes normal fixture folder settings, searches saved
source and one unsaved TypeScript buffer with Qwen, checks fast retrieval after
reranking is disabled, then restores the settings and discards the unsaved
buffer. Independent HTTP receipts must confirm the selected provider and request
settings. It is separate from the existing `--overlays` and `--saved-edits`
modes. The pinned isolated host remains an Extension Development Host; it does
not establish ordinary user-profile activation or retrieval performance.
