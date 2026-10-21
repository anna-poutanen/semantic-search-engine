# Complementary-window development replay

Observed-source CPU DEVELOPMENT complementary-window replay

365 observed first-repetition rows share 187 distinct dataset/query units. Former holdouts, including urllib3, are development data. Reused rows are not independent queries.

All original legacy responses and all saved prior-best coverage responses reproduced before new variants. Every condition used 8 excerpts, 12,000 source characters and 4,096 exact compact cl100k tokens. No model, retrieval, index or network calls occurred.

| condition | study / dataset / original order | complete | covered groups | multi-file complete | single-file first hit |
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
| legacy | native_urllib3_observed/urllib3/dense | 8 / 12 | 17 / 22 | 4 | 4 |
| legacy | native_urllib3_observed/urllib3/hybrid | 7 / 12 | 15 / 22 | 4 | 3 |
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
| coverage_control | language_observed/express/bm25 | 10 / 12 | 17 / 19 | 2 | 8 |
| coverage_control | language_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| coverage_control | language_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| coverage_control | language_observed/zod/bm25 | 7 / 12 | 11 / 19 | 0 | 7 |
| coverage_control | language_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| coverage_control | language_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| coverage_control | native_express_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| coverage_control | native_express_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| coverage_control | native_jinja_observed/jinja/dense | 7 / 12 | 17 / 23 | 3 | 4 |
| coverage_control | native_jinja_observed/jinja/hybrid | 9 / 12 | 19 / 23 | 5 | 4 |
| coverage_control | native_urllib3_observed/urllib3/dense | 8 / 12 | 17 / 22 | 4 | 4 |
| coverage_control | native_urllib3_observed/urllib3/hybrid | 7 / 12 | 15 / 22 | 4 | 3 |
| coverage_control | native_zod_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| coverage_control | native_zod_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| coverage_control | qwen_development_observed/click/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 3 | 17 |
| coverage_control | qwen_development_observed/express/hybrid_k24_b1 | 12 / 12 | 19 / 19 | 4 | 8 |
| coverage_control | qwen_development_observed/flask/hybrid_k24_b1 | 12 / 12 | 12 / 12 | 0 | 12 |
| coverage_control | qwen_development_observed/koa/hybrid_k24_b1 | 6 / 6 | 11 / 11 | 3 | 3 |
| coverage_control | qwen_development_observed/requests/hybrid_k24_b1 | 16 / 16 | 16 / 16 | 0 | 16 |
| coverage_control | qwen_development_observed/rich/hybrid_k24_b1 | 19 / 20 | 23 / 24 | 3 | 16 |
| coverage_control | qwen_development_observed/starlette/hybrid_k24_b1 | 20 / 20 | 23 / 23 | 3 | 17 |
| coverage_control | qwen_development_observed/zod/hybrid_k24_b1 | 9 / 12 | 14 / 19 | 1 | 8 |
| coverage_control | qwen_httpx_observed/httpx/hybrid_k24_b1 | 8 / 12 | 21 / 28 | 2 | 7 |
| connected_union | language_observed/express/bm25 | 10 / 12 | 17 / 19 | 2 | 8 |
| connected_union | language_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| connected_union | language_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| connected_union | language_observed/zod/bm25 | 6 / 12 | 10 / 19 | 0 | 6 |
| connected_union | language_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| connected_union | language_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| connected_union | native_express_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| connected_union | native_express_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| connected_union | native_jinja_observed/jinja/dense | 7 / 12 | 17 / 23 | 3 | 4 |
| connected_union | native_jinja_observed/jinja/hybrid | 9 / 12 | 19 / 23 | 5 | 4 |
| connected_union | native_urllib3_observed/urllib3/dense | 8 / 12 | 17 / 22 | 4 | 4 |
| connected_union | native_urllib3_observed/urllib3/hybrid | 7 / 12 | 15 / 22 | 4 | 3 |
| connected_union | native_zod_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| connected_union | native_zod_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| connected_union | qwen_development_observed/click/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 3 | 17 |
| connected_union | qwen_development_observed/express/hybrid_k24_b1 | 12 / 12 | 19 / 19 | 4 | 8 |
| connected_union | qwen_development_observed/flask/hybrid_k24_b1 | 12 / 12 | 12 / 12 | 0 | 12 |
| connected_union | qwen_development_observed/koa/hybrid_k24_b1 | 6 / 6 | 11 / 11 | 3 | 3 |
| connected_union | qwen_development_observed/requests/hybrid_k24_b1 | 16 / 16 | 16 / 16 | 0 | 16 |
| connected_union | qwen_development_observed/rich/hybrid_k24_b1 | 19 / 20 | 23 / 24 | 3 | 16 |
| connected_union | qwen_development_observed/starlette/hybrid_k24_b1 | 20 / 20 | 23 / 23 | 3 | 17 |
| connected_union | qwen_development_observed/zod/hybrid_k24_b1 | 9 / 12 | 14 / 19 | 1 | 8 |
| connected_union | qwen_httpx_observed/httpx/hybrid_k24_b1 | 8 / 12 | 21 / 28 | 2 | 7 |
| guarded_continuation | language_observed/express/bm25 | 10 / 12 | 17 / 19 | 2 | 8 |
| guarded_continuation | language_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| guarded_continuation | language_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| guarded_continuation | language_observed/zod/bm25 | 7 / 12 | 11 / 19 | 0 | 7 |
| guarded_continuation | language_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| guarded_continuation | language_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| guarded_continuation | native_express_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| guarded_continuation | native_express_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| guarded_continuation | native_jinja_observed/jinja/dense | 7 / 12 | 17 / 23 | 3 | 4 |
| guarded_continuation | native_jinja_observed/jinja/hybrid | 9 / 12 | 19 / 23 | 5 | 4 |
| guarded_continuation | native_urllib3_observed/urllib3/dense | 8 / 12 | 17 / 22 | 4 | 4 |
| guarded_continuation | native_urllib3_observed/urllib3/hybrid | 7 / 12 | 15 / 22 | 4 | 3 |
| guarded_continuation | native_zod_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| guarded_continuation | native_zod_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| guarded_continuation | qwen_development_observed/click/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 3 | 17 |
| guarded_continuation | qwen_development_observed/express/hybrid_k24_b1 | 12 / 12 | 19 / 19 | 4 | 8 |
| guarded_continuation | qwen_development_observed/flask/hybrid_k24_b1 | 12 / 12 | 12 / 12 | 0 | 12 |
| guarded_continuation | qwen_development_observed/koa/hybrid_k24_b1 | 6 / 6 | 11 / 11 | 3 | 3 |
| guarded_continuation | qwen_development_observed/requests/hybrid_k24_b1 | 16 / 16 | 16 / 16 | 0 | 16 |
| guarded_continuation | qwen_development_observed/rich/hybrid_k24_b1 | 19 / 20 | 23 / 24 | 3 | 16 |
| guarded_continuation | qwen_development_observed/starlette/hybrid_k24_b1 | 20 / 20 | 23 / 23 | 3 | 17 |
| guarded_continuation | qwen_development_observed/zod/hybrid_k24_b1 | 9 / 12 | 14 / 19 | 1 | 8 |
| guarded_continuation | qwen_httpx_observed/httpx/hybrid_k24_b1 | 8 / 12 | 21 / 28 | 2 | 7 |
| guarded_connected_union | language_observed/express/bm25 | 10 / 12 | 17 / 19 | 2 | 8 |
| guarded_connected_union | language_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| guarded_connected_union | language_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| guarded_connected_union | language_observed/zod/bm25 | 7 / 12 | 11 / 19 | 0 | 7 |
| guarded_connected_union | language_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| guarded_connected_union | language_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| guarded_connected_union | native_express_observed/express/dense | 10 / 12 | 17 / 19 | 2 | 8 |
| guarded_connected_union | native_express_observed/express/hybrid | 11 / 12 | 18 / 19 | 3 | 8 |
| guarded_connected_union | native_jinja_observed/jinja/dense | 7 / 12 | 17 / 23 | 3 | 4 |
| guarded_connected_union | native_jinja_observed/jinja/hybrid | 9 / 12 | 19 / 23 | 5 | 4 |
| guarded_connected_union | native_urllib3_observed/urllib3/dense | 8 / 12 | 17 / 22 | 4 | 4 |
| guarded_connected_union | native_urllib3_observed/urllib3/hybrid | 7 / 12 | 15 / 22 | 4 | 3 |
| guarded_connected_union | native_zod_observed/zod/dense | 8 / 12 | 13 / 19 | 0 | 8 |
| guarded_connected_union | native_zod_observed/zod/hybrid | 7 / 12 | 11 / 19 | 0 | 7 |
| guarded_connected_union | qwen_development_observed/click/hybrid_k24_b1 | 20 / 20 | 24 / 24 | 3 | 17 |
| guarded_connected_union | qwen_development_observed/express/hybrid_k24_b1 | 12 / 12 | 19 / 19 | 4 | 8 |
| guarded_connected_union | qwen_development_observed/flask/hybrid_k24_b1 | 12 / 12 | 12 / 12 | 0 | 12 |
| guarded_connected_union | qwen_development_observed/koa/hybrid_k24_b1 | 6 / 6 | 11 / 11 | 3 | 3 |
| guarded_connected_union | qwen_development_observed/requests/hybrid_k24_b1 | 16 / 16 | 16 / 16 | 0 | 16 |
| guarded_connected_union | qwen_development_observed/rich/hybrid_k24_b1 | 19 / 20 | 23 / 24 | 3 | 16 |
| guarded_connected_union | qwen_development_observed/starlette/hybrid_k24_b1 | 20 / 20 | 23 / 23 | 3 | 17 |
| guarded_connected_union | qwen_development_observed/zod/hybrid_k24_b1 | 9 / 12 | 14 / 19 | 1 | 8 |
| guarded_connected_union | qwen_httpx_observed/httpx/hybrid_k24_b1 | 8 / 12 | 21 / 28 | 2 | 7 |

Qualification requires no lost full group, complete row or single-file first hit compared with either legacy or the prior-best coverage control, plus an additional distinct complete query over that coverage control. Previously known gains do not count as new.

- connected_union: does not qualify; 0 additional distinct complete-query gains over coverage_control. Loss details against both controls are retained in summary.json.
- guarded_continuation: does not qualify; 0 additional distinct complete-query gains over coverage_control. Loss details against both controls are retained in summary.json.
- guarded_connected_union: does not qualify; 0 additional distinct complete-query gains over coverage_control. Loss details against both controls are retained in summary.json.

Every group/complete-query gain and regression is retained against both controls, with raw ranked, selected and eligible source, pre/postprocessing responses, source/assembly receipts and exact compact token counts. Source-evidence coverage is not coding correctness, billing, native warm speed or generalization. Defaults remain unchanged.
