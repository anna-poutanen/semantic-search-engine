#!/usr/bin/env python3
"""Independently inspect a completed transport diagnostic; never execute a model."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import shlex
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "results/multifile-transport-probe-2026-10-13"
RAW_ROOT = ROOT / ".cache/workflows/multifile-transport-probe-2026-10-13"
RAW = RAW_ROOT / "raw"
COMMON = ROOT / "results/multifile-common-runtime-2026-10-13/common-runtime.json"
OUTPUT = ROOT / "results/multifile-preflight-2026-10-13/transport-review.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def main():
    result, common = read(PUBLIC / "result.json"), read(COMMON)
    events, receipts = rows(RAW / "events.jsonl"), rows(RAW / "http-receipts.jsonl")
    checks = {}
    checks["common_runtime_hash"] = result["common_runtime_sha256"] == sha(COMMON)
    checks["runtime_artifact_hashes"] = all(sha(ROOT / path) == digest for path, digest in common["artifact_sha256"].items())
    checks["native_cli_hash"] = sha(common["cli_binary"]) == common["cli_sha256"]
    packages = dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()))
    checks["complete_python_package_versions"] = packages == common["packages"]
    checks["recorded_artifact_hashes"] = all(sha((RAW if kind == "raw" else PUBLIC) / path) == digest
        for kind, hashes in result["artifact_sha256"].items() for path, digest in hashes.items())
    command = read(RAW / "command.json")
    settings = {}
    for index, value in enumerate(command):
        if value == "-c":
            key, literal = command[index + 1].split("=", 1)
            settings[key] = tomllib.loads("value=" + literal)["value"]
    checks["common_model_and_settings"] = (command[0] == common["cli_binary"]
        and command[command.index("-m") + 1] == common["model"]
        and all(settings.get(key) == value for key, value in common["common_config"].items()))
    checks["isolated_cli_flags"] = all(flag in command for flag in
        ("--no-daemon", "--ignore-user-config", "--ignore-rules", "--strict-config", "--ephemeral", "--json"))
    checks["registered_text_only_adapter"] = (settings["mcp_servers.semantic_search.command"] == common["python"]
        and "--agent-text-only" in settings["mcp_servers.semantic_search.args"]
        and settings["mcp_servers.semantic_search.enabled_tools"] == ["search_code"])
    completed = [event["item"] for event in events if event.get("type") == "item.completed"]
    mcp = [item for item in completed if item.get("type") == "mcp_tool_call"]
    commands = [item for item in completed if item.get("type") == "command_execution"]
    expected_args = {"query": "workflow probe", "retrieval": "bm25", "max_results": 1,
                     "token_budget": 512, "include_dependencies": False}
    checks["exact_mcp_call_and_receipt_count"] = len(mcp) == len(receipts) == 1
    item, receipt = mcp[0], receipts[0]
    checks["mcp_identity_arguments_success"] = (item["server"] == "semantic_search" and item["tool"] == "search_code"
        and item["arguments"] == expected_args and item["status"] == "completed" and item.get("error") is None)
    content = item["result"]["content"]
    text = content[0]["text"]
    envelope = json.loads(text)
    checks["single_text_context_no_structured_payload"] = (len(content) == 1 and content[0]["type"] == "text"
        and item["result"].get("structured_content") is None and item["result"].get("structuredContent") is None)
    checks["independent_payload_hash_match"] = (hashlib.sha256(text.encode()).hexdigest() == receipt["compact_text_sha256"]
        and receipt["success"] is True and receipt["query"] == expected_args["query"])
    checks["http_settings_match"] = all(receipt["settings"].get(key) == value for key, value in expected_args.items() if key != "query")
    source = 'def workflow_probe():\n    return "MULTIFILE_PROBE_OK"\n'
    row = envelope["results"][0]
    checks["exact_returned_source_and_bounds"] = (len(envelope["results"]) == 1 and envelope["query"] == expected_args["query"]
        and row["path"] == "src/probe.py" and row["symbol"] == "workflow_probe" and row["source"] == source
        and (row["start_line"], row["end_line"]) == (1, 2) and row["source_hash"] == hashlib.sha256(source.encode()).hexdigest())
    before, after = receipt["snapshots"]["before"], receipt["snapshots"]["after"]
    checks["actual_source_snapshots"] = before == after and not receipt.get("snapshot_error")
    for phase in ("before", "after"):
        directory = RAW / "snapshots/0001" / phase
        snapshot = read(directory / "snapshot.json")
        checks["actual_source_snapshots"] &= (snapshot == receipt["snapshots"][phase]
            and all(sha(directory / path) == digest for path, digest in snapshot["files"].items())
            and (directory / "src/probe.py").read_text() == source)
    checks["reported_source_checks"] = all(value is True for record in receipt["returned_source_checks"] for key, value in record.items()
        if key in ("source_matches_snapshot", "physical_bounds_match", "source_hash_matches"))
    script = RAW_ROOT / "workspace/loopback_smoke.py"
    shell = shlex.split(commands[0]["command"])
    argv = shlex.split(shell[2]) if len(shell) == 3 and shell[1] in ("-c", "-lc") else shell
    checks["exact_loopback_command_success"] = (len(commands) == 1 and argv == [common["python"], "loopback_smoke.py"]
        and commands[0]["exit_code"] == 0 and commands[0]["status"] == "completed"
        and commands[0]["aggregated_output"].strip() == "LOOPBACK_OK" and sha(script) == result["loopback_script_sha256"])
    mcp_index = next(i for i, event in enumerate(events) if event.get("type") == "item.completed" and event.get("item", {}).get("id") == item["id"])
    command_start = next(i for i, event in enumerate(events) if event.get("type") == "item.started" and event.get("item", {}).get("id") == commands[0]["id"])
    checks["loopback_after_mcp"] = command_start > mcp_index
    current_files = {str(path.relative_to(RAW_ROOT / "workspace")): sha(path)
        for path in (RAW_ROOT / "workspace").rglob("*") if path.is_file() and ".git" not in path.parts and not path.is_symlink()}
    checks["unchanged_actual_fixture_no_file_changes"] = (current_files == result["initial_file_hashes"] == result["final_file_hashes"]
        and not any(item.get("type") == "file_change" for item in completed))
    turns = [event["usage"] for event in events if event.get("type") == "turn.completed"]
    usage = turns[0]
    checks["exact_provider_usage_receipt"] = (len(turns) == 1 and all(result["usage"].get(key) == value for key, value in usage.items())
        and result["usage"]["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]
        and result["usage"]["uncached_input_tokens"] == usage["input_tokens"] - usage["cached_input_tokens"]
        and result["per_model_call_usage"] is None)
    checks["normal_completion_without_local_models"] = (result["completed"] and result["completed_correct"]
        and not result["failed"] and not result["timed_out"] and result["agent_exit_code"] == 0
        and result["forbidden_local_model_calls"] == 0 and not result["local_model_inference"]
        and not any(event.get("type") in ("turn.failed", "error") for event in events))
    observed = read(RAW / "observed-events.json")
    public_events = rows(PUBLIC / "events.jsonl")
    checks["public_reasoning_excluded"] = not any(event.get("item", {}).get("type") == "reasoning" for event in public_events)
    checks["observed_intervals_are_reception_intervals"] = all(interval["observed_duration_s"] == interval["end"]["monotonic_s"] - interval["start"]["monotonic_s"] for interval in result["tool_intervals"])
    review = {"schema_version": 1, "classification": "Independent completed hosted transport evidence review; separate diagnostic cost",
              "checks": checks, "passed": all(checks.values()), "reviewer_hosted_gpu_calls": 0,
              "evidence_sha256": {"probe_result": sha(PUBLIC / "result.json"), "common_runtime": sha(COMMON),
                                  "raw_events": sha(RAW / "events.jsonl"), "raw_http_receipt": sha(RAW / "http-receipts.jsonl"),
                                  "script": sha(__file__)},
              "actual_usage": result["usage"], "agent_elapsed_s": result["agent_elapsed_s"],
              "independent_backend_call_s": receipt["backend_call_s"], "observer_tool_intervals": result["tool_intervals"],
              "event_records_observed": len(observed),
              "representation": "CLI normalizes structured_content to null; there is one text context and no structured payload. This is not duplicated source content. Literal raw-wire omission was independently tested for both protocol versions, not wiretapped in this hosted session.",
              "timing_limit": "Tool intervals measure event reception. Command started/completed arrived about21microseconds apart, showing batching; this cannot establish actual command latency. Whole CLI launch-to-exit and separately recorded backend timings are distinct observations.",
              "catalog_limit": "Observed MCP adoption and ordinary command execution are proven. Absence of delegation does not prove every alpha-CLI tool was removed.",
              "usage_limit": "Provider input/output are actual turn totals. Cached input and reasoning are subcategories; no per-model-call usage or billed money is inferred."}
    OUTPUT.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": review["passed"], "failed_checks": [key for key, value in checks.items() if not value],
                      "actual_tokens": result["usage"]["total_tokens"], "agent_elapsed_s": result["agent_elapsed_s"]}, indent=2))
    return 0 if review["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
