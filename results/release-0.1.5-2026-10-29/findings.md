# v0.1.5 pre-publication qualification

The exact candidate assets pass local file, installation and live launcher
qualification. They are **not yet published**. The candidate is
`865cd49e77049f05477e75d960f056037ec63c25`, based on main `1804b85`. No new
parallel research or product runtime change is included after that base. This
packages the already tested production coverage policy; it does not promote
the failed shared-flow experiment.

The Mac ZIP is **265,146 bytes**, SHA-256
`893c0739c2d85cfcf6e0edb197252e1e8124e4a3539306e8243e1a65f693410b`.
[The asset receipt](editor/asset-qualification.json) records the standalone VSIX
and checksums, every packaged source/guide/notice binding to the candidate,
18 compiled extension members and isolated ordinary Code CLI installation with
23 installed members verified. The [bundle receipt](bundle.json) and
[checksums](SHA256SUMS.txt) identify the exact tested files.

## Checks performed

- **Current CPU/editor:** 3,514 Python tests passed, 10 skipped; 118 extension
  and 61 ordinary-profile/helper checks passed. Complete logs and the separate
  preparation receipt are retained.
- **Fresh setup:** the ZIP was extracted outside Git into a new path containing
  spaces, with no app caches preseeded. The launcher downloaded its own managed
  Python, locked runtime and pinned unquantized Qwen weights. Missing-runtime
  diagnostics and invalid-source preflight failed as expected; an explicit
  exclusion passed without automatic scope changes. Preflight preceded model
  and index creation. Three real HTTP and three official MCP toy searches
  verified exact source/budgets and normal owned-service shutdown.
- **Actual interruption/resume:** an owned public Flask clone at
  `d086db856be187255b8ec61ef409357393020f32` established a 20-window seed index.
  During full indexing, an independent committed-vector witness was captured
  before stopping/killing only owned process groups. After termination, 28
  durable documents remained and all four active-table hashes still matched
  the original 20-window snapshot. Committed overshoot is allowed and measured;
  no SQLite read was attempted while the writer was stopped.
- **Resume and cache-only restart:** repeating the same launcher command reused
  the 28 durable documents and embedded exactly the 330 missing documents,
  then published all 358 windows. Another restart reused all 358 documents with
  zero document inference. Both sessions passed typed terminal searches,
  generated MCP configuration display, diagnostics and guarded MCP checks;
  the initial generated config was reused after restart. Scripted local feedback
  passed and represents zero independent external testers. Both sessions shut
  down normally; no extraction-owned process remained afterward.

See [fresh setup](cold-setup.json), [toy checks](toy-check.json),
[the immediate interruption witness](interruption.json),
[the complete resume receipt](resume-integration.json) and
[the bounded summary](prepublication-summary.json).

## Source-only correction and remaining work

Before any asset or native setup run, the new asset qualifier was corrected to
verify VSCE's intentional relative README link rewriting. The deterministic
transform reproduced the old v0.1.4 artifact exactly; the original and corrected
qualifier source are retained in Git. [Execution notes](execution-notes.md)
distinguish this harness correction from a product or native failure.

After green CI and source binding to the merged/tagged target, publish the exact
three assets as regular/latest `v0.1.5`. Then anonymously download and verify
all three, and repeat fresh setup plus interruption/resume in a separate public
extraction. Those checks remain pending and must not be inferred from this run.
The exclusive pre-publication setup/model lease has ended.

This is one-device release integration evidence, not independent Mac beta,
new retrieval generalization, coding correctness, billed-token savings,
comparative latency, throughput or power-loss certification. The earlier
2/8-versus-1/8 dense multi-file sample is separate from these release checks.
