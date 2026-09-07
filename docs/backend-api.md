# Local backend and HTTP API

The implemented backend keeps Qwen resident and queries a durable SQLite active source snapshot. SQLite FTS5 provides keyword retrieval; pinned `sqlite-vec==0.1.9` computes exact cosine top-k through an exhaustive SQL scan of normalized float32 vectors. This is not an ANN index. Dense search is the fast CLI/API default; optional H2O reranking uses a separate local process. The [MCP adapter](mcp.md) forwards to this same service.

## Start and query

Run these setup commands from the project directory on an Apple Silicon Mac if the environment and checkpoints have not already been installed:

```sh
python3 scripts/setup_mac.py --runtime-only
.venv/bin/python scripts/setup_embeddings.py
.venv/bin/python scripts/fetch_corpus.py \
  --dataset benchmarks/new_requests_queries.json \
  --destination .cache/corpora/requests
```

Start the search service in a terminal:

```sh
.venv/bin/semantic-search serve \
  --repo .cache/corpora/requests --source-root src/requests
```

`serve` indexes and performs a warmup dense query before printing `Ready`. Its default address is `http://127.0.0.1:8750`. `--port 0` chooses an available port; `--index-cache` changes the SQLite directory, which defaults to `.cache/indexes/`. The source root defaults to `src/flask`, so specify it for another repository.

Saved-file watching is enabled by default: polling every second and refreshing after a 0.5-second debounce. `--watch-interval 2` changes the polling interval; `--no-watch` limits change discovery to searches or explicit refreshes. The watcher fingerprints eligible saved bytes, tracked membership, Git HEAD/branch, extractor/parser policy, and configured exclusions. It does not index unsaved editor buffers. Optional JS/TS support and `--exclude-source GLOB` configuration are documented in [saved-source languages](saved-source-languages.md).

In another terminal:

```sh
.venv/bin/semantic-search search \
  "Where are credentials removed when a redirect changes hosts?" \
  --server http://127.0.0.1:8750 --json

.venv/bin/semantic-search status --server http://127.0.0.1:8750
.venv/bin/semantic-search refresh --server http://127.0.0.1:8750

# Use spare result slots for static source context, with a compact tool-text cap.
.venv/bin/semantic-search search \
  "Where are credentials removed when a redirect changes hosts?" \
  --server http://127.0.0.1:8750 --max-results 8 \
  --include-dependencies --token-budget 4096 --json
```

For optional H2O scoring, first install its pinned source and checkpoint with `python3 scripts/setup_mac.py`. Keep `.venv/bin/python scripts/serve_mac.py` running in another terminal, then add `--rerank --candidates 4` to a search. The search daemon's `--url` selects the H2O base URL; it defaults to `http://127.0.0.1:8741`. Dense searches work without that process. Use `--retrieval bm25` or `--retrieval hybrid` to select the other retrievers. `--lexical-only` forces BM25 and disables reranking; `--no-rerank` is an alias for the default fast mode.

Standalone search uses `--repo /path/to/repo --source-root src` instead of `--server`. It loads and closes its backend for each invocation. A server search always uses the repository/source root configured by `serve`.

`serve --abstention-policy /path/to/policy.json` can load a development-calibrated weak-match policy; standalone `search --repo ... --abstention-policy ...` accepts the same artifact. Server searches use the policy selected when starting their service. It is optional, requires matching embedding settings, and does not prove that a rejected behavior is absent. See [the frozen quality protocol](retrieval-quality-protocol.md) and [measured false rejections](../results/retrieval-quality-2026-10-12/findings.md).

## Endpoints

Paths are exact; extra query strings or trailing slashes are unsupported. Native clients send one `Host` header identifying the server and port. POST requests require one valid `Content-Length` and `Content-Type: application/json`, containing a UTF-8 JSON object. Request bodies are limited to 65,536 bytes; duplicate JSON keys, nonfinite numbers, and transfer encoding are rejected. Responses are JSON, have `Cache-Control: no-store`, and close the connection; the response limit is 1 MiB.

