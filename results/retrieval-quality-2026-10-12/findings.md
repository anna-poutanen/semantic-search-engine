# Findings and remaining failures

This run made 448 fresh HTTP searches for 106 frozen, source-reviewed queries across five pinned Python repositories. There are 88 present behaviors, 18 absent cases, and ten multi-file AND behaviors. Rich's 20 present and four absent queries were reserved as a repository holdout. Configurations and source labels were frozen before inference; the weak-match threshold was fitted only to the declared Click/Starlette calibration rows before Rich retrieval. [Raw results](searches.jsonl), [all metrics](results.json), [configuration](frozen-config.json), and the [protocol](../../docs/retrieval-quality-protocol.md) preserve the details.

The [independent CPU audit](audit.json) passed all 448 rows, 2,574 delivered excerpts, and 22,400 candidate-window references. It recomputes source slices/hashes, exact serialized token counts, evidence metrics, and calibration provenance. It does not regenerate GPU scores or prove a complete runtime call graph. Policy execution order is supported by the frozen code and ordering tests; per-row wall timestamps were not recorded.

Reproduce the audit from this checkout with the pinned corpora fetched:

```sh
.venv/bin/python scripts/audit_quality.py \
  --run results/retrieval-quality-2026-10-12 --output .cache/quality-audit.json
```

## Delivered evidence and candidate coverage

All modes use a 12,000-source-character limit and a 4,096-token compact JSON cap measured with pinned `cl100k_base`. Timing samples include present and absent queries. Candidate coverage measures all required groups in the first 50 full windows; delivered coverage instead checks the exact retained excerpts, after ranking, deduplication, result limits, and packing.

| Mode | All present: candidate / delivered | Rich present: candidate / delivered | Rich complete multi-file behaviors | Rich HTTP P95 |
|---|---|---|---|---|
| FTS5, five results | 85/88 / 66/88 | 20/20 / 12/20 | 2/4 | 133 ms |
| Dense, five results | 87/88 / 81/88 | 19/20 / 18/20 | 2/4 | 174 ms |
| Hybrid, five results | 88/88 / 78/88 | 20/20 / 18/20 | 3/4 | 171 ms |
| Dense context, up to eight results | 87/88 / 83/88 | 19/20 / 18/20 | 2/4 | 175 ms |

Dense remains the fast default. The MCP context configuration delivers complete evidence for 94.3% of the 88 positive cases overall and 90% of the 20 held-out positives. Hybrid retains every positive group's candidate evidence at depth 50, but its five-result ordering loses more delivered evidence overall. These counts are authored benchmark outcomes, not a guarantee for arbitrary repositories or queries.

Dense context preserves five anchors, adds bounded static callees, and fills unused slots with later anchors. Its two additional successes over dense-five split between one static dependency (`run_in_threadpool`) and one additional dense anchor (`Mount.matches`). The [post-hoc budget analysis](budget-analysis.md) records the origins and traces incomplete evidence through the selection stages. This run compares one declared output budget; narrower-budget repacking is separately labeled as a derived analysis rather than fresh inference or a newly held-out configuration.

Repacking the saved default-context outputs at 512, 1,024, 2,048, and 4,096 tokens retains complete evidence for 40, 71, 80, and 83 of 88 positive queries respectively. These derived outputs verify exact token caps and source integrity but do not measure new request latency or choose a default. The original 4,096-token configuration remains fixed.

## Weak-match rejection stays optional

The [frozen policy](abstention-policy.json) uses a threshold of **0.5642289817333221** for the highest dense cosine. It retains all 20 positive calibration queries and rejects three of four absent calibration queries. On Rich it rejects three of four absent cases, accepts one absent case, and falsely rejects **one of 20 present behaviors**. That false rejection is `rich_centered_panel_composition`, whose top cosine is 0.5354914367198944. Its evidence was already missing from the default delivered context, so delivered coverage stays at 18/20; the false rejection must still be counted.

The four negative calibration cases do not justify a universal rejection boundary. The policy remains opt-in and bound to the full model/settings metadata. `insufficient_evidence` means weak semantic evidence, not proof that code or a behavior is absent. No threshold was retuned on Rich.

## Five incomplete default-context queries

| Query | Complete evidence in candidates? | Observed limitation |
|---|---|---|
| `click_binary_console_output` | Yes, rank 31 | Low-ranked implementation is outside returned anchors/dependencies |
| `click_atomic_option_commit` | Yes, group ranks 13, 3, 1 | File-option conversion is missing while creation/commit helpers are returned |
| `click_standalone_usage_error` | Yes, group ranks 1, 24 | Usage printing is found; termination path is missing |
| `rich_console_markup_error_path` | No, one group outside depth 50 | Markup error handling is found; console parsing selection is missing |
| `rich_centered_panel_composition` | Yes, group ranks 6, 12 | Borders and centering implementations lose slots to earlier anchors/static helpers |

These failures preserve the original labels and ranks. Follow-up improvements to candidate/window selection or multi-file context need development evaluation and a fresh repository holdout if Rich informs those choices. The static graph cannot resolve arbitrary dynamic dispatch, inherited methods, factories, or a complete runtime execution path.

## Indexing and live MCP verification

Initial setup indexed 358 Flask, 240 Requests, 474 Click, 472 Starlette, and 873 Rich windows from scratch. Per-repository setup, including model loading and warmup, ranged from 14.9 to 52.4 seconds. Warm request timings exclude that setup and recompute every semantic query embedding; there is no cross-request query cache. SQLite FTS5 and float32 sqlite-vec differ from the earlier Python BM25/NumPy pilot engines, so prior H2O results are separate evidence.

The [live smoke result](mcp-smoke.json) exercised real Qwen through the CLI, HTTP service, and official stdio MCP client. It initialized/listed `search_code`, verified exact source/hash/line bounds and compact token counts, observed one saved edit refresh through status-only polling, and confirmed one new embedding plus two reused vectors. The next MCP call returned the changed source hash. SIGTERM stopped the CLI and watcher with exit status zero. This three-function integration check is separate from retrieval-quality measurements.

The [10,000-window index profile](../index-profile-2026-10-12/report.md) uses synthetic CPU vectors. It validates durable reuse and exact SQLite ordering at that size; it does not measure real Qwen indexing at 10,000 windows. Larger real-model scale, other languages, unsaved editor buffers, VS Code, and paired-agent correctness/provider-token evaluation remain future work.
