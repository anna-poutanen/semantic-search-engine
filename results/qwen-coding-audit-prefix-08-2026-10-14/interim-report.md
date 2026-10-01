# Three-condition coding comparison — interim snapshot

8/18 frozen attempts recorded. These are two balanced repetitions of three previously observed development tasks; they do not establish general coding accuracy or token savings.

Frozen correctness requires both the strict contract checks and restored upstream compatibility. Completed-correct additionally requires normal, clean CLI completion. Supplemental independent patch findings remain separate from these frozen grades.

This snapshot is incomplete. Missing attempts are pending, and full-workload totals remain unknown.

## Full frozen inventory

| Condition | Recorded/planned | Normal | Correct | Completed-correct | Timeouts |
| --- | --- | --- | --- | --- | --- |
| baseline | 3/6 | 2 | 1 | 1 | 0 |
| mcp_fast | 2/6 | 2 | 0 | 0 | 0 |
| mcp_qwen | 3/6 | 3 | 1 | 1 | 0 |

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

¹ Measured forwards exclude separately recorded model warmup. Startup-failure counts are labelled as retained lower bounds when total counts are unknown. Availability is not evidence of actual tool use.

## Actual usage and time

Gross tokens are reported completed-turn input plus output. Cached input and cache writes are input subsets; reasoning tokens are an output subset. Subsets are never added to the gross total. Uncached input is input minus cached input. Unknown or interrupted-turn usage is not zero. These receipts do not establish per-request billing.

Agent time spans CLI launch through exit, including hosted waits and tool calls. Service setup covers indexing/startup/warmup before that timer; baseline has no measured service setup. Full trial time also includes preparation, cleanup and grading. Timing values require qualified clocks.

| Condition | Metric | Observed/planned | Measured sum | Condition median |
| --- | --- | --- | --- | --- |
| baseline | Gross tokens | 3/6 | 1,705,364 | 537,596 |
| baseline | Uncached input | 3/6 | 179,597 | 61,030 |
| baseline | Cached input | 3/6 | 1,474,560 | 463,360 |
| baseline | Cache-write input subset | 3/6 | 0 | 0 |
| baseline | Output | 3/6 | 51,207 | 19,391 |
| baseline | Reasoning output subset | 3/6 | 21,126 | 7,335 |
| baseline | Agent seconds | 3/6 | 1,638.37 | 608.15 |
| baseline | Service setup seconds | 0/6 | unknown | unknown |
| baseline | Full trial seconds | 3/6 | 1,644.30 | 611.40 |
| mcp_fast | Gross tokens | 2/6 | 1,436,747 | 718,373.50 |
| mcp_fast | Uncached input | 2/6 | 109,636 | 54,818.00 |
| mcp_fast | Cached input | 2/6 | 1,287,040 | 643,520.00 |
| mcp_fast | Cache-write input subset | 2/6 | 0 | 0.00 |
| mcp_fast | Output | 2/6 | 40,071 | 20,035.50 |
| mcp_fast | Reasoning output subset | 2/6 | 17,335 | 8,667.50 |
| mcp_fast | Agent seconds | 2/6 | 1,373.86 | 686.93 |
| mcp_fast | Service setup seconds | 2/6 | 41.56 | 20.78 |
| mcp_fast | Full trial seconds | 2/6 | 1,420.41 | 710.21 |
| mcp_qwen | Gross tokens | 3/6 | 1,767,468 | 571,601 |
| mcp_qwen | Uncached input | 3/6 | 185,191 | 61,613 |
| mcp_qwen | Cached input | 3/6 | 1,533,312 | 477,696 |
| mcp_qwen | Cache-write input subset | 3/6 | 0 | 0 |
| mcp_qwen | Output | 3/6 | 48,965 | 15,965 |
| mcp_qwen | Reasoning output subset | 3/6 | 19,096 | 6,146 |
| mcp_qwen | Agent seconds | 3/6 | 1,573.63 | 540.90 |
| mcp_qwen | Service setup seconds | 3/6 | 65.46 | 22.00 |
| mcp_qwen | Full trial seconds | 3/6 | 1,648.31 | 569.90 |

