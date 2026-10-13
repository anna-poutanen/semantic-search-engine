"""Exercise the optional adapter using the official client and a real subprocess."""
import asyncio
import hashlib
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from mcp import Client, StdioServerParameters
except ImportError:
    Client = None


@unittest.skipIf(Client is None, "Install the optional mcp extra for protocol integration tests")
class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []
        self.cancelled = threading.Event()
        self.started = threading.Event()
        self.release = threading.Event()
        self.request_id = None
        self.proxy_calls = []
        self.malformed = None
        self.source_override = None
        case = self

        class SearchService(BaseHTTPRequestHandler):
            def do_POST(self):
                settings = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                case.calls.append((self.path, settings))
                if self.path == "/cancel":
                    case.request_id = settings["request_id"]
                    case.cancelled.set()
                    case.release.set()
                    return self.respond({"cancelled": True})
                if self.path != "/search":
                    return self.respond({"error": "unknown endpoint"}, 404)
                if settings["query"] == "fail":
                    return self.respond({"error": "source changed; retry search"}, 503)
                if settings["query"] == "wait":
                    case.started.set()
                    case.release.wait(8)
                source = case.source_override or "def restore_cookie():\n    return 'café'\n"
                digest = hashlib.sha256(source.encode()).hexdigest()
                result = {
                    "query": settings["query"], "match_status": "candidates",
                    "results": [{"path": "src/cookies.py", "symbol": "restore_cookie",
                                 "start_line": 1, "end_line": 2, "source": source,
                                 "source_hash": digest, "content_hash": digest,
                                 "truncated": False, "origin": "retrieval", "related_to": None}],
                    "budget_exhausted": False,
                    "token_usage": {"encoding": "cl100k_base", "tokens": 0,
                                    "budget": settings["token_budget"], "truncated": False},
                    "timings": {"total_s": .001}, "indexstats": {"indexed_windows": 0},
                }
                from semantic_search.context import count_context
                count_context(result)
                if case.malformed:
                    result.update(case.malformed.get("root", {}))
                    result["results"][0].update(case.malformed.get("row", {}))
                    result["token_usage"].update(case.malformed.get("usage", {}))
                return self.respond(result)

            def respond(self, value, status=200):
                raw = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        class Proxy(BaseHTTPRequestHandler):
            def do_POST(self):
                case.proxy_calls.append(self.path)
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server_url = self.start_server(SearchService)
        self.proxy_url = self.start_server(Proxy)

    def tearDown(self):
        self.release.set()

    def start_server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(2)

        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def parameters(self, server_url=None):
        # Inject a real proxy with NO_PROXY empty, so bypassing it is meaningful.
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "semantic_search.mcp_server", "--server", server_url or self.server_url],
            cwd=Path(__file__).resolve().parents[1],
            env={"HTTP_PROXY": self.proxy_url, "http_proxy": self.proxy_url,
                 "HTTPS_PROXY": self.proxy_url, "https_proxy": self.proxy_url,
                 "NO_PROXY": "", "no_proxy": ""},
        )

    async def test_initialize_list_and_call_with_legacy_and_current_protocol(self):
        for mode in ("legacy", "auto"):
            with self.subTest(mode=mode):
                async with Client(self.parameters(), mode=mode, read_timeout_seconds=10) as client:
                    tools = (await client.list_tools()).tools
                    self.assertEqual([tool.name for tool in tools], ["search_code"])
                    schema = tools[0].input_schema
                    self.assertFalse(schema["additionalProperties"])
                    self.assertEqual(schema["required"], ["query"])
                    self.assertEqual(schema["properties"]["token_budget"]["default"], 4096)
                    self.assertIsNotNone(tools[0].output_schema)
                    self.assertTrue(tools[0].annotations.read_only_hint)
                    result = await client.call_tool("search_code", {"query": "restore café cookies"})
                    self.assertFalse(result.is_error)
                    envelope = result.structured_content
                    self.assertEqual(json.loads(result.content[0].text), envelope)
                    self.assertEqual(envelope["results"][0]["source"],
                                     "def restore_cookie():\n    return 'café'\n")
                    self.assertEqual(envelope["results"][0]["end_line"], 2)
                    self.assertNotIn("timings", envelope)
                    from semantic_search.context import encoding, serialize_context
                    self.assertEqual(result.content[0].text, serialize_context(envelope))
                    self.assertEqual(envelope["token_usage"]["tokens"],
                                     len(encoding().encode(result.content[0].text, disallowed_special=())))
                    self.assertLessEqual(envelope["token_usage"]["tokens"], 4096)
                    self.assertEqual(envelope["results"][0]["source_hash"],
                                     hashlib.sha256(envelope["results"][0]["source"].encode()).hexdigest())
                    settings = self.calls[-1][1]
                    self.assertEqual(settings["token_budget"], 4096)
                    self.assertEqual(settings["retrieval"], "dense")
                    self.assertEqual(settings["max_results"], 8)
                    self.assertFalse(settings["rerank"])
                    self.assertTrue(settings["include_dependencies"])
                    self.assertEqual(len(settings["request_id"]), 32)
        self.assertEqual(self.proxy_calls, [])

    async def test_all_settings_are_forwarded_without_model_imports(self):
        args = {"query": "decode transport error", "retrieval": "hybrid", "rerank": True,
                "candidates": 3, "max_results": 2, "source_budget": 900,
                "token_budget": 1024, "include_dependencies": False}
        async with Client(self.parameters(), mode="legacy", read_timeout_seconds=10) as client:
            result = await client.call_tool("search_code", args)
            self.assertFalse(result.is_error)
        self.assertEqual({key: value for key, value in self.calls[-1][1].items()
                          if key != "request_id"}, args)
        # The adapter module must not import backend/model runtimes even on import.
        code = ("import sys; import semantic_search.mcp_server; "
                "assert not any(m in sys.modules for m in "
                "('semantic_search.backend','semantic_search.embeddings','mlx','mlx_embeddings'))")
        subprocess.run([sys.executable, "-c", code], check=True, timeout=10,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    async def test_unicode_paragraph_separators_inside_source_preserve_physical_bounds(self):
        self.source_override = "def restore_cookie():\n    return 'café\u2028paragraph\u2029separator'\n"
        async with Client(self.parameters(), mode="legacy", read_timeout_seconds=10) as client:
            result = await client.call_tool("search_code", {"query": "restore"})
            self.assertFalse(result.is_error)
            row = result.structured_content["results"][0]
            self.assertEqual(row["source"], self.source_override)
            self.assertEqual((row["start_line"], row["end_line"]), (1, 2))

    async def test_invalid_arguments_and_unknown_tool_are_error_results(self):
        from semantic_search.mcp_server import SearchArguments
        with self.assertRaisesRegex(ValueError, "(?i)unicode"):
            SearchArguments.model_validate({"query": "\ud800"})
        cases = [{}, {"query": " "}, {"query": 1}, {"query": "x", "extra": 1},
                 {"query": "x", "rerank": "false"}, {"query": "x", "rerank": 1},
                 {"query": "x", "candidates": True}, {"query": "x", "candidates": "4"},
                 {"query": "x", "candidates": 0}, {"query": "x", "candidates": 51},
                 {"query": "x", "max_results": 21}, {"query": "x", "retrieval": "unknown"},
                 {"query": "x", "source_budget": -1}, {"query": "x", "token_budget": 511},
                 {"query": "x", "token_budget": 32001}, {"query": "x", "token_budget": None},
                 {"query": "x", "include_dependencies": "true"}]
        async with Client(self.parameters(), mode="legacy", read_timeout_seconds=10) as client:
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    result = await client.call_tool("search_code", arguments)
                    self.assertTrue(result.is_error)
                    self.assertIn("Invalid search_code arguments", result.content[0].text)
                    self.assertLessEqual(len(result.content[0].text), 1024)
            result = await client.call_tool("missing", {"query": "x"})
            self.assertTrue(result.is_error)
        self.assertEqual(self.calls, [])

    async def test_service_error_is_actionable_and_adapter_remains_usable(self):
        async with Client(self.parameters(), mode="legacy", read_timeout_seconds=10) as client:
            result = await client.call_tool("search_code", {"query": "fail"})
            self.assertTrue(result.is_error)
            self.assertIn("HTTP 503", result.content[0].text)
            self.assertIn("source changed", result.content[0].text)
            self.assertFalse((await client.call_tool("search_code", {"query": "retry"})).is_error)

    async def test_inconsistent_context_hash_locations_or_token_usage_is_rejected(self):
        cases = [{"row": {"source_hash": "0" * 64}}, {"row": {"end_line": 3}},
                 {"usage": {"tokens": 0}}, {"usage": {"budget": 1024}},
                 {"root": {"match_status": "no_candidates"}}]
        async with Client(self.parameters(), mode="legacy", read_timeout_seconds=10) as client:
            for changed in cases:
                self.malformed = changed
                with self.subTest(changed=changed):
                    result = await client.call_tool("search_code", {"query": "source"})
                    self.assertTrue(result.is_error)
                    self.assertIn("invalid context", result.content[0].text)

    async def test_protocol_cancellation_notifies_same_http_request_and_next_call_works(self):
        async with Client(self.parameters(), mode="legacy", read_timeout_seconds=10) as client:
            task = asyncio.create_task(client.call_tool("search_code", {"query": "wait"}))
            self.assertTrue(await asyncio.to_thread(self.started.wait, 5))
            search_id = self.calls[-1][1]["request_id"]
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(await asyncio.to_thread(self.cancelled.wait, 5))
            self.assertEqual(self.request_id, search_id)
            result = await client.call_tool("search_code", {"query": "after cancellation"})
            self.assertFalse(result.is_error)
        self.assertEqual(self.proxy_calls, [])

    async def test_protocol_cancellation_stops_following_h2o_scores_in_actual_backend(self):
        from semantic_search.backend import SearchBackend
        from semantic_search.server import create_server

        scored, started, released, cancelled = [], threading.Event(), threading.Event(), threading.Event()

        class SlowH2O:
            def score(self, query, snippet):
                scored.append(snippet.id)
                started.set()
                released.wait(5)
                return SimpleNamespace(score=3.)

        class ObservedBackend(SearchBackend):
            def cancel(self, request_id):
                response = super().cancel(request_id)
                cancelled.set()
                return response

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "code.py").write_text("".join(f"def implementation_{i}():\n    return {i}\n\n"
                                                 for i in range(4)))
            backend = ObservedBackend(repo, ".", h2o_client=SlowH2O())
            server = create_server(backend, port=0)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
            worker.start()
            url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                async with Client(self.parameters(url), mode="legacy", read_timeout_seconds=10) as client:
                    task = asyncio.create_task(client.call_tool("search_code", {
                        "query": "implementation", "retrieval": "bm25", "rerank": True,
                        "candidates": 3, "include_dependencies": False,
                    }))
                    self.assertTrue(await asyncio.to_thread(started.wait, 5))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertTrue(await asyncio.to_thread(cancelled.wait, 5))
                    released.set()
                    result = await client.call_tool("search_code", {
                        "query": "implementation", "retrieval": "bm25", "rerank": False,
                    })
                    self.assertFalse(result.is_error)
                    self.assertEqual(len(scored), 1)
                    self.assertFalse(backend.status()["embedding_initialized"])
            finally:
                released.set()
                server.shutdown()
                server.server_close()
                worker.join(2)

    async def test_empty_actual_backend_returns_no_candidates_context(self):
        from semantic_search.backend import SearchBackend
        from semantic_search.server import create_server
        with tempfile.TemporaryDirectory() as directory:
            backend = SearchBackend(directory, ".")
            server = create_server(backend, port=0)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
            worker.start()
            url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                async with Client(self.parameters(url), mode="legacy", read_timeout_seconds=10) as client:
                    result = await client.call_tool("search_code", {"query": "implementation",
                                                                  "retrieval": "bm25"})
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content["results"], [])
                    self.assertEqual(result.structured_content["match_status"], "no_candidates")
            finally:
                server.shutdown()
                server.server_close()
                worker.join(2)

    async def test_raw_stdio_contains_only_protocol_json(self):
        def exchange():
            process = subprocess.Popen(
                [sys.executable, "-m", "semantic_search.mcp_server", "--server", self.server_url],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
            )
            def read_line():
                if not select.select([process.stdout], [], [], 5)[0]:
                    raise TimeoutError("MCP subprocess did not produce a protocol response")
                return process.stdout.readline()

            try:
                requests = [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                "clientInfo": {"name": "wire-test", "version": "1"}}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                ]
                process.stdin.write(json.dumps(requests[0]) + "\n")
                process.stdin.flush()
                first = read_line()
                process.stdin.write("\n".join(json.dumps(row) for row in requests[1:]) + "\n")
                process.stdin.flush()
                second = read_line()
                process.stdin.close()
                process.wait(timeout=5)
                remaining = process.stdout.read()
                return first, second, remaining, process.stderr.read(), process.returncode
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()

        first, second, remaining, stderr, code = await asyncio.wait_for(asyncio.to_thread(exchange), 15)
        self.assertEqual(json.loads(first)["id"], 1)
        self.assertEqual(json.loads(second)["id"], 2)
        self.assertEqual(remaining, "")
        self.assertEqual(code, 0, stderr)


if __name__ == "__main__":
    unittest.main()
