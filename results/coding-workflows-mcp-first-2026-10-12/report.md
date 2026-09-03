# MCP-first coding workflow follow-up

The MCP condition completed a repair and passed every selected hidden check in **3/3 tasks**; the baseline did so in **3/3 tasks**. All 6 declared trials are retained. The same model, prompt and seeded starting source were assigned within each pair; preserved fixture and prompt hashes document successful preparations.

The MCP-assigned workload used **618,537 gross tokens versus 578,218** in baseline (7.0% more). Summed agent wall time was **489.87s versus 426.78s** (14.8% more). Setup and complete evaluator-run time are published separately below. This follow-up showed no aggregate token or completion-time savings on these reused tasks.

Each condition was assigned **10 declared hidden-verifier methods** across these tasks. This count is separate from parameterized agent-authored test cases reproduced later.

**Selected-check success is weaker than complete correctness:** a separate CPU probe found 2 atomic-output repairs that reported command success after a failed commit, leaving the old destination bytes. Their formal labels remain unchanged; [supplemental evidence](close-error-probe.json) documents the defect.

This is a separate exploratory follow-up using the first original task per repository, selected deterministically rather than from observed savings. Both conditions receive the same added instruction to use search_code before broad source reads when available. It measures an explicitly encouraged search workflow on reused tasks, not spontaneous adoption or a fresh holdout. Its resources and outcomes are not pooled with the original availability pilot or the separate exposure diagnostic.

This is a descriptive study of 3 authored regressions in pinned Requests, Click and Starlette repositories. It measures actual agent patches and runtime-reported token usage. The regressions were introduced deliberately; these are not newly discovered upstream bugs. The repositories were used in development evaluation, and the repairs are small. One run per condition cannot establish significance, broad generalization, stable savings, or the project's 30% token-saving target.

Agent: `gpt-6.1-sol`, reasoning effort `ultra`; `codex-cli 0.162.0-alpha.17.2` on Apple M4. The frozen schedule alternates which condition runs first. No retries were selected from outcomes. See [frozen configuration](frozen-config.json), [task manifest](tasks.json), [raw measurements](results.json), and [machine summary](summary.json).

## Correctness and time

Correctness is limited to the withheld regression checks and related negative controls, not full upstream test suites. The completed-and-passed label additionally requires a completed agent turn, exit code zero, and no agent failure or timeout. Agent wall time runs from CLI launch to process exit, including its initialization, model waits, tools, tests and MCP adapter startup. Checkout preparation, backend indexing/warmup and evaluator grading are separate costs.

| Task | Condition | Hidden checks | Completed and passed | Agent wall time (s) | Setup (s) | Grading (s) | Exit | Timeout |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| requests_auth_ports | baseline | pass | yes | 138.68 | 0.28 | 0.10 | 0 | no |
| requests_auth_ports | mcp | pass | yes | 141.92 | 16.21 | 0.07 | 0 | no |
| click_atomic_close | mcp | pass | yes | 138.61 | 25.72 | 0.05 | 0 | no |
| click_atomic_close | baseline | pass | yes | 110.36 | 0.22 | 0.08 | 0 | no |
| starlette_background_order | baseline | pass | yes | 177.75 | 0.28 | 0.09 | 0 | no |
| starlette_background_order | mcp | pass | yes | 209.33 | 20.29 | 0.07 | 0 | no |

| Condition | Cost | Measured runs | Total (s) | Median (s) |
| --- | --- | --- | --- | --- |
| baseline | Setup | 3 | 0.79 | 0.28 |
| baseline | Full evaluator run | 3 | 427.94 | 139.08 |
| mcp | Setup | 3 | 62.22 | 20.29 |
| mcp | Full evaluator run | 3 | 553.48 | 164.83 |

Setup includes fixture preparation and initial validation, plus MCP model loading, indexing and warmup when applicable. The full evaluator interval also includes post-agent cleanup, grading and patch capture; it is not a model-only or warm-search latency.

