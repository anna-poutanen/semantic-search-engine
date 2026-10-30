"""Literal, vector-only inputs for the opt-in semantic context experiment.

This boundary performs no inference, retrieval or index access. It binds already
captured query and full-window document vectors to exact source and provenance.
Its cosine values are allocation features, never replacement retrieval scores.
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
import re
import struct


SCHEMA = "semantic_context_features_v1"
MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
DIMENSIONS = 1024
QUERY_PROMPT = (
    "Instruct: Given a description of code behavior, retrieve the code implementation that performs it."
    "\nQuery:"
)
PROVENANCE = ("source_origin", "buffer_version", "buffer_hash")
SCORE_SCALE = 1_000_000
_FIELDS = {
    "schema", "query", "facets", "query_prompt", "model_metadata",
    "model_fingerprint", "facet_vectors", "windows",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _same(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Feature metadata must be finite JSON data") from error


def _json_copy(value):
    result = json.loads(_canonical(value))
    _require(_same(value, result), "Feature metadata must retain literal JSON types")
    return result


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def expected_facets(query):
    """Keep the full query followed by its two to four eligible literal clauses."""
    from semantic_search.retrieval import multifile_clauses

    clauses = multifile_clauses(query)
    return [query, *clauses] if clauses else []


def _literal(row):
    _require(isinstance(row, dict), "Source row must be a dictionary")
    _require(all(isinstance(row.get(key), str) and row[key]
                 for key in ("id", "path", "symbol", "source")), "Literal source identity required")
    low, high, window_high = (row.get(key) for key in ("start_line", "end_line", "window_end_line"))
    _require(type(low) is int and type(high) is int and type(window_high) is int
             and 1 <= low <= high <= window_high, "Literal source bounds required")
    _require(type(row.get("truncated")) is bool, "Literal truncation flag required")
    _require(high == low + len(_lines(row["source"])) - 1, "Physical source interval differs")
    _require(row.get("source_hash") == _hash(row["source"]), "Delivered source hash differs")
    _require(isinstance(row.get("content_hash"), str)
             and re.fullmatch(r"[0-9a-f]{64}", row["content_hash"]) is not None,
             "Full-window content hash required")
    for key in PROVENANCE:
        if key in row:
            _json_copy(row[key])
    return not row["truncated"] and high == window_high


def document_text(row):
    """Render the exact existing Qwen document wrapper without an instruction."""
    _require(_literal(row) and row["source_hash"] == row["content_hash"],
             "Semantic document requires an exact full source window")
    return f'Path: {row["path"]}\nSymbol: {row["symbol"]}\nSource:\n{row["source"]}'


def row_binding(row):
    """Bind literal full source, physical bounds and provenance field presence."""
    rendered = document_text(row)
    result = {key: row[key] for key in
              ("id", "path", "symbol", "start_line", "end_line", "source_hash")}
    result["document_sha256"] = _hash(rendered)
    result.update((key, _json_copy(row[key])) for key in PROVENANCE if key in row)
    return result


def _prefix(clipped, full):
    keys = ("id", "path", "symbol", "start_line", "window_end_line", "content_hash")
    return (all(_same(clipped.get(key), full.get(key)) for key in keys)
            and all((key in clipped) == (key in full)
                    and _same(clipped.get(key), full.get(key)) for key in PROVENANCE)
            and _lines(clipped["source"]) == _lines(full["source"])[:len(_lines(clipped["source"]))])


def _required_rows(reference, raw_rows):
    _require(isinstance(reference, dict) and isinstance(reference.get("query"), str)
             and isinstance(reference.get("results"), list), "Reference query and rows required")
    _require(isinstance(raw_rows, list) and len(raw_rows) <= 50, "Raw source pool must contain at most 50 rows")
    required = {}
    for row in raw_rows:
        _require(_literal(row), "Raw source row must be a full window")
        _require(row["id"] not in required, "Raw source IDs must be unique")
        row_binding(row)
        required[row["id"]] = row
    seen = set()
    clipped_rows = []
    for row in reference["results"]:
        full = _literal(row)
        _require(row["id"] not in seen, "Reference source IDs must be unique")
        seen.add(row["id"])
        if not full:
            clipped_rows.append(row)
            continue
        binding = row_binding(row)
        if row["id"] in required:
            _require(_same(row_binding(required[row["id"]]), binding),
                     "Conflicting full source or provenance for the same ID")
        else:
            required[row["id"]] = row
    for row in clipped_rows:
        if row["id"] in required:
            _require(_prefix(row, required[row["id"]]),
                     "Clipped reference must be a literal prefix of its full window")
    return required


def _model(metadata):
    _require(isinstance(metadata, dict), "Model metadata must be a dictionary")
    _require(metadata.get("id") == MODEL_ID and metadata.get("revision") == MODEL_REVISION
             and type(metadata.get("dimensions")) is int and metadata["dimensions"] == DIMENSIONS
             and metadata.get("query_prompt") == QUERY_PROMPT
             and metadata.get("normalization") == "L2, float32", "Pinned Qwen query model metadata differs")
    return _json_copy(metadata)


def _vector(value, *, captured):
    if captured:
        _require(isinstance(value, list), "Captured vector must be a list")
    else:
        _require(not isinstance(value, (str, bytes, dict)), "Vector must be a numeric sequence")
        try:
            value = list(value)
        except TypeError as error:
            raise ValueError("Vector must be a numeric sequence") from error
    _require(len(value) == DIMENSIONS, "Vector dimension must be 1024")
    result = []
    for item in value:
        _require(type(item) is float if captured else
                 isinstance(item, numbers.Real) and not isinstance(item, numbers.Integral),
                 "Vector components must be floating-point numbers, not booleans or integers")
        number = float(item)
        _require(math.isfinite(number), "Vector components must be finite")
        try:
            restored = struct.unpack("<f", struct.pack("<f", number))[0]
        except (OverflowError, struct.error) as error:
            raise ValueError("Vector component exceeds float32 range") from error
        _require(restored == number, "Vector component must already be exact float32")
        result.append(number)
    _require(math.fsum(item * item for item in result) > 0, "Vector must have a nonzero norm")
    return result


def make_features(query, raw_rows, reference, facet_vectors, window_vectors, model_metadata):
    """Capture already available vectors without computing scores or model work."""
    required = _required_rows(reference, raw_rows)
    facets = expected_facets(query)
    _require(bool(facets) and reference.get("query") == query, "Literal query facets and reference query must match")
    metadata = _model(model_metadata)
    _require(isinstance(window_vectors, dict) and window_vectors.keys() == required.keys(),
             "Window vectors must match all and only required full windows")
    try:
        facet_vectors = list(facet_vectors)
    except TypeError as error:
        raise ValueError("Facet vectors must be a sequence") from error
    _require(len(facet_vectors) == len(facets), "Facet vector count differs")
    return {
        "schema": SCHEMA, "query": query, "facets": facets, "query_prompt": QUERY_PROMPT,
        "model_metadata": metadata, "model_fingerprint": _hash(_canonical(metadata)),
        "facet_vectors": [_vector(vector, captured=False) for vector in facet_vectors],
        "windows": {identifier: {"binding": row_binding(row),
                                 "vector": _vector(window_vectors[identifier], captured=False)}
                    for identifier, row in required.items()},
    }


def _cosine_ppm(left, right, left_norm, right_norm):
    dot = math.fsum(a * b for a, b in zip(left, right))
    denominator = left_norm * right_norm
    cosine = max(-1.0, min(1.0, dot / denominator))
    return int(round(cosine * SCORE_SCALE))


def validated_scores(reference, raw_rows, features, *, definition_keys=None):
    """Validate captured literal inputs and compute each cosine exactly once.

    Definition identities are a selector concern. They cannot substitute for a
    window's literal source binding, so ``definition_keys`` is not used here.
    Clipped reference rows remain outside the semantic feature set.
    """
    required = _required_rows(reference, raw_rows)
    facets = expected_facets(reference["query"])
    _require(bool(facets), "No eligible literal query facets")
    _require(isinstance(features, dict) and features.keys() == _FIELDS, "Feature schema fields differ")
    _require(features["schema"] == SCHEMA and features["query"] == reference["query"]
             and _same(features["facets"], facets) and features["query_prompt"] == QUERY_PROMPT,
             "Feature query or literal facets differ")
    metadata = _model(features["model_metadata"])
    _require(features["model_fingerprint"] == _hash(_canonical(metadata)), "Model fingerprint differs")
    _require(isinstance(features["facet_vectors"], list)
             and len(features["facet_vectors"]) == len(facets), "Facet vector count differs")
    query_vectors = [_vector(vector, captured=True) for vector in features["facet_vectors"]]
    windows = features["windows"]
    _require(isinstance(windows, dict) and windows.keys() == required.keys(),
             "Captured windows must match all and only required full windows")
    vectors = {}
    for identifier, row in required.items():
        record = windows[identifier]
        _require(isinstance(record, dict) and record.keys() == {"binding", "vector"}
                 and _same(record["binding"], row_binding(row)), "Literal window binding differs")
        vectors[identifier] = _vector(record["vector"], captured=True)
    # Reuse each unchanged vector's norm without changing the specified product
    # of two separately square-rooted sums or computing a similarity twice.
    query_norms = [math.sqrt(math.fsum(item * item for item in vector)) for vector in query_vectors]
    norms = {identifier: math.sqrt(math.fsum(item * item for item in vector))
             for identifier, vector in vectors.items()}
    return facets, {identifier: tuple(_cosine_ppm(vector, query_vector, norms[identifier], query_norm)
                                      for query_vector, query_norm in zip(query_vectors, query_norms))
                    for identifier, vector in vectors.items()}
