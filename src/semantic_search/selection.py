"""Source-only context selection, separate from retrieval and budget packing."""
from __future__ import annotations


def ranked_context(pairs, *, query, max_results, include_dependencies, references):
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


select_context = ranked_context
