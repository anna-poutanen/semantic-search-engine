"""Independent literal Python template-role and whole-response audit.

No candidate guard, study producer, backend, model or numeric inference module
is imported.  Source snapshots and the compact envelope use frozen preparation
helpers; AST classification, terminal decisions and quality are reconstructed
here, without executing captured corpus modules.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import importlib.abc
import json
import math
from pathlib import Path
import re
import socket
import subprocess
import sys
from unittest.mock import patch

from experiments.dependency_context import study_inputs as inputs
from experiments.dependency_context import audit as prior_audit


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "template_preservation_study_v1"
CLASSIFICATION = "observed_development_template_preservation"
GUARD_SCHEMA = "returned_template_guard_v1"
MODE = "returned_template_terminal_v1"
PARENT = "b7bcac5d66e5ae6e3c2798fb5b3703fc06de64bf"
PREPARED = "results/dependency-context-continuation-2026-11-09/prepared"
NO_WORK = {name: 0 for name in ("model", "retrieval", "index", "coalesce", "backfill", "v1")}
SOURCE_ONLY_CALLS = deepcopy(inputs.ZERO_CALLS)
GATE = {"zero_individual_losses_against": ["legacy", "coverage", "prior_role"],
        "at_least_one_distinct_new_complete_multi_positive_vs": "prior_role"}
REMAINING_GATES = {"native_median_max_ratio_vs_each_control": 1.25, "fresh_codebase_required": True}
PARENT_FOLDERS = ("results/dependency-context-development-2026-11-09",
    "results/dependency-context-continuation-2026-11-09", "results/provider-native-preflight-2026-11-10",
    "results/provider-native-observed-2026-11-10", "results/provider-native-observed-v2-2026-11-10")
NATIVE = PARENT_FOLDERS[-1]
BLOCKED = {"mlx", "mlx_embeddings", "torch", "transformers", "safetensors", "sentence_transformers", "numpy"}
FORBIDDEN = {"experiments.template_preservation.guard", "experiments.template_preservation.study",
             "semantic_search.backend", "semantic_search.embeddings", "semantic_search.index", "semantic_search.store"}


def exact_same(left, right):
    """Typed JSON values including signed-zero spelling; object order excluded."""
    return inputs.same(left, right) and inputs.canonical(left) == inputs.canonical(right)


def exact_operand(left, right):
    """The selected full saved operand preserves nested insertion order too."""
    return exact_same(left, right) and json.dumps(left, ensure_ascii=False, allow_nan=False, separators=(",", ":")) == \
        json.dumps(right, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def source_authority(root=ROOT, authority=None):
    root = Path(root).resolve()
    inputs.require(Path(__file__).resolve() == root / "experiments/template_preservation/audit.py",
                   "Executing independent auditor path differs")
    for name, module in tuple(sys.modules.items()):
        if name == "experiments" or name.startswith("experiments."):
            expected = root / "experiments"
        elif name == "semantic_search" or name.startswith("semantic_search."):
            expected = root / "src/semantic_search"
        elif name.startswith("scripts.") or name.startswith("audit_"):
            expected = root / "scripts"
        else:
            continue
        path = getattr(module, "__file__", None)
        inputs.require(path is not None and Path(path).resolve().is_relative_to(expected),
                       "Independent loaded source escapes checkout: " + name)
        if authority is not None:
            relative = Path(path).resolve().relative_to(root).as_posix()
            inputs.require(authority.get(relative) == inputs.file_sha(path),
                           "Executing helper absent from frozen source authority: " + name)


@contextmanager
def cpu_only():
    """Deny inference, candidate/producer imports and outbound sockets in CLI."""
    inputs.require(not any(name.split(".")[0] in BLOCKED or any(name == bad or name.startswith(bad + ".")
                   for bad in FORBIDDEN) for name in sys.modules), "Independent CLI requires a clean CPU process")

    class RefuseInference(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in BLOCKED or any(fullname == bad or fullname.startswith(bad + ".") for bad in FORBIDDEN):
                raise RuntimeError("Inference/candidate/producer import forbidden: " + fullname)

    def denied(*args, **kwargs):
        raise RuntimeError("Network forbidden in independent returned-template audit")

    finder = RefuseInference()
    sys.meta_path.insert(0, finder)
    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
             patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
             patch.object(socket.socket, "sendto", denied):
            yield
    finally:
        sys.meta_path.remove(finder)


def canonical_definitions(source, path):
    """Keep lexical names and decorated physical spans; duplicates stay distinct."""
    tree, definitions = ast.parse(source, filename=path), {}

    def visit(node, scope=()):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbol = ".".join((*scope, child.name))
                low = min([child.lineno, *(item.lineno for item in child.decorator_list)])
                definitions[path, symbol, low, child.end_lineno] = child
                visit(child, (*scope, child.name))
            else:
                visit(child, scope)

    visit(tree)
    return definitions


def literal_witness(source, path, node):
    """AST byte columns are UTF-8 offsets, not Python character positions."""
    lines = inputs.physical_lines(source)
    low = sum(len(line.encode()) for line in lines[:node.lineno-1]) + node.col_offset
    high = sum(len(line.encode()) for line in lines[:node.end_lineno-1]) + node.end_col_offset
    raw = source.encode()[low:high]
    return {"path": path, "start_line": node.lineno, "end_line": node.end_lineno,
            "start_byte": low, "end_byte": high, "source": raw.decode(), "sha256": inputs.sha(raw)}


def template_nodes(function):
    """Only immediate calls in direct returned f-string fields qualify."""
    result = []

    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, ast.Return) and isinstance(node.value, ast.JoinedStr):
            for field in node.value.values:
                if isinstance(field, ast.FormattedValue) and isinstance(field.value, ast.Call):
                    result.append((node, node.value, field, field.value))
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in function.body:
        visit(statement)
    return result


def source_hashes(root=ROOT):
    root = Path(root).resolve()
    paths = {path for folder in ("src", "scripts", "experiments", "tests") for path in (root / folder).rglob("*.py")}
    paths.update(root.glob("requirements*.lock"))
    paths.update(root / name for name in ("docs/template-preservation-protocol.md", "pyproject.toml",
        "requirements-mac.lock", "results/semantic-model-development-2026-11-02/model-binding.json"))
    return {path.relative_to(root).as_posix(): inputs.file_sha(path) for path in sorted(paths)}


def ancestor(root, earlier, later):
    inputs.require(all(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value) for value in (earlier, later)),
                   "Full immutable Git commits required")
    inputs.require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
                                  capture_output=True).returncode == 0, "Required commit ancestry changed")


def parent_binding(root=ROOT):
    """Reconstruct the complete retained input, failure and result closure."""
    root = Path(root).resolve()
    result = dict(inputs.parent_binding(root)["sha256"])

    def include(values):
        inputs.require(type(values) is dict and bool(values), "Explicit transitive parent byte map required")
        for name, value in values.items():
            inputs.require(name not in result or result[name] == value, "Conflicting transitive parent binding")
            result[name] = value

    for folder in PARENT_FOLDERS:
        paths = inputs.git(root, "ls-tree", "-r", "--name-only", PARENT, "--", folder).decode().splitlines()
        inputs.require(bool(paths), "Published retained parent tree is missing: " + folder)
        include({name: inputs.sha(inputs.git(root, "show", PARENT + ":" + name)) for name in paths})
    dependency = "results/dependency-context-continuation-2026-11-09"
    freeze = inputs.read(root / dependency / "study-freeze.json")
    include(freeze["sources"])
    include(freeze["artifacts"])
    failure = inputs.read(root / dependency / "preflight/failure-authority.json")
    include(failure["original_failure_artifact_sha256"])
    for folder in ("results/provider-native-observed-2026-11-10", NATIVE):
        plan = inputs.read(root / folder / "plan.json")
        include(plan["parent"]["artifacts"])
    include(inputs.read(root / NATIVE / "plan.json")["source_freeze"]["sha256"])
    first = inputs.read(root / "results/provider-native-observed-2026-11-10/plan.json")["source_freeze"]
    latest = inputs.read(root / NATIVE / "plan.json")["source_freeze"]
    historical = [
        {"commit": "82ad69cccfcf41acfbd97962c406861043b23c44", "sha256": inputs.read(root / PARENT_FOLDERS[0] / "study-freeze.json")["sources"]},
        {"commit": "aa8e5824a490f963339fc0b8de803c6a8456e9b4", "sha256": freeze["sources"]},
        {"commit": "dab55c239cf3a451ec7c389f0133c84ad7917e22", "sha256": inputs.read(root / "results/provider-native-preflight-2026-11-10/validation.json")["source_sha256"]},
        {"commit": first["commit"], "sha256": first["sha256"]},
        {"commit": latest["commit"], "sha256": latest["sha256"]}]
    for original in historical:
        ancestor(root, original["commit"], PARENT)
        for name, expected in original["sha256"].items():
            inputs.require(type(name) is str and not Path(name).is_absolute() and ".." not in Path(name).parts
                           and type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected)
                           and inputs.sha(inputs.git(root, "show", original["commit"] + ":" + name)) == expected,
                           "Earlier source freeze changed at its own historical commit")
    inputs.verify_blobs(root, result, commit=PARENT)
    inputs.verify_blobs(root, result, commit=inputs.git(root, "rev-parse", "HEAD").decode().strip())
    dependency_audit = inputs.read(root / dependency / "independent-audit.json")
    inputs.require(dependency_audit.get("status") == "passed" and dependency_audit.get("checks_passed") is True
                   and dependency_audit["independently_recomputed"]["quality_gate"]["qualified"] is False,
                   "Retain failed dependency qualification separately from successful integrity")
    native_audit = inputs.read(root / NATIVE / "independent-audit.json")
    inputs.require(native_audit.get("status") == "passed" and native_audit.get("checks_passed") is True
                   and native_audit.get("verified_requests") == 336 and native_audit.get("verified_warmups") == 8
                   and native_audit.get("verified_role_pairs") == 84 and native_audit.get("exact_fast_original_parity") is True
                   and inputs.read(root / NATIVE / "attempt/summary.json")["metrics"]["role_arms"]["fast_role"]
                       ["dense_promotion_gate_passed"] is False,
                   "Retain complete independently audited native provider outcomes")
    return {"commit": PARENT, "artifacts": dict(sorted(result.items())), "historical_sources": historical}


def verify_source_freeze(plan, *, root=ROOT):
    """Verify new executing source before inspecting observed parent operands."""
    root = Path(root).resolve()
    frozen = plan.get("source_freeze")
    inputs.require(type(frozen) is dict and set(frozen) == {"commit", "sha256", "bound_blob_count", "committed_at_unix"},
                   "Complete source-first freeze required")
    expected = source_hashes(root)
    inputs.require(inputs.same(frozen["sha256"], expected) and type(frozen["bound_blob_count"]) is int
                   and frozen["bound_blob_count"] == len(expected) and type(frozen["committed_at_unix"]) is int,
                   "Frozen executing source membership or bytes changed")
    head = inputs.git(root, "rev-parse", "HEAD").decode().strip()
    ancestor(root, PARENT, frozen["commit"])
    ancestor(root, frozen["commit"], head)
    inputs.verify_blobs(root, expected, commit=frozen["commit"])
    inputs.verify_blobs(root, expected, commit=head)
    actual_time = int(inputs.git(root, "show", "-s", "--format=%ct", frozen["commit"]))
    inputs.require(actual_time == frozen["committed_at_unix"],
                   "Source freeze commit/time binding differs")
    ancestor(root, frozen["commit"], plan.get("frozen_from_commit"))
    ancestor(root, plan["frozen_from_commit"], head)
    inputs.verify_blobs(root, expected, commit=plan["frozen_from_commit"])
    source_authority(root, expected)
    return frozen, head


def verify_plan(results, *, root=ROOT):
    root, results = Path(root).resolve(), Path(results).resolve()
    inputs.require(results.is_relative_to(root), "Study plan must be within repository")
    plan = inputs.read(results / "plan.json")
    frozen, head = verify_source_freeze(plan, root=root)
    inputs.require(plan.get("schema") == SCHEMA and type(plan.get("version")) is int and plan["version"] == 1
                   and plan.get("classification") == CLASSIFICATION
                   and plan.get("candidate") == "returned_template_v1"
                   and inputs.same(plan.get("candidate_operands"), ["reference", "proposal", "source_bundle", "definition_keys"])
                   and inputs.same(plan.get("counts"), inputs.COUNTS) and inputs.same(plan.get("limits"), inputs.LIMITS)
                   and inputs.same(plan.get("gate"), GATE) and inputs.same(plan.get("remaining_gates"), REMAINING_GATES),
                   "Frozen candidate/declarations/counts/limits/gates differ")
    inputs.require(inputs.same(plan.get("parent"), parent_binding(root)),
                   "Complete retained parent source/input/failure/result closure differs")
    prepared = {str((Path(PREPARED) / name).as_posix()): inputs.file_sha(root / PREPARED / name)
                for name in (*inputs.ARTIFACTS, "frozen-plan.json")}
    inputs.require(inputs.same(plan.get("prepared"), {"path": PREPARED, "artifacts": prepared}),
                   "Exact original six prepared input paths/bytes required")
    inputs.verify_blobs(root, prepared, commit=PARENT)
    inputs.verify_blobs(root, prepared, commit=head)
    actual_runtime = prior_audit.runtime(root)
    inputs.require(inputs.same(plan.get("runtime"), actual_runtime), "Exact offline parser/tokenizer runtime changed")
    when = datetime.fromisoformat(plan["frozen_at_utc"])
    inputs.require(when.tzinfo is not None and frozen["committed_at_unix"] <= when.timestamp()
                   <= datetime.now(timezone.utc).timestamp(), "Plan must follow source freeze")
    inputs.require(not inputs.git(root, "status", "--porcelain", "--untracked-files=no").strip(),
                   "Tracked source/input authority must be clean")
    return plan


def audit_inputs(results, *, root=ROOT):
    root, results = Path(root).resolve(), Path(results).resolve()
    plan = verify_plan(results, root=root)
    prepared = inputs.load_prepared(root / PREPARED, root=root, verify=True)
    inputs.require(inputs.same(prepared["plan"]["counts"], plan["counts"]), "Prepared and prospective study counts differ")
    report = {"schema": "template_preservation_input_audit_v1", "status": "passed", "checks_passed": True,
        "classification": CLASSIFICATION, "plan_sha256": inputs.file_sha(results / "plan.json"),
        "source_freeze": deepcopy(plan["source_freeze"]), "parent": deepcopy(plan["parent"]),
        "prepared": deepcopy(plan["prepared"]), "counts": deepcopy(plan["counts"]), "runtime": deepcopy(plan["runtime"]),
        "execution_commit": inputs.git(root, "rev-parse", "HEAD").decode().strip(),
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_only_calls": deepcopy(SOURCE_ONLY_CALLS), "new_outcomes_computed": 0}
    return report, prepared


def _rows(value):
    if type(value) is not dict or not inputs.literal_json(value) or type(value.get("results")) is not list:
        return None
    rows = value["results"]
    if any(type(row) is not dict or type(row.get("id")) is not str or not row["id"] for row in rows):
        return None
    return rows if len({row["id"] for row in rows}) == len(rows) else None


def verify_bundle(bundle, keys):
    inputs.require(inputs.literal_json(bundle) and type(keys) is dict
                   and inputs.same(keys, bundle["definition_keys"]), "Complete source bundle/key map required")
    inputs.validate_bundle(bundle)
    for path in bundle["sources"]:
        inputs.require(path and "\\" not in path and path == Path(path).as_posix(), "Canonical relative source path required")
    for row in bundle["inventory"]:
        inputs.require(all(type(row.get(name)) is str and bool(row[name]) for name in ("id", "path", "symbol", "text")),
                       "Nonempty literal source window required")


def verify_literal_row(row, bundle):
    inputs.require(type(row) is dict and inputs.literal_json(row)
                   and all(type(row.get(key)) is str and bool(row[key]) for key in ("id", "path", "symbol", "source"))
                   and all(type(row.get(key)) is int for key in ("start_line", "end_line", "window_end_line"))
                   and type(row.get("truncated")) is bool
                   and 1 <= row["start_line"] <= row["end_line"] <= row["window_end_line"],
                   "Well typed literal baseline source row required")
    inventory = {value["id"]: value for value in bundle["inventory"]}
    inputs.require(row["id"] in inventory, "Baseline window absent from complete inventory")
    original = inventory[row["id"]]
    expected = [original["path"], original["symbol"], original["start_line"], original["end_line"]]
    inputs.require([row["path"], row["symbol"], row["start_line"], row["window_end_line"]] == expected
                   and row.get("content_hash") == original["content_hash"], "Baseline inventory metadata changed")
    source = "".join(inputs.physical_lines(bundle["sources"][row["path"]])[row["start_line"]-1:row["end_line"]])
    inputs.require(row["source"] == source and row.get("source_hash") == inputs.sha(source.encode())
                   and len(inputs.physical_lines(source)) == row["end_line"]-row["start_line"]+1
                   and (row["truncated"] or row["end_line"] == row["window_end_line"]
                        and row["content_hash"] == row["source_hash"]), "Baseline literal source bytes differ")
    origin = row.get("source_origin")
    inputs.require("source_origin" not in row or origin in ("saved", "overlay"), "Unknown source provenance")
    if origin == "overlay":
        inputs.require(type(row.get("buffer_version")) is int and 0 <= row["buffer_version"] <= 2**53-1
                       and type(row.get("buffer_hash")) is str and re.fullmatch(r"[0-9a-f]{64}", row["buffer_hash"])
                       and row["buffer_hash"] == inputs.sha(bundle["sources"][row["path"]].encode()),
                       "Complete typed overlay provenance required")
    else:
        inputs.require("buffer_version" not in row and "buffer_hash" not in row, "Unexpected buffer provenance")


def reconstruct_fact(row, index, bundle, keys):
    verify_literal_row(row, bundle)
    key, source = keys[row["id"]], bundle["sources"][row["path"]]
    fact = {"index": index, "id": row["id"], "path": row["path"], "symbol": row["symbol"],
        "definition_key": deepcopy(key), "row_sha256": inputs.sha(inputs.canonical(row)),
        "source_sha256": inputs.sha(row["source"].encode()), "source_file_sha256": bundle["files"][row["path"]]["sha256"],
        "provenance": {name: deepcopy(row[name]) for name in inputs.PROVENANCE if name in row},
        "status": "unsupported_language", "witnesses": []}
    if Path(row["path"]).suffix != ".py":
        return fact
    if row["truncated"] or row["end_line"] != row["window_end_line"] or key[2:4] != [row["start_line"], row["end_line"]]:
        fact["status"] = "partial_definition"
        return fact
    fact["status"] = "uncertain_definition"
    if len(key) != 4:
        return fact
    try:
        definitions = canonical_definitions(source, row["path"])
        node = definitions.get(tuple(key))
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return fact
        lines = inputs.physical_lines(source)
        start = sum(len(line.encode()) for line in lines[:key[2]-1])
        end = sum(len(line.encode()) for line in lines[:key[3]])
        definition = {"path": row["path"], "start_line": key[2], "end_line": key[3],
            "start_byte": start, "end_byte": end, "source": source.encode()[start:end].decode(),
            "sha256": inputs.sha(source.encode()[start:end])}
        for returned, template, formatted, call in template_nodes(node):
            witnesses = {name: literal_witness(source, row["path"], value) for name, value in
                         (("return", returned), ("template", template), ("formatted_value", formatted), ("call", call))}
            fact["witnesses"].append({"definition": deepcopy(definition), **witnesses})
    except (SyntaxError, ValueError, RecursionError, OverflowError, UnicodeError):
        return fact
    fact["witnesses"].sort(key=lambda item: (item["return"]["start_byte"], item["formatted_value"]["start_byte"], item["call"]["start_byte"]))
    fact["status"] = "returned_template" if fact["witnesses"] else "no_returned_template"
    return fact


def reconstruct_guard(operand, bundle):
    reference, proposal, keys = operand["reference"], operand["proposal"], operand["definition_keys"]
    receipt = {"schema": GUARD_SCHEMA, "mode": MODE, "accepted": False, "fallback_reason": None,
        "source_bundle_sha256": None, "source_inventory_sha256": None, "definition_keys_sha256": None,
        "reference_facts": [], "protected_ids": [], "violated_ids": [], "calls": deepcopy(NO_WORK)}

    def finish(response, reason=None):
        receipt["accepted"], receipt["fallback_reason"] = reason is None, reason
        return deepcopy(response), receipt

    baseline, proposed = _rows(reference), _rows(proposal)
    if baseline is None:
        return finish(reference, "invalid_reference")
    if proposed is None:
        return finish(reference, "invalid_proposal")
    try:
        verify_bundle(bundle, keys)
        receipt["source_bundle_sha256"] = inputs.sha(inputs.canonical(bundle))
        receipt["source_inventory_sha256"] = inputs.sha(inputs.canonical(bundle["inventory"]))
        receipt["definition_keys_sha256"] = inputs.sha(inputs.canonical(keys))
    except (ValueError, TypeError, KeyError, UnicodeError, SyntaxError, RecursionError, OverflowError):
        return finish(reference, "invalid_source_inventory")
    try:
        for index, row in enumerate(baseline, 1):
            receipt["reference_facts"].append(reconstruct_fact(row, index, bundle, keys))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        return finish(reference, "invalid_reference_facts")
    receipt["protected_ids"] = [row["id"] for row in receipt["reference_facts"]
                                if row["status"] in ("returned_template", "uncertain_definition")]
    proposed_by_id = {row["id"]: row for row in proposed}
    receipt["violated_ids"] = [row["id"] for row in baseline if row["id"] in receipt["protected_ids"]
        and (row["id"] not in proposed_by_id or not exact_same(row, proposed_by_id[row["id"]]))]
    return finish(reference, "protected_template_row_changed") if receipt["violated_ids"] else finish(proposal)


def verify_record(record, operand, bundle, outcome, counter):
    inputs.require(inputs.literal_json(outcome), "Outcome must be finite literal JSON")
    expected, receipt = reconstruct_guard(operand, bundle)
    inputs.require(exact_operand(outcome.get("response"), expected) and exact_same(outcome.get("guard_receipt"), receipt),
                   "Independent exact operand decision or template witnesses differ")
    inputs.validate_response(outcome["response"], bundle)
    payload = inputs.compact_payload(expected)
    tokens = len(counter.encode(payload, disallowed_special=()))
    inputs.require(type(outcome.get("actual_compact_tokens")) is int and tokens == outcome["actual_compact_tokens"]
                   == expected["token_usage"]["tokens"] <= operand["limits"]["token_budget"], "Actual compact token count/cap differs")
    scores = {arm: prior_audit.score(record["scoring"], response) for arm, response in
              (("legacy", record["legacy"]), ("coverage", operand["reference"]), ("prior_role", operand["proposal"]), ("candidate", expected))}
    changes = {arm: prior_audit.comparison(value, scores["candidate"]) for arm, value in scores.items() if arm != "candidate"}
    values = {"id": record["id"], **{name: record[name] for name in ("dataset", "query_id", "mode", "repetition", "quality")},
        "multi_file": record["scoring"]["multi_file"], "operand_sha256": inputs.sha(inputs.canonical(operand)),
        "source_bundle_sha256": inputs.sha(inputs.canonical(bundle)), "response": expected, "guard_receipt": receipt,
        "compact_payload": payload, "compact_sha256": inputs.sha(payload.encode()), "actual_compact_tokens": tokens,
        "changed_vs_prior_role": not exact_operand(expected, operand["proposal"]), "scores": scores, "comparisons": changes}
    inputs.require(exact_same(values, outcome), "Outcome field/type/identity/compact/quality reconstruction differs")
    return values


def aggregate(outcomes):
    selected = [row for row in outcomes if row["quality"]]
    controls = ("legacy", "coverage", "prior_role")
    losses, gains, metrics, restorations = {arm: [] for arm in controls}, set(), {}, []
    for row in selected:
        for arm in controls:
            change = row["comparisons"][arm]
            if change["group_losses"] or change["complete_loss"] or change["single_file_first_hit_loss"]:
                losses[arm].append({name: row[name] for name in ("id", "dataset", "query_id", "mode")} | change)
        if row["comparisons"]["prior_role"]["complete_gain"]:
            restorations.append({name: row[name] for name in ("id", "dataset", "query_id", "mode", "multi_file")})
            if row["multi_file"]:
                gains.add((row["dataset"], row["query_id"]))
    for arm in (*controls, "candidate"):
        metrics[arm] = {"groups": sum(row["scores"][arm]["covered_groups"] for row in selected),
            "complete": sum(row["scores"][arm]["complete"] for row in selected),
            "multi_file_complete": sum(row["multi_file"] and row["scores"][arm]["complete"] for row in selected),
            "single_file_first_hit": sum(row["scores"][arm]["single_file_first_hit"] for row in selected)}
    return {"decisions": len(outcomes), "quality_occurrences": len(selected),
        "distinct_units": len({(row["dataset"], row["query_id"]) for row in selected}), "metrics": metrics,
        "quality_gate": {"qualified": bool(gains) and not any(losses.values()), "individual_losses": losses,
            "distinct_complete_multi_gains_vs_prior_role": [list(pair) for pair in sorted(gains)],
            "fresh_native_required": True, "default_change": False},
        "activity": {"accepted": sum(row["guard_receipt"]["accepted"] for row in outcomes),
            "changed_vs_prior_role": sum(row["changed_vs_prior_role"] for row in outcomes),
            "protected_rows": sum(len(row["guard_receipt"]["protected_ids"]) for row in outcomes),
            "fallback_reasons": dict(Counter(row["guard_receipt"]["fallback_reason"] or "accepted" for row in outcomes))},
        "restorations": restorations}


def verify_repetitions(records, outcomes):
    groups = {}
    for record in records.values():
        if record.get("original_identity", {}).get("origin") == "attrs_native_observed":
            groups.setdefault((record["dataset"], record["query_id"], record["mode"]), {})[record["repetition"]] = record
    for group in groups.values():
        inputs.require(set(group) == {1, 2, 3} and all(type(key) is int and record["quality"] is (key == 1)
                       for key, record in group.items()), "Original native repetition quality boundary differs")
        first = outcomes[group[1]["id"]]
        for repeat in (2, 3):
            actual = outcomes[group[repeat]["id"]]
            inputs.require(inputs.same(inputs.source_view(first["response"]), inputs.source_view(actual["response"]))
                           and all(inputs.same(first[name], actual[name]) for name in
                               ("guard_receipt", "compact_payload", "compact_sha256", "actual_compact_tokens")),
                           "Repeated native source/terminal decision/compact integrity changed")


def audit_outcomes(prepared, outcomes, counter):
    records, operands = (inputs.unique(prepared[name], "id") for name in ("records", "operands"))
    # Keep exact saved order, rather than silently sorting or accepting aliases.
    indexed = inputs.unique(outcomes, "id")
    inputs.require(list(records) == list(operands) == list(indexed), "Complete original decision membership/order required")
    verified = [verify_record(record, operands[identifier], prepared["source_bundles"][record["source_bundle_id"]],
                              indexed[identifier], counter) for identifier, record in records.items()]
    verify_repetitions(records, indexed)
    return aggregate(verified)


def verify_input_receipt(receipt, plan, plan_sha256):
    keys = {"schema", "status", "checks_passed", "classification", "plan_sha256", "source_freeze", "parent", "prepared",
            "counts", "runtime", "execution_commit", "reviewed_at_utc", "source_only_calls", "new_outcomes_computed"}
    inputs.require(type(receipt) is dict and set(receipt) == keys
                   and receipt["schema"] == "template_preservation_input_audit_v1" and receipt["status"] == "passed"
                   and receipt["checks_passed"] is True and receipt["classification"] == CLASSIFICATION
                   and receipt["plan_sha256"] == plan_sha256
                   and all(inputs.same(receipt[name], plan[name]) for name in ("source_freeze", "parent", "prepared", "counts", "runtime"))
                   and inputs.same(receipt["source_only_calls"], SOURCE_ONLY_CALLS)
                   and type(receipt["new_outcomes_computed"]) is int and receipt["new_outcomes_computed"] == 0
                   and type(receipt["execution_commit"]) is str and re.fullmatch(r"[0-9a-f]{40}", receipt["execution_commit"]),
                   "Exact independent source-only input audit barrier required")
    reviewed = datetime.fromisoformat(receipt["reviewed_at_utc"])
    inputs.require(reviewed.tzinfo is not None and datetime.fromisoformat(plan["frozen_at_utc"]) <= reviewed,
                   "Input review must follow prospective source/input plan")
    return reviewed


def input_closure(plan, results, *, root=ROOT):
    root, results = Path(root).resolve(), Path(results).resolve()
    bound = {}
    for values in (plan["source_freeze"]["sha256"], plan["parent"]["artifacts"], plan["prepared"]["artifacts"],
                   {(results / name).relative_to(root).as_posix(): inputs.file_sha(results / name) for name in ("plan.json", "input-audit.json")}):
        for name, value in values.items():
            inputs.require(name not in bound or bound[name] == value, "Conflicting prospective input/source binding")
            bound[name] = value
    return bound


def audit(results, *, root=ROOT):
    root, results = Path(root).resolve(), Path(results).resolve()
    plan = verify_plan(results, root=root)
    receipt = inputs.read(results / "input-audit.json")
    reviewed = verify_input_receipt(receipt, plan, inputs.file_sha(results / "plan.json"))
    prepared = inputs.load_prepared(root / PREPARED, root=root, verify=True)
    attempt = results / "replay"
    inputs.require({path.name for path in attempt.iterdir()} == {"run-start.json", "outcomes.jsonl.gz", "summary.json"},
                   "Exclusive complete successful replay closure required")
    start, summary = inputs.read(attempt / "run-start.json"), inputs.read(attempt / "summary.json")
    inputs.require(set(start) == {"schema", "classification", "started_at_utc", "execution_commit", "source_freeze", "plan_sha256",
                                 "input_audit_sha256", "input_freeze", "resume_allowed"}
                   and start["schema"] == SCHEMA and start["classification"] == CLASSIFICATION
                   and inputs.same(start["source_freeze"], plan["source_freeze"])
                   and start["plan_sha256"] == inputs.file_sha(results / "plan.json")
                   and start["input_audit_sha256"] == inputs.file_sha(results / "input-audit.json")
                   and start["resume_allowed"] is False, "Actual producer start/source/input authority differs")
    frozen, execution = start["input_freeze"], start["execution_commit"]
    bound = input_closure(plan, results, root=root)
    inputs.require(type(frozen) is dict and set(frozen) == {"commit", "committed_at_unix", "bound_blob_count"}
                   and type(frozen["committed_at_unix"]) is int and type(frozen["bound_blob_count"]) is int
                   and frozen["bound_blob_count"] == len(bound), "Complete committed input audit freeze required")
    head = inputs.git(root, "rev-parse", "HEAD").decode().strip()
    chain = (plan["source_freeze"]["commit"], receipt["execution_commit"], frozen["commit"], execution, head)
    for earlier, later in zip(chain, chain[1:]):
        ancestor(root, earlier, later)
    inputs.require(int(inputs.git(root, "show", "-s", "--format=%ct", frozen["commit"])) == frozen["committed_at_unix"],
                   "Actual input commit timestamp differs")
    for commit in (frozen["commit"], execution, head):
        inputs.verify_blobs(root, bound, commit=commit)
    before_input_commit = {name: value for name, value in bound.items() if name not in
                          {(results / item).relative_to(root).as_posix() for item in ("plan.json", "input-audit.json")}}
    inputs.verify_blobs(root, before_input_commit, commit=receipt["execution_commit"])
    began = datetime.fromisoformat(start["started_at_utc"])
    inputs.require(began.tzinfo is not None and math.floor(reviewed.timestamp()) <= frozen["committed_at_unix"] <= began.timestamp()
                   <= datetime.now(timezone.utc).timestamp()
                   and int(inputs.git(root, "show", "-s", "--format=%ct", execution)) <= began.timestamp(),
                   "Actual run must follow independent committed input audit")
    hashes = {name: inputs.file_sha(attempt / name) for name in ("run-start.json", "outcomes.jsonl.gz")}
    inputs.require(inputs.same(summary.get("artifacts"), hashes), "Finalized raw replay artifacts changed")
    outcomes = [value for _, value, _ in inputs.rows(attempt / "outcomes.jsonl.gz")]
    recomputed = audit_outcomes(prepared, outcomes, prior_audit.offline_counter(root))
    calls = {name: 0 for name in ("v1_proposals", "model", "index", "retrieval", "coalesce", "backfill", "network",
                                  "corpus_modules_imported_or_executed")}
    expected = {"schema", "classification", "status", *recomputed, "terminal_decisions", "new_work",
                "cpu_total_s_including_guard_and_io", "artifacts"}
    inputs.require(set(summary) == expected and summary.get("schema") == SCHEMA and summary.get("classification") == CLASSIFICATION
                   and summary.get("status") == "passed" and type(summary.get("terminal_decisions")) is int
                   and summary["terminal_decisions"] == len(outcomes) == 477
                   and inputs.same(summary["new_work"], calls)
                   and type(summary["cpu_total_s_including_guard_and_io"]) is float
                   and math.isfinite(summary["cpu_total_s_including_guard_and_io"]) and summary["cpu_total_s_including_guard_and_io"] >= 0
                   and all(inputs.same(summary.get(name), value) for name, value in recomputed.items())
                   and recomputed["quality_occurrences"] == 421 and recomputed["distinct_units"] == 215,
                   "Independent full decisions/quality/activity/count/call summary differs")
    inputs.require(inputs.same(verify_plan(results, root=root), plan), "Frozen source/input/parent changed during audit")
    return {"schema": "template_preservation_independent_audit_v1", "status": "passed", "checks_passed": True,
        "classification": CLASSIFICATION, "verified_terminal_decisions": 477, "quality_occurrences": 421,
        "distinct_quality_query_units": 215, "verified_source_datasets": 13, "verified_source_files": 417,
        "retained_original_negative_references": 3734, "plan_sha256": inputs.file_sha(results / "plan.json"),
        "input_audit_sha256": inputs.file_sha(results / "input-audit.json"), "source_freeze": deepcopy(plan["source_freeze"]),
        "input_freeze": frozen, "producer_execution_commit": execution, "auditor_execution_commit": head,
        "artifact_sha256": {**hashes, "summary.json": inputs.file_sha(attempt / "summary.json")},
        "independently_recomputed": recomputed, "calls": calls,
        "native_warm_latency_measured": False, "automatic_default_change": False,
        "novel_implementation_admission": {"count": 0, "reason": "Every response is exactly one original saved operand"},
        "independent_checks": ["literal canonical Python definitions and scoped returned-template AST",
            "literal UTF-8 byte/physical-line definition, return, template, formatted-field and call witnesses",
            "complete source-inventory and exact typed whole-response operand decision",
            "actual stored compact payload and token/source/result caps", "all three individual quality controls and gate"],
        "shared_helpers": {"preparation": "Frozen study_inputs source snapshot and immutable Git/three-control authority",
            "quality": "Frozen independent dependency audit evidence score/comparison", "candidate_guard_imported_or_called": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--check-inputs", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    failure_path = args.output.with_name(args.output.stem + "-failure.json")
    inputs.require(args.output.parent.is_dir() and not args.output.exists() and not failure_path.exists(),
                   "Audit requires existing parent and exclusive new success/failure files")
    inputs.require(not args.output.resolve().is_relative_to((args.results / "replay").resolve())
                   and not args.output.resolve().is_relative_to((ROOT / PREPARED).resolve()),
                   "Audit report must remain outside immutable replay/prepared directories")
    try:
        with cpu_only():
            report = audit_inputs(args.results)[0] if args.check_inputs else audit(args.results)
        inputs.write_json(args.output, report)
    except BaseException as error:
        try:
            inputs.write_json(failure_path, {"schema": "template_preservation_audit_failure_v1", "status": "failed",
                "classification": CLASSIFICATION, "error_type": type(error).__name__, "error": str(error),
                "resume_allowed": False, "audit_kind": "input" if args.check_inputs else "cpu_replay",
                "calls": {name: 0 for name in ("model", "index", "retrieval", "network")}})
        except BaseException as recorder_error:
            raise error from recorder_error
        raise
    print(json.dumps({"status": report["status"], "checks_passed": report["checks_passed"]}))


if __name__ == "__main__":
    main()

