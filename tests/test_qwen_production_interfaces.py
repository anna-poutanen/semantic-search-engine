"""CLI and real stdio MCP default/override checks, with an injected CPU backend."""
import contextlib
import io
from pathlib import Path
import sys
import threading
from unittest.mock import Mock, patch
import unittest

import pytest

from semantic_search import cli
from semantic_search.context import apply_token_budget, indexed_scope
from semantic_search.mcp_server import resolve_search_arguments, search_defaults_record
from semantic_search.server import create_server


def test_strict_mcp_launch_defaults_are_overridden_only_by_explicit_call_fields():
    defaults = search_defaults_record(dict(retrieval="hybrid", rerank=True, rerank_provider="qwen", candidates=24))
    first = resolve_search_arguments({"query": "behavior"}, defaults)
    assert first.retrieval == "hybrid" and first.rerank and first.rerank_provider == "qwen"
    assert first.candidates == 24
    changed = resolve_search_arguments({"query": "behavior", "rerank": False,
        "retrieval": "dense", "candidates": 2, "rerank_provider": "h2o"}, defaults)
    assert changed.retrieval == "dense" and not changed.rerank and changed.candidates == 2
    assert defaults == dict(retrieval="hybrid", rerank=True, rerank_provider="qwen", candidates=24)
    for invalid in ({"query": "x", "candidates": True}, {"query": "x", "rerank": "false"},
                    {"query": "x", "rerank_provider": "remote"}, []):
        with pytest.raises(ValueError):
            resolve_search_arguments(invalid, defaults)
    for invalid in ({"query": "not a launch option"}, {"candidates": 0}, {"rerank": "yes"}):
        with pytest.raises(ValueError):
            search_defaults_record(invalid)


def test_cli_qwen_provider_is_explicit_and_does_not_start_worker_for_server_calls():
    client = Mock()
    client.search.return_value = {"results": []}
    with patch("semantic_search.client.SearchClient", return_value=client), \
         patch.object(cli, "start_qwen_owner") as owner, contextlib.redirect_stdout(io.StringIO()):
        cli.main(["search", "behavior", "--server", "http://localhost:8750", "--rerank",
                  "--rerank-provider", "qwen", "--retrieval", "hybrid", "--candidates", "24"])
    owner.assert_not_called()
    assert client.search.call_args.kwargs["rerank_provider"] == "qwen"
    assert client.search.call_args.kwargs["candidates"] == 24


def test_cli_standalone_owns_and_closes_qwen_even_if_backend_construction_fails():
    owner = Mock()
    with patch.object(cli, "start_qwen_owner", return_value=owner), \
         patch("semantic_search.backend.SearchBackend", side_effect=ValueError("invalid repository")):
        with pytest.raises(ValueError):
            cli.main(["search", "behavior", "--repo", "missing", "--rerank", "--rerank-provider", "qwen"])
    owner.close.assert_called_once()


def test_cli_launch_defaults_are_forwarded_explicitly_to_mcp():
    with patch("semantic_search.mcp_server.run_mcp") as run:
        cli.main(["mcp", "--default-retrieval", "hybrid", "--default-rerank",
                  "--default-rerank-provider", "qwen", "--default-candidates", "24"])
    assert run.call_args.kwargs["search_defaults"] == dict(
        retrieval="hybrid", rerank=True, rerank_provider="qwen", candidates=24)


class InjectedBackend:
    def __init__(self):
        self.calls = []
    def search(self, query, **settings):
        self.calls.append(settings)
        result = {"query": query, "match_status": "no_candidates", "results": [],
                  "scope": indexed_scope("src"), "budget_exhausted": False}
        return apply_token_budget(result, settings["token_budget"])
    def close(self):
        pass


class MCPLaunchDefaultsTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_protocol_schema_defaults_and_explicit_overrides_match_actual_http_payload(self):
        from mcp import Client, StdioServerParameters
        backend = InjectedBackend()
        server = create_server(backend, port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()
        package_root = str(Path(cli.__file__).resolve().parents[1])
        parameters = StdioServerParameters(command=sys.executable,
            args=["-B", "-m", "semantic_search.mcp_server", "--server",
                f"http://127.0.0.1:{server.server_port}", "--default-retrieval", "hybrid",
                "--default-rerank", "--default-rerank-provider", "qwen", "--default-candidates", "24"],
            env={"PYTHONPATH": package_root, "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            async with Client(parameters, mode="legacy", read_timeout_seconds=10) as client:
                tool = (await client.list_tools()).tools[0]
                properties = tool.input_schema["properties"]
                self.assertEqual(properties["retrieval"]["default"], "hybrid")
                self.assertEqual(properties["rerank"]["default"], True)
                self.assertEqual(properties["rerank_provider"]["default"], "qwen")
                self.assertEqual(properties["candidates"]["default"], 24)
                self.assertIn("explicit call arguments override", tool.description)
                first = await client.call_tool("search_code", {"query": "behavior"})
                self.assertFalse(first.is_error)
                self.assertEqual(backend.calls[-1]["rerank_provider"], "qwen")
                self.assertTrue(backend.calls[-1]["rerank"])
                self.assertEqual(backend.calls[-1]["candidates"], 24)
                changed = await client.call_tool("search_code", {"query": "behavior", "rerank": False,
                    "retrieval": "dense", "candidates": 2, "rerank_provider": "h2o"})
                self.assertFalse(changed.is_error)
                self.assertFalse(backend.calls[-1]["rerank"])
                self.assertEqual(backend.calls[-1]["retrieval"], "dense")
                self.assertEqual(backend.calls[-1]["rerank_provider"], "h2o")
                self.assertEqual(backend.calls[-1]["candidates"], 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
