"""Synthetic CPU studies exercise gates, actual-sample ordering and certification."""
from __future__ import annotations

from copy import deepcopy
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/select_qwen_optimization.py"
spec = importlib.util.spec_from_file_location("optimization_selection_tested", SCRIPT)
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)


def config(family, k=50, b=1):
    return {"id": family if b == 0 else f"{family}_k{k}_b{b}", "retrieval": family, "shortlist": k, "batch_size": b}


def source_plan():
    sources, catalog = [], []
    for number in range(8):
        dataset = "repo" + str(number)
        queries = []
        for index in range(18):
            kind = "present" if index < 16 else "absent"
            count = 2 if index in (0, 1, 15) else 1
            groups = [[{"path": "code/a.py" if group == 0 or index == 1 else "code/b.py",
                        "evidence_lines": [10 + group * 10, 15 + group * 10], "symbol": "f" + str(group)}]
                      for group in range(count)] if kind == "present" else []
            identifier = "q" + str(index)
            queries.append({"query_id": identifier, "kind": kind, "groups": groups})
            catalog.append({"dataset": dataset, "query_id": identifier, "query": "Requested behavior " + str(index)})
        sources.append({"id": dataset, "queries": queries, "source_file_sha256": {"code/a.py": "frozen"},
                        "window_inventory_sha256": "frozen-window"})
    return sources, catalog


def schedule(catalog, ids):
    return [{"dataset": row["dataset"], "query_id": row["query_id"], "condition": condition, "repetition": repetition}
            for repetition in [1, 2, 3] for row in catalog for condition in ids]


def make_rows(plan, *, old=False, mutate=None):
    source = selection.source_queries(plan)
    catalog = {(q["dataset"], q["query_id"]): q["query"] for q in plan["query_catalog"]}
    configs = {} if old else {value["id"]: value for value in plan["conditions"]}
    result = []
    for identity in plan["schedule"]:
        key = identity["dataset"], identity["query_id"]
        q = source[key]
        condition = identity["condition"]
        ranks = [1] * len(q["groups"])
        if identity["query_id"] == "q1":
            ranks = [1, None] if "_qwen" in condition or not old and configs[condition]["batch_size"] else [None, None]
        if old and condition == "hybrid" and identity["query_id"] == "q15":
            ranks = [1, None]
        row = {"identity": identity, "status": "completed", "active_phase": "complete", "query": catalog[key],
               "kind": q["kind"], "required_groups": len(q["groups"]), "multi_file_and": q["multi_file"],
               "evidence_ranks": ranks, "elapsed_warm_pipeline_s": 1.0,
               "response": {"match_status": "candidates"}}
        if not old:
            row["configuration"] = configs[condition]
        if mutate is not None:
            mutate(row)
        result.append(row)
    return result


def certify(path, plan, rows, *, old=False):
    path.mkdir()
    (path / "frozen-plan.json").write_text(json.dumps(plan))
    with gzip.open(path / "searches.jsonl.gz", "wt") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    (path / "completion.json").write_text(json.dumps({"complete": True, "embedding_owner_closed": True,
        "attempted_requests": len(rows), "completed_requests": len(rows)}))
    (path / "supervisor.json").write_text(json.dumps({"main_exit_code": 0, "resident_exit_code": 0,
        "main_owned_group_stopped": True, "resident_owned_group_stopped": True, "forced_kill": False,
        "timed_out": False, "private_auth_token_removed": True}))
    hashes = {name: selection.file_hash(path / name) for name in (
        "frozen-plan.json", "searches.jsonl.gz", "completion.json", "supervisor.json")}
    audit = {"checks_passed": True, "artifact_sha256": hashes, "frozen_plan_sha256": hashes["frozen-plan.json"],
             "classification": "TEST FIXTURE ONLY; not an actual model certificate"}
    if not old:
        audit["stage"], audit["actual_stage_requests"] = plan["stage"], len(rows)
        compact = [{"dataset": row["identity"]["dataset"], "query_id": row["identity"]["query_id"],
                    "condition": row["identity"]["condition"], "repetition": row["identity"]["repetition"],
                    "kind": row["kind"], "evidence_ranks": row["evidence_ranks"], "multi_file": row["multi_file_and"],
                    "elapsed_s": row["elapsed_warm_pipeline_s"], "match_status": "candidates"} for row in rows]
        audit["metrics"] = {value["id"]: {"all": {key: value for key, value in selection.metric(
            [row for row in compact if row["condition"] == value["id"]]).items() if key != "complete_case_ids"}}
            for value in plan["conditions"]}
    (path / "independent-audit.json").write_text(json.dumps(audit))