Measured sums include only known values. They are full-condition totals only when every planned attempt contributes.

## Paired comparisons

Each percentage is the change from the left condition to the right, computed from paired measurement sums. Median pair changes are separate statistics. Every contributing task/repetition is named; different metrics may have different eligible pairs. A pair of incorrect implementations cannot demonstrate better correct-task efficiency.

### baseline → mcp_fast: all_observed_pairs

Eligible paired attempts: 2.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 2 | 1,376,346 | 1,436,747 | +4.39% | +6.17% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Uncached input | 2 | 118,567 | 109,636 | -7.53% | -8.37% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Cached input | 2 | 1,217,792 | 1,287,040 | +5.69% | +8.19% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Cache-write input subset | 2 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; requests_response_limit/r1 |
| Output | 2 | 39,987 | 40,071 | +0.21% | +0.29% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Reasoning output subset | 2 | 17,526 | 17,335 | -1.09% | +0.14% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Agent seconds | 2 | 1,280.95 | 1,373.86 | +7.25% | +7.25% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 2 | 1,285.47 | 1,420.41 | +10.50% | +10.46% | click_atomic_transactions/r1; requests_response_limit/r1 |

### baseline → mcp_fast: normally_completed_pairs

Eligible paired attempts: 1.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 1 | 838,750 | 822,343 | -1.96% | -1.96% | requests_response_limit/r1 |
| Uncached input | 1 | 64,927 | 65,201 | +0.42% | +0.42% | requests_response_limit/r1 |
| Cached input | 1 | 754,432 | 737,152 | -2.29% | -2.29% | requests_response_limit/r1 |
| Cache-write input subset | 1 | 0 | 0 | unknown | unknown | requests_response_limit/r1 |
| Output | 1 | 19,391 | 19,990 | +3.09% | +3.09% | requests_response_limit/r1 |
| Reasoning output subset | 1 | 7,335 | 7,898 | +7.68% | +7.68% | requests_response_limit/r1 |
| Agent seconds | 1 | 608.15 | 651.23 | +7.09% | +7.09% | requests_response_limit/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 1 | 611.40 | 670.60 | +9.68% | +9.68% | requests_response_limit/r1 |

### baseline → mcp_fast: completed_correct_pairs

Eligible paired attempts: 0.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 0 | unknown | unknown | unknown | unknown | none |
| Uncached input | 0 | unknown | unknown | unknown | unknown | none |
| Cached input | 0 | unknown | unknown | unknown | unknown | none |
| Cache-write input subset | 0 | unknown | unknown | unknown | unknown | none |
| Output | 0 | unknown | unknown | unknown | unknown | none |
| Reasoning output subset | 0 | unknown | unknown | unknown | unknown | none |
| Agent seconds | 0 | unknown | unknown | unknown | unknown | none |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 0 | unknown | unknown | unknown | unknown | none |

### baseline → mcp_qwen: all_observed_pairs

Eligible paired attempts: 3.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 3 | 1,705,364 | 1,767,468 | +3.64% | +6.33% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Uncached input | 3 | 179,597 | 185,191 | +3.11% | -5.10% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Cached input | 3 | 1,474,560 | 1,533,312 | +3.98% | +3.09% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Cache-write input subset | 3 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Output | 3 | 51,207 | 48,965 | -4.38% | +1.06% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Reasoning output subset | 3 | 21,126 | 19,096 | -9.61% | -14.28% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Agent seconds | 3 | 1,638.37 | 1,573.63 | -3.95% | -19.61% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 3 | 1,644.30 | 1,648.31 | +0.24% | -15.45% | click_atomic_transactions/r1; requests_response_limit/r1; starlette_body_limits/r1 |

### baseline → mcp_qwen: normally_completed_pairs

