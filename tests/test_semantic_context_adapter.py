"""Actual toy backend/MCP integration, with invented vectors and no model load."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
from unittest.mock import patch

import numpy as np
import pytest

from semantic_search import backend as backend_module, context
from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.embeddings import QwenEmbedder
from semantic_search.semantic_context_adapter import make_semantic_postprocessor
from semantic_search.semantic_features import expected_facets, make_features
from semantic_search.source import physical_lines

QUERY = "load alpha payload and render beta template"


def independent_backend_capture(record):
    script = Path(__file__).resolve().parents[1] / "scripts/audit_semantic_context.py"
    spec = importlib.util.spec_from_file_location("actual_backend_semantic_audit", script)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    value = deepcopy(record)
    payload = context.serialize_context(context.compact_result(value["response"]))
    value.update(compact_payload=payload, compact_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        actual_compact_tokens=len(context.encoding().encode(payload, disallowed_special=())))
    assert audit.verify_record(value, context.encoding()) == value["response"]


class ToyEmbedder:
    metadata = {"id": "semantic-adapter-invented-fixture", "dimensions": 2}

    def __init__(self):
        self.documents = self.queries = 0
        self.after_query = lambda: None

    def embed_documents(self, texts):
        self.documents += 1
        return np.asarray([[1., int(re.search(r"def implementation_(\d+)", text)[1]) / 10]
                           for text in texts]).reshape(-1, 2)

    def embed_query(self, query):
        self.queries += 1
        self.after_query()
        return np.asarray([1., 0.])

    def close(self):
        pass


@pytest.fixture
def toy(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/code.py").write_text("\n".join(
        f"def implementation_{n}():\n    return " +
        ("implementation_11()\n" if n == 1 else f"{n}\n") for n in range(1, 12)))
    embedder = ToyEmbedder()
    with closing(SearchBackend(tmp_path, "src", tmp_path / "toy.sqlite3", embedder=embedder)) as backend:
        yield tmp_path, backend, embedder


@pytest.fixture(autouse=True)
def forbid_real_checkpoint_load(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real model loading is forbidden in CPU preparation")
    monkeypatch.setattr(QwenEmbedder, "_ensure_loaded", forbidden)


def unit(axis):
    values = [0.] * 1024
    values[axis] = 1.
    return values


def invented_features(reference, rows, *, cancel_check, target_id=None):
    cancel_check()
    vectors = {row["id"]: unit(1 if (row["id"] == target_id if target_id is not None
                                   else row["symbol"] == "implementation_9") else 0)
               for row in [*rows, *reference["results"]]
               if not row["truncated"] and row["end_line"] == row["window_end_line"]}
    facets = expected_facets(reference["query"])
    return make_features(reference["query"], rows, reference,
        [unit(1 if index == len(facets) - 1 else 0) for index in range(len(facets))],
        vectors, QwenEmbedder(progress=False).metadata)


@pytest.mark.parametrize("query", [QUERY, "simple implementation"])
def test_one_actual_coverage_pass_terminal_exchange_and_mcp_payload(toy, query):
    pytest.importorskip("mcp")
    from semantic_search.mcp_server import SearchArguments, _validate_context
    from semantic_search import semantic_context_adapter as adapter

    repo, backend, embedder = toy
    control = backend.search(query, max_results=8, include_dependencies=True, token_budget=4096)
    original = backend_module.postprocess_context
    records, provider_calls = [], []

    def provider(*args, **kwargs):
        provider_calls.append(1)
        return invented_features(*args, **kwargs)

    post = make_semantic_postprocessor(provider, capture=records.append)
    with patch.object(QwenEmbedder, "_ensure_loaded", side_effect=AssertionError("Real model forbidden")), \
         patch.object(backend_module, "postprocess_context", post), \
         patch.object(adapter, "coalesce_context", wraps=adapter.coalesce_context) as actual_coalesce:
        result = backend.search(query, max_results=8, include_dependencies=True, token_budget=4096)
    assert actual_coalesce.call_count == 1 and len(records) == 1
    record = records[0]
    assert record["coverage_coalesce_calls"] == record["semantic_calls"] == 1
    assert record["feature_calls"] == len(provider_calls) == int(query == QUERY)
    assert record["semantic_receipt"]["subsequent_backfill_calls"] == 0
    # Backend finalizes these operational fields after the hook. The independent
    # auditor permits only those updates and reconstructs the source itself.
    independent_backend_capture(record)
    def source_view(value):
        return {key: item for key, item in value.items() if key not in ("timings", "indexstats")}
    assert source_view(record["reference_response"]) == source_view(control)
    assert embedder.documents == 1 and embedder.queries == 2
    envelope = context.compact_result(result)
    assert json.loads(_validate_context(envelope, SearchArguments(query=query, max_results=8,
        source_budget=12000, token_budget=4096, include_dependencies=True))) == envelope
    if query == QUERY:
        assert record["semantic_receipt"]["accepted"]
        assert "implementation_9" in {row["symbol"] for row in result["results"]}
        assert "_selection_backfill" not in result
    else:
        assert envelope == context.compact_result(control)
        assert record["features"] is None
    for row in result["results"]:
        source = (repo / row["path"]).read_text()
        assert row["source"] == "".join(physical_lines(source)[row["start_line"] - 1:row["end_line"]])
        assert row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()
    assert backend_module.postprocess_context is original


def test_provider_failure_cannot_masquerade_as_successful_fallback(toy):
    _, backend, _ = toy
    captured = []

    def broken(*args, **kwargs):
        raise RuntimeError("invented feature failure")

    with patch.object(backend_module, "postprocess_context",
                      make_semantic_postprocessor(broken, capture=captured.append)):
        with pytest.raises(RuntimeError, match="invented feature failure"):
            backend.search(QUERY, token_budget=4096)
    assert not captured


def test_invalid_feature_payload_returns_exact_actual_coverage(toy):
    _, backend, embedder = toy
    control = backend.search(QUERY, include_dependencies=True, token_budget=4096)
    captures = []
    with patch.object(backend_module, "postprocess_context",
                      make_semantic_postprocessor(lambda *a, **k: {}, capture=captures.append)):
        result = backend.search(QUERY, include_dependencies=True, token_budget=4096)
    assert context.compact_result(result) == context.compact_result(control)
    assert captures[0]["semantic_receipt"]["fallback_reason"] == "invalid_features"
    assert embedder.documents == 1 and embedder.queries == 2


def test_provider_cannot_rewrite_the_actual_reference_or_raw_source(toy):
    _, backend, _ = toy
    control = backend.search(QUERY, include_dependencies=True, token_budget=4096)

    def mutating(reference, rows, **kwargs):
        reference["query"] = "rewritten query"
        reference["results"][0]["source"] = "fabricated source"
        rows[0]["source"] = "fabricated raw source"
        return {}

    with patch.object(backend_module, "postprocess_context", make_semantic_postprocessor(mutating)):
        result = backend.search(QUERY, include_dependencies=True, token_budget=4096)
    assert context.compact_result(result) == context.compact_result(control)


def test_overlay_bindings_use_actual_unsaved_source(toy):
    repo, backend, _ = toy
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "src"], check=True)
    text = (repo / "src/code.py").read_text().replace("return 9\n", "return 900\n")
    digest = hashlib.sha256(text.encode()).hexdigest()
    records = []

    def overlay_features(reference, rows, *, cancel_check):
        # The overlay backend can already deliver the normal target. Exercise
        # an actual exchange by giving one still-missing invented window credit.
        present = {row["id"] for row in reference["results"]}
        target = next(row["id"] for row in rows if row["id"] not in present)
        return invented_features(reference, rows, cancel_check=cancel_check, target_id=target)

    with patch.object(backend_module, "postprocess_context",
                      make_semantic_postprocessor(overlay_features, capture=records.append)):
        result = backend.search(QUERY, max_results=8, token_budget=4096, include_dependencies=True,
            overlays=[{"path": "src/code.py", "version": 6, "text": text, "content_hash": digest}])
    receipt = records[0]["semantic_receipt"]
    assert receipt["accepted"], json.dumps({key: receipt[key] for key in (
        "fallback_reason", "termination", "protected_rows", "reference_support", "trials", "guard")})
    independent_backend_capture(records[0])
    for row in result["results"]:
        assert row["source_origin"] == "overlay" and row["buffer_version"] == 6 and row["buffer_hash"] == digest
        assert row["source"] == "".join(physical_lines(text)[row["start_line"] - 1:row["end_line"]])


@pytest.mark.parametrize("failure", ["cancel", "snapshot"])
def test_final_cancellation_and_snapshot_checks_remain_active(toy, failure):
    repo, backend, _ = toy

    def capture(event):
        if failure == "cancel":
            backend.cancel("semantic-fixture")
        else:
            (repo / "src/code.py").write_text("def implementation_1():\n    return 99\n")

    with patch.object(backend_module, "postprocess_context",
                      make_semantic_postprocessor(invented_features, capture=capture)):
        with pytest.raises(SearchCancelled if failure == "cancel" else SourceSnapshotError):
            backend.search(QUERY, token_budget=4096, include_dependencies=True, request_id="semantic-fixture")


def test_adapter_never_invokes_lazy_reference_factory(toy):
    _, backend, _ = toy
    hook = make_semantic_postprocessor(invented_features)

    def checked(*args, **kwargs):
        kwargs["reference_factory"] = lambda: pytest.fail("Second coverage pass forbidden")
        return hook(*args, **kwargs)

    with patch.object(backend_module, "postprocess_context", checked):
        assert backend.search(QUERY, token_budget=4096)["results"]


@pytest.mark.parametrize("provider,capture", [(None, None), (lambda: None, 1)])
def test_provider_and_capture_must_be_callable(provider, capture):
    with pytest.raises(TypeError):
        make_semantic_postprocessor(provider, capture=capture)
