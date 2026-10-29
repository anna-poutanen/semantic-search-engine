"""Independent shared-flow admission and provenance adversarial checks."""
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value
audit = module("flow_audit", "scripts/audit_flow_allocation.py")
invented = module("flow_old_independent_cases", "tests/test_source_allocation_audit.py")
QUERY, pool, label = invented.QUERY, invented.pool, invented.label


def plan(*, edges=None, origins=None, keys=None, query=QUERY):
    pairs, default_keys, default_origins = pool(12)
    if edges is None:
        edges = {label(pairs[8]): [label(pairs[0])], label(pairs[9]): [label(pairs[1])], label(pairs[10]): [label(pairs[2])]}
    return audit.planner(pairs, pairs, pairs[:8], origins or default_origins, keys or default_keys,
        edges, [], query=query)


def test_independent_admission_uses_same_pool_and_at_most_two_original_ranked_neighbors():
    selected, eligible, origins, receipt = plan()
    assert [row["snippet"]["id"] for row in selected] == list(map(str, range(1, 13)))
    assert receipt["admitted_ids"] == ["9", "10"]
    assert [trial["raw_rank"] for trial in receipt["candidate_trials"]] == [9, 10]
    assert receipt["termination"] == "max_additions"
    assert [row["snippet"]["id"] for row in eligible] == ["1", "2", "3", "4", "5", "9", "10", "6"]
    assert receipt["guard_plan"]["producer_schema"] == receipt["schema"] == "shared_flow_allocation_v1"
    assert receipt["guard_plan"]["mode"] == "source_graph" and receipt["mode"] == "shared_flow"


def test_shared_caller_and_sibling_are_protected_without_retrieving_them():
    pairs, keys, origins = pool(12)
    edges = {label(pairs[8]): [label(pairs[0])], label(pairs[11]): [label(pairs[8]), label(pairs[7])]}
    _, eligible, _, receipt = plan(edges=edges)
    assert receipt["admitted_ids"] == ["9"]
    assert receipt["guard_plan"]["protected_baseline_ids"] == ["1", "2", "3", "4", "5", "8"]
    assert receipt["guard_plan"]["evicted_ids"] == ["7"]
    assert {tuple(key) for key in receipt["flow_keys"]} == {tuple(keys[name]) for name in ("8", "9", "12")}
    assert all(row["snippet"]["id"] != "12" for row in eligible)
    assert len(receipt["flow_witnesses"]) == 2


def test_capacity_block_stops_at_strongest_candidate_and_never_skips_to_weaker():
    pairs, keys, origins = pool(12)
    origins["7"] = ["dependency", "6"]
    edges = {label(pairs[8]): [label(pairs[0])], label(pairs[9]): [label(pairs[1])],
        label(pairs[11]): [label(pairs[8]), label(pairs[7])]}
    _, eligible, _, receipt = plan(edges=edges, origins=origins)
    assert receipt["base_plan"]["available_candidate_ids"] == ["9", "10"]
    assert receipt["blocked_candidate_id"] == "9" and receipt["admitted_ids"] == []
    assert len(receipt["candidate_trials"]) == 1 and receipt["candidate_trials"][0]["exchangeable_baseline_ids"] == []
    assert receipt["flow_keys"] == receipt["flow_witnesses"] == []
    assert eligible == pairs[:8] and receipt["fallback_reason"] == "shared_flow_capacity_blocked"


def test_later_block_keeps_prefix_but_rolls_back_every_blocked_trial_fact():
    pairs, keys, origins = pool(12)
    edges = {label(pairs[8]): [label(pairs[0])], label(pairs[9]): [label(pairs[1])], label(pairs[10]): [label(pairs[2])],
        label(pairs[11]): [label(pairs[9]), label(pairs[5]), label(pairs[6]), label(pairs[7])]}
    _, eligible, _, receipt = plan(edges=edges)
    assert receipt["admitted_ids"] == ["9"] and receipt["blocked_candidate_id"] == "10"
    assert [trial["candidate_id"] for trial in receipt["candidate_trials"]] == ["9", "10"]
    assert receipt["candidate_trials"][-1]["flow_keys"]
    assert receipt["flow_keys"] == receipt["flow_witnesses"] == []
    assert receipt["guard_plan"]["protected_baseline_ids"] == ["1", "2", "3", "4", "5"]
    assert receipt["guard_plan"]["neighbor_keys"] == receipt["base_plan"]["neighbor_keys"]
    assert receipt["termination"] == "capacity_blocked" and receipt["accepted"] is True
    assert [row["snippet"]["id"] for row in eligible] == ["1", "2", "3", "4", "5", "9", "6", "7"]


@pytest.mark.parametrize("ambiguity", ["unknown_parent", "ambiguous_parent", "ambiguous_sibling"])
def test_unknown_or_nonunique_graph_labels_supply_no_extra_flow_protection(ambiguity):
    pairs, keys, origins = pool(12)
    edges = {label(pairs[8]): [label(pairs[0])], label(pairs[11]): [label(pairs[8]), label(pairs[7])]}
    if ambiguity == "unknown_parent":
        del keys["12"]
    elif ambiguity == "ambiguous_parent":
        keys["shadow"] = [*keys["12"][:2], 2, 3]
    else:
        keys["shadow"] = [*keys["8"][:2], 2, 3]
    _, _, _, receipt = plan(edges=edges, keys=keys)
    assert "8" not in receipt["guard_plan"]["protected_baseline_ids"]
    if ambiguity != "ambiguous_sibling":
        assert receipt["flow_keys"] == receipt["flow_witnesses"] == []


