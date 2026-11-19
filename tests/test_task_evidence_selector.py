"""Invented-source contracts for the research-only task-evidence selector."""

from copy import deepcopy
import hashlib
import json

import pytest

from semantic_search import context
from semantic_search.corpus import Snippet
from semantic_search.source import physical_lines
from semantic_search import task_evidence as candidate


QUERY = "change alpha streaming and preserve beta cleanup with regression tests"
NEEDS = ["implementation", "callers", "tests", "behavioral_constraints"]


def snippet(number, *, path="src/main.py", symbol=None, source=None, start=None):
    symbol = symbol or f"unit_{number}"
    source = source or f"def {symbol}():\n    return {number}\n"
    start = start or number * 10 + 1
    return Snippet(
        str(number),
        path,
        symbol,
        start,
        start + len(physical_lines(source)) - 1,
        source,
        hashlib.sha256(source.encode()).hexdigest(),
    )


class Graph:
    def __init__(self, rows, edges=()):
        self.windows = {}
        for row in rows:
            self.windows.setdefault((row.path, row.symbol), []).append(row)
        self.edges = {}
        for owner, target in edges:
            self.edges.setdefault((owner.path, owner.symbol), []).append(
                (target.path, target.symbol)
            )

    def dependencies(self, row, limit=1):
        output = []
        for target in self.edges.get((row.path, row.symbol), ()):
            output.extend(
                sorted(self.windows.get(target, ()), key=lambda item: item.start_line)[
                    :1
                ]
            )
            if len(output) >= limit:
                break
        return output


def definition_keys(rows):
    return {
        row.id: (row.path, row.symbol, row.start_line, row.end_line) for row in rows
    }


def row(item, score=1.0, *, origin="retrieval", related_to=None):
    return {
        "id": item.id,
        "path": item.path,
        "symbol": item.symbol,
        "start_line": item.start_line,
        "end_line": item.end_line,
        "window_end_line": item.end_line,
        "source": item.text,
        "source_hash": item.content_hash,
        "content_hash": item.content_hash,
        "retrieval_score": score,
        "h2o_score": None,
        "qwen_score": None,
        "origin": origin,
        "related_to": related_to,
        "truncated": False,
    }


def response(rows, *, token_budget=4096):
    value = {
        "query": QUERY,
        "scope": context.indexed_scope("."),
        "match_status": "candidates",
        "results": deepcopy(rows),
        "total_source_chars": sum(len(item["source"]) for item in rows),
        "budget_exhausted": False,
        "timings": {"total_s": 0.0},
    }
    return context.apply_token_budget(value, token_budget)


def fixture(
    *, candidate_source=None, extra_candidates=0, dependencies=False, test_number=7
):
    base = [snippet(number) for number in range(1, 9)]
    # One actual test artifact makes the baseline's test/constraint status honest.
    base[test_number - 1] = snippet(test_number, path="tests/test_main.py")
    complement = snippet(
        9,
        path="src/helper.py",
        symbol="helper",
        source=candidate_source,
    )
    extras = [
        snippet(10 + index, path=f"src/extra_{index}.py")
        for index in range(extra_candidates)
    ]
    rows = [*base, complement, *extras]
    edges = [(complement, base[0])]
    if dependencies:
        dependency = snippet(30, path="src/dependency.py")
        rows.append(dependency)
        edges.insert(0, (base[0], dependency))
    graph = Graph(rows, edges)
    pairs = [
        (item, 100.0 - rank)
        for rank, item in enumerate([*base, complement, *extras], 1)
    ]
    keys = definition_keys(rows)
    planned = candidate.task_evidence_plan(
        pairs,
        needed_evidence=NEEDS,
        query=QUERY,
        max_results=8,
        include_dependencies=dependencies,
        references=graph,
        definition_keys=keys,
    )
    selected, eligible, origins, plan = planned
    packed_rows = []
    scores = dict(pairs)
    for item, score in eligible:
        origin, owner = origins.get(item.id, ("retrieval", None))
        packed_rows.append(row(item, score, origin=origin, related_to=owner))
    return {
        "base": base,
        "complement": complement,
        "extras": extras,
        "pairs": pairs,
        "keys": keys,
        "graph": graph,
        "selected": selected,
        "eligible": eligible,
        "origins": origins,
        "plan": plan,
        "reference": response(packed_rows),
        "ranked_rows": [row(item, scores[item]) for item, _ in pairs],
    }