Eligible paired attempts: 2.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 2 | 1,167,768 | 1,195,867 | +2.41% | +9.73% | requests_response_limit/r1; starlette_body_limits/r1 |
| Uncached input | 2 | 125,957 | 107,251 | -14.85% | -15.16% | requests_response_limit/r1; starlette_body_limits/r1 |
| Cached input | 2 | 1,011,200 | 1,055,616 | +4.39% | +16.11% | requests_response_limit/r1; starlette_body_limits/r1 |
| Cache-write input subset | 2 | 0 | 0 | unknown | unknown | requests_response_limit/r1; starlette_body_limits/r1 |
| Output | 2 | 30,611 | 33,000 | +7.80% | +6.38% | requests_response_limit/r1; starlette_body_limits/r1 |
| Reasoning output subset | 2 | 10,935 | 12,950 | +18.43% | +10.10% | requests_response_limit/r1; starlette_body_limits/r1 |
| Agent seconds | 2 | 965.57 | 1,032.74 | +6.96% | +0.50% | requests_response_limit/r1; starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 2 | 970.23 | 1,078.42 | +11.15% | +5.17% | requests_response_limit/r1; starlette_body_limits/r1 |

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

Eligible paired attempts: 2.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 2 | 1,436,747 | 1,351,195 | -5.95% | -6.08% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Uncached input | 2 | 109,636 | 139,553 | +27.29% | +34.95% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Cached input | 2 | 1,287,040 | 1,174,016 | -8.78% | -9.33% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Cache-write input subset | 2 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; requests_response_limit/r1 |
| Output | 2 | 40,071 | 37,626 | -6.10% | -6.07% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Reasoning output subset | 2 | 17,335 | 16,010 | -7.64% | -4.99% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Agent seconds | 2 | 1,373.86 | 1,303.31 | -5.13% | -4.04% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Service setup seconds | 2 | 41.56 | 43.46 | +4.58% | +4.43% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Full trial seconds | 2 | 1,420.41 | 1,353.29 | -4.73% | -3.59% | click_atomic_transactions/r1; requests_response_limit/r1 |

### mcp_fast → mcp_qwen: normally_completed_pairs

Eligible paired attempts: 2.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 2 | 1,436,747 | 1,351,195 | -5.95% | -6.08% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Uncached input | 2 | 109,636 | 139,553 | +27.29% | +34.95% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Cached input | 2 | 1,287,040 | 1,174,016 | -8.78% | -9.33% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Cache-write input subset | 2 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; requests_response_limit/r1 |
| Output | 2 | 40,071 | 37,626 | -6.10% | -6.07% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Reasoning output subset | 2 | 17,335 | 16,010 | -7.64% | -4.99% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Agent seconds | 2 | 1,373.86 | 1,303.31 | -5.13% | -4.04% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Service setup seconds | 2 | 41.56 | 43.46 | +4.58% | +4.43% | click_atomic_transactions/r1; requests_response_limit/r1 |
| Full trial seconds | 2 | 1,420.41 | 1,353.29 | -4.73% | -3.59% | click_atomic_transactions/r1; requests_response_limit/r1 |

### mcp_fast → mcp_qwen: completed_correct_pairs

Eligible paired attempts: 0.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 0 | unknown | unknown | unknown | unknown | none |
| Uncached input | 0 | unknown | unknown | unknown | unknown | none |
| Cached input | 0 | unknown | unknown | unknown | unknown | none |
| Cache-write input subset | 0 | unknown | unknown | unknown | unknown | none |
| Output | 0 | unknown | unknown | unknown | unknown | none |
| Reasoning output subset | 0 | unknown | unknown | unknown | unknown | none |
| Agent seconds | 0 | unknown | unknown | unknown | unknown | none |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 0 | unknown | unknown | unknown | unknown | none |

## Evidence and limits

8/8 recorded attempts retain hash-verified published patches. Available artifacts retain original-test compatibility results, compact tool payloads, source snapshots, actual usage and process/clock receipts; startup or interrupted failures may lack some witnesses. Qwen requests retain native singleton forward witnesses. Independent evidence audits verify those bindings; independent patch review assesses defects beyond the frozen gates.

Task reuse, six attempts per condition, hosted variability, cache reuse, and observed tool-use differences limit causal attribution. Retrieval quality improvements do not establish coding correctness or paid-agent savings. The transport probe's usage is outside this eighteen-attempt inventory.
