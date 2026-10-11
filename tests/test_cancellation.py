import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from semantic_search.cancellation import CancellationRegistry, SearchCancelled, validate_request_id


class CancellationTests(unittest.TestCase):
    def test_cancel_before_dispatch_is_consumed_and_cleans_up(self):
        registry = CancellationRegistry()
        registry.cancel("before_dispatch")
        with self.assertRaises(SearchCancelled):
            with registry.request("before_dispatch"):
                self.fail("Cancelled request was dispatched")
        self.assertEqual(registry.active, {})
        # A finished request ID has no lingering cancellation or active entry.
        with registry.request("before_dispatch") as check:
            check()
        self.assertEqual(registry.active, {})

    def test_queued_and_active_work_cancel_independently_of_serial_lock(self):
        registry, serialized = CancellationRegistry(), threading.Lock()
        active_started, queued_registered, release = (threading.Event() for _ in range(3))
        outcomes, work = [], []

        def worker(request_id, is_first=False):
            try:
                with registry.request(request_id) as check:
                    if not is_first:
                        queued_registered.set()
                    with serialized:
                        check()
                        work.append(request_id)
                        if is_first:
                            active_started.set()
                            release.wait(3)
                        check()
            except SearchCancelled:
                outcomes.append(request_id)

        first = threading.Thread(target=worker, args=("active", True))
        second = threading.Thread(target=worker, args=("queued",))
        first.start()
        try:
            self.assertTrue(active_started.wait(2))
            second.start()
            self.assertTrue(queued_registered.wait(2))
            registry.cancel("queued")
            registry.cancel("active")
        finally:
            release.set()
            first.join(3)
            if second.ident is not None:
                second.join(3)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(set(outcomes), {"active", "queued"})
        self.assertEqual(work, ["active"])
        self.assertEqual(registry.active, {})

    def test_duplicate_active_id_rejected_without_losing_original_request(self):
        registry = CancellationRegistry()
        with registry.request("same") as original:
            with self.assertRaisesRegex(ValueError, "already active"):
                with registry.request("same"):
                    self.fail("Duplicate was dispatched")
            original()
            registry.cancel("same")
            with self.assertRaises(SearchCancelled):
                original()
        with registry.request("same") as check:
            check()

    def test_cancel_all_and_exception_cleanup(self):
        registry = CancellationRegistry()
        with registry.request("one") as one, registry.request("two") as two:
            registry.cancel_all()
            for check in (one, two):
                with self.assertRaises(SearchCancelled):
                    check()
        self.assertEqual(registry.active, {})

        with self.assertRaisesRegex(RuntimeError, "failed work"):
            with registry.request("failure"):
                raise RuntimeError("failed work")
        with registry.request("failure") as check:
            check()
        self.assertEqual(registry.active, {})

    def test_cancel_all_tracks_overlapping_anonymous_requests_without_wire_ids(self):
        registry = CancellationRegistry()
        with registry.request(None) as first, registry.request(None) as second, \
                registry.request("named") as named:
            # Anonymous work has no externally addressable invented identity.
            self.assertEqual(set(registry.active), {"named"})
            registry.cancel_all()
            for check in (first, second, named):
                with self.assertRaises(SearchCancelled):
                    check()
        self.assertEqual(registry.active, {})
        with registry.request(None) as fresh:
            fresh()

    def test_anonymous_exception_cleanup_removes_its_event(self):
        registry = CancellationRegistry()
        with self.assertRaisesRegex(RuntimeError, "failed anonymous work"):
            with registry.request(None):
                raise RuntimeError("failed anonymous work")
        self.assertEqual(registry._anonymous, set())
        with registry.request(None) as fresh:
            fresh()
        self.assertEqual(registry._anonymous, set())

    def test_backend_close_stops_later_scores_for_anonymous_http_style_work(self):
        from semantic_search.backend import SearchBackend

        first_score, release, cancellation_requested = (threading.Event() for _ in range(3))
        calls, failures, close_failures = [], [], []

        class PausedScorer:
            def score(self, query, snippet):
                calls.append(snippet.id)
                if len(calls) == 1:
                    first_score.set()
                    if not release.wait(3):
                        raise RuntimeError("CPU scorer was not released")
                return SimpleNamespace(score=1.)

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "src").mkdir()
            (repo / "src/code.py").write_text("def alpha():\n    return 1\n\ndef beta():\n    return 2\n")
            backend = SearchBackend(repo, "src", repo / "index.sqlite3", h2o_client=PausedScorer())
            original_registry_close = backend._requests.close

            def registry_close():
                original_registry_close()
                cancellation_requested.set()

            def search():
                try:
                    backend.search("return", retrieval="bm25", rerank=True, candidates=2)
                except Exception as error:
                    failures.append(error)

            def close():
                try:
                    backend.close()
                except Exception as error:
                    close_failures.append(error)

            worker, closer = threading.Thread(target=search), threading.Thread(target=close)
            with patch.object(backend._requests, "close", registry_close):
                worker.start()
                try:
                    self.assertTrue(first_score.wait(2))
                    closer.start()
                    self.assertTrue(cancellation_requested.wait(2))
                finally:
                    release.set()
                    worker.join(3)
                    if closer.ident is not None:
                        closer.join(3)
                    backend.close()
            self.assertFalse(worker.is_alive())
            self.assertFalse(closer.is_alive())
            self.assertEqual(close_failures, [])
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], SearchCancelled)
            self.assertTrue(backend.status()["closed"])
            self.assertFalse(backend.status()["embedding_initialized"])

    def test_expired_pre_dispatch_cancel_does_not_poison_later_request(self):
        registry = CancellationRegistry()
        with patch("semantic_search.cancellation.time.monotonic", return_value=10):
            registry.cancel("expired")
        with patch("semantic_search.cancellation.time.monotonic", return_value=41):
            with registry.request("expired") as check:
                check()
        self.assertEqual(registry.cancelled, {})

    def test_invalid_request_ids_fail_before_registration(self):
        for request_id in (None, True, 1, "", "x" * 65, "../id", "id space", "café", "id\n"):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                validate_request_id(request_id)
        validate_request_id("abc-XYZ_012")
        registry = CancellationRegistry()
        with self.assertRaises(ValueError):
            registry.cancel("bad/id")
        self.assertEqual((registry.active, registry.cancelled), ({}, {}))


if __name__ == "__main__":
    unittest.main()
