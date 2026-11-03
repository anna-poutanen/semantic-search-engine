#!/usr/bin/env python3
"""Independent role-write guard audit; never imports the research producer.

Only Python's AST and the directly pinned JavaScript/TypeScript grammars classify
source. Old source/vector/control auditors are independent data authorities, not
producer selectors. No model, retrieval, index or new v1 proposal is executed.
"""
from __future__ import annotations

import ast
import argparse
from bisect import bisect_right
from collections import Counter
from copy import deepcopy
from datetime import datetime
import gzip
import hashlib
import importlib.abc
import importlib.metadata
import json
import math
from pathlib import Path
import re
import socket
import subprocess
import sys
import tarfile
import textwrap
from unittest.mock import patch

import audit_semantic_context as context
import audit_semantic_study as prior

MODE = "role_state_write_v1"
SCHEMA = "role_context_guard_v1"
STUDY_SCHEMA = "role_context_study_v1"
ROOT = Path(__file__).resolve().parents[1]
PARENT = "results/semantic-model-development-2026-11-02"
COMPLEMENTARY = "results/complementary-development-v2-2026-10-29"
ORIGINAL_FREEZE = "cca82913ef67bf41d565f6424b9619fec8270edc"
PUBLICATION = "cd450f7fb91f783f5a883526649472ac7a2f00fd"
RECHECK = "results/role-context-development-2026-11-03/parent-recheck.json"
SOURCES = ("scripts/research_role_context.py", "scripts/run_role_context.py", "scripts/audit_role_context.py",
           "tests/test_role_context.py", "tests/test_role_context_study.py", "tests/test_role_context_audit.py",
           "docs/role-context-protocol.md")
ARTIFACTS = ("input-manifest.json", "parent-outcome-references.json", "control-references.json", "negative-references.json")
LIMITS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096}
PROVENANCE = ("source_origin", "buffer_version", "buffer_hash")
PARSER_PINS = {"tree-sitter": "0.26.0", "tree-sitter-javascript": "0.25.0", "tree-sitter-typescript": "0.23.2"}
GRAMMARS = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
            ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx"}


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def literal_json(value):
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(literal_json(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and literal_json(item) for key, item in value.items())
    return False


def canonical_definition(value):
    if not isinstance(value, (tuple, list)) or len(value) not in (4, 5):
        return None
    if not all(type(item) is str and item for item in value[:2]):
        return None
    if type(value[2]) is not int or type(value[3]) is not int or not 1 <= value[2] <= value[3]:
        return None
    if len(value) == 5 and (type(value[4]) is not int or value[4] < 0):
        return None
    return list(value)


def direct_parser(grammar):
    packages = ("tree-sitter", "tree-sitter-javascript" if grammar == "javascript" else "tree-sitter-typescript")
    for package in packages:
        if importlib.metadata.version(package) != PARSER_PINS[package]:
            raise ValueError("Pinned direct grammar unavailable")
    from tree_sitter import Language, Parser

    if grammar == "javascript":
        import tree_sitter_javascript

        language = tree_sitter_javascript.language()
    else:
        import tree_sitter_typescript

        language = tree_sitter_typescript.language_tsx() if grammar == "tsx" else tree_sitter_typescript.language_typescript()
    return Parser(Language(language))


def _node_fact(kind, node_type, start, end):
    return {"kind": kind, "node_type": node_type, "start_line": start, "end_line": end}


def python_writes(source, start_line):
    tree = ast.parse(textwrap.dedent(source))
    if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    facts = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Attribute, ast.Subscript)) or not isinstance(node.ctx, (ast.Store, ast.Del)):
            continue
        kind = ("attribute" if isinstance(node, ast.Attribute) else "subscript") + (
            "_store" if isinstance(node.ctx, ast.Store) else "_delete")
        facts.append(_node_fact(kind, type(node).__name__, start_line + node.lineno - 1,
                                start_line + node.end_lineno - 1))
    return facts


def _targets(node):
    """Follow only assignment pattern targets, never computed keys/default reads."""
    if node is None:
        return []
    if node.type in ("member_expression", "subscript_expression"):
        return [node]
    if node.type == "pair_pattern":
        return _targets(node.child_by_field_name("value"))
    if node.type in ("assignment_pattern", "object_assignment_pattern"):
        return _targets(node.child_by_field_name("left"))
    if node.type in ("object_pattern", "array_pattern", "rest_pattern"):
        return [target for child in node.named_children for target in _targets(child)]
    if node.type == "parenthesized_expression":
        return [target for child in node.named_children for target in _targets(child)]
    if node.type in {"as_expression", "satisfies_expression", "non_null_expression", "type_assertion"}:
        children = node.named_children
        # The direct pinned TS grammar places asserted type arguments before
        # the operand only for angle assertions; never visit type syntax.
        operand = children[-1] if node.type == "type_assertion" else children[0] if children else None
        return _targets(operand)
    return []


