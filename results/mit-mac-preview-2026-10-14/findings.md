# MIT-licensed Mac try-it preview

Version 0.1.1 adds an MIT grant for original project code and the VS Code package,
a small source ZIP, and a guided Apple Silicon launcher that needs no existing
Python or Homebrew. The default uses Qwen embeddings with reranking off.
Separately installed runtimes, weights and upstream excerpts keep their licenses.

## What shipped

- `semantic-search-mac-0.1.1.zip`: **213,501 bytes**, 59 files including source,
  executable launcher, pinned runtime/parser locks, guide, third-party notices,
  payload manifest and the exact tested VSIX. No weights, environments, Git
  history or benchmark datasets are included.
- `semantic-search-local-0.1.1.vsix`: **55,345 bytes**, with byte-exact project MIT
  license in the declared VSIX license asset and extension notices.
- [Checksums](SHA256SUMS.txt) bind both assets. Historical preview artifacts keep
  their original version and licensing metadata.

## Actual Mac checks

The initial source ZIP was extracted into a new non-Git directory whose path
contains spaces. Its own Python, package and model caches started empty. The
launcher downloaded the pinned uv 0.13.0 archive, checked its SHA-256, installed
local managed Python 3.12.15 and pinned packages, and fetched the pinned original
Qwen3-Embedding-0.6B checkpoint without credentials. Every declared runtime pin,
Metal availability, environment prefix, model revision/configuration and full
weight SHA-256 passed. This ran on the existing M4/24 GB/macOS 15.1.1 Mac with Git
already installed; it is not clean-machine or external-beta evidence.

[Fresh setup output](fresh-setup.log) and [runtime inventory](fresh-runtime.json)
retain the first setup. It used roughly 2.4 GiB for the final caches on this Mac;
this observation is not a general disk requirement or download-time guarantee.
The first [HTTP/MCP toy receipt](fresh-demo.json) passed three HTTP and three
actual official MCP stdio searches, checking saved source, hashes and budgets.
Its owned service stopped with exit code zero and no forced kill.

Later edits made the check output concise and normalized repository source paths.
The exact final ZIP was applied to the owned extraction, reusing its verified
runtime/model caches. The [final demo](final-demo.json) and
[output](final-source-demo.log) again passed all six searches and clean shutdown.
These rechecks are cached runs. Earlier candidate evidence is retained separately.

Two real terminal sessions exercised the interactive interface: a numbered toy
query returned `normalize_label`, and an existing Requests checkout searched with
`--source-root src/requests/` returned both `get_cookie_header` and
`PreparedRequest.prepare_cookies`. The existing checkout stayed clean. Each
session recorded one search and exit-zero/no-forced-kill shutdown. Transcripts
and session receipts are retained here. These are smoke checks, not retrieval
quality or latency benchmarks.

The [final ordinary-profile receipt](final-ordinary-run/receipt.json) passed exact
packaging/source/license binding, normal installation, public-command activation,
preview/source navigation, Qwen setup guidance and actual Restricted Mode.
All owned processes/profiles/listeners were closed. This uses the existing pinned
Code 1.141.0 host and a zero-model authored HTTP fixture; real Qwen terminal checks
are separate. The first ordinary run is also retained before a README correction.

## CPU qualification

The full earlier core run passed 2,277 tests with nine explicit skips. After the
setup refinements, [focused setup regression](setup-regression.log) passed 126
with one explicit Mac-inventory skip; [source-scope regression](source-scope-regression.log)
passed all 55 quickstart tests. Extension tests passed 116; ordinary-profile and
shared-storage Node regression passed 61. The CPU test suite covers bootstrap,
checksum/platform/Git guards, malformed cache recovery, scoped paths, concurrency,
source-root aliases, noninteractive usage, and owned interruption cleanup.

## Limits and licensing

Apple Silicon remains the supported dense-runtime setup. First setup still needs
internet, working Git, and space for broad MLX dependencies and weights. Windows,
Linux dense setup, Marketplace distribution and external beta outcomes remain.
Original code is MIT; `mlx-embeddings` is a separately installed GPL-3.0-or-later
component. Model weights and other dependencies retain their documented terms.
[Upstream notice](NOTICE.md) covers the Requests transcript excerpts.

The [published release](published-release.json) records all three uploaded assets;
GitHub's digest and size for each match the tested local bytes. The release tag
points to `d6f4772375a1522916a62dd7a86607356d97d5c3`, whose [GitHub CI](github-ci.json)
passed both core and editor jobs. The owned temporary extraction and its caches
were removed after closed-listener checks; [cleanup](cleanup.json) retains that
outcome. Tested release assets remain locally under ignored `.cache/releases/`.
