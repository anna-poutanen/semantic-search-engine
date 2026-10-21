# Response-selection development replay

Observed-source CPU DEVELOPMENT selection replay. All former holdouts are already observed.

337 observed first-repetition rows share 173 unique dataset/query units. Reused query/condition rows are not independent new queries.

Every original compact payload and full excerpt/source/hash/cap record reproduced before variants. All conditions used 8 excerpts, 12,000 source characters and 4,096 exact compact cl100k tokens; no model, retrieval or network calls occurred.

| selector | study / dataset / original order | complete | groups | multi-file complete | single-file first hit |
|---|---|---:|---:|---:|---:|
| legacy | language_observed/express/bm25 | 10 / 12 | 17 / 19 | 2 | 8 |
| legacy | language_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| legacy | language_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| legacy | language_observed/zod/bm25 | 7 / 12 | 11 / 19 | 0 | 7 |
| legacy | language_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| legacy | language_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| legacy | native_express_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| legacy | native_express_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| legacy | native_jinja_observed/jinja/dense | 7 / 12 | 17 / 23 | 3 | 4 |
| legacy | native_jinja_observed/jinja/hybrid | 9 / 12 | 19 / 23 | 5 | 4 |
| legacy | native_zod_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| legacy | native_zod_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| legacy | qwen_development_observed/click/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 3 | 17 |
| legacy | qwen_development_observed/express/hybrid_k24_b1 | 12 / 12 | 19 / 19 | 4 | 8 |
| legacy | qwen_development_observed/flask/hybrid_k24_b1 | 12 / 12 | 12 / 12 | 0 | 12 |
| legacy | qwen_development_observed/koa/hybrid_k24_b1 | 6 / 6 | 11 / 11 | 3 | 3 |
| legacy | qwen_development_observed/requests/hybrid_k24_b1 | 16 / 16 | 16 / 16 | 0 | 16 |
| legacy | qwen_development_observed/rich/hybrid_k24_b1 | 19 / 20 | 23 / 24 | 3 | 16 |
| legacy | qwen_development_observed/starlette/hybrid_k24_b1 | 19 / 20 | 22 / 23 | 2 | 17 |
| legacy | qwen_development_observed/zod/hybrid_k24_b1 | 8 / 12 | 13 / 19 | 0 | 8 |
| legacy | qwen_httpx_observed/httpx/hybrid_k24_b1 | 8 / 12 | 21 / 28 | 2 | 7 |
| definition | language_observed/express/bm25 | 10 / 12 | 17 / 19 | 2 | 8 |
| definition | language_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| definition | language_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| definition | language_observed/zod/bm25 | 7 / 12 | 11 / 19 | 0 | 7 |
| definition | language_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| definition | language_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| definition | native_express_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| definition | native_express_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| definition | native_jinja_observed/jinja/dense | 7 / 12 | 17 / 23 | 3 | 4 |
| definition | native_jinja_observed/jinja/hybrid | 9 / 12 | 19 / 23 | 5 | 4 |
| definition | native_zod_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| definition | native_zod_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| definition | qwen_development_observed/click/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 3 | 17 |
| definition | qwen_development_observed/express/hybrid_k24_b1 | 12 / 12 | 19 / 19 | 4 | 8 |
| definition | qwen_development_observed/flask/hybrid_k24_b1 | 12 / 12 | 12 / 12 | 0 | 12 |
| definition | qwen_development_observed/koa/hybrid_k24_b1 | 6 / 6 | 11 / 11 | 3 | 3 |
| definition | qwen_development_observed/requests/hybrid_k24_b1 | 16 / 16 | 16 / 16 | 0 | 16 |
| definition | qwen_development_observed/rich/hybrid_k24_b1 | 19 / 20 | 23 / 24 | 3 | 16 |
| definition | qwen_development_observed/starlette/hybrid_k24_b1 | 19 / 20 | 22 / 23 | 2 | 17 |
| definition | qwen_development_observed/zod/hybrid_k24_b1 | 8 / 12 | 13 / 19 | 0 | 8 |
| definition | qwen_httpx_observed/httpx/hybrid_k24_b1 | 8 / 12 | 21 / 28 | 2 | 7 |
| rank_preserving | language_observed/express/bm25 | 10 / 12 | 17 / 19 | 2 | 8 |
| rank_preserving | language_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| rank_preserving | language_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| rank_preserving | language_observed/zod/bm25 | 7 / 12 | 11 / 19 | 0 | 7 |
| rank_preserving | language_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| rank_preserving | language_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| rank_preserving | native_express_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| rank_preserving | native_express_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| rank_preserving | native_jinja_observed/jinja/dense | 6 / 12 | 15 / 23 | 2 | 4 |
| rank_preserving | native_jinja_observed/jinja/hybrid | 8 / 12 | 17 / 23 | 4 | 4 |
| rank_preserving | native_zod_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| rank_preserving | native_zod_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| rank_preserving | qwen_development_observed/click/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 3 | 17 |
| rank_preserving | qwen_development_observed/express/hybrid_k24_b1 | 12 / 12 | 19 / 19 | 4 | 8 |
| rank_preserving | qwen_development_observed/flask/hybrid_k24_b1 | 12 / 12 | 12 / 12 | 0 | 12 |
| rank_preserving | qwen_development_observed/koa/hybrid_k24_b1 | 6 / 6 | 11 / 11 | 3 | 3 |
| rank_preserving | qwen_development_observed/requests/hybrid_k24_b1 | 16 / 16 | 16 / 16 | 0 | 16 |
| rank_preserving | qwen_development_observed/rich/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 4 | 16 |
| rank_preserving | qwen_development_observed/starlette/hybrid_k24_b1 | 19 / 20 | 22 / 23 | 2 | 17 |
| rank_preserving | qwen_development_observed/zod/hybrid_k24_b1 | 9 / 12 | 14 / 19 | 1 | 8 |
| rank_preserving | qwen_httpx_observed/httpx/hybrid_k24_b1 | 8 / 12 | 22 / 28 | 2 | 7 |
| combined | language_observed/express/bm25 | 10 / 12 | 17 / 19 | 2 | 8 |
| combined | language_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| combined | language_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| combined | language_observed/zod/bm25 | 7 / 12 | 11 / 19 | 0 | 7 |
| combined | language_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| combined | language_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| combined | native_express_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| combined | native_express_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| combined | native_jinja_observed/jinja/dense | 6 / 12 | 15 / 23 | 2 | 4 |
| combined | native_jinja_observed/jinja/hybrid | 8 / 12 | 17 / 23 | 4 | 4 |
| combined | native_zod_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| combined | native_zod_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| combined | qwen_development_observed/click/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 3 | 17 |
| combined | qwen_development_observed/express/hybrid_k24_b1 | 12 / 12 | 19 / 19 | 4 | 8 |
| combined | qwen_development_observed/flask/hybrid_k24_b1 | 12 / 12 | 12 / 12 | 0 | 12 |
| combined | qwen_development_observed/koa/hybrid_k24_b1 | 6 / 6 | 11 / 11 | 3 | 3 |
| combined | qwen_development_observed/requests/hybrid_k24_b1 | 16 / 16 | 16 / 16 | 0 | 16 |
| combined | qwen_development_observed/rich/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 4 | 16 |
| combined | qwen_development_observed/starlette/hybrid_k24_b1 | 19 / 20 | 22 / 23 | 2 | 17 |
| combined | qwen_development_observed/zod/hybrid_k24_b1 | 9 / 12 | 14 / 19 | 1 | 8 |
| combined | qwen_httpx_observed/httpx/hybrid_k24_b1 | 8 / 12 | 22 / 28 | 2 | 7 |
| legacy_coverage_backfill | language_observed/express/bm25 | 10 / 12 | 17 / 19 | 2 | 8 |
| legacy_coverage_backfill | language_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| legacy_coverage_backfill | language_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| legacy_coverage_backfill | language_observed/zod/bm25 | 7 / 12 | 11 / 19 | 0 | 7 |
| legacy_coverage_backfill | language_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| legacy_coverage_backfill | language_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| legacy_coverage_backfill | native_express_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| legacy_coverage_backfill | native_express_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| legacy_coverage_backfill | native_jinja_observed/jinja/dense | 7 / 12 | 17 / 23 | 3 | 4 |
| legacy_coverage_backfill | native_jinja_observed/jinja/hybrid | 9 / 12 | 19 / 23 | 5 | 4 |
| legacy_coverage_backfill | native_zod_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| legacy_coverage_backfill | native_zod_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| legacy_coverage_backfill | qwen_development_observed/click/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 3 | 17 |
| legacy_coverage_backfill | qwen_development_observed/express/hybrid_k24_b1 | 12 / 12 | 19 / 19 | 4 | 8 |
| legacy_coverage_backfill | qwen_development_observed/flask/hybrid_k24_b1 | 12 / 12 | 12 / 12 | 0 | 12 |
| legacy_coverage_backfill | qwen_development_observed/koa/hybrid_k24_b1 | 6 / 6 | 11 / 11 | 3 | 3 |
| legacy_coverage_backfill | qwen_development_observed/requests/hybrid_k24_b1 | 16 / 16 | 16 / 16 | 0 | 16 |
| legacy_coverage_backfill | qwen_development_observed/rich/hybrid_k24_b1 | 19 / 20 | 23 / 24 | 3 | 16 |
| legacy_coverage_backfill | qwen_development_observed/starlette/hybrid_k24_b1 | 20 / 20 | 23 / 23 | 3 | 17 |
| legacy_coverage_backfill | qwen_development_observed/zod/hybrid_k24_b1 | 9 / 12 | 14 / 19 | 1 | 8 |
| legacy_coverage_backfill | qwen_httpx_observed/httpx/hybrid_k24_b1 | 8 / 12 | 21 / 28 | 2 | 7 |
| definition_coverage_backfill | language_observed/express/bm25 | 10 / 12 | 17 / 19 | 2 | 8 |
| definition_coverage_backfill | language_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| definition_coverage_backfill | language_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| definition_coverage_backfill | language_observed/zod/bm25 | 7 / 12 | 11 / 19 | 0 | 7 |
| definition_coverage_backfill | language_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| definition_coverage_backfill | language_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| definition_coverage_backfill | native_express_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| definition_coverage_backfill | native_express_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| definition_coverage_backfill | native_jinja_observed/jinja/dense | 7 / 12 | 17 / 23 | 3 | 4 |
| definition_coverage_backfill | native_jinja_observed/jinja/hybrid | 9 / 12 | 19 / 23 | 5 | 4 |
| definition_coverage_backfill | native_zod_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| definition_coverage_backfill | native_zod_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| definition_coverage_backfill | qwen_development_observed/click/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 3 | 17 |
| definition_coverage_backfill | qwen_development_observed/express/hybrid_k24_b1 | 12 / 12 | 19 / 19 | 4 | 8 |
| definition_coverage_backfill | qwen_development_observed/flask/hybrid_k24_b1 | 12 / 12 | 12 / 12 | 0 | 12 |
| definition_coverage_backfill | qwen_development_observed/koa/hybrid_k24_b1 | 6 / 6 | 11 / 11 | 3 | 3 |
| definition_coverage_backfill | qwen_development_observed/requests/hybrid_k24_b1 | 16 / 16 | 16 / 16 | 0 | 16 |
| definition_coverage_backfill | qwen_development_observed/rich/hybrid_k24_b1 | 19 / 20 | 23 / 24 | 3 | 16 |
| definition_coverage_backfill | qwen_development_observed/starlette/hybrid_k24_b1 | 20 / 20 | 23 / 23 | 3 | 17 |
| definition_coverage_backfill | qwen_development_observed/zod/hybrid_k24_b1 | 9 / 12 | 14 / 19 | 1 | 8 |
| definition_coverage_backfill | qwen_httpx_observed/httpx/hybrid_k24_b1 | 8 / 12 | 21 / 28 | 2 | 7 |

Qualification requires no lost group, baseline-complete row or single-file first hit, and an additional unique complete query. Each change is evaluated independently; no default is selected automatically.

- definition: does not qualify; 0 unique complete-query gains, 0 group losses, 0 complete-row losses.
- rank_preserving: does not qualify; 4 unique complete-query gains, 7 group losses, 4 complete-row losses.
- combined: does not qualify; 4 unique complete-query gains, 7 group losses, 4 complete-row losses.
- legacy_coverage_backfill: qualifies for further development review; 2 unique complete-query gains, 0 group losses, 0 complete-row losses.
- definition_coverage_backfill: qualifies for further development review; 2 unique complete-query gains, 0 group losses, 0 complete-row losses.

All full-evidence group gains and losses, complete-query changes, absent-query inventories and per-row responses are retained in summary.json and replay.jsonl.gz. Source-complete coverage does not establish coding correctness, generalization, speed or token savings.
