# Missing urllib3 evidence diagnosis

All 24 missing first-repetition group/arm/mode records are accounted for across eight distinct query/group units. Baseline and candidate have identical final evidence ranks in both retrieval modes. This is a post-outcome diagnosis; urllib3 is now observed development evidence.

Dense independently reproduces 8/12 complete positives, 4/8 complete multi-file queries and 17/22 groups in each arm. Hybrid reproduces 7/12, 4/8 and 15/22 in each arm. The 24 missing arm/mode records represent 12 distinct query/group/mode cases, 8 distinct query/group units and 6 queries; duplicated arm/mode rows are not additional independent queries.

| Earliest exclusion | Group/arm/mode records |
|---|---:|
| not_in_raw50_or_dependency_expansion | 2 |
| display_label_window_dedup | 1 |
| definition_window_dedup | 1 |
| eligible_rank_cap | 14 |
| character_packing | 2 |
| dependency_allocation_displacement | 4 |
| token_packing | 0 |
| postprocess | 0 |

The combined dedup count is two: the same later `HTTPConnectionPool.urlopen` window is suppressed by legacy display dedup and canonical definition dedup. Both keep the earlier window ending at line 751, which misses the required call at 759–761. No missing group first disappears during token packing or postprocessing.

Stage ranks below are one-based; `—` means no single full-evidence window. Raw, dedup, cap8 and eligible use complete source windows. Character, pre and final use literal delivered prefixes. Cap8 is the deduplicated prefix before dependency allocation; an available cap8 hit absent from eligible was displaced by dependency allocation.

| Mode | Arm | Query / group | Required literal span | Raw50 | Dedup | Cap8 | Eligible | Char | Pre | Final | Earliest exclusion |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| dense | baseline | urllib3_elapsed_total_budget_to_socket_read / g1 | src/urllib3/connectionpool.py:457–459 | 50 | 50 | — | — | — | — | — | eligible_rank_cap |
| dense | candidate | urllib3_elapsed_total_budget_to_socket_read / g1 | src/urllib3/connectionpool.py:457–459 | 50 | 50 | — | — | — | — | — | eligible_rank_cap |
| dense | baseline | urllib3_elapsed_total_budget_to_socket_read / g3 | src/urllib3/connectionpool.py:517–534 | 50 | 50 | — | — | — | — | — | eligible_rank_cap |
| dense | candidate | urllib3_elapsed_total_budget_to_socket_read / g3 | src/urllib3/connectionpool.py:517–534 | 50 | 50 | — | — | — | — | — | eligible_rank_cap |
| dense | baseline | urllib3_file_body_position_before_repeated_send / g1 | src/urllib3/connectionpool.py:759–761 | — | — | — | — | — | — | — | not_in_raw50_or_dependency_expansion |
| dense | candidate | urllib3_file_body_position_before_repeated_send / g1 | src/urllib3/connectionpool.py:759–761 | — | — | — | — | — | — | — | not_in_raw50_or_dependency_expansion |
| dense | baseline | urllib3_redirect_origin_check_and_header_removal / g1 | src/urllib3/connectionpool.py:575–590 | 6 | 6 | 6 | — | — | — | — | dependency_allocation_displacement |
| dense | candidate | urllib3_redirect_origin_check_and_header_removal / g1 | src/urllib3/connectionpool.py:575–590 | 6 | 6 | 6 | — | — | — | — | dependency_allocation_displacement |
| dense | baseline | urllib3_response_status_retry_and_drain / g2 | src/urllib3/connectionpool.py:928–947 | 15 | 13 | — | — | — | — | — | eligible_rank_cap |
| dense | candidate | urllib3_response_status_retry_and_drain / g2 | src/urllib3/connectionpool.py:928–947 | 15 | 15 | — | — | — | — | — | eligible_rank_cap |
| hybrid | baseline | urllib3_bounded_read_across_queued_chunks / g1 | src/urllib3/response.py:289–309 | 8 | 8 | 8 | — | — | — | — | dependency_allocation_displacement |
| hybrid | candidate | urllib3_bounded_read_across_queued_chunks / g1 | src/urllib3/response.py:289–309 | 8 | 8 | 8 | — | — | — | — | dependency_allocation_displacement |
| hybrid | baseline | urllib3_elapsed_total_budget_to_socket_read / g1 | src/urllib3/connectionpool.py:457–459 | 11 | 11 | — | — | — | — | — | eligible_rank_cap |
| hybrid | candidate | urllib3_elapsed_total_budget_to_socket_read / g1 | src/urllib3/connectionpool.py:457–459 | 11 | 11 | — | — | — | — | — | eligible_rank_cap |
| hybrid | baseline | urllib3_elapsed_total_budget_to_socket_read / g3 | src/urllib3/connectionpool.py:517–534 | 11 | 11 | — | — | — | — | — | eligible_rank_cap |
| hybrid | candidate | urllib3_elapsed_total_budget_to_socket_read / g3 | src/urllib3/connectionpool.py:517–534 | 11 | 11 | — | — | — | — | — | eligible_rank_cap |
| hybrid | baseline | urllib3_file_body_position_before_repeated_send / g1 | src/urllib3/connectionpool.py:759–761 | 22 | — | — | — | — | — | — | display_label_window_dedup |
| hybrid | candidate | urllib3_file_body_position_before_repeated_send / g1 | src/urllib3/connectionpool.py:759–761 | 22 | — | — | — | — | — | — | definition_window_dedup |
| hybrid | baseline | urllib3_release_response_connection_to_bounded_pool / g1 | src/urllib3/response.py:667–671 | 17 | 15 | — | — | — | — | — | eligible_rank_cap |
| hybrid | candidate | urllib3_release_response_connection_to_bounded_pool / g1 | src/urllib3/response.py:667–671 | 17 | 15 | — | — | — | — | — | eligible_rank_cap |
| hybrid | baseline | urllib3_response_status_retry_and_drain / g1 | src/urllib3/util/retry.py:396–407 | 19 | 17 | — | — | — | — | — | eligible_rank_cap |
| hybrid | candidate | urllib3_response_status_retry_and_drain / g1 | src/urllib3/util/retry.py:396–407 | 19 | 17 | — | — | — | — | — | eligible_rank_cap |
| hybrid | baseline | urllib3_response_status_retry_and_drain / g2 | src/urllib3/connectionpool.py:928–947 | 5 | 5 | 5 | 5 | — | — | — | character_packing |
| hybrid | candidate | urllib3_response_status_retry_and_drain / g2 | src/urllib3/connectionpool.py:928–947 | 5 | 5 | 5 | 5 | — | — | — | character_packing |

