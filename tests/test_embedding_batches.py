"""Long-document batches stay bounded without reducing document context."""
import hashlib
import json
import unittest

import numpy as np

from semantic_search.cancellation import SearchCancelled
from semantic_search.embeddings import (
    DIMENSIONS, MAX_PADDED_TOKENS, MAX_TOKENS, QwenEmbedder, normalize_rows,
)


class Tokenizer:
    def __init__(self):
        self.requests = []

    def __call__(self, texts, **kwargs):
        self.requests.append((texts, kwargs))
        return {"input_ids": [[ord(text[0])] * min(len(text), kwargs["max_length"])
                              for text in texts]}


class MetalCache:
    def __init__(self):
        self.clears = 0

    def clear_cache(self):
        self.clears += 1


class EmbeddingBatchTests(unittest.TestCase):
    def fake(self, *, batch_size=4, max_padded_tokens=MAX_PADDED_TOKENS):
        embedder = QwenEmbedder(batch_size=batch_size, max_padded_tokens=max_padded_tokens,
                                progress=False)
        embedder._model = object()
        embedder._tokenizer = Tokenizer()
        embedder._mx = MetalCache()
        batches = []

        def embed_batch(token_ids):
            batches.append(token_ids)
            vectors = np.zeros((len(token_ids), DIMENSIONS), dtype=np.float32)
            for position, row in enumerate(token_ids):
                vectors[position, :2] = row[0], len(row)
            return normalize_rows(vectors)

        embedder._embed_batch = embed_batch
        return embedder, batches

    def texts(self, lengths):
        return [chr(65 + position) * length for position, length in enumerate(lengths)]

    def expected(self, texts):
        matrix = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for position, text in enumerate(texts):
            matrix[position, :2] = ord(text[0]), min(len(text), MAX_TOKENS)
        return normalize_rows(matrix)

    def test_variable_batches_report_exact_completion_and_restore_input_order(self):
        embedder, batches = self.fake()
        texts = self.texts([5000, 2048, 3000, 1000, 2048, 8192, 4096, 1000])
        progress = []
        actual = embedder.embed_documents(
            texts, progress_callback=lambda completed, total: progress.append((completed, total)))
        self.assertEqual([[len(row) for row in batch] for batch in batches],
                         [[1000, 1000, 2048, 2048], [3000, 4096], [5000], [8192]])
        self.assertEqual(progress, [(4, 8), (6, 8), (7, 8), (8, 8)])
        # Tied lengths retain their original relative order.
        self.assertEqual([row[0] for row in batches[0]], [ord("D"), ord("H"), ord("B"), ord("E")])
        self.assertTrue(all(max(map(len, batch)) * len(batch) <= 8192 for batch in batches))
        np.testing.assert_array_equal(actual, self.expected(texts))
        self.assertEqual(actual.dtype, np.float32)
        self.assertEqual(embedder._mx.clears, len(batches) + 1)

    def test_exact_padded_token_boundaries_and_singleton_maximum(self):
        for lengths, groups in (([2048] * 4, [4]), ([2049] * 4, [3, 1]),
                                ([2731] * 3, [2, 1]), ([4096] * 2, [2]),
                                ([4097] * 2, [1, 1]), ([8192] * 2, [1, 1])):
            with self.subTest(lengths=lengths):
                embedder, batches = self.fake()
                texts = self.texts(lengths)
                np.testing.assert_array_equal(embedder.embed_documents(texts), self.expected(texts))
                self.assertEqual([len(batch) for batch in batches], groups)
                self.assertTrue(all(len(batch) * max(map(len, batch)) <= MAX_PADDED_TOKENS
                                    for batch in batches))

    def test_document_count_cap_still_applies_to_small_inputs(self):
        embedder, batches = self.fake(batch_size=3)
        texts = self.texts([1, 2, 3, 4, 5, 6, 7])
        actual = embedder.embed_documents(texts)
        self.assertEqual([len(batch) for batch in batches], [3, 3, 1])
        np.testing.assert_array_equal(actual, self.expected(texts))

    def test_explicit_larger_budget_allows_old_four_document_group(self):
        embedder, batches = self.fake(max_padded_tokens=4 * MAX_TOKENS)
        texts = self.texts([8192] * 4)
        np.testing.assert_array_equal(embedder.embed_documents(texts), self.expected(texts))
        self.assertEqual([len(batch) for batch in batches], [4])
        self.assertEqual(embedder.metadata["max_padded_tokens"], 4 * MAX_TOKENS)

    def test_original_document_token_limit_is_unchanged(self):
        embedder, batches = self.fake()
        texts = self.texts([MAX_TOKENS + 1000, 12])
        actual = embedder.embed_documents(texts)
        self.assertEqual([[len(row) for row in batch] for batch in batches], [[12], [MAX_TOKENS]])
        self.assertEqual(embedder._tokenizer.requests[0][1], {
            "padding": False, "truncation": True, "max_length": MAX_TOKENS,
            "add_special_tokens": True})
        np.testing.assert_array_equal(actual, self.expected(texts))

    def test_cancellation_between_variable_groups_runs_no_more_model_batches(self):
        embedder, batches = self.fake()
        progress = []

        def report(completed, total):
            progress.append((completed, total))

        def check():
            if progress:
                raise SearchCancelled("cancelled after first reported batch")

        with self.assertRaises(SearchCancelled):
            embedder.embed_documents(self.texts([4097, 8192, 4098]),
                                     cancel_check=check, progress_callback=report)
        self.assertEqual([len(batch) for batch in batches], [1])
        self.assertEqual(progress, [(1, 3)])
        self.assertEqual(embedder._mx.clears, 2)

    def test_cancellation_after_forward_never_reports_unaccepted_progress(self):
        embedder, batches = self.fake()
        progress = []

        def check():
            if batches:
                raise SearchCancelled("cancelled during forward")

        with self.assertRaises(SearchCancelled):
            embedder.embed_documents(self.texts([4097, 8192]), cancel_check=check,
                                     progress_callback=lambda *event: progress.append(event))
        self.assertEqual(len(batches), 1)
        self.assertEqual(progress, [])

    def test_invalid_budget_rejects_boolean_float_and_context_reduction(self):
        for value in (True, False, 0, -1, MAX_TOKENS - 1, float(MAX_TOKENS), "8192", None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "max_padded_tokens"):
                QwenEmbedder(max_padded_tokens=value)

    def test_tokenizer_violating_original_limit_is_rejected_before_forward(self):
        embedder, batches = self.fake()
        embedder._tokenizer = lambda texts, **kwargs: {"input_ids": [[1] * (MAX_TOKENS + 1)]}
        with self.assertRaisesRegex(ValueError, "Tokenizer exceeded"):
            embedder.embed_documents(["document"])
        self.assertEqual(batches, [])

    def test_batch_policy_is_part_of_stable_model_cache_fingerprint(self):
        embedder = QwenEmbedder(progress=False)
        metadata = embedder.metadata
        self.assertEqual(metadata["batch_size"], 4)
        self.assertEqual(metadata["max_tokens"], MAX_TOKENS)
        self.assertEqual(metadata["max_padded_tokens"], 8192)
        self.assertIn("longest_token_length * document_count <= max_padded_tokens", metadata["batch_policy"])
        old = {key: value for key, value in metadata.items()
               if key not in ("max_padded_tokens", "batch_policy")}

        def fingerprint(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                              allow_nan=False).encode()).hexdigest()

        self.assertNotEqual(fingerprint(metadata), fingerprint(old))
        self.assertNotEqual(fingerprint(metadata), fingerprint(QwenEmbedder(max_padded_tokens=16384).metadata))
        embedder._model = object()
        self.assertEqual(metadata, embedder.metadata)
        embedder.close()
        self.assertEqual(metadata, embedder.metadata)


if __name__ == "__main__":
    unittest.main()
