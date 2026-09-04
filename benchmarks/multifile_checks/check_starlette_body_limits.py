"""Bounded low-level reads and original HTTP TestClient/ASGI integration."""

import unittest
from common import checkout, run_tests

checkout("starlette", ".")
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request, ClientDisconnect
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def scope():
    return {
        "type": "http",
        "method": "POST",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "http_version": "1.1",
        "server": ("test", 80),
        "client": ("local", 1),
    }


def receiver(messages):
    pending = list(messages)
    reads = []

    async def receive():
        if not pending:
            raise AssertionError("Unexpected receive after supplied body")
        message = pending.pop(0)
        reads.append(message)
        return message

    return receive, reads


class BodyHelpers(unittest.IsolatedAsyncioTestCase):
    async def test_direct_helper_limit_error_identity_attributes_and_read_stop(self):
        from starlette.exceptions import RequestBodyTooLarge

        self.assertTrue(issubclass(RequestBodyTooLarge, HTTPException))
        receive, reads = receiver(
            [
                {"type": "http.request", "body": b"12", "more_body": True},
                {"type": "http.request", "body": b"345", "more_body": True},
                {"type": "http.request", "body": b"ignored", "more_body": False},
            ]
        )
        request = Request(scope(), receive)
        with self.assertRaises(RequestBodyTooLarge) as raised:
            await request.body(max_bytes=4)
        self.assertEqual(
            (
                raised.exception.status_code,
                raised.exception.limit,
                raised.exception.received,
            ),
            (413, 4, 5),
        )
        self.assertEqual(len(reads), 2)
        self.assertFalse(hasattr(request, "_body"))

    async def test_exact_boundary_unbounded_empty_and_cached_stricter_limit(self):
        from starlette.exceptions import RequestBodyTooLarge

        receive, reads = receiver(
            [{"type": "http.request", "body": b"123", "more_body": False}]
        )
        request = Request(scope(), receive)
        self.assertEqual(await request.body(max_bytes=3), b"123")
        self.assertEqual(await request.body(max_bytes=None), b"123")
        with self.assertRaises(RequestBodyTooLarge):
            await request.body(max_bytes=2)
        self.assertEqual(len(reads), 1)
        receive, _ = receiver(
            [{"type": "http.request", "body": b"", "more_body": False}]
        )
        self.assertEqual(await Request(scope(), receive).body(max_bytes=0), b"")

    async def test_invalid_limits_before_receiving_and_disconnect_unchanged(self):
        for bad in (-1, True, False, 1.5, "4"):
            receive, reads = receiver(
                [{"type": "http.request", "body": b"ok", "more_body": False}]
            )
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                await Request(scope(), receive).body(max_bytes=bad)
            self.assertEqual(reads, [])
        receive, _ = receiver([{"type": "http.disconnect"}])
        with self.assertRaises(ClientDisconnect):
            await Request(scope(), receive).body(max_bytes=3)


