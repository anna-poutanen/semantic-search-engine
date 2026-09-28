"""Mutations must fail independent accounting/source gates; no model/hosted calls."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("coding_independent_audit", ROOT / "scripts/audit_qwen_workflows.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def usage_event(**usage):
    return {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20,
            "cached_input_tokens": 60, "reasoning_output_tokens": 5, **usage}}


def test_real_gross_usage_does_not_add_subsets_and_partial_unknowns_are_retained():
    usage, turns, count, errors = audit.provider_usage([usage_event(), {"type": "turn.failed"}])
    assert usage["total_tokens"] == 120 and usage["uncached_input_tokens"] == 40
    assert turns == count == 1 and not errors
    assert audit.provider_usage([usage_event(), {"type": "turn.completed"}])[0]["total_tokens"] is None
    assert audit.provider_usage([{"type": "turn.failed"}])[0] is None


@pytest.mark.parametrize("values", [{"input_tokens": True}, {"output_tokens": -1}, {"cached_input_tokens": 101},
                                   {"reasoning_output_tokens": 21}, {"cache_write_input_tokens": 101}])
def test_malformed_actual_usage_is_unknown_and_preserved_as_errors(values):
    assert audit.provider_usage([usage_event(**values)])[0] is None
    assert audit.provider_usage([usage_event(**values)])[3]


def test_raw_duplicate_nonfinite_and_malformed_lines_are_not_repaired(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(usage_event()) + '\n{"type":"turn.failed","type":"turn.completed"}\n'
                    '{"score":1e999}\n[]\n')
    events, invalid = audit.raw_events(path)
    assert events == [usage_event()] and len(invalid) == 3


def sample(wall, perf):
    seconds, nanos = divmod(wall, 1_000_000_000)
    return {"wall_time_ns": wall, "perf_counter_ns": perf,
            "utc": datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=nanos // 1000).isoformat()}


def clock_fixture(drift=0):
    baseline = sample(1_700_000_000_000_000_000, 100)
    end = sample(baseline["wall_time_ns"] + 100 + drift, 200)
    delta = {"wall_elapsed_ns": 100 + drift, "perf_elapsed_ns": 100, "drift_ns": drift,
             "absolute_drift_ns": abs(drift), "max_drift_ns": 2_000_000_000,
             "consistent": abs(drift) <= 2_000_000_000}
    return {"version": 1, "label": "trial-end", "baseline": baseline, "sample": end,
            "cumulative": delta, "interval": deepcopy(delta), "max_drift_ns": 2_000_000_000,
            "consistent": delta["consistent"]}, baseline


@pytest.mark.parametrize("drift,valid", [(0, True), (2_000_000_000, True), (-2_000_000_000, True),
                                        (2_000_000_001, False), (-2_000_000_001, False)])
def test_integer_clock_gates_accept_exact_boundary_reject_any_excess(drift, valid):
    receipt, baseline = clock_fixture(drift)
    assert audit.clock_receipt(receipt, baseline, baseline) is valid


def test_clock_receipt_cannot_change_baseline_or_raw_delta_to_hide_sleep():
    receipt, baseline = clock_fixture(3_000_000_000)
    receipt["cumulative"]["drift_ns"] = 0
    with pytest.raises(ValueError, match="clock deltas"):
        audit.clock_receipt(receipt, baseline, baseline)
    receipt, baseline = clock_fixture()
    receipt["baseline"]["utc"] = "invented"
    with pytest.raises(ValueError, match="UTC"):
        audit.clock_receipt(receipt, deepcopy(baseline), baseline)


def pair(condition, tokens=120, timing=True, failed=False):
    return {"task_id": audit.TASK_IDS[0], "repetition": 1, "condition": condition,
            "completed": True, "agent_exit_code": 0, "failed": failed, "timed_out": False,
            "correct": True, "base_tree_sha256": "base", "prompt_sha256": "prompt",
            "timing_qualified": timing, "agent_elapsed_s": 2.0,
            "usage": {"total_tokens": tokens}}


def test_pairs_preserve_members_missing_usage_failures_and_invalid_timing():
    a, b = pair("baseline"), pair("mcp_qwen", tokens=None, timing=False, failed=True)
    outputs = audit.paired_measurements([a, b])
    observed = next(row for row in outputs if row["left"] == "baseline" and row["right"] == "mcp_qwen"
                    and row["subset"] == "all_observed_pairs")
    assert observed["eligible_pairs"] == 1
    assert observed["measurements"]["total_tokens"]["pairs"] == 0
    assert observed["measurements"]["agent_elapsed_s"]["pairs"] == 0
    normal = next(row for row in outputs if row["left"] == "baseline" and row["right"] == "mcp_qwen"
                  and row["subset"] == "normally_completed_pairs")
    assert normal["eligible_pairs"] == 0
    b["base_tree_sha256"] = "different"
    with pytest.raises(ValueError, match="Paired source"):
        audit.paired_measurements([a, b])


def test_balanced_full18_inventory_is_prospective_and_unique():
    schedule = audit.schedule()
    assert len(schedule) == 18 and len({tuple(sorted(row.items())) for row in schedule}) == 18
    for condition in audit.CONDITIONS:
        assert [sum(row["condition"] == condition for row in schedule[position::3]) for position in range(3)] == [2, 2, 2]


def test_offline_named_context_tokenizer_requires_exact_local_rank_bytes(tmp_path, monkeypatch):
    path = tmp_path / "ranks"
    path.write_text("substituted")
    monkeypatch.setattr(audit, "RANKS_PATH", path)
    with pytest.raises(ValueError, match="offline cl100k"):
        audit.offline_encoding()


def test_source_rows_caps_and_physical_bounds_checked_against_actual_snapshot(tmp_path):
    raw = tmp_path / "raw"
    text = "def f():\r\n    return 'x\u2028y'\n"
    metadata = {"files": {"src/a.py": audit.digest(text.encode())}, "git_revision": "base"}
    metadata["manifest_sha256"] = audit.digest(json.dumps(metadata["files"], sort_keys=True, separators=(",", ":")).encode())
    for phase in ("before", "after"):
        directory = raw / "snapshots/0001" / phase
        (directory / "src").mkdir(parents=True)
        (directory / "src/a.py").write_bytes(text.encode())
        (directory / "snapshot.json").write_text(json.dumps(metadata))
    row = {"path": "src/a.py", "symbol": "f", "start_line": 1, "end_line": 2,
           "source": text, "source_hash": audit.digest(text.encode()), "truncated": False}
    response = {"source_root": "src", "scope": {"source_root": "src"}, "query": "Return a value",
                "results": [row], "total_source_chars": len(text), "source_budget": len(text)}
    receipt = {"number": 1, "query": response["query"], "success": True,
               "snapshots": {phase: metadata for phase in ("before", "after")}, "response": response}
    receipt["compact_text_sha256"] = audit.digest(audit.compact(response).encode())
    assert audit.source_receipt(receipt, raw, "src", None)["source_rows"] == 1
    response["source_budget"] -= 1
    with pytest.raises(ValueError, match="character budget"):
        audit.source_receipt(receipt, raw, "src", None)
    response["source_budget"] += 1
    row["source"] = "def f():\r\n    ret"
    with pytest.raises(ValueError, match="snapshot differs"):
        audit.source_receipt(receipt, raw, "src", None)


def test_native_input_hash_is_from_exact_whole_physical_line_prefix():
    tokenizer = SimpleNamespace(encode=lambda text, **kwargs: SimpleNamespace(ids=list(text.encode())))
    constants = {"PREFIX": "P", "SUFFIX": "S", "INSTRUCTION": "I", "MAX_TOKENS": 70}
    source = "return 1\n" + "x" * 100 + "\n"
    result = audit.native_inputs("Q", source, tokenizer, constants)
    expected = list(("P<Instruct>: I\n<Query>: Q\n<Document>: return 1\nS").encode())
    assert result["input_ids_sha256"] == audit.digest(json.dumps(expected, separators=(",", ":")).encode())
    assert result["delivered_sha256"] == audit.digest(b"return 1\n")
    assert result["source_sha256"] == audit.digest(source.encode())


def test_recorded_strict_projection_keeps_actual_oracle_identity_and_types():
    detail = {"version": 1, "task_id": "t", "passed": True, "model_calls": 0, "tests_run": 1,
              "failures": 0, "errors": 0, "skipped": 0, "components": [{"script": "probe.py", "script_sha256": "sha",
              "result": {"version": 3, "probe_sha256": "sha", "cases": {"case": {"passed": True}}}}]}
    value = audit.projection(detail)
    assert value["components"][0]["cases"] == {"case": True}
    detail["tests_run"] = True
    assert audit.projection(detail) is None


def test_unknown_or_false_completion_cannot_enter_normal_subsets():
    record = pair("baseline")
    assert audit.completed(record)
    for key, value in (("failed", True), ("timed_out", True), ("agent_exit_code", 8), ("completed", False)):
        changed = deepcopy(record)
        changed[key] = value
        assert not audit.completed(changed)


@pytest.mark.parametrize("task_id,control,expected", [
    ("requests_response_limit", "strict-reference", True),
    ("click_atomic_transactions", "strict-reference", True),
    ("requests_response_limit", "original-reference", False),
    ("click_atomic_transactions", "original-reference", False),
])
def test_strict_grade_reconstructs_actual_control_receipts_including_old_reference_defects(task_id, control, expected):
    task = next(task for task in audit.read(ROOT / "benchmarks/coding_workflows_strict.json")["tasks"] if task["id"] == task_id)
    stdout = (ROOT / "results/strict-coding-preflight-2026-10-14/final-controls" / task_id / control / "strict.stdout.txt").read_text()
    detail = audit.decode(stdout)
    grade = {"stdout": stdout, "detail": detail, "projection": audit.projection(detail), "exit_code": 0 if detail["passed"] else 1,
             "passed": expected}
    assert audit.strict_grade(grade, task) is expected
    # An optimistic summary cannot conceal the real supplement cases.
    grade["detail"] = deepcopy(detail)
    grade["detail"]["failures"] += 1
    grade["stdout"] = json.dumps(grade["detail"])
    grade["projection"] = audit.projection(grade["detail"])
    with pytest.raises(ValueError, match="aggregate"):
        audit.strict_grade(grade, task)


def test_failed_setup_is_kept_without_fake_cli_usage_or_success(tmp_path):
    run_id = "01-r1-requests_response_limit-baseline"
    study, work = tmp_path / "study", tmp_path / "work"
    public = study / "runs" / run_id
    public.mkdir(parents=True)
    (work / "raw" / run_id).mkdir(parents=True)
    record = {"run_id": run_id, "task_id": "requests_response_limit", "condition": "baseline", "repetition": 1,
              "correct": False, "completed_correct": False, "completed": False, "failed": True, "usage": None,
              "mcp_calls": 0, "successful_qwen_searches": 0, "timing_qualified": True, "lifecycle_qualified": False}
    (public / "result.json").write_text(json.dumps(record))
    result = audit.audit_trial(record, {"id": record["task_id"], "source_root": "src"},
                               {"work_root": str(work), "python": sys.executable}, study, None, None)
    assert result["integrity_passed"] and result["failed"] and result["provider_usage"] is None
    assert not result["normally_completed"] and not result["completed_correct"]


def test_real_offline_context_counter_has_no_download_or_live_production_import():
    encoder = audit.offline_encoding()
    text = '{"query":"Return a value","results":[]}'
    assert encoder.name == "cl100k_base" and len(encoder.encode(text, disallowed_special=())) > 0


def test_normal_cli_phase_clock_receipts_cannot_be_omitted_or_relabelled():
    receipt, baseline = clock_fixture()
    start = deepcopy(receipt)
    start.update(label="trial-start", interval=None)
    agent_start = deepcopy(start)
    agent_start["label"] = "agent-start"
    agent_end = deepcopy(receipt)
    agent_end["label"] = "agent-end"
    # Same-time test samples require a zero within interval at agent end.
    zero = {"wall_elapsed_ns": 0, "perf_elapsed_ns": 0, "drift_ns": 0, "absolute_drift_ns": 0,
            "max_drift_ns": 2_000_000_000, "consistent": True}
    agent_end["interval"] = zero
    end = deepcopy(agent_end)
    end["label"] = "trial-end"
    row = {**pair("baseline"), "clock_start": start, "clock_agent_start": agent_start,
           "clock_agent_end": agent_end, "clock_end": end}
    assert audit.trial_clocks(row, baseline, True) == (True, [])
    for key in ("clock_start", "clock_agent_start", "clock_agent_end", "clock_end"):
        changed = deepcopy(row)
        del changed[key]
        with pytest.raises(ValueError, match="missing scheduled clock"):
            audit.trial_clocks(changed, baseline, True)
    changed = deepcopy(row)
    changed["clock_agent_start"]["label"] = "trial-start"
    with pytest.raises(ValueError, match="label"):
        audit.trial_clocks(changed, baseline, True)


def test_missing_interrupted_phase_clock_is_retained_as_unknown_timing():
    row = {**pair("baseline", failed=True), "interrupted": True, "timing_qualified": False}
    qualified, missing = audit.trial_clocks(row, None, True)
    assert qualified is False and len(missing) == 4


def test_native_startup_failure_preserves_warmup_attempts_before_ready(tmp_path):
    directory = tmp_path / "raw/qwen/owned-failed"
    directory.mkdir(parents=True)
    (directory / "forwards.jsonl").write_text(json.dumps({"request_id": "generic-warmup", "warmup": True, "event": "started"}) + '\n'
        + json.dumps({"request_id": "generic-warmup", "warmup": True, "event": "failed"}) + '\n')
    record = {"condition": "mcp_qwen", "failed": True, "completed_correct": False, "qwen_final_receipts": {}}
    result = audit.native_review(record, [], tmp_path / "raw", tmp_path / "public", {})
    assert result["startup_failed"] and result["warmup"] == 1 and result["warmup_completed"] == 0
    assert result["native_events"] == 2 and result["counts_complete"] is False
    assert "lower bounds" in result["count_scope"]


def test_missing_native_startup_trace_stays_lower_bound_unknown(tmp_path):
    record = {"condition": "mcp_qwen", "failed": True, "completed_correct": False, "qwen_final_receipts": {}}
    result = audit.native_review(record, [], tmp_path / "raw", tmp_path / "public", {})
    assert result["counts_complete"] is False and result["worker_stop_counts"] is None
