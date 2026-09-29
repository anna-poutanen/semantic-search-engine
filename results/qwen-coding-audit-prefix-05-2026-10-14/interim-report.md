# Three-condition coding comparison — interim snapshot

5/18 frozen attempts recorded. These are two balanced repetitions of three previously observed development tasks; they do not establish general coding accuracy or token savings.

Frozen correctness requires both the strict contract checks and restored upstream compatibility. Completed-correct additionally requires normal, clean CLI completion. Supplemental independent patch findings remain separate from these frozen grades.

This snapshot is incomplete. Missing attempts are pending, and full-workload totals remain unknown.

## Full frozen inventory

| Condition | Recorded/planned | Normal | Correct | Completed-correct | Timeouts |
| --- | --- | --- | --- | --- | --- |
| baseline | 1/6 | 1 | 0 | 0 | 0 |
| mcp_fast | 2/6 | 2 | 0 | 0 | 0 |
| mcp_qwen | 2/6 | 2 | 0 | 0 | 0 |

## Every recorded attempt

| Run | Normal | Correct | Completed-correct | Gross tokens | Uncached input | Agent s | MCP successes | Qwen forwards¹ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 01-r1-requests_response_limit-baseline | True | False | False | 838,750 | 64,927 | 608.15 | 0 | 0 |
| 02-r1-requests_response_limit-mcp_fast | True | False | False | 822,343 | 65,201 | 651.23 | 3 | 0 |
| 03-r1-requests_response_limit-mcp_qwen | True | False | False | 779,594 | 61,613 | 762.41 | 3 | 72 |
| 04-r1-click_atomic_transactions-mcp_fast | True | False | False | 614,404 | 44,435 | 722.62 | 3 | 0 |
| 05-r1-click_atomic_transactions-mcp_qwen | True | False | False | 571,601 | 77,940 | 540.90 | 4 | 96 |

¹ Measured forwards exclude separately recorded model warmup. Availability is not evidence of actual tool use.

## Actual usage and time

Gross tokens are reported completed-turn input plus output. Cached input and cache writes are input subsets; reasoning tokens are an output subset. Subsets are never added to the gross total. Uncached input is input minus cached input. Unknown or interrupted-turn usage is not zero. These receipts do not establish per-request billing.

Agent time spans CLI launch through exit, including hosted waits and tool calls. Service setup covers indexing/startup/warmup before that timer; baseline has no measured service setup. Full trial time also includes preparation, cleanup and grading. Timing values require qualified clocks.

| Condition | Metric | Observed/planned | Measured sum | Condition median |
| --- | --- | --- | --- | --- |
| baseline | Gross tokens | 1/6 | 838,750 | 838,750 |
| baseline | Uncached input | 1/6 | 64,927 | 64,927 |
| baseline | Cached input | 1/6 | 754,432 | 754,432 |
| baseline | Cache-write input subset | 1/6 | 0 | 0 |
| baseline | Output | 1/6 | 19,391 | 19,391 |
| baseline | Reasoning output subset | 1/6 | 7,335 | 7,335 |
| baseline | Agent seconds | 1/6 | 608.15 | 608.15 |
| baseline | Service setup seconds | 0/6 | unknown | unknown |
| baseline | Full trial seconds | 1/6 | 611.40 | 611.40 |
| mcp_fast | Gross tokens | 2/6 | 1,436,747 | 718,373.50 |
| mcp_fast | Uncached input | 2/6 | 109,636 | 54,818.00 |
| mcp_fast | Cached input | 2/6 | 1,287,040 | 643,520.00 |
| mcp_fast | Cache-write input subset | 2/6 | 0 | 0.00 |
| mcp_fast | Output | 2/6 | 40,071 | 20,035.50 |
| mcp_fast | Reasoning output subset | 2/6 | 17,335 | 8,667.50 |
| mcp_fast | Agent seconds | 2/6 | 1,373.86 | 686.93 |
| mcp_fast | Service setup seconds | 2/6 | 41.56 | 20.78 |
| mcp_fast | Full trial seconds | 2/6 | 1,420.41 | 710.21 |
| mcp_qwen | Gross tokens | 2/6 | 1,351,195 | 675,597.50 |
| mcp_qwen | Uncached input | 2/6 | 139,553 | 69,776.50 |
| mcp_qwen | Cached input | 2/6 | 1,174,016 | 587,008.00 |
| mcp_qwen | Cache-write input subset | 2/6 | 0 | 0.00 |
| mcp_qwen | Output | 2/6 | 37,626 | 18,813.00 |
| mcp_qwen | Reasoning output subset | 2/6 | 16,010 | 8,005.00 |
| mcp_qwen | Agent seconds | 2/6 | 1,303.31 | 651.66 |
| mcp_qwen | Service setup seconds | 2/6 | 43.46 | 21.73 |
| mcp_qwen | Full trial seconds | 2/6 | 1,353.29 | 676.65 |

