"""Actual backend/MCP contracts for shared-caller flow protection (CPU fixtures)."""
from contextlib import closing
import hashlib
import json
import re
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from semantic_search import backend as backend_module, context, selection, source_allocation
from semantic_search import flow_allocation
from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.context_backfill import coalesce_context
from semantic_search.source import physical_lines


QUERY = "resolve alpha implementation and attach beta implementation"


class Embedder:
    metadata = {"id": "shared-flow-test", "revision": "fixed", "dimensions": 2}

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


@pytest.fixture(params=["simple", "blocked", "prefix"])
def repository(tmp_path, request):
    kind = request.param
    bodies = {n: str(n) for n in range(1, 13)}
    if kind == "simple":
        bodies[9] = "implementation_1()"
    elif kind == "blocked":
        bodies.update({1: "implementation_10() + implementation_11()",
                       8: "implementation_1()", 9: "implementation_2()",
                       12: "implementation_8() + implementation_6()"})
    else:
        bodies.update({9: "implementation_1()", 10: "implementation_2()",
                       11: "implementation_9() + implementation_8()",
                       12: "implementation_10() + implementation_7()"})
    source = tmp_path / "src"
    source.mkdir()
    (source / "code.py").write_text("\n".join(
        f"def implementation_{n}():\n    return {body}\n" for n, body in bodies.items()))
    embedder = Embedder()
    with closing(SearchBackend(tmp_path, "src", tmp_path / "index.sqlite3", embedder=embedder)) as backend:
        yield tmp_path, backend, embedder, kind


def hooks():
    return patch.multiple(backend_module, select_context=flow_allocation.shared_flow_context,
                          postprocess_context=source_allocation.guard_source_context)


@pytest.mark.parametrize("limits", [
    {"max_results": 8, "source_budget": 12000, "token_budget": 4096},
    {"max_results": 4, "source_budget": 150, "token_budget": 1024},
    {"max_results": 2, "source_budget": 60, "token_budget": 512},
])
def test_actual_flow_plan_and_guard_use_one_query_forward_and_preserve_literal_mcp_contract(repository, limits):
    pytest.importorskip("mcp")
    from semantic_search.mcp_server import SearchArguments, _validate_context

    repo, backend, embedder, kind = repository
    settings = dict(candidates=50, include_dependencies=True, **limits)
    with patch.multiple(backend_module, select_context=selection.definition_context,
                        postprocess_context=coalesce_context):
        control = backend.search(QUERY, **settings)
    before = embedder.queries
    with hooks(), \
            patch.object(flow_allocation, "flow_allocation_plan", wraps=flow_allocation.flow_allocation_plan) as planner, \
            patch.object(source_allocation, "source_allocation_plan", wraps=source_allocation.source_allocation_plan) as base, \
            patch.object(source_allocation, "guard_source_plan", wraps=source_allocation.guard_source_plan) as guard:
        result = backend.search(QUERY, **settings)
    assert planner.call_count == base.call_count == guard.call_count == 1
    assert embedder.queries == before + 1 and embedder.documents == 1
    assert result["indexstats"]["memory_cache_hit"]
    assert len(result["results"]) <= limits["max_results"]
    assert result["total_source_chars"] <= limits["source_budget"]
    assert result["token_usage"]["tokens"] <= limits["token_budget"]
    assert not {"flow_plan", "source_plan", "_source_guard"} & result.keys()
    for row in result["results"]:
        full = (repo / row["path"]).read_text()
        lines = physical_lines(full)
        assert row["source"] == "".join(lines[row["start_line"] - 1:row["end_line"]])
        assert row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()
        assert row["content_hash"] == hashlib.sha256("".join(
            lines[row["start_line"] - 1:row["window_end_line"]]).encode()).hexdigest()
        assert row["h2o_score"] is None and row["qwen_score"] is None
    envelope = context.compact_result(result)
    assert json.loads(_validate_context(envelope, SearchArguments(query=QUERY, include_dependencies=True, **limits))) == envelope
    if limits["max_results"] == 8:
        names = [row["symbol"] for row in result["results"]]
        if kind == "simple":
            assert "implementation_9" in names
        elif kind == "blocked":
            assert context.compact_result(control) == envelope
            assert "implementation_6" in names and "implementation_8" not in names
            assert "implementation_9" not in names
        else:
            assert "implementation_9" in names and "implementation_10" not in names
            assert "implementation_8" in names and "implementation_7" not in names
    assert backend_module.select_context is selection.ranked_context
    assert backend_module.postprocess_context is selection.unchanged_context


