# Independent Koa source review

The pinned Koa corpus and labels pass independent CPU-only semantic and byte checks. All six positive queries have complete, necessary executable evidence; all 11 required groups are covered by actual source windows. The two absent queries are absent within the declared seven tracked `lib` files. No material label correction is required.

This corpus **fails comparative applicability for the current clause-fusion rule**: all eight unchanged queries produce only their original full-query input, with zero extra clauses. Single-list RRF preserves full-query ranking. It cannot discriminate a clause-fusion effect. Query wording was retained, and `inference_authorized` remains false.

| Query | Evidence assessment |
|---|---|
| Header-only response metadata | String body type/UTF-8 length assignment and body-free HEAD dispatch require two files. |
| Middleware error response | Middleware rejection wiring and writable/unwritten text error handling require two files. |
| Stream response lifecycle | Readable classification, body cleanup/length handling, and pipeline error forwarding require three files. |
| Client address trust/cache | Forwarded hop selection and cached socket fallback require two getter bodies in one file. |
| Relocation response | The full method includes destination encoding, redirect-status preservation, escaped HTML, and plain text. |
| Freshness guard | The full getter contains the GET/HEAD and 2xx/304 gates before delegating header comparison. |
| Remote-body fetch/retry | Inbound server and relocation URL handling do not implement outbound fetching with backoff. |
| Rotating body encryption | Plain JSON serialization, signed-cookie keys, and TLS state do not implement the requested encryption. |

I independently checked clean Git commit `2fecd029c6ce6b79d65158d28a76538f7f6b0378`, exact source membership/file hashes, source header hashes, pinned Git/local/license notice equality, all 175 extractor windows, and every target's declaration anchor, full physical evidence bytes, SHA, and actual window eligibility. The unchanged extractor fingerprint and production implementation hashes match the author audit. The full MIT notice is retained in `NOTICE.md`.

The absence assessment combines a manual inventory of all seven implementation files with searches for outbound HTTP, retry/backoff, and body encryption. It applies to this source scope; external dependencies and user middleware are excluded. The stream label also preserves the actual distinction between replacing a stream with another stream and replacing it with a non-stream body.

No model, retrieval, or ranking calls were made, and no new clause experiment outcomes were opened. This review establishes static source eligibility, not retrieval quality, dynamic runtime behavior, performance, or a pretraining holdout. Full executable-body evidence is stricter than a minimal branch but is feasible under the existing 160-line/40-overlap windows. Machine-readable bindings, per-group window proofs, and limits are in [source-review.json](source-review.json).
