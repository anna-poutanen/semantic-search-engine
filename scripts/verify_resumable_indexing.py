#!/usr/bin/env python3
"""Verify durable Qwen batches across SIGKILL and fresh-process resumption.

This serial qualification uses an existing local model and original public source
windows. It is a one-device durability/vector-parity check, not a retrieval or
throughput benchmark. Private source fixtures, databases and arrays stay in
--work-dir; --output-dir receives only source-free receipts and worker logs.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import sqlite3
import subprocess
import sys
import time

import numpy as np

EVENT_PREFIX = "RESUME_VERIFY "
SAMPLE_SIZE = 12


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def git_state(repo):
    def run(*arguments):
        result = subprocess.run(["git", "-C", str(repo), *arguments],
                                capture_output=True, check=True, timeout=10)
        return result.stdout

    revision = run("rev-parse", "HEAD").decode("ascii").strip()
    status = run("status", "--porcelain=v1", "--untracked-files=normal")
    return {"head": revision, "clean": not bool(status),
            "status_sha256": sha256(status), "status_entries": len(status.splitlines())}


def source_snapshot(repo, source_root):
    from semantic_search.corpus import _source_files

    files = [{"path": path.relative_to(repo).as_posix(),
              "sha256": sha256(path.read_bytes()), "bytes": path.stat().st_size}
             for path in _source_files(repo, (repo / source_root).resolve())]
    return {"git": git_state(repo), "files": files,
            "saved_source_manifest_sha256": sha256(json_bytes(files))}


def physical_check(repo, snippet):
    from semantic_search.languages import decode_source
    from semantic_search.source import physical_lines

    path = (repo / snippet.path).resolve()
    require(path.is_file() and path.is_relative_to(repo), "Sample left the source repository")
    lines = physical_lines(decode_source(snippet.path, path.read_bytes()))
    actual = "".join(lines[snippet.start_line - 1:snippet.end_line])
    require(1 <= snippet.start_line <= snippet.end_line <= len(lines)
            and actual == snippet.text and sha256(actual.encode()) == snippet.content_hash,
            "Selected physical source window changed")


def active_snapshot(db_path):
    """Read all active tables through a separate, bounded read transaction."""
    orders = {"snippets": "position", "snippets_fts": "id",
              "active_vectors": "snippet_id", "metadata": "key"}
    with closing(sqlite3.connect(db_path, timeout=10)) as connection:
        connection.execute("BEGIN")
        tables = {}
        for table, order in orders.items():
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
            encoded = [[{"blob_sha256": sha256(value), "bytes": len(value)}
                        if isinstance(value, bytes) else value for value in row] for row in rows]
            tables[table] = {"rows": len(rows), "sha256": sha256(json_bytes(encoded))}
        cache_count = connection.execute("SELECT count(*) FROM embeddings").fetchone()[0]
    return {"active_tables": tables, "cached_documents": cache_count}


def emit(event, **values):
    print(EVENT_PREFIX + json.dumps({"event": event, "pid": os.getpid(), **values},
                                    sort_keys=True, allow_nan=False), flush=True)


def worker(args):
    from semantic_search.corpus import Snippet
    from semantic_search.embeddings import MAX_TOKENS, QwenEmbedder
    from semantic_search.index import document_text
    from semantic_search.store import SQLiteIndex

    snippets = [Snippet(**row) for row in json.loads(args.manifest.read_text())["snippets"]]
    documents = [document_text(item) for item in snippets]
    document_positions = {sha256(text.encode()): index for index, text in enumerate(documents)}
    model = QwenEmbedder(model_path=args.model_path, progress=False)
    metadata = model.metadata
    fingerprint = sha256(json_bytes(metadata))
    started = time.perf_counter()
    store = SQLiteIndex(args.database)
    requests, forwards, checkpoints = [], [], []
    phase = "seed" if args.worker_mode in {"seed", "baseline"} else "full"
    try:
        model._ensure_loaded()
        token_ids = model._tokenizer(documents, padding=False, truncation=True,
                                    max_length=MAX_TOKENS, add_special_tokens=True)["input_ids"]
        token_positions = {sha256(json_bytes(ids)): index for index, ids in enumerate(token_ids)}
        require(len(token_positions) == len(snippets), "Selected tokenized inputs are not distinct")
        native_forward, embed_documents = model._embed_batch, model.embed_documents
        request_positions = []

        def observed_forward(rows):
            hashes = [sha256(json_bytes(ids)) for ids in rows]
            require(all(value in token_positions for value in hashes), "Unexpected native token input")
            before = time.perf_counter()
            vectors = native_forward(rows)
            row = {"phase": phase, "sample_positions": [token_positions[value] for value in hashes],
                   "token_counts": [len(ids) for ids in rows], "token_ids_sha256": hashes,
                   "elapsed_seconds": time.perf_counter() - before}
            forwards.append(row)
            emit("NATIVE_BATCH", **row)
            return vectors

        def observed_documents(texts, *, batch_callback=None, progress_callback=None):
            nonlocal request_positions
            request_positions = [document_positions[sha256(text.encode())] for text in texts]
            requests.append({"phase": phase, "sample_positions": request_positions.copy()})

            def observed_checkpoint(indices, vectors):
                require(batch_callback is not None, "Store did not request durable checkpoints")
                batch_callback(indices, vectors)
                row = {"phase": phase, "request_indices": indices,
                       "sample_positions": [request_positions[index] for index in indices],
                       "float32_sha256": sha256(vectors.astype("<f4").tobytes())}
                checkpoints.append(row)
                emit("DURABLE_BATCH", **row)

            return embed_documents(texts, batch_callback=observed_checkpoint,
                                   progress_callback=progress_callback)

        model._embed_batch = observed_forward
        model.embed_documents = observed_documents

        def progress(event):
            emit("PROGRESS", **event)
            if args.worker_mode == "pause" and event["phase"] == "embed" and event["completed"]:
                emit("CHECKPOINT", completed=event["completed"], total=event["total"],
                     snapshot=active_snapshot(args.database),
                     embedding_metadata=metadata, model_fingerprint=fingerprint)
                # The parent inspects the committed WAL through another connection,
                # then deliberately SIGKILLs this process before any next forward.
                sys.stdin.buffer.read(1)
                raise RuntimeError("Paused worker unexpectedly received input or EOF")

        seed_stats = None
        if args.worker_mode in {"seed", "baseline"}:
            _, seed_stats = store.refresh(snippets[:1], model, progress=progress)
        phase = "full"
        if args.worker_mode == "seed":
            stats = seed_stats
        else:
            matrix, stats = store.refresh(snippets, model, progress=progress)
            np.save(args.database.with_suffix(".raw.npy"), matrix, allow_pickle=False)
            with closing(sqlite3.connect(args.database, timeout=10)) as connection:
                rows = connection.execute(
                    "SELECT v.dimensions,v.vector FROM active_vectors v "
                    "JOIN snippets s ON s.id=v.snippet_id ORDER BY s.position").fetchall()
            active = np.stack([np.frombuffer(blob, dtype="<f4").reshape(dimensions)
                               for dimensions, blob in rows])
            np.save(args.database.with_suffix(".active.npy"), active, allow_pickle=False)
        require(model.metadata == metadata, "Model metadata changed during execution")
        emit("COMPLETE", mode=args.worker_mode, stats=stats, seed_stats=seed_stats,
             embedding_metadata=metadata, model_fingerprint=fingerprint,
             requests=requests, native_batches=forwards, durable_batches=checkpoints,
             elapsed_seconds=time.perf_counter() - started,
             offline_environment={name: os.environ.get(name) for name in
                                  ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")},
             snapshot=active_snapshot(args.database))
    finally:
        model.close()
        store.close()


class WorkerProcess:
    def __init__(self, command, log, timeout):
        environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                       "HF_DATASETS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"}
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env=environment,
                                        start_new_session=True, bufsize=0)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.log = log.open("wb")
        self.buffer = b""
        self.events = []
        self.deadline = time.monotonic() + timeout
        self.eof = False

    def pump(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Native worker exceeded the qualification deadline")
        for key, _ in self.selector.select(min(remaining, 1)):
            chunk = os.read(key.fd, 65536)
            if not chunk:
                self.eof = True
                self.selector.unregister(key.fileobj)
                continue
            self.log.write(chunk)
            self.log.flush()
            self.buffer += chunk
            while b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                line = raw.decode("utf-8", "replace")
                if line.startswith(EVENT_PREFIX):
                    self.events.append(json.loads(line[len(EVENT_PREFIX):]))

    def wait_event(self, name):
        while True:
            for event in self.events:
                if event["event"] == name:
                    return event
            if self.eof:
                raise RuntimeError(f"Native worker exited before {name}; inspect its log")
            self.pump()

    def finish(self, expected_returncode=0):
        while not self.eof:
            self.pump()
        remaining = max(0.1, self.deadline - time.monotonic())
        returncode = self.process.wait(timeout=remaining)
        require(returncode == expected_returncode, f"Unexpected native worker exit status {returncode}")
        return returncode

    def kill(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)

    def close(self):
        try:
            self.kill()
            self.process.wait(timeout=10)
        finally:
            self.selector.close()
            self.log.close()
            self.process.stdin.close()
            self.process.stdout.close()


def qualify(args):
    from semantic_search.corpus import extract_snippets
    from semantic_search.index import document_text

    repo, root = args.repo.resolve(), (args.repo / args.source_root).resolve()
    require(repo.is_dir() and root.is_dir() and root.is_relative_to(repo), "Invalid saved source scope")
    source_root = root.relative_to(repo).as_posix()
    require(args.model_path.is_dir(), "Expected an existing local pinned Qwen checkpoint")
    args.work_dir, args.output_dir = args.work_dir.resolve(), args.output_dir.resolve()
    require(not args.work_dir.is_relative_to(args.output_dir)
            and not args.output_dir.is_relative_to(args.work_dir),
            "Private work and public evidence directories must not overlap")
    for directory in (args.work_dir, args.output_dir):
        require(not directory.exists() or not any(directory.iterdir()),
                "Qualification directories must be new or empty")
        directory.mkdir(parents=True, exist_ok=True)
    args.work_dir.chmod(0o700)
    initial_source = source_snapshot(repo, source_root)
    snippets = extract_snippets(repo, source_root, max_lines=160, overlap_lines=40)
    require(len(snippets) >= SAMPLE_SIZE, "Source has fewer than twelve extracted windows")
    positions = [index * (len(snippets) - 1) // (SAMPLE_SIZE - 1) for index in range(SAMPLE_SIZE)]
    selected = [snippets[index] for index in positions]
    document_hashes = [sha256(document_text(item).encode()) for item in selected]
    require(len(set(document_hashes)) == SAMPLE_SIZE, "Evenly sampled documents are not distinct")
    for item in selected:
        physical_check(repo, item)
    require(source_snapshot(repo, source_root) == initial_source, "Source changed during extraction")
    manifest = args.work_dir / "private-source-fixture.json"
    write_json(manifest, {"snippets": [asdict(item) for item in selected]})
    manifest.chmod(0o600)
    database, baseline_db = args.work_dir / "resumed.sqlite3", args.work_dir / "baseline.sqlite3"
    workers = []

    def start(mode, db):
        command = [sys.executable, str(Path(__file__).resolve()), "--worker-mode", mode,
                   "--manifest", str(manifest), "--database", str(db),
                   "--model-path", str(args.model_path.resolve())]
        process = WorkerProcess(command, args.output_dir / f"{mode}.stdout.log", args.timeout)
        workers.append(process)
        return process

    try:
        seed = start("seed", database)
        seed_event = seed.wait_event("COMPLETE")
        seed.finish()
        old = active_snapshot(database)
        require(old["cached_documents"] == 1 and old["active_tables"]["snippets"]["rows"] == 1,
                "Seed did not establish exactly one active cached document")
        interrupted = start("pause", database)
        checkpoint = interrupted.wait_event("CHECKPOINT")
        during = active_snapshot(database)
        require(during["active_tables"] == old["active_tables"], "Checkpoint modified active tables")
        completed = checkpoint["completed"]
        require(0 < completed < SAMPLE_SIZE - 1
                and during["cached_documents"] == 1 + completed,
                "Durable batch was not independently visible before termination")
        interrupted.kill()
        killed_returncode = interrupted.finish(-signal.SIGKILL)
        after_kill = active_snapshot(database)
        require(after_kill == during, "SIGKILL lost a committed batch or modified active tables")
        resumed = start("resume", database)
        resume_event = resumed.wait_event("COMPLETE")
        resumed.finish()
        baseline = start("baseline", baseline_db)
        baseline_event = baseline.wait_event("COMPLETE")
        baseline.finish()
        metadata = seed_event["embedding_metadata"]
        fingerprint = seed_event["model_fingerprint"]
        require(all(event["embedding_metadata"] == metadata
                    and event["model_fingerprint"] == fingerprint
                    for event in (checkpoint, resume_event, baseline_event)),
                "Fresh workers used different embedding identities")
        resumed_inputs = [position for request in resume_event["requests"]
                          for position in request["sample_positions"]]
        interrupted_inputs = [position for event in interrupted.events
                              if event["event"] == "DURABLE_BATCH" for position in event["sample_positions"]]
        require(len(resumed_inputs) == SAMPLE_SIZE - 1 - completed
                and len(set(resumed_inputs)) == len(resumed_inputs)
                and not (set(resumed_inputs) & ({0} | set(interrupted_inputs)))
                and set(resumed_inputs) | set(interrupted_inputs) | {0} == set(range(SAMPLE_SIZE)),
                "Resume re-inferred a completed document or skipped a missing one")
        cache = next(event for event in resumed.events
                     if event["event"] == "PROGRESS" and event["phase"] == "cache")
        require(cache["cached_documents"] == 1 + completed
                and cache["pending_documents"] == SAMPLE_SIZE - 1 - completed,
                "Fresh-process cache accounting differs from durable work")
        comparisons = {}
        for name, dtype in (("raw", np.dtype("float64")), ("active", np.dtype("float32"))):
            actual = np.load(database.with_suffix(f".{name}.npy"), allow_pickle=False)
            expected = np.load(baseline_db.with_suffix(f".{name}.npy"), allow_pickle=False)
            require(actual.dtype == expected.dtype == dtype and actual.shape == expected.shape
                    and actual.shape == (SAMPLE_SIZE, metadata["dimensions"])
                    and np.isfinite(actual).all() and actual.tobytes() == expected.tobytes(),
                    f"Resumed {name} vectors differ from uninterrupted execution")
            comparisons[name] = {"dtype": str(dtype), "shape": list(actual.shape),
                                 "sha256": sha256(actual.tobytes()), "bitwise_equal": True,
                                 "maximum_absolute_difference": float(np.max(np.abs(actual - expected)))}
        baseline_full = [row["sample_positions"] for row in baseline_event["native_batches"]
                         if row["phase"] == "full"]
        resumed_full = [row["sample_positions"] for row in resume_event["native_batches"]]
        interrupted_full = [event["sample_positions"] for event in interrupted.events
                            if event["event"] == "NATIVE_BATCH"]
        require(interrupted_full + resumed_full == baseline_full,
                "Interrupted and resumed native batch schedules differ from baseline")
        require(source_snapshot(repo, source_root) == initial_source, "Source changed during native qualification")
        for item in selected:
            physical_check(repo, item)
        report = {"passed": True, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                  "kind": "One-device real-Qwen SIGKILL/resume durability and bitwise vector parity",
                  "limitations": ["Existing local checkpoint; no first model download",
                                  "One Mac; not independent installation qualification",
                                  "Not retrieval quality, coding correctness or throughput evidence",
                                  "Offline Hugging Face settings; no socket instrumentation"],
                  "source_root": source_root, "source_snapshot": initial_source,
                  "source_unchanged": True, "extraction": {"max_lines": 160, "overlap_lines": 40,
                      "total_windows": len(snippets), "sample_size": SAMPLE_SIZE,
                      "selection": "Evenly spaced extraction positions including first and last"},
                  "sample_manifest": [{"sample_position": index, "extraction_position": positions[index],
                      "id": item.id, "path": item.path, "symbol": item.symbol,
                      "start_line": item.start_line, "end_line": item.end_line,
                      "source_sha256": item.content_hash, "document_sha256": document_hashes[index]}
                      for index, item in enumerate(selected)],
                  "embedding_metadata": metadata, "model_fingerprint": fingerprint,
                  "workers": {"seed": seed_event, "resume": resume_event, "baseline": baseline_event,
                              "interrupted_checkpoint": checkpoint,
                              "interrupted_native_batches": [event for event in interrupted.events
                                  if event["event"] in {"NATIVE_BATCH", "DURABLE_BATCH"}]},
                  "durability": {"before": old, "while_paused": during, "after_sigkill": after_kill,
                      "active_tables_unchanged": True, "checkpoint_visible_to_separate_connection": True,
                      "returncode": killed_returncode, "signal": "SIGKILL",
                      "seeded_documents": 1, "checkpointed_before_kill": completed,
                      "cached_on_resume": cache["cached_documents"],
                      "new_documents_on_resume": len(resumed_inputs),
                      "fresh_process_resume": resumed.process.pid != interrupted.process.pid},
                  "comparison": comparisons, "native_batch_schedule_identical": True,
                  "timing_policy": "Descriptive process and native-forward times only; no latency comparison claim"}
        write_json(args.output_dir / "verification.json", report)
        print(json.dumps({"passed": True, "cached_on_resume": cache["cached_documents"],
                          "new_documents_on_resume": len(resumed_inputs),
                          "receipt": str(args.output_dir / "verification.json")}, indent=2))
    finally:
        for process in reversed(workers):
            process.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--source-root", default="src/flask")
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=600,
                        help="Maximum seconds per serial native worker (default: 600)")
    parser.add_argument("--worker-mode", choices=("seed", "pause", "resume", "baseline"), help=argparse.SUPPRESS)
    parser.add_argument("--manifest", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--database", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker_mode:
        if args.manifest is None or args.database is None:
            parser.error("Internal worker requires manifest and database")
        worker(args)
        return 0
    if any(value is None for value in (args.repo, args.work_dir, args.output_dir)):
        parser.error("--repo, --work-dir and --output-dir are required")
    if not np.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    qualify(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
