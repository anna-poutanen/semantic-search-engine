import tempfile
import unittest
from pathlib import Path

from semantic_search.corpus import extract_snippets
from semantic_search.relations import ReferenceGraph


class ReferenceGraphTests(unittest.TestCase):
    def graph(self, sources):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            snippets = extract_snippets(repo, source_root="src", sources=sources)
        return ReferenceGraph(sources, snippets), {(s.path, s.symbol): s for s in snippets}

    def symbols(self, graph, snippet, limit=20):
        return [(s.path, s.symbol) for s in graph.dependencies(snippet, limit)]

    def test_cross_file_relative_imports_module_aliases_and_forward_definitions(self):
        sources = {
            "src/pkg/__init__.py": "",
            "src/pkg/util.py": "def parse():\n    return 1\n",
            "src/pkg/sub/__init__.py": "from ..util import parse\ndef run():\n    return parse()\n",
            "src/pkg/sub/api.py": (
                "from ..util import parse as decode\nimport pkg.util as helpers\n"
                "def entry():\n    return decode() + helpers.parse() + later()\n"
                "def later():\n    return 2\n"),
        }
        graph, windows = self.graph(sources)
        self.assertEqual(self.symbols(graph, windows[("src/pkg/sub/api.py", "entry")]),
                         [("src/pkg/util.py", "parse"), ("src/pkg/sub/api.py", "later")])
        self.assertEqual(self.symbols(graph, windows[("src/pkg/sub/__init__.py", "run")]),
                         [("src/pkg/util.py", "parse")])

    def test_class_instance_classmethod_and_static_calls_are_explicit(self):
        sources = {"src/pkg/tools.py": (
            "class Tools:\n"
            "    def helper(self):\n        return 1\n"
            "    def normal(self):\n        return self.helper()\n"
            "    @classmethod\n    def class_call(cls):\n        return cls.static()\n"
            "    @staticmethod\n    def static():\n        return 2\n"
            "def use():\n    tools = Tools()\n    return tools.helper() + Tools.class_call()\n"),
            "src/pkg/api.py": "from .tools import Tools\ndef entry():\n    t = Tools()\n    return t.normal()\n"}
        graph, windows = self.graph(sources)
        expectations = {"Tools.normal": ["Tools.helper"], "Tools.class_call": ["Tools.static"],
                        "use": ["Tools.helper", "Tools.class_call"]}
        for symbol, expected in expectations.items():
            self.assertEqual([s.symbol for s in graph.dependencies(windows[("src/pkg/tools.py", symbol)], 10)], expected)
        self.assertEqual(self.symbols(graph, windows[("src/pkg/api.py", "entry")]),
                         [("src/pkg/tools.py", "Tools.normal")])

    def test_parameters_reassignment_factories_and_local_imports_do_not_invent_edges(self):
        sources = {
            "src/pkg/one.py": "def helper():\n    return 1\n",
            "src/pkg/two.py": "def helper():\n    return 2\n",
            "src/pkg/api.py": (
                "from .one import helper\n"
                "def local():\n    from .two import helper\n    return helper()\n"
                "def untouched():\n    return helper()\n"
                "def dynamic(helper):\n    return helper()\n"
                "def rebound():\n    helper = lambda: 5\n    return helper()\n"
                "class Widget:\n    def work(self):\n        return 3\n"
                "def factory():\n    def work():\n        return 4\n    return work\n"
                "def opaque(factory):\n    value = factory()\n    return value.work()\n"
                "def changed():\n    value = Widget()\n    value = object()\n    return value.work()\n"
                "def not_born(value):\n    result = value.work()\n    value = Widget()\n    return result\n"
                "def comprehension(items):\n    return [helper() for helper in items]\n"),
        }
        graph, windows = self.graph(sources)
        self.assertEqual(self.symbols(graph, windows[("src/pkg/api.py", "local")]), [("src/pkg/two.py", "helper")])
        self.assertEqual(self.symbols(graph, windows[("src/pkg/api.py", "untouched")]), [("src/pkg/one.py", "helper")])
        for name in ("dynamic", "rebound", "opaque", "changed", "not_born", "comprehension"):
            with self.subTest(symbol=name):
                self.assertEqual(self.symbols(graph, windows[("src/pkg/api.py", name)]), [])

    def test_nested_calls_belong_to_inner_function_and_closure_receiver_remains_known(self):
        sources = {"src/pkg/api.py": (
            "def helper():\n    return 1\n"
            "def outer():\n    def inner():\n        return helper()\n    return inner()\n"
            "class C:\n    def method(self):\n        return 2\n"
            "    def outer(self):\n        def inner():\n            return self.method()\n        return inner()\n")}
        graph, windows = self.graph(sources)
        self.assertEqual(self.symbols(graph, windows[("src/pkg/api.py", "outer")]), [("src/pkg/api.py", "outer.inner")])
        self.assertEqual(self.symbols(graph, windows[("src/pkg/api.py", "outer.inner")]), [("src/pkg/api.py", "helper")])
        self.assertEqual(self.symbols(graph, windows[("src/pkg/api.py", "C.outer.inner")]), [("src/pkg/api.py", "C.method")])

    def test_static_dynamic_receiver_and_replaced_method_are_unresolved(self):
        sources = {"src/pkg/api.py": (
            "class C:\n    def helper(self):\n        return 1\n"
            "    @staticmethod\n    def dynamic(self):\n        return self.helper()\n"
            "    def replaced(self):\n        self.helper = lambda: 2\n        return self.helper()\n")}
        graph, windows = self.graph(sources)
        for symbol in ("C.dynamic", "C.replaced"):
            self.assertEqual(self.symbols(graph, windows[("src/pkg/api.py", symbol)]), [])

    def test_reference_limit_and_first_window_are_deterministic(self):
        sources = {"src/pkg/api.py": "def one():\n    return 1\ndef two():\n    return 2\ndef run():\n    return one() + two()\n"}
        graph, windows = self.graph(sources)
        run = windows[("src/pkg/api.py", "run")]
        self.assertEqual(self.symbols(graph, run, 0), [])
        self.assertEqual(self.symbols(graph, run, 1), [("src/pkg/api.py", "one")])
        self.assertEqual(self.symbols(graph, run, 2), [("src/pkg/api.py", "one"), ("src/pkg/api.py", "two")])
        for limit in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                graph.dependencies(run, limit)


if __name__ == "__main__":
    unittest.main()