def pack(data, **overrides):
    settings = {
        "selection_plan": data["plan"],
        "ranked_candidates": deepcopy(data["ranked_rows"]),
        "reference_factory": lambda: deepcopy(data["reference"]),
        "max_results": 8,
        "source_budget": 12000,
        "token_budget": 4096,
    }
    settings.update(overrides)
    return candidate.pack_task_evidence({}, (), **settings)


def sized_source(symbol, size):
    head = f"def {symbol}():\n"
    tail = "    return 1\n"
    remaining = size - len(head) - len(tail)
    assert remaining == 0 or remaining >= 6
    filler = "" if not remaining else f"    #{'x' * (remaining - 6)}\n"
    source = f"{head}{filler}{tail}"
    assert len(source) == size
    return source


def planned_data(items, needed_evidence, *, max_results=8):
    pairs = [(item, 100.0 - rank) for rank, item in enumerate(items, 1)]
    graph = Graph(items)
    keys = definition_keys(items)
    selected, eligible, origins, plan = candidate.task_evidence_plan(
        pairs,
        needed_evidence=needed_evidence,
        query=QUERY,
        max_results=max_results,
        include_dependencies=False,
        references=graph,
        definition_keys=keys,
    )
    del selected
    baseline = [
        row(
            item,
            score,
            origin=origins.get(item.id, ("retrieval", None))[0],
            related_to=origins.get(item.id, ("retrieval", None))[1],
        )
        for item, score in eligible
    ]
    return {
        "plan": plan,
        "reference": response(baseline),
        "ranked_rows": [row(item, score) for item, score in pairs],
    }


def test_public_constants_validation_and_path_roles_are_closed():
    assert candidate.SUPPORTED_NEEDS == tuple(NEEDS)
    assert set(candidate.GAP_REASONS) == {
        "unsupported_source_kind",
        "unsupported_artifact_kind",
        "no_extractable_window",
        "outside_indexed_scope",
        "not_retrieved",
        "selection_excluded",
        "relation_unresolved",
        "result_limit_exhausted",
        "source_budget_exhausted",
        "token_budget_exhausted",
    }
    assert candidate.validate_needed_evidence(NEEDS) == tuple(NEEDS)
    assert candidate.infer_evidence_roles("src/service.py") == ("implementation",)
    assert candidate.infer_evidence_roles("tests/helpers.py") == (
        "tests",
        "behavioral_constraints",
    )
    assert candidate.infer_evidence_roles("src/service.py", caller=True) == (
        "implementation",
        "callers",
    )
    assert candidate.infer_evidence_roles("test_api.py") == (
        "tests",
        "behavioral_constraints",
    )
    for value in (None, "implementation", [], (), ["unknown"], ["tests", "tests"], [1]):
        with pytest.raises(ValueError):
            candidate.validate_needed_evidence(value)


def test_plan_preserves_the_definition_baseline_and_prioritizes_a_verified_caller():
    data = fixture(extra_candidates=1)
    assert [item.id for item, _ in data["eligible"]] == [
        str(number) for number in range(1, 9)
    ]
    assert data["plan"]["anchor_ids"] == [str(number) for number in range(1, 6)]
    assert data["plan"]["protected_ids"] == [str(number) for number in range(1, 6)]
    assert data["plan"]["candidate_ids"][:2] == ["9", "10"]
    assert data["plan"]["candidates"][0]["roles"] == ["implementation", "callers"]
    assert data["plan"]["candidates"][0]["caller_witnesses"][0]["anchor_id"] == "1"
    assert data["eligible"].source_plan == data["plan"]
    copy = data["eligible"].source_plan
    copy["anchor_ids"].clear()
    assert data["eligible"].source_plan["anchor_ids"] == ["1", "2", "3", "4", "5"]


def test_wrapper_returns_selector_shape_and_distinct_definitions_only():
    data = fixture()
    duplicate = snippet(90, path="src/helper.py", symbol="helper", start=91)
    keys = dict(data["keys"])
    keys[duplicate.id] = keys[data["complement"].id]
    pairs = [*data["pairs"], (duplicate, 1.0)]
    result = candidate.task_evidence_context(
        pairs,
        needed_evidence=NEEDS,
        query=QUERY,
        max_results=8,
        include_dependencies=False,
        references=data["graph"],
        definition_keys=keys,
    )
    assert len(result) == 3
    assert result[1].source_plan["candidate_ids"].count("9") == 1
    assert "90" not in result[1].source_plan["candidate_ids"]


