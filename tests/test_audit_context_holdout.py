"""Invented CPU proofs for the independent fresh-result auditor; no models."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("independent_context_holdout_audit", SCRIPTS / "audit_context_holdout.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_independent_schedule_has_exact_all_case_balance():
    ids = [f"q{i}" for i in range(15)]
    rows = audit.expected_schedule(ids)
    assert len(rows) == len({r["request_id"] for r in rows}) == 138
    assert len([r for r in rows if r["phase"] == "measured"]) == 135
    for query in ids:
        orders = [[r["config_id"] for r in rows if r["phase"] == "measured" and r["pass"] == p and r["query_id"] == query]
                  for p in range(3)]
        for position in range(3):
            assert {order[position] for order in orders} == set(audit.CONFIGS)


def test_transport_repair_keeps_actual_frozen_ids_and_uses_bounded_unique_requests():
    import re
    manifest = json.loads((SCRIPTS.parent / "benchmarks/werkzeug_context_queries.json").read_text())
    ids = [item["id"] for item in manifest["queries"]]
    rows = audit.expected_schedule(ids)
    assert len({row["request_id"] for row in rows}) == 138
    assert {row["query_id"] for row in rows} == set(ids)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", row["request_id"]) for row in rows)
    assert [row["query_id"] for row in rows[:3]] == [ids[0]] * 3


@pytest.mark.parametrize("ids", [["q"] * 15, [f"q{i}" for i in range(14)]])
def test_independent_schedule_rejects_inventory_damage(ids):
    with pytest.raises(ValueError):
        audit.expected_schedule(ids)


def source_fixture():
    first, second = "def first():\n    return second()\n", "def second():\n    return 42\n"
    def target(path, text):
        return {"path": path, "evidence_lines": [1, 2], "evidence_sha256": audit.sha(text.encode())}
    query = {"kind": "present", "target_groups": [[target("a.py", first)], [target("b.py", second)]]}
    results = [{"path": path, "source": text, "start_line": 1, "end_line": 2} for path, text in (("a.py", first), ("b.py", second))]
    return query, results


def test_independent_full_source_groups_and_absent_scope():
    query, results = source_fixture()
    assert audit.source_credit(query, results) == {"kind": "present", "group_ranks": [1, 2], "complete": True, "multi_file": True}
    assert audit.source_credit({"kind": "absent"}, results)["complete"] is None


@pytest.mark.parametrize("damage", ["missing-group", "partial-body", "wrong-bytes", "wrong-path"])
def test_independent_source_credit_rejects_semantic_evidence_shortcuts(damage):
    query, results = source_fixture()
    if damage == "missing-group":
        results.pop()
    elif damage == "partial-body":
        results[0].update(source="def first():\n", end_line=1)
    elif damage == "wrong-bytes":
        results[0]["source"] = results[0]["source"].replace("second", "unrelated")
    else:
        results[0]["path"] = "elsewhere.py"
    assert not audit.source_credit(query, results)["complete"]


def synthetic_quality(count=4):
    rows = []
    for repeat in range(3):
        for config, complete_count in (("baseline8", 3), ("primary12", count), ("diagnostic20", 12)):
            for number in range(15):
                present = number < 12
                complete = number < complete_count if present else None
                rows.append({"phase": "measured", "pass": repeat, "config_id": config, "query_id": f"q{number}",
                             "evidence": {"kind": "present" if present else "absent", "complete": complete,
                                          "group_ranks": [1 if complete else None] if present else [], "multi_file": number < 4}})
    return rows


def test_independent_primary_quality_gate_is_not_diagnostic_selection():
    assert audit.quality(synthetic_quality())["primary12_passed"]
    checked = audit.quality(synthetic_quality(3))
    assert not checked["primary12_passed"] and not checked["diagnostic20_can_rescue_primary12"] and not checked["default_changed"]


def test_independent_primary_gate_preserves_each_baseline_case_and_every_pass():
    rows = synthetic_quality(5)
    victim = next(r for r in rows if r["pass"] == 2 and r["config_id"] == "primary12" and r["query_id"] == "q0")
    victim["evidence"].update(complete=False, group_ranks=[None])
    result = audit.quality(rows)
    assert not result["primary12_passed"] and not result["passes"][2]["comparisons"]["baseline_complete_retained"]


@pytest.mark.parametrize("damage", ["missing", "duplicate", "wrong-query"])
def test_independent_quality_inventory_fails_closed(damage):
    rows = synthetic_quality()
    if damage == "missing":
        rows.pop()
    elif damage == "duplicate":
        rows[-1] = deepcopy(rows[-2])
    else:
        rows[-1]["query_id"] = "invented-extra"
    with pytest.raises(ValueError):
        audit.quality(rows)


def query_vector():
    values = np.ones(1024, dtype=np.float32)
    return {"dtype": "float32", "shape": [1024], "values": values.tolist(), "raw_sha256": audit.sha(values.tobytes())}


@pytest.mark.parametrize("damage", [None, "dtype", "shape", "float-shape", "hash", "value", "bool", "zero"])
def test_query_vector_requires_original_typed_finite_hash_bound_bytes(damage):
    row = query_vector()
    if damage is None:
        assert np.array_equal(audit.vector_capture(row), np.ones(1024, dtype=np.float32))
        return
    if damage == "dtype":
        row["dtype"] = "float64"
    elif damage == "shape":
        row["shape"] = [True]
    elif damage == "float-shape":
        row["shape"] = [1024.0]
    elif damage == "hash":
        row["raw_sha256"] = "0" * 64
    elif damage == "value":
        row["values"][0] = float("nan")
    elif damage == "bool":
        row["values"][0] = True
    else:
        row["values"] = [0.] * 1024
        row["raw_sha256"] = audit.sha(np.zeros(1024, dtype=np.float32).tobytes())
    with pytest.raises(ValueError):
        audit.vector_capture(row)


def test_independent_hybrid_has_literal_fts_and_rrf_tie_id_order(tmp_path):
    database = tmp_path / "invented.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE snippets(id TEXT PRIMARY KEY)")
        connection.execute("CREATE VIRTUAL TABLE snippets_fts USING fts5(id UNINDEXED,terms)")
        connection.executemany("INSERT INTO snippets VALUES (?)", [(x,) for x in ("a", "b", "c")])
        connection.executemany("INSERT INTO snippets_fts VALUES (?,?)", [("a", "alpha"), ("b", "beta"), ("c", "alpha alpha")])
    result = audit.hybrid_pool(database, ["a", "b", "c"], np.array([[1., 0.], [0., 1.], [1., 1.]], dtype=np.float32),
                               "alpha", np.array([1., 0.], dtype=np.float32))
    assert [r["id"] for r in result] == ["a", "c", "b"]
    assert result[0]["retrieval_score"] == result[1]["retrieval_score"] == 1 / 61 + 1 / 62
    assert result[2]["retrieval_score"] == 1 / 63


class CharacterTokenizer:
    """Invented tokenizer for CPU clipping faults; never actual model evidence."""
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return SimpleNamespace(ids=[ord(char) for char in text])


def test_independent_pair_clips_only_complete_physical_lines():
    tokenizer = CharacterTokenizer()
    full = "def first():\r\n    return '\u2028literal'\r\n" + "#" * 300 + "\n"
    query = "find behavior"
    first_two = "".join(audit.common.physical_lines(full)[:2])
    bound = audit.literal_pair(tokenizer, query, first_two)["total_tokens"]
    built = audit.literal_pair(tokenizer, query, full, maximum=bound)
    assert built["delivered_lines"] == 2 and built["source_lines"] == 3 and built["truncated"]
    assert built["delivered_sha256"] == audit.sha(first_two.encode())
    assert built["input_ids_sha256"] == audit.sha(json.dumps(built["input_ids"], separators=(",", ":")).encode())
    with pytest.raises(ValueError):
        audit.literal_pair(tokenizer, query, full, maximum=1)


@pytest.mark.parametrize("difference", [-1000., -1., 0., 1., 1000.])
def test_independent_binary_probability_is_stable_for_both_signs(difference):
    import math
    probability = 1 / (1 + math.exp(-difference)) if difference >= 0 else math.exp(difference) / (1 + math.exp(difference))
    result = {"yes_logit": difference, "no_logit": 0., "logit_difference": difference, "yes_rank_signal": probability}
    assert audit.scalar({"result": result}) == result


@pytest.mark.parametrize("damage", ["nan", "integer", "difference", "probability", "overflow"])
def test_independent_native_scalars_reject_invalid_or_changed_arithmetic(damage):
    result = {"yes_logit": 1., "no_logit": 0., "logit_difference": 1., "yes_rank_signal": .7310585786300049}
    if damage == "nan":
        result["yes_logit"] = float("nan")
    elif damage == "integer":
        result["yes_logit"] = 1
    elif damage == "difference":
        result["logit_difference"] = 0.
    elif damage == "probability":
        result["yes_rank_signal"] = .1
    else:
        result.update(yes_logit=1e308, no_logit=-1e308)
    with pytest.raises(ValueError):
        audit.scalar({"result": result})


class TinyTokenizer:
    """Invented three-token input encoder for inexpensive ledger mutation tests."""
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return SimpleNamespace(ids=[len(text)])


def ledger_fixture():
    tokenizer = TinyTokenizer()
    built = audit.literal_pair(tokenizer, "invented query", "def invented():\n    return 7\n")
    identity = {key: built[key] for key in ("source_sha256", "query_sha256", "delivered_sha256", "total_tokens", "input_ids_sha256")}
    score = {"yes_logit": 1., "no_logit": 0., "logit_difference": 1., "yes_rank_signal": .7310585786300049}
    events, records = [], []
    warm_built = audit.literal_pair(tokenizer, audit.WARMUP_QUERY, audit.WARMUP_SOURCE)
    warm = {**{key: warm_built[key] for key in identity}, "request_id": "generic-warmup", "warmup": True, "batch_size": 1, "forward_number": 1}
    events.extend([{**warm, "event": "started"}, {**warm, "event": "completed", "score": score}])
    for call in range(138):
        pairs = []
        for position in range(24):
            number = call * 24 + position + 2
            request_id = f"invented-{call}-{position}"
            actual = {**identity, "request_id": request_id, "warmup": False, "batch_size": 1, "forward_number": number}
            events.extend([{**actual, "event": "started"}, {**actual, "event": "completed", "score": score}])
            receipt = {"request_id": request_id, "batch_size": 1, "warmup": False,
                       "counts": dict.fromkeys(audit.COUNTERS, number),
                       "result": {k: v for k, v in built.items() if k not in ("input_ids", "input_ids_sha256")} | score}
            pairs.append({"query": "invented query", "source": "def invented():\n    return 7\n", "request_id": request_id,
                          "status": "completed", "receipt": receipt, "input_ids": built["input_ids"][:],
                          "input_ids_sha256": built["input_ids_sha256"]})
        records.append({"literal_pairs": pairs})
    return events, records, tokenizer


def test_independent_actual_native_ledger_has_complete_all3313_inventory():
    events, records, tokenizer = ledger_fixture()
    assert audit.ledger_pairs(events, records, tokenizer) == {
        "forwards": 3313, "http_pairs": 3312, "measured_pairs": 3240, "http_warmup_pairs": 72, "generic_warmup": 1}


@pytest.mark.parametrize("damage", ["missing", "unknown-event", "nonalternating", "bool-number", "bool-batch",
                                    "actual-token", "input-hash", "source", "scalar", "receipt-id", "incomplete",
                                    "pair-count", "bool-pair-count", "warmup-token", "warmup-scalar"])
def test_independent_native_ledger_rejects_input_counter_scalar_and_receipt_damage(damage):
    events, records, tokenizer = ledger_fixture()
    if damage == "missing":
        events.pop()
    elif damage == "unknown-event":
        events.append({"event": "unknown"})
    elif damage == "nonalternating":
        events[2], events[3] = events[3], events[2]
    elif damage == "bool-number":
        events[0]["forward_number"] = True
    elif damage == "bool-batch":
        events[2]["batch_size"] = events[3]["batch_size"] = True
    elif damage == "actual-token":
        records[0]["literal_pairs"][0]["input_ids"][0] += 1
    elif damage == "input-hash":
        events[3]["input_ids_sha256"] = "0" * 64
    elif damage == "source":
        records[0]["literal_pairs"][0]["source"] = "def another():\n    return 8\n"
    elif damage == "scalar":
        records[0]["literal_pairs"][0]["receipt"]["result"]["yes_rank_signal"] = .1
    elif damage == "receipt-id":
        records[0]["literal_pairs"][0]["receipt"]["request_id"] = "another"
    elif damage == "pair-count":
        records[0]["literal_pairs"][0]["receipt"]["counts"]["requests"] = 7
    elif damage == "bool-pair-count":
        records[0]["literal_pairs"][0]["receipt"]["counts"]["requests"] = True
    elif damage == "warmup-token":
        events[0]["input_ids_sha256"] = events[1]["input_ids_sha256"] = "0" * 64
    elif damage == "warmup-scalar":
        events[1]["score"] = {**events[1]["score"], "yes_rank_signal": .1}
    else:
        records[0]["literal_pairs"][0]["status"] = "failed"
    with pytest.raises(ValueError):
        audit.ledger_pairs(events, records, tokenizer)


def delivered_fixture():
    import tiktoken
    text = "def implementation():\n    return 42\n"
    identifier = "src/werkzeug/invented.py:implementation:1-2"
    row = {"id": identifier, "path": "src/werkzeug/invented.py", "symbol": "implementation",
           "start_line": 1, "end_line": 2, "window_end_line": 2, "source": text,
           "source_hash": audit.sha(text.encode()), "content_hash": audit.sha(text.encode()),
           "truncated": False, "origin": "retrieval", "related_to": None}
    response = {"query": "invented behavior", "retrieval": "hybrid", "rerank": True, "rerank_provider": "qwen",
                "reranker_batch_size": 1, "retrieval_candidate_windows": 50, "candidate_windows": 24,
                "results": [row], "total_source_chars": len(text),
                "scope": {"source_root": "src/werkzeug", "membership": "saved implementations; Git-tracked files only in Git projects",
                          "source_exclusions": [], "exclusions_omitted": 0},
                "token_usage": {"encoding": "cl100k_base", "tokens": 0, "budget": 4096, "truncated": False}}
    for _ in range(12):
        compact_row = {k: row[k] for k in ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated", "origin", "related_to")}
        compact = {"query": response["query"], "match_status": "candidates", "results": [compact_row],
                   "budget_exhausted": False, "token_usage": response["token_usage"], "scope": response["scope"]}
        payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        count = len(tiktoken.get_encoding("cl100k_base").encode(payload, disallowed_special=()))
        if count == response["token_usage"]["tokens"]:
            break
        response["token_usage"]["tokens"] = count
    return response, {row["path"]: text.encode()}, {identifier: {**row, "source_hash": row["content_hash"]}}


def test_independent_compact_payload_and_literal_delivered_source_are_exact():
    response, sources, windows = delivered_fixture()
    proof = audit.delivered_proof(response, response["query"], sources, windows, 12)
    assert proof["payload_tokens"] == response["token_usage"]["tokens"]
    assert proof["source_characters"] == response["total_source_chars"]


@pytest.mark.parametrize("damage", ["body", "hash", "unknown-window", "window-end", "truncated", "chars", "tokens", "dependency"])
def test_independent_delivered_payload_rejects_changed_source_scope_and_accounting(damage):
    response, sources, windows = delivered_fixture()
    row = response["results"][0]
    if damage == "body":
        row["source"] = row["source"].replace("42", "99")
    elif damage == "hash":
        row["source_hash"] = "0" * 64
    elif damage == "unknown-window":
        row["id"] = "unknown"
    elif damage == "window-end":
        row["window_end_line"] = 3
    elif damage == "truncated":
        row["truncated"] = True
    elif damage == "chars":
        response["total_source_chars"] += 1
    elif damage == "tokens":
        response["token_usage"]["tokens"] += 1
    else:
        row.update(origin="dependency", related_to="unknown")
    with pytest.raises(ValueError):
        audit.delivered_proof(response, response["query"], sources, windows, 12)


def test_retained_sorted_json_restores_exact_original_compact_key_order():
    response, sources, windows = delivered_fixture()
    original = audit.delivered_proof(response, response["query"], sources, windows, 12)
    retained = json.loads(json.dumps(response, sort_keys=True))
    assert list(retained["token_usage"]) != list(response["token_usage"])
    assert list(retained["scope"]) != list(response["scope"])
    assert audit.delivered_proof(retained, response["query"], sources, windows, 12) == original


@pytest.mark.parametrize("field", ["token_usage", "scope"])
def test_unexpected_compact_metadata_cannot_be_silently_discarded(field):
    response, sources, windows = delivered_fixture()
    response[field]["unreviewed"] = True
    with pytest.raises(ValueError):
        audit.delivered_proof(response, response["query"], sources, windows, 12)


@pytest.mark.parametrize("key", ["logit_difference", "yes_rank_signal"])
def test_all_scalar_fields_reject_boolean_numeric_aliases(key):
    result = {"yes_logit": -1000., "no_logit": 0., "logit_difference": -1000., "yes_rank_signal": 0.}
    if key == "logit_difference":
        result.update(yes_logit=0., no_logit=0., logit_difference=False, yes_rank_signal=.5)
    else:
        result[key] = False
    with pytest.raises(ValueError):
        audit.scalar({"result": result})


@pytest.mark.parametrize("damage", [None, "count", "bool-count", "request", "hash", "flag", "scalar"])
def test_readiness_warmup_matches_known_literal_and_actual_first_native_forward(damage):
    events, records, tokenizer = ledger_fixture()
    built = audit.literal_pair(tokenizer, audit.WARMUP_QUERY, audit.WARMUP_SOURCE)
    warmup = {"request_id": "generic-warmup", "batch_size": 1, "warmup": True,
              "counts": dict.fromkeys(audit.COUNTERS, 1),
              "result": {k: v for k, v in built.items() if k not in ("input_ids", "input_ids_sha256")} | events[1]["score"]}
    if damage is None:
        assert audit.ledger_pairs(events, records, tokenizer, warmup)["generic_warmup"] == 1
        return
    if damage == "count":
        warmup["counts"]["requests"] = 2
    elif damage == "bool-count":
        warmup["counts"]["requests"] = True
    elif damage == "request":
        warmup["request_id"] = "unrelated"
    elif damage == "hash":
        warmup["result"]["source_sha256"] = "0" * 64
    elif damage == "flag":
        warmup["warmup"] = False
    else:
        warmup["result"]["yes_rank_signal"] = .1
    with pytest.raises(ValueError):
        audit.ledger_pairs(events, records, tokenizer, warmup)


def ranked_fixture():
    pool, pairs = [], []
    for i in range(24):
        identifier = f"reverse-id-{24-i:02d}"
        pool.append({"id": identifier, "path": "src/werkzeug/invented.py", "symbol": f"function{i}", "retrieval_score": .01 + i / 1000})
        pairs.append({"snippet_id": identifier, "receipt": {"result": {"yes_logit": 1., "no_logit": 0.,
                                                                      "logit_difference": 1., "yes_rank_signal": .7310585786300049}}})
    results = [{**row, "origin": "retrieval", "related_to": None, "qwen_score": 1.} for row in pool[:3]]
    return {"full_pool": pool, "literal_pairs": pairs, "response": {"results": results}}


def test_native_scalar_ties_preserve_original_prefix_index_before_identifier():
    record = ranked_fixture()
    audit.anchor_ranking(record)
    record["response"]["results"] = sorted(record["response"]["results"], key=lambda r: r["id"])
    with pytest.raises(ValueError):
        audit.anchor_ranking(record)


@pytest.mark.parametrize("damage", ["outside-prefix", "scalar", "retrieval-scalar", "dependency-anchor", "missing-pair"])
def test_independent_anchor_ranking_rejects_wrong_scalar_lineage_or_order(damage):
    record = ranked_fixture()
    row = record["response"]["results"][0]
    if damage == "outside-prefix":
        row["id"] = "unscored-tail"
    elif damage == "scalar":
        row["qwen_score"] = .1
    elif damage == "retrieval-scalar":
        row["retrieval_score"] = .1
    elif damage == "dependency-anchor":
        row.update(origin="dependency", related_to=record["full_pool"][10]["id"])
    else:
        record["literal_pairs"].pop()
    with pytest.raises(ValueError):
        audit.anchor_ranking(record)
