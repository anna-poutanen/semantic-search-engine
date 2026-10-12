# Dense and fixed-RRF retrieval with H2O shortlists

This extends the frozen Flask development pilot. It evaluates whether better candidate retrieval and fewer H2O calls improve the quality/latency tradeoff on the same 12 present-feature queries. The two absent-feature queries remain descriptive checks. This is development evidence: decisions informed by these labels do not become held-out results.

Qwen dense retrieval and fixed equal-weight BM25/dense RRF are coequal candidate methods. Apply the same source windows, evidence checks, shortlist sizes, score reuse rules, output policy, and selection rule to each. Keep BM25 as a lexical baseline. Compare the two candidate methods directly rather than assuming fusion improves on dense retrieval.

## Freeze before inference

Keep queries, labels, decoys, evidence intervals, H2O rubric, and H2O instructions unchanged. Record the dataset SHA-256, Flask revision, code revision, ordered snippet manifest and hashes, model revisions, runtime versions, machine, and settings. The current dataset hash is `7394c6c04a13f470b693a58095edbb12e07db2330e8dbec4b5c6448529143235`; extraction at 160 lines with 40-line overlap yields 358 windows representing 350 symbols.

Pin Qwen query/document formatting, query instruction, pooling, normalization, dtype or quantization, effective token limit, and truncation behavior before comparing results. Record them with the cache metadata. Document embeddings may be built once and reused only for identical content and settings; query embeddings must be freshly computed in live latency runs.

## Candidate retrieval

Run BM25 and Qwen dense retrieval against the identical window manifest, fetching 50 windows from each. The dense method uses its cosine order directly. The fixed-RRF method fuses window identities using equal-weight reciprocal rank fusion with one-based ranks and `k = 60`:

`rrf(window) = 1 / (60 + bm25_rank) + 1 / (60 + dense_rank)`

A source missing the window contributes zero. Resolve score ties by a fixed snippet ID order. Retain the original BM25 and dense top-50 lists and the fused top-50 list for audit; never substitute a same-symbol window merely because its evidence better matches a gold label. Fusion operates on the window union before limiting the result list.

For this protocol, shortlist size K counts windows, hence H2O calls in a fresh live run. Do not silently switch to symbols at some K. For each candidate method, score the first K windows in its immutable baseline order. Rerank that prefix by descending H2O score, then the method's baseline score (cosine or RRF), then stable snippet ID. Return only this scored prefix; append no unscored tail. K = 1 must return that method's first baseline window, but its list has only one result, so its hit@3 and hit@5 need not equal baseline metrics calculated over a longer list.

Deduplicate symbols only after reranking the scored prefix, retaining its first window per `(path, symbol)`. A hit requires the returned window itself to match the gold path/symbol and contain the full inclusive `evidence_lines` interval. A partial window cannot be upgraded by another discarded window of the same symbol. Report both **candidate coverage** (a full-evidence window exists anywhere in the original K-window prefix) and **retained coverage** (such a window survives deduplication in the delivered list), as well as delivered-symbol ranks. The delivered list can contain fewer than K symbols. Deduplicating before scoring would be a different policy.

BM25 top 50 still misses `request_dispatch_pipeline`; the other 11 targets retain their previous BM25 ranks. This case therefore tests dense candidate retrieval, not just reranking.

## Quality sweep with exact-input reuse

Prespecify K = 1, 2, 3, 4, 5, 8, 10, with maximum K = 10 for this run. A later K = 20 extension, if the retrieval coverage requires it, must be separately identified rather than changing the declared selection rule after observing H2O scores.

For each method and query, obtain one judgment per distinct candidate in its maximum-K prefix. Reuse the earlier run's judgments, including a judgment from the other candidate method, only when dataset SHA-256, query identity/text, full snippet contents and boundaries, H2O rubric/instructions, model and checkpoint revision, quantization/head dtype, runtime revision, and serving configuration match exactly. H2O evaluates a query/window pair independently of the retrieval method. Issue new H2O requests only for unseen pairs. Disable facade prefix caching as in the original experiment. Save raw distributions, expected ordinal scores, input tokens, elapsed time, snippet identity/content hash, and provenance for every judgment, marking its origin as prior exact-input reuse or new live inference. Reused elapsed values belong to the original run and are never new latency measurements. Record the prior run location/source-record reference and counts of reused versus new requests. Do not inject gold targets or decoys into candidates. The earlier labeled-decoy diagnostic remains a separate experiment and need not be rerun for this shortlist study.

