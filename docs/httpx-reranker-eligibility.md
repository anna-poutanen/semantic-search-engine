# HTTPX fresh validation source and labels

HTTPX 0.28.1 is pinned to official `encode/httpx` commit
`26d48e0634e6ee9cdc0533996db289ce4b430177`. The scope is all 23 tracked Python
implementation files under `httpx`, with no exclusions. Tests, documentation,
dependencies and caller code are outside this scope. The ignored checkout is
`.cache/corpora/httpx`; [the source freeze](../results/httpx-reranker-preflight-2026-10-13/source-freeze.json)
records exact membership, Git blob/file hashes, cleanliness and the upstream
license before any query labels were authored.

The [dataset](../benchmarks/httpx_reranker_queries.json) contains 15 natural
behavior queries: **12 positive**, **28 AND-required evidence groups**, and
**three scoped absent**. Five positives require evidence from multiple files;
three require multiple definitions in one file and four have one group. Every
positive target binds a complete Python AST function or method, including its
guards and executable body, to one-based physical source bounds and SHA-256.
The query text contains no exact target symbol or source path. Source may still
share ordinary behavior words and comments with the queries.

The five multi-file cases cover JSON request construction/header preparation,
stacked response content decoding, asynchronous incremental text-line parsing,
multipart length/framing, and environment proxy opt-in/bypass. Other cases cover
redirect header safety, Digest challenge retry flow, repeated header lookup,
cookie ambiguity, immutable query parameter updates, Unicode/raw hostname views,
and in-process asynchronous HTTP application invocation. Notes delimit delegated
dependency algorithms and prerequisites; the labels do not treat a wrapper as a
complete algorithm when the requested behavior needs additional implementation.

The three absent behaviors are persistent response caching with expiration and
revalidation, retrying HTTP server-error responses with exponential backoff and
jitter, and Digest request-body integrity protection. HTTPX's in-memory body or
decoder memoization, delegated connection retry settings, redirect/authentication
retry loops and implemented Digest `auth` mode are nearby insufficient behavior.
The body-integrity mode has an explicit unsupported branch in the pinned source.
The labels make no claim about user authentication classes, custom transports,
external libraries, `httpcore`, or application code.

[The CPU-only eligibility generator](../scripts/validate_httpx_reranker_corpus.py)
rechecks the pinned Git commit and bytes, exact source membership, all **438**
production saved-source windows, whole-definition AST bounds, source hashes, and
at least one complete containing source window for every target. Its
[receipt](../results/httpx-reranker-preflight-2026-10-13/eligibility.json) verifies
static eligibility only. It made zero model, embedding, retrieval or scorer
calls, opened no ranking outcomes and did not import the HTTPX runtime. A neutral
semantic review must separately verify that these spans substantiate the behavior
and scoped absence statements before validation inference.

The initial successful static receipt and validator snapshot are retained as
`eligibility-initial.json` and `validate-httpx-initial.py.txt`: a subsequent lint
cleanup moved local imports inside the validation function, then regenerated the
same source/label/window inventory with the new generator hash. No queries,
targets or pinned bytes changed. [The label-authoring source](../results/httpx-reranker-preflight-2026-10-13/label-authoring.py.txt)
uses only the source freeze, Python AST and pinned source bytes.

HTTPX previously appeared as an installed dependency and as type references in
Starlette test-client snippets. The existing benchmark manifests, project docs,
source and scripts contained no HTTPX implementation retrieval corpus or ranking
benchmark. The codebase is source-read to author labels and could have appeared
in pretraining; this is a previously unranked repository/settings validation, not
an unread-source or pretraining holdout. Select the shortlist/batch setup using
the existing development corpus before opening HTTPX rankings, keep all 15
queries, and report the 12-positive/28-group/five-multi-file and three-absent
denominators separately. Returned decoys do not satisfy absent queries.

The [manifest](../benchmarks/httpx_reranker_manifest.json) binds the frozen source,
dataset, authoring script and static receipt. It does not select model settings,
assert retrieval improvement, establish a rejection threshold, or measure warm
latency/coding correctness. Those belong to the new separately frozen experiment.

HTTPX's BSD-3-Clause license is copied byte-for-byte in
[HTTPX_LICENSE.txt](../benchmarks/HTTPX_LICENSE.txt); see its
[corpus notice](../benchmarks/HTTPX_CORPUS_NOTICE.md) when exporting source.