class BodyMiddleware(unittest.TestCase):
    def application(self, limit):
        from starlette.middleware.body_limit import RequestBodyLimitMiddleware

        called = []

        async def endpoint(request):
            called.append("endpoint")
            first = await request.body()
            second = await request.body()
            return JSONResponse({"body": first.decode(), "same": first == second})

        return RequestBodyLimitMiddleware(
            Starlette(routes=[Route("/", endpoint, methods=["POST"])]), max_bytes=limit
        ), called

    def test_real_http_client_replays_accepted_body_and_rejects_before_endpoint(self):
        for backend in ("asyncio", "trio"):
            with self.subTest(backend=backend):
                app, called = self.application(4)
                with TestClient(app, backend=backend) as client:
                    r = client.post("/", content=b"1234")
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(r.json(), {"body": "1234", "same": True})
                    r = client.post("/", content=b"12345")
                    self.assertEqual(r.status_code, 413)
                    self.assertEqual(r.text, "Request body too large")
                    self.assertEqual(called, ["endpoint"])

    def test_empty_zero_unlimited_and_constructor_validation(self):
        from starlette.middleware.body_limit import RequestBodyLimitMiddleware

        for bad in (-1, True, False, 2.5, "2"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                RequestBodyLimitMiddleware(None, max_bytes=bad)
        app, _ = self.application(0)
        with TestClient(app) as client:
            self.assertEqual(client.post("/", content=b"").status_code, 200)
            self.assertEqual(client.post("/", content=b"x").status_code, 413)
        app, _ = self.application(None)
        with TestClient(app) as client:
            self.assertEqual(client.post("/", content=b"x" * 100).status_code, 200)

    def test_original_endpoint_failures_propagate_through_testclient(self):
        from starlette.middleware.body_limit import RequestBodyLimitMiddleware

        async def endpoint(request):
            await request.body()
            raise RuntimeError("endpoint failure")

        app = RequestBodyLimitMiddleware(
            Starlette(routes=[Route("/", endpoint, methods=["POST"])]), max_bytes=4
        )
        for backend in ("asyncio", "trio"):
            with (
                self.subTest(backend=backend),
                TestClient(app, backend=backend) as client,
            ):
                with self.assertRaisesRegex(RuntimeError, "endpoint failure"):
                    client.post("/", content=b"ok")


class BodyASGI(unittest.IsolatedAsyncioTestCase):
    async def test_none_limit_does_not_buffer_before_calling_application(self):
        from starlette.middleware.body_limit import RequestBodyLimitMiddleware

        receive, reads = receiver(
            [{"type": "http.request", "body": b"original", "more_body": False}]
        )
        observed = []

        async def app(s, r, w):
            observed.append(len(reads))
            observed.append(await r())

        await RequestBodyLimitMiddleware(app, max_bytes=None)(scope(), receive, None)
        self.assertEqual(
            observed,
            [0, {"type": "http.request", "body": b"original", "more_body": False}],
        )

    async def test_chunked_crossing_stops_reading_without_calling_application(self):
        from starlette.middleware.body_limit import RequestBodyLimitMiddleware

        called = []
        messages = []

        async def app(*args):
            called.append(True)

        async def send(message):
            messages.append(message)

        receive, reads = receiver(
            [
                {"type": "http.request", "body": b"12", "more_body": True},
                {"type": "http.request", "body": b"345", "more_body": True},
                {"type": "http.request", "body": b"ignored", "more_body": False},
            ]
        )
        await RequestBodyLimitMiddleware(app, max_bytes=4)(scope(), receive, send)
        self.assertEqual(len(reads), 2)
        self.assertEqual(called, [])
        self.assertEqual(messages[0]["status"], 413)

    async def test_replay_preserves_bytes_then_delegates_disconnect_and_ignores_headers(
        self,
    ):
        from starlette.middleware.body_limit import RequestBodyLimitMiddleware

        seen = []

        async def app(scope, receive, send):
            while True:
                message = await receive()
                seen.append(message)
                if message["type"] == "http.request" and not message.get(
                    "more_body", False
                ):
                    break
            seen.append(await receive())

        receive, _ = receiver(
            [
                {"type": "http.request", "body": b"a", "more_body": True},
                {"type": "http.request", "body": b"b", "more_body": False},
                {"type": "http.disconnect"},
            ]
        )
        s = scope()
        s["headers"] = [(b"content-length", b"999")]
        await RequestBodyLimitMiddleware(app, max_bytes=2)(s, receive, None)
        self.assertEqual(
            b"".join(message.get("body", b"") for message in seen[:-1]), b"ab"
        )
        self.assertTrue(all(message["type"] == "http.request" for message in seen[:-1]))
        self.assertEqual(seen[-1], {"type": "http.disconnect"})

    async def test_websocket_and_lifespan_scope_pass_through_without_reading(self):
        from starlette.middleware.body_limit import RequestBodyLimitMiddleware

        for kind in ("websocket", "lifespan"):
            calls = []

            async def app(s, r, w):
                calls.append((s, r, w))

            async def receive():
                raise AssertionError("Middleware must not read non-HTTP bodies")

            await RequestBodyLimitMiddleware(app, max_bytes=0)(
                {"type": kind}, receive, None
            )
            self.assertEqual(calls, [({"type": kind}, receive, None)])


if __name__ == "__main__":
    run_tests()
