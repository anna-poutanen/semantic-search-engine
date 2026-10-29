#!/usr/bin/env python3
"""Independent CPU audit of a frozen shared-flow native holdout.

The conservative source graph extractor is the only shared production code.
Selection, static-dependency allocation, flow planning, packing, guards, source
scoring, call accounting and qualification are reconstructed without a backend,
producer, selector, embedding implementation or numerical runtime import.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import importlib.abc
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import statistics
import struct
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("flow_native_independent_development", ROOT / "scripts/audit_flow_allocation.py")
flow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flow)
source, win, old = flow.base, flow.win, flow.old
require, sha, read, canonical, file_sha, git_sha = source.require, source.sha, source.read, source.canonical, source.file_sha, source.git_sha
ARMS, RETRIEVALS, CANDIDATE = ("legacy", "coverage_control", "candidate"), ("dense", "hybrid"), "shared_flow"
SETTINGS = dict(source.SETTINGS)
SOURCE_FIELDS = ("query", "scope", "match_status", "results", "total_source_chars", "budget_exhausted", "token_usage")
CALLS = ("flow_planner_calls", "base_planner_calls", "guard_calls", "reference_calls")
GATE = {"minimum_net_multi_file_complete_gain": 1, "maximum_lost_groups": 0,
    "maximum_lost_complete_positives": 0, "maximum_lost_single_file_first_hits": 0,
    "maximum_median_wall_ratio": 1.25, "promotion_mode": "dense",
    "hybrid_role": "separate diagnostic control; cannot rescue dense",
    "preservation_controls": ["legacy", "coverage_control"], "incremental_gain_control": "coverage_control"}


def same_json(left, right):
    return canonical(left) == canonical(right)


def context_view(response):
    """Operational backend timings/indexstats are deliberately outside this view."""
    if response is None:
        return None
    result = {field: deepcopy(response[field]) for field in SOURCE_FIELDS}
    if "_selection_backfill" in response:
        result["_selection_backfill"] = deepcopy(response["_selection_backfill"])
    return result


def canonical_ambiguities(keys):
    labels = {}
    for value in keys.values():
        labels.setdefault(tuple(value[:2]), set()).add(tuple(value))
    return {label for label, values in labels.items() if len(values) > 1}


def baseline(ranked, inventory, keys, edges, *, canonical_definitions, max_results=8, dependencies=True):
    """Reconstruct both native controls without executing production selectors.

    The graph's original target order matters. Only its first two targets are
    considered, even when a target is already included. Each dependency uses its
    earliest source window and inherits the actual anchor scalar and owner.
    """
    def identity(item):
        return source.definition_id(item, keys) if canonical_definitions else (item["path"], item["symbol"])
    seen, selected = set(), []
    for pair in ranked:
        key = identity(pair["snippet"])
        if key not in seen:
            seen.add(key)
            selected.append(deepcopy(pair))
    eligible = selected[:max_results]
    origins = {pair["snippet"]["id"]: ["retrieval", None] for pair in eligible}
    if not dependencies:
        return selected, deepcopy(eligible), origins
    windows = {}
    for item in inventory.values():
        windows.setdefault((item["path"], item["symbol"]), []).append(item)
    for values in windows.values():
        values.sort(key=lambda item: (item["start_line"], item["id"]))
    ambiguous = canonical_ambiguities(keys) if canonical_definitions else set()
    anchors = eligible[:min(5, max_results)]
    expanded = deepcopy(anchors)
    included = {identity(pair["snippet"]) for pair in expanded}
    for anchor in anchors:
        if len(expanded) >= max_results:
            break
        owner = anchor["snippet"]
        label = owner["path"], owner["symbol"]
        if label in ambiguous:
            continue
        targets = [target for target in edges.get(label, ()) if windows.get(target)][:2]
        for target in targets:
            if target in ambiguous:
                continue
            related = windows[target][0]
            key = identity(related)
            if key not in included and len(expanded) < max_results:
                expanded.append({"snippet": deepcopy(related), "score": anchor["score"]})
                origins[related["id"]] = ["dependency", owner["id"]]
                included.add(key)
    for pair in eligible:
        key = identity(pair["snippet"])
        if key not in included and len(expanded) < max_results:
            expanded.append(deepcopy(pair))
            included.add(key)
    return selected, expanded, origins


def native_template(query, scope, ranked):
    return {"query": query, "scope": deepcopy(scope), "match_status": "candidates" if ranked else "no_candidates",
            "results": [{"qwen_score": None}]}


def full_rows(pairs):
    output = []
    for pair in pairs:
        item, score = pair["snippet"], pair["score"]
        output.append({"id": item["id"], "path": item["path"], "symbol": item["symbol"],
            "start_line": item["start_line"], "end_line": item["end_line"], "window_end_line": item["end_line"],
            "source": item["text"], "content_hash": item["content_hash"], "source_hash": item["content_hash"],
            "retrieval_score": float(score), "h2o_score": None, "qwen_score": None,
            "origin": "retrieval", "related_to": None, "truncated": False})
    return output


def verify_trace(row, fixture, counter, *, query, scope):
    stages, keys, inventory = row["stages"], fixture["definition_keys"], fixture["inventory"]
    ranked = stages["ranked"]
    raw = win.raw_ranked(ranked, inventory)
    require(len(ranked) <= 50, "Native original raw candidate cap exceeded")
    expected_ambiguities = [list(label) for label in sorted(canonical_ambiguities(keys))]
    require(same_json(stages["definition_keys"], keys) and same_json(stages["ambiguous_symbols"], expected_ambiguities),
            "Native complete canonical identities or ambiguous labels changed")
    selected, eligible, origins = baseline(ranked, inventory, keys, fixture["graph_edges"],
        canonical_definitions=row["arm"] != "legacy")
    canonical_selected, canonical_eligible, canonical_origins = baseline(ranked, inventory, keys, fixture["graph_edges"],
        canonical_definitions=True)
    expected_flow = None
    if row["arm"] == "candidate":
        selected, eligible, origins, expected_flow = flow.planner(ranked, canonical_selected, canonical_eligible,
            canonical_origins, keys, fixture["graph_edges"], expected_ambiguities, query=query)
        require(same_json(row["flow_receipt"], expected_flow)
                and same_json(row["base_receipt"], expected_flow["base_plan"]),
                "Native flow/base plan differs from independently reconstructed original baseline")
        require(same_json(row["selection_plan"], expected_flow["guard_plan"])
                and same_json(row["actual_guard_plan"], expected_flow["guard_plan"]),
                "Native actual guard did not receive its truthful flow projection")
    else:
        require(all(row.get(field) is None for field in
            ("flow_receipt", "base_receipt", "selection_plan", "actual_guard_plan", "source_guard_receipt")),
            "Native control contains candidate planning or guard receipts")
    expected_calls = int(row["arm"] == "candidate")
    require(all(type(row[field]) is int and row[field] == expected_calls for field in CALLS),
            "Native actual flow/base/guard/reference counts differ")
    require(same_json(stages["selected"], selected) and same_json(stages["eligible"], eligible)
            and same_json(stages["origins"], origins), "Native selected/eligible scalar/order/origin differs from independent allocation")
    for name in ("selected", "eligible"):
        require(not win.verify_stage(stages[name], inventory, fixture["sources"], keys, raw),
                "Shared flow may not invent composite or rewritten source")
    require(same_json(row["backfill_candidates"], full_rows(selected))
            and same_json(row["ranked_candidates"], full_rows(ranked)),
            "Native complete drained candidate rows changed source, order, scores or provenance")
    template = native_template(query, scope, ranked)
    before = source.ordinary_pack(template, eligible, origins, counter)
    require(same_json(context_view(row["pre_postprocess_response"]), before),
            "Native maximal character/token prefix packing differs")
    if row["arm"] == "legacy":
        expected = before
    else:
        proposal = source.coalesce_pack(before, full_rows(selected), counter)
        expected = proposal
        if row["arm"] == "candidate":
            reference_before = source.ordinary_pack(template, canonical_eligible, canonical_origins, counter)
            reference = source.coalesce_pack(reference_before, full_rows(canonical_selected), counter)
            require(same_json(context_view(row["reference_response"]), reference)
                    and same_json(context_view(row["proposal_response"]), proposal),
                    "Native real lazy reference or exact containment/backfill proposal differs")
            expected, guard_receipt = source.guard(reference, proposal, expected_flow["guard_plan"])
            require(same_json(row["source_guard_receipt"], guard_receipt), "Native actual protected-source guard differs")
    if row["arm"] != "candidate":
        require(row["reference_response"] is None and row["proposal_response"] is None,
                "Native controls unexpectedly packed a lazy reference or candidate proposal")
    require(same_json(context_view(row["response"]), expected),
            "Native final source choice differs from exact accepted proposal/reference fallback")
    for name in ("pre_postprocess_response", "response", "reference_response", "proposal_response"):
        value = row.get(name)
        if value is None:
            continue
        win.verify_response(value, inventory, fixture["sources"], keys, raw, counter,
                            raw_rows={item["id"]: item for item in row["ranked_candidates"]})
        require(value["query"] == query and value["scope"] == scope
                and all(not any(field in item for field in ("source_origin", "buffer_version", "buffer_hash"))
                        for item in value["results"]), "Native saved-source scope/provenance changed")
    payload = win.compact_payload(row["response"])
    require(row["compact_payload"] == payload and row["compact_sha256"] == sha(payload.encode())
            and row["actual_compact_tokens"] == row["response"]["token_usage"]["tokens"],
            "Native actual compact payload/hash/token receipt differs")
    require(type(row["query_embedding_calls"]) is int and row["query_embedding_calls"] == 1
            and type(row["document_embedding_calls"]) is int and row["document_embedding_calls"] == 0,
            "Measured native request requires one actual query embedding and no document work")
    return expected_flow


def vector_receipt(row, *, dimensions=1024):
    vector = row["query_vector"]
    require(isinstance(vector, list) and len(vector) == dimensions and all(win.finite(value) for value in vector),
            "Actual native query vector has wrong dimensions or nonfinite components")
    require(abs(sum(value * value for value in vector) - 1) <= .001, "Actual native query vector is not L2 normalized")
    require(sha(struct.pack("<" + "f" * len(vector), *vector)) == row["query_vector_sha256"],
            "Actual native float32 query vector hash differs")
    return row["query_vector_sha256"]


def expected_schedule(queries):
    output = []
    modes = [(arm, retrieval) for retrieval in RETRIEVALS for arm in ARMS]
    for repetition in range(1, 4):
        for index, query in enumerate(queries):
            offset = (index + repetition - 1) % len(modes)
            output.extend((repetition, query["id"], arm, retrieval) for arm, retrieval in modes[offset:] + modes[:offset])
    return output


def verify_repeats(rows, queries):
    actual = [(row["repetition"], row["query_id"], row["arm"], row["retrieval"]) for row in rows]
    require(actual == expected_schedule(queries), "Exact252 rotated measured native schedule differs")
    vectors, ranked, payloads = {}, {}, {}
    for row in rows:
        require(win.finite(row["elapsed_backend_s"]) and row["elapsed_backend_s"] > 0,
                "Native actual monotonic wall duration must be finite and positive")
        fingerprint = vector_receipt(row)
        require(vectors.setdefault(row["query_id"], fingerprint) == fingerprint,
                "Actual query vector changed across native arms/modes/repetitions")
        rank_key = row["query_id"], row["retrieval"]
        rank_hash = sha(canonical(row["stages"]["ranked"]))
        require(ranked.setdefault(rank_key, rank_hash) == rank_hash, "Original native raw order/scalars changed across arms/repetitions")
        payload_key = *rank_key, row["arm"]
        require(payloads.setdefault(payload_key, row["compact_sha256"]) == row["compact_sha256"],
                "Actual native compact payload changed across repetitions")


def strict_ranks(rows, groups):
    return win.evidence_ranks(rows, groups, qualified=True)


def parent_proof(root, binding):
    parent = binding["development_parent"]
    require(parent["study"] == "source_flow", "Native validation requires its own shared-flow development parent")
    for name, digest in parent["artifacts"].items():
        require(file_sha(root / name) == git_sha(root, "HEAD", name) == digest,
                "Qualified development artifact/code is changed or uncommitted: " + name)
    folders = {str(Path(name).parent) for name in parent["artifacts"] if name.endswith("/independent-audit.json")}
    matching = [Path(folder) for folder in folders if read(root / folder / "independent-audit.json").get("study") == "source_flow"]
    require(len(matching) == 1, "Exactly one independently audited shared-flow parent required")
    folder = matching[0]
    plan, start, audit, summary = (read(root / folder / name) for name in
        ("frozen-plan.json", "run-start.json", "independent-audit.json", "summary.json"))
    qualification = summary["qualification"].get(CANDIDATE)
    gained = qualification["additional_distinct_complete_queries_vs_coverage_control"]
    require(qualification == parent["qualification"] and qualification["qualified"] is True
            and isinstance(gained, list) and len(gained) == len(set(gained)) >= 1
            and qualification["gained_additional_distinct_complete_queries"] == len(gained)
            and set(qualification["against_controls"]) == set(ARMS[:2])
            and all(set(loss) == {"lost_groups", "lost_complete_rows", "lost_single_file_first_hits"}
                    and all(type(value) is int and value == 0 for value in loss.values())
                    for loss in qualification["against_controls"].values()), "Native candidate lacks incremental development qualification against BOTH controls")
    require(audit["status"] == "passed" and audit["checks_passed"] is True and audit["study"] == "source_flow"
            and audit["candidate_planner_or_producer_modules_imported"] is False
            and audit["verified_outcome_rows"] == 365 and audit["exact_legacy_controls"] == 365
            and audit["exact_prior_coverage_controls"] == 337 and audit["referenced_control_rows"] == 730
            and audit["referenced_negative_rows"] == 1460 and audit["independently_recomputed_summary"] == summary
            and all(type(audit.get("calls", {}).get(name)) is int and audit["calls"][name] == 0
                    for name in ("model", "retrieval", "index", "network")),
            "Shared-flow development parent needs its actual independent full-matrix audit")
    require(parent["execution_commit"] == start["execution_commit"] == audit["producer_execution"]
            and parent["freeze"] == start["prospective_freeze"] == audit["producer_freeze"],
            "Qualified development original freeze/execution identities differ")
    for name, digest in audit["artifact_sha256"].items():
        require(parent["artifacts"].get((folder / name).as_posix()) == digest,
                "Qualified development audited artifact omitted or substituted")
    require(summary["plan_sha256"] == file_sha(root / folder / "frozen-plan.json")
            and summary["replay_sha256"] == file_sha(root / folder / "replay.jsonl.gz"),
            "Qualified development raw/plan bindings differ")
    for name, digest in {**plan["producer_sha256"], **audit["auditor_sha256"]}.items():
        require(parent["artifacts"].get(name) == digest, "Qualified parent's exact producer/auditor source binding omitted")
    require(all(type(summary[name]) is int and summary[name] == 0 for name in
                ("model_calls", "retrieval_calls", "network_calls")), "Qualified parent performed model/retrieval/network work")
    # Original inherited/re-written history is literal proof, never producer execution.
    window_proof = source.historical_proof(root, plan)
    negative_proof = source.parent_allocation_proof(root, plan)
    require(window_proof == read(root / folder / "historical-provenance.json")
            and negative_proof == read(root / folder / "negative-provenance.json"),
            "Qualified parent's original rewritten window/allocation history proof differs")
    source_proof = flow.source_parent_proof(root, plan)
    expected_source_proof = read(root / folder / "source-negative-provenance.json")
    require(source_proof == expected_source_proof, "Qualified parent's original historical proof differs")
    chain, execution = flow.new_freeze(root, root / folder, plan, start)
    require(chain == parent["freeze"] and execution == parent["execution_commit"], "Qualified development strict prospective chain differs")
    return {"path": folder.as_posix(), "producer_freeze": chain, "producer_execution": execution,
            "qualified": True, "qualification": qualification, "original_source_historical_proof": source_proof,
            "original_window_historical_proof": window_proof, "original_allocation_historical_proof": negative_proof}


def native_freeze(root, output, plan, start, artifacts):
    binding = plan["binding"]
    required_code = {"scripts/audit_flow_native.py", "tests/test_flow_native_audit.py",
        "scripts/benchmark_flow_allocation.py", "tests/test_flow_allocation_benchmark.py",
        "docs/flow-allocation-native-protocol.md", "src/semantic_search/flow_allocation.py", *source.GRAPH_SOURCES}
    require(required_code <= set(binding["implementation_sha256"]),
            "Commit native producer/auditor/tests/protocol/graph bindings before any inference")
    bound = {**binding["implementation_sha256"], **binding["development_parent"]["artifacts"],
             **(binding.get("definition_preflight") or {}),
             **{(output / name).relative_to(root).as_posix(): digest for name, digest in plan["artifacts"].items()},
             (output / "plan.json").relative_to(root).as_posix(): artifacts["plan.json"]}
    for name, digest in bound.items():
        require(file_sha(root / name) == digest, "Frozen native source/input bytes changed: " + name)
    chain = old.committed_freeze(root, output / "plan.json", bound, start["started_at_utc"])
    execution = start["execution_commit"]
    for older, newer in ((chain["commit"], execution), (execution, "HEAD")):
        subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", older, newer])
    for name, digest in bound.items():
        require(git_sha(root, execution, name) == digest, "Native execution Git blob differs: " + name)
    return chain, execution


def holdout_fixture(root, corpus, output, binding):
    data = read(root / binding["dataset_path"])
    require(sha((root / binding["dataset_path"]).read_bytes()) == binding["dataset_sha256"], "Native authored dataset hash")
    require(subprocess.check_output(["git", "-C", str(corpus), "rev-parse", "HEAD"], text=True).strip()
            == data["revision"] == binding["revision"] and not subprocess.check_output(
                ["git", "-C", str(corpus), "status", "--porcelain=v1"], text=True).strip(), "Clean pinned fresh corpus")
    require(binding["source_files"] == data["source_files"]
            and sha(old.canonical(binding["source_files"])) == data["source_inventory_sha256"], "Complete authored source membership")
    for path, metadata in {**data["source_files"], **data["excluded_supported_files"]}.items():
        absolute = (corpus / path).resolve()
        require(absolute.is_relative_to(corpus.resolve()), "Native source inventory escapes corpus")
        raw = absolute.read_bytes()
        require(len(raw) == metadata["bytes"] and sha(raw) == metadata["sha256"], "Included/excluded pinned source bytes")
    inventory_doc = read(output / "source-inventory.json")
    inventory_rows = inventory_doc["inventory"]
    require(sha(json.dumps(inventory_rows, sort_keys=True).encode()) == binding["inventory_sha256"]
            and sha(old.canonical(inventory_doc["definition_keys"])) == binding["definition_keys_sha256"], "Native full source/definition inventory binding")
    fixture = old.source_fixture(corpus.parent, corpus.name, binding["source_sha256"], inventory_rows)
    fixture["definition_keys"] = inventory_doc["definition_keys"]
    win.verify_inventory(fixture["inventory"], fixture["sources"], fixture["definition_keys"])
    review = read(root / binding["source_review_path"])
    require(sha((root / binding["source_review_path"]).read_bytes()) == binding["source_review_sha256"]
            and review["status"] == "passed_source_review_before_holdout_retrieval"
            and review["dataset"]["sha256"] == binding["dataset_sha256"]
            and review["protocol"]["sha256"] == sha((root / data["source_review_protocol"]).read_bytes())
            and all(review["calls"][name] == 0 for name in
                    ("model", "index", "retrieval", "selector", "postprocessor", "rule_activation", "gold_coverage_scoring")),
            "Fresh source-only independent review binding")
    preflight_paths = binding["definition_preflight"]
    require(preflight_paths and all(sha((root / path).read_bytes()) == digest for path, digest in preflight_paths.items()),
            "Committed complete source-only definition preflight changed")
    preflight = next((read(root / path) for path in preflight_paths if path.endswith("/definition-inventory.json")), None)
    require(preflight is not None and preflight["status"] == "passed_source_only_definition_inventory"
            and preflight["inventory"] == inventory_rows and preflight["definition_keys"] == fixture["definition_keys"]
            and preflight["dataset"]["sha256"] == binding["dataset_sha256"]
            and preflight["source_files"] == binding["source_files"] and all(value == 0 for value in preflight["calls"].values()),
            "Native extraction differs from pre-outcome complete canonical map")
    for identifier, check in preflight["independent_ast_boundary_checks"].items():
        item, key = fixture["inventory"][identifier], fixture["definition_keys"][identifier]
        raw = fixture["sources"][item["path"]].encode()
        low, high = check["node_byte_bounds"]
        require(low == key[4] and 0 <= low < high <= len(raw)
                and sha(raw[low:high]) == check["node_sha256"]
                and check["definition_lines"] == key[2:4]
                and check["exact_original_grammar_node_exists"] is True,
                "Pre-outcome independently checked original AST node/span binding")
    graph_rows = inventory_doc["graph_edges"]
    require(sha(old.canonical(graph_rows)) == binding["graph_sha256"], "Native complete static graph binding differs")
    fixture["graph_edges"] = source.verify_graph_records(graph_rows, fixture["sources"], fixture["inventory"])
    return data, fixture


def native_scope(data):
    excluded = data.get("source_exclusions", [])
    shown = [pattern for pattern in excluded if len(pattern) <= 80][:4]
    return {"source_root": data["source_root"],
        "membership": "saved implementations; Git-tracked files only in Git projects",
        "source_exclusions": shown, "exclusions_omitted": len(excluded) - len(shown)}


def verify_warmups(setup):
    expected = [(condition, retrieval) for condition in ("legacy", "coverage_control", CANDIDATE) for retrieval in RETRIEVALS]
    rows = setup["warmups"]
    require([(row["condition"], row["retrieval"]) for row in rows] == expected,
            "Exactly six explicit native arm/mode warmups required")
    require(all(row["query"] == "Find code that handles input and returns a result" for row in rows),
            "Native warmup query changed")
    require(all(type(row["query_embedding_calls"]) is int and row["query_embedding_calls"] == 1
                and type(row["document_embedding_calls"]) is int and row["document_embedding_calls"] >= 0
                for row in rows), "Native warmup actual query/document counters differ")
    require(all(row["document_embedding_calls"] == 0 for row in rows[1:]),
            "Native warmup repeated document work after initial index construction")
    require(setup["query_calls"] == 6 and setup["document_calls"] == sum(row["document_embedding_calls"] for row in rows),
            "Native setup counter totals differ from actual warmup deltas")
    require(win.finite(setup["index_model_and_explicit_warmup_s"]) and setup["index_model_and_explicit_warmup_s"] > 0,
            "Native setup duration must be positive and finite")


def native_metrics(rows):
    result = win.native_summary(rows)
    for retrieval, value in result["per_retrieval"].items():
        mode = [row for row in rows if row["retrieval"] == retrieval]
        for metric in value["arms"].values():
            metric.pop("delivered_composites")
            metric.pop("active_composite_calls")
        value.pop("active_composite_overhead")
        candidates = [row for row in mode if row["arm"] == "candidate"]
        controls = {arm: {(row["query_id"], row["repetition"]): row for row in mode if row["arm"] == arm}
                    for arm in ARMS[:2]}
        value["flow_activity"] = {
            "clause_activated_calls": sum(row["flow_receipt"]["base_plan"]["activated"] for row in candidates),
            "admitted_calls": sum(row["flow_receipt"]["accepted"] for row in candidates),
            "guard_accepted_calls": sum(row["source_guard_receipt"]["accepted"] for row in candidates),
            "compact_changed_calls": sum(row["compact_sha256"] != controls["coverage_control"][(row["query_id"], row["repetition"])]["compact_sha256"]
                                         for row in candidates)}
        value["paired_wall_by_admission"] = {}
        for admitted in (False, True):
            own = [row for row in candidates if bool(row["flow_receipt"]["accepted"]) is admitted]
            value["paired_wall_by_admission"]["admitted" if admitted else "inactive_or_blocked"] = {
                "samples": len(own), "median_paired_delta_to_controls_s": {
                    arm: statistics.median(row["elapsed_backend_s"] - controls[arm][(row["query_id"], row["repetition"])]["elapsed_backend_s"]
                                           for row in own) if own else None for arm in ARMS[:2]}}
    return result


def audit_native(root, corpus, output, counter):
    artifacts = {name: file_sha(output / name) for name in
                 ("plan.json", "source-inventory.json", "run-started.json", "searches.jsonl.gz", "summary.json")}
    require(not (output / "failure.json").exists(), "Failed native attempt cannot be certified; retain its receipt")
    plan, start, summary = (read(output / name) for name in ("plan.json", "run-started.json", "summary.json"))
    binding = plan["binding"]
    declared = {"legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
        "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
        CANDIDATE: {"selector": "shared_flow_context", "postprocessor": "guard_source_context"}}
    require(plan["version"] == 1 and plan["study"] == binding["study"] == summary["study"] == "source_flow_native"
            and plan["classification"] == summary["classification"] == binding["classification"] == "repository_holdout",
            "Frozen native study/role identity differs")
    require(binding["candidate"] == summary["candidate"] == start["candidate"] == CANDIDATE
            and binding["conditions"] == declared and binding["settings"] == SETTINGS
            and binding["arms"] == summary["arms"] == {"legacy": "legacy", "coverage_control": "coverage_control", "candidate": CANDIDATE}
            and binding["retrieval"] == list(RETRIEVALS) and binding["repetitions"] == 3
            and binding["expected_requests"] == summary["expected_requests"] == start["expected_measured_calls"] == 252
            and binding["expected_warmups"] == start["expected_warmups"] == 6
            and plan["quality_repetition"] == summary["quality_repetition"] == 1,
            "Frozen three-arm native caps/schedule declarations differ")
    require(start["arms"] == list(ARMS) and start["resume_allowed"] is False and summary["complete"] is True,
            "Native completion or no-resume declaration differs")
    require(binding["gate"] == summary["gate"] == GATE, "Frozen dual-control source-quality/warm-latency gate differs")
    require(summary["plan_sha256"] == start["plan_sha256"] == artifacts["plan.json"]
            and summary["raw_sha256"] == artifacts["searches.jsonl.gz"]
            and plan["artifacts"] == {"source-inventory.json": artifacts["source-inventory.json"]},
            "Native raw/plan/source inventory bindings differ")
    require(binding["embedding"]["id"] == "Qwen/Qwen3-Embedding-0.6B"
            and binding["embedding"]["revision"] == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
            and binding["embedding"]["dimensions"] == 1024 and binding["embedding"]["quantized"] is False
            and binding["model_files_sha256"]["model.safetensors"] == "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd",
            "Original pinned unquantized Qwen model identity/weight hash differs")
    parent = parent_proof(root, binding)
    chain, execution = native_freeze(root, output, plan, start, artifacts)
    require(start["prospective_freeze"] == chain, "Actual committed native prospective chain differs")
    data, fixture = holdout_fixture(root, corpus, output, binding)
    queries = {query["id"]: query for query in data["queries"]}
    require(len(queries) == len(data["queries"]) == 14 and plan["queries"] == [
        {key: query[key] for key in ("id", "query", "kind")} for query in data["queries"]], "Frozen literal14-query membership/order differs")
    groups = {identifier: old.evidence_groups(query) for identifier, query in queries.items()}
    is_multi = {identifier: len({target["path"] for alternatives in gold for target in alternatives}) > 1
                for identifier, gold in groups.items()}
    require(sum(is_multi.values()) == 8 and sum(query["kind"] == "absent" for query in queries.values()) == 2
            and sum(query["kind"] == "present" and not is_multi[identifier] for identifier, query in queries.items()) == 4,
            "Frozen8multi/4single/2absent query strata differ")
    for identifier, gold in groups.items():
        for alternatives in gold:
            for target in alternatives:
                require(sha(win.literal(fixture["sources"], target["path"], *target["evidence_lines"]).encode()) == target["evidence_sha256"]
                        and target["source_file_sha256"] == binding["source_sha256"][target["path"]],
                        "Fresh qualified full-interval evidence/source hashes differ")
                require(any(item["path"] == target["path"] and item["symbol"] == target["symbol"]
                            and item["start_line"] <= target["evidence_lines"][0] <= target["evidence_lines"][1] <= item["end_line"]
                            for item in fixture["inventory"].values()),
                        "Fresh authored qualified evidence is not fully eligible in one original source window")
    rows = old.json_rows(output / "searches.jsonl.gz")
    require(len(rows) == 252, "Exactly252 measured native requests required")
    verify_repeats(rows, data["queries"])
    scope = native_scope(data)
    indexed = {(row["query_id"], row["retrieval"], row["repetition"], row["arm"]): row for row in rows}
    for row in rows:
        query, gold = queries[row["query_id"]], groups[row["query_id"]]
        require(row["kind"] == query["kind"] and row["multi_file"] == is_multi[row["query_id"]]
                and row["condition"] == binding["arms"][row["arm"]], "Native query/arm reporting identity differs")
        verify_trace(row, fixture, counter, query=query["query"], scope=scope)
        raw_for_scoring = [{**pair["snippet"], "source": pair["snippet"]["text"]} for pair in row["stages"]["ranked"]]
        require(row["candidate_ranks"] == strict_ranks(raw_for_scoring, gold)
                and row["delivered_ranks"] == strict_ranks(row["response"]["results"], gold),
                "Fresh qualified-symbol/full-physical-interval/hash source scoring differs")
        if row["arm"] == "candidate":
            control = indexed[(row["query_id"], row["retrieval"], row["repetition"], "coverage_control")]
            require(same_json(context_view(row["reference_response"]), context_view(control["response"])),
                    "Actual lazy reference differs from measured same-query coverage control")
        if row["arm"] == "coverage_control":
            require(row["source_view_sha256"] == sha(old.canonical(context_view(row["response"]))),
                    "Measured coverage source-view hash differs")
    verify_warmups(summary["setup"])
    require(summary["requests"] == summary["measured_query_embeddings"] == len(rows) == 252
            and summary["unique_queries"] == 14 and summary["measured_document_embedding_calls"] == 0
            and summary["total_query_embeddings"] == 258
            and summary["ranked_repeat_changes"] == summary["compact_repeat_changes"] == summary["query_vector_repeat_changes"] == 0,
            "Native measured/setup query/document/stability accounting differs")
    metrics = native_metrics(rows)
    require(same_json(summary["metrics"], metrics), "Independent native quality/warm-latency metrics differ")
    require(all(file_sha(output / name) == digest for name, digest in artifacts.items()), "Native artifacts changed during audit")
    return {"status": "passed", "checks_passed": True, "study": "source_flow_native", "mode": "native",
        "artifact_sha256": artifacts, "producer_freeze": chain, "producer_execution": execution,
        "qualified_development_parent": parent, "verified_requests": 252, "verified_warmups": 6,
        "unique_queries": 14, "verified_source_windows": len(fixture["inventory"]),
        "independently_recomputed_metrics": metrics, "source_sha256": binding["source_sha256"],
        "strict_qualified_symbol_and_full_evidence_bytes": True,
        "trust_boundary": "Frozen conservative source graph/parser shared; nativebaseline/staticdependencies, base/flowplanning, maximalpacking/backfill, sourceguard, exactpayload/vector/callcounts andallgates independently reconstructed. No producer, selector, backend, model, index or numerical runtime imported.",
        "limits": "14source-authored independent-repository queries; source coverage and resident latency are distinct from coding correctness, external transport, generalization and billed tokens."}


def forbidden_module(name):
    return flow.forbidden_module(name) or any(name == prefix or name.startswith(prefix + ".") for prefix in
        ("benchmark_flow_allocation", "audit_flow_native_producer", "semantic_search.backend", "semantic_search.flow_allocation"))


class RefuseProduction(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if forbidden_module(fullname):
            raise RuntimeError("Independent native flow audit forbids selector/backend/producer/model imports: " + fullname)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root, folder = args.root.resolve(), args.results.resolve()
    target = args.output.resolve() if args.output else folder / "independent-audit.json"
    failure = target.with_name(target.stem + "-failure.json")
    require(not target.exists() and not failure.exists(), "Preserve prior native audit attempts; choose a fresh output")
    names = ("scripts/audit_flow_native.py", "tests/test_flow_native_audit.py", "scripts/audit_flow_allocation.py",
        "tests/test_flow_allocation_audit.py", "scripts/audit_source_allocation.py", "tests/test_source_allocation_audit.py",
        "scripts/audit_windows.py", "scripts/audit_selection.py", *source.GRAPH_SOURCES)
    raw = {name: (root / name).read_bytes() for name in names}
    hashes = {name: sha(value) for name, value in raw.items()}
    for name, digest in hashes.items():
        require(git_sha(root, "HEAD", name) == digest,
                "Commit unchanged independent native auditor/tests/shared graph before outcome auditing: " + name)
    execution = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    require(not any(forbidden_module(name) for name in sys.modules), "Independent native audit needs a clean CPU process")
    finder = RefuseProduction()
    sys.path.insert(0, str(root / "src"))
    sys.meta_path.insert(0, finder)
    def denied(*args, **kwargs):
        raise RuntimeError("Network forbidden in independent native flow audit")
    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
            patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
            patch.object(socket.socket, "sendto", denied):
            counter = win.offline_encoding(Path(os.environ.get("TIKTOKEN_CACHE_DIR", root / ".cache/tokenizers")))
            receipt = audit_native(root, args.corpus.resolve(), folder, counter)
        require(all((root / name).read_bytes() == value for name, value in raw.items()),
                "Independent native auditor/shared extractor changed during auditing")
        receipt.update(auditor_sha256=hashes, auditor_execution_commit=execution,
            reviewed_at_utc=datetime.now(timezone.utc).isoformat(), calls={"model": 0, "retrieval": 0, "index": 0, "network": 0},
            candidate_planner_or_producer_modules_imported=False,
            allowed_shared_source_graph_modules=sorted(name for name in source.ALLOWED_GRAPH_MODULES if name in sys.modules))
        with target.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(json.dumps({"checks_passed": True, "audit": str(target), "sha256": file_sha(target)}))
    except BaseException as error:
        with failure.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"status": "failed", "error_type": type(error).__name__, "error": str(error),
                "auditor_sha256": hashes, "auditor_execution_commit": execution, "resume_allowed": False,
                "calls": {"model": 0, "retrieval": 0, "index": 0, "network": 0}}, indent=2) + "\n")
        raise
    finally:
        sys.meta_path.remove(finder)
        sys.path.remove(str(root / "src"))


if __name__ == "__main__":
    main()
