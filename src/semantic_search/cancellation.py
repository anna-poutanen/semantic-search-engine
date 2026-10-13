"""Request cancellation independent of the serialized GPU lock."""
import re
import threading
import time
from contextlib import contextmanager


class SearchCancelled(RuntimeError):
    pass


def validate_request_id(request_id):
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
        raise ValueError("request_id must contain 1–64 letters, digits, underscores or hyphens")


class CancellationRegistry:
    def __init__(self):
        self.lock = threading.Lock()
        self.active, self.cancelled = {}, {}

    def _prune(self):
        now = time.monotonic()
        self.cancelled = {key: expiry for key, expiry in self.cancelled.items() if expiry > now}

    def cancel(self, request_id):
        validate_request_id(request_id)
        with self.lock:
            self._prune()
            event = self.active.get(request_id)
            if event is not None:
                event.set()
            else:
                if len(self.cancelled) >= 4096:
                    raise ValueError("Too many pending cancellation requests")
                self.cancelled[request_id] = time.monotonic() + 30
        return {"request_id": request_id, "cancelled": True}

    def cancel_all(self):
        with self.lock:
            for event in self.active.values():
                event.set()

    @contextmanager
    def request(self, request_id):
        event = threading.Event()
        if request_id is not None:
            validate_request_id(request_id)
            with self.lock:
                self._prune()
                if request_id in self.active:
                    raise ValueError("request_id is already active")
                if self.cancelled.pop(request_id, None) is not None:
                    event.set()
                self.active[request_id] = event
        def check():
            if event.is_set():
                raise SearchCancelled("Search request was cancelled")
        try:
            check()
            yield check
        finally:
            if request_id is not None:
                with self.lock:
                    if self.active.get(request_id) is event:
                        self.active.pop(request_id)
