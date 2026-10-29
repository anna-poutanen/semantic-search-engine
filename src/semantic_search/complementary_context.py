"""Research-only joint selection of original windows and complementary definitions.

The lexical utility is an allocation heuristic, never a retrieval/model score or
proof of behavior preservation. Production keeps the existing coverage policy.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from fractions import Fraction

from semantic_search import flow_allocation as flow, source_allocation as source
from semantic_search.context_backfill import coalesce_context
from semantic_search.retrieval import _CLAUSE_STOP_WORDS, multifile_clauses, tokenize
from semantic_search.selection import definition_context


MODE = "complementary_v2"
MAX_RAW_WINDOWS_PER_DEFINITION = 2
MAX_ADDITIONS = 2
RANK_OFFSET = 8
SCHEMA = "complementary_context_v2"


def content_terms(text):
    terms = set()
    for term in tokenize(text):
        if term in _CLAUSE_STOP_WORDS or len(term) < 3:
            continue
        if term.endswith("ies") and len(term) > 4:
            term = term[:-3] + "y"
        elif term.endswith("s") and len(term) > 3 and not term.endswith("ss"):
            term = term[:-1]
        terms.add(term)
    return terms


def fraction(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def definition_identity(item, definition_keys):
    key = source._canonical_key((definition_keys or {}).get(item.id))
    return ("definition", key) if key is not None else ("window", item.id)


def aspect_scores(terms, aspects, frequencies):
    """Exact normalized weighted overlap; one window must support an aspect."""
    scores = []
    for aspect in aspects:
        weights = {term: Fraction(1, max(1, frequencies[term])) for term in aspect}
        denominator = sum(weights.values(), Fraction())
        scores.append(sum((value for term, value in weights.items() if term in terms),
                          Fraction()) / denominator if denominator else Fraction())
    return tuple(scores)


def best_support(identifiers, scores, count):
    return tuple(max((scores[identifier][index] for identifier in identifiers),
                     default=Fraction()) for index in range(count))


def complementary_plan(pairs, *, mode=MODE, query, max_results, include_dependencies,
                       references, definition_keys=None, ambiguous_symbols=None):
    """Plan at most two exchanges from the unchanged actually ranked input pool."""
    if mode != MODE:
        raise ValueError("Unknown complementary context mode")
    pairs = list(pairs)
    selected, baseline, origins = definition_context(
        pairs, query=query, max_results=max_results, include_dependencies=include_dependencies,
        references=references, definition_keys=definition_keys, ambiguous_symbols=ambiguous_symbols)
    clauses = multifile_clauses(query)
    aspects = [content_terms(clause) for clause in clauses]
    terms = set().union(*aspects) if aspects else set()
    ranks, portfolio, counts = {}, [], Counter()
    for rank, (item, score) in enumerate(pairs, 1):
        if item.id in ranks:
            continue
        ranks[item.id] = rank
        key = definition_identity(item, definition_keys)
        if counts[key] < MAX_RAW_WINDOWS_PER_DEFINITION:
            portfolio.append((item, score))
            counts[key] += 1
    relevant = {item.id: item for item, _ in selected + baseline + portfolio}
    keys = {identifier: list(key) for identifier, item in relevant.items()
            if (key := source._canonical_key((definition_keys or {}).get(item.id))) is not None}
    plan = {"schema": SCHEMA, "mode": mode, "activated": bool(clauses),
            "clauses": list(clauses), "aspect_terms": [sorted(aspect) for aspect in aspects],
            "term_df": {}, "raw_ranks": ranks, "canonical_keys": keys,
            "base_selected_ids": [item.id for item, _ in selected],
            "baseline_eligible_ids": [item.id for item, _ in baseline],
            "portfolio_ids": [item.id for item, _ in portfolio],
            "raw_windows_per_definition": MAX_RAW_WINDOWS_PER_DEFINITION,
            "dependency_windows_outside_raw_exempt": True,
            "anchor_ids": [item.id for item, _ in selected[:min(5, max_results)]],
            "dependency_owners": {item.id: origins[item.id][1] for item, _ in baseline
                                  if origins.get(item.id, ("retrieval", None))[0] == "dependency"},
            "candidate_terms": {}, "candidate_scores": {}, "graph_witnesses": [],
            "initial_protected_ids": [], "protected_baseline_ids": [],
            "protected_keys": [], "protected_windows": [], "candidate_trials": [],
            "added_ids": [], "evicted_ids": [],
            "proposed_eligible_ids": [item.id for item, _ in baseline],
            "accepted": False, "fallback_reason": None, "termination": None}

    def finish(eligible, reason=None, termination=None):
        plan.update(fallback_reason=reason, termination=termination)
        # Crucial inactive fast path: no alternate-window backfill or lazy
        # reference packing can change or duplicate the original control.
        output = portfolio if plan["accepted"] else selected
        return output, source.PlannedPairs(eligible, plan), origins, deepcopy(plan)

    if not clauses:
        return finish(baseline, "no_literal_clauses", "inactive")
    definitions = {}
    for item, _ in pairs:
        definitions.setdefault(definition_identity(item, definition_keys), set()).update(
            content_terms(f"{item.path} {item.symbol}\n{item.text}") & terms)
    frequencies = Counter(term for values in definitions.values() for term in values)
    plan["term_df"] = {term: frequencies[term] for term in sorted(terms)}
    features = {identifier: content_terms(f"{item.path} {item.symbol}\n{item.text}") & terms
                for identifier, item in relevant.items()}
    scores = {identifier: aspect_scores(values, aspects, frequencies)
              for identifier, values in features.items()}
    plan["candidate_terms"] = {identifier: sorted(values) for identifier, values in features.items()}
    plan["candidate_scores"] = {identifier: [fraction(value) for value in values]
                                for identifier, values in scores.items()}
    witnesses = source._graph_witnesses(selected[:min(5, max_results)], references,
                                         definition_keys, ambiguous_symbols)
    protected_keys = {tuple(witness["neighbor_key"]) for witness in witnesses}
    protected = (set(plan["anchor_ids"]) | set(plan["dependency_owners"])
                 | set(plan["dependency_owners"].values()))
    protected.update(item.id for item, _ in baseline
                     if tuple(keys.get(item.id, ())) in protected_keys)
    plan["graph_witnesses"] = witnesses
    plan["initial_protected_ids"] = [item.id for item, _ in baseline if item.id in protected]
    unique, edges = flow._canonical_graph(references, definition_keys, ambiguous_symbols)
    baseline_ids = {item.id for item, _ in baseline}
    available = [(item, score) for item, score in portfolio if item.id not in baseline_ids]
    additions = []
    stop_reason = None
    while len(additions) < MAX_ADDITIONS:
        represented = protected | {item.id for item, _ in additions}
        support = best_support(represented, scores, len(aspects))
        options = []
        for item, score in available:
            identity = definition_identity(item, definition_keys)
            if sum(other.id in ranks and definition_identity(other, definition_keys) == identity
                   for other, _ in baseline + additions) >= MAX_RAW_WINDOWS_PER_DEFINITION:
                continue
            gain = sum((max(Fraction(), value - old)
                        for value, old in zip(scores[item.id], support)), Fraction())
            if gain:
                options.append((gain / (ranks[item.id] + RANK_OFFSET), ranks[item.id],
                                item, score, gain))
        if not options:
            stop_reason = "no_positive_aspect_gain"
            break
        utility, rank, item, score, gain = min(options, key=lambda value: (-value[0], value[1]))
        candidate_key = source._canonical_key((definition_keys or {}).get(item.id))
        parents, flow_keys, flow_witnesses = (flow._shared_flow(item, unique, edges)
            if candidate_key is not None and unique.get((item.path, item.symbol)) == candidate_key
            else ([], [], []))
        trial_keys = protected_keys | {tuple(key) for key in flow_keys}
        trial_protected = protected | {other.id for other, _ in baseline
                                      if tuple(keys.get(other.id, ())) in trial_keys}
        exchangeable = [other.id for other, _ in baseline if other.id not in trial_protected]
        fits = len(exchangeable) >= len(additions) + 1
        plan["candidate_trials"].append({
            "candidate_id": item.id, "raw_rank": rank,
            "aspect_scores": [fraction(value) for value in scores[item.id]],
            "represented_support": [fraction(value) for value in support],
            "gain": fraction(gain), "utility": fraction(utility),
            "orchestrator_keys": parents, "flow_keys": flow_keys,
            "flow_witnesses": flow_witnesses,
            "trial_protected_baseline_ids": [other.id for other, _ in baseline if other.id in trial_protected],
            "exchangeable_baseline_ids": exchangeable, "required_slots": len(additions) + 1,
            "accepted": fits, "stop_reason": None if fits else "insufficient_exchangeable_slots"})
        if not fits:
            stop_reason = "capacity_blocked"
            break
        protected, protected_keys = trial_protected, trial_keys
        additions.append((item, score))
        available = [(other, value) for other, value in available if other.id != item.id]
    plan["protected_baseline_ids"] = [item.id for item, _ in baseline if item.id in protected]
    plan["protected_keys"] = [list(key) for key in sorted(protected_keys)]
    windows = {}
    for item, _ in selected + baseline:
        if item.id in protected or tuple(keys.get(item.id, ())) in protected_keys:
            windows.setdefault(item.id, {"id": item.id, "path": item.path,
                "start_line": item.start_line, "end_line": item.end_line, "content_hash": item.content_hash})
    plan["protected_windows"] = list(windows.values())
    if not additions:
        return finish(baseline, stop_reason, stop_reason)
    exchangeable = [item.id for item, _ in baseline if item.id not in protected]
    removed = set(exchangeable[-len(additions):])
    eligible = [(item, score) for item, score in baseline if item.id in protected]
    eligible += additions
    eligible += [(item, score) for item, score in baseline if item.id not in protected and item.id not in removed]
    for item, _ in additions:
        origins[item.id] = ("retrieval", None)
    plan.update(added_ids=[item.id for item, _ in additions],
                evicted_ids=[item.id for item, _ in baseline if item.id in removed],
                proposed_eligible_ids=[item.id for item, _ in eligible], accepted=True)
    return finish(eligible, termination=stop_reason or "max_additions")


def complementary_context(pairs, **kwargs):
    return complementary_plan(pairs, **kwargs)[:3]


def protected_rows(reference, plan):
    protected, keys = set(plan["protected_baseline_ids"]), plan["canonical_keys"]
    protected_keys = {tuple(key) for key in plan["protected_keys"]}
    return [row for row in reference["results"] if row["id"] in protected
            or row.get("origin") == "dependency"
            or tuple(keys.get(row["id"], ())) in protected_keys
            or any(row["path"] == window["path"]
                   and row["start_line"] <= window["end_line"]
                   and window["start_line"] <= row["end_line"]
                   for window in plan["protected_windows"])]


def delivered_support(response, plan):
    aspects = [set(values) for values in plan["aspect_terms"]]
    scores = {index: aspect_scores(content_terms(f"{row['path']} {row['symbol']}\n{row['source']}"),
                                   aspects, plan["term_df"])
              for index, row in enumerate(response["results"])}
    return best_support(scores, scores, len(aspects))


def protected_covered(current, original):
    if not source._covers(current, original):
        return False
    # A containing retrieval window keeps its own truthful raw scalar. An
    # unchanged window/dependency must keep the original score fields exactly.
    return current.get("id") != original.get("id") or all(
        (name in current) == (name in original)
        and type(current.get(name)) is type(original.get(name)) and current.get(name) == original.get(name)
        for name in ("retrieval_score", "h2o_score", "qwen_score"))


def guard_complementary_plan(reference, proposal, selection_plan):
    """Use actual delivered spans/provenance and aspect support, never full tails."""
    plan = selection_plan
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA or plan.get("mode") != MODE:
        raise ValueError("Complementary guard requires its actual planner receipt")
    if not plan["accepted"]:
        if reference is not proposal:
            raise ValueError("Inactive complementary plan must use its actual self reference")
        receipt = {"schema": "complementary_guard_v2", "mode": MODE,
                   "reference_kind": "self_reference", "protected_ids": [],
                   "source_preserved": True, "aspect_preserved": True,
                   "reference_support": [], "proposal_support": [],
                   "accepted": False, "fallback_reason": "inactive_plan"}
        return deepcopy(reference), receipt
    protected = protected_rows(reference, plan)
    preserved = all(any(protected_covered(current, original) for current in proposal["results"])
                    for original in protected)
    old_support, new_support = delivered_support(reference, plan), delivered_support(proposal, plan)
    aspect_preserved = all(new >= old for old, new in zip(old_support, new_support))
    accepted = preserved and aspect_preserved
    reason = (None if accepted else "protected_source_not_preserved" if not preserved
              else "delivered_aspect_support_decreased")
    receipt = {"schema": "complementary_guard_v2", "mode": MODE,
               "reference_kind": "coverage_factory",
               "protected_ids": [row["id"] for row in protected],
               "source_preserved": preserved, "aspect_preserved": aspect_preserved,
               "reference_support": [fraction(value) for value in old_support],
               "proposal_support": [fraction(value) for value in new_support],
               "accepted": accepted, "fallback_reason": reason}
    return deepcopy(proposal if accepted else reference), receipt


def guard_complementary_context(response, candidates, *, selection_plan, max_results,
                                source_budget, token_budget=None, cancel_check=lambda: None,
                                ranked_candidates=None, definition_keys=None, reference_factory=None):
    """Coalesce once when inactive, otherwise compare one actual packed control."""
    if (not isinstance(selection_plan, dict) or selection_plan.get("schema") != SCHEMA
            or selection_plan.get("mode") != MODE):
        raise ValueError("Complementary postprocessor requires its actual planner receipt")
    if selection_plan["accepted"] and not callable(reference_factory):
        raise ValueError("Admitted complementary plan requires a same-input reference factory")
    cancel_check()
    proposal = coalesce_context(response, candidates, max_results=max_results,
        source_budget=source_budget, token_budget=token_budget, cancel_check=cancel_check)
    cancel_check()
    reference = reference_factory() if selection_plan["accepted"] else proposal
    cancel_check()
    chosen, _ = guard_complementary_plan(reference, proposal, selection_plan)
    cancel_check()
    return chosen
