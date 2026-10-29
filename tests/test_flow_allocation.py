"""Invented-source contracts for ranked-prefix shared-flow protection."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from unittest.mock import patch

import pytest

from semantic_search import flow_allocation as flow, source_allocation as source
from semantic_search.corpus import Snippet
from semantic_search.relations import ReferenceGraph
from semantic_search.selection import definition_context


QUERY = "alpha beta gamma and delta epsilon zeta"


def item(number, *, path="src/unit.py", symbol=None):
    text = f"def unit_{number}():\n    return {number}\n"
    return Snippet(str(number), path, symbol or f"unit_{number}", number * 10 + 1,
                   number * 10 + 2, text, hashlib.sha256(text.encode()).hexdigest())


def keys(items):
    return {row.id: (row.path, row.symbol, row.start_line, row.end_line) for row in items}


class Graph:
    def __init__(self, items, edges):
        self.windows = {(row.path, row.symbol): [row] for row in items}
        self.edges = {}
        for caller, callee in edges:
            self.edges.setdefault((caller.path, caller.symbol), []).append((callee.path, callee.symbol))

    def dependencies(self, row, limit=1):
        return [self.windows[target][0] for target in self.edges.get((row.path, row.symbol), ())[:limit]]


def plan(items=None, *, graph=None, definition_keys=None, query=QUERY, limit=8,
         ambiguous_symbols=None, include_dependencies=True):
    items = items or [item(number) for number in range(1, 10)]
    graph = graph or Graph(items, [(items[-1], items[0])])
    return flow.flow_allocation_plan(
        [(row, 1 / rank) for rank, row in enumerate(items, 1)], query=query,
        max_results=limit, include_dependencies=include_dependencies, references=graph,
        definition_keys=keys(items) if definition_keys is None else definition_keys,
        ambiguous_symbols=ambiguous_symbols)


def ids(pairs):
    return [row.id for row, _ in pairs]


def literal(row, **overrides):
    return {"id": row.id, "path": row.path, "symbol": row.symbol, "start_line": row.start_line,
            "end_line": row.end_line, "window_end_line": row.end_line, "source": row.text,
            "source_hash": row.content_hash, "content_hash": row.content_hash,
            "origin": "retrieval", "related_to": None, "retrieval_score": .1,
            "h2o_score": None, "qwen_score": None, "truncated": False, **overrides}


def response(rows):
    return {"query": QUERY, "results": deepcopy(rows), "budget_exhausted": False,
            "total_source_chars": sum(len(row["source"]) for row in rows)}


def test_no_shared_caller_retains_original_graph_gain_and_scores():
    items = [item(number) for number in range(1, 10)]
    selected, eligible, origins, receipt = plan(items)
    assert ids(selected) == [str(number) for number in range(1, 10)]
    assert ids(eligible) == ["1", "2", "3", "4", "5", "9", "6", "7"]
    assert eligible[-3][1] == 1 / 9
    assert receipt["admitted_ids"] == ["9"] and receipt["flow_keys"] == []
    assert receipt["candidate_trials"][0]["orchestrator_keys"] == []
    assert receipt["termination"] == "candidate_pool_exhausted"
    assert eligible.source_plan == receipt["guard_plan"]
    assert receipt["guard_plan"]["producer_schema"] == receipt["schema"] == "shared_flow_allocation_v1"
    assert receipt["guard_plan"]["schema"] == "source_graph_allocation_v1"
    assert receipt["guard_plan"]["mode"] == "source_graph" and receipt["mode"] == "shared_flow"
    assert all(origin == ("retrieval", None) for origin in origins.values())


def test_shared_parent_protects_eligible_sibling_without_retrieving_parent():
    items = [item(number) for number in range(1, 10)]
    parent = item(30, path="src/parent.py")
    graph = Graph(items + [parent], [(items[8], items[0]), (parent, items[8]), (parent, items[7])])
    _, eligible, _, receipt = plan(items, graph=graph, definition_keys=keys(items + [parent]))
    assert ids(eligible) == ["1", "2", "3", "4", "5", "8", "9", "6"]
    assert receipt["guard_plan"]["evicted_ids"] == ["7"]
    assert "30" not in ids(eligible) and "8" in receipt["guard_plan"]["protected_baseline_ids"]
    assert receipt["candidate_trials"][0]["orchestrator_keys"] == [list(keys([parent])["30"])]
    assert {tuple(key) for key in receipt["flow_keys"]} == {keys(items + [parent])[identifier] for identifier in ("8", "9", "30")}
    assert len(receipt["flow_witnesses"]) == 2


def test_baseline_parent_itself_and_all_its_unique_direct_callees_are_protected():
    items = [item(number) for number in range(1, 10)]
    parent = items[6]
    graph = Graph(items, [(items[8], items[0]), (parent, items[8]), (parent, items[5])])
    _, eligible, _, receipt = plan(items, graph=graph)
    assert ids(eligible) == ["1", "2", "3", "4", "5", "6", "7", "9"]
    assert receipt["guard_plan"]["protected_baseline_ids"] == ["1", "2", "3", "4", "5", "6", "7"]
    assert receipt["guard_plan"]["evicted_ids"] == ["8"]


def test_first_capacity_block_stops_prefix_and_does_not_try_lower_candidate():
    items = [item(number) for number in range(1, 11)]
    helper_a, helper_b, parent = item(20), item(21), item(30)
    graph = Graph(items + [helper_a, helper_b, parent], [
        (items[0], helper_a), (items[0], helper_b),
        (items[8], items[0]), (items[9], items[1]),
        (parent, items[8]), (parent, items[5])])
    selected, eligible, origins, receipt = plan(items, graph=graph,
                                               definition_keys=keys(items + [helper_a, helper_b, parent]))
    assert receipt["base_plan"]["available_candidate_ids"] == ["9", "10"]
    assert receipt["admitted_ids"] == [] and receipt["blocked_candidate_id"] == "9"
    assert len(receipt["candidate_trials"]) == 1
    assert receipt["candidate_trials"][0]["exchangeable_baseline_ids"] == []
    assert receipt["candidate_trials"][0]["stop_reason"] == "insufficient_exchangeable_slots"
    assert receipt["guard_plan"]["protected_baseline_ids"] == receipt["base_protected_ids"]
    assert receipt["flow_keys"] == receipt["flow_witnesses"] == []
    assert receipt["termination"] == "capacity_blocked" and not receipt["accepted"]
    assert receipt["fallback_reason"] == "shared_flow_capacity_blocked"
    assert ids(eligible) == ["1", "2", "3", "4", "5", "20", "21", "6"]
    assert origins["20"] == origins["21"] == ("dependency", "1")
    assert all(score == 1 for row, score in eligible if row.id in {"20", "21"})
    assert ids(selected) == [str(number) for number in range(1, 11)]


def test_later_block_keeps_accepted_prefix_and_rolls_back_blocked_protection():
    items = [item(number) for number in range(1, 12)]
    parent = item(30)
    graph = Graph(items + [parent], [(items[8], items[0]), (items[9], items[1]), (items[10], items[2]),
                                   (parent, items[9]), (parent, items[5]), (parent, items[6]), (parent, items[7])])
    _, eligible, _, receipt = plan(items, graph=graph, definition_keys=keys(items + [parent]))
    assert receipt["admitted_ids"] == ["9"] and receipt["blocked_candidate_id"] == "10"
    assert [trial["candidate_id"] for trial in receipt["candidate_trials"]] == ["9", "10"]
    assert ids(eligible) == ["1", "2", "3", "4", "5", "9", "6", "7"]
    assert receipt["flow_keys"] == receipt["flow_witnesses"] == []
    assert receipt["guard_plan"]["protected_baseline_ids"] == ["1", "2", "3", "4", "5"]
    assert receipt["guard_plan"]["neighbor_keys"] == receipt["base_plan"]["neighbor_keys"]
    assert receipt["termination"] == "capacity_blocked" and receipt["accepted"]
    assert receipt["fallback_reason"] is None


def test_at_most_two_ranked_candidates_are_admitted_and_later_candidates_not_tried():
    items = [item(number) for number in range(1, 13)]
    graph = Graph(items, [(row, items[0]) for row in reversed(items[8:])])
    _, eligible, _, receipt = plan(items, graph=graph)
    assert receipt["admitted_ids"] == ["9", "10"]
    assert [trial["raw_rank"] for trial in receipt["candidate_trials"]] == [9, 10]
    assert ids(eligible) == ["1", "2", "3", "4", "5", "9", "10", "6"]
    assert receipt["termination"] == "max_additions"


def test_previously_admitted_shared_flow_protection_counts_against_later_capacity():
    items = [item(number) for number in range(1, 11)]
    parent = item(30)
    graph = Graph(items + [parent], [(items[8], items[0]), (items[9], items[1]),
                                   (parent, items[8]), (parent, items[5])])
    _, eligible, _, receipt = plan(items, graph=graph, definition_keys=keys(items + [parent]))
    assert receipt["admitted_ids"] == ["9", "10"]
    assert receipt["candidate_trials"][1]["exchangeable_baseline_ids"] == ["7", "8"]
    assert receipt["candidate_trials"][1]["required_slots"] == 2
    assert ids(eligible) == ["1", "2", "3", "4", "5", "6", "9", "10"]


def test_original_dependency_score_is_preserved_when_it_also_has_a_raw_score():
    items = [item(number) for number in range(1, 10)]
    graph = Graph(items, [(items[0], items[5]), (items[8], items[0])])
    _, eligible, origins, receipt = plan(items, graph=graph)
    assert origins["6"] == ("dependency", "1")
    assert dict((row.id, score) for row, score in eligible)["6"] == 1
    assert receipt["base_plan"]["dependency_owners"] == {"6": "1"}


@pytest.mark.parametrize("failure", ["parent_unknown", "parent_ambiguous", "parent_cached_ambiguous", "sibling_ambiguous"])
def test_unknown_or_ambiguous_flow_nodes_supply_no_invented_protection(failure):
    items = [item(number) for number in range(1, 10)]
    parent = item(30)
    graph = Graph(items + [parent], [(items[8], items[0]), (parent, items[8]), (parent, items[7])])
    definition_keys = keys(items + [parent])
    ambiguous = None
    if failure == "parent_unknown":
        definition_keys.pop("30")
    elif failure == "parent_ambiguous":
        definition_keys["duplicate"] = (parent.path, parent.symbol, 999, 1001)
    elif failure == "parent_cached_ambiguous":
        ambiguous = {(parent.path, parent.symbol)}
    else:
        definition_keys["duplicate"] = (items[7].path, items[7].symbol, 999, 1001)
    _, eligible, _, receipt = plan(items, graph=graph, definition_keys=definition_keys, ambiguous_symbols=ambiguous)
    assert receipt["admitted_ids"] == ["9"]
    assert "8" not in receipt["guard_plan"]["protected_baseline_ids"]
    if failure != "sibling_ambiguous":
        assert receipt["flow_keys"] == receipt["flow_witnesses"] == []
    else:
        assert list(definition_keys["8"]) not in receipt["flow_keys"]
    assert "9" in ids(eligible)


def test_no_transitive_or_factory_receiver_relationship_is_inferred():
    a, candidate, sibling, parent = item(1), item(9), item(8), item(30)
    texts = {
        "src/unit.py": "def unit_1():\n    return 1\n\ndef unit_9():\n    return unit_1()\n\ndef unit_8():\n    return 8\n\ndef unit_30(factory):\n    receiver = factory()\n    receiver.unit_9()\n    receiver.unit_8()\n"}
    items = [item(number) for number in range(1, 10)]
    graph = ReferenceGraph(texts, items + [parent])
    _, _, _, receipt = plan(items, graph=graph, definition_keys=keys(items + [parent]))
    assert receipt["admitted_ids"] == ["9"]
    assert receipt["flow_keys"] == receipt["flow_witnesses"] == []


def test_flow_does_not_follow_grandparents_or_add_unretrieved_siblings():
    items = [item(number) for number in range(1, 10)]
    parent, grandparent, remote = item(30), item(31), item(32)
    graph = Graph(items + [parent, grandparent, remote], [
        (items[8], items[0]), (parent, items[8]), (parent, remote), (grandparent, parent), (grandparent, items[7])])
    _, eligible, _, receipt = plan(items, graph=graph, definition_keys=keys(items + [parent, grandparent, remote]))
    flow_keys = {tuple(key) for key in receipt["flow_keys"]}
    assert keys([remote])["32"] in flow_keys
    assert keys([grandparent])["31"] not in flow_keys
    assert keys(items)["8"] not in flow_keys
    assert all(identifier not in ids(eligible) for identifier in ("30", "31", "32"))


@pytest.mark.parametrize("query", ["one implementation", "alpha beta gamma and return it"])
def test_inactive_literal_queries_return_original_definition_control(query):
    items = [item(number) for number in range(1, 10)]
    graph = Graph(items, [(items[-1], items[0])])
    selected, eligible, origins, receipt = plan(items, graph=graph, query=query)
    assert (selected, eligible, origins) == definition_context(
        [(row, 1 / rank) for rank, row in enumerate(items, 1)], query=query, max_results=8,
        include_dependencies=True, references=graph, definition_keys=keys(items))
    assert not receipt["accepted"] and receipt["termination"] == "inactive_base_plan"
    assert receipt["candidate_trials"] == []


@pytest.mark.parametrize("limit", [0, 1, 3, 5])
def test_tiny_result_caps_return_control_without_flow_expansion(limit):
    _, eligible, _, receipt = plan(limit=limit)
    assert len(eligible) <= limit and not receipt["accepted"]
    assert receipt["candidate_trials"] == []


def test_same_line_js_byte_identity_is_retained_without_collapsing_distinct_definitions():
    items = [replace(item(number), path="src/unit.ts") for number in range(1, 10)]
    parent = replace(item(30), path="src/parent.ts")
    definition_keys = {identifier: (*key, index * 20) for index, (identifier, key) in enumerate(keys(items + [parent]).items())}
    graph = Graph(items + [parent], [(items[8], items[0]), (parent, items[8]), (parent, items[7])])
    _, _, _, receipt = plan(items, graph=graph, definition_keys=definition_keys)
    assert all(len(key) == 5 for key in receipt["flow_keys"])
    assert receipt["flow_witnesses"][0]["orchestrator_key"] == list(definition_keys["30"])


def test_one_actual_base_helper_call_with_truthful_immutable_receipts():
    with patch.object(source, "source_allocation_plan", wraps=source.source_allocation_plan) as base:
        selected, eligible, _, receipt = plan()
    assert base.call_count == 1
    assert receipt["base_plan"]["mode"] == "source_graph" and "producer_schema" not in receipt["base_plan"]
    actual = eligible.source_plan
    actual["added_ids"].clear()
    receipt["guard_plan"]["added_ids"].clear()
    assert eligible.source_plan["added_ids"] == ["9"]
    assert len(selected) == 9
    json.dumps(eligible.source_plan, allow_nan=False)


def test_backfilled_shared_sibling_and_its_containing_reference_row_are_protected_after_packing():
    items = [item(number) for number in range(1, 11)]
    parent = item(30)
    graph = Graph(items + [parent], [(items[8], items[0]), (parent, items[8]), (parent, items[9])])
    _, _, _, receipt = plan(items, graph=graph, definition_keys=keys(items + [parent]))
    sibling = items[9]
    outer_text = "# parent\n" + sibling.text
    outer = replace(sibling, id="outer", symbol="outer", start_line=sibling.start_line - 1,
                    text=outer_text, content_hash=hashlib.sha256(outer_text.encode()).hexdigest())
    reference, proposal = response([literal(outer)]), response([literal(items[0])])
    chosen, guard = source.guard_source_plan(reference, proposal, receipt["guard_plan"])
    assert guard["protected_ids"] == ["outer"] and not guard["accepted"]
    assert chosen == reference


@pytest.mark.parametrize("alteration", ["clip", "source_hash", "content_hash", "overlay_presence", "owner"])
def test_unchanged_source_guard_rejects_actual_flow_sibling_source_or_provenance_loss(alteration):
    items = [item(number) for number in range(1, 10)]
    parent = item(30)
    graph = Graph(items + [parent], [(items[8], items[0]), (parent, items[8]), (parent, items[7])])
    receipt = plan(items, graph=graph, definition_keys=keys(items + [parent]))[3]
    reference = response([literal(items[7])])
    proposal = deepcopy(reference)
    row = proposal["results"][0]
    if alteration == "clip":
        row["source"] = row["source"].splitlines(keepends=True)[0]
        row["end_line"] -= 1
        row["source_hash"] = hashlib.sha256(row["source"].encode()).hexdigest()
    elif alteration == "overlay_presence":
        row["buffer_hash"] = None
    elif alteration == "owner":
        row["related_to"] = "invented"
    else:
        row[alteration] = "0" * 64
    chosen, guard = source.guard_source_plan(reference, proposal, receipt["guard_plan"])
    assert chosen == reference and not guard["accepted"]


def test_new_wrapper_runs_exactly_one_flow_planner_and_changes_no_defaults():
    items = [item(number) for number in range(1, 10)]
    with patch.object(flow, "flow_allocation_plan", wraps=flow.flow_allocation_plan) as planner:
        selected, eligible, _ = flow.shared_flow_context(
            [(row, .5) for row in items], query=QUERY, max_results=8, include_dependencies=True,
            references=Graph(items, [(items[-1], items[0])]), definition_keys=keys(items))
    assert planner.call_count == 1 and len(selected) == 9 and "9" in ids(eligible)
    from semantic_search import selection
    assert selection.select_context is selection.ranked_context
    assert selection.postprocess_context is selection.unchanged_context


def test_unknown_mode_fails_before_base_helper():
    with patch.object(source, "source_allocation_plan", wraps=source.source_allocation_plan) as base:
        with pytest.raises(ValueError, match="Unknown shared flow allocation mode"):
            flow.flow_allocation_plan([], mode="invented", query=QUERY, max_results=8,
                                      include_dependencies=True, references=None)
    assert base.call_count == 0
