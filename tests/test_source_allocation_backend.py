"""Real backend/MCP contracts for the research source-graph allocation hooks."""
from contextlib import closing
import hashlib
import json
import re
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from semantic_search import backend as backend_module, context, selection
from semantic_search import source_allocation
from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.context_backfill import coalesce_context
from semantic_search.source import physical_lines


QUERY = "resolve alpha implementation and attach beta implementation"


class Embedder:
    metadata = {"id": "source-graph-test", "revision": "fixed", "dimensions": 2}

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
def repository(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    text = "\n".join(f"def implementation_{n}():\n    return " +
                     ("implementation_1()\n" if n == 9 else f"{n}\n")
                     for n in range(1, 10))
    (source / "code.py").write_text(text)
    embedder = Embedder()
    with closing(SearchBackend(tmp_path, "src", tmp_path / "index.sqlite3", embedder=embedder)) as backend:
        yield tmp_path, backend, embedder


def hooks(mode):
    return patch.multiple(backend_module,
        select_context=getattr(source_allocation, mode + "_context"),
        postprocess_context=source_allocation.guard_source_context)


@pytest.mark.parametrize("mode", ["source_graph", "source_graph_all"])
@pytest.mark.parametrize("limits", [
    {"max_results": 8, "source_budget": 12000, "token_budget": 4096},
    {"max_results": 4, "source_budget": 150, "token_budget": 1024},
    {"max_results": 2, "source_budget": 60, "token_budget": 512},
])
def test_actual_backend_plans_and_guards_once_with_one_embedding_and_exact_mcp_source(repository, mode, limits):
    pytest.importorskip("mcp")
    from semantic_search.mcp_server import SearchArguments, _validate_context

    repo, backend, embedder = repository
    settings = dict(retrieval="dense", candidates=50, include_dependencies=True, **limits)
    with patch.multiple(backend_module, select_context=selection.definition_context,
                        postprocess_context=coalesce_context):
        control = backend.search(QUERY, **settings)
    before = embedder.queries
    with hooks(mode), \
            patch.object(source_allocation, "source_allocation_plan", wraps=source_allocation.source_allocation_plan) as planner, \
            patch.object(source_allocation, "guard_source_plan", wraps=source_allocation.guard_source_plan) as guard:
        result = backend.search(QUERY, **settings)
    assert planner.call_count == guard.call_count == 1
    assert embedder.queries == before + 1 and embedder.documents == 1
    assert result["indexstats"]["memory_cache_hit"]
    assert len(result["results"]) <= limits["max_results"]
    assert result["total_source_chars"] <= limits["source_budget"]
    assert result["token_usage"]["tokens"] <= limits["token_budget"]
    assert "_source_guard" not in result and "source_plan" not in result
    for row in result["results"]:
        full = (repo / row["path"]).read_text()
        assert row["source"] == "".join(physical_lines(full)[row["start_line"] - 1:row["end_line"]])
        assert row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()
        original_window = "".join(physical_lines(full)[row["start_line"] - 1:row["window_end_line"]])
        assert row["content_hash"] == hashlib.sha256(original_window.encode()).hexdigest()
        assert row["origin"] == "retrieval" and row["related_to"] is None
        assert row["h2o_score"] is None and row["qwen_score"] is None
    envelope = context.compact_result(result)
    text = _validate_context(envelope, SearchArguments(query=QUERY, include_dependencies=True, **limits))
    assert json.loads(text) == envelope
    if limits["max_results"] == 8:
        symbols = [row["symbol"] for row in result["results"]]
        assert ("implementation_9" in symbols) == (mode == "source_graph")
        if mode == "source_graph_all":
            assert context.compact_result(control) == envelope
    assert backend_module.select_context is selection.ranked_context
    assert backend_module.postprocess_context is selection.unchanged_context


@pytest.mark.parametrize("mode", ["source_graph", "source_graph_all"])
def test_cancel_after_embedding_never_plans_and_recovery_reuses_index(repository, mode):
    _, backend, embedder = repository
    backend.refresh()
    embedder.after_query = lambda: backend.cancel("source-graph-cancel")
    with hooks(mode), patch.object(source_allocation, "source_allocation_plan", wraps=source_allocation.source_allocation_plan) as planner:
        with pytest.raises(SearchCancelled):
            backend.search(QUERY, candidates=50, max_results=8, token_budget=4096,
                           include_dependencies=True, request_id="source-graph-cancel")
    assert planner.call_count == 0 and embedder.documents == embedder.queries == 1
    embedder.after_query = lambda: None
    with hooks(mode):
        recovered = backend.search(QUERY, max_results=8, token_budget=4096, include_dependencies=True)
    assert recovered["indexstats"]["memory_cache_hit"]
    assert embedder.documents == 1 and embedder.queries == 2


@pytest.mark.parametrize("mode", ["source_graph", "source_graph_all"])
def test_unsaved_editor_source_and_version_survive_actual_guard(repository, mode):
    repo, backend, embedder = repository
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "src/code.py"], check=True)
    source = (repo / "src/code.py").read_text().replace("return 1", "return 101")
    digest = hashlib.sha256(source.encode()).hexdigest()
    with hooks(mode):
        result = backend.search(QUERY, max_results=8, token_budget=4096, include_dependencies=True,
            overlays=[{"path": "src/code.py", "version": 7, "text": source, "content_hash": digest}])
    assert result["results"]
    for row in result["results"]:
        assert row["source_origin"] == "overlay"
        assert row["buffer_version"] == 7 and row["buffer_hash"] == digest
        original_window = "".join(physical_lines(source)[row["start_line"] - 1:row["window_end_line"]])
        assert row["content_hash"] == hashlib.sha256(original_window.encode()).hexdigest()
        assert row["source"] == "".join(physical_lines(source)[row["start_line"] - 1:row["end_line"]])
    assert "return 101" in result["results"][0]["source"]


