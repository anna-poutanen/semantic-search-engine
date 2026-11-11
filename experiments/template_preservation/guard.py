"""Research-only terminal protection of literal returned-template definitions.

A direct returned f-string containing a direct evaluated formatted call is a
syntactic source role, including ordinary message formatters. It is not evidence
of query relevance, runtime mutation, or generated code. This module consumes
captured operands and source bytes; it never reads or executes repository source.
"""
from __future__ import annotations

import ast
import base64
from copy import deepcopy
import hashlib
import io
from pathlib import PurePosixPath
import re
import tokenize

from scripts.research_role_context import _canonical, _key, _literal, _rows, _same


MODE = "returned_template_terminal_v1"
SCHEMA = "returned_template_guard_v1"
NO_WORK = {name: 0 for name in ("model", "retrieval", "index", "coalesce", "backfill", "v1")}
_PROVENANCE = ("source_origin", "buffer_version", "buffer_hash")
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_LEXICAL_SCOPES = (*_FUNCTIONS, ast.ClassDef, ast.Lambda)


class _InvalidFacts(ValueError):
    """Captured facts cannot be bound to their complete source inventory."""


def _require(value):
    if not value:
        raise _InvalidFacts("Literal source-inventory binding required")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def _valid_bundle(bundle, keys, check):
    """Validate the complete original byte/text/window identity, without I/O."""
    _canonical(bundle)
    _require(type(bundle) is dict and type(bundle.get("id")) is str
             and type(bundle.get("dataset")) is str and type(keys) is dict)
    sources, files, inventory = bundle["sources"], bundle["files"], bundle["inventory"]
    _require(type(sources) is dict and type(files) is dict and sources.keys() == files.keys()
             and type(inventory) is list and type(bundle["definition_keys"]) is dict
             and _same(keys, bundle["definition_keys"]))
    for path, record in files.items():
        check()
        _require(type(path) is str and bool(path) and "\\" not in path
                 and not PurePosixPath(path).is_absolute() and ".." not in PurePosixPath(path).parts
                 and path == PurePosixPath(path).as_posix() and type(record) is dict
                 and type(sources[path]) is str and type(record.get("raw_base64")) is str)
        raw = base64.b64decode(record["raw_base64"], validate=True)
        _require(type(record.get("bytes")) is int and len(raw) == record["bytes"]
                 and _sha(raw) == record.get("sha256"))
        encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0] if path.endswith(".py") else "utf-8"
        _require(record.get("encoding") == encoding and raw.decode(encoding) == sources[path])
    by_id = {}
    for item in inventory:
        check()
        _require(type(item) is dict and set(item) == {
            "id", "path", "symbol", "start_line", "end_line", "text", "content_hash"})
        _require(all(type(item.get(name)) is str and item[name] for name in ("id", "path", "symbol", "text"))
                 and item["id"] not in by_id and item["path"] in sources
                 and type(item["start_line"]) is int and type(item["end_line"]) is int
                 and 1 <= item["start_line"] <= item["end_line"])
        literal = "".join(_lines(sources[item["path"]])[item["start_line"] - 1:item["end_line"]])
        _require(item["text"] == literal and item["content_hash"] == _sha(literal.encode("utf-8"))
                 and len(_lines(literal)) == item["end_line"] - item["start_line"] + 1)
        key = _key(keys.get(item["id"]))
        _require(key is not None and type(keys[item["id"]]) is list
                 and key[:2] == [item["path"], item["symbol"]]
                 and key[2] <= item["start_line"] <= item["end_line"] <= key[3])
        by_id[item["id"]] = item
    _require(by_id.keys() == keys.keys())
    identity = {"dataset": bundle["dataset"],
                "source_sha256": {name: record["sha256"] for name, record in files.items()},
                "definition_keys": keys}
    _require(bundle["id"] == "sources_" + _sha(_canonical(identity)))
    return by_id


def _definitions(tree, check):
    """Locate canonical lexical names/spans in the complete captured module."""
    result = []

    def walk(node, names):
        check()
        if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
            names = [*names, node.name]
            if isinstance(node, _FUNCTIONS):
                low = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
                result.append((".".join(names), low, node.end_lineno, node))
        for child in ast.iter_child_nodes(node):
            walk(child, names)

    walk(tree, [])
    return result


def _witness(path, source, node=None, *, low=None, high=None):
    lines = _lines(source)
    prefix = [0]
    for line in lines:
        prefix.append(prefix[-1] + len(line.encode("utf-8")))
    if node is not None:
        low, high = node.lineno, node.end_lineno
        start, end = prefix[low - 1] + node.col_offset, prefix[high - 1] + node.end_col_offset
    else:
        start, end = prefix[low - 1], prefix[high]
    raw = source.encode("utf-8")[start:end]
    return {"path": path, "start_line": low, "end_line": high,
            "start_byte": start, "end_byte": end, "source": raw.decode("utf-8"), "sha256": _sha(raw)}


def _returned_templates(function, path, source, low, high, check):
    result = []
    definition = _witness(path, source, low=low, high=high)

    def walk(node):
        check()
        if isinstance(node, _LEXICAL_SCOPES):
            return
        if isinstance(node, ast.Return) and isinstance(node.value, ast.JoinedStr):
            for formatted in node.value.values:
                check()
                if isinstance(formatted, ast.FormattedValue) and isinstance(formatted.value, ast.Call):
                    result.append({"definition": deepcopy(definition),
                        "return": _witness(path, source, node),
                        "template": _witness(path, source, node.value),
                        "formatted_value": _witness(path, source, formatted),
                        "call": _witness(path, source, formatted.value)})
        for child in ast.iter_child_nodes(node):
            walk(child)

    for statement in function.body:
        walk(statement)
    return sorted(result, key=lambda item: (item["return"]["start_byte"],
        item["formatted_value"]["start_byte"], item["call"]["start_byte"]))


