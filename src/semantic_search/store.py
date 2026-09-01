"""Durable incremental embeddings and an atomic active source-window snapshot."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from semantic_search.index import document_text

SCHEMA_VERSION = 1


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
            if version not in (0, SCHEMA_VERSION):
                raise ValueError(f"Unsupported SQLite index schema: {version}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    model_fingerprint TEXT NOT NULL,
                    document_hash TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    PRIMARY KEY (model_fingerprint, document_hash)
                );
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
                    model_fingerprint TEXT NOT NULL,
                    FOREIGN KEY (model_fingerprint, document_hash)
                        REFERENCES embeddings (model_fingerprint, document_hash)
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def refresh(self, snippets, embedder, *, before_commit=None):
        """Return vectors in input order; replace the snapshot after validation only."""
        started = time.perf_counter()
        snippets = list(snippets)
        if len({snippet.id for snippet in snippets}) != len(snippets):
            raise ValueError("Snippet IDs must be unique")
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
        documents = [document_text(snippet) for snippet in snippets]
        hashes = [hashlib.sha256(document.encode()).hexdigest() for document in documents]
        unique_documents = dict(zip(hashes, documents))

        with self._refresh_lock:
            cached = {}
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN")
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
                        try:
                            if not isinstance(stored_dimensions, int) or stored_dimensions < 1:
                                raise ValueError("Stored embedding dimensions must be positive")
                            vector = np.frombuffer(blob, dtype="<f8")
                            values = _matrix(vector.reshape(1, stored_dimensions), 1, dimensions)
                        except (TypeError, ValueError) as error:
                            raise ValueError("Stored embedding vector is invalid") from error
                        if dimensions is None:
                            dimensions = stored_dimensions
                        cached[document_hash] = values[0]

            missing = [key for key in unique_documents if key not in cached]
            new_vectors = {}
            if missing:
                values = _matrix(embedder.embed_documents([unique_documents[key] for key in missing]),
                                 len(missing), dimensions)
                dimensions = values.shape[1]
                new_vectors = dict(zip(missing, values))
            vectors_by_hash = cached | new_vectors
            matrix = (np.stack([vectors_by_hash[key] for key in hashes]) if hashes
                      else np.empty((0, dimensions or 0), dtype=np.float64))
            active = {"model_fingerprint": fingerprint, "embedding": metadata,
                      "dimensions": dimensions, "updated_at": datetime.now(timezone.utc).isoformat()}
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                previous_ids = {row[0] for row in connection.execute("SELECT id FROM snippets")}
                if before_commit is not None:
                    before_commit()
                for document_hash, vector in new_vectors.items():
                    connection.execute(
                        "INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?)",
                        (fingerprint, document_hash, dimensions,
                         sqlite3.Binary(vector.astype("<f8", copy=False).tobytes())),
                    )
                connection.execute("DELETE FROM snippets")
                connection.executemany(
                    "INSERT INTO snippets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(snippet.id, position, snippet.path, snippet.symbol,
                      snippet.start_line, snippet.end_line, snippet.text, snippet.content_hash,
                      hashes[position], fingerprint)
                     for position, snippet in enumerate(snippets)],
                )
                connection.execute("INSERT OR REPLACE INTO metadata VALUES ('active', ?)",
                                   (json.dumps(active, sort_keys=True),))
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
        active = json.loads(row[0]) if row else {
            "model_fingerprint": None, "embedding": None, "dimensions": None, "updated_at": None}
        return {"db_path": str(self.db_path), "schema_version": SCHEMA_VERSION,
                "total_windows": total, **active}

    def close(self):
        """Connections are already closed at the end of each operation."""
