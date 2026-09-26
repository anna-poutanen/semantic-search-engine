#!/usr/bin/env python3
"""Build the new task manifest from preserved strict CPU controls, without models."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

import run_qwen_workflows as runner

ROOT = runner.ROOT
PREFLIGHT = Path("results/strict-coding-preflight-2026-10-14")


def freeze(output):
    if output.exists():
        raise ValueError("Strict task manifest already exists; no overwrite")
    controls_path = PREFLIGHT / "final-controls/controls.json"
    summary_path = PREFLIGHT / "summary.json"
    controls, summary = runner.read(ROOT / controls_path), runner.read(ROOT / summary_path)
    strict_path = Path("benchmarks/strict_multifile_checks/manifest.json")
    strict = runner.read(ROOT / strict_path)
    if (controls.get("complete") is not True or summary.get("controls_complete") is not True
            or summary.get("controls_sha256") != runner.sha(ROOT / controls_path)
            or controls["strict_manifest_sha256"] != runner.sha(ROOT / strict_path)):
        raise ValueError("Complete hash-bound strict reference controls required")
    original = runner.read(ROOT / runner.mf.MANIFEST)
    tasks = []
    for task_id in runner.evidence.TASK_IDS:
        task = deepcopy(next(task for task in original["tasks"] if task["id"] == task_id))
        records = controls["tasks"][task_id]
        positive_name = "strict-reference" if "strict-reference" in records["controls"] else "original-reference"
        positive = records["controls"][positive_name]
        baseline = records["controls"]["base"]
        if not positive["strict"]["passed"] or not positive["compatibility"]["passed"]:
            raise ValueError("Strict reference and original compatibility must both pass")
        if baseline["strict"]["passed"] or not baseline["compatibility"]["passed"]:
            raise ValueError("Unmodified baseline must fail feature checks and pass compatibility")
        expected, base = runner.grade_projection(positive["strict"]["grade"]), runner.grade_projection(baseline["strict"]["grade"])
        if expected is None or base is None:
            raise ValueError("Control projection is malformed")
        task["prior_verification"] = task["verification"]
        task["prior_seed_validation"] = task["seed_validation"]
        script = strict["tasks"][task_id]["strict_script"]
        helper = "benchmarks/strict_multifile_checks/strict_common.py"
        task["verification"] = {"script": script, "sha256": runner.sha(ROOT / script),
            "shared_helper": helper, "shared_helper_sha256": runner.sha(ROOT / helper),
            "args": ["--repo", "{workspace}"], "timeout_s": 180}
        task["seed_validation"] = {"base": base, "reference": expected}
        task["strict_control_passed"] = True
        task["strict_reference_control"] = positive_name
        task["strict_artifact_sha256"] = dict(strict["input_sha256"])
        for path in (strict_path, controls_path, summary_path, Path(__file__).resolve().relative_to(ROOT)):
            task["strict_artifact_sha256"][str(path)] = runner.sha(ROOT / path)
        if "repair_patch_sha256" in records:
            repair = Path(".cache/strict-coding-controls-2026-10-14/repairs") / task_id / "repair.patch"
            if runner.sha(ROOT / repair) != records["repair_patch_sha256"]:
                raise ValueError("Evaluated ignored reference repair changed")
            task["strict_reference_repair_sha256"] = records["repair_patch_sha256"]
            task["strict_artifact_sha256"][str(repair)] = records["repair_patch_sha256"]
        tasks.append(task)
    manifest = {"version": 1, "classification": "Observed development task rerun, strict prospective checks",
        "task_prompt_changes": False, "model_calls": 0,
        "original_manifest_sha256": runner.sha(ROOT / runner.mf.MANIFEST),
        "strict_manifest_sha256": runner.sha(ROOT / strict_path),
        "control_receipt_sha256": runner.sha(ROOT / controls_path), "tasks": tasks}
    with output.open("x") as stream:
        stream.write(json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n")
    runner.validate_manifest(output)
    print(json.dumps({"tasks": len(tasks), "strict_selected_tests": {task["id"]:
        task["seed_validation"]["reference"]["tests_run"] for task in tasks},
        "manifest_sha256": runner.sha(output), "model_calls": 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / runner.MANIFEST)
    freeze(parser.parse_args().output)


if __name__ == "__main__":
    main()
