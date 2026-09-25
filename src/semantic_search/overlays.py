"""Bounded, literal editor text overrides with no persistent saved-index mutation."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.context import indexed_scope
from semantic_search.languages import decode_source, path_excluded, source_eligible, validate_source

MAX_OVERLAY_FILES = 16
MAX_OVERLAY_PATH_BYTES = 512
MAX_OVERLAY_FILE_BYTES = 256 * 1024
MAX_OVERLAY_TOTAL_BYTES = 1024 * 1024
MAX_OVERLAY_BODY_BYTES = 2 * 1024 * 1024
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class BufferOverlay:
    path: str
    text: str
    version: int
    content_hash: str

    @property
    def binding(self):
        return {"path": self.path, "version": self.version, "content_hash": self.content_hash}


def normalize_overlays(entries):
    """Validate and copy a caller's request before filesystem/model work begins."""
    if entries is None:
        return ()
    if not isinstance(entries, list) or len(entries) > MAX_OVERLAY_FILES:
        raise ValueError(f"overlays must be a list of at most {MAX_OVERLAY_FILES} buffers")
    output, seen, total = [], set(), 0
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "text", "version", "content_hash"}:
            raise ValueError("Each overlay requires exactly path, text, version, content_hash")
        path, text, version, content_hash = (item[key] for key in ("path", "text", "version", "content_hash"))
        if not isinstance(path, str) or not path or any(ord(char) < 32 or ord(char) == 127 for char in path):
            raise ValueError("Overlay paths must be nonempty repository-relative POSIX paths")
        relative = PurePosixPath(path)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in path
                or relative.as_posix() != path or relative == PurePosixPath(".")):
            raise ValueError("Overlay paths must be canonical repository-relative POSIX paths")
        if not isinstance(text, str):
            raise ValueError("Overlay text must be a Unicode string")
        try:
            path_bytes, text_bytes = path.encode("utf-8"), text.encode("utf-8")
        except UnicodeError as error:
            raise ValueError("Overlay paths and text must contain valid Unicode") from error
        if len(path_bytes) > MAX_OVERLAY_PATH_BYTES:
            raise ValueError(f"Overlay paths exceed {MAX_OVERLAY_PATH_BYTES} UTF-8 bytes")
        if len(text_bytes) > MAX_OVERLAY_FILE_BYTES:
            raise ValueError(f"Overlay text exceeds {MAX_OVERLAY_FILE_BYTES} UTF-8 bytes per buffer")
        total += len(text_bytes)
        if total > MAX_OVERLAY_TOTAL_BYTES:
            raise ValueError(f"Overlay text exceeds {MAX_OVERLAY_TOTAL_BYTES} UTF-8 bytes in total")
        if type(version) is not int or not 0 <= version <= 2**53 - 1:
            raise ValueError("Overlay version must be a nonnegative safe integer")
        if not isinstance(content_hash, str) or not HASH_PATTERN.fullmatch(content_hash):
            raise ValueError("Overlay content_hash must be a lowercase SHA-256 hash")
        if hashlib.sha256(text_bytes).hexdigest() != content_hash:
            raise ValueError("Overlay content_hash does not match its literal UTF-8 text")
        if path in seen:
            raise ValueError("Overlay paths must be unique")
        seen.add(path)
        output.append(BufferOverlay(path, text, version, content_hash))
    return tuple(sorted(output, key=lambda item: item.path))


class BorrowedEmbedder:
    """Forward to the one resident model; disposing a request never closes it."""

    def __init__(self, parent):
        self.parent = parent

    @property
    def metadata(self):
        return self.parent._get_embedder().metadata

    def embed_documents(self, texts, *, cancel_check=None):
        from semantic_search.backend import CancellableEmbedder
        return CancellableEmbedder(self.parent._get_embedder(), cancel_check or (lambda: None)).embed_documents(texts)

    def embed_query(self, query):
        return self.parent._get_embedder().embed_query(query)

    def close(self):
        pass


def _decoded_sources(entries, raw, check):
    overrides = {entry.path: entry for entry in entries}
    decoded = {}
    for path, data in raw.items():
        check()
        try:
            source = overrides[path].text if path in overrides else decode_source(path, data)
            # Unicode editor text bypasses file coding-cookie decoding entirely.
            validate_source(path, source)
        except (SyntaxError, UnicodeError, LookupError, ValueError, ImportError) as error:
            origin = "overlay" if path in overrides else "saved source"
            raise SourceSnapshotError(f"Cannot parse {origin} {path}: {error}") from error
        decoded[path] = source
    return decoded


