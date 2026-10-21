"""Source-only context selection, separate from retrieval and budget packing."""
from __future__ import annotations


def ranked_context(pairs, *, query, max_results, include_dependencies, references,
                   definition_keys=None):
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
             definition_keys, use_definitions, preserve_rank):
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
    ambiguous = _ambiguous_symbols(definition_keys) if use_definitions else set()
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
                       definition_keys=None):
    """Keep one ranked window per actual definition with the existing allocation."""
    return _context(pairs, query=query, max_results=max_results,
                    include_dependencies=include_dependencies, references=references,
                    definition_keys=definition_keys, use_definitions=True, preserve_rank=False)


def rank_preserving_context(pairs, *, query, max_results, include_dependencies, references,
                            definition_keys=None):
    """Control: keep the ranked prefix and use genuinely spare slots for callees."""
    return _context(pairs, query=query, max_results=max_results,
                    include_dependencies=include_dependencies, references=references,
                    definition_keys=definition_keys, use_definitions=False, preserve_rank=True)


def definition_rank_preserving_context(pairs, *, query, max_results, include_dependencies,
                                       references, definition_keys=None):
    """Combined definition identity and rank-preserving allocation control."""
    return _context(pairs, query=query, max_results=max_results,
                    include_dependencies=include_dependencies, references=references,
                    definition_keys=definition_keys, use_definitions=True, preserve_rank=True)


def unchanged_context(response, candidates, **kwargs):
    """The baseline post-packing hook deliberately does not consume candidates."""
    return response


select_context = ranked_context
postprocess_context = unchanged_context