def studies(tmp_path, *, stage="grid", mutate=None, configs=None):
    sources, catalog = source_plan()
    old_ids = ["dense", "dense_qwen", "hybrid", "hybrid_qwen"]
    old_plan = {"conditions": old_ids, "sources": sources, "query_catalog": catalog,
                "repetitions": 3, "primary_quality_repetition": 1, "schedule": schedule(catalog, old_ids)}
    old_plan["planned_requests"] = len(old_plan["schedule"])
    old = tmp_path / "old"
    certify(old, old_plan, make_rows(old_plan, old=True), old=True)
    if stage == "grid":
        chosen = {(source["id"], query["query_id"]) for source in sources for query in (source["queries"][0], source["queries"][15], source["queries"][16])}
        catalog = [q for q in catalog if (q["dataset"], q["query_id"]) in chosen]
    configs = configs or ([config(family, 50, 0) for family in ["dense", "hybrid"]] +
                          [config(family, k, b) for family in ["dense", "hybrid"] for k in [32, 50] for b in [1, 4]])
    plan = {"stage": stage, "conditions": configs, "sources": deepcopy(sources), "query_catalog": catalog,
            "repetitions": 3, "primary_quality_repetition": 1, "schedule": schedule(catalog, [c["id"] for c in configs])}
    plan["planned_requests"] = len(plan["schedule"])
    path = tmp_path / "actual"
    rows = make_rows(plan, mutate=mutate)
    certify(path, plan, rows)
    return path, old, plan, rows


def rehash(path, name):
    audit_path = path / "independent-audit.json"
    audit = json.loads(audit_path.read_text())
    audit["artifact_sha256"][name] = selection.file_hash(path / name)
    if name == "frozen-plan.json":
        audit["frozen_plan_sha256"] = audit["artifact_sha256"][name]
    audit_path.write_text(json.dumps(audit))


def rewrite_rows(path, rows):
    with gzip.open(path / "searches.jsonl.gz", "wt") as stream:
        stream.write("".join(json.dumps(row) + "\n" for row in rows))
    rehash(path, "searches.jsonl.gz")


def test_grid_uses_p95_of_all_actual_repetitions_before_median(tmp_path):
    def mutate(row):
        c, identity = row["configuration"], row["identity"]
        if c["batch_size"]:
            row["elapsed_warm_pipeline_s"] = .1 if c["batch_size"] == 4 else 1
            # Six slow samples change P95 despite fast first-pass/overall median.
            if c["batch_size"] == 4 and c["shortlist"] == 32 and identity["repetition"] == 2 and identity["dataset"] in {"repo0", "repo1"}:
                row["elapsed_warm_pipeline_s"] = 9
    path, old, _, _ = studies(tmp_path, mutate=mutate)
    result = selection.select(path, old)
    assert [value["id"] for value in result["selected_configs"]] == ["dense_k50_b4", "hybrid_k50_b4"]
    assert result["metrics"]["dense_k32_b4"]["warm_latency"]["p95_s"] == 9
    assert result["metrics"]["dense_k32_b4"]["warm_latency"]["median_s"] == .1
    assert result["metrics"]["dense_k50_b4"]["warm_latency"]["samples"] == 72
    assert result["metrics"]["dense_k50_b4"]["unique_queries"] == 24


def test_timing_ties_choose_smaller_k_then_smaller_batch(tmp_path):
    path, old, _, _ = studies(tmp_path)
    result = selection.select(path, old)
    assert [value["id"] for value in result["selected_configs"]] == ["dense_k32_b1", "hybrid_k32_b1"]
    assert [row["configuration"]["id"] for row in result["ranked_fallbacks"]["dense"]] == [
        "dense_k32_b1", "dense_k32_b4", "dense_k50_b1", "dense_k50_b4"]


