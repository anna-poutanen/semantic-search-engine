# H2O q8 / Flask development experiment

Corpus: `d086db856be187255b8ec61ef409357393020f32`; 358 windows.

| Metric | BM25 | Same candidates + H2O |
|---|---:|---:|
| hit_at_1 | 0.667 | 0.917 |
| hit_at_3 | 0.833 | 0.917 |
| hit_at_5 | 0.917 | 0.917 |
| mrr | 0.757 | 0.917 |

Candidate coverage: 91.7%. Gold beats both labeled decoys: 12/12.

First request: 6.30s. Warm per-candidate median/p95: 1.61/4.16s. Warm complete-search median/p95: 19.29/23.90s.

Maximum sampled combined facade/shim CPU RSS: 0.46 GiB. This excludes Metal allocations and must not be interpreted as model RAM.

| Query | BM25 rank | H2O rank | Gold minus strongest decoy |
|---|---:|---:|---:|
| request_dispatch_pipeline | miss | miss | 0.323 |
| view_return_conversion | 1 | 1 | 0.296 |
| nested_environment_config | 1 | 1 | 1.837 |
| session_cookie_restore | 1 | 1 | 1.751 |
| cleared_session_cookie | 1 | 1 | 1.438 |
| head_method_fallback | 1 | 1 | 1.597 |
| template_loader_fallback | 4 | 1 | 0.906 |
| cli_application_discovery | 1 | 1 | 0.178 |
| streaming_request_context | 1 | 1 | 2.726 |
| json_special_objects | 2 | 1 | 1.190 |
| exception_handler_precedence | 3 | 1 | 1.150 |
| nested_blueprint_prefixes | 1 | 1 | 1.185 |
| absent_builtin_csrf_validation | n/a | n/a | absent feature |
| absent_builtin_rate_limiting | n/a | n/a | absent feature |

## Absent-feature inspection

No rejection threshold was fitted. These are the highest expected ordinal scores among the retrieved candidates (0 = unrelated, 3 = direct implementation).

- absent_builtin_csrf_validation: 0.105/3; `src/flask/ctx.py:AppContext.__init__:300-337`.
- absent_builtin_rate_limiting: 0.308/3; `src/flask/wrappers.py:Request.max_form_parts:119-140`.

## Mac memory footprint

The facade's physical footprint was 6.3G (peak since launch: 7.5G) according to `vmmap -summary`.
This includes Metal allocations. Values are rounded by the macOS tool; the peak belongs to this process, not total system memory. Captured after the completed run.

## Limits

- Developer-authored development pilot; not held out.
- BM25 candidate retrieval only; embeddings and hybrid retrieval not built yet.
- Docstrings are included; unseen repositories and docstring ablations are needed.
- Top-window symbol deduplication can discard a lower-ranked implementation window.
- RSS is sampled after requests, not true peak or total system memory.
- Absence queries do not establish a calibrated rejection threshold.
- No paid-agent token savings are measured in this experiment.

Exact ranked windows, model distributions and timings are in `results.json` and `judgments.jsonl`.
