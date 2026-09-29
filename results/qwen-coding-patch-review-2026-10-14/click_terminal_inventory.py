#!/usr/bin/env python3
"""Read only a finished Click trial and retain immutable review input bindings.

No graders, tests, model calls or source edits are performed by this inventory.
Supplemental tests/probes are separate review actions after source inspection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
STUDY = ROOT / "results/qwen-coding-workflows-2026-10-14"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def source_tree(directory):
    return {str(path.relative_to(directory)): sha(path) for path in sorted((directory / "src/click").rglob("*.py"))}


def publication(value):
    if isinstance(value, dict):
        return {key: publication(item) for key, item in value.items()}
    if isinstance(value, list):
        return [publication(item) for item in value]
    if isinstance(value, str):
        return value.replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
    return value


def inventory(run_id):
    scope = read(OUTPUT / "click-scope.json")
    expected = next((row for row in scope["schedule"] if row["run_id"] == run_id), None)
    require(expected is not None, "Run is outside the frozen six-trial Click review scope")
    for name, digest in scope["source_sha256"].items():
        require(sha(ROOT / name) == digest, "Prospective review input changed: " + name)
    public = STUDY / "runs" / run_id
    result_path, patch_path = public / "result.json", public / "changes.patch"
    record = read(result_path)
    require(record.get("run_id") == run_id and record.get("task_id") == "click_atomic_transactions",
            "Trial identity differs")
    require(record.get("phase_timestamps", {}).get("trial_end") is not None,
            "Trial is still active or lacks a terminal receipt; do not inspect its patch")
    require(patch_path.is_file() and sha(patch_path) == record.get("patch_sha256"), "Missing/unbound terminal patch")
    require("grade" in record and "compatibility" in record, "Terminal evaluator receipts are unavailable")
    config = read(STUDY / "frozen-config.json")
    work_root = Path(config["work_root"])
    workspace = work_root / "workspaces" / run_id
    evaluator = work_root / "raw" / run_id / "original-upstream-checkout"
    require(workspace.is_dir() and evaluator.is_dir(), "Terminal agent/evaluator copies are unavailable")
    workspace_sources, evaluator_sources = source_tree(workspace), source_tree(evaluator)
    require(workspace_sources and workspace_sources == evaluator_sources,
            "Terminal candidate and restored evaluator production sources differ")
    base = read(public / "base-files.json")
    categories, hashes, deleted = {"production": [], "tests": [], "configuration": [], "other": []}, {}, []
    for name in record["changed_files"]:
        path = Path(name)
        require(not path.is_absolute() and ".." not in path.parts, "Unsafe changed-file path")
        item = workspace / path
        if name.startswith("src/click/"):
            kind = "production"
        elif name.startswith("tests/"):
            kind = "tests"
        elif path.name in {"pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", "conftest.py"}:
            kind = "configuration"
        else:
            kind = "other"
        categories[kind].append(name)
        if item.is_file():
            hashes[name] = sha(item)
        else:
            deleted.append(name)
    authored_tests = [name for name in categories["tests"] if name not in base and name.endswith(".py")
                      and (Path(name).name.startswith("test_") or Path(name).name.endswith("_test.py"))]
    copied_original = {name: sha(evaluator / name) if (evaluator / name).is_file() else None
        for name in record["compatibility"]["original_test_and_config_hashes"]}
    require(copied_original == record["compatibility"]["original_test_and_config_hashes"],
            "Evaluator original test/config restoration differs from frozen receipts")
    return {"version": 1, "classification": "Immutable terminal Click review inventory; frozen grades unchanged",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
        "condition": expected["condition"], "repetition": expected["repetition"],
        "review_inputs": {"scope_sha256": sha(OUTPUT / "click-scope.json"), "result_sha256": sha(result_path),
                          "patch_sha256": sha(patch_path), "base_files_sha256": sha(public / "base-files.json"),
                          "inventory_source_sha256": sha(Path(__file__))},
        "frozen_outcomes": {key: record.get(key) for key in ("completed", "failed", "timed_out", "correct", "completed_correct", "agent_exit_code")},
        "frozen_feature_grade": record["grade"]["projection"],
        "frozen_compatibility": {"passed": record["compatibility"]["passed"],
            "original_test_and_config_edits": record["compatibility"]["changed_or_deleted_original_tests"],
            "added_configuration_removed": record["compatibility"]["added_configuration_removed_for_grading"]},
        "changed_files": categories, "changed_file_sha256": hashes, "deleted_files": deleted,
        "authored_test_files": authored_tests, "production_source_sha256": workspace_sources,
        "evaluator_production_sources_match": True, "evaluator_original_tests_and_config_match": True,
        "model_calls": 0, "hosted_calls": 0, "graders_run": 0,
        "originals": {"workspace": str(workspace), "evaluator": str(evaluator), "public": str(public)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = inventory(args.run_id)
    destination = OUTPUT / ("click-" + args.run_id + "-inventory.json")
    with destination.open("x") as stream:
        json.dump(publication(result), stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"run_id": args.run_id, "inventory_sha256": sha(destination),
                      "changed_files": sum(len(values) for values in result["changed_files"].values())}))


if __name__ == "__main__":
    main()
