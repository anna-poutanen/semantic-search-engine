"""CPU contracts for literal-source unions and guarded complementary suffixes."""
import copy
import hashlib
from dataclasses import FrozenInstanceError, asdict, fields
from unittest.mock import patch

import pytest

from semantic_search import context
from semantic_search.corpus import Snippet
from semantic_search.source import physical_lines
from semantic_search.window_assembly import AssembledSnippet, extend_context, join_windows


def sha(source):
    return hashlib.sha256(source.encode()).hexdigest()


def snippet(identifier, source, start=1, path="src/code.py", symbol="run"):
    return Snippet(identifier, path, symbol, start, start + len(physical_lines(source)) - 1,
                   source, sha(source))


def row(identifier, source, start=1, path="src/code.py", symbol="run", **metadata):
    end = start + len(physical_lines(source)) - 1
    return {"id": identifier, "path": path, "symbol": symbol, "start_line": start,
            "end_line": end, "window_end_line": end, "source": source,
            "source_hash": sha(source), "content_hash": sha(source), "truncated": False,
            "retrieval_score": 0.9, "h2o_score": 0.8, "qwen_score": 0.7,
            "origin": "retrieval", "related_to": None, **metadata}


def response(rows, *, token_budget=10000, **metadata):
    result = {"query": "generic source fixture", "match_status": "candidates",
              "results": copy.deepcopy(rows), "budget_exhausted": False,
              "total_source_chars": sum(len(item["source"]) for item in rows), **metadata}
    if token_budget is not None:
        result["token_usage"] = {"encoding": context.ENCODING, "tokens": 0,
                                 "budget": token_budget, "truncated": False}
        context.count_context(result)
    return result


def key(*identifiers, path="src/code.py", symbol="run", end=1000):
    return {identifier: (path, symbol, 1, end) for identifier in identifiers}


def extend(original, candidates, *, keys=None, source_budget=12000, token_budget=10000, **options):
    return extend_context(original, candidates, definition_keys=keys,
                          max_results=options.pop("max_results", 8), source_budget=source_budget,
                          token_budget=token_budget, **options)


def exact_count(result):
    actual = len(context.encoding().encode(context.serialize_context(context.compact_result(result)),
                                           disallowed_special=()))
    assert actual == result["token_usage"]["tokens"]
    assert actual <= result["token_usage"]["budget"]
    return actual


def without_debug(result):
    return {name: value for name, value in result.items() if name != "_window_assembly"}


def test_join_overlapping_windows_has_literal_union_new_identity_and_complete_receipt():
    first, second = snippet("first", "a\nb\nc\n"), snippet("second", "c\nd\ne\n", 3)
    definition = ("src/code.py", "run", 1, 5)
    saved = (asdict(first), asdict(second))
    result = join_windows(first, second, definition_key=definition, anchor_rank=2, companion_rank=7)
    assert isinstance(result, AssembledSnippet)
    assert result.text == "a\nb\nc\nd\ne\n"
    assert (result.start_line, result.end_line) == (1, 5)
    assert result.content_hash == sha(result.text)
    assert result.id.startswith("context:connected_windows:")
    assert result.id not in {first.id, second.id}
    assert result.assembly["score_anchor_id"] == first.id
    assert result.assembly["anchor_rank"] == 2 and result.assembly["companion_rank"] == 7
    assert result.assembly["definition_key"] == list(definition)
    assert result.assembly["members"][0] == {
        "id": "first", "path": "src/code.py", "symbol": "run", "start_line": 1,
        "end_line": 3, "content_hash": first.content_hash, "used_start_line": 1,
        "used_end_line": 3, "used_source_hash": first.content_hash}
    assert (asdict(first), asdict(second)) == saved
    assert [item.name for item in fields(Snippet)] == [
        "id", "path", "symbol", "start_line", "end_line", "text", "content_hash"]
    with pytest.raises(FrozenInstanceError):
        result.text = "mutation"


