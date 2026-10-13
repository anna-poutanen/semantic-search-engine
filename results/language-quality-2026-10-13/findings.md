# Frozen JavaScript/TypeScript retrieval results

270 requests cover 30 unique queries: 24 present, 6 scoped absent. Quality uses repetition 1; later repetitions measure latency/stability, not additional query evidence.

JavaScript development/calibration and TypeScript retrieval holdout are reported separately. Zod syntax and executable-evidence eligibility were inspected and the generic extractor adapted before inference; labels and ranking settings stayed fixed. H2O and abstention are off. These results do not establish coding correctness, absence guarantees or provider token savings.

| Dataset / role | Condition | Candidate complete /12 | Delivered complete /12 | Candidate / delivered groups | Multi-file complete /4 | MRR | Median / p95 HTTP ms (45) | Truncated /15 | Max context tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| express / development | bm25 | 12 | 10 | 19 / 17 of 19 | 2 | 0.646 | 77.3 / 83.1 | 0 | 3052 |
| express / development | dense | 12 | 10 | 19 / 17 of 19 | 2 | 0.597 | 118.7 / 138.3 | 0 | 2232 |
| express / development | hybrid | 12 | 11 | 19 / 18 of 19 | 3 | 0.632 | 119.4 / 127.8 | 0 | 2600 |
| zod / repository_holdout | bm25 | 7 | 7 | 12 / 11 of 19 | 0 | 0.364 | 161.9 / 217.1 | 10 | 4092 |
| zod / repository_holdout | dense | 11 | 8 | 18 / 13 of 19 | 0 | 0.528 | 203.1 / 387.7 | 5 | 4092 |
| zod / repository_holdout | hybrid | 11 | 7 | 18 / 11 of 19 | 0 | 0.500 | 212.9 / 304.5 | 5 | 4092 |

Complete query coverage requires every AND group; alternatives within a group are OR. The first complete rank is the largest required group rank, including a shared window that covers several groups. Candidate depth 50 and delivered limits of 8 excerpts, 12,000 source characters and 4,096 compact JSON tokens were fixed. Token counts include scope metadata and describe local payloads, not billing.

Later repetitions provide 30 latency samples per dataset/condition in addition to 15 first-repetition samples. Candidate reconstruction and source verification occur outside HTTP timers. Repeated requests do not supply 90 unique queries.

Later repetitions had 0 candidate/delivered evidence-rank coverage changes relative to the first repetition; the exact changed query IDs are retained in analysis.json.

## Every first-repetition miss

Across 27 missing required groups, 9 had no complete candidate window, 17 had complete evidence beyond the first 8 raw candidates, and 1 had a complete candidate within the first 8 that was not delivered. Full evidence was cut inside an actually returned window for 0 groups.

Candidate ranks refer to raw windows before symbol deduplication/dependency expansion. A rank beyond 8 does not by itself establish why delivery failed. A cut flag means a returned original window contained the full evidence, but its delivered prefix did not.

