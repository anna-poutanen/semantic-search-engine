"""Completed native batches can be durably cached without changing inference."""
import unittest

import numpy as np

from semantic_search.backend import CancellableEmbedder
from semantic_search.cancellation import SearchCancelled
from semantic_search.embeddings import DIMENSIONS, QwenEmbedder, normalize_rows


def expected_vectors(texts):
    matrix = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
    for position, text in enumerate(texts):
        matrix[position, :2] = ord(text[0]), len(text)
    return normalize_rows(matrix)


class EmbeddingCheckpointTests(unittest.TestCase):
    def fake_embedder(self, *, batch_size=4, events=None):
        embedder = QwenEmbedder(batch_size=batch_size, progress=False)
        embedder._model = object()
        embedder._tokenizer = lambda texts, **kwargs: {
            "input_ids": [[ord(text[0])] * len(text) for text in texts]}
        batches = []

        def embed_batch(rows):
            batches.append(rows)
            if events is not None:
                events.append("forward")
            matrix = np.zeros((len(rows), DIMENSIONS), dtype=np.float32)
            for position, row in enumerate(rows):
                matrix[position, :2] = row[0], len(row)
            return normalize_rows(matrix)

        embedder._embed_batch = embed_batch
        return embedder, batches

    def test_adaptive_batches_checkpoint_original_indices_and_exact_output_rows(self):
        embedder, batches = self.fake_embedder()
        texts = ["a" * 3000, "b" * 100, "c" * 4500, "d" * 2000,
                 "e" * 100, "f" * 8192, "g" * 6000, "h" * 1000]
        metadata = embedder.metadata
        checkpoints, progress = [], []
        actual = embedder.embed_documents(
            texts, batch_callback=lambda indices, vectors: checkpoints.append((indices, vectors)),
            progress_callback=lambda count, total: progress.append((count, total)))
        self.assertEqual([indices for indices, _ in checkpoints],
                         [[1, 4, 7, 3], [0], [2], [6], [5]])
        self.assertEqual([[len(row) for row in batch] for batch in batches],
                         [[100, 100, 1000, 2000], [3000], [4500], [6000], [8192]])
        self.assertEqual(progress, [(4, 8), (5, 8), (6, 8), (7, 8), (8, 8)])
        for indices, vectors in checkpoints:
            self.assertEqual(vectors.dtype, np.float32)
            np.testing.assert_array_equal(vectors, actual[indices])
        np.testing.assert_array_equal(actual, expected_vectors(texts))
        self.assertEqual(metadata, embedder.metadata)

    def test_callback_mutation_cannot_change_results_or_remaining_schedule(self):
        embedder, batches = self.fake_embedder(batch_size=2)
        texts = ["aaaaaa", "bb", "ccc", "d", "eeeee"]
        checkpoint_indices = []

        def mutate(indices, vectors):
            checkpoint_indices.append(indices.copy())
            indices[:] = [999]
            vectors[:] = np.nan

        actual = embedder.embed_documents(texts, batch_callback=mutate)
        self.assertEqual(checkpoint_indices, [[3, 1], [2, 4], [0]])
        self.assertEqual([[len(row) for row in batch] for batch in batches],
                         [[1, 2], [3, 5], [6]])
        np.testing.assert_array_equal(actual, expected_vectors(texts))

    def test_checkpoint_is_after_post_forward_check_and_before_progress(self):
        events = []
        embedder, _ = self.fake_embedder(batch_size=1, events=events)
        embedder.embed_documents(
            ["a", "bb"], cancel_check=lambda: events.append("check"),
            batch_callback=lambda *_: events.append("checkpoint"),
            progress_callback=lambda *_: events.append("progress"))
        self.assertEqual(events, ["check", "forward", "check", "checkpoint", "progress"] * 2)

    def test_post_forward_cancellation_never_checkpoints_or_reports_batch(self):
        embedder, batches = self.fake_embedder(batch_size=1)
        checks, checkpoints, progress = [], [], []

        def cancel():
            checks.append(None)
            if len(checks) == 2:
                raise SearchCancelled("cancelled after inference")

        with self.assertRaises(SearchCancelled):
            embedder.embed_documents(
                ["a", "bb"], cancel_check=cancel,
                batch_callback=lambda *batch: checkpoints.append(batch),
                progress_callback=lambda *counts: progress.append(counts))
        self.assertEqual(len(batches), 1)
        self.assertEqual(checkpoints, [])
        self.assertEqual(progress, [])

    def test_progress_cancellation_retains_just_completed_checkpoint(self):
        embedder, batches = self.fake_embedder(batch_size=1)
        checkpoints, progress = [], []
        cancelled = False

        def check():
            if cancelled:
                raise SearchCancelled("cancelled by progress")

        def report(*counts):
            nonlocal cancelled
            progress.append(counts)
            cancelled = True

        with self.assertRaises(SearchCancelled):
            embedder.embed_documents(
                ["a", "bb"], cancel_check=check,
                batch_callback=lambda *batch: checkpoints.append(batch), progress_callback=report)
        self.assertEqual(len(batches), 1)
        self.assertEqual(progress, [(1, 2)])
        self.assertEqual(checkpoints[0][0], [0])
        np.testing.assert_array_equal(checkpoints[0][1], expected_vectors(["a"]))

    def test_checkpoint_exception_stops_next_forward_and_progress_and_clears_cache(self):
        embedder, batches = self.fake_embedder(batch_size=1)
        cleared, progress = [], []

        class FakeMLX:
            def clear_cache(self):
                cleared.append(None)

        embedder._mx = FakeMLX()

        def fail(*_):
            raise RuntimeError("checkpoint failed")

        with self.assertRaisesRegex(RuntimeError, "checkpoint failed"):
            embedder.embed_documents(["a", "bb"], batch_callback=fail,
                                     progress_callback=lambda *counts: progress.append(counts))
        self.assertEqual(len(batches), 1)
        self.assertEqual(progress, [])
        self.assertEqual(len(cleared), 2)  # Native batch release and request finally.

    def test_invalid_native_output_is_not_checkpointed(self):
        for invalid in (np.ones((1, DIMENSIONS - 1)),
                        np.zeros((1, DIMENSIONS)),
                        np.full((1, DIMENSIONS), np.nan)):
            with self.subTest(shape=invalid.shape, finite=np.isfinite(invalid).all()):
                embedder, _ = self.fake_embedder(batch_size=1)
                # The native batch normalizes before returning to _embed_texts.
                embedder._embed_batch = lambda rows: normalize_rows(invalid)
                checkpoints = []
                with self.assertRaises(ValueError):
                    embedder.embed_documents(["a"], batch_callback=lambda *batch: checkpoints.append(batch))
                self.assertEqual(checkpoints, [])

    def test_later_native_failure_retains_only_prior_validated_batch(self):
        embedder, batches = self.fake_embedder(batch_size=1)
        original = embedder._embed_batch
        checkpoints, progress = [], []

        def fail_second(rows):
            if batches:
                raise RuntimeError("native inference failed")
            return original(rows)

        embedder._embed_batch = fail_second
        with self.assertRaisesRegex(RuntimeError, "native inference failed"):
            embedder.embed_documents(
                ["a", "bb"], batch_callback=lambda *batch: checkpoints.append(batch),
                progress_callback=lambda *counts: progress.append(counts))
        self.assertEqual(len(batches), 1)
        self.assertEqual(progress, [(1, 2)])
        self.assertEqual(checkpoints[0][0], [0])
        np.testing.assert_array_equal(checkpoints[0][1], expected_vectors(["a"]))

    def test_empty_documents_are_lazy_and_queries_do_not_offer_checkpoint_callbacks(self):
        embedder = QwenEmbedder(model_path="/missing/model", progress=False)
        checkpoints = []
        actual = embedder.embed_documents([], batch_callback=lambda *batch: checkpoints.append(batch))
        self.assertEqual(actual.shape, (0, DIMENSIONS))
        self.assertEqual(checkpoints, [])
        self.assertIsNone(embedder._model)
        embedder, batches = self.fake_embedder()
        calls, original = [], embedder._embed_texts

        def record(texts, **kwargs):
            calls.append(kwargs)
            return original(texts, **kwargs)

        embedder._embed_texts = record
        embedder.embed_query("find the caller")
        self.assertEqual(calls, [{}])
        self.assertEqual(len(batches), 1)


