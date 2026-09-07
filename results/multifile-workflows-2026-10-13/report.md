# Repeated multifile coding workflows

16/16 scheduled attempts are finalized; all eight task/repetition pairs remain listed. This report excludes the earlier pilots and the separate transport diagnostic.

Completed-correct means normal agent completion plus the frozen external behavior checks and restored original upstream integration gates; it does not use the agent's test-pass claim.

Supplemental review confirms defects in 7 attempts, including 3 formally completed-correct attempts. The reference implementations share several holes. These findings remain separate from the frozen grades.

The planned gate contains 41 distinct behavioral methods (82 invocations per condition across two repetitions), plus upstream compatibility commands expecting 1090 passed and 29 skipped cases per repetition and condition. Actual outcomes remain reported separately.

Only 3/8 pairs have both normal completion and frozen-gate passes. Their performance comparison selects on outcomes; full-workload token totals remain unavailable when receipts are missing.

Both conditions use `gpt-6.1-sol` with `ultra` reasoning. Differences describe the improved search workflow package under the common frozen runtime.

## All scheduled attempts

| Condition | Finalized | Completed correct | Reported gross tokens (observed/8) | Full eight-attempt gross total | Attempt CLI median s (observed/8) | Service setup median s |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 8/8 | 4/8 | 1,473,069 (4/8) | unavailable | 739.8 (8/8) | 0.0 |
| mcp | 8/8 | 6/8 | 2,726,083 (6/8) | unavailable | 465.0 (8/8) | 18.1 |

Observed subtotals exclude unavailable receipts. A timeout can consume unreported provider tokens, so a missing receipt is never zero cost. Setup is outside agent time; evaluator work and teardown are reported separately in analysis.json. Attempt CLI durations include failed and censored runs, so they are resource time rather than successful completion time.

| Condition | Input | Cached input subset | Uncached input | Cache-write input subset | Output | Reasoning output subset |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 1,423,091 (4/8) | 1,256,832 (4/8) | 166,259 (4/8) | 0 (4/8) | 49,978 (4/8) | 17,245 (4/8) |
| mcp | 2,639,521 (6/8) | 2,338,816 (6/8) | 300,705 (6/8) | 0 (6/8) | 86,562 (6/8) | 30,824 (6/8) |

Cached input and reasoning are subsets of input/output; cache writes are separately shown and never added to gross. Uncached input is input minus cached input when both are known; it is not a billing estimate.

| Condition | Completed commands | Completed search_code | Other MCP calls | Output UTF-8 bytes | Output cl100k proxy | Same-call source containment | Excerpts repeated in later MCP | Excerpts seen in later stdout |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 159 (8/8) | 0 (8/8) | 0 (8/8) | 832,827 (8/8) | 194,359 (8/8) | 0 (8/8) | 0 (8/8) | 0 (8/8) |
| mcp | 128 (8/8) | 28 (8/8) | 0 (8/8) | 879,910 (8/8) | 193,221 (8/8) | 11 (8/8) | 19 (8/8) | 9 (8/8) |

Output and overlap counters are descriptive proxies, not provider billing or evidence of wasted work.

## All eight pairs

Percent changes are MCP versus baseline; negative means lower. Qualified pairs require both normal completion and frozen selected/upstream passes, plus matching fixture/prompt hashes; supplemental defects can still exist.

| Task | Rep | First | Baseline outcome | MCP outcome | Qualified | Gross token change | Agent time change | Output token proxy change |
|---|---:|---|---|---|---|---:|---:|---:|
| requests_ndjson | 1 | baseline | completed_correct | completed_correct | yes | 14.0% | 37.9% | -50.3% |
| requests_ndjson | 2 | mcp | completed_correct | completed_correct | yes | -2.2% | -49.4% | 6.0% |
| requests_response_limit | 1 | mcp | timeout | completed_correct | no | unavailable | -19.9% | 57.7% |
| requests_response_limit | 2 | baseline | timeout | provider_capacity_failure | no | unavailable | -99.4% | -100.0% |
| click_atomic_transactions | 1 | baseline | timeout | completed_correct | no | unavailable | -36.1% | -8.7% |
| click_atomic_transactions | 2 | mcp | completed_correct | provider_capacity_failure | no | unavailable | -29.5% | -5.9% |
| starlette_body_limits | 1 | mcp | provider_capacity_failure | completed_correct | no | unavailable | 1,051.9% | 2,122.8% |
| starlette_body_limits | 2 | baseline | completed_correct | completed_correct | yes | 18.4% | 80.0% | 17.1% |

