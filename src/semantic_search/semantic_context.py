"""Research-only semantic admissions in an actual, already packed coverage result.

No retrieval, inference, source assembly or subsequent backfill occurs here.
Integer semantic features are allocation heuristics, never retrieval scores or
proof that an implementation satisfies a task.
"""
from __future__ import annotations

from copy import deepcopy

from semantic_search.context import count_context
from semantic_search.context_backfill import _covers
from semantic_search.retrieval import multifile_clauses
from semantic_search.source_allocation import _canonical_key, _literal


MODE = "semantic_final_v1"
SCHEMA = "semantic_final_context_v1"
MAX_RAW_WINDOWS_PER_DEFINITION = 2
MAX_EXCHANGES = 2
MAX_BUDGET_TRIALS = 8
SCORE_SCALE = 1_000_000


def definition_identity(row, definition_keys=None):
    key = _canonical_key((definition_keys or {}).get(row["id"]))
    return ("definition", key) if key is not None else ("window", row["id"])


def is_clipped(row):
    return bool(row.get("truncated")) or row["end_line"] != row.get("window_end_line", row["end_line"])


def protected_final_rows(reference, raw_rows, definition_keys=None, max_results=8,
                         precoalesce_rows=()):
    """Protect actual rows overlapping original anchors, dependencies and owners.

    ``related_to`` remains an original source-window identifier. It need not be a
    returned row ID: the original source may be inside a delivered containing
    row. No graph edges or new owner identifiers are invented.
    """
    distinct, seen = [], set()
    for row in raw_rows:
        identity = definition_identity(row, definition_keys)
        if identity not in seen:
            seen.add(identity)
            distinct.append(row)
    anchors = distinct[:min(5, max_results)]
    rows = reference["results"]
    windows = {row["id"]: row for row in [*raw_rows, *precoalesce_rows, *rows]}
    dependencies = [row for row in [*precoalesce_rows, *rows] if row.get("origin") == "dependency"]
    owners = [windows[row["related_to"]] for row in dependencies if row.get("related_to") in windows]
    protected_windows = [*anchors, *dependencies, *owners]
    reasons = {}
    for row in rows:
        why = []
        if is_clipped(row):
            why.append("clipped")
        if row.get("origin") == "dependency":
            why.append("dependency")
        for window in protected_windows:
            if (row["path"] == window["path"] and row["start_line"] <= window["end_line"]
                    and window["start_line"] <= row["end_line"]):
                why.append("protected_source_overlap")
                break
        if why:
            reasons[row["id"]] = why
    return reasons, [row["id"] for row in anchors]


def support(rows, scores, facet_count):
    """Clipped source supplies no semantic credit from its full-window vector."""
    full = [row for row in rows if not is_clipped(row)]
    return tuple(max((scores[row["id"]][index] for row in full), default=-SCORE_SCALE)
                 for index in range(facet_count))


def _recount(response, token_budget):
    response["total_source_chars"] = sum(len(row["source"]) for row in response["results"])
    if token_budget is None:
        return None
    response.setdefault("token_usage", {})
    response["token_usage"].update(encoding="cl100k_base", budget=token_budget)
    response["token_usage"].setdefault("truncated", False)
    return count_context(response)


def _limits(max_results, source_budget, token_budget):
    for name, value in (("max_results", max_results), ("source_budget", source_budget)):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    if token_budget is not None and (type(token_budget) is not int or token_budget < 0):
        raise ValueError("token_budget must be a nonnegative integer or None")


def _same(first, second):
    """Preserve scalar types as well as values, recursively through metadata."""
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(_same(first[key], second[key]) for key in first)
    if isinstance(first, (tuple, list)):
        return len(first) == len(second) and all(_same(a, b) for a, b in zip(first, second))
    return first == second


def _precoalesce_covered(reference, precoalesce_rows):
    if len(precoalesce_rows) > 8 or len({row.get("id") for row in precoalesce_rows}) != len(precoalesce_rows):
        return False
    fields = ("source_origin", "buffer_version", "buffer_hash")
    return all(_literal(original) is not None and any(
        _literal(current) is not None and _covers(current, original)
        and all((name in current) == (name in original)
                and _same(current.get(name), original.get(name)) for name in fields)
        for current in reference["results"]) for original in precoalesce_rows)


