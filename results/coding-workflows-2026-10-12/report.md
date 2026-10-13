# Paired coding workflow pilot

The MCP condition completed a repair and passed every selected hidden check in **6/6 tasks**; the baseline did so in **6/6 tasks**. All 12 declared trials are retained. The same model, prompt and seeded starting source were assigned within each pair; preserved fixture and prompt hashes document successful preparations.

The MCP-assigned workload used **1,431,818 gross tokens versus 1,583,509** in baseline (9.6% fewer). Summed agent wall time was **944.41s versus 926.31s** (2.0% more). Setup and complete evaluator-run time are published separately below. No MCP tools were called, so these condition differences do not measure active retrieval benefit.

Each condition was assigned **21 declared hidden-verifier methods** across these tasks. This count is separate from parameterized agent-authored test cases reproduced later.

**Selected-check success is weaker than complete correctness:** a separate CPU probe found 2 atomic-output repairs that reported command success after a failed commit, leaving the old destination bytes. Their formal labels remain unchanged; [supplemental evidence](close-error-probe.json) documents the defect.

This is a descriptive study of 6 authored regressions in pinned Requests, Click and Starlette repositories. It measures actual agent patches and runtime-reported token usage. The regressions were introduced deliberately; these are not newly discovered upstream bugs. The repositories were used in development evaluation, and the repairs are small. One run per condition cannot establish significance, broad generalization, stable savings, or the project's 30% token-saving target.

Agent: `gpt-6.1-sol`, reasoning effort `ultra`; `codex-cli 0.162.0-alpha.17.2` on Apple M4. The frozen schedule alternates which condition runs first. No retries were selected from outcomes. See [frozen configuration](frozen-config.json), [task manifest](tasks.json), [raw measurements](results.json), and [machine summary](summary.json).

## Correctness and time

Correctness is limited to the withheld regression checks and related negative controls, not full upstream test suites. The completed-and-passed label additionally requires a completed agent turn, exit code zero, and no agent failure or timeout. Agent wall time runs from CLI launch to process exit, including its initialization, model waits, tools, tests and MCP adapter startup. Checkout preparation, backend indexing/warmup and evaluator grading are separate costs.

| Task | Condition | Hidden checks | Completed and passed | Agent wall time (s) | Setup (s) | Grading (s) | Exit | Timeout |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| requests_auth_ports | baseline | pass | yes | 225.66 | 0.27 | 0.10 | 0 | no |
| requests_auth_ports | mcp | pass | yes | 179.84 | 16.24 | 0.07 | 0 | no |
| requests_redirect_methods | mcp | pass | yes | 147.51 | 14.95 | 0.08 | 0 | no |
| requests_redirect_methods | baseline | pass | yes | 144.68 | 0.31 | 0.10 | 0 | no |
| click_atomic_close | baseline | pass | yes | 115.95 | 0.22 | 0.08 | 0 | no |
| click_atomic_close | mcp | pass | yes | 99.16 | 25.74 | 0.06 | 0 | no |
| click_file_paths | mcp | pass | yes | 125.36 | 25.75 | 0.06 | 0 | no |
| click_file_paths | baseline | pass | yes | 95.89 | 0.23 | 0.08 | 0 | no |
| starlette_background_order | baseline | pass | yes | 161.39 | 0.30 | 0.09 | 0 | no |
| starlette_background_order | mcp | pass | yes | 244.92 | 20.54 | 0.08 | 0 | no |
| starlette_streaming_buffers | mcp | pass | yes | 147.63 | 20.63 | 0.08 | 0 | no |
| starlette_streaming_buffers | baseline | pass | yes | 182.75 | 0.30 | 0.10 | 0 | no |

| Condition | Cost | Measured runs | Total (s) | Median (s) |
| --- | --- | --- | --- | --- |
| baseline | Setup | 6 | 1.62 | 0.28 |
| baseline | Full evaluator run | 6 | 928.71 | 153.47 |
| mcp | Setup | 6 | 123.85 | 20.59 |
| mcp | Full evaluator run | 6 | 1,070.62 | 165.92 |

