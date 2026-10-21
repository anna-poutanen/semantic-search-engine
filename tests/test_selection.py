"""Selection contracts distinguish definition identity from dependency capacity."""
from unittest.mock import Mock

from semantic_search.corpus import Snippet
from semantic_search.selection import (
    definition_context, definition_rank_preserving_context, ranked_context,
    rank_preserving_context, unchanged_context,
)


def snippet(number, symbol=None):
    return Snippet(str(number), "src/code.py", symbol or f"function{number}",
                   number * 10 + 1, number * 10 + 2, "return 1\n", str(number))


def choose(selector, snippets, *, references=None, keys=None, limit=8):
    return selector([(row, 1 / (rank + 1)) for rank, row in enumerate(snippets)],
                    query="ordinary query", max_results=limit,
                    include_dependencies=references is not None,
                    references=references, definition_keys=keys)


def identifiers(pairs):
    return [row.id for row, _ in pairs]


def test_canonical_identity_retains_separate_definitions_collapses_same_definition_windows():
    first, overlap, other = [snippet(i, "duplicate") for i in range(3)]
    keys = {first.id: (first.path, first.symbol, 1, 50),
            overlap.id: (first.path, first.symbol, 1, 50),
            other.id: (first.path, first.symbol, 60, 70)}
    assert identifiers(choose(ranked_context, [first, overlap, other])[0]) == [first.id]
    distinct, eligible, origins = choose(definition_context, [first, overlap, other], keys=keys)
    assert identifiers(distinct) == identifiers(eligible) == [first.id, other.id]
    assert origins == {first.id: ("retrieval", None), other.id: ("retrieval", None)}


def test_unknown_definition_metadata_never_conflates_display_names():
    rows = [snippet(i, "duplicate") for i in range(3)]
    assert identifiers(choose(definition_context, rows)[0]) == [row.id for row in rows]


def test_canonical_ambiguous_anchor_does_not_assert_a_shared_graph_edge():
    first, second, helper = snippet(1, "duplicate"), snippet(2, "duplicate"), snippet(3)
    keys = {row.id: (row.path, row.symbol, row.start_line, row.end_line)
            for row in [first, second, helper]}
    graph = Mock()
    graph.dependencies.return_value = [helper]
    _, eligible, _ = choose(definition_context, [first, second], references=graph, keys=keys)
    assert identifiers(eligible) == [first.id, second.id]
    graph.dependencies.assert_not_called()


def test_canonical_ambiguous_callee_does_not_assert_wrong_definition_provenance():
    anchor, first, second = snippet(1), snippet(2, "duplicate"), snippet(3, "duplicate")
    keys = {row.id: (row.path, row.symbol, row.start_line, row.end_line)
            for row in [anchor, first, second]}
    graph = Mock()
    graph.dependencies.return_value = [first]
    _, eligible, origins = choose(definition_context, [anchor], references=graph, keys=keys)
    assert identifiers(eligible) == [anchor.id]
    assert origins == {anchor.id: ("retrieval", None)}


def test_rank_capacity_control_retains_rank_eight_and_only_expands_spare_slots():
    rows, helper = [snippet(i) for i in range(8)], snippet(20)
    graph = Mock()
    graph.dependencies.return_value = [helper]
    assert helper.id in identifiers(choose(ranked_context, rows, references=graph)[1])
    for selector in (rank_preserving_context, definition_rank_preserving_context):
        graph.reset_mock()
        assert identifiers(choose(selector, rows, references=graph)[1]) == [row.id for row in rows]
        graph.dependencies.assert_not_called()
        _, eligible, origins = choose(selector, rows[:2], references=graph)
        assert identifiers(eligible) == [rows[0].id, rows[1].id, helper.id]
        assert origins[helper.id] == ("dependency", rows[0].id)


def test_definition_allocation_still_adds_unambiguous_callee_outside_ranked_pool():
    rows, helper = [snippet(i) for i in range(8)], snippet(20)
    keys = {row.id: (row.path, row.symbol, row.start_line, row.end_line) for row in rows + [helper]}
    graph = Mock()
    graph.dependencies.return_value = [helper]
    _, eligible, origins = choose(definition_context, rows, references=graph, keys=keys)
    assert identifiers(eligible) == [row.id for row in rows[:5]] + [helper.id, rows[5].id, rows[6].id]
    assert origins[helper.id] == ("dependency", rows[0].id)


def test_noop_postprocessor_does_not_evaluate_lazy_candidate_rows():
    def forbidden():
        raise AssertionError("baseline consumed candidates")
        yield
    response = {"results": []}
    assert unchanged_context(response, forbidden()) is response
