"""ASGI streaming behavior across sync/async text and binary iterators."""

import unittest

from common import checkout, run_tests

checkout("starlette", ".")
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse


class StreamingBuffers(unittest.IsolatedAsyncioTestCase):
    async def stream(self, content):
        messages = []
        completed = []

        async def send(message):
            messages.append(message)

        async def receive():
            raise AssertionError("ASGI 2.4 streaming should not call receive")

        await StreamingResponse(content, background=BackgroundTask(completed.append, "done"))(
            {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
        )
        self.assertEqual(messages[0]["type"], "http.response.start")
        self.assertEqual(messages[-1], {"type": "http.response.body", "body": b"", "more_body": False})
        self.assertEqual(completed, ["done"])
        self.assertTrue(all(message["more_body"] for message in messages[1:-1]))
        return messages[1:-1]

    async def test_sync_memoryview_and_bytes_are_binary_chunks(self):
        view = memoryview(b"buffer")
        messages = await self.stream(iter([b"prefix", view, "café"]))
        self.assertEqual([bytes(message["body"]) for message in messages], [b"prefix", b"buffer", "café".encode("utf-8")])

    async def test_async_memoryview_chunks(self):
        async def chunks():
            yield memoryview(b"first")
            yield memoryview(b"second")

        messages = await self.stream(chunks())
        self.assertEqual([bytes(message["body"]) for message in messages], [b"first", b"second"])

    async def test_plain_bytes_text_and_empty_iterators_remain_valid(self):
        messages = await self.stream(iter([b"binary", "text"]))
        self.assertEqual([message["body"] for message in messages], [b"binary", b"text"])
        self.assertEqual(await self.stream(iter([])), [])

    async def test_transport_error_remains_a_client_disconnect(self):
        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("fixture disconnect")

        async def receive():
            raise AssertionError("No receive expected")

        with self.assertRaises(ClientDisconnect):
            await StreamingResponse(iter([b"fixture"]))(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )


if __name__ == "__main__":
    run_tests()