| Endpoint | Request | Successful response |
|---|---|---|
| `GET /health` | No body | `{"ok":true}`; closed backend returns HTTP 503 with `{"ok":false}` |
| `GET /status` | No body | Backend status described below |
| `POST /index` | `{}` only | `{"indexstats":{...}}`, with the index statistics below |
| `POST /search` | Search object below | Search response below |
| `POST /cancel` | `{"request_id":"search-001"}` only | `{"request_id":"search-001","cancelled":true}` acknowledges the cancellation signal |

Example search body with the HTTP defaults; `request_id` is optional:

```json
{
  "query": "Where are redirect credentials removed?",
  "retrieval": "dense",
  "rerank": false,
  "candidates": 4,
  "max_results": 5,
  "source_budget": 12000,
  "token_budget": null,
  "include_dependencies": false
}
```

Only `query` is required. It must be nonblank valid Unicode text of at most 8,192 characters. `retrieval` accepts `dense`, `bm25`, or `hybrid`; `rerank` must be a boolean. Integer bounds are `candidates` 1–50, `max_results` 1–20, and `source_budget` 0–100,000. The CLI requires a positive source budget. Unknown options return HTTP 400.

`token_budget`, when supplied instead of `null`, must be an integer from 512–32,000. `include_dependencies` must be a boolean. A non-null `request_id` must contain 1–64 ASCII letters, digits, underscores, or hyphens and cannot already belong to an active request. Assign it before sending a search to make that request cancellable through `/cancel`. The MCP adapter assigns IDs itself. HTTP/CLI defaults are five results with dependencies disabled and no token cap; MCP defaults are eight results, dependencies enabled, and a 4,096-token compact text cap.

Search responses contain these fields:

| Field | Meaning |
|---|---|
| `repo`, `source_root`, `query`, `retrieval`, `rerank` | Absolute repository path, repository-relative root, and applied query/options |
| `scope` | Compact source root/membership policy, up to four complete exclusion globs of at most eighty characters, and omitted-pattern count; included inside the compact token budget |
| `results` | Ordered result objects with the exact fields listed below |
| `candidate_windows` | Number of retrieved windows, including same-symbol duplicates |
| `available_symbols` | Distinct `(path, symbol)` pairs after ranking, before the result/budget limits |
| `source_budget`, `total_source_chars`, `budget_exhausted` | Requested character budget, actual source characters returned, and whether a result was truncated or omitted by character/token packing |
| `token_usage` | Present when a token cap was supplied: `encoding`, exact compact-text `tokens`, requested `budget`, and token-trimming `truncated` |
| `match_status`, `match_evidence` | Candidate/weak-evidence status and diagnostic cosine/policy information; neither establishes a feature's absence |
| `indexstats` | Statistics from the refresh preceding this search |
| `timings` | Seconds under `index_s`, `query_embedding_s`, `retrieval_s`, `h2o_s`, `packaging_s`, and `total_s` |

Each result has `id`, `path`, `symbol`, `start_line`, `end_line`, `window_end_line`, `source`, `content_hash`, `source_hash`, `retrieval_score`, `h2o_score`, `truncated`, `origin`, and `related_to`. `path` is repository-relative; line numbers are inclusive and one-based. An anchor's `h2o_score` is a 0–3 ordinal score when reranking, otherwise `null`. `retrieval_score` is FTS5 BM25, cosine similarity, or RRF according to the selected retriever; it is not a probability. FTS5 retains the split identifier tokens of the earlier Python lexical baseline but uses different BM25 constants/IDF, so historical rankings need not match.

`total_s` includes waiting for the backend lock and the source refresh; it excludes HTTP serialization and network/client overhead. Warm service timings also exclude process startup and the initial `serve` warmup.

Without reranking, retrieval fetches up to `max(50, candidates, max_results)` windows. With reranking it fetches at most `candidates` windows and scores each with H2O; ranked anchors come only from that scored prefix. It sorts by H2O score, then retrieval score, then stable window ID. Both modes retain the highest-ranked window per `(path, symbol)` and then apply `max_results`. Hybrid uses equal-weight reciprocal-rank fusion with constant 60 and a fixed depth of 50 windows from each retriever.

