#!/usr/bin/env python3
"""Write an allowlisted local beta feedback record; offline unless --server is explicit."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import ipaddress
import json
import math
from pathlib import Path
import platform
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PUBLIC_PROJECTS = ("flask", "requests", "click", "starlette", "rich", "express", "zod", "koa", "httpx", "werkzeug")
PACKAGES = ("semantic-search-engine", "mcp", "tiktoken", "sqlite-vec")
WORKFLOWS = ("setup", "saved_search", "dirty_search", "stale_navigation", "saved_refresh", "service_restart")
NOTES = ("setup_failed", "partial_implementation", "no_relevant_result", "unexpected_stale_result",
         "dirty_buffer_mismatch", "refresh_failed", "navigation_blocked", "service_unavailable", "timeout")
MAX_STATUS_BYTES = 65536
VERSION = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][A-Za-z0-9.]{1,32})?\Z")


def count(value):
    try:
        number = int(value)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("Count must be an integer from 0 to 100000") from error
    if not 0 <= number <= 100000:
        raise argparse.ArgumentTypeError("Count must be an integer from 0 to 100000")
    return number


def revision(value):
    if not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise argparse.ArgumentTypeError("Public revision must be a full 40-character Git commit hash")
    return value.lower()


def version(value):
    if not VERSION.fullmatch(value):
        raise argparse.ArgumentTypeError("Version must have a numeric major.minor[.patch] format")
    return value


def loopback_server(value):
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port if parsed.port is not None else 80
        if (parsed.scheme != "http" or not address.is_loopback or not 1 <= port <= 65535
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)):
            raise ValueError
        host = f"[{address}]" if address.version == 6 else str(address)
        return f"http://{host}:{port}"
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("Server must be a numeric loopback HTTP base URL without credentials or extra URL fields") from error


def timeout(value):
    try:
        number = float(value)
        if not math.isfinite(number) or not .1 <= number <= 10:
            raise ValueError
        return number
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("Status timeout must be between 0.1 and 10 seconds") from error


def environment_summary():
    system = platform.system()
    os_name = system if system in ("Darwin", "Linux", "Windows") else "other"
    release = platform.mac_ver()[0] if system == "Darwin" else platform.release()
    os_version = release if re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", release) else None
    architecture = platform.machine()
    if architecture not in ("arm64", "aarch64", "x86_64", "AMD64", "i386", "i686"):
        architecture = "other"
    packages = {}
    for name in PACKAGES:
        try:
            observed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            observed = None
        packages[name] = observed if isinstance(observed, str) and VERSION.fullmatch(observed) else None
    return {"os": os_name, "os_version": os_version, "architecture": architecture,
            "python": platform.python_version(), "packages": packages}


def valid_count(value):
    return type(value) is int and 0 <= value <= 2**31 - 1


def status_summary(value):
    """Only documented booleans/counts survive; no string from /status is copied."""
    if (not isinstance(value, dict) or type(value.get("ready")) is not bool
            or type(value.get("closed")) is not bool or not valid_count(value.get("windows"))):
        raise ValueError("Invalid status summary")
    result = {key: value[key] for key in ("ready", "closed", "windows")}
    for key in ("embedding_initialized", "dense_ready"):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    watcher = value.get("watcher")
    if isinstance(watcher, dict):
        result["watcher"] = {key: watcher[key] for key in ("running", "pending") if type(watcher.get(key)) is bool}
        if valid_count(watcher.get("refresh_count")):
            result["watcher"]["refresh_count"] = watcher["refresh_count"]
    # Current backend status does not advertise reranker availability. If a
    # future compatible status does, retain only these known names/booleans.
    providers = value.get("providers")
    if isinstance(providers, dict):
        summary = {}
        for name in ("embedding", "h2o", "qwen"):
            item = providers.get(name)
            if isinstance(item, dict):
                safe = {key: item[key] for key in ("enabled", "ready") if type(item.get(key)) is bool}
                if safe:
                    summary[name] = safe
        if summary:
            result["providers"] = summary
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate status field")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("Nonfinite status JSON")


def collect_status(server, *, seconds=3.0):
    # Also validate direct callers. No proxy, redirect, authentication or search.
    server = loopback_server(server)
    request = Request(server + "/status", headers={"Accept": "application/json"})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=seconds) as response:
            raw = response.read(MAX_STATUS_BYTES + 1)
        if len(raw) > MAX_STATUS_BYTES:
            return {"collection": "response_too_large"}
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_pairs, parse_constant=reject_constant)
        return {"collection": "collected", "summary": status_summary(value)}
    except HTTPError as error:
        result = {"collection": "http_error"}
        if type(error.code) is int and 100 <= error.code <= 599:
            result["http_status"] = error.code
        return result
    except (URLError, TimeoutError, OSError):
        return {"collection": "connection_failed"}
    except (ValueError, UnicodeError, RecursionError):
        return {"collection": "invalid_response"}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path, required=True, help="New local JSON file in an existing directory")
    result.add_argument("--public-project", choices=PUBLIC_PROJECTS)
    result.add_argument("--revision", type=revision, help="Public project's full Git commit; supplied manually")
    result.add_argument("--interface", choices=("cli", "mcp", "vscode"), default="cli")
    result.add_argument("--workflow", choices=WORKFLOWS, default="saved_search")
    result.add_argument("--reranker", choices=("off", "h2o", "qwen"), default="off", help="Manually selected mode, not inferred from status")
    result.add_argument("--attempts", type=count, default=0)
    result.add_argument("--full", type=count, default=0, help="Attempts where all required implementation evidence was actually delivered")
    result.add_argument("--partial", type=count, default=0)
    result.add_argument("--errors", type=count, default=0)
    result.add_argument("--task-outcome", choices=("completed", "not_completed", "not_attempted"), default="not_attempted")
    result.add_argument("--note", choices=NOTES, action="append", default=[], help="Fixed issue category; arbitrary text is not collected")
    result.add_argument("--editor-version", type=version)
    result.add_argument("--server", type=loopback_server, help="Explicit opt-in to one bounded GET /status on numeric loopback")
    result.add_argument("--status-timeout", type=timeout, default=3.0)
    return result


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.revision is not None and args.public_project is None:
        arguments.error("--revision requires an allowlisted --public-project")
    if args.full + args.partial > args.attempts or args.errors > args.attempts:
        arguments.error("Full plus partial counts, and error count, cannot exceed attempts")
    if len(args.note) > 8:
        arguments.error("At most eight fixed note categories are allowed")
    if args.output.exists() or args.output.is_symlink():
        arguments.error("Output already exists; choose a new local feedback file")
    record = {"schema_version": 1, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": environment_summary(),
        "public_project": {"name": args.public_project, "revision": args.revision} if args.public_project else None,
        "session": {"interface": args.interface, "workflow": args.workflow, "reranker": args.reranker,
                    "editor_version": args.editor_version, "task_outcome": args.task_outcome},
        "counts": {"attempts": args.attempts, "full_implementation": args.full, "partial_implementation": args.partial, "operational_errors": args.errors},
        "notes": sorted(set(args.note)), "local_status": {"collection": "not_requested"}}
    if args.server is not None:
        record["local_status"] = collect_status(args.server, seconds=args.status_timeout)
    try:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    except OSError:
        arguments.error("Cannot create output; choose a new file in an existing writable directory")
    print("Created local feedback JSON. Review it before any manual sharing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
