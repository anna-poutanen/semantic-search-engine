#!/usr/bin/env python3
"""Independent, source-only reconstruction of semantic final-context exchange.

This auditor has no dependency on the production selector, feature adapter,
backend, models, index, numerical runtime or experiment producer.  All vectors
and source rows are explicit inputs.  Token encoding is supplied by the caller.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import struct


SCALE = 1_000_000
MAX_SWAPS = 2
MAX_BUDGET_TRIALS = 8
MAX_RAW_WINDOWS_PER_DEFINITION = 2
PROVENANCE = ("source_origin", "buffer_version", "buffer_hash")
SCORE_FIELDS = ("retrieval_score", "h2o_score", "qwen_score")
FEATURE_SCHEMA = "semantic_context_features_v1"
MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
MODEL_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
DIMENSIONS = 1024
QUERY_PROMPT = ("Instruct: Given a description of code behavior, retrieve the code implementation "
                "that performs it.\nQuery:")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def same(first, second):
    """JSON equality that does not confuse booleans and numeric captures."""
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(same(first[k], second[k]) for k in first)
    if isinstance(first, (list, tuple)):
        return len(first) == len(second) and all(same(a, b) for a, b in zip(first, second))
    return first == second


def same_source_response(first, second):
    """Compare every field except the backend's two top-level operational maps.

    The backend refreshes timings/index statistics after its postprocessor has
    returned. Their independent wall-time/model-call accounting belongs to the
    future measured harness; the source auditor cannot require those mutable
    operational values to equal the earlier coverage snapshot. This exemption
    never reaches nested result dictionaries or other response metadata.
    """
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    return same({key: value for key, value in first.items() if key not in {"timings", "indexstats"}},
                {key: value for key, value in second.items() if key not in {"timings", "indexstats"}})


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def physical_lines(text):
    parts, start = [], 0
    for match in re.finditer(r"\r\n|\r|\n", text):
        parts.append(text[start:match.end()])
        start = match.end()
    if start < len(text):
        parts.append(text[start:])
    return parts


def literal(row):
    text, start, end = row.get("source"), row.get("start_line"), row.get("end_line")
    require(isinstance(text, str) and text and isinstance(row.get("path"), str), "Literal source schema")
    require(type(start) is int and type(end) is int and start >= 1,
            "Literal physical line schema")
    lines = physical_lines(text)
    require(end == start + len(lines) - 1, "Literal physical line interval")
    require(row.get("source_hash") == digest(text), "Delivered source hash differs")
    return row["path"], start, end, lines


def covers(outer, inner):
    path, low, high, lines = literal(outer)
    other_path, other_low, other_high, other_lines = literal(inner)
    if path != other_path or low > other_low or high < other_high:
        return False
    if lines[other_low-low:other_high-low+1] != other_lines:
        return False
    if not all((key in outer) == (key in inner) and same(outer.get(key), inner.get(key))
               for key in PROVENANCE):
        return False
    if inner.get("origin", "retrieval") == "dependency":
        return (outer.get("id") == inner.get("id") and outer.get("origin") == "dependency"
                and same(outer.get("related_to"), inner.get("related_to")))
    if outer.get("id") == inner.get("id"):
        return (outer.get("content_hash") == inner.get("content_hash")
                and all((key in outer) == (key in inner) and same(outer.get(key), inner.get(key))
                        for key in SCORE_FIELDS))
    return True


def overlap(first, second):
    return (first["path"] == second["path"]
            and max(first["start_line"], second["start_line"])
            <= min(first["end_line"], second["end_line"]))


def float32_vector(vector):
    """Captured components must already be finite, exact IEEE float32 values."""
    require(isinstance(vector, list) and bool(vector), "Nonempty captured vector required")
    output = []
    for value in vector:
        require(type(value) is float and math.isfinite(value), "Finite float32 component required")
        try:
            converted = struct.unpack("<f", struct.pack("<f", value))[0]
        except (OverflowError, struct.error) as exc:
            raise ValueError("Float32 component overflow") from exc
        require(converted == value, "Captured component is not exact float32")
        output.append(value)
    require(math.fsum(value * value for value in output) > 0, "Zero-norm vector forbidden")
    return output


def cosine_ppm(first, second):
    left, right = float32_vector(first), float32_vector(second)
    require(len(left) == len(right), "Vector dimensions differ")
    product = math.fsum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(math.fsum(value * value for value in left)) * math.sqrt(
        math.fsum(value * value for value in right))
    cosine = max(-1.0, min(1.0, product / denominator))
    return int(round(cosine * SCALE))


def compact_payload(response):
    """Independent public compact-envelope specification, preserving field order."""
    rows = []
    for original in response["results"]:
        row = {key: original[key] for key in
               ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")}
        row.update(origin=original.get("origin", "retrieval"), related_to=original.get("related_to"))
        for key in PROVENANCE:
            if key in original:
                row[key] = original[key]
        rows.append(row)
    value = {"query": response["query"], "match_status": response.get("match_status", "candidates"),
             "results": rows, "budget_exhausted": response.get("budget_exhausted", False),
             "token_usage": response.get("token_usage", {"encoding": "cl100k_base", "tokens": None,
                                                          "budget": None, "truncated": False})}
    if "scope" in response:
        value["scope"] = response["scope"]
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def recount(response, token_budget, counter):
    response["total_source_chars"] = sum(len(row["source"]) for row in response["results"])
    if token_budget is None:
        return None
    usage = response.setdefault("token_usage", {})
    usage.update(encoding="cl100k_base", budget=token_budget, tokens=0)
    usage.setdefault("truncated", False)
    for _ in range(12):
        text = compact_payload(response)
        tokens = len(counter.encode(text, disallowed_special=()))
        if tokens == usage["tokens"]:
            return tokens
        usage["tokens"] = tokens
    raise ValueError("Compact token count did not stabilize")


def literal_clauses(query):
    """Independent reading of the existing literal-fragment specification."""
    require(isinstance(query, str) and bool(query.strip()), "Nonempty original query required")
    stop = set("where is are the a an and or to of for in on at by with from when while does do as "
               "be it its into only that this then otherwise can without which each either any "
               "all before after also through using return returns make made".split())
    clauses = []
    for part in re.split(r",|\band\b|\bwhile\b|\bthen\b", query, flags=re.I):
        part = part.strip(" ?.,")
        text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", part)
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        terms = set()
        for term in re.findall(r"[a-z0-9]+", text.lower()):
            if term in stop or len(term) < 3:
                continue
            if term.endswith("ies") and len(term) > 4:
                term = term[:-3] + "y"
            elif term.endswith("s") and len(term) > 3 and not term.endswith("ss"):
                term = term[:-1]
            terms.add(term)
        if len(terms) >= 3:
            clauses.append(part)
    return clauses[:4] if len(clauses) >= 2 else []


def clipped(row):
    return bool(row.get("truncated")) or row["end_line"] != row.get("window_end_line", row["end_line"])


def document_text(row):
    literal(row)
    require(all(isinstance(row.get(key), str) and bool(row[key]) for key in ("id", "path", "symbol"))
            and type(row.get("window_end_line")) is int and type(row.get("truncated")) is bool
            and not clipped(row) and row["window_end_line"] == row["end_line"]
            and row.get("content_hash") == row["source_hash"], "Exact full source document required")
    return f"Path: {row['path']}\nSymbol: {row['symbol']}\nSource:\n{row['source']}"


def row_binding(row):
    literal(row)
    binding = {key: row[key] for key in
               ("id", "path", "symbol", "start_line", "end_line", "source_hash")}
    binding["document_sha256"] = digest(document_text(row))
    for key in PROVENANCE:
        if key in row:
            binding[key] = json_data(row[key])
    return binding


def json_data(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    restored = json.loads(encoded)
    require(same(value, restored), "Literal finite JSON metadata types required")
    return restored


def audit_features(reference, raw_rows, features):
    """Bind every semantic scalar to the actual query/model/source capture."""
    clauses = literal_clauses(reference["query"])
    facets = [reference["query"], *clauses] if clauses else []
    require(isinstance(features, dict)
            and set(features) == {"schema", "query", "facets", "query_prompt", "model_metadata",
                                  "model_fingerprint", "facet_vectors", "windows"}
            and features.get("schema") == FEATURE_SCHEMA,
            "Semantic feature schema")
    require(features.get("query") == reference["query"] and same(features.get("facets"), facets),
            "Feature query/facets differ from literal query")
    require(features.get("query_prompt") == QUERY_PROMPT, "Feature query prompt differs")
    metadata = features.get("model_metadata")
    require(isinstance(metadata, dict) and metadata.get("id") == MODEL_ID
            and metadata.get("revision") == MODEL_REVISION
            and type(metadata.get("dimensions")) is int and metadata["dimensions"] == DIMENSIONS
            and metadata.get("query_prompt") == QUERY_PROMPT
            and metadata.get("normalization") == "L2, float32", "Pinned semantic model metadata differs")
    json_data(metadata)
    encoded_metadata = json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    require(features.get("model_fingerprint") == hashlib.sha256(encoded_metadata).hexdigest(),
            "Semantic model fingerprint differs")
    vectors = features.get("facet_vectors")
    require(isinstance(vectors, list) and len(vectors) == len(facets), "Facet vector count differs")
    for vector in vectors:
        require(len(float32_vector(vector)) == DIMENSIONS, "Pinned facet dimensions differ")
    require(isinstance(raw_rows, list) and len(raw_rows) <= 50
            and len({row["id"] for row in raw_rows}) == len(raw_rows), "Raw feature window cap/identity")
    require(all(not clipped(row) for row in raw_rows), "Raw feature windows must be full")
    raw_by_id = {row["id"]: row for row in raw_rows}
    require(isinstance(reference.get("results"), list)
            and len({row["id"] for row in reference["results"]}) == len(reference["results"]),
            "Reference feature identity schema")
    for row in reference["results"]:
        literal(row)
        require(all(isinstance(row.get(key), str) and row[key] for key in ("id", "path", "symbol"))
                and type(row.get("window_end_line")) is int and row["end_line"] <= row["window_end_line"]
                and type(row.get("truncated")) is bool
                and isinstance(row.get("content_hash"), str)
                and re.fullmatch(r"[0-9a-f]{64}", row["content_hash"]) is not None,
                "Reference literal window schema")
        for name in PROVENANCE:
            if name in row:
                json_data(row[name])
        if not clipped(row) or row["id"] not in raw_by_id:
            continue
        full = raw_by_id[row["id"]]
        require(all(key in row and key in full and same(row[key], full[key]) for key in
                    ("id", "path", "symbol", "start_line", "window_end_line", "content_hash"))
                and row["end_line"] <= full["end_line"]
                and row["source"] == "".join(physical_lines(full["source"])[
                    :row["end_line"]-row["start_line"]+1])
                and all((key in row) == (key in full) and same(row.get(key), full.get(key))
                        for key in PROVENANCE), "Clipped/full window identity or prefix differs")
    rows = {}
    for row in [*raw_rows, *reference["results"]]:
        if clipped(row):
            continue
        binding = row_binding(row)
        require(row["id"] not in rows or same(rows[row["id"]], binding), "Conflicting full-window feature identity")
        rows[row["id"]] = binding
    windows = features.get("windows")
    require(isinstance(windows, dict) and set(windows) == set(rows), "Exact semantic feature membership differs")
    scores = {}
    for identifier, binding in rows.items():
        captured = windows[identifier]
        require(isinstance(captured, dict) and set(captured) == {"binding", "vector"}
                and same(captured["binding"], binding), "Feature document/source/provenance binding differs")
        vector = captured["vector"]
        require(len(float32_vector(vector)) == DIMENSIONS, "Pinned document dimensions differ")
        scores[identifier] = tuple(cosine_ppm(facet, vector) for facet in vectors)
    return facets, scores


def canonical_key(value):
    if not isinstance(value, (tuple, list)) or len(value) not in (4, 5):
        return None
    if not all(isinstance(label, str) for label in value[:2]):
        return None
    if type(value[2]) is not int or type(value[3]) is not int or not 1 <= value[2] <= value[3]:
        return None
    if len(value) == 5 and (type(value[4]) is not int or value[4] < 0):
        return None
    return tuple(value)


def identity(row, keys):
    key = canonical_key(keys.get(row["id"]))
    return ("definition", key) if key is not None else ("window", row["id"])


def protected_rows(reference, raw_rows, keys, max_results, precoalesce_rows=()):
    distinct, seen = [], set()
    for row in raw_rows:
        key = identity(row, keys)
        if key not in seen:
            distinct.append(row)
            seen.add(key)
    anchors = distinct[:min(5, max_results)]
    windows = {row["id"]: row for row in [*raw_rows, *precoalesce_rows, *reference["results"]]}
    dependencies = [row for row in [*precoalesce_rows, *reference["results"]]
                    if row.get("origin") == "dependency"]
    owners = [windows[row["related_to"]] for row in dependencies if row.get("related_to") in windows]
    spans = [*anchors, *dependencies, *owners]
    protected = {}
    for row in reference["results"]:
        reasons = []
        if clipped(row):
            reasons.append("clipped")
        if row.get("origin") == "dependency":
            reasons.append("dependency")
        if any(overlap(row, span) for span in spans):
            reasons.append("protected_source_overlap")
        if reasons:
            protected[row["id"]] = reasons
    return protected, [row["id"] for row in anchors]


def semantic_support(rows, scores, facet_count):
    full = [row for row in rows if not clipped(row)]
    return tuple(max((scores[row["id"]][index] for row in full), default=-SCALE)
                 for index in range(facet_count))


def literal_covers(outer, inner):
    """Original coalescer's containment test; no fabricated source union."""
    path, low, high, lines = literal(outer)
    other_path, other_low, other_high, other_lines = literal(inner)
    return (path == other_path and low <= other_low <= other_high <= high
            and lines[other_low-low:other_high-low+1] == other_lines)


