# Optional original B1 Qwen reranker

This integration is opt-in. Dense retrieval without reranking remains the default. Legacy `rerank=true` selects H2O unless `rerank_provider=qwen` is explicit. Qwen uses only the original BF16 checkpoint and sequential unpadded B1 forwards; batches 2/4/8 failed the frozen precision gate and are unavailable.

Create a separate Python 3.12 environment for the exact dependency closure in `requirements-qwen-reranker.lock`. Do not add MLX-LM to the main embedding environment. The already validated local runtime is `.cache/reranker-experiment/.venv/bin/python`. The already downloaded checkpoint is `.cache/models/qwen3-reranker-0.6b-original`. These ignored paths are local prerequisites, not files shipped in this repository.

For a fresh installation, dependency installation and checkpoint download are explicit setup operations:

```sh
python3.12 -m venv .cache/reranker-experiment/.venv
.cache/bootstrap/bin/uv pip install --python .cache/reranker-experiment/.venv/bin/python -r requirements-qwen-reranker.lock
.cache/reranker-experiment/.venv/bin/hf download Qwen/Qwen3-Reranker-0.6B --revision e61197ed45024b0ed8a2d74b80b4d909f1255473 --local-dir .cache/models/qwen3-reranker-0.6b-original
```

These setup commands access package/model hosts; searches and worker startup never install packages, download a model, execute Hub code, or call hosted inference. The worker checks pinned config/tokenizer hashes, the full 1,191,588,280-byte weight hash before import, package versions, native Qwen3/base/RoPE source origins and bytes, and checkpoint file witnesses around scoring. Witnesses detect changes; they are not an adversarial filesystem lock.

To start the optional owner with the resident service:

```sh
.venv/bin/semantic-search serve --repo PATH --source-root SOURCE_ROOT --enable-qwen-reranker
.venv/bin/semantic-search search 'requested behavior' --server http://127.0.0.1:8750 --retrieval hybrid --rerank --rerank-provider qwen --candidates 24 --max-results 8 --include-dependencies --source-budget 12000 --token-budget 4096
```

The family and shortlist above are explicit examples; benchmark decisions determine the selected family/K. Production retrieves the original full 50-window pool, scores only the requested prefix in its original order, ranks by descending yes-minus-no difference with original prefix index on ties, then uses the existing symbol deduplication, static dependency expansion, whole-line source clipping and exact compact context token cap. No unscored tail is appended. A corpus with fewer than 50 windows uses its available pool and reports that count. There is no new abstention threshold or claim that candidates prove absence.

For agent trials, launch MCP with explicit defaults and record them in the trial manifest:

```sh
.venv/bin/semantic-search mcp --server http://127.0.0.1:8750 --agent-text-only --default-retrieval hybrid --default-rerank --default-rerank-provider qwen --default-candidates 24
```

The advertised schema reflects these defaults. Every explicit tool argument overrides its launch default. Preserve raw tool arguments/override keys and effective launch settings in workflow receipts. MCP's recommendation of five results/2,000 tokens is smaller than the retrieval study's eight results/4,096 tokens; correctness at one cap does not establish correctness at another. Diagnostic provider, score and forward receipts remain outside the counted compact source envelope.

`QwenRerankerProcess(python=..., checkpoint=..., log_dir=...)` explicitly owns one isolated subprocess/group and a private mode-700 directory with mode-600 authentication, readiness and logs. `start()` verifies and warms one toy pair before returning `client`; `receipt`, `provenance`, `status()`, `stop_receipt` and `close()` expose actual observations without the auth token. There is no score/query/KV cache; native compiled kernels can remain warm. Startup has a 180-second deadline, each singleton request a 60-second timeout, and cleanup uses cooperative TERM for 30 seconds then KILL for 10 seconds on only its owned group. Token removal is always attempted and any failure is preserved. Only trusted worker/init/client/scoring source is copied into the private runtime directory with hashes, so an installed main site-packages directory never enters the isolated interpreter's import path.

Standalone CLI Qwen searches install a SIGTERM handler before startup and restore the previous handler after backend/owner cleanup. The worker receives its intended owner PID explicitly, verifies its actual parent before imports/model loading/readiness and during its idle loop, and removes its token and closes resources if the parent disappears (including SIGKILL). An already orphaned startup never loads weights.

Search cancellation is checked before and after each singleton forward. An in-flight native forward finishes before the next check; MCP cancellation forwards the same request ID independently of the model lock. Timeout, malformed response, binding mismatch or native failure closes an owned worker before error propagation. If shutdown is unproved, subsequent embedding work is quarantined. A disconnected client cannot interrupt a Metal kernel instantly. Invalid requests with no forward do not silently substitute another model or ranking.

CPU injection tests verify source-level parity with the immutable original scorer, authentication/loopback/proxy/redirect rules, response identity/math, full50-to-prefix/tie behavior, exact compact packing/dependencies, overlay borrowing, cancellation, stale-source rejection, process lifecycle and failure cleanup.

The [actual production parity audit](../results/qwen-production-parity-2026-10-14/independent-findings.md) passes all 416 original literal pairs across nine sets: all 13 typed score/source fields, tokenizer input hashes and whole-line clipping match exactly. The 416 measured forwards plus one separate warmup reconcile with native counters and clean owned shutdown. This establishes parity with the original local MLX scorer; it does not establish official PyTorch parity or coding performance.

The [native HTTP/MCP smoke](../results/qwen-native-mcp-smoke-2026-10-14/verification.json) also passes. It checks four actual hybrid K24 searches, advertised MCP defaults, an explicit reranking override, exact compact source/token caps, and automatic saved-edit refresh. The edit embeds one changed window and reuses 60 vectors. All 96 measured forwards plus one warmup reconcile, and owned resources stop cleanly. This is a toy integration fixture, separate from the mixed HTTPX retrieval result and the prospective coding comparison.
