"""Source-neutral grammar compatibility cases; no corpus labels or models."""
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

from semantic_search.corpus import extract_snippets
from semantic_search.source import physical_lines


AVAILABLE = all(importlib.util.find_spec(name) is not None for name in
                ("tree_sitter", "tree_sitter_typescript"))


@unittest.skipUnless(AVAILABLE, "Install the pinned languages extra")
class TypeScriptVarianceTests(unittest.TestCase):
    def extract(self, source, path="src/variance.ts"):
        with tempfile.TemporaryDirectory() as folder:
            return extract_snippets(Path(folder), "src", sources={path: source})

    def assert_exact(self, source, snippets):
        for item in snippets:
            self.assertEqual(item.text, "".join(physical_lines(source)[item.start_line - 1:item.end_line]))
            self.assertEqual(item.content_hash, hashlib.sha256(item.text.encode()).hexdigest())

    def test_declarations_accept_variance_without_indexing_type_only_prose(self):
        source = ("interface Producer<out T> {value: T;}\n"
                  "type Consumer<in T> = (value: T) => void;\n"
                  "class Cell<in out T> { read(value: T) {return value;} }\n"
                  "const Factory = class<out T> { read(value: T) {return value;} };\n")
        snippets = self.extract(source)
        self.assertEqual([item.symbol for item in snippets], ["Cell.read", "Factory.read"])
        self.assert_exact(source, snippets)

    def test_comments_nested_constraints_unicode_and_original_offsets(self):
        for ending in ("\n", "\r\n", "\r"):
            source = ending.join(["// 🦉 before multibyte names", "export", "class Cell<",
                                  "  in /*🦉*/ out π extends Map<string, number>,",
                                  "  out U = π,", "> {",
                                  "  read(value: π) {return value;}", "}", ""])
            for extension in (".ts", ".tsx", ".mts", ".cts"):
                with self.subTest(ending=repr(ending), extension=extension):
                    snippets = self.extract(source, "src/variance" + extension)
                    self.assertEqual([item.symbol for item in snippets], ["Cell.read"])
                    self.assertEqual((snippets[0].start_line, snippets[0].end_line), (7, 7))
                    self.assert_exact(source, snippets)

    def test_contextual_out_can_remain_a_parameter_name(self):
        for declaration in ("interface Box<out> {value: out;}",
                            "interface Box<in out> {consume(value:out):void;}",
                            "interface Box<out out> {value:out;}"):
            with self.subTest(source=declaration):
                self.assertEqual(self.extract(declaration), [])

    def test_runtime_words_comments_and_strings_are_not_normalized(self):
        source = ("const text = 'interface Box<out T> {}';\n"
                  "// interface Box<in T> {}\n"
                  "function read() {const out = 1; const input = 2; return out + input;}\n")
        snippets = self.extract(source)
        self.assertEqual([item.symbol for item in snippets], ["<module>.text", "read"])
        self.assert_exact(source, snippets)

    def test_invalid_modifiers_context_and_other_errors_fail_closed(self):
        sources = ["interface Box<out in T> {}", "interface Box<in in T> {}",
                   "interface Box<out out T> {}", "interface Box<in> {}",
                   "interface Box<out T extends> {}", "interface Box<out T = > {}",
                   "interface Box<out T> { value: ; }", "class Box<out T> {read(){return (;}}",
                   "function read<out T>(value:T) {return value;}",
                   "const read = <out T>(value:T) => value;",
                   "class Box { read<in T>(value:T) {return value;} }",
                   "const value = call<out T>();", "const value = out T;",
                   "interface Box<out\nT> {}", "interface Box<out\rT> {}",
                   "interface Box<out/*\n*/T> {}", "interface Box<out\u2028T> {}",
                   "interface Box<in out\nT> {}", "interface Box<in\nout T> {}"]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                self.extract(source)
        with self.assertRaises(SyntaxError):
            self.extract("interface Box<out T> {}", "src/invalid.js")