Setup includes fixture preparation and initial validation, plus MCP model loading, indexing and warmup when applicable. The full evaluator interval also includes post-agent cleanup, grading and patch capture; it is not a model-only or warm-search latency.

## Actual token usage

These counts come from terminal `turn.completed` usage events. **Gross total = input + output.** Cached input is already included in input; reasoning output is already included in output. Neither subcategory is added again. Uncached input = input − cached input. Missing measurements remain unavailable. These are provider-reported token counts, not the local `cl100k_base` excerpt budget and not a dollar-cost estimate.

Reported terminal usage events per trial: `01-requests_auth_ports-baseline`=1, `02-requests_auth_ports-mcp`=1, `03-requests_redirect_methods-mcp`=1, `04-requests_redirect_methods-baseline`=1, `05-click_atomic_close-baseline`=1, `06-click_atomic_close-mcp`=1, `07-click_file_paths-mcp`=1, `08-click_file_paths-baseline`=1, `09-starlette_background_order-baseline`=1, `10-starlette_background_order-mcp`=1, `11-starlette_streaming_buffers-mcp`=1, `12-starlette_streaming_buffers-baseline`=1. The recorded aggregate preserves the usage fields supplied by those terminal events; missing fields are not reconstructed from transcript text.

| Task | Condition | Input | Cached input | Uncached input | Output | Gross total | Reasoning output | Cache writes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| requests_auth_ports | baseline | 281,141 | 235,904 | 45,237 | 4,722 | 285,863 | 1,923 | 0 |
| requests_auth_ports | mcp | 248,596 | 196,992 | 51,604 | 4,900 | 253,496 | 1,719 | 0 |
| requests_redirect_methods | mcp | 216,447 | 184,704 | 31,743 | 3,288 | 219,735 | 1,332 | 0 |
| requests_redirect_methods | baseline | 388,868 | 340,864 | 48,004 | 2,665 | 391,533 | 269 | 0 |
| click_atomic_close | baseline | 216,148 | 152,320 | 63,828 | 3,245 | 219,393 | 1,140 | 0 |
| click_atomic_close | mcp | 181,508 | 134,656 | 46,852 | 2,732 | 184,240 | 1,010 | 0 |
| click_file_paths | mcp | 145,246 | 110,976 | 34,270 | 3,181 | 148,427 | 1,260 | 0 |
| click_file_paths | baseline | 136,164 | 82,816 | 53,348 | 2,681 | 138,845 | 855 | 0 |
| starlette_background_order | baseline | 275,686 | 217,344 | 58,342 | 3,255 | 278,941 | 634 | 0 |
| starlette_background_order | mcp | 279,720 | 229,632 | 50,088 | 4,954 | 284,674 | 1,222 | 0 |
| starlette_streaming_buffers | mcp | 337,279 | 282,880 | 54,399 | 3,967 | 341,246 | 1,519 | 0 |
| starlette_streaming_buffers | baseline | 264,896 | 205,696 | 59,200 | 4,038 | 268,934 | 1,381 | 0 |

## Paired resource comparisons

Positive reductions mean MCP used less; negative reductions mean it used more. Each metric includes only pairs with that metric available in both conditions, with its denominator shown. “Reduction of sums” compares aggregate workload; “reduction of medians” compares condition medians; “median paired reduction” takes the median of task-specific percentages. These are different statistics and should not be substituted for one another. Percentages with a zero baseline are unavailable.

### All declared task pairs

| Metric | Measured pairs | Baseline sum | MCP sum | Reduction of sums | Baseline median | MCP median | Reduction of medians | Median paired reduction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Agent wall time (s) | 6 | 926.31 | 944.41 | -2.0% | 153.04 | 147.57 | +3.6% | +6.3% |
| Input tokens | 6 | 1,562,903 | 1,408,796 | +9.9% | 270,291 | 232,521.5 | +14.0% | +5.1% |
| Cached input tokens | 6 | 1,234,944 | 1,139,840 | +7.7% | 211,520 | 190,848 | +9.8% | +3.0% |
| Uncached input tokens | 6 | 327,959 | 268,956 | +18.0% | 55,845 | 48,470 | +13.2% | +20.4% |
| Output tokens | 6 | 20,606 | 23,022 | -11.7% | 3,250 | 3,627.5 | -11.6% | -11.2% |
| Gross total tokens | 6 | 1,583,509 | 1,431,818 | +9.6% | 273,937.5 | 236,615.5 | +13.6% | +4.6% |