| Comparison family | Declared pairs | Gross paired sums change (observed pairs) | Median pair gross change | Agent paired sums change (observed pairs) |
|---|---:|---:|---:|---:|
| All attempts | 8 | 9.2% (3) | 14.0% | -27.1% (8) |
| Both completed correct | 3 | 9.2% (3) | 14.0% | -2.2% (3) |

The correctness-qualified family selects on an outcome and can be biased. Missing pairs and nonzero failures stay visible in the all-attempt inventory.

## Attempt outcomes and integrity

| Attempt | Outcome | Gross tokens | Agent s | Commands / MCP completed | HTTP receipts | Original test/config edits | Integrity/data flags |
|---|---|---:|---:|---:|---:|---:|---:|
| requests_ndjson r1 baseline | completed_correct | 335,468 | 402.8 | 18 / 0 | 0 | 0 | 0 |
| requests_ndjson r1 mcp | completed_correct | 382,385 | 555.5 | 34 / 3 | 3 | 0 | 0 |
| requests_response_limit r1 mcp | completed_correct | 738,683 | 721.3 | 18 / 4 | 4 | 0 | 0 |
| requests_response_limit r1 baseline | timeout | unavailable | 900.0 | 30 / 0 | 0 | 0 | 0 |
| click_atomic_transactions r1 baseline | timeout | unavailable | 900.0 | 22 / 0 | 0 | 0 | 0 |
| click_atomic_transactions r1 mcp | completed_correct | 382,096 | 575.5 | 20 / 4 | 4 | 0 | 0 |
| starlette_body_limits r1 mcp | completed_correct | 423,319 | 367.1 | 13 / 5 | 5 | 0 | 0 |
| starlette_body_limits r1 baseline | provider_capacity_failure | unavailable | 31.9 | 5 / 0 | 0 | 0 | 0 |
| requests_ndjson r2 mcp | completed_correct | 400,303 | 370.6 | 18 / 3 | 3 | 0 | 0 |
| requests_ndjson r2 baseline | completed_correct | 409,256 | 732.5 | 21 / 0 | 0 | 0 | 0 |
| requests_response_limit r2 baseline | timeout | unavailable | 900.0 | 20 / 0 | 0 | 0 | 0 |
| requests_response_limit r2 mcp | provider_capacity_failure | unavailable | 5.0 | 0 / 0 | 0 | 0 | 0 |
| click_atomic_transactions r2 mcp | provider_capacity_failure | unavailable | 526.8 | 12 / 5 | 5 | 0 | 0 |
| click_atomic_transactions r2 baseline | completed_correct | 390,992 | 747.1 | 27 / 0 | 0 | 0 | 0 |
| starlette_body_limits r2 baseline | completed_correct | 337,353 | 224.0 | 16 / 0 | 0 | 0 | 0 |
| starlette_body_limits r2 mcp | completed_correct | 399,297 | 403.3 | 13 / 4 | 4 | 0 | 0 |

Recorded problems:

- starlette_body_limits r1 baseline: Selected model is at capacity. Please try a different model.
- requests_response_limit r2 mcp: Reconnecting... 2/5 (unexpected status 503 Service Unavailable: An error occurred while processing your request. You can retry your request, or contact us through our help center at help.openai.com if the error persists.); Selected model is at capacity. Please try a different model.
- click_atomic_transactions r2 mcp: Selected model is at capacity. Please try a different model.

Observed scope clues (heuristic):

- requests_ndjson r1 mcp, item_6: query mentions tests/pytest/fixtures; the index covers `src/requests`. This is not proof of intent, retrieval failure, or a cause of token differences.
- starlette_body_limits r1 mcp, item_6: query mentions tests/pytest/fixtures; the index covers `starlette`. This is not proof of intent, retrieval failure, or a cause of token differences.
- starlette_body_limits r1 mcp, item_7: query mentions tests/pytest/fixtures; the index covers `starlette`. This is not proof of intent, retrieval failure, or a cause of token differences.
- requests_ndjson r2 mcp, item_5: query mentions tests/pytest/fixtures; the index covers `src/requests`. This is not proof of intent, retrieval failure, or a cause of token differences.

