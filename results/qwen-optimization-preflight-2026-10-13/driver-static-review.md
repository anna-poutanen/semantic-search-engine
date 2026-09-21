# Independent final optimization driver and lifecycle review

**Passed static review** before any optimization inference. The
[receipt](driver-static-review.json) binds the final driver, worker, supervisor,
pipeline, scorer, protocol and test source hashes. **70 CPU tests passed**,
including **39 new driver tests**; scoped Ruff passed. No model weights or native
runtime were loaded and no embedding/retrieval/model calls were made.

The plan binds exact code/runtime/package/source inputs and decision hashes,
then rechecks them before an attempted run. Parity uses fixed development source
pools, fresh B1 reference scoring and frozen numeric bounds. Stable original-index
ties, near-tie inversions and top-five changes are explicitly checked. A CPU check
with the actual pinned tokenizer verified that the four-line clipping diagnostic
retains exactly two full physical lines and 4,605 tokens. Parity bypasses embedding
weight verification and the retrieval pipeline.

Actual searches recompute one query embedding, retrieve full50, freshly score
only prefix K and pack only that ranked prefix. Original dependency expansion,
source/token limits and full-span gold validation remain intact. Index copies,
embedding owner, resident scorer and root-owned sessions have explicit cleanup.
Forward/pair accounting remains separate; no query/score cache or retry is added.

One concrete blocker was fixed before inference: the driver used to overwrite the
pipeline's detailed `failure.json`. The final code preserves that receipt and its
completion/counters, writing a separate `driver-failure.json` when needed. A
regression test verifies preservation.

Root acknowledged two startup limitations: cooperative worker signal handlers
are installed after loading/warmups, and exclusive forward-ledger open precedes
the worker's try/finally. A failure in either interval can lack worker stop/counter
receipts. The supervisor still bounds and stops its owned process and preserves
failure evidence. Such an incomplete start must remain a failed attempt and
cannot pass the successful clean-stop audit. These limitations do not invalidate
a complete run with actual driver, worker and supervisor completion receipts.

Static checks do not establish numeric model parity, retrieval quality, warm
latency, native memory peaks or actual process cleanup. Those remain required
actual-run and independent post-run audit gates.
