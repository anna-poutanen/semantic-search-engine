import copy
import hashlib
import json
import os
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from semantic_search.corpus import Snippet
from semantic_search.model import H2OClient, INSTRUCTIONS, RUBRIC


VALID_RESPONSE = {
    "answers": {
        "relevance": {
            "score": 2.4,
            "probabilities": {"0": 0.1, "1": 0.1, "2": 0.1, "3": 0.7},
        }
    },
    "usage": {"input_tokens": 37},
}


class DecisionHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self._respond()

    def do_GET(self):
        # A followed 301/302/303 redirect can turn a POST into a GET.
        self._respond()

    def _respond(self):
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size)
        self.server.requests.append({
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers),
            "body": raw,
        })
        self.send_response(self.server.response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.server.response_body)))
        for key, value in self.server.response_headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(self.server.response_body)

    def log_message(self, *args):
        pass


@contextmanager
def decision_server():
    server = HTTPServer(("127.0.0.1", 0), DecisionHandler)
    server.requests = []
    server.response_status = 200
    server.response_headers = {}
    server.response_body = json.dumps(VALID_RESPONSE).encode("utf-8")
    worker = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def server_url(server):
    return f"http://127.0.0.1:{server.server_address[1]}"


class H2OClientTests(unittest.TestCase):
    def setUp(self):
        self.server = self.enterContext(decision_server())
        self.client = H2OClient(server_url(self.server), timeout=3)
        source = "def restore_cookie(value):\n    return value.strip()\n"
        self.snippet = Snippet(
            id="example.py:restore_cookie:4:5",
            path="src/example.py",
            symbol="restore_cookie",
            start_line=4,
            end_line=5,
            text=source,
            content_hash=hashlib.sha256(source.encode()).hexdigest(),
        )

    def test_decision_api_roundtrip_preserves_source_and_judgment(self):
        query = "restore a café cookie"
        judgment = self.client.score(query, self.snippet)

        self.assertEqual(len(self.server.requests), 1)
        request = self.server.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/v1/systemone")
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertEqual(json.loads(request["body"]), {
            "state": {
                "path": self.snippet.path,
                "symbol": self.snippet.symbol,
                "start_line": 4,
                "end_line": 5,
                "source": self.snippet.text,
            },
            "questions": {
                "relevance": {
                    "type": "score",
                    "instructions": INSTRUCTIONS.format(query=query),
                    "criteria": RUBRIC,
                }
            },
        })
        self.assertIsInstance(judgment.score, float)
        self.assertEqual(judgment.score, 2.4)
        self.assertEqual(judgment.probabilities,
                         VALID_RESPONSE["answers"]["relevance"]["probabilities"])
        self.assertEqual(judgment.input_tokens, 37)
        self.assertEqual(judgment.response, VALID_RESPONSE)
        self.assertGreaterEqual(judgment.elapsed_s, 0)

    def test_missing_usage_defaults_input_tokens_to_zero(self):
        result = copy.deepcopy(VALID_RESPONSE)
        del result["usage"]
        self.server.response_body = json.dumps(result).encode()
        self.assertEqual(self.client.score("restore a cookie", self.snippet).input_tokens, 0)

    def test_proxy_configuration_cannot_forward_loopback_source(self):
        proxy = self.enterContext(decision_server())
        proxy_url = server_url(proxy)
        environment = {
            "http_proxy": proxy_url,
            "HTTP_PROXY": proxy_url,
            "all_proxy": proxy_url,
            "ALL_PROXY": proxy_url,
            "no_proxy": "",
            "NO_PROXY": "",
        }
        # Force bypass off so platform settings cannot accidentally make the
        # default urllib transport pass this test by ignoring a local address.
        with patch.dict(os.environ, environment, clear=True), \
                patch("urllib.request.proxy_bypass", return_value=False):
            client = H2OClient(server_url(self.server), timeout=3)
            judgment = client.score("restore a private cookie", self.snippet)
        self.assertEqual(judgment.score, 2.4)
        self.assertEqual(proxy.requests, [])
        self.assertEqual(len(self.server.requests), 1)
        payload = json.loads(self.server.requests[0]["body"])
        self.assertEqual(payload["state"]["source"], self.snippet.text)

    def test_redirects_are_rejected_without_contacting_target(self):
        target = self.enterContext(decision_server())
        self.server.response_headers = {"Location": server_url(target) + "/collect"}
        self.server.response_body = b'{"message":"redirect"}'
        for status in (301, 302, 303, 307, 308):
            self.server.response_status = status
            with self.subTest(status=status), self.assertRaisesRegex(
                    RuntimeError, f"H2O returned HTTP {status}"):
                self.client.score("restore a private cookie", self.snippet)
        self.assertEqual(len(self.server.requests), 5)
        self.assertEqual(target.requests, [])

    def test_invalid_numeric_scores_and_distributions_are_rejected(self):
        valid = VALID_RESPONSE["answers"]["relevance"]
        cases = []
        for score in (-0.01, 3.01, float("nan"), float("inf"), "not-a-score"):
            cases.append((f"score {score}", {**valid, "score": score}))
        distributions = {
            "missing class": {"0": 0.1, "1": 0.1, "2": 0.8},
            "extra class": {"0": 0.1, "1": 0.1, "2": 0.1, "3": 0.7, "4": 0},
            "wrong total": {"0": 0.1, "1": 0.1, "2": 0.1, "3": 0.1},
            "negative probability": {"0": -0.1, "1": 0.1, "2": 0.1, "3": 0.9},
            "probability above one": {"0": 0, "1": 0, "2": 0, "3": 1.1},
            "nan probability": {"0": float("nan"), "1": 0.1, "2": 0.1, "3": 0.8},
            "infinite probability": {"0": float("inf"), "1": 0, "2": 0, "3": 0},
            "nonnumeric probability": {"0": "bad", "1": 0.1, "2": 0.1, "3": 0.8},
        }
        cases.extend((name, {**valid, "probabilities": probabilities})
                     for name, probabilities in distributions.items())
        for name, answer in cases:
            result = {"answers": {"relevance": answer}}
            self.server.response_body = json.dumps(result).encode()
            with self.subTest(case=name), self.assertRaises(ValueError):
                self.client.score("restore a cookie", self.snippet)

    def test_remote_or_ambiguous_service_urls_are_rejected(self):
        urls = (
            "http://example.com:8741", "http://192.168.1.2:8741",
            "http://127.0.0.2:8741", "http://2130706433:8741",
            "https://localhost:8741", "ftp://localhost:8741",
            "http://user:secret@localhost:8741", "http://localhost:8741/path",
            "http://@localhost:8741", "http://:@localhost:8741",
            "http://localhost:8741?q=x", "http://localhost:8741#fragment",
            "http://localhost:0", "http://localhost:99999", "http://localhost:bad",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                H2OClient(url)


if __name__ == "__main__":
    unittest.main()