def precoalesce_covered(reference, rows):
    if len(rows) > 8 or len({row.get("id") for row in rows}) != len(rows):
        return False
    try:
        return all(any(literal_covers(current, original)
                       and all((key in current) == (key in original)
                               and same(current.get(key), original.get(key)) for key in PROVENANCE)
                       for current in reference["results"]) for original in rows)
    except (ValueError, TypeError, KeyError):
        return False


def guard(reference, proposal, protected_ids, scores, facet_count, *, allowed_rows,
          max_results, source_budget, token_budget, counter):
    proposed = {row["id"]: row for row in proposal["results"]}
    source_ok = all(row["id"] in proposed and same(proposed[row["id"]], row)
                    for row in reference["results"] if row["id"] in protected_ids)
    literal_ok = (len(proposed) == len(proposal["results"])
                  and all(row["id"] in allowed_rows and same(row, allowed_rows[row["id"]])
                          for row in proposal["results"]))
    old = semantic_support(reference["results"], scores, facet_count)
    new = semantic_support(proposal["results"], scores, facet_count)
    semantic_ok = all(current >= previous for current, previous in zip(new, old))
    trial = deepcopy(proposal)
    tokens = recount(trial, token_budget, counter)
    budgets_ok = (len(trial["results"]) <= max_results and trial["total_source_chars"] <= source_budget
                  and (token_budget is None or tokens <= token_budget))
    accepted = source_ok and literal_ok and semantic_ok and budgets_ok
    delivered = deepcopy(trial if accepted else reference)
    if accepted:
        delivered.pop("_selection_backfill", None)
    return delivered, {
        "protected_source_preserved": source_ok, "literal_rows_preserved": literal_ok,
        "semantic_floor_preserved": semantic_ok, "budgets_preserved": budgets_ok,
        "reference_support": list(old), "proposal_support": list(new), "accepted": accepted,
        "fallback_reason": (None if accepted else "protected_source_changed" if not source_ok
                            else "literal_row_changed" if not literal_ok
                            else "semantic_floor_decreased" if not semantic_ok else "budget_exceeded")}


