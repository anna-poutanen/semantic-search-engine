"""Deterministic shutdown regressions using CPU stubs and saved local source."""

import threading
from contextlib import ExitStack
from unittest.mock import patch

import numpy as np
import pytest

from semantic_search.backend import SearchBackend
from semantic_search.cancellation import CancellationRegistry, SearchCancelled


class _CPUEmbedder:
    metadata = {"id": "shutdown-cpu-stub", "revision": "fixed", "dimensions": 2}

    def __init__(self):
        self.query_calls = []

    def embed_documents(self, texts):
        return np.asarray([[1.0, 0.1] for _ in texts])

    def embed_query(self, query):
        self.query_calls.append(query)
        return np.asarray([1.0, 0.0])

    def close(self):
        pass


@pytest.mark.parametrize("late_id", [None, "late-named"])
def test_registry_close_cancels_active_requests_and_rejects_late_entries(late_id):
    registry = CancellationRegistry()
    with ExitStack() as stack:
        active_checks = [stack.enter_context(registry.request(request_id))
                         for request_id in (None, "active-named")]
        registry.close()
        registry.close()
        for check in active_checks:
            with pytest.raises(SearchCancelled):
                check()
        with pytest.raises(SearchCancelled):
            with registry.request(late_id):
                pytest.fail("Shutdown admitted a new request")
    assert registry.active == {}
    assert registry._anonymous == set()


def test_cancel_all_remains_reusable_for_named_and_anonymous_requests():
    registry = CancellationRegistry()
    with registry.request(None) as anonymous, registry.request("named") as named:
        registry.cancel_all()
        for check in (anonymous, named):
            with pytest.raises(SearchCancelled):
                check()
    with registry.request(None) as anonymous, registry.request("named") as named:
        anonymous()
        named()
    assert registry.active == {}
    assert registry._anonymous == set()


@pytest.mark.parametrize("request_id", [None, "late-refresh"])
def test_backend_shutdown_rejects_registration_before_obtaining_backend_lock(tmp_path, request_id):
    (tmp_path / "src").mkdir()
    backend = SearchBackend(tmp_path, "src")
    stopping, resume_close = threading.Event(), threading.Event()
    work, close_failures = [], []

    class PausedWatcher:
        def stop(self):
            # close has swept the registry but has not obtained the model lock.
            stopping.set()
            if not resume_close.wait(3):
                raise RuntimeError("Test did not release the paused watcher")

    def cpu_refresh(*, cancel_check):
        cancel_check()
        work.append(request_id)
        return {"elapsed_s": 0.0}

    def close():
        try:
            backend.close()
        except Exception as error:
            close_failures.append(error)

    backend._watcher = PausedWatcher()
    closer = threading.Thread(target=close)
    with patch.object(backend, "_refresh", cpu_refresh):
        closer.start()
        try:
            assert stopping.wait(2)
            with pytest.raises(SearchCancelled):
                backend.refresh(request_id=request_id)
        finally:
            resume_close.set()
            closer.join(3)
            backend._watcher = None
            if not closer.is_alive():
                backend.close()
    assert not closer.is_alive()
    assert close_failures == []
    assert work == []
    assert backend._closed
    assert backend._requests.active == {}
    assert backend._requests._anonymous == set()


@pytest.mark.parametrize("request_id", [None, "refresh-boundary"])
def test_final_refresh_snapshot_cancellation_prevents_query_embedding(tmp_path, request_id):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/code.py").write_text("def alpha():\n    return 1\n")
    embedder = _CPUEmbedder()
    backend = SearchBackend(tmp_path, "src", tmp_path / "index.sqlite3", embedder=embedder)
    original_confirm = backend._confirm_snapshot
    snapshot_checks = []

    def confirm_and_cancel(expected):
        original_confirm(expected)
        snapshot_checks.append(expected)
        # A cold refresh confirms extraction, before commit, and after commit.
        # Cancellation during the final real source check must precede query work.
        if len(snapshot_checks) == 3:
            backend._requests.cancel_all()

    try:
        with patch.object(backend, "_confirm_snapshot", confirm_and_cancel):
            with pytest.raises(SearchCancelled):
                backend.search("alpha", request_id=request_id)
        assert len(snapshot_checks) == 3
        assert embedder.query_calls == []
        assert backend._requests.active == {}
        assert backend._requests._anonymous == set()
    finally:
        backend.close()
