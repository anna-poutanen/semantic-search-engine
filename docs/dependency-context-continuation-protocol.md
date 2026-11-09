# Timing-envelope correction to the dependency development harness

This is a post-outcome harness correction, not a new selector. The original
[protocol](dependency-context-protocol.md), source freeze
`82ad69cccfcf41acfbd97962c406861043b23c44`, complete input freeze
`8b17573decb0e1ae306c3eb375d4fa0a1b7d1fa3`, and failed run remain unchanged.
The first attempt stopped after 396 stored outcomes at the next operand,
`record_247401d37728355142117752f9c62f3049a2627bf12ed7f49a63f960ebe1e15a`.
Its complete response rows and compact payload match coverage exactly. Only
`timings.packaging_s` and `timings.total_s` differ between the original native
request's captured baseline and final response snapshots. The first attempt,
traceback and failure receipt were committed and
pushed in `d16f876d29ec821226d595512253c3b4152b6c06` before this correction.
That immutable commit binds the preserved artifacts below (paths relative to
`results/dependency-context-development-2026-11-09/`):

| Artifact | SHA-256 |
| --- | --- |
| `replay/run-start.json` | `0cd61b8a4d1cd73d20d0dad418e3030599fe501f479279b4bb7368769901dd3d` |
| `replay/graphs.jsonl.gz` | `47a7c91beb2420dbbc7fc2053612f1189b84edb414cacb068388782e376e28c9` |
| `replay/outcomes.jsonl.gz` | `ef9102ae14f8673c001621480a7981eecf34b620a2ebbe236592f9d6448ddb41` |
| `replay/failure.json` | `5e41024b6eb35115e95f86fb22136c4ef3aff068391a18870960dbf444e96f22` |
| `replay-failure.log` | `74605cd7dbe277f63f30272e6104e155eeecd8438943ae2e71a4c227058e981e` |

The certificate proves that inference cannot change the delivered context. It
does not imply that baseline and final snapshots have equal stopwatch values.
Correct only the producer and independent auditor's certificate
comparison: permit differing top-level `timings` dictionaries, requiring matching
presence and finite literal JSON, exact equality of every other full response
field, and exact compact serialized bytes. Never exclude `indexstats`, nested
timings, rows, scores, source, provenance, budgets or token counts. The actual
terminal response must remain one exact complete saved operand, including its
original timings. Report timing-only exceptions explicitly in the audit.

The annotated dependency resolver, pair guard, structural certificate, saved
proposals, quality metrics, sample and qualification thresholds remain byte
unchanged. No new graph, guard or quality rule is chosen to improve an observed
outcome. Test the correction on invented timing envelopes and adversarial
non-timing changes, then commit and push the corrected source and a new complete
input/execution freeze before continuing. Use an exclusive new attempt under
`results/dependency-context-continuation-2026-11-09/`; do not overwrite or resume
the failed first attempt. Verify the five prepared data artifacts are identical
to the original snapshot; only the source-binding plan/freeze may differ.

Repeat all 477 decisions and the independent audit, with quality counted on the
same 421 occurrences / 215 distinct units. All existing datasets remain observed
development evidence. A harness pass remains separate from the unchanged
individual-loss and incremental-gain quality gate. The provider's paired CPU
comparison retains its original source freeze and measurement plan. No native,
fresh-codebase, production-default, release or coding-token claim follows.
