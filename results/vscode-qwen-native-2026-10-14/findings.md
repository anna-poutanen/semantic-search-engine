# Actual optional Qwen editor integration

The saved-source, dirty-buffer and reranking-off cases pass in pinned VS Code
1.141.0 with the newly installed isolated Qwen runtime. Normal fixture folder
settings select hybrid retrieval, Qwen K24/B1, five results, 12,000 source
characters and 2,000 compact tokens. The authored Python/JavaScript/TypeScript
fixture has only five windows, so each Qwen search scores all five available
windows. This does not measure a full 24-window query or retrieval quality.

The [independent CPU audit](../vscode-qwen-auditor-correction-2026-10-14/independent-audit.json)
reconstructs the literal scoring inputs, tokenizer IDs, score arithmetic,
returned sources, dirty-buffer scopes and compact token caps. It reconciles
**10 measured singleton forwards plus one startup warmup**, 22 ordered native
events, seven successful searches, seven query embedding API entries and two
document API calls containing six texts. One cancellation attempt stops before
query embedding; it does not establish interruption of a running Metal forward.
Reranking off omits the provider and performs no reranker forward.

The actual host verifies captured plaintext previews, exact selections and
navigation in all three languages. The dirty TypeScript buffer retains its
version and full text hash. Indexed source and the saved index manifest remain
unchanged; temporary folder settings and unsaved text are restored. Final relay
receipts are stable with no unfinished or transport-error records. All three
recorded owned groups, both listeners and the worker auth token are gone; the
owned editor profile is removed. The hash-bound signal journal is empty.

The original auditor preparation and failed receipt-field lookup are preserved.
An archive-field correction raced the native run and its timestamp evidence is
retained. The final executable-field correction is explicitly **post-output**;
106 CPU tests and Ruff pass, and the corrected audit replays the unchanged
native artifacts without another model/editor run. See the
[correction record](../vscode-qwen-auditor-correction-2026-10-14/qualification.json).

This is an isolated Extension Development Host with Workspace Trust disabled
for the explicit fixture. It establishes optional-provider integration, source
delivery and owned cleanup. Ordinary installed-profile activation, Restricted
Mode enablement, large-codebase relevance, latency and coding-agent token
savings are separate claims.
