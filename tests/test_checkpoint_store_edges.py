"""Adversarial checkpoint inputs cannot partially replace the searchable index."""

import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import pytest

from semantic_search.corpus import Snippet
from semantic_search.index import document_text
from semantic_search.store import SQLiteIndex


METADATA = {"id": "checkpoint-edge-fixture", "revision": "fixed", "dimensions": 2}


def snippet(identifier, text=None):
    return Snippet(identifier, f"src/{identifier}.py", identifier, 1, 2,
                   text or f"return {identifier}()\n", "unused-supplied-hash")


def vectors_for(documents):
    return np.asarray([[len(text), sum(text.encode())] for text in documents], dtype=np.float32)


class ScriptedEmbedder:
    def __init__(self, operation, metadata=None):
        self.operation = operation
        self.metadata = dict(METADATA if metadata is None else metadata)
        self.calls = []

    def embed_documents(self, documents, *, batch_callback=None, progress_callback=None):
        self.calls.append(list(documents))
        return self.operation(documents, batch_callback, progress_callback)


def complete(documents, batch_callback, progress_callback):
    vectors = vectors_for(documents)
    batch_callback(list(range(len(documents))), vectors)
    if progress_callback is not None:
        progress_callback(len(documents), len(documents))
    return vectors


def active_snapshot(path):
    """Inspect every promoted component, excluding the independent vector cache."""
    with sqlite3.connect(path) as connection:
        return {
            "snippets": connection.execute("SELECT * FROM snippets ORDER BY position").fetchall(),
            "fts": connection.execute("SELECT * FROM snippets_fts ORDER BY rowid").fetchall(),
            "vectors": connection.execute(
                "SELECT * FROM active_vectors ORDER BY snippet_id").fetchall(),
            "metadata": connection.execute("SELECT * FROM metadata ORDER BY key").fetchall(),
        }


def cached_vectors(path):
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT document_hash,dimensions,vector FROM embeddings ORDER BY document_hash").fetchall()
    return {key: np.frombuffer(blob, dtype="<f8").reshape(dimensions)
            for key, dimensions, blob in rows}


def document_hash(item):
    return hashlib.sha256(document_text(item).encode()).hexdigest()


@pytest.fixture
def indexed(tmp_path):
    path = tmp_path / "index.sqlite3"
    store = SQLiteIndex(path)
    old = snippet("old")
    store.refresh([old], ScriptedEmbedder(complete))
    return store, path, old, active_snapshot(path)


@pytest.mark.parametrize("indices", [
    None, 0, True, [], [True], [0.0], ["0"], [[0]], [-1], [1], [0, 0],
    np.asarray([True]), np.asarray([0.0]), np.asarray([[0]]),
], ids=["none", "scalar", "bool-scalar", "empty", "bool", "float", "string",
        "nested", "negative", "outside", "duplicate", "numpy-bool", "numpy-float",
        "numpy-nested"])
def test_malformed_checkpoint_indices_never_write_or_promote(indexed, indices):
    store, path, _, snapshot = indexed
    before = cached_vectors(path)

    def malformed(documents, callback, progress):
        callback(indices, [[1., 2.]])
        return vectors_for(documents)

    with pytest.raises(ValueError):
        store.refresh([snippet("new")], ScriptedEmbedder(malformed))
    assert active_snapshot(path) == snapshot
    assert cached_vectors(path).keys() == before.keys()


@pytest.mark.parametrize("vectors", [
    [[0., 0.]], [[np.nan, 1.]], [[np.inf, 1.]], [[1j, 1.]],
    [[1.]], [[1., 2., 3.]], [], [1., 2.], [[1., 2.], [3., 4.]], [["bad", 1.]],
], ids=["zero", "nan", "infinite", "complex", "short", "wide", "empty",
        "one-dimensional", "extra-row", "nonnumeric"])
def test_malformed_checkpoint_vectors_never_write_or_promote(indexed, vectors):
    store, path, _, snapshot = indexed
    before = cached_vectors(path)

    def malformed(documents, callback, progress):
        callback([0], vectors)
        return vectors_for(documents)

    with pytest.raises(ValueError):
        store.refresh([snippet("new")], ScriptedEmbedder(malformed))
    assert active_snapshot(path) == snapshot
    assert cached_vectors(path).keys() == before.keys()