Separate qualitative patch reviews: 16/16 scheduled attempts reviewed; status `complete`, 0 unreviewed.

**Confirmed supplemental defects: 12 issue instances across 7 attempts, including 3 formally completed-correct attempts.** Some probes also fail the withheld references. Frozen green gates and qualified comparisons therefore do not establish complete feature correctness.

- requests_ndjson r1 baseline: No material contract defect found. Three coordinated source edits implement incremental NDJSON with UTF-8/BOM handling and contextual errors. New tests cover chunk boundaries, caching, argument validation, malformed late lines and real chunked/gzip HTTP consumption. [Review artifact](patch-review.json).
- requests_ndjson r1 mcp: No material contract defect found. Three coordinated source edits provide lazy UTF-8 NDJSON records and contextual errors. Two new test files cover incremental chunks, long records, final CR bytes, BOM placement, cached replay, decoder value semantics and real gzip/chunked HTTP. [Review artifact](patch-review.json).
- requests_response_limit r1 mcp: Two supplemental prompt-contract defects confirmed despite both frozen gates passing. Buffered iter_lines can deliver an old line after another read breaches the budget; a response context can mask ResponseTooLarge with a second failing close. The withheld reference fails both same probes, exposing gaps in the selected checker. The remaining cumulative-budget, redirect, hook, decompression and public API implementation passed its selected/original suites and128 authored tests. [Review artifact](patch-review.json).
- requests_response_limit r1 baseline: Retained timed-out attempt passes both frozen source grades, but completed_correct=false and reported usage is unavailable. Its two new test files contain129 cases:127 pass and2 fail on the terminal patch. Supplemental probes confirm buffered-line and already-created cached-iterator terminal failures; response-context cleanup preservation passes. All original tests and configuration remain unchanged. [Review artifact](patch-review.json).
- click_atomic_transactions r1 baseline: Four coordinated source edits and240 authored tests pass both frozen source grades; agent nonetheless timed out at900seconds without final message or reported usage. A supplemental public-resource interaction is confirmed: registering a contextlib.suppress resource makes the atomic file commit even though the callback exception remains primary and CLI exits1. Withheld reference shows the same behavior; independent root scope review accepts the narrow interaction as a failed-callback rollback violation. [Review artifact](patch-review.json).
- click_atomic_transactions r1 mcp: Four coordinated source edits implement direct/lazy/CLI atomic transactions, public zero-code exit success, cleanup diagnostics and context-stack restoration. Both frozen source grades and252 authored tests pass; normal completion and reported usage are present. The fixed supplemental suppressing-resource probe nevertheless confirms the same rare failed-callback commit as05 and the withheld reference. [Review artifact](patch-review.json).
- starlette_body_limits r1 mcp: No material prompt-contract defect found. Three source edits add observed-byte limits to body caching, a public413 error with first-crossing counts, and middleware that buffers bounded HTTP bodies before downstream invocation, replays accepted bytes then delegates receives, and bypasses disabled/non-HTTP requests.113 new cases include direct receive streams and original HTTPTestClient integration across asyncio/Trio. [Review artifact](patch-review.json).
- starlette_body_limits r1 baseline: Attempt ended with agent exit1 after about32seconds and made no source/test changes. Selected feature checks fail because the new API, exception and middleware are absent; original compatibility checks pass on pristine upstream. No patch or authored test exists to review, no final claim or actual terminal usage is available. [Review artifact](patch-review.json).
- requests_ndjson r2 mcp: No material prompt-contract defect found. Three source edits add lazy physical-line NDJSON decoding with UTF-8/BOM/delimiter handling, immediate argument validation and contextual public errors; ordinary response methods retain source implementation. Two new regression files cover chunks, Unicode, cached reads, malformed late records, real gzip/chunked HTTP and a first record available before the server finishes its body. [Review artifact](patch-review.json).
- requests_ndjson r2 baseline: No material prompt-contract defect found. Three coordinated source edits implement lazy byte-line splitting and UTF-8 JSON decoding with public contextual errors; validation precedes iteration, and cached response consumers remain unchanged. A new239-case file exercises decoder/value semantics, physical bytes, boundaries, gzip HTTP and a streamed record before the remaining response arrives. [Review artifact](patch-review.json).
- requests_response_limit r2 baseline: Retained900-second timeout passes frozen13+257/13 source gates and116 authored cases; no finalclaim or actual terminal usage. Fixed v2 probes confirm the shared buffered-line and response-context cleanup defects, while cached iterator terminal checking passes. Newly selected v3 hook-chain variants additionally deliver oversized replacement bytes to a later hook before budget attachment; independent root scope review accepts that added case as an explicit replacement-response budget violation. [Review artifact](patch-review.json).
- requests_response_limit r2 mcp: Provider-capacity failure exited1 after5seconds without source or test changes, finalclaim or actual terminal usage. Selected feature grading fails on absent new API, while original compatibility passes pristine upstream. [Review artifact](patch-review.json).
- click_atomic_transactions r2 mcp: Four coordinated source edits and148 authored cases pass both frozen source grades. Agent exits1 with provider-capacity failure after retaining this patch, without finalclaim/usage; completed_correct=false. The previously fixed suppressing-resource probe fails. Separate new callback/resource cleanup probes confirm generic Context cleanup masks primary callback exceptions; independent root scope review accepts the added behavior as an explicit primary-callback preservation violation. [Review artifact](patch-review.json).
- click_atomic_transactions r2 baseline: Four coordinated source edits and210 authored cases pass frozen9+307/1 gates with normal completion and actual usage. Generic context cleanup preserves callback exceptions with diagnostics; that fixed supplemental case passes. The same previously selected truthy suppressing-resource interaction still commits a failed command output, despite CLIexit1 and preserved primarycallback. [Review artifact](patch-review.json).
- starlette_body_limits r2 baseline: No material prompt-contract defect found. Shared validation, public413 exception, bounded/cached body helper and message-preserving buffering middleware span four production files. Middleware checks actual bytes before downstream invocation, preserves accepted message bytes and disconnect for downstream receive, and bypasses disabled/non-HTTP scopes.167 new cases and frozen source grades pass with normal completion. [Review artifact](patch-review.json).
- starlette_body_limits r2 mcp: No material prompt-contract defect found. Public limit error, bounded/cached Request.body and HTTP middleware span three source files. Middleware uses the bounded helper, rejects before downstream invocation, coalesces accepted body bytes for one replay then delegates receives, and preserves disconnected-body error propagation and disabled/non-HTTP bypass.146 new cases and both frozen source gates pass with normal completion and reportedusage. [Review artifact](patch-review.json).

