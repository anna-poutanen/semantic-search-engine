# Semantic Search for VS Code — local draft

Search saved Python, JavaScript and TypeScript implementations through the project's already running local
service. Enter a behavior, inspect ranked results in the Explorer, preview an
exact captured excerpt, and open its verified current source range. Dependency
rows are marked **context reference**; they are not verified query matches.

This draft is prepared separately from the frozen coding study. CPU tests and
VSIX packaging are distinct from an actual Extension Development Host smoke or
real-model validation; see [the acceptance recipe](docs/development-host.md).

## Connect

1. Set up the external Python environment/checkpoints using the project's
   [backend guide](https://github.com/anna-poutanen/semantic-search-engine/blob/main/docs/backend-api.md). The extension installs no models
   or dependencies and starts no Python process.
2. Start the service in your own terminal, choosing the repository you open in
   VS Code and its source root:

   ```sh
   /absolute/project/.venv/bin/semantic-search serve \
     --repo /absolute/target-repository --source-root src
   ```

3. Open that repository as a trusted, local VS Code workspace folder. In a
   multi-root workspace, explicitly choose the service's repository folder.
4. Run **Semantic Search: Connect / Check Service**. The default address is
   `http://127.0.0.1:8750`. A different address entered here is session-only;
   settings are not written automatically.
5. Run **Semantic Search: Find Implementation**, then click a result for its
   readonly, plain-text captured preview. Use the result's **Open Verified
   Source** action to navigate.

**Show Service Status**, **Refresh Index**, and **Cancel Search** are separate
commands. Status displays indexed window count and watcher/error state; it may
wait behind serialized model work. Cancellation signals the service, aborts the
client's wait, and suppresses late results. A single running Metal forward cannot
be interrupted, and cancellation acknowledgement does not prove that work had
not already completed. Refresh uses `/index`; aborting an HTTP wait does not
cancel that endpoint's model work.

Search uses saved, tracked files under the service's
configured `source_root`. It excludes unsaved editors and new untracked files;
`git add` makes a new saved source file eligible. The client supports `.py`, `.js`,
`.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.mts` and `.cts` results. JS/TS retrieval
requires the reviewed language-capable backend and its pinned `languages` extra;
the extension installs neither parser packages nor weights. Backend discovery
excludes declaration files and configured/default generated directories. A query mentioning tests cannot
search the tests tree when the service indexes only `src`. The current backend
scope determines which files are searched. Neither `no_candidates` nor a calibrated
`insufficient_evidence` response proves that a behavior is absent.

## Source verification

The service repository and selected workspace must resolve to the same canonical
directory. Result paths and symlinks must remain inside both that repository and
its configured source root. The client validates delivered hashes, inclusive
physical line bounds, budgets, and response echoes. Unknown future metadata is
accepted; fields used by this version retain strict types and invariants.

When results arrive, the extension verifies each current full window/excerpt and
captures the saved file's byte hash. Navigation rechecks that file, path,
window, excerpt, and open editor. Saved changes after this capture, unsupported
encoding, a missing file, or a dirty target editor block navigation and offer the
captured preview or a new search. There is an unavoidable interval between the
backend's final snapshot check and the extension's first file capture; it verifies
the exact current window, not an unprovided whole-file hash from query time.
Verification does not lock a file against edits after the final check.

UTF-8/BOM, ASCII and Latin-1 are covered by CPU fixtures. Other Node-supported
Python encoding labels must decode to the exact returned text or navigation
fails closed. JS/TS uses strict UTF-8 and retains its BOM in returned/provider
source. VS Code separates a leading BOM from editor text; verification restores
that one saved marker for comparison, without editing the document. Mixed line
endings that the editor normalizes can prevent exact navigation verification;
the original captured data remains available. Files exceeding 8 MiB can still be previewed as captured source but
are not read for navigation verification. Previews are plaintext virtual
documents, with a bounded in-memory cache; no HTML or webview is used.

Connections require Workspace Trust and a local desktop workspace. URLs accept
only plain HTTP `127.0.0.1` or `localhost`, optional port and root slash, without
credentials/query/fragment/path. Requests use numeric loopback and a private
native HTTP agent, independent of proxy environment variables. Redirects and
responses over 1 MiB are rejected. This is local transport validation, not
authentication against other processes on the same machine.

Queries and source are not written to diagnostic logs, telemetry, or persistent
extension state. The normal editor shows the source requested by the user.

## Settings and local build

Resource-scoped `semanticSearch.*` settings expose `serviceUrl`, `retrieval`,
`rerank`, `candidates`, `maxResults`, `sourceBudget`, `tokenBudget`, and
`includeDependencies`. Defaults are dense/no rerank, 4 candidates, 8 results,
12,000 source characters, a 4,096 `cl100k_base` compact-context cap, and static
dependency context. The token cap is not a coding model's paid-token counter.
Optional H2O reranking requires its separately running local server.

Build tooling uses Node ≥22 (verified locally with Node 24.12.0 / npm 11.6.2):

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run package
```

The VSIX is `semantic-search-local-0.1.0.vsix`; install it manually through
**Extensions: Install from VSIX** after the acceptance smoke. No Marketplace
publication is performed. The package is `UNLICENSED`; no distribution license
grant has been chosen for the project. There are no extension runtime npm
dependencies. Dev packages and all transitive integrity hashes are locked.

Pinned development packages were checked against the official npm registry on
October 13, 2026: TypeScript 5.9.3, `@types/vscode` 1.95.0, `@types/node` 22.20.5,
`@vscode/vsce` 4.0.0 and `@vscode/test-electron` 3.1.0. The TypeScript/API pins target the declared VS Code
minimum rather than assuming the latest API. Packaging's Node requirement is
separate from the extension host's runtime.

Primary references: [VS Code tree views](https://code.visualstudio.com/api/extension-guides/tree-view),
[virtual documents](https://code.visualstudio.com/api/extension-guides/virtual-documents),
[Workspace Trust](https://code.visualstudio.com/api/extension-guides/workspace-trust),
[official VSCE](https://github.com/microsoft/vscode-vsce), and
[Node's native HTTP agents/proxy behavior](https://nodejs.org/docs/latest-v24.x/api/http.html).
