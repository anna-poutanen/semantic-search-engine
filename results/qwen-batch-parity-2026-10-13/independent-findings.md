# Independent Qwen optimization audit

Verified numerical diagnostics from a failed supervised parity run; excluded from setup selection

Frozen source/input-token, actual native-forward/logical-pair, layout/padding, scalar and fixed numerical failures were verified. The original supervisor cleanup failed and remains preserved. Later read-only process/socket/authentication absence and the resident's own clean stop do not qualify this run for setup selection.

Stage: parity
Passing batch sizes: 1

Every detailed comparison, observed native/pair/token total and immutable artifact digest is in `independent-audit.json`.

- CPU auditor independently rebuilds tokenizer IDs, logical grouping/layout/padding and actual scalar math without loading model weights.
- Native causal and final-token path relies on committed frozen producer and verified pinned native source/loaded checkpoint witnesses, plus actual receipts and internal BF16 parity.
- This is internal BF16 B1-versus-B batching parity, not numerical official/PyTorch parity or a production/coding latency benchmark.
- Failed batching configurations remain retained and excluded rather than relaxed or retried.
