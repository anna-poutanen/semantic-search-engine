"""Corruption tests for independent source/score/paired evidence audits; no models."""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "broad_qwen_reranker_audit", Path(__file__).parents[1] / "scripts/audit_qwen_reranker_benchmark.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def raw_fixture():
    source = "first\r\n\u2028literal\nlast\r"
    row = {"id": "stable", "path": "lib/example.js", "symbol": "fn", "start_line": 1,
           "end_line": 3, "source": source, "source_hash": audit.sha(source.encode()),
           "retrieval_score": .5}
    inventory = {"stable": {key: value for key, value in row.items()
                            if key not in ("source", "retrieval_score")}}
    sources = {row["path"]: audit.evidence.physical_lines(source)}
    return [row], inventory, sources


def test_raw_unicode_physical_bytes_inventory_and_cosine_pass_without_mutation():
    rows, inventory, sources = raw_fixture()
    before = deepcopy((rows, inventory, sources))
    assert audit.verify_raw(rows, inventory, sources, score_kind="cosine") == 1
    assert (rows, inventory, sources) == before


@pytest.mark.parametrize("change", ["duplicate", "outside", "symbol", "bounds", "bytes", "hash",
                                  "boolean", "nan", "cosine", "rrf", "kind"])
def test_corrupt_raw_candidate_or_score_rejected(change):
    rows, inventory, sources = raw_fixture()
    kind = "cosine"
    if change == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif change == "outside":
        rows[0]["id"] = "other"
    elif change in ("symbol", "bytes", "hash"):
        rows[0][{"symbol": "symbol", "bytes": "source", "hash": "source_hash"}[change]] = "other"
    elif change == "bounds":
        rows[0]["start_line"] = True
        inventory["stable"]["start_line"] = True
    elif change == "boolean":
        rows[0]["retrieval_score"] = True
    elif change == "nan":
        rows[0]["retrieval_score"] = math.nan
    elif change == "cosine":
        rows[0]["retrieval_score"] = 2.
    elif change == "rrf":
        kind = "rrf_k60"
    else:
        kind = "unknown"
    with pytest.raises(ValueError):
        audit.verify_raw(rows, inventory, sources, score_kind=kind)


def score(delta):
    return {"yes_logit": delta, "no_logit": 0., "logit_difference": delta,
            "yes_rank_signal": 1 / (1 + math.exp(-delta)) if delta >= 0
            else math.exp(delta) / (1 + math.exp(delta))}


def test_sort_by_raw_logits_even_when_softmax_saturates_and_tie_by_original_rank():
    rows = [{"id": "later-id-first"}, {"id": "a"}, {"id": "b"}, {"id": "c"}]
    scores = [score(1000), score(1000), score(2000), score(-1000)]
    assert [row["id"] for row in audit.ordered(rows, scores)] == ["b", "later-id-first", "a", "c"]


@pytest.mark.parametrize("change", ["missing", "difference", "signal", "nan", "boolean"])
def test_fabricated_scalar_or_missing_score_rejected(change):
    scores = [score(1)]
    if change == "missing":
        scores = []
    elif change == "difference":
        scores[0]["logit_difference"] = 2.
    elif change == "signal":
        scores[0]["yes_rank_signal"] = .5
    elif change == "nan":
        scores[0]["yes_logit"] = math.nan
    else:
        scores[0]["no_logit"] = False
    with pytest.raises(ValueError):
        audit.ordered([{"id": "source"}], scores)


def test_source_span_primary_and_symbol_compatibility_secondary_are_separate():
    rows, _, sources = raw_fixture()
    target = {"path": "lib/example.js", "symbol": "nested", "evidence_lines": [2, 2],
              "evidence_sha256": audit.sha("\u2028literal\n".encode())}
    query = {"kind": "present", "targets": [target]}
    gold = audit.groups(query, sources, {target["path"]: "1" * 64})
    assert audit.ranks(rows, gold) == [1]
    assert audit.ranks(rows, gold, symbol_required=True) == [None]
    rows[0]["end_line"] = 1
    assert audit.ranks(rows, gold) == [None]


