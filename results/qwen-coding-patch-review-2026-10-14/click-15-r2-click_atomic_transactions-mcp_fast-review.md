# Click second fast MCP terminal patch review

Trial `15-r2-click_atomic_transactions-mcp_fast` changes four production files and adds two regression-test files. All six changed files and the complete patch were reviewed. No existing tests or configuration were changed.

The unchanged frozen feature checker passes its original9 cases, but the strict14 set has5 failures: eager, lazy and default-lazy suppression rollback plus independent registered callback/resource cleanup replacing the primary exception. Retained upstream compatibility passes307 tests with1 skip. The added tests pass231 parameterized cases in the evaluator copy; this is not231 distinct behaviors.

`Context.with_resource` delegates directly to `ExitStack.enter_context` (core.py:591). A later truthy suppressor clears the callback error within `ExitStack.__exit__` (core.py:619); the earlier atomic file then commits with delete=False (_compat.py:494). Preserving the outer callback exception does not establish rollback.

Context cleanup only resets its stack in finally (core.py:612); it does not preserve the callback primary if a separate resource or cleanup callback raises. The file-specific handler chains its own abort cleanup error behind the primary (_compat.py:499), but cannot cover independent registered cleanups. Six unchanged supplemental variants confirm this frozen defect: a registered callback raising SystemExit(0) incorrectly yields CLI exit0 after a failing callback, while registered resource KeyboardInterrupt replaces the primary with Abort/SystemExit1. Destinations roll back and contexts restore, but primary identity and cleanup diagnostics are lost. The added context cleanup test uses a clean body and misses this composition.

The unchanged24 supplemental diagnostics pass16 cases with no unknown results. The other two failures are the previously accepted clean-commit defect: an older unrelated registered suppressor hides the real `FileNotFoundError` from atomic `os.replace` (_compat.py:479), causing a zero CLI exit. This concerns unhandled automatic commit during cleanup, not manually caught explicit close errors. The scope decision remains separate and unchanged.

These are three distinct behaviors, with parameter and fault-type variants retained separately. Every diagnostic used the evaluator-only copy. Before-and-after hashes match for recursive production sources, all changed files, original tests/configuration, and the public result. Frozen graders were not rerun or edited, and grades remain unchanged. No model, hosted, installation, or download calls occurred.

Evidence: [inventory](click-15-r2-click_atomic_transactions-mcp_fast-inventory.json), [CPU receipt](click-15-r2-click_atomic_transactions-mcp_fast-cpu-review.json), [24 diagnostic receipts](click-15-r2-click_atomic_transactions-mcp_fast-probes.json), [machine-readable review](click-15-r2-click_atomic_transactions-mcp_fast-review.json), [accepted scope decision](click-scope-decision-clean-commit.json).
