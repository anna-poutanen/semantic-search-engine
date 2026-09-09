# Reproducible Mac demo passes

The explicit `demo_mac.py --run` invocation passed three real Qwen HTTP searches
and three searches through the official MCP stdio client. Each delivered three
exact saved excerpts under its compact context cap. The MCP execution also
checks exact text/structured agreement and tokenizer counts. H2O stayed off.
The owned service shut down cleanly without a forced kill; its port was closed
when the post-run companion was captured.

The [redacted receipt](demo.json) retains verified source summaries, rather than
raw transport responses. A separate [CPU companion](runtime-proof.json)
independently compares all18 excerpt summaries to the toy's saved physical
lines/hashes and records source/package identity after shutdown. This is an
integration demonstration, not unseen-code quality or performance evidence.

Default preparation is standard-library-only, starts no model and installs
nothing. Explicit run mode creates a fresh ignored Git toy and never reuses an
existing folder. See the [demo guide](../../docs/demo.md) for manual CLI, HTTP,
MCP and editor workflows. The fixture, index, service log and original receipt
remain in ignored local storage for inspection.