def reconstruct(reference, raw_rows, features, *, definition_keys=None, max_results=8,
                source_budget=12000, token_budget=4096, counter, precoalesce_rows=()):
    """Independently enumerate the fixed bounded exchanges in final row space."""
    require(type(max_results) is int and max_results >= 0
            and type(source_budget) is int and source_budget >= 0
            and (token_budget is None or type(token_budget) is int and token_budget >= 0),
            "Context budget schema")
    keys, raw, precoalesced = definition_keys or {}, list(raw_rows), list(precoalesce_rows)
    receipt = {"schema": "semantic_final_context_v1", "mode": "semantic_final_v1", "activated": False,
        "score_scale": SCALE, "max_exchanges": MAX_SWAPS, "max_budget_trials": MAX_BUDGET_TRIALS,
        "raw_windows_per_definition": MAX_RAW_WINDOWS_PER_DEFINITION, "anchor_ids": [],
        "protected_rows": {}, "portfolio_ids": [], "facets": [], "reference_support": [],
        "final_support": [], "trials": [], "exchanges": [], "accepted": False,
        "fallback_reason": None, "termination": None, "guard": None, "subsequent_backfill_calls": 0}

    def fallback(reason, error=None):
        receipt.update(fallback_reason=reason, termination=reason)
        if error is not None:
            receipt["feature_error"] = error
        return deepcopy(reference), deepcopy(receipt)

    if not literal_clauses(reference["query"]):
        return fallback("no_literal_clauses")
    receipt["activated"] = True
    if not precoalesce_covered(reference, precoalesced):
        return fallback("invalid_precoalesce_source")
    known_ids = {row["id"] for row in [*raw, *reference["results"], *precoalesced]}
    dependencies = [row for row in [*precoalesced, *reference["results"]]
                    if row.get("origin") == "dependency"]
    if any(not isinstance(row.get("related_to"), str) or row["related_to"] not in known_ids
           for row in dependencies):
        return fallback("unresolved_dependency_owner")
    try:
        facets, scores = audit_features(reference, raw, features)
    except (ValueError, TypeError, KeyError) as error:
        return fallback("invalid_features", str(error))
    receipt["facets"] = facets
    protected, anchors = protected_rows(reference, raw, keys, max_results, precoalesced)
    receipt.update(anchor_ids=anchors, protected_rows=protected)
    if any(identifier not in known_ids
           for identifier in reference.get("_selection_backfill", {}).get("coalesced_ids", [])):
        return fallback("unobservable_coalesced_source")
    portfolio, counts, ranks = [], {}, {}
    for rank, row in enumerate(raw, 1):
        if row["id"] in ranks:
            return fallback("duplicate_raw_id")
        ranks[row["id"]] = rank
        key = identity(row, keys)
        if counts.get(key, 0) < MAX_RAW_WINDOWS_PER_DEFINITION:
            portfolio.append(row)
            counts[key] = counts.get(key, 0) + 1
    receipt["portfolio_ids"] = [row["id"] for row in portfolio]
    current = deepcopy(reference)
    initial = semantic_support(current["results"], scores, len(facets))
    receipt["reference_support"] = list(initial)
    for _ in range(MAX_SWAPS):
        previous = semantic_support(current["results"], scores, len(facets))
        present = {row["id"] for row in current["results"]}
        options = []
        for candidate in portfolio:
            if candidate["id"] in present or clipped(candidate):
                continue
            if candidate.get("origin", "retrieval") != "retrieval" or candidate.get("related_to") is not None:
                continue
            if any(literal_covers(row, candidate) for row in current["results"]):
                continue
            victims = [(index, row) for index, row in enumerate(current["results"])
                       if row["id"] not in protected]
            if len(current["results"]) < max_results:
                victims.append((len(current["results"]), None))
            for position, victim in victims:
                rows = ([*current["results"], candidate] if victim is None else
                        [candidate if index == position else row for index, row in enumerate(current["results"])])
                proposed = semantic_support(rows, scores, len(facets))
                delta = [new - old for old, new in zip(previous, proposed)]
                if all(value >= 0 for value in delta) and sum(delta) > 0:
                    options.append((-sum(delta), ranks[candidate["id"]], -position,
                                    candidate, victim, position, proposed))
        options.sort(key=lambda value: value[:3])
        accepted = False
        for negative_gain, rank, _, candidate, victim, position, proposed in options:
            if len(receipt["trials"]) >= MAX_BUDGET_TRIALS:
                break
            trial = deepcopy(current)
            if victim is None:
                trial["results"].append(deepcopy(candidate))
            else:
                trial["results"][position] = deepcopy(candidate)
            tokens = recount(trial, token_budget, counter)
            fits = (len(trial["results"]) <= max_results and trial["total_source_chars"] <= source_budget
                    and (token_budget is None or tokens <= token_budget))
            item = {"candidate_id": candidate["id"], "victim_id": victim["id"] if victim is not None else None, "raw_rank": rank,
                "victim_position": position + 1, "gain": -negative_gain, "old_support": list(previous),
                "proposed_support": list(proposed), "source_chars": trial["total_source_chars"],
                "tokens": tokens, "accepted": fits, "reason": None if fits else "budget_exceeded"}
            receipt["trials"].append(item)
            if fits:
                current = trial
                receipt["exchanges"].append(deepcopy(item))
                accepted = True
                break
        if not accepted:
            receipt["termination"] = ("budget_trial_limit" if len(receipt["trials"]) >= MAX_BUDGET_TRIALS
                                      else "no_feasible_positive_exchange")
            break
    if not receipt["exchanges"]:
        receipt["final_support"] = list(initial)
        return fallback(receipt["termination"] or "no_feasible_positive_exchange")
    receipt["termination"] = receipt["termination"] or "max_exchanges"
    final, checked = guard(reference, current, protected, scores, len(facets),
        max_results=max_results, source_budget=source_budget, token_budget=token_budget, counter=counter,
        allowed_rows={row["id"]: row for row in [*raw, *reference["results"]]})
    receipt.update(guard=checked, accepted=checked["accepted"], fallback_reason=checked["fallback_reason"],
                   final_support=list(semantic_support(final["results"], scores, len(facets))))
    return final, deepcopy(receipt)


