#!/usr/bin/env python3
"""A guided, project-local Apple Silicon setup and interactive search demo."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.client import HTTPException
import fcntl
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid

from beta_tools import diagnose, write_mcp_config
from demo_mac import NoRedirect, QUERIES, SETTINGS, fresh_destination, prepare, stop_owned
from setup_embeddings import MODEL_ID, MODEL_REVISION, RUNTIME_VERSION

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS_SHA = "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"
MODEL_FILES = ("model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt")


@contextmanager
def defer_cleanup_interrupts():
    """Finish bounded owned shutdown and its receipt before honoring another interrupt."""
    interrupted = False

    def defer(signum, frame):
        nonlocal interrupted
        interrupted = True

    previous = {signum: signal.signal(signum, defer)
                for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if interrupted:
        raise KeyboardInterrupt


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
        with defer_cleanup_interrupts():
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


OWN_SETTINGS = {"retrieval": "dense", "rerank": False, "candidates": 4,
                "max_results": 8, "source_budget": 12000, "token_budget": 4096,
                "include_dependencies": True}


def positive_seconds(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Use a finite positive number of seconds")
    return result


def bounded_integer(minimum, maximum):
    def parse(value):
        result = int(value)
        if not minimum <= result <= maximum:
            raise argparse.ArgumentTypeError(f"Use an integer from {minimum} through {maximum}")
        return result
    return parse


def selected_settings(args, toy):
    settings = dict(SETTINGS if toy else OWN_SETTINGS)
    for name in ("max_results", "source_budget", "token_budget"):
        if getattr(args, name) is not None:
            settings[name] = getattr(args, name)
    if args.no_dependencies:
        settings["include_dependencies"] = False
    return settings


def new_log_lines(log, state):
    """Tail bounded new bytes; a large log cannot hide the later Ready line."""
    with log.open("rb") as stream:
        if stream.seek(0, os.SEEK_END) < state["offset"]:
            state.update(offset=0, pending=b"")
        stream.seek(state["offset"])
        chunk = stream.read(128 * 1024)
        state["offset"] = stream.tell()
    content = state["pending"] + chunk
    lines = content.split(b"\n")
    state["pending"] = lines.pop()[-65536:]
    return [line.decode("utf-8", errors="replace") for line in lines if len(line) <= 65536]


def format_progress(line):
    if not line.startswith("Progress: "):
        return None
    try:
        value = json.loads(line[len("Progress: "):])
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    phases = {"scan": "Scanning tracked source", "extract": "Extracting implementations",
              "cache": "Reusable embeddings", "embed": "Embedding new documents",
              "save": "Saving the index", "indexed": "Index snapshot complete", "ready": "Search ready"}
    phase, unit = value.get("phase"), value.get("unit")
    if phase not in phases or unit not in ("files", "documents", "windows", "service"):
        return None
    completed, total = value.get("completed"), value.get("total")
    if type(completed) is not int or completed < 0:
        return None
    if total is not None and (type(total) is not int or total < completed):
        return None
    text = f"{phases[phase]}: {completed}" + (f"/{total}" if total is not None else "") + f" {unit}"
    if phase == "cache":
        cached, pending = value.get("cached_documents"), value.get("pending_documents")
        if type(cached) is int and cached >= 0 and type(pending) is int and pending >= 0:
            text += f" ({cached} cached, {pending} new)"
    return phase, text, completed == total


def interactive(python, repo, source_root, work, timeout=1800, *, request_timeout=120,
                settings=None, toy=False, port=0, exclusions=()):
    settings = dict(OWN_SETTINGS if settings is None else settings)
    exclusions = tuple(sorted(set(exclusions)))
    identity = hashlib.sha256((str(repo) + "\n" + source_root + "\n" + json.dumps(list(exclusions))).encode()).hexdigest()
    artifacts = reserved_directory(work / "sessions" / (time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(4).hex()))
    log = artifacts / "service.log"
    cache = reserved_directory(work / "indexes" / identity)
    if port:
        with socket.socket() as probe:
            # Match HTTPServer so recently closed connections in TIME_WAIT do
            # not prevent reuse of a stable MCP address. A live listener still
            # owns its port; SO_REUSEPORT is deliberately not enabled.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise ValueError(f"Port {port} is unavailable; choose a different --port or use --port 0") from error
    arguments = [python, "-m", "semantic_search.cli", "serve", "--repo", repo, "--source-root", source_root,
                 "--index-cache", cache, "--port", str(port), "--no-watch", "--progress"]
    for pattern in exclusions:
        arguments.extend(("--exclude-source", pattern))
    process = None
    searches = errors = 0
    status = None
    startup_elapsed = None
    previous = signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        print(f"\nIndexing saved, Git-tracked code in {repo} ({source_root})…", flush=True)
        print(f"Startup allowance: {timeout:g} seconds. Ctrl-C closes this service.", flush=True)
        with log.open("wb") as stream:
            process = subprocess.Popen([str(item) for item in arguments], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        began = time.monotonic()
        state = {"offset": 0, "pending": b""}
        last_phase, last_progress = None, began
        while time.monotonic() - began < timeout:
            url = None
            for line in new_log_lines(log, state):
                progress = format_progress(line)
                if progress is not None:
                    phase, message, complete = progress
                    now = time.monotonic()
                    if phase != last_phase or complete or now - last_progress >= 5:
                        print(message, flush=True)
                        last_phase, last_progress = phase, now
                match = re.fullmatch(r"Ready: (http://127\.0\.0\.1:[0-9]+)(?: .*)?", line.rstrip("\r"))
                if match:
                    url = match[1]
            if url:
                remaining = max(.1, timeout - (time.monotonic() - began))
                status = request_json(url + "/status", timeout=min(request_timeout, remaining))
                if Path(status["repo"]).resolve() != repo.resolve() or status["source_root"] != source_root or not status["ready"]:
                    raise ValueError("Ready service has unexpected repository identity")
                if status.get("source_exclusions", []) != list(exclusions):
                    raise ValueError("Ready service has unexpected source exclusions")
                startup_elapsed = time.monotonic() - began
                break
            if process.poll() is not None:
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 12000))
                    tail = stream.read(12000).decode("utf-8", errors="replace")
                reasons = [line for line in tail.splitlines() if line.startswith(("semantic_search.backend.SourceSnapshotError:", "ValueError:", "SyntaxError:"))]
                reason = re.sub(r"[\x00-\x1f\x7f]", " ", reasons[-1])[:1000] if reasons else "Inspect the service log for the exact failure"
                raise RuntimeError(f"Service exited before readiness: {reason}. Inspect {log}. "
                                   "Use --exclude-source GLOB for generated/template files; --doctor checks prerequisites, not source syntax")
            now = time.monotonic()
            if now - last_progress >= 10:
                print(f"Still indexing/warming ({now - began:.0f}s elapsed); service log: {log}", flush=True)
                last_progress = now
            time.sleep(.1)
        else:
            raise RuntimeError(f"Startup exceeded {timeout:g}s. Increase --startup-timeout for a larger repository. Inspect {log}")
        print(f"\nReady: {url}", flush=True)
        windows = status.get("windows")
        if type(windows) is int and windows >= 0:
            print(f"Index: {windows} implementation windows; source directory: {source_root}")
        if exclusions:
            print("Excluded source: " + ", ".join(exclusions))
        if windows == 0:
            print("No eligible implementations were indexed. Check --source-root, save files and use git add. "
                  "Supported source is Python/JavaScript/TypeScript; functions and methods need implementation bodies.", flush=True)
        print(f"Search limits: {settings['max_results']} results, {settings['source_budget']} source characters, "
              f"{settings['token_budget']} compact tokens; dependency context {'on' if settings['include_dependencies'] else 'off'}.")
        configurations = write_mcp_config(artifacts, python, url,
                                          tokenizer_cache=ROOT / ".cache/quickstart/tiktoken")
        print(f"MCP configuration: {configurations['generic']}\nVS Code MCP configuration: {configurations['vscode']}")
        print("Copy the server entry into your MCP client. The service must stay running; :mcp prints the configuration.")
        print("Describe an implementation, :doctor for local diagnostics, or q to quit.", flush=True)
        if toy:
            for index, query in enumerate(QUERIES, 1):
                print(f"  {index}. {query}")
        while True:
            try:
                query = input("\nSearch> ").strip()
            except EOFError:
                break
            if query.lower() in ("q", "quit", "exit"):
                break
            if not query:
                continue
            if query.lower() == ":mcp":
                print(configurations['generic'].read_text())
                continue
            if query.lower() == ":doctor":
                print(json.dumps(diagnose(ROOT, repo, source_root), indent=2))
                continue
            if toy and query in ("1", "2", "3"):
                query = QUERIES[int(query) - 1]
            request_id = uuid.uuid4().hex
            try:
                value = request_json(url + "/search", {"query": query, **settings, "request_id": request_id}, request_timeout)
            except urllib.error.HTTPError as error:
                errors += 1
                try:
                    data = json.loads(error.read(65537))
                    detail = data.get("error", {})
                    message = detail.get("message", "Request rejected")
                except (ValueError, AttributeError, OSError, HTTPException):
                    message = "Request rejected"
                print(f"Search failed (HTTP {error.code}): {str(message)[:1000]}\nFix the source or query and search again; q closes the service.", flush=True)
                continue
            except (OSError, ValueError, HTTPException) as error:
                errors += 1
                try:
                    request_json(url + "/cancel", {"request_id": request_id}, min(request_timeout, 2))
                except (OSError, ValueError, HTTPException):
                    pass  # Best effort; own-process shutdown remains bounded on q/Ctrl-C.
                print(f"Search failed: {error}. Try again, or run :doctor. Service log: {log}", flush=True)
                continue
            searches += 1
            rows = value.get("results", [])
            if not rows:
                print("No eligible implementations found. Check saved/tracked source and the selected scope.")
            for row in rows:
                suffix = " (dependency context)" if row.get("origin") == "dependency" else ""
                if row.get("truncated"):
                    suffix += " (excerpt truncated)"
                print(f"\n{row['path']}:{row['start_line']}–{row['end_line']}  {row['symbol']}{suffix}\n{row['source']}")
            if value.get("budget_exhausted") or value.get("token_usage", {}).get("truncated"):
                print("Context limit reached. For more source, restart with a larger --source-budget or --token-budget.", flush=True)
    finally:
        try:
            with defer_cleanup_interrupts():
                shutdown = stop_owned(process) if process is not None else None
                (artifacts / "session.json").write_text(json.dumps({"repo": str(repo), "source_root": source_root,
                    "settings": settings, "startup_timeout": timeout, "request_timeout": request_timeout,
                    "source_exclusions": list(exclusions),
                    "startup_elapsed_s": startup_elapsed,
                    "windows": status.get("windows") if isinstance(status, dict) else None,
                    "searches": searches, "errors": errors, "shutdown": shutdown}, indent=2) + "\n")
        finally:
            signal.signal(signal.SIGTERM, previous)
    if shutdown != {"returncode": 0, "forced_kill": False}:
        raise RuntimeError(f"Service did not stop cleanly: {shutdown}. See {log}")
    print(f"Service closed. Local session log: {artifacts}", flush=True)
    print('To record feedback without source text: bash "Get Started.command" --feedback --output /path/to/new-feedback.json', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv-path", type=Path, required=True, help=argparse.SUPPRESS)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Run the toy HTTP + MCP checks and exit")
    modes.add_argument("--setup-only", action="store_true", help="Install runtime and weights without starting inference")
    parser.add_argument("--repo", type=Path, help="Search an existing Git repository instead of the fresh toy")
    parser.add_argument("--source-root", default="src", help="Repository-relative source directory (default: src)")
    parser.add_argument("--exclude-source", action="append", default=[], metavar="GLOB",
                        help="Exclude repository-relative source paths, such as generated/template files; repeat for more patterns")
    parser.add_argument("--startup-timeout", type=positive_seconds, default=1800,
                        help="Indexing and warmup allowance in seconds (default: 1800)")
    parser.add_argument("--request-timeout", type=positive_seconds, default=120,
                        help="Each status/search request timeout in seconds (default: 120)")
    parser.add_argument("--max-results", type=bounded_integer(1, 20), help="Own repo default: 8; toy default: 3")
    parser.add_argument("--source-budget", type=bounded_integer(1, 100000), help="Source characters; own repo default: 12000")
    parser.add_argument("--token-budget", type=bounded_integer(512, 32000), help="Compact context tokens (default: 4096)")
    parser.add_argument("--no-dependencies", action="store_true", help="Omit static dependency context")
    parser.add_argument("--port", type=bounded_integer(0, 65535), default=0,
                        help="Service port; default 0 chooses an available port. Choose a fixed port for stable MCP setup")
    args = parser.parse_args(argv)
    if any(not pattern.strip() or Path(pattern).is_absolute() or ".." in Path(pattern).parts for pattern in args.exclude_source):
        parser.error("--exclude-source requires nonempty repository-relative globs without '..'")
    args.exclude_source = sorted(set(args.exclude_source))
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
        try:
            checkout = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
                                      capture_output=True, text=True, timeout=10, check=False)
            if checkout.returncode != 0 or Path(checkout.stdout.strip()).resolve() != repo:
                parser.error("--repo must be a working Git checkout root; check the repository and Git configuration")
        except (OSError, subprocess.SubprocessError):
            parser.error("Cannot verify the Git checkout; check Git and the --repo directory")
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
            toy = repo is None
            if toy:
                repo = prepare(fresh_destination())
                source_root = "src"
            interactive(python, repo, source_root, work, timeout=args.startup_timeout,
                        request_timeout=args.request_timeout, settings=selected_settings(args, toy), toy=toy, port=args.port,
                        exclusions=args.exclude_source)
    except KeyboardInterrupt:
        parser.exit(130, "\nInterrupted; owned service cleanup completed. Rerun to resume setup.\n")
    except (OSError, ValueError, RuntimeError, HTTPException, subprocess.SubprocessError) as error:
        parser.exit(1, f"\nQuickstart stopped: {error}\nRerun the launcher to resume. Your existing .venv is unchanged.\n")
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    main()
