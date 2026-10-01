# Django source preparation, before measurements

This directory contains prospective inputs and source-only preparation evidence for `docs/large-index-profile-protocol.md` and `benchmarks/django_index_profile.json`. No Django index, document/query embedding, native model call, or timing measurement has been executed.

The official Django 5.2 tag peels to `9e7cc2b628fe8fd3895986af9b7fc9525034c1b0`. Its clean isolated checkout contains 883 tracked production Python files, 157,865 physical lines, and 9,097 source windows with 9,097 unique rendered document hashes. The public tag object is retained; its PGP signature is present but was not independently verified.

- `source-preflight.json` and `source-inventory.json` bind complete Python membership and saved file bytes.
- `window-inventory.json.gz` binds every implementation window, exact location, source hash, and rendered document hash.
- `prepare_source.py` performs extraction with model imports forbidden and no database construction.
- `author_profile.py` authors eight literal/behavioral examples and exact copy-only mutation expectations without any retrieval outcomes.
- `verify_preparation.py` imports no producer code and independently checks membership, source/window hashes, query labels, and all nine expected persistence stages.
- `preparation-verification.json` retains its passing source-only receipt and input hashes.
- `target-sources/` and `mutation-sources/` contain the small pinned source excerpts/modules needed to review labels and the behavior-preserving edit. `DJANGO_LICENSE.txt` preserves Django's BSD license.
- `frozen-producer/` retains the existing implementation sources used to prepare this protocol. These sources were read and archived; none was edited.
- `freeze.json` binds this preparation packet and records the requirements for a separately reviewed execution runner/checker.

The saved edit creates one new document hash. Deletion creates none and removes thirteen active IDs. A tracked rename creates thirteen new documents because the rendered document includes its path. Restoring older saved versions reuses historical cached vectors, ending with 9,097 active windows and 9,111 cached documents. These are source-derived expectations, not measured outcomes.

Commit these inputs and a new execution runner, independent checker, and meaningful CPU-only fake-model tests before executing native indexing. Every stage must refuse source/runtime drift and preserve failures. The original Django clone must remain clean; saved edit/delete/rename checks belong only to a separate execution clone. The current coding comparison must finish before any native profile work starts.
