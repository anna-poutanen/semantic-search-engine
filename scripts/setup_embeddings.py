#!/usr/bin/env python3
"""Install MLX embeddings and download pinned original Qwen BF16 weights.

No model conversion or GPU inference is performed by this setup script.
"""
import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

from setup_mac import venv_directory

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
RUNTIME_VERSION = "0.1.0"
MODEL_PATH = ROOT / ".cache/models/qwen3-embedding-0.6b"


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv-path", type=Path, default=Path(".venv"),
                        help="Virtual environment directory, absolute or relative to the project (default: .venv)")
    args = parser.parse_args(argv)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("The embedding runtime requires an Apple Silicon Mac.")
    try:
        environment = venv_directory(args.venv_path, ROOT)
    except ValueError as error:
        parser.error(str(error))
    python = environment / "bin/python"
    uv = ROOT / ".cache/bootstrap/bin/uv"
    if not python.exists() or not uv.exists():
        raise SystemExit("Run python3 scripts/setup_mac.py first.")
    # Existing pinned Mac runtime supplies MLX, mlx-vlm and Transformers.
    run(uv, "pip", "install", "--python", python, "--no-deps",
        f"mlx-embeddings=={RUNTIME_VERSION}")
    run(environment / "bin/hf", "download", MODEL_ID, "--revision", MODEL_REVISION,
        "--local-dir", MODEL_PATH)
    metadata = MODEL_PATH / ".cache/huggingface/download/model.safetensors.metadata"
    if not metadata.is_file() or metadata.read_text().splitlines()[0] != MODEL_REVISION:
        raise SystemExit("Downloaded weights do not have the expected Hub revision")
    config = json.loads((MODEL_PATH / "config.json").read_text())
    if config.get("quantization") or config.get("quantization_config"):
        raise SystemExit("Expected unquantized original Qwen weights")
    (MODEL_PATH / "semantic_search_checkpoint.json").write_text(json.dumps({
        "id": MODEL_ID, "revision": MODEL_REVISION,
        "runtime": "mlx-embeddings", "runtime_version": RUNTIME_VERSION,
        "weights_dtype": "bfloat16", "quantized": False,
        "weights_etag": metadata.read_text().splitlines()[1],
        "source": f"https://huggingface.co/{MODEL_ID}/tree/{MODEL_REVISION}",
    }, indent=2) + "\n")
    print(f"Ready: {MODEL_ID}@{MODEL_REVISION}, original BF16 weights.")


if __name__ == "__main__":
    main()
