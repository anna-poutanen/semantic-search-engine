import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from semantic_search.backend import SearchBackend
from semantic_search.server import MAX_BODY_BYTES, create_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        (self.repo / "code.py").write_text("def alpha():\n    return 1\n")
        self.backend = SearchBackend(self.repo, ".")
        self.server = create_server(self.backend, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(self, method, path, data=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else None
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        result = (response.status, json.loads(response.read()), dict(response.getheaders()))
        connection.close()
        return result

    def test_health_status_and_lexical_search_without_loading_models(self):
        status, body, headers = self.request("GET", "/health")
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(headers["Cache-Control"], "no-store")
        result = self.request("POST", "/search", {"query": "alpha", "retrieval": "bm25"})
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]["results"][0]["source"], "def alpha():\n    return 1\n")
        status = self.request("GET", "/status")[1]
        self.assertTrue(status["ready"])
        self.assertFalse(status["embedding_initialized"])
        self.assertFalse(status["dense_ready"])

    def test_host_origin_and_fetch_metadata_are_rejected(self):
        headers = [{"Host": "example.com"}, {"Host": f"evil.local:{self.port}"},
                   {"Host": "localhost:1"}, {"Origin": "http://localhost:3000"},
                   {"Origin": "null"}, {"Referer": "https://example.com"},
                   {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Mode": "navigate"}]
        for values in headers:
            with self.subTest(values=values):
                self.assertEqual(self.request("GET", "/health", headers=values)[0], 403)
        self.assertEqual(self.request("OPTIONS", "/search", headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/health", headers={"Host": f"localhost:{self.port}"})[0], 200)

    def test_malformed_json_options_sizes_and_content_type(self):
        cases = [(b"[]", {}, 400), (b"{", {}, 400), (b"\xff", {}, 400),
                 (b'{"query":"alpha","query":"beta"}', {}, 400),
                 (b'{"query":"\\ud800"}', {}, 400),
                 (b'{"query":"alpha","source_budget":NaN}', {}, 400),
                 (b'{"nested":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}', {}, 400),
                 (b"{}", {}, 400), (b'{"query":"alpha","extra":1}', {}, 400),
                 (b'{"query":"alpha","candidates":true}', {}, 400),
                 (b'{"query":"alpha"}', {"Content-Type": "text/plain"}, 415),
                 (b"x" * (MAX_BODY_BYTES + 1), {}, 413),
                 (b"{}", {"Transfer-Encoding": "chunked"}, 400)]
        for raw, headers, expected in cases:
            with self.subTest(raw=raw[:60], headers=headers):
                status, body, _ = self.request("POST", "/search", raw=raw, headers=headers)
                self.assertEqual(status, expected)
                self.assertIn("error", body)
        self.assertEqual(self.request("POST", "/index", {"unexpected": True})[0], 400)
        self.assertEqual(self.request("GET", "/unknown")[0], 404)

    def test_syntax_error_returns_conflict_and_close_releases_backend(self):
        self.request("POST", "/search", {"query": "alpha", "retrieval": "bm25"})
        (self.repo / "code.py").write_text("def invalid(\n")
        status, body, _ = self.request("POST", "/search", {"query": "alpha", "retrieval": "bm25"})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "source_snapshot")
        self.server.shutdown()
        self.server.server_close()
        self.assertTrue(self.backend.status()["closed"])

    def test_bind_validation_prevents_external_listening(self):
        for host in ("0.0.0.0", "192.168.1.1", "example.com"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                create_server(self.backend, host=host, port=0)
        for port in (-1, 65536, True):
            with self.subTest(port=port), self.assertRaises(ValueError):
                create_server(self.backend, port=port)

    def test_malformed_length_and_status_failures_return_bounded_json(self):
        for length in ("2" * 5000, "\xb2"):
            with self.subTest(length=length[:20]):
                status, body, _ = self.request("POST", "/search", raw=b"{}", headers={"Content-Length": length})
                self.assertIn(status, (411, 413))
                self.assertIn("error", body)
        with patch.object(self.backend, "status", side_effect=RuntimeError("database unavailable")):
            status, body, _ = self.request("GET", "/status")
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "backend_error")


if __name__ == "__main__":
    unittest.main()
