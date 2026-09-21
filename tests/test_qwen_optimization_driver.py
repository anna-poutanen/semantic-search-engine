"""Frozen driver and numerical gate contracts; no model/runtime inference."""
from __future__ import annotations

from copy import deepcopy
import gzip
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("optimization_driver_tested", ROOT / "scripts/run_qwen_optimization.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
prototype = importlib.import_module("prototype_qwen_reranker")


@pytest.mark.parametrize("retrieval,depth,size", [("dense", 50, 0), ("hybrid", 50, 0), ("dense", 5, 1),
                                               ("hybrid", 32, 4), ("hybrid", 50, 8)])
def test_configuration_has_exact_identity_and_declared_settings(retrieval, depth, size):
    result = driver.configuration(retrieval, depth, size)
    assert result == {"id": retrieval if size == 0 else f"{retrieval}_k{depth}_b{size}",
                      "retrieval": retrieval, "shortlist": depth, "batch_size": size}
    assert driver.check_configurations([result]) == [result]


@pytest.mark.parametrize("retrieval,depth,size", [("bm25", 50, 0), ("dense", True, 1), ("dense", 6, 2),
                                               ("dense", 0, 4), ("dense", 51, 4), ("dense", 32, 0),
                                               ("dense", 5, True), ("dense", 5, 3), ("dense", 5, 16)])
def test_configuration_rejects_undeclared_depth_batch_or_partial_baseline(retrieval, depth, size):
    with pytest.raises(ValueError):
        driver.configuration(retrieval, depth, size)


def test_duplicate_or_malformed_configuration_cannot_enter_a_frozen_schedule():
    baseline = driver.configuration("dense")
    for values in ([], [baseline, baseline], [baseline | {"id": "different"}], [baseline | {"gold": []}]):
        with pytest.raises(ValueError):
            driver.check_configurations(values)


def test_schedule_keeps_every_query_condition_repetition_and_rotates_order():
    catalog = [{"dataset": "a", "query_id": "first"}, {"dataset": "a", "query_id": "second"},
               {"dataset": "b", "query_id": "third"}]
    configurations = [driver.configuration("dense"), driver.configuration("hybrid"),
                      driver.configuration("dense", 32, 4)]
    schedule = driver.schedule(catalog, configurations)
    assert len(schedule) == 27 and len({tuple(row.items()) for row in schedule}) == 27
    ids = [value["id"] for value in configurations]
    assert [row["condition"] for row in schedule[:3]] == ids
    assert [row["condition"] for row in schedule[3:6]] == ids[1:] + ids[:1]
    assert [row["condition"] for row in schedule[6:9]] == ids[1:] + ids[:1]
    assert all(row["dataset"] == "a" for row in schedule[:18])
    assert all(row["dataset"] == "b" for row in schedule[18:])


def test_timing_panel_uses_fixed_manifest_positions_without_scores_or_gold_ranks():
    entries, catalog = [], []
    for number in range(8):
        identifier = f"repo{number}"
        queries = [{"id": name, "kind": kind} for name, kind in
                   (("absent_first", "absent"), ("positive_first", "present"),
                    ("positive_middle", "present"), ("absent_later", "absent"), ("positive_last", "present"))]
        entries.append(({"id": identifier}, {"queries": queries}, {}))
        catalog.extend({"dataset": identifier, "query_id": query["id"]} for query in queries)
    selected = driver.panel_catalog(entries, catalog)
    assert len(selected) == 24
    for number in range(8):
        assert [row["query_id"] for row in selected if row["dataset"] == f"repo{number}"] == [
            "absent_first", "positive_first", "positive_last"]


def scores(differences):
    return [{"source_sha256": f"source-{index}", "delivered_sha256": f"source-{index}",
             "query_sha256": "same-query", "total_tokens": index + 10,
             **prototype.binary_score(value, 0)} for index, value in enumerate(differences)]


def test_identical_parity_passes_with_zero_errors_and_stable_ties():
    reference = scores([1, 1, .5, .5, .5, -.5])
    result = driver.parity_comparison(reference, deepcopy(reference))
    assert result["passed"] and not result["ranking_inversions"]
    assert all(value == 0 for value in result["max_absolute_errors"].values())
    assert result["top_five_reference"] == result["top_five_candidate"] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("field", ["source_sha256", "delivered_sha256", "query_sha256", "total_tokens"])
def test_changed_constructed_input_fails_before_numeric_comparison(field):
    reference = scores([1])
    candidate = deepcopy(reference)
    candidate[0][field] = "drift"
    with pytest.raises(ValueError, match="constructed input differs"):
        driver.parity_comparison(reference, candidate)


@pytest.mark.parametrize("field", ["yes_logit", "no_logit", "logit_difference", "yes_rank_signal"])
def test_each_frozen_numeric_error_limit_is_inclusive_and_cannot_be_relaxed(field):
    # Large B1 logits avoid unrelated probability drift. These unit inputs test
    # the gate's scalar boundary; real worker scalar consistency is checked upstream.
    reference = scores([4.0])
    limit = driver.PARITY_LIMITS[field]
    exactly = deepcopy(reference)
    exactly[0][field] += limit
    assert driver.parity_comparison(reference, exactly)["passed"]
    outside = deepcopy(reference)
    outside[0][field] += limit + .0001
    assert not driver.parity_comparison(reference, outside)["passed"]


@pytest.mark.parametrize("field", ["yes_logit", "no_logit", "logit_difference", "yes_rank_signal"])
def test_nonfinite_parity_scalars_are_rejected(field):
    reference, candidate = scores([1]), scores([1])
    candidate[0][field] = float("nan")
    with pytest.raises(ValueError, match="Nonfinite"):
        driver.parity_comparison(reference, candidate)


def test_exact_reference_tie_becoming_opposite_order_is_recorded_as_near_tie():
    result = driver.parity_comparison(scores([1, 1]), scores([.99, 1.01]))
    assert result["passed"]
    assert result["ranking_inversions"] == [{"pair_indexes": [0, 1], "reference_margin": 0.0,
                                             "candidate_margin": pytest.approx(.02), "near_tie": True}]


def test_new_exact_tie_obeys_original_index_and_records_prior_order_flip():
    result = driver.parity_comparison(scores([1, 1.02]), scores([1.01, 1.01]))
    assert result["passed"] and len(result["ranking_inversions"]) == 1
    assert result["ranking_inversions"][0]["reference_margin"] == pytest.approx(.02)
    assert result["ranking_inversions"][0]["candidate_margin"] == 0


def test_strong_order_inversion_is_a_failed_gate_even_if_top_five_set_unchanged():
    result = driver.parity_comparison(scores([1, 1.3]), scores([1.2, 1.1]))
    assert not result["passed"] and not result["ranking_inversions"][0]["near_tie"]
    assert result["top_five_reference"] == result["top_five_candidate"]


def test_top_five_near_tie_membership_change_is_explicitly_reported():
    result = driver.parity_comparison(scores([1.04, 1.03, 1.02, 1.01, 1.005, 1.0]),
                                      scores([1.04, 1.03, 1.02, 1.01, .98, 1.02]))
    assert result["passed"] and result["top_five_reference"] == [0, 1, 2, 3, 4]
    assert result["top_five_candidate"] == [0, 1, 2, 3, 5]
    assert result["top_five_swap_reference_margins"] == [pytest.approx(.005)]


def test_parity_source_pool_uses_original_source_hash_and_first_positive(monkeypatch):
    source = "def sample():\n    return 'café'\n"
    source_hash = driver.sha(source.encode())
    def gz(path):
        if path.name == "source-content.jsonl.gz":
            return [{"sha256": source_hash, "source": source}]
        return [{"pool_key": "repo/first/dense", "windows": [{"source_hash": source_hash}]}]
    monkeypatch.setattr(driver, "gz_rows", gz)
    entries = [({"id": "repo"}, {"queries": [{"id": "absent", "kind": "absent", "query": "missing"},
                    {"id": "first", "kind": "present", "query": "return a greeting"},
                    {"id": "later", "kind": "present", "query": "later behavior"}]}, {})]
    result = driver.parity_inputs(entries)
    assert result[0]["id"] == "repo/first"
    assert result[0]["pairs"] == [{"query": "return a greeting", "source": source}]
    synthetic = result[-1]["pairs"]
    assert len(synthetic) == 16 and synthetic[0] == synthetic[14]
    assert len(prototype.physical_lines(synthetic[-1]["source"])) == 4
    assert "alpha " * 100 in synthetic[-1]["source"] and "beta " * 100 in synthetic[-1]["source"]


def test_parity_failure_keeps_failed_attempt_and_never_retries(tmp_path, monkeypatch):
    calls = []
    def fail(*args):
        calls.append(args)
        raise RuntimeError("injected parity scorer failure")
    monkeypatch.setattr(driver, "scored_request", fail)
    frozen = {"schedule": [{"parity_set": "synthetic", "batch_size": 1}],
              "parity_sets": [{"id": "synthetic", "pairs": [{"query": "q", "source": "s"}]}]}
    with pytest.raises(RuntimeError, match="parity scorer"):
        driver.run_parity(tmp_path, frozen, None, {}, "")
    with gzip.open(tmp_path / "parity-scores.jsonl.gz", "rt") as stream:
        row = json.loads(stream.read())
    assert len(calls) == 1 and row["status"] == "failed" and row["error_type"] == "RuntimeError"
    assert not (tmp_path / "parity-gate.json").exists()


def driver_fixture(tmp_path, monkeypatch, stage):
    current = {"stage": stage, "conditions": [], "embedding_checkpoint": {}, "implementation_sha256": {}}
    decision = tmp_path / "decision.json"
    decision.write_text("frozen selection")
    frozen = current | {"frozen_at_utc": "fixed", "source_commit": "a" * 40,
                        "decision_inputs": {"decision.json": driver.sha(decision.read_bytes())}}
    plan = tmp_path / "frozen-plan.json"
    plan.write_text(json.dumps(frozen))
    ready = tmp_path / "ready.json"
    ready.write_text(json.dumps({"url": "http://127.0.0.1:43210", "resident_ready": True,
                                "generic_warmup_completed": True,
                                "frozen_plan_sha256": driver.sha(plan.read_bytes())}))
    token = tmp_path / "token.secret"
    token.write_text("test-private-token")
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "freeze_inputs", lambda *args: ([], current))
    monkeypatch.setattr(driver, "implementation_hashes", lambda: {})
    monkeypatch.setattr(driver.previous, "loopback_opener", lambda: None)
    return decision, ready, token


