"""Source-neutral CPU fixtures for optional JS/TS syntax and saved-index dispatch."""
import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from semantic_search.backend import SearchBackend, SourceSnapshotError
from semantic_search.corpus import _source_files, extract_snippets
from semantic_search.languages import EXTRACTORS, extractor_fingerprint
from semantic_search.relations import ReferenceGraph
from semantic_search.source import physical_lines

PARSERS_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in
                        ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript"))


class RecordingEmbedder:
    metadata = {"id": "cpu-language-fixture", "revision": "1", "dimensions": 2}

    def __init__(self):
        self.documents = []
        self.after_documents = None

    def embed_documents(self, texts):
        self.documents.append(list(texts))
        vectors = np.array([[1., .25 + ("changed" in text)] for text in texts]).reshape(-1, 2)
        if self.after_documents:
            self.after_documents()
        return vectors

    def embed_query(self, text):
        return np.array([1., 0.])

    def close(self):
        pass


@unittest.skipUnless(PARSERS_AVAILABLE, "Install the pinned languages extra")
class LanguageCorpusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name).resolve()
        (self.repo / "src").mkdir()

    def extract(self, source, path="src/code.ts", **kwargs):
        return extract_snippets(self.repo, "src", sources={path: source}, **kwargs)

    def assert_exact(self, source, snippets):
        lines = physical_lines(source)
        for item in snippets:
            self.assertTrue(1 <= item.start_line <= item.end_line <= len(lines))
            self.assertEqual(item.text, "".join(lines[item.start_line - 1:item.end_line]))
            self.assertEqual(item.content_hash, hashlib.sha256(item.text.encode("utf-8")).hexdigest())

    def test_all_registered_extensions_and_python_retains_original_format(self):
        for ext in EXTRACTORS:
            source = "def run():\n    return 1\n" if ext == ".py" else "function run() {\n  return 1;\n}\n"
            with self.subTest(ext=ext):
                snippets = self.extract(source, "src/code" + ext)
                self.assertEqual([s.symbol for s in snippets], ["run"])
                self.assertEqual(snippets[0].id, f"src/code{ext}:run:1-{len(physical_lines(source))}")
                self.assert_exact(source, snippets)

    def test_overloads_declarations_types_and_empty_placeholders_are_omitted(self):
        source = (
            "declare function absent(x: string): string;\n"
            "interface Shape { method(): void; }\n"
            "type Handler = (x: string) => string;\n"
            "export function convert(x: string): string;\n"
            "export function convert(x: number): string;\n"
            "export function convert(x: any) { return String(x); }\n"
            "function empty() {}\n"
            "const absentArrow = () => { /* placeholder */ };\n"
            "target.absent = (() => {});\n"
            "function directiveOnly() { 'use strict'; }\n"
            "abstract class Base { abstract missing(): void; empty() {} useful() {return 2;} }\n"
        )
        snippets = self.extract(source)
        self.assertEqual([s.symbol for s in snippets], ["convert", "Base.useful"])
        self.assert_exact(source, snippets)
        for ext in (".d.ts", ".d.mts", ".d.cts"):
            self.assertEqual(self.extract("declare function absent(): void;\n", "src/types" + ext), [])

    def test_functions_methods_accessors_assigned_and_named_factory_callbacks(self):
        source = (
            "export async function run(x: number) {return x;}\n"
            "function* generate() {yield 1;}\n"
            "const arrow = async (x: number) => x + 1;\n"
            "const expression = function internal(x: number) {return x;}\n"
            "obj.member = (x: number) => x;\n"
            "exports.callback = wrap(function (x: number) {return x;});\n"
            "const callback = wrap((x: number) => x);\n"
            "const callbacks = wrap(() => 1, () => 2);\n"
            "const api = { method() {return 1;}, helper: () => 2, ['fixed']() {return 3;} };\n"
            "class Service { constructor() {this.ready=true;} get value() {return 1;} set value(v: number) {this.current=v;} #private() {return 1;} field = () => 1; }\n"
        )
        symbols = [s.symbol for s in self.extract(source)]
        self.assertEqual(symbols, ["run", "generate", "arrow", "expression", "obj.member",
                                   "exports.callback", "callback", "callbacks.callback1", "callbacks.callback2",
                                   "api.method", "api.helper", "api.fixed", "Service.constructor",
                                   "Service.get value", "Service.set value", "Service.#private", "Service.field"])
        javascript = source.replace(": number", "")
        self.assertEqual([s.symbol for s in self.extract(javascript, "src/code.js")], symbols)

    def test_default_exports_nested_scopes_and_decorators(self):
        source = (
            "export default function () {\n"
            "  function inner() {return 1;}\n"
            "  const local = () => inner();\n"
            "  return local();\n"
            "}\n"
            "@classDecorator\n"
            "class Service {\n"
            "  @decorate(\n"
            "    {enabled: true}\n"
            "  )\n"
            "  public async run() {\n"
            "    function inner() {return 2;}\n"
            "    return inner();\n"
            "  }\n"
            "}\n"
        )
        snippets = self.extract(source)
        self.assertEqual([s.symbol for s in snippets], ["default", "default.inner", "default.local", "Service.run", "Service.run.inner"])
        self.assertEqual((snippets[3].start_line, snippets[3].end_line), (8, 14))
        self.assert_exact(source, snippets)
        self.assertEqual([s.symbol for s in self.extract("export default (x: number) => x;\n")], ["default"])
        self.assertEqual([s.symbol for s in self.extract("export default class { run() {return 1;} }\n")], ["default.run"])
        exported = "export\nconst run = () => 1;\n"
        self.assertEqual(self.extract(exported)[0].text, exported)
        for ending in ("\n", "\r\n", "\r"):
            exported = ending.join(["export", "// café 😀", "const run = () => 1;", ""])
            self.assertEqual(self.extract(exported)[0].text, exported)
        template = "const text = `export\nconst literal = 1;`;\n"
        self.assert_exact(template, self.extract(template))

    def test_jsx_tsx_unicode_bom_and_all_physical_line_endings(self):
        for extension in (".jsx", ".tsx"):
            for ending in ("\n", "\r\n", "\r"):
                source = ending.join(["\ufeffconst Component = () => {", "  const label = 'café 😀\u2028data';",
                                       "  return <span>{label}</span>;", "};", "function second() {return 2;}", ""])
                with self.subTest(extension=extension, ending=repr(ending)):
                    snippets = self.extract(source, "src/component" + extension)
                    self.assertEqual([s.symbol for s in snippets], ["Component", "second"])
                    self.assertEqual([(s.start_line, s.end_line) for s in snippets], [(1, 4), (5, 5)])
                    self.assert_exact(source, snippets)
                    self.assertIn("\u2028data", snippets[0].text)

    def test_long_bodies_window_every_implementation_line(self):
        source = "function longBody() {\r\n" + "".join(f"  let value{i} = {i};\r\n" for i in range(380)) + "  return value379;\r\n}\r\n"
        snippets = self.extract(source, max_lines=160, overlap_lines=40)
        self.assertEqual([(s.start_line, s.end_line) for s in snippets], [(1, 160), (121, 280), (241, 383)])
        covered = {line for item in snippets for line in range(item.start_line, item.end_line + 1)}
        self.assertTrue(set(range(2, 383)).issubset(covered))
        self.assert_exact(source, snippets)

    def test_repeated_same_line_names_and_scopes_keep_unique_window_ids(self):
        source = "var f=()=>1; var f=()=>2; function run(){if(a){const f=()=>3;f();}else{const f=()=>4;f();}}"
        snippets = self.extract(source, "src/code.js")
        self.assertEqual(len(snippets), 5)
        self.assertEqual(len({item.id for item in snippets}), 5)
        self.assert_exact(source, snippets)
        self.assertIn(":definition@", snippets[1].id)

    def test_runtime_fields_static_blocks_module_initialization_and_mixed_objects(self):
        source = (
            "import {setup} from './helper';\n"
            "const values = setup();\n"
            "const config = {\n"
            "  handler() { return 1; },\n"
            "  timer: setup(),\n"
            "};\n"
            "class C {\n"
            "  timer = setup();\n"
            "  static { setup(); }\n"
            "  static { setup(); }\n"
            "}\n"
            "setup();\n"
        )
        snippets = self.extract(source)
        self.assertEqual([s.symbol for s in snippets], ["<module>.values", "config.handler", "<module>.config",
                                                       "C.timer", "C.<static:1>", "C.<static:2>", "<module>"])
        self.assert_exact(source, snippets)
        self.assertTrue(any(item.start_line <= 5 <= item.end_line for item in snippets))
        computed = "const config={\n [computeKey()]: ()=>1,\n};\nclass C { [computeKey()] = setup(); }\n"
        computed_snippets = self.extract(computed)
        self.assertEqual(computed_snippets[0].symbol, "<module>.config")
        self.assertTrue(computed_snippets[1].symbol.startswith("C.<field@"))
        self.assert_exact(computed, computed_snippets)
        module_exports = "export default initialize();\nexport enum Status { Initial=nextValue(), Final=3 }\nconst enum Types { Initial=1 }\n"
        module_snippets = self.extract(module_exports)
        self.assertEqual([item.symbol for item in module_snippets], ["<module>.default", "<module>.Status"])
        self.assert_exact(module_exports, module_snippets)

    def test_invalid_tree_missing_tokens_and_non_utf8_raise(self):
        for source in ("function broken( {", "function broken() { return 1;", "const x = ;", "function okay(){return 1;}\nconst broken=;", "export; const x=1;"):
            with self.subTest(source=source), self.assertRaises(SyntaxError):
                self.extract(source)
        path = self.repo / "src/bad.js"
        path.write_bytes(b"function bad() {return '\xff';}")
        with self.assertRaises(UnicodeError):
            extract_snippets(self.repo, "src")

    def test_discovery_tracked_membership_and_js_only_default_exclusions(self):
        files = {"src/code.js": "function tracked(){return 1;}", "src/scratch.ts": "function scratch(){return 2;}",
                 "src/types.d.ts": "declare function absent(): void;", "src/node_modules/dep.js": "function dep(){return 1;}",
                 "src/build/output.ts": "function built(){return 1;}", "src/build/retained.py": "def retained():\n    return 1\n"}
        for relative, source in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "src/code.js", "src/types.d.ts", "src/node_modules/dep.js",
                        "src/build/output.ts", "src/build/retained.py"], check=True)
        paths = [p.relative_to(self.repo).as_posix() for p in _source_files(self.repo, self.repo / "src")]
        self.assertEqual(paths, ["src/build/retained.py", "src/code.js"])
        self.assertEqual([s.symbol for s in extract_snippets(self.repo, "src")], ["retained", "tracked"])
        self.assertEqual(extract_snippets(self.repo, "src", source_exclusions=["src/code.js", "*.py"]), [])

    def test_captured_membership_survives_live_delete_and_symlinks_cannot_escape(self):
        source = "function saved(){return 1;}\n"
        captured = {"src/deleted.ts": source}
        with patch.object(Path, "read_bytes", side_effect=AssertionError("File reread")):
            self.assertEqual(self.extract(source, "src/deleted.ts"), extract_snippets(self.repo, "src", sources=captured))
        outside = self.repo / "outside.ts"
        outside.write_text(source)
        (self.repo / "src/linked.ts").symlink_to(outside)
        self.assertEqual(extract_snippets(self.repo, "src"), [])
        with self.assertRaises(ValueError):
            self.extract(source, "src/linked.ts")


