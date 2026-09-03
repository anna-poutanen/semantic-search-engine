#!/usr/bin/env python3
"""Freeze and explicitly run a separate repeated, paired multi-file workflow study.

validate/freeze-common/freeze do not invoke a hosted model or GPU inference.
probe invokes the hosted agent with a model-rejecting lexical fixture.
run invokes hosted agents and serial local Qwen services. Old studies are unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import shlex
import signal
import statistics
import subprocess
import sys
import tarfile
import threading
import time
import tokenize

from semantic_search import workflows as old
from semantic_search.context import compact_result, serialize_context
from semantic_search.corpus import _source_files
from semantic_search.source import physical_lines

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path("benchmarks/coding_workflows_multifile.json")
PROTOCOL = Path("docs/multifile-workflow-protocol.md")
SELF = Path("scripts/run_multifile_workflows.py")
LOCK = Path("requirements-multifile.lock")
REMOVED_ENV_NAMES = (
    "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN",
    "CODEX_APP_TOOLS_PIPE_PATH", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    "CODEX_PERMISSION_PROFILE", "CODEX_SESSION_ID",
    "CODEX_TASK_WORKSPACE_VERIFYING_IDENTITY", "CODEX_THREAD_ID",
)
PYTEST_ADDOPTS = ('--tb=short -W "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning" '
                 '-W "ignore:The `cancellable=` keyword argument:DeprecationWarning"')
TEST_CONFIG_NAMES = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")
PYTEST_COUNT_FIELDS = ("passed", "skipped", "failed", "errors", "xfailed", "xpassed", "deselected")
DEVELOPER_INSTRUCTIONS = (
    "Complete the task in this single agent. Do not create, spawn, or message other agents. "
    "Use ordinary repository tools and the configured local search tool as available."
)
WORKFLOW_INSTRUCTION = (
    "When search_code is available, use it before broad source reads to locate the relevant "
    "implementations. Start with max_results=5 and token_budget=2000; use its returned excerpts "
    "as source reads, then read only narrow missing definitions or line ranges needed to implement "
    "and verify the change. If it is unavailable, use ordinary repository search and reads. "
    "Both paths may use all ordinary repository tools. Run focused new tests first, then relevant "
    "existing integration tests; summarize failures with --tb=short instead of dumping large logs."
)
PROBE_SOURCE = 'def workflow_probe():\n    return "MULTIFILE_PROBE_OK"\n'
PROBE_ARGUMENTS = {"query": "workflow probe", "retrieval": "bm25", "max_results": 1,
                   "token_budget": 512, "include_dependencies": False}
LOOPBACK_SOURCE = '''import socket
import threading

with socket.socket() as server:
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    def reply():
        with server.accept()[0] as connection:
            connection.sendall(b"LOOPBACK_OK")
    worker = threading.Thread(target=reply)
    worker.start()
    with socket.create_connection(server.getsockname(), timeout=5) as client:
        assert client.recv(32) == b"LOOPBACK_OK"
    worker.join(5)
    assert not worker.is_alive()
print("LOOPBACK_OK")
'''
COMMON_CONFIG = {
    **old.COMMON_CONFIG, "agents.enabled": False, "tool_output_token_limit": 4096,
    "developer_instructions": DEVELOPER_INSTRUCTIONS,
    "features.multi_agent": False, "features.multi_agent_v2": False,
    "sandbox_workspace_write.network_access": True,
}


def stamp():
    return {"utc": datetime.now(timezone.utc).isoformat(), "monotonic_s": time.monotonic()}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text())


def public_redact(value, substitutions):
    value = old.redact(value, substitutions)
    if isinstance(value, dict):
        return {key: public_redact(item, ()) for key, item in value.items()}
    if isinstance(value, list):
        return [public_redact(item, ()) for item in value]
    if isinstance(value, str):
        # Do not load authentication files or inspect secret environment values.
        # Mask credential-like sequences only if they appear in captured output.
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "<REDACTED_API_KEY>", value)
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{24,}=*", "Bearer <REDACTED_TOKEN>", value)
    return value


def pytest_counts(output):
    candidates = [line for line in output.splitlines() if re.search(
        r"\b\d+ (?:passed|failed|skipped|xfailed|xpassed|deselected|errors?)\b", line)]
    if not candidates:
        return None
    result = dict.fromkeys(PYTEST_COUNT_FIELDS, 0)
    for count, category in re.findall(r"\b(\d+) (passed|failed|skipped|xfailed|xpassed|deselected|errors?)\b", candidates[-1]):
        result["errors" if category in ("error", "errors") else category] += int(count)
    return result


def full_tree_hashes(workspace):
    return {str(path.relative_to(workspace)): old.digest(path) for path in sorted(workspace.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(workspace).parts and not path.is_symlink()}


def probe_matches(events, receipts):
    matches = []
    for event in events:
        item = event.get("item", {})
        if (event.get("type") != "item.completed" or item.get("type") != "mcp_tool_call"
                or item.get("server") != "semantic_search" or item.get("tool") != "search_code"
                or item.get("status") != "completed" or item.get("error")
                or any(item.get("arguments", {}).get(key) != value for key, value in PROBE_ARGUMENTS.items())):
            continue
        result = item.get("result")
        if not isinstance(result, dict) or result.get("isError") or result.get("is_error"):
            continue
        content = result.get("content")
        if not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
            continue
        text = content[0].get("text")
        if not isinstance(text, str):
            continue
        try:
            envelope = json.loads(text)
            rows = envelope["results"]
            if (envelope["query"] != PROBE_ARGUMENTS["query"] or len(rows) != 1
                    or rows[0]["path"] != "src/probe.py" or rows[0]["symbol"] != "workflow_probe"
                    or rows[0]["source"] != PROBE_SOURCE or rows[0]["start_line"] != 1
                    or rows[0]["end_line"] != 2
                    or rows[0]["source_hash"] != hashlib.sha256(PROBE_SOURCE.encode()).hexdigest()):
                continue
        except (ValueError, KeyError, TypeError, IndexError):
            continue
        payload_hash = hashlib.sha256(text.encode()).hexdigest()
        for receipt in receipts:
            checks = receipt.get("returned_source_checks")
            before, after = receipt.get("snapshots", {}).get("before"), receipt.get("snapshots", {}).get("after")
            if (not receipt.get("success") or receipt.get("query") != PROBE_ARGUMENTS["query"]
                    or receipt.get("compact_text_sha256") != payload_hash
                    or not isinstance(checks, list) or len(checks) != 1
                    or not all(check.get(key) is True for check in checks for key in
                               ("source_matches_snapshot", "physical_bounds_match", "source_hash_matches"))
                    or not before or not after or before != after
                    or receipt.get("snapshot_error")):
                continue
            matches.append({"item_id": item.get("id"), "receipt_number": receipt["number"],
                            "compact_text_sha256": payload_hash})
    return matches


def controlled_loopback_commands(events, python, workspace):
    matched = []
    mcp_end = next((index for index, event in enumerate(events) if event.get("type") == "item.completed"
        and event.get("item", {}).get("type") == "mcp_tool_call"), None)
    starts = {event["item"]["id"]: index for index, event in enumerate(events)
              if event.get("type") == "item.started" and event.get("item", {}).get("type") == "command_execution"}
    for index, event in enumerate(events):
        item = event.get("item", {})
        if (event.get("type") != "item.completed" or item.get("type") != "command_execution"
                or item.get("status") != "completed" or item.get("exit_code") != 0
                or item.get("aggregated_output", "").strip() != "LOOPBACK_OK"):
            continue
        try:
            tokens = shlex.split(item["command"])
            if len(tokens) == 3 and Path(tokens[0]).name in ("bash", "zsh", "sh") and tokens[1] in ("-c", "-lc"):
                tokens = shlex.split(tokens[2])
            if (len(tokens) != 2 or Path(tokens[0]).resolve() != Path(python).resolve()
                    or (workspace / tokens[1]).resolve() != (workspace / "loopback_smoke.py").resolve()
                    or mcp_end is None or starts.get(item.get("id"), index) <= mcp_end):
                continue
        except (ValueError, KeyError, TypeError):
            continue
        matched.append({"item_id": item["id"], "normalized_argv": [python, "loopback_smoke.py"],
                        "stdout": "LOOPBACK_OK", "exit_code": 0})
    return matched


def artifact(path):
    candidate = (ROOT / path).resolve(strict=True)
    if not candidate.is_relative_to(ROOT):
        raise ValueError("Frozen artifacts must be inside the project")
    return str(candidate.relative_to(ROOT))


def environment(workspace=None, source_root=None):
    env = {key: value for key, value in os.environ.items() if key not in REMOVED_ENV_NAMES}
    env["PYTEST_ADDOPTS"] = PYTEST_ADDOPTS
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    if workspace is not None:
        env["PYTHONPATH"] = str((Path(workspace) / source_root).parent)
    return env


def prompt(task, python):
    return ("Work only in the current repository checkout. Implement the requested public behavior "
            "and add regression tests. Do not access parent directories, other repositories, "
            "evaluation materials, home-directory files, or external network services. Local loopback "
            "integration tests are allowed. Do not install dependencies "
            f"or commit changes. Prefer a new regression test file and preserve existing upstream tests. "
            f"The existing Python interpreter is {python}. "
            "Finish with a concise change/check summary.\n\n" + WORKFLOW_INSTRUCTION + "\n\n" + task["prompt"])


def schedule(tasks):
    trials = []
    for repetition in (1, 2):
        for index, task in enumerate(tasks):
            conditions = ("baseline", "mcp") if (index + repetition - 1) % 2 == 0 else ("mcp", "baseline")
            for condition in conditions:
                trials.append({"task_id": task["id"], "condition": condition, "repetition": repetition})
    return trials


def task_artifacts(task):
    verification = task["verification"]
    items = {verification["script"]: verification["sha256"],
             verification["shared_helper"]: verification["shared_helper_sha256"]}
    reference = Path(".cache/multifile-reference") / task["id"] / "reference.patch"
    if task.get("reference_patch_sha256"):
        items[artifact(reference)] = task["reference_patch_sha256"]
    if task.get("preparation_patch") is not None:
        items[task["preparation_patch"]] = task["preparation_patch_sha256"]
    for path, digest in task.get("upstream_test_sha256", {}).items():
        # These hashes identify files inside the original corpus, not evaluator files.
        if old.digest(ROOT / task["corpus"] / path) != digest:
            raise ValueError(f"Pinned original upstream test changed: {task['id']}:{path}")
    return items


def validate_manifest(path=MANIFEST):
    path = ROOT / path
    data = read_json(path)
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 4:
        raise ValueError("The new study must declare exactly four tasks")
    ids = [task.get("id") for task in tasks]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != 4:
        raise ValueError("Task IDs must be nonempty and unique")
    for task in tasks:
        for name, expected in task_artifacts(task).items():
            if old.digest(ROOT / artifact(name)) != expected:
                raise ValueError(f"Task artifact changed: {name}")
        current = old.git(ROOT / task["corpus"], "rev-parse", "HEAD").stdout.decode().strip()
        if current != task["revision"]:
            raise ValueError(f"Corpus revision changed: {task['id']}")
        if task.get("preparation_patch") is not None:
            raise ValueError("These new feature tasks require an unmodified pinned baseline")
        if not task.get("compatibility_commands"):
            raise ValueError(f"Task needs frozen original integration commands: {task['id']}")
        for command in task["compatibility_commands"]:
            expected = command.get("expected_summary")
            if (not isinstance(expected, dict) or set(expected) != set(PYTEST_COUNT_FIELDS)
                    or any(type(value) is not int or value < 0 for value in expected.values())
                    or expected["passed"] == 0):
                raise ValueError("Every original integration command requires exact expected pytest counts")
    return data


def prepare(task, workspace):
    workspace.mkdir(parents=True, exist_ok=False)
    archive = old.git(ROOT / task["corpus"], "archive", task["revision"]).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        stream.extractall(workspace, filter="data")
    old.git(workspace, "init", "-b", "main")
    if task.get("preparation_patch") is not None:
        raise ValueError("The new study never applies old seeded-regression patches")
    old.git(workspace, "add", ".")
    env = environment()
    env.update(GIT_AUTHOR_DATE="2026-10-13T12:00:00+00:00", GIT_COMMITTER_DATE="2026-10-13T12:00:00+00:00")
    old.git(workspace, "-c", "user.name=Multi-file fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "Pinned unmodified feature baseline", env=env)
    return old.git(workspace, "rev-parse", "HEAD").stdout.decode().strip(), old.tree_hashes(workspace)


def cli_command(config, workspace, final_path, user_prompt, service_url=None, source_root=None):
    command = [config["cli_binary"], "--no-daemon", "exec", "--ignore-user-config", "--ignore-rules",
               "--strict-config", "--ephemeral", "--json", "--color", "never", "--sandbox", "workspace-write",
               "-m", config["model"], "-C", str(workspace), "-o", str(final_path)]
    settings = dict(config["common_config"])
    settings["shell_environment_policy.set"] = {"PYTEST_ADDOPTS": PYTEST_ADDOPTS, "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": str(Path(config["python"]).parent) + os.pathsep + os.environ.get("PATH", "")}
    if source_root:
        settings["shell_environment_policy.set"]["PYTHONPATH"] = str((workspace / source_root).parent)
    if service_url is not None:
        settings.update({"mcp_servers.semantic_search.command": config["python"],
            "mcp_servers.semantic_search.args": ["-m", "semantic_search.mcp_server", "--server", service_url,
                                                  "--agent-text-only"],
            "mcp_servers.semantic_search.cwd": str(ROOT),
            "mcp_servers.semantic_search.env": {"PYTHONPATH": str(ROOT / "src")},
            "mcp_servers.semantic_search.enabled_tools": ["search_code"],
            "mcp_servers.semantic_search.required": True,
            "mcp_servers.semantic_search.startup_timeout_sec": 20,
            "mcp_servers.semantic_search.tool_timeout_sec": 180,
            "mcp_servers.semantic_search.default_tools_approval_mode": "auto"})
    for key, value in settings.items():
        command.extend(["-c", f"{key}={old.toml_value(value)}"])
    command.append(user_prompt)
    return command


def verify_runtime(config):
    for name, expected in config["artifact_sha256"].items():
        if old.digest(ROOT / name) != expected:
            raise ValueError(f"Runtime artifact changed after freeze: {name}")
    if old.digest(config["cli_binary"]) != config["cli_sha256"]:
        raise ValueError("Codex executable changed after freeze")
    packages = dict(sorted((item.metadata["Name"], item.version) for item in importlib.metadata.distributions()))
    if packages != config["packages"]:
        raise ValueError("Python runtime packages changed after freeze")


def freeze_common(output):
    if output.exists():
        raise ValueError("Choose a fresh common-runtime output directory")
    if importlib.metadata.version("trio") != "0.32.0" or importlib.metadata.version("anyio") != "4.15.1":
        raise ValueError("Common runtime requires pinned Trio0.32.0 and AnyIO4.15.1")
    cli = shutil.which("codex")
    if not cli:
        raise RuntimeError("Codex CLI is not installed")
    from semantic_search.embeddings import QwenEmbedder
    files = [SELF, PROTOCOL, LOCK, Path("src/semantic_search/workflows.py"),
             Path("requirements-mac.lock"), Path("pyproject.toml"),
             *[path.relative_to(ROOT) for path in (ROOT / "src/semantic_search").glob("*.py")]]
    config = {"schema_version": 1, "classification": "Common runtime before separate transport probe and16 trials",
        "frozen_at": stamp()["utc"], "cli_binary": cli, "cli_sha256": old.digest(cli),
        "cli_version": subprocess.check_output([cli, "--version"], text=True).strip(),
        "python": str(Path(sys.executable).absolute()), "python_version": sys.version,
        "model": "gpt-6.1-sol", "common_config": COMMON_CONFIG, "timeout_s": 900,
        "probe_timeout_s": 180, "repetitions_per_task_condition": 2,
        "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())),
        "embedding_model": QwenEmbedder(progress=False).metadata,
        "host": {"platform": platform.platform(), "machine": platform.machine()},
        "artifact_sha256": {str(path): old.digest(ROOT / path) for path in sorted(set(files))},
        "runtime_git_revision": old.git(ROOT, "rev-parse", "HEAD").stdout.decode().strip(),
        "environment": {"removed_names": list(REMOVED_ENV_NAMES), "values_not_recorded": True,
                        "auth": "Existing normal ChatGPT CLI authentication; credentials never copied/read by harness",
                        "preserved": ["HOME", "CODEX_HOME", "PATH"], "pytest_addopts": PYTEST_ADDOPTS,
                        "path_strategy": "Prepend frozen Python bin to normal PATH in both agent and tool environment",
                        "normal_path_sha256": hashlib.sha256(os.environ.get("PATH", "").encode()).hexdigest()},
        "workflow_instruction": WORKFLOW_INSTRUCTION,
        "mcp": {"mode": "agent_text_only", "argv_suffix": ["--agent-text-only"], "tool": "search_code",
                "prompted_start": {"max_results": 5, "token_budget": 2000},
                "tool_defaults": {"retrieval": "dense", "rerank": False, "max_results": 8,
                                  "source_budget": 12000, "token_budget": 4096, "include_dependencies": True},
                "startup_timeout_s": 20, "tool_timeout_s": 180, "h2o_running": False},
        "usage": "Reported completed-turn input+output. Cached input/cache writes and reasoning output are subsets; per-model-call usage unavailable from exec JSON.",
        "retry_policy": "No retries/resume. Preserve timeouts and infrastructure/verification failures.",
        "timing": "Agent CLI launch to exit includes adapter and every tool. Fixture/Qwen/index warmup, shutdown and independent grading are separate.",
    }
    output.mkdir(parents=True)
    write_json(output / "common-runtime.json", config)
    print(f"Frozen common runtime: {output / 'common-runtime.json'}", flush=True)
    return config


def freeze(output, work_root, common_path, probe_path, manifest_path=MANIFEST):
    if output.exists() or work_root.exists():
        raise ValueError("Fresh output and trial workspace roots are required")
    config = read_json(common_path)
    verify_runtime(config)
    probe = read_json(probe_path)
    if (probe.get("common_runtime_sha256") != old.digest(common_path)
            or probe.get("successful_expected_mcp_calls") != 1
            or probe.get("successful_expected_http_receipts") != 1
            or probe.get("forbidden_local_model_calls", 1) != 0
            or probe.get("observed_command_items") != 1
            or len(probe.get("matched_loopback_commands", [])) != 1
            or probe.get("observed_file_change_items") != 0
            or probe.get("initial_file_hashes") != probe.get("final_file_hashes")
            or not probe.get("completed_correct")):
        raise ValueError("A successful separately recorded model-rejecting transport probe is required")
    manifest = validate_manifest(manifest_path)
    config.update({"classification": "New exploratory multi-file feature workflows;16 trials,8 paired repetitions",
                   "manifest": artifact(manifest_path), "manifest_sha256": old.digest(ROOT / manifest_path),
                   "work_root": str(work_root), "common_runtime_source": artifact(common_path),
                   "common_runtime_sha256": old.digest(common_path), "transport_probe": artifact(probe_path),
                   "transport_probe_sha256": old.digest(probe_path), "schedule": schedule(manifest["tasks"]),
                   "prompt_sha256": {task["id"]: hashlib.sha256(prompt(task, config["python"]).encode()).hexdigest()
                                     for task in manifest["tasks"]},
                   "test_integrity": "Original tracked tests and test configuration hashes; changed/deleted tests require review, additions alone do not prove weakening.",
                   "causal_scope": "Package of improved tool guidance/output mode/history cap/runtime/isolation versus ordinary search under the same new common setup; not isolated component effects."})
    config["artifact_sha256"][artifact(manifest_path)] = config["manifest_sha256"]
    for task in manifest["tasks"]:
        config["artifact_sha256"].update(task_artifacts(task))
    config["artifact_sha256"].update({artifact(common_path): old.digest(common_path), artifact(probe_path): old.digest(probe_path)})
    output.mkdir(parents=True)
    write_json(output / "frozen-config.json", config)
    shutil.copyfile(ROOT / manifest_path, output / "tasks.json")
    print(f"Frozen16 trials: {output}", flush=True)


def snapshot(workspace, source_root, directory):
    directory.mkdir(parents=True)
    workspace = workspace.resolve()
    files = _source_files(workspace, workspace / source_root)
    hashes = {}
    for path in files:
        relative = str(path.relative_to(workspace))
        content = path.read_bytes()
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        hashes[relative] = hashlib.sha256(content).hexdigest()
    result = {"files": hashes, "git_revision": old.git(workspace, "rev-parse", "HEAD").stdout.decode().strip(),
              "manifest_sha256": hashlib.sha256(
              json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    write_json(directory / "snapshot.json", result)
    return result


def check_receipt_source(response, after_directory):
    checks = []
    for row in response["results"]:
        saved = (after_directory / row["path"]).read_bytes()
        codec, _ = tokenize.detect_encoding(io.BytesIO(saved).readline)
        source = saved.decode(codec)
        exact = "".join(physical_lines(source)[row["start_line"] - 1:row["end_line"]])
        checks.append({"id": row["id"], "path": row["path"],
                       "source_matches_snapshot": exact == row["source"],
                       "physical_bounds_match": row["end_line"] == row["start_line"] + len(physical_lines(row["source"])) - 1,
                       "source_hash_matches": row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()})
    return checks


def recording_backend(workspace, source_root, raw_dir, *, model_rejecting=False):
    from semantic_search.backend import SearchBackend

    class Guard:
        calls = 0
        metadata = {"id": "forbidden-probe-model", "dimensions": 2}
        def embed_documents(self, documents):
            self.calls += 1
            raise AssertionError("Local model inference is forbidden in transport probe")
        def embed_query(self, query):
            self.calls += 1
            raise AssertionError("Local model inference is forbidden in transport probe")
        def score(self, *args):
            self.calls += 1
            raise AssertionError("H2O inference is forbidden in transport probe")
        def close(self):
            pass

    guard = Guard() if model_rejecting else None

    class RecordingBackend(SearchBackend):
        record_requests = False
        receipt_count = 0
        receipt_lock = threading.Lock()

        def search(self, query, **settings):
            if not self.record_requests:
                return super().search(query, **settings)
            with self.receipt_lock:
                self.receipt_count += 1
                number = self.receipt_count
            name = f"{number:04d}"
            receipt = {"number": number, "start": stamp(), "query": query,
                       "settings": settings, "snapshots": {}}
            before = time.perf_counter()
            try:
                receipt["snapshots"]["before"] = snapshot(workspace, source_root, raw_dir / "snapshots" / name / "before")
                receipt["snapshot_before_s"] = time.perf_counter() - before
                call_start = time.perf_counter()
                response = super().search(query, **settings)
                receipt["backend_call_s"] = time.perf_counter() - call_start
                receipt["response"] = response
                receipt["compact_text_sha256"] = hashlib.sha256(serialize_context(compact_result(response)).encode()).hexdigest()
                receipt["success"] = True
                return response
            except Exception as error:
                receipt.update(success=False, error=f"{type(error).__name__}: {error}")
                raise
            finally:
                after = time.perf_counter()
                try:
                    after_directory = raw_dir / "snapshots" / name / "after"
                    receipt["snapshots"]["after"] = snapshot(workspace, source_root, after_directory)
                    if receipt.get("success"):
                        receipt["returned_source_checks"] = check_receipt_source(receipt["response"], after_directory)
                except Exception as error:
                    receipt["snapshot_error"] = f"{type(error).__name__}: {error}"
                receipt["snapshot_after_s"] = time.perf_counter() - after
                receipt["end"] = stamp()
                with self.receipt_lock, (raw_dir / "http-receipts.jsonl").open("a") as stream:
                    stream.write(json.dumps(receipt, allow_nan=False) + "\n")

    backend = RecordingBackend(workspace, source_root, db_path=raw_dir / "index.sqlite3",
                               embedder=guard, h2o_client=guard)
    return backend, guard


def run_agent(command, config, workspace, raw_dir, *, timeout=None):
    start = stamp()
    events, observed, invalid = [], [], []
    with (raw_dir / "events.jsonl").open("w") as raw_events, (raw_dir / "stderr.txt").open("w") as stderr:
        process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=stderr, text=True, encoding="utf-8", errors="replace",
            start_new_session=True, env=environment(workspace, config.get("source_root", "src")))

        def consume():
            for line in process.stdout:
                raw_events.write(line)
                raw_events.flush()
                received = stamp()
                try:
                    event = json.loads(line)
                    events.append(event)
                    observed.append({"received": received, "event": event})
                except ValueError:
                    invalid.append({"received": received, "line": line})

        reader = threading.Thread(target=consume, name="workflow-event-reader", daemon=True)
        reader.start()
        timed_out = False
        try:
            process.wait(timeout=timeout or config["timeout_s"])
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        reader.join(5)
        process.stdout.close()
    end = stamp()
    write_json(raw_dir / "observed-events.json", observed)
    measurements = old.parse_events(events)
    tools, starts = [], {}
    for row in observed:
        event, received = row["event"], row["received"]
        item = event.get("item", {})
        if event.get("type") == "item.started" and item.get("id"):
            starts[item["id"]] = received
        if event.get("type") == "item.completed" and item.get("type") in ("mcp_tool_call", "command_execution"):
            began = starts.get(item.get("id"))
            tools.append({"item_id": item.get("id"), "type": item["type"], "start": began, "end": received,
                          "observed_duration_s": received["monotonic_s"] - began["monotonic_s"] if began else None})
    return {**measurements, "agent_start": start, "agent_end": end,
            "agent_elapsed_s": end["monotonic_s"] - start["monotonic_s"],
            "agent_exit_code": process.returncode, "timed_out": timed_out,
            "invalid_event_lines": invalid, "tool_intervals": tools,
            "per_model_call_usage": None, "per_model_call_usage_reason": "Not emitted by exec --json"}, events, observed


def grade(task, workspace):
    verification = task["verification"]
    command = [sys.executable, str(ROOT / verification["script"]),
               *[arg.replace("{workspace}", str(workspace)) for arg in verification["args"]]]
    start = stamp()
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=verification["timeout_s"], env=environment(workspace, task["source_root"]))
        try:
            detail = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            detail = None
        return {"passed": result.returncode == 0 and detail == task["seed_validation"]["reference"],
                "exit_code": result.returncode, "detail": detail, "stdout": result.stdout,
                "stderr": result.stderr, "start": start, "end": stamp()}
    except subprocess.TimeoutExpired:
        return {"passed": False, "exit_code": None, "error": "selected_check_timeout", "start": start, "end": stamp()}


def compatibility(task, workspace, base_files, raw_dir):
    # Test modifications remain in the agent artifact, but cannot weaken this
    # independent gate. Restore original tests/config in a separate candidate copy.
    destination = raw_dir / "original-upstream-checkout"
    shutil.copytree(workspace, destination, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    original_test_paths = [name for name in base_files if name.startswith("tests/")]
    config_names = TEST_CONFIG_NAMES
    for name in config_names:
        if name not in base_files and (destination / name).is_file():
            (destination / name).unlink()
    for name in original_test_paths + [name for name in config_names if name in base_files]:
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(old.git(ROOT / task["corpus"], "show", f"{task['revision']}:{name}").stdout)
    results = []
    for specification in task["compatibility_commands"]:
        args = specification["args"] if isinstance(specification, dict) else specification
        timeout = specification.get("timeout_s", 180) if isinstance(specification, dict) else 180
        command = [sys.executable, *[arg.replace("{workspace}", str(destination)) for arg in args]]
        start = stamp()
        try:
            result = subprocess.run(command, cwd=destination, env=environment(destination, task["source_root"]),
                                    capture_output=True, text=True, timeout=timeout)
            actual_summary = pytest_counts(result.stdout)
            expected_summary = specification["expected_summary"]
            results.append({"command": command, "exit_code": result.returncode,
                            "passed": result.returncode == 0 and actual_summary == expected_summary,
                            "actual_summary": actual_summary, "expected_summary": expected_summary,
                            "stdout": result.stdout, "stderr": result.stderr, "start": start, "end": stamp()})
        except subprocess.TimeoutExpired:
            results.append({"command": command, "exit_code": None, "passed": False,
                            "error": "upstream_check_timeout", "start": start, "end": stamp()})
    original_integrity = {name: digest for name, digest in base_files.items()
                          if name.startswith("tests/") or name in TEST_CONFIG_NAMES}
    changes = [{"path": name, "before": digest, "after": old.digest(workspace / name) if (workspace / name).is_file() else None}
               for name, digest in original_integrity.items()
               if (not (workspace / name).is_file() or old.digest(workspace / name) != digest)]
    return {"passed": all(row["passed"] for row in results), "commands": results,
            "original_test_and_config_hashes": original_integrity,
            "changed_or_deleted_original_tests": changes,
            "added_configuration_removed_for_grading": [name for name in TEST_CONFIG_NAMES
                if name not in base_files and (workspace / name).is_file()],
            "test_edits_require_review": bool(changes),
            "grading_method": "Candidate sources; original pinned tracked tests and test configuration restored in evaluator-only copy"}


def public_artifacts(raw_dir, public, events, observed, substitutions):
    public.mkdir(parents=True, exist_ok=True)
    public_events = [event for event in events if event.get("item", {}).get("type") != "reasoning"]
    with (public / "events.jsonl").open("w") as stream:
        for event in public_events:
            stream.write(json.dumps(public_redact(event, substitutions)) + "\n")
    safe_observed = [row for row in observed if row["event"].get("item", {}).get("type") != "reasoning"]
    write_json(public / "event-timestamps.json", public_redact(safe_observed, substitutions))
    for name in ("stderr.txt", "final.txt", "command.json", "http-receipts.jsonl"):
        if (raw_dir / name).exists():
            (public / name).write_text(public_redact((raw_dir / name).read_text(), substitutions))
    return {"raw": {name: old.digest(raw_dir / name) for name in
                    ("events.jsonl", "observed-events.json", "stderr.txt", "final.txt", "command.json", "http-receipts.jsonl")
                    if (raw_dir / name).exists()},
            "published": {name: old.digest(public / name) for name in
                         ("events.jsonl", "event-timestamps.json", "stderr.txt", "final.txt", "command.json", "http-receipts.jsonl")
                         if (public / name).exists()}}


def run_trial(task, trial, number, config, output):
    name = f"{number:02d}-r{trial['repetition']}-{task['id']}-{trial['condition']}"
    raw_dir = Path(config["work_root"]) / "raw" / name
    workspace = Path(config["work_root"]) / "workspaces" / name
    public = output / "runs" / name
    raw_dir.mkdir(parents=True, exist_ok=False)
    public.mkdir(parents=True, exist_ok=False)
    substitutions = [(str(workspace), "<WORKSPACE>"), (str(raw_dir), "<EVALUATOR_LOG>"),
                     (str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]
    record = {"run_id": name, **trial, "phase_timestamps": {"fixture_start": stamp()},
              "correct": False, "completed_correct": False, "completed": False, "failed": False,
              "timed_out": False, "usage": None, "mcp_calls": 0, "agent_elapsed_s": None}
    backend = server = worker = None
    events = observed = []
    try:
        base_commit, base_files = prepare(task, workspace)
        write_json(public / "base-files.json", base_files)
        record.update(base_commit=base_commit, base_tree_sha256=hashlib.sha256(
                      json.dumps(base_files, sort_keys=True).encode()).hexdigest())
        record["seed_grade"] = grade(task, workspace)
        if record["seed_grade"]["detail"] != task["seed_validation"]["base"]:
            raise RuntimeError("Unmodified baseline no longer produces its frozen feature-check result")
        record["phase_timestamps"]["fixture_end"] = stamp()
        service_url = None
        if trial["condition"] == "mcp":
            from semantic_search.server import create_server
            record["phase_timestamps"]["service_setup_start"] = stamp()
            backend, _ = recording_backend(workspace, task["source_root"], raw_dir)
            record["initial_index"] = backend.refresh()
            backend.search("Find a function implementation", max_results=1, token_budget=512)
            backend.start_watching()
            backend.record_requests = True
            server = create_server(backend, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            service_url = f"http://127.0.0.1:{server.server_port}"
            record["phase_timestamps"]["service_setup_end"] = stamp()
            record["service_setup_s"] = (record["phase_timestamps"]["service_setup_end"]["monotonic_s"]
                                         - record["phase_timestamps"]["service_setup_start"]["monotonic_s"])
        user_prompt = prompt(task, config["python"])
        record["prompt_sha256"] = hashlib.sha256(user_prompt.encode()).hexdigest()
        if record["prompt_sha256"] != config["prompt_sha256"][task["id"]]:
            raise ValueError("User prompt changed after freeze")
        command = cli_command(config, workspace, raw_dir / "final.txt", user_prompt, service_url, task["source_root"])
        write_json(raw_dir / "command.json", command)
        (public / "prompt.txt").write_text(public_redact(user_prompt, substitutions))
        print(f"Running {name}", flush=True)
        measurements, events, observed = run_agent(command, {**config, "source_root": task["source_root"]}, workspace, raw_dir)
        record.update(measurements)
        if backend:
            record["final_backend_status"] = backend.status()
    except Exception as error:
        record.update(infrastructure_error=f"{type(error).__name__}: {error}", failed=True)
    finally:
        record["phase_timestamps"]["shutdown_start"] = stamp()
        if server:
            server.shutdown()
            server.server_close()
        if worker:
            worker.join(5)
        if backend:
            backend.close()
        record["phase_timestamps"]["shutdown_end"] = stamp()
        record["artifact_sha256"] = public_artifacts(raw_dir, public, events, observed, substitutions)
        write_json(public / "result.json", public_redact(record, substitutions))
    if workspace.is_dir() and "base_tree_sha256" in record:
        try:
            record["phase_timestamps"]["grading_start"] = stamp()
            record["grade"] = grade(task, workspace)
            record["compatibility"] = compatibility(task, workspace, base_files, raw_dir)
            record["correct"] = record["grade"]["passed"] and record["compatibility"]["passed"]
            record["phase_timestamps"]["grading_end"] = stamp()
            old.git(workspace, "add", "--intent-to-add", ".")
            patch = old.git(workspace, "diff", "--binary", base_commit).stdout.decode()
            (public / "changes.patch").write_text(public_redact(patch, substitutions))
            record["changed_files"] = old.git(workspace, "diff", "--name-only", base_commit).stdout.decode().splitlines()
            record["patch_sha256"] = old.digest(public / "changes.patch")
        except Exception as error:
            record.update(grading_error=f"{type(error).__name__}: {error}", correct=False)
    record["completed_correct"] = bool(record["correct"] and record.get("completed") and not record.get("failed")
                                       and record.get("agent_exit_code") == 0 and not record.get("timed_out"))
    record["phase_timestamps"]["trial_end"] = stamp()
    write_json(public / "result.json", public_redact(record, substitutions))
    return public_redact(record, substitutions)


def summarize(records):
    output = {"trial_count": len(records), "conditions": {}, "pairs": []}
    for condition in ("baseline", "mcp"):
        rows = [row for row in records if row["condition"] == condition]
        tokens = [row["usage"]["total_tokens"] for row in rows if row.get("usage") and row["usage"].get("total_tokens") is not None]
        times = [row["agent_elapsed_s"] for row in rows if row.get("agent_elapsed_s") is not None]
        output["conditions"][condition] = {"runs": len(rows), "completed_correct": sum(row["completed_correct"] for row in rows),
            "correct": sum(row["correct"] for row in rows), "timed_out": sum(row.get("timed_out", False) for row in rows),
            "usage_observed_runs": len(tokens), "measured_total_tokens": sum(tokens) if tokens else None,
            "complete_inventory_total_tokens": sum(tokens) if len(tokens) == len(rows) and rows else None,
            "median_total_tokens": statistics.median(tokens) if tokens else None,
            "median_agent_elapsed_s": statistics.median(times) if times else None}
    pairs = {}
    for row in records:
        pairs.setdefault((row["task_id"], row["repetition"]), {})[row["condition"]] = row
    for (task_id, repetition), pair in pairs.items():
        if set(pair) != {"baseline", "mcp"}:
            continue
        baseline, mcp = pair["baseline"], pair["mcp"]
        if (baseline.get("base_tree_sha256") and mcp.get("base_tree_sha256")
                and (baseline["base_tree_sha256"] != mcp["base_tree_sha256"]
                     or baseline.get("prompt_sha256") != mcp.get("prompt_sha256"))):
            raise ValueError("Paired fixture or user prompts differ")
        row = {"task_id": task_id, "repetition": repetition,
               "both_completed_correct": baseline["completed_correct"] and mcp["completed_correct"]}
        for field in ("agent_elapsed_s",):
            b, m = baseline.get(field), mcp.get(field)
            row[field + "_change_pct"] = 100 * (m / b - 1) if b and m is not None else None
        b, m = baseline.get("usage"), mcp.get("usage")
        row["total_tokens_change_pct"] = 100 * (m["total_tokens"] / b["total_tokens"] - 1) if (
            b and m and b.get("total_tokens") and m.get("total_tokens") is not None) else None
        output["pairs"].append(row)
    return output


def run(output):
    config_path = output / "frozen-config.json"
    config = read_json(config_path)
    if (output / "runs").exists():
        raise ValueError("Automatic resume/retries are prohibited; runs already exist")
    manifest = validate_manifest(Path(config["manifest"]))
    tasks = {task["id"]: task for task in manifest["tasks"]}
    frozen_hash = old.digest(config_path)
    records = []
    for number, trial in enumerate(config["schedule"], 1):
        verify_runtime(config)
        if old.digest(config_path) != frozen_hash:
            raise ValueError("Frozen configuration changed during study")
        try:
            record = run_trial(tasks[trial["task_id"]], trial, number, config, output)
        except Exception as error:
            record = {**trial, "correct": False, "completed_correct": False, "failed": True,
                      "usage": None, "agent_elapsed_s": None,
                      "infrastructure_error": f"{type(error).__name__}: {error}"}
            directory = output / "runs" / f"{number:02d}-r{trial['repetition']}-{trial['task_id']}-{trial['condition']}"
            directory.mkdir(parents=True, exist_ok=True)
            if (directory / "result.json").exists():
                record.update(read_json(directory / "result.json"))
                record["infrastructure_error"] = f"{type(error).__name__}: {error}"
            write_json(directory / "result.json", record)
        records.append(record)
        write_json(output / "results.json", records)
        write_json(output / "summary.json", summarize(records))
    verify_runtime(config)
    print(f"Recorded all {len(records)} declared trials", flush=True)


def probe(common_path, output, work_root):
    """Explicit hosted diagnostic; local GPU/model calls are guarded to fail."""
    config = read_json(common_path)
    verify_runtime(config)
    if output.exists() or work_root.exists():
        raise ValueError("Transport probe needs fresh output/work paths")
    output.mkdir(parents=True)
    raw_dir, workspace = work_root / "raw", work_root / "workspace"
    raw_dir.mkdir(parents=True)
    (workspace / "src").mkdir(parents=True)
    (workspace / "src/probe.py").write_text(PROBE_SOURCE)
    (workspace / "loopback_smoke.py").write_text(LOOPBACK_SOURCE)
    old.git(workspace, "init", "-b", "main")
    old.git(workspace, "add", ".")
    old.git(workspace, "-c", "user.name=Transport probe", "-c", "user.email=probe@example.invalid",
            "commit", "-m", "Independent lexical probe")
    from semantic_search.server import create_server
    backend, guard = recording_backend(workspace, "src", raw_dir, model_rejecting=True)
    backend.search("workflow probe", retrieval="bm25", max_results=1, token_budget=512)
    backend.record_requests = True
    server = create_server(backend, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    user_prompt = ('Do not read or edit files, and do not run any commands except the exact one specified below. '
        'First use the registered semantic_search.search_code tool once, '
        'with query="workflow probe", retrieval="bm25", max_results=1, token_budget=512, include_dependencies=false. '
        'Verify its returned function workflow_probe returns MULTIFILE_PROBE_OK. After that tool finishes, run exactly '
        f'{shlex.quote(config["python"])} loopback_smoke.py in the current workspace. Its prewritten local socket test must print LOOPBACK_OK. '
        'Then state both results in one sentence. '
        'Do not create or message other agents. This is a separate transport diagnostic, not a coding task.')
    command = cli_command(config, workspace, raw_dir / "final.txt", user_prompt,
                          f"http://127.0.0.1:{server.server_port}", "src")
    write_json(raw_dir / "command.json", command)
    substitutions = [(str(work_root), "<PROBE>"), (str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]
    events = observed = []
    record = {"classification": "Separate hosted transport diagnostic; never pooled with sixteen workflow trials",
              "common_runtime_sha256": old.digest(common_path), "local_model_inference": False,
              "initial_file_hashes": full_tree_hashes(workspace), "probe_source": PROBE_SOURCE,
              "probe_source_sha256": hashlib.sha256(PROBE_SOURCE.encode()).hexdigest(),
              "loopback_script": LOOPBACK_SOURCE, "loopback_script_sha256": hashlib.sha256(LOOPBACK_SOURCE.encode()).hexdigest()}
    try:
        measurements, events, observed = run_agent(command, {**config, "source_root": "src"}, workspace, raw_dir,
                                                  timeout=config["probe_timeout_s"])
        record.update(measurements)
    except Exception as error:
        record.update(failed=True, completed=False, usage=None,
                      infrastructure_error=f"{type(error).__name__}: {error}")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)
        backend.close()
        record["artifact_sha256"] = public_artifacts(raw_dir, output, events, observed, substitutions)
    receipts = [json.loads(line) for line in (raw_dir / "http-receipts.jsonl").read_text().splitlines()] if (raw_dir / "http-receipts.jsonl").exists() else []
    matched = probe_matches(events, receipts)
    loopback = controlled_loopback_commands(events, config["python"], workspace)
    command_items = {event["item"].get("id", str(index)) for index, event in enumerate(events)
                     if event.get("item", {}).get("type") == "command_execution"}
    file_items = {event["item"].get("id", str(index)) for index, event in enumerate(events)
                  if event.get("item", {}).get("type") == "file_change"}
    record.update(successful_expected_mcp_calls=len({row["item_id"] for row in matched}),
                  successful_expected_http_receipts=len({row["receipt_number"] for row in matched}),
                  matched_calls=matched, observed_command_items=len(command_items), observed_file_change_items=len(file_items),
                  matched_loopback_commands=loopback,
                  final_file_hashes=full_tree_hashes(workspace), forbidden_local_model_calls=guard.calls)
    record["completed_correct"] = bool(record.get("completed") and not record.get("failed") and not record.get("timed_out")
         and record.get("agent_exit_code") == 0 and len(matched) == 1 and record.get("mcp_calls") == 1
         and record.get("reported_turns") == 1 and record.get("usage") is not None
         and record["usage"].get("total_tokens") is not None
         and len(command_items) == 1 and len(loopback) == 1 and not file_items
         and record["initial_file_hashes"] == record["final_file_hashes"]
         and guard.calls == 0)
    write_json(output / "result.json", public_redact(record, substitutions))
    verify_runtime(config)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "freeze-common", "probe", "freeze", "run"))
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--common-config", type=Path)
    parser.add_argument("--probe-result", type=Path)
    args = parser.parse_args(argv)
    if args.action == "validate":
        data = validate_manifest(args.manifest)
        print(json.dumps({"tasks": [task["id"] for task in data["tasks"]], "schedule": schedule(data["tasks"]),
                          "hosted_calls_started": 0, "gpu_inference_started": 0}, indent=2))
    elif args.action == "freeze-common":
        if args.output is None:
            parser.error("freeze-common requires --output")
        freeze_common(args.output.resolve())
    elif args.action in ("probe", "freeze"):
        if any(value is None for value in (args.output, args.work_root, args.common_config)):
            parser.error("probe/freeze require --output, --work-root and --common-config")
        if args.action == "probe":
            probe(args.common_config.resolve(), args.output.resolve(), args.work_root.resolve())
        else:
            if args.probe_result is None:
                parser.error("freeze requires --probe-result")
            freeze(args.output.resolve(), args.work_root.resolve(), args.common_config.resolve(),
                   args.probe_result.resolve(), args.manifest)
    else:
        if args.output is None:
            parser.error("run requires --output")
        run(args.output.resolve())


if __name__ == "__main__":
    main()
