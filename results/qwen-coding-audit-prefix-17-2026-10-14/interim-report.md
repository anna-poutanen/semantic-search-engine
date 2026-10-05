# Three-condition coding comparison — interim snapshot

17/18 frozen attempts recorded. These are two balanced repetitions of three previously observed development tasks; they do not establish general coding accuracy or token savings.

Frozen correctness requires both the strict contract checks and restored upstream compatibility. Completed-correct additionally requires normal, clean CLI completion. Supplemental independent patch findings remain separate from these frozen grades.

This snapshot is incomplete. Missing attempts are pending, and full-workload totals remain unknown.

## Full frozen inventory

| Condition | Recorded/planned | Normal | Correct | Completed-correct | Timeouts |
| --- | --- | --- | --- | --- | --- |
| baseline | 6/6 | 5 | 2 | 2 | 0 |
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
| 17-r2-starlette_body_limits-baseline | True | True | True | 317,500 | 40,399 | 358.73 | 0 | 0 |

¹ Measured forwards exclude separately recorded model warmup. Startup-failure counts are labelled as retained lower bounds when total counts are unknown. Availability is not evidence of actual tool use.

## Actual usage and time

Gross tokens are reported completed-turn input plus output. Cached input and cache writes are input subsets; reasoning tokens are an output subset. Subsets are never added to the gross total. Uncached input is input minus cached input. Unknown or interrupted-turn usage is not zero. These receipts do not establish per-request billing.

Agent time spans CLI launch through exit, including hosted waits and tool calls. Service setup covers indexing/startup/warmup before that timer; baseline has no measured service setup. Full trial time also includes preparation, cleanup and grading. Timing values require qualified clocks.

| Condition | Metric | Observed/planned | Measured sum | Condition median |
| --- | --- | --- | --- | --- |
| baseline | Gross tokens | 6/6 | 3,211,061 | 443,791.00 |
| baseline | Uncached input | 6/6 | 349,568 | 60,940.00 |
| baseline | Cached input | 6/6 | 2,758,784 | 367,808.00 |
| baseline | Cache-write input subset | 6/6 | 0 | 0.00 |
| baseline | Output | 6/6 | 102,709 | 18,135.50 |
| baseline | Reasoning output subset | 6/6 | 42,756 | 7,744.50 |
| baseline | Agent seconds | 6/6 | 3,304.85 | 570.35 |
| baseline | Service setup seconds | 0/6 | unknown | unknown |
| baseline | Full trial seconds | 6/6 | 3,316.58 | 572.63 |
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

Eligible paired attempts: 6.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 5 | 2,372,850 | 2,603,720 | +9.73% | +14.29% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Uncached input | 5 | 280,846 | 289,652 | +3.14% | +0.42% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Cached input | 5 | 2,011,648 | 2,236,288 | +11.17% | +18.67% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Cache-write input subset | 5 | 0 | 0 | unknown | unknown | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Output | 5 | 80,356 | 77,780 | -3.21% | -2.50% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Reasoning output subset | 5 | 33,139 | 30,050 | -9.32% | -7.40% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Agent seconds | 6 | 3,304.85 | 3,367.88 | +1.91% | +7.25% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1; starlette_body_limits/r2 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 6 | 3,316.58 | 3,504.13 | +5.65% | +10.46% | click_atomic_transactions/r1; click_atomic_transactions/r2; requests_response_limit/r1; requests_response_limit/r2; starlette_body_limits/r1; starlette_body_limits/r2 |

### baseline → mcp_fast: normally_completed_pairs

Eligible paired attempts: 4.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 4 | 1,835,254 | 1,989,316 | +8.39% | +11.30% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Uncached input | 4 | 227,206 | 245,217 | +7.93% | +2.29% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Cached input | 4 | 1,548,288 | 1,686,400 | +8.92% | +14.16% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Cache-write input subset | 4 | 0 | 0 | unknown | unknown | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Output | 4 | 59,760 | 57,699 | -3.45% | -0.04% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Reasoning output subset | 4 | 22,948 | 20,613 | -10.18% | -4.33% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Agent seconds | 4 | 1,856.86 | 1,745.20 | -6.01% | -5.93% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 4 | 1,864.30 | 1,836.23 | -1.51% | -2.16% | click_atomic_transactions/r2; requests_response_limit/r1; starlette_body_limits/r1; starlette_body_limits/r2 |

### baseline → mcp_fast: completed_correct_pairs

Eligible paired attempts: 2.

| Metric | Pairs | Left sum | Right sum | Aggregate change | Median pair change | Members |
| --- | --- | --- | --- | --- | --- | --- |
| Gross tokens | 2 | 646,518 | 711,430 | +10.04% | +9.78% | starlette_body_limits/r1; starlette_body_limits/r2 |
| Uncached input | 2 | 101,429 | 132,666 | +30.80% | +37.60% | starlette_body_limits/r1; starlette_body_limits/r2 |
| Cached input | 2 | 521,600 | 555,136 | +6.43% | +6.80% | starlette_body_limits/r1; starlette_body_limits/r2 |
| Cache-write input subset | 2 | 0 | 0 | unknown | unknown | starlette_body_limits/r1; starlette_body_limits/r2 |
| Output | 2 | 23,489 | 23,628 | +0.59% | +0.43% | starlette_body_limits/r1; starlette_body_limits/r2 |
| Reasoning output subset | 2 | 7,459 | 7,308 | -2.02% | -2.50% | starlette_body_limits/r1; starlette_body_limits/r2 |
| Agent seconds | 2 | 716.15 | 662.25 | -7.53% | -7.55% | starlette_body_limits/r1; starlette_body_limits/r2 |
| Service setup seconds | 0 | unknown | unknown | unknown | unknown | none |
| Full trial seconds | 2 | 719.04 | 706.49 | -1.75% | -1.77% | starlette_body_limits/r1; starlette_body_limits/r2 |

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

17/17 recorded attempts retain hash-verified published patches. Available artifacts retain original-test compatibility results, compact tool payloads, source snapshots, actual usage and process/clock receipts; startup or interrupted failures may lack some witnesses. Qwen requests retain native singleton forward witnesses. Independent evidence audits verify those bindings; independent patch review assesses defects beyond the frozen gates.

Task reuse, six attempts per condition, hosted variability, cache reuse, and observed tool-use differences limit causal attribution. Retrieval quality improvements do not establish coding correctness or paid-agent savings. The transport probe's usage is outside this eighteen-attempt inventory.