def test_changed_decision_hash_rejects_before_any_new_attempt_or_scoring(tmp_path, monkeypatch):
    decision, ready, token = driver_fixture(tmp_path, monkeypatch, "parity")
    decision.write_text("selection changed")
    monkeypatch.setattr(driver, "run_parity", lambda *args: pytest.fail("must not score"))
    with pytest.raises(ValueError, match="Decision input changed"):
        driver.run(tmp_path, Path("unused"), Path("unused"), ready, token)
    assert not (tmp_path / "run-start.json").exists()


def test_parity_driver_does_not_verify_load_or_call_embedding_pipeline(tmp_path, monkeypatch):
    _, ready, token = driver_fixture(tmp_path, monkeypatch, "parity")
    calls = []
    monkeypatch.setattr(driver, "run_parity", lambda *args: calls.append(args))
    monkeypatch.setattr(driver.packing, "verify_weights", lambda *args: pytest.fail("parity must not read embedding weights"))
    driver.run(tmp_path, Path("unused"), Path("unused"), ready, token)
    assert len(calls) == 1 and (tmp_path / "run-start.json").exists()
    assert not (tmp_path / "embedding-weight-verification.json").exists()


def test_pipeline_failure_receipt_and_completion_are_preserved_by_root_driver(tmp_path, monkeypatch):
    import qwen_optimization_pipeline
    _, ready, token = driver_fixture(tmp_path, monkeypatch, "grid")
    detailed_failure = {"error_type": "RuntimeError", "error": "actual helper failure",
                        "attempted_requests": 2, "completed_requests": 1}
    detailed_completion = {"complete": False, "attempted_requests": 2, "completed_requests": 1,
                           "embedding_owner_closed": True}
    def fail(output, *args):
        driver.write(output / "failure.json", detailed_failure)
        driver.write(output / "completion.json", detailed_completion)
        raise RuntimeError("actual helper failure")
    monkeypatch.setattr(qwen_optimization_pipeline, "run_pipeline", fail)
    monkeypatch.setattr(driver.packing, "verify_weights", lambda *args: {})
    with pytest.raises(RuntimeError, match="actual helper failure"):
        driver.run(tmp_path, Path("unused"), Path("unused"), ready, token)
    assert json.loads((tmp_path / "failure.json").read_text()) == detailed_failure
    assert json.loads((tmp_path / "completion.json").read_text()) == detailed_completion
    failure = json.loads((tmp_path / "driver-failure.json").read_text())
    assert failure["stage"] == "grid" and failure["no_retry"] is True
