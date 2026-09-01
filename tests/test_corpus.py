import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from semantic_search.corpus import extract_snippets


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.root = self.repo / "src/flask"
        self.root.mkdir(parents=True)

    def write_source(self, source):
        path = self.root / "example.py"
        path.write_text(source)
        return path

    def test_decorators_methods_and_nested_function_locations(self):
        source = (
            "class Service:\n"
            "    @decorate(\n"
            "        flag=True,\n"
            "    )\n"
            "    async def run(self):\n"
            "        def inner():\n"
            "            return 'café'\n"
            "        return inner()\n"
            "\n"
            "def outside():\n"
            "    return 1\n"
        )
        self.write_source(source)
        snippets = extract_snippets(self.repo)
        self.assertEqual([s.symbol for s in snippets], ["Service.run", "Service.run.inner", "outside"])
        self.assertEqual([(s.start_line, s.end_line) for s in snippets], [(2, 8), (6, 7), (10, 11)])
        self.assertEqual(snippets[0].text, "".join(source.splitlines(keepends=True)[1:8]))
        self.assertEqual(snippets[0].path, "src/flask/example.py")
        self.assertEqual(snippets[0].content_hash, hashlib.sha256(snippets[0].text.encode()).hexdigest())

    def test_windows_preserve_source_and_cover_every_implementation_line(self):
        source = "def long_function():\n" + "".join(f"    value_{i} = {i}\n" for i in range(25))
        self.write_source(source)
        snippets = extract_snippets(self.repo, max_lines=10, overlap_lines=3)
        self.assertEqual([(s.start_line, s.end_line) for s in snippets], [(1, 10), (8, 17), (15, 24), (22, 26)])
        covered = set()
        for snippet in snippets:
            self.assertEqual(snippet.text, "".join(source.splitlines(keepends=True)[snippet.start_line - 1:snippet.end_line]))
            covered.update(range(snippet.start_line, snippet.end_line + 1))
        self.assertEqual(covered, set(range(1, 27)))
        self.assertEqual(len({s.id for s in snippets}), 4)

    def test_docstring_only_windows_are_skipped_and_body_retained(self):
        source = 'def documented():\n    """\n' + "    documentation\n" * 30 + '    """\n    return 42\n'
        self.write_source(source)
        snippets = extract_snippets(self.repo, max_lines=8, overlap_lines=2)
        self.assertEqual([(s.start_line, s.end_line) for s in snippets], [(31, 34)])
        self.assertIn("return 42", snippets[-1].text)

    def test_overloads_and_placeholder_methods_do_not_hide_real_implementation(self):
        source = (
            "import typing as t\n"
            "from abc import abstractmethod\n"
            "@t.overload\n"
            "def convert(value: str): ...\n"
            "@t.overload\n"
            "def convert(value: int):\n"
            "    return str(value)\n"
            "def convert(value):\n"
            "    return str(value)\n"
            "class Base:\n"
            "    @abstractmethod\n"
            "    def missing(self):\n"
            '        """Override in subclasses."""\n'
            "        pass\n"
            "    async def absent(self): ...\n"
            "    def unsupported(self):\n"
            '        """No default implementation."""\n'
            "        raise NotImplementedError('override')\n"
            "    def unsupported_bare(self):\n"
            "        raise NotImplementedError\n"
            "    def described(self):\n"
            '        """Only a description."""\n'
            "    @abstractmethod\n"
            "    def useful_default(self):\n"
            "        return 42\n"
            "def conditional(value):\n"
            "    if value is None:\n"
            "        raise NotImplementedError\n"
            "    return value\n"
        )
        self.write_source(source)
        snippets = extract_snippets(self.repo)
        self.assertEqual([s.symbol for s in snippets],
                         ["convert", "Base.useful_default", "conditional"])
        self.assertEqual((snippets[0].start_line, snippets[0].end_line), (8, 9))

    def test_long_docstring_windows_cover_all_multiline_behavior(self):
        source = 'def documented():\n    """\n' + "    documentation\n" * 20 + (
            '    """\n'
            "    values = [\n"
            "        1,\n"
            "        2,\n"
            "        3,\n"
            "    ]\n"
            "    return sum(values)\n"
        )
        self.write_source(source)
        snippets = extract_snippets(self.repo, max_lines=5, overlap_lines=1)
        covered = {line for s in snippets for line in range(s.start_line, s.end_line + 1)}
        self.assertTrue(set(range(24, 30)).issubset(covered))
        self.assertTrue(all(s.end_line >= 24 for s in snippets))
        for snippet in snippets:
            self.assertEqual(snippet.text, "".join(source.splitlines(keepends=True)[snippet.start_line - 1:snippet.end_line]))

    def test_behavior_sharing_a_docstring_line_is_preserved(self):
        source = 'def compact(): """A summary."""; return 42\n'
        self.write_source(source)
        snippets = extract_snippets(self.repo)
        self.assertEqual(len(snippets), 1)
        self.assertEqual(snippets[0].text, source)

    def test_preserves_original_line_endings(self):
        path = self.root / "example.py"
        path.write_bytes(b"def run():\r\n    return 1\r\n")
        self.assertEqual(extract_snippets(self.repo)[0].text, "def run():\r\n    return 1\r\n")

    @unittest.skipUnless(shutil.which("git"), "Git is unavailable")
    def test_git_corpus_ignores_untracked_python_and_other_source_roots(self):
        self.write_source("def tracked():\n    return 1\n")
        (self.root / "scratch.py").write_text("def scratch():\n    return 2\n")
        (self.repo / "other.py").write_text("def other():\n    return 3\n")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "src/flask/example.py", "other.py"], check=True)
        self.assertEqual([s.symbol for s in extract_snippets(self.repo)], ["tracked"])

    def test_rejects_invalid_windows_and_source_outside_repo(self):
        with self.assertRaises(ValueError):
            extract_snippets(self.repo, max_lines=5, overlap_lines=5)
        with self.assertRaises(ValueError):
            extract_snippets(self.repo, source_root="../")

    def test_symlinks_cannot_escape_the_source_directory(self):
        self.write_source("def internal():\n    return 1\n")
        outside = self.repo / "outside.py"
        outside.write_text("def external():\n    return 2\n")
        (self.root / "linked.py").symlink_to(outside)
        self.assertEqual([s.symbol for s in extract_snippets(self.repo)], ["internal"])
        (self.repo / "escape").symlink_to(self.repo.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            extract_snippets(self.repo, source_root="escape")

    def test_captured_sources_keep_initial_symbols_and_membership_during_live_edits(self):
        original = "class C:\n    def method(self):\n        return 1\n"
        removed_source = "def removed():\n    return 2\n"
        path = self.write_source(original)
        removed_path = self.root / "removed.py"
        removed_path.write_text(removed_source)
        sources = {"src/flask/removed.py": removed_source, "src/flask/example.py": original}
        initial = extract_snippets(self.repo)
        path.write_text(original.replace("class C:", "class D:"))
        removed_path.unlink()
        try:
            with patch("semantic_search.corpus._source_files", side_effect=AssertionError("Git listing")), \
                    patch.object(Path, "read_bytes", side_effect=AssertionError("File reread")):
                captured = extract_snippets(self.repo, sources=sources)
        finally:
            path.write_text(original)
            removed_path.write_text(removed_source)
        self.assertEqual(captured, initial)
        self.assertEqual([s.symbol for s in captured], ["C.method", "removed"])

    def test_captured_source_paths_and_contents_are_validated(self):
        source = "def run():\n    return 1\n"
        invalid = [{"../outside.py": source}, {"other.py": source},
                   {str(self.root / "absolute.py"): source}, {"src/flask/file.txt": source},
                   {"src/flask/code.py": b"not decoded"}, [source]]
        for sources in invalid:
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                extract_snippets(self.repo, sources=sources)


if __name__ == "__main__":
    unittest.main()
