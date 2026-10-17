# v0.1.3 release qualification

The regular v0.1.3 release packages source preflight before own-repository embedding
setup. The exact candidate ZIP is **239,620 bytes**, SHA-256
`db26c6d38ec6468fd4a7589789d0ccb6150210e4926ae0ae260d6bd3d6cc4df2`.
Its application source digest is
`17a6214ba5d21e8de949bc02547073f252a232460cf5f84ccd042d6998ca937c`.
The embedded and separate 55,345-byte VSIX has SHA-256
`af5c556a9013e4c2a7d0fa77df9d0eab5136a5204af90a9f1f6e598c14289b48`.
All three files for publication are bound by [SHA256SUMS.txt](SHA256SUMS.txt)
and [the build receipt](bundle.json). The checksum file lists the ZIP and VSIX;
publication separately records the checksum file's own digest.

## Actual fresh-folder setup

The [setup driver](qualify_setup.py) extracted every archive entry into a new
directory with spaces under `/private/tmp`, outside Git. There was no app `.cache`
directory and no preseeded runtime, package or model cache. All 61 payload entries
matched the manifest, all 62 extracted files matched the ZIP, the launcher was
executable and the packaged Python version was 0.1.3. The child PATH contained
only macOS system directories; inherited Python environments and Hub/GitHub
credentials were removed.

The [cold setup receipt](cold-setup.json) and hash-bound logs establish:

- Before setup, `--doctor` used its expected offline fallback and exited 1 because
  a suitable local Python was unavailable. It created no app cache.
- `--preflight` downloaded pinned uv, managed Python 3.12.15 and the pinned runtime
  packages, installed project 0.1.3 and verified its package/Metal probe. It then
  rejected the tracked invalid JavaScript template. The model and index directories
  were still absent.
- An explicit file exclusion made the same scope ready, retaining a minified-file
  and oversized-window warning. There were still no model weights or index, and
  no automatic exclusions.
- `--check` invoked the actual unauthenticated Hub download into the new local
  cache. It verified the original pinned unquantized Qwen3-Embedding-0.6B revision
  and physical weight SHA-256, then passed all three HTTP and three official MCP
  searches. All delivered excerpts and hashes matched saved physical source,
  exact compact-token caps passed, and the service exited 0 without forced kill.

See [the toy receipt](toy-check.json). This is a fresh app-folder/environment test
on the existing Mac, **not an independent or factory-clean Mac**. It used macOS
27.0.1 and the existing Apple Git/command-line tools. One-run battery-power wall
times in receipts are observations, not installation or retrieval benchmarks.

## Own repository, MCP, restart and support

The actual packaged launcher indexed the clean public Flask checkout at
`d086db856be187255b8ec61ef409357393020f32`, scope `src/flask`: 24 files and 358
implementation windows. The first index had 358 uncached documents and completed
successfully. Two final terminal-driven sessions then reopened all 358 saved
vectors with zero pending document embeddings. Query embedding still runs.

Both final sessions delivered the expected `open_session` source for the typed
terminal query, used the own-repository defaults (8 results, 12,000 source
characters, 4,096 compact tokens, dependency context enabled), and passed `:mcp`,
`:doctor` and normal `q` shutdown. The generated first-session MCP configuration
was reused unchanged after the service restarted at the same fixed port.
Each session passed two default official MCP calls, with physical excerpt/hash
and exact budget verification. The client/adapter Python socket guards observed
zero outbound attempts; the resident backend is outside that guard's scope.
Both final services and the initial index session stopped cleanly without forced
kill. See [ownrepo.json](ownrepo.json) and the separate
[reopen](ownrepo-reopen-mcp-verification.json) and
[restart](ownrepo-restart-mcp-verification.json) verification receipts.

Two qualification-harness issues are preserved explicitly. The toy adapter had
populated the normal fallback tokenizer cache, while the stricter generated-config
check requires that cache absent. The first MCP validation therefore stopped
before its calls. Its successful cold index and clean shutdown are retained in
[the initial attempt](before-mcp-cache-isolation/failure.json). The owned fallback
cache was preserved outside the app to test the generated warmed-cache environment
in isolation. A later receipt filename collision overwrote detailed successful
MCP reports with generated configs; those transcripts remain in
[the correction record](before-mcp-receipt-name-correction/correction.json).
The two sessions were repeated with distinct report/config filenames. Final
reports retain all checks. Neither correction changed shipping source or ZIP bytes.

Standalone `--doctor` and the bundled local feedback collector passed. The feedback
record is scripted with zero external testers; it is not adoption or external
quality evidence. Previously observed public queries are integration probes, not
a new retrieval-quality benchmark.

## Automated and editor validation

All **184 focused release tests** passed. Linux GitHub CI at the candidate's
metadata/editor commit passed **2,509 core tests, 10 skipped**, the installed
lexical CLI smoke and the editor job; see [CI metadata](initial-ci.json) and
[its test summary](ci-test-summary.log).

The new exact VSIX passed 177 editor/helper tests and an actual isolated VS Code
1.142.0 arm64 CLI install/list check. All packaged installed files were verified.
Only its version/container metadata and README differ from v0.1.2; all nine runtime
JavaScript members and notices are byte-identical. Native GUI activation was not
rerun for this metadata update. See [editor findings](editor/findings.md).

After publishing, public download hashes and a second fresh-folder setup from the
anonymously downloaded ZIP will be recorded separately. Independent Mac beta
testing, broader coding-workflow benefits and agent token savings remain
unestablished. The release changes onboarding diagnostics, not retrieval defaults.
