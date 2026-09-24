#!/usr/bin/env python3
"""CPU-only inventory and paired measurements for the three-condition study.

Missing provider usage stays unknown. Paired subsets explicitly identify their
members and never turn a failed or interrupted attempt into a completed one.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
import math
import statistics

CONDITIONS = ("baseline", "mcp_fast", "mcp_qwen")
TASK_IDS = ("requests_response_limit", "click_atomic_transactions", "starlette_body_limits")
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                "output_tokens", "reasoning_output_tokens")


def schedule(tasks):
    ids = [task["id"] for task in tasks]
    if tuple(ids) != TASK_IDS:
        raise ValueError("The three predeclared task IDs and their order are required")
    rows = []
    for repetition in (1, 2):
        for index, task_id in enumerate(ids):
            order = CONDITIONS[index:] + CONDITIONS[:index]
            if repetition == 2:
                order = tuple(reversed(order))
            rows.extend({"task_id": task_id, "repetition": repetition,
                         "condition": condition} for condition in order)
    return rows


def provider_usage(events):
    """Sum reported completed turns only; validate subset counts and raw types."""
    turns = [event.get("usage") for event in events if event.get("type") == "turn.completed"]
    valid = [turn for turn in turns if isinstance(turn, dict)]
    errors = []
    for index, turn in enumerate(valid):
        for key in USAGE_FIELDS:
            value = turn.get(key)
            if value is not None and (type(value) is not int or value < 0):
                errors.append(f"turn {index}: invalid {key}")
        for subset, total in (("cached_input_tokens", "input_tokens"),
                              ("cache_write_input_tokens", "input_tokens"),
                              ("reasoning_output_tokens", "output_tokens")):
            a, b = turn.get(subset), turn.get(total)
            if type(a) is int and type(b) is int and a > b:
                errors.append(f"turn {index}: {subset} exceeds {total}")
    usage = None
    if valid and not errors:
        usage = {key: sum(turn[key] for turn in valid)
                 if len(valid) == len(turns) and all(type(turn.get(key)) is int for turn in valid)
                 else None for key in USAGE_FIELDS}
        a, b = usage["input_tokens"], usage["output_tokens"]
        usage["total_tokens"] = a + b if a is not None and b is not None else None
        c = usage["cached_input_tokens"]
        usage["uncached_input_tokens"] = a - c if a is not None and c is not None else None
    return {"usage": usage, "reported_turns": len(valid),
            "completed_turn_events": len(turns), "usage_errors": errors,
            "usage_scope": "Reported completed-turn usage; interrupted-turn and per-request usage unknown"}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def complete(row):
    return bool(row.get("completed") and row.get("agent_exit_code") == 0
                and not row.get("failed") and not row.get("timed_out")
                and not row.get("infrastructure_error"))


def measured(row, field):
    value = row.get("usage", {}).get(field) if isinstance(row.get("usage"), dict) else None
    if field in ("agent_elapsed_s", "full_trial_elapsed_s", "service_setup_s"):
        if row.get("timing_qualified") is not True:
            return None
        value = row.get(field)
    return value if finite(value) else None


def distribution(values):
    return {"observations": len(values), "sum": sum(values) if values else None,
            "median": statistics.median(values) if values else None}


def summarize(records, planned=None):
    planned = schedule([{"id": value} for value in TASK_IDS]) if planned is None else planned
    expected = {(row["task_id"], row["repetition"], row["condition"]) for row in planned}
    identities = [(row["task_id"], row["repetition"], row["condition"]) for row in records]
    if len(set(identities)) != len(identities) or not set(identities) <= expected:
        raise ValueError("Duplicate or unscheduled attempts cannot enter the inventory")
    fields = ("total_tokens", "uncached_input_tokens", "cached_input_tokens",
              "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens",
              "agent_elapsed_s", "service_setup_s", "full_trial_elapsed_s")
    result = {"planned_attempts": len(planned), "recorded_attempts": len(records),
              "inventory_complete": set(identities) == expected,
              "missing_attempts": [dict(zip(("task_id", "repetition", "condition"), key))
                                   for key in sorted(expected - set(identities))],
              "conditions": {}, "comparisons": []}
    for condition in CONDITIONS:
        rows = [row for row in records if row["condition"] == condition]
        measurements = {field: distribution([value for row in rows
                         if (value := measured(row, field)) is not None]) for field in fields}
        result["conditions"][condition] = {
            "planned": sum(row["condition"] == condition for row in planned),
            "recorded": len(rows), "normally_completed": sum(complete(row) for row in rows),
            "correct": sum(row.get("correct") is True for row in rows),
            "completed_correct": sum(complete(row) and row.get("correct") is True for row in rows),
            "timeouts": sum(row.get("timed_out") is True for row in rows),
            "infrastructure_errors": sum(bool(row.get("infrastructure_error")) for row in rows),
            "usage_error_runs": sum(bool(row.get("usage_errors")) for row in rows),
            "timing_unqualified_runs": [row.get("run_id", [row["task_id"], row["repetition"]])
                                         for row in rows if row.get("timing_qualified") is not True],
            "mcp_calls": sum(row.get("mcp_calls", 0) for row in rows),
            "successful_qwen_searches": sum(row.get("successful_qwen_searches", 0) for row in rows)
            if all(row.get("successful_qwen_searches", 0) is not None for row in rows) else None,
            "successful_qwen_searches_unknown_runs": sum(row.get("successful_qwen_searches", 0) is None for row in rows),
            "observed_native_qwen_forwards": sum(row.get("qwen_evidence", {}).get("observed_qwen_completed_forwards", 0)
                                                  for row in rows),
            "measurements": measurements,
            "complete_inventory_total_tokens": measurements["total_tokens"]["sum"]
            if len(rows) == 6 and measurements["total_tokens"]["observations"] == 6 else None,
        }
    grouped = {}
    for row in records:
        grouped.setdefault((row["task_id"], row["repetition"]), {})[row["condition"]] = row
    for left, right in combinations(CONDITIONS, 2):
        for subset in ("all_observed_pairs", "normally_completed_pairs", "completed_correct_pairs"):
            paired = []
            binding_exclusions = []
            for identity, members in sorted(grouped.items()):
                if left not in members or right not in members:
                    continue
                a, b = members[left], members[right]
                missing = [key for key in ("base_tree_sha256", "prompt_sha256")
                           if not a.get(key) or not b.get(key)]
                if missing:
                    binding_exclusions.append({"member": list(identity), "missing": missing})
                    continue
                for key in ("base_tree_sha256", "prompt_sha256"):
                    if a[key] != b[key]:
                        raise ValueError("Paired fixtures or prompts differ: " + key)
                if subset != "all_observed_pairs" and not (complete(a) and complete(b)):
                    continue
                if subset == "completed_correct_pairs" and not (a.get("correct") is True and b.get("correct") is True):
                    continue
                paired.append((identity, a, b))
            metrics = {}
            for field in fields:
                values = [(identity, measured(a, field), measured(b, field)) for identity, a, b in paired]
                values = [(identity, a, b) for identity, a, b in values if a is not None and b is not None]
                left_total = sum(a for _, a, _ in values) if values else None
                right_total = sum(b for _, _, b in values) if values else None
                ratios = [100 * (b / a - 1) for _, a, b in values if a > 0]
                metrics[field] = {"pairs": len(values), "members": [list(identity) for identity, _, _ in values],
                    "left_sum": left_total, "right_sum": right_total,
                    "aggregate_change_pct": 100 * (right_total / left_total - 1) if left_total else None,
                    "median_pair_change_pct": statistics.median(ratios) if ratios else None,
                    "ratio_pairs": len(ratios)}
            result["comparisons"].append({"left": left, "right": right, "subset": subset,
                "eligible_pairs": len(paired), "binding_exclusions": binding_exclusions,
                "measurements": metrics})
    result["condition_position_counts"] = {
        condition: dict(Counter(index % 3 + 1 for index, row in enumerate(planned)
                                if row["condition"] == condition)) for condition in CONDITIONS}
    return result