## Actual token usage

These counts come from terminal `turn.completed` usage events. **Gross total = input + output.** Cached input is already included in input; reasoning output is already included in output. Neither subcategory is added again. Uncached input = input − cached input. Missing measurements remain unavailable. These are provider-reported token counts, not the local `cl100k_base` excerpt budget and not a dollar-cost estimate.

Reported terminal usage events per trial: `01-requests_auth_ports-baseline`=1, `02-requests_auth_ports-mcp`=1, `03-click_atomic_close-mcp`=1, `04-click_atomic_close-baseline`=1, `05-starlette_background_order-baseline`=1, `06-starlette_background_order-mcp`=1. The recorded aggregate preserves the usage fields supplied by those terminal events; missing fields are not reconstructed from transcript text.

| Task | Condition | Input | Cached input | Uncached input | Output | Gross total | Reasoning output | Cache writes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| requests_auth_ports | baseline | 217,920 | 179,968 | 37,952 | 3,792 | 221,712 | 1,299 | 0 |
| requests_auth_ports | mcp | 176,787 | 135,808 | 40,979 | 4,001 | 180,788 | 1,738 | 0 |
| click_atomic_close | mcp | 196,860 | 171,008 | 25,852 | 2,302 | 199,162 | 367 | 0 |
| click_atomic_close | baseline | 139,852 | 108,672 | 31,180 | 2,972 | 142,824 | 1,205 | 0 |
| starlette_background_order | baseline | 208,516 | 161,664 | 46,852 | 5,166 | 213,682 | 2,219 | 0 |
| starlette_background_order | mcp | 232,600 | 154,496 | 78,104 | 5,987 | 238,587 | 3,133 | 0 |

## Paired resource comparisons

Positive reductions mean MCP used less; negative reductions mean it used more. Each metric includes only pairs with that metric available in both conditions, with its denominator shown. “Reduction of sums” compares aggregate workload; “reduction of medians” compares condition medians; “median paired reduction” takes the median of task-specific percentages. These are different statistics and should not be substituted for one another. Percentages with a zero baseline are unavailable.

### All declared task pairs

| Metric | Measured pairs | Baseline sum | MCP sum | Reduction of sums | Baseline median | MCP median | Reduction of medians | Median paired reduction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Agent wall time (s) | 3 | 426.78 | 489.87 | -14.8% | 138.68 | 141.92 | -2.3% | -17.8% |
| Input tokens | 3 | 566,288 | 606,247 | -7.1% | 208,516 | 196,860 | +5.6% | -11.6% |
| Cached input tokens | 3 | 450,304 | 461,312 | -2.4% | 161,664 | 154,496 | +4.4% | +4.4% |
| Uncached input tokens | 3 | 115,984 | 144,935 | -25.0% | 37,952 | 40,979 | -8.0% | -8.0% |
| Output tokens | 3 | 11,930 | 12,290 | -3.0% | 3,792 | 4,001 | -5.5% | -5.5% |
| Gross total tokens | 3 | 578,218 | 618,537 | -7.0% | 213,682 | 199,162 | +6.8% | -11.7% |

### Both completed and passed selected checks

This subset contains **3/3 pairs**. It compares resources when both conditions delivered a checked repair, while the complete inventory above retains failures and non-use of MCP.

| Metric | Measured pairs | Baseline sum | MCP sum | Reduction of sums | Baseline median | MCP median | Reduction of medians | Median paired reduction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Agent wall time (s) | 3 | 426.78 | 489.87 | -14.8% | 138.68 | 141.92 | -2.3% | -17.8% |
| Input tokens | 3 | 566,288 | 606,247 | -7.1% | 208,516 | 196,860 | +5.6% | -11.6% |
| Cached input tokens | 3 | 450,304 | 461,312 | -2.4% | 161,664 | 154,496 | +4.4% | +4.4% |
| Uncached input tokens | 3 | 115,984 | 144,935 | -25.0% | 37,952 | 40,979 | -8.0% | -8.0% |
| Output tokens | 3 | 11,930 | 12,290 | -3.0% | 3,792 | 4,001 | -5.5% | -5.5% |
| Gross total tokens | 3 | 578,218 | 618,537 | -7.0% | 213,682 | 199,162 | +6.8% | -11.7% |

