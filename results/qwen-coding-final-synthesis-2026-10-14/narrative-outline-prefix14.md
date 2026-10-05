# Prospective final coding report outline

This outline uses the certified first 14 of 18 attempts. It is written after those outcomes were observed; it is not a preregistration for the entire study. The remaining Click fast-MCP and three Starlette repetition-two outcomes have not been inspected here. Publish a final conclusion only after the 18-attempt certificate, driver completion witness and remaining independent patch reviews pass.

Lead the final report with task completion and correctness. Token and time differences between incorrect implementations do not establish better efficiency at completing a correct task. Retrieval improved independently, but the coding comparison measures the whole hosted workflow, including its chosen searches, source reads, implementation and tests.

## Preserve the accounting distinctions

At prefix14, baseline has 5 recorded attempts, 4 normal completions and 1 completed-correct result. Fast MCP has 4 recorded, 3 normal and 1 completed-correct result; its second correct snapshot is the Requests timeout. Qwen MCP has 5 recorded, 5 normal and 1 completed-correct result. These are partial counts against six planned attempts per condition.

Trial06 retained a provider 503 reconnect error, despite CLI exit0 and a completed-turn event. Keep its actual usage in all-observed accounting and exclude it from normal-completion comparisons. Trial11 hit the 900-second limit, exited -15 and produced no completed-turn usage. Its final snapshot passed the frozen strict and compatibility gates, but it is not completed-correct and its token usage is unknown. Never use zero for that unknown or report a full fast-MCP workload token total.

Gross tokens equal reported input plus output. Cached input and cache writes are input subsets; reasoning is an output subset. Uncached input equals input minus cached input. Compare gross, uncached input, output and cached input separately. None of these receipts establishes per-request billing or the causal token effect of a tool response.

Agent time includes hosted waits and tools from CLI launch through exit. Service setup includes indexing, model startup and warmup before that timer. Baseline service setup is unmeasured, rather than a measured zero. Full-trial time includes setup, cleanup and grading. Preserve qualified-clock status and name each metric's eligible task/repetition pairs.

## Interim matched comparisons to replace with the final certificate

These aggregate changes are computed from matched sums, not differences between condition medians. All rows below are interim. The exact sums, median pair changes and metric-specific members are retained in observations-prefix14.json.

| Matched normal-completion cohort | Members | Gross change | Uncached-input change | Agent-time change | Full-trial-time change |
| --- | --- | --- | --- | --- | --- |
| baseline → fast MCP | Requests/r1; Starlette/r1 | +5.51% | +2.23% | -3.92% | -0.08% |
| baseline → Qwen MCP | Click/r2; Requests/r1+r2; Starlette/r1 | +6.23% | -0.31% | -4.21% | -0.41% |
| fast MCP → Qwen MCP | Click/r1; Requests/r1; Starlette/r1 | -4.28% | +6.92% | -4.65% | -4.13% |

Only Starlette/r1 currently supplies a completed-correct pair for all three comparisons. Against baseline, Qwen used 26.52% more gross tokens, 25.22% less uncached input, 24.37% less agent time and 17.78% less full-trial time. Fast MCP used 24.55% more gross tokens, 4.16% more uncached input, 22.63% less agent time and 16.70% less full-trial time. Qwen versus fast MCP is +1.58% gross, -28.21% uncached input, -2.24% agent time and -1.29% full-trial time. These are single-pair observations, not general wins.

The gross/uncached apparent contradiction has an exact arithmetic explanation. In Starlette/r1, Qwen versus baseline adds 87,255 gross tokens: +102,528 cached input, -15,392 uncached input and +119 output. That does not explain why the provider's cache behaved that way. In the three normal fast→Qwen pairs, gross falls 79,074 while uncached input rises 11,985; cached input falls 89,088 and output falls 1,971. A lower gross count is therefore not automatically a lower uncached-input count.

