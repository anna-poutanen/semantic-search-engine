# Three-condition coding comparison — interim snapshot

16/18 frozen attempts recorded. These are two balanced repetitions of three previously observed development tasks; they do not establish general coding accuracy or token savings.

Frozen correctness requires both the strict contract checks and restored upstream compatibility. Completed-correct additionally requires normal, clean CLI completion. Supplemental independent patch findings remain separate from these frozen grades.

This snapshot is incomplete. Missing attempts are pending, and full-workload totals remain unknown.

## Full frozen inventory

| Condition | Recorded/planned | Normal | Correct | Completed-correct | Timeouts |
| --- | --- | --- | --- | --- | --- |
| baseline | 5/6 | 4 | 1 | 1 | 0 |
| mcp_fast | 6/6 | 5 | 3 | 2 | 1 |
| mcp_qwen | 5/6 | 5 | 1 | 1 | 0 |

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
| 14-r2-click_atomic_transactions-mcp_qwen | True | False | False | 452,944 | 59,517 | 507.31 | 3 | 72 |
| 15-r2-click_atomic_transactions-mcp_fast | True | False | False | 455,543 | 47,350 | 431.71 | 3 | 0 |
| 16-r2-starlette_body_limits-mcp_fast | True | True | True | 301,635 | 69,096 | 385.73 | 5 | 0 |

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
| mcp_fast | Gross tokens | 5/6 | 2,603,720 | 455,543 |
| mcp_fast | Uncached input | 5/6 | 289,652 | 63,570 |
| mcp_fast | Cached input | 5/6 | 2,236,288 | 394,112 |
| mcp_fast | Cache-write input subset | 5/6 | 0 | 0 |
| mcp_fast | Output | 5/6 | 77,780 | 14,081 |
| mcp_fast | Reasoning output subset | 5/6 | 30,050 | 5,407 |
| mcp_fast | Agent seconds | 6/6 | 3,367.88 | 541.47 |
| mcp_fast | Service setup seconds | 6/6 | 122.29 | 20.18 |
| mcp_fast | Full trial seconds | 6/6 | 3,504.13 | 564.87 |
| mcp_qwen | Gross tokens | 5/6 | 3,074,262 | 571,601 |
| mcp_qwen | Uncached input | 5/6 | 332,689 | 61,613 |
| mcp_qwen | Cached input | 5/6 | 2,657,920 | 477,696 |
| mcp_qwen | Cache-write input subset | 5/6 | 0 | 0 |
| mcp_qwen | Output | 5/6 | 83,653 | 16,467 |
| mcp_qwen | Reasoning output subset | 5/6 | 32,266 | 6,330 |
| mcp_qwen | Agent seconds | 5/6 | 2,718.40 | 540.90 |
| mcp_qwen | Service setup seconds | 5/6 | 108.79 | 22.00 |
| mcp_qwen | Full trial seconds | 5/6 | 2,842.94 | 569.90 |

Measured sums include only known values. They are full-condition totals only when every planned attempt contributes.

## Paired comparisons

Each percentage is the change from the left condition to the right, computed from paired measurement sums. Median pair changes are separate statistics. Every contributing task/repetition is named; different metrics may have different eligible pairs. A pair of incorrect implementations cannot demonstrate better correct-task efficiency.

### baseline → mcp_fast: all_observed_pairs

Eligible paired attempts: 5.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 4 | 2,055,350 | 2,302,085 | +12.00% | +19.42% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Uncached input | 4 | 240,447 | 220,556 | -8.27% | -8.37% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Cached input | 4 | 1,746,816 | 2,016,512 | +15.44% | +24.64% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Cache-write input subset | 4 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Output | 4 | 68,087 | 65,017 | -4.51% | -2.83% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Reasoning output subset | 4 | 29,280 | 25,754 | -12.04% | -11.87% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Agent seconds | 5 | 2,946.12 | 2,982.16 | +1.22% | +7.09% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 5 | 2,956.38 | 3,096.53 | +4.74% | +9.68% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |

### baseline → mcp_fast: normally_completed_pairs