def guard_semantic_final(reference, proposal, *, protected_ids, scores, facet_count,
                         max_results, source_budget, token_budget, allowed_rows):
    """Check the actual final payload against the actual final coverage payload."""
    protected = [row for row in reference["results"] if row["id"] in protected_ids]
    proposed = {row["id"]: row for row in proposal["results"]}
    # Direct exchanges preserve each protected dictionary exactly, including
    # scalar field presence/types, dependency owner and saved/buffer provenance.
    preserved = all(row["id"] in proposed and _same(proposed[row["id"]], row) for row in protected)
    literal = (len(proposed) == len(proposal["results"])
               and all(row["id"] in allowed_rows and _same(row, allowed_rows[row["id"]])
                       for row in proposal["results"]))
    old = support(reference["results"], scores, facet_count)
    # Never credit a forged literal row with the original window's features.
    new = support(proposal["results"], scores, facet_count) if literal else ()
    semantic_preserved = literal and all(value >= previous for value, previous in zip(new, old))
    trial = deepcopy(proposal)
    tokens = _recount(trial, token_budget)
    budgets = (len(trial["results"]) <= max_results
               and trial["total_source_chars"] <= source_budget
               and (token_budget is None or tokens <= token_budget))
    accepted = preserved and literal and semantic_preserved and budgets
    delivered = deepcopy(trial if accepted else reference)
    if accepted:
        # This diagnostic described the baseline's containment/backfill pass.
        # After a direct exchange it can no longer describe delivered coverage.
        delivered.pop("_selection_backfill", None)
    return delivered, {
        "protected_source_preserved": preserved, "literal_rows_preserved": literal,
        "semantic_floor_preserved": semantic_preserved,
        "budgets_preserved": budgets, "reference_support": list(old), "proposal_support": list(new),
        "accepted": accepted,
        "fallback_reason": (None if accepted else "protected_source_changed" if not preserved
                            else "literal_row_changed" if not literal
                            else "semantic_floor_decreased" if not semantic_preserved else "budget_exceeded"),
    }


