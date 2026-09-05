"""Independent, source-neutral JS/TS review fixtures; no model or query labels."""
from __future__ import annotations

import builtins
import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.corpus import extract_snippets
from semantic_search.languages import extractor_fingerprint
from semantic_search.relations import ReferenceGraph
from semantic_search.source import physical_lines


PARSERS_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in
                        ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript"))


class ReviewEmbedder:
    """Deterministic CPU vectors and an observable document-call boundary."""

    def __init__(self):
        self.metadata = {"id": "independent-cpu-review", "revision": "one", "dimensions": 3}
        self.document_batches = []
        self.after_documents = None

    def embed_documents(self, documents):
        self.document_batches.append(list(documents))
        values = []
        for document in documents:
            digest = hashlib.sha256(document.encode()).digest()
            vector = np.array([1., 1 + digest[0], 1 + digest[1]], dtype=float)
            values.append(vector / np.linalg.norm(vector))
        if self.after_documents:
            self.after_documents()
        return np.array(values).reshape(-1, 3)

    def embed_query(self, query):
        return np.array([1., 0., 0.])

    def close(self):
        pass


class SourceNeutralReview(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name).resolve()
        (self.repo / "src").mkdir()

    def extract(self, sources, **kwargs):
        return extract_snippets(self.repo, "src", sources=sources,
                                max_lines=160, overlap_lines=40, **kwargs)

    def graph(self, sources):
        snippets = self.extract(sources)
        return snippets, ReferenceGraph(sources, snippets)

    def assert_exact(self, sources, snippets):
        for item in snippets:
            lines = physical_lines(sources[item.path])
            self.assertGreaterEqual(item.start_line, 1)
            self.assertGreaterEqual(item.end_line, item.start_line)
            self.assertLessEqual(item.end_line, len(lines))
            self.assertEqual(item.text, "".join(lines[item.start_line - 1:item.end_line]))
            self.assertEqual(item.content_hash, hashlib.sha256(item.text.encode()).hexdigest())

class PythonLegacyReview(SourceNeutralReview):
    def test_legacy_python_encoding_decorators_and_docstring_window_contract(self):
        path = self.repo / "src/legacy.py"
        path.write_bytes(("# coding: latin-1\r\n@decorate\r\ndef café(value):\r\n"
                          "    \"\"\"Documentation only.\"\"\"\r\n    return value + 1\r\n").encode("latin-1"))
        imported = builtins.__import__

        def forbid_optional_parsers(name, *args, **kwargs):
            if name.startswith(("tree_sitter", "semantic_search.javascript")):
                raise AssertionError("Python extraction loaded an optional language parser")
            return imported(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=forbid_optional_parsers):
            snippets = extract_snippets(self.repo, "src", max_lines=160, overlap_lines=40)
        self.assertEqual([(item.symbol, item.start_line, item.end_line) for item in snippets],
                         [("café", 2, 5)])
        self.assert_exact({"src/legacy.py": path.read_bytes().decode("latin-1")}, snippets)
        self.assertIn('Documentation only.', snippets[0].text)


