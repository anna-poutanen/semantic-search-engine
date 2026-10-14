# Try Semantic Search on your Mac

Describe what code does and get the implementation, with its file and line numbers.
The app runs locally and is MIT licensed. This preview supports Apple Silicon
Macs and Git-tracked Python, JavaScript and TypeScript source.

## Download and start

1. Download **semantic-search-mac-0.1.1.zip** from the
   [preview release](https://github.com/anna-poutanen/semantic-search-engine/releases/tag/v0.1.1-preview.1).
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
space. The service refreshes saved changes when you search. Sample queries 1–3
still refer to the toy; type descriptions relevant to your own project.

The terminal prints the service's chosen `http://127.0.0.1:PORT` address. Keep that
terminal open while using another client. Each launcher uses an available port.

## Optional VS Code and MCP

The ZIP includes **Semantic Search.vsix**. In VS Code, open Extensions, choose
**Install from VSIX…** from the menu, and select that file. Open the repository
shown in the terminal, trust the workspace if you intend to search its source,
then run **Semantic Search: Connect / Check Service** and paste the address
printed by the launcher. Run **Semantic Search: Find Implementation** to search.
The extension requires the running local service.

To use an MCP client, configure a stdio server with this executable and argument
list, replacing both placeholders with absolute paths:

```json
{
  "command": "/absolute/path/to/extracted-app/.cache/quickstart/venv/bin/python",
  "args": ["-m", "semantic_search.mcp_server", "--server", "http://127.0.0.1:PORT"]
}
```

The tool is `search_code`. Client configuration varies; the launcher does not
change editor settings or MCP configuration. Local retrieval runs without hosted
inference. Your MCP client may send returned code to its chosen coding model.

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
Interrupted setup can be resumed by running the same command. The setup uses
`.cache/quickstart/` for Python, packages and caches, `.cache/models/` for the
embedding checkpoint, and `.cache/demos/` for fresh demo repositories. It leaves
an existing developer `.venv` alone. Interactive indexes and session logs live
in `.cache/quickstart/`; automatic toy receipts live inside that toy's
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
