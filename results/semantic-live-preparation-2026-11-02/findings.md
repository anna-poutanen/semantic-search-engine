# Live semantic capture preparation

The executing-request provider, canonical vector reader, forward observations
and durable capture component are implemented. **550 affected CPU tests pass**
in 58.23 seconds; Ruff and whitespace checks pass. The development quality gate,
fresh attrs inference, native warm timing and coding comparison have not run.
The user's code-preparation-only constraint remains in force.

## End-to-end invented capture

[The original attempt](request-fixture/manifest.json) uses constructed Python
source and a deliberately invented CPU model, not real Qwen outputs. Its exact
[request](request-fixture/request.json),
[full result/feature/forward record](request-fixture/record.json),
[independent audit](request-fixture/audit.json) and
[readiness summary](request-fixture/readiness.json) are retained.

- The cold index uses three stub forwards for eleven documents.
- The original full query uses one forward and its exact vector is reused.
- Two additional literal clauses use one batched forward; no document inference
  or extra retrieval is performed by the provider itself.
- The capture's five reported forwards match the fixture's five actual calls.
- One actual coverage pass and one semantic allocation deliver eight rows in
  863 compact tokens; independent reconstruction and JSON-roundtrip audit pass.
- All 45 captured product/helper source bindings remain unchanged during the
  request. The CLI also binds the fixture, its own source and verified tokenizer.

Those admissions and timing fields are fixture mechanics, not behavior labels
or native performance results. The attempt is explicitly unqualified: its Git
state is a preparation checkout and its model/control/provenance claims are
false. No model checkpoint was loaded. Selected model-import/socket guards
record zero attempts; they are not a generic filesystem/network sandbox. The
zero real-model, real-codebase, observed-outcome and reserved-corpus activity
claims are based on review of the executed invented paths.

Reproduce with a new path whose parent already exists:

```bash
PYTHONPATH=src python scripts/check_semantic_live_preparation.py \
  --tokenizer-cache .cache/tokenizers \
  --output-dir results/local/semantic-live-new
```

The development dependencies and verified tokenizer must already be installed.
The helper imports an explicitly hashed test fixture and cannot serve as a
downloaded-user or native-model benchmark. CI runs this same check and retains
the attempt, including any failure files.

## Review and corrections

Separate source review covered the provider, vector reader, observer, executing
saved/overlay backend and capture/CLI. No blockers remain after these fixes:

1. A first integration assertion compared operational timings that the backend
   legitimately updates after capture. It now checks the source envelope and
   independent audit while allowing only the existing timing/indexstats boundary.
2. The initial vector reader's ordinary SQLite connection could recreate a
   vanished database before enabling query-only mode. It now uses `mode=ro`;
   missing-cache and special URI-path tests pass. SQLite read coordination side
   files are distinct from logical vector writes or database recreation.
3. A source manifest could initially hash this worktree while an editable install
   executed another checkout. Package-root authority now fails closed before
   search. Bindings are snapshotted so later mutation cannot rewrite the initial
   classification. Source drift after a request also fails capture.
4. The capture now correlates external original-query records with provider
   records, checks cold document-forward completeness, preserves dictionary order
   through exact compact-payload roundtrip, and retains partial/failed receipts.
5. Attempt creation fsyncs its parent directory entry before execution. Parents
   must already exist. Hooks reject work outside the one-request capture window,
   preventing warmup calls from silently entering request counts.

Tests cover saved control parity, non-axis/signed-zero query bytes, canonical
cached floats and metadata drift, snapshot races, cold/warm overlay isolation,
inactive BM25/empty/abstained paths, split batches, cancellation, observer/model/
provider failures, serialization/audit/disk errors and no-overwrite behavior.
All fixtures and indexes are invented and temporary. Earlier observed study
results and the failed `complementary_v2` gate remain untouched.

## Still required

Complete and prospectively freeze the observed capture/replay runner and trusted
source/raw/control/runtime/model bindings before real execution. Reproduce the
786 controls before 393 candidate cases/201 distinct units. A historical saved
pool without an original query vector needs explicitly counted new query-feature
work; this live request's vector-reuse guarantee cannot be transferred to it.
Require zero individual evidence losses against both controls and a new distinct
complete multi-file behavior over coverage. Only then use the reserved attrs
corpus and native warm timing under the unchanged latency gate.

The [implementation notes](../../docs/semantic-live-preparation.md) and
[validation receipt](validation.json) record scope and source bindings. Full
CPU/editor CI is delegated to GitHub after commit. The service/CLI still use
ordinary coverage/backfill; v0.1.5 assets and package version are unchanged.
