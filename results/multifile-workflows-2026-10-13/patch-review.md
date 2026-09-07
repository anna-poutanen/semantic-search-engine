# Multifile patch review and supplemental correctness findings

Reviewed 16 of 16 scheduled attempts. This is a post-output source and regression review of every retained terminal artifact. It leaves the frozen selected/upstream grades, prompts, run inventory and performance subsets unchanged.

## Confirmed supplemental findings

**6 distinct scoped defects affect 7 reviewed attempts, comprising 12 attempt-level findings. The withheld references fail 9 supplemental probe cases spanning 5 distinct behaviors, showing that the frozen checks do not establish complete contract correctness.**

| Behavior | Affected retained attempts |
|---|---|
| click_atomic_transactions: registered_cleanup_masks_primary_callback | 13-r2-click_atomic_transactions-mcp |
| click_atomic_transactions: suppressing_registered_resource | 05-r1-click_atomic_transactions-baseline, 06-r1-click_atomic_transactions-mcp, 13-r2-click_atomic_transactions-mcp, 14-r2-click_atomic_transactions-baseline |
| requests_response_limit: buffered_iter_lines_after_breach | 03-r1-requests_response_limit-mcp, 04-r1-requests_response_limit-baseline, 11-r2-requests_response_limit-baseline |
| requests_response_limit: cached_iterator_after_lower_limit_breach | 04-r1-requests_response_limit-baseline |
| requests_response_limit: context_manager_cleanup_preserves_size_error | 03-r1-requests_response_limit-mcp, 11-r2-requests_response_limit-baseline |
| requests_response_limit: hook_chain_replacement_before_subsequent_read | 11-r2-requests_response_limit-baseline |

The Requests probes examine suspended buffered line readers after a size breach, already-created cached iterators after a later lower limit breaches the same response, preservation of the primary size error through failing response-context cleanup, and replacement-body reads inside a later response hook before the limit is attached. Passing cleanup diagnostics may use notes, explicit cause or implicit exception context. Click probes examine two combinations: a separately registered truthy suppressing resource makes the atomic destination commit while the CLI still propagates the original callback failure, and a separate registered cleanup callback/resource can replace the primary callback error. These do not imply ordinary callbacks or all cleanup paths fail.

Independent root scope review accepted these behaviors against the existing explicit callback/terminal-read/cleanup/hook requirements. The response oracle was repaired once to accept implicit chaining; v3 then added separately documented live/cached hook variants after terminal11 source review. Click's v2 added separate registered cleanup after13 review; v3 broadened its diagnostic oracle to bounded recursive cause/context/ExceptionGroup chains or notes, still requiring the original outer primary callback exception. Prior scripts/results remain in ignored version archives, and public hashes/reasons/timing are in [supplemental-probe-revisions.json](supplemental-probe-revisions.json). The original neutral cases were unchanged. All applicable terminal candidates and the frozen withheld reference receive the same probes; no failed attempt is dropped or retried because of its result.

A frozen `correct=true` means both selected and restored-original source gates passed. `completed_correct=true` also requires normal completion, exit zero, no timeout and no recorded failure. Neither label is silently redefined by this supplemental review. An attempt can pass both gates and still have one of the demonstrated defects.

## Retained attempts

| Run | Frozen correct | Completed correct | Added tests pass/fail | Findings |
|---|---:|---:|---:|---:|
| 01-r1-requests_ndjson-baseline | true | true | 155/0 | 0 |
| 02-r1-requests_ndjson-mcp | true | true | 170/0 | 0 |
| 03-r1-requests_response_limit-mcp | true | true | 128/0 | 2 |
| 04-r1-requests_response_limit-baseline | true | false | 127/2 | 2 |
| 05-r1-click_atomic_transactions-baseline | true | false | 240/0 | 1 |
| 06-r1-click_atomic_transactions-mcp | true | true | 252/0 | 1 |
| 07-r1-starlette_body_limits-mcp | true | true | 113/0 | 0 |
| 08-r1-starlette_body_limits-baseline | false | false | not available | 0 |
| 09-r2-requests_ndjson-mcp | true | true | 108/0 | 0 |
| 10-r2-requests_ndjson-baseline | true | true | 239/0 | 0 |
| 11-r2-requests_response_limit-baseline | true | false | 116/0 | 3 |
| 12-r2-requests_response_limit-mcp | false | false | not available | 0 |
| 13-r2-click_atomic_transactions-mcp | true | false | 148/0 | 2 |
| 14-r2-click_atomic_transactions-baseline | true | true | 210/0 | 1 |
| 15-r2-starlette_body_limits-baseline | true | true | 167/0 | 0 |
| 16-r2-starlette_body_limits-mcp | true | true | 146/0 | 0 |

## Regression and integrity evidence

Fresh evaluator clones reproduce 2319 passing and 2 failing authored test cases. These are parameterized cases, not that many independent behaviors. 0 attempts changed or removed original tests/configuration. Added tests alone are not classified as weakening. The independent runtime/source-receipt audit is a separate artifact.

Added tests are also exercised after restoring only production source. A missing public feature may cause collection errors, so those outcomes are not counted as hundreds of independently failing behavioral cases. Full results, invocation and hashes are in [patch-review.json](patch-review.json) and `patch-review-checks/`.

Timeouts retain their patches and source grades but are not completed-correct, and absent actual usage is not imputed as zero. No-edit provider-capacity attempts08/12 are distinguished from implementation failure: their absent features fail selected grading while pristine original tests pass. Attempt13 also ends with a provider-capacity error, after retaining a substantive patch. These operational outcomes must remain in the main comparison, with any conditional performance analysis clearly labeled.

## Limits

The supplemental probes were selected after outputs; they cannot replace prospectively frozen primary grading or support a causal claim about MCP by themselves. The four authored tasks and two repetitions are a small sample. Source inspection, original restoration, authored regressions and targeted probes strengthen the evidence while leaving unexamined framework behavior outside the demonstrated scope.
