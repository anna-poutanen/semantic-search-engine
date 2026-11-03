#!/usr/bin/env python3
"""Frozen observed-source feature capture and CPU semantic-context replay.

Capture freshly encodes historical query/document features. Historical query
vectors were not recorded: the full query is encoded once as a singleton, then
only that query's literal clauses are batched. This is feature-capture timing,
not an end-to-end retrieval or warm-latency measurement. CLI capture acquires an
exclusive local GPU lease and verifies committed operands/runtime/source first.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCHEMA = "semantic_model_capture_v1"
WEIGHTS_SHA256 = "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def features_sha256(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                             separators=(",", ":")).encode())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def model_fingerprint(metadata):
    return digest(json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Ledger:
    """Exclusive append-only JSONL; each observer/checkpoint is durable."""
    def __init__(self, path, *, compressed=False):
        self.raw = Path(path).open("xb")
        self.stream = gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, mtime=0) if compressed else self.raw
        self.rows = []
        self.closed = False

    def append(self, row):
        snapshot = json.loads(canonical(row))
        self.stream.write(canonical(snapshot) + b"\n")
        self.stream.flush()
        self.raw.flush()
        os.fsync(self.raw.fileno())
        self.rows.append(snapshot)

    def close(self):
        if not self.closed:
            if self.stream is not self.raw:
                self.stream.close()
            self.raw.flush()
            os.fsync(self.raw.fileno())
            self.raw.close()
            self.closed = True


def vector_record(vector):
    from semantic_search.semantic_features import _vector

    values = _vector(vector, captured=False)
    return {"vector": values, "float32_le_sha256": digest(struct.pack(f"<{len(values)}f", *values))}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def close_preserving_error(embedder):
    original = sys.exception()
    try:
        embedder.close()
    except BaseException as close_error:
        if original is not None:
            raise original from close_error
        raise


def verify_runtime(root, manifest_path, freeze_commit, model_path):
    """Read/check files only; neither imports a numerical runtime nor loads it."""
    root, manifest_path = Path(root).resolve(), Path(manifest_path).resolve()
    relative = manifest_path.relative_to(root).as_posix()
    raw = manifest_path.read_bytes()
    for commit in (freeze_commit, "HEAD"):
        original = subprocess.check_output(["git", "-C", str(root), "show", f"{commit}:{relative}"])
        require(original == raw, "Runtime manifest is not the committed frozen blob")
    manifest = json.loads(raw)
    require(manifest.get("schema") == "semantic_model_runtime_v1", "Runtime manifest schema differs")
    binding = manifest["model_binding"]
    metadata, fingerprint = binding["metadata"], binding["fingerprint"]
    require(model_fingerprint(metadata) == fingerprint, "Runtime model fingerprint differs")
    checkpoint = binding["checkpoint"]
    path = Path(model_path).resolve()
    require(path == Path(checkpoint["path"]).resolve(), "Executing checkpoint path differs")
    files = {item.relative_to(path).as_posix(): item for item in path.rglob("*") if item.is_file()}
    require(files.keys() == checkpoint["files"].keys(), "Checkpoint file inventory differs")
    for name, record in checkpoint["files"].items():
        require(files[name].stat().st_size == record["bytes"] and file_sha(files[name]) == record["sha256"],
                "Checkpoint bytes differ: " + name)
    provenance = json.loads((path / "semantic_search_checkpoint.json").read_bytes())
    require(checkpoint["files"]["model.safetensors"]["sha256"] == WEIGHTS_SHA256,
            "Actual checkpoint weights do not match the pinned original SHA")
    # The recorded Hub ETag is a content SHA for this single weights file.
    require(provenance.get("weights_etag", "").strip('"') == WEIGHTS_SHA256,
            "Checkpoint provenance ETag differs from actual pinned weights")
    runtime = binding["runtime"]
    require(runtime["python"] == platform.python_version()
            and Path(runtime["executable"]).resolve() == Path(sys.executable).resolve()
            and runtime["platform"] == platform.platform(), "Executing Python/platform differs")
    for package, version in runtime["packages"].items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        require(actual == version, "Executing package differs: " + package)
    return {"manifest_path": relative, "manifest_sha256": digest(raw), "model_binding": binding}


@contextmanager
def gpu_lease(root):
    common = Path(git(root, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = Path(root) / common
    path = common.resolve() / "semantic-search-gpu.lock"
    stream = path.open("a+")
    receipt = {"path": str(path), "pid": os.getpid(), "acquired_at": None, "released_at": None}
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt["acquired_at"] = utc()
        yield receipt
    finally:
        if receipt["acquired_at"] is not None:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            receipt["released_at"] = utc()
        stream.close()


def _summary(forwards, calls):
    value = {stage: {"api_calls": calls[name], "model_forward_calls": 0, "encoded_texts": 0}
             for stage, name in (("documents", "document_api_calls"), ("query_full", "full_query_api_calls"),
                                 ("query_clauses", "clause_api_calls"))}
    for item in forwards:
        event = item["event"]
        if event["status"] in ("completed", "failed"):
            value[item["stage"]]["model_forward_calls"] += 1
            value[item["stage"]]["encoded_texts"] += event["batch_input_count"]
    value["total_model_forward_calls"] = sum(value[stage]["model_forward_calls"] for stage in
                                             ("documents", "query_full", "query_clauses"))
    value["total_encoded_texts"] = sum(value[stage]["encoded_texts"] for stage in
                                      ("documents", "query_full", "query_clauses"))
    return value


def capture(prepared, output, *, embedder, binding, finish=None, on_claim=None):
    """Capture only; injectable invented embedders support CPU contract tests.

    CLI owns the committed-freeze/runtime gate. Calling this library directly
    does not establish those attestations. A failed attempt is never overwritten
    or silently treated as a cache; completed stage/batch checkpoints remain.
    """
    from semantic_search.semantic_features import QUERY_PROMPT, _model, expected_facets

    output = Path(output)
    output.mkdir(exist_ok=False)
    directory = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    if on_claim is not None:
        on_claim()
    requests = None
    started, begin = utc(), time.perf_counter()
    stage, request_id = "initialization", None
    forwards = documents_log = queries_log = None
    documents, queries, timings = {}, {}, {}
    calls = {"document_api_calls": 0, "full_query_api_calls": 0, "clause_api_calls": 0,
             "retrieval_calls": 0, "index_calls": 0, "cache_hits": 0}
    finished = False

    def finish_once():
        nonlocal finished
        if not finished:
            finished = True
            if finish is not None:
                finish()

    def check_metadata():
        require(embedder.metadata == metadata, "Executing model metadata changed during capture")

    def observe(event):
        forwards.append({"sequence": len(forwards.rows), "stage": stage,
                         "request_id": request_id, "event": event})

    def artifacts():
        return {name: file_sha(output / name) for name in
                ("capture-start.json", "forward-events.jsonl", "document-vectors.jsonl", "query-vectors.jsonl")
                if (output / name).is_file()}

    try:
        requests = prepared["feature_requests"]
        metadata = _model(embedder.metadata)
        require(metadata == binding["model_binding"]["metadata"]
                and model_fingerprint(metadata) == binding["model_binding"]["fingerprint"],
                "Executing embedder metadata differs from frozen model binding")
        forwards = Ledger(output / "forward-events.jsonl")
        documents_log = Ledger(output / "document-vectors.jsonl")
        queries_log = Ledger(output / "query-vectors.jsonl")
        write_new(output / "capture-start.json", {"schema": SCHEMA, "status": "started", "started_at": started,
            "binding": binding, "feature_requests_sha256": digest(canonical(requests)),
            "document_order": [row["document_id"] for row in requests["document_requests"]],
            "query_order": [row["request_id"] for row in requests["query_requests"]],
            "classification": "Fresh observed-source feature encoding; no retrieval/native warm claim"})
        stage, request_id = "documents", "documents"
        required_documents = requests["document_requests"]
        texts = []
        for row in required_documents:
            require(digest(row["text"].encode()) == row["document_sha256"], "Document wrapper SHA differs")
            texts.append(row["text"])

        def checkpoint(indices, vectors):
            rows = []
            require(len(indices) == len(vectors), "Document batch checkpoint shape differs")
            for index, vector in zip(indices, vectors):
                item = required_documents[index]
                require(item["document_id"] not in documents, "Duplicate document checkpoint")
                record = {"document_id": item["document_id"], "document_sha256": item["document_sha256"],
                          **vector_record(vector)}
                rows.append(record)
            documents_log.append({"schema": "semantic_document_batch_v1", "stage": stage,
                "request_id": request_id, "input_indices": [int(value) for value in indices], "documents": rows})
            documents.update((row["document_id"], row) for row in rows)

        phase = time.perf_counter()
        if texts:
            check_metadata()
            calls["document_api_calls"] += 1
            values = embedder.embed_documents(texts, batch_callback=checkpoint, forward_observer=observe)
            require(len(values) == len(texts) and len(documents) == len(texts),
                    "Every document requires an actual durable native-batch checkpoint")
            for item, vector in zip(required_documents, values):
                require(documents[item["document_id"]] == {"document_id": item["document_id"],
                    "document_sha256": item["document_sha256"], **vector_record(vector)},
                    "Returned document vector differs from its actual checkpoint")
            check_metadata()
        timings["document_feature_capture_s"] = time.perf_counter() - phase
        for item in requests["query_requests"]:
            query = item["query"]
            facets = expected_facets(query)
            require(item["facets"] == facets and bool(facets), "Literal query request facets differ")
            request_id = item["request_id"]
            require(request_id not in queries, "Duplicate query feature request")
            phase = time.perf_counter()
            stage = "query_full"
            check_metadata()
            offset = len(forwards.rows)
            calls["full_query_api_calls"] += 1
            full = vector_record(embedder.embed_query(query, forward_observer=observe))
            full_events = [row["event"] for row in forwards.rows[offset:]]
            queries_log.append({"schema": "semantic_query_checkpoint_v1", "stage": stage,
                "request_id": request_id, "query": query, "facets": [query], "vectors": [full["vector"]],
                "float32_le_sha256": [full["float32_le_sha256"]]})
            check_metadata()
            stage = "query_clauses"
            offset = len(forwards.rows)
            calls["clause_api_calls"] += 1
            clauses = [vector_record(vector) for vector in embedder.embed_queries(facets[1:], forward_observer=observe)]
            require(len(clauses) == len(facets) - 1, "Clause vector count differs")
            clause_events = [row["event"] for row in forwards.rows[offset:]]
            queries_log.append({"schema": "semantic_query_checkpoint_v1", "stage": stage,
                "request_id": request_id, "query": query, "facets": facets[1:],
                "vectors": [row["vector"] for row in clauses],
                "float32_le_sha256": [row["float32_le_sha256"] for row in clauses]})
            check_metadata()
            queries[request_id] = {"query": query, "facets": facets,
                "vectors": [full["vector"], *(row["vector"] for row in clauses)],
                "vector_hashes": [full["float32_le_sha256"], *(row["float32_le_sha256"] for row in clauses)],
                "origin": "fresh_historical_query_feature_encoding", "historical_query_vector_present": False,
                "full_query_forward_events": full_events, "clause_forward_events": clause_events,
                "literal_prompts_sha256": [digest((QUERY_PROMPT + facet).encode()) for facet in facets]}
            timings[request_id] = {"query_feature_capture_s": time.perf_counter() - phase}
        stage, request_id = "finalization", None
        from semantic_search.semantic_provider import _forward_summary

        if texts:
            _forward_summary([row["event"] for row in forwards.rows if row["stage"] == "documents"], texts, metadata)
        for item in requests["query_requests"]:
            for name, facets in (("query_full", item["facets"][:1]), ("query_clauses", item["facets"][1:])):
                events = [row["event"] for row in forwards.rows
                          if row["stage"] == name and row["request_id"] == item["request_id"]]
                _forward_summary(events, [QUERY_PROMPT + facet for facet in facets], metadata)
        finish_once()
        report = {"schema": SCHEMA, "status": "passed", "started_at": started, "completed_at": utc(),
            "binding": binding, "feature_requests_sha256": digest(canonical(requests)),
            "model_metadata": metadata, "model_fingerprint": model_fingerprint(metadata),
            "documents": documents, "queries": queries, "forward_summary": _summary(forwards.rows, calls),
            "calls": calls, "timings": timings, "feature_capture_total_s": time.perf_counter() - begin,
            "artifact_sha256": artifacts(), "historical_query_vector_present": False,
            "feature_reuse_policy": "Reuse fresh captured features across recorded case occurrences; no extra retrieval",
            "classification": "Fresh observed-source features; separate CPU proposals; no native warm/end-to-end claim"}
        write_new(output / "capture.json", report)
        return report
    except BaseException as error:
        finish_error = None
        try:
            finish_once()
        except BaseException as failure:
            finish_error = failure
        failure = {"schema": SCHEMA, "status": "failed", "started_at": started, "failed_at": utc(),
            "stage": stage, "request_id": request_id,
            "error_type": f"{type(error).__module__}.{type(error).__qualname__}",
            "binding": binding, "calls": calls, "forward_summary": _summary([] if forwards is None else forwards.rows, calls),
            "durable_document_vectors": len(documents),
            "durable_query_stage_checkpoints": 0 if queries_log is None else len(queries_log.rows),
            "artifact_sha256": artifacts(), "finish_error_type": None if finish_error is None else
                f"{type(finish_error).__module__}.{type(finish_error).__qualname__}"}
        try:
            write_new(output / "failure.json", failure)
        except BaseException as ledger_error:
            raise error from ledger_error
        if finish_error is not None:
            raise error from finish_error
        raise
    finally:
        for ledger in (forwards, documents_log, queries_log):
            if ledger is not None:
                ledger.close()


def read_capture(folder):
    folder = Path(folder)
    report = json.loads((folder / "capture.json").read_bytes())
    require(report.get("status") == "passed" and not (folder / "failure.json").exists(),
            "Complete successful model feature capture required")
    for name, expected in report["artifact_sha256"].items():
        require(file_sha(folder / name) == expected, "Feature capture artifact changed: " + name)
    return report


def read_jsonl(path):
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def evidence(case, response):
    """Unchanged line-qualified group criteria used by the historical study."""
    from semantic_search.source import physical_lines

    ranks = []
    for alternatives in case["groups"]:
        hit = None
        for index, row in enumerate(response["results"], 1):
            for target in alternatives:
                low, high = target["evidence_lines"]
                if row["path"] != target["path"] or not row["start_line"] <= low <= high <= row["end_line"]:
                    continue
                if case["qualified"]:
                    source = "".join(physical_lines(row["source"])[low-row["start_line"]:high-row["start_line"]+1])
                    if row["symbol"] != target["symbol"] or digest(source.encode()) != target["evidence_sha256"]:
                        continue
                hit = index
                break
            if hit is not None:
                break
        ranks.append(hit)
    positive = case["kind"] == "present"
    return {"evidence_ranks": ranks, "covered_groups": sum(value is not None for value in ranks),
            "complete": positive and bool(ranks) and None not in ranks,
            "single_file_first_hit": positive and not case["multi_file"] and bool(ranks) and ranks[0] is not None}


def compare(control, candidate):
    before, after = control["evidence_ranks"], candidate["evidence_ranks"]
    require(len(before) == len(after), "Quality evidence-group arity differs")
    return {"group_gains": [i + 1 for i, (a, b) in enumerate(zip(before, after)) if a is None and b is not None],
            "group_losses": [i + 1 for i, (a, b) in enumerate(zip(before, after)) if a is not None and b is None],
            "complete_gain": candidate["complete"] and not control["complete"],
            "complete_loss": control["complete"] and not candidate["complete"],
            "single_file_first_hit_loss": control["single_file_first_hit"] and not candidate["single_file_first_hit"]}


def replay(prepared, capture_report, output, *, counter=None, audit_hook=None, binding=None):
    """One frozen proposal per case; controls are consumed, never recomputed."""
    from semantic_search.context import compact_result, encoding, serialize_context
    from semantic_search.semantic_context import semantic_final_context
    from semantic_search.semantic_features import make_features

    require(capture_report.get("status") == "passed", "Successful feature capture required")
    output = Path(output)
    output.mkdir(exist_ok=False)
    directory = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    counter = encoding() if counter is None else counter
    ledger = Ledger(output / "outcomes.jsonl.gz", compressed=True)
    feature_cases = {row["case_id"]: row for row in prepared["feature_requests"]["cases"]}
    require(len(feature_cases) == len(prepared["cases"]), "Complete feature case inventory required")
    gains, changes, by_condition = set(), {"legacy": [], "coverage_control": []}, {}
    start = time.perf_counter()
    write_new(output / "replay-start.json", {"schema": "semantic_model_replay_v1", "started_at": utc(),
        "binding": binding, "feature_capture_sha256": digest(canonical(capture_report)),
        "classification": "CPU proposals from frozen source/ranks/features; no retrieval/native latency"})
    try:
        for case in prepared["cases"]:
            phase = time.perf_counter()
            inventory = feature_cases[case["case_id"]]
            require(inventory["query_request_id"] == case["feature_request_id"], "Case feature request differs")
            features = None
            if case["feature_request_id"] is not None:
                query = capture_report["queries"][case["feature_request_id"]]
                require(query["query"] == case["query"], "Captured feature query differs")
                vectors = {identifier: capture_report["documents"][document_id]["vector"]
                           for identifier, document_id in inventory["window_documents"].items()}
                features = make_features(case["query"], case["raw_rows"], case["reference_response"],
                    query["vectors"], vectors, capture_report["model_metadata"])
            response, receipt = semantic_final_context(deepcopy(case["reference_response"]),
                deepcopy(case["raw_rows"]), features, definition_keys=case["definition_keys"],
                precoalesce_rows=deepcopy(case["precoalesce_rows"]), **case["limits"])
            compact = serialize_context(compact_result(response))
            result = {"case_id": case["case_id"], "key": case["key"],
                **{name: case[name] for name in ("subset", "dataset", "query_id", "condition", "kind", "multi_file", "qualified")},
                "query_request_id": case["feature_request_id"], "window_documents": inventory["window_documents"],
                "reference_response": case["reference_response"], "ranked_candidates": case["raw_rows"],
                "precoalesce_rows": case["precoalesce_rows"], "definition_keys": case["definition_keys"],
                "features_sha256": features_sha256(features), "limits": case["limits"],
                "response": response, "semantic_receipt": receipt,
                "coverage_coalesce_calls": 0, "reference_coverage_coalesce_calls": 1, "semantic_calls": 1,
                "feature_calls": int(features is not None), "subsequent_backfill_calls": 0,
                "calls": {name: 0 for name in ("model", "retrieval", "index", "network")},
                "compact_payload": compact, "compact_sha256": digest(compact.encode()),
                "actual_compact_tokens": len(counter.encode(compact, disallowed_special=())),
                "proposal_s": time.perf_counter() - phase}
            score = evidence(case, response)
            result.update(score)
            result["comparisons"] = {name: compare(evidence(case, control), score) for name, control in
                (("legacy", case["legacy_control"]), ("coverage_control", case["reference_response"]))}
            for name, difference in result["comparisons"].items():
                if difference["group_losses"] or difference["complete_loss"] or difference["single_file_first_hit_loss"]:
                    changes[name].append({"case_id": case["case_id"], **difference})
            if case["multi_file"] and result["comparisons"]["coverage_control"]["complete_gain"]:
                gains.add((case["dataset"], case["query_id"]))
            for name, control in (("legacy", case["legacy_control"]), ("coverage_control", case["reference_response"]),
                                  ("semantic_final_v1", response)):
                key = "/".join(case[field] for field in ("subset", "dataset", "condition")) + "/" + name
                metric = by_condition.setdefault(key, {"rows": 0, "required_groups": 0, "covered_groups": 0,
                    "complete": 0, "multi_file_complete": 0, "single_file_first_hit": 0})
                value = evidence(case, control)
                metric["rows"] += 1
                metric["required_groups"] += len(case["groups"])
                for field in ("covered_groups", "complete", "single_file_first_hit"):
                    metric[field] += value[field]
                metric["multi_file_complete"] += bool(case["multi_file"] and value["complete"])
            if audit_hook is not None:
                audit_hook(case, result, features)
            ledger.append(result)
        ledger.close()  # The final gzip trailer is part of the immutable hash.
        units = {(case["dataset"], case["query_id"]) for case in prepared["cases"]}
        summary = {"schema": "semantic_model_replay_v1", "status": "passed", "stored_candidate_rows": len(ledger.rows),
            "distinct_dataset_query_units": len(units), "referenced_control_rows": 2 * len(ledger.rows),
            "quality_gate": {"qualified": bool(gains) and not any(changes.values()), "individual_losses": changes,
                "additional_distinct_complete_multi_queries_vs_coverage": [list(value) for value in sorted(gains)],
                "automatic_default_change": False, "fresh_and_native_validation_still_required": True},
            "metrics_by_original_condition": by_condition, "cpu_replay_total_s": time.perf_counter() - start,
            "candidate_phase_calls": {name: 0 for name in ("model", "retrieval", "index", "network", "backfill")},
            "feature_reuse": {"fresh_query_requests": len(capture_report["queries"]),
                "fresh_document_vectors": len(capture_report["documents"]),
                "active_case_occurrences": sum(case["feature_request_id"] is not None for case in prepared["cases"])},
            "artifact_sha256": {"outcomes.jsonl.gz": file_sha(output / "outcomes.jsonl.gz"),
                                "replay-start.json": file_sha(output / "replay-start.json")},
            "classification": "Observed-source CPU proposal replay; feature timing is separate; no native warm claim"}
        write_new(output / "summary.json", summary)
        return summary
    except BaseException as error:
        try:
            ledger.close()
            write_new(output / "failure.json", {"status": "failed", "error_type":
                f"{type(error).__module__}.{type(error).__qualname__}", "stored_rows": len(ledger.rows),
                "outcomes_sha256": file_sha(output / "outcomes.jsonl.gz")})
        except BaseException as failure:
            raise error from failure
        raise
    finally:
        if not ledger.closed:
            original_error = sys.exception()
            try:
                ledger.close()
            except BaseException as close_error:
                if original_error is not None:
                    raise original_error from close_error
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--capture", metavar="PREPARED")
    modes.add_argument("--replay", metavar="PREPARED")
    parser.add_argument("--output", required=True)
    parser.add_argument("--freeze", required=True)
    parser.add_argument("--runtime-manifest")
    parser.add_argument("--model-path")
    parser.add_argument("--features", help="Successful feature capture folder for replay")
    args = parser.parse_args()
    import semantic_study_inputs as inputs

    prepared_path = Path(args.capture or args.replay).resolve()
    freeze = inputs.verify_frozen(prepared_path, args.freeze, root=ROOT)
    require(not git(ROOT, "status", "--porcelain", "--untracked-files=no"), "Tracked source is dirty")
    prepared = inputs.load_prepared(prepared_path)
    if args.capture:
        require(args.runtime_manifest and args.model_path, "Capture requires explicit runtime manifest and model path")
        require(not Path(args.output).exists(), "Capture output already exists; preserve the original attempt")
        runtime = verify_runtime(ROOT, args.runtime_manifest, args.freeze, args.model_path)
        from semantic_search.embeddings import QwenEmbedder

        lease, claimed = None, False

        def on_claim():
            nonlocal claimed
            claimed = True

        try:
            with gpu_lease(ROOT) as lease:
                embedder = QwenEmbedder(model_path=args.model_path, batch_size=4, progress=False, max_padded_tokens=8192)

                def finish():
                    try:
                        inputs.verify_frozen(prepared_path, args.freeze, root=ROOT)
                        require(not git(ROOT, "status", "--porcelain", "--untracked-files=no"),
                                "Tracked source changed during model capture")
                        verify_runtime(ROOT, args.runtime_manifest, args.freeze, args.model_path)
                    finally:
                        close_preserving_error(embedder)

                try:
                    result = capture(prepared, args.output, embedder=embedder,
                        binding={"freeze": freeze, "runtime": runtime, "model_binding": runtime["model_binding"],
                                 "execution_commit": git(ROOT, "rev-parse", "HEAD"), "gpu_lease": lease},
                        finish=finish, on_claim=on_claim)
                finally:
                    close_preserving_error(embedder)
        finally:
            output = Path(args.output)
            if claimed and lease is not None and lease["acquired_at"] is not None:
                # This is separate because release occurs after model close and
                # after the immutable successful/failed feature receipt.
                original_error = sys.exception()
                try:
                    write_new(output / "gpu-lease-released.json", lease)
                except BaseException as release_error:
                    if original_error is not None:
                        raise original_error from release_error
                    raise
    else:
        require(args.features, "Replay requires explicit successful feature capture")
        from audit_semantic_study import verify_capture

        report = read_capture(args.features)
        folder = Path(args.features)
        verify_capture(report, prepared["feature_requests"],
            forward_events=read_jsonl(folder / "forward-events.jsonl"),
            document_checkpoints=read_jsonl(folder / "document-vectors.jsonl"),
            query_checkpoints=read_jsonl(folder / "query-vectors.jsonl"))
        result = replay(prepared, report, args.output,
                        binding={"freeze": freeze, "execution_commit": git(ROOT, "rev-parse", "HEAD"),
                                 "features_path": str(Path(args.features).resolve())})
    print(json.dumps({"status": result["status"], "output": str(Path(args.output).resolve())}))


if __name__ == "__main__":
    main()
