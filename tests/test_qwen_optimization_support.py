"""Independent receipt explanation tests; no producer or model imports."""
from __future__ import annotations

from copy import deepcopy
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/explain_qwen_optimization_support.py"
spec = importlib.util.spec_from_file_location("optimization_support_tested", SCRIPT)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


SOURCE = "def complete():\n    value = 1\n    return value\n"


def target(path="module.py"):
    return {"path": path, "symbol": "complete", "evidence_lines": [1, 3], "evidence_sha256": support.sha(SOURCE.encode())}


def window(*, partial=False, path="module.py", origin="retrieval"):
    source = SOURCE if not partial else "".join(support.physical_lines(SOURCE)[:2])
    return {"id": path + ("/partial" if partial else "/full"), "path": path, "symbol": "different_parent_name",
            "start_line": 1, "end_line": 2 if partial else 3, "source": source,
            "source_hash": support.sha(source.encode()), "origin": origin, "related_to": None}


def case(*, covered, condition="dense", absent=False, pool="same"):
    groups = [] if absent else [support.group_support([target()], [window(partial=not covered)])]
    return {"dataset": "repo", "query_id": "q", "query": "Find a numeric return", "kind": "absent" if absent else "present",
            "condition": condition, "configuration": {"id": condition}, "groups": groups,
            "complete": all(group["covered"] for group in groups) if groups else None,
            "match_status": "candidates", "candidate_identity_source_sha256": pool}


def test_full_source_span_hash_qualifies_without_symbol_shortcut():
    result = support.group_support([target()], [window()])
    assert result["covered"] and result["delivered_rank"] == 1
    assert result["covering_results"][0]["symbol"] != result["targets"][0]["symbol"]
    assert result["covering_results"][0]["covering_alternative_indexes"] == [0]


def test_partial_definition_does_not_receive_coverage():
    result = support.group_support([target()], [window(partial=True)])
    assert not result["covered"] and result["delivered_rank"] is None and result["covering_results"] == []


def test_or_group_and_dependency_provenance_are_explicit():
    result = support.group_support([target("absent.py"), target("actual.py")], [window(path="actual.py", origin="dependency")])
    assert result["covered"] and result["covered_only_by_dependencies"]
    assert result["covering_results"][0]["covering_alternative_indexes"] == [1]


@pytest.mark.parametrize("mutation", ["source", "bounds", "gold_hash"])
def test_literal_source_or_full_target_hash_corruption_is_rejected(mutation):
    result, gold = window(), target()
    if mutation == "source":
        result["source"] = result["source"].replace("value = 1", "value = 2")
    elif mutation == "bounds":
        result["end_line"] = 4
    else:
        gold["evidence_sha256"] = "wrong"
    with pytest.raises(ValueError):
        support.group_support([gold], [result])


def test_support_delta_keeps_lost_and_gained_intervals_even_when_net_complete_is_equal():
    keys = [("repo", "first"), ("repo", "second")]
    before = {keys[0]: case(covered=True), keys[1]: case(covered=False)}
    after = {keys[0]: case(covered=False, condition="dense_k5_b1"), keys[1]: case(covered=True, condition="dense_k5_b1")}
    for key in keys:
        before[key]["query_id"] = after[key]["query_id"] = key[1]
    result = support.compare(before, after, label="comparison")
    assert result["complete_losses"] == ["repo/first"] and result["complete_gains"] == ["repo/second"]
    assert result["covered_group_losses"] == result["covered_group_gains"] == 1
    assert result["cases"][0]["groups"][0]["before"]["targets"][0]["evidence_sha256"] == support.sha(SOURCE.encode())


def test_absent_candidates_are_not_complete_or_positive_coverage():
    before = {("repo", "q"): case(covered=False, absent=True)}
    after = {("repo", "q"): case(covered=False, absent=True, condition="dense_k5_b1")}
    result = support.compare(before, after, label="absent")
    assert result["covered_group_gains"] == result["covered_group_losses"] == 0
    assert result["complete_gains"] == result["complete_losses"] == []
    assert result["cases"][0]["before_complete"] is result["cases"][0]["after_complete"] is None
    assert result["cases"][0]["after_match_status"] == "candidates"


