# Try Semantic Search on your Mac

Describe what code does and get the implementation, with its file and line numbers.
The app runs locally and is MIT licensed. This preview supports Apple Silicon
Macs and Git-tracked Python, JavaScript and TypeScript source.

## Download and start

1. Download **semantic-search-mac-0.1.2.zip** from the
   [preview release](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.2-preview.1).
   Unzip it into a writable folder. Keep the whole folder together.
2. Open Terminal, type `cd `, drag the extracted folder into Terminal, and press
   Return. Then run:

   ```bash
   bash "Get Started.command"
   ```

3. Wait for **Ready**, then enter **1**, **2** or **3** to search the included toy
   codebase. You can also type your own description. Enter **q** or press Ctrl-C
   to close the service.

No Python, Homebrew, API key, GitHub account or paid model is required. If setup
asks for Git, run `xcode-select --install`, finish Apple's command line tools
installation, then rerun the launcher. A working Git installation is checked
before any downloads. Launching through Terminal also avoids relying on Finder's
handling of a downloaded unsigned `.command` file.

First setup needs internet, at least **6 GB free disk space**, and downloads a
local Python runtime, pinned packages and approximately **1.1 GiB of Qwen
embedding weights**. Download time depends on your connection. The tested Mac is
an M4 MacBook Pro with 24 GB RAM and macOS 15.1.1; other machines have not yet had
external beta testing. H2O and the optional Qwen reranker are excluded from this
setup. Keep the Mac awake while installing.

The default demo creates a fresh tiny Git repository containing three functions.
It is a walkthrough of search and source delivery, rather than a retrieval
benchmark. You do not need a Git commit identity.

## Search your own code

From the extracted app folder:

```bash
bash "Get Started.command" --repo "/absolute/path/to/your/repo" --source-root src
```

Choose the checkout's root and a repository-relative source directory. Use
`--source-root .` when the source is at the checkout root. The app indexes saved,
Git-tracked Python, JavaScript and TypeScript implementations; save and track
files you want searched. Large repositories take longer to index and use more
space. The service refreshes saved changes when you search. The own-repository
prompt asks for descriptions relevant to your project; it does not show or
substitute toy queries.

Some repositories track generated files or templates with JavaScript/TypeScript
extensions whose contents are not valid standalone source. If startup reports a
parse error in such a file, choose a narrower source directory or explicitly
exclude those paths:

```bash
bash "Get Started.command" --repo "/absolute/path/to/your/repo" --source-root src \
  --exclude-source 'src/generated/*' --exclude-source 'src/templates/*'
```

Replace the example patterns with paths from your checkout. Patterns are relative
to the **repository root**, including the source-directory prefix, and must be
quoted so your shell does not expand them. Repeat `--exclude-source` for multiple
patterns. For example, a Django checkout with `--source-root django` uses
`--exclude-source 'django/views/templates/*'`. Excluded files are outside the
searchable scope; changing exclusions selects a separate index cache. The app
reports invalid included source instead of silently skipping it.

Startup prints the current indexing phase and actual file/document/window counts,
then reports the repository, source scope and indexed-window count when ready.
It waits up to **30 minutes** for indexing and embedding initialization, separately
from the **120-second** timeout for each HTTP search. Increase those limits for a
larger repository if needed:

```bash
bash "Get Started.command" --repo "/absolute/path/to/your/repo" --source-root . \
  --startup-timeout 3600 --request-timeout 180
```

Own-repository terminal searches use dense retrieval with reranking off, up to
**8 results**, **12,000 source characters** and a **4,096-token** response budget,
including implementation dependencies. The tiny toy retains its smaller limits.
These are delivery caps: a large implementation can still be truncated or omitted,
and the local token budget does not measure a coding agent's paid token usage.
The prompt marks dependency context and truncated excerpts, and explains when a
response reached its context limit. You can adjust the caps for an interactive
session:

```bash
bash "Get Started.command" --repo "/absolute/path/to/your/repo" --source-root src \
  --max-results 12 --source-budget 20000 --token-budget 6000 --no-dependencies
```

If the index is empty, check the displayed source scope and save and Git-track
supported files within it. An empty index means there are no eligible indexed
implementations; it does not prove that a behavior is absent from the codebase.

The terminal prints the service's chosen `http://127.0.0.1:PORT` address. Keep that
terminal open while using another client. By default, `--port 0` selects an
available port. For an MCP configuration you want to reuse across restarts, choose
the same fixed port each time:

```bash
bash "Get Started.command" --repo "/absolute/path/to/your/repo" --source-root src --port 8750
```

If that port is occupied, the launcher explains how to choose another port or
return to `--port 0`.

## Optional VS Code and MCP

The ZIP includes **Semantic Search.vsix**. In VS Code, open Extensions, choose
**Install from VSIX…** from the menu, and select that file. Open the repository
shown in the terminal, trust the workspace if you intend to search its source,
then run **Semantic Search: Connect / Check Service** and paste the address
printed by the launcher. Run **Semantic Search: Find Implementation** to search.
The extension requires the running local service.

Each ready launcher session writes two configuration snippets into the session
folder printed in Terminal:

- **mcp.json** uses the portable `mcpServers` format.
- **vscode-mcp.json** uses VS Code's `servers` format with `type: "stdio"`.

