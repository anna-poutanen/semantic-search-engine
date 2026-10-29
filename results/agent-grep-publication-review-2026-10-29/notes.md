# Publication review execution notes

This is a new posthoc review on October 29, 2026. No original attempt, outcome,
grade, usage receipt, clock sample, historical report or original source file
was replaced. The initial offline-verifier development run failed because
`tokenize.detect_encoding` received bytes instead of a callable `readline`;
correcting that call allowed all literal snapshots to be checked. This was a
new review implementation error, not a historical trial failure or repair.
The completed review and its adversarial tests pass.

The input recovery retains all 64 source/control inputs named by the original
frozen configuration and 19 earlier preflight files. Some tracked historical
source was recovered by matching SHA-256 bytes in existing Git blobs; pending
uncommitted experiment files and ignored evaluator controls were recovered from
the original local checkout. The input manifest states each retrieval source.
These recovery identities are not prospective Git proof for the experiment.

The original 57 targeted tests remain unchanged. Twenty-four new tests cover the
posthoc verifier, including invalid usage, missing receipts, timing drift,
duplicate MCP payloads, self-consistent forged excerpts, source line boundaries,
structured-payload duplication, path traversal and complete retained evidence.
The final targeted suite passes 81 tests. Validation checks the original capture
and multifile harness too; it does not rerun historical graders or model work.

## Initial GitHub CI packaging failure and repair

The first Linux CI run for publication commit `e2ab4e1` failed one evidence
integration check (3,222 passed, 10 skipped). Twenty recovered evaluator-control
files existed locally but were excluded from the commit by the nested `.cache`
ignore rule. This was a publication packaging error; local disk verification
had not checked whether every manifest-bound input was tracked. The original
historical artifacts and outcome bytes were unaffected.

The repair force-adds only the twenty exact, hash-verified manifest-bound files.
A separate repository integration test checks Git tracking of all 68 recovered
frozen/review source entries and 19 earlier preflight entries. It skips when
Git or the repository checkout is unavailable; pure evidence fixtures do not
require Git. The bounded original failure log is retained as
`initial-linux-ci-failure.log.gz`, after a scan for credential-shaped values.
The original full-suite Mac result remains historical validation; the repair's
focused tests and offline audit are recorded separately.

After the repair, all 82 focused tests and the offline audit pass. All 87
input-manifest paths are now in the Git index. `ci-repair-validation.json` and
`ci-repair-audit.json` retain the new checks separately from the prior receipts.
