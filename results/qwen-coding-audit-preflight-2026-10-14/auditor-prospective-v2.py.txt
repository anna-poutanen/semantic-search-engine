#!/usr/bin/env python3
"""Independent CPU reconciliation of terminal strict Qwen coding trials.

No coding agent, server, retrieval, embedding or reranker runs. Only terminal
records are inspected. Sources and upstream fixtures are read; no checks rerun.
Raw reasoning stays private. Partial usage is never estimated or called billing.
"""
from __future__ import annotations

import argparse
import ast
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
from itertools import combinations
import io
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import tarfile
import tokenize
import tomllib

from audit_production_qwen import decode, physical_lines, require, typed_equal, validate_result

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ("baseline", "mcp_fast", "mcp_qwen")
TASK_IDS = ("requests_response_limit", "click_atomic_transactions", "starlette_body_limits")
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens")
FIELDS = ("total_tokens", "uncached_input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "agent_elapsed_s", "service_setup_s", "full_trial_elapsed_s")
COUNTS = ("requests", "attempted_pairs", "completed_pairs", "attempted_forwards", "completed_forwards")
PYTEST_FIELDS = ("passed", "failed", "skipped", "errors", "xfailed", "xpassed", "deselected")
RANKS_SHA = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
RANKS_PATH = ROOT / ".cache/tokenizers/9b5ad71b2ce5302211f9c61530b329a4922fc6a4"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    return digest(Path(path).read_bytes())


def read(path):
    return decode(Path(path).read_text())


def jsonl(path):
    if not path.is_file():
        return []
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return [decode(line) for line in raw.decode().splitlines()]


def safe(value, substitutions=()):
    if isinstance(value, str):
        for source, replacement in substitutions:
            value = value.replace(source, replacement)
        value = value.replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "<REDACTED_API_KEY>", value)
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{24,}=*", "Bearer <REDACTED_TOKEN>", value)
    if isinstance(value, dict):
        return {key: safe(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [safe(item, substitutions) for item in value]
    return value


def git(repo, *arguments):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(repo), *arguments])


def schedule():
    result = []
    for repetition in (1, 2):
        for index, task in enumerate(TASK_IDS):
            order = CONDITIONS[index:] + CONDITIONS[:index]
            if repetition == 2:
                order = tuple(reversed(order))
            result += [{"task_id": task, "repetition": repetition, "condition": condition} for condition in order]
    return result


def raw_events(path):
    events, invalid = [], []
    if not path.is_file():
        return events, invalid
    for line in path.read_bytes().splitlines(keepends=True):
        try:
            event = decode(line.decode())
            require(isinstance(event, dict), "CLI event must be an object")
            # json.loads accepts exponent overflow without parse_constant.
            def finite(value):
                if type(value) is float:
                    require(math.isfinite(value), "Nonfinite CLI float")
                elif isinstance(value, dict):
                    for item in value.values():
                        finite(item)
                elif isinstance(value, list):
                    for item in value:
                        finite(item)
            finite(event)
            events.append(event)
        except (ValueError, UnicodeError):
            invalid.append(digest(line.decode(errors="replace").encode()))
    return events, invalid


def provider_usage(events):
    turns = [event.get("usage") for event in events if event.get("type") == "turn.completed"]
    valid = [turn for turn in turns if isinstance(turn, dict)]
    errors = []
    for index, turn in enumerate(valid):
        for key in USAGE_FIELDS:
            value = turn.get(key)
            if value is not None and (type(value) is not int or value < 0):
                errors.append(f"turn {index}: invalid {key}")
        for subset, total in (("cached_input_tokens", "input_tokens"), ("cache_write_input_tokens", "input_tokens"),
                              ("reasoning_output_tokens", "output_tokens")):
            a, b = turn.get(subset), turn.get(total)
            if type(a) is int and type(b) is int and a > b:
                errors.append(f"turn {index}: {subset} exceeds {total}")
    usage = None
    if valid and not errors:
        usage = {key: sum(turn[key] for turn in valid) if len(valid) == len(turns)
                 and all(type(turn.get(key)) is int for turn in valid) else None for key in USAGE_FIELDS}
        a, b, c = usage["input_tokens"], usage["output_tokens"], usage["cached_input_tokens"]
        usage.update(total_tokens=a + b if a is not None and b is not None else None,
                     uncached_input_tokens=a - c if a is not None and c is not None else None)
    return usage, len(valid), len(turns), errors


def completed(row):
    return bool(row.get("completed") and row.get("agent_exit_code") == 0 and not row.get("failed")
                and not row.get("timed_out") and not row.get("infrastructure_error"))


def measured(row, field):
    if field.endswith("_s"):
        value = row.get(field) if row.get("timing_qualified") is True else None
    else:
        value = row.get("usage", {}).get(field) if isinstance(row.get("usage"), dict) else None
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def distributions(records):
    output = {}
    for condition in CONDITIONS:
        members = [row for row in records if row["condition"] == condition]
        output[condition] = {"recorded": len(members), "normally_completed": sum(completed(row) for row in members),
            "correct": sum(row.get("correct") is True for row in members),
            "completed_correct": sum(completed(row) and row.get("correct") is True for row in members),
            "timeouts": sum(row.get("timed_out") is True for row in members),
            "infrastructure_errors": sum(bool(row.get("infrastructure_error")) for row in members),
            "measurements": {}}
        for field in FIELDS:
            values = [value for row in members if (value := measured(row, field)) is not None]
            output[condition]["measurements"][field] = {"observations": len(values),
                "sum": sum(values) if values else None, "median": statistics.median(values) if values else None}
    return output


def paired_measurements(records):
    grouped = {}
    for row in records:
        grouped.setdefault((row["task_id"], row["repetition"]), {})[row["condition"]] = row
    output = []
    for left, right in combinations(CONDITIONS, 2):
        for subset in ("all_observed_pairs", "normally_completed_pairs", "completed_correct_pairs"):
            eligible, exclusions = [], []
            for identity, rows in sorted(grouped.items()):
                if left not in rows or right not in rows:
                    continue
                a, b = rows[left], rows[right]
                missing = [key for key in ("base_tree_sha256", "prompt_sha256") if not a.get(key) or not b.get(key)]
                if missing:
                    exclusions.append({"member": list(identity), "missing": missing})
                    continue
                require(all(a[key] == b[key] for key in ("base_tree_sha256", "prompt_sha256")), "Paired source/prompt drift")
                if subset != "all_observed_pairs" and not (completed(a) and completed(b)):
                    continue
                if subset == "completed_correct_pairs" and not (a["correct"] is True and b["correct"] is True):
                    continue
                eligible.append((identity, a, b))
            metrics = {}
            for field in FIELDS:
                values = [(identity, measured(a, field), measured(b, field)) for identity, a, b in eligible]
                values = [(identity, a, b) for identity, a, b in values if a is not None and b is not None]
                x = sum(a for _, a, _ in values) if values else None
                y = sum(b for _, _, b in values) if values else None
                ratios = [100 * (b / a - 1) for _, a, b in values if a > 0]
                metrics[field] = {"pairs": len(values), "members": [list(identity) for identity, _, _ in values],
                    "left_sum": x, "right_sum": y, "aggregate_change_pct": 100 * (y / x - 1) if x else None,
                    "median_pair_change_pct": statistics.median(ratios) if ratios else None, "ratio_pairs": len(ratios)}
            output.append({"left": left, "right": right, "subset": subset, "eligible_pairs": len(eligible),
                           "binding_exclusions": exclusions, "measurements": metrics})
    return output