| Task | Both completed and passed | Time reduction | Input reduction | Cached-input reduction | Uncached-input reduction | Output reduction | Gross-total reduction |
| --- | --- | --- | --- | --- | --- | --- | --- |
| requests_auth_ports | yes | -2.3% | +18.9% | +24.5% | -8.0% | -5.5% | +18.5% |
| click_atomic_close | yes | -25.6% | -40.8% | -57.4% | +17.1% | +22.5% | -39.4% |
| starlette_background_order | yes | -17.8% | -11.6% | +4.4% | -66.7% | -15.9% | -11.7% |

## MCP uptake and actual calls

**3/3 MCP-assigned runs called a tool**, with 3 recorded calls, 3 completed successful search responses with self-consistent source hashes, and 0 reported tool errors. 3 calls changed at least one argument in the frozen default map. Availability is the assigned treatment; a run that did not use MCP remains in the comparison.

Frozen defaults: `{"include_dependencies": true, "max_results": 8, "rerank": false, "retrieval": "dense", "source_budget": 12000, "token_budget": 4096}`. Queries are never counted as overrides. Explicit arguments absent from the frozen default map are listed separately, without guessing their defaults. H2O was not running; calls requesting reranking can fail and remain recorded.

| Task | Condition | Ordinary command calls | File-change items | MCP calls |
| --- | --- | --- | --- | --- |
| requests_auth_ports | baseline | 18 | 2 | 0 |
| requests_auth_ports | mcp | 10 | 2 | 1 |
| click_atomic_close | mcp | 11 | 2 | 1 |
| click_atomic_close | baseline | 10 | 2 | 0 |
| starlette_background_order | baseline | 22 | 3 | 0 |
| starlette_background_order | mcp | 12 | 2 | 1 |

Ordinary command calls are completed shell-command items. File-change items count completed edit items in the preserved trace; a command may also edit files, so these columns describe recorded tool activity rather than a count of changed files.

| Task | Tool | Supplied arguments | Changed frozen defaults | Arguments outside default map | Tool error |
| --- | --- | --- | --- | --- | --- |
| requests_auth_ports | search_code | {"include_dependencies": false, "max_results": 3, "query": "Requests redirect Authorization header should_strip_auth hostname scheme port default ports HTTP HTTPS upgrade", "source_budget": 6500, "token_budget": 2400} | {"include_dependencies": false, "max_results": 3, "source_budget": 6500, "token_budget": 2400} | {} | no |
| click_atomic_close | search_code | {"include_dependencies": false, "max_results": 4, "query": "Click File.convert output option lazy open atomic writes ctx.call_on_close LazyFile close close_intelligently", "source_budget": 13000, "token_budget": 4200} | {"include_dependencies": false, "max_results": 4, "source_budget": 13000, "token_budget": 4200} | {} | no |
| starlette_background_order | search_code | {"include_dependencies": true, "max_results": 5, "query": "Starlette BackgroundTasks collect add_task background callbacks then execute registered tasks after response sends, asynchronous and synchronous BackgroundTask __call__", "retrieval": "bm25", "source_budget": 9000, "token_budget": 3000} | {"max_results": 5, "retrieval": "bm25", "source_budget": 9000, "token_budget": 3000} | {} | no |

