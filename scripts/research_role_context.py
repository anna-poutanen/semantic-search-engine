"""Conservative terminal protection for explicit state-writing source windows.

This research guard consumes an already validated, budgeted semantic-v1 proposal.
It preserves selected baseline dictionaries; it does not establish task relevance
or detect all effects. Call-only mutation, reflection and indirect alias effects
can remain unrecognized. Parser uncertainty protects source rather than guessing.
No source is reformatted, stitched, retrieved or packed by this module.
"""
from __future__ import annotations

import ast
from bisect import bisect_right
from copy import deepcopy
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
import textwrap


MODE = "role_state_write_v1"
SCHEMA = "role_context_guard_v1"
PROVENANCE = ("source_origin", "buffer_version", "buffer_hash")
GRAMMARS = {".py": "python", **{suffix: "javascript" for suffix in (".js", ".jsx", ".mjs", ".cjs")},
            **{suffix: "typescript" for suffix in (".ts", ".mts", ".cts")}, ".tsx": "tsx"}
JS_FUNCTIONS = frozenset({"function_expression", "generator_function", "arrow_function"})
JS_DECLARATIONS = frozenset({"function_declaration", "generator_function_declaration", "class_declaration",
                             "abstract_class_declaration"})


def _same(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _json_literal(value):
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(_json_literal(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _json_literal(item) for key, item in value.items())
    return False


def _canonical(value):
    if not _json_literal(value):
        raise ValueError("Literal finite JSON required")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def _sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _literal(row):
    if (type(row) is not dict or any(type(row.get(key)) is not str or not row[key]
            for key in ("id", "path", "symbol", "source"))
            or any(type(row.get(key)) is not int for key in ("start_line", "end_line", "window_end_line"))
            or not 1 <= row["start_line"] <= row["end_line"] <= row["window_end_line"]
            or type(row.get("truncated")) is not bool):
        return False
    source = row["source"]
    if (row["end_line"] != row["start_line"] + len(_lines(source)) - 1
            or row.get("source_hash") != _hash(source.encode()) or not _sha(row.get("content_hash"))
            or not row["truncated"] and (row["end_line"] != row["window_end_line"]
                or row["content_hash"] != row["source_hash"])):
        return False
    origin = row.get("source_origin")
    if "source_origin" in row and origin not in ("saved", "overlay"):
        return False
    if origin == "overlay":
        return (type(row.get("buffer_version")) is int and 0 <= row["buffer_version"] <= 2**53 - 1
                and _sha(row.get("buffer_hash")))
    return not any(key in row for key in ("buffer_version", "buffer_hash"))


def _key(value):
    if not isinstance(value, (list, tuple)) or len(value) not in (4, 5):
        return None
    if (any(type(item) is not str for item in value[:2]) or not all(value[:2])
            or type(value[2]) is not int or type(value[3]) is not int or not 1 <= value[2] <= value[3]
            or len(value) == 5 and (type(value[4]) is not int or value[4] < 0)):
        return None
    return list(value)


def _python(source, offset, check):
    tree = ast.parse(textwrap.dedent(source))
    if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    result = []
    for node in ast.walk(tree):
        check()
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            result.append({"kind": ("attribute" if isinstance(node, ast.Attribute) else "subscript")
                           + ("_delete" if isinstance(node.ctx, ast.Del) else "_store"),
                "node_type": type(node).__name__, "start_line": offset + node.lineno - 1,
                "end_line": offset + node.end_lineno - 1})
    return result


def _expression_function(node):
    while node is not None and node.type == "parenthesized_expression" and len(node.named_children) == 1:
        node = node.named_children[0]
    return node is not None and node.type in JS_FUNCTIONS


def _js_context(root):
    children = [child for child in root.named_children if child.type != "comment"]
    if len(children) != 1:
        return False
    node = children[0]
    if node.type == "export_statement":
        children = [child for child in node.named_children if child.type != "comment"]
        if len(children) != 1:
            return False
        node = children[0]
    if node.type in JS_DECLARATIONS or _expression_function(node):
        return True
    if node.type in {"lexical_declaration", "variable_declaration"}:
        declarations = [child for child in node.named_children if child.type == "variable_declarator"]
        return len(declarations) == 1 and _expression_function(declarations[0].child_by_field_name("value"))
    if node.type == "expression_statement" and len(node.named_children) == 1:
        return _expression_function(node.named_children[0])
    return False


def _javascript(path, source, offset, check):
    # parser_for verifies the existing pinned wheels. Calling parse directly
    # deliberately bypasses parse_source's repairs and introduces no wrapper.
    from semantic_search.javascript import parser_for

    raw = source.encode()
    tree = parser_for(path).parse(raw)
    if tree.root_node.has_error:
        raise SyntaxError("Direct source grammar contains an error or missing node")
    if not _js_context(tree.root_node):
        return None
    result = []
    starts = [0]
    for line in _lines(source):
        starts.append(starts[-1] + len(line.encode()))

    def emit(node, kind):
        # Tree-sitter rows only count LF; source windows also support bare CR.
        # Byte-prefix physical-line counts bind every target to literal source.
        result.append({"kind": kind, "node_type": node.type,
                       "start_line": offset + bisect_right(starts, node.start_byte) - 1,
                       "end_line": offset + bisect_right(starts, max(node.start_byte, node.end_byte - 1)) - 1})

    def targets(node, deleting=False):
        if node is None:
            return
        check()
        if node.type in {"member_expression", "subscript_expression"}:
            emit(node, ("attribute" if node.type == "member_expression" else "subscript")
                 + ("_delete" if deleting else "_store"))
        elif node.type == "pair_pattern":
            targets(node.child_by_field_name("value"), deleting)
        elif node.type in {"assignment_pattern", "object_assignment_pattern"}:
            targets(node.child_by_field_name("left"), deleting)
        elif node.type in {"as_expression", "satisfies_expression", "non_null_expression", "type_assertion"}:
            # The pinned TS grammar has the runtime operand first, except in a
            # prefix type assertion whose type_arguments precede the operand.
            # Type children are never assignment targets or runtime effects.
            if node.named_children:
                targets(node.named_children[-1 if node.type == "type_assertion" else 0], deleting)
        elif node.type in {"array_pattern", "object_pattern", "rest_pattern", "parenthesized_expression"}:
            for child in node.named_children:
                targets(child, deleting)

    pending = [tree.root_node]
    while pending:
        check()
        node = pending.pop()
        if node.type in {"assignment_expression", "augmented_assignment_expression", "for_in_statement"}:
            targets(node.child_by_field_name("left"))
        elif node.type == "update_expression":
            targets(node.child_by_field_name("argument"))
        elif node.type == "unary_expression":
            operator = node.child_by_field_name("operator")
            if operator is not None and operator.type == "delete":
                targets(node.child_by_field_name("argument"), True)
        elif node.type in {"public_field_definition", "field_definition"} and node.child_by_field_name("value") is not None:
            emit(node, "initialized_field")
        pending.extend(reversed(node.named_children))
    return result


def classify_row(row, *, index=1, definition_keys=None, cancel_check=lambda: None):
    """Bind one literal complete definition to conservative syntactic facts."""
    cancel_check()
    if type(index) is not int or index < 1:
        raise ValueError("Row index must be a positive integer")
    value = row if type(row) is dict else {}
    identifier = value.get("id")
    keys = definition_keys if type(definition_keys) is dict else {}
    key = _key(keys.get(identifier)) if type(identifier) is str else None
    grammar = GRAMMARS.get(PurePosixPath(value["path"]).suffix) if type(value.get("path")) is str else None
    fact = {"index": index, "id": identifier if type(identifier) is str else None,
        "path": value.get("path") if type(value.get("path")) is str else None,
        "symbol": value.get("symbol") if type(value.get("symbol")) is str else None,
        **{name: value.get(name) if type(value.get(name)) is int else None
           for name in ("start_line", "end_line", "window_end_line")},
        "row_sha256": None, "source_sha256": None, "definition_key": key, "grammar": grammar,
        "full_definition": False, "provenance": {}, "reasons": [], "nodes": []}
    try:
        fact["row_sha256"] = _hash(_canonical(row))
        if type(value.get("source")) is str:
            fact["source_sha256"] = _hash(value["source"].encode())
        provenance = {name: value[name] for name in PROVENANCE if name in value}
        _canonical(provenance)
        fact["provenance"] = deepcopy(provenance)
        valid = _literal(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        valid = False
    if not valid:
        fact["reasons"].append("invalid_literal")
        return fact
    identity = key is not None and key[:2] == [value["path"], value["symbol"]]
    clipped = value["truncated"] or value["end_line"] != value["window_end_line"]
    fact["full_definition"] = bool(identity and not clipped and key[2:4] == [value["start_line"], value["end_line"]])
    if clipped:
        fact["reasons"].append("clipped")
    if grammar is None:
        fact["reasons"].append("unsupported_grammar")
        return fact
    if not identity:
        fact["reasons"].append("definition_unverified")
        return fact
    if not fact["full_definition"]:
        fact["reasons"].append("partial_definition")
        return fact
    cancellation = None

    def check():
        nonlocal cancellation
        try:
            cancel_check()
        except BaseException as error:
            cancellation = error
            raise

    try:
        nodes = (_python(value["source"], value["start_line"], check) if grammar == "python"
                 else _javascript(value["path"], value["source"], value["start_line"], check))
    except (ImportError, ValueError, SyntaxError, RecursionError, OverflowError) as error:
        if error is cancellation:
            raise
        fact["reasons"].append("parse_error" if isinstance(error, (SyntaxError, RecursionError, OverflowError)) else "parser_unavailable")
        return fact
    if nodes is None:
        fact["reasons"].append("parse_context_uncertain")
        return fact
    fact["nodes"] = [dict(zip(("kind", "node_type", "start_line", "end_line"), item)) for item in sorted(
        {tuple(node[name] for name in ("kind", "node_type", "start_line", "end_line")) for node in nodes},
        key=lambda item: (item[2], item[3], item[0], item[1]))]
    if nodes:
        fact["reasons"].append("explicit_write")
    cancel_check()
    return fact


def _rows(response):
    if type(response) is not dict or type(response.get("results")) is not list:
        return None
    try:
        if not _json_literal(response):
            return None
    except (RecursionError, OverflowError):
        return None
    rows = response["results"]
    if any(type(row) is not dict or type(row.get("id")) is not str or not row["id"] for row in rows):
        return None
    return rows if len({row["id"] for row in rows}) == len(rows) else None


def apply_role_guard(reference, proposal, *, definition_keys=None, cancel_check=lambda: None):
    """Preserve protected baseline rows in a trusted, validated v1 proposal.

    The inherited v1 producer and independent study auditor retain responsibility
    for global literal/source/score/budget validation. This guard adds no packing,
    proposal search, fallback retries or inference. Its acceptance is a role-guard
    pass, which may also describe an unchanged proposal.
    """
    cancel_check()
    receipt = {"schema": SCHEMA, "mode": MODE, "reference_facts": [], "protected_ids": [],
        "violated_ids": [], "accepted": False, "fallback_reason": None,
        "calls": {name: 0 for name in ("v1", "model", "retrieval", "coalesce", "backfill")}}
    baseline, proposed = _rows(reference), _rows(proposal)
    if baseline is None:
        receipt["fallback_reason"] = "invalid_reference"
        return deepcopy(reference), receipt
    receipt["reference_facts"] = [classify_row(row, index=index, definition_keys=definition_keys,
        cancel_check=cancel_check) for index, row in enumerate(baseline, 1)]
    receipt["protected_ids"] = [fact["id"] for fact in receipt["reference_facts"] if fact["reasons"]]
    if proposed is None:
        receipt["fallback_reason"] = "invalid_proposal"
        return deepcopy(reference), receipt
    by_id = {row["id"]: row for row in proposed}
    receipt["violated_ids"] = [row["id"] for row in baseline if row["id"] in receipt["protected_ids"]
        and (row["id"] not in by_id or not _same(row, by_id[row["id"]]))]
    receipt["accepted"] = not receipt["violated_ids"]
    receipt["fallback_reason"] = None if receipt["accepted"] else "protected_role_row_changed"
    cancel_check()
    return deepcopy(proposal if receipt["accepted"] else reference), deepcopy(receipt)


def role_final_context(reference, raw_rows, features, *, definition_keys=None, cancel_check=lambda: None, **kwargs):
    """Optional live research wrapper; calls unchanged v1 once, then the guard."""
    from semantic_search.semantic_context import semantic_final_context

    proposal, v1_receipt = semantic_final_context(reference, raw_rows, features,
        definition_keys=definition_keys, cancel_check=cancel_check, **kwargs)
    chosen, role_receipt = apply_role_guard(reference, proposal, definition_keys=definition_keys,
                                          cancel_check=cancel_check)
    role_receipt["calls"]["v1"] = 1
    return chosen, {"v1_receipt": v1_receipt, "role_guard_receipt": role_receipt}
