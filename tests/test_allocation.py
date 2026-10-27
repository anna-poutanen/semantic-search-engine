"""Synthetic contracts for source/query-only complementary allocation."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import json
from unittest.mock import Mock

import pytest

from semantic_search.allocation import (
    _content_terms, allocation_plan, aspect_balanced_context, aspect_fill_context,
)
from semantic_search.corpus import Snippet
from semantic_search.selection import definition_context


QUERY = "alpha beta gamma and delta epsilon zeta"


def row(number, text="alpha beta gamma\n", *, path="src/unit.py", symbol=None):
    return Snippet(str(number), path, symbol or f"unit_{number}",
                   number * 10 + 1, number * 10 + 2, text, f"hash-{number}")


def pairs(items):
    return [(item, Fraction(1, index + 1)) for index, item in enumerate(items)]


def keys(items):
    return {item.id: (item.path, item.symbol, item.start_line, item.end_line) for item in items}


def plan(items, *, mode="aspect_fill", query=QUERY, references=None,
         definition_keys=None, limit=8, include_dependencies=None):
    return allocation_plan(pairs(items), mode=mode, query=query, max_results=limit,
                           include_dependencies=(references is not None if include_dependencies is None
                                                 else include_dependencies),
                           references=references,
                           definition_keys=keys(items) if definition_keys is None else definition_keys)


def ids(items):
    return [item.id for item, _ in items]


def assert_baseline(actual, items, *, query=QUERY, references=None,
                    definition_keys=None, limit=8):
    expected = definition_context(pairs(items), query=query, max_results=limit,
        include_dependencies=references is not None, references=references,
        definition_keys=keys(items) if definition_keys is None else definition_keys)
    assert actual[:3] == expected


@pytest.mark.parametrize("mode,anchors,expected", [
    ("aspect_fill", 5, [1, 2, 3, 4, 5, 9, 6, 7]),
    ("aspect_balanced", 3, [1, 2, 3, 9, 4, 5, 6, 7]),
])
def test_later_complement_can_replace_redundant_ranked_tail(mode, anchors, expected):
    items = [row(number) for number in range(1, 9)] + [row(9, "delta epsilon zeta\n")]
    selected, eligible, origins, receipt = plan(items, mode=mode)
    assert ids(selected) == [str(number) for number in range(1, 10)]
    assert ids(eligible) == [str(number) for number in expected]
    assert receipt["protected_anchor_ids"] == [str(number) for number in range(1, anchors + 1)]
    assert receipt["accepted"] is True and receipt["fallback_reason"] is None
    assert receipt["picks"][0] == {
        "id": "9", "rank": 9, "novel_terms": ["delta", "epsilon", "zeta"],
        "weighted_gain": {"numerator": 3, "denominator": 1},
        "utility": {"numerator": 1, "denominator": 3}, "reason": "novel_query_terms",
    }
    assert all(origin == ("retrieval", None) for origin in origins.values())
    assert dict((item.id, score) for item, score in eligible) == {
        item.id: score for item, score in pairs(items) if item.id in ids(eligible)}


@pytest.mark.parametrize("mode", ["aspect_fill", "aspect_balanced"])
@pytest.mark.parametrize("query", [
    "ordinary single implementation", "alpha beta gamma and return it",
    "the and this, where is it", "cookie cookie cookie and weak weak weak",
])
def test_plain_or_insufficient_literal_clauses_return_exact_definition_control(mode, query):
    items = [row(number) for number in range(1, 11)]
    actual = plan(items, mode=mode, query=query)
    assert_baseline(actual, items, query=query)
    assert actual[3]["activated"] is False
    assert actual[3]["fallback_reason"] == "no_literal_clauses"
    assert actual[3]["raw_ranks"] == actual[3]["candidate_terms"] == actual[3]["term_df"] == {}


def test_content_terms_match_existing_clause_normalization_and_identifier_boundaries():
    assert _content_terms("Where are registryEntries and registries, HTTPRetryPolicies? class Class") == {
        "registry", "entry", "http", "retry", "policy", "class"}


@pytest.mark.parametrize("mode,limit", [("aspect_fill", 5), ("aspect_balanced", 3)])
def test_no_spare_slot_preserves_exact_baseline(mode, limit):
    items = [row(number) for number in range(1, 10)]
    result = plan(items, mode=mode, limit=limit)
    assert_baseline(result, items, limit=limit)
    assert result[3]["fallback_reason"] == "no_free_slots"


def test_no_novel_terms_returns_exact_baseline_including_origins():
    items = [row(number) for number in range(1, 10)]
    actual = plan(items)
    assert_baseline(actual, items)
    assert actual[3]["fallback_reason"] == "no_novel_query_terms"
    assert all(pick["reason"] == "rank_fill" for pick in actual[3]["picks"])


def test_term_preservation_guard_rejects_even_a_proposal_with_more_new_terms():
    # The two free slots favor the many novel terms in rank six and the rarer
    # epsilon in rank five, dropping baseline delta. The guard must reject it.
    query = "alpha beta gamma and delta epsilon zeta theta iota kappa"
    items = [row(number) for number in range(1, 4)] + [
        row(4, "delta\n"), row(5, "epsilon\n"), row(6, "zeta theta iota kappa\n"),
        row(7, "delta\n"), row(8, "delta\n"),
    ]
    actual = plan(items, mode="aspect_balanced", limit=5, query=query)
    assert_baseline(actual, items, limit=5, query=query)
    assert actual[3]["fallback_reason"] == "query_terms_not_preserved"
    assert actual[3]["accepted"] is False
    assert set(actual[3]["baseline_query_terms"]) - set(actual[3]["proposed_query_terms"])


def test_all_original_dependencies_and_owners_stay_protected():
    items = [row(number) for number in range(1, 10)]
    items[-1] = row(9, "delta epsilon zeta\n")
    helper = row(20, "alpha\n", path="src/helper.py")
    graph = Mock()
    graph.dependencies.side_effect = lambda item, limit: [helper] if item.id == "5" else []
    selected, eligible, origins, receipt = plan(items, mode="aspect_balanced", references=graph,
                                              definition_keys=keys(items + [helper]))
    assert ids(selected) == [str(number) for number in range(1, 10)]
    assert ids(eligible) == ["1", "2", "3", "5", "20", "9", "4", "6"]
    assert receipt["protected_dependency_ids"] == ["20"]
    assert receipt["protected_owner_ids"] == ["5"]
    assert receipt["protected_ids"] == ["1", "2", "3", "5", "20"]
    assert origins[helper.id] == ("dependency", "5")
    assert dict((item.id, score) for item, score in eligible)[helper.id] == pairs(items)[4][1]
    assert all(origins[item.id] == ("retrieval", None) for item, _ in eligible if item.id != helper.id)


def test_dependency_capacity_exhaustion_returns_the_exact_baseline():
    items = [row(number) for number in range(1, 10)]
    helpers = [row(number, path="src/helper.py") for number in (20, 21, 22)]
    graph = Mock()
    graph.dependencies.side_effect = lambda item, limit: helpers[:2] if item.id == "1" else helpers[2:]
    actual = plan(items, references=graph, definition_keys=keys(items + helpers))
    assert_baseline(actual, items, references=graph, definition_keys=keys(items + helpers))
    assert actual[3]["fallback_reason"] == "no_free_slots"
    assert actual[3]["protected_dependency_ids"] == ["20", "21", "22"]


def test_ambiguous_labels_do_not_invent_dependencies():
    items = [row(number) for number in range(1, 9)] + [row(9, "delta epsilon zeta\n")]
    items[1] = replace(items[1], symbol=items[0].symbol)
    graph = Mock()
    graph.dependencies.return_value = []
    result = plan(items, references=graph)
    assert result[3]["accepted"]
    graph.dependencies.assert_any_call(items[2], limit=2)
    assert all(call.args[0].id not in {"1", "2"} for call in graph.dependencies.call_args_list)


def test_distinct_definitions_share_display_names_without_being_collapsed():
    items = [row(number) for number in range(1, 9)] + [row(9, "delta epsilon zeta\n")]
    items[-1] = replace(items[-1], symbol=items[0].symbol)
    selected, eligible, _, receipt = plan(items)
    assert len(selected) == 9 and "9" in ids(eligible)
    assert receipt["term_df"]["delta"] == 1


def test_multiple_windows_count_once_per_actual_definition_for_term_frequency():
    items = [row(number) for number in range(1, 9)] + [row(9, "delta epsilon zeta\n")]
    continuation = replace(items[-1], id="continuation", start_line=95, end_line=96)
    definition_keys = keys(items)
    definition_keys[continuation.id] = definition_keys[items[-1].id]
    items.append(continuation)
    selected, eligible, _, receipt = plan(items, definition_keys=definition_keys)
    assert len(selected) == 9 and "9" in ids(eligible)
    assert receipt["term_df"]["delta"] == 1
    assert receipt["raw_ranks"][continuation.id] == 10
    assert continuation.id not in ids(selected)


def test_later_window_contributes_df_but_does_not_invent_representative_source_terms():
    items = [row(number) for number in range(1, 10)]
    continuation = replace(items[-1], id="continuation", text="delta epsilon zeta\n",
                           start_line=95, end_line=96)
    definition_keys = keys(items)
    definition_keys[continuation.id] = definition_keys[items[-1].id]
    actual = plan(items + [continuation], definition_keys=definition_keys)
    assert actual[3]["term_df"]["delta"] == 1
    assert actual[3]["candidate_terms"]["9"] == ["alpha", "beta", "gamma"]
    assert actual[3]["fallback_reason"] == "no_novel_query_terms"


def test_unknown_metadata_treats_windows_as_separate_df_units():
    items = [row(number) for number in range(1, 9)] + [row(9, "delta epsilon zeta\n")]
    duplicate = replace(items[-1], id="unknown", start_line=95)
    actual = plan(items + [duplicate], definition_keys={})
    assert len(actual[0]) == 10
    assert actual[3]["term_df"]["delta"] == 2


@pytest.mark.parametrize("field", ["path", "symbol", "text"])
def test_identifier_path_and_body_terms_all_supply_literal_features(field):
    items = [row(number) for number in range(1, 10)]
    items[-1] = replace(items[-1], **{field: "deltaEpsilonZeta"})
    actual = plan(items)
    assert actual[3]["picks"][0]["id"] == "9"
    assert actual[3]["picks"][0]["novel_terms"] == ["delta", "epsilon", "zeta"]


def test_original_raw_rank_counts_collapsed_windows_in_utility():
    items = [row(number) for number in range(1, 9)] + [row(9, "delta epsilon zeta\n")]
    duplicate = replace(items[0], id="duplicate")
    definition_keys = keys(items)
    definition_keys[duplicate.id] = definition_keys[items[0].id]
    actual = plan([items[0], duplicate, *items[1:]], definition_keys=definition_keys)
    assert actual[3]["picks"][0]["rank"] == 10
    assert actual[3]["picks"][0]["utility"] == {"numerator": 3, "denominator": 10}


def test_exact_fraction_tie_keeps_original_raw_rank():
    query = "alpha beta gamma and delta epsilon zeta"
    # Arrange an exact utility tie with 1/2 / 6 and 1 / 12, counting delta only
    # once in each of two actual definitions.
    items = [row(number) for number in range(1, 6)] + [row(6, "delta\n")]
    items += [row(number) for number in range(7, 12)] + [row(12, "epsilon\n"), row(13, "delta\n")]
    actual = plan(items, query=query)
    assert actual[3]["term_df"]["delta"] == 2
    assert actual[3]["picks"][0]["id"] == "6"
    assert actual[3]["picks"][0]["utility"] == {"numerator": 1, "denominator": 12}


@pytest.mark.parametrize("mode", ["aspect_fill", "aspect_balanced"])
@pytest.mark.parametrize("limit", range(9))
def test_fixed_result_limits_never_grow_selection(mode, limit):
    items = [row(number) for number in range(1, 9)] + [row(9, "delta epsilon zeta\n")]
    selected, eligible, _, receipt = plan(items, mode=mode, limit=limit)
    assert len(selected) == 9 and len(eligible) == min(limit, 9)
    if limit:
        assert eligible[0][0] is items[0]
    json.dumps(receipt, allow_nan=False)


def test_inputs_and_indexed_snippets_stay_unchanged_and_repeated_plans_are_identical():
    items = [row(number) for number in range(1, 9)] + [row(9, "delta epsilon zeta\n")]
    original_items, definition_keys = deepcopy(items), keys(items)
    original_keys = deepcopy(definition_keys)
    assert plan(items, definition_keys=definition_keys) == plan(items, definition_keys=definition_keys)
    assert items == original_items and definition_keys == original_keys


@pytest.mark.parametrize("selector,mode", [
    (aspect_fill_context, "aspect_fill"), (aspect_balanced_context, "aspect_balanced"),
])
def test_selector_wrappers_keep_existing_three_value_interface(selector, mode):
    items = [row(number) for number in range(1, 9)] + [row(9, "delta epsilon zeta\n")]
    kwargs = dict(query=QUERY, max_results=8, include_dependencies=False,
                  references=None, definition_keys=keys(items))
    assert selector(pairs(items), **kwargs) == allocation_plan(pairs(items), mode=mode, **kwargs)[:3]


def test_unknown_mode_is_rejected_before_selection():
    with pytest.raises(ValueError, match="Unknown complementary allocation mode"):
        plan([], mode="unfrozen")


def test_literal_query_term_preservation_does_not_assert_implementation_correctness():
    items = [row(number) for number in range(1, 9)] + [row(9, "# delta epsilon zeta\n")]
    selected, eligible, _, receipt = plan(items)
    assert receipt["accepted"] is True and "9" in ids(eligible)
    assert "8" in ids(selected) and "8" not in ids(eligible)
    # A behavior gold target on excluded unit_8 would still be lost. The source
    # heuristic cannot inspect such labels; the independent quality gate must.
