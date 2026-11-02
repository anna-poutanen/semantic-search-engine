"""Invented CPU-only fixtures for the opt-in canonical vector read boundary."""
import hashlib
import json
import sqlite3
from dataclasses import replace

import numpy as np
import pytest

from semantic_search.corpus import Snippet
from semantic_search.index import document_text
from semantic_search.source import physical_lines
from semantic_search.store import SQLiteIndex


class FixtureEmbedder:
    def __init__(self, revision="fixture-v1"):
        self.metadata = {"id": "invented-cpu", "revision": revision, "dimensions": 3,
                         "normalization": "fixture-original-float32"}
        self.calls = 0

    def embed_documents(self, documents):
        self.calls += 1
        return np.array([[1.5, -.125, index + .25] for index in range(len(documents))],
                        dtype=np.float32)


def snippet(identifier="alpha", source="def alpha():\r\n    return 1\r\n", *, start=3):
    return Snippet(identifier, f"src/{identifier}.py", identifier, start,
                   start + len(physical_lines(source)) - 1, source,
                   hashlib.sha256(source.encode()).hexdigest())


def row(item, **changes):
    value = {"id": item.id, "path": item.path, "symbol": item.symbol,
             "start_line": item.start_line, "end_line": item.end_line,
             "window_end_line": item.end_line, "source": item.text,
             "source_hash": item.content_hash, "content_hash": item.content_hash,
             "truncated": False, "retrieval_score": .5, "origin": "retrieval"}
    value.update(changes)
    return value


@pytest.fixture
def indexed(tmp_path):
    index = SQLiteIndex(tmp_path / "invented.sqlite3")
    embedder = FixtureEmbedder()
    items = [snippet(), snippet("beta", "def beta():\n    return 2\n", start=19)]
    matrix, _ = index.refresh(items, embedder)
    return index, embedder, items, matrix


def read(indexed, rows=None, **options):
    index, embedder, items, _ = indexed
    return index.read_semantic_vectors(
        [row(item) for item in items] if rows is None else rows,
        model_metadata=options.pop("model_metadata", embedder.metadata), **options)


def mutate(indexed, sql, params=()):
    with sqlite3.connect(indexed[0].db_path) as connection:
        connection.execute(sql, params)


def test_returns_canonical_outputs_in_request_order_without_active_renormalization(indexed):
    index, embedder, items, matrix = indexed
    result = read(indexed, [row(items[1]), row(items[0])])
    assert list(result) == ["beta", "alpha"]
    for item, expected in zip(items, matrix):
        assert result[item.id].dtype == np.float32
        np.testing.assert_array_equal(result[item.id], expected)
    with sqlite3.connect(index.db_path) as connection:
        normalized = np.frombuffer(connection.execute(
            "SELECT vector FROM active_vectors WHERE snippet_id='alpha'").fetchone()[0],
            dtype="<f4")
    assert not np.array_equal(normalized, result["alpha"])
    assert embedder.calls == 1
    result["alpha"][0] = 999
    np.testing.assert_array_equal(read(indexed)["alpha"], matrix[0])


def test_read_is_query_only_one_closed_transaction_with_no_database_changes(indexed, monkeypatch):
    index = indexed[0]
    statements, closed_changes = [], []

    class ReadConnection(sqlite3.Connection):
        def close(self):
            closed_changes.append(self.total_changes)
            return super().close()

    connections = []

    def connect(*, read_only=False):
        assert read_only is True
        connection = sqlite3.connect(index.db_path.resolve().as_uri() + "?mode=ro", uri=True, factory=ReadConnection)
        connection.set_trace_callback(statements.append)
        connections.append(connection)
        return connection

    monkeypatch.setattr(index, "_connect", connect)
    read(indexed)
    assert len(connections) == 1 and closed_changes == [0]
    assert statements[:2] == ["PRAGMA query_only=ON", "BEGIN"]
    assert statements[-1] == "COMMIT"
    assert sum(statement == "BEGIN" for statement in statements) == 1
    assert all(not any(token in statement.upper() for token in ("UPDATE ", "INSERT ", "DELETE "))
               for statement in statements)
    assert all("active_vectors" not in statement for statement in statements)
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")


