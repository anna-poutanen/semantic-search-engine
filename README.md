# Semantic search engine

Find code implementations by describing their behavior. The implemented MCP
tool and VS Code extension share a local search backend, so developers and coding
agents can retrieve source excerpts without sending the whole repository to a
paid model.

**Current stage: a resident local search service and MCP adapter with durable
SQLite FTS5/vector retrieval, saved-file watching, cancellation, budgeted context,
and optional H2O-Lightning-4B reranking in 8-bit MLX.** Qwen dense retrieval is
the fast default. Paired coding repairs now have actual agent timing, provider
token usage, preserved patches, and independent correctness review. The CLI and
MCP adapter use the same backend. See
[the project plan](docs/project-plan.md), [the service API](docs/backend-api.md),
and [MCP setup](docs/mcp.md).

The [GitHub preview release](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.0-preview.1)
includes the verified VS Code package. See [the preview guide](docs/release-preview.md)
for installation, demonstrations and measured limits.

For a small reproducible walkthrough, see [the demo guide](docs/demo.md).
After the runtime and Qwen setup below, run:

```bash
.venv/bin/python scripts/demo_mac.py --run
```

This creates a fresh ignored Python/JS/TS toy, checks real HTTP and MCP delivery,
and stops its own service. The [completed demo](results/demo-2026-10-13/findings.md)
retains exact source summaries and its runtime limits. Running the script without
`--run` prepares instructions and source without loading a model.

## Run on an Apple Silicon Mac

The tested setup is an M4 MacBook Pro, 24 GB RAM, macOS 15.1.1. Python dependencies,
approximately 1.1 GiB of Qwen weights and the optional 4.8 GiB H2O checkpoint are stored locally
under ignored `.venv/` and `.cache/` directories. Retrieval and local scoring do
not call a hosted inference service. An MCP host receives source excerpts and
may include them in requests to its chosen coding model.

```bash
python3 scripts/setup_mac.py --runtime-only
.venv/bin/python scripts/setup_embeddings.py
.venv/bin/python scripts/fetch_corpus.py
.venv/bin/semantic-search serve --repo .cache/corpora/flask
```

The final command stays running in a terminal. In a second terminal:

```bash
.venv/bin/semantic-search search \
  "Where does an expired or tampered cookie become an empty session?" \
  --server http://127.0.0.1:8750

# Bounded, structured excerpts with exact paths and line ranges.
.venv/bin/semantic-search search \
  "Where is the request dispatched to its view and exceptions handled?" \
  --server http://127.0.0.1:8750 --source-budget 6000 --json

.venv/bin/semantic-search status
.venv/bin/semantic-search refresh
```

For optional H2O reranking, install its pinned runtime and weights with
`python3 scripts/setup_mac.py`, start `.venv/bin/python scripts/serve_mac.py` in
another terminal, then pass `--rerank`:

```bash
.venv/bin/semantic-search search \
  "Where does an expired or tampered cookie become an empty session?" \
  --server http://127.0.0.1:8750 --rerank --candidates 4

# Fixed FTS5 + Qwen reciprocal-rank fusion; score only three windows.
.venv/bin/semantic-search search \
  "Where are application and blueprint template loaders combined?" \
  --server http://127.0.0.1:8750 --retrieval hybrid --rerank --candidates 3

.venv/bin/semantic-search shortlist-benchmark --output results/local/shortlist-run

# Reproduce the original lexical experiment.
.venv/bin/semantic-search benchmark --output results/local/flask-run
.venv/bin/python scripts/capture_memory.py results/local/flask-run
```

Use `--retrieval bm25` to choose SQLite FTS5 keyword retrieval, or `--lexical-only` to skip
both models in standalone mode. With `--rerank`, `--candidates` counts windows
scored by H2O; results contain only that prefix, with at most one window per
symbol. Without reranking, results follow the retrieval order. `--no-rerank`
remains a compatibility alias. Start a service for another repository
with `serve --repo /path/to/repo --source-root src`. Alternatively,
`search "behavior" --repo /path/to/repo --source-root src` runs standalone and
loads Qwen for that invocation. The backend indexes saved, tracked Python files
and optional JavaScript/TypeScript with the pinned `languages` extra. See
[saved-source languages](docs/saved-source-languages.md) for implementation,
membership, and `--exclude-source` rules. JS/TS extraction and persistence have
CPU fixture coverage and a frozen real-Qwen Express/Zod evaluation. Dense search
delivers complete evidence for 10/12 Express and 8/12 Zod behaviors; Zod multi-file
coverage remains incomplete. The saved mixed-language editor workflow also
[passes in an actual VS Code host](results/editor-workflows-2026-10-13/findings.md).
The ordinary CLI/MCP workflow searches saved source. Backend/HTTP callers can
provide explicit [request-only editor text overlays](docs/unsaved-overlays.md);
the VS Code command can explicitly include unsaved eligible buffers. Real Qwen
overlay and actual dirty-editor source checks pass; they are integration fixtures.

