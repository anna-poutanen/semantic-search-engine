import contextlib
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from semantic_search import cli
from semantic_search.corpus import Snippet


def window(identifier, symbol=None, start_line=1):
    return Snippet(identifier, "src/code.py", symbol or identifier, start_line, start_line + 1,
                   f"return '{identifier}'", identifier)


class CLITests(unittest.TestCase):
    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.main(list(args))
        return output.getvalue()

    def test_original_benchmark_keeps_ten_bm25_candidates(self):
        with patch.object(cli, "run_experiment", return_value={"metrics": {}}) as benchmark:
            self.call("benchmark", "--output", "results/test")
        benchmark.assert_called_once_with(Path(".cache/corpora/flask"), Path("benchmarks/flask_queries.json"),
                                          Path("results/test"), "http://127.0.0.1:8741", 10, 160)

    def test_dense_default_scores_only_four_windows_and_deduplicates_after_reranking(self):
        items = [window("early", "same"), window("implementation", "same", 200),
                 window("other"), window("fourth"), window("unscored")]
        embedder = Mock()
        embedder.embed_query.return_value = [1, 0]
        dense = Mock()
        dense.search.return_value = [(item, 1 - i * 0.1) for i, item in enumerate(items)]
        scores = {"early": 1, "implementation": 3, "other": 2, "fourth": 0}
        scored = []

        def judge(query, snippet):
            embedder.close.assert_called_once()
            scored.append(snippet.id)
            return SimpleNamespace(score=scores[snippet.id])

        with patch.object(cli, "extract_snippets", return_value=items) as extract, \
                patch("semantic_search.embeddings.QwenEmbedder", return_value=embedder), \
                patch("semantic_search.index.build_or_load_index", return_value=("vectors", {})) as index, \
                patch.object(cli, "DenseRetriever", return_value=dense), \
                patch.object(cli, "H2OClient", return_value=SimpleNamespace(score=judge)):
            output = self.call("search", "find implementation", "--repo", "repo", "--max-results", "2")
        extract.assert_called_once_with(Path("repo"), "src/flask", max_lines=160, overlap_lines=40)
        index.assert_called_once_with(items, embedder, Path(".cache/indexes"))
        dense.search.assert_called_once_with([1, 0], 4)
        self.assertEqual(scored, ["early", "implementation", "other", "fourth"])
        self.assertIn("src/code.py:200-201 same", output)
        self.assertIn("return 'other'", output)
        self.assertNotIn("return 'early'", output)
        self.assertNotIn("return 'fourth'", output)
        self.assertNotIn("unscored", output)

    def test_no_rerank_fetches_at_least_requested_results_and_closes_embeddings(self):
        items = [window(str(i)) for i in range(6)]
        embedder, dense = Mock(), Mock()
        embedder.embed_query.return_value = [1, 0]
        dense.search.return_value = [(item, 1) for item in items]
        with patch.object(cli, "extract_snippets", return_value=items), \
                patch("semantic_search.embeddings.QwenEmbedder", return_value=embedder), \
                patch("semantic_search.index.build_or_load_index", return_value=("vectors", {})), \
                patch.object(cli, "DenseRetriever", return_value=dense), \
                patch.object(cli, "H2OClient") as h2o:
            output = self.call("search", "query", "--repo", "repo", "--no-rerank",
                               "--candidates", "2", "--max-results", "6")
        dense.search.assert_called_once_with([1, 0], 6)
        embedder.close.assert_called_once()
        h2o.assert_not_called()
        self.assertIn("fetched 6 of up to 6 source windows", output)

    def test_lexical_only_alias_uses_bm25_and_skips_both_models(self):
        lexical = Mock()
        lexical.search.return_value = [(window("match"), 1)]
        with patch.object(cli, "extract_snippets", return_value=[window("match")]), \
                patch.object(cli, "BM25Retriever", return_value=lexical), \
                patch("semantic_search.embeddings.QwenEmbedder") as qwen, \
                patch.object(cli, "H2OClient") as h2o:
            output = self.call("search", "query", "--repo", "repo", "--lexical-only")
        lexical.search.assert_called_once_with("query", 5)
        qwen.assert_not_called()
        h2o.assert_not_called()
        self.assertIn("bm25 retrieval only", output)

    def test_hybrid_uses_fixed_rrf_depth_independent_of_requested_k(self):
        item = window("match")
        embedder, lexical, dense, hybrid = Mock(), Mock(), Mock(), Mock()
        embedder.embed_query.return_value = [1, 0]
        hybrid.search.return_value = [(item, 0.02)]
        with patch.object(cli, "extract_snippets", return_value=[item]), \
                patch("semantic_search.embeddings.QwenEmbedder", return_value=embedder), \
                patch("semantic_search.index.build_or_load_index", return_value=("vectors", {})), \
                patch.object(cli, "BM25Retriever", return_value=lexical), \
                patch.object(cli, "DenseRetriever", return_value=dense), \
                patch.object(cli, "HybridRetriever", return_value=hybrid) as constructor:
            self.call("search", "query", "--repo", "repo", "--retrieval", "hybrid", "--no-rerank")
        constructor.assert_called_once_with(lexical, dense, rrf_k=60, fetch_depth=50)
        hybrid.search.assert_called_once_with("query", [1, 0], 5)
        embedder.close.assert_called_once()

    def test_embedding_failure_still_closes_weights(self):
        embedder = Mock()
        with patch.object(cli, "extract_snippets", return_value=[window("match")]), \
                patch("semantic_search.embeddings.QwenEmbedder", return_value=embedder), \
                patch("semantic_search.index.build_or_load_index", side_effect=ValueError("invalid cache")):
            with self.assertRaisesRegex(ValueError, "invalid cache"):
                self.call("search", "query", "--repo", "repo")
        embedder.close.assert_called_once()

    def test_invalid_queries_and_sizes_fail_before_extracting_source(self):
        invalid = [("search", " ", "--repo", "repo"),
                   ("search", "query", "--repo", "repo", "--candidates", "0"),
                   ("search", "query", "--repo", "repo", "--max-results", "0"),
                   ("benchmark", "--output", "result", "--candidates", "0")]
        for args in invalid:
            with self.subTest(args=args), patch.object(cli, "extract_snippets") as extract, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.call(*args)
                self.assertEqual(error.exception.code, 2)
                extract.assert_not_called()

    def test_shortlist_benchmark_passes_paths_and_original_prior_run(self):
        with patch("semantic_search.shortlist.run_shortlist_experiment", create=True) as experiment:
            self.call("shortlist-benchmark", "--output", "results/shortlist")
        experiment.assert_called_once_with(
            Path(".cache/corpora/flask"), Path("benchmarks/flask_queries.json"),
            Path("results/shortlist"), Path(".cache/indexes"), "http://127.0.0.1:8741",
            Path("results/flask-h2o-q8-2026-10-12"))


if __name__ == "__main__":
    unittest.main()
