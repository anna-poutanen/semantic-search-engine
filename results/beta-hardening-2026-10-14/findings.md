# Mac beta hardening qualification

This work qualifies the 0.1.2 preview on the existing M4 / 24 GB / macOS 15.1.1
Mac. Runs span October 14–15, 2026. There are still zero independent external
beta testers, and this is not a clean-machine installation claim.

## Product changes

- Own-repository terminal searches now use eight excerpts, 12,000 source
  characters, 4,096 compact tokens and dependency context. Caps are configurable;
  the toy keeps its smaller defaults and its numbered query menu.
- Initial indexing has a separate 1,800-second allowance; individual HTTP
  requests default to 120 seconds. Startup reports actual scanning, extraction,
  cache, embedding, save and readiness counts. Failed searches permit retries;
  abandoned transport requests receive a best-effort cancellation.
- Explicit repository-relative exclusions support tracked generated/template
  files. The selected scope is verified against service readiness and participates
  in the persistent cache identity. Invalid included source still fails visibly.
- The small download includes offline allowlisted diagnostics and the local
  feedback collector. Feedback is saved locally and is not uploaded.
- Every ready session writes portable and VS Code MCP snippets, with the exact
  dedicated interpreter, loopback service address and warmed tokenizer cache.
  A fixed-port option supports reuse across restarts. Client settings are not
  automatically modified. MCP initialization advertises the installed version.
- Repeated Ctrl-C, termination and hangup signals during owned shutdown are
  deferred until bounded process cleanup and the session receipt finish.
- Document embedding groups are bounded by both four documents and 8,192 padded
  tokens. The per-document 8,192-token limit and source extraction are unchanged.
  Updated model metadata causes a one-time vector cache rebuild; custom match
  policies need recalibration. The launcher uses no match policy.

## Preserved failures and corrective checks

The first full Django attempt failed on the tracked JavaScript template
`django/views/templates/i18n_catalog.js`. That file is not standalone JavaScript.
The failure and session receipt are retained. The first stress attempts
explicitly exclude that one file; no Django source is edited.

Before token-aware batching, a cold run reached 10,844 of 11,601 unique documents
and slowed severely in its long-document tail while system memory pressure was
high. It was interrupted; bounded cleanup escalated to SIGKILL. This is an
unsuccessful run, not clean shutdown evidence. Its progress and forced-shutdown
receipt are retained. Memory pressure and slowdown coincided; this is not an
isolated causal performance experiment.

The adaptive-batch stress attempt reached 10,791 of 11,601 documents before being
deliberately interrupted, with clean exit and no forced kill. A CPU source-shape
investigation found 853 windows longer than 32,000 rendered characters, all from
three vendored minified JavaScript files. Physical-line extraction repeats the
large shared line for different functions. That character threshold is not a
token measurement. This incomplete stress attempt is retained separately; it
does not qualify the complete unfiltered index. See
[the source-shape receipt](minified-source-profile.json).

The follow-up acceptance explicitly also excludes `django/**/*.min.js`. Both
exclusions are displayed and bound to a separate persistent cache. This selected
scope retains other eligible Python/JavaScript/TypeScript source; it makes no
claim about an unrestricted Django index. The download guide explains explicit
minified/vendor scope choices. There is no new automatic minified-file exclusion.

The corrected batching passed actual model-forward parity against four fresh
singleton document forwards using original public Django Python spans. Inputs
had 3,781 / 1,006 / 3,114 / 1,008 tokens; adaptive groups used 2,016 and 7,562 padded
tokens. Token IDs and input order matched; the maximum observed component
difference was zero. The fixture deliberately uses recorded 480-line extraction
spans to exercise longer inputs; production extraction remains unchanged.
This finite parity probe establishes neither general retrieval quality nor a
throughput improvement. See [the actual receipt](embedding-batch-parity.json).

Initial compatibility-test failures and an incorrect new MCP test assertion are
preserved in their logs. The test assertion was corrected to the existing RPC
helper's result envelope; subsequent launcher/MCP checks passed.

