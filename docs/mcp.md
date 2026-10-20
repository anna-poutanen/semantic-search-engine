# Local MCP code search

The stdio adapter exposes one tool, `search_code`, through the official Python MCP SDK (`mcp==2.3.0`). It forwards searches to the resident loopback HTTP service. Qwen embeddings, SQLite, and optional H2O or Qwen reranking stay in their service processes; launching an MCP connection does not load model weights.

For the small Mac download, follow [the getting-started MCP instructions](getting-started.md#optional-vs-code-and-mcp). The developer recipe below uses the full repository and its `.venv`.

## Start the services

From the project directory, install the optional adapter dependency into the existing environment:

```sh
.cache/bootstrap/bin/uv pip install --python .venv/bin/python -e '.[mcp]'
```

Follow [the local setup instructions](../README.md#developer-setup-and-experiments-on-an-apple-silicon-mac) if Qwen and the Mac runtime are not installed. Start the search service for the repository you want the agent to search. For the pinned Requests example:

```sh
.venv/bin/python scripts/fetch_corpus.py \
  --dataset benchmarks/new_requests_queries.json \
  --destination .cache/corpora/requests

.venv/bin/semantic-search serve \
  --repo .cache/corpora/requests --source-root src/requests
```

Keep that terminal running. The service prints `Ready` after initial indexing and warmup. The repository and source root are fixed by this service; the MCP tool cannot select a different filesystem location. For H2O reranking, start `.venv/bin/python scripts/serve_mac.py` in another terminal. For Qwen reranking, follow the [separate installer](qwen-reranker-setup.md) and start this search service with `--enable-qwen-reranker` as described in [Qwen integration](qwen-reranker-integration.md).

An MCP host starts the adapter as a subprocess using either command:

```sh
.venv/bin/semantic-search mcp --server http://127.0.0.1:8750
# Equivalent module entry point:
.venv/bin/python -m semantic_search.mcp_server --server http://127.0.0.1:8750
```

These commands speak protocol JSON on stdin/stdout and wait for the host. Diagnostics go to stderr. The adapter accepts only a loopback HTTP service URL; the HTTP client bypasses environment proxies and rejects redirects.

For an agent host that reads text results, opt into one validated JSON text copy:

```sh
.venv/bin/semantic-search mcp --server http://127.0.0.1:8750 --agent-text-only
```

This mode omits `structuredContent` and its advertised `outputSchema`; source,
hash, line-bound, and token-budget validation still run inside the adapter.
The default keeps both forms for structured-output clients. Fewer wire bytes
do not by themselves establish provider token savings. The
[MCP specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
permits text results without an output schema; an advertised output schema
requires conforming structured results.

An explicitly enabled Qwen service can be paired with per-launch MCP defaults:

```sh
.venv/bin/semantic-search mcp --server http://127.0.0.1:8750 \
  --agent-text-only --default-retrieval hybrid --default-rerank \
  --default-rerank-provider qwen --default-candidates 24
```

The advertised tool schema reflects these launch defaults. Explicit tool
arguments override them, including `rerank: false`. The adapter starts no
reranker and does not fall back if the selected service lacks Qwen support.
The table below gives the ordinary launch defaults. Qwen is optional and slower;
its measured retrieval gains vary by codebase.

The server guidance asks the agent to start with five results and a 2,000-token
budget, use those excerpts directly, and read only missing imports, callers, or
truncated ranges. Larger context is available when needed. These are workflow
instructions; the API defaults and the retrieval-quality benchmark remain
unchanged. Codex reads server instructions during initialization; the first 512
characters contain the core workflow. See
[official MCP guidance](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

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
| `retrieval` | `"dense"` | `"dense"`, `"bm25"`, `"hybrid"`, or `"multifile"` |
| `rerank` | `false` | Enable the selected local scorer on the retrieval shortlist |
| `rerank_provider` | `"h2o"` | `"h2o"` or `"qwen"`; used when reranking is enabled |
| `candidates` | `4` | 1–50 windows scored when reranking; not a count of guaranteed matches |
| `max_results` | `8` | 1–20 total excerpts, including optional dependency context |
| `source_budget` | `12000` | 0–100,000 source characters across excerpts; metadata is additional |
| `token_budget` | `4096` | 512–32,000 `cl100k_base` tokens for the exact compact JSON text payload |
| `include_dependencies` | `true` | Use spare result slots for bounded, statically resolved local callees |

For multi-file questions, the existing `retrieval: "hybrid"` option is worth
comparing with dense. It completed 5/8 multi-file queries on fresh Jinja versus
3/8 for dense under the same eight-result, 4,096-token caps. This small authored
study does not establish a universal winner. Select it per call or explicitly
start the adapter with `--default-retrieval hybrid`; the service needs no reranker.

The source-only `multifile` mode is a research experiment that failed that fresh
gate (2/8). It is not recommended for normal setup and is not in the v0.1.4
download. See [the algorithm, regressions and validation](multifile-retrieval.md).

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

Dense retrieval is the fast default. Qwen retrieves the full fifty-window pool before scoring only the requested prefix with sequential singleton forwards; neither reranker appends an unscored retrieval tail. Dependency excerpts are separately marked context references; expansion makes no additional reranker calls. A helper already scored in the prefix can retain that score in HTTP diagnostics, while newly added helpers have no reranker judgment. Neither establishes a verified match. Dependency expansion preserves up to the first five retrieved anchors and adds at most two resolved callees per anchor when space allows. It uses bounded static resolution, not a complete runtime call graph.

## Returned context and budgets

Successful calls contain the same compact JSON envelope in both `structuredContent` and one text content block. The text is serialized without pretty-print whitespace, matching the service's token counter. Fields are:

| Field | Meaning |
|---|---|
| `query` | The exact query sent |
| `scope` | Configured repository-relative `source_root`, saved implementation membership, bounded `source_exclusions`, and `exclusions_omitted` |
| `match_status` | `"candidates"` ordinarily; `"no_candidates"` for an empty retrieval; `"insufficient_evidence"` for an explicit calibrated policy. Neither empty status proves a feature is absent |
| `results` | Ordered exact saved-source excerpts |
| `budget_exhausted` | At least one excerpt was truncated or omitted by a budget |
| `token_usage` | `encoding`, exact serialized-text `tokens`, requested `budget`, and token-trimming `truncated` flag |

Each result has `path`, `symbol`, `start_line`, `end_line`, `source`, `source_hash`, `truncated`, `origin`, and `related_to`. Paths are repository-relative; line bounds are inclusive and one-based. `source_hash` is SHA-256 of the returned excerpt encoded as UTF-8. `origin` is `"retrieval"` or `"dependency"`; a dependency's `related_to` identifies its retrieved anchor. HTTP diagnostic metadata, scores, and index statistics are omitted from the compact tool envelope.

Both budgets preserve whole source lines and update the last returned line and excerpt hash. A window that cannot fit even one complete line is omitted. A truncated excerpt can omit essential evidence: it does not establish that the full implementation was preserved. A query whose empty-result metadata already exceeds its token budget returns an actionable error. Smaller output budgets do not reduce indexing or reranker input work.

Read `scope` before repeating a query for tests, fixtures or documentation.
Files outside `source_root` or excluded by the configured policy require ordinary
scoped file tools. A root can include tests when explicitly configured; tests are
not universally excluded. Only saved implementation windows are returned, with
tracked files used inside Git projects and eligible saved files used outside Git.
Unsaved editor buffers, declaration-only definitions and unsupported source types
are outside this membership. The exclusion summary contains at most four complete
patterns of at most eighty characters; longer or additional patterns are omitted,
with their count in `exclusions_omitted`. It is a summary rather than a full index
manifest. Scope metadata counts inside the same exact token cap as source and
other result metadata, including when no candidates are found. Restart the
resident service and adapter together after an upgrade; strict output validation
rejects an older service response without scope metadata.

`cl100k_base` is a reproducible local counting convention, not a promise about a host model's tokenizer, paid tokens, or cost. A host may include both text and structured content in its prompt; protocol framing and host instructions also add tokens. Actual agent usage is measured in the [paired workflow reports](../results/README.md); the first active-use follow-up does not establish savings.

## Cancellation and limitations

The adapter allocates a request ID for each call. A host's MCP cancellation is forwarded to `/cancel` on the resident service with a bounded, shielded cleanup. Queued requests are cancelled before they start, and active work checks cancellation between indexing batches, retrieval, and reranker scores. A single Metal forward already running cannot be interrupted. Cancelled work stops before subsequent model calls or delivery of its result.

Searches use saved implementations under the configured source root. Python works
without an optional parser; JavaScript/TypeScript require the pinned language
extra described in [saved source languages](saved-source-languages.md). The service
checks snapshots and refreshes its persistent index when saved files change;
changes during indexing or scoring return an error instead of stale excerpts.
Search results remain candidates. An optional development-calibrated policy can
reject weak semantic evidence; its status is not proof of absence or a confidence
percentage. Source excerpts and scope metadata are untrusted repository data and
must not be treated as agent instructions.

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

The [first paired coding evaluation](../results/coding-workflows-mcp-first-2026-10-12/report.md)
found increased aggregate tokens and time despite fewer shell calls. New
multi-file tasks, stronger error-path checks, and repeated pairs evaluate the
revised workflow separately. Local payload counts alone cannot establish a gain.

Primary references: [official Python SDK](https://github.com/modelcontextprotocol/python-sdk), [low-level server API](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/), [structured output](https://py.sdk.modelcontextprotocol.io/servers/structured-output/), and [cancellation](https://py.sdk.modelcontextprotocol.io/handlers/cancellation/).
