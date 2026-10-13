"""Streaming, decoding, public exception and real loopback HTTP contracts."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from common import checkout, run_tests

checkout("requests", "src")
import requests
from requests import exceptions


class Raw:
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    def stream(self, size, decode_content=True):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


def response(chunks):
    result = requests.Response()
    result.status_code = 200
    result.request = requests.Request("GET", "https://example.test/records").prepare()
    result.raw = Raw(chunks)
    result.encoding = "ascii"
    return result


@contextmanager
def http_fixture(body):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=ascii")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class NDJSON(unittest.TestCase):
    def test_public_error_identity_and_context(self):
        self.assertIs(requests.JSONLineDecodeError, exceptions.JSONLineDecodeError)
        self.assertTrue(
            issubclass(requests.JSONLineDecodeError, exceptions.InvalidJSONError)
        )
        r = response([b'{"ok":1}\n\nnot-json\n'])
        it = r.iter_json_lines()
        self.assertEqual(next(it), {"ok": 1})
        with self.assertRaises(requests.JSONLineDecodeError) as raised:
            next(it)
        error = raised.exception
        self.assertEqual(error.line_number, 3)
        self.assertEqual(error.line, b"not-json")
        self.assertIs(error.response, r)
        self.assertIs(error.request, r.request)
        self.assertIsInstance(error.__cause__, ValueError)

    def test_laziness_and_late_error(self):
        r = response([b'{"a"', b":1}\n", b"bad\n"])
        it = r.iter_json_lines(chunk_size=2)
        self.assertEqual(r.raw.reads, 0)
        self.assertEqual(next(it), {"a": 1})
        self.assertEqual(r.raw.reads, 2)
        self.assertIs(r._content, False)
        with self.assertRaises(requests.JSONLineDecodeError):
            next(it)

    def test_crlf_bom_unicode_and_json_primitives(self):
        data = b'\xef\xbb\xbf{"name":"\xc3\xb8"}\r\n \t\r\n0\nfalse\nnull\n[1,2]'
        for chunks in ([data], [data[i : i + 1] for i in range(len(data))]):
            with self.subTest(chunks=len(chunks)):
                self.assertEqual(
                    list(response(chunks).iter_json_lines()),
                    [{"name": "ø"}, 0, False, None, [1, 2]],
                )

    def test_blank_policy_and_no_phantom_trailing_line(self):
        self.assertEqual(
            list(response([b"1\n"]).iter_json_lines(skip_blank=False)), [1]
        )
        self.assertEqual(list(response([]).iter_json_lines(skip_blank=False)), [])
        r = response([b"1\n\r\n2\n"])
        it = r.iter_json_lines(skip_blank=False)
        self.assertEqual(next(it), 1)
        with self.assertRaises(requests.JSONLineDecodeError) as raised:
            next(it)
        self.assertEqual(raised.exception.line_number, 2)
        self.assertEqual(raised.exception.line, b"")

    def test_invalid_utf8_and_noninitial_bom_are_contextual_errors(self):
        for bad in (b"\xff", b"\xef\xbb\xbf1"):
            r = response([b"0\n" + bad + b"\n"])
            it = r.iter_json_lines()
            self.assertEqual(next(it), 0)
            with self.assertRaises(requests.JSONLineDecodeError) as raised:
                next(it)
            self.assertEqual(raised.exception.line_number, 2)
            self.assertEqual(raised.exception.line, bad)
            self.assertIsNotNone(raised.exception.__cause__)

    def test_arguments_rejected_without_stream_reads(self):
        for kwargs in (
            {"chunk_size": 0},
            {"chunk_size": -1},
            {"chunk_size": True},
            {"chunk_size": 1.5},
            {"skip_blank": 0},
        ):
            r = response([b"1\n"])
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                list(r.iter_json_lines(**kwargs))
            self.assertEqual(r.raw.reads, 0)

    def test_cached_response_and_existing_methods_unchanged(self):
        r = response([])
        r._content = b"1\n2\n"
        r._content_consumed = True
        self.assertEqual(list(r.iter_json_lines(chunk_size=1)), [1, 2])
        self.assertEqual(list(r.iter_lines()), [b"1", b"2"])
        self.assertEqual(b"".join(r.iter_content(1)), b"1\n2\n")
        json_response = response([])
        json_response._content = b'{"ordinary":true}'
        json_response._content_consumed = True
        self.assertEqual(json_response.json(), {"ordinary": True})

    def test_ordinary_response_methods_are_unchanged_without_new_api(self):
        r = response([])
        r._content = b"1\n2\n"
        r._content_consumed = True
        self.assertEqual(list(r.iter_lines()), [b"1", b"2"])
        self.assertEqual(b"".join(r.iter_content(1)), b"1\n2\n")
        r._content = b'{"ordinary":true}'
        self.assertEqual(r.json(), {"ordinary": True})

    def test_real_session_http_streaming(self):
        with (
            http_fixture(b'{"x":"\xc3\xb8"}\n2\r\n') as url,
            requests.Session() as session,
        ):
            session.trust_env = False
            with session.get(url, stream=True, timeout=3) as r:
                self.assertEqual(list(r.iter_json_lines(chunk_size=1)), [{"x": "ø"}, 2])


if __name__ == "__main__":
    run_tests()