@unittest.skipUnless(PARSERS_AVAILABLE, "Install the pinned languages extra")
class LanguageReferenceTests(unittest.TestCase):
    def graph(self, sources):
        snippets = extract_snippets(Path('/tmp'), "src", sources=sources)
        return ReferenceGraph(sources, snippets)

    def test_direct_lexical_nested_and_relative_named_default_namespace_imports(self):
        sources = {"src/helper.ts": "export function helper(x: number) {return x + 1;}\nexport default (x: number) => x;\n",
                   "src/main.ts": "import {helper as execute} from './helper.js';\nimport * as ns from './helper';\nimport def from './helper';\nfunction run(){return execute(1)+ns.helper(2)+def(3);}\nfunction outer(){const local=()=>1; function inner(){return local();} return inner();}\n"}
        edges = self.graph(sources).edges
        self.assertEqual(edges[("src/main.ts", "run")], [("src/helper.ts", "helper"), ("src/helper.ts", "default")])
        self.assertEqual(edges[("src/main.ts", "outer")], [("src/main.ts", "outer.inner")])
        self.assertEqual(edges[("src/main.ts", "outer.inner")], [("src/main.ts", "outer.local")])

    def test_same_stem_symbols_do_not_change_python_reference_targets(self):
        sources = {"src/helper.py": "def helper():\n    return 'python'\n",
                   "src/helper.ts": "export function helper(){return 'typescript';}\n",
                   "src/main.py": "from helper import helper\ndef run():\n    return helper()\n",
                   "src/main.ts": "import {helper} from './helper'; function run(){return helper();}\n"}
        edges = self.graph(sources).edges
        self.assertEqual(edges[("src/main.py", "run")], [("src/helper.py", "helper")])
        self.assertEqual(edges[("src/main.ts", "run")], [("src/helper.ts", "helper")])

    def test_shadowing_reassignments_type_imports_factories_and_runtime_dispatch_unresolved(self):
        source = (
            "function helper(){return 1;}\n"
            "const product=wrap(()=>1);\n"
            "function parameter(helper: any){return helper();}\n"
            "function shadow(){const helper=unknown; return helper();}\n"
            "function hoisted(){if(flag){var helper=unknown;} return helper();}\n"
            "function block(){if(flag){const helper=unknown; helper();} return helper();}\n"
            "function factory(){return product();}\n"
            "class C {method(){return 1;}}\n"
            "function runtime(obj: C){return obj.method();}\n"
            "function inherited(){return new C().method();}\n"
        )
        edges = self.graph({"src/code.ts": source}).edges
        self.assertEqual(dict(edges), {("src/code.ts", "block"): [("src/code.ts", "helper")]})
        sources = {"src/helper.ts": "export function helper(){return 1;}",
                   "src/main.ts": "import {type helper} from './helper'; function run(){return helper();}"}
        self.assertEqual(dict(self.graph(sources).edges), {})
        self.assertEqual(dict(self.graph({"src/code.ts": "function helper(){return 1;} function run(){helper = other; return helper();}"}).edges), {})

    def test_mutated_namespace_ambiguous_modules_external_and_reexports_are_unresolved(self):
        sources = {"src/helper.ts": "export function helper(){return 1;}", "src/helper.js": "export function helper(){return 2;}",
                   "src/reexport.ts": "export {helper} from './helper.ts';",
                   "src/main.ts": "import * as ns from './helper.ts'; import {helper} from './helper'; import {helper as external} from 'external'; import {helper as reexport} from './reexport'; function run(){ns.helper=other; return ns.helper()+helper()+external()+reexport();}"}
        self.assertEqual(dict(self.graph(sources).edges), {})

    def test_es_exports_require_actual_export_aliases_and_default_binding(self):
        sources = {"src/helper.ts": "function privateFn(){return 1;} function local(){return 2;} export {local as alias}; export default local;",
                   "src/main.ts": "import {privateFn, alias} from './helper'; import defaultFn from './helper'; function run(){return privateFn()+alias()+defaultFn();}"}
        self.assertEqual(dict(self.graph(sources).edges), {("src/main.ts", "run"): [("src/helper.ts", "local")]})

    def test_commonjs_relative_namespace_destructured_member_and_export_assignments(self):
        sources = {"src/helper.cjs": "function local(){return 1;} exports.fn=local; module.exports.other=()=>2;",
                   "src/main.js": "const {fn:alias}=require('./helper.cjs'); const ns=require('./helper.cjs'); const f=require('./helper.cjs').other; function run(){return alias()+ns.fn()+f();}"}
        self.assertEqual(dict(self.graph(sources).edges), {("src/main.js", "run"): [("src/helper.cjs", "local"), ("src/helper.cjs", "module.exports.other")]})
        sources["src/helper.cjs"] = "function local(){return 1;} module.exports={fn:local, other(){return 2;}};"
        self.assertEqual(dict(self.graph(sources).edges), {("src/main.js", "run"): [("src/helper.cjs", "local"), ("src/helper.cjs", "module.exports.other")]})
        sources = {"src/helper.js": "module.exports=()=>1;", "src/main.js": "const fn=require('./helper'); function run(){return fn();}"}
        self.assertEqual(dict(self.graph(sources).edges), {("src/main.js", "run"): [("src/helper.js", "module.exports")]})

    def test_commonjs_shadowed_require_dynamic_exports_and_reassigned_imports_unresolved(self):
        cases = [
            ("exports.fn=()=>1;", "function run(require){const ns=require('./helper'); return ns.fn();}"),
            ("exports.fn=()=>1;", "const ns=require('./helper'); function run(){ns.fn=other; return ns.fn();}"),
            ("exports.fn=()=>1; exports.fn=()=>2;", "const ns=require('./helper'); function run(){return ns.fn();}"),
            ("exports.fn=()=>1; function mutate(){exports.fn=other;}", "const ns=require('./helper'); function run(){return ns.fn();}"),
            ("exports.fn=()=>1; module.exports={fn:()=>2};", "const ns=require('./helper'); function run(){return ns.fn();}"),
            ("exports.fn=()=>1; exports[key]=other;", "const ns=require('./helper'); function run(){return ns.fn();}"),
            ("const exports=other; exports.fn=()=>1;", "const ns=require('./helper'); function run(){return ns.fn();}"),
            ("exports.fn=()=>1;", "const ns=require('./helper'); require=other; function run(){return ns.fn();}"),
        ]
        for helper, main in cases:
            with self.subTest(helper=helper, main=main):
                self.assertEqual(dict(self.graph({"src/helper.js": helper, "src/main.js": main}).edges), {})

    def test_block_scoped_functions_have_distinct_source_targets(self):
        source = "function helper(){return 1;}\nfunction run(){\n if(flag){\n  const helper=()=>2;\n  helper();\n }\n return helper();\n}\n"
        edges = self.graph({"src/code.js": source}).edges
        self.assertEqual(edges[("src/code.js", "run")], [("src/code.js", "run.<block@3:10>.helper"), ("src/code.js", "helper")])

    def test_runtime_initializers_reference_lexical_helpers_without_instance_dispatch(self):
        source = "function setup(){return 1;}\nconst initialized=setup();\nclass C{ field=setup(); static{setup();} method(){return this.field();} }\nsetup();\n"
        self.assertEqual(dict(self.graph({"src/code.js": source}).edges), {
            ("src/code.js", "<module>.initialized"): [("src/code.js", "setup")],
            ("src/code.js", "C.field"): [("src/code.js", "setup")],
            ("src/code.js", "C.<static:1>"): [("src/code.js", "setup")],
            ("src/code.js", "<module>"): [("src/code.js", "setup")]})

    def test_namespace_lexical_bindings_do_not_escape_to_module_scope(self):
        source = "namespace N{export function helper(){return 1;} export function run(){return helper();}} function outside(){return helper();}"
        self.assertEqual(dict(self.graph({"src/code.ts": source}).edges), {
            ("src/code.ts", "N.run"): [("src/code.ts", "N.helper")]})


