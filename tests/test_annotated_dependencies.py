"""Invented, in-memory source only; no model, corpus or cache access."""
import ast
from dataclasses import replace
import hashlib
import json

import pytest

from experiments.dependency_context.annotated_dependencies import AnnotatedDependencies, SCHEMA
from semantic_search.corpus import extract_snippets
from semantic_search.relations import ReferenceGraph


CLASS = "class Engine:\n    def render(self, value=0):\n        return str(value)\n"
CALLER = "def use(receiver: Engine):\n    return receiver.render(1)\n"
PATH = "src/pkg/api.py"


def inputs(tmp_path, sources, *, max_lines=160):
    keys = {}
    snippets = extract_snippets(tmp_path, source_root="src", sources=sources,
                                max_lines=max_lines, overlap_lines=0, definition_keys=keys)
    return snippets, keys


def graph(tmp_path, source=CLASS + CALLER, *, sources=None, max_lines=160, **kwargs):
    sources = sources if sources is not None else {PATH: source}
    snippets, keys = inputs(tmp_path, sources, max_lines=max_lines)
    return AnnotatedDependencies(sources, snippets, definition_keys=keys, **kwargs)


def expected(caller="use", method="Engine.render", path=PATH, target_path=PATH):
    return {(path, caller): ((target_path, method),)}


def test_original_graph_unchanged_and_exact_literal_witnesses(tmp_path):
    sources = {PATH: CLASS + CALLER}
    snippets, keys = inputs(tmp_path, sources)
    original = ReferenceGraph(sources, snippets)
    before = dict(original.edges)
    result = AnnotatedDependencies(sources, snippets, definition_keys=keys)
    assert result.edges == expected()
    assert dict(original.edges) == before
    assert original.edges.get((PATH, "use"), []) == []
    fact, = result.receipt["accepted"]
    assert result.receipt["schema"] == SCHEMA
    assert result.receipt["runtime_dispatch_proven"] is False
    assert fact["call"]["source"] == "receiver.render(1)"
    assert fact["annotation"]["source"] == "Engine"
    assert fact["class"] == {"path": PATH, "symbol": "Engine"}
    for endpoint in (fact["caller"], fact["callee"]):
        assert endpoint["full_definition"] is True
        assert endpoint["definition_sha256"] == endpoint["source_sha256"] == hashlib.sha256(
            endpoint["source"].encode()).hexdigest()
        assert endpoint["definition_key"] == list(keys[endpoint["window_ids"][0]])
    for witness in (fact["annotation"], fact["call"], *fact["resolution"]):
        raw = sources[witness["path"]].encode()[witness["start_byte"]:witness["end_byte"]]
        assert raw.decode() == witness["source"]
        assert hashlib.sha256(raw).hexdigest() == witness["sha256"]
    assert json.loads(json.dumps(result.receipt, allow_nan=False)) == result.receipt


@pytest.mark.parametrize("annotation", ["Engine", "'Engine'", '"Engine"', "'''Engine'''"])
def test_simple_and_quoted_annotations(tmp_path, annotation):
    assert graph(tmp_path, CLASS + CALLER.replace("Engine):", f"{annotation}):")).edges == expected()


@pytest.mark.parametrize("class_name,parameter,method", [("Renamed", "item", "emit"), ("Café", "élément", "rendre")])
def test_renaming_and_unicode_do_not_change_rule(tmp_path, class_name, parameter, method):
    source = CLASS.replace("Engine", class_name).replace("render", method)
    source += CALLER.replace("Engine", class_name).replace("receiver", parameter).replace("render", method)
    assert graph(tmp_path, source).edges == expected(method=f"{class_name}.{method}")


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_witness_offsets_are_literal_physical_bytes(tmp_path, newline):
    source = ("# é\n" + CLASS + CALLER.replace("return receiver.render(1)", "return (\n        receiver.render(\n            'é'\n        )\n    )")).replace("\n", newline)
    result = graph(tmp_path, source)
    assert result.edges == expected()
    fact, = result.receipt["accepted"]
    call = fact["call"]
    assert source.encode()[call["start_byte"]:call["end_byte"]].decode() == call["source"]
    assert call["end_line"] - call["start_line"] == 2


