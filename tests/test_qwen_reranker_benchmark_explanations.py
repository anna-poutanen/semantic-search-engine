"""Semantic source-origin/prefix diagnostics use only CPU fake receipts."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reranker_explanations_test_subject",
                                            ROOT / "scripts/explain_qwen_reranker_benchmark.py")
explain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(explain)

SOURCES = {"a.py": "def caller():\n    return helper()\n",
           "b.py": "def helper():\n    return 7\n"}
GOLD = [[{"path": "a.py", "symbol": "caller", "evidence_lines": [1, 2]}],
        [{"path": "b.py", "symbol": "helper", "evidence_lines": [1, 2]}]]


def window(path, symbol, *, start=1, end=2, origin="retrieval", related_to=None, identifier=None):
    source = "".join(explain.physical_lines(SOURCES[path])[start - 1:end])
    return {"id": identifier or f"{path}:{symbol}:{start}-{end}", "path": path, "symbol": symbol,
            "start_line": start, "end_line": end, "source": source,
            "source_hash": explain.sha(source.encode()), "origin": origin, "related_to": related_to}


def score(raw, *, count=2):
    source = raw["source"]
    lines = explain.physical_lines(source)
    prefix = "".join(lines[:count])
    return {"source_lines": len(lines), "delivered_lines": count,
            "source_sha256": explain.sha(source.encode()), "delivered_sha256": explain.sha(prefix.encode()),
            "truncated": count < len(lines)}


def fixture(*, score_clipped=False):
    raw = window("a.py", "caller")
    dependency = window("b.py", "helper", origin="dependency", related_to=raw["id"])
    pools, rows, schedule = {}, [], []
    for condition in explain.CONDITIONS:
        identity = {"dataset": "flask", "query_id": "compound", "condition": condition, "repetition": 1}
        schedule.append(identity)
        # Gain for dense; regression for hybrid. Preserve both in the report.
        returned = [raw, dependency] if condition in ("dense_qwen", "hybrid") else [raw]
        row = {"identity": identity, "status": "completed", "query": "Run a caller and its helper.",
               "kind": "present", "pool_id": condition, "candidate_order": [raw["id"]],
               "ranking_order": [raw["id"]], "response": {"results": returned, "match_status": "candidates"},
               "evidence_ranks": [1, 2 if len(returned) == 2 else None],
               "symbol_compatibility_evidence_ranks": [1, 2 if len(returned) == 2 else None]}
        pools[condition] = {"windows": [raw]}
        if condition.endswith("_qwen"):
            row["reranker"] = {"results": [score(raw, count=1 if score_clipped else 2)]}
        rows.append(row)
    plan = {"sources": [{"id": "flask", "queries": [{"query_id": "compound", "kind": "present", "groups": GOLD}]}],
            "schedule": schedule, "planned_requests": 4, "primary_quality_repetition": 1}
    return plan, rows, pools, {"flask": SOURCES}


def test_all_groups_required_with_dense_gain_and_hybrid_regression_retained():
    plan, rows, pools, sources = fixture()
    result = explain.analyze(plan, rows, pools, sources)
    assert len(result["comparisons"]["dense"]["complete_gains"]) == 1
    assert len(result["comparisons"]["hybrid"]["complete_regressions"]) == 1
    assert result["comparisons"]["dense"]["complete_gains"][0]["before_ranks"] == [1, None]
    assert result["comparisons"]["dense"]["complete_gains"][0]["after_ranks"] == [1, 2]
    assert result["primary_condition_rows"] == 4


def test_dependency_gold_outside_all_raw_candidates_is_explicit():
    plan, rows, pools, sources = fixture()
    result = explain.analyze(plan, rows, pools, sources)
    outside = result["primary_delivered_gold_absent_from_raw_pool"]
    assert len(outside) == 2  # Dense-Qwen gain and hybrid baseline already had the dependency.
    assert all(case["group"] == 2 for case in outside)
    assert all(case["evidence"]["covered_only_by_dependency_results"] for case in outside)
    assert all(case["evidence"]["covering_results"][0]["exact_window_id_outside_raw_pool"] for case in outside)
    assert result["primary_result_origin_counts"] == {"retrieval": 4, "dependency": 2}


def test_exact_dependency_window_outside_pool_can_overlap_ranked_source():
    raw = window("b.py", "enclosing", identifier="enclosing-window")
    returned = window("b.py", "helper", origin="dependency", identifier="callee-window")
    prefix = explain.scored_prefixes([raw], {"results": [score(raw)]}, SOURCES)
    group = explain.group_detail(GOLD[1], [raw], [raw], [returned], prefix)
    assert group["delivered_evidence_absent_from_raw_pool"] is False
    detail = group["covering_results"][0]
    assert detail["exact_window_id_outside_raw_pool"] is True
    assert detail["entire_delivered_span_inside_some_raw_window"] is True
    assert detail["entire_delivered_span_inside_some_scored_prefix"] is True


def test_direct_scored_clip_and_unscored_delivered_gold_are_distinguished():
    plan, rows, pools, sources = fixture(score_clipped=True)
    result = explain.analyze(plan, rows, pools, sources)
    assert result["scoring_prefix_clips"]["actual_measured_pairs"] == 2
    assert result["scoring_prefix_clips"]["clipped_pair_occurrences"] == 2
    assert len(result["primary_delivered_suffix_beyond_direct_scored_prefix"]) == 2
    cases = result["primary_delivered_gold_without_complete_scored_prefix"]
    assert len(cases) == 3  # Two direct gold spans plus one unseen dependency body.
    assert any(case["evidence"]["raw_candidate_evidence_rank"] == 1 for case in cases)
    assert any(case["evidence"]["raw_candidate_evidence_rank"] is None for case in cases)


def test_another_scoring_window_can_cover_directly_unscored_suffix():
    direct = window("a.py", "caller", identifier="direct")
    other = window("a.py", "enclosing", identifier="other")
    prefixes = explain.scored_prefixes([direct, other],
        {"results": [score(direct, count=1), score(other)]}, SOURCES)
    detail = explain.result_detail(direct, [direct, other], prefixes)
    assert detail["delivered_extends_beyond_direct_scored_prefix"] is True
    assert detail["entire_delivered_span_inside_some_scored_prefix"] is True


def test_or_alternative_membership_does_not_require_every_target():
    raw = window("a.py", "caller")
    returned = window("b.py", "helper", origin="dependency")
    group = explain.group_detail(GOLD[0] + GOLD[1], [raw], [raw], [returned], None)
    assert group["raw_candidate_evidence_rank"] == 1
    assert group["delivered_evidence_rank"] == 1
    assert group["delivered_evidence_absent_from_raw_pool"] is False


def test_first_symbol_window_can_hide_later_complete_source_span():
    early = window("a.py", "caller", end=1)
    full = window("a.py", "caller")
    group = explain.group_detail(GOLD[0], [early, full], [early, full], [early], None)
    assert group["raw_candidate_evidence_rank"] == 2
    assert group["first_per_symbol_evidence_rank"] is None
    assert any("first window" in text for text in group["observations"])


def test_full_first_anchor_can_lose_gold_during_packing():
    full = window("a.py", "caller")
    clipped_return = window("a.py", "caller", end=1, identifier=full["id"])
    group = explain.group_detail(GOLD[0], [full], [full], [clipped_return], None)
    assert group["first_per_symbol_evidence_rank"] == 1
    assert group["delivered_evidence_rank"] is None
    assert any("after packing" in text for text in group["observations"])


def test_primary_span_credit_retains_python_exact_symbol_difference():
    raw = window("a.py", "enclosing")
    row = {"identity": {"dataset": "flask", "query_id": "q", "condition": "dense", "repetition": 1},
           "query": "call", "kind": "present", "pool_id": "pool", "candidate_order": [raw["id"]],
           "ranking_order": [raw["id"]], "response": {"results": [raw], "match_status": "candidates"},
           "evidence_ranks": [1], "symbol_compatibility_evidence_ranks": [None]}
    result = explain.inspect_row(row, {"pool": {"windows": [raw]}}, [GOLD[0]], SOURCES)
    assert result["complete"] is True
    assert result["symbol_compatibility_complete"] is False
    assert result["symbol_compatibility_evidence_ranks"] == [None]


@pytest.mark.parametrize("field,value", [("delivered_sha256", "bad"), ("delivered_lines", 0),
                                        ("source_lines", 99), ("truncated", True)])
def test_scored_prefix_metadata_must_match_literal_complete_lines(field, value):
    raw = window("a.py", "caller")
    wrong = score(raw)
    wrong[field] = value
    with pytest.raises(ValueError):
        explain.scored_prefixes([raw], {"results": [wrong]}, SOURCES)


def test_full_schedule_and_failed_attempts_cannot_be_silently_removed():
    plan, rows, pools, sources = fixture()
    with pytest.raises(ValueError, match="Whole frozen schedule"):
        explain.analyze(plan, rows[:-1], pools, sources)
    failed = deepcopy(rows)
    failed[0]["status"] = "failed"
    with pytest.raises(ValueError, match="completed scheduled"):
        explain.analyze(plan, failed, pools, sources)


def test_candidate_mismatch_is_retained_as_a_limit_on_ranking_comparison():
    plan, rows, pools, sources = fixture()
    different = window("b.py", "helper")
    pools["dense_qwen"]["windows"] = [different]
    row = rows[1]
    row["candidate_order"] = row["ranking_order"] = [different["id"]]
    row["reranker"]["results"] = [score(different)]
    result = explain.analyze(plan, rows, pools, sources)
    assert len(result["comparisons"]["dense"]["candidate_pair_mismatches"]) == 1
    assert len(result["comparisons"]["dense"]["complete_gains"]) == 1


def test_absent_empty_groups_are_not_counted_as_positive_complete():
    plan, rows, pools, sources = fixture()
    plan["sources"][0]["queries"][0].update(kind="absent", groups=[])
    for row in rows:
        row.update(kind="absent", evidence_ranks=[], symbol_compatibility_evidence_ranks=[])
    result = explain.analyze(plan, rows, pools, sources)
    assert all(row["complete"] is None for row in result["primary_case_details"])
    assert result["comparisons"]["dense"]["complete_gains"] == []
