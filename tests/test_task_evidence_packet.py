"""Invented CPU contracts for research packet binding and accounting."""

from copy import deepcopy
import hashlib

import pytest

from semantic_search import context
from semantic_search.corpus import Snippet
from semantic_search.task_evidence import pack_task_evidence, task_evidence_plan
from semantic_search.task_evidence_adapter import TaskEvidenceAdapter


QUERY = "repair alpha behavior and preserve beta constraints"
NEEDS = ["implementation", "callers", "tests", "behavioral_constraints"]


def request(*, needed=...):
    value = {
        "query": QUERY,
        "retrieval": "dense",
        "rerank": False,
        "rerank_provider": "h2o",
        "candidates": 50,
        "max_results": 8,
        "source_budget": 12000,
        "token_budget": 4096,
        "include_dependencies": True,
        "overlays": [],
    }
    if needed is not ...:
        value["needed_evidence"] = needed
    return value


def snippet(number, *, path="src/control.py", body=None):
    symbol = f"implementation_{number}"
    text = body or f"def {symbol}():\n    return {number}\n"
    return Snippet(
        f"{path}:{symbol}:{number * 10 + 1}",
        path,
        symbol,
        number * 10 + 1,
        number * 10 + len(text.splitlines()),
        text,
        hashlib.sha256(text.encode()).hexdigest(),
    )


def source_row(item, score):
    return {
        "id": item.id,
        "path": item.path,
        "symbol": item.symbol,
        "start_line": item.start_line,
        "end_line": item.end_line,
        "window_end_line": item.end_line,
        "source": item.text,
        "source_hash": hashlib.sha256(item.text.encode()).hexdigest(),
        "content_hash": item.content_hash,
        "retrieval_score": float(score),
        "h2o_score": None,
        "qwen_score": None,
        "origin": "retrieval",
        "related_to": None,
        "truncated": False,
    }


def packet_fixture(*, candidate_body=None, token_budget=4096):
    items = [snippet(number) for number in range(1, 9)]
    items.append(snippet(9, path="src/extra.py", body=candidate_body))
    pairs = [(item, 100.0 - rank) for rank, item in enumerate(items)]
    keys = {
        item.id: (item.path, item.symbol, item.start_line, item.end_line)
        for item in items
    }
    selected, eligible, origins, plan = task_evidence_plan(
        pairs,
        needed_evidence=["implementation"],
        query=QUERY,
        max_results=8,
        include_dependencies=False,
        references=None,
        definition_keys=keys,
    )

    ranked_rows = [source_row(item, score) for item, score in pairs]
    baseline_rows = [source_row(item, score) for item, score in eligible]

    def reference():
        value = {
            "query": QUERY,
            "scope": context.indexed_scope("."),
            "match_status": "candidates",
            "results": deepcopy(baseline_rows),
            "total_source_chars": sum(len(row["source"]) for row in baseline_rows),
            "budget_exhausted": False,
        }
        return context.apply_token_budget(value, token_budget)

    standard, packet, receipt = pack_task_evidence(
        {},
        (),
        selection_plan=plan,
        ranked_candidates=ranked_rows,
        reference_factory=reference,
        max_results=8,
        source_budget=12000,
        token_budget=token_budget,
        definition_keys=keys,
    )
    return standard, packet, receipt, plan


def independent_tokens(packet):
    return len(
        context.encoding().encode(
            context.serialize_context(packet), disallowed_special=()
        )
    )


def test_bind_removes_only_needed_evidence_and_returns_defensive_request_copy():
    adapter = TaskEvidenceAdapter()
    original = request(needed=NEEDS)
    with pytest.raises(RuntimeError, match="without executing"):
        with adapter._bind(original) as run:
            backend_request = run.backend_request
            assert backend_request == request()
            assert "needed_evidence" not in backend_request
            backend_request["query"] = "mutated outside"
            assert run.backend_request == request()
    assert original == request(needed=NEEDS)


