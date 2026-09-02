"""Async response/background integration, sequencing, and fail-fast checks."""

import asyncio
import unittest

from common import checkout, run_tests

checkout("starlette", ".")
from starlette.background import BackgroundTask, BackgroundTasks
from starlette.responses import Response


class OrderedBackground(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_callbacks_run_in_registration_order_after_response(self):
        events = []

        def first(value, *, suffix):
            events.append(value + suffix)

        async def second(value):
            await asyncio.sleep(0)
            events.append(value)

        async def send(message):
            events.append(message["type"])

        async def receive():
            raise AssertionError("Ordinary response should not receive")

        tasks = BackgroundTasks()
        tasks.add_task(first, "first", suffix="-done")
        tasks.add_task(second, "second-done")
        tasks.add_task(first, "third", suffix="-done")
        await Response("ok", background=tasks)({"type": "http"}, receive, send)
        self.assertEqual(events, ["http.response.start", "http.response.body", "first-done", "second-done", "third-done"])

    async def test_failure_does_not_run_later_callbacks(self):
        events = []

        def earlier():
            events.append("earlier")

        async def failing():
            events.append("failing")
            raise RuntimeError("fixture failure")

        def later():
            events.append("later")

        tasks = BackgroundTasks()
        tasks.add_task(earlier)
        tasks.add_task(failing)
        tasks.add_task(later)
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            await tasks()
        self.assertEqual(events, ["earlier", "failing"])

    async def test_constructor_tasks_keep_order_and_empty_sequence_is_valid(self):
        events = []
        await BackgroundTasks([BackgroundTask(events.append, "a"), BackgroundTask(events.append, "b")])()
        self.assertEqual(events, ["a", "b"])
        await BackgroundTasks()()


if __name__ == "__main__":
    run_tests()