They already contain the dedicated Python executable, that session's service
address and `TIKTOKEN_CACHE_DIR` pointing at the warmed local tokenizer cache.
Inspect the files, then copy or merge the full server entry, including `env`, into
your client's MCP configuration. For VS Code, merge the `servers` entry into the workspace's
`.vscode/mcp.json` or your user MCP configuration; preserve any existing entries.
See the [VS Code MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
for the current locations and format. Other clients may use different schemas.

The generated server uses this command, argument list and environment:

```json
{
  "command": "/absolute/path/to/extracted-app/.cache/quickstart/venv/bin/python",
  "args": ["-m", "semantic_search.mcp_server", "--server", "http://127.0.0.1:PORT"],
  "env": {
    "TIKTOKEN_CACHE_DIR": "/absolute/path/to/extracted-app/.cache/quickstart/tiktoken"
  }
}
```

The tool is `search_code`. Keep the launcher running while the MCP client searches.
The tokenizer environment lets a client launched outside Terminal reuse the local
cache instead of attempting its own tokenizer download.
With the default available-port choice, use the new session's snippet after
restarting; a fixed `--port` keeps its service address stable. Enter **:mcp** in the
search prompt to print the current portable configuration. Generated snippets
contain local paths and should stay local. The launcher changes no editor settings
or client MCP configuration.
Local retrieval runs without hosted inference. Your MCP client may send returned
code to its chosen coding model. Terminal delivery limits do not change the MCP
tool's own arguments or client defaults.

## Diagnose setup and record feedback

Run the offline diagnostic before or after setup, optionally checking the scope
of your own checkout:

```bash
bash "Get Started.command" --doctor --repo "/absolute/path/to/your/repo" --source-root src
```

It reports machine compatibility, free disk space, Git, installed runtime package
versions, Metal availability, embedding checkpoint integrity, and eligible tracked
file counts when a repository is supplied. It loads no model, installs nothing,
starts no service and makes no network request. It uses an existing local Python
3.12 or newer; if none is available, the launcher prints a basic platform/Git/disk
preflight and explains that the Python diagnostic is unavailable. It does not
bootstrap downloads in diagnostic mode. `--skip-weight-check` skips reading the
full weight file and leaves physical weight integrity unverified. Missing runtime
or model checks are expected before the first setup. The Python report excludes
repository paths, filenames, queries, source, environment variables and logs.
Repository counts describe tracked file membership, not parsed implementations.
The diagnostic does not parse source or accept `--exclude-source`; it can pass
while indexing later reports a source syntax error. Use the indexing error and
local service log to find the offending file and correct or explicitly exclude it.
During a running session, enter **:doctor** in the search prompt for the same local
diagnostic checks. If an HTTP search fails, the prompt stays open so you can fix
the saved source or query and try again; enter **q** to close the service.

The small ZIP also includes the local feedback collector. Record your own counts
after trying a few searches; the following counts are an example, not measured
beta results:

```bash
bash "Get Started.command" --feedback \
  --output /tmp/semantic-search-beta-first-session.json \
  --interface cli --workflow saved_search \
  --attempts 4 --full 2 --partial 1 --errors 1 --note partial_implementation
```

Use a new output filename in an existing directory each time. Count a full
implementation only when every required implementation is in the delivered
source; use partial for incomplete evidence. The collector installs no model or
runtime packages and does not start a service. The launcher can download its local
setup tool and managed Python if no suitable Python is available yet. It writes
an allowlisted JSON file with machine/package versions, selected interface/workflow,
counts and fixed issue categories. It collects no repository path, query, source
or private log, and is offline by default. An explicit
`--server http://127.0.0.1:PORT` opts into one local, bounded `/status` request; the
address is not saved. Run `--feedback --help` for all fields. Review the JSON before
manually sharing it through
[GitHub issues](https://github.com/anna-poutanen/semantic-search-engine/issues).
Nothing is uploaded automatically.

## Check, rerun and remove

For an automatic toy check that runs three HTTP searches and three official MCP
searches, verifies their saved-source excerpts, then shuts down its own service:

```bash
bash "Get Started.command" --check
```

To install without starting a search service:

```bash
bash "Get Started.command" --setup-only
```

Rerunning checks the cached runtime and embedding weights and reuses downloads.
Indexing groups now contain up to four documents and at most 8,192 padded tokens
(the longest document's token length multiplied by the document count). Each
document still has an 8,192-token input limit, separate from the search response
budget. The updated batching metadata changes the embedding fingerprint, so an
older vector cache rebuilds once with this policy; cached model downloads remain
reusable.
Interrupted setup can be resumed by running the same command. The setup uses
`.cache/quickstart/` for Python, packages and caches, `.cache/models/` for the
embedding checkpoint, and `.cache/demos/` for fresh demo repositories. It leaves
an existing developer `.venv` alone. Interactive indexes, session logs and MCP
snippets live in `.cache/quickstart/`; automatic toy receipts live inside that toy's
`.semantic-search/` directory. Nothing is installed globally.

Stop any launcher services before moving or deleting the extracted folder;
installed virtual environments depend on its location. If you move it after
setup, move its `.cache/quickstart/` directory aside and rerun setup. After
closing the app, deleting the extracted folder removes its runtime, weights and
indexes. An installed VS Code extension can be uninstalled through Extensions.

If setup stops, follow its specific error and rerun. A corrupt or unexpected
cached setup tool must be moved aside as directed; never delete a cache while
another launcher is running. Service failures include the path to their local
log. Problems or feedback can be reported through
[GitHub issues](https://github.com/anna-poutanen/semantic-search-engine/issues).

## License and current limits

Original project code is available under the included **LICENSE (MIT)**.
**THIRD_PARTY_NOTICES.md** describes upstream components, model weights and
separately installed dependencies; those retain their own licenses. In
particular, the separately installed `mlx-embeddings` runtime is GPL-3.0-or-later;
the project's MIT license does not change that license.

This is an early Mac preview. Retrieval can miss implementations, especially
multi-file behaviors. Inspect the returned source before using it. There is no
Windows/Linux dense-runtime installer, Marketplace release, or evidence from
external beta users yet. The public repository includes the full benchmark
history, architecture and measured limitations; the small ZIP omits those large
research artifacts.