class OverlayBackend(SearchBackend):
    """Reuse search/packing under the parent lock against a disposable SQLite index."""

    def __init__(self, parent, entries, digest, raw, decoded, index, check):
        super().__init__(parent.repo, parent.source_root, index.db_path, index=index,
                         embedder=BorrowedEmbedder(parent), h2o_client=parent._h2o, qwen_client=parent._qwen,
                         source_exclusions=parent.source_exclusions)
        self.parent = parent
        self._policy = parent._policy
        self.entries = {entry.path: entry for entry in entries}
        self.saved_digest, self.raw, self.decoded, self.check = digest, dict(raw), dict(decoded), check
        manifest = {"saved_source_manifest": digest,
                    "buffers": [entry.binding for entry in entries]}
        self.overlay_digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()

    @contextmanager
    def _request_lock(self, request_id):
        # The parent already registered the ID and owns the global model lock.
        self.check()
        yield self.check

    def _snapshot(self):
        self.check()
        return self.overlay_digest, self.raw

    def _confirm_snapshot(self, expected):
        self.check()
        if expected != self.overlay_digest:
            raise SourceSnapshotError("Overlay request snapshot changed")
        self.parent._confirm_snapshot(self.saved_digest)
        self.check()

    def _validate_source(self, raw):
        self.check()
        return dict(self.decoded)

    def _source_provenance(self, path):
        entry = self.entries.get(path)
        if entry is None:
            return {"source_origin": "saved"}
        return {"source_origin": "overlay", "buffer_version": entry.version,
                "buffer_hash": entry.content_hash}

    def _search_scope(self):
        return {**indexed_scope(self.source_root, self.source_exclusions),
                "membership": "tracked implementations with request-only editor text overrides",
                "source_state": "overlay", "saved_source_manifest": self.saved_digest,
                "overlays": [entry.binding for entry in self.entries.values()]}

    def close(self):
        # No shared registry cancellation, watcher shutdown, or model disposal.
        if not self._closed:
            self._closed = True
            self._index.close()


def _require_membership(parent, entries, raw):
    try:
        result = subprocess.run(["git", "-C", str(parent.repo), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SourceSnapshotError("Cannot verify tracked overlay membership") from error
    if result.returncode != 0 or result.stdout.strip() != b"true":
        raise ValueError("Overlays require saved tracked files in a Git worktree")
    root = (parent.repo / parent.source_root).resolve()
    for entry in entries:
        path = parent.repo / entry.path
        if (entry.path not in raw or not source_eligible(entry.path)
                or path_excluded(entry.path, parent.source_exclusions)
                or not path.is_relative_to(root) or not path.resolve().is_relative_to(root)):
            raise ValueError("Overlay paths must be tracked eligible saved files inside source_root")


def _file_stat(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _cache_snapshot(paths, check):
    """Read only filesystem bytes; never open an original SQLite connection."""
    before = {path: _file_stat(path) for path in paths}
    hashes = {}
    for path, stat in before.items():
        check()
        if stat is None:
            continue
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                check()
                digest.update(chunk)
        hashes[path] = digest.hexdigest()
    if before != {path: _file_stat(path) for path in paths}:
        raise SourceSnapshotError("Saved vector cache changed during overlay setup; retry with a stable cache")
    check()
    return before, hashes


def _copy_cache(saved, destination, check):
    """Guard database/WAL bytes while copying; recovery touches the private copy.

    This detects cache changes during setup, not an atomic cross-process SQLite
    lock. The parent lock excludes writes by this resident backend.
    """
    saved = saved.resolve()
    paths = (saved, Path(str(saved) + "-wal"))
    try:
        expected = _cache_snapshot(paths, check)
        for path, stat in expected[0].items():
            check()
            if stat is None:
                continue
            copied = destination if path == saved else Path(str(destination) + "-wal")
            digest = hashlib.sha256()
            with path.open("rb") as source, copied.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    check()
                    target.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != expected[1][path]:
                raise SourceSnapshotError("Saved vector cache changed during overlay setup; retry with a stable cache")
        if _cache_snapshot(paths, check) != expected:
            raise SourceSnapshotError("Saved vector cache changed during overlay setup; retry with a stable cache")
    except OSError as error:
        raise SourceSnapshotError(f"Cannot capture a stable saved vector cache: {error}") from error


def search_overlay(parent, entries, settings, check):
    """No parent refresh: even a cold overlay request leaves its saved store absent."""
    check()
    started = time.perf_counter()
    digest, raw = parent._snapshot()
    _require_membership(parent, entries, raw)
    decoded = _decoded_sources(entries, raw, check)
    parent._confirm_snapshot(digest)
    check()
    with tempfile.TemporaryDirectory(prefix="semantic-search-overlay-") as directory:
        destination = Path(directory) / "request.sqlite3"
        saved = getattr(parent._index, "db_path", parent.db_path)
        saved = Path(saved)
        if saved.is_file():
            # No original SQLite connection: even mode=ro may create -wal/-shm.
            # Never call parent's _get_index(): that would create/migrate its DB.
            _copy_cache(saved, destination, check)
        check()
        from semantic_search.store import SQLiteIndex
        index = SQLiteIndex(destination)
        backend = None
        try:
            backend = OverlayBackend(parent, entries, digest, raw, decoded, index, check)
            preparation_s = time.perf_counter() - started
            response = backend.search(**settings)
            response["indexstats"]["elapsed_s"] += preparation_s
            response["timings"]["index_s"] = response["indexstats"]["elapsed_s"]
            return response
        finally:
            if backend is not None:
                backend.close()
            else:
                index.close()
