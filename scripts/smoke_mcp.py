#!/usr/bin/env python3
"""Live Qwen/MCP/watch integration on three saved toy implementations.

This launches model inference. Run separately from quality/latency experiments.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time

from mcp import Client, StdioServerParameters

from semantic_search.client import SearchClient
from semantic_search.context import encoding, serialize_context
from semantic_search.source import physical_lines

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '''import json

def normalize_path(path):
    return path.strip().rstrip("/")

def backoff_delay(attempt):
    return min(2 ** attempt, 60)

def decode_response(data):
    return json.loads(data)
'''
QUERY = "Calculate an exponential retry delay with a maximum wait."
SETTINGS = {"query": QUERY, "retrieval": "dense", "rerank": False,
            "candidates": 4, "max_results": 8, "source_budget": 12000,
            "token_budget": 4096, "include_dependencies": True}


def check(condition, detail):
    if not condition:
        raise AssertionError(detail)


def stop(process):
    """Verify the actual CLI handles SIGTERM and closes its watcher/model."""
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise RuntimeError("Search CLI did not stop within 20s; forced termination")
    check(process.returncode == 0, f"Search CLI exited with status {process.returncode}")
    return {"signal": "SIGTERM", "returncode": process.returncode, "forced_kill": False}


def start(repo, cache, log, timeout):
    command = [sys.executable, "-m", "semantic_search.cli", "serve", "--repo", str(repo),
               "--source-root", "src", "--index-cache", str(cache), "--port", "0",
               "--watch-interval", "0.2"]
    began = time.perf_counter()
    with log.open("w") as stream:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    try:
        while time.perf_counter() - began < timeout:
            output = log.read_text()
            match = re.search(r"Ready: (http://127\.0\.0\.1:\d+)", output)
            if match:
                return process, SearchClient(match[1], timeout=timeout), time.perf_counter() - began, command
            if process.poll() is not None:
                raise RuntimeError(f"Search CLI failed to start; see {log}")
            time.sleep(.1)
        raise RuntimeError(f"Search CLI was not ready within {timeout}s; see {log}")
    except BaseException:
        if process.poll() is None:
            stop(process)
        raise


def verify(result, source):
    check(not result.is_error, "MCP search returned an error")
    envelope = result.structured_content
    check(isinstance(envelope, dict), "MCP result omitted structuredContent")
    text = serialize_context(envelope)
    check(len(result.content) == 1 and result.content[0].type == "text",
          "MCP result did not contain exactly one text block")
    check(result.content[0].text == text, "Text and structured envelopes differ")
    actual = len(encoding().encode(text, disallowed_special=()))
    check(envelope["token_usage"] == {"encoding": "cl100k_base", "tokens": actual,
                                      "budget": SETTINGS["token_budget"], "truncated": False},
          "MCP token usage differs from the actual serialized payload")
    check(actual <= SETTINGS["token_budget"], "MCP payload exceeds the requested token cap")
    check(envelope["query"] == QUERY, "MCP response query changed")
    rows = envelope["results"]
    check(len(rows) == 3 and len(rows) <= SETTINGS["max_results"],
          "Toy corpus did not return exactly three implementation excerpts")
    check(sum(len(row["source"]) for row in rows) <= SETTINGS["source_budget"],
          "MCP excerpts exceed the source character cap")
    lines = physical_lines(source)
    for row in rows:
        check(row["path"] == "src/sample.py", "MCP result path is not repository-relative")
        check(row["source"] == "".join(lines[row["start_line"] - 1:row["end_line"]]),
              "MCP excerpt does not exactly match saved source at its bounds")
        check(row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest(),
              "MCP excerpt SHA-256 does not match saved source")
        check(not row["truncated"], "Unexpected truncation of the small toy corpus")
    return envelope


async def exercise(service, source_file, initial, timeout):
    steps = [{"phase": "initial_index", "status": initial}]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "semantic_search.mcp_server", "--server", service.url],
        cwd=ROOT,
    )
    async with Client(parameters, mode="legacy", read_timeout_seconds=timeout) as client:
        tools = (await client.list_tools()).tools
        check([tool.name for tool in tools] == ["search_code"], "MCP exposed an unexpected tool list")
        protocol = client.protocol_version
        server_info = client.server_info.model_dump(mode="json", by_alias=True, exclude_none=True)
        began = time.perf_counter()
        before = verify(await client.call_tool("search_code", SETTINGS), SOURCE)
        steps.append({"phase": "initial_mcp_search", "elapsed_s": time.perf_counter() - began,
                      "context": before})
        old_hash = next(row["source_hash"] for row in before["results"] if row["symbol"] == "backoff_delay")
        changed = SOURCE.replace("attempt, 60", "attempt, 90")
        source_file.write_text(changed)
        began = time.perf_counter()
        polls = 0
        # GET/status never refreshes or queries. This phase proves the watcher
        # updates the index independently of a search or POST/index request.
        while time.perf_counter() - began < timeout:
            observed = await asyncio.to_thread(service.status)
            polls += 1
            stats, watcher = observed.get("indexstats") or {}, observed.get("watcher") or {}
            if (watcher.get("refresh_count", 0) >= 1
                    and observed["source_manifest"] != initial["source_manifest"]
                    and stats.get("indexed_windows") == 1 and stats.get("reused_windows") == 2):
                break
            await asyncio.sleep(.1)
        else:
            raise RuntimeError("Watcher did not independently embed the one saved edit")
        check(watcher.get("last_error") is None, "Watcher reported a refresh error")
        check(stats["total_windows"] == 3, "Watcher changed the toy corpus window count")
        steps.append({"phase": "watcher_refreshed_without_search", "elapsed_s": time.perf_counter() - began,
                      "status_polls": polls, "trigger_requests": "GET /status only", "status": observed})
        began = time.perf_counter()
        after = verify(await client.call_tool("search_code", SETTINGS), changed)
        updated = next(row for row in after["results"] if row["symbol"] == "backoff_delay")
        check(updated["source_hash"] != old_hash and "attempt, 90" in updated["source"],
              "MCP did not return the saved edit with a new excerpt hash")
        steps.append({"phase": "mcp_sees_saved_edit", "elapsed_s": time.perf_counter() - began,
                      "previous_source_hash": old_hash, "new_source_hash": updated["source_hash"],
                      "context": after})
    return steps, protocol, server_info


def redact(value, work_dir):
    """Keep machine-specific absolute paths out of the publishable JSON."""
    if isinstance(value, dict):
        return {key: redact(item, work_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, work_dir) for item in value]
    if isinstance(value, str):
        return value.replace(str(work_dir), "<work-dir>").replace(str(ROOT), "<project>")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".cache/mcp-smoke")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeout > 600:
        parser.error("--timeout must be finite, positive and at most 600s")
    work_dir = args.work_dir.resolve()
    if not work_dir.is_relative_to(ROOT / ".cache"):
        parser.error("--work-dir must be inside this project's ignored .cache directory")
    if args.output.exists():
        parser.error("Output exists; choose a new JSON file")
    work_dir.mkdir(parents=True, exist_ok=False)
    repo = work_dir / "repo"
    (repo / "src").mkdir(parents=True)
    source_file = repo / "src/sample.py"
    source_file.write_text(SOURCE)

    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(repo), *arguments], stderr=subprocess.STDOUT)

    git("init", "-b", "main")
    git("add", "src/sample.py")
    git("-c", "user.name=Local MCP smoke", "-c", "user.email=smoke@example.invalid",
        "commit", "-m", "Create three toy implementations")
    revision = git("rev-parse", "HEAD").decode().strip()
    process, service, startup_s, command = start(repo, work_dir / "indexes", work_dir / "service.log", args.timeout)
    termination = None
    try:
        initial = service.status()
        check(initial["windows"] == 3, "Initial index did not contain three windows")
        check(initial["indexstats"]["indexed_windows"] == 3, "Fresh toy index did not embed all three windows")
        check(initial["indexstats"]["reused_windows"] == 0, "Fresh toy index unexpectedly reused vectors")
        check(initial["watcher"]["running"], "Actual CLI did not start its watcher")
        steps, protocol, server_info = asyncio.run(exercise(service, source_file, initial, args.timeout))
    finally:
        termination = stop(process)
    result = {"kind": "live_mcp_watcher_toy_smoke", "model_inference": True,
              "semantic_quality_measurement": False, "settings": SETTINGS,
              "service_command": command, "service_startup_s": startup_s,
              "toy_git_revision": revision, "source_root": "src", "windows": 3,
              "mcp_protocol": protocol, "mcp_server": server_info,
              "runtime": {name: importlib.metadata.version(name)
                          for name in ("mcp", "mcp-types", "anyio", "tiktoken", "numpy")},
              "embedding": initial["store"]["embedding"], "steps": steps,
              "termination": termination,
              "limitations": "Three toy functions exercise real Qwen, CLI, stdio MCP, saved-edit watcher and shutdown; this is not retrieval quality or paid-token savings evidence."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(redact(result, work_dir), indent=2) + "\n")
    print(f"Live MCP/watch checks passed; saved {args.output}")


if __name__ == "__main__":
    main()
