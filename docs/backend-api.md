# Local backend and HTTP API

The implemented backend keeps Qwen, retrieval structures, and an incremental SQLite index resident. Dense search is the CLI and API default. H2O reranking is optional and uses a separate local H2O process.

## Start and query

Run these setup commands from the project directory on an Apple Silicon Mac if the environment and checkpoints have not already been installed:

```sh
python3 scripts/setup_mac.py
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

In another terminal:

```sh
.venv/bin/semantic-search search \
  "Where are credentials removed when a redirect changes hosts?" \
  --server http://127.0.0.1:8750 --json

.venv/bin/semantic-search status --server http://127.0.0.1:8750
.venv/bin/semantic-search refresh --server http://127.0.0.1:8750
```

For optional H2O scoring, keep `.venv/bin/python scripts/serve_mac.py` running in another terminal, then add `--rerank --candidates 4` to a search. The search daemon's `--url` selects the H2O base URL; it defaults to `http://127.0.0.1:8741`. Dense searches work without that process. Use `--retrieval bm25` or `--retrieval hybrid` to select the other retrievers. `--lexical-only` forces BM25 and disables reranking; `--no-rerank` is an alias for the default fast mode.

Standalone search uses `--repo /path/to/repo --source-root src` instead of `--server`. It loads and closes its backend for each invocation. A server search always uses the repository/source root configured by `serve`.

## Endpoints

Paths are exact; extra query strings or trailing slashes are unsupported. Native clients send one `Host` header identifying the server and port. POST requests require one valid `Content-Length` and `Content-Type: application/json`, containing a UTF-8 JSON object. Request bodies are limited to 65,536 bytes; duplicate JSON keys, nonfinite numbers, and transfer encoding are rejected. Responses are JSON, have `Cache-Control: no-store`, and close the connection; the response limit is 1 MiB.

| Endpoint | Request | Successful response |
|---|---|---|
| `GET /health` | No body | `{"ok":true}`; closed backend returns HTTP 503 with `{"ok":false}` |
| `GET /status` | No body | Backend status described below |
| `POST /index` | `{}` only | `{"indexstats":{...}}`, with the index statistics below |
| `POST /search` | Search object below | Search response below |

Example search body, including all defaults:

```json
{
  "query": "Where are redirect credentials removed?",
  "retrieval": "dense",
  "rerank": false,
  "candidates": 4,
  "max_results": 5,
  "source_budget": 12000
}
```

Only `query` is required. It must be nonblank valid Unicode text of at most 8,192 characters. `retrieval` accepts `dense`, `bm25`, or `hybrid`; `rerank` must be a boolean. Integer bounds are `candidates` 1–50, `max_results` 1–20, and `source_budget` 0–100,000. The CLI requires a positive source budget. Unknown options return HTTP 400.

Search responses contain these fields:

| Field | Meaning |
|---|---|
| `repo`, `source_root`, `query`, `retrieval`, `rerank` | Absolute repository path, repository-relative root, and applied query/options |
| `results` | Ordered result objects with the exact fields listed below |
| `candidate_windows` | Number of retrieved windows, including same-symbol duplicates |
| `available_symbols` | Distinct `(path, symbol)` pairs after ranking, before the result/budget limits |
| `source_budget`, `total_source_chars`, `budget_exhausted` | Requested character budget, actual source characters returned, and whether a result was truncated or omitted by that budget |
| `indexstats` | Statistics from the refresh preceding this search |
| `timings` | Seconds under `index_s`, `query_embedding_s`, `retrieval_s`, `h2o_s`, `packaging_s`, and `total_s` |

Each result has `id`, `path`, `symbol`, `start_line`, `end_line`, `window_end_line`, `source`, `content_hash`, `source_hash`, `retrieval_score`, `h2o_score`, and `truncated`. `path` is repository-relative; line numbers are inclusive and one-based. `h2o_score` is a 0–3 ordinal score when reranking, otherwise `null`. `retrieval_score` is BM25, cosine similarity, or RRF score according to the selected retriever; it is not a probability.

`total_s` includes waiting for the backend lock and the source refresh; it excludes HTTP serialization and network/client overhead. Warm service timings also exclude process startup and the initial `serve` warmup.

Without reranking, retrieval fetches up to `max(10, candidates, max_results)` windows. With reranking it fetches at most `candidates` windows and scores each with H2O; it returns only that scored prefix. It sorts by H2O score, then retrieval score, then stable window ID. Both modes retain the highest-ranked window per `(path, symbol)` and then apply `max_results`. Hybrid uses equal-weight reciprocal-rank fusion with constant 60 and a fixed depth of 50 windows from each retriever.

