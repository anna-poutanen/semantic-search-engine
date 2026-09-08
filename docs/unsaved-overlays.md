# Request-only editor text overlays

The backend and loopback `/search` endpoint accept an optional `overlays` list.
Each entry replaces one eligible saved, Git-tracked file for that search only:

```json
{
  "query": "Where is the current buffer implemented?",
  "retrieval": "dense",
  "request_id": "editor-search-42",
  "overlays": [
    {
      "path": "src/component.tsx",
      "text": "const View = () => <span>Current buffer</span>;\n",
      "version": 7,
      "content_hash": "SHA256_OF_THE_FULL_LITERAL_TEXT_ENCODED_AS_UTF8"
    }
  ]
}
```

`content_hash` must actually be 64 lowercase hexadecimal digits matching SHA-256
of the full literal Unicode text encoded as UTF-8. Version is a nonnegative safe
integer (at most `2**53 - 1`); callers should use the editor's document version.
Entry keys are exact. Duplicate paths, invalid Unicode, mismatched hashes, and
unknown entry keys are rejected. Empty lists and `null` use the unchanged saved
search path.

## Bounds and membership

Requests allow at most 16 buffers, 256 KiB of UTF-8 text per buffer, and 1 MiB of
UTF-8 text in total. Each canonical repository-relative POSIX path is limited to
512 UTF-8 bytes. Absolute paths, `..`, redundant `.`/slashes, backslashes, and
control characters are rejected. The file must be present on disk, tracked by
Git, inside the configured source root, supported by the language registry, and
eligible under its default exclusions plus configured `source_exclusions`.
Symlinks must resolve inside the source root. Untracked/new buffers, deleted
files, declaration-only excluded files, and projects outside Git cannot receive
overrides. `git add` can establish tracked membership before commit, following
the existing saved-source membership policy.

The encoded JSON transport ceiling is separately 2 MiB for nonempty overlay
searches. JSON escaping can expand text beyond that ceiling even when the text
itself satisfies all caps. The Python client sends literal UTF-8 JSON with
`ensure_ascii=False`. Ordinary saved-source requests, `/index`, and `/cancel`
retain their 64 KiB body limit. Response transport remains limited to 1 MiB.

Editor text is already decoded Unicode. An overlay's Python coding cookie never
reinterprets that text; for example, a literal `café` identifier and `λ` string
remain intact under a `# coding: latin-1` comment. Saved files retain their
existing file-byte decoder. All eligible non-overlaid saved source and all
overlaid text must parse. An override can repair malformed saved bytes without
saving them; malformed applied text or malformed remaining saved source rejects
the entire request with the existing source-snapshot conflict response.

## Isolation and lifecycle

One registration in the parent's cancellation registry and its existing global
lock cover the whole request, including lock waiting, validation, cache seeding,
document/query embedding, reranking, packing, and disposal. A child request index
uses that existing cancellation check directly; it neither registers the ID
again nor cancels another request on disposal.

Overlay searches never refresh the parent's saved index. They capture actual
saved bytes/membership/ref and validate the merged source, then build a disposable
SQLite index in a private temporary directory. An existing saved database and
live WAL are copied using filesystem reads only. DB/WAL membership, inode,
size/modification/change times, actual source byte hashes, and copied byte hashes
are guarded before and after copying. Copying and hashing check cancellation in
bounded chunks. Recovery, checkpointing, migration, and any new WAL/SHM files
touch only the private directory; originals are never opened through SQLite,
removed, or changed. An absent saved database stays absent.

This is guarded cache seeding, not a cross-process SQLite locking guarantee.
The resident parent's lock excludes its own writes; detected external cache
changes reject the request for retry. Reused vectors still require the existing
rendered-document and model/settings hashes. The request can reuse historical
saved vectors but writes new overlay vectors only into its disposable database.
Repeated unsaved requests may re-embed their new documents. There is no session
cache, persistent overlay table, background overlay watcher, or file write.

The request borrows the resident embedder. A dense request can initialize that
one parent model, and all inference stays under the same serialization lock.
Borrowed-model disposal cannot close parent weights. Lexical-only overlays do
not initialize either model. Request databases are closed and removed on
success, failure, and cancellation; the saved backend's manifest, active source,
cache, retrievers, last saved statistics/error, and files are retained.

The saved source fingerprint is verified before index promotion and again before
result delivery. Saved edits, deletion/rename, membership/ref changes, or parser
policy changes during the request reject it. These checks do not lock editor
buffers or disk files after the final check.

## Provenance and editor freshness

Overlay response scope adds `source_state: "overlay"`,
`saved_source_manifest`, and all input buffer bindings under `overlays`; bindings
contain only path, version, and hash. The snapshot digest also includes every
buffer's version/hash, even when its text matches the saved file.

Each returned row has `source_origin: "saved"` or `"overlay"`. Overlay rows also
have `buffer_version` and `buffer_hash` for the full input text. Existing `origin`
still describes retrieval versus dependency context. `source_hash` still hashes
the returned excerpt and `content_hash` the original source window; neither
should be confused with a full buffer hash.

Origin/version/hash fields and scope bindings are present before compact token
packing and counted by the named tokenizer. Character/source and token budgets
still retain complete physical lines. A token cap too small for query/scope
metadata rejects the request; callers may need a larger cap or fewer buffers.
HTTP diagnostic timing/statistics fields remain outside the compact token count,
as with saved searches. Overlay index timing includes source preflight and cache
copying; total timing also includes lock waiting and disposal.

The Python client can check editor state at delivery:

```python
response = client.search(
    "Find the behavior",
    overlays=buffer_entries,
    current_buffers=lambda: {
        path: {"version": editor_version, "content_hash": current_utf8_hash}
        for path, editor_version, current_utf8_hash in read_current_buffers()
    },
)
```

The callback remains local and is never sent to the service. Missing/closed
buffers, changed versions, or changed hashes raise `StaleBufferError`. The client
also checks that the server bound every requested buffer and that row provenance
matches the request. Call `ensure_overlay_current(response, current_buffers)`
again immediately before preview/navigation, because the editor can change after
delivery. Check every input buffer even if retrieval returned only saved rows.
The server cannot prove that an editor version is still current.

The ordinary CLI and MCP tool continue to search saved source. The isolated VS
Code draft adds explicit request selection through `semanticSearch.find` with
`{query, includeUnsaved: true, options}` or its palette scope choice. It discovers
source root/exclusions from status, captures every eligible dirty tracked file,
and rechecks Git membership, saved full-file bytes, editor identity, version and
full literal UTF-8 hash at delivery and navigation. Saved mode remains the
default. Excluded dirty documents have concise reason counts; caps reject a
whole request. Dirty results navigate to the same open editor and retain their
captured previews when stale. Editor events conservatively invalidate dirty
searches, including some unrelated file changes.

See [the extension guide](../extensions/vscode/README.md) for its source proof
and BOM details. This draft's CPU gates are separate from actual unsaved
Extension Development Host and real-model acceptance. CPU verification uses
temporary Git/source/SQLite fixtures and fake embedders, with no weights or
held-out labels.
