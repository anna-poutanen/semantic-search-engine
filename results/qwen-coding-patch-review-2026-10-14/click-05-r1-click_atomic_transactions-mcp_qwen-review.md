Trial 05 completed normally but failed the frozen feature gate in the same
three eager/lazy/default-lazy variants as trial 04: an independently registered
suppressing resource makes an atomic file commit after a failed callback.
The original nine checks and restored upstream compatibility suite passed.
All 138 authored test cases passed independently; that count includes
parameter expansions.

The complete patch changes four production files and adds two test files. It
changes no original tests, configuration, or other files. Compared with trial
04, it also uses resource registration for ordinary lazy files and restricts
transaction-specific lazy cleanup to an actual atomic wrapper. The reviewed
ordinary-file and borrowed-standard-stream controls pass.

The suppression defect remains because `with_resource` delegates directly to
`ExitStack.enter_context`. Passing the callback exception into the stack is
insufficient: a later resource can clear it before the earlier atomic file's
exit method chooses whether to commit. Preserving the original outer callback
exception after cleanup does not repair the already committed destination.
The added resource tests omit this composition with a separate suppressor.

The unchanged prospective supplemental probes passed 22 of 24 cases. Both
failures reproduce the accepted clean-commit defect: a separately registered
older suppressor hides a genuine `FileNotFoundError` during automatic atomic
commit, allowing CLI exit zero. This covers an unhandled commit during context
cleanup, rather than a caller manually catching an explicit close exception.
The eager/lazy variants represent one distinct added behavior, while the
frozen fourteen-case grade remains unchanged.

The [inventory](click-05-r1-click_atomic_transactions-mcp_qwen-inventory.json),
[CPU receipts](click-05-r1-click_atomic_transactions-mcp_qwen-cpu-review.json),
[probe outputs](click-05-r1-click_atomic_transactions-mcp_qwen-probes.json),
and [structured findings](click-05-r1-click_atomic_transactions-mcp_qwen-review.json)
bind the exact artifacts. Candidate sources and the frozen result remain
unchanged. No hosted or model inference calls were performed.
