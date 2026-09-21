# Independent source review of changed retrieval cases

The source explanations support the reported gain: dense complete evidence rises
from 107 to 110 of 118 positive queries, and hybrid from 103 to 111. Dense has
four complete gains and one regression; hybrid has ten gains and two regressions.
These are distinct first-repetition queries, not 1,728 independent quality cases.
The [machine explanations](evidence-explanations.json) retain every group,
original candidate/reranked/delivered rank, source origin and compatibility metric.
The [review receipt](semantic-review.json) binds this appendix to those artifacts
and the exact source files inspected. No inference or producer changes were made
for this review.

I checked the changed executable evidence against pinned source. The findings,
README and updated plan agree with these source facts. The improvement does not
remove the need to return multiple necessary implementations, and does not
establish coding correctness.

## Complete gains and partial gains

Every newly covered group in these changed cases is returned with origin
`retrieval`. Each was already represented in the unchanged raw candidate pool.
The following table covers all thirteen distinct gain cases, including the two
cases that recover support without completing the requested behavior. Some cases
gain under both retrieval families, so rows must not be added to infer a new
whole-study denominator.

| Case | Condition | Newly delivered executable evidence | Source assessment |
|---|---|---|---|
| Flask request dispatch | Hybrid | `Flask.full_dispatch_request`, raw rank 24 to delivered rank 4 | The span runs the early hook, dispatches only when its result is absent, handles the exception and finalizes the response. A route-dispatch helper alone would omit this orchestration. |
| Click binary console output | Dense and hybrid | `echo`, raw ranks 31/10 to delivered rank 1 | The byte branch finds an available binary writer, flushes the text wrapper, writes bytes and flushes that writer. |
| Click atomic file option | Dense and hybrid | Dense recovers `File.convert` at rank 5; hybrid recovers `_AtomicFile.close` at rank 2 | The eager option must schedule the returned file's cleanup; the atomic close must replace the temporary file over the destination. Recovering different missing groups makes both three-group results complete. |
| Starlette repeated headers | Hybrid | `Headers.getlist`, raw rank 18 to delivered rank 1 | It lowercases and Latin-1 encodes the lookup key, returns all matching raw values and decodes them. |
| Starlette multipart field limit | Hybrid | `MultiPartParser.on_part_data`, raw rank 8 to delivered rank 2 | The non-file branch checks accumulated field bytes plus the new chunk before extending the field. |
| Rich terminal-cell fit | Hybrid | `set_cell_size`, raw rank 11 to delivered rank 1 | The span pads or cuts by display cells and substitutes a space when a double-width character straddles the boundary. |
| Rich markup error path | Hybrid | `Console.render_str`, raw rank 15 to delivered rank 3 | It selects markup parsing when enabled; the closing-tag rejection implementation was already delivered. Both files are necessary. |
| Express configured query parser | Dense and hybrid | `app.set`, raw ranks 24/9 to delivered rank 7 | The settings switch invokes `compileQueryParser` and stores the compiled function. Compiler and request-getter code alone did not show this configuration connection. |
| Express trusted forwarded protocol | Dense | `app.set`, raw rank 14 to delivered rank 7 | The switch invokes `compileTrust` and stores the trust function. This supports the already delivered policy compiler and forwarded-protocol getter. |
| Zod synchronous parse | Hybrid | `_parse` / `_parse.fn`, raw rank 14 to delivered rank 1 | The executable span rejects a promise result, finalizes and throws validation issues, or returns the parsed value. Parent and nested views overlap. |
| Koa client address | Hybrid | `get ips`, raw rank 19 to delivered rank 3 | It gates forwarded addresses on proxy trust and caps the retained hops. The cached address/socket fallback getter was already delivered at rank 1. This is a same-file AND gain. |
| Zod nested deep partial | Dense and hybrid, partial only | One returned `visit` window at rank 3 covers two supporting groups | Recursive child/catchall mapping and cached visitation are now present. The separate `deepPartial` object transformation that invokes `.partial()` remains absent, so neither result completes the three-group query. |
| Zod metadata attachment | Hybrid, partial only | `$ZodRegistry.add`, raw rank 17 to delivered rank 5 | The weak registry stores metadata and an optional ID mapping. The API attachment callback that merges existing and supplied metadata remains absent; registry storage alone is incomplete. |

## The three complete regressions

**Starlette, dense.** `ExceptionMiddleware.__call__` contains the request-scope
attachment of the registered handler dictionaries and passes the request through
the exception wrapper. Its full-span candidate moves from original rank 4 to
reranked rank 6. The first five distinct anchors instead include the outer and
nested exception wrapper, server error middleware, handler registration and the
constructor. Three static callees fill the remaining slots. The wrapper's
dispatch/response-start guard is delivered, but the scope-attachment caller is
omitted. Registering handlers in an instance dictionary is insufficient evidence
of attaching those handlers to the request scope.