| Task | Call | Successful response | Match status | Local excerpt tokens | Local budget | Returned source excerpts | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| requests_auth_ports | item_2 | yes | candidates | 1101 | 2400 | src/requests/sessions.py:SessionRedirectMixin.should_strip_auth L154–176 (retrieval; truncated=False; SHA256=a90b9e25bd6e853d17e06f38abe5417e24e42ec7d430cd8d94233329f2e0525e); src/requests/sessions.py:SessionRedirectMixin.rebuild_auth L301–324 (retrieval; truncated=False; SHA256=ddd3997ad6d691e21896aa22b8ffe74dee94d6a2768578c59eaaf58de36b7dbb); src/requests/sessions.py:SessionRedirectMixin.rebuild_proxies L326–360 (retrieval; truncated=False; SHA256=af1d06abd7643f36583273e3d77018177d258399ced71f6bd481cd61a54d8fb4) | [trace](runs/02-requests_auth_ports-mcp/events.jsonl) |
| click_atomic_close | item_2 | yes | candidates | 908 | 4200 | src/click/types.py:File.convert L704–742 (retrieval; truncated=False; SHA256=84ebe45ce5b538c9e2e501b625e8b4bf7b140133f74cdb1417b50cdbed895af7); src/click/utils.py:LazyFile.close_intelligently L171–176 (retrieval; truncated=False; SHA256=d0669b1a80ab0b88d62a3795a2931b79093d39fd0c21e1f0c5f8e9e6bcdd566e); src/click/utils.py:LazyFile.close L166–169 (retrieval; truncated=False; SHA256=41c47321b32c85e444e92e3d2639f3bb5e3c3e7c97009229ca4c1ffb4e973cc0); src/click/_compat.py:_AtomicFile.close L465–470 (retrieval; truncated=False; SHA256=7be553b407ceb3a4d96482711e39f51d78deb525bfad29a6285997697bd986f4) | [trace](runs/03-click_atomic_close-mcp/events.jsonl) |
| starlette_background_order | item_2 | yes | candidates | 846 | 3000 | starlette/background.py:BackgroundTasks.add_task L35–37 (retrieval; truncated=False; SHA256=5c65a719e5fe5947291ce235508c95af05df8f2d7690628b016e3a72a1d589b8); starlette/background.py:BackgroundTasks.__call__ L39–41 (retrieval; truncated=False; SHA256=848efdf777bafd44d99d611e38db29a4f000d5e17df4b78150336611621fa5ab); starlette/background.py:BackgroundTasks.__init__ L32–33 (retrieval; truncated=False; SHA256=ce83653e0f00f009db8420a70ceedc9104224b2e44b715fdc2af2b374a051559); starlette/responses.py:StreamingResponse.__call__ L252–271 (retrieval; truncated=False; SHA256=b919a0fb3fef66f8e435fe845f7f50a9545d985f028209411569f83624d3f690); starlette/background.py:BackgroundTask.__call__ L24–28 (retrieval; truncated=False; SHA256=eefe65980ba39eb785d74ca27e9e00c77dd2837593c79c04f6fea61eecfcc7e1) | [trace](runs/06-starlette_background_order-mcp/events.jsonl) |

These returned excerpts are preserved MCP response payloads, with no independently recorded HTTP receipt in the paired workflow runner. SHA256 checks above verify the returned source bytes are internally consistent with their reported hashes; they do not independently prove relevance or historical saved-file identity. Historical identity requires source reconstruction from the seeded tree and edit trace, while the hidden checks evaluate the final repair. Local excerpt tokens and budgets in this table are separate from actual provider input/output usage reported above.

See the [independent artifact audit](audit.json) for recorded usage, settings, fixtures and trace checks. Returned excerpt bytes and bounds are checked in the separate MCP source audit below.

The [independent MCP source audit](mcp-source-audit.json) covers **3/3 observed calls**; recorded audit passed=`True`, errors=0, source-freshness reviews needed=0.

