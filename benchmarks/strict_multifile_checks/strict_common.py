"""Prospective CPU grading: original checks plus previously accepted edge probes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("manifest.json")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def bindings(task_id):
    manifest = json.loads(MANIFEST.read_text())
    require(manifest["version"] == 1 and task_id in manifest["tasks"], "Unknown strict task")
    for name, expected in manifest["input_sha256"].items():
        path = (ROOT / name).resolve(strict=True)
        require(path.is_relative_to(ROOT) and digest(path) == expected, "Strict grading input changed: " + name)
    task = manifest["tasks"][task_id]
    original = json.loads((ROOT / "benchmarks/coding_workflows_multifile.json").read_text())
    frozen = next(row for row in original["tasks"] if row["id"] == task_id)
    require(hashlib.sha256(frozen["prompt"].encode()).hexdigest() == task["prompt_sha256"], "Original contract changed")
    return manifest, task


def clean_environment(repo, source_root):
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(name, None)
    env["PYTHONPATH"] = str((repo / source_root).parent)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_component(specification, repo, source_root, *, runner=subprocess.run):
    command = [sys.executable, str(ROOT / specification["script"]), "--repo", str(repo)]
    try:
        result = runner(command, cwd=repo, env=clean_environment(repo, source_root), capture_output=True,
                        text=True, timeout=specification["timeout_s"])
        stdout, stderr, exit_code, timed_out = result.stdout, result.stderr, result.returncode, False
    except subprocess.TimeoutExpired as error:
        stdout, stderr, exit_code, timed_out = error.stdout or "", error.stderr or "", None, True
        stdout = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
    record = {"script": specification["script"], "script_sha256": digest(ROOT / specification["script"]),
              "command": command, "exit_code": exit_code, "timed_out": timed_out,
              "stdout": stdout, "stderr": stderr, "passed": False, "result": None}
    try:
        payload = json.loads(stdout)
        require(isinstance(payload, dict), "Component must return a JSON object")
        if specification["kind"] == "original":
            require(all(type(payload.get(name)) is int and payload[name] >= 0 for name in
                        ("tests_run", "failures", "errors", "skipped")) and type(payload.get("passed")) is bool,
                    "Original unittest receipt must retain exact typed counters")
            record["passed"] = (payload["passed"] and payload["tests_run"] > 0 and payload["failures"] == 0
                                and payload["errors"] == 0 and payload["skipped"] == 0)
        else:
            require(payload.get("version") == 3 and isinstance(payload.get("cases"), dict)
                    and set(payload["cases"]) == set(specification["case_ids"]), "Exact accepted supplemental cases required")
            require(payload.get("probe_sha256") == record["script_sha256"], "Supplemental source receipt changed")
            require(all(isinstance(case, dict) and type(case.get("passed")) is bool for case in payload["cases"].values()),
                    "Supplemental case grades must be explicit booleans")
            record["passed"] = all(case["passed"] for case in payload["cases"].values())
        record["result"] = payload
        record["passed"] = record["passed"] and exit_code == 0 and not timed_out
    except (ValueError, TypeError, KeyError) as error:
        record["receipt_error"] = {"type": type(error).__name__, "message": str(error)}
        record["passed"] = False
    return record


def grade(task_id, repo, *, runner=subprocess.run):
    manifest, task = bindings(task_id)
    repo = Path(repo).resolve(strict=True)
    require((repo / task["source_root"]).is_dir(), "Actual task source checkout required")
    components = [run_component(component, repo, task["source_root"], runner=runner)
                  for component in task["components"]]
    original = components[0]["result"] or {}
    supplemental = [case for component in components[1:] if component["result"] is not None
                    for case in component["result"]["cases"].values()]
    return {"version": 1, "classification": "Prospective strict grade combining original frozen checks and accepted prior contract probes",
            "task_id": task_id, "strict_manifest_sha256": digest(MANIFEST), "model_calls": 0,
            "tests_run": original.get("tests_run", 0) + len(supplemental),
            "failures": original.get("failures", 0) + sum(not case["passed"] for case in supplemental),
            "errors": original.get("errors", 0) + sum("receipt_error" in component for component in components),
            "skipped": original.get("skipped", 0), "passed": all(component["passed"] for component in components),
            "original_grade_unchanged": original, "components": components}


def main(task_id):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    result = grade(task_id, args.repo)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["passed"] else 1
