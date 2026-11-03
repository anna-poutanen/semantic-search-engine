#!/usr/bin/env python3
"""Independent CPU audit of the prospective native role-context holdout.

The frozen conservative source graph/parser is shared. Native baseline/static
dependencies, packing, semantic features/selector, terminal roles, exact source,
forwards, resident reads and gates are independently reconstructed. No producer,
backend, embedding implementation, model, index or numerical runtime is imported.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import io
from copy import deepcopy
from datetime import datetime, timezone
import importlib.abc
import json
import math
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import sys
import tokenize
from unittest.mock import patch

import audit_flow_native as native
import audit_role_context as role
import audit_semantic_context as context
import audit_semantic_study as semantic


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "role_native_audit_v1"
STUDY = "role_context_native_v1"
MODE = "role_state_write_v1"
ARMS, RETRIEVALS = ("legacy", "coverage_control", "candidate"), ("dense", "hybrid")
PARENT_PUBLICATION = "597c5ed7235bcc1cc2ef95a8b9af0c0eb57a32ae"
RESERVED_DATASET = "benchmarks/attrs_complementary_holdout_queries.json"
RESERVED_DATASET_SHA = "2170ca4c21fc3750233a76a905918a914e9f2e0ba50dda3c30064cdefa89923d"
RESERVED_REVISION = "3dca08ce5bbe673d7df25f44f3dda92505d1043d"
PRIOR_SOURCE_FREEZE = "0b651ef32cb6ca2444a016587ce1842854696244"
FAILED_PREPARATION_COMMIT = "69fb0a88914d3e2721320789094810e47587ef91"
FAILED_PREPARATION = "results/role-native-preflight-2026-11-03/prepare-attempt-1"
REVIEW_FOLDER = "results/attrs-complementary-source-only-preflight-2026-10-29"
REVIEW_TRUE_FIELDS = ("OR_alternatives_cover_same_exact_bytes_and_distinct_qualified_spans",
    "all_positive_groups_reviewed_against_executable_source_and_context",
    "corroborating_tests_read_not_run_and_not_a_proof_of_all_combined_configurations",
    "multi_file_flags_and_cross_file_necessity_reviewed",
    "same_definition_separated_windows_are_distinct_real_requirements",
    "same_named_closure_branches_are_not_interchangeable",
    "scoped_absences_confirmed_from_complete_membership_API_and_callback_paths")
SOURCES = ("scripts/benchmark_role_native.py", "scripts/audit_role_native.py", "tests/test_role_native.py",
           "tests/test_role_native_audit.py", "docs/role-native-protocol.md")
SETTINGS = dict(native.SETTINGS)
GATE = dict(native.GATE)
require, canonical, sha, file_sha, read, jsonl = role.require, role.canonical, role.digest, role.file_sha, role.read, role.jsonl
win, source = native.win, native.source


def same(left, right):
    return context.same(left, right)


def source_view(response):
    return native.context_view(response)


def vector_hash(vector):
    return semantic.vector_hash(context.float32_vector(vector))


def verify_query(row, query, metadata):
    vector = context.float32_vector(row["query_vector"])
    require(len(vector) == 1024 and abs(math.fsum(value * value for value in vector) - 1) <= .001,
            "Actual retrieval query must be a normalized exact float32 vector")
    require(row.get("query_vector_sha256") == vector_hash(vector), "Executing retrieval vector hash differs")
    events = row.get("external_forward_events")
    require(type(events) is list and all(type(event) is dict and event.get("purpose") == "retrieval_query" for event in events),
            "Measured request contains document or unknown external forward events")
    original = [{key: value for key, value in event.items() if key != "purpose"} for event in events]
    counts = semantic.verify_forward_events(original, [context.QUERY_PROMPT + query], metadata, singleton=True)
    require(type(row.get("query_embedding_calls")) is int and row["query_embedding_calls"] == 1
            and type(row.get("document_embedding_calls")) is int and row["document_embedding_calls"] == 0,
            "Warm request needs one original query API and zero document inference")
    return original, counts


def verify_clock(row):
    clock = row.get("request_clock")
    require(type(clock) is dict, "Actual complete backend search clock required")
    start, end = clock.get("started_perf_counter"), clock.get("ended_perf_counter")
    require(type(start) is float and type(end) is float and math.isfinite(start) and math.isfinite(end) and end > start,
            "Measured backend monotonic interval must be positive and finite")
    elapsed = row.get("elapsed_backend_s")
    require(type(elapsed) is float and elapsed == end - start, "Backend duration must cover the complete search interval")
    low, high = (datetime.fromisoformat(clock[name]) for name in ("started_at_utc", "ended_at_utc"))
    require(low.tzinfo is not None and high.tzinfo is not None and low <= high, "Measured UTC request chronology differs")
    return elapsed


def verify_resident_read(reads, reference, raw, features, metadata):
    require(type(reads) is list and len(reads) == 1, "Active request needs exactly one actual resident lookup")
    record = reads[0]
    require(same(record.get("model_metadata"), metadata), "Resident lookup model identity differs")
    required = {}
    for row in [*raw, *reference["results"]]:
        if context.clipped(row):
            continue
        binding = context.row_binding(row)
        require(row["id"] not in required or same(required[row["id"]], binding), "Resident window identity conflicts")
        required[row["id"]] = binding
    requested = record.get("requested_rows")
    require(type(requested) is list and len(requested) == len(required)
            and [row["id"] for row in requested] == list(required)
            and all(same(context.row_binding(row), required[row["id"]]) for row in requested),
            "Actual resident lookup requested source/order differs")
    returned = record.get("returned_vectors")
    require(type(returned) is dict and returned.keys() == required.keys() == features["windows"].keys(),
            "Resident lookup returned membership differs")
    for identifier, value in returned.items():
        require(type(value) is dict and set(value) == {"vector", "float32_le_sha256"}
                and value["float32_le_sha256"] == vector_hash(value["vector"])
                and same(value["vector"], features["windows"][identifier]["vector"]),
                "Feature document vector differs from actual resident lookup")


def verify_provider(row, captured, query, metadata, original_events):
    reference, raw, features = (captured[name] for name in ("reference_response", "ranked_candidates", "features"))
    clauses = context.literal_clauses(query)
    receipts = captured.get("provider_receipts")
    require(type(receipts) is list and len(receipts) == int(bool(clauses)), "Provider invocation count differs from literal query eligibility")
    reason = ("abstained" if reference.get("match_status") == "insufficient_evidence" else
              "empty_raw_pool" if not raw else "empty_reference" if not reference["results"] else
              "ineligible_query" if not clauses else None)
    active = bool(clauses) and reason is None
    require(type(row.get("clause_query_calls")) is int and row["clause_query_calls"] == int(active)
            and type(row.get("clause_query_texts")) is int and row["clause_query_texts"] == (len(clauses) if active else 0),
            "Native clause API/text counts differ; clauses cannot be cached")
    if not active:
        require(features is None and row.get("resident_reads") == [], "Inactive request performed resident feature work")
        if receipts:
            expected = {"schema": "semantic_context_live_provider_v1", "status": "inactive", "reason": reason,
                "query": query, "retrieval": row["retrieval"], "extra_query_api_calls": 0,
                "extra_query_model_forward_calls": 0, "extra_query_encoded_texts": 0,
                "resident_vector_reads": 0, "document_inference_calls": 0, "extra_retrieval_calls": 0}
            require(same(receipts[0], expected), "Inactive provider receipt differs")
        return {"active": False, "extra_model_forwards": 0, "extra_texts": 0, "extra_duration_s": 0.0}
    context.audit_features(reference, raw, features)
    receipt = receipts[0]
    require(type(receipt) is dict and receipt.get("schema") == "semantic_context_live_provider_v1"
            and receipt.get("status") == "active" and receipt.get("query") == query
            and receipt.get("retrieval") == row["retrieval"], "Active provider identity differs")
    require(set(receipt) == {"schema", "status", "query", "retrieval", "source_snapshot", "model_metadata", "model_fingerprint",
        "facets", "extra_clause_prompts", "original_query_vector", "original_query_forward_events",
        "original_query_model_forward_calls", "original_query_encoded_texts", "extra_query_forward_events",
        "extra_query_api_calls", "extra_query_model_forward_calls", "extra_query_encoded_texts", "resident_vector_read_attempts",
        "resident_vector_reads", "document_inference_calls", "extra_retrieval_calls", "windows"}, "Active provider receipt fields differ")
    binding = captured.get("request_binding")
    require(type(binding) is dict and binding.get("query") == query and binding.get("retrieval") == row["retrieval"]
            and type(binding.get("source_snapshot")) is str and bool(binding["source_snapshot"])
            and binding["source_snapshot"] == receipt.get("source_snapshot")
            and binding.get("model_fingerprint") == receipt.get("model_fingerprint") == features["model_fingerprint"],
            "Actual executing query/source/model binding differs")
    require(same(receipt.get("model_metadata"), metadata) and same(features["model_metadata"], metadata)
            and same(receipt.get("facets"), [query, *clauses])
            and same(receipt.get("extra_clause_prompts"), [context.QUERY_PROMPT + clause for clause in clauses]),
            "Actual query/model/facet prompt provenance differs")
    original = {"origin": "executing_retrieval_query_vector", "reencoded": False, "dimensions": 1024,
                "float32_le_sha256": row["query_vector_sha256"]}
    require(same(receipt.get("original_query_vector"), original)
            and same(features["facet_vectors"][0], row["query_vector"])
            and same(receipt.get("original_query_forward_events"), original_events),
            "Semantic features must reuse the exact executing original query and forwards")
    forwards = semantic.verify_forward_events(receipt.get("extra_query_forward_events"),
        [context.QUERY_PROMPT + clause for clause in clauses], metadata)
    counts = {"original_query_model_forward_calls": 1, "original_query_encoded_texts": 1,
        "extra_query_api_calls": 1, "extra_query_model_forward_calls": forwards["model_forward_calls"],
        "extra_query_encoded_texts": len(clauses), "resident_vector_read_attempts": 1,
        "resident_vector_reads": 1, "document_inference_calls": 0, "extra_retrieval_calls": 0}
    require(all(type(receipt.get(name)) is int and receipt[name] == value for name, value in counts.items()),
            "Actual provider inference/lookup/retrieval work differs")
    windows = {identifier: {"binding": value["binding"], "float32_le_sha256": vector_hash(value["vector"])}
               for identifier, value in features["windows"].items()}
    require(same(receipt.get("windows"), windows), "Provider feature/source/vector binding differs")
    verify_resident_read(row.get("resident_reads"), reference, raw, features, metadata)
    return {"active": True, "extra_model_forwards": forwards["model_forward_calls"], "extra_texts": len(clauses),
        "extra_duration_s": math.fsum(event["duration_seconds"] for event in receipt["extra_query_forward_events"]
                                    if event["status"] == "completed")}


def semantic_receipt_equal(actual, expected, features):
    """Frozen diagnostic precedent; only None-feature invalid wording may differ."""
    diagnostic = None
    if features is None and expected.get("fallback_reason") == "invalid_features" and type(actual) is dict:
        require(actual.get("fallback_reason") == "invalid_features"
                and type(actual.get("feature_error")) is str and bool(actual["feature_error"])
                and type(expected.get("feature_error")) is str and bool(expected["feature_error"]),
                "Both None-feature decisions require nonempty invalid-feature diagnostics")
        copied = deepcopy(actual)
        if actual["feature_error"] != expected["feature_error"]:
            require(actual["feature_error"] == "Feature schema fields differ"
                    and expected["feature_error"] == "Semantic feature schema",
                    "Only the exact already published inactive diagnostic pair may differ")
            diagnostic = {"actual": actual["feature_error"], "independent": expected["feature_error"]}
        copied["feature_error"] = expected["feature_error"]
        actual = copied
    require(same(actual, expected), "Independent native v1 selector receipt differs")
    return diagnostic


def verify_trace(row, fixture, counter, *, query, scope, model_metadata):
    """Reconstruct controls, original features, v1 and terminal guard once."""
    require(role.literal_json(row), "Native trace must be finite typed literal JSON")
    semantic.verify_metadata(model_metadata)
    elapsed = verify_clock(row)
    original_events, _ = verify_query(row, query, model_metadata)
    stages, inventory, keys = row["stages"], fixture["inventory"], fixture["definition_keys"]
    ranked = stages["ranked"]
    require(len(ranked) <= 50 and same(stages.get("definition_keys"), keys), "Original full raw cap/canonical source map differs")
    native.win.raw_ranked(ranked, inventory)
    canonical_definitions = row["arm"] != "legacy"
    selected, eligible, origins = native.baseline(ranked, inventory, keys, fixture["graph_edges"],
                                                 canonical_definitions=canonical_definitions)
    require(same(stages.get("selected"), selected) and same(stages.get("eligible"), eligible)
            and same(stages.get("origins"), origins), "Independent native source/static dependency selection differs")
    require(same(stages.get("ambiguous_symbols"), [list(value) for value in sorted(native.canonical_ambiguities(keys))]),
            "Native ambiguous source definitions differ")
    for name in ("selected", "eligible"):
        require(not win.verify_stage(stages[name], inventory, fixture["sources"], keys,
                                    {pair["snippet"]["id"]: pair for pair in ranked}), "Role guard may not create composites")
    raw, candidates = native.full_rows(ranked), native.full_rows(selected)
    require(same(row.get("ranked_candidates"), raw) and same(row.get("backfill_candidates"), candidates),
            "Actual full raw/backfill source order/scalars differ")
    before = source.ordinary_pack(native.native_template(query, scope, ranked), eligible, origins, counter)
    require(same(source_view(row["pre_postprocess_response"]), before), "Original maximal source/token prefix packing differs")
    baseline = before if row["arm"] == "legacy" else source.coalesce_pack(before, candidates, counter)
    captured = row.get("candidate_capture")
    activity = {"active": False, "extra_model_forwards": 0, "extra_texts": 0, "extra_duration_s": 0.0,
                "diagnostic_normalization": None, "role_accepted": None}
    if row["arm"] == "candidate":
        require(type(captured) is dict and same(captured.get("limits"), role.LIMITS)
                and same(captured.get("precoalesce_rows"), before["results"])
                and same(captured.get("ranked_candidates"), raw) and same(captured.get("definition_keys"), keys),
                "Actual native candidate reference/precoalesce/raw/source operands differ")
        request = captured.get("request_binding")
        require(type(request) is dict and request.get("query") == query and request.get("retrieval") == row["retrieval"]
                and type(request.get("source_snapshot")) is str and bool(request["source_snapshot"])
                and request.get("model_fingerprint") == semantic.model_fingerprint(model_metadata),
                "Actual candidate executing source/query/model snapshot differs")
        require(all(type(captured.get(name)) is int and captured[name] == count for name, count in
                    (("coverage_coalesce_calls", 1), ("semantic_calls", 1), ("feature_calls", int(bool(context.literal_clauses(query)))))),
                "Actual adapter invocation counters differ")
        reference = captured["reference_response"]
        require(same(source_view(reference), baseline) and reference.get("retrieval") == row["retrieval"],
                "Actual native coverage reference differs from independent control")
        require(context.compact_payload(reference) == context.compact_payload(baseline),
                "Actual coverage compact scope/metadata order differs")
        activity.update(verify_provider(row, captured, query, model_metadata, original_events))
        proposed, expected_semantic = context.reconstruct(reference, raw, captured["features"], definition_keys=keys,
            precoalesce_rows=before["results"], counter=counter, **role.LIMITS)
        activity["diagnostic_normalization"] = semantic_receipt_equal(captured.get("semantic_receipt"), expected_semantic, captured["features"])
        require(context.same_source_response(captured.get("v1_response"), proposed), "Actual unchanged v1 proposal differs")
        require(context.compact_payload(captured["v1_response"]) == context.compact_payload(proposed),
                "Actual unchanged v1 compact scope/metadata order differs")
        expected, receipt = role.reconstruct(reference, captured["v1_response"], definition_keys=keys)
        require(same(captured.get("role_guard_receipt"), receipt)
                and context.same_source_response(captured.get("response"), expected), "Independent native terminal guard differs")
        activity["role_accepted"] = receipt["accepted"]
        require(same(row.get("reference_response"), reference) and same(row.get("proposal_response"), captured["v1_response"]),
                "Native duplicate source operands differ from actual candidate capture")
        calls = {"coverage_coalesce": 1, "semantic": 1, "role_guard": 1, "feature": int(bool(context.literal_clauses(query))),
                 "current_v1": 1, "extra_retrieval": 0, "document_inference": 0, "backfill_after_guard": 0}
        require(same(captured.get("calls"), calls), "Actual native candidate passes/inference/backfill counts differ")
        phases = captured.get("phase_timings")
        require(type(phases) is dict and set(phases) == {"coverage_context_s", "live_feature_provider_s",
                    "prepared_feature_allocation_s", "terminal_role_guard_s"}
                and all(type(value) is float and math.isfinite(value) and value >= 0 for value in phases.values())
                and math.fsum(phases.values()) <= elapsed + 1e-9, "Actual disjoint candidate phase clocks differ")
    else:
        require(captured is None and row.get("resident_reads") == []
                and row.get("reference_response") is None and row.get("proposal_response") is None
                and type(row.get("clause_query_calls")) is int and row["clause_query_calls"] == 0
                and type(row.get("clause_query_texts")) is int and row["clause_query_texts"] == 0,
                "Native control performed semantic feature/role work")
        expected = baseline
    require(same(source_view(row["response"]), source_view(expected)), "Actual final native source choice differs")
    if row["arm"] == "candidate":
        require(context.same_source_response(row["response"], expected), "Final native metadata changed outside backend operational maps")
    require(row["response"].get("retrieval") == row["retrieval"], "Final native actual retrieval mode differs")
    require(type(row.get("guard_calls")) is int and row["guard_calls"] == int(row["arm"] == "candidate"),
            "Native actual terminal guard call count differs")
    for response in (row["pre_postprocess_response"], row["response"]):
        win.verify_response(response, inventory, fixture["sources"], keys, ranked, counter,
                            raw_rows={value["id"]: value for value in raw})
        require(response["query"] == query and same(response["scope"], scope)
                and all(not any(name in value for name in context.PROVENANCE) for value in response["results"]),
                "Saved source scope/provenance differs")
    payload = context.compact_payload(expected)
    tokens = len(counter.encode(payload, disallowed_special=()))
    require(row.get("compact_payload") == payload == context.compact_payload(row["response"])
            and row.get("compact_sha256") == sha(payload.encode())
            and type(row.get("actual_compact_tokens")) is int and row["actual_compact_tokens"] == tokens
            == row["response"]["token_usage"]["tokens"] <= 4096, "Actual native compact source/token bytes differ")
    duration = math.fsum(event["duration_seconds"] for event in original_events if event["status"] == "completed")
    require(duration + activity["extra_duration_s"] <= elapsed + 1e-9, "Native model work lies outside measured backend interval")
    return activity


def expected_schedule(queries):
    return native.expected_schedule(queries)


def verify_repeats(rows, queries):
    require([(row["repetition"], row["query_id"], row["arm"], row["retrieval"]) for row in rows]
            == expected_schedule(queries), "Complete rotated 252-request native schedule differs")
    vectors, ranked, payloads = {}, {}, {}
    snapshots = set()
    for row in rows:
        identifier, mode, arm = row["query_id"], row["retrieval"], row["arm"]
        require(vectors.setdefault(identifier, row["query_vector_sha256"]) == row["query_vector_sha256"],
                "Executing query float32 bytes changed across arms/modes/repetitions")
        raw_hash = sha(canonical(row["stages"]["ranked"]))
        require(ranked.setdefault((identifier, mode), raw_hash) == raw_hash, "Original raw source/order/scalar bytes changed across arms/repetitions")
        require(payloads.setdefault((identifier, mode, arm), row["compact_sha256"]) == row["compact_sha256"],
                "Actual final compact payload changed across repetitions")
        captured = row.get("candidate_capture")
        if captured:
            snapshots.add(captured["request_binding"]["source_snapshot"])
    require(len(snapshots) <= 1, "Resident source snapshot changed across measured requests")
    verify_resident_stability(rows)


def verify_resident_stability(rows):
    observed, snapshots = {}, set()
    for row in rows:
        captured = row.get("candidate_capture")
        if not captured:
            continue
        snapshots.add(captured["request_binding"]["source_snapshot"])
        features = captured.get("features")
        if features is None:
            continue
        for identifier, record in features["windows"].items():
            key = identifier, record["binding"]["document_sha256"], features["model_fingerprint"]
            value = vector_hash(record["vector"])
            require(observed.setdefault(key, value) == value, "Same resident model/source document vector changed between requests")
    require(len(snapshots) <= 1, "Executing source snapshot changed between warmups and measured calls")


def native_metrics(rows):
    metrics = win.native_summary(rows)
    for mode, value in metrics["per_retrieval"].items():
        value.pop("active_composite_overhead")
        for arm in value["arms"].values():
            arm.pop("delivered_composites")
            arm.pop("active_composite_calls")
        candidates = [row for row in rows if row["retrieval"] == mode and row["arm"] == "candidate"]
        controls = {arm: {(row["query_id"], row["repetition"]): row for row in rows
                         if row["retrieval"] == mode and row["arm"] == arm} for arm in ARMS[:-1]}
        value["role_activity"] = {"accepted_calls": sum(row["candidate_capture"]["role_guard_receipt"]["accepted"] for row in candidates),
            "changed_calls": sum(row["compact_sha256"] != controls["coverage_control"][(row["query_id"], row["repetition"])]["compact_sha256"] for row in candidates),
            "clause_api_calls": sum(row["clause_query_calls"] for row in candidates),
            "clause_encoded_texts": sum(row["clause_query_texts"] for row in candidates)}
        value["median_paired_wall_delta_to_controls_s"] = {arm: statistics.median(
            row["elapsed_backend_s"] - controls[arm][(row["query_id"], row["repetition"])]["elapsed_backend_s"]
            for row in candidates) for arm in ARMS[:-1]}
    return metrics


def python_inventory(sources, *, max_lines=160, overlap_lines=40):
    """Rebuild every Python definition/window directly from the original AST.

    No production extractor is imported. Scope ancestry, decorator boundaries,
    overload/placeholder omission and implementation-bearing windows are all
    checked, including missing definitions which per-row boundary checks miss.
    """
    require(type(max_lines) is int and type(overlap_lines) is int and 0 <= overlap_lines < max_lines,
            "Valid fixed source window limits required")
    inventory, definition_keys, definition_count = [], {}, 0

    def docstring(node):
        return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)

    def placeholder(statement):
        if isinstance(statement, ast.Pass):
            return True
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and statement.value.value is Ellipsis:
            return True
        if isinstance(statement, ast.Raise):
            raised = statement.exc.func if isinstance(statement.exc, ast.Call) else statement.exc
            return isinstance(raised, ast.Name) and raised.id == "NotImplementedError" or isinstance(raised, ast.Attribute) and raised.attr == "NotImplementedError"
        return False

    for path, text in sorted(sources.items()):
        require(Path(path).suffix == ".py" and type(text) is str, "This fresh inventory requires only complete Python implementation files")
        tree, scopes, functions, docs = ast.parse(text, filename=path), [], [], set()

        def visit(node):
            nonlocal definition_count
            scoped = isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            if scoped:
                scopes.append(node.name)
                if node.body and docstring(node.body[0]):
                    docs.update(range(node.body[0].lineno, node.body[0].end_lineno + 1))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = node.body[1:] if node.body and docstring(node.body[0]) else node.body
                overload = any(isinstance(item, ast.Name) and item.id == "overload"
                    or isinstance(item, ast.Attribute) and item.attr == "overload" for item in node.decorator_list)
                if not overload and not all(placeholder(item) for item in body):
                    functions.append((".".join(scopes), node))
                    definition_count += 1
            for child in ast.iter_child_nodes(node):
                visit(child)
            if scoped:
                scopes.pop()

        visit(tree)
        lines = context.physical_lines(text)
        for symbol, node in functions:
            implementation = set()
            for statement in ast.walk(node):
                if statement is node or not isinstance(statement, ast.stmt) or docstring(statement):
                    continue
                high = (max(statement.lineno, statement.body[0].lineno - 1)
                        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else statement.end_lineno)
                covered = set(range(statement.lineno, high + 1))
                if hasattr(statement, "body") or isinstance(statement, ast.Match):
                    covered.difference_update(docs)
                implementation.update(covered)
            low, high = min([node.lineno, *(item.lineno for item in node.decorator_list)]), node.end_lineno
            key = [path, symbol, low, high]
            for start in range(low, high + 1, max_lines - overlap_lines):
                end = min(high, start + max_lines - 1)
                if any(start <= line <= end for line in implementation):
                    source_text = "".join(lines[start-1:end])
                    identifier = f"{path}:{symbol}:{start}-{end}"
                    require(identifier not in definition_keys, "Duplicate canonical Python window identity")
                    inventory.append({"id": identifier, "path": path, "symbol": symbol, "start_line": start,
                        "end_line": end, "text": source_text, "content_hash": sha(source_text.encode())})
                    definition_keys[identifier] = list(key)
                if end == high:
                    break
    return {"inventory": inventory, "definition_keys": definition_keys, "definitions": definition_count}


def verify_python_inventory(document, sources):
    expected = python_inventory(sources)
    require(same(document.get("inventory"), expected["inventory"])
            and same(document.get("definition_keys"), expected["definition_keys"]),
            "Complete independent Python definition/window inventory differs")
    win.verify_inventory({row["id"]: row for row in expected["inventory"]}, sources, expected["definition_keys"])
    return {"windows": len(expected["inventory"]), "definitions": expected["definitions"],
        "inventory_sha256": sha(canonical(expected["inventory"])),
        "definition_keys_sha256": sha(canonical(expected["definition_keys"]))}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def ancestor(root, first, last):
    require(type(first) is str and re.fullmatch(r"[0-9a-f]{40}", first), "Full immutable Git SHA required")
    require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", first, last],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0, "Prospective native ancestry differs")


def development_parent(root, binding, *, counter=None):
    """Before any fresh/checkpoint/tokenizer bytes: bind the whole public proof.

    With a supplied offline counter, additionally rerun the unchanged independent
    393-case audit. This checks observed development, never fresh native results.
    """
    root, parent = Path(root).resolve(), binding["development_parent"]
    folder = "results/role-context-development-2026-11-03"
    paths = {"prepared_path": folder + "/prepared-v2", "replay_path": folder + "/replay",
             "audit_path": folder + "/independent-audit.json"}
    require(parent.get("publication_commit") == PARENT_PUBLICATION
            and all(parent.get(key) == value for key, value in paths.items()), "Exact public development parent paths required")
    plan_folder, replay = root / paths["prepared_path"], root / paths["replay_path"]
    plan, report = read(plan_folder / "frozen-plan.json"), read(root / paths["audit_path"])
    expected = {**plan["parent_binding"]["sha256"], **plan["source_sha256"],
        **{(plan_folder / name).relative_to(root).as_posix(): value for name, value in plan["artifact_sha256"].items()}}
    for path in (plan_folder / "frozen-plan.json", replay / "run-start.json", replay / "outcomes.jsonl.gz",
                 replay / "summary.json", root / paths["audit_path"]):
        expected[path.relative_to(root).as_posix()] = file_sha(path)
    require(same(parent.get("artifacts"), expected), "Entire unchanged development source/input/result closure required")
    for name, value in expected.items():
        role.verify_blob(root, name, value, (PARENT_PUBLICATION, "HEAD"))
    require(report.get("schema") == "role_context_audit_v1" and report.get("status") == "passed"
            and report.get("checks_passed") is True and report.get("verified_candidate_rows") == 393
            and report.get("exact_control_rows") == 786 and report.get("retained_negative_references") == 3734
            and report.get("distinct_query_units") == 201 and report.get("referenced_saved_v1_proposals") == 393,
            "Complete independently audited 393/786/3734/201 public development proof required")
    gate = report["independently_recomputed_qualification"]
    require(same(parent.get("qualification"), gate) and gate.get("qualified") is True
            and set(gate["against_controls"]) == {"legacy", "coverage_control"}
            and all(not value[name] for value in gate["against_controls"].values()
                    for name in ("group_losses", "complete_losses", "single_file_first_hit_losses"))
            and len({tuple(value) for value in gate["additional_distinct_complete_multi_queries_vs_coverage"]}) >= 1,
            "Parent needs individual preservation against both controls and a distinct multi-file gain")
    require(same(parent.get("producer_freeze"), report["producer_freeze"]), "Qualified parent prospective identity differs")
    ancestor(root, report["producer_freeze"]["commit"], report["producer_freeze"]["execution_commit"])
    ancestor(root, report["producer_freeze"]["execution_commit"], PARENT_PUBLICATION)
    ancestor(root, PARENT_PUBLICATION, "HEAD")
    if counter is not None:
        recomputed = role.audit(root, plan_folder, replay, counter)
        require(same(recomputed, report), "Unchanged exhaustive independent development audit differs")
    return {"publication_commit": PARENT_PUBLICATION, **paths, "bound_blobs": len(expected),
            "verified_outcomes": 393, "control_references": 786, "negative_references": 3734,
            "distinct_units": 201, "qualified": True, "exhaustive_reconstruction": counter is not None}


def first_source_freeze(root, binding, *, before=None):
    frozen = binding.get("native_source_freeze")
    require(type(frozen) is dict and type(frozen.get("sha256")) is dict
            and set(SOURCES) <= set(frozen["sha256"]), "Complete committed native source-first freeze required")
    ancestor(root, frozen["commit"], "HEAD")
    required = {path.relative_to(root).as_posix() for path in (Path(root) / "src/semantic_search").rglob("*.py")}
    required.update(path.relative_to(root).as_posix() for path in (Path(root) / "scripts").glob("*.py"))
    required.update({"pyproject.toml", "requirements-mac.lock", *SOURCES})
    require(required == set(frozen["sha256"]), "Native first freeze source closure differs")
    for name, value in frozen["sha256"].items():
        role.verify_blob(root, name, value, (frozen["commit"], "HEAD"))
    committed = int(git(root, "show", "-s", "--format=%ct", frozen["commit"]).decode().strip())
    require(type(frozen.get("committed_at_unix")) is int and frozen["committed_at_unix"] == committed,
            "Native source-first commit time differs")
    if before is not None:
        require(committed <= datetime.fromisoformat(before).timestamp(), "Source-first freeze must precede fresh preparation")
    parent_bytes = binding["development_parent"]["artifacts"]
    for name, value in parent_bytes.items():
        role.verify_blob(root, name, value, (frozen["commit"], "HEAD"))
    require(type(frozen.get("bound_blob_count")) is int and frozen["bound_blob_count"] == len(frozen["sha256"])
            and type(frozen.get("parent_bound_blob_count")) is int and frozen["parent_bound_blob_count"] == len(parent_bytes),
            "Complete native first-freeze source/parent blob counts differ")
    return frozen


def verify_review_compatibility(root, binding):
    """Preserve the failed source-only attempt and disclose the interface fix."""
    compatibility = binding.get("source_review_compatibility")
    require(type(compatibility) is dict and compatibility.get("prior_source_freeze") == PRIOR_SOURCE_FREEZE
            and compatibility.get("failed_preparation_commit") == FAILED_PREPARATION_COMMIT
            and compatibility.get("projection_scope") == "in_memory_legacy_helper_only",
            "Versioned original flat-review compatibility provenance required")
    expected = {FAILED_PREPARATION + "/" + name: file_sha(Path(root) / FAILED_PREPARATION / name)
                for name in ("failure.json", "prepare.log")}
    require(same(compatibility.get("failed_preparation_artifacts"), expected), "Both original failed-preparation artifacts required")
    for name, value in expected.items():
        role.verify_blob(root, name, value, (FAILED_PREPARATION_COMMIT, binding["native_source_freeze"]["commit"], "HEAD"))
        require(binding["implementation_sha256"].get(name) == value, "Original failed preparation omitted from corrected closure")
    ancestor(root, PRIOR_SOURCE_FREEZE, FAILED_PREPARATION_COMMIT)
    ancestor(root, FAILED_PREPARATION_COMMIT, binding["native_source_freeze"]["commit"])
    receipt = read(Path(root) / FAILED_PREPARATION / "failure.json")
    require(receipt.get("status") == "failed_source_only_preparation" and receipt.get("source_freeze") == PRIOR_SOURCE_FREEZE
            and receipt.get("output_plan_created") is False
            and receipt.get("log_sha256") == expected[FAILED_PREPARATION + "/prepare.log"]
            and all(type(receipt.get(name)) is int and receipt[name] == 0 for name in
                    ("model_calls", "index_calls", "retrieval_calls", "native_outcomes")),
            "Prior attempt was not the preserved zero-inference source-only compatibility failure")
    return compatibility


def verify_flat_reviews(review, author, *, author_sha256, dataset_sha256, protocol_sha256, revision,
                        source_files, excluded_files, unsupported_files, inventory, definition_keys, counts):
    """Validate original flat receipts as flat receipts, without normalizing them.

    The old generic producer interface may project metadata in memory. This
    independent checker never consumes that projection or persists a substitute.
    """
    require(type(review) is dict and type(author) is dict and role.literal_json(review) and role.literal_json(author),
            "Original typed flat review receipts required")
    require(review.get("status") == author.get("status") == "passed"
            and review.get("dataset_sha256") == author.get("dataset_sha256") == dataset_sha256
            and review.get("protocol_sha256") == author.get("protocol_sha256") == protocol_sha256
            and review.get("revision") == revision and review.get("author_source_receipt_sha256") == author_sha256,
            "Original flat source-review dataset/protocol/revision/author link differs")
    require(same(review.get("calls"), {name: 0 for name in ("index", "model", "network", "reranker", "retrieval", "selector_activation")})
            and same(author.get("calls"), {name: 0 for name in ("model", "index", "retrieval", "rule_activation", "network")})
            and review.get("blocking_findings") == [] and review.get("dataset_changed") is False
            and review.get("upstream_repository_module_or_test_executed") is False
            and author.get("source_repository_code_executed") is False and author.get("source_only_precommit_receipt") is True,
            "Original reviews must remain source-only with no blocking findings or dataset edits")
    semantic_review = review.get("semantic_review")
    require(type(semantic_review) is dict and all(semantic_review.get(name) is True for name in REVIEW_TRUE_FIELDS),
            "Original independent semantic source review is incomplete")
    require(same(review.get("counts"), counts) and same(author.get("counts"), counts), "Original reviewed source/sample counts differ")
    required = ("id", "path", "symbol", "start_line", "end_line", "content_hash")
    windows = [{name: value[name] for name in required} for value in inventory]
    require(same(author.get("source_files"), source_files) and same(author.get("excluded_supported_files"), excluded_files)
            and same(author.get("unsupported_source_root_files"), unsupported_files)
            and same(author.get("window_inventory"), windows) and same(author.get("definition_keys"), definition_keys),
            "Original author receipt source/complete-window/canonical facts differ")
    # Original archived validator used JSON's default ASCII escaping, plus NL.
    def author_canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    require(author.get("window_inventory_sha256") == sha(author_canonical(windows))
            and author.get("definition_keys_sha256") == sha(author_canonical(definition_keys)),
            "Original author canonical inventory hash framing differs")
    return {"format": "attrs_flat_source_review_v1", "author_sha256": author_sha256,
            "counts": deepcopy(counts), "persisted_normalized_receipt": False}


def fresh_fixture(root, corpus, output, binding):
    """Complete source-only Python membership/boundaries; never ranks or scores."""
    root, corpus, output = Path(root).resolve(), Path(corpus).resolve(), Path(output).resolve()
    require(binding.get("dataset_path") == RESERVED_DATASET and binding.get("dataset_sha256") == RESERVED_DATASET_SHA
            and binding.get("revision") == RESERVED_REVISION, "The original reserved attrs sample is fixed")
    dataset_path = (root / binding["dataset_path"]).resolve()
    require(dataset_path.is_relative_to(root) and file_sha(dataset_path) == binding["dataset_sha256"], "Frozen authored dataset bytes differ")
    data = read(dataset_path)
    require(git(corpus, "rev-parse", "HEAD").decode().strip() == data["revision"] == binding["revision"]
            and not git(corpus, "status", "--porcelain=v1").strip(), "Fresh corpus must be clean at the authored revision")
    root_path = (corpus / data["source_root"]).resolve()
    require(root_path.is_relative_to(corpus) and root_path.is_dir(), "Fresh source root escapes corpus")
    exclusions = data.get("source_exclusions", [])
    require(type(exclusions) is list and all(type(value) is str and value and not Path(value).is_absolute()
            and ".." not in Path(value).parts for value in exclusions), "Valid fixed source exclusions required")
    included, excluded, unsupported = {}, {}, {}
    supported = set(role.GRAMMARS)
    for raw_name in git(corpus, "ls-files", "-z", "--", data["source_root"]).split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode("utf-8", "surrogateescape")
        path = (corpus / name).resolve()
        suffix = Path(name).suffix
        if not path.is_file() or not path.is_relative_to(root_path):
            continue
        if suffix not in supported:
            unsupported[name] = {"bytes": path.stat().st_size, "sha256": file_sha(path)}
            continue
        if suffix != ".py" and (name.endswith((".d.ts", ".d.mts", ".d.cts")) or
                {"node_modules", "bower_components", "dist", "build", "coverage", ".next", ".nuxt", ".svelte-kit", "out"}.intersection(Path(name).parts[:-1])):
            continue
        value = {"bytes": path.stat().st_size, "sha256": file_sha(path)}
        (excluded if any(fnmatch.fnmatchcase(name, pattern) for pattern in exclusions) else included)[name] = value
    require(same(included, data["source_files"]) and same(included, binding["source_files"])
            and same(excluded, data.get("excluded_supported_files", {}))
            and sha(native.old.canonical(included)) == data["source_inventory_sha256"],
            "Complete included/excluded supported source membership differs")
    sources = {}
    for name, metadata in included.items():
        require(Path(name).suffix == ".py" and binding["source_sha256"].get(name) == metadata["sha256"],
                "Pinned Python implementation source membership differs")
        raw = (corpus / name).read_bytes()
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        sources[name] = raw.decode(encoding)
    require(set(sources) == set(binding["source_sha256"]), "Frozen source map omits or adds files")
    document = read(output / "source-inventory.json")
    proof = verify_python_inventory(document, sources)
    require(sha(json.dumps(document["inventory"], sort_keys=True).encode()) == binding["inventory_sha256"]
            and sha(native.old.canonical(document["definition_keys"])) == binding["definition_keys_sha256"],
            "Complete source/definition inventory hash binding differs")
    review_path = (root / binding["source_review_path"]).resolve()
    require(review_path.is_relative_to(root) and file_sha(review_path) == binding["source_review_sha256"], "Independent source review bytes differ")
    review = read(review_path)
    author_binding = binding.get("source_review_author")
    require(binding.get("source_review_format") == "attrs_flat_source_review_v1" and type(author_binding) is dict
            and set(author_binding) == {"path", "sha256"} and author_binding["path"] == REVIEW_FOLDER + "/source-review.json"
            and binding["source_review_path"] == REVIEW_FOLDER + "/independent-source-review.json",
            "Original flat independent/author review paths required")
    author_path = root / author_binding["path"]
    author = read(author_path)
    require(file_sha(author_path) == author_binding["sha256"], "Original author receipt bytes differ")
    groups = [native.old.evidence_groups(query) for query in data["queries"]]
    multiple = [len({target["path"] for alternatives in gold for target in alternatives}) > 1 for gold in groups]
    definition_counts = {}
    for key in document["definition_keys"].values():
        definition_counts[tuple(key)] = definition_counts.get(tuple(key), 0) + 1
    counts = {"queries": len(data["queries"]), "present": sum(query["kind"] == "present" for query in data["queries"]),
        "multi_file_present": sum(multiple), "single_file_present": sum(query["kind"] == "present" and not multi
            for query, multi in zip(data["queries"], multiple)), "scoped_absent": sum(query["kind"] == "absent" for query in data["queries"]),
        "required_positive_evidence_groups": sum(len(gold) for gold in groups),
        "target_rows": sum(len(alternatives) for gold in groups for alternatives in gold),
        "source_files": len(included), "excluded_supported_files": len(excluded), "unsupported_source_root_files": len(unsupported),
        "source_windows_160_40": len(document["inventory"]), "extracted_definitions": len(definition_counts),
        "source_files_with_extracted_implementations": len({value["path"] for value in document["inventory"]}),
        "canonical_definitions_with_multiple_windows": sum(count > 1 for count in definition_counts.values())}
    verify_flat_reviews(review, author, author_sha256=author_binding["sha256"], dataset_sha256=binding["dataset_sha256"],
        protocol_sha256=file_sha(root / data["source_review_protocol"]), revision=binding["revision"], source_files=included,
        excluded_files=excluded, unsupported_files=unsupported, inventory=document["inventory"],
        definition_keys=document["definition_keys"], counts=counts)
    auxiliaries = binding.get("source_review_auxiliaries")
    expected_auxiliaries = {REVIEW_FOLDER + "/independent-source-reviewer.py.txt": review["reviewer_source_sha256"],
        REVIEW_FOLDER + "/source-review-validator.py.txt": author["validator_sha256"],
        REVIEW_FOLDER + "/absence-checks.json": author["absence_checks_sha256"]}
    require(same(auxiliaries, expected_auxiliaries), "Original reviewer/validator/absence source artifacts required")
    for name, value in author["shared_source_extractor_sha256"].items():
        role.verify_blob(root, name, value, (PARENT_PUBLICATION, "HEAD"))
        require(binding["implementation_sha256"].get(name) == value, "Reviewed source-extractor authority differs")
    verify_review_compatibility(root, binding)
    freshness = binding.get("freshness_review")
    require(type(freshness) is dict and set(freshness) == {"path", "sha256"}, "Bound original freshness review required")
    frozen_files = {binding["dataset_path"]: binding["dataset_sha256"], binding["source_review_path"]: binding["source_review_sha256"],
                    author_binding["path"]: author_binding["sha256"], **expected_auxiliaries,
                    freshness["path"]: freshness["sha256"], data["source_review_protocol"]: file_sha(root / data["source_review_protocol"])}
    for name, value in frozen_files.items():
        role.verify_blob(root, name, value, (PARENT_PUBLICATION, binding["native_source_freeze"]["commit"], "HEAD"))
        require(binding["implementation_sha256"].get(name) == value, "Original authored review/dataset omitted from native closure")
    require(type(read(root / freshness["path"])) is dict, "Freshness review must preserve its original structured disclosure")
    rows = document["graph_edges"]
    require(sha(native.old.canonical(rows)) == binding["graph_sha256"], "Complete frozen conservative source graph hash differs")
    inventory = {row["id"]: row for row in document["inventory"]}
    edges = source.verify_graph_records(rows, sources, inventory)
    return data, {"inventory": inventory, "sources": sources, "definition_keys": document["definition_keys"], "graph_edges": edges}, proof


SOURCE_ONLY_CALLS = {name: 0 for name in ("model", "index", "retrieval", "selector", "postprocessor", "rule_activation", "gold_coverage_scoring")}


def verify_queries(data, plan, fixture, binding):
    queries = semantic._unique(data["queries"], "id")
    require(len(queries) == 14 and same(plan["queries"], [{name: query[name] for name in ("id", "query", "kind")}
                                                       for query in data["queries"]]), "Unchanged full 14-query membership/order required")
    groups = {identifier: native.old.evidence_groups(query) for identifier, query in queries.items()}
    multi = {identifier: len({target["path"] for alternatives in gold for target in alternatives}) > 1 for identifier, gold in groups.items()}
    require(sum(multi.values()) == 8 and sum(query["kind"] == "absent" for query in queries.values()) == 2
            and sum(query["kind"] == "present" and not multi[identifier] for identifier, query in queries.items()) == 4,
            "Unchanged eight multi/four single/two absent strata required")
    # These are authoring-time source eligibility checks, not retrieval coverage.
    for gold in groups.values():
        for alternatives in gold:
            for target in alternatives:
                low, high = target["evidence_lines"]
                require(type(low) is int and type(high) is int and 1 <= low <= high
                        and sha(win.literal(fixture["sources"], target["path"], low, high).encode()) == target["evidence_sha256"]
                        and target["source_file_sha256"] == binding["source_sha256"][target["path"]], "Authored qualified evidence literal bytes differ")
                require(any(item["path"] == target["path"] and item["symbol"] == target["symbol"]
                            and item["start_line"] <= low <= high <= item["end_line"] for item in fixture["inventory"].values()),
                        "Authored full evidence interval is not eligible in one original source window")
    return queries, groups, multi


def audit_inputs(root, corpus, output, counter, *, parent=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    require(output.is_relative_to(root), "Native plan must live inside bound project")
    plan = read(output / "plan.json")
    binding = plan["binding"]
    # This metadata/publication proof precedes every fresh/checkpoint read.
    parent = development_parent(root, binding) if parent is None else parent
    first = first_source_freeze(root, binding, before=plan["frozen_at_utc"])
    require(plan.get("schema") == STUDY and plan.get("version") == 1 and plan.get("classification") == "repository_holdout"
            and binding.get("study") == STUDY and binding.get("candidate") == MODE
            and same(binding.get("settings"), SETTINGS) and same(binding.get("gate"), GATE)
            and same(binding.get("retrieval"), list(RETRIEVALS)) and binding.get("repetitions") == 3
            and binding.get("expected_requests") == plan.get("expected_requests") == 252
            and binding.get("expected_warmups") == plan.get("expected_warmups") == 6
            and plan.get("quality_repetition") == 1, "Fixed native conditions/caps/counts differ")
    declared = {"legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
        "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
        MODE: {"selector": "definition_context", "postprocessor": "live_semantic_then_terminal_role_guard"}}
    require(same(binding.get("conditions"), declared)
            and same(binding.get("arms"), {"legacy": "legacy", "coverage_control": "coverage_control", "candidate": MODE}),
            "Fixed actual native arm source paths differ")
    require(set(plan.get("artifacts", {})) == {"source-inventory.json"}
            and plan["artifacts"]["source-inventory.json"] == file_sha(output / "source-inventory.json"),
            "Complete source-only plan artifact closure differs")
    authority = binding["implementation_sha256"]
    require(set(SOURCES) <= set(authority) and set(first["sha256"]) <= set(authority), "Complete native executing source closure required")
    for name, value in authority.items():
        role.verify_blob(root, name, value, ("HEAD",))
    require(Path(__file__).resolve() == root / "scripts/audit_role_native.py", "Executing new independent source authority differs")
    # Reconstruct every observed parent case independently, after its immutable
    # complete public authority has already been checked above.
    qualified_parent = development_parent(root, binding, counter=counter)
    manifest = binding["runtime_manifest"]
    path = (root / manifest["manifest_path"]).resolve()
    require(path.is_relative_to(root) and file_sha(path) == manifest["manifest_sha256"]
            and authority.get(manifest["manifest_path"]) == manifest["manifest_sha256"]
            and same(read(path).get("model_binding"), manifest["model_binding"]), "Bound actual model/runtime manifest differs")
    model = semantic.verify_model_binding(manifest["model_binding"])
    require(same(binding["embedding"], manifest["model_binding"]["metadata"]), "Native actual embedding metadata differs")
    expected_files = {name: value["sha256"] for name, value in manifest["model_binding"]["checkpoint"]["files"].items()
                      if ".cache" not in Path(name).parts}
    require(same(binding["model_files_sha256"], expected_files), "Complete actual checkpoint content binding differs")
    data, fixture, definitions = fresh_fixture(root, corpus, output, binding)
    queries, groups, multi = verify_queries(data, plan, fixture, binding)
    result = {"schema": "role_native_input_audit_v1", "status": "passed", "checks_passed": True,
        "plan_sha256": file_sha(output / "plan.json"), "source_inventory_sha256": file_sha(output / "source-inventory.json"),
        "native_source_freeze": first, "source_sha256": authority, "qualified_development_parent": qualified_parent,
        "source_review_format": binding["source_review_format"], "source_review_author": binding["source_review_author"],
        "source_review_compatibility": binding["source_review_compatibility"],
        "source_only_calls": dict(SOURCE_ONLY_CALLS), "new_native_outcomes_computed": 0,
        "independent_python_inventory": definitions, "execution_commit": git(root, "rev-parse", "HEAD").decode().strip(),
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat()}
    return result, {"plan": plan, "data": data, "fixture": fixture, "queries": queries, "groups": groups,
                    "multi": multi, "model_verification": model}


def verify_plan_freeze(root, output, plan, start, input_receipt):
    binding = plan["binding"]
    bound = {**binding["implementation_sha256"], **binding["development_parent"]["artifacts"],
             **(binding.get("definition_preflight") or {})}
    bound.update({(output / name).relative_to(root).as_posix(): value for name, value in plan["artifacts"].items()})
    for name in ("plan.json", "input-audit.json"):
        bound[(output / name).relative_to(root).as_posix()] = file_sha(output / name)
    freeze, execution = start["prospective_freeze"], start["execution_commit"]
    ancestor(root, binding["native_source_freeze"]["commit"], freeze["commit"])
    ancestor(root, input_receipt["execution_commit"], freeze["commit"])
    ancestor(root, freeze["commit"], execution)
    ancestor(root, execution, "HEAD")
    committed = int(git(root, "show", "-s", "--format=%ct", freeze["commit"]).decode().strip())
    require(same(freeze, {"commit": freeze["commit"], "bound_blob_count": len(bound), "committed_at_unix": committed})
            and committed <= datetime.fromisoformat(start["started_at_utc"]).timestamp(), "Complete native prospective plan chain differs")
    for name, value in bound.items():
        role.verify_blob(root, name, value, (freeze["commit"], execution, "HEAD"))
    return freeze, execution


def successful_work(events):
    terminals = [event for event in events if event["status"] == "completed"]
    return {"started_forward_calls": len(terminals), "completed_forward_calls": len(terminals), "failed_forward_calls": 0,
        "reported_model_forward_calls": len(terminals), "reported_encoded_texts": sum(event["batch_input_count"] for event in terminals),
        "malformed_events": 0, "terminal_coverage_complete": True}


def verify_forward_counts(row):
    query = [{key: value for key, value in event.items() if key != "purpose"} for event in row["external_forward_events"]]
    captured = row.get("candidate_capture")
    receipts = captured.get("provider_receipts", []) if captured else []
    extra = receipts[0].get("extra_query_forward_events", []) if receipts else []
    docs, original, clauses = successful_work([]), successful_work(query), successful_work(extra)
    expected = {"documents": docs, "retrieval_query": original, "extra_queries": clauses,
        "unknown_external_events": 0, "extra_receipts_consistent": True,
        "reported_total_model_forward_calls": original["reported_model_forward_calls"] + clauses["reported_model_forward_calls"],
        "reported_request_document_inference_calls": 0, "receipt_coverage_complete": True,
        "scope": "Reported execution receipts; started events alone do not prove a model forward"}
    require(same(row.get("forward_counts"), expected), "Reported actual native forward ledger counts differ")


def verify_setup(setup, warmups, fixture, counter, metadata, *, root, attempt):
    order = [(arm, mode) for arm in ARMS for mode in RETRIEVALS]
    require([(row["arm"], row["retrieval"]) for row in warmups] == order and setup.get("warmup_calls") == 6,
            "Exactly six fixed arm/mode warmups required")
    query = "Find code that handles input and returns a result"
    for row in warmups:
        require(row["query_id"] == "__generic_warmup__" and row["repetition"] == 0 and row["kind"] == "absent"
                and row["multi_file"] is False and row["candidate_ranks"] == row["delivered_ranks"] == [],
                "Warmups cannot probe authored quality labels")
        verify_trace(row, fixture, counter, query=query, scope=row["response"]["scope"], model_metadata=metadata)
        verify_forward_counts(row)
    calls = setup.get("document_calls")
    require(type(calls) is list and bool(calls) and type(setup.get("document_api_calls")) is int
            and setup["document_api_calls"] == len(calls), "Actual new-index document API ledger required")
    documents = []
    for item in fixture["inventory"].values():
        text = context.document_text(native.full_rows([{"snippet": item, "score": 0.0}])[0])
        if text not in documents:
            documents.append(text)
    observed, external, forwards = [], [], 0
    for call in calls:
        require(call.get("status") == "completed" and type(call.get("texts")) is list, "Index document API failed or incomplete")
        counts = semantic.verify_forward_events(call["forward_events"], call["texts"], metadata)
        observed.extend(call["texts"])
        external.extend({"purpose": "documents", **event} for event in call["forward_events"])
        forwards += counts["model_forward_calls"]
    require(same(observed, documents) and same(setup.get("external_forward_events"), external),
            "Cold index document work differs from complete unique source documents")
    require(setup.get("query_api_calls") == 6
            and setup.get("clause_api_calls") == sum(row["clause_query_calls"] for row in warmups)
            and setup.get("clause_encoded_texts") == sum(row["clause_query_texts"] for row in warmups), "Setup query/clause work differs")
    expected_db = Path(root) / ".cache/indexes" / ("role-native-" + sha(attempt.relative_to(root).as_posix().encode())[:20] + ".sqlite3")
    require(Path(setup["db_path"]).resolve() == expected_db.resolve(), "Index is not the exclusively owned native attempt database")
    require(type(setup.get("index_model_and_explicit_warmup_s")) is float
            and math.isfinite(setup["index_model_and_explicit_warmup_s"]) and setup["index_model_and_explicit_warmup_s"] > 0,
            "Actual source index/model/warmup setup duration differs")
    return {"warmups": 6, "document_api_calls": len(calls), "document_model_forwards": forwards,
            "unique_document_texts": len(documents), "clause_api_calls": setup["clause_api_calls"],
            "clause_encoded_texts": setup["clause_encoded_texts"]}


def audit_native(root, corpus, output, counter):
    root, output = Path(root).resolve(), Path(output).resolve()
    input_result, prepared = audit_inputs(root, corpus, output, counter)
    plan, binding = prepared["plan"], prepared["plan"]["binding"]
    input_receipt = read(output / "input-audit.json")
    # Audit execution/date can change; every substantive input proof must match.
    for name, value in input_result.items():
        if name not in {"execution_commit", "reviewed_at_utc"}:
            require(same(input_receipt.get(name), value), "Frozen independent source-only barrier differs: " + name)
    attempt = output / "attempt"
    require(not any((attempt / name).exists() for name in ("failure.json", "request-failure.json", "setup-partial.json")),
            "Failed/partial native attempts cannot qualify")
    start, summary, setup = (read(attempt / name) for name in ("run-started.json", "summary.json", "setup.json"))
    artifacts = summary["artifact_sha256"]
    expected = {"run-started.json", "setup.json", "warmups.jsonl.gz", "searches.jsonl.gz", "gpu-lease-acquired.json",
                "model-closed.json", "gpu-lease-released.json"}
    require(set(artifacts) == expected and {path.name for path in attempt.iterdir() if path.is_file()} == expected | {"summary.json"},
            "Complete successful native artifact closure required")
    for name, value in artifacts.items():
        require(file_sha(attempt / name) == value, "Finalized native artifact bytes differ: " + name)
    chain, execution = verify_plan_freeze(root, output, plan, start, input_receipt)
    require(start.get("schema") == STUDY and start.get("candidate") == MODE and start.get("resume_allowed") is False
            and start.get("expected_measured_calls") == 252 and start.get("expected_warmups") == 6
            and start.get("plan_sha256") == summary.get("plan_sha256") == file_sha(output / "plan.json")
            and same(start.get("native_source_freeze"), binding["native_source_freeze"]), "Native actual start/source/prospective binding differs")
    fixture, metadata = prepared["fixture"], binding["embedding"]
    warmups, rows = jsonl(attempt / "warmups.jsonl.gz"), jsonl(attempt / "searches.jsonl.gz")
    setup_counts = verify_setup(setup, warmups, fixture, counter, metadata, root=root, attempt=attempt)
    require(len(rows) == 252, "All 252 native requests required")
    verify_repeats(rows, prepared["data"]["queries"])
    scope = native.native_scope(prepared["data"])
    indexed = {(row["query_id"], row["retrieval"], row["repetition"], row["arm"]): row for row in rows}
    diagnostics, activity = [], []
    for row in rows:
        query = prepared["queries"][row["query_id"]]
        require(row["kind"] == query["kind"] and row["multi_file"] is prepared["multi"][row["query_id"]]
                and row["condition"] == (MODE if row["arm"] == "candidate" else row["arm"]), "Native original query/arm reporting identity differs")
        value = verify_trace(row, fixture, counter, query=query["query"], scope=scope, model_metadata=metadata)
        verify_forward_counts(row)
        if value["diagnostic_normalization"] is not None:
            diagnostics.append({"query_id": row["query_id"], "repetition": row["repetition"], "retrieval": row["retrieval"],
                                **value["diagnostic_normalization"]})
        activity.append(value)
        gold = prepared["groups"][row["query_id"]]
        raw = [{**pair["snippet"], "source": pair["snippet"]["text"]} for pair in row["stages"]["ranked"]]
        require(same(row["candidate_ranks"], native.strict_ranks(raw, gold))
                and same(row["delivered_ranks"], native.strict_ranks(row["response"]["results"], gold)), "Native full-symbol/literal evidence scoring differs")
        if row["arm"] == "candidate":
            control = indexed[(row["query_id"], row["retrieval"], row["repetition"], "coverage_control")]
            reference = row["candidate_capture"]["reference_response"]
            require(same(source_view(reference), source_view(control["response"]))
                    and context.compact_payload(reference) == control["compact_payload"], "Actual candidate coverage operand differs from separately measured control")
            require(all(Path(read["index_path"]).resolve() == Path(setup["db_path"]).resolve() for read in row["resident_reads"]),
                    "Measured provider read another index")
    metrics = native_metrics(rows)
    require(summary.get("schema") == STUDY and summary.get("complete") is True and summary.get("candidate") == MODE
            and summary.get("classification") == "repository_holdout" and summary.get("quality_repetition") == 1
            and summary.get("requests") == summary.get("measured_query_embeddings") == 252 and summary.get("unique_queries") == 14
            and summary.get("measured_document_embedding_calls") == 0
            and summary.get("measured_clause_query_calls") == sum(row["clause_query_calls"] for row in rows)
            and summary.get("measured_clause_encoded_texts") == sum(row["clause_query_texts"] for row in rows)
            and same(summary.get("gate"), GATE) and same(summary.get("metrics"), metrics) and same(summary.get("setup"), setup)
            and summary.get("ranked_repeat_changes") == summary.get("compact_repeat_changes") == summary.get("query_vector_repeat_changes") == 0,
            "Independent actual native work/quality/warm-latency metrics differ")
    acquired, closed, released = (read(attempt / name) for name in ("gpu-lease-acquired.json", "model-closed.json", "gpu-lease-released.json"))
    require(same({key: value for key, value in acquired.items() if key != "released_at"},
                 {key: value for key, value in released.items() if key != "released_at"})
            and acquired.get("released_at") is None and type(acquired.get("pid")) is int and acquired["pid"] > 0,
            "Exclusive actual GPU lease identity differs")
    common = Path(git(root, "rev-parse", "--git-common-dir").decode().strip())
    if not common.is_absolute():
        common = root / common
    require(Path(acquired["path"]).resolve() == common.resolve() / "semantic-search-gpu.lock", "Model lease is not shared across worktrees")
    ordered = [*warmups, *rows]
    verify_resident_stability(ordered)
    require(datetime.fromisoformat(start["started_at_utc"]) <= datetime.fromisoformat(acquired["acquired_at"])
            <= datetime.fromisoformat(ordered[0]["request_clock"]["started_at_utc"])
            and all(datetime.fromisoformat(a["request_clock"]["ended_at_utc"]) <= datetime.fromisoformat(b["request_clock"]["started_at_utc"])
                    for a, b in zip(ordered, ordered[1:]))
            and datetime.fromisoformat(ordered[-1]["request_clock"]["ended_at_utc"]) <= datetime.fromisoformat(closed["closed_at"])
            <= datetime.fromisoformat(released["released_at"]), "Native setup/measured/model-close/lease chronology differs")
    for state in (setup.get("machine_state"), summary.get("post_run_machine_state")):
        require(type(state) is dict and type(state.get("power")) is str and bool(state["power"])
                and state.get("pid") == acquired["pid"] and state.get("platform") == binding["runtime_manifest"]["model_binding"]["runtime"]["platform"]
                and type(state.get("machine")) is str and bool(state["machine"]) and type(state.get("python")) is str,
                "Actual power/machine/runtime state receipt required")
    require(datetime.fromisoformat(warmups[-1]["request_clock"]["ended_at_utc"])
            <= datetime.fromisoformat(setup["machine_state"]["recorded_at_utc"])
            <= datetime.fromisoformat(rows[0]["request_clock"]["started_at_utc"])
            and datetime.fromisoformat(released["released_at"])
            <= datetime.fromisoformat(summary["post_run_machine_state"]["recorded_at_utc"]), "Power/machine observation chronology differs")
    require(all(file_sha(attempt / name) == value for name, value in artifacts.items()), "Native artifacts changed during audit")
    return {"schema": SCHEMA, "status": "passed", "checks_passed": True, "study": STUDY,
        "verified_requests": 252, "verified_warmups": 6, "unique_queries": 14,
        "verified_source_windows": len(fixture["inventory"]), "artifact_sha256": artifacts,
        "producer_freeze": chain, "producer_execution": execution, "qualified_development_parent": input_result["qualified_development_parent"],
        "source_review_compatibility": input_result["source_review_compatibility"],
        "independently_recomputed_metrics": metrics, "independent_python_inventory": input_result["independent_python_inventory"],
        "setup_forward_summary": setup_counts, "measured_native_forward_calls": 252 + sum(value["extra_model_forwards"] for value in activity),
        "measured_extra_clause_texts": sum(value["extra_texts"] for value in activity),
        "inactive_diagnostic_wording_normalizations": diagnostics, "normalization_count": len(diagnostics),
        "diagnostic_policy": "Only copied exact published None-feature message pair; every other receipt/decision/source/payload field strict",
        "native_warm_latency_measured": True, "automatic_default_change": False,
        "calls": {"model": 0, "index": 0, "retrieval": 0, "network": 0},
        "trust_boundary": "Frozen conservative graph only shared; native source, packing, semantic proposal, terminal role, forwards, original query and resident vector provenance independently checked"}


def forbidden_module(name):
    return native.forbidden_module(name) or any(name == prefix or name.startswith(prefix + ".") for prefix in
        ("benchmark_role_native", "research_role_context", "run_role_context", "semantic_model_study", "semantic_study_inputs",
         "capture_semantic_request", "jax")) or name.startswith("benchmark_")


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def preflight_authority(root, folder):
    """Check the immutable parent and source-only freeze before fresh hashing."""
    plan = read(folder / "plan.json")
    parent = development_parent(root, plan["binding"])
    first_source_freeze(root, plan["binding"], before=plan["frozen_at_utc"])
    names = plan["binding"]["implementation_sha256"]
    require(set(SOURCES) <= set(names), "Native auditor/tests/protocol closure must be frozen")
    before = {name: file_sha(root / name) for name in names}
    require(same(before, names), "Native source authority differs before auditing")
    return parent, names, before


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--check-inputs", action="store_true")
    parser.add_argument("--tokenizer-cache", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root, folder = args.root.resolve(), args.results.resolve()
    target = args.output.resolve() if args.output else folder / ("input-audit.json" if args.check_inputs else "independent-audit.json")
    failure = target.with_name(target.stem + "-failure.json")
    require(not target.exists() and not failure.exists(), "Preserve independent native audit attempts; use an exclusive new output")
    require(Path(__file__).resolve() == root / "scripts/audit_role_native.py", "Executing native auditor source authority differs")
    require(not any(forbidden_module(name) for name in sys.modules), "Independent native role audit requires a clean CPU process")
    before = {}

    class Guard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if forbidden_module(fullname):
                raise RuntimeError("Independent native role audit forbids producer/model imports: " + fullname)

    def deny(*values, **kwargs):
        raise RuntimeError("Independent native role audit forbids network")

    guard = Guard()
    sys.meta_path.insert(0, guard)
    sys.path.insert(0, str(root / "src"))
    try:
        with patch.object(socket, "create_connection", deny), patch.object(socket, "getaddrinfo", deny), \
             patch.object(socket.socket, "connect", deny), patch.object(socket.socket, "connect_ex", deny), \
             patch.object(socket.socket, "sendto", deny):
            parent, names, before = preflight_authority(root, folder)
            # The original complete published parent is checked before these
            # tokenizer bytes or any fresh source/checkpoint file is opened.
            counter = win.offline_encoding(args.tokenizer_cache or root / ".cache/tokenizers")
            if args.check_inputs:
                result, _ = audit_inputs(root, args.corpus.resolve(), folder, counter, parent=parent)
            else:
                result = audit_native(root, args.corpus.resolve(), folder, counter)
        require(all(file_sha(root / name) == value for name, value in before.items()), "Native source changed during independent audit")
        for name, module in tuple(sys.modules.items()):
            if name.startswith("audit_") or name == "complementary_inputs":
                location = getattr(module, "__file__", None)
                require(location is not None and Path(location).resolve().is_relative_to(root / "scripts"),
                        "Executing independent helper source escapes native root")
                relative = Path(location).resolve().relative_to(root).as_posix()
                require(relative in names and file_sha(location) == names[relative], "Executing independent helper lacks native source binding")
        if not args.check_inputs:
            result.update(auditor_execution_commit=git(root, "rev-parse", "HEAD").decode().strip(),
                auditor_sha256={name: value for name, value in names.items() if name.startswith("scripts/audit_") or name in SOURCES},
                reviewed_at_utc=datetime.now(timezone.utc).isoformat(),
                candidate_planner_or_producer_modules_imported=False,
                allowed_shared_source_graph_modules=sorted(name for name in source.ALLOWED_GRAPH_MODULES if name in sys.modules))
        write_new(target, result)
        print(json.dumps({"checks_passed": True, "audit": str(target), "sha256": file_sha(target)}))
    except BaseException as error:
        write_new(failure, {"schema": SCHEMA, "status": "failed", "error_type": type(error).__name__, "error": str(error),
            "resume_allowed": False, "source_sha256": before,
            "calls": {"model": 0, "index": 0, "retrieval": 0, "network": 0}})
        raise
    finally:
        sys.meta_path.remove(guard)
        sys.path.remove(str(root / "src"))


if __name__ == "__main__":
    main()
