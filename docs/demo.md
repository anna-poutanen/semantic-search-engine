# A small local implementation-search demo

This demo creates a toy Git repository with one obvious behavior in each saved
language: Python normalizes labels, JavaScript removes duplicate values, and
TypeScript clamps a number to a range. It is a portfolio walkthrough and an
integration check, not a retrieval benchmark or evidence of general search
accuracy. The queries and implementations are deliberately visible together.

## Prepare without models

Use a Git checkout, rather than GitHub's Source ZIP, for the default ignored
destination. If needed, clone the project first:

```sh
git clone https://github.com/anna-poutanen/semantic-search-engine.git
cd semantic-search-engine
```

From that project directory, run:

```sh
python3 scripts/demo_mac.py
```

Preparation needs Python and Git. It imports no model/runtime packages, downloads
nothing, starts no service and changes no editor or agent configuration. It
creates a fresh `.cache/demos/toy-<timestamp>-<id>` directory, ignored by this
project, writes the three source files and stages them with Git. A commit and Git
user identity are unnecessary for tracked-source indexing. Its own `.gitignore`
excludes the `.semantic-search/` index, log and receipt directory.

For an easy-to-read path, choose a fresh destination:

```sh
python3 scripts/demo_mac.py --destination '.cache/demos/My Portfolio Demo'
```

Existing directories, files and symlinks are refused. An in-project destination
must already be ignored by the outer repository, including destinations reached
through parent-directory symlinks. Preparation never resets an
existing checkout. If Git fails after directory creation, inspect the new
partial fixture and use a different fresh destination for a retry.

The script prints complete shell-quoted commands with absolute paths, so paths
containing spaces remain copyable. `--port 8751` changes the port printed in the
manual recipe. `--python '/absolute/custom-env/bin/python'` selects an already
installed project runtime for those commands; it does not install an environment.

## Install the runtime only if missing

For real dense search, use an Apple Silicon Mac and the project's pinned Python
3.12 runtime and original Qwen embedding checkpoint. From the project directory:

```sh
python3 scripts/setup_mac.py --runtime-only
.venv/bin/python scripts/setup_embeddings.py
```

These are explicit manual setup/download commands, separate from preparation.
The runtime-only installer includes the MCP and JS/TS parser extras. The
embedding setup downloads the pinned original Qwen3-Embedding-0.6B checkpoint.
H2O is optional and is not used by this demo. Do not start its server for these
steps. Use the printed `--venv-path` commands when selecting a custom project
virtual environment.

## Manual CLI, HTTP, MCP and editor walkthrough

Use the exact commands printed by preparation:

1. In terminal 1, start the foreground `semantic_search.cli serve` command with
   the toy repository, `--source-root src`, and its explicit index-cache path.
   Wait for `Ready: http://127.0.0.1:<port>`. This indexes and warms Qwen once.
2. In terminal 2, run the printed status and three CLI searches. Searches use
   `--server`, dense retrieval and no reranking, reusing that same resident model.
   Inspect the returned path, symbol, inclusive line bounds and captured source.
   Returned rows are candidates; their order is not an accuracy measurement.
3. Run the printed native `curl` request to exercise `/search`. The recipe bypasses
   proxy environment variables and keeps the same 4,000-source-character and
   4,096-compact-token caps. HTTP diagnostic fields are outside the compact cap.
4. The printed MCP command is a **stdio adapter launch command** for a compatible
   MCP client. Configure it manually in a client you choose; the demo changes no
   agent settings. Call `search_code` with the printed JSON arguments. Running the
   adapter alone in a terminal waits for MCP protocol input; ordinary prose is
   not a tool call. The automatic mode below makes actual official SDK calls.
5. With the reviewed VS Code extension installed, open the toy repository as a
   trusted local folder. Run **Connect / Check Service**, select that folder and
   enter the printed loopback URL. Run **Find Implementation**, choose a query
   above and saved source, then preview an excerpt and **Open Verified Source**.
6. Edit a tracked toy function without saving. Run another search and explicitly
   choose **Include eligible dirty tracked buffers**. A dirty row uses request-only
   editor text and navigates to that same open document. Another edit expires the
   old navigation proof; its captured preview remains available. Search again,
   or save and run a saved search. New/untracked, deleted, unsupported or excluded
   files are outside the dirty-buffer scope. **Refresh Index** refreshes saved
   source only; it never saves documents.

The printed SQLite filename is stable for that toy path and `source_root=src`.
The cache contains source as well as embeddings. Stop the foreground service
with Ctrl-C when finished; the separate fixture and persistent index remain.
Editor walkthrough steps are manual; the demo never launches VS Code or an
Extension Development Host.

## Optional automatic real-model integration

This explicitly starts model inference. Run it separately from experiments:

```sh
.venv/bin/python scripts/demo_mac.py --run
```

Automatic mode creates another fresh fixture, starts one owned foreground CLI
service on an available loopback port with watching disabled, sends the three
HTTP searches, then uses the official pinned MCP client and stdio adapter for
three searches against that same service. It checks scope, request echoes,
source hashes and exact saved physical lines. MCP text and structured envelopes
must agree, and the actual `cl100k_base` count must match the reported cap.
It checks no expected rank or winner.

The runtime parent calls the official SDK directly. An AnyIO scope bounds MCP
work; SIGINT/SIGTERM cancel that scope so the SDK's shielded, bounded adapter
process-tree cleanup can finish. There is no extra worker PID whose descendants
would outlive a timeout. The owned service starts in its own process group and
is terminated and waited for in `finally`, including startup, request and MCP
errors. An unresponsive owned group is force-stopped after the bounded grace
period and the run fails. The script never stops a service it did
not launch. Successful shutdown produces `.semantic-search/demo.json`; startup
and backend diagnostics are in `.semantic-search/service.log`. The receipt lists
HTTP/MCP source ranges and hashes, index location, protocol version and shutdown
status. It contains toy output and local absolute paths. There is no publication
step.

`--timeout 120` bounds readiness and each request (the MCP scope allows four times
that duration for three requests and connection setup). Invoke automatic mode
using an installed runtime with pinned `mcp==2.3.0`. Missing runtime Python is
reported before creating a fixture in automatic mode. Missing packages,
checkpoint provenance or an MCP extra require the printed manual setup steps;
inspect the service log or SDK error detail. A default-port conflict
only affects the manual command: print another `--port`, or use automatic mode's
available port. Preparation, CPU lifecycle tests and a real-model receipt are
separate evidence; VS Code acceptance is another gate.
