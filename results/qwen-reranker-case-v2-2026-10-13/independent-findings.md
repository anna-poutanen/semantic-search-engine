# Selected known-miss development reranker audit

All 50 original dense candidates and 100 started/completed forward receipts pass independent source, tokenizer/template/token-cap, scalar-score and runtime checks. No scored source prefix was truncated.

The unchanged packer exactly reproduces the baseline compact payload, then changes complete executable evidence ranks from `[None, 2, 1]` to `[7, 1, 2]`. All three required groups are now delivered on this deliberately selected prior miss; no embedding calls were made during packing. Actual compact payloads contain 1055 and 1268 tokens, both within 4096; saved-source hashes and whole physical-line bounds are verified.

- One deliberately chosen known miss on already-observed Express development data; no holdout, broad quality, default-change, coding correctness, billing, HTTP or P95 claim.
- Original BF16 weight SHA is checked through retained full-byte verification; the auditor rehashes tokenizer/config/source, not weight bytes or model outputs through inference.
- No PyTorch numerical parity proof; two logits are ranking signals, not calibrated correctness.
- Forward intervals include graph construction/evaluation/scalar extraction and instrumentation. Total interval also includes load/tokenization/setup/cleanup; no inference-only attribution.
