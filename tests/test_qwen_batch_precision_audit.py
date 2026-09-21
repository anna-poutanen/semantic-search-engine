"""CPU corruption proofs for independent recorded precision-decomposition audit."""
from collections import Counter
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location("precision_independent_audit_tested",
    Path(__file__).parents[1] / "scripts/audit_qwen_batch_precision.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def vector(values):
    values = np.asarray(values, dtype=np.float32)
    return {"native_dtype": "mlx.core.bfloat16", "float32_dtype": "float32", "float32_shape": [len(values)],
            "float32_sha256": audit.sha(values.tobytes()), "values": values.tolist()}


def test_exact_native_bf16_float32_vector_bytes_signed_zero_and_stats():
    before = vector([1., -2., 0., -0.])
    after = vector([3.125, -1., 0., -0.])
    assert audit.vector(before, 4).tolist() == [1., -2., 0., -0.]
    result = audit.difference(audit.vector(before, 4), audit.vector(after, 4))
    assert result["max_absolute"] == 2.125
    assert result["mean_absolute"] == (2.125 + 1.) / 4
    assert result["rms"] == math.sqrt((2.125 ** 2 + 1) / 4)
    assert result["changed_dimensions"] == 2


@pytest.mark.parametrize("change", ["dtype", "shape", "float_shape", "hash", "value", "nonfinite",
                                   "boolean", "not_bf16", "extra", "missing"])
def test_false_native_dtype_value_hash_dimension_or_bf16_claim_rejected(change):
    record = vector([1., -2., 0., 3.125])
    if change == "dtype":
        record["native_dtype"] = "mlx.core.float32"
    elif change == "shape":
        record["float32_shape"] = [3]
    elif change == "float_shape":
        record["float32_shape"] = [4.]
    elif change == "hash":
        record["float32_sha256"] = "0" * 64
    elif change == "value":
        record["values"][0] = 2.
    elif change == "nonfinite":
        record["values"][0] = math.inf
    elif change == "boolean":
        record["values"][0] = True
    elif change == "not_bf16":
        values = np.asarray([.1, -2., 0., 3.125], dtype=np.float32)
        record["values"], record["float32_sha256"] = values.tolist(), audit.sha(values.tobytes())
    elif change == "extra":
        record["query_labels"] = "not allowed"
    else:
        record.pop("values")
    with pytest.raises((ValueError, KeyError)):
        audit.vector(record, 4)


def scalar(delta):
    return {"yes_logit": delta, "no_logit": 0., "logit_difference": delta,
            "yes_rank_signal": 1 / (1 + math.exp(-delta))}


def witness():
    metadata = {"source_sha256": "literal-source", "total_tokens": 4}
    old = metadata | scalar(1.) | {"input_ids_sha256": "exact-tokens"}
    current = scalar(1.03125)
    delta = {key: abs(old[key] - current[key]) for key in audit.SCORES}
    report = {"exact_score_match": False, "absolute_score_errors": delta,
              "original_score": {key: old[key] for key in audit.SCORES}}
    return metadata, old, current, report


def test_nonexact_original_r1_control_is_retained_and_blocks_exact_cause_control():
    metadata, old, current, report = witness()
    exact, delta = audit.witness_comparison(report, old, metadata, current, "exact-tokens")
    assert exact is False and delta["logit_difference"] == .03125


@pytest.mark.parametrize("change", ["token", "source", "difference", "signal", "nan", "fake_exact", "fake_error"])
def test_witness_input_scalar_math_or_invented_match_cannot_pass(change):
    metadata, old, current, report = witness()
    token_hash = "exact-tokens"
    if change == "token":
        token_hash = "other-tokens"
    elif change == "source":
        metadata["source_sha256"] = "another-source"
    elif change == "difference":
        current["logit_difference"] = 1.
    elif change == "signal":
        current["yes_rank_signal"] = .2
    elif change == "nan":
        current["yes_logit"] = math.nan
    elif change == "fake_exact":
        report["exact_score_match"] = True
    else:
        report["absolute_score_errors"]["yes_logit"] = 0.
    with pytest.raises(ValueError):
        audit.witness_comparison(report, old, metadata, current, token_hash)


def operations():
    identity = {"set_id": "source", "pair_indexes": [0], "batch_size": 1, "shared_hidden_reuse": False}
    first = {"event": "started", "kind": "sequential_backbone", "operation_number": 1, **identity}
    second = first | {"event": "completed", "elapsed_s": .02}
    return identity, [first, second]


def test_only_observed_complete_operation_advances_kind_count_and_sum():
    identity, events = operations()
    counts, elapsed = Counter(), Counter()
    assert audit.operation(events, 0, "sequential_backbone", identity, counts, elapsed) == 2
    assert counts == {"sequential_backbone": 1} and elapsed == {"sequential_backbone": .02}


@pytest.mark.parametrize("change", ["missing_start", "missing_completion", "reordered", "number", "boolean_index",
                                   "reuse", "kind", "negative", "nan", "extra"])
def test_missing_invented_reordered_or_invalid_operation_rejected(change):
    identity, events = operations()
    if change == "missing_start":
        events.pop(0)
    elif change == "missing_completion":
        events.pop()
    elif change == "reordered":
        events.reverse()
    elif change == "number":
        events[1]["operation_number"] = 2
    elif change == "boolean_index":
        events[0]["pair_indexes"] = [False]
    elif change == "reuse":
        events[1]["shared_hidden_reuse"] = True
    elif change == "kind":
        events[1]["kind"] = "batched_backbone"
    elif change == "negative":
        events[1]["elapsed_s"] = -1.
    elif change == "nan":
        events[1]["elapsed_s"] = math.nan
    else:
        events[1]["undocumented_reuse"] = True
    with pytest.raises(ValueError):
        audit.operation(events, 0, "sequential_backbone", identity, Counter(), Counter())


def lifecycle_fixture(tmp_path):
    root, study = tmp_path / "root", tmp_path / "root/results/probe"
    study.mkdir(parents=True)
    launcher = root / "results/qwen-precision-preflight-2026-10-13/launcher.py.txt"
    launcher.parent.mkdir()
    launcher.write_text("actual launcher")
    cleanup = root / "scripts/supervise_qwen_optimization.py"
    cleanup.parent.mkdir()
    cleanup.write_text("actual cleanup")
    (study / "frozen-plan.json").write_text("exact-plan")
    plan = {"frozen_at_utc": "2026-10-13T20:00:00+00:00"}
    start = {"frozen_plan_sha256": audit.sha(b"exact-plan"), "pid": 31, "process_group": 31,
             "started_at_utc": "2026-10-13T20:00:01+00:00"}
    completion = {"complete": True, "checkpoint_witnesses_final_passed": True, "checkpoint_witness_error": None,
                  "model_owners_released": True, "shared_hidden_reuse_diagnostic_only": True,
                  "reranking_latency_measured": False, "finished_at_utc": "2026-10-13T20:00:02+00:00",
                  "cleanup": {"mlx_active_memory_after_cleanup": 8, "mlx_process_lifetime_peak_bytes": 1000}}
    owner = {"frozen_plan_sha256": start["frozen_plan_sha256"], "model_pid": 31, "caffeinate_pid": 32,
             "success": True, "timed_out": False, "owned_sessions": True,
             "launcher_sha256": audit.sha(launcher.read_bytes()), "cleanup_source_sha256": audit.sha(cleanup.read_bytes())}
    for name, pid in (("model", 31), ("caffeinate", 32)):
        owner[name + "_stop"] = {"pid": pid, "owned_group": pid, "owned_group_stopped": True,
                                  "forced_kill": False, "errors": [], "exit_code": 0}
    values = {"run-start.json": start, "completion.json": completion, "supervisor.json": owner}
    for name, value in values.items():
        (study / name).write_text(json.dumps(value))
    return root, study, plan, values


def test_clean_standalone_diagnostic_owner_reconciles_without_performance_approval(tmp_path):
    root, study, plan, _ = lifecycle_fixture(tmp_path)
    result = audit.lifecycle(root, study, plan)
    assert result["owned_model_pid"] == 31 and result["cleanup"]["mlx_active_memory_after_cleanup"] == 8
    assert "not reranking latency" in result["limits"]


@pytest.mark.parametrize("change", ["failed", "timeout", "group", "wrong_pid", "force", "checkpoint", "owners",
                                   "latency", "early", "launcher", "cleanup", "memory"])
def test_false_lifecycle_checkpoint_source_or_performance_claim_is_rejected(tmp_path, change):
    root, study, plan, values = lifecycle_fixture(tmp_path)
    owner, complete = values["supervisor.json"], values["completion.json"]
    if change == "failed":
        owner["success"] = False
    elif change == "timeout":
        owner["timed_out"] = True
    elif change == "group":
        owner["model_stop"]["owned_group_stopped"] = False
    elif change == "wrong_pid":
        owner["model_stop"]["pid"] = 999
    elif change == "force":
        owner["model_stop"]["forced_kill"] = True
    elif change == "checkpoint":
        complete["checkpoint_witnesses_final_passed"] = False
    elif change == "owners":
        complete["model_owners_released"] = False
    elif change == "latency":
        complete["reranking_latency_measured"] = True
    elif change == "early":
        values["run-start.json"]["started_at_utc"] = "2026-10-13T19:00:00+00:00"
    elif change == "launcher":
        owner["launcher_sha256"] = "0" * 64
    elif change == "cleanup":
        owner["cleanup_source_sha256"] = "0" * 64
    else:
        complete["cleanup"]["mlx_active_memory_after_cleanup"] = False
    for name, value in values.items():
        (study / name).write_text(json.dumps(value))
    with pytest.raises(ValueError):
        audit.lifecycle(root, study, plan)


def test_independent_precision_import_has_no_native_model_or_producer(tmp_path):
    import subprocess
    import sys
    path = Path(__file__).parents[1] / "scripts/audit_qwen_batch_precision.py"
    code = (f"import runpy,sys; runpy.run_path({str(path)!r}); "
            "assert 'mlx.core' not in sys.modules and 'mlx_lm' not in sys.modules; "
            "assert 'probe_qwen_batch_precision' not in sys.modules; "
            "assert 'qwen_reranker_batch' not in sys.modules")
    subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path, check=True, capture_output=True)
