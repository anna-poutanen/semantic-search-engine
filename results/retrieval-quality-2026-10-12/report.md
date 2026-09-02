# Multi-repository retrieval quality

Source labels and configurations were frozen before inference. Complete delivery requires every AND evidence group in the actual returned excerpts. Candidate coverage uses 50 full windows.

| Mode | Group | Present | Candidate complete | Delivered complete | Multi-file complete | Absent rejected | Median / P95 HTTP |
|---|---|---:|---:|---:|---:|---:|---:|
| dense | all | 88 | 87/88 | 81/88 | 5/10 | 0/18 | 0.130s / 0.164s |
| dense | development | 68 | 68/68 | 63/68 | 3/6 | 0/14 | 0.125s / 0.148s |
| dense | development_queries | 48 | 48/48 | 47/48 | 2/2 | 0/10 | 0.122s / 0.148s |
| dense | calibration | 20 | 20/20 | 16/20 | 1/4 | 0/4 | 0.131s / 0.152s |
| dense | repository_holdout | 20 | 19/20 | 18/20 | 2/4 | 0/4 | 0.158s / 0.174s |
| dense | repository:benchmarks/click_queries.json | 20 | 20/20 | 17/20 | 1/3 | 0/4 | 0.133s / 0.149s |
| dense | repository:benchmarks/flask_queries.json | 12 | 12/12 | 12/12 | 0/0 | 0/2 | 0.121s / 0.131s |
| dense | repository:benchmarks/new_requests_queries.json | 16 | 16/16 | 16/16 | 0/0 | 0/4 | 0.116s / 0.120s |
| dense | repository:benchmarks/rich_queries.json | 20 | 19/20 | 18/20 | 2/4 | 0/4 | 0.158s / 0.174s |
| dense | repository:benchmarks/starlette_queries.json | 20 | 20/20 | 18/20 | 2/3 | 0/4 | 0.129s / 0.152s |
| dense_context | all | 88 | 87/88 | 83/88 | 6/10 | 0/18 | 0.135s / 0.169s |
| dense_context | development | 68 | 68/68 | 65/68 | 4/6 | 0/14 | 0.131s / 0.150s |
| dense_context | development_queries | 48 | 48/48 | 47/48 | 2/2 | 0/10 | 0.127s / 0.150s |
| dense_context | calibration | 20 | 20/20 | 18/20 | 2/4 | 0/4 | 0.135s / 0.154s |
| dense_context | repository_holdout | 20 | 19/20 | 18/20 | 2/4 | 0/4 | 0.160s / 0.175s |
| dense_context | repository:benchmarks/click_queries.json | 20 | 20/20 | 17/20 | 1/3 | 0/4 | 0.136s / 0.149s |
| dense_context | repository:benchmarks/flask_queries.json | 12 | 12/12 | 12/12 | 0/0 | 0/2 | 0.126s / 0.139s |
| dense_context | repository:benchmarks/new_requests_queries.json | 16 | 16/16 | 16/16 | 0/0 | 0/4 | 0.121s / 0.127s |
| dense_context | repository:benchmarks/rich_queries.json | 20 | 19/20 | 18/20 | 2/4 | 0/4 | 0.160s / 0.175s |
| dense_context | repository:benchmarks/starlette_queries.json | 20 | 20/20 | 20/20 | 3/3 | 0/4 | 0.137s / 0.177s |
| dense_context_policy | all | 20 | 19/20 | 18/20 | 2/4 | 3/4 | 0.163s / 0.174s |
| dense_context_policy | repository_holdout | 20 | 19/20 | 18/20 | 2/4 | 3/4 | 0.163s / 0.174s |
| dense_context_policy | repository:benchmarks/rich_queries.json | 20 | 19/20 | 18/20 | 2/4 | 3/4 | 0.163s / 0.174s |
| fts5 | all | 88 | 85/88 | 66/88 | 4/10 | 0/18 | 0.100s / 0.127s |
| fts5 | development | 68 | 65/68 | 54/68 | 2/6 | 0/14 | 0.095s / 0.121s |
| fts5 | development_queries | 48 | 47/48 | 40/48 | 0/2 | 0/10 | 0.091s / 0.121s |
| fts5 | calibration | 20 | 18/20 | 14/20 | 2/4 | 0/4 | 0.101s / 0.122s |
| fts5 | repository_holdout | 20 | 20/20 | 12/20 | 2/4 | 0/4 | 0.116s / 0.133s |
| fts5 | repository:benchmarks/click_queries.json | 20 | 20/20 | 13/20 | 1/3 | 0/4 | 0.099s / 0.122s |
| fts5 | repository:benchmarks/flask_queries.json | 12 | 11/12 | 10/12 | 0/0 | 0/2 | 0.092s / 0.127s |
| fts5 | repository:benchmarks/new_requests_queries.json | 16 | 16/16 | 16/16 | 0/0 | 0/4 | 0.086s / 0.089s |
| fts5 | repository:benchmarks/rich_queries.json | 20 | 20/20 | 12/20 | 2/4 | 0/4 | 0.116s / 0.133s |
| fts5 | repository:benchmarks/starlette_queries.json | 20 | 18/20 | 15/20 | 1/3 | 0/4 | 0.103s / 0.121s |
| hybrid | all | 88 | 88/88 | 78/88 | 6/10 | 0/18 | 0.135s / 0.168s |
| hybrid | development | 68 | 68/68 | 60/68 | 3/6 | 0/14 | 0.132s / 0.159s |
| hybrid | development_queries | 48 | 48/48 | 43/48 | 1/2 | 0/10 | 0.125s / 0.164s |
| hybrid | calibration | 20 | 20/20 | 17/20 | 2/4 | 0/4 | 0.136s / 0.149s |
| hybrid | repository_holdout | 20 | 20/20 | 18/20 | 3/4 | 0/4 | 0.159s / 0.171s |
| hybrid | repository:benchmarks/click_queries.json | 20 | 20/20 | 17/20 | 2/3 | 0/4 | 0.137s / 0.164s |
| hybrid | repository:benchmarks/flask_queries.json | 12 | 12/12 | 11/12 | 0/0 | 0/2 | 0.123s / 0.210s |
| hybrid | repository:benchmarks/new_requests_queries.json | 16 | 16/16 | 16/16 | 0/0 | 0/4 | 0.118s / 0.123s |
| hybrid | repository:benchmarks/rich_queries.json | 20 | 20/20 | 18/20 | 3/4 | 0/4 | 0.159s / 0.171s |
| hybrid | repository:benchmarks/starlette_queries.json | 20 | 20/20 | 16/20 | 1/3 | 0/4 | 0.136s / 0.159s |

