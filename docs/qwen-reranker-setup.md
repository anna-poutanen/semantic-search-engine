# Set up the optional Qwen reranker

The main embedding environment and the optional reranker use separate Python
environments. The reranker installer reproduces the validated Python 3.12.15,
34-package closure, original BF16 checkpoint and inspected MLX-LM native source
bytes. It creates no MCP registration and performs no model inference.

Start from the checkout with a working Python 3.9 or newer `python3` and pip. Python 3.12 does not
need to be preinstalled: the explicit install uses pinned `uv==0.13.0` to obtain
managed Python 3.12.15. Package archives and managed Python may download from
their package/distribution hosts; the four checkpoint files download from the
public Hugging Face repository at its fixed commit. A supported inference host
is an Apple Silicon Mac.

```sh
# Local metadata only: no network, environment writes, MLX import or forward.
python3 scripts/setup_qwen_reranker.py

# Explicit setup: isolated packages/Python plus pinned checkpoint downloads.
python3 scripts/setup_qwen_reranker.py --install

# Read and SHA-256 verify the existing 1.19 GB weight file without loading it.
python3 scripts/setup_qwen_reranker.py --verify-weights
```

`--dry-run` is the same read-only operation as the default command. It does not
run an online resolver or the Hub's download dry run. JSON output distinguishes
missing, invalid and valid local resources. `ready` in a default preflight means
the runtime, config/tokenizer hashes and weight size match; check
`checkpoint.weight_identity_verified` for full local weight hash verification.
The explicit install verifies all four file hashes before reporting success.
These are setup receipts, not retrieval quality or latency measurements.

The default ignored destinations match the optional production owner:

| Resource | Path |
| --- | --- |
| Reranker interpreter | `.cache/reranker-experiment/.venv/bin/python` |
| Original checkpoint | `.cache/models/qwen3-reranker-0.6b-original` |
| Pinned bootstrap | `.cache/bootstrap/bin/uv` |
| Setup Python/package/Hub caches | `.cache/qwen-reranker-install/` |

The installer invokes the venv's absolute `bin/python` path without resolving
its symlink to the managed base interpreter. The inspector uses `-I -S` to
prevent user-site, environment-path and `.pth` startup execution, then reads
only the venv's explicit metadata directory. It checks the invoked executable,
venv configuration/base executable, exact distribution inventory and native
source paths/hashes. On Python 3.12, `-S` makes the observed `sys.prefix` equal
the base prefix; the report retains that fact instead of claiming an inferred
venv prefix was observed. System-site package inclusion is rejected. It never
imports MLX for a setup check. Package installation is confined to a newly created unseeded reranker
venv; the main `.venv` and project package installation remain separate.

New runtime destinations are reserved with an exclusive directory creation.
The installer uses uv's preserve-existing mode only in that owned empty
reservation and checks that its inode remains unchanged. A concurrent install
that creates the destination first causes this install to stop before uv can
recreate it.

Valid existing environments/files are reused. Wrong or incomplete existing
environments, bootstrap versions, or checkpoint bytes are refused; the script
does not repair, delete or overwrite them. A failed install preserves partial
output and downloaded staging files. Choose new unused ignored destinations
to investigate or retry:

```sh
python3 scripts/setup_qwen_reranker.py --install \
  --venv-path .cache/qwen-install-check/.venv \
  --checkpoint .cache/qwen-install-check/model
```

Alternate destinations must be distinct directories inside this checkout's
ignored `.cache`, with no directory symlinks. Downloads go into a private
sibling staging directory. The installer checks each pinned file and publishes
it with an atomic operation that fails if another file already occupies the
destination. It retains staging metadata for inspection. Newly published
checkpoint files use mode 600; owned top-level runtime/model/bootstrap
destinations use mode 700. Nested package directories can have different modes
inside those protected destinations.
It does not change permissions on existing directories.

The lock pins versions, not all wheel/Python archive hashes. Bootstrap uv is
also version pinned rather than an executable hash. This is an exact observed
package closure and checkpoint/native-source verification, not a fully
hash-locked supply chain or a fresh-machine performance claim. Existing user
package caches may affect install time. Before running the model, the
production worker repeats its checkpoint/runtime checks, including the full
weight hash and imported native source origins. Searches remain offline and
never initiate installation.

For startup flags, per-launch MCP defaults, cancellation and worker lifecycle,
see [optional Qwen integration](qwen-reranker-integration.md). The production
gate checks the original sequential singleton scorer; batching is not enabled
by this installer.
