# Context selection follow-up

The development replay recovered two complete behaviors by removing redundant
excerpts only when another delivered excerpt already contained their entire
literal source, then filling the freed slots. Reserving more slots for ranked
candidates lost required dependency evidence and was rejected. This study keeps
the original eight-excerpt, 12,000-source-character and 4,096-compact-token caps.
The fresh native validation and promotion decision are recorded below when the
frozen run and independent audit finish.

## What changed

Extraction can populate a transient map from each window ID to its actual AST
definition. Python keys include the decorated definition bounds; JavaScript and
TypeScript keys also include the parser's byte offset to distinguish same-line
declarations. Several windows of one long function share a key; separate lexical
definitions with the same displayed symbol have different keys. Snippet fields,
IDs, literal source, content hashes, extraction membership, persistent index
format and embedding-cache compatibility stay identical.

The definition selector uses those keys to keep one ranked window per actual
definition. The existing static graph identifies edges by displayed labels, so
the selector suppresses expansion from or to a label shared by distinct
definitions. It does not invent which definition owns that edge. Unknown window
identities fall back to their window IDs. Ambiguous labels are computed once per
atomic source refresh and published alongside the map; warm searches reuse that
immutable set. Failed or cancelled refreshes retain the prior complete snapshot.

The coverage postprocessor works after ordinary character and token packing. It
removes an actual delivered excerpt only if a single other actual delivered
excerpt contains the same physical lines and identical source bytes. This handles
an earlier child contained in a later parent as well as the reverse order. Equal
spans keep their first-ranked representative. Retained rows, source, hashes,
locations and provenance stay unchanged. Partial overlaps are retained.

Only after a removal can later distinct ranked candidates fill spare capacity.
At most eight noncovered candidates are tried. Only new rows can be clipped, by
whole physical source lines, with an exact compact-token recount after each
trim. Every original source span must remain inside one actual retained row;
failed preservation or budget guards restore the original response. Diagnostics
remain outside the compact MCP context. Existing dependencies keep their real
owner; newly backfilled ranked candidates are labeled retrieval results.

## Development evidence

The [prospective protocol](selection-development-protocol.md) freezes six
conditions and the full input matrix. Its 337 first-repetition condition rows
share **173 distinct dataset/query units**; neither repetitions nor reused
conditions count as independent queries. Express, Zod, Jinja, HTTPX and the
broader Qwen study are all observed development data here.

| Change against legacy | New distinct complete queries | Lost full groups | Lost complete condition rows | Decision |
|---|---:|---:|---:|---|
| Definition identity | 0 | 0 | 0 | No measured completeness gain alone |
| Preserve ranked slots | 4 | 7 | 4 | Reject |
| Identity plus preserve ranked slots | 4 | 7 | 4 | Reject |
| Coverage/backfill | 2 | 0 | 0 | Qualifies for fresh validation |
| Identity plus coverage/backfill | 2 | 0 | 0 | Chosen for fresh validation |

The two gained queries are `starlette_exception_registration_and_dispatch` and
`zod_metadata_attachment_registry`, both using the previously observed hybrid
K24 Qwen reranked order. Definition identity adds no gold coverage over the
coverage-only control. Those gains must be attributed to coverage/backfill.
The fast dense/hybrid Express, Zod and Jinja orders gain no complete queries in
this replay. All original single-file first hits survive these two coverage
conditions. Dependency allocation losses include Jinja's async iteration helpers;
all losses on already incomplete queries remain in the raw group ledger too.

The original freeze and all-condition run are retained in
[`selection-development-2026-10-21`](../results/selection-development-2026-10-21/findings.md).
A review identified a per-query full-corpus ambiguity scan. After moving it into
the atomic refresh, a new committed freeze replayed the exact same six conditions
in [`selection-development-v2-2026-10-21`](../results/selection-development-v2-2026-10-21/findings.md).
All **2,022 full outcome records** are identical between attempts, including
source, metadata, compact hashes and diagnostics; their compressed raw ledgers
have the same SHA256. This second attempt is an optimization equivalence check,
not additional independent quality evidence. Both attempts preserve all 337
original full-metadata and exact compact-payload controls before any variants.

An independent auditor uses its own literal compact serializer, physical-line
reader and evidence scorer. It imports no production selector, retriever, backend
or model. Audit receipts bind the historical committed producer bytes; later
test and optimization changes cannot be represented as the code that generated
the first replay. The early auditor's exact source snapshot is retained alongside
its receipt; final audits use committed auditor code.

## Native validation

The [native protocol](selection-native-protocol.md) freezes the chosen identity
plus coverage candidate before querying urllib3 2.5.0. Its independent source-only
review covers all 36 supported source files, 453 windows, 22 exact executable
targets, eight multi-file behaviors, four single-file controls and two scoped
absent queries. It reads upstream test definitions as supporting source evidence;
it does not claim those upstream tests were executed.

Dense and hybrid are measured separately in three rotated real repetitions per
arm, with a fresh native Qwen query embedding for each of 168 measured backend
calls. Index/model setup and explicit warm-up are outside warm timings. The
candidate must add a complete multi-file query, lose no previously covered group
or complete positive, preserve single-file first hits, pass every source/token
audit and stay within 25% median warm overhead. Dense must pass before the product
default changes; a hybrid-only result cannot rescue it. The study does not measure
HTTP/MCP transport latency, coding correctness, provider billing or agent savings.

## Verification and limits

Focused tests cover same-name Python/JavaScript/TypeScript definitions, long
overlapping windows, docstring-only gaps, same-line JavaScript declarations,
actual clipped containment, reverse-order containment, equal spans, partial
overlaps, Unicode/CRLF source, hashes, exact token boundaries, rolled-back trials,
bounded work, cancellation, cache reuse and atomic metadata publication. Backend
integration tests exercise both distinct definitions and a redundant child/parent
pair with a later real ranked implementation. Harness tests run an entire
invented 24-request workflow with fake CPU components and verify the prospective
freeze, source guards, metrics and failure receipts.

The first full suite exposed a test-isolation error: a replay guard test assumed
the entire pytest process had never imported the backend. It now tests the actual
offline guard in a fresh subprocess, matching the replay's clean-process contract.
Production and replay algorithms were unchanged by that repair. Initial editor
helper checks also ran before TypeScript compilation completed; the correctly
sequenced run passed. Final test totals are recorded with the native audit and
GitHub checks.

The benchmarks are small source-authored samples. Literal source-complete AND
coverage is useful for diagnosis, but does not establish coding correctness or
generalization. Retrieval still has upstream candidate misses and the fixed
context cap can still exclude useful implementations. Any unsuccessful fresh
gate leaves the existing production default intact. No release assets or version
bump are part of this work.
