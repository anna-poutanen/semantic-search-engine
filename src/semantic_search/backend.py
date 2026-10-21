"""Persistent, serialized local search over stable snapshots of saved source."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import json
import math
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from semantic_search.corpus import _source_files, extract_snippets
from semantic_search.model import H2OClient
from semantic_search.retrieval import HybridRetriever, MultifileRetriever
from semantic_search.cancellation import CancellationRegistry, SearchCancelled
from semantic_search.context import apply_token_budget, indexed_scope
from semantic_search.source import physical_lines
from semantic_search.selection import select_context, postprocess_context
from semantic_search.languages import decode_source, extractor_fingerprint, validate_exclusions, validate_source


class CancellableEmbedder:
    def __init__(self, embedder, check):
        self.embedder, self.check = embedder, check

    @property
    def metadata(self):
        return self.embedder.metadata

    def embed_documents(self, texts, *, progress_callback=None, batch_callback=None):
        self.check()
        parameters = inspect.signature(self.embedder.embed_documents).parameters
        extra = {}
        if "cancel_check" in parameters:
            extra["cancel_check"] = self.check
        if progress_callback is not None and "progress_callback" in parameters:
            extra["progress_callback"] = progress_callback
        if batch_callback is not None and "batch_callback" in parameters:
            def checked_batch(indices, vectors):
                self.check()
                batch_callback(indices, vectors)

            extra["batch_callback"] = checked_batch
        result = self.embedder.embed_documents(texts, **extra)
        self.check()
        return result

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
                 embedder=None, h2o_url="http://127.0.0.1:8741", h2o_client=None,
                 abstention_policy=None, source_exclusions=(), qwen_client=None, progress=None):
        self.repo = Path(repo).resolve()
        if not self.repo.is_dir():
            raise ValueError("repo must be an existing directory")
        if not isinstance(source_root, str) or not source_root.strip():
            raise ValueError("source_root must be a nonempty path")
        root = (self.repo / source_root).resolve()
        if not root.is_relative_to(self.repo):
            raise ValueError("source_root must be inside repo")
        self.source_root = str(root.relative_to(self.repo))
        self.source_exclusions = validate_exclusions(source_exclusions)
        identity = f"{self.repo}\n{self.source_root}"
        if self.source_exclusions:
            identity += "\n" + json.dumps(self.source_exclusions)
        namespace = hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.db_path = Path(db_path) if db_path else self.repo / f".semantic-search/{namespace}.sqlite3"
        self._index = index
        self._embedder = embedder
        if progress is not None and not callable(progress):
            raise ValueError("progress must be a callback")
        self._progress = progress
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
        self._qwen = qwen_client
        self._lock = threading.RLock()
        self._requests = CancellationRegistry()
        self._closed = False
        self._manifest = None
        self._vectors_manifest = None
        self._vectors_model_fingerprint = None
        self._snippets = []
        self._lexical = self._dense = self._hybrid = self._multifile = None
        self._last_stats = None
        self._last_error = None
        self._references = None
        self._definition_keys = {}
        self._watcher = None
        self._watch_request_id = f"watch-{id(self)}"
        from semantic_search.matching import MatchPolicy
        self._policy = MatchPolicy(abstention_policy) if abstention_policy is not None else None

    def cancel(self, request_id):
        return self._requests.cancel(request_id)

    @contextmanager
    def _request_lock(self, request_id):
        with self._requests.request(request_id) as check:
            while not self._lock.acquire(timeout=.05):
                check()
            try:
                check()
                yield check
            finally:
                self._lock.release()

    def _check_open(self):
        if self._closed:
            raise RuntimeError("Search backend is closed")
        if self._qwen is not None and getattr(self._qwen, "safe_for_embedding", True) is False:
            raise RuntimeError("Previous Qwen failure left worker shutdown unproved; restart the backend")

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

    def _snapshot(self, *, report_progress=False):
        """Hash actual saved bytes and file membership, including the Git ref."""
        root = (self.repo / self.source_root).resolve()
        if not root.is_relative_to(self.repo) or not root.is_dir():
            raise SourceSnapshotError("Source directory is missing or outside repo")
        try:
            if report_progress:
                self._report_progress({"phase": "scan", "completed": 0, "unit": "files"})
            files = _source_files(self.repo, root, self.source_exclusions)
            raw = {}
            for completed, path in enumerate(files, 1):
                raw[str(path.relative_to(self.repo))] = path.read_bytes()
                if report_progress and (completed % 32 == 0 or completed == len(files)):
                    self._report_progress({"phase": "scan", "completed": completed,
                                           "total": len(files), "unit": "files"})
            if report_progress and not files:
                self._report_progress({"phase": "scan", "completed": 0,
                                       "total": 0, "unit": "files"})
        except (OSError, ValueError) as error:
            raise SourceSnapshotError(f"Source changed while reading: {error}") from error
        # Tracked membership is supplied by _source_files. Outside Git, all
        # Eligible supported files beneath the source root are considered.
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
                    "source_exclusions": self.source_exclusions, "extractor": extractor_fingerprint(),
                    "files": {path: hashlib.sha256(data).hexdigest() for path, data in raw.items()}}
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        return digest, raw

    def _report_progress(self, event):
        """Opt-in counts for a local launcher; never include source paths or text."""
        if self._progress is not None:
            self._progress(event)

    def _confirm_snapshot(self, expected):
        if self._snapshot()[0] != expected:
            raise SourceSnapshotError("Source changed during the request; retry with stable saved files")

    @staticmethod
    def _validate_source(raw):
        decoded = {}
        for path, data in raw.items():
            try:
                source = decode_source(path, data)
                validate_source(path, source)
            except (SyntaxError, UnicodeError, LookupError, ValueError, ImportError) as error:
                raise SourceSnapshotError(f"Cannot parse saved source {path}: {error}") from error
            decoded[path] = source
        return decoded

    def _refresh(self, require_vectors=True, cancel_check=lambda: None):
        started = time.perf_counter()
        cancel_check()
        digest, raw = (self._snapshot(report_progress=True) if self._progress is not None
                       else self._snapshot())
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
            cancel_check()
            self._report_progress({"phase": "indexed", "completed": len(self._snippets),
                                   "total": len(self._snippets), "unit": "windows",
                                   "memory_cache_hit": True})
            return stats
        if unchanged:
            snippets = self._snippets
            references = self._references
            definition_keys = self._definition_keys
        else:
            self._report_progress({"phase": "extract", "completed": 0,
                                   "total": len(raw), "unit": "files"})
            decoded = self._validate_source(raw)
            definition_keys = {}
            try:
                snippets = extract_snippets(self.repo, self.source_root, max_lines=160,
                                            overlap_lines=40, sources=decoded,
                                            definition_keys=definition_keys, source_exclusions=self.source_exclusions)
            except (OSError, SyntaxError, UnicodeError, ValueError, ImportError) as error:
                raise SourceSnapshotError(f"Cannot extract saved source: {error}") from error
            # Assert every returned source window matches the captured bytes.
            for snippet in snippets:
                source = decoded.get(snippet.path)
                if source is None or snippet.text != "".join(
                        physical_lines(source)[snippet.start_line - 1:snippet.end_line]):
                    raise SourceSnapshotError("Source changed during extraction; retry with stable saved files")
            self._confirm_snapshot(digest)
            from semantic_search.relations import ReferenceGraph
            references = ReferenceGraph(decoded, snippets)
            self._report_progress({"phase": "extract", "completed": len(raw),
                                   "total": len(raw), "unit": "files",
                                   "windows": len(snippets)})
        stats = {"indexed_windows": 0, "reused_windows": 0, "deleted_windows": 0,
                 "total_windows": len(snippets)}
        dense = hybrid = multifile = None
        from semantic_search.persistent_retrieval import PersistentDenseRetriever, PersistentLexicalRetriever
        index = self._get_index()
        def before_commit():
            cancel_check()
            self._confirm_snapshot(digest)
            cancel_check()
        if require_vectors:
            extra = {}
            if (self._progress is not None
                    and "progress" in inspect.signature(index.refresh).parameters):
                extra["progress"] = self._report_progress
            vectors, stats = index.refresh(snippets, CancellableEmbedder(self._get_embedder(), cancel_check),
                                           before_commit=before_commit, **extra)
            if snippets:
                dense = PersistentDenseRetriever(index)
        else:
            stats = index.refresh_lexical(snippets, before_commit=before_commit)
        lexical = PersistentLexicalRetriever(index)
        if dense is not None:
            hybrid = HybridRetriever(lexical, dense, rrf_k=60, fetch_depth=50)
            multifile = MultifileRetriever(lexical, dense)
        cancel_check()
        self._confirm_snapshot(digest)
        self._manifest, self._snippets = digest, snippets
        self._references = references
        self._definition_keys = definition_keys
        self._vectors_manifest = digest if require_vectors else None
        self._vectors_model_fingerprint = fingerprint
        self._lexical, self._dense, self._hybrid, self._multifile = lexical, dense, hybrid, multifile
        stats = {**stats, "memory_cache_hit": False, "source_manifest": digest,
                 "source_files": len(raw), "elapsed_s": time.perf_counter() - started}
        self._last_stats, self._last_error = stats, None
        self._report_progress({"phase": "indexed", "completed": len(snippets),
                               "total": len(snippets), "unit": "windows",
                               "memory_cache_hit": False,
                               "reused_windows": stats["reused_windows"],
                               "indexed_windows": stats["indexed_windows"]})
        return stats

    def refresh(self, request_id=None):
        """Refresh persistent vectors from saved files without executing a query."""
        with self._request_lock(request_id) as check:
            self._check_open()
            try:
                return self._refresh(cancel_check=check)
            except Exception as error:
                if not isinstance(error, SearchCancelled):
                    self._last_error = str(error)
                raise

    def start_watching(self, poll_interval=1.0, debounce=.5):
        from semantic_search.watcher import PollingWatcher
        with self._lock:
            self._check_open()
            if self._watcher is None:
                self._watcher = PollingWatcher(lambda: self._snapshot()[0],
                    lambda: self.refresh(request_id=self._watch_request_id),
                    poll_interval=poll_interval, debounce=debounce,
                    cancel=lambda: self.cancel(self._watch_request_id))
            self._watcher.start(initial_snapshot=self._manifest)
            return self._watcher.status()

    def status(self):
        with self._lock:
            return {"repo": str(self.repo), "source_root": self.source_root,
                    "source_exclusions": list(self.source_exclusions),
                    "extractor": extractor_fingerprint(),
                    "closed": self._closed, "ready": self._manifest is not None and self._last_error is None,
                    "source_manifest": self._manifest, "windows": len(self._snippets),
                    "embedding_initialized": self._embedder is not None,
                    "dense_ready": self._dense is not None,
                    "last_error": self._last_error, "indexstats": self._last_stats,
                    "watcher": self._watcher.status() if self._watcher else None,
                    "abstention_policy": self._policy.record if self._policy else None,
                    "store": self._index.status() if self._index is not None and not self._closed else None}

    def _source_provenance(self, path):
        return {}

    def _search_scope(self):
        return indexed_scope(self.source_root, self.source_exclusions)

    def search(self, query, retrieval="dense", rerank=False, candidates=4,
               max_results=5, source_budget=12000, token_budget=None,
               include_dependencies=False, request_id=None, overlays=None, rerank_provider="h2o"):
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
            raise ValueError(f"query must be nonempty and at most {MAX_QUERY_CHARS} characters")
        try:
            query.encode("utf-8")
        except UnicodeError as error:
            raise ValueError("query must contain valid Unicode text") from error
        if retrieval not in ("bm25", "dense", "hybrid", "multifile"):
            raise ValueError("retrieval must be bm25, dense, hybrid, or multifile")
        if type(rerank) is not bool:
            raise ValueError("rerank must be a boolean")
        if rerank_provider not in ("h2o", "qwen"):
            raise ValueError("rerank_provider must be h2o or qwen")
        if rerank and rerank_provider == "qwen" and self._qwen is None:
            raise ValueError("Qwen reranking is not configured; start an explicitly owned local worker")
        _integer(candidates, "candidates", 1, MAX_CANDIDATES)
        _integer(max_results, "max_results", 1, MAX_RESULTS)
        _integer(source_budget, "source_budget", 0, MAX_SOURCE_BUDGET)
        if token_budget is not None:
            _integer(token_budget, "token_budget", 512, 32000)
        if type(include_dependencies) is not bool:
            raise ValueError("include_dependencies must be a boolean")
        from semantic_search.overlays import normalize_overlays
        entries = normalize_overlays(overlays)
        started = time.perf_counter()
        with self._request_lock(request_id) as check:
            self._check_open()
            if entries:
                from semantic_search.overlays import search_overlay
                settings = {"query": query, "retrieval": retrieval, "rerank": rerank,
                            "rerank_provider": rerank_provider, "candidates": candidates, "max_results": max_results,
                            "source_budget": source_budget, "token_budget": token_budget,
                            "include_dependencies": include_dependencies}
                response = search_overlay(self, entries, settings, check)
                response["timings"]["total_s"] = time.perf_counter() - started
                return response
            try:
                stats = self._refresh(require_vectors=retrieval != "bm25", cancel_check=check)
                check()
                phase = time.perf_counter()
                vector = None
                if self._snippets and retrieval != "bm25":
                    vector = self._get_embedder().embed_query(query)
                check()
                embedding_s = time.perf_counter() - phase if vector is not None else 0.0
                phase = time.perf_counter()
                qwen_rerank = rerank and rerank_provider == "qwen"
                limit = 50 if qwen_rerank else candidates if rerank else max(50, candidates, max_results)
                pairs = []
                if self._snippets:
                    if retrieval == "bm25":
                        pairs = self._lexical.search(query, limit)
                    elif retrieval == "dense":
                        pairs = self._dense.search(vector, limit)
                    elif retrieval == "hybrid":
                        pairs = self._hybrid.search(query, vector, limit)
                    else:
                        pairs = self._multifile.search(query, vector, limit)
                full_candidate_count = len(pairs)
                match_status = "candidates" if pairs else "no_candidates"
                top_cosine = (pairs[0][1] if retrieval == "dense" and pairs else
                              self._dense.search(vector, limit=1)[0][1] if vector is not None else None)
                if (self._policy is not None and top_cosine is not None
                        and self._policy.rejects(top_cosine, self._get_embedder().metadata)):
                    match_status, pairs = "insufficient_evidence", []
                retrieval_s = time.perf_counter() - phase
                phase = time.perf_counter()
                judgments, qwen_judgments = {}, {}
                if qwen_rerank:
                    pairs = pairs[:candidates]
                    scoring_request_id = request_id or uuid.uuid4().hex
                    for index, (snippet, _) in enumerate(pairs):
                        check()
                        judgment = self._qwen.score(query, snippet,
                            request_id=f"{scoring_request_id}-{index}")
                        if (type(judgment.score) not in (int, float)
                                or not math.isfinite(judgment.score)):
                            raise ValueError("Qwen reranker returned a nonfinite ranking scalar")
                        qwen_judgments[snippet.id] = judgment
                        check()
                    # Preserve the original prefix index on ties, as in the audited study.
                    order = sorted(range(len(pairs)), key=lambda i: (
                        -qwen_judgments[pairs[i][0].id].score, i, pairs[i][0].id))
                    pairs = [pairs[index] for index in order]
                elif rerank:
                    for snippet, _ in pairs:
                        check()
                        judgments[snippet.id] = self._h2o.score(query, snippet)
                        check()
                    pairs.sort(key=lambda pair: (-judgments[pair[0].id].score, -pair[1], pair[0].id))
                reranker_s = time.perf_counter() - phase if rerank else 0.0
                h2o_s = reranker_s if rerank and not qwen_rerank else 0.0
                qwen_s = reranker_s if qwen_rerank else 0.0
                phase = time.perf_counter()
                selected, eligible, origins = select_context(
                    pairs, query=query, max_results=max_results,
                    include_dependencies=include_dependencies, references=self._references,
                    definition_keys=self._definition_keys)
                def result_row(snippet, score, source, origin="retrieval", related_to=None):
                    truncated = source != snippet.text
                    return {"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
                                    "start_line": snippet.start_line,
                                    "end_line": snippet.start_line + len(physical_lines(source)) - 1,
                                    "window_end_line": snippet.end_line,
                                    "source": source, "content_hash": snippet.content_hash,
                                    "source_hash": hashlib.sha256(source.encode()).hexdigest(),
                                    "retrieval_score": float(score),
                                    "h2o_score": judgments[snippet.id].score if snippet.id in judgments else None,
                                    "qwen_score": qwen_judgments[snippet.id].score if snippet.id in qwen_judgments else None,
                                    "origin": origin,
                                    "related_to": related_to,
                                    **self._source_provenance(snippet.path),
                                    "truncated": truncated}

                remaining, results = source_budget, []
                for snippet, score in eligible:
                    source = snippet.text
                    if len(source) > remaining:
                        lines, used = [], 0
                        for line in physical_lines(source):
                            if used + len(line) > remaining:
                                break
                            lines.append(line)
                            used += len(line)
                        source = "".join(lines)
                    if not source:
                        continue
                    remaining -= len(source)
                    results.append(result_row(snippet, score, source,
                        *origins.get(snippet.id, ("retrieval", None))))
                self._confirm_snapshot(self._manifest)
                packaging_s = time.perf_counter() - phase
                response = {"repo": str(self.repo), "source_root": self.source_root,
                        "scope": self._search_scope(),
                        "query": query, "retrieval": retrieval, "rerank": rerank,
                        "rerank_provider": rerank_provider, "reranker_batch_size": 1 if qwen_rerank else None,
                        "retrieval_candidate_windows": full_candidate_count, "results": results, "candidate_windows": len(pairs),
                        "available_symbols": len(selected), "source_budget": source_budget,
                        "total_source_chars": source_budget - remaining,
                        "budget_exhausted": any(r["truncated"] for r in results) or len(results) < len(eligible),
                        "indexstats": stats,
                        "timings": {"index_s": stats["elapsed_s"], "query_embedding_s": embedding_s,
                                    "retrieval_s": retrieval_s, "h2o_s": h2o_s, "qwen_s": qwen_s,
                                    "packaging_s": packaging_s, "total_s": time.perf_counter() - started}}
                if qwen_rerank:
                    response["reranker_receipts"] = {identifier: judgment.response
                        for identifier, judgment in qwen_judgments.items()}
                response["match_status"] = match_status
                response["match_evidence"] = {"top_cosine": top_cosine,
                    "threshold": self._policy.threshold if self._policy else None,
                    "meaning": "Candidate relevance is not proof that a behavior exists or is absent."}
                if retrieval == "multifile":
                    response["match_evidence"]["retrieval_score_kind"] = "multifile_rank_priority"
                if qwen_rerank:
                    response["match_evidence"]["ranking_score_kind"] = "qwen_yes_minus_no_logit_difference"
                if token_budget is not None:
                    response = apply_token_budget(response, token_budget)
                response = postprocess_context(response,
                    (result_row(snippet, score, snippet.text) for snippet, score in selected),
                    max_results=max_results, source_budget=source_budget,
                    token_budget=token_budget, cancel_check=check)
                check()
                self._confirm_snapshot(self._manifest)
                response["timings"]["packaging_s"] = time.perf_counter() - phase
                response["timings"]["total_s"] = time.perf_counter() - started
                return response
            except Exception as error:
                if not isinstance(error, SearchCancelled):
                    self._last_error = str(error)
                raise

    def close(self):
        self._requests.close()
        if self._watcher is not None:
            self._watcher.stop()
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    if self._embedder is not None:
                        self._embedder.close()
                finally:
                    if self._index is not None:
                        self._index.close()