def _classify(row, index, bundle, keys, inventory, check, cancelled):
    check()
    _require(_literal(row) and row["id"] in inventory)
    item = inventory[row["id"]]
    key = keys[row["id"]]
    _require([row["path"], row["symbol"], row["start_line"], row["window_end_line"]]
             == [item["path"], item["symbol"], item["start_line"], item["end_line"]]
             and row["content_hash"] == item["content_hash"])
    source = bundle["sources"][row["path"]]
    literal = "".join(_lines(source)[row["start_line"] - 1:row["end_line"]])
    _require(row["source"] == literal)
    if row.get("source_origin") == "overlay":
        _require(row["buffer_hash"] == _sha(source.encode("utf-8")))
    provenance = {name: deepcopy(row[name]) for name in _PROVENANCE if name in row}
    fact = {"index": index, "id": row["id"], "path": row["path"], "symbol": row["symbol"],
        "definition_key": deepcopy(key), "row_sha256": _sha(_canonical(row)),
        "source_sha256": _sha(row["source"].encode("utf-8")),
        "source_file_sha256": bundle["files"][row["path"]]["sha256"],
        "provenance": provenance, "status": "unsupported_language", "witnesses": []}
    if PurePosixPath(row["path"]).suffix != ".py":
        return fact
    if (row["truncated"] or row["end_line"] != row["window_end_line"]
            or key[2:4] != [row["start_line"], row["end_line"]]):
        fact["status"] = "partial_definition"
        return fact
    fact["status"] = "uncertain_definition"
    if len(key) != 4:
        return fact
    try:
        tree = ast.parse(source, filename=row["path"])
        definitions = _definitions(tree, check)
        matches = [node for symbol, low, high, node in definitions
                   if (symbol, low, high) == (row["symbol"], row["start_line"], row["end_line"])]
        if len(matches) != 1:
            return fact
        fact["witnesses"] = _returned_templates(matches[0], row["path"], source,
                                                 row["start_line"], row["end_line"], check)
    except (SyntaxError, ValueError, RecursionError, OverflowError, UnicodeError) as error:
        if error is cancelled():
            raise
        return fact
    fact["status"] = "returned_template" if fact["witnesses"] else "no_returned_template"
    return fact


def apply_template_guard(reference, proposal, *, source_bundle, definition_keys, cancel_check=lambda: None):
    """Return an exact defensive copy of coverage or the saved prior-role input.

    Complete source inventory binding is mandatory. Valid partial definitions
    and other languages receive no new protection. Full canonical definitions
    with parser uncertainty are conservatively protected; this is not a positive
    template classification. Protected dictionaries must remain exactly typed
    and field-preserving. No row is created, clipped, repacked, or admitted here.
    """
    cancellation = None

    def check():
        nonlocal cancellation
        try:
            cancel_check()
        except BaseException as error:
            cancellation = error
            raise

    check()
    receipt = {"schema": SCHEMA, "mode": MODE, "accepted": False, "fallback_reason": None,
        "source_bundle_sha256": None, "source_inventory_sha256": None, "definition_keys_sha256": None,
        "reference_facts": [], "protected_ids": [], "violated_ids": [], "calls": dict(NO_WORK)}

    def finish(value, reason=None):
        check()
        receipt["fallback_reason"] = reason
        receipt["accepted"] = reason is None
        return deepcopy(value), deepcopy(receipt)

    baseline, proposed = _rows(reference), _rows(proposal)
    if baseline is None:
        return finish(reference, "invalid_reference")
    if proposed is None:
        return finish(reference, "invalid_proposal")
    try:
        inventory = _valid_bundle(source_bundle, definition_keys, check)
        receipt["source_bundle_sha256"] = _sha(_canonical(source_bundle))
        receipt["source_inventory_sha256"] = _sha(_canonical(source_bundle["inventory"]))
        receipt["definition_keys_sha256"] = _sha(_canonical(definition_keys))
    except (ValueError, TypeError, KeyError, UnicodeError, SyntaxError, RecursionError, OverflowError) as error:
        if error is cancellation:
            raise
        return finish(reference, "invalid_source_inventory")
    try:
        for index, row in enumerate(baseline, 1):
            receipt["reference_facts"].append(_classify(row, index, source_bundle, definition_keys, inventory, check, lambda: cancellation))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError) as error:
        if error is cancellation:
            raise
        return finish(reference, "invalid_reference_facts")
    receipt["protected_ids"] = [fact["id"] for fact in receipt["reference_facts"]
                                if fact["status"] in ("returned_template", "uncertain_definition")]
    by_id = {row["id"]: row for row in proposed}
    receipt["violated_ids"] = [row["id"] for row in baseline if row["id"] in receipt["protected_ids"]
                               and (row["id"] not in by_id or not _same(row, by_id[row["id"]])
                                    or _canonical(row) != _canonical(by_id[row["id"]]))]
    if receipt["violated_ids"]:
        return finish(reference, "protected_template_row_changed")
    return finish(proposal)