def test_dependencies_and_owners_are_protected_before_any_exchange():
    data = fixture(dependencies=True)
    dependency_ids = set(data["plan"]["dependency_owners"])
    assert dependency_ids
    assert dependency_ids <= set(data["plan"]["protected_ids"])
    assert set(data["plan"]["dependency_owners"].values()) <= set(
        data["plan"]["protected_ids"]
    )
    standard, _, receipt = pack(data)
    before = {item["id"]: item for item in data["reference"]["results"]}
    after = {item["id"]: item for item in standard["results"]}
    for identifier in dependency_ids:
        assert after[identifier] == before[identifier]
    assert receipt["accepted"]


def test_one_exchange_preserves_anchors_and_returns_a_counted_public_packet():
    data = fixture()
    standard, packet, receipt = pack(data)
    assert receipt["accepted"] and receipt["candidate_id"] == "9"
    assert receipt["victim_id"] == "8"
    assert [item["id"] for item in standard["results"]] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "9",
    ]
    for before, after in zip(data["reference"]["results"][:5], standard["results"][:5]):
        assert before == after
    assert "task_evidence" not in standard
    assert packet["task_evidence"]["requested"] == NEEDS
    statuses = {
        item["kind"]: item["status"] for item in packet["task_evidence"]["needs"]
    }
    assert statuses == {need: "candidate" for need in NEEDS}
    actual = len(
        context.encoding().encode(
            context.serialize_context(packet), disallowed_special=()
        )
    )
    assert actual == packet["token_usage"]["tokens"] <= 4096
    encoded = json.dumps(packet)
    assert all(
        word not in encoded for word in ("evaluator", "group_id", "expected_rank")
    )


def test_exchange_does_not_evict_the_only_row_for_another_requested_role():
    data = fixture(test_number=8)
    standard, packet, receipt = pack(data)
    assert receipt["accepted"] and receipt["candidate_id"] == "9"
    assert receipt["victim_id"] == "7"
    assert [item["id"] for item in standard["results"]] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "8",
        "9",
    ]
    needs = {item["kind"]: item for item in packet["task_evidence"]["needs"]}
    assert needs["tests"]["status"] == "candidate"
    assert needs["behavioral_constraints"]["status"] == "candidate"
    assert needs["tests"]["result_indexes"] == [6]
    assert needs["behavioral_constraints"]["result_indexes"] == [6]


def test_clipped_test_candidate_keeps_the_existing_full_test_candidate():
    base = [snippet(number) for number in range(1, 8)]
    full_test = snippet(8, path="tests/test_main.py")
    complement = snippet(
        9,
        path="tests/test_extra.py",
        source=sized_source("unit_9", 170),
    )
    data = planned_data([*base, full_test, complement], ["tests"])
    cap = data["reference"]["total_source_chars"]
    standard, packet, receipt = pack(data, source_budget=cap)
    assert receipt["accepted"] and receipt["victim_id"] == "7"
    assert standard["results"][-1]["id"] == "9"
    assert standard["results"][-1]["truncated"]
    retained = next(item for item in standard["results"] if item["id"] == "8")
    assert retained == data["reference"]["results"][-1]
    need = packet["task_evidence"]["needs"][0]
    full_test_index = next(
        index for index, item in enumerate(standard["results"]) if item["id"] == "8"
    )
    assert need == {
        "kind": "tests",
        "status": "candidate",
        "result_indexes": [full_test_index],
    }


def test_actual_packing_rejects_a_replacement_that_downgrades_request_status():
    base = [snippet(number) for number in range(1, 6)]
    full_test = snippet(6, path="tests/test_main.py")
    complement = snippet(
        7,
        path="tests/test_extra.py",
        source=sized_source("unit_7", 170),
    )
    data = planned_data([*base, full_test, complement], ["tests"], max_results=6)
    cap = data["reference"]["total_source_chars"]
    standard, packet, receipt = pack(data, max_results=6, source_budget=cap)
    assert not receipt["accepted"]
    assert receipt["trials"][0]["victim_id"] == "6"
    assert receipt["trials"][0]["reason"] == "requested_role_status_degraded"
    assert standard == data["reference"]
    assert packet["task_evidence"]["needs"][0]["status"] == "candidate"


