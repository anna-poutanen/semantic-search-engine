"""Actual toy-backend checks for the explicit task-evidence research adapter."""
from contextlib import closing
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

import numpy as np
import pytest

from semantic_search import backend as backend_module, selection
from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import SearchCancelled
from semantic_search.context import compact_result, serialize_context
from semantic_search.task_evidence_adapter import TaskEvidenceAdapter


QUERY = "shared behavior implementation"


class FixedEmbedder:
    metadata = {"id": "invented-task-evidence-fixture", "revision": "fixed", "dimensions": 2}

    def __init__(self):
        self.documents = self.queries = 0
        self.after_query = lambda: None

    def embed_documents(self, texts):
        self.documents += 1
        return np.asarray([[1., 0.] for _ in texts]).reshape(-1, 2)

    def embed_query(self, query, *, forward_observer=None):
        self.queries += 1
        self.after_query()
        return np.asarray([1., 0.])

    def close(self):
        pass


def repository(tmp_path):
    repo = tmp_path / "invented-repository"
    (repo / "src").mkdir(parents=True)
    (repo / "zz").mkdir()
    control = "\n".join(
        f"def implementation_{number}():\n    return 'shared behavior {number}'\n"
        for number in range(1, 9))
    (repo / "src/control.py").write_text(control)
    (repo / "zz/extra.py").write_text(
        "def complementary_implementation():\n"
        "    return 'shared behavior complementary'\n")
    return repo


def request(*, needed=...):
    value = {
        "query": QUERY,
        "retrieval": "dense",
        "rerank": False,
        "rerank_provider": "h2o",
        "candidates": 50,
        "max_results": 8,
        "source_budget": 12000,
        "token_budget": 4096,
        "include_dependencies": True,
        "overlays": [],
    }
    if needed is not ...:
        value["needed_evidence"] = needed
    return value


def hooked_backend(repo, db_path, embedder, adapter):
    return SearchBackend(
        repo, ".", db_path, embedder=embedder,
        research_postprocessor=adapter.postprocess_context,
    )


def test_control_bytes_treatment_capture_and_one_shared_retrieval_per_search(tmp_path):
    repo = repository(tmp_path)
    ordinary_embedder, research_embedder = FixedEmbedder(), FixedEmbedder()
    captures = []

    def capture(record):
        captures.append(deepcopy(record))
        record.clear()

    adapter = TaskEvidenceAdapter(capture=capture)
    with closing(SearchBackend(repo, ".", tmp_path / "ordinary.sqlite3",
                               embedder=ordinary_embedder)) as ordinary, \
            closing(hooked_backend(repo, tmp_path / "research.sqlite3",
                                   research_embedder, adapter)) as research:
        expected = ordinary.search(**request())
        research.refresh()
        orders = []
        original_search = research._dense.search

        def observed(vector, limit):
            rows = original_search(vector, limit)
            orders.append([(row.id, score) for row, score in rows])
            return rows

        with patch.object(research._dense, "search", side_effect=observed) as search:
            control, control_packet = adapter.execute(research, request())
            treatment, treatment_packet = adapter.execute(
                research, request(needed=["implementation"]))

        assert search.call_count == 2 and orders[0] == orders[1]
        assert serialize_context(control_packet) == serialize_context(compact_result(control))
        assert serialize_context(control_packet) == serialize_context(compact_result(expected))
        assert "task_evidence" not in control and "task_evidence" not in treatment
        assert treatment_packet["task_evidence"]["requested"] == ["implementation"]
        assert treatment["results"][-1]["path"] == "zz/extra.py"
        assert research_embedder.documents == 1 and research_embedder.queries == 2
        assert ordinary_embedder.documents == ordinary_embedder.queries == 1

    assert len(captures) == 2
    assert captures[0]["needed_evidence"] is None
    assert captures[0]["selection_receipt"] is captures[0]["packing_receipt"] is None
    assert captures[0]["packet"] == control_packet
    assert captures[1]["needed_evidence"] == ["implementation"]
    assert captures[1]["selection_receipt"]["schema"] == "task_evidence_plan_v1"
    assert captures[1]["packing_receipt"]["schema"] == "task_evidence_packing_v1"
    assert captures[1]["packet"] == treatment_packet
    assert captures[0]["candidate_inventory"] == captures[1]["candidate_inventory"]
    assert [(row["id"], row["retrieval_score"])
            for row in captures[0]["candidate_inventory"]] == orders[0]
    assert captures[0]["request_receipt"] == captures[1]["request_receipt"]
    assert all(record["backend_search_s"] >= 0 for record in captures)
    json.dumps(captures, allow_nan=False)


def test_execute_does_not_mutate_the_resolved_request(tmp_path):
    repo = repository(tmp_path)
    adapter = TaskEvidenceAdapter()
    with closing(hooked_backend(repo, tmp_path / "research.sqlite3",
                                FixedEmbedder(), adapter)) as backend:
        bound = request()
        original = deepcopy(bound)
        response, _ = adapter.execute(backend, bound)
    assert response["query"] == QUERY
    assert response["candidate_windows"] > 1
    assert bound == original


def test_manual_backend_search_cannot_bypass_the_bound_execution(tmp_path):
    repo = repository(tmp_path)
    adapter = TaskEvidenceAdapter()
    changed = request()
    changed["candidates"] = 49
    with closing(hooked_backend(repo, tmp_path / "research.sqlite3",
                                FixedEmbedder(), adapter)) as backend:
        with pytest.raises(RuntimeError, match="requires TaskEvidenceAdapter.execute"):
            with adapter._bind(request()):
                backend.search(**changed)
        response, packet = adapter.execute(backend, request())
    assert packet == compact_result(response)
    assert backend_module.select_context is selection.definition_context