The first fixed-port reopen exposed a preflight bug: a plain probe socket rejected
the old port during TCP TIME_WAIT although the listener had stopped. The probe
now uses SO_REUSEADDR, matching HTTPServer. Real TCP tests reproduce the old error,
permit reuse after server-first close and still reject a live listener. Actual
same-port reopens pass with the corrected launcher.

## Qualification scope

The source ZIP was extracted outside Git into an owned temporary directory with
spaces in its name. It received a fresh managed Python and package environment,
but used the already downloaded pinned uv executable and a verified local model
copy. Later candidate updates preserved that owned runtime and cache. The exact
final asset and extracted-byte binding are recorded separately.

The exact 55,345-byte 0.1.2 VSIX passed ordinary installed-profile activation,
preview/navigation and actual Restricted Mode checks. Those checks use an authored
zero-model HTTP fixture and explicitly disable trust for the trusted phase. All
owned editor processes and profiles were cleaned up. This remains separate from
real Qwen service/MCP acceptance. See [ordinary receipt](ordinary-run/receipt.json).

The doctor passed on the extracted runtime and verified physical model weights.
It counts supported tracked files without parsing them or applying index
exclusions; it can therefore pass while indexing later rejects invalid source.
The bundled feedback command produced a local allowlisted report. This is a
scripted local check, not external user feedback.

The selected Django scope completed **10,734 windows from 966 source files**.
It built 10,734 new document embeddings. Two later process starts reused all
10,734 with zero new document embeddings, including an immediate same-port
restart. Observed service readiness was 557.10 seconds for the fresh vector
build, then 11.40 and 10.31 seconds after process spawn. These battery-power
observations exclude package setup/downloads and are not a latency benchmark.

The generated MCP snippet passed an actual pinned-SDK call under Python socket
guards, with zero outbound attempts by the client/adapter. Both source-authored
spot checks delivered the complete expected implementation: database-session
expiry and signed timestamp expiry. All 16 delivered excerpts matched saved
physical lines/hashes and exact compact-token caps; both calls returned eight
excerpts. These are two integration checks, not held-out retrieval evidence.
The old snippet worked again after restart; both configuration formats remained
byte-identical at the same port. A separate terminal search displayed eight
results and dependency labels. See [spot-check results](mcp-spot-check-results.json)
and [the acceptance summary](qualification.json).

The final ZIP also passed three real HTTP and three official MCP toy searches,
followed by clean owned-service shutdown. The final archive's 61 entries still
matched the extracted app after these runs. The cold Django candidate differs
from the final archive only in the fixed-port preflight correction; its embedding,
index and protocol code are unchanged. Final-archive reopens, MCP reuse and the
toy check exercised the corrected package. All successful acceptance services
stopped cleanly, their ports closed and their product processes were absent.

The expanded core run passed **2,440 tests with 9 skipped**. After the final
shutdown and port changes, the focused launcher/MCP/bundle suite passed **130 tests**,
including real repeated-interrupt and TCP checks. Extension tests passed
116 checks and the ordinary-helper regressions passed 61. The retained
`release-test-filename-correction.log` records a mistyped test filename that ran
no tests; the corrected focused invocation is `release-launcher-tests.log`.

Upstream captured source retains [Django's license](NOTICE.md).

## Publication

[Version 0.1.2 preview 1](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.2-preview.1)
is published at the qualified `ced5bcbd2b25d127e939d19797e414f4d0938215` commit.
GitHub CI passed both jobs on that commit: 2,445 core tests passed with ten
platform/optional skips, the installed lexical CLI smoke passed, and the editor
job passed. These Linux counts are separate from the local Mac runs above.

The 233,252-byte ZIP, 55,345-byte VSIX and checksum file each downloaded once
without authentication and matched both their local hashes and GitHub asset
digests. These synthetic verification requests are not user adoption. See
[publication proof](publication.json). Owned temporary runtime/model/index
copies were removed after process and port checks; the developer runtime, main
model caches and public corpus remain. See [cleanup](cleanup.json).

## Remaining beta work

Recruit independent Apple Silicon users and validate first-run setup, memory
headroom, MCP-client configuration and useful results on their own repositories.
There is no Windows/Linux release or established coding-agent token-saving claim.
