# Extension Development Host acceptance recipe

**Pending execution.** CPU mock-service tests validate transport/controller/file
logic; they do not validate the actual VS Code command/view wiring. Do not claim
this smoke or real-model behavior passed until its evidence has been recorded.
Run after the frozen multifile study finishes and its artifacts are secured.

## Launch and verify the local service workflow

1. Build with `npm ci --ignore-scripts --no-audit --no-fund` and `npm test` inside
   `extensions/vscode`. Record commit, Node/npm/VS Code versions and package-lock
   SHA. Use a fresh local Python Git fixture or an explicitly selected repository.
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
retrieval, remote workspaces and Marketplace publication are later milestones.