def test_candidate_trials_larger_safe_victim_when_last_safe_victim_clips_source():
    base = [snippet(number) for number in range(1, 7)]
    base.append(snippet(7, source=sized_source("unit_7", 283)))
    base.append(snippet(8, source=sized_source("unit_8", 27)))
    complement = snippet(
        9,
        path="src/extra.py",
        source=sized_source("unit_9", 170),
    )
    data = planned_data([*base, complement], ["implementation"])
    cap = data["reference"]["total_source_chars"]
    standard, packet, receipt = pack(data, source_budget=cap)
    assert receipt["accepted"] and receipt["candidate_id"] == "9"
    assert receipt["victim_id"] == "7"
    assert receipt["trials"][0]["victim_id"] == "8"
    assert receipt["trials"][0]["source_clipped"]
    assert receipt["trials"][0]["reason"] == "source_budget_exhausted"
    assert receipt["trials"][1]["accepted"]
    delivered = standard["results"][-1]
    assert delivered["id"] == "9" and len(delivered["source"]) == 170
    assert not delivered["truncated"]
    assert packet["task_evidence"]["needs"][0]["status"] == "candidate"


def test_candidate_path_must_be_absent_from_the_actual_control():
    data = fixture(extra_candidates=1)
    data["ranked_rows"][8]["path"] = "src/main.py"
    data["ranked_rows"][8]["source_hash"] = hashlib.sha256(
        data["ranked_rows"][8]["source"].encode()
    ).hexdigest()
    standard, _, receipt = pack(data)
    assert receipt["accepted"] and receipt["candidate_id"] == "10"
    assert standard["results"][-1]["path"] == "src/extra_0.py"


def test_clipped_implementation_is_rejected_and_the_next_full_candidate_is_tried():
    long_source = "def helper():\n    first = 'alpha'\n    second = 'beta'\n    return first + second\n"
    data = fixture(candidate_source=long_source, extra_candidates=1)
    control_chars = data["reference"]["total_source_chars"] + 10
    standard, packet, receipt = pack(data, source_budget=control_chars)
    assert receipt["accepted"] and receipt["candidate_id"] == "10"
    assert receipt["trials"][0]["source_clipped"]
    assert receipt["trials"][0]["reason"] == "source_budget_exhausted"
    delivered = standard["results"][-1]
    assert not delivered["truncated"] and delivered["id"] == "10"
    assert (
        delivered["source_hash"]
        == hashlib.sha256(delivered["source"].encode()).hexdigest()
    )
    needs = {item["kind"]: item for item in packet["task_evidence"]["needs"]}
    assert needs["callers"]["status"] == "unresolved"
    assert needs["callers"]["reason"] == "source_budget_exhausted"
    assert standard["total_source_chars"] <= control_chars


def test_full_packet_budget_tries_another_safe_victim_before_clipping_implementation():
    source = (
        "def helper():\n"
        + "".join(f"    value_{index} = '{'owl ' * 12}'\n" for index in range(8))
        + "    return value_0\n"
    )
    data = fixture(candidate_source=source, extra_candidates=1)
    first_standard, first_packet, _ = pack(data)
    tight = first_packet["token_usage"]["tokens"] - 1
    standard, packet, receipt = pack(data, token_budget=tight)
    assert receipt["accepted"] and receipt["candidate_id"] == "9"
    assert receipt["victim_id"] == "6"
    assert receipt["trials"][0]["token_clipped"]
    assert receipt["trials"][0]["reason"] == "token_budget_exhausted"
    assert receipt["trials"][1]["accepted"]
    assert packet["token_usage"]["tokens"] <= tight
    assert standard["results"][:5] == first_standard["results"][:5]
    assert standard["results"][5] == first_standard["results"][6]
    assert standard["results"][-1]["id"] == "9"
    assert not standard["results"][-1]["truncated"]