The hybrid retry-and-drain group 2 reaches eligible rank 5 as `HTTPConnectionPool.urlopen:832–960`, but earlier excerpts consume most of the character allowance. Its delivered prefix ends at 912, before evidence 928–947. Its final token count remains below 4,096; the evidence was already absent before token packing. Dense origin-check group 1 (dedup rank 6) and hybrid queued-chunk group 1 (dedup rank 8) are inside cap8 but displaced when three dependencies fill slots 6–8. The JSON lists every eligible dependency and its originating anchor.

## Containment and backfill activations

- **hybrid / urllib3_file_body_position_before_repeated_send**: removed `src/urllib3/util/request.py:body_to_chunks.chunk_readable:232-241` (actual lines 232–238, dependency) because its exact bytes remain inside `src/urllib3/util/request.py:body_to_chunks:199-266`. Appended `src/urllib3/contrib/emscripten/fetch.py:_StreamingFetcher.send:231-301` as a retrieval row, actually delivered lines 231–237. It covers none of this query's required groups. Evidence ranks stayed `[None, 1, 2]` → `[None, 1, 2]`; characters 11977 → 11999, tokens 3729 → 3738.
- **dense / urllib3_file_body_chunks_and_wire_framing**: removed `src/urllib3/util/request.py:body_to_chunks.chunk_readable:232-241` (actual lines 232–241, retrieval) because its exact bytes remain inside `src/urllib3/util/request.py:body_to_chunks:199-266`. Appended `src/urllib3/response.py:HTTPResponse._update_chunk_length:1162-1178` as a retrieval row, actually delivered lines 1162–1172. It covers none of this query's required groups. Evidence ranks stayed `[1, 4]` → `[1, 4]`; characters 11955 → 11985, tokens 3715 → 3743.

The dense wire-framing query was already complete: the retained `body_to_chunks` and `HTTPConnection.request` excerpts contain both required groups. The appended `_update_chunk_length` prefix concerns response chunk parsing and adds no required sender-framing evidence. The hybrid repeated-send query still lacks `connectionpool.py:759–761`; the appended `_StreamingFetcher.send` prefix at another implementation cannot cover that required call. Every removed actual source span remains inside one retained row and every retained old row stays unchanged.

## Bindings and limits

This diagnosis uses only saved raw receipts and pinned source bytes. It imports no producer, selector, backend, retrieval, index or model code and makes no new model/retrieval calls. All 36 source files, 453 extraction windows, 22 evidence targets, 56 first-repetition rows and 88 group/arm/mode traces were checked with independent physical-line and SHA256 logic. The JSON retains all 88 group traces, all 24 missing traces with covering candidate IDs, source hashes, actual bounds and provenance, the dedup representatives, eligible dependencies and character cuts. No gold, selector, budget or default was changed.

- raw: `results/selection-holdout-urllib3-2026-10-21/searches.jsonl.gz` — SHA256 `d89be2bc4bb78edf0142eef7ceefcd1e15bf701354a64e3e51bde0008fc0af12`
- dataset: `benchmarks/selection_holdout_queries.json` — SHA256 `b29dfe038e4d227d9820b0df316bdf2e88a94639a634eea7782d4ed47c4dbd02`
- inventory: `results/selection-holdout-urllib3-2026-10-21/source-inventory.json` — SHA256 `25b3dc423770e583cb9c93182ac1fd30b9ef0160052e9d4acc86caabf58cf154`
- producer_plan: `results/selection-holdout-urllib3-2026-10-21/plan.json` — SHA256 `e71179513859cea1e5c4cf993d53143106530b9bdc1cc53e06138514a9294f74`
- producer_summary: `results/selection-holdout-urllib3-2026-10-21/summary.json` — SHA256 `adae2a6b31a67b271a3ab51c17aa91f9d55170cee50de0b17d0ea27bdb9e4917`

The complete producer implementation hash map from the frozen native plan is retained in the JSON; its canonical manifest SHA256 is `48f8cf6316e377de69306538cc219f83fee364fae9cf82ade93de942e6f4f811`.
