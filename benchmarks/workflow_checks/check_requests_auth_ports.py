"""Offline public-session redirect checks, including credential leak controls."""

import unittest

from common import checkout, run_tests

checkout("requests", "src")
import requests
from requests.adapters import BaseAdapter


class RedirectAdapter(BaseAdapter):
    def __init__(self, target):
        self.target = target
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append(request)
        if len(self.sent) > 2:
            raise AssertionError("Unexpected extra network request")
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 302 if len(self.sent) == 1 else 200
        response._content = b"ok"
        response._content_consumed = True
        if len(self.sent) == 1:
            response.headers["Location"] = self.target
        return response

    def close(self):
        pass


class RedirectCredentials(unittest.TestCase):
    def redirect(self, old, new):
        with requests.Session() as session:
            session.trust_env = False
            adapter = RedirectAdapter(new)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            response = session.get(old, headers={"Authorization": "Bearer fixture"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(adapter.sent), 2)
            return adapter.sent[1].headers.get("Authorization")

    def test_equivalent_default_ports_keep_credentials(self):
        for scheme, port in (("http", 80), ("https", 443)):
            for old, new in (
                (f"{scheme}://example.test/a", f"{scheme}://example.test:{port}/b"),
                (f"{scheme}://example.test:{port}/a", f"{scheme}://example.test/b"),
            ):
                with self.subTest(old=old, new=new):
                    self.assertEqual(self.redirect(old, new), "Bearer fixture")

    def test_same_port_and_standard_https_upgrade_keep_credentials(self):
        for old, new in (
            ("https://example.test:8443/a", "https://example.test:8443/b"),
            ("http://example.test/a", "https://example.test/b"),
            ("http://example.test:80/a", "https://example.test:443/b"),
        ):
            with self.subTest(old=old, new=new):
                self.assertEqual(self.redirect(old, new), "Bearer fixture")

    def test_changed_origin_drops_credentials(self):
        for old, new in (
            ("https://example.test/a", "https://other.test/b"),
            ("https://example.test/a", "https://example.test:8443/b"),
            ("https://example.test:8443/a", "https://example.test/b"),
            ("https://example.test/a", "http://example.test/b"),
            ("http://example.test:8080/a", "https://example.test:8443/b"),
        ):
            with self.subTest(old=old, new=new):
                self.assertIsNone(self.redirect(old, new))


if __name__ == "__main__":
    run_tests()
