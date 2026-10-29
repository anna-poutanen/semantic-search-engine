# Post hoc diagnosis of remaining multi-file retrieval gaps

This describes the fixed native holdout results. It is **outside the canonical independent audit**, introduces no new candidate or scoring rule, and makes no model, index, retrieval or network calls. TanStack Query is now observed development evidence. Frozen quality/latency gates and outputs remain unchanged.

[Full stage records, OR alternatives and input hashes](posthoc-quality-diagnosis.json) accompany this report. Every evidence group still requires one actual allowed qualified-symbol excerpt containing its whole physical interval with exact evidence bytes/hash. Separate snippets are never combined for credit.

## What remains missing

The first repetition contains33 required groups across12 positives per mode. Candidate and coverage control each deliver19/33 groups in dense and hybrid. Dense completes6/12 positives (2/8 multi-file); hybrid completes5/12 (1/8 multi-file). All four single-file controls complete in both modes. The remaining candidate misses are28 group occurrences across the two modes, not28 independent requirements.

| First missing stage or delivered-prefix issue | Dense candidate | Hybrid candidate | Total |
| --- | ---: | ---: | ---: |
| Covering raw window discarded by canonical representative choice | 2 | 2 | 4 |
| Canonical window retrieved but omitted from final allocation/backfill | 11 | 7 | 18 |
| No full qualified evidence window in raw50 | 1 | 4 | 5 |
| Required window backfilled as a character-clipped prefix | 0 | 1 | 1 |

No missing candidate group is attributed to token clipping. Ordinary whole-window availability is distinguished from actual delivered prefixes; a same-symbol window that lacks the complete interval receives no credit.

## Raw retrieval versus response assembly

Five missing candidate group occurrences have no complete qualified evidence window in raw50: dense lacks `addToEnd`; hybrid lacks `QueryObserver.onQueryUpdate`, `addToEnd`, `addToStart` and `fetchState`. Their implementations exist in the frozen source inventory. These misses cannot be fixed by rearranging only the current complete raw windows.

Four occurrences lose a covering raw window when one canonical representative is chosen: the request-reuse part of `Query.fetch` and the result-promise part of `QueryObserver.createResult`, in both modes. Dense retrieves the necessary windows at ranks48 and25; hybrid at ranks13 and5. The surviving first window belongs to the same qualified implementation but covers another portion. Full canonical identity prevents conflating definitions; it does not guarantee choosing the right part of a long implementation.

Eighteen further occurrences retain a covering canonical window but do not deliver it through the bounded eligible list or backfill. Many correct implementations are ranked below the first eight: for example dense retrieves `Query.#dispatch` at raw18, `QueryObserver.onQueryUpdate` at37, and `addToStart` at41. Shared-caller protection addresses unsafe eviction, but it does not by itself select these later complementary implementations.

## A packing issue that a slot-only diagnosis would miss

For hybrid pending-promise hydration, `Query.fetch` is raw rank7 and in canonical eligible slot8. The shared-flow proposal omits it, then containment/backfill returns that window as a prefix through line488. Its original window spans387–546, and the saved required interval lies beyond the delivered prefix. The final response uses11,986 source characters and3,897 compact tokens; token truncation is false. This is a character-limited backfill prefix, not failed retrieval or a token-limit problem. The coverage control also misses this group with its shorter returned prefix through line470.

## Every missing candidate group by stage

Ranks below refer to complete qualified evidence. `—` means no qualifying excerpt at that stage. Canonical8 is the first eight canonical retrieval representatives, before static dependencies; proposed is the actual shared-flow eligible set before source packing. Detailed records additionally include all canonical candidates, dependency-expanded controls, packed/proposal/reference branches, exact alternatives and partial-window metadata.

