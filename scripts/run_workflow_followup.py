#!/usr/bin/env python3
"""Prepare or explicitly run the separate, conditional MCP-first follow-up.

The default validation action does no inference. Freeze and run are separate
commands; no original pilot artifact or runtime implementation is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from semantic_search import workflows


ROOT = workflows.ROOT
MANIFEST = Path("benchmarks/coding_workflows_mcp_first.json")
PROTOCOL = Path("docs/coding-workflow-mcp-first-protocol.md")
WRAPPER = Path("scripts/run_workflow_followup.py")
SUPPLEMENTAL_PROBE = Path("scripts/probe_workflow_close_errors.py")
SOURCE_MANIFEST = Path("benchmarks/coding_workflows.json")
EXPECTED_TASKS = ("requests_auth_ports", "click_atomic_close", "starlette_background_order")
IDENTICAL_FIELDS = (
    "model", "common_config", "timeout_s", "embedding_model", "packages", "python",
    "python_version", "cli_binary", "cli_sha256", "cli_version", "sandbox", "mcp",
    "auth", "auth_environment", "shell_environment", "repeats_per_task_condition",
    "timing", "usage", "retry_policy", "correctness",
)


def relative_artifact(path):
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(ROOT):
        raise ValueError("Follow-up provenance files must be inside this project")
    return str(path.relative_to(ROOT))


def validate_selection():
    manifest = workflows.validate_manifest(json.loads((ROOT / MANIFEST).read_text()))
    source = json.loads((ROOT / SOURCE_MANIFEST).read_text())
    if manifest["source_manifest"] != str(SOURCE_MANIFEST) or manifest["source_manifest_sha256"] != workflows.digest(ROOT / SOURCE_MANIFEST):
        raise ValueError("Original source manifest changed")
    first, seen = [], set()
    for task in source["tasks"]:
        if task["corpus_id"] not in seen:
            first.append(task)
            seen.add(task["corpus_id"])
    if tuple(task["id"] for task in first) != EXPECTED_TASKS or len(manifest["tasks"]) != len(first):
        raise ValueError("Follow-up must select the first original task per corpus")
    instruction = manifest["conditional_workflow_instruction"]
    for original, followup in zip(first, manifest["tasks"]):
        for key, value in original.items():
            expected = value + "\n\n" + instruction if key == "prompt" else value
            if followup.get(key) != expected:
                raise ValueError(f"Changed original task field: {original['id']}.{key}")
        if followup.get("original_task_id") != original["id"] or followup.get("original_behavior_prompt_sha256") != hashlib.sha256(original["prompt"].encode()).hexdigest():
            raise ValueError("Original behavior-prompt provenance differs")
    return manifest


def validate_source_pilot(config_path, diagnostic_path):
    config_path = Path(config_path).resolve(strict=True)
    diagnostic_path = Path(diagnostic_path).resolve(strict=True)
    relative_artifact(config_path)
    relative_artifact(diagnostic_path)
    source = json.loads(config_path.read_text())
    if source["manifest"] != str(SOURCE_MANIFEST):
        raise ValueError("Source configuration must belong to the original pilot")
    records = json.loads((config_path.parent / "results.json").read_text())
    expected = [(row["task_id"], row["condition"]) for row in source["schedule"]]
    actual = [(row["task_id"], row["condition"]) for row in records]
    if len(expected) != 12 or len(actual) != len(set(actual)) or sorted(actual) != sorted(expected):
        raise ValueError("All twelve original declared trials must be recorded first")
    mcp_rows = [row for row in records if row["condition"] == "mcp"]
    if len(mcp_rows) != 6 or any(row.get("mcp_calls", 0) != 0 for row in mcp_rows):
        raise ValueError("This follow-up is contingent on no original MCP tool uptake")
    diagnostic = json.loads(diagnostic_path.read_text())
    if (diagnostic.get("tool_exposure_confirmed") is not True
            or diagnostic.get("successful_expected_tool_calls", 0) < 1
            or diagnostic.get("successful_expected_http_calls", 0) < 1
            or diagnostic.get("frozen_pilot_config_sha256") != workflows.digest(config_path)
            or diagnostic.get("model") != source["model"]
            or diagnostic.get("common_config") != source["common_config"]
            or diagnostic.get("forbidden_embedding_attempts", 1) != 0
            or diagnostic.get("forbidden_h2o_attempts", 1) != 0):
        raise ValueError("Successful separate exposure evidence under the original configuration is required")
    for name, expected_hash in source["artifact_sha256"].items():
        if workflows.digest(ROOT / name) != expected_hash:
            raise ValueError(f"Original runtime/task artifact changed: {name}")
    if workflows.digest(source["cli_binary"]) != source["cli_sha256"]:
        raise ValueError("Original CLI binary changed")
    return source, diagnostic


def freeze(output, work_root, source_config, diagnostic_path):
    manifest = validate_selection()
    source, diagnostic = validate_source_pilot(source_config, diagnostic_path)
    if output.exists() or work_root.exists():
        raise ValueError("Fresh follow-up output and workspace paths are required")
    previous_manifest = workflows.MANIFEST
    try:
        workflows.MANIFEST = MANIFEST
        workflows.freeze(output, work_root, timeout=source["timeout_s"])
    finally:
        workflows.MANIFEST = previous_manifest
    config_path = output / "frozen-config.json"
    config = json.loads(config_path.read_text())
    for key in IDENTICAL_FIELDS:
        if config.get(key) != source.get(key):
            raise ValueError(f"Follow-up must preserve original configuration field: {key}")
    original_close_probe = Path(source_config).parent / "close-error-probe.json"
    artifact_names = [str(WRAPPER), str(PROTOCOL), str(SOURCE_MANIFEST), str(SUPPLEMENTAL_PROBE),
                      relative_artifact(source_config), relative_artifact(diagnostic_path),
                      relative_artifact(original_close_probe)]
    config["artifact_sha256"].update({name: workflows.digest(ROOT / name) for name in artifact_names})
    config["followup"] = {
        "classification": "Separate exploratory MCP-first workflow follow-up; do not pool with original pilot or exposure diagnostic",
        "source_config": relative_artifact(source_config),
        "source_config_sha256": workflows.digest(source_config),
        "exposure_diagnostic": relative_artifact(diagnostic_path),
        "exposure_diagnostic_sha256": workflows.digest(diagnostic_path),
        "selection": manifest["selection"],
        "conditional_workflow_instruction": manifest["conditional_workflow_instruction"],
        "task_ids": list(EXPECTED_TASKS),
        "declared_verifier_methods_per_condition": sum(task["seed_validation"]["reference"]["tests_run"] for task in manifest["tasks"]),
        "reuse": "Original development tasks and check preflight; amended prompts frozen before six new executions",
        "baseline_first_pairs": 2, "mcp_first_pairs": 1,
        "supplemental_diagnostic": {
            "script": str(SUPPLEMENTAL_PROBE),
            "script_sha256": workflows.digest(ROOT / SUPPLEMENTAL_PROBE),
            "original_evidence": relative_artifact(original_close_probe),
            "original_evidence_sha256": workflows.digest(original_close_probe),
            "classification": "Predefined from the original pilot's post-hoc patch review; separate from frozen selected-check labels and performance metrics",
        },
    }
    workflows.write_json(config_path, config)
    print(f"Follow-up provenance finalized before runs: {config_path}")


def run(output):
    validate_selection()
    config = json.loads((output / "frozen-config.json").read_text())
    if config.get("manifest") != str(MANIFEST) or "followup" not in config:
        raise ValueError("Expected a separately frozen MCP-first follow-up")
    source, _ = validate_source_pilot(ROOT / config["followup"]["source_config"],
                                      ROOT / config["followup"]["exposure_diagnostic"])
    for key in IDENTICAL_FIELDS:
        if config.get(key) != source.get(key):
            raise ValueError(f"Follow-up configuration changed: {key}")
    for name in (str(WRAPPER), str(PROTOCOL), str(SOURCE_MANIFEST)):
        if config["artifact_sha256"].get(name) != workflows.digest(ROOT / name):
            raise ValueError(f"Follow-up preparation changed: {name}")
    workflows.run(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "freeze", "run"))
    parser.add_argument("--source-config", type=Path)
    parser.add_argument("--diagnostic", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-root", type=Path)
    args = parser.parse_args()
    if args.action == "validate":
        manifest = validate_selection()
        print(json.dumps({"tasks": [task["id"] for task in manifest["tasks"]],
                          "verifier_methods_per_condition": sum(task["seed_validation"]["reference"]["tests_run"] for task in manifest["tasks"]),
                          "agent_runs_started": 0}, indent=2))
    elif args.action == "freeze":
        if any(value is None for value in (args.source_config, args.diagnostic, args.output, args.work_root)):
            parser.error("freeze requires --source-config, --diagnostic, --output and --work-root")
        freeze(args.output.resolve(), args.work_root.resolve(), args.source_config.resolve(), args.diagnostic.resolve())
    else:
        if args.output is None:
            parser.error("run requires --output")
        run(args.output.resolve())


if __name__ == "__main__":
    main()
