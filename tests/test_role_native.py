"""Invented CPU contracts for prospectively frozen native role-guard requests.

These fixtures never open a real checkpoint or fresh evaluation repository.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from semantic_search.backend import SearchBackend
from semantic_search.cancellation import SearchCancelled
from semantic_search.embeddings import DIMENSIONS, QUERY_PROMPT, QwenEmbedder


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
native = importlib.import_module("benchmark_role_native")
QUERY = "load alpha payload and render beta template"


class InventedTokenizer:
    def __call__(self, texts, **kwargs):
        return {"input_ids": [[value + 1 for value in text.encode()] for text in texts]}


class InventedEmbedder(native.RecordingEmbedder):
    """Exercise the real observer/batching API without loading model weights."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tokenizer = InventedTokenizer()
        self.actual_forwards = []
        self.before_forward = None
        self.failure = None

    @property
    def metadata(self):
        return {**super().metadata, "synthetic_fixture": "Invented CPU outputs, not checkpoint evidence"}

    def _ensure_loaded(self):
        pass

    def _embed_batch(self, ids):
        texts = [bytes(value - 1 for value in tokens).decode() for tokens in ids]
        self.actual_forwards.append(texts)
        if self.before_forward is not None:
            self.before_forward(texts)
        if self.failure is not None:
            raise self.failure
        vectors = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for position, text in enumerate(texts):
            axis = (1 if text == QUERY_PROMPT + "load alpha payload" or "def implementation_9(" in text
                    else 2 if text.startswith(QUERY_PROMPT + "render beta template")
                    or "def implementation_10(" in text else 0)
            vectors[position, axis] = 1
        return vectors