def test_first_pass_loss_rejects_fast_config_even_if_later_repetitions_improve(tmp_path):
    def mutate(row):
        if row["configuration"]["id"] == "dense_k32_b4":
            row["elapsed_warm_pipeline_s"] = .01
            if row["identity"]["dataset"] == "repo0" and row["identity"]["query_id"] == "q0" and row["identity"]["repetition"] == 1:
                row["evidence_ranks"] = [1, None]
    path, old, _, _ = studies(tmp_path, mutate=mutate)
    result = selection.select(path, old)
    fastest = result["ranked_fallbacks"]["dense"][0]
    assert fastest["configuration"]["id"] == "dense_k32_b4" and not fastest["qualified"]
    assert fastest["gate"]["lost_reference_complete_cases"] == ["repo0/q0"]
    assert result["selected_by_family"]["dense"]["configuration"]["id"] == "dense_k32_b1"


def test_default_k50_fallback_is_recorded_when_no_grid_config_clears_original_group_floor(tmp_path):
    def mutate(row):
        if row["configuration"]["retrieval"] == "dense" and row["configuration"]["batch_size"] and row["identity"]["query_id"] == "q0":
            row["evidence_ranks"] = [None, None]
    path, old, _, _ = studies(tmp_path, mutate=mutate)
    selected = selection.select(path, old)["selected_by_family"]["dense"]
    assert selected["configuration"]["id"] == "dense_k50_b1" and selected["default_k50_fallback"]
    assert not selected["qualified"]


def test_development_retains_complete_cases_but_publishes_partial_support_losses(tmp_path):
    configs = [config(family, 50, 0) for family in ["dense", "hybrid"]] + [config("dense", 32, 4), config("hybrid", 32, 4)]
    def mutate(row):
        if row["configuration"]["id"] == "dense_k32_b4":
            row["elapsed_warm_pipeline_s"] = .1
            if row["identity"]["query_id"] == "q1":
                row["evidence_ranks"] = [None, None]
    path, old, _, _ = studies(tmp_path, stage="development", configs=configs, mutate=mutate)
    result = selection.select(path, old)
    dense = result["selected_by_family"]["dense"]
    assert dense["qualified"] and len(dense["changes_from_k50"]["covered_group_losses"]) == 8
    assert dense["changes_from_k50"]["complete_case_losses"] == []
    assert dense["metrics"]["covered_groups"] == dense["gate"]["original_no_reranker_covered_groups"]
    assert result["selected_config"]["id"] == "hybrid_k32_b4"  # Same complete count, more support.
    assert result["ready_for_fresh_validation"]


def test_development_complete_count_takes_priority_over_warm_speed(tmp_path):
    configs = [config(family, 50, 0) for family in ["dense", "hybrid"]] + [config("dense", 32, 4), config("hybrid", 32, 4)]
    def mutate(row):
        if row["configuration"]["id"] == "hybrid_k32_b4" and row["identity"]["query_id"] == "q1":
            row["evidence_ranks"] = [1, 2]
        row["elapsed_warm_pipeline_s"] = 9 if row["configuration"]["retrieval"] == "hybrid" else .1
    path, old, _, _ = studies(tmp_path, stage="development", configs=configs, mutate=mutate)
    result = selection.select(path, old)
    assert result["selected_config"]["retrieval"] == "hybrid"
    assert result["selected_by_family"]["hybrid"]["metrics"]["complete"] == 128


def test_development_dense_breaks_a_remaining_exact_tie(tmp_path):
    configs = [config(family, 50, 0) for family in ["dense", "hybrid"]] + [config("dense", 32, 4), config("hybrid", 32, 4)]
    path, old, _, _ = studies(tmp_path, stage="development", configs=configs)
    result = selection.select(path, old)
    assert result["selected_config"]["retrieval"] == "dense"


