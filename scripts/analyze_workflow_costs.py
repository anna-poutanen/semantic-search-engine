"""Offline forensic analysis of preserved coding-workflow events.

Provider usage is available only for a completed turn. Per-phase counts below
measure published, path-redacted strings with cl100k_base; they are diagnostic
proxies, not provider tokens or billing. No saved command is executed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                "output_tokens", "reasoning_output_tokens")
DEFAULT_INPUT = ROOT / "results/coding-workflows-mcp-first-2026-10-12"
DEFAULT_OUTPUT = ROOT / "results/workflow-investigation-2026-10-13"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def offline_encoding():
    """Use an already-cached tokenizer and prohibit a fallback download."""
    import tiktoken
    import tiktoken.load
    cache = ROOT / ".cache/tokenizers"
    if not cache.is_dir():
        raise RuntimeError("Offline tokenizer cache unavailable")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
    original = tiktoken.load.read_file
    def no_download(path):
        raise RuntimeError(f"Offline analysis cannot fetch tokenizer data: {path}")
    tiktoken.load.read_file = no_download
    try:
        return tiktoken.get_encoding("cl100k_base")
    finally:
        tiktoken.load.read_file = original


def count_text(text, encoder):
    return {"utf8_bytes": len(text.encode("utf-8")),
            "local_token_proxy": len(encoder.encode(text, disallowed_special=()))}


def classify_command(command):
    """An exclusive primary bucket plus transparent, nonexclusive tags.

    Mixed reads/searches stay mixed: their combined stdout cannot be reliably
    apportioned among subprocesses from this event format.
    """
    tests = bool(re.search(r"(?:\s|\b)pytest(?:\s|$)", command))
    inline_check = "<<" in command and ("assert " in command or "unittest" in command)
    source_read = bool(re.search(r"\b(?:sed\s+-n|cat\s)", command))
    search = bool(re.search(r"\brg\s", command))
    diff = "git diff" in command
    debug = bool(re.search(r"\b(?:ruff|mypy)\b|python\s+-c|python\s+-\s*<<", command))
    tags = [name for name, enabled in (("test", tests or inline_check),
            ("source_read", source_read), ("search", search), ("edit_review", diff),
            ("debug", debug)) if enabled]
    if tests or inline_check:
        phase = "test"
    elif source_read and search:
        phase = "mixed_search_source_read"
    elif source_read:
        phase = "source_read"
    elif search:
        phase = "search"
    elif diff:
        phase = "edit_review"
    elif debug:
        phase = "debug"
    else:
        phase = "workspace"
    return phase, tags


def output_findings(output):
    tags = []
    if "anyio.abc.BlockingPortal alias is deprecated" in output:
        tags.append("dependency_deprecation")
    if "CancelScope.cancel() takes 1 positional argument but 2 were given" in output:
        tags.append("anyio_trio_incompatibility")
    if "No module named ruff" in output or "No module named 'ruff'" in output:
        tags.append("missing_linter")
    if "AssertionError" in output or "KeyError: 'authorization'" in output:
        tags.append("behavioral_assertion_or_seed_failure")
    return tags


def repeated_lines(source, output):
    """Diagnostic exact-line overlap, accepting numbered rg output.

    Empty and very short lines are excluded to avoid counting braces or generic
    Python statements as distinctive repeated source. This is not an evidence
    coverage metric: it does not establish a full function was delivered again.
    """
    source_lines = {line.rstrip() for line in source.splitlines() if len(line.strip()) >= 20}
    output_lines = set()
    for line in output.splitlines():
        normalized = re.sub(r"^(?:[^\n]*?\.py[:-])?\d+[:-]", "", line)
        output_lines.add(normalized.rstrip())
    return {"distinctive_source_lines": len(source_lines),
            "distinctive_lines_seen_again": len(source_lines & output_lines)}


def analyze_run(directory, encoder):
    result = json.loads((directory / "result.json").read_text())
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    receipts = [event["usage"] for event in events if event.get("type") == "turn.completed"]
    if len(receipts) != 1:
        raise ValueError("This analysis requires exactly one provider usage receipt per trial")
    for field in USAGE_FIELDS:
        if receipts[0].get(field) != result["usage"].get(field):
            raise ValueError(f"Provider receipt differs from result metadata: {result['run_id']} / {field}")
    assert result["usage"]["total_tokens"] == receipts[0]["input_tokens"] + receipts[0]["output_tokens"]
    completed = [(index, event["item"]) for index, event in enumerate(events)
                 if event.get("type") == "item.completed" and "item" in event]
    command_rows = []
    phases = defaultdict(Counter)
    phase_findings = defaultdict(Counter)
    seen = set()
    for index, item in completed:
        if item.get("type") != "command_execution" or item["id"] in seen:
            continue
        seen.add(item["id"])
        command, output = item["command"], item.get("aggregated_output", "")
        phase, tags = classify_command(command)
        measured = count_text(output, encoder)
        pytest_seconds = re.findall(r"(?:\d+ (?:passed|failed|deselected|skipped|errors?)[^\n]*) in (\d+(?:\.\d+)?)s", output)
        findings = output_findings(output)
        row = {"event_index": index, "item_id": item["id"], "command": command,
               "phase": phase, "tags": tags, "exit_code": item.get("exit_code"),
               "output": measured, "command_string_proxy": count_text(command, encoder),
               "findings": findings, "measured_tool_duration_s": None,
               "pytest_reported_execution_s": float(pytest_seconds[-1]) if pytest_seconds else None}
        command_rows.append(row)
        phases[phase]["completed_calls"] += 1
        phases[phase]["output_utf8_bytes"] += measured["utf8_bytes"]
        phases[phase]["output_local_token_proxy"] += measured["local_token_proxy"]
        phases[phase]["command_local_token_proxy"] += row["command_string_proxy"]["local_token_proxy"]
        phases[phase]["nonzero_exits"] += item.get("exit_code") not in (0, None)
        phase_findings[phase].update(findings)
    mcp_calls = []
    for index, item in completed:
        if item.get("type") != "mcp_tool_call":
            continue
        returned = item.get("result") or {}
        text_blocks = [block["text"] for block in returned.get("content", []) if block.get("type") == "text"]
        text = "\n".join(text_blocks)
        structured = returned.get("structured_content")
        payload = json.loads(text)
        structured_text = compact(structured) if structured is not None else ""
        rows = []
        later_source = [(j, candidate) for j, candidate in completed if j > index
                        and candidate.get("type") == "command_execution"
                        and any(tag in classify_command(candidate["command"])[1]
                                for tag in ("source_read", "search"))]
        for snippet in payload["results"]:
            source = snippet["source"]
            full_rereads = [candidate["id"] for _, candidate in later_source
                            if source.strip() in candidate.get("aggregated_output", "")]
            overlaps = [(candidate["id"], repeated_lines(source, candidate.get("aggregated_output", "")))
                        for _, candidate in later_source]
            rows.append({key: snippet[key] for key in ("path", "symbol", "start_line", "end_line", "origin")})
            rows[-1].update(source_utf8_bytes=len(source.encode()),
                           source_local_token_proxy=count_text(source, encoder)["local_token_proxy"],
                           exact_full_excerpt_reread_in_items=full_rereads,
                           line_overlap=[{"item_id": identifier, **overlap} for identifier, overlap in overlaps
                                         if overlap["distinctive_lines_seen_again"]])
        call = {"item_id": item["id"], "event_index": index, "arguments": item["arguments"],
                "text_payload": count_text(text, encoder),
                "structured_payload": count_text(structured_text, encoder),
                "text_and_structured_are_equivalent": structured == payload,
                "potential_transport_duplicate_local_token_proxy": count_text(structured_text, encoder)["local_token_proxy"]
                if structured == payload else 0,
                "host_model_payload_serialization_known": False,
                "server_reported_single_payload_token_usage": payload.get("token_usage"),
                "results": rows, "result_count": len(rows),
                "exact_full_excerpts_reread": sum(bool(row["exact_full_excerpt_reread_in_items"]) for row in rows),
                "exact_reread_source_bytes": sum(row["source_utf8_bytes"] for row in rows
                                                if row["exact_full_excerpt_reread_in_items"])}
        mcp_calls.append(call)
        phases["mcp_search"]["completed_calls"] += 1
        phases["mcp_search"]["output_utf8_bytes"] += call["text_payload"]["utf8_bytes"]
        phases["mcp_search"]["output_local_token_proxy"] += call["text_payload"]["local_token_proxy"]
        phases["mcp_search"]["command_local_token_proxy"] += count_text(compact(item["arguments"]), encoder)["local_token_proxy"]
    assert len(command_rows) == result["command_calls"]
    assert len(mcp_calls) == result["mcp_calls"]
    assert sum(stats["output_local_token_proxy"] for stats in phases.values()) == (
        sum(row["output"]["local_token_proxy"] for row in command_rows)
        + sum(call["text_payload"]["local_token_proxy"] for call in mcp_calls))
    dependency_outputs = [row for row in command_rows if any(tag in row["findings"]
                          for tag in ("dependency_deprecation", "anyio_trio_incompatibility"))]
    changed = [item for _, item in completed if item.get("type") == "file_change"]
    setup, agent, grade, total = (result.get("setup_s"), result["agent_elapsed_s"],
                                 result.get("grade", {}).get("elapsed_s"), result.get("end_to_end_s"))
    return {"run_id": result["run_id"], "task_id": result["task_id"], "condition": result["condition"],
            "provenance": {"events_sha256": sha256(directory / "events.jsonl"),
                           "result_sha256": sha256(directory / "result.json"),
                           "patch_sha256": sha256(directory / "changes.patch")},
            "actual_provider_usage": result["usage"],
            "measured_time_s": {"agent_cli": agent, "setup_excluded_from_agent": setup,
                                "final_grading": grade, "end_to_end": total,
                                "unassigned_remaining_overhead": total - setup - agent - grade
                                if all(value is not None for value in (total, setup, agent, grade)) else None,
                                "per_tool_and_per_phase_available": False},
            "phase_output_proxies": {phase: {**dict(stats), "findings": dict(phase_findings[phase])}
                                     for phase, stats in sorted(phases.items())},
            "commands": command_rows, "mcp_calls": mcp_calls,
            "dependency_failure_output": {"calls": len(dependency_outputs),
                                          "utf8_bytes": sum(row["output"]["utf8_bytes"] for row in dependency_outputs),
                                          "local_token_proxy": sum(row["output"]["local_token_proxy"] for row in dependency_outputs)},
            "file_change_events": len(changed),
            "final_patch_proxy": count_text((directory / "changes.patch").read_text(), encoder),
            "edit_tool_input_text_recorded": False,
            "quality_review": {"passed_selected_checks": result["correct"],
                               "changed_files": result["changed_files"]}}


def aggregate(runs):
    conditions = {}
    for condition in ("baseline", "mcp"):
        subset = [row for row in runs if row["condition"] == condition]
        phases = defaultdict(Counter)
        for row in subset:
            for phase, values in row["phase_output_proxies"].items():
                phases[phase].update({key: value for key, value in values.items() if key != "findings"})
        usage = {field: sum(row["actual_provider_usage"][field] for row in subset)
                 for field in (*USAGE_FIELDS, "total_tokens", "uncached_input_tokens")}
        usage["uncached_input_plus_output_tokens"] = usage["uncached_input_tokens"] + usage["output_tokens"]
        usage["cached_input_fraction"] = usage["cached_input_tokens"] / usage["input_tokens"]
        conditions[condition] = {"actual_provider_usage": usage,
            "agent_elapsed_s": sum(row["measured_time_s"]["agent_cli"] for row in subset),
            "setup_s": sum(row["measured_time_s"]["setup_excluded_from_agent"] for row in subset),
            "end_to_end_s": sum(row["measured_time_s"]["end_to_end"] for row in subset),
            "phase_output_proxies": {phase: dict(values) for phase, values in sorted(phases.items())},
            "dependency_failure_output_utf8_bytes": sum(row["dependency_failure_output"]["utf8_bytes"] for row in subset),
            "dependency_failure_output_local_token_proxy": sum(row["dependency_failure_output"]["local_token_proxy"] for row in subset)}
    base, mcp = conditions["baseline"], conditions["mcp"]
    changes = {field: 100 * (mcp["actual_provider_usage"][field] / base["actual_provider_usage"][field] - 1)
               for field in ("total_tokens", "uncached_input_tokens", "uncached_input_plus_output_tokens", "output_tokens")}
    changes.update(agent_time_pct=100 * (mcp["agent_elapsed_s"] / base["agent_elapsed_s"] - 1),
                   end_to_end_time_pct=100 * (mcp["end_to_end_s"] / base["end_to_end_s"] - 1))
    calls = [call for row in runs for call in row["mcp_calls"]]
    return {"conditions": conditions, "mcp_relative_change_pct": changes,
            "retrieval": {"calls": len(calls), "returned_excerpts": sum(c["result_count"] for c in calls),
                          "exact_full_excerpts_reread_later": sum(c["exact_full_excerpts_reread"] for c in calls),
                          "text_payload_local_token_proxy": sum(c["text_payload"]["local_token_proxy"] for c in calls),
                          "potential_structured_duplicate_local_token_proxy": sum(c["potential_transport_duplicate_local_token_proxy"] for c in calls),
                          "provider_saw_duplicate_known": False}}


def render_findings(result):
    summary = result["summary"]
    baseline, mcp = (summary["conditions"][name] for name in ("baseline", "mcp"))
    base_usage, mcp_usage = baseline["actual_provider_usage"], mcp["actual_provider_usage"]
    local = [sum(phase["output_local_token_proxy"] for phase in row["phase_output_proxies"].values())
             for row in (baseline, mcp)]
    calls = [sum(phase["completed_calls"] for name, phase in row["phase_output_proxies"].items()
                 if name != "mcp_search") for row in (baseline, mcp)]
    changes = summary["mcp_relative_change_pct"]
    lines = ["# Why the guided MCP pilot cost more", "",
        f"MCP produced fewer shell calls ({calls[1]} versus {calls[0]}) and less captured output when each MCP text payload is counted once ({local[1]:,} versus {local[0]:,} cl100k_base tokens). It still used {changes['total_tokens']:.2f}% more provider-reported input plus output and took {changes['agent_time_pct']:.2f}% longer. Raw output volume alone therefore does not explain the regression.", "",
        "The local token counts below are diagnostic proxies over published, path-redacted strings. Actual provider usage is available only for each completed turn. These logs contain neither request-level receipts nor tool/phase timestamps, so they cannot establish actual tokens or elapsed time per call. Cached input and reasoning output are subsets, not additional tokens. No monetary billing claim is made.", "",
        "| Recorded metric across three pairs | Baseline | MCP | Change |",
        "|---|---:|---:|---:|",
        f"| Actual input + output tokens | {base_usage['total_tokens']:,} | {mcp_usage['total_tokens']:,} | +{changes['total_tokens']:.2f}% |",
        f"| Actual uncached input tokens | {base_usage['uncached_input_tokens']:,} | {mcp_usage['uncached_input_tokens']:,} | +{changes['uncached_input_tokens']:.2f}% |",
        f"| Uncached input + all output tokens | {base_usage['uncached_input_plus_output_tokens']:,} | {mcp_usage['uncached_input_plus_output_tokens']:,} | +{changes['uncached_input_plus_output_tokens']:.2f}% |",
        f"| Cached share of input | {base_usage['cached_input_fraction']:.1%} | {mcp_usage['cached_input_fraction']:.1%} | {(mcp_usage['cached_input_fraction']-base_usage['cached_input_fraction'])*100:.2f} percentage points |",
        f"| Agent CLI seconds | {baseline['agent_elapsed_s']:.2f} | {mcp['agent_elapsed_s']:.2f} | +{changes['agent_time_pct']:.2f}% |",
        f"| Setup seconds, excluded above | {baseline['setup_s']:.2f} | {mcp['setup_s']:.2f} | Includes fresh MCP indexing/warmup |",
        f"| Complete trial seconds | {baseline['end_to_end_s']:.2f} | {mcp['end_to_end_s']:.2f} | +{changes['end_to_end_time_pct']:.2f}% |", "",
        "Caching differs descriptively between conditions. In particular, the uncached increase is dominated by the Starlette pair. Without request-level data, cache-hit differences cannot be assigned to MCP, model batching, source rereads or any particular tool. The setup totals describe fresh per-trial indexes, not the cost of every query against a resident service.", "",
        "| Task | Actual baseline tokens | Actual MCP tokens | Token change | Baseline seconds | MCP seconds |",
        "|---|---:|---:|---:|---:|---:|"]
    by_task = defaultdict(dict)
    for row in result["runs"]:
        by_task[row["task_id"]][row["condition"]] = row
    for task, pair in by_task.items():
        b, m = pair["baseline"], pair["mcp"]
        bt, mt = b["actual_provider_usage"]["total_tokens"], m["actual_provider_usage"]["total_tokens"]
        lines.append(f"| {task} | {bt:,} | {mt:,} | {100*(mt/bt-1):+.2f}% | {b['measured_time_s']['agent_cli']:.2f} | {m['measured_time_s']['agent_cli']:.2f} |")
    lines.extend(["", "Requests used fewer actual total tokens with MCP; Click and Starlette used more. This is one run per task and condition. Different test code, batching, reasoning and provider latency remain confounders, and these results do not isolate a stable causal effect.", "",
        "| Exclusive command/output phase | Baseline calls | MCP calls | Baseline local output proxy | MCP local output proxy |",
        "|---|---:|---:|---:|---:|"])
    for phase in sorted(set(baseline["phase_output_proxies"]) | set(mcp["phase_output_proxies"])):
        b, m = (row["phase_output_proxies"].get(phase, {}) for row in (baseline, mcp))
        lines.append(f"| {phase} | {b.get('completed_calls',0)} | {m.get('completed_calls',0)} | {b.get('output_local_token_proxy',0):,} | {m.get('output_local_token_proxy',0):,} |")
    retrieval = summary["retrieval"]
    lines.extend(["", "Mixed search/read commands remain mixed because their combined stdout cannot be reliably divided among subprocesses. Test output includes expected seed failures, successful checks and environment failures. The JSON retains every command, tags, output size and exit code for inspection.", "",
        f"{retrieval['exact_full_excerpts_reread_later']} of {retrieval['returned_excerpts']} retrieved excerpts appear again in full in later source/search outputs: one of three Requests excerpts, three of four Click excerpts, and four of five Starlette excerpts. MCP did not consistently replace those reads. This is a concrete workflow inefficiency, but it cannot be assigned a numerical share of the measured provider overhead: total single-copy captured output was still lower with MCP.", "",
        "All three MCP results contain equivalent text and structured copies of the same envelope. The text payloads contain only 1,101, 908 and 846 local tokens, respectively. Agents requested three to five results, and two calls disabled dependency expansion. Large default payload budgets were therefore not the observed pattern. The recorded transport has another 2,855-token equivalent structured copy; whether Codex fed both representations to the model is unobserved. Removing duplicate transport data is a reasonable intervention, not a proven explanation for actual token growth.", "",
        f"Starlette dependency-warning/incompatibility outputs total {baseline['dependency_failure_output_utf8_bytes']:,} bytes ({baseline['dependency_failure_output_local_token_proxy']:,} local tokens) in baseline and {mcp['dependency_failure_output_utf8_bytes']:,} bytes ({mcp['dependency_failure_output_local_token_proxy']:,} local tokens) in MCP. Baseline emitted more of this output. The AnyIO/Trio problem made both workflows noisy and changed their testing strategies; it is not an observed cause of the additional MCP usage or time.", "",
        "Both Starlette repairs replaced the supplied HTTP TestClient failure test with direct BackgroundTasks invocation. Direct tests were stronger for callback order and sync/async failure, but avoided an incompatible HTTP cleanup path and removed its integration coverage. Both Click repairs passed selected success checks while suppressing atomic commit failures through safecall. These differences show why passing the ten declared verifier methods is weaker than full correctness.", "",
        "The next experiment should make retrieval replace broad source reads, return one concise context representation, and prepare a compatible isolated test environment before timing. Keep full logs outside the model context and return bounded failure summaries, without hiding failure status or weakening required integration checks. Include larger tasks whose repairs and gates span several production files; retain absent/negative controls and atomic-write failure checks.", "",
        "Prospectively timestamp event reception and independently time local backend calls. CLI events may arrive in a batch, so their observed intervals cannot establish exact tool execution, queue delays, provider phases, or thinking time. If the runner exposes request-level usage updates, preserve them as receipts; otherwise continue reporting turn totals without invented phase attribution. Freeze the new tasks, gates, context format and common workflow policy before running either condition, with repeated or balanced trials when account limits permit.", "",
        "Reproduce the analysis with `.venv/bin/python scripts/analyze_workflow_costs.py`. It uses cached cl100k_base data, forbids tokenizer downloads, does not execute saved commands, and preserves every prior artifact. [Machine-readable analysis](analysis.json) contains receipt validation, phase counts, reread evidence and input hashes."])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    encoder = offline_encoding()
    runs = [analyze_run(directory, encoder) for directory in sorted((args.input / "runs").iterdir())
            if (directory / "result.json").exists()]
    if len(runs) != 6:
        raise ValueError("Expected the original six guided trials")
    inventory = Counter((row["task_id"], row["condition"]) for row in runs)
    expected = Counter((task, condition) for task in ("requests_auth_ports", "click_atomic_close",
                                                     "starlette_background_order")
                       for condition in ("baseline", "mcp"))
    if inventory != expected:
        raise ValueError("Task/condition inventory differs from the six guided pilot runs")
    summary = aggregate(runs)
    review = json.loads((args.input / "patch-review.json").read_text())
    result = {"schema_version": 1, "analyzed_at": datetime.now(timezone.utc).isoformat(),
              "input": str(args.input.relative_to(ROOT)),
              "method": {"actual_usage": "Only turn.completed provider usage is actual; cached input and reasoning output are subcategories.",
                         "proxies": "UTF-8 bytes and cl100k_base counts of published path-redacted strings. These exclude unknown provider wrappers and are not model tokens or bills.",
                         "phase_classification": "Exclusive deterministic command buckets; mixed search/read commands remain mixed. Tags and commands are preserved for audit.",
                         "timing": "Only whole CLI, setup, final grading and end-to-end times were preserved. Tool/phase/model-wait durations cannot be reconstructed.",
                         "mcp_duplication": "Text and structured payloads repeat equivalent data in recorded transport. Whether the host serialized both into model input is unobserved.",
                         "source_reread": "Exact complete-excerpt rereads are a conservative substring match against later read/search outputs. Distinctive-line overlap is additional diagnostic evidence, not coverage proof.",
                         "edits": "Events record changed paths, not edit tool arguments. The final patch proxy cannot reconstruct all generated edits.",
                         "execution": "Offline analysis only; saved commands are never executed and prior artifacts are never edited."},
              "provenance": {"script_sha256": sha256(__file__), "frozen_config_sha256": sha256(args.input / "frozen-config.json"),
                             "patch_review_sha256": sha256(args.input / "patch-review.json")},
              "summary": summary, "quality_findings": review["summary"], "runs": runs}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (args.output / "findings.md").write_text(render_findings(result))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
