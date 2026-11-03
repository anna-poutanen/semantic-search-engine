"""Invented CPU syntax/identity fixtures for the conservative terminal guard."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from semantic_search.cancellation import SearchCancelled


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("role_guard_test_subject", ROOT / "scripts/research_role_context.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def row(source="def invented(obj):\n    return obj.value\n", *, identifier="invented", path="src/example.py", start=11, **metadata):
    end = start + len([line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]) - 1
    digest = hashlib.sha256(source.encode()).hexdigest()
    return {"id": identifier, "path": path, "symbol": identifier, "start_line": start,
        "end_line": end, "window_end_line": end, "source": source, "source_hash": digest,
        "content_hash": digest, "truncated": False, "retrieval_score": 0.125,
        "qwen_score": None, "h2o_score": None, "origin": "retrieval", "related_to": None, **metadata}


def keys(*rows):
    return {value["id"]: [value[name] for name in ("path", "symbol", "start_line", "end_line")] for value in rows}


def classify(source, *, path="src/example.py", start=11, **metadata):
    value = row(source, path=path, start=start, **metadata)
    return guard.classify_row(value, definition_keys=keys(value))


def response(*rows, **metadata):
    return {"query": "find invented loading and publishing", "results": deepcopy(list(rows)),
        "total_source_chars": sum(len(value["source"]) for value in rows),
        "token_usage": {"budget": 4096, "tokens": 100, "encoding": "cl100k_base"}, **metadata}


def run(reference, proposal, **kwargs):
    return guard.apply_role_guard(reference, proposal, definition_keys=keys(*reference["results"]), **kwargs)


@pytest.mark.parametrize(("statement", "kind", "node_type"), [
    ("obj.value = 1", "attribute_store", "Attribute"),
    ("obj.value += 1", "attribute_store", "Attribute"),
    ("obj['value'] = 1", "subscript_store", "Subscript"),
    ("obj['value'] *= 2", "subscript_store", "Subscript"),
    ("del obj.value", "attribute_delete", "Attribute"),
    ("del obj['value']", "subscript_delete", "Subscript"),
    ("local, obj.value = other", "attribute_store", "Attribute"),
    ("(local, [obj['value']]) = other", "subscript_store", "Subscript"),
    ("for obj.value in values:\n        pass", "attribute_store", "Attribute"),
    ("with resource() as obj.value:\n        pass", "attribute_store", "Attribute"),
])
def test_python_explicit_write_targets(statement, kind, node_type):
    fact = classify("def invented(obj):\n    " + statement + "\n")
    assert fact["reasons"] == ["explicit_write"] and fact["full_definition"]
    assert fact["nodes"] == [{"kind": kind, "node_type": node_type, "start_line": 12, "end_line": 12}]


def test_python_nested_writes_decorators_dedent_and_sorted_physical_lines():
    source = ("    @decorator\n    async def invented(obj):\n        def nested():\n"
              "            del obj['x']\n        obj.value = nested\n        return nested\n")
    value = row(source, start=21)
    before = deepcopy(value)
    fact = guard.classify_row(value, index=3, definition_keys=keys(value))
    assert fact["index"] == 3 and value == before
    assert fact["nodes"] == [
        {"kind": "subscript_delete", "node_type": "Subscript", "start_line": 24, "end_line": 24},
        {"kind": "attribute_store", "node_type": "Attribute", "start_line": 25, "end_line": 25}]


@pytest.mark.parametrize("source", [
    "def invented(obj):\n    # obj.value = 1\n    return 'obj[0] += 1'\n",
    "def invented(obj):\n    local = {'x': obj.value}\n    return local['x']\n",
    "def invented(obj):\n    return obj.receiver.value + obj['key']\n",
    "def invented(obj):\n    obj.install(value)\n    setattr(obj, 'value', 1)\n    return obj\n",
])
def test_write_free_means_only_no_recognized_explicit_write(source):
    fact = classify(source)
    assert fact["full_definition"] and not fact["reasons"] and not fact["nodes"]


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("path", ["src/example.py", "src/example.js", "src/example.ts", "src/example.tsx"])
def test_nonascii_and_physical_line_endings(path, ending):
    source = ("def invented(obj):" + ending + "    label = '日本語'" + ending + "    obj.value = label" + ending
              if path.endswith(".py") else "function invented(obj) {" + ending + "  const label = '日本語';" + ending
              + "  obj.value = label;" + ending + "}" + ending)
    fact = classify(source, path=path, start=101)
    assert fact["reasons"] == ["explicit_write"]
    assert fact["nodes"] == [{"kind": "attribute_store", "node_type": "Attribute" if path.endswith(".py") else "member_expression",
                              "start_line": 103, "end_line": 103}]


@pytest.mark.parametrize(("statement", "kind", "node_type"), [
    ("obj.value = 1;", "attribute_store", "member_expression"),
    ("obj.value += 1;", "attribute_store", "member_expression"),
    ("obj['value'] = 1;", "subscript_store", "subscript_expression"),
    ("obj['value'] *= 2;", "subscript_store", "subscript_expression"),
    ("obj.value++;", "attribute_store", "member_expression"),
    ("++obj['value'];", "subscript_store", "subscript_expression"),
    ("delete obj.value;", "attribute_delete", "member_expression"),
    ("delete obj['value'];", "subscript_delete", "subscript_expression"),
    ("[local, obj.value] = other;", "attribute_store", "member_expression"),
    ("({key: obj['value']} = other);", "subscript_store", "subscript_expression"),
    ("[...obj.value] = other;", "attribute_store", "member_expression"),
    ("({ ...obj['value'] } = other);", "subscript_store", "subscript_expression"),
    ("[obj.value = other.fallback] = other;", "attribute_store", "member_expression"),
    ("for (obj.value in other) {}", "attribute_store", "member_expression"),
    ("for (obj['value'] of other) {}", "subscript_store", "subscript_expression"),
])
@pytest.mark.parametrize("path", ["src/example.js", "src/example.ts"])
def test_js_ts_direct_targets(statement, kind, node_type, path):
    fact = classify("function invented(obj, other) {\n  " + statement + "\n}\n", path=path)
    assert fact["reasons"] == ["explicit_write"]
    assert fact["nodes"] == [{"kind": kind, "node_type": node_type, "start_line": 12, "end_line": 12}]


@pytest.mark.parametrize("statement", [
    "({x = other.fallback} = other);",
    "({[other.key]: local} = other);",
    "const {x = other.fallback} = other;",
    "const local = other.value; // obj.value = 1",
    "const text = 'obj.value++'; return other['value'];",
    "obj.install(other); Reflect.set(obj, 'value', other);",
    "for (const local of other.values) {}",
])
def test_js_reads_defaults_keys_strings_and_call_only_effects_not_explicit_targets(statement):
    fact = classify("function invented(obj, other) {\n  " + statement + "\n}\n", path="src/example.js")
    assert fact["full_definition"] and not fact["reasons"] and not fact["nodes"]


def test_js_only_outer_lvalue_and_nested_function_writes():
    fact = classify("function invented(obj) {\n  obj.owner.value = 1;\n  function nested() { delete obj[0]; }\n}\n",
                    path="src/example.js")
    assert fact["nodes"] == [
        {"kind": "attribute_store", "node_type": "member_expression", "start_line": 12, "end_line": 12},
        {"kind": "subscript_delete", "node_type": "subscript_expression", "start_line": 13, "end_line": 13}]


@pytest.mark.parametrize(("target", "node_type", "prefix"), [
    ("(obj.value as any)", "member_expression", "attribute"),
    ("obj.value!", "member_expression", "attribute"),
    ("(obj.value satisfies any)", "member_expression", "attribute"),
    ("(<any>obj.value)", "member_expression", "attribute"),
    ("((obj.value! as any) satisfies any)", "member_expression", "attribute"),
    ("((<any>obj['value'])! as any)", "subscript_expression", "subscript"),
])
@pytest.mark.parametrize(("statement", "effect"), [
    ("{target} = 1;", "store"), ("{target} += 1;", "store"), ("{target}++;", "store"),
    ("delete {target};", "delete"), ("for ({target} of other) {{}}", "store"),
])
def test_typescript_transparent_lvalue_operands_preserve_explicit_write_detection(target, node_type, prefix, statement, effect):
    source = "function invented(obj, other) {\n  " + statement.format(target=target) + "\n}\n"
    fact = classify(source, path="src/example.ts")
    assert fact["full_definition"] and fact["reasons"] == ["explicit_write"]
    assert fact["nodes"] == [{"kind": prefix + "_" + effect, "node_type": node_type,
                              "start_line": 12, "end_line": 12}]


@pytest.mark.parametrize("statement", [
    "return obj.value as any;",
    "return obj.value!;",
    "return obj.value satisfies any;",
    "return <any>obj.value;",
    "(local as typeof other.value) = 1;",
    "(<typeof other.value>local) = 1;",
    "delete (local as typeof other.value);",
    "for ((local as typeof other.value) of other) {}",
    "({x = other.value as any} = other);",
    "const local: typeof other.value = other.value as any;",
])
def test_typescript_wrapper_read_default_and_type_branches_are_not_write_targets(statement):
    fact = classify("function invented(obj, other) {\n  " + statement + "\n}\n", path="src/example.ts")
    assert fact["full_definition"] and not fact["reasons"] and not fact["nodes"]


@pytest.mark.parametrize("path", ["src/example.js", "src/example.ts"])
def test_initialized_public_private_and_static_class_fields(path):
    fact = classify("class Invented {\n  field = 1;\n  #secret = 2;\n  static cache = new Map();\n}\n", path=path)
    assert fact["reasons"] == ["explicit_write"]
    assert [(value["kind"], value["start_line"], value["end_line"]) for value in fact["nodes"]] == [
        ("initialized_field", 12, 12), ("initialized_field", 13, 13), ("initialized_field", 14, 14)]


def test_uninitialized_typescript_fields_and_method_reads_are_write_free():
    fact = classify("export abstract class Invented {\n  public field: number;\n  private value!: number;\n"
                    "  method() { return this.field; }\n}\n", path="src/example.ts")
    assert fact["full_definition"] and not fact["reasons"]


@pytest.mark.parametrize("source", [
    "function invented(obj) { return obj.value; }\n",
    "function* invented(obj) { yield obj.value; }\n",
    "export default function invented(obj) { return obj.value; }\n",
    "export const invented = (obj) => obj.value;\n",
    "let invented = function(obj) { return obj.value; };\n",
    "var invented = function*(obj) { yield obj.value; };\n",
    "((obj) => obj.value);\n",
    "class Invented { method() { return 1; } }\n",
    "// leading comment\nexport function invented() { return 1; }\n// trailing comment\n",
])
def test_strict_supported_js_definition_contexts(source):
    assert classify(source, path="src/example.js")["reasons"] == []


@pytest.mark.parametrize(("source", "path", "reason"), [
    ("obj.value = 1;\n", "src/example.js", "parse_context_uncertain"),
    ("const local = 1;\n", "src/example.js", "parse_context_uncertain"),
    ("const one = () => 1, two = () => 2;\n", "src/example.js", "parse_context_uncertain"),
    ("function first() {}\nfunction second() {}\n", "src/example.js", "parse_context_uncertain"),
    ("method(obj) { return obj.value; }\n", "src/example.js", "parse_error"),
    ("function invented(obj) {\n", "src/example.js", "parse_error"),
    ("export\nfunction invented() { return 1; }\n", "src/example.js", "parse_context_uncertain"),
    ("function invented( {\n", "src/example.ts", "parse_error"),
    ("class Invented<out T> {}\n", "src/example.ts", "parse_error"),
    ("local = 1\n", "src/example.py", "parse_context_uncertain"),
    ("class Invented:\n    pass\n", "src/example.py", "parse_context_uncertain"),
    ("def first():\n    pass\ndef second():\n    pass\n", "src/example.py", "parse_context_uncertain"),
    ("def invented(:\n", "src/example.py", "parse_error"),
    ("def invented():\n", "src/example.py", "parse_error"),
    ("fn invented() {}\n", "src/example.rs", "unsupported_grammar"),
])
def test_ambiguous_or_invalid_contexts_protect_without_repairs(source, path, reason):
    fact = classify(source, path=path)
    assert fact["reasons"] == [reason] and not fact["nodes"]


@pytest.mark.parametrize("error", [ModuleNotFoundError("not installed"), ImportError("unavailable"), ValueError("wrong pin")])
def test_unavailable_pinned_parser_protects(monkeypatch, error):
    import semantic_search.javascript as javascript

    def fail(path):
        raise error
    monkeypatch.setattr(javascript, "parser_for", fail)
    assert classify("function invented() {}\n", path="src/example.js")["reasons"] == ["parser_unavailable"]


@pytest.mark.parametrize("error", [RecursionError("parser depth"), OverflowError("parser depth")])
@pytest.mark.parametrize("path", ["src/example.py", "src/example.js"])
def test_parser_resource_uncertainty_protects(monkeypatch, error, path):
    def fail(*args, **kwargs):
        raise error
    if path.endswith(".py"):
        monkeypatch.setattr(guard.ast, "parse", fail)
        source = "def invented():\n    return 1\n"
    else:
        import semantic_search.javascript as javascript

        class Parser:
            parse = fail
        monkeypatch.setattr(javascript, "parser_for", lambda path: Parser())
        source = "function invented() {}\n"
    assert classify(source, path=path)["reasons"] == ["parse_error"]


@pytest.mark.parametrize("bad", [None, [], {}, {"invented": ["wrong.py", "invented", 11, 12]},
    {"invented": ["src/example.py", "wrong", 11, 12]},
    {"invented": ["src/example.py", "invented", True, 12]},
    {"invented": ["src/example.py", "invented", 11, 12, -1]}])
def test_unknown_or_invalid_canonical_definition_protects(bad):
    fact = guard.classify_row(row(), definition_keys=bad)
    assert fact["reasons"] == ["definition_unverified"] and not fact["full_definition"]


def test_complete_syntactically_valid_prefix_still_requires_exact_definition_interval():
    value = row()
    canonical = keys(value)
    canonical[value["id"]][3] += 10
    fact = guard.classify_row(value, definition_keys=canonical)
    assert fact["reasons"] == ["partial_definition"] and not fact["full_definition"]
    canonical[value["id"]] = [value["path"], value["symbol"], value["start_line"], value["end_line"], 0]
    assert guard.classify_row(value, definition_keys=canonical)["full_definition"]


def test_clipped_full_window_and_unsupported_order_is_fixed():
    value = row(truncated=True)
    value["window_end_line"] += 10
    value["content_hash"] = "f" * 64
    fact = guard.classify_row(value, definition_keys=keys(value))
    assert fact["reasons"] == ["clipped", "partial_definition"]
    value["path"] = "src/example.unknown"
    fact = guard.classify_row(value, definition_keys=keys(value))
    assert fact["reasons"] == ["clipped", "unsupported_grammar"]


@pytest.mark.parametrize(("field", "value"), [
    ("source_hash", "f" * 64), ("content_hash", "f" * 64), ("start_line", True),
    ("end_line", 99), ("window_end_line", 9), ("truncated", 0), ("source", ""),
    ("id", ""), ("path", 10), ("symbol", ""), ("source_origin", "buffer"),
    ("buffer_version", 1), ("buffer_hash", "b" * 64), ("qwen_score", float("nan")),
    ("metadata", (1, 2)),
])
def test_invalid_literal_metadata_and_identity_fail_closed(field, value):
    original = row()
    canonical = keys(original)
    original[field] = value
    assert guard.classify_row(original, definition_keys=canonical)["reasons"] == ["invalid_literal"]


def test_hashes_bind_exact_literal_payload_and_provenance():
    value = row(source_origin="overlay", buffer_version=4, buffer_hash="b" * 64, extra={"typed": [False, 0]})
    fact = guard.classify_row(value, definition_keys=keys(value))
    assert not fact["reasons"] and fact["provenance"] == {
        "source_origin": "overlay", "buffer_version": 4, "buffer_hash": "b" * 64}
    assert fact["row_sha256"] == hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert fact["source_sha256"] == hashlib.sha256(value["source"].encode()).hexdigest()
    value["extra"]["typed"][0] = 0
    assert guard.classify_row(value, definition_keys=keys(value))["row_sha256"] != fact["row_sha256"]


@pytest.mark.parametrize("metadata", [
    {"source_origin": "overlay"}, {"source_origin": "overlay", "buffer_version": True, "buffer_hash": "b" * 64},
    {"source_origin": "overlay", "buffer_version": 2**53, "buffer_hash": "b" * 64},
    {"source_origin": "overlay", "buffer_version": 0, "buffer_hash": "bad"},
    {"source_origin": "saved", "buffer_version": 0},
])
def test_invalid_saved_buffer_provenance_is_protected(metadata):
    assert classify("def invented():\n    return 1\n", **metadata)["reasons"] == ["invalid_literal"]


def test_names_paths_queries_and_modes_are_not_selection_signals():
    first = row("def alpha(receiver):\n    receiver.value = 1\n", identifier="alpha", path="src/alpha.py")
    second = row("def omega(container):\n    container.entry = 1\n", identifier="omega", path="other/renamed.py")
    facts = [guard.classify_row(value, definition_keys=keys(value)) for value in (first, second)]
    assert facts[0]["reasons"] == facts[1]["reasons"] == ["explicit_write"]
    assert facts[0]["nodes"] == facts[1]["nodes"]
    for query, mode in (("ordinary question", "dense"), ("unrelated text", "other")):
        reference = response(first, query=query, retrieval=mode)
        chosen, receipt = run(reference, response(second, query=query, retrieval=mode))
        assert chosen == reference and receipt["violated_ids"] == ["alpha"]


def test_safe_exchange_selects_exact_defensive_proposal_and_no_extra_work():
    original = row()
    newcomer = row("def newcomer():\n    return 2\n", identifier="newcomer")
    reference, proposal = response(original), response(newcomer, metadata={"opaque": [1, False]})
    before = deepcopy((reference, proposal))
    chosen, receipt = run(reference, proposal)
    assert chosen == proposal and chosen is not proposal
    assert receipt["accepted"] and not receipt["protected_ids"] and not receipt["violated_ids"]
    assert receipt["calls"] == {"v1": 0, "model": 0, "retrieval": 0, "coalesce": 0, "backfill": 0}
    chosen["results"][0]["source"] = "mutated"
    chosen["metadata"]["opaque"].append(2)
    receipt["reference_facts"][0]["definition_key"].append(4)
    assert (reference, proposal) == before


def test_one_forbidden_exchange_rejects_entire_two_exchange_proposal():
    safe = row(identifier="safe")
    protected = row("def protected(obj):\n    obj.value = 1\n", identifier="protected", source_origin="saved")
    first, second = row(identifier="first"), row(identifier="second")
    reference, proposal = response(safe, protected), response(first, second)
    chosen, receipt = run(reference, proposal)
    assert chosen == reference and chosen is not reference
    assert receipt["protected_ids"] == ["protected"] and receipt["violated_ids"] == ["protected"]
    assert receipt["fallback_reason"] == "protected_role_row_changed" and not receipt["accepted"]
    chosen["results"][0]["path"] = "mutated"
    assert reference["results"][0]["path"] != "mutated"


@pytest.mark.parametrize("change", ["type", "presence", "provenance", "source", "id", "order"])
def test_protected_dictionary_retention_preserves_types_presence_and_buffer_identity(change):
    protected = row("def protected(obj):\n    obj.value = 1\n", source_origin="overlay", buffer_version=4,
                    buffer_hash="b" * 64, metadata={"value": [False, 0]})
    reference = response(protected)
    proposal = deepcopy(reference)
    altered = proposal["results"][0]
    if change == "type":
        altered["metadata"]["value"][0] = 0
    elif change == "presence":
        altered["new_field"] = None
    elif change == "provenance":
        altered["buffer_version"] += 1
    elif change == "source":
        altered["source"] += "# comment\n"
    elif change == "id":
        altered["id"] = "other"
    else:
        altered["metadata"]["value"].reverse()
    chosen, receipt = run(reference, proposal)
    assert chosen == reference and not receipt["accepted"]


def test_protected_rows_can_move_order_but_payload_dictionaries_remain_exact():
    protected = row("def invented(obj):\n    del obj.value\n")
    safe = row(identifier="safe")
    reference, proposal = response(protected, safe), response(safe, protected, extra="proposal")
    chosen, receipt = run(reference, proposal)
    assert chosen == proposal and receipt["accepted"] and receipt["protected_ids"] == [protected["id"]]


@pytest.mark.parametrize("proposal", [None, [], {}, {"results": ()}, {"results": [None]},
    {"results": [{"id": ""}]}, {"results": [{"id": "same"}, {"id": "same"}]}])
def test_malformed_or_duplicate_proposal_falls_back(proposal):
    reference = response(row())
    chosen, receipt = run(reference, proposal)
    assert chosen == reference and receipt["fallback_reason"] == "invalid_proposal"


def test_malformed_reference_is_returned_defensively_without_accepting_proposal():
    reference = {"results": [{"id": "same"}, {"id": "same"}]}
    chosen, receipt = guard.apply_role_guard(reference, response(row()))
    assert chosen == reference and chosen is not reference
    assert receipt["fallback_reason"] == "invalid_reference" and not receipt["reference_facts"]


@pytest.mark.parametrize("metadata", [float("nan"), float("inf"), float("-inf"), {1: "integer key"},
    (1, 2), {1, 2}, b"bytes"])
@pytest.mark.parametrize("operand", ["reference", "proposal"])
def test_entire_response_metadata_requires_finite_literal_json(metadata, operand):
    reference, proposal = response(row()), response(row(identifier="other"))
    (reference if operand == "reference" else proposal)["metadata"] = metadata
    chosen, receipt = run(reference, proposal)
    assert chosen is not reference and repr(chosen) == repr(reference)
    assert receipt["fallback_reason"] == "invalid_" + operand and not receipt["accepted"]


def test_recursive_response_metadata_fails_closed_without_syntax_or_comparison_work():
    reference, proposal = response(row()), response(row(identifier="other"))
    proposal["recursive"] = proposal
    chosen, receipt = run(reference, proposal)
    assert chosen == reference and receipt["fallback_reason"] == "invalid_proposal"


@pytest.mark.parametrize("error", [ValueError("cancel"), SyntaxError("cancel"), KeyboardInterrupt("cancel"),
    RecursionError("cancel"), OverflowError("cancel"), SearchCancelled("cancel")])
@pytest.mark.parametrize("path", ["src/example.py", "src/example.js"])
def test_cancellation_inside_syntax_traversal_propagates_original_exception(error, path):
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise error
    source = "def invented(obj):\n    obj.value = 1\n" if path.endswith(".py") else "function invented(obj) { obj.value = 1; }\n"
    value = row(source, path=path)
    with pytest.raises(type(error)) as caught:
        guard.classify_row(value, definition_keys=keys(value), cancel_check=cancel)
    assert caught.value is error and calls == 4


def test_cancellation_before_any_guard_analysis():
    error = RuntimeError("cancel before work")

    def cancel():
        raise error
    with pytest.raises(RuntimeError) as caught:
        guard.apply_role_guard(None, None, cancel_check=cancel)
    assert caught.value is error


def test_optional_wrapper_calls_unchanged_v1_once_and_then_protects(monkeypatch):
    import semantic_search.semantic_context as semantic_context

    value = row("def invented(obj):\n    obj.value = 1\n")
    reference, proposal, calls = response(value), response(row(identifier="other")), []

    def v1(*args, **kwargs):
        calls.append((args, kwargs))
        return deepcopy(proposal), {"accepted": True, "opaque": "v1"}
    monkeypatch.setattr(semantic_context, "semantic_final_context", v1)
    chosen, receipt = guard.role_final_context(reference, [value], None, definition_keys=keys(value), max_results=8)
    assert chosen == reference and len(calls) == 1
    assert calls[0][1]["max_results"] == 8 and receipt["v1_receipt"] == {"accepted": True, "opaque": "v1"}
    assert receipt["role_guard_receipt"]["calls"] == {"v1": 1, "model": 0, "retrieval": 0, "coalesce": 0, "backfill": 0}


def test_python_guard_import_and_classification_need_no_project_or_model_dependencies():
    code = '''
import importlib.abc, importlib.util, sys
class NoProject(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'semantic_search', 'numpy', 'mlx', 'torch', 'tree_sitter'}:
            raise AssertionError('unexpected dependency: ' + fullname)
sys.meta_path.insert(0, NoProject())
spec = importlib.util.spec_from_file_location('guard', sys.argv[1])
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
source = 'def invented(obj):\\n    obj.value = 1\\n'
digest = guard._hash(source.encode())
row = {'id':'x','path':'x.py','symbol':'invented','start_line':1,'end_line':2,'window_end_line':2,
       'source':source,'source_hash':digest,'content_hash':digest,'truncated':False}
fact = guard.classify_row(row, definition_keys={'x':['x.py','invented',1,2]})
assert fact['reasons'] == ['explicit_write']
'''
    completed = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT / "scripts/research_role_context.py")],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
