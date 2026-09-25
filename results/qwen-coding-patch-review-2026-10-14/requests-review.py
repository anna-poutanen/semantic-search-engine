"""Review only explicitly confirmed terminal Requests patches in CPU copies.

The unchanged strict grade and the post-output cached-history diagnostic remain
separate. This driver never changes a coding trial, checker, or reference.
"""
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
RAW = ROOT / ".cache/qwen-requests-review-r2-2026-10-14"
STUDY = ROOT / "results/qwen-coding-workflows-2026-10-14"
PROBE_SHA256 = "a8529a12a7a3dbbe4fdf159d0869f376cf1b99712ae63547244dd6f1ec4661f7"
COUNT_FIELDS = ("passed", "skipped", "failed", "errors", "xfailed", "xpassed", "deselected")
CONFIGS = ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "conftest.py")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(json.dumps(redacted(value), indent=2, sort_keys=True, allow_nan=False) + "\n")


def tree(path):
    return {str(file.relative_to(path)): digest(file) for file in sorted(path.rglob("*"))
            if file.is_file() and not set(file.relative_to(path).parts).intersection(
                (".git", "__pycache__", ".pytest_cache", ".ruff_cache"))}


def archive(task, destination):
    destination.mkdir(parents=True, exist_ok=False)
    data = subprocess.run(["git", "-C", str(ROOT / task["corpus"]), "archive", task["revision"]],
                          check=True, stdout=subprocess.PIPE).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as stream:
        stream.extractall(destination, filter="data")
    subprocess.run(["git", "init", "--quiet"], cwd=destination, check=True)


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
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(name, None)
    env.update(PYTHONPATH=str(checkout / "src"), PYTHONDONTWRITEBYTECODE="1")
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(command, cwd=checkout, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=timeout)
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as error:
        stdout, stderr, exit_code = error.stdout or b"", error.stderr or b"", None
        timed_out = True
    elapsed = time.monotonic() - started
    raw_dir = RAW / "logs" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    public_dir = PUBLIC / "requests-r2-logs" / run_id
    public_dir.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for name, payload in (("stdout", stdout), ("stderr", stderr)):
        raw_path = raw_dir / f"{label}.{name}.txt"
        with raw_path.open("xb") as stream:
            stream.write(payload)
        target = public_dir / f"{label}.{name}.txt"
        with target.open("x") as stream:
            stream.write(redact(payload.decode("utf-8", errors="replace")))
        outputs[name] = {"path": str(target.relative_to(ROOT)), "sha256": digest(target),
                         "raw_sha256": hashlib.sha256(payload).hexdigest()}
    text = stdout.decode("utf-8", errors="replace")
    projection = None
    parse_error = None
    if receipt:
        try:
            projection = json.loads(text)
        except ValueError as error:
            parse_error = str(error)
    return {"command": [redact(str(part)) for part in command], "elapsed_s": elapsed,
            "exit_code": exit_code, "timed_out": timed_out, "receipt": projection,
            "receipt_parse_error": parse_error, "pytest_counts": counts(text), "outputs": outputs}


def restore_original(checkout, base, before):
    restored = {}
    for path in before:
        if path.startswith("tests/") or Path(path).name in CONFIGS:
            target = checkout / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base / path, target)
            restored[path] = digest(target)
    for path in tree(checkout):
        if path not in before and Path(path).name in CONFIGS:
            (checkout / path).unlink()
    return restored