class CancellableCheckpointTests(unittest.TestCase):
    def test_explicitly_supported_callbacks_and_cancellation_are_forwarded(self):
        events = []

        class Cooperative:
            def embed_documents(self, texts, *, cancel_check=None,
                                progress_callback=None, batch_callback=None):
                cancel_check()
                events.append("forward")
                batch_callback([0], np.asarray([[1., 0.]], dtype=np.float32))
                progress_callback(1, 1)
                return "result"

        wrapper = CancellableEmbedder(Cooperative(), lambda: events.append("check"))
        result = wrapper.embed_documents(
            ["document"], batch_callback=lambda *_: events.append("checkpoint"),
            progress_callback=lambda *_: events.append("progress"))
        self.assertEqual(result, "result")
        self.assertEqual(events, ["check", "check", "forward", "check", "checkpoint", "progress", "check"])

    def test_wrapper_checks_before_custom_embedder_checkpoint_without_cancel_support(self):
        checkpoints = []
        cancelled = False

        def check():
            if cancelled:
                raise SearchCancelled("cancelled during custom inference")

        class Custom:
            def embed_documents(self, texts, *, batch_callback=None):
                nonlocal cancelled
                cancelled = True
                batch_callback([0], np.asarray([[1., 0.]], dtype=np.float32))
                raise AssertionError("cancelled callback must stop the embedder")

        wrapper = CancellableEmbedder(Custom(), check)
        with self.assertRaises(SearchCancelled):
            wrapper.embed_documents(["document"], batch_callback=lambda *batch: checkpoints.append(batch))
        self.assertEqual(checkpoints, [])

    def test_legacy_and_kwargs_only_embedders_keep_pre_and_post_result_checks(self):
        for supports_kwargs in (False, True):
            with self.subTest(supports_kwargs=supports_kwargs):
                events = []

                class Legacy:
                    def embed_documents(self, texts):
                        events.append("forward")
                        return "legacy result"

                class KwargsOnly:
                    def embed_documents(self, texts, **kwargs):
                        if kwargs:
                            raise AssertionError("implicit support must not receive callbacks")
                        events.append("forward")
                        return "legacy result"

                wrapper = CancellableEmbedder(KwargsOnly() if supports_kwargs else Legacy(),
                                             lambda: events.append("check"))
                result = wrapper.embed_documents(["document"], batch_callback=lambda *_: self.fail(),
                                                 progress_callback=lambda *_: self.fail())
                self.assertEqual(result, "legacy result")
                self.assertEqual(events, ["check", "forward", "check"])

    def test_final_cancellation_check_remains_for_legacy_result(self):
        checks = []

        def check():
            checks.append(None)
            if len(checks) == 2:
                raise SearchCancelled("cancelled before final result")

        class Legacy:
            def embed_documents(self, texts):
                return "must not escape"

        with self.assertRaises(SearchCancelled):
            CancellableEmbedder(Legacy(), check).embed_documents(["document"], batch_callback=lambda *_: self.fail())


if __name__ == "__main__":
    unittest.main()
