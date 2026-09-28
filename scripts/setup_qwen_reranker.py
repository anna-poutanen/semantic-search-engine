#!/usr/bin/env python3
"""Inspect or explicitly install the isolated, pinned optional Qwen reranker.

Default/--dry-run is local metadata only: no install, downloads, MLX imports,
model loads or forwards. --install alone authorizes setup network/writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = "0.13.0"
PYTHON_VERSION = "3.12.15"
LOCK_NAME = "requirements-qwen-reranker.lock"
LOCK_SHA256 = "19a6993a95ed349bd445ebd99e05a2650120a1242dbe1fa291f6e9d900d3467a"
MODEL_ID = "Qwen/Qwen3-Reranker-0.6B"
REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"
PINS = {
    "config.json": (727, "d479c427a9ca5295218063d4f9aca4f297ab4ac27487cca7af42c84643d51ef0"),
    "tokenizer_config.json": (9706, "253153d0738ceb4c668d2eff957714dd2bea0b56de772a9fdccd96cbf517e6a0"),
    "tokenizer.json": (11422654, "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"),
    "model.safetensors": (1191588280, "27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b"),
}
NATIVE_PINS = {
    "qwen3.py": "7e29b7055d8e526a0ede6e2740a3038ad24d0cef6a1abb446c411d3fbd0efa2d",
    "base.py": "b782b2ac0f55ed8fa9c1afce1f36307d5d8714aad2c0991d63998047f7507174",
    "rope_utils.py": "06466d57dfe1ccc1e61ba595c0a8f2dd3a14bcb5c6a1d06c67591dc03128606b",
}
# -S also prevents arbitrary site/.pth startup execution. Python3.12's observed
# prefix is then the base prefix, so report it honestly and read only explicit
# venv metadata directories; never substitute an inferred observed sys.prefix.
METADATA_CODE = '''import hashlib,importlib.metadata as m,json,platform,re,sys
site=sys.argv[1]
distributions=list(m.distributions(path=[site]))
packages=[{"name":d.metadata["Name"],"version":d.version,"root":str(d.locate_file(""))} for d in distributions]
native={}
for d in distributions:
 if re.sub(r"[-_.]+","-",d.metadata["Name"]).lower()=="mlx-lm":
  for name in ("qwen3.py","base.py","rope_utils.py"):
   p=d.locate_file("mlx_lm/models/"+name)
   native[name]={"path":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None}
print(json.dumps({"python":platform.python_version(),"observed_prefix":sys.prefix,"observed_base_prefix":sys.base_prefix,
                  "executable":sys.executable,"metadata_directory":site,
                  "packages":packages,"native":native,"model_calls":0,"mlx_imported":any(n=="mlx" or n.startswith("mlx.") for n in sys.modules)}))
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def lock_pins(root=ROOT):
    path = root / LOCK_NAME
    require(file_hash(path) == LOCK_SHA256, "Pinned reranker lock bytes changed")
    result = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        require(match is not None, "Only exact, unconditional package pins are accepted")
        name, version = canonical(match[1]), match[2]
        require(name not in result, "Duplicate package pin: " + name)
        result[name] = version
    require(len(result) == 34, "Expected the original 34-package dependency closure")
    return result


def private_destination(value, root=ROOT):
    path = Path(value)
    path = Path(os.path.abspath(path if path.is_absolute() else root / path))
    cache = root.resolve() / ".cache"
    require(path.is_relative_to(cache) and path != cache, "Destination must be below this checkout's ignored .cache")
    require(path.resolve().is_relative_to(cache), "Destination symlink escapes ignored .cache")
    for ancestor in (path, *path.parents):
        if ancestor == root:
            break
        require(not ancestor.is_symlink(), "Destination directories must not be symlinks")
    require(not path.exists() or path.is_dir(), "Destination must be a directory")
    return path


def environment_for_setup(root=ROOT):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTHON", "UV_", "PIP_", "HF_", "HUGGINGFACE_"))
           and key not in {"VIRTUAL_ENV", "CONDA_PREFIX"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", UV_CACHE_DIR=str(root / ".cache/qwen-reranker-install/uv-cache"),
               UV_PYTHON_INSTALL_DIR=str(root / ".cache/qwen-reranker-install/python-managed"),
               HF_HOME=str(root / ".cache/qwen-reranker-install/huggingface"), HF_HUB_DISABLE_TELEMETRY="1",
               HF_HUB_DISABLE_PROGRESS_BARS="1")
    return env


def command(args, *, root=ROOT, timeout=1800):
    return subprocess.run([str(arg) for arg in args], cwd=root, env=environment_for_setup(root),
                          text=True, capture_output=True, check=True, timeout=timeout).stdout


def inspect_runtime(environment, pins, *, run=command, root=ROOT):
    python = (environment / "bin/python").absolute()  # Never resolve this venv symlink for invocation.
    if not environment.exists():
        return {"status": "missing", "python_invocation": str(python), "errors": []}
    if not python.is_file() or not (environment / "pyvenv.cfg").is_file():
        return {"status": "invalid", "python_invocation": str(python), "errors": ["Existing destination is not a complete venv; choose a new path"]}
    try:
        cfg = dict(line.split("=", 1) for line in (environment / "pyvenv.cfg").read_text().splitlines() if "=" in line)
        cfg = {key.strip().lower(): value.strip() for key, value in cfg.items()}
        require(cfg.get("include-system-site-packages", "").lower() == "false", "Venv must exclude system site packages")
        require(Path(cfg.get("home", "")).resolve() == python.resolve().parent, "Venv home differs from actual base interpreter")
        if cfg.get("executable"):
            require(Path(cfg["executable"]).resolve() == python.resolve(), "Venv executable differs from configured interpreter")
        site = environment / "lib/python3.12/site-packages"
        require(site.is_dir() and site.resolve().is_relative_to(environment.resolve()), "Explicit venv metadata directory is missing or escapes isolation")
        data = json.loads(run([python, "-I", "-S", "-B", "-c", METADATA_CODE, site], root=root, timeout=30))
        packages = {}
        for item in data["packages"]:
            name = canonical(item["name"])
            require(name not in packages, "Duplicate installed distribution: " + name)
            require(Path(item["root"]).resolve().is_relative_to(site.resolve()), "Installed distribution escapes isolated metadata directory")
            packages[name] = item["version"]
        errors = []
        if data["python"] != PYTHON_VERSION:
            errors.append("Requires Python " + PYTHON_VERSION)
        if Path(data["executable"]).absolute() != python or Path(data["metadata_directory"]).resolve() != site.resolve():
            errors.append("Metadata inspector invocation/directory differs from requested venv")
        if packages != pins:
            errors.append("Installed package closure differs from the exact 34 pins")
        for name, expected in NATIVE_PINS.items():
            if data.get("native", {}).get(name, {}).get("sha256") != expected:
                errors.append("Pinned native source differs: " + name)
            elif not Path(data["native"][name]["path"]).resolve().is_relative_to(site.resolve()):
                errors.append("Pinned native source origin escapes isolated venv: " + name)
        if data.get("model_calls") != 0 or data.get("mlx_imported") is not False:
            errors.append("Metadata inspection imported MLX or called a model")
        return {"status": "valid" if not errors else "invalid", "python_invocation": str(python),
                "resolved_binary": str(python.resolve()), "metadata": data, "errors": errors}
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        return {"status": "invalid", "python_invocation": str(python),
                "errors": ["Cannot verify existing runtime: " + type(error).__name__ + ": " + str(error)]}


def inspect_checkpoint(directory, *, verify_weights=False, pins=None):
    pins = PINS if pins is None else pins
    files, errors = {}, []
    if directory.exists():
        extras = {path.name for path in directory.glob("*.safetensors")} - {"model.safetensors"}
        if extras:
            errors.append("Unexpected safetensors files: " + ", ".join(sorted(extras)))
    for name, (size, expected) in pins.items():
        path = directory / name
        if not path.exists() and not path.is_symlink():
            files[name] = {"status": "missing", "size": None, "sha256": None}
            continue
        actual_size = path.stat().st_size if path.is_file() and not path.is_symlink() else None
        hashed = actual_size == size and (name != "model.safetensors" or verify_weights)
        actual_hash = file_hash(path) if hashed else None
        valid = actual_size == size and (not hashed or actual_hash == expected)
        files[name] = {"status": "valid" if valid else "invalid", "size": actual_size, "sha256": actual_hash}
        if not valid:
            errors.append("Existing pinned checkpoint file differs; choose a new path: " + name)
    state = "invalid" if errors else "missing" if any(row["status"] == "missing" for row in files.values()) else "valid"
    return {"status": state, "files": files, "errors": errors, "weight_identity_verified":
            files.get("model.safetensors", {}).get("sha256") == pins.get("model.safetensors", (None, None))[1]
            and files.get("model.safetensors", {}).get("status") == "valid"}


def inspect_bootstrap(root=ROOT, *, run=command):
    directory = root / ".cache/bootstrap"
    uv = directory / "bin/uv"
    if not directory.exists():
        return {"status": "missing", "path": str(uv), "errors": []}
    if not uv.is_file() or uv.is_symlink() or directory.is_symlink():
        return {"status": "invalid", "path": str(uv), "errors": ["Existing bootstrap is incomplete or a symlink"]}
    try:
        version = run([uv, "--version"], root=root, timeout=30).strip()
        valid = re.fullmatch(r"uv " + re.escape(UV_VERSION) + r"(?: \([^\n]*\))?", version) is not None
        return {"status": "valid" if valid else "invalid", "path": str(uv), "version": version,
                "errors": [] if valid else ["Existing bootstrap must be uv==" + UV_VERSION]}
    except (OSError, subprocess.SubprocessError) as error:
        return {"status": "invalid", "path": str(uv), "errors": ["Cannot inspect existing bootstrap: " + str(error)]}


def preflight(environment, checkpoint, *, verify_weights=False, root=ROOT, run=command):
    pins = lock_pins(root)
    runtime = inspect_runtime(environment, pins, run=run, root=root)
    model = inspect_checkpoint(checkpoint, verify_weights=verify_weights)
    bootstrap = inspect_bootstrap(root, run=run)
    supported = platform.system() == "Darwin" and platform.machine() == "arm64"
    errors = runtime["errors"] + model["errors"] + bootstrap["errors"]
    return {"mode": "preflight", "model_calls": 0, "weights_loaded": False, "network_calls": 0,
            "writes": 0, "host": {"system": platform.system(), "machine": platform.machine(), "supported": supported},
            "python_version": PYTHON_VERSION, "lock_sha256": LOCK_SHA256, "package_count": len(pins),
            "model_id": MODEL_ID, "revision": REVISION, "runtime": runtime, "checkpoint": model,
            "bootstrap": bootstrap, "errors": errors,
            "ready": supported and runtime["status"] == model["status"] == "valid" and not errors,
            "setup_requires": "Explicit --install permits package/Python/model downloads and ignored-cache writes; no inference"}


def mkdir_new_or_parent(path):
    """Only explicit installation calls this; do not chmod existing directories."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def publish_file(staged, target):
    """Hard-link publication is atomic and refuses an existing target."""
    require(staged.is_file() and not staged.is_symlink(), "Downloaded file must be an ordinary file")
    staged.chmod(0o600)
    os.link(staged, target)
    staged.unlink()