@pytest.mark.parametrize("imports,annotation", [
    ("from .tools import Engine\n", "Engine"),
    ("from .tools import Engine as Alias\n", "Alias"),
    ("import pkg.tools as helpers\n", "helpers.Engine"),
    ("from . import tools as helpers\n", "helpers.Engine"),
    ("from pkg.tools import Engine as Alias\n", "'Alias'"),
    ("import pkg.tools as helpers\n", "'helpers.Engine'"),
])
def test_import_aliases_are_resolved_with_source_witnesses(tmp_path, imports, annotation):
    sources = {"src/pkg/tools.py": CLASS, PATH: imports + CALLER.replace("Engine):", f"{annotation}):")}
    result = graph(tmp_path, sources=sources)
    assert result.edges == expected(target_path="src/pkg/tools.py")
    fact, = result.receipt["accepted"]
    assert fact["resolution"][0]["path"] == PATH
    assert any(witness["source"].startswith("class Engine") for witness in fact["resolution"])


def test_explicit_reexport_chain(tmp_path):
    sources = {"src/pkg/tools.py": CLASS, "src/pkg/__init__.py": "from .tools import Engine as Public\n",
               PATH: "from pkg import Public as Local\n" + CALLER.replace("Engine):", "Local):")}
    result = graph(tmp_path, sources=sources)
    assert result.edges == expected(target_path="src/pkg/tools.py")
    assert len(result.receipt["accepted"][0]["resolution"]) == 3


def test_submodule_import_with_inventoried_package_and_package_shadowing(tmp_path):
    sources = {"src/pkg/tools.py": CLASS, "src/pkg/__init__.py": "",
               PATH: "from . import tools as helpers\n" + CALLER.replace("Engine):", "helpers.Engine):")}
    assert graph(tmp_path, sources=sources).edges == expected(target_path="src/pkg/tools.py")
    sources["src/pkg/__init__.py"] = "tools = other\n"
    assert graph(tmp_path, sources=sources).edges == {}


@pytest.mark.parametrize("mutation", [
    "helpers.Engine = Other", "del helpers.Engine", "helpers.Engine += 1",
    "setattr(helpers, 'Engine', Other)", "delattr(helpers, 'Engine')", "setattr(helpers, name, Other)",
    "helpers.__dict__['Engine'] = Other", "vars(helpers)['Engine'] = Other",
    "alias = helpers", "aliases = [helpers]", "mutate(helpers)", "mutate(module=helpers)",
    "def later():\n    return helpers", "def later():\n    mutate(helpers)",
])
def test_imported_module_export_mutation_and_escape_declines(tmp_path, mutation):
    sources = {"src/pkg/tools.py": CLASS,
        PATH: "import pkg.tools as helpers\n" + CALLER.replace("Engine):", "helpers.Engine):") + mutation + "\n"}
    assert graph(tmp_path, sources=sources).edges == {}


@pytest.mark.parametrize("mutation", [
    "package.Engine = Other", "del package.Engine", "setattr(package, name, Other)",
    "alias = package", "mutate(package)",
])
def test_escaping_reexport_namespace_taints_original_class_across_import_paths(tmp_path, mutation):
    sources = {"src/pkg/tools.py": CLASS, "src/pkg/__init__.py": "from .tools import Engine\n",
        "src/pkg/patcher.py": "import pkg as package\n" + mutation + "\n",
        PATH: "from .tools import Engine\n" + CALLER}
    assert graph(tmp_path, sources=sources).edges == {}


@pytest.mark.parametrize("mutation", [
    "aliases = [Engine]", "aliases = {'type': Engine}", "alias, = (Engine,)",
    "mutate(Engine)", "mutate(type=Engine)", "Engine.__dict__['render'] = other",
    "exec('Engine.render = replacement')", "globals()['Engine'] = Other",
])
def test_class_container_aliases_unknown_escapes_and_namespace_mutation_decline(tmp_path, mutation):
    assert graph(tmp_path, CLASS + CALLER + mutation + "\n").edges == {}


