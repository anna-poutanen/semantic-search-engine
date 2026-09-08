#!/usr/bin/env python3
"""Install the pinned Mac runtime, with an optional H2O checkpoint download."""
import argparse
import platform
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REV = "0fc8e0299608867746682a0552c906b03a284a47"
MODEL_REV = "a1048d02d9e713ba9f7481efa5c3df7ec8a2adaa"
MODEL_ID = "netprojects/h2o-lightning-4b-mlx-q8"


def venv_directory(value, root=None):
    """Select an explicit install destination without using a repository ancestor."""
    root = Path(root if root is not None else ROOT).resolve()
    directory = (Path(value) if Path(value).is_absolute() else root / value).resolve()
    if root == directory or root.is_relative_to(directory):
        raise ValueError("venv-path must not be the project directory or one of its ancestors")
    if directory.exists() and not directory.is_dir():
        raise ValueError("venv-path must be a directory")
    return directory


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-only", action="store_true",
                        help="Install dependencies without downloading the optional H2O runtime or weights")
    parser.add_argument("--venv-path", type=Path, default=Path(".venv"),
                        help="Virtual environment directory, absolute or relative to the project (default: .venv)")
    args = parser.parse_args(argv)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("The local model runtime requires an Apple Silicon Mac.")
    try:
        environment = venv_directory(args.venv_path)
    except ValueError as error:
        parser.error(str(error))
    uv = ROOT / ".cache/bootstrap/bin/uv"
    if not uv.exists():
        run(sys.executable, "-m", "pip", "install", "--target",
            ROOT / ".cache/bootstrap", "uv==0.13.0")
    python = environment / "bin/python"
    if not python.exists():
        run(uv, "venv", "--python", "3.12", environment)
    run(python, "-c", "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 'The pinned Mac environment requires Python 3.12')")
    run(uv, "pip", "install", "--python", python, "-r", "requirements-mac.lock", "-r", "requirements-languages.lock")
    run(uv, "pip", "install", "--python", python, "-e", ".[mcp,languages]")
    run(python, "-c", "from semantic_search.context import encoding; encoding(); print('Context tokenizer ready')")
    if args.runtime_only:
        run(python, "-c", "import mlx.core as mx; assert mx.metal.is_available(); print('MLX Metal ready')")
        print("Runtime ready. Download Qwen with: " + shlex.join([
            str(python), "scripts/setup_embeddings.py", "--venv-path", str(environment)]))
        return
    vendor = ROOT / ".cache/vendor/h2o-lightning-mlx"
    if not vendor.exists():
        run("git", "clone", "https://github.com/internet-projects/h2o-lightning-mlx", vendor)
    run("git", "-C", vendor, "checkout", "--detach", RUNTIME_REV)
    run(environment / "bin/hf", "download", MODEL_ID, "--revision", MODEL_REV,
        "--local-dir", ROOT / ".cache/models/h2o-lightning-4b-mlx-q8")
    run(python, "-c", "import mlx.core as mx; assert mx.metal.is_available(); print('MLX Metal ready')")
    print("Ready. Start with: " + shlex.join([str(python), "scripts/serve_mac.py"]))


if __name__ == "__main__":
    main()
