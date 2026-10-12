"""Content-addressed, local NumPy embedding index for saved code windows."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path

import numpy as np

DOCUMENT_FORMAT = "Path: {path}\nSymbol: {symbol}\nSource:\n{text}"


def document_text(snippet):
    return DOCUMENT_FORMAT.format(path=snippet.path, symbol=snippet.symbol, text=snippet.text)


def index_manifest(snippets, model_metadata):
    return {"schema_version": 2, "embedding": model_metadata, "document_format": DOCUMENT_FORMAT,
            "snippets": [{"id": s.id, "content_hash": s.content_hash,
                          "document_sha256": hashlib.sha256(document_text(s).encode()).hexdigest()}
                         for s in snippets]}


def index_key(manifest):
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_vectors(vectors, snippet_count, model_metadata, message):
    dimensions = model_metadata.get("dimensions")
    if (vectors.ndim != 2 or vectors.shape[0] != snippet_count or vectors.shape[1] == 0
            or (dimensions is not None and vectors.shape[1] != dimensions)):
        raise ValueError(message)
    try:
        valid = (not np.iscomplexobj(vectors) and np.isfinite(vectors).all()
                 and not np.any(np.all(vectors == 0, axis=1)))
    except TypeError as error:
        raise ValueError(message) from error
    if not valid:
        raise ValueError(message)


def build_or_load_index(snippets, embedder, cache_dir, progress=True):
    """Reuse only vectors matching exact source, boundaries, and embedding settings."""
    manifest = index_manifest(snippets, embedder.metadata)
    key = index_key(manifest)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.npz"
    started = time.perf_counter()
    if path.exists():
        try:
            with np.load(path, allow_pickle=False) as cached:
                stored_manifest = json.loads(str(cached["manifest"].item()))
                vectors = cached["vectors"]
        except (OSError, ValueError, KeyError, TypeError, EOFError, zipfile.BadZipFile) as error:
            raise ValueError(f"Embedding cache is unreadable or malformed: {path}") from error
        if stored_manifest != manifest:
            raise ValueError(f"Embedding cache manifest mismatch: {path}")
        _validate_vectors(vectors, len(snippets), manifest["embedding"],
                          f"Embedding cache contains an invalid document matrix: {path}")
        return vectors, {"key": key, "cache_hit": True,
                         "elapsed_s": time.perf_counter() - started, "manifest": manifest}
    if progress:
        print(f"Embedding {len(snippets)} source windows; cache key {key[:12]}", flush=True)
    vectors = np.asarray(embedder.embed_documents([document_text(s) for s in snippets]), dtype=np.float32)
    _validate_vectors(vectors, len(snippets), manifest["embedding"],
                      "Embedding model returned an invalid document matrix")
    handle, temporary = tempfile.mkstemp(dir=cache_dir, suffix=".npz")
    try:
        with os.fdopen(handle, "wb") as stream:
            np.savez_compressed(stream, vectors=vectors, manifest=json.dumps(manifest, sort_keys=True))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return vectors, {"key": key, "cache_hit": False,
                     "elapsed_s": time.perf_counter() - started, "manifest": manifest}