When `include_dependencies` is true, expansion preserves up to the first five ranked anchors and uses spare result slots for up to two statically resolved local callees per anchor, then remaining retrieved anchors. It resolves explicit imports, local calls, and known-class methods conservatively; parameters, ambiguous bindings, factories, inherited/dynamic dispatch, and reflection remain unresolved. Dependency rows have `origin: "dependency"`, with `related_to` naming an anchor ID. Expansion makes no extra H2O calls: a helper already judged in the scored prefix can retain its own `h2o_score`, while a newly added helper has `null`. Its inherited anchor `retrieval_score` is context priority, not the helper's similarity. These rows supply context rather than proof of a query match. `max_results` and both output budgets include them.

`source_budget` counts Python string characters across returned `source` fields, including whitespace and newlines. It is **not a token budget** and excludes JSON metadata. Excerpts preserve whole source lines. `end_line` identifies the last line actually returned, while `window_end_line` records the original window's final line. `content_hash` is SHA-256 of the full source window; `source_hash` is SHA-256 of the returned excerpt, both encoded as UTF-8. `truncated` distinguishes them. A window with no complete line fitting the remaining budget is omitted; budget zero returns no source results. Reducing this budget does not reduce indexing or H2O input work.

`token_budget` packs against the exact compact JSON context serialized by the MCP adapter, including query, line/hash/origin metadata, budget flags, and its own `token_usage` fields. The named tokenizer is `cl100k_base` through pinned tiktoken. HTTP diagnostics and pretty CLI JSON contain additional fields outside this counted representation. Whole CR/LF source lines are retained; Unicode separators inside source strings remain data. A query whose empty-result context already exceeds the cap returns 400 with an actionable message. This is a reproducible payload cap, not a coding model's billing counter or measured agent-token savings.

`match_status` is ordinarily `candidates`; an empty retrieval is `no_candidates`, and an explicit weak-match policy can produce `insufficient_evidence`. That policy rejects only below its calibrated top-cosine threshold. No status is proof of absence. If a budget removes every excerpt from a nonempty retrieval, the status remains `candidates` and `budget_exhausted` explains the omission.

Status responses have `repo`, `source_root`, `source_exclusions`, `extractor`, `closed`, `ready`, `source_manifest`, `windows`, `embedding_initialized`, `dense_ready`, `last_error`, `indexstats`, `watcher`, `abstention_policy`, and `store`. Before initialization, optional manifest/statistics/store/watcher/policy fields may be `null`. `store`, when open and initialized, contains `db_path`, `schema_version`, `total_windows`, `dense_windows`, `model_fingerprint`, `embedding` metadata, `dimensions`, `updated_at`, and configured retrieval/version fields. `watcher` reports running/pending state, refresh count, last error/refresh time, and polling/debounce intervals. Status does not rescan saved files. `ready` describes the last completed refresh/error; `/health` checks only whether the backend is open, not H2O availability or retrieval quality. These calls share the search lock and can wait behind model work.

Index statistics contain `indexed_windows`, `reused_windows`, `deleted_windows`, `total_windows`, `memory_cache_hit`, `source_manifest`, `source_files`, and `elapsed_s`. Dense refreshes also include `model_fingerprint`; an unchanged in-memory refresh includes it as `null` for BM25. A memory cache hit reports zero indexed/reused/deleted windows because it skips SQLite work. These are source-window counts, not changed-file counts.

`deleted_windows` counts old window IDs removed from the active snapshot. Moving unchanged code to new line numbers replaces its IDs, so this count can exceed the number of deleted functions even while embeddings are reused.

Errors use `{"error":{"code":"...","message":"..."}}`. Invalid search options return 400 (`invalid_request`), malformed JSON 400 (`invalid_json`), source changes or parse failures 409 (`source_snapshot`), cancelled requests 409 (`request_cancelled`), backend/model failures 503 (`backend_error`), and unknown paths 404 (`not_found`). Transport guards also return 403 for invalid hosts/browser requests, 411 for invalid or missing content length, 413 for oversized bodies, and 415 for a wrong content type. Oversized responses return 500 (`response_limit`).

## Saved source and lifecycle

Each search checks file membership, saved bytes, Git HEAD/branch, extractor/parser policy, and configured exclusions before using the index. In Git repositories, eligible tracked supported files beneath the configured root are indexed; `git add` makes a new saved file eligible even before commit. Outside Git, eligible supported files beneath that root are considered. Python extraction retains executable functions/methods in windows of at most 160 lines with 40-line overlap, omitting overloads, placeholders, and signature/docstring-only windows. Optional JS/TS uses the same source window limits with the membership and implementation rules in [saved-source languages](saved-source-languages.md).

