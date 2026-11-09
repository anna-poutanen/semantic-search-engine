"""Independent terminal decisions and quality audit for observed dependency inputs.

The source resolver is shared for graph consistency, not independently proven
complete. Accepted endpoint/call/annotation witnesses are separately checked
against Python AST and literal bytes. Guard, skip, scoring and study producers
are never imported or called here. No corpus module or model is executed.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.abc
import importlib.metadata
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch

from experiments.dependency_context import study_inputs as inputs


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "dependency_context_study_v1"
GUARD_SCHEMA = "dependency_context_guard_v1"
GRAPH_SCHEMA = "annotated_parameter_dependencies_v1"
MODE = "role_annotated_pairs_v1"
NO_WORK = {name: 0 for name in ("model", "retrieval", "index", "coalesce", "backfill", "v1")}
PINS = {"tiktoken": "0.14.0", "tree-sitter": "0.26.0", "tree-sitter-javascript": "0.25.0",
        "tree-sitter-typescript": "0.23.2"}
CACHE_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
CACHE_KEY = hashlib.sha1(CACHE_URL.encode()).hexdigest()
CACHE_SHA = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


def source_authority(root=ROOT):
    root = Path(root).resolve()
    for name, module in tuple(sys.modules.items()):
        if name == "semantic_search" or name.startswith("semantic_search."):
            expected = root / "src/semantic_search"
        elif name == "experiments" or name.startswith("experiments."):
            expected = root / "experiments"
        elif name.startswith("scripts.") or name in {"audit_role_context", "audit_semantic_context", "audit_semantic_study"}:
            expected = root / "scripts"
        else:
            continue
        location = getattr(module, "__file__", None)
        inputs.require(location is not None and Path(location).resolve().is_relative_to(expected),
                       "Auditor executing module is outside frozen checkout: " + name)


@contextmanager
def cpu_only():
    blocked = {"mlx", "mlx_embeddings", "torch", "transformers", "safetensors", "sentence_transformers", "numpy"}

    class RefuseModels(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in blocked:
                raise RuntimeError("Model/numeric inference import forbidden: " + fullname)

    inputs.require(not any(name.split(".")[0] in blocked for name in sys.modules), "Independent CLI requires a clean CPU process")
    finder = RefuseModels()
    sys.meta_path.insert(0, finder)

    def denied(*args, **kwargs):
        raise RuntimeError("Network forbidden in independent dependency audit")

    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
             patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
             patch.object(socket.socket, "sendto", denied):
            yield
    finally:
        sys.meta_path.remove(finder)


def runtime(root=ROOT):
    cache = Path(os.environ.get("TIKTOKEN_CACHE_DIR", Path(root) / ".cache/tokenizers")) / CACHE_KEY
    inputs.require(inputs.file_sha(cache) == CACHE_SHA, "Exact offline cl100k cache required")
    packages = {name: importlib.metadata.version(name) for name in PINS}
    inputs.require(inputs.same(packages, PINS), "Pinned parser/tokenizer versions required")
    return {"python": sys.version, "packages": packages,
            "tokenizer": {"encoding": "cl100k_base", "sha256": CACHE_SHA}}


def offline_counter(root=ROOT):
    import tiktoken
    import tiktoken.load

    raw = (Path(os.environ.get("TIKTOKEN_CACHE_DIR", Path(root) / ".cache/tokenizers")) / CACHE_KEY).read_bytes()
    inputs.require(inputs.sha(raw) == CACHE_SHA, "Exact offline tokenizer bytes required")

    def captured(url, expected_hash=None):
        inputs.require(url == CACHE_URL and expected_hash == CACHE_SHA, "Unexpected tokenizer resource")
        return raw

    with patch.object(tiktoken.load, "read_file_cached", captured):
        return tiktoken.get_encoding("cl100k_base")


def verify_freeze(folder, commit, *, root=ROOT):
    root, folder = Path(root).resolve(), Path(folder).resolve()
    inputs.require(folder.is_relative_to(root), "Prepared input path escapes repository")
    path = folder.parent / "study-freeze.json"
    frozen = inputs.read(path)
    inputs.require(frozen.get("schema") == SCHEMA and type(frozen.get("new_guard_outcomes")) is int
                   and frozen["new_guard_outcomes"] == 0 and type(frozen.get("native_calls")) is int
                   and frozen["native_calls"] == 0 and frozen.get("default_change") is False,
                   "Prospective no-outcome execution freeze required")
    inputs.require(inputs.same(frozen["sources"], inputs.source_hashes(root))
                   and inputs.same(frozen["runtime"], runtime(root)), "Frozen source/runtime changed")
    artifacts = {str(item.relative_to(root)): inputs.file_sha(item) for item in sorted(folder.iterdir()) if item.is_file()}
    inputs.require(inputs.same(frozen["artifacts"], artifacts)
                   and frozen["prepared_plan_sha256"] == inputs.file_sha(folder / "frozen-plan.json"),
                   "Frozen input artifact membership/bytes changed")
    hashes = {**frozen["sources"], **artifacts, str(path.relative_to(root)): inputs.file_sha(path)}
    inputs.verify_blobs(root, hashes, commit=commit)
    head = inputs.git(root, "rev-parse", "HEAD").decode().strip()
    inputs.verify_blobs(root, hashes, commit=head)
    inputs.require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, head],
                                  capture_output=True).returncode == 0, "Freeze must remain an ancestor")
    inputs.require(not inputs.git(root, "status", "--porcelain", "--untracked-files=no").strip(), "Tracked source must be clean")
    when = datetime.fromisoformat(inputs.git(root, "show", "-s", "--format=%cI", commit).decode().strip())
    inputs.require(when <= datetime.now(timezone.utc), "Freeze cannot follow audit")
    return {"commit": commit, "execution_commit": head, "freeze_sha256": inputs.file_sha(path), "bound_blobs": len(hashes)}


def ast_definitions(source, path):
    tree = ast.parse(source, filename=path)
    result = {}

    def walk(node, scope=()):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbol = ".".join((*scope, child.name))
                low = min([child.lineno, *(value.lineno for value in child.decorator_list)])
                result[path, symbol, low, child.end_lineno] = child
                walk(child, (*scope, child.name))
            else:
                walk(child, scope)

    walk(tree)
    return tree, result


def ast_witness(source, path, node):
    lines = inputs.physical_lines(source)
    low = sum(len(value.encode()) for value in lines[:node.lineno-1]) + node.col_offset
    high = sum(len(value.encode()) for value in lines[:node.end_lineno-1]) + node.end_col_offset
    raw = source.encode()[low:high]
    return {"path": path, "start_line": node.lineno, "end_line": node.end_lineno,
            "start_byte": low, "end_byte": high, "source": raw.decode(), "sha256": inputs.sha(raw)}


def verify_witness(witness, bundle):
    inputs.require(type(witness) is dict and set(witness) == {"path", "start_line", "end_line", "start_byte", "end_byte", "source", "sha256"},
                   "Exact literal witness schema required")
    path = witness["path"]
    inputs.require(path in bundle["sources"] and all(type(witness[key]) is int for key in
                   ("start_line", "end_line", "start_byte", "end_byte")), "Witness source/typed interval differs")
    text = bundle["sources"][path]
    raw = text.encode()
    start, end = witness["start_byte"], witness["end_byte"]
    inputs.require(0 <= start < end <= len(raw) and 1 <= witness["start_line"] <= witness["end_line"],
                   "Witness byte/line bounds differ")
    offsets, offset = [], 0
    for line in inputs.physical_lines(text):
        offsets.append((offset, offset + len(line.encode())))
        offset += len(line.encode())
    spans = [index for index, (low, high) in enumerate(offsets, 1) if max(low, start) < min(high, end)]
    inputs.require(spans and [spans[0], spans[-1]] == [witness["start_line"], witness["end_line"]]
                   and raw[start:end].decode() == witness["source"] and inputs.sha(raw[start:end]) == witness["sha256"],
                   "Witness exact original source bytes differ")


def verify_endpoint(endpoint, bundle, definitions):
    key = endpoint.get("definition_key")
    inputs.require(type(key) is list and len(key) == 4 and tuple(key) in definitions
                   and key[:2] == [endpoint.get("path"), endpoint.get("symbol")]
                   and isinstance(definitions[tuple(key)], (ast.FunctionDef, ast.AsyncFunctionDef)),
                   "Accepted endpoint is not an original canonical Python function")
    source = "".join(inputs.physical_lines(bundle["sources"][key[0]])[key[2]-1:key[3]])
    expected = {"path": key[0], "symbol": key[1], "definition_key": key, "source": source,
                "source_sha256": inputs.sha(source.encode()), "definition_sha256": inputs.sha(source.encode()),
                "full_definition": True, "window_ids": sorted(identifier for identifier, value in bundle["definition_keys"].items() if value == key)}
    inputs.require(inputs.same(endpoint, expected) and expected["window_ids"], "Accepted endpoint source/window proof differs")
    return definitions[tuple(key)]


def verify_graph_receipt(bundle, receipt):
    """Shared resolver consistency plus independent AST and exact-byte proof."""
    from experiments.dependency_context.annotated_dependencies import AnnotatedDependencies
    from semantic_search.corpus import Snippet

    actual = AnnotatedDependencies(bundle["sources"], [Snippet(**row) for row in bundle["inventory"]],
                                   definition_keys=bundle["definition_keys"]).witnesses()
    inputs.require(inputs.same(actual, receipt), "Graph differs from frozen resolver/source inventory")
    definitions, trees = {}, {}
    for path, source in bundle["sources"].items():
        if not path.endswith(".py"):
            continue
        try:
            tree, values = ast_definitions(source, path)
        except (SyntaxError, RecursionError, OverflowError, ValueError):
            continue
        trees[path] = tree
        definitions.update(values)
    for fact in receipt["accepted"]:
        caller = verify_endpoint(fact["caller"], bundle, definitions)
        verify_endpoint(fact["callee"], bundle, definitions)
        for witness in (fact["annotation"], fact["call"], *fact["resolution"]):
            verify_witness(witness, bundle)
        path = fact["caller"]["path"]
        source = bundle["sources"][path]
        parameters = [*caller.args.posonlyargs, *caller.args.args, *caller.args.kwonlyargs]
        matching = [arg for arg in parameters if arg.arg == fact["parameter"] and arg.annotation is not None
                    and inputs.same(ast_witness(source, path, arg.annotation), fact["annotation"])]
        calls = [node for node in ast.walk(caller) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == fact["parameter"] and node.func.attr == fact["callee"]["symbol"].split(".")[-1]
                 and inputs.same(ast_witness(source, path, node), fact["call"])]
        inputs.require(len(matching) == len(calls) == 1, "Accepted annotation/call is not an actual original AST witness")
        class_key = fact["class"]
        inputs.require(type(class_key) is dict and set(class_key) == {"path", "symbol"}
                       and fact["callee"]["path"] == class_key["path"]
                       and fact["callee"]["symbol"] == class_key["symbol"] + "." + calls[0].func.attr,
                       "Accepted callee/class identity differs")
        for witness in fact["resolution"]:
            nodes = ast.walk(trees[witness["path"]])
            inputs.require(any(isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef))
                               and inputs.same(ast_witness(bundle["sources"][witness["path"]], witness["path"], node), witness)
                               for node in nodes), "Resolution witness is not an actual import/class AST node")
    return {"accepted_edges": len(receipt["accepted"]), "source_files": len(bundle["files"]),
            "shared_resolver_consistency": True, "independent_accepted_ast_and_literal_witnesses": True}


def delivers(row, witness):
    low, high = witness["start_line"], witness["end_line"]
    return row["start_line"] <= low <= high <= row["end_line"] and witness["source"] in "".join(
        inputs.physical_lines(row["source"])[low-row["start_line"]:high-row["start_line"]+1])


def bound_endpoint(row, endpoint, keys):
    key = keys.get(row["id"])
    return (type(key) is list and inputs.same(key, endpoint["definition_key"])
            and [row["path"], row["symbol"], row["start_line"], row["end_line"]] == key[:4]
            and not row["truncated"] and row["end_line"] == row["window_end_line"]
            and row["source_hash"] == endpoint["definition_sha256"])


def reconstruct_guard(operand, graph):
    baseline, proposal = operand["reference"], operand["proposal"]
    pairs, protected = [], set()
    for index, fact in enumerate(graph["accepted"]):
        callers = [row for row in baseline["results"] if bound_endpoint(row, fact["caller"], operand["definition_keys"])
                   and delivers(row, fact["call"]) and delivers(row, fact["annotation"])]
        callees = [row for row in baseline["results"] if bound_endpoint(row, fact["callee"], operand["definition_keys"])]
        if len(callers) == len(callees) == 1 and callers[0]["id"] != callees[0]["id"]:
            pairs.append({"witness_index": index, "caller_id": callers[0]["id"], "callee_id": callees[0]["id"]})
            protected.update((callers[0]["id"], callees[0]["id"]))
    by_id = {row["id"]: row for row in proposal["results"]}
    violated = [row["id"] for row in baseline["results"] if row["id"] in protected
                and (row["id"] not in by_id or not inputs.same(row, by_id[row["id"]]))]
    receipt = {"schema": GUARD_SCHEMA, "mode": MODE, "accepted": not violated,
               "fallback_reason": "delivered_dependency_pair_changed" if violated else None,
               "dependency_receipt_sha256": inputs.sha(inputs.canonical(graph)), "pairs": pairs,
               "protected_ids": [row["id"] for row in baseline["results"] if row["id"] in protected],
               "violated_ids": violated, "calls": dict(NO_WORK)}
    return deepcopy(baseline if violated else proposal), receipt


def independent_role_classifier():
    # Reuse the already-frozen independent classifier, never its producer guard.
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    source_authority()
    module = importlib.import_module("audit_role_context")
    source_authority()
    return module.classify_row


def reconstruct_certificate(operand, graph, *, role_classifier=None):
    baseline, raw = operand["reference"]["results"], operand["raw_rows"]
    maximum, keys = operand["limits"]["max_results"], operand["definition_keys"]
    receipt = {"schema": "dependency_skip_certificate_v1", "eligible": False, "reason": None,
               "max_results": maximum, "semantic_protected_ids": [], "role_protected_ids": [],
               "pair_protected_ids": [], "unprotected_ids": [], "calls": dict(NO_WORK)}
    if len(baseline) != maximum:
        receipt["reason"] = "context_not_full"
        return receipt
    distinct, seen = [], set()
    for row in raw:
        identity = ("definition", tuple(keys[row["id"]])) if row["id"] in keys else ("window", row["id"])
        if identity not in seen:
            seen.add(identity)
            distinct.append(row)
    windows = {row["id"]: row for row in [*raw, *operand["precoalesce_rows"], *baseline]}
    dependencies = [row for row in [*operand["precoalesce_rows"], *baseline] if row.get("origin") == "dependency"]
    owners = [windows[row["related_to"]] for row in dependencies if row.get("related_to") in windows]
    guards = [*distinct[:min(5, maximum)], *dependencies, *owners]
    semantic = [row["id"] for row in baseline if row["truncated"] or row["end_line"] != row["window_end_line"]
                or row.get("origin") == "dependency" or any(row["path"] == window["path"]
                    and max(row["start_line"], window["start_line"]) <= min(row["end_line"], window["end_line"])
                    for window in guards)]
    classifier = independent_role_classifier() if role_classifier is None else role_classifier
    role = [row["id"] for index, row in enumerate(baseline, 1)
            if classifier(row, index=index, definition_keys=keys)["reasons"]]
    _, pair = reconstruct_guard({**operand, "proposal": operand["reference"]}, graph)
    protected = set(semantic) | set(role) | set(pair["protected_ids"])
    unprotected = [row["id"] for row in baseline if row["id"] not in protected]
    receipt.update(semantic_protected_ids=semantic, role_protected_ids=role, pair_protected_ids=pair["protected_ids"],
                   unprotected_ids=unprotected, eligible=not unprotected,
                   reason="unprotected_victim_exists" if unprotected else "all_full_context_rows_protected")
    return receipt


def score(scoring, response):
    ranks = []
    for group in scoring["groups"]:
        hits = []
        for index, row in enumerate(response["results"], 1):
            for target in group:
                low, high = target["evidence_lines"]
                if row["path"] != target["path"] or not row["start_line"] <= low <= high <= row["end_line"]:
                    continue
                if scoring["qualified"]:
                    text = "".join(inputs.physical_lines(row["source"])[low-row["start_line"]:high-row["start_line"]+1])
                    if row["symbol"] != target["symbol"] or inputs.sha(text.encode()) != target["evidence_sha256"]:
                        continue
                hits.append(index)
        ranks.append(min(hits) if hits else None)
    positive = scoring["kind"] == "present"
    return {"evidence_ranks": ranks, "covered_groups": sum(rank is not None for rank in ranks),
            "complete": positive and bool(ranks) and all(rank is not None for rank in ranks),
            "single_file_first_hit": positive and not scoring["multi_file"] and bool(ranks) and ranks[0] is not None}


def comparison(before, after):
    inputs.require(len(before["evidence_ranks"]) == len(after["evidence_ranks"]), "Evidence group arity changed")
    pairs = list(zip(before["evidence_ranks"], after["evidence_ranks"]))
    return {"group_gains": [index for index, (a, b) in enumerate(pairs, 1) if a is None and b is not None],
            "group_losses": [index for index, (a, b) in enumerate(pairs, 1) if a is not None and b is None],
            "complete_gain": after["complete"] and not before["complete"],
            "complete_loss": before["complete"] and not after["complete"],
            "single_file_first_hit_loss": before["single_file_first_hit"] and not after["single_file_first_hit"]}


def certificate_equal(response, reference):
    """Compare full contexts while excluding only the top-level timing values.

    This is a disclosed post-failure harness correction. The certificate covers
    context equivalence, not baseline/final timing snapshots of one request. Presence
    and dictionary types remain strict; no other metadata, nested timing field,
    row dictionary or compact serialization is excluded or rewritten.
    """
    if (type(response) is not dict or type(reference) is not dict
            or not inputs.literal_json(response) or not inputs.literal_json(reference)
            or ("timings" in response) != ("timings" in reference)):
        return False
    if "timings" in response and (type(response["timings"]) is not dict or type(reference["timings"]) is not dict):
        return False
    return (inputs.same({name: value for name, value in response.items() if name != "timings"},
                        {name: value for name, value in reference.items() if name != "timings"})
            and inputs.compact_payload(response) == inputs.compact_payload(reference))


def verify_record(record, operand, bundle, graph, outcome, counter, *, role_classifier=None):
    inputs.require(inputs.literal_json(outcome), "Outcome must be finite literal JSON")
    expected, receipt = reconstruct_guard(operand, graph)
    certificate = reconstruct_certificate(operand, graph, role_classifier=role_classifier)
    inputs.require(inputs.same(outcome["response"], expected) and inputs.same(outcome["guard_receipt"], receipt)
                   and inputs.same(outcome["skip_certificate"], certificate), "Independent exact decision/receipt/certificate differs")
    inputs.require(not certificate["eligible"] or certificate_equal(expected, operand["reference"]),
                   "Eligible structural proof differs from exact coverage context outside top-level timings")
    inputs.validate_response(outcome["response"], bundle)
    payload = inputs.compact_payload(expected)
    inputs.require(payload == inputs.compact_payload(outcome["response"]) == outcome["compact_payload"]
                   and inputs.sha(payload.encode()) == outcome["compact_sha256"], "Actual stored response compact payload differs")
    tokens = len(counter.encode(payload, disallowed_special=()))
    inputs.require(type(outcome.get("actual_compact_tokens")) is int and tokens == outcome["actual_compact_tokens"]
                   == expected["token_usage"]["tokens"] <= operand["limits"]["token_budget"], "Actual compact token count/cap differs")
    scores = {arm: score(record["scoring"], response) for arm, response in
              (("legacy", record["legacy"]), ("coverage", operand["reference"]),
               ("prior_role", operand["proposal"]), ("candidate", expected))}
    changes = {arm: comparison(value, scores["candidate"]) for arm, value in scores.items() if arm != "candidate"}
    values = {"id": record["id"], **{name: record[name] for name in ("dataset", "query_id", "mode", "repetition", "quality")},
              "multi_file": record["scoring"]["multi_file"], "operand_sha256": inputs.sha(inputs.canonical(operand)),
              "graph_receipt_sha256": inputs.sha(inputs.canonical(graph)), "response": expected, "guard_receipt": receipt,
              "skip_certificate": certificate, "compact_payload": payload, "compact_sha256": inputs.sha(payload.encode()),
              "actual_compact_tokens": tokens, "changed_vs_prior_role": not inputs.same(expected, operand["proposal"]),
              "scores": scores, "comparisons": changes}
    inputs.require(inputs.same(values, outcome), "Unexpected/missing outcome field, identity, score, comparison or source differs")
    return values


def aggregate(outcomes):
    selected = [row for row in outcomes if row["quality"]]
    arms = ("legacy", "coverage", "prior_role")
    losses, gains, metrics = {arm: [] for arm in arms}, set(), {}
    for row in selected:
        for arm in arms:
            change = row["comparisons"][arm]
            if change["group_losses"] or change["complete_loss"] or change["single_file_first_hit_loss"]:
                losses[arm].append({"id": row["id"], "dataset": row["dataset"], "query_id": row["query_id"], "mode": row["mode"], **change})
        if row["multi_file"] and row["comparisons"]["prior_role"]["complete_gain"]:
            gains.add((row["dataset"], row["query_id"]))
    for arm in (*arms, "candidate"):
        values = [row["scores"][arm] for row in selected]
        metrics[arm] = {"groups": sum(value["covered_groups"] for value in values),
                        "complete": sum(value["complete"] for value in values),
                        "multi_file_complete": sum(row["multi_file"] and row["scores"][arm]["complete"] for row in selected),
                        "single_file_first_hit": sum(value["single_file_first_hit"] for value in values)}
    return {"decisions": len(outcomes), "quality_occurrences": len(selected),
            "distinct_units": len({(row["dataset"], row["query_id"]) for row in selected}), "metrics": metrics,
            "quality_gate": {"qualified": bool(gains) and not any(losses.values()), "individual_losses": losses,
                             "distinct_complete_multi_gains_vs_prior_role": [list(pair) for pair in sorted(gains)],
                             "fresh_native_required": True, "default_change": False},
            "activity": {"accepted": sum(row["guard_receipt"]["accepted"] for row in outcomes),
                         "changed_vs_prior_role": sum(row["changed_vs_prior_role"] for row in outcomes),
                         "protected_pairs": sum(len(row["guard_receipt"]["pairs"]) for row in outcomes),
                         "skip_certificates": sum(row["skip_certificate"]["eligible"] for row in outcomes),
                         "fallback_reasons": dict(Counter(row["guard_receipt"]["fallback_reason"] or "accepted" for row in outcomes))}}


def verify_repetitions(records, outcomes):
    grouped = {}
    for record in records.values():
        if record.get("original_identity", {}).get("origin") == "attrs_native_observed":
            grouped.setdefault((record["dataset"], record["query_id"], record["mode"]), {})[record["repetition"]] = record
    for group in grouped.values():
        inputs.require(set(group) == {1, 2, 3} and all(record["quality"] is (repeat == 1) for repeat, record in group.items()),
                       "Attrs repetitions must have one first-repetition quality occurrence")
        first, first_outcome = group[1], outcomes[group[1]["id"]]
        for repeat in (2, 3):
            record, outcome = group[repeat], outcomes[group[repeat]["id"]]
            inputs.require(all(inputs.same(inputs.source_view(record[name]), inputs.source_view(first[name]))
                               for name in ("reference", "proposal", "legacy"))
                           and all(inputs.same(record[name], first[name]) for name in
                                   ("raw_rows", "precoalesce_rows", "definition_keys", "limits", "scoring"))
                           and inputs.same(inputs.source_view(outcome["response"]), inputs.source_view(first_outcome["response"]))
                           and all(inputs.same(outcome[name], first_outcome[name]) for name in
                                   ("guard_receipt", "skip_certificate", "compact_payload", "compact_sha256", "actual_compact_tokens")),
                           "Attrs repeated source/decision/compact integrity changed")


def timing_envelope_exclusions(records, outcomes):
    excluded = []
    for outcome in outcomes:
        reference = records[outcome["id"]]["reference"]
        if outcome["skip_certificate"]["eligible"] and not inputs.same(outcome["response"], reference):
            inputs.require(certificate_equal(outcome["response"], reference), "Unapproved certificate envelope difference")
            excluded.append({"id": outcome["id"], "dataset": outcome["dataset"], "query_id": outcome["query_id"],
                             "mode": outcome["mode"], "repetition": outcome["repetition"],
                             "reference_response_sha256": inputs.sha(inputs.canonical(reference)),
                             "actual_response_sha256": inputs.sha(inputs.canonical(outcome["response"])),
                             "reference_timings": deepcopy(reference["timings"]),
                             "actual_timings": deepcopy(outcome["response"]["timings"]),
                             "identical_compact_sha256": outcome["compact_sha256"]})
    return excluded


def audit_outcomes(prepared, outcomes, graphs, counter, *, role_classifier=None):
    records, operands = inputs.unique(prepared["records"], "id"), inputs.unique(prepared["operands"], "id")
    indexed = inputs.unique(outcomes, "id")
    inputs.require(records.keys() == operands.keys() == indexed.keys() and graphs.keys() == prepared["source_bundles"].keys(),
                   "Complete original decision and graph membership required")
    verified = [verify_record(record, operands[identifier], prepared["source_bundles"][record["source_bundle_id"]],
                             graphs[record["source_bundle_id"]], indexed[identifier], counter, role_classifier=role_classifier)
                for identifier, record in records.items()]
    verify_repetitions(records, indexed)
    return aggregate(verified)


def audit(prepared_path, outcome_path, commit, *, root=ROOT):
    root, prepared_path, outcome_path = Path(root).resolve(), Path(prepared_path).resolve(), Path(outcome_path).resolve()
    source_authority(root)
    binding = verify_freeze(prepared_path, commit, root=root)
    prepared = inputs.load_prepared(prepared_path, root=root)
    expected_files = {"run-start.json", "graphs.jsonl.gz", "outcomes.jsonl.gz", "summary.json"}
    inputs.require({path.name for path in outcome_path.iterdir()} == expected_files, "Complete successful outcome closure required")
    start, summary = inputs.read(outcome_path / "run-start.json"), inputs.read(outcome_path / "summary.json")
    run_binding = start.get("binding")
    inputs.require(type(run_binding) is dict and set(run_binding) == set(binding)
                   and all(inputs.same(run_binding.get(name), value) for name, value in binding.items() if name != "execution_commit")
                   and summary.get("schema") == SCHEMA and summary.get("status") == "passed"
                   and start.get("schema") == SCHEMA, "Run source/freeze/status binding differs")
    execution = run_binding["execution_commit"]
    inputs.require(type(execution) is str and len(execution) == 40
                   and subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, execution], capture_output=True).returncode == 0
                   and subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", execution, binding["execution_commit"]], capture_output=True).returncode == 0,
                   "Actual producer execution must follow freeze and remain an ancestor")
    freeze = inputs.read(prepared_path.parent / "study-freeze.json")
    inputs.verify_blobs(root, {**freeze["sources"], **freeze["artifacts"],
                        str((prepared_path.parent / "study-freeze.json").relative_to(root)): binding["freeze_sha256"]}, commit=execution)
    when = datetime.fromisoformat(inputs.git(root, "show", "-s", "--format=%cI", commit).decode().strip())
    began = datetime.fromisoformat(start["started_at_utc"])
    execution_time = datetime.fromisoformat(inputs.git(root, "show", "-s", "--format=%cI", execution).decode().strip())
    inputs.require(when <= execution_time <= began <= datetime.now(timezone.utc), "Run started before prospective freeze/execution")
    hashes = {name: inputs.file_sha(outcome_path / name) for name in expected_files if name != "summary.json"}
    inputs.require(inputs.same(summary["artifacts"], hashes), "Finalized raw outcome/graph/start artifacts changed")
    graph_rows = [value for _, value, _ in inputs.rows(outcome_path / "graphs.jsonl.gz")]
    graph_records = inputs.unique(graph_rows, "source_bundle_id")
    inputs.require(graph_records.keys() == prepared["source_bundles"].keys()
                   and all(set(value) == {"source_bundle_id", "receipt"} for value in graph_rows), "Complete source graph closure required")
    graphs = {identifier: value["receipt"] for identifier, value in graph_records.items()}
    graph_checks = {identifier: verify_graph_receipt(prepared["source_bundles"][identifier], receipt)
                    for identifier, receipt in graphs.items()}
    outcomes = [value for _, value, _ in inputs.rows(outcome_path / "outcomes.jsonl.gz")]
    recomputed = audit_outcomes(prepared, outcomes, graphs, offline_counter(root))
    exclusions = timing_envelope_exclusions(inputs.unique(prepared["records"], "id"), outcomes)
    inputs.require(all(inputs.same(summary.get(name), value) for name, value in recomputed.items())
                   and summary.get("terminal_decisions") == len(outcomes) == 477
                   and summary.get("certificate_checks") == 477
                   and inputs.same(summary.get("new_work"), {name: 0 for name in
                        ("v1_proposals", "model", "index", "retrieval", "coalesce", "backfill", "network")})
                   and type(summary.get("cpu_total_s_including_graph_and_io")) is float
                   and math.isfinite(summary["cpu_total_s_including_graph_and_io"])
                   and summary["cpu_total_s_including_graph_and_io"] >= 0,
                   "Independent full quality/activity/count/call summary differs")
    inputs.require(recomputed["quality_occurrences"] == 421 and recomputed["distinct_units"] == 215,
                   "Quality occurrences inflated distinct query units or repetition boundary")
    inputs.require(inputs.same(verify_freeze(prepared_path, commit, root=root), binding), "Frozen source changed during audit")
    return {"schema": "dependency_context_independent_audit_v1", "status": "passed", "checks_passed": True,
            "verified_terminal_decisions": 477, "exact_saved_control_occurrences": 954, "prior_role_control_occurrences": 477,
            "quality_occurrences": 421, "distinct_quality_query_units": 215, "verified_source_datasets": 13,
            "verified_source_files": 417, "retained_original_negative_references": 3734,
            "producer_freeze": run_binding, "auditor_execution_commit": binding["execution_commit"],
            "artifact_sha256": {**hashes, "summary.json": inputs.file_sha(outcome_path / "summary.json")},
            "prepared_plan_sha256": inputs.file_sha(prepared_path / "frozen-plan.json"),
            "independently_recomputed": recomputed, "graph_checks": graph_checks,
            "post_failure_certificate_harness_correction": {
                "classification": "Post-outcome technical correction; original396-row failure preserved",
                "prior_failure_publication_commit": "d16f876d29ec821226d595512253c3b4152b6c06",
                "scope": "Only top-level timings values may differ, with equal field presence and finite dictionary values; every other typed field and compact byte remains exact",
                "terminal_response_rewritten": False, "guard_graph_skip_or_quality_rule_changed": False,
                "timing_envelope_exclusions_count": len(exclusions), "timing_envelope_exclusions": exclusions},
            "shared_helpers": {"graph_resolver": "AnnotatedDependencies consistency and peer review; not an independent resolution-completeness proof",
                               "role_skip_facts": "frozen independent audit_role_context.classify_row",
                               "source_snapshot": "study_inputs literal source and immutable Git authority"},
            "independent_checks": ["accepted AST endpoint/annotation/call/import witnesses", "literal UTF-8 byte and physical-line provenance",
                                   "protected endpoint dictionaries", "exact whole-operand decision", "structural skip union",
                                   "actual stored compact payload and tokens", "all three individual quality controls and gate"],
            "calls": {"model": 0, "index": 0, "retrieval": 0, "network": 0, "new_v1_proposals": 0},
            "native_warm_latency_measured": False, "automatic_default_change": False,
            "classification": "Observed development including attrs; technical audit status is distinct from quality qualification"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--outcomes", required=True, type=Path)
    parser.add_argument("--freeze", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inputs.require(args.output.parent.is_dir() and not args.output.exists(), "Audit output requires an existing parent and new file")
    inputs.require(not args.output.resolve().is_relative_to(args.outcomes.resolve())
                   and not args.output.resolve().is_relative_to(args.prepared.resolve()), "Audit report must be outside immutable input/outcome directories")
    with cpu_only():
        report = audit(args.prepared, args.outcomes, args.freeze)
    inputs.write_json(args.output, report)
    print(json.dumps({"status": report["status"], "qualified": report["independently_recomputed"]["quality_gate"]["qualified"]}))


if __name__ == "__main__":
    main()
