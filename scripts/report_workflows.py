#!/usr/bin/env python3
"""Render completed paired workflow results without estimating model usage."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any


CONDITIONS = ("baseline", "mcp")
TOKEN_FIELDS = (
    "input_tokens", "cached_input_tokens", "uncached_input_tokens",
    "output_tokens", "total_tokens", "reasoning_output_tokens",
    "cache_write_input_tokens",
)
METRICS = (
    ("agent_elapsed_s", "Agent wall time (s)", False),
    ("input_tokens", "Input tokens", True),
    ("cached_input_tokens", "Cached input tokens", True),
    ("uncached_input_tokens", "Uncached input tokens", True),
    ("output_tokens", "Output tokens", True),
    ("total_tokens", "Gross total tokens", True),
)


def number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def measurement(record: dict, metric: str) -> int | float | None:
    value = record.get(metric) if metric == "agent_elapsed_s" else (record.get("usage") or {}).get(metric)
    if value is not None and not number(value):
        raise ValueError(f"Invalid {metric} in {record['run_id']}")
    return value


def format_number(value: int | float | None, *, tokens: bool = False) -> str:
    if value is None:
        return "unavailable"
    if tokens:
        return f"{value:,.0f}" if value == int(value) else f"{value:,.1f}"
    return f"{value:,.2f}"


def reduction(baseline: int | float | None, mcp: int | float | None) -> float | None:
    if baseline is None or mcp is None or baseline <= 0:
        return None
    return 100 * (baseline - mcp) / baseline


def format_pct(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:+.1f}%"


def cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    return "\n".join([
        "| " + " | ".join(cell(v) for v in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows),
    ])


def validate_inventory(config: dict, records: list[dict], summary: dict) -> list[str]:
    expected = [(row["task_id"], row["condition"]) for row in config["schedule"]]
    observed = [(row["task_id"], row["condition"]) for row in records]
    if len(set(expected)) != len(expected) or len(set(observed)) != len(observed):
        raise ValueError("Duplicate task/condition entries")
    if sorted(observed) != sorted(expected):
        raise ValueError("Refusing report before every declared trial is recorded")
    tasks = list(dict.fromkeys(task for task, _ in expected))
    if any({c for t, c in expected if t == task} != set(CONDITIONS) for task in tasks):
        raise ValueError("Every task must have exactly one baseline and one MCP trial")
    if summary.get("trial_count") != len(records):
        raise ValueError("summary.json trial count differs from results.json")
    for record in records:
        usage = record.get("usage")
        if usage:
            for field in TOKEN_FIELDS:
                value = usage.get(field)
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError(f"Invalid reported {field} in {record['run_id']}")
            inp, cached, out, total = (usage.get(k) for k in (
                "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"
            ))
            if inp is not None and out is not None and total != inp + out:
                raise ValueError(f"Total tokens double-counted or inconsistent: {record['run_id']}")
            if inp is not None and cached is not None:
                if cached > inp or usage.get("uncached_input_tokens") != inp - cached:
                    raise ValueError(f"Cached-input categories inconsistent: {record['run_id']}")
            reasoning = usage.get("reasoning_output_tokens")
            if out is not None and reasoning is not None and reasoning > out:
                raise ValueError(f"Reasoning output exceeds total output: {record['run_id']}")
    for condition in CONDITIONS:
        rows = [r for r in records if r["condition"] == condition]
        actual = summary["conditions"][condition]
        totals = [measurement(r, "total_tokens") for r in rows]
        totals = [value for value in totals if value is not None]
        elapsed = [measurement(r, "agent_elapsed_s") for r in rows]
        elapsed = [value for value in elapsed if value is not None]
        for key, expected_value in (
            ("runs", len(rows)),
            ("correct", sum(bool(r.get("correct")) for r in rows)),
            ("completed_correct", sum(bool(r.get("completed_correct")) for r in rows)),
            ("mcp_calls", sum(r.get("mcp_calls", 0) for r in rows)),
            ("runs_with_mcp_calls", sum(r.get("mcp_calls", 0) > 0 for r in rows)),
            ("timed_out", sum(bool(r.get("timed_out")) for r in rows)),
            ("usage_observed", len(totals)),
            ("total_tokens", sum(totals) if totals else None),
            ("median_total_tokens", statistics.median(totals) if totals else None),
            ("total_agent_elapsed_s", sum(elapsed) if elapsed else None),
            ("median_agent_elapsed_s", statistics.median(elapsed) if elapsed else None),
        ):
            if actual.get(key) != expected_value:
                raise ValueError(f"summary.json {condition}.{key} is inconsistent")
    return tasks


def paired_metrics(pairs: list[tuple[dict, dict]]) -> str:
    rows = []
    for metric, label, tokens in METRICS:
        values = [(measurement(b, metric), measurement(m, metric)) for b, m in pairs]
        values = [(b, m) for b, m in values if b is not None and m is not None]
        if not values:
            rows.append([label, 0, "unavailable", "unavailable", "unavailable", "unavailable", "unavailable", "unavailable", "unavailable"])
            continue
        baseline = [b for b, _ in values]
        mcp = [m for _, m in values]
        paired_reductions = [r for b, m in values if (r := reduction(b, m)) is not None]
        baseline_median, mcp_median = statistics.median(baseline), statistics.median(mcp)
        rows.append([
            label, len(values), format_number(sum(baseline), tokens=tokens),
            format_number(sum(mcp), tokens=tokens), format_pct(reduction(sum(baseline), sum(mcp))),
            format_number(baseline_median, tokens=tokens), format_number(mcp_median, tokens=tokens),
            format_pct(reduction(baseline_median, mcp_median)),
            format_pct(statistics.median(paired_reductions) if paired_reductions else None),
        ])
    return markdown_table([
        "Metric", "Measured pairs", "Baseline sum", "MCP sum", "Reduction of sums",
        "Baseline median", "MCP median", "Reduction of medians", "Median paired reduction",
    ], rows)


def parse_arguments(item: dict) -> dict | None:
    arguments = item.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    return arguments if isinstance(arguments, dict) else None


def tool_error(item: dict) -> bool:
    result = item.get("result") or {}
    result_error = isinstance(result, dict) and bool(result.get("isError") or result.get("is_error"))
    return bool(item.get("error") or item.get("status") in ("failed", "error") or result_error)


def tool_payload(item: dict) -> dict | None:
    result = item.get("result")
    if not isinstance(result, dict):
        return None
    for key in ("structured_content", "structuredContent"):
        if isinstance(result.get(key), dict):
            return result[key]
    for content in result.get("content", []):
        if content.get("type") == "text":
            try:
                payload = json.loads(content["text"])
            except (ValueError, KeyError):
                continue
            if isinstance(payload, dict):
                return payload
    return None


def source_receipt(payload: dict | None) -> tuple[bool, str]:
    if payload is None or not isinstance(payload.get("query"), str) or not isinstance(payload.get("results"), list):
        return False, "unavailable"
    sources = []
    for row in payload["results"]:
        source = row.get("source")
        if (not isinstance(source, str) or not source
                or row.get("source_hash") != hashlib.sha256(source.encode()).hexdigest()
                or type(row.get("start_line")) is not int or type(row.get("end_line")) is not int
                or not 1 <= row["start_line"] <= row["end_line"]):
            return False, "invalid source receipt"
        sources.append(f"{row.get('path')}:{row.get('symbol')} L{row['start_line']}–{row['end_line']} ({row.get('origin')}; truncated={row.get('truncated')}; SHA256={row['source_hash']})")
    return True, "; ".join(sources) if sources else "no excerpts returned"


def render(config: dict, records: list[dict], summary: dict, artifacts: Path | None = None) -> str:
    tasks = validate_inventory(config, records, summary)
    by_task = {task: {r["condition"]: r for r in records if r["task_id"] == task} for task in tasks}
    pairs = [(by_task[task]["baseline"], by_task[task]["mcp"]) for task in tasks]
    for task, (baseline, mcp) in zip(tasks, pairs):
        for key in ("base_tree_sha256", "prompt_sha256"):
            if baseline.get(key) is not None and mcp.get(key) is not None and baseline[key] != mcp[key]:
                raise ValueError(f"Paired {key} differs for {task}")
    ordered_records = [by_task[row["task_id"]][row["condition"]] for row in config["schedule"]]
    complete = {condition: sum(bool(r.get("completed_correct")) for r in records if r["condition"] == condition) for condition in CONDITIONS}
    selected = [(b, m) for b, m in pairs if b.get("completed_correct") and m.get("completed_correct")]
    followup = config.get("followup")
    declared_methods = followup.get("declared_verifier_methods_per_condition") if followup else None
    if artifacts is not None and (artifacts / "tasks.json").exists():
        task_manifest = json.loads((artifacts / "tasks.json").read_text())
        if {task["id"] for task in task_manifest["tasks"]} != set(tasks):
            raise ValueError("Task manifest differs from report inventory")
        declared_methods = sum(task["seed_validation"]["reference"]["tests_run"] for task in task_manifest["tasks"])
    early_close_probe = artifacts / "close-error-probe.json" if artifacts is not None else None
    material_gaps = []
    if early_close_probe is not None and early_close_probe.exists():
        early_diagnostic = json.loads(early_close_probe.read_text())
        material_gaps = [row for row in early_diagnostic["results"] if row["source"] != "pinned_upstream"
                         and not row["observed"]["matches_pinned_upstream_error_behavior"]]
    resource_summary = "Aggregate resource comparisons preserve unavailable measurements and are shown below."
    token_values = [(measurement(b, "total_tokens"), measurement(m, "total_tokens")) for b, m in pairs]
    time_values = [(measurement(b, "agent_elapsed_s"), measurement(m, "agent_elapsed_s")) for b, m in pairs]
    if all(b is not None and m is not None for b, m in token_values + time_values):
        baseline_tokens, mcp_tokens = (sum(row[i] for row in token_values) for i in (0, 1))
        baseline_time, mcp_time = (sum(row[i] for row in time_values) for i in (0, 1))
        token_change = reduction(baseline_tokens, mcp_tokens)
        time_change = reduction(baseline_time, mcp_time)
        resource_summary = (
            f"The MCP-assigned workload used **{mcp_tokens:,} gross tokens versus {baseline_tokens:,}** in baseline "
            f"({abs(token_change):.1f}% {'fewer' if token_change >= 0 else 'more'}). "
            f"Summed agent wall time was **{mcp_time:.2f}s versus {baseline_time:.2f}s** "
            f"({abs(time_change):.1f}% {'less' if time_change >= 0 else 'more'}). Setup and complete evaluator-run time are published separately below."
        )
        if sum(record.get("mcp_calls", 0) for _, record in pairs) == 0:
            resource_summary += " No MCP tools were called, so these condition differences do not measure active retrieval benefit."
        elif mcp_tokens >= baseline_tokens and mcp_time >= baseline_time:
            resource_summary += " This follow-up showed no aggregate token or completion-time savings on these reused tasks."
    parts = [
        "# MCP-first coding workflow follow-up" if followup else "# Paired coding workflow pilot",
        f"The MCP condition completed a repair and passed every selected hidden check in **{complete['mcp']}/{len(tasks)} tasks**; the baseline did so in **{complete['baseline']}/{len(tasks)} tasks**. All {len(records)} declared trials are retained. The same model, prompt and seeded starting source were assigned within each pair; preserved fixture and prompt hashes document successful preparations.",
        resource_summary,
        (f"Each condition was assigned **{declared_methods} declared hidden-verifier methods** across these tasks. This count is separate from parameterized agent-authored test cases reproduced later."
         if declared_methods is not None else "The exact declared verifier counts are preserved in the task manifest."),
        (f"**Selected-check success is weaker than complete correctness:** a separate CPU probe found {len(material_gaps)} atomic-output repairs that reported command success after a failed commit, leaving the old destination bytes. Their formal labels remain unchanged; [supplemental evidence](close-error-probe.json) documents the defect."
         if material_gaps else "Selected-check success is limited to the declared behavior and controls; supplemental review is reported separately."),
        f"This is a descriptive study of {len(tasks)} authored regressions in pinned Requests, Click and Starlette repositories. It measures actual agent patches and runtime-reported token usage. The regressions were introduced deliberately; these are not newly discovered upstream bugs. The repositories were used in development evaluation, and the repairs are small. One run per condition cannot establish significance, broad generalization, stable savings, or the project's 30% token-saving target.",
        f"Agent: `{config['model']}`, reasoning effort `{config['common_config']['model_reasoning_effort']}`; `{config['cli_version']}` on {config['host'].get('machdep.cpu.brand_string', config['host'].get('machine', 'recorded host'))}. The frozen schedule alternates which condition runs first. No retries were selected from outcomes. See [frozen configuration](frozen-config.json), [task manifest](tasks.json), [raw measurements](results.json), and [machine summary](summary.json).",
        "## Correctness and time",
        "Correctness is limited to the withheld regression checks and related negative controls, not full upstream test suites. The completed-and-passed label additionally requires a completed agent turn, exit code zero, and no agent failure or timeout. Agent wall time runs from CLI launch to process exit, including its initialization, model waits, tools, tests and MCP adapter startup. Checkout preparation, backend indexing/warmup and evaluator grading are separate costs.",
    ]
    if followup:
        parts.insert(5, "This is a separate exploratory follow-up using the first original task per repository, selected deterministically rather than from observed savings. Both conditions receive the same added instruction to use search_code before broad source reads when available. It measures an explicitly encouraged search workflow on reused tasks, not spontaneous adoption or a fresh holdout. Its resources and outcomes are not pooled with the original availability pilot or the separate exposure diagnostic.")
    parts.append(markdown_table([
        "Task", "Condition", "Hidden checks", "Completed and passed", "Agent wall time (s)",
        "Setup (s)", "Grading (s)", "Exit", "Timeout",
    ], [[
        r["task_id"], r["condition"], "pass" if r.get("correct") else "fail",
        "yes" if r.get("completed_correct") else "no", format_number(r.get("agent_elapsed_s")),
        format_number(r.get("setup_s")), format_number((r.get("grade") or {}).get("elapsed_s")),
        r.get("agent_exit_code", "unavailable"), "yes" if r.get("timed_out") else "no",
    ] for r in ordered_records]))
    setup_rows = []
    for condition in CONDITIONS:
        condition_records = [r for r in records if r["condition"] == condition]
        for key, label in (("setup_s", "Setup"), ("end_to_end_s", "Full evaluator run")):
            values = [r[key] for r in condition_records if r.get(key) is not None]
            setup_rows.append([condition, label, len(values), format_number(sum(values) if values else None), format_number(statistics.median(values) if values else None)])
    parts.append(markdown_table(["Condition", "Cost", "Measured runs", "Total (s)", "Median (s)"], setup_rows))
    parts.append("Setup includes fixture preparation and initial validation, plus MCP model loading, indexing and warmup when applicable. The full evaluator interval also includes post-agent cleanup, grading and patch capture; it is not a model-only or warm-search latency.")
    parts.extend([
        "## Actual token usage",
        "These counts come from terminal `turn.completed` usage events. **Gross total = input + output.** Cached input is already included in input; reasoning output is already included in output. Neither subcategory is added again. Uncached input = input − cached input. Missing measurements remain unavailable. These are provider-reported token counts, not the local `cl100k_base` excerpt budget and not a dollar-cost estimate.",
    ])
    parts.append("Reported terminal usage events per trial: " + ", ".join(
        f"`{r['run_id']}`={r.get('reported_turns', 'unavailable')}" for r in ordered_records
    ) + ". The recorded aggregate preserves the usage fields supplied by those terminal events; missing fields are not reconstructed from transcript text.")
    parts.append(markdown_table([
        "Task", "Condition", "Input", "Cached input", "Uncached input", "Output",
        "Gross total", "Reasoning output", "Cache writes",
    ], [[r["task_id"], r["condition"], *(format_number((r.get("usage") or {}).get(field), tokens=True) for field in TOKEN_FIELDS)] for r in ordered_records]))
    parts.extend([
        "## Paired resource comparisons",
        "Positive reductions mean MCP used less; negative reductions mean it used more. Each metric includes only pairs with that metric available in both conditions, with its denominator shown. “Reduction of sums” compares aggregate workload; “reduction of medians” compares condition medians; “median paired reduction” takes the median of task-specific percentages. These are different statistics and should not be substituted for one another. Percentages with a zero baseline are unavailable.",
        "### All declared task pairs",
        paired_metrics(pairs),
        "### Both completed and passed selected checks",
        f"This subset contains **{len(selected)}/{len(tasks)} pairs**. It compares resources when both conditions delivered a checked repair, while the complete inventory above retains failures and non-use of MCP.",
        paired_metrics(selected),
    ])
    parts.append(markdown_table([
        "Task", "Both completed and passed", "Time reduction", "Input reduction",
        "Cached-input reduction", "Uncached-input reduction", "Output reduction", "Gross-total reduction",
    ], [[
        task, "yes" if b.get("completed_correct") and m.get("completed_correct") else "no",
        *(format_pct(reduction(measurement(b, metric), measurement(m, metric))) for metric, _, _ in METRICS),
    ] for task, (b, m) in zip(tasks, pairs)]))
    defaults = config["mcp"]["defaults"]
    mcp_records = [r for r in ordered_records if r["condition"] == "mcp"]
    calls = [(r, item) for r in mcp_records for item in r.get("mcp_items", [])]
    if len(calls) != sum(r.get("mcp_calls", 0) for r in mcp_records):
        raise ValueError("MCP call counts differ from preserved call items")
    errors = sum(tool_error(item) for _, item in calls)
    overridden_calls = 0
    call_rows = []
    receipt_rows = []
    successful_calls = 0
    for record, item in calls:
        arguments = parse_arguments(item)
        overrides = ({k: v for k, v in arguments.items() if k in defaults and v != defaults[k]} if arguments is not None else None)
        extra = ({k: v for k, v in arguments.items() if k not in defaults and k != "query"} if arguments is not None else None)
        overridden_calls += bool(overrides)
        payload = tool_payload(item)
        valid_receipt, sources = source_receipt(payload)
        successful = bool(item.get("server") == "semantic_search" and item.get("tool") == "search_code"
                          and item.get("status") == "completed" and not tool_error(item)
                          and valid_receipt and arguments is not None and payload["query"] == arguments.get("query"))
        successful_calls += successful
        local_usage = payload.get("token_usage", {}) if payload is not None else {}
        receipt_rows.append([
            record["task_id"], item.get("id", "unavailable"), "yes" if successful else "no",
            payload.get("match_status", "unavailable") if payload is not None else "unavailable",
            local_usage.get("tokens", "unavailable"), local_usage.get("budget", "unavailable"), sources,
            f"[trace](runs/{record['run_id']}/events.jsonl)",
        ])
        call_rows.append([
            record["task_id"], item.get("tool", item.get("name", "unavailable")),
            json.dumps(arguments, ensure_ascii=False, sort_keys=True) if arguments is not None else "unavailable",
            json.dumps(overrides, sort_keys=True) if overrides is not None else "unavailable",
            json.dumps(extra, sort_keys=True) if extra is not None else "unavailable",
            "yes" if tool_error(item) else "no",
        ])
    parts.extend([
        "## MCP uptake and actual calls",
        f"**{sum(r.get('mcp_calls', 0) > 0 for r in mcp_records)}/{len(mcp_records)} MCP-assigned runs called a tool**, with {len(calls)} recorded calls, {successful_calls} completed successful search responses with self-consistent source hashes, and {errors} reported tool errors. {overridden_calls} calls changed at least one argument in the frozen default map. Availability is the assigned treatment; a run that did not use MCP remains in the comparison.",
        "Frozen defaults: `" + json.dumps(defaults, sort_keys=True) + "`. Queries are never counted as overrides. Explicit arguments absent from the frozen default map are listed separately, without guessing their defaults. H2O was not running; calls requesting reranking can fail and remain recorded.",
    ])
    if not calls:
        parts.append("**No MCP-assigned run used a search tool.** This is an availability pilot: observed time and token differences cannot be attributed to actual semantic retrieval. The study retains non-use as an outcome; it does not demonstrate an active MCP search benefit.")
    parts.append(markdown_table(["Task", "Condition", "Ordinary command calls", "File-change items", "MCP calls"], [[r["task_id"], r["condition"], r.get("command_calls", "unavailable"), r.get("file_change_items", "unavailable"), r.get("mcp_calls", 0)] for r in ordered_records]))
    parts.append("Ordinary command calls are completed shell-command items. File-change items count completed edit items in the preserved trace; a command may also edit files, so these columns describe recorded tool activity rather than a count of changed files.")
    if call_rows:
        parts.append(markdown_table(["Task", "Tool", "Supplied arguments", "Changed frozen defaults", "Arguments outside default map", "Tool error"], call_rows))
        parts.append(markdown_table(["Task", "Call", "Successful response", "Match status", "Local excerpt tokens", "Local budget", "Returned source excerpts", "Evidence"], receipt_rows))
        parts.append("These returned excerpts are preserved MCP response payloads, with no independently recorded HTTP receipt in the paired workflow runner. SHA256 checks above verify the returned source bytes are internally consistent with their reported hashes; they do not independently prove relevance or historical saved-file identity. Historical identity requires source reconstruction from the seeded tree and edit trace, while the hidden checks evaluate the final repair. Local excerpt tokens and budgets in this table are separate from actual provider input/output usage reported above.")
        if artifacts is not None and (artifacts / "audit.json").exists():
            parts.append("See the [independent artifact audit](audit.json) for recorded usage, trace and returned-source checks.")
        source_audit_path = artifacts / "mcp-source-audit.json" if artifacts is not None else None
        if source_audit_path is not None and source_audit_path.exists():
            source_audit = json.loads(source_audit_path.read_text())
            audited_calls = source_audit["calls"]
            observed_call_keys = {(record["run_id"], item.get("id")) for record, item in calls}
            audited_call_keys = {(row["run_id"], row["item_id"]) for row in audited_calls}
            if len(audited_call_keys) != len(audited_calls) or not audited_call_keys.issubset(observed_call_keys):
                raise ValueError("Source audit refers to duplicate or unobserved MCP calls")
            parts.append(
                f"The [independent MCP source audit](mcp-source-audit.json) covers **{len(audited_calls)}/{len(calls)} observed calls**; recorded audit passed=`{source_audit.get('passed')}`, "
                f"errors={len(source_audit.get('errors', []))}, source-freshness reviews needed={len(source_audit.get('source_freshness_review_needed', []))}."
            )
            parts.append(markdown_table([
                "Run", "Call", "No observed prior mutation", "Excerpts", "Valid source hashes", "Valid physical bounds", "Match seeded Git bytes",
            ], [[
                row["run_id"], row["item_id"], row["no_observed_mutation_before_call"], len(row["excerpts"]),
                sum(excerpt["valid_hash"] is True for excerpt in row["excerpts"]),
                sum(excerpt["valid_physical_bounds"] is True for excerpt in row["excerpts"]),
                sum(excerpt["matches_seeded_git_bytes"] is True for excerpt in row["excerpts"]),
            ] for row in audited_calls]))
            parts.append(source_audit["limits"])
            if source_audit.get("errors") or source_audit.get("source_freshness_review_needed"):
                parts.append("Source audit findings: `" + json.dumps({
                    "errors": source_audit.get("errors", []),
                    "source_freshness_review_needed": source_audit.get("source_freshness_review_needed", []),
                }, sort_keys=True) + "`.")
    backend_rows = []
    for record in mcp_records:
        status = record.get("final_backend_status")
        if status:
            stats = status.get("indexstats") or {}
            backend_rows.append([
                record["task_id"], status.get("ready"), status.get("dense_ready"),
                status.get("windows"), stats.get("source_files"), status.get("source_manifest"),
            ])
    if backend_rows:
        parts.append(markdown_table(["Task", "Final backend ready", "Dense index ready", "Windows", "Source files", "Final source manifest"], backend_rows))
        parts.append("These are final backend/index snapshots after agent execution, not per-call HTTP receipts or proof of the source state at an earlier query.")
    parts.append("The host's handling of MCP text and structured content is part of these observed input counts. No adjustment was made from local excerpt sizes. Cached input is shown separately because provider caching and paired execution order can affect observed workload and latency.")
    spawn_warning_runs = [r for r in ordered_records if r.get("runtime_observation", {}).get("failed_spawn_lines", 0)]
    if spawn_warning_runs:
        parts.append("## Observed CLI behavior")
        parts.append(
            "The frozen configuration requested `features.multi_agent=false` and `features.multi_agent_v2=false` in both conditions. Despite those intended settings, the alpha CLI's preserved stderr reports failed collaboration-spawn attempts in "
            + ", ".join(f"{sum(r['condition'] == condition for r in spawn_warning_runs)} {condition} trials" for condition in CONDITIONS)
            + ". The failures report “no rollout found” while loading a model context in these ephemeral runs. Related rollout and subagent-hook warnings are preserved. These are observed runtime warnings rather than evidence that the intended disable flags fully controlled behavior. Agent durations and reported usage retain their overhead. Ordinary-command and MCP counts cover completed recorded items; failed spawn attempts reported only in stderr are counted separately here."
        )
        observed_spawns = sum(r.get("runtime_observation", {}).get("successful_spawn_items", 0) for r in ordered_records)
        parts.append(
            "No successful subagent spawn is visible in the published event streams. This statement is limited to recorded observations."
            if not observed_spawns else
            f"The published event streams contain {observed_spawns} completed collaboration-spawn items; their implications require manual trace review."
        )
        parts.append(markdown_table([
            "Run", "Failed spawn stderr lines", "Rollout warning lines", "Subagent-hook warning lines", "Completed spawn items", "Stderr",
        ], [[
            r["run_id"], r.get("runtime_observation", {}).get("failed_spawn_lines", "unavailable"),
            r.get("runtime_observation", {}).get("rollout_warning_lines", "unavailable"),
            r.get("runtime_observation", {}).get("subagent_hook_warning_lines", "unavailable"),
            r.get("runtime_observation", {}).get("successful_spawn_items", "unavailable"),
            f"[stderr](runs/{r['run_id']}/stderr.txt)" if artifacts is not None and (artifacts / "runs" / r["run_id"] / "stderr.txt").exists() else "unavailable",
        ] for r in ordered_records]))
    parts.append("## Failures and preserved patches")
    failed_records = [r for r in ordered_records if not r.get("completed_correct") or r.get("infrastructure_error") or r.get("errors") or r.get("invalid_event_lines")]
    if not failed_records:
        parts.append("No trial had a failing hidden check, failed terminal turn, timeout, runner infrastructure error or invalid event line. Tool errors and runtime warnings, if any, are still listed above and preserved in the traces.")
    else:
        for record in failed_records:
            detail = {key: record.get(key) for key in (
                "correct", "completed", "completed_correct", "failed", "timed_out", "agent_exit_code",
                "infrastructure_error", "errors", "invalid_event_lines", "grade",
            ) if record.get(key) is not None}
            parts.append(f"- `{record['run_id']}`: `{json.dumps(detail, ensure_ascii=False, sort_keys=True)}`")
    artifact_rows = []
    for record in ordered_records:
        row = [record["run_id"]]
        for name, label in (("changes.patch", "patch"), ("final.txt", "final response"), ("events.jsonl", "events")):
            relative = f"runs/{record['run_id']}/{name}"
            row.append(f"[{label}]({relative})" if artifacts is not None and (artifacts / relative).exists() else "unavailable")
        artifact_rows.append(row)
    parts.append(markdown_table(["Run", "Patch", "Agent checks and outcome", "Tool trace"], artifact_rows))
    close_probe = artifacts / "close-error-probe.json" if artifacts is not None else None
    if close_probe is not None and close_probe.exists():
        diagnostic = json.loads(close_probe.read_text())
        diagnostic_rows = diagnostic["results"]
        different_repairs = [row for row in diagnostic_rows if row["source"] != "pinned_upstream"
                             and not row["observed"]["matches_pinned_upstream_error_behavior"]]
        parts.extend([
            "## Supplemental atomic-output error check",
            f"A separate CPU fault-injection probe found **{len(different_repairs)} agent repairs** whose error behavior differs from pinned upstream Click. The diagnostic removes the atomic temporary source after the callback writes and flushes, before cleanup commits the destination. Upstream raises `FileNotFoundError` and exits with status 1. A repair can suppress that failure and exit successfully while the original destination bytes remain. See [complete diagnostic evidence](close-error-probe.json).",
            ("This check was predefined for this follow-up from the original pilot's post-hoc patch-review finding. "
             if followup else "This check was selected after patch review. ")
            + "It is excluded from the frozen correctness labels and paired performance estimates. The declared atomic-output checks can all pass while this additional compatibility defect remains; selected-check success does not establish complete correctness.",
            markdown_table([
                "Source", "Frozen verifier passed", "CLI exit", "Exception", "Original destination retained", "Matches upstream error behavior",
            ], [[
                row["source"], row.get("declared_verifier_passed", "unavailable"),
                row["observed"]["cli_exit_code"], row["observed"]["actual_exception_type"],
                row["observed"]["destination_old_bytes_preserved"], row["observed"]["matches_pinned_upstream_error_behavior"],
            ] for row in diagnostic_rows]),
        ])
    patch_review = artifacts / "patch-review.json" if artifacts is not None else None
    if patch_review is not None and patch_review.exists():
        review = json.loads(patch_review.read_text())
        reviewed = {row["run_id"]: row for row in review["runs"]}
        if len(reviewed) != len(review["runs"]) or set(reviewed) != {r["run_id"] for r in records}:
            raise ValueError("Supplemental patch review inventory differs from measured trials")
        for record in records:
            if reviewed[record["run_id"]]["patch_sha256"] != record.get("patch_sha256"):
                raise ValueError(f"Patch review refers to a different patch: {record['run_id']}")
            for issue in reviewed[record["run_id"]].get("issues", []):
                evidence = issue.get("evidence", {})
                if evidence.get("path") and evidence.get("sha256"):
                    evidence_path = Path(__file__).resolve().parents[1] / evidence["path"]
                    actual_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
                    if actual_hash != evidence["sha256"]:
                        raise ValueError(f"Patch review evidence changed: {evidence['path']}")
        review_summary = review["summary"]
        parts.extend([
            "## Supplemental patch and regression review",
            "This qualitative QA and CPU reproduction were performed after the measured trials and are excluded from frozen correctness and performance scores. They compare submitted production changes with pinned upstream behavior, reproduce selected agent-authored regressions on final and restored seeded code, and check testing claims against recorded command output. See [complete patch review](patch-review.json).",
            f"All {review_summary['runs_reviewed']} patches were reviewed. The submitted fixes passed {review_summary['fixed_selected_regression_cases_passed']} selected regression cases; restoring the seeded production faults while retaining those tests produced {review_summary['seed_selected_regression_cases_failed']} expected failures and {review_summary['seed_control_cases_passed']} passing controls. Every selected test set detected its seeded fault. The review found {review_summary['unsupported_test_claims']} unsupported testing claims within the claims examined. These post-trial case counts are separate from the original hidden-check totals.",
            review_summary["interpretation"],
        ])
        for record in ordered_records:
            row = reviewed[record["run_id"]]
            for issue in row.get("issues", []):
                parts.append(f"- `{row['run_id']}` — {issue['behavior']} {issue.get('status', '')} {issue.get('comparative_impact', '')}".strip())
        parts.append(markdown_table(["Run", "Production and regression review", "Scope limits"], [[
            reviewed[record["run_id"]]["run_id"], reviewed[record["run_id"]]["summary"],
            " ".join(reviewed[record["run_id"]].get("limitations", [])),
        ] for record in ordered_records]))
    parts.extend([
        "## Practical limits",
        "The hidden checks establish selected observable behavior and preserve nearby negative controls; they do not prove every repair is idiomatic, every new agent-authored test is adequate, or every upstream behavior remains correct. Manual patch review should accompany broader claims. The tasks are deliberately small seeded repairs in previously evaluated repositories, with no independent task holdout and no repeated stochastic trials. Hosted-model waits, background machine activity, prompt caching and condition order can affect timing and tokens. Warm MCP setup was excluded from agent wall time but published separately. Fresh tasks and repeated runs are needed before claiming production savings or expanding the quality gate.",
    ])
    return "\n\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True, help="Directory containing all completed workflow artifacts")
    args = parser.parse_args()
    directory = args.results.resolve(strict=True)
    config = json.loads((directory / "frozen-config.json").read_text())
    records = json.loads((directory / "results.json").read_text())
    summary = json.loads((directory / "summary.json").read_text())
    # Derived only for this report; preserve the runner's measurements unchanged.
    for record in records:
        trace = directory / "runs" / record["run_id"] / "events.jsonl"
        if trace.exists():
            file_changes = set()
            successful_spawns = set()
            for index, line in enumerate(trace.read_text().splitlines()):
                event = json.loads(line)
                item = event.get("item", {})
                if event.get("type") == "item.completed" and item.get("type") == "file_change":
                    file_changes.add(item.get("id", f"event-{index}"))
                if (event.get("type") == "item.completed" and "collab" in item.get("type", "")
                        and "spawn" in str(item.get("tool", item.get("name", "")))
                        and item.get("status") == "completed" and not item.get("error")):
                    successful_spawns.add(item.get("id", f"event-{index}"))
            record["file_change_items"] = len(file_changes)
            record["runtime_observation"] = {"successful_spawn_items": len(successful_spawns)}
        stderr = directory / "runs" / record["run_id"] / "stderr.txt"
        if stderr.exists():
            lines = stderr.read_text().splitlines()
            record.setdefault("runtime_observation", {}).update({
                "failed_spawn_lines": sum("collab spawn failed" in line for line in lines),
                "rollout_warning_lines": sum("codex_rollout::list" in line for line in lines),
                "subagent_hook_warning_lines": sum("subagent hook" in line for line in lines),
            })
    report = render(config, records, summary, directory)
    target = directory / "report.md"
    target.write_text(report)
    print(target)


if __name__ == "__main__":
    main()
