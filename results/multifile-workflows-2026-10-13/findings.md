# Investigation outcome and next changes

The improved workflow does **not establish token savings**. All sixteen frozen
attempts are retained. Only three pairs have normal completion and pass both
selected and restored-original gates: NDJSON repetitions 1 and 2, and Starlette
body limits repetition 2. Their actual provider receipts total 1,082,077 baseline
tokens versus 1,181,985 MCP tokens (**+9.23%**). Uncached input is 129,703 versus
153,853 (**+18.62%**). Cached input and reasoning output are subsets, never added
again. No dollar-cost estimate is made.

Those same three pairs total 1,359.253 versus 1,329.325 agent seconds (**-2.20%**).
Including fixture, fresh service setup, grading and shutdown gives 1,366.299
versus 1,388.029 seconds (**+1.59%**). Individual agent-time changes range from
-49.4% to +80.0%; aggregate, median pair and condition medians describe different
statistics. This outcome-selected three-pair subset is small and excludes the
harder incomplete pairs. It cannot establish general workflow gains.

## Why shorter excerpts did not establish lower usage

In the three matched completed pairs, the single-copy published output proxy is
69,718 baseline versus 57,499 MCP `cl100k` tokens, about 17.5% lower. Yet MCP has
65 completed shell commands plus ten searches versus 55 baseline shell commands.
The provider reports 1,144,829 versus 1,045,671 cumulative input tokens. Tool
output size alone therefore does not explain provider input: additional agent
steps, history, tool metadata and cache behavior matter. Per-model-response usage
and exact provider/queue/think timing are unavailable, so these records do not
assign an exact token share or causal timing share to those components.

All 28 assigned searches used the intended MCP tool and linked to successful
HTTP receipts with exact saved-source evidence. Seven of eight assigned attempts
used it; attempt 12 failed before a call. H2O is disabled. Independently timed
backend calls total 7.097 seconds, with a 0.264-second median and 0.535-second
maximum. Fresh service setup has an 18.1-second median outside agent time.
These backend timers exclude other tool/provider orchestration and do not
measure complete hosted MCP-call latency.

Across all attempts, 126 excerpts include eleven fully contained source pairs,
nineteen excerpts repeated in later MCP results, and nine exact excerpts seen
again in later shell output. Four queries mention tests or fixtures although
their configured root excludes the top-level test tree. These are descriptive
clues; edits, verification and different questions can justify repeated reads,
and query wording does not prove intended scope or a retrieval error.

## Correctness and missing receipts

Baseline completes and passes the frozen gates in four of eight attempts; MCP
does so in six of eight. Three baseline attempts time out at 900 seconds. Three
attempts fail with explicit provider-capacity errors: baseline 08, MCP 12 and MCP
13. Attempt 13 retains a substantial gate-passing patch; 08 and 12 retain no
implementation. These operational outcomes are distinct from implementation
quality. Six usage receipts are unavailable, including all timeouts; they remain
null. Full-workload token totals and savings are unknown. Failed/censored CLI
durations measure resource time, not successful completion time.

The stronger frozen gates comprise 41 behavioral methods and restored original
integration tests. Nonetheless, separate post-output probes find **six distinct
defects across seven attempts**, including three formally completed-correct
attempts. The references share five gaps. Two authored tests still fail in
attempt 04. No original tests or configuration were weakened. See the
[complete patch review](patch-review.md); these findings leave the primary grades
and qualified denominators unchanged.

The [independent audit](../multifile-audit-2026-10-13/audit.json) verifies all sixteen
fixtures, prompts, runtime/package/native hashes, source receipts and actual
usage records. The [report review](../multifile-audit-2026-10-13/report-review.json)
reconciles all patch reviews and capacity/missing-receipt classifications.

## Product changes and remaining evaluation

The measured package uses MCP-first guidance, a single text-only payload, a
shared history/output cap, resident Qwen and corrected integration dependencies.
The earlier [forensic analysis](../workflow-investigation-2026-10-13/findings.md)
remains a separate study. These changes were evaluated together; individual
component effects are not isolated.

The next adapter exposes concise source-root/membership/exclusion metadata inside
the same token cap, addressing scope visibility. That change follows this study
and has **no measured coding-token benefit yet**. Language and editor integration
can proceed for product usability while the token-saving target remains unmet.
Future hosted comparisons should obtain actual intermediate usage receipts,
pre-freeze the demonstrated cleanup/iterator/resource/hook edge cases, retain
operational failures, and use a larger independent task sample. This study has
only four authored features in development repositories and two repetitions;
provider variation, caches, observation overhead and parallel CPU draft work
limit timing attribution. GPU inference was serialized throughout.
