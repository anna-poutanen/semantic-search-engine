#!/usr/bin/env python3
"""CPU-only reporting for the separately frozen 16-attempt multifile study.

Never executes saved commands, grades a candidate, accesses hosted services, or
loads a model. Published event strings provide reproducible output proxies;
optional ignored raw logs support exact MCP-to-HTTP payload linkage.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                "output_tokens", "reasoning_output_tokens", "total_tokens", "uncached_input_tokens")
CONDITIONS = ("baseline", "mcp")
COUNT_FIELDS = ("passed", "skipped", "failed", "errors", "xfailed", "xpassed", "deselected")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()] if Path(path).is_file() else []


def valid_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def valid_count(value):
    return type(value) is int and value >= 0


def physical_lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def elapsed(start, end):
    a, b = (start or {}).get("monotonic_s"), (end or {}).get("monotonic_s")
    return b - a if valid_number(a) and valid_number(b) and b >= a else None


def numeric_summary(values, expected):
    known = [value for value in values if valid_number(value)]
    return {"observed": len(known), "expected": expected, "missing": expected - len(known),
            "measured_sum": sum(known) if known else None,
            "complete_total": sum(known) if len(known) == expected and expected else None,
            "median": statistics.median(known) if known else None,
            "mean": statistics.mean(known) if known else None,
            "min": min(known) if known else None, "max": max(known) if known else None}


def offline_encoder():
    """Load cached cl100k data only; an absent cache cannot trigger a download."""
    import tiktoken
    import tiktoken.load
    cache = ROOT / ".cache/tokenizers"
    if not cache.is_dir():
        raise RuntimeError("Offline tokenizer cache unavailable; use --no-token-proxies")
    original = tiktoken.load.read_file
    previous = os.environ.get("TIKTOKEN_CACHE_DIR")
    def no_network(path):
        raise RuntimeError("Offline report cannot download tokenizer data")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
    tiktoken.load.read_file = no_network
    try:
        return tiktoken.get_encoding("cl100k_base")
    finally:
        tiktoken.load.read_file = original
        if previous is None:
            os.environ.pop("TIKTOKEN_CACHE_DIR", None)
        else:
            os.environ["TIKTOKEN_CACHE_DIR"] = previous


def text_size(text, encoder):
    return {"utf8_bytes": len(text.encode("utf-8")),
            "cl100k_token_proxy": len(encoder.encode(text, disallowed_special=())) if encoder else None}


def usage_from_events(events):
    turns = [event["usage"] for event in events if event.get("type") == "turn.completed"
             and isinstance(event.get("usage"), dict)]
    if not turns:
        return None, 0
    result = {field: sum(turn[field] for turn in turns) if all(valid_count(turn.get(field)) for turn in turns)
              else None for field in USAGE_FIELDS[:5]}
    result["total_tokens"] = (result["input_tokens"] + result["output_tokens"]
                              if result["input_tokens"] is not None and result["output_tokens"] is not None else None)
    result["uncached_input_tokens"] = (result["input_tokens"] - result["cached_input_tokens"]
        if result["input_tokens"] is not None and result["cached_input_tokens"] is not None
        and result["cached_input_tokens"] <= result["input_tokens"] else None)
    return result, len(turns)


def command_phase(command):
    tags = [name for name, expression in (
        ("test", r"\b(?:pytest|unittest)\b"), ("source_read", r"\b(?:cat|head|tail)\s|\bsed\s+-n"),
        ("search", r"\b(?:rg|grep|find)\s"), ("edit_review", r"\bgit\s+diff\b"),
        ("debug", r"\b(?:ruff|mypy)\b|python\s+-c")) if re.search(expression, command)]
    if "test" in tags:
        phase = "test"
    elif "source_read" in tags and "search" in tags:
        phase = "mixed_search_source_read"
    else:
        phase = next((tag for tag in ("source_read", "search", "edit_review", "debug") if tag in tags), "workspace")
    return phase, tags


def event_tools(events, encoder):
    seen, commands, mcp = set(), [], []
    source_payloads = []
    all_ids = defaultdict(set)
    phases = defaultdict(lambda: {"completed_calls": 0, "output_utf8_bytes": 0,
                                  "output_cl100k_token_proxy": 0 if encoder else None})
    for index, event in enumerate(events):
        item = event.get("item") or {}
        kind = item.get("type")
        key = item.get("id", f"event-{index}")
        if kind in ("command_execution", "mcp_tool_call"):
            all_ids[kind].add(key)
        if event.get("type") != "item.completed" or (kind, key) in seen:
            continue
        seen.add((kind, key))
        if kind == "command_execution":
            command, output = item.get("command", ""), item.get("aggregated_output", "")
            phase, tags = command_phase(command)
            size = text_size(output, encoder)
            commands.append({"item_id": key, "event_index": index, "phase": phase, "tags": tags,
                "status": item.get("status"), "exit_code": item.get("exit_code"), "output": size,
                "command_text": text_size(command, encoder)})
        elif kind == "mcp_tool_call":
            result = item.get("result") or {}
            blocks = result.get("content", []) if isinstance(result, dict) else []
            text = "\n".join(block["text"] for block in blocks if block.get("type") == "text" and isinstance(block.get("text"), str))
            is_search_code = item.get("server") == "semantic_search" and item.get("tool") == "search_code"
            phase, size = "mcp_search" if is_search_code else "other_mcp", text_size(text, encoder)
            payload = None
            try:
                payload = json.loads(text) if text else None
            except (ValueError, TypeError):
                pass
            structured_present = isinstance(result, dict) and any(name in result for name in
                                                                  ("structuredContent", "structured_content"))
            rows = payload.get("results", []) if isinstance(payload, dict) else []
            excerpts = []
            for row in rows:
                source = row.get("source", "")
                later = [event["item"].get("id") for event in events[index + 1:]
                         if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "command_execution"
                         and source.strip() and source.strip() in event["item"].get("aggregated_output", "")]
                start, end = row.get("start_line"), row.get("end_line")
                excerpts.append({name: row.get(name) for name in
                    ("path", "symbol", "start_line", "end_line", "origin", "related_to", "truncated")})
                excerpts[-1].update(source_size=text_size(source, encoder),
                    delivered_hash_matches=hashlib.sha256(source.encode()).hexdigest() == row.get("source_hash"),
                    physical_bounds_match=(valid_count(start) and start >= 1 and valid_count(end)
                                           and end == start + len(physical_lines(source)) - 1),
                    exact_full_excerpt_seen_in_later_stdout=sorted(set(later)))
            mcp.append({"item_id": key, "event_index": index, "server": item.get("server"), "tool": item.get("tool"),
                "arguments": item.get("arguments"), "status": item.get("status"),
                "error": item.get("error"), "is_error": bool(isinstance(result, dict) and
                (result.get("isError") or result.get("is_error"))), "text_payload": size,
                "published_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "is_semantic_search_code": is_search_code,
                "structured_field_present": structured_present, "parseable_json": isinstance(payload, dict),
                "structured_payload_present": bool(isinstance(result, dict) and any(result.get(name) is not None for name in ("structuredContent", "structured_content"))),
                "reported_payload_token_usage": payload.get("token_usage") if isinstance(payload, dict) else None,
                "results": excerpts, "result_count": len(excerpts),
                "dependency_results": sum(row.get("origin") == "dependency" for row in excerpts),
                "truncated_results": sum(row.get("truncated") is True for row in excerpts)})
            contained = []
            for containing_index, parent in enumerate(rows):
                for contained_index, child in enumerate(rows):
                    if containing_index == contained_index or parent.get("path") != child.get("path"):
                        continue
                    bounds = [parent.get("start_line"), parent.get("end_line"), child.get("start_line"), child.get("end_line")]
                    if not all(valid_count(value) and value >= 1 for value in bounds) or not (bounds[0] <= bounds[2] <= bounds[3] <= bounds[1]):
                        continue
                    exact = "".join(physical_lines(parent.get("source", ""))[bounds[2] - bounds[0]:bounds[3] - bounds[0] + 1])
                    if exact == child.get("source") and exact:
                        contained.append({"containing_result_index": containing_index, "contained_result_index": contained_index,
                            "path": child["path"], "containing_symbol": parent.get("symbol"), "contained_symbol": child.get("symbol"),
                            "source_size": text_size(exact, encoder)})
            mcp[-1]["same_call_fully_contained_source"] = contained
            source_payloads.append(rows)
            token_usage = mcp[-1]["reported_payload_token_usage"] or {}
            mcp[-1]["reported_tokens_match_published_text"] = (token_usage.get("tokens") == size["cl100k_token_proxy"]
                if encoder and valid_count(token_usage.get("tokens")) else None)
            mcp[-1]["reported_payload_within_budget"] = (token_usage["tokens"] <= token_usage["budget"]
                if valid_count(token_usage.get("tokens")) and valid_count(token_usage.get("budget")) else None)
        else:
            continue
        phases[phase]["completed_calls"] += 1
        phases[phase]["output_utf8_bytes"] += size["utf8_bytes"]
        if encoder:
            phases[phase]["output_cl100k_token_proxy"] += size["cl100k_token_proxy"]
    for call_index, rows in enumerate(source_payloads):
        for result_index, source_row in enumerate(rows):
            source = source_row.get("source", "")
            repeated = []
            for later_index in range(call_index + 1, len(source_payloads)):
                for later_result_index, later_row in enumerate(source_payloads[later_index]):
                    if source and source_row.get("path") == later_row.get("path") and source in later_row.get("source", ""):
                        repeated.append({"item_id": mcp[later_index]["item_id"], "result_index": later_result_index,
                            "same_line_bounds": source_row.get("start_line") == later_row.get("start_line")
                                                and source_row.get("end_line") == later_row.get("end_line"),
                            "later_symbol": later_row.get("symbol")})
            mcp[call_index]["results"][result_index]["exact_excerpt_present_in_later_mcp_results"] = repeated
    return {"commands": commands, "mcp_calls": mcp, "phase_output_proxies": dict(sorted(phases.items())),
        "attempted_command_items": len(all_ids["command_execution"]), "attempted_mcp_items": len(all_ids["mcp_tool_call"]),
        "unfinished_command_items": len(all_ids["command_execution"] - {row["item_id"] for row in commands}),
        "unfinished_mcp_items": len(all_ids["mcp_tool_call"] - {row["item_id"] for row in mcp}),
        "completed_command_items": len(commands), "completed_mcp_items": len(mcp),
        "completed_search_code_items": sum(row["is_semantic_search_code"] for row in mcp),
        "unexpected_mcp_items": [{"item_id": row["item_id"], "server": row["server"], "tool": row["tool"]}
                                 for row in mcp if not row["is_semantic_search_code"]],
        "total_output_utf8_bytes": sum(row["output_utf8_bytes"] for row in phases.values()),
        "total_output_cl100k_token_proxy": sum(row["output_cl100k_token_proxy"] for row in phases.values()) if encoder else None,
        "exact_full_excerpt_rereads": sum(bool(row["exact_full_excerpt_seen_in_later_stdout"])
                                            for call in mcp for row in call["results"]),
        "same_call_source_containment_pairs": sum(len(call["same_call_fully_contained_source"]) for call in mcp),
        "source_excerpts_repeated_in_later_mcp": sum(bool(row["exact_excerpt_present_in_later_mcp_results"])
                                                    for call in mcp for row in call["results"])}


def receipt_audit(receipts, raw_events=None):
    raw_payloads = {}
    for event in raw_events or []:
        item = event.get("item") or {}
        if event.get("type") != "item.completed" or item.get("type") != "mcp_tool_call":
            continue
        result = item.get("result") or {}
        if not isinstance(result, dict):
            continue
        blocks = result.get("content", [])
        text = "\n".join(block["text"] for block in blocks if block.get("type") == "text")
        raw_payloads[item.get("id")] = hashlib.sha256(text.encode()).hexdigest()
    rows = []
    for receipt in receipts:
        checks = receipt.get("returned_source_checks")
        response_rows = (receipt.get("response") or {}).get("results", [])
        before, after = (receipt.get("snapshots") or {}).get("before"), (receipt.get("snapshots") or {}).get("after")
        flags = {name: (all(check.get(name) is True for check in checks)
                 if isinstance(checks, list) and len(checks) == len(response_rows) and checks else
                 None)
                 for name in ("source_matches_snapshot", "physical_bounds_match", "source_hash_matches")}
        linked = [key for key, value in raw_payloads.items() if value == receipt.get("compact_text_sha256")]
        rows.append({"number": receipt.get("number"), "query": receipt.get("query"), "settings": receipt.get("settings"),
            "success": receipt.get("success"), "error": receipt.get("error"), "snapshot_error": receipt.get("snapshot_error"),
            "returned_results": len(response_rows), "source_checks": flags,
            "source_check_count_matches_results": isinstance(checks, list) and len(checks) == len(response_rows),
            "source_manifest_unchanged_during_call": before == after if before is not None and after is not None else None,
            "source_manifest_before": (before or {}).get("manifest_sha256"), "source_manifest_after": (after or {}).get("manifest_sha256"),
            "backend_call_s": receipt.get("backend_call_s"), "snapshot_before_s": receipt.get("snapshot_before_s"),
            "snapshot_after_s": receipt.get("snapshot_after_s"), "http_observer_span_s": elapsed(receipt.get("start"), receipt.get("end")),
            "compact_text_sha256": receipt.get("compact_text_sha256"),
            "indexstats": (receipt.get("response") or {}).get("indexstats"),
            "raw_payload_matching_mcp_items": linked if raw_events is not None else None})
    return {"calls": rows, "http_call_count": len(rows), "successful_calls": sum(row["success"] is True for row in rows),
        "failed_calls": sum(row["success"] is False for row in rows),
        "snapshot_error_calls": sum(bool(row["snapshot_error"]) for row in rows),
        "source_changed_during_call": sum(row["source_manifest_unchanged_during_call"] is False for row in rows),
        "source_validation_failed_calls": sum(any(value is False for value in row["source_checks"].values()) for row in rows),
        "source_validation_unavailable_calls": sum(row["returned_results"] > 0 and any(value is None for value in row["source_checks"].values()) for row in rows),
        "raw_link_validation_available": raw_events is not None,
        "raw_link_ambiguous_receipts": sum(row["success"] is True and len(row["raw_payload_matching_mcp_items"]) > 1 for row in rows)
                                      if raw_events is not None else None,
        "successful_receipts_without_raw_mcp_link": sum(row["success"] is True and not row["raw_payload_matching_mcp_items"] for row in rows)
                                                   if raw_events is not None else None,
        "backend_call_s": numeric_summary([row["backend_call_s"] for row in rows], len(rows)),
        "snapshot_observer_overhead_s": numeric_summary([
            row["snapshot_before_s"] + row["snapshot_after_s"] if valid_number(row["snapshot_before_s"])
            and valid_number(row["snapshot_after_s"]) else None for row in rows], len(rows))}


def analyze_record(slot, record, directory, finalized, encoder, raw_directory=None, task=None):
    identity = {key: slot[key] for key in ("task_id", "condition", "repetition")}
    if record is None:
        return {**identity, "record_present": False, "finalized": False, "status": "not_recorded",
                "completed_correct": False, "usage": None, "timing_s": {}, "flags": ["No result recorded for scheduled attempt"]}
    flags = []
    events_path = directory / "events.jsonl"
    events = read_jsonl(events_path)
    usage, turns = usage_from_events(events)
    reported = record.get("usage")
    if usage is None and reported is not None:
        flags.append("Result reports usage without a completed-turn event")
    if usage is not None and any(usage[field] != (reported or {}).get(field) for field in USAGE_FIELDS[:6]):
        flags.append("Completed-turn usage and result metadata differ")
    if reported and reported.get("cached_input_tokens") is not None and reported.get("input_tokens") is not None and reported["cached_input_tokens"] > reported["input_tokens"]:
        flags.append("Cached input exceeds gross input")
    if reported and reported.get("reasoning_output_tokens") is not None and reported.get("output_tokens") is not None and reported["reasoning_output_tokens"] > reported["output_tokens"]:
        flags.append("Reasoning subset exceeds output")
    tools = event_tools(events, encoder)
    scope = (task or {}).get("source_root")
    for call in tools["mcp_calls"]:
        query = (call.get("arguments") or {}).get("query", "")
        call["configured_index_source_root"] = scope
        call["query_scope_clue"] = ("Query mentions tests/pytest/fixtures while the configured source root excludes the top-level tests tree; heuristic, not proof of intended scope or a retrieval error."
            if isinstance(query, str) and scope and scope not in (".", "", "tests")
            and not scope.startswith("tests/") and re.search(r"\b(?:tests?|pytest|fixtures?)\b", query, re.I) else None)
    for field, observed in (("mcp_calls", tools["completed_mcp_items"]), ("command_calls", tools["completed_command_items"]), ("reported_turns", turns)):
        if field in record and record[field] != observed:
            flags.append(f"Recorded {field} differs from published events")
    phases = record.get("phase_timestamps") or {}
    phase_time = lambda name: elapsed(phases.get(name + "_start"), phases.get(name + "_end"))
    timing = {"agent_cli": record.get("agent_elapsed_s"), "service_setup_excluded_from_agent": phase_time("service_setup"),
        "fixture_and_seed_check": phase_time("fixture"), "grading": phase_time("grading"),
        "shutdown": phase_time("shutdown"), "trial_wall": elapsed(phases.get("fixture_start"), phases.get("trial_end")),
        "selected_checker": elapsed((record.get("grade") or {}).get("start"), (record.get("grade") or {}).get("end"))}
    if slot["condition"] == "baseline" and not any(name.startswith("service_setup") for name in phases):
        timing["service_setup_excluded_from_agent"] = 0.0
    if valid_number(timing["fixture_and_seed_check"]) and valid_number(timing["grading"]):
        timing["fixture_plus_grading_evaluator"] = timing["fixture_and_seed_check"] + timing["grading"]
    else:
        timing["fixture_plus_grading_evaluator"] = None
    receipts_path = directory / "http-receipts.jsonl"
    receipts = read_jsonl(receipts_path)
    raw_events = None
    if raw_directory is not None and (raw_directory / "events.jsonl").is_file():
        raw_events = read_jsonl(raw_directory / "events.jsonl")
        # Raw reasoning never enters the report, proxies, or published result.
    http = receipt_audit(receipts, raw_events)
    published_hashes = record.get("artifact_sha256", {}).get("published", {})
    artifact_checks = {name: digest(directory / name) == expected if (directory / name).is_file() else False
                       for name, expected in published_hashes.items()}
    if any(not value for value in artifact_checks.values()):
        flags.append("Published artifact differs from captured hash")
    selected, upstream = record.get("grade") or {}, record.get("compatibility") or {}
    frozen_selected = (task or {}).get("seed_validation", {}).get("reference")
    selected_matches_frozen = (selected.get("detail") == frozen_selected if frozen_selected is not None else None)
    if selected.get("passed") and selected_matches_frozen is False:
        flags.append("Selected checker pass differs from the frozen reference outcome")
    integrity = {"changed_or_deleted_original_tests_and_configs": upstream.get("changed_or_deleted_original_tests", []),
        "added_configuration_removed_for_grading": upstream.get("added_configuration_removed_for_grading", []),
        "test_edits_require_review": upstream.get("test_edits_require_review"),
        "candidate_changed_files": record.get("changed_files", []),
        "original_test_and_config_hash_count": len(upstream.get("original_test_and_config_hashes", {}))}
    checks = []
    for index, command in enumerate(upstream.get("commands", [])):
        expected, actual = command.get("expected_summary"), command.get("actual_summary")
        frozen_commands = (task or {}).get("compatibility_commands", [])
        frozen_expected = frozen_commands[index].get("expected_summary") if index < len(frozen_commands) else None
        matches = isinstance(expected, dict) and set(expected) == set(COUNT_FIELDS) and expected == actual
        checks.append({"passed": command.get("passed"), "exit_code": command.get("exit_code"),
            "expected_summary": expected, "actual_summary": actual, "exact_counts_match": matches,
            "recorded_expected_matches_frozen": expected == frozen_expected if frozen_expected is not None else None,
            "actual_matches_frozen": actual == frozen_expected if frozen_expected is not None else None,
            "elapsed_s": elapsed(command.get("start"), command.get("end")), "error": command.get("error")})
    verified_correct = selected.get("passed") is True and upstream.get("passed") is True
    if bool(record.get("correct")) != verified_correct:
        flags.append("Recorded correctness differs from independent checker/upstream flags")
    if any(row["passed"] is True and not row["exact_counts_match"] for row in checks):
        flags.append("An upstream pass lacks the exact frozen counts")
    if any(row["recorded_expected_matches_frozen"] is False for row in checks):
        flags.append("An upstream count declaration differs from frozen tasks.json")
    if any(row["passed"] is True and row["actual_matches_frozen"] is False for row in checks):
        flags.append("A claimed upstream pass differs from the counts in frozen tasks.json")
    if task and len(checks) != len(task.get("compatibility_commands", [])):
        flags.append("Recorded upstream command count differs from frozen task")
    frozen_test_hashes = (task or {}).get("upstream_test_sha256")
    recorded_originals = upstream.get("original_test_and_config_hashes", {})
    originals_match_frozen = (all(recorded_originals.get(name) == expected for name, expected in frozen_test_hashes.items())
                             if isinstance(frozen_test_hashes, dict) and frozen_test_hashes else None)
    if originals_match_frozen is False:
        flags.append("Original test/config inventory differs from frozen hashes")
    independently_verified_correct = bool(verified_correct and selected.get("exit_code") == 0
        and selected_matches_frozen is not False and checks and all(
            row["passed"] is True and row["exit_code"] == 0 and row["exact_counts_match"]
            and row["recorded_expected_matches_frozen"] is not False and row["actual_matches_frozen"] is not False
            for row in checks) and (not task or len(checks) == len(task.get("compatibility_commands", []))))
    independently_completed_correct = bool(independently_verified_correct and record.get("completed")
        and not record.get("failed") and not record.get("timed_out") and record.get("agent_exit_code") == 0
        and not record.get("infrastructure_error") and not record.get("grading_error"))
    if record.get("completed_correct") and not (verified_correct and record.get("completed") and not record.get("failed")
        and not record.get("timed_out") and record.get("agent_exit_code") == 0):
        flags.append("Completed-correct flag violates normal completion requirements")
    status = ("partial" if not finalized else "timeout" if record.get("timed_out") else "infrastructure_failure"
        if record.get("infrastructure_error") else "agent_failure" if record.get("failed") else "grading_failure"
        if record.get("grading_error") else "completed_correct" if independently_completed_correct else "incorrect_or_incomplete")
    intervals = record.get("tool_intervals", [])
    return {**identity, "run_id": record.get("run_id", directory.name), "record_present": True, "finalized": finalized,
        "status": status, "completed": record.get("completed"), "correct": independently_verified_correct,
        "recorded_correct": record.get("correct"), "recorded_completed_correct": record.get("completed_correct"),
        "completed_correct": independently_completed_correct, "timed_out": record.get("timed_out", False),
        "agent_failed": record.get("failed", False), "agent_exit_code": record.get("agent_exit_code"),
        "infrastructure_error": record.get("infrastructure_error"), "grading_error": record.get("grading_error"),
        "usage": usage, "usage_matches_result": (all(usage[field] == (reported or {}).get(field) for field in USAGE_FIELDS[:6])
                                                 if usage is not None else reported is None),
        "usage_source": "Published completed-turn events; unavailable usage remains null", "reported_turns": turns,
        "timing_s": timing, "event_observer_spans_s": [row.get("observed_duration_s") for row in intervals],
        "event_observer_warning": "CLI can batch/queue item events; these reception spans are not exact tool, queue, provider, or thinking durations.",
        "tools": tools, "http": http, "checks": {"selected": {"passed": selected.get("passed"), "exit_code": selected.get("exit_code"),
        "detail": selected.get("detail"), "error": selected.get("error"), "matches_frozen_reference": selected_matches_frozen},
        "upstream_passed": upstream.get("passed"), "upstream_commands": checks, "original_hashes_match_frozen": originals_match_frozen},
        "test_integrity": integrity, "pair_provenance": {"base_tree_sha256": record.get("base_tree_sha256"),
        "prompt_sha256": record.get("prompt_sha256")}, "artifact_checks": artifact_checks,
        "artifact_sha256": {name: digest(directory / name) for name in ("result.json", "events.jsonl", "http-receipts.jsonl", "changes.patch") if (directory / name).is_file()},
        "qualitative_patch_review": read_json(directory / "patch-review.json") if (directory / "patch-review.json").is_file() else None,
        "flags": flags}


def comparison(a, b):
    if not valid_number(a) or not valid_number(b):
        return {"baseline": a, "mcp": b, "difference": None, "change_pct": None}
    return {"baseline": a, "mcp": b, "difference": b - a, "change_pct": 100 * (b / a - 1) if a else None}


def metric(row, name):
    if name in USAGE_FIELDS:
        return (row.get("usage") or {}).get(name)
    if name in ("agent_cli", "service_setup_excluded_from_agent", "grading", "trial_wall"):
        return row.get("timing_s", {}).get(name)
    return row.get("tools", {}).get(name)


PAIR_METRICS = (*USAGE_FIELDS, "agent_cli", "service_setup_excluded_from_agent", "grading", "trial_wall",
                "completed_command_items", "completed_mcp_items", "completed_search_code_items", "total_output_utf8_bytes", "total_output_cl100k_token_proxy",
                "exact_full_excerpt_rereads", "same_call_source_containment_pairs", "source_excerpts_repeated_in_later_mcp")


def aggregate_pairs(pairs):
    result = {"declared_pairs": len(pairs), "metrics": {}}
    for name in PAIR_METRICS:
        available = [row["metrics"][name] for row in pairs if valid_number(row["metrics"][name]["baseline"])
                     and valid_number(row["metrics"][name]["mcp"])]
        baseline = sum(row["baseline"] for row in available) if available else None
        mcp = sum(row["mcp"] for row in available) if available else None
        percentages = [row["change_pct"] for row in available if row["change_pct"] is not None]
        result["metrics"][name] = {"observed_pairs": len(available), "missing_pairs": len(pairs) - len(available),
            "baseline_measured_sum": baseline, "mcp_measured_sum": mcp,
            "measured_sums_change_pct": 100 * (mcp / baseline - 1) if baseline else None,
            "median_pair_change_pct": statistics.median(percentages) if percentages else None,
            "mean_pair_change_pct": statistics.mean(percentages) if percentages else None,
            "pct_observed_pairs": len(percentages)}
    return result


def build_report(study, *, encoder=None, raw_root=None, allow_partial=False):
    study = Path(study)
    config_path = study / "frozen-config.json"
    config = read_json(config_path)
    tasks_path = study / "tasks.json"
    tasks = {task["id"]: task for task in read_json(tasks_path).get("tasks", [])} if tasks_path.is_file() else {}
    schedule = config.get("schedule", [])
    key = lambda row: (row["task_id"], row["repetition"], row["condition"])
    expected = {key(row): (number, row) for number, row in enumerate(schedule, 1)}
    if len(schedule) != 16 or len(expected) != 16 or len({row["task_id"] for row in schedule}) != 4:
        raise ValueError("This report requires the new frozen 16-attempt, four-task schedule")
    for task in {row["task_id"] for row in schedule}:
        if {(row["repetition"], row["condition"]) for row in schedule if row["task_id"] == task} != {
            (repetition, condition) for repetition in (1, 2) for condition in CONDITIONS}:
            raise ValueError("Every task needs two repetitions of both conditions")
    aggregate_path = study / "results.json"
    recorded = read_json(aggregate_path) if aggregate_path.is_file() else []
    records = {}
    for row in recorded:
        identity = key(row)
        if identity not in expected or identity in records:
            raise ValueError("Out-of-schedule or duplicate/retried attempt cannot be silently pooled")
        records[identity] = row
    if len(records) != 16 and not allow_partial:
        raise ValueError("All 16 finalized attempts are required; use --allow-partial to show missing slots")
    if raw_root is None:
        value = config.get("work_root")
        if isinstance(value, str) and "<" not in value and Path(value).is_dir():
            raw_root = Path(value)
    rows = []
    for number, slot in enumerate(schedule, 1):
        identity = key(slot)
        name = f"{number:02d}-r{slot['repetition']}-{slot['task_id']}-{slot['condition']}"
        directory = study / "runs" / name
        record = records.get(identity)
        finalized = record is not None
        if record is None and (directory / "result.json").is_file():
            record = read_json(directory / "result.json")
        raw_directory = Path(raw_root) / "raw" / name if raw_root else None
        rows.append(analyze_record(slot, record, directory, finalized, encoder, raw_directory, tasks.get(slot["task_id"])))
    review_path = study / "patch-review.json"
    review_record = read_json(review_path) if review_path.is_file() else {}
    reviews = {review.get("run_id"): review for review in review_record.get("runs", [])}
    for row in rows:
        if row.get("run_id") in reviews:
            row["qualitative_patch_review"] = reviews[row["run_id"]]
    conditions = {}
    for condition in CONDITIONS:
        subset = [row for row in rows if row["condition"] == condition]
        timing_fields = sorted({field for row in subset for field in row["timing_s"]})
        cache_rows = [row["usage"] for row in subset if row.get("usage") and valid_count(row["usage"].get("input_tokens"))
                      and valid_count(row["usage"].get("cached_input_tokens")) and row["usage"]["cached_input_tokens"] <= row["usage"]["input_tokens"]]
        cache_input = sum(row["input_tokens"] for row in cache_rows)
        conditions[condition] = {"scheduled": len(subset), "record_present": sum(row["record_present"] for row in subset),
            "finalized": sum(row["finalized"] for row in subset), "completed_correct": sum(bool(row["completed_correct"]) for row in subset),
            "correct": sum(bool(row.get("correct")) for row in subset), "statuses": dict(Counter(row["status"] for row in subset)),
            "usage": {field: numeric_summary([(row.get("usage") or {}).get(field) for row in subset], len(subset)) for field in USAGE_FIELDS},
            "cached_input_fraction": {"observed_trials": len(cache_rows), "missing_trials": len(subset) - len(cache_rows),
                "cached_fraction_of_coobserved_input": sum(row["cached_input_tokens"] for row in cache_rows) / cache_input if cache_input else None},
            "timing_s": {field: numeric_summary([row["timing_s"].get(field) for row in subset], len(subset)) for field in timing_fields},
            "tools": {field: numeric_summary([row.get("tools", {}).get(field) for row in subset], len(subset)) for field in PAIR_METRICS if field not in USAGE_FIELDS
                      and field not in ("agent_cli", "service_setup_excluded_from_agent", "grading", "trial_wall")},
            "original_test_config_edits": sum(bool(row.get("test_integrity", {}).get("changed_or_deleted_original_tests_and_configs")) for row in subset),
            "added_configuration_trials": sum(bool(row.get("test_integrity", {}).get("added_configuration_removed_for_grading")) for row in subset),
            "source_validation_failed_calls": sum(row.get("http", {}).get("source_validation_failed_calls", 0) for row in subset),
            "flagged_trials": sum(bool(row["flags"]) for row in subset)}
    by_key = {key(row): row for row in rows}
    pairs = []
    for task in dict.fromkeys(row["task_id"] for row in schedule):
        for repetition in (1, 2):
            a, b = (by_key[(task, repetition, condition)] for condition in CONDITIONS)
            provenance = {field: (a.get("pair_provenance", {}).get(field) == b.get("pair_provenance", {}).get(field)
                                  if a.get("pair_provenance", {}).get(field) and b.get("pair_provenance", {}).get(field) else None)
                          for field in ("base_tree_sha256", "prompt_sha256")}
            pairs.append({"task_id": task, "repetition": repetition,
                "first_condition": next(row["condition"] for row in schedule if row["task_id"] == task and row["repetition"] == repetition),
                "baseline_status": a["status"], "mcp_status": b["status"], "provenance_equal": provenance,
                "both_completed_correct": bool(a["completed_correct"] and b["completed_correct"]),
                "correctness_qualified": bool(a["finalized"] and b["finalized"] and a["completed_correct"] and b["completed_correct"]
                                               and all(value is True for value in provenance.values())),
                "metrics": {field: comparison(metric(a, field), metric(b, field)) for field in PAIR_METRICS}})
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": "New repeated exploratory multifile study only; older studies and transport diagnostics excluded",
        "report_generator_sha256": digest(__file__), "frozen_config_sha256": digest(config_path),
        "frozen_tasks_sha256": digest(tasks_path) if tasks_path.is_file() else None,
        "frozen_tasks_hash_matches_config": digest(tasks_path) == config.get("manifest_sha256") if tasks_path.is_file() and config.get("manifest_sha256") else None,
        "aggregate_results_sha256": digest(aggregate_path) if aggregate_path.is_file() else None,
        "tokenizer": {"name": "cl100k_base" if encoder else None,
                      "package_version": importlib.metadata.version("tiktoken") if encoder else None,
                      "network_downloads": False, "scope": "Published path-redacted tool-output strings"},
        "model": config.get("model"), "model_reasoning_effort": config.get("common_config", {}).get("model_reasoning_effort"),
        "configuration": {name: config.get(name) for name in ("common_runtime_sha256", "runtime_git_revision", "embedding_model", "mcp", "host")},
        "gate_inventory": {"distinct_selected_methods": sum(task.get("seed_validation", {}).get("reference", {}).get("tests_run", 0) for task in tasks.values()) if tasks else None,
            "expected_selected_invocations_per_condition": 2 * sum(task.get("seed_validation", {}).get("reference", {}).get("tests_run", 0) for task in tasks.values()) if tasks else None,
            "expected_upstream_counts_per_repetition_per_condition": {field: sum(command.get("expected_summary", {}).get(field, 0) for task in tasks.values()
                for command in task.get("compatibility_commands", [])) for field in COUNT_FIELDS} if tasks else None},
        "qualitative_review_inventory": {"artifact_sha256": digest(review_path) if review_path.is_file() else None,
            "status": review_record.get("status", "unavailable"),
            "reviewed_scheduled_attempts": sum(row.get("qualitative_patch_review") is not None for row in rows),
            "unreviewed_scheduled_attempts": sum(row.get("qualitative_patch_review") is None for row in rows),
            "classification": review_record.get("classification"), "method": review_record.get("method"),
            "summary": review_record.get("summary"), "unmatched_run_ids": sorted(set(reviews) - {row.get("run_id") for row in rows})},
        "scheduled_attempts": 16, "finalized_attempts": len(records), "complete_inventory": len(records) == 16,
        "conditions": conditions, "runs": rows, "pairs": pairs,
        "all_attempt_pair_comparisons": aggregate_pairs(pairs),
        "correctness_qualified_pair_comparisons": aggregate_pairs([pair for pair in pairs if pair["correctness_qualified"]]),
        "definitions": {"gross_tokens": "Reported input + output; cached/cache-write input and reasoning output are subsets, never added to gross.",
            "uncached_input": "Input minus cached input only where both are reported; cache writes are separately reported and not subtracted.",
            "missing_usage": "No completed-turn receipt means unavailable usage, including potentially charged work before a timeout; it is never treated as zero.",
            "timeout_time": "A timed-out CLI duration measures observed resource time to cutoff, not a successful completion time; its completion time is censored at that limit.",
            "output_proxies": "UTF-8 bytes and optional cached cl100k counts of published path-redacted completed tool output strings; not provider input, billed tokens, or exact model-visible history.",
            "structured_fields": "CLI normalization can add null structured fields; structured_payload_present requires a non-null value. A null field is not a duplicate payload or billing evidence.",
            "rereads": "Exact full delivered excerpt substring seen in later command stdout; not complete evidence or phase attribution.",
            "source_overlap": "Fully contained same-path source ranges with exact text at that location, and exact source excerpts seen in later same-path MCP results. Repetition can be justified by edits or new queries; no waste, paid-saving, or causal claim.",
            "observer_timing": "CLI events can be batched/queued; reception spans do not measure exact tool, queue, provider, or thinking time.",
            "http_timing": "Backend call is timed independently; source snapshot observation overhead is measured separately and included in agent workflow time.",
            "pair_change": "100*(MCP/baseline-1); negative is lower. All pairs and correctness-qualified pairs have separate denominators.",
            "source_checks": "Recorded HTTP snapshot checks plus recomputed delivered UTF-8 hashes/physical-line lengths; raw event linkage is available only with ignored evaluator logs.",
            "redaction": "Output proxies use published strings. Path redaction can change raw source/text hashes; recorded snapshot checks and optional raw payload linkage retain that distinction. Repeated identical payloads can yield explicitly ambiguous links.",
            "scope_clues": "Query mentions of tests/pytest/fixtures are marked when the frozen source root excludes the top-level tests tree; a heuristic, not proof of intended scope or causal attribution.",
            "qualified_pairs": "Both trials finalized and completed independently correct, with equal fixture and user-prompt hashes; this outcome-selected subset can be biased.",
            "limits": "Four authored feature tasks in existing development repositories, two repetitions, selected external/original tests, unblinded conditional tool guidance, filesystem/provider caches, instrumentation overhead; exploratory package comparison, no isolated component effect or broad savings claim."}}


def fmt(value, decimals=1):
    return "unavailable" if value is None else f"{value:,.{decimals}f}"


def render_markdown(report):
    rows = ["# Repeated multifile coding workflows", "",
        f"{report['finalized_attempts']}/16 scheduled attempts are finalized; all eight task/repetition pairs remain listed. "
        "This report excludes the earlier pilots and the separate transport diagnostic.", "",
        "Completed-correct means normal agent completion plus the frozen external behavior checks and restored original upstream integration gates; it does not use the agent's test-pass claim.", "",
        f"Both conditions use `{report.get('model')}` with `{report.get('model_reasoning_effort')}` reasoning. "
        "Differences describe the improved search workflow package under the common frozen runtime.", "",
        "## All scheduled attempts", "",
        "| Condition | Finalized | Completed correct | Reported gross tokens (observed/8) | Full eight-attempt gross total | Agent median s (observed/8) | Service setup median s |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    inventory = report.get("gate_inventory", {})
    if inventory.get("distinct_selected_methods") is not None:
        gate = inventory["expected_upstream_counts_per_repetition_per_condition"]
        rows[8:8] = [f"The planned gate contains {inventory['distinct_selected_methods']} distinct behavioral methods "
            f"({inventory['expected_selected_invocations_per_condition']} invocations per condition across two repetitions), plus upstream compatibility commands expecting "
            f"{gate['passed']} passed and {gate['skipped']} skipped cases per repetition and condition. Actual outcomes remain reported separately.", ""]
    for condition in CONDITIONS:
        data = report["conditions"][condition]
        tokens = data["usage"]["total_tokens"]
        agent = data["timing_s"].get("agent_cli", {})
        setup = data["timing_s"].get("service_setup_excluded_from_agent", {})
        rows.append(f"| {condition} | {data['finalized']}/8 | {data['completed_correct']}/8 | {fmt(tokens['measured_sum'],0)} ({tokens['observed']}/8) | "
                    f"{fmt(tokens['complete_total'],0)} | {fmt(agent.get('median'))} ({agent.get('observed',0)}/8) | {fmt(setup.get('median'))} |")
    rows += ["", "Observed subtotals exclude unavailable receipts. A timeout can consume unreported provider tokens, so a missing receipt is never zero cost. "
        "Setup is outside agent time; evaluator work and teardown are reported separately in analysis.json.", "",
        "| Condition | Input | Cached input subset | Uncached input | Cache-write input subset | Output | Reasoning output subset |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for condition in CONDITIONS:
        usage = report["conditions"][condition]["usage"]
        cells = [f"{fmt(usage[name]['measured_sum'],0)} ({usage[name]['observed']}/8)" for name in USAGE_FIELDS if name != "total_tokens"]
        cells = [cells[i] for i in (0,1,5,2,3,4)]
        rows.append("| " + condition + " | " + " | ".join(cells) + " |")
    rows += ["", "Cached input and reasoning are subsets of input/output; cache writes are separately shown and never added to gross. "
        "Uncached input is input minus cached input when both are known; it is not a billing estimate.", "",
        "| Condition | Completed commands | Completed search_code | Other MCP calls | Output UTF-8 bytes | Output cl100k proxy | Same-call source containment | Excerpts repeated in later MCP | Excerpts seen in later stdout |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for condition in CONDITIONS:
        data = report["conditions"][condition]
        tools = data["tools"]
        observed = data["record_present"]
        names = ("completed_command_items", "completed_search_code_items", "total_output_utf8_bytes", "total_output_cl100k_token_proxy",
                 "same_call_source_containment_pairs", "source_excerpts_repeated_in_later_mcp", "exact_full_excerpt_rereads")
        cells = [fmt(tools[name]["measured_sum"], 0) + f" ({tools[name]['observed']}/8)" for name in names]
        other = sum(len(row.get("tools", {}).get("unexpected_mcp_items", [])) for row in report["runs"] if row["condition"] == condition)
        cells.insert(2, f"{other} ({observed}/8)")
        rows.append("| " + condition + " | " + " | ".join(cells) + " |")
    rows += ["", "Output and overlap counters are descriptive proxies, not provider billing or evidence of wasted work.", "",
        "## All eight pairs", "",
        "Percent changes are MCP versus baseline; negative means lower. Correctness-qualified pairs require both normal completion and independent correctness, plus matching fixture/prompt hashes.", "",
        "| Task | Rep | First | Baseline outcome | MCP outcome | Qualified | Gross token change | Agent time change | Output token proxy change |",
        "|---|---:|---|---|---|---|---:|---:|---:|"]
    for pair in report["pairs"]:
        changes = [fmt(pair["metrics"][name]["change_pct"]) + "%" if pair["metrics"][name]["change_pct"] is not None else "unavailable"
                   for name in ("total_tokens", "agent_cli", "total_output_cl100k_token_proxy")]
        rows.append(f"| {pair['task_id']} | {pair['repetition']} | {pair['first_condition']} | {pair['baseline_status']} | {pair['mcp_status']} | "
                    f"{'yes' if pair['correctness_qualified'] else 'no'} | " + " | ".join(changes) + " |")
    rows += ["", "| Comparison family | Declared pairs | Gross paired sums change (observed pairs) | Median pair gross change | Agent paired sums change (observed pairs) |",
        "|---|---:|---:|---:|---:|"]
    for label, key in (("All attempts", "all_attempt_pair_comparisons"), ("Both completed correct", "correctness_qualified_pair_comparisons")):
        group = report[key]
        token, time = (group["metrics"][name] for name in ("total_tokens", "agent_cli"))
        rows.append(f"| {label} | {group['declared_pairs']} | {fmt(token['measured_sums_change_pct'])}% ({token['observed_pairs']}) | "
                    f"{fmt(token['median_pair_change_pct'])}% | {fmt(time['measured_sums_change_pct'])}% ({time['observed_pairs']}) |")
    rows += ["", "The correctness-qualified family selects on an outcome and can be biased. Missing pairs and nonzero failures stay visible in the all-attempt inventory.", "",
        "## Attempt outcomes and integrity", "",
        "| Attempt | Outcome | Gross tokens | Agent s | Commands / MCP completed | HTTP receipts | Original test/config edits | Integrity/data flags |",
        "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in report["runs"]:
        tools, integrity = row.get("tools", {}), row.get("test_integrity", {})
        rows.append(f"| {row['task_id']} r{row['repetition']} {row['condition']} | {row['status']} | {fmt((row.get('usage') or {}).get('total_tokens'),0)} | "
            f"{fmt(row['timing_s'].get('agent_cli'))} | {tools.get('completed_command_items','—')} / {tools.get('completed_mcp_items','—')} | "
            f"{row.get('http',{}).get('http_call_count','—')} | {len(integrity.get('changed_or_deleted_original_tests_and_configs',[]))} | {len(row['flags'])} |")
    flagged = [row for row in report["runs"] if row["flags"] or row.get("infrastructure_error") or row.get("grading_error")]
    if flagged:
        rows += ["", "Recorded problems:", ""]
        for row in flagged:
            messages = row["flags"] + [row[name] for name in ("infrastructure_error", "grading_error") if row.get(name)]
            rows.append(f"- {row['task_id']} r{row['repetition']} {row['condition']}: " + "; ".join(messages))
    scope_clues = [(row, call) for row in report["runs"] for call in row.get("tools", {}).get("mcp_calls", []) if call.get("query_scope_clue")]
    if scope_clues:
        rows += ["", "Observed scope clues (heuristic):", ""]
        for row, call in scope_clues:
            rows.append(f"- {row['task_id']} r{row['repetition']} {row['condition']}, {call['item_id']}: query mentions tests/pytest/fixtures; "
                        f"the index covers `{call['configured_index_source_root']}`. This is not proof of intent, retrieval failure, or a cause of token differences.")
    reviews = [row for row in report["runs"] if row.get("qualitative_patch_review") is not None]
    if reviews:
        inventory = report["qualitative_review_inventory"]
        rows += ["", f"Separate qualitative patch reviews: {inventory['reviewed_scheduled_attempts']}/16 scheduled attempts reviewed; "
                 f"status `{inventory['status']}`, {inventory['unreviewed_scheduled_attempts']} unreviewed.", ""]
        for row in reviews:
            summary = row["qualitative_patch_review"].get("summary", "Review is available in the separate artifact.")
            rows.append(f"- {row['task_id']} r{row['repetition']} {row['condition']}: {summary} [Review artifact](patch-review.json).")
        rows += ["", "These source reviews supplement the frozen gates and are not substituted for their outcomes or used to change the completed-correct denominator."]
    rows += ["", "Ordinary selected-check or compatibility failures are outcome failures. Integrity/data flags identify inconsistent claims, count declarations, or artifact provenance; a failed patch is not by itself a data error. "
        "Original upstream tests/configuration were restored for independent compatibility checks. "
        "Edits are review flags, not automatic proof of weakening; newly added regression tests remain in candidate patches. "
        "The JSON includes exact selected-check outcomes, frozen versus observed upstream counts, receipt/source validation, hash checks, call settings, changed files, and unavailable measurements.", "",
        "## Measurement limits", "",
        "A timeout's CLI duration measures time spent up to the cutoff. Successful completion time is unknown beyond that limit; it is not a completed task in exactly 900 seconds. Timeout usage without a provider receipt stays unavailable.", "",
        "Published stdout and MCP text produce UTF-8 byte and cl100k token proxies. They measure received strings, not provider billing or exact model-visible history. "
        "The history cap can affect what reaches the model; no token-saving claim follows from output bytes alone.", "",
        "Source overlap and repeated context are descriptive diagnostics: same-call containment and exact excerpts in later same-path results/stdout. "
        "Edits, different questions, or verification can justify repetition, so these are not labeled waste or paid savings.", "",
        "CLI start/completion events can arrive in a batch. Their observer reception spans do not measure exact tool execution, queue delay, provider phases, or thinking time. "
        "HTTP backend calls have independent timers; snapshot instrumentation is separately measured and contributes to observed workflow time. "
        "Provider usage per underlying model response is unavailable, and no time subtraction is used to attribute model thinking.", "",
        "Four authored features in existing development repositories and two repetitions give a small exploratory sample. "
        "Selected behavioral/original integration tests do not prove all library behavior. Conditions are unblinded and use conditional tool guidance; provider/filesystem caches and observation overhead remain. "
        "This compares a workflow package and does not isolate guidance, representation, runtime, history caps, or environment effects, establish general savings, or establish the project's 30% target.", "",
        "Reproduction: `scripts/report_multifile_workflows.py --study <study-directory>` reads saved artifacts only. "
        "Ignored raw logs optionally validate exact MCP-to-HTTP linkage; they are not published by the report generator.", ""]
    return "\n".join(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Defaults to the study directory; writes analysis.json and report.md")
    parser.add_argument("--raw-root", type=Path, help="Optional ignored evaluator work_root for exact raw payload linkage")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--no-token-proxies", action="store_true", help="Keep byte measurements and mark tokenizer proxies unavailable")
    args = parser.parse_args(argv)
    encoder = None if args.no_token_proxies else offline_encoder()
    report = build_report(args.study, encoder=encoder, raw_root=args.raw_root, allow_partial=args.allow_partial)
    output = args.output or args.study
    output.mkdir(parents=True, exist_ok=True)
    (output / "analysis.json").write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
    (output / "report.md").write_text(render_markdown(report))
    print(f"Reported {report['finalized_attempts']}/16 finalized attempts; no saved commands executed")


if __name__ == "__main__":
    main()
