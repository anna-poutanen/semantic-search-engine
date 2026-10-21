"""Transient definition identity does not alter persisted extraction inventories."""
import hashlib
import importlib.util
import tempfile
import unittest
from collections import UserDict
from dataclasses import asdict, fields
from pathlib import Path
from types import MappingProxyType

from semantic_search.corpus import Snippet, extract_snippets
from semantic_search.javascript import definitions, extract_javascript
from semantic_search.source import physical_lines


PARSERS_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in
                        ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript"))


class _ExtractionFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)

    def extract(self, source, path="src/code.py", **kwargs):
        return extract_snippets(self.repo, "src", sources={path: source}, **kwargs)

    def assert_exact(self, source, snippets):
        lines = physical_lines(source)
        for snippet in snippets:
            self.assertEqual(snippet.text, "".join(lines[snippet.start_line - 1:snippet.end_line]))
            self.assertEqual(snippet.content_hash, hashlib.sha256(snippet.text.encode()).hexdigest())


class DefinitionIdentityTests(_ExtractionFixtures):
    def test_optional_keys_leave_snippet_fields_ids_text_and_hashes_identical(self):
        source = "@decorate\r\ndef run():\r\n    return 'café'\r\n"
        plain = self.extract(source)
        keys = {}
        with_keys = self.extract(source, definition_keys=keys)
        self.assertEqual([field.name for field in fields(Snippet)],
                         ["id", "path", "symbol", "start_line", "end_line", "text", "content_hash"])
        self.assertEqual([asdict(snippet) for snippet in with_keys],
                         [asdict(snippet) for snippet in plain])
        self.assertEqual(keys, {"src/code.py:run:1-3": ("src/code.py", "run", 1, 3)})
        self.assert_exact(source, with_keys)

    def test_python_repeated_qualified_names_are_distinct_definitions(self):
        source = ("def duplicate():\n    return 1\n"
                  "def duplicate():\n    return 2\n"
                  "class Container:\n"
                  "    def duplicate(self):\n        return 3\n"
                  "    def duplicate(self):\n        return 4\n")
        keys = {}
        snippets = self.extract(source, definition_keys=keys)
        self.assertEqual([snippet.symbol for snippet in snippets],
                         ["duplicate", "duplicate", "Container.duplicate", "Container.duplicate"])
        self.assertEqual(list(keys.values()), [
            ("src/code.py", "duplicate", 1, 2), ("src/code.py", "duplicate", 3, 4),
            ("src/code.py", "Container.duplicate", 6, 7),
            ("src/code.py", "Container.duplicate", 8, 9),
        ])
        self.assertEqual(len(set(keys.values())), 4)

    def test_python_overlapping_windows_share_the_complete_definition_span(self):
        source = "@decorate\ndef long_function():\n" + "".join(
            f"    value_{number} = {number}\n" for number in range(26))
        keys = {}
        snippets = self.extract(source, max_lines=10, overlap_lines=4, definition_keys=keys)
        self.assertEqual([(snippet.start_line, snippet.end_line) for snippet in snippets],
                         [(1, 10), (7, 16), (13, 22), (19, 28)])
        self.assertEqual(set(keys), {snippet.id for snippet in snippets})
        self.assertEqual(set(keys.values()), {("src/code.py", "long_function", 1, 28)})
        self.assertEqual(snippets, self.extract(source, max_lines=10, overlap_lines=4))

    def test_python_docstring_only_gaps_do_not_split_definition_identity(self):
        source = ("def outer():\n    before = 1\n    class Description:\n"
                  '        """\n' + "        documentation\n" * 30
                  + '        """\n    after = 2\n    return before + after\n')
        keys = {}
        snippets = self.extract(source, max_lines=8, overlap_lines=2, definition_keys=keys)
        self.assertTrue(any(left.end_line + 1 < right.start_line
                            for left, right in zip(snippets, snippets[1:])))
        self.assertEqual(set(keys.values()), {("src/code.py", "outer", 1, 37)})
        self.assert_exact(source, snippets)
        self.assertEqual(snippets, self.extract(source, max_lines=8, overlap_lines=2))

    def test_python_leading_docstring_uses_ast_start_instead_of_first_emitted_window(self):
        source = ('@decorate\ndef documented():\n    """\n'
                  + "    documentation\n" * 30 + '    """\n    return 42\n')
        keys = {}
        snippets = self.extract(source, max_lines=8, overlap_lines=2, definition_keys=keys)
        self.assertGreater(snippets[0].start_line, 1)
        self.assertEqual(set(keys.values()), {("src/code.py", "documented", 1, 35)})

    def test_python_nested_definitions_do_not_share_the_outer_key(self):
        source = ("def outer():\n    def inner():\n        return 1\n"
                  "    def inner():\n        return 2\n    return inner()\n")
        keys = {}
        snippets = self.extract(source, max_lines=4, overlap_lines=1, definition_keys=keys)
        outer_keys = {keys[snippet.id] for snippet in snippets if snippet.symbol == "outer"}
        inner_keys = {keys[snippet.id] for snippet in snippets if snippet.symbol == "outer.inner"}
        self.assertEqual(outer_keys, {("src/code.py", "outer", 1, 6)})
        self.assertEqual(inner_keys, {("src/code.py", "outer.inner", 2, 3),
                                      ("src/code.py", "outer.inner", 4, 5)})

    def test_caller_mapping_is_mutable_and_preserves_unrelated_entries(self):
        keys = UserDict({"unrelated": ("src/other.py", "other", 1, 2)})
        snippets = self.extract("def run():\n    return 1\n", definition_keys=keys)
        self.assertEqual(keys[snippets[0].id], ("src/code.py", "run", 1, 2))
        self.assertEqual(keys["unrelated"], ("src/other.py", "other", 1, 2))

    def test_invalid_mapping_fails_clearly_even_for_an_empty_corpus(self):
        for invalid in ([], (), "keys", object(), MappingProxyType({})):
            with self.subTest(mapping=type(invalid).__name__):
                with self.assertRaisesRegex(ValueError, "definition_keys must be a mutable mapping"):
                    extract_snippets(self.repo, "src", sources={}, definition_keys=invalid)
                with self.assertRaisesRegex(ValueError, "definition_keys must be a mutable mapping"):
                    extract_javascript("src/code.js", "", 8, 2, definition_keys=invalid)


