# Complementary-window holdout: source review

The fresh repository is [TanStack Query](https://github.com/TanStack/query),
specifically its framework-independent `@tanstack/query-core` package. Release
`@tanstack/query-core@5.90.5` resolves through annotated tag object
`b5dd42fe7e65865ae0d3a2b3f7f206a7c0fe4173` to commit
`ecdf96296b1dfcee617a5a5b9afdf2a42ad436ba`. The source was cloned cleanly into
ignored local storage. No dependency installation, build, model download, index
construction, retrieval, ranking, selection-rule activation or inference was
performed during this source review.

The source-author dataset is
[window_holdout_queries.json](../benchmarks/window_holdout_queries.json), SHA-256
`ec4ebb01aa366d51af9966e830bd490592ef2a784e9e9de5d57ea558beb25931`.
It contains 14 queries: 8 cross-file cases requiring every evidence group,
4 single-file controls and 2 scoped absences. There are 33 required positive
groups and 43 target rows, including explicit source-equivalent alternatives.
All labels, source pins, selection code and experimental configuration must be
committed and independently reviewed before the first held-out retrieval.
This document records source authorship; it is not a benchmark result.

## Why this repository

The corpus was chosen before inspecting any ranking or algorithm activation. It
is a modest TypeScript library with production source in a single published
package, real interactions across cache, query, observer, retry, hydration and
pagination implementations, and long implementations with distinct lifecycle
stages. Its MIT license permits the publication of source excerpts with notices.
This selection did not depend on how any proposed selector would behave on it.

The source-only author searched the project's then-current working tree and all
reachable Git history for `tanstack` and `query-core` across benchmark, research,
result, script, test, implementation and README files. Neither search found an
existing occurrence before the new corpus was introduced. The history search
was run from project commit
`5d1e80062638e19101acf3b9e8543066590fd1c5`. The already-observed corpus list was
also checked: Express, Zod, Jinja, HTTPX, urllib3, Click, Flask, Rich, Starlette,
Requests, Koa, Werkzeug and Django are development data and were not reused as
this repository holdout. This is project-experiment isolation; upstream source
may have appeared in model pretraining.

## Exact source scope and eligibility

The source root is `packages/query-core/src`. The only exclusion is
`packages/query-core/src/__tests__/**`. This matches the pinned package.json
`files` declaration, which publishes `src` while excluding `src/__tests__`.
Every supported production source file is included, including the type-only
file that does not emit an implementation. No implementation file was removed
because it was large, difficult, unrelated to a query or poorly retrieved.

The dataset contains complete file inventories for both included source and
excluded supported test/helper files. Each entry stores the exact byte count
and SHA-256 of saved bytes. Inventory hashes use UTF-8 JSON with sorted keys,
compact separators and one final LF:

| Source review item | Value |
|---|---|
| Included supported files | 23 TypeScript files |
| Included source inventory SHA-256 | `aa46e17da2b8d3d28c435b6001595edd2a2a5c5e011dd5221aa677ed777cdb59` |
| Excluded supported test/helper files | 24 |
| Excluded inventory SHA-256 | `c47caf83fc92034259d33cb19389e9c4baa12a9850d92939c7a3ba8240efc9df` |
| Source files emitting implementations | 22 |
| Exact AST definition identities | 329 |
| Source windows at 160 lines / 40 overlap | 331 |

Only syntax parsing and extraction eligibility were run. No embeddings, search
scores, candidate order, returned excerpts, gold coverage or selector activations
were calculated. The author checked that every target alternative has at least
one emitted 160/40 source window containing its entire evidence interval, with
the same qualified symbol and exact definition identity.

## Evidence and semantic review

Each group requires a concrete piece of implementation, rather than just a
symbol or file hit. It records the exact physical evidence lines, their complete
saved bytes' SHA-256 and half-open byte bounds, the complete source-file hash,
AST definition identity and byte bounds, definition hash, and executable lines
within the evidence. AST and span byte bounds refer to the original UTF-8 file;
they do not include invented formatting or source reconstructed from tokens.
Evidence byte bounds include complete physical lines and their saved line ends.

OR alternatives are allowed only when an actual nested implementation or AST
ancestor contains precisely the same full evidence interval and bytes. They do
not weaken the required fact. A large parent excerpt that actually contains the
evidence is acceptable; a different window with the same parent symbol is not.
All groups are AND requirements. Multiple groups within one long definition
remain separate facts even when a particular source window could cover both.

The author read the complete relevant implementations and adjacent callers and
callees before assigning evidence. Per-query notes distinguish required facts
from linkage context and state important conditions. Related upstream test
bodies were read as corroborating source, and exact test-file hashes and titles
are recorded. Those upstream tests were not executed: this review certifies
source labels and eligibility, not upstream runtime behavior.

| Case | Required groups | Source-reviewed behavior |
|---|---:|---|
| In-flight reuse and successful delivery | 4 | Reuse existing work; commit successful data and cache callbacks; dispatch observer updates; refresh the observer result. |
| Last-observer signal cancellation | 4 | Distinguish consumed and unused signals; record consumption; reject through the retryer; restore state and abort. |
| Mutation scope serialization | 4 | Pass cache scheduling into execution; permit one pending scope member; run continuation in finally; continue a paused peer. |
| Invalidation and refetch filtering | 3 | Invalidate matches and honor no-refetch; change query invalidation state; exclude disabled/static queries before refetch. |
| Bounded bidirectional infinite pages | 4 | Choose fetch direction; append or prepend pages and parameters; truncate the opposite end in both helpers. |
| Optimistic results and result promises | 4 | Predict fetch state; derive pending/fetching/paused state; finalize the exposed thenable; update promise state and remove pending resolvers. |
| Hydrated pending-promise reuse | 3 | Attach and deserialize an eligible dehydrated promise; forward initialPromise; reuse it for the initial retryer execution. |
| Selector memoization and structural sharing | 3 | Reuse unchanged selection; apply sharing options; recursively reuse equal children and complete previous values. |
| Stable query-key hash | 1 | Sort plain-object keys during recursive JSON serialization. |
| Recursive partial-key match | 1 | Directionally require every requested subset key to match. |
| Nested notification batches | 1 | Decrement transactions in finally and flush only when the outermost transaction ends. |
| Non-shortening garbage-collection timeout | 1 | Preserve the maximum prior/configured duration with the browser/server defaults. |

The two absent cases are explicitly limited to this published core package:
atomic filesystem persistence and cross-tab BroadcastChannel synchronization.
The author searched included source for filesystem imports/writes/renames,
temporary or atomic writes, browser storage, BroadcastChannel and postMessage,
and reviewed the persister extension points, local cache listeners and browser
online/focus handlers. Those searches found no implementation of either requested
feature. Separate upstream persistence or broadcast packages outside query-core
do not make a scoped absence present. These are source-review labels, not proofs
about all possible application plugins or the whole upstream monorepo.

## Licenses and source attribution

The pinned root license is MIT, Copyright (c) 2021-present Tanner Linsley. Its
byte-for-byte copy is [TANSTACK_QUERY_LICENSE.txt](../benchmarks/TANSTACK_QUERY_LICENSE.txt),
SHA-256 `a405ee70c632bb938acb7ac5f210e814409b3760ea9d2329d8ed8ffcfd11a0e7`.
The pinned checkout tracks no separate NOTICE file.

`utils.ts:327` explicitly credits copied code from
[jonschlinkert/is-plain-object](https://github.com/jonschlinkert/is-plain-object).
The additional MIT copyright and permission notice is preserved in
[IS_PLAIN_OBJECT_LICENSE.txt](../benchmarks/IS_PLAIN_OBJECT_LICENSE.txt), SHA-256
`4cd903859549d4b20b571041f96dfae1136ed079c476126268f9d7cc1b611150`.
That notice was fetched from upstream commit
[`74620fdf57c9d2809c6b0d112bb12ceaae3d7308/LICENSE`](https://github.com/jonschlinkert/is-plain-object/blob/74620fdf57c9d2809c6b0d112bb12ceaae3d7308/LICENSE);
it is an attribution reference,
not another indexed corpus. `thenable.ts` also links React promise-type
references. It declares no different source-level license; those original links
remain attached to any complete excerpt including the header. These provenance
details and the applicable MIT notices accompany exported source evidence.

## Before evaluation

An independent reviewer must verify source hashes and inventories, semantic
necessity of every group, all qualified AST definitions and byte bounds, complete
evidence-window eligibility, upstream test references, scoped absence labels and
license copies. The review must bind the exact final dataset and this document
and state that no index, retrieval, model or rule-activation outcomes were used.
Any justified source-only correction must be recorded before the joint code and
evaluation freeze. After retrieval begins, this corpus becomes observed data;
it cannot select parameters or be presented as fresh again.