The service preloads Qwen and builds a durable SQLite index under `.cache/indexes/`.
Its polling watcher checks eligible saved bytes, tracked membership, Git HEAD,
branch, and extractor/parser policy every second, then refreshes after a
0.5-second debounce. `--watch-interval`
changes the polling interval; `--no-watch` discovers changes on searches and
explicit refreshes instead. Every search still verifies the source snapshot.
Changes embed only new
document text; unchanged vectors survive process restarts and branch switches.
Deleted windows leave the active snapshot, and line shifts update locations
without recomputing identical document embeddings. Model revision and embedding
settings are part of the cache key. Files changed during indexing or scoring,
and temporarily invalid eligible saved source, produce an error so stale excerpts are not
delivered. These checks scan source hashes; they do not lock files against later edits.
Qwen uses original
BF16 weights, 1024-dimensional normalized vectors, last-token pooling, and a fixed
query instruction, with an 8192-token truncation limit. Indexing documents uses
no instruction. SQLite FTS5 stores identifier tokens split at snake/camel/acronym
boundaries. Pinned `sqlite-vec==0.1.9` computes exact cosine distances over the
active normalized float32 vectors in an exhaustive SQL scan; there is no ANN
index. Hybrid uses equal-weight RRF with constant 60 and a fixed 50-window depth
per source. Historical experiments below used Python BM25 and NumPy retrieval;
FTS5's BM25 scores and float32 near ties can change their rankings.

The service serializes GPU work and keeps Qwen loaded. `--server` calls reuse it;
the Requests measurements below include the actual HTTP round-trip. Standalone
`--repo` searches incur additional model startup time. `--source-budget` limits
returned source characters, with whole-line truncation and accurate excerpt
line ranges; JSON metadata is additional. It is not a token budget.

The [stdio MCP adapter](docs/mcp.md) exposes `search_code`. Its defaults return up
to eight excerpts within 12,000 source characters and **4,096 `cl100k_base` tokens
for the exact compact JSON text**, including query and result metadata. The CLI
can request the same context cap with `--token-budget 4096`; its diagnostic JSON
contains additional fields outside that counted representation. The cap is a
local counting convention, not agent billing or measured paid-token savings.

MCP enables conservative dependency context by default. The CLI opts in with
`--include-dependencies --max-results 8`: up to five retrieved anchors retain
their order, and spare slots can contain statically resolved local callees.
These references are marked `origin: "dependency"`; expansion makes no extra H2O
calls. A helper already judged in the scored prefix can retain its own H2O score;
a new helper is unjudged. Its inherited anchor retrieval score is a context
priority, not helper similarity or proof of a match. Static references do not
form a complete runtime call graph. Optional `serve --abstention-policy /path/to/policy.json`
loads a development-calibrated weak-match policy bound to the embedding
settings; a rejected or budget-empty response does not prove absence.

MCP cancellation is forwarded through request IDs to the service. Cancellation
checks run while waiting for the model lock, between embedding batches and H2O
scores, and before snapshot promotion or result delivery. A Metal forward already
running finishes before the next check. The watcher stops during backend shutdown.

Stop either service with Ctrl-C. The optional H2O launcher runs its official shim on loopback port 8741
and a community MLX facade on 8743. Its model stays loaded while the server runs.
Use `--port` / `--facade-port` if those ports are occupied, and pass the new shim
URL via the CLI's `--url`. Prefix caching is disabled for this experiment, and
Boolean confidence flooring is disabled. Ordinal scores use the shipped
temperature calibration and are ranking signals, not certainty percentages.

## Experiments

