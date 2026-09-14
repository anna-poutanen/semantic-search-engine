"""Narrow score/forward corruption checks; fake tokenizer, no model imports."""
import copy
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("reranker_case_audit", Path(__file__).parents[1] / "scripts/audit_reranker_case.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class Tokenizer:
    def encode(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        return SimpleNamespace(ids=list(text.encode()))


def fixture():
    config = {"PREFIX": "prefix", "SUFFIX": "suffix", "INSTRUCTION": "fixed", "MAX_TOKENS": 8192}
    pairs = [{"query": "source-neutral behavior", "source": "\ufeffcode\r\n\u2028literal\n"} for _ in range(50)]
    records, results = [], []
    for number, pair in enumerate(pairs, 1):
        rebuilt = audit.rebuild_pair(pair["query"], pair["source"], Tokenizer(), config)
        score = {"yes_logit": number / 10, "no_logit": -1., "logit_difference": number / 10 + 1,
                 "yes_rank_signal": 1 / (1 + math.exp(-(number / 10 + 1)))}
        results.append({key: value for key, value in rebuilt.items() if key != "input_ids_sha256"} | score)
        started = {key: rebuilt[key] for key in ("input_ids_sha256", "total_tokens", "source_sha256", "delivered_sha256", "truncated")}
        started.update(pair_number=number, status="started", started_utc="2026-10-13T13:00:00+00:00")
        records.extend([started, {**started, "status": "completed", "forward_seconds": .01, "score": score}])
    return records, pairs, results, config


def test_all_started_completed_receipts_literal_unicode_and_scores_match_without_mutation():
    records, pairs, results, config = fixture()
    before = copy.deepcopy((records, pairs, results))
    audit.verify_forwards(records, pairs, results, Tokenizer(), config)
    assert before == (records, pairs, results)
    assert results[0]["source_lines"] == 2


@pytest.mark.parametrize("change", ["missing", "order", "ids", "tokens", "source", "logit", "probability", "time"])
def test_corrupted_forward_or_score_cannot_pass(change):
    records, pairs, results, config = fixture()
    if change == "missing":
        records.pop()
    elif change == "order":
        records[2]["pair_number"] = 1
    elif change == "ids":
        records[0]["input_ids_sha256"] = records[1]["input_ids_sha256"] = "0" * 64
    elif change == "tokens":
        results[0]["total_tokens"] = 8192
    elif change == "source":
        results[0]["source"] = "wrong\n"
        results[0]["delivered_sha256"] = audit.sha(b"wrong\n")
    elif change == "logit":
        results[0]["logit_difference"] = 0
    elif change == "probability":
        results[0]["yes_rank_signal"] = .5
    else:
        records[1]["forward_seconds"] = -1
    with pytest.raises(ValueError):
        audit.verify_forwards(records, pairs, results, Tokenizer(), config)


def test_raw_logit_order_is_tied_by_original_dense_position():
    candidates = [{"id": f"id-{number:02}"} for number in range(50)]
    results = [{"logit_difference": 1.0} for _ in range(50)]
    results[-1]["logit_difference"] = 2.0
    order = audit.ranking(candidates, results)
    assert order[0]["id"] == "id-49" and order[0]["original_dense_rank"] == 50
    assert [row["id"] for row in order[1:]] == [candidate["id"] for candidate in candidates[:-1]]


def test_prefix_clipping_keeps_crlf_and_literal_unicode_separators():
    tokenizer = Tokenizer()
    config = {"PREFIX": "P", "SUFFIX": "S", "INSTRUCTION": "I", "MAX_TOKENS": 8192}
    first = audit.rebuild_pair("query", "first\r\n", tokenizer, config)
    config["MAX_TOKENS"] = first["total_tokens"]
    clipped = audit.rebuild_pair("query", "first\r\nsecond\u2028literal\n", tokenizer, config)
    assert clipped["source"] == "first\r\n" and clipped["delivered_lines"] == 1 and clipped["truncated"]