| Run | Call | No observed prior mutation | Excerpts | Valid source hashes | Valid physical bounds | Match seeded Git bytes |
| --- | --- | --- | --- | --- | --- | --- |
| 02-requests_auth_ports-mcp | item_2 | True | 3 | 3 | 3 | 3 |
| 03-click_atomic_close-mcp | item_2 | True | 4 | 4 | 4 | 4 |
| 06-starlette_background_order-mcp | item_2 | True | 5 | 5 | 5 | 5 |

Paired runner records CLI MCP items and initial/final backend status, not separate per-call HTTP receipts or independently timestamped source snapshots. Read-only command classification and file-change events are observed-trace diagnostics, not an adversarial security proof. Seeded Git identity is established only for calls before observed mutations; matching source metadata does not itself prove semantic relevance or savings.

| Task | Final backend ready | Dense index ready | Windows | Source files | Final source manifest |
| --- | --- | --- | --- | --- | --- |
| requests_auth_ports | True | True | 240 | 19 | 87d991cabc83b67de8d2dd3ac94ed4b80564ea049351e28a5522725ef631aeae |
| click_atomic_close | True | True | 474 | 16 | 285ddc3eecc41538a93577e7806210d0c9c316dc3034c13e8011d5a6c4885ced |
| starlette_background_order | True | True | 472 | 34 | 7ae5501b736e86abfd77e348b1b30788ab85fd9c6c9caa7d90f5fbe0ea136e4a |

These are final backend/index snapshots after agent execution, not per-call HTTP receipts or proof of the source state at an earlier query.

The host's handling of MCP text and structured content is part of these observed input counts. No adjustment was made from local excerpt sizes. Cached input is shown separately because provider caching and paired execution order can affect observed workload and latency.

## Observed CLI behavior

The frozen configuration requested `features.multi_agent=false` and `features.multi_agent_v2=false` in both conditions. Despite those intended settings, the alpha CLI's preserved stderr reports failed collaboration-spawn attempts in 3 baseline trials, 3 mcp trials. The failures report “no rollout found” while loading a model context in these ephemeral runs. Related rollout and subagent-hook warnings are preserved. These are observed runtime warnings rather than evidence that the intended disable flags fully controlled behavior. Agent durations and reported usage retain their overhead. Ordinary-command and MCP counts cover completed recorded items; failed spawn attempts reported only in stderr are counted separately here.

No successful subagent spawn is visible in the published event streams. This statement is limited to recorded observations.

| Run | Failed spawn stderr lines | Rollout warning lines | Subagent-hook warning lines | Completed spawn items | Stderr |
| --- | --- | --- | --- | --- | --- |
| 01-requests_auth_ports-baseline | 1 | 5 | 1 | 0 | [stderr](runs/01-requests_auth_ports-baseline/stderr.txt) |
| 02-requests_auth_ports-mcp | 1 | 5 | 0 | 0 | [stderr](runs/02-requests_auth_ports-mcp/stderr.txt) |
| 03-click_atomic_close-mcp | 1 | 5 | 1 | 0 | [stderr](runs/03-click_atomic_close-mcp/stderr.txt) |
| 04-click_atomic_close-baseline | 1 | 5 | 0 | 0 | [stderr](runs/04-click_atomic_close-baseline/stderr.txt) |
| 05-starlette_background_order-baseline | 1 | 5 | 0 | 0 | [stderr](runs/05-starlette_background_order-baseline/stderr.txt) |
| 06-starlette_background_order-mcp | 2 | 5 | 0 | 0 | [stderr](runs/06-starlette_background_order-mcp/stderr.txt) |

## Failures and preserved patches

No trial had a failing hidden check, failed terminal turn, timeout, runner infrastructure error or invalid event line. Tool errors and runtime warnings, if any, are still listed above and preserved in the traces.

