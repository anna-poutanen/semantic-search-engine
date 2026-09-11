# Actual saved and dirty editor workflows

The mixed-language smoke passed in an actual VS Code 1.141.0 Extension
Development Host against the resident Qwen3-Embedding-0.6B BF16 backend. The
fixture contains five implementations in three tracked Python, JavaScript and
TypeScript files. This is an integration check on a small authored fixture,
not a retrieval-quality or coding-correctness benchmark.

[Host assertions and HTTP receipts](saved-host.json) verify activation and
registered commands, connect/status, dense search, Explorer view focus,
plaintext captured previews, literal HTML source, exact saved navigation,
refresh, cancellation and replacement. JavaScript includes BOM/CRLF; TypeScript
includes type annotations. Four successful search receipts independently check
nonempty exact excerpts. Saved source is unchanged before and after the run.
The [runtime proof](saved-runtime-proof.json) records the initialized dense
backend, pinned Qwen revision, Python implementation hashes and byte equality
between the tested extension checkout and main.

The [first attempt](saved-host-attempt-01-long-ipc-path.json) failed before the
extension ran: the worktree-based user-data path exceeded macOS's Unix socket
limit. The launcher now uses an isolated short temporary directory and removes
it after writing the receipt. A real Unix socket regression passes; the saved-only
editor CPU suite had 34 passing tests at that revision. The second host attempt passes.

Cancellation uses a test relay that holds one response until a cancel arrives.
This establishes command/transport handling and suppression of late results;
it does not establish interruption of an active Metal forward. Receipt times
include that relay and are not inference benchmarks. The host deliberately
disables Workspace Trust only in isolated fixture user data. Actual Restricted
Mode, multi-root and disconnected-service acceptance remain separate checks. CPU tests cover several of these cases, but
they do not replace actual-host execution.


## Dirty buffers

The [dirty-host run](overlay-host.json) and [runtime proof](overlay-runtime-proof.json)
pass against current integrated extension and backend source. Seven successful
searches delivered 35 independently checked excerpts: 31 saved and four literal
editor overlays. All compact payloads fit the 2,000-token cap. JavaScript
BOM/CRLF and Unicode editor text retain full-buffer hash/version bindings.
Navigation opens the same unsaved document. Editing a different input buffer
expires navigation while preserving its captured preview; a new search binds
current versions. A later ordinary search still returns saved bytes, with the
saved parent index unchanged. No files were saved in this phase.

The [independent audit](overlay-host-audit.json) reconstructs all requests and
normalized redacted-response hashes, verifies exact source and input bindings,
and links cancellation. Parsed public JSON does not retain the raw HTTP bytes,
so original raw-response SHA values cannot be independently reconstructed.
Two executed host source files were preserved before the next host phase changed
those files; the original runtime proof remains unchanged.

## Saved changes and watcher

The [separate saved-edits run](saved-edits-host.json) passes ten successful
searches with controlled writes in the authored fixture. It checks that an edit
outside the returned excerpt invalidates prior navigation, and a new search has
an updated whole-file witness. A saved TypeScript semantic change is published
by the watcher before the next search, observed through status polling without
an explicit refresh or query trigger. Current source opens correctly afterward.
Git rename removes the old path, deletion removes active windows, and invalid
saved Python reports an error rather than returning stale excerpts.

The fixture's original bytes, Git HEAD and clean status are restored afterward.
Final byte equality establishes restoration; this phase intentionally changed
files during the run. The [runtime proof](saved-edits-runtime-proof.json) records
55 source hashes and an initialized dense backend with five restored windows.
These tiny-fixture checks establish integration behavior, not correctness on
unseen coding tasks or large-repository performance.