The [106-query frozen evaluation](results/retrieval-quality-2026-10-12/findings.md)
covers five repositories, including ten multi-file AND behaviors and a Rich
repository holdout. Dense context delivers complete evidence for **83/88 positive
queries overall and 18/20 held-out positives**, with **175 ms held-out HTTP P95**.
Dense candidate coverage is 87/88 at depth 50; hybrid reaches 88/88 but returns
less complete evidence in its first five results. Defaults remain fast dense
retrieval. The optional weak-match policy rejects three of four absent Rich
queries while falsely rejecting one of twenty present behaviors, so it stays
opt-in. Raw outputs, independent audits, and failures accompany the report.

The [live MCP/watch test](results/retrieval-quality-2026-10-12/mcp-smoke.json)
uses real Qwen and the official MCP client: a saved edit independently embeds
one function and reuses two vectors. Durable retrieval, watching, and cancellation
also have CPU verification. A [10,000-window synthetic index profile](results/index-profile-2026-10-12/report.md)
records exact SQLite cosine P95 of 19.63 ms and full durable reuse; it does not
establish real Qwen indexing performance at that scale. The separate
[language study](results/language-quality-2026-10-13/findings.md) measures real
Qwen indexing and durable reuse for 1,802 Zod windows. Actual VS Code saved,
dirty-buffer and saved-edit workflows now pass. Separate
[native acceptance checks](results/editor-acceptance-2026-10-13/findings.md)
exercise false workspace trust, multi-folder repository selection/removal and
offline handling with an authored HTTP fixture. Ordinary user-profile activation
and external beta feedback remain untested.

The [coding workflow availability pilot](results/coding-workflows-2026-10-12/report.md)
runs six paired seeded repairs in real Requests, Click, and Starlette checkouts
with the same agent, prompt, and starting source. All twelve runs pass the
selected hidden checks, with actual provider usage available for each. **None
of the six MCP-assigned runs called `search_code`**, so their time and token
differences cannot be attributed to retrieval. Independent review also finds
both Click fixes can silently swallow an atomic commit failure; selected-check
success does not establish complete correctness. The frozen configuration,
patches, event streams, and supplemental fault injection are published.

A [separate MCP-first follow-up](results/coding-workflows-mcp-first-2026-10-12/report.md)
reuses three tasks with the same conditional search-first instruction in both
conditions. All three MCP agents use `search_code`: Requests and Click use Qwen
dense retrieval; Starlette chooses lexical retrieval. Independent review
matches their excerpts to the seeded source. Both conditions pass the ten
declared check methods, while both Click fixes still suppress commit errors.

| Three paired tasks | Without MCP | With MCP |
| --- | ---: | ---: |
| Selected-check passes | 3/3 | 3/3 |
| Total agent wall time | 426.78 s | 489.87 s |
| Total setup time (including indexing/warmup) | 0.79 s | 62.22 s |
| Full evaluator time (including setup and grading) | 427.94 s | 553.48 s |
| Actual total tokens (input + output) | 578,218 | 618,537 |
| Uncached input tokens | 115,984 | 144,935 |
| Median total tokens per task | 213,682 | 199,162 |

MCP uses **7.0% more total tokens and 14.8% more agent time** over this workload,
despite a 6.8% lower condition median token count. These are different statistics.
Requests uses fewer tokens with MCP; Click and Starlette use more. Index loading
and warmup are additional setup costs; including setup and grading, evaluator
time is 29.3% higher. Each trial builds a separate index; a resident service can
reuse an existing index. This small reused
development sample does not establish savings or the 30% median-token target.

The [larger repeated study](results/multifile-workflows-2026-10-13/findings.md)
adds four fresh multi-file features, two repetitions, 41 frozen behavioral methods
and restored original integration checks. All sixteen attempts are retained and
independently audited. Seven of eight MCP-assigned attempts use retrieval.
Three timeouts and three provider-capacity failures leave six usage receipts
unavailable. Full-workload token savings are unknown.

Only three pairs have both normal completion and frozen-gate passes. Their
actual total tokens rise **9.23%** with MCP, uncached input rises **18.62%**, agent
time falls **2.20%**, and full trial time rises **1.59%**. This outcome-selected
subset contains two NDJSON repetitions and one Starlette body-limit pair;
individual results vary substantially. Separate
[patch review](results/multifile-workflows-2026-10-13/patch-review.md) finds six
scoped defects across seven attempts, including holes in the frozen references.
Those findings supplement the formal grades. The token-saving target remains
unmet; [scope visibility and product integration](docs/project-plan.md) follow
without implying a measured coding benefit.
The original availability pilot and follow-up remain separate studies, with
every attempt retained and no selected retries.

