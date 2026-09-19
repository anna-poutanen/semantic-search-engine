"""Independent CPU receipt/parity corruption tests; no producer/model imports."""
from copy import deepcopy
import importlib.util
import math
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "independent_qwen_optimization_audit", Path(__file__).parents[1] / "scripts/audit_qwen_optimization.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class Tokenizer:
    def encode(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        return SimpleNamespace(ids=list(text.encode("utf-8")))


CONFIG = {"PREFIX": "P", "SUFFIX": "S", "INSTRUCTION": "I", "MAX_TOKENS": 8192}


def scalar(value):
    return {"yes_logit": value, "no_logit": 0., "logit_difference": value,
            "yes_rank_signal": 1 / (1 + math.exp(-value)) if value >= 0
            else math.exp(value) / (1 + math.exp(value))}


def fixture(size=2):
    pairs = [{"query": "Find behavior", "source": text} for text in ["long source\r\n", "x\n", "middle\n"]]
    built = [audit.built_pair(pair["query"], pair["source"], Tokenizer(), CONFIG) for pair in pairs]
    # Explicit manually authored fixture grouping, independently of producer.
    groups = [[0], [1], [2]] if size == 1 else [[1, 2], [0]]
    counts = audit.zero_counts()
    counts["requests"] = 1
    events, completed, results = [], [], [None] * len(pairs)
    for number, group in enumerate(groups):
        layout = audit.expected_layout(built, group)
        identities = []
        scores = []
        for index in group:
            item = built[index]
            identities.append({"pair_index": index,
                **{key: item[key] for key in ("query_sha256", "source_sha256", "delivered_sha256",
                                             "total_tokens", "truncated", "delivered_lines")},
                "input_ids_sha256": audit.sha(audit.canonical(item["input_ids"]))})
            scores.append(scalar(float(index + 1)))
            results[index] = {key: value for key, value in item.items() if key != "input_ids"} | scores[-1]
        counts["attempted_forwards"] += 1
        counts["attempted_batch_forwards"] += 1
        counts["completed_forwards"] += 1
        counts["completed_batch_forwards"] += 1
        counts["attempted_pairs"] += len(group)
        counts["completed_pairs"] += len(group)
        counts["completed_real_input_tokens"] += layout["real_input_tokens"]
        counts["completed_allocated_input_tokens"] += layout["allocated_input_tokens"]
        counts["completed_padding_tokens"] += layout["padding_tokens"]
        identity = {"request_id": "actual", "warmup": False, "batch_index": number,
                    "batch_forward_number": number + 1, "batch_size_requested": size,
                    "max_padded_tokens": 16384, "pairs": identities, "layout": layout}
        finished = identity | {"event": "completed", "elapsed_s": .1, "scores": scores}
        events.extend([identity | {"event": "started"}, finished])
        completed.append(finished)
    receipt = {"request_id": "actual", "warmup": False, "pairs_sha256": audit.sha(audit.canonical(pairs)),
               "batch_size": size, "batch_size_requested": size, "max_padded_tokens": 16384,
               "batches": completed, "batch_receipts": completed, "results": results, "counts": counts,
               "forward_s": .1 * len(groups), "tokenization_s": .01, "batch_preparation_s": .01,
               "server_scoring_s": .1 * len(groups) + .03}
    return pairs, receipt, events


def verify(pairs, receipt, events, size=2):
    return audit.verify_batch_receipt(receipt, pairs, events, Tokenizer(), CONFIG, request_id="actual",
                                      batch_size=size, previous_counts=audit.zero_counts())


@pytest.mark.parametrize("size", [1, 2])
def test_real_pair_and_native_forward_counts_are_distinct_and_restore_original_scores(size):
    pairs, receipt, events = fixture(size)
    before = deepcopy((pairs, receipt, events))
    counts, built = verify(pairs, receipt, events, size)
    assert counts["completed_pairs"] == 3
    assert counts["completed_batch_forwards"] == (3 if size == 1 else 2)
    assert [item["source_sha256"] for item in built] == [audit.sha(pair["source"].encode()) for pair in pairs]
    assert (pairs, receipt, events) == before


@pytest.mark.parametrize("change", ["query", "source", "extra_input", "digest", "config", "float_config", "warmup",
                                   "started", "missing_event", "forward_number", "float_number", "original_indexes",
                                   "token_hash", "prefix_hash", "causal", "padding", "position", "last",
                                   "width", "real_tokens", "padding_ratio", "singleton", "boolean_layout",
                                   "missing_score", "score_nan", "score_math", "score_order", "result_bytes",
                                   "result_boolean", "count_pairs", "count_forwards", "count_padding",
                                   "count_boolean", "sum", "interval", "negative", "alias", "extra_receipt"])
def test_corrupt_batch_identity_layout_actual_scalar_or_counter_is_rejected(change):
    pairs, receipt, events = fixture()
    if change in ("query", "source"):
        pairs[0][change] += "tamper"
    elif change == "extra_input":
        pairs[0]["gold"] = "forbidden"
    elif change == "digest":
        receipt["pairs_sha256"] = "0" * 64
    elif change == "config":
        receipt["batch_size"] = 4
    elif change == "float_config":
        receipt["batch_size"] = 2.
    elif change == "warmup":
        receipt["warmup"] = True
    elif change == "started":
        events[0]["event"] = "completed"
    elif change == "missing_event":
        events.pop()
    elif change == "forward_number":
        events[0]["batch_forward_number"] = 2
    elif change == "float_number":
        events[0]["batch_index"] = 0.
    elif change in ("original_indexes", "token_hash", "prefix_hash"):
        key = {"original_indexes": "pair_index", "token_hash": "input_ids_sha256", "prefix_hash": "delivered_sha256"}[change]
        events[0]["pairs"][0][key] = 0 if key == "pair_index" else "0" * 64
    elif change in ("causal", "padding", "position", "last", "width", "real_tokens", "padding_ratio", "singleton", "boolean_layout"):
        key, value = {"causal": ("attention_mask", "all"), "padding": ("padding_side", "left"),
                      "position": ("position_offset", 1), "last": ("last_token_positions", [0, 0]),
                      "width": ("padded_sequence_length", 1), "real_tokens": ("real_input_tokens", 999),
                      "padding_ratio": ("padding_ratio", math.nan), "singleton": ("singleton_original_path", True),
                      "boolean_layout": ("position_offset", False)}[change]
        events[0]["layout"][key] = value
    elif change == "missing_score":
        receipt["batches"][0]["scores"].pop()
    elif change == "score_nan":
        receipt["batches"][0]["scores"][0]["yes_logit"] = math.nan
    elif change == "score_math":
        receipt["batches"][0]["scores"][0]["logit_difference"] = 77.
    elif change == "score_order":
        receipt["results"][0], receipt["results"][1] = receipt["results"][1], receipt["results"][0]
    elif change == "result_bytes":
        receipt["results"][0]["source_utf8_bytes"] += 1
    elif change == "result_boolean":
        receipt["results"][0]["truncated"] = 0
    elif change in ("count_pairs", "count_forwards", "count_padding", "count_boolean"):
        key = {"count_pairs": "completed_pairs", "count_forwards": "completed_batch_forwards",
               "count_padding": "completed_padding_tokens", "count_boolean": "requests"}[change]
        receipt["counts"][key] = True if change == "count_boolean" else receipt["counts"][key] + 1
    elif change == "sum":
        receipt["forward_s"] += 1.
    elif change == "interval":
        receipt["server_scoring_s"] = .1
    elif change == "negative":
        receipt["tokenization_s"] = -1.
    elif change == "alias":
        receipt["batch_receipts"] = []
    elif change == "extra_receipt":
        receipt["batches"][0]["cache_reused"] = True
    with pytest.raises((ValueError, KeyError)):
        verify(pairs, receipt, events)


def test_original_literal_unicode_physical_prefix_and_query_cap_are_rebuilt_without_decode():
    config = CONFIG | {"MAX_TOKENS": 80}
    source = "a\r\n literal\n" + "long" * 30 + "\n"
    built = audit.built_pair("q", source, Tokenizer(), config)
    assert built["source_lines"] == 3 and built["delivered_lines"] == 2
    assert built["delivered_sha256"] == audit.sha("a\r\n literal\n".encode())
    assert built["truncated"] is True
    with pytest.raises(ValueError, match="No complete"):
        audit.built_pair("q" * 90, "a\n", Tokenizer(), config)


def test_stable_length_groups_keep_oversized_pairs_and_split_at_slot_budget():
    built = [{"input_ids": [1] * count} for count in (8192, 2, 2, 8192, 8192)]
    assert audit.expected_groups(built, 8, 16384) == [[1, 2], [0, 3], [4]]
    assert audit.expected_groups(built, 1, 16384) == [[0], [1], [2], [3], [4]]
    with pytest.raises(ValueError, match="caps"):
        audit.expected_groups(built, 8, 1000)


def parity_result(value, index):
    return {"source_sha256": str(index), "input_ids_sha256": str(index)} | scalar(value)


def test_near_tie_inversions_and_top_five_changes_are_retained_without_failing():
    reference = [parity_result(value, index) for index, value in enumerate((10., 8., 6., 4., 2., 2.01))]
    sample = deepcopy(reference)
    sample[4].update(scalar(2.02))
    result = audit.parity(reference, sample)
    assert result["qualified"] is True
    assert result["rank_inversions"] and result["top_five_membership_changed"]
    assert result["top_five_outside_near_tie_violations"] == []


@pytest.mark.parametrize("change", ["yes", "difference", "signal", "metadata", "nonfinite"])
def test_failed_parity_is_preserved_or_invalid_input_rejected(change):
    reference = [parity_result(0., 0), parity_result(1., 1)]
    sample = deepcopy(reference)
    if change == "yes":
        sample[0] |= scalar(.063)
    elif change == "difference":
        sample[0] |= scalar(.126)
    elif change == "signal":
        sample[0] |= scalar(.101)
    elif change == "metadata":
        sample[0]["source_sha256"] = "other"
    else:
        sample[0]["yes_logit"] = math.inf
    if change in ("metadata", "nonfinite"):
        with pytest.raises(ValueError):
            audit.parity(reference, sample)
    else:
        result = audit.parity(reference, sample)
        assert result["qualified"] is False and result["scalar_violations"]


def test_inversion_with_strong_reference_margin_fails_even_if_scalar_gate_also_fails():
    reference = [parity_result(.3, 0), parity_result(0., 1)]
    sample = [parity_result(-.1, 0), parity_result(.1, 1)]
    result = audit.parity(reference, sample)
    assert result["qualified"] is False
    assert result["rank_inversions"][0]["outside_near_tie_band"] is True


def case(identifier, repetition, ranks, kind="present"):
    return {"dataset": "repo", "query_id": identifier, "repetition": repetition,
            "kind": kind, "multi_file": len(ranks) > 1, "evidence_ranks": ranks,
            "match_status": "candidates", "elapsed_s": float(repetition)}


def test_quality_uses_first_pass_and_warm_latency_uses_every_actual_observation():
    cases = [case("positive", 1, [1, None]), case("absent", 1, [], "absent"),
             case("positive", 2, [1, 2]), case("absent", 2, [], "absent")]
    metric = audit.quality(cases)
    assert metric["complete"] == 0 and metric["covered_groups"] == 1
    assert metric["multi_file_complete"] == 0
    assert metric["absent_statuses"] == {"candidates": 1}
    assert metric["warm_latency"]["samples"] == 4
    with pytest.raises(ValueError, match="Unique"):
        audit.quality(cases + [cases[0]])


def test_latency_p95_is_nearest_rank_and_not_interpolated():
    assert audit.distribution(list(range(1, 21)))["p95_s"] == 19
    with pytest.raises(ValueError):
        audit.distribution([False])


def test_signal_tolerance_can_fail_when_both_logits_and_difference_pass():
    reference = [parity_result(0., 0)]
    sample = deepcopy(reference)
    delta = .11
    sample[0].update(yes_logit=.055, no_logit=-.055, logit_difference=delta,
                     yes_rank_signal=1 / (1 + math.exp(-delta)))
    result = audit.parity(reference, sample)
    assert result["absolute_error_max"]["yes_logit"] < audit.LOGIT_TOLERANCE
    assert result["absolute_error_max"]["no_logit"] < audit.LOGIT_TOLERANCE
    assert result["absolute_error_max"]["logit_difference"] < audit.DIFFERENCE_TOLERANCE
    assert result["absolute_error_max"]["yes_rank_signal"] > audit.SIGNAL_TOLERANCE
    assert result["qualified"] is False


@pytest.mark.parametrize("value", [
    {"id": "dense", "retrieval": "dense", "shortlist": 5, "batch_size": 0},
    {"id": "dense_k24_b2", "retrieval": "dense", "shortlist": 32, "batch_size": 2},
    {"id": "dense_k24_b2", "retrieval": "dense", "shortlist": 24, "batch_size": 2.0},
    {"id": "hybrid_k24_b1", "retrieval": "hybrid", "shortlist": 24, "batch_size": True},
])
def test_config_identity_depth_baseline_or_boolean_change_rejected(value):
    with pytest.raises(ValueError):
        audit.configuration(value)


def test_baseline_comparisons_preserve_complete_regressions_and_partial_group_changes():
    before = [case("gain", 1, [None]), case("loss", 1, [1, 2]), case("partial", 1, [None, 3]),
              case("absent", 1, [], "absent"), case("loss", 2, [1, 2])]
    after = [case("gain", 1, [4]), case("loss", 1, [None, 2]), case("partial", 1, [None, None]),
             case("absent", 1, [], "absent"), case("loss", 2, [1, 2])]
    change = audit.changes(before, after)
    assert [item["query_id"] for item in change["complete_gains"]] == ["gain"]
    assert [item["query_id"] for item in change["complete_regressions"]] == ["loss"]
    assert [item["query_id"] for item in change["group_changes"]] == ["gain", "loss", "partial"]


def test_import_of_independent_auditor_cannot_import_native_model_or_producer(tmp_path):
    import subprocess
    import sys
    path = Path(__file__).parents[1] / "scripts/audit_qwen_optimization.py"
    code = (f"import runpy,sys; runpy.run_path({str(path)!r}); "
            "assert 'mlx.core' not in sys.modules and 'mlx_lm' not in sys.modules; "
            "assert 'qwen_reranker_batch' not in sys.modules; "
            "assert 'run_qwen_optimization' not in sys.modules")
    subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path, capture_output=True, check=True)