### Both completed and passed selected checks

This subset contains **6/6 pairs**. It compares resources when both conditions delivered a checked repair, while the complete inventory above retains failures and non-use of MCP.

| Metric | Measured pairs | Baseline sum | MCP sum | Reduction of sums | Baseline median | MCP median | Reduction of medians | Median paired reduction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Agent wall time (s) | 6 | 926.31 | 944.41 | -2.0% | 153.04 | 147.57 | +3.6% | +6.3% |
| Input tokens | 6 | 1,562,903 | 1,408,796 | +9.9% | 270,291 | 232,521.5 | +14.0% | +5.1% |
| Cached input tokens | 6 | 1,234,944 | 1,139,840 | +7.7% | 211,520 | 190,848 | +9.8% | +3.0% |
| Uncached input tokens | 6 | 327,959 | 268,956 | +18.0% | 55,845 | 48,470 | +13.2% | +20.4% |
| Output tokens | 6 | 20,606 | 23,022 | -11.7% | 3,250 | 3,627.5 | -11.6% | -11.2% |
| Gross total tokens | 6 | 1,583,509 | 1,431,818 | +9.6% | 273,937.5 | 236,615.5 | +13.6% | +4.6% |

| Task | Both completed and passed | Time reduction | Input reduction | Cached-input reduction | Uncached-input reduction | Output reduction | Gross-total reduction |
| --- | --- | --- | --- | --- | --- | --- | --- |
| requests_auth_ports | yes | +20.3% | +11.6% | +16.5% | -14.1% | -3.8% | +11.3% |
| requests_redirect_methods | yes | -2.0% | +44.3% | +45.8% | +33.9% | -23.4% | +43.9% |
| click_atomic_close | yes | +14.5% | +16.0% | +11.6% | +26.6% | +15.8% | +16.0% |
| click_file_paths | yes | -30.7% | -6.7% | -34.0% | +35.8% | -18.6% | -6.9% |
| starlette_background_order | yes | -51.8% | -1.5% | -5.7% | +14.1% | -52.2% | -2.1% |
| starlette_streaming_buffers | yes | +19.2% | -27.3% | -37.5% | +8.1% | +1.8% | -26.9% |

## MCP uptake and actual calls

**0/6 MCP-assigned runs called a tool**, with 0 recorded calls, 0 completed successful search responses with self-consistent source hashes, and 0 reported tool errors. 0 calls changed at least one argument in the frozen default map. Availability is the assigned treatment; a run that did not use MCP remains in the comparison.

Frozen defaults: `{"include_dependencies": true, "max_results": 8, "rerank": false, "retrieval": "dense", "source_budget": 12000, "token_budget": 4096}`. Queries are never counted as overrides. Explicit arguments absent from the frozen default map are listed separately, without guessing their defaults. H2O was not running; calls requesting reranking can fail and remain recorded.

**No MCP-assigned run used a search tool.** This is an availability pilot: observed time and token differences cannot be attributed to actual semantic retrieval. The study retains non-use as an outcome; it does not demonstrate an active MCP search benefit.

| Task | Condition | Ordinary command calls | File-change items | MCP calls |
| --- | --- | --- | --- | --- |
| requests_auth_ports | baseline | 22 | 2 | 0 |
| requests_auth_ports | mcp | 12 | 2 | 0 |
| requests_redirect_methods | mcp | 15 | 2 | 0 |
| requests_redirect_methods | baseline | 20 | 2 | 0 |
| click_atomic_close | baseline | 14 | 2 | 0 |
| click_atomic_close | mcp | 20 | 2 | 0 |
| click_file_paths | mcp | 10 | 2 | 0 |
| click_file_paths | baseline | 9 | 2 | 0 |
| starlette_background_order | baseline | 11 | 2 | 0 |
| starlette_background_order | mcp | 25 | 4 | 0 |
| starlette_streaming_buffers | mcp | 17 | 3 | 0 |
| starlette_streaming_buffers | baseline | 18 | 2 | 0 |

