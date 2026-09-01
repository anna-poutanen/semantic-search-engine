# Semantic search engine

Find code implementations by describing their behavior. The planned VS Code
extension and MCP tool share a local search backend, so developers and coding
agents can retrieve source excerpts without sending the whole repository to a
paid model.

**Current stage: a working local model experiment and search CLI on Apple
Silicon.** It indexes Python functions, retrieves a BM25 shortlist, and reranks
with H2O-Lightning-4B in 8-bit MLX. Embeddings, persistent indexing, the extension,
and MCP integration are next. See [the project plan](docs/project-plan.md).

## Run on an Apple Silicon Mac

The tested setup is an M4 MacBook Pro, 24 GB RAM, macOS 15.1.1. Python dependencies,
the runtime source, and approximately 4.8 GiB of model weights are stored locally
under ignored `.venv/` and `.cache/` directories. Nothing in the corpus is sent
to a hosted inference service.

```bash
python3 scripts/setup_mac.py
.venv/bin/python scripts/fetch_corpus.py
.venv/bin/python scripts/serve_mac.py
```

The final command stays running in a terminal. In a second terminal:

```bash
.venv/bin/semantic-search search \
  "Where does an expired or tampered cookie become an empty session?" \
  --repo .cache/corpora/flask

.venv/bin/semantic-search benchmark --output results/local/flask-run
.venv/bin/python scripts/capture_memory.py results/local/flask-run
```

Use `--lexical-only` for a search without the model. Search another Python
repository with `--repo /path/to/repo --source-root src`. The first version
indexes saved, tracked Python files within that source root; it does not yet
handle TypeScript or unsaved editor buffers.

Stop the server with Ctrl-C. It runs the official H2O shim on loopback port 8741
and a community MLX facade on 8743. Its model stays loaded while the server runs.
Use `--port` / `--facade-port` if those ports are occupied, and pass the new shim
URL via the CLI's `--url`. Prefix caching is disabled for this experiment, and
Boolean confidence flooring is disabled. Ordinal scores use the shipped
temperature calibration and are ranking signals, not certainty percentages.

## Experiment

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
The remaining miss was missing from candidate retrieval. This is promising
development evidence for an optional reranker. It does not meet the plan's
five-second warm search target. Next: embeddings and hybrid candidate retrieval,
a smaller reranked shortlist, and evaluation on unseen repositories.

[The frozen Flask dataset](benchmarks/flask_queries.json) contains 12 reviewed
behavior queries, two plausible decoys per implementation, and two absent-feature
queries. The model sees source and the behavior request; gold labels and notes
are never included in its prompt.

The benchmark searches the entire indexed corpus, scores the top 10 BM25 windows,
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

The core parser, BM25 baseline, and metric checks use only Python's standard
library. Run:

```bash
python3 -m unittest discover -s tests -v
```

The actual model experiment needs the Mac runtime. Runtime package versions are
pinned in [requirements-mac.lock](requirements-mac.lock). Checkpoint and community
runtime commits are pinned in setup and recorded in each report.

## Model and corpus sources

- [H2O-Lightning-4B](https://huggingface.co/h2oai/h2o-lightning-4b), Apache-2.0.
- [Community MLX q8 checkpoint](https://huggingface.co/netprojects/h2o-lightning-4b-mlx-q8)
  and [MLX facade](https://github.com/internet-projects/h2o-lightning-mlx), Apache-2.0.
- [Flask](https://github.com/pallets/flask), BSD-3-Clause. Corpus checkout is kept
  out of this repository. Included experiment source excerpts retain the corpus
  attribution; see [third-party notices](THIRD_PARTY_NOTICES.md).

The scorer does one forward pass per window. Warm interactive latency, candidate
coverage, and unseen-query quality determine whether it should remain the default
scorer as the project grows.
