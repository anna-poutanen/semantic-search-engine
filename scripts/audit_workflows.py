#!/usr/bin/env python3
"""Independently reconcile a paired-workflow run using CPU-only evidence.

Read completed trials only. Never print raw reasoning or suspected credentials.
This checks recorded traces for leakage; it is not a security proof that every
possible filesystem access was observed. No retrieval or agent inference runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens")
SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}"),
    re.compile(r'"(?:access_token|refresh_token|id_token)"\s*:\s*"[^"\s]{24,}"'),
)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load(path: Path):
    return json.loads(path.read_text())


def git(workspace: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(workspace), *args], check=True,
                          capture_output=True).stdout


def substitute(value, replacements):
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [substitute(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, replacements) for key, item in value.items()}
    return value


def read_events(path: Path):
    events, invalid = [], 0
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("Event is not an object")
            events.append(event)
        except ValueError:
            invalid += 1
    return events, invalid


def reported_usage(events):
    turns = [event["usage"] for event in events
             if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict)]
    if not turns:
        return None, 0
    counts = {}
    for field in FIELDS:
        values = [turn.get(field) for turn in turns]
        if any(value is not None and (type(value) is not int or value < 0) for value in values):
            raise ValueError("Invalid provider token count")
        counts[field] = sum(values) if all(type(value) is int for value in values) else None
    inp, out, cached = (counts[field] for field in ("input_tokens", "output_tokens", "cached_input_tokens"))
    if inp is not None and cached is not None and cached > inp:
        raise ValueError("Cached input exceeds input")
    reasoning = counts["reasoning_output_tokens"]
    if out is not None and reasoning is not None and reasoning > out:
        raise ValueError("Reasoning output exceeds output")
    counts["total_tokens"] = inp + out if inp is not None and out is not None else None
    counts["uncached_input_tokens"] = inp - cached if inp is not None and cached is not None else None
    return counts, len(turns)


def completed_items(events, kind):
    return {event["item"].get("id", str(index)): event["item"]
            for index, event in enumerate(events)
            if event.get("type") == "item.completed" and isinstance(event.get("item"), dict)
            and event["item"].get("type") == kind}


def audit(output: Path, *, allow_incomplete=False, regrade=True):
    config_path = output / "frozen-config.json"
    config, manifest = load(config_path), load(output / "tasks.json")
    tasks = {task["id"]: task for task in manifest["tasks"]}
    records = load(output / "results.json") if (output / "results.json").exists() else []
    errors, warnings, trials = [], [], []

    def require(condition, message):
        if not condition:
            errors.append(message)

    require(sha((output / "tasks.json").read_bytes()) == config["artifact_sha256"][config["manifest"]],
            "Published task manifest differs from freeze")
    require(sha(Path(config["cli_binary"]).read_bytes()) == config["cli_sha256"],
            "CLI executable differs from freeze")
    for name, expected in config["artifact_sha256"].items():
        path = ROOT / name
        require(path.is_file() and sha(path.read_bytes()) == expected, f"Frozen artifact changed: {name}")
    declared = config["schedule"]
    require(len(records) <= len(declared), "More recorded trials than declared")
    if not allow_incomplete:
        require(len(records) == len(declared), "Declared trial inventory is incomplete")
    work_root = Path(config["work_root"])

    for index, record in enumerate(records):
        expected = declared[index]
        name = f"{index + 1:02d}-{expected['task_id']}-{expected['condition']}"
        task = tasks[expected["task_id"]]
        public, raw = output / "runs" / name, work_root / "raw" / name
        workspace = work_root / "workspaces" / name
        prefix = name + ": "
        trial_error_start = len(errors)
        require(record["run_id"] == name and record["task_id"] == expected["task_id"]
                and record["condition"] == expected["condition"], prefix + "Schedule mismatch")
        require(load(public / "result.json") == record, prefix + "Inventory/result mismatch")
        replacements = [(str(workspace), "<WORKSPACE>"), (str(raw), "<EVALUATOR_LOG>"),
                        (str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]
        trial = {"run_id": name, "checks_attempted": [], "mcp_calls": 0, "mcp_failed_calls": 0,
                 "command_calls": 0, "reported_usage_turns": 0, "leakage_flags": []}
        if not (raw / "events.jsonl").exists():
            require(bool(record.get("infrastructure_error")), prefix + "Missing raw events without failure")
            warnings.append(prefix + "Agent events unavailable; infrastructure failure retained")
            trial["passed"] = len(errors) == trial_error_start
            trials.append(trial)
            continue

        events, invalid = read_events(raw / "events.jsonl")
        usage, turns = reported_usage(events)
        require(usage == record["usage"], prefix + "Reported usage mismatch")
        require(turns <= 1, prefix + "Multiple usage turns need explicit semantics review")
        require(record.get("reported_turns") == turns, prefix + "Reported turn count mismatch")
        require(record["completed"] == any(event.get("type") == "turn.completed" for event in events),
                prefix + "Completion status differs from events")
        if not record.get("infrastructure_error"):
            require(record["failed"] == any(event.get("type") == "turn.failed" for event in events),
                    prefix + "Failure status differs from events")
        require(record["completed_correct"] == bool(record["correct"] and record["completed"]
                                                    and not record["failed"] and record["agent_exit_code"] == 0
                                                    and not record["timed_out"]),
                prefix + "Combined completed/correct status differs")
        require(invalid == len(record.get("invalid_event_lines", [])), prefix + "Invalid event count mismatch")
        trial.update(usage=usage, reported_usage_turns=turns)
        stderr = (raw / "stderr.txt").read_text()
        trial["runtime_collaboration_spawn_failures"] = stderr.count("collab spawn failed")
        trial["runtime_parent_transcript_resolution_warnings"] = stderr.count("failed to resolve parent transcript path")
        if trial["runtime_collaboration_spawn_failures"]:
            warnings.append(prefix + "Alpha CLI attempted collaboration despite requested disabled flags; failed spawn remains recorded")
        require(record.get("raw_events_sha256") == sha((raw / "events.jsonl").read_bytes()),
                prefix + "Raw event digest mismatch")
        public_events, public_invalid = read_events(public / "events.jsonl")
        expected_events = [substitute(event, replacements) for event in events
                           if event.get("item", {}).get("type") != "reasoning"]
        require(public_invalid == 0 and public_events == expected_events,
                prefix + "Published event redaction/removal differs from raw")
        require(record.get("published_events_sha256") == sha((public / "events.jsonl").read_bytes()),
                prefix + "Published event digest mismatch")
        require(not any(event.get("item", {}).get("type") == "reasoning" for event in public_events),
                prefix + "Published reasoning event")

        command = load(raw / "command.json")
        settings = tomllib.loads("\n".join(command[position + 1] for position, value in enumerate(command)
                                            if value == "-c"))
        require(command[0] == config["cli_binary"] and command[command.index("-m") + 1] == config["model"],
                prefix + "Agent executable/model differs")
        for flag in ("--no-daemon", "--ignore-user-config", "--ignore-rules", "--strict-config", "--ephemeral", "--json"):
            require(flag in command, prefix + "Missing isolation flag " + flag)
        require(command[command.index("--sandbox") + 1] == config["sandbox"], prefix + "Sandbox differs")
        require(sha(command[-1].encode()) == config["prompt_sha256"][task["id"]], prefix + "Prompt hash differs")
        require(record["prompt_sha256"] == config["prompt_sha256"][task["id"]],
                prefix + "Recorded prompt digest differs")
        require((public / "prompt.txt").read_text() == substitute(command[-1], replacements),
                prefix + "Published prompt differs")
        expected_settings = tomllib.loads("\n".join(key + "=" + json.dumps(value)
                                                     for key, value in config["common_config"].items()))
        actual_common = {key: value for key, value in settings.items()
                         if key not in ("mcp_servers", "shell_environment_policy")}
        require(actual_common == expected_settings, prefix + "Ordinary configuration differs")
        require(settings["shell_environment_policy"]["set"] == {
            "PYTHONPATH": str((workspace / task["source_root"]).parent), "PYTHONDONTWRITEBYTECODE": "1"},
            prefix + "Fixture import environment differs")
        servers = settings.get("mcp_servers", {})
        if record["condition"] == "baseline":
            require(servers == {}, prefix + "Baseline has MCP registration")
        else:
            server = servers.get("semantic_search", {})
            require(set(servers) == {"semantic_search"} and server.get("required") is True
                    and server.get("enabled_tools") == ["search_code"], prefix + "MCP configuration differs")
            require(server.get("command") == config["python"] and server.get("cwd") == str(ROOT),
                    prefix + "Adapter executable/cwd differs")
            require(server.get("env") == {"PYTHONPATH": str(ROOT / "src")}, prefix + "Adapter import path differs")

        commands, mcp = completed_items(events, "command_execution"), completed_items(events, "mcp_tool_call")
        require(len(commands) == record["command_calls"] and len(mcp) == record["mcp_calls"],
                prefix + "Tool-call count mismatch")
        trial.update(command_calls=len(commands), mcp_calls=len(mcp),
                     file_change_items=len(completed_items(events, "file_change")))
        for item in mcp.values():
            require(record["condition"] == "mcp" and item.get("server") == "semantic_search"
                    and item.get("tool") == "search_code", prefix + "Undeclared MCP tool")
            result = item.get("result") or {}
            if item.get("status") in ("failed", "error") or item.get("error") or result.get("isError") or result.get("is_error"):
                trial["mcp_failed_calls"] += 1
        for item in commands.values():
            text = item.get("command", "")
            allowed_removed = text.replace(config["python"], "<PYTHON>")
            if any(marker in allowed_removed for marker in
                   ("workflow_checks", "workflow-reference", "workflow-independent-review",
                    ".codex/auth", "auth.json", "/raw/", "<EVALUATOR_LOG>")):
                trial["leakage_flags"].append(item.get("id", "unknown") + ": evaluator/auth path reference")
            if re.search(r"(?:^|[\s\"'])\.\./|\b(?:cd|pushd)\s+\.\.(?:\s|/|[;\"']|$)", allowed_removed):
                trial["leakage_flags"].append(item.get("id", "unknown") + ": parent path reference")
            if str(ROOT) in allowed_removed and str(workspace) not in allowed_removed:
                trial["leakage_flags"].append(item.get("id", "unknown") + ": evaluator project reference")
        require(not trial["leakage_flags"], prefix + "Tool traces need leakage review")

        base_files = load(public / "base-files.json")
        require(record["base_tree_sha256"] == sha(json.dumps(base_files, sort_keys=True).encode()),
                prefix + "Seeded tree digest mismatch")
        for path, expected_hash in base_files.items():
            require(sha(git(workspace, "show", record["base_commit"] + ":" + path)) == expected_hash,
                    prefix + "Seeded Git bytes differ for " + path)
        require(record["seed_grade"]["detail"] == task["seed_validation"]["base"], prefix + "Seed grade differs")
        require(record["patch_sha256"] == sha((public / "changes.patch").read_bytes()), prefix + "Patch digest mismatch")
        patch = git(workspace, "diff", "--binary", record["base_commit"]).decode()
        require(substitute(patch, replacements) == (public / "changes.patch").read_text(), prefix + "Published patch differs")
        if regrade:
            verifier = ROOT / task["verification"]["script"]
            checked = subprocess.run([config["python"], str(verifier), "--repo", str(workspace)],
                                     capture_output=True, text=True, timeout=task["verification"]["timeout_s"])
            checked_summary = json.loads(checked.stdout.strip().splitlines()[-1])
            checked_pass = checked.returncode == 0 and checked_summary == task["seed_validation"]["reference"]
            require(checked_summary == record["grade"]["detail"] and checked_pass == record["correct"],
                    prefix + "Independent regrade differs")
            trial["independent_grade"] = checked_summary
        if record["condition"] == "mcp" and record.get("final_backend_status"):
            store = record["final_backend_status"].get("store", {})
            require(store.get("embedding") == config["embedding_model"], prefix + "Embedding metadata differs")
            require(record["final_backend_status"].get("abstention_policy") is None, prefix + "Unexpected rejection policy")
        for field in ("setup_s", "agent_elapsed_s", "end_to_end_s"):
            value = record.get(field)
            require(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0),
                    prefix + "Invalid elapsed time " + field)
        if all(record.get(field) is not None for field in ("setup_s", "agent_elapsed_s", "end_to_end_s")):
            require(record["end_to_end_s"] >= record["setup_s"] + record["agent_elapsed_s"] + record["grade"]["elapsed_s"],
                    prefix + "Elapsed timing components inconsistent")
        trial["checks_attempted"] = ["source_and_prompt", "provider_usage", "frozen_settings",
                                     "public_trace_reconciliation", "recorded_path_leakage_scan"]
        trial["passed"] = len(errors) == trial_error_start
        trials.append(trial)

    for task_id in tasks:
        pair = [record for record in records if record["task_id"] == task_id]
        if len(pair) == 2 and all(record.get("base_tree_sha256") for record in pair):
            require(pair[0]["base_tree_sha256"] == pair[1]["base_tree_sha256"]
                    and pair[0]["base_commit"] == pair[1]["base_commit"]
                    and pair[0]["prompt_sha256"] == pair[1]["prompt_sha256"], task_id + ": Pair source/prompt mismatch")

    # Scan source-bearing public artifacts without echoing a possible credential.
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.suffix not in (".json", ".jsonl", ".txt", ".patch", ".md"):
            continue
        if path.name in ("frozen-config.json", "audit.json"):
            continue  # Configuration deliberately records absolute executable/cache locations.
        text = path.read_text()
        require(str(Path.home()) not in text, "Unredacted local home path: " + str(path.relative_to(output)))
        require(not any(pattern.search(text) for pattern in SECRET_PATTERNS),
                "Possible credential requires private review: " + str(path.relative_to(output)))

    measurements = {}
    for condition in ("baseline", "mcp"):
        rows = [record for record in records if record["condition"] == condition]
        values = {field: [record["usage"][field] for record in rows if record.get("usage")
                          and record["usage"].get(field) is not None]
                  for field in (*FIELDS, "total_tokens", "uncached_input_tokens")}
        measurements[condition] = {"runs": len(rows), "completed_correct": sum(record["completed_correct"] for record in rows),
            "usage": {field: {"observed": len(samples), "total": sum(samples) if samples else None,
                               "median": statistics.median(samples) if samples else None} for field, samples in values.items()},
            "agent_elapsed_median_s": statistics.median([record["agent_elapsed_s"] for record in rows
                if record.get("agent_elapsed_s") is not None]) if any(record.get("agent_elapsed_s") is not None for record in rows) else None}
    return {"auditor_sha256": sha(Path(__file__).read_bytes()), "frozen_config_sha256": sha(config_path.read_bytes()),
            "complete": len(records) == len(declared), "declared_trials": len(declared), "audited_trials": len(records),
            "passed": not errors, "errors": errors, "warnings": warnings, "trials": trials, "measurements": measurements,
            "limits": "CPU audit of observed events, hashes, settings and selected behavior checks. Path/credential scans are diagnostics, not an adversarial security proof. Single-pair pilot does not establish generalization or dollar cost."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--no-regrade", action="store_true")
    args = parser.parse_args()
    result = audit(args.run.resolve(), allow_incomplete=args.allow_incomplete, regrade=not args.no_regrade)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("complete", "audited_trials", "passed", "errors", "warnings")}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
