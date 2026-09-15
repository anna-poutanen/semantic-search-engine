# Experimental Qwen3 batching mechanics

This new experiment uses the original pinned BF16 `Qwen/Qwen3-Reranker-0.6B` checkpoint, revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`, through MLX 0.32.3 and MLX-LM 0.32.0. It imports the unchanged original `PairBuilder`, retaining the exact literal query/source instruction, complete-physical-line clipping, 8,192-token sequence cap, final-token projection, and stable yes/no binary-score math. It does not change production retrieval or previous experiment artifacts.

The local primary implementation inspected is MLX-LM's installed `mlx_lm/models/qwen3.py` (SHA-256 `7e29b7055d8e526a0ede6e2740a3038ad24d0cef6a1abb446c411d3fbd0efa2d`), `base.py` (`b782b2ac0f55ed8fa9c1afce1f36307d5d8714aad2c0991d63998047f7507174`), and `rope_utils.py` (`06466d57dfe1ccc1e61ba595c0a8f2dd3a14bcb5c6a1d06c67591dc03128606b`). Import-free preflight checks these supporting source bytes as well as the original runtime pins; the explicit loader verifies their imported origins and bytes. Importing the new module or running its CLI preflight does not load weights or import MLX.

## Why right padding preserves the real positions

The pinned `Qwen3Model.__call__` does not accept an externally supplied attention mask. With no cache, it creates the native causal mask (`"causal"` for sequence lengths greater than one; no mask is needed at length one). Each layer passes that mask to native scaled dot-product attention. RoPE receives no cache and therefore uses offset zero for both queries and keys.

For a row with real length `n`, right padding places real tokens at positions `0..n−1` and padding at `n..L−1`. A retained real query at position `i<n` can attend only keys `j≤i`; every such key is real. Its prefix, key visibility, and RoPE positions therefore match the unpadded sequence. The transformer normalizations and MLPs operate per token, and attention never crosses the batch axis. Induction over layers preserves the real hidden states mathematically. Padded query rows may compute outputs, but none is selected or fed back into a retained real position. This proof avoids allocating an explicit `B×L×L` padding mask.

The scorer gathers each row at `n−1` before projecting the head, rather than at the padded tensor's `L−1`. It projects only one hidden state per row. A singleton, including a final singleton inside a larger configured batch, calls the unchanged original `score_pair` function with no padding. The proof does not guarantee bitwise equality across differently shaped BF16 matrix kernels; actual numerical parity is a separate gate.

## Scheduling and receipts

Configured batch sizes are 1, 2, 4, and 8. Batch size 1 keeps original pair order. Larger configurations sort by ascending tokenized length, breaking ties by original candidate index. A greedy group closes when the configured size would be exceeded or its padded allocation would exceed **16,384 token slots**. This allocation bound is not a memory-byte limit: the model's attention, hidden states, kernels, and allocator also consume memory. Every built sequence remains in the request exactly once; an incompatible lower token budget fails before inference. No sequence is dropped, concatenated into another sequence, or served from an input/KV/result cache. The native runtime may reuse compiled kernels normally during warm measurements. Results are restored to original candidate order.

Each actual batch attempt has a started receipt and a completed or failed receipt. Receipts map original pair indexes and exact input-token hashes into the forward, and record configured size, actual size, real lengths, padded length, final positions, padding token ID, right padding, zero position offset, causal mask, real input tokens, allocated input tokens, padding tokens, and padding ratio. Attempted/completed **batch forwards** and attempted/completed **pairs** are separate counters. One completed batch with eight pairs is one forward and eight scores. The original source and delivered-source hashes remain attached to each score.

## Prospective resident-model parity gate

Before any quality or latency matrix runs, use predeclared non-benchmark literal query/source pairs with varied lengths, Unicode, duplicate pairs, reordered peers, and a complete-line clipping boundary. Use the same resident model and checkpoint for a batch-size-1 reference and configurations 2, 4, and 8, with exact builder/token/truncation hashes checked. The request permutation must restore to the same pair identities. Require finite scalar scores in all configurations.

The internal BF16 gate freezes these numeric bounds before model inference:

- Absolute error in each yes and no logit: **≤0.0625**.
- Absolute error in the yes-minus-no logit difference: **≤0.125**.
- Absolute error in the yes probability signal: **≤0.025**.
- Reject any ranking inversion whose sequential logit-difference margin is **>0.25**, twice the maximum difference error. Record every flip within that band as a near-tie change, including its original margin and both new scores.
- Require unchanged top-five membership for each prospective parity query except swaps inside that same predeclared near-tie band; record those exceptions explicitly rather than hiding them.

These are internal checks against the original local sequential scorer, not parity with the official PyTorch implementation. Even a passing gate does not justify reusing old sorted rankings: the study must score actual smaller shortlists in each batched configuration, rerun the unchanged context packer, and measure actual delivered coverage and warm latency. A failed gate is preserved as a failed attempt and blocks that configuration from the comparative study; tolerances must not be relaxed after seeing results.
