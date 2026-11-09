#!/usr/bin/env python3
"""Profile the unchanged provider using already observed saved-vector requests.

This CPU diagnostic performs no inference, retrieval, SQLite access or source
snapshot scan. Historical forward receipts are replayed solely to check exact
feature/receipt equivalence. Replay timings are not native or warm latency.
"""
from __future__ import annotations

import argparse
import cProfile
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib
import importlib.abc
import json
import marshal
import math
import os
from pathlib import Path
import platform
import pstats
import re
import socket
import statistics
import struct
import subprocess
import sys
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "dependency_provider_cpu_profile_v1"
PUBLICATION = "b08c784162c4a89192c1cd0a1bf94f6d35edddcb"
DEFAULT_STUDY = ROOT / "results/role-native-holdout-v2-2026-11-03"
SOURCE_NAMES = ("semantic_provider", "semantic_features", "retrieval", "corpus", "languages", "source", "__init__")
PROFILE_SOURCES = ("experiments/dependency_context/provider_profile.py", "experiments/dependency_context/fast_provider.py",
                   "experiments/__init__.py", "experiments/dependency_context/__init__.py",
                   "tests/test_dependency_provider_profile.py", "tests/test_dependency_fast_provider.py")
FORBIDDEN = ("mlx", "mlx_embeddings", "torch", "transformers", "jax", "semantic_search.embeddings",
             "semantic_search.backend", "semantic_search.store", "semantic_search.persistent_retrieval",
             "semantic_search.index", "semantic_search.model", "semantic_search.qwen_worker",
             "semantic_search.qwen_reranker", "semantic_search.qwen_reranker_core")
EXCLUSIONS = ["actual model inference and tokenizer/model API work", "actual SQLite accessor validation and I/O",
              "actual saved-source snapshot scans", "actual embedder metadata/package-version generation",
              "provider module imports, both provider factories, fast function clone setup and replay fixture reconstruction",
              "full backend retrieval/selection/packing", "MCP, coding-agent and provider billing measurements"]
TIMING_BOUNDARY = "Only the provider callable: imports, both factory constructors, fast clone setup and fixture reconstruction occur before its timer"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def file_sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def forbidden(name):
    return any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN)


@contextmanager
def cpu_only_guard(*, strict=False):
    """Block new model/storage imports and network; permit CPU NumPy values."""
    if strict:
        require(not any(forbidden(name) for name in sys.modules), "CPU replay requires a clean model/index-free process")

    class Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if forbidden(fullname):
                raise RuntimeError("CPU replay forbids model/index import: " + fullname)

    def deny(*args, **kwargs):
        raise RuntimeError("CPU replay forbids network and retrieval calls")

    guard = Guard()
    sys.meta_path.insert(0, guard)
    try:
        with ExitStack() as stack:
            for target, name in ((socket, "create_connection"), (socket, "getaddrinfo"),
                                 (socket.socket, "connect"), (socket.socket, "connect_ex"), (socket.socket, "sendto"),
                                 (socket.socket, "send"), (socket.socket, "sendall")):
                stack.enter_context(patch.object(target, name, deny))
            yield
    finally:
        sys.meta_path.remove(guard)


def executing_profiler(root=ROOT):
    root = Path(root).resolve()
    require(Path(__file__).resolve() == root / "experiments/dependency_context/provider_profile.py",
            "Executing profiler escapes the supplied bound checkout")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for name, path in (("experiments", "experiments/__init__.py"),
                       ("experiments.dependency_context", "experiments/dependency_context/__init__.py")):
        module = importlib.import_module(name)
        require(Path(module.__file__).resolve() == root / path,
                "Executing profiler package escapes the supplied bound checkout")


def provider_modules(root=ROOT):
    executing_profiler(root)
    location = str(Path(root) / "src")
    if location not in sys.path:
        sys.path.insert(0, location)
    provider = importlib.import_module("semantic_search.semantic_provider")
    features = importlib.import_module("semantic_search.semantic_features")
    # Import the literal-clause helper before timing; never construct a retriever.
    for name in SOURCE_NAMES:
        module = importlib.import_module("semantic_search" if name == "__init__" else "semantic_search." + name)
        expected = Path(root) / "src/semantic_search" / (name + ".py")
        require(Path(module.__file__).resolve() == expected.resolve(), "Executing original provider source escapes the bound checkout")
    return provider, features


