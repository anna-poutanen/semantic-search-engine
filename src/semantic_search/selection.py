"""Source-only context selection, separate from retrieval and budget packing."""
from __future__ import annotations

from collections import ChainMap


def ranked_context(pairs, *, query, max_results, include_dependencies, references,
                   definition_keys=None, ambiguous_symbols=None):
    """Keep ranked symbols and the existing bounded static-callee expansion."""
    seen, selected = set(), []
    for snippet, score in pairs:
        key = (snippet.path, snippet.symbol)
        if key not in seen:
            seen.add(key)
            selected.append((snippet, score))
    eligible = selected[:max_results]
    origins = {snippet.id: ("retrieval", None) for snippet, _ in eligible}
    if include_dependencies and references is not None:
        expanded, included = [], set()
        anchors = eligible[:min(5, max_results)]
        for snippet, score in anchors:
            expanded.append((snippet, score))
            included.add((snippet.path, snippet.symbol))
        for snippet, score in anchors:
            for related in references.dependencies(snippet, limit=2):
                key = (related.path, related.symbol)
                if key not in included and len(expanded) < max_results:
                    expanded.append((related, score))
                    origins[related.id] = ("dependency", snippet.id)
                    included.add(key)
        for snippet, score in eligible:
            key = (snippet.path, snippet.symbol)
            if key not in included and len(expanded) < max_results:
                expanded.append((snippet, score))
                included.add(key)
        eligible = expanded
    return selected, eligible, origins


def _definition_key(snippet, definition_keys):
    key = definition_keys.get(snippet.id) if definition_keys is not None else None
    # Unknown windows must not be silently collapsed by their display labels.
    return ("definition", key) if key is not None else ("window", snippet.id)


def _ambiguous_symbols(definition_keys):
    definitions = {}
    for key in (definition_keys or {}).values():
        definitions.setdefault(key[:2], set()).add(key)
    return {symbol for symbol, keys in definitions.items() if len(keys) > 1}


def _context(pairs, *, query, max_results, include_dependencies, references,
             definition_keys, use_definitions, preserve_rank, ambiguous_symbols):
    key_for = (lambda snippet: _definition_key(snippet, definition_keys)) if use_definitions else (
        lambda snippet: (snippet.path, snippet.symbol))
    seen, selected = set(), []
    for snippet, score in pairs:
        key = key_for(snippet)
        if key not in seen:
            seen.add(key)
            selected.append((snippet, score))
    eligible = selected[:max_results]
    origins = {snippet.id: ("retrieval", None) for snippet, _ in eligible}
    if not include_dependencies or references is None:
        return selected, eligible, origins
    anchors = eligible[:min(5, max_results)]
    expanded = list(eligible if preserve_rank else anchors)
    included = {key_for(snippet) for snippet, _ in expanded}
    # Static graph edges use display labels. Without resolving which of several
    # definitions owns an edge, adding a callee would assert false provenance.
    ambiguous = ((ambiguous_symbols if ambiguous_symbols is not None else
                  _ambiguous_symbols(definition_keys)) if use_definitions else set())
    for snippet, score in anchors:
        if len(expanded) >= max_results:
            break
        if (snippet.path, snippet.symbol) in ambiguous:
            continue
        for related in references.dependencies(snippet, limit=2):
            if (related.path, related.symbol) in ambiguous:
                continue
            key = key_for(related)
            if key not in included and len(expanded) < max_results:
                expanded.append((related, score))
                origins[related.id] = ("dependency", snippet.id)
                included.add(key)
    for snippet, score in eligible:
        key = key_for(snippet)
        if key not in included and len(expanded) < max_results:
            expanded.append((snippet, score))
            included.add(key)
    return selected, expanded, origins


def definition_context(pairs, *, query, max_results, include_dependencies, references,
                       definition_keys=None, ambiguous_symbols=None):
    """Keep one ranked window per actual definition with the existing allocation."""
    return _context(pairs, query=query, max_results=max_results,
                    include_dependencies=include_dependencies, references=references,
                    definition_keys=definition_keys, use_definitions=True, preserve_rank=False,
                    ambiguous_symbols=ambiguous_symbols)


