"""Evidence helpers for the prospective Django profile; imports never load models."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()


def read(path):
    return json.loads(Path(path).read_text())


def artifact(path):
    path = Path(path).absolute()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def resolve(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def public(value):
    if isinstance(value, str):
        return value.replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items()}
    return value


def jsonsafe(value):
    if isinstance(value, np.ndarray):
        return {"array_shape": list(value.shape), "array_dtype": str(value.dtype),
                "array_raw_sha256": digest(value.tobytes())}
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": digest(value)}
    if is_dataclass(value):
        return jsonsafe(asdict(value))
    if isinstance(value, dict):
        return {str(key): jsonsafe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonsafe(item) for item in value]
    if isinstance(value, Path):
        return artifact(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"object_type": type(value).__name__}


def stamp():
    return {"utc": datetime.now(timezone.utc).isoformat(), "wall_time_ns": time.time_ns(),
            "perf_counter_ns": time.perf_counter_ns()}


def write_new(path, value):
    with Path(path).open("xb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())


def write_atomic(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Journal:
    def __init__(self, path):
        self.stream = Path(path).open("x")
        self.sequence = 0

    def emit(self, event, **fields):
        self.sequence += 1
        row = {"event": event, "sequence": self.sequence, "observed": stamp(), **fields}
        self.stream.write(json.dumps(public(row), sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
        self.stream.flush()
        return row

    def close(self):
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()


class ProfileInterrupt(KeyboardInterrupt):
    pass


class Signals:
    """Capture SIGINT/TERM once, with deferral around process birth and cleanup."""
    def __init__(self):
        self.depth = 0
        self.pending = None
        self.previous = {}
        self.receipt = {"received": []}

    def handler(self, signum, frame):
        self.receipt["received"].append({"signal": signum, "observed": stamp()})
        if self.pending is None:
            self.pending = signum
        if self.depth == 0:
            for value in self.previous:
                signal.signal(value, signal.SIG_IGN)
            raise ProfileInterrupt(f"Profile interrupted by signal{signum}")

    def __enter__(self):
        require(threading.current_thread() is threading.main_thread(), "Signals require the main thread")
        for number in (signal.SIGINT, signal.SIGTERM):
            self.previous[number] = signal.getsignal(number)
            signal.signal(number, self.handler)
        return self

    def __exit__(self, *args):
        for number, previous in self.previous.items():
            signal.signal(number, previous)
        self.receipt["restored"] = True

    @contextmanager
    def defer(self, *, raise_pending=True):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1
            if self.depth == 0 and self.pending is not None and raise_pending:
                for number in self.previous:
                    signal.signal(number, signal.SIG_IGN)
                raise ProfileInterrupt(f"Deferred profile signal{self.pending}")


def block_network():
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden during the local index profile")
    socket.create_connection = forbidden
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.socket.sendto = forbidden
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(ROOT / ".cache/tokenizers")


class BlockModelImports:
    def __init__(self, strict=False):
        self.strict = strict
        self.blocked = []

    def find_spec(self, fullname, path=None, target=None):
        prefixes = ("mlx", "mlx_embeddings", "mlx_vlm", "mlx_lm", "transformers")
        if self.strict:
            prefixes += ("semantic_search.embeddings", "semantic_search.model", "semantic_search.qwen")
        if any(fullname == name or fullname.startswith(name + ".") or
               (name == "semantic_search.qwen" and fullname.startswith(name)) for name in prefixes):
            self.blocked.append(fullname)
            raise AssertionError("Model import forbidden: " + fullname)
        return None


def packages():
    """Bind every physical metadata directory, including editable egg-info."""
    rows, origins = {}, {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", name),
                "Invalid installed distribution name")
        canonical = re.sub(r"[-_.]+", "-", name).lower()
        version = distribution.version
        require(isinstance(version, str) and bool(version), "Missing installed distribution version")
        path = getattr(distribution, "_path", None)
        require(isinstance(path, Path), "Unsupported installed metadata origin: " + name)
        origin = path.resolve(strict=True)
        require(origin.is_dir(), "Installed metadata origin is not a directory: " + name)
        metadata_files = [filename for filename in ("METADATA", "PKG-INFO") if (origin / filename).is_file()]
        require(len(metadata_files) == 1, "Ambiguous installed metadata file: " + name)
        direct_path = origin / "direct_url.json"
        direct = read(direct_path) if direct_path.is_file() else None
        if canonical == "semantic-search-engine":
            if metadata_files[0] == "PKG-INFO":
                require(origin == (ROOT / "src/semantic_search_engine.egg-info").resolve(),
                        "Project egg-info origin differs from frozen project")
            if direct is not None:
                require(isinstance(direct, dict) and set(direct) == {"url", "dir_info"}
                        and direct["dir_info"] == {"editable": True} and isinstance(direct["url"], str),
                        "Project editable metadata is malformed")
                parsed = urlsplit(direct["url"])
                require(parsed.scheme == "file" and parsed.path.startswith("/") and not parsed.netloc and not parsed.query and not parsed.fragment
                        and Path(unquote(parsed.path)).resolve() == ROOT.resolve(),
                        "Project editable origin differs from frozen project")
        instance = {"metadata_origin": artifact(origin), "declared_name": name, "version": version,
                    "metadata_file": metadata_files[0], "metadata_sha256": sha(origin / metadata_files[0]),
                    "record_sha256": sha(origin / "RECORD") if (origin / "RECORD").is_file() else None,
                    "direct_url_sha256": sha(direct_path) if direct_path.is_file() else None,
                    "direct_url": direct}
        if canonical in rows:
            require(rows[canonical]["declared_name"] == name, "Ambiguous canonical distribution name: " + canonical)
            require(rows[canonical]["version"] == version, "Conflicting installed distribution versions: " + canonical)
        else:
            rows[canonical] = {"declared_name": name, "version": version, "instances": []}
        key = str(origin)
        if key in origins:
            require(origins[key] == (canonical, instance), "Conflicting physical metadata alias: " + key)
            continue
        origins[key] = (canonical, instance)
        rows[canonical]["instances"].append(instance)
    if "semantic-search-engine" in rows:
        require(any(instance["direct_url"] is not None for instance in rows["semantic-search-engine"]["instances"]),
                "Project editable installation provenance is absent")
    for row in rows.values():
        row["instances"].sort(key=lambda instance: instance["metadata_origin"])
    return {"schema_version": 2, "logical_distribution_count": len(rows),
            "physical_metadata_instance_count": len(origins), "distributions": dict(sorted(rows.items()))}


def native_sources():
    """Hash the embedding/tokenizer/native implementation closure without imports."""
    rows = {}
    for name in ("mlx", "mlx-metal", "mlx-embeddings", "mlx-vlm", "mlx-lm", "transformers", "tokenizers", "numpy", "sqlite-vec", "tiktoken"):
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            # The embedding environment has mlx-vlm; mlx-lm is optional and
            # absent here. Package closure records its absence. If it is loaded
            # from an unregistered path, the actual-origin check refuses it.
            if name == "mlx-lm":
                continue
            raise
        for file in distribution.files or ():
            value = str(file)
            if value.endswith((".py", ".so", ".dylib", ".metallib")) and "__pycache__" not in value:
                path = Path(distribution.locate_file(file)).resolve()
                require(path.is_file(), "Installed native source absent: " + str(path))
                rows[str(path)] = sha(path)
    return rows


def runtime():
    return {"python": sys.version, "executable": artifact(sys.executable),
            "executable_sha256": sha(sys.executable), "prefix": artifact(sys.prefix),
            "base_prefix": artifact(sys.base_prefix), "platform": platform.platform(),
            "machine": platform.machine(), "packages": packages(),
            "sys_path": [artifact(value) if value else "" for value in sys.path],
            "working_directory": artifact(Path.cwd())}


def loaded_native_origins():
    prefixes = ("mlx", "mlx_embeddings", "mlx_vlm", "mlx_lm", "transformers", "tokenizers", "numpy", "sqlite_vec", "tiktoken")
    rows = {}
    for name, module in list(sys.modules.items()):
        if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
            filename = getattr(module, "__file__", None)
            if filename is not None:
                path = Path(filename).resolve()
                if path.suffix == ".pyc":
                    import importlib.util
                    path = Path(importlib.util.source_from_cache(str(path)))
                require(path.is_file(), "Loaded native origin absent: " + str(path))
                rows[name] = {"path": str(path), "sha256": sha(path)}
    return rows


def schedule(queries, providers, warmups=3, sweeps=10):
    require(queries and len(set(queries)) == len(queries), "Unique nonempty queries required")
    require(providers and len(set(providers)) == len(providers), "Unique nonempty providers required")
    require(type(warmups) is type(sweeps) is int and warmups >= 0 and sweeps > 0, "Strict schedule counts required")
    rows = [{"phase": "first", "sweep": -1, "query_id": queries[0], "provider": provider}
            for provider in providers]
    for phase, count in (("warmup", warmups), ("measured", sweeps)):
        for sweep in range(count):
            query_order = queries[sweep % len(queries):] + queries[:sweep % len(queries)]
            for position, query_id in enumerate(query_order):
                rotation = (sweep + position) % len(providers)
                for provider in providers[rotation:] + providers[:rotation]:
                    rows.append({"phase": phase, "sweep": sweep, "query_id": query_id, "provider": provider})
    return rows


def capture_source(repo, source_root, exclusions):
    from semantic_search.corpus import _source_files, extract_snippets
    from semantic_search.languages import decode_source
    from semantic_search.source import physical_lines
    repo = Path(repo)
    paths = _source_files(repo, repo / source_root, exclusions)
    raw = {path.relative_to(repo).as_posix(): path.read_bytes() for path in paths}
    decoded = {path: decode_source(path, data) for path, data in raw.items()}
    snippets = extract_snippets(repo, source_root, max_lines=160, overlap_lines=40,
                                sources=decoded, source_exclusions=exclusions)
    for snippet in snippets:
        require(snippet.text == "".join(physical_lines(decoded[snippet.path])[snippet.start_line - 1:snippet.end_line]),
                "Saved source span differs from extraction")
    return raw, snippets


def document(snippet):
    return f"Path: {snippet.path}\nSymbol: {snippet.symbol}\nSource:\n{snippet.text}"


def inventories(snippets):
    return {snippet.id: {"document_sha256": digest(document(snippet).encode()),
                         "content_sha256": snippet.content_hash, "path": snippet.path,
                         "symbol": snippet.symbol, "start_line": snippet.start_line,
                         "end_line": snippet.end_line} for snippet in snippets}


def expected_snapshots(manifest, window_rows, edited_source):
    """Derive each active document set from frozen source identities, never outputs."""
    initial = {row["id"]: row["document_sha256"] for row in window_rows}
    mutation = manifest["mutation"]
    require(len(initial) == manifest["source_window_count"], "Frozen window inventory count differs")
    edited = dict(initial)
    edited[mutation["edited_window_id"]] = mutation["edited_document_sha256"]
    deleted = {key: value for key, value in edited.items() if not key.startswith(mutation["path"] + ":")}
    renamed = dict(deleted)
    from semantic_search.source import physical_lines
    lines = physical_lines(edited_source)
    for row in window_rows:
        if row["path"] == mutation["path"]:
            text = "".join(lines[row["start_line"] - 1:row["end_line"]])
            key = f"{mutation['renamed_path']}:{row['symbol']}:{row['start_line']}-{row['end_line']}"
            renamed[key] = digest(f"Path: {mutation['renamed_path']}\nSymbol: {row['symbol']}\nSource:\n{text}".encode())
    return [initial, initial, edited, deleted, edited, renamed, edited, initial, initial]


def expected_source_snapshots(manifest, file_rows):
    initial = {row["path"]: row["sha256"] for row in file_rows}
    spec = manifest["mutation"]
    require(initial.get(spec["path"]) == spec["original_file_sha256"], "Frozen mutation source differs")
    edited = {**initial, spec["path"]: spec["edited_file_sha256"]}
    deleted = {path: value for path, value in edited.items() if path != spec["path"]}
    renamed = {**deleted, spec["renamed_path"]: spec["edited_file_sha256"]}
    return [initial, initial, edited, deleted, edited, renamed, edited, initial, initial]


def verify_stage(expected, stats, actual_documents, expected_documents, cached_count):
    require(actual_documents == expected_documents, "Stage source/document identity drift")
    for key in ("total_windows", "indexed_windows", "reused_windows", "deleted_windows"):
        require(type(stats.get(key)) is int and stats[key] == expected[key], "Stage refresh counter differs: " + key)
    require(type(cached_count) is int and cached_count == expected["cached_documents_after"], "Cached document count differs")


def inspect_database(index, snippets, known_vectors, metadata):
    expected = inventories(snippets)
    fingerprint = digest(json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    with sqlite3.connect(index.db_path) as connection:
        source_rows = connection.execute("SELECT id,path,symbol,start_line,end_line,text,content_hash,document_hash,model_fingerprint FROM snippets ORDER BY position").fetchall()
        active = connection.execute("SELECT snippet_id,model_fingerprint,dimensions,vector FROM active_vectors").fetchall()
        cached = connection.execute("SELECT model_fingerprint,document_hash,dimensions,vector FROM embeddings").fetchall()
        fts = connection.execute("SELECT id,terms FROM snippets_fts").fetchall()
    require(len(source_rows) == len(expected) == len(active) == len(fts), "Active source/FTS/dense count mismatch")
    require({row[0] for row in active} == {row[0] for row in fts} == set(expected), "Stale/missing active IDs")
    by_id = {snippet.id: snippet for snippet in snippets}
    import re
    for identifier, actual_terms in fts:
        snippet = by_id[identifier]
        text = f"{snippet.path} {snippet.symbol}\n{snippet.text}"
        text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        require(actual_terms == " ".join(re.findall(r"[a-z0-9]+", text.lower())), "Stored FTS terms differ")
    for row in source_rows:
        snippet = by_id[row[0]]
        require(tuple(row[:7]) == (snippet.id, snippet.path, snippet.symbol, snippet.start_line, snippet.end_line, snippet.text, snippet.content_hash), "Stored source bytes differ")
        require(row[7] == expected[snippet.id]["document_sha256"] and row[8] == fingerprint, "Stored document/model binding differs")
    require({row[1] for row in cached} == set(known_vectors) and len(cached) == len(known_vectors), "Historical cache inventory differs")
    for model, document_hash, dimensions, blob in cached:
        reference = known_vectors[document_hash]
        require(model == fingerprint and dimensions == metadata["dimensions"], "Cached vector model/dimension drift")
        value = np.frombuffer(blob, dtype="<f8")
        require(np.array_equal(value, reference.astype(np.float64)), "Cached vector bytes differ from genuine model output")
    for identifier, model, dimensions, blob in active:
        reference = known_vectors[expected[identifier]["document_sha256"]].astype(np.float64)
        scaled = reference / np.max(np.abs(reference))
        normalized = (scaled / np.linalg.norm(scaled)).astype("<f4")
        require(model == fingerprint and dimensions == metadata["dimensions"] and blob == normalized.tobytes(), "Active normalized float32 vector differs")
    return {"cached_documents": len(cached), "active_source_windows": len(source_rows),
            "active_fts_windows": len(fts), "active_dense_windows": len(active),
            "model_fingerprint": fingerprint, "all_source_and_cached_and_active_vectors_exact": True}


def verify_numeric(index, snippets, raw_matrix, query_vectors, top_k=10, tolerance=3e-6):
    """Independent float32 representation and float64 cosine, no producer math helpers."""
    raw = np.asarray(raw_matrix, dtype=np.float64)
    require(raw.shape == (len(snippets), len(query_vectors[0])), "Numerical matrix dimensions differ")
    require(np.isfinite(raw).all() and np.all(np.any(raw != 0, axis=1)), "Invalid numerical reference vectors")
    scaled = raw / np.max(np.abs(raw), axis=1, keepdims=True)
    stored = (scaled / np.linalg.norm(scaled, axis=1, keepdims=True)).astype(np.float32).astype(np.float64)
    normalized = stored / np.linalg.norm(stored, axis=1, keepdims=True)
    ids = np.asarray([snippet.id for snippet in snippets])
    rows = []
    for position, vector in enumerate(query_vectors):
        query = np.asarray(vector, dtype=np.float64)
        require(query.ndim == 1 and np.isfinite(query).all() and np.any(query != 0), "Invalid numerical query vector")
        query = query / np.max(np.abs(query))
        query = (query / np.linalg.norm(query)).astype(np.float32).astype(np.float64)
        query /= np.linalg.norm(query)
        scores = normalized @ query
        ranked = np.lexsort((ids, -scores))[:top_k]
        expected = ids[ranked].tolist()
        actual = index.search_dense(vector, top_k)
        actual_ids = [snippet.id for snippet, _ in actual]
        errors = [abs(score - scores[np.where(ids == snippet.id)[0][0]]) for snippet, score in actual]
        row = {"query_position": position, "actual_ids": actual_ids, "expected_numpy_ids": expected,
               "exact_order": actual_ids == expected, "max_score_error": max(errors, default=math.inf),
               "cutoff_gap": float(scores[ranked[-1]] - np.sort(scores)[-top_k - 1]) if len(scores) > top_k else None}
        rows.append(row)
    return {"checks_passed": all(row["exact_order"] and row["max_score_error"] <= tolerance for row in rows),
            "tolerance": tolerance, "near_tie_mismatches_excused": False, "queries": rows}


def verify_response(response, repo, query, config, encoding):
    from semantic_search.source import physical_lines
    require(response["query"] == query and response["rerank"] is False, "Response settings differ")
    require(len(response["results"]) <= config["quality_max_results"], "Result cap exceeded")
    characters = 0
    for row in response["results"]:
        path = Path(repo) / row["path"]
        require(path.resolve().is_relative_to(Path(repo).resolve()), "Source path escaped execution corpus")
        source = "".join(physical_lines(path.read_text())[row["start_line"] - 1:row["end_line"]])
        require(source == row["source"] and digest(source.encode()) == row["source_hash"], "Delivered source/location/hash drift")
        characters += len(source)
    from semantic_search.context import compact_result
    payload = json.dumps(compact_result(response), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    tokens = len(encoding.encode(payload, disallowed_special=()))
    require(characters == response["total_source_chars"] <= config["quality_source_budget"], "Source cap/accounting differs")
    require(response["token_usage"]["encoding"] == "cl100k_base" and response["token_usage"]["tokens"] == tokens <= config["quality_token_budget"], "Exact payload token count/cap differs")
    return {"payload_tokens": tokens, "source_characters": characters, "delivered_sources_exact": True,
            "payload_sha256": digest(payload.encode())}


def coverage(rows, target):
    # Source proof is separately required; IDs/names alone do not earn credit.
    return any((row.path if hasattr(row, "path") else row["path"]) == target["path"]
               and (row.start_line if hasattr(row, "start_line") else row["start_line"]) <= target["evidence_lines"][0]
               and (row.end_line if hasattr(row, "end_line") else row["end_line"]) >= target["evidence_lines"][1]
               for row in rows)


class RecordingTokenizer:
    def __init__(self, original, owner):
        self.original, self.owner = original, owner

    def __getattr__(self, key):
        return getattr(self.original, key)

    def __call__(self, texts, **kwargs):
        result = self.original(texts, **kwargs)
        active = self.owner.active
        require(active is not None and list(texts) == active["actual_texts"], "Tokenizer input differs from active operation")
        ids = result["input_ids"]
        require(len(ids) == len(texts) and all(tokens for tokens in ids), "Tokenizer returned missing inputs")
        lengths = [len(tokens) for tokens in ids]
        full_lengths = list(lengths)
        limit = kwargs.get("max_length")
        suspects = [i for i, length in enumerate(lengths) if kwargs.get("truncation") and length == limit]
        if suspects:
            full = self.original([texts[i] for i in suspects], padding=False, truncation=False,
                                 add_special_tokens=kwargs.get("add_special_tokens", True))["input_ids"]
            for i, tokens in zip(suspects, full, strict=True):
                full_lengths[i] = len(tokens)
        active["tokens"] = [list(tokens) for tokens in ids]
        active["full_lengths"] = full_lengths
        active["order"] = sorted(range(len(ids)), key=lambda i: (len(ids[i]), i))
        return result


def recording_embedder_class(base):
    """Subclass only for evidence; delegate every model/tokenizer/math operation."""
    class Recorded(base):
        def __init__(self, *args, journal, work, **kwargs):
            super().__init__(*args, **kwargs)
            self.journal, self.work = journal, Path(work)
            self.active = None
            self.documents_by_text = {}
            self.cache_vectors = {}
            self.operations = []
            self.query_ids = {}
            self.initialization = []
            self.allowed_native_sources = None
            self.counts = {"document_inputs": 0, "query_inputs": 0,
                           "attempted_forwards": 0, "completed_forwards": 0}

        def bind_snippets(self, snippets):
            self.documents_by_text = {document(snippet): snippet.id for snippet in snippets}

        def bind_queries(self, queries):
            self.query_ids = {row["query"]: row["id"] for row in queries}

        def _ensure_loaded(self):
            fresh = self._model is None
            start = stamp() if fresh else None
            if fresh:
                self.journal.emit("model_initialization_started")
            super()._ensure_loaded()
            if fresh:
                self._tokenizer = RecordingTokenizer(self._tokenizer, self)
                end = stamp()
                receipt = {"start": start, "end": end,
                           "elapsed_s": (end["perf_counter_ns"] - start["perf_counter_ns"]) / 1e9,
                           "model_class": type(self._model).__module__ + "." + type(self._model).__qualname__,
                           "tokenizer_class": type(self._tokenizer.original).__module__ + "." + type(self._tokenizer.original).__qualname__,
                           "loaded_native_origins": loaded_native_origins()}
                if self.allowed_native_sources is not None:
                    for origin in receipt["loaded_native_origins"].values():
                        require(self.allowed_native_sources.get(origin["path"]) == origin["sha256"], "Loaded native origin differs from frozen source")
                self.initialization.append(receipt)
                self.journal.emit("model_initialization_completed", **receipt)

        def _embed_batch(self, token_ids):
            active = self.active
            require(active is not None and "order" in active, "Native batch has no recorded tokenizer operation")
            first = active["completed_inputs"]
            positions = active["order"][first:first + len(token_ids)]
            require(len(positions) == len(token_ids) and all(list(tokens) == active["tokens"][i]
                    for tokens, i in zip(token_ids, positions, strict=True)), "Native batch order/input tokens differ")
            require(0 < len(token_ids) <= self.batch_size, "Native batch size exceeds frozen value")
            inputs = [{"position": i, "input_sha256": digest(active["actual_texts"][i].encode()),
                       "document_id": self.documents_by_text.get(active["actual_texts"][i]),
                       "input_ids": active["tokens"][i],
                       "input_ids_sha256": digest(json.dumps(active["tokens"][i], separators=(",", ":")).encode()),
                       "actual_tokens": len(active["tokens"][i]), "full_tokens": active["full_lengths"][i],
                       "truncated": active["full_lengths"][i] > len(active["tokens"][i])} for i in positions]
            self.counts["attempted_forwards"] += 1
            number = self.counts["attempted_forwards"]
            self.journal.emit("native_batch_started", operation=active["number"], forward_number=number,
                              kind=active["kind"], batch_size=len(token_ids), inputs=inputs)
            before = time.perf_counter_ns()
            vectors = super()._embed_batch(token_ids)
            elapsed = (time.perf_counter_ns() - before) / 1e9
            require(isinstance(vectors, np.ndarray) and vectors.dtype == np.float32
                    and vectors.shape == (len(token_ids), self.metadata["dimensions"])
                    and np.isfinite(vectors).all() and np.all(np.any(vectors != 0, axis=1)), "Native batch output invalid")
            self.counts["completed_forwards"] += 1
            active["completed_inputs"] += len(token_ids)
            self.journal.emit("native_batch_completed", operation=active["number"], forward_number=number,
                              kind=active["kind"], batch_size=len(token_ids), inputs=inputs,
                              vector_sha256=[digest(vector.tobytes()) for vector in vectors], native_compute_s=elapsed)
            return vectors

        def _record(self, kind, texts, actual_texts, operation):
            require(self.active is None, "Overlapping embedding operations forbidden")
            require(isinstance(texts, list) and texts and all(isinstance(text, str) and text.strip() for text in texts), "Nonempty literal embedding inputs required")
            if kind == "documents":
                require(all(text in self.documents_by_text for text in texts), "Unbound document embedding input")
            else:
                require(texts[0] in self.query_ids, "Unbound query embedding input")
            number = len(self.operations) + 1
            self.active = {"kind": kind, "number": number, "actual_texts": actual_texts, "completed_inputs": 0}
            self.counts["document_inputs" if kind == "documents" else "query_inputs"] += len(texts)
            start = stamp()
            self.journal.emit("embedding_operation_started", number=number, kind=kind,
                              query_id=self.query_ids.get(texts[0]) if kind == "query" else None,
                              input_sha256=[digest(text.encode()) for text in actual_texts])
            try:
                vectors = operation()
                if kind == "query":
                    vectors = vectors.reshape(1, -1)
                require(isinstance(vectors, np.ndarray) and vectors.dtype == np.float32
                        and vectors.shape == (len(texts), self.metadata["dimensions"])
                        and np.isfinite(vectors).all() and np.all(np.any(vectors != 0, axis=1)), "Embedding operation returned invalid matrix")
                require(self.active["completed_inputs"] == len(texts), "Native input/operation count mismatch")
                receipt = {"number": number, "kind": kind, "inputs": len(texts), "start": start, "end": stamp(),
                           "input_sha256": [digest(text.encode()) for text in actual_texts],
                           "query_id": self.query_ids.get(texts[0]) if kind == "query" else None,
                           "matrix_raw_sha256": digest(vectors.tobytes()), "shape": list(vectors.shape), "dtype": str(vectors.dtype)}
                if kind == "documents":
                    path = self.work / f"document-operation-{number:04d}.npy"
                    with path.open("xb") as stream:
                        np.save(stream, vectors, allow_pickle=False)
                    receipt["matrix_file"] = artifact(path)
                    receipt["matrix_file_sha256"] = sha(path)
                    for text, vector in zip(texts, vectors, strict=True):
                        self.cache_vectors[digest(text.encode())] = vector.copy()
                self.operations.append(receipt)
                self.journal.emit("embedding_operation_completed", **receipt)
                return vectors if kind == "documents" else vectors[0]
            finally:
                self.active = None

        def embed_documents(self, texts, *, cancel_check=None):
            return self._record("documents", texts, texts, lambda: super(Recorded, self).embed_documents(texts, cancel_check=cancel_check))

        def embed_query(self, query):
            from semantic_search.embeddings import QUERY_PROMPT
            return self._record("query", [query], [QUERY_PROMPT + query], lambda: super(Recorded, self).embed_query(query))
    return Recorded


def distribution(values):
    require(values and all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in values), "Finite nonnegative samples required")
    return {"count": len(values), "median_s": float(np.median(values)),
            "p95_s": float(np.percentile(values, 95, method="linear")),
            "min_s": min(values), "max_s": max(values), "samples_s": values}


def disk(database):
    paths = [Path(database), Path(str(database) + "-wal"), Path(str(database) + "-shm")]
    sizes = {path.name: path.stat().st_size for path in paths if path.exists()}
    return {"files": sizes, "total_bytes": sum(sizes.values())}


def memory(mx=None):
    import resource
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    row = {"rss_peak_bytes": int(raw if sys.platform == "darwin" else raw * 1024),
           "rss_peak_is_cumulative": True}
    result = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, check=True)
    row["rss_current_bytes"] = int(result.stdout.strip()) * 1024
    if mx is not None:
        row["mlx"] = {"active_bytes": mx.get_active_memory(), "peak_bytes": mx.get_peak_memory(), "cache_bytes": mx.get_cache_memory()}
    return row


def power():
    if sys.platform != "darwin":
        return {"supported": False, "raw": None, "ac_power": None}
    result = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, check=True)
    return {"supported": True, "raw": result.stdout, "ac_power": "AC Power" in result.stdout}


def sleep_events(start_utc):
    if sys.platform != "darwin":
        return {"supported": False, "events": []}
    result = subprocess.run(["pmset", "-g", "log"], capture_output=True, text=True, check=True, timeout=20)
    from datetime import datetime
    start = datetime.fromisoformat(start_utc).timestamp()
    events = []
    for line in result.stdout.splitlines():
        if "Entering Sleep" not in line and "DarkWake" not in line and " Wake " not in line:
            continue
        try:
            observed = datetime.strptime(line[:25], "%Y-%m-%d %H:%M:%S %z").timestamp()
        except ValueError:
            continue
        if observed >= start:
            events.append(line)
    return {"supported": True, "events": events}


def compress_journal(path, destination=None):
    path = Path(path)
    destination = Path(str(path) + ".gz") if destination is None else Path(destination)
    require(not destination.exists(), "No journal overwrite")
    destination.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    return destination


def stop_owned(process, term_grace_s=30., kill_grace_s=10.):
    """Stop only the directly created session/group; preserve clean vs forced exit."""
    require(type(process.pid) is int and process.pid > 1, "Owned process PID required")
    result = {"pid": process.pid, "owned_group": process.pid, "term_sent": False,
              "forced_kill": False, "owned_group_stopped": False, "errors": [],
              "started_cleanup": stamp(), "term_grace_s": term_grace_s, "kill_grace_s": kill_grace_s}

    def action(number):
        process.poll()
        try:
            os.killpg(process.pid, number)
            return True
        except ProcessLookupError:
            return False

    try:
        if process.poll() is None:
            require(os.getpgid(process.pid) == process.pid, "Refuse cleanup of a process without its own session group")
        if action(0):
            result["term_sent"] = action(signal.SIGTERM)
            deadline = time.monotonic() + term_grace_s
            while action(0) and time.monotonic() < deadline:
                time.sleep(.05)
            if action(0):
                result["forced_kill"] = action(signal.SIGKILL)
                deadline = time.monotonic() + kill_grace_s
                while action(0) and time.monotonic() < deadline:
                    time.sleep(.05)
        if process.poll() is None:
            process.wait(timeout=kill_grace_s)
        result["owned_group_stopped"] = not action(0)
    except BaseException as error:
        result["errors"].append({"type": type(error).__name__, "message": str(error)})
    result.update(exit_code=process.poll(), finished_cleanup=stamp())
    return result