def git_sha(root, commit, name):
    process = subprocess.Popen(["git", "-C", str(root), "show", commit + ":" + name], stdout=subprocess.PIPE)
    value = hashlib.sha256()
    for block in iter(lambda: process.stdout.read(1024 * 1024), b""):
        value.update(block)
    require(process.wait() == 0, "Original published blob is unavailable: " + name)
    return value.hexdigest()


def bind_native_study(study=DEFAULT_STUDY, *, root=ROOT, verify_git=True):
    """Bind original compressed archive, audit and executing provider bytes."""
    root, study = Path(root).resolve(), Path(study).resolve()
    require(study.is_relative_to(root), "Native study must be inside the bound checkout")
    paths = [study / name for name in ("plan.json", "independent-audit.json", "attempt/summary.json",
             "attempt/run-started.json", "attempt/searches.jsonl.gz")]
    plan, audit, summary, start = (json.loads(path.read_bytes()) for path in paths[:4])
    require(audit.get("schema") == "role_native_audit_v1" and audit.get("status") == "passed"
            and audit.get("checks_passed") is True and audit.get("verified_requests") == 252
            and audit.get("verified_warmups") == 6 and summary.get("complete") is True,
            "Complete original independently audited native study required")
    archive = paths[-1]
    expected = summary["artifact_sha256"]["searches.jsonl.gz"]
    require(expected == audit["artifact_sha256"]["searches.jsonl.gz"] == file_sha(archive)
            and summary["plan_sha256"] == start["plan_sha256"] == file_sha(paths[0]),
            "Original compressed archive/plan binding differs")
    source_names = ["src/semantic_search/" + name + ".py" for name in SOURCE_NAMES]
    source_hashes = {name: plan["binding"]["implementation_sha256"][name] for name in source_names}
    require(all(file_sha(root / name) == value for name, value in source_hashes.items()),
            "Original provider or transitive source changed")
    hashes = {path.relative_to(root).as_posix(): file_sha(path) for path in paths}
    if verify_git:
        for name, value in {**hashes, **source_hashes}.items():
            require(git_sha(root, PUBLICATION, name) == value, "Original published archive/source blob differs: " + name)
    return {"publication_commit": PUBLICATION if verify_git else None, "published_blobs_verified": verify_git,
            "artifact_sha256": hashes, "original_source_sha256": source_hashes,
            "archive_path": archive.relative_to(root).as_posix(), "archive_sha256": expected,
            "model_fingerprint": plan["binding"]["runtime_manifest"]["model_binding"]["fingerprint"],
            "native_execution_commit": start["execution_commit"], "scope": "Trusted saved native inputs; no new model provenance"}


def select_records(archive, *, active_per_mode=2, repetition=1):
    require(type(active_per_mode) is int and active_per_mode > 0 and type(repetition) is int and repetition in (1, 2, 3),
            "Positive active sample count and original repetition1..3 required")
    selected, counts = [], {}
    with gzip.open(archive, "rb") as stream:
        for number, raw in enumerate(stream, 1):
            record = json.loads(raw)
            if record.get("arm") != "candidate" or record.get("repetition") != repetition:
                continue
            capture = record["candidate_capture"]
            receipts = capture["provider_receipts"]
            active = bool(receipts and receipts[0].get("status") == "active")
            key = record["retrieval"], active
            limit = active_per_mode if active else 1
            if key[0] not in ("dense", "hybrid") or counts.get(key, 0) >= limit:
                continue
            selected.append({"record": record, "source": {"jsonl_line": number, "line_sha256": digest(raw),
                "canonical_record_sha256": digest(canonical(record)), "identity": [record["query_id"], record["retrieval"], record["repetition"], "candidate"]}})
            counts[key] = counts.get(key, 0) + 1
    require(all(counts.get((mode, active), 0) == (active_per_mode if active else 1)
                for mode in ("dense", "hybrid") for active in (True, False)),
            "Original archive lacks the requested active/inactive sample strata")
    return selected


