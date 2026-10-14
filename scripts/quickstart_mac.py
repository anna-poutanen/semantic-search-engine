#!/usr/bin/env python3
"""A guided, project-local Apple Silicon setup and interactive search demo."""
from __future__ import annotations

import argparse
import hashlib
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
import urllib.request

from demo_mac import NoRedirect, QUERIES, SETTINGS, fresh_destination, prepare, stop_owned
from setup_embeddings import MODEL_ID, MODEL_REVISION, RUNTIME_VERSION

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS_SHA = "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"
MODEL_FILES = ("model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt")


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def run(*arguments, capture=False):
    process = subprocess.Popen([str(value) for value in arguments], cwd=ROOT, text=True,
                               stdout=subprocess.PIPE if capture else None, start_new_session=True)
    try:
        stdout, stderr = process.communicate()
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, process.args, stdout, stderr)
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    except BaseException:
        stop_owned(process)
        raise


def reserved_directory(path):
    path = Path(path)
    for ancestor in (path, *path.parents):
        if ancestor == ROOT.parent:
            break
        if ancestor.is_symlink():
            raise ValueError(f"Reserved setup path must not be a symlink: {ancestor}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"Reserved setup path is not a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_fingerprint():
    names = ("requirements-mac.lock", "requirements-languages.lock", "pyproject.toml", "scripts/quickstart_mac.py")
    return {name: digest(ROOT / name) for name in names}


def runtime_probe(python):
    # Check the actual interpreter/distributions rather than trusting a completion file.
    code = '''import importlib.metadata as m, json, sys, tomllib
from pathlib import Path
assert sys.version_info[:2] == (3, 12), "Expected Python 3.12"
expected = {}
for name in ("requirements-mac.lock", "requirements-languages.lock"):
    for line in Path(name).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            package, version = line.strip().split("==")
            expected[package] = version
expected["semantic-search-engine"] = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
for name, version in expected.items():
    assert m.version(name) == version, f"{name} differs from pinned version {version}"
import mlx.core as mx
assert mx.metal.is_available(), "MLX Metal unavailable"
from semantic_search.context import encoding
encoding()
print(json.dumps({"python": sys.version, "prefix": sys.prefix, "packages": len(expected), "metal": True}))
'''
    result = run(python, "-c", code, capture=True)
    value = json.loads(result.stdout)
    if Path(value["prefix"]).resolve() != Path(python).parent.parent.resolve():
        raise ValueError("The quickstart Python points outside its dedicated environment")
    return value


def setup_runtime(uv, work):
    environment = work / "venv"
    marker = work / "runtime.json"
    if environment.is_symlink() or (environment.exists() and not environment.is_dir()):
        raise ValueError(f"Refusing unrecognized environment: {environment}")
    python = environment / "bin/python"
    fingerprint = runtime_fingerprint()
    if marker.is_symlink():
        raise ValueError(f"Runtime marker must not be a symlink: {marker}")
    try:
        previous = json.loads(marker.read_text()) if marker.is_file() else None
    except (ValueError, OSError):
        previous = None
    if not isinstance(previous, dict):
        previous = None
    if python.exists() and previous and previous.get("inputs") == fingerprint:
        try:
            inventory = runtime_probe(python)
        except (ValueError, subprocess.SubprocessError):
            print("Repairing the dedicated quickstart runtime…", flush=True)
        else:
            print("Using the checked local runtime.", flush=True)
            return python, inventory
    if environment.exists() and not (environment / "pyvenv.cfg").is_file():
        raise ValueError(f"Move the unrecognized directory aside before retrying: {environment}")
    if not python.exists():
        run(uv, "venv", "--python", sys.executable, environment, "--no-config")
    print("Installing pinned runtime packages into .cache/quickstart/venv…", flush=True)
    run(uv, "pip", "install", "--python", python, "-r", "requirements-mac.lock", "-r", "requirements-languages.lock", "--no-config")
    run(uv, "pip", "install", "--python", python, "--no-deps", "-e", ".[mcp,languages]", "--no-config")
    inventory = runtime_probe(python)
    marker.write_text(json.dumps({"inputs": fingerprint, "inventory": inventory}, indent=2) + "\n")
    return python, inventory


def check_model(directory):
    directory = Path(directory)
    if not all((directory / name).is_file() for name in MODEL_FILES):
        raise ValueError("Embedding checkpoint is incomplete")
    config = json.loads((directory / "config.json").read_text())
    if not isinstance(config, dict) or config.get("model_type") != "qwen3" or config.get("hidden_size") != 1024 or config.get("quantization") or config.get("quantization_config"):
        raise ValueError("Expected the original unquantized Qwen3 embedding configuration")
    metadata = directory / ".cache/huggingface/download/model.safetensors.metadata"
    rows = metadata.read_text().splitlines() if metadata.is_file() else []
    if len(rows) < 2 or rows[:2] != [MODEL_REVISION, WEIGHTS_SHA]:
        raise ValueError("Embedding checkpoint does not identify the pinned Hub revision/weights")
    if digest(directory / "model.safetensors") != WEIGHTS_SHA:
        raise ValueError("Embedding weights failed the pinned SHA-256 check")
    return {"id": MODEL_ID, "revision": MODEL_REVISION, "runtime": "mlx-embeddings",
            "runtime_version": RUNTIME_VERSION, "weights_dtype": "bfloat16", "quantized": False,
            "weights_etag": WEIGHTS_SHA, "source": f"https://huggingface.co/{MODEL_ID}/tree/{MODEL_REVISION}"}


def setup_model(python):
    directory = ROOT / ".cache/models/qwen3-embedding-0.6b"
    reserved_directory(directory)
    marker_path = directory / "semantic_search_checkpoint.json"
    if marker_path.is_symlink():
        raise ValueError("Checkpoint provenance must not be a symlink")
    for name in MODEL_FILES:
        if (directory / name).is_symlink():
            raise ValueError(f"Checkpoint file must not be a symlink: {name}")
    reserved_directory(directory / ".cache/huggingface/download")
    if (directory / ".cache/huggingface/download/model.safetensors.metadata").is_symlink():
        raise ValueError("Checkpoint metadata must not be a symlink")
    try:
        marker = check_model(directory)
    except (OSError, ValueError):
        print("Downloading pinned Qwen embedding weights (~1.1 GiB). Interrupted downloads can resume…", flush=True)
        # A complete but modified file could otherwise be accepted by the Hub cache.
        weight = directory / "model.safetensors"
        corrupt_weight = weight.is_file() and digest(weight) != WEIGHTS_SHA
        complete_but_invalid = all((directory / name).is_file() for name in MODEL_FILES)
        force = ["--force-download"] if corrupt_weight or complete_but_invalid else []
        run(python.parent / "hf", "download", MODEL_ID, "--revision", MODEL_REVISION, "--local-dir", directory, *force)
        marker = check_model(directory)
    marker_path.write_text(json.dumps(marker, indent=2) + "\n")
    print("Embedding checkpoint verified. Rerankers are disabled.", flush=True)


def request_json(url, payload=None, timeout=120):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, None if payload is None else json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Service response exceeded 1 MiB")
    return json.loads(raw)


def interactive(python, repo, source_root, work, timeout=180):
    identity = hashlib.sha256((str(repo) + "\n" + source_root).encode()).hexdigest()
    artifacts = reserved_directory(work / "sessions" / (time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(4).hex()))
    log = artifacts / "service.log"
    cache = reserved_directory(work / "indexes" / identity)
    arguments = [python, "-m", "semantic_search.cli", "serve", "--repo", repo, "--source-root", source_root,
                 "--index-cache", cache, "--port", "0", "--no-watch"]
    process = None
    searches = 0
    previous = signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        print(f"\nIndexing saved, Git-tracked code in {repo} ({source_root})…", flush=True)
        with log.open("wb") as stream:
            process = subprocess.Popen([str(item) for item in arguments], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        began = time.monotonic()
        while time.monotonic() - began < timeout:
            with log.open("rb") as stream:
                content = stream.read(2 * 1024 * 1024).decode(errors="replace")
            match = re.search(r"^Ready: (http://127\.0\.0\.1:[0-9]+)", content, re.MULTILINE)
            if match:
                url = match[1]
                status = request_json(url + "/status", timeout=timeout)
                if Path(status["repo"]).resolve() != repo.resolve() or status["source_root"] != source_root or not status["ready"]:
                    raise ValueError("Ready service has unexpected repository identity")
                break
            if process.poll() is not None:
                raise RuntimeError(f"Service exited before readiness. See {log}")
            time.sleep(.1)
        else:
            raise RuntimeError(f"Service readiness timed out. See {log}")
        print(f"\nReady: {url}\nDescribe an implementation, enter a sample number, or q to quit.", flush=True)
        for index, query in enumerate(QUERIES, 1):
            print(f"  {index}. {query}")
        print("The service stays available to CLI, VS Code and MCP until you quit.", flush=True)
        while True:
            try:
                query = input("\nSearch> ").strip()
            except EOFError:
                break
            if query.lower() in ("q", "quit", "exit"):
                break
            if not query:
                continue
            if query in ("1", "2", "3"):
                query = QUERIES[int(query) - 1]
            value = request_json(url + "/search", {"query": query, **SETTINGS}, timeout)
            searches += 1
            rows = value.get("results", [])
            if not rows:
                print("No eligible implementations found.")
            for row in rows:
                print(f"\n{row['path']}:{row['start_line']}–{row['end_line']}  {row['symbol']}\n{row['source']}")
    finally:
        try:
            shutdown = stop_owned(process) if process is not None else None
            (artifacts / "session.json").write_text(json.dumps({"repo": str(repo), "source_root": source_root,
                "searches": searches, "shutdown": shutdown}, indent=2) + "\n")
        finally:
            signal.signal(signal.SIGTERM, previous)
    if shutdown != {"returncode": 0, "forced_kill": False}:
        raise RuntimeError(f"Service did not stop cleanly: {shutdown}. See {log}")
    print(f"Service closed. Local session log: {artifacts}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv-path", type=Path, required=True, help=argparse.SUPPRESS)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Run the toy HTTP + MCP checks and exit")
    modes.add_argument("--setup-only", action="store_true", help="Install runtime and weights without starting inference")
    parser.add_argument("--repo", type=Path, help="Search an existing Git repository instead of the fresh toy")
    parser.add_argument("--source-root", default="src", help="Repository-relative source directory (default: src)")
    args = parser.parse_args(argv)
    if platform.system() != "Darwin" or platform.machine() != "arm64" or sys.version_info[:2] != (3, 12):
        parser.error("Use Get Started.command on an Apple Silicon Mac with its managed Python 3.12")
    if not args.check and not args.setup_only and not sys.stdin.isatty():
        parser.error("Interactive mode requires a terminal. Use --check for an automatic demo or --setup-only")
    if args.check and args.repo:
        parser.error("--check validates the fresh toy; use interactive --repo for your project")
    repo = args.repo.expanduser().resolve() if args.repo else None
    if repo is not None:
        source = Path(args.source_root)
        if source.is_absolute() or ".." in source.parts or not (repo / source).is_dir():
            parser.error("--source-root must be an existing repository-relative directory without '..'")
        resolved_source = (repo / source).resolve()
        if not resolved_source.is_relative_to(repo):
            parser.error("--source-root must resolve inside the selected repository")
        source_root = str(resolved_source.relative_to(repo))
        if not (repo / ".git").exists():
            parser.error("--repo must name a Git checkout root (linked worktrees are supported)")
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    previous = {signum: signal.signal(signum, interrupt) for signum in (signal.SIGTERM, signal.SIGHUP)}
    try:
        work = reserved_directory(ROOT / ".cache/quickstart")
        for variable, relative in (("UV_CACHE_DIR", "uv-cache"), ("UV_PYTHON_INSTALL_DIR", "python"),
                                   ("HF_HOME", "huggingface"), ("HF_HUB_CACHE", "huggingface/hub"),
                                   ("HF_XET_CACHE", "huggingface/xet"), ("TIKTOKEN_CACHE_DIR", "tiktoken")):
            os.environ[variable] = str(reserved_directory(work / relative))
        os.environ["UV_NO_CONFIG"] = "1"
        lock_path = work / "setup.lock"
        if lock_path.is_symlink():
            raise ValueError("Setup lock must not be a symlink")
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another launcher is setting up this download. Wait for it to finish and retry") from None
            python, _inventory = setup_runtime(args.uv_path.absolute(), work)
            setup_model(python)
        if args.setup_only:
            print(f"\nSetup complete. Start the demo with: bash {str(ROOT / 'Get Started.command')!r}")
        elif args.check:
            run(python, ROOT / "scripts/demo_mac.py", "--run", "--quiet", "--python", python)
        else:
            if repo is None:
                repo = prepare(fresh_destination())
                source_root = "src"
            interactive(python, repo, source_root, work)
    except KeyboardInterrupt:
        parser.exit(130, "\nInterrupted; owned service cleanup completed. Rerun to resume setup.\n")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"\nQuickstart stopped: {error}\nRerun the launcher to resume. Your existing .venv is unchanged.\n")
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    main()