def test_builtin_typecheck_and_constructor_uses_are_not_escapes(tmp_path):
    source = CLASS + CALLER + "def other(value):\n    item = Engine()\n    return isinstance(value, (Engine, Engine))\n"
    assert graph(tmp_path, source).edges == expected()
    assert graph(tmp_path, source + "def isinstance(*args):\n    mutate(args)\n").edges == {}
    assert graph(tmp_path, source + "def shadow(isinstance):\n    return isinstance(value, Engine)\n").edges == {}


def test_mutated_builtin_namespace_cannot_make_typecheck_escape_look_safe(tmp_path):
    sources = {PATH: CLASS + CALLER + "def other(value):\n    return isinstance(value, Engine)\n",
        "src/zpatch.py": "import builtins\nbuiltins.isinstance = replacement\n"}
    assert graph(tmp_path, sources=sources).edges == {}


def test_class_body_dynamic_namespace_declines(tmp_path):
    source = CLASS + "    locals()['render'] = replacement\n" + CALLER
    assert graph(tmp_path, source).edges == {}


@pytest.mark.parametrize("body", [
    "return locals()", "state = locals()\n    return list(state)", "exec(code)",
    "exec(code, {})", "namespace = {}\n    exec(code, {}, namespace)\n    return namespace",
    "return eval(code, {})",
])
def test_unrelated_function_local_inspection_or_isolated_codegen_does_not_taint_module(tmp_path, body):
    source = CLASS + CALLER + "def generator(code):\n    " + body + "\n"
    assert graph(tmp_path, source).edges == expected()


@pytest.mark.parametrize("statement", ["exec(code)", "eval(code)", "namespace = locals()", "locals()['render'] = other"])
def test_immediate_class_shared_namespace_uncertainty_declines(tmp_path, statement):
    source = CLASS + "    " + statement + "\n" + CALLER
    assert graph(tmp_path, source).edges == {}


@pytest.mark.parametrize("statement", ["exec(code, {})", "eval(code, {})", "locals()", "value = locals()['render']"])
def test_class_isolated_codegen_or_non_escaping_namespace_read_is_not_module_taint(tmp_path, statement):
    source = CLASS + "    " + statement + "\n" + CALLER
    assert graph(tmp_path, source).edges == expected()


@pytest.mark.parametrize("declaration", [
    "class Other(eval(code)):\n    pass\n",
    "@eval(code)\nclass Other:\n    pass\n",
    "def other(value=eval(code)):\n    return value\n",
])
def test_definition_headers_use_surrounding_namespace(tmp_path, declaration):
    assert graph(tmp_path, CLASS + CALLER + declaration).edges == {}


def test_explicit_module_globals_mapping_write_in_function_declines(tmp_path):
    source = CLASS + CALLER + "def mutate():\n    globals()['Engine'] = other\n"
    assert graph(tmp_path, source).edges == {}


@pytest.mark.parametrize("binding", [
    "Engine = object\n", "del Engine\n", "Engine += 1\n", "Engine: object\n",
    "if False:\n    Engine = object\n", "for Engine in []:\n    pass\n",
    "try:\n    pass\nexcept Exception as Engine:\n    pass\n",
    "match None:\n    case {'class': Engine}:\n        pass\n",
    "def mutate():\n    global Engine\n    Engine = object\n",
    "from external import *\n", "from external import Engine\n",
    "[(Engine := object) for value in []]\n",
    "class Engine:\n    def render(self):\n        return 2\n",
])
def test_any_late_class_rebinding_or_ambiguity_declines(tmp_path, binding):
    assert graph(tmp_path, CLASS + CALLER + binding).edges == {}


@pytest.mark.parametrize("imports", [
    "import pkg.tools\n", "from ...tools import Engine\n", "from missing import Engine\n",
    "if True:\n    from .tools import Engine\n", "from .tools import Engine\nEngine = object\n",
])
def test_uncertain_import_semantics_decline(tmp_path, imports):
    sources = {"src/pkg/tools.py": CLASS, PATH: imports + CALLER}
    assert graph(tmp_path, sources=sources).edges == {}


