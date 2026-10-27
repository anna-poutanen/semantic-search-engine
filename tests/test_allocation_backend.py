"""Actual backend/client contracts for research allocation under fixed budgets."""
from contextlib import closing
import hashlib
import json
from unittest.mock import patch

import numpy as np
import pytest

from semantic_search import allocation, backend as backend_module, context, selection
from semantic_search.backend import SearchBackend
from semantic_search.cancellation import SearchCancelled
from semantic_search.context_backfill import coalesce_context
from semantic_search.source import physical_lines


QUERY = "alpha beta gamma and delta epsilon zeta"


class CountingEmbedder:
    metadata = {"id": "allocation-test", "revision": "fixed", "dimensions": 2}

    def __init__(self):
        self.documents = self.queries = 0
        self.after_query = lambda: None

    def embed_documents(self, texts):
        self.documents += 1
        return np.asarray([[1., 0.] for _ in texts]).reshape(-1, 2)

    def embed_query(self, query):
        self.queries += 1
        self.after_query()
        return np.asarray([1., 0.])

    def close(self):
        pass


@pytest.fixture
def repository(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    for number in range(9):
        symbol = f"alpha_beta_gamma_{number}" if number < 8 else "delta_epsilon_zeta"
        (source / f"{number:02}.py").write_text(f"def {symbol}():\n    return {number}\n")
    embedder = CountingEmbedder()
    with closing(SearchBackend(tmp_path, "src", tmp_path / "index.sqlite3", embedder=embedder)) as backend:
        yield tmp_path, backend, embedder


@pytest.mark.parametrize("mode", list(allocation.MODES))
@pytest.mark.parametrize("limits", [
    {"max_results": 8, "source_budget": 12000, "token_budget": 4096},
    {"max_results": 4, "source_budget": 400, "token_budget": 1024},
    {"max_results": 2, "source_budget": 90, "token_budget": 512},
])
def test_real_backend_uses_one_embedding_and_returns_literal_client_valid_source(repository, mode, limits):
    pytest.importorskip("mcp")
    from semantic_search.mcp_server import SearchArguments, _validate_context

    repo, backend, embedder = repository
    settings = dict(retrieval="dense", candidates=50, include_dependencies=True, **limits)
    with patch.object(backend_module, "select_context", selection.definition_context), \
            patch.object(backend_module, "postprocess_context", coalesce_context):
        control = backend.search(QUERY, **settings)
    assert not any(row["symbol"] == "delta_epsilon_zeta" for row in control["results"])
    before = embedder.queries
    with patch.object(backend_module, "select_context", getattr(allocation, mode + "_context")), \
            patch.object(backend_module, "postprocess_context", coalesce_context), \
            patch.object(allocation, "allocation_plan", wraps=allocation.allocation_plan) as planner:
        result = backend.search(QUERY, **settings)
    assert planner.call_count == 1
    assert embedder.queries == before + 1 and embedder.documents == 1
    assert result["indexstats"]["memory_cache_hit"]
    assert result["indexstats"]["total_windows"] == 9
    assert len(result["results"]) <= limits["max_results"]
    assert result["total_source_chars"] <= limits["source_budget"]
    assert result["token_usage"]["tokens"] <= limits["token_budget"]
    for row in result["results"]:
        full = (repo / row["path"]).read_text()
        assert row["source"] == "".join(physical_lines(full)[row["start_line"] - 1:row["end_line"]])
        assert row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()
        assert row["content_hash"] == hashlib.sha256(full.encode()).hexdigest()
        assert row["origin"] == "retrieval" and row["related_to"] is None
        assert row["h2o_score"] is None and row["qwen_score"] is None
        assert "assembly" not in row
    envelope = context.compact_result(result)
    text = _validate_context(envelope, SearchArguments(query=QUERY, include_dependencies=True, **limits))
    assert json.loads(text) == envelope and "allocation" not in envelope
    if limits["max_results"] == 8:
        assert any(row["symbol"] == "delta_epsilon_zeta" for row in result["results"])
    assert backend_module.select_context is selection.ranked_context
    assert backend_module.postprocess_context is selection.unchanged_context


@pytest.mark.parametrize("mode", list(allocation.MODES))
def test_cancellation_after_embedding_prevents_allocation_and_reuses_index(repository, mode):
    _, backend, embedder = repository
    backend.refresh()
    embedder.after_query = lambda: backend.cancel("cancel-allocation")
    with patch.object(backend_module, "select_context", getattr(allocation, mode + "_context")), \
            patch.object(allocation, "allocation_plan", wraps=allocation.allocation_plan) as planner:
        with pytest.raises(SearchCancelled):
            backend.search(QUERY, candidates=50, max_results=8, token_budget=4096,
                           include_dependencies=True, request_id="cancel-allocation")
    assert planner.call_count == 0
    assert embedder.queries == 1 and embedder.documents == 1
    embedder.after_query = lambda: None
    recovered = backend.search(QUERY, max_results=8, token_budget=4096)
    assert recovered["indexstats"]["memory_cache_hit"]
    assert embedder.documents == 1 and embedder.queries == 2
