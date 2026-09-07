"""Index scope remains visible in exact-budget context without loading models."""
import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

from semantic_search.backend import SearchBackend
from semantic_search.context import (INDEX_MEMBERSHIP, apply_token_budget, compact_result,
                                     encoding, indexed_scope, serialize_context)
from semantic_search.source import physical_lines


class ScopeContextTests(unittest.TestCase):
    def test_scope_includes_only_bounded_complete_exclusions(self):
        patterns = ("tests/**", "x" * 81, *(f"src/ignore{i}/**" for i in range(8)))
        scope = indexed_scope("src/pkg", patterns)
        self.assertEqual(scope["membership"], INDEX_MEMBERSHIP)
        self.assertEqual(scope["source_exclusions"], ["tests/**", "src/ignore0/**", "src/ignore1/**", "src/ignore2/**"])
        self.assertEqual(scope["exclusions_omitted"], 6)
        self.assertTrue(all(pattern in patterns and len(pattern) <= 80 for pattern in scope["source_exclusions"]))

    def test_scope_is_counted_inside_token_cap_while_source_is_trimmed_truthfully(self):
        source = "def example():\n" + "    value = 'café 😀'\r\n" * 120 + "    return value\n"
        original = {"query": "Find the implementation", "scope": indexed_scope("src/pkg", ("src/pkg/tests/**",)),
                    "match_status": "candidates", "budget_exhausted": False,
                    "results": [{"path": "src/pkg/code.py", "symbol": "example", "start_line": 1,
                                 "end_line": len(physical_lines(source)), "source": source,
                                 "source_hash": hashlib.sha256(source.encode()).hexdigest(),
                                 "truncated": False, "origin": "retrieval", "related_to": None}]}
        result = apply_token_budget(original, 512)
        envelope = compact_result(result)
        actual = len(encoding().encode(serialize_context(envelope), disallowed_special=()))
        self.assertEqual(envelope["scope"], original["scope"])
        self.assertEqual(actual, result["token_usage"]["tokens"])
        self.assertLessEqual(actual, 512)
        self.assertTrue(result["token_usage"]["truncated"])
        row = result["results"][0]
        self.assertEqual(row["source"], "".join(physical_lines(source)[:row["end_line"]]))
        self.assertEqual(row["source_hash"], hashlib.sha256(row["source"].encode()).hexdigest())

    def test_actual_git_scope_excludes_untracked_and_outside_root_files(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "src/pkg").mkdir(parents=True)
            (repo / "tests").mkdir()
            for name, text in (("src/pkg/code.py", "def indexed():\n    return 'indexed'\n"),
                               ("src/pkg/untracked.py", "def untracked():\n    return 'untracked'\n"),
                               ("src/pkg/excluded.py", "def excluded():\n    return 'excluded'\n"),
                               ("tests/test_example.py", "def fixture():\n    return 'fixture'\n")):
                (repo / name).write_text(text)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "add", "src/pkg/code.py", "src/pkg/excluded.py", "tests/test_example.py"],
                           check=True, capture_output=True)
            backend = SearchBackend(repo, "src/pkg", db_path=repo / "index.sqlite3",
                                    source_exclusions=("src/pkg/excluded.py",))
            try:
                for query in ("indexed", "untracked", "excluded", "fixture"):
                    result = backend.search(query, retrieval="bm25", token_budget=512)
                    self.assertEqual(result["scope"], indexed_scope("src/pkg", ("src/pkg/excluded.py",)))
                    self.assertEqual(len(result["results"]), 1 if query == "indexed" else 0)
                    self.assertFalse(backend.status()["embedding_initialized"])
            finally:
                backend.close()

    def test_outside_git_policy_does_not_claim_a_git_checkout_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "code.py").write_text("def saved():\n    return 1\n")
            backend = SearchBackend(repo, ".", db_path=repo / "index.sqlite3")
            try:
                result = backend.search("saved", retrieval="bm25", token_budget=512)
                self.assertEqual(result["scope"]["source_root"], ".")
                self.assertEqual(result["scope"]["membership"], INDEX_MEMBERSHIP)
                self.assertEqual(len(result["results"]), 1)
            finally:
                backend.close()


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install the optional MCP extra")
class ScopeValidationTests(unittest.TestCase):
    def test_malformed_scope_is_rejected_by_strict_output_contract(self):
        from semantic_search.mcp_server import IndexedScope
        scope = indexed_scope("src/pkg")
        cases = [{"source_root": "/absolute"}, {"source_root": "../other"},
                 {"membership": "all files"}, {"source_exclusions": ["x" * 81]},
                 {"source_exclusions": ["valid"] * 5}, {"exclusions_omitted": -1},
                 {"exclusions_omitted": True}, {"unknown": "extra"}]
        for changed in cases:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                IndexedScope.model_validate({**scope, **changed})

    def test_scope_root_must_contain_the_returned_path(self):
        from semantic_search.mcp_server import SearchArguments, _validate_context
        source = "def example():\n    return 1\n"
        response = {"query": "Find implementation", "scope": indexed_scope("src/pkg"),
                    "match_status": "candidates", "budget_exhausted": False,
                    "results": [{"path": "tests/example.py", "symbol": "example", "start_line": 1,
                                 "end_line": 2, "source": source, "source_hash": hashlib.sha256(source.encode()).hexdigest(),
                                 "truncated": False, "origin": "retrieval", "related_to": None}]}
        response = apply_token_budget(response, 4096)
        with self.assertRaisesRegex(ValueError, "outside.*source root"):
            _validate_context(compact_result(response), SearchArguments(query=response["query"]))
