"""CPU checks of full-pool/prefix ordering, packing, cancellation and overlays."""
import subprocess
import hashlib
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.context import compact_result


class Embedder:
    metadata = {"id": "injected_cpu", "dimensions": 2}
    def embed_documents(self, documents):
        return np.asarray([[1., .1] for _ in documents])
    def embed_query(self, query):
        return np.asarray([1., 0.])
    def close(self):
        pass


class Scorer:
    def __init__(self):
        self.calls = []
        self.after = None
        self.score_value = 2.
    def score(self, query, snippet, **kwargs):
        self.calls.append((query, snippet, kwargs))
        if self.after:
            self.after()
        return SimpleNamespace(score=self.score_value, response={"cpu_injected": True})


class PreparedRetriever:
    def __init__(self, pairs):
        self.pairs, self.limits = pairs, []
    def search(self, *args, **kwargs):
        limit = kwargs.get("limit", args[-1])
        self.limits.append(limit)
        return self.pairs[:limit]


@pytest.fixture
def backend(tmp_path):
    source = "from helpers import helper\n\n" + "\n".join(
        f"def implementation_{i}():\n    return helper({i})\n" for i in range(60))
    (tmp_path / "code.py").write_text(source)
    (tmp_path / "helpers.py").write_text("def helper(value):\n    return value + 1\n")
    h2o, qwen = Scorer(), Scorer()
    backend = SearchBackend(tmp_path, ".", tmp_path / "index.sqlite3", embedder=Embedder(),
                            h2o_client=h2o, qwen_client=qwen)
    backend.search("implementation", max_results=1)
    yield backend, h2o, qwen
    backend.close()


def controlled_pool(backend):
    snippets = [snippet for snippet in backend._snippets if snippet.symbol.startswith("implementation_")]
    snippets.sort(key=lambda snippet: int(snippet.symbol.split("_")[-1]), reverse=True)
    return [(snippet, float(i)) for i, snippet in enumerate(snippets[:50])]


@pytest.mark.parametrize("retrieval", ["dense", "hybrid"])
def test_qwen_retrieves_full50_scores_only_prefix_preserves_ties_and_has_no_tail(backend, retrieval):
    service, h2o, qwen = backend
    pool = controlled_pool(service)
    spy = PreparedRetriever(pool)
    stats = service._last_stats
    with patch.object(service, "_refresh", return_value=stats):
        if retrieval == "dense":
            service._dense = spy
        else:
            service._hybrid = spy
        response = service.search("implementation", retrieval=retrieval, rerank=True,
            rerank_provider="qwen", candidates=3, max_results=8, include_dependencies=False)
    assert spy.limits == [50]
    assert [call[1].id for call in qwen.calls] == [snippet.id for snippet, _ in pool[:3]]
    assert [row["id"] for row in response["results"]] == [snippet.id for snippet, _ in pool[:3]]
    assert response["retrieval_candidate_windows"] == 50 and response["candidate_windows"] == 3
    assert response["reranker_batch_size"] == 1 and h2o.calls == []
    assert all(row["h2o_score"] is None and row["qwen_score"] == 2 for row in response["results"])


def test_qwen_exact_compact_packing_matches_existing_packer_including_dependencies(backend):
    service, _, _ = backend
    pool = controlled_pool(service)
    stats = service._last_stats
    settings = dict(max_results=8, include_dependencies=True, source_budget=12000, token_budget=4096)
    with patch.object(service, "_refresh", return_value=stats):
        service._dense = PreparedRetriever(pool)
        qwen = service.search("implementation", rerank=True, rerank_provider="qwen", candidates=24, **settings)
        service._dense = PreparedRetriever(pool[:24])
        original_packing = service.search("implementation", rerank=False, candidates=50, **settings)
    assert compact_result(qwen) == compact_result(original_packing)
    dependency = next(row for row in qwen["results"] if row["origin"] == "dependency")
    assert dependency["symbol"] == "helper" and dependency["qwen_score"] is None


def test_legacy_h2o_and_disabled_qwen_are_preserved(backend):
    service, h2o, qwen = backend
    service.search("implementation", rerank=True, candidates=2)
    assert len(h2o.calls) == 2 and qwen.calls == []
    service.search("implementation", rerank=False, rerank_provider="qwen")
    assert qwen.calls == []


def test_cancellation_between_singleton_forwards_stops_remaining_pairs_and_next_request_works(backend):
    service, _, qwen = backend
    qwen.after = lambda: service.cancel("cancel-qwen")
    with pytest.raises(SearchCancelled):
        service.search("implementation", rerank=True, rerank_provider="qwen", candidates=24,
                       request_id="cancel-qwen")
    assert len(qwen.calls) == 1
    qwen.after = None
    service.search("implementation", rerank=True, rerank_provider="qwen", candidates=2)
    assert len(qwen.calls) == 3


def test_source_change_during_scoring_prevents_stale_delivery(backend):
    service, _, qwen = backend
    qwen.after = lambda: (service.repo / "code.py").write_text("def changed():\n    return 1\n")
    with pytest.raises(SourceSnapshotError):
        service.search("implementation", rerank=True, rerank_provider="qwen", candidates=1)


def test_nonfinite_injected_scores_and_unconfigured_provider_fail(backend):
    service, _, qwen = backend
    qwen.score_value = float("nan")
    with pytest.raises(ValueError, match="nonfinite"):
        service.search("implementation", rerank=True, rerank_provider="qwen")
    service._qwen = None
    with pytest.raises(ValueError, match="not configured"):
        service.search("implementation", rerank=True, rerank_provider="qwen")


def test_unproved_shutdown_blocks_later_embedding_work_even_without_reranking(backend):
    service, _, qwen = backend
    qwen.safe_for_embedding = False
    with patch.object(service._embedder, "embed_query") as embedding:
        with pytest.raises(RuntimeError, match="shutdown unproved"):
            service.search("implementation", rerank=False)
    embedding.assert_not_called()


def test_overlay_borrows_qwen_and_scores_exact_editor_source_without_closing_owner(backend):
    service, _, qwen = backend
    subprocess.run(["git", "init", "-q", str(service.repo)], check=True)
    subprocess.run(["git", "-C", str(service.repo), "add", "code.py", "helpers.py"], check=True)
    source = "def editor_implementation():\n    return 'unsaved editor source'\n"
    response = service.search("implementation", rerank=True, rerank_provider="qwen", candidates=2,
        overlays=[{"path": "code.py", "version": 5, "text": source,
                   "content_hash": hashlib.sha256(source.encode()).hexdigest()}])
    assert any(call[1].text == source for call in qwen.calls)
    assert any(row.get("source_origin") == "overlay" for row in response["results"])
    assert service._qwen is qwen
