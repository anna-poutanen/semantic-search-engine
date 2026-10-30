"""Batched query instructions and scheduling, using only CPU test doubles."""
import hashlib
import json
import unittest

import numpy as np

from semantic_search.cancellation import SearchCancelled
from semantic_search.embeddings import (
    DIMENSIONS, MAX_TOKENS, QUERY_PROMPT, QwenEmbedder, normalize_rows,
)


class QueryTokenizer:
    def __init__(self):
        self.requests = []

    def __call__(self, texts, **kwargs):
        self.requests.append((list(texts), kwargs))
        return {"input_ids": [list(text.encode())[:kwargs["max_length"]] for text in texts]}


class CacheCounter:
    def __init__(self):
        self.clears = 0

    def clear_cache(self):
        self.clears += 1


def fake_vectors(rows):
    matrix = np.zeros((len(rows), DIMENSIONS), dtype=np.float32)
    for position, row in enumerate(rows):
        matrix[position, :3] = len(row), row[-1], sum(row)
    return normalize_rows(matrix)


def fingerprint(metadata):
    return hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


class BatchedQueryTests(unittest.TestCase):
    def fake(self, *, batch_size=4):
        embedder = QwenEmbedder(batch_size=batch_size, progress=False)
        embedder._model = object()
        embedder._tokenizer = QueryTokenizer()
        embedder._mx = CacheCounter()
        forwards = []

        def forward(rows):
            forwards.append([list(row) for row in rows])
            return fake_vectors(rows)

        embedder._embed_batch = forward
        return embedder, forwards

    def expected(self, queries):
        return fake_vectors([list((QUERY_PROMPT + query).encode())[:MAX_TOKENS]
                             for query in queries])

    def test_three_short_queries_share_one_forward_and_restore_original_order(self):
        embedder, forwards = self.fake()
        queries = ["deliver the longest callback result", "save", "retry pending work"]
        actual = embedder.embed_queries(queries)
        self.assertEqual(len(forwards), 1)
        self.assertEqual([len(row) for row in forwards[0]],
                         sorted(len(QUERY_PROMPT + query) for query in queries))
        np.testing.assert_array_equal(actual, self.expected(queries))
        self.assertEqual(actual.shape, (3, DIMENSIONS))
        self.assertEqual(actual.dtype, np.float32)
        self.assertEqual(embedder._tokenizer.requests, [(
            [QUERY_PROMPT + query for query in queries],
            {"padding": False, "truncation": True, "max_length": MAX_TOKENS,
             "add_special_tokens": True})])
        self.assertEqual(embedder._mx.clears, 1)

    def test_query_count_limit_still_splits_short_inputs(self):
        embedder, forwards = self.fake(batch_size=2)
        queries = ["longer query", "a", "middle"]
        np.testing.assert_array_equal(embedder.embed_queries(queries), self.expected(queries))
        self.assertEqual([len(rows) for rows in forwards], [2, 1])

    def test_padded_token_limit_can_split_two_queries_into_two_forwards(self):
        embedder, forwards = self.fake()
        queries = ["a" * (4097 - len(QUERY_PROMPT)), "b" * (4097 - len(QUERY_PROMPT))]
        np.testing.assert_array_equal(embedder.embed_queries(queries), self.expected(queries))
        self.assertEqual([[len(row) for row in rows] for rows in forwards], [[4097], [4097]])
        self.assertEqual(embedder._mx.clears, 2)

    def test_each_prompted_query_keeps_single_query_truncation(self):
        embedder, forwards = self.fake()
        queries = ["x" * (MAX_TOKENS + 100), "short"]
        np.testing.assert_array_equal(embedder.embed_queries(queries), self.expected(queries))
        self.assertEqual([[len(row) for row in rows] for rows in forwards],
                         [[len(QUERY_PROMPT + "short")], [MAX_TOKENS]])

    def test_empty_queries_are_lazy_with_float32_matrix_and_no_cancellation_work(self):
        embedder = QwenEmbedder(model_path="/missing/checkpoint", progress=False)
        checks = []
        actual = embedder.embed_queries([], cancel_check=lambda: checks.append(True))
        self.assertEqual(actual.shape, (0, DIMENSIONS))
        self.assertEqual(actual.dtype, np.float32)
        self.assertIsNone(embedder._model)
        self.assertEqual(checks, [])

    def test_invalid_query_list_is_rejected_before_tokenization_or_loading(self):
        for value in (None, "query", ("query",), [""], [" \n"], [None],
                      ["valid", 1], ["valid", " \t"]):
            with self.subTest(value=value):
                embedder = QwenEmbedder(model_path="/missing/checkpoint", progress=False)
                with self.assertRaisesRegex(ValueError, "list of nonempty strings"):
                    embedder.embed_queries(value)
                self.assertIsNone(embedder._model)

    def test_cancellation_before_forward_runs_no_fake_forward(self):
        embedder, forwards = self.fake()

        def cancel():
            raise SearchCancelled("cancel before forward")

        with self.assertRaisesRegex(SearchCancelled, "before forward"):
            embedder.embed_queries(["query"], cancel_check=cancel)
        self.assertEqual(forwards, [])
        self.assertEqual(embedder._mx.clears, 0)

    def test_cancellation_after_first_forward_prevents_remaining_forward(self):
        embedder, forwards = self.fake(batch_size=1)

        def cancel():
            if forwards:
                raise SearchCancelled("cancel after first forward")

        with self.assertRaisesRegex(SearchCancelled, "after first forward"):
            embedder.embed_queries(["first", "second"], cancel_check=cancel)
        self.assertEqual(len(forwards), 1)
        # The existing shared scheduler clears each completed multi-input batch
        # before its post-forward cancellation check; the wrapper adds no clear.
        self.assertEqual(embedder._mx.clears, 1)

    def test_singleton_matches_single_query_result_and_cache_behavior(self):
        batched, batched_forwards = self.fake()
        single, single_forwards = self.fake()
        np.testing.assert_array_equal(batched.embed_queries(["resolve pending work"])[0],
                                      single.embed_query("resolve pending work"))
        self.assertEqual(batched_forwards, single_forwards)
        self.assertEqual(batched._mx.clears, single._mx.clears)
        self.assertEqual(batched._mx.clears, 0)

    def test_model_failure_propagates_without_extra_forward_or_cleanup(self):
        embedder, forwards = self.fake()

        def fail(rows):
            forwards.append(rows)
            raise RuntimeError("fake forward failed")

        embedder._embed_batch = fail
        with self.assertRaisesRegex(RuntimeError, "fake forward failed"):
            embedder.embed_queries(["first", "second"])
        self.assertEqual(len(forwards), 1)
        # Existing embed_query has no extra finally cleanup; batched queries
        # retain that behavior and do not alter document cleanup semantics.
        self.assertEqual(embedder._mx.clears, 0)

    def test_query_api_does_not_change_document_format_or_cache_fingerprint(self):
        embedder, _ = self.fake()
        before = embedder.metadata
        before_fingerprint = fingerprint(before)
        embedder.embed_queries(["find implementation", "deliver callbacks"])
        embedder.embed_query("find implementation")
        document = "Path: src/task.py\nSymbol: complete\nSource:\nreturn result"
        embedder.embed_documents([document])
        self.assertEqual(embedder._tokenizer.requests[-1][0], [document])
        self.assertEqual(embedder.metadata, before)
        self.assertEqual(fingerprint(embedder.metadata), before_fingerprint)
        self.assertEqual(set(before), {
            "id", "revision", "weights_dtype", "quantized", "runtime", "runtime_version",
            "packages", "dimensions", "query_instruction", "query_prompt", "document_prompt",
            "pooling", "normalization", "max_tokens", "truncation", "padding_side",
            "add_special_tokens", "batch_size", "max_padded_tokens", "batch_order",
            "batch_policy", "checkpoint_validation"})
        self.assertIsNone(before["document_prompt"])


if __name__ == "__main__":
    unittest.main()