The [Requests service experiment](results/requests-service-2026-10-12/report.md)
tests 20 newly authored queries on a second real codebase: 16 present behaviors
and four absent features. Queries and an eight-positive/eight-positive
development/test split were frozen before retrieval. The configurations came
from the Flask experiment and were not adjusted to these labels.

| Mode | Correct first | Correct implementation in results | Median / P95 HTTP |
|---|---|---|---|
| BM25, first five | 10/16 | 15/16 | 0.044s / 0.051s |
| Qwen dense, first five | 13/16 | 16/16 | 0.089s / 0.118s |
| Hybrid, first five | 12/16 | 16/16 | 0.068s / 0.086s |
| Dense + H2O, four-window prefix | 15/16 | 15/16 | 6.36s / 8.69s |
| Hybrid + H2O, three-window prefix | 16/16 | 16/16 | 4.92s / 7.53s |

Dense/K4 loses the streamed-upload implementation because dense ranks its
evidence window fifth. Hybrid/K3 recovers it on Requests, but previously missed
Flask dispatch. A four-window shortlist therefore does not guarantee coverage
across repositories. Fast dense retrieval remains the default; H2O improves
ordering at a substantial latency cost. Reranking still misses the five-second
P95 aspiration. The service used about 1.7 GB resident for Qwen, with a 2.0 GB
indexing peak; optional H2O used another 6.3 GB resident.

Hits require the actual delivered excerpt to contain all executable evidence
lines. Latency has 20 searches per configuration, including absent queries; split
tables use ten searches each. Absent queries still return candidates, so this
does not demonstrate abstention. This remains a small authored dataset, not a
representative benchmark or evidence of paid-agent token savings. Frozen inputs,
raw scores, excerpts, and per-split metrics are included in the report directory.
See [the protocol](docs/next-evaluation.md) to reproduce it.

The [Qwen and shortlist experiment](results/qwen-hybrid-shortlist-2026-10-12/report.md)
uses the same frozen Flask queries:

| Mode | Correct first result | Correct implementation in results | Warm median / P95 |
|---|---|---|---|
| Qwen dense, no H2O, first five results | 10/12 | 12/12 | 0.040s / 0.180s |
| Qwen dense + H2O, four-window prefix | 12/12 | 12/12 | 5.95s / 9.70s |
| Hybrid + H2O, three-window prefix | 11/12 | 11/12 | 5.06s / 8.85s |

Four is the smallest tested dense shortlist that keeps all twelve implementation
windows. Three loses the template-loader implementation. Hybrid keeps eleven with
three windows but misses dispatch: equal-weight fusion moves its dense rank 2
to hybrid rank 24 because BM25 omits it. The fusion weights were fixed before
scoring and were not tuned to these labels. Dense/K4 was the initial reranking
configuration; the Requests run above shows its coverage limit.

Dense/K4 is about 3.2 times faster than the previous 19.29-second median, but
still exceeds the plan's five-second reranked-search target. Dense/K3 reaches
4.76s median and returns 11/12 targets, losing template lookup; these smaller
prefixes make the coverage/latency tradeoff explicit. Each new latency condition
has 14 fresh searches, including the two absent features. With this sample size,
nearest-rank P95 equals the maximum and is not a production latency guarantee.

A repeat after freeing document-batch GPU buffers preserved 12/12 first results
at 5.84s median / 9.45s P95; retrieval-only took 0.032s / 0.044s. This cleanup
reduced the embedding worker's current physical footprint from 15.4 GB to
about 1.7 GB, and its indexing peak from 15.5 GB to about 2.0 GB. H2O remained
around 6.3 GB resident. These are individual macOS process footprints, including
Metal allocations; full raw measurements and both sweeps are in the report.

The [shortlist protocol](docs/shortlist-protocol.md) declares K = 1, 2, 3, 4, 5, 8,
10. Quality replay may reuse exact prior judgments; fresh latency measurements
recompute every query embedding and every H2O judgment. Raw artifacts distinguish
the two. Neither pass inserts labeled implementations into the shortlist.

