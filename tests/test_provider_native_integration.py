"""Invented real-backend integration; never a model or observed-corpus run."""
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import time
from types import ModuleType
from unittest.mock import patch

import pytest

from experiments.provider_native import integration


QUERY = "load alpha payload and render beta template"


def query_record(text=QUERY):
    return {"id": "invented_query", "query": text, "kind": "absent", "targets": []}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    native, fast = integration.modules()
    import numpy as np
    from semantic_search.backend import SearchBackend

    def forbidden(*args, **kwargs):
        raise AssertionError("Only invented CPU work is allowed")

    monkeypatch.setattr(native.QwenEmbedder, "_ensure_loaded", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    cache_key = hashlib.sha1(b"https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken").hexdigest()
    candidates = [Path(os.environ.get("TIKTOKEN_CACHE_DIR", integration.ROOT / ".cache/tokenizers")),
        integration.ROOT / ".cache/tokenizers", integration.ROOT.parents[2] / ".cache/tokenizers"]
    cache = next((value for value in candidates if (value / cache_key).is_file()), None)
    assert cache is not None, "Verified existing offline tokenizer cache required; no downloads allowed"
    assert hashlib.sha256((cache / cache_key).read_bytes()).hexdigest() == "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache))

    class InventedTokenizer:
        def __call__(self, texts, **kwargs):
            return {"input_ids": [[value+1 for value in text.encode()] for text in texts]}

    class InventedEmbedder(native.RecordingEmbedder):
        def __init__(self):
            super().__init__(tmp_path / "never-loaded-invented-model", progress=False)
            self._tokenizer = InventedTokenizer()
            self.actual_forwards = []
            self.before_forward = None

        @property
        def metadata(self):
            return {**super().metadata, "synthetic_fixture": "Invented CPU outputs only"}

        def _ensure_loaded(self):
            pass

        def _embed_batch(self, rows):
            texts = [bytes(value-1 for value in tokens).decode() for tokens in rows]
            self.actual_forwards.append(texts)
            if self.before_forward is not None:
                self.before_forward(texts)
            values = np.zeros((len(rows), 1024), dtype=np.float32)
            for index, text in enumerate(texts):
                axis = (1 if text == native.provider_module.QUERY_PROMPT + "load alpha payload" or "def implementation_9(" in text
                        else 2 if text == native.provider_module.QUERY_PROMPT + "render beta template" or "def implementation_10(" in text else 0)
                values[index, axis] = 1
            return values

    repo = tmp_path / "invented-repository"
    (repo / "src").mkdir(parents=True)
    source = repo / "src/code.py"
    source.write_text("\n".join(f"def implementation_{number}():\n    return {number}\n" for number in range(1, 12)))
    embedder = InventedEmbedder()
    with closing(SearchBackend(repo, "src", tmp_path / "invented.sqlite3", embedder=embedder)) as backend:
        backend.refresh()
        yield native, fast, backend, embedder, source


@pytest.mark.parametrize("arm", ["original_role", "fast_role"])
@pytest.mark.parametrize("retrieval", ["dense", "hybrid"])
def test_real_search_retains_live_vectors_forwards_v1_and_role(fixture, arm, retrieval):
    native, _, backend, embedder, _ = fixture
    before = len(embedder.actual_forwards)
    row = integration.measure(backend, embedder, query_record(), 1, arm, retrieval)
    metrics, event = row["provider_integration"], row["candidate_capture"]
    assert row["arm"] == arm and row["condition"] == native.CANDIDATE
    assert metrics["implementation"] == integration.ROLE_ARMS[arm]
    assert [metrics[name] for name in ("factory_attempts", "factory_completed", "provider_attempts", "provider_completed")] == [1]*4
    assert metrics["factory_failed"] == metrics["provider_failed"] == 0
    assert metrics["factory_elapsed_s"] > 0 and metrics["provider_elapsed_s"] > 0
    assert metrics["factory_elapsed_s"] + metrics["provider_elapsed_s"] < row["elapsed_backend_s"]
    assert event["calls"] == {"coverage_coalesce": 1, "semantic": 1, "role_guard": 1, "feature": 1,
        "current_v1": 1, "extra_retrieval": 0, "document_inference": 0, "backfill_after_guard": 0}
    assert row["query_embedding_calls"] == row["clause_query_calls"] == 1
    assert row["clause_query_texts"] == 2 and row["document_embedding_calls"] == 0
    assert len(embedder.actual_forwards) == before+2
    assert embedder.actual_forwards[before] == [native.provider_module.QUERY_PROMPT + QUERY]
    assert sorted(embedder.actual_forwards[before+1]) == sorted([native.provider_module.QUERY_PROMPT + "load alpha payload",
        native.provider_module.QUERY_PROMPT + "render beta template"])
    assert len(row["resident_reads"]) == 1
    features = event["features"]
    assert features["facet_vectors"][0] == row["query_vector"]
    assert event["provider_receipts"][0]["original_query_vector"]["float32_le_sha256"] == row["query_vector_sha256"]
    for key, value in row["resident_reads"][0]["returned_vectors"].items():
        assert value["vector"] == features["windows"][key]["vector"]
    assert event["role_guard_receipt"]["accepted"] and event["semantic_receipt"]["accepted"]
    assert row["compact_sha256"] != hashlib.sha256(native.context.serialize_context(
        native.context.compact_result(event["reference_response"])).encode()).hexdigest()
    assert backend._research_postprocessor is None and backend._research_forward_observer is None


@pytest.mark.parametrize("retrieval", ["dense", "hybrid"])
def test_original_and_fast_have_exact_feature_compact_v1_role_and_resident_parity(fixture, retrieval):
    native, _, backend, embedder, _ = fixture
    rows = [integration.measure(backend, embedder, query_record(), 1, arm, retrieval)
            for arm in ("original_role", "fast_role")]
    first, second = rows
    for name in ("compact_payload", "compact_sha256", "query_vector", "query_vector_sha256", "stages",
                 "ranked_candidates", "candidate_ranks", "delivered_ranks", "resident_reads"):
        assert first[name] == second[name]
    for name in ("features", "semantic_receipt", "role_guard_receipt", "definition_keys", "precoalesce_rows"):
        assert first["candidate_capture"][name] == second["candidate_capture"][name]
    assert native.source_view(first["response"]) == native.source_view(second["response"])
    assert native.source_view(first["candidate_capture"]["v1_response"]) == native.source_view(second["candidate_capture"]["v1_response"])


@pytest.mark.parametrize("retrieval", ["dense", "hybrid"])
@pytest.mark.parametrize("arm", integration.ARMS)
def test_frozen_independent_trace_auditor_reconstructs_each_actual_arm(fixture, retrieval, arm):
    native, _, backend, embedder, source = fixture
    independent = importlib.import_module("audit_role_native")
    bundle = {"inventory": {item.id: asdict(item) for item in backend._index.snippets},
        "definition_keys": json.loads(json.dumps(backend._definition_keys)), "sources": {"src/code.py": source.read_text()},
        "graph_edges": {caller: list(targets) for caller, targets in backend._references.edges.items()}}
    row = integration.measure(backend, embedder, query_record(), 1, arm, retrieval)
    # The unchanged independent auditor sees the original three-arm contract.
    # This projection changes identity only, retaining every source/forward/
    # vector/feature/proposal/role receipt from the executing search.
    # Match the existing ledger's JSON transport (canonical keys may be tuples
    # in memory, and are lists in every saved native trace).
    projected = json.loads(json.dumps({**row, "arm": integration.mapped_arm(arm)}, ensure_ascii=False, allow_nan=False))
    independent.verify_trace(projected, bundle, native.context.encoding(), query=QUERY,
        scope=native.context.indexed_scope("src"), model_metadata=embedder.metadata)


@pytest.mark.parametrize("arm", ["original_role", "fast_role"])
def test_ineligible_query_still_times_actual_factory_and_clone_setup(fixture, arm):
    _, _, backend, embedder, _ = fixture
    row = integration.measure(backend, embedder, query_record("find one implementation"), 1, arm, "dense")
    metrics = row["provider_integration"]
    assert metrics["factory_attempts"] == metrics["factory_completed"] == 1
    assert metrics["factory_elapsed_s"] > 0
    assert metrics["provider_attempts"] == metrics["provider_completed"] == 0
    assert metrics["provider_elapsed_s"] == 0.0
    assert row["candidate_capture"]["features"] is None
    assert row["candidate_capture"]["provider_receipts"] == row["resident_reads"] == []
    assert row["clause_query_calls"] == row["clause_query_texts"] == 0


@pytest.mark.parametrize("arm", ["legacy", "coverage_control"])
def test_controls_execute_no_factory_provider_or_role_work(fixture, monkeypatch, arm):
    native, fast, backend, embedder, _ = fixture
    def forbidden(*args, **kwargs):
        pytest.fail("Control touched a provider factory")
    monkeypatch.setattr(native.provider_module, "make_live_feature_provider", forbidden)
    monkeypatch.setattr(fast, "make_fast_feature_provider", forbidden)
    row = integration.measure(backend, embedder, query_record(), 1, arm, "dense")
    assert row["arm"] == arm and row["candidate_capture"] is None
    assert row["provider_integration"] == integration.new_metrics(arm)
    assert row["clause_query_calls"] == row["clause_query_texts"] == 0
    assert row["guard_calls"] == 0 and row["resident_reads"] == []


@pytest.mark.parametrize("arm", ["original_role", "fast_role"])
def test_unchanged_terminal_role_fallback_is_retained(fixture, arm):
    native, _, backend, embedder, source = fixture
    source.write_text("\n".join(f"def implementation_{number}(obj):\n    obj.value = {number}\n    return {number}\n"
        for number in range(1, 12)))
    backend.refresh()
    row = integration.measure(backend, embedder, query_record(), 1, arm, "dense")
    event = row["candidate_capture"]
    assert event["semantic_receipt"]["accepted"] and not event["role_guard_receipt"]["accepted"]
    assert event["role_guard_receipt"]["fallback_reason"] == "protected_role_row_changed"
    assert native.source_view(row["response"]) == native.source_view(event["reference_response"])


def test_fast_factory_clones_once_per_actual_request_inside_search(fixture, monkeypatch):
    native, fast, backend, embedder, _ = fixture
    clones, searches, actual = [], [], fast.clone_functions
    original_search = backend.search
    def search(*args, **kwargs):
        searches.append("entered")
        try:
            return original_search(*args, **kwargs)
        finally:
            searches.append("finished")
    def clone():
        assert searches[-1] == "entered"
        clones.append(True)
        return actual()
    monkeypatch.setattr(backend, "search", search)
    monkeypatch.setattr(fast, "clone_functions", clone)
    for repetition in (1, 2):
        integration.measure(backend, embedder, query_record(), repetition, "fast_role", "dense")
    assert len(clones) == 2 and embedder.clause_query_calls == 2
    assert native.provider_module.make_live_feature_provider is fast.ORIGINAL_FACTORY


def test_public_capture_and_receipt_reuse_whole_actual_search(fixture):
    native, _, backend, embedder, _ = fixture
    with integration.capture("fast_role", backend) as captured:
        started = time.perf_counter()
        response = backend.search(QUERY, retrieval="dense", **native.SETTINGS)
        elapsed = time.perf_counter()-started
    row = integration.receipt(query_record(), 1, "dense", "fast_role", response, captured, elapsed, embedder.last_query)
    assert row["arm"] == "fast_role" and row["provider_integration"]["factory_completed"] == 1
    assert row["candidate_capture"]["feature_calls"] == row["provider_integration"]["provider_completed"] == 1


@pytest.mark.parametrize("arm", ["original_role", "fast_role"])
@pytest.mark.parametrize("phase", ["query", "clauses"])
def test_model_api_failure_preserves_actual_error_partial_forward_and_integration(fixture, arm, phase):
    native, fast, backend, embedder, _ = fixture
    from semantic_search.cancellation import SearchCancelled
    error, records = SearchCancelled("invented " + phase), []
    def fail(texts):
        is_query = texts == [native.provider_module.QUERY_PROMPT + QUERY]
        if (phase == "query") == is_query:
            raise error
    embedder.before_forward = fail
    with pytest.raises(SearchCancelled) as caught:
        integration.measure(backend, embedder, query_record(), 1, arm, "dense", on_failure=records.append)
    assert caught.value is error and len(records) == 1
    record, metrics = records[0], records[0]["provider_integration"]
    assert record["arm"] == arm
    assert metrics["factory_attempts"] == metrics["factory_completed"] == (0 if phase == "query" else 1)
    assert metrics["provider_attempts"] == metrics["provider_failed"] == (0 if phase == "query" else 1)
    assert metrics["provider_completed"] == metrics["factory_failed"] == 0
    statuses = [value["status"] for value in record["partial_capture"]["external_forward_events"]]
    assert statuses == (["started", "failed"] if phase == "query" else ["started", "completed"])
    if phase == "clauses":
        event = record["partial_capture"]["semantic_events"][0]
        assert event["provider_receipts"][0]["status"] == "failed"
    assert native.provider_module.make_live_feature_provider is fast.ORIGINAL_FACTORY
    assert backend._research_postprocessor is None and backend._research_forward_observer is None


def test_timed_factory_argument_identity_and_partial_failures():
    metrics = integration.new_metrics("original_role")
    values, received = object(), []
    error = KeyboardInterrupt("invented provider cancellation")
    def original(*args, **kwargs):
        received.append((args, kwargs))
        def provider(*args, **kwargs):
            received.append((args, kwargs))
            raise error
        return provider
    factory = integration.timed_factory(original, metrics)
    def callback():
        pass
    provider = factory(values, capture=callback)
    with pytest.raises(KeyboardInterrupt) as caught:
        provider(values, cancel_check=callback)
    assert caught.value is error
    assert received == [((values,), {"capture": callback}), ((values,), {"cancel_check": callback})]
    assert metrics["factory_attempts"] == metrics["factory_completed"] == metrics["provider_attempts"] == metrics["provider_failed"] == 1
    assert metrics["factory_failed"] == metrics["provider_completed"] == 0
    def failed(*args, **kwargs):
        raise error
    with pytest.raises(KeyboardInterrupt) as caught:
        integration.timed_factory(failed, metrics)(values)
    assert caught.value is error and metrics["factory_attempts"] == 2 and metrics["factory_failed"] == 1


def test_failure_callback_error_keeps_original_search_error(fixture):
    native, _, backend, embedder, _ = fixture
    original, recording = RuntimeError("invented query failure"), OSError("invented recorder failure")
    embedder.before_forward = lambda texts: (_ for _ in ()).throw(original)
    def failed(value):
        assert value["arm"] == "fast_role"
        raise recording
    with pytest.raises(RuntimeError) as caught:
        integration.measure(backend, embedder, query_record(), 1, "fast_role", "dense", on_failure=failed)
    assert caught.value is original and caught.value.__cause__ is recording


def test_fast_factory_clone_failure_records_completed_original_query(fixture, monkeypatch):
    native, fast, backend, embedder, _ = fixture
    error, records = RuntimeError("invented clone failure"), []
    def fail():
        raise error
    monkeypatch.setattr(fast, "clone_functions", fail)
    with pytest.raises(RuntimeError) as caught:
        integration.measure(backend, embedder, query_record(), 1, "fast_role", "dense", on_failure=records.append)
    assert caught.value is error and len(records) == 1
    record, metrics = records[0], records[0]["provider_integration"]
    assert metrics["factory_attempts"] == metrics["factory_failed"] == 1
    assert metrics["factory_completed"] == metrics["provider_attempts"] == 0
    assert metrics["factory_elapsed_s"] > 0 and metrics["provider_elapsed_s"] == 0.0
    assert record["attempted_api_calls"] == {"query": 1, "clauses": 0, "clause_texts": 0, "documents": 0}
    assert [value["status"] for value in record["partial_capture"]["external_forward_events"]] == ["started", "completed"]
    assert native.provider_module.make_live_feature_provider is fast.ORIGINAL_FACTORY


def test_reporting_failure_retains_completed_search_and_integration(fixture, monkeypatch):
    native, _, backend, embedder, _ = fixture
    error, records = RuntimeError("invented reporting error"), []
    def report(*args, **kwargs):
        raise error
    monkeypatch.setattr(native, "receipt", report)
    with pytest.raises(RuntimeError) as caught:
        integration.measure(backend, embedder, query_record(), 1, "fast_role", "dense", on_failure=records.append)
    assert caught.value is error and len(records) == 1
    assert records[0]["provider_integration"]["factory_completed"] == 1
    assert records[0]["provider_integration"]["provider_completed"] == 1
    assert records[0]["partial_capture"]["role_guard_calls"] == 1


def test_integration_validation_failure_retains_complete_native_receipt(fixture, monkeypatch):
    _, _, backend, embedder, _ = fixture
    error, records = ValueError("invented integration reporting error"), []
    monkeypatch.setattr(integration, "checked_metrics", lambda *args: (_ for _ in ()).throw(error))
    with pytest.raises(ValueError) as caught:
        integration.measure(backend, embedder, query_record(), 1, "fast_role", "dense", on_failure=records.append)
    assert caught.value is error and records[0]["reporting_error_after_search"] is True
    assert records[0]["completed_native_receipt"]["arm"] == "fast_role"
    assert records[0]["provider_integration"]["provider_completed"] == 1


def test_foreign_helper_and_own_source_are_rejected(tmp_path):
    foreign = ModuleType("semantic_search.invented_foreign")
    foreign.__file__ = str(tmp_path / "foreign.py")
    with patch.dict(sys.modules, {foreign.__name__: foreign}):
        with pytest.raises(ValueError, match="escapes the bound checkout"):
            integration.source_authority()
    with patch.object(integration, "__file__", str(tmp_path / "integration.py")):
        with pytest.raises(ValueError, match="integration escapes"):
            integration.source_authority()


@pytest.mark.parametrize("arm", ["candidate", "unknown", None])
def test_unknown_arms_cannot_be_relabelled(arm):
    with pytest.raises(ValueError, match="Unknown provider native arm"):
        integration.new_metrics(arm)


def test_component_timer_excludes_reporting_and_includes_factory_clone_work(fixture, monkeypatch):
    native, fast, backend, embedder, _ = fixture
    clock = [100.0]
    monkeypatch.setattr(integration.time, "perf_counter", lambda: clock[0])
    actual_factory, actual_allocate, actual_role, actual_search, actual_report = (
        fast.make_fast_feature_provider, native.semantic_module.semantic_final_context,
        native.role.apply_role_guard, backend.search, native.receipt)
    def factory(*args, **kwargs):
        clock[0] += 2.0  # setup/clone work is inside the whole search
        provider = actual_factory(*args, **kwargs)
        def provide(*args, **kwargs):
            clock[0] += 5.0
            return provider(*args, **kwargs)
        return provide
    def allocated(*args, **kwargs):
        clock[0] += 3.0
        return actual_allocate(*args, **kwargs)
    def role(*args, **kwargs):
        clock[0] += 7.0
        return actual_role(*args, **kwargs)
    def search(*args, **kwargs):
        clock[0] += 11.0
        return actual_search(*args, **kwargs)
    def report(*args, **kwargs):
        clock[0] += 1000.0
        return actual_report(*args, **kwargs)
    monkeypatch.setattr(fast, "make_fast_feature_provider", factory)
    monkeypatch.setattr(native.semantic_module, "semantic_final_context", allocated)
    monkeypatch.setattr(native.role, "apply_role_guard", role)
    monkeypatch.setattr(backend, "search", search)
    monkeypatch.setattr(native, "receipt", report)
    row = integration.measure(backend, embedder, query_record(), 1, "fast_role", "dense")
    assert row["elapsed_backend_s"] == 28.0
    assert row["provider_integration"]["factory_elapsed_s"] == 2.0
    assert row["provider_integration"]["provider_elapsed_s"] == 5.0
    assert row["candidate_capture"]["phase_timings"]["live_feature_provider_s"] == 5.0
    assert row["candidate_capture"]["phase_timings"]["prepared_feature_allocation_s"] == 3.0
    assert row["candidate_capture"]["phase_timings"]["terminal_role_guard_s"] == 7.0


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf"), True])
def test_invalid_reported_component_durations_fail_closed(value):
    metrics = integration.new_metrics("legacy")
    metrics["provider_elapsed_s"] = value
    with pytest.raises(ValueError, match="duration"):
        integration.checked_metrics(metrics, {"candidate_capture": None, "elapsed_backend_s": 1.0})


def test_actual_eight_conditions_use_lossless_native_tuple_transport(fixture, tmp_path):
    """Exercise the actual measure→archive boundary that unit traces omitted."""
    _, _, backend, embedder, _ = fixture
    study = importlib.import_module("experiments.provider_native.study")
    strict = importlib.import_module("experiments.dependency_context.study_inputs")
    path, rows, snapshots, expected_lines = tmp_path / "native-conditions.jsonl.gz", [], [], []
    with study.Ledger(path) as ledger:
        for arm in integration.ARMS:
            for mode in ("dense", "hybrid"):
                row = integration.measure(backend, embedder, query_record(), 1, arm, mode)
                # These are original frozen native in-memory metadata types,
                # intentionally arrays after the historical JSON transport.
                assert any(type(value) is tuple for value in row["stages"]["origins"].values())
                assert all(type(value) is tuple for value in row["stages"]["definition_keys"].values())
                if arm in integration.ROLE_ARMS:
                    assert all(type(value) is tuple for value in row["candidate_capture"]["definition_keys"].values())
                assert not strict.literal_json(row)
                snapshots.append(deepcopy(row))
                expected_lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()+b"\n")
                ledger.append(row)
                rows.append(row)
    assert rows == snapshots  # Transport may not rewrite the actual receipts.
    with gzip.open(path, "rb") as stream:
        wire = stream.read()
    assert wire == b"".join(expected_lines)  # Includes nested dictionary order.
    decoded = [json.loads(line) for line in wire.splitlines()]
    assert len(decoded) == 8 and all(strict.literal_json(row) for row in decoded)
    for actual, saved in zip(rows, decoded, strict=True):
        assert saved == json.loads(json.dumps(actual, ensure_ascii=False, allow_nan=False))
        for key in ("compact_payload", "compact_sha256", "query_vector", "query_vector_sha256", "provider_integration"):
            assert saved[key] == actual[key]
        if actual["candidate_capture"] is not None:
            for key in ("features", "provider_receipts", "semantic_receipt", "role_guard_receipt"):
                assert saved["candidate_capture"][key] == actual["candidate_capture"][key]
