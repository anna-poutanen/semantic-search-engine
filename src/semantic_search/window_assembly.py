"""Transient, literal-source window unions; indexed snippets remain unchanged."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from semantic_search.context import ENCODING, count_context
from semantic_search.corpus import Snippet
from semantic_search.source import physical_lines


MAX_EXTENSION_TRIALS = 8
_PROVENANCE = ("source_origin", "buffer_version", "buffer_hash")


@dataclass(frozen=True)
class AssembledSnippet(Snippet):
    assembly: dict


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _literal(row, *, full=False):
    source, start, end = row.get("source"), row.get("start_line"), row.get("end_line")
    if (not isinstance(source, str) or not source or type(start) is not int
            or type(end) is not int or start < 1 or not isinstance(row.get("path"), str)):
        return None
    lines = physical_lines(source)
    if end != start + len(lines) - 1 or row.get("source_hash") != _sha(source):
        return None
    if full and (row.get("window_end_line", end) != end
                 or row.get("content_hash") != _sha(source) or row.get("truncated", False)):
        return None
    return start, end, lines


def _valid_key(key, path, symbol, start, end):
    return (isinstance(key, (tuple, list)) and len(key) in (4, 5)
            and tuple(key[:2]) == (path, symbol)
            and type(key[2]) is int and type(key[3]) is int
            and 1 <= key[2] <= start <= end <= key[3]
            and (len(key) == 4 or (type(key[4]) is int and key[4] >= 0)))


def _member(row):
    return {"id": row["id"], "path": row["path"], "symbol": row["symbol"],
            "start_line": row["start_line"], "end_line": row.get("window_end_line", row["end_line"]),
            "content_hash": row["content_hash"], "used_start_line": row["start_line"],
            "used_end_line": row["end_line"], "used_source_hash": row["source_hash"]}


def _identity(members, definition_key, source, start, end):
    canonical = sorted(members, key=lambda item: (item["id"], item["used_start_line"],
                                                item["used_end_line"], item["used_source_hash"]))
    binding = {"kind": "connected_windows", "definition_key": list(definition_key),
               "members": canonical, "start_line": start, "end_line": end,
               "content_hash": _sha(source)}
    digest = hashlib.sha256(json.dumps(binding, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return "context:connected_windows:" + digest


def _assembly(anchor, companion, definition_key, anchor_rank, companion_rank):
    return {"kind": "connected_windows", "definition_key": list(definition_key),
            "members": [_member(anchor), _member(companion)],
            "score_anchor_id": anchor["id"], "anchor_rank": anchor_rank,
            "companion_rank": companion_rank}


def _join(left, right):
    """Join only connected, consistent physical lines, without reading files."""
    a, b = _literal(left), _literal(right)
    if a is None or b is None or left["path"] != right["path"]:
        return None
    if a[0] > b[0]:
        a, b = b, a
    start, end, lines = a
    other_start, other_end, other_lines = b
    if other_start > end + 1:
        return None
    overlap = max(0, min(end, other_end) - other_start + 1)
    if lines[other_start - start:other_start - start + overlap] != other_lines[:overlap]:
        return None
    source = "".join(lines + other_lines[max(0, end - other_start + 1):])
    final = max(end, other_end)
    if len(physical_lines(source)) != final - start + 1:
        return None
    return start, final, source


def _snippet_row(snippet):
    return {"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
            "start_line": snippet.start_line, "end_line": snippet.end_line,
            "window_end_line": snippet.end_line, "source": snippet.text,
            "source_hash": snippet.content_hash, "content_hash": snippet.content_hash}


def join_windows(anchor, companion, *, definition_key, anchor_rank, companion_rank):
    """Create an ephemeral union of two full retrieved windows of one definition.

    Callers must establish that both IDs map to ``definition_key``. Bounds and
    labels are checked here too; a union never represents sibling definitions.
    Its retrieval score belongs to the anchor and is deliberately not stored here.
    """
    first, second = _snippet_row(anchor), _snippet_row(companion)
    if (anchor.path != companion.path or anchor.symbol != companion.symbol
            or not _valid_key(definition_key, anchor.path, anchor.symbol,
                              min(anchor.start_line, companion.start_line),
                              max(anchor.end_line, companion.end_line))
            or _literal(first, full=True) is None or _literal(second, full=True) is None):
        return None
    joined = _join(first, second)
    if joined is None:
        return None
    start, end, source = joined
    if start == anchor.start_line and end == anchor.end_line:
        return None
    assembly = _assembly(first, second, definition_key, anchor_rank, companion_rank)
    identity = _identity(assembly["members"], definition_key, source, start, end)
    return AssembledSnippet(identity, anchor.path, anchor.symbol, start, end,
                            source, _sha(source), assembly)


def _same_provenance(first, second):
    return all((field in first) == (field in second) and first.get(field) == second.get(field)
               for field in _PROVENANCE)


def _covers(outer, inner):
    a, b = _literal(outer), _literal(inner)
    return (a is not None and b is not None and outer["path"] == inner["path"]
            and _same_provenance(outer, inner) and a[0] <= b[0] <= b[1] <= a[1]
            and a[2][b[0] - a[0]:b[1] - a[0] + 1] == b[2])


def _preserved(original, current):
    return all(any(_covers(outer, row) for outer in current) for row in original)


def _recount(response, token_budget):
    response["total_source_chars"] = sum(len(row["source"]) for row in response["results"])
    if token_budget is not None:
        response.setdefault("token_usage", {})
        response["token_usage"].update(encoding=ENCODING, budget=token_budget)
        response["token_usage"].setdefault("truncated", False)
    return count_context(response) if "token_usage" in response else None


def extend_context(response, candidates, *, ranked_candidates=None, definition_keys=None,
                   max_results, source_budget, token_budget=None, cancel_check=lambda: None):
    """Extend delivered retrieval prefixes using one connected retrieved companion.

    Existing source can never be removed. Only a new suffix is clipped; complete
    intended union metadata remains verifiable against saved source. Raw ranked
    full rows are supplied through ``ranked_candidates`` or, by default,
    ``candidates``. Debug and assembly receipts stay outside compact MCP context.
    """
    if (type(max_results) is not int or max_results < 0
            or type(source_budget) is not int or source_budget < 0
            or (token_budget is not None and (type(token_budget) is not int or token_budget < 0))):
        raise ValueError("Context limits must be nonnegative integers")
    cancel_check()
    result = copy.deepcopy(response)
    originals = copy.deepcopy(result["results"])
    debug = {"applied": [], "rejected": [], "old_to_new_ids": {}, "trials": 0,
             "trial_limit": MAX_EXTENSION_TRIALS, "trial_limit_reached": False,
             "coverage_preserved": False, "fallback_reason": None}

    def finish(value):
        value["_window_assembly"] = debug
        return value

    def fallback(reason):
        debug["fallback_reason"] = reason
        debug["rejected_applied"] = debug["applied"]
        debug["applied"], debug["old_to_new_ids"] = [], {}
        return finish(copy.deepcopy(response))

    tokens = _recount(result, token_budget)
    if (len(originals) > max_results or result["total_source_chars"] > source_budget
            or (token_budget is not None and tokens > token_budget)):
        return fallback("base_budget_exceeded")
    if not _preserved(originals, result["results"]):
        return fallback("invalid_original_source")
    pool = list(ranked_candidates if ranked_candidates is not None else candidates)
    ranks = {row["id"]: rank for rank, row in reversed(list(enumerate(pool, 1)))}
    keys = definition_keys or {}
    for index, anchor in enumerate(originals):
        cancel_check()
        key = keys.get(anchor.get("id"))
        if (anchor.get("origin", "retrieval") != "retrieval" or "assembly" in anchor
                or not _valid_key(key, anchor["path"], anchor["symbol"],
                                  anchor["start_line"], anchor.get("window_end_line", anchor["end_line"]))):
            continue
        for rank, candidate in enumerate(pool, 1):
            cancel_check()
            candidate_key = keys.get(candidate.get("id"))
            if (not isinstance(candidate_key, (tuple, list)) or tuple(candidate_key) != tuple(key)
                    or candidate.get("path") != anchor["path"]
                    or candidate.get("symbol") != anchor["symbol"]
                    or candidate.get("origin", "retrieval") != "retrieval"
                    or "assembly" in candidate):
                continue
            literal = _literal(candidate, full=True)
            if literal is None:
                debug["rejected"].append({"anchor_id": anchor["id"], "id": candidate.get("id"),
                                          "reason": "invalid_full_candidate"})
                continue
            start, end, _ = literal
            if not anchor["start_line"] <= start <= anchor["end_line"] + 1 or end <= anchor["end_line"]:
                continue
            if debug["trials"] >= MAX_EXTENSION_TRIALS:
                debug["trial_limit_reached"] = True
                break
            debug["trials"] += 1
            if (not _same_provenance(anchor, candidate)
                    or not _valid_key(key, candidate["path"], candidate["symbol"], start, end)):
                debug["rejected"].append({"anchor_id": anchor["id"], "id": candidate["id"],
                                          "reason": "incompatible_source_binding"})
                continue
            joined = _join(anchor, candidate)
            if joined is None:
                debug["rejected"].append({"anchor_id": anchor["id"], "id": candidate["id"],
                                          "reason": "inconsistent_overlap"})
                continue
            start, end, full = joined
            tail = physical_lines(full)[len(physical_lines(anchor["source"])):]
            remaining = source_budget - result["total_source_chars"]
            added, used = [], 0
            for line in tail:
                cancel_check()
                if used + len(line) > remaining:
                    break
                added.append(line)
                used += len(line)
            if not added:
                debug["rejected"].append({"anchor_id": anchor["id"], "id": candidate["id"],
                                          "reason": "no_character_suffix"})
                continue
            assembly = _assembly(anchor, candidate, key, ranks.get(anchor["id"]), rank)
            assembly["inherited_scores"] = {field: anchor.get(field)
                                            for field in ("retrieval_score", "h2o_score", "qwen_score")}
            identity = _identity(assembly["members"], key, full, start, end)
            trial = copy.deepcopy(result)
            new = copy.deepcopy(anchor)
            new.update(id=identity, window_end_line=end, content_hash=_sha(full), assembly=assembly,
                       h2o_score=None, qwen_score=None)
            trial["results"][index] = new
            for row in trial["results"]:
                if row.get("related_to") == anchor["id"]:
                    row["related_to"] = identity
            token_clipped = False
            while added:
                cancel_check()
                source = anchor["source"] + "".join(added)
                new.update(source=source, end_line=anchor["end_line"] + len(added),
                           source_hash=_sha(source), truncated=source != full)
                if source != full:
                    trial["budget_exhausted"] = True
                if token_clipped:
                    trial["budget_exhausted"] = True
                    trial["token_usage"]["truncated"] = True
                tokens = _recount(trial, token_budget)
                if token_budget is None or tokens <= token_budget:
                    break
                token_clipped = True
                added.pop()
            if not added:
                debug["rejected"].append({"anchor_id": anchor["id"], "id": candidate["id"],
                                          "reason": "no_token_suffix"})
                continue
            if not _preserved(originals, trial["results"]):
                return fallback("original_source_not_preserved_during_extension")
            result = trial
            debug["applied"].append({"anchor_id": anchor["id"], "companion_id": candidate["id"],
                                     "id": identity, "added_lines": len(added),
                                     "token_clipped": token_clipped})
            debug["old_to_new_ids"][anchor["id"]] = identity
            break
        if debug["trial_limit_reached"]:
            break
    tokens = _recount(result, token_budget)
    if (len(result["results"]) > max_results or result["total_source_chars"] > source_budget
            or (token_budget is not None and tokens > token_budget)
            or not _preserved(originals, result["results"])):
        return fallback("final_source_or_budget_guard_failed")
    debug["coverage_preserved"] = True
    return finish(result)
