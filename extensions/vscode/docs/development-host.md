# Extension Development Host acceptance recipe

**Pending execution.** CPU mock-service tests validate transport/controller/file
logic; they do not validate the actual VS Code command/view wiring. Do not claim
this smoke or real-model behavior passed until its evidence has been recorded.
Run after the frozen multifile study finishes and its artifacts are secured.

## Automated host smoke prepared, not yet executed

The [official extension testing guide](https://code.visualstudio.com/api/working-with-extensions/testing-extension)
supports a custom `run(): Promise<void>` test runner through
`--extensionDevelopmentPath` and `--extensionTestsPath`. This checkout pins
`@vscode/test-electron` 3.1.0 for download/unzip and the official macOS arm64
VS Code 1.141.0 build (product commit
`2a59476c9bfcb90b3ddc372c36762471b7dfad1c`). The verified archive SHA-256 is
`9b4ae386e3cf738d7ba990237ddf0a18092c924a1bcae465ed4697964745f42d`;
the extracted executable SHA-256 is
`e9760eca0a62d96ff2bb334ef5c8d3b677a6275a2dd0cf5a63c356b7a19435a2`.
No installed VS Code app was found. The downloaded test copy stays under ignored
`.cache/vscode-host`, outside the user's applications, settings and extensions.

`npm run host:prepare` downloads and verifies that copy without executing it.
`npm run host:fixture -- /absolute/new-fixture-directory` creates a fresh Git
repository with three generic Python functions. Add `--mixed` to include a
JavaScript BOM/CRLF implementation and a typed TypeScript implementation (five
functions across three files). It starts no backend or model.
Both preparation steps are CPU/setup work; the host smoke below remains pending.

After the study is secured, start the actual backend explicitly in another
terminal using the project's existing Python runtime:

```sh
/absolute/project/.venv/bin/python -m semantic_search.cli serve \
  --repo /absolute/new-fixture-directory --source-root src --port 8765 \
  --index-cache /absolute/editor-checkout/extensions/vscode/.cache/indexes
```

Then, from this extension directory, run:

```sh
npm run host:smoke -- --run \
  --repo /absolute/new-fixture-directory \
  --url http://127.0.0.1:8765 \
  --output /absolute/editor-checkout/extensions/vscode/.cache/host-acceptance.json
```

The explicit `--run` starts the isolated Development Host. A fresh user-data
directory disables updates and telemetry and loads the development extension
with other installed extensions disabled. Workspace Trust is disabled only for
this deliberate fixture run, following the official trusted-path test recipe.
This does **not** test Restricted Mode; that remains a separate acceptance case.
The launcher owns and terminates only this test-host process group, with a
five-minute timeout. The externally started backend remains owned by its caller.

The actual-host runner executes the registered connect, status, find, refresh,
preview, open and cancel commands. It checks that the known fixture behavior ranks
first, captured virtual documents are plaintext (including literal HTML source),
the current saved-file selection matches the returned hash and physical bounds,
and cancelled results cannot replace a fresh search. A test-only loopback receipt
relay records the forwarded request IDs and source proofs separately from editor
state. One cancellation query's response may wait at that relay until cancel
arrives, making the UI case deterministic. Receipt timings include this observer;
they are not model latency measurements, and no running Metal forward is claimed
to have been interrupted. The shipped extension contains no receipt/source log.

With the mixed fixture, the runner additionally exercises JS/TS preview and exact
saved-source selection, preserving BOM/CRLF source hashes. The tiny fixture
delivers all five functions under the selected cap; each language's observed
returned position is diagnostic, not a retrieval-quality benchmark or a default
setting selection. No held-out language query labels are used. The exact captured
string stays in the provider/data. VS Code's
[pinned text-buffer builder](https://github.com/microsoft/vscode/blob/1.141.0/src/vs/editor/common/model/pieceTreeTextBuffer/pieceTreeTextBufferBuilder.ts)
separates a leading BOM even in virtual documents; assertions restore that one
marker to compare the original source hash, without changing editor text.

The JSON artifact retains failures, timeout/exit status, actual host version,
backend model metadata, command checks, HTTP receipts and before/after source
hashes. User/worktree paths are replaced with explicit placeholders; HTTP hashes
refer to original bytes, and a separate hash covers redacted JSON. Require both
the host assertions and nonempty independent source/cancellation checks to pass.
No real-host or real-model result has been recorded yet.

## Programmatic commands

Trusted local automation can call the same command handlers without input boxes:

```ts
await vscode.commands.executeCommand('semanticSearch.connect', {
  workspacePath: openFolder.uri.fsPath,
  serviceUrl: 'http://127.0.0.1:8750'
});
await vscode.commands.executeCommand('semanticSearch.find', {
  query: 'Describe a behavior',
  options: { max_results: 5, token_budget: 2000 }
});
await vscode.commands.executeCommand('semanticSearch.preview', 0);
await vscode.commands.executeCommand('semanticSearch.open', 0);
```

Connect arguments must select an already open local workspace folder; source
verification, loopback transport and trust guards still apply. Find requires an
existing connection and validates overrides using normal search settings.
Result indices refer to current results, and programmatic stale navigation throws
instead of opening a notification prompt. Calls without arguments retain the
normal palette flow. The activation API exposes cloned in-memory `getState()`
snapshots and `onDidChangeState()` for assertions; it persists no query or source.

## Launch and verify the local service workflow

1. Build with `npm ci --ignore-scripts --no-audit --no-fund` and `npm test` inside
   `extensions/vscode`. Record commit, Node/npm/VS Code versions and package-lock
   SHA. Use a fresh local Python or mixed-language Git fixture, or an explicitly selected repository.
2. Start the existing resident service yourself with its configured Python
   executable and explicit `--repo` and `--source-root`. Keep H2O off for the
   default dense workflow. Capture readiness/settings/model revision separately;
   no extension command starts or installs it.
3. Launch installed VS Code with:

   ```sh
   code --new-window \
     --extensionDevelopmentPath=/absolute/project/extensions/vscode \
     /absolute/target-repository
   ```

   Alternatively, open `extensions/vscode`, select **Run Extension** in its
   debugger, then open the target repository in the development window. Launch
   profiles are local convenience configuration, not an automatic host test.
4. Trust this local fixture. Run **Connect / Check Service** with the actual
   address. Confirm the selected folder matches `/status.repo` canonically,
   the status item shows window count, and the Explorer's **Semantic Search**
   view is available.
5. Run **Find Implementation** with a known fixture behavior. Confirm rank order,
   relative path, inclusive range, truncation indicator, and context-reference
   labeling. Clicking a result must open a readonly plaintext virtual document
   containing exactly the returned source. **Open Verified Source** must select
   the same current file/range. Inspect service receipts independently if needed;
   do not rely on a UI claim about quality.

## Acceptance cases to record

- **Stale saved source:** edit/save the selected file after search, including an
  edit outside the excerpt. Navigation must offer captured preview/new search.
  A new search after watcher refresh must use new source/hash. Also test saved
  delete/rename and invalid saved Python errors.
- **Dirty editor:** edit without saving. Navigation must refuse to treat the
  unsaved text as saved search evidence. Captured preview remains readable.
  Save and search again to obtain navigable current evidence.
- **Multi-root:** add a different repository folder. Connecting that folder to
  this service must fail; selecting the correct canonical repository succeeds.
  Remove the connected folder and confirm disconnection/late suppression.
- **Cancellation/replacement:** start a search, cancel or replace it, then allow
  the old server work to finish. Old results must never replace the latest view.
  This is not proof that a running Metal forward was interrupted.
- **Scope/weak results:** `no_candidates`, `insufficient_evidence`, a source budget
  that omits all excerpts, and source-root/test-tree mismatch need accurate text;
  none may claim proof of absence.
- **Disconnected service:** stop the externally owned service. Status/search
  must show an actionable error without hanging the editor. Restart/reconnect
  explicitly. Disabling the extension must not stop a service it does not own.
- **Trust and privacy:** in Restricted Mode, no local connection occurs. Inspect
  that there is no source/query output channel, telemetry, persisted query history,
  webview, shell launch, or model install. Test literal HTML source as plain text.

Record each case's outcome, relevant screenshot (without unrelated private data),
file/response hashes, service revision and known limitations in a new acceptance
artifact. The existing Node tests cover malformed/oversized/redirected/error
responses, proxy bypass, Unicode/CRLF/encoding, traversal/symlink boundaries, dirty
buffers and late responses with CPU mocks. They are not a real-model evaluation.

Once these cases pass, package/install the VSIX locally and repeat one fresh
service search. Explicit owned process startup/shutdown, unsaved overlays, TS/JS
retrieval-quality measurement, remote workspaces and Marketplace publication are later milestones.

This editor draft supports the nine explicit Python/JS/TS suffixes. JS/TS source
uses strict UTF-8 with BOM retained; Python keeps its cookie/BOM decoding rules.
The actual mixed-language host run remains pending until the reviewed backend
language branch integrates and the frozen study is secured. CPU source-neutral
verification tests have passed; no language ranking or real-host outcome is
claimed from them.