The [October 12 Flask pilot](results/flask-h2o-q8-2026-10-12/report.md) is complete:

| Measurement | Result |
|---|---|
| Correct implementation at rank 1 | BM25: 8/12; BM25 + H2O: 11/12 |
| Correct implementation beats both labeled decoys | 12/12 |
| Correct implementation in the initial shortlist | 11/12 |
| Warm ten-candidate search, median / p95 | 19.3s / 23.9s |
| Warm single-candidate scoring, median / p95 | 1.61s / 4.16s |
| Facade peak physical footprint, including Metal | About 7.5 GB, as reported by macOS |

H2O improved three query rankings and preserved the other successful top results.
The remaining miss was missing from candidate retrieval. Qwen's dense retrieval
recovers that implementation. These are development results on the same twelve
queries. The Requests run adds one frozen repository; larger independent
evaluation remains necessary.

[The frozen Flask dataset](benchmarks/flask_queries.json) contains 12 reviewed
behavior queries, two plausible decoys per implementation, and two absent-feature
queries. The model sees source and the behavior request; gold labels and notes
are never included in its prompt.

The original benchmark searches the entire indexed corpus, scores the top 10 BM25 windows,
and compares the resulting symbol ranks. A separate diagnostic scores verified
implementation windows against handpicked decoys. That diagnostic is clearly
separated from retrieval: it never inserts labeled targets into the search
shortlist. A hit requires the returned window to contain the target's executable
evidence interval. See [the evaluation protocol](benchmarks/README.md).

Each run writes raw judgments with exact source excerpts, distributions, input
token counts, timing, source hashes, ranked windows, a machine-readable summary,
and a report. Input token counts here are **local H2O processing counts**; no
paid-agent token savings have been demonstrated. The dataset is a small
development pilot, not a held-out quality benchmark.

`capture_memory.py` adds macOS physical footprint and peak since process launch
after the experiment, while the server is still running. Ordinary `ps` RSS on
Apple Silicon omits Metal allocations and understates the model's memory use.

## Validation

Core CPU tests use NumPy, sqlite-vec, and tiktoken. The full suite also needs the
optional `[mcp,test]` dependencies; the workflow gate tests invoke pytest on
temporary fixtures. See [MCP setup](docs/mcp.md). Tests use fake
models and real loopback/subprocess transports without checkpoint inference. Run:

```bash
.cache/bootstrap/bin/uv pip install --python .venv/bin/python -e '.[mcp,test]'
.venv/bin/python -m pytest -q
```

The actual model experiment needs the Mac runtime. Runtime package versions are
pinned in [requirements-mac.lock](requirements-mac.lock). Checkpoint and community
runtime commits are pinned in setup and recorded in each report.

## Model and corpus sources

- [H2O-Lightning-4B](https://huggingface.co/h2oai/h2o-lightning-4b), Apache-2.0.
- [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), Apache-2.0;
  original BF16 weights on MLX through `mlx-embeddings` 0.1.0 (GPL-3.0-or-later).
- [Community MLX q8 checkpoint](https://huggingface.co/netprojects/h2o-lightning-4b-mlx-q8)
  and [MLX facade](https://github.com/internet-projects/h2o-lightning-mlx), Apache-2.0.
- [Flask](https://github.com/pallets/flask), BSD-3-Clause. Corpus checkout is kept
  out of this repository. Included experiment source excerpts retain the corpus
  attribution; see [third-party notices](THIRD_PARTY_NOTICES.md).
- [Requests](https://github.com/psf/requests), Apache-2.0. The additional frozen
  corpus is downloaded separately; committed excerpts retain its license and
  attribution in [third-party notices](THIRD_PARTY_NOTICES.md).
- [Click](https://github.com/pallets/click), [Starlette](https://github.com/encode/starlette),
  and [Rich](https://github.com/Textualize/rich) supply the expanded frozen corpus;
  their pinned revisions and licenses are in [corpus notices](benchmarks/CORPUS_NOTICES.md).
- [sqlite-vec](https://github.com/asg017/sqlite-vec), [tiktoken](https://github.com/openai/tiktoken),
  and the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
  provide vector distance, context counting, and the optional stdio adapter.

The scorer does one forward pass per window. Candidate coverage and the quality
gain per second determine where optional reranking is useful as the project grows.
