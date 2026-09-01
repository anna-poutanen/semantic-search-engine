import unittest
from types import SimpleNamespace

from semantic_search.corpus import Snippet
from semantic_search.experiment import matches, unique_symbols
from semantic_search.shortlist import metrics_for_ranks, rerank_prefix


def snippet(identifier, start=1, end=10, symbol=None):
    return Snippet(identifier, "code.py", symbol or identifier, start, end, "source", "hash")


class ShortlistTests(unittest.TestCase):
    def test_high_scoring_gold_outside_prefix_cannot_enter_results(self):
        first, second, gold = [snippet(name) for name in ("first", "second", "gold")]
        pairs = [(first, 3), (second, 2), (gold, 1)]
        judgments = {first.id: SimpleNamespace(score=0), second.id: SimpleNamespace(score=1),
                     gold.id: SimpleNamespace(score=3)}
        self.assertEqual([s.id for s, _ in rerank_prefix(pairs, judgments, 1)], ["first"])
        self.assertEqual([s.id for s, _ in rerank_prefix(pairs, judgments, 2)], ["second", "first"])

    def test_deduplication_does_not_replace_partial_window_with_gold_window(self):
        partial = snippet("partial", 1, 70, "implementation")
        full = snippet("full", 60, 110, "implementation")
        pairs = [(partial, 2), (full, 1)]
        judgments = {partial.id: SimpleNamespace(score=3), full.id: SimpleNamespace(score=2)}
        retained = unique_symbols([s for s, _ in rerank_prefix(pairs, judgments, 2)])
        target = {"path": "code.py", "symbol": "implementation", "evidence_lines": [80, 100]}
        self.assertTrue(any(matches(s, target) for s, _ in pairs))
        self.assertFalse(any(matches(s, target) for s in retained))

    def test_missing_answers_stay_in_metric_denominator(self):
        metrics = metrics_for_ranks([1, None, 4])
        self.assertAlmostEqual(metrics["hit_at_1"], 1 / 3)
        self.assertAlmostEqual(metrics["hit_at_5"], 2 / 3)
        self.assertAlmostEqual(metrics["mrr"], 1.25 / 3)
        with self.assertRaises(ValueError):
            metrics_for_ranks([])


if __name__ == "__main__":
    unittest.main()
