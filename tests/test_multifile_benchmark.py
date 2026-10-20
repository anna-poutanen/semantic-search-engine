"""CPU guardrails for source evidence, declared budgets and AND/OR scoring."""
import copy
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import benchmark_multifile as benchmark


class Counter:
    def encode(self, text, **kwargs):
        # Deliberately allow >12000 source characters inside the token cap so
        # character-budget validation cannot accidentally rely on token count.
        return range((len(text) + 7) // 8)


@pytest.fixture(autouse=True)
def cpu_counter(monkeypatch):
    monkeypatch.setattr(benchmark, "encoding", lambda: Counter())


def payload(source="first();\nsecond();\n"):
    row = {"path": "src/code.py", "symbol": "run", "start_line": 1, "end_line": 2,
           "source": source, "source_hash": hashlib.sha256(source.encode()).hexdigest(),
           "truncated": False}
    response = {"query": "Find the implementation", "results": [row],
                "total_source_chars": len(source), "budget_exhausted": False,
                "token_usage": {"encoding": "cl100k_base", "tokens": 0,
                                "budget": 4096, "truncated": False}}
    return recount(response), {"src/code.py": source}


def recount(response):
    for _ in range(12):
        count = len(Counter().encode(benchmark.serialize_context(benchmark.compact_result(response))))
        if count == response["token_usage"]["tokens"]:
            return response
        response["token_usage"]["tokens"] = count
    raise AssertionError("Test payload token count did not converge")


def test_exact_literal_source_and_compact_count_are_accepted():
    response, sources = payload()
    benchmark.checked_response(response, sources)


def test_source_body_and_hash_must_both_match_saved_lines():
    response, sources = payload()
    response["results"][0]["source"] = "different();\n"
    recount(response)
    with pytest.raises(ValueError):
        benchmark.checked_response(response, sources)
    response, sources = payload()
    response["results"][0]["source_hash"] = "0" * 64
    recount(response)
    with pytest.raises(ValueError):
        benchmark.checked_response(response, sources)


@pytest.mark.parametrize("start,end", [(0, 1), (-1, 1), (2, 1), (1, 3), ("1", 2), (True, 2)])
def test_physical_spans_require_integer_positive_in_bounds_lines(start, end):
    response, sources = payload()
    response["results"][0].update(start_line=start, end_line=end)
    recount(response)
    with pytest.raises(ValueError):
        benchmark.checked_response(response, sources)


def test_unknown_path_and_empty_excerpt_are_rejected():
    response, sources = payload()
    response["results"][0]["path"] = "unknown.py"
    recount(response)
    with pytest.raises(ValueError):
        benchmark.checked_response(response, sources)
    response, sources = payload("")
    with pytest.raises(ValueError):
        benchmark.checked_response(response, sources)


def test_character_cap_is_measured_from_actual_source_not_declared_total():
    response, sources = payload("a" * 12001 + "\n")
    response["results"][0]["end_line"] = 1
    response["total_source_chars"] = 1
    recount(response)
    assert response["token_usage"]["tokens"] < 4096
    with pytest.raises(ValueError):
        benchmark.checked_response(response, sources)
    response, sources = payload()
    response["total_source_chars"] -= 1
    with pytest.raises(ValueError):
        benchmark.checked_response(response, sources)


def test_row_cap_token_cap_and_exact_reported_token_count_are_independent():
    response, sources = payload()
    response["results"] *= 9
    response["total_source_chars"] *= 9
    recount(response)
    with pytest.raises(ValueError):
        benchmark.checked_response(response, sources)
    response, sources = payload()
    response["results"][0]["symbol"] = "metadata" * 6000
    recount(response)
    assert response["token_usage"]["tokens"] > 4096
    with pytest.raises(ValueError):
        benchmark.checked_response(response, sources)
    response, sources = payload()
    response["token_usage"]["tokens"] += 1
    with pytest.raises(ValueError):
        benchmark.checked_response(response, sources)


def target(path, low=3, high=4):
    return {"path": path, "symbol": "label-is-not-window-identity", "evidence_lines": [low, high]}


def test_evidence_ranks_require_one_complete_span_and_support_or_alternatives():
    windows = [{"path": "a.py", "symbol": "enclosing_wrapper", "start_line": 2, "end_line": 5},
               {"path": "b.py", "symbol": "other", "start_line": 1, "end_line": 10}]
    gold = [[target("wrong.py"), target("a.py")], [target("b.py", 7, 9)]]
    assert benchmark.ranks(windows, gold) == [1, 2]
    clipped = copy.deepcopy(windows)
    clipped[0]["end_line"] = 3
    assert benchmark.ranks(clipped, gold) == [None, 2]
    # Two partial excerpts do not collectively satisfy a full executable span.
    fragments = [{"path": "a.py", "start_line": 2, "end_line": 3},
                 {"path": "a.py", "start_line": 4, "end_line": 5}]
    assert benchmark.ranks(fragments, [gold[0]]) == [None]


def test_multifile_requires_multiple_and_groups_with_no_common_file_alternative():
    assert not benchmark.is_multifile([])
    assert not benchmark.is_multifile([[target("a.py"), target("b.py")]])
    assert not benchmark.is_multifile([[target("a.py")], [target("a.py", 8, 9)]])
    assert benchmark.is_multifile([[target("a.py")], [target("b.py")]])
    assert not benchmark.is_multifile([[target("a.py"), target("b.py")],
                                     [target("b.py"), target("c.py")]])
    assert benchmark.is_multifile([[target("a.py"), target("b.py")],
                                  [target("b.py"), target("c.py")],
                                  [target("a.py"), target("c.py")]])
