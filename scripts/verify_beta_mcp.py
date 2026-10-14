#!/usr/bin/env python3
"""Qualify a generated beta MCP snippet against an already-running service.

This validation driver is not included in the consumer download. It exercises
the real pinned SDK and adapter; it never starts, stops or mutates the search
service. Run with the qualified extracted runtime and a new receipt filename.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import signal
import socket
import sys
import tempfile
import time
import urllib.request

from beta_tools import loopback_url

DEFAULTS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096, "include_dependencies": True}
MAX_BYTES = 1024 * 1024

# Installed through PYTHONPATH in the real adapter, and explicitly in the parent
# before SDK imports. Do not record addresses, hostnames, environment or payloads.
# This guards Python socket DNS/connect calls, not unrelated native processes.
GUARD_SOURCE = r'''import atexit, ipaddress, json, os, socket

def install(log, role):
    originals = {"connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex,
                 "getaddrinfo": socket.getaddrinfo}
    def record(kind, operation=None, classification=None):
        value = {"role": role, "pid": os.getpid(), "kind": kind}
        if operation is not None:
            value["operation"] = operation
        if classification is not None:
            value["classification"] = classification
        descriptor = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, (json.dumps(value, separators=(",", ":")) + "\n").encode())
        finally:
            os.close(descriptor)
    def loopback(host):
        try:
            if isinstance(host, bytes):
                host = host.decode("ascii")
            return ipaddress.ip_address(host).is_loopback
        except (ValueError, TypeError, UnicodeError):
            return False
    def connection(original, operation):
        def guarded(self, address):
            if self.family == socket.AF_UNIX:
                record("allowed", operation, "unix")
            elif self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and loopback(address[0]):
                record("allowed", operation, "loopback")
            else:
                record("blocked", operation, "nonloopback")
                raise PermissionError("Beta qualification blocks nonloopback network connections")
            return original(self, address)
        return guarded
    def guarded_dns(host, *args, **kwargs):
        if not loopback(host):
            record("blocked", "getaddrinfo", "nonloopback")
            raise PermissionError("Beta qualification blocks nonloopback DNS resolution")
        record("allowed", "getaddrinfo", "loopback")
        return originals["getaddrinfo"](host, *args, **kwargs)
    socket.socket.connect = connection(originals["connect"], "connect")
    socket.socket.connect_ex = connection(originals["connect_ex"], "connect_ex")
    socket.getaddrinfo = guarded_dns
    record("installed")
    def exit_record():
        record("exit")
    if role == "adapter":
        atexit.register(exit_record)
    def restore():
        socket.socket.connect = originals["connect"]
        socket.socket.connect_ex = originals["connect_ex"]
        socket.getaddrinfo = originals["getaddrinfo"]
        record("restored")
    return restore

if __name__ == "sitecustomize":
    try:
        install(os.environ["BETA_MCP_GUARD_LOG"], "adapter")
    except BaseException:
        # Python normally continues after an unsuccessful sitecustomize import.
        # Qualification must fail closed if the socket guard cannot be installed.
        os._exit(78)
'''


@contextmanager
def parent_guard(log):
    namespace = {"__name__": "beta_parent_guard"}
    exec(compile(GUARD_SOURCE, "beta_socket_guard", "exec"), namespace)
    restore = namespace["install"](str(log), "parent")
    try:
        yield
    finally:
        restore()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_config(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Expected a regular, bounded generated MCP configuration")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or set(value) != {"mcpServers"} or not isinstance(value["mcpServers"], dict) or set(value["mcpServers"]) != {"semantic-search"}:
        raise ValueError("Expected the generated portable semantic-search snippet")
    server = value["mcpServers"]["semantic-search"]
    if not isinstance(server, dict) or set(server) != {"command", "args", "env"}:
        raise ValueError("Expected generated command, arguments and warmed cache environment")
    python = Path(server["command"])
    args = server["args"]
    if not python.is_absolute() or not python.is_file() or not isinstance(args, list) or len(args) != 4 or args[:3] != ["-m", "semantic_search.mcp_server", "--server"]:
        raise ValueError("Configuration is not the generated dedicated adapter invocation")
    url = loopback_url(args[3])
    if url != args[3]:
        raise ValueError("Expected the canonical live loopback URL")
    env = server["env"]
    if not isinstance(env, dict) or set(env) != {"TIKTOKEN_CACHE_DIR"}:
        raise ValueError("Expected only the generated warmed tokenizer cache environment")
    cache = Path(env["TIKTOKEN_CACHE_DIR"])
    if not cache.is_absolute() or not cache.is_dir() or any(item.is_symlink() for item in (cache, *cache.parents)):
        raise ValueError("Expected an existing absolute tokenizer cache without symlinks")
    return server, url, cache


def service_status(url, repo, source_root):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(urllib.request.Request(url + "/status", headers={"Accept": "application/json"}), timeout=10) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("Service status is too large")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("ready") is not True or value.get("source_root") != source_root or not isinstance(value.get("repo"), str) or Path(value["repo"]).resolve() != repo:
        raise ValueError("Service identity does not match the qualification repository and source root")
    return {"ready": True, "repo_matches": True, "source_root": source_root,
            "windows": value.get("windows") if type(value.get("windows")) is int else None}


def verify_context(value, query, repo, source_root):
    """Compare every delivered excerpt with saved physical bytes and all caps."""
    from semantic_search.context import encoding, serialize_context, INDEX_MEMBERSHIP
    from semantic_search.languages import decode_source
    from semantic_search.source import physical_lines

    if not isinstance(value, dict) or value.get("query") != query or not isinstance(value.get("results"), list) or not value["results"]:
        raise ValueError("MCP returned no request-matching implementation evidence")
    scope = value.get("scope", {})
    if scope.get("source_root") != source_root or scope.get("membership") != INDEX_MEMBERSHIP or len(value["results"]) > DEFAULTS["max_results"]:
        raise ValueError("MCP evidence does not respect source scope or default result cap")
    text = serialize_context(value)
    usage = value.get("token_usage", {})
    actual_tokens = len(encoding().encode(text, disallowed_special=()))
    if usage.get("encoding") != "cl100k_base" or usage.get("budget") != DEFAULTS["token_budget"] or type(usage.get("tokens")) is not int or usage["tokens"] != actual_tokens or actual_tokens > DEFAULTS["token_budget"]:
        raise ValueError("Exact MCP compact token count or default budget differs")
    total = 0
    root = (repo / source_root).resolve()
    verified = []
    for row in value["results"]:
        path = PurePosixPath(row.get("path", ""))
        start, end, source = row.get("start_line"), row.get("end_line"), row.get("source")
        if path.is_absolute() or ".." in path.parts or not path.is_relative_to(PurePosixPath(source_root)) or type(start) is not int or type(end) is not int or not isinstance(source, str):
            raise ValueError("MCP excerpt path or line metadata is invalid")
        actual = repo / path
        if not actual.is_file() or not actual.resolve().is_relative_to(root):
            raise ValueError("MCP excerpt does not identify saved source in scope")
        physical = physical_lines(decode_source(path, actual.read_bytes()))
        if not 1 <= start <= end <= len(physical) or source != "".join(physical[start - 1:end]):
            raise ValueError("MCP excerpt differs from saved physical source lines")
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if row.get("source_hash") != digest or row.get("origin") not in ("retrieval", "dependency"):
            raise ValueError("MCP excerpt source hash or origin is invalid")
        if row["origin"] == "dependency" and not row.get("related_to"):
            raise ValueError("MCP dependency lacks an anchor")
        total += len(source)
        verified.append({key: row[key] for key in ("path", "symbol", "start_line", "end_line", "source_hash", "origin")})
    if total > DEFAULTS["source_budget"]:
        raise ValueError("MCP default source character budget was exceeded")
    return {"query": query, "verified_excerpts": verified, "source_characters": total,
            "compact_tokens": actual_tokens, "envelope": value}, text


async def exercise_mcp(server, child_environment, repo, source_root, queries):
    from mcp import Client, StdioServerParameters

    parameters = StdioServerParameters(command=server["command"], args=server["args"], env=child_environment)
    calls, searches = [], []
    async with Client(parameters, mode="legacy", read_timeout_seconds=120) as client:
        tools = (await client.list_tools()).tools
        calls.append({"method": "tools/list", "tools": [tool.name for tool in tools]})
        if [tool.name for tool in tools] != ["search_code"]:
            raise ValueError("Generated adapter exposed unexpected MCP tools")
        properties = tools[0].input_schema.get("properties", {})
        for name, expected in DEFAULTS.items():
            observed = properties.get(name, {}).get("default")
            if type(observed) is not type(expected) or observed != expected:
                raise ValueError("Advertised MCP evidence defaults differ")
        if properties.get("rerank", {}).get("default") is not False:
            raise ValueError("Generated MCP adapter enabled reranking")
        for query in queries:
            before = time.perf_counter()
            result = await client.call_tool("search_code", {"query": query})
            calls.append({"method": "tools/call", "name": "search_code", "arguments": {"query": query}})
            if result.is_error or not isinstance(result.structured_content, dict):
                raise ValueError("Generated MCP adapter search failed")
            verified, text = verify_context(result.structured_content, query, repo, source_root)
            if len(result.content) != 1 or result.content[0].type != "text" or result.content[0].text != text:
                raise ValueError("MCP compact text and structured content differ")
            verified["elapsed_seconds"] = time.perf_counter() - before
            searches.append(verified)
        protocol = client.protocol_version
    return {"sdk_mode": "legacy", "protocol_version": protocol, "calls": calls,
            "defaults": DEFAULTS, "searches": searches, "sdk_context_closed": True}


def run_mcp(server, environment, repo, source_root, queries):
    import anyio

    async def guarded():
        interrupted = False
        with anyio.fail_after(120 * (len(queries) + 1)) as scope:
            def cancel(signum, frame):
                nonlocal interrupted
                interrupted = True
                scope.cancel()
            previous = {signum: signal.signal(signum, cancel) for signum in (signal.SIGINT, signal.SIGTERM)}
            try:
                result = await exercise_mcp(server, environment, repo, source_root, queries)
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
        if interrupted:
            raise KeyboardInterrupt
        return result

    return anyio.run(guarded)


def guard_summary(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        if (not isinstance(row, dict) or set(row) - {"role", "pid", "kind", "operation", "classification"}
                or row.get("role") not in ("parent", "adapter") or type(row.get("pid")) is not int
                or row.get("kind") not in ("installed", "allowed", "blocked", "exit", "restored")
                or row.get("operation") not in (None, "connect", "connect_ex", "getaddrinfo")
                or row.get("classification") not in (None, "loopback", "unix", "nonloopback")):
            raise ValueError("Network guard emitted an unexpected record")
    adapters = {row["pid"] for row in rows if row["role"] == "adapter" and row["kind"] == "installed"}
    exited = {row["pid"] for row in rows if row["role"] == "adapter" and row["kind"] == "exit"}
    blocked = sum(row["kind"] == "blocked" for row in rows)
    parents = {row["pid"] for row in rows if row["role"] == "parent" and row["kind"] == "installed"}
    restored = {row["pid"] for row in rows if row["role"] == "parent" and row["kind"] == "restored"}
    connected = {row["role"] for row in rows if row["kind"] == "allowed" and row.get("operation") == "connect" and row.get("classification") == "loopback"}
    if (len(adapters) != 1 or not adapters.issubset(exited) or blocked
            or len(parents) != 1 or not parents.issubset(restored) or connected != {"parent", "adapter"}):
        raise ValueError("Adapter guard installation, normal interpreter exit or offline check failed")
    return {"scope": "Python socket DNS/connect in qualification parent and MCP adapter; resident backend is separate",
            "outbound_attempts": blocked, "adapter_guard_installed": True, "adapter_interpreter_exit_observed": True,
            "allowed_operations": sum(row["kind"] == "allowed" for row in rows), "events": rows}


def qualify(config, repo, source_root, queries):
    config_digest = hashlib.sha256(Path(config).read_bytes()).hexdigest()
    server, url, cache = read_config(config)
    repo = Path(repo).resolve()
    relative = PurePosixPath(source_root)
    if relative.is_absolute() or ".." in relative.parts or not (repo / source_root).resolve().is_relative_to(repo):
        raise ValueError("Expected a repository-relative source root")
    package = importlib.util.find_spec("semantic_search")
    if package is None or package.origin is None:
        raise ValueError("Run this driver with the qualified extracted runtime")
    default_cache = Path(package.origin).resolve().parents[2] / ".cache/tokenizers"
    if default_cache.exists() or default_cache.is_symlink():
        raise ValueError("Default app tokenizer directory already exists; use a fresh qualified extraction")
    previous = os.environ.pop("TIKTOKEN_CACHE_DIR", None)
    began = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="semantic-beta-mcp-guard-") as temporary:
            directory = Path(temporary)
            log = directory / "network.jsonl"
            (directory / "sitecustomize.py").write_text(GUARD_SOURCE)
            child = {**server["env"], "PYTHONPATH": str(directory), "BETA_MCP_GUARD_LOG": str(log)}
            # Both exact token counting and the adapter use only the generated
            # warmed cache, even when the calling terminal has another setting.
            os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
            with parent_guard(log):
                status = service_status(url, repo, source_root)
                result = run_mcp(server, child, repo, source_root, queries)
            network = guard_summary(log)
            if default_cache.exists() or default_cache.is_symlink():
                raise ValueError("Qualification created the app default tokenizer directory")
    finally:
        os.environ.pop("TIKTOKEN_CACHE_DIR", None)
        if previous is not None:
            os.environ["TIKTOKEN_CACHE_DIR"] = previous
    if hashlib.sha256(Path(config).read_bytes()).hexdigest() != config_digest:
        raise ValueError("Generated MCP configuration changed during qualification")
    return {"kind": "generated beta MCP integration qualification; not a retrieval benchmark",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.perf_counter() - began,
            "generated_config_sha256": config_digest,
            "configuration": {"exact_generated_command_and_args": True, "warmed_cache_from_generated_environment": True,
                "parent_cache_setting_removed": True, "default_tokenizer_directory_absent_before": True,
                "default_tokenizer_directory_absent_after": True, "client_settings_unchanged": True},
            "packages": {name: importlib.metadata.version(name) for name in ("semantic-search-engine", "mcp", "tiktoken")},
            "service": status, "network_guard": network, "mcp": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--query", action="append", required=True, help="Implementation behavior; repeat for multiple queries")
    parser.add_argument("--output", type=Path, required=True, help="New JSON receipt in an existing directory")
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir():
        parser.error("Choose a new receipt filename in an existing directory")
    if not 1 <= len(args.query) <= 8 or any(not query.strip() or len(query) > 8192 for query in args.query):
        parser.error("Supply one to eight nonempty queries of at most 8192 characters")
    result = qualify(args.config, args.repo, args.source_root, args.query)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    print("Generated MCP snippet qualified: physical excerpts, exact budgets, warmed cache and zero outbound attempts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
