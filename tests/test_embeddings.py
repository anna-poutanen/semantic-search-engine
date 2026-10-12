import unittest

import numpy as np

from semantic_search.embeddings import (
    DIMENSIONS, MAX_TOKENS, QUERY_PROMPT, QwenEmbedder, last_token_pool, normalize_rows,
)


class FakeTokenizer:
    def __init__(self):
        self.requests = []

    def __call__(self, texts, **kwargs):
        self.requests.append((texts, kwargs))
        return {"input_ids": [[ord(text[0])] * len(text) for text in texts]}


class EmbeddingTests(unittest.TestCase):
    def fake_embedder(self):
        embedder = QwenEmbedder(batch_size=2, progress=False)
        tokenizer = FakeTokenizer()
        embedder._tokenizer = tokenizer
        embedder._model = object()
        batches = []

        def embed_batch(ids):
            batches.append(ids)
            vectors = np.zeros((len(ids), DIMENSIONS), dtype=np.float32)
            for i, row in enumerate(ids):
                vectors[i, :2] = (row[0], len(row))
            return normalize_rows(vectors)

        embedder._embed_batch = embed_batch
        return embedder, tokenizer, batches

    def test_last_valid_token_pooling_with_left_and_right_padding(self):
        states = np.arange(2 * 4 * 3).reshape(2, 4, 3)
        left = np.array([[0, 0, 1, 1], [0, 1, 1, 1]])
        right = np.array([[1, 1, 0, 0], [1, 1, 1, 0]])
        np.testing.assert_array_equal(last_token_pool(states, left), states[:, -1])
        np.testing.assert_array_equal(last_token_pool(states, right), states[[0, 1], [1, 2]])
        with self.assertRaises(ValueError):
            last_token_pool(states, np.zeros((2, 4)))

    def test_batched_length_sort_preserves_input_order_and_normalization(self):
        embedder, _, batches = self.fake_embedder()
        texts = ["aaaaaa", "bb", "ccc", "d"]
        output = embedder.embed_documents(texts)
        self.assertEqual([[len(row) for row in batch] for batch in batches], [[1, 2], [3, 6]])
        expected = np.zeros((4, DIMENSIONS), dtype=np.float32)
        for i, text in enumerate(texts):
            expected[i, :2] = (ord(text[0]), len(text))
        np.testing.assert_allclose(output, normalize_rows(expected))
        np.testing.assert_allclose(np.linalg.norm(output, axis=1), 1, atol=1e-6)
        self.assertEqual(output.dtype, np.float32)

    def test_queries_have_fixed_instruction_and_documents_do_not(self):
        embedder, tokenizer, _ = self.fake_embedder()
        doc = "Path: src/test.py\nSymbol: run\nSource:\nreturn value"
        embedder.embed_documents([doc])
        query = embedder.embed_query("restore a signed cookie")
        self.assertEqual(tokenizer.requests[0][0], [doc])
        self.assertEqual(tokenizer.requests[1][0], [QUERY_PROMPT + "restore a signed cookie"])
        self.assertEqual(query.shape, (DIMENSIONS,))
        kwargs = tokenizer.requests[1][1]
        self.assertEqual(kwargs["max_length"], MAX_TOKENS)
        self.assertTrue(kwargs["truncation"])
        self.assertFalse(kwargs["padding"])

    def test_empty_inputs_are_lazy_and_bad_inputs_fail(self):
        embedder = QwenEmbedder(model_path="/missing/checkpoint", progress=False)
        self.assertEqual(embedder.embed_documents([]).shape, (0, DIMENSIONS))
        self.assertIsNone(embedder._model)
        for value in ("not a list", [""], [" \n"], [None]):
            with self.assertRaises(ValueError):
                embedder.embed_documents(value)
        with self.assertRaises(ValueError):
            embedder.embed_query("")
        with self.assertRaises(ValueError):
            QwenEmbedder(batch_size=0)

    def test_nonfinite_and_zero_model_vectors_fail(self):
        with self.assertRaises(ValueError):
            normalize_rows(np.array([[np.nan, 1]]))
        with self.assertRaises(ValueError):
            normalize_rows(np.zeros((1, 2)))

    def test_metadata_does_not_change_with_lazy_model_lifecycle(self):
        embedder = QwenEmbedder(progress=False)
        initial = embedder.metadata
        embedder._model = object()
        embedder._checkpoint_verified = True
        self.assertEqual(initial, embedder.metadata)
        embedder.close()
        self.assertEqual(initial, embedder.metadata)


if __name__ == "__main__":
    unittest.main()
