# Small external beta handoff

The next portfolio step is a few independent testers trying real public code
and reporting relevance and operational failures. Start with **dense retrieval,
reranking off**. Optional Qwen reranking has mixed quality and adds latency;
it is a separate comparison, not the beta's required setup. The existing studies
do not establish a general paid-token benefit. Local compact `cl100k_base` counts
and actual coding-agent usage are different measurements.

## Prepare a local session

Use the [guided Mac download](getting-started.md) on an Apple Silicon Mac. The
small ZIP includes diagnostics, generated MCP snippets and the feedback collector;
testers do not need the research checkout or benchmark datasets. From its extracted
folder, start a checkout you already have:

```sh
bash "Get Started.command" --repo "/absolute/path/to/your/repo" --source-root src
```

Wait for `Ready` and keep that terminal open. The launcher shows indexing phases
and counts, then the source scope and indexed-window count. Own-repository terminal
searches default to 8 results, 12,000 source characters, a 4,096-token response
budget and dependency inclusion; the toy has smaller limits and sample queries.
Startup can wait 30 minutes, with a separate 120-second HTTP request timeout.
`--startup-timeout`, `--request-timeout`, `--max-results`, `--source-budget`,
`--token-budget` and `--no-dependencies` are explicit session options. Record changes
to these settings when comparing sessions; larger delivery caps alone are not
evidence of better retrieval or paid-token savings.

Indexing groups are capped at 8,192 padded tokens with up to four documents;
the per-document input limit remains 8,192 tokens. Updated batching metadata
changes the embedding fingerprint, so older vector caches rebuild once. Model
downloads remain reusable. This policy is an implementation limit, not a measured
latency or memory-performance claim.
Custom calibrated match policies require recalibration for the new embedding
metadata; old calibration artifacts cannot be reused. The default launcher uses
no match policy.

For tracked generated/template files that are not standalone supported source,
repeat `--exclude-source 'src/generated/*' --exclude-source 'src/templates/*'` with
patterns matching your checkout. Patterns are quoted globs relative to the
repository root, including the source-directory prefix. A Django template scope
uses `--exclude-source 'django/views/templates/*'` even with `--source-root django`.
Excluded files cannot contribute evidence. Record exclusions with the session;
changing them changes the searchable scope and index cache. Included invalid
source surfaces an error rather than being silently omitted.

Use the ready session's `mcp.json` or `vscode-mcp.json` snippet for your client, or
connect the included VSIX to the printed local service address. The launcher
changes no client settings. Snippets contain local runtime paths and the chosen
port, so keep them local and use the new snippet after each restart with the
default available-port choice. Choose the same fixed `--port 8750` on successive
starts to retain a stable address, or `--port 0` if it is occupied. Enter `:mcp`
in the search prompt to print the portable snippet. Copy the full server entry,
including `env.TIKTOKEN_CACHE_DIR`, to reuse the warmed tokenizer cache from a GUI
client. The ordinary
CLI/MCP workflow searches saved source; explicit dirty-buffer search is an editor
feature.