| Run | Patch | Agent checks and outcome | Tool trace |
| --- | --- | --- | --- |
| 01-requests_auth_ports-baseline | [patch](runs/01-requests_auth_ports-baseline/changes.patch) | [final response](runs/01-requests_auth_ports-baseline/final.txt) | [events](runs/01-requests_auth_ports-baseline/events.jsonl) |
| 02-requests_auth_ports-mcp | [patch](runs/02-requests_auth_ports-mcp/changes.patch) | [final response](runs/02-requests_auth_ports-mcp/final.txt) | [events](runs/02-requests_auth_ports-mcp/events.jsonl) |
| 03-click_atomic_close-mcp | [patch](runs/03-click_atomic_close-mcp/changes.patch) | [final response](runs/03-click_atomic_close-mcp/final.txt) | [events](runs/03-click_atomic_close-mcp/events.jsonl) |
| 04-click_atomic_close-baseline | [patch](runs/04-click_atomic_close-baseline/changes.patch) | [final response](runs/04-click_atomic_close-baseline/final.txt) | [events](runs/04-click_atomic_close-baseline/events.jsonl) |
| 05-starlette_background_order-baseline | [patch](runs/05-starlette_background_order-baseline/changes.patch) | [final response](runs/05-starlette_background_order-baseline/final.txt) | [events](runs/05-starlette_background_order-baseline/events.jsonl) |
| 06-starlette_background_order-mcp | [patch](runs/06-starlette_background_order-mcp/changes.patch) | [final response](runs/06-starlette_background_order-mcp/final.txt) | [events](runs/06-starlette_background_order-mcp/events.jsonl) |

## Supplemental atomic-output error check

A separate CPU fault-injection probe found **2 agent repairs** whose error behavior differs from pinned upstream Click. The diagnostic removes the atomic temporary source after the callback writes and flushes, before cleanup commits the destination. Upstream raises `FileNotFoundError` and exits with status 1. A repair can suppress that failure and exit successfully while the original destination bytes remain. See [complete diagnostic evidence](close-error-probe.json).

This check was predefined for this follow-up from the original pilot's post-hoc patch-review finding. It is excluded from the frozen correctness labels and paired performance estimates. The declared atomic-output checks can all pass while this additional compatibility defect remains; selected-check success does not establish complete correctness.

| Source | Frozen verifier passed | CLI exit | Exception | Original destination retained | Matches upstream error behavior |
| --- | --- | --- | --- | --- | --- |
| pinned_upstream | None | 1 | FileNotFoundError | True | True |
| 03-click_atomic_close-mcp | True | 0 | None | True | False |
| 04-click_atomic_close-baseline | True | 0 | None | True | False |

## Supplemental patch and regression review

This qualitative QA and CPU reproduction were performed after the measured trials and are excluded from frozen correctness and performance scores. They compare submitted production changes with pinned upstream behavior, reproduce selected agent-authored regressions on final and restored seeded code, and check testing claims against recorded command output. See [complete patch review](patch-review.json).

All 6 patches were reviewed. The submitted fixes passed 46 selected regression cases; restoring the seeded production faults while retaining those tests produced 36 expected failures and 10 passing controls. Every selected test set detected its seeded fault. The review found 0 unsupported testing claims within the claims examined. These post-trial case counts are separate from the original hidden-check totals.

All selected regression cases pass on fixes and detect their seeds. Both atomic-output repairs suppress failed commits, confirmed by a supplemental predefined probe. Both background repairs replace HTTP failure integration coverage with direct task checks. The paired production implementations are behaviorally equivalent; passing formal checks does not establish full correctness.

- `03-click_atomic_close-mcp` — Wraps the lazy close callback in safecall, suppressing atomic commit/close exceptions. Pinned upstream registers this callback directly. Confirmed by the supplemental predefined CPU fault-injection probe after both atomic-output trials completed. Frozen selected-check correctness remains unchanged; success-path tests omit atomic commit failure.

- `04-click_atomic_close-baseline` — Wraps lazy close in safecall, suppressing atomic commit/close exceptions. Pinned upstream invokes the lazy callback directly. Confirmed by the supplemental predefined CPU fault-injection probe after both atomic-output trials completed. Same error-path gap in both conditions; selected-check scores remain unchanged.

