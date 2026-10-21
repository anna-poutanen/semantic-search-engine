"""Guarded containment removal and bounded backfill after ordinary context packing.

Existing excerpts are immutable evidence: only an exact physical-line slice of a
single retained excerpt can replace one. Backfill may trim new rows, never rows
that the ordinary packer already delivered.
"""
from __future__ import annotations

import copy
import hashlib

from semantic_search.context import ENCODING, count_context
from semantic_search.source import physical_lines


MAX_BACKFILL_TRIALS = 8


def _literal(row):
    """Reject uncertain bounds instead of inferring coverage from window metadata."""
    source = row.get("source")
    start, end = row.get("start_line"), row.get("end_line")
    if (not isinstance(source, str) or not source or not isinstance(row.get("path"), str)
            or type(start) is not int or type(end) is not int or start < 1):
        return None
    lines = physical_lines(source)
    if end != start + len(lines) - 1:
        return None
    return row["path"], start, end, lines


def _covers(outer, inner):
    containing, contained = _literal(outer), _literal(inner)
    if containing is None or contained is None:
        return False
    path, start, end, lines = containing
    other_path, other_start, other_end, other_lines = contained
    if path != other_path or start > other_start or end < other_end:
        return False
    # Exact strings imply exact UTF-8 bytes; CRLF, bare CR and Unicode separators
    # remain literal source rather than being normalized into logical lines.
    return lines[other_start - start:other_end - start + 1] == other_lines


def _all_covered(original, retained):
    return all(any(_covers(outer, row) for outer in retained) for row in original)


def _usage(response, token_budget):
    if token_budget is not None:
        response.setdefault("token_usage", {})
        response["token_usage"].update(encoding=ENCODING, budget=token_budget)
        response["token_usage"].setdefault("truncated", False)
    elif "token_usage" in response:
        response["token_usage"]["encoding"] = ENCODING


def _recount(response):
    response["total_source_chars"] = sum(len(row["source"]) for row in response["results"])
    return count_context(response) if "token_usage" in response else None


def _prefix(row, lines, full):
    row["source"] = "".join(lines)
    row["end_line"] = row["start_line"] + len(lines) - 1
    row["source_hash"] = hashlib.sha256(row["source"].encode("utf-8")).hexdigest()
    row["truncated"] = bool(row.get("truncated", False)) or row["source"] != full


def coalesce_context(response, candidates, *, max_results, source_budget,
                     token_budget=None, cancel_check=lambda: None):
    """Return a copy with covered rows removed and up to eight new-row trials.

    ``response`` is the ordinary packer's already budgeted response. Candidates
    are full, ordered backend row dictionaries, including their source and origin
    metadata. Original rows and flags remain unchanged. Diagnostic metadata is
    outside the compact tool envelope and never consumes its token budget.
    """
    if (type(max_results) is not int or max_results < 0
            or type(source_budget) is not int or source_budget < 0
            or (token_budget is not None and (type(token_budget) is not int or token_budget < 0))):
        raise ValueError("Context limits must be nonnegative integers")
    cancel_check()
    result = copy.deepcopy(response)
    original = result["results"]
    debug = {"coalesced_ids": [], "appended_ids": [], "trials": 0,
             "trial_limit": MAX_BACKFILL_TRIALS, "trial_limit_reached": False,
             "skipped": [], "coverage_before": False, "coverage_after": False,
             "fallback_reason": None}

    def finish(value):
        value["_selection_backfill"] = debug
        return value

    def fallback(reason):
        debug["fallback_reason"] = reason
        debug["rejected_coalesced_ids"] = debug["coalesced_ids"]
        debug["coalesced_ids"] = []
        debug["appended_ids"] = []
        return finish(copy.deepcopy(response))

    retained = []
    for rank, row in enumerate(original):
        cancel_check()
        covered = any(
            other_rank != rank and _covers(other, row)
            and (not _covers(row, other) or other_rank < rank)
            for other_rank, other in enumerate(original)
        )
        if covered:
            debug["coalesced_ids"].append(row.get("id"))
        else:
            retained.append(row)
    if not _all_covered(original, retained):
        return fallback("original_source_not_covered")
    debug["coverage_before"] = True
    result["results"] = retained
    _usage(result, token_budget)
    tokens = _recount(result)
    if (len(retained) > max_results or result["total_source_chars"] > source_budget
            or (token_budget is not None and tokens > token_budget)):
        return fallback("base_budget_exceeded")
    if not debug["coalesced_ids"]:
        debug["coverage_after"] = True
        return finish(result)

    original_ids = {row.get("id") for row in original}
    seen_ids = set(original_ids)
    for candidate in candidates:
        cancel_check()
        if len(result["results"]) >= max_results:
            break
        identifier = candidate.get("id")
        if identifier in seen_ids:
            debug["skipped"].append({"id": identifier, "reason": "original_or_seen_id"})
            continue
        seen_ids.add(identifier)
        if _literal(candidate) is None:
            debug["skipped"].append({"id": identifier, "reason": "invalid_source_interval"})
            continue
        if any(_covers(row, candidate) for row in result["results"]):
            debug["skipped"].append({"id": identifier, "reason": "covered_full_source"})
            continue
        if debug["trials"] >= MAX_BACKFILL_TRIALS:
            debug["trial_limit_reached"] = True
            break
        debug["trials"] += 1
        cancel_check()
        full = candidate["source"]
        remaining = source_budget - result["total_source_chars"]
        lines, used = [], 0
        for line in physical_lines(full):
            if used + len(line) > remaining:
                break
            lines.append(line)
            used += len(line)
        if not lines:
            debug["skipped"].append({"id": identifier, "reason": "no_character_prefix"})
            continue
        trial = copy.deepcopy(result)
        new = copy.deepcopy(candidate)
        _prefix(new, lines, full)
        trial["results"].append(new)
        if new["source"] != full:
            trial["budget_exhausted"] = True
        token_clipped = False
        if token_budget is not None:
            while count_context(trial) > token_budget:
                cancel_check()
                token_clipped = True
                trial["budget_exhausted"] = True
                trial["token_usage"]["truncated"] = True
                lines.pop()
                if not lines:
                    break
                _prefix(new, lines, full)
        if not lines:
            debug["skipped"].append({"id": identifier, "reason": "no_token_prefix"})
            continue
        if any(_covers(row, new) for row in result["results"]):
            debug["skipped"].append({"id": identifier, "reason": "covered_prefix"})
            continue
        tokens = _recount(trial)
        if (trial["total_source_chars"] > source_budget
                or (token_budget is not None and tokens > token_budget)):
            debug["skipped"].append({"id": identifier, "reason": "trial_budget_exceeded"})
            continue
        # Failed candidates never mutate result, including its flags and counters.
        result = trial
        debug["appended_ids"].append(identifier)
        if token_clipped:
            debug.setdefault("token_clipped_ids", []).append(identifier)

    if not _all_covered(original, result["results"]):
        return fallback("original_source_not_covered_after_backfill")
    debug["coverage_after"] = True
    _recount(result)
    return finish(result)
