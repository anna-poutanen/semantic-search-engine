#!/usr/bin/env python3
"""Offline, allowlisted diagnostics and local MCP setup for the Mac beta.

Diagnostics never download, index, search, inspect source contents, or transmit
data. Generated MCP files deliberately contain a local executable path and are
configuration snippets, not shareable diagnostic records.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
WEIGHTS_SHA = "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"
MODEL_FILES = ("model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt")
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.]{1,32})?\Z")
SUPPORTED = frozenset((".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".tsx"))
JS_EXCLUDED = frozenset(("node_modules", "bower_components", "dist", "build", "coverage", ".next", ".nuxt", ".svelte-kit", "out"))
# Actions are fixed prose; no exception text or filesystem location is included.
ACTIONS = {
    "unsupported_machine": "Use an Apple Silicon Mac for the current beta.",
    "git_unavailable": "Run xcode-select --install, finish the Git installation, then retry.",
    "disk_space_low": "Free disk space before retrying setup; keep at least 6 GiB available.",
    "disk_unavailable": "Check that the extracted download is on an accessible local disk.",
    "runtime_missing": "Run Get Started.command to install the dedicated local runtime.",
    "runtime_unavailable": "Close the launcher, then rerun Get Started.command to check or repair the dedicated runtime.",
    "runtime_timeout": "The local runtime check timed out. Close other busy applications and retry diagnostics.",
    "runtime_version_mismatch": "Rerun Get Started.command to repair the pinned runtime versions.",
    "metal_unavailable": "Check that this is an Apple Silicon Mac, then rerun setup.",
    "runtime_inputs_invalid": "Extract a fresh complete beta download, then rerun setup.",
    "model_missing": "Run Get Started.command to resume or install the embedding checkpoint.",
    "model_invalid": "Run Get Started.command to verify and repair the embedding checkpoint.",
    "repository_unavailable": "Check the --repo directory and Git installation.",
    "source_root_invalid": "Set --source-root to a repository-relative directory inside --repo.",
    "source_root_missing": "Check that --source-root names an existing directory in the repository.",
    "repository_not_git": "Use a Git working tree containing saved, tracked implementation files.",
    "tracked_sources_missing": "Track supported implementation files under the selected source root, or choose another source root.",
}


def _digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _reserved_symlink(path, root):
    """Inspect managed directory ancestors, allowing the venv Python symlink."""
    for candidate in (path, *path.parents):
        if candidate == root:
            return False
        if candidate.is_symlink():
            return True
    return True


def _bounded_json(path, limit=65536):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected a regular metadata file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Metadata is too large")
    return json.loads(raw)


def _git_environment():
    result = dict(os.environ)
    # The diagnostic is read-only and does not need user/global configuration.
    result.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                  GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    # Avoid inherited redirection to another working tree/index/configuration.
    for name in tuple(result):
        if name.startswith("GIT_") and name not in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"):
            result.pop(name)
    return result


def _run(arguments, *, timeout=20, cwd=None, env=None):
    # Captured error text is deliberately never copied into the report.
    return subprocess.run([str(item) for item in arguments], capture_output=True,
                          check=False, timeout=timeout, cwd=cwd, env=env)


def _expected_packages(root):
    expected = {}
    for name in ("requirements-mac.lock", "requirements-languages.lock"):
        for line in (root / name).read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                package, version = line.strip().split("==")
                if not re.fullmatch(r"[a-z0-9-]+", package) or not VERSION.fullmatch(version):
                    raise ValueError("Invalid pinned runtime input")
                expected[package] = version
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if not isinstance(project, str) or not VERSION.fullmatch(project):
        raise ValueError("Invalid project version")
    expected["semantic-search-engine"] = project
    return expected


RUNTIME_PROBE = r'''import importlib.metadata as m, json, sys
from pathlib import Path
expected = json.loads(sys.argv[1])
packages = {}
for name in expected:
    try:
        packages[name] = m.version(name)
    except m.PackageNotFoundError:
        packages[name] = None
try:
    import mlx.core as mx
    metal = bool(mx.metal.is_available())
except Exception:
    metal = False
print(json.dumps({"python": ".".join(map(str, sys.version_info[:3])),
    "prefix_matches": Path(sys.prefix).resolve() == Path(sys.argv[2]).resolve(),
    "metal": metal, "packages": packages}))
'''


def runtime_summary(root):
    environment = root / ".cache/quickstart/venv"
    python = environment / "bin/python"
    result = {"state": "missing", "python": None, "python_312": False,
              "dedicated_environment": False, "metal": False, "packages": {}, "versions_match": False}
    if (_reserved_symlink(python.parent, root) or not python.is_file()
            or not (environment / "pyvenv.cfg").is_file()
            or (environment / "pyvenv.cfg").is_symlink()):
        return result, "runtime_missing"
    try:
        expected = _expected_packages(root)
    except (OSError, ValueError, KeyError, TypeError):
        result["state"] = "inputs_invalid"
        return result, "runtime_inputs_invalid"
    try:
        process = _run((python, "-I", "-c", RUNTIME_PROBE, json.dumps(expected), environment), timeout=20, cwd=root)
        if process.returncode or len(process.stdout) > 65536:
            raise ValueError("Runtime probe failed")
        observed = json.loads(process.stdout)
        if not isinstance(observed, dict) or not isinstance(observed.get("packages"), dict):
            raise ValueError("Runtime probe shape is invalid")
        version = observed.get("python")
        result["python"] = version if isinstance(version, str) and VERSION.fullmatch(version) else None
        result["python_312"] = bool(result["python"] and result["python"].startswith("3.12."))
        result["dedicated_environment"] = observed.get("prefix_matches") is True
        result["metal"] = observed.get("metal") is True
        for name, pin in expected.items():
            actual = observed["packages"].get(name)
            safe = actual if isinstance(actual, str) and VERSION.fullmatch(actual) else None
            result["packages"][name] = {"expected": pin, "installed": safe, "matches": safe == pin}
        result["versions_match"] = all(item["matches"] for item in result["packages"].values())
    except subprocess.TimeoutExpired:
        result["state"] = "timeout"
        return result, "runtime_timeout"
    except (OSError, ValueError, TypeError, RecursionError):
        result["state"] = "unavailable"
        return result, "runtime_unavailable"
    if not result["python_312"] or not result["dedicated_environment"] or not result["versions_match"]:
        result["state"] = "version_mismatch"
        return result, "runtime_version_mismatch"
    if not result["metal"]:
        result["state"] = "metal_unavailable"
        return result, "metal_unavailable"
    result["state"] = "checked"
    return result, None


def model_summary(root, *, verify_weights=True):
    directory = root / ".cache/models/qwen3-embedding-0.6b"
    result = {"id": MODEL_ID, "revision": MODEL_REVISION, "state": "missing",
              "files_complete": False, "config_matches": False, "provenance_matches": False,
              "weights_sha256": "not_checked"}
    files = [directory / name for name in MODEL_FILES]
    if _reserved_symlink(directory, root) or not all(path.is_file() and not path.is_symlink() for path in files):
        return result, "model_missing"
    result["files_complete"] = True
    try:
        config = _bounded_json(directory / "config.json")
        result["config_matches"] = isinstance(config, dict) and config.get("model_type") == "qwen3" and config.get("hidden_size") == 1024 and not config.get("quantization") and not config.get("quantization_config")
        metadata = directory / ".cache/huggingface/download/model.safetensors.metadata"
        if _reserved_symlink(metadata.parent, root) or metadata.is_symlink() or not metadata.is_file():
            raise ValueError("Checkpoint metadata missing")
        with metadata.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("Checkpoint metadata is too large")
        rows = raw.decode("utf-8").splitlines()
        marker = _bounded_json(directory / "semantic_search_checkpoint.json")
        result["provenance_matches"] = (rows[:2] == [MODEL_REVISION, WEIGHTS_SHA]
            and isinstance(marker, dict) and marker.get("id") == MODEL_ID
            and marker.get("revision") == MODEL_REVISION and marker.get("weights_etag") == WEIGHTS_SHA
            and marker.get("quantized") is False)
        if verify_weights:
            result["weights_sha256"] = "matches" if _digest(directory / "model.safetensors") == WEIGHTS_SHA else "mismatch"
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        result["state"] = "invalid"
        return result, "model_invalid"
    if not result["config_matches"] or not result["provenance_matches"] or result["weights_sha256"] == "mismatch":
        result["state"] = "invalid"
        return result, "model_invalid"
    result["state"] = "checked" if verify_weights else "metadata_checked"
    return result, None


def _source_eligible(path):
    return path.suffix in SUPPORTED and (path.suffix == ".py" or (not str(path).endswith((".d.ts", ".d.mts", ".d.cts")) and not JS_EXCLUDED.intersection(path.parts[:-1])))


def repository_summary(repo, source_root, git):
    result = {"state": "not_checked", "git_working_tree": False, "source_root_exists": False,
              "tracked_files_in_scope": 0, "supported_tracked_files": 0,
              "counts_describe": "saved tracked file membership, not parsed implementations"}
    try:
        repo = Path(repo).resolve()
        relative = PurePosixPath(source_root)
        if (not isinstance(source_root, str) or not source_root.strip() or relative.is_absolute()
                or ".." in relative.parts or any(ord(char) < 32 or ord(char) == 127 for char in source_root)):
            raise ValueError
        source = (repo / source_root).resolve()
        if not source.is_relative_to(repo):
            raise ValueError
    except (OSError, ValueError, TypeError):
        result["state"] = "invalid_source_root"
        return result, "source_root_invalid"
    if not repo.is_dir() or git is None:
        result["state"] = "unavailable"
        return result, "repository_unavailable"
    result["source_root_exists"] = source.is_dir()
    if not result["source_root_exists"]:
        result["state"] = "source_root_missing"
        return result, "source_root_missing"
    arguments = [git, "--literal-pathspecs", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-C", repo]
    try:
        check = _run([*arguments, "rev-parse", "--is-inside-work-tree"], timeout=10, env=_git_environment())
        if check.returncode or check.stdout.strip() != b"true":
            result["state"] = "not_git"
            return result, "repository_not_git"
        result["git_working_tree"] = True
        listing = _run([*arguments, "ls-files", "-z", "--", source.relative_to(repo).as_posix()], timeout=10, env=_git_environment())
        if listing.returncode or len(listing.stdout) > 16 * 1024 * 1024:
            raise ValueError("Tracked file listing unavailable")
        for name in listing.stdout.split(b"\0"):
            if not name:
                continue
            path = Path(name.decode("utf-8", "surrogateescape"))
            if path.is_absolute() or ".." in path.parts:
                continue
            actual = repo / path
            if not actual.is_file() or not actual.resolve().is_relative_to(source):
                continue
            result["tracked_files_in_scope"] += 1
            if _source_eligible(PurePosixPath(path.as_posix())):
                result["supported_tracked_files"] += 1
    except (OSError, ValueError, subprocess.TimeoutExpired):
        result["state"] = "unavailable"
        return result, "repository_unavailable"
    result["state"] = "checked" if result["supported_tracked_files"] else "no_tracked_sources"
    return result, None if result["supported_tracked_files"] else "tracked_sources_missing"


def diagnose(root, repo=None, source_root="src", *, verify_weights=True):
    """Return fixed enums, booleans, counts and versions, with no private paths."""
    root = Path(root)
    system = platform.system()
    machine = platform.machine()
    release = platform.mac_ver()[0] if system == "Darwin" else platform.release()
    try:
        memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        if type(memory) is not int or memory <= 0:
            memory = None
    except (OSError, ValueError, AttributeError):
        memory = None
    cores = os.cpu_count()
    report = {"schema_version": 1, "offline": True, "machine": {
        "os": system if system in ("Darwin", "Linux", "Windows") else "other",
        "os_version": release if re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", release) else None,
        "architecture": machine if machine in ("arm64", "aarch64", "x86_64", "AMD64") else "other",
        "memory_bytes": memory, "logical_cpu_count": cores if type(cores) is int and cores > 0 else None,
        "supported": system == "Darwin" and machine == "arm64"}, "issues": []}
    issues = []
    if not report["machine"]["supported"]:
        issues.append("unsupported_machine")
    try:
        free = shutil.disk_usage(root).free
        report["disk"] = {"state": "checked", "free_bytes": free, "recommended_free_bytes": 6 * 1024**3}
        if free < 6 * 1024**3:
            issues.append("disk_space_low")
    except OSError:
        report["disk"] = {"state": "unavailable", "free_bytes": None, "recommended_free_bytes": 6 * 1024**3}
        issues.append("disk_unavailable")
    git = shutil.which("git")
    report["git"] = {"working": False, "version": None}
    if git is not None:
        try:
            process = _run((git, "--version"), timeout=10, env=_git_environment())
            match = re.match(rb"git version ([0-9]+(?:\.[0-9]+){1,3})(?:[ .]|$)", process.stdout)
            if not process.returncode and match:
                report["git"] = {"working": True, "version": match[1].decode("ascii")}
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not report["git"]["working"]:
        issues.append("git_unavailable")
        git = None
    report["runtime"], issue = runtime_summary(root)
    if issue:
        issues.append(issue)
    report["model"], issue = model_summary(root, verify_weights=verify_weights)
    if issue:
        issues.append(issue)
    report["repository"] = None
    if repo is not None:
        report["repository"], issue = repository_summary(repo, source_root, git)
        if issue:
            issues.append(issue)
    report["issues"] = [{"code": name, "action": ACTIONS[name]} for name in dict.fromkeys(issues)]
    report["checks_passed"] = not report["issues"]
    return report


def loopback_url(value):
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
        if (parsed.scheme != "http" or not address.is_loopback or port is None or not 1 <= port <= 65535
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError
        host = f"[{address}]" if address.version == 6 else str(address)
        return f"http://{host}:{port}"
    except (ValueError, TypeError) as error:
        raise ValueError("MCP requires the current numeric loopback HTTP URL, including its port") from error


def write_mcp_config(directory, python, url, *, tokenizer_cache=None):
    """Exclusively create two local snippets; never write client settings.

    Call only with an existing owned session directory and its live service URL.
    The interpreter path is kept as supplied: resolving a virtualenv's Python
    symlink would bypass its dedicated environment.
    """
    directory, python = Path(directory), Path(python)
    if not directory.is_dir() or any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError("MCP output requires an existing session directory without symlinks")
    if not python.is_absolute() or not python.is_file() or any(ord(char) < 32 or ord(char) == 127 for char in str(python)):
        raise ValueError("MCP requires the absolute dedicated Python executable")
    server = {"command": str(python), "args": ["-m", "semantic_search.mcp_server", "--server", loopback_url(url)]}
    if tokenizer_cache is not None:
        tokenizer_cache = Path(tokenizer_cache)
        if (not tokenizer_cache.is_absolute() or not tokenizer_cache.is_dir()
                or any(path.is_symlink() for path in (tokenizer_cache, *tokenizer_cache.parents))
                or any(ord(char) < 32 or ord(char) == 127 for char in str(tokenizer_cache))):
            raise ValueError("MCP tokenizer cache must be an absolute existing directory without symlinks")
        server["env"] = {"TIKTOKEN_CACHE_DIR": str(tokenizer_cache)}
    payloads = {"generic": (directory / "mcp.json", {"mcpServers": {"semantic-search": server}}),
                "vscode": (directory / "vscode-mcp.json", {"servers": {"semantic-search": {"type": "stdio", **server}}})}
    for path, _ in payloads.values():
        if path.exists() or path.is_symlink():
            raise FileExistsError("MCP snippet already exists; choose a fresh session directory")
    created = []
    try:
        # Reserve both names before writing. O_EXCL also refuses broken symlinks.
        for path, payload in payloads.values():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            identity = os.fstat(descriptor)
            created.append((path, descriptor, identity.st_dev, identity.st_ino))
        for (_, payload), (_, descriptor, _, _) in zip(payloads.values(), created):
            with os.fdopen(os.dup(descriptor), "w", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except BaseException:
        for path, _, device, inode in created:
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (device, inode):
                    path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        for _, descriptor, _, _ in created:
            os.close(descriptor)
    return {name: path for name, (path, _) in payloads.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doctor", action="store_true", required=True, help="Check local prerequisites without setup, networking, indexing or inference")
    parser.add_argument("--repo", type=Path, help="Optionally count saved, tracked supported files; file names and contents are not recorded")
    parser.add_argument("--source-root", default="src")
    parser.add_argument("--skip-weight-check", action="store_true", help="Check model metadata only; skip reading and hashing the 1.1 GiB weight file")
    args = parser.parse_args(argv)
    report = diagnose(ROOT, args.repo, args.source_root, verify_weights=not args.skip_weight_check)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    return 0 if report["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