@unittest.skipUnless(PARSERS_AVAILABLE, "Install the pinned languages extra")
class LanguageBackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        (self.repo / "src").mkdir()
        self.path = self.repo / "src/code.ts"
        self.path.write_bytes(b"export function run() {\r\n return 'saved';\r\n}\r\n")
        self.embedder = RecordingEmbedder()
        self.backend = SearchBackend(self.repo, "src", self.repo / "index.sqlite3", embedder=self.embedder)
        self.addCleanup(self.backend.close)

    def test_mixed_languages_and_exact_source_rename_delete_persistence_and_vector_reuse(self):
        (self.repo / "src/code.py").write_text("def python_run():\n    return 1\n")
        initial = self.backend.refresh()
        self.assertEqual(initial["total_windows"], 2)
        self.assertEqual(len(self.embedder.documents), 1)
        self.assertTrue(self.backend.refresh()["memory_cache_hit"])
        self.backend.close()
        reloaded = SearchBackend(self.repo, "src", self.repo / "index.sqlite3", embedder=self.embedder)
        self.addCleanup(reloaded.close)
        self.assertEqual(reloaded.refresh()["indexed_windows"], 0)
        self.assertEqual(len(self.embedder.documents), 1)
        self.path.write_text("export function run() {return 'changed';}\n")
        self.assertEqual(reloaded.refresh()["indexed_windows"], 1)
        self.path.rename(self.repo / "src/renamed.ts")
        renamed = reloaded.search("changed", retrieval="bm25")
        self.assertEqual(renamed["results"][0]["path"], "src/renamed.ts")
        self.assertEqual(renamed["results"][0]["source"], "export function run() {return 'changed';}\n")
        (self.repo / "src/renamed.ts").unlink()
        self.assertEqual(reloaded.refresh()["total_windows"], 1)

    def test_invalid_saved_snapshot_is_atomic_then_recovers_and_rejects_racing_edits(self):
        self.backend.refresh()
        before = self.backend.status()["store"]
        (self.repo / "src/bad.ts").write_text("function valid(){return 2;}\nfunction broken( {")
        with self.assertRaisesRegex(SourceSnapshotError, "Cannot parse saved source src/bad.ts"):
            self.backend.search("saved", retrieval="bm25")
        self.assertFalse(self.backend.status()["ready"])
        self.assertEqual(self.backend.status()["store"], before)
        (self.repo / "src/bad.ts").unlink()
        self.path.write_text("export function run(){return 'changed';}\n")
        self.embedder.after_documents = lambda: self.path.write_text("export function run(){return 'raced';}\n")
        with self.assertRaises(SourceSnapshotError):
            self.backend.refresh()
        self.assertEqual(self.backend.status()["store"], before)
        self.embedder.after_documents = None
        self.assertIn("raced", self.backend.search("raced")["results"][0]["source"])

    def test_extractor_policy_and_exclusions_affect_manifest_reusing_identical_vectors(self):
        self.backend.refresh()
        initial = self.backend.status()["source_manifest"]
        changed = {**extractor_fingerprint(), "policy": "changed-for-fixture"}
        with patch("semantic_search.backend.extractor_fingerprint", return_value=changed):
            refresh = self.backend.refresh()
            self.assertNotEqual(refresh["source_manifest"], initial)
            self.assertEqual(refresh["indexed_windows"], 0)
        self.assertEqual(len(self.embedder.documents), 1)
        excluded = SearchBackend(self.repo, "src", embedder=self.embedder, source_exclusions=["*.ts"])
        self.addCleanup(excluded.close)
        self.assertEqual(excluded.refresh()["total_windows"], 0)
        self.assertEqual(excluded.status()["source_exclusions"], ["*.ts"])
        self.assertNotEqual(excluded.status()["source_manifest"], initial)
        self.assertNotEqual(excluded.db_path, SearchBackend(self.repo, "src").db_path)
        for invalid in ("*.ts", ["../outside/*"], [""], [None]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SearchBackend(self.repo, "src", source_exclusions=invalid)

    def test_git_branch_switch_changes_manifest_without_reembedding_identical_source(self):
        def git(*args):
            return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()
        git("init", "-q")
        git("add", "src/code.ts")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "initial")
        initial = self.backend.refresh()["source_manifest"]
        git("checkout", "-qb", "other")
        refreshed = self.backend.refresh()
        self.assertNotEqual(initial, refreshed["source_manifest"])
        self.assertEqual(refreshed["indexed_windows"], 0)
        self.assertEqual(len(self.embedder.documents), 1)


if __name__ == "__main__":
    unittest.main()
