"""Indexing progress uses real completed work and leaves retrieval unchanged."""
import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from semantic_search import cli
from semantic_search.backend import SearchBackend
from semantic_search.cancellation import SearchCancelled
from semantic_search.corpus import Snippet
from semantic_search.embeddings import DIMENSIONS, QwenEmbedder, normalize_rows
from semantic_search.store import SQLiteIndex


class BatchEmbedder:
    metadata = {"id": "cpu-progress", "revision": "fixed", "dimensions": 2}

    def __init__(self):
        self.documents = []

    def embed_documents(self, texts, *, cancel_check=None, progress_callback=None):
        self.documents.append(list(texts))
        result = np.asarray([[len(text), 1.] for text in texts])
        for completed in range(1, len(texts) + 1):
            if cancel_check is not None:
                cancel_check()
            if progress_callback is not None:
                progress_callback(completed, len(texts))
        return result

    def embed_query(self, query):
        return np.asarray([1., 0.])

    def close(self):
        pass


class LegacyEmbedder(BatchEmbedder):
    def embed_documents(self, texts):
        return super().embed_documents(texts)


def snippet(identifier, text="return 1"):
    return Snippet(identifier, "src/code.py", "run", 1, 2, text, "supplied-hash")


class IndexProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_qwen_reports_completed_batches_without_changing_length_sort_or_output(self):
        embedder = QwenEmbedder(batch_size=2, progress=False)
        embedder._model = object()
        embedder._tokenizer = lambda texts, **kwargs: {
            "input_ids": [[ord(text[0])] * len(text) for text in texts]}
        batches = []

        def embed_batch(rows):
            batches.append(rows)
            matrix = np.zeros((len(rows), DIMENSIONS), dtype=np.float32)
            for position, row in enumerate(rows):
                matrix[position, :2] = row[0], len(row)
            return normalize_rows(matrix)

        embedder._embed_batch = embed_batch
        events = []
        texts = ["aaaaaa", "bb", "ccc", "d", "eeeee"]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            actual = embedder.embed_documents(
                texts, progress_callback=lambda completed, total: events.append((completed, total)))
        self.assertEqual(events, [(2, 5), (4, 5), (5, 5)])
        self.assertEqual([[len(row) for row in batch] for batch in batches], [[1, 2], [3, 5], [6]])
        self.assertEqual(output.getvalue(), "")
        expected = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for position, text in enumerate(texts):
            expected[position, :2] = ord(text[0]), len(text)
        np.testing.assert_array_equal(actual, normalize_rows(expected))

    def test_store_progress_counts_unique_missing_documents_and_cache_reuse(self):
        store = SQLiteIndex(self.root / "index.sqlite3")
        embedder, events = BatchEmbedder(), []
        first = snippet("first")
        duplicate = replace(first, id="duplicate", start_line=10, end_line=11)
        second = snippet("second", "return 2")
        baseline, stats = store.refresh([first, duplicate, second], embedder, progress=events.append)
        cache = next(event for event in events if event["phase"] == "cache")
        self.assertEqual(cache, {"phase": "cache", "completed": 0, "total": 2,
                                 "unit": "documents", "cached_documents": 0,
                                 "pending_documents": 2, "windows": 3})
        self.assertEqual([(event["completed"], event["total"]) for event in events
                          if event["phase"] == "embed"], [(0, 2), (1, 2), (2, 2)])
        self.assertEqual(stats["indexed_windows"], 3)
        self.assertEqual(events[-1], {"phase": "save", "completed": 3, "total": 3, "unit": "windows"})
        events.clear()
        reopened = SQLiteIndex(store.db_path)
        reused, stats = reopened.refresh([second, first, duplicate], embedder, progress=events.append)
        np.testing.assert_array_equal(reused, baseline[[2, 0, 1]])
        self.assertEqual((events[0]["cached_documents"], events[0]["pending_documents"]), (2, 0))
        self.assertFalse(any(event["phase"] == "embed" for event in events))
        self.assertEqual(stats["reused_windows"], 3)
        self.assertEqual(len(embedder.documents), 1)

    def test_legacy_embedder_reports_only_start_and_validated_completion(self):
        events = []
        store = SQLiteIndex(self.root / "index.sqlite3")
        store.refresh([snippet("one"), snippet("two", "return 2")], LegacyEmbedder(), progress=events.append)
        self.assertEqual([(event["completed"], event["total"]) for event in events
                          if event["phase"] == "embed"], [(0, 2), (2, 2)])

    def test_invalid_embedding_output_never_claims_completion_or_save(self):
        embedder = LegacyEmbedder()
        embedder.embed_documents = lambda texts: np.zeros((len(texts), 2))
        store, events = SQLiteIndex(self.root / "index.sqlite3"), []
        with self.assertRaisesRegex(ValueError, "finite and nonzero"):
            store.refresh([snippet("one")], embedder, progress=events.append)
        self.assertEqual([event["phase"] for event in events], ["cache", "embed"])
        self.assertEqual(events[-1]["completed"], 0)
        self.assertEqual(store.status()["total_windows"], 0)

    def backend(self, events=None, embedder=None):
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src/code.py").write_text(
            "def alpha():\n    return 'alpha'\n\ndef beta():\n    return 'beta'\n")
        backend = SearchBackend(self.root, "src", self.root / "index.sqlite3",
                                embedder=embedder or BatchEmbedder(),
                                progress=events.append if events is not None else None)
        self.addCleanup(backend.close)
        return backend

    def test_backend_progress_has_only_counts_and_stable_cache_results(self):
        events = []
        backend = self.backend(events)
        first = backend.search("alpha")
        phases = [event["phase"] for event in events]
        self.assertEqual(phases[:4], ["scan", "scan", "extract", "extract"])
        self.assertEqual(phases[-1], "indexed")
        self.assertEqual(events[-1]["completed"], first["indexstats"]["total_windows"])
        self.assertFalse(events[-1]["memory_cache_hit"])
        all_events = list(events)
        events.clear()
        second = backend.search("alpha")
        self.assertEqual(first["results"], second["results"])
        self.assertEqual([event["phase"] for event in events], ["scan", "scan", "indexed"])
        self.assertTrue(events[-1]["memory_cache_hit"])
        all_events.extend(events)
        allowed = {"phase", "completed", "total", "unit", "windows", "cached_documents",
                   "pending_documents", "memory_cache_hit", "reused_windows", "indexed_windows"}
        self.assertTrue(all(set(event) <= allowed for event in all_events))
        serialized = json.dumps(all_events)
        for private in (str(self.root), "code.py", "alpha", "beta", "return"):
            self.assertNotIn(private, serialized)

    def test_lexical_progress_keeps_model_lazy(self):
        events = []
        backend = self.backend(events)
        with patch.object(backend, "_get_embedder", side_effect=AssertionError("model used")):
            backend.search("alpha", retrieval="bm25")
        self.assertEqual(events[-1]["phase"], "indexed")
        self.assertFalse(any(event["phase"] == "embed" for event in events))

    def test_cancel_during_reported_batch_preserves_old_atomic_snapshot(self):
        events = []
        backend = self.backend(events)
        backend.refresh()
        original = backend.status()["store"]
        (self.root / "src/code.py").write_text("def gamma():\n    return 'gamma'\n")
        events.clear()

        def report(event):
            events.append(event)
            if event["phase"] == "embed" and event["completed"] > 0:
                backend.cancel("cancel-progress")

        backend._progress = report
        with self.assertRaises(SearchCancelled):
            backend.search("gamma", request_id="cancel-progress")
        self.assertEqual(backend.status()["store"], original)
        self.assertFalse(any(event["phase"] in {"save", "indexed"} for event in events))

    def test_default_service_does_not_enable_progress_and_opt_in_is_json(self):
        for enabled in (False, True):
            backend, server = Mock(), Mock()
            server.server_address = ("127.0.0.1", 34567)
            server.serve_forever.side_effect = KeyboardInterrupt
            events = []

            def factory(*args, **kwargs):
                callback = kwargs.get("progress")
                events.append(callback)
                if callback is not None:
                    callback({"phase": "embed", "completed": 2, "total": 4, "unit": "documents"})
                return backend

            output = io.StringIO()
            with patch("semantic_search.backend.SearchBackend", side_effect=factory), \
                    patch("semantic_search.server.create_server", return_value=server), \
                    contextlib.redirect_stdout(output):
                cli.main(["serve", "--repo", str(self.root), "--no-watch", *(["--progress"] if enabled else [])])
            lines = output.getvalue().splitlines()
            progress = [json.loads(line.removeprefix("Progress: ")) for line in lines
                        if line.startswith("Progress: ")]
            self.assertEqual(len(progress), 2 if enabled else 0)
            self.assertEqual(events[0] is not None, enabled)
            self.assertTrue(lines[-1].startswith("Ready: http://127.0.0.1:34567"))
            if enabled:
                self.assertEqual(progress[0]["completed"], 2)
                self.assertEqual(progress[-1]["phase"], "ready")
            backend.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
