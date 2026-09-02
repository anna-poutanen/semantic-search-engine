#!/usr/bin/env python3
"""Post-pilot tool-exposure diagnostic; excluded from paired performance results.

Running this script launches one hosted coding-agent turn. Local retrieval is
strictly lexical: embedding and H2O providers are replaced by rejecting stubs.
Invoke only after all declared paired trials have finished. No retries occur.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

from semantic_search.backend import SearchBackend
from semantic_search.server import create_server
from semantic_search.source import physical_lines
from semantic_search.workflows import ROOT, cli_command, digest, git, parse_events, redact, write_json


SOURCE = '''def normalize_path(path):
    return path.strip().rstrip("/")

def encoded_hex_checksum(data):
    return bytes(data).hex()

def retry_delay(attempt):
    return min(2 ** attempt, 60)
'''
QUERY = "encoded hex checksum"
ARGUMENTS = {"query": QUERY, "retrieval": "bm25", "rerank": False,
             "max_results": 3, "token_budget": 4096, "include_dependencies": False}
PROMPT = (
    "This is a tool-exposure diagnostic, not a coding task. Before running any shell "
    "command, call the registered semantic_search MCP server's search_code tool with "
    "these exact arguments: " + json.dumps(ARGUMENTS, sort_keys=True) + ". "
    "Then report the tool's returned implementation names and repository-relative paths, "
    "and whether the call succeeded. Do not use shell commands, edit files, launch "
    "subagents, or access parent directories, other repositories, home files, evaluation "
    "materials or the network. Do not infer the answer from source files. If the tool is "
    "unavailable, state that limitation and stop."
)


class ForbiddenEmbeddings:
    """Ensure an unexpected dense call cannot load or execute a model."""
    metadata = {"id": "diagnostic-embeddings-forbidden", "dimensions": 1}

    def __init__(self):
        self.attempts = 0

    def embed_documents(self, texts):
        self.attempts += 1
        raise RuntimeError("This lexical exposure diagnostic forbids embedding inference")

    def embed_query(self, query):
        self.attempts += 1
        raise RuntimeError("This lexical exposure diagnostic forbids embedding inference")

    def close(self):
        pass


class ForbiddenH2O:
    def __init__(self):
        self.attempts = 0

    def score(self, query, snippet):
        self.attempts += 1
        raise RuntimeError("This lexical exposure diagnostic forbids H2O inference")


class RecordingBackend(SearchBackend):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.search_calls = []

    def search(self, query, **kwargs):
        start = time.perf_counter()
        record = {"query": query, "arguments": kwargs}
        self.search_calls.append(record)
        try:
            response = super().search(query, **kwargs)
            record["response"] = response
            return response
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            record["elapsed_s"] = time.perf_counter() - start


def envelopes(value, depth=0):
    """Handle the CLI's text/structured CallToolResult representations."""
    if depth > 12:
        return
    if isinstance(value, dict):
        if value.get("query") == QUERY and isinstance(value.get("results"), list):
            yield value
        for child in value.values():
            yield from envelopes(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from envelopes(child, depth + 1)
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return
        if isinstance(parsed, (dict, list)):
            yield from envelopes(parsed, depth + 1)


def exact_target(envelope):
    lines = physical_lines(SOURCE)
    for row in envelope.get("results", []):
        if (row.get("symbol") != "encoded_hex_checksum" or row.get("path") != "src/sample.py"
                or type(row.get("start_line")) is not int or type(row.get("end_line")) is not int):
            continue
        start, end = row["start_line"], row["end_line"]
        if not 1 <= start <= end <= len(lines):
            continue
        source = "".join(lines[start - 1:end])
        if (row.get("source") == source and "return bytes(data).hex()" in source
                and row.get("source_hash") == hashlib.sha256(source.encode()).hexdigest()):
            return True
    return False


def successful_tool_item(item):
    name = str(item.get("tool", item.get("name", "")))
    result = item.get("result")
    if (item.get("server") != "semantic_search" or name != "search_code"
            or item.get("status") in ("failed", "error")
            or item.get("error") or not result):
        return False
    if isinstance(result, dict) and (result.get("isError") or result.get("is_error")):
        return False
    arguments = item.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return False
    if not isinstance(arguments, dict) or any(arguments.get(k) != v for k, v in ARGUMENTS.items()):
        return False
    return any(exact_target(envelope) for envelope in envelopes(result))


def validate_completed_pilot(config_path, config):
    results = json.loads((config_path.parent / "results.json").read_text())
    expected = [(r["task_id"], r["condition"]) for r in config["schedule"]]
    observed = [(r["task_id"], r["condition"]) for r in results]
    if len(observed) != len(set(observed)) or sorted(observed) != sorted(expected):
        raise ValueError("Run this diagnostic only after every declared paired trial is recorded")
    if digest(config["cli_binary"]) != config["cli_sha256"]:
        raise ValueError("Frozen CLI binary changed")
    for name, expected_hash in config["artifact_sha256"].items():
        if digest(ROOT / name) != expected_hash:
            raise ValueError(f"Frozen runtime/task artifact changed: {name}")


def run(config_path, output, work_root):
    config_path = config_path.resolve(strict=True)
    config = json.loads(config_path.read_text())
    validate_completed_pilot(config_path, config)
    output.mkdir(parents=True, exist_ok=False)
    work_root.mkdir(parents=True, exist_ok=False)
    workspace, raw = work_root / "workspace", work_root / "raw"
    (workspace / "src").mkdir(parents=True)
    raw.mkdir()
    (workspace / "src/sample.py").write_text(SOURCE)
    git(workspace, "init", "-b", "main")
    git(workspace, "add", ".")
    git(workspace, "-c", "user.name=MCP diagnostic", "-c", "user.email=mcp-diagnostic@example.invalid",
        "commit", "-m", "Lexical exposure fixture")
    substitutions = [(str(workspace), "<WORKSPACE>"), (str(raw), "<DIAGNOSTIC_LOG>"),
                     (str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]
    guard, h2o_guard = ForbiddenEmbeddings(), ForbiddenH2O()
    backend = RecordingBackend(workspace, "src", db_path=raw / "index.sqlite3",
                               embedder=guard, h2o_client=h2o_guard)
    server = thread = process = None
    record = {
        "schema_version": 1,
        "classification": "Post-pilot explicit tool-exposure diagnostic; excluded from paired performance estimates",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "frozen_pilot_config_sha256": digest(config_path),
        "diagnostic_script_sha256": digest(Path(__file__)),
        "model": config["model"], "cli_version": config["cli_version"],
        "common_config": config["common_config"],
        "requested_tool_arguments": ARGUMENTS,
        "timeout_s": 180, "retry_policy": "None",
        "local_models": "Embedding and H2O providers replaced with rejecting stubs; lexical-only fixture",
        "tool_exposure_confirmed": False, "timed_out": False,
        "agent_elapsed_s": None, "agent_exit_code": None,
        "usage": None, "completed": False, "failed": False,
    }
    try:
        setup = time.perf_counter()
        warm = backend.search(**ARGUMENTS)
        if not exact_target(warm):
            raise RuntimeError("Lexical fixture warmup failed its expected source check")
        backend.search_calls.clear()
        server = create_server(backend, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        command = cli_command(config, workspace, raw / "final.txt", PROMPT, url, source_root="src")
        record["setup_s"] = time.perf_counter() - setup
        write_json(output / "command.json", redact(command, substitutions))
        (output / "prompt.txt").write_text(PROMPT)
        (output / "fixture.py").write_text(SOURCE)
        write_json(output / "registered-mcp.json", redact({
            "server_name": "semantic_search", "command": config["python"],
            "args": ["-m", "semantic_search.mcp_server", "--server", url],
            "cwd": str(ROOT), "env": {"PYTHONPATH": str(ROOT / "src")},
            "enabled_tools": ["search_code"], "required": True,
            "startup_timeout_sec": 20, "tool_timeout_sec": 180,
            "default_tools_approval_mode": "auto",
        }, substitutions))
        env = dict(os.environ)
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
            env.pop(name, None)
        began = time.perf_counter()
        with (raw / "events.jsonl").open("w") as stdout, (raw / "stderr.txt").open("w") as stderr:
            process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True, env=env)
            try:
                process.wait(timeout=180)
            except subprocess.TimeoutExpired:
                record["timed_out"] = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        record["agent_elapsed_s"] = time.perf_counter() - began
        record["agent_exit_code"] = process.returncode
        events, invalid = [], []
        for line in (raw / "events.jsonl").read_text().splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                invalid.append(line)
        record.update(parse_events(events))
        record["invalid_event_lines"] = invalid
        record["successful_expected_tool_calls"] = sum(successful_tool_item(item) for item in record["mcp_items"])
        record["http_search_calls"] = backend.search_calls
        expected_http_calls = [call for call in backend.search_calls
                               if call["query"] == QUERY
                               and all(call["arguments"].get(k) == v for k, v in ARGUMENTS.items() if k != "query")
                               and exact_target(call.get("response", {}))]
        record["successful_expected_http_calls"] = len(expected_http_calls)
        record["tool_exposure_confirmed"] = bool(record["successful_expected_tool_calls"] and expected_http_calls)
        with (output / "events.jsonl").open("w") as stream:
            for event in events:
                if event.get("item", {}).get("type") != "reasoning":
                    stream.write(json.dumps(redact(event, substitutions)) + "\n")
        (output / "stderr.txt").write_text(redact((raw / "stderr.txt").read_text(), substitutions))
        if (raw / "final.txt").exists():
            (output / "final.txt").write_text(redact((raw / "final.txt").read_text(), substitutions))
        record["raw_events_sha256"] = digest(raw / "events.jsonl")
        record["published_events_sha256"] = digest(output / "events.jsonl")
        record["working_tree_unchanged"] = not git(workspace, "status", "--porcelain").stdout.strip()
    except Exception as error:
        record["infrastructure_error"] = f"{type(error).__name__}: {error}"
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        backend.close()
        # Preserve partial evidence even when usage parsing or startup fails.
        if (raw / "events.jsonl").exists():
            if not (output / "events.jsonl").exists():
                with (output / "events.jsonl").open("w") as stream:
                    for line in (raw / "events.jsonl").read_text().splitlines():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            record.setdefault("invalid_event_lines", []).append(line)
                            continue
                        if event.get("item", {}).get("type") != "reasoning":
                            stream.write(json.dumps(redact(event, substitutions)) + "\n")
            record["raw_events_sha256"] = digest(raw / "events.jsonl")
            record["published_events_sha256"] = digest(output / "events.jsonl")
        for name in ("stderr.txt", "final.txt"):
            if (raw / name).exists():
                (output / name).write_text(redact((raw / name).read_text(), substitutions))
        if (raw / "stderr.txt").exists():
            stderr_lines = (raw / "stderr.txt").read_text().splitlines()
            record["runtime_collaboration_spawn_failures"] = sum("collab spawn failed" in line for line in stderr_lines)
            record["runtime_subagent_hook_warnings"] = sum("subagent hook" in line for line in stderr_lines)
        record["forbidden_embedding_attempts"] = guard.attempts
        record["forbidden_h2o_attempts"] = h2o_guard.attempts
        write_json(output / "diagnostic.json", redact(record, substitutions))
    print(json.dumps({"tool_exposure_confirmed": record["tool_exposure_confirmed"],
                      "agent_elapsed_s": record["agent_elapsed_s"], "usage": record["usage"],
                      "result": str(output / "diagnostic.json")}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    args = parser.parse_args()
    run(args.config, args.output.resolve(), args.work_root.resolve())


if __name__ == "__main__":
    main()
