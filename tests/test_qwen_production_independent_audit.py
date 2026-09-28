"""Independent audit rejects attractive producer summaries when raw evidence drifts."""
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/audit_production_qwen.py"
SPEC = importlib.util.spec_from_file_location("independent_production_audit", PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def fixture():
    pair = {"query": "Return the second value", "source": "def f():\r\n    return 'x\u2028y'\n"}
    result = {"source_lines": 2, "delivered_lines": 2,
              "source_utf8_bytes": len(pair["source"].encode()),
              "delivered_utf8_bytes": len(pair["source"].encode()),
              "source_sha256": audit.sha(pair["source"].encode()),
              "delivered_sha256": audit.sha(pair["source"].encode()),
              "query_sha256": audit.sha(pair["query"].encode()),
              "total_tokens": 128, "truncated": False, "yes_logit": 3.0, "no_logit": 1.0,
              "logit_difference": 2.0, "yes_rank_signal": 1 / (1 + math.exp(-2.0))}
    case = {"group": "fixture", "index": 0, "pair": pair, "expected": result,
            "expected_input_ids_sha256": "f" * 64}
    warmup = {"request_id": "generic-warmup", "batch_size": 1, "warmup": True,
              "result": deepcopy(result), "counts": dict.fromkeys(audit.COUNT_KEYS, 1),
              "forward_s": .2, "tokenization_s": .001, "server_scoring_s": .201}
    response = deepcopy(warmup)
    response.update(request_id="production-parity-0", warmup=False,
                    counts=dict.fromkeys(audit.COUNT_KEYS, 2))
    observed = [{"number": 0, "group": "fixture", "index": 0, "mismatches": {}, "response": response}]
    ledger = []
    for i, item in enumerate((warmup, response)):
        base = {key: result[key] for key in audit.HASH_KEYS | {"total_tokens"}}
        base.update(request_id=item["request_id"], warmup=item["warmup"], batch_size=1,
                    forward_number=i + 1, input_ids_sha256="f" * 64)
        ledger += [base | {"event": "started"}, base | {"event": "completed", "forward_s": .2,
                            "score": {key: result[key] for key in audit.SCORE_KEYS}}]
    return [case], observed, ledger, warmup


def test_independent_literal_and_sequential_inventory_passes():
    cases, observed, ledger, warmup = fixture()
    audit.validate_result(cases[0]["pair"], cases[0]["expected"])
    assert audit.validate_observations(cases, observed, ledger, warmup) == 1


@pytest.mark.parametrize("change", [
    lambda rows, events: events.append(deepcopy(events[-1])),
    lambda rows, events: events.pop(2),
    lambda rows, events: events[2].update(event="completed"),
    lambda rows, events: events[2].update(input_ids_sha256="a" * 64),
    lambda rows, events: events[3].update(forward_number=1),
    lambda rows, events: events[3]["score"].update(yes_logit=3.125),
    lambda rows, events: rows[0]["response"]["counts"].update(requests=3),
    lambda rows, events: rows[0]["response"]["result"].update(total_tokens=129),
    lambda rows, events: rows[0]["response"]["result"].update(truncated=0),
    lambda rows, events: rows[0].update(number=True),
    lambda rows, events: events[0].update(request_id="production-parity-0"),
])
def test_missing_duplicate_extra_or_mutated_native_evidence_fails(change):
    cases, observed, ledger, warmup = fixture()
    change(observed, ledger)
    with pytest.raises(ValueError):
        audit.validate_observations(cases, observed, ledger, warmup)


def test_whole_physical_prefix_clipping_not_invented_or_partial_line():
    cases, _, _, _ = fixture()
    pair, result = cases[0]["pair"], cases[0]["expected"]
    delivered = "def f():\r\n"
    result.update(delivered_lines=1, delivered_utf8_bytes=len(delivered.encode()),
                  delivered_sha256=audit.sha(delivered.encode()), truncated=True)
    audit.validate_result(pair, result)
    result["delivered_sha256"] = audit.sha("def f():\r\n    ret".encode())
    with pytest.raises(ValueError, match="whole-line"):
        audit.validate_result(pair, result)


@pytest.mark.parametrize("field,value", [("yes_logit", 3), ("yes_rank_signal", float("nan")),
                                         ("total_tokens", True), ("source_lines", 3),
                                         ("truncated", 0), ("logit_difference", 2.125)])
def test_types_finite_scores_and_literal_counts_not_trusted(field, value):
    cases, _, _, _ = fixture()
    cases[0]["expected"][field] = value
    with pytest.raises(ValueError):
        audit.validate_result(cases[0]["pair"], cases[0]["expected"])


def test_exact_typed_equality_distinguishes_python_bool_and_int():
    assert not audit.typed_equal({"truncated": 0}, {"truncated": False})
    assert not audit.typed_equal({"yes_logit": 3}, {"yes_logit": 3.0})
    assert not audit.typed_equal({"a": [1, 2]}, {"a": [2, 1]})


def test_duplicate_or_nonfinite_json_cannot_be_silently_repaired():
    for text in ('{"passed":false,"passed":true}', '{"score":NaN}'):
        with pytest.raises(ValueError):
            audit.decode(text)
    assert audit.decode(json.dumps({"passed": False})) == {"passed": False}


def test_reference_pool_missing_duplicated_or_reindexed_group_rejected():
    cases, _, _, _ = fixture()
    case = cases[0]
    plan = {"parity_sets": [{"id": "fixture", "pairs": [case["pair"]]}]}
    result = case["expected"]
    prepared = {key: result[key] for key in ("query_sha256", "source_sha256", "delivered_sha256",
                "total_tokens", "truncated", "delivered_lines")}
    prepared.update(pair_index=0, input_ids_sha256=case["expected_input_ids_sha256"])
    recorded = [{"identity": {"batch_size": 1, "parity_set": "fixture"},
                 "reranker": {"results": [result], "batch_receipts": [{"pairs": [prepared]}]}}]
    assert audit.reconstruct_cases(plan, recorded) == cases
    with pytest.raises(ValueError, match="Duplicate"):
        audit.reconstruct_cases(plan, recorded * 2)
    recorded[0]["reranker"]["batch_receipts"][0]["pairs"][0]["pair_index"] = 1
    with pytest.raises(ValueError, match="index inventory"):
        audit.reconstruct_cases(plan, recorded)
