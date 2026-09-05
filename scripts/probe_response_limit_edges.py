#!/usr/bin/env python3
"""Post-output, CPU-only response-limit edge probes in evaluator clones.

These probes supplement the unchanged frozen study grades. They were authored
after the first response-limit outputs: one retained candidate's own added
regressions exposed buffered-reader gaps, and source review suggested that a
context-manager cleanup retry could mask the required size error.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys


class Body(io.BytesIO):
    def __init__(self, value, fail_close=False):
        super().__init__(value)
        self.reads = 0
        self.closes = 0
        self.fail_close = fail_close

    def read(self, size=-1):
        self.reads += 1
        return super().read(size)

    def close(self):
        self.closes += 1
        if self.fail_close:
            raise OSError("controlled cleanup failure")
        super().close()


def error_info(error):
    if error is None:
        return None
    return {"type": type(error).__name__, "message": str(error),
            "cause_type": type(error.__cause__).__name__ if error.__cause__ else None,
            "context_type": type(error.__context__).__name__ if error.__context__ else None,
            "notes": getattr(error, "__notes__", [])}


def observed(call):
    try:
        return call(), None
    except Exception as error:
        return None, error


def setup(requests, value, limit, fail_close=False):
    response = requests.Response()
    response.status_code = 200
    response.raw = Body(value, fail_close=fail_close)
    response.encoding = "utf-8"

    class Adapter(requests.adapters.BaseAdapter):
        def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
            response.request = request
            response.url = request.url
            return response

        def close(self):
            pass

    session = requests.Session(max_response_bytes=limit)
    session.trust_env = False
    session.mount("http://", Adapter())
    return response, session


def buffered_lines(requests):
    response, session = setup(requests, b"a\nb\ncdef", 5)
    try:
        session.get("http://fixture.invalid/", stream=True)
        lines = response.iter_lines(chunk_size=4)
        first = next(lines)
        _, crossing = observed(lambda: response.content)
        reads = response.raw.reads
        later, error = observed(lambda: next(lines))
        passed = (first == b"a" and isinstance(crossing, requests.ResponseTooLarge)
                  and isinstance(error, requests.ResponseTooLarge)
                  and response.raw.reads == reads)
        return {"passed": passed, "first_line": first.decode(),
                "crossing": error_info(crossing), "later_error": error_info(error),
                "later_yield": later.decode() if isinstance(later, bytes) else later,
                "raw_reads_after_crossing": reads, "raw_reads_after_later": response.raw.reads}
    finally:
        response.raw.fail_close = False
        response.raw.close()
        session.close()


def cached_iterator(requests):
    response, session = setup(requests, b"abcd", 4)
    try:
        session.get("http://fixture.invalid/")
        chunks = response.iter_content(1)
        _, crossing = observed(lambda: session.get("http://fixture.invalid/", max_response_bytes=3))
        reads = response.raw.reads
        later, error = observed(lambda: next(chunks))
        passed = (isinstance(crossing, requests.ResponseTooLarge)
                  and isinstance(error, requests.ResponseTooLarge)
                  and response.raw.reads == reads)
        return {"passed": passed, "crossing": error_info(crossing),
                "later_error": error_info(error),
                "later_yield": later.decode() if isinstance(later, bytes) else later,
                "raw_reads_after_crossing": reads, "raw_reads_after_later": response.raw.reads}
    finally:
        response.raw.fail_close = False
        response.raw.close()
        session.close()


def context_cleanup(requests):
    response, session = setup(requests, b"abcd", 3, fail_close=True)
    try:
        def consume():
            with session.get("http://fixture.invalid/", stream=True) as returned:
                returned.content
        _, error = observed(consume)
        diagnostic = (isinstance(error.__cause__, OSError)
                      or isinstance(error.__context__, OSError)
                      or bool(getattr(error, "__notes__", []))) if error else False
        return {"passed": isinstance(error, requests.ResponseTooLarge) and diagnostic,
                "outer_error": error_info(error), "raw_reads": response.raw.reads,
                "close_attempts": response.raw.closes}
    finally:
        response.raw.fail_close = False
        response.raw.close()
        session.close()


def hook_chain(requests, cached):
    original, session = setup(requests, b"", 4)
    replacement = requests.Response()
    replacement.status_code = 200
    replacement.raw = Body(b"abcdefgh")
    replacement.encoding = "utf-8"
    if cached:
        replacement.content
    seen = []
    calls = []

    def replace(response, **kwargs):
        calls.append("replace")
        replacement.request = response.request
        return replacement

    def consume(response, **kwargs):
        calls.append("consume")
        for chunk in response.iter_content(4):
            seen.append(chunk.decode())

    try:
        _, error = observed(lambda: session.get("http://fixture.invalid/", stream=True,
                                               hooks={"response": [replace, consume]}))
        expected = [] if cached else ["abcd"]
        return {"passed": isinstance(error, requests.ResponseTooLarge) and seen == expected,
                "cached_before_hook": cached, "yielded_in_second_hook": seen,
                "expected_yielded_in_second_hook": expected,
                "hook_calls": calls, "error": error_info(error),
                "received": getattr(error, "received", None)}
    finally:
        replacement.raw.close()
        original.raw.close()
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    sys.path.insert(0, str(repo / "src"))
    import requests
    expected = repo / "src/requests/__init__.py"
    if Path(requests.__file__).resolve() != expected:
        raise RuntimeError("Probe imported a different Requests checkout")
    result = {"version": 3, "classification": "Post-output supplemental edge checks; frozen formal grades unchanged",
              "revision": "Original three v2 cases unchanged; add live/cached hook-chain variants selected after terminal11 source inspection. All applicable retained candidates and reference receive the same added cases, without feedback to coding trials",
              "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "feature_available": hasattr(requests, "ResponseTooLarge"),
              "implementation_neutral": "Public Session/read APIs with one deterministic in-memory adapter; no private budget names or edits to candidate source",
              "cases": {}}
    for name, probe in (("buffered_iter_lines_after_breach", buffered_lines),
                        ("cached_iterator_after_lower_limit_breach", cached_iterator),
                        ("context_manager_cleanup_preserves_size_error", context_cleanup),
                        ("hook_chain_live_replacement", lambda r: hook_chain(r, False)),
                        ("hook_chain_cached_replacement", lambda r: hook_chain(r, True))):
        if not result["feature_available"]:
            result["cases"][name] = {"passed": False, "setup_unavailable": "Pinned upstream has no response-limit feature; behavioral probe is inapplicable"}
            continue
        try:
            result["cases"][name] = probe(requests)
        except Exception as error:
            result["cases"][name] = {"passed": False, "setup_error": error_info(error)}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