def test_query_or_target_drift_is_rejected_and_candidate_drift_is_reported():
    original = case(covered=True)
    changed = case(covered=True, pool="different")
    result = support.compare({("repo", "q"): original}, {("repo", "q"): changed}, label="drift")
    assert not result["cases"][0]["same_candidate_identity_source"]
    for mutation in ("query", "gold"):
        changed = deepcopy(original)
        if mutation == "query":
            changed["query"] = "new wording"
        else:
            changed["groups"][0]["targets"][0]["evidence_lines"] = [2, 3]
        with pytest.raises(ValueError):
            support.compare({("repo", "q"): original}, {("repo", "q"): changed}, label="drift")


def make_study(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    configs = [{"id": "dense", "retrieval": "dense", "shortlist": 50, "batch_size": 0},
               {"id": "dense_k5_b1", "retrieval": "dense", "shortlist": 5, "batch_size": 1}]
    schedule = [{"dataset": "repo", "query_id": "q", "condition": condition["id"], "repetition": repetition}
                for repetition in (1, 2, 3) for condition in configs]
    plan = {"stage": "grid", "conditions": configs, "repetitions": 3, "primary_quality_repetition": 1,
            "sources": [{"id": "repo", "queries": [{"query_id": "q", "kind": "present", "groups": [[target()]]}]}],
            "schedule": schedule}
    (study / "frozen-plan.json").write_text(json.dumps(plan))
    with gzip.open(study / "searches.jsonl.gz", "wt") as stream:
        for identity in schedule:
            covered = identity["repetition"] > 1 or identity["condition"] == "dense"
            stream.write(json.dumps({"identity": identity, "status": "completed", "query": "Find a numeric return",
                "evidence_ranks": [1 if covered else None], "candidate_identity_source_sha256": "same",
                "response": {"results": [window(partial=not covered)], "match_status": "candidates"}}) + "\n")
    (study / "completion.json").write_text(json.dumps({"complete": True, "embedding_owner_closed": True}))
    (study / "supervisor.json").write_text(json.dumps({"main_exit_code": 0, "resident_exit_code": 0,
        "main_owned_group_stopped": True, "resident_owned_group_stopped": True, "forced_kill": False,
        "timed_out": False, "private_auth_token_removed": True}))
    names = ("frozen-plan.json", "searches.jsonl.gz", "completion.json", "supervisor.json")
    hashes = {name: support.file_hash(study / name) for name in names}
    (study / "independent-audit.json").write_text(json.dumps({"checks_passed": True, "artifact_sha256": hashes,
                                                           "frozen_plan_sha256": hashes["frozen-plan.json"]}))
    return study


def test_only_first_pass_is_explained_not_better_later_repetition(tmp_path):
    result = support.study_cases(make_study(tmp_path))
    assert len(result["cases"]) == 2
    assert result["cases"][("dense", "repo", "q")]["complete"] is True
    assert result["cases"][("dense_k5_b1", "repo", "q")]["complete"] is False


def test_certified_artifact_corruption_is_rejected_before_reading_support(tmp_path):
    study = make_study(tmp_path)
    (study / "completion.json").write_text("{}")
    with pytest.raises(ValueError, match="Certified artifact changed"):
        support.study_cases(study)


def test_diagnostic_p95_uses_nearest_rank_without_implied_repetitions():
    assert support.distribution([8, 1, 7, 2, 6, 3, 5, 4]) == {
        "samples": 8, "median_s": 4.5, "p95_s": 8, "min_s": 1, "max_s": 8}


def test_fresh_decision_must_be_bound_before_cross_family_winner_comparison(monkeypatch, tmp_path):
    baseline, winner = case(covered=False), case(covered=True, condition="dense_k5_b1")
    actual = {"plan": {"stage": "fresh", "decision_inputs": {}}, "configs": {
        "dense": {"id": "dense", "retrieval": "dense", "shortlist": 50, "batch_size": 0},
        "dense_k5_b1": {"id": "dense_k5_b1", "retrieval": "dense", "shortlist": 5, "batch_size": 1}},
        "cases": {("dense", "repo", "q"): baseline, ("dense_k5_b1", "repo", "q"): winner}, "binding": {}}
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps({"ready_for_fresh_validation": True, "selected_config": actual["configs"]["dense_k5_b1"]}))
    monkeypatch.setattr(support, "ROOT", tmp_path)
    monkeypatch.setattr(support, "study_cases", lambda _: actual)
    with pytest.raises(ValueError, match="pre-retrieval selection"):
        support.explain(tmp_path, decision_path=decision)
    actual["plan"]["decision_inputs"]["decision.json"] = support.file_hash(decision)
    result = support.explain(tmp_path, decision_path=decision)
    assert result["comparisons"][0]["complete_gains"] == ["repo/q"]
