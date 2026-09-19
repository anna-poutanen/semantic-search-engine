"""Provider accounting and failure-inventory regressions; no hosted/model calls."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/qwen_workflow_evidence.py"
spec = importlib.util.spec_from_file_location("qwen_workflow_evidence", SCRIPT)
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def row(condition, *, total=100, correct=True, completed=True, timeout=False, **extra):
    return {"task_id": evidence.TASK_IDS[0], "repetition": 1, "condition": condition,
            "correct": correct, "completed": completed, "failed": False,
            "agent_exit_code": 0 if completed else -15, "timed_out": timeout,
            "usage": {"total_tokens": total, "uncached_input_tokens": total / 2}
            if total is not None else None, "agent_elapsed_s": 10,
            "timing_qualified": True,
            "base_tree_sha256": "source", "prompt_sha256": "prompt", **extra}


def comparison(result, subset="all_observed_pairs", right="mcp_qwen"):
    return next(item for item in result["comparisons"]
                if item["left"] == "baseline" and item["right"] == right and item["subset"] == subset)


def test_gross_usage_does_not_add_cached_or_reasoning_subsets():
    result = evidence.provider_usage([{"type": "turn.completed", "usage": {
        "input_tokens": 100, "cached_input_tokens": 70, "cache_write_input_tokens": 20,
        "output_tokens": 30, "reasoning_output_tokens": 25}}])
    assert result["usage"]["total_tokens"] == 130
    assert result["usage"]["uncached_input_tokens"] == 30
    assert result["usage"]["reasoning_output_tokens"] == 25


def test_only_reported_completed_turns_enter_usage():
    result = evidence.provider_usage([
        {"type": "turn.failed", "usage": {"input_tokens": 999, "output_tokens": 999}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 3}}])
    assert result["usage"]["total_tokens"] == 35
    assert result["usage"]["cached_input_tokens"] is None
    assert result["reported_turns"] == 2


def test_missing_turn_receipt_prevents_complete_usage_total():
    result = evidence.provider_usage([
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"type": "turn.completed"}])
    assert result["usage"]["total_tokens"] is None
    assert result["completed_turn_events"] == 2
    assert evidence.provider_usage([{"type": "turn.failed"}])["usage"] is None


@pytest.mark.parametrize("usage", [
    {"input_tokens": True, "output_tokens": 1},
    {"input_tokens": -1, "output_tokens": 1},
    {"input_tokens": 10, "output_tokens": 1, "cached_input_tokens": 11},
    {"input_tokens": 10, "output_tokens": 1, "reasoning_output_tokens": 2},
    {"input_tokens": 10, "output_tokens": 1, "cache_write_input_tokens": 11},
])
def test_invalid_provider_values_are_retained_as_errors_without_usable_total(usage):
    result = evidence.provider_usage([{"type": "turn.completed", "usage": usage}])
    assert result["usage"] is None
    assert result["usage_errors"]


def test_schedule_balances_every_condition_position_and_reverses_orders():
    planned = evidence.schedule([{"id": task} for task in evidence.TASK_IDS])
    assert len(planned) == 18
    result = evidence.summarize([])
    assert all(counts == {1: 2, 2: 2, 3: 2}
               for counts in result["condition_position_counts"].values())
    for task in evidence.TASK_IDS:
        orders = [[item["condition"] for item in planned
                   if item["task_id"] == task and item["repetition"] == repetition]
                  for repetition in (1, 2)]
        assert orders[1] == list(reversed(orders[0]))


def test_timeout_with_correct_patch_stays_outside_completed_subsets():
    result = evidence.summarize([row("baseline"),
        row("mcp_qwen", total=120, completed=False, timeout=True)])
    assert comparison(result)["measurements"]["total_tokens"]["aggregate_change_pct"] == pytest.approx(20)
    assert comparison(result, "normally_completed_pairs")["eligible_pairs"] == 0
    assert result["conditions"]["mcp_qwen"]["correct"] == 1
    assert result["conditions"]["mcp_qwen"]["completed_correct"] == 0
    assert result["conditions"]["mcp_qwen"]["timeouts"] == 1


def test_missing_usage_does_not_become_zero_or_exclude_measured_time_pair():
    result = evidence.summarize([row("baseline"), row("mcp_qwen", total=None)])
    metrics = comparison(result)["measurements"]
    assert metrics["total_tokens"]["pairs"] == 0
    assert metrics["total_tokens"]["right_sum"] is None
    assert metrics["agent_elapsed_s"]["pairs"] == 1
    assert result["conditions"]["mcp_qwen"]["complete_inventory_total_tokens"] is None


def test_capacity_error_after_completed_event_is_not_normal_completion():
    result = evidence.summarize([row("baseline"),
        row("mcp_qwen", infrastructure_error="capacity exhausted")])
    assert comparison(result, "normally_completed_pairs")["eligible_pairs"] == 0
    assert result["conditions"]["mcp_qwen"]["infrastructure_errors"] == 1


def test_completed_wrong_patch_keeps_usage_but_not_correct_subset():
    result = evidence.summarize([row("baseline"), row("mcp_qwen", correct=False, total=150)])
    assert comparison(result, "normally_completed_pairs")["eligible_pairs"] == 1
    assert comparison(result, "completed_correct_pairs")["eligible_pairs"] == 0
    assert comparison(result)["measurements"]["total_tokens"]["members"] == [[evidence.TASK_IDS[0], 1]]


@pytest.mark.parametrize("key", ["base_tree_sha256", "prompt_sha256"])
def test_paired_source_or_prompt_drift_is_rejected(key):
    with pytest.raises(ValueError, match="Paired"):
        evidence.summarize([row("baseline"), row("mcp_qwen", **{key: "changed"})])


@pytest.mark.parametrize("key", ["base_tree_sha256", "prompt_sha256"])
def test_missing_pair_binding_excludes_comparison_and_preserves_failed_inventory(key):
    result = evidence.summarize([row("baseline"), row("mcp_qwen", **{key: None})])
    paired = comparison(result)
    assert paired["eligible_pairs"] == 0
    assert paired["binding_exclusions"][0]["missing"] == [key]
    assert result["recorded_attempts"] == 2


def test_clock_invalid_time_is_preserved_but_excluded_from_comparison():
    result = evidence.summarize([row("baseline"), row("mcp_qwen", timing_qualified=False)])
    metrics = comparison(result)["measurements"]
    assert metrics["agent_elapsed_s"]["pairs"] == 0
    assert metrics["total_tokens"]["pairs"] == 1
    assert result["conditions"]["mcp_qwen"]["timing_unqualified_runs"]


def test_duplicate_and_unscheduled_attempts_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        evidence.summarize([row("baseline"), row("baseline")])
    with pytest.raises(ValueError, match="unscheduled"):
        evidence.summarize([row("other")])


def test_partial_inventory_is_explicit_and_complete_total_requires_all_six_receipts():
    planned = evidence.schedule([{"id": task} for task in evidence.TASK_IDS])
    rows = [{**row(item["condition"]), **item} for item in planned]
    result = evidence.summarize(rows)
    assert result["inventory_complete"] is True
    assert result["conditions"]["mcp_qwen"]["complete_inventory_total_tokens"] == 600
    rows[-1]["usage"] = None
    assert evidence.summarize(rows)["conditions"][rows[-1]["condition"]]["complete_inventory_total_tokens"] is None
    assert len(evidence.summarize(rows[:-1])["missing_attempts"]) == 1
