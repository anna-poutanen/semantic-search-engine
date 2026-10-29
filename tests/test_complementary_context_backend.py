"""Actual backend/MCP calls exercise research hooks using CPU fixture vectors."""
from contextlib import closing
import hashlib
import json
import re
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from semantic_search import backend as backend_module, complementary_context as candidate, context
from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.source import physical_lines

QUERY = "load alpha payload and render beta template and persist gamma cache"


class Embedder:
    metadata = {"id": "complementary-backend-fixture", "revision": "fixed", "dimensions": 2}
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
    (tmp_path / "src").mkdir()
    (tmp_path / "src/code.py").write_text("from helper import implementation_11\n\n" + "\n".join(
        f"def implementation_{n}():\n    return " +
        ("implementation_11(), 'load alpha payload'\n" if n == 1 else "'ordinary value'\n")
        for n in range(1, 9)))
    (tmp_path / "src/helper.py").write_text(
        "def implementation_9():\n    return 'render beta template'\n\n"
        "def implementation_10():\n    return 'persist gamma cache'\n\n"
        "def implementation_11():\n    return 'ordinary helper'\n")
    embedder = Embedder()
    with closing(SearchBackend(tmp_path, "src", tmp_path / "index.sqlite3", embedder=embedder)) as backend:
        yield tmp_path, backend, embedder


def hooks(postprocessor=None):
    return patch.multiple(backend_module, select_context=candidate.complementary_context,
        postprocess_context=postprocessor or candidate.guard_complementary_context)


def assert_literal(repo, response, texts=None):
    for row in response["results"]:
        full = texts.get(row["path"], (repo / row["path"]).read_bytes().decode()) if texts else (repo / row["path"]).read_bytes().decode()
        lines = physical_lines(full)
        assert row["source"] == "".join(lines[row["start_line"]-1:row["end_line"]])
        assert row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()
        assert row["content_hash"] == hashlib.sha256("".join(lines[row["start_line"]-1:row["window_end_line"]]).encode()).hexdigest()


@pytest.mark.parametrize("query", [QUERY, "simple implementation"])
@pytest.mark.parametrize("limits", [
    {"max_results": 8, "source_budget": 12000, "token_budget": 4096},
    {"max_results": 8, "source_budget": 150, "token_budget": 512},
    {"max_results": 2, "source_budget": 60, "token_budget": 512},
])
def test_actual_backend_uses_one_query_and_actual_zero_or_one_reference_factory(repository, query, limits):
    pytest.importorskip("mcp")
    from semantic_search.mcp_server import SearchArguments, _validate_context
    repo, backend, embedder = repository
    original_selector, original_postprocessor = backend_module.select_context, backend_module.postprocess_context
    control = backend.search(query, include_dependencies=True, **limits)
    plans, guards, references = [], [], []
    planner, guard = candidate.complementary_plan, candidate.guard_complementary_plan
    def plan(*args, **kwargs):
        result = planner(*args, **kwargs)
        plans.append(result[3])
        return result
    def guarded(reference, proposal, receipt):
        result = guard(reference, proposal, receipt)
        guards.append((reference is proposal, result[1]))
        return result
    def postprocess(response, candidates, **kwargs):
        actual_factory = kwargs["reference_factory"]
        def factory():
            value = actual_factory()
            references.append(value)
            return value
        kwargs["reference_factory"] = factory
        return candidate.guard_complementary_context(response, candidates, **kwargs)
    before = embedder.queries
    with hooks(postprocess), patch.object(candidate, "complementary_plan", plan), \
            patch.object(candidate, "guard_complementary_plan", guarded):
        result = backend.search(query, include_dependencies=True, **limits)
    assert len(plans) == len(guards) == 1 and len(references) == int(plans[0]["accepted"])
    assert guards[0][0] is (not plans[0]["accepted"])
    assert embedder.documents == 1 and embedder.queries == before + 1
    assert result["indexstats"]["memory_cache_hit"]
    assert_literal(repo, result)
    compact = context.compact_result(result)
    assert json.loads(_validate_context(compact, SearchArguments(query=query, include_dependencies=True, **limits))) == compact
    if not plans[0]["accepted"]:
        assert compact == context.compact_result(control)
    if limits["token_budget"] == 4096 and plans[0]["accepted"]:
        assert guards[0][1]["accepted"]
        assert {"implementation_9", "implementation_10"} <= {row["symbol"] for row in result["results"]}
    assert not {"source_plan", "complementary_plan", "_complementary_guard"} & result.keys()
    assert backend_module.select_context is original_selector and backend_module.postprocess_context is original_postprocessor