Replay the smaller K prefixes from these judgments, sorting only candidates inside each exact prefix. Mark these tables as **quality replay using exact-input reuse plus new unseen-pair judgments**. They are not independent fresh inference runs or measured smaller-K latency. Do not sum stored judgment timings to claim new end-to-end measurements.

Report BM25, dense, and RRF baselines; candidate and retained evidence coverage at each K; delivered hit@1, hit@3, hit@5, and MRR on the 12 present queries; and the actual ranked windows. Distinguish candidate miss, evidence-window loss, and H2O misordering. Maximum observed candidate coverage means the best within this grid; it must not be described as full coverage unless it is 12/12. For absent queries, show the highest candidate scores at each K without fitting a rejection threshold.

## Fresh latency runs

For each method independently, the predeclared selection rule chooses the smallest K in 1, 2, 3, 4, 5, 8, 10 that attains that method's maximum candidate coverage across the grid and has hit@1 at least as high as that method's K = 10. Call it K*. This rule uses the development labels and measures candidate coverage rather than retained coverage; display any difference between them instead of changing the rule after seeing results.

After the quality sweep, measure live end-to-end searches for both methods at K = 0, K = 1, and their respective K*. Include each method's K* - 1 if K* > 1, deduplicating repeated conditions; optionally include K = 3 if distinct. K* - 1 can be outside the original replay grid. K = 0 means no H2O calls: return the first five distinct symbols in baseline order and recompute the query embedding so retrieval-only latency is a fair comparison. Report all selected method/K conditions and the predeclared rule. At minimum run one complete 14-query sweep per condition; a repeat of the proposed default gives a useful stability check.

Keep both model weights resident and serialize GPU work. Warm each model with an explicitly recorded unmeasured warmup before measured sweeps. Recompute each query embedding and issue every H2O request afresh: no embedding-result cache, judgment memoization, prior-response replay, or prefix cache. Reusing the persistent document index is expected. Rotate K order across queries or use a fixed recorded schedule to reduce order and thermal bias; do not run all competing GPU work concurrently.

Time each search from immediately before fresh query processing until the final ranked result is materialized. Record query embedding, lexical/dense retrieval and fusion, summed live H2O request time, ranking/output work, and total wall time separately. Force completion of lazy MLX evaluation before stopping an embedding timer; graph construction alone does not execute the computation ([MLX documentation](https://ml-explore.github.io/mlx/build/html/usage/lazy_evaluation.html)). Inspect candidate identity and input-token counts against the quality pass: a different window or query prefix invalidates the claim that only K changed. Memory sampling and benchmark audit writes should happen outside the query timer, or their inclusion must be explicit.

Report actual measured median and nearest-rank p95 for each selected K, sample count, every per-query timing, and the maximum. The small pilot's p95 is close to its worst sample and is not a population service-level claim. Keep replay-derived estimates out of measured-latency columns. Also compare fresh scores/ranks with quality replay to expose runtime nondeterminism rather than silently retaining the old scores.

Record startup/model-load time and first embedding/first H2O forward-pass time separately when measured. A first request to already resident weights is a first-forward measurement, not a cold application start. Excluding one first query does not by itself establish that both models were warm. Report resident memory after both models have executed, alongside pressure/swap indicators if captured; process CPU RSS alone omits Metal allocations.

## Audit checks

- Every replay shortlist is an exact prefix of that method's frozen dense or fixed-RRF order; only that prefix is reranked and returned.
- RRF ranks begin at one; unavailable-source contributions are zero; weights, fetch count, and tie rules are fixed.
- K = 1 returns that method's first baseline window, and its candidate evidence coverage cannot decrease as K grows. Retained coverage can decrease if another window of the same symbol outranks its evidence window.
- Same-symbol windows remain distinct scorer inputs; delivered hits retain full evidence after deduplication.
- Raw snippets, query text, Qwen instruction prefix, truncation, and H2O prompt match recorded provenance.
- Prior reused judgments, new unseen-pair judgments, quality replay, and fresh latency requests are distinguishable in artifacts. Fresh timing tables contain exactly the advertised new calls and no judgment-cache hits.
- Startup, first-forward, warmed weights, cached document indexing, and fresh query computation are labeled separately.
- Negative queries do not enter positive accuracy metrics, and no query/label/prompt is tuned after seeing outputs.

The next independent quality test should use unseen queries and repositories. This protocol chooses a development default; it does not establish general retrieval quality or paid-agent token savings.
