# Requests source-reviewed evaluation

The next quality check uses a repository new to this project's evaluation: [Requests](https://github.com/psf/requests), pinned to commit `611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60`. It complements the Flask pilot while the reusable local search service is being built. It is a small developer-authored, source-reviewed test; it is not a representative user benchmark, a 100-query quality gate, or evidence that the repository was absent from model pretraining.

## Frozen input

The dataset is `benchmarks/new_requests_queries.json`. Its SHA-256, recorded after authoring and before any Requests retrieval rankings or model inference, is:

`6120911fce0ad94d42c0cf340e38d397f61454e57faa377298054ca76eb5645c`

There are 20 queries: 16 present behaviors and four absent features. The `development` and `test` partitions each contain eight present queries and two absent queries. Assignment alternates present-query topics in source-review order; two negatives were assigned to each partition before retrieval. The partitions share a repository and related code, so they are not independent repository-level samples.

All present queries have exactly one gold implementation with a repository-relative path, AST-qualified symbol, and inclusive executable `evidence_lines` interval. There are no alternative-target or multi-function AND labels in this version. An implementation hit requires that the actual retained window matches the gold path/symbol and contains the entire evidence interval. Do not count a discarded same-symbol window, a type-only overload, or a prose-only window as a hit.

Source review verified all 16 golds against executable AST definitions and checked that extraction at 160 lines with 40-line overlap produces a window containing each evidence interval. The pinned source produces 240 windows. Relevant upstream tests were read as supporting evidence where available; they were not executed as part of dataset authoring. Neither model judgments nor retrieval rankings were inspected before this freeze. An independent source/label review should precede inference.

The behaviors cover setting precedence, redirect authentication boundaries and method rewriting, environment transport options, URL preparation, streamed and multipart uploads, response line buffering/JSON/status errors, proxy managers and wire targets, TLS setup, Digest challenge retries, ambiguous cookie lookup, and proxy exclusions. The four negatives ask for native async scheduling, persistent HTTP response caching, OAuth token refresh, and certificate public-key pinning. Their absence is scoped to `src/requests`, excluding dependencies, extensions, custom adapters, examples, tests, and documentation. A mention of an external feature is not its implementation.

## Run without changing the labels

Fetch or restore the exact corpus with the existing generic fetch script:

```sh
python3 scripts/fetch_corpus.py \
  --dataset benchmarks/new_requests_queries.json \
  --destination .cache/corpora/requests
```

Keep the existing Qwen checkpoint/query instruction, source format, embedding normalization, H2O checkpoint/rubric, and extraction policy fixed for the first comparison. Evaluate dense retrieval and the established dense-plus-H2O K4 configuration. If reporting fixed RRF as another baseline, retain equal weights, rank constant 60, and input depth 50. Declare any additional condition before examining this test's outcomes; do not choose a new K or fusion weight from the test labels and call it held out.

The first completed service run fixed five conditions before inference: BM25,
dense, and hybrid without H2O; dense with H2O K4; and hybrid with H2O K3. It
rotates their execution order per query, delivers at most five symbols within
12,000 source characters, and performs 100 actual loopback HTTP searches plus
140 fresh measured H2O judgments. Setup and warmup are separate. Reproduce it
with installed models and `.venv/bin/python scripts/serve_mac.py` running:

```sh
.venv/bin/python scripts/benchmark_service.py \
  --output results/local/requests-service-run \
  --h2o-url http://127.0.0.1:8741
```

Omit `--h2o-url` for the three fast retrieval conditions. Choose a new output
directory for each run. The runner starts its own resident search backend and
temporary HTTP server; an existing search daemon is not needed. The committed
[report](../results/requests-service-2026-10-12/report.md) includes per-split
metrics and an independent audit of delivered evidence, budgets, scores, tokens,
and timing summaries. Both partitions were measured under the same frozen
configuration; neither drove configuration changes in this run.

Use the development partition for implementation checks, then evaluate the test partition with the frozen configuration. Report both partitions separately and optionally show an overall descriptive aggregate. If any test outputs guide later prompt, label, retrieval, or parameter changes, the affected test has become development evidence; use new queries for the next independent quality check. A source-review correction before inference must receive a new recorded dataset hash and an explanation.

Report evidence-qualified hit@1, hit@3, hit@5, MRR, and candidate versus retained coverage on present queries. Derive denominators from the selected partition: 16 present queries overall, or eight per split. Keep the four absent cases out of positive accuracy and inspect their returned source and scores without fitting a rejection threshold. Publish actual ranked window IDs, source hashes, model/settings provenance, failures, and query-level outcomes so the aggregate can be audited.

Quality replay may reuse H2O judgments only for exact query/window/model/prompt/config inputs with explicit provenance. Requests query IDs and source differ from Flask, so Flask judgments are not interchangeable. Latency measurements must freshly embed each query and freshly score every shortlisted window with warmed, resident models; preserve startup/indexing measurements separately. Follow `docs/shortlist-protocol.md` for candidate prefix, output, reuse, and timing rules. Persist this run separately from the original Flask results.

## Source redistribution

Requests is licensed under Apache License 2.0. `benchmarks/REQUESTS_LICENSE.txt` is a byte-for-byte copy of the pinned repository's `LICENSE`. The ignored corpus remains under `.cache/corpora/requests`; it should not be committed as a second source tree. Preserve the license and original attribution when publishing indexed text, raw model judgments, or result excerpts containing Requests source.

The pinned repository's `NOTICE` reads:

```text
Requests
Copyright 2019 Kenneth Reitz
```

This notice is preserved here alongside the license. Include the same attribution in the project's third-party notices when distributing source-bearing evaluation artifacts.