| Mode | Query suffix | Group | Raw50 | Canonical8 | Proposed | Delivered | Diagnosis |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| dense | inflight_reuse_and_success_delivery | 1 | 48 | — | — | — | Covering raw window discarded by canonical representative choice |
| dense | inflight_reuse_and_success_delivery | 2 | 31 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| dense | inflight_reuse_and_success_delivery | 3 | 18 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| dense | inflight_reuse_and_success_delivery | 4 | 37 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| dense | last_observer_signal_cancellation | 2 | 35 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| dense | infinite_pages_bounded_both_directions | 3 | — | — | — | — | No full qualified evidence window in raw50 |
| dense | infinite_pages_bounded_both_directions | 4 | 41 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| dense | optimistic_result_and_result_promise | 1 | 16 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| dense | optimistic_result_and_result_promise | 2 | 48 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| dense | optimistic_result_and_result_promise | 3 | 25 | — | — | — | Covering raw window discarded by canonical representative choice |
| dense | hydrate_pending_promise_without_second_query_fn | 2 | 25 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| dense | hydrate_pending_promise_without_second_query_fn | 3 | 39 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| dense | selector_memoization_and_deep_sharing | 1 | 19 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| dense | selector_memoization_and_deep_sharing | 2 | 16 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| hybrid | inflight_reuse_and_success_delivery | 1 | 13 | — | — | — | Covering raw window discarded by canonical representative choice |
| hybrid | inflight_reuse_and_success_delivery | 2 | 10 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| hybrid | inflight_reuse_and_success_delivery | 4 | — | — | — | — | No full qualified evidence window in raw50 |
| hybrid | mutation_scope_serial_continuation | 1 | 15 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| hybrid | mutation_scope_serial_continuation | 3 | 15 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| hybrid | invalidation_and_refetch_filter | 1 | 9 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| hybrid | invalidation_and_refetch_filter | 2 | 24 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| hybrid | infinite_pages_bounded_both_directions | 3 | — | — | — | — | No full qualified evidence window in raw50 |
| hybrid | infinite_pages_bounded_both_directions | 4 | — | — | — | — | No full qualified evidence window in raw50 |
| hybrid | optimistic_result_and_result_promise | 2 | — | — | — | — | No full qualified evidence window in raw50 |
| hybrid | optimistic_result_and_result_promise | 3 | 5 | — | — | — | Covering raw window discarded by canonical representative choice |
| hybrid | hydrate_pending_promise_without_second_query_fn | 2 | 7 | 7 | — | — | Required window backfilled as a character-clipped prefix |
| hybrid | hydrate_pending_promise_without_second_query_fn | 3 | 15 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |
| hybrid | selector_memoization_and_deep_sharing | 1 | 14 | — | — | — | Canonical window retrieved but omitted from final allocation/backfill |

## Bounded next improvement

The next selection experiment should retain alternative raw windows for each canonical definition until packing, rather than irreversibly discarding all but its first scored window. Test a bounded per-definition window portfolio together with query-specific complementary implementation choices under the same eight-result, character and token caps. Four observed missing-group occurrences motivate the window-choice part; eighteen allocation misses require measuring which later definitions enter context. This is a proposed experiment, not evidence that any particular heuristic will complete those cases. The earlier negative window-union study remains relevant and must stay in the comparison.

Freeze one generic label-free rule, replay it against both original controls on the prior365 cases plus this now-observed corpus, and reject any individual evidence loss. Preserve ordinary and backfill prefix checks. Use another independently source-reviewed codebase for a future generalization claim; TanStack can no longer serve as fresh validation.

A separate performance experiment can test returning the existing control immediately when no flow candidate is admitted, avoiding redundant reference packing. The current native run does not measure that optimization. Source quality and latency failures remain separate requirements, and no gate should be weakened to promote this result.

The companion JSON records all missing groups for legacy and coverage control as well as candidate, with source/hash-bound inputs. Its SHA-256 is `365f272c3b88fde0b5dc8d6ff07124bd1080645d22da0323f8c9e30f5054e458`.
