Reusable independent native-smoke audit is ready before any new installation smoke output. All 100 CPU tests and Ruff passed. Source snapshots and exact hashes are retained in review.json and sources/. No production/harness/runtime files changed, no inference or hosted calls ran, and weight bytes were not read.

Run after a new native smoke completes and its frozen plan is committed:

```sh
.venv/bin/python -B scripts/audit_native_qwen_mcp.py \
  --study results/<new-native-smoke> \
  --work-dir .cache/<new-native-smoke-work> \
  --reranker-python .cache/<fresh-venv>/bin/python \
  --checkpoint .cache/<fresh-checkpoint> \
  --context-tokenizer .cache/tokenizers/9b5ad71b2ce5302211f9c61530b329a4922fc6a4 \
  --output results/<new-native-smoke>/independent-audit.json
```

Paths must match actual retained receipts. The full weight is not reread. Raw MCP text is not retained by the producer; the independent checker reconstructs compact token counts and exact structured HTTP/MCP equality. This certifies toy integration, not retrieval or coding quality or token savings.
