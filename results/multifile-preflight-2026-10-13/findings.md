# Independent multifile preflight

CPU-only checks reconstructed each pristine pinned checkout from Git and applied only the ignored reference patch in evaluator copies. No model or GPU calls were made.

| Task | Reference methods | Partial repairs rejected | Original baseline/reference suites | Test/config bytes unchanged |
| --- | ---: | ---: | --- | --- |
| requests_ndjson | 9 | 6/6 | baseline: 257 passed, 13 skipped, 6 warnings in 0.76s; reference: 257 passed, 13 skipped, 6 warnings in 0.75s | True |
| requests_response_limit | 13 | 8/8 | baseline: 257 passed, 13 skipped, 6 warnings in 0.76s; reference: 257 passed, 13 skipped, 6 warnings in 0.75s | True |
| click_atomic_transactions | 9 | 8/8 | baseline: 307 passed, 1 skipped in 0.34s; reference: 307 passed, 1 skipped in 0.31s | True |
| starlette_body_limits | 10 | 6/6 | baseline: 269 passed, 2 skipped in 0.40s; reference: 269 passed, 2 skipped in 0.42s | True |

Single-file mutants apply exactly one reference production file to pristine upstream. Omit-layer mutants restore exactly one changed file to upstream, deleting a newly added module when appropriate. Rejections include incomplete imports as well as behavioral failures; detailed categories and original transcripts are retained in audit.json and logs.

The baseline gate failures are expected missing-feature outcomes. This preflight establishes the selected contract and original-suite gates; it is not a proof of all library behavior or an independent repository holdout.

All preflight gates passed: **True**. Frozen task assets were stable during audit: **True**.

Independent semantic probes rejected five incorrect behaviors: eager NDJSON buffering, response counters reset per iterator, an old iterator reading after another iterator crossed the budget, Click dropping exception context, and eager unlimited middleware buffering. Three valid alternatives passed: multi-chunk Starlette replay, a different Click atomic temporary prefix, and os.remove-based abort deletion.

The 28 layer-mutant rejections comprise 20 behavioral failures and eight incomplete-import failures. Those categories remain separate in the audit. Original compatibility suites had exactly the frozen pass/skip counts and zero failures, errors, xfails, xpasses, or deselections on both pristine and reference source. All evaluated package imports came from the selected fixture; all original test/config bytes remained unchanged.

Review corrected four issues before freezing: resettable response-wide budgets (including suspended iterator reads), an overstrict replay segmentation oracle, missing evidence that unlimited middleware bypasses buffering, and implementation-specific Click abort fault injection. Findings and their resolved evidence remain in `prefreeze-findings.json`; final judgments and limitations are in `review.json`. There are no remaining preflight blockers.