`source_budget` counts Python string characters across returned `source` fields, including whitespace and newlines. It is **not a token budget** and excludes JSON metadata. Excerpts preserve whole source lines. `end_line` identifies the last line actually returned, while `window_end_line` records the original window's final line. `content_hash` is SHA-256 of the full source window; `source_hash` is SHA-256 of the returned excerpt, both encoded as UTF-8. `truncated` distinguishes them. A window with no complete line fitting the remaining budget is omitted; budget zero returns no source results. Reducing this budget does not reduce indexing or H2O input work.

Status responses have `repo`, `source_root`, `closed`, `ready`, `source_manifest`, `windows`, `embedding_initialized`, `dense_ready`, `last_error`, `indexstats`, and `store`. Before refresh, the manifest/statistics/store may be `null`. `store`, when open and initialized, contains `db_path`, `schema_version`, `total_windows`, `model_fingerprint`, `embedding` metadata, `dimensions`, and `updated_at`. Status does not rescan saved files. `ready` describes the last completed refresh/error; `/health` checks only whether the backend is open, not H2O availability or retrieval quality. These calls share the search lock and can wait behind model work.

Index statistics contain `indexed_windows`, `reused_windows`, `deleted_windows`, `total_windows`, `memory_cache_hit`, `source_manifest`, `source_files`, and `elapsed_s`. Dense refreshes also include `model_fingerprint`; an unchanged in-memory refresh includes it as `null` for BM25. A memory cache hit reports zero indexed/reused/deleted windows because it skips SQLite work. These are source-window counts, not changed-file counts.

`deleted_windows` counts old window IDs removed from the active snapshot. Moving unchanged code to new line numbers replaces its IDs, so this count can exceed the number of deleted functions even while embeddings are reused.

Errors use `{"error":{"code":"...","message":"..."}}`. Invalid search options return 400 (`invalid_request`), malformed JSON 400 (`invalid_json`), source changes or parse failures 409 (`source_snapshot`), backend/model failures 503 (`backend_error`), and unknown paths 404 (`not_found`). Transport guards also return 403 for invalid hosts/browser requests, 411 for invalid or missing content length, 413 for oversized bodies, and 415 for a wrong content type. Oversized responses return 500 (`response_limit`).

## Saved source and lifecycle

Each search checks file membership, saved bytes, Git HEAD, and branch before using the index. In Git repositories, only tracked `.py` files beneath the configured root are indexed; `git add` makes a new saved file eligible even before commit. Outside Git, Python files beneath that root are considered. Extraction indexes executable functions/methods in windows of at most 160 lines with 40-line overlap, omitting overloads, placeholder implementations, and windows containing only signatures/docstrings.

SQLite stores an atomic active snapshot plus reusable embeddings keyed by rendered document content and model/settings fingerprint. A changed source window is embedded when no matching vector is cached; unchanged windows reuse vectors. Deleted or renamed windows leave the active snapshot. Cached historical vectors remain available for branch revisits. A branch/ref change triggers snapshot verification even if the resulting source is identical. BM25 search refreshes its in-memory source without initializing Qwen or refreshing persistent vectors; `/index` explicitly refreshes dense vectors.

Source is captured, decoded, parsed, and extracted from the same bytes. The backend checks the snapshot during extraction, immediately before SQLite commit, after refresh, and before returning search results. Detected changes return 409 for a retry with stable saved files. These checks do not lock the repository against edits after the final check. Invalid saved Python is reported as a source snapshot error rather than silently retaining an old result.

One backend lock serializes refreshes, searches, status, and shutdown. Ctrl-C closes the search service, releases its embedding model, and closes its index; it preserves the SQLite cache. The separately launched H2O process remains running until stopped in its own terminal. There is no file watcher: saved changes are discovered on the next search or explicit refresh. Unsaved editor buffers, TypeScript indexing, MCP, and VS Code integration are not implemented yet.

## Local transport

The CLI binds the service to `127.0.0.1` or `localhost`. The HTTP handler requires a loopback peer and an exact local `Host`/port, and rejects any `Origin`, `Referer`, or `Sec-Fetch-*` header. There is no browser CORS integration. Native `SearchClient` and `H2OClient` connections explicitly disable proxy handling, reject redirects, and require plain HTTP loopback base URLs without credentials, paths, query strings, or fragments. These are transport restrictions, not authentication: other processes on the same machine can call the service.

The SQLite cache stores source text as well as embeddings. Published result excerpts retain their upstream licenses; see [third-party notices](../THIRD_PARTY_NOTICES.md).
