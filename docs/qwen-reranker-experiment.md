# Prospective Qwen3 reranker feasibility draft

This is an experimental script, with CPU fake-model tests only. No checkpoint was
downloaded, no real pair was scored, and no production/default retrieval behavior
or runtime lock was changed. There are no quality, speed, memory or billing claims.

The main `.venv` has MLX 0.32.3 and tokenizers 0.23.3, but **does
not contain mlx-lm**. Preflight therefore reports a blocker. A future separately
approved runtime must contain mlx-lm 0.32.0 with the inspected Qwen3 source hash;
this draft does not install it or substitute the vendored audio implementation.
Root prepared a separate ignored `.cache/reranker-experiment/.venv`; its
metadata-only preflight confirms these three version pins and the inspected Qwen3
source SHA-256 `7e29b7055d8e526a0ede6e2740a3038ad24d0cef6a1abb446c411d3fbd0efa2d`.
That check loaded no weights and imported no MLX code.

## Fixed prospective implementation

- Official model: [Qwen/Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/tree/e61197ed45024b0ed8a2d74b80b4d909f1255473), revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`.
- Original single BF16 `model.safetensors`: 1,191,588,280 bytes; SHA-256 `27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b`.
- Exact config/tokenizer hashes are embedded in the script. Metadata preflight
  reads local config/tokenizer bytes and weight size, but never weight bytes.
  Explicit `--run` verifies the full original weight hash before MLX imports.
  File identity/stat witnesses are checked before and after loading/scoring;
  the checkpoint must remain fixed. This is not an adversarial filesystem lock.
- The [official Transformers scoring example](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B#using-transformers)
  supplies the instruction/query/document wrapper and closed thinking suffix.
  This draft preserves that wrapper and uses one fixed code instruction:
  “Given a requested code behavior, retrieve executable code that directly
  implements the behavior or any necessary supporting part of that behavior.
  Names, comments, or related topics alone are insufficient.”
- Every pair is forwarded separately, unpadded, without KV cache, generation,
  temperature or threshold. The [primary MLX-LM Qwen3 implementation](https://github.com/ml-explore/mlx-lm/blob/v0.32.0/mlx_lm/models/qwen3.py)
  returns normalized hidden states from `model.model`. Only `hidden[:, -1:, :]`
  reaches the tied embedding projection or untied head. This avoids materializing
  vocabulary logits at every sequence position; the backbone still processes the
  complete pair and its resource cost needs real measurement.
- Yes/no final-token logits yield both their difference and a stable two-class
  softmax signal. They are ranking signals, not calibrated correctness estimates.
- The complete pair, including both wrapper segments, is capped at 8192 tokens.
  The query/instruction are never truncated. If necessary, the source loses whole
  trailing physical lines (CR/CRLF/LF only), preserving an exact literal Unicode
  prefix. The output records full/delivered UTF-8 hashes, bytes, physical lines,
  token count and truncation. A source with no complete line that fits fails.
  Re-encoding prefixes favors simple verifiable clipping over batching speed.

The constructor accepts an injected tokenizer and performs no I/O. Preflight
imports neither MLX nor Hub clients. The opt-in loader uses only the pinned local
tokenizer JSON and built-in Qwen3 class; it never runs Hub code, reads a custom chat
template, applies an adapter or reads user model settings. There is no downloader.
The package/child module origins are checked before import, and the imported Qwen3
file path/hash is checked again and recorded before model construction, refusing
ordinary `PYTHONPATH` or editable-module shadowing. The interpreter and installed
environment themselves must be trusted.

## CPU check and future opt-in

From the project checkout, run:

```sh
.venv/bin/python -B scripts/prototype_qwen_reranker.py --preflight
.venv/bin/python -B -m pytest -q tests/test_prototype_qwen_reranker.py
```

An already-present checkpoint can be inspected with `--checkpoint '/absolute/path
with spaces/checkpoint'`. No local checkpoint is required for default preflight.
After separately approving and preparing the exact runtime/checkpoint, an owned
UTF-8 JSON file can contain at most 50 objects, each with only `query` and `source`
literal text. It must contain no labels. The future explicit command is:

```sh
'/absolute/path to approved environment/bin/python' -B scripts/prototype_qwen_reranker.py \
  --run --checkpoint '/absolute/path to exact checkpoint' --pairs '/absolute/path to pairs.json'
```

No inference is authorized by this document. Before a comparative experiment,
freeze the script/runtime/checkpoint hashes, source snapshot, ordered candidates,
instruction and packing limits; independently review eligibility and labels;
retain every score, clipped source prefix, regression/decoy, cost and failure. Keep
the candidate order and delivered-context packer fixed so a later comparison can
isolate reranking. Previously observed Express/Zod cases are development evidence;
any genuine unseen holdout requires a separate prospective protocol. This draft
does not establish latency suitability, injection resistance, cross-platform
support or parity with the official Transformers output. Real runtime smoke and
numerical comparison are required before treating the adapter as validated.