def verify_record(record, counter, *, reference=None, raw_rows=None, features=None,
                  definition_keys=None, precoalesce_rows=None):
    """Verify one explicit capture; optional trusted operands reject input rewrites.

    A future observed-data runner must bind these operands to its independently
    qualified original controls/source inventories and feature capture manifest.
    This preparation module itself opens no corpus, ledger, model or index.
    """
    for name, trusted in (("reference_response", reference), ("ranked_candidates", raw_rows),
                          ("features", features), ("definition_keys", definition_keys),
                          ("precoalesce_rows", precoalesce_rows)):
        require(name in record, "Missing captured operand: " + name)
        if trusted is not None:
            require(same(record[name], trusted), "Captured operand differs from trusted input: " + name)
    reference = record["reference_response"]
    raw_rows = record["ranked_candidates"]
    keys = record["definition_keys"]
    limits = record.get("limits", {"max_results": 8, "source_budget": 12000, "token_budget": 4096})
    require(isinstance(limits, dict) and set(limits) == {"max_results", "source_budget", "token_budget"},
            "Explicit context limit schema")
    expected, receipt = reconstruct(reference, raw_rows, record["features"],
        definition_keys=keys, counter=counter, precoalesce_rows=record["precoalesce_rows"], **limits)
    actual = record.get("semantic_receipt")
    # Exception strings are diagnostics rather than a selector decision. Both
    # implementations must independently fail on the actual invalid capture;
    # their wording may differ without weakening the exact fallback contract.
    if receipt.get("fallback_reason") == "invalid_features" and isinstance(actual, dict):
        require(isinstance(actual.get("feature_error"), str) and bool(actual["feature_error"]),
                "Actual invalid-feature diagnostic required")
        actual = deepcopy(actual)
        actual["feature_error"] = receipt["feature_error"]
    require(same(actual, receipt), "Semantic selection/guard receipt differs")
    require(same_source_response(record.get("response"), expected), "Actual final response differs")
    payload = compact_payload(expected)
    require(record.get("compact_payload") == payload, "Actual compact payload differs")
    require(record.get("compact_sha256") == digest(payload), "Actual compact payload hash differs")
    actual_tokens = len(counter.encode(payload, disallowed_special=()))
    require(type(record.get("actual_compact_tokens")) is int
            and record["actual_compact_tokens"] == actual_tokens, "Actual compact token receipt differs")
    if limits["token_budget"] is not None:
        require(expected.get("token_usage", {}).get("tokens") == actual_tokens,
                "Public compact token count differs")
    active = bool(literal_clauses(reference["query"]))
    for name, count in (("coverage_coalesce_calls", 1), ("semantic_calls", 1),
                        ("feature_calls", int(active))):
        require(type(record.get(name)) is int and record[name] == count,
                "Actual call count differs: " + name)
    if "subsequent_backfill_calls" in record:
        require(type(record["subsequent_backfill_calls"]) is int
                and record["subsequent_backfill_calls"] == 0, "A final exchange cannot run another backfill")
    return deepcopy(record["response"])
