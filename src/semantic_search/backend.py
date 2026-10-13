"""Persistent, serialized local search over stable snapshots of saved Python files."""
from __future__ import annotations

import ast
import hashlib
import io
import json
import subprocess
import threading
import time
import tokenize
from pathlib import Path
from urllib.parse import urlsplit

from semantic_search.corpus import _source_files, extract_snippets
from semantic_search.model import H2OClient
from semantic_search.retrieval import BM25Retriever, DenseRetriever, HybridRetriever

MAX_QUERY_CHARS = 8192
MAX_CANDIDATES = 50
MAX_RESULTS = 20
MAX_SOURCE_BUDGET = 100_000


class SourceSnapshotError(RuntimeError):
    """The saved source could not provide a stable, parseable search snapshot."""


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")


class SearchBackend:
    """Keep weights and retrievers resident; one lock serializes all model work.

    Raw file hashes are checked before every search. Source windows and their
    embeddings are reused only after the same source snapshot is confirmed.
    Lexical-only searches do not initialize an embedding model.
    """

    def __init__(self, repo, source_root="src/flask", db_path=None, *, index=None,
                 embedder=None, h2o_url="http://127.0.0.1:8741", h2o_client=None):
        self.repo = Path(repo).resolve()
        if not self.repo.is_dir():
            raise ValueError("repo must be an existing directory")
        if not isinstance(source_root, str) or not source_root.strip():
            raise ValueError("source_root must be a nonempty path")
        root = (self.repo / source_root).resolve()
        if not root.is_relative_to(self.repo):
            raise ValueError("source_root must be inside repo")
        self.source_root = str(root.relative_to(self.repo))
        namespace = hashlib.sha256(f"{self.repo}\n{self.source_root}".encode()).hexdigest()[:24]
        self.db_path = Path(db_path) if db_path else self.repo / f".semantic-search/{namespace}.sqlite3"
        self._index = index
        self._embedder = embedder
        try:
            parsed = urlsplit(h2o_url)
            if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
                raise ValueError
            parsed.port
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("h2o_url must be a loopback HTTP base URL without credentials") from error
        self._h2o = h2o_client or H2OClient(h2o_url)
        self._lock = threading.RLock()
        self._closed = False
        self._manifest = None
        self._vectors_manifest = None
        self._vectors_model_fingerprint = None
        self._snippets = []
        self._lexical = self._dense = self._hybrid = None
        self._last_stats = None
        self._last_error = None

    def _check_open(self):
        if self._closed:
            raise RuntimeError("Search backend is closed")

    def _get_embedder(self):
        if self._embedder is None:
            from semantic_search.embeddings import QwenEmbedder

            self._embedder = QwenEmbedder(progress=False)
        return self._embedder

    def _embedding_fingerprint(self):
        metadata = self._get_embedder().metadata
        if not isinstance(metadata, dict):
            raise ValueError("Embedding metadata must be a JSON object")
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _get_index(self):
        if self._index is None:
            from semantic_search.store import SQLiteIndex

            self._index = SQLiteIndex(self.db_path)
        return self._index

    def _snapshot(self):
        """Hash actual saved bytes and file membership, including the Git ref."""
        root = (self.repo / self.source_root).resolve()
        if not root.is_relative_to(self.repo) or not root.is_dir():
            raise SourceSnapshotError("Source directory is missing or outside repo")
        try:
            files = _source_files(self.repo, root)
            raw = {str(p.relative_to(self.repo)): p.read_bytes() for p in files}
        except (OSError, ValueError) as error:
            raise SourceSnapshotError(f"Source changed while reading: {error}") from error
        # Tracked membership is supplied by _source_files. Outside Git, all
        # Python files beneath the source root are considered.
        git = {}
        for field, args in (("revision", ["rev-parse", "HEAD"]),
                            ("branch", ["symbolic-ref", "--quiet", "HEAD"])):
            try:
                result = subprocess.run(["git", "-C", str(self.repo), *args],
                                        capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise SourceSnapshotError("Could not determine the source Git snapshot") from error
            git[field] = result.stdout.strip() if result.returncode == 0 else None
        manifest = {"source_root": self.source_root, "git": git,
                    "files": {path: hashlib.sha256(data).hexdigest() for path, data in raw.items()}}
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        return digest, raw

    def _confirm_snapshot(self, expected):
        if self._snapshot()[0] != expected:
            raise SourceSnapshotError("Source changed during the request; retry with stable saved files")

    @staticmethod
    def _validate_source(raw):
        decoded = {}
        for path, data in raw.items():
            try:
                encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
                source = data.decode(encoding)
                ast.parse(source, filename=path)
            except (SyntaxError, UnicodeError, LookupError, ValueError) as error:
                raise SourceSnapshotError(f"Cannot parse saved source {path}: {error}") from error
            decoded[path] = source
        return decoded

    def _refresh(self, require_vectors=True):
        started = time.perf_counter()
        digest, raw = self._snapshot()
        unchanged = digest == self._manifest
        fingerprint = self._embedding_fingerprint() if require_vectors else None
        if unchanged and (not require_vectors or (self._vectors_manifest == digest
                                                  and self._vectors_model_fingerprint == fingerprint)):
            stats = {"indexed_windows": 0, "reused_windows": 0, "deleted_windows": 0,
                     "total_windows": len(self._snippets), "memory_cache_hit": True,
                     "model_fingerprint": fingerprint,
                     "source_manifest": digest, "source_files": len(raw),
                     "elapsed_s": time.perf_counter() - started}
            self._last_stats, self._last_error = stats, None
            return stats
        if unchanged:
            snippets = self._snippets
        else:
            decoded = self._validate_source(raw)
            try:
                snippets = extract_snippets(self.repo, self.source_root, max_lines=160,
                                            overlap_lines=40, sources=decoded)
            except (OSError, SyntaxError, UnicodeError, ValueError) as error:
                raise SourceSnapshotError(f"Cannot extract saved source: {error}") from error
            # Assert every returned source window matches the captured bytes.
            for snippet in snippets:
                source = decoded.get(snippet.path)
                if source is None or snippet.text != "".join(
                        source.splitlines(keepends=True)[snippet.start_line - 1:snippet.end_line]):
                    raise SourceSnapshotError("Source changed during extraction; retry with stable saved files")
            self._confirm_snapshot(digest)
        stats = {"indexed_windows": 0, "reused_windows": 0, "deleted_windows": 0,
                 "total_windows": len(snippets)}
        dense = hybrid = None
        lexical = BM25Retriever(snippets)
        if require_vectors:
            vectors, stats = self._get_index().refresh(
                snippets, self._get_embedder(), before_commit=lambda: self._confirm_snapshot(digest))
            if snippets:
                dense = DenseRetriever(snippets, vectors)
                hybrid = HybridRetriever(lexical, dense, rrf_k=60, fetch_depth=50)
        self._confirm_snapshot(digest)
        self._manifest, self._snippets = digest, snippets
        self._vectors_manifest = digest if require_vectors else None
        self._vectors_model_fingerprint = fingerprint
        self._lexical, self._dense, self._hybrid = lexical, dense, hybrid
        stats = {**stats, "memory_cache_hit": False, "source_manifest": digest,
                 "source_files": len(raw), "elapsed_s": time.perf_counter() - started}
        self._last_stats, self._last_error = stats, None
        return stats

    def refresh(self):
        """Refresh persistent vectors from saved files without executing a query."""
        with self._lock:
            self._check_open()
            try:
                return self._refresh()
            except Exception as error:
                self._last_error = str(error)
                raise

    def status(self):
        with self._lock:
            return {"repo": str(self.repo), "source_root": self.source_root,
                    "closed": self._closed, "ready": self._manifest is not None and self._last_error is None,
                    "source_manifest": self._manifest, "windows": len(self._snippets),
                    "embedding_initialized": self._embedder is not None,
                    "dense_ready": self._dense is not None,
                    "last_error": self._last_error, "indexstats": self._last_stats,
                    "store": self._index.status() if self._index is not None and not self._closed else None}

    def search(self, query, retrieval="dense", rerank=False, candidates=4,
               max_results=5, source_budget=12000):
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
            raise ValueError(f"query must be nonempty and at most {MAX_QUERY_CHARS} characters")
        try:
            query.encode("utf-8")
        except UnicodeError as error:
            raise ValueError("query must contain valid Unicode text") from error
        if retrieval not in ("bm25", "dense", "hybrid"):
            raise ValueError("retrieval must be bm25, dense, or hybrid")
        if type(rerank) is not bool:
            raise ValueError("rerank must be a boolean")
        _integer(candidates, "candidates", 1, MAX_CANDIDATES)
        _integer(max_results, "max_results", 1, MAX_RESULTS)
        _integer(source_budget, "source_budget", 0, MAX_SOURCE_BUDGET)
        started = time.perf_counter()
        with self._lock:
            self._check_open()
            try:
                stats = self._refresh(require_vectors=retrieval != "bm25")
                phase = time.perf_counter()
                vector = None
                if self._snippets and retrieval != "bm25":
                    vector = self._get_embedder().embed_query(query)
                embedding_s = time.perf_counter() - phase if vector is not None else 0.0
                phase = time.perf_counter()
                limit = candidates if rerank else max(10, candidates, max_results)
                pairs = []
                if self._snippets:
                    if retrieval == "bm25":
                        pairs = self._lexical.search(query, limit)
                    elif retrieval == "dense":
                        pairs = self._dense.search(vector, limit)
                    else:
                        pairs = self._hybrid.search(query, vector, limit)
                retrieval_s = time.perf_counter() - phase
                phase = time.perf_counter()
                judgments = {s.id: self._h2o.score(query, s) for s, _ in pairs} if rerank else {}
                if rerank:
                    pairs.sort(key=lambda pair: (-judgments[pair[0].id].score, -pair[1], pair[0].id))
                h2o_s = time.perf_counter() - phase if rerank else 0.0
                phase = time.perf_counter()
                seen, selected = set(), []
                for snippet, score in pairs:
                    key = (snippet.path, snippet.symbol)
                    if key not in seen:
                        seen.add(key)
                        selected.append((snippet, score))
                eligible = selected[:max_results]
                remaining, results = source_budget, []
                for snippet, score in eligible:
                    source = snippet.text
                    if len(source) > remaining:
                        lines, used = [], 0
                        for line in source.splitlines(keepends=True):
                            if used + len(line) > remaining:
                                break
                            lines.append(line)
                            used += len(line)
                        source = "".join(lines)
                    if not source:
                        continue
                    remaining -= len(source)
                    truncated = source != snippet.text
                    results.append({"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
                                    "start_line": snippet.start_line,
                                    "end_line": snippet.start_line + len(source.splitlines()) - 1,
                                    "window_end_line": snippet.end_line,
                                    "source": source, "content_hash": snippet.content_hash,
                                    "source_hash": hashlib.sha256(source.encode()).hexdigest(),
                                    "retrieval_score": float(score),
                                    "h2o_score": judgments[snippet.id].score if rerank else None,
                                    "truncated": truncated})
                self._confirm_snapshot(self._manifest)
                packaging_s = time.perf_counter() - phase
                return {"repo": str(self.repo), "source_root": self.source_root,
                        "query": query, "retrieval": retrieval, "rerank": rerank,
                        "results": results, "candidate_windows": len(pairs),
                        "available_symbols": len(selected), "source_budget": source_budget,
                        "total_source_chars": source_budget - remaining,
                        "budget_exhausted": any(r["truncated"] for r in results) or len(results) < len(eligible),
                        "indexstats": stats,
                        "timings": {"index_s": stats["elapsed_s"], "query_embedding_s": embedding_s,
                                    "retrieval_s": retrieval_s, "h2o_s": h2o_s,
                                    "packaging_s": packaging_s, "total_s": time.perf_counter() - started}}
            except Exception as error:
                self._last_error = str(error)
                raise

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    if self._embedder is not None:
                        self._embedder.close()
                finally:
                    if self._index is not None:
                        self._index.close()
