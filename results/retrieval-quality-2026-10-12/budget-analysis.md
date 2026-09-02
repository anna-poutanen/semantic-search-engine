# Post-hoc context-budget analysis

This CPU-only analysis repacks the 448 saved HTTP responses from the frozen 106-query run. It performs no retrieval or model inference. Smaller caps are descriptive reuse of the saved outputs; they are not new independently held-out configurations and do not select a default.

Every derived compact JSON payload was counted exactly with pinned `cl100k_base`, including metadata and its self-reported token count. Excerpts retain whole physical source lines and their verified SHA-256. No fresh latency is reported. The JSON artifact includes every derived excerpt's bounds/hash and context hash.

| Tokens | Mode | Query partition | Complete present | Complete multi-file | Evidence groups | Median / max delivered tokens |
|---:|---|---|---:|---:|---:|---:|
| 512 | dense | all | 40/88 | 0/10 | 46/99 | 504 / 512 |
| 512 | dense | development | 34/68 | 0/6 | 38/75 | 502.5 / 512 |
| 512 | dense | development_queries | 24/48 | 0/2 | 25/50 | 504.5 / 512 |
| 512 | dense | calibration | 10/20 | 0/4 | 13/25 | 500 / 511 |
| 512 | dense | repository_holdout | 6/20 | 0/4 | 8/24 | 507 / 512 |
| 512 | dense_context | all | 40/88 | 0/10 | 46/99 | 504 / 512 |
| 512 | dense_context | development | 34/68 | 0/6 | 38/75 | 502.5 / 512 |
| 512 | dense_context | development_queries | 24/48 | 0/2 | 25/50 | 504.5 / 512 |
| 512 | dense_context | calibration | 10/20 | 0/4 | 13/25 | 500 / 511 |
| 512 | dense_context | repository_holdout | 6/20 | 0/4 | 8/24 | 507 / 512 |
| 512 | dense_context_policy | all | 6/20 | 0/4 | 8/24 | 504 / 512 |
| 512 | dense_context_policy | repository_holdout | 6/20 | 0/4 | 8/24 | 504 / 512 |
| 512 | fts5 | all | 24/88 | 0/10 | 26/99 | 505.5 / 512 |
| 512 | fts5 | development | 21/68 | 0/6 | 22/75 | 504.5 / 512 |
| 512 | fts5 | development_queries | 17/48 | 0/2 | 17/50 | 503.5 / 512 |
| 512 | fts5 | calibration | 4/20 | 0/4 | 5/25 | 506.5 / 512 |
| 512 | fts5 | repository_holdout | 3/20 | 0/4 | 4/24 | 508.5 / 512 |
| 512 | hybrid | all | 34/88 | 0/10 | 40/99 | 505 / 512 |
| 512 | hybrid | development | 29/68 | 0/6 | 33/75 | 504.5 / 512 |
| 512 | hybrid | development_queries | 23/48 | 0/2 | 24/50 | 504.5 / 512 |
| 512 | hybrid | calibration | 6/20 | 0/4 | 9/25 | 504 / 512 |
| 512 | hybrid | repository_holdout | 5/20 | 0/4 | 7/24 | 507 / 512 |
| 1024 | dense | all | 71/88 | 3/10 | 79/99 | 1015.5 / 1024 |
| 1024 | dense | development | 56/68 | 1/6 | 62/75 | 1012 / 1024 |
| 1024 | dense | development_queries | 41/48 | 1/2 | 43/50 | 1012.5 / 1024 |
| 1024 | dense | calibration | 15/20 | 0/4 | 19/25 | 1011.5 / 1024 |
| 1024 | dense | repository_holdout | 15/20 | 2/4 | 17/24 | 1019 / 1023 |
| 1024 | dense_context | all | 71/88 | 3/10 | 79/99 | 1016 / 1024 |
| 1024 | dense_context | development | 56/68 | 1/6 | 62/75 | 1014 / 1024 |
| 1024 | dense_context | development_queries | 41/48 | 1/2 | 43/50 | 1014 / 1024 |
| 1024 | dense_context | calibration | 15/20 | 0/4 | 19/25 | 1013 / 1024 |
| 1024 | dense_context | repository_holdout | 15/20 | 2/4 | 17/24 | 1019 / 1023 |
| 1024 | dense_context_policy | all | 15/20 | 2/4 | 17/24 | 1017.5 / 1023 |
| 1024 | dense_context_policy | repository_holdout | 15/20 | 2/4 | 17/24 | 1017.5 / 1023 |
| 1024 | fts5 | all | 44/88 | 1/10 | 49/99 | 1017 / 1024 |
| 1024 | fts5 | development | 36/68 | 1/6 | 39/75 | 1016 / 1024 |
| 1024 | fts5 | development_queries | 25/48 | 0/2 | 26/50 | 1016 / 1024 |
| 1024 | fts5 | calibration | 11/20 | 1/4 | 13/25 | 1018 / 1024 |
| 1024 | fts5 | repository_holdout | 8/20 | 0/4 | 10/24 | 1018.5 / 1024 |
| 1024 | hybrid | all | 63/88 | 2/10 | 71/99 | 1017 / 1024 |
| 1024 | hybrid | development | 49/68 | 0/6 | 54/75 | 1017 / 1024 |
| 1024 | hybrid | development_queries | 36/48 | 0/2 | 38/50 | 1017 / 1024 |
| 1024 | hybrid | calibration | 13/20 | 0/4 | 16/25 | 1017 / 1024 |
| 1024 | hybrid | repository_holdout | 14/20 | 2/4 | 17/24 | 1018.5 / 1024 |
| 2048 | dense | all | 79/88 | 5/10 | 89/99 | 1665.5 / 2048 |
| 2048 | dense | development | 62/68 | 3/6 | 69/75 | 1622 / 2048 |
| 2048 | dense | development_queries | 46/48 | 2/2 | 48/50 | 1710 / 2048 |
| 2048 | dense | calibration | 16/20 | 1/4 | 21/25 | 1397 / 2044 |
| 2048 | dense | repository_holdout | 17/20 | 2/4 | 20/24 | 1860 / 2046 |
| 2048 | dense_context | all | 80/88 | 6/10 | 90/99 | 2033.5 / 2048 |
| 2048 | dense_context | development | 63/68 | 4/6 | 70/75 | 2033.5 / 2048 |
| 2048 | dense_context | development_queries | 46/48 | 2/2 | 48/50 | 2035 / 2048 |
| 2048 | dense_context | calibration | 17/20 | 2/4 | 22/25 | 2006 / 2048 |
| 2048 | dense_context | repository_holdout | 17/20 | 2/4 | 20/24 | 2033.5 / 2047 |
| 2048 | dense_context_policy | all | 17/20 | 2/4 | 20/24 | 2005.5 / 2047 |
| 2048 | dense_context_policy | repository_holdout | 17/20 | 2/4 | 20/24 | 2005.5 / 2047 |
| 2048 | fts5 | all | 63/88 | 3/10 | 73/99 | 2033.5 / 2048 |
| 2048 | fts5 | development | 52/68 | 1/6 | 58/75 | 2036 / 2048 |
| 2048 | fts5 | development_queries | 39/48 | 0/2 | 40/50 | 2035 / 2048 |
| 2048 | fts5 | calibration | 13/20 | 1/4 | 18/25 | 2036 / 2046 |
| 2048 | fts5 | repository_holdout | 11/20 | 2/4 | 15/24 | 2008 / 2048 |
| 2048 | hybrid | all | 76/88 | 4/10 | 86/99 | 1951 / 2048 |
| 2048 | hybrid | development | 59/68 | 2/6 | 65/75 | 1929.5 / 2048 |
| 2048 | hybrid | development_queries | 42/48 | 0/2 | 44/50 | 1985 / 2047 |
| 2048 | hybrid | calibration | 17/20 | 2/4 | 21/25 | 1783.5 / 2048 |
| 2048 | hybrid | repository_holdout | 17/20 | 2/4 | 21/24 | 1992 / 2047 |
| 4096 | dense | all | 81/88 | 5/10 | 91/99 | 1665.5 / 3284 |
| 4096 | dense | development | 63/68 | 3/6 | 70/75 | 1622 / 3163 |
| 4096 | dense | development_queries | 47/48 | 2/2 | 49/50 | 1710 / 3163 |
| 4096 | dense | calibration | 16/20 | 1/4 | 21/25 | 1397 / 2723 |
| 4096 | dense | repository_holdout | 18/20 | 2/4 | 21/24 | 1860 / 3284 |
| 4096 | dense_context | all | 83/88 | 6/10 | 93/99 | 2461 / 3753 |
| 4096 | dense_context | development | 65/68 | 4/6 | 72/75 | 2434.5 / 3753 |
| 4096 | dense_context | development_queries | 47/48 | 2/2 | 49/50 | 2449 / 3753 |
| 4096 | dense_context | calibration | 18/20 | 2/4 | 23/25 | 2209.5 / 3694 |
| 4096 | dense_context | repository_holdout | 18/20 | 2/4 | 21/24 | 2622 / 3701 |
| 4096 | dense_context_policy | all | 18/20 | 2/4 | 21/24 | 2495.5 / 3701 |
| 4096 | dense_context_policy | repository_holdout | 18/20 | 2/4 | 21/24 | 2495.5 / 3701 |
| 4096 | fts5 | all | 66/88 | 4/10 | 76/99 | 2248.5 / 3446 |
| 4096 | fts5 | development | 54/68 | 2/6 | 60/75 | 2272 / 3446 |
| 4096 | fts5 | development_queries | 40/48 | 0/2 | 41/50 | 2316.5 / 3446 |
| 4096 | fts5 | calibration | 14/20 | 2/4 | 19/25 | 2090 / 3319 |
| 4096 | fts5 | repository_holdout | 12/20 | 2/4 | 16/24 | 2131.5 / 3315 |
| 4096 | hybrid | all | 78/88 | 6/10 | 89/99 | 1951 / 3500 |
| 4096 | hybrid | development | 60/68 | 3/6 | 67/75 | 1929.5 / 3500 |
| 4096 | hybrid | development_queries | 43/48 | 1/2 | 45/50 | 2020 / 3500 |
| 4096 | hybrid | calibration | 17/20 | 2/4 | 22/25 | 1783.5 / 2779 |
| 4096 | hybrid | repository_holdout | 18/20 | 3/4 | 22/24 | 2250.5 / 3196 |

