import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from semantic_search.corpus import Snippet
from semantic_search.index import build_or_load_index


class FakeEmbedder:
    def __init__(self, revision="frozen"):
        self.metadata = {"model_revision": revision, "prompt": "fixed", "dimensions": 4}
        self.calls = 0

    def embed_documents(self, documents):
        self.calls += 1
        return np.ones((len(documents), 4), dtype=np.float32) / 2


class IndexTests(unittest.TestCase):
    def test_cache_reused_and_invalidated_by_source_and_model_revision(self):
        snippet = Snippet("src/a.py:f:1-2", "src/a.py", "f", 1, 2, "def f():\n return 1\n", "source-one")
        with tempfile.TemporaryDirectory() as directory:
            embedder = FakeEmbedder()
            first, info = build_or_load_index([snippet], embedder, Path(directory), progress=False)
            second, info2 = build_or_load_index([snippet], embedder, Path(directory), progress=False)
            self.assertFalse(info["cache_hit"])
            self.assertTrue(info2["cache_hit"])
            self.assertEqual(embedder.calls, 1)
            np.testing.assert_array_equal(first, second)
            changed = replace(snippet, text="def f():\n return 2\n", content_hash="source-two")
            _, info3 = build_or_load_index([changed], embedder, directory, progress=False)
            self.assertNotEqual(info["key"], info3["key"])
            self.assertEqual(embedder.calls, 2)
            _, info4 = build_or_load_index([snippet], FakeEmbedder("new-revision"), directory, progress=False)
            self.assertNotEqual(info["key"], info4["key"])

    def test_actual_document_changes_invalidate_stale_supplied_source_hash(self):
        snippet = Snippet("src/a.py:f:1-2", "src/a.py", "f", 1, 2, "return 1", "stale-hash")
        changed = replace(snippet, text="return 2")
        with tempfile.TemporaryDirectory() as directory:
            embedder = FakeEmbedder()
            _, first = build_or_load_index([snippet], embedder, directory, progress=False)
            _, second = build_or_load_index([changed], embedder, directory, progress=False)
            self.assertNotEqual(first["key"], second["key"])
            self.assertFalse(second["cache_hit"])
            self.assertEqual(embedder.calls, 2)

    def test_corrupt_cached_vector_matrices_fail_before_retrieval(self):
        snippet = Snippet("src/a.py:f:1-2", "src/a.py", "f", 1, 2, "return 1", "hash")
        with tempfile.TemporaryDirectory() as directory:
            embedder = FakeEmbedder()
            _, info = build_or_load_index([snippet], embedder, directory, progress=False)
            path = Path(directory) / f"{info['key']}.npz"
            invalid = [np.array(1), np.ones(4), np.ones((2, 4)), np.ones((1, 3)),
                       np.empty((1, 0)), np.zeros((1, 4)), np.full((1, 4), np.nan),
                       np.full((1, 4), np.inf), np.ones((1, 4), dtype=complex),
                       np.array([["bad"] * 4])]
            for vectors in invalid:
                np.savez_compressed(path, vectors=vectors, manifest=json.dumps(info["manifest"]))
                with self.subTest(shape=vectors.shape, dtype=vectors.dtype), self.assertRaisesRegex(
                        ValueError, "Embedding cache contains an invalid document matrix"):
                    build_or_load_index([snippet], embedder, directory, progress=False)
            self.assertEqual(embedder.calls, 1)

    def test_malformed_cache_has_a_clear_error(self):
        snippet = Snippet("src/a.py:f:1-2", "src/a.py", "f", 1, 2, "return 1", "hash")
        with tempfile.TemporaryDirectory() as directory:
            embedder = FakeEmbedder()
            _, info = build_or_load_index([snippet], embedder, directory, progress=False)
            path = Path(directory) / f"{info['key']}.npz"
            np.savez_compressed(path, manifest=json.dumps(info["manifest"]))
            with self.assertRaisesRegex(ValueError, "Embedding cache is unreadable or malformed"):
                build_or_load_index([snippet], embedder, directory, progress=False)

    def test_new_embeddings_must_match_model_metadata_dimension(self):
        class WrongDimensionEmbedder(FakeEmbedder):
            def embed_documents(self, documents):
                return np.ones((len(documents), 3), dtype=np.float32)

        snippet = Snippet("src/a.py:f:1-2", "src/a.py", "f", 1, 2, "return 1", "hash")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Embedding model returned an invalid document matrix"):
                build_or_load_index([snippet], WrongDimensionEmbedder(), directory, progress=False)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