@pytest.mark.parametrize("mode", ["source_graph", "source_graph_all"])
@pytest.mark.parametrize("provider", ["h2o", "qwen"])
def test_promoted_retrieval_keeps_its_own_original_reranker_and_cosine_score(repository, mode, provider):
    _, backend, embedder = repository
    calls = []

    def score(query, snippet, **kwargs):
        number = int(snippet.symbol.rsplit("_", 1)[1])
        calls.append(snippet.id)
        return SimpleNamespace(score=100. - number, response={"synthetic": True})

    scorer = SimpleNamespace(score=score)
    with hooks(mode), patch.object(backend, "_" + provider, scorer):
        result = backend.search(QUERY, candidates=9, max_results=8, token_budget=4096,
                                rerank=True, rerank_provider=provider, include_dependencies=True)
    assert len(calls) == 9 and len(set(calls)) == 9
    assert embedder.documents == embedder.queries == 1
    for row in result["results"]:
        number = int(row["symbol"].rsplit("_", 1)[1])
        assert row[provider + "_score"] == 100. - number
        assert row[("qwen" if provider == "h2o" else "h2o") + "_score"] is None
        assert row["retrieval_score"] == pytest.approx(1. / np.sqrt(1. + (number / 10) ** 2))
        assert row["origin"] == "retrieval" and row["related_to"] is None
    assert any(row["symbol"] == "implementation_9" for row in result["results"]) == (mode == "source_graph")


@pytest.mark.parametrize("mode", ["source_graph", "source_graph_all"])
@pytest.mark.parametrize("interruption", ["cancel", "source_change"])
def test_final_backend_checks_prevent_delivery_after_guard_interruption(repository, mode, interruption):
    repo, backend, _ = repository
    original = source_allocation.guard_source_plan

    def interrupted(*args, **kwargs):
        value = original(*args, **kwargs)
        if interruption == "cancel":
            backend.cancel("during-source-guard")
        else:
            (repo / "src/code.py").write_text("def implementation_1():\n    return 0\n")
        return value

    error = SearchCancelled if interruption == "cancel" else SourceSnapshotError
    with hooks(mode), patch.object(source_allocation, "guard_source_plan", interrupted):
        with pytest.raises(error):
            backend.search(QUERY, candidates=50, max_results=8, token_budget=4096,
                           include_dependencies=True, request_id="during-source-guard")
