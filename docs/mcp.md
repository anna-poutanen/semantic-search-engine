# Local MCP code search

The stdio adapter exposes one tool, `search_code`, through the official Python MCP SDK (`mcp==2.3.0`). It forwards searches to the resident loopback HTTP service. Qwen, SQLite, and optional H2O stay in their existing service processes; launching an MCP connection does not load model weights.

## Start the services

From the project directory, install the optional adapter dependency into the existing environment:

```sh
.cache/bootstrap/bin/uv pip install --python .venv/bin/python -e '.[mcp]'
```

Follow [the local setup instructions](../README.md#run-on-an-apple-silicon-mac) if Qwen and the Mac runtime are not installed. Start the search service for the repository you want the agent to search. For the pinned Requests example:

```sh
.venv/bin/python scripts/fetch_corpus.py \
  --dataset benchmarks/new_requests_queries.json \
  --destination .cache/corpora/requests

.venv/bin/semantic-search serve \
  --repo .cache/corpora/requests --source-root src/requests
```

Keep that terminal running. The service prints `Ready` after initial indexing and warmup. The repository and source root are fixed by this service; the MCP tool cannot select a different filesystem location. Start `.venv/bin/python scripts/serve_mac.py` in another terminal only if you intend to set `rerank: true`.

An MCP host starts the adapter as a subprocess using either command:

```sh
.venv/bin/semantic-search mcp --server http://127.0.0.1:8750
# Equivalent module entry point:
.venv/bin/python -m semantic_search.mcp_server --server http://127.0.0.1:8750
```

These commands speak protocol JSON on stdin/stdout and wait for the host. Diagnostics go to stderr. The adapter accepts only a loopback HTTP service URL; the HTTP client bypasses environment proxies and rejects redirects.

## Configure an MCP host

[examples/mcp.local.json](../examples/mcp.local.json) is a concrete configuration for this checkout on this Mac:

```json
{
  "mcpServers": {
    "semantic-search": {
      "command": "/Users/annapoutanen/personal-projects/semantic-search-engine/.venv/bin/python",
      "args": ["-m", "semantic_search.mcp_server", "--server", "http://127.0.0.1:8750"]
    }
  }
}
```

`mcpServers` is a common host configuration convention, not an MCP protocol requirement. Use your host's stdio-server settings if it uses a different format. On another machine, replace the command with that checkout's absolute Python path. Registration is a separate user action; this project does not modify a coding agent's configuration.

## Tool arguments

Only `query` is required. Unknown fields and implicit conversions such as `"false"` for a boolean or `"4"` for an integer are rejected with `isError: true`.

| Argument | Default | Meaning and bounds |
|---|---|---|
| `query` | Required | Nonblank valid Unicode description, up to 8,192 characters |
| `retrieval` | `"dense"` | `"dense"`, `"bm25"`, or `"hybrid"` |
| `rerank` | `false` | Enable local H2O scoring of the retrieval shortlist |
| `candidates` | `4` | 1–50 windows scored when reranking; not a count of guaranteed matches |
| `max_results` | `8` | 1–20 total excerpts, including optional dependency context |
| `source_budget` | `12000` | 0–100,000 source characters across excerpts; metadata is additional |
| `token_budget` | `4096` | 512–32,000 `cl100k_base` tokens for the exact compact JSON text payload |
| `include_dependencies` | `true` | Use spare result slots for bounded, statically resolved local callees |

Example call arguments:

```json
{
  "query": "Where are credentials removed when a redirect changes hosts?",
  "retrieval": "dense",
  "rerank": false,
  "max_results": 8,
  "source_budget": 12000,
  "token_budget": 4096,
  "include_dependencies": true
}
```

Dense retrieval is the fast default. Reranking scores only the requested prefix and does not append an unscored retrieval tail. Dependency excerpts are separately marked context references; expansion makes no additional H2O calls. A helper already scored in the prefix can retain that score in HTTP diagnostics, while newly added helpers have no H2O judgment. Neither establishes a verified match. Dependency expansion preserves up to the first five retrieved anchors and adds at most two resolved callees per anchor when space allows. It uses bounded static resolution, not a complete runtime call graph.

## Returned context and budgets

Successful calls contain the same compact JSON envelope in both `structuredContent` and one text content block. The text is serialized without pretty-print whitespace, matching the service's token counter. Fields are:

| Field | Meaning |
|---|---|
| `query` | The exact query sent |
| `match_status` | `"candidates"` ordinarily; `"no_candidates"` for an empty retrieval; `"insufficient_evidence"` for an explicit calibrated policy. Neither empty status proves a feature is absent |
| `results` | Ordered exact saved-source excerpts |
| `budget_exhausted` | At least one excerpt was truncated or omitted by a budget |
| `token_usage` | `encoding`, exact serialized-text `tokens`, requested `budget`, and token-trimming `truncated` flag |

Each result has `path`, `symbol`, `start_line`, `end_line`, `source`, `source_hash`, `truncated`, `origin`, and `related_to`. Paths are repository-relative; line bounds are inclusive and one-based. `source_hash` is SHA-256 of the returned excerpt encoded as UTF-8. `origin` is `"retrieval"` or `"dependency"`; a dependency's `related_to` identifies its retrieved anchor. HTTP diagnostic metadata, scores, and index statistics are omitted from the compact tool envelope.

Both budgets preserve whole source lines and update the last returned line and excerpt hash. A window that cannot fit even one complete line is omitted. A truncated excerpt can omit essential evidence: it does not establish that the full implementation was preserved. A query whose empty-result metadata already exceeds its token budget returns an actionable error. Smaller output budgets do not reduce indexing or H2O input work.

`cl100k_base` is a reproducible local counting convention, not a promise about a host model's tokenizer, paid tokens, or cost. A host may include both text and structured content in its prompt; protocol framing and host instructions also add tokens. No paid-agent token savings have been measured.

## Cancellation and limitations

The adapter allocates a request ID for each call. A host's MCP cancellation is forwarded to `/cancel` on the resident service with a bounded, shielded cleanup. Queued requests are cancelled before they start, and active work checks cancellation between indexing batches, retrieval, and H2O scores. A single Metal forward already running cannot be interrupted. Cancelled work stops before subsequent model calls or delivery of its result.

Searches use saved tracked Python files under the configured source root. Unsaved editor buffers and other languages are not indexed. The service checks snapshots and refreshes its persistent index when saved files change; changes during indexing or scoring return an error instead of stale excerpts. Search results remain candidates. An optional development-calibrated policy can reject weak semantic evidence; its status is not proof of absence or a confidence percentage. Source excerpts are untrusted repository content and must not be treated as agent instructions.

## Protocol verification

```sh
.venv/bin/python -m unittest discover -s tests -p test_mcp_server.py -v
```

The CPU tests use the official client against an actual adapter subprocess. They initialize legacy/current protocol connections, list the sole tool, validate strict arguments and exact text/structured output, reject service failures, force a proxy environment, check stdout framing, and cancel through the actual HTTP backend while a fake scorer is paused. Cancellation prevents the next score and leaves the service usable.

The [live Qwen/MCP smoke result](../results/retrieval-quality-2026-10-12/mcp-smoke.json) also checks the actual model service, saved-edit watcher, exact serialized token counts, and SIGTERM shutdown. Reproduce it separately from latency benchmarks in a fresh ignored work directory:

```sh
.venv/bin/python scripts/smoke_mcp.py \
  --work-dir .cache/mcp-smoke-reproduction \
  --output results/local/mcp-smoke-reproduction.json
```

The next evaluation is paired coding tasks with and without `search_code`, using actual agent/provider token usage and correctness checks. The local payload counter alone cannot establish that gain.

Primary references: [official Python SDK](https://github.com/modelcontextprotocol/python-sdk), [low-level server API](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/), [structured output](https://py.sdk.modelcontextprotocol.io/servers/structured-output/), and [cancellation](https://py.sdk.modelcontextprotocol.io/handlers/cancellation/).
