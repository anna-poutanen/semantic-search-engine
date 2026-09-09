# Real Qwen request-only overlays

The revised [probe](proof.json) passed all ten executed checks. Full Python,
JavaScript and TypeScript editor buffers use literal UTF-8 text, including a
Python encoding cookie, café/emoji, and JavaScript BOM/CRLF. Five retained HTTP
responses have exact full-buffer/version, source-window and excerpt bindings.
All compact payloads fit their caps; the clipped response retains accurate
physical bounds. A narrow search also binds supplied inputs absent from results.

The saved parent index bytes, modification time, inode and cache membership are
unchanged. Later saved-source retrieval and a new backend reopening the saved
index reuse the original vectors. Overlay document vectors are recomputed for
each request; no persistent editor session cache is claimed. This fixture has
no WAL/SHM sidecars; live-sidecar preservation has separate CPU tests. There is
no cross-process SQLite lock.

The [independent audit](audit.json) checks retained sources and counts without
production helpers. Sorted public JSON does not retain the compact serializer's
field order; the reader reconstructs the reviewed hash-matched runtime order.
The raw compact wire text was not retained. Callback freshness, parent status
and later saved search are executed assertions in the retained probe rather
than independently retained HTTP responses.

The first attempt's counter audit reused a production helper that mutated the
reported count, making reported-count equality tautological. Its source and
result are retained as attempt 01. The revised run directly encodes the compact
payload without changing it and repeated real inference; the independent reader
confirms all five counts. Both attempts remain visible.

These authored fixtures establish source/cache integration, not relevance or
latency improvements. A separate [fresh-environment run](../install-validation-2026-10-13/fresh-model-audit.json)
uses freshly installed pinned dependencies and newly populated Qwen weights.