**Click, hybrid.** `BaseCommand.main` contains the `standalone_mode` guard,
exception display and termination using the exception's exit code. It moves from
original rank 4 to reranked rank 7. Usage formatting, failing a context, displaying
a usage error and parsing arguments take the first five anchors; usage pieces,
stderr and `echo` take the dependency slots. The usage/help text remains complete,
but the process-termination caller is omitted. Output helpers do not replace
the missing exit-code branch.

**Rich, hybrid.** The nested `Align.__rich_console__.generate_segments` branch
contains the left/right padding that actually centers each rendered line. It
moves from original rank 3 to reranked rank 12. The panel border implementation
is still delivered at rank 8, and `Align.center` appears as a dependency; that
factory constructs an aligned renderable but does not contain the required
padding algorithm. This result demonstrates panel borders and nearby centering
configuration, without completing the requested composition behavior.

The recorded orders, source bodies and filled slots explain these observable
losses. They do not identify why the model assigned its scores. There was no
candidate-pool mismatch or scored-prefix clip in these cases.

## Fresh Koa, retained case by case

All three Koa multi-file cases are already complete in both baseline conditions
and remain complete with Qwen. The new complete gain is the same-file address
case. This table reports the first prefix containing each required group;
`missing` means that a required span is not delivered.

| Koa behavior | Dense → Qwen group ranks | Hybrid → Qwen group ranks |
|---|---|---|
| String-body metadata and body-free HEAD dispatch | `[4, 8]` → `[2, 1]` | `[1, 2]` → `[2, 1]` |
| Middleware rejection and sanitized error reply | `[1, 2]` → `[4, 1]` | `[2, 1]` → `[4, 1]` |
| Stream classification, lifecycle and final pipeline | `[6, 1, 2]` → `[6, 1, 2]` | `[6, 1, 3]` → `[6, 1, 2]` |
| Forwarded-address trust/hops and cached socket fallback | `[2, 1]` → `[3, 1]` | `[missing, 1]` → `[3, 1]` |
| Encoded relocation response and accepted format | `[1]` → `[1]` | `[1]` → `[1]` |
| Method/status freshness guard | `[1]` → `[1]` | `[1]` → `[1]` |

Reranking does not improve every already complete case's first-completion rank:
the middleware-error prefix becomes longer, for example. The stream classifier
is delivered as a static dependency in all four conditions; it was already in
each raw top-50 pool. Both scoped absent behaviors still return candidates in
every condition. Inbound HTTP/imports, URL helpers, ordinary serialization and
signed-cookie support do not implement outbound retrying body-URL fetches or
rotating tenant-key body encryption. No absence confidence is established.

## Source overlap, dependency membership and limits

The Starlette outer exception wrapper contains the complete nested dispatch
span. Three primary condition/query rows therefore credit that span at rank 1
while the historical exact-symbol metric credits the nested window at rank 2.
All three rows have the same complete-query outcome under either metric. The
Zod synchronous-parse result similarly returns both the nested parsing function
and its enclosing factory, followed by nested/enclosing asynchronous variants.
These inspected examples show that distinct parser symbol identities can still
consume slots with overlapping source. They justify testing source diversity;
they do not prove that a new packing policy would recover the regressions.

There are 490 primary dependency-result occurrences whose **exact window ID** is
outside the raw top 50. This is distinct from gold evidence absent across all
raw windows: the latter count is zero. Eight primary required-group occurrences
are covered only by dependency-origin results—four for Starlette's threadpool
helper and four for Koa's stream classifier—and all eight gold spans were already
in their raw pools. Static expansion can in general add evidence absent from
retrieval, so candidate coverage is not universally a delivered-evidence ceiling.
It did not produce that form of new gold coverage in this study.

All 43,200 measured scoring pairs retained their full literal source. Among
primary excerpts with a directly scored candidate window, none extends beyond
that scoring prefix. No delivered gold group is absent from every scored prefix
in a Qwen condition. Scoring truncation
does not explain these gains or losses. Required intervals and OR alternatives
remain exactly as authored; Python intervals can be narrower than complete
definitions, while Koa requires full bodies. The source-span primary metric must
remain distinct from historical symbol-based scores.

The semantic result supports further work on Qwen reranking, especially after
hybrid retrieval. It also supports retaining the fast default: the independently
reported sequential top-50 warm latency is not suitable for that workflow.
A prospective smaller-shortlist and/or batching experiment should preserve these
supporting-code losses, avoid treating duplicate source views as new evidence,
and validate actual quality and latency. Complete gains originated as deep as
dense rank 31 and hybrid rank 24; partial Zod support came from hybrid rank 49.
A shallow shortlist cannot be assumed to retain these spans. Koa is now observed
development evidence; tuned settings need a
new fresh check before a stronger coding comparison with complete usage receipts.
