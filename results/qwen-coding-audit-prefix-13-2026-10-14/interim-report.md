# Three-condition coding comparison — interim snapshot

13/18 frozen attempts recorded. These are two balanced repetitions of three previously observed development tasks; they do not establish general coding accuracy or token savings.

Frozen correctness requires both the strict contract checks and restored upstream compatibility. Completed-correct additionally requires normal, clean CLI completion. Supplemental independent patch findings remain separate from these frozen grades.

This snapshot is incomplete. Missing attempts are pending, and full-workload totals remain unknown.

## Full frozen inventory

| Condition | Recorded/planned | Normal | Correct | Completed-correct | Timeouts |
| --- | --- | --- | --- | --- | --- |
| baseline | 5/6 | 4 | 1 | 1 | 0 |
| mcp_fast | 4/6 | 3 | 2 | 1 | 1 |
| mcp_qwen | 4/6 | 4 | 1 | 1 | 0 |

## Every recorded attempt

| Run | Normal | Correct | Completed-correct | Gross tokens | Uncached input | Agent s | MCP successes | Qwen forwards¹ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 01-r1-requests_response_limit-baseline | True | False | False | 838,750 | 64,927 | 608.15 | 0 | 0 |
| 02-r1-requests_response_limit-mcp_fast | True | False | False | 822,343 | 65,201 | 651.23 | 3 | 0 |
| 03-r1-requests_response_limit-mcp_qwen | True | False | False | 779,594 | 61,613 | 762.41 | 3 | 72 |
| 04-r1-click_atomic_transactions-mcp_fast | True | False | False | 614,404 | 44,435 | 722.62 | 3 | 0 |
| 05-r1-click_atomic_transactions-mcp_qwen | True | False | False | 571,601 | 77,940 | 540.90 | 4 | 96 |
| 06-r1-click_atomic_transactions-baseline | False | False | False | 537,596 | 53,640 | 672.80 | 0 | 0 |
| 07-r1-starlette_body_limits-mcp_qwen | True | True | True | 416,273 | 45,638 | 270.32 | 2 | 48 |
| 08-r1-starlette_body_limits-baseline | True | True | True | 329,018 | 61,030 | 357.42 | 0 | 0 |
| 09-r1-starlette_body_limits-mcp_fast | True | True | True | 409,795 | 63,570 | 276.53 | 4 | 0 |
| 10-r2-requests_response_limit-mcp_qwen | True | False | False | 853,850 | 87,981 | 637.45 | 3 | 72 |
| 11-r2-requests_response_limit-mcp_fast | False | True | False | unknown | unknown | 900.06 | 5 | 0 |
| 12-r2-requests_response_limit-baseline | True | False | False | 838,211 | 68,722 | 775.19 | 0 | 0 |
| 13-r2-click_atomic_transactions-baseline | True | False | False | 349,986 | 60,850 | 532.56 | 0 | 0 |

¹ Measured forwards exclude separately recorded model warmup. Startup-failure counts are labelled as retained lower bounds when total counts are unknown. Availability is not evidence of actual tool use.

## Actual usage and time

Gross tokens are reported completed-turn input plus output. Cached input and cache writes are input subsets; reasoning tokens are an output subset. Subsets are never added to the gross total. Uncached input is input minus cached input. Unknown or interrupted-turn usage is not zero. These receipts do not establish per-request billing.

Agent time spans CLI launch through exit, including hosted waits and tool calls. Service setup covers indexing/startup/warmup before that timer; baseline has no measured service setup. Full trial time also includes preparation, cleanup and grading. Timing values require qualified clocks.

| Condition | Metric | Observed/planned | Measured sum | Condition median |
| --- | --- | --- | --- | --- |
| baseline | Gross tokens | 5/6 | 2,893,561 | 537,596 |
| baseline | Uncached input | 5/6 | 309,169 | 61,030 |
| baseline | Cached input | 5/6 | 2,493,952 | 463,360 |
| baseline | Cache-write input subset | 5/6 | 0 | 0 |
| baseline | Output | 5/6 | 90,440 | 19,391 |
| baseline | Reasoning output subset | 5/6 | 38,897 | 8,154 |
| baseline | Agent seconds | 5/6 | 2,946.12 | 608.15 |
| baseline | Service setup seconds | 0/6 | unknown | unknown |
| baseline | Full trial seconds | 5/6 | 2,956.38 | 611.40 |
| mcp_fast | Gross tokens | 3/6 | 1,846,542 | 614,404 |
| mcp_fast | Uncached input | 3/6 | 173,206 | 63,570 |
| mcp_fast | Cached input | 3/6 | 1,622,400 | 549,888 |
| mcp_fast | Cache-write input subset | 3/6 | 0 | 0 |
| mcp_fast | Output | 3/6 | 50,936 | 19,990 |
| mcp_fast | Reasoning output subset | 3/6 | 20,347 | 7,898 |
| mcp_fast | Agent seconds | 4/6 | 2,550.45 | 686.93 |
| mcp_fast | Service setup seconds | 4/6 | 76.72 | 18.18 |
| mcp_fast | Full trial seconds | 4/6 | 2,637.38 | 710.21 |
| mcp_qwen | Gross tokens | 4/6 | 2,621,318 | 675,597.50 |
| mcp_qwen | Uncached input | 4/6 | 273,172 | 69,776.50 |
| mcp_qwen | Cached input | 4/6 | 2,280,960 | 587,008.00 |
| mcp_qwen | Cache-write input subset | 4/6 | 0 | 0.00 |
| mcp_qwen | Output | 4/6 | 67,186 | 17,093.00 |
| mcp_qwen | Reasoning output subset | 4/6 | 25,936 | 6,493.00 |
| mcp_qwen | Agent seconds | 4/6 | 2,211.08 | 589.17 |
| mcp_qwen | Service setup seconds | 4/6 | 81.76 | 19.33 |
| mcp_qwen | Full trial seconds | 4/6 | 2,306.38 | 613.98 |

