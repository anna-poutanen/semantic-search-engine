import unittest

from semantic_search.corpus import Snippet
from semantic_search.experiment import matches, percentile, rank_of, retrieval_metrics, unique_symbols


def snippet(start, end, symbol="implementation"):
    return Snippet(f"src/example.py:{symbol}:{start}-{end}", "src/example.py", symbol,
                   start, end, "source", "hash")


class EvaluationTests(unittest.TestCase):
    def test_docstring_and_partial_windows_are_not_implementation_hits(self):
        target = {"path": "src/example.py", "symbol": "implementation", "evidence_lines": [80, 100]}
        self.assertFalse(matches(snippet(1, 70), target))
        self.assertFalse(matches(snippet(60, 90), target))
        self.assertTrue(matches(snippet(60, 110), target))
        self.assertEqual(rank_of([snippet(1, 70), snippet(60, 110)], [target]), 2)

    def test_duplicate_symbol_cannot_hide_missing_source_evidence(self):
        target = {"path": "src/example.py", "symbol": "implementation", "evidence_lines": [80, 100]}
        ranking = unique_symbols([snippet(1, 70), snippet(60, 110)])
        self.assertEqual(len(ranking), 1)
        self.assertIsNone(rank_of(ranking, [target]))

    def test_missing_gold_and_absent_queries_are_not_dropped_from_denominators(self):
        rows = [{"kind": "present", "rank": 1}, {"kind": "present", "rank": None},
                {"kind": "present", "rank": 4}, {"kind": "absent", "rank": None}]
        metrics = retrieval_metrics(rows, "rank")
        self.assertEqual(metrics["queries"], 3)
        self.assertAlmostEqual(metrics["hit_at_1"], 1 / 3)
        self.assertAlmostEqual(metrics["hit_at_5"], 2 / 3)
        self.assertAlmostEqual(metrics["mrr"], 1.25 / 3)

    def test_nearest_rank_percentile_reports_long_tail(self):
        self.assertEqual(percentile([1, 2, 3, 20], .95), 20)
        self.assertIsNone(percentile([], .95))


if __name__ == "__main__":
    unittest.main()
