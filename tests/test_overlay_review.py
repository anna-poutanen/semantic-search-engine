"""Independent CPU checks for request-only cache and editor-buffer isolation."""
import copy
import hashlib
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np

from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.cancellation import CancellationRegistry, SearchCancelled
from semantic_search.client import StaleBufferError, ensure_overlay_current
from semantic_search.context import compact_result, encoding, serialize_context
from semantic_search.overlays import MAX_OVERLAY_FILE_BYTES, normalize_overlays
from semantic_search.source import physical_lines


def buffer(path, text, version=1):
    return {"path": path, "text": text, "version": version,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}


class ReviewEmbedder:
    metadata = {"id": "independent-cpu", "revision": "fixed", "dimensions": 3}

    def __init__(self):
        self.documents, self.queries, self.closes = [], [], 0
        self.on_documents = None

    def embed_documents(self, texts):
        self.documents.append(list(texts))
        if self.on_documents:
            self.on_documents()
        return np.asarray([[1., 1. + ("buffer_name" in text), .5] for text in texts])

    def embed_query(self, query):
        self.queries.append(query)
        return np.asarray([1., 1., .5])

    def close(self):
        self.closes += 1


class CountingRegistry(CancellationRegistry):
    def __init__(self):
        super().__init__()
        self.registrations = []

    @contextmanager
    def request(self, request_id):
        self.registrations.append(request_id)
        with super().request(request_id) as check:
            yield check


