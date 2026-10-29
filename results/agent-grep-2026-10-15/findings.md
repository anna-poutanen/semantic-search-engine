# Fast semantic search versus ordinary repository tools

16/16 attempts retained: four new maintenance tasks, two repetitions per condition. Both conditions use the same coding model, prompts, ordinary tools, time limit and grader. The search-assisted condition additionally receives the current local dense Qwen MCP tool.

This run does not establish a coding-quality advantage for semantic search. Ordinary tools completed 7/8 attempts normally; search-assisted tools completed 7/8. Sleep/connection interruptions are infrastructure outcomes, not method-quality wins.

Supplemental review confirmed 1 scoped compatibility regression in a search-assisted Click patch beyond the frozen grader. The original automated grades and the separately labelled review-adjusted measurements both remain visible.

Across 5 matched pairs with no detected contract defect, the changes below are descriptive; negative values mean search used less:

| Metric | Search versus ordinary tools |
|---|---:|
| agent_elapsed_s | +0.18% |
| full_trial_elapsed_s | +10.15% |
| total_tokens | -4.97% |
| uncached_input_tokens | -5.77% |

This is a small post-output subset of authored maintenance tasks, not a representative coding benchmark or a billing-savings estimate.

## Complete attempt inventory

| Condition | Recorded / planned | Normally completed | Frozen completed-correct | Timeouts | Verified dense searches |
|---|---:|---:|---:|---:|---:|
| baseline | 8/8 | 7 | 7 | 0 | 0 |
| mcp | 8/8 | 7 | 7 | 0 | 14 |

Frozen success requires passing the withheld behavior checks and restored upstream tests, plus normal CLI completion. Agent test-pass claims do not determine grades. Supplemental review can still find contract defects after these gates pass.

| Attempt | Frozen success | Agent seconds | Setup seconds | Full trial seconds | Gross tokens | Uncached input |
|---|---:|---:|---:|---:|---:|---:|
| 01-r1-requests_default_timeout-baseline | True | 278.54 | 0.00 | 280.22 | 507,585 | 59,237 |
| 02-r1-requests_default_timeout-mcp | True | 268.03 | 16.35 | 286.51 | 570,754 | 43,936 |
| 03-r1-click_choice_aliases-mcp | True | 272.23 | 26.28 | 299.88 | 311,405 | 39,503 |
| 04-r1-click_choice_aliases-baseline | True | 340.00 | 0.00 | 340.89 | 426,982 | 66,143 |
| 05-r1-starlette_background_continuation-baseline | False | unknown | unknown | unknown | unknown | unknown |
| 06-r1-starlette_background_continuation-mcp | True | 222.28 | 21.84 | 245.86 | 272,257 | 36,657 |
| 07-r1-httpx_quoted_headers-mcp | True | 112.52 | 20.42 | 134.19 | 167,436 | 30,138 |
| 08-r1-httpx_quoted_headers-baseline | True | 108.40 | 0.00 | 109.55 | 229,180 | 43,119 |
| 09-r2-requests_default_timeout-mcp | True | 255.25 | 14.89 | 272.38 | 511,174 | 54,731 |
| 10-r2-requests_default_timeout-baseline | True | 220.24 | 0.00 | 222.02 | 471,829 | 46,834 |
| 11-r2-click_choice_aliases-baseline | True | 281.18 | 0.00 | 282.02 | 413,866 | 34,632 |
| 12-r2-click_choice_aliases-mcp | True | 280.83 | 25.73 | 307.88 | 298,806 | 40,563 |
| 13-r2-starlette_background_continuation-mcp | False | unknown | unknown | unknown | unknown | unknown |
| 14-r2-starlette_background_continuation-baseline | True | 205.02 | 0.00 | 206.47 | 235,027 | 37,465 |
| 15-r2-httpx_quoted_headers-baseline | True | 118.27 | 0.00 | 119.63 | 167,755 | 41,988 |
| 16-r2-httpx_quoted_headers-mcp | True | 91.79 | 22.10 | 115.38 | 153,113 | 43,417 |

**Infrastructure interruption:** 05-r1-starlette_background_continuation-baseline, 13-r2-starlette_background_continuation-mcp stopped after sleep/connection gaps. Interrupted attempts were not retried. Their timings are unqualified and incomplete usage is unknown. Remaining, unattempted identities ran in separately frozen segments after successful transport probes. These failures must not be attributed to the search method; cross-segment provider variability is an additional limitation.


## Task-level outcomes

Each condition has two scheduled attempts per task. Mean times below use only matched, normally completed repetitions with qualified timings in both conditions. Unpaired observations and failures remain in the attempt table above.

| Task | Baseline frozen pass / observed | Search frozen pass / observed | Baseline mean agent s | Search mean agent s |
|---|---:|---:|---:|---:|
| requests_default_timeout | 2/2 | 2/2 | 249.39 | 261.64 |
| click_choice_aliases | 2/2 | 2/2 | 310.59 | 276.53 |
| starlette_background_continuation | 1/2 | 1/2 | unknown | unknown |
| httpx_quoted_headers | 2/2 | 2/2 | 113.34 | 102.15 |

## Paired correctness

Each pair has identical starting source and task prompt. Balanced order reverses between repetitions.

- both_correct: 6
- baseline_only: 1
- mcp_only: 1
- neither_correct: 0

## Supplemental contract regression

Manual patch review found an interaction missing from the frozen checker: Click normalizes canonical names and then casefolds, and the two-stage collision ordering is existing behavior even without aliases. A separate post-output probe compares each completed Click candidate to both pinned upstream and the reference.

