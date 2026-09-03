"""Paired, isolated coding-agent trials; provider usage is never estimated.

Freeze first, then run exactly the declared schedule. Raw agent events remain
in evaluator storage; publish tool/answer events with local paths redacted and
without reasoning messages. The withheld checkers are not a security boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import platform
from pathlib import Path
import shutil
import signal
import statistics
import subprocess
import sys
import tarfile
import threading
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path("benchmarks/coding_workflows.json")
COMMON_CONFIG = {
    "model_reasoning_effort": "ultra", "service_tier": "default",
    "approval_policy": "never", "web_search": "disabled",
    "project_doc_max_bytes": 0, "skills.bundled.enabled": False,
    "skills.include_instructions": False,
    "suppress_unstable_features_warning": True,
    **{f"features.{name}": False for name in (
        "plugins", "remote_plugin", "apps", "hooks", "memories", "multi_agent",
        "multi_agent_v2", "browser_use", "browser_use_external", "computer_use",
        "image_generation", "workspace_dependencies", "tool_suggest", "skill_search")},
    "features.skip_host_skill_discovery": True,
}
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                "output_tokens", "reasoning_output_tokens")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def git(repo, *args, **kwargs):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, **kwargs)


def validate_manifest(manifest, root=ROOT):
    """Reject a changed seed/oracle before any agent execution."""
    seen = set()
    for task in manifest["tasks"]:
        if task["id"] in seen:
            raise ValueError("Duplicate task ID")
        seen.add(task["id"])
        checks = [(task["preparation_patch"], task["preparation_patch_sha256"]),
                  (task["verification"]["script"], task["verification"]["sha256"]),
                  (task["verification"]["shared_helper"],
                   task["verification"]["shared_helper_sha256"])]
        for name, expected in checks:
            path = (Path(root) / name).resolve()
            if not path.is_relative_to(Path(root).resolve()) or digest(path) != expected:
                raise ValueError(f"Frozen task artifact changed: {name}")
    if not seen:
        raise ValueError("No workflow tasks")
    return manifest


def paired_schedule(tasks):
    return [(task, condition) for i, task in enumerate(tasks)
            for condition in (("baseline", "mcp") if i % 2 == 0 else ("mcp", "baseline"))]


def common_prompt(task, python_path):
    return ("Work only in the current repository checkout. Inspect its source and tests, "
            "make a focused repair, and add a regression test. Use the available tools "
            "as you find useful. Do not access other repositories, parent directories, "
            "home-directory files, evaluation materials, or the network. Do not install "
            "dependencies or commit changes. Run relevant local checks when possible; "
            f"the existing Python interpreter is {python_path}. "
            "Finish with a concise description of the change and checks run.\n\n" + task["prompt"])


def toml_value(value):
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items()) + "}"
    return json.dumps(value)


def usage_summary(usage):
    if usage is None:
        return None
    result = {key: usage.get(key) for key in USAGE_FIELDS}
    for key, value in result.items():
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"Invalid reported usage: {key}")
    inp, out, cached = (result[k] for k in ("input_tokens", "output_tokens", "cached_input_tokens"))
    result["total_tokens"] = inp + out if inp is not None and out is not None else None
    if inp is not None and cached is not None and cached > inp:
        raise ValueError("Cached input exceeds reported input")
    result["uncached_input_tokens"] = inp - cached if inp is not None and cached is not None else None
    return result


def parse_events(events):
    turns = [e["usage"] for e in events if e.get("type") == "turn.completed"
             and isinstance(e.get("usage"), dict)]
    # Each completed turn reports that turn's usage. Preserve missing fields,
    # rather than treating an unavailable measurement as zero.
    usage = None
    if turns:
        usage = {key: sum(turn[key] for turn in turns) if all(
            type(turn.get(key)) is int for turn in turns) else None for key in USAGE_FIELDS}
        usage = usage_summary(usage)
    calls = {}
    commands = {}
    for index, event in enumerate(events):
        item = event.get("item", {})
        if event.get("type") != "item.completed":
            continue
        key = item.get("id", f"event-{index}")
        if item.get("type") == "mcp_tool_call":
            calls[key] = item
        if item.get("type") == "command_execution":
            commands[key] = item
    return {"usage": usage, "completed": any(e.get("type") == "turn.completed" for e in events),
            "failed": any(e.get("type") == "turn.failed" for e in events),
            "mcp_calls": len(calls), "command_calls": len(commands),
            "reported_turns": len(turns), "mcp_items": list(calls.values()),
            "errors": [e for e in events if e.get("type") in ("error", "turn.failed")]}


def redact(value, substitutions):
    if isinstance(value, str):
        for before, after in substitutions:
            value = value.replace(before, after)
        return value
    if isinstance(value, list):
        return [redact(v, substitutions) for v in value]
    if isinstance(value, dict):
        return {k: redact(v, substitutions) for k, v in value.items()}
    return value


def tree_hashes(workspace):
    paths = git(workspace, "ls-files", "-z").stdout.decode().split("\0")
    return {name: digest(workspace / name) for name in paths
            if name and (workspace / name).is_file() and not (workspace / name).is_symlink()}


def prepare_workspace(task, workspace):
    workspace.mkdir(parents=True, exist_ok=False)
    archive = git(ROOT / task["corpus"], "archive", task["revision"]).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(workspace, filter="data")
    # Establish a repository boundary before git apply: fixtures live beneath
    # the evaluator checkout and must never inherit its Git root.
    git(workspace, "init", "-b", "main")
    subprocess.run(["git", "apply", str(ROOT / task["preparation_patch"])],
                   cwd=workspace, check=True, capture_output=True)
    git(workspace, "add", ".")
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-10-12T12:00:00+00:00",
           "GIT_COMMITTER_DATE": "2026-10-12T12:00:00+00:00"}
    git(workspace, "-c", "user.name=Workflow fixture", "-c",
        "user.email=workflow-fixture@example.invalid", "commit", "-m",
        "Prepared coding task", env=env)
    return git(workspace, "rev-parse", "HEAD").stdout.decode().strip(), tree_hashes(workspace)


def cli_command(config, workspace, final_path, prompt, service_url=None, source_root=None):
    command = [config["cli_binary"], "--no-daemon", "exec", "--ignore-user-config",
               "--ignore-rules", "--strict-config", "--ephemeral", "--json", "--color", "never",
               "--sandbox", "workspace-write", "-m", config["model"], "-C", str(workspace),
               "-o", str(final_path)]
    settings = dict(config["common_config"])
    if source_root:
        settings["shell_environment_policy.set"] = {
            "PYTHONPATH": str((workspace / source_root).parent),
            "PYTHONDONTWRITEBYTECODE": "1"}
    if service_url is not None:
        settings.update({
            "mcp_servers.semantic_search.command": config["python"],
            "mcp_servers.semantic_search.args": ["-m", "semantic_search.mcp_server", "--server", service_url],
            "mcp_servers.semantic_search.cwd": str(ROOT),
            "mcp_servers.semantic_search.env": {"PYTHONPATH": str(ROOT / "src")},
            "mcp_servers.semantic_search.enabled_tools": ["search_code"],
            "mcp_servers.semantic_search.required": True,
            "mcp_servers.semantic_search.startup_timeout_sec": 20,
            "mcp_servers.semantic_search.tool_timeout_sec": 180,
            "mcp_servers.semantic_search.default_tools_approval_mode": "auto",
        })
    for key, value in settings.items():
        command.extend(["-c", f"{key}={toml_value(value)}"])
    command.append(prompt)
    return command


def grade(task, workspace):
    start = time.perf_counter()
    command = [sys.executable, str(ROOT / task["verification"]["script"]),
               *[arg.replace("{workspace}", str(workspace)) for arg in task["verification"]["args"]]]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=task["verification"]["timeout_s"])
        try:
            detail = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            detail = None
        return {"passed": result.returncode == 0 and detail == task["seed_validation"]["reference"],
                "exit_code": result.returncode, "detail": detail,
                "stdout": result.stdout, "stderr": result.stderr,
                "elapsed_s": time.perf_counter() - start}
    except subprocess.TimeoutExpired:
        return {"passed": False, "exit_code": None, "detail": None,
                "error": "verifier_timeout", "elapsed_s": time.perf_counter() - start}


def freeze(output, work_root, timeout=600):
    manifest = validate_manifest(json.loads((ROOT / MANIFEST).read_text()))
    output.mkdir(parents=True, exist_ok=False)
    cli = shutil.which("codex")
    if not cli:
        raise RuntimeError("Codex CLI not found")
    artifacts = [MANIFEST, Path("src/semantic_search/workflows.py"), Path("requirements-mac.lock"),
                 Path("requirements-workflows.lock"),
                 Path("docs/coding-workflow-protocol.md"),
                 *Path("src/semantic_search").glob("*.py")]
    artifacts.extend(Path(t["preparation_patch"]) for t in manifest["tasks"])
    artifacts.extend(Path(t["verification"]["script"]) for t in manifest["tasks"])
    artifacts.append(Path("benchmarks/workflow_checks/common.py"))
    python = str(Path(sys.executable).absolute())
    from semantic_search.embeddings import QwenEmbedder
    auth_status = subprocess.run([cli, "login", "status"], check=True, capture_output=True, text=True)
    if "ChatGPT" not in auth_status.stdout + auth_status.stderr:
        raise RuntimeError("Pilot requires the already authenticated ChatGPT login")
    config = {
        "schema_version": 1, "frozen_at": datetime.now(timezone.utc).isoformat(),
        "runtime_git_revision": git(ROOT, "rev-parse", "HEAD").stdout.decode().strip(),
        "artifact_sha256": {str(p): digest(ROOT / p) for p in sorted(set(artifacts))},
        "manifest": str(MANIFEST), "cli_binary": cli, "cli_sha256": digest(cli),
        "cli_version": subprocess.run([cli, "--version"], check=True, capture_output=True,
                                      text=True).stdout.strip(),
        "python": python, "python_version": sys.version,
        "embedding_model": QwenEmbedder(progress=False).metadata,
        "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())),
        "host": {"platform": platform.platform(), "machine": platform.machine(),
                 **{key: subprocess.run(["sysctl", "-n", key], check=True, capture_output=True,
                                         text=True).stdout.strip() for key in
                    ("hw.model", "hw.memsize", "machdep.cpu.brand_string")}},
        "model": "gpt-6.1-sol", "common_config": COMMON_CONFIG,
        "sandbox": "workspace-write", "auth": "existing ChatGPT login; no credentials copied",
        "auth_environment": "OPENAI_API_KEY, CODEX_API_KEY, CODEX_ACCESS_TOKEN removed from agent process environment",
        "shell_environment": "PYTHONPATH=checkout package parent; PYTHONDONTWRITEBYTECODE=1, identical strategy in both conditions",
        "timeout_s": timeout, "repeats_per_task_condition": 1,
        "work_root": str(work_root),
        "schedule": [{"task_id": task["id"], "condition": condition}
                     for task, condition in paired_schedule(manifest["tasks"])],
        "prompt_sha256": {task["id"]: hashlib.sha256(common_prompt(task, python).encode()).hexdigest()
                          for task in manifest["tasks"]},
        "mcp": {"tool": "search_code", "required": True,
                "defaults": {"retrieval": "dense", "rerank": False, "max_results": 8,
                             "source_budget": 12000, "token_budget": 4096, "include_dependencies": True},
                "agent_can_override_arguments": True, "h2o_running": False,
                "startup_timeout_s": 20, "tool_timeout_s": 180,
                "warm_setup_excluded_from_agent_time": True,
                "watch_interval_s": 1.0, "watch_debounce_s": .5},
        "timing": "CLI launch to process exit; includes adapter startup, model waits and all agent tools. Setup and grading separate.",
        "usage": "Reported turn.completed usage; total=input+output. Cached input and reasoning output are subcategories, not extra tokens.",
        "retry_policy": "None; retain every declared trial including timeout, startup and verifier failures.",
        "correctness": "External selected behavioral checks, not full upstream suites; completion recorded separately.",
    }
    write_json(output / "frozen-config.json", config)
    shutil.copyfile(ROOT / MANIFEST, output / "tasks.json")
    preflight = ROOT / ".cache/workflow-independent-review/review.json"
    if preflight.exists():
        write_json(output / "preflight.json", redact(json.loads(preflight.read_text()),
                   [(str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]))
    print(f"Frozen {len(config['schedule'])} trials: {output}", flush=True)


def run_trial(task, condition, number, config, output):
    name = f"{number:02d}-{task['id']}-{condition}"
    raw_dir = Path(config["work_root"]) / "raw" / name
    workspace = Path(config["work_root"]) / "workspaces" / name
    public = output / "runs" / name
    raw_dir.mkdir(parents=True, exist_ok=False)
    public.mkdir(parents=True, exist_ok=False)
    substitutions = [(str(workspace), "<WORKSPACE>"), (str(raw_dir), "<EVALUATOR_LOG>"),
                     (str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]
    began = time.perf_counter()
    print(f"Preparing {name}", flush=True)
    base_commit, base_files = prepare_workspace(task, workspace)
    write_json(public / "base-files.json", base_files)
    seed_grade = grade(task, workspace)
    if seed_grade["exit_code"] != 1 or seed_grade["detail"] != task["seed_validation"]["base"]:
        raise RuntimeError(f"Seed does not reproduce its declared failures: {task['id']}")
    backend = server = thread = None
    record = {"run_id": name, "task_id": task["id"], "condition": condition,
              "base_commit": base_commit, "base_tree_sha256": hashlib.sha256(
                  json.dumps(base_files, sort_keys=True).encode()).hexdigest(),
              "seed_grade": seed_grade, "prompt_sha256": config["prompt_sha256"][task["id"]],
              "setup_s": None, "agent_elapsed_s": None, "agent_exit_code": None,
              "timed_out": False, "usage": None, "correct": False,
              "completed": False, "failed": False, "mcp_calls": 0, "completed_correct": False}
    try:
        service_url = None
        if condition == "mcp":
            from semantic_search.backend import SearchBackend
            from semantic_search.server import create_server
            backend = SearchBackend(workspace, task["source_root"],
                                    db_path=raw_dir / "index.sqlite3")
            record["initial_index"] = backend.refresh()
            backend.search("Find a function implementation", max_results=1, token_budget=512)
            backend.start_watching()
            server = create_server(backend, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            service_url = f"http://127.0.0.1:{server.server_port}"
        prompt = common_prompt(task, config["python"])
        command = cli_command(config, workspace, raw_dir / "final.txt", prompt, service_url,
                              source_root=task["source_root"])
        write_json(raw_dir / "command.json", command)
        (public / "prompt.txt").write_text(redact(prompt, substitutions))
        record["setup_s"] = time.perf_counter() - began
        print(f"Running {name}; setup {record['setup_s']:.1f}s", flush=True)
        start = time.perf_counter()
        agent_env = dict(os.environ)
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
            agent_env.pop(key, None)
        with (raw_dir / "events.jsonl").open("w") as stdout, (raw_dir / "stderr.txt").open("w") as stderr:
            process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True, env=agent_env)
            try:
                process.wait(timeout=config["timeout_s"])
            except subprocess.TimeoutExpired:
                record["timed_out"] = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        record["agent_elapsed_s"] = time.perf_counter() - start
        record["agent_exit_code"] = process.returncode
        events = []
        invalid_lines = []
        for line in (raw_dir / "events.jsonl").read_text().splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                invalid_lines.append(line)
        record.update(parse_events(events))
        record["invalid_event_lines"] = invalid_lines
        with (public / "events.jsonl").open("w") as stream:
            for event in events:
                if event.get("item", {}).get("type") != "reasoning":
                    stream.write(json.dumps(redact(event, substitutions)) + "\n")
        (public / "stderr.txt").write_text(redact((raw_dir / "stderr.txt").read_text(), substitutions))
        if (raw_dir / "final.txt").exists():
            (public / "final.txt").write_text(redact((raw_dir / "final.txt").read_text(), substitutions))
        record["raw_events_sha256"] = digest(raw_dir / "events.jsonl")
        record["published_events_sha256"] = digest(public / "events.jsonl")
        if backend:
            record["final_backend_status"] = backend.status()
    except Exception as error:
        record["infrastructure_error"] = f"{type(error).__name__}: {error}"
        if record["setup_s"] is None:
            record["setup_s"] = time.perf_counter() - began
    finally:
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        if backend:
            backend.close()
    # Persist measurements before grading or Git capture can fail. The outer
    # inventory retains these partial observations on infrastructure errors.
    write_json(public / "result.json", redact(record, substitutions))
    record["grade"] = grade(task, workspace)
    record["correct"] = record["grade"]["passed"]
    write_json(public / "result.json", redact(record, substitutions))
    # Capture newly added regression tests as well as changes to tracked files.
    git(workspace, "add", "--intent-to-add", ".")
    patch = git(workspace, "diff", "--binary", base_commit).stdout.decode()
    (public / "changes.patch").write_text(redact(patch, substitutions))
    record["patch_sha256"] = digest(public / "changes.patch")
    record["changed_files"] = git(workspace, "diff", "--name-only", base_commit).stdout.decode().splitlines()
    record["end_to_end_s"] = time.perf_counter() - began
    record["completed_correct"] = bool(record["correct"] and record["completed"]
                                       and not record["failed"] and record["agent_exit_code"] == 0
                                       and not record["timed_out"])
    write_json(public / "result.json", redact(record, substitutions))
    print(f"Finished {name}: correct={record['correct']}, {record['agent_elapsed_s']}s, usage={record['usage']}", flush=True)
    return redact(record, substitutions)


def summarize(records):
    result = {"trial_count": len(records), "conditions": {}, "pairs": []}
    for condition in ("baseline", "mcp"):
        rows = [r for r in records if r["condition"] == condition]
        elapsed = [r["agent_elapsed_s"] for r in rows if r["agent_elapsed_s"] is not None]
        totals = [r["usage"]["total_tokens"] for r in rows if r["usage"] is not None
                  and r["usage"]["total_tokens"] is not None]
        result["conditions"][condition] = {
            "runs": len(rows), "correct": sum(r["correct"] for r in rows),
            "completed_correct": sum(r["completed_correct"] for r in rows),
            "usage_observed": len(totals), "total_tokens": sum(totals) if totals else None,
            "median_total_tokens": statistics.median(totals) if totals else None,
            "median_agent_elapsed_s": statistics.median(elapsed) if elapsed else None,
            "total_agent_elapsed_s": sum(elapsed) if elapsed else None,
            "mcp_calls": sum(r["mcp_calls"] for r in rows),
            "runs_with_mcp_calls": sum(r["mcp_calls"] > 0 for r in rows),
            "timed_out": sum(r["timed_out"] for r in rows)}
    by_task = {}
    for record in records:
        by_task.setdefault(record["task_id"], {})[record["condition"]] = record
    for task_id, pair in by_task.items():
        if set(pair) != {"baseline", "mcp"}:
            continue
        baseline, mcp = pair["baseline"], pair["mcp"]
        comparable = baseline.get("base_tree_sha256") is not None and mcp.get("base_tree_sha256") is not None
        if comparable and (baseline["base_tree_sha256"] != mcp["base_tree_sha256"] or baseline["prompt_sha256"] != mcp["prompt_sha256"]):
            raise ValueError(f"Pair differs in source or prompt: {task_id}")
        row = {"task_id": task_id, "both_completed_correct": baseline["completed_correct"] and mcp["completed_correct"]}
        for key in ("agent_elapsed_s",):
            b, m = baseline[key], mcp[key]
            row[key + "_change_pct"] = 100 * (m / b - 1) if b and m is not None else None
        b, m = baseline["usage"], mcp["usage"]
        row["total_tokens_change_pct"] = 100 * (m["total_tokens"] / b["total_tokens"] - 1) if (
            b and m and b["total_tokens"] and m["total_tokens"] is not None) else None
        result["pairs"].append(row)
    return result


def run(output):
    config_path = output / "frozen-config.json"
    config_hash = digest(config_path)
    config = json.loads(config_path.read_text())
    if (output / "runs").exists():
        raise RuntimeError("Run output already exists; automatic retry/resume is prohibited")
    def check_artifacts():
        for name, expected in config["artifact_sha256"].items():
            if digest(ROOT / name) != expected:
                raise ValueError(f"Runtime artifact changed since freeze: {name}")
    check_artifacts()
    if digest(config["cli_binary"]) != config["cli_sha256"]:
        raise ValueError("Codex binary changed since freeze")
    manifest = validate_manifest(json.loads((ROOT / config["manifest"]).read_text()))
    tasks = {task["id"]: task for task in manifest["tasks"]}
    records = []
    for number, trial in enumerate(config["schedule"], start=1):
        if digest(config_path) != config_hash:
            raise ValueError("Frozen configuration changed during trials")
        check_artifacts()
        try:
            record = run_trial(tasks[trial["task_id"]], trial["condition"], number, config, output)
        except Exception as error:
            # Fixture/capture/verifier infrastructure failures must not erase
            # attempts or silently cancel the rest of the declared schedule.
            name = f"{number:02d}-{trial['task_id']}-{trial['condition']}"
            record = {"run_id": name, **trial, "base_tree_sha256": None,
                      "prompt_sha256": config["prompt_sha256"][trial["task_id"]],
                      "correct": False, "completed_correct": False, "completed": False,
                      "failed": True, "timed_out": False, "usage": None, "mcp_calls": 0,
                      "agent_elapsed_s": None, "agent_exit_code": None,
                      "infrastructure_error": f"{type(error).__name__}: {error}"}
            public = output / "runs" / name
            public.mkdir(parents=True, exist_ok=True)
            if (public / "result.json").exists():
                record.update(json.loads((public / "result.json").read_text()))
                record["infrastructure_error"] = f"{type(error).__name__}: {error}"
            write_json(public / "result.json", record)
            print(f"Failed infrastructure: {name}: {error}", flush=True)
        records.append(record)
        check_artifacts()
        write_json(output / "results.json", records)
        write_json(output / "summary.json", summarize(records))
    print(f"All {len(records)} declared trials recorded", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    if args.action == "freeze":
        if args.work_root is None:
            parser.error("freeze requires --work-root")
        freeze(args.output.resolve(), args.work_root.resolve(), args.timeout)
    else:
        run(args.output.resolve())


if __name__ == "__main__":
    main()
