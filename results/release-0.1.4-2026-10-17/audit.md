# Independent v0.1.4 candidate audit

The final candidate passes independent archive and source-binding checks. This
audit binds the guide-clarified artifact built after `1644590`. It does not yet
claim completion of the final artifact's native setup, resume, publication or
anonymous public-download checks; those are tracked by the release owner.

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
**2,566 passed, 10 skipped** in 71.51 seconds. No archive or process-lifecycle
blocker remains in this audit. Final native and publication claims must be
bound to the final ZIP above, rather than earlier guide-only candidates.