def all_candidate_records(archive):
    """The complete fixed native candidate schedule, with no outcome filtering."""
    selected, seen = [], set()
    with gzip.open(archive, "rb") as stream:
        for number, raw in enumerate(stream, 1):
            record = json.loads(raw)
            if record.get("arm") != "candidate":
                continue
            key = record["query_id"], record["retrieval"], record["repetition"], "candidate"
            require(key not in seen, "Duplicate saved native candidate request")
            seen.add(key)
            selected.append({"record": record, "source": {"jsonl_line": number, "line_sha256": digest(raw),
                "canonical_record_sha256": digest(canonical(record)), "identity": list(key)}})
    query_ids = {key[0] for key in seen}
    require(len(query_ids) == 14 and len(seen) == 84
            and seen == {(query, mode, repetition, "candidate") for query in query_ids
                         for mode in ("dense", "hybrid") for repetition in (1, 2, 3)},
            "Exact original 84-candidate native schedule required")
    return selected


def verify_freeze(root, commit):
    """Paired measured replay requires committed source/test bytes first."""
    require(type(commit) is str and re.fullmatch(r"[0-9a-f]{40}", commit) is not None,
            "Paired CPU comparison requires an explicit full source freeze commit")
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, head],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0,
            "CPU comparison source freeze must be an ancestor of execution")
    hashes = {name: file_sha(Path(root) / name) for name in PROFILE_SOURCES}
    require(all(value == git_sha(root, commit, name) == git_sha(root, head, name) for name, value in hashes.items()),
            "CPU comparison source/test bytes changed after freeze or are uncommitted")
    return {"commit": commit, "execution_commit": head, "source_sha256": hashes}


def exact_vector(value, expected_hash, *, dimensions=1024):
    require(type(value) is list and len(value) == dimensions
            and all(type(item) is float and math.isfinite(item) for item in value), "Exact saved float32 vector required")
    try:
        raw = struct.pack("<" + "f" * dimensions, *value)
    except (OverflowError, struct.error) as error:
        raise ValueError("Saved vector exceeds float32") from error
    require(list(struct.unpack("<" + "f" * dimensions, raw)) == value and digest(raw) == expected_hash,
            "Saved float32 value/hash changed")
    import numpy as np
    vector = np.frombuffer(raw, dtype="<f4")
    require(bool(np.any(vector)), "Saved vector must have a nonzero norm")
    return vector


class ReplayIndex:
    """Return source-bound saved arrays; never open or emulate SQLite."""
    def __init__(self, request, vectors):
        self.request, self.vectors, self.calls = request, vectors, 0

    def read_semantic_vectors(self, rows, *, model_metadata, cancel_check):
        cancel_check()
        require(rows == self.request["requested_rows"] and model_metadata == self.request["model_metadata"],
                "Replayed resident lookup differs from the recorded request")
        self.calls += 1
        return self.vectors


class ReplayEmbedder:
    """Replay clause arrays/receipts; no tokenizer, inference or query encoding."""
    def __init__(self, metadata, facets, vectors, events):
        self._metadata, self.facets, self.vectors, self.events = metadata, facets, vectors, events
        self.calls = 0

    @property
    def metadata(self):
        return deepcopy(self._metadata)

    def embed_queries(self, clauses, *, cancel_check, forward_observer):
        cancel_check()
        require(clauses == self.facets[1:], "Replayed clauses differ from the original literal prompts")
        self.calls += 1
        for event in self.events:
            forward_observer(deepcopy(event))
        cancel_check()
        return self.vectors

    def embed_query(self, *args, **kwargs):
        raise AssertionError("CPU replay cannot re-encode the original query")

    def embed_documents(self, *args, **kwargs):
        raise AssertionError("CPU replay cannot encode documents")


