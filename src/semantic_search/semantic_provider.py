"""Opt-in resident-vector feature capture for the semantic context experiment.

This provider reuses the executing request's original query vector and current
index vectors. Only additional literal clauses are encoded. Receipts describe
the supplied execution boundaries; they cannot independently prove that a real
checkpoint produced an otherwise fabricated vector or observer event.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import struct

from semantic_search.semantic_features import (
    DIMENSIONS, PROVENANCE, QUERY_PROMPT, _model, _required_rows, _same, _vector,
    expected_facets, make_features,
)


SCHEMA = "semantic_context_live_provider_v1"
_MODES = {"bm25", "dense", "hybrid", "multifile"}
_FORWARD_SCHEMA = "qwen_embedding_forward_v1"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vector_hash(vector):
    """Hash existing exact float32 values without normalization or rounding."""
    return hashlib.sha256(struct.pack(f"<{len(vector)}f", *vector)).hexdigest()


def _forward_summary(events, texts, metadata):
    """Count actual forwards and bind their supplied receipts to exact inputs."""
    _require(isinstance(events, list), "Query forward events must be a list")
    starts, terminals = {}, {}
    last_time = -1.0
    for position, event in enumerate(events):
        _require(isinstance(event, dict) and event.get("schema") == _FORWARD_SCHEMA,
                 "Query forward receipt schema differs")
        index, status = event.get("forward_index"), event.get("status")
        _require(type(index) is int and index >= 0 and status in ("started", "completed"),
                 "A successful feature capture requires completed query forwards")
        _require(index == position // 2
                 and status == ("started" if position % 2 == 0 else "completed"),
                 "Query forward receipts must preserve actual start/completion order")
        elapsed = event.get("elapsed_seconds")
        _require(type(elapsed) is float and math.isfinite(elapsed) and elapsed >= 0 and elapsed >= last_time,
                 "Query forward elapsed time must be finite and monotonic")
        last_time = elapsed
        target = starts if status == "started" else terminals
        _require(index not in target, "Duplicate query forward receipt")
        target[index] = event
    _require(starts.keys() == terminals.keys() and bool(starts),
             "Every query forward must have a start and completion receipt")
    _require(set(starts) == set(range(len(starts))), "Query forward indices must be contiguous")
    covered = []
    for index in sorted(starts):
        start, terminal = starts[index], terminals[index]
        for key in ("input_count", "batch_input_count", "input_indices", "input_sha256",
                    "token_ids_sha256", "token_lengths", "padded_tokens", "padded_shape",
                    "expected_output_shape"):
            _require(key in start and key in terminal and _same(start[key], terminal[key]),
                     "Query forward completion differs from its start")
        indices = start["input_indices"]
        _require(type(start["input_count"]) is int and start["input_count"] == len(texts)
                 and isinstance(indices, list) and bool(indices)
                 and all(type(value) is int and 0 <= value < len(texts) for value in indices)
                 and type(start["batch_input_count"]) is int
                 and start["batch_input_count"] == len(indices),
                 "Query forward input indices differ")
        _require(start["input_sha256"] == [_sha(texts[value]) for value in indices],
                 "Query forward prompted input hash differs")
        lengths = start["token_lengths"]
        _require(isinstance(lengths, list) and len(lengths) == len(indices)
                 and all(type(length) is int and 0 < length <= metadata.get("max_tokens", 8192)
                         for length in lengths), "Query forward token lengths differ")
        hashes = start["token_ids_sha256"]
        _require(isinstance(hashes, list) and len(hashes) == len(indices)
                 and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                         for value in hashes), "Query forward token hashes differ")
        expected_padding = [len(indices), max(lengths)]
        _require(_same(start["padded_shape"], expected_padding)
                 and type(start["padded_tokens"]) is int
                 and start["padded_tokens"] == len(indices) * max(lengths)
                 and start["padded_tokens"] <= metadata.get("max_padded_tokens", 8192)
                 and len(indices) <= metadata.get("batch_size", 4),
                 "Query forward padded shape or batch budget differs")
        expected_output = [len(indices), DIMENSIONS]
        _require(_same(start["expected_output_shape"], expected_output)
                 and _same(terminal.get("output_shape"), expected_output)
                 and terminal.get("output_dtype") == "float32", "Query forward output differs")
        duration = terminal.get("duration_seconds")
        _require(type(duration) is float and math.isfinite(duration) and duration >= 0
                 and duration <= terminal["elapsed_seconds"] - start["elapsed_seconds"],
                 "Query forward duration differs from its elapsed interval")
        covered.extend(indices)
    _require(sorted(covered) == list(range(len(texts))),
             "Query forward receipts must cover every requested input exactly once")
    return {"model_forward_calls": len(terminals), "encoded_texts": len(covered)}


def _reported_work(events):
    """Partial terminal evidence; a started callback alone proves no forward."""
    if not isinstance(events, list):
        return {"receipt_list_valid": False, "reported_model_forward_calls": None,
                "reported_completed_model_forward_calls": None,
                "reported_failed_model_forward_calls": None, "reported_encoded_texts": None}
    terminal = [event for event in events if isinstance(event, dict)
                and event.get("status") in ("completed", "failed")]
    return {"receipt_list_valid": True, "reported_model_forward_calls": len(terminal),
            "reported_completed_model_forward_calls": sum(event["status"] == "completed" for event in terminal),
            "reported_failed_model_forward_calls": sum(event["status"] == "failed" for event in terminal),
            "reported_encoded_texts": sum(event["batch_input_count"] for event in terminal
                if type(event.get("batch_input_count")) is int and event["batch_input_count"] >= 0)}


def _provenance(rows, source_provenance):
    """Version/hash authority is the actual executing backend, not SQLite."""
    for row in rows:
        current = source_provenance(row["path"])
        _require(isinstance(current, dict), "Executing source provenance must be a dictionary")
        for key in PROVENANCE:
            _require((key in row) == (key in current)
                     and _same(row.get(key), current.get(key)),
                     "Source provenance differs from the executing request")


def make_live_feature_provider(request_context, *, capture=None):
    """Return an adapter provider for one actual executing backend request.

    ``confirm_snapshot()`` and ``source_provenance(path)`` must be bound to that
    backend, including an overlay child when present. A missing original query
    vector, lexical searches, absent/abstained pools and ineligible queries are
    explicitly inactive. Missing required document vectors, broken active
    bindings and execution failures propagate with a partial-work receipt.
    """
    if not isinstance(request_context, dict):
        raise TypeError("Semantic request context must be a dictionary")
    if capture is not None and not callable(capture):
        raise TypeError("Semantic provider capture must be callable")

    def provide(reference, raw_rows, cancel_check, state):
        cancel_check()
        context = request_context
        query, retrieval = context.get("query"), context.get("retrieval")
        _require(isinstance(query, str) and bool(query.strip())
                 and isinstance(reference, dict) and reference.get("query") == query,
                 "Semantic request query differs from the reference")
        _require(retrieval in _MODES and reference.get("retrieval") == retrieval,
                 "Semantic request retrieval differs from the reference")
        _require(isinstance(raw_rows, list) and isinstance(reference.get("results"), list),
                 "Semantic request source pools must be lists")
        facets = expected_facets(query)
        reason = (
            "lexical_only" if retrieval == "bm25" else
            "missing_original_query_vector" if context.get("query_vector") is None else
            "abstained" if reference.get("match_status") == "insufficient_evidence" else
            "empty_raw_pool" if not raw_rows else
            "empty_reference" if not reference["results"] else
            "ineligible_query" if not facets else None)
        if reason is not None:
            state["stage"] = "inactive_capture"
            if capture is not None:
                capture({"schema": SCHEMA, "status": "inactive", "reason": reason,
                    "query": query, "retrieval": retrieval, "extra_query_api_calls": 0,
                    "extra_query_model_forward_calls": 0, "extra_query_encoded_texts": 0,
                    "resident_vector_reads": 0, "document_inference_calls": 0,
                    "extra_retrieval_calls": 0})
            cancel_check()
            return None

        state["stage"] = "active_binding_validation"
        required = _required_rows(reference, raw_rows)
        metadata = _model(context.get("model_metadata"))
        fingerprint = _sha(_json(metadata))
        _require(context.get("model_fingerprint") == fingerprint,
                 "Semantic request model fingerprint differs")
        snapshot = context.get("source_snapshot")
        _require(isinstance(snapshot, str) and bool(snapshot), "Current source snapshot required")
        embedder, index = context.get("embedder"), context.get("index")
        confirm, provenance = context.get("confirm_snapshot"), context.get("source_provenance")
        _require(callable(confirm) and callable(provenance), "Executing source callbacks required")
        _require(callable(getattr(embedder, "embed_queries", None))
                 and callable(getattr(index, "read_semantic_vectors", None)),
                 "Executing embedder and resident vector accessor required")
        original_vector = _vector(context["query_vector"], captured=False)
        original_hash = _vector_hash(original_vector)
        original_events = deepcopy(context.get("query_forward_events"))
        state["original_events"] = original_events
        original_summary = _forward_summary(original_events, [QUERY_PROMPT + query], metadata)
        all_rows = [*raw_rows, *reference["results"]]

        def validate_current():
            cancel_check()
            _require(context.get("query") == query and context.get("retrieval") == retrieval
                     and context.get("source_snapshot") == snapshot
                     and context.get("model_fingerprint") == fingerprint
                     and _same(context.get("model_metadata"), metadata)
                     and context.get("embedder") is embedder and context.get("index") is index
                     and context.get("confirm_snapshot") is confirm
                     and context.get("source_provenance") is provenance,
                     "Semantic executing request context changed")
            _require(_same(_model(embedder.metadata), metadata),
                     "Executing query model metadata changed")
            _require(_vector_hash(_vector(context["query_vector"], captured=False)) == original_hash,
                     "Original retrieval query vector changed")
            _require(_same(context.get("query_forward_events"), original_events),
                     "Original retrieval query forward receipts changed")
            confirm()
            _provenance(all_rows, provenance)
            cancel_check()

        state["stage"] = "source_validation_before_read"
        validate_current()
        state["stage"] = "resident_vector_read"
        state["read_attempts"] += 1
        vectors = index.read_semantic_vectors(list(required.values()), model_metadata=metadata,
                                             cancel_check=cancel_check)
        state["reads"] += 1
        cancel_check()
        # Validate source/vector membership before doing even clause inference.
        # make_features performs the same exact vector validation for the final
        # capture; this temporary check supplies only already captured vectors.
        state["stage"] = "resident_vector_validation"
        make_features(query, raw_rows, reference,
                      [original_vector] * len(facets), vectors, metadata)
        state["stage"] = "source_validation_before_clauses"
        validate_current()
        extra_events = state["extra_events"]
        clauses = facets[1:]
        state["stage"] = "clause_encoding"
        state["api_calls"] += 1
        clause_vectors = embedder.embed_queries(clauses, cancel_check=cancel_check,
                                               forward_observer=extra_events.append)
        state["completed_api_calls"] += 1
        cancel_check()
        extra_events = deepcopy(extra_events)
        state["stage"] = "clause_forward_validation"
        extra_summary = _forward_summary(extra_events, [QUERY_PROMPT + clause for clause in clauses], metadata)
        state["stage"] = "feature_validation"
        features = make_features(query, raw_rows, reference,
                                 [original_vector, *clause_vectors], vectors, metadata)
        state["stage"] = "source_validation_after_clauses"
        validate_current()
        # The feature maker expands exact float32 components to Python floats;
        # hashing their original float32 representation must be byte-identical.
        _require(_vector_hash(features["facet_vectors"][0]) == original_hash,
                 "Original retrieval vector changed during feature capture")
        state["stage"] = "active_capture"
        if capture is not None:
            capture({"schema": SCHEMA, "status": "active", "query": query,
                "retrieval": retrieval, "source_snapshot": snapshot,
                "model_metadata": deepcopy(metadata), "model_fingerprint": fingerprint,
                "facets": list(facets), "extra_clause_prompts": [QUERY_PROMPT + clause for clause in clauses],
                "original_query_vector": {"origin": "executing_retrieval_query_vector",
                    "reencoded": False, "dimensions": len(original_vector),
                    "float32_le_sha256": original_hash},
                "original_query_forward_events": original_events,
                "original_query_model_forward_calls": original_summary["model_forward_calls"],
                "original_query_encoded_texts": original_summary["encoded_texts"],
                "extra_query_forward_events": extra_events, "extra_query_api_calls": 1,
                "extra_query_model_forward_calls": extra_summary["model_forward_calls"],
                "extra_query_encoded_texts": extra_summary["encoded_texts"],
                "resident_vector_read_attempts": state["read_attempts"],
                "resident_vector_reads": state["reads"], "document_inference_calls": 0,
                "extra_retrieval_calls": 0,
                "windows": {identifier: {"binding": deepcopy(record["binding"]),
                    "float32_le_sha256": _vector_hash(record["vector"])}
                    for identifier, record in features["windows"].items()}})
        cancel_check()
        return features

    def provider(reference, raw_rows, *, cancel_check=lambda: None):
        state = {"stage": "request_validation", "original_events": [], "extra_events": [],
                 "read_attempts": 0, "reads": 0, "api_calls": 0, "completed_api_calls": 0,
                 "request_binding": {}}
        try:
            state["request_binding"] = deepcopy({key: request_context.get(key) for key in
                ("query", "retrieval", "source_snapshot", "model_fingerprint")})
            state["original_events"] = deepcopy(request_context.get("query_forward_events", []))
            return provide(reference, raw_rows, cancel_check, state)
        except BaseException as error:
            if capture is not None:
                try:
                    original = deepcopy(state["original_events"])
                    extra = deepcopy(state["extra_events"])
                    binding = state["request_binding"]
                    capture({"schema": SCHEMA, "status": "failed", "stage": state["stage"],
                        "error_type": f"{type(error).__module__}.{type(error).__qualname__}",
                        "query": binding.get("query"), "retrieval": binding.get("retrieval"),
                        "source_snapshot": binding.get("source_snapshot"),
                        "model_fingerprint": binding.get("model_fingerprint"),
                        "original_query_forward_events": original,
                        "extra_query_forward_events": extra,
                        "original_query_work": _reported_work(original),
                        "extra_query_work": _reported_work(extra),
                        "extra_query_api_calls": state["api_calls"],
                        "extra_query_api_completed_calls": state["completed_api_calls"],
                        "resident_vector_read_attempts": state["read_attempts"],
                        "resident_vector_reads": state["reads"],
                        "document_inference_calls": 0, "extra_retrieval_calls": 0})
                except BaseException as capture_error:
                    if capture_error is error:
                        raise error
                    raise error from capture_error
            raise

    return provider
