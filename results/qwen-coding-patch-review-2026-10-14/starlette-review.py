"""Review a terminal Starlette patch in new pinned CPU evaluator copies."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "results/qwen-coding-patch-review-2026-10-14"
RAW = ROOT / ".cache/qwen-starlette-patch-review-2026-10-14"
STUDY = ROOT / "results/qwen-coding-workflows-2026-10-14"
COUNT_FIELDS = ("passed", "skipped", "failed", "errors", "xfailed", "xpassed", "deselected")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redacted(value), indent=2, sort_keys=True) + "\n")


def redact(text):
    return text.replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")


def redacted(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redacted(item) for item in value]
    return value


def tree(path):
    return {str(file.relative_to(path)): digest(file) for file in sorted(path.rglob("*"))
            if file.is_file() and ".git" not in file.relative_to(path).parts
            and "__pycache__" not in file.relative_to(path).parts
            and ".pytest_cache" not in file.relative_to(path).parts}


def archive(task, destination):
    destination.mkdir(parents=True, exist_ok=False)
    data = subprocess.run(["git", "-C", str(ROOT / task["corpus"]), "archive", task["revision"]],
                          check=True, stdout=subprocess.PIPE).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as stream:
        stream.extractall(destination, filter="data")


def counts(text):
    lines = [line for line in text.splitlines() if re.search(
        r"\b\d+ (?:passed|failed|skipped|xfailed|xpassed|deselected|errors?)\b", line)]
    if not lines:
        return None
    result = dict.fromkeys(COUNT_FIELDS, 0)
    for count, field in re.findall(
            r"\b(\d+) (passed|failed|skipped|xfailed|xpassed|deselected|errors?)\b", lines[-1]):
        result["errors" if field in ("error", "errors") else field] += int(count)
    return result


def run(command, checkout, label, run_id, timeout=180, receipt=False):
    env = os.environ.copy()
    env.update(PYTHONPATH=str(checkout), PYTHONDONTWRITEBYTECODE="1")
    start = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(command, cwd=checkout, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=timeout)
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as error:
        stdout, stderr, exit_code = error.stdout or b"", error.stderr or b"", None
        timed_out = True
    elapsed = time.monotonic() - start
    directory = RAW / "runs" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    output_files = {}
    raw_hashes = {}
    for name, payload in (("stdout", stdout), ("stderr", stderr)):
        (directory / f"{label}.{name}.txt").write_bytes(payload)
        target = PUBLIC / "starlette-logs" / run_id / f"{label}.{name}.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(redact(payload.decode("utf-8", errors="replace")))
        output_files[name] = {"path": str(target.relative_to(ROOT)), "sha256": digest(target)}
        raw_hashes[name] = hashlib.sha256(payload).hexdigest()
    text = stdout.decode("utf-8", errors="replace")
    projection = None
    if receipt:
        try:
            projection = json.loads(text.splitlines()[-1])
        except (ValueError, IndexError):
            pass
    return {"command": [redact(str(part)) for part in command], "elapsed_s": elapsed,
            "exit_code": exit_code, "timed_out": timed_out, "receipt": projection,
            "pytest_counts": counts(text), "outputs": output_files,
            "raw_output_sha256": raw_hashes}


def review(run_id):
    if not re.fullmatch(r"(?:07|08|09|16|17|18)-r[12]-starlette_body_limits-(?:baseline|mcp_fast|mcp_qwen)", run_id):
        raise ValueError("Only assigned Starlette terminal runs may be reviewed")
    published = STUDY / "runs" / run_id
    result_path = published / "result.json"
    result = json.loads(result_path.read_text())
    if result["run_id"] != run_id or result["task_id"] != "starlette_body_limits":
        raise ValueError("Terminal record identity mismatch")
    if "trial_end" not in result.get("phase_timestamps", {}):
        raise ValueError("The published record is not yet terminal")
    task = next(item for item in json.loads((STUDY / "tasks.json").read_text())["tasks"]
                if item["id"] == "starlette_body_limits")
    plan = json.loads((PUBLIC / "starlette-probe-plan.json").read_text())
    probe = PUBLIC / "starlette-probes.py"
    if digest(probe) != plan["probe_sha256"]:
        raise ValueError("Frozen supplementary probe changed")
    controls_path = PUBLIC / "starlette-probe-controls.json"
    controls = json.loads(controls_path.read_text())
    if (controls["probe_sha256"] != digest(probe)
            or controls["plan_sha256"] != digest(PUBLIC / "starlette-probe-plan.json")
            or not next(item for item in controls["controls"]
                        if item["control"] == "reference")["receipt"]["passed"]):
        raise ValueError("Frozen positive control binding changed or did not pass")
    patch = published / "changes.patch"
    if digest(patch) != result["patch_sha256"]:
        raise ValueError("Terminal patch hash mismatch")
    target = PUBLIC / f"starlette-{run_id}.json"
    if target.exists():
        raise ValueError("Retain prior reviews; a review must not be overwritten")
    base = RAW / "controls" / "base"
    if not base.exists():
        raise ValueError("Frozen base control unavailable")
    before = tree(base)
    recorded_base = json.loads((published / "base-files.json").read_text())
    if before != recorded_base:
        raise ValueError("Independent pinned archive does not match the frozen fixture")
    candidate = RAW / "candidates" / run_id
    archive(task, candidate)
    subprocess.run(["git", "init", "--quiet"], cwd=candidate, check=True)
    subprocess.run(["git", "apply", "--unsafe-paths", str(patch)], cwd=candidate, check=True)
    after = tree(candidate)
    changed = {path: {"base_sha256": before.get(path), "candidate_sha256": after.get(path)}
               for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)}
    if sorted(changed) != sorted(result["changed_files"]):
        raise ValueError("Reconstructed patch file changes do not match the terminal inventory")
    config = json.loads((STUDY / "frozen-config.json").read_text())
    terminal = Path(config["work_root"]) / "workspaces" / run_id
    changed_terminal_hashes = {path: digest(terminal / path) if (terminal / path).is_file() else None
                               for path in changed}
    if any(changed_terminal_hashes[path] != item["candidate_sha256"]
           for path, item in changed.items()):
        raise ValueError("Reconstructed changed-file bytes do not match the terminal source copy")
    if tree(terminal / "starlette") != tree(candidate / "starlette"):
        raise ValueError("Reconstructed full production source differs from the terminal source copy")
    new_tests = [path for path in changed if path.startswith("tests/") and path.endswith(".py")
                 and path not in before and path in after]
    production = [path for path in changed if path.startswith("starlette/")]
    configs = ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "conftest.py")
    original_test_config_changes = [path for path in changed if path in before
                                    and (path.startswith("tests/") or Path(path).name in configs)]
    added_configs = [path for path in changed if path not in before and Path(path).name in configs]
    python = str(ROOT / ".venv/bin/python")
    supplemental = run([python, "-B", str(probe), "--repo", str(candidate)], candidate,
                       "supplemental", run_id, timeout=60, receipt=True)
    strict = run([python, "-B", str(ROOT / task["verification"]["script"]), "--repo", str(candidate)],
                 candidate, "strict", run_id, timeout=180, receipt=True)
    added = None
    restored_added = None
    if new_tests:
        command = [python, "-B", "-m", "pytest", "-q", "-o", "addopts=", "--tb=short",
                   "-W", "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning",
                   "-W", "ignore:The `cancellable=` keyword argument:DeprecationWarning", *new_tests]
        added = run(command, candidate, "candidate-added-tests", run_id)
        restored = RAW / "restored-production" / run_id
        shutil.copytree(candidate, restored)
        shutil.rmtree(restored / "starlette")
        shutil.copytree(base / "starlette", restored / "starlette")
        restored_added = run(command, restored, "base-production-added-tests", run_id)
    compatibility = RAW / "compatibility" / run_id
    shutil.copytree(candidate, compatibility)
    restored_paths = {}
    for path in before:
        if path.startswith("tests/") or Path(path).name in configs:
            target_path = compatibility / path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base / path, target_path)
            restored_paths[path] = digest(target_path)
    for path in added_configs:
        (compatibility / path).unlink(missing_ok=True)
    compatibility_records = []
    for index, specification in enumerate(task["compatibility_commands"]):
        observed = run([python, "-B", *specification["args"]], compatibility,
                       f"restored-upstream-{index}", run_id, timeout=specification["timeout_s"])
        observed["expected_summary"] = specification["expected_summary"]
        observed["passed"] = (observed["exit_code"] == 0 and not observed["timed_out"]
                              and observed["pytest_counts"] == specification["expected_summary"])
        compatibility_records.append(observed)
    supplemental_receipt = supplemental["receipt"]
    strict_receipt = strict["receipt"]
    receipt = {"version": 1, "reviewed_at": datetime.now(timezone.utc).isoformat(),
               "classification": "Independent terminal-patch review; unchanged frozen formal grades",
               "run_id": run_id, "condition": result["condition"], "repetition": result["repetition"],
               "model_calls": 0, "network_calls": 0,
               "bindings": {"terminal_result_sha256": digest(result_path),
                            "patch_sha256": digest(patch), "base_files_sha256": digest(published / "base-files.json"),
                            "review_source_sha256": digest(__file__), "probe_sha256": digest(probe),
                            "probe_plan_sha256": digest(PUBLIC / "starlette-probe-plan.json"),
                            "probe_controls_sha256": digest(controls_path),
                            "pre_output_inventory_sha256": digest(PUBLIC / "starlette-pre-output-inventory.json"),
                            "study_tasks_sha256": digest(STUDY / "tasks.json")},
               "method": "Fresh pinned archive plus captured terminal patch; no active workspace reads",
               "changed_files": changed, "changed_production_files": production,
               "terminal_changed_file_sha256": changed_terminal_hashes,
               "terminal_full_production_source_exact": True,
               "new_test_files": new_tests, "changed_original_tests_or_configuration": original_test_config_changes,
               "added_configuration": added_configs, "candidate_source_sha256": tree(candidate / "starlette"),
               "supplemental": supplemental, "independent_strict_grade": strict,
               "candidate_added_tests": added, "base_production_added_tests": restored_added,
               "restored_upstream": compatibility_records, "restored_original_test_and_config_sha256": restored_paths,
               "formal_grade_unchanged": {"correct": result["correct"],
                                          "completed_correct": result["completed_correct"],
                                          "strict_passed": result["grade"]["passed"],
                                          "upstream_passed": result["compatibility"]["passed"]},
               "independent_grade_agrees": bool(strict_receipt)
                   and strict_receipt["passed"] == result["grade"]["passed"]
                   and all(record["passed"] for record in compatibility_records) == result["compatibility"]["passed"],
               "supplemental_failed_case_ids": sorted({case["id"] for case in
                       (supplemental_receipt or {}).get("cases", []) if not case["passed"]}),
               "source_review_findings": [],
               "limits": ["Nine additional behavioral cases are supplementary and cannot prove the entire contract.",
                          "Authored-test failures and uncaught contract defects remain separate from frozen formal grades."]}
    write(target, receipt)
    print(json.dumps({"run_id": run_id, "supplemental_passed": (supplemental_receipt or {}).get("passed"),
                      "strict_passed": (strict_receipt or {}).get("passed"),
                      "grade_agrees": receipt["independent_grade_agrees"],
                      "new_tests": len(new_tests), "changed_original_test_config": original_test_config_changes}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    review(parser.parse_args().run_id)