Ordinary command calls are completed shell-command items. File-change items count completed edit items in the preserved trace; a command may also edit files, so these columns describe recorded tool activity rather than a count of changed files.

| Task | Final backend ready | Dense index ready | Windows | Source files | Final source manifest |
| --- | --- | --- | --- | --- | --- |
| requests_auth_ports | True | True | 240 | 19 | b339026c58d64972b8d3b5e5bbfbc0bfd7ea0599f4aae7d7bc42f57fae3c3690 |
| requests_redirect_methods | True | True | 240 | 19 | b041115ed361a62695d7344d196c973f6a7ecd2fb4843f3b4beaad29f8d0f6b4 |
| click_atomic_close | True | True | 474 | 16 | 285ddc3eecc41538a93577e7806210d0c9c316dc3034c13e8011d5a6c4885ced |
| click_file_paths | True | True | 474 | 16 | 242f003c19642f93b556010d692c907333453740c04fe80692876c5b68cf707e |
| starlette_background_order | True | True | 472 | 34 | 7ae5501b736e86abfd77e348b1b30788ab85fd9c6c9caa7d90f5fbe0ea136e4a |
| starlette_streaming_buffers | True | True | 472 | 34 | 21f53e31607347b81de8c9628028e4125b3b14fac9a1b4c5306e1c141aab79b3 |

These are final backend/index snapshots after agent execution, not per-call HTTP receipts or proof of the source state at an earlier query.

The host's handling of MCP text and structured content is part of these observed input counts. No adjustment was made from local excerpt sizes. Cached input is shown separately because provider caching and paired execution order can affect observed workload and latency.

## Observed CLI behavior

The frozen configuration requested `features.multi_agent=false` and `features.multi_agent_v2=false` in both conditions. Despite those intended settings, the alpha CLI's preserved stderr reports failed collaboration-spawn attempts in 6 baseline trials, 6 mcp trials. The failures report “no rollout found” while loading a model context in these ephemeral runs. Related rollout and subagent-hook warnings are preserved. These are observed runtime warnings rather than evidence that the intended disable flags fully controlled behavior. Agent durations and reported usage retain their overhead. Ordinary-command and MCP counts cover completed recorded items; failed spawn attempts reported only in stderr are counted separately here.

No successful subagent spawn is visible in the published event streams. This statement is limited to recorded observations.

| Run | Failed spawn stderr lines | Rollout warning lines | Subagent-hook warning lines | Completed spawn items | Stderr |
| --- | --- | --- | --- | --- | --- |
| 01-requests_auth_ports-baseline | 1 | 5 | 1 | 0 | [stderr](runs/01-requests_auth_ports-baseline/stderr.txt) |
| 02-requests_auth_ports-mcp | 1 | 5 | 1 | 0 | [stderr](runs/02-requests_auth_ports-mcp/stderr.txt) |
| 03-requests_redirect_methods-mcp | 2 | 5 | 0 | 0 | [stderr](runs/03-requests_redirect_methods-mcp/stderr.txt) |
| 04-requests_redirect_methods-baseline | 1 | 5 | 2 | 0 | [stderr](runs/04-requests_redirect_methods-baseline/stderr.txt) |
| 05-click_atomic_close-baseline | 2 | 5 | 0 | 0 | [stderr](runs/05-click_atomic_close-baseline/stderr.txt) |
| 06-click_atomic_close-mcp | 1 | 5 | 0 | 0 | [stderr](runs/06-click_atomic_close-mcp/stderr.txt) |
| 07-click_file_paths-mcp | 1 | 5 | 0 | 0 | [stderr](runs/07-click_file_paths-mcp/stderr.txt) |
| 08-click_file_paths-baseline | 1 | 5 | 1 | 0 | [stderr](runs/08-click_file_paths-baseline/stderr.txt) |
| 09-starlette_background_order-baseline | 1 | 5 | 2 | 0 | [stderr](runs/09-starlette_background_order-baseline/stderr.txt) |
| 10-starlette_background_order-mcp | 1 | 5 | 2 | 0 | [stderr](runs/10-starlette_background_order-mcp/stderr.txt) |
| 11-starlette_streaming_buffers-mcp | 1 | 5 | 0 | 0 | [stderr](runs/11-starlette_streaming_buffers-mcp/stderr.txt) |
| 12-starlette_streaming_buffers-baseline | 1 | 5 | 1 | 0 | [stderr](runs/12-starlette_streaming_buffers-baseline/stderr.txt) |