def test_development_no_eligible_config_blocks_fresh_validation(tmp_path):
    def mutate(row):
        if row["configuration"]["batch_size"] and row["identity"]["query_id"] == "q0":
            row["evidence_ranks"] = [None, None]
    path, old, _, _ = studies(tmp_path, stage="development", mutate=mutate)
    result = selection.select(path, old)
    assert result["selected_config"] is None and not result["ready_for_fresh_validation"]
    assert result["selected_configs"] == [] and all(value is None for value in result["selected_by_family"].values())


def test_uncertified_or_mutated_artifacts_are_rejected(tmp_path):
    path, old, _, _ = studies(tmp_path)
    audit_path = path / "independent-audit.json"
    audit = json.loads(audit_path.read_text())
    audit["checks_passed"] = False
    audit_path.write_text(json.dumps(audit))
    with pytest.raises(ValueError, match="Independent audit"):
        selection.select(path, old)
    audit["checks_passed"] = True
    audit_path.write_text(json.dumps(audit))
    (path / "completion.json").write_text("{}")
    with pytest.raises(ValueError, match="artifact hash changed"):
        selection.select(path, old)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "failed", "zero", "nan", "bad_rank", "configuration"])
def test_rehashed_raw_corruptions_still_fail_contract_checks(tmp_path, kind):
    path, old, _, rows = studies(tmp_path)
    if kind == "missing":
        rows.pop()
    elif kind == "duplicate":
        rows[1] = rows[0]
    elif kind == "failed":
        rows[0]["status"] = "failed"
    elif kind in ("zero", "nan"):
        rows[0]["elapsed_warm_pipeline_s"] = 0 if kind == "zero" else float("nan")
    elif kind == "bad_rank":
        rows[0]["evidence_ranks"][0] = True
    else:
        rows[0]["configuration"]["shortlist"] = 8
    rewrite_rows(path, rows)
    with pytest.raises(ValueError):
        selection.select(path, old)


def test_audit_metrics_must_equal_recomputed_raw_results(tmp_path):
    path, old, _, _ = studies(tmp_path)
    audit_path = path / "independent-audit.json"
    audit = json.loads(audit_path.read_text())
    audit["metrics"]["dense_k32_b1"]["all"]["complete"] = 999
    audit_path.write_text(json.dumps(audit))
    with pytest.raises(ValueError, match="disagree with independent audit"):
        selection.select(path, old)


def test_development_cannot_use_changed_frozen_gold(tmp_path):
    path, old, plan, _ = studies(tmp_path, stage="development")
    plan["sources"][0]["queries"][0]["groups"][0][0]["symbol"] = "changed"
    (path / "frozen-plan.json").write_text(json.dumps(plan))
    rehash(path, "frozen-plan.json")
    with pytest.raises(ValueError, match="gold changed"):
        selection.select(path, old)


def test_grid_decision_binding_rejects_unsupported_development_configuration(tmp_path):
    path, old, _, _ = studies(tmp_path, stage="development")
    decision = tmp_path / "grid-decision.json"
    old_audit_hash = selection.file_hash(old / "independent-audit.json")
    decision.write_text(json.dumps({"stage": "grid", "old_reference_binding": {"audit_sha256": old_audit_hash},
        "ranked_fallbacks": {"dense": [{"configuration": config("dense", 50, 1)}],
                             "hybrid": [{"configuration": config("hybrid", 50, 1)}]}}))
    with pytest.raises(ValueError, match="absent from frozen ranked grid"):
        selection.select(path, old, grid_decision=decision)


def test_cli_writes_reviewable_decision_once_and_imports_no_models(tmp_path):
    path, old, _, _ = studies(tmp_path)
    output = tmp_path / "decision.json"
    selection.main(["--study", str(path), "--old-study", str(old), "--output", str(output)])
    result = json.loads(output.read_text())
    assert result["model_calls"] == result["embedding_calls"] == result["retrieval_calls"] == 0
    with pytest.raises(FileExistsError):
        selection.main(["--study", str(path), "--old-study", str(old), "--output", str(output)])
    code = (f"import runpy,sys; runpy.run_path({str(SCRIPT)!r}); "
            "assert 'mlx.core' not in sys.modules and 'semantic_search.embeddings' not in sys.modules")
    subprocess.run([sys.executable, "-B", "-c", code], check=True)
