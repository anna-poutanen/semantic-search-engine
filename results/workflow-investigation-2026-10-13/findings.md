# Why the guided MCP pilot cost more

MCP produced fewer shell calls (33 versus 50) and less captured output when each MCP text payload is counted once (35,029 versus 37,413 cl100k_base tokens). It still used 6.97% more provider-reported input plus output and took 14.78% longer. Raw output volume alone therefore does not explain the regression.

The local token counts below are diagnostic proxies over published, path-redacted strings. Actual provider usage is available only for each completed turn. These logs contain neither request-level receipts nor tool/phase timestamps, so they cannot establish actual tokens or elapsed time per call. Cached input and reasoning output are subsets, not additional tokens. No monetary billing claim is made.

| Recorded metric across three pairs | Baseline | MCP | Change |
|---|---:|---:|---:|
| Actual input + output tokens | 578,218 | 618,537 | +6.97% |
| Actual uncached input tokens | 115,984 | 144,935 | +24.96% |
| Uncached input + all output tokens | 127,914 | 157,225 | +22.91% |
| Cached share of input | 79.5% | 76.1% | -3.43 percentage points |
| Agent CLI seconds | 426.78 | 489.87 | +14.78% |
| Setup seconds, excluded above | 0.79 | 62.22 | Includes fresh MCP indexing/warmup |
| Complete trial seconds | 427.94 | 553.48 | +29.34% |

Caching differs descriptively between conditions. In particular, the uncached increase is dominated by the Starlette pair. Without request-level data, cache-hit differences cannot be assigned to MCP, model batching, source rereads or any particular tool. The setup totals describe fresh per-trial indexes, not the cost of every query against a resident service.

| Task | Actual baseline tokens | Actual MCP tokens | Token change | Baseline seconds | MCP seconds |
|---|---:|---:|---:|---:|---:|
| requests_auth_ports | 221,712 | 180,788 | -18.46% | 138.68 | 141.92 |
| click_atomic_close | 142,824 | 199,162 | +39.45% | 110.36 | 138.61 |
| starlette_background_order | 213,682 | 238,587 | +11.66% | 177.75 | 209.33 |

Requests used fewer actual total tokens with MCP; Click and Starlette used more. This is one run per task and condition. Different test code, batching, reasoning and provider latency remain confounders, and these results do not isolate a stable causal effect.

| Exclusive command/output phase | Baseline calls | MCP calls | Baseline local output proxy | MCP local output proxy |
|---|---:|---:|---:|---:|
| debug | 4 | 1 | 96 | 18 |
| edit_review | 5 | 6 | 2,129 | 2,245 |
| mcp_search | 0 | 3 | 0 | 2,855 |
| mixed_search_source_read | 3 | 3 | 6,756 | 4,711 |
| search | 12 | 7 | 4,865 | 5,290 |
| source_read | 10 | 4 | 8,798 | 8,743 |
| test | 12 | 11 | 14,753 | 11,167 |
| workspace | 4 | 1 | 16 | 0 |

Mixed search/read commands remain mixed because their combined stdout cannot be reliably divided among subprocesses. Test output includes expected seed failures, successful checks and environment failures. The JSON retains every command, tags, output size and exit code for inspection.

8 of 12 retrieved excerpts appear again in full in later source/search outputs: one of three Requests excerpts, three of four Click excerpts, and four of five Starlette excerpts. MCP did not consistently replace those reads. This is a concrete workflow inefficiency, but it cannot be assigned a numerical share of the measured provider overhead: total single-copy captured output was still lower with MCP.

All three MCP results contain equivalent text and structured copies of the same envelope. The text payloads contain only 1,101, 908 and 846 local tokens, respectively. Agents requested three to five results, and two calls disabled dependency expansion. Large default payload budgets were therefore not the observed pattern. The recorded transport has another 2,855-token equivalent structured copy; whether Codex fed both representations to the model is unobserved. Removing duplicate transport data is a reasonable intervention, not a proven explanation for actual token growth.

Starlette dependency-warning/incompatibility outputs total 36,527 bytes (9,127 local tokens) in baseline and 28,506 bytes (6,951 local tokens) in MCP. Baseline emitted more of this output. The AnyIO/Trio problem made both workflows noisy and changed their testing strategies; it is not an observed cause of the additional MCP usage or time.

Both Starlette repairs replaced the supplied HTTP TestClient failure test with direct BackgroundTasks invocation. Direct tests were stronger for callback order and sync/async failure, but avoided an incompatible HTTP cleanup path and removed its integration coverage. Both Click repairs passed selected success checks while suppressing atomic commit failures through safecall. These differences show why passing the ten declared verifier methods is weaker than full correctness.

The next experiment should make retrieval replace broad source reads, return one concise context representation, and prepare a compatible isolated test environment before timing. Keep full logs outside the model context and return bounded failure summaries, without hiding failure status or weakening required integration checks. Include larger tasks whose repairs and gates span several production files; retain absent/negative controls and atomic-write failure checks.

Prospectively timestamp each event at capture so tool and queue durations can be measured. If the runner exposes request-level usage updates, preserve them as receipts; otherwise continue reporting turn totals without invented phase attribution. Freeze the new tasks, gates, context format and common workflow policy before running either condition, with repeated or balanced trials when account limits permit.

Reproduce the analysis with `.venv/bin/python scripts/analyze_workflow_costs.py`. It uses cached cl100k_base data, forbids tokenizer downloads, does not execute saved commands, and preserves every prior artifact. [Machine-readable analysis](analysis.json) contains receipt validation, phase counts, reread evidence and input hashes.