@pytest.mark.parametrize("provider", ["h2o", "qwen"])
def test_original_optional_reranker_and_inherited_dependency_scalars_are_preserved(repository, provider):
    repo, backend, embedder = repository
    calls = []
    def score(query, snippet, **kwargs):
        calls.append(snippet.id)
        return SimpleNamespace(score=100. - int(snippet.symbol.rsplit("_", 1)[1]), response={"synthetic": True})
    with hooks(), patch.object(backend, "_" + provider, SimpleNamespace(score=score)):
        result = backend.search(QUERY, candidates=11, max_results=8, token_budget=4096,
            rerank=True, rerank_provider=provider, include_dependencies=True)
    assert len(calls) == len(set(calls)) == 11
    assert embedder.documents == embedder.queries == 1
    for row in result["results"]:
        number = int(row["symbol"].rsplit("_", 1)[1])
        assert row[provider + "_score"] == 100. - number
        owner = number if row["origin"] == "retrieval" else 1
        assert row["retrieval_score"] == pytest.approx(1. / np.sqrt(1. + (owner / 10) ** 2))
    dependency = next(row for row in result["results"] if row["symbol"] == "implementation_11")
    assert dependency["origin"] == "dependency" and "implementation_1:" in dependency["related_to"]
    assert_literal(repo, result)


def test_overlay_uses_request_source_hashes_and_never_mutates_saved_index(repository):
    repo, backend, _ = repository
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "src"], check=True)
    text = (repo / "src/code.py").read_text().replace("load alpha payload", "load alpha payload unsaved")
    digest = hashlib.sha256(text.encode()).hexdigest()
    with hooks():
        result = backend.search(QUERY, max_results=8, include_dependencies=True, token_budget=4096,
            overlays=[{"path": "src/code.py", "text": text, "version": 23, "content_hash": digest}])
        saved = backend.search(QUERY, max_results=8, include_dependencies=True, token_budget=4096)
    assert any("unsaved" in row["source"] for row in result["results"])
    for row in result["results"]:
        if row["path"] == "src/code.py":
            assert row["source_origin"] == "overlay" and row["buffer_version"] == 23 and row["buffer_hash"] == digest
        else:
            assert row["source_origin"] == "saved" and "buffer_hash" not in row
    assert_literal(repo, result, {"src/code.py": text})
    assert all("unsaved" not in row["source"] for row in saved["results"])


@pytest.mark.parametrize("interruption", ["cancel_before", "cancel_after_guard", "source_change_after_guard"])
def test_cancellation_and_final_source_snapshot_still_enclose_new_research_hooks(repository, interruption):
    repo, backend, embedder = repository
    backend.refresh()
    if interruption == "cancel_before":
        embedder.after_query = lambda: backend.cancel("complementary-interruption")
    original = candidate.guard_complementary_plan
    def interrupted(*args):
        result = original(*args)
        if interruption == "cancel_after_guard":
            backend.cancel("complementary-interruption")
        elif interruption == "source_change_after_guard":
            (repo / "src/helper.py").write_text("def implementation_9():\n    return 99\n")
        return result
    with hooks(), patch.object(candidate, "guard_complementary_plan", interrupted):
        with pytest.raises(SourceSnapshotError if interruption.startswith("source") else SearchCancelled):
            backend.search(QUERY, max_results=8, include_dependencies=True, token_budget=4096,
                           request_id="complementary-interruption")