def test_cyclic_exports_and_ambiguous_module_paths_decline(tmp_path):
    cycle = {"src/pkg/a.py": "from .b import Engine\n", "src/pkg/b.py": "from .a import Engine\n",
             PATH: "from .a import Engine\n" + CALLER}
    assert graph(tmp_path, sources=cycle).edges == {}
    collision = {"src/pkg/tools.py": CLASS, "src/pkg/tools/__init__.py": CLASS,
                 PATH: "from .tools import Engine\n" + CALLER}
    assert graph(tmp_path, sources=collision).edges == {}
    caller_collision = {"src/pkg/tools.py": CLASS, PATH: "from .tools import Engine\n" + CALLER,
                        "src/pkg/api/__init__.py": ""}
    assert graph(tmp_path, sources=caller_collision).edges == {}


@pytest.mark.parametrize("annotation", ["list[Engine]", "Engine | None", "'list[Engine]'", "'Engine | None'",
    "'factory()'", "'\"Engine\"'", "42", "None", "'Engine['", "'Engine; run()'", "' Engine '", "factory()"])
def test_unsupported_annotations_are_not_evaluated(tmp_path, annotation):
    assert graph(tmp_path, CLASS + CALLER.replace("Engine):", f"{annotation}):")).edges == {}


@pytest.mark.parametrize("statement", [
    "receiver = object()", "del receiver", "receiver += 1", "receiver: object", "(receiver := object())",
    "for receiver in []:\n        pass", "with context() as receiver:\n        pass",
    "try:\n        pass\n    except Exception as receiver:\n        pass",
    "match None:\n        case {'x': receiver}:\n            pass",
    "match None:\n        case [*receiver]:\n            pass",
    "global receiver", "nonlocal receiver", "import module as receiver", "from module import receiver",
    "def receiver():\n        return 1", "class receiver:\n        pass",
])
def test_late_receiver_bindings_block_entire_scope(tmp_path, statement):
    source = CLASS + CALLER + "    " + statement + "\n"
    assert graph(tmp_path, source).edges == {}


@pytest.mark.parametrize("statement", [
    "alias = receiver", "return receiver", "consume(receiver)", "consume(value=receiver)",
    "receiver.render = lambda: 1", "del receiver.render", "receiver.render += 1",
    "alias = receiver.render", "getattr(receiver, 'render')()", "setattr(receiver, 'render', replacement)",
    "receiver.__setattr__('render', replacement)", "receiver.__delattr__('render')",
    "receiver.__dict__['render'] = replacement", "receiver.other.render()", "return [receiver]",
])
def test_alias_escape_and_dynamic_receiver_attributes_decline(tmp_path, statement):
    assert graph(tmp_path, CLASS + CALLER + "    " + statement + "\n").edges == {}


@pytest.mark.parametrize("nested", [
    "def inner():\n        return receiver.render(2)",
    "def inner():\n        nonlocal receiver\n        receiver = object()",
    "inner = lambda: receiver.render(2)",
    "result = [receiver.render() for _ in []]",
    "result = [receiver.render() for receiver in []]",
    "class Inner:\n        value = receiver.render()",
])
def test_nested_receivers_never_leak_or_mutate_outer_binding_unnoticed(tmp_path, nested):
    assert graph(tmp_path, CLASS + CALLER + "    " + nested + "\n").edges == {}


def test_nested_function_with_own_annotation_is_initially_unsupported(tmp_path):
    source = CLASS + "def outer():\n    def inner(receiver: Engine):\n        return receiver.render()\n    return inner\n"
    result = graph(tmp_path, source)
    assert result.edges == {}
    assert any(fact["reason"] == "nested_conditional_or_unindexed_caller" for fact in result.receipt["declined"])


