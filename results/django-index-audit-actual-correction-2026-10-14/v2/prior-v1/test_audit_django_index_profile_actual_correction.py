"""CPU mutation tests for the independent prospective large-index auditor."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import gzip
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import numpy as np
import pytest


PATH = Path(__file__).resolve().parents[1] / "scripts/audit_django_index_profile_actual_correction.py"
SPEC = importlib.util.spec_from_file_location("django_index_actual_correction_audit", PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_json_duplicate_and_nonfinite_rejected():
    for raw in ('{"count":1,"count":2}', '{"latency":NaN}', '{"latency":Infinity}'):
        with pytest.raises(ValueError):
            audit.decode(raw)


def test_typed_counts_reject_boolean_alias():
    assert not audit.typed_equal(True, 1)
    assert not audit.typed_equal({"n": True}, {"n": 1})
    assert not audit.typed_equal([1.0], [1])


def test_saved_span_uses_encoding_cookie_and_physical_lines():
    raw = b"# coding: latin-1\r\ndef caf\xe9():\r\n    return '\xe9'\r\n"
    text = "def café():\r\n    return 'é'\r\n"
    row = {"id": "django/a.py:café:2-3", "path": "django/a.py", "symbol": "café",
           "start_line": 2, "end_line": 3, "content_sha256": audit.sha(text.encode()),
           "document_sha256": audit.sha(audit.document("django/a.py", "café", text).encode())}
    assert audit.saved_windows([row], {"django/a.py": raw})[0]["text"] == text
    corrupt = dict(row, end_line=4)
    with pytest.raises(ValueError, match="past saved file"):
        audit.saved_windows([corrupt], {"django/a.py": raw})


@pytest.mark.parametrize("field", ["id", "content_sha256", "document_sha256"])
def test_source_or_document_receipt_drift_rejected(field):
    text = "def f():\n    return 1\n"
    row = {"id": "django/a.py:f:1-2", "path": "django/a.py", "symbol": "f",
           "start_line": 1, "end_line": 2, "content_sha256": audit.sha(text.encode()),
           "document_sha256": audit.sha(audit.document("django/a.py", "f", text).encode())}
    row[field] = "incorrect"
    with pytest.raises(ValueError):
        audit.saved_windows([row], {"django/a.py": text.encode()})


def test_independent_normalization_preserves_float32_and_handles_extreme_values():
    values = np.array([[1e300, -1e300], [1e-300, 0]], dtype=np.float64)
    result = audit.independent_float32(values)
    assert result.dtype == np.dtype("<f4")
    np.testing.assert_allclose(result[0], [2 ** -0.5, -2 ** -0.5], atol=1e-7)
    np.testing.assert_array_equal(result[1], [1, 0])


@pytest.mark.parametrize("values", [np.array([[0.0, 0.0]]), np.array([[np.nan, 1.0]]),
                                     np.array([[np.inf, 1.0]]), np.array([[1j, 1j]]),
                                     np.array([[1, 2]], dtype=int), np.array([1.0, 2.0])])
def test_unusable_native_vectors_rejected(values):
    with pytest.raises(ValueError):
        audit.independent_float32(values)


def test_exact_cosine_uses_id_ties_and_not_original_input_order():
    values = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    result = audit.independent_dense(["z", "a", "other"], values, np.array([1.0, 0.0]))
    assert [row["id"] for row in result] == ["a", "z", "other"]
    assert [row["score"] for row in result] == [1.0, 1.0, 0.0]


def test_near_tie_rank_disagreement_is_failure_even_inside_error_tolerance():
    expected = [{"id": "a", "score": 0.9}, {"id": "b", "score": 0.9 - 1e-7}]
    with pytest.raises(ValueError, match="near ties"):
        audit.verify_dense(expected, list(reversed(expected)))


def test_score_bound_and_typed_scalar_rejected():
    expected = [{"id": "a", "score": 1.0}]
    assert audit.verify_dense(expected, [{"id": "a", "score": 1.0 - 2e-6}]) < 3e-6
    with pytest.raises(ValueError, match="bound"):
        audit.verify_dense(expected, [{"id": "a", "score": 1.0 - 4e-6}])
    with pytest.raises(ValueError, match="scalar"):
        audit.verify_dense(expected, [{"id": "a", "score": 1}])


def test_latency_distribution_retains_count_and_linear_percentile():
    values = [float(i) for i in range(80)]
    result = audit.timing_summary(values)
    assert result == {"count": 80, "median_s": 39.5, "p95_s": 75.05, "min_s": 0.0, "max_s": 79.0}
    for bad in ([], [True], [float("nan")], [-1.0]):
        with pytest.raises(ValueError):
            audit.timing_summary(bad)


def test_clock_checks_elapsed_and_sleep_discontinuity():
    first = datetime(2026, 10, 14, tzinfo=timezone.utc)
    start = {"wall_utc": first.isoformat(), "monotonic_s": 10.0}
    end = {"wall_utc": (first + timedelta(seconds=4)).isoformat(), "monotonic_s": 14.0}
    audit.verify_clock(start, end, 4.0)
    with pytest.raises(ValueError, match="monotonic"):
        audit.verify_clock(start, end, 3.0)
    interrupted = dict(end, wall_utc=(first + timedelta(seconds=100)).isoformat())
    with pytest.raises(ValueError, match="discontinuity"):
        audit.verify_clock(start, interrupted, 4.0)


def fixture_mutation():
    path = "django/core/cache/backends/locmem.py"
    rows = []
    for i in range(13):
        symbol = "LocMemCache.get" if i == 0 else f"helper{i}"
        text = "def f():\n        return pickle.loads(pickled)\n" if i == 0 else "def f():\n    return 1\n"
        first = i * 3 + 1
        rows.append({"id": f"{path}:{symbol}:{first}-{first + 1}", "path": path, "symbol": symbol,
                     "start_line": first, "end_line": first + 1, "text": text,
                     "content_sha256": audit.sha(text.encode()),
                     "document": audit.document(path, symbol, text),
                     "document_sha256": audit.sha(audit.document(path, symbol, text).encode())})
    before, after = "        return pickle.loads(pickled)", "        return pickle.loads(pickled)  # edit"
    edited_hash = audit.sha(audit.document(path, "LocMemCache.get", rows[0]["text"].replace(before, after)).encode())
    sequence = [
        ("initial", 13, 13, 0, 0, 13), ("fresh_process_reopen", 13, 0, 13, 0, 13),
        ("saved_edit", 13, 1, 12, 0, 14), ("delete_file", 0, 0, 0, 13, 14),
        ("restore_edited_file", 13, 0, 13, 0, 14), ("rename_edited_file", 13, 13, 0, 13, 27),
        ("rename_back", 13, 0, 13, 13, 27), ("restore_pristine_file", 13, 0, 13, 0, 27),
        ("final_fresh_process_reopen", 13, 0, 13, 0, 27)]
    manifest = {"mutation": {"path": path, "renamed_path": "django/core/cache/backends/locmem_profile_copy.py",
                              "edited_window_id": rows[0]["id"], "edited_document_sha256": edited_hash,
                              "replacement_line_before": before, "replacement_line_after": after},
                "persistence_sequence": [{"stage": name, "total_windows": total, "indexed_windows": new,
                                          "new_unique_documents": new, "reused_windows": reuse,
                                          "deleted_windows": deleted, "cached_documents_after": cached}
                                         for name, total, new, reuse, deleted, cached in sequence]}
    return rows, manifest


def test_rename_creates_thirteen_path_bound_documents_and_back_reuses_cache():
    rows, manifest = fixture_mutation()
    states = audit.mutation_states(rows, manifest)
    assert len(states[5]["new_hashes"]) == 13
    assert len(states[-1]["cached_hashes"]) == 27
    assert states[6]["new_hashes"] == []
    assert states[3]["windows"] == []
    assert [row["id"] for row in states[-1]["windows"]] == [row["id"] for row in rows]


def test_zero_inference_rename_claim_cannot_pass_frozen_counts():
    rows, manifest = fixture_mutation()
    wrong = copy.deepcopy(manifest)
    wrong["persistence_sequence"][5]["indexed_windows"] = 0
    with pytest.raises(ValueError, match="mutation counts"):
        audit.mutation_states(rows, wrong)


def test_artifact_descriptor_bound_to_physical_bytes(tmp_path):
    path = tmp_path / "vector.npy"
    path.write_bytes(b"literal bytes")
    receipt = {"path": path.name, "sha256": audit.sha(path.read_bytes()), "bytes": path.stat().st_size}
    assert audit.bound_artifact(receipt, tmp_path)[0] == path
    path.write_bytes(b"changed bytes")
    with pytest.raises(ValueError, match="SHA"):
        audit.bound_artifact(receipt, tmp_path)


def small_snapshot(tmp_path):
    path = tmp_path / "snapshot.sqlite3"
    text = "def f():\n    return 1\n"
    row = {"id": "django/a.py:f:1-2", "path": "django/a.py", "symbol": "f", "start_line": 1,
           "end_line": 2, "text": text, "content_sha256": audit.sha(text.encode()),
           "document_sha256": audit.sha(audit.document("django/a.py", "f", text).encode())}
    metadata = {"dimensions": 1024, "batch_size": 4, "id": "synthetic CPU evidence"}
    fingerprint = audit.sha(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode())
    vector = np.zeros(1024, dtype=np.float32)
    vector[0] = 1.0
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            PRAGMA user_version=2;
            CREATE TABLE metadata(key TEXT,value TEXT);
            CREATE TABLE snippets(id TEXT,position INTEGER,path TEXT,symbol TEXT,start_line INTEGER,end_line INTEGER,
                                  text TEXT,content_hash TEXT,document_hash TEXT,model_fingerprint TEXT);
            CREATE TABLE embeddings(model_fingerprint TEXT,document_hash TEXT,dimensions INTEGER,vector BLOB);
            CREATE TABLE active_vectors(snippet_id TEXT,model_fingerprint TEXT,dimensions INTEGER,vector BLOB);
            CREATE TABLE snippets_fts(id TEXT,terms TEXT);
        """)
        connection.execute("INSERT INTO metadata VALUES (?,?)", ("active", json.dumps(
            {"dimensions": 1024, "model_fingerprint": fingerprint, "embedding": metadata})))
        connection.execute("INSERT INTO snippets VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (row["id"], 0, row["path"], row["symbol"], 1, 2, text,
                            row["content_sha256"], row["document_sha256"], fingerprint))
        connection.execute("INSERT INTO embeddings VALUES (?,?,?,?)",
                           (fingerprint, row["document_sha256"], 1024, vector.astype("<f8").tobytes()))
        connection.execute("INSERT INTO active_vectors VALUES (?,?,?,?)", (row["id"], fingerprint, 1024, vector.tobytes()))
        connection.execute("INSERT INTO snippets_fts VALUES (?,?)",
                           (row["id"], " ".join(audit.lexical_terms(f"{row['path']} {row['symbol']}\n{text}"))))
    expected = {"total_windows": 1, "cached_documents_after": 1, "windows": [row],
                "cached_hashes": [row["document_sha256"]]}
    return path, expected, {row["document_sha256"]: vector}, metadata


