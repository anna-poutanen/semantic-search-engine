# Qwen reranker evidence explanations

This report describes observed source delivery and ranking changes in the completed frozen study. It does not infer model intent or establish coding correctness. All comparisons below use the first quality repetition; repeated pair forwards are counted separately.

## Dense

Complete-evidence gains: 4; regressions: 1. Ordered candidate identity/source mismatches: 0.

| Repository | Query | Before ranks | After ranks | Changed evidence |
|---|---|---|---|---|
| click | click_binary_console_output | [None] | [1] | group 1 gained: src/click/utils.py:echo (after origin retrieval) |
| click | click_atomic_option_commit | [None, 3, 1] | [5, 1, 2] | group 1 gained: src/click/types.py:File.convert (after origin retrieval) |
| starlette | starlette_exception_registration_and_dispatch | [4, 2] | [None, 1] | group 1 lost: starlette/middleware/exceptions.py:ExceptionMiddleware.__call__ (after origin none) |
| express | express_configured_query_parser | [None, 2, 1] | [7, 1, 2] | group 1 gained: lib/application.js:app.set (after origin retrieval) |
| express | express_trusted_forwarded_protocol | [None, 1, 2] | [7, 1, 2] | group 1 gained: lib/application.js:app.set (after origin retrieval) |
| zod | zod_nested_deep_partial | [None, None, None] | [None, 3, 3] | group 2 gained: packages/zod/src/v4/core/visit.ts:visit.mapInner (after origin retrieval); group 3 gained: packages/zod/src/v4/core/visit.ts:visit.run (after origin retrieval) |

## Hybrid

Complete-evidence gains: 10; regressions: 2. Ordered candidate identity/source mismatches: 0.

| Repository | Query | Before ranks | After ranks | Changed evidence |
|---|---|---|---|---|
| flask | request_dispatch_pipeline | [None] | [4] | group 1 gained: src/flask/app.py:Flask.full_dispatch_request (after origin retrieval) |
| click | click_binary_console_output | [None] | [1] | group 1 gained: src/click/utils.py:echo (after origin retrieval) |
| click | click_atomic_option_commit | [5, 2, None] | [5, 1, 2] | group 3 gained: src/click/_compat.py:_AtomicFile.close (after origin retrieval) |
| click | click_standalone_usage_error | [1, 4] | [3, None] | group 2 lost: src/click/core.py:BaseCommand.main (after origin none) |
| starlette | starlette_repeated_case_insensitive_headers | [None] | [1] | group 1 gained: starlette/datastructures.py:Headers.getlist (after origin retrieval) |
| starlette | starlette_multipart_field_limit | [None] | [2] | group 1 gained: starlette/formparsers.py:MultiPartParser.on_part_data (after origin retrieval) |
| rich | rich_wide_character_fit | [None] | [1] | group 1 gained: rich/cells.py:set_cell_size (after origin retrieval) |
| rich | rich_console_markup_error_path | [None, 1] | [3, 1] | group 1 gained: rich/console.py:Console.render_str (after origin retrieval) |
| rich | rich_centered_panel_composition | [4, 3] | [8, None] | group 2 lost: rich/align.py:Align.__rich_console__.generate_segments (after origin none) |
| express | express_configured_query_parser | [None, 2, 1] | [7, 1, 2] | group 1 gained: lib/application.js:app.set (after origin retrieval) |
| zod | zod_sync_rejects_async | [None] | [1] | group 1 gained: packages/zod/src/v4/core/parse.ts:_parse, packages/zod/src/v4/core/parse.ts:_parse.fn (after origin retrieval) |
| zod | zod_nested_deep_partial | [None, None, None] | [None, 3, 3] | group 2 gained: packages/zod/src/v4/core/visit.ts:visit.mapInner (after origin retrieval); group 3 gained: packages/zod/src/v4/core/visit.ts:visit.run (after origin retrieval) |
| zod | zod_metadata_attachment_registry | [None, None, 4] | [None, 5, 1] | group 2 gained: packages/zod/src/v4/core/registries.ts:$ZodRegistry.add (after origin retrieval) |
| koa | koa_client_address_trust_and_cache | [None, 1] | [3, 1] | group 1 gained: lib/request.js:module.exports.get ips (after origin retrieval) |

## Source delivery and score-input limits

0 primary condition/group occurrences deliver complete evidence absent from all raw candidates; the report binds their static dependency origins.

0 of 43200 measured pair inputs were source-prefix clipped (0 distinct query/condition/window inputs). 0 primary delivered excerpts extend past their directly scored prefixes. 0 primary Qwen condition/group occurrences deliver complete gold absent from every scored source prefix.

3 primary Python condition/query occurrences have different span-based and historical exact-symbol evidence ranks. The original labels and intervals remain unchanged.

Exact dependency-window membership and evidence-span membership are distinct: an unranked callee window can overlap a ranked enclosing window. A returned suffix can also be absent from its direct scoring pair but present in another scored prefix. See the complete machine report for both distinctions.

Warm latency, scalar/cap checks, complete forward reconciliation and runtime cleanup are established by the separate independent audit. These source diagnostics do not replace it.
