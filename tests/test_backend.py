import hashlib
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.corpus import extract_snippets
from semantic_search.cancellation import SearchCancelled


class FakeEmbedder:
    metadata = {"id": "test", "revision": "fixed", "dimensions": 2}

    def __init__(self):
        self.documents = []
        self.queries = []
        self.closed = False
        self.after_documents = self.after_query = None

    def embed_documents(self, documents):
        self.documents.append(list(documents))
        result = np.asarray([[1., .1 + ("beta" in text)] for text in documents]).reshape(-1, 2)
        if self.after_documents:
            self.after_documents()
        return result

    def embed_query(self, query):
        self.queries.append(query)
        if self.after_query:
            self.after_query()
        return np.asarray([1., 0.])

    def close(self):
        self.closed = True


class FakeH2O:
    def __init__(self):
        self.calls = []

    def score(self, query, snippet):
        self.calls.append(snippet.id)
        return SimpleNamespace(score=3. if snippet.symbol == "beta" else 1.)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        (self.repo / "src").mkdir()
        self.source = self.repo / "src/code.py"
        self.source.write_text("def alpha():\n    return 'alpha'\n\ndef beta():\n    return 'beta'\n")
        self.embedder, self.h2o = FakeEmbedder(), FakeH2O()
        self.backend = SearchBackend(self.repo, "src", self.repo / "index.sqlite3",
                                     embedder=self.embedder, h2o_client=self.h2o)

    def tearDown(self):
        self.backend.close()
        self.temporary.cleanup()

    def test_warm_search_reuses_parser_store_and_model(self):
        first = self.backend.search("implement alpha")
        with patch("semantic_search.backend.extract_snippets", side_effect=AssertionError("reparsed")):
            second = self.backend.search("implement alpha")
        self.assertFalse(first["indexstats"]["memory_cache_hit"])
        self.assertTrue(second["indexstats"]["memory_cache_hit"])
        self.assertEqual(len(self.embedder.documents), 1)
        self.assertEqual(len(self.embedder.queries), 2)
        self.assertFalse(self.h2o.calls)
        self.assertTrue(self.backend.status()["dense_ready"])
        self.assertEqual(first["results"][0]["source"], "def alpha():\n    return 'alpha'\n")
        self.assertIsNone(first["results"][0]["h2o_score"])

    def test_lexical_only_is_lazy_and_cannot_reuse_stale_dense_vectors(self):
        self.backend.search("alpha")
        self.source.write_text("def alpha():\n    return 'changed'\n")
        self.backend.search("changed", retrieval="bm25")
        self.assertFalse(self.backend.status()["dense_ready"])
        self.assertEqual(len(self.embedder.documents), 1)
        dense = self.backend.search("changed")
        self.assertEqual(len(self.embedder.documents), 2)
        self.assertIn("changed", dense["results"][0]["source"])
        with SearchBackendForTest(self.repo, "src") as lexical:
            with patch("semantic_search.backend.SearchBackend._get_embedder", side_effect=AssertionError("model used")):
                result = lexical.search("changed", retrieval="bm25")
            self.assertEqual(len(result["results"]), 1)
            self.assertFalse(lexical.status()["embedding_initialized"])

    def test_edits_deletes_and_renames_refresh_exact_locations(self):
        self.backend.search("alpha")
        self.source.write_text("def alpha():\n    return 'updated'\n")
        edited = self.backend.search("updated")
        self.assertEqual(edited["indexstats"]["indexed_windows"], 1)
        self.assertEqual(edited["indexstats"]["deleted_windows"], 1)
        renamed = self.repo / "src/renamed.py"
        self.source.rename(renamed)
        result = self.backend.search("updated")
        self.assertEqual(result["results"][0]["path"], "src/renamed.py")
        self.assertEqual(result["indexstats"]["total_windows"], 1)
        renamed.unlink()
        empty = self.backend.search("updated")
        self.assertEqual(empty["results"], [])
        self.assertEqual(empty["indexstats"]["deleted_windows"], 1)
        self.assertEqual(self.backend.status()["store"]["total_windows"], 0)

    def test_git_ref_and_tracked_membership_change_snapshot(self):
        def git(*args):
            return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()
        git("init", "-q")
        git("add", "src/code.py")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial")
        self.backend.search("alpha")
        initial = self.backend.status()["source_manifest"]
        git("checkout", "-qb", "other")
        switched = self.backend.search("alpha")
        self.assertNotEqual(initial, self.backend.status()["source_manifest"])
        self.assertEqual(switched["indexstats"]["indexed_windows"], 0)
        (self.repo / "src/untracked.py").write_text("def omitted():\n    return 4\n")
        self.assertEqual(self.backend.search("alpha")["indexstats"]["total_windows"], 2)
        git("add", "src/untracked.py")
        self.assertEqual(self.backend.search("alpha")["indexstats"]["total_windows"], 3)

    def test_syntax_errors_and_racing_edits_reject_stale_results(self):
        self.backend.search("alpha")
        previous_store = self.backend.status()["store"]
        self.source.write_text("def broken(\n")
        with self.assertRaisesRegex(SourceSnapshotError, "Cannot parse"):
            self.backend.search("alpha")
        self.assertFalse(self.backend.status()["ready"])
        self.source.write_text("def changed():\n    return 1\n")
        self.embedder.after_documents = lambda: self.source.write_text("def changed():\n    return 2\n")
        with self.assertRaisesRegex(SourceSnapshotError, "Source changed"):
            self.backend.search("changed")
        self.assertEqual(self.backend.status()["store"], previous_store)
        self.embedder.after_documents = None
        result = self.backend.search("changed")
        self.assertIn("return 2", result["results"][0]["source"])
        self.embedder.after_query = lambda: self.source.write_text("def changed():\n    return 3\n")
        with self.assertRaisesRegex(SourceSnapshotError, "Source changed"):
            self.backend.search("changed")

    def test_rerank_scores_only_prefix_and_does_not_append_tail(self):
        result = self.backend.search("implementation", rerank=True, candidates=2, max_results=5)
        self.assertEqual(result["results"][0]["symbol"], "beta")
        self.assertEqual(result["results"][0]["h2o_score"], 3.)
        self.assertEqual(len(self.h2o.calls), 2)
        self.assertEqual(len(result["results"]), 2)
        self.h2o.calls.clear()
        prefix = self.backend.search("implementation", rerank=True, candidates=1, max_results=5)
        self.assertEqual(len(self.h2o.calls), 1)
        self.assertEqual(len(prefix["results"]), 1)

    def test_extraction_uses_captured_source_despite_temporary_header_and_membership_changes(self):
        original = "class C:\n    def method(self):\n        return 1\n"
        self.source.write_text(original)
        other = self.repo / "src/other.py"
        other_source = "def retained():\n    return 2\n"
        other.write_text(other_source)
        def transient(repo, source_root, **kwargs):
            self.source.write_text(original.replace("class C", "class D"))
            other.unlink()
            try:
                return extract_snippets(repo, source_root, **kwargs)
            finally:
                self.source.write_text(original)
                other.write_text(other_source)
        with patch("semantic_search.backend.extract_snippets", side_effect=transient):
            result = self.backend.search("implementation")
        self.assertEqual({r["symbol"] for r in result["results"]}, {"C.method", "retained"})
        self.assertEqual(result["indexstats"]["total_windows"], 2)

    def test_source_budget_is_hard_whole_line_and_hashes_are_truthful(self):
        full = self.backend.search("alpha")["results"][0]
        budget = len("def alpha():\n")
        partial = self.backend.search("alpha", source_budget=budget, max_results=1)
        result = partial["results"][0]
        self.assertEqual(result["source"], "def alpha():\n")
        self.assertEqual(result["end_line"], 1)
        self.assertEqual(result["window_end_line"], 2)
        self.assertEqual(result["content_hash"], full["content_hash"])
        self.assertEqual(result["source_hash"], hashlib.sha256(result["source"].encode()).hexdigest())
        self.assertTrue(result["truncated"])
        self.assertTrue(partial["budget_exhausted"])
        self.assertEqual(partial["total_source_chars"], budget)
        empty = self.backend.search("alpha", source_budget=0)
        self.assertEqual(empty["results"], [])
        self.assertEqual(empty["total_source_chars"], 0)
        self.assertTrue(empty["budget_exhausted"])

    def test_validation_and_closed_lifecycle(self):
        cases = [{"query": " "}, {"query": 4}, {"query": "\ud800"}, {"query": "x" * 8193},
                 {"retrieval": "other"}, {"rerank": 1}, {"candidates": True},
                 {"candidates": 0}, {"candidates": 51}, {"max_results": 21},
                 {"source_budget": -1}, {"source_budget": 100001}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.backend.search(**{"query": "alpha", **case})
        self.backend.close()
        self.backend.close()
        self.assertTrue(self.embedder.closed)
        self.assertTrue(self.backend.status()["closed"])
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.backend.search("alpha")

    def test_gpu_work_is_serialized_across_concurrent_searches(self):
        self.backend.refresh()
        active, peak = 0, 0
        guard = threading.Lock()
        def query_hook():
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(.02)
            with guard:
                active -= 1
        self.embedder.after_query = query_hook
        failures = []
        def search():
            try:
                self.backend.search("alpha")
            except Exception as error:
                failures.append(error)
        threads = [threading.Thread(target=search) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(failures)
        self.assertEqual(peak, 1)

    def test_default_database_is_namespaced_by_source_root(self):
        first = SearchBackend(self.repo, "src")
        second = SearchBackend(self.repo, ".")
        self.assertNotEqual(first.db_path, second.db_path)
        first.close()
        second.close()

    def test_changed_model_metadata_invalidates_warm_vectors(self):
        first = self.backend.search("alpha")
        self.embedder.metadata = {**self.embedder.metadata, "revision": "updated"}
        changed = self.backend.search("alpha")
        self.assertFalse(changed["indexstats"]["memory_cache_hit"])
        self.assertNotEqual(first["indexstats"]["model_fingerprint"], changed["indexstats"]["model_fingerprint"])
        self.assertEqual(len(self.embedder.documents), 2)

    def test_remote_or_credentialed_h2o_urls_are_rejected(self):
        for url in ("https://127.0.0.1:8741", "http://example.com", "http://user@localhost:8741",
                    "http://localhost:8741/other", "http://localhost:8741?x=1", "http://localhost:bad"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "loopback"):
                SearchBackend(self.repo, "src", h2o_url=url)

    def test_token_budget_counts_exact_tool_text_and_physical_unicode_lines(self):
        from semantic_search.context import compact_result, serialize_context, encoding
        self.source.write_text("def alpha():\n" + "    value = 'café\\n雪\u2028tail'\n" * 120 + "    return value\n")
        result = self.backend.search("alpha", token_budget=512, max_results=1)
        serialized = serialize_context(compact_result(result))
        tokens = len(encoding().encode(serialized, disallowed_special=()))
        self.assertEqual(tokens, result["token_usage"]["tokens"])
        self.assertLessEqual(tokens, 512)
        self.assertTrue(result["token_usage"]["truncated"])
        row = result["results"][0]
        from semantic_search.source import physical_lines
        self.assertEqual(row["source"], "".join(physical_lines(self.source.read_text())[row["start_line"]-1:row["end_line"]]))

    def test_saved_edit_before_watcher_start_is_not_missed(self):
        self.backend.refresh()
        self.source.write_text("def alpha():\n    return 'new saved value'\n")
        self.backend.start_watching(poll_interval=.01, debounce=.01)
        deadline = time.monotonic() + 3
        while self.backend.status()["watcher"]["refresh_count"] == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        status = self.backend.status()
        self.assertGreaterEqual(status["watcher"]["refresh_count"], 1)
        self.assertEqual(status["windows"], 1)
        self.assertIn("new saved value", self.backend.search("new value")["results"][0]["source"])
        self.backend.close()
        self.assertFalse(self.backend.status()["watcher"]["running"])

    def test_cancelled_index_build_keeps_previous_atomic_snapshot(self):
        self.backend.refresh()
        previous = self.backend.status()["store"]
        self.source.write_text("def gamma():\n    return 'gamma'\n")
        self.embedder.after_documents = lambda: self.backend.cancel("index-request")
        with self.assertRaises(SearchCancelled):
            self.backend.search("gamma", request_id="index-request")
        self.assertEqual(self.backend.status()["store"], previous)
        self.embedder.after_documents = None
        self.assertEqual(self.backend.search("gamma")["results"][0]["symbol"], "gamma")

    def test_queued_request_cancels_before_entering_model_work(self):
        self.backend.refresh()
        failures = []
        with self.backend._lock:
            def worker():
                try:
                    self.backend.search("alpha", request_id="queued")
                except Exception as error:
                    failures.append(error)
            thread = threading.Thread(target=worker)
            thread.start()
            deadline = time.monotonic() + 2
            while "queued" not in self.backend._requests.active and time.monotonic() < deadline:
                time.sleep(.01)
            self.backend.cancel("queued")
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.assertIsInstance(failures[0], SearchCancelled)
        self.assertEqual(self.embedder.queries, [])

    def test_cross_file_context_is_marked_without_reranker_score(self):
        self.source.write_text("from helpers import decode\n\ndef alpha():\n    return decode('value')\n")
        (self.repo / "src/helpers.py").write_text("def decode(value):\n    return value.upper()\n")
        # Force just one retrieved anchor to exercise expansion independently
        # of whether the second implementation also matches lexical terms.
        result = self.backend.search("alpha", retrieval="bm25", include_dependencies=True, max_results=8)
        dependency = next(row for row in result["results"] if row["origin"] == "dependency")
        self.assertEqual(dependency["path"], "src/helpers.py")
        self.assertEqual(dependency["symbol"], "decode")
        self.assertIsNone(dependency["h2o_score"])
        self.assertIn("alpha", dependency["related_to"])


class SearchBackendForTest(SearchBackend):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == "__main__":
    unittest.main()
