# Context delivery: seven scored HTTPX spans missing from output

All seven required groups are present as complete literal source windows in the scored hybrid K24 prefix. The original four affected responses contain eight untruncated excerpts each, with 1,498–2,278 compact tokens and 2,384–6,578 source characters. None hits the 4,096-token or 12,000-character cap. Four spans lose the initial eight-symbol eligibility cutoff; three are eligible but lose slots to static callees. There are no duplicate-window losses or partially delivered evidence lines among these seven spans.

| Query / required span | Retrieval rank | Scored rank | Actual evidence lines delivered | Exclusion |
|---|---:|---:|---|---|
| `httpx_json_request_encoding_and_headers`: `Request.__init__` (httpx/_models.py:383–439) | 9 | 9 | none | outside first eight distinct scored symbols |
| `httpx_json_request_encoding_and_headers`: `encode_request` (httpx/_content.py:186–218) | 5 | 8 | none | static callees take remaining slots |
| `httpx_stacked_content_decoding`: `MultiDecoder.decode` (httpx/_decoders.py:216–219) | 20 | 17 | none | outside first eight distinct scored symbols |
| `httpx_stacked_content_decoding`: `MultiDecoder.flush` (httpx/_decoders.py:221–225) | 24 | 19 | none | outside first eight distinct scored symbols |
| `httpx_async_incremental_text_lines`: `LineDecoder.decode` (httpx/_decoders.py:333–369) | 3 | 6 | none | static callees take remaining slots |
| `httpx_async_incremental_text_lines`: `LineDecoder.flush` (httpx/_decoders.py:371–378) | 10 | 10 | none | outside first eight distinct scored symbols |
| `httpx_immutable_query_parameter_updates`: `QueryParams.merge` (httpx/_urls.py:582–598) | 2 | 6 | none | static callees take remaining slots |

The frozen packer first retains eight distinct scored symbols, preserves the first five as anchors, then inserts at most two static callees per anchor before considering anchors six through eight. No scored symbol after the eighth is eligible even when source/token capacity remains. The actual complete line intervals, dependency owners, source-file/evidence/window hashes, and original result IDs are in [diagnostics.json](diagnostics.json). This is a source-only diagnosis of retained outputs, not a new policy replay.

## Development evidence and a bounded next experiment

The full observed-development hybrid K24 run has four additional scored required groups missing from delivery:

| Development query / group | Scored rank | Observed exclusion |
|---|---:|---|
| `starlette_exception_registration_and_dispatch` / group 1 | 6 | dependency_slots_displace_scored_anchor |
| `rich_centered_panel_composition` / group 2 | 8 | dependency_slots_displace_scored_anchor |
| `zod_custom_refinement_issue_context` / group 1 | 11 | outside_eight_symbol_eligible_prefix |
| `zod_metadata_attachment_registry` / group 1 | 8 | dependency_slots_displace_scored_anchor |

These development losses justify separately freezing an experiment about competition between scored anchors and helper slots. They do not establish a safe replacement policy. In particular, a rule that always spends all eight slots on scored anchors would threaten the existing Starlette `starlette_sync_endpoint_worker` success: its required `run_in_threadpool` span is delivered only as a dependency and lies outside scored K24. HTTPX's already complete redirect-header query also relies on two dependency-only groups outside the first eight scored windows: `_same_origin` at scored rank 15 and `_port_or_default` (77–80), absent from the entire raw candidate 50 as well as scored K24. HTTPX has now been observed and cannot serve as a fresh holdout for a policy motivated by these results.

| Existing dependency-only required group | Raw candidate rank | Scored rank | Full evidence in first eight scored symbols? |
|---|---:|---:|---|
| `starlette/starlette_sync_endpoint_worker` / group 2 | 28 | absent from K24 | no |
| `koa/koa_stream_response_lifecycle` / group 1 | 7 | 8 | yes |
| `httpx/httpx_redirect_header_safety` / group 2 | 5 | 15 | no |
| `httpx/httpx_redirect_header_safety` / group 4 | absent from 50 | absent from K24 | no |

The prior prospective Express/Zod containment-plus-backfill replay changed 28/90 contexts and skipped 47 covered prefixes, yet produced zero complete-query or evidence-group gains across all 72 positive query/condition cases. Repeating that control alone has no demonstrated relevance benefit. The current seven HTTPX losses occur without clipping or covered duplicate spans, so they point to rank/slot allocation rather than that earlier containment mechanism.

Recommended next scope: approve and commit a separate development protocol comparing a small, predeclared anchor/helper allocation family on the existing source-bound orders, preserving scored-prefix provenance and measuring all regressions and dependency-only groups. Make no default change from these diagnostics. Candidate-rank losses at 9/10/17/19 need a separate relevance/diversity hypothesis; a dependency-priority repair alone cannot recover them. Any later generalization claim requires a new source-reviewed repository holdout. No control is implemented or scored here.

Checks: pinned literal corpus bytes, full candidate/evidence spans, physical whole-line hashes, saved rank/dedup identities and the prior independent-audit input digests. Compact token counts are inherited from the bound independent audit; no new tokenizer execution or latency measurement is performed. Only standard-library CPU code runs. No production code, earlier result, settings, model environment, inference, network or hosted coding trial is changed.