Measured sums include only known values. They are full-condition totals only when every planned attempt contributes.

## Paired comparisons

Each percentage is the change from the left condition to the right, computed from paired measurement sums. Median pair changes are separate statistics. Every contributing task/repetition is named; different metrics may have different eligible pairs. A pair of incorrect implementations cannot demonstrate better correct-task efficiency.

### baseline → mcp_fast: all_observed_pairs

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

Eligible paired attempts: 1.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 1 | 838,750 | 779,594 | -7.05% | -7.05% | requests_response_limit/r1 |
| Uncached input | 1 | 64,927 | 61,613 | -5.10% | -5.10% | requests_response_limit/r1 |
| Cached input | 1 | 754,432 | 696,320 | -7.70% | -7.70% | requests_response_limit/r1 |
| Cache-write input subset | 1 | 0 | 0 | unknown | unknown | requests_response_limit/r1 |
| Output | 1 | 19,391 | 21,661 | +11.71% | +11.71% | requests_response_limit/r1 |
| Reasoning output subset | 1 | 7,335 | 9,864 | +34.48% | +34.48% | requests_response_limit/r1 |
| Agent seconds | 1 | 608.15 | 762.41 | +25.37% | +25.37% | requests_response_limit/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 1 | 611.40 | 783.39 | +28.13% | +28.13% | requests_response_limit/r1 |

### baseline → mcp_qwen: normally_completed_pairs

Eligible paired attempts: 1.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 1 | 838,750 | 779,594 | -7.05% | -7.05% | requests_response_limit/r1 |
| Uncached input | 1 | 64,927 | 61,613 | -5.10% | -5.10% | requests_response_limit/r1 |
| Cached input | 1 | 754,432 | 696,320 | -7.70% | -7.70% | requests_response_limit/r1 |
| Cache-write input subset | 1 | 0 | 0 | unknown | unknown | requests_response_limit/r1 |
| Output | 1 | 19,391 | 21,661 | +11.71% | +11.71% | requests_response_limit/r1 |
| Reasoning output subset | 1 | 7,335 | 9,864 | +34.48% | +34.48% | requests_response_limit/r1 |
| Agent seconds | 1 | 608.15 | 762.41 | +25.37% | +25.37% | requests_response_limit/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 1 | 611.40 | 783.39 | +28.13% | +28.13% | requests_response_limit/r1 |

### baseline → mcp_qwen: completed_correct_pairs

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

All attempts retain exact patches, original-test compatibility results, compact tool payloads, source snapshots, actual usage and process/clock receipts. Qwen requests retain native singleton forward witnesses. Independent evidence audits verify those bindings; independent patch review assesses defects beyond the frozen gates.

Task reuse, six attempts per condition, hosted variability, cache reuse, and observed tool-use differences limit causal attribution. Retrieval quality improvements do not establish coding correctness or paid-agent savings. The transport probe's usage is outside this eighteen-attempt inventory.