def projection(detail):
    if not isinstance(detail, dict):
        return None
    if detail.get("version") != 1 or type(detail.get("passed")) is not bool or any(
            type(detail.get(key)) is not int or detail[key] < 0 for key in ("model_calls", "tests_run", "failures", "errors", "skipped")):
        return None
    result = {key: detail.get(key) for key in ("version", "task_id", "strict_manifest_sha256", "model_calls", "tests_run",
        "failures", "errors", "skipped", "passed", "original_grade_unchanged")}
    result["components"] = []
    for component in detail.get("components", []):
        item = {key: component.get(key) for key in ("script", "script_sha256", "exit_code", "timed_out", "passed")}
        payload = component.get("result")
        if isinstance(payload, dict) and isinstance(payload.get("cases"), dict):
            item.update(supplemental_version=payload.get("version"), probe_sha256=payload.get("probe_sha256"),
                        cases={key: value.get("passed") if isinstance(value, dict) else None for key, value in payload["cases"].items()})
        item["receipt_error"] = component.get("receipt_error")
        result["components"].append(item)
    return result


def strict_grade(grade, task):
    if grade.get("detail") is not None:
        detail = decode(grade["stdout"])
        require(typed_equal(detail, grade["detail"]), "Strict grade stdout/detail differ")
        require(typed_equal(projection(detail), grade["projection"]), "Strict grade projection differs")
        require(detail["task_id"] == task["id"] and detail["model_calls"] == 0, "Strict task/CPU checker identity differs")
        strict_manifest = read(ROOT / "benchmarks/strict_multifile_checks/manifest.json")
        specifications = strict_manifest["tasks"][task["id"]]["components"]
        require(len(detail["components"]) == len(specifications), "Strict component inventory differs")
        for component, specification in zip(detail["components"], specifications, strict=True):
            require(component["script"] == specification["script"] and sha(ROOT / component["script"]) == component["script_sha256"],
                    "Strict component source/order differs")
            payload = component.get("result")
            if payload is not None:
                require(typed_equal(decode(component["stdout"]), payload), "Actual component stdout/result differ")
                if specification["kind"] == "original":
                    require(all(type(payload.get(key)) is int and payload[key] >= 0 for key in
                                ("tests_run", "failures", "errors", "skipped")) and type(payload.get("passed")) is bool,
                            "Actual original checker typed counters differ")
                    passed = payload["passed"] and payload["tests_run"] > 0 and not any(payload[key] for key in ("failures", "errors", "skipped"))
                else:
                    require(payload["version"] == 3 and payload["probe_sha256"] == component["script_sha256"]
                            and set(payload["cases"]) == set(specification["case_ids"])
                            and all(type(case.get("passed")) is bool for case in payload["cases"].values()),
                            "Actual accepted supplemental oracle identity/grades differ")
                    passed = all(case["passed"] for case in payload["cases"].values())
                passed = bool(passed and component["exit_code"] == 0 and not component["timed_out"])
                require(component["passed"] is passed, "Strict component outcome differs")
            else:
                require(component["passed"] is False, "Missing component result counted as passed")
        original = detail["components"][0].get("result") or {}
        cases = [case for component in detail["components"][1:] if component.get("result") is not None
                 for case in component["result"]["cases"].values()]
        expected_detail = {"tests_run": original.get("tests_run", 0) + len(cases),
                           "failures": original.get("failures", 0) + sum(not case["passed"] for case in cases),
                           "errors": original.get("errors", 0) + sum("receipt_error" in component for component in detail["components"]),
                           "skipped": original.get("skipped", 0), "original_grade_unchanged": original,
                           "passed": all(component["passed"] for component in detail["components"])}
        require(all(typed_equal(detail[key], value) for key, value in expected_detail.items()), "Strict typed aggregate/original grade differs")
    expected = grade.get("exit_code") == 0 and typed_equal(grade.get("projection"), task["seed_validation"]["reference"])
    require(grade["passed"] is expected, "Strict contract outcome differs")
    return expected


def clock_receipt(receipt, baseline, since=None):
    require(typed_equal(receipt["baseline"], baseline), "Clock baseline changed")
    sample = receipt["sample"]
    for value in (baseline, sample, since):
        if value is None:
            continue
        require(type(value["wall_time_ns"]) is int and type(value["perf_counter_ns"]) is int, "Clock samples must be integer ns")
        seconds, ns = divmod(value["wall_time_ns"], 1_000_000_000)
        utc = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=ns // 1000).isoformat()
        require(value["utc"] == utc, "Clock sample UTC differs")
    def deltas(start):
        wall = sample["wall_time_ns"] - start["wall_time_ns"]
        perf = sample["perf_counter_ns"] - start["perf_counter_ns"]
        return wall, perf, wall - perf
    def expected_delta(start):
        w, p, d = deltas(start)
        return {"wall_elapsed_ns": w, "perf_elapsed_ns": p, "drift_ns": d,
                "absolute_drift_ns": abs(d), "max_drift_ns": 2_000_000_000,
                "consistent": p >= 0 and abs(d) <= 2_000_000_000}
    expected = expected_delta(baseline)
    require(typed_equal(receipt["cumulative"], expected), "Cumulative integer clock deltas differ")
    valid = expected["consistent"]
    if since is None:
        require(receipt["interval"] is None, "Unexpected within-interval clock baseline")
    else:
        within = expected_delta(since)
        require(typed_equal(receipt["interval"], within), "Within-interval integer clock deltas differ")
        valid = valid and within["consistent"]
    require(receipt["consistent"] is valid and receipt["max_drift_ns"] == 2_000_000_000, "Clock gate verdict differs")
    return valid


def offline_encoding():
    require(sha(RANKS_PATH) == RANKS_SHA, "Pinned offline cl100k tokenizer differs")
    import tiktoken
    ranks = {base64.b64decode(token): int(rank) for token, rank in (line.split() for line in RANKS_PATH.read_bytes().splitlines())}
    return tiktoken.Encoding(name="cl100k_base", mergeable_ranks=ranks,
        pat_str=r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s",
        special_tokens={"<|endoftext|>": 100257, "<|fim_prefix|>": 100258, "<|fim_middle|>": 100259,
                        "<|fim_suffix|>": 100260, "<|endofprompt|>": 100276})


def compact(response):
    fields = ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")
    result_rows = []
    for item in response["results"]:
        row = {key: item[key] for key in fields}
        row.update(origin=item.get("origin", "retrieval"), related_to=item.get("related_to"))
        row.update({key: item[key] for key in ("source_origin", "buffer_version", "buffer_hash") if key in item})
        result_rows.append(row)
    envelope = {"query": response["query"], "match_status": response.get("match_status", "candidates"),
        "results": result_rows, "budget_exhausted": response.get("budget_exhausted", False),
        "token_usage": response.get("token_usage", {"encoding": "cl100k_base", "tokens": None, "budget": None, "truncated": False})}
    if "scope" in response:
        envelope["scope"] = response["scope"]
    return json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def source_text(path):
    raw = path.read_bytes()
    codec, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(codec)