@pytest.fixture(autouse=True)
def forbid_real_models_and_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Only invented CPU fixtures are allowed in this test suite")

    monkeypatch.setattr(QwenEmbedder, "_ensure_loaded", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def fixture(tmp_path):
    repo = tmp_path / "invented-repository"
    (repo / "src").mkdir(parents=True)
    (repo / "src/code.py").write_text("\n".join(
        f"def implementation_{number}():\n    return {number}\n" for number in range(1, 12)))
    embedder = InventedEmbedder(tmp_path / "invented-model", progress=False)
    with closing(SearchBackend(repo, "src", tmp_path / "invented.sqlite3", embedder=embedder)) as backend:
        yield repo, backend, embedder


def options(tmp_path):
    return SimpleNamespace(candidate=native.CANDIDATE, development_study=tmp_path / "invented-parent",
        dataset=tmp_path / "unread-invented-data.json", corpus=tmp_path / "unread-invented-corpus",
        model=tmp_path / "unread-invented-model", output=tmp_path / "attempt",
        source_review=tmp_path / "unread-invented-source-review.json",
        freshness_review=tmp_path / "unread-invented-freshness-review.json",
        definition_inventory=tmp_path / "unread-invented-definitions.json", repetitions=3,
        role="repository_holdout", tokenizer_cache=tmp_path / "invented-tokenizers",
        runtime_manifest=tmp_path / "unread-invented-runtime.json")


def configured_search(backend, condition, *, query=QUERY, retrieval="dense"):
    previous = backend._research_postprocessor, backend._research_forward_observer
    try:
        with native.capture(condition) as captured:
            backend._research_postprocessor = captured["postprocessor"] if condition == native.CANDIDATE else None
            backend._research_forward_observer = captured["forward_observer"]
            response = backend.search(query, retrieval=retrieval, **native.SETTINGS)
        return response, captured
    finally:
        backend._research_postprocessor, backend._research_forward_observer = previous


def test_fixed_rotation_contains_252_requests_and_all_six_explicit_warmup_pairs():
    queries = [{"id": f"invented_{number}", "query": f"invented text {number}"} for number in range(14)]
    scheduled = list(native.schedule(queries))
    assert len(scheduled) == 252 and native.REPETITIONS == 3
    expected = [(arm, mode) for mode in ("dense", "hybrid") for arm in ("legacy", "coverage_control", "candidate")]
    for repetition in range(1, 4):
        for index, query in enumerate(queries):
            calls = [(arm, mode) for rep, value, arm, mode in scheduled if rep == repetition and value is query]
            offset = (index + repetition - 1) % 6
            assert calls == expected[offset:] + expected[:offset]
    assert len({(arm, retrieval) for _, _, arm, retrieval in scheduled}) == 6


def test_recording_embedder_forwards_original_query_observer_and_copies_result(tmp_path, monkeypatch):
    vector, observer, seen = np.arange(DIMENSIONS, dtype=np.float32), lambda event: None, []

    def original(self, query, **kwargs):
        seen.append((self, query, kwargs))
        return vector
    monkeypatch.setattr(QwenEmbedder, "embed_query", original)
    embedder = native.RecordingEmbedder(tmp_path / "absent-model", progress=False)
    actual = embedder.embed_query("invented text", forward_observer=observer)
    assert actual is vector and seen == [(embedder, "invented text", {"forward_observer": observer})]
    assert embedder.query_calls == 1 and embedder.last_query.tobytes() == vector.tobytes()
    vector[0] = -10
    assert embedder.last_query[0] == 0


def test_recording_embedder_forwards_clause_cancel_and_forward_observers(tmp_path, monkeypatch):
    vectors, observer, cancel, seen = np.zeros((2, DIMENSIONS), dtype=np.float32), lambda event: None, lambda: None, []

    def original(self, queries, **kwargs):
        seen.append((queries, kwargs))
        return vectors
    monkeypatch.setattr(QwenEmbedder, "embed_queries", original)
    embedder = native.RecordingEmbedder(tmp_path / "absent-model", progress=False)
    queries = ["invented clause one", "invented clause two"]
    assert embedder.embed_queries(queries, cancel_check=cancel, forward_observer=observer) is vectors
    assert seen == [(queries, {"cancel_check": cancel, "forward_observer": observer})]
    assert embedder.clause_query_calls == 1 and embedder.clause_query_texts == 2 and embedder.query_calls == 0


def test_recording_embedder_forwards_all_document_checkpoint_callbacks(tmp_path, monkeypatch):
    callbacks = {name: lambda *args: None for name in
                 ("cancel_check", "progress_callback", "batch_callback", "forward_observer")}
    vectors, seen, events = np.zeros((1, DIMENSIONS), dtype=np.float32), [], []
    callbacks["forward_observer"] = events.append
    event = {"status": "completed", "batch_input_count": 1}

    def original(self, texts, **kwargs):
        seen.append((texts, kwargs))
        kwargs["forward_observer"](event)
        return vectors
    monkeypatch.setattr(QwenEmbedder, "embed_documents", original)
    embedder = native.RecordingEmbedder(tmp_path / "absent-model", progress=False)
    assert embedder.embed_documents(["invented document"], **callbacks) is vectors
    assert seen[0][0] == ["invented document"] and embedder.document_calls == 1
    assert all(seen[0][1][name] is callbacks[name] for name in callbacks if name != "forward_observer")
    assert events == [event] and events[0] is event
    assert embedder.document_api_records[0]["forward_events"][0] is event
    assert embedder.document_api_records[0]["status"] == "completed"
    assert embedder.query_calls == embedder.clause_query_calls == 0


@pytest.mark.parametrize("method,argument,counter", [("embed_query", "invented text", "query_calls"),
    ("embed_queries", ["invented clause"], "clause_query_calls"),
    ("embed_documents", ["invented document"], "document_calls")])
def test_recording_embedder_counts_failed_api_attempts_without_hiding_original_error(tmp_path, monkeypatch, method, argument, counter):
    error = SearchCancelled("invented API cancellation")

    def original(*args, **kwargs):
        raise error
    monkeypatch.setattr(QwenEmbedder, method, original)
    embedder = native.RecordingEmbedder(tmp_path / "absent-model", progress=False)
    with pytest.raises(SearchCancelled) as caught:
        getattr(embedder, method)(argument)
    assert caught.value is error and getattr(embedder, counter) == 1


def query_record(text=QUERY):
    # Labels are deliberately unrelated to selection and enter reporting only.
    return {"id": "invented_query", "query": text, "kind": "absent", "targets": []}


@pytest.mark.parametrize("retrieval", ["dense", "hybrid"])
def test_actual_candidate_uses_executing_query_and_resident_vectors_once(fixture, monkeypatch, retrieval):
    _, backend, embedder = fixture
    backend.refresh()
    resident = backend._index
    actual_read, reads = resident.read_semantic_vectors, []

    def read(rows, **kwargs):
        result = actual_read(rows, **kwargs)
        reads.append((deepcopy(rows), deepcopy(kwargs["model_metadata"]), result))
        return result
    monkeypatch.setattr(resident, "read_semantic_vectors", read)
    before, documents = len(embedder.actual_forwards), embedder.document_calls
    result = native.measure(backend, embedder, query_record(), 1, "candidate", retrieval)
    captured = result["candidate_capture"]
    assert captured["calls"] == {"coverage_coalesce": 1, "semantic": 1, "role_guard": 1,
        "feature": 1, "current_v1": 1, "extra_retrieval": 0, "document_inference": 0, "backfill_after_guard": 0}
    assert len(reads) == len(result["resident_reads"]) == 1 and resident is backend._index
    assert reads[0][1] == embedder.metadata and set(reads[0][2]) == set(captured["features"]["windows"])
    for identifier, vector in reads[0][2].items():
        assert vector.dtype == np.float32
        assert np.asarray(captured["features"]["windows"][identifier]["vector"], dtype=np.float32).tobytes() == vector.tobytes()
    assert embedder.document_calls == documents and result["document_embedding_calls"] == 0
    assert embedder.actual_forwards[before] == [QUERY_PROMPT + QUERY]
    assert sorted(embedder.actual_forwards[before + 1]) == sorted([
        QUERY_PROMPT + "load alpha payload", QUERY_PROMPT + "render beta template"])
    assert len(embedder.actual_forwards) == before + 2
    provider = captured["provider_receipts"][0]
    assert provider["original_query_vector"]["reencoded"] is False
    assert provider["original_query_vector"]["origin"] == "executing_retrieval_query_vector"
    assert provider["resident_vector_reads"] == 1 and provider["document_inference_calls"] == 0
    assert result["query_embedding_calls"] == result["clause_query_calls"] == 1
    assert result["clause_query_texts"] == 2 and result["forward_counts"]["reported_total_model_forward_calls"] == 2
    assert captured["role_guard_receipt"]["accepted"] and captured["semantic_receipt"]["accepted"]
    assert native.source_view(result["response"]) == native.source_view(captured["v1_response"])
    assert result["compact_sha256"] != hashlib.sha256(native.context.serialize_context(
        native.context.compact_result(captured["reference_response"])).encode()).hexdigest()
    assert backend._research_postprocessor is None and backend._research_forward_observer is None


def test_full_guard_fallback_preserves_actual_coverage_operand(fixture):
    repo, backend, embedder = fixture
    (repo / "src/code.py").write_text("\n".join(
        f"def implementation_{number}(obj):\n    obj.value = {number}\n    return {number}\n" for number in range(1, 12)))
    backend.refresh()
    outcome = native.measure(backend, embedder, query_record(), 1, "candidate", "dense")
    captured = outcome["candidate_capture"]
    assert captured["semantic_receipt"]["accepted"] and len(captured["semantic_receipt"]["exchanges"]) == 2
    receipt = captured["role_guard_receipt"]
    assert not receipt["accepted"] and receipt["fallback_reason"] == "protected_role_row_changed"
    assert len(receipt["violated_ids"]) == 2
    assert native.source_view(outcome["response"]) == native.source_view(captured["reference_response"])
    assert native.source_view(outcome["response"]) != native.source_view(captured["v1_response"])
    assert outcome["guard_calls"] == captured["calls"]["role_guard"] == 1


def test_original_nonaxis_query_bits_remain_the_executing_feature_vector(fixture, monkeypatch):
    _, backend, embedder = fixture
    backend.refresh()
    original = np.zeros(DIMENSIONS, dtype=np.float32)
    original[:4] = [0.1, -0.0, 0.7, 0.9]
    actual = embedder._embed_batch

    def batch(ids):
        values = actual(ids)
        for position, tokens in enumerate(ids):
            if bytes(value - 1 for value in tokens).decode() == QUERY_PROMPT + QUERY:
                values[position] = original
        return values
    monkeypatch.setattr(embedder, "_embed_batch", batch)
    result = native.measure(backend, embedder, query_record(), 1, "candidate", "dense")
    captured = result["candidate_capture"]
    assert np.asarray(captured["features"]["facet_vectors"][0], dtype=np.float32).tobytes() == original.tobytes()
    assert result["query_vector_sha256"] == hashlib.sha256(original.tobytes()).hexdigest()
    assert captured["provider_receipts"][0]["original_query_vector"]["float32_le_sha256"] == result["query_vector_sha256"]


@pytest.mark.parametrize("arm", ["legacy", "coverage_control"])
def test_controls_execute_no_candidate_hooks_clauses_or_resident_feature_reads(fixture, monkeypatch, arm):
    _, backend, embedder = fixture
    backend.refresh()

    def forbidden(*args, **kwargs):
        pytest.fail("A control entered candidate-only feature or guard work")
    monkeypatch.setattr(backend._index, "read_semantic_vectors", forbidden)
    monkeypatch.setattr(native.role, "apply_role_guard", forbidden)
    monkeypatch.setattr(embedder, "embed_queries", forbidden)
    before = len(embedder.actual_forwards)
    result = native.measure(backend, embedder, query_record(), 1, arm, "dense")
    assert result["candidate_capture"] is None and result["resident_reads"] == [] and result["guard_calls"] == 0
    assert result["query_embedding_calls"] == 1 and result["clause_query_calls"] == result["clause_query_texts"] == 0
    assert len(embedder.actual_forwards) == before + 1
    assert result["forward_counts"]["reported_total_model_forward_calls"] == 1
    assert backend._research_postprocessor is None and backend._research_forward_observer is None


def test_repeated_candidates_encode_fresh_clauses_without_cross_request_cache(fixture):
    _, backend, embedder = fixture
    backend.refresh()
    before = len(embedder.actual_forwards)
    first = native.measure(backend, embedder, query_record(), 1, "candidate", "dense")
    second = native.measure(backend, embedder, query_record(), 2, "candidate", "dense")
    assert [batch for batch in embedder.actual_forwards[before:] if len(batch) == 2] == [
        [QUERY_PROMPT + "load alpha payload", QUERY_PROMPT + "render beta template"]] * 2
    assert first["query_vector_sha256"] == second["query_vector_sha256"]
    assert first["compact_sha256"] == second["compact_sha256"]
    assert first["clause_query_calls"] == second["clause_query_calls"] == 1


def test_whole_request_timer_includes_provider_v1_and_role_but_excludes_reporting(fixture, monkeypatch):
    from semantic_search import semantic_context, semantic_context_adapter

    _, backend, embedder = fixture
    backend.refresh()
    clock, phases = [10.0], []

    def advance(name, seconds):
        phases.append(name)
        clock[0] += seconds

    def forward(texts):
        advance("original_query" if len(texts) == 1 else "fresh_clauses", 2)
    embedder.before_forward = forward
    original_coalesce = semantic_context_adapter.coalesce_context
    original_read = backend._index.read_semantic_vectors
    original_v1 = semantic_context.semantic_final_context
    original_role = native.role.apply_role_guard
    original_receipt = native.receipt

    def coalesce(*args, **kwargs):
        advance("coverage", 3)
        return original_coalesce(*args, **kwargs)

    def read(*args, **kwargs):
        advance("resident_vectors", 11)
        return original_read(*args, **kwargs)

    def v1(*args, **kwargs):
        advance("v1", 5)
        return original_v1(*args, **kwargs)

    def role(*args, **kwargs):
        advance("role", 7)
        return original_role(*args, **kwargs)

    def report(*args, **kwargs):
        assert clock[0] == 40.0
        assert backend._research_postprocessor is None and backend._research_forward_observer is None
        advance("reporting_copy_drain_and_serialization", 100)
        return original_receipt(*args, **kwargs)
    monkeypatch.setattr(native.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(semantic_context_adapter, "coalesce_context", coalesce)
    monkeypatch.setattr(backend._index, "read_semantic_vectors", read)
    monkeypatch.setattr(semantic_context, "semantic_final_context", v1)
    monkeypatch.setattr(native.role, "apply_role_guard", role)
    monkeypatch.setattr(native, "receipt", report)
    result = native.measure(backend, embedder, query_record(), 1, "candidate", "dense")
    assert phases == ["original_query", "coverage", "resident_vectors", "fresh_clauses", "v1", "role",
                      "reporting_copy_drain_and_serialization"]
    assert result["elapsed_backend_s"] == 30.0 and clock[0] == 140.0
    assert result["request_clock"]["ended_perf_counter"] - result["request_clock"]["started_perf_counter"] == 30.0
    assert result["candidate_capture"]["phase_timings"] == {"coverage_context_s": 3.0,
        "live_feature_provider_s": 13.0, "prepared_feature_allocation_s": 5.0, "terminal_role_guard_s": 7.0}


@pytest.mark.parametrize("where", ["query", "clauses"])
def test_capture_retains_failed_terminal_forward_and_partial_provider_receipts(fixture, where):
    _, backend, embedder = fixture
    backend.refresh()
    error = RuntimeError("invented failed " + where)

    def before(texts):
        if where == "query" or len(texts) > 1:
            embedder.failure = error
    embedder.before_forward = before
    with native.capture(native.CANDIDATE, backend) as captured:
        with pytest.raises(RuntimeError) as caught:
            backend.search(QUERY, retrieval="dense", **native.SETTINGS)
    assert caught.value is error
    assert captured["external_forward_events"][-1]["status"] == ("failed" if where == "query" else "completed")
    if where == "clauses":
        failed = captured["semantic_events"][-1]["provider_receipts"][-1]
        assert failed["status"] == "failed" and failed["stage"] == "clause_encoding"
        assert failed["extra_query_work"]["reported_failed_model_forward_calls"] == 1
        assert failed["original_query_work"]["reported_completed_model_forward_calls"] == 1
        assert captured["resident_reads"][0]["returned_vectors"] is not None
    else:
        assert captured["semantic_events"] == [] and captured["resident_reads"] == []
    assert backend._research_postprocessor is None and backend._research_forward_observer is None


def test_capture_cancellation_after_original_query_preserves_completed_cost(fixture):
    _, backend, embedder = fixture
    backend.refresh()
    before = len(embedder.actual_forwards)
    with native.capture(native.CANDIDATE, backend) as captured:
        actual = backend._research_forward_observer

        def observe(event):
            actual(event)
            if event["status"] == "completed":
                backend.cancel("invented-cancel")
        backend._research_forward_observer = observe
        with pytest.raises(SearchCancelled):
            backend.search(QUERY, retrieval="dense", request_id="invented-cancel", **native.SETTINGS)
    assert [value["status"] for value in captured["external_forward_events"]] == ["started", "completed"]
    assert len(embedder.actual_forwards) == before + 1 and captured["semantic_events"] == []


def commit_fixture(root, message):
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", message], check=True, capture_output=True)
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def git_fixture(root):
    for arguments in (("init", "-q"), ("config", "user.name", "Invented native test"),
                      ("config", "user.email", "invented@example.invalid")):
        subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


def parent_fixture(tmp_path, monkeypatch):
    args = options(tmp_path)
    monkeypatch.setattr(native, "ROOT", tmp_path)
    git_fixture(tmp_path)
    producer = tmp_path / "invented-producer.py"
    producer.write_text("# invented source-only binding\n")
    initial = commit_fixture(tmp_path, "invented parent source freeze")
    folder = args.development_study
    replay, prepared = folder / "replay", folder / "prepared-v2"
    replay.mkdir(parents=True)
    prepared.mkdir()
    native.write(replay / "run-start.json", {"binding": {"freeze": {"commit": initial}, "execution_commit": initial}})
    (replay / "outcomes.jsonl.gz").write_bytes(gzip.compress(b'{"invented":true}\n', mtime=0))
    qualification = {"qualified": True,
        "against_controls": {arm: {name: [] for name in
            ("group_losses", "complete_losses", "single_file_first_hit_losses")} for arm in native.ARMS[:-1]},
        "additional_distinct_complete_multi_queries_vs_coverage": ["invented/new_complete_behavior"]}
    native.write(replay / "summary.json", {"schema": "role_context_study_v1", "mode": native.CANDIDATE,
        "status": "passed", "quality_gate": qualification,
        "artifact_sha256": {name: native.file_sha(replay / name) for name in ("run-start.json", "outcomes.jsonl.gz")}})
    native.write(prepared / "frozen-plan.json", {"parent_binding": {"sha256": {}},
        "source_sha256": {producer.name: native.file_sha(producer)}, "artifact_sha256": {}})
    native.write(folder / "independent-audit.json", {"schema": "role_context_audit_v1", "status": "passed",
        "checks_passed": True, "independently_recomputed_qualification": qualification,
        "verified_candidate_rows": 393, "exact_control_rows": 786, "retained_negative_references": 3734,
        "distinct_query_units": 201, "referenced_saved_v1_proposals": 393,
        "producer_freeze": {"commit": initial, "execution_commit": initial},
        **{name: 0 for name in ("model_calls", "retrieval_calls", "network_calls", "attrs_reads")}})
    publication = commit_fixture(tmp_path, "invented complete audited parent publication")
    monkeypatch.setattr(native, "PARENT_PUBLICATION", publication)
    return args


def test_actual_parent_gate_requires_committed_audit_and_control_proof(tmp_path, monkeypatch):
    args = parent_fixture(tmp_path, monkeypatch)
    binding = native.parent_binding(args)
    assert binding["qualification"]["qualified"] and len(binding["artifacts"]) == 6
    assert binding["publication_commit"] == native.PARENT_PUBLICATION
    (tmp_path / "invented-producer.py").write_text("# changed invented source\n")
    with pytest.raises(ValueError, match="Changed or uncommitted"):
        native.parent_binding(args)


@pytest.mark.parametrize("fault", ["unqualified", "loss", "missing_gain", "missing_saved_proposals", "attrs_read", "old_audit_schema"])
def test_invalid_parent_stops_before_fresh_source_dataset_tokenizer_or_model(tmp_path, monkeypatch, fault):
    args = parent_fixture(tmp_path, monkeypatch)
    path = args.development_study / "independent-audit.json"
    audit = native.read(path)
    if fault == "unqualified":
        audit["independently_recomputed_qualification"]["qualified"] = False
    elif fault == "loss":
        audit["independently_recomputed_qualification"]["against_controls"]["legacy"]["group_losses"] = [{"group": 1}]
    elif fault == "missing_gain":
        audit["independently_recomputed_qualification"]["additional_distinct_complete_multi_queries_vs_coverage"] = []
    elif fault == "missing_saved_proposals":
        audit["referenced_saved_v1_proposals"] = 392
    elif fault == "attrs_read":
        audit["attrs_reads"] = 1
    else:
        audit["schema"] = "an_older_independent_audit"
    path.write_bytes(native.canonical(audit))
    monkeypatch.setattr(native, "source_authority", lambda: None)
    monkeypatch.setattr(native.frozen, "_identity_after_parent", lambda *args: pytest.fail("fresh inputs read before parent gate"))
    monkeypatch.setattr(native.transport, "verify_runtime", lambda *args: pytest.fail("checkpoint/runtime read before parent gate"))
    with pytest.raises(ValueError):
        native.identity(args)
    assert not any(path.exists() for path in (args.dataset, args.corpus, args.model, args.tokenizer_cache, args.runtime_manifest))


def test_committed_new_native_sources_are_required_before_any_fresh_preparation(tmp_path, monkeypatch):
    args = options(tmp_path)
    seen = []
    monkeypatch.setattr(native, "parent_binding", lambda value: seen.append("parent") or {"invented": True})
    monkeypatch.setattr(native, "file_sha", lambda path: "a" * 64)

    def uncommitted(paths, *args):
        seen.append("native_source_freeze")
        assert set(paths) == set(native.NATIVE_SOURCES)
        raise ValueError("invented native source is not committed")
    monkeypatch.setattr(native, "committed", uncommitted)
    monkeypatch.setattr(native.frozen, "_identity_after_parent", lambda *args: pytest.fail("fresh source before native source freeze"))
    monkeypatch.setattr(native.transport, "verify_runtime", lambda *args: pytest.fail("model before native source freeze"))
    with pytest.raises(ValueError, match="not committed"):
        native.identity(args)
    assert seen == ["parent", "native_source_freeze"]


def test_other_checkout_import_is_rejected_before_parent_or_fresh_reads(tmp_path, monkeypatch):
    args = options(tmp_path)
    monkeypatch.setattr(native.role, "__file__", str(tmp_path / "other-checkout/research_role_context.py"))
    monkeypatch.setattr(native, "parent_binding", lambda *args: pytest.fail("source authority must precede all input reads"))
    with pytest.raises(ValueError, match="source authority"):
        native.identity(args)


def plan_fixture(tmp_path, monkeypatch):
    args = options(tmp_path)
    monkeypatch.setattr(native, "ROOT", tmp_path)
    git_fixture(tmp_path)
    code = tmp_path / "invented-native.py"
    code.write_text("# invented native source\n")
    source_commit = commit_fixture(tmp_path, "invented native source freeze")
    args.output.mkdir()
    native.write(args.output / "source-inventory.json", {"inventory": [], "definition_keys": {}, "graph_edges": []})
    binding = {"implementation_sha256": {code.name: native.file_sha(code)}, "development_parent": {"artifacts": {}},
        "definition_preflight": None, "native_source_freeze": {"commit": source_commit}}
    native.write(args.output / "plan.json", {"schema": native.SCHEMA, "version": 1, "binding": binding,
        "artifacts": {"source-inventory.json": native.file_sha(args.output / "source-inventory.json")}})
    audit = {"schema": "role_native_input_audit_v1", "status": "passed", "checks_passed": True,
        "plan_sha256": native.file_sha(args.output / "plan.json"), "execution_commit": source_commit,
        "source_inventory_sha256": native.file_sha(args.output / "source-inventory.json"),
        "native_source_freeze": binding["native_source_freeze"], "source_sha256": binding["implementation_sha256"],
        "new_native_outcomes_computed": 0, "source_only_calls": {name: 0 for name in
            ("model", "index", "retrieval", "selector", "postprocessor", "rule_activation", "gold_coverage_scoring")}}
    native.write(args.output / "input-audit.json", audit)
    freeze = commit_fixture(tmp_path, "invented source-only plan and independent input audit")
    return args, binding, freeze


def test_actual_git_plan_freeze_binds_input_audit_source_inventory_and_code(tmp_path, monkeypatch):
    args, binding, freeze = plan_fixture(tmp_path, monkeypatch)
    _, chain = native.verify_plan(args, binding)
    assert chain["commit"] == freeze and chain["bound_blob_count"] == 4
    (tmp_path / "invented-native.py").write_text("# altered source after native freeze\n")
    with pytest.raises(ValueError, match="Changed or uncommitted"):
        native.verify_plan(args, binding)


@pytest.mark.parametrize("fault", ["missing", "schema", "failed", "plan", "inventory", "code", "native_outcomes", "model_work"])
def test_independent_input_barrier_must_match_before_native_work(tmp_path, monkeypatch, fault):
    args, binding, _ = plan_fixture(tmp_path, monkeypatch)
    path = args.output / "input-audit.json"
    audit = native.read(path)
    if fault == "missing":
        path.unlink()
    else:
        if fault == "schema":
            audit["schema"] = "different_audit"
        elif fault == "failed":
            audit["checks_passed"] = False
        elif fault == "plan":
            audit["plan_sha256"] = "a" * 64
        elif fault == "inventory":
            audit["source_inventory_sha256"] = "a" * 64
        elif fault == "code":
            audit["source_sha256"] = {}
        elif fault == "native_outcomes":
            audit["new_native_outcomes_computed"] = 1
        else:
            audit["source_only_calls"]["model"] = 1
        path.write_bytes(native.canonical(audit))
    with pytest.raises((ValueError, FileNotFoundError)):
        native.verify_plan(args, binding)


def test_source_only_freeze_claims_new_directory_and_syncs_parent_before_writing(tmp_path, monkeypatch):
    args, events = options(tmp_path), []
    original_write, original_sync = native.write, native.sync
    monkeypatch.setattr(native, "identity", lambda *args: ({"queries": [query_record()]}, {}, {"invented": True}, {}))
    monkeypatch.setattr(native, "git", lambda *args: "a" * 40)

    def sync(path):
        events.append(("sync", path))
        return original_sync(path)

    def write(path, value):
        assert events[0] == ("sync", args.output.parent)
        events.append(("write", path.name))
        return original_write(path, value)
    monkeypatch.setattr(native, "sync", sync)
    monkeypatch.setattr(native, "write", write)
    native.freeze(args)
    assert native.read(args.output / "plan.json")["expected_requests"] == 252
    monkeypatch.setattr(native, "identity", lambda *args: pytest.fail("Existing freeze must be rejected before input reads"))
    with pytest.raises(ValueError, match="new native plan directory"):
        native.freeze(args)


@pytest.mark.parametrize("failure", ["query", "clause", "report"])
def test_measure_failure_snapshots_partial_work_after_timer_and_preserves_error(fixture, monkeypatch, failure):
    _, backend, embedder = fixture
    backend.refresh()
    error, snapshots = SearchCancelled("invented " + failure + " cancellation"), []
    if failure == "report":
        monkeypatch.setattr(native, "receipt", lambda *a, **kw: (_ for _ in ()).throw(error))
    else:
        def before(texts):
            if failure == "query" or len(texts) > 1:
                embedder.failure = error
        embedder.before_forward = before

    def snapshot(value):
        assert backend._research_postprocessor is None and backend._research_forward_observer is None
        snapshots.append(value)
    with pytest.raises(SearchCancelled) as caught:
        native.measure(backend, embedder, query_record(), 1, "candidate", "dense", on_failure=snapshot)
    assert caught.value is error and len(snapshots) == 1
    partial = snapshots[0]["partial_capture"]
    assert snapshots[0]["request_clock"]["ended_perf_counter"] >= snapshots[0]["request_clock"]["started_perf_counter"]
    terminal = partial["external_forward_events"][-1]
    assert terminal["status"] == ("failed" if failure == "query" else "completed")
    if failure == "clause":
        assert partial["semantic_events"][-1]["provider_receipts"][0]["extra_query_work"]["reported_failed_model_forward_calls"] == 1
    elif failure == "report":
        assert partial["role_guard_receipt"] is not None and partial["semantic_capture"] is not None


def test_failed_partial_recorder_cannot_replace_original_model_error(fixture):
    _, backend, embedder = fixture
    backend.refresh()
    error, recorder_error = RuntimeError("invented model error"), OSError("invented recorder error")
    embedder.failure = error

    def recorder(value):
        raise recorder_error
    with pytest.raises(RuntimeError) as caught:
        native.measure(backend, embedder, query_record(), 1, "candidate", "dense", on_failure=recorder)
    assert caught.value is error and caught.value.__cause__ is recorder_error


def test_resident_lookup_failure_counts_entered_provider_and_coverage_not_unrun_allocator(fixture, monkeypatch):
    _, backend, embedder = fixture
    backend.refresh()
    error, captured = RuntimeError("invented missing resident vector"), []

    def missing(*args, **kwargs):
        raise error
    monkeypatch.setattr(backend._index, "read_semantic_vectors", missing)
    before = len(embedder.actual_forwards)
    with pytest.raises(RuntimeError) as caught:
        native.measure(backend, embedder, query_record(), 1, "candidate", "dense", on_failure=captured.append)
    assert caught.value is error and len(captured) == 1
    partial = captured[0]["partial_capture"]
    assert partial["coverage_coalesce_calls"] == partial["feature_calls"] == partial["semantic_postprocessor_calls"] == 1
    assert partial["semantic_calls"] == partial["role_guard_calls"] == 0
    assert partial["reference_response"] is not None and partial["proposal_response"] is None
    assert len(embedder.actual_forwards) == before + 1
    assert captured[0]["attempted_api_calls"] == {"query": 1, "clauses": 0, "clause_texts": 0, "documents": 0}
    assert partial["resident_reads"][0]["returned_vectors"] is None
    assert partial["phase_timings"]["coverage_context_s"] >= 0
    assert partial["phase_timings"]["live_feature_provider_s"] >= 0
    assert partial["phase_timings"]["prepared_feature_allocation_s"] == partial["phase_timings"]["terminal_role_guard_s"] == 0


def loop_fixture(tmp_path, monkeypatch, *, protected=False, fail_after=None, close_error=None):
    """Run all real CPU code on invented source; replace only external gates/GPU."""
    args = options(tmp_path)
    args.corpus = tmp_path / "invented-corpus"
    source_dir = args.corpus / "src"
    source_dir.mkdir(parents=True)
    source = "\n".join(f"def implementation_{number}({'obj' if protected else ''}):\n"
        + (f"    obj.value = {number}\n" if protected else "") + f"    return {number}\n" for number in range(1, 12))
    (source_dir / "code.py").write_text(source)
    # All actual index/database work is in this invented test's tmpdir.
    probe_model = InventedEmbedder(args.model, progress=False)
    with closing(SearchBackend(args.corpus, "src", tmp_path / "inventory.sqlite3", embedder=probe_model)) as probe:
        probe.refresh()
        inventory = {"inventory": [asdict(item) for item in probe._index.snippets],
            "definition_keys": deepcopy(probe._definition_keys),
            "graph_edges": [{"caller": list(caller), "targets": [list(target) for target in targets]}
                for caller, targets in sorted(probe._references.edges.items())]}
    by_symbol = {item["symbol"]: item for item in inventory["inventory"]}

    def target(symbol):
        value = by_symbol[symbol]
        return {"path": value["path"], "symbol": symbol, "evidence_lines": [value["start_line"], value["end_line"]],
                "evidence_sha256": value["content_hash"]}
    queries = []
    for number in range(14):
        value = {"id": f"invented_query_{number}", "query": QUERY + f" request {number}",
                 "kind": "absent" if number >= 12 else "present"}
        if number < 8:
            value["target_groups"] = [[target("implementation_1")], [target("implementation_9")]]
        else:
            value["targets"] = [target("implementation_1")] if number < 12 else []
        queries.append(value)
    data, sources = {"source_root": "src", "queries": queries}, {"src/code.py": source}
    args.output.mkdir()
    args.tokenizer_cache.mkdir()
    ranks = b"invented tokenizer bytes; real tokenizer initialized before this loader seam"
    (args.tokenizer_cache / native.base.replay.CACHE_KEY).write_bytes(ranks)
    native.context.encoding()
    monkeypatch.setattr(native.base.replay, "CACHE_SHA256", hashlib.sha256(ranks).hexdigest())
    binding = {"gate": native.GATE, "runtime_manifest": {"model_binding": {"metadata": probe_model.metadata}},
        "native_source_freeze": {"commit": "c" * 40, "committed_at_unix": 0, "source_sha256": {}}}
    plan = {"schema": native.SCHEMA, "binding": binding}
    native.write(args.output / "plan.json", plan)
    chain = {"commit": "a" * 40, "committed_at_unix": 0, "bound_blob_count": 1}
    monkeypatch.setattr(native, "identity", lambda value: (data, sources, inventory, deepcopy(binding)))
    monkeypatch.setattr(native, "verify_plan", lambda *args: (deepcopy(plan), deepcopy(chain)))
    monkeypatch.setattr(native, "ROOT", tmp_path)
    monkeypatch.setattr(native, "git", lambda *args: "b" * 40)
    monkeypatch.setattr(native, "machine_state", lambda: {"classification": "invented CPU machine state"})
    monkeypatch.setattr(native, "RecordingEmbedder", InventedEmbedder)
    lease_events, backends, models = [], [], []

    @contextmanager
    def lease(root):
        receipt = {"acquired_at": native.utc(), "released_at": None, "path": str(tmp_path / "invented-gpu.lock")}
        lease_events.append("acquired")
        try:
            yield receipt
        finally:
            receipt["released_at"] = native.utc()
            lease_events.append("released")
    monkeypatch.setattr(native.transport, "gpu_lease", lease)

    class Backend(SearchBackend):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.attempted_fixture_calls = 0
            self.closed_fixture = False

        def search(self, *args, **kwargs):
            self.attempted_fixture_calls += 1
            if fail_after is not None and self.attempted_fixture_calls == 6 + fail_after:
                raise failure
            return super().search(*args, **kwargs)

        def close(self):
            self.closed_fixture = True
            super().close()
            if close_error is not None:
                raise close_error
    failure = SearchCancelled("invented interrupted measured request")

    def backend(value, data, db, embedder):
        model = embedder
        created = Backend(value.corpus, data["source_root"], db, embedder=model)
        backends.append(created)
        models.append(model)
        return created
    monkeypatch.setattr(native, "make_backend", backend)
    return SimpleNamespace(args=args, data=data, sources=sources, inventory=inventory,
        backends=backends, models=models, lease_events=lease_events, failure=failure)


def ledger_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


@pytest.mark.parametrize("protected", [False, True])
def test_complete_252_request_loop_has_six_separate_warmups_and_exact_final_hashes(tmp_path, monkeypatch, protected):
    fixture = loop_fixture(tmp_path, monkeypatch, protected=protected)
    summary = native.run(fixture.args)
    attempt = fixture.args.output / "attempt"
    rows, warmups = ledger_rows(attempt / "searches.jsonl.gz"), ledger_rows(attempt / "warmups.jsonl.gz")
    assert len(rows) == summary["requests"] == summary["measured_query_embeddings"] == 252
    assert len(warmups) == summary["setup"]["warmup_calls"] == summary["setup"]["query_api_calls"] == 6
    assert summary["measured_document_embedding_calls"] == 0
    assert summary["measured_clause_query_calls"] == 84 and summary["measured_clause_encoded_texts"] == 168
    assert fixture.backends[0].attempted_fixture_calls == 258 and fixture.backends[0].closed_fixture
    assert fixture.lease_events == ["acquired", "released"]
    expected = [(rep, value["id"], arm, mode) for rep, value, arm, mode in native.schedule(fixture.data["queries"])]
    assert [(value["repetition"], value["query_id"], value["arm"], value["retrieval"]) for value in rows] == expected
    assert {(value["arm"], value["retrieval"]) for value in warmups} == {
        (arm, mode) for arm in ("legacy", "coverage_control", "candidate") for mode in ("dense", "hybrid")}
    for value in rows:
        assert value["query_embedding_calls"] == 1 and value["document_embedding_calls"] == 0
        assert value["actual_compact_tokens"] == value["response"]["token_usage"]["tokens"] <= 4096
        assert value["compact_payload"] == native.context.serialize_context(native.context.compact_result(value["response"]))
        assert hashlib.sha256(value["compact_payload"].encode()).hexdigest() == value["compact_sha256"]
        if value["arm"] == "candidate":
            assert value["candidate_capture"]["role_guard_receipt"]["accepted"] is (not protected)
            assert value["clause_query_calls"] == 1 and value["clause_query_texts"] == 2
        else:
            assert value["candidate_capture"] is None and value["clause_query_calls"] == 0
    audit = importlib.import_module("audit_role_native")
    independent_fixture = {"inventory": {item["id"]: item for item in fixture.inventory["inventory"]},
        "definition_keys": json.loads(json.dumps(fixture.inventory["definition_keys"])), "sources": fixture.sources,
        "graph_edges": {tuple(item["caller"]): [tuple(target) for target in item["targets"]]
                        for item in fixture.inventory["graph_edges"]}}
    queries = {value["id"]: value for value in fixture.data["queries"]}
    # Reconstruct all six arms/modes for one invented query, then independently
    # check the entire repeated schedule and every quality/timing aggregate.
    for value in rows[:6]:
        audit.verify_trace(value, independent_fixture, native.context.encoding(),
            query=queries[value["query_id"]]["query"], scope=native.context.indexed_scope("src"),
            model_metadata=fixture.models[0].metadata)
    audit.verify_repeats(rows, fixture.data["queries"])
    assert audit.native_metrics(rows) == summary["metrics"]
    for name, digest in summary["artifact_sha256"].items():
        assert hashlib.sha256((attempt / name).read_bytes()).hexdigest() == digest
    assert native.read(attempt / "gpu-lease-released.json")["released_at"] is not None
    assert not (attempt / "failure.json").exists()
    with pytest.raises(ValueError, match="no overwrite, resume or retry"):
        native.run(fixture.args)


@pytest.mark.parametrize("close_also_fails", [False, True])
def test_partial_cancel_preserves_completed_gzip_rows_and_original_error(tmp_path, monkeypatch, close_also_fails):
    close_error = OSError("invented close failure") if close_also_fails else None
    fixture = loop_fixture(tmp_path, monkeypatch, fail_after=2, close_error=close_error)
    with pytest.raises(SearchCancelled) as caught:
        native.run(fixture.args)
    assert caught.value is fixture.failure
    if close_also_fails:
        assert caught.value.__cause__ is close_error
    attempt = fixture.args.output / "attempt"
    rows = ledger_rows(attempt / "searches.jsonl.gz")
    assert len(rows) == 1 and len(ledger_rows(attempt / "warmups.jsonl.gz")) == 6
    failure = native.read(attempt / "failure.json")
    assert failure["completed_requests"] == 1 and failure["attempted_requests"] == 2 and not failure["resume_allowed"]
    partial = native.read(attempt / "request-failure.json")
    assert partial["error_type"] == "SearchCancelled" and partial["partial_capture"]["external_forward_events"] == []
    assert fixture.backends[0].closed_fixture and fixture.lease_events == ["acquired", "released"]
    assert native.read(attempt / "gpu-lease-released.json")["released_at"] is not None
    for name, digest in failure["artifacts"].items():
        assert hashlib.sha256((attempt / name).read_bytes()).hexdigest() == digest
    assert not (attempt / "summary.json").exists()
    with pytest.raises(ValueError, match="no overwrite, resume or retry"):
        native.run(fixture.args)


def test_attempt_claim_parent_and_every_completed_row_are_synced_before_use(tmp_path, monkeypatch):
    fixture = loop_fixture(tmp_path, monkeypatch, fail_after=2)
    original_sync, original_fsync, original_ledger = native.sync, native.os.fsync, native.transport.Ledger
    parent_synced, synced, appended, finalized = [], [], [], []

    def sync(path):
        original_sync(path)
        parent_synced.append(path)

    def fsync(descriptor):
        original_fsync(descriptor)
        synced.append(descriptor)

    class Ledger(original_ledger):
        def __init__(self, path, **kwargs):
            assert parent_synced == [fixture.args.output]
            super().__init__(path, **kwargs)

        def append(self, value):
            descriptor, before = self.raw.fileno(), len(synced)
            super().append(value)
            assert descriptor in synced[before:]
            appended.append((Path(self.raw.name).name, value["query_id"]))

        def close(self):
            if self.closed:
                return super().close()
            path, descriptor, before = Path(self.raw.name), self.raw.fileno(), len(synced)
            super().close()
            assert descriptor in synced[before:] and path.is_file()
            # Decompression validates the finalized trailer and CRC before any
            # failure receipt hashes the compressed bytes.
            assert len(gzip.decompress(path.read_bytes()).splitlines()) == len(self.rows)
            finalized.append(path.name)
    monkeypatch.setattr(native, "sync", sync)
    monkeypatch.setattr(native.os, "fsync", fsync)
    monkeypatch.setattr(native.transport, "Ledger", Ledger)
    with pytest.raises(SearchCancelled):
        native.run(fixture.args)
    assert len(appended) == 7 and set(finalized) == {"searches.jsonl.gz", "warmups.jsonl.gz"}


def test_cold_setup_failure_records_failed_document_cost_and_releases_resources(tmp_path, monkeypatch):
    fixture = loop_fixture(tmp_path, monkeypatch)
    make_backend, error = native.make_backend, RuntimeError("invented setup model failure")

    def failing_backend(*args, **kwargs):
        backend = make_backend(*args, **kwargs)
        backend._get_embedder().failure = error
        return backend
    monkeypatch.setattr(native, "make_backend", failing_backend)
    with pytest.raises(RuntimeError) as caught:
        native.run(fixture.args)
    assert caught.value is error
    attempt = fixture.args.output / "attempt"
    failure, setup = native.read(attempt / "failure.json"), native.read(attempt / "setup-partial.json")
    assert failure["phase"] == "setup" and failure["completed_requests"] == failure["attempted_requests"] == 0
    assert setup["document_api_calls"] == 1 and setup["query_api_calls"] == 0
    assert setup["document_calls"][0]["status"] == "failed"
    assert [event["status"] for event in setup["document_calls"][0]["forward_events"]] == ["started", "failed"]
    assert ledger_rows(attempt / "searches.jsonl.gz") == ledger_rows(attempt / "warmups.jsonl.gz") == []
    assert fixture.backends[0].closed_fixture and fixture.lease_events == ["acquired", "released"]


def test_failure_receipt_write_error_keeps_original_cancellation_and_claimed_attempt(tmp_path, monkeypatch):
    fixture = loop_fixture(tmp_path, monkeypatch, fail_after=1)
    original_write, recorder_error = native.write, OSError("invented disk failure recording failure")

    def write(path, value):
        if path.name == "failure.json":
            raise recorder_error
        return original_write(path, value)
    monkeypatch.setattr(native, "write", write)
    with pytest.raises(SearchCancelled) as caught:
        native.run(fixture.args)
    assert caught.value is fixture.failure and caught.value.__cause__ is recorder_error
    attempt = fixture.args.output / "attempt"
    assert ledger_rows(attempt / "searches.jsonl.gz") == [] and len(ledger_rows(attempt / "warmups.jsonl.gz")) == 6
    assert (attempt / "request-failure.json").is_file() and fixture.lease_events == ["acquired", "released"]
    with pytest.raises(ValueError, match="no overwrite, resume or retry"):
        native.run(fixture.args)