def review(run_id):
    if not re.fullmatch(r"(?:10|11|12)-r2-requests_response_limit-(?:baseline|mcp_fast|mcp_qwen)", run_id):
        raise ValueError("Only assigned terminal repetition-two Requests trials may be reviewed")
    published = STUDY / "runs" / run_id
    result_path = published / "result.json"
    result = json.loads(result_path.read_text())
    if (result["run_id"] != run_id or result["task_id"] != "requests_response_limit"
            or "trial_end" not in result.get("phase_timestamps", {})):
        raise ValueError("A matching terminal result is required")
    target = PUBLIC / f"requests-{run_id}-cpu-review.json"
    if target.exists() or (RAW / "candidates" / run_id).exists():
        raise ValueError("No overwriting or silently retrying prior review attempts")
    task = next(row for row in json.loads((STUDY / "tasks.json").read_text())["tasks"]
                if row["id"] == "requests_response_limit")
    if task["revision"] != "611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60":
        raise ValueError("Pinned Requests revision changed")
    for path, expected in ((task["verification"]["script"], task["verification"]["sha256"]),
                           (task["verification"]["shared_helper"], task["verification"]["shared_helper_sha256"])):
        if digest(ROOT / path) != expected:
            raise ValueError("Frozen strict checker bytes changed")
    probe = PUBLIC / "requests-hook-history-probe.py.txt"
    if digest(probe) != PROBE_SHA256:
        raise ValueError("Unchanged supplemental probe hash required")
    patch = published / "changes.patch"
    if digest(patch) != result["patch_sha256"]:
        raise ValueError("Terminal patch hash mismatch")
    base = RAW / "controls" / "base"
    if not base.exists():
        archive(task, base)
    before = tree(base)
    if before != json.loads((published / "base-files.json").read_text()):
        raise ValueError("Fresh pinned base tree differs from frozen coding fixture")
    candidate = RAW / "candidates" / run_id
    archive(task, candidate)
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=candidate, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=candidate, check=True)
    after = tree(candidate)
    changed = {path: {"base_sha256": before.get(path), "candidate_sha256": after.get(path)}
               for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)}
    if sorted(changed) != sorted(result["changed_files"]):
        raise ValueError("Patch reconstruction does not match exact changed-file inventory")
    config = json.loads((STUDY / "frozen-config.json").read_text())
    terminal = Path(config["work_root"]) / "workspaces" / run_id
    terminal_hashes = {path: digest(terminal / path) if (terminal / path).is_file() else None
                       for path in changed}
    if any(terminal_hashes[path] != item["candidate_sha256"] for path, item in changed.items()):
        raise ValueError("Changed-file bytes differ from terminal source")
    if tree(terminal / "src/requests") != tree(candidate / "src/requests"):
        raise ValueError("Full reconstructed production tree differs from terminal source")
    production_before = tree(candidate / "src/requests")
    binding = {"version": 1, "run_id": run_id, "bound_at": datetime.now(timezone.utc).isoformat(),
               "terminal_result_sha256": digest(result_path), "patch_sha256": digest(patch),
               "base_files_sha256": digest(published / "base-files.json"),
               "full_production_source_exact": True, "changed_files": changed,
               "terminal_changed_file_sha256": terminal_hashes,
               "candidate_production_source_sha256": production_before,
               "review_source_sha256": digest(__file__), "probe_sha256": digest(probe),
               "study_tasks_sha256": digest(STUDY / "tasks.json")}
    write_new(PUBLIC / f"requests-{run_id}-terminal-binding.json", binding)
    python = str(ROOT / ".venv/bin/python")
    strict = run([python, "-B", str(ROOT / task["verification"]["script"]), "--repo", str(candidate)],
                 candidate, "strict", run_id, timeout=task["verification"]["timeout_s"], receipt=True)
    supplemental = run([python, "-B", str(probe), "--repo", str(candidate)], candidate,
                       "unchanged-hook-history", run_id, timeout=60, receipt=True)
    new_tests = [path for path in changed if path.startswith("tests/") and path.endswith(".py")
                 and path not in before and path in after]
    added = None
    restored_added = None
    if new_tests:
        command = [python, "-B", "-m", "pytest", "-q", "-o", "addopts=", "--tb=short", *new_tests]
        added = run(command, candidate, "candidate-added-tests", run_id)
        base_production = RAW / "base-production-added-tests" / run_id
        shutil.copytree(candidate, base_production)
        shutil.rmtree(base_production / "src/requests")
        shutil.copytree(base / "src/requests", base_production / "src/requests")
        restore_original(base_production, base, before)
        restored_added = run(command, base_production, "base-production-added-tests", run_id)
    compatibility = RAW / "compatibility" / run_id
    shutil.copytree(candidate, compatibility)
    restored_paths = restore_original(compatibility, base, before)
    compatibility_records = []
    for index, specification in enumerate(task["compatibility_commands"]):
        observed = run([python, "-B", *specification["args"]], compatibility,
                       f"restored-upstream-{index}", run_id, timeout=specification["timeout_s"])
        observed["expected_summary"] = specification["expected_summary"]
        observed["passed"] = (observed["exit_code"] == 0 and not observed["timed_out"]
                              and observed["pytest_counts"] == specification["expected_summary"])
        compatibility_records.append(observed)
    strict_receipt = strict["receipt"] or {}
    supplemental_receipt = supplemental["receipt"] or {}
    source_unchanged = (production_before == tree(candidate / "src/requests")
                        and after == tree(candidate))
    published_detail = result["grade"]["detail"]
    grade_agrees = (strict_receipt.get("passed") == result["grade"]["passed"]
                   and all(row["passed"] for row in compatibility_records) == result["compatibility"]["passed"]
                   and all(strict_receipt.get(field) == published_detail.get(field)
                           for field in ("tests_run", "failures", "errors", "skipped"))
                   and [row["result"] for row in strict_receipt.get("components", [])]
                       == [row["result"] for row in published_detail["components"]])
    receipt = {"version": 1, "classification": "Independent terminal Requests CPU review; original formal grades unchanged",
               "reviewed_at": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
               "condition": result["condition"], "repetition": result["repetition"],
               "model_calls": 0, "external_network_calls": 0,
               "method": "Fresh pinned initialized Git archive plus exact terminal patch; no active workspace reads",
               "bindings": binding, "candidate_source_hashes_unchanged": source_unchanged,
               "new_test_files": new_tests,
               "changed_original_tests_or_configuration": [path for path in changed if path in before
                   and (path.startswith("tests/") or Path(path).name in CONFIGS)],
               "added_configuration": [path for path in changed if path not in before and Path(path).name in CONFIGS],
               "independent_strict_grade": strict, "unchanged_supplemental_hook_history": supplemental,
               "candidate_added_tests": added, "base_production_added_tests": restored_added,
               "restored_upstream": compatibility_records,
               "restored_original_test_and_config_sha256": restored_paths,
               "independent_grade_agrees": grade_agrees,
               "formal_grade_unchanged": {"correct": result["correct"], "completed_correct": result["completed_correct"],
                   "strict_passed": result["grade"]["passed"], "upstream_passed": result["compatibility"]["passed"]},
               "limits": ["Post-output history probe is supplementary; frozen 18-case grades are never changed.",
                   "Compatibility covers the frozen selected upstream suite, not every Requests test.",
                   "Candidate-authored tests and base-production comparisons are reported separately."]}
    write_new(target, receipt)
    print(json.dumps({"run_id": run_id, "strict_passed": strict_receipt.get("passed"),
                      "history_probe_passed": supplemental_receipt.get("passed"),
                      "grade_agrees": grade_agrees, "source_unchanged": source_unchanged,
                      "added_tests": (added or {}).get("pytest_counts"),
                      "upstream_passed": all(row["passed"] for row in compatibility_records)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    review(parser.parse_args().run_id)
