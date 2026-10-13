# Editor and next-language implementation scope

This is a prospective implementation plan, written during the frozen multifile
coding study on October 13, 2026. It does not change that study's runtime or imply
that retrieval has demonstrated a token or completion-time benefit. An isolated
editor draft can be prepared with CPU fixtures while the study runs. Integration
into the shared backend and real-model verification follow completion of the
study and preservation of its artifacts.

## First usable VS Code version

Build a local desktop extension in `extensions/vscode` that uses the existing
resident Python service. A user invokes **Semantic Search: Find Implementation**,
enters a behavior, sees implementation results, previews an excerpt, and opens
the verified source range. This first version searches saved Python source.

Use a command and input box, a results `TreeDataProvider`, and a readonly
`TextDocumentContentProvider` for previews. Result rows show symbol, relative
path, line range, and an excerpt-truncation indicator. Preserve retrieval order;
dependency rows say that they supply related context. Display the existing
`no_candidates` and `insufficient_evidence` states without implying proof of
absence. These extension surfaces are supported by the official [tree-view
API](https://code.visualstudio.com/api/extension-guides/tree-view) and [virtual
document API](https://code.visualstudio.com/api/extension-guides/virtual-documents).

Start with an explicitly configured loopback service URL and **Check Service**,
**Refresh Index**, and **Cancel Search** commands. Call `/status`, `/index`,
`/search`, and `/cancel` from the Node extension host. Use a unique request ID per
search; replacement searches cancel the previous request and reject late results
using a local generation counter. Keep dense retrieval and no H2O reranking as
defaults. Reuse backend result/source budgets and expose advanced retrieval
settings through normal extension settings.

Validate service responses and enforce the existing 1 MiB response cap. Allow
only loopback HTTP URLs without credentials, query, fragment, or path; reject
redirects. Bind a connection to the canonical repository reported by `/status`.
For multiple workspace folders, select a folder explicitly and require its
canonical path to match the connected service. Validate result paths, resolve
symlinks within that folder, validate line bounds, and verify the current excerpt
against the returned source before navigation. If a file changed after search,
offer the captured readonly preview and a new search rather than presenting the
old range as current. Dirty editors make the saved-source scope visible, and
navigation to changed text requires a fresh matching excerpt.

The status item shows disconnected/loading, indexed window count, watcher state,
and the backend's last error. Status requests may wait behind serialized model
work; the extension must remain responsive and keep its own loading state. Keep
queries and source out of diagnostic logs. No hosted model or editor chat API is
needed for this interface.

After the connected-service workflow passes, add explicit **Start Local Service**
and **Stop Local Service** commands. Spawn a configured Python executable with
argument arrays and no shell; own only processes the extension started. Use an
extension storage directory for the index, capture bounded startup diagnostics,
and distinguish model loading from ready service state. Do not silently install
weights or dependencies. Gate process execution and workspace-defined executable
settings on Workspace Trust using the official [trust
API](https://code.visualstudio.com/api/extension-guides/workspace-trust).

Acceptance checks include successful preview/navigation, multi-root mismatch,
stale and dirty source, malformed/oversized responses, cancellation and late
responses, disconnected service, watcher refresh after save/delete/rename, and
startup/shutdown ownership. Pure client/controller tests can use CPU fixtures;
one Extension Development Host smoke verifies the actual command/view wiring.
Package a locally installable VSIX and document its external Python/service
prerequisites. Marketplace publication is a later release step.

## Unsaved source follows the saved-source version

The current service snapshots tracked saved bytes. Introduce an explicit,
session-scoped overlay API before claiming searches include dirty editors. Each
overlay entry needs a repository-relative path, full text, editor version, and
content hash. An overlay replaces that path's saved snippets only for the request;
it must not overwrite the persistent saved-source index or affect another client.
Results identify saved versus overlay provenance and the editor version used.
Reject late results after edits, and clear overlays on save, close, or session end.
Add byte/entry limits, cancellation, and tests for concurrent clients and disposal.
New untracked files also need an explicit membership policy; the current Git
discovery indexes tracked files only.

## TypeScript and JavaScript phase

Add `.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.mts`, and `.cts` through a
language extractor registry. Retain the existing Python AST extractor and exact
snippet format. Use pinned Python Tree-sitter bindings and pinned JavaScript,
TypeScript, and TSX grammars; check installation/ABI compatibility on the M4
before committing the lock. The [Python bindings](https://github.com/tree-sitter/py-tree-sitter)
and separate [TypeScript/TSX grammars](https://github.com/tree-sitter/tree-sitter-typescript)
provide the primary implementation references.

Index implemented named functions, class/object methods, and assigned function
expressions or arrows. Use stable qualified names for exported/default and
nested definitions. Exclude declaration-only signatures, `.d.ts` declarations,
and empty placeholders. Preserve original source, decorators, exact physical
lines, Unicode, and LF/CRLF; window long bodies using the existing limits. Include
JSX/TSX-containing implementations. Define a conservative parse-error policy
before implementation: an invalid saved snapshot reports an error rather than
silently serving an apparently current partial index.

File discovery, captured-source path validation, backend syntax validation, and
static dependency construction all currently assume Python and must dispatch by
language. Reuse `Snippet` and the SQLite representation where possible; add an
extractor/grammar fingerprint to snapshot metadata so old windows cannot silently
survive an extractor change. Render actual source into embeddings using the same
Qwen model; no training is needed. Python relations stay unchanged. Initially
leave uncertain JS calls unresolved; add only tested relative imports and direct
lexical calls as context references, without claiming runtime dispatch resolution.

Freeze CPU extraction fixtures for overloads versus implementations, anonymous
assigned functions, default exports, methods/accessors, nested scopes, decorators,
JSX, Unicode, CRLF, long-body windows, malformed files, and branch/delete/rename
updates. Exclude dependency/build output under the documented membership policy.
Then author a separate source-reviewed TS/JS retrieval set with decoys, absent
behaviors, and multi-file requirements on pinned real repositories. Reserve an
unseen repository before tuning. Only after the current study is complete run
serial real-Qwen latency/coverage checks and publish language-specific misses.

Delivery order is: saved-Python editor workflow, explicit service lifecycle,
language extractor and persistent-index compatibility, real TS/JS retrieval
checks, then session overlays and a reproducible repository demo. Each stage has
its own tests and claims; the Python workflow study remains unchanged.
