"""CPU-only validation-driver checks; no real backend, SDK or model requests."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def driver():
    spec = importlib.util.spec_from_file_location("verify_beta_mcp_under_test", ROOT / "scripts/verify_beta_mcp.py")
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]), patch.dict(sys.modules):
        spec.loader.exec_module(module)
        yield module


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def test_socket_guard_rejects_external_dns_and_both_connect_methods_without_destination_logging(driver, tmp_path):
    log = tmp_path / "network.jsonl"
    original = socket.getaddrinfo
    with driver.parent_guard(log):
        for action in (lambda: socket.getaddrinfo("PRIVATE_AUTH.example", 443),
                       lambda: socket.socket().connect(("198.51.100.2", 443)),
                       lambda: socket.socket().connect_ex(("198.51.100.2", 443))):
            with pytest.raises(PermissionError, match="nonloopback"):
                action()
    assert socket.getaddrinfo is original
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len([row for row in rows if row["kind"] == "blocked"]) == 3
    assert "PRIVATE_AUTH" not in log.read_text() and "198.51.100" not in log.read_text()


def test_socket_guard_allows_only_numeric_loopback_or_unix_connections_and_restores_methods(driver, tmp_path):
    log = tmp_path / "network.jsonl"
    with patch.object(socket.socket, "connect", return_value=None) as connect, \
            patch.object(socket.socket, "connect_ex", return_value=0) as connect_ex, \
            patch.object(socket, "getaddrinfo", return_value=[]) as resolve:
        with driver.parent_guard(log):
            with socket.socket() as tcp:
                tcp.connect(("127.0.0.1", 49152))
                assert tcp.connect_ex(("127.0.0.1", 49152)) == 0
            with socket.socket(socket.AF_UNIX) as local:
                local.connect("PRIVATE/local/socket")
            assert socket.getaddrinfo("::1", 49152) == []
        assert socket.getaddrinfo is resolve and socket.socket.connect is connect
    assert connect.call_count == 2 and connect_ex.call_count == 1
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len([row for row in rows if row["kind"] == "allowed"]) == 4
    assert "PRIVATE" not in log.read_text()


def test_adapter_sitecustomize_installs_and_exits_in_a_real_cpu_python_child(driver, tmp_path):
    log = tmp_path / "network.jsonl"
    write(tmp_path / "sitecustomize.py", driver.GUARD_SOURCE)
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "BETA_MCP_GUARD_LOG": str(log)}
    process = subprocess.run([sys.executable, "-c", "import socket\ntry: socket.getaddrinfo('PRIVATE.example',443)\nexcept PermissionError: pass\nelse: raise AssertionError('unguarded')"],
                             env=env, capture_output=True, timeout=10, check=False)
    assert process.returncode == 0
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [row["kind"] for row in rows] == ["installed", "blocked", "exit"]
    assert all(row["role"] == "adapter" for row in rows)
    assert "PRIVATE" not in log.read_text()


def config(tmp_path):
    python = tmp_path / "runtime with spaces/bin/python"
    write(python, "fixture interpreter")
    cache = tmp_path / "cache with spaces/tiktoken"
    cache.mkdir(parents=True)
    server = {"command": str(python), "args": ["-m", "semantic_search.mcp_server", "--server", "http://127.0.0.1:49152"],
              "env": {"TIKTOKEN_CACHE_DIR": str(cache)}}
    path = tmp_path / "mcp.json"
    write(path, json.dumps({"mcpServers": {"semantic-search": server}}))
    return path, server, cache


def test_driver_accepts_exact_generated_config_and_preserves_python_and_cache(driver, tmp_path):
    path, server, cache = config(tmp_path)
    assert driver.read_config(path) == (server, "http://127.0.0.1:49152", cache)


@pytest.mark.parametrize("kind", ["remote", "extra_arg", "provider_env", "missing_cache", "cache_symlink"])
def test_driver_rejects_changed_or_incomplete_configuration(driver, tmp_path, kind):
    path, server, cache = config(tmp_path)
    if kind == "remote":
        server["args"][-1] = "http://example.com:8750"
    elif kind == "extra_arg":
        server["args"].append("--default-rerank")
    elif kind == "provider_env":
        server["env"]["PRIVATE_TOKEN"] = "PRIVATE"
    elif kind == "missing_cache":
        cache.rmdir()
    elif kind == "cache_symlink":
        cache.rmdir()
        cache.symlink_to(cache.parent, target_is_directory=True)
    write(path, json.dumps({"mcpServers": {"semantic-search": server}}))
    with pytest.raises(ValueError):
        driver.read_config(path)


def envelope(repo):
    from semantic_search.context import INDEX_MEMBERSHIP
    source = "def behavior():\r\n    return 42\r\n"
    write(repo / "django/private.py", source)
    row = {"path": "django/private.py", "symbol": "behavior", "start_line": 1, "end_line": 2,
           "source": source, "source_hash": hashlib.sha256(source.encode()).hexdigest(),
           "origin": "retrieval", "truncated": False, "related_to": None}
    return {"query": "find behavior", "results": [row], "match_status": "candidates", "budget_exhausted": False,
            "scope": {"source_root": "django", "membership": INDEX_MEMBERSHIP, "source_exclusions": [], "exclusions_omitted": 0},
            "token_usage": {"encoding": "cl100k_base", "tokens": 100, "budget": 4096, "truncated": False}}


class Counter:
    def encode(self, text, *, disallowed_special):
        assert disallowed_special == ()
        return [0] * 100


def test_context_verifier_checks_saved_crlf_physical_source_hash_and_default_caps(driver, tmp_path):
    from semantic_search import context
    value = envelope(tmp_path)
    with patch.object(context, "encoding", return_value=Counter()):
        receipt, compact = driver.verify_context(value, "find behavior", tmp_path, "django")
    assert receipt["compact_tokens"] == 100 and receipt["source_characters"] == len(value["results"][0]["source"])
    assert receipt["verified_excerpts"][0]["path"] == "django/private.py"
    assert compact == context.serialize_context(value)


@pytest.mark.parametrize("kind", ["source", "hash", "tokens", "budget", "scope", "path", "lines", "result_limit", "source_limit"])
def test_context_verifier_rejects_false_evidence_counts_and_caps(driver, tmp_path, kind):
    from semantic_search import context
    value = envelope(tmp_path)
    row = value["results"][0]
    if kind == "source":
        row["source"] = "changed source\n"
    elif kind == "hash":
        row["source_hash"] = "0" * 64
    elif kind == "tokens":
        value["token_usage"]["tokens"] = 99
    elif kind == "budget":
        value["token_usage"]["budget"] = 2000
    elif kind == "scope":
        value["scope"]["source_root"] = "elsewhere"
    elif kind == "path":
        row["path"] = "../outside.py"
    elif kind == "lines":
        row["end_line"] = 3
    elif kind == "result_limit":
        value["results"] *= 9
    elif kind == "source_limit":
        row["source"] = "x" * 12001 + "\n"
        row["source_hash"] = hashlib.sha256(row["source"].encode()).hexdigest()
        row["start_line"] = row["end_line"] = 1
        write(tmp_path / row["path"], row["source"])
    with patch.object(context, "encoding", return_value=Counter()), pytest.raises(ValueError):
        driver.verify_context(value, "find behavior", tmp_path, "django")


def events():
    return [{"role": "parent", "pid": 1, "kind": "installed"},
            {"role": "parent", "pid": 1, "kind": "allowed", "operation": "connect", "classification": "loopback"},
            {"role": "adapter", "pid": 2, "kind": "installed"},
            {"role": "adapter", "pid": 2, "kind": "allowed", "operation": "connect", "classification": "loopback"},
            {"role": "adapter", "pid": 2, "kind": "exit"},
            {"role": "parent", "pid": 1, "kind": "restored"}]


def test_network_summary_requires_both_real_guards_requests_and_adapter_exit(driver, tmp_path):
    path = tmp_path / "network.jsonl"
    write(path, "\n".join(json.dumps(row) for row in events()))
    value = driver.guard_summary(path)
    assert value["outbound_attempts"] == 0 and value["allowed_operations"] == 2
    assert value["adapter_guard_installed"] and value["adapter_interpreter_exit_observed"]


@pytest.mark.parametrize("kind", ["missing_adapter", "missing_exit", "missing_parent", "no_requests", "blocked", "private_fields"])
def test_network_summary_refuses_incomplete_or_dirty_network_proofs(driver, tmp_path, kind):
    rows = events()
    if kind == "missing_adapter":
        rows = [row for row in rows if row["role"] != "adapter"]
    elif kind == "missing_exit":
        rows = [row for row in rows if row["kind"] != "exit"]
    elif kind == "missing_parent":
        rows = [row for row in rows if row["role"] != "parent"]
    elif kind == "no_requests":
        rows = [row for row in rows if row["kind"] != "allowed"]
    elif kind == "blocked":
        rows.append({"role": "adapter", "pid": 2, "kind": "blocked", "operation": "getaddrinfo", "classification": "nonloopback"})
    elif kind == "private_fields":
        rows[0]["auth"] = "PRIVATE"
    path = tmp_path / "network.jsonl"
    write(path, "\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError):
        driver.guard_summary(path)