- `05-starlette_background_order-baseline` — Replaces an existing HTTP TestClient background-failure integration test with direct BackgroundTasks invocation. Stronger sync/async failure assertions detect the seed, but HTTP failure propagation is no longer exercised by that test. Verified in published patch and independently reproduced changed test selections. Production loop matches pinned upstream; direct failure invocation avoids the installed Trio TestClient error-cleanup incompatibility.

- `06-starlette_background_order-mcp` — Replaces an existing HTTP TestClient failure-propagation test with direct BackgroundTasks testing. New normal-order tests invoke the ASGI Response directly, avoiding TestClient entirely for those added cases. Verified in the published patch and independently reproduced new/modified selections. Production loop equals baseline and pinned upstream. Direct task/ASGI testing avoids the installed Trio TestClient error-cleanup incompatibility, while removing the former HTTP failure integration coverage.

| Run | Production and regression review | Scope limits |
| --- | --- | --- |
| 01-requests_auth_ports-baseline | Focused same-scheme implicit/default-port equivalence guard is behaviorally equivalent to pinned upstream. Host, unsafe upgrade/downgrade and nondefault-port guards unchanged. | Selected offline checks only; full upstream Requests suite not run. |
| 02-requests_auth_ports-mcp | Focused production guard is behaviorally equivalent to the paired baseline and pinned upstream. Existing host, scheme-upgrade, downgrade and genuinely different-port rules unchanged. | Selected offline checks only; full upstream Requests suite not run. New regression invokes rebuild_auth directly; the paired baseline uses resolve_redirects. Both independently detect the seed. |
| 03-click_atomic_close-mcp | Registers lazy-file cleanup and passes normal/atomic/unused-output checks, but introduces the same exception-suppressing wrapper as original failed-error-path repairs. | Normal close/commit success is covered; atomic commit errors are suppressed and not covered by selected success-path checks. Six relevant modules run; full upstream Click suite not claimed. |
| 04-click_atomic_close-baseline | Same production cleanup registration as paired MCP, including the material error-path deviation caused by safecall. Normal atomic/non-atomic writes and unused outputs behave correctly. | Added tests retain output references so garbage collection cannot substitute for command cleanup. Commit failure paths are not covered and safecall suppresses them. Seven relevant modules run; full upstream Click suite not claimed. |
| 05-starlette_background_order-baseline | One-line insertion-order loop repair equals pinned upstream; callback awaiting and failure propagation unchanged. Tests add explicit post-response sync/async/mixed/empty ordering and replace HTTP failure testing with direct collection tests. | Existing HTTP failure integration coverage is removed; stronger direct failure tests avoid the incompatible Trio cleanup path. Selected relevant modules only; full upstream Starlette suite not run. |
| 06-starlette_background_order-mcp | One-line loop is identical to baseline and pinned upstream. New direct-ASGI ordering tests await response sends and mixed callbacks; failure tests invoke the task collection directly. | Existing HTTP failure integration test is replaced with direct task failure checks. Related asyncio suites pass, but the installed AnyIO/Trio TestClient error-cleanup incompatibility remains outside these selections. Selected modules only; full upstream Starlette suite not run. |

## Practical limits

The hidden checks establish selected observable behavior and preserve nearby negative controls; they do not prove every repair is idiomatic, every new agent-authored test is adequate, or every upstream behavior remains correct. Manual patch review should accompany broader claims. The tasks are deliberately small seeded repairs in previously evaluated repositories, with no independent task holdout and no repeated stochastic trials. Hosted-model waits, background machine activity, prompt caching and condition order can affect timing and tokens. MCP loading, initial indexing and warmup were excluded from agent wall time and published separately. Fresh tasks and repeated runs are needed before claiming production savings or expanding the quality gate.
