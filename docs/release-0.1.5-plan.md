# v0.1.5 release qualification plan

Package the already tested production coverage policy from main commit
`1804b85` as a regular GitHub release. This release does not promote the failed
shared-flow selector or any new selection experiment, change model weights,
expand context limits, add generation, or establish coding-agent savings.

The product decision was explicitly post hoc. The prior small TanStack Query
sample completes 2/8 dense multi-file behaviors with coverage versus 1/8 with
legacy, with no covered group lost. The original frozen study and integration
parity remain distinct evidence; packaging introduces no new retrieval study.
The final main check passed 3,514 core tests and the established 179 editor/helper
checks. These historical counts are not substituted for this release's checks.

## Before live setup

1. Update Python, extension and package-lock versions and current download docs.
2. Preserve all historical release/research receipts. Create new qualification
   scripts/results in `results/release-0.1.5-2026-10-29` using the corrected v0.1.4
   observers, without editing the previous scripts or receipts.
3. Run current CPU Python, editor and ordinary-profile helper checks. Commit the
   exact candidate source before building assets; do not include work from the
   parallel research branch.
4. Build the VSIX and small ZIP from those committed source bytes. Verify archive
   inventory/CRCs, package/container/version/license identity, compiled files,
   exact embedded VSIX and SHA256SUMS. Record commit and every payload hash.
5. Run the new `qualify_assets.py` to bind all source/guide/notice payloads to
   the candidate commit and compare compiled extension members. Install the exact
   VSIX through ordinary Code CLI into owned isolated profile
   directories, compare installed bytes and record the scope. CLI installation
   is not a new GUI, Workspace Trust or editor retrieval-quality result.

## Exclusive setup/model phase

A root-coordinated runtime/GPU lease is required before this phase. Keep all
model/setup processes serial and use an owned keepawake process that ends with
the qualification process. Do not reuse another phase's service or index.

1. Extract the exact ZIP outside Git into a fresh directory containing spaces.
   Verify every extracted byte against the bundle and initial absence of app
   runtime, model and tokenizer caches. Restrict inherited Python/auth/cache
   settings; never inspect or copy credentials.
2. Run expected missing-runtime diagnostics, invalid-source preflight before
   model/index creation, and corrected preflight with an explicit exclusion.
   Retain failures and warnings. Download a fresh managed Python, locked runtime
   and pinned Qwen weights through the unmodified launcher, then run `--check`:
   three HTTP and three official MCP toy searches with exact source/budget checks
   and owned-service cleanup. Record physical weight hashes and package version.
3. In an owned clone of pinned public Flask, complete a small seed index, restore
   all production tracked files, observe a newly committed partial embedding
   batch, stop/kill only the owned launcher/service groups and inspect SQLite
   after termination. Never read SQLite while a writer is stopped.
4. Repeat the same launcher command. Verify durable vectors survive, only missing
   documents are embedded, the old active snapshot remains until complete atomic
   publication, and all windows are available afterward. Restart once more and
   require zero document inference.
5. Exercise typed terminal searches, generated MCP config display/reuse after
   restart, exact saved-source and compact budgets, offline diagnostics, local
   scripted feedback and clean shutdown. Label scripted feedback as integration
   evidence, with zero independent external testers.

Use the new `qualify_setup.py` and `qualify_resume.py`; output directories and
attempt paths must be fresh. Retain any failed run before corrections and repeat
necessary checks on the final exact artifact. No source or response is fabricated
to simulate a native indexing/resume result.

## Publish and independently fetch

After qualification and green CI, bind the merged/tagged source to every packaged
payload and publish the three exact assets as regular `v0.1.5`, latest rather than
prerelease: Mac ZIP, standalone VSIX and checksums. Use the new public verifier to
fetch all assets anonymously, verify GitHub API digests, tag target, regular/latest
status, safe manifests and exact local bytes. Then perform a separate fresh setup
and interruption/resume sequence from the public ZIP, with new caches and receipts.

Report the release URL, exact source/assets, passed checks, preserved failures and
one-Mac limits. Do not claim external beta adoption, arbitrary-repository quality,
paid-token savings, power-loss durability, throughput or new comparative latency.