def test_failed_snapshot_request_clears_bound_condition_for_later_control(tmp_path):
    repo = repository(tmp_path)
    embedder, adapter = FixedEmbedder(), TaskEvidenceAdapter()
    original = (repo / "src/control.py").read_text()
    with closing(hooked_backend(repo, tmp_path / "research.sqlite3", embedder, adapter)) as backend:
        backend.refresh()

        def change_source():
            (repo / "src/control.py").write_text(original + "\n# changed during query\n")

        embedder.after_query = change_source
        with pytest.raises(SourceSnapshotError):
            adapter.execute(backend, request(needed=["implementation"]))
        (repo / "src/control.py").write_text(original)
        embedder.after_query = lambda: None
        response, packet = adapter.execute(backend, request())
        assert packet == compact_result(response)
    assert embedder.queries == 2


def test_search_cancellation_identity_is_preserved_and_alias_is_restored(tmp_path):
    repo = repository(tmp_path)
    embedder, adapter = FixedEmbedder(), TaskEvidenceAdapter()
    cancelled = SearchCancelled("invented cancellation")

    def cancel_query():
        raise cancelled

    embedder.after_query = cancel_query
    with closing(hooked_backend(repo, tmp_path / "research.sqlite3", embedder, adapter)) as backend:
        with pytest.raises(SearchCancelled) as caught:
            adapter.execute(backend, request())
    assert caught.value is cancelled
    assert backend_module.select_context is selection.definition_context


def test_execute_rejects_a_backend_installed_with_another_adapter(tmp_path):
    repo = repository(tmp_path)
    installed, other = TaskEvidenceAdapter(), TaskEvidenceAdapter()
    with closing(hooked_backend(repo, tmp_path / "research.sqlite3",
                                FixedEmbedder(), installed)) as backend:
        with pytest.raises(ValueError, match="not installed with this"):
            other.execute(backend, request())


def test_normal_unfinished_exit_fails_and_restores_global_selector():
    adapter = TaskEvidenceAdapter()
    with pytest.raises(RuntimeError, match="without executing"):
        with adapter._bind(request()):
            pass
    assert backend_module.select_context is selection.definition_context


def test_alias_tamper_is_restored_without_masking_the_primary_error():
    adapter = TaskEvidenceAdapter()
    primary = RuntimeError("primary failure")
    with pytest.raises(RuntimeError) as caught:
        with adapter._bind(request()):
            backend_module.select_context = lambda *args, **kwargs: ([], [], {})
            raise primary
    assert caught.value is primary
    assert "selector alias changed" in " ".join(getattr(primary, "__notes__", ()))
    assert backend_module.select_context is selection.definition_context


def test_completed_but_tampered_binding_is_not_captured(tmp_path):
    repo = repository(tmp_path)
    captures = []
    adapter = TaskEvidenceAdapter(capture=captures.append)
    with closing(hooked_backend(repo, tmp_path / "research.sqlite3",
                                FixedEmbedder(), adapter)) as backend:
        with pytest.raises(RuntimeError, match="selector alias changed"):
            with adapter._bind(request()) as run:
                run._execute(backend)
                backend_module.select_context = lambda *args, **kwargs: ([], [], {})
    assert captures == []
    assert backend_module.select_context is selection.definition_context


def test_distinct_adapters_serialize_selector_patch_bindings():
    first_entered, second_attempted = Event(), Event()
    second_entered, release_first = Event(), Event()
    failures = []
    first_error, second_error = RuntimeError("first"), RuntimeError("second")

    def bind_first():
        try:
            with TaskEvidenceAdapter()._bind(request()):
                first_entered.set()
                if not release_first.wait(2):
                    raise AssertionError("first binding was not released")
                raise first_error
        except BaseException as error:
            if error is not first_error:
                failures.append(error)

    def bind_second():
        if not first_entered.wait(2):
            failures.append(AssertionError("first binding did not start"))
            return
        second_attempted.set()
        try:
            with TaskEvidenceAdapter()._bind(request()):
                second_entered.set()
                raise second_error
        except BaseException as error:
            if error is not second_error:
                failures.append(error)

    first = Thread(target=bind_first)
    second = Thread(target=bind_second)
    first.start()
    second.start()
    assert second_attempted.wait(2)
    assert not second_entered.wait(.05)
    release_first.set()
    first.join(2)
    second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert second_entered.is_set() and not failures
    assert backend_module.select_context is selection.definition_context


def test_needed_evidence_is_absent_from_every_production_request_interface():
    from semantic_search.mcp_server import SearchArguments
    from semantic_search import cli, mcp_server, server

    assert "needed_evidence" not in inspect.signature(SearchBackend.search).parameters
    assert "needed_evidence" not in SearchArguments.model_json_schema()["properties"]
    for module in (cli, mcp_server, server):
        source = Path(module.__file__).read_text()
        assert "needed_evidence" not in source


def test_capture_receipts_do_not_enter_standard_backend_source_or_hashes(tmp_path):
    repo = repository(tmp_path)
    adapter = TaskEvidenceAdapter()
    with closing(hooked_backend(repo, tmp_path / "research.sqlite3",
                                FixedEmbedder(), adapter)) as backend:
        response, packet = adapter.execute(
            backend, request(needed=["implementation"]))
    assert "task_evidence" not in response
    for row in response["results"]:
        source = (repo / row["path"]).read_text()
        lines = source.splitlines(keepends=True)
        assert row["source"] == "".join(lines[row["start_line"] - 1:row["end_line"]])
        assert row["source_hash"] == hashlib.sha256(row["source"].encode()).hexdigest()
    assert packet["task_evidence"]