def test_flow_does_not_expand_through_grandparents():
    pairs, _, _ = pool(12)
    edges = {label(pairs[8]): [label(pairs[0])], label(pairs[10]): [label(pairs[8])],
        label(pairs[11]): [label(pairs[10]), label(pairs[7])]}
    _, _, _, receipt = plan(edges=edges)
    assert "8" not in receipt["guard_plan"]["protected_baseline_ids"]
    assert all(witness["orchestrator_key"][:2] != list(label(pairs[11])) for witness in receipt["flow_witnesses"])


def planned_row():
    pairs, keys, origins = pool(12)
    edges = {label(pairs[8]): [label(pairs[0])]}
    selected, eligible, planned_origins, receipt = plan(edges=edges)
    baseline = {"stages": {"ranked": pairs, "selected": pairs, "eligible": pairs[:8], "origins": origins}}
    row = {"selector": "shared_flow", "stages": {"ranked": pairs, "selected": selected, "eligible": eligible,
        "origins": planned_origins, "ambiguous_symbols": []}, "response": {"query": QUERY},
        "flow_allocation_receipt": receipt, "base_allocation_receipt": receipt["base_plan"], "base_planner_calls": 1,
        "selection_plan": receipt["guard_plan"], "guard_plan": receipt["guard_plan"]}
    return row, baseline, {"definition_keys": keys, "graph_edges": edges}, deepcopy(receipt["base_plan"])


@pytest.mark.parametrize("tamper", ["raw_order", "score", "prefix", "witness", "projection", "base", "base_calls", "bool_calls", "integer_bool"])
def test_planning_verifier_rejects_forged_stages_witnesses_or_actual_base(tamper):
    row, baseline, fixture, original = planned_row()
    audit.verify_planning(row, baseline, fixture, original)
    row = deepcopy(row)
    if tamper == "raw_order":
        row["stages"]["ranked"].reverse()
    elif tamper == "score":
        row["stages"]["eligible"][-1]["score"] = 999
    elif tamper == "prefix":
        row["flow_allocation_receipt"]["admitted_ids"] = ["10"]
    elif tamper == "witness":
        row["flow_allocation_receipt"]["candidate_trials"][0]["raw_rank"] = 99
    elif tamper == "projection":
        row["selection_plan"]["producer_schema"] = "old_producer"
    elif tamper == "base":
        row["base_allocation_receipt"]["raw_ranks"]["9"] = 1
    elif tamper == "integer_bool":
        row["flow_allocation_receipt"]["accepted"] = 1
    else:
        row["base_planner_calls"] = True if tamper == "bool_calls" else 2
    with pytest.raises(ValueError):
        audit.verify_planning(row, baseline, fixture, original)


def test_independent_planner_matches_actual_invented_source_without_importing_it_inside_auditor():
    unit = module("flow_producer_invented_pool", "tests/test_flow_allocation.py")
    items = [unit.item(number) for number in range(1, 12)]
    parent = unit.item(30)
    graph = unit.Graph(items + [parent], [(items[8], items[0]), (items[9], items[1]),
        (parent, items[9]), (parent, items[5]), (parent, items[6]), (parent, items[7])])
    selected, eligible, origins, receipt = unit.plan(items, graph=graph, definition_keys=unit.keys(items + [parent]))
    from semantic_search.selection import definition_context
    pairs = [(item, 1 / number) for number, item in enumerate(items, 1)]
    before = definition_context(pairs, query=QUERY, max_results=8, include_dependencies=True, references=graph,
        definition_keys=unit.keys(items + [parent]))
    serial = lambda pairs: [{"snippet": asdict(item), "score": score} for item, score in pairs]
    independent = audit.planner(serial(pairs), serial(before[0]), serial(before[1]), before[2],
        unit.keys(items + [parent]), graph.edges, [], query=QUERY)
    assert independent == (serial(selected), serial(eligible), origins, receipt)


def test_summary_profile_preserves_old_auditor_configuration():
    original = (audit.base.CONDITIONS, audit.base.NEGATIVES, audit.base.CANDIDATES)
    with audit.report_profile():
        assert audit.base.CANDIDATES == ("shared_flow",)
        assert len(audit.base.NEGATIVES) == 4
    assert (audit.base.CONDITIONS, audit.base.NEGATIVES, audit.base.CANDIDATES) == original


@pytest.mark.parametrize("name", ["semantic_search.flow_allocation", "semantic_search.source_allocation", "semantic_search.backend",
    "replay_flow_allocation", "semantic_search.embeddings", "numpy"])
def test_independent_import_guard_blocks_all_candidate_and_model_paths(name):
    with pytest.raises(RuntimeError, match="forbids"):
        audit.RefuseProduction().find_spec(name)


@pytest.mark.parametrize("name", sorted(audit.base.ALLOWED_GRAPH_MODULES))
def test_frozen_graph_parser_trust_boundary_is_explicit(name):
    assert audit.RefuseProduction().find_spec(name) is None


def test_cli_help_never_imports_producer():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/audit_flow_allocation.py"), "--help"], text=True, capture_output=True)
    assert result.returncode == 0 and "--corpus-root" in result.stdout and "--results" in result.stdout
