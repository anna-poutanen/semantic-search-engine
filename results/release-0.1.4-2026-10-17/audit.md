# Independent v0.1.4 candidate audit

The final candidate passes independent archive, source-binding and native
integration-evidence checks. This audit binds the guide-clarified artifact built
after `1644590`. The final artifact's native setup and resume receipts have been
checked; publication, final tag-target CI and anonymous public-download checks
remain pending and are tracked by the release owner.

## Exact artifact

The audited ZIP is `semantic-search-mac-0.1.4.zip`, **241,525 bytes**, SHA-256
`64e152ccfd23f5e1a07b16ee8427dcb8368011d46e447a63a457c39b7741bba2`.
All 61 payload files match their manifest hashes and byte sizes. Those files
plus `BUNDLE_MANIFEST.json` account for all 62 archive entries, with no duplicate,
escaping or symlink entries. Every entry is a regular file. The launcher mode
is `0755`; other entries use `0644`. The checksum file matches the ZIP and VSIX.

Every packaged Python source, launcher, setup script, dependency input, license
and notice matches the release checkout. The 32 application Python files
independently recompute to application-source digest
`d9263cc255140ac0b2371cc027188251b0307f2c781c105cac85a9eed54bbf8d`.
The packaged store is SHA-256
`2064461031a205fe94a32392578fe42692105d690a9e05eb5f96d434f6a98994`;
its durable checkpoint/canonical-cache logic and the backend/embedding batch
callbacks are included. The external qualification driver is intentionally not
part of the downloadable application.

The packaged project, embedded VSIX package and VSIX container all declare
version `0.1.4`. The embedded VSIX exactly matches the separate **55,344-byte**
`semantic-search-local-0.1.4.vsix`, SHA-256
`66d7db1d983b9d87a6eb7ef0742de3564f606a831add7aaf9c055ffcc95032c1`.
Its publisher/name identity is `anna-poutanen.semantic-search-local`. The
container binds the original project MIT license, and third-party notices are
present. Editor installation evidence is reviewed separately in
[editor findings](editor/findings.md).

The ZIP README exactly matches `docs/getting-started.md`, SHA-256
`b33b710f7df45e595f6f91f17ad684c71481c143e11cd9c1d4cf97de2544f44d`.
It names the v0.1.4 download/release, describes automatic own-repository resume,
distinguishes the fresh-toy flow and states v0.1.3 vector-cache compatibility.
Its Markdown links use absolute HTTPS targets. Current qualification host
information and the historical v0.1.2 batch-policy change are distinguished.

## Cancellation defect and correction

The first full core run found a real adapter-spawn cancellation race: one SIGINT
cleanup test failed while 2,563 tests passed and 10 skipped. The original draft
candidate and failure are preserved in
[before-mcp-cancellation](before-mcp-cancellation/failure.json). It was not a
published release.

The SDK spawns its stdio process before arming transport cleanup. Cancellation
during that handoff can abandon the live adapter and its descendant. A
deterministic real-process regression held the spawn handoff open and reproduced
the old implementation's leak and closed-event-loop warning. Its test cleanup
terminated the explicitly owned processes.

Commit `000e1ca` uses the official exported `stdio_client` and the public Client
transport argument. It shields transport entry until cleanup is armed, then
disables that shield before handshake/search. It does not patch SDK internals in
production. The test fault injection uses the SDK spawn boundary only to make
the original race reproducible.

All **24 demo tests** passed after the correction, including timeout, SIGINT,
spawn-handoff cancellation, spawn error propagation and signal-handler
restoration. The real-process spawn-handoff case also passed **five additional
serial trials**, with both adapter and descendant stopped. These are CPU and
process-lifecycle checks, without model inference or latency claims. The final
ZIP includes the corrected demo script, SHA-256
`09075966511e16986736403accebc8819ab7ee5f87f96e2eb03326a5f50d9346`.

The retained [complete core log](core-tests.log) subsequently reports
**2,566 passed, 10 skipped** in 71.51 seconds.

## Final packaged native flow

The [cold-setup receipt](cold-setup.json) binds the final ZIP above to a new,
initially cache-free extraction outside Git, with spaces in its path. The
controller clears inherited Python, package/model-cache and authentication
settings and restricts its executable search path. Actual setup downloaded its
own pinned uv, Python 3.12.15, runtime packages and original Qwen weights. The
runtime inventory reports 74 packages and Metal availability. All four retained
step-log hashes independently match the receipt.

Invalid tracked source blocked preflight before model weights or an index
existed. An explicit exclusion passed while retaining the included minified-file
warning. The actual packaged `--check` then passed three HTTP and three official
MCP searches and stopped its owned service cleanly. I independently rechecked
all 18 toy excerpt hashes against saved physical files. These summaries preserve
executed compact-token checks, without retaining complete toy MCP envelopes.

The [resume integration](resume-integration.json) uses the unmodified extracted
launcher/service and original Flask source at
`d086db856be187255b8ec61ef409357393020f32`. A sessions-file-only tracked seed
published 20 windows. Tracking the complete saved scope produced 358 unique
documents/windows and 338 pending embeddings. A separate SQLite reader observed
four additional committed documents before the owned launcher/service process
groups were stopped and deliberately killed. All four active-table hashes
remained equal to the 20-window seed; the complete 24-document cache digest and
all active hashes were identical before and after SIGKILL.

A fresh launcher reused those 24 documents, embedded the remaining 334 and
published 358 windows. A later restart reused all 358 with zero pending document
embeddings. Query embeddings still run. The seed, resumed and restart sessions
all report normal return code zero without forced shutdown. Their two completed
terminal queries returned the expected saved session implementation; generated
MCP display, diagnostics and support commands passed. Scripted feedback is
explicitly zero external testers.

The resumed session's generated MCP configuration was reused unchanged after
restart. Both final official MCP clients completed two searches and exited.
I independently rechecked all 32 delivered excerpts against saved physical
Flask source and recomputed exact compact budgets with outbound sockets/DNS
blocked. Each session delivered eight excerpts per call: 4,546/11,517 source
characters and 1,926/3,385 compact tokens. Original client/adapter guards report
zero outbound attempts and interpreter exit. Their scope excludes the separate
resident backend. All original extracted archive files still match the ZIP
after these sessions.

An earlier external qualification preflight omitted the launcher tokenizer-cache
environment, causing a generated-MCP verifier precondition to stop after a real
resume had completed. That attempt is preserved in
[before-mcp-cache-precondition](before-mcp-cache-precondition/failure.json).
The corrected external harness explicitly selects the launcher cache and saves
its interruption witness before optional client checks; final own-repository
qualification reran from an empty index in the same cold-qualified runtime.
This correction did not change shipping source or artifact bytes.

No archive, lifecycle or native integration blocker remains in this audit.
These are checks on the existing development Mac, not independent-machine,
retrieval-quality, coding-benefit or latency evidence. Publication and public
setup must bind the same final ZIP, rather than earlier guide-only candidates.
