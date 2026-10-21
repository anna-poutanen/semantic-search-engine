"""Actual composite rows remain valid in the strict compact MCP envelope."""
import json
from pathlib import Path

import pytest

from semantic_search.context import compact_result, count_context, indexed_scope


@pytest.mark.parametrize("kind", ["full", "truncated"])
def test_composite_fixture_passes_strict_mcp_without_exposing_internal_receipt(kind):
    pytest.importorskip("mcp")
    from semantic_search.mcp_server import SearchArguments, _validate_context
    fixture = json.loads((Path(__file__).parent / "fixtures/window_union.json").read_text())
    response = {"query": "Locate the implementation", "match_status": "candidates",
        "scope": indexed_scope("src"), "results": [fixture[kind]],
        "budget_exhausted": kind == "truncated",
        "token_usage": {"encoding": "cl100k_base", "budget": 4096, "tokens": 0,
                        "truncated": kind == "truncated"}}
    count_context(response)
    envelope = compact_result(response)
    text = _validate_context(envelope, SearchArguments(query=response["query"],
        max_results=8, source_budget=12000, token_budget=4096))
    assert json.loads(text) == envelope
    assert "assembly" not in envelope["results"][0]
    assert "context:connected_windows" not in text
