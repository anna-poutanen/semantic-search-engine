# v0.1.4 release qualification

The release candidate packages automatic completed-batch resume and fixes a
demo MCP cancellation race found during qualification. The exact Mac ZIP is
241,525 bytes, SHA-256
`64e152ccfd23f5e1a07b16ee8427dcb8368011d46e447a63a457c39b7741bba2`.
Its separate and embedded VSIX are identical: 55,344 bytes, SHA-256
`66d7db1d983b9d87a6eb7ef0742de3564f606a831add7aaf9c055ffcc95032c1`.
See [the bundle receipt](bundle.json), [checksums](SHA256SUMS.txt) and
[independent audit](audit.md).

## Full fresh setup

All three assets were downloaded from the draft release and compared byte for
byte with the local build. The ZIP was extracted outside Git, into a new path
containing spaces with no application cache. The launcher downloaded its own
managed Python 3.12.15, 74-package runtime and pinned unquantized Qwen embedding
weights. No runtime, tokenizer or model cache was preseeded. Child PATH and
inherited Python/authentication settings were restricted or removed. The
checkpoint's actual weight-file SHA-256 matched its pinned provenance.

The [fresh setup receipt](cold-setup.json) records expected missing-runtime
diagnostics, invalid-source preflight failure before weights or index creation,
successful preflight after an explicit exclusion, and `--check` completing
three real HTTP and three official MCP searches with exact source checks and
clean owned-service shutdown. The [toy receipt](toy-check.json) preserves those
search checks. Qualification uses the existing Apple M4 Mac with 24 GB memory
and macOS 27.0.1, rather than an independent computer.

## Interrupted indexing through the actual launcher

[The resume receipt](resume-integration.json) uses the unmodified extracted
launcher, installed runtime, model and SQLite index. No inference function or
batch schedule was patched. An owned clone of public Flask at
`d086db856be187255b8ec61ef409357393020f32` supplied original saved code; its
[license](FLASK_LICENSE.txt) is retained.

| Stage | Cached documents at start | New documents embedded | Active windows after stage |
| --- | ---: | ---: | ---: |
| Track only `sessions.py`; complete a seed index | 0 | 20 | 20 |
| Restore all 24 production files; interrupt after one completed batch | 20 | 4 | 20 |
| Restart the same command and finish | 24 | 334 | 358 |
| Restart once more with the same cache | 358 | 0 | 358 |

The parent observed committed cache growth using a separate SQLite read
connection, stopped the known launcher/service process groups, inspected their
state, then sent SIGKILL. Before and after termination, all four active-table
hashes matched the seeded snapshot. The complete cache digest, including vector
bytes, also matched across termination. The interruption left exactly 24
durable documents while the previous active snapshot still contained 20.
The immediate [interruption witness](interruption.json) records just that phase;
the full resume receipt records successful completion of all later checks.

The restarted launcher reported 24 cached and 334 pending documents, completed
exactly those 334 and published all 358 windows. A later restart reported zero
pending documents and emitted no document-embedding progress. Both completed
sessions passed a typed terminal search, generated MCP configuration display,
diagnostics and two real generated-config MCP queries. The earlier MCP config
was reused after restart. The guarded verifier checked exact excerpts and
budgets, package version 0.1.4 and zero outbound Python socket attempts. Both
sessions closed normally without forced service termination. Scripted feedback
collection also passed; it does not represent external testers.

## Tests and package installation

The final local Python suite passed **2,566 tests, with 10 skipped**, in 71.51
seconds; [the complete log](core-tests.log) is retained. The new deterministic
spawn-handoff regression reproduces the original MCP cancellation failure and
passes with the fix. The 24-test demo suite and five additional serial real
process trials also passed. The fix defers cancellation only until the official
SDK has armed transport cleanup, then permits handshake/search cancellation.

Editor compilation/tests and 61 ordinary-profile/shared-storage helper tests
passed. [Exact VSIX qualification](editor/findings.md) checked all compiled
members, version/container/license identity, ordinary CLI installation in a
fresh isolated Code profile and all installed payload bytes. All nine runtime
JavaScript files remain byte-identical to v0.1.3. Native GUI activation and
real-model extension searches were not rerun.

## Preserved earlier attempts

- [Initial core failure](before-mcp-cancellation/failure.json): the original
  candidate passed 2,563 tests but failed one real MCP SIGINT cleanup check.
  The race was reproduced, fixed and covered before the final ZIP was built.
- [Pre-guide-clarification setup](before-guide-clarification/scope.json): an
  earlier draft with the same runtime code passed a full fresh setup. Only the
  bundled guide and its manifest changed afterward, clarifying the current OS
  and compatibility of v0.1.3 caches. The final ZIP was independently downloaded
  and set up fresh again; earlier success is not substituted for final evidence.
- [External MCP verifier precondition](before-mcp-cache-precondition/failure.json):
  the first interruption run completed production resume, then the test driver
  failed its generated-cache-only MCP precondition because its direct preflight
  omitted the launcher's tokenizer-cache setting. The driver was corrected and
  the entire own-index interruption/resume/MCP sequence repeated from an empty
  owned index. Release code and assets did not change for this harness fix.

## Scope and reproduction

This establishes download/setup, real-model indexing durability and local
source/transport integration on this Mac. It is not a new retrieval-quality,
coding-benefit, latency, throughput, power-loss or independent-machine study.
The earlier [twelve-window vector-parity proof](../resumable-indexing-2026-10-17/findings.md)
remains separate. An in-flight batch can repeat; exact document/model matches
and the same cache location are required for reuse.

Run [qualify_setup.py](qualify_setup.py) against a directory containing the three
release assets, then [qualify_resume.py](qualify_resume.py) with that directory
and a clean local Flask checkout. Both accept `--output-directory`; their output
paths must be unused. Private owned runtime/model/index data remain outside the
public receipts. [verify_public_release.py](verify_public_release.py) performs
the separate post-publication anonymous asset and tag-target check.
