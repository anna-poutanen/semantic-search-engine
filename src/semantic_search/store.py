"""Durable incremental embeddings and an atomic active source-window snapshot."""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from semantic_search.index import document_text
from semantic_search.corpus import Snippet
from semantic_search.retrieval import tokenize

SCHEMA_VERSION = 2
SQLITE_VEC_VERSION = "0.1.9"
SNIPPET_COLUMNS = "id,path,symbol,start_line,end_line,text,content_hash"


def _limit(limit):
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a nonnegative integer")


def _float32_vectors(matrix):
    """Normalize before conversion so finite extreme values remain representable."""
    if not len(matrix):
        return np.empty(matrix.shape, dtype="<f4")
    scaled = matrix / np.max(np.abs(matrix), axis=1, keepdims=True)
    return (scaled / np.linalg.norm(scaled, axis=1, keepdims=True)).astype("<f4")


def _source_rows(snippets, hashes, fingerprint):
    return [(snippet.id, position, snippet.path, snippet.symbol,
             snippet.start_line, snippet.end_line, snippet.text, snippet.content_hash,
             hashes[position], fingerprint)
            for position, snippet in enumerate(snippets)]


def _documents(snippets):
    snippets = list(snippets)
    if len({snippet.id for snippet in snippets}) != len(snippets):
        raise ValueError("Snippet IDs must be unique")
    documents = [document_text(snippet) for snippet in snippets]
    hashes = [hashlib.sha256(document.encode()).hexdigest() for document in documents]
    return snippets, documents, hashes


