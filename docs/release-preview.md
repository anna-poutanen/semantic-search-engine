# Local semantic search — portfolio preview

An Apple Silicon prototype for finding local Python, JavaScript and TypeScript
implementations by behavior. Qwen3-Embedding-0.6B stays resident behind a loopback
service; SQLite FTS5 and exact cosine retrieval support BM25, dense and hybrid
search. H2O reranking is optional and was off in the demonstrations below.

The current product includes saved tracked-source indexing, source-root and
exclusion controls, durable vector reuse, saved-file watching, bounded exact
excerpts, conservative static dependency context, CLI/HTTP access and an official
MCP stdio adapter. The VS Code extension adds captured plaintext previews and
verified navigation. Explicit dirty-buffer searches bind every supplied input
to its version/full UTF-8 hash, use a disposable request index and preserve the
saved source/index. Default searches continue to use saved files.

## What has been demonstrated

| Evidence | Established behavior | Scope |
|---|---|---|
| [Real demo](../results/demo-2026-10-13/findings.md) | Three HTTP and three official MCP searches, 18 exact source summaries, compact caps, clean owned-service shutdown | Three visible authored functions; integration, not relevance or performance |
| [Actual editor runs](../results/editor-workflows-2026-10-13/findings.md) | Saved/dirty Python–JS–TS search, previews/navigation, Unicode/BOM/CRLF, stale-input rejection, cancellation/replacement, watcher updates, rename/delete and malformed saved-source errors | VS Code 1.141.0 Development Host and a small authored fixture |
| [Overlay/cache proof](../results/live-overlays-2026-10-13/findings.md) | Literal editor text and unchanged saved parent index; later saved/reopened vector reuse | Authored real-Qwen probe plus separate CPU live-WAL tests; no atomic cross-process SQLite lock |
| [Installer validation](installer-validation.md) | Pinned Mac dependency setup and separate fresh-environment checks | One existing Apple Silicon Mac; cached setup timing is not cold-install/download timing |

The demo's public receipt contains verified summaries. Its CPU companion checks
all 18 against saved lines/hashes and records source/package identity after
shutdown. Raw MCP envelopes are not retained there, so their exact tokenizer
count remains an executed check in the hash-bound demo, rather than a new count
reconstructed from public summaries.

## Try it locally

From the project on an Apple Silicon Mac, install the runtime and Qwen only if
needed:

```sh
python3 scripts/setup_mac.py --runtime-only
.venv/bin/python scripts/setup_embeddings.py
```

Prepare a fresh ignored toy without models, downloads or agent/editor settings:

```sh
python3 scripts/demo_mac.py
```

The script prints shell-quoted foreground service, CLI, native HTTP, MCP and
VS Code steps, including the exact persistent index location. To run the real
HTTP/MCP integration explicitly:

```sh
.venv/bin/python scripts/demo_mac.py --run
```

See [the demo guide](demo.md) for custom paths, runtime selection and cleanup.
The backend remains a separately started local process; the extension installs
no model or Python environment.

**GitHub preview release / VSIX download: PREVIEW_RELEASE_LINK_PENDING.**
The planned preview will provide the VSIX for manual **Extensions: Install from
VSIX** installation. Connect it to the service indexing the same trusted local
workspace folder. Actual Restricted Mode, multi-root and disconnected/offline
service acceptance still remain separate gates. CPU coverage and the successful
single-workspace Development Host runs do not complete those gates.

## Quality and measurement limits

Frozen dense retrieval delivered complete required evidence for **10/12 Express**
development queries and **8/12 Zod** repository-holdout queries; Zod multi-file
coverage was **0/4**. Delivery was capped at eight excerpts, 12,000 source
characters and 4,096 compact tokens. Zod syntax/source eligibility was inspected
before inference; this is a retrieval-settings holdout, not an untouched-source
claim. Absent queries still returned decoys with abstention off. These results
prove neither absence nor coding correctness. [Retrieval findings](../results/language-quality-2026-10-13/findings.md)

The 16-attempt coding comparison yielded only **three qualified pairs** with
normal completion and passes on both frozen and restored-original gates. On
that outcome-selected subset, MCP used **9.23% more provider-reported input plus
output** and **18.62% more uncached input**. Six usage receipts are unavailable;
full-workload totals remain unknown. Supplemental probes found defects even in
some gate-passing outputs. The **30% token-saving target was not established**;
shorter local output is not provider-token or monetary savings. [Coding findings](../results/multifile-workflows-2026-10-13/findings.md)

The studies and authored demos do not establish large-repository model latency,
general workflow gains, or paid-token benefits. Cancellation is cooperative;
an active Metal forward can finish before it is observed. Source proofs do not
lock files against changes after the final check.

This is a GitHub portfolio preview, with no Marketplace publication. The
extension package remains `UNLICENSED`; no distribution license grant is implied
by the planned preview artifact.