class OverlayIndependentReview(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name).resolve() / "project"
        (self.repo / "src").mkdir(parents=True)
        self.saved = "def saved_name():\r\n    return 'saved'\r\n"
        (self.repo / "src/main.py").write_bytes(self.saved.encode())
        (self.repo / "src/helper.py").write_text("def helper_name():\n    return 'helper'\n")
        self.git("init", "-q")
        self.git("add", "src")
        self.git("-c", "user.name=CPU Review", "-c", "user.email=review@example.invalid",
                 "commit", "-qm", "fixture")
        self.model = ReviewEmbedder()
        self.database = self.repo / "cache" / "saved.sqlite3"
        self.backend = SearchBackend(self.repo, "src", self.database, embedder=self.model)
        self.addCleanup(self.backend.close)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def disk_cache(self):
        # No SQLite inspection here: opening it can itself create/delete sidecars.
        return {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.database.parent.glob(self.database.name + "*")}

    def source_bytes(self):
        return {path.name: path.read_bytes() for path in (self.repo / "src").iterdir()}

    def test_warm_overlay_does_not_create_sidecars_or_touch_saved_objects(self):
        self.backend.refresh()
        sources, cache, status = self.source_bytes(), self.disk_cache(), copy.deepcopy(self.backend.status())
        self.assertEqual(set(cache), {self.database.name})
        snapshots = tuple(getattr(self.backend, attr) for attr in
                          ("_manifest", "_vectors_manifest", "_snippets", "_dense", "_references", "_index"))
        entry = buffer("src/main.py", "def buffer_name():\n    return 'unsaved'\n", 17)
        for retrieval in ("dense", "bm25"):
            with self.subTest(retrieval=retrieval):
                result = self.backend.search("buffer_name", retrieval=retrieval, overlays=[entry])
                self.assertTrue(any(row["symbol"] == "buffer_name" for row in result["results"]))
                self.assertEqual(self.disk_cache(), cache)
                self.assertEqual(self.source_bytes(), sources)
                self.assertEqual(self.backend.status(), status)
                for attr, old in zip(("_manifest", "_vectors_manifest", "_snippets", "_dense", "_references", "_index"), snapshots):
                    self.assertIs(getattr(self.backend, attr), old)
                self.assertEqual(self.model.closes, 0)

    def test_unopened_saved_database_seed_does_not_create_original_sidecars(self):
        self.backend.refresh()
        self.backend.close()
        model = ReviewEmbedder()
        reopened = SearchBackend(self.repo, "src", self.database, embedder=model)
        self.addCleanup(reopened.close)
        cache = self.disk_cache()
        self.assertEqual(set(cache), {self.database.name})
        result = reopened.search("saved", overlays=[buffer("src/main.py", self.saved, 3)])
        self.assertEqual((result["indexstats"]["indexed_windows"], result["indexstats"]["reused_windows"]), (0, 2))
        self.assertEqual(model.documents, [])
        self.assertEqual(self.disk_cache(), cache)
        self.assertIsNone(reopened._index)
        self.assertIsNone(reopened._manifest)

    def test_live_wal_vectors_are_reused_without_modifying_db_wal_or_shm(self):
        self.backend.refresh()
        connection = sqlite3.connect(self.database)
        self.addCleanup(connection.close)
        connection.execute("SELECT count(*) FROM snippets").fetchone()
        changed = "def saved_name():\n    return 'committed in WAL'\n"
        (self.repo / "src/main.py").write_text(changed)
        self.backend.refresh()
        cache = self.disk_cache()
        self.assertIn(self.database.name + "-wal", cache)
        self.assertGreater(len(cache[self.database.name + "-wal"][0]), 32)
        self.assertIn(self.database.name + "-shm", cache)
        self.model.documents.clear()
        result = self.backend.search("saved_name", overlays=[buffer("src/main.py", changed)])
        self.assertEqual((result["indexstats"]["indexed_windows"], result["indexstats"]["reused_windows"]), (0, 2))
        self.assertEqual(self.model.documents, [])
        self.assertEqual(self.disk_cache(), cache)
        row = next(row for row in result["results"] if row["path"] == "src/main.py")
        self.assertEqual(row["source"], changed)

    def test_cache_change_during_filesystem_copy_rejects_before_embedding(self):
        from semantic_search import overlays

        self.backend.refresh()
        original = self.database.read_bytes()
        status = copy.deepcopy(self.backend.status())
        capture = overlays._cache_snapshot
        calls = []

        def race(paths, check):
            snapshot = capture(paths, check)
            calls.append(True)
            if len(calls) == 1:
                with self.database.open("ab") as target:
                    target.write(b"external cache writer")
            return snapshot

        self.model.documents.clear()
        try:
            with patch("semantic_search.overlays._cache_snapshot", side_effect=race):
                with self.assertRaisesRegex(SourceSnapshotError, "cache changed"):
                    self.backend.search("saved", overlays=[buffer("src/main.py", self.saved)])
        finally:
            self.database.write_bytes(original)
        self.assertEqual(self.model.documents, [])
        self.assertEqual(self.model.queries, [])
        self.assertEqual(self.backend.status(), status)

    def test_cold_dense_overlay_owns_no_saved_cache_and_borrows_model_until_parent_close(self):
        entry = buffer("src/main.py", "def buffer_name():\n    return 42\n")
        with patch.object(self.backend, "_get_index", side_effect=AssertionError("parent index initialized")):
            result = self.backend.search("implementation", overlays=[entry])
        self.assertEqual(result["indexstats"]["indexed_windows"], 2)
        self.assertFalse(self.database.parent.exists())
        self.assertIsNone(self.backend._manifest)
        self.assertIsNone(self.backend._index)
        self.assertEqual(self.model.closes, 0)
        self.backend.close()
        self.backend.close()
        self.assertEqual(self.model.closes, 1)

    def test_global_model_lock_and_single_registration_survive_active_overlay_cancellation(self):
        self.backend.refresh()
        before = self.disk_cache()
        registry = CountingRegistry()
        self.backend._requests = registry
        entered, release, queued = threading.Event(), threading.Event(), threading.Event()
        results, errors, observed = {}, {}, []

        def block():
            observed.append(sorted(registry.active))
            entered.set()
            if not release.wait(5):
                raise AssertionError("review synchronization timed out")

        def search(name, **settings):
            try:
                if name == "saved-request":
                    queued.set()
                results[name] = self.backend.search("saved_name", request_id=name, **settings)
            except Exception as error:
                errors[name] = error

        self.model.on_documents = block
        overlay_thread = threading.Thread(target=search, args=("overlay-request",), kwargs={
            "overlays": [buffer("src/main.py", "def buffer_name():\n    return 9\n")]})
        saved_thread = threading.Thread(target=search, args=("saved-request",), kwargs={"retrieval": "bm25"})
        try:
            overlay_thread.start()
            self.assertTrue(entered.wait(3))
            saved_thread.start()
            self.assertTrue(queued.wait(3))
            self.assertNotIn("saved-request", results)
            self.assertEqual(registry.registrations.count("overlay-request"), 1)
            self.backend.cancel("overlay-request")
        finally:
            release.set()
            overlay_thread.join(5)
            if saved_thread.ident is not None:
                saved_thread.join(5)
        self.assertFalse(overlay_thread.is_alive())
        self.assertFalse(saved_thread.is_alive())
        self.assertIsInstance(errors.get("overlay-request"), SearchCancelled)
        self.assertNotIn("saved-request", errors)
        self.assertEqual(results["saved-request"]["results"][0]["symbol"], "saved_name")
        self.assertEqual(observed, [["overlay-request"]])
        self.assertEqual(sorted(registry.registrations), ["overlay-request", "saved-request"])
        self.assertEqual(registry.active, {})
        self.assertEqual(self.model.queries, [])
        self.assertEqual(self.model.closes, 0)
        self.assertEqual(self.disk_cache(), before)

    def test_literal_cookie_override_physical_lines_hashes_and_all_buffer_bindings_survive_budget(self):
        source = "# coding: ascii\r\ndef buffer_name():\r\n" + "".join(
            f"    value{i} = 'λ😀\u2028{'data ' * 30}'\r\n" for i in range(30)) + "    return value29\r\n"
        entries = [buffer("src/main.py", source, 4), buffer("src/helper.py", "def helper_name():\n    return 'same'\n", 8)]
        result = self.backend.search("buffer_name", retrieval="bm25", overlays=entries, token_budget=512)
        envelope = compact_result(result)
        actual = len(encoding().encode(serialize_context(envelope), disallowed_special=()))
        self.assertEqual(actual, result["token_usage"]["tokens"])
        self.assertLessEqual(actual, 512)
        self.assertEqual(envelope["scope"]["overlays"], [{key: entry[key] for key in ("path", "version", "content_hash")}
                                                       for entry in sorted(entries, key=lambda entry: entry["path"])])
        self.assertTrue(result["token_usage"]["truncated"])
        self.assertTrue(result["results"])
        for row in result["results"]:
            original = next(entry["text"] for entry in entries if entry["path"] == row["path"])
            self.assertEqual(row["source"], "".join(physical_lines(original)[row["start_line"] - 1:row["end_line"]]))
            self.assertEqual(len(physical_lines(row["source"])), row["end_line"] - row["start_line"] + 1)
            self.assertEqual(row["source_hash"], hashlib.sha256(row["source"].encode()).hexdigest())
        current = {entry["path"]: {"version": entry["version"], "content_hash": entry["content_hash"]} for entry in entries}
        ensure_overlay_current(result, current)
        current["src/helper.py"]["version"] += 1
        with self.assertRaises(StaleBufferError):
            ensure_overlay_current(result, current)
        self.assertEqual((self.repo / "src/main.py").read_bytes(), self.saved.encode())

    def test_multibyte_per_buffer_limit_is_bytes_not_characters(self):
        text = "😀" * (MAX_OVERLAY_FILE_BYTES // 4)
        self.assertEqual(len(normalize_overlays([buffer("src/main.py", text)])), 1)
        with self.assertRaisesRegex(ValueError, "UTF-8 bytes per buffer"):
            normalize_overlays([buffer("src/main.py", text + "x")])


if __name__ == "__main__":
    unittest.main()
