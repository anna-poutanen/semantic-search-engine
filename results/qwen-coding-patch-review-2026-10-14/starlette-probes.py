"""Independent CPU contract probes, authored before the new Starlette outputs.

These checks supplement the prospectively frozen ten-case grade. They never
change that grade and operate only on an explicitly selected evaluator copy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest


parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
args = parser.parse_args()
repo = args.repo.resolve(strict=True)
sys.dont_write_bytecode = True
sys.path.insert(0, str(repo))
import starlette

if not Path(starlette.__file__).resolve().is_relative_to(repo / "starlette"):
    raise RuntimeError("The probe imported Starlette outside its evaluator copy")

from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect, Request


def scope(headers=()):
    return {"type": "http", "method": "POST", "path": "/", "raw_path": b"/",
            "query_string": b"", "headers": list(headers), "scheme": "http",
            "http_version": "1.1", "server": ("test", 80), "client": ("local", 1)}


def receiver(messages):
    pending = list(messages)
    reads = []

    async def receive():
        if not pending:
            raise AssertionError("Unexpected read beyond the supplied ASGI messages")
        message = pending.pop(0)
        reads.append(message)
        return message

    return receive, reads


def middleware(app, limit):
    from starlette.middleware.body_limit import RequestBodyLimitMiddleware
    return RequestBodyLimitMiddleware(app, max_bytes=limit)


class BodyContractEdges(unittest.IsolatedAsyncioTestCase):
    async def test_cached_json_lower_bound_preserves_cache_and_error_attributes(self):
        from starlette.exceptions import RequestBodyTooLarge
        body = b'{"ok": true}'
        receive, reads = receiver([{"type": "http.request", "body": body}])
        request = Request(scope(), receive)
        parsed = await request.json()
        with self.assertRaises(RequestBodyTooLarge) as raised:
            await request.body(max_bytes=len(body) - 1)
        error = raised.exception
        self.assertIsInstance(error, HTTPException)
        self.assertEqual((error.status_code, error.limit, error.received),
                         (413, len(body) - 1, len(body)))
        self.assertEqual(await request.body(), body)
        self.assertIs(await request.json(), parsed)
        self.assertEqual(len(reads), 1)

    async def test_partial_disconnect_preserved_without_partial_body_cache(self):
        receive, reads = receiver([{"type": "http.request", "body": b"abc", "more_body": True},
                                   {"type": "http.disconnect"}])
        request = Request(scope(), receive)
        with self.assertRaises(ClientDisconnect):
            await request.body(max_bytes=9)
        self.assertEqual(len(reads), 2)
        self.assertFalse(hasattr(request, "_body"))

    async def test_empty_chunks_do_not_change_cumulative_crossing(self):
        from starlette.exceptions import RequestBodyTooLarge
        receive, reads = receiver([{"type": "http.request", "body": b"", "more_body": True},
                                   {"type": "http.request", "body": b"12", "more_body": True},
                                   {"type": "http.request", "body": b"", "more_body": True},
                                   {"type": "http.request", "body": b"345", "more_body": True},
                                   {"type": "http.request", "body": b"never read"}])
        request = Request(scope(), receive)
        with self.assertRaises(RequestBodyTooLarge) as raised:
            await request.body(max_bytes=4)
        self.assertEqual((raised.exception.limit, raised.exception.received), (4, 5))
        self.assertEqual(len(reads), 4)
        self.assertFalse(hasattr(request, "_body"))

    async def test_omitted_empty_body_and_large_integer_bound(self):
        receive, reads = receiver([{"type": "http.request", "more_body": True},
                                   {"type": "http.request"}])
        request = Request(scope(), receive)
        self.assertEqual(await request.body(max_bytes=0), b"")
        self.assertEqual(await request.body(max_bytes=10 ** 100), b"")
        self.assertEqual(len(reads), 2)

    async def test_invalid_cached_limits_fail_before_another_read(self):
        receive, reads = receiver([{"type": "http.request", "body": b"abc"}])
        request = Request(scope(), receive)
        self.assertEqual(await request.body(), b"abc")
        for invalid in (-1, True, False, 1.0, "3", (), object()):
            with self.subTest(invalid_type=type(invalid).__name__), self.assertRaises(ValueError):
                await request.body(max_bytes=invalid)
        self.assertEqual(len(reads), 1)

    async def test_middleware_lying_small_content_length_cannot_bypass_limit(self):
        for header in (b"1", b"invalid", b"-1"):
            with self.subTest(content_length=header):
                receive, reads = receiver([
                    {"type": "http.request", "body": b"ab", "more_body": True},
                    {"type": "http.request", "body": b"cd", "more_body": True},
                    {"type": "http.request", "body": b"never read"}])
                calls, sent = [], []

                async def app(*unused):
                    calls.append(True)

                async def send(message):
                    sent.append(message)

                await middleware(app, 3)(scope([(b"content-length", header)]), receive, send)
                self.assertEqual(calls, [])
                self.assertEqual(len(reads), 2)
                starts = [message for message in sent if message["type"] == "http.response.start"]
                self.assertEqual([message["status"] for message in starts], [413])
                self.assertEqual(b"".join(message.get("body", b"") for message in sent
                                          if message["type"] == "http.response.body"),
                                 b"Request body too large")

    async def test_middleware_json_replay_preserves_split_utf8_and_cached_helpers(self):
        body = '{"name":"é☃"}'.encode("utf-8")
        chunks = [body[:10], b"", body[10:11], body[11:]]
        messages = [{"type": "http.request", "body": chunk,
                     "more_body": index < len(chunks) - 1}
                    for index, chunk in enumerate(chunks)]
        disconnect = {"type": "http.disconnect", "reason": "fixture"}
        receive, reads = receiver([*messages, disconnect])
        seen = []

        async def app(s, r, w):
            self.assertEqual(len(reads), len(chunks))
            request = Request(s, r)
            first = await request.json()
            seen.append(first)
            self.assertIs(await request.json(), first)
            self.assertEqual(await request.body(), body)
            self.assertEqual(await request.body(max_bytes=len(body)), body)
            seen.append(await r())

        await middleware(app, len(body))(scope([(b"content-length", b"1")]), receive, None)
        self.assertEqual(seen, [{"name": "é☃"}, disconnect])
        self.assertEqual(len(reads), len(chunks) + 1)

    async def test_middleware_zero_empty_replay_then_every_receive_delegates(self):
        first_disconnect = {"type": "http.disconnect", "reason": "first"}
        second_disconnect = {"type": "http.disconnect", "reason": "second"}
        receive, reads = receiver([{"type": "http.request", "body": b"", "more_body": True},
                                   {"type": "http.request"},
                                   first_disconnect, second_disconnect])
        seen = []

        async def app(s, r, w):
            self.assertEqual(len(reads), 2)
            seen.extend([await r(), await r(), await r()])

        await middleware(app, 0)(scope(), receive, None)
        self.assertEqual(seen[0].get("body", b""), b"")
        self.assertEqual(seen[0]["type"], "http.request")
        self.assertFalse(seen[0].get("more_body", False))
        self.assertEqual(seen[1:], [first_disconnect, second_disconnect])
        self.assertEqual(len(reads), 4)

    async def test_middleware_partial_disconnect_preserves_original_exception(self):
        receive, reads = receiver([{"type": "http.request", "body": b"abc", "more_body": True},
                                   {"type": "http.disconnect"}])
        calls = []

        async def app(*unused):
            calls.append(True)

        with self.assertRaises(ClientDisconnect):
            await middleware(app, 9)(scope(), receive, None)
        self.assertEqual(calls, [])
        self.assertEqual(len(reads), 2)


class CaseResult(unittest.TextTestResult):
    def __init__(self, *values, **settings):
        super().__init__(*values, **settings)
        self.cases = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append({"id": test.id().split(".")[-1], "passed": True})

    def addError(self, test, error):
        super().addError(test, error)
        self.cases.append({"id": test.id().split(".")[-1], "passed": False,
                           "error_type": error[0].__name__})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.cases.append({"id": test.id().split(".")[-1], "passed": False,
                           "error_type": error[0].__name__})

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            self.cases.append({"id": test.id().split(".")[-1], "passed": False,
                               "subtest": str(subtest), "error_type": error[0].__name__})


if __name__ == "__main__":
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2, resultclass=CaseResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BodyContractEdges))
    print(json.dumps({"version": 1, "classification": "Supplemental; frozen primary grades unchanged",
                      "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      "model_calls": 0, "tests_run": result.testsRun,
                      "failures": len(result.failures), "errors": len(result.errors),
                      "skipped": len(result.skipped), "passed": result.wasSuccessful(),
                      "cases": result.cases}, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
