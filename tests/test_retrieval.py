import unittest

from semantic_search.corpus import Snippet
from semantic_search.retrieval import BM25Retriever, tokenize


def snippet(identifier, text, path="src/example.py", symbol="run"):
    return Snippet(identifier, path, symbol, 1, 2, text, "unused")


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


if __name__ == "__main__":
    unittest.main()
