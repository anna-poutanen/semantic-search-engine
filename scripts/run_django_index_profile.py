#!/usr/bin/env python3
"""Freeze metadata, then explicitly run the owned native Django index profile."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
import math
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import sys
import threading
import time

import numpy as np

import django_index_profile_core as core
from qwen_optimization_clock import ClockIntegrity, ClockIntegrityError, POLICY as CLOCK_POLICY
from django_index_profile_core import stop_owned

ROOT = core.ROOT
MANIFEST = ROOT / "benchmarks/django_index_profile.json"
PREPARATION = ROOT / "results/django-index-preflight-2026-10-14"
PROTOCOL = ROOT / "docs/large-index-profile-protocol.md"
RUNTIME_AMENDMENT = ROOT / "docs/large-index-runtime-metadata-amendment.md"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def committed(paths):
    revision = git(ROOT, "rev-parse", "HEAD").decode().strip()
    for path in paths:
        relative = Path(path).relative_to(ROOT).as_posix()
        core.require(git(ROOT, "show", revision + ":" + relative) == Path(path).read_bytes(),
                     "Commit exact inputs/source before freeze or run: " + relative)
    return revision


def source_files():
    return [Path(__file__).resolve(), ROOT / "scripts/django_index_profile_core.py",
            ROOT / "scripts/qwen_optimization_clock.py", ROOT / "scripts/qwen_workflow_process.py",
            ROOT / "scripts/qwen_workflow_evidence.py", ROOT / "tests/test_django_index_profile.py",
            ROOT / "scripts/audit_django_index_profile.py", ROOT / "tests/test_audit_django_index_profile.py",
            ROOT / "pyproject.toml", ROOT / "requirements-mac.lock",
            *sorted((ROOT / "src/semantic_search").glob("*.py")),
            MANIFEST, PROTOCOL, RUNTIME_AMENDMENT, *sorted(path for path in PREPARATION.rglob("*") if path.is_file())]


def verify_hashes(bindings):
    for value, expected in bindings.items():
        path = core.resolve(value)
        core.require(path.is_file() and core.sha(path) == expected, "Frozen file changed: " + value)


def checkpoint_metadata(checkpoint):
    provenance = core.read(checkpoint / "semantic_search_checkpoint.json")
    core.require(provenance.get("id") == "Qwen/Qwen3-Embedding-0.6B"
                 and provenance.get("revision") == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
                 and provenance.get("weights_dtype") == "bfloat16" and provenance.get("quantized") is False,
                 "Original pinned embedding provenance required")
    weight = checkpoint / "model.safetensors"
    core.require(weight.is_file(), "Embedding weights absent")
    expected = provenance.get("weights_etag")
    core.require(isinstance(expected, str) and len(expected) == 64 and all(c in "0123456789abcdef" for c in expected), "Pinned weight SHA-256 required")
    files = {path.relative_to(checkpoint).as_posix(): {"bytes": path.stat().st_size,
             "sha256": expected if path == weight else core.sha(path),
             "sha256_verified_during_freeze": path != weight}
             for path in sorted(checkpoint.rglob("*")) if path.is_file()}
    return {"path": core.artifact(checkpoint), "files": files,
            "download_cached": True, "weights_loaded_during_freeze": False,
            "full_weight_hash_verified_during_run_before_inference": True}


def verify_checkpoint(binding):
    checkpoint = core.resolve(binding["path"])
    actual = {path.relative_to(checkpoint).as_posix() for path in checkpoint.rglob("*") if path.is_file()}
    core.require(actual == set(binding["files"]), "Checkpoint file membership changed")
    for name, expected in binding["files"].items():
        path = checkpoint / name
        core.require(path.stat().st_size == expected["bytes"] and core.sha(path) == expected["sha256"], "Checkpoint bytes differ: " + name)


def verify_original(plan):
    repo = core.resolve(plan["manifest"]["corpus"])
    core.require(git(repo, "rev-parse", "HEAD").decode().strip() == plan["manifest"]["revision"]
                 and not git(repo, "status", "--porcelain=v1"), "Original fixed corpus must remain clean")
    inventory = core.read(PREPARATION / "source-inventory.json")
    for row in inventory:
        core.require(core.sha(repo / row["path"]) == row["sha256"], "Original saved source changed")


def verify_environment(plan, *, checkpoint=True):
    verify_hashes(plan["source_sha256"])
    core.require(core.runtime() == plan["runtime"], "Python/package runtime drift")
    verify_hashes(plan["native_source_sha256"])
    verify_hashes(plan["tokenizer_cache_sha256"])
    verify_original(plan)
    if checkpoint:
        verify_checkpoint(plan["checkpoint"])


def coding_guard(pointer=None):
    pointer = ROOT / ".cache/qwen-coding-workflows-2026-10-14/driver-observed.json" if pointer is None else Path(pointer)
    core.require(pointer.is_file(), "Known coding-driver ownership proof is required")
    receipt = core.read(pointer)
    driver = receipt.get("driver")
    core.require(isinstance(driver, dict) and set(driver) == {"pid", "parent_pid", "process_group", "command"},
                 "Exact nested coding-driver ownership shape required")
    pid = driver["pid"]
    core.require(type(pid) is type(driver["process_group"]) is type(driver["parent_pid"]) is int
                 and pid > 1 and driver["process_group"] == pid and driver["parent_pid"] > 1
                 and isinstance(driver["command"], str), "Registered coding-driver PID/group proof malformed")
    argv = shlex.split(driver["command"])
    expected = [".venv/bin/python", "-B", "scripts/run_qwen_workflows.py", "run", "--output",
                "results/qwen-coding-workflows-2026-10-14"]
    core.require(argv == expected, "Registered coding-driver argv differs from authorized study")
    observed = subprocess.run(["ps", "-p", str(pid), "-o", "pid=,pgid=,command="], capture_output=True, text=True)
    if observed.returncode == 0:
        values = observed.stdout.strip().split(None, 2)
        core.require(len(values) == 3 and values[0] == str(pid) and values[1] == str(driver["process_group"])
                     and shlex.split(values[2]) == argv, "Live registered PID/group/argv differs; ownership remains unproved")
        raise ValueError("Coding comparison still active: no concurrent native index profile")
    core.require(observed.returncode == 1 and not observed.stdout.strip(), "Coding-driver exit status unproved")
    return {"registered_driver": True, "pid": pid, "owned_group": driver["process_group"],
            "recorded_argv": argv, "active": False, "ps_exit_code": observed.returncode,
            "proof": core.artifact(pointer), "proof_sha256": core.sha(pointer)}


def coding_completion(audit_path, study=None):
    study = ROOT / "results/qwen-coding-workflows-2026-10-14" if study is None else Path(study)
    certificate = core.read(audit_path)
    core.require(certificate.get("checks_passed") is True and certificate.get("inventory_complete") is True
                 and certificate.get("final_completion_receipt") is True and certificate.get("planned_attempts") == 18
                 and certificate.get("recorded_attempts") == 18 and certificate.get("missing_attempts") == [],
                 "Full18 terminal attempts and independent final certificate required")
    results = core.read(study / "results.json")
    completion = core.read(study / "completion.json")
    core.require(len(results) == 18 and len({row["run_id"] for row in results}) == 18
                 and completion["recorded_attempts"] == 18, "Coding final completion inventory differs")
    core.require(certificate["frozen_config_sha256"] == completion["frozen_config_sha256"] == core.sha(study / "frozen-config.json"),
                 "Coding final certificate/receipt configuration binding differs")
    for name, expected in certificate["artifact_sha256"].items():
        core.require(Path(name).name == name and core.sha(study / name) == expected, "Certified coding artifact changed: " + name)
    return {"study": core.artifact(study), "audit": core.artifact(audit_path), "audit_sha256": core.sha(audit_path),
            "completed_attempts": 18, "artifact_sha256": {core.artifact(study / name): core.sha(study / name) for name in
                ("frozen-config.json", "results.json", "completion.json", "summary.json", "run-start.json", "tasks.json")}}


def freeze(output, work, checkpoint, coding_audit):
    core.require(not output.exists() and not work.exists(), "Fresh output and work paths required")
    core.require(output.resolve().is_relative_to((ROOT / "results").resolve()) and
                 work.resolve().is_relative_to((ROOT / ".cache").resolve()) and work.resolve() != (ROOT / ".cache").resolve(), "Use new results/.cache children")
    files = source_files()
    revision = committed(files)
    verify_hashes({path: metadata["sha256"] for path, metadata in core.read(PREPARATION / "freeze.json")["artifacts"].items()})
    manifest = core.read(MANIFEST)
    coding = coding_completion(coding_audit)
    coding_exit = coding_guard()
    core.require(core.read(PREPARATION / "preparation-verification.json")["passed"] is True,
                 "Passing prospective source preparation required")
    guard = core.BlockModelImports()
    core.block_network()
    sys.meta_path.insert(0, guard)
    try:
        from semantic_search.embeddings import QwenEmbedder
        embedder = QwenEmbedder(model_path=checkpoint, batch_size=manifest["embedding"]["batch_size"], progress=False)
        metadata = embedder.metadata
        core.require(embedder._model is None and embedder._mx is None, "Freeze initialized model")
        embedder.close()
        native = core.native_sources()
        environment = core.runtime()
    finally:
        sys.meta_path.remove(guard)
    query_ids = [row["id"] for row in manifest["queries"]]
    providers = manifest["retrieval"]["providers"]
    ordered = core.schedule(query_ids, providers, manifest["retrieval"]["warmup_sweeps_per_provider"],
                            manifest["retrieval"]["measured_sweeps_per_provider"])
    tokenizer_cache = ROOT / ".cache/tokenizers"
    core.require(tokenizer_cache.is_dir() and any(tokenizer_cache.iterdir()), "Cached cl100k tokenizer required; no network in run")
    plan = {"version": 1, "kind": "prospective_large_real_persistent_index_profile",
            "frozen_at_utc": datetime.now(timezone.utc).isoformat(), "source_commit": revision,
            "output": core.artifact(output), "work": core.artifact(work), "manifest": manifest,
            "source_sha256": {core.artifact(path): core.sha(path) for path in files},
            "runtime": environment, "native_source_sha256": native,
            "checkpoint": checkpoint_metadata(checkpoint), "embedding_metadata": metadata,
            "tokenizer_cache_sha256": {core.artifact(path): core.sha(path) for path in tokenizer_cache.iterdir() if path.is_file()},
            "schedules": {"store": ordered, "api": ordered}, "clock_policy": CLOCK_POLICY,
            "worker_timeout_s": 3600, "fresh_reopen_timeout_s": 180,
            "owned_cleanup_bounds_s": {"term": 30., "kill": 10.},
            "model_calls_during_freeze": 0, "native_imports_during_freeze": guard.blocked,
            "fresh_reopen_layer": "source extraction + durable SQLite index; not SearchBackend/service startup",
            "instrumentation": "Native tokenizer/batch evidence and output matrix writes occur inside instrumented build/API timings; snapshots, independent checks, runtime hashes and public serialization occur outside query timings.",
            "resume_or_overwrite": False, "near_tie_mismatches_excused": False}
    plan["coding_precondition"] = coding
    plan["coding_driver_exit"] = coding_exit
    verify_original(plan)
    output.mkdir(parents=True, exist_ok=False)
    core.write_new(output / "frozen-plan.json", plan)
    print(json.dumps({"frozen": True, "output": core.artifact(output), "windows": manifest["source_window_count"],
                      "schedules": {key: len(rows) for key, rows in plan["schedules"].items()}, "model_calls": 0}), flush=True)


def descriptor(path):
    path = Path(path)
    return {"path": core.artifact(path), "sha256": core.sha(path), "bytes": path.stat().st_size}


def matrix_file(path, matrix):
    with Path(path).open("xb") as stream:
        np.save(stream, matrix, allow_pickle=False)
    return {**descriptor(path), "shape": list(matrix.shape), "dtype": str(matrix.dtype),
            "raw_sha256": core.digest(matrix.tobytes())}


def sqlite_snapshot(database, destination):
    core.require(not destination.exists(), "No database snapshot overwrite")
    destination.touch(exist_ok=False)
    with sqlite3.connect(database) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    return descriptor(destination)


def timed(clock, label, operation, journal):
    start = clock.check(label + "-start")
    value = operation()
    try:
        end = clock.check(label + "-end", since=start["sample"])
    except ClockIntegrityError as error:
        journal.emit("clock_failed_after_operation", label=label, clock_failure=error.receipt,
                     returned_operation_value=core.jsonsafe(value))
        raise
    return value, {"start": start, "end": end,
                   "elapsed_s": end["interval"]["perf_elapsed_ns"] / 1e9}


class NoInference:
    def __init__(self, metadata):
        self.metadata = metadata
        self.document_calls = self.query_calls = 0

    def embed_documents(self, texts):
        self.document_calls += 1
        raise AssertionError("Fresh reopening requested document inference")

    def embed_query(self, text):
        self.query_calls += 1
        raise AssertionError("Fresh reopening requested query inference")


def reopen_child(request_path, owner_pid):
    guard = core.BlockModelImports(strict=True)
    sys.meta_path.insert(0, guard)
    core.block_network()
    result = {"complete": False, "passed": False, "pid": os.getpid(), "parent_pid": os.getppid(),
              "fresh_process": True, "layer": "durable_index_source_extraction_refresh", "native_imports": []}
    request = core.read(request_path)
    output = core.resolve(request["receipt"])
    index = embedder = None
    clock = ClockIntegrity()
    try:
        core.require(os.getppid() == owner_pid and owner_pid > 1, "Fresh reopen parent ownership differs")
        require_request = request["reference_matrix"]
        core.require(core.sha(core.resolve(require_request["path"])) == require_request["sha256"], "Fresh reopen reference matrix changed")
        expected = np.load(core.resolve(require_request["path"]), allow_pickle=False)
        core.require(expected.dtype == np.float32 and expected.ndim == 2, "Fresh reopen original reference must be float32 matrix")
        before = clock.check("fresh-reopen-start")
        (raw, snippets), extraction = timed(clock, "fresh-reopen-source-extraction", lambda: core.capture_source(
            core.resolve(request["repo"]), request["source_root"], request["source_exclusions"]), _NullJournal())
        core.require({path: core.digest(data) for path, data in raw.items()} == request["source_files"], "Fresh reopen source changed")
        core.require({key: value["document_sha256"] for key, value in core.inventories(snippets).items()} == request["documents"], "Fresh reopen windows changed")
        from semantic_search.store import SQLiteIndex
        core.require(core.resolve(request["database"]).is_file(), "Fresh reopen requires an existing database")
        index, construction = timed(clock, "fresh-reopen-store-construction", lambda: SQLiteIndex(core.resolve(request["database"])), _NullJournal())
        with sqlite3.connect(index.db_path) as connection:
            cached = {row[0]: np.frombuffer(row[1], dtype="<f8").astype(np.float32)
                      for row in connection.execute("SELECT document_hash,vector FROM embeddings")}
        core.require(expected.shape == (len(snippets), request["embedding_metadata"]["dimensions"]), "Fresh reference dimensions differ")
        for snippet, vector in zip(snippets, expected, strict=True):
            document_hash = core.digest(core.document(snippet).encode())
            core.require(document_hash in cached and np.array_equal(cached[document_hash], vector), "Fresh cached original value differs before refresh")
        before_inspection = core.inspect_database(index, snippets, cached, request["embedding_metadata"])
        embedder = NoInference(request["embedding_metadata"])
        (matrix, stats), refresh = timed(clock, "fresh-reopen-refresh", lambda: index.refresh(snippets, embedder), _NullJournal())
        core.require(matrix.dtype == np.float64 and np.array_equal(matrix, expected.astype(np.float64)), "Fresh reopen cached values differ")
        core.require(stats["indexed_windows"] == stats["deleted_windows"] == 0 and stats["reused_windows"] == len(snippets)
                     and embedder.document_calls == embedder.query_calls == 0, "Fresh reopen inference/reuse counter differs")
        # The independent final checker additionally compares every historical
        # cached vector to real native ledgers/matrices through the DB snapshots.
        inspected = core.inspect_database(index, snippets, cached, request["embedding_metadata"])
        end = clock.check("fresh-reopen-complete", since=before["sample"])
        result.update(complete=True, passed=True, stats=stats, database=inspected,
                      source_files=request["source_files"], documents=request["documents"], before_refresh_database=before_inspection,
                      reference_matrix=require_request, returned_matrix_raw_sha256=core.digest(matrix.tobytes()),
                      extraction_timing=extraction, construction_timing=construction, refresh_timing=refresh,
                      overall_clock=end, cached_values_exact=True, memory=core.memory())
    except BaseException as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        if isinstance(error, ClockIntegrityError):
            result["clock_failure"] = error.receipt
    finally:
        if index is not None:
            index.close()
        result.update(document_inference_calls=embedder.document_calls if embedder else 0,
                      query_inference_calls=embedder.query_calls if embedder else 0,
                      blocked_import_attempts=guard.blocked,
                      native_imports=[name for name in sys.modules if name == "mlx" or name.startswith(("mlx.", "mlx_embeddings", "mlx_vlm", "mlx_lm", "transformers", "semantic_search.embeddings", "semantic_search.model", "semantic_search.qwen"))],
                      network_forbidden=True, owned_parent_matches=os.getppid() == owner_pid)
        result["passed"] = result["passed"] and not result["native_imports"] and not guard.blocked and result["owned_parent_matches"]
        core.write_new(output, core.public(result))
    return 0 if result["passed"] else 1


class _NullJournal:
    def emit(self, *args, **kwargs):
        return None


def run_reopen(stage, plan, repo, database, raw, snippets, reference, output, work, clock, journal):
    request = {"repo": core.artifact(repo), "database": core.artifact(database),
               "source_root": plan["manifest"]["source_root"], "source_exclusions": plan["manifest"]["source_exclusions"],
               "embedding_metadata": plan["embedding_metadata"], "source_files": {path: core.digest(data) for path, data in raw.items()},
               "documents": {key: value["document_sha256"] for key, value in core.inventories(snippets).items()},
               "reference_matrix": reference, "receipt": core.artifact(output / "stages" / (stage + "-fresh-process.json"))}
    path = work / (stage + "-request.json")
    core.write_new(path, request)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--_reopen", str(path), "--owner-pid", str(os.getpid())]
    started = clock.check(stage + "-child-launch")
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=(work / (stage + "-stderr.log")).open("xb"))
    try:
        code = process.wait(timeout=plan["fresh_reopen_timeout_s"])
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        raise
    ended = clock.check(stage + "-child-exit", since=started["sample"])
    receipt = core.read(core.resolve(request["receipt"]))
    core.require(code == 0 and receipt.get("passed") is True and receipt.get("parent_pid") == os.getpid()
                 and receipt.get("pid") == process.pid, "Fresh CPU reopen failed")
    journal.emit("fresh_process_reopen_completed", stage=stage, command=command,
                 command_sha256=core.digest(json.dumps(command).encode()), pid=process.pid, exit_code=code,
                 receipt_sha256=core.sha(core.resolve(request["receipt"])), start=started, end=ended)
    return receipt["stats"], {"start": started, "end": ended, "elapsed_s": ended["interval"]["perf_elapsed_ns"] / 1e9,
                              "layer": "owned fresh CPU process including startup, extraction, store construction and durable refresh"}


def mutation(stage, manifest, repo):
    spec = manifest["mutation"]
    original = repo / spec["path"]
    renamed = repo / spec["renamed_path"]
    if stage == "saved_edit":
        core.require(core.sha(original) == spec["original_file_sha256"], "Edit predecessor bytes differ")
        replacement = gzip.decompress((PREPARATION / "mutation-sources/locmem.saved-edit.py.gz").read_bytes())
        core.require(core.digest(replacement) == spec["edited_file_sha256"], "Frozen edited source differs")
        original.write_bytes(replacement)
    elif stage == "delete_file":
        core.require(core.sha(original) == spec["edited_file_sha256"], "Delete predecessor bytes differ")
        original.unlink()
    elif stage == "restore_edited_file":
        core.require(not original.exists(), "Restore predecessor must be absent")
        original.write_bytes(gzip.decompress((PREPARATION / "mutation-sources/locmem.saved-edit.py.gz").read_bytes()))
    elif stage == "rename_edited_file":
        core.require(core.sha(original) == spec["edited_file_sha256"] and not renamed.exists(), "Rename predecessor differs")
        git(repo, "mv", spec["path"], spec["renamed_path"])
    elif stage == "rename_back":
        core.require(core.sha(renamed) == spec["edited_file_sha256"] and not original.exists(), "Rename-back predecessor differs")
        git(repo, "mv", spec["renamed_path"], spec["path"])
    elif stage == "restore_pristine_file":
        core.require(core.sha(original) == spec["edited_file_sha256"], "Pristine restore predecessor differs")
        original.write_bytes(gzip.decompress((PREPARATION / "target-sources" / (spec["path"] + ".gz")).read_bytes()))
    else:
        raise ValueError("Unknown copy-only mutation stage: " + stage)


def record_stage(name, expected, snapshots, ordinal, plan, repo, index, embedder, stats, timing, output, work):
    raw, snippets = core.capture_source(repo, plan["manifest"]["source_root"], plan["manifest"]["source_exclusions"])
    inventory = core.inventories(snippets)
    file_snapshots = core.expected_source_snapshots(plan["manifest"], core.read(PREPARATION / "source-inventory.json"))
    core.require({path: core.digest(data) for path, data in raw.items()} == file_snapshots[ordinal], "Exact saved file bytes differ at stage")
    actual = {key: value["document_sha256"] for key, value in inventory.items()}
    inspection = core.inspect_database(index, snippets, embedder.cache_vectors, plan["embedding_metadata"])
    core.verify_stage(expected, stats, actual, snapshots[ordinal], inspection["cached_documents"])
    matrix = np.stack([embedder.cache_vectors[actual[snippet.id]] for snippet in snippets])
    reference = matrix_file(work / "snapshots" / (name + "-original-values.npy"), matrix)
    database = sqlite_snapshot(index.db_path, work / "snapshots" / (name + ".sqlite3"))
    receipt = {"stage": name, "ordinal": ordinal, "expected": expected, "stats": stats, "timing": timing,
               "source_files": {path: core.digest(data) for path, data in raw.items()}, "windows": inventory,
               "git_revision": git(repo, "rev-parse", "HEAD").decode().strip(),
               "git_status": git(repo, "status", "--porcelain=v1").decode(),
               "database_inspection": inspection, "database_snapshot": database,
               "current_raw_matrix": reference, "index_status": index.status(),
               "disk_live": core.disk(index.db_path), "memory": core.memory(embedder._mx),
               "native_counts": dict(embedder.counts), "checks_passed": True}
    core.write_new(output / "stages" / (name + ".json"), core.public(receipt))
    return raw, snippets, matrix, reference


class NoReranker:
    def score(self, *args, **kwargs):
        raise AssertionError("Reranking forbidden by the frozen profile")


def searches(plan, repo, backend, index, embedder, snippets, raw_matrix, output, work, clock, journal):
    from semantic_search.context import encoding
    from semantic_search.persistent_retrieval import PersistentDenseRetriever, PersistentLexicalRetriever
    from semantic_search.retrieval import HybridRetriever
    queries = plan["manifest"]["queries"]
    by_id = {row["id"]: row for row in queries}
    vectors = np.stack([embedder.embed_query(row["query"]) for row in queries])
    query_descriptor = matrix_file(work / "query-vectors.npy", vectors)
    core.write_new(output / "query-vectors.json", {"matrix": query_descriptor,
        "rows": [{"id": row["id"], "query_sha256": core.digest(row["query"].encode()),
                  "vector_sha256": core.digest(vector.tobytes())} for row, vector in zip(queries, vectors, strict=True)]})
    clock.check("raw-query-vectors-complete")
    by_vector = {row["id"]: vector for row, vector in zip(queries, vectors, strict=True)}
    lexical, dense = PersistentLexicalRetriever(index), PersistentDenseRetriever(index)
    hybrid = HybridRetriever(lexical, dense, rrf_k=60, fetch_depth=50)
    config = plan["manifest"]["retrieval"]
    raw_functions = {"bm25": lambda q: lexical.search(q["query"], config["raw_top_k"]),
                     "dense": lambda q: dense.search(by_vector[q["id"]], config["raw_top_k"]),
                     "hybrid": lambda q: hybrid.search(q["query"], by_vector[q["id"]], config["raw_top_k"])}
    token_encoding = None
    expected_source_manifest = backend._manifest
    summary, raw_candidates = {}, {}
    sample_journal = core.Journal(work / "samples.jsonl")
    try:
        for workload in ("store", "api"):
            verify_environment(plan)
            summary[workload] = {}
            collected = {provider: [] for provider in config["providers"]}
            first = {}
            for number, item in enumerate(plan["schedules"][workload]):
                core.require(backend._snapshot()[0] == expected_source_manifest, "Execution source changed before timed query")
                query, provider = by_id[item["query_id"]], item["provider"]
                before = dict(embedder.counts)
                if workload == "store":
                    value, measurement = timed(clock, f"{workload}-{number}", lambda q=query, p=provider: raw_functions[p](q), journal)
                    actual = [{"id": snippet.id, "score": score} for snippet, score in value]
                    if item["phase"] == "measured":
                        raw_candidates[(query["id"], provider)] = value
                    evidence = {"candidates": actual}
                else:
                    value, measurement = timed(clock, f"{workload}-{number}", lambda q=query, p=provider: backend.search(
                        q["query"], retrieval=p, rerank=False, candidates=config["quality_candidates"],
                        max_results=config["quality_max_results"], source_budget=config["quality_source_budget"],
                        token_budget=config["quality_token_budget"], include_dependencies=False), journal)
                    if token_encoding is None:
                        token_encoding = encoding()
                    proof = core.verify_response(value, repo, query["query"], config, token_encoding)
                    core.require(value["indexstats"]["source_manifest"] == expected_source_manifest,
                                 "Execution source changed during API query")
                    evidence = {"response": value, "source_and_caps": proof}
                expected_queries = int(workload == "api" and provider != "bm25")
                core.require(backend._snapshot()[0] == expected_source_manifest, "Execution source changed during timed query")
                delta = {key: embedder.counts[key] - before[key] for key in before}
                core.require(delta["document_inputs"] == 0 and delta["query_inputs"] == expected_queries
                             and delta["attempted_forwards"] == delta["completed_forwards"] == expected_queries,
                             "Search workload inference counters differ")
                sample_journal.emit("search_sample", workload=workload, schedule_number=number,
                                    identity=item, measurement=measurement, native_delta=delta, **evidence)
                if item["phase"] == "measured":
                    collected[provider].append(measurement["elapsed_s"])
                elif item["phase"] == "first":
                    first[provider] = measurement["elapsed_s"]
            for provider, values in collected.items():
                core.require(len(values) == len(queries) * config["measured_sweeps_per_provider"], "Warm sample inventory incomplete")
                summary[workload][provider] = {"warm": core.distribution(values), "first_use_s": first[provider]}
            core.write_new(output / (workload + "-latency.json"), summary[workload])
    finally:
        sample_journal.close()
        core.compress_journal(work / "samples.jsonl", output / "samples.jsonl.gz")
    verify_environment(plan)
    numeric, numeric_timing = timed(clock, "independent-numpy-verification", lambda: core.verify_numeric(
        index, snippets, raw_matrix, vectors, config["raw_top_k"], plan["manifest"]["numpy_validation"]["max_cosine_absolute_error"]), journal)
    numeric["verification_timing_excluded_from_search_samples"] = numeric_timing
    core.write_new(output / "numerical-check.json", numeric)
    core.require(numeric["checks_passed"], "SQLite/independent NumPy parity failed; no tie relaxation")
    quality = []
    for query in queries:
        target = query["target_groups"][0][0]
        for provider in config["providers"]:
            core.require(backend._snapshot()[0] == expected_source_manifest, "Execution source changed before quality query")
            before = dict(embedder.counts)
            response = backend.search(query["query"], retrieval=provider, rerank=False,
                                      candidates=config["quality_candidates"], max_results=config["quality_max_results"],
                                      source_budget=config["quality_source_budget"], token_budget=config["quality_token_budget"],
                                      include_dependencies=False)
            proof = core.verify_response(response, repo, query["query"], config, token_encoding)
            core.require(response["indexstats"]["source_manifest"] == expected_source_manifest,
                         "Execution source changed during quality query")
            candidates = raw_candidates[(query["id"], provider)]
            delta = {key: embedder.counts[key] - before[key] for key in before}
            expected_queries = int(provider != "bm25")
            core.require(delta["document_inputs"] == 0 and delta["query_inputs"] == expected_queries
                         and delta["attempted_forwards"] == delta["completed_forwards"] == expected_queries,
                         "Quality query inference counters differ")
            quality.append({"query_id": query["id"], "provider": provider,
                            "candidate_top_ten_complete_span": core.coverage([snippet for snippet, _ in candidates], target),
                            "delivered_five_complete_span": core.coverage(response["results"], target),
                            "raw_candidate_ids": [snippet.id for snippet, _ in candidates], "response": response,
                            "source_and_caps": proof, "native_delta": delta})
            clock.check("quality-" + query["id"] + "-" + provider)
    core.write_new(output / "quality.json", core.public({"kind": "eight_source_authored_examples_not_general_benchmark", "rows": quality}))
    return summary


def worker(output, owner_pid):
    plan = core.read(output / "frozen-plan.json")
    work = core.resolve(plan["work"])
    result = {"complete": False, "checks_passed": False, "completed_stages": [],
              "frozen_plan_sha256": core.sha(output / "frozen-plan.json"), "pid": os.getpid(),
              "parent_pid": os.getppid(), "started": core.stamp()}
    journal = core.Journal(work / "native-events.jsonl")
    backend = embedder = index = mx = None
    watcher_stop = threading.Event()
    clock = ClockIntegrity()
    result["clock_baseline"] = clock.baseline
    try:
        with core.Signals() as signals:
            result["signals"] = signals.receipt
            core.require(os.getppid() == owner_pid and owner_pid > 1, "Native worker ownership differs")
            deadline = time.monotonic() + 5
            proof_path = work / "owned-process.json"
            while not proof_path.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            proof = core.read(proof_path)
            core.require(proof["pid"] == os.getpid() and proof["owner_pid"] == owner_pid and os.getpgid(0) == os.getpid(), "Native worker owned session proof differs")
            result["owned_process"] = proof

            def watch_parent():
                while not watcher_stop.wait(.5):
                    if os.getppid() != owner_pid:
                        os.kill(os.getpid(), signal.SIGTERM)
                        return
            watcher = threading.Thread(target=watch_parent, daemon=True)
            watcher.start()
            core.block_network()
            result["coding_guard"] = coding_guard()
            core.require(coding_completion(core.resolve(plan["coding_precondition"]["audit"])) == plan["coding_precondition"],
                         "Coding final evidence changed after freeze")
            verify_environment(plan)
            core.require(sys.platform == "darwin" and plan["runtime"]["machine"] == "arm64", "Native profile requires the frozen Apple Silicon Mac")
            result["power_start"] = core.power()
            core.require(result["power_start"]["ac_power"] is True, "AC power required for timing qualification")
            result["memory_before_model"] = core.memory()
            repo = work / "corpus"
            git(ROOT, "clone", "--no-hardlinks", str(core.resolve(plan["manifest"]["corpus"])), str(repo))
            git(repo, "checkout", "--detach", plan["manifest"]["revision"])
            core.require(not git(repo, "status", "--porcelain=v1"), "Fresh execution clone must be clean")
            database = work / "index.sqlite3"
            core.require(not database.exists(), "First index database must be absent")
            from semantic_search.store import SQLiteIndex
            index, schema = timed(clock, "schema-construction", lambda: SQLiteIndex(database), journal)
            result["schema_construction"] = schema
            with sqlite3.connect(database) as connection:
                empty_counts = {table: connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                                for table in ("embeddings", "snippets", "active_vectors", "snippets_fts")}
                core.require(all(value == 0 for value in empty_counts.values()), "First index must have zero prior source/vectors")
            result["initial_empty_database"] = {"database_was_absent": True, "table_counts": empty_counts,
                                               "snapshot": sqlite_snapshot(database, work / "snapshots/initial-empty.sqlite3")}
            from semantic_search.embeddings import QwenEmbedder
            from semantic_search.backend import SearchBackend
            Recorded = core.recording_embedder_class(QwenEmbedder)
            embedder = Recorded(model_path=core.resolve(plan["checkpoint"]["path"]),
                                batch_size=plan["manifest"]["embedding"]["batch_size"], progress=True,
                                journal=journal, work=work)
            embedder.allowed_native_sources = plan["native_source_sha256"]
            embedder.bind_queries(plan["manifest"]["queries"])
            core.require(embedder.metadata == plan["embedding_metadata"], "Actual embedder metadata differs from freeze")
            backend = SearchBackend(repo, plan["manifest"]["source_root"], index=index, embedder=embedder,
                                    source_exclusions=plan["manifest"]["source_exclusions"], h2o_client=NoReranker())
            windows = json.loads(gzip.decompress((PREPARATION / "window-inventory.json.gz").read_bytes()))
            edited_source = gzip.decompress((PREPARATION / "mutation-sources/locmem.saved-edit.py.gz").read_bytes()).decode()
            snapshots = core.expected_snapshots(plan["manifest"], windows, edited_source)
            file_snapshots = core.expected_source_snapshots(plan["manifest"], core.read(PREPARATION / "source-inventory.json"))
            reference = raw = snippets = matrix = None
            for ordinal, expected in enumerate(plan["manifest"]["persistence_sequence"]):
                name = expected["stage"]
                verify_environment(plan)
                core.require(git(repo, "rev-parse", "HEAD").decode().strip() == plan["manifest"]["revision"], "Execution Git revision drift")
                core.require(core.power()["ac_power"] is True, "Power changed before profile stage")
                previous_raw, _ = core.capture_source(repo, plan["manifest"]["source_root"], plan["manifest"]["source_exclusions"])
                core.require({path: core.digest(data) for path, data in previous_raw.items()} == file_snapshots[max(0, ordinal - 1)],
                             "Unplanned saved source changes before stage")
                if name in ("fresh_process_reopen", "final_fresh_process_reopen"):
                    stats, measured = run_reopen(name, plan, repo, database, raw, snippets, reference, output, work, clock, journal)
                else:
                    if name != "initial":
                        mutation(name, plan["manifest"], repo)
                    raw, snippets = core.capture_source(repo, plan["manifest"]["source_root"], plan["manifest"]["source_exclusions"])
                    core.require({path: core.digest(data) for path, data in raw.items()} == file_snapshots[ordinal],
                                 "Exact saved source files differ before refresh")
                    actual = {key: value["document_sha256"] for key, value in core.inventories(snippets).items()}
                    core.require(actual == snapshots[ordinal], "Saved source drift before refresh")
                    embedder.bind_snippets(snippets)
                    stats, measured = timed(clock, name + "-backend-refresh", backend.refresh, journal)
                raw, snippets, matrix, reference = record_stage(name, expected, snapshots, ordinal, plan, repo,
                    index, embedder, stats, measured, output, work)
                result["completed_stages"].append(name)
                core.write_atomic(output / "worker-progress.json", core.public(result))
                journal.emit("stage_completed", stage=name, native_counts=dict(embedder.counts), refresh_stats=stats)
                print(json.dumps({"completed_stage": name, "indexed_windows": stats["indexed_windows"],
                                  "reused_windows": stats["reused_windows"]}), flush=True)
                if name == "fresh_process_reopen":
                    result["latency"] = searches(plan, repo, backend, index, embedder, snippets, matrix, output, work, clock, journal)
            verify_environment(plan)
            core.require(not git(repo, "status", "--porcelain=v1"), "Execution copy not restored pristine")
            queries = len(plan["manifest"]["queries"])
            api_native = sum(item["provider"] != "bm25" for item in plan["schedules"]["api"])
            expected_query_inputs = queries + api_native + queries * 2
            expected_documents = sum(stage["new_unique_documents"] for stage in plan["manifest"]["persistence_sequence"])
            expected_forwards = sum(math.ceil(stage["new_unique_documents"] / embedder.batch_size)
                                    for stage in plan["manifest"]["persistence_sequence"]) + expected_query_inputs
            expected_counts = {"document_inputs": expected_documents, "query_inputs": expected_query_inputs,
                               "attempted_forwards": expected_forwards, "completed_forwards": expected_forwards}
            core.require(embedder.counts == expected_counts, "Whole profile real inference counts differ")
            result.update(native_expected_counts=expected_counts, native_counts=dict(embedder.counts),
                          embedding_operations=embedder.operations, model_initialization=embedder.initialization,
                          memory_before_cleanup=core.memory(embedder._mx), complete=True)
            result["clock_final"] = clock.check("profile-complete")
            result["power_end"] = core.power()
            result["sleep_check"] = core.sleep_events(result["started"]["utc"])
            core.require(result["power_end"]["ac_power"] is True and not result["sleep_check"]["events"], "Power/sleep timing qualification failed")
            result["checks_passed"] = True
    except BaseException as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        if isinstance(error, ClockIntegrityError):
            result["clock_failure"] = error.receipt
        journal.emit("profile_failed", error=result["error"], completed_stages=result["completed_stages"])
    finally:
        watcher_stop.set()
        with core.Signals() as cleanup_signals:
            result["cleanup_signals"] = cleanup_signals.receipt
            with cleanup_signals.defer(raise_pending=False):
                try:
                    mx = embedder._mx if embedder is not None else None
                    if embedder is not None:
                        result["native_counts_final"] = dict(embedder.counts)
                    if backend is not None:
                        backend.close()
                    elif embedder is not None:
                        embedder.close()
                    if index is not None:
                        index.close()
                    result["cleanup"] = {"model_released": embedder is None or embedder._model is None,
                                         "index_closed": True, "memory_after_model_release": core.memory(mx)}
                    if index is not None:
                        result["disk_closed"] = core.disk(index.db_path)
                except BaseException as error:
                    result["cleanup_error"] = {"type": type(error).__name__, "message": str(error)}
                    result["checks_passed"] = False
                journal.emit("profile_cleanup_completed", cleanup=result.get("cleanup"), cleanup_error=result.get("cleanup_error"))
                journal.close()
                core.compress_journal(work / "native-events.jsonl", output / "native-events.jsonl.gz")
                result["raw_journals"] = {name: descriptor(work / name) for name in ("native-events.jsonl", "samples.jsonl")
                                          if (work / name).is_file()}
                result["artifact_sha256"] = {core.artifact(path): core.sha(path) for path in output.rglob("*")
                                             if path.is_file() and path.name not in ("worker-progress.json", "worker-result.json", "parent-result.json")}
        result["checks_passed"] = (result["checks_passed"] and result.get("cleanup", {}).get("model_released") is True
                                   and not cleanup_signals.receipt["received"])
        result["finished"] = core.stamp()
        # Restore the default OS signal policy before the final small publication:
        # any late signal makes the actual worker exit nonzero, so the parent can
        # never qualify a late interrupted write as a successful owned attempt.
        core.write_new(output / "worker-result.json", core.public(result))
    return 0 if result["checks_passed"] else 1


def run(output):
    plan_path = output / "frozen-plan.json"
    plan = core.read(plan_path)
    core.block_network()
    committed([plan_path])
    core.require(core.artifact(output) == plan["output"], "Frozen output path differs")
    verify_environment(plan, checkpoint=False)
    guard = coding_guard()
    core.require(coding_completion(core.resolve(plan["coding_precondition"]["audit"])) == plan["coding_precondition"],
                 "Coding final evidence changed after freeze")
    work = core.resolve(plan["work"])
    core.require(not work.exists(), "No profile retry/resume or existing work path")
    core.require(not any((output / name).exists() for name in ("run-start.json", "parent-result.json", "worker-result.json",
                 "worker-progress.json", "parent-progress.json", "stages", "native-events.jsonl.gz", "samples.jsonl.gz")),
                 "No attempt overwrite or resume")
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--_worker", str(output), "--owner-pid", str(os.getpid())]
    result = {"complete": False, "checks_passed": False, "frozen_plan_sha256": core.sha(plan_path),
              "started": core.stamp(), "command": command, "model_calls_in_parent": 0}
    process, claimed = None, False
    with core.Signals() as signals:
        result["signals"] = signals.receipt
        try:
            with signals.defer():
                core.write_new(output / "run-start.json", {"started": core.stamp(), "plan_sha256": core.sha(plan_path),
                                                          "coding_guard": guard, "model_calls_in_parent": 0})
                claimed = True
                result["attempt_claimed"] = True
                work.mkdir(parents=True, exist_ok=False, mode=0o700)
                result["work_reserved"] = True
                (work / "snapshots").mkdir()
                (output / "stages").mkdir(exist_ok=False)
                with (work / "native-worker.log").open("xb") as stream:
                    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                ownership = {"pid": process.pid, "owned_group": process.pid, "owner_pid": os.getpid(),
                             "start_new_session": True, "command": command,
                             "command_sha256": core.digest(json.dumps(command).encode()), "observed": core.stamp()}
                result["ownership"] = ownership
                core.write_new(work / "owned-process.json", ownership)
                core.write_atomic(output / "parent-progress.json", core.public(result))
            code = process.wait(timeout=plan["worker_timeout_s"])
            result["exit_code"] = code
            result["worker_leader_exit"] = core.stamp()
            result["complete"] = True
        except BaseException as error:
            result["error"] = {"type": type(error).__name__, "message": str(error)}
        finally:
            with signals.defer(raise_pending=False):
                result["cleanup"] = stop_owned(process) if process is not None else {"launched": False, "owned_group_stopped": True}
                worker_path = output / "worker-result.json"
                if worker_path.exists():
                    try:
                        outcome = core.read(worker_path)
                        result["worker_result_sha256"] = core.sha(worker_path)
                        result["worker_checks_passed"] = outcome.get("checks_passed")
                    except (ValueError, OSError) as error:
                        result["worker_evidence_error"] = {"type": type(error).__name__, "message": str(error)}
                cleanup = result["cleanup"]
                result["checks_passed"] = (result["complete"] and result.get("exit_code") == 0
                    and result.get("worker_checks_passed") is True and cleanup.get("owned_group_stopped") is True
                    and not cleanup.get("forced_kill") and not cleanup.get("errors") and not signals.receipt["received"])
                result["finished"] = core.stamp()
    if claimed:
        core.write_new(output / "parent-result.json", core.public(result))
    print(json.dumps({"checks_passed": result["checks_passed"], "exit_code": result.get("exit_code"),
                      "owned_group_stopped": result["cleanup"].get("owned_group_stopped")}), flush=True)
    return 0 if result["checks_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--_worker", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--_reopen", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--checkpoint", type=Path, default=ROOT / ".cache/models/qwen3-embedding-0.6b")
    parser.add_argument("--owner-pid", type=int)
    parser.add_argument("--coding-audit", type=Path)
    args = parser.parse_args()
    if args._reopen is not None:
        return reopen_child(args._reopen, args.owner_pid)
    if args._worker is not None:
        return worker(args._worker.absolute(), args.owner_pid)
    core.require(args.output is not None, "--output required")
    if args.freeze:
        core.require(args.work is not None, "--work required for freeze")
        core.require(args.coding_audit is not None, "--coding-audit final full18 certificate required for freeze")
        freeze(args.output.absolute(), args.work.absolute(), args.checkpoint.absolute(), args.coding_audit.absolute())
        return 0
    return run(args.output.absolute())


if __name__ == "__main__":
    raise SystemExit(main())
