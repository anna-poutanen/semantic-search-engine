"""Preserve already delivered, source-bound annotated caller/callee pairs.

This terminal filter accepts a previously validated role-guarded proposal. It
never retrieves, packs, invents a dependency row, or scores query relevance.
Static annotations describe declared dependencies; they do not prove runtime
dispatch in the presence of subclassing or external mutation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from scripts.research_role_context import _canonical, _key, _literal, _rows, _same


MODE = "role_annotated_pairs_v1"
SCHEMA = "dependency_context_guard_v1"
NO_WORK = {name: 0 for name in ("model", "retrieval", "index", "coalesce", "backfill", "v1")}


def _sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _endpoint(value):
    if type(value) is not dict:
        return False
    key = _key(value.get("definition_key"))
    return (key is not None and key[:2] == [value.get("path"), value.get("symbol")]
            and value.get("full_definition") is True
            and type(value.get("source")) is str and bool(value["source"])
            and _sha(value.get("definition_sha256"))
            and value.get("source_sha256") == value["definition_sha256"]
            == hashlib.sha256(value["source"].encode()).hexdigest())


def _witness(value, path):
    return (type(value) is dict and value.get("path") == path
            and type(value.get("start_line")) is int and type(value.get("end_line")) is int
            and 1 <= value["start_line"] <= value["end_line"]
            and type(value.get("start_byte")) is int and type(value.get("end_byte")) is int
            and 0 <= value["start_byte"] < value["end_byte"]
            and type(value.get("source")) is str and bool(value["source"])
            and value["end_byte"]-value["start_byte"] == len(value["source"].encode())
            and value.get("sha256") == hashlib.sha256(value["source"].encode()).hexdigest())


def _bound(row, endpoint, keys):
    key = _key(keys.get(row["id"]))
    return (key is not None and _same(key, _key(endpoint["definition_key"]))
            and _literal(row) and not row["truncated"]
            and [row["path"], row["symbol"], row["start_line"], row["end_line"]] == key[:4]
            and row["end_line"] == row["window_end_line"]
            and row["source_hash"] == endpoint["definition_sha256"])


def _delivers(row, witness):
    from semantic_search.source import physical_lines

    low, high = witness["start_line"], witness["end_line"]
    if not row["start_line"] <= low <= high <= row["end_line"]:
        return False
    lines = physical_lines(row["source"])
    span = "".join(lines[low-row["start_line"]:high-row["start_line"]+1])
    return witness["source"] in span


def apply_dependency_guard(reference, proposal, *, dependencies, definition_keys,
                           cancel_check=lambda: None):
    """Return one exact operand; protect both ends of each delivered pair.

    ``dependencies`` is the trusted, source-inventory-bound graph receipt from
    AnnotatedDependencies. Corpus/input validation belongs to the preparation
    and audit boundary. This function rechecks literal endpoint and call-source
    containment against the actual delivered baseline. Malformed facts return
    exact coverage; unrelated valid facts establish no protected pair.
    """
    from experiments.dependency_context.annotated_dependencies import SCHEMA as GRAPH_SCHEMA

    cancellation = None

    def check():
        nonlocal cancellation
        try:
            cancel_check()
        except BaseException as error:
            cancellation = error
            raise

    check()
    receipt = {"schema": SCHEMA, "mode": MODE, "accepted": False,
        "fallback_reason": None, "dependency_receipt_sha256": None,
        "pairs": [], "protected_ids": [], "violated_ids": [], "calls": dict(NO_WORK)}

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
        raw = _canonical(dependencies)
        if (type(dependencies) is not dict or dependencies.get("schema") != GRAPH_SCHEMA
                or type(dependencies.get("accepted")) is not list
                or type(definition_keys) is not dict):
            return finish(reference, "invalid_dependency_facts")
        receipt["dependency_receipt_sha256"] = hashlib.sha256(raw).hexdigest()
        facts = dependencies["accepted"]
        if any(type(fact) is not dict or not _endpoint(fact.get("caller"))
               or not _endpoint(fact.get("callee"))
               or not _witness(fact.get("call"), fact["caller"]["path"])
               or not _witness(fact.get("annotation"), fact["caller"]["path"])
               for fact in facts):
            return finish(reference, "invalid_dependency_facts")
        protected = set()
        for index, fact in enumerate(facts):
            check()
            callers = [row for row in baseline if _bound(row, fact["caller"], definition_keys)
                       and _delivers(row, fact["call"]) and _delivers(row, fact["annotation"])]
            callees = [row for row in baseline if _bound(row, fact["callee"], definition_keys)]
            if len(callers) == len(callees) == 1 and callers[0]["id"] != callees[0]["id"]:
                pair = {"witness_index": index, "caller_id": callers[0]["id"], "callee_id": callees[0]["id"]}
                receipt["pairs"].append(pair)
                protected.update((pair["caller_id"], pair["callee_id"]))
        receipt["protected_ids"] = [row["id"] for row in baseline if row["id"] in protected]
        by_id = {row["id"]: row for row in proposed}
        receipt["violated_ids"] = [row["id"] for row in baseline if row["id"] in protected
            and (row["id"] not in by_id or not _same(row, by_id[row["id"]]))]
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError) as error:
        if error is cancellation:
            raise
        return finish(reference, "invalid_dependency_facts")
    if receipt["violated_ids"]:
        return finish(reference, "delivered_dependency_pair_changed")
    return finish(proposal)
