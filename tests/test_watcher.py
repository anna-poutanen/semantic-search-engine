import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path

from semantic_search.watcher import PollingWatcher


class WatcherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "source.py"
        self.path.write_text("def one():\n    return 1\n")

    def snapshot(self):
        return [(path.name, hashlib.sha256(path.read_bytes()).hexdigest())
                for path in sorted(self.root.glob("*.py"))]

    def watcher(self, refresh, **kwargs):
        watcher = PollingWatcher(self.snapshot, refresh, poll_interval=0.01, debounce=0.08, **kwargs)
        self.addCleanup(watcher.stop)
        return watcher.start()

    def wait_until(self, condition, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            threading.Event().wait(0.005)
        self.fail("watcher condition timed out")

    def test_saved_edit_burst_debounces_then_deletion_and_rename_refresh(self):
        observed, refreshed = [], threading.Event()

        def refresh():
            observed.append(self.snapshot())
            refreshed.set()

        watcher = self.watcher(refresh)
        watcher.start()  # Idempotent, no duplicate polling thread.
        for i in range(3):
            self.path.write_text(f"def saved():\n    return {i}\n")
            threading.Event().wait(0.02)
        self.assertTrue(refreshed.wait(2))
        self.assertEqual(observed, [self.snapshot()])
        self.wait_until(lambda: watcher.status()["refresh_count"] == 1)
        renamed = self.path.with_name("renamed.py")
        self.path.rename(renamed)
        self.wait_until(lambda: len(observed) == 2)
        self.assertEqual(observed[-1][0][0], "renamed.py")
        renamed.unlink()
        self.wait_until(lambda: len(observed) == 3)
        self.assertEqual(observed[-1], [])
        self.assertIsNotNone(watcher.status()["last_refresh_at"])

    def test_stop_during_debounce_prevents_refresh_and_wakes_long_poll(self):
        refreshed = threading.Event()
        watcher = self.watcher(refreshed.set)
        self.path.write_text("changed")
        self.wait_until(lambda: watcher.status()["pending"])
        self.assertTrue(watcher.stop(timeout=1))
        self.assertFalse(refreshed.is_set())
        self.assertFalse(watcher.status()["running"])
        slow = PollingWatcher(self.snapshot, refreshed.set, poll_interval=30)
        slow.start()
        self.assertTrue(slow.stop(timeout=0.5))

    def test_refresh_failure_is_visible_then_retried_after_debounce(self):
        attempts, refreshed = [], threading.Event()

        def refresh():
            attempts.append(time.monotonic())
            if len(attempts) == 1:
                raise RuntimeError("partial save")
            refreshed.set()

        watcher = self.watcher(refresh)
        self.path.write_text("changed")
        self.wait_until(lambda: watcher.status()["last_error"] == "partial save")
        self.assertTrue(refreshed.wait(2))
        self.assertGreaterEqual(attempts[1] - attempts[0], 0.08)
        self.wait_until(lambda: watcher.status()["refresh_count"] == 1)
        self.assertIsNone(watcher.status()["last_error"])

    def test_stop_cooperatively_cancels_running_refresh_without_deadlock(self):
        entered, cancelled, exited = threading.Event(), threading.Event(), threading.Event()

        def refresh():
            entered.set()
            try:
                if not cancelled.wait(2):
                    raise RuntimeError("cancellation never arrived")
                raise RuntimeError("cancelled")
            finally:
                exited.set()

        watcher = self.watcher(refresh, cancel=cancelled.set)
        self.path.write_text("changed")
        self.assertTrue(entered.wait(2))
        self.assertTrue(watcher.stop(timeout=1))
        self.assertTrue(exited.is_set())
        self.assertFalse(watcher.status()["running"])
        self.assertEqual(watcher.status()["refresh_count"], 0)

    def test_save_during_refresh_is_observed_next_poll(self):
        entered, proceed = threading.Event(), threading.Event()
        snapshots = []

        def refresh():
            snapshots.append(self.snapshot())
            if len(snapshots) == 1:
                entered.set()
                if not proceed.wait(2):
                    raise RuntimeError("fixture stalled")

        watcher = self.watcher(refresh)
        self.path.write_text("first save")
        self.assertTrue(entered.wait(2))
        self.path.write_text("second save")
        proceed.set()
        self.wait_until(lambda: watcher.status()["refresh_count"] == 2)
        self.assertNotEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], self.snapshot())

    def test_invalid_timings_are_rejected(self):
        for options in ({"poll_interval": 0}, {"poll_interval": True}, {"poll_interval": float("inf")},
                        {"debounce": -0.5}, {"debounce": float("nan")}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                PollingWatcher(self.snapshot, lambda: None, **options)


if __name__ == "__main__":
    unittest.main()