@pytest.mark.parametrize("change", ["absent", "evidence_hash", "source_hash", "span", "definition"])
def test_invalid_gold_never_receives_credit(change):
    rows, _, sources = raw_fixture()
    target = {"path": "lib/example.js", "symbol": "fn", "evidence_lines": [2, 2]}
    query = {"kind": "present", "targets": [target]}
    if change == "absent":
        query["kind"] = "absent"
    elif change == "evidence_hash":
        target["evidence_sha256"] = "0" * 64
    elif change == "source_hash":
        target["source_file_sha256"] = "0" * 64
    elif change == "span":
        target["evidence_lines"] = [2, 4]
    else:
        target["definition_lines"] = [3, 3]
    with pytest.raises(ValueError):
        audit.groups(query, sources, {rows[0]["path"]: "1" * 64})


def case(identifier, repetition, ranks, *, kind="present"):
    return {"dataset": "repo", "query_id": identifier, "repetition": repetition,
            "kind": kind, "multi_file": len(ranks) > 1, "evidence_ranks": ranks,
            "candidate_evidence_ranks": [1 for _ in ranks], "match_status": "candidates",
            "tokens": 20, "elapsed_s": repetition / 10}


def test_quality_uses_first_pass_unique_queries_and_latency_uses_all_passes():
    cases = [case("a", 1, [1, None]), case("b", 1, [], kind="absent"),
             case("a", 2, [1, 2]), case("b", 2, [], kind="absent")]
    summary = audit.summarize(cases)
    assert summary["unique_queries"] == 2 and summary["present"] == 1
    assert summary["delivered_complete"] == 0 and summary["covered_groups"] == 1
    assert summary["warm_measured_seconds"]["samples"] == 4
    assert summary["absent_statuses"] == {"candidates": 1}


def test_duplicate_unique_query_rejected():
    with pytest.raises(ValueError):
        audit.summarize([case("a", 1, [1]), case("a", 1, [2])])


def test_paired_changes_preserve_gains_and_regressions_without_counting_repeats():
    before = [case("a", 1, [1, None]), case("b", 1, [2]), case("a", 2, [1, None])]
    after = [case("a", 1, [1, 3]), case("b", 1, [None]), case("a", 2, [1, 3])]
    result = audit.changes(before, after)
    assert len(result["complete_gains"]) == len(result["complete_regressions"]) == 1
    after[0]["candidate_evidence_ranks"] = [1, None]
    result = audit.changes(before, after)
    assert len(result["candidate_pool_mismatches"]) == 1 and not result["complete_gains"]


@pytest.mark.parametrize("values", [[], [True], [-1.], [math.nan], [math.inf]])
def test_warm_latency_missing_nonfinite_or_negative_rejected(values):
    with pytest.raises(ValueError):
        audit.distributions(values)


def test_p95_uses_nearest_rank_on_retained_population():
    assert audit.distributions(list(range(1, 21))) == {
        "samples": 20, "median_s": 10.5, "p95_s": 19,
        "min_s": 1, "max_s": 20,
    }


