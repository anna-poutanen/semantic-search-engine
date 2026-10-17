# Independent v0.1.3 candidate audit

The candidate passes independent archive and integration-evidence review. No
product or packaging blocker was found. This review checked the existing
receipts and source, recomputed artifact hashes and repeated source/token
verification; it did not rerun model inference or change the candidate.

The audited ZIP is `semantic-search-mac-0.1.3.zip`, **239,620 bytes**, SHA-256
`db26c6d38ec6468fd4a7589789d0ccb6150210e4926ae0ae260d6bd3d6cc4df2`.
All 61 payload files match their manifest hashes and byte sizes, and those files
plus the manifest account for all 62 archive entries. The launcher has mode
`0755`; the packaged project version is `0.1.3`. The application source inventory
hash independently recomputes to
`17a6214ba5d21e8de949bc02547073f252a232460cf5f84ccd042d6998ca937c`.
Every packaged application source, launcher, setup script and dependency input
matches the release checkout, and all extracted archive files still match their
ZIP bytes after the integration sessions. The embedded VSIX SHA-256 is
`af5c556a9013e4c2a7d0fa77df9d0eab5136a5204af90a9f1f6e598c14289b48`,
matching the separate [editor qualification](editor/findings.md).

The [cold-setup controller](qualify_setup.py), [receipt](cold-setup.json) and logs
substantiate an initially cache-free extraction outside Git, with spaces in its
path, a restricted executable search path and no preseeded runtime or model.
Logs show actual pinned uv, managed Python 3.12.15 and package downloads. The
runtime probe checks 74 package versions, the dedicated environment and Metal.
The invalid tracked template blocks preflight before any model directory exists;
an explicit exclusion succeeds while still warning about the included minified
file. The subsequent unauthenticated Hub download verifies the original pinned
weights SHA-256. All four step-log hashes match `cold-setup.json`. The
[toy receipt](toy-check.json) records three real HTTP searches, three official
MCP searches, saved-source verification and clean owned-service shutdown.

The own-repository evidence uses public Flask revision
`d086db856be187255b8ec61ef409357393020f32`, `src/flask`: 24 source files and
358 windows. The initial retained service log shows 358 new documents embedded;
the two final sessions each reuse all 358 with zero pending documents. Their
terminal queries, generated MCP display, diagnostics and clean `q` shutdown
agree with the [controller](qualify_ownrepo.py), [summary](ownrepo.json), service
logs and session receipts. The first generated MCP configuration is reused on
the second restart; both copied configurations have the same digest, matching
the digests recorded by both verification reports.

I independently rechecked all **32 delivered excerpts across four final MCP
calls** against saved physical Flask lines and SHA-256 hashes. For each pair of
queries, the delivered source totals are 4,546 and 11,517 characters and exact
compact counts are 1,926 and 3,385 tokens. Every call respects the eight-result,
12,000-character and 4,096-token limits. These rechecks ran with socket
connections and DNS blocked, using the already-warmed tokenizer cache. Both
retained MCP reports show guard installation, adapter exit and zero outbound
attempts in the original Python client/adapter runs. The guard does not cover
the separate resident backend or arbitrary native processes.

Two qualification-harness corrections are preserved transparently. The initial
MCP verifier stopped on its cache-isolation precondition after the toy had
populated a fallback tokenizer cache; the successful initial index and clean
shutdown remain in [before-mcp-cache-isolation](before-mcp-cache-isolation/failure.json).
Later, a controller filename collision overwrote successful verification reports
with generated configurations. Those transcripts remain in
[before-mcp-receipt-name-correction](before-mcp-receipt-name-correction/correction.json).
The corrected controller uses distinct filenames and repeated both warm
sessions, retaining full reports. Neither correction changed shipping code or
ZIP bytes.

The final offline diagnostic reports all checks passed, including model
integrity. The feedback record is scripted and represents zero external testers.
These are integration checks on the existing Apple Silicon development Mac,
running macOS 27.0.1. They do not establish independent-Mac compatibility, new
retrieval-quality results or latency benchmarks; the runs used battery power.
Editor runtime byte equivalence and new package/CLI installation checks are
documented separately; native GUI acceptance was not repeated for this version.

Selected evidence SHA-256 bindings at audit time:

| Artifact | SHA-256 |
| --- | --- |
| `cold-setup.json` | `ed1c0a6b5a1e20715c4575caab245ea48a6d23e65ccf972d8d94666102f50f2c` |
| `ownrepo.json` | `4322c6591fa8c50a281c1f414cca59473ba0e22628c34758204a5d0578d3b842` |
| `ownrepo-reopen-mcp-verification.json` | `84b12f81b07bf3630250b6380bed8844d06db37853e1085ffad267a87ce61aec` |
| `ownrepo-restart-mcp-verification.json` | `72e9073249fc66194422d9d724e4254d8e76c05aba8484c3e9b4cca5b36ee3c5` |
| Both final generated `mcp-config.json` files | `897413cd12daf3ccfb38bb0ac3826ecd69007bb4adb00c50df77aa52afc708a3` |
| Both final own-repository service logs | `ea7929b5afa42fc9cac40ccbac436629859ecc87d7b92509f400370a422eabca` |
| `doctor-after-setup.log` | `e7cb30f0d8e4820343eb5b71830b71762975dbd2959846727e95d1536474f2fe` |
| `scripted-feedback.json` | `ddfb1cc5bf5050d939faf8d8fd76750d392b6b470d1d5267db7edb1689e81124` |

This is a candidate-stage audit. Final commit CI and unauthenticated downloaded
public-asset verification are publication gates tracked by the release owner;
they had not yet completed when this audit was written.
