# Fresh response-selection holdout: source-only urllib3 review

[The dataset](../benchmarks/selection_holdout_queries.json) contains fourteen
authored behavior questions: eight multi-file positives, four single-file
controls and two scoped absent features. It is reserved for an independently
frozen response-context selection comparison. Authoring used pinned source,
upstream test reading and extraction metadata only. No urllib3 retrieval,
candidate ranking, model score, response-selection result or inference was run
or inspected. Queries were not tailored to a selector's behavior.

## Corpus and source freeze

- Repository: [urllib3/urllib3](https://github.com/urllib3/urllib3), release **2.5.0**.
- Peeled commit: `aaab4eccc10c965897540b21e15f11859d0b62e7`.
- Annotated tag object: `e6e91da8dbf0e4efec05cb9839608a787fc11e10`.
- Scope: all **36** saved, Git-tracked supported source files under `src/urllib3`:
  35 Python files and the Emscripten fetch worker's JavaScript file. No exclusions.
  Empty production package files and typing-only production modules remain in
  the source inventory even when they contribute no executable windows. Tests,
  examples, documentation and dependency internals are outside retrieval scope.
- Clean ignored checkout: `.cache/corpora/urllib3-selection-holdout` in the
  primary project checkout. `scripts/fetch_corpus.py` can fetch the same pinned
  dataset with a chosen destination.
- Dataset SHA-256: `b29dfe038e4d227d9820b0df316bdf2e88a94639a634eea7782d4ed47c4dbd02`.
- Source-inventory SHA-256: `01cbd8fe6af43de9286e6353c16f78cb1750b5333afcbf0f550b64bd203a7d99`.
- Production extraction: **453 windows**, using 160 lines and 40 lines of overlap.

Every source file records its exact saved byte length and SHA-256. The inventory
digest covers UTF-8 JSON of `source_files`, sorted keys and compact comma/colon
separators, followed by LF. Each target records the full-file hash and the hash
of its complete inclusive physical evidence lines, including line endings.

The exposure check searched this project's benchmarks, documentation, results,
scripts and tests for urllib3 upstream repository URLs, corpus paths, query
datasets and holdout references. A matching-pattern Git history search over
those paths also returned no prior corpus exposure. Existing mentions were
installed dependency metadata and Requests source/dependency references. The
already observed Flask, Click, Requests, Rich, Starlette, Express, Zod, HTTPX,
Koa, Werkzeug, Django and Jinja repositories were excluded. This is fresh
repository evaluation within an encountered HTTP ecosystem, not a claim of
ecosystem independence or absence from model pretraining. Exposure inspection
did not use retrieval outcomes to select questions or labels.

## Labeling and source eligibility

Questions use behavior descriptions without exact source paths or qualified
symbol names. Each compound question needs distinct implementation steps in at
least two files. Linkage helpers were read to establish the actual flow; a
forwarding call does not automatically become an extra required group. Gold
intervals contain executable statements, rather than only declarations,
signatures or documentation. Upstream tests were read, not executed, and all
stored qualified test references were resolved against the pinned Python AST.

The author rechecked the clean pinned HEAD, supported-file membership, file and
evidence hashes, symbol bounds, executable evidence and 160/40 eligibility.
Every one of the **22 target rows** fits an existing source-faithful window of
its stated symbol. This was extraction eligibility only: no indexing, embedding,
ranking, response packing or inference. One test-class qualification and a
missing reference to the header-combining test were corrected during this
source-only pass before the frozen dataset hash above.

A separate source reviewer must review the frozen labels before any holdout
retrieval or inference. Source corrections require a reason and a new dataset
hash before evaluation. Once outcomes are visible, gold labels cannot be
repaired to compensate for retrieval or selection misses.

## Required compound evidence

Groups are AND; alternatives inside a group are OR. The eight compound cases
require **18 groups**. Paths below are relative to `src/urllib3`; all query IDs
begin with `urllib3_` in the JSON.

| Query suffix | Required implementation spans | Why all groups are needed |
|---|---|---|
| multipart_request_and_boundary_body | `_request_methods.py::RequestMethods.request_encode_body` 263–274; `filepost.py::encode_multipart_formdata` 65–89 | Select multipart request encoding, then construct the boundary-delimited payload and matching content type. |
| file_body_position_before_repeated_send | `connectionpool.py::HTTPConnectionPool.urlopen` 759–761; `util/request.py::set_file_position` 151–161; `util/request.py::rewind_body` 175–187 | Perform positioning before each send, record/restore the position, then handle failed recording or seeking. |
| response_status_retry_and_drain | `util/retry.py::Retry.is_retry` 396–407; `connectionpool.py::HTTPConnectionPool.urlopen` 928–947 | Decide status/header eligibility, then update the budget, drain, wait and resend. |
| redirect_origin_check_and_header_removal | `connectionpool.py::HTTPConnectionPool.is_same_host` 575–590; `poolmanager.py::PoolManager.urlopen` 479–489 | Compare normalized origins/default ports, then remove configured sensitive headers on differing destinations. |
| release_response_connection_to_bounded_pool | `response.py::HTTPResponse.release_conn` 667–671; `connectionpool.py::HTTPConnectionPool._put_conn` 308–335 | Return and clear the response's connection reference, then retain or close the returned connection. |
| elapsed_total_budget_to_socket_read | `connectionpool.py::HTTPConnectionPool._make_request` 457–459; `util/timeout.py::Timeout.read_timeout` 262–275; `connectionpool.py::HTTPConnectionPool._make_request` 517–534 | Start timing, calculate the remaining capped read allowance, then set that allowance before receiving the response. |
| file_body_chunks_and_wire_framing | `util/request.py::body_to_chunks` 230–244; `connection.py::HTTPConnection.request` 460–513 | Adapt a file body to unknown-length blocks, then choose and write the actual chunked HTTP framing. |
| certificate_name_policy_and_error_context | `util/ssl_match_hostname.py::match_hostname` 127–159; `connection.py::_match_hostname` 1032–1043 | Apply alternative-name/common-name policy and raise on mismatch, then attach the peer certificate to the reraised exception. |

Multipart request setup preserves an explicit Content-Type through `setdefault`.
The position label does not promise replay for arbitrary iterables. Response
release clears its connection only after a successful pool return; a full
blocking pool can raise before that transition, so the question specifies the
nonblocking case. The timeout label is the allowance set at the read transition,
not a deadline covering all subsequent body reads and retries. Certificate
matching does not substitute for TLS trust validation or require every wildcard
or IP normalization helper as an additional gold group.

## Single-file controls

| Query suffix | Required executable evidence |
|---|---|
| multipart_parameter_escaping | `fields.py::format_multipart_header_param` 109–114 |
| consecutive_error_backoff_and_jitter | `util/retry.py::Retry.get_backoff_time` 294–306 |
| duplicate_header_append_or_combine | `_collections.py::HTTPHeaderDict.add` 319–333 |
| bounded_read_across_queued_chunks | `response.py::BytesQueueBuffer.get` 289–309 |

These add four groups, for **22 positive evidence groups and 22 target rows**
across twelve positive questions. Header escaping is specific to the LF, CR and
double-quote translation in this implementation. Backoff covers the calculated
delay; a server Retry-After delay can take another path. Buffered reads ask about
positive-sized reads from a nonempty queue, with at most the available bytes.

## Scoped absent features

- **Persistent HTTP response cache with expiry/entity-tag revalidation:** read
  response body storage in both ordinary and Emscripten responses, request
  construction, connection pool caching and HTTP/2 probe caching. `cache_content`
  saves data on a response object in memory; the other caches concern connections
  or protocol support. None implements the requested on-disk representation
  cache or automatic expiry/revalidation flow. Browser-managed caches, dependency
  behavior, external adapters and caller-owned caches are outside scope.
- **Coordinated OAuth token refresh after authorization failure:** read Basic
  authorization construction, request dispatch, status retries, redirect header
  filtering and HTTP/2 probe locks. Configured retries repeat a request; probe
  locks coordinate protocol support. Neither performs a token-refresh exchange
  or coordinates concurrent requests around renewal. Caller-provided bearer
  headers, browser credentials and external auth clients are outside scope.

The JSON stores the actual source-search terms and nearby implementations.
Absence is a bounded source-review judgment, not proof that a caller or extension
cannot supply the feature. Keep absent cases outside positive coverage
denominators and report their delivery/abstention behavior separately under the
independently frozen evaluation rule.

## Scoring and holdout use

Coverage requires the exact pinned path and the complete literal evidence
interval in delivered, verified source. An enclosing/nested window can cover a
group regardless of its symbol label, and one window may cover several groups.
Symbol names are navigation aids, not substitutes for evidence. Candidate-pool
presence does not count when result limits, selection or truncation remove the
evidence before delivery.

Only the question text may enter retrieval or response selection. Gold paths,
symbols, line ranges, hashes, query kinds, split labels and notes cannot choose
candidates, policy, prompts, budgets or thresholds. The algorithm and experiment
configuration must be fixed independently before the first holdout outcome.
Report complete compound coverage, required-group coverage, single-file
controls, every regression and latency at the same declared budgets. If outcomes
inform a later change, this corpus becomes development evidence and another
fresh holdout is required for an independent claim.

This small, deliberately compound-heavy sample is not representative of all
user requests. It tests evidence delivery, not coding correctness, paid-agent
token savings or latency on another machine.

## Licenses

The root urllib3 license is MIT, **Copyright (c) 2008-2020 Andrey Petrov and
contributors**. Its [verbatim license copy](../benchmarks/URLLIB3_LICENSE.txt)
has SHA-256 `130e3a64d5fdd5d096a752694634a7d9df284469de86e5732100268041e3d686`.
No separate upstream NOTICE file is tracked at this revision.

`src/urllib3/util/ssl_match_hostname.py` has an explicit PSF source-level license
notice for its Python standard-library origin. That exception and the original
notice are retained in [corpus notices](../benchmarks/CORPUS_NOTICES.md) and
[third-party notices](../THIRD_PARTY_NOTICES.md); result excerpts from that file
must retain the applicable notice. The ignored checkout is downloaded separately.