def test_join_reversed_and_adjacent_windows_are_canonical_and_preserve_anchor_receipt():
    first, second = snippet("first", "a\nb\n"), snippet("second", "c\nd\n", 3)
    definition = ("src/code.py", "run", 1, 4, 23)
    forward = join_windows(first, second, definition_key=definition, anchor_rank=1, companion_rank=2)
    reverse = join_windows(second, first, definition_key=definition, anchor_rank=2, companion_rank=1)
    assert forward.id == reverse.id and forward.text == reverse.text == "a\nb\nc\nd\n"
    assert forward.assembly["score_anchor_id"] == "first"
    assert reverse.assembly["score_anchor_id"] == "second"


@pytest.mark.parametrize("companion", [
    snippet("contained", "b\n", 2),
    snippet("equal", "a\nb\nc\n"),
    snippet("gap", "e\n", 5),
    snippet("mismatch", "WRONG\nd\n", 3),
    snippet("path", "c\nd\n", 3, path="src/other.py"),
    snippet("symbol", "c\nd\n", 3, symbol="sibling"),
    Snippet("hash", "src/code.py", "run", 3, 4, "c\nd\n", "0" * 64),
    Snippet("bounds", "src/code.py", "run", 3, 9, "c\nd\n", sha("c\nd\n")),
])
def test_join_rejects_no_novel_source_gaps_inconsistent_bytes_and_other_definitions(companion):
    assert join_windows(snippet("first", "a\nb\nc\n"), companion,
                        definition_key=("src/code.py", "run", 1, 10),
                        anchor_rank=1, companion_rank=2) is None


@pytest.mark.parametrize("definition", [None, (), ("src/code.py", "run", 1, 3),
                                       ("src/code.py", "other", 1, 10),
                                       ("src/code.py", "run", True, 10)])
def test_join_requires_definition_bounds_and_identity(definition):
    assert join_windows(snippet("first", "a\nb\nc\n"), snippet("second", "c\nd\n", 3),
                        definition_key=definition, anchor_rank=1, companion_rank=2) is None


def test_join_rejects_artificial_adjacency_after_unterminated_physical_line():
    assert join_windows(snippet("first", "a"), snippet("second", "b\n", 2),
                        definition_key=("src/code.py", "run", 1, 2),
                        anchor_rank=1, companion_rank=2) is None


def test_extension_preserves_old_rows_rewrites_dependency_and_explains_inherited_score():
    anchor = row("anchor", "a\nb\nc\n")
    dependency = row("helper", "helper()\n", path="src/helper.py", symbol="helper",
                     origin="dependency", related_to="anchor")
    companion = row("companion", "c\nd\ne\n", 3)
    original = response([anchor, dependency])
    saved, candidates = copy.deepcopy(original), [anchor, companion]
    saved_candidates = copy.deepcopy(candidates)
    result = extend(original, candidates, keys=key("anchor", "companion"))
    new, helper = result["results"]
    assert new["source"] == "a\nb\nc\nd\ne\n" and new["end_line"] == new["window_end_line"] == 5
    assert new["content_hash"] == new["source_hash"] == sha(new["source"])
    assert new["h2o_score"] is None and new["qwen_score"] is None
    assert new["retrieval_score"] == anchor["retrieval_score"]
    assert new["assembly"]["inherited_scores"] == {
        "retrieval_score": 0.9, "h2o_score": 0.8, "qwen_score": 0.7}
    assert helper == {**dependency, "related_to": new["id"]}
    assert result["_window_assembly"]["old_to_new_ids"] == {"anchor": new["id"]}
    assert result["_window_assembly"]["coverage_preserved"]
    assert not new["truncated"] and not result["budget_exhausted"]
    assert original == saved and candidates == saved_candidates
    exact_count(result)


def test_clipped_anchor_can_continue_its_own_full_retrieved_window():
    full = row("anchor", "a\nb\nc\nd\n")
    clipped = {**full, "source": "a\nb\n", "end_line": 2, "source_hash": sha("a\nb\n"),
               "truncated": True}
    original = response([clipped], budget_exhausted=True)
    original["token_usage"]["truncated"] = True
    result = extend(original, [full], keys=key("anchor"))
    new = result["results"][0]
    assert new["source"] == full["source"] and not new["truncated"]
    assert new["assembly"]["members"][0]["end_line"] == 4
    assert new["assembly"]["members"][0]["used_end_line"] == 2
    assert new["assembly"]["members"][1]["used_end_line"] == 4
    assert result["budget_exhausted"] and result["token_usage"]["truncated"]
    exact_count(result)