def semantic_final_context(reference, raw_rows, features, *, definition_keys=None,
                           max_results=8, source_budget=12000, token_budget=4096,
                           cancel_check=lambda: None, precoalesce_rows=()):
    """Admit at most two whole windows after one ordinary coverage pass.

    ``features`` is validated against actual literal full windows by the separate
    feature boundary. Any unavailable/invalid feature returns an exact defensive
    copy of coverage. The final response receives no research-only receipt.
    """
    _limits(max_results, source_budget, token_budget)
    cancel_check()
    raw_rows = list(raw_rows)
    precoalesce_rows = list(precoalesce_rows)
    receipt = {"schema": SCHEMA, "mode": MODE, "activated": False,
               "score_scale": SCORE_SCALE, "max_exchanges": MAX_EXCHANGES,
               "max_budget_trials": MAX_BUDGET_TRIALS,
               "raw_windows_per_definition": MAX_RAW_WINDOWS_PER_DEFINITION,
               "anchor_ids": [], "protected_rows": {}, "portfolio_ids": [],
               "facets": [], "reference_support": [], "final_support": [],
               "trials": [], "exchanges": [], "accepted": False,
               "fallback_reason": None, "termination": None, "guard": None,
               "subsequent_backfill_calls": 0}

    def fallback(reason, *, error=None):
        receipt.update(fallback_reason=reason, termination=reason)
        if error is not None:
            receipt["feature_error"] = error
        return deepcopy(reference), deepcopy(receipt)

    if not multifile_clauses(reference["query"]):
        return fallback("no_literal_clauses")
    receipt["activated"] = True
    if not _precoalesce_covered(reference, precoalesce_rows):
        return fallback("invalid_precoalesce_source")
    known_ids = {row["id"] for row in [*reference["results"], *raw_rows, *precoalesce_rows]}
    dependencies = [row for row in [*precoalesce_rows, *reference["results"]]
                    if row.get("origin") == "dependency"]
    if any(not isinstance(row.get("related_to"), str) or row["related_to"] not in known_ids
           for row in dependencies):
        return fallback("unresolved_dependency_owner")
    # Feature validation is deliberately lazy: inactive requests need no NumPy,
    # index, model or feature object at all.
    from semantic_search.semantic_features import validated_scores
    try:
        facets, scores = validated_scores(reference, raw_rows, features, definition_keys=definition_keys)
    except (ValueError, TypeError, KeyError) as error:
        return fallback("invalid_features", error=str(error))
    cancel_check()
    if not (3 <= len(facets) <= 5 and all(isinstance(facet, str) for facet in facets)):
        return fallback("invalid_features", error="Expected full query and two to four literal clauses")
    full_rows = [row for row in [*reference["results"], *raw_rows] if not is_clipped(row)]
    if any(row["id"] not in scores or len(scores[row["id"]]) != len(facets)
           or any(type(value) is not int or not -SCORE_SCALE <= value <= SCORE_SCALE
                  for value in scores[row["id"]]) for row in full_rows):
        return fallback("invalid_features", error="Missing or invalid full-window integer scores")
    receipt["facets"] = list(facets)
    protected, anchors = protected_final_rows(reference, raw_rows, definition_keys, max_results,
                                             precoalesce_rows)
    receipt.update(anchor_ids=anchors, protected_rows=protected)
    # A coalesced static dependency outside raw cannot be reconstructed from a
    # final response's ID-only backfill receipt. The research adapter supplies
    # every actual original packed row; callers without that capture fail
    # closed rather than pretending that an unobservable source was protected.
    if any(identifier not in known_ids
           for identifier in reference.get("_selection_backfill", {}).get("coalesced_ids", [])):
        return fallback("unobservable_coalesced_source")
    portfolio, seen_ids, counts = [], set(), {}
    ranks = {}
    for rank, row in enumerate(raw_rows, 1):
        if row["id"] in seen_ids:
            return fallback("duplicate_raw_id")
        seen_ids.add(row["id"])
        ranks[row["id"]] = rank
        identity = definition_identity(row, definition_keys)
        count = counts.get(identity, 0)
        if count < MAX_RAW_WINDOWS_PER_DEFINITION:
            portfolio.append(row)
            counts[identity] = count + 1
    receipt["portfolio_ids"] = [row["id"] for row in portfolio]
    current = deepcopy(reference)
    original_support = support(current["results"], scores, len(facets))
    receipt["reference_support"] = list(original_support)
    for _ in range(MAX_EXCHANGES):
        cancel_check()
        old_support = support(current["results"], scores, len(facets))
        current_ids = {row["id"] for row in current["results"]}
        options = []
        for candidate in portfolio:
            if candidate["id"] in current_ids or is_clipped(candidate):
                continue
            if candidate.get("origin", "retrieval") != "retrieval" or candidate.get("related_to") is not None:
                continue
            if any(_covers(row, candidate) for row in current["results"]):
                continue
            victims = [(position, victim) for position, victim in enumerate(current["results"])
                       if victim["id"] not in protected]
            if len(current["results"]) < max_results:
                victims.append((len(current["results"]), None))
            for position, victim in victims:
                rows = (current["results"] + [candidate] if victim is None else
                        current["results"][:position] + [candidate] + current["results"][position + 1:])
                proposed_support = support(rows, scores, len(facets))
                deltas = [new - old for new, old in zip(proposed_support, old_support)]
                if any(delta < 0 for delta in deltas) or sum(deltas) <= 0:
                    continue
                options.append((-sum(deltas), ranks[candidate["id"]], -position,
                                candidate, victim, position, proposed_support))
        options.sort(key=lambda option: option[:3])
        accepted = False
        for negative_gain, rank, _, candidate, victim, position, proposed_support in options:
            if len(receipt["trials"]) >= MAX_BUDGET_TRIALS:
                break
            cancel_check()
            trial = deepcopy(current)
            if victim is None:
                trial["results"].append(deepcopy(candidate))
            else:
                trial["results"][position] = deepcopy(candidate)
            tokens = _recount(trial, token_budget)
            fits = (len(trial["results"]) <= max_results
                    and trial["total_source_chars"] <= source_budget
                    and (token_budget is None or tokens <= token_budget))
            trial_receipt = {"candidate_id": candidate["id"], "victim_id": victim["id"] if victim is not None else None,
                             "raw_rank": rank, "victim_position": position + 1,
                             "gain": -negative_gain, "old_support": list(old_support),
                             "proposed_support": list(proposed_support),
                             "source_chars": trial["total_source_chars"], "tokens": tokens,
                             "accepted": fits, "reason": None if fits else "budget_exceeded"}
            receipt["trials"].append(trial_receipt)
            if fits:
                current = trial
                receipt["exchanges"].append(deepcopy(trial_receipt))
                accepted = True
                break
        if not accepted:
            receipt["termination"] = ("budget_trial_limit" if len(receipt["trials"]) >= MAX_BUDGET_TRIALS
                                      else "no_feasible_positive_exchange")
            break
    if not receipt["exchanges"]:
        receipt["final_support"] = list(original_support)
        return fallback(receipt["termination"] or "no_feasible_positive_exchange")
    receipt["termination"] = receipt["termination"] or "max_exchanges"
    cancel_check()
    final, guard = guard_semantic_final(reference, current, protected_ids=protected,
        scores=scores, facet_count=len(facets), max_results=max_results,
        source_budget=source_budget, token_budget=token_budget,
        allowed_rows={row["id"]: row for row in [*raw_rows, *reference["results"]]})
    receipt.update(guard=guard, accepted=guard["accepted"], fallback_reason=guard["fallback_reason"],
                   final_support=list(support(final["results"], scores, len(facets))))
    cancel_check()
    return final, deepcopy(receipt)