All timing samples include absent queries. Startup, indexing, and candidate diagnostics are separate. Every measured semantic search freshly embeds its query. FTS5 BM25 and float32 sqlite-vec cosine are different engines from earlier Python/NumPy pilot baselines.

## Calibrated weak-match policy

```json
{
  "version": 1,
  "threshold": 0.5642289817333221,
  "embedding": {
    "add_special_tokens": true,
    "batch_order": "stable ascending token length; restore input order",
    "batch_size": 4,
    "checkpoint_validation": "pinned Hub download provenance; original unquantized config",
    "dimensions": 1024,
    "document_prompt": null,
    "id": "Qwen/Qwen3-Embedding-0.6B",
    "max_tokens": 8192,
    "normalization": "L2, float32",
    "packages": {
      "mlx": "0.32.3",
      "mlx-embeddings": "0.1.0",
      "mlx-metal": "0.32.3",
      "numpy": "2.5.4",
      "transformers": "5.19.0"
    },
    "padding_side": "right",
    "pooling": "last non-padding token",
    "quantized": false,
    "query_instruction": "Given a description of code behavior, retrieve the code implementation that performs it.",
    "query_prompt": "Instruct: Given a description of code behavior, retrieve the code implementation that performs it.\nQuery:",
    "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
    "runtime": "mlx-embeddings",
    "runtime_version": "0.1.0",
    "truncation": "right",
    "weights_dtype": "bfloat16"
  },
  "calibration": {
    "role": "development",
    "split": "calibration",
    "positive_queries": 20,
    "absent_queries": 4,
    "retained_positives": 20,
    "rejected_absent": 3,
    "query_ids": [
      "click_date_format_fallback",
      "click_boolean_text",
      "click_path_kind_validation",
      "click_command_source_precedence",
      "click_repeated_env_tuples",
      "click_short_option_cluster",
      "click_unwrapped_help_paragraph",
      "click_confirmation_retry",
      "click_atomic_option_commit",
      "click_standalone_usage_error",
      "click_yaml_config_autoload",
      "click_persistent_command_history",
      "starlette_repeated_case_insensitive_headers",
      "starlette_request_stream_disconnect",
      "starlette_urlencoded_field_assembly",
      "starlette_file_head_or_chunked_body",
      "starlette_websocket_send_lifecycle",
      "starlette_lifespan_state_and_failures",
      "starlette_mounted_root_paths",
      "starlette_trusted_host_redirect",
      "starlette_sync_endpoint_worker",
      "starlette_lenient_cookie_session_restore",
      "starlette_redis_distributed_rate_limit",
      "starlette_tls_client_certificate_handshake"
    ],
    "dataset_sha256": [
      "0aee9fc24238731966e6f98f14e2205fcd3cab80059f3165b43dade8eb53afb6",
      "fdcd668df309124aefe7e3fd00171a84b8f9ce75cc7fac5dbbd446dfe706ade3"
    ]
  },
  "meaning": "Insufficient semantic evidence; this does not prove the behavior is absent.",
  "evaluation": {
    "manifest_sha256": "51222d58f565e5865c4569bf122aa957b2cb703f4d41ae20f079f4f987a14b11",
    "frozen_config_sha256": "89b1bef5abe162aced0af9dee538fa8099f08189145aeb7253f6e7e7c08692d0"
  }
}
```

The policy was fitted only to declared Click/Starlette development calibration queries and written before any Rich retrieval. It preserves all calibration positives; held-out positive rejections and negative acceptance still require inspection. Insufficient evidence does not prove absence.

## Limits

- Developer-authored source-reviewed queries; not representative user tasks.
- Repository holdout applies to this evaluation, not model pretraining.
- Token counts describe one compact tool payload, not provider billing.
- Insufficient evidence does not prove behavior absent.
