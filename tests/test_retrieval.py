import unittest

from semantic_search.corpus import Snippet
from semantic_search.retrieval import BM25Retriever, DenseRetriever, HybridRetriever, tokenize


def snippet(identifier, text, path="src/example.py", symbol="run", start_line=1):
    return Snippet(identifier, path, symbol, start_line, start_line + 1, text, "unused")


class RetrievalTests(unittest.TestCase):
    def test_identifier_conventions_share_search_terms(self):
        self.assertEqual(tokenize("XMLHttpRequest getHTTPResponse create_session"),
                         ["xml", "http", "request", "get", "http", "response", "create", "session"])
        retriever = BM25Retriever([
            snippet("connection", "def initializeDatabase():\n    return connect_database()"),
            snippet("request", "def process_request():\n    return response()"),
        ])
        self.assertEqual(retriever.search("initialize database")[0][0].id, "connection")
        self.assertEqual(retriever.search("initialize_database"), retriever.search("initializeDatabase"))

    def test_path_and_symbol_are_searchable_without_matching_body(self):
        retriever = BM25Retriever([
            snippet("session", "return value", path="src/network/session.py", symbol="SessionManager.create"),
            snippet("cache", "return value", path="src/cache.py", symbol="Cache.clear"),
        ])
        self.assertEqual(retriever.search("network session manager")[0][0].id, "session")
        self.assertEqual(retriever.search("cache clear")[0][0].id, "cache")

    def test_ties_are_stable_when_input_order_changes(self):
        items = [snippet("b", "return request"), snippet("a", "return request")]
        first = BM25Retriever(items).search("request")
        second = BM25Retriever(reversed(items)).search("request")
        self.assertEqual([s.id for s, score in first], ["a", "b"])
        self.assertEqual(first, second)
        self.assertGreater(first[0][1], 0)

    def test_absent_empty_queries_and_limits(self):
        retriever = BM25Retriever([snippet("request", "return request")])
        self.assertEqual(retriever.search("unicorn"), [])
        self.assertEqual(retriever.search(""), [])
        self.assertEqual(retriever.search("request", limit=0), [])
        self.assertEqual(BM25Retriever([]).search("request"), [])
        with self.assertRaises(ValueError):
            retriever.search("request", limit=-1)


class DenseRetrievalTests(unittest.TestCase):
    def test_cosine_orders_direction_and_ignores_magnitude(self):
        items = [snippet("vertical", "up"), snippet("diagonal", "between"), snippet("horizontal", "right")]
        first = DenseRetriever(items, [[0, 2], [3, 3], [4, 0]]).search([7, 0])
        scaled = DenseRetriever(items, [[0, 2e-300], [3e300, 3e300], [4e-200, 0]]).search([7e300, 0])
        self.assertEqual([item.id for item, _ in first], ["horizontal", "diagonal", "vertical"])
        self.assertEqual([item.id for item, _ in first], [item.id for item, _ in scaled])
        for (_, score), (_, scaled_score) in zip(first, scaled):
            self.assertAlmostEqual(score, scaled_score)
        self.assertAlmostEqual(first[0][1], 1)
        self.assertAlmostEqual(first[1][1], 2 ** -0.5)
        self.assertEqual(first[2][1], 0)

    def test_equal_cosines_have_stable_ties_and_k_prefixes(self):
        items = [snippet("c", "same"), snippet("a", "same"), snippet("b", "same")]
        retriever = DenseRetriever(items, [[1, 0], [1, 0], [1, 0]])
        all_hits = retriever.search([1, 0], limit=10)
        reversed_hits = DenseRetriever(reversed(items), [[1, 0]] * 3).search([1, 0])
        self.assertEqual([item.id for item, _ in all_hits], ["a", "b", "c"])
        self.assertEqual(all_hits, reversed_hits)
        self.assertEqual(retriever.search([1, 0], limit=1), all_hits[:1])
        self.assertEqual(retriever.search([1, 0], limit=0), [])
        with self.assertRaises(ValueError):
            retriever.search([1, 0], limit=-1)

    def test_rejects_mismatched_nonfinite_zero_and_duplicate_embeddings(self):
        item = snippet("a", "value")
        invalid = [[], [1, 2], [[1, 2], [3, 4]], [[]], [[0, 0]],
                   [[float("nan"), 1]], [[float("inf"), 1]], [[1 + 1j, 2]],
                   [[[1, 2]]], [["invalid", 2]]]
        for vectors in invalid:
            with self.subTest(vectors=vectors), self.assertRaises(ValueError):
                DenseRetriever([item], vectors)
        with self.assertRaises(ValueError):
            DenseRetriever([item, item], [[1, 0], [0, 1]])
        retriever = DenseRetriever([item], [[1, 2]])
        for query in [[], [1], [1, 2, 3], [[1, 2]], [0, 0],
                      [float("nan"), 1], [float("inf"), 1], [1j, 1]]:
            with self.subTest(query=query), self.assertRaises(ValueError):
                retriever.search(query)

    def test_empty_corpus_with_declared_dimension_has_no_matches(self):
        import numpy as np

        self.assertEqual(DenseRetriever([], np.empty((0, 2))).search([1, 0]), [])


