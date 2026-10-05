# Six Click patch reviews

All six terminal Click patches pass the original9 feature cases and retained upstream compatibility (307 passed,1 skipped), but fail the frozen strict14 checker. Each changes four production files and adds two test files; no original tests or configuration were changed. Sources, public results, original evaluator tests/configuration, and immutable review/probe/control bindings were rechecked.

| Trial | Condition | Frozen failures /14 | Supplemental passes /24 | Authored cases passing | Distinct behaviors |
|---|---|---:|---:|---:|---:|
| [04](click-04-r1-click_atomic_transactions-mcp_fast-review.md) | mcp_fast | 3 | 22 | 183 | 2 |
| [05](click-05-r1-click_atomic_transactions-mcp_qwen-review.md) | mcp_qwen | 3 | 22 | 138 | 2 |
| [06](click-06-r1-click_atomic_transactions-baseline-review.md) | baseline | 3 | 22 | 263 | 2 |
| [13](click-13-r2-click_atomic_transactions-baseline-review.md) | baseline | 3 | 22 | 174 | 2 |
| [14](click-14-r2-click_atomic_transactions-mcp_qwen-review.md) | mcp_qwen | 5 | 16 | 235 | 3 |
| [15](click-15-r2-click_atomic_transactions-mcp_fast-review.md) | mcp_fast | 5 | 16 | 231 | 3 |

Three underlying behaviors explain the variants:

- All six: a later registered suppressor clears the callback error before atomic-file cleanup, incorrectly committing the failed transaction. This is already within the frozen checker.
- Trials14 and15: a separately registered cleanup error replaces the callback primary. This is already within the frozen checker; six supplemental fault-type variants confirm it.
- All six: an older unrelated registered suppressor hides an unhandled clean atomic commit FileNotFoundError, causing CLI success. This is the separately accepted supplemental contract defect, and never changes the frozen grade.

Trials04/05/06/13 have one frozen behavior plus the accepted supplemental behavior; trials14/15 have two frozen behaviors plus that supplemental behavior. Parameter and fault-type variants are not counted as independent defects. The24 unchanged supplemental diagnostics contain zero unknown results for every trial. Passing authored tests count parameterized cases and do not override these failures.

Prospective unchanged controls pass8/24 (pinned base),16/24 (old reference), and22/24 (repaired strict reference). The repaired reference still fails the same two clean-commit supplemental variants, despite passing the frozen14 checker. Thus22/24 is a comparative diagnostic result, not complete correctness.

Trial06 retains a surfaced503 Service Unavailable error and frozen failed:true, alongside terminal completed:true/agent_exit0 and correct:false/completed_correct:false. Its patch and diagnostic results are available and remain incorrect; no diagnostic result is unknown. Service cause and unreported retry/interrupted-request token usage are unknown and are not reconstructed. The source usage scope reports completed-turn totals, with interrupted-turn and per-request usage unknown.

This source review does not qualify overall timing/token/lifecycle metrics; the separate study auditor owns those checks. No model, hosted, installer, or frozen grader calls were made during these reviews. Frozen source/probe/scope/control/result bytes and grades remain unchanged. Trial16 was not accessed.

The [machine-readable summary](click-six-trial-summary.json) binds all six complete reviews, inventory/CPU/probe/test-output receipts, the unchanged controls, scope decision, and the separate trial04 line-location clarification.
