"""Lightweight stdlib mocks; no actual service, model, parser, or network call."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

SPEC = importlib.util.spec_from_file_location("beta_feedback", Path(__file__).resolve().parents[1] / "scripts/collect_beta_feedback.py")
feedback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feedback)


class Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.body[:limit]


class FeedbackTests(unittest.TestCase):
    def test_default_is_offline_and_output_has_no_private_fields(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(feedback, "collect_status") as request, \
                patch.object(feedback, "environment_summary", return_value={"os": "Darwin", "architecture": "arm64"}), redirect_stdout(io.StringIO()):
            output = Path(folder) / "feedback.json"
            feedback.main(["--output", str(output), "--public-project", "requests", "--revision", "a" * 40,
                           "--attempts", "3", "--full", "1", "--partial", "1", "--errors", "1", "--note", "timeout"])
            request.assert_not_called()
            data = json.loads(output.read_text())
            self.assertEqual(data["local_status"], {"collection": "not_requested"})
            self.assertEqual(data["session"]["reranker"], "off")
            self.assertEqual(data["counts"]["full_implementation"], 1)
            self.assertNotIn(folder, output.read_text())
            self.assertEqual(data["public_project"], {"name": "requests", "revision": "a" * 40})

    def test_status_discards_paths_queries_auth_logs_and_provenance(self):
        secret = "PRIVATE_SECRET"
        status = {"ready": True, "closed": False, "windows": 20, "repo": "/Users/private/repo",
                  "query": secret, "last_error": secret, "source": secret, "auth": secret,
                  "embedding_initialized": True, "dense_ready": True,
                  "watcher": {"running": True, "pending": False, "refresh_count": 2, "last_error": secret},
                  "providers": {"qwen": {"ready": True, "enabled": True, "provenance": secret, "token": secret},
                                "private-provider": {"ready": True}}}
        safe = feedback.status_summary(status)
        encoded = json.dumps(safe)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("private", encoded)
        self.assertEqual(safe["watcher"], {"running": True, "pending": False, "refresh_count": 2})
        self.assertEqual(safe["providers"], {"qwen": {"enabled": True, "ready": True}})

    def test_numeric_loopback_only(self):
        self.assertEqual(feedback.loopback_server("http://127.0.0.1:8750/"), "http://127.0.0.1:8750")
        self.assertEqual(feedback.loopback_server("http://[::1]:8750"), "http://[::1]:8750")
        for value in ("http://localhost:8750", "https://127.0.0.1", "http://example.com", "http://0.0.0.0",
                      "http://127.0.0.1:0", "http://user:PRIVATE@127.0.0.1", "http://127.0.0.1/search",
                      "http://127.0.0.1?token=PRIVATE", "http://127.0.0.1#PRIVATE", "http://127.0.0.1\n"):
            with self.subTest(value=value), self.assertRaises(feedback.argparse.ArgumentTypeError):
                feedback.loopback_server(value)

    def test_explicit_status_uses_get_only_no_proxy_or_redirect_and_stores_no_url(self):
        body = json.dumps({"ready": True, "closed": False, "windows": 7, "repo": "PRIVATE"}).encode()
        with patch.object(feedback, "build_opener") as build:
            build.return_value.open.return_value = Reply(body)
            result = feedback.collect_status("http://127.0.0.1:8750", seconds=.5)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.full_url, "http://127.0.0.1:8750/status")
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsNone(build.call_args.args[1].redirect_request(request, None, 302, "Found", {}, "http://example.com"))
            self.assertEqual(result, {"collection": "collected", "summary": {"ready": True, "closed": False, "windows": 7}})

    def test_unavailable_or_malformed_status_never_records_private_exception_or_body(self):
        cases = ((URLError("PRIVATE"), "connection_failed"), (HTTPError("PRIVATE", 302, "PRIVATE", {}, None), "http_error"))
        for error, expected in cases:
            with self.subTest(error=error), patch.object(feedback, "build_opener") as build:
                build.return_value.open.side_effect = error
                result = feedback.collect_status("http://127.0.0.1:8750")
                self.assertEqual(result["collection"], expected)
                self.assertNotIn("PRIVATE", json.dumps(result))
        for body, expected in ((b'{"ready":true,"ready":false}', "invalid_response"), (b'{"ready":NaN}', "invalid_response"),
                               (b"PRIVATE", "invalid_response"), (b"x" * (feedback.MAX_STATUS_BYTES + 1), "response_too_large")):
            with self.subTest(expected=expected), patch.object(feedback, "build_opener") as build:
                build.return_value.open.return_value = Reply(body)
                self.assertEqual(feedback.collect_status("http://127.0.0.1:8750")["collection"], expected)

    def test_existing_file_is_preserved_before_any_status_call(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(feedback, "collect_status") as request:
            output = Path(folder) / "feedback.json"
            output.write_text("original")
            with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                feedback.main(["--output", str(output), "--server", "http://127.0.0.1:8750"])
            request.assert_not_called()
            self.assertEqual(output.read_text(), "original")

    def test_arbitrary_notes_projects_revisions_and_invalid_counts_are_refused(self):
        examples = (["--note", "PRIVATE text"], ["--public-project", "private-repo"], ["--revision", "/private/path"],
                    ["--revision", "a" * 40], ["--attempts", "1", "--full", "2"], ["--attempts", "1", "--errors", "2"])
        with tempfile.TemporaryDirectory() as folder:
            for index, extra in enumerate(examples):
                output = Path(folder) / f"{index}.json"
                with self.subTest(extra=extra), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                    feedback.main(["--output", str(output), *extra])
                self.assertFalse(output.exists())

    def test_environment_omits_hostname_custom_os_tag_and_nonversion_metadata(self):
        with patch.object(feedback.platform, "system", return_value="Linux"), \
                patch.object(feedback.platform, "release", return_value="6.8-private-user"), \
                patch.object(feedback.platform, "machine", return_value="private-hostname"), \
                patch.object(feedback.importlib.metadata, "version", return_value="/private/package"):
            observed = feedback.environment_summary()
            self.assertEqual(observed["architecture"], "other")
            self.assertIsNone(observed["os_version"])
            self.assertTrue(all(value is None for value in observed["packages"].values()))


if __name__ == "__main__":
    unittest.main()