def test_matching_overlay_bindings_and_two_anchors_preserve_source_and_references():
    overlay = {"source_origin": "overlay", "buffer_version": 3, "buffer_hash": "f" * 64}
    first = row("first", "a\nb\n", **overlay)
    second = row("second", "x\ny\n", path="src/other.py", symbol="other")
    helper = row("helper", "help()\n", path="src/helper.py", symbol="helper",
                 origin="dependency", related_to="first")
    pool = [first, row("first_tail", "b\nc\n", 2, **overlay), second,
            row("second_tail", "y\nz\n", 2, path="src/other.py", symbol="other")]
    definitions = {**key("first", "first_tail"),
                   **key("second", "second_tail", path="src/other.py", symbol="other")}
    original = response([first, second, helper])
    result = extend(original, pool, keys=definitions)
    assert [item["source"] for item in result["results"]] == ["a\nb\nc\n", "x\ny\nz\n", "help()\n"]
    assert result["results"][2]["related_to"] == result["results"][0]["id"]
    assert result["results"][0]["source_origin"] == "overlay"
    assert result["results"][0]["buffer_version"] == 3
    assert result["results"][0]["buffer_hash"] == "f" * 64
    assert len(result["_window_assembly"]["applied"]) == 2
    exact_count(result)


def test_dependency_and_composite_candidates_are_not_claimed_as_raw_retrieval():
    original = response([row("a", "a\nb\n")])
    candidates = [row("b", "b\nc\n", 2, origin="dependency", related_to="a"),
                  row("c", "b\nc\n", 2, assembly={"kind": "connected_windows"})]
    result = extend(original, candidates, keys=key("a", "b", "c"))
    assert without_debug(result) == original
    assert result["_window_assembly"]["trials"] == 0


def test_only_one_companion_is_accepted_per_original_anchor_in_raw_rank_order():
    anchor = row("a", "one\ntwo\n")
    near, far = row("near", "two\nthree\n", 2), row("far", "two\nthree\nfour\n", 2)
    result = extend(response([anchor]), [far, near], keys=key("a", "near", "far"))
    assert result["results"][0]["source"] == "one\ntwo\nthree\nfour\n"
    assert result["results"][0]["assembly"]["companion_rank"] == 1
    assert result["_window_assembly"]["trials"] == 1
    assert len(result["_window_assembly"]["applied"]) == 1


def test_ranked_candidates_explicitly_override_default_candidate_order():
    anchor = row("a", "one\ntwo\n")
    first, second = row("first", "two\nthree\n", 2), row("second", "two\nthree\nfour\n", 2)
    result = extend(response([anchor]), [first], ranked_candidates=[second, first],
                    keys=key("a", "first", "second"))
    assert result["_window_assembly"]["applied"][0]["companion_id"] == "second"


@pytest.mark.parametrize("change", [
    {"path": "src/other.py"}, {"symbol": "other"}, {"source_origin": "saved"},
    {"source_origin": "overlay", "buffer_version": 2, "buffer_hash": "a" * 64},
    {"source_hash": "0" * 64}, {"content_hash": "0" * 64}, {"truncated": True},
    {"window_end_line": 100},
])
def test_extension_rejects_different_or_unverifiable_source_binding(change):
    anchor, companion = row("a", "a\nb\n"), row("b", "b\nc\n", 2)
    original = response([anchor])
    companion.update(change)
    result = extend(original, [companion], keys=key("a", "b"))
    assert without_debug(result) == original
    assert not result["_window_assembly"]["applied"]


@pytest.mark.parametrize("anchor_change", [{"origin": "dependency", "related_to": "owner"},
                                            {"assembly": {"kind": "connected_windows"}}])
def test_dependency_or_composite_anchor_is_never_extended(anchor_change):
    anchor, companion = row("a", "a\nb\n", **anchor_change), row("b", "b\nc\n", 2)
    original = response([anchor])
    result = extend(original, [companion], keys=key("a", "b"))
    assert without_debug(result) == original
    assert result["_window_assembly"]["trials"] == 0