@pytest.mark.parametrize(
    "mutate",
    [
        None,
        lambda value: value.pop("query"),
        lambda value: value.update(evaluator_groups=[]),
        lambda value: value.update(needed_evidence=None),
        lambda value: value.update(needed_evidence=[]),
        lambda value: value.update(needed_evidence=("implementation",)),
        lambda value: value.update(
            needed_evidence=["implementation", "implementation"]
        ),
        lambda value: value.update(needed_evidence=["configuration"]),
        lambda value: value.update(max_results=True),
        lambda value: value.update(overlays={}),
    ],
)
def test_invalid_or_hidden_request_inputs_fail_before_any_hook_state(mutate):
    adapter = TaskEvidenceAdapter()
    value = request(needed=["implementation"])
    if mutate is None:
        value = []
    else:
        mutate(value)
    with pytest.raises((TypeError, ValueError)):
        with adapter._bind(value):
            pytest.fail("invalid request entered its binding")
    with pytest.raises(RuntimeError, match="active bound request"):
        adapter.select_context(
            [], query=QUERY, max_results=8, include_dependencies=False, references=None
        )


def test_context_state_rejects_nesting_and_is_cleared_after_exception():
    adapter = TaskEvidenceAdapter()
    with pytest.raises(RuntimeError, match="cannot be nested"):
        with adapter._bind(request()):
            with adapter._bind(request()):
                pass
    with pytest.raises(RuntimeError, match="synthetic failure"):
        with adapter._bind(request()):
            raise RuntimeError("synthetic failure")
    # A later condition is independent and receives the control request, but a
    # normal exit without executing it still fails closed.
    with pytest.raises(RuntimeError, match="without executing"):
        with adapter._bind(request()) as run:
            assert run.backend_request == request()


def test_treatment_packet_is_separate_and_counts_its_complete_metadata():
    standard, packet, receipt, plan = packet_fixture()
    assert receipt["accepted"] and receipt["candidate_id"] == plan["candidate_ids"][0]
    assert "task_evidence" not in standard
    assert packet["task_evidence"]["requested"] == ["implementation"]
    assert packet["task_evidence"]["needs"] == [
        {
            "kind": "implementation",
            "status": "candidate",
            "result_indexes": list(range(len(standard["results"]))),
        }
    ]
    assert packet["token_usage"]["tokens"] == independent_tokens(packet)
    assert packet["token_usage"]["tokens"] <= packet["token_usage"]["budget"]
    assert packet["token_usage"]["tokens"] > independent_tokens(
        context.compact_result(standard)
    )
    assert standard["results"][-1]["path"] == "src/extra.py"
    assert all(row in standard["results"] for row in standard["results"][:5])


def test_clipped_implementation_trials_safe_victims_until_full_candidate_fits():
    body = "def implementation_9():\n" + "".join(
        f"    value_{number} = 'alpha beta gamma delta {number}'\n"
        for number in range(120)
    )
    full_standard, full_packet, _, _ = packet_fixture(
        candidate_body=body, token_budget=4096
    )
    base_tokens = independent_tokens(context.compact_result(full_standard))
    assert full_packet["token_usage"]["tokens"] > base_tokens
    budget = min(
        full_packet["token_usage"]["tokens"] - 1,
        base_tokens
        + max(40, (full_packet["token_usage"]["tokens"] - base_tokens) // 2),
    )
    budget = max(512, budget)

    standard, packet, receipt, _ = packet_fixture(
        candidate_body=body, token_budget=budget
    )
    assert receipt["accepted"]
    assert len(receipt["trials"]) == 3
    assert all(
        trial["token_clipped"] and trial["reason"] == "token_budget_exhausted"
        for trial in receipt["trials"][:2]
    )
    assert receipt["trials"][2]["accepted"]
    delivered = standard["results"][-1]
    assert delivered["path"] == "src/extra.py" and not delivered["truncated"]
    assert packet["token_usage"]["tokens"] == independent_tokens(packet) <= budget
    assert packet["task_evidence"]["needs"][0]["status"] == "candidate"


def test_repeated_plans_and_packets_are_byte_identical_and_inputs_are_immutable():
    first = packet_fixture()
    second = packet_fixture()
    assert first == second
    assert context.serialize_context(first[1]) == context.serialize_context(second[1])


def test_bound_hooks_reject_manual_calls_that_bypass_execute():
    adapter = TaskEvidenceAdapter()
    with pytest.raises(RuntimeError, match="requires TaskEvidenceAdapter.execute"):
        with adapter._bind(request()):
            adapter.select_context(
                [],
                query=QUERY,
                max_results=8,
                include_dependencies=True,
                references=None,
            )