@unittest.skipUnless(PARSERS_AVAILABLE, "Install the pinned languages extra")
class IndependentExtractionReview(SourceNeutralReview):
    def test_multibyte_offsets_mixed_newlines_and_unicode_separator_are_exact(self):
        source = ("\ufeffconst π = '🪁';\r\n// emoji before declaration 🦉\r"
                  "export function calculate(value: number) {\n"
                  "  const text = 'left\u2028middle\u2029right';\r\n"
                  "  return value + text.length;\r}\n")
        sources = {"src/unicode.mts": source}
        snippets = self.extract(sources)
        self.assert_exact(sources, snippets)
        function = next(item for item in snippets if item.symbol == "calculate")
        self.assertEqual((function.start_line, function.end_line), (3, 6))
        self.assertEqual(len(physical_lines(function.text)), 4)
        self.assertIn("\u2028middle\u2029", function.text)

    def test_full_captured_source_controls_symbols_and_membership_during_live_aba(self):
        path = self.repo / "src/captured.ts"
        original = "class Original {\n  method(){return 1;}\n}\nfunction retained(){return 2;}\n"
        path.write_text("class Temporary {\n  method(){return 1;}\n}\n")
        with patch.object(Path, "read_bytes", side_effect=AssertionError("Captured source reread")):
            snippets = self.extract({"src/captured.ts": original})
        self.assertEqual([item.symbol for item in snippets], ["Original.method", "retained"])
        self.assert_exact({"src/captured.ts": original}, snippets)

    def test_parser_revision_mismatch_fails_instead_of_accepting_changed_grammar(self):
        from semantic_search import javascript
        real_version = javascript.version
        with patch.object(javascript, "version",
                          side_effect=lambda name: "999.0" if name == "tree-sitter" else real_version(name)):
            with self.assertRaisesRegex(ValueError, "requires"):
                self.extract({"src/example.js": "function example(){return 1;}"})

    def test_export_unicode_line_terminators_preserve_physical_source(self):
        for separator in ("\u2028", "\u2029"):
            source = f"export{separator}function exported(){{return 1;}}\n"
            sources = {"src/exported.mjs": source,
                       "src/caller.mjs": "import {exported} from './exported.mjs'; function call(){return exported();}"}
            with self.subTest(separator=separator):
                snippets, graph = self.graph(sources)
                self.assert_exact(sources, snippets)
                self.assertEqual(graph.edges[("src/caller.mjs", "call")], [("src/exported.mjs", "exported")])
                exported = next(item for item in snippets if item.symbol == "exported")
                self.assertEqual((exported.start_line, exported.end_line), (1, 1))
                self.assertEqual(exported.text, source)

    def test_export_trivia_repair_never_accepts_export_inside_function(self):
        for extension in (".js", ".ts"):
            with self.subTest(extension=extension), self.assertRaises(SyntaxError):
                self.extract({"src/invalid" + extension: "function outer(){export\nconst inner=()=>1;}"})

    def test_same_line_distinct_blocks_have_unique_implementation_ids(self):
        sources = {"src/compact.ts": "function run(){if(a){const helper=()=>1; helper();}else{const helper=()=>2; helper();}}"}
        snippets = self.extract(sources)
        helpers = [item for item in snippets if item.symbol.endswith(".helper")]
        self.assertEqual(len(helpers), 2)
        self.assertEqual(len({item.symbol for item in helpers}), 2)
        self.assertEqual(len({item.id for item in snippets}), len(snippets))
        self.assert_exact(sources, snippets)