def test_physical_database_binds_sources_fts_native_cache_and_active_vectors(tmp_path):
    path, expected, vectors, metadata = small_snapshot(tmp_path)
    assert audit.verify_sqlite_snapshot(path, expected, vectors, metadata)["active_windows"] == 1


@pytest.mark.parametrize("statement", ["UPDATE snippets SET text='wrong'",
                                      "INSERT INTO snippets_fts VALUES ('deleted','stale')",
                                      "UPDATE active_vectors SET vector=zeroblob(4096)",
                                      "UPDATE embeddings SET vector=zeroblob(8192)",
                                      "UPDATE active_vectors SET dimensions=512",
                                      "UPDATE snippets SET position=1"])
def test_corrupt_database_or_stale_snapshot_is_rejected(tmp_path, statement):
    path, expected, vectors, metadata = small_snapshot(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(statement)
    with pytest.raises(ValueError):
        audit.verify_sqlite_snapshot(path, expected, vectors, metadata)


def delivered_fixture(monkeypatch):
    monkeypatch.setitem(sys.modules, "tiktoken", SimpleNamespace(
        get_encoding=lambda _: SimpleNamespace(encode=lambda *args, **kwargs: [1, 2, 3])))
    text = "def f():\n    return 1\n"
    row = {"id": "django/a.py:f:1-2", "path": "django/a.py", "symbol": "f", "start_line": 1,
           "end_line": 2, "window_end_line": 2, "source": text, "source_hash": audit.sha(text.encode()),
           "content_hash": audit.sha(text.encode()), "origin": "retrieval", "related_to": None,
           "truncated": False}
    query = {"query": "read f", "target_groups": [[{"path": "django/a.py", "symbol": "f",
                                                    "evidence_lines": [1, 2]}]]}
    response = {"query": "read f", "retrieval": "hybrid", "rerank": False, "source_budget": 12000,
                "results": [row], "total_source_chars": len(text),
                "token_usage": {"tokens": 3, "budget": 2000, "encoding": "cl100k_base", "truncated": False}}
    return response, query, {"django/a.py": text.encode()}


def test_source_credit_requires_full_target_and_exact_payload_tokens(monkeypatch):
    response, query, sources = delivered_fixture(monkeypatch)
    assert audit.verify_delivered(response, query, sources)["complete"] is True
    response["results"][0].update(end_line=1, source="def f():\n", source_hash=audit.sha(b"def f():\n"), truncated=True)
    response["total_source_chars"] = len("def f():\n")
    assert audit.verify_delivered(response, query, sources)["complete"] is False
    response["token_usage"]["tokens"] = 4
    with pytest.raises(ValueError, match="token count"):
        audit.verify_delivered(response, query, sources)


@pytest.mark.parametrize("field,value", [("source", "def f():\n    return 2\n"),
                                        ("source_hash", "incorrect"), ("content_hash", "incorrect"),
                                        ("truncated", True), ("id", "wrong"), ("origin", "dependency")])
def test_delivered_saved_source_or_full_window_binding_cannot_be_forged(monkeypatch, field, value):
    response, query, sources = delivered_fixture(monkeypatch)
    response["results"][0][field] = value
    with pytest.raises(ValueError):
        audit.verify_delivered(response, query, sources)


def test_native_order_uses_actual_post_truncation_lengths_and_stable_positions():
    ids = [[1] * 3, [2], [3] * 2, [4] * 2, [5] * 9000, [6] * 8192]
    order = [[1, 2, 3, 0], [4, 5]]
    batches = [[ids[i][:8192] for i in batch] for batch in order]
    result = audit.verify_native_batch_inputs(["one"] * 6, order, batches, ids, "documents")
    assert result == {"inputs": 6, "forwards": 2, "truncated_inputs": 1}
    with pytest.raises(ValueError, match="order"):
        audit.verify_native_batch_inputs(["one"] * 6, [[1, 3, 2, 0], [4, 5]], batches, ids, "documents")
    batches[0][0] = [99]
    with pytest.raises(ValueError, match="token IDs"):
        audit.verify_native_batch_inputs(["one"] * 6, order, batches, ids, "documents")


def test_physical_witness_rejects_replaced_bytes_even_same_size(tmp_path):
    path = tmp_path / "source.py"
    path.write_bytes(b"first")
    stat = path.stat()
    witness = {"device": stat.st_dev, "inode": stat.st_ino, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    audit.verify_witness(path, witness)
    path.write_bytes(b"other")
    with pytest.raises(ValueError, match="witness"):
        audit.verify_witness(path, witness)


def test_rotated_schedule_retains_first_warmup_and_all_eighty_per_provider():
    rows = audit.schedule([f"q{i}" for i in range(8)])
    assert len(rows) == 315
    assert rows[:3] == [{"phase": "first", "sweep": -1, "query_id": "q0", "provider": provider}
                        for provider in ("bm25", "dense", "hybrid")]
    for provider in ("bm25", "dense", "hybrid"):
        assert sum(row["provider"] == provider and row["phase"] == "warmup" for row in rows) == 24
        assert sum(row["provider"] == provider and row["phase"] == "measured" for row in rows) == 80
    assert rows[27]["query_id"] == "q1"
    assert rows[27]["provider"] == "dense"


def sample_clock(perf, wall=None):
    wall = perf if wall is None else wall
    seconds, nanos = divmod(wall, 1_000_000_000)
    return {"wall_time_ns": wall, "perf_counter_ns": perf,
            "utc": datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=nanos // 1000).isoformat()}


def sample_measurement(index=0):
    baseline = sample_clock(0)
    start, end = sample_clock(index * 10_000_000), sample_clock(index * 10_000_000 + 1_000_000)
    def receipt(sample, interval):
        return {"version": 1, "label": "test", "baseline": baseline, "sample": sample,
                "cumulative": audit.nanosecond_interval(baseline, sample), "interval": interval,
                "max_drift_ns": 2_000_000_000, "consistent": True}
    return {"start": receipt(start, None), "end": receipt(end, audit.nanosecond_interval(start, end)),
            "elapsed_s": 0.001}


def test_exact_nanosecond_measurement_rejects_forged_consistency_or_reset():
    value = sample_measurement(3)
    assert audit.measurement(value) == 0.001
    value["end"]["interval"]["drift_ns"] = 1
    with pytest.raises(ValueError, match="interval"):
        audit.measurement(value)
    value = sample_measurement(3)
    value["end"]["baseline"] = sample_clock(1)
    with pytest.raises(ValueError):
        audit.measurement(value)


def test_plain_event_utc_can_be_separately_sampled_without_relaxing_integrity_clock():
    value = sample_clock(1_000_000)
    value["utc"] = datetime.fromtimestamp(0.001005, timezone.utc).isoformat()
    audit.plain_stamp(value)
    with pytest.raises(ValueError, match="UTC"):
        audit.nanosecond_sample(value)


def sample_study(tmp_path, monkeypatch):
    response, example, sources = delivered_fixture(monkeypatch)
    queries = {f"q{i}": {**example, "id": f"q{i}"} for i in range(8)}
    expected = audit.schedule(list(queries))
    dense = {key: [{"id": "django/a.py:f:1-2", "score": 1.0}] for key in queries}
    rows = []
    for workload in ("store", "api"):
        for number, identity in enumerate(expected):
            count = int(workload == "api" and identity["provider"] != "bm25")
            row = {"sequence": len(rows) + 1, "event": "search_sample", "workload": workload,
                   "schedule_number": number, "identity": identity, "measurement": sample_measurement(len(rows)),
                   "native_delta": {"document_inputs": 0, "query_inputs": count,
                                    "attempted_forwards": count, "completed_forwards": count}}
            if workload == "store":
                row["candidates"] = dense[identity["query_id"]]
            else:
                row["response"] = copy.deepcopy(response)
                row["response"]["retrieval"] = identity["provider"]
                row["source_and_caps"] = {"payload_tokens": 3, "source_characters": len(response["results"][0]["source"]),
                                          "delivered_sources_exact": True}
            rows.append(row)
        summary = {provider: {"first_use_s": 0.001,
                              "warm": {**audit.timing_summary([0.001] * 80), "samples_s": [0.001] * 80}}
                   for provider in audit.PROVIDERS}
        (tmp_path / (workload + "-latency.json")).write_text(json.dumps(summary))
    def write_rows():
        (tmp_path / "samples.jsonl.gz").write_bytes(gzip.compress(
            ("\n".join(json.dumps(row) for row in rows) + "\n").encode(), mtime=0))
    write_rows()
    return rows, expected, queries, dense, sources, write_rows


def test_raw_timing_inventory_and_native_provider_deltas_are_independently_qualified(tmp_path, monkeypatch):
    _, expected, queries, dense, sources, _ = sample_study(tmp_path, monkeypatch)
    summary, candidates = audit.verify_samples(tmp_path, expected, queries, dense, sources)
    assert summary["store"]["dense"]["warm"]["count"] == 80
    assert len(candidates) == 24


@pytest.mark.parametrize("mutation", ["missing", "schedule", "bm25_native", "score", "clock", "percentile"])
def test_mixed_filtered_reordered_or_forged_samples_are_rejected(tmp_path, monkeypatch, mutation):
    rows, expected, queries, dense, sources, write_rows = sample_study(tmp_path, monkeypatch)
    if mutation == "missing":
        rows.pop()
    elif mutation == "schedule":
        rows[4]["identity"] = rows[5]["identity"]
    elif mutation == "bm25_native":
        rows[315]["native_delta"]["query_inputs"] = 1
    elif mutation == "score":
        rows[1]["candidates"] = [{"id": "django/a.py:f:1-2", "score": 0.5}]
    elif mutation == "clock":
        rows[2]["measurement"]["elapsed_s"] = 0.0001
    else:
        path = tmp_path / "api-latency.json"
        value = json.loads(path.read_text())
        value["dense"]["warm"]["p95_s"] = 0.5
        path.write_text(json.dumps(value))
    write_rows()
    with pytest.raises(ValueError):
        audit.verify_samples(tmp_path, expected, queries, dense, sources)


@pytest.mark.parametrize("value", [{"rss_peak_is_cumulative": False, "rss_peak_bytes": 1, "rss_current_bytes": 1},
                                   {"rss_peak_is_cumulative": True, "rss_peak_bytes": True, "rss_current_bytes": 1},
                                   {"rss_peak_is_cumulative": True, "rss_peak_bytes": 1, "rss_current_bytes": 0}])
def test_rss_units_and_cumulative_label_are_not_stage_allocations(value):
    with pytest.raises(ValueError):
        audit.verify_memory(value)


def test_disk_counts_wal_and_shm_separately_and_rejects_inconsistent_sum():
    value = {"files": {"index.sqlite3": 1024, "index.sqlite3-wal": 512, "index.sqlite3-shm": 256}, "total_bytes": 1792}
    audit.verify_disk(value)
    value["total_bytes"] = 1024
    with pytest.raises(ValueError, match="sum"):
        audit.verify_disk(value)


def test_empty_first_index_requires_physical_schema_snapshot_not_only_a_claim(tmp_path):
    path = tmp_path / "empty.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=2")
        for table in ("embeddings", "snippets", "active_vectors", "snippets_fts", "metadata"):
            connection.execute(f"CREATE TABLE {table}(value TEXT)")
    receipt = {"database_was_absent": True,
               "table_counts": {"embeddings": 0, "snippets": 0, "active_vectors": 0, "snippets_fts": 0},
               "snapshot": {"path": str(path), "sha256": audit.file_sha(path), "bytes": path.stat().st_size}}
    audit.verify_empty_database(receipt)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO embeddings VALUES ('imported-vector')")
    receipt["snapshot"].update(sha256=audit.file_sha(path), bytes=path.stat().st_size)
    with pytest.raises(ValueError, match="imported"):
        audit.verify_empty_database(receipt)


def test_special_postprocessor_tokens_must_survive_clipping_when_independently_encoded():
    full = [[1] * 9000 + [99]]
    clipped = [[1] * 8191 + [99]]
    result = audit.verify_native_batch_inputs(["literal"], [[0]], [clipped], full, "query", clipped)
    assert result["truncated_inputs"] == 1
    with pytest.raises(ValueError, match="token IDs"):
        audit.verify_native_batch_inputs(["literal"], [[0]], [[full[0][:8192]]], full, "query", clipped)


def native_operations_fixture(tmp_path, monkeypatch):
    class FakeTokenizer:
        @classmethod
        def from_file(cls, path):
            return cls()
        def no_truncation(self):
            pass
        def no_padding(self):
            pass
        def enable_truncation(self, **kwargs):
            pass
        def encode_batch(self, texts, **kwargs):
            return [SimpleNamespace(ids=[len(text), 99]) for text in texts]
    monkeypatch.setitem(sys.modules, "tokenizers", SimpleNamespace(Tokenizer=FakeTokenizer))
    first = [audit.document("django/a.py", f"f{i}", f"def f{i}():\n    return {i}\n") for i in range(9)]
    last = audit.document("django/a.py", "f0", "def f0():\n    return 999\n")
    expected = [("documents", first, None)] + [("query", ["formatted query"], "q0")] * 3 + [("documents", [last], None)]
    document_ids = {audit.sha(text.encode()): f"django/a.py:f{i}:1-2" for i, text in enumerate(first)}
    document_ids[audit.sha(last.encode())] = "django/a.py:f0:1-2"
    ledger, operations, forward = [], [], 0
    def emit(event, **fields):
        row = {"event": event, "sequence": len(ledger) + 1, "observed": sample_clock(len(ledger) * 1_000), **fields}
        ledger.append(row)
        return row
    for number, (kind, texts, query_id) in enumerate(expected, 1):
        hashes = [audit.sha(text.encode()) for text in texts]
        begin = emit("embedding_operation_started", number=number, kind=kind, query_id=query_id, input_sha256=hashes)
        if number == 1:
            emit("model_initialization_started")
            emit("model_initialization_completed")
        values = np.zeros((len(texts), 1024), dtype=np.float32)
        values[:, 0] = 1.0
        for offset in range(0, len(texts), 4):
            positions = list(range(offset, min(offset + 4, len(texts))))
            inputs = [{"position": i, "input_sha256": hashes[i], "document_id": document_ids.get(hashes[i]),
                       "input_ids": [len(texts[i]), 99], "input_ids_sha256": audit.sha(json.dumps(
                           [len(texts[i]), 99], separators=(",", ":")).encode()),
                       "actual_tokens": 2, "full_tokens": 2, "truncated": False} for i in positions]
            forward += 1
            common = {"operation": number, "forward_number": forward, "kind": kind,
                      "batch_size": len(positions), "inputs": inputs}
            emit("native_batch_started", **copy.deepcopy(common))
            emit("native_batch_completed", **copy.deepcopy(common),
                 vector_sha256=[audit.sha(values[i].tobytes()) for i in positions], native_compute_s=0.0)
        receipt = {"number": number, "kind": kind, "query_id": query_id, "input_sha256": hashes,
                   "inputs": len(texts), "start": begin["observed"], "end": sample_clock(len(ledger) * 1_000),
                   "matrix_raw_sha256": audit.sha(values.tobytes()), "shape": list(values.shape), "dtype": "float32"}
        if kind == "documents":
            path = tmp_path / f"doc-{number}.npy"
            np.save(path, values, allow_pickle=False)
            receipt.update(matrix_file=str(path), matrix_file_sha256=audit.file_sha(path))
        operations.append(receipt)
        emit("embedding_operation_completed", **copy.deepcopy(receipt))
    return operations, expected, ledger, {"path": str(tmp_path)}, document_ids


def test_native_start_completion_literal_ids_and_original_output_order_are_independently_audited(tmp_path, monkeypatch):
    arguments = native_operations_fixture(tmp_path, monkeypatch)
    vectors, query_hashes = audit.verify_operations(*arguments)
    assert len(vectors) == 10
    assert len(query_hashes) == 3


@pytest.mark.parametrize("fault", ["missing_completion", "swapped_position", "token_ids", "query_hash",
                                   "wrong_document_id", "forward_number", "vector_hash", "matrix_bytes"])
def test_native_receipt_corruptions_cannot_create_a_false_success(tmp_path, monkeypatch, fault):
    arguments = native_operations_fixture(tmp_path, monkeypatch)
    operations, _, ledger, _, _ = arguments
    starts = [row for row in ledger if row["event"] == "native_batch_started"]
    ends = [row for row in ledger if row["event"] == "native_batch_completed"]
    if fault == "missing_completion":
        ledger.remove(ends[0])
    elif fault == "swapped_position":
        starts[0]["inputs"][0]["position"] = 1
    elif fault == "token_ids":
        starts[0]["inputs"][0]["input_ids"] = [999, 99]
    elif fault == "query_hash":
        operations[1]["matrix_raw_sha256"] = "wrong"
    elif fault == "wrong_document_id":
        starts[0]["inputs"][0]["document_id"] = "django/a.py:another:1-2"
        ends[0]["inputs"][0]["document_id"] = "django/a.py:another:1-2"
    elif fault == "forward_number":
        starts[0]["forward_number"] = 99
    elif fault == "vector_hash":
        ends[0]["vector_sha256"][0] = "wrong"
    else:
        Path(operations[0]["matrix_file"]).write_bytes(b"overwritten")
    with pytest.raises(ValueError):
        audit.verify_operations(*arguments)


def physical_distribution(path, name="example-package", version="1.0", metadata_file="METADATA", direct_url=None):
    path.mkdir(parents=True)
    (path / metadata_file).write_text(f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n\n")
    if metadata_file == "METADATA":
        (path / "RECORD").write_bytes(b"literal,physical,record\n")
    if direct_url is not None:
        (path / "direct_url.json").write_text(json.dumps(direct_url))
    return SimpleNamespace(_path=path, metadata={"Name": name}, version=version)


def test_dist_and_egg_metadata_instances_remain_separate_under_one_logical_distribution(tmp_path):
    installed = physical_distribution(tmp_path / "installed/example.dist-info")
    source = physical_distribution(tmp_path / "source/example.egg-info", metadata_file="PKG-INFO")
    inventory = audit.package_inventory([source, installed], root=tmp_path)
    assert inventory["logical_distribution_count"] == 1
    assert inventory["physical_metadata_instance_count"] == 2
    instances = inventory["distributions"]["example-package"]["instances"]
    assert [row["metadata_origin"] for row in instances] == ["installed/example.dist-info", "source/example.egg-info"]
    assert instances[0]["record_sha256"] == audit.sha(b"literal,physical,record\n")
    assert instances[1]["record_sha256"] is None
    assert instances[0]["metadata_sha256"] == audit.file_sha(installed._path / "METADATA")


def test_repeated_discovery_of_one_resolved_directory_deduplicates_only_identical_records(tmp_path):
    first = physical_distribution(tmp_path / "physical.dist-info")
    alias = tmp_path / "metadata-alias"
    alias.symlink_to(first._path, target_is_directory=True)
    same = SimpleNamespace(_path=alias, metadata=first.metadata, version=first.version)
    inventory = audit.package_inventory([first, same], root=tmp_path)
    assert inventory["physical_metadata_instance_count"] == 1
    def mutated():
        yield first
        (first._path / "RECORD").write_bytes(b"changed record")
        yield same
    with pytest.raises(ValueError, match="repeated physical"):
        audit.package_inventory(mutated(), root=tmp_path)


@pytest.mark.parametrize("name,version,match", [("example-package", "2.0", "versions"),
                                              ("Example_Package", "1.0", "canonical")])
def test_metadata_version_conflicts_and_ambiguous_canonical_spellings_fail(tmp_path, name, version, match):
    first = physical_distribution(tmp_path / "first.dist-info")
    second = physical_distribution(tmp_path / "second.dist-info", name, version)
    with pytest.raises(ValueError, match=match):
        audit.package_inventory([first, second], root=tmp_path)


@pytest.mark.parametrize("damage", ["ambiguous", "unsupported", "forged_name", "forged_version"])
def test_physical_metadata_names_and_origins_are_independently_checked(tmp_path, damage):
    distribution = physical_distribution(tmp_path / "example.dist-info")
    if damage == "ambiguous":
        (distribution._path / "PKG-INFO").write_text("another metadata file")
    elif damage == "unsupported":
        distribution._path = "not a physical pathlib origin"
    elif damage == "forged_name":
        distribution.metadata["Name"] = "forged-package"
    else:
        distribution.version = "9.0"
    with pytest.raises(ValueError):
        audit.package_inventory([distribution], root=tmp_path)


def test_known_editable_project_retains_exact_origin_url_metadata_and_record_hashes(tmp_path):
    direct = {"url": tmp_path.as_uri(), "dir_info": {"editable": True}}
    installed = physical_distribution(tmp_path / ".venv/project.dist-info", "semantic-search-engine", "0.1.0", direct_url=direct)
    source = physical_distribution(tmp_path / "src/semantic_search_engine.egg-info", "semantic-search-engine", "0.1.0", "PKG-INFO")
    actual = audit.package_inventory([source, installed], root=tmp_path)
    instances = actual["distributions"]["semantic-search-engine"]["instances"]
    assert actual["logical_distribution_count"] == 1 and actual["physical_metadata_instance_count"] == 2
    assert instances[0]["direct_url"] == direct
    assert instances[0]["direct_url_sha256"] == audit.file_sha(installed._path / "direct_url.json")
    assert instances[1]["metadata_file"] == "PKG-INFO"


@pytest.mark.parametrize("damage", ["foreign_url", "relative_url", "remote_url", "hosted_file_url",
                                    "foreign_egg", "missing_direct", "coerced_editable"])
def test_foreign_or_ambiguous_editable_installation_cannot_pass(tmp_path, damage):
    direct = {"url": tmp_path.as_uri(), "dir_info": {"editable": True}}
    if damage == "foreign_url":
        direct["url"] = (tmp_path / "other").as_uri()
    elif damage == "relative_url":
        direct["url"] = "file:"
    elif damage == "remote_url":
        direct["url"] = "https://example.com/project"
    elif damage == "hosted_file_url":
        direct["url"] = "file://localhost" + str(tmp_path)
    elif damage == "coerced_editable":
        direct["dir_info"]["editable"] = 1
    installed = physical_distribution(tmp_path / ".venv/project.dist-info", "semantic-search-engine", "0.1.0",
                                      direct_url=None if damage == "missing_direct" else direct)
    source = physical_distribution(tmp_path / ("other.egg-info" if damage == "foreign_egg" else "src/semantic_search_engine.egg-info"),
                                   "semantic-search-engine", "0.1.0", "PKG-INFO")
    with pytest.raises(ValueError):
        audit.package_inventory([installed, source], root=tmp_path)


def owned_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    work = tmp_path / ".cache/study"
    work.mkdir(parents=True)
    study = tmp_path / "results/study"
    study.mkdir(parents=True)
    command = [str(tmp_path / ".venv/bin/python"), "-B",
               str(tmp_path / "scripts/run_django_index_profile.py"), "--_worker",
               str(study), "--owner-pid", "4321"]
    raw_hash = audit.sha(json.dumps(command).encode())
    private = {"command": command, "command_sha256": raw_hash, "owner_pid": 4321,
               "pid": 4322, "owned_group": 4322, "start_new_session": True,
               "observed": {"perf_counter_ns": 123, "wall_time_ns": 456, "utc": "invented CPU fixture"}}
    published = {**private, "command": [value.replace(str(tmp_path), "<PROJECT>") for value in command]}
    (work / "owned-process.json").write_text(json.dumps(private))
    return published, private, {"runtime": {"executable": ".venv/bin/python"}, "work": ".cache/study"}, study, work


def test_raw_hash_survives_public_redaction_only_after_exact_known_restoration(tmp_path, monkeypatch):
    public, private, plan, study, _ = owned_fixture(tmp_path, monkeypatch)
    assert audit.sha(json.dumps(public["command"]).encode()) != private["command_sha256"]
    proof = audit.verify_owned_command(public, plan, study)
    assert proof["private_argv_exact"] and proof["expected_frozen_argv_exact"]
    assert proof["raw_command_sha256"] == private["command_sha256"]
    assert proof["published_command_sha256"] != proof["raw_command_sha256"]


@pytest.mark.parametrize("damage", ["interpreter", "runner", "study", "owner-arg", "mode", "unknown-placeholder",
                                    "redacted-hash", "compact-hash", "nonstring", "bool-owner",
                                    "private-argv", "private-hash", "private-pid", "private-clock", "private-extra", "missing-private"])
def test_corrected_owned_hash_still_rejects_identity_or_serialization_mutations(tmp_path, monkeypatch, damage):
    public, private, plan, study, work = owned_fixture(tmp_path, monkeypatch)
    if damage in {"interpreter", "runner", "study", "owner-arg", "mode", "unknown-placeholder"}:
        slot = {"interpreter": 0, "runner": 2, "study": 4, "owner-arg": 6, "mode": 3, "unknown-placeholder": 0}[damage]
        public["command"][slot] = "<UNVERIFIED>/evil" if damage == "unknown-placeholder" else "/changed"
        private["command"][slot] = public["command"][slot].replace("<PROJECT>", str(tmp_path))
        public["command_sha256"] = private["command_sha256"] = audit.sha(json.dumps(private["command"]).encode())
    elif damage == "redacted-hash":
        public["command_sha256"] = audit.sha(json.dumps(public["command"]).encode())
    elif damage == "compact-hash":
        public["command_sha256"] = audit.sha(json.dumps(private["command"], separators=(",", ":")).encode())
    elif damage == "nonstring":
        public["command"][1] = 3
    elif damage == "bool-owner":
        public["owner_pid"] = True
    elif damage == "private-argv":
        private["command"][0] = "/changed"
    elif damage == "private-hash":
        private["command_sha256"] = "0" * 64
    elif damage == "private-pid":
        private["pid"] += 1
    elif damage == "private-clock":
        private["observed"] = {**private["observed"], "perf_counter_ns": 999}
    elif damage == "private-extra":
        private["unbound"] = True
    (work / "owned-process.json").write_text(json.dumps(private))
    if damage == "missing-private":
        (work / "owned-process.json").unlink()
    with pytest.raises((ValueError, OSError)):
        audit.verify_owned_command(public, plan, study)
