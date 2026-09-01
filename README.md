# Semantic search engine

Find code implementations by describing their behavior. The planned VS Code
extension and MCP tool share a local search backend, so developers and coding
agents can retrieve source excerpts without sending the whole repository to a
paid model.

**Current stage: local Qwen embeddings, persistent vector caching, BM25/dense/hybrid
retrieval, and optional H2O-Lightning-4B reranking in 8-bit MLX.** Search defaults
to four dense candidates followed by H2O. VS Code, a persistent search service,
incremental updates, and MCP integration are next. See [the project plan](docs/project-plan.md).

## Run on an Apple Silicon Mac

The tested setup is an M4 MacBook Pro, 24 GB RAM, macOS 15.1.1. Python dependencies,
the runtime source, approximately 4.8 GiB of H2O weights and 1.1 GiB of Qwen weights are stored locally
under ignored `.venv/` and `.cache/` directories. Nothing in the corpus is sent
to a hosted inference service.

```bash
python3 scripts/setup_mac.py
.venv/bin/python scripts/setup_embeddings.py
.venv/bin/python scripts/fetch_corpus.py
.venv/bin/python scripts/serve_mac.py
```

The final command stays running in a terminal. In a second terminal:

```bash
.venv/bin/semantic-search search \
  "Where does an expired or tampered cookie become an empty session?" \
  --repo .cache/corpora/flask

# Fast Qwen retrieval without H2O (does not need the H2O server).
.venv/bin/semantic-search search \
  "Where is the request dispatched to its view and exceptions handled?" \
  --repo .cache/corpora/flask --no-rerank

# Fixed BM25 + Qwen reciprocal-rank fusion; score only three windows.
.venv/bin/semantic-search search \
  "Where are application and blueprint template loaders combined?" \
  --repo .cache/corpora/flask --retrieval hybrid --candidates 3

.venv/bin/semantic-search shortlist-benchmark --output results/local/shortlist-run

# Reproduce the original lexical experiment.
.venv/bin/semantic-search benchmark --output results/local/flask-run
.venv/bin/python scripts/capture_memory.py results/local/flask-run
```

Use `--retrieval bm25` to choose the lexical baseline, or `--lexical-only` to skip
both models. `--candidates` counts windows scored by H2O; reranking returns only
that prefix, with at most one window per symbol. `--no-rerank` fetches at least
`--max-results` windows and returns the retrieval order. Search another Python
repository with `--repo /path/to/repo --source-root src`. The first version
indexes saved, tracked Python files within that source root; it does not yet
handle TypeScript or unsaved editor buffers.

The first dense/hybrid search builds document embeddings under `.cache/indexes/`.
Subsequent searches reuse them only when the actual source text, window boundaries,
document formatting, model revision, and embedding settings match. This is a
whole-corpus cache; changed repositories currently rebuild it. Qwen uses original
BF16 weights, 1024-dimensional normalized vectors, last-token pooling, and a fixed
query instruction, with an 8192-token truncation limit. Indexing documents uses
no instruction. Dense search scans the small vector matrix with cosine similarity;
hybrid uses equal-weight RRF with constant 60 and a fixed 50-window depth per source.

Warm benchmark timings keep both model weights resident. Each CLI invocation
loads Qwen anew and parses the corpus, so its full startup time is additional.
A persistent backend is needed to deliver the warm timings interactively.

Stop the server with Ctrl-C. It runs the official H2O shim on loopback port 8741
and a community MLX facade on 8743. Its model stays loaded while the server runs.
Use `--port` / `--facade-port` if those ports are occupied, and pass the new shim
URL via the CLI's `--url`. Prefix caching is disabled for this experiment, and
Boolean confidence flooring is disabled. Ordinal scores use the shipped
temperature calibration and are ranking signals, not certainty percentages.

## Experiments

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
scoring and were not tuned to these labels. This supports dense/K4 as the current
development default, with retrieval-only mode for speed.

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
queries; evaluation on unseen queries and repositories remains necessary.

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

The CPU tests require NumPy but do not import MLX or download models. Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
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

The scorer does one forward pass per window. Warm interactive latency, candidate
coverage, and unseen-query quality determine whether it should remain the default
scorer as the project grows.
