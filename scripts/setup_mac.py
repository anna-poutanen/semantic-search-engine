#!/usr/bin/env python3
"""Install the pinned Mac runtime and checkpoint into ignored project folders."""
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REV = "0fc8e0299608867746682a0552c906b03a284a47"
MODEL_REV = "a1048d02d9e713ba9f7481efa5c3df7ec8a2adaa"
MODEL_ID = "netprojects/h2o-lightning-4b-mlx-q8"


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("The H2O runtime requires an Apple Silicon Mac.")
    uv = ROOT / ".cache/bootstrap/bin/uv"
    if not uv.exists():
        run(sys.executable, "-m", "pip", "install", "--target",
            ROOT / ".cache/bootstrap", "uv==0.13.0")
    python = ROOT / ".venv/bin/python"
    if not python.exists():
        run(uv, "venv", "--python", "3.12", ROOT / ".venv")
    run(uv, "pip", "install", "--python", python, "-r", "requirements-mac.lock")
    run(uv, "pip", "install", "--python", python, "-e", ".[mcp]")
    run(python, "-c", "from semantic_search.context import encoding; encoding(); print('Context tokenizer ready')")
    vendor = ROOT / ".cache/vendor/h2o-lightning-mlx"
    if not vendor.exists():
        run("git", "clone", "https://github.com/internet-projects/h2o-lightning-mlx", vendor)
    run("git", "-C", vendor, "checkout", "--detach", RUNTIME_REV)
    run(ROOT / ".venv/bin/hf", "download", MODEL_ID, "--revision", MODEL_REV,
        "--local-dir", ROOT / ".cache/models/h2o-lightning-4b-mlx-q8")
    run(python, "-c", "import mlx.core as mx; assert mx.metal.is_available(); print('MLX Metal ready')")
    print("Ready. Start with: .venv/bin/python scripts/serve_mac.py")


if __name__ == "__main__":
    main()