def test_one_snapshot_covers_metadata_and_vectors_during_concurrent_model_refresh(indexed):
    index, old_embedder, items, original = indexed
    calls = 0
    new_embedder = FixtureEmbedder("fixture-v2")

    def replace_snapshot_after_metadata_read():
        nonlocal calls
        calls += 1
        if calls == 4:  # Entry, two requested rows, then the first snapshot check.
            index.refresh([snippet("replacement")], new_embedder)

    result = read(indexed, cancel_check=replace_snapshot_after_metadata_read)
    for item, expected in zip(items, original):
        np.testing.assert_array_equal(result[item.id], expected)
    assert index.status()["embedding"] == new_embedder.metadata
    with pytest.raises(ValueError, match="Active semantic model"):
        read(indexed, model_metadata=old_embedder.metadata)


def test_same_model_new_source_makes_old_rows_stale_but_keeps_immutable_cache(indexed):
    index, embedder, items, _ = indexed
    changed = snippet("alpha", "def alpha():\n    return 777\n")
    index.refresh([changed, items[1]], embedder)
    with pytest.raises(ValueError, match="Current semantic source row differs"):
        read(indexed)
    assert "alpha" in read(indexed, [row(changed)])
    with sqlite3.connect(index.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 3


def test_saved_and_disposable_overlay_rows_validate_literal_source_not_unstored_provenance(indexed):
    index, embedder, items, _ = indexed
    np.testing.assert_array_equal(read(indexed, [row(items[0], source_origin="saved")])["alpha"],
                                  read(indexed)["alpha"])
    buffered = snippet("alpha", "def alpha():\n    return 'unsaved'\n")
    index.refresh([buffered], embedder)  # Invented stand-in for a disposable child snapshot.
    buffered_row = row(buffered, source_origin="overlay", buffer_version=6, buffer_hash="a" * 64)
    assert "alpha" in read(indexed, [buffered_row])
    # Versions/file hashes are deliberately not asserted as stored provenance.
    assert "alpha" in read(indexed, [{**buffered_row, "buffer_version": 7, "buffer_hash": "b" * 64}])
    with pytest.raises(ValueError, match="Current semantic source row differs"):
        read(indexed, [row(items[0], source_origin="overlay", buffer_version=6, buffer_hash="a" * 64)])


@pytest.mark.parametrize("change", [
    {"source_origin": None}, {"source_origin": "buffer"}, {"source_origin": []},
    {"source_origin": "saved", "buffer_version": 0}, {"buffer_hash": "a" * 64},
    {"source_origin": "overlay"},
    {"source_origin": "overlay", "buffer_version": True, "buffer_hash": "a" * 64},
    {"source_origin": "overlay", "buffer_version": -1, "buffer_hash": "a" * 64},
    {"source_origin": "overlay", "buffer_version": 2**53, "buffer_hash": "a" * 64},
    {"source_origin": "overlay", "buffer_version": 0, "buffer_hash": "A" * 64},
    {"source_origin": "overlay", "buffer_version": 0, "buffer_hash": "a" * 63},
    {"source_origin": "overlay", "buffer_version": 0, "buffer_hash": None},
])
def test_invalid_provenance_shapes_fail(indexed, change):
    with pytest.raises(ValueError, match="provenance"):
        read(indexed, [row(indexed[2][0], **change)])


@pytest.mark.parametrize("change", [
    {"id": None}, {"id": ""}, {"path": ""}, {"symbol": ""}, {"source": ""},
    {"start_line": True}, {"end_line": 4.0}, {"window_end_line": None},
    {"start_line": 0}, {"end_line": 5}, {"window_end_line": 5},
    {"truncated": True}, {"truncated": 0}, {"source_hash": "wrong"}, {"content_hash": "wrong"},
])
def test_incomplete_clipped_or_invalid_row_fails(indexed, change):
    with pytest.raises(ValueError):
        read(indexed, [row(indexed[2][0], **change)])


def test_unicode_paragraph_separators_are_source_data_and_crlf_is_literal(indexed):
    index, embedder, _, _ = indexed
    item = snippet("unicode", 'def unicode():\r\n    return "a\u2028b\u2029c"\r\n')
    index.refresh([item], embedder)
    assert item.end_line == item.start_line + 1
    assert "unicode" in read(indexed, [row(item)])
    altered = item.text.replace("\r\n", "\n")
    changed = replace(item, text=altered, content_hash=hashlib.sha256(altered.encode()).hexdigest())
    with pytest.raises(ValueError, match="Current semantic source row differs"):
        read(indexed, [row(changed)])


@pytest.mark.parametrize("field,value", [("id", "missing"), ("path", "src/other.py"),
                                         ("symbol", "renamed"), ("start_line", 8)])
def test_valid_row_identity_must_match_current_snapshot(indexed, field, value):
    changed = replace(indexed[2][0], **{field: value})
    if field == "start_line":
        changed = replace(changed, end_line=changed.start_line + 1)
    with pytest.raises(ValueError, match="differs|missing"):
        read(indexed, [row(changed)])


def test_duplicate_ids_and_unsupported_rows_container_fail(indexed):
    item = row(indexed[2][0])
    with pytest.raises(ValueError, match="unique"):
        read(indexed, [item, item])
    with pytest.raises(ValueError, match="list or tuple"):
        read(indexed, iter([item]))
    with pytest.raises(ValueError, match="full window"):
        read(indexed, [None])
    assert read(indexed, ()) == {}


@pytest.mark.parametrize("metadata", [None, [], {}, {"dimensions": True}, {"dimensions": 0},
                                      {"dimensions": 3.0}, {"dimensions": 3, "bad": float("nan")},
                                      {"dimensions": 3, "bad": object()}])
def test_invalid_requested_metadata_fails(indexed, metadata):
    with pytest.raises(ValueError):
        read(indexed, model_metadata=metadata)


def test_different_requested_model_metadata_never_uses_other_cached_vectors(indexed):
    changed = {**indexed[1].metadata, "revision": "different"}
    with pytest.raises(ValueError, match="Active semantic model"):
        read(indexed, model_metadata=changed)


@pytest.mark.parametrize("active", [None, [], "bad", {}, {"dimensions": True},
                                    {"model_fingerprint": "a" * 64, "dimensions": 3}])
def test_missing_or_invalid_active_metadata_fails(indexed, active):
    mutate(indexed, "UPDATE metadata SET value=? WHERE key='active'", (json.dumps(active),))
    with pytest.raises(ValueError, match="Active semantic model"):
        read(indexed)


@pytest.mark.parametrize("field,value", [("model_fingerprint", "b" * 64), ("dimensions", 2),
                                         ("dimensions", 3.0), ("embedding", {"dimensions": 3})])
def test_active_fingerprint_metadata_and_dimensions_must_all_match(indexed, field, value):
    with sqlite3.connect(indexed[0].db_path) as connection:
        active = json.loads(connection.execute("SELECT value FROM metadata WHERE key='active'").fetchone()[0])
    active[field] = value
    mutate(indexed, "UPDATE metadata SET value=? WHERE key='active'", (json.dumps(active),))
    with pytest.raises(ValueError, match="Active semantic model"):
        read(indexed)


def test_lexical_refresh_cannot_supply_semantic_vectors(indexed):
    indexed[0].refresh_lexical(indexed[2])
    with pytest.raises(ValueError, match="Active semantic model"):
        read(indexed)


@pytest.mark.parametrize("field,value", [("path", "src/stale.py"), ("symbol", "stale"),
                                         ("start_line", 99), ("end_line", 99), ("text", "stale"),
                                         ("content_hash", "c" * 64), ("document_hash", "d" * 64),
                                         ("model_fingerprint", "e" * 64)])
def test_corrupt_current_snippet_identity_or_wrapper_hash_fails(indexed, field, value):
    mutate(indexed, f"UPDATE snippets SET {field}=? WHERE id='alpha'", (value,))
    with pytest.raises(ValueError, match="Current semantic source row differs"):
        read(indexed)


def test_missing_current_snippet_or_canonical_vector_is_not_repaired(indexed):
    index, embedder, _, _ = indexed
    mutate(indexed, "DELETE FROM embeddings")
    with pytest.raises(ValueError, match="Stored embedding vector is invalid"):
        read(indexed)
    assert embedder.calls == 1
    mutate(indexed, "DELETE FROM snippets WHERE id='alpha'")
    with pytest.raises(ValueError):
        read(indexed)
    with sqlite3.connect(index.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0


@pytest.mark.parametrize("vector", [
    np.array([0, 0, 0], dtype="<f8"), np.array([np.nan, 1, 2], dtype="<f8"),
    np.array([np.inf, 1, 2], dtype="<f8"), np.array([.1, 1, 2], dtype="<f8"),
    np.array([1e100, 1, 2], dtype="<f8"), np.array([1e-100, 1, 2], dtype="<f8"),
    np.array([1, 2], dtype="<f8"), np.array([1, 2, 3], dtype="<f4"),
])
def test_invalid_lossy_or_non_float64_canonical_vector_fails(indexed, vector):
    mutate(indexed, "UPDATE embeddings SET vector=?", (sqlite3.Binary(vector.tobytes()),))
    with pytest.raises(ValueError, match="Stored embedding vector|exact float32"):
        read(indexed)
    assert indexed[1].calls == 1


def test_stored_dimensions_must_match_metadata(indexed):
    mutate(indexed, "UPDATE embeddings SET dimensions=2")
    with pytest.raises(ValueError, match="Stored embedding vector"):
        read(indexed)


def test_exact_float32_subnormal_and_signed_zero_are_preserved(indexed):
    original = np.array([np.nextafter(np.float32(0), np.float32(1)), -0., 1.], dtype=np.float32)
    mutate(indexed, "UPDATE embeddings SET vector=?", (sqlite3.Binary(original.astype("<f8").tobytes()),))
    result = read(indexed)["alpha"]
    assert result.tobytes() == original.tobytes()


@pytest.mark.parametrize("cancel_at", range(1, 9))
def test_cancellation_is_propagated_before_during_and_after_read(indexed, cancel_at):
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == cancel_at:
            raise RuntimeError("fixture cancellation")

    with pytest.raises(RuntimeError, match="fixture cancellation"):
        read(indexed, cancel_check=cancel)
    assert indexed[1].calls == 1
    assert len(read(indexed)) == 2


def test_noncallable_cancellation_check_fails(indexed):
    with pytest.raises(ValueError, match="callable"):
        read(indexed, cancel_check=None)


def test_more_than_one_sql_parameter_chunk_uses_one_snapshot(tmp_path):
    index = SQLiteIndex(tmp_path / "many-invented.sqlite3")
    embedder = FixtureEmbedder()
    items = [snippet(str(number), f"return {number}\n", start=1) for number in range(501)]
    index.refresh(items, embedder)
    result = index.read_semantic_vectors(tuple(row(item) for item in reversed(items)),
                                         model_metadata=embedder.metadata)
    assert list(result) == [item.id for item in reversed(items)]
    assert len(result) == 501 and embedder.calls == 1


def test_requested_wrapper_hash_matches_existing_document_format(indexed):
    item = indexed[2][0]
    expected = hashlib.sha256(document_text(item).encode()).hexdigest()
    with sqlite3.connect(indexed[0].db_path) as connection:
        assert connection.execute("SELECT document_hash FROM snippets WHERE id=?", (item.id,)).fetchone()[0] == expected
    assert item.id in read(indexed, [row(item)])


def test_missing_database_is_not_recreated(indexed):
    indexed[0].db_path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        read(indexed)
    assert not indexed[0].db_path.exists()


def test_read_only_uri_handles_special_path_characters(tmp_path):
    index = SQLiteIndex(tmp_path / "invented ? # vectors.sqlite3")
    embedder, item = FixtureEmbedder(), snippet()
    index.refresh([item], embedder)
    assert index.read_semantic_vectors([row(item)], model_metadata=embedder.metadata)[item.id].shape == (3,)
