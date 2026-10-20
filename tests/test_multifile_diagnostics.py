"""Stage attribution separates slot/clipping losses and dependency recovery."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import diagnose_multifile as diagnosis
from semantic_search.corpus import Snippet


def snippet(identifier, *, path=None, symbol=None, start=1, text="work();\n"):
    return Snippet(identifier, path or f"src/{identifier}.py", symbol or identifier,
                   start, start + len(diagnosis.physical_lines(text)) - 1,
                   text, diagnosis.digest(text.encode()))


class Graph:
    def __init__(self, edges=None):
        self.edges = edges or {}

    def dependencies(self, item, limit):
        return self.edges.get(item.id, [])[:limit]


@pytest.fixture(autouse=True)
def character_tokens(monkeypatch):
    class Encoder:
        def encode(self, text, **kwargs):
            return list(text)
    monkeypatch.setattr(diagnosis.context, "encoding", lambda: Encoder())


def trace(items, *, graph=None, **kwargs):
    pairs = [(item, float(len(items) - index)) for index, item in enumerate(items)]
    return diagnosis.trace_selection(pairs, graph or Graph(), {"query": "work", "results": []},
                                     token_budget=kwargs.pop("token_budget", 100000), **kwargs)


def gold(item, lo=None, hi=None):
    return [[{"path": item.path, "symbol": "label-is-not-window-identity",
              "evidence_lines": [lo or item.start_line, hi or item.end_line]}]]


def outcome(result, item, **kwargs):
    return diagnosis.attribute_groups(result, gold(item, **kwargs))[0]["outcome"]


def test_full_span_credit_uses_path_lines_and_or_alternatives_not_symbols():
    item = snippet("correct", start=4, text="first();\r\nsecond();\rthird();\n")
    result = trace([item])
    assert outcome(result, item) == "delivered"
    alternatives = [[{"path": "wrong.py", "evidence_lines": [1, 2]}, *gold(item)[0]]]
    assert diagnosis.attribute_groups(result, alternatives)[0]["outcome"] == "delivered"
    assert result["stages"]["token_packed"][0]["end_line"] == 6


def test_upstream_shortlist_and_symbol_deduplication_are_distinct():
    missing, wrong = snippet("missing"), snippet("wrong")
    assert outcome(trace([wrong]), missing) == "upstream"
    shortlisted = diagnosis.trace_selection([(wrong, 1.)], Graph(), {"query": "work"},
                                            retrieved=[(missing, 2.), (wrong, 1.)], token_budget=100000)
    assert outcome(shortlisted, missing) == "shortlist"
    first = snippet("first", path=missing.path, symbol=missing.symbol, start=8)
    assert outcome(trace([first, missing]), missing) == "deduplication"


def test_result_cap_and_dependency_displacement_are_distinct():
    items = [snippet(f"item{i}") for i in range(9)]
    assert outcome(trace(items), items[8]) == "result_cap"
    helpers = [snippet(f"helper{i}") for i in range(3)]
    graph = Graph({items[0].id: helpers[:2], items[1].id: helpers[2:]})
    result = trace(items, graph=graph)
    assert outcome(result, items[5]) == "dependency_displacement"
    assert len(result["stages"]["anchors"]) == 5
    assert len(result["stages"]["expanded"]) == 8


def test_character_and_token_clipping_are_separate_and_whole_line():
    item = snippet("cut", text="first();\nlast();\n")
    characters = trace([item], source_budget=len("first();\n"))
    assert outcome(characters, item) == "character_clipping"
    assert characters["stages"]["character_packed"][0]["end_line"] == 1
    full = trace([item])
    # Leave room for the shorter serialized budget number itself.
    budget = full["response"]["token_usage"]["tokens"] - 10
    tokens = trace([item], token_budget=budget)
    assert outcome(tokens, item) == "token_clipping"
    assert tokens["stages"]["character_packed"][0]["end_line"] == 2
    assert tokens["response"]["token_usage"]["tokens"] <= budget


def test_dependency_can_restore_upstream_or_deduplicated_evidence():
    anchor, helper = snippet("anchor"), snippet("helper")
    recovered = trace([anchor], graph=Graph({anchor.id: [helper]}))
    assert outcome(recovered, helper) == "delivered"
    assert diagnosis.attribute_groups(recovered, gold(helper))[0]["ranks"]["retrieved"] is None
    wrong_window = snippet("wrong", path=helper.path, symbol=helper.symbol, start=20)
    recovered = trace([anchor, *[snippet(f"filler{i}") for i in range(4)], wrong_window, helper],
                      graph=Graph({anchor.id: [helper]}))
    assert outcome(recovered, helper) == "delivered"


def test_recovered_dependency_lost_to_characters_is_not_blamed_on_retrieval():
    anchor, helper = snippet("anchor"), snippet("helper")
    result = trace([anchor], graph=Graph({anchor.id: [helper]}), source_budget=len(anchor.text))
    assert outcome(result, helper) == "character_clipping"


def test_first_five_is_only_intermediate_and_later_anchor_can_return():
    items = [snippet(f"item{i}") for i in range(8)]
    result = trace(items)
    assert outcome(result, items[-1]) == "delivered"
    row = diagnosis.attribute_groups(result, gold(items[-1]))[0]
    assert row["ranks"]["anchors"] is None and row["ranks"]["expanded"] == 8


def test_ranked_window_outside_original_pool_is_rejected():
    with pytest.raises(ValueError, match="outside retrieval"):
        diagnosis.trace_selection([(snippet("different"), 1.)], Graph(), {"query": "work"},
                                  retrieved=[(snippet("original"), 2.)])
