# Starlette terminal patch review

All six candidates reproduce the unchanged ten-case strict grade and the original upstream compatibility suite (269 passed, two skipped per candidate). Their new regression files produce 881 passing parameterized cases. No original tests/configuration were edited, and reconstructed production and changed-file bytes match every terminal source copy. No additional contract defect was demonstrated in the examined paths.

The original nine-case supplemental probe was frozen before Starlette outputs. Its raw outcomes are preserved: five candidates pass nine of nine; trial18 passes seven of nine and fails two. Scope review found both failures depend on unrequested assumptions: accepted chunks must be coalesced into a single frame, or the middleware must raise ClientDisconnect before calling an app that does not read the body. Trial18 replays original messages and preserves ClientDisconnect when its downstream Request.body consumes them.

Seven supported prospective cases pass for all six candidates (42 case executions). Two separately labeled, chunk-neutral diagnostics were authored after outputs and pass for all six candidates (12 executions) and the original reference (two executions). These follow-ups do not replace the frozen probe, redefine formal correctness, or become prospective grading.

| Run | Original supplemental | Strict | Upstream | New authored cases |
|---|---:|---:|---:|---:|
| 07-r1-starlette_body_limits-mcp_qwen | 9/9 | 10/10 | 269 passed, 2 skipped | 143 |
| 08-r1-starlette_body_limits-baseline | 9/9 | 10/10 | 269 passed, 2 skipped | 150 |
| 09-r1-starlette_body_limits-mcp_fast | 9/9 | 10/10 | 269 passed, 2 skipped | 148 |
| 16-r2-starlette_body_limits-mcp_fast | 9/9 | 10/10 | 269 passed, 2 skipped | 143 |
| 17-r2-starlette_body_limits-baseline | 9/9 | 10/10 | 269 passed, 2 skipped | 126 |
| 18-r2-starlette_body_limits-mcp_qwen | 7/9 | 10/10 | 269 passed, 2 skipped | 171 |

Each authored-test suite also ran after restoring only original production. Missing public feature imports cause two collection errors per candidate; those are not hundreds of independent behavioral failures.

The initial trial07 evaluator reconstruction failure and known-warning import blockage remain preserved with exact source/result/log hashes. The corrected fresh repositories verify both changed-file inventory and byte hashes before checks. The frozen control publication correction and oracle-scope decision have separate receipts.

The complete file, result, source, control and plan bindings are in [starlette-review-summary.json](starlette-review-summary.json). Passing these scoped cases does not establish every possible Starlette behavior or a causal MCP benefit.
