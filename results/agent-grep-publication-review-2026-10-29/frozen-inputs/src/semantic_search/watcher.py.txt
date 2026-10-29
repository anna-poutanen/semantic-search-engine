"""Debounced polling of saved source snapshots with explicit thread lifecycle."""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone

_UNSET = object()


class PollingWatcher:
    """Refresh after a source fingerprint stays unchanged through the debounce.

    ``snapshot`` returns a comparable source fingerprint; ``refresh`` promotes a
    freshly validated snapshot. Exceptions are recorded and retried while the
    watcher runs. ``cancel`` can request cooperative cancellation of a running
    refresh during stop. Call stop before acquiring the backend's model lock.
    """

    def __init__(self, snapshot, refresh, *, poll_interval=0.25, debounce=0.5, cancel=None):
        for value, name in ((poll_interval, "poll_interval"), (debounce, "debounce")):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0 or (name == "poll_interval" and value == 0)):
                requirement = "positive" if name == "poll_interval" else "nonnegative"
                raise ValueError(f"{name} must be finite and {requirement}")
        if not callable(snapshot) or not callable(refresh) or (cancel is not None and not callable(cancel)):
            raise ValueError("watcher callbacks must be callable")
        self.snapshot, self.refresh, self.cancel = snapshot, refresh, cancel
        self.poll_interval, self.debounce = poll_interval, debounce
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._pending = False
        self._refreshes = 0
        self._last_error = None
        self._last_refresh_at = None

    def start(self, *, initial_snapshot=_UNSET):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            self._stop.clear()
            self._pending = False
            try:
                baseline = self.snapshot() if initial_snapshot is _UNSET else initial_snapshot
            except Exception as error:
                baseline = _UNSET
                self._last_error = str(error)
            self._thread = threading.Thread(target=self._run, args=(baseline,),
                                            name="semantic-search-watcher", daemon=True)
            self._thread.start()
        return self

    def _run(self, applied):
        observed, changed_at = applied, None
        while not self._stop.wait(self.poll_interval):
            try:
                current = self.snapshot()
            except Exception as error:
                with self._lock:
                    self._last_error = str(error)
                # A failed observation interrupts the period of proven stability.
                changed_at = None
                continue
            now = time.monotonic()
            if current != observed:
                observed, changed_at = current, now
            if current == applied:
                changed_at = None
                with self._lock:
                    self._pending = False
                continue
            if changed_at is None:
                changed_at = now
            with self._lock:
                self._pending = True
            if now - changed_at < self.debounce or self._stop.is_set():
                continue
            try:
                self.refresh()
            except Exception as error:
                with self._lock:
                    self._last_error = str(error)
                changed_at = time.monotonic()
                continue
            # A save during inference will be observed in the next poll; only
            # the fingerprint that triggered this validated refresh is acknowledged.
            applied, changed_at = current, None
            with self._lock:
                self._pending = False
                self._refreshes += 1
                self._last_error = None
                self._last_refresh_at = datetime.now(timezone.utc).isoformat()

    def stop(self, timeout=None):
        """Signal cancellation and join without holding a lock needed by refresh."""
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                    or not math.isfinite(timeout) or timeout < 0):
            raise ValueError("timeout must be finite and nonnegative")
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive() and self.cancel is not None:
            self.cancel()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if not running:
                self._pending = False
        return not running

    def status(self):
        with self._lock:
            return {"running": self._thread is not None and self._thread.is_alive(),
                    "pending": self._pending, "refresh_count": self._refreshes,
                    "last_error": self._last_error, "last_refresh_at": self._last_refresh_at,
                    "poll_interval_s": self.poll_interval, "debounce_s": self.debounce}