SQLite stores exact source fields, FTS5 terms, and active float32 vectors in one atomic snapshot, plus reusable float64 embeddings keyed by SHA-256 of actual rendered document text and frozen model/settings fingerprint. A changed source window is embedded when no matching vector is cached; unchanged rendered documents reuse vectors even if line boundaries/IDs shift. Deleted or renamed windows leave both retrieval snapshots. Cached historical vectors remain available for branch revisits. A model/settings or dimension change rebuilds the active dense snapshot without reusing another model's vectors; schema migration preserves older cache contents.

A branch/ref change triggers snapshot verification even if the resulting source is identical. Lexical-only search can refresh durable source/FTS without initializing Qwen; that refresh clears active dense vectors while retaining their reusable cache. The next dense search or `/index` rebuilds active vectors from the matching cache and new inference as needed. FTS5 and exact SQL cosine queries filter through the active source/model snapshot, so historical cached vectors cannot surface deleted or old-branch windows.

Source is captured, decoded, parsed, and extracted from the same bytes. The backend checks the snapshot during extraction, immediately before SQLite commit, after refresh, and before returning search results. Detected changes return 409 for a retry with stable saved files. These checks do not lock the repository against edits after the final check. Invalid eligible saved source is reported as a source snapshot error rather than silently retaining an old result.

The polling watcher refreshes after saved bytes/membership/ref changes remain stable through debounce. Watcher errors are visible in status and retried; request-time validation remains active. Invalid or changing eligible saved source does not silently become a current successful result.

One backend lock serializes model work, refreshes, searches, and status. Cancellation signalling uses a separate registry, so `/cancel` can stop queued requests and signal an active search. Checks occur while waiting for the lock, between embedding batches/retrieval/H2O scores, immediately before snapshot promotion, and before result delivery. A single Metal forward already running cannot be interrupted. A cancellation caught before promotion leaves the previous SQLite snapshot intact; the HTTP acknowledgement is not a guarantee that work had not already completed.

Ctrl-C signals active requests, stops and joins the watcher outside the model lock, releases the embedding model, and closes the index while preserving its cache. The separate H2O process remains running until stopped in its own terminal. MCP and optional saved JS/TS indexing are implemented; unsaved editor buffers and the VS Code extension remain pending. JS/TS extraction and persistence are CPU-tested; language-specific real-model quality and latency evaluation remain pending.

## Persistence and scale verification

CPU tests exercise real temporary SQLite databases, reopen/reuse, deleted/renamed/old-branch filtering, model/dimension changes, transactional rollback, reference cosine ordering, watcher debounce/shutdown, and cancellation. A seeded synthetic profile is available without model inference:

```sh
.venv/bin/python scripts/profile_index.py \
  --output results/local/sqlite-index-profile --windows 10000 \
  --dimensions 1024 --searches 30
```

The [published profile](../results/index-profile-2026-10-12/report.md) measures exact SQLite retrieval/index persistence on generated vectors. It does not measure real-model document indexing or establish large-repository model latency/memory. The [five-repository retrieval results](../results/retrieval-quality-2026-10-12/findings.md) and [real MCP/watch smoke](../results/retrieval-quality-2026-10-12/mcp-smoke.json) exercise the implemented pipeline at smaller scale; earlier pilots used different in-memory retrieval implementations. Larger real-model scale and paired-agent evaluation remain empirical work.

## Local transport

The CLI binds the service to `127.0.0.1` or `localhost`. The HTTP handler requires a loopback peer and an exact local `Host`/port, and rejects any `Origin`, `Referer`, or `Sec-Fetch-*` header. There is no browser CORS integration. Native `SearchClient` and `H2OClient` connections explicitly disable proxy handling, reject redirects, and require plain HTTP loopback base URLs without credentials, paths, query strings, or fragments. These are transport restrictions, not authentication: other processes on the same machine can call the service.

The SQLite cache stores source text as well as embeddings. Published result excerpts retain their upstream licenses; see [third-party notices](../THIRD_PARTY_NOTICES.md).