@pytest.mark.parametrize("body", [
    "if flag:\n        return receiver.render()\n    return receiver.render(2)",
    "for index in values:\n        receiver.render(index)\n    return 1",
    "try:\n        return receiver.render()\n    finally:\n        done()",
    "return f'{receiver.render()}'",
])
def test_control_flow_without_rebinding_keeps_literal_direct_calls(tmp_path, body):
    result = graph(tmp_path, CLASS + "def use(receiver: Engine):\n    " + body + "\n")
    assert result.edges == expected()
    assert result.receipt["accepted"]


@pytest.mark.parametrize("change", [
    "Engine.render = lambda *args: 1\n", "del Engine.render\n", "Engine.render += 1\n",
    "Alias = Engine\n", "setattr(Engine, 'render', replacement)\n", "delattr(Engine, name)\n",
    "import builtins\nbuiltins.setattr(Engine, name, replacement)\n",
    "def later():\n    Engine.render = replacement\n",
])
def test_class_aliases_and_method_monkeypatches_decline_even_later(tmp_path, change):
    assert graph(tmp_path, CLASS + CALLER + change).edges == {}


@pytest.mark.parametrize("class_source", [
    "@decorator\n" + CLASS,
    CLASS.replace("class Engine:", "class Engine(Base):"),
    CLASS.replace("class Engine:", "class Engine(object):"),
    CLASS.replace("class Engine:", "class Engine(metaclass=Meta):"),
    CLASS + "    render = replacement\n",
    CLASS + "    if flag:\n        render = replacement\n",
    CLASS + "    def render(self):\n        return 2\n",
    CLASS + "    def __getattribute__(self, name):\n        return lookup(name)\n",
    CLASS + "    def __getattr__(self, name):\n        return lookup(name)\n",
    CLASS + "    def __init__(self):\n        self.render = replacement\n",
    CLASS + "    def __init__(self):\n        setattr(self, name, replacement)\n",
    CLASS + "    def __init__(self):\n        object.__setattr__(self, 'render', replacement)\n",
    CLASS + "    def __init__(self):\n        self.__setattr__('render', replacement)\n",
    CLASS + "    def __init__(self):\n        alias = self\n        alias.render = replacement\n",
    CLASS.replace("    def render", "    @property\n    def render"),
    CLASS.replace("    def render", "    @classmethod\n    def render"),
])
def test_class_dispatch_uncertainty_declines(tmp_path, class_source):
    assert graph(tmp_path, class_source + CALLER).edges == {}


def test_unrelated_instance_fields_do_not_invent_method_overrides(tmp_path):
    source = CLASS + "    def __init__(self):\n        self.value = 1\n" + CALLER
    assert graph(tmp_path, source).edges == expected()


def test_static_method_target_and_caller_methods(tmp_path):
    source = CLASS.replace("    def render(self,", "    @staticmethod\n    def render(")
    source += "class Caller:\n    @staticmethod\n    def use(receiver: Engine):\n        return receiver.render(1)\n"
    assert graph(tmp_path, source).edges == expected("Caller.use")
    source = CLASS + "class Caller:\n    def use(self, receiver: Engine):\n        return receiver.render(1)\n"
    assert graph(tmp_path, source).edges == expected("Caller.use")


@pytest.mark.parametrize("shadow", ["staticmethod = decorator\n", "class staticmethod:\n    pass\n"])
def test_staticmethod_builtin_shadowing_declines(tmp_path, shadow):
    source = shadow + CLASS.replace("    def render(self,", "    @staticmethod\n    def render(") + CALLER
    assert graph(tmp_path, source).edges == {}


def test_staticmethod_builtin_namespace_mutation_declines(tmp_path):
    source = CLASS.replace("    def render(self,", "    @staticmethod\n    def render(") + CALLER
    sources = {PATH: source, "src/zpatch.py": "import builtins\nbuiltins.staticmethod = replacement\n"}
    assert graph(tmp_path, sources=sources).edges == {}


def test_class_namespace_cannot_silently_shadow_annotation(tmp_path):
    source = CLASS + "class Caller:\n    Engine = Other\n    def use(self, receiver: Engine):\n        return receiver.render(1)\n"
    assert graph(tmp_path, source).edges == {}