def source_window(identifier, after):
    path, _, bounds = identifier.rsplit(":", 2)
    start, end = (int(value) for value in bounds.split("-"))
    relative = Path(path)
    require(not relative.is_absolute() and ".." not in relative.parts, "Unsafe snippet source path")
    lines = physical_lines(source_text(after / path))
    require(1 <= start <= end <= len(lines), "Scored window source bounds differ")
    return "".join(lines[start - 1:end])


def source_receipt(receipt, raw, source_root, encoder):
    snapshots = {}
    for phase in ("before", "after"):
        directory = raw / "snapshots" / f'{receipt["number"]:04d}' / phase
        if phase not in receipt.get("snapshots", {}):
            require(receipt.get("success") is not True and receipt.get("snapshot_error"), "Successful search missing source snapshot")
            continue
        metadata = read(directory / "snapshot.json")
        require(typed_equal(metadata, receipt["snapshots"][phase]), "Actual source snapshot receipt differs")
        require(all(sha(directory / name) == digest for name, digest in metadata["files"].items()), "Actual snapshot bytes differ")
        require(digest(json.dumps(metadata["files"], sort_keys=True, separators=(",", ":")).encode()) == metadata["manifest_sha256"], "Snapshot membership digest differs")
        require(all(Path(name).is_relative_to(Path(source_root)) and ".." not in Path(name).parts for name in metadata["files"]), "Source scope leakage")
        snapshots[phase] = directory
    if receipt.get("success") is not True:
        return {"number": receipt["number"], "success": False, "error": receipt.get("error"), "source_rows": 0}
    response = receipt["response"]
    require(response["source_root"] == source_root and response["scope"]["source_root"] == source_root, "Actual returned index scope differs")
    require(response["query"] == receipt["query"], "Actual search query differs")
    count = 0
    for row in response["results"]:
        expected = source_window(f'{row["path"]}:{row["symbol"]}:{row["start_line"]}-{row["end_line"]}', snapshots["after"])
        require(expected == row["source"] and digest(expected.encode()) == row["source_hash"], "Returned source/current saved snapshot differs")
        require(row["end_line"] == row["start_line"] + len(physical_lines(expected)) - 1, "Returned physical source bounds differ")
        count += len(row["source"])
    require(count == response["total_source_chars"] and count <= response["source_budget"], "Actual source character budget differs")
    text = compact(response)
    require(digest(text.encode()) == receipt["compact_text_sha256"], "Actual compact payload hash differs")
    usage = response.get("token_usage", {})
    if usage.get("budget") is not None:
        tokens = len(encoder.encode(text, disallowed_special=()))
        require(usage["encoding"] == "cl100k_base" and type(usage["tokens"]) is int and usage["tokens"] == tokens
                and tokens <= usage["budget"], "Actual compact tokenizer/token cap differs")
    return {"number": receipt["number"], "success": True, "source_rows": len(response["results"]),
            "source_changed_during_call": receipt["snapshots"]["before"] != receipt["snapshots"]["after"],
            "tokens": usage.get("tokens"), "source_chars": count}


def cli_settings(command, config, workspace, raw, condition, source_root):
    require(command[0] == config["cli_binary"] and "--ignore-user-config" in command
            and "--ignore-rules" in command and "--strict-config" in command and "--ephemeral" in command
            and "--no-daemon" in command and "--json" in command, "Actual isolated CLI command differs")
    require(command[command.index("-m") + 1] == config["model"] and command[command.index("-C") + 1] == str(workspace)
            and command[command.index("-o") + 1] == str(raw / "final.txt"), "Actual model/workspace/output command differs")
    settings = {}
    for index, token in enumerate(command):
        if token != "-c":
            continue
        name, text = command[index + 1].split("=", 1)
        require(name not in settings, "Duplicate CLI configuration override")
        settings[name] = tomllib.loads("value=" + text)["value"]
    for name, expected in config["common_config"].items():
        require(typed_equal(settings.get(name), expected), "Actual common CLI setting changed: " + name)
    shell = settings["shell_environment_policy.set"]
    require(shell["PYTHONPATH"] == str((workspace / source_root).parent)
            and shell["PYTHONDONTWRITEBYTECODE"] == "1"
            and shell["PATH"].split(os.pathsep)[0] == str(Path(config["python"]).parent), "Actual frozen shell interpreter/scope differs")
    mcp_keys = {key for key in settings if key.startswith("mcp_servers.")}
    if condition == "baseline":
        require(not mcp_keys, "Baseline unexpectedly has a search MCP")
        return
    require(settings["mcp_servers.semantic_search.command"] == config["python"]
            and settings["mcp_servers.semantic_search.env"] == {"PYTHONPATH": str(ROOT / "src")}
            and settings["mcp_servers.semantic_search.enabled_tools"] == ["search_code"]
            and settings["mcp_servers.semantic_search.required"] is True, "Actual MCP isolated environment/tool scope differs")
    args = settings["mcp_servers.semantic_search.args"]
    profile = config["selected_profile"]
    expected = ["-m", "semantic_search.mcp_server", "--server", args[args.index("--server") + 1], "--agent-text-only",
                "--default-retrieval", profile["retrieval"], "--default-candidates", str(profile["shortlist"])]
    if condition == "mcp_qwen":
        expected += ["--default-rerank", "--default-rerank-provider", "qwen"]
    require(args == expected, "Actual condition default profile differs")
    require(re.fullmatch(r"http://127\.0\.0\.1:\d+", args[args.index("--server") + 1]) is not None, "Actual MCP server is not loopback")


def native_inputs(query, source, tokenizer, constants):
    def encode(text):
        return tokenizer.encode(text, add_special_tokens=False).ids
    prefix, suffix = encode(constants["PREFIX"]), encode(constants["SUFFIX"])
    header = f'<Instruct>: {constants["INSTRUCTION"]}\n<Query>: {query}\n<Document>: '
    lines = physical_lines(source)
    count = len(lines)
    delivered = source
    ids = prefix + encode(header + delivered) + suffix
    while len(ids) > constants["MAX_TOKENS"] and count:
        count -= 1
        delivered = "".join(lines[:count])
        ids = prefix + encode(header + delivered) + suffix
    require(delivered, "Actual scored literal source has no complete-line input")
    return {"input_ids_sha256": digest(json.dumps(ids, separators=(",", ":")).encode()),
            "total_tokens": len(ids), "query_sha256": digest(query.encode()), "source_sha256": digest(source.encode()),
            "delivered_sha256": digest(delivered.encode())}


def constants(path, names):
    tree = ast.parse(path.read_text())
    return {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in names}