def js_definition(root):
    meaningful = [child for child in root.named_children if child.type != "comment"]
    if len(meaningful) != 1:
        return False
    node = meaningful[0]
    if node.type == "export_statement":
        children = [child for child in node.named_children if child.type != "comment"]
        if len(children) != 1:
            return False
        node = children[0]

    def expression_function(value):
        while value is not None and value.type == "parenthesized_expression" and len(value.named_children) == 1:
            value = value.named_children[0]
        return value is not None and value.type in {"function_expression", "generator_function", "arrow_function"}

    if node.type in {"function_declaration", "generator_function_declaration", "class_declaration", "abstract_class_declaration"}:
        return True
    if expression_function(node):
        return True
    if node.type in {"lexical_declaration", "variable_declaration"}:
        values = [child for child in node.named_children if child.type == "variable_declarator"]
        return len(values) == 1 and expression_function(values[0].child_by_field_name("value"))
    return node.type == "expression_statement" and len(node.named_children) == 1 and expression_function(node.named_children[0])


def physical_node_fact(kind, node, raw, offset):
    starts = [0, *(match.end() for match in re.finditer(rb"\r\n|\r|\n", raw))]
    start = offset + bisect_right(starts, node.start_byte) - 1
    end = offset + bisect_right(starts, max(node.start_byte, node.end_byte - 1)) - 1
    return _node_fact(kind, node.type, start, end)


def javascript_writes(source, start_line, grammar, *, parser_factory=direct_parser):
    raw = source.encode("utf-8")
    tree = parser_factory(grammar).parse(raw)
    root = tree.root_node
    if root.has_error:
        raise SyntaxError("Direct grammar rejects source")
    if not js_definition(root):
        return None
    facts = []
    pending = [root]
    while pending:
        node = pending.pop()
        pending.extend(reversed(node.named_children))
        kind, targets = "store", []
        if node.type in ("assignment_expression", "augmented_assignment_expression", "for_in_statement"):
            targets = _targets(node.child_by_field_name("left"))
        elif node.type == "update_expression":
            targets = _targets(node.child_by_field_name("argument"))
        elif node.type == "unary_expression":
            operator = node.child_by_field_name("operator")
            if operator is not None and raw[operator.start_byte:operator.end_byte] == b"delete":
                kind, targets = "delete", _targets(node.child_by_field_name("argument"))
        elif node.type in ("field_definition", "public_field_definition") and node.child_by_field_name("value") is not None:
            facts.append(physical_node_fact("initialized_field", node, raw, start_line))
        for target in targets:
            prefix = "attribute" if target.type == "member_expression" else "subscript"
            facts.append(physical_node_fact(prefix + "_" + kind, target, raw, start_line))
    return facts


def ordered_nodes(facts):
    values = {(row["start_line"], row["end_line"], row["kind"], row["node_type"]): row for row in facts}
    return [values[key] for key in sorted(values)]


def valid_literal(row):
    if type(row) is not dict or not literal_json(row):
        return False
    if any(type(row.get(key)) is not str or not row[key] for key in ("id", "path", "symbol", "source")):
        return False
    if any(type(row.get(key)) is not int for key in ("start_line", "end_line", "window_end_line")):
        return False
    low, high, full = (row[key] for key in ("start_line", "end_line", "window_end_line"))
    if not 1 <= low <= high <= full or type(row.get("truncated")) is not bool:
        return False
    source_sha = digest(row["source"].encode("utf-8"))
    if high != low + len(context.physical_lines(row["source"])) - 1 or row.get("source_hash") != source_sha:
        return False
    if type(row.get("content_hash")) is not str or re.fullmatch(r"[0-9a-f]{64}", row["content_hash"]) is None:
        return False
    if not row["truncated"] and (high != full or source_sha != row["content_hash"]):
        return False
    origin = row.get("source_origin")
    if "source_origin" in row and origin not in ("saved", "overlay"):
        return False
    if origin == "overlay":
        return (type(row.get("buffer_version")) is int and 0 <= row["buffer_version"] <= 2**53 - 1
                and type(row.get("buffer_hash")) is str and re.fullmatch(r"[0-9a-f]{64}", row["buffer_hash"]) is not None)
    return "buffer_version" not in row and "buffer_hash" not in row


