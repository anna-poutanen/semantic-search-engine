# Allocation preflight diagnosis

Post hoc descriptive source/query analysis of already observed DEVELOPMENT cases; no new selector or outcome evaluation.

Input commit: `63a34ebcae6a01209a93e0c0439b7cc5b47fe649`. [Exact rows, source hashes and method](diagnosis.json). No model, indexing, retrieval, network, or new variant calls.

The 61 omitted full-group occurrences cover 44 saved condition rows and 23 distinct dataset/query units. Of these, 57 occurrences are in 40 multi-file rows; four are in four single-file rows. There are 53 occurrences beyond selected rank 8; eight were in ranks 6–8 and displaced by the existing dependency allocation. Those eight share seven distinct units because Zod metadata hybrid appears in both observed language and native studies. These are repeated condition/group occurrences, not independent trials.

Of 61 occurrences, 30 have a target in a path already delivered and 31 require a new delivered path. The best target selected rank has median 17 and maximum 50. Exact query-word novelty exists in 15/61 occurrences under the **handwritten descriptive stopwords and tokenizer recorded in JSON**. This is not a proposed selector or a prospective quality criterion. It shows why exact lexical set cover alone is an uncertain explanation.

| Dataset/query unit | Saved rows | Omitted group occurrences | Best target ranks across rows | Dependency displacement |
|---|---:|---:|---|---|
| express/express_callable_app_dispatch_context | 1 | 1 | 14 |  |
| express/express_configured_query_parser | 4 | 4 | 9, 24 |  |
| express/express_trusted_forwarded_protocol | 2 | 2 | 14 |  |
| express/express_view_cache_and_lookup | 1 | 1 | 22 |  |
| httpx/httpx_async_incremental_text_lines | 1 | 2 | 6, 10 | yes |
| httpx/httpx_immutable_query_parameter_updates | 1 | 1 | 6 | yes |
| httpx/httpx_json_request_encoding_and_headers | 1 | 2 | 8, 9 | yes |
| httpx/httpx_stacked_content_decoding | 1 | 2 | 17, 19 |  |
| jinja/jinja_bytecode_reject_and_recompile | 1 | 1 | 16 |  |
| jinja/jinja_file_change_invalidates_template_cache | 2 | 2 | 9, 23 |  |
| jinja/jinja_filename_autoescape_to_generated_output | 2 | 4 | 12, 18, 28, 37 |  |
| jinja/jinja_include_parent_and_local_context | 2 | 2 | 11, 27 |  |
| jinja/jinja_sandbox_attribute_security_placeholder | 1 | 1 | 32 |  |
| rich/rich_centered_panel_composition | 1 | 1 | 8 | yes |
| urllib3/urllib3_bounded_read_across_queued_chunks | 1 | 1 | 8 | yes |
| urllib3/urllib3_elapsed_total_budget_to_socket_read | 2 | 4 | 11, 50 |  |
| urllib3/urllib3_redirect_origin_check_and_header_removal | 1 | 1 | 6 | yes |
| urllib3/urllib3_release_response_connection_to_bounded_pool | 1 | 1 | 15 |  |
| urllib3/urllib3_response_status_retry_and_drain | 2 | 2 | 15, 17 |  |
| zod/zod_custom_refinement_issue_context | 5 | 5 | 11, 21 |  |
| zod/zod_metadata_attachment_registry | 5 | 7 | 7, 16, 17, 23 | yes |
| zod/zod_nested_deep_partial | 4 | 12 | 17, 26, 34, 48 |  |
| zod/zod_sync_rejects_async | 2 | 2 | 14 |  |

Representative fixed traces:

- **Express configured parser, hybrid:** `app.set` at selected rank 9 writes `query parser fn` and calls `compileQueryParser`; the delivered prefix contains the invocation at 1, compiler at 2, extended helper at 3 and compiler import at 4. The missing aspect is the orchestration caller, in an already delivered path. Dense puts that same setter at 24.
- **Express view lookup, BM25:** returned `View.prototype.lookup` at 4 calls `this.resolve(dir,file)`, while `View.prototype.resolve` at 22 implements direct-file and directory-index alternatives. Both are in the same file. Runtime receiver names must not become invented static provenance.
- **HTTPX incremental lines, observed Qwen order:** `Response.aiter_lines` at 1 constructs `LineDecoder`, calls `decode` and `flush`; `LineDecoder.decode` at 6 is displaced by dependency expansion and `LineDecoder.flush` at 10 is outside the eight slots. Actual selected dependencies include `request_context`, `aiter_bytes` and `iter_bytes`.
- **HTTPX stacked decoding:** `MultiDecoder.__init__` at 2 documents and reverses the order; `MultiDecoder.decode` at 17 and `flush` at 19 perform transformation and tail propagation. Relevant query words already occur in the prefix. Same-owner behavior differs from constructors.
- **urllib3 redirects, dense:** `PoolManager.urlopen` at 1 calls `conn.is_same_host`; `HTTPConnectionPool.is_same_host` at 6 implements scheme/host/default-port comparison, displaced by `parse_url`, `connection_from_host` and `_normalize_host`. The current graph deliberately cannot resolve a receiver returned by a factory.
- **Rich panel centering:** `Align.__init__` at 5 mentions left/center/right, but actual bilateral padding occurs in `Align.__rich_console__` at 8 or its nested generator at 10, displaced by existing dependencies. Constructor keyword matching does not establish behavioral coverage.

The eight displacement occurrences are: Zod metadata hybrid twice; Rich panel composition; HTTPX JSON request encoding, incremental lines, immutable query parameters; urllib3 redirect origin and queued chunks. HTTPX immutable query parameters and urllib3 queued chunks are single-file cases, so a multi-file-only switch would miss them.

Preservation risks: the earlier rank-preserving control lost seven full groups across four complete condition rows, including Jinja `async_variant`/`auto_aiter`, Starlette `run_in_threadpool`, and HTTPX `_same_origin`/`_port_or_default`, each supplied by real dependency evidence. Retaining a fixed ranked prefix or deleting dependency slots repeats a measured failure mode. Any source/query aspect proposal needs both-control, all-group, complete-query and single-file gates, with packing and source checks under the same caps.

Forty-two of 44 affected case rows have no other classified missing cause, but this is not an achievable-gain claim: source/token space, the eight slots, and preserving existing groups remain binding. The other rows are Zod metadata BM25 (another group absent from the input pool) and urllib3 retry hybrid (another group lost during packing).

No fresh TanStack source, labels, retrieval, or outcomes were accessed for this diagnosis.