Measured sums include only known values. They are full-condition totals only when every planned attempt contributes.

## Paired comparisons

Each percentage is the change from the left condition to the right, computed from paired measurement sums. Median pair changes are separate statistics. Every contributing task/repetition is named; different metrics may have different eligible pairs. A pair of incorrect implementations cannot demonstrate better correct-task efficiency.

### baseline → mcp_fast: all_observed_pairs

Eligible paired attempts: 4.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 3 | 1,705,364 | 1,846,542 | +8.28% | +14.29% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Uncached input | 3 | 179,597 | 173,206 | -3.56% | +0.42% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Cached input | 3 | 1,474,560 | 1,622,400 | +10.03% | +18.67% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Cache-write input subset | 3 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Output | 3 | 51,207 | 50,936 | -0.53% | -2.50% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Reasoning output subset | 3 | 21,126 | 20,347 | -3.69% | -7.40% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Agent seconds | 4 | 2,413.55 | 2,550.45 | +5.67% | +7.25% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 4 | 2,422.51 | 2,637.38 | +8.87% | +10.46% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |

### baseline → mcp_fast: normally_completed_pairs

Eligible paired attempts: 2.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 2 | 1,167,768 | 1,232,138 | +5.51% | +11.30% | requests_response_limit/r1; starlette_body_limits/r1 |
| Uncached input | 2 | 125,957 | 128,771 | +2.23% | +2.29% | requests_response_limit/r1; starlette_body_limits/r1 |
| Cached input | 2 | 1,011,200 | 1,072,512 | +6.06% | +14.16% | requests_response_limit/r1; starlette_body_limits/r1 |
| Cache-write input subset | 2 | 0 | 0 | unknown | unknown | requests_response_limit/r1; starlette_body_limits/r1 |
| Output | 2 | 30,611 | 30,855 | +0.80% | -0.04% | requests_response_limit/r1; starlette_body_limits/r1 |
| Reasoning output subset | 2 | 10,935 | 10,910 | -0.23% | -4.33% | requests_response_limit/r1; starlette_body_limits/r1 |
| Agent seconds | 2 | 965.57 | 927.76 | -3.92% | -7.77% | requests_response_limit/r1; starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 2 | 970.23 | 969.49 | -0.08% | -3.51% | requests_response_limit/r1; starlette_body_limits/r1 |

### baseline → mcp_fast: completed_correct_pairs

Eligible paired attempts: 1.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 1 | 329,018 | 409,795 | +24.55% | +24.55% | starlette_body_limits/r1 |
| Uncached input | 1 | 61,030 | 63,570 | +4.16% | +4.16% | starlette_body_limits/r1 |
| Cached input | 1 | 256,768 | 335,360 | +30.61% | +30.61% | starlette_body_limits/r1 |
| Cache-write input subset | 1 | 0 | 0 | unknown | unknown | starlette_body_limits/r1 |
| Output | 1 | 11,220 | 10,865 | -3.16% | -3.16% | starlette_body_limits/r1 |
| Reasoning output subset | 1 | 3,600 | 3,012 | -16.33% | -16.33% | starlette_body_limits/r1 |
| Agent seconds | 1 | 357.42 | 276.53 | -22.63% | -22.63% | starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 1 | 358.83 | 298.89 | -16.70% | -16.70% | starlette_body_limits/r1 |

### baseline → mcp_qwen: all_observed_pairs

Eligible paired attempts: 4.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 4 | 2,543,575 | 2,621,318 | +3.06% | +4.10% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Uncached input | 4 | 248,319 | 273,172 | +10.01% | +11.46% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Cached input | 4 | 2,221,696 | 2,280,960 | +2.67% | +1.58% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Cache-write input subset | 4 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Output | 4 | 73,560 | 67,186 | -8.67% | -8.71% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Reasoning output subset | 4 | 30,743 | 25,936 | -15.64% | -21.58% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Agent seconds | 4 | 2,413.55 | 2,211.08 | -8.39% | -18.69% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 4 | 2,422.51 | 2,306.38 | -4.79% | -15.45% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |

### baseline → mcp_qwen: normally_completed_pairs