| Dataset / condition | Query | Candidate ranks | Delivered ranks | Recorded gap / budget flags |
|---|---|---|---|---|
| express / hybrid | `express_configured_query_parser` | [9, 2, 1] | [None, 2, 1] | g1: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=False, token_truncated=False |
| express / dense | `express_configured_query_parser` | [24, 2, 1] | [None, 2, 1] | g1: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=False, token_truncated=False |
| express / dense | `express_trusted_forwarded_protocol` | [14, 1, 2] | [None, 1, 2] | g1: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=False, token_truncated=False |
| express / bm25 | `express_view_cache_and_lookup` | [1, 4, 22] | [1, 4, None] | g3: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=False, token_truncated=False |
| express / bm25 | `express_callable_app_dispatch_context` | [14, 4] | [None, 4] | g1: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=False, token_truncated=False |
| zod / bm25 | `zod_sync_rejects_async` | [None] | [None] | g1: no complete evidence window in retrieved candidates; budget_exhausted=True, token_truncated=False |
| zod / hybrid | `zod_sync_rejects_async` | [14] | [None] | g1: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=False, token_truncated=False |
| zod / hybrid | `zod_safe_parse_lazy_error_redaction` | [None, 2, 1] | [None, 2, 1] | g1: no complete evidence window in retrieved candidates; budget_exhausted=False, token_truncated=False |
| zod / bm25 | `zod_safe_parse_lazy_error_redaction` | [None, 3, 2] | [None, 3, 2] | g1: no complete evidence window in retrieved candidates; budget_exhausted=True, token_truncated=False |
| zod / dense | `zod_safe_parse_lazy_error_redaction` | [None, 2, 1] | [None, 2, 1] | g1: no complete evidence window in retrieved candidates; budget_exhausted=False, token_truncated=False |
| zod / bm25 | `zod_custom_refinement_issue_context` | [None, 3] | [None, 3] | g1: no complete evidence window in retrieved candidates; budget_exhausted=False, token_truncated=False |
| zod / dense | `zod_custom_refinement_issue_context` | [11, 1] | [None, 1] | g1: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=False, token_truncated=False |
| zod / hybrid | `zod_custom_refinement_issue_context` | [21, 1] | [None, 1] | g1: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=False, token_truncated=False |
| zod / dense | `zod_nested_deep_partial` | [17, 26, 26] | [None, None, None] | g1: complete evidence candidate exists but was not delivered; candidate rank>8; g2: complete evidence candidate exists but was not delivered; candidate rank>8; g3: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=False, token_truncated=False |
| zod / hybrid | `zod_nested_deep_partial` | [35, 49, 49] | [None, None, None] | g1: complete evidence candidate exists but was not delivered; candidate rank>8; g2: complete evidence candidate exists but was not delivered; candidate rank>8; g3: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=True, token_truncated=False |
| zod / bm25 | `zod_nested_deep_partial` | [None, None, None] | [None, None, None] | g1: no complete evidence window in retrieved candidates; g2: no complete evidence window in retrieved candidates; g3: no complete evidence window in retrieved candidates; budget_exhausted=True, token_truncated=False |
| zod / hybrid | `zod_metadata_attachment_registry` | [8, 17, 4] | [None, None, 4] | g1: complete evidence candidate exists but was not delivered; g2: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=True, token_truncated=False |
| zod / bm25 | `zod_metadata_attachment_registry` | [4, None, 18] | [4, None, None] | g2: no complete evidence window in retrieved candidates; g3: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=True, token_truncated=False |
| zod / dense | `zod_metadata_attachment_registry` | [24, 1, 3] | [None, 1, 3] | g1: complete evidence candidate exists but was not delivered; candidate rank>8; budget_exhausted=False, token_truncated=False |

Full missing-group diagnostics in analysis.json also retain first-distinct-symbol ranks, original/delivered bounds for cuts, and actual dependency excerpt counts. These observations do not identify a causal model-ranking mechanism.

## Scoped absent queries and returned decoys

No abstention threshold was fitted. Returning candidates for an absent query is a recorded decoy outcome, not proof the requested behavior exists. Every first-repetition negative/condition stays listed.