Eligible paired attempts: 3.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 3 | 1,517,754 | 1,687,681 | +11.20% | +24.55% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Uncached input | 3 | 186,807 | 176,121 | -5.72% | +0.42% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Cached input | 3 | 1,283,456 | 1,466,624 | +14.27% | +30.61% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Cache-write input subset | 3 | 0 | 0 | unknown | unknown | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Output | 3 | 47,491 | 44,936 | -5.38% | -3.16% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Reasoning output subset | 3 | 19,089 | 16,317 | -14.52% | -16.33% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Agent seconds | 3 | 1,498.13 | 1,359.47 | -9.26% | -18.94% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 3 | 1,504.10 | 1,428.63 | -5.02% | -14.00% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |

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

Eligible paired attempts: 5.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 5 | 2,893,561 | 3,074,262 | +6.24% | +6.33% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Uncached input | 5 | 309,169 | 332,689 | +7.61% | -2.19% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Cached input | 5 | 2,493,952 | 2,657,920 | +6.57% | +3.09% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Cache-write input subset | 5 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Output | 5 | 90,440 | 83,653 | -7.50% | -2.45% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Reasoning output subset | 5 | 38,897 | 32,266 | -17.05% | -22.37% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Agent seconds | 5 | 2,946.12 | 2,718.40 | -7.73% | -17.77% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 5 | 2,956.38 | 2,842.94 | -3.84% | -15.44% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |

### baseline → mcp_qwen: normally_completed_pairs

Eligible paired attempts: 4.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 4 | 2,355,965 | 2,502,661 | +6.23% | +14.19% | click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Uncached input | 4 | 255,529 | 254,749 | -0.31% | -3.65% | click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Cached input | 4 | 2,030,592 | 2,180,224 | +7.37% | +19.26% | click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Cache-write input subset | 4 | 0 | 0 | unknown | unknown | click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Output | 4 | 69,844 | 67,688 | -3.09% | -0.69% | click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Reasoning output subset | 4 | 28,706 | 26,120 | -9.01% | -18.32% | click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Agent seconds | 4 | 2,273.32 | 2,177.50 | -4.21% | -11.25% | click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 4 | 2,282.31 | 2,273.04 | -0.41% | -7.47% | click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |

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

Eligible paired attempts: 5.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 4 | 2,302,085 | 2,220,412 | -3.55% | -2.88% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Uncached input | 4 | 220,556 | 244,708 | +10.95% | +10.10% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Cached input | 4 | 2,016,512 | 1,910,272 | -5.27% | -4.95% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Cache-write input subset | 4 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Output | 4 | 65,017 | 65,432 | +0.64% | +6.36% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Reasoning output subset | 4 | 25,754 | 25,426 | -1.27% | +9.76% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Agent seconds | 5 | 2,982.16 | 2,718.40 | -8.84% | -2.24% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Service setup seconds | 5 | 102.25 | 108.79 | +6.40% | +5.88% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |
| Full trial seconds | 5 | 3,096.53 | 2,842.94 | -8.19% | -1.29% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1 |

### mcp_fast → mcp_qwen: normally_completed_pairs

Eligible paired attempts: 4.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 4 | 2,302,085 | 2,220,412 | -3.55% | -2.88% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Uncached input | 4 | 220,556 | 244,708 | +10.95% | +10.10% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Cached input | 4 | 2,016,512 | 1,910,272 | -5.27% | -4.95% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Cache-write input subset | 4 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Output | 4 | 65,017 | 65,432 | +0.64% | +6.36% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Reasoning output subset | 4 | 25,754 | 25,426 | -1.27% | +9.76% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Agent seconds | 4 | 2,082.09 | 2,080.94 | -0.06% | +7.41% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Service setup seconds | 4 | 87.41 | 92.49 | +5.82% | +5.48% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |
| Full trial seconds | 4 | 2,178.45 | 2,184.88 | +0.30% | +7.76% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1 |

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

16/16 recorded attempts retain hash-verified published patches. Available artifacts retain original-test compatibility results, compact tool payloads, source snapshots, actual usage and process/clock receipts; startup or interrupted failures may lack some witnesses. Qwen requests retain native singleton forward witnesses. Independent evidence audits verify those bindings; independent patch review assesses defects beyond the frozen gates.

Task reuse, six attempts per condition, hosted variability, cache reuse, and observed tool-use differences limit causal attribution. Retrieval quality improvements do not establish coding correctness or paid-agent savings. The transport probe's usage is outside this eighteen-attempt inventory.