Eligible paired attempts: 3.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 3 | 2,005,979 | 2,049,717 | +2.18% | +1.87% | requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Uncached input | 3 | 194,679 | 195,232 | +0.28% | -5.10% | requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Cached input | 3 | 1,758,336 | 1,803,264 | +2.56% | +0.07% | requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Cache-write input subset | 3 | 0 | 0 | unknown | unknown | requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Output | 3 | 52,964 | 51,221 | -3.29% | +1.06% | requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Reasoning output subset | 3 | 20,552 | 19,790 | -3.71% | -14.28% | requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Agent seconds | 3 | 1,740.75 | 1,670.19 | -4.05% | -17.77% | requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 3 | 1,748.45 | 1,736.48 | -0.68% | -15.44% | requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |

### baseline → mcp_qwen: completed_correct_pairs

Eligible paired attempts: 1.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 1 | 329,018 | 416,273 | +26.52% | +26.52% | starlette_body_limits/r1 |
| Uncached input | 1 | 61,030 | 45,638 | -25.22% | -25.22% | starlette_body_limits/r1 |
| Cached input | 1 | 256,768 | 359,296 | +39.93% | +39.93% | starlette_body_limits/r1 |
| Cache-write input subset | 1 | 0 | 0 | unknown | unknown | starlette_body_limits/r1 |
| Output | 1 | 11,220 | 11,339 | +1.06% | +1.06% | starlette_body_limits/r1 |
| Reasoning output subset | 1 | 3,600 | 3,086 | -14.28% | -14.28% | starlette_body_limits/r1 |
| Agent seconds | 1 | 357.42 | 270.32 | -24.37% | -24.37% | starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 1 | 358.83 | 295.02 | -17.78% | -17.78% | starlette_body_limits/r1 |

### mcp_fast → mcp_qwen: all_observed_pairs

Eligible paired attempts: 4.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 3 | 1,846,542 | 1,767,468 | -4.28% | -5.20% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Uncached input | 3 | 173,206 | 185,191 | +6.92% | -5.50% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Cached input | 3 | 1,622,400 | 1,533,312 | -5.49% | -5.54% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Cache-write input subset | 3 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Output | 3 | 50,936 | 48,965 | -3.87% | +4.36% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Reasoning output subset | 3 | 20,347 | 19,096 | -6.15% | +2.46% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Agent seconds | 4 | 2,550.45 | 2,211.08 | -13.31% | -13.70% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Service setup seconds | 4 | 76.72 | 81.76 | +6.57% | +6.67% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Full trial seconds | 4 | 2,637.38 | 2,306.38 | -12.55% | -12.64% | click_atomic_transactions/r1; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |

### mcp_fast → mcp_qwen: normally_completed_pairs

Eligible paired attempts: 3.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 3 | 1,846,542 | 1,767,468 | -4.28% | -5.20% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Uncached input | 3 | 173,206 | 185,191 | +6.92% | -5.50% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Cached input | 3 | 1,622,400 | 1,533,312 | -5.49% | -5.54% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Cache-write input subset | 3 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Output | 3 | 50,936 | 48,965 | -3.87% | +4.36% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Reasoning output subset | 3 | 20,347 | 19,096 | -6.15% | +2.46% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Agent seconds | 3 | 1,650.38 | 1,573.63 | -4.65% | -2.24% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Service setup seconds | 3 | 61.88 | 65.46 | +5.79% | +5.08% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Full trial seconds | 3 | 1,719.31 | 1,648.31 | -4.13% | -1.29% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |

### mcp_fast → mcp_qwen: completed_correct_pairs

Eligible paired attempts: 1.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 1 | 409,795 | 416,273 | +1.58% | +1.58% | starlette_body_limits/r1 |
| Uncached input | 1 | 63,570 | 45,638 | -28.21% | -28.21% | starlette_body_limits/r1 |
| Cached input | 1 | 335,360 | 359,296 | +7.14% | +7.14% | starlette_body_limits/r1 |
| Cache-write input subset | 1 | 0 | 0 | unknown | unknown | starlette_body_limits/r1 |
| Output | 1 | 10,865 | 11,339 | +4.36% | +4.36% | starlette_body_limits/r1 |
| Reasoning output subset | 1 | 3,012 | 3,086 | +2.46% | +2.46% | starlette_body_limits/r1 |
| Agent seconds | 1 | 276.53 | 270.32 | -2.24% | -2.24% | starlette_body_limits/r1 |
| Service setup seconds | 1 | 20.32 | 22.00 | +8.27% | +8.27% | starlette_body_limits/r1 |
| Full trial seconds | 1 | 298.89 | 295.02 | -1.29% | -1.29% | starlette_body_limits/r1 |

## Evidence and limits

13/13 recorded attempts retain hash-verified published patches. Available artifacts retain original-test compatibility results, compact tool payloads, source snapshots, actual usage and process/clock receipts; startup or interrupted failures may lack some witnesses. Qwen requests retain native singleton forward witnesses. Independent evidence audits verify those bindings; independent patch review assesses defects beyond the frozen gates.

Task reuse, six attempts per condition, hosted variability, cache reuse, and observed tool-use differences limit causal attribution. Retrieval quality improvements do not establish coding correctness or paid-agent savings. The transport probe's usage is outside this eighteen-attempt inventory.