| Dataset / condition | Query | Status | Delivered decoys (path:symbol@lines) |
|---|---|---|---|
| express / bm25 | `express_absent_builtin_rate_limit` | candidates | `lib/request.js:<module>.isIP@17-17`; `lib/response.js:res.jsonp@264-308`; `lib/request.js:protocol@297-315`; `lib/response.js:res.header@670-692`; `lib/request.js:req.header@63-83`; `lib/response.js:stringify@1026-1050`; `lib/response.js:res.append@635-647`; `lib/response.js:res.send@126-222` |
| express / dense | `express_absent_builtin_rate_limit` | candidates | `lib/response.js:sendfile.onfinish@968-983`; `lib/response.js:sendfile.onaborted@929-936`; `lib/request.js:<module>@469-486`; `lib/request.js:ips@357-366`; `lib/request.js:ip@340-343`; `lib/response.js:sendfile.onerror@949-953`; `lib/request.js:defineGetter@521-527`; `lib/response.js:<module>.http@20-20` |
| express / hybrid | `express_absent_builtin_rate_limit` | candidates | `lib/request.js:protocol@297-315`; `lib/request.js:ip@340-343`; `lib/request.js:ips@357-366`; `lib/request.js:<module>.isIP@17-17`; `lib/request.js:<module>@469-486`; `lib/request.js:defineGetter@521-527`; `lib/response.js:res.jsonp@264-308`; `lib/response.js:res.redirect@815-867` |
| express / dense | `express_absent_outbound_retry` | candidates | `lib/response.js:sendfile.onaborted@929-936`; `lib/response.js:sendfile.onfinish@968-983`; `lib/response.js:sendfile@924-1012`; `lib/request.js:<module>.http@19-19`; `lib/response.js:<module>.http@20-20`; `lib/response.js:sendfile.onerror@949-953`; `lib/response.js:<module>.createError@16-16`; `lib/response.js:res.redirect@815-867` |
| express / hybrid | `express_absent_outbound_retry` | candidates | `lib/request.js:<module>.http@19-19`; `lib/response.js:<module>.http@20-20`; `lib/application.js:<module>.http@19-19`; `lib/response.js:<module>.createError@16-16`; `lib/response.js:res.redirect@815-867`; `lib/request.js:xhr@508-511`; `lib/request.js:<module>.req@30-30`; `lib/response.js:res.status@65-77` |
| express / bm25 | `express_absent_outbound_retry` | candidates | `lib/utils.js:exports.compileTrust@194-214`; `lib/request.js:xhr@508-511`; `lib/application.js:<module>.http@19-19`; `lib/request.js:<module>.http@19-19`; `lib/response.js:<module>.http@20-20`; `lib/response.js:<module>.createError@16-16`; `lib/utils.js:<module>.<field@15:5>@15-15`; `lib/request.js:<module>.req@30-30` |
| express / hybrid | `express_absent_cookie_encryption_rotation` | candidates | `lib/response.js:res.cookie@748-781`; `lib/request.js:protocol@297-315`; `lib/response.js:res.format@574-599`; `lib/response.js:<module>.cookie@30-30`; `lib/response.js:<module>.res@43-43`; `lib/utils.js:exports.normalizeType@61-65`; `lib/utils.js:exports.normalizeTypes@75-77`; `lib/response.js:res.send@126-222` |
| express / bm25 | `express_absent_cookie_encryption_rotation` | candidates | `lib/response.js:res.cookie@748-781`; `lib/response.js:res.status@65-77`; `lib/response.js:res.download@437-486`; `lib/request.js:xhr@508-511`; `lib/response.js:res.format@574-599`; `lib/utils.js:exports.normalizeType@61-65`; `lib/utils.js:exports.normalizeTypes@75-77`; `lib/response.js:res.header@670-692` |
| express / dense | `express_absent_cookie_encryption_rotation` | candidates | `lib/response.js:res.cookie@748-781`; `lib/response.js:<module>.cookie@30-30`; `lib/response.js:<module>.sign@26-26`; `lib/response.js:res.clearCookie@715-722`; `lib/request.js:secure@326-328`; `lib/request.js:<module>@469-486`; `lib/response.js:<module>.res@43-43`; `lib/request.js:protocol@297-315` |
| zod / bm25 | `zod_absent_remote_ref_fetch` | candidates | `packages/zod/src/v4/core/to-json-schema.ts:finalize@688-758`; `packages/zod/src/v4/core/to-json-schema.ts:extractDefs.makeURI@330-361`; `packages/zod/src/v4/core/visit.ts:visit.run@42-57`; `packages/zod/src/v4/core/to-json-schema.ts:extractDefs@301-441`; `packages/zod/src/v4/core/to-json-schema.ts:foldIntersection@541-566`; `packages/zod/src/v4/core/to-json-schema.ts:finalize.flattenRef@576-601` |
| zod / dense | `zod_absent_remote_ref_fetch` | candidates | `packages/zod/src/v4/classic/from-json-schema.ts:resolveRef@132-155`; `packages/zod/src/v4/core/to-json-schema.ts:extractDefs@301-441`; `packages/zod/src/v4/core/to-json-schema.ts:extractDefs.makeURI@330-361`; `packages/zod/src/v4/core/to-json-schema.ts:finalize@688-758`; `packages/zod/src/v4/core/to-json-schema.ts:finalize.flattenRef@576-631` |
| zod / hybrid | `zod_absent_remote_ref_fetch` | candidates | `packages/zod/src/v4/core/to-json-schema.ts:finalize@688-758`; `packages/zod/src/v4/core/to-json-schema.ts:extractDefs.makeURI@330-361`; `packages/zod/src/v4/core/to-json-schema.ts:extractDefs@301-441`; `packages/zod/src/v4/core/visit.ts:visit.run@42-57`; `packages/zod/src/v4/core/compile.ts:generateLazyCheck@2161-2184`; `packages/zod/src/v4/core/to-json-schema.ts:finalize.flattenRef@576-604` |
| zod / dense | `zod_absent_async_retry_backoff` | candidates | `packages/zod/src/v4/core/parse.ts:validateFallback@151-172`; `packages/zod/src/v4/classic/schemas.ts:validateAsync@319-321`; `packages/zod/src/v4/core/schemas.ts:standardProps.validate@348-356`; `packages/zod/src/v4/core/schemas.ts:validateAsync@341-344`; `packages/zod/src/v4/core/compile.ts:throwAsync@675-677`; `packages/zod/src/v4/core/schemas.ts:toStandardResult@338-339`; `packages/zod/src/v4/core/parse.ts:validateAsync@181-188`; `packages/zod/src/v4/core/schemas.ts:$ZodType.<block@232:12>.runChecks.<block@243:34>.<block@257:52>.asyncResult@258-264` |
| zod / hybrid | `zod_absent_async_retry_backoff` | candidates | `packages/zod/src/v4/core/compile.ts:generateCustomCheck@2229-2248`; `packages/zod/src/v4/core/core.ts:newError@30-49`; `packages/zod/src/v4/core/compile.ts:generateCustomRefineCheck@684-726`; `packages/zod/src/v4/core/schemas.ts:handleCatchResult@4345-4363`; `packages/zod/src/v4/core/parse.ts:validateFallback@151-172`; `packages/zod/src/v4/core/compile.ts:isAsyncFunction@2221-2227`; `packages/zod/src/v4/core/compile.ts:addUserConstant@298-301`; `packages/zod/src/v4/core/schemas.ts:handleOptionalResult@3925-3929` |
| zod / bm25 | `zod_absent_async_retry_backoff` | candidates | `packages/zod/src/v4/core/schemas.ts:handleOptionalResult@3925-3929`; `packages/zod/src/v4/core/compile.ts:generateCustomCheck@2229-2248`; `packages/zod/src/v4/core/core.ts:newError@30-49`; `packages/zod/src/v4/core/compile.ts:runtimeCatch@2251-2261`; `packages/zod/src/v4/core/compile.ts:generateIntersectionCheck@1932-1945`; `packages/zod/src/v4/core/compile.ts:isAsyncFunction@2221-2227`; `packages/zod/src/v4/core/compile.ts:addUserConstant@298-301`; `packages/zod/src/v4/core/compile.ts:compileChild@332-356` |
| zod / hybrid | `zod_absent_issue_encryption` | candidates | `packages/zod/src/v4/core/schemas.ts:handleIntersectionResults@2915-2967`; `packages/zod/src/v4/core/compile.ts:generateRecordCheck@1947-2061`; `packages/zod/src/v4/core/util.ts:defineLazyInternal.get@1235-1253`; `packages/zod/src/v4/locales/he.ts:error@125-175`; `packages/zod/src/v4/core/util.ts:defineLazyInternal@1220-1221` |
| zod / bm25 | `zod_absent_issue_encryption` | candidates | `packages/zod/src/v4/core/util.ts:defineLazyInternal.get@1235-1253`; `packages/zod/src/v4/core/util.ts:defineLazyInternal@1220-1258`; `packages/zod/src/v4/core/compile.ts:generatePropertiesChecks@616-637`; `packages/zod/src/v4/core/schemas.ts:$ZodDefault.inst._zod.parse@4090-4109`; `packages/zod/src/v4/core/schemas.ts:handleIntersectionResults@2915-2967`; `packages/zod/src/v4/core/compile.ts:addConstant@287-295`; `packages/zod/src/v4/core/util.ts:esc@496-498`; `packages/zod/src/v4/core/schemas.ts:handleDefaultResult@4113-4118` |
| zod / dense | `zod_absent_issue_encryption` | candidates | `packages/zod/src/v4/locales/he.ts:error@125-247`; `packages/zod/src/v4/locales/ar.ts:error@5-114`; `packages/zod/src/v4/locales/id.ts:error@5-5` |

