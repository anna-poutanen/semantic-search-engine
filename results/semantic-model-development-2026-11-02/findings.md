# Real Qwen semantic final-context study — November 2, 2026

**The candidate does not qualify.** Qwen semantic support adds one distinct
complete Jinja behavior but loses three required implementation groups, including
two previously complete case occurrences. The released coverage policy remains
unchanged. The reserved attrs corpus remains unqueried.

## Fixed experiment

The user unpaused model testing on November 2. Before inference or candidate
outcomes, commit `cca82913ef67bf41d565f6424b9619fec8270edc` froze and published the
[protocol](../../docs/semantic-model-development-protocol.md), producer and
independent auditor, tests, model/runtime/checkpoint hashes, all prepared inputs,
and the successful 786-control barrier. The final preparation is
[prepared-v3](prepared-v3/frozen-plan.json). All six prepared artifact values are
[unchanged from the initial preparation](transport-parity.json); only recording
format and crash durability changed before this freeze. Initial exact source and
data, and the interrupted intermediate CPU preparation, remain preserved.

The sample has 393 observed case occurrences, 201 distinct dataset/query units,
322 positive and 71 absent occurrences. Repeated modes and historical studies
are not independent trials. All 2,948 historical negative references remain.
The fixed selector consumes the original ranked pool and once-coalesced coverage
response, with at most two exchanges, eight budget trials, eight excerpts,
12,000 source characters and 4,096 compact `cl100k_base` tokens. No labels,
thresholds, ties, ranks, retrieval scores or caps changed after the freeze.

Pinned original BF16 Qwen3-Embedding-0.6B encoded 3,184 distinct document wrappers
and 185 full queries plus 475 literal clauses: **3,844 texts in 1,167 actual native
forward calls**. Documents used 797 forwards; each full query used its own
singleton forward, followed by one batch containing only its clauses. Historical
retrieval query vectors were unavailable, so these are fresh feature encodings,
with zero claimed original-query reuse or old cache hits. Capture took 362.68
seconds on this Mac; CPU replay took 39.26 seconds. Neither is a native warm
search benchmark. There was no new retrieval, reranking, indexing or later
backfill. Replay used no model calls.

## Quality

These are repeated **case and evidence-group occurrences**, not independent
queries. Complete positives use the unchanged original benchmark criteria.

| Selector | Required groups delivered | Complete positive cases | Complete multi-file cases |
| --- | ---: | ---: | ---: |
| Legacy | 411 / 522 | 250 / 322 | 47 |
| Coverage control | 415 / 522 | 253 / 322 | 50 |
| Semantic final-context candidate | 418 / 522 | 253 / 322 | 50 |

Against coverage, six group gains coexist with three group losses; two complete
gains coexist with two complete losses. The two complete gains are dense and
hybrid occurrences of **one** Jinja query,
`jinja_include_parent_and_local_context`. Against legacy, there are eight group
gains and one group loss, with three complete gains and no complete losses.
One of those complete gains is inherited unchanged from coverage and cannot be
credited to semantic selection. Neither comparison loses a single-file first
hit under the existing criterion. The zero-individual-loss gate fails against
both controls; aggregate completeness conceals the regressions.

All three lost required windows were fully present in the ranked pool and the
coverage response, then evicted from unprotected final slots. They were not
missing upstream or lost through source clipping:

| Query, all `hybrid_k24_b1` | Required delivered implementation removed | Replacement and effect |
| --- | --- | --- |
| Starlette exception registration/dispatch | `ExceptionMiddleware.__call__`, `starlette/middleware/exceptions.py:46–62`, raw rank 6 | Raw rank 17 `BaseHTTPMiddleware.__call__.call_next` improves a clause similarity maximum but loses handler attachment to request scope; a complete behavior becomes incomplete. |
| Zod metadata attachment/registry | Core `meta`, `packages/zod/src/v4/core/api.ts:1758–1768`, raw rank 8 | Same-named classic schema `meta` at raw rank 16 replaces the core attachment/merge implementation; a second exchange adds registry lookup. A complete behavior becomes incomplete. |
| HTTPX JSON request encoding/headers | `Request._prepare`, `httpx/_models.py:441–460`, raw rank 6 | Raw rank 7 `Response._prepare` has similar header code with the wrong receiver. Required request preparation disappears against both controls, including an already incomplete query. |

