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
