"""Invented proof checks for the pre-inference all-victims-protected skip."""
from copy import deepcopy
import hashlib
import random
from unittest.mock import patch

import pytest

from experiments.dependency_context.annotated_dependencies import SCHEMA
from experiments.dependency_context.guard import apply_dependency_guard
from experiments.dependency_context.skip import skip_certificate
from scripts.research_role_context import apply_role_guard
from semantic_search import context
from semantic_search.semantic_context import semantic_final_context


def fixture(*, writers=3):
    raw, keys = [], {}
    for i in range(10):
        body = "    self.state = value\n" if 8-writers <= i < 8 else "    return value\n"
        source = f"def window_{i}(self, value):\n" + body
        digest = hashlib.sha256(source.encode()).hexdigest()
        item = {"id": f"w{i}", "path": f"w{i}.py", "symbol": f"window_{i}", "start_line": 1,
            "end_line": 2, "window_end_line": 2, "source": source, "source_hash": digest,
            "content_hash": digest, "truncated": False, "origin": "retrieval", "related_to": None,
            "retrieval_score": .2, "h2o_score": None, "qwen_score": None}
        raw.append(item)
        keys[item["id"]] = [item["path"], item["symbol"], 1, 2]
    reference = {"query": "load alpha payload and render beta template", "results": deepcopy(raw[:8]),
        "match_status": "candidates", "total_source_chars": sum(len(r["source"]) for r in raw[:8]),
        "budget_exhausted": False, "token_usage": {"encoding": context.ENCODING,
            "tokens": 0, "budget": 4096, "truncated": False}}
    context.count_context(reference)
    return reference, raw, keys, {"schema": SCHEMA, "accepted": []}


def test_full_context_with_all_possible_victims_protected_certifies_zero_work():
    reference, raw, keys, facts = fixture()
    before = deepcopy((reference, raw, keys, facts))
    certificate = skip_certificate(reference, raw, dependencies=facts, definition_keys=keys)
    assert certificate["eligible"]
    assert certificate["semantic_protected_ids"] == [f"w{i}" for i in range(5)]
    assert certificate["role_protected_ids"] == [f"w{i}" for i in range(5, 8)]
    assert not certificate["unprotected_ids"]
    assert all(v == 0 for v in certificate["calls"].values())
    assert (reference, raw, keys, facts) == before


def test_proof_matches_actual_two_exchange_producer_and_terminal_guards_across_scores():
    reference, raw, keys, facts = fixture()
    assert skip_certificate(reference, raw, dependencies=facts, definition_keys=keys)["eligible"]
    changed = 0
    for seed in range(30):
        rng = random.Random(seed)
        scores = {r["id"]: tuple(rng.randrange(-100, 101) for _ in range(3)) for r in raw}
        # Force an available positive exchange while keeping arbitrary floors.
        scores["w8"] = (1000, 1000, 1000)
        scores["w9"] = (2000, 2000, 2000)
        facets = [reference["query"], "load alpha payload", "render beta template"]
        with patch("semantic_search.semantic_features.validated_scores", return_value=(facets, scores)):
            proposed, _ = semantic_final_context(reference, raw, {}, definition_keys=keys)
        changed += proposed != reference
        role, _ = apply_role_guard(reference, proposed, definition_keys=keys)
        final, _ = apply_dependency_guard(reference, role, dependencies=facts, definition_keys=keys)
        assert final == reference
    assert changed == 30


def test_one_unprotected_possible_victim_declines_skip():
    reference, raw, keys, facts = fixture(writers=2)
    receipt = skip_certificate(reference, raw, dependencies=facts, definition_keys=keys)
    assert not receipt["eligible"]
    assert receipt["unprotected_ids"] == ["w5"]


@pytest.mark.parametrize("count", [0, 1, 7, 9])
def test_free_slot_or_oversized_context_never_certifies(count):
    reference, raw, keys, facts = fixture()
    reference["results"] = deepcopy(raw[:count])
    receipt = skip_certificate(reference, raw, dependencies=facts, definition_keys=keys)
    assert not receipt["eligible"] and receipt["reason"] == "context_not_full"


def test_invalid_graph_cannot_issue_certificate():
    reference, raw, keys, facts = fixture()
    receipt = skip_certificate(reference, raw, dependencies={}, definition_keys=keys)
    assert not receipt["eligible"] and receipt["reason"] == "invalid_protection_facts"


@pytest.mark.parametrize("kind", [ValueError, TypeError, RuntimeError, KeyboardInterrupt])
def test_cancellation_preserves_original_exception(kind):
    reference, raw, keys, facts = fixture()
    error = kind("stop")
    calls = []

    def cancel():
        calls.append(True)
        if len(calls) == 5:
            raise error

    with pytest.raises(kind) as caught:
        skip_certificate(reference, raw, dependencies=facts, definition_keys=keys, cancel_check=cancel)
    assert caught.value is error