## Failures and preserved patches

No trial had a failing hidden check, failed terminal turn, timeout, runner infrastructure error or invalid event line. Tool errors and runtime warnings, if any, are still listed above and preserved in the traces.

| Run | Patch | Agent checks and outcome | Tool trace |
| --- | --- | --- | --- |
| 01-requests_auth_ports-baseline | [patch](runs/01-requests_auth_ports-baseline/changes.patch) | [final response](runs/01-requests_auth_ports-baseline/final.txt) | [events](runs/01-requests_auth_ports-baseline/events.jsonl) |
| 02-requests_auth_ports-mcp | [patch](runs/02-requests_auth_ports-mcp/changes.patch) | [final response](runs/02-requests_auth_ports-mcp/final.txt) | [events](runs/02-requests_auth_ports-mcp/events.jsonl) |
| 03-requests_redirect_methods-mcp | [patch](runs/03-requests_redirect_methods-mcp/changes.patch) | [final response](runs/03-requests_redirect_methods-mcp/final.txt) | [events](runs/03-requests_redirect_methods-mcp/events.jsonl) |
| 04-requests_redirect_methods-baseline | [patch](runs/04-requests_redirect_methods-baseline/changes.patch) | [final response](runs/04-requests_redirect_methods-baseline/final.txt) | [events](runs/04-requests_redirect_methods-baseline/events.jsonl) |
| 05-click_atomic_close-baseline | [patch](runs/05-click_atomic_close-baseline/changes.patch) | [final response](runs/05-click_atomic_close-baseline/final.txt) | [events](runs/05-click_atomic_close-baseline/events.jsonl) |
| 06-click_atomic_close-mcp | [patch](runs/06-click_atomic_close-mcp/changes.patch) | [final response](runs/06-click_atomic_close-mcp/final.txt) | [events](runs/06-click_atomic_close-mcp/events.jsonl) |
| 07-click_file_paths-mcp | [patch](runs/07-click_file_paths-mcp/changes.patch) | [final response](runs/07-click_file_paths-mcp/final.txt) | [events](runs/07-click_file_paths-mcp/events.jsonl) |
| 08-click_file_paths-baseline | [patch](runs/08-click_file_paths-baseline/changes.patch) | [final response](runs/08-click_file_paths-baseline/final.txt) | [events](runs/08-click_file_paths-baseline/events.jsonl) |
| 09-starlette_background_order-baseline | [patch](runs/09-starlette_background_order-baseline/changes.patch) | [final response](runs/09-starlette_background_order-baseline/final.txt) | [events](runs/09-starlette_background_order-baseline/events.jsonl) |
| 10-starlette_background_order-mcp | [patch](runs/10-starlette_background_order-mcp/changes.patch) | [final response](runs/10-starlette_background_order-mcp/final.txt) | [events](runs/10-starlette_background_order-mcp/events.jsonl) |
| 11-starlette_streaming_buffers-mcp | [patch](runs/11-starlette_streaming_buffers-mcp/changes.patch) | [final response](runs/11-starlette_streaming_buffers-mcp/final.txt) | [events](runs/11-starlette_streaming_buffers-mcp/events.jsonl) |
| 12-starlette_streaming_buffers-baseline | [patch](runs/12-starlette_streaming_buffers-baseline/changes.patch) | [final response](runs/12-starlette_streaming_buffers-baseline/final.txt) | [events](runs/12-starlette_streaming_buffers-baseline/events.jsonl) |

