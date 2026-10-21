"""CPU safety contracts for post-packing containment and bounded backfill."""
import copy
import hashlib
from unittest.mock import patch

import pytest

from semantic_search import context
from semantic_search.context_backfill import coalesce_context
from semantic_search.source import physical_lines


def row(identifier, source, start=1, path="src/code.py", **metadata):
    end = start + len(physical_lines(source)) - 1
    return {"id": identifier, "path": path, "symbol": identifier,
            "start_line": start, "end_line": end, "window_end_line": end,
            "source": source, "source_hash": hashlib.sha256(source.encode()).hexdigest(),
            "content_hash": "full-window-" + identifier, "truncated": False,
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


def exact_count(result):
    actual = len(context.encoding().encode(context.serialize_context(context.compact_result(result)),
                                           disallowed_special=()))
    assert actual == result["token_usage"]["tokens"]
    assert result["token_usage"]["encoding"] == context.ENCODING
    assert actual <= result["token_usage"]["budget"]
    return actual


def without_debug(result):
    return {key: value for key, value in result.items() if key != "_selection_backfill"}


@pytest.mark.parametrize("child_first", [True, False])
def test_contained_child_coalesces_in_either_rank_order_and_retains_original_tail(child_first):
    parent = row("parent", "one\ntwo\nthree\n")
    child = row("child", "two\n", start=2, origin="dependency", related_to="parent")
    tail = row("tail", "old tail\n", path="src/tail.py", truncated=True,
               source_origin="open_buffer", buffer_version=7, buffer_hash="buffer-full")
    delivered = [child, parent, tail] if child_first else [parent, child, tail]
    original = response(delivered, budget_exhausted=True)
    saved = copy.deepcopy(original)
    extra = row("extra", "new body\n", path="src/extra.py")
    candidates = [child, parent, tail, extra]
    candidate_saved = copy.deepcopy(candidates)
    result = coalesce_context(original, candidates, max_results=3,
                              source_budget=1000, token_budget=10000)
    assert result["results"] == [parent, tail, extra]
    assert result["budget_exhausted"]
    assert result["_selection_backfill"]["coalesced_ids"] == ["child"]
    assert result["_selection_backfill"]["appended_ids"] == ["extra"]
    assert result["_selection_backfill"]["coverage_before"]
    assert result["_selection_backfill"]["coverage_after"]
    assert original == saved
    assert candidates == candidate_saved
    result["results"][0]["source"] = "changed copy"
    assert original == saved


def test_equal_literal_bounds_keep_first_rank_with_all_its_flags_and_metadata():
    first = row("first", "same\n", truncated=True, source_hash="preserve-delivered-hash",
                origin="dependency", related_to="anchor", source_origin="open_buffer",
                buffer_version=9, buffer_hash="original-buffer", custom={"nested": True})
    second = row("second", "same\n", origin="retrieval")
    original = response([first, second], budget_exhausted=True)
    original["token_usage"]["truncated"] = True
    result = coalesce_context(original, [], max_results=2, source_budget=100,
                              token_budget=10000)
    assert result["results"] == [first]
    assert result["token_usage"]["truncated"]
    assert result["budget_exhausted"]
    assert result["_selection_backfill"]["coalesced_ids"] == ["second"]
    exact_count(result)


@pytest.mark.parametrize("other", [
    row("other", "same\n", path="src/other.py"),
    row("other", "same\r\n"),
    row("other", "different\n"),
])
def test_different_paths_line_endings_or_bytes_never_coalesce(other):
    original = response([row("first", "same\n"), other])
    result = coalesce_context(original, [row("new", "new\n")], max_results=3,
                              source_budget=1000, token_budget=10000)
    assert without_debug(result) == original
    assert result["_selection_backfill"]["appended_ids"] == []
    assert result["_selection_backfill"]["coalesced_ids"] == []


def test_literal_physical_crlf_bare_cr_and_unicode_are_preserved():
    text = 'one\r\nvalue = "é🦉\u2028inside\u2029data"\rthree\n'
    parent = row("parent", text)
    child = row("child", physical_lines(text)[1], start=2)
    extra = row("extra", "é😀\r", start=7, path="src/é.py")
    original = response([parent, child])
    result = coalesce_context(original, [extra], max_results=2, source_budget=1000,
                              token_budget=10000)
    assert result["results"] == [parent, extra]
    assert result["results"][0]["source"] == text
    assert result["results"][1]["end_line"] == 7
    exact_count(result)


def test_partial_overlap_and_union_of_separate_rows_do_not_remove_source():
    cases = [
        [row("first", "one\ntwo\nthree\n"), row("partial", "two\nthree\nfour\n", start=2)],
        [row("first", "one\ntwo\n"), row("second", "three\nfour\n", start=3),
         row("spans_both", "two\nthree\n", start=2)],
    ]
    for rows in cases:
        original = response(rows)
        result = coalesce_context(original, [row("extra", "extra\n", path="src/extra.py")],
                                  max_results=8, source_budget=1000, token_budget=10000)
        assert result["results"] == rows
        assert result["_selection_backfill"]["coalesced_ids"] == []


def test_only_actual_delivered_bounds_count_and_uncertain_metadata_falls_back():
    clipped = row("clipped", "one\ntwo\n", window_end_line=50, truncated=True)
    separate = row("separate", "three\n", start=3)
    original = response([clipped, separate])
    result = coalesce_context(original, [], max_results=2, source_budget=1000, token_budget=10000)
    assert result["results"] == [clipped, separate]
    malformed = response([row("bad", "one\n", end_line=100), row("child", "one\n")])
    result = coalesce_context(malformed, [], max_results=2, source_budget=1000, token_budget=10000)
    assert without_debug(result) == malformed
    assert result["_selection_backfill"]["fallback_reason"] == "original_source_not_covered"


def test_only_new_row_is_token_clipped_while_old_prefix_tail_and_metadata_stay_fixed():
    parent = row("parent", "one\ntwo\nthree\n")
    child = row("child", "two\n", start=2)
    tail = row("tail", "preserve old tail\n", path="src/tail.py", truncated=True,
               source_hash="old-delivered-hash", content_hash="old-full-hash")
    original = response([parent, child, tail])
    budget = original["token_usage"]["tokens"] + 100
    original["token_usage"]["budget"] = budget
    context.count_context(original)
    extra = row("extra", "".join(f'new_value_{i} = "é🦉";\r\n' for i in range(100)),
                start=50, path="src/extra.ts", origin="dependency", related_to="parent",
                source_origin="open_buffer", buffer_version=42, buffer_hash="buffer-hash")
    saved = copy.deepcopy(original)
    result = coalesce_context(original, [extra], max_results=3, source_budget=10000,
                              token_budget=budget)
    assert result["results"][:2] == [parent, tail]
    new = result["results"][2]
    assert new["id"] == "extra"
    assert new["source"] != extra["source"]
    assert new["source"] == "".join(physical_lines(extra["source"])[:len(physical_lines(new["source"]))])
    assert new["end_line"] == 49 + len(physical_lines(new["source"]))
    assert new["source_hash"] == hashlib.sha256(new["source"].encode()).hexdigest()
    assert new["content_hash"] == extra["content_hash"]
    for field in ("origin", "related_to", "source_origin", "buffer_version", "buffer_hash", "window_end_line"):
        assert new[field] == extra[field]
    assert new["truncated"] and result["token_usage"]["truncated"] and result["budget_exhausted"]
    assert original == saved
    exact_count(result)


def test_source_clipping_uses_whole_lines_and_truthful_new_source_metadata():
    parent, child = row("parent", "a\nb\n"), row("child", "b\n", start=2)
    extra = row("extra", "é😀\r" + "does not fit\r\n", start=8, path="src/new.py")
    original = response([parent, child])
    result = coalesce_context(original, [extra], max_results=2, source_budget=7, token_budget=10000)
    assert result["results"][0] == parent
    new = result["results"][1]
    assert new["source"] == "é😀\r" and new["end_line"] == 8
    assert new["source_hash"] == hashlib.sha256("é😀\r".encode()).hexdigest()
    assert new["truncated"] and result["budget_exhausted"]
    assert not result["token_usage"]["truncated"]
    assert result["total_source_chars"] == 7
    exact_count(result)


@pytest.mark.parametrize("limit_kind", ["source", "tokens"])
def test_no_fitting_line_rolls_back_flags_and_allows_a_later_candidate(limit_kind):
    parent, child = row("parent", "a\nb\n"), row("child", "b\n", start=2)
    original = response([parent, child])
    too_large = row("large", "😀" * 1000 + "\n", path="src/large.py")
    later = row("later", "ok\n", path="src/later.py")
    budget = original["token_usage"]["tokens"] + 120
    original["token_usage"]["budget"] = budget
    context.count_context(original)
    result = coalesce_context(original, [too_large, later], max_results=2,
                              source_budget=10000 if limit_kind == "tokens" else 7,
                              token_budget=budget)
    assert result["results"] == [parent, later]
    assert not result["budget_exhausted"]
    assert not result["token_usage"]["truncated"]
    assert result["_selection_backfill"]["trials"] == 2
    assert result["_selection_backfill"]["skipped"][0]["reason"] == (
        "no_token_prefix" if limit_kind == "tokens" else "no_character_prefix")
    exact_count(result)


def test_candidate_metadata_can_fit_while_no_complete_source_line_fits():
    parent, child = row("parent", "a\nb\n"), row("child", "b\n", start=2)
    original = response([parent, child])
    extra = row("extra", "😀" * 500 + "\n", path="src/new.py")
    metadata_only = copy.deepcopy(extra)
    metadata_only.update(source="", end_line=0, source_hash=hashlib.sha256(b"").hexdigest(), truncated=True)
    envelope = response([parent, metadata_only], budget_exhausted=True)
    envelope["token_usage"]["truncated"] = True
    budget = max(context.count_context(envelope), original["token_usage"]["tokens"]) + 1
    original["token_usage"]["budget"] = budget
    context.count_context(original)
    result = coalesce_context(original, [extra], max_results=2, source_budget=10000,
                              token_budget=budget)
    assert result["results"] == [parent]
    assert not result["budget_exhausted"] and not result["token_usage"]["truncated"]
    assert result["_selection_backfill"]["appended_ids"] == []
    exact_count(result)


def test_original_ids_and_covered_full_sources_do_not_spend_eight_trial_limit():
    parent, child = row("parent", "a\nb\n"), row("child", "b\n", start=2)
    original = response([parent, child], token_budget=None)
    covered = [row(f"covered{i}", "a\n") for i in range(9)]
    failures = [row(f"wide{i}", "x" * 100, path=f"src/wide{i}.py") for i in range(8)]
    later = row("later", "x\n", path="src/later.py")
    result = coalesce_context(original, [parent, child, *covered, *failures, later],
                              max_results=2, source_budget=6)
    assert result["results"] == [parent]
    debug = result["_selection_backfill"]
    assert debug["trials"] == 8 and debug["trial_limit_reached"]
    assert len([item for item in debug["skipped"] if item["reason"] == "covered_full_source"]) == 9
    assert "token_usage" not in result


def test_clipped_prefix_already_covered_does_not_append_duplicate_or_leave_flags():
    parent, child = row("parent", "a\nb\n"), row("child", "b\n", start=2)
    extra = row("extra", "a\nb\nlong suffix\n")
    original = response([parent, child], token_budget=None)
    result = coalesce_context(original, [extra], max_results=2, source_budget=8)
    assert result["results"] == [parent]
    assert not result["budget_exhausted"]
    assert result["_selection_backfill"]["skipped"] == [{"id": "extra", "reason": "covered_prefix"}]


def test_source_only_support_never_loads_tokenizer_or_injects_usage():
    parent, child = row("parent", "a\nb\n"), row("child", "b\n", start=2)
    original = response([parent, child], token_budget=None)
    extra = row("extra", "x\n", path="src/new.py")
    with patch("semantic_search.context_backfill.count_context", side_effect=AssertionError("token count")):
        result = coalesce_context(original, [extra], max_results=2, source_budget=6)
    assert result["results"] == [parent, extra]
    assert "token_usage" not in result
    assert result["total_source_chars"] == 6


def test_empty_or_unchanged_responses_do_not_backfill_without_coalescing():
    for rows in ([], [row("single", "single\n")]):
        original = response(rows)
        result = coalesce_context(original, [row("extra", "new\n")], max_results=8,
                                  source_budget=1000, token_budget=10000)
        assert without_debug(result) == original
        assert result["_selection_backfill"]["trials"] == 0
        assert result["_selection_backfill"]["coverage_before"]
        assert result["_selection_backfill"]["coverage_after"]
        exact_count(result)


@pytest.mark.parametrize("budget_kind", ["source", "tokens", "results"])
def test_base_budget_guard_falls_back_without_mutating_old_response(budget_kind):
    original = response([row("fixed", "fixed\n")])
    saved = copy.deepcopy(original)
    result = coalesce_context(original, [], max_results=0 if budget_kind == "results" else 1,
                              source_budget=0 if budget_kind == "source" else 100,
                              token_budget=0 if budget_kind == "tokens" else 10000)
    assert without_debug(result) == original == saved
    assert result["_selection_backfill"]["fallback_reason"] == "base_budget_exceeded"


def test_coverage_failure_after_backfill_rolls_back_to_original_response():
    parent, child = row("parent", "a\nb\n"), row("child", "b\n", start=2)
    original = response([parent, child])
    with patch("semantic_search.context_backfill._all_covered", side_effect=[True, False]):
        result = coalesce_context(original, [row("extra", "x\n", path="src/new.py")],
                                  max_results=2, source_budget=1000, token_budget=10000)
    assert without_debug(result) == original
    assert result["_selection_backfill"]["fallback_reason"] == "original_source_not_covered_after_backfill"


@pytest.mark.parametrize("during_trim", [False, True])
def test_cancellation_is_checked_for_each_trial_and_token_trim_without_mutation(during_trim):
    parent, child = row("parent", "a\nb\n"), row("child", "b\n", start=2)
    original = response([parent, child])
    extra = row("extra", "".join("😀" * 50 + "\n" for _ in range(20)), path="src/new.py")
    saved, calls = copy.deepcopy(original), []

    class Cancelled(Exception):
        pass

    def check():
        calls.append(True)
        # Initial check, two original-row checks, candidate check, trial check,
        # then at least one token-trimming check.
        if len(calls) == (6 if during_trim else 5):
            raise Cancelled

    with pytest.raises(Cancelled):
        coalesce_context(original, [extra], max_results=2, source_budget=10000,
                         token_budget=original["token_usage"]["tokens"] + 100,
                         cancel_check=check)
    assert original == saved
    assert len(calls) == (6 if during_trim else 5)


def test_debug_is_outside_the_exact_compact_envelope():
    parent, child = row("parent", "a\nb\n"), row("child", "b\n", start=2)
    result = coalesce_context(response([parent, child]), [], max_results=2,
                              source_budget=1000, token_budget=10000)
    before = context.serialize_context(context.compact_result(result))
    result["_selection_backfill"]["arbitrary_debug"] = "😀" * 1000
    assert context.serialize_context(context.compact_result(result)) == before
    exact_count(result)
