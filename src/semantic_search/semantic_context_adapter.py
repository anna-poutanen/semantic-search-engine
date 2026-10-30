"""Research-only terminal postprocessor; never installed by the product default.

The feature provider owns query encoding and resident document-vector lookup.
This adapter performs one actual coverage pass, then final-context allocation.
Recording hooks retain references during timing; an experiment must copy/drain
them after the measured request and must include feature work in native timing.
"""
from __future__ import annotations

from copy import deepcopy

from semantic_search.context_backfill import coalesce_context


def make_semantic_postprocessor(feature_provider, *, capture=None):
    """Create an explicitly injected experiment hook, without changing backend.py.

    Provider exceptions propagate: a broken model run must not masquerade as a
    successful no-change search. Invalid *returned* features are handled by the
    pure allocator's exact-reference fallback. Capture callbacks are trusted
    experiment instrumentation and must not mutate the retained objects.
    """
    if not callable(feature_provider) or (capture is not None and not callable(capture)):
        raise TypeError("Semantic experiment provider and capture must be callable")

    def postprocess(response, candidates, *, max_results, source_budget,
                    token_budget=None, cancel_check=lambda: None,
                    ranked_candidates=None, definition_keys=None, **unused):
        from semantic_search.semantic_features import expected_facets
        from semantic_search.semantic_context import semantic_final_context

        cancel_check()
        # coalesce_context copies its input; these are the actual original
        # packed dependency/owner spans, including ones later coalesced away.
        precoalesce_rows = list(response["results"])
        reference = coalesce_context(response, candidates, max_results=max_results,
            source_budget=source_budget, token_budget=token_budget, cancel_check=cancel_check)
        cancel_check()
        raw = list(ranked_candidates) if ranked_candidates is not None else []
        features, feature_calls = None, 0
        if expected_facets(reference["query"]):
            feature_calls += 1
            # A provider cannot rewrite the baseline/raw source that the guard
            # compares. Copy cost is part of an actual native request's timing.
            features = feature_provider(deepcopy(reference), deepcopy(raw), cancel_check=cancel_check)
            cancel_check()
        chosen, receipt = semantic_final_context(reference, raw, features,
            definition_keys=definition_keys, precoalesce_rows=precoalesce_rows,
            max_results=max_results, source_budget=source_budget,
            token_budget=token_budget, cancel_check=cancel_check)
        cancel_check()
        if capture is not None:
            capture({"reference_response": reference, "ranked_candidates": raw,
                "features": features, "definition_keys": definition_keys or {},
                "precoalesce_rows": precoalesce_rows, "response": chosen,
                "semantic_receipt": receipt, "coverage_coalesce_calls": 1,
                "semantic_calls": 1, "feature_calls": feature_calls,
                "limits": {"max_results": max_results, "source_budget": source_budget,
                           "token_budget": token_budget}})
            cancel_check()
        return chosen

    return postprocess
