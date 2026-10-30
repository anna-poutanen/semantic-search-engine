# Live feature-provider source handoff

Read-only API inspection at preparation commit
`08a4d48ff4d59662e3f3bc71900d47b7f7de38ce`; no model imports, databases,
corpora or observed outcomes were used. These are implementation requirements
for the next phase, not an already implemented provider or frozen native run.

1. **Expose actual request context.** The original full-query vector is local
   to `SearchBackend.search` in `src/semantic_search/backend.py:405–422`; the
   current postprocessor receives neither that vector nor the executing index.
   An explicit research request context must retain that exact vector, snapshot,
   current model fingerprint and actual backend/index. Do not re-embed the full
   query or include it in a new clause batch.
2. **Bind the executing overlay backend.** `search_overlay` in
   `src/semantic_search/overlays.py:248` executes a child backend with a disposable
   request index. A provider closed over the parent would read saved vectors for
   unsaved source. Lookup must use the actual child and finish before its index
   is disposed, with exact buffer hashes/versions. Saved-source semantics remain
   unchanged.
3. **Add a validated vector accessor.** The SQLite store has search/source APIs
   but no current-source, fingerprint-bound document-vector reader. A read-only
   transactional accessor should bind current snippets to the active model,
   dimensions and exact rendered-document hashes. Include all full delivered
   dependencies, not just the raw retrieval pool. Never silently infer missing
   document vectors in a timed search. Clipped prefixes receive no semantic
   support from full-window vectors.
4. **Freeze the vector representation.** `store.py` stores canonical cached
   embedding components in float64 and separately normalizes/casts active search
   vectors to float32 (`_float32_vectors`, active publication near line 435).
   Native Qwen outputs are float32, so cached widening may be lossless, but the
   future accessor must verify rather than assume that property. The existing
   feature boundary rejects lossy float32 values. Choose, document and bind the
   representation before outcomes; do not switch between cached output and
   active normalized search vectors opportunistically.
5. **Observe actual forwards.** `embed_queries` in `embeddings.py:215` batches
   extra clauses, but exposes no forward observer. The future ledger must record
   actual scheduled starts/completions/failures, original indices, input token
   lengths and padding limits inside `_embed_texts`. One API invocation can use
   multiple forwards. Preserve existing cancellation and cache behavior.
6. **Handle modes without a query vector.** BM25 intentionally skips query
   embedding. Either keep this research provider inactive there or prospectively
   count a new full-query encoding plus clause work. Empty or abstained pools
   also need an explicit no-vector/no-source policy; do not fabricate an existing
   query forward or count a failed provider as a successful unchanged search.
7. **Keep terminal allocation.** The prepared adapter calls actual coverage
   once, the feature provider once for active clauses, then semantic allocation
   once. It never invokes the lazy reference factory or performs later backfill.
   Preserve that ordering and include defensive-copy/validation cost in timing.

Before executing observed cases, commit the completed provider/capture harness
and all bindings under the [existing protocol](../../docs/semantic-context-protocol.md).
Reproduce original controls before candidate outcomes. Required-implementation
quality and native latency remain unmeasured in this preparation phase.
