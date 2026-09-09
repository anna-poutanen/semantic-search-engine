# Fresh Mac installation validation

This is an isolated installer draft. Mocked command tests have run; a fresh Mac
installation, fresh checkpoint download, and real-model smoke test remain
pending. These are separate gates. Existing project environments and cached
model tests do not establish that a new user can install successfully.

The intended fast path installs the pinned Python runtime, MCP adapter, and
saved Python/JavaScript/TypeScript parsers. It downloads the tokenizer cache and
checks that MLX reports Metal availability. It performs no model inference and
downloads neither Qwen nor H2O weights. Qwen weights are a separate explicit
step; H2O reranking stays optional.

```bash
python3 scripts/setup_mac.py --runtime-only
.venv/bin/python scripts/setup_embeddings.py
.venv/bin/semantic-search serve --repo /path/to/project --source-root src
```

Run the service in the foreground. MCP connects to that existing service using
the configuration in [MCP setup](mcp.md); installation does not register tools
in a user's agent settings. Saved JS/TS requires strict UTF-8 source, while
Python retains its encoding-cookie rules. See
[supported source languages](saved-source-languages.md).

## Pinning and prerequisites

The model runtime requires macOS on Apple Silicon and Python 3.12. The installer
uses `uv==0.13.0` to create a missing Python 3.12 environment. This can download
Python; a working initial `python3` with pip and network access are still
required. An existing environment is checked for Python 3.12 before package
installation. Git is required for tracked-source discovery and optional H2O
source checkout; setup does not install Git or system developer tools.

Package versions come from `requirements-mac.lock` and
`requirements-languages.lock`. The `mcp` and `languages` extras use the same
direct pins. Qwen remains the original BF16
`Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, about
1.1 GiB of weights. Optional H2O is
`netprojects/h2o-lightning-4b-mlx-q8@a1048d02d9e713ba9f7481efa5c3df7ec8a2adaa`,
about 4.8 GiB, with runtime commit
`0fc8e0299608867746682a0552c906b03a284a47`.

Runtime-only setup still installs the broad pinned dependencies required by
the selected MLX embeddings runtime. In particular, `mlx-embeddings==0.1.0`
requires `mlx-vlm`, and the pinned `mlx-vlm==0.7.6` declares audio, vision, and
HTTP packages. Removing these without validating the installed runtime would
change its dependency contract. Package archives, Python, tokenizer data, and
download caches add disk usage beyond the weight sizes above. No install time
or total disk requirement has yet been measured for a clean machine.

The requirements files pin versions rather than wheel hashes. The editable
project build uses `setuptools>=75`, not an exact build-backend pin, and the
Python patch version is selected within 3.12. An already cached bootstrap uv is
reused. This is not a fully hash-locked supply-chain or bit-identical build. The
post-install inventory gate checks every declared runtime/parser version and
the bootstrap uv version so resolver changes are visible.

## Dependency-only validation

Use a fresh checkout for the strongest isolation: it has no `.venv`, bootstrap
tool, tokenizer cache, or model directories. Alternatively, a clean alternate
environment in the current checkout tests installed packages without replacing
the main `.venv`; shared bootstrap/tokenizer caches then need to be recorded.
The `--venv-path` option applies to setup and the Qwen download helper. Relative
paths resolve against the checkout; absolute destinations are supported. The
project directory, its ancestors, and existing regular files are rejected.
The destination is not deleted or cleared automatically.

These commands are a validation recipe and have **not** been executed for this
draft. Choose an unused destination; do not reuse a developer environment.

```bash
validation_venv="$PWD/.cache/install-validation/.venv"
test ! -e "$validation_venv"
python3 scripts/setup_mac.py --runtime-only --venv-path "$validation_venv"

# Inspect exact versions and dependency compatibility before adding test tools.
.cache/bootstrap/bin/uv pip check --python "$validation_venv/bin/python"
.cache/bootstrap/bin/uv pip freeze --python "$validation_venv/bin/python" \
  > .cache/install-validation/installed-runtime.txt

# This adds only the declared test tool. Re-check runtime versions afterward.
.cache/bootstrap/bin/uv pip install --python "$validation_venv/bin/python" \
  -e '.[mcp,languages,test]'
SEMANTIC_INSTALLER_VERIFY_MAC=1 \
SEMANTIC_INSTALLATION_VENV="$validation_venv" \
  "$validation_venv/bin/python" -m pytest -q \
  tests/test_setup_mac.py tests/test_installation_contract.py \
  tests/test_corpus.py tests/test_languages.py tests/test_language_review.py \
  tests/test_backend.py tests/test_server.py tests/test_client.py tests/test_mcp_server.py
```

The ordinary tests use deterministic CPU embeddings, real temporary Git/SQLite
repositories, a loopback service, and the official MCP subprocess client. They
do not load Qwen/H2O or run Metal forwards. The opt-in inventory test rejects a
different interpreter destination and compares all installed lock versions.
JS/TS parser tests must execute rather than be silently skipped. Capture the
pytest summary, uv `pip check` result, exact installed distribution inventory,
OS/architecture/Python version, checkout commit, both lock hashes, setup-script
hashes, elapsed setup time, and before/after cache membership and byte sizes.
Record failures as failures; do not interpret import success as model quality.

The existing project `.venv` must retain its before/after package inventory.
No checkpoint directory should be created by runtime-only setup. In a reused
checkout, compare existing checkpoint bytes/metadata instead of requiring
absence. Bootstrap, editable package metadata, and tokenizer-cache files are
expected setup writes within that checkout. Network access during installation
is required and is not restricted to loopback.

## Later real-model gates

Model download and inference checks follow dependency validation as separate
steps. They remain pending while the active language evaluation runs. Use the
chosen Python environment throughout:

```bash
"$validation_venv/bin/python" scripts/setup_embeddings.py \
  --venv-path "$validation_venv"
"$validation_venv/bin/semantic-search" serve \
  --repo /path/to/a/fresh/toy-git-project --source-root src --port 8750
```

Run the frozen source-neutral Python/JS/TS fixture through real dense retrieval,
HTTP, and MCP; check returned text against saved bytes, physical line ranges,
and hashes. Edit, rename, delete, and restart to check watcher and durable reuse.
Measure model download/setup/indexing/search separately. The saved editor host
smoke test is another explicit step in
[Development Host testing](../extensions/vscode/docs/development-host.md).

For optional H2O, run `python3 scripts/setup_mac.py --venv-path "$validation_venv"`
without `--runtime-only`, then start `scripts/serve_mac.py` with that same
environment's Python. That launcher uses `sys.executable` for both child
processes. H2O requires additional weights and a separate resident service;
successful fast setup is not a claim that H2O or a local forward has been tested.
