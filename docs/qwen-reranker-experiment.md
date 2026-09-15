# Local Qwen3 reranker development experiment

The [full frozen Mac study](../results/qwen-reranker-full-2026-10-13/findings.md)
now covers 144 distinct queries across eight real codebases, including fresh Koa,
with three actual repeated passes and 43,200 benchmark pair forwards. Scoring
the full 50-candidate pool improves complete delivered evidence from 107 to 110
of 118 positives for dense and from 103 to 111 for hybrid. Dense has four gains
and one regression; hybrid has ten gains and two regressions. Fresh Koa stays
6/6 for dense and improves from 5/6 to 6/6 for hybrid.

Warm median/P95 is 7.345/16.592 seconds for dense + Qwen and 9.461/17.049 for
hybrid + Qwen, compared with 0.283/0.675 and 0.290/0.856 without reranking.
These are resident in-process pipeline timings including a scorer HTTP hop,
not the full production HTTP/MCP route. Model loading and index setup are
excluded. The resident scorer's MLX allocator peak is 1,834,129,428 bytes;
it does not measure operating-system RSS or combined embedding/scorer memory.
The modest quality gain does not justify making the 50-candidate sequential
scorer the default. Shortlist/batching validation precedes another coding study.
All 26 absent queries return candidates; no absence threshold is fitted.
Production defaults and the main runtime lock remain unchanged.

The [frozen protocol](qwen-reranker-full-protocol.md), raw outputs, independent
audit and evidence explanations preserve all scores, actual forward receipts,
source spans, context budgets, gains and regressions. The primary metric requires
complete original executable source intervals, independent of symbol identity;
historical Python symbol-based scores remain a separate diagnostic. No numerical
PyTorch parity, coding correctness or paid-agent token benefit is established.

## Earlier selected-case probe

The prototype previously had one independently audited
real Mac run on a deliberately selected, previously observed Express miss.
It scored all 50 retained dense candidates; the unchanged context packer
delivered all three required evidence groups, compared with two in the baseline.
The child took 5.13 seconds, including checkpoint verification, loading,
tokenization, scoring and disposal. This is development evidence from one chosen
case, not a broader quality, warm service latency or billing result.
Production defaults and the main runtime lock remain unchanged.

The [successful run and independent audit](../results/qwen-reranker-case-v2-2026-10-13/independent-findings.md)
retain every pair, score, forward receipt and packed response. The
[first failed attempt](../results/qwen-reranker-case-2026-10-13/independent-findings.md)
loaded and evaluated weights, then rejected an instrumentation wrapper signature
before scoring any pair. Its records remain intact; the corrected wrapper was
frozen separately before the successful run. Both used the same model, inputs,
ranking policy and packing limits.

The main `.venv` has MLX 0.32.3 and tokenizers 0.23.3, but **does
not contain mlx-lm**. Preflight there therefore reports a blocker. The separate
experimental runtime contains mlx-lm 0.32.0 with the inspected Qwen3 source hash;
the script does not install it or substitute the vendored audio implementation.
Root prepared a separate ignored `.cache/reranker-experiment/.venv`; its
metadata-only preflight confirms these three version pins and the inspected Qwen3
source SHA-256 `7e29b7055d8e526a0ede6e2740a3038ad24d0cef6a1abb446c411d3fbd0efa2d`.
That metadata check loaded no weights and imported no MLX code. The subsequent
explicit run used the original BF16 checkpoint and recorded its 34-package
isolated environment. The MLX allocator process-lifetime peak was 1.59 GB;
this is not an operating-system RSS measurement.

## Fixed experimental implementation

- Official model: [Qwen/Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/tree/e61197ed45024b0ed8a2d74b80b4d909f1255473), revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`.
- Original single BF16 `model.safetensors`: 1,191,588,280 bytes; SHA-256 `27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b`.
- Exact config/tokenizer hashes are embedded in the script. Metadata preflight
  reads local config/tokenizer bytes and weight size, but never weight bytes.
  Explicit `--run` verifies the full original weight hash before MLX imports.
  File identity/stat witnesses are checked before and after loading/scoring;
  the checkpoint must remain fixed. This is not an adversarial filesystem lock.
- The [official Transformers scoring example](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B#using-transformers)
  supplies the instruction/query/document wrapper and closed thinking suffix.
  The script preserves that wrapper and uses one fixed code instruction:
  “Given a requested code behavior, retrieve executable code that directly
  implements the behavior or any necessary supporting part of that behavior.
  Names, comments, or related topics alone are insufficient.”
- Every pair is forwarded separately, unpadded, without KV cache, generation,
  temperature or threshold. The [primary MLX-LM Qwen3 implementation](https://github.com/ml-explore/mlx-lm/blob/v0.32.0/mlx_lm/models/qwen3.py)
  returns normalized hidden states from `model.model`. Only `hidden[:, -1:, :]`
  reaches the tied embedding projection or untied head. This avoids materializing
  vocabulary logits at every sequence position; the backbone still processes the
  complete pair. The full study above now measures repeated resident pipeline
  latency with this sequential implementation.
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

## CPU check and explicit local run

From the project checkout, run:

```sh
.venv/bin/python -B scripts/prototype_qwen_reranker.py --preflight
.venv/bin/python -B -m pytest -q tests/test_prototype_qwen_reranker.py
```

An already-present checkpoint can be inspected with `--checkpoint '/absolute/path
with spaces/checkpoint'`. No local checkpoint is required for default preflight.
After preparing the exact separate runtime/checkpoint, an owned
UTF-8 JSON file can contain at most 50 objects, each with only `query` and `source`
literal text. It must contain no labels. The explicit command is:

```sh
'/absolute/path to experimental environment/bin/python' -B scripts/prototype_qwen_reranker.py \
  --run --checkpoint '/absolute/path to exact checkpoint' --pairs '/absolute/path to pairs.json'
```

Before a comparative experiment,
freeze the script/runtime/checkpoint hashes, source snapshot, ordered candidates,
instruction and packing limits; independently review eligibility and labels;
retain every score, clipped source prefix, regression/decoy, cost and failure. Keep
the candidate order and delivered-context packer fixed so a later comparison can
isolate reranking. The successful case retained the original baseline inputs and
packer, verified zero new embedding calls, and ranked by descending yes-minus-no
logit difference with original dense rank and stable ID as tie-breakers.
Previously observed Express/Zod cases are development evidence;
any genuine unseen holdout requires a separate prospective protocol. This probe
does not establish latency suitability, injection resistance, cross-platform
support or parity with the official Transformers output. Numerical comparison
remains outstanding. The full study above completes the development matrix,
fresh repository check and repeated warm pipeline measurements for the fixed
50-candidate sequential scorer. All observed queries, including Koa, are now
development evidence for future tuning.