Known regressions: 03-r1-click_choice_aliases-mcp.

For choices `A_B`, `a_b`, `A-B`, underscore-to-hyphen normalization and case-insensitive conversion of `a_b`, upstream and reference return `a_b`. A fused per-choice normalization/casefold loop can return `A-B` instead, violating the stated requirement to preserve behavior without aliases. This does not rewrite frozen grades.

The additional review-adjusted comparison excludes pairs containing that known contract regression. It is a post-output diagnostic, not a replacement primary grade. Its contributing members and measurements are retained in evidence-audit.json.

| Review-adjusted metric | Pairs | Baseline sum | Search-assisted sum | Change |
|---|---:|---:|---:|---:|
| agent_elapsed_s | 5 | 1,006.64 | 1,008.42 | +0.18% |
| full_trial_elapsed_s | 5 | 1,013.45 | 1,116.34 | +10.15% |
| total_tokens | 5 | 1,790,215 | 1,701,283 | -4.97% |
| uncached_input_tokens | 5 | 225,810 | 212,785 | -5.77% |

## Paired usage and time

Changes are (search-assisted / baseline − 1), calculated from matched sums. Negative means less time or fewer tokens. Cached input is a subset of input; it is not added to gross tokens. Missing measurements remain unknown.

### all_pairs

Eligible pairs: 8.

| Metric | Measured pairs | Baseline sum | Search-assisted sum | Change |
|---|---:|---:|---:|---:|
| total_tokens | 6 | 2,217,197 | 2,012,688 | -9.22% |
| uncached_input_tokens | 6 | 291,953 | 252,288 | -13.59% |
| cached_input_tokens | 6 | 1,861,760 | 1,699,584 | -8.71% |
| output_tokens | 6 | 63,484 | 60,816 | -4.20% |
| agent_elapsed_s | 6 | 1,346.64 | 1,280.66 | -4.90% |
| service_setup_s | 6 | 0.00 | 125.77 | unknown |
| full_trial_elapsed_s | 6 | 1,354.34 | 1,416.22 | +4.57% |

### normally_completed_pairs

Eligible pairs: 6.

| Metric | Measured pairs | Baseline sum | Search-assisted sum | Change |
|---|---:|---:|---:|---:|
| total_tokens | 6 | 2,217,197 | 2,012,688 | -9.22% |
| uncached_input_tokens | 6 | 291,953 | 252,288 | -13.59% |
| cached_input_tokens | 6 | 1,861,760 | 1,699,584 | -8.71% |
| output_tokens | 6 | 63,484 | 60,816 | -4.20% |
| agent_elapsed_s | 6 | 1,346.64 | 1,280.66 | -4.90% |
| service_setup_s | 6 | 0.00 | 125.77 | unknown |
| full_trial_elapsed_s | 6 | 1,354.34 | 1,416.22 | +4.57% |

### both_correct_pairs

Eligible pairs: 6.

| Metric | Measured pairs | Baseline sum | Search-assisted sum | Change |
|---|---:|---:|---:|---:|
| total_tokens | 6 | 2,217,197 | 2,012,688 | -9.22% |
| uncached_input_tokens | 6 | 291,953 | 252,288 | -13.59% |
| cached_input_tokens | 6 | 1,861,760 | 1,699,584 | -8.71% |
| output_tokens | 6 | 63,484 | 60,816 | -4.20% |
| agent_elapsed_s | 6 | 1,346.64 | 1,280.66 | -4.90% |
| service_setup_s | 6 | 0.00 | 125.77 | unknown |
| full_trial_elapsed_s | 6 | 1,354.34 | 1,416.22 | +4.57% |

## Evidence and interpretation limits

The evidence audit checked 61 returned source excerpts against retained saved-source snapshots and reconstructed provider usage and aggregate grades. Per-run patches, commands, events, source receipts, grading logs and exact upstream test counts are retained. See `evidence-audit.json`, `qualified-summary.json` and `runs/`.

The capture helper omitted a positive timing-qualification marker on normal runs. Raw producer records and summary.json remain unchanged. This report derives qualification uniformly from the already captured wall/performance clocks and clean process shutdown; qualified-measurements.json and qualified-summary.json retain that separate calculation.

Model: gpt-6.1-sol, ultra effort, 900 seconds per attempt. Search setup precedes agent timing; full trial timing includes setup and grading. Comparisons retain failures and explicitly name measured pair members in qualified-summary.json.

Transport diagnostics are outside the 16 coding attempts and all paired totals:

| Diagnostic | Passed | Reported gross tokens | Agent seconds |
|---|---:|---:|---:|
| agent-grep-transport-2026-10-15 | True | 39,910 | 16.19 |
| agent-grep-transport-v2-2026-10-15 | True | 39,928 | 13.11 |
| agent-grep-transport-v3-2026-10-16 | True | 40,077 | 15.97 |
| agent-grep-transport-v4-2026-10-16 | True | 40,079 | 17.24 |

- Four authored tasks in observed repositories; eight paired attempts are descriptive.
- Conditional search-first instructions measure prompted use, not spontaneous adoption.
- Recorded usage is completed-turn provider usage, not per-request billing.
- Frozen automated grades do not establish that all possible bugs are absent.
- Prompt and filesystem/provider cache effects, task familiarity, and hosted variability limit causal attribution.
- This measures realistic controlled maintenance scenarios, not a representative sample of production issues.
- Supplemental patch review is recorded separately from frozen automated grades.
