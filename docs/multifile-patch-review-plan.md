# Independent multifile patch review

This review supplements the frozen sixteen-trial study. It does not replace or
adapt the selected behavioral and original-upstream grades. Review every retained
attempt, including timeouts, infrastructure failures, partial patches, and missing
usage. Review each completed artifact as it arrives, without sending feedback to
the coding trial or changing later task prompts, checks, or runtime.

## Evidence reconciliation

For each `(task_id, repetition, condition)`, check run identity, captured patch
hash, base-source inventory, prompt hash, process outcome, and phase timestamps.
The authoritative selected result is `grade`: exit zero and the exact frozen
reference JSON, with 9, 13, 9, or 10 declared test methods for the respective task.
Check JSON against preserved stdout and failure diagnostics. Baseline validation
must equal the declared unmodified feature result; it is not a repair grade.

The separate `compatibility` result restores original pinned tracked tests,
fixtures, and test configuration in an evaluator-only copy of the candidate.
Verify every command's actual seven-category pytest outcome equals its frozen
expected outcome, rather than relying on exit zero or an agent claim. The
Requests tasks each expect 257 passed/13 skipped, Click 307/1, and Starlette
269/2, with all other categories zero. Preserve command logs and distinguish
grader timeouts/errors from source failures. Reconstruct candidate copies for
additional CPU checks only outside the agent workspace.

`correct` requires both frozen gates. `completed_correct` additionally requires
normal completion, zero agent exit, no timeout, and no recorded failure. Report
these labels exactly as the runner recorded them. They establish success on
selected contracts and compatibility suites, not complete correctness of the
libraries.

## Patch and test review

Read every production edit and every changed, removed, or added test/configuration
file. Confirm the requested public behavior is coordinated across source layers
and inspect nearby default behavior, error handling, resource cleanup, and state
transitions. Record how many production files changed; public API export wiring
does not by itself demonstrate deep implementation scope.

Compare changed original tests with their pinned versions. Explain whether the
edit adds coverage, weakens an assertion, removes a backend/error path, bypasses
HTTP/CLI integration, introduces skip/xfail, or changes collection/configuration.
New regression files are ordinary evidence; additions alone are not weakening.
Original-restoration grading protects the frozen upstream suite even when the
agent's checkout edits its tests, but any coverage weakening remains a separate
qualitative finding. Inspect final testing claims against command evidence,
including claims that partial suites or earlier failures were resolved.

Specific review attention:

- NDJSON: incremental consumption, UTF-8/BOM/delimiter boundaries, physical line
  accounting, malformed-record laziness and exception context, cached reads.
- Response budgets: inherited versus explicit unlimited settings, all transport
  entry points, redirects/hooks/decompression, response-wide cumulative state,
  already-created iterators after rejection, and cleanup error ordering.
- Atomic files: commit versus rollback on all exit paths, ownership and lazy
  state, temporary cleanup and commit-error propagation, nested contexts and
  actual exceptions supplied to registered resources.
- Request limits: streaming/cached budgets, exact observed bytes, disabled and
  non-HTTP bypass, replay/delegation, downstream invocation ordering, and original
  HTTP exception propagation under asyncio and Trio.

## Supplemental probes and reporting

If patch review finds a plausible defect outside the frozen checks, create a
small CPU-only probe after the relevant output. Record why it was selected,
which explicit prompt contract or broader compatibility behavior it examines,
and whether it is implementation-neutral. Run it against pinned upstream where
meaningful, the withheld reference, and every applicable retained candidate.
Keep probe code, invocation, outcomes, and hashes in new artifacts outside frozen
assets. A post-output probe remains supplemental: do not amend formal grades or
quietly select a new performance subset from its outcome.

Publish `patch-review.json` with method/classification, run IDs, summaries,
issues with concrete evidence, test-integrity findings, testing-claim review,
limitations, and patch hashes. Separate confirmed defects from inspection-only
concerns and from unavailable review evidence. Attribute source-excerpt receipt
validation and provider-usage reconciliation to their independent audit artifacts;
this patch review does not estimate paid tokens or infer hidden model reasoning.
Report all sixteen attempts and eight pairs, then describe any supplemental
correctness limitation beside the aggregate performance finding.
