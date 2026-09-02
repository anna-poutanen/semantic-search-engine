import threading
import unittest
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
