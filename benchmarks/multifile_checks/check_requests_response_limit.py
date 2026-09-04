"""Cross-layer response budgets, redirects, eager/streamed bodies and gzip HTTP."""

from contextlib import contextmanager
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pickle
import threading
import unittest
from unittest.mock import patch
from common import checkout, run_tests

checkout("requests", "src")
import requests
from requests.adapters import BaseAdapter
from requests import exceptions


class Raw:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def stream(self, size, decode_content=True):
        yield from self.chunks

    def close(self):
        self.closed = True


class Adapter(BaseAdapter):
    def __init__(self, bodies, statuses=None, header_length="9999"):
        self.bodies, self.sent, self.responses = bodies, [], []
        self.statuses, self.header_length = (
            statuses or [200] * len(bodies),
            header_length,
        )

    def send(self, request, **kwargs):
        if "max_response_bytes" in kwargs:
            raise AssertionError("Custom budget leaked into adapter transport kwargs")
        i = len(self.sent)
        self.sent.append(request)
        r = requests.Response()
        r.request = request
        r.url = request.url
        r.status_code = self.statuses[i]
        r.raw = Raw(self.bodies[i])
        r.headers["Content-Length"] = self.header_length
        if r.status_code == 302:
            r.headers["Location"] = "/next"
        self.responses.append(r)
        return r

    def close(self):
        pass


def session(bodies, *, limit=None, statuses=None):
    s = requests.Session(max_response_bytes=limit)
    s.trust_env = False
    a = Adapter(bodies, statuses)
    s.mount("https://", a)
    return s, a


