# Post hoc source-allocation diagnosis

This describes fixed, observed development outcomes. It is **outside the
canonical independent audit scope**. The frozen producer, guards and tests were
not changed, and no new candidate, model, index, retrieval or fresh-corpus run
was performed. [Machine-readable details and input hashes](diagnosis.json) and
the [exact descriptive script](diagnosis.py.txt) accompany this report.

The plan and execution remain commit
`e34d6f6ab2d8e6c5884e607ce8068ee090dc4299`. This analysis verifies its bound
source blobs, the new ledger, and all 730 referenced original control records.
The diagnosis JSON SHA-256 is
`d3cbf142d5941e549372f06b4572f133c79cda77ac39534a171f94add7bc65ff`.

## Activation and delivered changes

Each arm has 365 cases sharing 187 dataset/query units. Repeated studies and
retrieval orders are not independent queries. Both proposals activate on 329
cases and accept a graph allocation on 58 cases.

| Arm | Postpacking guard accepts | Final compact responses changed vs coverage | New complete case rows | Distinct complete query gains | Lost full-group occurrences |
|---|---:|---:|---:|---:|---:|
| `source_graph` | 50 | 47 | 6 | 2 | 1 |
| `source_graph_all` | 5 | 2 | 0 | 0 | 0 |

The structural arm has seven group-gain occurrences and one group loss against
coverage. Six gains complete an Express query; the seventh exchanges required
HTTPX groups while leaving that query incomplete. Neither arm qualifies.
The original coverage control's Starlette and Zod gains are not new gains here.

## Why the Express gains work

All six new complete case rows add `lib/application.js:app.set:351-383` at
delivered slot six. The original retrieval already contains that implementation;
the old eight-slot allocation omits it. Its real calls to the retrieved compiler
implementations provide the graph witness. No character/token clipping or
backfill occurs in these cases.

| Study | Query | Order | Original raw rank of `app.set` | Explicit caller witness | Evicted original slot-eight result | Final chars / tokens |
|---|---|---|---:|---|---|---:|
| Language | Configured query parser | Hybrid | 9 | `app.set` → `exports.compileQueryParser` | `lib/response.js:<module>.encodeUrl:18-18` | 3,146 / 1,641 |
| Language | Configured query parser | Dense | 24 | `app.set` → `exports.compileQueryParser` | `lib/request.js:<module>.parseRange:21-21` | 1,718 / 1,261 |
| Language | Trusted forwarded protocol | Dense | 14 | `app.set` → `exports.compileTrust` | `lib/request.js:secure:326-328` | 3,165 / 1,687 |
| Native Express | Configured query parser | Hybrid | 9 | `app.set` → `exports.compileQueryParser` | `lib/response.js:<module>.encodeUrl:18-18` | 3,146 / 1,641 |
| Native Express | Configured query parser | Dense | 24 | `app.set` → `exports.compileQueryParser` | `lib/request.js:<module>.parseRange:21-21` | 1,718 / 1,261 |
| Native Express | Trusted forwarded protocol | Dense | 14 | `app.set` → `exports.compileTrust` | `lib/request.js:secure:326-328` | 3,165 / 1,687 |

The saved executable evidence is `app.set` lines 360–370 for query-parser
configuration and 371–379 for trust-proxy configuration. The full 351–383 window
is delivered in each gain. These rows reduce to two distinct dataset/query
units: `express_configured_query_parser` and
`express_trusted_forwarded_protocol`.

## Why the HTTPX regression survives the structural guard

For `httpx_json_request_encoding_and_headers` in the observed
`hybrid_k24_b1` order, `Request._prepare` is raw rank six and is fully delivered
at baseline slot eight. Two original static dependencies occupy slots six and
seven, after the first five protected encoder anchors. That leaves one
exchangeable result.

The proposal promotes raw-rank-eight `encode_request` through three honest
caller witnesses: it calls `encode_json`, `encode_content`, and
`encode_urlencoded_data`. It then evicts `Request._prepare` **before packing**.
The protected five anchors and two dependencies survive unchanged, so the
structural postpacking guard accepts the proposal.

| Required implementation | Baseline delivered rank | Candidate delivered rank | Result |
|---|---:|---:|---|
| `Request.__init__` | Missing | Missing | Original gap remains |
| `encode_request` | Missing | 8 | Group gained |
| `encode_json` | 1 | 1 | Preserved |
| `Request._prepare` | 8 | Missing | Group lost |

`Request._prepare` lines 441–460 implement default-header insertion with
`setdefault`, including the caller-supplied content-header handling required by
this query. The final response uses **5,461 of 12,000 source characters** and
**2,223 of 4,096 compact tokens**, compared with 5,007 characters and 2,087 tokens
in the control. It has no truncated results, no coalescing, and zero backfill
trials. This loss is slot eviction, not a packing or backfill error.

The frozen full source graph contains `Request.__init__` calls to both
`encode_request` and `Request._prepare`. The latter is therefore a sibling in
the constructor flow, beyond the current first-five anchors' one-hop protection.
This explains why source connectivity improves selection yet fails to identify
every required implementation. It is a post hoc interpretation, not a measured
result for any broader graph rule.

The query was incomplete before and after because the constructor group remains
missing. Complete-query counts alone would conceal the swapped required group;
the frozen individual-group gate rejects it against both original controls.
The all-source arm rejects this proposal and returns the exact original control.
It also blocks the measured complete-query gains. Production defaults therefore
remain unchanged and the fresh codebase remains reserved.
