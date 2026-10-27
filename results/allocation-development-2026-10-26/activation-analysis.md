# Fixed allocation outcome diagnosis

Post hoc descriptive activation and loss analysis of fixed independently audited DEVELOPMENT outcomes.

The canonical outcome audit passed on the first attempt: `39ea33f1b11e4a9b9072eb74effc0ea7b02e2dea7c18c68a7de3499eb7390ba2`. This later descriptive analysis is separate from that audit. [Exact input hashes, all loss records and method](activation-analysis.json). No new variant, model, index, retrieval or network calls.

All counts below are occurrences across shared conditions. Each candidate has 365 rows covering 187 distinct dataset/query units.

| Candidate | Clause active rows / units | Accepted rows | Changed eligible rows / units | Changed compact rows / units |
|---|---:|---:|---:|---:|
| aspect_fill | 329 / 172 | 160 | 151 / 85 | 146 / 85 |
| aspect_balanced | 329 / 172 | 275 | 274 / 150 | 272 / 150 |

| Candidate | Additional distinct complete units vs coverage | Lost groups vs legacy / coverage | Lost complete rows vs legacy / coverage | Qualified |
|---|---:|---:|---:|---|
| aspect_fill | 1 | 4 / 4 | 4 / 4 | no |
| aspect_balanced | 3 | 11 / 12 | 9 / 10 | no |

The five-anchor candidate gains `jinja_bytecode_reject_and_recompile`, but loses `app.set` in four saved rows across two distinct Express queries. Its literal query words are already represented in protected rows, so the planner accepts replacements containing incidental words such as “empty”, “URL”, “may” or “boolean”. The setter supplies required wiring despite no new matched term.

The three-anchor candidate gains that Jinja query plus `jinja_include_parent_and_local_context` and `urllib3_redirect_origin_check_and_header_removal` in particular observed orders. It also loses 12 full groups versus coverage. Nine losses remove formerly eligible implementations; one consumes an old backfill opportunity for `ExceptionMiddleware.__call__`; two retain the implementation but move it later and clip required lines. The Jinja include unit gains in dense and loses another required group in hybrid, so a distinct gained unit does not establish cross-condition improvement.

Both clipping losses reach the character limit with token space remaining:

- Flask `full_dispatch_request` moves to excerpt 8 and ends at line 1011, before required lines 1012–1019. The response uses 11,991 characters and 3,544 of 4,096 tokens.
- urllib3 dense `HTTPConnection.request` moves to excerpt 7 and ends at line 465, before the required interval ends at 513. The response uses 12,000 characters and 3,612 tokens.

All 16 candidate-specific lost-group occurrences passed the lexical containment guard. Protecting existing dependency identities cannot preserve behavior when necessary retrieved implementations are replaced, and full-window term preservation cannot protect a suffix later clipped during packing. Rare terms can occur in unrelated comments or wrappers; merely adding stopwords would not prove behavioral complementarity.

Both candidates fail the prospective development gate. Production defaults remain unchanged. No fresh corpus or native model run is justified for these candidates.