@pytest.mark.parametrize("keys", [None, {}, {"a": ("src/code.py", "run", 1, 10)},
                                  {"a": ("src/code.py", "run", 1, 10),
                                   "b": ("src/code.py", "run", 2, 10)}])
def test_unknown_or_different_actual_definitions_are_not_merged(keys):
    original = response([row("a", "a\nb\n")])
    result = extend(original, [row("b", "b\nc\n", 2)], keys=keys)
    assert without_debug(result) == original


@pytest.mark.parametrize("candidate", [row("b", "WRONG\nc\n", 2), row("b", "c\n", 4),
                                       row("b", "a\nb\nc\n", 1), row("b", "b\n", 2)])
def test_disjoint_backward_contained_or_inconsistent_candidate_does_not_extend(candidate):
    anchor = row("a", "b\n", 2)
    original = response([anchor])
    result = extend(original, [candidate], keys=key("a", "b"))
    assert without_debug(result) == original


def test_character_clipping_retains_full_union_hash_and_only_appends_complete_physical_lines():
    anchor = row("a", 'value = "é🦉\u2028inside\u2029data"\r\n')
    companion = row("b", anchor["source"] + "next = 1\r" + "last = 2", 1)
    original = response([anchor])
    budget = len(anchor["source"]) + len("next = 1\r")
    result = extend(original, [companion], keys=key("a", "b"), source_budget=budget)
    new = result["results"][0]
    assert new["source"] == anchor["source"] + "next = 1\r"
    assert new["end_line"] == 2 and new["window_end_line"] == 3
    assert new["source_hash"] == sha(new["source"])
    assert new["content_hash"] == sha(companion["source"])
    assert new["truncated"] and result["budget_exhausted"]
    assert not result["token_usage"]["truncated"]
    assert result["total_source_chars"] == budget
    exact_count(result)


def test_token_clipping_trims_only_new_suffix_and_keeps_other_row_source_immutable():
    anchor = row("a", "original()\n")
    dependency = row("helper", "helper()\n", path="src/helper.py", symbol="helper",
                     origin="dependency", related_to="a")
    companion = row("b", anchor["source"] + "".join(f"value_{i} = '😀🦉';\n" for i in range(40)))
    original = response([anchor, dependency])
    budget = original["token_usage"]["tokens"] + 150
    original["token_usage"]["budget"] = budget
    context.count_context(original)
    result = extend(original, [anchor, companion], keys=key("a", "b"), token_budget=budget)
    new = result["results"][0]
    assert new["source"].startswith(anchor["source"]) and new["source"] != anchor["source"]
    assert new["source"] != companion["source"] and new["truncated"]
    assert result["results"][1]["source"] == dependency["source"]
    assert result["results"][1]["related_to"] == new["id"]
    assert result["token_usage"]["truncated"] and result["budget_exhausted"]
    assert result["_window_assembly"]["applied"][0]["token_clipped"]
    exact_count(result)


@pytest.mark.parametrize("limit", ["source", "tokens"])
def test_no_fitting_new_line_rolls_back_flags_and_tries_later_companion(limit):
    anchor = row("a", "a\n")
    large = row("large", "a\n" + "😀" * 1000 + "\n")
    small = row("small", "a\nb\n")
    original = response([anchor])
    budget = original["token_usage"]["tokens"] + 50
    original["token_usage"]["budget"] = budget
    context.count_context(original)
    result = extend(original, [large, small], keys=key("a", "large", "small"),
                    source_budget=4 if limit == "source" else 12000, token_budget=budget)
    assert result["results"][0]["source"] == "a\nb\n"
    assert not result["budget_exhausted"] and not result["token_usage"]["truncated"]
    assert result["_window_assembly"]["trials"] == 2
    assert result["_window_assembly"]["rejected"][0]["reason"] == (
        "no_character_suffix" if limit == "source" else "no_token_suffix")
    exact_count(result)


