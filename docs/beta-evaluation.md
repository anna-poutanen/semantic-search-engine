# Small external beta handoff

The next portfolio step is a few independent testers trying real public code
and reporting relevance and operational failures. Start with **dense retrieval,
reranking off**. Optional Qwen reranking has mixed quality and adds latency;
it is a separate comparison, not the beta's required setup. The existing studies
do not establish a general paid-token benefit. Local compact `cl100k_base` counts
and actual coding-agent usage are different measurements.

## Prepare a local session

For a quick try, use the [guided Mac download](getting-started.md). The collector
recipe below requires an Apple Silicon Mac and the full Git checkout, including
benchmark data and collector scripts omitted from the small ZIP. Follow the
[developer setup](../README.md#developer-setup-and-experiments-on-an-apple-silicon-mac)
to create its separate `.venv`, and the [backend guide](backend-api.md). The embedding setup downloads the pinned model;
the feedback collector below performs no setup or downloads. Start with the
public Requests checkout for a reproducible source root:

```sh
python3 scripts/setup_mac.py --runtime-only
.venv/bin/python scripts/setup_embeddings.py
.venv/bin/python scripts/fetch_corpus.py \
  --dataset benchmarks/new_requests_queries.json \
  --destination .cache/corpora/requests
.venv/bin/semantic-search serve \
  --repo .cache/corpora/requests --source-root src/requests
```

Wait for `Ready` and keep that service terminal open. In another terminal, use
the [CLI/API examples](backend-api.md), [MCP setup](mcp.md), or
[VS Code guide](../extensions/vscode/README.md). Configure an MCP host manually;
this kit changes no agent or editor settings. The ordinary CLI/MCP workflow
searches saved source; explicit dirty-buffer search is an editor feature.

The updated downloadable package is the
[v0.1.1-preview.1 GitHub release](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.1-preview.1).
Follow the [preview installation guide](release-preview.md). Its exact asset was
installed and tested in ordinary Code profiles, including Restricted Mode;
optional Qwen controls are included, while actual model setup stays separate.
The MIT-licensed manifest is `0.1.1`; accept the update and reload, or use
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

The [ordinary installed-profile acceptance](../results/ordinary-vscode-profile-v3-2026-10-14/findings.md)
passes public activation and actual Restricted Mode disablement in two fresh
profiles. It uses a zero-model HTTP fixture and explicitly disables trust for
the trusted phase, so independent users and trust-prompt UX remain valuable
feedback. The existing [Development Host checks](packaged-editor-host.md) and
[editor acceptance limits](editor-acceptance.md) retain their separate scope.

## Record a small local feedback file

The standalone collector uses only Python's standard library. It defaults to
offline, sends no feedback, imports no models, opens no repository, and writes
a new local JSON file chosen by the tester. Run it in the same environment as
the backend if you want installed package versions; missing versions are `null`.

```sh
.venv/bin/python scripts/collect_beta_feedback.py \
  --output /tmp/semantic-search-beta-first-session.json \
  --public-project requests --revision PUBLIC_40_CHARACTER_GIT_COMMIT \
  --interface vscode --workflow saved_search \
  --attempts 4 --full 2 --partial 1 --errors 1 \
  --note partial_implementation --note timeout
```

Supply the public checkout's complete commit manually; the script does not run
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
.venv/bin/python scripts/collect_beta_feedback.py \
  --output /tmp/semantic-search-beta-status.json \
  --interface cli --workflow service_restart \
  --attempts 1 --full 1 --server http://127.0.0.1:8750
```

Only numeric loopback HTTP addresses are accepted. The service address itself
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

## Portfolio readiness after feedback

Summarize independent sessions by workflow, version and public revision, retaining
full/partial/error denominators and observed regressions. Verify and publish any
updated VSIX before changing the release handoff. Ordinary-profile evidence and
broader repository/workflow behavior remain separate gates. The preview package
now includes the project MIT license. Upstream source, model weights and
separately installed dependencies retain their licenses. Any future
paid-token or workflow-benefit claim needs actual consumer usage and correctness
evidence, not a smaller local context cap or feedback counts alone.
