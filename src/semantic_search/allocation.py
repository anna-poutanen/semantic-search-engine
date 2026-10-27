"""Research-only allocation of complementary definitions from one ranked pool.

These selectors do not retrieve, rescore or rewrite source. Literal query-term
preservation is a conservative heuristic, not a guarantee of behavior coverage.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction

from semantic_search.retrieval import _CLAUSE_STOP_WORDS, multifile_clauses, tokenize
from semantic_search.selection import _definition_key, definition_context


MODES = {"aspect_fill": 5, "aspect_balanced": 3}


def _content_terms(text):
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


def _features(snippet, query_terms):
    return _content_terms(f"{snippet.path} {snippet.symbol}\n{snippet.text}") & query_terms


def _fraction(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def allocation_plan(pairs, *, mode, query, max_results, include_dependencies,
                    references, definition_keys=None, ambiguous_symbols=None):
    """Return ``selected, eligible, origins, receipt`` without reading gold.

    The full canonical selected list stays in its original order. The receipt is
    deliberately separate from response metadata so experimental traces can bind
    the actual planning decision without changing a public client schema.
    """
    if mode not in MODES:
        raise ValueError("Unknown complementary allocation mode")
    pairs = list(pairs)
    selected, baseline, origins = definition_context(
        pairs, query=query, max_results=max_results,
        include_dependencies=include_dependencies, references=references,
        definition_keys=definition_keys, ambiguous_symbols=ambiguous_symbols)
    clauses = multifile_clauses(query)
    receipt = {
        "schema": "aspect_allocation_v1", "mode": mode,
        "activated": bool(clauses), "clauses": list(clauses),
        "query_terms": [], "raw_ranks": {}, "candidate_terms": {}, "term_df": {},
        "baseline_eligible_ids": [item.id for item, _ in baseline],
        "protected_anchor_ids": [], "protected_dependency_ids": [],
        "protected_owner_ids": [], "protected_ids": [], "baseline_query_terms": [],
        "picks": [], "proposed_eligible_ids": [], "proposed_query_terms": [],
        "accepted": False, "fallback_reason": None,
    }

    def fallback(reason):
        receipt["fallback_reason"] = reason
        return selected, baseline, origins, receipt

    if not clauses:
        return fallback("no_literal_clauses")

    query_terms = set().union(*(_content_terms(clause) for clause in clauses))
    raw_ranks, features, by_definition = {}, {}, {}
    for rank, (item, _) in enumerate(pairs, 1):
        raw_ranks.setdefault(item.id, rank)
        features[item.id] = _features(item, query_terms)
        by_definition.setdefault(_definition_key(item, definition_keys), set()).update(features[item.id])
    frequencies = Counter(term for terms in by_definition.values() for term in terms)
    receipt.update(query_terms=sorted(query_terms), raw_ranks=raw_ranks,
                   candidate_terms={key: sorted(value) for key, value in features.items()},
                   term_df={term: frequencies[term] for term in sorted(query_terms)})

    anchor_ids = [item.id for item, _ in selected[:min(MODES[mode], max_results)]]
    dependency_ids, owner_ids = [], []
    for item, _ in baseline:
        origin, owner = origins.get(item.id, ("retrieval", None))
        if origin == "dependency":
            dependency_ids.append(item.id)
            if owner not in owner_ids:
                owner_ids.append(owner)
    protected = set(anchor_ids) | set(dependency_ids) | set(owner_ids)
    proposal = [(item, score) for item, score in baseline if item.id in protected]
    covered = set().union(*(_features(item, query_terms) for item, _ in proposal))
    baseline_terms = set().union(*(_features(item, query_terms) for item, _ in baseline))
    receipt.update(protected_anchor_ids=anchor_ids, protected_dependency_ids=dependency_ids,
                   protected_owner_ids=owner_ids, protected_ids=[item.id for item, _ in proposal],
                   baseline_query_terms=sorted(baseline_terms))
    if len(proposal) >= len(baseline):
        return fallback("no_free_slots")

    included = {_definition_key(item, definition_keys) for item, _ in proposal}
    available = [(item, score) for item, score in selected
                 if _definition_key(item, definition_keys) not in included]
    positive_picks = 0
    while len(proposal) < len(baseline) and available:
        choices = []
        for position, (item, score) in enumerate(available):
            novel = features[item.id] - covered
            gain = sum((Fraction(1, frequencies[term]) for term in novel), Fraction())
            utility = gain / raw_ranks[item.id]
            choices.append((utility, -raw_ranks[item.id], position, gain, novel))
        utility, _, position, gain, novel = max(choices, key=lambda value: value[:2])
        reason = "novel_query_terms" if utility > 0 else "rank_fill"
        if utility > 0:
            positive_picks += 1
        else:
            position = 0
            item = available[position][0]
            novel, gain, utility = set(), Fraction(), Fraction()
        item, score = available.pop(position)
        proposal.append((item, score))
        covered.update(features[item.id])
        receipt["picks"].append({
            "id": item.id, "rank": raw_ranks[item.id], "novel_terms": sorted(novel),
            "weighted_gain": _fraction(gain), "utility": _fraction(utility), "reason": reason,
        })
    receipt.update(proposed_eligible_ids=[item.id for item, _ in proposal],
                   proposed_query_terms=sorted(covered))
    if len(proposal) != len(baseline):
        return fallback("candidate_pool_exhausted")
    if not positive_picks:
        return fallback("no_novel_query_terms")
    if not baseline_terms <= covered:
        return fallback("query_terms_not_preserved")
    planned_origins = dict(origins)
    for item, _ in proposal:
        if item.id not in dependency_ids:
            planned_origins[item.id] = ("retrieval", None)
    receipt["accepted"] = True
    return selected, proposal, planned_origins, receipt


def aspect_fill_context(pairs, **kwargs):
    """Research: retain five anchors and their callees; vary only spare slots."""
    return allocation_plan(pairs, mode="aspect_fill", **kwargs)[:3]


def aspect_balanced_context(pairs, **kwargs):
    """Research: retain three anchors, all original callees and their owners."""
    return allocation_plan(pairs, mode="aspect_balanced", **kwargs)[:3]