For a reproducible Requests source root in the full developer checkout, follow
the [developer setup](../README.md#developer-setup-and-experiments-on-an-apple-silicon-mac)
and [backend guide](backend-api.md). This alternative uses the separate `.venv`
and benchmark fetcher, which are not required for the downloaded own-repository
workflow:

```sh
python3 scripts/setup_mac.py --runtime-only
.venv/bin/python scripts/setup_embeddings.py
.venv/bin/python scripts/fetch_corpus.py \
  --dataset benchmarks/new_requests_queries.json \
  --destination .cache/corpora/requests
.venv/bin/semantic-search serve \
  --repo .cache/corpora/requests --source-root src/requests
```

The [CLI/API examples](backend-api.md), [MCP setup](mcp.md) and
[VS Code guide](../extensions/vscode/README.md) also cover the full checkout.

The updated downloadable package is the
[v0.1.2-preview.1 GitHub release](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.2-preview.1).
Follow the [preview installation guide](release-preview.md). The MIT-licensed
manifest is `0.1.2`; accept the update and reload, or use
CLI installation with `--force`. Baseline dense/off evaluation does not require
optional reranking. [Qwen editor setup](vscode-qwen-reranker.md)

## A short tester session

Use a disposable checkout for edits, and choose three to five behaviors before
searching. Describe behavior in your own words, then independently inspect the
actual implementation. Public project names supported by the collector are
Flask, Requests, Click, Starlette, Rich, Express, Zod, Koa, HTTPX and Werkzeug.
The existing [demo](demo.md) is useful for learning the UI; its obvious authored
functions do not substitute for external relevance feedback.

| Workflow | Try | Record |
|---|---|---|
| Saved search | Search for a behavior, preview returned source, open verified lines. For a multi-file behavior, check every required implementation. | Full implementation, partial evidence, no relevant evidence, or operational error. A score/order is not proof. |
| Explicit dirty search | In VS Code, edit one eligible tracked file without saving, then choose **Include eligible dirty tracked buffers**. | Whether the current buffer's actual source is delivered and navigation uses that document. An ordinary saved search should retain saved-source behavior. |
| Stale navigation | After receiving a result, edit its source again and try **Open Verified Source**. | Whether stale navigation is blocked and the captured preview remains available; search again for a new binding. |
| Saved refresh | Save the edit, use **Refresh Index** or CLI `refresh`, and search again. | Whether current saved code appears; refresh must not save dirty documents. A malformed saved implementation should surface an error, not a stale excerpt. |
| Restart | Stop your own service with Ctrl-C, restart the same repository/source root, then search. | Whether the workflow works after reopening; count errors separately from relevance. Do not label subjective waiting as a benchmark latency measurement. |

For a “full implementation” count, every required executable span must be
present in the actual delivered excerpts. Inspect truncation and line ranges;
knowing that a helper exists elsewhere is not complete delivered evidence.
An absent feature returning candidates is not successful absence detection.
Use partial or leave the task outcome unattempted when uncertain. Changes to
source, query wording, result/token limits or reranking make a different session;
keep its feedback record separate.

The earlier [ordinary installed-profile acceptance](../results/ordinary-vscode-profile-v3-2026-10-14/findings.md)
passed public activation and actual Restricted Mode disablement in two fresh
profiles. It uses a zero-model HTTP fixture and explicitly disables trust for
the trusted phase, so independent users and trust-prompt UX remain valuable
feedback. The existing [Development Host checks](packaged-editor-host.md) and
[editor acceptance limits](editor-acceptance.md) retain their separate scope.

## Record a small local feedback file

Run the offline diagnostic first if setup or indexing failed:

```sh
bash "Get Started.command" --doctor --repo "/absolute/path/to/your/repo" --source-root src
```

It checks machine/disk/Git/runtime/checkpoint and optional eligible-file counts,
without model loading, package installation, service startup or network access.
Without an existing Python 3.12 or newer, the shell prints a basic preflight and
missing-Python guidance rather than downloading anything. The report excludes
repository paths, filenames, source, queries, environment variables and logs.
`--skip-weight-check` explicitly skips physical weight integrity; missing checks
before setup do not imply a tested runtime.
The diagnostic only counts tracked file membership; it does not parse source or
apply indexing exclusions, and does not accept `--exclude-source`. A passing
diagnostic can coexist with an indexing parse error. Inspect the reported parse
cause/service log, then correct the saved source or explicitly exclude the intended
generated/template paths.
The interactive `:doctor` command runs the same local checks. Search errors leave
the prompt open for a source/query correction and retry; record errors alongside
successful searches rather than counting a retry as an uninterrupted workflow.

The standalone collector uses only Python's standard library. It defaults to
offline, sends no feedback, imports no models, opens no repository, and writes
a new local JSON file chosen by the tester. The downloaded launcher routes
`--feedback` to this collector without package or model installation; if it needs
a Python interpreter, it may download the local setup tool and managed Python.
Installed versions missing from the selected interpreter are `null`.

```sh
bash "Get Started.command" --feedback \
  --output /tmp/semantic-search-beta-first-session.json \
  --public-project requests --revision PUBLIC_40_CHARACTER_GIT_COMMIT \
  --interface vscode --workflow saved_search \
  --attempts 4 --full 2 --partial 1 --errors 1 \
  --note partial_implementation --note timeout
```

These counts are illustrative; replace them with your observed outcomes. Supply
the public checkout's complete commit manually; the script does not run
Git or collect a repository path. Omit the project/revision if it is not one of
the allowlisted public projects. `--task-outcome completed|not_completed|not_attempted`
is optional; it is a tester observation, not independently verified correctness.
Use a new output filename for each workflow/session; existing files are refused.

Collected fields are OS/version, architecture, Python and the allowlisted
`semantic-search-engine`, `mcp`, `tiktoken`, `sqlite-vec` package versions;
manually selected interface/workflow/reranker/editor version; optional public
project/revision; full/partial/error counts; task outcome; and fixed issue-note
categories. Arbitrary notes are intentionally not accepted. No repository paths,
queries, source excerpts, hashes of private source, credentials, hostnames,
environment variables or private logs enter the record.

An explicit option may add **one bounded local GET `/status`**, independent of
proxy settings and with redirects refused:

```sh
bash "Get Started.command" --feedback \
  --output /tmp/semantic-search-beta-status.json \
  --interface cli --workflow service_restart \
  --attempts 1 --full 1 --server http://127.0.0.1:8750
```

Use the actual ready session's port in place of `8750`. Only numeric loopback
HTTP addresses are accepted. The service address itself
is not retained. The status allowlist contains boolean `ready`, `closed`,
`embedding_initialized`, `dense_ready`; integer `windows`; watcher booleans
`running`, `pending` and integer `refresh_count`; and, **only if advertised**,
`embedding`/`h2o`/`qwen` provider `enabled`/`ready` booleans. Current backend status
does not advertise reranker availability; missing provider status means unknown.
Paths, error strings, provenance, tokens, process IDs and every other response
field are discarded. Unavailable/invalid/oversized replies retain only a fixed
collection category and optional HTTP status code. Status can wait behind model
work; a collection timeout does not measure search quality or service speed.

Review the JSON locally before any manual sharing. This kit uploads nothing,
contacts no tester, and supplies no messaging integration. Expanded explanations
or screenshots should be separately reviewed rather than inserted into this
minimal collector. Preserve failures and partial outcomes alongside successes;
external authored sessions remain a small sample, not a general quality claim.

In the full developer checkout, the equivalent entry point is
`.venv/bin/python scripts/collect_beta_feedback.py` with the same collector
arguments. Run `bash "Get Started.command" --feedback --help` in the small download
for its allowlisted fields. Feedback and diagnostics are available without the
optional H2O or Qwen reranker.

## Portfolio readiness after feedback

Summarize independent sessions by workflow, version and public revision, retaining
full/partial/error denominators and observed regressions. Verify and publish any
updated VSIX before changing the release handoff. Ordinary-profile evidence and
broader repository/workflow behavior remain separate gates. The preview package
now includes the project MIT license. Upstream source, model weights and
separately installed dependencies retain their licenses. Any future
paid-token or workflow-benefit claim needs actual consumer usage and correctness
evidence, not a smaller local context cap or feedback counts alone.