def test_a_clipped_test_candidate_is_partial_with_a_procedural_reason():
    base = [snippet(number) for number in range(1, 9)]
    test = snippet(
        9,
        path="tests/test_extra.py",
        source="def test_extra():\n    first = 'alpha'\n    second = 'beta'\n    assert first != second\n",
    )
    pairs = [(item, 100.0 - rank) for rank, item in enumerate([*base, test], 1)]
    graph = Graph([*base, test])
    _, eligible, origins, plan = candidate.task_evidence_plan(
        pairs,
        needed_evidence=["tests"],
        query=QUERY,
        max_results=8,
        include_dependencies=False,
        references=graph,
        definition_keys=definition_keys([*base, test]),
    )
    reference = response(
        [
            row(item, score, origin=origins.get(item.id, ("retrieval", None))[0])
            for item, score in eligible
        ]
    )
    control_chars = reference["total_source_chars"]
    standard, packet, receipt = candidate.pack_task_evidence(
        {},
        (),
        selection_plan=plan,
        ranked_candidates=[row(item, score) for item, score in pairs],
        reference_factory=lambda: deepcopy(reference),
        max_results=8,
        source_budget=control_chars,
        token_budget=4096,
    )
    assert receipt["accepted"] and standard["results"][-1]["truncated"]
    need = packet["task_evidence"]["needs"][0]
    assert need["status"] == "partial"
    assert need["reason"] == "source_budget_exhausted"
    assert need["candidate_id"] == "9"
    assert need["cap"] == control_chars
    assert need["result_indexes"] == [7]


def test_token_clipped_test_gap_names_the_candidate_and_exact_cap():
    base = [snippet(number) for number in range(1, 9)]
    source = "def test_extra():\n" + "".join(
        f"    value_{number} = 'alpha beta gamma delta {number}'\n"
        for number in range(40)
    )
    complement = snippet(9, path="tests/test_extra.py", source=source)
    data = planned_data([*base, complement], ["tests"])
    _, control_packet, _ = pack(data, ranked_candidates=data["ranked_rows"][:8])
    cap = control_packet["token_usage"]["tokens"] + 80
    standard, packet, receipt = pack(data, token_budget=cap)
    assert receipt["accepted"] and receipt["trials"][0]["token_clipped"]
    assert standard["results"][-1]["truncated"]
    need = packet["task_evidence"]["needs"][0]
    assert need["status"] == "partial"
    assert need["reason"] == "token_budget_exhausted"
    assert need["candidate_id"] == "9"
    assert need["cap"] == cap
    assert packet["token_usage"]["tokens"] <= cap


def test_accepted_partial_uses_its_own_cause_after_an_earlier_candidate_fails():
    base = [
        snippet(number, source=sized_source(f"unit_{number}", 40))
        for number in range(1, 6)
    ]
    base.extend(
        snippet(number, source=sized_source(f"unit_{number}", 180))
        for number in range(6, 9)
    )
    caller = snippet(
        9,
        path="tests/test_caller.py",
        symbol="test_caller",
        source=(
            "def test_caller():\n"
            + "".join(f"    value_{number} = 'x'\n" for number in range(20))
            + "    assert value_0\n"
        ),
    )
    plain_test = snippet(
        10,
        path="tests/test_plain.py",
        symbol="test_plain",
        source=(
            "def test_plain():\n"
            + "".join(
                f"    value_{number} = '{number} alpha beta'\n"
                for number in range(5)
            )
            + "    assert value_0\n"
        ),
    )
    items = [*base, caller, plain_test]
    pairs = [(item, 100.0 - rank) for rank, item in enumerate(items, 1)]
    graph = Graph(items, [(caller, base[0])])
    _, eligible, origins, plan = candidate.task_evidence_plan(
        pairs,
        needed_evidence=["callers", "tests", "behavioral_constraints"],
        query=QUERY,
        max_results=8,
        include_dependencies=False,
        references=graph,
        definition_keys=definition_keys(items),
    )
    baseline = [
        row(
            item,
            score,
            origin=origins.get(item.id, ("retrieval", None))[0],
            related_to=origins.get(item.id, ("retrieval", None))[1],
        )
        for item, score in eligible
    ]
    source_cap = sum(len(item["source"]) for item in baseline)
    token_cap = 1006
    reference = response(baseline, token_budget=token_cap)
    standard, packet, receipt = candidate.pack_task_evidence(
        {},
        (),
        selection_plan=plan,
        ranked_candidates=[row(item, score) for item, score in pairs],
        reference_factory=lambda: deepcopy(reference),
        max_results=8,
        source_budget=source_cap,
        token_budget=token_cap,
    )
    assert receipt["accepted"] and receipt["candidate_id"] == "10"
    assert receipt["trials"][:3] == [
        {
            "candidate_id": "9",
            "raw_rank": 9,
            "roles": ["callers", "tests", "behavioral_constraints"],
            "victim_id": str(victim),
            "accepted": False,
            "reason": "source_budget_exhausted",
            "source_clipped": True,
            "token_clipped": False,
        }
        for victim in (8, 7, 6)
    ]
    assert standard["results"][-1]["id"] == "10"
    assert standard["results"][-1]["truncated"]
    needs = {item["kind"]: item for item in packet["task_evidence"]["needs"]}
    assert needs["callers"] == {
        "kind": "callers",
        "status": "unresolved",
        "reason": "source_budget_exhausted",
        "candidate_id": "9",
        "cap": source_cap,
    }
    for kind in ("tests", "behavioral_constraints"):
        assert needs[kind] == {
            "kind": kind,
            "status": "partial",
            "result_indexes": [7],
            "reason": "token_budget_exhausted",
            "candidate_id": "10",
            "cap": token_cap,
        }