class HybridRetrievalTests(unittest.TestCase):
    def setUp(self):
        # The IDs are separate windows of the same symbol and must stay separate.
        self.items = [snippet("exact", "cache cache cache"),
                      snippet("semantic", "return request", start_line=10),
                      snippet("agreement", "cache", start_line=20)]
        self.lexical = BM25Retriever(self.items)
        self.dense = DenseRetriever(self.items, [[0, 1], [1, 0], [0.8, 0.6]])

    def test_complements_candidate_pools_and_rewards_agreement(self):
        retriever = HybridRetriever(self.lexical, self.dense, fetch_depth=2)
        lexical_ids = {item.id for item, _ in self.lexical.search("cache", limit=2)}
        dense_ids = {item.id for item, _ in self.dense.search([1, 0], limit=2)}
        hits = retriever.search("cache", [1, 0])
        self.assertEqual(lexical_ids, {"exact", "agreement"})
        self.assertEqual(dense_ids, {"semantic", "agreement"})
        self.assertEqual([item.id for item, _ in hits], ["agreement", "exact", "semantic"])
        self.assertAlmostEqual(hits[0][1], 2 / 62)
        self.assertAlmostEqual(hits[1][1], 1 / 61)
        self.assertEqual({item.id for item, _ in hits}, lexical_ids | dense_ids)

    def test_fixed_fetch_depth_makes_every_k_a_prefix(self):
        retriever = HybridRetriever(self.lexical, self.dense, fetch_depth=2)
        all_hits = retriever.search("cache", [1, 0], limit=10)
        for limit in (1, 2, 3, 7):
            self.assertEqual(retriever.search("cache", [1, 0], limit=limit), all_hits[:limit])
        self.assertEqual(retriever.search("cache", [1, 0], limit=0), [])
        with self.assertRaises(ValueError):
            retriever.search("cache", [1, 0], limit=-1)

    def test_rank_fusion_ignores_score_scales(self):
        class Rescaled:
            def __init__(self, retriever, factor):
                self.retriever = retriever
                self.snippets = retriever.snippets
                self.factor = factor

            def search(self, query, limit):
                return [(item, score * self.factor)
                        for item, score in self.retriever.search(query, limit)]

        ordinary = HybridRetriever(self.lexical, self.dense, fetch_depth=2)
        rescaled = HybridRetriever(Rescaled(self.lexical, 1e8), Rescaled(self.dense, 1e-8), fetch_depth=2)
        self.assertEqual(ordinary.search("cache", [1, 0]), rescaled.search("cache", [1, 0]))

    def test_reordered_inputs_preserve_ties(self):
        first = HybridRetriever(self.lexical, self.dense, fetch_depth=2).search("cache", [1, 0])
        reversed_items = list(reversed(self.items))
        reversed_dense = DenseRetriever(reversed_items, [[0.8, 0.6], [1, 0], [0, 1]])
        second = HybridRetriever(BM25Retriever(reversed_items), reversed_dense, fetch_depth=2).search("cache", [1, 0])
        self.assertEqual(first, second)

    def test_rejects_duplicate_conflicting_ids_and_invalid_fusion_settings(self):
        duplicate = BM25Retriever([self.items[0], self.items[0]])
        with self.assertRaises(ValueError):
            HybridRetriever(duplicate, self.dense)
        conflict = DenseRetriever([snippet("exact", "different source")], [[1, 0]])
        with self.assertRaises(ValueError):
            HybridRetriever(self.lexical, conflict)
        for settings in ({"rrf_k": -1}, {"rrf_k": float("nan")},
                         {"rrf_k": float("inf")}, {"fetch_depth": 0},
                         {"fetch_depth": 1.5}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                HybridRetriever(self.lexical, self.dense, **settings)


if __name__ == "__main__":
    unittest.main()
