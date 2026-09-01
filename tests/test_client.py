import json
import os
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from semantic_search.client import MAX_RESPONSE_BYTES, SearchClient
from semantic_search.server import create_server


class FakeBackend:
    closed = False

    def search(self, query, **settings):
        if not query:
            raise ValueError("query required")
        return {"query": query, "settings": settings, "results": []}

    def status(self):
        return {"closed": self.closed, "windows": 3}

    def refresh(self):
        return {"indexed_windows": 1}

    def close(self):
        self.closed = True


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.server = create_server(self.backend, port=0)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.worker.start()
        self.client = SearchClient(f"http://127.0.0.1:{self.server.server_address[1]}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def start_http(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        worker.start()

        def stop():
            server.shutdown()
            server.server_close()
            worker.join(2)

        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_json_roundtrip_preserves_query_and_options(self):
        result = self.client.search("restore café cookie", retrieval="dense", source_budget=100, rerank=False)
        self.assertEqual(result["query"], "restore café cookie")
        self.assertEqual(result["settings"], {"retrieval": "dense", "source_budget": 100, "rerank": False})
        self.assertEqual(self.client.status()["windows"], 3)
        self.assertEqual(self.client.refresh(), {"indexstats": {"indexed_windows": 1}})

    def test_http_failure_includes_actionable_server_error(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 400.*query required"):
            self.client.search("")

    def test_remote_or_ambiguous_service_urls_are_rejected(self):
        for url in ("https://localhost:8750", "http://example.com", "http://127.0.0.1/path",
                    "http://user:secret@localhost:8750", "http://localhost:8750?q=x",
                    "http://localhost:99999", "http://localhost:8750#fragment",
                    "http://localhost:0", "http://127.0.0.1:0", "http://localhost:",
                    "http://@localhost:8750"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                SearchClient(url)

    def test_forced_proxy_is_bypassed_for_status_query_and_index_requests(self):
        calls = []

        class Proxy(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                raw = b'{"closed":false,"windows":999}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        proxy_url = self.start_http(Proxy)
        with patch.dict(os.environ, {"http_proxy": proxy_url, "HTTP_PROXY": proxy_url,
                                     "https_proxy": proxy_url, "HTTPS_PROXY": proxy_url,
                                     "no_proxy": "", "NO_PROXY": ""}):
            # A fresh default opener proves this environment really routes via the proxy.
            ordinary = urllib.request.build_opener(urllib.request.ProxyHandler())
            with ordinary.open(self.client.url + "/status", timeout=5) as response:
                self.assertEqual(json.load(response)["windows"], 999)
            self.assertEqual(len(calls), 1)
            calls.clear()
            client = SearchClient(self.client.url, timeout=5)
            self.assertEqual(client.status()["windows"], 3)
            self.assertEqual(client.search("private implementation query")["query"],
                             "private implementation query")
            self.assertEqual(client.refresh()["indexstats"]["indexed_windows"], 1)
            self.assertEqual(calls, [])

    def test_real_redirects_never_reach_another_loopback_listener(self):
        received = []

        class Destination(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.path)
                raw = b'{"closed":false,"windows":999}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        destination = self.start_http(Destination)

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", destination + "/forwarded")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(303)
                self.send_header("Location", destination + "/forwarded-query")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        origin = self.start_http(Redirect)
        ordinary = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with ordinary.open(origin + "/status", timeout=5) as response:
            self.assertEqual(json.load(response)["windows"], 999)
        self.assertEqual(received, ["/forwarded"])
        received.clear()
        client = SearchClient(origin, timeout=5)
        with self.assertRaisesRegex(RuntimeError, "redirects are not allowed.*302"):
            client.status()
        with self.assertRaisesRegex(RuntimeError, "redirects are not allowed.*303"):
            client.search("private implementation query")
        self.assertEqual(received, [])

    def test_malformed_endpoint_responses_fail_with_useful_errors(self):
        body = {"raw": b"{}", "content_type": "application/json"}

        class Response(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", body["content_type"])
                self.send_header("Content-Length", str(len(body["raw"])))
                self.end_headers()
                self.wfile.write(body["raw"])

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.do_GET()

            def log_message(self, *args):
                pass

        client = SearchClient(self.start_http(Response), timeout=5)
        valid_row = {"path": "src/code.py", "symbol": "run", "source": "return 1\n",
                     "start_line": 1, "end_line": 1, "h2o_score": 2.5, "truncated": False}
        cases = [(client.status, b"[]", "unexpected response"),
                 (client.status, b'{"closed":"false","windows":3}', "status response"),
                 (client.status, b'{"closed":false,"windows":true}', "status response"),
                 (client.refresh, b'{"indexstats":[]}', "index response"),
                 (client.refresh, b'{"indexstats":{"indexed_windows":true}}', "index response"),
                 (client.status, b'{"closed":false,"windows":3,"bad":NaN}', "invalid JSON"),
                 (client.status, b'{"closed":false,"windows":3,"bad":1e400}', "invalid JSON"),
                 (client.status, b'{"closed":false,"closed":false,"windows":3}', "invalid JSON"),
                 (client.status, b"\xff", "invalid JSON")]
        for method, raw, message in cases:
            body["raw"] = raw
            with self.subTest(raw=raw), self.assertRaisesRegex(RuntimeError, message):
                method()
        for changed in ({"path": []}, {"source": None}, {"symbol": 1},
                        {"start_line": True}, {"start_line": 0}, {"end_line": 0},
                        {"h2o_score": "2.5"}, {"h2o_score": False}, {"h2o_score": 10 ** 400},
                        {"truncated": "false"}):
            body["raw"] = json.dumps({"query": "query", "results": [{**valid_row, **changed}]}).encode()
            with self.subTest(changed=changed), self.assertRaisesRegex(RuntimeError, "search response"):
                client.search("query")
        body["raw"] = json.dumps({"query": "different query", "results": []}).encode()
        with self.assertRaisesRegex(RuntimeError, "search response"):
            client.search("query")
        body["raw"] = json.dumps({"query": "query", "results": [valid_row]}).encode()
        self.assertEqual(client.search("query")["results"], [valid_row])
        body["content_type"] = "text/html"
        with self.assertRaisesRegex(RuntimeError, "application/json"):
            client.search("query")
        body["content_type"] = "application/json"
        body["raw"] = b" " * (MAX_RESPONSE_BYTES + 1)
        with self.assertRaisesRegex(RuntimeError, "1 MiB limit"):
            client.status()


if __name__ == "__main__":
    unittest.main()
