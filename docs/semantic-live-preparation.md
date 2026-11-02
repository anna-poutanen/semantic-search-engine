# Live semantic capture: CPU preparation

The live feature provider and request-capture components are implemented and
tested with invented CPU models. Real model testing is still paused under the
user's code-preparation-only instruction. No observed retrieval outcomes or
reserved attrs data were used. This extends the
[October 30 preparation](semantic-context-preparation.md); it does not establish
retrieval improvement, native latency or coding-token savings.

## What is connected

`SearchBackend` accepts explicit `research_postprocessor` and
`research_forward_observer` callbacks. The service and CLI do not install them.
The ordinary coverage/backfill default and published v0.1.5 assets remain in
place. The research hook receives the executing request's original query vector,
model identity, source snapshot, vector index and provenance callbacks. Its
defensive query-vector copy preserves the original float32 bytes; the full query
is never re-encoded by the feature provider.

`make_live_semantic_postprocessor` creates a fresh provider for every request.
It performs one actual coverage pass, obtains features, then applies the
unchanged `semantic_final_v1` rule once, with no subsequent backfill. The original
[allocation protocol](semantic-context-protocol.md), quality gates and limits are
unchanged. The feature provider supports dense, hybrid and existing multifile
retrieval. BM25 remains inactive because it has no original embedding vector;
empty/abstained pools and unavailable original vectors also return exact
coverage without clause inference. Missing required document vectors or broken
active bindings fail the request and retain a failure receipt.

Only the extra literal clauses enter `embed_queries`. A single API invocation
can schedule several forwards. Optional observations record starts, completions
and failures with original input indices, prompted-text/token hashes, actual
token lengths, padded dimensions, output shape and monotonic timing. A start
alone does not prove model work: its observer can fail before the call. Terminal
completed/failed events count actual attempted forwards. Observers cannot mutate
the scheduler or vectors through their isolated event dictionaries. Original
model failures remain primary if reporting also fails.

## Current source and vector identity

`SQLiteIndex.read_semantic_vectors` reads full literal windows and original
canonical cached outputs in one read-only transaction. It validates active model
metadata/fingerprint/dimensions, current snippet identity and source, rendered
document hashes, and required-vector membership. The cache stores float64;
components must round-trip exactly to float32. Lossy conversion fails. The
separately normalized `active_vectors` search representation is not substituted.
The accessor neither refreshes nor infers documents.

SQLite does not store editor buffer versions. The provider therefore compares
every raw/reference row's provenance with the actual executing backend and
checks its source snapshot before reading, before clause encoding and after
encoding. Those checks, defensive copies and feature validation are part of
future measured request time. Their cost has not been measured natively.

Unsaved editor searches use the disposable child overlay index and callbacks.
They finish vector capture before the child is disposed and preserve the saved
parent index and resident model. The tests exercise this through the actual
backend, not a provider bound to the saved parent.

## Captures and failure evidence

`scripts/capture_semantic_request.py` supplies `SemanticRequestCapture` for one
research request. It requires its own hooks on the executing backend, writes an
exclusive attempt manifest/request before execution, retains event references
during the request, then serializes and independently audits the actual final
payload afterward. Existing attempt paths and object reuse are refused. Failure
receipts preserve partial provider and query/document forward evidence; failures
are re-raised rather than recorded as successful unchanged searches.

Backend observations label document and original-query work separately. The
provider's zero document-inference count describes only the provider. A cold
request can still embed documents during refresh; capture counts retain that
work. Native warm status and model provenance cannot be inferred from invented
events or a zero provider-local count. Snapshot consistency and individual-source
auditing likewise do not establish behavior correctness.

The final compact response, exact token count, input bindings, code hashes,
forward records and independent reconstruction are retained outside the product
response. Post-request copying, serialization and audit cost is separate from
backend request timing; synchronous forward observation and provider work stay
inside the request. The capture component is not yet the prospectively frozen
393-case benchmark runner or its native latency harness.

## Validation and next work

The [preparation results](../results/semantic-live-preparation-2026-11-02/findings.md)
record affected CPU tests, lint, source invariants and independent review.
Fixtures use constructed source, stub tokenization/forwards and temporary CPU
indexes. They cover original-vector/control parity, exact cached vectors,
overlay isolation, inactive searches, batching, cancellation, stale source,
observer/provider failures, partial receipts and no-overwrite capture behavior.
The previous study's failed quality gate is preserved.

Next, finish and commit the observed control/feature capture plan, trusted source
and raw-pool manifests, runtime/model/checkpoint identities, and replay/native
runner bindings before model execution resumes. Existing vectors must match the
current metadata fingerprint; historical caches cannot be relabeled. Reproduce
all 786 original controls before the 393 candidate cases (201 distinct units).
For historical traces without a retained original query vector, any fresh query
encoding must be named and counted as new feature work; it cannot be presented
as reuse of the original retrieval vector. This live provider's reuse guarantee
applies to an actual executing backend request, not an arbitrary saved pool.
Require zero individual required-evidence losses against both controls and at
least one new distinct complete multi-file behavior over coverage. Only a
qualifying rule proceeds to reserved attrs and actual warm timing, with median
latency at most 1.25 times each control. Coding comparisons remain gated on that
retrieval evidence.