## Supplemental atomic-output error check

A separate CPU fault-injection probe found **2 agent repairs** whose error behavior differs from pinned upstream Click. The diagnostic removes the atomic temporary source after the callback writes and flushes, before cleanup commits the destination. Upstream raises `FileNotFoundError` and exits with status 1. A repair can suppress that failure and exit successfully while the original destination bytes remain. See [complete diagnostic evidence](close-error-probe.json).

This check was selected after patch review. It is excluded from the frozen correctness labels and paired performance estimates. The declared atomic-output checks can all pass while this additional compatibility defect remains; selected-check success does not establish complete correctness.

| Source | Frozen verifier passed | CLI exit | Exception | Original destination retained | Matches upstream error behavior |
| --- | --- | --- | --- | --- | --- |
| pinned_upstream | None | 1 | FileNotFoundError | True | True |
| 05-click_atomic_close-baseline | True | 0 | None | True | False |
| 06-click_atomic_close-mcp | True | 0 | None | True | False |

## Supplemental patch and regression review

This qualitative QA and CPU reproduction were performed after the measured trials and are excluded from frozen correctness and performance scores. They compare submitted production changes with pinned upstream behavior, reproduce selected agent-authored regressions on final and restored seeded code, and check testing claims against recorded command output. See [complete patch review](patch-review.json).

All 12 patches were reviewed. The submitted fixes passed 147 selected regression cases; restoring the seeded production faults while retaining those tests produced 50 expected failures and 97 passing controls. Every selected test set detected its seeded fault. The review found 0 unsupported testing claims within the claims examined. These post-trial case counts are separate from the original hidden-check totals.

All selected regressions pass on the submitted fixes. Both Click atomic-output repairs silently suppress failed commits, confirmed by separate fault injection. Trial 10 replaces an HTTP failure integration test with direct task tests. Passing selected checks is therefore weaker than full correctness.

- `05-click_atomic_close-baseline` — Both repairs wrap lazy-file intelligent close in safecall, whereas pinned upstream invokes it directly. This suppresses close/atomic-rename exceptions and may report command success without publishing output. Confirmed by independent CPU fault injection after the trials completed. Same change in both conditions; keep frozen grading unchanged.

- `06-click_atomic_close-mcp` — Both repairs wrap lazy-file intelligent close in safecall, whereas pinned upstream invokes it directly. This suppresses close/atomic-rename exceptions and may report command success without publishing output. Confirmed by independent CPU fault injection after the trials completed. Same change in both conditions; keep frozen grading unchanged.

- `10-starlette_background_order-mcp` — Replaces the existing HTTP TestClient failure-propagation test with a direct BackgroundTasks invocation. New assertions are stronger for callback ordering and sync/async failure, but HTTP failure integration coverage is removed. Verified in published patch and reproduced changed tests. Production loop remains identical to baseline; all14 background tests pass because direct invocation avoids the existing Trio TestClient cleanup incompatibility.

