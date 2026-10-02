"""Post-output diagnostics for two assumptions in the unchanged frozen probes.

The public task permits replaying the original chunk boundaries. It requires
ClientDisconnect preservation for body reads, without explicitly requiring the
middleware to raise before a downstream app is called. These diagnostics use
those behavioral requirements and retain the original nine-case failures.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
from pathlib import Path
import sys


def scope():
    return {"type": "http", "method": "POST", "path": "/", "raw_path": b"/",
            "query_string": b"", "headers": [], "scheme": "http", "http_version": "1.1",
            "server": ("test", 80), "client": ("local", 1)}


def receiver(messages):
    pending = list(messages)
    reads = []

    async def receive():
        if not pending:
            raise AssertionError("Unexpected read beyond supplied ASGI messages")
        message = pending.pop(0)
        reads.append(message)
        return message

    return receive, reads


async def empty_chunk_replay(middleware):
    first = {"type": "http.disconnect", "reason": "first"}
    second = {"type": "http.disconnect", "reason": "second"}
    receive, reads = receiver([{"type": "http.request", "body": b"", "more_body": True},
                               {"type": "http.request"}, first, second])
    replayed = []
    downstream = []

    async def app(s, r, w):
        assert len(reads) == 2
        while True:
            message = await r()
            assert message["type"] == "http.request"
            replayed.append(message)
            assert len(reads) == 2
            if not message.get("more_body", False):
                break
        assert b"".join(message.get("body", b"") for message in replayed) == b""
        downstream.extend([await r(), await r()])
        assert downstream == [first, second]

    await middleware(app, max_bytes=0)(scope(), receive, None)
    assert len(reads) == 4
    return {"passed": True, "replayed_request_messages": replayed,
            "replayed_message_count": len(replayed), "original_reads": len(reads),
            "disconnect_delegation": downstream}


async def partial_disconnect(middleware, Request, ClientDisconnect):
    receive, reads = receiver([{"type": "http.request", "body": b"abc", "more_body": True},
                               {"type": "http.disconnect"}])
    requests = []

    async def app(s, r, w):
        request = Request(s, r)
        requests.append(request)
        await request.body()

    observed = None
    try:
        await middleware(app, max_bytes=9)(scope(), receive, None)
    except ClientDisconnect as error:
        observed = error
    assert isinstance(observed, ClientDisconnect)
    assert len(reads) == 2
    assert all(not hasattr(request, "_body") for request in requests)
    return {"passed": True, "client_disconnect_preserved": True,
            "downstream_called": bool(requests), "original_reads": len(reads),
            "partial_body_cached": any(hasattr(request, "_body") for request in requests)}


async def diagnose(repo):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(repo))
    starlette = importlib.import_module("starlette")
    if not Path(starlette.__file__).resolve().is_relative_to(repo / "starlette"):
        raise RuntimeError("Diagnostic imported Starlette outside its evaluator copy")
    request_module = importlib.import_module("starlette.requests")
    middleware_module = importlib.import_module("starlette.middleware.body_limit")
    middleware = middleware_module.RequestBodyLimitMiddleware
    observations = {}
    for name, call in (("empty_chunk_replay_then_disconnect_delegation", lambda: empty_chunk_replay(middleware)),
                       ("partial_disconnect_preserved_by_body_consumption", lambda: partial_disconnect(
                           middleware, request_module.Request, request_module.ClientDisconnect))):
        try:
            observations[name] = await call()
        except Exception as error:
            observations[name] = {"passed": False, "error_type": type(error).__name__, "error": str(error)}
    return observations


def json_safe(value):
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    repo = parser.parse_args().repo.resolve(strict=True)
    observations = asyncio.run(diagnose(repo))
    receipt = {"version": 1, "classification": "Post-output diagnostics; original frozen probes and grades unchanged",
               "diagnostic_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "model_calls": 0, "network_calls": 0, "cases": observations,
               "passed": all(case["passed"] for case in observations.values())}
    print(json.dumps(json_safe(receipt), sort_keys=True))
    raise SystemExit(0 if receipt["passed"] else 1)
