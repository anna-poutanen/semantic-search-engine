#!/usr/bin/env python3
"""Freeze and replay complementary implementation windows on observed CPU inputs."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("observed_selection_replay", ROOT / "scripts/replay_selection.py")
prior = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prior)
require, sha, read, rows, write_new = prior.require, prior.sha, prior.read, prior.rows, prior.write_new
SETTINGS = dict(prior.SETTINGS)
CONDITIONS = {
    "legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
    "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
    "connected_union": {"selector": "connected_window_context", "postprocessor": "coalesce_context"},
    "guarded_continuation": {"selector": "definition_context", "postprocessor": "continuation_context"},
    "guarded_connected_union": {"selector": "connected_window_context", "postprocessor": "guarded_connected_context"},
}
CONTROLS = ("legacy", "coverage_control")
CANDIDATES = ("connected_union", "guarded_continuation", "guarded_connected_union")
EXPECTED_ROWS = {**prior.EXPECTED_ROWS, "native_urllib3_observed": 28}
BASE_COMMIT = "5d1e80062638e19101acf3b9e8543066590fd1c5"
OLD_DEVELOPMENT = "results/selection-development-v2-2026-10-21"
OLD_NATIVE = "results/selection-holdout-urllib3-2026-10-21"
FROZEN_FILES = ("frozen-plan.json", "baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json")
ATTEMPT_FILES = ("run-start.json", "control-gate.json", "replay.jsonl.gz", "summary.json", "findings.md", "failure.json")


def canonical(value, *, newline=False):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    return (text + ("\n" if newline else "")).encode()


def identity(case):
    return {key: case[key] for key in ("subset", "dataset", "query_id", "condition")}


def identity_tuple(case):
    return tuple(case[key] for key in ("subset", "dataset", "query_id", "condition"))


def counts(cases):
    return {"observed_rows": len(cases),
            "unique_queries": len({(case["dataset"], case["query_id"]) for case in cases}),
            "row_counts_by_subset": dict(Counter(case["subset"] for case in cases)),
            "unique_queries_by_subset": {subset: len({(case["dataset"], case["query_id"]) for case in cases
                                                       if case["subset"] == subset}) for subset in EXPECTED_ROWS},
            "warning": "Reused queries and retrieval conditions are shared units; observed rows are not independent queries."}


def own_bindings(root):
    names = ["scripts/replay_windows.py", "tests/test_window_replay.py", "docs/window-development-protocol.md",
             "scripts/replay_selection.py", "tests/test_selection_replay.py", "tests/test_selection.py",
             "tests/test_definition_identity.py", "tests/test_context_backfill.py", "tests/test_window_assembly.py",
             "tests/test_backend.py", "tests/test_scope_context.py",
             "tests/test_connected_window_selection.py", "tests/test_guarded_window_selection.py",
             "tests/test_window_contracts.py", "tests/fixtures/window_union.json",
             "extensions/vscode/test/evidence.test.ts",
             *("src/semantic_search/" + name + ".py" for name in (*prior.MODULES, "window_assembly"))]
    return {name: sha((root / name).read_bytes()) for name in names}


def _bound(root, relative, bindings, expected=None):
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), "Bound input escapes project")
    raw = path.read_bytes()
    digest = sha(raw)
    require(expected is None or digest == expected, "Frozen historical input changed: " + relative)
    # Previous receipts and their inputs must be reachable committed data, not
    # newly substituted files that merely agree with another local manifest.
    saved = subprocess.check_output(["git", "-C", str(root), "show", BASE_COMMIT + ":" + relative])
    require(sha(saved) == digest, "Historical input differs from committed baseline: " + relative)
    bindings[relative] = digest
    return path


def _verified_artifacts(root, folder, bindings):
    audit = read(_bound(root, folder + "/independent-audit.json", bindings))
    require(audit.get("checks_passed") is True, "Successful independent historical audit required")
    for filename, digest in audit["artifact_sha256"].items():
        _bound(root, folder + "/" + filename, bindings, digest)
    return audit


def _source_fixture(root, corpus_root, modules, plan, inventory):
    binding = plan["binding"]
    labels = read(root / binding["dataset_path"])
    corpus = (corpus_root / "urllib3-selection-holdout").resolve()
    actual_files = {}
    sources = {}
    for name, expected in binding["source_files"].items():
        path = (corpus / name).resolve()
        require(path.is_relative_to(corpus), "Source path escapes pinned urllib3 corpus")
        raw = path.read_bytes()
        actual_files[name] = {"bytes": len(raw), "sha256": sha(raw)}
        require(actual_files[name] == expected, "Pinned urllib3 source bytes changed: " + name)
        sources[name] = modules["languages"].decode_source(path, raw)
    require(actual_files == labels["source_files"]
            and sha(canonical(actual_files, newline=True)) == labels["source_inventory_sha256"]
            and {name: row["sha256"] for name, row in actual_files.items()} == binding["source_sha256"],
            "Complete urllib3 source inventory differs")
    definition_keys = {}
    snippets = modules["corpus"].extract_snippets(corpus, labels["source_root"], 160, 40,
        sources=sources, source_exclusions=labels.get("source_exclusions", ()), definition_keys=definition_keys)
    actual_inventory = [asdict(item) for item in snippets]
    require(actual_inventory == inventory["inventory"]
            and sha(json.dumps(actual_inventory, sort_keys=True).encode()) == binding["inventory_sha256"]
            and json.loads(canonical(definition_keys)) == inventory["definition_keys"]
            and sha(canonical(definition_keys, newline=True)) == binding["definition_keys_sha256"],
            "Complete urllib3 extraction/definition inventory differs")
    require(len({item.id for item in snippets}) == len(snippets) == len(definition_keys),
            "Every urllib3 source window needs one canonical definition")
    return {"dataset": "urllib3", "sources": sources, "source_sha256": binding["source_sha256"],
            "inventory": {item.id: item for item in snippets}, "definition_keys": definition_keys,
            "graph": modules["relations"].ReferenceGraph(sources, snippets)}, labels


def load_inputs(root, corpus_root, modules):
    cases, bindings, fixtures = prior.load_inputs(root, corpus_root, modules)
    require(len(cases) == 337, "All 337 original observed rows required")
    audit = _verified_artifacts(root, OLD_DEVELOPMENT, bindings)
    old = read(root / OLD_DEVELOPMENT / "frozen-plan.json")
    expected_inputs = old["input_sha256"]
    for name, expected in expected_inputs.items():
        require(bindings.get(name) == expected, "Original development input matrix changed: " + name)
        _bound(root, name, bindings, expected)
    require(audit["input_sha256"] == expected_inputs and old["settings"] == SETTINGS,
            "Historical development audit/input settings disagree")
    source_matrix = {dataset + "/" + signature: fx["source_sha256"]
                     for (dataset, signature), fx in fixtures.items()}
    require(source_matrix == old["source_sha256"] == audit["source_sha256"],
            "Historical development source matrix differs")
    previous = {}
    for row in rows(root / OLD_DEVELOPMENT / "replay.jsonl.gz"):
        if row["selector"] == "definition_coverage_backfill":
            key = identity_tuple(row)
            require(key not in previous, "Duplicate historical coverage control")
            previous[key] = row
    require(len(previous) == 337 and set(previous) == {identity_tuple(case) for case in cases},
            "Historical coverage-control matrix differs")

    native_audit = _verified_artifacts(root, OLD_NATIVE, bindings)
    native = read(root / OLD_NATIVE / "plan.json")
    binding = native["binding"]
    require(binding["settings"] == SETTINGS and binding["arms"] == {
        "baseline": "legacy", "candidate": "definition_coverage_backfill"}, "Original native arms/settings differ")
    _bound(root, binding["dataset_path"], bindings, binding["dataset_sha256"])
    inventory = read(_bound(root, OLD_NATIVE + "/source-inventory.json", bindings,
                            native["artifacts"]["source-inventory.json"]))
    fx, labels = _source_fixture(root, corpus_root, modules, native, inventory)
    require(native_audit["source_sha256"] == fx["source_sha256"], "Native audit/source matrix differs")
    signature = sha(json.dumps(fx["source_sha256"], sort_keys=True).encode())
    fixtures[("urllib3", signature)] = fx
    queries = {query["id"]: query for query in labels["queries"]}
    physical_lines = modules["corpus"].physical_lines
    for row in rows(root / OLD_NATIVE / "searches.jsonl.gz"):
        if row["repetition"] != 1 or row["arm"] != "baseline":
            continue
        require(row["condition"] == "legacy" and row["retrieval"] in ("dense", "hybrid"),
                "Unexpected original urllib3 control")
        query = queries[row["query_id"]]
        require(row["kind"] == query["kind"], "Original urllib3 kind differs")
        evidence = prior.groups(query)
        for alternatives in evidence:
            for target in alternatives:
                low, high = target["evidence_lines"]
                require(type(low) is type(high) is int and 1 <= low <= high <= len(physical_lines(fx["sources"][target["path"]])),
                        "Invalid complete urllib3 evidence interval")
                source = "".join(physical_lines(fx["sources"][target["path"]])[low-1:high])
                require(sha(source.encode()) == target["evidence_sha256"]
                        and target["source_file_sha256"] == fx["source_sha256"][target["path"]],
                        "Pinned urllib3 evidence/source bytes differ")
        pairs = []
        for candidate in row["stages"]["ranked"]:
            item = fx["inventory"][candidate["snippet"]["id"]]
            require(asdict(item) == candidate["snippet"], "Original urllib3 candidate bytes/identity differ")
            score = candidate["score"]
            require(type(score) in (int, float) and math.isfinite(score), "Invalid original urllib3 score")
            pairs.append((item, score))
        require(len(pairs) <= 50 and len({item.id for item, _ in pairs}) == len(pairs), "Invalid urllib3 candidate pool")
        original = prior.ordered_response(row["response"])
        require(prior.evidence_ranks(original["results"], evidence) == row["delivered_ranks"]
                and sha(row["compact_payload"].encode()) == row["compact_sha256"], "Original urllib3 receipt differs")
        cases.append({"subset": "native_urllib3_observed", "dataset": "urllib3", "query_id": query["id"],
                      "condition": row["retrieval"], "kind": query["kind"], "groups": evidence,
                      "multi_file": row["multi_file"], "pairs": pairs, "original": original, **fx})
    require(dict(Counter(case["subset"] for case in cases)) == EXPECTED_ROWS
            and len({identity_tuple(case) for case in cases}) == len(cases), "Incomplete declared observed-row matrix")
    require(counts(cases)["unique_queries"] == 187, "Declared distinct-query count differs")
    for case in cases:
        by_label = {}
        for key in case["definition_keys"].values():
            by_label.setdefault(key[:2], set()).add(key)
        case["ambiguous_symbols"] = {label for label, definitions in by_label.items() if len(definitions) > 1}
    return cases, bindings, fixtures, previous


def source_row(item, score, origin, related_to, source, physical_lines):
    row = prior.source_row(item, score, origin, related_to, source, physical_lines)
    if hasattr(item, "assembly"):
        row["assembly"] = deepcopy(item.assembly)
    return row


def full_rows(case, pairs, physical_lines):
    result = [source_row(item, score, "retrieval", None, item.text, physical_lines) for item, score in pairs]
    if case["original"]["results"] and "qwen_score" in case["original"]["results"][0]:
        for row in result:
            row["qwen_score"] = None
    return result


def pack(template, stream, context, physical_lines):
    """Original packing, with an out-of-compact assembly receipt on composites."""
    result = {key: deepcopy(template[key]) for key in ("query", "scope", "match_status")}
    result["results"], remaining = [], SETTINGS["source_budget"]
    for item, score, origin, related_to in stream:
        prefix, used = [], 0
        for line in physical_lines(item.text):
            if used + len(line) > remaining:
                break
            prefix.append(line)
            used += len(line)
        source = "".join(prefix)
        if not source:
            continue
        remaining -= len(source)
        row = source_row(item, score, origin, related_to, source, physical_lines)
        if template["results"] and "qwen_score" in template["results"][0]:
            row["qwen_score"] = None
        result["results"].append(row)
    result["total_source_chars"] = SETTINGS["source_budget"] - remaining
    result["budget_exhausted"] = any(row["truncated"] for row in result["results"]) or len(result["results"]) < len(stream)
    return context.apply_token_budget(result, SETTINGS["token_budget"])


def verify_assembly(row, case, physical_lines):
    """Independent verifier: never calls the union/continuation producer."""
    assembly = row["assembly"]
    require(assembly.get("kind") == "connected_windows" and len(assembly.get("members", [])) == 2,
            "Composite must declare exactly two physical source members")
    key = tuple(assembly["definition_key"])
    members = assembly["members"]
    intervals = []
    ranks = {item.id: rank for rank, (item, _) in enumerate(case["pairs"], 1)}
    scores = {item.id: score for item, score in case["pairs"]}
    for member in members:
        require(set(member) == {"id", "path", "symbol", "start_line", "end_line", "content_hash",
                                "used_start_line", "used_end_line", "used_source_hash"}, "Unexpected member receipt")
        item = case["inventory"].get(member["id"])
        require(item is not None and all(member[name] == getattr(item, name) for name in
                ("path", "symbol", "start_line", "end_line", "content_hash")), "Composite member differs from original inventory")
        require(tuple(case["definition_keys"][item.id]) == key and member["id"] in ranks,
                "Composite members must be raw ranked windows of the same exact definition")
        low, high = member["used_start_line"], member["used_end_line"]
        require(type(low) is type(high) is int and low == item.start_line <= high <= item.end_line,
                "Composite member must use its whole physical prefix")
        text = "".join(physical_lines(case["sources"][item.path])[low-1:high])
        require(sha(text.encode()) == member["used_source_hash"], "Composite used-member bytes differ")
        intervals.append((low, high, text))
    anchor, companion = members
    require(assembly["score_anchor_id"] == anchor["id"]
            and assembly["anchor_rank"] == ranks[anchor["id"]]
            and assembly["companion_rank"] == ranks[companion["id"]], "Composite rank/score anchor differs")
    require(companion["used_end_line"] == companion["end_line"], "Companion must be a full raw ranked window")
    first, second = sorted(intervals, key=lambda value: value[:2])
    require(second[0] <= first[1] + 1 and (first[0] < anchor["used_start_line"]
            or max(first[1], second[1]) > anchor["used_end_line"]),
            "Composite members have a gap or add no source")
    start, end = first[0], max(first[1], second[1])
    require(tuple(key[:2]) == (row["path"], row["symbol"])
            and key[2] <= start <= end <= key[3], "Composite escaped its actual AST definition")
    full = "".join(physical_lines(case["sources"][row["path"]])[start-1:end])
    for low, high, text in intervals:
        require("".join(physical_lines(full)[low-start:high-start+1]) == text,
                "Composite overlap or member source is inconsistent")
    binding = {"kind": "connected_windows", "definition_key": list(key),
               "members": sorted(members, key=lambda item: (item["id"], item["used_start_line"], item["used_end_line"], item["used_source_hash"])),
               "start_line": start, "end_line": end, "content_hash": sha(full.encode())}
    require(row["id"] == "context:connected_windows:" + sha(canonical(binding))
            and row["start_line"] == start and row["window_end_line"] == end
            and row["content_hash"] == binding["content_hash"], "Composite deterministic identity/full-source hash differs")
    require(row["retrieval_score"] == scores[anchor["id"]]
            and row.get("h2o_score") is None and row.get("qwen_score") is None,
            "Composite falsely claims a new score or loses its inherited anchor score")
    if "inherited_scores" in assembly:
        require(start == anchor["used_start_line"] and end > anchor["used_end_line"],
                "Guarded continuation must extend forward without moving old source")
        require(assembly["inherited_scores"] == {"retrieval_score": scores[anchor["id"]],
                                                "h2o_score": None, "qwen_score": None},
                "Continuation inherited-score receipt differs")
    return full


def verify_response(response, case, context, physical_lines, assemblies=None):
    require(len(response["results"]) <= 8 and len({row["id"] for row in response["results"]}) == len(response["results"]),
            "Delivered excerpt count/identity differs")
    assembly_rows = dict(assemblies or {})
    assembly_rows.update({row["id"]: row for row in response["results"] if "assembly" in row})
    raw_scores = {item.id: score for item, score in case["pairs"]}
    for row in response["results"]:
        if "assembly" in row:
            full = verify_assembly(row, case, physical_lines)
        else:
            item = case["inventory"].get(row["id"])
            require(item is not None and all(row[name] == getattr(item, name) for name in ("path", "symbol", "start_line"))
                    and row["window_end_line"] == item.end_line and row["content_hash"] == item.content_hash,
                    "Physical source/window identity differs")
            full = item.text
        require(type(row["end_line"]) is int and row["start_line"] <= row["end_line"] <= row["window_end_line"],
                "Invalid delivered physical bounds")
        source = "".join(physical_lines(case["sources"][row["path"]])[row["start_line"]-1:row["end_line"]])
        require(source == row["source"] and sha(source.encode()) == row["source_hash"]
                and len(physical_lines(source)) == row["end_line"]-row["start_line"]+1
                and row["truncated"] == (source != full), "Literal source/hash/truncation differs")
        if row["origin"] == "retrieval":
            anchor = row["assembly"]["score_anchor_id"] if "assembly" in row else row["id"]
            require(row["related_to"] is None and anchor in raw_scores and row["retrieval_score"] == raw_scores[anchor],
                    "Retrieval result has no original score anchor")
        else:
            require(row["origin"] == "dependency" and "assembly" not in row, "Invalid origin/composite dependency")
            parent = row["related_to"]
            if parent in assembly_rows:
                verify_assembly(assembly_rows[parent], case, physical_lines)
                parent = assembly_rows[parent]["assembly"]["score_anchor_id"]
            require(parent in raw_scores and parent in case["inventory"]
                    and row["retrieval_score"] == raw_scores[parent]
                    and row["id"] in {item.id for item in case["graph"].dependencies(case["inventory"][parent], limit=2)},
                    "Dependency composite/raw parent is not an allowed original score anchor")
    require(response["total_source_chars"] == sum(len(row["source"]) for row in response["results"]) <= 12000,
            "Character cap/count differs")
    text = context.serialize_context(context.compact_result(response))
    require(response["token_usage"]["encoding"] == "cl100k_base" and response["token_usage"]["budget"] == 4096
            and len(context.encoding().encode(text, disallowed_special=())) == response["token_usage"]["tokens"] <= 4096,
            "Exact compact token cap/count differs")
    return text


def source_preserved(before, after, physical_lines):
    for old in before["results"]:
        covering = [row for row in after["results"] if row["path"] == old["path"]
                    and row["start_line"] <= old["start_line"] <= old["end_line"] <= row["end_line"]
                    and all((name in row) == (name in old) and row.get(name) == old.get(name)
                            for name in ("source_origin", "buffer_version", "buffer_hash"))]
        if not any("".join(physical_lines(row["source"])[old["start_line"]-row["start_line"]:
                       old["end_line"]-row["start_line"]+1]) == old["source"] for row in covering):
            return False
    return True


def preserved(before, after, physical_lines, *, retained_metadata=True):
    require(source_preserved(before, after, physical_lines), "Postprocessor lost prior actual delivered source")
    if not retained_metadata:
        return
    replacements = {row["assembly"]["score_anchor_id"]: row["id"]
                    for row in after["results"] if "assembly" in row}
    old_by_id = {row["id"]: row for row in before["results"]}
    for row in after["results"]:
        if row["id"] not in old_by_id:
            continue
        expected = deepcopy(old_by_id[row["id"]])
        if expected.get("related_to") in replacements:
            expected["related_to"] = replacements[expected["related_to"]]
        require(row == expected, "Postprocessor changed retained old metadata beyond a truthful composite parent ID")


def evaluate(case, name, modules, *, reference_response=None):
    specification = CONDITIONS[name]
    selector = getattr(modules["selection"], specification["selector"])
    # Gold, case IDs, kinds and target groups are deliberately absent here.
    selected, eligible, origins = selector(case["pairs"], query=case["original"]["query"],
        max_results=8, include_dependencies=True, references=case["graph"],
        definition_keys=case["definition_keys"], ambiguous_symbols=case["ambiguous_symbols"])
    stream = [(item, score, *origins.get(item.id, ("retrieval", None))) for item, score in eligible]
    physical_lines, context = modules["corpus"].physical_lines, modules["context"]
    response = pack(case["original"], stream, context, physical_lines)
    before = deepcopy(response)
    candidates = full_rows(case, selected, physical_lines)
    ranked = full_rows(case, case["pairs"], physical_lines)
    postprocessor = (importlib.import_module("semantic_search.context_backfill").coalesce_context
                     if specification["postprocessor"] == "coalesce_context"
                     else getattr(modules["selection"], specification["postprocessor"]))
    kwargs = {"ranked_candidates": deepcopy(ranked), "definition_keys": case["definition_keys"],
              "max_results": 8, "source_budget": 12000, "token_budget": 4096}
    reference = []
    guard_trace = []
    guard_patch = nullcontext()
    if name == "guarded_connected_union":
        def reference_factory():
            value = (reference_response if reference_response is not None else
                     evaluate(case, "coverage_control", modules)["response"])
            reference.append(deepcopy(value))
            return deepcopy(value)
        kwargs["reference_factory"] = reference_factory
        original_guard = modules["selection"]._preserves_context
        def capture_guard(actual_reference, proposal):
            guard_trace.append({"reference": deepcopy(actual_reference), "proposal": deepcopy(proposal)})
            return original_guard(actual_reference, proposal)
        guard_patch = patch.object(modules["selection"], "_preserves_context", capture_guard)
    with guard_patch:
        response = postprocessor(response, deepcopy(candidates), **kwargs)
    if name == "guarded_connected_union":
        require(len(reference) == len(guard_trace) == 1 and reference[-1] == guard_trace[-1]["reference"],
                "Guarded connected selector must use its same-input coverage reference exactly once")
        preserved(reference[-1], response, physical_lines, retained_metadata=False)
        proposal = guard_trace[-1]["proposal"]
        accepted = source_preserved(reference[-1], proposal, physical_lines)
        reference_text = context.serialize_context(context.compact_result(reference[-1]))
        expected_debug = {"accepted": accepted,
                          "fallback_reason": None if accepted else "coverage_control_source_not_preserved",
                          "reference_compact_sha256": sha(reference_text.encode()),
                          "proposed_composites": sum("assembly" in row for row in proposal["results"]),
                          "accepted_composites": sum("assembly" in row for row in response["results"])}
        require(response.get("_window_guard") == expected_debug
                and {key: value for key, value in response.items() if key != "_window_guard"}
                    == (proposal if accepted else reference[-1]), "Independent source guard decision/fallback differs")
    elif name != "legacy":
        preserved(before, response, physical_lines)
    assemblies = {row["id"]: row for row in full_rows(case, selected + eligible, physical_lines) if "assembly" in row}
    verify_response(before, case, context, physical_lines, assemblies)
    text = verify_response(response, case, context, physical_lines, assemblies)
    if name == "guarded_connected_union":
        verify_response(reference[-1], case, context, physical_lines, assemblies)
        verify_response(proposal, case, context, physical_lines, assemblies)
    keys = dict(case["definition_keys"])
    for item, _ in selected + eligible:
        if hasattr(item, "assembly"):
            keys[item.id] = tuple(item.assembly["definition_key"])
    relevant = {item.id for item, _ in case["pairs"] + selected + eligible}
    result = {"stages": {"ranked": [{"snippet": asdict(item), "score": score} for item, score in case["pairs"]],
                       "selected": [{"snippet": asdict(item), "score": score} for item, score in selected],
                       "eligible": [{"snippet": asdict(item), "score": score} for item, score in eligible],
                       "origins": origins,
                       "definition_keys": {key: keys[key] for key in sorted(relevant) if key in keys},
                       "ambiguous_symbols": sorted(case["ambiguous_symbols"])},
            "pre_postprocess_response": before, "backfill_candidates": candidates, "ranked_candidates": ranked,
            "response": response, "compact_payload": text, "compact_sha256": sha(text.encode()),
            "actual_compact_tokens": response["token_usage"]["tokens"]}
    if name == "guarded_connected_union":
        result["reference_response"] = reference[-1]
        result["proposal_response"] = proposal
    return result


def control_barrier(cases, modules):
    context, physical_lines = modules["context"], modules["corpus"].physical_lines
    controls = []
    for case in cases:
        baseline = pack(case["original"], prior.legacy_stream(case["pairs"], case["graph"]), context, physical_lines)
        text = verify_response(baseline, case, context, physical_lines)
        original = case["original"]
        require(text == context.serialize_context(context.compact_result(original))
                and baseline["results"] == original["results"]
                and baseline["total_source_chars"] == original["total_source_chars"]
                and baseline["budget_exhausted"] == original["budget_exhausted"]
                and baseline["token_usage"] == original["token_usage"], "Exact baseline mismatch: " + str(identity(case)))
        controls.append({**identity(case), "compact_sha256": sha(text.encode())})
    return controls


def coverage_barrier(cases, previous, modules):
    controls = []
    for case in cases:
        key = identity_tuple(case)
        if key not in previous:
            continue
        actual = evaluate(case, "coverage_control", modules)
        saved = previous[key]
        ranks = prior.evidence_ranks(actual["response"]["results"], case["groups"])
        require(actual["response"] == saved["response"] and actual["compact_sha256"] == saved["compact_sha256"]
                and ranks == saved["evidence_ranks"], "Exact historical coverage-control mismatch: " + str(identity(case)))
        controls.append({**identity(case), "compact_sha256": actual["compact_sha256"],
                         "response_sha256": sha(canonical(actual["response"])), "evidence_ranks": ranks})
    require(len(controls) == 337, "All 337 historical coverage controls required")
    return controls


def fixture_snapshot(fixtures):
    return {"classification": "Complete source-only extraction/definition receipts before new variant outcomes",
            "fixtures": {dataset + "/" + signature: {
                "dataset": dataset, "source_sha256": fx["source_sha256"],
                "inventory": [asdict(item) for item in fx["inventory"].values()],
                "definition_keys": fx["definition_keys"]}
                for (dataset, signature), fx in fixtures.items()}}


def freeze(root, corpus_root, output):
    require(not output.exists(), "Use a new freeze directory")
    runtime, rank_bytes = prior.runtime(root)
    with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
        cases, bindings, fixtures, previous = load_inputs(root, corpus_root, modules)
        controls = control_barrier(cases, modules)
        coverage_controls = coverage_barrier(cases, previous, modules)
    output.mkdir(parents=True)
    write_new(output / "source-fixtures.json", fixture_snapshot(fixtures))
    plan = {"version": 1, "classification": "Prospective CPU DEVELOPMENT complementary-window replay on observed orders",
            "frozen_at_utc": datetime.now(timezone.utc).isoformat(), "historical_baseline_commit": BASE_COMMIT,
            "conditions": CONDITIONS, "settings": SETTINGS, "counts": counts(cases), "runtime": runtime,
            "producer_sha256": own_bindings(root), "input_sha256": bindings,
            "artifacts": {"source-fixtures.json": sha((output / "source-fixtures.json").read_bytes())},
            "source_sha256": {dataset + "/" + signature: fx["source_sha256"] for (dataset, signature), fx in fixtures.items()},
            "baseline_controls_sha256": sha(canonical(controls)),
            "prior_coverage_controls_sha256": sha(canonical(coverage_controls)),
            "barriers": {"legacy_exact_rows": 365, "known_coverage_exact_rows": 337,
                         "new_variant_outcomes_computed": 0,
                         "rule": "Reproduce every immutable legacy compact/result/source/hash/cap record and every saved prior-best coverage response before any new variant."},
            "selection": {
                "legacy": "Original display-label dedup, first five anchors, two static callees per anchor, ranked backfill to eight.",
                "coverage_control": "The prior-best research definition selector and source-preserving covered-row removal/backfill; reproduce all 337 saved outcomes exactly.",
                "connected_union": "At each definition's first original rank, join its representative with the first same-definition raw ranked companion whose full windows overlap or are adjacent and add source. One companion only; inherit the original anchor scalar, emit no new reranker score, then apply unchanged allocation/packing and coverage backfill. Larger early windows may cut later evidence; retain all losses.",
                "guarded_continuation": "The definition selector plus existing coverage/backfill, then forward-extend actual delivered retrieval prefixes using a connected raw ranked same-definition companion. At most eight new candidate trials; trim only appended suffix lines to exact caps, preserve every prior actual source byte and update dependency IDs truthfully.",
                "guarded_connected_union": "Render the connected+coalesced proposal and the same-input coverage_control reference without new retrieval/model calls. Accept the proposal only if it literally contains every reference actual source interval with identical provenance; otherwise return the coverage reference. Guard decisions use source only, never gold."},
            "packing": "Unchanged 8 excerpts, 12000 source characters, 4096 exact compact cl100k JSON tokens; complete physical-line prefixes, exact recounts. Assembly/debug receipts stay outside compact text, while changed dependency related_to IDs are counted.",
            "assembly_verification": "Independent pinned-source/member/definition/rank/score/union/ID verifier; no assembly producer helper is called to verify its own output. Full intended union bytes and metadata remain verifiable when delivered source is clipped.",
            "labels": "Gold IDs/kinds/targets are reporting-only. Selection receives raw pairs, query, caps, references and ephemeral canonical definition/ambiguity metadata; postprocessing receives full distinct/raw rows and definition metadata, never labels.",
            "qualification": "Each new candidate must preserve every covered full evidence group, complete row and single-file first hit against BOTH legacy and coverage_control, plus gain at least one ADDITIONAL DISTINCT complete dataset/query unit compared with coverage_control. The two previously known gains do not count as new. No automatic default change.",
            "execution": "Freeze computes only 365 literal legacy controls and 337 already-observed coverage controls. Commit unchanged code/tests/protocol/plan/gates before --run; reject changed bindings, uncommitted files and existing attempts. Run repeats both barriers before any new variant.",
            "limits": ["All 365 rows / 187 query units are observed development data, including urllib3; no fresh holdout enters this replay.",
                       "Rows share queries across retrieval conditions and studies; stratify rather than count rows as independent queries.",
                       "No model, numerical runtime, index, retrieval, reranking, network or GPU calls. CPU replay latency is not native warm latency.",
                       "Source-evidence AND coverage is not coding correctness, provider-token usage or agent speed."]}
    write_new(output / "frozen-plan.json", plan)
    digest = sha((output / "frozen-plan.json").read_bytes())
    write_new(output / "baseline-gate.json", {"exact_rows": len(controls), "cases": controls,
              "plan_sha256": digest, "new_variant_outcomes_computed": 0})
    write_new(output / "prior-coverage-gate.json", {"exact_rows": len(coverage_controls), "cases": coverage_controls,
              "plan_sha256": digest, "new_variant_outcomes_computed": 0})
    return {"classification": plan["classification"], **plan["counts"], "exact_legacy_controls": len(controls),
            "exact_prior_coverage_controls": len(coverage_controls), "new_variant_outcomes_computed": 0}


def validate_run(root, output, plan):
    require(plan["conditions"] == CONDITIONS and plan["settings"] == SETTINGS, "Undeclared selector/configuration")
    require(plan["producer_sha256"] == own_bindings(root), "Code/tests/protocol changed after freeze")
    bound = [*plan["producer_sha256"], *((output / name).relative_to(root).as_posix() for name in FROZEN_FILES)]
    subprocess.check_call(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", *bound], stdout=subprocess.DEVNULL)
    require(subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", *bound]).returncode == 0,
            "Commit unchanged prospective code/tests/protocol/plan/gates before variant evaluation")
    require(not any((output / name).exists() for name in ATTEMPT_FILES), "Never overwrite or resume a prior attempt/failure")


def comparison(control, response, groups):
    before, after, gains, losses = prior.group_changes(control, response, groups)
    return {"control_ranks": before, "variant_ranks": after, "group_gains": gains, "group_losses": losses,
            "complete_gain": bool(groups) and None in before and None not in after,
            "complete_loss": bool(groups) and None not in before and None in after}


def metrics(items):
    result = prior.metrics(items)
    result["assembled_rows"] = sum(any("assembly" in row for row in item["response"]["results"]) for item in items)
    return result


def summarize(outcomes, case_counts):
    strata_metrics, changes, qualification = {}, {}, {}
    for name in CONDITIONS:
        selected = [row for row in outcomes if row["selector"] == name]
        strata = sorted({(row["subset"], row["dataset"], row["condition"]) for row in selected})
        strata_metrics[name] = {"/".join(stratum): metrics([row for row in selected if
            tuple(row[key] for key in ("subset", "dataset", "condition")) == stratum]) for stratum in strata}
        changes[name] = {}
        for control in CONTROLS:
            group_gains, group_losses, complete_gains, complete_losses, first_losses = [], [], [], [], []
            for row in selected:
                difference = row["comparisons"][control]
                reference = identity(row)
                if difference["group_gains"]:
                    group_gains.append({**reference, "groups": difference["group_gains"]})
                if difference["group_losses"]:
                    group_losses.append({**reference, "groups": difference["group_losses"]})
                if difference["complete_gain"]:
                    complete_gains.append(reference)
                if difference["complete_loss"]:
                    complete_losses.append(reference)
                if row["kind"] == "present" and not row["multi_file"] and difference["control_ranks"] \
                        and difference["control_ranks"][0] is not None and difference["variant_ranks"][0] is None:
                    first_losses.append(reference)
            unique_gains = sorted({row["dataset"] + "/" + row["query_id"] for row in complete_gains})
            changes[name][control] = {"group_gains": group_gains, "group_losses": group_losses,
                                     "complete_gains": complete_gains, "complete_losses": complete_losses,
                                     "unique_complete_gains": unique_gains, "single_file_first_hit_losses": first_losses}
        if name in CANDIDATES:
            losses = {control: {"lost_groups": sum(len(row["groups"]) for row in changes[name][control]["group_losses"]),
                                "lost_complete_rows": len(changes[name][control]["complete_losses"]),
                                "lost_single_file_first_hits": len(changes[name][control]["single_file_first_hit_losses"])}
                      for control in CONTROLS}
            new_gains = changes[name]["coverage_control"]["unique_complete_gains"]
            qualification[name] = {"qualified": bool(new_gains) and not any(value for loss in losses.values() for value in loss.values()),
                                   "against_controls": losses,
                                   "additional_distinct_complete_queries_vs_coverage_control": new_gains,
                                   "gained_additional_distinct_complete_queries": len(new_gains),
                                   "requires_fresh_native_validation": True, "automatic_default_change": False}
    return {"classification": "Observed-source CPU DEVELOPMENT complementary-window replay", "counts": case_counts,
            "metrics_by_study_dataset_original_condition": strata_metrics, "all_changes": changes,
            "qualification": qualification, "model_calls": 0, "retrieval_calls": 0, "network_calls": 0,
            "automatic_default_change": False}


def findings(summary):
    result = ["# Complementary-window development replay", "", summary["classification"], "",
              f"{summary['counts']['observed_rows']} observed first-repetition rows share {summary['counts']['unique_queries']} distinct dataset/query units. Former holdouts, including urllib3, are development data. Reused rows are not independent queries.", "",
              "All original legacy responses and all saved prior-best coverage responses reproduced before new variants. Every condition used 8 excerpts, 12,000 source characters and 4,096 exact compact cl100k tokens. No model, retrieval, index or network calls occurred.", "",
              "| condition | study / dataset / original order | complete | covered groups | multi-file complete | single-file first hit |",
              "|---|---|---:|---:|---:|---:|"]
    for name, strata in summary["metrics_by_study_dataset_original_condition"].items():
        for stratum, metric in strata.items():
            result.append(f"| {name} | {stratum} | {metric['complete']} / {metric['present_rows']} | {metric['covered_groups']} / {metric['required_groups']} | {metric['multi_file_complete']} | {metric['single_file_first_hit']} |")
    result += ["", "Qualification requires no lost full group, complete row or single-file first hit compared with either legacy or the prior-best coverage control, plus an additional distinct complete query over that coverage control. Previously known gains do not count as new.", ""]
    for name, verdict in summary["qualification"].items():
        result.append(f"- {name}: {'qualifies for further review' if verdict['qualified'] else 'does not qualify'}; {verdict['gained_additional_distinct_complete_queries']} additional distinct complete-query gains over coverage_control. Loss details against both controls are retained in summary.json.")
    result += ["", "Every group/complete-query gain and regression is retained against both controls, with raw ranked, selected and eligible source, pre/postprocessing responses, source/assembly receipts and exact compact token counts. Source-evidence coverage is not coding correctness, billing, native warm speed or generalization. Defaults remain unchanged.", ""]
    return "\n".join(result)


def run(root, corpus_root, output):
    plan = read(output / "frozen-plan.json")
    validate_run(root, output, plan)
    write_new(output / "run-start.json", {"created_at_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
              "execution_commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
              "plan_sha256": sha((output / "frozen-plan.json").read_bytes()),
              "model_calls": 0, "retrieval_calls": 0, "network_calls": 0, "resume_allowed": False})
    state = {"stage": "runtime", "exact_legacy_controls": 0, "exact_prior_coverage_controls": 0,
             "attempted_new_variant_rows": 0, "new_variant_rows": 0}
    try:
        runtime, rank_bytes = prior.runtime(root)
        require(runtime == plan["runtime"], "CPU runtime changed after freeze")
        with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
            cases, bindings, fixtures, previous = load_inputs(root, corpus_root, modules)
            require(bindings == plan["input_sha256"] and counts(cases) == plan["counts"]
                    and {dataset + "/" + signature: fx["source_sha256"] for (dataset, signature), fx in fixtures.items()} == plan["source_sha256"],
                    "Frozen input/source/matrix changed")
            require(sha((output / "source-fixtures.json").read_bytes()) == plan["artifacts"]["source-fixtures.json"]
                    and json.loads(canonical(fixture_snapshot(fixtures))) == read(output / "source-fixtures.json"),
                    "Frozen source-only extraction/definition snapshot changed")
            state["stage"] = "all_historical_controls"
            controls = control_barrier(cases, modules)
            coverage_controls = coverage_barrier(cases, previous, modules)
            state["exact_legacy_controls"], state["exact_prior_coverage_controls"] = len(controls), len(coverage_controls)
            digest = sha((output / "frozen-plan.json").read_bytes())
            baseline_gate, coverage_gate = read(output / "baseline-gate.json"), read(output / "prior-coverage-gate.json")
            require(controls == baseline_gate["cases"] and coverage_controls == coverage_gate["cases"]
                    and baseline_gate["plan_sha256"] == coverage_gate["plan_sha256"] == digest
                    and plan["baseline_controls_sha256"] == sha(canonical(controls))
                    and plan["prior_coverage_controls_sha256"] == sha(canonical(coverage_controls)), "Prospective historical-control gates differ")
            write_new(output / "control-gate.json", {"exact_legacy_rows": len(controls), "exact_prior_coverage_rows": len(coverage_controls),
                      "baseline_cases": controls, "coverage_cases": coverage_controls, "new_variant_rows_before_gate": 0,
                      "plan_sha256": digest})
            state["stage"] = "all_fixed_conditions"
            outcomes = []
            for case in cases:
                results = {}
                for name in CONDITIONS:
                    state["current_case"], state["current_condition"] = identity(case), name
                    state["attempted_new_variant_rows"] += name in CANDIDATES
                    results[name] = evaluate(case, name, modules,
                        reference_response=results["coverage_control"]["response"] if name == "guarded_connected_union" else None)
                    state["new_variant_rows"] += name in CANDIDATES
                require(results["legacy"]["response"]["results"] == case["original"]["results"]
                        and results["legacy"]["compact_payload"] == modules["context"].serialize_context(modules["context"].compact_result(case["original"])),
                        "Production legacy selector differs from literal historical control")
                for name, result in results.items():
                    evidence_ranks = prior.evidence_ranks(result["response"]["results"], case["groups"])
                    comparisons = {control: comparison(results[control]["response"], result["response"], case["groups"])
                                   for control in CONTROLS}
                    outcomes.append({**identity(case), "selector": name, "kind": case["kind"], "multi_file": case["multi_file"],
                                     "evidence_groups": case["groups"], "evidence_ranks": evidence_ranks,
                                     "comparisons": comparisons, **result})
            state["stage"] = "publish_all_outcomes"
            with (output / "replay.jsonl.gz").open("xb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
                    for outcome in outcomes:
                        zipped.write((json.dumps(outcome, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode())
            summary = summarize(outcomes, counts(cases))
            summary.update({"baseline_exact_rows": len(controls), "prior_coverage_exact_rows": len(coverage_controls),
                            "plan_sha256": digest, "replay_sha256": sha((output / "replay.jsonl.gz").read_bytes())})
            write_new(output / "summary.json", summary)
            with (output / "findings.md").open("x", encoding="utf-8") as stream:
                stream.write(findings(summary))
            return {"classification": summary["classification"], **summary["counts"],
                    "qualification": summary["qualification"], "baseline_exact_rows": len(controls),
                    "prior_coverage_exact_rows": len(coverage_controls)}
    except BaseException as error:
        write_new(output / "failure.json", {"failed": True, "error_type": type(error).__name__, "error": str(error),
                  "progress": state, "resume_allowed": False, "model_calls": 0, "retrieval_calls": 0, "network_calls": 0})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args()
    operation = freeze if args.freeze else run
    print(json.dumps(operation(args.root.resolve(), args.corpus_root.resolve(), args.output.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
