#!/usr/bin/env python3
"""CPU-only reconciliation of terminal multifile trials, never replaying commands.

Only trials already appended to results.json are inspected. Active agent
workspaces are not read. Raw reasoning is never published. Failures, timeouts,
missing usage, and failed tools remain in the inventory.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STUDY = ROOT / "results/multifile-workflows-2026-10-13"
DEFAULT_OUTPUT = ROOT / "results/multifile-audit-2026-10-13"
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens")
COUNT_FIELDS = ("passed", "failed", "skipped", "errors", "xfailed", "xpassed", "deselected")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def safe(value):
    if isinstance(value, str):
        value = value.replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "<REDACTED_API_KEY>", value)
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{24,}=*", "Bearer <REDACTED_TOKEN>", value)
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items()}
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe(value), indent=2, sort_keys=True) + "\n")


def git_read(repository, *arguments):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(repository), *arguments])


def counts(text):
    lines = [line for line in text.splitlines() if re.search(r"\b\d+ (?:passed|failed|skipped|errors?|xfailed|xpassed|deselected)\b", line)]
    if not lines:
        return None
    result = dict.fromkeys(COUNT_FIELDS, 0)
    for number, key in re.findall(r"\b(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)\b", lines[-1]):
        result["errors" if key in ("error", "errors") else key] += int(number)
    return result


def usage_from_events(events):
    receipts = [event["usage"] for event in events if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict)]
    if not receipts:
        return None, 0
    output = {key: sum(row[key] for row in receipts) if all(type(row.get(key)) is int for row in receipts) else None for key in USAGE_FIELDS}
    inp, out, cached = output["input_tokens"], output["output_tokens"], output["cached_input_tokens"]
    output["total_tokens"] = inp + out if inp is not None and out is not None else None
    output["uncached_input_tokens"] = inp - cached if inp is not None and cached is not None else None
    return output, len(receipts)


def body_detail(text):
    try:
        return json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def command_review(events, workspace, python):
    flagged = []
    commands = [event["item"] for event in events if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "command_execution"]
    patterns = {
        "evaluator_or_reference_path": r"(?:multifile[_/-]checks|multifile-reference|coding_workflows_multifile|reference\.patch|(?:^|[/'\"])benchmarks[/\"])",
        "parent_or_home_path": r"(?:\.\./|\.\.['\"]|Path\([^\n]*\)\.parent|\.parents\[|~[/\\]|/Users/|\.codex/|auth\.json)",
        "dependency_install_or_external_fetch": r"(?:\b(?:pip|uv)\s+(?:install|sync)|\b(?:curl|wget)\b|\bgit\s+(?:clone|fetch|pull)\b)",
    }
    for item in commands:
        text = item.get("command", "")
        # Frozen runtime paths are authorized. Exact current fixture paths are
        # expected, including the source parent of Requests/Click's src layout.
        inspected = text.replace(str(workspace), "<WORKSPACE>").replace(python, "<PYTHON>")
        findings = [name for name, expression in patterns.items() if re.search(expression, inspected)]
        if findings:
            flagged.append({"item_id": item.get("id"), "patterns": findings, "command": safe(text)})
    return {"completed_command_items": len(commands), "flagged_observed_commands": flagged,
            "meaning": "Conservative textual flags for manual review, not proof of file accesses or an OS isolation boundary."}


def source_receipt_checks(receipt, directory):
    outcomes = []
    for phase in ("before", "after"):
        snapshot_dir = directory / "snapshots" / f"{receipt['number']:04d}" / phase
        metadata = read(snapshot_dir / "snapshot.json")
        outcomes.append({"phase": phase, "manifest_matches_receipt": metadata == receipt["snapshots"][phase],
                         "all_saved_file_hashes_match": all(sha(snapshot_dir / name) == digest for name, digest in metadata["files"].items())})
    source = []
    if receipt.get("success"):
        after = directory / "snapshots" / f"{receipt['number']:04d}" / "after"
        for row in receipt["response"]["results"]:
            raw = (after / row["path"]).read_bytes()
            import io
            import tokenize
            codec, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            text = raw.decode(codec)
            # Python's splitlines recognizes additional Unicode separators;
            # source locations use only physical CR/LF boundaries.
            lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", text)
            excerpt = "".join(lines[row["start_line"] - 1:row["end_line"]])
            returned_lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", row["source"])
            source.append({"id": row["id"], "path": row["path"], "exact_saved_source": excerpt == row["source"],
                           "physical_bounds": row["end_line"] == row["start_line"] + len(returned_lines) - 1,
                           "source_hash": row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()})
    return {"snapshots": outcomes, "returned_source": source,
            "source_changed_during_call": receipt["snapshots"].get("before") != receipt["snapshots"].get("after"),
            "passed": all(row["manifest_matches_receipt"] and row["all_saved_file_hashes_match"] for row in outcomes)
                      and all(row["exact_saved_source"] and row["physical_bounds"] and row["source_hash"] for row in source)}


def audit_run(record, task, config, study):
    run_id = record["run_id"]
    public = study / "runs" / run_id
    raw = Path(config["work_root"]) / "raw" / run_id
    workspace = Path(config["work_root"]) / "workspaces" / run_id
    saved = read(public / "result.json")
    checks = {"inventory_record_matches_result": saved == record}
    if record.get("artifact_sha256"):
        checks["recorded_raw_and_public_hashes"] = all(sha((raw if kind == "raw" else public) / name) == digest
            for kind, hashes in record["artifact_sha256"].items() for name, digest in hashes.items())
    if record.get("patch_sha256"):
        checks["published_patch_hash"] = sha(public / "changes.patch") == record["patch_sha256"]
    base_hashes = read(public / "base-files.json") if (public / "base-files.json").is_file() else None
    if base_hashes is not None:
        checks["base_tree_hash"] = hashlib.sha256(json.dumps(base_hashes, sort_keys=True).encode()).hexdigest() == record["base_tree_sha256"]
        checks["base_original_test_hashes"] = all(base_hashes.get(path) == digest for path, digest in task["upstream_test_sha256"].items())
        with tarfile.open(fileobj=io.BytesIO(git_read(workspace, "archive", record["base_commit"]))) as archive:
            actual_base = {entry.name: hashlib.sha256(archive.extractfile(entry).read()).hexdigest()
                           for entry in archive if entry.isfile()}
        checks["actual_prepared_commit_bytes"] = all(actual_base.get(path) == digest for path, digest in base_hashes.items())
    events = rows(raw / "events.jsonl")
    usage, reported_turns = usage_from_events(events)
    checks["provider_usage_reconciled"] = usage == record.get("usage")
    checks["reported_turn_receipts_reconciled"] = reported_turns == record.get("reported_turns", 0)
    if events:
        checks["completion_status_reconciled"] = any(event.get("type") == "turn.completed" for event in events) == record["completed"]
    if record.get("grade"):
        grade = record["grade"]
        if "stdout" in grade:
            checks["contract_check_detail_from_stdout"] = body_detail(grade["stdout"]) == grade["detail"]
        expected_pass = grade.get("exit_code") == 0 and grade.get("detail") == task["seed_validation"]["reference"]
        checks["contract_check_flag_reconciled"] = grade["passed"] == expected_pass
    if record.get("compatibility"):
        compat = record["compatibility"]
        frozen_selections = task["compatibility_commands"]
        checks["original_suite_count"] = len(compat["commands"]) == len(frozen_selections)
        checks["original_suites_outcomes_reconciled"] = all(
            actual.get("expected_summary") == frozen["expected_summary"]
            and (counts(actual["stdout"]) == actual["actual_summary"] if "stdout" in actual else actual.get("error") == "upstream_check_timeout")
            and actual["passed"] == (actual.get("exit_code") == 0 and actual.get("actual_summary") == frozen["expected_summary"])
            for actual, frozen in zip(compat["commands"], frozen_selections))
        restored = raw / "original-upstream-checkout"
        checks["actual_restored_original_test_bytes"] = all(sha(restored / name) == digest for name, digest in compat["original_test_and_config_hashes"].items())
        files = {str(path.relative_to(workspace)): sha(path) for path in (workspace / task["source_root"]).rglob("*.py") if "__pycache__" not in path.parts}
        checks["graded_source_copy_matches_final_candidate"] = all(sha(restored / name) == digest for name, digest in files.items())
        expected_correct = record["grade"]["passed"] and compat["passed"]
        checks["correctness_flag_reconciled"] = record["correct"] == expected_correct
    checks["completed_correct_flag_reconciled"] = record["completed_correct"] == bool(record["correct"] and record.get("completed") and not record.get("failed") and record.get("agent_exit_code") == 0 and not record.get("timed_out"))
    if (raw / "command.json").is_file():
        command = read(raw / "command.json")
        checks["actual_user_prompt_hash"] = hashlib.sha256(command[-1].encode()).hexdigest() == record["prompt_sha256"] == config["prompt_sha256"][task["id"]]
    items = {}
    for event in events:
        item = event.get("item", {})
        if item.get("type") == "mcp_tool_call":
            entry = items.setdefault(item["id"], {"id": item["id"], "started": False, "completed_event": False})
            if event["type"] == "item.started":
                entry["started"] = True
            elif event["type"] == "item.completed":
                entry.update(completed_event=True, item=item)
    receipts = rows(raw / "http-receipts.jsonl")
    unused = list(range(len(receipts)))
    successful, failed, context_rows = 0, 0, []
    for entry in items.values():
        item = entry.get("item", {})
        result = item.get("result") or {}
        success = bool(entry["completed_event"] and item.get("status") == "completed" and not item.get("error") and not result.get("isError") and not result.get("is_error"))
        successful += int(success)
        failed += int(entry["completed_event"] and not success)
        if not success:
            continue
        content = result.get("content", [])
        text = content[0].get("text") if len(content) == 1 and content[0].get("type") == "text" else None
        digest = hashlib.sha256(text.encode()).hexdigest() if isinstance(text, str) else None
        matching = [index for index in unused if receipts[index].get("success") and receipts[index].get("compact_text_sha256") == digest and receipts[index].get("query") == item.get("arguments", {}).get("query")]
        matched = matching[0] if matching else None
        checks[f"mcp_{entry['id']}_single_text_no_structured_payload"] = text is not None and result.get("structured_content") is None and result.get("structuredContent") is None
        checks[f"mcp_{entry['id']}_independent_http_payload_match"] = matched is not None
        if matched is not None:
            unused.remove(matched)
            receipt = receipts[matched]
            checks[f"mcp_{entry['id']}_arguments_match_http"] = all(receipt["settings"].get(key) == value for key, value in item["arguments"].items() if key != "query")
            envelope = json.loads(text)
            context_rows.append({"item_id": entry["id"], "receipt_number": receipt["number"], "query": item["arguments"]["query"],
                                 "indexed_scope": task["source_root"], "retrieval": receipt["settings"].get("retrieval"),
                                 "token_budget": receipt["settings"].get("token_budget"), "local_payload_tokens": envelope["token_usage"]["tokens"],
                                 "returned_results": len(envelope["results"]),
                                 "query_mentions_tests_or_fixtures": bool(re.search(r"\btests?\b|\bfixtures?\b", item["arguments"]["query"], re.I))})
    receipt_sources = []
    for receipt in receipts:
        evidence = source_receipt_checks(receipt, raw)
        checks[f"http_{receipt['number']}_source_evidence"] = evidence["passed"]
        checks[f"http_{receipt['number']}_actual_index_scope"] = receipt.get("response", {}).get("source_root", task["source_root"]) == task["source_root"]
        receipt_sources.append({"number": receipt["number"], "success": receipt.get("success"), "error": receipt.get("error"), **evidence})
    completed_calls = sum(row["completed_event"] for row in items.values())
    checks["reported_completed_mcp_count"] = completed_calls == record.get("mcp_calls", 0)
    if record["condition"] == "baseline":
        checks["baseline_has_no_search_mcp_or_http"] = not items and not receipts
    outcome = {"run_id": run_id, "task_id": record["task_id"], "condition": record["condition"], "repetition": record["repetition"],
               "checks": checks, "integrity_passed": all(checks.values()), "correct": record["correct"], "completed_correct": record["completed_correct"],
               "completed": record.get("completed"), "failed": record.get("failed"), "timed_out": record.get("timed_out"),
               "infrastructure_error": record.get("infrastructure_error"), "grading_error": record.get("grading_error"),
               "provider_usage": usage, "reported_turns": reported_turns, "agent_elapsed_s": record.get("agent_elapsed_s"),
               "mcp": {"observed_attempt_items": len(items), "started_items": sum(item["started"] for item in items.values()),
                       "completed_items": completed_calls, "successful_completed_calls": successful, "failed_completed_calls": failed,
                       "unfinished_items": len(items) - completed_calls},
               "http": {"receipts": len(receipts), "successful_receipts": sum(row.get("success") is True for row in receipts),
                        "failed_receipts": sum(row.get("success") is not True for row in receipts), "unmatched_receipt_numbers": [receipts[i]["number"] for i in unused]},
               "context_calls": context_rows, "source_receipt_evidence": receipt_sources,
               "observed_command_review": command_review(events, workspace, config["python"]),
               "observer_intervals": record.get("tool_intervals", []),
               "base_file_inventory": base_hashes, "prompt_sha256": record.get("prompt_sha256"),
               "result_sha256": sha(public / "result.json")}
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    study, output = args.study.resolve(), args.output.resolve()
    config_path = study / "frozen-config.json"
    config = read(config_path)
    manifest = read(study / "tasks.json")
    tasks = {task["id"]: task for task in manifest["tasks"]}
    records = read(study / "results.json") if (study / "results.json").is_file() else []
    expected_ids = [f"{number:02d}-r{trial['repetition']}-{trial['task_id']}-{trial['condition']}" for number, trial in enumerate(config["schedule"], 1)]
    terminal_ids = [record["run_id"] for record in records]
    runtime_checks = {"manifest_copy_hash": sha(study / "tasks.json") == config["manifest_sha256"],
                      "frozen_runtime_artifact_hashes": all(sha(ROOT / path) == digest for path, digest in config["artifact_sha256"].items()),
                      "native_cli_hash": sha(config["cli_binary"]) == config["cli_sha256"],
                      "python_package_versions": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())) == config["packages"],
                      "terminal_inventory_is_exact_schedule_prefix": terminal_ids == expected_ids[:len(records)]}
    reviewed = []
    for record in records:
        try:
            reviewed.append(audit_run(record, tasks[record["task_id"]], config, study))
        except Exception as error:
            reviewed.append({"run_id": record["run_id"], "integrity_passed": False, "audit_error": f"{type(error).__name__}: {error}"})
    pair_checks = []
    for task in tasks.values():
        for repetition in (1, 2):
            pair = [row for row in reviewed if row.get("task_id") == task["id"] and row.get("repetition") == repetition]
            if len(pair) == 2:
                pair_checks.append({"task_id": task["id"], "repetition": repetition,
                                    "exact_base_file_bytes": pair[0]["base_file_inventory"] == pair[1]["base_file_inventory"],
                                    "exact_prompt_sha256": pair[0]["prompt_sha256"] == pair[1]["prompt_sha256"]})
    complete = terminal_ids == expected_ids
    integrity = all(runtime_checks.values()) and all(row["integrity_passed"] for row in reviewed) and all(
        row["exact_base_file_bytes"] and row["exact_prompt_sha256"] for row in pair_checks)
    data = {"schema_version": 1, "classification": "Independent CPU-only terminal-trial evidence reconciliation",
            "study": str(study.relative_to(ROOT)), "frozen_config_sha256": sha(config_path), "script_sha256": sha(__file__),
            "runtime_checks": runtime_checks, "inventory_complete": complete, "expected_trials": len(expected_ids),
            "terminal_trials": len(records), "pending_run_ids": expected_ids[len(records):], "integrity_passed": integrity,
            "pair_checks": pair_checks, "runs": reviewed,
            "limitations": ["Observed MCP item attempts, successful completed calls, and independent HTTP receipts are different measurements.",
                            "Tool intervals are event-reception intervals; batching prevents treating them as actual command/model phase latency.",
                            "Command scans identify textual evidence for manual review, not OS read isolation; evaluator/reference material is outside fixtures and MCP indexes.",
                            "Configured alpha-CLI feature removal and absence of delegation do not prove a complete tool catalog.",
                            "Actual provider usage is completed-turn input plus output; cached/reasoning fields are subsets and missing usage remains null.",
                            "Source-only indexes exclude tests/fixtures; queries asking for them are recorded without claiming they caused cost changes."],
            "reviewer_hosted_gpu_calls": 0}
    write(output / "audit.json", data)
    flagged = sum(len(row.get("observed_command_review", {}).get("flagged_observed_commands", [])) for row in reviewed)
    summary = {"inventory_complete": complete, "terminal_trials": len(records), "expected_trials": len(expected_ids),
               "integrity_passed": integrity, "runtime_checks": runtime_checks, "flagged_commands_for_manual_review": flagged,
               "audit_errors": [{"run_id": row["run_id"], "error": row.get("audit_error"), "failed_checks": [name for name, passed in row.get("checks", {}).items() if not passed]} for row in reviewed if not row["integrity_passed"]]}
    print(json.dumps(summary, indent=2))
    return 0 if integrity and (complete or not args.require_complete) else 1


if __name__ == "__main__":
    raise SystemExit(main())