def classify_row(row, *, index=1, definition_keys=None, parser_factory=direct_parser):
    require(type(index) is int and index >= 1, "Positive fact index required")
    value = row if type(row) is dict else {}
    identifier = value.get("id")
    key = canonical_definition(definition_keys.get(identifier)) if type(identifier) is str and type(definition_keys) is dict else None
    grammar = GRAMMARS.get(Path(value["path"]).suffix) if type(value.get("path")) is str else None
    fact = {"index": index, "id": identifier if type(identifier) is str else None,
        "path": value.get("path") if type(value.get("path")) is str else None,
        "symbol": value.get("symbol") if type(value.get("symbol")) is str else None,
        **{name: value.get(name) if type(value.get(name)) is int else None
           for name in ("start_line", "end_line", "window_end_line")},
        "row_sha256": None, "source_sha256": None, "definition_key": key, "grammar": grammar,
        "full_definition": False, "provenance": {}, "reasons": [], "nodes": []}
    try:
        require(literal_json(row), "Literal finite JSON required")
        fact["row_sha256"] = digest(canonical(row))
        if type(value.get("source")) is str:
            fact["source_sha256"] = digest(value["source"].encode("utf-8"))
        provenance = {name: value[name] for name in PROVENANCE if name in value}
        require(literal_json(provenance), "Literal provenance required")
        canonical(provenance)
        fact["provenance"] = deepcopy(provenance)
        valid = valid_literal(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        valid = False
    if not valid:
        fact["reasons"] = ["invalid_literal"]
        return fact
    identity = key is not None and key[:2] == [value["path"], value["symbol"]]
    clipped = value["truncated"] or value["end_line"] != value["window_end_line"]
    fact["full_definition"] = bool(identity and not clipped and key[2:4] == [value["start_line"], value["end_line"]])
    if clipped:
        fact["reasons"].append("clipped")
    reason = ("unsupported_grammar" if grammar is None else "definition_unverified" if not identity
              else "partial_definition" if not fact["full_definition"] else None)
    if reason:
        fact["reasons"].append(reason)
        return fact
    try:
        nodes = (python_writes(value["source"], value["start_line"]) if grammar == "python" else
                 javascript_writes(value["source"], value["start_line"], grammar, parser_factory=parser_factory))
    except (ImportError, ModuleNotFoundError, ValueError):
        fact["reasons"].append("parser_unavailable")
        return fact
    except (SyntaxError, RecursionError, OverflowError):
        fact["reasons"].append("parse_error")
        return fact
    if nodes is None:
        fact["reasons"].append("parse_context_uncertain")
    else:
        fact["nodes"] = ordered_nodes(nodes)
        if nodes:
            fact["reasons"].append("explicit_write")
    return fact


def response_rows(value):
    if type(value) is not dict or type(value.get("results")) is not list:
        return None
    try:
        if not literal_json(value):
            return None
    except (RecursionError, OverflowError):
        return None
    rows = value["results"]
    if any(type(row) is not dict or type(row.get("id")) is not str or not row["id"] for row in rows):
        return None
    return rows if len({row["id"] for row in rows}) == len(rows) else None


def reconstruct(reference, proposal, *, definition_keys=None, max_results=8, source_budget=12000,
                token_budget=4096, counter=None):
    # Budgets are checked independently in verify_record. This terminal guard
    # performs no allocation or token recount and preserves either operand whole.
    receipt = {"schema": SCHEMA, "mode": MODE, "reference_facts": [], "protected_ids": [],
        "violated_ids": [], "accepted": False, "fallback_reason": None,
        "calls": {name: 0 for name in ("v1", "model", "retrieval", "coalesce", "backfill")}}
    rows, proposed = response_rows(reference), response_rows(proposal)
    if rows is None:
        receipt["fallback_reason"] = "invalid_reference"
        return deepcopy(reference), receipt
    facts = [classify_row(row, index=index, definition_keys=definition_keys) for index, row in enumerate(rows, 1)]
    receipt["reference_facts"] = facts
    receipt["protected_ids"] = [fact["id"] for fact in facts if fact["reasons"]]
    if proposed is None:
        receipt["fallback_reason"] = "invalid_proposal"
        return deepcopy(reference), receipt
    available = {row["id"]: row for row in proposed}
    receipt["violated_ids"] = [row["id"] for row in rows if row["id"] in receipt["protected_ids"]
        and (row["id"] not in available or not context.same(row, available[row["id"]]))]
    receipt["accepted"] = not receipt["violated_ids"]
    receipt["fallback_reason"] = None if receipt["accepted"] else "protected_role_row_changed"
    return deepcopy(proposal if receipt["accepted"] else reference), receipt


def score(case, response):
    value = prior.quality(case, response)
    return {"evidence_ranks": value["ranks"], "covered_groups": value["groups_hit"], "complete": value["complete"],
            "single_file_first_hit": bool(not case["multi_file"] and value["first_hit"])}


def compare(case, before, after):
    a, b = score(case, before), score(case, after)
    return {"group_gains": [i for i, (x, y) in enumerate(zip(a["evidence_ranks"], b["evidence_ranks"]), 1)
                            if x is None and y is not None],
            "group_losses": [i for i, (x, y) in enumerate(zip(a["evidence_ranks"], b["evidence_ranks"]), 1)
                             if x is not None and y is None],
            "complete_gain": b["complete"] and not a["complete"],
            "complete_loss": a["complete"] and not b["complete"],
            "single_file_first_hit_loss": a["single_file_first_hit"] and not b["single_file_first_hit"]}


CALLS = {"role_guard": 1, **{name: 0 for name in ("current_v1", "model", "retrieval", "index", "coalesce", "backfill")}}


def verify_record(row, *, case, parent_proposal, parent_proposal_ref, counter):
    require(literal_json(row), "Finite literal outcome required")
    require(all(context.same(row.get(name), case[name]) for name in
        ("case_id", "key", "subset", "dataset", "query_id", "condition", "kind", "multi_file", "qualified")),
        "Original occurrence/label identity changed")
    require(context.same(case["limits"], LIMITS), "Original limits changed")
    require(context.same(row.get("parent_proposal_ref"), parent_proposal_ref)
            and context.same(row.get("input_sha256"), {"case": digest(canonical(case)),
                "coverage": digest(canonical(case["reference_response"])),
                "saved_v1_proposal": digest(canonical(parent_proposal["response"]))}), "Frozen guard input binding differs")
    response, receipt = reconstruct(case["reference_response"], parent_proposal["response"],
                                    definition_keys=case["definition_keys"])
    require(context.same(row.get("response"), response) and context.same(row.get("role_guard_receipt"), receipt),
            "Independent role guard response/receipt differs")
    require(all(valid_literal(source) for source in response["results"]), "Final literal source/provenance differs")
    payload = context.compact_payload(response)
    tokens = len(counter.encode(payload, disallowed_special=()))
    require(row.get("compact_payload") == payload == context.compact_payload(row["response"])
            and row.get("compact_sha256") == digest(payload.encode())
            and type(row.get("actual_compact_tokens")) is int and row["actual_compact_tokens"] == tokens
            and type(response["token_usage"]["tokens"]) is int and response["token_usage"]["tokens"] == tokens
            and tokens <= LIMITS["token_budget"] and len(response["results"]) <= LIMITS["max_results"]
            and type(response["total_source_chars"]) is int
            and response["total_source_chars"] == sum(len(item["source"]) for item in response["results"])
            <= LIMITS["source_budget"], "Exact compact envelope/source/token limits differ")
    require(context.same(row.get("calls"), CALLS), "One guard and zero fresh v1/model/retrieval/index/coalesce/backfill calls required")
    require(type(row.get("role_guard_s")) in (int, float) and math.isfinite(row["role_guard_s"])
            and row["role_guard_s"] >= 0, "Finite nonnegative CPU guard duration required")
    require(all(context.same(row.get(name), value) for name, value in score(case, response).items()),
            "Independent individual evidence score differs")
    comparisons = {name: compare(case, control, response) for name, control in
        (("legacy", case["legacy_control"]), ("coverage_control", case["reference_response"]),
         ("semantic_final_v1", parent_proposal["response"]))}
    require(context.same(row.get("comparisons"), comparisons), "Individual quality comparisons differ")
    return response, receipt


def audit_outcomes(cases, outcomes, proposals, references, *, counter, expected_rows=393, expected_units=201):
    by_id, actual = prior._unique(cases, "case_id"), prior._unique(outcomes, "case_id")
    require(len(cases) == expected_rows and len({(case["dataset"], case["query_id"]) for case in cases}) == expected_units
            and by_id.keys() == actual.keys() == proposals.keys() == references.keys(), "Complete guard occurrence/unit matrix required")
    responses = {}
    for identifier, case in by_id.items():
        responses[identifier], _ = verify_record(actual[identifier], case=case, parent_proposal=proposals[identifier],
                                               parent_proposal_ref=references[identifier], counter=counter)
    return prior.quality_gate(cases, responses)


def file_sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def jsonl(path):
    opener = gzip.open if Path(path).suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def runtime():
    import platform

    return {"python": platform.python_version(), "packages": {name: importlib.metadata.version(name)
        for name in ("tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript", "tiktoken")}}


def verify_blob(root, name, sha, commits=()):
    root = Path(root).resolve()
    path = (root / name).resolve()
    require(path.is_relative_to(root) and type(sha) is str and re.fullmatch(r"[0-9a-f]{64}", sha)
            and file_sha(path) == sha, "Bound bytes differ: " + name)
    require(all(digest(git(root, "show", commit + ":" + name)) == sha for commit in commits),
            "Committed bound bytes differ: " + name)


def verify_published_parents(root, bound, *, recheck=RECHECK):
    require(recheck == RECHECK, "Exact new parent recheck exemption required")
    for name, sha in bound.items():
        if name != recheck:
            verify_blob(root, name, sha, (PUBLICATION,))


def row_reference(path, number, row, condition=None):
    result = {"path": path, "line_number": number, "record_sha256": digest(canonical(row)),
        "response_sha256": digest(canonical(row["response"])), "compact_sha256": row["compact_sha256"]}
    if condition is not None:
        result["condition"] = condition
    return result


def rebuild_inputs(cases, proposals, controls, old_negatives, complementary, *, expected_rows=393,
                   expected_units=201, expected_old_negatives=2948):
    """Rebuild reference identity and exact record/response/compact bindings."""
    by_id = prior._unique(cases, "case_id")
    saved = prior._unique(proposals, "case_id")
    controls_by_id = prior._unique(controls, "case_id")
    require(len(by_id) == len(proposals) == len(controls) == len(complementary) == expected_rows
            and by_id.keys() == saved.keys() == controls_by_id.keys()
            and len({(case["dataset"], case["query_id"]) for case in cases}) == expected_units
            and len(old_negatives) == expected_old_negatives, "Complete original cases/controls/proposals/negative matrix required")
    references = {row["case_id"]: {"case_id": row["case_id"], "key": row["key"],
        **row_reference(PARENT + "/replay/outcomes.jsonl.gz", number, row)} for number, row in enumerate(proposals, 1)}
    manifests = []
    for case in cases:
        proposal = saved[case["case_id"]]
        require(context.same(proposal["key"], case["key"])
                and all(context.same(proposal[name], case[name]) for name in ("reference_response", "definition_keys", "limits"))
                and context.same(case["limits"], LIMITS), "Original saved proposal operand identity differs")
        payload = context.compact_payload(proposal["response"])
        require(proposal["compact_payload"] == payload and proposal["compact_sha256"] == digest(payload.encode()),
                "Original saved proposal compact bytes differ")
        require(context.same(controls_by_id[case["case_id"]]["controls"], case["control_references"])
                and set(case["control_references"]) == {"legacy", "coverage_control"}, "Both original controls required")
        manifests.append({"case_id": case["case_id"], "key": case["key"], "case_sha256": digest(canonical(case)),
            "coverage_sha256": digest(canonical(case["reference_response"])),
            "saved_v1_proposal_sha256": digest(canonical(proposal["response"])),
            "parent_record_sha256": digest(canonical(proposal)), "legacy_sha256": digest(canonical(case["legacy_control"])),
            "groups_sha256": digest(canonical(case["groups"]))})
    require(len({tuple(row[name] for name in ("subset", "dataset", "query_id", "condition")) for row in complementary}) == expected_rows
            and {tuple(row[name] for name in ("subset", "dataset", "query_id", "condition")) for row in complementary}
            == {tuple(case["key"]) for case in cases}, "All complementary-v2 negative identities required")
    negatives = deepcopy(old_negatives)
    negatives.extend(row_reference(COMPLEMENTARY + "/replay.jsonl.gz", number, row, "complementary_v2")
                     for number, row in enumerate(complementary, 1))
    negatives.extend(row_reference(PARENT + "/replay/outcomes.jsonl.gz", number, row, "semantic_final_v1")
                     for number, row in enumerate(proposals, 1))
    require(len({(row["path"], row["line_number"]) for row in negatives}) == len(negatives),
            "Historical negative references overlap")
    return {"input-manifest.json": {"schema": STUDY_SCHEMA, "cases": manifests,
                "cases_path": PARENT + "/prepared-v3/operands.json", "new_guard_outcomes_computed": 0},
        "parent-outcome-references.json": [references[case["case_id"]] for case in cases],
        "control-references.json": deepcopy(controls), "negative-references.json": negatives}


def verify_archive(root, manifest):
    archive = Path(root) / PARENT / "native-capture.tar.gz"
    require(manifest.get("status") == "passed_lossless_archive" and len(manifest.get("members", {})) == 6
            and archive.stat().st_size == manifest.get("archive_bytes")
            and file_sha(archive) == manifest.get("archive_sha256"), "Lossless original native archive differs")
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        require(len(members) == 6 and {member.name for member in members} == set(manifest["members"])
                and all(member.isfile() for member in members), "Exact six native regular members required")
        for member in members:
            require(not Path(member.name).is_absolute() and ".." not in Path(member.name).parts
                    and "\\" not in member.name, "Native archive member path is unsafe")
            expected = manifest["members"][member.name]
            require(member.size == expected["bytes"], "Native archive member size differs")
            value = hashlib.sha256()
            with stream.extractfile(member) as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    value.update(block)
            require(value.hexdigest() == expected["sha256"], "Native archive member SHA differs")


def verify_parent_binding(root, binding):
    """Independently bind immutable original/audit-correction/native parents."""
    root = Path(root).resolve()
    require(binding.get("parent_recheck_path") == RECHECK, "Exact new parent recheck path required")
    folder = root / PARENT / "prepared-v3"
    plan = read(folder / "frozen-plan.json")
    original = {**plan["source_sha256"], **plan["historical_artifact_sha256"],
        plan["model_binding"]["path"]: plan["model_binding"]["sha256"], **plan["retrieval_conditions"]["source_sha256"],
        **{(folder / name).relative_to(root).as_posix(): sha for name, sha in plan["artifact_sha256"].items()},
        (folder / "frozen-plan.json").relative_to(root).as_posix(): file_sha(folder / "frozen-plan.json")}
    require(len(original) == 147 and binding.get("original_bound_blob_count") == 147
            and binding.get("original_freeze") == ORIGINAL_FREEZE, "Original complete 147-blob identity differs")
    for name, sha in original.items():
        verify_blob(root, name, sha, (ORIGINAL_FREEZE, "HEAD"))
    correction_path = PARENT + "/auditor-recheck-plan.json"
    correction = read(root / correction_path)
    require(correction.get("original_study_freeze") == ORIGINAL_FREEZE and correction.get("expected_normalizations") == 26,
            "Original post-outcome audit correction differs")
    names = [binding["parent_recheck_path"], correction_path, PARENT + "/independent-audit-recheck-1.json",
        PARENT + "/audit-attempt-1/audit.log", PARENT + "/native-capture-manifest.json", PARENT + "/native-capture.tar.gz",
        PARENT + "/replay/outcomes.jsonl.gz", PARENT + "/replay/summary.json", PARENT + "/replay/replay-start.json",
        COMPLEMENTARY + "/replay.jsonl.gz", COMPLEMENTARY + "/summary.json", COMPLEMENTARY + "/independent-audit.json",
        COMPLEMENTARY + "/frozen-plan.json", COMPLEMENTARY + "/run-start.json"]
    expected = {**original, **correction["source_sha256"]}
    expected.update({name: file_sha(root / name) for name in names})
    reports = [read(root / PARENT / "independent-audit-recheck-1.json"), read(root / binding["parent_recheck_path"])]
    for report in reports:
        require(report.get("status") == "passed" and report.get("checks_passed") is True
                and report.get("verified_candidate_rows") == 393 and report.get("exact_control_rows") == 786
                and report.get("distinct_query_units") == 201 and report.get("retained_negative_references") == 2948
                and report.get("producer_freeze", {}).get("commit") == ORIGINAL_FREEZE
                and report.get("producer_freeze", {}).get("bound_blob_count") == 147
                and report.get("model_calls") == report.get("retrieval_calls") == report.get("network_calls") == 0,
                "Complete independent parent recheck required")
        proof = report["post_outcome_auditor_correction"]
        require(proof.get("normalization_count") == 26 and len(proof.get("normalizations", [])) == 26
                and proof.get("plan_sha256") == file_sha(root / correction_path)
                and proof.get("source_sha256") == correction["source_sha256"], "Immutable correction proof differs")
        correction_bound = {**correction["source_sha256"], correction_path: file_sha(root / correction_path),
            correction["original_audit_failure"]["path"]: correction["original_audit_failure"]["sha256"]}
        for name, sha in correction_bound.items():
            verify_blob(root, name, sha, (proof["audit_freeze"], proof["audit_execution_commit"], "HEAD"))
        require(datetime.fromisoformat(git(root, "show", "-s", "--format=%cI", proof["audit_freeze"]).decode().strip())
                <= datetime.fromisoformat(proof["started_at"]), "Correction must precede its parent audit")
    require(context.same(reports[0]["independently_recomputed_qualification"], reports[1]["independently_recomputed_qualification"])
            and reports[0]["post_outcome_auditor_correction"]["audit_freeze"] ==
                reports[1]["post_outcome_auditor_correction"]["audit_freeze"], "Parent audit changed historical quality")
    archive = read(root / PARENT / "native-capture-manifest.json")
    verify_archive(root, archive)
    require(context.same(binding.get("native_archive_members"), archive["members"]), "Bound native member identities differ")
    for name, sha in correction["immutable_result_sha256"].items():
        if name == PARENT + "/capture/capture.json":
            require(archive["members"]["capture/capture.json"]["sha256"] == sha, "Archived immutable capture differs")
        else:
            verify_blob(root, name, sha)
    comp_folder = root / COMPLEMENTARY
    comp, comp_plan, comp_start = (read(comp_folder / name) for name in ("independent-audit.json", "frozen-plan.json", "run-start.json"))
    require(comp.get("status") == "passed" and comp.get("checks_passed") is True
            and comp.get("verified_candidate_rows") == 393 and comp.get("exact_control_rows") == 786
            and comp.get("distinct_query_units") == 201 and comp.get("retained_negative_references") == 2948,
            "Complete complementary-v2 independent parent audit required")
    frozen, execution = comp["producer_freeze"]["commit"], comp["producer_execution"]
    require(context.same(comp_start["prospective_freeze"], comp["producer_freeze"])
            and comp_start["execution_commit"] == execution, "Complementary original producer identities differ")
    comp_blobs = {**comp_plan["producer_sha256"], **comp_plan["input_sha256"],
        COMPLEMENTARY + "/frozen-plan.json": file_sha(comp_folder / "frozen-plan.json")}
    require(len(comp_blobs) == comp["producer_freeze"]["bound_blob_count"], "Complete complementary original freeze closure required")
    for name, sha in comp_blobs.items():
        require(all(digest(git(root, "show", commit + ":" + name)) == sha for commit in (frozen, execution)),
                "Historical complementary frozen producer changed")
    for name, sha in comp["auditor_sha256"].items():
        require(digest(git(root, "show", comp["auditor_execution_commit"] + ":" + name)) == sha,
                "Historical complementary independent auditor changed")
    comp_artifacts = {COMPLEMENTARY + "/" + name: sha for name, sha in comp["artifact_sha256"].items()}
    comp_artifacts[COMPLEMENTARY + "/independent-audit.json"] = file_sha(comp_folder / "independent-audit.json")
    for name, sha in comp_artifacts.items():
        verify_blob(root, name, sha)
    expected.update(comp_artifacts)
    comp_binding = {"producer_freeze": frozen, "producer_execution": execution,
        "auditor_execution": comp["auditor_execution_commit"], "bound_git_blob_sha256": comp_blobs,
        "auditor_git_blob_sha256": comp["auditor_sha256"], "artifact_sha256": comp_artifacts}
    require(context.same(binding.get("complementary_parent"), comp_binding)
            and context.same(binding.get("sha256"), expected)
            and binding.get("publication_commit") == PUBLICATION
            and binding.get("model_calls") == binding.get("current_v1_calls") == 0, "Complete unchanged parent closure differs")
    for name, sha in expected.items():
        verify_blob(root, name, sha)
    verify_published_parents(root, expected, recheck=binding["parent_recheck_path"])
    return {"bound_parent_blobs": len(expected), "original_bound_blobs": 147,
            "native_archive_members": 6, "parent_recheck_path": binding["parent_recheck_path"]}


def audit_preparation(root, prepared_dir, counter):
    root, folder = Path(root).resolve(), Path(prepared_dir).resolve()
    require(folder.is_relative_to(root) and not (folder / "prepare-failure.json").exists(), "Successful owned preparation required")
    plan = read(folder / "frozen-plan.json")
    require(plan.get("schema") == STUDY_SCHEMA and plan.get("mode") == MODE and context.same(plan.get("settings"), LIMITS)
            and set(plan.get("source_sha256", {})) == set(SOURCES) and set(plan.get("artifact_sha256", {})) == set(ARTIFACTS),
            "Complete new prospective source/plan/artifact closure required")
    require(context.same(plan.get("runtime"), runtime()) and plan.get("default_change") is False
            and plan.get("reserved_attrs_access") is False, "Pinned role runtime or scope differs")
    for name, sha in plan["source_sha256"].items():
        verify_blob(root, name, sha)
    for name, sha in plan["artifact_sha256"].items():
        require(file_sha(folder / name) == sha, "Prepared artifact bytes changed")
    parents = verify_parent_binding(root, plan["parent_binding"])
    old = prior.audit_preparation(root, root / PARENT / "prepared-v3", counter)
    cases = old["cases"]
    proposals = jsonl(root / PARENT / "replay/outcomes.jsonl.gz")
    controls = read(root / PARENT / "prepared-v3/control-references.json")
    negatives = read(root / PARENT / "prepared-v3/negative-references.json")
    rebuilt = rebuild_inputs(cases, proposals, controls, negatives, jsonl(root / COMPLEMENTARY / "replay.jsonl.gz"))
    require(all(context.same(read(folder / name), rebuilt[name]) for name in ARTIFACTS), "Independently rebuilt role inputs differ")
    # Every loaded independent authority must execute the exact bound source,
    # including helpers imported adaptively while reconstructing old controls.
    authority = {**plan["source_sha256"], **plan["parent_binding"]["sha256"]}
    for name, module in tuple(sys.modules.items()):
        if name.startswith("audit_") or name == "complementary_inputs":
            filename = getattr(module, "__file__", None)
            require(filename is not None, "Loaded independent helper lacks source authority")
            path = Path(filename).resolve()
            require(path.is_relative_to(root / "scripts") and path.relative_to(root).as_posix() in authority,
                    "Loaded independent helper escapes bound source closure")
            require(file_sha(path) == authority[path.relative_to(root).as_posix()], "Executing independent helper bytes differ")
    require(context.same(plan.get("counts"), {"cases": 393, "distinct_units": 201, "control_references": 786,
        "old_negative_references": 2948, "complementary_v2_negative_references": 393,
        "semantic_v1_negative_references": 393, "total_negative_references": 3734})
        and context.same(plan.get("calls_before_freeze"), {name: 0 for name in CALLS}), "Counts or prospective no-outcome barrier differs")
    return {"plan": plan, "cases": cases, "parent_proposals": prior._unique(proposals, "case_id"),
        "proposal_references": prior._unique(rebuilt["parent-outcome-references.json"], "case_id"),
        "negatives": rebuilt["negative-references.json"], "parent_verification": parents}


def verify_freeze(root, prepared_dir, prepared, binding, started_at):
    root, folder = Path(root).resolve(), Path(prepared_dir).resolve()
    freeze, execution = binding["freeze"]["commit"], binding["execution_commit"]
    require(all(type(commit) is str and re.fullmatch(r"[0-9a-f]{40}", commit) for commit in (freeze, execution)),
            "Full immutable prospective/execution commits required")
    for first, last in ((freeze, execution), (execution, "HEAD")):
        require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", first, last],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0, "Role prospective execution ancestry differs")
    require(datetime.fromisoformat(git(root, "show", "-s", "--format=%cI", freeze).decode().strip())
            <= datetime.fromisoformat(started_at), "Role freeze must precede observed guard outcomes")
    plan = prepared["plan"]
    bound = {**plan["source_sha256"], **plan["parent_binding"]["sha256"],
        **{(folder / name).relative_to(root).as_posix(): sha for name, sha in plan["artifact_sha256"].items()},
        (folder / "frozen-plan.json").relative_to(root).as_posix(): file_sha(folder / "frozen-plan.json")}
    for name, sha in bound.items():
        verify_blob(root, name, sha, (freeze, execution, "HEAD"))
    require(context.same(binding["freeze"], {"commit": freeze, "execution_commit": execution, "bound_blob_count": len(bound),
        "plan_sha256": file_sha(folder / "frozen-plan.json"), "new_guard_outcomes_before_freeze": 0}), "Complete prospective guard receipt differs")
    return {"commit": freeze, "execution_commit": execution, "bound_blob_count": len(bound)}


def summary_gate(outcomes, gate):
    losses = {name: [] for name in prior.CONTROLS}
    for row in outcomes:
        for name in losses:
            value = row["comparisons"][name]
            if value["group_losses"] or value["complete_loss"] or value["single_file_first_hit_loss"]:
                losses[name].append({"case_id": row["case_id"], **value})
    return {"qualified": gate["qualified"], "individual_losses": losses,
        "additional_distinct_complete_multi_queries_vs_coverage": gate["additional_distinct_complete_multi_queries_vs_coverage"],
        "automatic_default_change": False, "fresh_and_native_validation_still_required": True}


def aggregate(cases, outcomes, proposals):
    rows = {row["case_id"]: row for row in outcomes}
    totals = {}
    for case in cases:
        for arm, response in (("legacy", case["legacy_control"]), ("coverage_control", case["reference_response"]),
                              ("semantic_final_v1", proposals[case["case_id"]]["response"]),
                              (MODE, rows[case["case_id"]]["response"])):
            key = "/".join(case[name] for name in ("subset", "dataset", "condition")) + "/" + arm
            value = prior.quality(case, response)
            count = totals.setdefault(key, {"rows": 0, "required_groups": 0, "covered_groups": 0, "complete": 0,
                                           "multi_file_complete": 0, "single_file_first_hit": 0})
            count["rows"] += 1
            count["required_groups"] += len(case["groups"])
            count["covered_groups"] += value["groups_hit"]
            count["complete"] += value["complete"]
            count["multi_file_complete"] += bool(case["multi_file"] and value["complete"])
            count["single_file_first_hit"] += bool(not case["multi_file"] and value["first_hit"])
    return totals


def audit(root, prepared_dir, replay_dir, counter):
    root, replay = Path(root).resolve(), Path(replay_dir).resolve()
    prepared = audit_preparation(root, prepared_dir, counter)
    require(not (replay / "failure.json").exists(), "Failed guard attempt cannot qualify")
    summary, start = read(replay / "summary.json"), read(replay / "run-start.json")
    require(start.get("schema") == STUDY_SCHEMA and start.get("mode") == MODE, "Role attempt identity differs")
    frozen = verify_freeze(root, prepared_dir, prepared, start["binding"], start["started_at"])
    require(set(summary.get("artifact_sha256", {})) == {"outcomes.jsonl.gz", "run-start.json"}, "Complete finalized gzip artifact closure required")
    for name, sha in summary["artifact_sha256"].items():
        require(file_sha(replay / name) == sha, "Role execution artifact bytes differ")
    outcomes = jsonl(replay / "outcomes.jsonl.gz")
    gate = audit_outcomes(prepared["cases"], outcomes, prepared["parent_proposals"], prepared["proposal_references"], counter=counter)
    activity = {"accepted": sum(row["role_guard_receipt"]["accepted"] for row in outcomes),
        "fallback_reasons": dict(Counter(row["role_guard_receipt"]["fallback_reason"] or "accepted" for row in outcomes))}
    require(summary.get("schema") == STUDY_SCHEMA and summary.get("mode") == MODE and summary.get("status") == "passed"
            and context.same(summary.get("quality_gate"), summary_gate(outcomes, gate))
            and summary.get("stored_candidate_rows") == 393 and summary.get("distinct_dataset_query_units") == 201
            and summary.get("referenced_control_rows") == 786 and summary.get("referenced_saved_v1_proposals") == 393
            and summary.get("retained_negative_references") == 3734 and context.same(summary.get("role_guard_activity"), activity)
            and context.same(summary.get("candidate_phase_calls"), {name: 393 * count for name, count in CALLS.items()}),
            "Independent role study counts/activity/individual quality gate differ")
    reasons = Counter(reason for row in outcomes for fact in row["role_guard_receipt"]["reference_facts"] for reason in fact["reasons"])
    return {"schema": "role_context_audit_v1", "status": "passed", "checks_passed": True,
        "verified_candidate_rows": 393, "exact_control_rows": 786, "referenced_saved_v1_proposals": 393,
        "retained_negative_references": 3734, "distinct_query_units": 201, "producer_freeze": frozen,
        "parent_verification": prepared["parent_verification"], "independently_recomputed_qualification": gate,
        "role_guard_activity": activity, "protected_reason_occurrences": dict(sorted(reasons.items())),
        "metrics_by_original_condition": aggregate(prepared["cases"], outcomes, prepared["parent_proposals"]),
        "candidate_phase_calls": {name: 393 * count for name, count in CALLS.items()},
        "model_calls": 0, "retrieval_calls": 0, "network_calls": 0, "attrs_reads": 0,
        "native_warm_latency_measured": False, "automatic_default_change": False,
        "classification": "Already-observed development, with immutable saved v1 proposals and post-outcome rule design"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--check-controls", "--check-inputs", dest="check_controls", action="store_true")
    parser.add_argument("--tokenizer-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    require(args.check_controls or args.replay is not None, "Select control preparation audit or complete replay audit")
    require(not args.output.exists(), "Independent role audit output must be new")
    require(Path(__file__).resolve() == root / "scripts/audit_role_context.py"
            and Path(context.__file__).resolve() == root / "scripts/audit_semantic_context.py"
            and Path(prior.__file__).resolve() == root / "scripts/audit_semantic_study.py", "Executing independent audit source differs")
    forbidden = ("semantic_search", "research_role_context", "run_role_context", "semantic_model_study", "semantic_study_inputs",
                 "mlx", "torch", "numpy", "transformers", "jax")
    require(not any(any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden) for name in sys.modules),
            "Independent role process contains producer/model imports")

    class Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if any(fullname == prefix or fullname.startswith(prefix + ".") or fullname.startswith("replay_") for prefix in forbidden):
                raise RuntimeError("Independent role audit forbids producer/model imports: " + fullname)

    def deny(*values, **kwargs):
        raise RuntimeError("Independent role audit forbids network")

    guard = Guard()
    sys.meta_path.insert(0, guard)
    try:
        with patch.object(socket, "create_connection", deny), patch.object(socket, "getaddrinfo", deny), \
             patch.object(socket.socket, "connect", deny), patch.object(socket.socket, "connect_ex", deny), \
             patch.object(socket.socket, "sendto", deny):
            from audit_selection import offline_encoding

            counter = offline_encoding(args.tokenizer_cache or root / ".cache/tokenizers")
            if args.check_controls:
                prepared = audit_preparation(root, args.prepared, counter)
                result = {"schema": "role_context_audit_v1", "status": "passed", "exact_control_rows": 786,
                    "referenced_saved_v1_proposals": 393, "retained_negative_references": 3734,
                    "candidate_outcomes_computed": 0, "model_calls": 0, "retrieval_calls": 0, "network_calls": 0,
                    "parent_verification": prepared["parent_verification"]}
            else:
                result = audit(root, args.prepared, args.replay, counter)
    finally:
        sys.meta_path.remove(guard)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
