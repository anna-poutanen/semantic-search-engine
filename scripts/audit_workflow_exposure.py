#!/usr/bin/env python3
"""CPU-only independent audit of the separate hosted MCP exposure diagnostic.

No agent, MCP server, embedding or reranker is started. Raw event traces are
read only and never printed. The result cannot establish earlier tool visibility.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tomllib

from audit_workflows import completed_items, read_events, reported_usage, SECRET_PATTERNS, substitute


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ARGUMENTS = {"query": "encoded hex checksum", "retrieval": "bm25", "rerank": False,
                      "max_results": 3, "token_budget": 4096, "include_dependencies": False}


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text())


def envelopes(value, depth=0):
    if depth > 12:
        return
    if isinstance(value, dict):
        if value.get("query") == EXPECTED_ARGUMENTS["query"] and isinstance(value.get("results"), list):
            yield value
        for child in value.values():
            yield from envelopes(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from envelopes(child, depth + 1)
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return
        if isinstance(parsed, (dict, list)):
            yield from envelopes(parsed, depth + 1)


def exact_source(value, fixture):
    lines = fixture.splitlines(keepends=True)
    for envelope in envelopes(value):
        for row in envelope["results"]:
            start, end = row.get("start_line"), row.get("end_line")
            if (row.get("symbol") != "encoded_hex_checksum" or row.get("path") != "src/sample.py"
                    or type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines)):
                continue
            source = "".join(lines[start - 1:end])
            if (row.get("source") == source and "return bytes(data).hex()" in source
                    and row.get("source_hash") == hashlib.sha256(source.encode()).hexdigest()):
                return True
    return False


def audit(output: Path, pilot_config: Path, work_root: Path):
    record, config = load(output / "diagnostic.json"), load(pilot_config)
    raw, workspace = work_root / "raw", work_root / "workspace"
    errors, checks = [], []

    def require(condition, description):
        checks.append(description)
        if not condition:
            errors.append(description)

    require(record["frozen_pilot_config_sha256"] == sha(pilot_config), "Original frozen configuration digest")
    require(record["model"] == config["model"] and record["common_config"] == config["common_config"],
            "Assigned model and common settings")
    require(record["cli_version"] == config["cli_version"] and sha(Path(config["cli_binary"])) == config["cli_sha256"],
            "Frozen CLI version and binary")
    script = ROOT / "scripts/probe_workflow_mcp.py"
    require(record["diagnostic_script_sha256"] == sha(script), "Recorded diagnostic source digest")
    script_tree = ast.parse(script.read_text())
    declared_fixture = next(ast.literal_eval(node.value) for node in script_tree.body
                            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "SOURCE"
                                                                    for target in node.targets))
    fixture = (output / "fixture.py").read_text()
    require(fixture == declared_fixture == (workspace / "src/sample.py").read_text(), "Unchanged lexical source fixture")
    status = subprocess.run(["git", "-C", str(workspace), "status", "--porcelain"], check=True, capture_output=True)
    require(not status.stdout.strip() and record["working_tree_unchanged"], "Unchanged diagnostic Git checkout")

    substitutions = [(str(workspace), "<WORKSPACE>"), (str(raw), "<DIAGNOSTIC_LOG>"),
                     (str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]
    events, invalid = read_events(raw / "events.jsonl")
    published, public_invalid = read_events(output / "events.jsonl")
    expected_events = [substitute(event, substitutions) for event in events
                       if event.get("item", {}).get("type") != "reasoning"]
    require(not public_invalid and published == expected_events, "Exact public trace redaction and reasoning removal")
    require(invalid == len(record["invalid_event_lines"]), "Invalid event line count")
    require(sha(raw / "events.jsonl") == record["raw_events_sha256"]
            and sha(output / "events.jsonl") == record["published_events_sha256"], "Raw and published trace digests")
    usage, turns = reported_usage(events)
    require(usage == record["usage"] and turns == record["reported_turns"] == 1, "Exact provider token accounting")
    require(record["completed"] and not record["failed"] and not record["timed_out"] and record["agent_exit_code"] == 0,
            "Successful terminal diagnostic status")
    require(record["completed"] == any(event.get("type") == "turn.completed" for event in events)
            and record["failed"] == any(event.get("type") == "turn.failed" for event in events), "Terminal events agree")
    tools, commands = completed_items(events, "mcp_tool_call"), completed_items(events, "command_execution")
    require(len(tools) == record["mcp_calls"] and not commands and record["command_calls"] == 0, "Recorded MCP and ordinary command counts")
    valid_tools = []
    for item in tools.values():
        arguments = item.get("arguments")
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        result = item.get("result")
        valid = (item.get("server") == "semantic_search" and item.get("tool") == "search_code"
                 and item.get("status") == "completed" and not item.get("error")
                 and arguments == EXPECTED_ARGUMENTS and exact_source(result, fixture))
        if isinstance(result, dict) and (result.get("isError") or result.get("is_error")):
            valid = False
        valid_tools.append(valid)
    valid_http = [call for call in record["http_search_calls"] if call["query"] == EXPECTED_ARGUMENTS["query"]
                  and all(call["arguments"].get(key) == value for key, value in EXPECTED_ARGUMENTS.items() if key != "query")
                  and not call.get("error") and exact_source(call.get("response"), fixture)]
    require(sum(valid_tools) == record["successful_expected_tool_calls"] >= 1, "Exact MCP identity, arguments and returned source")
    require(len(valid_http) == record["successful_expected_http_calls"] >= 1, "Matching backend HTTP search and returned source")
    require(record["tool_exposure_confirmed"] == bool(sum(valid_tools) and valid_http), "Exposure claim follows observed tool and HTTP evidence")
    require(record["forbidden_embedding_attempts"] == record["forbidden_h2o_attempts"] == 0, "No local-model inference attempts")
    require(record["requested_tool_arguments"] == EXPECTED_ARGUMENTS, "Requested lexical-only arguments")
    command = load(output / "command.json")
    settings = tomllib.loads("\n".join(command[i + 1] for i, value in enumerate(command) if value == "-c"))
    ordinary = {key: value for key, value in settings.items() if key not in ("mcp_servers", "shell_environment_policy")}
    expected_ordinary = tomllib.loads("\n".join(key + "=" + json.dumps(value) for key, value in config["common_config"].items()))
    require(ordinary == expected_ordinary and command[command.index("-m") + 1] == config["model"], "Exact common CLI configuration")
    require(command[0] == config["cli_binary"] and command[command.index("--sandbox") + 1] == config["sandbox"], "CLI executable and sandbox")
    require(all(flag in command for flag in ("--ignore-user-config", "--ignore-rules", "--strict-config", "--ephemeral", "--json", "--no-daemon")), "Requested CLI isolation flags")
    server = settings["mcp_servers"]["semantic_search"]
    require(set(settings["mcp_servers"]) == {"semantic_search"} and server["enabled_tools"] == ["search_code"]
            and server["required"] is True and server["command"] == substitute(config["python"], substitutions)
            and server["cwd"] == "<PROJECT>" and server["env"] == {"PYTHONPATH": "<PROJECT>/src"}, "Sole frozen MCP registration")
    require((output / "prompt.txt").read_text() == command[-1], "Published diagnostic prompt")
    for path in output.rglob("*"):
        if path.is_file() and path.name != "audit.json" and path.suffix in (".json", ".jsonl", ".txt", ".py"):
            value = path.read_text()
            require(str(Path.home()) not in value and not any(pattern.search(value) for pattern in SECRET_PATTERNS),
                    "Public privacy scan: " + str(path.relative_to(output)))
    for field in ("agent_elapsed_s", "setup_s"):
        value = record[field]
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0, "Finite recorded timing: " + field)
    return {"passed": not errors, "errors": errors, "checks": checks,
            "auditor_sha256": sha(Path(__file__)), "provider_usage": usage,
            "tool_exposure_confirmed": record["tool_exposure_confirmed"],
            "limits": "Separate CPU audit of this explicit post-pilot diagnostic. No hosted or local-model calls were made by the auditor. This establishes exposure during the diagnostic, not earlier paired agents' visibility, natural adoption, retrieval benefit, or dollar cost."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--pilot-config", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    args = parser.parse_args()
    output = args.run.resolve()
    result = audit(output, args.pilot_config.resolve(), args.work_root.resolve())
    (output / "audit.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": result["passed"], "errors": result["errors"]}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
