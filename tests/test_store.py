import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np

from semantic_search.corpus import Snippet
from semantic_search.store import SQLiteIndex


def snippet(identifier, text="return 1", path="src/a.py", symbol="run", start_line=1):
    return Snippet(identifier, path, symbol, start_line, start_line + 1, text, "supplied-hash")


class FakeEmbedder:
    def __init__(self, revision="first"):
        self.metadata = {"revision": revision, "dimensions": 2, "instruction": "frozen"}
        self.documents = []

    def embed_documents(self, documents):
        self.documents.append(documents)
        return np.array([[len(text), sum(text.encode())] for text in documents], dtype=np.float32)


class StoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "cache/index.sqlite3"
        self.store = SQLiteIndex(self.path)
        self.embedder = FakeEmbedder()

    def test_reopen_reuses_vectors_in_requested_order_and_persists_exact_source(self):
        first, second = snippet("a"), snippet("b", "return 2", symbol="other")
        vectors, initial = self.store.refresh([first, second], self.embedder)
        self.assertEqual((initial["indexed_windows"], initial["reused_windows"]), (2, 0))
        reopened = SQLiteIndex(self.path)
        reordered, stats = reopened.refresh([second, first], self.embedder)
        np.testing.assert_array_equal(reordered, vectors[[1, 0]])
        self.assertEqual((stats["indexed_windows"], stats["reused_windows"], stats["total_windows"]), (0, 2, 2))
        self.assertEqual(len(self.embedder.documents), 1)
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT id,path,symbol,start_line,end_line,text,content_hash FROM snippets ORDER BY position"
            ).fetchall()
        self.assertEqual(rows[0], (second.id, second.path, second.symbol, second.start_line,
                                  second.end_line, second.text, second.content_hash))
        self.assertEqual(reopened.status()["model_fingerprint"], stats["model_fingerprint"])
        self.assertEqual(reopened.status()["embedding"], self.embedder.metadata)
        reopened.close()

    def test_source_edit_invalidates_stale_hash_and_deletion_removes_active_window(self):
        first, deleted = snippet("a"), snippet("deleted", symbol="gone")
        self.store.refresh([first, deleted], self.embedder)
        changed = replace(first, text="return 99")
        _, stats = self.store.refresh([changed], self.embedder)
        self.assertEqual((stats["indexed_windows"], stats["reused_windows"], stats["deleted_windows"]), (1, 0, 1))
        self.assertEqual(len(self.embedder.documents[-1]), 1)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT id,text FROM snippets").fetchall(), [("a", "return 99")])

    def test_changed_boundaries_reuse_rendered_document_and_branch_return_reuses_cache(self):
        original = snippet("old-lines")
        vectors, _ = self.store.refresh([original], self.embedder)
        shifted = replace(original, id="new-lines", start_line=100, end_line=101)
        shifted_vectors, shifted_stats = self.store.refresh([shifted], self.embedder)
        np.testing.assert_array_equal(shifted_vectors, vectors)
        self.assertEqual((shifted_stats["indexed_windows"], shifted_stats["reused_windows"],
                          shifted_stats["deleted_windows"]), (0, 1, 1))
        renamed = replace(shifted, id="renamed", path="src/renamed.py")
        _, renamed_stats = self.store.refresh([renamed], self.embedder)
        self.assertEqual((renamed_stats["indexed_windows"], renamed_stats["deleted_windows"]), (1, 1))
        returned, returned_stats = self.store.refresh([original], self.embedder)
        np.testing.assert_array_equal(returned, vectors)
        self.assertEqual((returned_stats["indexed_windows"], returned_stats["reused_windows"]), (0, 1))
        self.assertEqual(self.store.status()["total_windows"], 1)

    def test_model_settings_change_never_reuses_other_model_vectors(self):
        item = snippet("a")
        _, first = self.store.refresh([item], self.embedder)
        changed_model = FakeEmbedder("second")
        _, second = self.store.refresh([item], changed_model)
        self.assertNotEqual(first["model_fingerprint"], second["model_fingerprint"])
        self.assertEqual((second["indexed_windows"], second["reused_windows"]), (1, 0))
        self.assertEqual(self.store.status()["embedding"], changed_model.metadata)

    def test_failed_inference_or_invalid_matrix_preserves_the_previous_snapshot(self):
        item = snippet("original")
        baseline, _ = self.store.refresh([item], self.embedder)
        status = self.store.status()
        changed = snippet("changed", text="return 2")

        class BrokenEmbedder(FakeEmbedder):
            output = None

            def embed_documents(self, documents):
                if isinstance(self.output, Exception):
                    raise self.output
                return self.output

        broken = BrokenEmbedder()
        for output in (RuntimeError("inference failed"), [[1, 2, 3]], [[0, 0]],
                       [[np.nan, 1]], [[np.inf, 1]], [[1j, 1]], []):
            broken.output = output
            with self.subTest(output=output), self.assertRaises((ValueError, RuntimeError)):
                self.store.refresh([changed], broken)
            self.assertEqual(self.store.status(), status)
            with sqlite3.connect(self.path) as connection:
                self.assertEqual(connection.execute("SELECT id FROM snippets").fetchall(), [("original",)])
        restored, stats = SQLiteIndex(self.path).refresh([item], self.embedder)
        np.testing.assert_array_equal(restored, baseline)
        self.assertEqual(stats["reused_windows"], 1)

    def test_empty_snapshot_deletes_every_active_window_without_running_inference(self):
        self.assertEqual(self.store.status()["total_windows"], 0)
        self.store.refresh([snippet("a")], self.embedder)
        empty, stats = self.store.refresh([], self.embedder)
        self.assertEqual(empty.shape, (0, 2))
        self.assertEqual((stats["indexed_windows"], stats["reused_windows"], stats["deleted_windows"],
                          stats["total_windows"]), (0, 0, 1, 0))
        self.assertEqual(len(self.embedder.documents), 1)
        self.assertEqual(SQLiteIndex(self.path).status()["total_windows"], 0)

    def test_duplicate_rendered_windows_share_embedding_but_keep_separate_locations(self):
        first = snippet("a")
        second = replace(first, id="b", start_line=20, end_line=21)
        vectors, stats = self.store.refresh([first, second], self.embedder)
        self.assertEqual(len(self.embedder.documents[0]), 1)
        np.testing.assert_array_equal(vectors[0], vectors[1])
        self.assertEqual(stats["total_windows"], 2)

    def test_readers_observe_the_previous_snapshot_until_new_embeddings_validate(self):
        self.store.refresh([snippet("old")], self.embedder)
        entered, proceed = threading.Event(), threading.Event()

        class BlockingEmbedder(FakeEmbedder):
            def embed_documents(self, documents):
                entered.set()
                if not proceed.wait(timeout=5):
                    raise RuntimeError("test timed out")
                return super().embed_documents(documents)

        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = executor.submit(self.store.refresh,
                                      [snippet("new", "return 2"), snippet("also", "return 3")],
                                      BlockingEmbedder())
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.assertEqual(executor.submit(self.store.status).result(timeout=5)["total_windows"], 1)
            finally:
                proceed.set()
            _, stats = pending.result(timeout=5)
        self.assertEqual(stats["total_windows"], 2)
        self.assertEqual(self.store.status()["total_windows"], 2)

    def test_duplicate_ids_and_invalid_metadata_fail_without_inference(self):
        item = snippet("a")
        with self.assertRaises(ValueError):
            self.store.refresh([item, item], self.embedder)
        self.embedder.metadata["dimensions"] = 0
        with self.assertRaises(ValueError):
            self.store.refresh([item], self.embedder)
        self.assertEqual(self.embedder.documents, [])
        self.assertEqual(self.store.status()["total_windows"], 0)

    def test_invalid_persisted_vectors_are_rejected_without_replacing_snapshot(self):
        item = snippet("a")
        self.store.refresh([item], self.embedder)
        status = self.store.status()
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE embeddings SET vector=?", (sqlite3.Binary(np.zeros(2).tobytes()),))
        with self.assertRaisesRegex(ValueError, "Stored embedding vector is invalid"):
            SQLiteIndex(self.path).refresh([item], self.embedder)
        self.assertEqual(self.store.status(), status)
        self.assertEqual(len(self.embedder.documents), 1)

    def test_failed_before_commit_check_preserves_old_snapshot_and_embedding_cache(self):
        original = snippet("old")
        self.store.refresh([original], self.embedder)
        status = self.store.status()

        def reject_changed_source():
            self.assertEqual(len(self.embedder.documents), 2)
            raise RuntimeError("Source changed during inference")

        with self.assertRaisesRegex(RuntimeError, "Source changed"):
            self.store.refresh([snippet("new", "return 99")], self.embedder,
                               before_commit=reject_changed_source)
        self.assertEqual(self.store.status(), status)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT id FROM snippets").fetchall(), [("old",)])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
