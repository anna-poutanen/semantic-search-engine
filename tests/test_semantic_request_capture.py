"""One-request artifact capture with invented CPU vectors and temporary source."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import numpy as np
import pytest

from semantic_search.backend import SearchBackend
from semantic_search.cancellation import SearchCancelled
from semantic_search.context import compact_result, encoding, serialize_context
from semantic_search.embeddings import DIMENSIONS, QUERY_PROMPT, QwenEmbedder


PATH = Path(__file__).resolve().parents[1] / "scripts/capture_semantic_request.py"
SPEC = importlib.util.spec_from_file_location("semantic_capture_test_subject", PATH)
capture_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_module)
SemanticRequestCapture = capture_module.SemanticRequestCapture
QUERY = "load alpha payload and render beta template"
KWARGS = {"query": QUERY, "max_results": 8, "source_budget": 12000, "token_budget": 4096}
BINDINGS = {"classification": "invented CPU fixture; not real Qwen or benchmark evidence",
            "trusted_source_control_bindings": False}


class InventedTokenizer:
    def __call__(self, texts, **kwargs):
        return {"input_ids": [[value + 1 for value in text.encode()] for text in texts]}


class InventedEmbedder(QwenEmbedder):
    def __init__(self):
        super().__init__(progress=False)
        self._tokenizer = InventedTokenizer()
        self.actual_forwards = []
        self.before_forward = None
        self.fail_if = lambda texts: False
        self.error = RuntimeError("invented model failed")

    @property
    def metadata(self):
        return {**super().metadata, "synthetic_fixture": "Invented CPU outputs, not a Qwen checkpoint"}

    def _ensure_loaded(self):
        pass

    def _embed_batch(self, ids):
        texts = [bytes(value - 1 for value in row).decode() for row in ids]
        self.actual_forwards.append(texts)
        if self.before_forward is not None:
            self.before_forward()
        if self.fail_if(texts):
            raise self.error
        vectors = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for position, text in enumerate(texts):
            axis = (1 if text == QUERY_PROMPT + "load alpha payload" or "def invented_9(" in text
                    else 2 if text == QUERY_PROMPT + "render beta template" or "def invented_10(" in text
                    else 0)
            vectors[position, axis] = 1
        return vectors


@pytest.fixture(autouse=True)
def forbid_real_models_and_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("This test permits invented CPU fixtures only")

    monkeypatch.setattr(QwenEmbedder, "_ensure_loaded", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def fixture(tmp_path):
    repo = tmp_path / "invented-repository"
    (repo / "src").mkdir(parents=True)
    (repo / "src/code.py").write_text("\n".join(
        f"def invented_{number}():\n    return {number}\n" for number in range(1, 12)))
    embedder = InventedEmbedder()
    with closing(SearchBackend(repo, "src", tmp_path / "invented.sqlite3", embedder=embedder)) as backend:
        yield backend, embedder, tmp_path / "attempt"


def configure(fixture, *, warm=False):
    backend, embedder, directory = fixture
    if warm:
        backend.refresh()
    capture = SemanticRequestCapture(directory, bindings=BINDINGS)
    backend._research_postprocessor = capture.postprocessor
    backend._research_forward_observer = capture.forward_observer
    return capture, backend, embedder, directory


def load(directory, name):
    return json.loads((directory / name).read_bytes())


def test_warm_success_persists_audited_actual_envelope_and_source_bindings(fixture):
    capture, backend, embedder, directory = configure(fixture, warm=True)
    before = len(embedder.actual_forwards)

    def require_pre_request_files():
        assert (directory / "manifest.json").is_file()
        assert (directory / "request.json").is_file()
        assert not (directory / "record.json").exists()

    embedder.before_forward = require_pre_request_files
    response = capture.run(backend, **KWARGS)
    record, manifest, request, audit = [load(directory, name) for name in
        ("record.json", "manifest.json", "request.json", "audit.json")]
    assert record["response"] == response
    payload = serialize_context(compact_result(response))
    assert record["compact_payload"] == payload
    assert record["compact_sha256"] == hashlib.sha256(payload.encode()).hexdigest()
    assert record["actual_compact_tokens"] == len(encoding().encode(payload, disallowed_special=()))
    assert record["limits"] == request["limits"] == {key: KWARGS[key] for key in capture_module.LIMITS}
    assert record["provider_receipts"][0]["original_query_vector"]["reencoded"] is False
    assert record["forward_counts"]["reported_request_document_inference_calls"] == 0
    assert record["forward_counts"]["reported_total_model_forward_calls"] == 2
    assert len(embedder.actual_forwards) == before + 2
    assert record["forward_counts"]["receipt_coverage_complete"]
    assert manifest["classification"] == BINDINGS["classification"]
    assert all(value is False for value in manifest["claims"].values())
    for filename in ("backend.py", "store.py", "embeddings.py", "overlays.py", "semantic_provider.py",
                     "semantic_features.py", "semantic_context.py", "semantic_context_adapter.py"):
        relative = "src/semantic_search/" + filename
        assert manifest["source_sha256"][relative] == hashlib.sha256(
            (PATH.parents[1] / relative).read_bytes()).hexdigest()
    assert "scripts/audit_semantic_context.py" in manifest["source_sha256"]
    assert "scripts/capture_semantic_request.py" in manifest["source_sha256"]
    assert audit["status"] == "passed" and audit["json_roundtrip_audit"]
    assert audit["record_sha256"] == hashlib.sha256((directory / "record.json").read_bytes()).hexdigest()
    assert not (directory / "failure.json").exists()
    assert all(isinstance(value, list) for value in record["definition_keys"].values())


def test_cold_document_costs_are_independent_of_provider_zero_counter(fixture):
    capture, backend, embedder, directory = configure(fixture)
    capture.run(backend, **KWARGS)
    record = load(directory, "record.json")
    counts = record["forward_counts"]
    assert record["provider_receipts"][0]["document_inference_calls"] == 0
    assert counts["documents"]["reported_model_forward_calls"] == 3
    assert counts["documents"]["reported_encoded_texts"] == 11
    assert counts["retrieval_query"]["reported_model_forward_calls"] == 1
    assert counts["extra_queries"]["reported_model_forward_calls"] == 1
    assert counts["reported_total_model_forward_calls"] == len(embedder.actual_forwards) == 5


def test_lexical_only_has_no_query_or_document_model_receipts(fixture):
    capture, backend, embedder, directory = configure(fixture)
    capture.run(backend, **KWARGS, retrieval="bm25")
    record = load(directory, "record.json")
    assert record["provider_receipts"][0]["reason"] == "lexical_only"
    assert record["forward_counts"]["reported_total_model_forward_calls"] == 0
    assert embedder.actual_forwards == []


def test_ineligible_query_still_counts_original_query_forward(fixture):
    capture, backend, _, directory = configure(fixture, warm=True)
    capture.run(backend, **{**KWARGS, "query": "find the alpha implementation"})
    record = load(directory, "record.json")
    assert record["provider_receipts"] == []
    assert record["forward_counts"]["retrieval_query"]["reported_model_forward_calls"] == 1
    assert record["forward_counts"]["extra_queries"]["reported_model_forward_calls"] == 0


@pytest.mark.parametrize("existing", ["directory", "file"])
def test_existing_attempt_path_is_never_reused_or_overwritten(fixture, existing):
    capture, backend, embedder, directory = configure(fixture)
    if existing == "directory":
        directory.mkdir()
        (directory / "untouched").write_text("keep this")
    else:
        directory.write_text("keep this")
    with pytest.raises(FileExistsError):
        capture.run(backend, **KWARGS)
    assert embedder.actual_forwards == []
    assert (directory / "untouched" if existing == "directory" else directory).read_text() == "keep this"


def test_successful_attempt_cannot_run_a_second_time(fixture):
    capture, backend, embedder, directory = configure(fixture, warm=True)
    capture.run(backend, **KWARGS)
    before = len(embedder.actual_forwards)
    original = (directory / "record.json").read_bytes()
    with pytest.raises(RuntimeError, match="only once"):
        capture.run(backend, **KWARGS)
    assert len(embedder.actual_forwards) == before and (directory / "record.json").read_bytes() == original


@pytest.mark.parametrize("hook", ["_research_postprocessor", "_research_forward_observer"])
def test_hook_identity_mismatch_fails_before_model_or_directory_creation(fixture, hook):
    capture, backend, embedder, directory = configure(fixture)
    setattr(backend, hook, lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="exact stable"):
        capture.run(backend, **KWARGS)
    assert not directory.exists() and embedder.actual_forwards == []


@pytest.mark.parametrize("bindings", [None, [], {1: "key"}, {"tuple": ()}, {"bad": float("nan")},
                                      {"bad": float("inf")}, {"bad": object()}])
def test_bindings_must_be_finite_literal_json(tmp_path, bindings):
    with pytest.raises(ValueError, match="finite literal JSON"):
        SemanticRequestCapture(tmp_path / "not-created", bindings=bindings)
    assert not (tmp_path / "not-created").exists()


def test_supplied_bindings_are_copied_and_unqualified_default_is_explicit(fixture):
    backend, _, directory = fixture
    bindings = {"note": ["original"]}
    capture = SemanticRequestCapture(directory, bindings=bindings)
    bindings["note"][0] = "changed"
    backend._research_postprocessor = capture.postprocessor
    backend._research_forward_observer = capture.forward_observer
    capture.run(backend, **KWARGS)
    manifest = load(directory, "manifest.json")
    assert manifest["bindings"] == {"note": ["original"]}
    assert manifest["classification"] == "unqualified_one_request_capture"


def test_invalid_request_json_fails_before_model_or_directory_creation(fixture):
    capture, backend, embedder, directory = configure(fixture)
    with pytest.raises(ValueError, match="search arguments"):
        capture.run(backend, **{**KWARGS, "source_budget": float("nan")})
    assert not directory.exists() and embedder.actual_forwards == []


@pytest.mark.parametrize("purpose", ["documents", "retrieval_query", "clauses"])
def test_model_failures_preserve_original_exception_and_partial_receipts(fixture, purpose):
    capture, backend, embedder, directory = configure(fixture, warm=purpose != "documents")
    if purpose == "documents":
        embedder.fail_if = lambda texts: any(text.startswith("Path:") for text in texts)
    elif purpose == "retrieval_query":
        embedder.fail_if = lambda texts: texts == [QUERY_PROMPT + QUERY]
    else:
        embedder.fail_if = lambda texts: any(text == QUERY_PROMPT + "load alpha payload" for text in texts)
    with pytest.raises(RuntimeError) as caught:
        capture.run(backend, **KWARGS)
    assert caught.value is embedder.error
    failure = load(directory, "failure.json")
    assert failure["phase"] == "backend_search" and failure["error_type"] == "builtins.RuntimeError"
    assert load(directory, "request.json")["search_kwargs"]["query"] == QUERY
    key = "extra_queries" if purpose == "clauses" else purpose
    assert failure["forward_counts"][key]["failed_forward_calls"] == 1
    if purpose == "clauses":
        receipt = failure["provider_receipts"][0]
        assert receipt["status"] == "failed"
        assert receipt["extra_query_forward_events"][-1]["status"] == "failed"
        assert receipt["original_query_forward_events"][-1]["status"] == "completed"
        assert failure["forward_counts"]["reported_total_model_forward_calls"] == 2
    assert not (directory / "audit.json").exists()


def test_cancellation_after_query_retains_completed_work_without_provider_capture(fixture):
    capture, backend, _, directory = configure(fixture, warm=True)

    class CancelEvents(list):
        def append(self, event):
            super().append(event)
            if event.get("purpose") == "retrieval_query" and event.get("status") == "completed":
                backend.cancel("capture-cancel")

    capture._forwards = CancelEvents()
    with pytest.raises(SearchCancelled):
        capture.run(backend, **KWARGS, request_id="capture-cancel")
    failure = load(directory, "failure.json")
    assert failure["captured_events"] == []
    assert failure["forward_counts"]["retrieval_query"]["completed_forward_calls"] == 1
    assert failure["forward_counts"]["extra_queries"]["reported_model_forward_calls"] == 0


def test_started_observer_failure_does_not_claim_an_actual_forward(fixture):
    capture, backend, embedder, directory = configure(fixture, warm=True)
    before = len(embedder.actual_forwards)
    original = RuntimeError("invented recorder failure before model call")

    class FailedEvents(list):
        def append(self, event):
            super().append(event)
            raise original

    capture._forwards = FailedEvents()
    with pytest.raises(RuntimeError) as caught:
        capture.run(backend, **KWARGS)
    assert caught.value is original and len(embedder.actual_forwards) == before
    counts = load(directory, "failure.json")["forward_counts"]
    assert counts["reported_total_model_forward_calls"] == 0
    assert counts["retrieval_query"]["started_forward_calls"] == 1
    assert not counts["receipt_coverage_complete"]


def test_error_after_postprocessor_keeps_the_completed_capture(fixture, monkeypatch):
    capture, backend, _, directory = configure(fixture, warm=True)
    actual_search = backend.search
    original = RuntimeError("invented final source check failed")

    def search(query, *, max_results=8, source_budget=12000, token_budget=4096, **kwargs):
        actual_search(query, max_results=max_results, source_budget=source_budget, token_budget=token_budget, **kwargs)
        raise original

    monkeypatch.setattr(backend, "search", search)
    with pytest.raises(RuntimeError) as caught:
        capture.run(backend, **KWARGS)
    assert caught.value is original
    failure = load(directory, "failure.json")
    assert failure["captured_events"][0]["semantic_receipt"]
    assert failure["provider_receipts"][0]["status"] == "active"
    assert failure["forward_counts"]["reported_total_model_forward_calls"] == 2


def test_independent_audit_failure_persists_partial_record(fixture, monkeypatch):
    capture, backend, _, directory = configure(fixture, warm=True)
    auditor = capture_module._audit_module()
    original = ValueError("invented independent audit failure")

    def fail(*args, **kwargs):
        raise original

    monkeypatch.setattr(auditor, "verify_record", fail)
    monkeypatch.setattr(capture_module, "_audit_module", lambda: auditor)
    with pytest.raises(ValueError) as caught:
        capture.run(backend, **KWARGS)
    assert caught.value is original
    failure = load(directory, "failure.json")
    assert failure["phase"] == "independent_audit" and failure["captured_events"]
    assert not (directory / "audit.json").exists()


def test_serialization_failure_persists_explicit_unsupported_value_marker(fixture):
    capture, backend, _, directory = configure(fixture, warm=True)

    class UnserializableRecords(list):
        def append(self, record):
            record["unsupported_fixture"] = object()
            super().append(record)

    capture._records = UnserializableRecords()
    with pytest.raises(TypeError):
        capture.run(backend, **KWARGS)
    failure = load(directory, "failure.json")
    assert failure["phase"] == "json_roundtrip"
    assert failure["captured_events"][0]["unsupported_fixture"] == {
        "capture_unserializable_type": "builtins.object"}
    assert not (directory / "record.json").exists()


def test_actual_final_response_is_bound_before_independent_audit(fixture, monkeypatch):
    capture, backend, _, directory = configure(fixture, warm=True)
    actual_search = backend.search

    def search(query, *, max_results=8, source_budget=12000, token_budget=4096, **kwargs):
        response = deepcopy(actual_search(query, max_results=max_results, source_budget=source_budget,
                                        token_budget=token_budget, **kwargs))
        response["results"][0]["symbol"] = "fabricated_final_symbol"
        return response

    monkeypatch.setattr(backend, "search", search)
    with pytest.raises(ValueError, match="actual final backend response"):
        capture.run(backend, **KWARGS)
    assert load(directory, "failure.json")["phase"] == "final_response_binding"


def test_result_affecting_source_drift_fails_after_audit(fixture, monkeypatch):
    capture, backend, _, directory = configure(fixture, warm=True)
    hashes = iter([{"invented_source": "before"}, {"invented_source": "after"}])
    monkeypatch.setattr(capture_module, "_source_hashes", lambda: next(hashes))
    with pytest.raises(ValueError, match="source changed"):
        capture.run(backend, **KWARGS)
    assert load(directory, "failure.json")["phase"] == "source_binding_recheck"


def test_failure_recorder_error_chains_but_preserves_original_model_exception(fixture, monkeypatch):
    capture, backend, embedder, directory = configure(fixture, warm=True)
    embedder.fail_if = lambda texts: True
    actual_write = capture._write_json
    recorder_error = OSError("invented failure marker disk error")

    def write(filename, value):
        if filename == "failure.json":
            raise recorder_error
        return actual_write(filename, value)

    monkeypatch.setattr(capture, "_write_json", write)
    with pytest.raises(RuntimeError) as caught:
        capture.run(backend, **KWARGS)
    assert caught.value is embedder.error and caught.value.__cause__ is recorder_error
    assert (directory / "request.json").exists()


def test_request_persistence_failure_happens_before_any_backend_work(fixture, monkeypatch):
    capture, backend, embedder, directory = configure(fixture)
    actual_write = capture._write_json

    def write(filename, value):
        if filename == "request.json":
            raise OSError("invented request persistence failed")
        return actual_write(filename, value)

    monkeypatch.setattr(capture, "_write_json", write)
    with pytest.raises(OSError, match="request persistence"):
        capture.run(backend, **KWARGS)
    assert embedder.actual_forwards == []
    assert load(directory, "failure.json")["phase"] == "pre_request_manifest"


def test_success_record_is_not_a_pass_when_audit_file_cannot_be_written(fixture, monkeypatch):
    capture, backend, _, directory = configure(fixture, warm=True)
    actual_write = capture._write_json

    def write(filename, value):
        if filename == "audit.json":
            raise OSError("invented audit persistence failed")
        return actual_write(filename, value)

    monkeypatch.setattr(capture, "_write_json", write)
    with pytest.raises(OSError, match="audit persistence"):
        capture.run(backend, **KWARGS)
    assert (directory / "record.json").exists()
    assert load(directory, "failure.json")["phase"] == "persist_success"
    assert not (directory / "audit.json").exists()


def test_exclusive_writer_never_overwrites_an_existing_artifact(fixture):
    capture, _, _, directory = configure(fixture)
    directory.mkdir()
    (directory / "marker.json").write_text("immutable")
    with pytest.raises(FileExistsError):
        capture._write_json("marker.json", {"changed": True})
    assert (directory / "marker.json").read_text() == "immutable"


def test_file_and_directory_are_fsynced(fixture, monkeypatch):
    capture, backend, _, _ = configure(fixture, warm=True)
    calls = []
    original = capture_module.os.fsync

    def fsync(descriptor):
        calls.append(descriptor)
        original(descriptor)

    monkeypatch.setattr(capture_module.os, "fsync", fsync)
    capture.run(backend, **KWARGS)
    assert len(calls) >= 8  # Each of four artifacts has a file and directory sync.


def test_incomplete_capture_is_a_failed_attempt_with_request_preserved(fixture, monkeypatch):
    capture, backend, embedder, directory = configure(fixture)

    def search(query, max_results=8, source_budget=12000, token_budget=4096):
        return {"invented": "backend returned without invoking its installed hook"}

    monkeypatch.setattr(backend, "search", search)
    with pytest.raises(ValueError, match="Exactly one"):
        capture.run(backend, **KWARGS)
    assert load(directory, "failure.json")["phase"] == "capture_snapshot"
    assert embedder.actual_forwards == []


def test_actual_backend_defaults_are_recorded_as_exact_limits(fixture):
    capture, backend, _, directory = configure(fixture, warm=True)
    capture.run(backend, query=QUERY)
    request, record = load(directory, "request.json"), load(directory, "record.json")
    assert request["search_kwargs"] == {"query": QUERY}
    assert request["limits"] == record["limits"] == {
        "max_results": 5, "source_budget": 12000, "token_budget": None}


def test_public_binding_mutation_during_search_cannot_rewrite_pre_request_classification(fixture):
    capture, backend, embedder, directory = configure(fixture, warm=True)

    def mutate_public_view():
        capture.bindings["classification"] = "attempted later rewrite"

    embedder.before_forward = mutate_public_view
    capture.run(backend, **KWARGS)
    assert load(directory, "record.json")["capture"]["classification"] == BINDINGS["classification"]
    assert load(directory, "manifest.json")["classification"] == BINDINGS["classification"]


def test_different_executing_package_checkout_is_rejected_before_model_work(fixture, monkeypatch):
    capture, backend, embedder, directory = configure(fixture)
    monkeypatch.setitem(capture_module.sys.modules, "semantic_search.invented_wrong_checkout",
                        SimpleNamespace(__file__="/tmp/invented-unrelated-checkout/module.py"))
    with pytest.raises(ValueError, match="package differs"):
        capture.run(backend, **KWARGS)
    assert embedder.actual_forwards == []
    assert load(directory, "failure.json")["phase"] == "pre_request_manifest"


@pytest.mark.parametrize("omit", ["retrieval_query", "documents"])
def test_success_cannot_omit_external_query_or_cold_document_receipts(fixture, omit):
    capture, backend, _, directory = configure(fixture)

    class OmitEvents(list):
        def append(self, event):
            if event.get("purpose") != omit:
                super().append(event)

    capture._forwards = OmitEvents()
    with pytest.raises(ValueError, match="receipts|document forwards"):
        capture.run(backend, **KWARGS)
    assert load(directory, "failure.json")["phase"] == "final_response_binding"
    assert not (directory / "audit.json").exists()


def test_failed_attempt_cannot_be_retried_or_overwritten(fixture):
    capture, backend, embedder, directory = configure(fixture, warm=True)
    embedder.fail_if = lambda texts: True
    with pytest.raises(RuntimeError):
        capture.run(backend, **KWARGS)
    original = (directory / "failure.json").read_bytes()
    embedder.fail_if = lambda texts: False
    with pytest.raises(RuntimeError, match="only once"):
        capture.run(backend, **KWARGS)
    assert (directory / "failure.json").read_bytes() == original


def test_failure_markers_handle_cycles_and_nonfinite_values_explicitly():
    cycle = {"nan": float("nan")}
    cycle["self"] = cycle
    result = capture_module._safe_snapshot(cycle)
    assert result["nan"]["reason"] == "nonfinite"
    assert result["self"]["reason"] == "cycle"


def test_repeated_provider_receipts_count_the_same_actual_clause_sequence_once():
    events = [{"schema": "qwen_embedding_forward_v1", "forward_index": 0,
               "batch_input_count": 2, "status": status} for status in ("started", "completed")]
    receipts = [{"extra_query_forward_events": events}, {"extra_query_forward_events": deepcopy(events)}]
    counts = capture_module._counts([], receipts)
    assert counts["extra_queries"]["reported_model_forward_calls"] == 1
    assert counts["extra_queries"]["reported_encoded_texts"] == 2
    assert counts["extra_receipts_consistent"]


def test_new_attempt_parent_entry_is_fsynced_before_backend_work(fixture, monkeypatch):
    capture, backend, embedder, directory = configure(fixture, warm=True)
    synced = []
    original = capture._sync_directory

    def sync(path):
        synced.append(Path(path))
        original(path)

    monkeypatch.setattr(capture, "_sync_directory", sync)

    def check_durable_attempt():
        assert synced[0] == directory.parent
        assert synced.count(directory) >= 2  # Manifest and request links are also durable.

    embedder.before_forward = check_durable_attempt
    capture.run(backend, **KWARGS)


def test_attempt_requires_existing_parent_and_never_starts_model_work(fixture):
    backend, embedder, directory = fixture
    capture = SemanticRequestCapture(directory / "missing-parent" / "attempt", bindings=BINDINGS)
    backend._research_postprocessor = capture.postprocessor
    backend._research_forward_observer = capture.forward_observer
    with pytest.raises(FileNotFoundError):
        capture.run(backend, **KWARGS)
    assert embedder.actual_forwards == [] and not directory.exists()


def test_hook_scope_excludes_pre_run_and_post_run_work(fixture):
    capture, backend, _, _ = configure(fixture, warm=True)
    with pytest.raises(RuntimeError, match="inside the captured request"):
        capture.forward_observer({"status": "started"})
    with pytest.raises(RuntimeError, match="inside the captured request"):
        capture.record({"invented": "outside run"})
    assert capture._records == capture._forwards == []
    capture.run(backend, **KWARGS)
    before = len(capture._forwards)
    with pytest.raises(RuntimeError, match="inside the captured request"):
        capture.forward_observer({"status": "completed"})
    assert len(capture._forwards) == before
