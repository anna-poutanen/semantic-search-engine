"""Redirect method behavior through actual Session requests; no live sockets."""

import unittest

from common import checkout, run_tests

checkout("requests", "src")
import requests
from requests.adapters import BaseAdapter


class RedirectAdapter(BaseAdapter):
    def __init__(self, status):
        self.status = status
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append(request)
        if len(self.sent) > 2:
            raise AssertionError("Unexpected extra request")
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = self.status if len(self.sent) == 1 else 200
        response._content = b"ok"
        response._content_consumed = True
        if len(self.sent) == 1:
            response.headers["Location"] = "/destination"
        return response

    def close(self):
        pass


class RedirectMethods(unittest.TestCase):
    def redirect(self, status, method):
        with requests.Session() as session:
            session.trust_env = False
            adapter = RedirectAdapter(status)
            session.mount("https://", adapter)
            response = session.request(method, "https://example.test/start", data=b"fixture")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(adapter.sent), 2)
            return adapter.sent[1]

    def test_301_only_rewrites_post(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "GET", "HEAD"):
            with self.subTest(method=method):
                actual = self.redirect(301, method)
                self.assertEqual(actual.method, "GET" if method == "POST" else method)

    def test_302_and_303_keep_head_and_rewrite_other_methods(self):
        for status in (302, 303):
            for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD"):
                with self.subTest(status=status, method=method):
                    actual = self.redirect(status, method)
                    self.assertEqual(actual.method, "HEAD" if method == "HEAD" else "GET")

    def test_307_and_308_preserve_method_and_body(self):
        for status in (307, 308):
            for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD"):
                with self.subTest(status=status, method=method):
                    actual = self.redirect(status, method)
                    self.assertEqual(actual.method, method)
                    self.assertEqual(actual.body, b"fixture")


if __name__ == "__main__":
    run_tests()