@unittest.skipUnless(PARSERS_AVAILABLE, "Install the pinned languages extra")
class IndependentReferencesReview(SourceNeutralReview):
    def test_commonjs_escaped_export_objects_are_unresolved(self):
        mutations = [
            "const alias=exports; alias.fn=()=>2;",
            "const alias=module; alias.exports={fn:()=>2};",
            "const holder={api:exports}; holder.api.fn=()=>2;",
            "function expose(){return exports;} expose().fn=()=>2;",
            "outside.reference=module.exports; outside.reference.fn=()=>2;",
        ]
        for mutation in mutations:
            sources = {"src/helper.cjs": "function local(){return 1;} exports.fn=local; " + mutation,
                       "src/main.cjs": "const {fn}=require('./helper.cjs'); function run(){return fn();}"}
            with self.subTest(mutation=mutation):
                _, graph = self.graph(sources)
                self.assertNotIn(("src/main.cjs", "run"), graph.edges)

    def test_mutated_named_default_export_does_not_resolve_original_body(self):
        sources = {"src/helper.mjs": "export default function implementation(){return 1;} implementation=()=>2;",
                   "src/main.mjs": "import execute from './helper.mjs'; function run(){return execute();}"}
        _, graph = self.graph(sources)
        self.assertNotIn(("src/main.mjs", "run"), graph.edges)
        sources["src/helper.mjs"] = "export default function implementation(){return 1;}"
        _, graph = self.graph(sources)
        self.assertEqual(graph.edges[("src/main.mjs", "run")], [("src/helper.mjs", "implementation")])

    def test_switch_lexical_scope_does_not_hide_outer_function_after_switch(self):
        sources = {"src/scope.ts": "function helper(){return 1;} function run(){switch(value){case 1: let helper=unknown; helper(); break;} return helper();}"}
        _, graph = self.graph(sources)
        self.assertEqual(graph.edges[("src/scope.ts", "run")], [("src/scope.ts", "helper")])

    def test_distinct_blocks_link_only_their_own_exact_helper(self):
        source = ("function run(){if(flag){const helper=()=>1; function first(){return helper();}}else{\n"
                  "const helper=()=>2; function second(){return helper();}\nreturn second();}}")
        snippets, graph = self.graph({"src/scope.ts": source})
        first = next(item for item in snippets if item.symbol.endswith(".first"))
        second = next(item for item in snippets if item.symbol.endswith(".second"))
        first_dependency = graph.dependencies(first, limit=1)
        second_dependency = graph.dependencies(second, limit=1)
        self.assertEqual(len(first_dependency), 1)
        self.assertEqual(len(second_dependency), 1)
        self.assertNotEqual(first_dependency[0].symbol, second_dependency[0].symbol)
        self.assertEqual(first_dependency[0].start_line, 1)
        self.assertEqual(second_dependency[0].start_line, 2)
        self.assertIn("helper=()=>2", second_dependency[0].text)

    def test_with_object_dynamic_lookup_is_unresolved_inside_and_lexical_outside(self):
        sources = {"src/dynamic.cjs": "function helper(){return 1;} function dynamic(other){with(other){return helper();}} function normal(){return helper();}"}
        _, graph = self.graph(sources)
        self.assertNotIn(("src/dynamic.cjs", "dynamic"), graph.edges)
        self.assertEqual(graph.edges[("src/dynamic.cjs", "normal")], [("src/dynamic.cjs", "helper")])

    def test_parameter_catch_loop_and_destructuring_shadows_stay_local(self):
        for declaration in (
                "function parameter({value:helper}:{value:any}){return helper();}",
                "function rest(...helper:any[]){return helper();}",
                "function assigned(){({value:helper}=object); return helper();}"):
            with self.subTest(declaration=declaration):
                _, graph = self.graph({"src/scopes.ts": "function helper(){return 1;}\n" + declaration})
                self.assertEqual(dict(graph.edges), {})
        source = ("function helper(){return 1;}\n"
                  "function scoped(){for(const helper of values){helper();} try{throw object;}catch({helper}){helper();} return helper();}\n")
        _, graph = self.graph({"src/scopes.ts": source})
        self.assertEqual(dict(graph.edges), {("src/scopes.ts", "scoped"): [("src/scopes.ts", "helper")]})


