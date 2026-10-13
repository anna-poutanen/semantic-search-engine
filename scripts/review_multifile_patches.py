#!/usr/bin/env python3
"""CPU checks of a terminal coding patch in fresh evaluator-only copies.

Agent-authored new tests are exercised against candidate and restored upstream
production source. This is supplemental regression-quality evidence, never a
replacement grade. It does not import models, edit task assets, or access active
agent workspaces.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(repo, *args, input=None):
    return subprocess.run(["git", "-C", str(repo), *args], input=input,
                          capture_output=True, check=True).stdout


def write_json(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def counts(stdout):
    fields = ("passed", "skipped", "failed", "errors", "xfailed", "xpassed", "deselected")
    lines = [line for line in stdout.splitlines()
             if re.search(r"\b\d+ (?:passed|failed|error|errors|skipped|xfailed|xpassed|deselected)\b", line)
             and " in " in line]
    if not lines:
        return None
    result = dict.fromkeys(fields, 0)
    for number, name in re.findall(r"\b(\d+) (passed|skipped|failed|errors?|xfailed|xpassed|deselected)\b", lines[-1]):
        result["errors" if name == "error" else name] = int(number)
    return result


def run_tests(workspace, task, files, log_dir, label):
    command = [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "--tb=short", *files]
    env = dict(os.environ)
    env["PYTHONPATH"] = str((workspace / task["source_root"]).parent)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_ADDOPTS"] = ('--tb=short -W "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning" '
                            '-W "ignore:The `cancellable=` keyword argument:DeprecationWarning"')
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=workspace, env=env, capture_output=True,
                                text=True, timeout=180)
        stdout, stderr, exit_code, timed_out = result.stdout, result.stderr, result.returncode, False
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or b""
        stderr = error.stderr or b""
        stdout = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
        exit_code, timed_out = None, True
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{label}.stdout.txt").write_text(stdout)
    (log_dir / f"{label}.stderr.txt").write_text(stderr)
    command = [arg.replace(str(ROOT), "<PROJECT>") for arg in command]
    return {"command": command, "exit_code": exit_code, "timed_out": timed_out,
            "summary": counts(stdout), "elapsed_s": time.monotonic() - started,
            "stdout_sha256": digest(log_dir / f"{label}.stdout.txt"),
            "stderr_sha256": digest(log_dir / f"{label}.stderr.txt")}


def inspect_run(output, run_id, work_root):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", run_id):
        raise ValueError("Invalid run identity")
    run = output / "runs" / run_id
    result = json.loads((run / "result.json").read_text())
    inventory = json.loads((output / "results.json").read_text())
    rows = inventory["runs"] if isinstance(inventory, dict) and "runs" in inventory else inventory
    if not isinstance(rows, list) or not any(row.get("run_id") == run_id for row in rows):
        raise ValueError("Run is not in the terminal result inventory")
    if result.get("run_id") != run_id or result.get("phase_timestamps", {}).get("trial_end") is None:
        raise ValueError("Run has no terminal evaluator record")
    tasks = json.loads((output / "tasks.json").read_text())
    tasks = tasks["tasks"] if isinstance(tasks, dict) else tasks
    task = next(task for task in tasks if task["id"] == result["task_id"])
    patch = run / "changes.patch"
    if digest(patch) != result["patch_sha256"]:
        raise ValueError("Captured patch hash differs")
    base_files = json.loads((run / "base-files.json").read_text())
    new_tests = [name for name in result.get("changed_files", [])
                 if name.startswith("tests/") and name.endswith(".py") and name not in base_files]
    record = {"run_id": run_id, "task_id": result["task_id"], "condition": result["condition"],
              "review_script_sha256": digest(Path(__file__)),
              "patch_sha256": result["patch_sha256"], "method": "Fresh pinned archive plus captured patch; authored new tests also run with original production files restored",
              "classification": "Post-output supplementary regression-test evidence; frozen grades unchanged",
              "selected_grade": result.get("grade", {}).get("passed"),
              "upstream_grade": result.get("compatibility", {}).get("passed"),
              "new_test_files": new_tests}
    if not new_tests:
        record["regression_check"] = "No added Python regression files to exercise"
        return record
    destination = work_root / run_id
    if destination.exists():
        shutil.rmtree(destination)
    candidate, original = destination / "candidate", destination / "restored-production"
    candidate.mkdir(parents=True)
    corpus = ROOT / task["corpus"]
    archive = git(corpus, "archive", task["revision"])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(candidate, filter="data")
    for name, expected in base_files.items():
        if digest(candidate / name) != expected:
            raise ValueError(f"Pinned archive differs from recorded base: {name}")
    git(candidate, "init", "--quiet")
    git(candidate, "apply", "--binary", "-", input=patch.read_bytes())
    shutil.copytree(candidate, original, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    production_files = [name for name in result.get("changed_files", [])
                        if name.startswith(task["source_root"].rstrip("/") + "/")]
    for name in production_files:
        target = original / name
        if name in base_files:
            target.write_bytes(git(corpus, "show", f"{task['revision']}:{name}"))
        elif target.exists():
            target.unlink()
    record["changed_production_files"] = production_files
    record["candidate_new_tests"] = run_tests(candidate, task, new_tests, destination / "logs", "candidate")
    record["restored_production_new_tests"] = run_tests(original, task, new_tests, destination / "logs", "restored-production")
    record["source_restoration_only"] = True
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/multifile-workflows-2026-10-13")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--work-root", type=Path, default=ROOT / ".cache/multifile-patch-review")
    args = parser.parse_args()
    record = inspect_run(args.output.resolve(), args.run_id, args.work_root.resolve())
    destination = args.output / "patch-review-checks" / f"{args.run_id}.json"
    write_json(destination, record)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