class Tokenizer:
    def encode(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        return SimpleNamespace(ids=list(text.encode()))


def receipt_fixture():
    pairs = [{"query": "behavior", "source": "literal\r\n\u2028source\n"}]
    config = {"PREFIX": "P", "SUFFIX": "S", "INSTRUCTION": "I", "MAX_TOKENS": 8192}
    built = audit.rebuild_pair(pairs[0]["query"], pairs[0]["source"], Tokenizer(), config)
    result = {key: value for key, value in built.items() if key != "input_ids_sha256"} | score(1)
    identity = {key: built[key] for key in ("query_sha256", "source_sha256", "delivered_sha256",
                "total_tokens", "input_ids_sha256", "truncated", "delivered_lines")}
    identity.update(request_id="literal-request", pair_index=0, warmup=False, forward_number=2)
    records = [identity | {"event": "started"},
               identity | {"event": "completed", "elapsed_s": .1, "score": score(1)}]
    import json
    receipt = {"request_id": "literal-request", "warmup": False,
               "pairs_sha256": audit.sha(json.dumps(pairs, sort_keys=True, ensure_ascii=False,
                                                     separators=(",", ":")).encode()),
               "results": [result], "counts": {"requests": 2, "attempted_forwards": 2,
                                                "completed_forwards": 2},
               "forward_s": .1, "tokenization_s": .01, "server_scoring_s": .12}
    previous = {"requests": 1, "attempted_forwards": 1, "completed_forwards": 1}
    return receipt, pairs, records, config, previous


def verify_receipt(receipt, pairs, records, config, previous):
    return audit.verify_scoring_receipt(receipt, pairs, records, Tokenizer(), config,
                                       request_id="literal-request", previous_counts=previous)


def test_scoring_receipt_binds_source_only_pairs_forward_graph_and_no_cached_score():
    receipt, pairs, records, config, previous = receipt_fixture()
    assert verify_receipt(receipt, pairs, records, config, previous) == receipt["counts"]


@pytest.mark.parametrize("change", ["request", "digest", "missing", "source", "template", "score",
                                  "counter", "cached", "number", "warmup", "time", "sum"])
def test_corrupt_resident_request_forward_warmup_or_interval_rejected(change):
    receipt, pairs, records, config, previous = receipt_fixture()
    if change == "request":
        receipt["request_id"] = "other"
    elif change == "digest":
        receipt["pairs_sha256"] = "0" * 64
    elif change == "missing":
        records.pop()
    elif change == "source":
        receipt["results"][0]["source_sha256"] = "0" * 64
    elif change == "template":
        config["PREFIX"] = "another"
    elif change == "score":
        records[1]["score"] = score(2)
    elif change == "counter":
        receipt["counts"]["requests"] = True
    elif change == "cached":
        receipt["counts"]["attempted_forwards"] = 1
    elif change == "number":
        records[0]["forward_number"] = records[1]["forward_number"] = 1
    elif change == "warmup":
        receipt["warmup"] = True
    elif change == "time":
        receipt["server_scoring_s"] = .05
    else:
        receipt["forward_s"] = .2
    with pytest.raises(ValueError):
        verify_receipt(receipt, pairs, records, config, previous)


def test_independent_prefix_clipping_preserves_complete_physical_lines():
    config = {"PREFIX": "P", "SUFFIX": "S", "INSTRUCTION": "I", "MAX_TOKENS": 8192}
    first = audit.rebuild_pair("query", "first\r\n", Tokenizer(), config)
    config["MAX_TOKENS"] = first["total_tokens"]
    clipped = audit.rebuild_pair("query", "first\r\nsecond\u2028line\n", Tokenizer(), config)
    assert clipped["truncated"] is True and clipped["delivered_lines"] == 1
    assert clipped["delivered_sha256"] == audit.sha(b"first\r\n")


@pytest.mark.parametrize("change", ["result_bool", "result_count", "started_count", "completed_bool"])
def test_boolean_and_integer_receipt_metadata_cannot_be_substituted(change):
    receipt, pairs, records, config, previous = receipt_fixture()
    if change == "result_bool":
        receipt["results"][0]["truncated"] = 0
    elif change == "result_count":
        receipt["results"][0]["source_lines"] = float(receipt["results"][0]["source_lines"])
    elif change == "started_count":
        records[0]["pair_index"] = False
    else:
        records[1]["warmup"] = 0
    with pytest.raises(ValueError):
        verify_receipt(receipt, pairs, records, config, previous)


def vector_fixture():
    import numpy as np
    values = np.ones(1024, dtype=np.float32)
    return {"dtype": "float32", "shape": [1024], "values": values.tolist(),
            "sha256": audit.sha(values.tobytes())}


def test_exact_observed_vector_bytes_dimension_and_finite_nonzero_values():
    record = vector_fixture()
    assert audit.verify_vector(record).shape == (1024,)


@pytest.mark.parametrize("change", ["dtype", "shape", "hash", "nan", "boolean", "zero", "query"])
def test_fabricated_observed_query_vector_rejected(change):
    record = vector_fixture()
    if change == "dtype":
        record["dtype"] = "float64"
    elif change == "shape":
        record["shape"] = [512]
    elif change == "hash":
        record["sha256"] = "0" * 64
    elif change in ("nan", "boolean"):
        record["values"][0] = math.nan if change == "nan" else True
    elif change == "zero":
        record["values"] = [0.] * 1024
    else:
        record["input"] = "other query"
    with pytest.raises(ValueError):
        audit.verify_vector(record, "literal query")


def test_candidate_identity_mismatch_is_retained_and_not_counted_as_reranking_gain():
    before, after = case("a", 1, [None]), case("a", 1, [1])
    before["pool_identity_sha256"], after["pool_identity_sha256"] = "a", "b"
    result = audit.changes([before], [after])
    assert result["candidate_pool_mismatches"] and not result["complete_gains"]
