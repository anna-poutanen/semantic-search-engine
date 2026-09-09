# Actual saved-source editor workflow

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
it after writing the receipt. A real Unix socket regression passes; the full
editor CPU suite has 34 passing tests. The second host attempt passes.

Cancellation uses a test relay that holds one response until a cancel arrives.
This establishes command/transport handling and suppression of late results;
it does not establish interruption of an active Metal forward. Receipt times
include that relay and are not inference benchmarks. The host deliberately
disables Workspace Trust only in isolated fixture user data. Actual Restricted
Mode, multi-root, saved-edit/delete/rename, disconnected-service and unsaved-buffer
acceptance remain separate checks. CPU tests cover several of these cases, but
they do not replace actual-host execution.