These source reviews supplement the frozen gates and are not substituted for their outcomes or used to change the completed-correct denominator.

Ordinary selected-check or compatibility failures are outcome failures. Integrity/data flags identify inconsistent claims, count declarations, or artifact provenance; a failed patch is not by itself a data error. Original upstream tests/configuration were restored for independent compatibility checks. Edits are review flags, not automatic proof of weakening; newly added regression tests remain in candidate patches. The JSON includes exact selected-check outcomes, frozen versus observed upstream counts, receipt/source validation, hash checks, call settings, changed files, and unavailable measurements.

## Measurement limits

A timeout's CLI duration measures time spent up to the cutoff. Successful completion time is unknown beyond that limit; it is not a completed task in exactly 900 seconds. Timeout usage without a provider receipt stays unavailable.

Published stdout and MCP text produce UTF-8 byte and cl100k token proxies. They measure received strings, not provider billing or exact model-visible history. The history cap can affect what reaches the model; no token-saving claim follows from output bytes alone.

Source overlap and repeated context are descriptive diagnostics: same-call containment and exact excerpts in later same-path results/stdout. Edits, different questions, or verification can justify repetition, so these are not labeled waste or paid savings.

CLI start/completion events can arrive in a batch. Their observer reception spans do not measure exact tool execution, queue delay, provider phases, or thinking time. HTTP backend calls have independent timers; snapshot instrumentation is separately measured and contributes to observed workflow time. Provider usage per underlying model response is unavailable, and no time subtraction is used to attribute model thinking.

Four authored features in existing development repositories and two repetitions give a small exploratory sample. Selected behavioral/original integration tests do not prove all library behavior. Conditions are unblinded and use conditional tool guidance; provider/filesystem caches and observation overhead remain. This compares a workflow package and does not isolate guidance, representation, runtime, history caps, or environment effects, establish general savings, or establish the project's 30% target.

Reproduction: `scripts/report_multifile_workflows.py --study <study-directory>` reads saved artifacts only. Ignored raw logs optionally validate exact MCP-to-HTTP linkage; they are not published by the report generator.