@pytest.mark.parametrize("deferred", ["quote", "future"])
def test_forward_class_annotation_requires_explicit_deferred_form(tmp_path, deferred):
    assert graph(tmp_path, CALLER + CLASS).edges == {}
    source = CALLER.replace("Engine):", "'Engine'):") + CLASS if deferred == "quote" else "from __future__ import annotations\n" + CALLER + CLASS
    assert graph(tmp_path, source).edges == expected()


def test_late_import_requires_explicit_deferred_annotation(tmp_path):
    sources = {"src/pkg/tools.py": CLASS, PATH: CALLER + "from .tools import Engine\n"}
    assert graph(tmp_path, sources=sources).edges == {}
    sources[PATH] = CALLER.replace("Engine):", "'Engine'):") + "from .tools import Engine\n"
    assert graph(tmp_path, sources=sources).edges == expected(target_path="src/pkg/tools.py")


@pytest.mark.parametrize("source", [
    CLASS + "def use[Engine](receiver: Engine):\n    return receiver.render()\n",
    CLASS + "class Caller[Engine]:\n    def use(self, receiver: Engine):\n        return receiver.render()\n",
    CLASS.replace("class Engine:", "class Engine[T]:") + CALLER,
])
def test_generic_lexical_type_scopes_are_not_resolved_as_module_classes(tmp_path, source):
    assert graph(tmp_path, source).edges == {}


@pytest.mark.parametrize("signature", ["receiver: Engine, /", "*, receiver: Engine", "receiver: Engine = None"])
def test_positional_keyword_and_default_parameters(tmp_path, signature):
    result = graph(tmp_path, CLASS + f"def use({signature}):\n    return receiver.render()\n")
    assert result.edges == expected()


@pytest.mark.parametrize("signature", ["*receiver: Engine", "**receiver: Engine"])
def test_variadic_type_is_not_the_container_receiver(tmp_path, signature):
    assert graph(tmp_path, CLASS + f"def use({signature}):\n    return receiver.render()\n").edges == {}


def test_async_functions_and_awaited_calls(tmp_path):
    source = CLASS.replace("def render", "async def render") + CALLER.replace("def use", "async def use").replace("return receiver", "return await receiver")
    assert graph(tmp_path, source).edges == expected()


def test_comments_and_strings_are_not_calls_or_rebinding(tmp_path):
    source = CLASS + CALLER + "    # receiver = bad; receiver.render = other\n    text = 'setattr(Engine, render, bad); receiver.render()'\n"
    result = graph(tmp_path, source)
    assert result.edges == expected()
    assert len(result.receipt["accepted"]) == 1


def test_long_definitions_bind_canonical_slice_and_call_windows(tmp_path):
    source = CLASS + "def use(receiver: Engine):\n" + "    value = 1\n" * 12 + "    return receiver.render()\n"
    result = graph(tmp_path, source, max_lines=5)
    fact, = result.receipt["accepted"]
    assert len(fact["caller"]["window_ids"]) == 3
    assert fact["caller"]["source"].startswith("def use")
    assert fact["caller"]["source"].endswith("    return receiver.render()\n")
    assert fact["caller"]["definition_key"][2:] == [4, 17]


def test_decorated_definition_sha_includes_decorator(tmp_path):
    source = CLASS.replace("    def render(self,", "    @staticmethod\n    def render(") + CALLER
    fact, = graph(tmp_path, source).receipt["accepted"]
    assert fact["callee"]["source"].startswith("    @staticmethod\n")
    assert fact["callee"]["definition_key"][2] == 2


def test_deterministic_order_and_detached_receipts(tmp_path):
    sources = {PATH: CLASS + CALLER + "def second(item: Engine):\n    return item.render()\n",
               "src/unused.py": "def other():\n    return 1\n"}
    snippets, keys = inputs(tmp_path, sources)
    left = AnnotatedDependencies(sources, snippets, definition_keys=keys)
    right = AnnotatedDependencies(dict(reversed(list(sources.items()))), list(reversed(snippets)),
                                  definition_keys=dict(reversed(list(keys.items()))))
    assert left.receipt == right.receipt
    assert left.edges == right.edges
    receipt = left.witnesses()
    receipt["accepted"].clear()
    assert len(left.receipt["accepted"]) == 2


