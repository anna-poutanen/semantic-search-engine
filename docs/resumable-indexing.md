# Resumable indexing

**Included in v0.1.4.** The [Mac download](getting-started.md) and source checkout
both support completed-batch resume. The earlier v0.1.3 ZIP predates this feature.

Qwen indexing saves each completed embedding batch to SQLite before reporting
that batch as finished. If the process is cancelled, exits or is killed, the next
refresh reuses compatible saved documents and embeds only the missing ones. No
resume flag or separate checkpoint file is needed. Checkpoints are saved even
when progress output is disabled.

## Restart an interrupted index

From the extracted v0.1.5 app folder, rerun the same own-repository command:

```sh
bash "Get Started.command" --repo /absolute/path/to/your/repo --source-root src
```

For a developer source checkout, rerun the same service command instead:

```sh
.venv/bin/semantic-search serve \
  --repo /absolute/path/to/your/repo --source-root src
```

Keep the repository path, source root, exclusions and index-cache directory the
same. Those settings select the cache; moving or deleting it prevents reuse.
The launcher uses the same automatic behavior when rerun with the same
own-repository arguments. The toy walkthrough creates a fresh demo
repository each time, so it is not a restart test for an existing repository.

Restart still includes source scanning, parsing, model initialization and final
index publication. With progress enabled, the `cache` phase reports compatible
unique `cached_documents` and `pending_documents` before inference. These are
document counts, while final `reused_windows` and `indexed_windows` are source
window counts. Checkpoints from an earlier interrupted attempt count as reused
windows when that later refresh completes.

An in-flight model batch may run again. Work is reusable only after its validated
vectors have committed to SQLite; a forward pass that finished just before a
cancellation check or process termination might not have reached that commit.

## Reuse and publication rules

A reusable vector must match both:

- SHA-256 of the rendered embedding document: repository-relative path, symbol
  and exact source text, using the production document formatter.
- The frozen embedding model/settings fingerprint, with compatible dimensions.

Line numbers and window IDs can change without inference if the rendered
document remains identical. Editing its text or renaming its path changes its
identity. Changing the model or embedding settings selects different cached
vectors. A branch change still triggers source verification even when matching
documents can reuse embeddings.

Completed batches populate only the reusable vector cache. The active source
windows, keyword index, dense vectors and active metadata are replaced together
in one final transaction after all embeddings and the captured source pass
validation. Source and cancellation checks run before active writes and again
immediately before their transaction commits.

If a refresh fails before publication, the previous active snapshot remains
intact and completed batch vectors remain cached. An error reported after the
commit cannot undo an already published snapshot. A first index interrupted
before publication has no active snapshot yet. The backend still validates current source before
searching; it can reject a request rather than returning an old snapshot for
changed files. Checkpointing does not promise concurrent search availability
during indexing: the backend serializes model work, refreshes and searches.

If source changes during inference, that attempt cannot publish the captured
old source. Its completed vectors can still be reused on the next attempt when
their document hashes match current source. A changed implementation requires
new inference. Historical cache entries never become search results on their
own; retrieval uses only the validated active snapshot.

This change keeps the existing SQLite schema, embedding metadata and model
fingerprint. Compatible v0.1.3 vectors remain valid; batch resume alone does not
force a rebuild or change retrieval settings.

## Custom embedders

The production Qwen embedder and cancellation wrapper support the optional
`batch_callback(indices, vectors)` argument to `embed_documents`. Indices name
positions in that call's missing-document input list, including when native
batches finish in a different order. Each completed index must be reported
exactly once. Vectors must be real, finite, nonzero and dimensionally compatible.
The callback returns only after the whole validated batch is durably cached.

Before publication, the store verifies that callback output covers the entire
request and matches the returned complete embedding matrix. Invalid callback
input cannot publish a partial active snapshot. Previously validated cache
batches can remain available after an attempt fails later validation.

Legacy embedders without batch callbacks continue to work. They keep the
whole-result fallback: new vectors are saved with successful active publication,
so an interrupted inference call cannot checkpoint its partial work. Merely
implementing a progress callback does not enable resume.

## Verification

The implementation adds 55 CPU tests across
[`test_embedding_checkpoints.py`](../tests/test_embedding_checkpoints.py),
[`test_checkpoint_store_edges.py`](../tests/test_checkpoint_store_edges.py) and
[`test_resumable_backend.py`](../tests/test_resumable_backend.py). They cover
length-sorted batch ordering, progress-disabled resume, invalid callback data,
model/content isolation, independent-writer cache consistency, cancellation and
source changes during final SQL writes.

The process-kill integration test waits for a marker emitted after a checkpoint
callback returns, verifies the old source/keyword/dense snapshot through another
connection, sends SIGKILL, then starts a new process and checks that only missing
documents are embedded. These tests use deterministic CPU vectors; they do not
measure native-model latency, retrieval improvements or another Mac's behavior.

A separate [real Qwen verification](../results/resumable-indexing-2026-10-17/findings.md)
passes on twelve original Flask source windows. A fresh process reused one
existing document plus four checkpointed before SIGKILL, embedded only the seven
remaining documents, and produced bitwise-identical raw and active vectors to
an uninterrupted run. The old active tables remained unchanged until successful
publication. This is a one-Mac durability and vector-parity check, rather than a
retrieval-quality or indexing-speed benchmark.

See [the backend API](backend-api.md) for persistence, cancellation and source
validation contracts.
