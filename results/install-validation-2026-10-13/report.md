# Isolated Mac dependency installation

The dependency-only installer succeeded in a new environment on macOS 15.1.1,
Apple Silicon, using uv-managed CPython 3.12.15. `uv pip check` passed; all
**72 declared runtime/parser versions matched**. The corrected inventory plus
source-neutral Python/JS/TS, SQLite, HTTP, and official MCP client checks passed:
**118 tests, no skips**, in 13.10 seconds. No Qwen/H2O weights were downloaded and
no model forward ran. MLX's Metal-availability check passed.

The initial installer checkout was commit
`6f248eba6b927634044e9df9fc68693154a13608`. Its command log, script/lock hashes,
OS/interpreter details, cache membership, installed distributions, and main
environment inventories are preserved in [results.json](results.json).
The installation took 3.35 seconds with existing user-level uv package and
managed-Python caches. Bootstrap uv itself was installed from a cached pip
wheel. These timings describe this reused-cache installation; they do not
estimate a new machine's downloads or total setup time.

The first CPU gate had **112 passes and one audit failure**: it expected the
entire uv version output to equal `uv 0.13.0`, while the official binary reports
`uv 0.13.0 (4ecd0eff2 2026-10-09 aarch64-apple-darwin)`. The audit now checks the
exact release token and records the full build string and executable hash.
No package version changed. The failed [initial log](cpu-checks.log) and
[follow-up proof](followup.json) remain separate. The follow-up added five CPU
regressions for exact versions with optional build metadata.

The main project's installed-package inventory is identical before/after.
The new environment has its own editable project installation. Adding the
test extra installed pytest 8.3.5, iniconfig 2.3.1, and pluggy 1.6.0; the recorded
runtime versions remained unchanged. New checkout-local caches include about
30.3 MB of bootstrap files and 1.68 MB of tokenizer data. The environment's sum
of file sizes is 617,028,750 bytes, including symlink targets; this is neither
download volume nor physical disk allocation because uv can reuse links or
clones. No checkout-local model directory was created.

The fresh resolver exposed one missing lock entry: **protobuf 7.36.2**.
Installed primary metadata shows `mlx-embeddings==0.1.0` requesting
`transformers[sentencepiece]>=5.0.0`, whose selected 5.19.0 distribution requests
protobuf for that extra. Its installed wheel tag is
`cp310-abi3-macosx_10_9_universal2`.
[Dependency metadata](dependency-metadata.json) records versions, requirements,
and METADATA/WHEEL hashes. Other apparent name differences are normalized using
PEP 503 rules. The original lock and failed/corrected first-run records are
retained; a separate follow-up will pin this observed dependency and verify
closure while reusing the validated environment.

This validates dependency installation and CPU behavior on one existing Mac.
It does not establish a cold-machine install, uncached model download, or
real-model search in the new environment. Build dependencies use
`setuptools>=75`, the Python patch version is not fixed, and the requirement
files do not contain wheel hashes. The supported workflow and remaining gates
are in [installer validation](../../docs/installer-validation.md).
