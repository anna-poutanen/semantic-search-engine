# Source inspection before embedding setup

This change addresses the actual Django startup problems preserved in the beta
hardening study: one invalid tracked JavaScript template and three minified vendor
files that produce very large rendered windows. The source checkout now offers
`semantic-search inspect` and `Get Started.command --preflight --repo …`, and normal
own-repository launcher sessions inspect source before embedding model setup.
Invalid included source blocks that setup; valid minified or oversized source
produces warnings and explicit file exclusion suggestions. Nothing is silently
excluded. No retrieval defaults, extraction policies or embedding settings change.

The standalone CLI uses the production saved-source membership and 160/40-line
extraction policy. Its report contains counts, paths, line numbers, hashes and
fixed error categories, without source text or exception messages. It compares a
second capture of saved bytes, membership and Git refs before declaring readiness.
Detail lists are bounded independently; blocked reports mark extraction totals
as partial. Ready means a stable parsed source snapshot, not model availability,
token length, embedding latency or retrieval quality.

## Real public source check

The [reproducible driver](verify.py) exercised three actual CLI child processes on
the existing clean Django checkout at
`9e7cc2b628fe8fd3895986af9b7fc9525034c1b0`. All three ran under Python import and
audit guards that reject model/tokenizer/database imports, network operations,
filesystem mutations and non-Git child processes. Git subprocesses are allowed
and are not themselves instrumented. All assertions passed; the checkout remained
clean. The [receipt](run/verification.json) records implementation hashes, parser
versions, scope choices and one-run wall times. Report `repo` fields alone were
redacted to omit the local checkout location. The Django license is retained.

| Scope | Included / parsed files | Exit / status | Windows / distinct documents | >32,000 rendered characters |
| --- | ---: | --- | ---: | ---: |
| Full `django` directory | 970 / 969 | 1 / blocked | 11,617 / 11,601, partial | 853 |
| Explicitly exclude the invalid template | 969 / 969 | 0 / ready | 11,617 / 11,601 | 853 |
| Explicitly exclude template + three minified files | 966 / 966 | 0 / ready | 10,734 / 10,734 | 0 |

The first report identifies `django/views/templates/i18n_catalog.js` at line 1.
The other warnings identify the vendored jQuery, Select2 and XRegExp minified files.
Four explicit literal-file suggestions from that report produce the final scope.
The 32,000-character threshold is a source-shape heuristic, not a token count,
model input limit or performance estimate. Repeated rendered documents explain
the difference between window and distinct-document counts.

## Package and regression checks

A local **test-only** ZIP was built with the existing tested VSIX. Its application
files and bundled guide match the source checkout. From an extracted directory
with spaces, its actual CLI passed a ready-source check (exit 0) and invalid-source
check (exit 1), under the same guards. The [package receipt](run/packaging.json)
records bytes, hashes and limits. This used the existing developer Python/parser
environment; it does not prove installation on a new Mac. The candidate retains
the source version 0.1.2 solely to test the current builder; it was not published
and does not replace the immutable v0.1.2 release assets.

Focused tests cover real Python/JavaScript/TypeScript extraction, source-free
bounded errors, exact exclusion escaping, physical source lines, changed bytes,
membership and refs, unreadable source, empty-scope guidance, CLI exit codes and
launcher ordering. The standalone CLI guards exclude model, tokenizer, database,
network and write activity. The launcher's dedicated mode can first install its
managed runtime/packages and warm its tokenizer cache; it never calls embedding
model setup or starts a service. `--doctor` retains its separate no-source offline
contract.

The first full-suite run passed 2,503 checks and skipped 15, but failed one historical
CPU replay check because the isolated checkout lacked its expected local offline
tokenizer cache. That check passed alone with the preverified existing cache.
The same verified cache was then copied into this checkout, without downloading,
before repeating the full suite: **2,509 passed, 10 skipped, zero failures** in
65.17 seconds. The [regression receipt](run/regression.json) records both attempts
and retains the final test output. Independent review reproduced all three Django
reports exactly and verified all five recorded implementation hashes.

## Next validation

Publish a new download after qualifying its exact installation and launcher flow;
the current public v0.1.2 ZIP does not yet contain this feature. The most valuable
external check remains a first installation and own-repository workflow on another
Apple Silicon Mac. Use the existing local feedback collector to capture operational
errors and complete/partial implementation results. No external testers, clean
machine installation, model inference, warm search timings or retrieval quality
improvements are claimed by this source-inspection study. Resuming partially
computed embedding batches remains separate future index work.