Keep all-observed pairs separately. At prefix14, baseline→fast has four time pairs but three known-usage pairs, because Requests/r2 timed out with unknown usage. It also includes the non-normal Click/r1 baseline. Removing those attempts changes the cohort and the headline; do not mix all-observed time with normal-only token results without naming both populations.

## Explain correctness using independent source and test reviews

Requests: all six candidates passed the original13 checks and selected upstream compatibility, yet five fail at least one frozen edge case. The normal baseline and Qwen runs have no completed-correct Requests result. The failures involve a resumed buffered line iterator, an iterator created before a lower cached limit breaches, cleanup masking the primary size error, and later hooks consuming a replacement before its budget is bound. The unchanged supplemental redirect-history hook probe exposes one additional shared reference/checker coverage gap in five candidates. Report that separately without changing the frozen18 grades.

Click: the five reviewed candidates04/05/06/13/14 all fail resource-composition requirements. In four, three eager/lazy/default-lazy cases share one mechanism: a later suppressing resource clears the error before an earlier atomic file exits, so the destination commits after a failed callback. Trial14 also lets separately registered cleanup failures replace the callback primary. The accepted supplemental older-suppressor probe shows automatic commit failures can be hidden and yield CLI exit0; it is outside the frozen14 grade. Await trial15 before making an all-six statement.

Authored passing tests did not resolve those failures. Reviewed Click suites pass 183/138/263/174/235 parameterized cases, but omit the relevant combinations or check the outer exception without checking the destination. Requests/r2 Qwen passes100 authored cases while omitting the frozen temporal edges; baseline passes120 but tests a newly created line iterator instead of resuming an already buffered one. Parameterized case totals are not counts of independently guaranteed behaviors.

Starlette/r1: all three conditions pass the frozen10 checks, nine independent pre-output supplemental checks and restored upstream269 tests with2 skips. Source review found no additional demonstrated defect in the exercised paths. Authored tests pass143/150/148 parameterized cases; missing-feature collection errors on base production do not prove each case independently detects a regression. Await all three repetition-two reviews.

## Source exposure is a clue, not causal proof

The certified first14 attempts contain30 successful MCP searches:15 fast and15 Qwen. Qwen executes360 real singleton forwards, with five separate worker warmups. Baselines execute no MCP calls. Per-call queries, effective settings, literal span hashes, clipping, source characters, payload tokens and backend elapsed intervals are bound in source-exposure-prefix14.json.

Relevant implementations were sometimes delivered in full even when the patch remained wrong. Requests/r1 Qwen receives the complete Response.iter_lines span, then still fails the buffered-line case. Click04/05/14 receive complete Context.with_resource and Context.__exit__ spans, yet still miss cross-resource suppression behavior. This supports examining contract reasoning and compositional test coverage alongside retrieval. It does not prove the agent attended to those snippets or establish that alternative retrieval would produce the same result.

Fast and Qwen agents choose different queries and call counts. More or fewer payload tokens, backend intervals, ordinary commands or authored tests cannot be converted into a per-request hosted-token delta. Concurrent backend intervals may overlap, and hosted waiting/reasoning/cache behavior is not independently isolated by this two-repetition study.

## Final publication checklist

Use the final immutable as-of certificate and reporter output, not mutable live globals. Include all18 scheduled attempts, each unknown/failure/timeout, normal and completed-correct subsets, actual MCP/native counts with warmup separate, and exact metric-specific pair identities. Preserve the historical prefix14 outline and all original grades. Add the four pending outcomes and remaining source reviews in new final files, without overwriting this outline or retuning the chosen hybrid/K24/B1 condition.

State the practical finding only to the extent supported by the completed-correct pairs. If a faster or lower-token workflow remains incorrect, report that plainly. The study can show where source retrieval helped or failed to translate into a correct patch; it cannot establish general accuracy, causal reranker benefit or paid-token savings from three reused tasks.