@unittest.skipUnless(PARSERS_AVAILABLE, "Install the pinned languages extra")
class JavascriptDefinitionIdentityTests(_ExtractionFixtures):
    def test_js_ts_repeated_same_line_functions_keep_id_suffix_and_distinct_byte_keys(self):
        source = ("/* café 🦉 */ function duplicate(){return 1;} "
                  "function duplicate(){return 2;} function duplicate(){return 3;}\n")
        for extension in ("js", "ts"):
            path = f"src/code.{extension}"
            with self.subTest(extension=extension):
                keys = {}
                snippets = self.extract(source, path, definition_keys=keys)
                parsed = definitions(path, source)
                self.assertEqual([snippet.symbol for snippet in snippets], ["duplicate"] * 3)
                self.assertEqual([snippet.id for snippet in snippets], [
                    f"{path}:duplicate:1-1",
                    f"{path}:duplicate:1-1:definition@{parsed[1].node.start_byte}",
                    f"{path}:duplicate:1-1:definition@{parsed[2].node.start_byte}",
                ])
                self.assertEqual(list(keys.values()), [
                    (path, "duplicate", 1, 1, definition.node.start_byte) for definition in parsed
                ])
                self.assertEqual(len(set(keys.values())), 3)
                self.assertEqual(parsed[0].node.start_byte, len("/* café 🦉 */ ".encode()))
                self.assertEqual(snippets, self.extract(source, path))
                self.assert_exact(source, snippets)

    def test_js_ts_distinct_functions_with_overlapping_line_spans(self):
        source = "function duplicate(){\n return 1;\n} function duplicate(){\n return 2;\n}\n"
        for extension in ("js", "ts"):
            path = f"src/code.{extension}"
            with self.subTest(extension=extension):
                keys = {}
                snippets = self.extract(source, path, definition_keys=keys)
                self.assertEqual([(snippet.start_line, snippet.end_line) for snippet in snippets],
                                 [(1, 3), (3, 5)])
                self.assertEqual(len(set(keys.values())), 2)
                self.assertEqual([key[2:4] for key in keys.values()], [(1, 3), (3, 5)])

    def test_js_ts_long_windows_and_nested_definitions_use_parser_spans(self):
        source = ("export function outer(){\n"
                  + "".join(f" const value{number} = {number};\n" for number in range(18))
                  + " function inner(){\n  return 42;\n }\n return inner();\n}\n")
        for extension in ("js", "ts"):
            path = f"src/code.{extension}"
            with self.subTest(extension=extension):
                keys = {}
                snippets = self.extract(source, path, max_lines=8, overlap_lines=3,
                                        definition_keys=keys)
                parsed = {definition.symbol: definition for definition in definitions(path, source)}
                self.assertGreater(sum(snippet.symbol == "outer" for snippet in snippets), 1)
                for snippet in snippets:
                    definition = parsed[snippet.symbol]
                    self.assertEqual(keys[snippet.id],
                                     (path, snippet.symbol, definition.start_line,
                                      definition.end_line, definition.node.start_byte))
                self.assertEqual(len(set(keys.values())), 2)
                self.assertEqual(snippets, self.extract(source, path, max_lines=8, overlap_lines=3))

    def test_js_ts_directive_and_comment_gaps_share_one_definition_identity(self):
        source = ("function documented(){\n 'use strict';\n /*\n"
                  + " documentation\n" * 30 + " */\n return 42;\n}\n")
        for extension in ("js", "ts"):
            path = f"src/code.{extension}"
            with self.subTest(extension=extension):
                keys = {}
                snippets = self.extract(source, path, max_lines=8, overlap_lines=2,
                                        definition_keys=keys)
                self.assertGreater(snippets[0].start_line, 1)
                self.assertEqual(set(keys.values()), {(path, "documented", 1, 36, 0)})
                self.assertEqual(snippets, self.extract(source, path, max_lines=8, overlap_lines=2))

    def test_direct_javascript_extraction_and_corpus_dispatch_have_identical_keys(self):
        source = "function run(){\n return 1;\n}\n"
        keys = {}
        snippets = extract_javascript("src/code.js", source, 8, 2, definition_keys=keys)
        dispatched_keys = {}
        dispatched = self.extract(source, "src/code.js", max_lines=8, overlap_lines=2,
                                  definition_keys=dispatched_keys)
        self.assertEqual(snippets, extract_javascript("src/code.js", source, 8, 2))
        self.assertEqual(snippets, dispatched)
        self.assertEqual(keys, dispatched_keys)


if __name__ == "__main__":
    unittest.main()
