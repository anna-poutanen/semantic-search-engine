"""Small CPU-only fixture gates; these never certify native inference or an editor."""
import copy
import contextvars
import importlib.util
import json
import os
from pathlib import Path

import pytest

from semantic_search.qwen_reranker_core import PairBuilder, binary_score
from semantic_search.qwen_worker import WARMUP

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vscode_qwen_cpu", ROOT / "scripts/smoke_vscode_qwen.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class CPUTokenizer:
    def encode(self, text, add_special_tokens=False):
        if text in ("yes", "no"):
            return [90001 if text == "yes" else 90002]
        return [ord(char) for char in text]


def fixture():
    builder = PairBuilder(CPUTokenizer())
    warmup = builder.build(WARMUP["query"], WARMUP["source"])
    identity = {"request_id": "generic-warmup", "batch_size": 1, "forward_number": 1, "warmup": True,
                "input_ids_sha256": smoke.sha(json.dumps(warmup["input_ids"], separators=(",", ":")).encode()),
                **{key: warmup[key] for key in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens")}}
    observations, rows = [], [identity | {"event": event} for event in ("started", "completed")]
    number = 1
    for dirty in (False, True):
        query = "Find the literal changed value" if dirty else "Find the saved value"
        result_rows, receipts = [], {}
        for index in range(5):
            number += 1
            source = f"def function_{index}():\n    return {index + int(dirty)}\n"
            identifier, request = f"window-{index}", f"request-{number}"
            built = builder.build(query, source)
            result = {key: value for key, value in built.items() if key not in ("input_ids", "source")} | binary_score(float(index), 0.)
            receipt = {"request_id": request, "batch_size": 1, "result": result,
                       "counts": {key: number for key in smoke.COUNTERS}}
            receipts[identifier] = receipt
            result_rows.append({"id": identifier, "source": source, "truncated": False,
                                "qwen_score": result["logit_difference"], "h2o_score": None})
            identity = {"request_id": request, "batch_size": 1, "forward_number": number, "warmup": False,
                        "input_ids_sha256": smoke.sha(json.dumps(built["input_ids"], separators=(",", ":")).encode()),
                        **{key: result[key] for key in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens")}}
            rows.extend([identity | {"event": "started"}, identity | {"event": "completed", "score": binary_score(float(index), 0.)}])
        observations.append({"query": query, "settings": {"overlays": [{"path": "src/delta.ts"}]} if dirty else {},
                             "response": {"rerank": True, "rerank_provider": "qwen", "candidate_windows": 5,
                                          "retrieval_candidate_windows": 5, "reranker_batch_size": 1,
                                          "results": result_rows, "reranker_receipts": receipts}})
    return builder, observations, rows, {key: 11 for key in smoke.COUNTERS}


def test_complete_cpu_literal_inventory_reconciles_but_performs_no_inference():
    builder, observations, rows, counts = fixture()
    result = smoke.verify_native(rows, observations, counts, builder=builder)
    assert result == {"started": 11, "completed": 11, "measured": 10, "warmup": 1,
                      "literal_pairs_reconstructed": 10, "tokenizer_input_hashes_exact": True}


@pytest.mark.parametrize("kind", ["count", "duplicate-event", "warmup", "tokenizer", "source", "provider", "scalar", "returned-score"])
def test_native_gate_rejects_mismatched_actual_evidence(kind):
    builder, observations, rows, counts = fixture()
    if kind == "count":
        counts["completed_forwards"] = 10
    elif kind == "duplicate-event":
        rows.append(copy.deepcopy(rows[-1]))
    elif kind == "warmup":
        rows[3]["warmup"] = True
    elif kind == "tokenizer":
        rows[2]["input_ids_sha256"] = "0" * 64
    elif kind == "source":
        observations[0]["response"]["results"][0]["source"] += "# wrong literal\n"
    elif kind == "provider":
        observations[1]["response"]["rerank_provider"] = "h2o"
    elif kind == "scalar":
        observations[0]["response"]["reranker_receipts"]["window-0"]["result"]["yes_rank_signal"] = .2
    else:
        observations[0]["response"]["results"][0]["qwen_score"] = 9.
    with pytest.raises(AssertionError):
        smoke.verify_native(rows, observations, counts, builder=builder)


@pytest.mark.parametrize("counter", smoke.COUNTERS)
@pytest.mark.parametrize("value", [999, True, 2.0])
def test_native_gate_requires_every_http_cumulative_counter_exact_int_and_number(counter, value):
    builder, observations, rows, counts = fixture()
    observations[0]["response"]["reranker_receipts"]["window-0"]["counts"][counter] = value
    with pytest.raises(AssertionError):
        smoke.verify_native(rows, observations, counts, builder=builder)


def test_coding_gate_rejects_partial_inventory_and_live_owned_driver(tmp_path):
    audit = tmp_path / "audit.json"
    value = {"checks_passed": True, "inventory_complete": False, "final_completion_receipt": False,
             "recorded_attempts": 17, "missing_attempts": ["trial18"]}
    audit.write_text(json.dumps(value))
    with pytest.raises(AssertionError, match="Full18"):
        smoke.coding_gate(audit)
    value.update(inventory_complete=True, final_completion_receipt=True, recorded_attempts=18, missing_attempts=[])
    audit.write_text(json.dumps(value))
    driver = tmp_path / "driver.json"
    driver.write_text(json.dumps({"driver": {"pid": os.getpid(), "process_group": os.getpgrp()}}))
    with pytest.raises(AssertionError, match="must be gone"):
        smoke.coding_gate(audit, driver_file=driver)


def test_partial_coding_gate_refuses_freeze_before_any_fixture_or_subprocess(tmp_path, monkeypatch):
    audit = tmp_path / "partial.json"
    audit.write_text(json.dumps({"checks_passed": True, "recorded_attempts": 17}))
    monkeypatch.setattr(smoke.subprocess, "check_output", lambda *args, **kwargs: pytest.fail("No subprocess allowed before gates"))
    output, work = tmp_path / "output", tmp_path / "work"
    with pytest.raises(AssertionError, match="Full18"):
        smoke.freeze(output, work, Path("unused"), Path("unused"), {name: audit for name in ("coding", "install", "parity", "mcp")})
    assert not output.exists() and not work.exists()


def test_editor_fallback_rejects_wrong_nonce_before_any_signal(tmp_path, monkeypatch):
    record = tmp_path / "ownership.json"
    record.write_text(json.dumps({"nonce": "other", "pid": 100, "process_group": 100, "detached": True}))
    monkeypatch.setattr(smoke.os, "killpg", lambda *args: pytest.fail("Cannot signal an unowned editor"))
    with pytest.raises(AssertionError, match="ownership"):
        smoke.stop_editor(record, "this-run", tmp_path / "repo")


def embedding_schedule_fixture(cancel_entries, cancel_status):
    attempts = [{"request_id": f"request_{i}", "query": f"query {i}", "settings": {}, "status": "completed"}
                for i in range(7)]
    attempts[-1]["settings"] = {"overlays": [{"path": "src/delta.ts"}]}
    cancel = {"request_id": "cancelled", "query": "query" + smoke.CANCEL_SUFFIX,
              "settings": {}, "status": cancel_status}
    attempts.append(cancel)
    entries = [{"kind": "documents", "request_id": None, "status": "completed", "document_texts": 5}]
    entries += [{"kind": "query", "request_id": item["request_id"], "status": "completed",
                 "query_sha256": smoke.sha(item["query"].encode())} for item in attempts[:7]]
    entries.append({"kind": "documents", "request_id": attempts[-2]["request_id"], "status": "completed", "document_texts": 1})
    if cancel_entries:
        entries.append({"kind": "query", "request_id": cancel["request_id"], "status": "completed",
                        "query_sha256": smoke.sha(cancel["query"].encode())})
    for number, entry in enumerate(entries, 1):
        entry["number"] = number
    observations = [{"request_id": item["request_id"]} for item in attempts if item["status"] == "completed"]
    return attempts, entries, observations, {"query_calls": 7 + cancel_entries, "document_calls": 2, "document_texts": 6}


@pytest.mark.parametrize("cancel_entries,cancel_status", [(0, "cancelled"), (1, "cancelled"), (1, "completed")])
def test_cancel_stage_reconciles_actual_entries_and_responses_without_forcing_race(cancel_entries, cancel_status):
    data = embedding_schedule_fixture(cancel_entries, cancel_status)
    result = smoke.verify_embedding_schedule(*data)
    assert result["observed_query_entries"] == 7 + cancel_entries
    assert result["successful_responses"] == 7 + int(cancel_status == "completed")
    assert result["cancelled_request_query_entries"] == cancel_entries


@pytest.mark.parametrize("change", ["missing", "duplicate", "wrongquery", "extraentry", "unfinished", "failed",
                                  "fabricatedsuccess", "wrongcount", "extradoc"])
def test_embedding_schedule_does_not_invent_or_drop_actual_stage_evidence(change):
    attempts, entries, observations, counts = embedding_schedule_fixture(0, "cancelled")
    if change == "missing":
        attempts.pop()
    elif change == "duplicate":
        attempts[-1]["request_id"] = attempts[0]["request_id"]
    elif change == "wrongquery":
        entries[1]["query_sha256"] = "0" * 64
    elif change == "extraentry":
        entries.append(dict(entries[1], number=len(entries) + 1))
    elif change == "unfinished":
        entries[1]["status"] = "entered"
    elif change == "failed":
        attempts[-1]["status"] = "failed"
    elif change == "fabricatedsuccess":
        attempts[-1]["status"] = "completed"
        observations.append({"request_id": attempts[-1]["request_id"]})
    elif change == "wrongcount":
        counts["query_calls"] = 8
    else:
        entries.append(dict(entries[0], number=len(entries) + 1))
    with pytest.raises(AssertionError):
        smoke.verify_embedding_schedule(attempts, entries, observations, counts)


def test_request_embedding_observer_binds_entry_exit_and_failed_api_to_current_context():
    class Inner:
        metadata = {"fixture": True}
        def embed_query(self, query):
            if query == "fail":
                raise ValueError("synthetic failure")
            return [1]
        def embed_documents(self, texts, *, cancel_check):
            return [[1] for _ in texts]
        def close(self):
            pass
    context = contextvars.ContextVar("fixture", default=None)
    entries = []
    observer = smoke.RequestObservedEmbedder(Inner(), context, entries)
    observer.embed_documents(["initial"])
    token = context.set("real-request")
    try:
        assert observer.embed_query("literal") == [1]
        with pytest.raises(ValueError, match="synthetic"):
            observer.embed_query("fail")
    finally:
        context.reset(token)
    assert [entry["request_id"] for entry in entries] == [None, "real-request", "real-request"]
    assert [entry["status"] for entry in entries] == ["completed", "completed", "failed"]
    assert entries[1]["query_sha256"] == smoke.sha(b"literal")
    assert entries[-1]["error_type"] == "ValueError" and all("finished_at_utc" in entry for entry in entries)


def test_late_publication_signal_invalidates_prior_passing_json_and_digest(tmp_path):
    journal = tmp_path / "signals.jsonl"
    journal.write_bytes(b"")
    certificate = {"checks_passed": True,
        "publication_rule": "Empty actual signals.jsonl with exact artifact hash required",
        "artifact_sha256": {"signals.jsonl": smoke.sha(b"")}}
    assert smoke.publication_qualified(tmp_path, certificate)
    journal.write_text('{"signal":"SIGTERM","received_at_utc":"actual-cpu-fixture"}\n')
    assert certificate["checks_passed"] is True  # The earlier serialized flag cannot establish validity.
    assert not smoke.publication_qualified(tmp_path, certificate)


@pytest.mark.parametrize("change", ["missing", "wronghash", "rule", "failed"])
def test_publication_qualification_requires_complete_empty_bound_actual_journal(tmp_path, change):
    journal = tmp_path / "signals.jsonl"
    journal.write_bytes(b"")
    certificate = {"checks_passed": True,
        "publication_rule": "Empty actual signals.jsonl with exact artifact hash required",
        "artifact_sha256": {"signals.jsonl": smoke.sha(b"")}}
    if change == "missing":
        journal.unlink()
    elif change == "wronghash":
        certificate["artifact_sha256"]["signals.jsonl"] = "0" * 64
    elif change == "rule":
        del certificate["publication_rule"]
    else:
        certificate["checks_passed"] = False
    assert not smoke.publication_qualified(tmp_path, certificate)
