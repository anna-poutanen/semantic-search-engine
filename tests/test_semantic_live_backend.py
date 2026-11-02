"""Actual saved/overlay backend integration using an invented CPU model."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import subprocess

import numpy as np
import pytest

from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.context import compact_result, encoding, serialize_context
from semantic_search.embeddings import DIMENSIONS, QUERY_PROMPT, QwenEmbedder
from semantic_search.semantic_context_adapter import make_live_semantic_postprocessor

QUERY = "load alpha payload and render beta template"


class InventedTokenizer:
    def __call__(self, texts, **kwargs):
        return {"input_ids": [[value + 1 for value in text.encode()] for text in texts]}


class InventedEmbedder(QwenEmbedder):
    def __init__(self):
        super().__init__(progress=False)
        self._tokenizer = InventedTokenizer()
        self.actual_forwards = []

    @property
    def metadata(self):
        return {**super().metadata, "synthetic_fixture": "invented CPU model, not Qwen outputs"}

    def _ensure_loaded(self):
        pass

    def _embed_batch(self, ids):
        texts = [bytes(value - 1 for value in row).decode() for row in ids]
        self.actual_forwards.append(texts)
        vectors = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for position, text in enumerate(texts):
            if text == QUERY_PROMPT + "load alpha payload" or "def implementation_9(" in text:
                axis = 1
            elif text == QUERY_PROMPT + "render beta template" or "def implementation_10(" in text:
                axis = 2
            else:
                axis = 0
            vectors[position, axis] = 1
        return vectors


@pytest.fixture
def fixture(tmp_path):
    (tmp_path / "src").mkdir()
    source = "\n".join(f"def implementation_{n}():\n    return {n}\n" for n in range(1, 12))
    (tmp_path / "src/code.py").write_text(source)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "src"], check=True, capture_output=True)
    embedder = InventedEmbedder()
    with closing(SearchBackend(tmp_path, "src", tmp_path / "saved.sqlite3", embedder=embedder)) as backend:
        yield tmp_path, backend, embedder


def audit(record):
    path = Path(__file__).resolve().parents[1] / "scripts/audit_semantic_context.py"
    spec = importlib.util.spec_from_file_location("live_backend_independent_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = deepcopy(record)
    captured.pop("provider_receipts")
    payload = serialize_context(compact_result(captured["response"]))
    captured.update(compact_payload=payload,
        compact_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        actual_compact_tokens=len(encoding().encode(payload, disallowed_special=())))
    assert module.verify_record(captured, encoding()) == captured["response"]


def test_request_context_binds_original_vector_and_control_has_no_hook(fixture):
    _, backend, embedder = fixture
    backend.refresh()
    before = len(embedder.actual_forwards)
    contexts = []

    def processor(response, candidates, *, request_context, **kwargs):
        from semantic_search.context_backfill import coalesce_context
        contexts.append(request_context)
        return coalesce_context(response, candidates, **kwargs)

    backend._research_postprocessor = processor
    result = backend.search(QUERY, max_results=8, token_budget=4096)
    ctx = contexts[0]
    assert len(embedder.actual_forwards) == before + 1
    assert ctx["index"] is backend._index and ctx["embedder"] is embedder
    assert np.array_equal(ctx["query_vector"], np.eye(1, DIMENSIONS, dtype=np.float32)[0])
    assert [event["status"] for event in ctx["query_forward_events"]] == ["started", "completed"]
    assert ctx["query_forward_events"][0]["input_sha256"] == [hashlib.sha256((QUERY_PROMPT + QUERY).encode()).hexdigest()]
    ctx["confirm_snapshot"]()
    assert result["retrieval"] == "dense"


@pytest.mark.parametrize("mode", ["dense", "hybrid", "multifile"])
def test_actual_live_provider_uses_original_query_and_resident_document_vectors(fixture, mode):
    _, backend, embedder = fixture
    backend.refresh()
    before = len(embedder.actual_forwards)
    records, query_events = [], []
    backend._research_postprocessor = make_live_semantic_postprocessor(capture=records.append)
    backend._research_forward_observer = query_events.append
    result = backend.search(QUERY, retrieval=mode, max_results=8, include_dependencies=True, token_budget=4096)
    new_forwards = embedder.actual_forwards[before:]
    # Multifile retrieval currently reuses this original vector; extra semantic
    # clauses alone enter the new query batch, never documents/full query again.
    assert new_forwards[0] == [QUERY_PROMPT + QUERY]
    assert sorted(new_forwards[1]) == sorted([QUERY_PROMPT + "load alpha payload", QUERY_PROMPT + "render beta template"])
    assert len(new_forwards) == 2 and len(query_events) == 2
    assert records[0]["provider_receipts"] and records[0]["features"] is not None
    assert records[0]["coverage_coalesce_calls"] == records[0]["semantic_calls"] == 1
    assert records[0]["semantic_receipt"]["subsequent_backfill_calls"] == 0
    assert records[0]["features"]["facet_vectors"][0] == [1.] + [0.] * (DIMENSIONS - 1)
    assert "provider_receipts" not in result
    audit(records[0])


@pytest.mark.parametrize("warm_saved_index", [False, True])
def test_overlay_provider_reads_child_index_and_preserves_parent(fixture, warm_saved_index):
    repo, backend, embedder = fixture
    if warm_saved_index:
        backend.refresh()
    saved_index = backend._index
    saved_state = deepcopy(saved_index.status()) if saved_index is not None else None
    saved_snippets = saved_index.snippets if saved_index is not None else None
    text = (repo / "src/code.py").read_text().replace("return 9", "return 'buffer_changed'")
    records, contexts = [], []
    actual = make_live_semantic_postprocessor(capture=records.append)

    def processor(*args, request_context, **kwargs):
        contexts.append(request_context)
        return actual(*args, request_context=request_context, **kwargs)

    backend._research_postprocessor = processor
    result = backend.search(QUERY, max_results=8, token_budget=4096, overlays=[{
        "path": "src/code.py", "text": text, "version": 7,
        "content_hash": hashlib.sha256(text.encode()).hexdigest()}])
    assert contexts[0]["index"] is not saved_index
    assert not contexts[0]["index"].db_path.exists()
    assert contexts[0]["source_snapshot"] != backend._manifest
    assert all(row["source_origin"] == "overlay" and row["buffer_version"] == 7 for row in result["results"])
    if saved_index is not None:
        assert backend._index.status() == saved_state and backend._index.snippets == saved_snippets
    else:
        assert backend._index is None and not backend.db_path.exists()
    assert records[0]["features"] is not None
    audit(records[0])


def test_bm25_has_no_query_or_clause_forward(fixture):
    _, backend, embedder = fixture
    records = []
    backend._research_postprocessor = make_live_semantic_postprocessor(capture=records.append)
    backend.search(QUERY, retrieval="bm25", max_results=8, token_budget=4096)
    assert embedder.actual_forwards == []
    assert records[0]["features"] is None
    assert {k: v for k, v in records[0]["response"].items() if k not in ("timings", "indexstats")} == {
        k: v for k, v in records[0]["reference_response"].items() if k not in ("timings", "indexstats")}
    assert records[0]["provider_receipts"]
    audit(records[0])


def test_external_query_observer_failure_stops_before_actual_forward(fixture):
    _, backend, embedder = fixture
    backend.refresh()
    before = len(embedder.actual_forwards)

    def fail(event):
        raise RuntimeError("fixture observer failed")

    backend._research_forward_observer = fail
    with pytest.raises(RuntimeError, match="observer failed"):
        backend.search(QUERY)
    assert len(embedder.actual_forwards) == before


def test_changed_saved_source_after_provider_is_rejected(fixture):
    repo, backend, _ = fixture
    actual = make_live_semantic_postprocessor()

    def processor(*args, **kwargs):
        response = actual(*args, **kwargs)
        (repo / "src/code.py").write_text("def changed():\n    return 0\n")
        return response

    backend._research_postprocessor = processor
    with pytest.raises(SourceSnapshotError):
        backend.search(QUERY, max_results=8, token_budget=4096)


def test_cancel_after_original_query_keeps_completed_forward_receipt(fixture):
    _, backend, embedder = fixture
    backend.refresh()
    events = []

    def observe(event):
        events.append(event)
        if event["status"] == "completed":
            backend.cancel("fixture-cancel")

    backend._research_forward_observer = observe
    with pytest.raises(SearchCancelled):
        backend.search(QUERY, request_id="fixture-cancel")
    assert [event["status"] for event in events] == ["started", "completed"]
    assert not any("load alpha payload" == text for batch in embedder.actual_forwards for text in batch)


def test_cold_research_request_records_document_work_separately(fixture):
    _, backend, embedder = fixture
    events, records = [], []
    backend._research_forward_observer = events.append
    backend._research_postprocessor = make_live_semantic_postprocessor(capture=records.append)
    backend.search(QUERY, max_results=8, token_budget=4096)
    terminals = [event for event in events if event["status"] in ("completed", "failed")]
    assert [event["purpose"] for event in terminals] == ["documents"] * 3 + ["retrieval_query"]
    assert len(embedder.actual_forwards) == len(terminals) + 1  # One extra clause batch.
    assert records[0]["provider_receipts"][0]["document_inference_calls"] == 0
    # A zero provider count does not conceal the actual cold index cost.
    assert sum(event["batch_input_count"] for event in terminals if event["purpose"] == "documents") == 11


def test_original_non_axis_query_vector_is_bit_preserved_and_reference_matches_control(fixture):
    _, backend, embedder = fixture
    actual_batch = embedder._embed_batch
    original = np.zeros(DIMENSIONS, dtype=np.float32)
    original[:4] = [0.1, -0., 0.7, 0.9]

    def invented_batch(ids):
        result = actual_batch(ids)
        for i, row in enumerate(ids):
            if bytes(value - 1 for value in row).decode() == QUERY_PROMPT + QUERY:
                result[i] = original
        return result

    embedder._embed_batch = invented_batch
    control = backend.search(QUERY, max_results=8, token_budget=4096)
    records = []
    backend._research_postprocessor = make_live_semantic_postprocessor(capture=records.append)
    backend.search(QUERY, max_results=8, token_budget=4096)
    captured = np.asarray(records[0]["features"]["facet_vectors"][0], dtype=np.float32)
    assert captured.tobytes() == original.tobytes()
    assert {k: v for k, v in records[0]["reference_response"].items() if k not in ("timings", "indexstats")} == {
        k: v for k, v in control.items() if k not in ("timings", "indexstats")}
    audit(records[0])


@pytest.mark.parametrize("kwargs", [{"research_postprocessor": False}, {"research_forward_observer": []}])
def test_invalid_research_hook_is_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError, match="Research hooks"):
        SearchBackend(tmp_path, ".", **kwargs)
