# Coding comparison findings

All 18 scheduled attempts were retained and independently audited. Six completed correctly: two per condition, all on Starlette. Baseline, fast MCP and Qwen MCP therefore each finish 2/6 correctly; this study does not show a coding-correctness gain or meet the 30% token-savings target. Fast dense retrieval remains the product default; optional Qwen reranking remains experimental.

Fast MCP additionally produced a correct Requests snapshot in trial 11, but timed out after 900.06 seconds without completed-turn usage. Its tokens remain unknown and it is not completed-correct. Trial 06 baseline retained a provider 503 error despite CLI exit0, so it is excluded from normal-completion comparisons. There were 5 normal baseline, 5 normal fast-MCP and 6 normal Qwen completions.

The [complete numerical report](report.md) binds the [immutable final certificate](../qwen-coding-audit-final-2026-10-14/independent-audit.json). Gross tokens are reported input plus output; cached input and reasoning are subsets, never additional tokens. These receipts do not establish billing or a causal per-request token effect.

## Two completed-correct pairs

Every comparison below uses Starlette/r1 and Starlette/r2. Changes use matched sums; median pair changes remain separately available in the report.

| Workflow | Gross tokens | Uncached input | Agent seconds | Full-trial seconds |
| --- | ---: | ---: | ---: | ---: |
| baseline | 646,518 | 101,429 | 716.15 | 719.04 |
| fast MCP | 711,430 | 132,666 | 662.25 | 706.49 |
| Qwen MCP | 843,179 | 104,406 | 662.58 | 711.60 |

Against baseline, fast MCP uses 10.04% more gross tokens and 30.80% more uncached input; agent time falls 7.53%, but full trial time falls 1.75%. Qwen uses 30.42% more gross tokens and 2.94% more uncached input; agent time falls 7.48%, but full trial time falls 1.03%. Qwen versus fast MCP is 18.52% more gross,21.30% less uncached input, 0.05% more agent time and 0.72% more full trial time.

Cache accounting explains the apparent token contradiction arithmetically. Qwen versus fast MCP adds 131,749 gross tokens: +159,360 cached input, -28,260 uncached input and +649 output. Against baseline, its 196,661 extra gross tokens comprise 192,896 cached input, 2,977 uncached input and 788 output. Cache reuse, hosted variation and different search/test workflows remain confounds; these numbers do not isolate why those inputs were cached.

## Cohort and timing limits

| Matched normal-completion comparison | Pairs | Gross change | Uncached-input change | Agent-time change | Full-trial change |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline → fast MCP | 4 | +8.39% | +7.93% | -6.01% | -1.51% |
| baseline → Qwen MCP | 5 | +9.58% | +5.94% | -2.37% | +1.78% |
| fast MCP → Qwen MCP | 5 | +1.67% | +4.77% | +0.22% | +0.60% |

Baseline → fast normal pairs are Click/r2, Requests/r1 and Starlette/r1+r2. Baseline → Qwen excludes Click/r1; fast → Qwen excludes Requests/r2. In all-observed accounting, baseline → fast has six time pairs but five known-usage pairs: gross increases 9.73% and agent time 1.91%, while retaining the provider-error and timeout attempts. The full fast-MCP token total is unknown, even though five measured attempts sum to 2,603,720 tokens. Exact members for every metric appear in the report.

Service indexing/startup/warmup occurs before the agent timer; cleanup and grading add to full trial time. Baseline service setup is unmeasured, rather than a measured zero. Qwen made 19 actual MCP searches and 456 real singleton forwards, plus six separate warmups. Fast MCP made 23 searches; baselines made none. Concurrent tool intervals and hosted waits prevent a simple subtraction of local scorer time from overall completion time.

## What source review explained

Requests: all six pass the original 13 checks and upstream compatibility, yet five miss frozen temporal edges involving suspended buffered/cached iterators, cleanup errors or hook replacement timing. Only the timed-out fast-MCP snapshot passes the stricter gate. A separate redirect-history hook probe exposes a shared reference/checker coverage gap; frozen grades stay unchanged.

Click: all six pass the original 9 checks but fail the strict 14 gate. A later suppressing resource permits an earlier atomic file to commit after callback failure; trials 14/15 also lose the primary error during independent cleanup. An accepted supplemental older-suppressor defect affects all six and the repaired reference. Passing 1,224 authored parameterized cases did not cover those compositions.

Starlette: all six pass the strict 10 checks and upstream 269 tests with 2 skips each. The original prospective nine-case probe retains 52 passes and 2 failures: trial 18 is 7/9. Scope review rejects those two unrequested single-frame replay/pre-app disconnect assumptions. All 42 executions of the seven supported prospective cases pass. Separately labeled post-output chunk-neutral diagnostics pass 12 candidate and 2 reference executions. They do not replace the raw probe or alter formal grades; no additional contract defect was demonstrated in these reviewed paths.

Relevant source was available despite some errors: trial 03 received the complete Response.iter_lines span, and Click 04/05/14 received complete Context.with_resource/Context.__exit__ spans. This points to contract reasoning and compositional tests as useful follow-ups, while source exposure alone does not prove correct use. [Source bindings and independent arithmetic](../qwen-coding-final-synthesis-2026-10-14/source-bindings-final.json) preserve the evidence. Three reused tasks and two repetitions per condition support these scoped observations, not general accuracy or causal token savings.