def test_invalid_later_row_rolls_back_the_entire_checkpoint_batch(indexed):
    store, path, _, snapshot = indexed

    def malformed(documents, callback, progress):
        callback([0, 1], [[1., 2.], [np.nan, 3.]])
        return vectors_for(documents)

    with pytest.raises(ValueError):
        store.refresh([snippet("one"), snippet("two")], ScriptedEmbedder(malformed))
    assert active_snapshot(path) == snapshot
    assert len(cached_vectors(path)) == 1


def test_repeated_index_keeps_only_the_first_completed_checkpoint(indexed):
    store, path, _, snapshot = indexed
    items = [snippet("one"), snippet("two")]

    def duplicate(documents, callback, progress):
        values = vectors_for(documents)
        callback([0], values[[0]])
        callback([0], values[[0]])
        return values

    with pytest.raises(ValueError):
        store.refresh(items, ScriptedEmbedder(duplicate))
    assert active_snapshot(path) == snapshot
    assert len(cached_vectors(path)) == 2
    np.testing.assert_array_equal(cached_vectors(path)[document_hash(items[0])],
                                  vectors_for([document_text(items[0])])[0])
    assert document_hash(items[1]) not in cached_vectors(path)


def test_incomplete_callback_coverage_cannot_promote_a_valid_final_matrix(indexed):
    store, path, _, snapshot = indexed
    items = [snippet("one"), snippet("two")]

    def incomplete(documents, callback, progress):
        values = vectors_for(documents)
        callback([1], values[[1]])
        return values

    with pytest.raises(ValueError):
        store.refresh(items, ScriptedEmbedder(incomplete))
    assert active_snapshot(path) == snapshot
    assert document_hash(items[0]) not in cached_vectors(path)
    assert document_hash(items[1]) in cached_vectors(path)


def test_final_matrix_must_agree_with_completed_checkpoint_vectors(indexed):
    store, path, _, snapshot = indexed
    item = snippet("new")
    authoritative = vectors_for([document_text(item)])

    def inconsistent(documents, callback, progress):
        callback([0], authoritative)
        return authoritative + 1

    with pytest.raises(ValueError):
        store.refresh([item], ScriptedEmbedder(inconsistent))
    assert active_snapshot(path) == snapshot
    np.testing.assert_array_equal(cached_vectors(path)[document_hash(item)], authoritative[0])


def test_out_of_order_native_batches_preserve_requested_order_and_duplicate_locations(tmp_path):
    store = SQLiteIndex(tmp_path / "index.sqlite3")
    one, two, three = snippet("one"), snippet("two"), snippet("three")
    duplicate = replace(one, id="shifted", start_line=10, end_line=11)
    items = [one, two, duplicate, three]
    def out_of_order(documents, callback, progress):
        values = vectors_for(documents)
        callback(np.asarray([2, 0], dtype=np.uint64), values[[2, 0]])
        callback((np.int64(1),), values[[1]])
        return values

    embedder = ScriptedEmbedder(out_of_order)
    actual, stats = store.refresh(items, embedder)
    assert embedder.calls == [[document_text(one), document_text(two), document_text(three)]]
    np.testing.assert_array_equal(actual, vectors_for([document_text(item) for item in items]))
    assert stats["total_windows"] == 4
    assert len(cached_vectors(store.db_path)) == 3
    assert SQLiteIndex(store.db_path).snippets == items


def test_callback_buffer_mutation_cannot_change_durable_or_comparison_vectors(tmp_path):
    store = SQLiteIndex(tmp_path / "index.sqlite3")
    item = snippet("new")
    expected = np.asarray([[1., 2.]], dtype=np.float64)

    def mutates(documents, callback, progress):
        shared = expected.copy()
        callback([0], shared)
        shared[:] = 99
        return expected.copy()

    actual, _ = store.refresh([item], ScriptedEmbedder(mutates))
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(cached_vectors(store.db_path)[document_hash(item)], expected[0])


def test_no_progress_observer_is_required_to_checkpoint_and_resume(indexed):
    store, path, _, snapshot = indexed
    items = [snippet("one"), snippet("two")]

    def interrupted(documents, callback, progress):
        assert progress is None
        values = vectors_for(documents)
        callback([1], values[[1]])
        raise RuntimeError("interrupted after durable batch")

    with pytest.raises(RuntimeError, match="interrupted after durable batch"):
        store.refresh(items, ScriptedEmbedder(interrupted))
    assert active_snapshot(path) == snapshot
    assert document_hash(items[1]) in cached_vectors(path)
    resumed = ScriptedEmbedder(complete)
    actual, stats = SQLiteIndex(path).refresh(items, resumed)
    assert resumed.calls == [[document_text(items[0])]]
    np.testing.assert_array_equal(actual, vectors_for([document_text(item) for item in items]))
    assert (stats["indexed_windows"], stats["reused_windows"]) == (1, 1)


