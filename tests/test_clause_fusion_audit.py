"""Independent reconstruction corruption checks, with source-neutral CPU fixtures."""
import copy
from fractions import Fraction
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("clause_audit", ROOT / "scripts/audit_clause_fusion.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class Counter:
    def encode(self, text, **kwargs):
        return range(len(text))


def counters(inputs=()):
    return {"attempted_document_calls": 1, "completed_document_calls": 1, "document_texts": 2,
            "attempted_query_calls": len(inputs), "completed_query_calls": len(inputs),
            "observed_query_inputs": list(inputs)}


def fixture(condition="clause_rrf"):
    query = "find the first behavior here before locate the second behavior there"
    texts = {"src/a.ts": '\ufeffexport function a() {\r\n  return "😀\u2028literal";\r\n}\r\n',
             "src/b.ts": 'export function b() {\n  return "second";\n}\n'}
    sources = {path: audit.evidence.physical_lines(text) for path, text in texts.items()}
    inventory = {path: {"id": path, "path": path, "symbol": path[-4], "start_line": 1,
                       "end_line": 3, "source_hash": audit.sha(text.encode())} for path, text in texts.items()}
    inputs = [query] if condition == "full_query_dense" else audit.split_inputs(query)
    rankings = []
    for number in range(len(inputs)):
        paths = list(texts) if number == 0 else list(texts)[::-1]
        rankings.append([{**inventory[path], "source": texts[path], "retrieval_score": .7 - offset / 10}
                         for offset, path in enumerate(paths)])
    merged = rankings[0] if condition == "full_query_dense" else audit.reconstruct_fusion(rankings)
    delivered = [{**window, "window_end_line": 3, "content_hash": window["source_hash"],
                  "retrieval_score": window["retrieval_score"] if condition == "full_query_dense" else window["fusion_priority"],
                  "h2o_score": None, "truncated": False, "origin": "retrieval", "related_to": None}
                 for window in merged]
    kind = "cosine" if condition == "full_query_dense" else "equal_weight_rrf_priority_k60"
    response = {"query": query, "scope": {"source_root": "src"}, "retrieval": "dense", "rerank": False,
                "source_budget": 12000, "candidate_windows": 2, "results": delivered,
                "match_status": "candidates", "total_source_chars": sum(len(text) for text in texts.values()),
                "budget_exhausted": False, "token_usage": {"encoding": "cl100k_base", "tokens": 0,
                "budget": 4096, "truncated": False}, "match_evidence": {"top_cosine": .7,
                "threshold": None, "ranking_score_kind": kind}}
    for _ in range(6):
        response["token_usage"]["tokens"] = len(audit.evidence.compact_text(response))
    vector = np.asarray([1.0] + [0.0] * 1023, dtype=np.float32)
    row = {"status": "completed", "query": query, "query_inputs": inputs,
           "counts_before": counters(), "counts_after": counters(inputs),
           "query_vectors": [{"input": text, "shape": [1024], "dtype": "float32",
                              "sha256": audit.sha(vector.tobytes()), "l2_norm": 1.0,
                              "values": vector.tolist()} for text in inputs],
           "raw_ranked_source_lists": [{"input": text, "score_kind": "cosine", "windows": windows}
                                       for text, windows in zip(inputs, rankings)],
           "merged_candidates": merged, "score_kind": kind, "source_and_shared_caps_verified": True,
           "response": response, "query_embeddings_s": 1.0, "candidate_retrieval_s": .2,
           "fusion_s": .1, "packing_s": .1, "elapsed_inprocess_s": 1.5}
    return row, query, inventory, sources


def test_text_rule_preserves_original_caps_clauses_and_avoids_arbitrary_and():
    query = "  Find the first behavior and WHERE locate the second behavior WHILE find the first behavior THEN another valid four word clause before fifth ignored four word clause  "
    values = audit.split_inputs(query)
    assert values == [query, "Find the first behavior", "locate the second behavior", "another valid four word clause"]
    assert audit.split_inputs("find one behavior and other behavior here") == ["find one behavior and other behavior here"]
    assert audit.split_inputs("find behavior beforehand inside the source") == ["find behavior beforehand inside the source"]
    assert audit.split_inputs("one two three before four five six") == ["one two three before four five six"]


def test_exact_rrf_ties_full_rank_then_id_and_not_cosine():
    a, b = {"id": "a", "retrieval_score": .01}, {"id": "b", "retrieval_score": .99}
    result = audit.reconstruct_fusion([[b, a], [a, b]])
    assert [row["id"] for row in result] == ["b", "a"]
    expected = Fraction(1, 61) + Fraction(1, 62)
    assert all(row["fusion_exact"] == str(expected) and row["fusion_priority"] == float(expected) for row in result)
    result = audit.reconstruct_fusion([[], [b, a], [a, b]])
    assert [row["id"] for row in result] == ["a", "b"]
    with pytest.raises(ValueError, match="Unique depth"):
        audit.reconstruct_fusion([[a, a]])


@pytest.mark.parametrize("condition", audit.CONDITIONS)
def test_exact_source_budget_vector_counter_and_fusion_audit_is_read_only(condition):
    row, query, inventory, sources = fixture(condition)
    before = copy.deepcopy(row)
    merged, count, calls = audit.verify_attempt(row, query, condition, inventory, sources, Counter())
    assert merged == row["merged_candidates"] and count == 2
    assert calls == len(row["query_inputs"]) and row == before


@pytest.mark.parametrize("change", ["split", "input_log", "doc_call", "attempt_count", "rank_order", "duplicate_rank",
                                  "source", "fusion", "priority", "raw_cosine", "origin", "line", "tokens", "chars",
                                  "vectors", "vector_hash", "vector_values", "time"])
def test_corrupted_receipt_is_rejected_before_gold_scoring(change):
    row, query, inventory, sources = fixture()
    if change == "split":
        row["query_inputs"][1] = "changed text"
    elif change == "input_log":
        row["counts_after"]["observed_query_inputs"][1] = "different actual model input"
    elif change == "doc_call":
        row["counts_after"]["completed_document_calls"] += 1
    elif change == "attempt_count":
        row["counts_after"]["attempted_query_calls"] += 1
    elif change == "rank_order":
        row["raw_ranked_source_lists"][0]["windows"].reverse()
    elif change == "duplicate_rank":
        row["raw_ranked_source_lists"][0]["windows"][1] = copy.deepcopy(row["raw_ranked_source_lists"][0]["windows"][0])
    elif change == "source":
        window = row["raw_ranked_source_lists"][0]["windows"][0]
        window["source"] = "wrong\n"
        window["source_hash"] = audit.sha(window["source"].encode())
    elif change == "fusion":
        row["merged_candidates"].reverse()
    elif change == "priority":
        row["merged_candidates"][0]["fusion_exact"] = "1/60"
    elif change == "raw_cosine":
        row["response"]["match_evidence"]["top_cosine"] = row["merged_candidates"][0]["fusion_priority"]
    elif change == "origin":
        row["response"]["results"][0].update(origin="dependency", related_to="missing-anchor")
    elif change == "line":
        row["response"]["results"][0]["start_line"] = True
    elif change == "tokens":
        row["response"]["token_usage"]["tokens"] = 0
    elif change == "chars":
        row["response"]["total_source_chars"] += 1
    elif change == "vectors":
        row["query_vectors"][0]["l2_norm"] = .1
    elif change == "vector_hash":
        row["query_vectors"][0]["sha256"] = "a" * 64
    elif change == "vector_values":
        row["query_vectors"][0]["values"][1] = .1
    else:
        row["packing_s"] = -1
    with pytest.raises(ValueError):
        audit.verify_attempt(row, query, "clause_rrf", inventory, sources, Counter())


def test_all_scheduled_conditions_must_remain_unique_in_order():
    schedule = [{"dataset": "toy", "query_id": f"q{number}", "condition": condition}
                for number in range(30) for condition in audit.CONDITIONS]
    rows = [{"identity": identity} for identity in copy.deepcopy(schedule)]
    audit.verify_matrix(rows, schedule)
    rows[-1] = copy.deepcopy(rows[-2])
    with pytest.raises(ValueError, match="sixty unique"):
        audit.verify_matrix(rows, schedule)


def test_gain_regression_and_absent_denominators_are_paired_without_selection():
    cases = []
    for identifier, kind, before, after in (("gain", "present", [1, None], [1, 2]),
                                           ("loss", "present", [1, 2], [None, 2]),
                                           ("decoy", "absent", [], [])):
        for condition, ranks in zip(audit.CONDITIONS, (before, after)):
            cases.append({"dataset": "toy", "query_id": identifier, "condition": condition, "kind": kind,
                          "multi_file": kind == "present", "ranks": ranks, "candidate_ranks": [1, 2] if kind == "present" else [],
                          "result_count": 2, "match_status": "candidates", "tokens": 100,
                          "elapsed_inprocess_s": 1.0, "query_api_calls": 1})
    changes = audit.paired_changes(cases)
    assert len(changes["complete_gains"]) == len(changes["complete_regressions"]) == 1
    assert len(changes["and_gains"]) == len(changes["and_regressions"]) == 1
    metrics = audit.summarize_cases([case for case in cases if case["condition"] == "full_query_dense"])
    assert metrics["present"] == 2 and metrics["absent"] == metrics["absent_with_decoys"] == 1
    assert metrics["delivered_complete"] == 1 and metrics["and_queries"] == 2 and metrics["and_complete"] == 1


def test_changed_observed_prefix_is_rejected_even_with_correct_scalar_counts():
    before = counters(["old query"])
    after = counters(["replaced history", "new query"])
    with pytest.raises(ValueError, match="Immutable actual"):
        audit.verify_counters(before, after, ["new query"])


@pytest.mark.parametrize("change", [None, "plan_hash", "weights", "origin", "network"])
def test_actual_run_weight_and_module_provenance_is_bound_to_frozen_plan(change):
    plan = {"source_sha256": {"src/semantic_search/backend.py": "a" * 64},
            "semantic_module_origins": {"semantic_search.backend": {
                "project_relative_path": "src/semantic_search/backend.py", "sha256": "a" * 64}},
            "packages": {"tiktoken": "0.14.0"}, "checkpoint": {"model.safetensors": {
                "expected_sha256_from_provenance": "b" * 64, "bytes": 100}},
            "offline_tokenizer": {"sha256": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
                                  "tiktoken": "0.14.0", "network": False, "cache_writes": False}}
    receipt = {"frozen_plan_sha256": "c" * 64, "source_sha256": copy.deepcopy(plan["source_sha256"]),
               "semantic_module_origins": copy.deepcopy(plan["semantic_module_origins"]),
               "offline_tokenizer": copy.deepcopy(plan["offline_tokenizer"]),
               "weights": {"model.safetensors": {"sha256": "b" * 64, "bytes": 100, "matches_pinned_provenance": True}}}
    if change == "plan_hash":
        receipt["frozen_plan_sha256"] = "d" * 64
    elif change == "weights":
        receipt["weights"]["model.safetensors"]["sha256"] = "d" * 64
    elif change == "origin":
        receipt["semantic_module_origins"]["semantic_search.backend"]["project_relative_path"] = "other/checkout.py"
    elif change == "network":
        receipt["offline_tokenizer"]["network"] = plan["offline_tokenizer"]["network"] = True
    if change is None:
        audit.verify_run_receipt(plan, receipt, "c" * 64)
    else:
        with pytest.raises(ValueError):
            audit.verify_run_receipt(plan, receipt, "c" * 64)


def test_partial_failures_and_pending_conditions_are_never_imputed_as_quality_misses(tmp_path):
    schedule = [{"dataset": "toy", "query_id": "first", "condition": name} for name in audit.CONDITIONS]
    (tmp_path / "frozen-plan.json").write_text(json.dumps({"schedule": schedule}))
    (tmp_path / "attempts.jsonl").write_text(json.dumps({"identity": schedule[0], "status": "failed"}) + "\n")
    result = audit.incomplete_audit(tmp_path, ValueError("incomplete ledger"))
    assert result["checks_passed"] is False and result["planned_conditions"] == 2
    assert result["retained_status_counts"] == {"failed": 1}
    assert result["failed_identities"] == [schedule[0]] and result["pending_identities"] == [schedule[1]]
    assert "metrics" not in result