def prepare_record(record, *, root=ROOT):
    provider, features_module = provider_modules(root)
    capture = record["candidate_capture"]
    reference, raw = deepcopy(capture["reference_response"]), deepcopy(capture["ranked_candidates"])
    require(record.get("arm") == "candidate" and reference["query"] == record["response"]["query"]
            and reference["retrieval"] == record["retrieval"] and len(raw) <= 50, "Saved candidate identity differs")
    receipts = capture["provider_receipts"]
    facets = features_module.expected_facets(reference["query"])
    if not facets:
        require(capture["features"] is None and receipts == [] and record["resident_reads"] == [],
                "Native adapter bypass must contain no provider work")
        return {"provider": provider, "features_module": features_module, "record": record,
                "reference": reference, "raw": raw, "bypassed": True}
    require(type(receipts) is list and len(receipts) == 1, "One original provider receipt required")
    receipt = receipts[0]
    require(receipt["status"] == "active" and len(record["resident_reads"]) == 1,
            "This profile supports original active captures or actual adapter bypasses")
    features = capture["features"]
    require(features["facets"] == facets and receipt["facets"] == facets
            and features["facet_vectors"][0] == record["query_vector"], "Original query/facet vectors differ")
    query = exact_vector(record["query_vector"], record["query_vector_sha256"])
    require(receipt["original_query_vector"]["float32_le_sha256"] == record["query_vector_sha256"],
            "Original query vector receipt differs")
    request = record["resident_reads"][0]
    required = features_module._required_rows(reference, raw)
    require(request["requested_rows"] == list(required.values()) and features["windows"].keys() == required.keys()
            == request["returned_vectors"].keys(), "Original full source/vector lookup membership differs")
    vectors = {}
    for key, value in request["returned_vectors"].items():
        vector = exact_vector(value["vector"], value["float32_le_sha256"])
        require(features["windows"][key]["vector"] == value["vector"]
                and features["windows"][key]["binding"] == features_module.row_binding(required[key])
                and receipt["windows"][key]["float32_le_sha256"] == value["float32_le_sha256"],
                "Original captured resident vector/source binding differs")
        vectors[key] = vector
    clauses = [exact_vector(value, digest(struct.pack("<" + "f" * 1024, *value)))
               for value in features["facet_vectors"][1:]]
    metadata = deepcopy(features["model_metadata"])
    require(request["model_metadata"] == receipt["model_metadata"] == metadata
            and digest(json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
            == features["model_fingerprint"] == capture["request_binding"]["model_fingerprint"], "Original model binding differs")
    provenances = {}
    for row in [*raw, *reference["results"]]:
        value = {name: row[name] for name in features_module.PROVENANCE if name in row}
        require(row["path"] not in provenances or provenances[row["path"]] == value, "Conflicting saved source provenance")
        provenances[row["path"]] = value
    return {"provider": provider, "features_module": features_module, "record": record,
            "reference": reference, "raw": raw, "bypassed": False, "query_vector": query,
            "clause_vectors": clauses, "vectors": vectors, "metadata": metadata, "provenances": provenances}


class Stages:
    """Inclusive and child-adjusted wrapper times; neither is native latency."""
    def __init__(self):
        self.events, self.stack = [], []

    def wrap(self, name, function):
        def call(*args, **kwargs):
            entry = {"stage": name, "children_s": 0.0}
            self.stack.append(entry)
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                self.stack.pop()
                entry.update(inclusive_s=elapsed, exclusive_s=max(0.0, elapsed-entry.pop("children_s")))
                self.events.append(entry)
                if self.stack:
                    self.stack[-1]["children_s"] += elapsed
        return call


def one_replay(prepared, *, implementation="original", stage_trace=False, profiler=None):
    require(implementation in ("original", "fast"), "Unknown provider implementation")
    if prepared["bypassed"]:
        # The real native adapter did not call the provider for this record.
        return {"wall_s": None, "features": None, "receipts": [], "stage_events": [],
                "calls": {"saved_clause_vector_replay": 0, "saved_resident_vector_replay": 0,
                          "saved_snapshot_confirmation": 0}, "execution": "native_adapter_bypassed_provider"}
    original = prepared["record"]["candidate_capture"]
    receipt = original["provider_receipts"][0]
    index = ReplayIndex(prepared["record"]["resident_reads"][0], prepared["vectors"])
    embedder = ReplayEmbedder(prepared["metadata"], original["features"]["facets"], prepared["clause_vectors"],
                              receipt["extra_query_forward_events"])
    checks = []

    def confirm():
        checks.append(True)

    context = {**deepcopy(original["request_binding"]), "query_vector": prepared["query_vector"],
        "query_forward_events": deepcopy(receipt["original_query_forward_events"]), "model_metadata": deepcopy(prepared["metadata"]),
        "index": index, "embedder": embedder, "confirm_snapshot": confirm,
        "source_provenance": lambda path: prepared["provenances"][path]}
    captures, stages = [], Stages()
    factory = prepared["provider"].make_live_feature_provider
    if implementation == "fast":
        # Import and clone setup is deliberately outside the measured call.
        fast_module = importlib.import_module("experiments.dependency_context.fast_provider")
        root = Path(prepared["provider"].__file__).resolve().parents[2]
        require(Path(fast_module.__file__).resolve() == root / "experiments/dependency_context/fast_provider.py"
                and fast_module.ORIGINAL_FACTORY is factory
                and fast_module.ORIGINAL_FEATURES is prepared["features_module"].make_features,
                "Executing fast provider escapes the bound original source")
        factory, feature_maker = fast_module.clone_functions()
        namespaces = (factory.__globals__, feature_maker.__globals__)
    else:
        namespaces = (prepared["provider"].__dict__, prepared["features_module"].__dict__)
    with ExitStack() as stack:
        if stage_trace:
            for name in ("make_features", "_required_rows", "_vector", "_model", "_forward_summary", "_provenance", "_vector_hash"):
                for namespace in namespaces:
                    if name in namespace:
                        stack.enter_context(patch.dict(namespace, {name: stages.wrap(name, namespace[name])}))
        provider = factory(context, capture=captures.append)
        started = time.perf_counter()
        if profiler is not None:
            profiler.enable()
        try:
            features = provider(prepared["reference"], prepared["raw"])
        finally:
            if profiler is not None:
                profiler.disable()
        elapsed = time.perf_counter()-started
    require(canonical(features) == canonical(original["features"]) and canonical(captures) == canonical(original["provider_receipts"]),
            "Provider replay feature/receipt equivalence failed")
    require(index.calls == embedder.calls == 1 and len(checks) == 3, "Provider replay call boundary changed")
    return {"wall_s": elapsed, "features": features, "receipts": captures, "stage_events": stages.events,
            "calls": {"saved_clause_vector_replay": embedder.calls, "saved_resident_vector_replay": index.calls,
                      "saved_snapshot_confirmation": len(checks)}, "execution": implementation + "_provider_with_saved_float32_arrays"}


def paired_record(record, *, repetitions=3, ordinal=0, root=ROOT):
    """Alternate order on identical saved arrays; no model or native timing."""
    require(type(repetitions) is int and repetitions > 0 and type(ordinal) is int and ordinal >= 0,
            "Positive CPU repetition count and nonnegative record ordinal required")
    prepared, passes = prepare_record(record, root=root), []
    for repetition in range(repetitions):
        order = ["original", "fast"] if (ordinal + repetition) % 2 == 0 else ["fast", "original"]
        pair = {}
        for implementation in order:
            value = one_replay(prepared, implementation=implementation)
            pair[implementation] = {"wall_s": value["wall_s"], "calls": value["calls"], "execution": value["execution"],
                "features_sha256": digest(canonical(value["features"])), "receipts_sha256": digest(canonical(value["receipts"]))}
        require(all(pair["original"][name] == pair["fast"][name] for name in ("calls", "features_sha256", "receipts_sha256")),
                "Paired providers differ in exact features, receipts or executing replay calls")
        passes.append({"repetition": repetition + 1, "order": order, **pair})
    medians = {name: statistics.median([value[name]["wall_s"] for value in passes]) if not prepared["bypassed"] else None
               for name in ("original", "fast")}
    return {"identity": [record["query_id"], record["retrieval"], record["repetition"], "candidate"],
        "raw_rows": len(prepared["raw"]), "full_vector_windows": len(prepared.get("vectors", {})),
        "bypassed": prepared["bypassed"], "passes": passes, "median_provider_call_s": medians,
        "exact_equivalence": True, "new_model_forwards": 0, "retrieval_calls": 0, "sqlite_accesses": 0,
        "classification": "CPU saved-vector paired replay; not native or warm latency",
        "timing_boundary": TIMING_BOUNDARY, "exclusions": list(EXCLUSIONS)}


def profile_record(record, *, repetitions=3, root=ROOT):
    require(type(repetitions) is int and repetitions > 0, "Positive CPU replay repetition count required")
    prepared = prepare_record(record, root=root)
    ordinary = [one_replay(prepared) for _ in range(repetitions)]
    staged = one_replay(prepared, stage_trace=True)
    profiler = cProfile.Profile()
    profiled = one_replay(prepared, profiler=profiler)
    stats = pstats.Stats(profiler) if not prepared["bypassed"] else pstats.Stats()
    functions = [{"file": str(Path(file).relative_to(root)) if Path(file).is_relative_to(root) else file,
                  "line": line, "function": function, "primitive_calls": value[0], "total_calls": value[1],
                  "self_s": value[2], "cumulative_s": value[3]}
                 for (file, line, function), value in stats.stats.items()]
    functions.sort(key=lambda item: (-item["cumulative_s"], item["file"], item["line"]))
    original = record["candidate_capture"]
    timings = [row["wall_s"] for row in ordinary if row["wall_s"] is not None]
    result = {"identity": [record["query_id"], record["retrieval"], record["repetition"], "candidate"],
        "classification": "CPU saved-vector replay; not native, warm, inference or end-to-end latency",
        "raw_rows": len(prepared["raw"]), "full_vector_windows": len(prepared.get("vectors", {})),
        "execution": staged["execution"], "vector_representation": "read-only NumPy little-endian float32; original component bytes",
        "equivalence": {"features_sha256": digest(canonical(original["features"])),
            "provider_receipts_sha256": digest(canonical(original["provider_receipts"])), "all_passes_exact": True},
        "uninstrumented_replay_wall_s": timings, "uninstrumented_replay_median_s": statistics.median(timings) if timings else None,
        "stage_instrumented_wall_s": staged["wall_s"], "stage_events": staged["stage_events"],
        "cprofile_wall_s": profiled["wall_s"], "cprofile_functions": functions,
        "replay_calls_per_pass": staged["calls"], "new_model_forward_calls": 0, "new_query_encoding_calls": 0,
        "new_document_encoding_calls": 0, "sqlite_index_calls": 0, "retrieval_calls": 0,
        "historical_receipt_scope": "Original forward events replayed for equivalence only; no new forward was performed",
        "timing_boundary": TIMING_BOUNDARY, "exclusions": list(EXCLUSIONS)}
    return result, stats


def sync(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new(path, value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    sync(Path(path).parent)


def runtime():
    import numpy as np
    return {"python": platform.python_version(), "executable": sys.executable,
            "platform": platform.platform(), "numpy": np.__version__}


def run(study, output, *, active_per_mode=2, repetitions=3, root=ROOT, verify_git=True):
    root, output = Path(root).resolve(), Path(output).resolve()
    executing_profiler(root)
    require(not output.exists(), "Use a new CPU profile directory; never overwrite or resume")
    with cpu_only_guard(strict=True):
        binding = bind_native_study(study, root=root, verify_git=verify_git)
        provider_modules(root)
        selected = select_records(root / binding["archive_path"], active_per_mode=active_per_mode)
        code_hash = file_sha(Path(__file__))
        output.mkdir(exist_ok=False)
        sync(output.parent)
        results = []
        try:
            write_new(output / "run-start.json", {"schema": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "binding": binding, "profiler_sha256": code_hash,
                "git_head": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
                "runtime": runtime(),
                "selection": "First original repetition; first active records per mode and first actual native adapter bypass per mode; no quality/latency selection",
                "classification": "CPU diagnostic replay, not a prospectively frozen native experiment",
                "timing_boundary": TIMING_BOUNDARY, "exclusions": EXCLUSIONS})
            for ordinal, item in enumerate(selected, 1):
                result, stats = profile_record(item["record"], repetitions=repetitions, root=root)
                result["source_record"] = item["source"]
                path = output / f"record-{ordinal:02d}.pstats"
                with path.open("xb") as stream:
                    marshal.dump(stats.stats, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                result["pstats_sha256"] = file_sha(path)
                write_new(output / f"record-{ordinal:02d}.json", result)
                results.append(result)
            require(file_sha(Path(__file__)) == code_hash
                    and all(file_sha(root / name) == value for name, value in binding["original_source_sha256"].items())
                    and file_sha(root / binding["archive_path"]) == binding["archive_sha256"], "CPU profile source/archive changed during replay")
            report = {"schema": SCHEMA, "status": "passed", "binding": binding, "records": results,
                "actual_work": {"new_model_forwards": 0, "query_encodings": 0, "document_encodings": 0,
                                "sqlite_accesses": 0, "retrieval_calls": 0, "network_calls": 0},
                "timing_boundary": TIMING_BOUNDARY,
                "classification": "Original-provider CPU attribution only; timings exclude native work and use synthetic source/index/model boundaries"}
            write_new(output / "summary.json", report)
            return report
        except BaseException as error:
            try:
                write_new(output / "failure.json", {"schema": SCHEMA, "status": "failed", "completed_records": len(results),
                    "error_type": type(error).__name__, "error": str(error), "binding": binding,
                    "classification": "Failed CPU diagnostic; no new inference or native timing conclusion"})
            except BaseException as recording_error:
                raise error from recording_error
            raise


def run_paired(study, output, *, freeze, repetitions=3, root=ROOT):
    """Freeze-checked complete saved schedule; never a native latency claim."""
    root, output = Path(root).resolve(), Path(output).resolve()
    executing_profiler(root)
    require(not output.exists(), "Use a new paired CPU directory; never overwrite or resume")
    with cpu_only_guard(strict=True):
        frozen = verify_freeze(root, freeze)
        binding = bind_native_study(study, root=root)
        provider_modules(root)
        selected = all_candidate_records(root / binding["archive_path"])
        output.mkdir(exist_ok=False)
        sync(output.parent)
        results = []
        try:
            write_new(output / "run-start.json", {"schema": SCHEMA, "mode": "paired_complete_saved_schedule",
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "binding": binding, "source_freeze": frozen,
                "runtime": runtime(),
                "repetitions": repetitions, "saved_records": 84,
                "selection": "All84 saved native candidate records in original archive order; alternating implementation order by record+CPU repetition",
                "classification": "CPU saved-vector comparison; not native, warm or end-to-end latency",
                "timing_boundary": TIMING_BOUNDARY, "exclusions": EXCLUSIONS})
            for ordinal, item in enumerate(selected):
                result = paired_record(item["record"], repetitions=repetitions, ordinal=ordinal, root=root)
                result["source_record"] = item["source"]
                write_new(output / f"record-{ordinal+1:02d}.json", result)
                results.append(result)
            require(verify_freeze(root, freeze) == frozen and all(file_sha(root / name) == value
                for name, value in binding["original_source_sha256"].items())
                and file_sha(root / binding["archive_path"]) == binding["archive_sha256"],
                "CPU paired source/archive bindings changed during replay")
            by_mode = {}
            for mode in ("dense", "hybrid"):
                rows = [row for row in results if row["identity"][1] == mode]
                values = {name: [entry[name]["wall_s"] for row in rows for entry in row["passes"]
                                if entry[name]["wall_s"] is not None] for name in ("original", "fast")}
                by_mode[mode] = {"saved_requests": len(rows), "active_requests": sum(not row["bypassed"] for row in rows),
                    "adapter_bypasses": sum(row["bypassed"] for row in rows),
                    "cpu_provider_call_median_s": {name: statistics.median(value) for name, value in values.items()},
                    "measured_cpu_calls": {name: len(value) for name, value in values.items()}}
            report = {"schema": SCHEMA, "status": "passed", "mode": "paired_complete_saved_schedule",
                "source_freeze": frozen, "binding": binding, "records": results, "by_mode": by_mode,
                "every_feature_receipt_and_call_count_exact": True,
                "actual_work": {"new_model_forwards": 0, "query_encodings": 0, "document_encodings": 0,
                    "sqlite_accesses": 0, "retrieval_calls": 0, "network_calls": 0},
                "classification": "CPU replay only; historical forward receipts replayed, not performed",
                "timing_boundary": TIMING_BOUNDARY, "exclusions": EXCLUSIONS}
            write_new(output / "summary.json", report)
            return report
        except BaseException as error:
            try:
                write_new(output / "failure.json", {"schema": SCHEMA, "status": "failed", "completed_records": len(results),
                    "error_type": type(error).__name__, "error": str(error), "binding": binding, "source_freeze": frozen})
            except BaseException as recording_error:
                raise error from recording_error
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--active-per-mode", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--paired", action="store_true", help="Compare original/fast on all84 saved candidates after committed source freeze")
    parser.add_argument("--freeze", help="Full committed source/test SHA; required for --paired")
    args = parser.parse_args()
    if args.paired:
        result = run_paired(args.study, args.output, freeze=args.freeze, repetitions=args.repetitions)
    else:
        require(args.freeze is None, "--freeze belongs to the paired comparison")
        result = run(args.study, args.output, active_per_mode=args.active_per_mode, repetitions=args.repetitions)
    print(json.dumps({"status": result["status"], "records": len(result["records"]),
                      "output": str(args.output.resolve()), "new_model_forwards": 0}))


if __name__ == "__main__":
    main()