def _matrix(vectors, rows, dimensions=None):
    try:
        if np.iscomplexobj(vectors):
            raise ValueError("Embedding vectors must be real")
        values = np.asarray(vectors, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Embedding output must be a numeric matrix") from error
    if (values.ndim != 2 or values.shape[0] != rows or values.shape[1] == 0
            or (dimensions is not None and values.shape[1] != dimensions)):
        raise ValueError("Embedding matrix has incompatible dimensions")
    if not np.isfinite(values).all() or np.any(np.all(values == 0, axis=1)):
        raise ValueError("Embedding vectors must be finite and nonzero")
    return values


def _stored_vector(dimensions, blob, expected=None):
    try:
        if type(dimensions) is not int or dimensions < 1:
            raise ValueError("Invalid stored dimensions")
        return _matrix(np.frombuffer(blob, dtype="<f8").reshape(1, dimensions),
                       1, expected)[0].copy()
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Stored embedding vector is invalid") from error


def _vector_hash(vector):
    return hashlib.sha256(vector.astype("<f8", copy=False).tobytes()).digest()


def _cache_vectors(connection, fingerprint, vectors, dimensions):
    """Insert immutable cache rows in the caller's short write transaction.

    Independent index instances can infer the same missing keys concurrently.
    The first committed vector is canonical; every caller reads that row back.
    The fingerprint's first dimension also serializes initially untyped models.
    """
    if not vectors:
        return {}
    first = connection.execute(
        "SELECT dimensions FROM embeddings WHERE model_fingerprint=? LIMIT 1",
        (fingerprint,)).fetchone()
    if first is not None and first[0] != dimensions:
        raise ValueError("Embedding dimensions differ for the same model fingerprint")
    connection.executemany("INSERT OR IGNORE INTO embeddings VALUES (?,?,?,?)", [
        (fingerprint, key, dimensions, sqlite3.Binary(vector.astype("<f8", copy=False).tobytes()))
        for key, vector in vectors.items()])
    canonical = {}
    keys = list(vectors)
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        for key, stored_dimensions, blob in connection.execute(
                f"SELECT document_hash,dimensions,vector FROM embeddings "
                f"WHERE model_fingerprint=? AND document_hash IN ({placeholders})",
                [fingerprint, *chunk]):
            canonical[key] = _stored_vector(stored_dimensions, blob, dimensions)
    if len(canonical) != len(vectors):
        raise ValueError("Embedding checkpoint is incomplete")
    return canonical


class SQLiteIndex:
    """Cache vectors by rendered source and frozen model metadata, independently of IDs.

    The active snapshot contains only the latest refresh's exact snippets. Cached
    vectors from earlier snapshots remain reusable when a branch is revisited.
    Each operation owns its connection; refreshes on this instance are serialized.
    """

    def __init__(self, db_path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._refresh_lock = threading.Lock()
        with closing(self._connect()) as connection, connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, SCHEMA_VERSION):
                raise ValueError(f"Unsupported SQLite index schema: {version}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    model_fingerprint TEXT NOT NULL,
                    document_hash TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    PRIMARY KEY (model_fingerprint, document_hash)
                )
            """)
            if version == 1:
                connection.execute("ALTER TABLE snippets RENAME TO snippets_v1")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS snippets (
                    id TEXT PRIMARY KEY,
                    position INTEGER NOT NULL UNIQUE,
                    path TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    document_hash TEXT NOT NULL,
                    model_fingerprint TEXT,
                    FOREIGN KEY (model_fingerprint, document_hash)
                        REFERENCES embeddings (model_fingerprint, document_hash)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS snippets_fts USING fts5(
                    id UNINDEXED, terms, tokenize='ascii'
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS active_vectors (
                    snippet_id TEXT PRIMARY KEY REFERENCES snippets(id) ON DELETE CASCADE,
                    model_fingerprint TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL
                )
            """)
            if version == 1:
                connection.execute("INSERT INTO snippets SELECT * FROM snippets_v1")
                connection.execute("DROP TABLE snippets_v1")
                items = [Snippet(*row) for row in connection.execute(
                    f"SELECT {SNIPPET_COLUMNS} FROM snippets ORDER BY position")]
                self._write_fts(connection, items)
                for identifier, fingerprint, dimensions, blob in connection.execute(
                        "SELECT s.id,e.model_fingerprint,e.dimensions,e.vector FROM snippets s "
                        "JOIN embeddings e USING (model_fingerprint,document_hash)").fetchall():
                    try:
                        matrix = _matrix(np.frombuffer(blob, dtype="<f8").reshape(1, dimensions), 1)
                    except (TypeError, ValueError) as error:
                        raise ValueError("Stored embedding vector is invalid") from error
                    connection.execute("INSERT INTO active_vectors VALUES (?,?,?,?)",
                                       (identifier, fingerprint, dimensions,
                                        _float32_vectors(matrix)[0].tobytes()))
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _write_fts(connection, snippets):
        connection.execute("DELETE FROM snippets_fts")
        connection.executemany("INSERT INTO snippets_fts(id,terms) VALUES (?,?)", [
            (snippet.id, " ".join(tokenize(f"{snippet.path} {snippet.symbol}\n{snippet.text}")))
            for snippet in snippets])

    @staticmethod
    def _load_vec(connection):
        # The package supplies the fixed bundled extension; no caller-controlled path.
        try:
            import sqlite_vec
        except ImportError as error:
            raise RuntimeError(f"Dense SQLite search requires sqlite-vec=={SQLITE_VEC_VERSION}") from error
        try:
            connection.enable_load_extension(True)
            try:
                sqlite_vec.load(connection)
            finally:
                connection.enable_load_extension(False)
        except (AttributeError, sqlite3.Error) as error:
            raise RuntimeError("This Python SQLite build cannot load sqlite-vec") from error
        version = connection.execute("SELECT vec_version()").fetchone()[0]
        if version != f"v{SQLITE_VEC_VERSION}":
            raise RuntimeError(f"Expected sqlite-vec {SQLITE_VEC_VERSION}, found {version}")

    @property
    def snippets(self):
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            return [Snippet(*row) for row in connection.execute(
                f"SELECT {SNIPPET_COLUMNS} FROM snippets ORDER BY position")]

    def search_lexical(self, query, limit=10):
        """FTS5 BM25 over the same identifier tokens as the original lexical index.

        Query terms are literal OR matches, never caller-provided FTS syntax.
        FTS5's BM25 constants differ from the experiment's Python baseline.
        """
        _limit(limit)
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms or limit == 0:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT s.id,s.path,s.symbol,s.start_line,s.end_line,s.text,s.content_hash,"
                "bm25(snippets_fts) AS distance FROM snippets_fts "
                "JOIN snippets s ON s.id=snippets_fts.id WHERE snippets_fts MATCH ? "
                "ORDER BY distance,s.id LIMIT ?", (match, limit)).fetchall()
        return [(Snippet(*row[:7]), -float(row[7])) for row in rows]

    def search_dense(self, query_vector, limit=10):
        """Exact float32 cosine scan in SQLite; stable IDs resolve distance ties."""
        _limit(limit)
        if limit == 0:
            return []
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT value FROM metadata WHERE key='active'").fetchone()
            active = json.loads(row[0]) if row else {}
            dimensions = active.get("dimensions")
            if dimensions is None:
                return []
            try:
                query = np.asarray(query_vector)
                if query.ndim != 1:
                    raise ValueError("Query vector must be one-dimensional")
                query = _matrix(query.reshape(1, -1), 1, dimensions)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError(f"Query vector must be finite, nonzero and have shape ({dimensions},)") from error
            self._load_vec(connection)
            rows = connection.execute(
                "SELECT s.id,s.path,s.symbol,s.start_line,s.end_line,s.text,s.content_hash,"
                "vec_distance_cosine(v.vector, ?) AS distance FROM active_vectors v "
                "JOIN snippets s ON s.id=v.snippet_id AND s.model_fingerprint=v.model_fingerprint "
                "WHERE v.model_fingerprint=? AND v.dimensions=? "
                "ORDER BY distance,s.id LIMIT ?",
                (_float32_vectors(query)[0].tobytes(), active.get("model_fingerprint"),
                 dimensions, limit)).fetchall()
        if any(row[7] is None or not np.isfinite(row[7]) for row in rows):
            raise ValueError("Stored SQLite cosine result is not finite")
        return [(Snippet(*row[:7]), 1.0 - float(row[7])) for row in rows]

    def refresh_lexical(self, snippets, *, before_commit=None):
        """Atomically promote saved source/FTS without embedding inference.

        The active dense snapshot is cleared; the document/model vector cache
        remains available for the next dense refresh or a return to an old branch.
        """
        started = time.perf_counter()
        snippets, _, hashes = _documents(snippets)
        with self._refresh_lock, closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = dict(connection.execute("SELECT id,document_hash FROM snippets"))
            if before_commit is not None:
                before_commit()
            connection.execute("DELETE FROM active_vectors")
            connection.execute("DELETE FROM snippets")
            connection.executemany("INSERT INTO snippets VALUES (?,?,?,?,?,?,?,?,?,?)",
                                   _source_rows(snippets, hashes, None))
            self._write_fts(connection, snippets)
            active = {"model_fingerprint": None, "embedding": None, "dimensions": None,
                      "updated_at": datetime.now(timezone.utc).isoformat()}
            connection.execute("INSERT OR REPLACE INTO metadata VALUES ('active',?)",
                               (json.dumps(active, sort_keys=True),))
        reused = sum(previous.get(snippet.id) == key for snippet, key in zip(snippets, hashes))
        return {"indexed_windows": len(snippets) - reused, "reused_windows": reused,
                "deleted_windows": len(previous.keys() - {snippet.id for snippet in snippets}),
                "total_windows": len(snippets), "elapsed_s": time.perf_counter() - started,
                "model_fingerprint": None}

    def refresh(self, snippets, embedder, *, before_commit=None, progress=None):
        """Checkpoint completed batches; promote the complete snapshot atomically.

        A batch callback's indices refer to the supplied missing-document list,
        not completion order. Valid completed vectors survive interruption even
        when the final source check fails. No active source/FTS/vector rows change
        until full output validation and the final transaction succeed. Legacy
        embedders without callbacks retain their whole-result caching behavior.
        ``before_commit`` is a repeatable guard, checked before writes and again
        immediately before committing the active snapshot.
        """
        started = time.perf_counter()
        snippets, documents, hashes = _documents(snippets)
        try:
            metadata_json = json.dumps(embedder.metadata, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False)
            metadata = json.loads(metadata_json)
        except (TypeError, ValueError) as error:
            raise ValueError("Embedding metadata must be finite JSON data") from error
        if not isinstance(metadata, dict):
            raise ValueError("Embedding metadata must be a JSON object")
        fingerprint = hashlib.sha256(metadata_json.encode()).hexdigest()
        dimensions = metadata.get("dimensions")
        if dimensions is not None and (not isinstance(dimensions, int)
                                       or isinstance(dimensions, bool) or dimensions < 1):
            raise ValueError("Embedding dimensions must be a positive integer")
        unique_documents = dict(zip(hashes, documents))

        with self._refresh_lock:
            cached = {}
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN")
                known_dimensions = connection.execute(
                    "SELECT DISTINCT dimensions FROM embeddings WHERE model_fingerprint=? LIMIT 2",
                    (fingerprint,)).fetchall()
                if known_dimensions:
                    stored = known_dimensions[0][0]
                    if (len(known_dimensions) != 1 or type(stored) is not int or stored < 1
                            or (dimensions is not None and stored != dimensions)):
                        raise ValueError("Stored embedding dimensions are inconsistent for the model fingerprint")
                    dimensions = stored
                keys = list(unique_documents)
                for start in range(0, len(keys), 500):
                    chunk = keys[start:start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = connection.execute(
                        f"SELECT document_hash, dimensions, vector FROM embeddings "
                        f"WHERE model_fingerprint=? AND document_hash IN ({placeholders})",
                        [fingerprint, *chunk],
                    )
                    for document_hash, stored_dimensions, blob in rows:
                        cached[document_hash] = _stored_vector(stored_dimensions, blob, dimensions)

            missing = [key for key in unique_documents if key not in cached]
            if progress is not None:
                progress({"phase": "cache", "completed": len(cached),
                          "total": len(unique_documents), "unit": "documents",
                          "cached_documents": len(cached), "pending_documents": len(missing),
                          "windows": len(snippets)})
            new_vectors = {}
            if missing:
                last_completed = 0
                reported = {}

                def checkpoint(indices, batch):
                    nonlocal dimensions
                    if (not isinstance(indices, (list, tuple, np.ndarray))
                            or (isinstance(indices, np.ndarray) and indices.ndim != 1)):
                        raise ValueError("Checkpoint indices must be a nonempty sequence of integers")
                    indices = list(indices)
                    if (not indices or any(isinstance(index, (bool, np.bool_))
                            or not isinstance(index, (int, np.integer))
                            or not 0 <= index < len(missing) for index in indices)
                            or len(set(indices)) != len(indices)
                            or any(index in reported for index in indices)):
                        raise ValueError("Checkpoint indices must be unique, in-range integers")
                    values = _matrix(batch, len(indices), dimensions).copy()
                    batch_dimensions = values.shape[1]
                    batch_vectors = {missing[index]: row for index, row in zip(indices, values)}
                    # Commit before acknowledging the callback or reporting native
                    # batch completion. Never hold a DB transaction over inference.
                    with closing(self._connect()) as connection, connection:
                        connection.execute("BEGIN IMMEDIATE")
                        _cache_vectors(connection, fingerprint, batch_vectors, batch_dimensions)
                    dimensions = batch_dimensions
                    # Hash private float64 rows to validate final output without
                    # retaining a second full copy of all checkpoint matrices.
                    reported.update((int(index), _vector_hash(row)) for index, row in zip(indices, values))

                def report_batch(completed, total):
                    nonlocal last_completed
                    if (type(completed) is not int or type(total) is not int
                            or total != len(missing) or not last_completed <= completed <= total):
                        raise ValueError("Embedding progress must count completed documents")
                    if progress is not None and completed != last_completed:
                        progress({"phase": "embed", "completed": completed,
                                  "total": total, "unit": "documents"})
                    last_completed = completed

                extra = {}
                if "batch_callback" in inspect.signature(embedder.embed_documents).parameters:
                    extra["batch_callback"] = checkpoint
                if progress is not None:
                    progress({"phase": "embed", "completed": 0,
                              "total": len(missing), "unit": "documents"})
                    if "progress_callback" in inspect.signature(embedder.embed_documents).parameters:
                        extra["progress_callback"] = report_batch
                values = _matrix(embedder.embed_documents(
                    [unique_documents[key] for key in missing], **extra),
                                 len(missing), dimensions)
                if reported and (len(reported) != len(missing) or any(
                        reported[index] != _vector_hash(row) for index, row in enumerate(values))):
                    raise ValueError("Embedding checkpoints do not match the complete returned matrix")
                if progress is not None:
                    report_batch(len(missing), len(missing))
                dimensions = values.shape[1]
                new_vectors = dict(zip(missing, values))
            active = {"model_fingerprint": fingerprint, "embedding": metadata,
                      "dimensions": dimensions, "updated_at": datetime.now(timezone.utc).isoformat()}
            if progress is not None:
                progress({"phase": "save", "completed": 0,
                          "total": len(snippets), "unit": "windows"})
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                previous_ids = {row[0] for row in connection.execute("SELECT id FROM snippets")}
                if before_commit is not None:
                    before_commit()
                canonical = _cache_vectors(connection, fingerprint, new_vectors, dimensions)
                vectors_by_hash = cached | canonical
                matrix = (np.stack([vectors_by_hash[key] for key in hashes]) if hashes
                          else np.empty((0, dimensions or 0), dtype=np.float64))
                connection.execute("DELETE FROM active_vectors")
                connection.execute("DELETE FROM snippets")
                connection.executemany(
                    "INSERT INTO snippets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _source_rows(snippets, hashes, fingerprint),
                )
                self._write_fts(connection, snippets)
                normalized = _float32_vectors(matrix)
                connection.executemany("INSERT INTO active_vectors VALUES (?,?,?,?)", [
                    (snippet.id, fingerprint, dimensions, normalized[position].tobytes())
                    for position, snippet in enumerate(snippets)])
                connection.execute("INSERT OR REPLACE INTO metadata VALUES ('active', ?)",
                                   (json.dumps(active, sort_keys=True),))
                if before_commit is not None:
                    before_commit()
            if progress is not None:
                progress({"phase": "save", "completed": len(snippets),
                          "total": len(snippets), "unit": "windows"})
            reused = sum(key in cached for key in hashes)
            return matrix, {"indexed_windows": len(snippets) - reused, "reused_windows": reused,
                            "deleted_windows": len(previous_ids - {snippet.id for snippet in snippets}),
                            "total_windows": len(snippets), "elapsed_s": time.perf_counter() - started,
                            "model_fingerprint": fingerprint}

    def status(self):
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT value FROM metadata WHERE key='active'").fetchone()
            total = connection.execute("SELECT COUNT(*) FROM snippets").fetchone()[0]
            dense_windows = connection.execute("SELECT COUNT(*) FROM active_vectors").fetchone()[0]
        active = json.loads(row[0]) if row else {
            "model_fingerprint": None, "embedding": None, "dimensions": None, "updated_at": None}
        return {"db_path": str(self.db_path), "schema_version": SCHEMA_VERSION,
                "total_windows": total, "dense_windows": dense_windows,
                "lexical_backend": "sqlite_fts5", "dense_backend": "sqlite_vec_cosine_exact",
                "sqlite_vec_version": SQLITE_VEC_VERSION, **active}

    def close(self):
        """Connections are already closed at the end of each operation."""
