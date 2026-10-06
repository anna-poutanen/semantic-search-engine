"""Stdlib tests: portable invariants plus optional isolated grammar fixtures."""
import hashlib
import importlib.util
import sys
import unittest
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import SimpleNamespace

SPEC = importlib.util.spec_from_file_location("csharp_prototype", Path(__file__).resolve().parents[1] / "scripts/csharp_extractor_prototype.py")
prototype = importlib.util.module_from_spec(SPEC)
# dataclasses require the dynamically loaded module to be present in sys.modules.
sys.modules[SPEC.name] = prototype
SPEC.loader.exec_module(prototype)


def available():
    try:
        return all(version(name) == expected for name, expected in prototype.PINS.items())
    except PackageNotFoundError:
        return False


class PortableInvariantTests(unittest.TestCase):
    def test_physical_lines_preserve_bom_crlf_cr_lf_and_unicode_data(self):
        source = '\ufeffa\r\nb\rc\n"x\u2028y"'
        self.assertEqual(prototype.physical_lines(source), ['\ufeffa\r\n', 'b\r', 'c\n', '"x\u2028y"'])
        self.assertEqual(''.join(prototype.physical_lines(source)), source)

    def test_long_windows_cover_all_body_lines_with_exact_bounds_bytes_ids_hashes(self):
        source = "\r\n".join("line" + str(i) for i in range(405)) + "\r\n"
        raw = source.encode()
        definition = prototype.Definition("Ns.Type.F#sig", "method_declaration", 0, len(raw), 3, len(raw) - 2)
        windows = prototype.windows_for(source, "src/code.cs", [definition])
        self.assertEqual([(w.start_line, w.end_line) for w in windows], [(1,160),(121,280),(241,400),(361,405)])
        self.assertEqual(set(range(1,406)), {n for w in windows for n in range(w.start_line,w.end_line+1)})
        self.assertEqual(len(windows), len({w.id for w in windows}))
        lines = prototype.physical_lines(source)
        for window in windows:
            self.assertEqual(window.text, ''.join(lines[window.start_line-1:window.end_line]))
            self.assertEqual(window.content_hash, hashlib.sha256(window.text.encode()).hexdigest())
            self.assertEqual(window.id, f'src/code.cs:{definition.symbol}:{window.start_line}-{window.end_line}')

    def test_duplicate_branch_symbols_fail_instead_of_deduplicating(self):
        first = prototype.Definition("C.F#same", "method_declaration", 0, 3, 1, 2)
        second = prototype.Definition("C.F#same", "method_declaration", 4, 7, 5, 6)
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            prototype.reject_ambiguous_symbols([first, second])

    def test_signature_identity_distinguishes_overloads_and_enclosing_types(self):
        one = prototype.member_symbol(("Ns",), ("Outer", "Inner`1"), "method", "Run", [("type","int")])
        two = prototype.member_symbol(("Ns",), ("Outer", "Inner`1"), "method", "Run", [("type","string")])
        self.assertTrue(one.startswith("Ns.Outer.Inner`1.Run#"))
        self.assertNotEqual(one, two)
        self.assertEqual(one, prototype.member_symbol(("Ns",), ("Outer", "Inner`1"), "method", "Run", [("type","int")]))

    def test_window_and_path_faults_reject(self):
        for path in ('../code.cs', '/code.cs', 'code.csx', 'dir\\code.cs', '', './code.cs',
                     'dir/./code.cs', 'dir//code.cs', 'bad\0.cs', 'bad\n.cs', 'bad\u2028.cs'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                prototype.validate_path(path)
        for size, overlap in ((True,0),(160,True),(0,0),(160,160),(160,-1)):
            with self.subTest(size=size,overlap=overlap), self.assertRaises(ValueError):
                prototype.windows_for('x','code.cs',[],size,overlap)
        bad = prototype.Definition('bad','method',0,4,1,5)
        with self.assertRaises(ValueError):
            prototype.windows_for('code','code.cs',[bad])
        for offset in (True, 0.0):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                prototype.windows_for('code','code.cs',[prototype.Definition('bad','method',offset,4,1,3)])

    def test_root_error_rejected_even_without_explicit_error_nodes(self):
        parser = SimpleNamespace(parse=lambda raw: SimpleNamespace(root_node=SimpleNamespace(has_error=True)))
        with self.assertRaisesRegex(SyntaxError, "grammar errors"):
            prototype.parse_definitions('broken',parser)


@unittest.skipUnless(available(), "Actual C# grammar tests require the isolated pinned optional environment")
class GrammarDevelopmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = prototype.load_parser()

    def extract(self, source):
        return prototype.extract(source,'src/code.cs',self.parser)

    def test_attributes_expression_nonempty_block_and_constructor_initializer(self):
        source = '''namespace Demo {
class Parent { public Parent(int value) {} }
class C : Parent {
 [Mark]
 public C(int value) : base(value) {}
 [Test]
 public int Twice(int value) => value * 2;
 public int Add(int value) { return value + 1; }
}
}
'''
        definitions, windows = self.extract(source)
        self.assertEqual(len(definitions),3)
        self.assertEqual([(w.start_line,w.end_line) for w in windows],[(4,5),(6,7),(8,8)])
        self.assertTrue(all(d.symbol.startswith('Demo.C.') for d in definitions))
        self.assertTrue(any('[Mark]' in w.text and ': base(value)' in w.text for w in windows))
        self.assertTrue(any('[Test]' in w.text and '=> value * 2;' in w.text for w in windows))
        self.assertTrue(all(w.text==''.join(prototype.physical_lines(source)[w.start_line-1:w.end_line]) for w in windows))

    def test_file_scoped_nested_generic_overloads_and_body_edit_stability(self):
        source = 'namespace Demo.Space;\nclass Outer<T> { class Inner { int Echo(int value)=>value; string Echo(string value)=>value; } }'
        before, _ = self.extract(source)
        after, _ = self.extract(source.replace('=>value;', ' => value ;'))
        self.assertEqual(len(before),2)
        self.assertNotEqual(before[0].symbol,before[1].symbol)
        self.assertTrue(all(d.symbol.startswith('Demo.Space.Outer`1.Inner.Echo#') for d in before))
        self.assertEqual([d.symbol for d in before],[d.symbol for d in after])
        spaced, _ = self.extract(source.replace('(int value)','( int /*same*/ value )'))
        self.assertEqual([d.symbol for d in before],[d.symbol for d in spaced])

    def test_declarations_empty_bodies_and_deferred_forms_not_separate_implementations(self):
        source = '''abstract class C {
 public abstract int Absent();
 public static extern int External();
 public void Empty() {}
 public void CommentOnly() { /*empty*/ ; }
 public int P { get { return 1; } }
 public static C operator +(C left,C right) => left;
 public int Outer() { int Local() => 1; System.Func<int> f = () => 2; return Local()+f(); }
}
interface I { int Declaration(); }
'''
        definitions,_ = self.extract(source)
        self.assertEqual(len(definitions),1)
        self.assertTrue(definitions[0].symbol.startswith('C.Outer#'))

    def test_nested_namespace_and_bom_crlf_unicode_whole_line_preservation(self):
        source = '\ufeffnamespace A { namespace B {\r\n class C {\r\n  public string Text() { return "café😎\u2028data"; }\r\n }\r\n}}\r\n'
        definitions,windows = self.extract(source)
        self.assertEqual(len(definitions),1)
        self.assertTrue(definitions[0].symbol.startswith('A.B.C.Text#'))
        self.assertEqual([(w.start_line,w.end_line)for w in windows],[(3,3)])
        self.assertEqual(windows[0].text,'  public string Text() { return "café😎\u2028data"; }\r\n')

    def test_actual_long_body_windows_cover_every_body_line(self):
        source = 'class C {\n int Long() {\n int value = 0;\n' + (' value += 1;\n'*330) + ' return value;\n }\n}\n'
        definitions,windows = self.extract(source)
        self.assertEqual(len(definitions),1)
        self.assertEqual([(w.start_line,w.end_line)for w in windows],[(2,161),(122,281),(242,335)])
        self.assertTrue(set(range(2,336)).issubset({line for w in windows for line in range(w.start_line,w.end_line+1)}))
        self.assertTrue(all(w.text==''.join(prototype.physical_lines(source)[w.start_line-1:w.end_line]) for w in windows))

    def test_preprocessor_branches_retain_unique_methods_and_reject_ambiguous_same_signature(self):
        unique='class C {\n#if FEATURE\n int First() => 1;\n#else\n int Second() => 2;\n#endif\n}\n'
        definitions,_=self.extract(unique)
        self.assertEqual(len(definitions),2)
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            self.extract(unique.replace('First','Same').replace('Second','Same'))

    def test_invalid_root_error_and_utf8_rejected(self):
        with self.assertRaises(SyntaxError):
            self.extract('class C { int Broken() { return value + ; } }')
        with self.assertRaises(UnicodeDecodeError):
            self.extract(b'\xffclass C {}'.decode('utf-8'))

    def test_directive_only_bodies_omit_condition_names_but_keep_branch_statements(self):
        empty = 'class C { void M() {\n#if DEBUG\n// comment\n#else\n// another comment\n#endif\n} }'
        definitions, _ = self.extract(empty)
        self.assertEqual(definitions, [])
        for source in (empty.replace('// comment', 'return;'), empty.replace('// another comment', 'return;')):
            with self.subTest(source=source):
                definitions, _ = self.extract(source)
                self.assertEqual(len(definitions), 1)


if __name__ == '__main__':
    unittest.main()
