# Independent native guard audit

Both retained first attempts pass the independent CPU audit. The restricted
Development Host observed `workspace.isTrusted=false` before product commands;
its API/connect/search/status guards refused and the owned fixture received zero
product HTTP attempts. The trusted run retained four successful fixture requests
in order: status, status, search, status. Its recorded states show wrong-root
rejection, cleared connection/results after removal of the indexed second
folder, and settled status/search errors after the owned listener stopped.

The auditor recomputes the frozen 51,493-byte VSIX SHA-256 and all 21 archive
members. Both runs retain the same exact 20 installed members before and after,
including the container-bound `.vsixmanifest`; installer manifest metadata is
handled only through retained normalized semantic equality. Six tracked protocol
files match frozen revision `798ad06462d6a6d006d7f0ab69bcc694c4f7b9f7`; the ignored
compiled host entry matches retained before/after hashes and current bytes. Both
toy repositories retain the independently computed original source hash. Owned
process-group, listener and profile cleanup were captured as successful.

This tests native editor guards against an authored HTTP fixture. It starts no
semantic backend or model and establishes no retrieval accuracy, token usage,
inference performance or ordinary-profile Restricted Mode enablement. The
fixture's `128` token field is deliberately a schema placeholder. Per-action
no-extra-HTTP checks and TCP refusal additionally rely on reviewed executed
assertions: the final attempt inventory is retained, but intermediate observer
snapshots are not. Raw installer `__metadata` is withheld, so its raw manifest
hash cannot be reconstructed from the normalized public object.

The approximate declared plan time falls after the native runs. The original
plan remains unchanged, and a separate correction reports earlier filesystem
creation. The frozen commit, corrected creation observation and actual run
timestamps have consistent order; the filesystem observation is not a signed
independent timestamp. This limitation remains explicit in the audit.

Validation: 26 focused CPU corruption tests pass; scoped Ruff passes. The tests
reject trust overrides, unexpected HTTP attempts, changed payload/protocol,
wrong source or bounds, retained stale states and missing cleanup/stop evidence.
No native application, model, backend HTTP request or diagnostic run was started
by the auditor.

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_editor_acceptance.py \
  results/editor-acceptance-2026-10-13 \
  --package-freeze results/editor-package-2026-10-13/frozen-package.json \
  --vsix .cache/releases/semantic-search-local-0.1.0.vsix \
  --output results/editor-acceptance-2026-10-13/independent-audit.json
```