def test_raising_progress_observer_retains_completed_batch_without_promotion(indexed):
    store, path, _, snapshot = indexed
    items = [snippet("one"), snippet("two")]

    def interrupted(documents, callback, progress):
        values = vectors_for(documents)
        callback([0], values[[0]])
        progress(1, len(documents))
        raise AssertionError("observer should have interrupted this call")

    def report(event):
        if event["phase"] == "embed" and event["completed"] == 1:
            raise RuntimeError("observer stopped indexing")

    with pytest.raises(RuntimeError, match="observer stopped indexing"):
        store.refresh(items, ScriptedEmbedder(interrupted), progress=report)
    assert active_snapshot(path) == snapshot
    assert document_hash(items[0]) in cached_vectors(path)
    assert document_hash(items[1]) not in cached_vectors(path)


def test_independent_writers_cannot_mix_inferred_dimensions_for_one_fingerprint(tmp_path):
    path = tmp_path / "index.sqlite3"
    first, second = SQLiteIndex(path), SQLiteIndex(path)
    entered, proceed = threading.Event(), threading.Event()
    metadata = {"id": "inferred-dimension-race", "revision": "fixed"}

    def slow_three_dimensional(documents, callback, progress):
        entered.set()
        if not proceed.wait(timeout=5):
            raise RuntimeError("test writer timed out")
        values = np.asarray([[1., 2., 3.]])
        callback([0], values)
        return values

    def two_dimensional(documents, callback, progress):
        values = np.asarray([[1., 2.]])
        callback([0], values)
        return values

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(second.refresh, [snippet("three")],
                                  ScriptedEmbedder(slow_three_dimensional, metadata))
        try:
            assert entered.wait(timeout=5)
            first.refresh([snippet("two")], ScriptedEmbedder(two_dimensional, metadata))
            committed = active_snapshot(path)
        finally:
            proceed.set()
        with pytest.raises(ValueError):
            pending.result(timeout=5)
    assert active_snapshot(path) == committed
    assert len(cached_vectors(path)) == 1
    assert {len(vector) for vector in cached_vectors(path).values()} == {2}


def test_independent_writer_reuses_first_committed_vector_after_a_key_collision(tmp_path):
    path = tmp_path / "index.sqlite3"
    first, second = SQLiteIndex(path), SQLiteIndex(path)
    item = snippet("shared")
    entered, proceed = threading.Event(), threading.Event()

    def second_writer(documents, callback, progress):
        entered.set()
        if not proceed.wait(timeout=5):
            raise RuntimeError("test writer timed out")
        values = np.asarray([[0., 1.]], dtype=np.float32)
        callback([0], values)
        return values

    def first_writer(documents, callback, progress):
        values = np.asarray([[1., 0.]], dtype=np.float32)
        callback([0], values)
        return values

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(second.refresh, [item], ScriptedEmbedder(second_writer))
        try:
            assert entered.wait(timeout=5)
            first.refresh([item], ScriptedEmbedder(first_writer))
        finally:
            proceed.set()
        actual, _ = pending.result(timeout=5)
    np.testing.assert_array_equal(actual, [[1., 0.]])
    np.testing.assert_array_equal(cached_vectors(path)[document_hash(item)], [1., 0.])
    with sqlite3.connect(path) as connection:
        stored = connection.execute("SELECT vector FROM active_vectors").fetchone()[0]
    np.testing.assert_array_equal(np.frombuffer(stored, dtype="<f4"), [1., 0.])
    assert SQLiteIndex(path).snippets == [item]


def test_mixed_dimensions_already_in_a_fingerprint_fail_before_new_inference(indexed):
    store, path, _, snapshot = indexed
    with sqlite3.connect(path) as connection:
        fingerprint = connection.execute("SELECT model_fingerprint FROM embeddings").fetchone()[0]
        connection.execute("INSERT INTO embeddings VALUES (?,?,?,?)",
                           (fingerprint, "unrelated-corrupt-cache-entry", 3,
                            np.asarray([1., 2., 3.], dtype="<f8").tobytes()))
    embedder = ScriptedEmbedder(complete)
    with pytest.raises(ValueError):
        store.refresh([snippet("new")], embedder)
    assert embedder.calls == []
    assert active_snapshot(path) == snapshot