def test_superseded_frozen_producer_needs_matching_archive_and_committed_bytes(tmp_path, monkeypatch):
    import gzip
    import json
    root, study = tmp_path / "root", tmp_path / "root/results/study"
    (root / "scripts").mkdir(parents=True)
    study.mkdir(parents=True)
    old, current = b"original frozen producer", root / "scripts/producer.py"
    current.write_bytes(b"later corrected producer")
    path = study / "frozen-sources/scripts/producer.py.gz"
    path.parent.mkdir(parents=True)
    with gzip.open(path, "wb") as stream:
        stream.write(old)
    plan = {"version": 1, "stage": "parity", "settings": audit.original.evidence.CAPS,
            "repetitions": 3, "primary_quality_repetition": 1, "query_cache": False,
            "score_cache": False, "weights_loaded_in_freeze": False, "model_calls_in_freeze": 0,
            "batch_sizes": [1, 2, 4, 8], "max_padded_tokens": 16384,
            "generic_reranker_warmup_pairs": 32, "generic_reranker_warmup_forwards": 15,
            "parity_limits": {"yes_logit": .0625, "no_logit": .0625, "logit_difference": .125,
                              "yes_rank_signal": .025, "inversion_margin": .25},
            "reranker": {"supporting_sources": audit.SUPPORTING_RUNTIME_HASHES},
            "frozen_at_utc": "2026-10-13T20:00:00+00:00", "source_commit": "frozen-commit",
            "implementation_sha256": {"scripts/producer.py": audit.sha(old)}, "semantic_module_origins": {},
            "decision_inputs": {}, "packages": {}, "conditions": [
                {"id": "dense", "retrieval": "dense", "shortlist": 50, "batch_size": 0}]}
    (study / "frozen-plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(audit.subprocess, "check_output", lambda *args, **kwargs: old)
    monkeypatch.setattr(audit.importlib.metadata, "distributions", lambda: [])
    assert audit.verify_frozen_plan(root, study) == plan
    with gzip.open(path, "wb") as stream:
        stream.write(b"tampered archive")
    with pytest.raises(ValueError, match="archived"):
        audit.verify_frozen_plan(root, study)


def failed_supervisor_fixture(tmp_path):
    import json
    root, study = tmp_path / "root", tmp_path / "root/results/study"
    study.mkdir(parents=True)
    (study / "frozen-plan.json").write_text("frozen")
    owner_sha = "a" * 64
    plan = {"frozen_at_utc": "2026-10-13T20:00:00+00:00",
            "implementation_sha256": {"scripts/supervise_qwen_optimization.py": owner_sha}}
    supervisor = {"success": False, "supervisor_exit_code": 1,
                  "frozen_plan_sha256": audit.sha(b"frozen"), "supervisor_sha256": owner_sha,
                  "owned_sessions": True, "main_exit_code": 0, "main_owned_group_stopped": True,
                  "forced_kill": False, "timed_out": False, "main_pid": 10, "resident_pid": 20,
                  "caffeinate_pid": 30, "finished_utc": "2026-10-13T20:00:03+00:00",
                  "resident_stop": {"errors": [{"error_type": "PermissionError", "error": "Operation not permitted"}]}}
    stop = audit.zero_counts() | {"clean_stop": True, "listener_closed": True,
                                "mlx_active_memory_after_cleanup": 8, "mlx_process_lifetime_peak_bytes": 1000,
                                "memory_scope": "MLX allocator"}
    observed = {"model_calls": 0, "owned_groups_in_ps": [], "listener_closed": True, "token_absent": True,
                "observed_at_utc": "2026-10-13T20:00:04+00:00", "owned_process_checks": {
                    str(pid): {"getpgid": {"present": False}, "group_signal_zero": {"result": "ProcessLookupError", "errno": 3}}
                    for pid in (10, 20, 30)}}
    values = {"supervisor.json": supervisor, "reranker-stop.json": stop, "post-supervisor-cleanup.json": observed}
    for name in ("run-start.json", "reranker-start.json"):
        values[name] = {"frozen_plan_sha256": supervisor["frozen_plan_sha256"],
                        "started_at_utc": "2026-10-13T20:00:01+00:00"}
    for name, value in values.items():
        (study / name).write_text(json.dumps(value))
    return root, study, plan, values


def test_later_cleanup_does_not_promote_failed_supervision_to_success(tmp_path):
    root, study, plan, _ = failed_supervisor_fixture(tmp_path)
    result = audit.verify_failed_supervisor_diagnostics(root, study, plan, audit.zero_counts())
    assert result["clean_supervised_run"] is False
    assert result["original_supervisor_failure"]["success"] is False
    assert result["resident_stopped_cleanly"] is True


@pytest.mark.parametrize("change", ["success", "foreign_pid", "listener", "token", "group", "resident_count", "chronology"])
def test_failed_owner_diagnostic_rejects_false_cleanup_or_changed_counts(tmp_path, change):
    import json
    root, study, plan, values = failed_supervisor_fixture(tmp_path)
    if change == "success":
        values["supervisor.json"]["success"] = True
    elif change == "foreign_pid":
        values["post-supervisor-cleanup.json"]["owned_process_checks"]["999"] = {}
    elif change == "listener":
        values["post-supervisor-cleanup.json"]["listener_closed"] = False
    elif change == "token":
        token = root / ".cache/study/token.secret"
        token.parent.mkdir(parents=True)
        token.write_text("retained-private-token")
    elif change == "group":
        values["post-supervisor-cleanup.json"]["owned_process_checks"]["20"]["group_signal_zero"] = {
            "result": "PermissionError", "errno": 1}
    elif change == "resident_count":
        values["reranker-stop.json"]["completed_pairs"] = 1
    else:
        values["post-supervisor-cleanup.json"]["observed_at_utc"] = "2026-10-13T19:00:00+00:00"
    for name, value in values.items():
        (study / name).write_text(json.dumps(value))
    with pytest.raises(ValueError):
        audit.verify_failed_supervisor_diagnostics(root, study, plan, audit.zero_counts())


def clock_fixture(points=None):
    """Hand-authored successful timeline; no producer clock implementation used."""
    baseline = {"wall_time_ns": 1_700_000_000_000_000_123, "perf_counter_ns": 100_000_000_000,
                "utc": "2023-11-14T22:13:20+00:00"}
    # (elapsed performance nanoseconds, cumulative wall-minus-performance drift).
    points = points or [(step * 100_000_000, 0) for step in range(1, 8)]

    def sample(point):
        perf, drift = point
        wall = baseline["wall_time_ns"] + perf + drift
        seconds, ns = divmod(wall, 1_000_000_000)
        return {"wall_time_ns": wall, "perf_counter_ns": baseline["perf_counter_ns"] + perf,
                "utc": datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=ns // 1000).isoformat()}

    def delta(before, after):
        perf = after["perf_counter_ns"] - before["perf_counter_ns"]
        wall = after["wall_time_ns"] - before["wall_time_ns"]
        return {"wall_elapsed_ns": wall, "perf_elapsed_ns": perf, "drift_ns": wall - perf,
                "absolute_drift_ns": abs(wall - perf), "max_drift_ns": 2_000_000_000,
                "consistent": perf >= 0 and abs(wall - perf) <= 2_000_000_000}

    def receipt(index, label, start=None):
        end = sample(points[index])
        cumulative = delta(baseline, end)
        interval = None if start is None else delta(start["sample"], end)
        return {"version": 1, "label": label, "baseline": deepcopy(baseline), "sample": end,
                "cumulative": cumulative, "interval": interval, "max_drift_ns": 2_000_000_000,
                "consistent": cumulative["consistent"] and (interval is None or interval["consistent"])}

    schedule = [{"dataset": "code", "query_id": query, "condition": "dense", "repetition": 1}
                for query in ("first", "second")]
    setup = {"dataset": "code", "clock_start": receipt(0, "setup/code/start"), "index_and_embedding_warmup_s": .02}
    setup["clock_end"] = receipt(1, "setup/code/end", setup["clock_start"])
    rows = []
    for index, identity in enumerate(schedule):
        request = "code/" + identity["query_id"] + "/dense/1"
        row = {"identity": identity, "clock_start": receipt(2 + 2 * index, request + "/start"),
               "query_embedding_s": .01, "candidate_retrieval_s": .01, "reranker_http_s": .01, "packing_s": .01}
        row["clock_end"] = receipt(3 + 2 * index, request + "/end", row["clock_start"])
        rows.append(row)
    final = receipt(6, "pipeline/complete")
    completion = {"clock_baseline": deepcopy(baseline), "clock_integrity": deepcopy(audit.CLOCK_POLICY),
                  "clock_end": final, "clock_failure": None, "finished_at_utc": final["sample"]["utc"]}
    plan = {"schedule": schedule, "clock_integrity": deepcopy(audit.CLOCK_POLICY)}
    return plan, rows, [setup], completion


def test_clock_auditor_reconstructs_every_setup_query_and_cleanup_sample_without_mutation():
    values = clock_fixture()
    before = deepcopy(values)
    result = audit.verify_clock_study(*values)
    assert result["checkpoint_count"] == 7 and result["query_intervals"] == 2 and result["setup_intervals"] == 1
    assert result["wall_elapsed_ns"] == result["perf_elapsed_ns"] == 700_000_000
    assert result["max_absolute_cumulative_drift_ns"] == result["max_absolute_interval_drift_ns"] == 0
    assert result["chronological_wall_and_performance_samples"] is True
    assert values == before


@pytest.mark.parametrize("change", ["sample_type", "utc", "missing_start", "missing_end", "label", "version_bool",
    "threshold", "signed_delta", "absolute_delta", "consistent_claim", "baseline_reset", "interval_anchor",
    "failure", "completion_failure", "final_utc", "policy", "hidden_phase", "final_interval", "missing_final"])
def test_clock_auditor_rejects_forged_receipts_endpoints_policy_and_missing_lifecycle_samples(change):
    plan, rows, setups, completion = clock_fixture()
    receipt = rows[0]["clock_end"]
    if change == "sample_type":
        receipt["sample"]["perf_counter_ns"] = float(receipt["sample"]["perf_counter_ns"])
    elif change == "utc":
        receipt["sample"]["utc"] = "2023-11-14T22:13:21+00:00"
    elif change in ("missing_start", "missing_end"):
        rows[0].pop("clock_start" if change == "missing_start" else "clock_end")
    elif change == "label":
        receipt["label"] = rows[1]["clock_end"]["label"]
    elif change == "version_bool":
        receipt["version"] = True
    elif change == "threshold":
        receipt["max_drift_ns"] = 2_000_000_001
    elif change == "signed_delta":
        receipt["cumulative"]["drift_ns"] = 1
    elif change == "absolute_delta":
        receipt["interval"]["absolute_drift_ns"] = 1
    elif change == "consistent_claim":
        receipt["interval"]["consistent"] = 1
    elif change == "baseline_reset":
        second = rows[1]["clock_start"]
        second["baseline"] = deepcopy(second["sample"])
        second["cumulative"].update(wall_elapsed_ns=0, perf_elapsed_ns=0)
    elif change == "interval_anchor":
        # A plausible positive interval from a substituted earlier sample.
        receipt["interval"].update(wall_elapsed_ns=200_000_000, perf_elapsed_ns=200_000_000)
    elif change == "failure":
        setups[0]["clock_failure"] = {"consistent": False}
    elif change == "completion_failure":
        completion["clock_failure"] = {"consistent": False}
    elif change == "final_utc":
        completion["finished_at_utc"] = rows[-1]["clock_end"]["sample"]["utc"]
    elif change == "policy":
        plan["clock_integrity"]["max_absolute_drift_ns"] = 3_000_000_000
    elif change == "hidden_phase":
        receipt["filtered_samples"] = []
    elif change == "final_interval":
        completion["clock_end"]["interval"] = deepcopy(receipt["interval"])
    else:
        completion.pop("clock_end")
    with pytest.raises((ValueError, KeyError)):
        audit.verify_clock_study(plan, rows, setups, completion)


@pytest.mark.parametrize("position", [1, 2, 3, 6])
def test_clock_auditor_fails_suspend_during_setup_between_queries_within_query_or_cleanup(position):
    points = [(step * 100_000_000, 7_200_000_000_000 if step - 1 >= position else 0) for step in range(1, 8)]
    with pytest.raises(ValueError):
        audit.verify_clock_study(*clock_fixture(points))


def test_clock_auditor_never_turns_consistent_claim_into_authority_for_excess_drift():
    values = clock_fixture([(step * 100_000_000, 2_000_000_001 if step == 7 else 0) for step in range(1, 8)])
    values[3]["clock_end"]["consistent"] = True
    values[3]["clock_end"]["cumulative"]["consistent"] = True
    with pytest.raises(ValueError):
        audit.verify_clock_study(*values)


def test_clock_auditor_exact_two_second_boundary_and_nanosecond_precision():
    points = [(step * 100_000_000, 2_000_000_000 if step == 7 else 0) for step in range(1, 8)]
    result = audit.verify_clock_study(*clock_fixture(points))
    assert result["max_absolute_cumulative_drift_ns"] == 2_000_000_000
    assert result["baseline"]["wall_time_ns"] % 1000 == 123


def test_clock_auditor_rejects_opposing_wall_steps_even_when_final_cumulative_drift_is_tolerated():
    points = [(100_000_000, 0), (200_000_000, 0), (3_000_000_000, 1_900_000_000),
              (7_000_000_000, -1_900_000_000), (8_000_000_000, 0), (9_000_000_000, 0), (10_000_000_000, 0)]
    with pytest.raises(ValueError):
        audit.verify_clock_study(*clock_fixture(points))


@pytest.mark.parametrize("points", [
    [(100_000_000, 0), (200_000_000, 0), (500_000_000, 0), (600_000_000, 0),
     (300_000_000, 0), (400_000_000, 0), (700_000_000, 0)],
    [(100_000_000, 0), (200_000_000, 0), (300_000_000, -200_000_000),
     (400_000_000, 0), (500_000_000, 0), (600_000_000, 0), (700_000_000, 0)],
])
def test_clock_auditor_requires_actual_phase_order_on_both_clocks(points):
    with pytest.raises(ValueError):
        audit.verify_clock_study(*clock_fixture(points))


def test_clock_auditor_preserves_legacy_no_clock_qualification_without_fabricating_new_evidence():
    assert audit.verify_clock_study({}, [], [], {})["available"] is False
    with pytest.raises(ValueError):
        audit.verify_clock_study({}, [{"clock_start": {}}], [], {})


@pytest.mark.parametrize("where", ["setup", "query"])
def test_clock_auditor_rejects_measured_interior_phase_time_outside_its_actual_clock_span(where):
    plan, rows, setups, completion = clock_fixture()
    if where == "setup":
        setups[0]["index_and_embedding_warmup_s"] = .5
    else:
        rows[0]["packing_s"] = .5
    with pytest.raises(ValueError):
        audit.verify_clock_study(plan, rows, setups, completion)


INTERRUPTION_SPEC = importlib.util.spec_from_file_location(
    "independent_clock_interruption", Path(__file__).parents[1] / "scripts/audit_qwen_optimization_clock_interruption.py"
)
interruption = importlib.util.module_from_spec(INTERRUPTION_SPEC)
INTERRUPTION_SPEC.loader.exec_module(interruption)


def interrupted_clock_fixture():
    plan, rows, setups, completion = clock_fixture()
    plan["schedule"].append({"dataset": "code", "query_id": "never_attempted", "condition": "dense", "repetition": 1})
    plan["planned_requests"] = 3
    for row in rows:
        row["status"] = "completed"
    rows[-1]["status"] = "failed"
    rows[-1]["active_phase"] = "reranker_http"
    rows[-1]["clock_end"]["label"] = rows[-1]["clock_end"]["label"].replace("/end", "/failed_end")
    completion.update(complete=False, embedding_owner_closed=True, attempted_requests=2, completed_requests=1)
    return plan, rows, setups, completion


def test_interrupted_clock_diagnostic_keeps_failed_end_and_never_fills_unobserved_schedule():
    values = interrupted_clock_fixture()
    result = interruption.interrupted_clocks(*values)
    assert result["attempted_queries"] == 2 and result["completed_queries"] == 1
    assert result["checkpoint_count"] == 7
    assert result["failed_end_label"] == "code/second/dense/1/failed_end"
    assert result["failed_phase"] == "reranker_http"


@pytest.mark.parametrize("change", ["qualified", "owner", "filtered", "retry", "failed_end", "lost_failed_row", "missing_final", "counts", "drift"])
def test_interrupted_clock_diagnostic_rejects_loss_of_failure_or_clock_evidence(change):
    plan, rows, setups, completion = interrupted_clock_fixture()
    if change == "qualified":
        completion["complete"] = True
    elif change == "owner":
        completion["embedding_owner_closed"] = False
    elif change == "filtered":
        rows.pop(0)
    elif change == "retry":
        rows[-1]["identity"] = deepcopy(rows[0]["identity"])
    elif change == "failed_end":
        rows[-1]["clock_end"]["label"] = "code/second/dense/1/end"
    elif change == "lost_failed_row":
        rows[-1]["status"] = "completed"
    elif change == "missing_final":
        completion.pop("clock_end")
    elif change == "counts":
        completion["completed_requests"] = 2
    else:
        rows[-1]["clock_end"]["interval"]["absolute_drift_ns"] = 2_000_000_001
    with pytest.raises((ValueError, KeyError)):
        interruption.interrupted_clocks(plan, rows, setups, completion)