def native_review(record, receipts, raw, public, config):
    if record["condition"] != "mcp_qwen":
        require(not record.get("qwen_evidence") and not record.get("qwen_start"), "Native Qwen appeared in other condition")
        return {"attempted": 0, "completed": 0, "warmup": 0, "mapped_successful": 0, "unmatched_completed": []}
    final = record.get("qwen_final_receipts", {})
    starts = [path for path in (raw / "qwen").glob("owned-*/ready.json")]
    if not starts:
        require(record.get("failed") is True and not record.get("completed_correct"), "Qwen condition missing actual startup witness")
        owned_directories = [path for path in (raw / "qwen").glob("owned-*") if path.is_dir()]
        require(len(owned_directories) <= 1, "Unexpected retries before native readiness")
        ledger = jsonl(owned_directories[0] / "forwards.jsonl") if owned_directories else []
        attempted = sum(row.get("event") == "started" and row.get("warmup") is False for row in ledger)
        ended = sum(row.get("event") == "completed" and row.get("warmup") is False for row in ledger)
        warmup_attempted = sum(row.get("event") == "started" and row.get("warmup") is True for row in ledger)
        warmup_completed = sum(row.get("event") == "completed" and row.get("warmup") is True for row in ledger)
        worker_stop = read(owned_directories[0] / "stop.json") if owned_directories and (owned_directories[0] / "stop.json").is_file() else {}
        if worker_stop:
            require(typed_equal(safe(worker_stop), final.get("worker_stop", {})), "Actual failed-start native stop witness differs")
        evidence = record.get("qwen_evidence", {})
        for name, item in evidence.get("native_artifacts", {}).items():
            require(owned_directories and sha(owned_directories[0] / name) == item["raw_sha256"]
                    and sha(public / item["published_file"]) == item["published_sha256"], "Failed-start native artifact drift")
        stop = final.get("stop", {})
        if stop.get("owned_group_stopped") is True:
            try:
                os.killpg(final["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise ValueError("Failed-start native group disappearance unproved")
        return {"startup_failed": True, "attempted": attempted, "completed": ended,
                "warmup": warmup_attempted, "warmup_completed": warmup_completed,
                "mapped_successful": 0, "unmatched_completed": [row["request_id"] for row in ledger if row.get("event") == "completed"],
                "native_events": len(ledger), "worker_stop_counts": worker_stop.get("counts"),
                "counts_complete": False, "count_scope": "Actual retained native event lower bounds before readiness; missing forwards remain unknown"}
    require(len(starts) == 1, "Unexpected Qwen worker retries")
    directory = starts[0].parent
    ready = read(directory / "ready.json")
    pid = ready["pid"]
    require(pid == ready["process_group"] == final["pid"] == final["owned_group"], "Actual Qwen worker ownership differs")
    for name, expected in final["worker_source_sha256"].items():
        require(sha(directory / "runtime/semantic_search" / name) == expected == sha(ROOT / "src/semantic_search" / name), "Actual isolated Qwen source copy differs")
    stop = final.get("stop", {})
    proven = stop.get("owned_group_stopped") is True
    if proven:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise ValueError("Actual Qwen owned group is still present")
    require(proven == record["lifecycle_qualified"] or record["lifecycle_qualified"] is False, "Unproved native cleanup was qualified")
    clean = proven and stop.get("exit_code") == 0 and not stop.get("forced_kill") and not stop.get("errors")
    clean = clean and final.get("auth_token_removed") is True and not final.get("private_cleanup_errors")
    worker_stop = read(directory / "stop.json") if (directory / "stop.json").exists() else {}
    require(typed_equal(safe(worker_stop), final.get("worker_stop", {})), "Actual native stop witness differs")
    clean = clean and worker_stop.get("clean_stop") is True and worker_stop.get("listener_closed") is True
    clean = clean and worker_stop.get("auth_token_removed") is True and worker_stop.get("auth_cleanup_error") is None
    require(clean or record.get("failed") is True, "Unclean native stop counted as a normal completion")
    require(not final.get("auth_token_removed") or not (directory / "token.secret").exists(), "Native auth-removal claim differs")
    provenance = ready["provenance"]
    runtime = provenance["runtime"]
    expected_runtime = config["reranker_expected_runtime"]
    require(runtime["packages"] == expected_runtime["runtime"]["packages"] and runtime["errors"] == []
            and runtime["qwen3_source_sha256"] == runtime["imported_qwen3_source_sha256"] == expected_runtime["runtime"]["qwen3_source_sha256"]
            and runtime["supporting_source_sha256"] == expected_runtime["supporting_sources"], "Native pinned Qwen runtime differs")
    model = provenance["model"]
    require(model["model_id"] == expected_runtime["model_id"] and model["revision"] == expected_runtime["revision"]
            and model["weight_identity_verified"] is True, "Actual native model identity differs")
    core_constants = constants(ROOT / "src/semantic_search/qwen_reranker_core.py", {"PREFIX", "SUFFIX", "INSTRUCTION", "MAX_TOKENS", "PINS"})
    for name, (size, expected) in core_constants["PINS"].items():
        require(model["files"][name] == {"size": size, "sha256": expected}, "Actual pinned checkpoint bytes receipt differs")
    from tokenizers import Tokenizer
    require(sha(Path(config["reranker_checkpoint"]) / "tokenizer.json") == core_constants["PINS"]["tokenizer.json"][1], "Offline Qwen tokenizer changed")
    tokenizer = Tokenizer.from_file(str(Path(config["reranker_checkpoint"]) / "tokenizer.json"))
    ledger = jsonl(directory / "forwards.jsonl")
    started = [row for row in ledger if row["event"] == "started"]
    ended = [row for row in ledger if row["event"] in ("completed", "failed")]
    require(len({row["request_id"] for row in started}) == len(started), "Duplicate actual Qwen request identity")
    require(sum(row.get("warmup") is True for row in started) == 1
            and started[0]["request_id"] == "generic-warmup", "Exactly one distinct initial generic native warmup required")
    require([row["forward_number"] for row in started] == list(range(1, len(started) + 1)), "Native B1 actual forward order differs")
    mapping = {}
    for receipt in receipts:
        if receipt.get("success") is not True:
            continue
        search_response = receipt["response"]
        scored = search_response.get("reranker_receipts", {})
        if search_response.get("rerank") is True and search_response.get("rerank_provider") == "qwen":
            expected_scored = min(receipt["settings"]["candidates"], search_response["retrieval_candidate_windows"])
            require(search_response["candidate_windows"] == expected_scored == len(scored), "Actual full50/prefix scoring count differs")
            require(search_response["retrieval_candidate_windows"] <= 50 and search_response["reranker_batch_size"] == 1,
                    "Actual native candidate pool/B1 bounds differ")
            ranked = sorted(scored, key=lambda identifier: (-scored[identifier]["result"]["logit_difference"],
                            list(scored).index(identifier), identifier))
            anchors = [row for row in search_response["results"] if row.get("origin", "retrieval") == "retrieval"]
            require([row["id"] for row in anchors] == [identifier for identifier in ranked if identifier in {row["id"] for row in anchors}],
                    "Returned original-prefix anchor ordering differs")
            for row in anchors:
                require(row["qwen_score"] == scored[row["id"]]["result"]["logit_difference"], "Returned anchor ranking scalar differs")
        for identifier, response in scored.items():
            request = response["request_id"]
            require(request not in mapping, "One native score reused across searches")
            after = raw / "snapshots" / f'{receipt["number"]:04d}' / "after"
            source = source_window(identifier, after)
            validate_result({"query": receipt["query"], "source": source}, response["result"])
            require(response["warmup"] is False and response["batch_size"] == 1, "Production score is not original B1")
            mapping[request] = (response, native_inputs(receipt["query"], source, tokenizer, core_constants))
    warmup = ready["warmup"]
    literal_warmup = constants(ROOT / "src/semantic_search/qwen_worker.py", {"WARMUP"})["WARMUP"]
    mapping["generic-warmup"] = (warmup, native_inputs(**literal_warmup, tokenizer=tokenizer, constants=core_constants))
    terminal = {row["request_id"]: row for row in ended}
    require(len(terminal) == len(ended), "Duplicate native terminal event")
    if clean:
        require(len(ledger) == len(started) + len(ended) and len(started) == len(ended)
                and all(ledger[index]["event"] == "started" and ledger[index + 1]["event"] in {"completed", "failed"}
                        and ledger[index]["request_id"] == ledger[index + 1]["request_id"] for index in range(0, len(ledger), 2)),
                "Native singleton event sequence differs")
    require(set(mapping) <= set(terminal), "Successful returned Qwen score has no native terminal event")
    for start in started:
        request = start["request_id"]
        end = terminal.get(request)
        if end is None:
            require(record.get("failed") is True, "Normal completion has unended native forward")
            continue
        require(start["warmup"] is (request == "generic-warmup") and start["batch_size"] == 1
                and all(typed_equal(start.get(key), end.get(key)) for key in ("request_id", "warmup", "batch_size",
                    "query_sha256", "source_sha256", "delivered_sha256", "total_tokens", "input_ids_sha256")), "Native paired input/event identity differs")
        if request in mapping:
            response, inputs = mapping[request]
            require(end["event"] == "completed" and all(start.get(key) == value for key, value in inputs.items()), "Native tokenizer/literal input differs")
            require(end["score"] == {key: response["result"][key] for key in ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")}, "Native response/scalars differ")
            require(end["forward_s"] == response["forward_s"], "Native forward duration differs")
    completed_rows = [row for row in ended if row["event"] == "completed"]
    counter = worker_stop.get("counts", {})
    if clean:
        require(all(type(counter.get(key)) is int for key in COUNTS), "Native final counter types differ")
        require(counter["attempted_pairs"] == counter["attempted_forwards"] == len(started)
                and counter["completed_pairs"] == counter["completed_forwards"] == len(completed_rows)
                and counter["requests"] >= len(started), "Native actual event/stop counts differ")
    artifacts = record.get("qwen_evidence", {}).get("native_artifacts", {})
    for name, item in artifacts.items():
        require(name in {"forwards.jsonl", "start.json", "stop.json", "ready.json"} and sha(directory / name) == item["raw_sha256"]
                and sha(public / item["published_file"]) == item["published_sha256"], "Native raw/public artifact digest differs")
    actual_attempted = sum(row["warmup"] is False for row in started)
    actual_completed = sum(row["warmup"] is False for row in completed_rows)
    evidence = record.get("qwen_evidence", {})
    require(evidence.get("observed_qwen_attempted_forwards") == actual_attempted
            and evidence.get("observed_qwen_completed_forwards") == actual_completed, "Native headline counts differ")
    unmatched = [row["request_id"] for row in completed_rows if row["request_id"] not in mapping]
    return {"attempted": actual_attempted, "completed": actual_completed, "warmup": len(started) - actual_attempted,
            "mapped_successful": len(mapping) - 1, "unmatched_completed": unmatched, "cleanup_clean": bool(clean)}


def pytest_counts(text):
    lines = [line for line in text.splitlines() if re.search(r"\b\d+ (?:passed|failed|skipped|xfailed|xpassed|deselected|errors?)\b", line)]
    if not lines:
        return None
    result = dict.fromkeys(PYTEST_FIELDS, 0)
    for count, key in re.findall(r"\b(\d+) (passed|failed|skipped|xfailed|xpassed|deselected|errors?)\b", lines[-1]):
        result["errors" if key in ("error", "errors") else key] += int(count)
    return result


def compatibility(compat, task, raw, workspace, base):
    require(len(compat["commands"]) == len(task["compatibility_commands"]), "Frozen original compatibility selection differs")
    passed = []
    for actual, expected in zip(compat["commands"], task["compatibility_commands"], strict=True):
        summary = pytest_counts(actual["stdout"]) if "stdout" in actual else None
        if summary is None:
            require(actual.get("error") == "upstream_check_timeout", "Missing actual compatibility summary")
        else:
            require(typed_equal(summary, actual["actual_summary"]) and typed_equal(actual["expected_summary"], expected["expected_summary"]), "Actual original-suite typed counters differ")
        success = actual["exit_code"] == 0 and typed_equal(summary, expected["expected_summary"])
        require(actual["passed"] is success, "Actual original-suite outcome differs")
        passed.append(success)
    require(compat["passed"] is all(passed), "Compatibility aggregate differs")
    restored = raw / "original-upstream-checkout"
    for name, digest in compat["original_test_and_config_hashes"].items():
        require(base[name] == digest == sha(restored / name), "Actual restored original test/config changed")
    for path in (workspace / task["source_root"]).rglob("*.py"):
        if "__pycache__" not in path.parts:
            require(sha(restored / path.relative_to(workspace)) == sha(path), "Graded restored source copy differs from final candidate")
    return all(passed)


def trial_clocks(record, baseline, launched):
    clock_valid = True
    required_clock_fields = ["clock_start", "clock_end"]
    if launched:
        required_clock_fields += ["clock_agent_start", "clock_agent_end"]
    missing_clock_fields = [key for key in required_clock_fields if not record.get(key)]
    if completed(record):
        require(not missing_clock_fields, "Normally completed attempt missing scheduled clock receipts")
    if missing_clock_fields:
        clock_valid = False
    labels = {"clock_start": "trial-start", "clock_agent_start": "agent-start", "clock_agent_end": "agent-end", "clock_end": "trial-end"}
    for key, label in labels.items():
        if record.get(key):
            require(record[key]["label"] == label, "Scheduled clock receipt label differs")
    ordered_clock_samples = [record[key]["sample"] for key in labels if record.get(key)]
    require([sample["perf_counter_ns"] for sample in ordered_clock_samples] == sorted(sample["perf_counter_ns"] for sample in ordered_clock_samples),
            "Scheduled phase clock order differs")
    start_receipt = record.get("clock_start")
    if start_receipt:
        clock_valid &= clock_receipt(start_receipt, baseline)
    agent_start = record.get("clock_agent_start")
    if agent_start:
        clock_valid &= clock_receipt(agent_start, baseline)
    agent_end = record.get("clock_agent_end")
    if agent_end:
        clock_valid &= clock_receipt(agent_end, baseline, agent_start["sample"])
    if record.get("clock_end"):
        clock_valid &= clock_receipt(record["clock_end"], baseline, start_receipt["sample"])
    if record.get("clock_failure"):
        failed = record["clock_failure"]
        label = failed["label"]
        since = agent_start["sample"] if label == "agent-end" else start_receipt["sample"] if label == "trial-end" else None
        require(clock_receipt(failed, baseline, since) is False, "Invalid clock failure receipt")
        clock_valid = False
    require(clock_valid or missing_clock_fields or record["timing_qualified"] is False, "Clock invalidated time remained qualified")
    independently_qualified_timing = bool(clock_valid and record["timing_qualified"] is True)
    return independently_qualified_timing, missing_clock_fields


def command_review(events, workspace, python):
    flags = []
    patterns = {
        "evaluator_or_reference_path": r"(?:strict[_/-](?:coding|multifile)|multifile[_/-]checks|multifile-reference|coding_workflows|reference\.patch|(?:^|[/\"'])benchmarks[/\"])",
        "parent_or_home_path": r"(?:\.\./|\.\.['\"]|\.parents\[|~[/\\]|/Users/|\.codex/|auth\.json)",
        "dependency_install_or_external_fetch": r"(?:\b(?:pip|uv)\s+(?:install|sync)|\b(?:curl|wget)\b|\bgit\s+(?:clone|fetch|pull)\b)",
    }
    commands = [event.get("item", {}) for event in events if event.get("type") == "item.completed"
                and event.get("item", {}).get("type") == "command_execution"]
    for item in commands:
        text = item.get("command", "")
        inspected = text.replace(str(workspace), "<WORKSPACE>").replace(python, "<PYTHON>")
        findings = [name for name, pattern in patterns.items() if re.search(pattern, inspected)]
        if findings:
            flags.append({"item_id": item.get("id"), "patterns": findings, "command_sha256": digest(text.encode())})
    return {"completed_commands": len(commands), "flagged": flags,
            "scope": "Conservative text flags require manual trace review; they are not proof of file access or OS isolation."}


def audit_trial(record, task, config, study, baseline, encoder):
    public = study / "runs" / record["run_id"]
    raw = Path(config["work_root"]) / "raw" / record["run_id"]
    workspace = Path(config["work_root"]) / "workspaces" / record["run_id"]
    require(typed_equal(read(public / "result.json"), record), "Terminal inventory/result file differ")
    substitutions = [(str(workspace), "<WORKSPACE>"), (str(raw), "<EVALUATOR_LOG>")]
    for kind, mapping in record.get("artifact_sha256", {}).items():
        require(kind in ("raw", "published"), "Unknown trial artifact inventory")
        for name, expected in mapping.items():
            require(Path(name).name == name and sha((raw if kind == "raw" else public) / name) == expected, "Actual trial artifact digest differs")
    events, invalid = raw_events(raw / "events.jsonl")
    observed = read(raw / "observed-events.json") if (raw / "observed-events.json").exists() else []
    require(typed_equal([row["event"] for row in observed], events), "Raw events/actual observed timeline differ")
    usage, turns, completions, errors = provider_usage(events)
    require(typed_equal(record.get("usage"), usage) and record.get("reported_turns", 0) == turns
            and record.get("completed_turn_events", 0) == completions and record.get("usage_errors", []) == errors, "Actual provider usage inventory differs")
    require(record["completed"] is any(event.get("type") == "turn.completed" for event in events), "Actual completion event flag differs")
    require(len(invalid) == len(record.get("invalid_event_lines", [])), "Actual invalid-event inventory differs")
    if invalid:
        require(record["failed"] is True, "Invalid raw event lines disappeared from failed inventory")
        require([row["line_sha256"] for row in record["invalid_event_lines"]] == invalid, "Public invalid raw line hash differs")
    if (public / "events.jsonl").is_file():
        expected_events = [safe(event, substitutions) for event in events if event.get("item", {}).get("type") != "reasoning"]
        require(typed_equal(jsonl(public / "events.jsonl"), expected_events), "Public trace differs from actual events or exposes reasoning")
        expected_timeline = [safe(row, substitutions) for row in observed if row["event"].get("item", {}).get("type") != "reasoning"]
        require(typed_equal(read(public / "event-timestamps.json"), expected_timeline), "Public event timeline differs")
    if (public / "base-files.json").is_file():
        base = read(public / "base-files.json")
        require(digest(json.dumps(base, sort_keys=True).encode()) == record["base_tree_sha256"], "Actual base-tree identity differs")
        with tarfile.open(fileobj=io.BytesIO(git(workspace, "archive", record["base_commit"]))) as archive:
            actual = {item.name: digest(archive.extractfile(item).read()) for item in archive if item.isfile()}
        with tarfile.open(fileobj=io.BytesIO(git(ROOT / task["corpus"], "archive", task["revision"]))) as archive:
            original = {item.name: digest(archive.extractfile(item).read()) for item in archive if item.isfile()}
        require(actual == base == original, "Actual prepared baseline changed from pinned original source")
    else:
        base = {}
        require(record["failed"] is True, "Normal attempt lacks pinned baseline witness")
    if (raw / "command.json").is_file():
        command = read(raw / "command.json")
        cli_settings(command, config, workspace, raw, record["condition"], task["source_root"])
        require(digest(command[-1].encode()) == record["prompt_sha256"] == config["prompt_sha256"][task["id"]], "Actual common prompt identity differs")
        require((public / "prompt.txt").read_text() == safe(command[-1], substitutions), "Published prompt differs")
        require((public / "command.json").read_text() == safe((raw / "command.json").read_text(), substitutions), "Published actual command differs")
    process = record.get("agent_process", {})
    if process.get("launched"):
        require(process["start_new_session"] is True and process["pid"] == process["owned_group"], "Actual CLI ownership differs")
        require(process["command_sha256"] == digest(json.dumps(read(raw / "command.json"), ensure_ascii=False).encode()), "Actual CLI launch command digest differs")
        cleanup = record["agent_cleanup"]
        proven = cleanup.get("owned_group_stopped") is True and record.get("output_drain_complete") is True
        clean = proven and not cleanup.get("forced_kill") and not cleanup.get("errors")
        require(record["cleanup_qualified"] is proven and record["lifecycle_qualified"] is (proven and not record.get("shutdown_error")
            and not (record.get("qwen_shutdown_error") and record.get("qwen_stop", {}).get("owned_group_stopped") is not True)), "CLI safe continuation verdict differs")
        require(record["cleanup_clean"] is bool(clean), "CLI clean-completion verdict differs")
        if not clean:
            require(record["failed"] is True, "Unclean CLI attempt counted as completion")
        if proven:
            try:
                os.killpg(process["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise ValueError("Actual CLI group disappearance unproved")
        start, end = record["agent_start"], record["agent_end"]
        if end is not None:
            require(record["agent_elapsed_s"] == end["monotonic_s"] - start["monotonic_s"], "Actual leader elapsed differs")
            require(end["monotonic_s"] <= cleanup["capture_cleanup_end"]["monotonic_s"], "Agent timer improperly follows cleanup")
        require(cleanup["capture_cleanup_s"] == cleanup["capture_cleanup_end"]["monotonic_s"] - cleanup["capture_cleanup_start"]["monotonic_s"], "Actual separate cleanup elapsed differs")
    elif record.get("completed_correct"):
        raise ValueError("Unlaunched CLI counted as completed correctness")
    independently_qualified_timing, missing_clock_fields = trial_clocks(record, baseline, process.get("launched"))
    if record.get("interrupted"):
        require(record["failed"] is True and record["timing_qualified"] is False, "Interrupted trial lost failed/unknown-timing status")
    if record.get("grade"):
        contract_pass = strict_grade(record["grade"], task)
        compat_pass = compatibility(record["compatibility"], task, raw, workspace, base) if record.get("compatibility") else False
        require(record["correct"] is (contract_pass and compat_pass), "Actual strict+compatibility correctness flag differs")
    else:
        require(record["correct"] is False, "Ungraded attempt counted correct")
    require(record["completed_correct"] is bool(completed(record) and record["correct"]), "Actual normally completed correctness differs")
    if record.get("patch_sha256"):
        require(sha(public / "changes.patch") == record["patch_sha256"], "Published actual patch hash differs")
        patch = git(workspace, "diff", "--binary", record["base_commit"]).decode()
        require((public / "changes.patch").read_text() == safe(patch, substitutions), "Published source patch differs from final candidate")
    receipts = jsonl(raw / "http-receipts.jsonl")
    require([row["number"] for row in receipts] == list(range(1, len(receipts) + 1)), "Actual HTTP receipt order/count differs")
    sources = [source_receipt(receipt, raw, task["source_root"], encoder) for receipt in receipts]
    items = {}
    for event in events:
        item = event.get("item") or {}
        if isinstance(item, dict) and item.get("type") == "mcp_tool_call" and isinstance(item.get("id"), str):
            entry = items.setdefault(item["id"], {})
            if event["type"] == "item.completed":
                require("completed" not in entry, "Duplicate completed MCP item")
                entry["completed"] = item
    require(sum("completed" in value for value in items.values()) == record["mcp_calls"], "Actual completed MCP count differs")
    unused = list(range(len(receipts)))
    overrides = []
    successes, failures = 0, 0
    for item_id, entry in items.items():
        if "completed" not in entry:
            continue
        item = entry["completed"]
        result = item.get("result") or {}
        success = item.get("status") == "completed" and not item.get("error") and not result.get("isError") and not result.get("is_error")
        if not success:
            failures += 1
            continue
        require(item["server"] == "semantic_search" and item["tool"] == "search_code", "Unexpected successful MCP tool")
        arguments = item["arguments"]
        require(isinstance(arguments, dict), "Actual MCP arguments must be retained object")
        prohibited = {key: arguments[key] for key in ("retrieval", "rerank", "rerank_provider", "candidates") if key in arguments}
        if prohibited:
            overrides.append({"item_id": item_id, "explicit_profile_overrides": prohibited})
        content = result.get("content", [])
        require(len(content) == 1 and content[0].get("type") == "text" and result.get("structured_content") is None
                and result.get("structuredContent") is None, "MCP repeated/structured payload differs")
        text = content[0]["text"]
        matches = [index for index in unused if receipts[index].get("success") is True
                   and receipts[index]["query"] == arguments["query"] and receipts[index]["compact_text_sha256"] == digest(text.encode())]
        require(len(matches) >= 1, "Successful MCP payload lacks independent HTTP witness")
        index = matches[0]
        unused.remove(index)
        receipt = receipts[index]
        require(text == compact(receipt["response"]), "Actual MCP text differs from independent returned payload")
        require(all(receipt["settings"].get(key) == value for key, value in arguments.items() if key != "query"), "Actual MCP/HTTP arguments differ")
        expected_rerank = record["condition"] == "mcp_qwen"
        response = receipt["response"]
        default_profile = response["retrieval"] == config["selected_profile"]["retrieval"] and response["rerank"] is expected_rerank
        if not prohibited:
            require(default_profile and receipt["settings"]["candidates"] == config["selected_profile"]["shortlist"], "Actual advertised default condition differs")
        successes += 1
    if record["condition"] == "baseline":
        require(not items and not receipts, "Baseline unexpectedly used search MCP/HTTP")
    native = native_review(record, receipts, raw, public, config)
    successful_qwen = sum(row.get("success") is True and row.get("response", {}).get("rerank") is True
        and row.get("response", {}).get("rerank_provider") == "qwen" for row in receipts)
    require(record["successful_qwen_searches"] == successful_qwen or record.get("publication_error"), "Actual successful Qwen-search count differs")
    return {"run_id": record["run_id"], "integrity_passed": True, "correct": record["correct"],
        "completed_correct": record["completed_correct"], "normally_completed": completed(record),
        "failed": record["failed"], "timed_out": record.get("timed_out"), "interrupted": record.get("interrupted", False),
        "timing_qualified": independently_qualified_timing, "missing_clock_receipts": missing_clock_fields,
        "provider_usage": usage, "reported_turns": turns,
        "mcp": {"completed_items": record["mcp_calls"], "successful": successes, "failed": failures,
                "unfinished": sum("completed" not in entry for entry in items.values()), "profile_overrides": overrides},
        "http": {"total": len(receipts), "unmatched_numbers": [receipts[index]["number"] for index in unused], "source_evidence": sources},
        "native_qwen": native, "observed_command_review": command_review(events, workspace, config["python"]),
        "result_sha256": sha(public / "result.json")}


def audit(study, require_complete=False):
    config_path = study / "frozen-config.json"
    config = read(config_path)
    committed_config = git(ROOT, "show", "HEAD:" + str(config_path.relative_to(ROOT)))
    require(digest(committed_config) == sha(config_path), "Committed prospective coding configuration required")
    manifest = read(study / "tasks.json")
    require(tuple(task["id"] for task in manifest["tasks"]) == TASK_IDS, "Exact prospective observed task set required")
    require(config["schedule"] == schedule() and config["planned_attempts"] == 18, "Exact balanced prospective18 schedule required")
    require(config["model"] == "gpt-6.1-sol" and config["timeout_s"] == 900 and config["selected_profile"]["batch_size"] == 1,
            "Frozen hosted model/timeout/native B1 profile differs")
    require(sha(study / "tasks.json") == config["manifest_sha256"] == sha(ROOT / config["manifest"]), "Frozen strict task manifest copy differs")
    require(sha(config["cli_binary"]) == config["cli_sha256"], "Frozen actual CLI binary changed")
    require(dict(sorted((distribution.metadata["Name"], distribution.version) for distribution in importlib.metadata.distributions()))
            == config["packages"], "Frozen main package closure differs")
    for name, expected in config["artifact_sha256"].items():
        require(sha(ROOT / name) == expected, "Frozen runtime/task/oracle artifact changed: " + name)
        if name.startswith(("src/", "scripts/", "docs/")):
            require(digest(git(ROOT, "show", config["runtime_git_revision"] + ":" + name)) == expected,
                    "Frozen runtime Git/source binding differs: " + name)
    require(sha(config["reranker_python"]) == config["reranker_python_sha256"], "Frozen native interpreter changed")
    native_metadata = decode(subprocess.check_output([config["reranker_python"], "-B", "-c",
        'import importlib.metadata as m,json;print(json.dumps({d.metadata["Name"]:d.version for d in m.distributions()}))'], text=True))
    require(native_metadata == config["reranker_expected_runtime"]["packages"], "Frozen native package closure differs")
    development_decision = read(ROOT / config["development_decision"])
    fresh_plan = read(ROOT / config["fresh_study"] / "frozen-plan.json")
    require(typed_equal(config["selected_profile"], development_decision["selected_config"])
            and config["selected_profile"] in fresh_plan["conditions"], "Prospective full-development/fresh profile changed")
    review = read(ROOT / "results/strict-coding-preflight-2026-10-14/independent-review/review.json")
    require(review["checks_passed"] is True and review["model_calls"] == 0
            and review["actual_controls_sha256"] == manifest["control_receipt_sha256"], "Independent passing strict controls not bound")
    parity = ROOT / config["production_parity"]
    independent_parity = read(parity / "independent-audit.json")
    require(independent_parity["checks_passed"] is True, "Independent production B1 certificate required")
    for name, expected in independent_parity["artifact_sha256"].items():
        require(sha(parity / name) == expected, "Independently audited production parity artifact changed")
    raw_records = read(study / "results.json") if (study / "results.json").is_file() else []
    expected = [f"{number:02d}-r{trial['repetition']}-{trial['task_id']}-{trial['condition']}"
                for number, trial in enumerate(schedule(), 1)]
    require([row["run_id"] for row in raw_records] == expected[:len(raw_records)] and len(raw_records) <= 18,
            "Actual trial inventory is not exact scheduled prefix")
    for row, planned in zip(raw_records, schedule()):
        require(all(typed_equal(row[key], value) for key, value in planned.items()), "Actual trial identity differs")
    if require_complete:
        require(len(raw_records) == 18 and (study / "completion.json").is_file(), "Full18 terminal attempts and final receipt required")
    start = read(study / "run-start.json")
    frozen_hash = sha(config_path)
    require(start["frozen_config_sha256"] == frozen_hash, "Actual run-start frozen binding differs")
    baseline = start["clock"]
    encoder = offline_encoding()
    tasks = {task["id"]: task for task in manifest["tasks"]}
    outcomes = [audit_trial(row, tasks[row["task_id"]], config, study, baseline, encoder) for row in raw_records]
    summary = read(study / "summary.json") if (study / "summary.json").exists() else None
    chronology = [baseline]
    for row in raw_records:
        chronology.extend(row[key]["sample"] for key in ("clock_start", "clock_agent_start", "clock_agent_end", "clock_end") if row.get(key))
    global_clock_ordered = all([sample[key] for sample in chronology] == sorted(sample[key] for sample in chronology)
                               for key in ("wall_time_ns", "perf_counter_ns"))
    require(global_clock_ordered or not all(outcome["timing_qualified"] for outcome in outcomes),
            "Qualified cross-trial clock chronology differs")
    independently_qualified = [dict(row, timing_qualified=outcome["timing_qualified"]) for row, outcome in zip(raw_records, outcomes, strict=True)]
    recomputed = distributions(independently_qualified)
    pairs = paired_measurements(independently_qualified)
    if summary is not None:
        require(summary["planned_attempts"] == 18 and summary["recorded_attempts"] == len(raw_records)
                and summary["inventory_complete"] is (len(raw_records) == 18), "Headline attempt inventory differs")
        expected_missing = sorted(schedule()[len(raw_records):], key=lambda row: (row["task_id"], row["repetition"], row["condition"]))
        require(summary["missing_attempts"] == expected_missing, "Headline missing attempts differ")
        for condition, measured_values in recomputed.items():
            require(all(typed_equal(summary["conditions"][condition][key], value) for key, value in measured_values.items()),
                    "Independent headline condition/measurement reconciliation failed: " + condition)
        require(typed_equal(summary["comparisons"], pairs), "Independent paired-measurement reconciliation failed")
        expected_positions = {condition: {str(position): 2 for position in (1, 2, 3)} for condition in CONDITIONS}
        require(summary["condition_position_counts"] == expected_positions, "Prospective balanced position counts differ")
    completion = read(study / "completion.json") if (study / "completion.json").exists() else None
    if completion:
        require(completion["recorded_attempts"] == 18 == len(raw_records) and completion["frozen_config_sha256"] == frozen_hash,
                "Actual final completion inventory/freeze binding differs")
        require(completion["clock"]["label"] == "study-complete" and clock_receipt(completion["clock"], baseline) is True,
                "Final study clock label/drift invalid")
        require(all(completion["clock"]["sample"][key] >= chronology[-1][key] for key in ("wall_time_ns", "perf_counter_ns")),
                "Final completion clock chronology differs")
    missing = schedule()[len(raw_records):]
    return {"checks_passed": True, "classification": "Independent CPU evidence audit; observed strict coding development rerun",
        "audited_at_utc": datetime.now(timezone.utc).isoformat(), "auditor_sha256": sha(Path(__file__)),
        "auditor_dependency_sha256": {"scripts/audit_production_qwen.py": sha(Path(__file__).with_name("audit_production_qwen.py"))},
        "frozen_config_sha256": frozen_hash, "manifest_sha256": config["manifest_sha256"],
        "planned_attempts": 18, "recorded_attempts": len(raw_records), "missing_attempts": missing,
        "inventory_complete": len(raw_records) == 18, "final_completion_receipt": completion is not None,
        "timing_all_qualified": all(row["timing_qualified"] is True for row in outcomes),
        "global_clock_ordered": global_clock_ordered,
        "conditions": recomputed, "comparisons": pairs, "attempts": outcomes,
        "model_calls_in_audit": 0, "hosted_calls_in_audit": 0, "retrieval_calls_in_audit": 0,
        "tokenization_in_audit": "Pinned offline cl100k context counts and pinned original Qwen tokenizer only; no model forwards",
        "artifact_sha256": {path.name: sha(path) for path in sorted(study.iterdir()) if path.is_file()},
        "limits": ["These are previously observed development coding tasks, not fresh coding validation.",
            "Completed-turn reported input plus output is gross observed usage; interrupted-turn and per-request billing remain unknown.",
            "Cached input, cache writes and reasoning tokens are subsets, never additional gross tokens.",
            "Correctness is limited to strict frozen behavioral probes and restored original compatibility selections; it is not a proof of every possible behavior.",
            "Actual tool use, profile overrides, provider failures, and cache reuse are reported confounds; comparisons do not isolate reranking alone.",
            "Unmatched completed native events may belong to failed searches; their returned source attribution remains unknown and is retained explicitly.",
            "This audit reads terminal candidate workspaces and traces; it never reruns submitted commands or model inference."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    require(not args.output.exists(), "Preserve earlier audits; fresh output directory required")
    args.output.mkdir(parents=True)
    try:
        result = audit(args.study.resolve(), args.require_complete)
    except Exception as error:
        result = {"checks_passed": False, "error_type": type(error).__name__, "error": str(error),
                  "auditor_sha256": sha(Path(__file__)), "model_calls_in_audit": 0, "hosted_calls_in_audit": 0}
    with (args.output / "independent-audit.json").open("x") as stream:
        stream.write(json.dumps(safe(result), indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"checks_passed": result["checks_passed"], "output": str(args.output)}))
    return 0 if result["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