def rank_preserving_context(pairs, *, query, max_results, include_dependencies, references,
                            definition_keys=None, ambiguous_symbols=None):
    """Control: keep the ranked prefix and use genuinely spare slots for callees."""
    return _context(pairs, query=query, max_results=max_results,
                    include_dependencies=include_dependencies, references=references,
                    definition_keys=definition_keys, use_definitions=False, preserve_rank=True,
                    ambiguous_symbols=ambiguous_symbols)


def definition_rank_preserving_context(pairs, *, query, max_results, include_dependencies,
                                       references, definition_keys=None, ambiguous_symbols=None):
    """Combined definition identity and rank-preserving allocation control."""
    return _context(pairs, query=query, max_results=max_results,
                    include_dependencies=include_dependencies, references=references,
                    definition_keys=definition_keys, use_definitions=True, preserve_rank=True,
                    ambiguous_symbols=ambiguous_symbols)


def connected_window_context(pairs, *, query, max_results, include_dependencies, references,
                             definition_keys=None, ambiguous_symbols=None):
    """Research: join one connected companion at each definition's original rank.

    The representative's scalar remains its inherited score. A composite is not
    itself an embedded or reranked document; its assembly receipt names the real
    score anchor. Source size can grow, so ordinary packing may lose later rows.
    """
    from semantic_search.window_assembly import join_windows

    groups = {}
    for rank, (snippet, score) in enumerate(pairs, 1):
        key = _definition_key(snippet, definition_keys)
        groups.setdefault(key, []).append((rank, snippet, score))
    joined_pairs, extra_keys = [], {}
    for key, members in groups.items():
        anchor_rank, anchor, score = members[0]
        representative = anchor
        if key[0] == "definition":
            for companion_rank, companion, _ in members[1:]:
                joined = join_windows(anchor, companion, definition_key=key[1],
                                      anchor_rank=anchor_rank, companion_rank=companion_rank)
                if joined is not None:
                    representative = joined
                    extra_keys[joined.id] = key[1]
                    break
        joined_pairs.append((representative, score))
    keys = ChainMap(extra_keys, definition_keys or {})
    return definition_context(joined_pairs, query=query, max_results=max_results,
        include_dependencies=include_dependencies, references=references,
        definition_keys=keys, ambiguous_symbols=ambiguous_symbols)


def continuation_context(response, candidates, *, ranked_candidates=None,
                         definition_keys=None, reference_factory=None, **limits):
    """Research: retain guarded coverage backfill, then extend retrieved windows."""
    from semantic_search.context_backfill import coalesce_context
    from semantic_search.window_assembly import extend_context

    packed = coalesce_context(response, candidates, **limits)
    return extend_context(packed, (), ranked_candidates=ranked_candidates,
                          definition_keys=definition_keys, **limits)


def _preserves_context(reference, proposal):
    from semantic_search.window_assembly import _preserved
    return _preserved(reference["results"], proposal["results"])


def guarded_connected_context(response, candidates, *, reference_factory,
                              ranked_candidates=None, definition_keys=None, **limits):
    """Accept prepacked unions only when the coverage control loses no source.

    The reference is packed from the same ranked pairs and graph, with no new
    retrieval or model call. Its contents, rather than benchmark labels, decide
    whether a larger union can safely spend this query's remaining capacity.
    """
    from copy import deepcopy
    import hashlib
    from semantic_search.context import compact_result, serialize_context
    from semantic_search.context_backfill import coalesce_context

    if not callable(reference_factory):
        raise ValueError("Guarded connected context requires a source-only reference factory")
    limits.get("cancel_check", lambda: None)()
    proposal = coalesce_context(response, candidates, **limits)
    reference = reference_factory()
    limits.get("cancel_check", lambda: None)()
    accepted = _preserves_context(reference, proposal)
    text = serialize_context(compact_result(reference))
    result = deepcopy(proposal if accepted else reference)
    result["_window_guard"] = {
        "accepted": accepted, "fallback_reason": None if accepted else "coverage_control_source_not_preserved",
        "reference_compact_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "proposed_composites": sum("assembly" in row for row in proposal["results"]),
        "accepted_composites": sum("assembly" in row for row in result["results"]),
    }
    return result


def unchanged_context(response, candidates, **kwargs):
    """The baseline post-packing hook deliberately does not consume candidates."""
    return response


select_context = ranked_context
postprocess_context = unchanged_context
