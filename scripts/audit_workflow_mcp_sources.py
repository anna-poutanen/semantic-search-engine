#!/usr/bin/env python3
"""CPU-only check of paired MCP excerpts against immutable seeded Git source.

This never runs retrieval or modifies checkouts. Pre-edit calls can be checked
against the seeded tree. Source freshness after edits needs additional evidence;
the frozen runner has no independent per-call HTTP/source snapshot receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def git(workspace, *args):
    return subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True).stdout


def envelopes(value, depth=0):
    if depth > 12:
        return
    if isinstance(value, dict):
        if isinstance(value.get("query"), str) and isinstance(value.get("results"), list):
            yield value
        for child in value.values():
            yield from envelopes(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from envelopes(child, depth + 1)
    elif isinstance(value, str):
        try:
            child = json.loads(value)
        except ValueError:
            return
        if isinstance(child, (dict, list)):
            yield from envelopes(child, depth + 1)


def physical_lines(value):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", value) if line]


def plainly_read_only(command):
    """Conservative trace classification; this is not a filesystem security proof."""
    parts = shlex.split(command)
    if len(parts) != 3 or parts[1] != "-lc" or any(marker in parts[2] for marker in (">", "<", "`", "$(", "|", "&", "\n")):
        return False
    for segment in parts[2].split(";"):
        args = shlex.split(segment)
        if not args:
            continue
        if args == ["pwd"]:
            continue
        if args[0] == "rg" and "--files" in args:
            position, accepted = 1, True
            while position < len(args):
                argument = args[position]
                if argument in ("-g", "--glob", "--iglob"):
                    if position + 1 == len(args):
                        accepted = False
                        break
                    position += 2
                elif argument in ("--files", "--hidden") or not argument.startswith("-"):
                    position += 1
                else:
                    accepted = False
                    break
            if accepted:
                continue
        return False
    return True


def audit(output, include_runs=()):
    config = json.loads((output / "frozen-config.json").read_text())
    records = json.loads((output / "results.json").read_text()) if (output / "results.json").exists() else []
    recorded = {record["run_id"]: record for record in records}
    work_root = Path(config["work_root"])
    rows, errors, limits = [], [], []
    for index, scheduled in enumerate(config["schedule"]):
        run_id = f"{index + 1:02d}-{scheduled['task_id']}-{scheduled['condition']}"
        if scheduled["condition"] != "mcp" or (run_id not in recorded and run_id not in include_runs):
            continue
        workspace = work_root / "workspaces" / run_id
        raw = work_root / "raw" / run_id / "events.jsonl"
        snapshot = raw.read_bytes()
        events = []
        for line in snapshot.decode().splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                if run_id in recorded:
                    errors.append(run_id + ": invalid terminal event line")
        initial_commits = git(workspace, "rev-list", "--max-parents=0", "HEAD").decode().splitlines()
        if len(initial_commits) != 1:
            raise ValueError("Expected a single immutable fixture root commit")
        base_commit = initial_commits[0]
        if run_id in recorded and base_commit != recorded[run_id]["base_commit"]:
            errors.append(run_id + ": immutable root differs from recorded base commit")
        tool_events = {event["item"]["id"]: (event_index, event)
                       for event_index, event in enumerate(events)
                       if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "mcp_tool_call"}
        for event_index, event in tool_events.values():
            item = event["item"]
            item_id = item["id"]
            start_index = next((i for i, earlier in enumerate(events[:event_index])
                                if earlier.get("type") == "item.started" and earlier.get("item", {}).get("id") == item_id), event_index)
            previous = events[:start_index]
            changes = [earlier["item"] for earlier in previous if earlier.get("item", {}).get("type") == "file_change"]
            commands = {earlier["item"]["id"]: earlier["item"] for earlier in previous
                        if earlier.get("item", {}).get("type") == "command_execution"}
            pre_edit = not changes and all(plainly_read_only(command["command"]) for command in commands.values())
            if not pre_edit:
                limits.append(run_id + "/" + item_id + ": call-time seeded-source identity needs manual review after prior edits/commands")
            arguments = item.get("arguments")
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            excerpts = []
            local_seen = set()
            for envelope in envelopes(item.get("result")):
                serialized = json.dumps(envelope, sort_keys=True)
                if serialized in local_seen:
                    continue
                local_seen.add(serialized)
                if envelope["query"] != arguments["query"]:
                    errors.append(run_id + "/" + item_id + ": returned query differs from arguments")
                for excerpt in envelope["results"]:
                    path, source = excerpt["path"], excerpt["source"]
                    start, end = excerpt["start_line"], excerpt["end_line"]
                    safe = not PurePosixPath(path).is_absolute() and ".." not in PurePosixPath(path).parts
                    valid_hash = digest(source.encode()) == excerpt["source_hash"]
                    lines = physical_lines(git(workspace, "show", base_commit + ":" + path).decode()) if safe else []
                    valid_bounds = (type(start) is int and type(end) is int and 1 <= start <= end <= len(lines)
                                    and len(physical_lines(source)) == end - start + 1)
                    window = "".join(lines[start - 1:end]) if valid_bounds else ""
                    exact = (window.startswith(source) if excerpt["truncated"] else window == source) if valid_bounds else False
                    if not safe or not valid_hash or not valid_bounds or (pre_edit and not exact):
                        errors.append(run_id + "/" + item_id + ": invalid returned source metadata or seeded bytes: " + path)
                    excerpts.append({key: excerpt[key] for key in ("path", "symbol", "start_line", "end_line", "source_hash", "truncated")}
                                    | {"valid_hash": valid_hash, "valid_physical_bounds": valid_bounds,
                                       "matches_seeded_git_bytes": exact})
            if item.get("status") == "completed" and not item.get("error") and not local_seen:
                errors.append(run_id + "/" + item_id + ": successful tool item has no recognized source envelope")
            rows.append({"run_id": run_id, "item_id": item_id, "trial_recorded_terminal": run_id in recorded,
                         "tool_status": item.get("status"), "arguments": arguments,
                         "seeded_git_commit": base_commit, "raw_trace_snapshot_sha256": digest(snapshot),
                         "no_observed_mutation_before_call": pre_edit,
                         "prior_file_change_events": len(changes), "prior_command_ids": list(commands),
                         "excerpts": excerpts})
    return {"passed": not errors, "errors": errors, "source_freshness_review_needed": limits,
            "auditor_sha256": digest(Path(__file__).read_bytes()), "calls": rows,
            "precall_command_review_scope": "Actual preceding commands are reviewed in this study. The automated classifier only accepts plain pwd and rg --files with listing/glob arguments; command-name whitelists, executable flags, shell startup behavior and unobserved filesystem activity are not a read-only proof.",
            "classification": "Independent CPU reconciliation of observed paired MCP tool results; no retrieval/model calls or checkout edits",
            "limits": "Paired runner records CLI MCP items and initial/final backend status, not separate per-call HTTP receipts or independently timestamped source snapshots. Read-only command classification and file-change events are observed-trace diagnostics, not an adversarial security proof. Seeded Git identity is established only for calls before observed mutations; matching source metadata does not itself prove semantic relevance or savings."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-run", action="append", default=[], help="Read completed tool items from a running trial; never regrade or inspect mutable source")
    args = parser.parse_args()
    result = audit(args.run.resolve(), args.include_run)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": result["passed"], "errors": result["errors"], "calls": len(result["calls"]),
                      "source_freshness_review_needed": result["source_freshness_review_needed"]}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
