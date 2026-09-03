#!/usr/bin/env python3
"""Publish the existing CLI transport preflight without running an agent."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from semantic_search.workflows import parse_events, redact, write_json


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / ".cache/workflow-probe"
OUTPUT = ROOT / "results/workflow-runtime-preflight-2026-10-12"


def main():
    substitutions = [(str(RAW / "toy"), "<WORKSPACE>"), (str(RAW), "<EVALUATOR_LOG>"),
                     (str(ROOT), "<PROJECT>"), (str(Path.home()), "<HOME>")]
    events = [json.loads(line) for line in (RAW / "events.jsonl").read_text().splitlines()]
    metadata = json.loads((RAW / "metadata.json").read_text())
    command = json.loads((RAW / "command.json").read_text())
    parsed = parse_events(events)
    record = {
        "classification": "Standalone runtime transport preflight; not a coding-task trial or an MCP exposure test",
        "provenance": "Exported after execution from preserved raw files; no pre-run frozen task/configuration claim",
        "sandbox": "read-only",
        "model": "gpt-6.1-sol",
        "agent_elapsed_s": metadata["wall_seconds"],
        "agent_exit_code": metadata["exit_code"],
        "usage": parsed["usage"],
        "usage_event_count": parsed["reported_turns"],
        "completed": parsed["completed"],
        "failed": parsed["failed"],
        "mcp_calls": parsed["mcp_calls"],
        "command_calls": parsed["command_calls"],
        "excluded_from_coding_comparisons": True,
        "raw_sha256": {name: hashlib.sha256((RAW / name).read_bytes()).hexdigest()
                       for name in ("events.jsonl", "command.json", "metadata.json", "final.txt", "stderr.log")},
    }
    OUTPUT.mkdir(parents=True, exist_ok=False)
    public_events = [redact(event, substitutions) for event in events
                     if event.get("item", {}).get("type") != "reasoning"]
    (OUTPUT / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in public_events))
    write_json(OUTPUT / "metadata.json", metadata)
    write_json(OUTPUT / "command.json", redact(command, substitutions))
    for source, target in (("final.txt", "final.txt"), ("stderr.log", "stderr.txt")):
        (OUTPUT / target).write_text(redact((RAW / source).read_text(), substitutions))
    (OUTPUT / "prompt.txt").write_text(command[-1] + "\n")
    write_json(OUTPUT / "result.json", record)
    print(json.dumps({"output": str(OUTPUT), "usage": record["usage"],
                      "agent_elapsed_s": record["agent_elapsed_s"]}, indent=2))


if __name__ == "__main__":
    main()