@contextmanager
def gzip_http():
    expanded = b"0123456789" * 30
    body = gzip.compress(expanded)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", expanded, len(body)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class ResponseLimits(unittest.TestCase):
    def test_public_error_and_eager_session_context(self):
        self.assertIs(requests.ResponseTooLarge, exceptions.ResponseTooLarge)
        self.assertTrue(
            issubclass(requests.ResponseTooLarge, requests.RequestException)
        )
        s, a = session([[b"12", b"345"]], limit=4)
        with s, self.assertRaises(requests.ResponseTooLarge) as raised:
            s.get("https://example.test/start")
        e = raised.exception
        self.assertEqual((e.limit, e.received), (4, 5))
        self.assertIs(e.response, a.responses[0])
        self.assertIs(e.request, a.sent[0])
        self.assertTrue(a.responses[0].raw.closed)

    def test_stream_never_delivers_the_crossing_chunk(self):
        s, a = session([[b"12", b"345", b"ignored"]], limit=4)
        with s:
            r = s.get("https://example.test/", stream=True)
            it = r.iter_content(2)
            self.assertEqual(next(it), b"12")
            with self.assertRaises(requests.ResponseTooLarge) as raised:
                next(it)
            self.assertEqual(raised.exception.received, 5)
            self.assertTrue(r.raw.closed)

    def test_live_budget_cannot_be_reset_by_another_iterator_or_content(self):
        class StatefulRaw(Raw):
            def __init__(self):
                super().__init__([b"12", b"345", b"ignored"])
                self.remaining = iter(self.chunks)
                self.reads = 0

            def stream(self, size, decode_content=True):
                for chunk in self.remaining:
                    self.reads += 1
                    yield chunk

        for second_read in ("iterator", "content"):
            with self.subTest(second_read=second_read):
                s, a = session([[b"placeholder"]], limit=4)
                with s:
                    r = s.get("https://example.test/", stream=True)
                    r.raw = StatefulRaw()
                    first = r.iter_content(2)
                    self.assertEqual(next(first), b"12")
                    with self.assertRaises(requests.ResponseTooLarge) as raised:
                        if second_read == "iterator":
                            next(r.iter_content(2))
                        else:
                            r.content
                    self.assertEqual(raised.exception.received, 5)
                    self.assertTrue(r.raw.closed)
                    self.assertEqual(r.raw.reads, 2)
                    with self.assertRaises(requests.ResponseTooLarge):
                        next(r.iter_content(2))
                    with self.assertRaises(requests.ResponseTooLarge):
                        r.content
                    with self.assertRaises(requests.ResponseTooLarge):
                        next(first)
                    self.assertEqual(r.raw.reads, 2)
        s, _ = session([[b"123"]], limit=3)
        with s:
            r = s.get("https://example.test/")
            self.assertEqual(b"".join(r.iter_content(1)), b"123")
            self.assertEqual(b"".join(r.iter_content(1)), b"123")

    def test_limit_error_survives_cleanup_failure_with_diagnostics(self):
        class FailingClose(Raw):
            def close(self):
                self.closed = True
                raise OSError("close failed")

        s, _ = session([[b"placeholder"]], limit=2)
        with s:
            r = s.get("https://example.test/", stream=True)
            r.raw = FailingClose([b"123"])
            with self.assertRaises(requests.ResponseTooLarge) as raised:
                list(r.iter_content(2))
            self.assertEqual(
                (raised.exception.limit, raised.exception.received), (2, 3)
            )
            self.assertTrue(r.raw.closed)
            notes = " ".join(getattr(raised.exception, "__notes__", []))
            self.assertTrue(
                "close failed" in notes
                or raised.exception.__cause__ is not None
                or raised.exception.__context__ is not None
            )

    def test_inheritance_override_disable_and_exact_boundary(self):
        for default, override, body in (
            (3, {}, b"123"),
            (1, {"max_response_bytes": 3}, b"123"),
            (1, {"max_response_bytes": None}, b"12345"),
            (None, {}, b"12345"),
            (0, {}, b""),
        ):
            with self.subTest(default=default, override=override):
                s, _ = session([[body]], limit=default)
                with s:
                    self.assertEqual(
                        s.get("https://example.test/", **override).content, body
                    )

    def test_invalid_values_fail_before_transport(self):
        for bad in (-1, True, False, 1.5, "3"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                requests.Session(max_response_bytes=bad)
            s, a = session([[b"1"]])
            with s, self.assertRaises(ValueError):
                s.get("https://example.test/", max_response_bytes=bad)
            self.assertEqual(a.sent, [])

    def test_zero_rejects_nonempty_and_header_is_not_a_size_oracle(self):
        s, _ = session([[b"1"]], limit=0)
        with s, self.assertRaises(requests.ResponseTooLarge):
            s.get("https://example.test/")
        s, _ = session([[b"ok"]], limit=2)
        with s:
            self.assertEqual(s.get("https://example.test/").content, b"ok")

    def test_redirect_responses_each_receive_the_same_budget(self):
        s, a = session([[b"ok"], [b"1234"]], limit=3, statuses=[302, 200])
        with s, self.assertRaises(requests.ResponseTooLarge) as raised:
            s.get("https://example.test/start")
        self.assertEqual(len(a.sent), 2)
        self.assertIs(raised.exception.response, a.responses[1])
        s, a = session([[b"1234"], [b"ok"]], limit=3, statuses=[302, 200])
        with s, self.assertRaises(requests.ResponseTooLarge):
            s.get("https://example.test/start")
        self.assertEqual(len(a.sent), 1)

    def test_prepared_send_and_session_pickle_preserve_setting(self):
        s, _ = session([[b"123"]], limit=2)
        prepared = s.prepare_request(requests.Request("GET", "https://example.test/"))
        with s, self.assertRaises(requests.ResponseTooLarge):
            s.send(prepared)
        restored = pickle.loads(pickle.dumps(requests.Session(max_response_bytes=7)))
        self.assertEqual(restored.max_response_bytes, 7)
        restored.close()

    def test_response_hooks_and_cached_content_do_not_bypass_limit(self):
        s, a = session([[b"x"]], limit=2)

        def replacement(response, **kwargs):
            r = requests.Response()
            r.request = response.request
            r.status_code = 200
            r._content = b"123"
            r._content_consumed = True
            return r

        with s, self.assertRaises(requests.ResponseTooLarge) as raised:
            s.get("https://example.test/", hooks={"response": replacement})
        self.assertEqual(raised.exception.received, 3)

    def test_real_http_gzip_limit_counts_decoded_bytes(self):
        with (
            gzip_http() as (url, expanded, wire_size),
            requests.Session(max_response_bytes=50) as s,
        ):
            s.trust_env = False
            self.assertLess(wire_size, 50)
            with self.assertRaises(requests.ResponseTooLarge):
                s.get(url, timeout=3)
            self.assertEqual(
                s.get(url, max_response_bytes=len(expanded), timeout=3).content,
                expanded,
            )

    def test_module_helpers_and_legacy_default_sessions(self):
        s, _ = session([[b"123"]], limit=None)
        with (
            patch("requests.sessions.Session", return_value=s),
            self.assertRaises(requests.ResponseTooLarge),
        ):
            requests.get("https://example.test/", max_response_bytes=2)
        legacy = requests.Session()
        legacy.trust_env = False
        adapter = Adapter([[b"123"]])
        legacy.mount("https://", adapter)
        with legacy:
            self.assertEqual(legacy.get("https://example.test/").content, b"123")

    def test_legacy_default_session_remains_unbounded(self):
        legacy = requests.Session()
        legacy.trust_env = False
        adapter = Adapter([[b"123"]])
        legacy.mount("https://", adapter)
        with legacy:
            self.assertEqual(legacy.get("https://example.test/").content, b"123")


if __name__ == "__main__":
    run_tests()