In each loss, every semantic facet maximum stayed at least as high as before.
That surrogate does not prove that complementary implementation roles survive.
The HTTPX result uses only 1,952 tokens, so its token cap was not exhausted;
the eight-result cap still applied. The full
[source/evidence/exchange diagnosis](diagnostics/selector-diagnosis.json) retains
every changed required group and its original control references.

Features were enabled for 329 cases. The candidate changed 86 responses
(21.9% overall, 26.1% of feature-enabled cases), using 99 exchanges: 73 cases
with one and 13 with two. Of the other feature-enabled cases, 234 had no feasible
positive exchange and nine reached the trial limit. The 64 inactive cases keep
exact coverage: 38 have no eligible literal clauses, and 26 intentionally inactive
BM25 cases record an `invalid_features` fallback for absent features. These 26
are not failed model captures. Sixteen absent-case responses changed; that alone
does not establish absence detection quality.

## Independent verification and preserved correction

The [corrected full independent audit](independent-audit-recheck-1.json) passes
all 393 outcomes, 786 controls, 2,948 retained negative references, model/native
forward bindings, exact source/payload/token checks and individual quality
comparisons. It independently confirms **qualification is false**. Correction
commit `af54523f3a583f05fcb6ca8eba92504483350ea6` was pushed before this
recheck; the report binds that separate execution and lists all 26 normalized
diagnostics.

The original full audit stopped on a diagnostic-string comparison. For all 26
intentionally inactive BM25 cases, the producer says `Feature schema fields
differ` while the independent implementation says `Semantic feature schema`.
All 393 response reconstructions are exact; 367 receipts are entirely exact and
the other 26 differ only in this wording. The original
[failure log](audit-attempt-1/audit.log) and independent
[full classification](diagnostics/all-receipt-classification.json) remain intact.

The separately committed, explicitly **post-outcome**
[correction plan](auditor-recheck-plan.json) binds a new auditor wrapper and tests
before the corrected audit executes. It follows the already frozen
`audit_semantic_context.verify_record` diagnostic policy. Only copied, nonempty
`feature_error` strings may normalize, when the request is intentionally inactive,
features are absent and both implementations independently reject them. Every
other receipt field, feature, source, payload, score, token count, call count and
individual quality gate remains strict. Original producer/auditor source and
saved outcomes remain byte-identical. No model inference or producer selector replay was repeated.

The frozen-source push and PR CI runs each passed **4,218 Python tests, 10
skipped, and 179 editor tests**, plus installed CLI, semantic readiness and
captured backend smoke checks. The final correction preflight passes **162
affected CPU tests** and Ruff. The original CI logs and all six artifact ZIPs are
retained with GitHub API digest verification and frozen source checks.
Both correction-source CI runs also pass **4,254 Python tests, 10 skipped, and
179 editor tests**, plus all three CPU smoke steps. Their original logs,
artifact ZIPs and source/provenance checks are retained separately in
[correction CI evidence](correction-ci-evidence.tar.gz) and its
[verified summary](correction-ci-summary.json).

## Evidence and reproduction

- [Model/runtime/checkpoint binding](model-binding.json) and [native capture archive manifest](native-capture-manifest.json).
- [Lossless native capture](native-capture.tar.gz): forward events, exact float32 vectors, batch/query checkpoints and model-lock release.
- [393 saved outcomes](replay/outcomes.jsonl.gz) and [per-condition metrics and gate](replay/summary.json).
- [Frozen CI evidence](frozen-ci-evidence.tar.gz), [manifest](frozen-ci-manifest.json) and [source/step summary](frozen-ci-summary.json).
- [Correction CI manifest](correction-ci-manifest.json) binds its original downloads and verification files.
- [Initial preparation/source archive](initial-control-preparation.tar.gz) and [receipt](initial-control-preparation-receipt.json).

On a new checkout, extract the capture only when its directory is absent:

```sh
cd results/semantic-model-development-2026-11-02
test ! -e capture && tar -xzf native-capture.tar.gz
```

The study scripts require the pinned tokenizer/runtime and explicit immutable
freeze commits. Full original-environment verification also checks the bound
local checkpoint files. The capture archive supplies the exact features for
CPU replay without new inference; its time is not warm retrieval latency.

## Next work

Design and freeze a new exchange guard that distinguishes receiver and
implementation roles, particularly setup/registration/attachment code that
can receive lower similarity scores. Retain these three failures and all prior
negative results, then rerun the complete development matrix with the same
individual-loss gate. Choosing only favorable modes after observing these
results cannot rescue this failed experiment. Fresh attrs/native validation and
another coding-agent comparison remain gated on a qualifying candidate.
