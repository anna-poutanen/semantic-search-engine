import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from semantic_search import cli


RESPONSE = {"results": [{"path": "src/code.py", "symbol": "run", "start_line": 3,
                        "end_line": 4, "source": "def run():\n    return 1\n",
                        "h2o_score": None, "truncated": False}], "budget_exhausted": False}


class CLITests(unittest.TestCase):
    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.main(list(args))
        return output.getvalue()

    def test_standalone_fast_default_uses_shared_backend_and_closes_weights(self):
        backend = Mock()
        backend.search.return_value = RESPONSE
        with patch("semantic_search.backend.SearchBackend", return_value=backend) as factory:
            output = self.call("search", "find behavior", "--repo", "repo")
        factory.assert_called_once_with(Path("repo"), "src/flask",
            cli.index_path(Path("repo"), "src/flask", Path(".cache/indexes")),
            h2o_url="http://127.0.0.1:8741")
        backend.search.assert_called_once_with("find behavior", retrieval="dense", rerank=False,
            candidates=4, max_results=5, source_budget=12000)
        backend.close.assert_called_once()
        self.assertIn("src/code.py:3-4 run", output)

    def test_standalone_error_closes_backend(self):
        backend = Mock()
        backend.search.side_effect = ValueError("invalid saved source")
        with patch("semantic_search.backend.SearchBackend", return_value=backend):
            with self.assertRaisesRegex(ValueError, "invalid saved source"):
                self.call("search", "query", "--repo", "repo")
        backend.close.assert_called_once()

    def test_service_termination_closes_backend_and_restores_signal_handler(self):
        backend, server = Mock(), Mock()
        server.server_address = ("127.0.0.1", 8750)
        server.serve_forever.side_effect = lambda **kwargs: cli.interrupt_service(cli.signal.SIGTERM, None)
        previous_handler = Mock()
        with patch("semantic_search.backend.SearchBackend", return_value=backend), \
                patch("semantic_search.server.create_server", return_value=server), \
                patch.object(cli.signal, "signal", return_value=previous_handler) as install:
            output = self.call("serve", "--repo", "repo")
        self.assertIn("Ready:", output)
        backend.search.assert_called_once_with("Find the implementation", max_results=1)
        server.server_close.assert_called_once()
        backend.close.assert_called_once()
        backend.start_watching.assert_called_once_with(poll_interval=1.0)
        self.assertEqual(install.call_args_list[0].args, (cli.signal.SIGTERM, cli.interrupt_service))
        self.assertEqual(install.call_args_list[1].args, (cli.signal.SIGTERM, previous_handler))

    def test_mcp_command_reuses_service_url(self):
        with patch("semantic_search.mcp_server.run_mcp") as run:
            self.call("mcp", "--server", "http://127.0.0.1:9123")
        run.assert_called_once_with("http://127.0.0.1:9123", agent_text_only=False)

    def test_mcp_command_can_request_one_text_copy(self):
        with patch("semantic_search.mcp_server.run_mcp") as run:
            self.call("mcp", "--server", "http://127.0.0.1:9123", "--agent-text-only")
        run.assert_called_once_with("http://127.0.0.1:9123", agent_text_only=True)

    def test_server_search_does_not_create_local_backend_and_transmits_budget(self):
        client = Mock()
        client.search.return_value = RESPONSE
        with patch("semantic_search.client.SearchClient", return_value=client) as factory, \
                patch("semantic_search.backend.SearchBackend") as backend:
            result = self.call("search", "query", "--server", "http://localhost:8750",
                "--retrieval", "hybrid", "--rerank", "--candidates", "3", "--source-budget", "80", "--json")
        factory.assert_called_once_with("http://localhost:8750")
        client.search.assert_called_once_with("query", retrieval="hybrid", rerank=True,
            candidates=3, max_results=5, source_budget=80)
        backend.assert_not_called()
        self.assertEqual(json.loads(result), RESPONSE)

    def test_lexical_alias_disables_reranking(self):
        backend = Mock()
        backend.search.return_value = RESPONSE
        with patch("semantic_search.backend.SearchBackend", return_value=backend):
            self.call("search", "query", "--repo", "repo", "--lexical-only", "--rerank")
        self.assertEqual(backend.search.call_args.kwargs["retrieval"], "bm25")
        self.assertFalse(backend.search.call_args.kwargs["rerank"])

    def test_invalid_queries_scope_and_sizes_fail_before_backend(self):
        invalid = [("search", " ", "--repo", "repo"), ("search", "query"),
                   ("search", "query", "--repo", "repo", "--server", "http://localhost:8750"),
                   ("search", "query", "--repo", "repo", "--candidates", "0"),
                   ("search", "query", "--repo", "repo", "--max-results", "0"),
                   ("search", "query", "--repo", "repo", "--source-budget", "0")]
        for args in invalid:
            with self.subTest(args=args), patch("semantic_search.backend.SearchBackend") as backend, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.call(*args)
                self.assertEqual(error.exception.code, 2)
                backend.assert_not_called()

    def test_status_and_refresh_use_service(self):
        client = Mock()
        client.status.return_value = {"windows": 2}
        client.refresh.return_value = {"indexstats": {"indexed_windows": 1}}
        with patch("semantic_search.client.SearchClient", return_value=client):
            self.assertEqual(json.loads(self.call("status")), {"windows": 2})
            self.assertEqual(json.loads(self.call("refresh")), {"indexstats": {"indexed_windows": 1}})
        client.status.assert_called_once()
        client.refresh.assert_called_once()

    def test_source_budget_marker_and_truthful_excerpt_boundaries_render(self):
        response = {"results": [{**RESPONSE["results"][0], "end_line": 3,
                                "truncated": True, "source": "def run():\n"}], "budget_exhausted": True}
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            cli.print_results(response)
        self.assertIn("src/code.py:3-3", capture.getvalue())
        self.assertIn("excerpt truncated", capture.getvalue())
        self.assertIn("Source budget reached", capture.getvalue())

    def test_original_benchmark_keeps_ten_bm25_candidates(self):
        with patch.object(cli, "run_experiment", return_value={"metrics": {}}) as benchmark:
            self.call("benchmark", "--output", "results/test")
        benchmark.assert_called_once_with(Path(".cache/corpora/flask"), Path("benchmarks/flask_queries.json"),
            Path("results/test"), "http://127.0.0.1:8741", 10, 160)

    def test_shortlist_benchmark_passes_frozen_prior_run(self):
        with patch("semantic_search.shortlist.run_shortlist_experiment") as experiment:
            self.call("shortlist-benchmark", "--output", "results/shortlist")
        experiment.assert_called_once_with(Path(".cache/corpora/flask"), Path("benchmarks/flask_queries.json"),
            Path("results/shortlist"), Path(".cache/indexes"), "http://127.0.0.1:8741",
            Path("results/flask-h2o-q8-2026-10-12"))


if __name__ == "__main__":
    unittest.main()
