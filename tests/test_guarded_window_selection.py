"""Source-only acceptance guards keep the previous context under any budget."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from semantic_search.context import compact_result, count_context, indexed_scope, serialize_context
from semantic_search.selection import guarded_connected_context


FIXTURE = json.loads((Path(__file__).parent / "fixtures/window_union.json").read_text())


def response(rows):
    value = {"query": "find implementation", "scope": indexed_scope("src"),
        "results": deepcopy(rows), "match_status": "candidates", "budget_exhausted": False,
        "total_source_chars": sum(len(row["source"]) for row in rows),
        "token_usage": {"encoding": "cl100k_base", "tokens": 0, "budget": 4096, "truncated": False}}
    count_context(value)
    return value


def anchor():
    row = deepcopy(FIXTURE["full"])
    row.pop("assembly")
    source = "".join(row["source"].splitlines(keepends=True)[:4])
    digest = hashlib.sha256(source.encode()).hexdigest()
    row.update(id="anchor", source=source, end_line=4, window_end_line=4,
               source_hash=digest, content_hash=digest)
    return row


def guard(proposal, reference, **kwargs):
    return guarded_connected_context(proposal, (), reference_factory=lambda: reference,
        max_results=8, source_budget=12000, token_budget=4096, **kwargs)


def test_guard_accepts_a_real_union_without_discarding_reference_source():
    proposal, reference = response([FIXTURE["full"]]), response([anchor()])
    originals = deepcopy((proposal, reference))
    actual = guard(proposal, reference)
    assert actual["_window_guard"]["accepted"]
    assert actual["results"] == proposal["results"]
    assert actual["_window_guard"]["reference_compact_sha256"] == hashlib.sha256(
        serialize_context(compact_result(reference)).encode()).hexdigest()
    assert actual["_window_guard"]["proposed_composites"] == 1
    assert (proposal, reference) == originals


def test_guard_rejects_budget_crowding_even_if_the_dropped_source_is_not_gold():
    extra = anchor()
    extra.update(id="other", path="src/other.py")
    reference = response([anchor(), extra])
    actual = guard(response([FIXTURE["full"]]), reference)
    assert actual["results"] == reference["results"]
    assert compact_result(actual) == compact_result(reference)
    assert actual["_window_guard"]["fallback_reason"] == "coverage_control_source_not_preserved"
    assert actual["_window_guard"]["accepted_composites"] == 0


def test_guard_rejects_identical_intervals_with_different_source_binding():
    reference, proposal = response([anchor()]), response([FIXTURE["full"]])
    reference["results"][0].update(source_origin="overlay", buffer_version=1, buffer_hash="a" * 64)
    actual = guard(proposal, reference)
    assert not actual["_window_guard"]["accepted"]
    assert actual["results"] == reference["results"]


def test_cancelled_guard_never_invokes_reference_or_mutates_response():
    original = response([FIXTURE["full"]])
    before = deepcopy(original)
    def cancelled():
        raise RuntimeError("cancelled")
    def forbidden_reference():
        raise AssertionError("reference invoked after cancellation")
    with pytest.raises(RuntimeError, match="cancelled"):
        guarded_connected_context(original, (), reference_factory=forbidden_reference,
            max_results=8, source_budget=12000, token_budget=4096, cancel_check=cancelled)
    assert original == before