## Source, reuse and memory evidence

All 270 saved HTTP responses independently pass exact source, physical-line, evidence-rank and compact-token checks. The contemporaneous runtime-audit.json was recorded before subsequent authorized code changes; this report reads that historical proof without loading weights or rerunning inference.

| Dataset | Files / windows | Cold index+model warm s | Document calls / texts | Query calls incl 1 warmup | Reopen reused / total; calls | Physical footprint setup / after queries; lifetime peak |
|---|---:|---:|---:|---:|---|---|
| express | 6 / 190 | 6.61 | 1 / 190 | 91 | 190 / 190; 0 query/0 document | 1.4G / 1.8G; 2.0G |
| zod | 107 / 1802 | 108.23 | 1 / 1793 | 91 | 1802 / 1802; 0 query/0 document | 1.5G / 1.7G; 2.3G |

The unchanged query phase performed no document embedding/index refresh work; fresh reopened backends reused every window with zero embedding calls. Document-input counts and indexed-window counts are separate quantities. vmmap measurements are rounded physical footprints outside HTTP timers; process-lifetime peaks include earlier datasets and cannot be summed or treated as isolated model VRAM.

The authored sample is small. The repository/language holdout is not a claim about model pretraining. Saved edits, unsaved overlays, real editor workflows and later coding correctness require their own evidence. No parameter change or threshold is selected from these outcomes.

The complete embedding revision, extractor policy, fusion and dependency parameters remain in frozen-config.json and analysis.json. Reproduce reporting with `scripts/report_language_quality.py --study <saved-study>` and the pinned corpus bytes. No evaluation HTTP request or model call is made.