def install(environment, checkpoint, *, root=ROOT, run=command):
    before = preflight(environment, checkpoint, verify_weights=True, root=root, run=run)
    require(before["host"]["supported"], "The Qwen reranker requires an Apple Silicon Mac")
    require(not before["errors"], "Refusing to alter an invalid existing setup: " + "; ".join(before["errors"]))
    uv = root / ".cache/bootstrap/bin/uv"
    actions = []
    try:
        if before["bootstrap"]["status"] == "missing":
            mkdir_new_or_parent(uv.parent.parent.parent)
            uv.parent.parent.mkdir(mode=0o700)  # Refuse concurrent or partial bootstrap overwrites.
            actions.append("bootstrap_uv")
            run([sys.executable, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
                 "--no-deps", "--target", uv.parent.parent, "uv==" + UV_VERSION], root=root)
            require(inspect_bootstrap(root, run=run)["status"] == "valid", "Installed uv version does not match")
        python = (environment / "bin/python").absolute()
        if before["runtime"]["status"] == "missing":
            mkdir_new_or_parent(environment.parent)
            environment.mkdir(mode=0o700)  # Exclusive reservation; never race uv's default recreation.
            reserved = environment.stat()
            require(not any(environment.iterdir()), "Owned runtime reservation is no longer empty")
            actions.append("create_isolated_python")
            run([uv, "--no-config", "venv", "--no-project", "--allow-existing", "--managed-python", "--python", PYTHON_VERSION,
                 environment], root=root)
            created = environment.stat()
            require((created.st_dev, created.st_ino) == (reserved.st_dev, reserved.st_ino),
                    "Virtual environment creation replaced the owned destination")
            environment.chmod(0o700)  # Only this newly reserved directory belongs to this install.
            actions.append("install_locked_packages")
            run([uv, "--no-config", "pip", "install", "--python", python, "--no-deps", "--no-build",
                 "-r", root / LOCK_NAME], root=root)
            run([uv, "--no-config", "pip", "check", "--python", python], root=root)
        require(inspect_runtime(environment, lock_pins(root), run=run, root=root)["status"] == "valid",
                "Installed runtime does not match original Python/package/native-source identity")
        missing = [name for name, row in before["checkpoint"]["files"].items() if row["status"] == "missing"]
        if missing:
            mkdir_new_or_parent(checkpoint)
            staging = Path(tempfile.mkdtemp(prefix=".qwen-download-", dir=checkpoint.parent))
            actions.append("download_pinned_checkpoint")
            # Explicit filenames avoid unneeded repository files/custom Hub code.
            run([environment / "bin/hf", "download", MODEL_ID, *missing, "--revision", REVISION,
                 "--local-dir", staging, "--max-workers", "1"], root=root)
            staged = inspect_checkpoint(staging, verify_weights=True)
            require(not staged["errors"] and all(staged["files"][name]["status"] == "valid" for name in missing),
                    "Downloaded checkpoint bytes differ from original pinned files; staging retained")
            for name in missing:
                publish_file(staging / name, checkpoint / name)
        after = preflight(environment, checkpoint, verify_weights=True, root=root, run=run)
        require(after["ready"] and after["checkpoint"]["weight_identity_verified"], "Setup verification failed")
        return {"mode": "install", "checks_passed": True, "model_calls": 0, "weights_loaded": False,
                "before": before, "after": after, "actions": actions}
    except BaseException as error:
        # Preserve owned partial output; choose a new destination rather than repair
        # a failed/unknown environment in place. No misleading 'ready' receipt.
        if hasattr(error, "add_note"):
            error.add_note("Qwen setup actions attempted: " + ", ".join(actions))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv-path", type=Path, default=Path(".cache/reranker-experiment/.venv"))
    parser.add_argument("--checkpoint", type=Path, default=Path(".cache/models/qwen3-reranker-0.6b-original"))
    parser.add_argument("--verify-weights", action="store_true", help="Read/hash local BF16 weight bytes; no model load")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="Default: inspect local metadata without network/writes")
    modes.add_argument("--install", action="store_true", help="Explicitly install/download into ignored isolated paths")
    args = parser.parse_args(argv)
    try:
        environment, checkpoint = private_destination(args.venv_path), private_destination(args.checkpoint)
        require(environment != checkpoint and not environment.is_relative_to(checkpoint)
                and not checkpoint.is_relative_to(environment), "Runtime and checkpoint destinations must be separate")
        result = install(environment, checkpoint) if args.install else preflight(environment, checkpoint,
                                                                               verify_weights=args.verify_weights)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"checks_passed": False, "model_calls": 0, "weights_loaded": False,
                          "error_type": type(error).__name__, "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
