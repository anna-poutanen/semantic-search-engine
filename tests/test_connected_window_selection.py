"""Connected windows inherit one real score and the original dependency policy."""
import hashlib
from unittest.mock import Mock

from semantic_search.corpus import Snippet
from semantic_search.selection import connected_window_context


def window(identifier, start, end, *, symbol="run"):
    text = "".join(f"    line_{line} = {line}\n" for line in range(start, end + 1))
    return Snippet(identifier, "src/code.py", symbol, start, end, text,
                   hashlib.sha256(text.encode()).hexdigest())


def choose(pairs, keys, graph=None):
    return connected_window_context(pairs, query="ordinary behavior", max_results=8,
        include_dependencies=graph is not None, references=graph,
        definition_keys=keys, ambiguous_symbols=frozenset())


def test_one_connected_companion_retains_first_rank_and_inherited_score():
    first, second, third = window("first", 1, 160), window("second", 121, 280), window("third", 241, 400)
    key = (first.path, first.symbol, 1, 400)
    distinct, eligible, origins = choose([(first, .8), (second, .7), (third, .6)],
                                         {row.id: key for row in (first, second, third)})
    assert len(distinct) == len(eligible) == 1
    joined, score = eligible[0]
    assert (joined.start_line, joined.end_line, score) == (1, 280, .8)
    assert joined.assembly["score_anchor_id"] == first.id
    assert [member["id"] for member in joined.assembly["members"]] == [first.id, second.id]
    assert origins[joined.id] == ("retrieval", None)


def test_unknown_metadata_or_separate_same_name_definitions_never_join():
    first, second = window("first", 1, 10), window("second", 11, 20)
    keys = {first.id: (first.path, first.symbol, 1, 10),
            second.id: (second.path, second.symbol, 11, 20)}
    for mapping in (keys, {}):
        distinct, _, _ = choose([(first, .8), (second, .7)], mapping)
        assert [row.id for row, _ in distinct] == [first.id, second.id]


def test_skip_gap_and_choose_first_connected_companion_in_original_rank_order():
    first, gap, connected = window("first", 1, 160), window("gap", 300, 400), window("connected", 121, 280)
    key = (first.path, first.symbol, 1, 400)
    _, eligible, _ = choose([(first, .8), (gap, .75), (connected, .7)],
                            {row.id: key for row in (first, gap, connected)})
    assert eligible[0][0].end_line == 280
    assert eligible[0][0].assembly["companion_rank"] == 3


def test_original_static_callee_outside_ranked_pool_retains_composite_owner():
    first, second, helper = window("first", 1, 160), window("second", 121, 280), window("helper", 500, 510, symbol="helper")
    keys = {first.id: (first.path, first.symbol, 1, 280),
            second.id: (first.path, first.symbol, 1, 280),
            helper.id: (helper.path, helper.symbol, 500, 510)}
    graph = Mock()
    graph.dependencies.return_value = [helper]
    _, eligible, origins = choose([(first, .8), (second, .7)], keys, graph)
    assert [row.symbol for row, _ in eligible] == ["run", "helper"]
    assert origins[helper.id] == ("dependency", eligible[0][0].id)
