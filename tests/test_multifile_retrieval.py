"""Freeze opt-in literal-clause retrieval independently of benchmark labels."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from semantic_search.corpus import Snippet
from semantic_search.persistent_retrieval import PersistentLexicalRetriever
from semantic_search.retrieval import MultifileRetriever, multifile_clauses
from semantic_search.store import SQLiteIndex


def snippet(identifier, *, symbol=None, path="src/code.py", text="return value"):
    return Snippet(identifier, path, symbol or identifier, 1, 2, text, "hash")


FIRST = "Normalize signed cookie data"
SECOND = "persist weak registry mappings"
QUERY = FIRST + ", then " + SECOND


class Ranked:
    def __init__(self, snippets, rankings):
        self.snippets, self.rankings = snippets, rankings
        self.calls = []

    def search(self, query, limit=10):
        self.calls.append((query, limit))
        ranking = self.rankings[query] if isinstance(self.rankings, dict) else self.rankings
        return ranking[:limit]


class MultifileRetrievalTests(unittest.TestCase):
    def make(self):
        items = [snippet(str(i)) for i in range(12)]
        dense = Ranked(items, [(item, 1 - index / 20) for index, item in enumerate(items[:8])])
        lexical = Ranked(items, {
            FIRST: [(items[index], 1000 - index) for index in (0, 8, 9, 10)],
            SECOND: [(items[index], 1000 - index) for index in (10, 11, 8, 9)],
        })
        return MultifileRetriever(lexical, dense), lexical, dense, items

    def test_literal_clause_split_is_bounded_and_requires_content(self):
        self.assertEqual(multifile_clauses(QUERY), (FIRST, SECOND))
        query = "alpha beta gamma AND delta epsilon zeta while eta theta iota, kappa lambda omega then theta rho sigma"
        self.assertEqual(multifile_clauses(query), (
            "alpha beta gamma", "delta epsilon zeta", "eta theta iota", "kappa lambda omega"))
        self.assertEqual(multifile_clauses("Find cookie storage"), ())
        self.assertEqual(multifile_clauses("find cookie storage and then return it"), ())
        self.assertEqual(multifile_clauses("the and this, where is it"), ())
        self.assertEqual(multifile_clauses("cookie cookie cookie and weak weak weak"), ())
        self.assertEqual(multifile_clauses("registry registries registry and weak weak weak"), ())

    def test_preserves_dense_three_and_interleaves_unique_clause_hits_then_backfills(self):
        retriever, lexical, dense, _ = self.make()
        vector = object()
        hits = retriever.search(QUERY, vector, limit=50)
        self.assertEqual([item.id for item, _ in hits],
                         ["0", "1", "2", "10", "8", "11", "9", "3", "4", "5", "6", "7"])
        self.assertEqual(dense.calls, [(vector, 50)])
        self.assertEqual(lexical.calls, [(FIRST, 50), (SECOND, 50)])
        self.assertEqual([score for _, score in hits], [1 / (60 + rank) for rank in range(1, 13)])

    def test_distinct_symbol_anchors_and_first_window_representatives_are_stable(self):
        first = snippet("first", symbol="same")
        later = replace(first, id="later", start_line=10, end_line=11)
        other, third, facet = snippet("other"), snippet("third"), snippet("facet")
        corpus = [first, later, other, third, facet]
        dense = Ranked(corpus, [(item, .9) for item in (first, later, other, third)])
        lexical = Ranked(corpus, {FIRST: [(later, 10), (facet, 9)], SECOND: []})
        hits = MultifileRetriever(lexical, dense).search(QUERY, object())
        self.assertEqual([item.id for item, _ in hits], ["first", "other", "third", "facet"])

    def test_output_limits_are_prefixes_of_the_same_fixed_depth_merge(self):
        retriever, lexical, dense, _ = self.make()
        vector = object()
        all_hits = retriever.search(QUERY, vector, limit=100)
        for limit in (1, 2, 3, 5, 8, 20):
            self.assertEqual(retriever.search(QUERY, vector, limit=limit), all_hits[:limit])
        self.assertTrue(all(limit == 50 for _, limit in lexical.calls + dense.calls))
        before = len(lexical.calls), len(dense.calls)
        self.assertEqual(retriever.search(QUERY, vector, limit=0), [])
        self.assertEqual((len(lexical.calls), len(dense.calls)), before)

    def test_merged_candidate_count_is_capped_at_fifty(self):
        items = [snippet(str(i)) for i in range(80)]
        dense = Ranked(items, [(item, 1) for item in items[:50]])
        lexical = Ranked(items, {FIRST: [(item, 1) for item in items[50:]],
                                 SECOND: [(item, 1) for item in items[60:]]})
        hits = MultifileRetriever(lexical, dense).search(QUERY, object(), limit=100)
        self.assertEqual(len(hits), 50)
        self.assertEqual([item.id for item, _ in hits[:3]], ["0", "1", "2"])
        self.assertEqual(len({(item.path, item.symbol) for item, _ in hits}), 50)

    def test_no_valid_clause_split_retains_dense_window_order_without_lexical_calls(self):
        first = snippet("first", symbol="same")
        later = replace(first, id="later", start_line=10, end_line=11)
        dense = Ranked([first, later], [(first, .75), (later, .6)])
        lexical = Ranked([first, later], {})
        hits = MultifileRetriever(lexical, dense).search("cookie storage", object())
        self.assertEqual(hits, [(first, 1 / 61), (later, 1 / 62)])
        self.assertEqual(lexical.calls, [])

    def test_scores_are_rank_priorities_and_independent_of_input_score_scales(self):
        first, _, _, _ = self.make()
        second, lexical, dense, _ = self.make()
        dense.rankings = [(item, score * 1e8) for item, score in dense.rankings]
        lexical.rankings = {query: [(item, score * 1e-8) for item, score in ranking]
                            for query, ranking in lexical.rankings.items()}
        self.assertEqual(first.search(QUERY, object()), second.search(QUERY, object()))

    def test_invalid_queries_limits_and_conflicting_corpora_fail(self):
        retriever, lexical, dense, _ = self.make()
        for query in (None, "", " \n", 42):
            with self.subTest(query=query), self.assertRaises(ValueError):
                retriever.search(query, object())
        for limit in (-1, 1.5, True, "8"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                retriever.search(QUERY, object(), limit=limit)
        self.assertEqual(lexical.calls + dense.calls, [])
        one = snippet("one")
        with self.assertRaisesRegex(ValueError, "unique snippet IDs"):
            MultifileRetriever(Ranked([one, one], []), Ranked([one], []))
        with self.assertRaisesRegex(ValueError, "same source window"):
            MultifileRetriever(Ranked([one], []), Ranked([replace(one, text="different")], []))

    def test_empty_corpus_and_empty_clause_hits_are_valid(self):
        lexical = Ranked([], {FIRST: [], SECOND: []})
        dense = Ranked([], [])
        self.assertEqual(MultifileRetriever(lexical, dense).search(QUERY, object()), [])
        retriever, lexical, _, _ = self.make()
        lexical.rankings = {FIRST: [], SECOND: []}
        self.assertEqual([item.id for item, _ in retriever.search(QUERY, object())], list(map(str, range(8))))

    def test_real_persistent_fts_adds_a_clause_match_beyond_dense_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            anchor = [snippet(f"anchor-{i}", text="return generic_value") for i in range(5)]
            cookie = snippet("cookie", text="normalize signed cookie data")
            registry = snippet("registry", text="persist weak registry mappings")
            store = SQLiteIndex(Path(directory) / "index.sqlite3")
            self.addCleanup(store.close)
            store.refresh_lexical([*anchor, cookie, registry])
            lexical = PersistentLexicalRetriever(store)
            dense = Ranked([*anchor, cookie, registry], [(item, .9) for item in anchor])
            hits = MultifileRetriever(lexical, dense).search(QUERY, object())
            self.assertEqual([item.id for item, _ in hits[:5]],
                             ["anchor-0", "anchor-1", "anchor-2", "cookie", "registry"])
            self.assertEqual([item.id for item, _ in hits[5:]], ["anchor-3", "anchor-4"])


if __name__ == "__main__":
    unittest.main()
