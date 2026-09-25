# Independent Qwen optimization audit

Independent CPU source/token/native-forward/full-span/packing/warm-latency audit; no inference

All frozen source, input-token, actual native-forward, logical-pair, layout/padding, scalar, timing and cleanup checks passed.

Stage: fresh
Passing batch sizes:

Every detailed comparison, observed native/pair/token total and immutable artifact digest is in `independent-audit.json`.

- Quality uses unique first-pass original full-span OR/AND source labels; every repeated actual warm sample remains in latency distributions.
- Warm measurements include actual query embedding, depth50 retrieval, authenticated blocking scoring HTTP, instrumentation and unchanged context packing, excluding model/index setup and generic warmups.
- These are in-process experimental intervals on one Mac, not full production HTTP/MCP/provider/coding latency or billed token usage.
- Static callee expansion may deliver source outside the scored prefix; candidate completeness is not a delivered-coverage ceiling.
- All eight earlier repositories including Koa are observed development; HTTPX source-authored freshness does not imply pretraining exclusion.
- MLX memory is process allocator peak/cleanup, not OS RSS or simultaneous combined process memory.
- Original weight identity relies on actual full-byte verification receipts and unchanged file witnesses; CPU auditing does not repeat inference or official/PyTorch parity.
