import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np

from semantic_search.corpus import Snippet
from semantic_search.index import document_text
from semantic_search.persistent_retrieval import PersistentDenseRetriever, PersistentLexicalRetriever
from semantic_search.retrieval import DenseRetriever, HybridRetriever
from semantic_search.store import SQLiteIndex


def snippet(identifier, text, symbol=None, path=None):
    return Snippet(identifier, path or f"src/{identifier}.py", symbol or identifier,
                   1, 2, text, "deliberately-stale-hash")


class FixedEmbedder:
    def __init__(self, vectors, revision="one"):
        self.vectors = vectors
        self.metadata = {"dimensions": len(vectors[0]), "revision": revision}
        self.calls = []

    def embed_documents(self, documents):
        self.calls.append(documents)
        if len(documents) != len(self.vectors):
            raise RuntimeError("Unexpected fixture document count")
        return np.array(self.vectors)


class PersistentRetrievalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "index.sqlite3"
        self.store = SQLiteIndex(self.path)

    def test_reopen_fts_matches_identifiers_symbols_paths_and_literal_terms(self):
        items = [snippet("http", "return transport.read()", symbol="getHTTPResponse"),
                 snippet("snake", "return cache.pop(key)", symbol="remove_cache_entry"),
                 snippet("other", "return 1", path="src/webhook_dispatch.py")]
        self.store.refresh_lexical(items)
        reopened = SQLiteIndex(self.path)
        for query, expected in (("httpResponse", "http"), ("removeCacheEntry", "snake"),
                                ("webhook_dispatch", "other"), ("transport", "http")):
            with self.subTest(query=query):
                self.assertEqual(reopened.search_lexical(query, 1)[0][0].id, expected)
        self.assertEqual(reopened.snippets, items)
        self.assertEqual(reopened.search_lexical('" OR * - :'), [])
        self.assertEqual(reopened.search_lexical("http' OR 1=1 --", 10)[0][0].id, "http")
        self.assertEqual(reopened.status()["dense_windows"], 0)
        self.assertEqual(reopened.search_dense([1, 0]), [])

    def test_exact_cosine_reopens_matches_reference_and_has_deterministic_prefixes(self):
        random = np.random.default_rng(1327)
        vectors = random.normal(size=(31, 12))
        vectors[0], vectors[1] = np.ones(12), np.ones(12)
        # Input order puts the larger tie ID first.
        items = [snippet(f"item-{30 - i:02d}", f"return {i}") for i in range(31)]
        query = np.ones(12)
        self.store.refresh(items, FixedEmbedder(vectors))
        reopened = SQLiteIndex(self.path)
        expected = DenseRetriever(items, vectors).search(query, 31)
        actual = reopened.search_dense(query, 31)
        self.assertEqual([item.id for item, _ in actual], [item.id for item, _ in expected])
        np.testing.assert_allclose([score for _, score in actual], [score for _, score in expected], atol=2e-7)
        for k in (1, 3, 9, 31, 50):
            self.assertEqual(reopened.search_dense(query, k), actual[:k])
        self.assertEqual(reopened.search_dense(query * 1e300), actual[:10])
        self.assertEqual(reopened.search_dense(query * 1e-300), actual[:10])

    def test_deleted_renamed_branch_entries_are_filtered_and_old_vectors_reused(self):
        old = snippet("old", "return obsolete_implementation()")
        original = FixedEmbedder([[1, 0]])
        self.store.refresh([old], original)
        renamed = replace(old, id="renamed", path="src/new_path.py", text="return replacement()")
        self.store.refresh([renamed], FixedEmbedder([[0, 1]]))
        self.assertEqual([item.id for item, _ in self.store.search_dense([1, 0])], ["renamed"])
        self.assertEqual(self.store.search_lexical("obsolete"), [])
        self.assertEqual(self.store.search_lexical("replacement")[0][0], renamed)
        _, stats = self.store.refresh([old], original)
        self.assertEqual((stats["indexed_windows"], stats["reused_windows"]), (0, 1))
        self.assertEqual(len(original.calls), 1)
        self.assertEqual(self.store.search_dense([1, 0])[0][0], old)
        self.assertEqual(self.store.search_lexical("replacement"), [])
        self.store.refresh([], original)
        self.assertEqual(self.store.search_dense([1, 0]), [])
        self.assertEqual(self.store.search_lexical("obsolete"), [])

    def test_model_and_dimension_change_reset_active_dense_but_keep_old_cache(self):
        item = snippet("run", "return call()")
        first = FixedEmbedder([[1, 0]])
        self.store.refresh([item], first)
        _, changed = self.store.refresh([item], FixedEmbedder([[0, 0, 1]], revision="two"))
        self.assertEqual(changed["indexed_windows"], 1)
        self.assertEqual(self.store.status()["dimensions"], 3)
        self.assertAlmostEqual(self.store.search_dense([0, 0, 1])[0][1], 1)
        with self.assertRaisesRegex(ValueError, "shape"):
            self.store.search_dense([1, 0])
        _, restored = self.store.refresh([item], first)
        self.assertEqual(restored["reused_windows"], 1)
        self.assertAlmostEqual(self.store.search_dense([1, 0])[0][1], 1)

    def test_fts_ties_and_limit_prefixes_are_stable_after_reorder_and_reopen(self):
        items = [snippet("z", "return keyword()"), snippet("a", "return keyword()")]
        self.store.refresh_lexical(items)
        full = self.store.search_lexical("keyword", 10)
        self.assertEqual([item.id for item, _ in full], ["a", "z"])
        self.assertEqual(full[0][1], full[1][1])
        self.assertEqual(self.store.search_lexical("keyword", 1), full[:1])
        self.store.refresh_lexical(items[::-1])
        self.assertEqual(SQLiteIndex(self.path).search_lexical("keyword"), full)

    def test_search_readers_keep_old_complete_snapshot_while_inference_runs(self):
        old = snippet("old", "return previous()")
        self.store.refresh([old], FixedEmbedder([[1, 0]]))
        entered, proceed = threading.Event(), threading.Event()

        class BlockingEmbedder(FixedEmbedder):
            def embed_documents(self, documents):
                entered.set()
                if not proceed.wait(2):
                    raise RuntimeError("fixture stalled")
                return super().embed_documents(documents)

        new = snippet("new", "return replacement()")
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.store.refresh, [new], BlockingEmbedder([[0, 1]]))
            try:
                self.assertTrue(entered.wait(2))
                reopened = SQLiteIndex(self.path)
                self.assertEqual(reopened.search_dense([1, 0])[0][0], old)
                self.assertEqual(reopened.search_lexical("previous")[0][0], old)
                self.assertEqual(reopened.search_lexical("replacement"), [])
            finally:
                proceed.set()
            pending.result(timeout=2)
        self.assertEqual(self.store.search_dense([0, 1])[0][0], new)
        self.assertEqual(self.store.search_lexical("previous"), [])

    def test_sql_write_failure_rolls_back_fts_vectors_source_metadata_and_cache(self):
        old = snippet("old", "return previous()")
        self.store.refresh([old], FixedEmbedder([[1, 0]]))
        status = self.store.status()
        with sqlite3.connect(self.path) as connection:
            connection.execute("""CREATE TRIGGER reject_new BEFORE INSERT ON snippets
                WHEN NEW.id='broken' BEGIN SELECT RAISE(ABORT, 'blocked fixture'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "blocked fixture"):
            self.store.refresh([snippet("broken", "return changed()")], FixedEmbedder([[0, 1]]))
        reopened = SQLiteIndex(self.path)
        self.assertEqual(reopened.status(), status)
        self.assertEqual(reopened.snippets, [old])
        self.assertEqual(reopened.search_dense([1, 0])[0][0], old)
        self.assertEqual(reopened.search_lexical("previous")[0][0], old)
        self.assertEqual(reopened.search_lexical("changed"), [])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0], 1)

    def test_lexical_refresh_before_commit_failure_preserves_dense_and_fts(self):
        old = snippet("old", "return previous()")
        self.store.refresh([old], FixedEmbedder([[1, 0]]))
        status = self.store.status()

        def cancel():
            raise RuntimeError("cancelled")

        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.store.refresh_lexical([snippet("new", "return changed()")], before_commit=cancel)
        self.assertEqual(self.store.status(), status)
        self.assertEqual(self.store.search_dense([1, 0])[0][0], old)
        self.assertEqual(self.store.search_lexical("previous")[0][0], old)
        self.store.refresh_lexical([old])
        self.assertEqual(self.store.search_dense([1, 0]), [])
        _, reused = self.store.refresh([old], FixedEmbedder([[1, 0]]))
        self.assertEqual(reused["reused_windows"], 1)

    def test_adapters_support_existing_hybrid_and_validation(self):
        items = [snippet("lexical", "return keyword()"), snippet("dense", "return another()")]
        self.store.refresh(items, FixedEmbedder([[0, 1], [1, 0]]))
        hybrid = HybridRetriever(PersistentLexicalRetriever(self.store), PersistentDenseRetriever(self.store))
        actual = hybrid.search("keyword", [1, 0], limit=2)
        self.assertEqual({item.id for item, _ in actual}, {"lexical", "dense"})
        for limit in (-1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.search_dense([1, 0], limit)
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.search_lexical("keyword", limit)
        for query in ([0, 0], [np.inf, 0], [np.nan, 1], [[1, 0]], [1j, 1], [1]):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.store.search_dense(query)
        self.assertEqual(self.store.search_dense([1, 0], 0), [])
        self.assertEqual(self.store.search_lexical("keyword", 0), [])

    def test_schema_one_migration_keeps_source_cache_and_populates_both_retrievals(self):
        path = self.path.with_name("legacy.sqlite3")
        item = snippet("legacy", "return legacy_method()")
        metadata = {"dimensions": 2, "revision": "legacy"}
        fingerprint = hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        document_hash = hashlib.sha256(document_text(item).encode()).hexdigest()
        with sqlite3.connect(path) as connection:
            connection.executescript("""
                CREATE TABLE embeddings(model_fingerprint TEXT,document_hash TEXT,dimensions INTEGER,vector BLOB,
                    PRIMARY KEY(model_fingerprint,document_hash));
                CREATE TABLE snippets(id TEXT PRIMARY KEY,position INTEGER,path TEXT,symbol TEXT,start_line INTEGER,
                    end_line INTEGER,text TEXT,content_hash TEXT,document_hash TEXT,model_fingerprint TEXT NOT NULL);
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
                PRAGMA user_version=1;
            """)
            connection.execute("INSERT INTO embeddings VALUES (?,?,?,?)",
                               (fingerprint, document_hash, 2, np.array([1., 0.], dtype="<f8").tobytes()))
            connection.execute("INSERT INTO snippets VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (item.id, 0, item.path, item.symbol, 1, 2, item.text, item.content_hash,
                                document_hash, fingerprint))
            connection.execute("INSERT INTO metadata VALUES ('active',?)", (json.dumps({
                "model_fingerprint": fingerprint, "embedding": metadata, "dimensions": 2,
                "updated_at": "legacy"}),))
        migrated = SQLiteIndex(path)
        self.assertEqual(migrated.snippets, [item])
        self.assertEqual(migrated.search_lexical("legacy_method")[0][0], item)
        self.assertEqual(migrated.search_dense([1, 0])[0][0], item)
        model = FixedEmbedder([[1, 0]], revision="legacy")
        _, stats = migrated.refresh([item], model)
        self.assertEqual(stats["reused_windows"], 1)
        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()