## Where expanded context recovered evidence

The frozen expanded mode preserves five dense anchors, adds at most two static callees per anchor, and fills unused slots with later anchors, up to eight total results. Added slots can improve quality without resolving a dependency. These comparisons use the original 4096-token outputs.

Expanded context adds 2 complete queries and loses 0 compared with dense-five. Recovered evidence groups by their first delivered origin: `{"dependency": 1, "extra_anchor_slot": 1}`.

| Query | Dense-five evidence ranks | Expanded evidence ranks | Recovered group origins |
|---|---|---|---|
| `starlette_mounted_root_paths` | [None] | [8] | group 1: extra_anchor_slot at starlette/routing.py:Mount.matches:399-430 |
| `starlette_sync_endpoint_worker` | [4, None] | [4, 8] | group 2: dependency at starlette/concurrency.py:run_in_threadpool:35-37 |

## Missing evidence through the selection stages

The JSON artifact traces every originally incomplete present-query group through the saved first 50 candidates, exact path/symbol deduplication, reconstructed full selected windows, and actual delivered excerpts. The default expanded-context failures are listed here; no labels or rankings were changed.

| Query | Group | Candidate 50 rank | Deduplicated rank | Full selected window rank | Limitation |
|---|---:|---:|---:|---:|---|
| `click_binary_console_output` | 1 | 31 | 31 | — | outside_selected_anchor_and_callee_slots |
| `click_atomic_option_commit` | 1 | 13 | 13 | — | outside_selected_anchor_and_callee_slots |
| `click_standalone_usage_error` | 2 | 24 | 24 | — | outside_selected_anchor_and_callee_slots |
| `rich_console_markup_error_path` | 1 | — | — | — | outside_candidate50_and_selected_callees |
| `rich_centered_panel_composition` | 1 | 6 | 6 | — | outside_selected_anchor_and_callee_slots |
| `rich_centered_panel_composition` | 2 | 12 | 12 | — | outside_selected_anchor_and_callee_slots |

## Limits

- Post-hoc descriptive reuse of saved responses; no fresh retrieval, inference, or latency measurements.
- Caps below 4096 were not predeclared held-out configurations. No default or threshold is selected from this analysis.
- Cannot recover windows or lines already omitted by original ranking, deduplication, result limits, 12000 source characters, or 4096 tokens.
- Original repository_holdout labels identify the source query partition; smaller-cap outcomes are derived descriptions, not a new independent holdout.
- Source dependencies are reconstructed with the frozen static graph; runtime dispatch or complete execution-path correctness is not proven.
- Exact token counts cover one compact context payload, not provider billing or paired-agent token savings.
