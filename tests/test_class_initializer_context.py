"""Generic adjacent-field context, with exact saved source and no gold labels."""
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

from semantic_search.corpus import extract_snippets
from semantic_search.relations import ReferenceGraph
from semantic_search.source import physical_lines


AVAILABLE = all(importlib.util.find_spec(name) is not None for name in
                ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript"))


@unittest.skipUnless(AVAILABLE, "Install the pinned languages extra")
class ClassInitializerContextTests(unittest.TestCase):
    def extract(self, source, path="src/context.ts", **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            return extract_snippets(Path(folder), "src", sources={path: source}, **kwargs)

    def assert_exact(self, source, snippets):
        for item in snippets:
            self.assertEqual(item.text, "".join(physical_lines(source)[item.start_line - 1:item.end_line]))
            self.assertEqual(item.content_hash, hashlib.sha256(item.text.encode()).hexdigest())

    def groups(self, snippets):
        return [item for item in snippets if "-initializers:" in item.symbol]

    def test_adjacent_instance_initializers_keep_individual_fields_and_exact_context(self):
        for ending in ("\n", "\r\n", "\r"):
            for extension in (".js", ".jsx", ".ts", ".tsx"):
                source = ending.join(["// 🦉 before class", "class Store {", "  first = new WeakMap();",
                                      "  // second independent initialization", "  second = new Map();",
                                      "  read() {return this.second;}", "}", ""])
                with self.subTest(ending=repr(ending), extension=extension):
                    snippets = self.extract(source, "src/context" + extension)
                    self.assertEqual([item.symbol for item in snippets],
                                     ["Store.first", "Store.second", "Store.read", "Store.<instance-initializers:1>"])
                    group = self.groups(snippets)[0]
                    self.assertEqual((group.start_line, group.end_line), (3, 5))
                    self.assertNotIn("read()", group.text)
                    self.assert_exact(source, snippets)

    def test_static_and_instance_runs_are_separate_and_contextual_static_is_a_name(self):
        source = ("class Store {\n static first=1;\n static second=2;\n third=3;\n static=4;\n"
                  " static final=5;\n sixth=6;\n seventh=7;\n}\n")
        snippets = self.extract(source)
        self.assertEqual([(item.symbol, item.start_line, item.end_line) for item in self.groups(snippets)],
                         [("Store.<static-initializers:1>", 2, 3),
                          ("Store.<instance-initializers:1>", 4, 5),
                          ("Store.<instance-initializers:2>", 7, 8)])
        self.assert_exact(source, snippets)

    def test_type_only_fields_methods_callbacks_and_static_blocks_break_runs(self):
        source = ("class Store {\n first=1;\n declared: unknown;\n second=2;\n declare erased: unknown;\n"
                  " third=3;\n read(){return 1;}\n fourth=4;\n handler=()=>1;\n fifth=5;\n"
                  " static { initialize(); }\n sixth=6;\n}\n")
        snippets = self.extract(source)
        self.assertEqual(self.groups(snippets), [])
        self.assertFalse(any(item.symbol.endswith((".declared", ".erased")) for item in snippets))
        self.assert_exact(source, snippets)

    def test_long_initializer_runs_use_existing_windows_and_cover_each_executable_line(self):
        source = "class Store {\n" + "".join(f"  field{i} = {i};\n" for i in range(380)) + "}\n"
        snippets = self.extract(source, max_lines=160, overlap_lines=40)
        groups = self.groups(snippets)
        self.assertEqual([(item.start_line, item.end_line) for item in groups], [(2, 161), (122, 281), (242, 381)])
        self.assertTrue(set(range(2, 382)).issubset({line for item in groups for line in range(item.start_line, item.end_line + 1)}))
        self.assert_exact(source, snippets)

    def test_group_context_does_not_replace_individual_call_graph_owners(self):
        source = ("function first(){return 1;}\nfunction second(){return 2;}\nclass Store {\n"
                  " a=first();\n b=second();\n}\n")
        snippets = self.extract(source)
        graph = ReferenceGraph({"src/context.ts": source}, snippets)
        for field, callee in (("Store.a", "first"), ("Store.b", "second")):
            item = next(item for item in snippets if item.symbol == field)
            self.assertEqual([item.symbol for item in graph.dependencies(item)], [callee])

    def test_abstract_type_only_classes_and_single_fields_get_no_extra_context(self):
        for source in ("abstract class Store { abstract first: unknown; abstract second: unknown; }",
                       "declare class Store { first: unknown; second: unknown; }",
                       "class Store { first = 1; }", "interface Store { first:unknown; second:unknown; }"):
            with self.subTest(source=source):
                self.assertEqual(self.groups(self.extract(source)), [])