def test_noncontained_trials_are_bounded_globally_and_skips_do_not_spend_limit():
    anchor = row("a", "a\n")
    contained = [row(f"contained{i}", "a\n") for i in range(10)]
    large = [row(f"large{i}", "a\n" + "x" * 100 + "\n") for i in range(8)]
    later = row("later", "a\nb\n")
    pool = [*contained, *large, later]
    original = response([anchor], token_budget=None)
    result = extend(original, pool, keys=key("a", *(item["id"] for item in pool)),
                    source_budget=4, token_budget=None)
    assert without_debug(result) == original
    assert result["_window_assembly"]["trials"] == 8
    assert result["_window_assembly"]["trial_limit_reached"]


@pytest.mark.parametrize("budget_kind", ["source", "tokens", "results"])
def test_invalid_base_budgets_fall_back_to_exact_original_response(budget_kind):
    original = response([row("a", "a\nb\n")])
    result = extend(original, [row("b", "b\nc\n", 2)], keys=key("a", "b"),
                    source_budget=0 if budget_kind == "source" else 12000,
                    token_budget=0 if budget_kind == "tokens" else 10000,
                    max_results=0 if budget_kind == "results" else 8)
    assert without_debug(result) == original
    assert result["_window_assembly"]["fallback_reason"] == "base_budget_exceeded"


def test_source_only_extension_does_not_inject_token_usage_or_load_tokenizer():
    original = response([row("a", "a\nb\n")], token_budget=None)
    with patch("semantic_search.window_assembly.count_context", side_effect=AssertionError("tokenizer")):
        result = extend(original, [row("b", "b\nc\n", 2)], keys=key("a", "b"), token_budget=None)
    assert result["results"][0]["source"] == "a\nb\nc\n"
    assert "token_usage" not in result


def test_invalid_original_hash_cannot_be_extended_and_preservation_guard_rolls_back():
    original = response([row("a", "a\nb\n", source_hash="0" * 64)])
    result = extend(original, [row("b", "b\nc\n", 2)], keys=key("a", "b"))
    assert without_debug(result) == original
    assert result["_window_assembly"]["fallback_reason"] == "invalid_original_source"
    valid = response([row("a", "a\nb\n")])
    with patch("semantic_search.window_assembly._preserved", side_effect=[True, False]):
        result = extend(valid, [row("b", "b\nc\n", 2)], keys=key("a", "b"))
    assert without_debug(result) == valid
    assert result["_window_assembly"]["fallback_reason"] == "original_source_not_preserved_during_extension"


def test_cancellation_during_trial_leaves_all_inputs_unchanged():
    original = response([row("a", "a\nb\n")])
    candidates = [row("b", "b\nc\nd\n", 2)]
    saved, saved_candidates, calls = copy.deepcopy(original), copy.deepcopy(candidates), []

    class Cancelled(Exception):
        pass

    def check():
        calls.append(True)
        if len(calls) == 5:
            raise Cancelled

    with pytest.raises(Cancelled):
        extend(original, candidates, keys=key("a", "b"), cancel_check=check)
    assert original == saved and candidates == saved_candidates


def test_full_row_and_mcp_compact_contracts_exclude_assembly_diagnostics():
    full = "a\nb\nc\nd\n"
    result = extend(response([row("a", "a\nb\n")]), [row("b", "b\nc\nd\n", 2)],
                    keys=key("a", "b"), source_budget=6)
    new = result["results"][0]
    # Equivalent to saved/overlay VS Code evidence checks for the pinned source.
    lines = physical_lines(full)
    assert "".join(lines[new["start_line"] - 1:new["end_line"]]) == new["source"]
    assert sha("".join(lines[new["start_line"] - 1:new["window_end_line"]])) == new["content_hash"]
    assert new["end_line"] <= new["window_end_line"]
    envelope = context.compact_result(result)
    assert set(envelope["results"][0]) == {
        "path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated",
        "origin", "related_to"}
    before = context.serialize_context(envelope)
    new["assembly"]["debug"] = "😀" * 1000
    result["_window_assembly"]["debug"] = "🦉" * 1000
    assert context.serialize_context(context.compact_result(result)) == before
    exact_count(result)