@unittest.skipUnless(PARSERS_AVAILABLE, "Install the pinned languages extra")
class IndependentLifecycleReview(SourceNeutralReview):
    def setUp(self):
        super().setUp()
        self.embedder = ReviewEmbedder()
        self.db = self.repo / "review-index.sqlite3"
        self.write("src/alpha.ts", "export function alpha(){\n return 'alpha';\n}\n")
        self.write("src/beta.cjs", "function beta(){return 'beta';}\n")
        self.write("src/legacy.py", "def legacy():\n    return 'legacy'\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "src")
        self.git("-c", "user.name=CPU Fixture", "-c", "user.email=cpu@example.invalid", "commit", "-qm", "initial")
        self.backend = self.new_backend()

    def write(self, relative, source):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8", newline="")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def new_backend(self):
        backend = SearchBackend(self.repo, "src", self.db, embedder=self.embedder)
        self.addCleanup(backend.close)
        return backend

    def test_saved_edit_rename_delete_reopen_and_branch_reuse_exact_documents(self):
        initial = self.backend.refresh()
        initial_manifest = initial["source_manifest"]
        self.assertEqual((initial["indexed_windows"], initial["reused_windows"]), (3, 0))
        self.assertEqual(len(self.embedder.document_batches[0]), 3)
        self.write("src/scratch.js", "function unfinished( {")  # Untracked is outside membership.
        self.assertTrue(self.backend.refresh()["memory_cache_hit"])
        self.write("src/alpha.ts", "export function alpha(){\n return 'saved edit';\n}\n")
        edited = self.backend.refresh()
        self.assertEqual((edited["indexed_windows"], edited["reused_windows"]), (1, 2))
        self.assertEqual(len(self.embedder.document_batches[-1]), 1)
        self.git("mv", "src/beta.cjs", "src/moved.cjs")
        renamed = self.backend.refresh()
        self.assertEqual((renamed["indexed_windows"], renamed["reused_windows"]), (1, 2))
        self.assertIn("Path: src/moved.cjs", self.embedder.document_batches[-1][0])
        (self.repo / "src/alpha.ts").unlink()
        deleted = self.backend.refresh()
        self.assertEqual((deleted["indexed_windows"], deleted["reused_windows"]), (0, 2))
        self.assertEqual(deleted["deleted_windows"], 1)
        self.git("add", "-u", "src")
        self.git("-c", "user.name=CPU Fixture", "-c", "user.email=cpu@example.invalid", "commit", "-qm", "saved changes")
        self.git("branch", "changed")
        self.backend.close()
        self.backend = self.new_backend()
        batches = len(self.embedder.document_batches)
        reopened = self.backend.refresh()
        self.assertEqual((reopened["indexed_windows"], reopened["reused_windows"]), (0, 2))
        self.assertEqual(len(self.embedder.document_batches), batches)
        self.git("checkout", "-q", "main~1")
        old_branch = self.backend.refresh()
        self.assertEqual((old_branch["indexed_windows"], old_branch["reused_windows"]), (0, 3))
        self.assertNotEqual(old_branch["source_manifest"], initial_manifest)  # Detached vs main.
        self.assertEqual({item.path for item in self.backend._index.snippets},
                         {"src/alpha.ts", "src/beta.cjs", "src/legacy.py"})
        self.git("checkout", "-q", "changed")
        returned = self.backend.refresh()
        self.assertEqual((returned["indexed_windows"], returned["reused_windows"]), (0, 2))
        self.assertEqual(len(self.embedder.document_batches), batches)
        response = self.backend.search("moved beta", retrieval="bm25")
        self.assertEqual({row["path"] for row in response["results"]}, {"src/moved.cjs"})
        self.assertEqual(len(self.embedder.document_batches), batches)

    def test_syntax_invalid_snapshot_keeps_previous_durable_source_and_vectors(self):
        self.backend.refresh()
        before = self.backend._index.snippets
        batches = len(self.embedder.document_batches)
        self.write("src/alpha.ts", "export function alpha( {\n")
        with self.assertRaises(SourceSnapshotError):
            self.backend.refresh()
        self.assertEqual(self.backend._index.snippets, before)
        self.assertEqual(len(self.embedder.document_batches), batches)
        self.assertFalse(self.backend.status()["ready"])

    def test_source_changes_during_embedding_fail_before_durable_promotion(self):
        self.backend.refresh()
        before = self.backend._index.snippets
        self.write("src/alpha.ts", "export function alpha(){return 'new alpha';}\n")
        self.embedder.after_documents = lambda: self.write("src/beta.cjs", "function beta(){return 'new beta';}\n")
        with self.assertRaises(SourceSnapshotError):
            self.backend.refresh()
        self.assertEqual(self.backend._index.snippets, before)
        self.embedder.after_documents = None
        stable = self.backend.refresh()
        self.assertEqual((stable["indexed_windows"], stable["reused_windows"]), (2, 1))
        self.assertEqual(len(self.embedder.document_batches[-1]), 2)

    def test_extractor_fingerprint_reparses_but_reuses_identical_rendered_documents(self):
        from semantic_search import backend as backend_module
        first = extractor_fingerprint()
        with patch.object(backend_module, "extractor_fingerprint", return_value={**first, "review_epoch": 1}):
            self.backend.refresh()
            with patch.object(backend_module, "extract_snippets", wraps=extract_snippets) as extraction:
                self.assertTrue(self.backend.refresh()["memory_cache_hit"])
                extraction.assert_not_called()
        batches = len(self.embedder.document_batches)
        with patch.object(backend_module, "extractor_fingerprint", return_value={**first, "review_epoch": 2}):
            with patch.object(backend_module, "extract_snippets", wraps=extract_snippets) as extraction:
                changed = self.backend.refresh()
                extraction.assert_called_once()
                self.assertEqual((changed["indexed_windows"], changed["reused_windows"]), (0, 3))
            self.assertEqual(len(self.embedder.document_batches), batches)
            self.embedder.metadata["revision"] = "two"
            new_model = self.backend.refresh()
            self.assertEqual((new_model["indexed_windows"], new_model["reused_windows"]), (3, 0))
            self.assertEqual(len(self.embedder.document_batches), batches + 1)


if __name__ == "__main__":
    unittest.main()