def test_pretruncated_reference_partial_names_its_row_and_source_cap():
    data = fixture(test_number=8)
    test_row = data["reference"]["results"][-1]
    first_line = physical_lines(test_row["source"])[0]
    test_row.update(
        source=first_line,
        source_hash=hashlib.sha256(first_line.encode()).hexdigest(),
        end_line=test_row["start_line"],
        truncated=True,
    )
    data["reference"]["budget_exhausted"] = True
    data["reference"]["total_source_chars"] = sum(
        len(item["source"]) for item in data["reference"]["results"]
    )
    data["reference"] = context.apply_token_budget(data["reference"], 4096)
    source_cap = data["reference"]["total_source_chars"]

    _, packet, receipt = pack(
        data,
        ranked_candidates=data["ranked_rows"][:8],
        source_budget=source_cap,
    )
    assert not receipt["accepted"]
    needs = {item["kind"]: item for item in packet["task_evidence"]["needs"]}
    for kind in ("tests", "behavioral_constraints"):
        assert needs[kind] == {
            "kind": kind,
            "status": "partial",
            "result_indexes": [7],
            "reason": "source_budget_exhausted",
            "candidate_id": "8",
            "cap": source_cap,
        }


def test_spare_result_capacity_appends_without_evicting_a_control_row():
    data = fixture()
    data["reference"]["results"] = data["reference"]["results"][:6]
    data["reference"]["total_source_chars"] = sum(
        len(item["source"]) for item in data["reference"]["results"]
    )
    standard, _, receipt = pack(data)
    assert receipt["accepted"] and receipt["victim_id"] is None
    assert [item["id"] for item in standard["results"]] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "9",
    ]


def test_fallback_reserves_metadata_tokens_by_trimming_only_unprotected_lines():
    data = fixture()
    ranked_without_complements = data["ranked_rows"][:8]
    _, roomy_packet, _ = pack(data, ranked_candidates=ranked_without_complements)
    tight = roomy_packet["token_usage"]["tokens"] - 6
    standard, packet, receipt = pack(
        data, ranked_candidates=ranked_without_complements, token_budget=tight
    )
    assert not receipt["accepted"] and receipt["metadata_token_clipped_ids"] == ["8"]
    assert packet["token_usage"]["tokens"] <= tight
    assert standard["results"][:5] == data["reference"]["results"][:5]
    assert standard["results"][-1]["id"] == "8" and standard["results"][-1]["truncated"]
    assert "task_evidence" not in standard


def test_fallback_metadata_trim_preserves_the_only_full_test_row_when_possible():
    data = fixture(test_number=8)
    ranked_without_complements = data["ranked_rows"][:8]
    _, roomy_packet, _ = pack(data, ranked_candidates=ranked_without_complements)
    tight = roomy_packet["token_usage"]["tokens"] - 6
    standard, packet, receipt = pack(
        data,
        ranked_candidates=ranked_without_complements,
        token_budget=tight,
    )
    assert not receipt["accepted"]
    assert receipt["metadata_token_clipped_ids"] == ["7"]
    retained = next(item for item in standard["results"] if item["id"] == "8")
    assert retained == data["reference"]["results"][-1]
    test_index = next(
        index for index, item in enumerate(standard["results"]) if item["id"] == "8"
    )
    needs = {item["kind"]: item for item in packet["task_evidence"]["needs"]}
    assert needs["tests"] == {
        "kind": "tests",
        "status": "candidate",
        "result_indexes": [test_index],
    }
    assert needs["behavioral_constraints"] == {
        "kind": "behavioral_constraints",
        "status": "candidate",
        "result_indexes": [test_index],
    }