def test_cancel_before_planning_keeps_cache_reusable(repository):
    _, backend, embedder, _ = repository
    backend.refresh()
    embedder.after_query = lambda: backend.cancel("flow-cancel")
    with hooks(), patch.object(flow_allocation, "flow_allocation_plan", wraps=flow_allocation.flow_allocation_plan) as planner:
        with pytest.raises(SearchCancelled):
            backend.search(QUERY, candidates=50, max_results=8, token_budget=4096,
                           include_dependencies=True, request_id="flow-cancel")
    assert planner.call_count == 0 and embedder.documents == embedder.queries == 1
    embedder.after_query = lambda: None
    with hooks():
        recovered = backend.search(QUERY, max_results=8, token_budget=4096, include_dependencies=True)
    assert recovered["indexstats"]["memory_cache_hit"]
    assert embedder.documents == 1 and embedder.queries == 2


def test_unsaved_buffer_source_and_provenance_survive_shared_flow_guard(repository):
    repo, backend, _, _ = repository
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "src/code.py"], check=True)
    source = (repo / "src/code.py").read_text().replace("return 2\n", "return 202\n")
    digest = hashlib.sha256(source.encode()).hexdigest()
    with hooks():
        result = backend.search(QUERY, max_results=8, token_budget=4096, include_dependencies=True,
            overlays=[{"path": "src/code.py", "version": 8, "text": source, "content_hash": digest}])
    assert result["results"]
    for row in result["results"]:
        assert row["source_origin"] == "overlay" and row["buffer_version"] == 8
        assert row["buffer_hash"] == digest
        assert row["source"] == "".join(physical_lines(source)[row["start_line"] - 1:row["end_line"]])


@pytest.mark.parametrize("provider", ["h2o", "qwen"])
def test_flow_preserves_actual_original_retrieval_reranker_and_dependency_scores(repository, provider):
    _, backend, embedder, _ = repository
    calls = []

    def score(query, snippet, **kwargs):
        calls.append(snippet.id)
        number = int(snippet.symbol.rsplit("_", 1)[1])
        return SimpleNamespace(score=100. - number, response={"synthetic": True})

    with hooks(), patch.object(backend, "_" + provider, SimpleNamespace(score=score)):
        result = backend.search(QUERY, candidates=12, max_results=8, token_budget=4096,
                                rerank=True, rerank_provider=provider, include_dependencies=True)
    assert len(calls) == len(set(calls)) == 12
    assert embedder.documents == embedder.queries == 1
    for row in result["results"]:
        number = int(row["symbol"].rsplit("_", 1)[1])
        assert row[provider + "_score"] == 100. - number
        owner_number = number if row["origin"] == "retrieval" else int(row["related_to"].split(":")[1].rsplit("_", 1)[1])
        assert row["retrieval_score"] == pytest.approx(1. / np.sqrt(1. + (owner_number / 10) ** 2))
        assert row[("qwen" if provider == "h2o" else "h2o") + "_score"] is None


@pytest.mark.parametrize("interruption", ["cancel", "source_change"])
def test_final_snapshot_and_cancel_check_still_run_after_flow_guard(repository, interruption):
    repo, backend, _, _ = repository
    original = source_allocation.guard_source_plan

    def interrupted(*args, **kwargs):
        value = original(*args, **kwargs)
        if interruption == "cancel":
            backend.cancel("during-flow-guard")
        else:
            (repo / "src/code.py").write_text("def implementation_1():\n    return 0\n")
        return value

    error = SearchCancelled if interruption == "cancel" else SourceSnapshotError
    with hooks(), patch.object(source_allocation, "guard_source_plan", interrupted):
        with pytest.raises(error):
            backend.search(QUERY, candidates=50, max_results=8, token_budget=4096,
                           include_dependencies=True, request_id="during-flow-guard")
