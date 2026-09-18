"""Independent CPU receipt/parity corruption tests; no producer/model imports."""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "independent_qwen_optimization_audit", Path(__file__).parents[1] / "scripts/audit_qwen_optimization.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class Tokenizer:
    def encode(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        return SimpleNamespace(ids=list(text.encode("utf-8")))


CONFIG = {"PREFIX": "P", "SUFFIX": "S", "INSTRUCTION": "I", "MAX_TOKENS": 8192}


def scalar(value):
    return {"yes_logit": value, "no_logit": 0., "logit_difference": value,
            "yes_rank_signal": 1 / (1 + math.exp(-value)) if value >= 0
            else math.exp(value) / (1 + math.exp(value))}


def fixture(size=2):
    pairs = [{"query": "Find behavior", "source": text} for text in ["long source\r\n", "x\n", "middle\n"]]
    built = [audit.built_pair(pair["query"], pair["source"], Tokenizer(), CONFIG) for pair in pairs]
    # Explicit manually authored fixture grouping, independently of producer.
    groups = [[0], [1], [2]] if size == 1 else [[1, 2], [0]]
    counts = audit.zero_counts()
    counts["requests"] = 1
    events, completed, results = [], [], [None] * len(pairs)
    for number, group in enumerate(groups):
        layout = audit.expected_layout(built, group)
        identities = []
        scores = []
        for index in group:
            item = built[index]
            identities.append({"pair_index": index,
                **{key: item[key] for key in ("query_sha256", "source_sha256", "delivered_sha256",
                                             "total_tokens", "truncated", "delivered_lines")},
                "input_ids_sha256": audit.sha(audit.canonical(item["input_ids"]))})
            scores.append(scalar(float(index + 1)))
            results[index] = {key: value for key, value in item.items() if key != "input_ids"} | scores[-1]
        counts["attempted_forwards"] += 1
        counts["attempted_batch_forwards"] += 1
        counts["completed_forwards"] += 1
        counts["completed_batch_forwards"] += 1
        counts["attempted_pairs"] += len(group)
        counts["completed_pairs"] += len(group)
        counts["completed_real_input_tokens"] += layout["real_input_tokens"]
        counts["completed_allocated_input_tokens"] += layout["allocated_input_tokens"]
        counts["completed_padding_tokens"] += layout["padding_tokens"]
        identity = {"request_id": "actual", "warmup": False, "batch_index": number,
                    "batch_forward_number": number + 1, "batch_size_requested": size,
                    "max_padded_tokens": 16384, "pairs": identities, "layout": layout}
        finished = identity | {"event": "completed", "elapsed_s": .1, "scores": scores}
        events.extend([identity | {"event": "started"}, finished])
        completed.append(finished)
    receipt = {"request_id": "actual", "warmup": False, "pairs_sha256": audit.sha(audit.canonical(pairs)),
               "batch_size": size, "batch_size_requested": size, "max_padded_tokens": 16384,
               "batches": completed, "batch_receipts": completed, "results": results, "counts": counts,
               "forward_s": .1 * len(groups), "tokenization_s": .01, "batch_preparation_s": .01,
               "server_scoring_s": .1 * len(groups) + .03}
    return pairs, receipt, events


def verify(pairs, receipt, events, size=2):
    return audit.verify_batch_receipt(receipt, pairs, events, Tokenizer(), CONFIG, request_id="actual",
                                      batch_size=size, previous_counts=audit.zero_counts())


@pytest.mark.parametrize("size", [1, 2])
def test_real_pair_and_native_forward_counts_are_distinct_and_restore_original_scores(size):
    pairs, receipt, events = fixture(size)
    before = deepcopy((pairs, receipt, events))
    counts, built = verify(pairs, receipt, events, size)
    assert counts["completed_pairs"] == 3
    assert counts["completed_batch_forwards"] == (3 if size == 1 else 2)
    assert [item["source_sha256"] for item in built] == [audit.sha(pair["source"].encode()) for pair in pairs]
    assert (pairs, receipt, events) == before


@pytest.mark.parametrize("change", ["query", "source", "extra_input", "digest", "config", "float_config", "warmup",
                                   "started", "missing_event", "forward_number", "float_number", "original_indexes",
                                   "token_hash", "prefix_hash", "causal", "padding", "position", "last",
                                   "width", "real_tokens", "padding_ratio", "singleton", "boolean_layout",
                                   "missing_score", "score_nan", "score_math", "score_order", "result_bytes",
                                   "result_boolean", "count_pairs", "count_forwards", "count_padding",
                                   "count_boolean", "sum", "interval", "negative", "alias", "extra_receipt"])
def test_corrupt_batch_identity_layout_actual_scalar_or_counter_is_rejected(change):
    pairs, receipt, events = fixture()
    if change in ("query", "source"):
        pairs[0][change] += "tamper"
    elif change == "extra_input":
        pairs[0]["gold"] = "forbidden"
    elif change == "digest":
        receipt["pairs_sha256"] = "0" * 64
    elif change == "config":
        receipt["batch_size"] = 4
    elif change == "float_config":
        receipt["batch_size"] = 2.
    elif change == "warmup":
        receipt["warmup"] = True
    elif change == "started":
        events[0]["event"] = "completed"
    elif change == "missing_event":
        events.pop()
    elif change == "forward_number":
        events[0]["batch_forward_number"] = 2
    elif change == "float_number":
        events[0]["batch_index"] = 0.
    elif change in ("original_indexes", "token_hash", "prefix_hash"):
        key = {"original_indexes": "pair_index", "token_hash": "input_ids_sha256", "prefix_hash": "delivered_sha256"}[change]
        events[0]["pairs"][0][key] = 0 if key == "pair_index" else "0" * 64
    elif change in ("causal", "padding", "position", "last", "width", "real_tokens", "padding_ratio", "singleton", "boolean_layout"):
        key, value = {"causal": ("attention_mask", "all"), "padding": ("padding_side", "left"),
                      "position": ("position_offset", 1), "last": ("last_token_positions", [0, 0]),
                      "width": ("padded_sequence_length", 1), "real_tokens": ("real_input_tokens", 999),
                      "padding_ratio": ("padding_ratio", math.nan), "singleton": ("singleton_original_path", True),
                      "boolean_layout": ("position_offset", False)}[change]
        events[0]["layout"][key] = value
    elif change == "missing_score":
        receipt["batches"][0]["scores"].pop()
    elif change == "score_nan":
        receipt["batches"][0]["scores"][0]["yes_logit"] = math.nan
    elif change == "score_math":
        receipt["batches"][0]["scores"][0]["logit_difference"] = 77.
    elif change == "score_order":
        receipt["results"][0], receipt["results"][1] = receipt["results"][1], receipt["results"][0]
    elif change == "result_bytes":
        receipt["results"][0]["source_utf8_bytes"] += 1
    elif change == "result_boolean":
        receipt["results"][0]["truncated"] = 0
    elif change in ("count_pairs", "count_forwards", "count_padding", "count_boolean"):
        key = {"count_pairs": "completed_pairs", "count_forwards": "completed_batch_forwards",
               "count_padding": "completed_padding_tokens", "count_boolean": "requests"}[change]
        receipt["counts"][key] = True if change == "count_boolean" else receipt["counts"][key] + 1
    elif change == "sum":
        receipt["forward_s"] += 1.
    elif change == "interval":
        receipt["server_scoring_s"] = .1
    elif change == "negative":
        receipt["tokenization_s"] = -1.
    elif change == "alias":
        receipt["batch_receipts"] = []
    elif change == "extra_receipt":
        receipt["batches"][0]["cache_reused"] = True
    with pytest.raises((ValueError, KeyError)):
        verify(pairs, receipt, events)


def test_original_literal_unicode_physical_prefix_and_query_cap_are_rebuilt_without_decode():
    config = CONFIG | {"MAX_TOKENS": 80}
    source = "a\r\n literal\n" + "long" * 30 + "\n"
    built = audit.built_pair("q", source, Tokenizer(), config)
    assert built["source_lines"] == 3 and built["delivered_lines"] == 2
    assert built["delivered_sha256"] == audit.sha("a\r\n literal\n".encode())
    assert built["truncated"] is True
    with pytest.raises(ValueError, match="No complete"):
        audit.built_pair("q" * 90, "a\n", Tokenizer(), config)


def test_stable_length_groups_keep_oversized_pairs_and_split_at_slot_budget():
    built = [{"input_ids": [1] * count} for count in (8192, 2, 2, 8192, 8192)]
    assert audit.expected_groups(built, 8, 16384) == [[1, 2], [0, 3], [4]]
    assert audit.expected_groups(built, 1, 16384) == [[0], [1], [2], [3], [4]]
    with pytest.raises(ValueError, match="caps"):
        audit.expected_groups(built, 8, 1000)


def parity_result(value, index):
    return {"source_sha256": str(index), "input_ids_sha256": str(index)} | scalar(value)


def test_near_tie_inversions_and_top_five_changes_are_retained_without_failing():
    reference = [parity_result(value, index) for index, value in enumerate((10., 8., 6., 4., 2., 2.01))]
    sample = deepcopy(reference)
    sample[4].update(scalar(2.02))
    result = audit.parity(reference, sample)
    assert result["qualified"] is True
    assert result["rank_inversions"] and result["top_five_membership_changed"]
    assert result["top_five_outside_near_tie_violations"] == []


@pytest.mark.parametrize("change", ["yes", "difference", "signal", "metadata", "nonfinite"])
def test_failed_parity_is_preserved_or_invalid_input_rejected(change):
    reference = [parity_result(0., 0), parity_result(1., 1)]
    sample = deepcopy(reference)
    if change == "yes":
        sample[0] |= scalar(.063)
    elif change == "difference":
        sample[0] |= scalar(.126)
    elif change == "signal":
        sample[0] |= scalar(.101)
    elif change == "metadata":
        sample[0]["source_sha256"] = "other"
    else:
        sample[0]["yes_logit"] = math.inf
    if change in ("metadata", "nonfinite"):
        with pytest.raises(ValueError):
            audit.parity(reference, sample)
    else:
        result = audit.parity(reference, sample)
        assert result["qualified"] is False and result["scalar_violations"]


def test_inversion_with_strong_reference_margin_fails_even_if_scalar_gate_also_fails():
    reference = [parity_result(.3, 0), parity_result(0., 1)]
    sample = [parity_result(-.1, 0), parity_result(.1, 1)]
    result = audit.parity(reference, sample)
    assert result["qualified"] is False
    assert result["rank_inversions"][0]["outside_near_tie_band"] is True


def case(identifier, repetition, ranks, kind="present"):
    return {"dataset": "repo", "query_id": identifier, "repetition": repetition,
            "kind": kind, "multi_file": len(ranks) > 1, "evidence_ranks": ranks,
            "match_status": "candidates", "elapsed_s": float(repetition)}


def test_quality_uses_first_pass_and_warm_latency_uses_every_actual_observation():
    cases = [case("positive", 1, [1, None]), case("absent", 1, [], "absent"),
             case("positive", 2, [1, 2]), case("absent", 2, [], "absent")]
    metric = audit.quality(cases)
    assert metric["complete"] == 0 and metric["covered_groups"] == 1
    assert metric["multi_file_complete"] == 0
    assert metric["absent_statuses"] == {"candidates": 1}
    assert metric["warm_latency"]["samples"] == 4
    with pytest.raises(ValueError, match="Unique"):
        audit.quality(cases + [cases[0]])


def test_latency_p95_is_nearest_rank_and_not_interpolated():
    assert audit.distribution(list(range(1, 21)))["p95_s"] == 19
    with pytest.raises(ValueError):
        audit.distribution([False])