def test_no_exchangeable_result_returns_the_exact_control_with_truthful_gaps():
    data = fixture()
    selected, eligible, origins, plan = candidate.task_evidence_plan(
        data["pairs"],
        needed_evidence=NEEDS,
        query=QUERY,
        max_results=5,
        include_dependencies=False,
        references=data["graph"],
        definition_keys=data["keys"],
    )
    reference = response(
        [
            row(item, score, origin=origins.get(item.id, ("retrieval", None))[0])
            for item, score in eligible
        ]
    )
    standard, packet, receipt = candidate.pack_task_evidence(
        {},
        (),
        selection_plan=plan,
        ranked_candidates=deepcopy(data["ranked_rows"]),
        reference_factory=lambda: deepcopy(reference),
        max_results=5,
        source_budget=12000,
        token_budget=4096,
    )
    assert standard == reference and not receipt["accepted"]
    assert receipt["fallback_reason"] == "no_exchangeable_result"
    unresolved = [
        item
        for item in packet["task_evidence"]["needs"]
        if item["status"] == "unresolved"
    ]
    gaps = {item["kind"]: item for item in unresolved}
    assert gaps["callers"] == {
        "kind": "callers",
        "status": "unresolved",
        "reason": "result_limit_exhausted",
        "candidate_id": "9",
        "cap": 5,
    }
    for kind in ("tests", "behavioral_constraints"):
        assert gaps[kind] == {
            "kind": kind,
            "status": "unresolved",
            "reason": "result_limit_exhausted",
            "candidate_id": "7",
            "cap": 5,
        }


def test_ambiguous_static_labels_do_not_claim_caller_evidence():
    data = fixture()
    keys = dict(data["keys"])
    keys["ambiguous"] = (
        data["complement"].path,
        data["complement"].symbol,
        999,
        1000,
    )
    _, planned, _, receipt = candidate.task_evidence_plan(
        data["pairs"],
        needed_evidence=NEEDS,
        query=QUERY,
        max_results=8,
        include_dependencies=False,
        references=data["graph"],
        definition_keys=keys,
    )
    assert "callers" not in receipt["roles"]["9"]
    assert not receipt["caller_witnesses"]
    assert planned.source_plan == receipt


def test_only_eight_invalid_candidate_trials_are_attempted_before_exact_fallback():
    data = fixture(extra_candidates=10)
    for item in data["ranked_rows"][8:]:
        item["source_hash"] = "invalid"
    standard, _, receipt = pack(data)
    assert standard == data["reference"] and not receipt["accepted"]
    assert len(receipt["trials"]) == candidate.MAX_TRIALS
    assert all(
        item["reason"] == "invalid_candidate_source" for item in receipt["trials"]
    )


def test_missing_relation_is_reported_procedurally_not_as_behavior_absence():
    data = fixture()
    plan = deepcopy(data["plan"])
    plan["roles"]["9"] = ["implementation"]
    plan["caller_witnesses"] = {}
    plan["candidates"][0]["roles"] = ["implementation"]
    plan["candidates"][0]["caller_witnesses"] = []
    _, packet, _ = candidate.pack_task_evidence(
        {},
        (),
        selection_plan=plan,
        ranked_candidates=deepcopy(data["ranked_rows"]),
        reference_factory=lambda: deepcopy(data["reference"]),
        max_results=8,
        source_budget=12000,
        token_budget=4096,
    )
    caller = next(
        item for item in packet["task_evidence"]["needs"] if item["kind"] == "callers"
    )
    assert caller == {
        "kind": "callers",
        "status": "unresolved",
        "reason": "relation_unresolved",
    }


def test_cancellation_identity_and_invalid_plan_fail_closed():
    data = fixture()
    marker = RuntimeError("cancelled")

    def cancel():
        raise marker

    with pytest.raises(RuntimeError) as caught:
        pack(data, cancel_check=cancel)
    assert caught.value is marker
    changed = deepcopy(data["plan"])
    changed["schema"] = "wrong"
    with pytest.raises(ValueError, match="planner receipt"):
        pack(data, selection_plan=changed)