def test_graph_lookup_first_window_and_limits(tmp_path):
    sources = {PATH: CLASS + CALLER}
    snippets, keys = inputs(tmp_path, sources)
    result = AnnotatedDependencies(sources, snippets, definition_keys=keys)
    caller = next(s for s in snippets if s.symbol == "use")
    assert [s.symbol for s in result.dependencies(caller)] == ["Engine.render"]
    assert result.dependencies(caller, 0) == []
    for value in (-1, True, 1.5):
        with pytest.raises(ValueError):
            result.dependencies(caller, value)


@pytest.mark.parametrize("mutation", ["text", "hash", "span", "id", "symbol", "duplicate", "key", "key_bool", "key_missing"])
def test_invalid_source_or_canonical_bindings_fail_before_relationships(tmp_path, mutation):
    sources = {PATH: CLASS + CALLER}
    snippets, keys = inputs(tmp_path, sources)
    snippet = snippets[-1]
    if mutation == "text":
        snippets[-1] = replace(snippet, text=snippet.text + "# changed\n")
    if mutation == "hash":
        snippets[-1] = replace(snippet, content_hash="0" * 64)
    if mutation == "span":
        snippets[-1] = replace(snippet, end_line=snippet.end_line + 1)
    if mutation == "id":
        snippets[-1] = replace(snippet, id="invented")
    if mutation == "symbol":
        snippets[-1] = replace(snippet, symbol="wrong")
    if mutation == "duplicate":
        snippets.append(snippet)
    if mutation == "key":
        keys[snippet.id] = [PATH, snippet.symbol, 1, snippet.end_line]
    if mutation == "key_bool":
        keys[snippet.id] = [PATH, snippet.symbol, True, snippet.end_line]
    if mutation == "key_missing":
        del keys[snippet.id]
    with pytest.raises(ValueError):
        AnnotatedDependencies(sources, snippets, definition_keys=keys)


def test_unindexed_method_declines_instead_of_inventing_window(tmp_path):
    sources = {PATH: CLASS + CALLER}
    snippets, keys = inputs(tmp_path, sources)
    snippets = [s for s in snippets if s.symbol == "use"]
    result = AnnotatedDependencies(sources, snippets, definition_keys=keys)
    assert result.edges == {}


def test_malformed_or_non_python_inventory_never_runs_source(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Source must not execute or import")
    monkeypatch.setattr("builtins.exec", forbidden)
    result = AnnotatedDependencies({"src/bad.py": "def bad(\n", "src/other.js": "throw new Error('no execution')"},
                                    [], definition_keys={})
    assert result.edges == {}
    assert result.receipt["declined"] == [{"path": "src/bad.py", "reason": "parse_error"}]


@pytest.mark.parametrize("path,source", [("../escape.py", ""), ("/absolute.py", ""), ("src//file.py", ""), (PATH, 123)])
def test_source_inventory_paths_and_values_are_literal(tmp_path, path, source):
    with pytest.raises(ValueError):
        AnnotatedDependencies({path: source}, [], definition_keys={})


def test_cancellation_propagates_original_callback_exception_mid_walk(tmp_path):
    sources = {PATH: CLASS + CALLER}
    snippets, keys = inputs(tmp_path, sources)
    failure = ValueError("cancelled by invented callback")
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 14:
            raise failure
    with pytest.raises(ValueError) as error:
        AnnotatedDependencies(sources, snippets, definition_keys=keys, cancel_check=check)
    assert error.value is failure


def test_parser_uncertainty_declines_and_does_not_swallow_callback_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(ast, "parse", lambda *args, **kwargs: (_ for _ in ()).throw(RecursionError()))
    result = AnnotatedDependencies({PATH: CLASS + CALLER}, [], definition_keys={})
    assert result.edges == {}
    assert result.receipt["declined"][0]["reason"] == "parse_error"
