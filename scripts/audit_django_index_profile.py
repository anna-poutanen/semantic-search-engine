#!/usr/bin/env python3
"""Independent CPU audit of the prospective Django durable-index profile.

No production extraction, normalization, ranking, index, embedding, or producer
helper is imported. Expected membership comes from the pre-output source/window
inventories and literal saved files. NumPy independently reconstructs SQLite's
normalized float32 blobs and accumulates cosine scores in float64.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import sqlite3
import subprocess
import sys
import tokenize

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "results/django-index-preflight-2026-10-14"
MANIFEST = ROOT / "benchmarks/django_index_profile.json"
DIMENSIONS = 1024
PROVIDERS = ("bm25", "dense", "hybrid")
QUERY_PROMPT = (
    "Instruct: Given a description of code behavior, retrieve the code implementation "
    "that performs it.\nQuery:"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(items):
    result = {}
    for key, value in items:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          ValueError("Nonfinite JSON: " + value)))


def read(path):
    return decode(Path(path).read_text())


def typed_equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            typed_equal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            typed_equal(a, b) for a, b in zip(actual, expected, strict=True))
    return actual == expected


def physical_lines(source):
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", source)


def source_text(raw):
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding)


def document(path, symbol, text):
    return f"Path: {path}\nSymbol: {symbol}\nSource:\n{text}"


def saved_windows(inventory, sources):
    """Verify saved inventory spans and build documents without extraction code."""
    output = []
    for row in inventory:
        path, first, last = row["path"], row["start_line"], row["end_line"]
        require(type(first) is int and type(last) is int and 1 <= first <= last,
                "Invalid inventory source boundaries")
        lines = physical_lines(source_text(sources[path]))
        require(last <= len(lines), "Window extends past saved file")
        text = "".join(lines[first - 1:last])
        rendered = document(path, row["symbol"], text)
        require(row["id"] == f"{path}:{row['symbol']}:{first}-{last}",
                "Window ID not bound to source location")
        require(row["content_sha256"] == sha(text.encode()), "Frozen window content hash differs")
        require(row["document_sha256"] == sha(rendered.encode()), "Frozen rendered document hash differs")
        output.append({**row, "text": text, "document": rendered})
    require(len({row["id"] for row in output}) == len(output), "Duplicate active ID")
    require(len({row["document_sha256"] for row in output}) == len(output),
            "Expected unique rendered document inventory differs")
    return output


def mutation_states(original, manifest):
    """Derive copy-only states and cache union from exact rendered source bytes."""
    mutation = manifest["mutation"]
    path, renamed = mutation["path"], mutation["renamed_path"]
    affected = [row for row in original if row["path"] == path]
    target = [row for row in affected if row["id"] == mutation["edited_window_id"]]
    require(len(affected) == 13 and len(target) == 1, "Mutation window membership differs")
    require(target[0]["text"].count(mutation["replacement_line_before"]) == 1,
            "Mutation is not one unique line")
    edited = []
    for row in original:
        changed = dict(row)
        if row["id"] == mutation["edited_window_id"]:
            changed["text"] = row["text"].replace(mutation["replacement_line_before"],
                                                    mutation["replacement_line_after"])
            changed["content_sha256"] = sha(changed["text"].encode())
            changed["document"] = document(path, row["symbol"], changed["text"])
            changed["document_sha256"] = sha(changed["document"].encode())
            require(changed["document_sha256"] == mutation["edited_document_sha256"],
                    "Saved edit document binding differs")
        edited.append(changed)
    moved = []
    for row in edited:
        changed = dict(row)
        if row["path"] == path:
            changed["path"] = renamed
            changed["id"] = f"{renamed}:{row['symbol']}:{row['start_line']}-{row['end_line']}"
            changed["document"] = document(renamed, row["symbol"], row["text"])
            changed["document_sha256"] = sha(changed["document"].encode())
        moved.append(changed)
    # Production extraction sorts tracked paths, then preserves definition order.
    path_order = {row["id"]: position for position, row in enumerate(original)}
    moved.sort(key=lambda row: (row["path"], path_order.get(row["id"].replace(renamed, path), -1)))
    by_name = {"initial": original, "fresh_process_reopen": original,
               "saved_edit": edited, "delete_file": [row for row in edited if row["path"] != path],
               "restore_edited_file": edited, "rename_edited_file": moved,
               "rename_back": edited, "restore_pristine_file": original,
               "final_fresh_process_reopen": original}
    cache, previous_ids, result = set(), set(), []
    for expected in manifest["persistence_sequence"]:
        name = expected["stage"]
        active = by_name[name]
        hashes = {row["document_sha256"] for row in active}
        ids = {row["id"] for row in active}
        actual = {"stage": name, "total_windows": len(active), "indexed_windows": len(hashes - cache),
                  "new_unique_documents": len(hashes - cache), "reused_windows": len(hashes & cache),
                  "deleted_windows": len(previous_ids - ids), "cached_documents_after": len(cache | hashes)}
        require(all(typed_equal(actual[key], value) for key, value in expected.items()
                    if key != "forbid_document_and_query_inference"), "Independent mutation counts differ")
        result.append({**actual, "windows": active, "cached_hashes": sorted(cache | hashes),
                       "new_hashes": [row["document_sha256"] for row in active
                                      if row["document_sha256"] not in cache]})
        cache |= hashes
        previous_ids = ids
    return result


def independent_float32(values):
    """Reconstruct float32 stored unit vectors, independently of store helpers."""
    matrix = np.asarray(values)
    require(matrix.ndim == 2 and matrix.shape[1] > 0 and matrix.dtype.kind == "f",
            "Vector capture must be a real floating matrix")
    require(np.isfinite(matrix).all() and np.all(np.any(matrix != 0, axis=1)),
            "Vectors must be finite and nonzero")
    wide = matrix.astype(np.float64)
    largest = np.max(np.abs(wide), axis=1)
    scaled = wide / largest[:, None]
    lengths = np.sqrt(np.sum(scaled * scaled, axis=1))
    return (scaled / lengths[:, None]).astype("<f4")


def independent_dense(ids, document_vectors, query, limit=10):
    require(len(ids) == len(document_vectors) and len(set(ids)) == len(ids), "Dense ID inventory differs")
    documents = independent_float32(document_vectors).astype(np.float64)
    query = independent_float32(np.asarray(query).reshape(1, -1))[0].astype(np.float64)
    require(documents.shape[1] == query.shape[0], "Query dimensions differ")
    numerators = np.sum(documents * query, axis=1)
    scores = numerators / (np.sqrt(np.sum(documents * documents, axis=1))
                           * math.sqrt(float(np.sum(query * query))))
    order = sorted(range(len(ids)), key=lambda i: (-float(scores[i]), ids[i]))[:limit]
    return [{"id": ids[i], "score": float(scores[i])} for i in order]


def verify_dense(expected, observed, tolerance=3e-6):
    require(len(observed) == len(expected), "Dense top-k count differs")
    require([row["id"] for row in observed] == [row["id"] for row in expected],
            "Dense order differs; near ties are not automatically accepted")
    errors = []
    for actual, wanted in zip(observed, expected, strict=True):
        require(type(actual["score"]) is float and math.isfinite(actual["score"]), "Invalid cosine scalar")
        errors.append(abs(actual["score"] - wanted["score"]))
    require(max(errors, default=0.0) <= tolerance, "Dense cosine error exceeds fixed bound")
    return max(errors, default=0.0)


def timing_summary(samples):
    require(samples and all(type(value) is float and math.isfinite(value) and value >= 0 for value in samples),
            "Invalid finite elapsed samples")
    return {"count": len(samples), "median_s": float(np.median(samples)),
            "p95_s": float(np.percentile(samples, 95, method="linear")),
            "min_s": min(samples), "max_s": max(samples)}


def verify_embedding_metadata(metadata):
    expected = {"id": "Qwen/Qwen3-Embedding-0.6B",
                "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
                "weights_dtype": "bfloat16", "quantized": False, "runtime": "mlx-embeddings",
                "runtime_version": "0.1.0", "dimensions": DIMENSIONS, "batch_size": 4,
                "query_instruction": QUERY_PROMPT[len("Instruct: "):].removesuffix("\nQuery:"),
                "query_prompt": QUERY_PROMPT, "document_prompt": None,
                "pooling": "last non-padding token", "normalization": "L2, float32",
                "max_tokens": 8192, "truncation": "right", "padding_side": "right",
                "add_special_tokens": True,
                "batch_order": "stable ascending token length; restore input order"}
    require(isinstance(metadata, dict) and all(typed_equal(metadata.get(key), value)
                                              for key, value in expected.items()),
            "Frozen original embedding metadata differs")
    require(metadata.get("packages", {}).get("mlx-embeddings") == "0.1.0",
            "Unexpected native embedding runtime")


def verify_witness(path, witness):
    current = Path(path).stat()
    fields = {"device": current.st_dev, "inode": current.st_ino, "size": current.st_size,
              "mtime_ns": current.st_mtime_ns}
    require(all(type(witness.get(key)) is int and witness[key] == value for key, value in fields.items()),
            "Physical source/runtime/checkpoint witness differs: " + str(path))


def verify_native_batch_inputs(inputs, batch_positions, token_ids, expected_token_ids, kind,
                               clipped_token_ids=None):
    """Inspect real unpadded tokenizer IDs and stable ascending native order."""
    require(kind in ("documents", "query"), "Unknown native operation kind")
    require(len(inputs) == len(expected_token_ids), "Native token/input inventory differs")
    require(all(isinstance(text, str) and text.strip() for text in inputs), "Empty native literal input")
    require(all(isinstance(ids, list) and ids and all(type(value) is int and value >= 0 for value in ids)
                for ids in expected_token_ids), "Invalid tokenizer token-ID inventory")
    clipped = clipped_token_ids if clipped_token_ids is not None else [ids[:8192] for ids in expected_token_ids]
    require(len(clipped) == len(expected_token_ids) and all(isinstance(ids, list) and 0 < len(ids) <= 8192
                                                           for ids in clipped), "Clipped tokenizer inventory differs")
    expected_order = sorted(range(len(inputs)), key=lambda i: (len(clipped[i]), i))
    expected_batches = [expected_order[start:start + 4] for start in range(0, len(inputs), 4)]
    require(typed_equal(batch_positions, expected_batches), "Native length-sorted batch order differs")
    require(len(token_ids) == len(expected_batches), "Native forward batch inventory differs")
    for positions, actual in zip(expected_batches, token_ids, strict=True):
        require(typed_equal(actual, [clipped[position] for position in positions]),
                "Actual native token IDs differ from independent saved input encoding")
    return {"inputs": len(inputs), "forwards": len(expected_batches),
            "truncated_inputs": sum(len(ids) > 8192 for ids in expected_token_ids)}


def verify_clock(start, end, elapsed):
    require(type(elapsed) is float and math.isfinite(elapsed) and elapsed >= 0, "Invalid elapsed clock")
    wall_start, wall_end = datetime.fromisoformat(start["wall_utc"]), datetime.fromisoformat(end["wall_utc"])
    require(wall_start.tzinfo is not None and wall_end.tzinfo is not None, "Clock lacks timezone")
    for key in ("monotonic_s",):
        require(all(type(item[key]) is float and math.isfinite(item[key]) for item in (start, end)),
                "Invalid monotonic clock")
    monotonic = end["monotonic_s"] - start["monotonic_s"]
    wall = (wall_end - wall_start).total_seconds()
    require(monotonic >= 0 and abs(monotonic - elapsed) <= 0.01, "Elapsed is not bound to monotonic clock")
    require(abs(wall - monotonic) <= 2.0, "Wall/monotonic discontinuity invalidates timing")


def schedule(query_ids):
    output = [{"phase": "first", "sweep": -1, "query_id": query_ids[0], "provider": provider}
              for provider in PROVIDERS]
    for phase, count in (("warmup", 3), ("measured", 10)):
        for sweep in range(count):
            shift = sweep % len(query_ids)
            ordered = query_ids[shift:] + query_ids[:shift]
            for position, query in enumerate(ordered):
                offset = (sweep + position) % len(PROVIDERS)
                for provider in PROVIDERS[offset:] + PROVIDERS[:offset]:
                    output.append({"phase": phase, "sweep": sweep, "query_id": query, "provider": provider})
    return output


def nanosecond_sample(value):
    require(isinstance(value, dict) and set(value) == {"wall_time_ns", "perf_counter_ns", "utc"},
            "Exact raw nanosecond clock sample required")
    require(type(value["wall_time_ns"]) is int and type(value["perf_counter_ns"]) is int
            and value["perf_counter_ns"] >= 0, "Nanosecond clocks must be strict integers")
    seconds, nanos = divmod(value["wall_time_ns"], 1_000_000_000)
    utc = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=nanos // 1000).isoformat()
    require(value["utc"] == utc, "UTC differs from actual wall-clock nanoseconds")


def nanosecond_interval(before, after, strict_utc=True):
    if strict_utc:
        nanosecond_sample(before)
        nanosecond_sample(after)
    else:
        plain_stamp(before)
        plain_stamp(after)
    wall = after["wall_time_ns"] - before["wall_time_ns"]
    perf = after["perf_counter_ns"] - before["perf_counter_ns"]
    return {"wall_elapsed_ns": wall, "perf_elapsed_ns": perf, "drift_ns": wall - perf,
            "absolute_drift_ns": abs(wall - perf), "max_drift_ns": 2_000_000_000,
            "consistent": perf >= 0 and abs(wall - perf) <= 2_000_000_000}


def clock_receipt(value):
    require(value.get("version") == 1 and value.get("max_drift_ns") == 2_000_000_000
            and value.get("consistent") is True, "Clock qualification differs")
    expected = nanosecond_interval(value["baseline"], value["sample"])
    require(expected["consistent"] is True and typed_equal(value["cumulative"], expected),
            "Cumulative clock receipt differs or is discontinuous")


def measurement(value):
    start, end = value["start"], value["end"]
    clock_receipt(start)
    clock_receipt(end)
    require(typed_equal(start["baseline"], end["baseline"]), "Measurement reset clock baseline")
    expected = nanosecond_interval(start["sample"], end["sample"])
    require(expected["consistent"] and typed_equal(end["interval"], expected), "Measured clock interval differs")
    elapsed = expected["perf_elapsed_ns"] / 1e9
    require(type(value["elapsed_s"]) is float and value["elapsed_s"] == elapsed, "Elapsed not derived from actual clock interval")
    return elapsed


def plain_stamp(value):
    require(isinstance(value, dict) and set(value) == {"wall_time_ns", "perf_counter_ns", "utc"}
            and type(value["wall_time_ns"]) is int and type(value["perf_counter_ns"]) is int,
            "Plain event clock must retain nanosecond integers and UTC")
    observed = datetime.fromisoformat(value["utc"])
    require(observed.tzinfo is not None and abs(observed.timestamp() - value["wall_time_ns"] / 1e9) <= 0.01,
            "Plain event UTC differs materially from its wall sample")


def rows(path):
    raw = Path(path).read_bytes()
    if Path(path).suffix == ".gz":
        raw = gzip.decompress(raw)
    return [decode(line) for line in raw.decode().splitlines() if line]


def local_path(value):
    value = value.replace("<PROJECT>", str(ROOT)).replace("<HOME>", str(Path.home()))
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def matrix_capture(descriptor):
    path, _ = bound_artifact(descriptor, ROOT)
    values = np.load(path, allow_pickle=False)
    require(values.dtype == np.dtype("float32") and values.ndim == 2 and values.shape[1] == DIMENSIONS,
            "Original capture must be float32 with1024 dimensions")
    require(np.isfinite(values).all() and np.all(np.any(values != 0, axis=1)), "Invalid original capture")
    require(typed_equal(descriptor["shape"], list(values.shape)) and descriptor["dtype"] == "float32"
            and descriptor["raw_sha256"] == sha(values.tobytes()), "Matrix descriptor/raw-byte binding differs")
    return values


def verify_memory(value, model_loaded=False):
    require(value.get("rss_peak_is_cumulative") is True, "Peak RSS must be labelled cumulative")
    require(all(type(value.get(key)) is int and value[key] > 0 for key in ("rss_peak_bytes", "rss_current_bytes")),
            "RSS memory requires correct positive byte units")
    if model_loaded:
        mlx = value.get("mlx", {})
        require(all(type(mlx.get(key)) is int and mlx[key] >= 0
                    for key in ("active_bytes", "peak_bytes", "cache_bytes")), "MLX memory labels/byte units differ")
        require(mlx["peak_bytes"] >= mlx["active_bytes"], "MLX peak below active memory")


def verify_disk(value):
    require(isinstance(value.get("files"), dict) and value["files"], "Disk checkpoint requires actual files")
    require(all(type(size) is int and size >= 0 for size in value["files"].values()), "Disk sizes must be bytes")
    require(type(value.get("total_bytes")) is int and value["total_bytes"] == sum(value["files"].values()),
            "Database/WAL/SHM disk sum differs")


def file_sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify_bindings(plan):
    require(plan.get("fresh_reopen_layer") == "source extraction + durable SQLite index; not SearchBackend/service startup",
            "Fresh reopen layer is mislabeled")
    bindings = plan["source_sha256"]
    required = ("scripts/run_django_index_profile.py", "scripts/django_index_profile_core.py",
                "scripts/audit_django_index_profile.py", "tests/test_audit_django_index_profile.py",
                "benchmarks/django_index_profile.json", "docs/large-index-profile-protocol.md")
    require(all(name in bindings for name in required), "Executed runner/checker/frozen inputs must all be sourcebound")
    for name, expected in bindings.items():
        path = local_path(name)
        require(file_sha(path) == expected, "Executed frozen source drift: " + name)
        relative = path.relative_to(ROOT).as_posix()
        committed = subprocess.run(["git", "-C", str(ROOT), "show", plan["source_commit"] + ":" + relative],
                                   capture_output=True, check=True, timeout=30).stdout
        require(sha(committed) == expected, "Executed source was not committed before freeze")
    require(file_sha(Path(__file__)) == bindings["scripts/audit_django_index_profile.py"],
            "Actual auditor source differs from committed prospective checker")
    for name in ("native_source_sha256", "tokenizer_cache_sha256"):
        require(plan[name], "Missing native/tokenizer implementation closure")
        for path, expected in plan[name].items():
            require(file_sha(local_path(path)) == expected, "Executed native/tokenizer file drift")
    runtime = plan["runtime"]
    require(runtime["python"] == sys.version and runtime["machine"] == platform.machine() == "arm64"
            and runtime["platform"] == platform.platform() and sys.platform == "darwin"
            and runtime["executable_sha256"] == file_sha(local_path(runtime["executable"])),
            "Actual Python/native architecture or binary differs")
    def artifact_path(value):
        path = Path(value).absolute()
        return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    require(runtime["working_directory"] == artifact_path(Path.cwd())
            and runtime["sys_path"] == [artifact_path(value) if value else "" for value in sys.path]
            and runtime["prefix"] == artifact_path(sys.prefix)
            and runtime["base_prefix"] == artifact_path(sys.base_prefix),
            "Actual Python import paths/prefix/working directory differ from prospective runtime")
    distributions = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        require(name not in distributions, "Duplicate installed runtime distribution")
        record = distribution.read_text("RECORD")
        distributions[name] = {"version": distribution.version,
                               "record_sha256": sha(record.encode()) if record is not None else None}
    require(typed_equal(distributions, runtime["packages"]), "Actual complete package closure differs")
    checkpoint = plan["checkpoint"]
    require(checkpoint.get("download_cached") is True and checkpoint.get("weights_loaded_during_freeze") is False
            and checkpoint.get("full_weight_hash_verified_during_run_before_inference") is True,
            "Cached-checkpoint/first-index phase labels differ")
    path = local_path(checkpoint["path"])
    require({item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file()} == set(checkpoint["files"]),
            "Checkpoint physical file membership differs")
    require(checkpoint["files"]["model.safetensors"]["sha256"] ==
            "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd",
            "Original checkpoint expected weight digest differs")
    for name, expected in checkpoint["files"].items():
        file = path / name
        require(type(expected["bytes"]) is int and expected["bytes"] == file.stat().st_size
                and file_sha(file) == expected["sha256"], "Current checkpoint byte/hash witness differs")
    coding = plan["coding_precondition"]
    require(type(coding.get("completed_attempts")) is int and coding["completed_attempts"] == 18,
            "All18 coding attempts must terminate before profile freeze")
    certificate = read(local_path(coding["audit"]))
    require(file_sha(local_path(coding["audit"])) == coding["audit_sha256"]
            and certificate.get("checks_passed") is True and certificate.get("inventory_complete") is True
            and certificate.get("final_completion_receipt") is True and certificate.get("recorded_attempts") == 18,
            "Independent final coding certificate is absent or changed")
    for path, expected in coding["artifact_sha256"].items():
        require(file_sha(local_path(path)) == expected, "Certified pre-profile coding artifact drift")
    driver = plan["coding_driver_exit"]
    require(driver.get("registered_driver") is True and driver.get("active") is False
            and driver.get("ps_exit_code") == 1 and driver.get("owned_group") == driver.get("pid"),
            "Coding/native work isolation proof differs")
    require(file_sha(local_path(driver["proof"])) == driver["proof_sha256"], "Registered coding-driver exit proof changed")


def verify_empty_database(receipt):
    require(receipt.get("database_was_absent") is True and typed_equal(receipt.get("table_counts"),
            {"embeddings": 0, "snippets": 0, "active_vectors": 0, "snippets_fts": 0}),
            "First-index cache/source tables were not genuinely empty")
    snapshot, _ = bound_artifact(receipt["snapshot"], ROOT)
    with sqlite3.connect(f"file:{snapshot.resolve()}?mode=ro", uri=True) as connection:
        require(connection.execute("PRAGMA user_version").fetchone()[0] == 2, "Empty database schema differs")
        for table in ("embeddings", "snippets", "active_vectors", "snippets_fts", "metadata"):
            require(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0,
                    "Actual first-index schema snapshot contains imported source/vectors")


def verify_operations(operations, expected, ledger, checkpoint, document_ids):
    from tokenizers import Tokenizer
    tokenizer_path = local_path(checkpoint["path"]) / "tokenizer.json"
    full_tokenizer = Tokenizer.from_file(str(tokenizer_path))
    clipped_tokenizer = Tokenizer.from_file(str(tokenizer_path))
    full_tokenizer.no_truncation()
    full_tokenizer.no_padding()
    clipped_tokenizer.no_padding()
    clipped_tokenizer.enable_truncation(max_length=8192, direction="right")
    require(expected and len(operations) == len(expected), "Actual embedding operation inventory differs")
    started = [row for row in ledger if row["event"] == "embedding_operation_started"]
    completed = [row for row in ledger if row["event"] == "embedding_operation_completed"]
    batches = [row for row in ledger if row["event"] == "native_batch_started"]
    ends = [row for row in ledger if row["event"] == "native_batch_completed"]
    expected_forwards = sum(math.ceil(len(texts) / 4) for _, texts, _ in expected)
    require(len(started) == len(completed) == len(expected)
            and len(batches) == len(ends) == expected_forwards,
            "Native actual start/completion inventory differs")
    require([row["forward_number"] for row in batches] == [row["forward_number"] for row in ends]
            == list(range(1, expected_forwards + 1)), "Native forward numbering differs")
    require(sum(row["event"] == "model_initialization_started" for row in ledger) == 1
            and sum(row["event"] == "model_initialization_completed" for row in ledger) == 1,
            "Real initial model loading event inventory differs")
    require(all(before["sequence"] < after["sequence"] for before, after in zip(completed, started[1:])),
            "Native embedding operations overlap or changed chronological order")
    vectors, query_hashes = {}, []
    for number, (receipt, wanted, begin, finish) in enumerate(zip(operations, expected, started, completed, strict=True), 1):
        kind, texts, query_id = wanted
        input_hashes = [sha(text.encode()) for text in texts]
        common = {"number": number, "kind": kind, "query_id": query_id, "input_sha256": input_hashes}
        require(all(typed_equal(receipt.get(key), value) and typed_equal(begin.get(key), value)
                    and typed_equal(finish.get(key), value) for key, value in common.items()),
                "Actual embedding operation input/order identity differs")
        require(typed_equal(receipt["inputs"], len(texts)) and typed_equal(receipt["shape"], [len(texts), DIMENSIONS])
                and receipt["dtype"] == "float32", "Actual embedding output shape/dtype differs")
        require(all(typed_equal(finish.get(key), value) for key, value in receipt.items()),
                "Completion ledger differs from returned operation receipt")
        plain_stamp(receipt["start"])
        plain_stamp(receipt["end"])
        require(nanosecond_interval(receipt["start"], receipt["end"], strict_utc=False)["consistent"],
                "Native operation clock discontinuity")
        require(begin["sequence"] < finish["sequence"], "Operation completed before start")
        actual_batches = [row for row in batches if row["operation"] == number]
        actual_ends = [row for row in ends if row["operation"] == number]
        tokens = [item.ids for item in full_tokenizer.encode_batch(texts, add_special_tokens=True)]
        clipped = [item.ids for item in clipped_tokenizer.encode_batch(texts, add_special_tokens=True)]
        positions = [[item["position"] for item in row["inputs"]] for row in actual_batches]
        actual_tokens = [[item["input_ids"] for item in row["inputs"]] for row in actual_batches]
        verify_native_batch_inputs(texts, positions, actual_tokens, tokens, kind, clipped)
        values = None
        if kind == "documents":
            path = local_path(receipt["matrix_file"])
            require(file_sha(path) == receipt["matrix_file_sha256"], "Native document matrix file hash differs")
            values = np.load(path, allow_pickle=False)
            require(values.dtype == np.dtype("float32") and values.shape == (len(texts), DIMENSIONS)
                    and np.isfinite(values).all() and np.all(np.any(values != 0, axis=1)),
                    "Native original document output capture differs")
            require(sha(values.tobytes()) == receipt["matrix_raw_sha256"], "Native original output bytes differ")
            for text, vector in zip(texts, values, strict=True):
                require(sha(text.encode()) not in vectors, "Document unexpectedly embedded twice")
                vectors[sha(text.encode())] = vector.copy()
        else:
            require(len(texts) == 1, "Each actual query must be independently embedded")
            query_hashes.append(receipt["matrix_raw_sha256"])
        for batch, end in zip(actual_batches, actual_ends, strict=True):
            require(begin["sequence"] < batch["sequence"] < end["sequence"] < finish["sequence"],
                    "Native forward not contained inside its operation")
            for key in ("operation", "forward_number", "kind", "batch_size", "inputs"):
                require(typed_equal(batch[key], end[key]), "Actual started/completed native inputs differ")
            require(type(batch["batch_size"]) is int and batch["batch_size"] == len(batch["inputs"])
                    and 0 < batch["batch_size"] <= 4 and batch["kind"] == kind,
                    "Actual native batch size/kind differs")
            require(type(end["native_compute_s"]) is float and math.isfinite(end["native_compute_s"])
                    and end["native_compute_s"] >= 0, "Invalid native compute subphase")
            for index, item in enumerate(batch["inputs"]):
                position = item["position"]
                require(type(position) is int and item["input_sha256"] == input_hashes[position]
                        and item["input_ids_sha256"] == sha(json.dumps(clipped[position], separators=(",", ":")).encode())
                        and type(item["full_tokens"]) is int and item["full_tokens"] == len(tokens[position])
                        and type(item["actual_tokens"]) is int and item["actual_tokens"] == len(clipped[position])
                        and item["truncated"] is (len(tokens[position]) > len(clipped[position])),
                        "Actual tokenizer length/truncation/token-ID binding differs")
                if kind == "documents":
                    require(item["document_id"] == document_ids[input_hashes[position]], "Native exact source document ID differs")
                    require(end["vector_sha256"][index] == sha(values[position].tobytes()),
                            "Native batch vector differs from original captured output")
                else:
                    require(item["document_id"] is None and end["vector_sha256"] == [receipt["matrix_raw_sha256"]],
                            "Native query output hash differs")
    return vectors, query_hashes[:8]


def verify_samples(study, expected_schedule, queries, dense_expected, sources, baseline=None):
    samples = rows(study / "samples.jsonl.gz")
    require(len(samples) == 630 and [row["sequence"] for row in samples] == list(range(1, 631)),
            "Raw search sample inventory is incomplete or duplicated")
    summary, candidates, max_error = {}, {}, 0.0
    for workload_number, workload in enumerate(("store", "api")):
        group = samples[workload_number * 315:(workload_number + 1) * 315]
        first, measured = {}, {provider: [] for provider in PROVIDERS}
        for number, (row, expected) in enumerate(zip(group, expected_schedule, strict=True)):
            require(row["event"] == "search_sample" and row["workload"] == workload
                    and type(row["schedule_number"]) is int and row["schedule_number"] == number
                    and typed_equal(row["identity"], expected), "Raw sample prospective order/identity differs")
            elapsed = measurement(row["measurement"])
            if baseline is not None:
                require(typed_equal(row["measurement"]["start"]["baseline"], baseline),
                        "Search sample reset persistent worker clock baseline")
            count = int(workload == "api" and expected["provider"] != "bm25")
            require(typed_equal(row["native_delta"], {"document_inputs": 0, "query_inputs": count,
                                                      "attempted_forwards": count, "completed_forwards": count}),
                    "Actual sample document/query/native inference differs")
            key = expected["query_id"], expected["provider"]
            if workload == "store":
                values = row["candidates"]
                require(len(values) <= 10 and len({item["id"] for item in values}) == len(values),
                        "Store raw top-ten count or IDs differ")
                require(all(type(item["score"]) is float and math.isfinite(item["score"]) for item in values),
                        "Store retrieval score must be finite float")
                if expected["provider"] == "dense":
                    max_error = max(max_error, verify_dense(dense_expected[expected["query_id"]], values))
                if key in candidates:
                    require(typed_equal(candidates[key], values), "Fixed-vector store results changed across sweeps")
                candidates[key] = values
            else:
                require(row["response"]["retrieval"] == expected["provider"], "Actual API provider differs")
                proof = verify_delivered(row["response"], queries[expected["query_id"]], sources)
                require(row["source_and_caps"]["payload_tokens"] == proof["tokens"]
                        and row["source_and_caps"]["source_characters"] == proof["source_chars"]
                        and row["source_and_caps"]["delivered_sources_exact"] is True,
                        "API retained source/caps receipt differs")
            if expected["phase"] == "first":
                first[expected["provider"]] = elapsed
            elif expected["phase"] == "measured":
                measured[expected["provider"]].append(elapsed)
        recorded = read(study / (workload + "-latency.json"))
        summary[workload] = {}
        for provider in PROVIDERS:
            require(len(measured[provider]) == 80, "Each provider requires80 measured samples")
            expected_summary = timing_summary(measured[provider])
            actual = recorded[provider]
            require(type(actual["first_use_s"]) is float and actual["first_use_s"] == first[provider],
                    "First-use observation changed or blended")
            require(all(typed_equal(actual["warm"][key], value) for key, value in expected_summary.items())
                    and typed_equal(actual["warm"]["samples_s"], measured[provider]),
                    "Warm distribution/count/linear percentile differs from actual raw samples")
            summary[workload][provider] = {"first_use_s": first[provider], "warm": expected_summary}
    return {**summary, "max_dense_score_error": max_error}, candidates


def verify_quality(quality, manifest, initial, sources, candidates):
    require(quality.get("kind") == "eight_source_authored_examples_not_general_benchmark",
            "Example scope is mislabeled as a general benchmark")
    rows = quality["rows"]
    expected = [(query, provider) for query in manifest["queries"] for provider in PROVIDERS]
    require(len(rows) == len(expected) == 24, "Every selected source-authored example/provider must remain")
    by_id = {row["id"]: row for row in initial["windows"]}
    result = []
    for row, (query, provider) in zip(rows, expected, strict=True):
        require(row["query_id"] == query["id"] and row["provider"] == provider, "Example/provider order differs")
        actual_candidates = candidates[(query["id"], provider)]
        require(row["raw_candidate_ids"] == [item["id"] for item in actual_candidates],
                "Example raw candidate list differs from measured fixed-vector results")
        require(all(value in by_id for value in row["raw_candidate_ids"]), "Candidate outside pinned source inventory")
        candidate_rows = [by_id[value] for value in row["raw_candidate_ids"]]
        groups = [any(any(value["path"] == target["path"] and value["symbol"] == target["symbol"]
                          and value["start_line"] <= target["evidence_lines"][0]
                          and value["end_line"] >= target["evidence_lines"][1] for value in candidate_rows)
                      for target in group) for group in query["target_groups"]]
        proof = verify_delivered(row["response"], query, sources)
        require(row["response"]["retrieval"] == provider
                and row["candidate_top_ten_complete_span"] is all(groups)
                and row["delivered_five_complete_span"] is proof["complete"],
                "Full-span source-authored example credit differs")
        count = int(provider != "bm25")
        require(typed_equal(row["native_delta"], {"document_inputs": 0, "query_inputs": count,
                                                  "attempted_forwards": count, "completed_forwards": count}),
                "Actual quality query inference count differs")
        result.append({"query_id": query["id"], "provider": provider, "candidate_complete": all(groups),
                       "delivered_complete": proof["complete"], "payload_tokens": proof["tokens"],
                       "source_chars": proof["source_chars"]})
    return result


def verify_reopen(study, expected, stage, vectors, worker_pid, ledger):
    path = study / "stages" / (expected["stage"] + "-fresh-process.json")
    receipt = read(path)
    require(receipt.get("complete") is True and receipt.get("passed") is True
            and receipt.get("fresh_process") is True and receipt.get("layer") == "durable_index_source_extraction_refresh"
            and receipt.get("parent_pid") == worker_pid and type(receipt.get("pid")) is int
            and receipt["pid"] != worker_pid, "Reopen was not a successful separate fresh CPU process")
    require(receipt.get("native_imports") == [] and receipt.get("blocked_import_attempts") == []
            and type(receipt.get("document_inference_calls")) is int and receipt["document_inference_calls"] == 0
            and type(receipt.get("query_inference_calls")) is int and receipt["query_inference_calls"] == 0
            and receipt.get("network_forbidden") is True and receipt.get("owned_parent_matches") is True
            and receipt.get("cached_values_exact") is True, "Fresh CPU process imported model or attempted inference")
    require(typed_equal(receipt["stats"], stage["stats"]) and receipt["source_files"] == stage["source_files"]
            and receipt["documents"] == {row["id"]: row["document_sha256"] for row in expected["windows"]},
            "Fresh reopen saved source/document/count bindings differ")
    require(typed_equal(receipt["before_refresh_database"], receipt["database"])
            and receipt["database"].get("all_source_and_cached_and_active_vectors_exact") is True
            and receipt["database"].get("cached_documents") == expected["cached_documents_after"]
            and all(receipt["database"].get(key) == expected["total_windows"] for key in
                    ("active_source_windows", "active_fts_windows", "active_dense_windows")),
            "Fresh CPU reopen changed source/FTS/dense bindings or repaired a corrupt starting state")
    matrix = np.stack([vectors[row["document_sha256"]] for row in expected["windows"]])
    require(receipt["returned_matrix_raw_sha256"] == sha(matrix.astype(np.float64).tobytes()),
            "Fresh CPU returned cached values differ from original native float32 values")
    reference = matrix_capture(receipt["reference_matrix"])
    require(np.array_equal(reference, matrix), "Fresh CPU input reference differs from genuine native output")
    for key in ("extraction_timing", "construction_timing", "refresh_timing"):
        measurement(receipt[key])
    clock_receipt(receipt["overall_clock"])
    verify_memory(receipt["memory"])
    matches = [row for row in ledger if row["event"] == "fresh_process_reopen_completed"
               and row["stage"] == expected["stage"]]
    require(len(matches) == 1 and matches[0]["pid"] == receipt["pid"] and matches[0]["exit_code"] == 0
            and matches[0]["receipt_sha256"] == sha(path.read_bytes()), "Owned fresh CPU completion receipt differs")


def bound_artifact(descriptor, base):
    require(isinstance(descriptor, dict) and isinstance(descriptor.get("path"), str), "Missing artifact descriptor")
    value = descriptor["path"].replace("<PROJECT>", str(ROOT)).replace("<HOME>", str(Path.home()))
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    raw = path.read_bytes()
    require(descriptor.get("sha256") == sha(raw), "Artifact SHA-256 differs: " + str(path))
    require(type(descriptor.get("bytes")) is int and descriptor["bytes"] == len(raw), "Artifact byte size differs")
    return path, raw


def lexical_terms(text):
    """Independent public token-splitting specification for FTS bindings."""
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", separated)
    return re.findall(r"[a-z0-9]+", separated.lower())


def verify_sqlite_snapshot(path, expected, vectors_by_hash, metadata):
    fingerprint = sha(json.dumps(metadata, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode())
    with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True) as connection:
        require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                "SQLite snapshot integrity failure")
        require(connection.execute("PRAGMA user_version").fetchone()[0] == 2, "SQLite schema differs")
        active_record = connection.execute("SELECT value FROM metadata WHERE key='active'").fetchone()
        require(active_record is not None, "No active metadata")
        active = decode(active_record[0])
        require(active.get("dimensions") == DIMENSIONS and active.get("model_fingerprint") == fingerprint
                and typed_equal(active.get("embedding"), metadata), "Stored embedding metadata differs")
        rows = connection.execute("SELECT id,position,path,symbol,start_line,end_line,text,content_hash,"
                                  "document_hash,model_fingerprint FROM snippets ORDER BY position").fetchall()
        require(len(rows) == expected["total_windows"], "Active source row count differs")
        wanted_rows = [(row["id"], i, row["path"], row["symbol"], row["start_line"], row["end_line"],
                        row["text"], row["content_sha256"], row["document_sha256"], fingerprint)
                       for i, row in enumerate(expected["windows"])]
        require(rows == wanted_rows, "Active saved source/position/document bindings differ")
        fts = dict(connection.execute("SELECT id,terms FROM snippets_fts"))
        require(len(fts) == len(rows) and fts == {
            row["id"]: " ".join(lexical_terms(f"{row['path']} {row['symbol']}\n{row['text']}"))
            for row in expected["windows"]}, "Active FTS bindings differ or stale IDs remain")
        cached = connection.execute("SELECT model_fingerprint,document_hash,dimensions,vector FROM embeddings").fetchall()
        require(len(cached) == expected["cached_documents_after"], "Cached document table count differs")
        require({row[1] for row in cached} == set(expected["cached_hashes"]), "Cached historical document union differs")
        for model, document_hash, dimensions, blob in cached:
            require(model == fingerprint and type(dimensions) is int and dimensions == DIMENSIONS,
                    "Cached vector model/dimensions differ")
            value = vectors_by_hash[document_hash]
            require(value.dtype == np.dtype("float32") and value.shape == (DIMENSIONS,),
                    "Original native vector capture must be float32/1024")
            require(np.isfinite(value).all() and np.any(value != 0), "Invalid original vector")
            require(blob == value.astype("<f8").tobytes(), "Cached vector differs from original native float32 output")
        dense = connection.execute("SELECT snippet_id,model_fingerprint,dimensions,vector FROM active_vectors").fetchall()
        require(len(dense) == len(rows) and {row[0] for row in dense} == {row[0] for row in rows},
                "Active dense ID set differs or stale/deleted IDs remain")
        documents = {row["id"]: row["document_sha256"] for row in expected["windows"]}
        for identifier, model, dimensions, blob in dense:
            require(model == fingerprint and type(dimensions) is int and dimensions == DIMENSIONS,
                    "Active dense model/dimensions differ")
            expected_blob = independent_float32(vectors_by_hash[documents[identifier]].reshape(1, -1))[0].tobytes()
            require(blob == expected_blob, "Active normalized float32 vector differs")
    return {"active_windows": len(rows), "cached_documents": len(cached), "model_fingerprint": fingerprint}


def verify_delivered(response, query, sources):
    require(response.get("query") == query["query"] and response.get("retrieval") in PROVIDERS,
            "Delivered query/provider differs")
    require(response.get("rerank") is False and response.get("source_budget") == 12000,
            "Delivered reranking/source budget differs")
    rows = response["results"]
    require(len(rows) <= 5, "Delivered result cap exceeded")
    total = 0
    for row in rows:
        path = row["path"]
        require(path in sources and row.get("origin") == "retrieval" and row.get("related_to") is None,
                "Delivered source scope/dependency differs")
        first, last = row["start_line"], row["end_line"]
        require(type(first) is int and type(last) is int and 1 <= first <= last, "Invalid delivered span")
        lines = physical_lines(source_text(sources[path]))
        require(last <= len(lines), "Delivered span extends beyond saved file")
        text = "".join(lines[first - 1:last])
        require(row["source"] == text and row["source_hash"] == sha(text.encode()),
                "Delivered source bytes/location/hash differ")
        require(type(row["truncated"]) is bool, "Truncation flag must be boolean")
        window_end = row["window_end_line"]
        require(type(window_end) is int and last <= window_end <= len(lines), "Saved window boundary differs")
        full = "".join(lines[first - 1:window_end])
        require(row["content_hash"] == sha(full.encode()) and row["truncated"] is (text != full),
                "Saved window content/truncation binding differs")
        require(row["id"] == f"{path}:{row['symbol']}:{first}-{window_end}",
                "Delivered snippet ID/location differs")
        total += len(text)
    require(total <= 12000 and type(response.get("total_source_chars")) is int
            and response["total_source_chars"] == total, "Actual delivered source character accounting differs")
    usage = response["token_usage"]
    require(usage.get("encoding") == "cl100k_base" and type(usage.get("tokens")) is int
            and 0 <= usage["tokens"] <= 2000 and usage.get("budget") == 2000,
            "Actual delivered token cap differs")
    # Import a CPU tokenizer only; this path is outside every measured sample.
    os.environ["TIKTOKEN_CACHE_DIR"] = str(ROOT / ".cache/tokenizers")
    import tiktoken
    fields = ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")
    compact_rows = []
    for row in rows:
        compact = {key: row[key] for key in fields}
        compact.update(origin=row.get("origin", "retrieval"), related_to=row.get("related_to"))
        for key in ("source_origin", "buffer_version", "buffer_hash"):
            if key in row:
                compact[key] = row[key]
        compact_rows.append(compact)
    compact = {"query": response["query"], "match_status": response.get("match_status", "candidates"),
               "results": compact_rows, "budget_exhausted": response.get("budget_exhausted", False),
               "token_usage": usage}
    if "scope" in response:
        compact["scope"] = response["scope"]
    payload = json.dumps(compact, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    tokens = len(tiktoken.get_encoding("cl100k_base").encode(payload, disallowed_special=()))
    require(tokens == usage["tokens"], "Independent compact-payload token count differs")
    coverage = []
    for group in query["target_groups"]:
        coverage.append(any(any(row["path"] == target["path"] and row["symbol"] == target["symbol"]
                                and row["start_line"] <= target["evidence_lines"][0]
                                and row["end_line"] >= target["evidence_lines"][1]
                                for row in rows) for target in group))
    return {"tokens": tokens, "source_chars": total, "groups": coverage, "complete": all(coverage)}


def run(command):
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=30).stdout.strip()


def prospective_inputs(manifest_path=MANIFEST, preflight=PREFLIGHT, original=None):
    manifest = read(manifest_path)
    freeze = read(preflight / "freeze.json")
    for relative, descriptor in freeze["artifacts"].items():
        path = ROOT / relative
        raw = path.read_bytes()
        require(sha(raw) == descriptor["sha256"] and len(raw) == descriptor["bytes"],
                "Frozen prospective input differs: " + relative)
    require(manifest["source_window_count"] == 9097 and manifest["unique_document_count"] == 9097,
            "Frozen corpus counts differ")
    source = Path(original) if original else ROOT / manifest["corpus"]
    require(run(["git", "-C", str(source), "rev-parse", "HEAD"]) == manifest["revision"], "Original source commit differs")
    require(not run(["git", "-C", str(source), "status", "--porcelain"]), "Original source fixture is dirty")
    inventory = read(preflight / "source-inventory.json")
    names = run(["git", "-C", str(source), "ls-files", "--", "django"]).splitlines()
    require(sorted(name for name in names if name.endswith(".py")) == [row["path"] for row in inventory],
            "Original tracked Python membership differs")
    sources = {}
    for row in inventory:
        raw = (source / row["path"]).read_bytes()
        require(sha(raw) == row["sha256"] and len(raw) == row["bytes"], "Original saved file differs")
        require(len(physical_lines(source_text(raw))) == row["physical_lines"], "Physical source lines differ")
        sources[row["path"]] = raw
    windows = decode(gzip.decompress((preflight / "window-inventory.json.gz").read_bytes()))
    originals = saved_windows(windows, sources)
    mutation = manifest["mutation"]
    raw_before = sources[mutation["path"]]
    before = mutation["replacement_line_before"].encode()
    after = mutation["replacement_line_after"].encode()
    require(raw_before.count(before) == 1, "Saved file mutation line is not unique")
    raw_edited = raw_before.replace(before, after)
    require(sha(raw_before) == mutation["original_file_sha256"]
            and sha(raw_edited) == mutation["edited_file_sha256"], "Saved mutation file hash differs")
    require(raw_edited == gzip.decompress((preflight / "mutation-sources/locmem.saved-edit.py.gz").read_bytes()),
            "Retained saved edit differs from independent literal mutation")
    for query in manifest["queries"]:
        for group in query["target_groups"]:
            for target in group:
                raw = sources[target["path"]]
                require(sha(raw) == target["source_file_sha256"], "Authored example saved file hash differs")
                first, last = target["evidence_lines"]
                span = "".join(physical_lines(source_text(raw))[first - 1:last])
                require(sha(span.encode()) == target["evidence_sha256"], "Authored example evidence span differs")
                require(any(row["id"] == target["eligible_window_id"] and row["symbol"] == target["symbol"]
                            and row["start_line"] <= first and row["end_line"] >= last
                            for row in originals), "Authored example lacks a complete eligible window")
    stages = mutation_states(originals, manifest)
    return manifest, sources, stages


def audit(study):
    manifest, sources, states = prospective_inputs()
    study = Path(study).resolve()
    plan = read(study / "frozen-plan.json")
    require(typed_equal(plan["manifest"], manifest), "Executed manifest differs from prospective inventory")
    expected_schedule = schedule([query["id"] for query in manifest["queries"]])
    require(typed_equal(plan["schedules"], {"store": expected_schedule, "api": expected_schedule}),
            "Exact rotated prospective timing schedule differs")
    require(plan.get("model_calls_during_freeze") == 0 and plan.get("native_imports_during_freeze") == []
            and plan.get("resume_or_overwrite") is False and plan.get("near_tie_mismatches_excused") is False,
            "Prospective freeze/no-resume/no-tie-excuse controls differ")
    verify_embedding_metadata(plan["embedding_metadata"])
    verify_bindings(plan)
    parent, worker = read(study / "parent-result.json"), read(study / "worker-result.json")
    start = read(study / "run-start.json")
    plan_hash = sha((study / "frozen-plan.json").read_bytes())
    require(parent.get("complete") is True and parent.get("checks_passed") is True
            and type(parent.get("exit_code")) is int and parent["exit_code"] == 0
            and parent.get("worker_checks_passed") is True,
            "Parent did not complete a clean successful attempt")
    require(worker.get("complete") is True and worker.get("checks_passed") is True
            and worker.get("completed_stages") == [row["stage"] for row in states],
            "Whole nine-stage worker attempt is incomplete or failed")
    require(parent["frozen_plan_sha256"] == worker["frozen_plan_sha256"] == start["plan_sha256"] == plan_hash,
            "Executed plan binding differs")
    require(parent["worker_result_sha256"] == sha((study / "worker-result.json").read_bytes()),
            "Parent worker-result hash differs")
    for value in (parent, worker):
        require("error" not in value and "clock_failure" not in value and "cleanup_error" not in value,
                "An interrupted/failed attempt cannot be certified")
        require(value.get("signals", {}).get("received") == [], "Interrupted attempt cannot be timed")
    require(worker.get("cleanup_signals", {}).get("received") == [],
            "Native model cleanup/publication was interrupted")
    require(parent.get("attempt_claimed") is True and parent.get("work_reserved") is True,
            "Fresh owned attempt/work reservation evidence is absent")
    cleanup = parent["cleanup"]
    require(cleanup.get("owned_group_stopped") is True and not cleanup.get("forced_kill")
            and not cleanup.get("errors"), "Owned native process cleanup was not clean")
    ownership = parent["ownership"]
    require(type(ownership["pid"]) is int and ownership["pid"] > 1 and ownership["owned_group"] == ownership["pid"]
            and ownership["start_new_session"] is True and worker["pid"] == ownership["pid"]
            and worker["parent_pid"] == ownership["owner_pid"] and worker["owned_process"] == ownership,
            "Native process ownership proof differs")
    require(ownership["command_sha256"] == sha(json.dumps(ownership["command"]).encode()), "Owned command hash differs")
    try:
        os.killpg(ownership["owned_group"], 0)
    except ProcessLookupError:
        pass
    else:
        raise ValueError("Owned native process group still exists")
    require(worker["cleanup"].get("model_released") is True and worker["cleanup"].get("index_closed") is True,
            "Model/index cleanup proof differs")
    for label in ("power_start", "power_end"):
        value = worker[label]
        require(value.get("supported") is True and value.get("ac_power") is True and "AC Power" in value.get("raw", ""),
                "AC power timing qualification differs")
    require(worker["sleep_check"].get("supported") is True and worker["sleep_check"].get("events") == [],
            "Sleep or wake interrupted the attempt")
    clock_receipt(worker["clock_final"])
    measurement(worker["schema_construction"])
    require(typed_equal(worker["schema_construction"]["start"]["baseline"], worker["clock_baseline"]),
            "Schema construction reset persistent clock baseline")
    verify_empty_database(worker["initial_empty_database"])
    for label, loaded in (("memory_before_model", False), ("memory_before_cleanup", True)):
        verify_memory(worker[label], loaded)
    verify_memory(worker["cleanup"]["memory_after_model_release"], True)
    require(worker["cleanup"]["memory_after_model_release"]["mlx"]["active_bytes"] <= 4096,
            "Resident native model allocations remain after cleanup")
    verify_disk(worker["disk_closed"])
    database = local_path(plan["work"]) / "index.sqlite3"
    actual_disk = {path.name: path.stat().st_size for path in
                   (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")) if path.exists()}
    require(actual_disk == worker["disk_closed"]["files"], "Current closed database/WAL/SHM witness differs")
    require(len(worker["model_initialization"]) == 1, "Model must initialize once inside initial indexing")
    initialization = worker["model_initialization"][0]
    require(initialization["model_class"] == "mlx_embeddings.models.qwen3.Model"
            and initialization["tokenizer_class"] == "mlx_embeddings.tokenizer_utils.TokenizerWrapper",
            "Actual loaded model/tokenizer class differs from original embedding implementation")
    require(initialization.get("loaded_native_origins"), "Loaded native implementation origins are absent")
    for name, origin in initialization["loaded_native_origins"].items():
        path = local_path(origin["path"])
        require(plan["native_source_sha256"].get(str(path)) == origin["sha256"] == file_sha(path),
                "Actual loaded native implementation origin differs: " + name)
    for key in ("start", "end"):
        plain_stamp(initialization[key])
    require(type(initialization["elapsed_s"]) is float and initialization["elapsed_s"] ==
            (initialization["end"]["perf_counter_ns"] - initialization["start"]["perf_counter_ns"]) / 1e9,
            "Real initial model-loading nested subphase differs")
    for path, expected in worker["artifact_sha256"].items():
        require(sha(local_path(path).read_bytes()) == expected, "Retained actual artifact changed: " + path)
    require(set(worker["raw_journals"]) == {"native-events.jsonl", "samples.jsonl"},
            "Actual private raw native/sample journal inventory differs")
    for name, descriptor in worker["raw_journals"].items():
        _, actual_raw = bound_artifact(descriptor, ROOT)
        require(actual_raw == gzip.decompress((study / (name + ".gz")).read_bytes()),
                "Public compressed native/sample journal differs from actual sealed raw bytes")
    ledger = rows(study / "native-events.jsonl.gz")
    require([row["sequence"] for row in ledger] == list(range(1, len(ledger) + 1)), "Native journal sequence differs")
    for row in ledger:
        plain_stamp(row["observed"])
        require(nanosecond_interval(worker["clock_baseline"], row["observed"], strict_utc=False)["consistent"],
                "Native event cumulative clock discontinuity")
    queries = {row["id"]: row for row in manifest["queries"]}
    operations_expected = [("documents", [row["document"] for row in states[0]["windows"]], None)]
    operations_expected.extend(("query", [QUERY_PROMPT + query["query"]], query["id"])
                               for query in manifest["queries"])
    operations_expected.extend(("query", [QUERY_PROMPT + queries[item["query_id"]]["query"]], item["query_id"])
                               for item in expected_schedule if item["provider"] != "bm25")
    operations_expected.extend(("query", [QUERY_PROMPT + query["query"]], query["id"])
                               for query in manifest["queries"] for provider in PROVIDERS if provider != "bm25")
    all_documents = {row["document_sha256"]: row["document"] for state in states for row in state["windows"]}
    operations_expected.extend(("documents", [all_documents[value] for value in state["new_hashes"]], None)
                               for state in states[1:] if state["new_hashes"])
    document_ids = {row["document_sha256"]: row["id"] for state in states for row in state["windows"]}
    require(len(operations_expected) == 237, "Independent full native operation schedule differs")
    vectors, query_vector_hashes = verify_operations(worker["embedding_operations"], operations_expected, ledger,
                                                    plan["checkpoint"], document_ids)
    require(set(vectors) == set(states[-1]["cached_hashes"]), "Native document capture union differs")
    expected_counts = {"document_inputs": 9111, "query_inputs": 234,
                       "attempted_forwards": 2514, "completed_forwards": 2514}
    require(all(typed_equal(worker[key], expected_counts)
                for key in ("native_expected_counts", "native_counts", "native_counts_final")),
            "Whole actual native inference inventory differs")
    stage_receipts = []
    mutation = manifest["mutation"]
    edited_sources = dict(sources)
    edited_sources[mutation["path"]] = sources[mutation["path"]].replace(
        mutation["replacement_line_before"].encode(), mutation["replacement_line_after"].encode())
    cumulative_documents, cumulative_queries, cumulative_forwards = 0, 0, 0
    for number, expected in enumerate(states):
        receipt = read(study / "stages" / (expected["stage"] + ".json"))
        require(receipt["stage"] == expected["stage"] and type(receipt["ordinal"]) is int
                and receipt["ordinal"] == number and receipt["checks_passed"] is True,
                "Stage identity/order/result differs")
        require(receipt["git_revision"] == manifest["revision"], "Execution copy Git revision differs")
        expected_source = dict(sources if number in (0, 1, 7, 8) else edited_sources)
        if number == 3:
            expected_source.pop(mutation["path"])
        if number == 5:
            expected_source[mutation["renamed_path"]] = expected_source.pop(mutation["path"])
        require(receipt["source_files"] == {path: sha(raw) for path, raw in expected_source.items()},
                "Stage tracked saved-file hash inventory differs")
        expected_windows = {row["id"]: {key: row[key] for key in ("document_sha256", "content_sha256", "path",
                                                                "symbol", "start_line", "end_line")}
                            for row in expected["windows"]}
        require(typed_equal(receipt["windows"], expected_windows), "Stage active window inventory differs")
        for key in ("indexed_windows", "reused_windows", "deleted_windows", "total_windows"):
            require(typed_equal(receipt["stats"].get(key), expected[key]), "Stage actual refresh count differs: " + key)
        snapshot, _ = bound_artifact(receipt["database_snapshot"], ROOT)
        inspection = verify_sqlite_snapshot(snapshot, expected, vectors, plan["embedding_metadata"])
        raw_values = matrix_capture(receipt["current_raw_matrix"])
        require(np.array_equal(raw_values, np.stack([vectors[row["document_sha256"]] for row in expected["windows"]])),
                "Stage retained current matrix differs from genuine native output order")
        elapsed = measurement(receipt["timing"])
        require(typed_equal(receipt["timing"]["start"]["baseline"], worker["clock_baseline"]),
                "Persistence stage reset worker clock baseline")
        verify_memory(receipt["memory"], True)
        verify_disk(receipt["disk_live"])
        cumulative_documents += expected["new_unique_documents"]
        cumulative_forwards += math.ceil(expected["new_unique_documents"] / 4)
        if number == 2:
            cumulative_queries = 234
            cumulative_forwards += 234
        counts = {"document_inputs": cumulative_documents, "query_inputs": cumulative_queries,
                  "attempted_forwards": cumulative_forwards, "completed_forwards": cumulative_forwards}
        require(typed_equal(receipt["native_counts"], counts), "Stage native count prefix differs")
        if expected["stage"] in ("fresh_process_reopen", "final_fresh_process_reopen"):
            verify_reopen(study, expected, receipt, vectors, worker["pid"], ledger)
        stage_receipts.append({"stage": expected["stage"], "elapsed_s": elapsed, **inspection})
    query_record = read(study / "query-vectors.json")
    query_matrix = matrix_capture(query_record["matrix"])
    require(query_matrix.shape == (8, DIMENSIONS), "Eight real raw query vectors required")
    for index, (query, receipt, vector) in enumerate(zip(manifest["queries"], query_record["rows"], query_matrix, strict=True)):
        require(receipt["id"] == query["id"] and receipt["query_sha256"] == sha(query["query"].encode())
                and receipt["vector_sha256"] == sha(vector.tobytes()) == query_vector_hashes[index],
                "Raw query matrix row differs from actual native operation")
    initial_ids = [row["id"] for row in states[0]["windows"]]
    initial_values = np.stack([vectors[row["document_sha256"]] for row in states[0]["windows"]])
    dense_expected = {query["id"]: independent_dense(initial_ids, initial_values, vector)
                      for query, vector in zip(manifest["queries"], query_matrix, strict=True)}
    latency, raw_candidates = verify_samples(study, expected_schedule, queries, dense_expected, sources,
                                            worker["clock_baseline"])
    numeric = read(study / "numerical-check.json")
    require(numeric["checks_passed"] is True and numeric["tolerance"] == 3e-6
            and numeric["near_tie_mismatches_excused"] is False and len(numeric["queries"]) == 8,
            "Numerical parity inventory/threshold differs")
    for index, (query, actual) in enumerate(zip(manifest["queries"], numeric["queries"], strict=True)):
        require(actual["query_position"] == index and actual["exact_order"] is True
                and actual["expected_numpy_ids"] == actual["actual_ids"] == [row["id"] for row in dense_expected[query["id"]]],
                "Independent dense top-ten exact ID order differs")
        require(type(actual["max_score_error"]) is float and 0 <= actual["max_score_error"] <= 3e-6,
                "Reported numerical error exceeds fixed tolerance")
    measurement(numeric["verification_timing_excluded_from_search_samples"])
    quality = verify_quality(read(study / "quality.json"), manifest, states[0], sources, raw_candidates)
    return {"prospective_only": False, "complete_inventory": True, "stages": stage_receipts,
            "native_counts": expected_counts, "numerical_queries": 8, "exact_dense_id_order": True,
            "max_dense_score_error": latency.pop("max_dense_score_error"), "latency": latency,
            "source_authored_examples": quality,
            "fresh_reopen_layer": "durable SQLite index and source extraction; not service startup",
            "limitations": manifest["limitations"], "owned_group_gone": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    arguments = parser.parse_args(argv)
    require(not arguments.output.exists(), "Refuse replacing an audit attempt")
    result = {"kind": "independent_django_index_profile_audit", "passed": False,
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "auditor_sha256": sha(Path(__file__).read_bytes()),
              "production_helpers_imported": False, "model_imports_or_inference": False}
    try:
        if arguments.preflight:
            manifest, _, stages = prospective_inputs()
            result.update(prospective_only=True, outcomes_observed=False,
                          window_count=manifest["source_window_count"],
                          independently_derived_stages=[{key: value for key, value in row.items()
                                                        if key not in ("windows", "cached_hashes", "new_hashes")}
                                                       for row in stages])
        else:
            require(arguments.study is not None, "--study is required for outcomes")
            result.update(audit(arguments.study))
        result["passed"] = True
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
