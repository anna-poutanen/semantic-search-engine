# v0.1.5 public download and setup qualification

[Regular v0.1.5](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.5)
is published and latest at merged commit
`3d8d32c419473b1f078d12c79f139256890c21ad`. All three assets were downloaded
anonymously on the first attempt and match the exact pre-publication qualified
bytes, GitHub API digests and checksums. The public ZIP's safe payload inventory,
source/guide/notice hashes and embedded VSIX also pass. See
[public verification](publication.json) and
[the final bounded summary](postpublication-summary.json).

The tested ZIP is **265,146 bytes**, SHA-256
`893c0739c2d85cfcf6e0edb197252e1e8124e4a3539306e8243e1a65f693410b`.
The standalone/embedded VSIX is **55,632 bytes**, SHA-256
`616fd79c7b8466140f7296c4317e3c7bb0f8be3b77631f8dc87fa9baea98bdbe`.
The exact artifact source candidate is
`865cd49e77049f05477e75d960f056037ec63c25`; merged-source binding is separately
recorded by the release publisher. Prior receipts are preserved as their original
pre-publication snapshots rather than rewritten as public-download evidence.

## Separate cache-free public extraction

The anonymously downloaded ZIP was extracted into another new, outside-Git
folder containing spaces. No app runtime, model or tokenizer cache was preseeded.
The unmodified launcher downloaded its own managed Python, locked packages and
pinned Qwen weights. Planned missing-runtime diagnostics and invalid-source
preflight negatives were retained. Explicit exclusion passed before model/index
creation; six real HTTP/MCP toy searches then passed exact-source/budget checks
and normal owned-service shutdown.

[The fresh setup receipt](public-download-setup/cold-setup.json) and
[toy receipt](public-download-setup/toy-check.json) describe this public extraction,
separately from the earlier pre-publication run.

## Public interruption/resume and MCP setup

An owned clone of pinned public Flask repeats the complete unmodified launcher
flow. No inference/batch function was patched.

| Stage | Cached documents | Newly embedded documents | Active windows |
| --- | ---: | ---: | ---: |
| Complete sessions-only seed | 0 initially | 20 | 20 |
| Interrupt expanded indexing | 28 after termination | 8 durable additions | 20 |
| Resume identical command | 28 initially | 330 | 358 |
| Restart again | 358 initially | 0 | 358 |

All four original active-table hashes survived the interruption. Every vector
witnessed committed before the stop survived SIGKILL; measured additional commits
between observation and termination are permitted. SQLite was inspected while
the writer ran and after death, never while the writer was stopped.

Both completed sessions passed typed terminal queries, generated MCP config
printing, guarded official MCP queries, diagnostics, local scripted feedback and
normal shutdown. The initial generated configuration was reused after restart,
including its warmed tokenizer-cache setting. Scripted feedback represents zero
independent external testers. No extraction-owned process remains.

See [the immediate interruption witness](public-download-setup/interruption.json),
[full resume checks](public-download-setup/resume-integration.json),
[guarded resumed MCP](public-download-setup/resumed-mcp-verification.json) and
[restarted MCP](public-download-setup/restart-mcp-verification.json).
No step was retried or substituted.

## Scope

The source/asset qualification, 3,514 passing Python tests (10 skipped), 179
editor/helper checks and isolated ordinary VSIX installation remain in
[pre-publication findings](findings.md). The source-only README link-rewrite
qualifier correction is retained there; there was no public native-flow failure.

These two initially cache-free setups ran on the same existing Mac. This proves
local download/setup, process-interruption durability and source/client
integration for the tested workflows. It is not independent-machine beta,
new retrieval relevance, coding correctness, paid-token savings, comparative
latency, throughput or power-loss certification. The more complex shared-flow
selector remains experimental; no parallel research was added to this release.
