# Local semantic search — portfolio release

An Apple Silicon prototype for finding local Python, JavaScript and TypeScript
implementations by behavior. Qwen3-Embedding-0.6B stays resident behind a loopback
service; SQLite FTS5 and exact cosine retrieval support BM25, dense and hybrid
search. H2O and Qwen reranking are optional. Dense retrieval with reranking off remains
the fast default; optional Qwen has separate native integration evidence.

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
| [Installed package](../results/editor-package-2026-10-13/findings.md) | Exact installed VSIX payload, seven real Qwen saved/dirty searches, navigation and cancellation | Historical isolated Extension Development Host; newer ordinary-profile evidence is recorded separately |
| [Editor guard acceptance](../results/editor-acceptance-2026-10-13/findings.md) | Actual false trust with zero product HTTP; wrong repository, connected-folder removal and offline errors | Separate Development Hosts with an authored HTTP fixture, no model |
| [Ordinary installed profiles](../results/ordinary-vscode-profile-v3-2026-10-14/findings.md) | Normally installed public-command activation, default search, preview/navigation, Qwen setup guidance, and actual Restricted Mode with zero product HTTP | Fresh ordinary Code 1.141.0 profiles; zero-model HTTP fixture, trusted phase explicitly disables trust |

The demo's public receipt contains verified summaries. Its CPU companion checks
all 18 against saved lines/hashes and records source/package identity after
shutdown. Raw MCP envelopes are not retained there, so their exact tokenizer
count remains an executed check in the hash-bound demo, rather than a new count
reconstructed from public summaries.

## Try it locally

Use a Git checkout on an Apple Silicon Mac. If the project is not already cloned:

```sh
git clone https://github.com/anna-poutanen/semantic-search-engine.git
cd semantic-search-engine
```

Install the runtime and Qwen only if needed:

```sh
python3 scripts/setup_mac.py --runtime-only
.venv/bin/python scripts/setup_embeddings.py
```

Prepare a fresh ignored toy without models, downloads or agent/editor settings:

```sh
python3 scripts/demo_mac.py
```

The script prints shell-quoted foreground service, CLI, native HTTP, MCP and
VS Code steps, including the exact persistent index location. For the editor
walkthrough, keep its printed foreground `serve` command running, open the exact
printed toy folder, and connect to its printed loopback URL.

To run a separate automatic real
HTTP/MCP integration explicitly:

```sh
.venv/bin/python scripts/demo_mac.py --run
```

Automatic mode creates another toy and stops its service when finished. It does
not leave a service running for the editor walkthrough.

See [the demo guide](demo.md) for custom paths, runtime selection and cleanup.
The backend remains a separately started local process; the extension installs
no model or Python environment.

Download the verified VSIX from the
[GitHub release](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.2),
then use **Extensions: Install from VSIX**. Connect it to the separately started
service indexing the same trusted local workspace folder. The release includes
SHA-256 checksums for its assets. The earlier 53,874-byte preview 2 package and
its recorded digest remain historical acceptance evidence.
The current package is version `0.1.2` and includes the MIT license. The small Mac
ZIP also includes a guided launcher; start with [the getting-started guide](getting-started.md).
Version 0.1.2 adds larger configurable own-repository budgets, a separate indexing
timeout, actual indexing progress, offline diagnostics, bundled local feedback,
and ready-to-copy MCP snippets. Document embedding batches now have a padded-token
cap; older vector caches rebuild once. See the
[beta hardening qualification](../results/beta-hardening-2026-10-14/findings.md).

Separate native acceptance checks observed actual false Workspace Trust,
multi-folder selection/removal and offline failure handling. These exercised
product guards in a Development Host with an authored HTTP fixture. The separate ordinary-profile run now observes Production extension mode,
public activation, and actual Restricted Mode disablement. Its four trusted
fixture HTTP requests and zero restricted requests are integration evidence,
with no model inference; trust-prompt UX remains untested.

The [MIT Mac download qualification](../results/mit-mac-preview-2026-10-14/findings.md)
records the exact 213,501-byte source ZIP and 55,345-byte VSIX, actual first
download into empty project-local caches on the existing Mac, final cached
HTTP/MCP checks, interactive toy/Requests sessions, and ordinary-profile
installation with byte-exact license binding. This is not external beta evidence.

## Quality and measurement limits

The separate [106-query Python study](../results/retrieval-quality-2026-10-12/findings.md)
delivered complete evidence for **83/88 positive behaviors overall** and **18/20
Rich holdout positives**, with **175 ms held-out HTTP P95**. Only **6/10 multi-file
behaviors** had complete delivered evidence. These are frozen sample outcomes.

Frozen dense retrieval delivered complete required evidence for **10/12 Express**
development queries and **8/12 Zod** repository-holdout queries; Zod multi-file
coverage was **0/4**. Delivery was capped at eight excerpts, 12,000 source
characters and 4,096 compact tokens. Zod syntax/source eligibility was inspected
before inference; this is a retrieval-settings holdout, not an untouched-source
claim. Absent queries still returned decoys with abstention off. These results
prove neither absence nor coding correctness. [Retrieval findings](../results/language-quality-2026-10-13/findings.md)

The [completed eighteen-attempt coding study](../results/qwen-coding-workflows-2026-10-14/findings.md)
completes 2/6 correctly in each of ordinary tools, fast MCP and Qwen MCP. On the
two matched correct pairs, Qwen versus fast MCP uses 18.52% more gross tokens,
21.30% less uncached input and 0.72% more full trial time. One timeout has unknown
usage; provider failures, supplemental defects and checker limits are preserved.
The 30% token-saving target is unmet. Smaller local context is not provider-token
or monetary savings.

The [real Django profile](../results/django-index-profile-2026-10-14/findings.md)
validates 9,097 windows, all nine persistence stages and warm dense/hybrid backend
P95 of about 0.72 seconds without reranking/HTTP transport. Its 3.89-second CPU
reopen excludes model/service startup. The [Werkzeug allocation study](../results/context-holdout-werkzeug-v2-2026-10-14/findings.md)
fails the primary twelve-excerpt complete-improvement gate; eight stays default.
These finite samples establish no general workflow gain, throughput guarantee
or paid-token benefit. Cancellation is cooperative;
an active Metal forward can finish before it is observed. Source proofs do not
lock files against changes after the final check.

This is a GitHub portfolio release, with no Marketplace publication. Original project code and the current extension are MIT licensed. Separately
installed dependencies and model weights retain their licenses, as described in
[third-party notices](../THIRD_PARTY_NOTICES.md). Historical preview artifacts
retain the metadata with which they were published.
