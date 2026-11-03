#!/usr/bin/env python3
"""Independent semantic study audit; no producer, selector, model or NumPy import.

Historical input readers and previously committed independent control/context
auditors are the trust boundary. Vectors are exact float32 values, never inferred
by this module. Replay quality is separate from native warm latency.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime
import gzip
import hashlib
import importlib.metadata
import importlib.abc
import json
import math
from pathlib import Path
import platform
import re
import socket
import struct
import subprocess
import sys
from unittest.mock import patch

import audit_semantic_context as context

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "semantic_model_study_audit_v1"
CONTROLS = ("legacy", "coverage_control")
LIMITS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096}
FRESH_ORIGIN = "fresh_historical_query_feature_encoding"
WEIGHTS_SHA256 = "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"
POLICY = "fresh_singleton_full_query_then_within_query_batched_clauses; historical retrieval query vectors absent"
RETRIEVALS = {"bm25": "bm25", "dense": "dense", "hybrid": "hybrid", "hybrid_k24_b1": "hybrid"}
MAPPING_SOURCES = ("results/language-quality-2026-10-13/frozen-config.json",
    "results/qwen-shortlist-development-2026-10-14/frozen-plan.json",
    "results/qwen-shortlist-httpx-2026-10-14/frozen-plan.json")
ARTIFACTS = ("operands.json", "feature-requests.json", "control-parity.json", "control-references.json",
             "negative-references.json", "input-manifest.json")


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def jsonl(path):
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def same(first, second):
    return context.same(first, second)


def vector_hash(vector):
    values = context.float32_vector(vector)
    require(len(values) == context.DIMENSIONS, "Pinned vector dimensions differ")
    return sha(struct.pack(f"<{len(values)}f", *values))


def model_fingerprint(metadata):
    return sha(json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def verify_metadata(metadata):
    require(isinstance(metadata, dict) and metadata.get("id") == context.MODEL_ID
            and metadata.get("revision") == context.MODEL_REVISION
            and metadata.get("dimensions") == context.DIMENSIONS
            and type(metadata.get("dimensions")) is int
            and metadata.get("query_prompt") == context.QUERY_PROMPT
            and metadata.get("normalization") == "L2, float32"
            and metadata.get("weights_dtype") == "bfloat16"
            and metadata.get("quantized") is False
            and metadata.get("document_prompt") is None,
            "Pinned original model identity differs")
    context.json_data(metadata)
    for field in ("batch_size", "max_tokens", "max_padded_tokens"):
        require(type(metadata.get(field)) is int and metadata[field] > 0,
                "Explicit native batching metadata required")
    require(metadata["max_tokens"] == 8192 and metadata["max_padded_tokens"] == 8192 and metadata["batch_size"] == 4,
            "Original token limit or padded budget differs")
    return model_fingerprint(metadata)


def verify_model_binding(binding, *, verify_files=True, verify_runtime=True):
    """Verify frozen checkpoint bytes and the actually installed runtime.

    Tests can disable filesystem/runtime observations for invented bindings.
    The real study entry point always performs both; a provenance JSON alone is
    insufficient. Hub revision and LFS SHA must agree with the actual weights.
    """
    require(isinstance(binding, dict), "Frozen model binding required")
    metadata = binding.get("metadata")
    fingerprint = verify_metadata(metadata)
    require(binding.get("fingerprint") == fingerprint, "Model fingerprint differs")
    checkpoint, runtime = binding.get("checkpoint"), binding.get("runtime")
    require(isinstance(checkpoint, dict) and isinstance(runtime, dict), "Checkpoint/runtime binding required")
    files = checkpoint.get("files")
    required = {"config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
                "semantic_search_checkpoint.json", ".cache/huggingface/download/model.safetensors.metadata"}
    require(isinstance(files, dict) and required <= set(files), "Complete checkpoint byte proof required")
    root = Path(checkpoint["path"]).resolve()
    if verify_files:
        require({path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()} == set(files),
                "Complete actual checkpoint file inventory differs")
    for name, record in files.items():
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts and relative.as_posix() == name,
                "Checkpoint path must stay inside the frozen checkpoint")
        require(isinstance(record, dict) and type(record.get("bytes")) is int and record["bytes"] >= 0
                and isinstance(record.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]),
                "Checkpoint file hash schema differs")
        if verify_files:
            path = root / relative
            require(path.resolve().is_relative_to(root) and path.is_file()
                    and path.stat().st_size == record["bytes"] and file_sha(path) == record["sha256"],
                    "Actual checkpoint bytes differ: " + name)
    if verify_files:
        config = read(root / "config.json")
        provenance = read(root / "semantic_search_checkpoint.json")
        download = (root / ".cache/huggingface/download/model.safetensors.metadata").read_text().splitlines()
        require(config.get("model_type") == "qwen3" and config.get("hidden_size") == context.DIMENSIONS
                and not config.get("quantization") and not config.get("quantization_config"),
                "Checkpoint configuration differs")
        require(len(download) >= 2 and download[0] == context.MODEL_REVISION
                and download[1] == files["model.safetensors"]["sha256"]
                and provenance.get("weights_etag") == download[1]
                and provenance.get("id") == context.MODEL_ID and provenance.get("revision") == context.MODEL_REVISION
                and provenance.get("weights_dtype") == "bfloat16" and provenance.get("quantized") is False,
                "Pinned Hub revision/LFS SHA/provenance differs from actual weights")
    packages = runtime.get("packages")
    require(isinstance(packages, dict) and isinstance(metadata.get("packages"), dict)
            and all(packages.get(name) == version for name, version in metadata["packages"].items()),
            "Runtime packages differ from model metadata")
    if verify_runtime:
        require(runtime.get("python") == platform.python_version()
                and Path(runtime.get("executable", "")).resolve() == Path(sys.executable).resolve()
                and runtime.get("platform") == platform.platform(), "Actual Python/runtime identity differs")
        for name, version in packages.items():
            try:
                actual = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                actual = None
            require(actual == version, "Actual runtime package differs: " + name)
    return {"model_fingerprint": fingerprint, "checkpoint_files": len(files),
            "actual_checkpoint_verified": verify_files, "actual_runtime_verified": verify_runtime}


def verify_forward_events(events, texts, metadata, *, singleton=False):
    """Independently verify every successful actual native forward receipt."""
    verify_metadata(metadata)
    require(isinstance(events, list) and len(events) % 2 == 0, "Complete forward event pairs required")
    if not texts:
        require(not events, "Empty request must not forward")
        return {"api_calls": 0, "model_forward_calls": 0, "encoded_texts": 0}
    require(bool(events), "Nonempty request requires native forward receipts")
    seen, lengths_by_index, groups = [], {}, []
    last_elapsed = -1.0
    for number in range(0, len(events), 2):
        start, complete = events[number:number + 2]
        for event, status in ((start, "started"), (complete, "completed")):
            require(isinstance(event, dict) and event.get("schema") == "qwen_embedding_forward_v1"
                    and event.get("status") == status and type(event.get("forward_index")) is int
                    and event["forward_index"] == number // 2, "Actual forward order/status differs")
            elapsed = event.get("elapsed_seconds")
            require(type(elapsed) is float and math.isfinite(elapsed) and elapsed >= last_elapsed and elapsed >= 0,
                    "Forward time must be finite and monotonic")
            last_elapsed = elapsed
        keys = ("input_count", "batch_input_count", "input_indices", "token_lengths", "padded_tokens",
                "padded_shape", "expected_output_shape", "input_sha256", "token_ids_sha256")
        require(all(name in start and same(start[name], complete.get(name)) for name in keys),
                "Forward identity changed between start and completion")
        indices, lengths = start["input_indices"], start["token_lengths"]
        require(type(start["input_count"]) is int and start["input_count"] == len(texts)
                and isinstance(indices, list) and indices and all(type(i) is int and 0 <= i < len(texts) for i in indices)
                and isinstance(lengths, list) and len(lengths) == len(indices)
                and all(type(n) is int and 0 < n <= metadata["max_tokens"] for n in lengths)
                and type(start["batch_input_count"]) is int and start["batch_input_count"] == len(indices),
                "Forward input count/indices/lengths differ")
        require(start["input_sha256"] == [sha(texts[i].encode()) for i in indices], "Actual prompted input hash differs")
        require(isinstance(start["token_ids_sha256"], list) and len(start["token_ids_sha256"]) == len(indices)
                and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                        for value in start["token_ids_sha256"]), "Truncated token hash schema differs")
        require(len(indices) <= metadata["batch_size"] and same(start["padded_shape"], [len(indices), max(lengths)])
                and type(start["padded_tokens"]) is int and start["padded_tokens"] == len(indices) * max(lengths)
                and start["padded_tokens"] <= metadata["max_padded_tokens"], "Actual padded batch exceeds limits")
        shape = [len(indices), context.DIMENSIONS]
        require(same(start["expected_output_shape"], shape) and same(complete.get("output_shape"), shape)
                and complete.get("output_dtype") == "float32", "Actual output shape/dtype differs")
        duration = complete.get("duration_seconds")
        require(type(duration) is float and math.isfinite(duration) and 0 <= duration
                <= complete["elapsed_seconds"] - start["elapsed_seconds"], "Actual forward duration differs")
        seen.extend(indices)
        lengths_by_index.update(zip(indices, lengths))
        groups.append(indices)
    require(sorted(seen) == list(range(len(texts))), "Each input must be forwarded exactly once")
    order = sorted(range(len(texts)), key=lambda i: (lengths_by_index[i], i))
    expected, position = [], 0
    while position < len(order):
        end = min(position + metadata["batch_size"], len(order))
        while lengths_by_index[order[end - 1]] * (end - position) > metadata["max_padded_tokens"]:
            end -= 1
        expected.append(order[position:end])
        position = end
    require(same(groups, expected), "Actual token-order batching differs from frozen schedule")
    if singleton:
        require(len(texts) == 1 and groups == [[0]], "Full historical query feature must use a singleton forward")
    return {"api_calls": 1, "model_forward_calls": len(groups), "encoded_texts": len(texts)}


def _unique(rows, name):
    require(isinstance(rows, list) and all(isinstance(row, dict) and isinstance(row.get(name), str) for row in rows),
            "Explicit unique request records required")
    values = {row[name]: row for row in rows}
    require(len(values) == len(rows), "Duplicate request identity")
    return values


def verify_capture(capture, feature_requests, *, forward_events, document_checkpoints, query_checkpoints):
    """Bind fresh feature vectors to exact request/document and durable ledgers."""
    require(isinstance(capture, dict) and capture.get("status") == "passed", "Complete feature capture required")
    metadata = capture.get("model_metadata")
    fingerprint = verify_metadata(metadata)
    require(capture.get("model_fingerprint") == fingerprint, "Capture model fingerprint differs")
    documents = _unique(feature_requests["document_requests"], "document_id")
    queries = _unique(feature_requests["query_requests"], "request_id")
    require(set(capture.get("documents", {})) == set(documents) and set(capture.get("queries", {})) == set(queries),
            "Exact document/query feature membership differs")
    for identity, request in documents.items():
        digest = sha(request["text"].encode())
        require(identity == "doc_" + digest and request["document_sha256"] == digest,
                "Exact document wrapper hash differs")
        actual = capture["documents"][identity]
        require(actual.get("document_id") == identity and actual.get("document_sha256") == digest
                and actual.get("float32_le_sha256") == vector_hash(actual["vector"]),
                "Captured document vector/hash differs")
    grouped, global_order = {}, []
    for sequence, wrapper in enumerate(forward_events):
        require(isinstance(wrapper, dict) and type(wrapper.get("sequence")) is int and wrapper["sequence"] == sequence,
                "Durable global forward sequence differs")
        stage, identity = wrapper.get("stage"), wrapper.get("request_id")
        require(stage in ("documents", "query_full", "query_clauses") and isinstance(identity, str),
                "Native forward role/request differs")
        grouped.setdefault((stage, identity), []).append(wrapper.get("event"))
        global_order.append((stage, identity))
    expected_roles = ({("documents", "documents")} if documents else set()) | {
        (stage, identity) for identity in queries for stage in ("query_full", "query_clauses")}
    require(set(grouped) == expected_roles, "Missing or extra native inference request")
    doc_events = grouped.get(("documents", "documents"), [])
    counts = {"documents": verify_forward_events(doc_events, [r["text"] for r in documents.values()], metadata),
              "query_full": {"api_calls": 0, "model_forward_calls": 0, "encoded_texts": 0},
              "query_clauses": {"api_calls": 0, "model_forward_calls": 0, "encoded_texts": 0}}
    expected_order = [("documents", "documents")] * len(doc_events)
    checkpoint_docs = {}
    require(len(document_checkpoints) == len(doc_events) // 2, "One durable document checkpoint per native forward required")
    doc_ids = list(documents)
    for ordinal, checkpoint in enumerate(document_checkpoints):
        indices = doc_events[2 * ordinal]["input_indices"]
        require(same(checkpoint.get("input_indices"), indices), "Document checkpoint input order differs")
        values = checkpoint.get("documents")
        require(isinstance(values, list) and len(values) == len(indices), "Document checkpoint count differs")
        for index, actual in zip(indices, values):
            identity = doc_ids[index]
            require(actual.get("document_id") == identity and identity not in checkpoint_docs,
                    "Document checkpoint identity differs")
            require(same(actual, capture["documents"][identity]),
                    "Final document vector differs from actual native checkpoint")
            checkpoint_docs[identity] = actual
    require(set(checkpoint_docs) == set(documents), "Complete document checkpoint membership required")
    query_stage_records = {}
    for value in query_checkpoints:
        key = value.get("stage"), value.get("request_id")
        require(key not in query_stage_records, "Duplicate query stage checkpoint")
        query_stage_records[key] = value
    require(set(query_stage_records) == {(stage, identity) for identity in queries for stage in ("query_full", "query_clauses")},
            "Complete fresh query stage checkpoints required")
    for identity, request in queries.items():
        query, facets = request["query"], request["facets"]
        clauses = context.literal_clauses(query)
        require(clauses and same(facets, [query, *clauses])
                and identity == "query_" + sha(canonical({"query": query, "facets": facets, "query_prompt": context.QUERY_PROMPT})),
                "Original full query/facet order/request identity differs")
        actual = capture["queries"][identity]
        require(actual.get("query") == query and same(actual.get("facets"), facets)
                and actual.get("origin") == FRESH_ORIGIN and actual.get("historical_query_vector_present") is False,
                "Fresh historical query features must not claim original retrieval-vector reuse")
        vectors = actual.get("vectors")
        require(isinstance(vectors, list) and len(vectors) == len(facets), "Fresh facet vector membership differs")
        for vector in vectors:
            vector_hash(vector)
        require(actual.get("vector_hashes") == [vector_hash(vector) for vector in vectors]
                and actual.get("literal_prompts_sha256") == [sha((context.QUERY_PROMPT + facet).encode()) for facet in facets],
                "Captured facet vector/prompt hash differs")
        for stage, field, texts, expected_vectors in (
            ("query_full", "full_query_forward_events", [context.QUERY_PROMPT + query], vectors[:1]),
            ("query_clauses", "clause_forward_events", [context.QUERY_PROMPT + clause for clause in clauses], vectors[1:])):
            events = grouped[stage, identity]
            require(same(actual.get(field), events), "Captured query events differ from durable native ledger")
            count = verify_forward_events(events, texts, metadata, singleton=stage == "query_full")
            for name, number in count.items():
                counts[stage][name] += number
            expected_order += [(stage, identity)] * len(events)
            checkpoint = query_stage_records[stage, identity]
            require(same(checkpoint.get("vectors"), expected_vectors)
                    and checkpoint.get("float32_le_sha256") == [vector_hash(v) for v in expected_vectors],
                    "Final fresh query vector differs from native stage checkpoint")
    require(global_order == expected_order, "Actual document/full-query/clause chronology differs")
    counts["total_model_forward_calls"] = sum(counts[stage]["model_forward_calls"] for stage in ("documents", "query_full", "query_clauses"))
    counts["total_encoded_texts"] = sum(counts[stage]["encoded_texts"] for stage in ("documents", "query_full", "query_clauses"))
    require(same(capture.get("forward_summary"), counts), "Actual feature API/forward/text counts differ")
    require(same(capture.get("calls"), {"document_api_calls": int(bool(documents)), "full_query_api_calls": len(queries),
            "clause_api_calls": len(queries), "retrieval_calls": 0, "index_calls": 0, "cache_hits": 0}),
            "Feature capture must use only fresh declared embedding APIs")
    return counts


def features_for_case(case, capture, request):
    if case.get("feature_request_id") is None:
        require(request.get("active") is False and request.get("query_request_id") is None
                and not request.get("window_documents") and not request.get("window_bindings"),
                "Inactive case must have no feature work")
        return None
    identity = case["feature_request_id"]
    require(request.get("active") is True and request.get("query_request_id") == identity,
            "Actual case feature request differs")
    query = capture["queries"][identity]
    windows = {}
    rows = {}
    for row in [*case["raw_rows"], *case["reference_response"]["results"]]:
        if context.clipped(row):
            continue
        binding = context.row_binding(row)
        require(row["id"] not in rows or same(rows[row["id"]], binding), "Conflicting current full source identity")
        rows[row["id"]] = binding
    require(set(request.get("window_documents", {})) == set(rows)
            and same(request.get("window_bindings"), rows), "Exact current full-window feature source/provenance differs")
    for identifier, binding in rows.items():
        doc_id = request["window_documents"][identifier]
        require(doc_id == "doc_" + binding["document_sha256"], "Window document wrapper differs")
        windows[identifier] = {"binding": binding, "vector": capture["documents"][doc_id]["vector"]}
    result = {"schema": context.FEATURE_SCHEMA, "query": case["query"], "facets": query["facets"],
              "query_prompt": context.QUERY_PROMPT, "model_metadata": capture["model_metadata"],
              "model_fingerprint": capture["model_fingerprint"], "facet_vectors": query["vectors"], "windows": windows}
    context.audit_features(case["reference_response"], case["raw_rows"], result)
    return result


def quality(case, response):
    ranks = []
    for alternatives in case["groups"]:
        hit = None
        for rank, row in enumerate(response["results"], 1):
            for target in alternatives:
                low, high = target["evidence_lines"]
                if row["path"] != target["path"] or not row["start_line"] <= low <= high <= row["end_line"]:
                    continue
                if case["qualified"]:
                    source = "".join(context.physical_lines(row["source"])[low-row["start_line"]:high-row["start_line"]+1])
                    if row["symbol"] != target["symbol"] or sha(source.encode()) != target["evidence_sha256"]:
                        continue
                hit = rank
                break
            if hit is not None:
                break
        ranks.append(hit)
    return {"ranks": ranks, "groups_hit": sum(rank is not None for rank in ranks),
            "complete": case["kind"] == "present" and bool(ranks) and None not in ranks,
            "first_hit": case["kind"] == "present" and bool(ranks) and ranks[0] is not None}


def quality_gate(cases, responses):
    comparisons, gains = {}, set()
    for control in CONTROLS:
        losses, complete_losses, first_losses, complete_gains = [], [], [], []
        for case in cases:
            before = quality(case, case["legacy_control"] if control == "legacy" else case["reference_response"])
            after = quality(case, responses[case["case_id"]])
            identity = {name: case[name] for name in ("subset", "dataset", "query_id", "condition")}
            lost = [i for i, (a, b) in enumerate(zip(before["ranks"], after["ranks"]), 1) if a is not None and b is None]
            if lost:
                losses.append({**identity, "groups": lost})
            if before["complete"] and not after["complete"]:
                complete_losses.append(identity)
            if not case["multi_file"] and before["first_hit"] and not after["first_hit"]:
                first_losses.append(identity)
            if case["multi_file"] and after["complete"] and not before["complete"]:
                complete_gains.append(identity)
                if control == "coverage_control":
                    gains.add((case["dataset"], case["query_id"]))
        comparisons[control] = {"group_losses": losses, "complete_losses": complete_losses,
                               "single_file_first_hit_losses": first_losses, "complete_multi_gains": complete_gains}
    passed = bool(gains) and not any(value[field] for value in comparisons.values()
        for field in ("group_losses", "complete_losses", "single_file_first_hit_losses"))
    return {"qualified": passed, "against_controls": comparisons,
            "additional_distinct_complete_multi_queries_vs_coverage": [list(value) for value in sorted(gains)],
            "occurrences": len(cases), "distinct_dataset_query_units": len({(c["dataset"], c["query_id"]) for c in cases}),
            "native_warm_latency_measured": False, "automatic_default_change": False}


def audit_outcomes(cases, outcomes, capture, feature_requests, *, counter, expected_rows=393, expected_units=201):
    by_id = _unique(cases, "case_id")
    actual = _unique(outcomes, "case_id")
    requests = _unique(feature_requests["cases"], "case_id")
    require(len(cases) == expected_rows and len({(c["dataset"], c["query_id"]) for c in cases}) == expected_units
            and set(actual) == set(by_id) == set(requests), "Complete occurrence/unit/outcome matrix required")
    responses = {}
    for identity, case in by_id.items():
        row = actual[identity]
        require(all(same(row.get(name), case[name]) for name in
                    ("subset", "dataset", "query_id", "condition", "kind", "multi_file", "qualified")),
                "Original occurrence/label semantics changed")
        require(same(row.get("key"), case["key"]) and same(row.get("limits"), case["limits"]) and same(case["limits"], LIMITS),
                "Original case identity or existing budget changed")
        features = features_for_case(case, capture, requests[identity])
        for name, trusted in (("reference_response", case["reference_response"]), ("ranked_candidates", case["raw_rows"]),
                              ("precoalesce_rows", case["precoalesce_rows"]), ("definition_keys", case["definition_keys"])):
            require(same(row.get(name), trusted), "Captured source/feature operand differs: " + name)
        require("features" not in row and row.get("features_sha256") == sha(canonical(features)),
                "Independently reconstructed canonical feature hash differs")
        expected, receipt = context.reconstruct(case["reference_response"], case["raw_rows"], features,
            definition_keys=case["definition_keys"], precoalesce_rows=case["precoalesce_rows"], counter=counter, **case["limits"])
        require(same(row.get("semantic_receipt"), receipt) and same(row.get("response"), expected),
                "Independent actual final-context allocation differs")
        payload = context.compact_payload(expected)
        require(row.get("compact_payload") == payload and row.get("compact_sha256") == sha(payload.encode())
                and type(row.get("actual_compact_tokens")) is int
                and row["actual_compact_tokens"] == len(counter.encode(payload, disallowed_special=()))
                == expected["token_usage"]["tokens"] <= case["limits"]["token_budget"], "Exact final compact source/token envelope differs")
        counts = {"coverage_coalesce_calls": 0, "reference_coverage_coalesce_calls": 1, "semantic_calls": 1,
                  "feature_calls": int(features is not None), "subsequent_backfill_calls": 0}
        for name, value in counts.items():
            require(type(row.get(name)) is int and row[name] == value, "Replay actual/reference call count differs: " + name)
        require(row.get("query_request_id") == case.get("feature_request_id")
                and same(row.get("window_documents"), requests[identity]["window_documents"]), "Outcome feature-request binding differs")
        require(same(row.get("calls"), {name: 0 for name in ("model", "retrieval", "index", "network")}),
                "CPU replay must perform no new model/retrieval/index/network work")
        value = quality(case, expected)
        score = {"evidence_ranks": value["ranks"], "covered_groups": value["groups_hit"], "complete": value["complete"],
                 "single_file_first_hit": not case["multi_file"] and value["first_hit"]}
        require(all(same(row.get(name), expected_value) for name, expected_value in score.items()),
                "Reported individual evidence score differs")
        comparisons = {}
        for name, control in (("legacy", case["legacy_control"]), ("coverage_control", case["reference_response"])):
            before = quality(case, control)
            comparisons[name] = {"group_gains": [i for i, (a, b) in enumerate(zip(before["ranks"], value["ranks"]), 1)
                                                  if a is None and b is not None],
                "group_losses": [i for i, (a, b) in enumerate(zip(before["ranks"], value["ranks"]), 1) if a is not None and b is None],
                "complete_gain": value["complete"] and not before["complete"],
                "complete_loss": before["complete"] and not value["complete"],
                "single_file_first_hit_loss": not case["multi_file"] and before["first_hit"] and not value["first_hit"]}
        require(same(row.get("comparisons"), comparisons), "Individual control comparison differs")
        responses[identity] = expected
    return quality_gate(cases, responses)


def independent_requests(cases):
    """Rebuild exact activation, prompts and current full-source membership."""
    queries, documents, mappings = {}, {}, []
    for case in cases:
        require(case["condition"] in RETRIEVALS and case.get("retrieval") == RETRIEVALS[case["condition"]],
                "Unknown or changed original retrieval condition")
        condition = case["retrieval"]
        clauses = context.literal_clauses(case["query"])
        reason = ("lexical_only" if condition == "bm25" or condition.startswith(("bm25_", "bm25-", "bm25+", "bm25/")) else
                  "abstained" if case["reference_response"].get("match_status") == "insufficient_evidence" else
                  "empty_raw_pool" if not case["raw_rows"] else
                  "empty_reference" if not case["reference_response"]["results"] else
                  "ineligible_query" if not clauses else None)
        request = {"case_id": case["case_id"], "active": reason is None, "reason": reason,
                   "query_request_id": None, "window_documents": {}, "window_bindings": {}}
        if reason is None:
            facets = [case["query"], *clauses]
            identity = "query_" + sha(canonical({"query": case["query"], "facets": facets, "query_prompt": context.QUERY_PROMPT}))
            if identity not in queries:
                queries[identity] = {"request_id": identity, "query": case["query"], "facets": facets, "case_ids": []}
            queries[identity]["case_ids"].append(case["case_id"])
            request["query_request_id"] = identity
            rows = {}
            for row in [*case["raw_rows"], *case["reference_response"]["results"]]:
                if context.clipped(row):
                    continue
                binding = context.row_binding(row)
                require(row["id"] not in rows or same(rows[row["id"]], binding), "Conflicting full source identity")
                rows[row["id"]] = binding
                text = context.document_text(row)
                doc_id = "doc_" + sha(text.encode())
                document = {"document_id": doc_id, "document_sha256": sha(text.encode()), "text": text}
                require(doc_id not in documents or same(documents[doc_id], document), "Document SHA collision")
                documents[doc_id] = document
                request["window_documents"][row["id"]] = doc_id
                request["window_bindings"][row["id"]] = binding
        case["feature_request_id"], case["inactive_reason"] = request["query_request_id"], reason
        mappings.append(request)
    return {"schema": "semantic_study_feature_requests_v1", "query_vector_policy": POLICY,
            "query_requests": [queries[key] for key in sorted(queries)],
            "document_requests": [documents[key] for key in sorted(documents)], "cases": mappings,
            "counts": {"active_cases": sum(r["active"] for r in mappings), "inactive_cases": sum(not r["active"] for r in mappings),
                       "query_requests": len(queries), "document_requests": len(documents)},
            "claims": {"retrieval_query_vector_reuse": False, "new_candidate_outcomes_computed": 0}}


def audit_preparation(root, prepared_dir, counter, *, expected_rows=393, expected_units=201,
                      expected_negatives=2948, expected_kinds=None):
    """Reconstruct all original controls before opening any candidate outcomes."""
    import complementary_inputs as data
    import audit_complementary_context as history

    root, folder = Path(root).resolve(), Path(prepared_dir).resolve()
    require(not (folder / "prepare-failure.json").exists(), "Failed preparation cannot qualify")
    plan = read(folder / "frozen-plan.json")
    require(plan.get("schema") == "semantic_study_inputs_v1" and same(plan.get("settings"), LIMITS)
            and plan.get("feature_policy") == POLICY, "Frozen control/feature preparation differs")
    require(set(plan.get("artifact_sha256", {})) == set(ARTIFACTS), "Complete preparation artifact closure required")
    for name, digest in plan["artifact_sha256"].items():
        require(file_sha(folder / name) == digest, "Prepared bytes changed: " + name)
    cases, controls, negatives, parents = data.load(root)
    kinds = {"present": 322, "absent": 71} if expected_kinds is None else expected_kinds
    require(len(cases) == expected_rows and len(controls) == 2 * expected_rows and len(negatives) == expected_negatives
            and len({(c["dataset"], c["query_id"]) for c in cases}) == expected_units
            and Counter(c["kind"] for c in cases) == kinds, "Fixed complete historical sample required")
    require(same(plan.get("counts"), data.counts(cases)) and same(plan.get("historical_parents"), parents),
            "Original historical identities or occurrence/unit counts differ")
    declarations = {}
    language = read(root / MAPPING_SOURCES[0])
    require(set(language["conditions"]) == {"bm25", "dense", "hybrid"}, "Original language retrieval declarations differ")
    declarations[MAPPING_SOURCES[0]] = {name: name for name in language["conditions"]}
    for name in MAPPING_SOURCES[1:]:
        matching = [r for r in read(root / name)["conditions"] if r["id"] == "hybrid_k24_b1"]
        require(len(matching) == 1 and matching[0]["retrieval"] == "hybrid"
                and matching[0]["shortlist"] == 24 and matching[0]["batch_size"] == 1,
                "Original scored hybrid retrieval declaration differs")
        declarations[name] = matching[0]
    require(same(plan.get("retrieval_conditions"), {"mapping": RETRIEVALS, "source_declarations": declarations,
            "source_sha256": {name: file_sha(root / name) for name in MAPPING_SOURCES},
            "case_counts": dict(sorted(Counter(c["condition"] for c in cases).items()))}),
            "Independent original condition/retrieval map differs")
    manifest = []
    for case in sorted(cases, key=data.case_key):
        identity = {name: case[name] for name in ("subset", "dataset", "query_id", "condition")}
        manifest.append({**identity, "kind": case["kind"], "multi_file": case["multi_file"], "qualified": case["qualified"],
            "query_sha256": sha(case["original"]["query"].encode()), "groups_sha256": sha(canonical(case["groups"])),
            "raw_pairs_sha256": sha(canonical([{"id": item.id, "score": score} for item, score in case["pairs"]])),
            "inventory_sha256": sha(canonical([asdict(item) for item in case["inventory"].values()])),
            "definition_keys_sha256": sha(canonical(case["definition_keys"])),
            "edges_sha256": sha(canonical([[list(key), [list(target) for target in values]] for key, values in case["graph"].edges.items()]))})
    require(same(read(folder / "input-manifest.json"), {"schema": "complementary_inputs_v2", "parents": parents, "cases": manifest}),
            "Complete original source/graph/raw/query/label manifest differs")
    operands, parity, references = [], [], []
    for case in sorted(cases, key=data.case_key):
        key = data.case_key(case)
        identity = {name: case[name] for name in ("subset", "dataset", "query_id", "condition")}
        case_id = "case_" + sha(canonical(list(key)))
        actual, hashes, refs, packed = {}, {}, {}, None
        for name in CONTROLS:
            response, stages = history.reconstruct_control(case, name, counter)
            reference = controls[name, key]
            payload = context.compact_payload(response)
            require(canonical(response) == canonical(reference["response"])
                    and sha(payload.encode()) == reference["reference"]["compact_sha256"],
                    "Independent original control source/compact differs")
            actual[name] = response
            refs[name] = reference["reference"]
            hashes[name] = {"source_view_sha256": sha(canonical(response)), "compact_sha256": sha(payload.encode())}
            parity.append({"case_id": case_id, **identity, "selector": name, **hashes[name], "reference": refs[name]})
            if name == "coverage_control":
                packed = history.base.ordinary_pack(case["original"], stages[1], stages[2], counter)
        references.append({"case_id": case_id, **identity, "controls": refs})
        operands.append({"case_id": case_id, "key": list(key), **identity, "query": case["original"]["query"],
            "retrieval": RETRIEVALS[case["condition"]],
            "kind": case["kind"], "multi_file": case["multi_file"], "qualified": case["qualified"], "groups": case["groups"],
            "raw_rows": history.full_rows(case, history.pairs(case)), "precoalesce_rows": packed["results"],
            "reference_response": actual["coverage_control"], "legacy_control": actual["legacy"],
            "definition_keys": json.loads(canonical(case["definition_keys"])), "limits": dict(LIMITS),
            "control_references": refs, "control_hashes": hashes})
    expected_features = independent_requests(operands)
    require(same(read(folder / "operands.json"), {"schema": "semantic_study_inputs_v1", "cases": operands}),
            "Independent original raw/precoalesce/reference/label operands differ")
    require(same(read(folder / "feature-requests.json"), expected_features), "Independent feature activation/prompt/document inventory differs")
    require(same(read(folder / "control-parity.json"), {"exact_control_rows": 2 * expected_rows, "coverage_coalesce_calls": expected_rows,
            "new_candidate_outcomes_computed": 0, "cases": parity}), "Independent complete 786-control barrier differs")
    require(same(read(folder / "control-references.json"), references) and same(read(folder / "negative-references.json"), negatives),
            "Original control/negative evidence references changed")
    require(same(plan.get("barriers"), {"exact_control_rows": 2 * expected_rows, "referenced_negative_rows": expected_negatives,
            "new_candidate_outcomes_computed": 0, "model_calls": 0, "retrieval_calls": 0, "index_calls": 0, "network_calls": 0}),
            "Preparation must precede every new model/candidate outcome")
    return {"plan": plan, "cases": operands, "feature_requests": expected_features,
            "independent_controls": 2 * expected_rows, "retained_negative_references": len(negatives), "historical_parents": parents}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def verify_freeze(root, prepared_dir, prepared, binding, started_at):
    root, folder = Path(root).resolve(), Path(prepared_dir).resolve()
    freeze, execution = binding["freeze"]["commit"], binding["execution_commit"]
    for first, last in ((freeze, execution), (execution, "HEAD")):
        require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", first, last],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0, "Prospective execution ancestry differs")
    committed = datetime.fromisoformat(git(root, "show", "-s", "--format=%cI", freeze).decode().strip())
    started = datetime.fromisoformat(started_at)
    require(committed <= started, "Prospective commit must precede model/candidate execution")
    plan = prepared["plan"]
    names = dict(plan["source_sha256"])
    names.update(plan["historical_artifact_sha256"])
    names[plan["model_binding"]["path"]] = plan["model_binding"]["sha256"]
    names.update(plan["retrieval_conditions"]["source_sha256"])
    names.update({(folder / name).relative_to(root).as_posix(): digest for name, digest in plan["artifact_sha256"].items()})
    names[(folder / "frozen-plan.json").relative_to(root).as_posix()] = file_sha(folder / "frozen-plan.json")
    closure = ("audit_semantic_study", "audit_semantic_context", "audit_complementary_context", "audit_flow_native",
               "audit_flow_allocation", "audit_source_allocation", "audit_windows", "audit_selection", "complementary_inputs",
               "semantic_study_inputs", "semantic_model_study")
    require({"scripts/" + name + ".py" for name in closure} <= set(plan["source_sha256"]),
            "Independent/producer source closure not prospectively frozen")
    require({p.relative_to(root).as_posix() for p in (root / "src/semantic_search").rglob("*.py")}
            <= set(plan["source_sha256"]), "Product package closure not frozen")
    for name, digest in names.items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and file_sha(path) == digest
                and all(sha(git(root, "show", commit + ":" + name)) == digest for commit in (freeze, execution, "HEAD")),
                "Frozen source/input/plan bytes changed: " + name)
    require(binding["freeze"].get("bound_blob_count") == len(names)
            and binding["freeze"].get("plan_sha256") == file_sha(folder / "frozen-plan.json")
            and binding["freeze"].get("new_candidate_outcomes_before_freeze") == 0,
            "Complete prospective freeze receipt differs")
    return {"commit": freeze, "execution_commit": execution, "bound_blob_count": len(names)}


def _artifacts(folder, expected):
    require(isinstance(expected, dict), "Artifact hashes required")
    for name, digest in expected.items():
        path = (Path(folder) / name).resolve()
        require(path.is_relative_to(Path(folder).resolve()) and file_sha(path) == digest, "Execution artifact changed: " + name)


def verify_replay_artifacts(folder, expected):
    require(isinstance(expected, dict) and set(expected) == {"outcomes.jsonl.gz", "replay-start.json"},
            "Complete lossless gzip replay artifact hash closure required")
    _artifacts(folder, expected)


def aggregate_metrics(cases, outcomes):
    by_id = {row["case_id"]: row for row in outcomes}
    metrics = {}
    for case in cases:
        for name, response in (("legacy", case["legacy_control"]), ("coverage_control", case["reference_response"]),
                               ("semantic_final_v1", by_id[case["case_id"]]["response"])):
            key = "/".join(case[field] for field in ("subset", "dataset", "condition")) + "/" + name
            metric = metrics.setdefault(key, {"rows": 0, "required_groups": 0, "covered_groups": 0,
                "complete": 0, "multi_file_complete": 0, "single_file_first_hit": 0})
            value = quality(case, response)
            metric["rows"] += 1
            metric["required_groups"] += len(case["groups"])
            metric["covered_groups"] += value["groups_hit"]
            metric["complete"] += value["complete"]
            metric["multi_file_complete"] += bool(case["multi_file"] and value["complete"])
            metric["single_file_first_hit"] += bool(not case["multi_file"] and value["first_hit"])
    return metrics


def audit(root, prepared_dir, capture_dir, replay_dir, counter):
    root, capture_dir, replay_dir = Path(root).resolve(), Path(capture_dir).resolve(), Path(replay_dir).resolve()
    prepared = audit_preparation(root, prepared_dir, counter)
    require(not any((folder / "failure.json").exists() for folder in (capture_dir, replay_dir)), "Failed attempts cannot qualify")
    capture, start, release = (read(capture_dir / name) for name in ("capture.json", "capture-start.json", "gpu-lease-released.json"))
    require(capture.get("status") == "passed" and same(capture.get("binding"), start.get("binding")), "Successful capture start binding differs")
    binding = capture["binding"]
    frozen = verify_freeze(root, prepared_dir, prepared, binding, start["started_at"])
    runtime = binding["runtime"]
    require(runtime.get("manifest_path") == prepared["plan"]["model_binding"]["path"]
            and runtime.get("manifest_sha256") == prepared["plan"]["model_binding"]["sha256"],
            "Actual runtime manifest differs from prepared model binding")
    manifest_path = (root / runtime["manifest_path"]).resolve()
    require(manifest_path.is_relative_to(root) and file_sha(manifest_path) == runtime["manifest_sha256"], "Frozen model manifest bytes differ")
    for commit in (frozen["commit"], frozen["execution_commit"], "HEAD"):
        require(sha(git(root, "show", commit + ":" + manifest_path.relative_to(root).as_posix())) == runtime["manifest_sha256"],
                "Model/runtime/checkpoint binding must be committed before inference")
    model_binding = read(manifest_path)["model_binding"]
    require(same(runtime.get("model_binding"), model_binding) and same(binding.get("model_binding"), model_binding)
            and same(capture.get("model_metadata"), model_binding["metadata"])
            and capture.get("model_fingerprint") == model_binding["fingerprint"], "Actual executing model binding differs")
    require(model_binding["checkpoint"]["files"]["model.safetensors"]["sha256"] == WEIGHTS_SHA256,
            "Pinned original weights content SHA differs")
    model = verify_model_binding(model_binding)
    lease = binding["gpu_lease"]
    common = Path(git(root, "rev-parse", "--git-common-dir").decode().strip())
    if not common.is_absolute():
        common = root / common
    require(Path(lease["path"]).resolve() == common.resolve() / "semantic-search-gpu.lock", "GPU lease is not the shared Git runtime lock")
    require(type(lease.get("pid")) is int and lease["pid"] > 0 and lease.get("released_at") is None
            and same({k: v for k, v in release.items() if k != "released_at"}, {k: v for k, v in lease.items() if k != "released_at"})
            and datetime.fromisoformat(lease["acquired_at"]) <= datetime.fromisoformat(start["started_at"])
            <= datetime.fromisoformat(capture["completed_at"]) <= datetime.fromisoformat(release["released_at"]),
            "Exclusive model lease acquisition/close/release chronology differs")
    requests = prepared["feature_requests"]
    # Producer transport preserves order; this SHA deliberately does not sort.
    request_sha = sha(json.dumps(requests, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
    require(capture.get("feature_requests_sha256") == start.get("feature_requests_sha256") == request_sha
            and start.get("document_order") == [r["document_id"] for r in requests["document_requests"]]
            and start.get("query_order") == [r["request_id"] for r in requests["query_requests"]], "Actual fresh feature input order differs")
    require(set(capture["artifact_sha256"]) == {"capture-start.json", "forward-events.jsonl", "document-vectors.jsonl", "query-vectors.jsonl"},
            "Complete durable feature artifact hash closure required")
    _artifacts(capture_dir, capture["artifact_sha256"])
    counts = verify_capture(capture, requests, forward_events=jsonl(capture_dir / "forward-events.jsonl"),
        document_checkpoints=jsonl(capture_dir / "document-vectors.jsonl"), query_checkpoints=jsonl(capture_dir / "query-vectors.jsonl"))
    replay_start, summary = read(replay_dir / "replay-start.json"), read(replay_dir / "summary.json")
    require(replay_start["feature_capture_sha256"] == sha(json.dumps(capture, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()),
            "CPU replay consumed a different feature capture")
    verify_freeze(root, prepared_dir, prepared, replay_start["binding"], replay_start["started_at"])
    require(datetime.fromisoformat(release["released_at"]) <= datetime.fromisoformat(replay_start["started_at"]),
            "Candidate outcomes must follow completed feature capture and model release")
    verify_replay_artifacts(replay_dir, summary["artifact_sha256"])
    outcomes = jsonl(replay_dir / "outcomes.jsonl.gz")
    gate = audit_outcomes(prepared["cases"], outcomes, capture, requests, counter=counter)
    changes = {name: [] for name in CONTROLS}
    for row in outcomes:
        for name, comparison in row["comparisons"].items():
            if comparison["group_losses"] or comparison["complete_loss"] or comparison["single_file_first_hit_loss"]:
                changes[name].append({"case_id": row["case_id"], **comparison})
    expected_gate = {"qualified": gate["qualified"], "individual_losses": changes,
        "additional_distinct_complete_multi_queries_vs_coverage": gate["additional_distinct_complete_multi_queries_vs_coverage"],
        "automatic_default_change": False, "fresh_and_native_validation_still_required": True}
    require(summary.get("status") == "passed" and same(summary.get("quality_gate"), expected_gate)
            and summary.get("stored_candidate_rows") == 393 and summary.get("distinct_dataset_query_units") == 201
            and summary.get("referenced_control_rows") == 786
            and same(summary.get("candidate_phase_calls"), {name: 0 for name in ("model", "retrieval", "index", "network", "backfill")}),
            "Independent individual quality gate/counts/replay work differ")
    require(same(summary.get("metrics_by_original_condition"), aggregate_metrics(prepared["cases"], outcomes)),
            "Independent original-condition aggregate metrics differ")
    return {"schema": SCHEMA, "status": "passed", "checks_passed": True,
            "verified_candidate_rows": 393, "exact_control_rows": 786, "distinct_query_units": 201,
            "retained_negative_references": 2948, "independently_recomputed_qualification": gate,
            "forward_summary": counts, "model_binding_verification": model, "producer_freeze": frozen,
            "native_warm_latency_measured": False, "automatic_default_change": False,
            "model_calls": 0, "retrieval_calls": 0, "network_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--check-controls", action="store_true")
    parser.add_argument("--tokenizer-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(args.check_controls or args.capture and args.replay, "Select --check-controls or supply --capture and --replay")
    require(not args.output.exists(), "Independent audit output must be new")
    forbidden = ("semantic_search", "semantic_model_study", "semantic_study_inputs", "replay_", "mlx", "numpy", "torch", "transformers")
    require(not any(any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden) for name in sys.modules),
            "Independent auditor process already contains production/model imports")

    class Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if any(fullname == prefix or fullname.startswith(prefix + ".") or fullname.startswith("replay_") for prefix in forbidden):
                raise RuntimeError("Independent auditor forbids producer/model import: " + fullname)

    def deny(*args, **kwargs):
        raise RuntimeError("Independent auditor forbids network")

    guard = Guard()
    sys.meta_path.insert(0, guard)
    try:
        with patch.object(socket, "create_connection", deny), patch.object(socket, "getaddrinfo", deny), \
             patch.object(socket.socket, "connect", deny), patch.object(socket.socket, "connect_ex", deny), \
             patch.object(socket.socket, "sendto", deny):
            from audit_selection import offline_encoding

            counter = offline_encoding(args.tokenizer_cache or args.root / ".cache/tokenizers")
            if args.check_controls:
                value = audit_preparation(args.root, args.prepared, counter)
                result = {"schema": SCHEMA, "status": "passed", "exact_control_rows": value["independent_controls"],
                          "retained_negative_references": value["retained_negative_references"],
                          "candidate_outcomes_computed": 0, "model_calls": 0, "retrieval_calls": 0, "network_calls": 0}
            else:
                result = audit(args.root, args.prepared, args.capture, args.replay, counter)
    finally:
        sys.meta_path.remove(guard)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
