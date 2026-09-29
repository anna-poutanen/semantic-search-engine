# Prospective Werkzeug context-allocation holdout

The preferred candidate is **12 context results with dependencies**, chosen before this repository's retrieval outcomes. The basis is the observed development/HTTPX CPU replay and the smaller development median context size at12 versus20. That prior evidence is development evidence; Werkzeug is the prospective repository holdout. The product default remains unchanged.

Freeze and commit this source/label preflight and a passing independent semantic gold review before any holdout indexing, embedding, scoring or search. Source-only extraction in this preflight is not an index or a retrieval experiment. Queries cannot be rewritten after outcomes to obtain a benefit at12.

## Fixed source and labels

Official repository: https://github.com/pallets/werkzeug . Historical annotated tag3.1.3 has object aefbcabecec911dc6fa95791e50be264bf5e8415, peeled commit6389612fd1ee1bd93579eed5026e8fd471d04abd and tree307954153426c43960b35906da0a4d82d8eced81. Official remote refs, tag contents, clean source blobs and local strict Git object integrity were checked; this does not assert verification of the tag's PGP signer.

The frozen source is all52 tracked Python files under src/werkzeug. Eight explicit JS/TS-family globs exclude the tracked debugger JavaScript and any other supported non-Python extension. Tests, docs, assets, external packages and caller applications are outside the corpus. The unchanged saved-implementations-v3 extractor produces1,025 exact160-line/40-overlap windows. Every window has literal physical bounds and a source hash; all file, extractor and inventory fingerprints are retained.

benchmarks/werkzeug_context_queries.json contains exactly15 queries:12 positive and3 scoped absent, with34 required groups and8 cross-file AND cases. All groups have one source-authored alternative; no unverified equivalence/OR paths exist. Evidence is the complete physical executable function/method definition, not a selected line, name or comment. Every span has a literal hash and one or more eligible original extraction windows. Independent review must assess whether each local behavior and callee scope is semantically supported, in addition to syntactic eligibility.

## Runtime conditions

| Condition | Context max_results | Selection role |
| --- | ---: | --- |
| baseline_8 | 8 | Frozen comparator |
| primary_12 | 12 | Preferred candidate selected before holdout results |
| diagnostic_20 | 20 | Reported diagnostic; cannot replace/rescue the primary |

All three use hybrid retrieval, the original full50 candidate pool, original B1 Qwen scoring of its first24 candidates, unchanged score/tie ordering and packer, dependencies enabled,12,000 source characters and4,096 compact cl100k tokens. **8/12/20 changes max_results, not the reranking shortlist.** No score/query/vector-result cache, runtime substitution, alternative source membership or new abstention rule is allowed. Every request embeds its actual query and performs fresh native scoring. Index persistence may reuse verified stored document vectors as declared by the runtime producer.

The intended full schedule is15 queries ×3 conditions ×3 balanced condition-order passes =135 HTTP searches. Each actual successful search should account for24 singleton pairs; startup warmup is separate. The producer/runtime plan must bind all implementation/model/package/input source hashes, source/label review receipts, condition/schedule identities, clocks, actual counters and owned clean lifecycle. No inference is authorized by this source preflight itself; root will explicitly freeze/commit/run the runtime stage serially after its other native qualification phases.

## Primary acceptance and honest reporting

1. Retain **every individual positive query** whose complete required evidence is delivered by baseline8. An equal aggregate completion count cannot conceal a lost baseline implementation.
2. Full required-group coverage and completed cross-file AND counts must each be at least baseline8. Report every complete/partial group gain and loss, full source bounds, clipping and dependency origin.
3. A strict complete-support gain is preferred. Passing retention/floors without one supports only no observed regression, not an improvement claim.
4. Failure of primary12 cannot be rescued by diagnostic20. Do not retune the preferred count or queries using holdout outcomes; preserve all conditions and failures, and keep the current default.
5. Three absent features remain scoped absence labels. Similar-name/related snippets do not prove implementation; returned candidates do not establish calibrated abstention.
6. Reconcile all repetitions and actual source/token/native/clock/cleanup receipts before qualification. Incomplete or interrupted prefixes cannot select a winner or certify latency. Warm timings must be reported with fixed-condition order and measured scopes, rather than mixed with setup or coding latency.

## Required independent gold review interface

Expected new receipt path: results/werkzeug-context-preflight-2026-10-14/independent-gold-review.json . Root's independent reviewer owns it; this author does not create a positive self-certificate. Require checks_passed:true and an explicit independent semantic/absence review classification, plus artifact_sha256 bindings for at least:

- benchmarks/werkzeug_context_queries.json
- results/werkzeug-context-preflight-2026-10-14/source-freeze.json
- results/werkzeug-context-preflight-2026-10-14/window-inventory.json
- results/werkzeug-context-preflight-2026-10-14/eligibility.json

The review should record all15/12/3/34/8 query/group counts, complete-definition eligibility, callee scope, absent-feature reasoning and the source pin. Any corrections must occur before outcomes, preserve earlier source/label versions and be re-reviewed and re-bound. eligibility.json alone is a source-only gate and cannot stand in for this neutral semantic certificate.

Freshness is limited: prior Flask source references Werkzeug as an external dependency, and this source was read to author labels. Fresh means a previously unranked implementation repository/settings, not exclusion from installed dependencies, runtime familiarity or model pretraining. No model, embedding, index, search, scored order or vector outcome was observed during source authoring.