| Run | Production and regression review | Scope limits |
| --- | --- | --- |
| 01-requests_auth_ports-baseline | Restores same-scheme omitted/default-port equivalence after host and safe-upgrade checks; different hosts, nondefault ports and downgrades still strip credentials. Focused source change matches requested behavior. | Selected behavioral/regression checks; complete upstream Requests suite not run. |
| 02-requests_auth_ports-mcp | Restores same-scheme implicit/default-port equivalence after host and upgrade guards. Production behavior equals baseline repair; variable naming and comment differ. | Selected checks; complete upstream Requests suite not run. New test exercises rebuild_auth directly; baseline test exercises resolve_redirects. Both detect the seeded regression. |
| 03-requests_redirect_methods-mcp | Changes only the 301 rewrite predicate from non-HEAD to POST. Existing 302/303 rewrites and 307/308 body/method preservation remain intact. | Selected checks; complete upstream Requests suite not run. |
| 04-requests_redirect_methods-baseline | Identical one-predicate production repair to MCP trial: only POST rewrites on301; existing branches retained. | Selected checks; complete upstream Requests suite not run. |
| 05-click_atomic_close-baseline | Registers lazy-file cleanup and passes ordinary/atomic/unused-output checks. Both conditions additionally introduce safecall around lazy close, differing from upstream exception propagation; this requires separate error-path validation. | Six relevant upstream modules run; complete upstream Click suite not claimed. Passing declared success-path checks does not establish production correctness: atomic commit failures are silently suppressed by this repair. |
| 06-click_atomic_close-mcp | Registers lazy-file cleanup and passes ordinary/atomic/unused-output checks. Both conditions additionally introduce safecall around lazy close, differing from upstream exception propagation; this requires separate error-path validation. | Five relevant upstream modules run; complete Click suite not claimed. New cases cover explicit lazy=True; baseline additionally covers default lazy=None. Passing declared success-path checks does not establish production correctness: atomic commit failures are silently suppressed by this repair. |
| 07-click_file_paths-mcp | Restores the exact pinned-upstream disallowed-directory guard before access checks, retaining normal self.fail usage error formatting. Callback is suppressed on validation failure. | Three relevant upstream modules run; complete upstream Click suite not claimed. |
| 08-click_file_paths-baseline | Identical production guard to MCP and pinned upstream: reject existing directories when dir_okay=False before access checks; normal error handling unchanged. | Two relevant upstream modules run; complete upstream Click suite not claimed. MCP new test additionally covers required missing path. |
| 09-starlette_background_order-baseline | One-line loop repair matches pinned upstream: await registered tasks in insertion order without reordering/mutating the collection. Existing error propagation stops later tasks. | Independent new/expanded regression reproduction uses asyncio only and targeted deprecation filter. Installed AnyIO4.15.1/Trio0.29.0 cleanup incompatibility causes2 background-suite failures; they were not counted as successful tests. Full upstream Starlette suite and Ruff not run. |
| 10-starlette_background_order-mcp | Same one-line insertion-order loop as baseline and pinned upstream. Test changes replace earlier commutative sum test with stronger response/callback ordering and replace TestClient failure path with direct BackgroundTasks invocation. | The installed AnyIO/Trio TestClient cleanup incompatibility remains; replacing the failure test with direct task invocation avoids that failing integration path rather than fixing dependencies. Direct task failure tests no longer cover failure propagation through HTTP TestClient. Selected background module only; full upstream Starlette suite not run. |
| 11-starlette_streaming_buffers-mcp | One-line binary-chunk predicate matches pinned upstream: memoryview and bytes bypass text encoding; other chunks retain existing charset encoding. Existing final-body, background and disconnect handling unchanged. | Independent reproduction and broad agent checks exclude Trio because installed AnyIO/Trio compatibility fails existing cleanup paths. Selected checks; full upstream Starlette suite not run. |
| 12-starlette_streaming_buffers-baseline | Production predicate is identical to MCP and pinned upstream: memoryview/bytes remain binary chunks; text encoding and existing streaming lifecycle unchanged. | Trio ASGI2.0 task-group cancellation fails due to installed AnyIO/Trio incompatibility; global response suite not fullypassing. Selected new regression cases reproduced; complete upstream Starlette suite and Ruff not run. |

## Practical limits

The hidden checks establish selected observable behavior and preserve nearby negative controls; they do not prove every repair is idiomatic, every new agent-authored test is adequate, or every upstream behavior remains correct. Manual patch review should accompany broader claims. The tasks are deliberately small seeded repairs in previously evaluated repositories, with no independent task holdout and no repeated stochastic trials. Hosted-model waits, background machine activity, prompt caching and condition order can affect timing and tokens. MCP loading, initial indexing and warmup were excluded from agent wall time and published separately. Fresh tasks and repeated runs are needed before claiming production savings or expanding the quality gate.
