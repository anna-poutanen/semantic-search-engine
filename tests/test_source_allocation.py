"""Invented-source contracts for graph allocation and post-packing protection."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from unittest.mock import Mock

import pytest

from semantic_search import source_allocation as allocation
from semantic_search.cancellation import SearchCancelled
from semantic_search.context import apply_token_budget, compact_result, count_context
from semantic_search.corpus import Snippet
from semantic_search.relations import ReferenceGraph
from semantic_search.selection import definition_context
from semantic_search.source import physical_lines


QUERY = "alpha beta gamma and delta epsilon zeta"


def snippet(number, *, path="src/unit.py", symbol=None, source=None):
    source = source or f"def unit_{number}():\n    return {number}\n"
    start = number * 10 + 1
    return Snippet(str(number), path, symbol or f"unit_{number}", start,
                   start + len(physical_lines(source)) - 1, source,
                   hashlib.sha256(source.encode()).hexdigest())


def keys(items):
    return {item.id: (item.path, item.symbol, item.start_line, item.end_line) for item in items}


class Graph:
    def __init__(self, items, edges=()):
        self.windows = {(item.path, item.symbol): [item] for item in items}
        self.edges = {}
        for source, target in edges:
            self.edges.setdefault((source.path, source.symbol), []).append((target.path, target.symbol))

    def dependencies(self, item, limit=1):
        return [self.windows[target][0] for target in self.edges.get((item.path, item.symbol), ())[:limit]]


def plan(items=None, *, mode="source_graph", query=QUERY, graph=None, limit=8,
         definition_keys=None, ambiguous_symbols=None, include_dependencies=True):
    items = items or [snippet(number) for number in range(1, 10)]
    graph = graph or Graph(items, [(items[-1], items[0])])
    return allocation.source_allocation_plan(
        [(item, 1 / rank) for rank, item in enumerate(items, 1)], mode=mode, query=query,
        max_results=limit, include_dependencies=include_dependencies, references=graph,
        definition_keys=keys(items) if definition_keys is None else definition_keys,
        ambiguous_symbols=ambiguous_symbols)


def ids(pairs):
    return [item.id for item, _ in pairs]


def source_row(item, **overrides):
    return {"id": item.id, "path": item.path, "symbol": item.symbol,
            "start_line": item.start_line, "end_line": item.end_line,
            "window_end_line": item.end_line, "source": item.text,
            "source_hash": item.content_hash, "content_hash": item.content_hash,
            "origin": "retrieval", "related_to": None, "truncated": False,
            "retrieval_score": .5, "h2o_score": None, "qwen_score": None, **overrides}


def response(rows, *, budget=None):
    value = {"query": QUERY, "scope": {"source_root": "src", "meaning": "invented fixture"},
             "results": deepcopy(rows), "total_source_chars": sum(len(row["source"]) for row in rows),
             "budget_exhausted": False, "match_status": "candidates_found"}
    if budget is not None:
        value = apply_token_budget(value, budget)
    return value


@pytest.mark.parametrize("mode", allocation.MODES)
def test_direct_caller_replaces_only_last_unprotected_result(mode):
    items = [snippet(number) for number in range(1, 10)]
    selected, eligible, origins, receipt = plan(items, mode=mode)
    assert ids(selected) == [str(number) for number in range(1, 10)]
    assert ids(eligible) == ["1", "2", "3", "4", "5", "9", "6", "7"]
    assert receipt["protected_baseline_ids"] == ["1", "2", "3", "4", "5"]
    assert receipt["available_candidate_ids"] == receipt["added_ids"] == ["9"]
    assert receipt["evicted_ids"] == ["8"] and receipt["accepted"]
    assert receipt["graph_witnesses"] == [{
        "anchor_id": "1", "anchor_key": list(keys(items)["1"]), "direction": "caller",
        "neighbor_key": list(keys(items)["9"]), "source_key": list(keys(items)["9"]),
        "target_key": list(keys(items)["1"])}]
    assert all(origin == ("retrieval", None) for origin in origins.values())
    assert dict((item.id, score) for item, score in eligible)["9"] == 1 / 9
    assert eligible.source_plan == receipt


def test_both_modes_have_the_identical_proposal_except_protection_scope():
    left, right = plan(mode="source_graph"), plan(mode="source_graph_all")
    assert left[:3] == right[:3]
    a, b = left[3], right[3]
    assert a.pop("mode") != b.pop("mode")
    assert a == b


def test_at_most_two_additions_in_raw_rank_order_without_rescoring():
    items = [snippet(number) for number in range(1, 13)]
    graph = Graph(items, [(item, items[0]) for item in reversed(items[8:])])
    _, eligible, _, receipt = plan(items, graph=graph)
    assert ids(eligible) == ["1", "2", "3", "4", "5", "9", "10", "6"]
    assert receipt["available_candidate_ids"] == ["9", "10", "11", "12"]
    assert receipt["added_ids"] == ["9", "10"] and receipt["evicted_ids"] == ["7", "8"]


def test_baseline_graph_neighbors_are_protected_and_stay_in_original_relative_order():
    items = [snippet(number) for number in range(1, 10)]
    graph = Graph(items, [(items[5], items[0]), (items[8], items[0])])
    _, eligible, _, receipt = plan(items, graph=graph)
    assert receipt["protected_baseline_ids"] == ["1", "2", "3", "4", "5", "6"]
    assert ids(eligible) == ["1", "2", "3", "4", "5", "6", "9", "7"]


def test_dependencies_and_original_owners_keep_their_inherited_scores_and_provenance():
    items = [snippet(number) for number in range(1, 10)]
    helpers = [snippet(number, path="src/helper.py") for number in (20, 21)]
    graph = Graph(items + helpers, [(items[0], helper) for helper in helpers] + [(items[8], items[0])])
    selected, eligible, origins, receipt = plan(items, graph=graph, definition_keys=keys(items + helpers))
    assert ids(selected) == [str(number) for number in range(1, 10)]
    assert ids(eligible) == ["1", "2", "3", "4", "5", "20", "21", "9"]
    assert receipt["dependency_owners"] == {"20": "1", "21": "1"}
    assert receipt["protected_baseline_ids"] == ["1", "2", "3", "4", "5", "20", "21"]
    assert origins["20"] == origins["21"] == ("dependency", "1")
    assert dict((item.id, score) for item, score in eligible)["20"] == 1
    assert origins.get("9", ("retrieval", None)) == ("retrieval", None)


def test_later_real_callee_can_be_retrieved_without_claiming_new_dependency_expansion():
    items = [snippet(number) for number in range(1, 10)]
    helpers = [snippet(number, path="src/helper.py") for number in (20, 21)]
    graph = Graph(items + helpers, [(items[0], target) for target in helpers + [items[8]]])
    _, eligible, origins, receipt = plan(items, graph=graph, definition_keys=keys(items + helpers))
    assert receipt["added_ids"] == ["9"] and ids(eligible)[-1] == "9"
    assert origins.get("9", ("retrieval", None)) == ("retrieval", None)
    assert len(receipt["dependency_owners"]) == 2


@pytest.mark.parametrize("query", ["one ordinary implementation", "alpha beta gamma and return it", "the and this"])
def test_inactive_queries_return_exact_definition_control(query):
    items = [snippet(number) for number in range(1, 10)]
    graph = Graph(items, [(items[-1], items[0])])
    actual = plan(items, query=query, graph=graph)
    expected = definition_context([(item, 1 / rank) for rank, item in enumerate(items, 1)],
        query=query, max_results=8, include_dependencies=True, references=graph, definition_keys=keys(items))
    assert actual[:3] == expected
    assert actual[3]["fallback_reason"] == "no_literal_clauses"
    assert actual[3]["graph_witnesses"] == []


@pytest.mark.parametrize("limit", [0, 1, 3, 5])
def test_tiny_result_caps_cannot_exchange_protected_anchors(limit):
    _, eligible, _, receipt = plan(limit=limit)
    assert len(eligible) <= limit and not receipt["accepted"]
    assert receipt["fallback_reason"] in {"no_graph_neighbors", "no_exchangeable_slots"}


@pytest.mark.parametrize("failure", ["no_graph", "unknown", "ambiguous_anchor", "ambiguous_neighbor"])
def test_absent_or_ambiguous_canonical_identity_cannot_invent_an_edge(failure):
    items = [snippet(number) for number in range(1, 10)]
    definition_keys = keys(items)
    graph = Graph(items, [(items[-1], items[0])])
    if failure == "no_graph":
        graph.edges = {}
    elif failure == "unknown":
        definition_keys = {}
    else:
        target = items[0] if failure == "ambiguous_anchor" else items[-1]
        definition_keys["ambiguous"] = (target.path, target.symbol, 500, 510)
    _, _, _, receipt = plan(items, graph=graph, definition_keys=definition_keys)
    assert receipt["fallback_reason"] == "no_graph_neighbors" and not receipt["graph_witnesses"]


def test_cached_ambiguity_remains_conservative_even_with_one_visible_definition():
    items = [snippet(number) for number in range(1, 10)]
    _, _, _, receipt = plan(items, ambiguous_symbols={(items[-1].path, items[-1].symbol)})
    assert receipt["fallback_reason"] == "no_graph_neighbors"


def test_js_same_line_byte_identity_is_retained_in_witnesses():
    items = [replace(snippet(number), path="src/unit.ts") for number in range(1, 10)]
    definition_keys = {identifier: (*key, index * 50) for index, (identifier, key) in enumerate(keys(items).items())}
    _, _, _, receipt = plan(items, definition_keys=definition_keys)
    assert receipt["accepted"] and len(receipt["graph_witnesses"][0]["source_key"]) == 5


def test_second_window_never_substitutes_for_first_scored_canonical_representative():
    items = [snippet(number) for number in range(1, 10)]
    continuation = replace(items[-1], id="tail", start_line=items[-1].end_line, end_line=items[-1].end_line)
    definition_keys = keys(items)
    definition_keys["9"] = (*definition_keys["9"][:3], items[-1].end_line + 10)
    definition_keys["tail"] = definition_keys["9"]
    _, eligible, _, receipt = plan(items + [continuation], graph=Graph(items, [(items[-1], items[0])]),
                                  definition_keys=definition_keys)
    assert receipt["added_ids"] == ["9"] and "tail" not in ids(eligible)


def test_real_graph_does_not_infer_unknown_receiver_calls():
    source = "def anchor():\n    return 1\n\ndef later(receiver):\n    return receiver.anchor()\n"
    items = [snippet(1, symbol="anchor"), snippet(9, symbol="later")]
    graph = ReferenceGraph({"src/unit.py": source}, items)
    assert graph.edges.get(("src/unit.py", "later"), []) == []
    assert not plan(items, graph=graph)[3]["accepted"]


def test_real_explicit_cross_file_import_supplies_a_truthful_caller_witness():
    items = [replace(snippet(number), path="src/anchor.py", start_line=(number - 1) * 3 + 1,
                     end_line=(number - 1) * 3 + 2) for number in range(1, 9)]
    caller_source = "from anchor import unit_1\n\ndef caller():\n    return unit_1()\n"
    caller = snippet(9, path="src/caller.py", symbol="caller")
    caller = replace(caller, start_line=3, end_line=4, text="def caller():\n    return unit_1()\n",
                     content_hash=hashlib.sha256(b"def caller():\n    return unit_1()\n").hexdigest())
    items.append(caller)
    anchor_source = "".join(f"def unit_{number}():\n    return {number}\n\n" for number in range(1, 9))
    graph = ReferenceGraph({"src/anchor.py": anchor_source, "src/caller.py": caller_source}, items)
    _, eligible, origins, receipt = plan(items, graph=graph)
    assert receipt["added_ids"] == ["9"]
    assert receipt["graph_witnesses"][0]["source_key"][:2] == ["src/caller.py", "caller"]
    assert receipt["graph_witnesses"][0]["target_key"][:2] == ["src/anchor.py", "unit_1"]
    assert origins.get("9", ("retrieval", None)) == ("retrieval", None)
    assert ids(eligible) == ["1", "2", "3", "4", "5", "9", "6", "7"]


def test_receipt_property_is_defensive_and_pure_planner_does_not_mutate_inputs():
    items = [snippet(number) for number in range(1, 10)]
    original = deepcopy(items)
    selected, eligible, _, receipt = plan(items)
    edited = eligible.source_plan
    edited["added_ids"].append("counterfeit")
    receipt["protected_baseline_ids"].clear()
    assert eligible.source_plan["added_ids"] == ["9"]
    assert eligible.source_plan["protected_baseline_ids"] == ["1", "2", "3", "4", "5"]
    assert items == original and selected == list(zip(items, [1 / rank for rank in range(1, 10)]))
    json.dumps(eligible.source_plan, allow_nan=False)


def test_structural_scope_accepts_exchange_while_all_scope_returns_exact_reference():
    items = [snippet(number) for number in range(1, 10)]
    _, eligible, _, receipt = plan(items)
    reference = response([source_row(item) for item in items[:8]])
    proposal = response([source_row(item) for item, _ in eligible])
    original = deepcopy((reference, proposal, receipt))
    chosen, guard = allocation.guard_source_plan(reference, proposal, receipt)
    assert chosen == proposal and guard["accepted"] and guard["source_preserved"]
    assert guard["protected_ids"] == ["1", "2", "3", "4", "5"]
    all_receipt = {**receipt, "mode": "source_graph_all"}
    chosen_all, guard_all = allocation.guard_source_plan(reference, proposal, all_receipt)
    assert chosen_all == reference and not guard_all["accepted"]
    assert guard_all["fallback_reason"] == "protected_source_not_preserved"
    assert (reference, proposal, receipt) == original
    assert chosen is not proposal and chosen_all is not reference
    assert not any("source_graph" in key for key in chosen)


def test_source_guard_protects_actual_backfilled_graph_neighbor():
    items = [snippet(number) for number in range(1, 10)]
    receipt = plan(items)[3]
    reference = response([source_row(item) for item in [*items[:5], items[-1]]])
    proposal = response([source_row(item) for item in items[:5]])
    chosen, guard = allocation.guard_source_plan(reference, proposal, receipt)
    assert guard["protected_ids"] == ["1", "2", "3", "4", "5", "9"]
    assert chosen == reference and not guard["accepted"]


@pytest.mark.parametrize("kind", ["anchor", "backfill_neighbor"])
def test_guard_protects_actual_containing_reference_row_after_original_id_was_coalesced(kind):
    items = [snippet(number) for number in range(1, 10)]
    protected = items[0] if kind == "anchor" else items[-1]
    parent_source = "# parent\n" + protected.text
    outer = replace(protected, id="outer", symbol="outer", start_line=protected.start_line - 1,
                    text=parent_source, content_hash=hashlib.sha256(parent_source.encode()).hexdigest())
    reference = response([source_row(outer)])
    proposal = response([source_row(items[5])])
    chosen, guard = allocation.guard_source_plan(reference, proposal, plan(items)[3])
    assert guard["protected_ids"] == ["outer"]
    assert chosen == reference and not guard["accepted"]


def test_window_overlap_protection_does_not_expand_to_entire_canonical_definition():
    items = [snippet(number) for number in range(1, 10)]
    definition_keys = keys(items)
    definition_keys["1"] = (*definition_keys["1"][:3], 1000)
    receipt = plan(items, definition_keys=definition_keys)[3]
    far_away = source_row(snippet(50))
    reference = response([far_away])
    assert allocation.protected_source_rows(reference, receipt) == []


@pytest.mark.parametrize("alteration", ["character_suffix", "source_bytes", "false_hash", "content_hash", "start", "overlay", "origin", "owner"])
def test_after_packing_guard_rejects_clipping_or_provenance_change(alteration):
    items = [snippet(number) for number in range(1, 10)]
    reference = response([source_row(items[0])])
    proposal = deepcopy(reference)
    row = proposal["results"][0]
    if alteration == "character_suffix":
        row["source"] = physical_lines(row["source"])[0]
        row["end_line"] -= 1
        row["source_hash"] = hashlib.sha256(row["source"].encode()).hexdigest()
    elif alteration == "source_bytes":
        row["source"] = row["source"].replace("return 1", "return 9")
        row["source_hash"] = hashlib.sha256(row["source"].encode()).hexdigest()
    elif alteration == "false_hash":
        row["source_hash"] = "0" * 64
    elif alteration == "content_hash":
        row["content_hash"] = "0" * 64
    elif alteration == "start":
        row["start_line"] += 1
    elif alteration == "overlay":
        row.update(source_origin="editor_buffer", buffer_version=2, buffer_hash="edited")
    elif alteration == "origin":
        row["origin"] = "dependency"
    else:
        row["related_to"] = "invented"
    chosen, guard = allocation.guard_source_plan(reference, proposal, plan(items)[3])
    assert chosen == reference and guard["fallback_reason"] == "protected_source_not_preserved"


@pytest.mark.parametrize("field,value", [("related_to", "different"), ("id", "different"), ("origin", "retrieval")])
def test_original_dependency_owner_and_identity_are_protected(field, value):
    items = [snippet(number) for number in range(1, 10)]
    old = source_row(items[5], origin="dependency", related_to="1")
    reference, proposal = response([old]), response([{**old, field: value}])
    chosen, guard = allocation.guard_source_plan(reference, proposal, plan(items)[3])
    assert chosen == reference and not guard["accepted"]


def test_dependency_owner_must_survive_by_actual_id_even_when_outer_source_covers_it():
    owner = snippet(1, source="def owner():\n    return helper()\n")
    dependency = snippet(20)
    outer = replace(owner, id="containing", symbol="outer", start_line=owner.start_line - 1,
                    text="# parent\n" + owner.text,
                    content_hash=hashlib.sha256(("# parent\n" + owner.text).encode()).hexdigest())
    old = source_row(dependency, origin="dependency", related_to=owner.id)
    reference = response([source_row(owner), old])
    proposal = response([source_row(outer), old])
    chosen, guard = allocation.guard_source_plan(reference, proposal, plan()[3])
    assert chosen == reference and not guard["accepted"]


@pytest.mark.parametrize("field", ["source_origin", "buffer_version", "buffer_hash"])
def test_guard_distinguishes_absent_provenance_key_from_present_none(field):
    row = source_row(snippet(1))
    reference, proposal = response([row]), response([{**row, field: None}])
    chosen, guard = allocation.guard_source_plan(reference, proposal, plan()[3])
    assert chosen == reference and not guard["accepted"]
    assert field not in guard["protected_rows"][0]


def test_whole_outer_literal_source_can_preserve_nested_retrieval_evidence():
    item = snippet(1, source="outer\n  nested\n  end\n")
    old = source_row(replace(item, id="nested", start_line=item.start_line + 1,
                             end_line=item.start_line + 1, text="  nested\n",
                             content_hash=hashlib.sha256(b"  nested\n").hexdigest()))
    receipt = plan()[3]
    receipt["protected_baseline_ids"].append("nested")
    chosen, guard = allocation.guard_source_plan(response([old]), response([source_row(item)]), receipt)
    assert guard["accepted"] and chosen["results"][0]["id"] == item.id


@pytest.mark.parametrize("source", ["one\r\ntwo\r\n", "one\rtwo\r", "one\ntwo", "λ\n💡\n", "one\u2028two\n"])
def test_guard_uses_exact_physical_lines_and_unicode_bytes(source):
    item = snippet(1, source=source)
    reference = response([source_row(item)])
    assert allocation.guard_source_plan(reference, deepcopy(reference), plan()[3])[1]["accepted"]


def test_inactive_plan_always_returns_exact_control_even_if_proposal_differs():
    receipt = plan(query="one implementation")[3]
    reference, proposal = response([source_row(snippet(1))]), response([source_row(snippet(9))])
    chosen, guard = allocation.guard_source_plan(reference, proposal, receipt)
    assert chosen == reference and guard["fallback_reason"] == "inactive_plan"


def test_guard_applies_exact_compact_token_clipping_before_source_comparison():
    # A later large result moves before an old protected row. The ordinary token
    # packer clips it away; identity eligibility alone would miss that loss.
    old = snippet(1, source="def anchor():\n" + "    value = 'protected source'\n" * 10)
    large = snippet(9, source="def later():\n" + "    value = 'a b c d e f g h i j'\n" * 100)
    baseline = response([source_row(old)], budget=512)
    proposal = response([source_row(large), source_row(old)], budget=512)
    assert baseline["results"] and proposal["token_usage"]["tokens"] <= 512
    chosen, guard = allocation.guard_source_plan(baseline, proposal, plan()[3])
    assert chosen == baseline and not guard["accepted"]


def test_context_hook_calls_each_stage_once_and_adds_no_response_receipt():
    items = [snippet(number) for number in range(1, 10)]
    _, eligible, _, receipt = plan(items)
    reference = response([source_row(item) for item in items[:8]])
    proposal = response([source_row(item) for item, _ in eligible])
    factory = Mock(return_value=reference)
    from unittest.mock import patch
    with patch.object(allocation, "guard_source_plan", wraps=allocation.guard_source_plan) as guard:
        chosen = allocation.guard_source_context(proposal, [], selection_plan=receipt,
            reference_factory=factory, max_results=8, source_budget=12000)
    assert factory.call_count == guard.call_count == 1
    assert chosen["results"] == proposal["results"]
    assert not any(key.startswith("_source") for key in chosen)
    assert "source_plan" not in compact_result(chosen)


@pytest.mark.parametrize("stage", [1, 2, 3, 4])
def test_cancellation_preserves_inputs_and_stops_before_return(stage):
    item = source_row(snippet(1))
    original = response([item])
    factory = Mock(return_value=deepcopy(original))
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == stage:
            raise SearchCancelled("cancelled")
    before = deepcopy(original)
    with pytest.raises(SearchCancelled):
        allocation.guard_source_context(original, [], selection_plan=plan()[3],
            reference_factory=factory, max_results=8, source_budget=12000, cancel_check=cancel)
    assert original == before
    if stage <= 3:
        assert factory.call_count == 0


def test_invalid_research_mode_and_missing_guard_factory_fail_explicitly():
    with pytest.raises(ValueError, match="Unknown source allocation mode"):
        plan(mode="invented")
    with pytest.raises(ValueError, match="actual source allocation plan"):
        allocation.guard_source_plan(response([]), response([]), {})
    with pytest.raises(ValueError, match="same-input reference factory"):
        allocation.guard_source_context(response([]), [], selection_plan=plan()[3], reference_factory=None,
                                        max_results=8, source_budget=12000)
