#!/usr/bin/env python3
"""Independent literal-source audit for complementary-window experiments.

This module never imports the producer, selectors, backend, index or models.
The older auditor supplies only its independent serializer, offline tokenizer,
historical case readers and Git-blob checks.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
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
SPEC = importlib.util.spec_from_file_location("independent_selection_audit", ROOT / "scripts/audit_selection.py")
old = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(old)
require, sha, physical_lines = old.require, old.sha, old.physical_lines
compact_payload, offline_encoding = old.compact_payload, old.offline_encoding
read, json_rows = old.read, old.json_rows
CONDITIONS = ("legacy", "coverage_control", "connected_union", "guarded_continuation", "guarded_connected_union")
PROVENANCE = ("source_origin", "buffer_version", "buffer_hash")
SNIPPET_FIELDS = {"id", "path", "symbol", "start_line", "end_line", "text", "content_hash"}
MEMBER_FIELDS = {"id", "path", "symbol", "start_line", "end_line", "content_hash",
                 "used_start_line", "used_end_line", "used_source_hash"}
FORBIDDEN = ("semantic_search", "replay_windows", "benchmark_windows", "replay_selection",
             "benchmark_selection", "mlx", "mlx_embeddings", "torch", "transformers", "numpy", "jax")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def case_key(row):
    return tuple(row[name] for name in ("subset", "dataset", "query_id", "condition"))


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def literal(sources, path, low, high):
    require(path in sources and type(low) is type(high) is int, "Source path/bounds schema")
    lines = physical_lines(sources[path])
    require(1 <= low <= high <= len(lines), "Source interval outside physical file")
    return "".join(lines[low - 1:high])


def same_provenance(first, second):
    return all((name in first) == (name in second) and first.get(name) == second.get(name)
               for name in PROVENANCE)


def verify_inventory(inventory, sources, definition_keys):
    """Check frozen canonical source maps without executing an extractor.

    Complete JS/TS keys must already be bound by the committed source-only
    preflight snapshot. Their exact byte offsets remain part of identity.
    """
    require(set(inventory) == set(definition_keys), "Every inventory window needs a canonical definition key")
    for identifier, item in inventory.items():
        require(item["id"] == identifier and "assembly" not in item
                and not identifier.startswith("context:"), "Ephemeral assembly persisted in raw inventory")
        text = literal(sources, item["path"], item["start_line"], item["end_line"])
        require(item["text"] == text and item["content_hash"] == sha(text.encode()), "Canonical inventory source/hash")
        key = definition_keys[identifier]
        require(isinstance(key, (tuple, list)) and len(key) in (4, 5)
                and list(key[:2]) == [item["path"], item["symbol"]]
                and type(key[2]) is type(key[3]) is int
                and 1 <= key[2] <= item["start_line"] <= item["end_line"] <= key[3]
                <= len(physical_lines(sources[item["path"]])), "Canonical definition label/bounds")
        if len(key) == 5:
            # Initializer groups deliberately use the enclosing class_body
            # offset, which can precede the first grouped field's physical row.
            require(type(key[4]) is int and 0 <= key[4] < len(sources[item["path"]].encode()),
                    "Canonical JavaScript definition byte offset")


def raw_ranked(stage, inventory):
    """Saved raw candidates must stay exact canonical snippets in saved order."""
    result = {}
    require(len(stage) <= 50, "Raw candidate cap")
    for position, pair in enumerate(stage, 1):
        item, score = pair["snippet"], pair["score"]
        require(set(item) == SNIPPET_FIELDS and item["id"] in inventory,
                "Raw candidate must be an unchanged indexed snippet")
        require(item == {name: inventory[item["id"]][name] for name in SNIPPET_FIELDS},
                "Raw candidate source/cache identity changed")
        require(item["id"] not in result and finite(score), "Raw candidate ID/score")
        result[item["id"]] = {"snippet": item, "score": score, "rank": position}
    return result


def composite_id(members, key, source, low, high):
    ordered = sorted(members, key=lambda member: (member["id"], member["used_start_line"],
                                                member["used_end_line"], member["used_source_hash"]))
    binding = {"kind": "connected_windows", "definition_key": list(key), "members": ordered,
               "start_line": low, "end_line": high, "content_hash": sha(source.encode())}
    return "context:connected_windows:" + sha(canonical(binding))


def verify_composite(item, inventory, sources, definition_keys, ranked, *, score=None):
    """Independently reconstruct exactly two literal, connected source members."""
    require(item["id"] not in inventory and item["id"].startswith("context:connected_windows:"),
            "Composite must have an ephemeral, nonindexed identity")
    assembly = item.get("assembly")
    require(isinstance(assembly, dict) and assembly.get("kind") == "connected_windows", "Composite assembly schema")
    require(set(assembly) in (
        {"kind", "definition_key", "members", "score_anchor_id", "anchor_rank", "companion_rank"},
        {"kind", "definition_key", "members", "score_anchor_id", "anchor_rank", "companion_rank", "inherited_scores"}),
        "Unknown composite assembly fields")
    members = assembly["members"]
    require(isinstance(members, list) and len(members) == 2, "Composite must contain exactly two members")
    key = assembly["definition_key"]
    require(isinstance(key, list) and len(key) in (4, 5), "Composite canonical definition schema")
    line_map = {}
    for member in members:
        require(set(member) == MEMBER_FIELDS and member["id"] in inventory,
                "Composite member must reference canonical raw inventory")
        full = inventory[member["id"]]
        require(all(member[name] == full[name] for name in
                    ("id", "path", "symbol", "start_line", "end_line", "content_hash")),
                "Composite member original identity/hash changed")
        require(list(definition_keys[member["id"]]) == key, "Composite joins different canonical definitions")
        low, high = member["used_start_line"], member["used_end_line"]
        require(type(low) is type(high) is int and full["start_line"] <= low <= high <= full["end_line"],
                "Composite member used interval outside original window")
        used = literal(sources, member["path"], low, high)
        require(member["used_source_hash"] == sha(used.encode()), "Composite member used source hash")
        for number, line in enumerate(physical_lines(used), low):
            require(number not in line_map or line_map[number] == line, "Composite overlap source mismatch")
            line_map[number] = line
    low, high = min(line_map), max(line_map)
    require(len(line_map) == high - low + 1, "Composite bridged an uncovered source gap")
    full_source = "".join(line_map[number] for number in range(low, high + 1))
    require(full_source == literal(sources, item["path"], low, high), "Composite is not a contiguous literal source union")
    require(item["path"] == key[0] and item["symbol"] == key[1] and item["start_line"] == low,
            "Composite path/symbol/start differs from canonical definition")
    full_end = item.get("window_end_line", item["end_line"])
    require(full_end == high and item["content_hash"] == sha(full_source.encode()), "Composite full bounds/hash")
    if "text" in item:
        require(item["end_line"] == high and item["text"] == full_source, "Composite stage full source")
    require(item["id"] == composite_id(members, key, full_source, low, high), "Composite canonical identity digest")
    anchor, companion = members
    require(low < anchor["used_start_line"] or high > anchor["used_end_line"],
            "Composite does not add any source beyond its anchor")
    anchor_id, companion_id = assembly["score_anchor_id"], companion["id"]
    require(anchor_id == anchor["id"] and anchor_id in ranked and companion_id in ranked,
            "Composite score anchor and companion must be actual raw retrieved IDs")
    require(assembly["anchor_rank"] == ranked[anchor_id]["rank"]
            and assembly["companion_rank"] == ranked[companion_id]["rank"], "Composite raw rank receipt")
    inherited = ranked[anchor_id]["score"]
    if score is not None:
        require(finite(score) and score == inherited, "Composite inherited anchor score")
    if "retrieval_score" in item:
        require(item["retrieval_score"] == inherited, "Composite retrieval score must belong to actual raw anchor")
        require(item.get("h2o_score") is None and item.get("qwen_score") is None,
                "Composite cannot claim direct model/reranker scores")
    if anchor_id == companion_id:
        require(anchor["used_end_line"] < anchor["end_line"]
                and anchor["used_start_line"] == anchor["start_line"]
                and companion["used_start_line"] == companion["start_line"]
                and companion["used_end_line"] == companion["end_line"]
                and companion["used_end_line"] > anchor["used_end_line"],
                "Repeated member ID is only valid for clipped-own-window continuation")
    if "inherited_scores" in assembly:
        recorded = assembly["inherited_scores"]
        require(set(recorded) == {"retrieval_score", "h2o_score", "qwen_score"}
                and recorded["retrieval_score"] == inherited
                and all(recorded[name] is None or finite(recorded[name]) for name in recorded),
                "Composite inherited-score provenance")
    return {"id": item["id"], "path": item["path"], "symbol": item["symbol"],
            "start_line": low, "end_line": high, "text": full_source,
            "content_hash": sha(full_source.encode()), "assembly": deepcopy(assembly)}


def verify_stage(stage, inventory, sources, definition_keys, ranked):
    composites, seen = {}, set()
    for pair in stage:
        item, score = pair["snippet"], pair["score"]
        require(item["id"] not in seen and finite(score), "Stage duplicate ID or nonfinite score")
        seen.add(item["id"])
        if "assembly" in item:
            require(set(item) == SNIPPET_FIELDS | {"assembly"}, "Composite snippet fields")
            composites[item["id"]] = verify_composite(item, inventory, sources, definition_keys, ranked, score=score)
        else:
            require(item["id"] in inventory and set(item) == SNIPPET_FIELDS
                    and item == {name: inventory[item["id"]][name] for name in SNIPPET_FIELDS},
                    "Noncanonical stage source")
    return composites


def verify_response(response, inventory, sources, definition_keys, ranked, counter,
                    *, composites=None, raw_rows=None, max_results=8, chars=12000, tokens=4096):
    known = dict(composites or {})
    rows = response["results"]
    require(len(rows) <= max_results and len({row["id"] for row in rows}) == len(rows), "Excerpt count/ID cap")
    for row in rows:
        if "assembly" in row:
            known[row["id"]] = verify_composite(row, inventory, sources, definition_keys, ranked,
                                                score=row.get("retrieval_score"))
    all_windows = {**inventory, **known}
    for row in rows:
        require(row["id"] in all_windows, "Noncanonical delivered source window")
        full = all_windows[row["id"]]
        require(all(row[name] == full[name] for name in ("path", "symbol", "start_line")), "Actual full-window identity")
        require(type(row["end_line"]) is int and full["start_line"] <= row["end_line"] <= full["end_line"]
                and row["window_end_line"] == full["end_line"], "Actual/window bounds")
        source = literal(sources, row["path"], row["start_line"], row["end_line"])
        require(row["source"] == source and row["source_hash"] == sha(source.encode()), "Actual source/hash")
        require(row["content_hash"] == full["content_hash"] and type(row["truncated"]) is bool
                and row["truncated"] == (source != full["text"]), "Full source/hash/truncation")
        require(row["origin"] in ("retrieval", "dependency") and
                (row["related_to"] is None if row["origin"] == "retrieval" else row["related_to"] in all_windows),
                "Dependency provenance owner")
        if "assembly" in row:
            require(row["origin"] == "retrieval" and row["related_to"] is None,
                    "Composite must retain retrieval-anchor origin")
        if raw_rows is not None:
            owner_id = row.get("assembly", {}).get("score_anchor_id", row["id"])
            if owner_id in raw_rows:
                require(same_provenance(row, raw_rows[owner_id]), "Source/overlay provenance changed")
    require(response["total_source_chars"] == sum(len(row["source"]) for row in rows) <= chars, "Character total/cap")
    usage = response["token_usage"]
    require(usage["encoding"] == "cl100k_base" and usage["budget"] == tokens
            and type(usage["tokens"]) is int and type(usage["truncated"]) is bool, "Token usage schema")
    text = compact_payload(response)
    require(len(counter.encode(text, disallowed_special=())) == usage["tokens"] <= tokens, "Exact compact tokens/cap")
    return text


def verify_guarded_preservation(before, after):
    """Require one actual byte-identical witness per old span and strict rewrites."""
    previous, current = before["results"], after["results"]
    require(len(previous) == len(current), "Guarded continuation changed the prior coverage row count")
    mapping = {}
    for old_row, new_row in zip(previous, current):
        if old_row["id"] != new_row["id"]:
            assembly = new_row.get("assembly", {})
            require(assembly.get("score_anchor_id") == old_row["id"], "Guarded continuation replaced an unrelated old row")
            mapping[old_row["id"]] = new_row["id"]
    for old_row, new_row in zip(previous, current):
        require(any(row["path"] == old_row["path"] and same_provenance(row, old_row)
                    and row["start_line"] <= old_row["start_line"] <= old_row["end_line"] <= row["end_line"]
                    and "".join(physical_lines(row["source"])[old_row["start_line"] - row["start_line"]:
                        old_row["end_line"] - row["start_line"] + 1]) == old_row["source"] for row in current),
                "Guarded continuation lost old actual source bytes")
        if old_row["id"] == new_row["id"]:
            expected = deepcopy(old_row)
            if expected.get("related_to") in mapping:
                expected["related_to"] = mapping[expected["related_to"]]
            require(new_row == expected, "Guarded continuation changed retained old metadata beyond strict owner rewrite")
        else:
            require(new_row["origin"] == old_row["origin"] == "retrieval"
                    and new_row["related_to"] is None and old_row["related_to"] is None
                    and new_row["retrieval_score"] == old_row["retrieval_score"]
                    and new_row["start_line"] == old_row["start_line"]
                    and new_row["end_line"] > old_row["end_line"], "Guarded anchor extension metadata")
            recorded = new_row["assembly"].get("inherited_scores")
            require(recorded == {name: old_row.get(name) for name in
                                 ("retrieval_score", "h2o_score", "qwen_score")}, "Guarded inherited scores changed")
    require(not before.get("budget_exhausted") or after.get("budget_exhausted"), "Guarded exhaustion flag lost")
    require(not before["token_usage"]["truncated"] or after["token_usage"]["truncated"], "Guarded token truncation flag lost")
    debug = after.get("_window_assembly", {})
    require(debug.get("old_to_new_ids") == mapping and debug.get("coverage_preserved") is True,
            "Guarded preservation diagnostic differs from actual rows")
    require(type(debug.get("trials")) is int and 0 <= debug["trials"] <= 8
            and debug.get("trial_limit") == 8, "Guarded bounded-work diagnostic")
    applied = debug.get("applied")
    require(isinstance(applied, list) and {row["anchor_id"]: row["id"] for row in applied} == mapping
            and len(applied) == len(mapping), "Guarded applied receipt differs from actual replacements")


def verify_guarded_union(row, coverage_control):
    reference, proposal, final = row["reference_response"], row["proposal_response"], row["response"]
    if "guard_calls" in row:
        require(row["guard_calls"] == 1, "Guard predicate must run exactly once")
    require(reference["results"] == coverage_control["results"]
            and compact_payload(reference) == compact_payload(coverage_control)
            and all(reference[name] == coverage_control[name] for name in
                    ("total_source_chars", "budget_exhausted", "token_usage")),
            "Guard reference differs from actual same-input coverage control")
    accepted = all(any(covers_actual(outer, inner) for outer in proposal["results"])
                   for inner in reference["results"])
    debug = final.get("_window_guard")
    require(debug == {"accepted": accepted,
        "fallback_reason": None if accepted else "coverage_control_source_not_preserved",
        "reference_compact_sha256": sha(compact_payload(reference).encode()),
        "proposed_composites": sum("assembly" in item for item in proposal["results"]),
        "accepted_composites": sum("assembly" in item for item in final["results"])},
        "Guard decision/necessity receipt does not match actual proposal bytes")
    expected = deepcopy(proposal if accepted else reference)
    expected["_window_guard"] = debug
    require(final == expected, "Guard changed accepted proposal or exact coverage fallback")


def evidence_ranks(rows, groups, *, qualified=False):
    """Use historical semantics for old controls; fresh labels require symbols."""
    result = []
    for alternatives in groups:
        found = None
        for rank, row in enumerate(rows, 1):
            for target in alternatives:
                low, high = target["evidence_lines"]
                if row["path"] != target["path"] or not row["start_line"] <= low <= high <= row["end_line"]:
                    continue
                if qualified and row["symbol"] != target["symbol"]:
                    continue
                if qualified:
                    source = "".join(physical_lines(row["source"])[low - row["start_line"]:high - row["start_line"] + 1])
                    if sha(source.encode()) != target["evidence_sha256"]:
                        continue
                found = rank
                break
            if found is not None:
                break
        result.append(found)
    return result


def changes(before, after, groups, *, qualified=False):
    a = evidence_ranks(before["results"], groups, qualified=qualified)
    b = evidence_ranks(after["results"], groups, qualified=qualified)
    gains, losses = [], []
    for number, (left, right, targets) in enumerate(zip(a, b, groups), 1):
        if (left is None) == (right is None):
            continue
        item = {"group": number, "evidence": targets, "baseline_rank": left, "variant_rank": right}
        (gains if left is None else losses).append(item)
    return {"baseline_ranks": a, "evidence_ranks": b, "group_gains": gains, "group_losses": losses,
            "complete_gain": bool(groups) and None in a and None not in b,
            "complete_loss": bool(groups) and None not in a and None in b}


def comparison(before, after, groups):
    left, right, gains, losses = old.group_changes(before, after, groups)
    return {"control_ranks": left, "variant_ranks": right, "group_gains": gains, "group_losses": losses,
            "complete_gain": bool(groups) and None in left and None not in right,
            "complete_loss": bool(groups) and None not in left and None in right}


def sources_from_fixture(corpus_root, fixture):
    dataset = fixture["dataset"]
    folder = {"jinja": "jinja-multifile-holdout", "urllib3": "urllib3-selection-holdout"}.get(dataset, dataset)
    return old.source_fixture(corpus_root, folder, fixture["source_sha256"], fixture["inventory"])


def original_cases(root, corpus_root):
    """Reconstruct saved candidates and controls from historical data only."""
    cases = old.load_cases(root, corpus_root)
    by_case = {case_key(case): case for case in cases}

    def attach(subset, dataset, condition, row, candidates, scores=None):
        query = row.get("query_id", row.get("identity", {}).get("query_id"))
        case = by_case[(subset, dataset, query, condition)]
        pairs = []
        for item in candidates:
            item = item.get("snippet", item)
            full = case["inventory"][item["id"]]
            require(all(item[name] == full[name] for name in ("path", "symbol", "start_line", "end_line")),
                    "Historical raw candidate identity")
            require(item.get("source_hash", item.get("content_hash")) == full["content_hash"],
                    "Historical raw candidate source hash")
            score = scores[item["id"]] if scores is not None else item["retrieval_score"]
            pairs.append({"snippet": {name: full[name] for name in SNIPPET_FIELDS}, "score": score})
        case["ranked"] = pairs

    for row in json_rows(root / "results/language-quality-2026-10-13/searches.jsonl"):
        if row["repetition"] == 1:
            attach("language_observed", row["dataset"], row["condition"], row, row["candidate_windows"])
    for dataset in ("express", "zod", "jinja"):
        for row in json_rows(root / ("results/multifile-" + dataset + "-2026-10-19/searches.jsonl.gz")):
            if row["repetition"] == 1 and row["retrieval"] in ("dense", "hybrid"):
                attach("native_" + dataset + "_observed", dataset, row["retrieval"], row, row["stages"]["ranked"])
    for subset, folder in (("qwen_development_observed", "qwen-shortlist-development-2026-10-14"),
                           ("qwen_httpx_observed", "qwen-shortlist-httpx-2026-10-14")):
        path = root / "results" / folder
        pools = {pool["pool_id"]: pool for pool in json_rows(path / "candidate-pools.jsonl.gz")}
        for row in json_rows(path / "searches.jsonl.gz"):
            if row["identity"]["repetition"] != 1 or row["configuration"]["id"] != "hybrid_k24_b1":
                continue
            pool = {item["id"]: item for item in pools[row["pool_id"]]["windows"]}
            scores = dict(zip(row["shortlist_original_order"],
                              [score["logit_difference"] for score in row["reranker"]["results"]]))
            attach(subset, row["identity"]["dataset"], "hybrid_k24_b1", row,
                   [pool[identifier] for identifier in row["ranking_order"]], scores)
    folder = root / "results/selection-holdout-urllib3-2026-10-21"
    plan = read(folder / "plan.json")
    document = read(folder / "source-inventory.json")
    fixture = old.source_fixture(corpus_root, "urllib3-selection-holdout", plan["binding"]["source_sha256"], document["inventory"])
    queries = {q["id"]: q for q in read(root / plan["binding"]["dataset_path"])["queries"]}
    for row in json_rows(folder / "searches.jsonl.gz"):
        if row["repetition"] == 1 and row["arm"] == "baseline":
            cases.append({"subset": "native_urllib3_observed", "dataset": "urllib3", "query_id": row["query_id"],
                          "condition": row["retrieval"], "kind": row["kind"], "groups": old.evidence_groups(queries[row["query_id"]]),
                          "multi_file": row["multi_file"], "original": old.ordered_original(row["response"]),
                          "ranked": row["stages"]["ranked"], **fixture})
    require(len(cases) == 365 and len({case_key(case) for case in cases}) == 365
            and len({(case["dataset"], case["query_id"]) for case in cases}) == 187, "Exact original 365-row/187-unit matrix")
    require(all("ranked" in case for case in cases), "Missing historical ranked pool")
    return cases


def verify_full_rows(rows, pairs, inventory, sources, definition_keys, ranked):
    require(len(rows) == len(pairs), "Full candidate receipt count")
    composites = {}
    for row, pair in zip(rows, pairs):
        item = pair["snippet"]
        require(row["id"] == item["id"] and row["origin"] == "retrieval" and row["related_to"] is None,
                "Full candidate order/origin")
        if "assembly" in item:
            full = verify_composite(row, inventory, sources, definition_keys, ranked, score=pair["score"])
            composites[row["id"]] = full
        else:
            full = inventory[item["id"]]
        require(all(row[name] == full[name] for name in ("path", "symbol", "start_line", "end_line", "content_hash"))
                and row["window_end_line"] == full["end_line"] and row["source"] == full["text"]
                and row["source_hash"] == full["content_hash"] and row["truncated"] is False
                and row["retrieval_score"] == pair["score"] and row.get("h2o_score") is None and row.get("qwen_score") is None,
                "Full candidate source/score/metadata changed")
    return composites


def covers_actual(outer, inner):
    return (outer["path"] == inner["path"] and same_provenance(outer, inner)
            and outer["start_line"] <= inner["start_line"] <= inner["end_line"] <= outer["end_line"]
            and "".join(physical_lines(outer["source"])[inner["start_line"] - outer["start_line"]:
                        inner["end_line"] - outer["start_line"] + 1]) == inner["source"])


def verify_trace(row, fixture, counter, *, expected_ranked=None):
    inventory, sources, keys = fixture["inventory"], fixture["sources"], fixture["definition_keys"]
    stages = row["stages"]
    ranked = raw_ranked(stages["ranked"], inventory)
    if expected_ranked is not None:
        require(stages["ranked"] == expected_ranked, "Frozen original candidate order/scores changed")
    composites = {}
    for name in ("selected", "eligible"):
        composites.update(verify_stage(stages[name], inventory, sources, keys, ranked))
    require(len(stages["eligible"]) <= 8, "Eligible result cap")
    expected_keys = {identifier: list(keys[identifier]) for identifier in
                     {pair["snippet"]["id"] for name in ("ranked", "selected", "eligible") for pair in stages[name]}
                     if identifier in keys}
    expected_keys.update({identifier: value["assembly"]["definition_key"] for identifier, value in composites.items()})
    # Native captures the resident complete map; replay records only relevant IDs.
    actual_keys = {identifier: list(value) for identifier, value in stages["definition_keys"].items()}
    require(actual_keys == expected_keys or actual_keys == {i: list(k) for i, k in keys.items()}
            or actual_keys == {**{i: list(k) for i, k in keys.items()},
                               **{i: c["assembly"]["definition_key"] for i, c in composites.items()}},
            "Stage canonical definition map changed")
    labels = {}
    for key in keys.values():
        labels.setdefault(tuple(key[:2]), set()).add(tuple(key))
    ambiguous = [list(label) for label, values in sorted(labels.items()) if len(values) > 1]
    require(stages["ambiguous_symbols"] == ambiguous, "Ambiguous display-label set changed")
    composites.update(verify_full_rows(row["backfill_candidates"], stages["selected"], inventory, sources, keys, ranked))
    verify_full_rows(row["ranked_candidates"], stages["ranked"], inventory, sources, keys, ranked)
    raw_rows = {item["id"]: item for item in row["ranked_candidates"]}
    for item in row["response"]["results"]:
        if "assembly" in item:
            composites[item["id"]] = verify_composite(item, inventory, sources, keys, ranked,
                                                      score=item.get("retrieval_score"))
    for name in ("pre_postprocess_response", "response", "proposal_response", "reference_response"):
        if name not in row or row[name] is None:
            continue
        verify_response(row[name], inventory, sources, keys, ranked, counter,
                        composites=composites, raw_rows=raw_rows)
    allowed = {pair["snippet"]["id"]: pair for pair in stages["eligible"]}
    before = row["pre_postprocess_response"]
    require([item["id"] for item in before["results"]] == [pair["snippet"]["id"] for pair in stages["eligible"]
             if pair["snippet"]["id"] in {item["id"] for item in before["results"]}], "Packing changed eligible row order")
    origins = stages["origins"]
    for item in before["results"]:
        require(item["id"] in allowed and [item["origin"], item["related_to"]] == origins[item["id"]]
                and item["retrieval_score"] == allowed[item["id"]]["score"], "Packed eligible score/origin changed")
    all_windows = {**inventory, **composites}
    for response in (before, row["response"]):
        for item in response["results"]:
            if item["origin"] == "dependency":
                owner = item["related_to"]
                if owner in composites:
                    owner = composites[owner]["assembly"]["score_anchor_id"]
                require(owner in ranked and item["retrieval_score"] == ranked[owner]["score"],
                        "Dependency owner has no real raw score anchor")
            elif "assembly" not in item:
                require(item["id"] in ranked and item["retrieval_score"] == ranked[item["id"]]["score"],
                        "Retrieval row has no real raw score anchor")
    condition = row.get("selector", row.get("condition"))
    if condition != "guarded_connected_union":
        require(row.get("reference_response") is None and row.get("proposal_response") is None
                and row.get("guard_calls", 0) == 0, "Unexpected reference or guard work in an unguarded condition")
    if condition not in ("legacy", "guarded_connected_union"):
        require(all(any(covers_actual(outer, inner) for outer in row["response"]["results"])
                    for inner in before["results"]), "Postprocessing lost actual prepacked source")
    text = compact_payload(row["response"])
    require(row["compact_payload"] == text and row["compact_sha256"] == sha(text.encode())
            and row["actual_compact_tokens"] == row["response"]["token_usage"]["tokens"], "Exact compact receipt differs")
    return ranked


def development_summary(rows, counts):
    metrics, changes_by_condition, qualifications = {}, {}, {}
    for condition in CONDITIONS:
        selected = [row for row in rows if row["selector"] == condition]
        strata = sorted({(row["subset"], row["dataset"], row["condition"]) for row in selected})
        metrics[condition] = {}
        for stratum in strata:
            group = [row for row in selected if tuple(row[name] for name in ("subset", "dataset", "condition")) == stratum]
            value = old.metrics(group)
            value["assembled_rows"] = sum(any("assembly" in item for item in row["response"]["results"]) for row in group)
            metrics[condition]["/".join(stratum)] = value
        controls = {}
        for control in ("legacy", "coverage_control"):
            gains, losses, complete_gains, complete_losses, first_losses = [], [], [], [], []
            for row in selected:
                value = row["comparisons"][control]
                identity = old.identity(row)
                if value["group_gains"]:
                    gains.append({**identity, "groups": value["group_gains"]})
                if value["group_losses"]:
                    losses.append({**identity, "groups": value["group_losses"]})
                if value["complete_gain"]:
                    complete_gains.append(identity)
                if value["complete_loss"]:
                    complete_losses.append(identity)
                if row["kind"] == "present" and not row["multi_file"] and value["control_ranks"] \
                        and value["control_ranks"][0] is not None and value["variant_ranks"][0] is None:
                    first_losses.append(identity)
            controls[control] = {"group_gains": gains, "group_losses": losses, "complete_gains": complete_gains,
                                 "complete_losses": complete_losses,
                                 "unique_complete_gains": sorted({r["dataset"] + "/" + r["query_id"] for r in complete_gains}),
                                 "single_file_first_hit_losses": first_losses}
        changes_by_condition[condition] = controls
        if condition not in ("legacy", "coverage_control"):
            lost = {name: {"lost_groups": sum(len(r["groups"]) for r in value["group_losses"]),
                           "lost_complete_rows": len(value["complete_losses"]),
                           "lost_single_file_first_hits": len(value["single_file_first_hit_losses"])}
                    for name, value in controls.items()}
            added = controls["coverage_control"]["unique_complete_gains"]
            qualifications[condition] = {"qualified": bool(added) and not any(v for control in lost.values() for v in control.values()),
                "against_controls": lost, "additional_distinct_complete_queries_vs_coverage_control": added,
                "gained_additional_distinct_complete_queries": len(added),
                "requires_fresh_native_validation": True, "automatic_default_change": False}
    return {"classification": "Observed-source CPU DEVELOPMENT complementary-window replay", "counts": counts,
            "metrics_by_study_dataset_original_condition": metrics, "all_changes": changes_by_condition,
            "qualification": qualifications, "model_calls": 0, "retrieval_calls": 0, "network_calls": 0,
            "automatic_default_change": False}


def git_bytes(root, commit, path):
    return subprocess.check_output(["git", "-C", str(root), "show", commit + ":" + path])


def historical_bindings(root, plan_path, plan, started, artifacts):
    """Resolve the exact committed execution bytes, explicitly noting later edits."""
    bound = {**plan.get("producer_sha256", plan.get("binding", {}).get("implementation_sha256", {})),
             **plan.get("input_sha256", {})}
    native = plan.get("binding", {})
    if native.get("development_parent"):
        bound.update(native["development_parent"]["artifacts"])
    bound.update(native.get("definition_preflight") or {})
    frozen_files = {plan_path.relative_to(root).as_posix(): sha(plan_path.read_bytes())}
    for name in ("baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json", "source-inventory.json"):
        if name in artifacts:
            frozen_files[(plan_path.parent / name).relative_to(root).as_posix()] = artifacts[name]
    chain = old.committed_freeze(root, plan_path, {**bound, **frozen_files},
                                started.get("created_at_utc", started.get("started_at_utc")))
    execution = started["execution_commit"]
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", chain["commit"], execution])
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", execution, "HEAD"])
    resolutions = {}
    for path, expected in {**bound, **frozen_files}.items():
        require(sha(git_bytes(root, execution, path)) == expected, "Historical execution blob differs: " + path)
        current = sha((root / path).read_bytes())
        if current != expected:
            require(path in plan.get("producer_sha256", plan.get("binding", {}).get("implementation_sha256", {})),
                    "Frozen nonproducer input artifact changed: " + path)
            resolutions[path] = {"frozen_sha256": expected, "current_sha256": current,
                "resolution": "Exact committed producer execution bytes are verified as data, never executed; later working bytes are not attributed to this historical run"}
    return chain, execution, resolutions


def audit_development(root, corpus_root, output, counter):
    names = ("frozen-plan.json", "source-fixtures.json", "baseline-gate.json", "prior-coverage-gate.json",
             "run-start.json", "control-gate.json", "replay.jsonl.gz", "summary.json")
    artifacts = {name: sha((output / name).read_bytes()) for name in names}
    require(not (output / "failure.json").exists(), "Producer attempt failed; retain its exclusive failure receipt")
    plan, summary = read(output / "frozen-plan.json"), read(output / "summary.json")
    start, gate = read(output / "run-start.json"), read(output / "control-gate.json")
    legacy_gate, coverage_gate = read(output / "baseline-gate.json"), read(output / "prior-coverage-gate.json")
    require(tuple(plan["conditions"]) == CONDITIONS and plan["settings"] == {
        "max_results": 8, "source_budget": 12000, "token_budget": 4096,
        "include_dependencies": True, "rerank": False, "candidates": 50}, "Declared fixed conditions/caps")
    require(plan["historical_baseline_commit"] == "5d1e80062638e19101acf3b9e8543066590fd1c5", "Historical baseline anchor")
    for path, expected in plan["input_sha256"].items():
        require(sha((root / path).read_bytes()) == expected
                and sha(git_bytes(root, plan["historical_baseline_commit"], path)) == expected,
                "Original historical input changed: " + path)
    require(plan["artifacts"]["source-fixtures.json"] == artifacts["source-fixtures.json"], "Frozen source-fixture artifact hash")
    require(start["plan_sha256"] == gate["plan_sha256"] == legacy_gate["plan_sha256"]
            == coverage_gate["plan_sha256"] == summary["plan_sha256"] == artifacts["frozen-plan.json"], "Plan bindings")
    require(summary["replay_sha256"] == artifacts["replay.jsonl.gz"] and gate["new_variant_rows_before_gate"] == 0
            and legacy_gate["new_variant_outcomes_computed"] == coverage_gate["new_variant_outcomes_computed"] == 0,
            "All exact historical controls must precede variants")
    chain, execution, resolutions = historical_bindings(root, output / "frozen-plan.json", plan, start, artifacts)
    fixture_document = read(output / "source-fixtures.json")
    fixtures = []
    for name, frozen in fixture_document["fixtures"].items():
        fixture = sources_from_fixture(corpus_root, frozen)
        fixture["inventory"] = {identifier: {field: item[field] for field in SNIPPET_FIELDS}
                                for identifier, item in fixture["inventory"].items()}
        fixture.update(definition_keys=frozen["definition_keys"], dataset=frozen["dataset"], source_sha256=frozen["source_sha256"])
        verify_inventory(fixture["inventory"], fixture["sources"], fixture["definition_keys"])
        require(plan["source_sha256"][name] == frozen["source_sha256"], "Frozen full source matrix differs")
        fixtures.append(fixture)
    require(set(fixture_document["fixtures"]) == set(plan["source_sha256"]), "Complete frozen source-fixture matrix")
    cases = original_cases(root, corpus_root)
    by_case = {case_key(case): case for case in cases}
    for case in cases:
        case["inventory"] = {identifier: {field: item[field] for field in SNIPPET_FIELDS}
                             for identifier, item in case["inventory"].items()}
        matched = [fixture for fixture in fixtures if fixture["dataset"] == case["dataset"]
                   and fixture["sources"] == case["sources"]]
        require(len(matched) == 1, "Each historical case needs exactly one source-only fixture")
        case["definition_keys"] = matched[0]["definition_keys"]
        case["source_sha256"] = matched[0]["source_sha256"]
        require(case["inventory"] == matched[0]["inventory"], "Canonical historical source inventory changed")
        for alternatives in case["groups"]:
            for target in alternatives:
                low, high = target["evidence_lines"]
                require(sha(literal(case["sources"], target["path"], low, high).encode()) == target["evidence_sha256"],
                        "Historical full physical evidence interval/hash")
                if "source_file_sha256" in target:
                    require(target["source_file_sha256"] == case["source_sha256"][target["path"]],
                            "Historical evidence source-file hash")
    previous = {case_key(row): row for row in json_rows(root / "results/selection-development-v2-2026-10-21/replay.jsonl.gz")
                if row["selector"] == "definition_coverage_backfill"}
    controls, known = [], []
    for case in cases:
        old.verify_response(case["original"], case["inventory"], case["sources"], counter)
        controls.append({**old.identity(case), "compact_sha256": sha(compact_payload(case["original"]).encode())})
        if case_key(case) in previous:
            saved = previous[case_key(case)]
            known.append({**old.identity(case), "compact_sha256": saved["compact_sha256"],
                          "response_sha256": sha(canonical(saved["response"])), "evidence_ranks": saved["evidence_ranks"]})
    require(len(controls) == legacy_gate["exact_rows"] == gate["exact_legacy_rows"] == summary["baseline_exact_rows"] == 365,
            "All 365 exact legacy controls required")
    require(len(known) == coverage_gate["exact_rows"] == gate["exact_prior_coverage_rows"] == summary["prior_coverage_exact_rows"] == 337,
            "All 337 exact prior coverage controls required")
    require(controls == legacy_gate["cases"] == gate["baseline_cases"]
            and sha(canonical(controls)) == plan["baseline_controls_sha256"], "Exact historical legacy control payloads")
    require(known == coverage_gate["cases"] == gate["coverage_cases"]
            and sha(canonical(known)) == plan["prior_coverage_controls_sha256"], "Exact historical prior-best coverage payloads")
    rows = json_rows(output / "replay.jsonl.gz")
    index = {(row["selector"], *case_key(row)): row for row in rows}
    require(len(rows) == 365 * len(CONDITIONS) and len(index) == len(rows)
            and set(index) == {(name, *case_key(case)) for name in CONDITIONS for case in cases}, "Complete five-condition outcome matrix")
    for row in rows:
        case = by_case[case_key(row)]
        require(row["kind"] == case["kind"] and row["multi_file"] == case["multi_file"]
                and row["evidence_groups"] == case["groups"], "Historical case/gold identity")
        require(row["response"]["query"] == case["original"]["query"]
                and row["response"]["scope"] == case["original"]["scope"], "Historical query/scope changed")
        verify_trace(row, case, counter, expected_ranked=case["ranked"])
        require(row["evidence_ranks"] == evidence_ranks(row["response"]["results"], case["groups"]), "Historical evidence ranks")
        if row["selector"] == "legacy":
            require(row["response"]["results"] == case["original"]["results"]
                    and compact_payload(row["response"]) == compact_payload(case["original"])
                    and all(row["response"][name] == case["original"][name] for name in
                            ("total_source_chars", "budget_exhausted", "token_usage")),
                    "Legacy literal payload or full original result metadata changed")
        if row["selector"] == "coverage_control" and case_key(row) in previous:
            saved = previous[case_key(row)]
            require(row["response"] == saved["response"] and row["compact_sha256"] == saved["compact_sha256"]
                    and row["evidence_ranks"] == saved["evidence_ranks"], "Prior-best full response changed")
        for control in ("legacy", "coverage_control"):
            require(row["comparisons"][control] == comparison(index[(control, *case_key(row))]["response"],
                    row["response"], case["groups"]), "All group/complete changes against both controls")
        if row["selector"] == "guarded_continuation":
            verify_guarded_preservation(index[("coverage_control", *case_key(row))]["response"], row["response"])
        if row["selector"] == "guarded_connected_union":
            verify_guarded_union(row, index[("coverage_control", *case_key(row))]["response"])
    expected = development_summary(rows, plan["counts"])
    expected.update(baseline_exact_rows=365, prior_coverage_exact_rows=337,
                    plan_sha256=artifacts["frozen-plan.json"], replay_sha256=artifacts["replay.jsonl.gz"])
    require(summary == expected, "Independent complete development summary/qualification differs")
    require(plan["counts"]["observed_rows"] == 365 and plan["counts"]["unique_queries"] == 187,
            "Observed condition rows must not inflate independent query units")
    require(all(start[name] == summary[name] == 0 for name in ("model_calls", "retrieval_calls", "network_calls")), "Zero producer-call declarations")
    require(all(sha((output / name).read_bytes()) == value for name, value in artifacts.items()), "Bound artifacts changed during independent audit")
    return {"status": "passed", "checks_passed": True, "mode": "development", "artifact_sha256": artifacts,
            "conditions": list(CONDITIONS), "exact_legacy_controls": 365, "exact_prior_coverage_controls": 337,
            "verified_outcome_rows": len(rows), "distinct_dataset_query_units": 187,
            "independently_recomputed_summary": expected, "producer_freeze": chain, "producer_execution": execution,
            "bound_file_resolutions": resolutions, "source_sha256": plan["source_sha256"],
            "limits": "Historical path/full-interval scoring is retained within development strata; fresh qualified-symbol/hash scoring is a separate criterion and must not be pooled across corpora."}


def active(row):
    return any("assembly" in item for item in row["response"]["results"]) or any(
        "assembly" in pair["snippet"] for pair in row["stages"]["selected"])


def native_summary(rows):
    result = {}
    for mode in ("dense", "hybrid"):
        selected = [row for row in rows if row["retrieval"] == mode]
        first = {arm: {row["query_id"]: row for row in selected if row["repetition"] == 1 and row["arm"] == arm}
                 for arm in ("legacy", "coverage_control", "candidate")}
        arms = {}
        for arm in first:
            own = [row for row in selected if row["arm"] == arm]
            positive = [row for row in first[arm].values() if row["kind"] == "present"]
            times = sorted(row["elapsed_backend_s"] for row in own)
            arms[arm] = {"measured_calls": len(own), "median_backend_s": statistics.median(times),
                "p95_backend_s": times[math.ceil(.95 * len(times)) - 1],
                "present_queries": len(positive), "multi_file_queries": sum(row["multi_file"] for row in positive),
                "complete_positives": sum(None not in row["delivered_ranks"] for row in positive),
                "multi_file_complete": sum(row["multi_file"] and None not in row["delivered_ranks"] for row in positive),
                "required_groups": sum(len(row["delivered_ranks"]) for row in positive),
                "covered_groups": sum(sum(rank is not None for rank in row["delivered_ranks"]) for row in positive),
                "absent_inventory": [{"query_id": row["query_id"], "match_status": row["response"]["match_status"],
                                      "excerpt_ids": [item["id"] for item in row["response"]["results"]]}
                                     for row in first[arm].values() if row["kind"] == "absent"],
                "coalesced_rows": sum(len(row["response"].get("_selection_backfill", {}).get("coalesced_ids", [])) for row in first[arm].values()),
                "appended_rows": sum(len(row["response"].get("_selection_backfill", {}).get("appended_ids", [])) for row in first[arm].values()),
                "delivered_composites": sum(sum("assembly" in item for item in row["response"]["results"]) for row in first[arm].values()),
                "active_composite_calls": sum(active(row) for row in own)}
        controls = {}
        for control in ("legacy", "coverage_control"):
            gains, losses, complete_gains, complete_losses, first_losses = [], [], [], [], []
            net = 0
            for identifier, before in first[control].items():
                after = first["candidate"][identifier]
                if before["kind"] == "absent":
                    continue
                a, b = before["delivered_ranks"], after["delivered_ranks"]
                if None in a and None not in b:
                    complete_gains.append(identifier)
                    net += bool(before["multi_file"])
                if None not in a and None in b:
                    complete_losses.append(identifier)
                    net -= bool(before["multi_file"])
                for number, (left, right) in enumerate(zip(a, b), 1):
                    value = {"query_id": identifier, "group": number, "control_rank": left, "candidate_rank": right}
                    if left is None and right is not None:
                        gains.append(value)
                    if left is not None and right is None:
                        losses.append(value)
                if not before["multi_file"] and a and a[0] is not None and b[0] is None:
                    first_losses.append(identifier)
            controls[control] = {"complete_gains": complete_gains, "complete_losses": complete_losses,
                "group_gains": gains, "group_losses": losses, "single_file_first_hit_losses": first_losses,
                "net_multi_file_complete_gain": net, "preserved": not losses and not complete_losses and not first_losses}
        ratio = arms["candidate"]["median_backend_s"] / arms["legacy"]["median_backend_s"]
        ratios = {control: arms["candidate"]["median_backend_s"] / arms[control]["median_backend_s"]
                  for control in ("legacy", "coverage_control")}
        base_times = {(row["query_id"], row["repetition"]): row["elapsed_backend_s"] for row in selected if row["arm"] == "legacy"}
        candidates = [row for row in selected if row["arm"] == "candidate"]
        activated = [row for row in candidates if active(row)]
        deltas = [row["elapsed_backend_s"] - base_times[(row["query_id"], row["repetition"])] for row in candidates]
        active_deltas = [row["elapsed_backend_s"] - base_times[(row["query_id"], row["repetition"])] for row in activated]
        result[mode] = {"arms": arms, "against_controls": controls, "median_wall_ratio_to_legacy": ratio,
            "median_wall_ratio_to_controls": ratios,
            "median_paired_wall_delta_s_to_legacy": statistics.median(deltas),
            "active_composite_overhead": {"criterion": "Composite in selected stage or final delivered response",
                "samples": len(activated), "median_paired_wall_delta_s": statistics.median(active_deltas) if activated else None,
                "median_wall_ratio_to_legacy": statistics.median(row["elapsed_backend_s"] for row in activated) /
                    statistics.median(base_times[(row["query_id"], row["repetition"])] for row in activated) if activated else None},
            "passed": all(control["preserved"] for control in controls.values())
                and controls["coverage_control"]["net_multi_file_complete_gain"] >= 1
                and all(value <= 1.25 for value in ratios.values())}
    return {"per_retrieval": result, "dense_promotion_gate_passed": result["dense"]["passed"], "automatic_default_change": False}


def audit_native(root, corpus, output, counter):
    names = ("plan.json", "source-inventory.json", "run-started.json", "searches.jsonl.gz", "summary.json")
    artifacts = {name: sha((output / name).read_bytes()) for name in names}
    require(not (output / "failure.json").exists(), "Failed native attempt cannot be certified complete")
    plan, summary, start = read(output / "plan.json"), read(output / "summary.json"), read(output / "run-started.json")
    binding = plan["binding"]
    require(summary["complete"] is True and summary["plan_sha256"] == start["plan_sha256"] == artifacts["plan.json"]
            and summary["raw_sha256"] == artifacts["searches.jsonl.gz"]
            and plan["artifacts"]["source-inventory.json"] == artifacts["source-inventory.json"], "Native raw/plan bindings")
    require(binding["candidate"] in ("connected_union", "guarded_continuation", "guarded_connected_union")
            and binding["arms"] == {"legacy": "legacy", "coverage_control": "coverage_control", "candidate": binding["candidate"]}
            and binding["retrieval"] == ["dense", "hybrid"] and binding["repetitions"] == 3
            and plan["quality_repetition"] == 1, "Fixed native arms/modes/repetitions")
    require(binding["settings"] == {"max_results": 8, "source_budget": 12000, "token_budget": 4096,
            "include_dependencies": True, "rerank": False, "candidates": 50}, "Native context caps")
    embedding = binding["embedding"]
    require(embedding["id"] == "Qwen/Qwen3-Embedding-0.6B"
            and embedding["revision"] == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
            and embedding["dimensions"] == 1024 and embedding["quantized"] is False
            and binding["model_files_sha256"]["model.safetensors"]
            == "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd",
            "Pinned original Qwen checkpoint/weight receipt")
    expected_gate = {"minimum_net_multi_file_complete_gain": 1, "maximum_lost_groups": 0,
        "maximum_lost_complete_positives": 0, "maximum_lost_single_file_first_hits": 0,
        "maximum_median_wall_ratio": 1.25, "promotion_mode": "dense",
        "hybrid_role": "separate diagnostic control; cannot rescue dense",
        "preservation_controls": ["legacy", "coverage_control"], "incremental_gain_control": "coverage_control"}
    require(binding["gate"] == summary["gate"] == expected_gate, "Frozen native incremental quality/latency gate")
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
    verify_inventory(fixture["inventory"], fixture["sources"], fixture["definition_keys"])
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
    parent = binding["development_parent"]
    require(parent and parent["qualification"]["qualified"] is True
            and parent["qualification"]["gained_additional_distinct_complete_queries"] >= 1
            and all(value == 0 for loss in parent["qualification"]["against_controls"].values() for value in loss.values()),
            "Candidate lacks incremental development qualification against both controls")
    parent_audits = [read(root / path) for path in parent["artifacts"] if path.endswith("/independent-audit.json")]
    require(len(parent_audits) == 1 and parent_audits[0].get("checks_passed") is True
            and parent_audits[0].get("status") == "passed" and parent_audits[0]["exact_legacy_controls"] == 365
            and parent_audits[0]["exact_prior_coverage_controls"] == 337
            and parent_audits[0]["independently_recomputed_summary"]["qualification"][binding["candidate"]] == parent["qualification"],
            "Candidate development parent was not independently audited")
    chain, execution, resolutions = historical_bindings(root, output / "plan.json", plan, start, artifacts)
    queries = {query["id"]: query for query in data["queries"]}
    require(plan["queries"] == [{name: query[name] for name in ("id", "query", "kind")} for query in data["queries"]], "Frozen literal native query order")
    for query in queries.values():
        for alternatives in old.evidence_groups(query):
            for target in alternatives:
                low, high = target["evidence_lines"]
                require(sha(literal(fixture["sources"], target["path"], low, high).encode()) == target["evidence_sha256"]
                        and binding["source_sha256"][target["path"]] == target["source_file_sha256"], "Exact fresh target source/hash")
    rows = json_rows(output / "searches.jsonl.gz")
    schedule, modes = [], [(arm, mode) for mode in ("dense", "hybrid") for arm in ("legacy", "coverage_control", "candidate")]
    for repetition in range(1, 4):
        for index, query in enumerate(data["queries"]):
            offset = (index + repetition - 1) % 6
            schedule.extend((repetition, query["id"], arm, mode) for arm, mode in modes[offset:] + modes[:offset])
    require([(row["repetition"], row["query_id"], row["arm"], row["retrieval"]) for row in rows] == schedule,
            "Exact three-arm rotated native schedule")
    indexed = {(row["query_id"], row["retrieval"], row["repetition"], row["arm"]): row for row in rows}
    vectors, rankings, payloads = {}, {}, {}
    for row in rows:
        query = queries[row["query_id"]]
        groups = old.evidence_groups(query)
        require(row["kind"] == query["kind"] and row["condition"] == binding["arms"][row["arm"]]
                and row["multi_file"] == (len({target["path"] for group in groups for target in group}) > 1), "Native query/arm identity")
        require(finite(row["elapsed_backend_s"]) and row["elapsed_backend_s"] > 0, "Native monotonic duration")
        verify_trace(row, fixture, counter)
        require(row["response"]["query"] == row["pre_postprocess_response"]["query"] == query["query"], "Native literal response query")
        for name in ("response", "pre_postprocess_response", "reference_response", "proposal_response"):
            if row.get(name) is not None:
                scope = row[name]["scope"]
                require(scope["source_root"] == data["source_root"]
                        and scope["source_exclusions"] == data["source_exclusions"], "Native literal authored source scope")
        raw_for_scoring = [{**pair["snippet"], "source": pair["snippet"]["text"]} for pair in row["stages"]["ranked"]]
        require(evidence_ranks(raw_for_scoring, groups, qualified=True) == row["candidate_ranks"]
                and evidence_ranks(row["response"]["results"], groups, qualified=True) == row["delivered_ranks"],
                "Strict qualified-symbol/exact-byte fresh evidence ranks")
        if row["arm"] == "legacy":
            require(row["response"] == row["pre_postprocess_response"], "Legacy postprocessor changed native response")
        if row["condition"] == "guarded_continuation":
            control = indexed[(row["query_id"], row["retrieval"], row["repetition"], "coverage_control")]
            verify_guarded_preservation(control["response"], row["response"])
        if row["condition"] == "guarded_connected_union":
            control = indexed[(row["query_id"], row["retrieval"], row["repetition"], "coverage_control")]
            verify_guarded_union(row, control["response"])
        vector = row["query_vector"]
        require(row["query_embedding_calls"] == 1 and vector and all(finite(value) for value in vector), "One real fresh query embedding receipt")
        require(len(vector) == 1024 and abs(sum(value * value for value in vector) - 1) <= .001,
                "Native query vector dimensions/L2 normalization")
        require(sha(struct.pack("<" + "f" * len(vector), *vector)) == row["query_vector_sha256"], "Native float32 vector hash")
        require(vectors.setdefault(row["query_id"], row["query_vector_sha256"]) == row["query_vector_sha256"], "Native paired vector stability")
        ranking_key = row["query_id"], row["retrieval"]
        payload_key = row["query_id"], row["retrieval"], row["arm"]
        ranking_hash = sha(canonical(row["stages"]["ranked"]))
        require(rankings.setdefault(ranking_key, ranking_hash) == ranking_hash
                and payloads.setdefault(payload_key, row["compact_sha256"]) == row["compact_sha256"], "Paired/repeated raw orders or payloads changed")
    recomputed = native_summary(rows)
    require(summary["metrics"] == recomputed, "Independent native quality/warm-latency summary")
    require(summary["requests"] == summary["measured_query_embeddings"] == len(rows) == len(queries) * 18
            and summary["unique_queries"] == len(queries) and summary["quality_repetition"] == 1
            and summary["ranked_repeat_changes"] == summary["compact_repeat_changes"] == 0,
            "Native request/independent-unit/accounting counts")
    require(summary["setup"]["query_calls"] == 6
            and summary["total_query_embeddings"] == len(rows) + summary["setup"]["query_calls"], "Native six warmups/accounting")
    require(all(sha((output / name).read_bytes()) == digest for name, digest in artifacts.items()), "Native artifacts changed during audit")
    return {"status": "passed", "checks_passed": True, "mode": "native", "artifact_sha256": artifacts,
            "producer_freeze": chain, "producer_execution": execution, "bound_file_resolutions": resolutions,
            "verified_requests": len(rows), "unique_queries": len(queries), "verified_source_windows": len(fixture["inventory"]),
            "independently_recomputed_metrics": recomputed,
            "source_sha256": binding["source_sha256"], "strict_qualified_symbol_and_full_evidence_bytes": True,
            "limits": "Audit validates saved native accounting/source receipts independently, without making model calls. Fresh scoring is stricter than historical development scoring; strata must remain separate."}


class RefuseProduction(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in FORBIDDEN):
            raise RuntimeError("Independent window audit forbids producer/model imports: " + fullname)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=("development", "native"), default="development")
    parser.add_argument("--corpus-root", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root, results = args.root.resolve(), args.results.resolve()
    target = args.output.resolve() if args.output else results / "independent-audit.json"
    require(not target.exists(), "Never overwrite a completed or failed independent audit")
    failure = target.with_name(target.stem + "-failure.json")
    require(not failure.exists(), "Preserve failed attempts; choose a new audit output")
    paths = ("scripts/audit_windows.py", "tests/test_window_audit.py", "scripts/audit_selection.py")
    auditor_sources = {path: (root / path).read_bytes() for path in paths}
    auditor_hashes = {path: sha(raw) for path, raw in auditor_sources.items()}
    # This is a precondition, before any result parsing or outcome audit.
    for path, expected in auditor_hashes.items():
        require(sha(git_bytes(root, "HEAD", path)) == expected,
                "Commit the unchanged independent auditor and tests before outcome auditing: " + path)
    execution = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    require(not any(any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN)
                    for name in sys.modules), "Independent audit needs a clean CPU process")
    guard = RefuseProduction()
    sys.meta_path.insert(0, guard)
    def denied(*unused, **unused_kwargs):
        raise RuntimeError("Network is forbidden in independent window audit")
    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
                patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
                patch.object(socket.socket, "sendto", denied):
            counter = offline_encoding(Path(os.environ.get("TIKTOKEN_CACHE_DIR", root / ".cache/tokenizers")))
            if args.mode == "native":
                require(args.corpus is not None and args.corpus_root is None, "Native audit needs --corpus only")
                receipt = audit_native(root, args.corpus.resolve(), results, counter)
            else:
                require(args.corpus_root is not None and args.corpus is None, "Development audit needs --corpus-root only")
                receipt = audit_development(root, args.corpus_root.resolve(), results, counter)
        require(all((root / path).read_bytes() == raw for path, raw in auditor_sources.items()), "Auditor changed during execution")
        receipt.update(auditor_sha256=auditor_hashes, auditor_execution_commit=execution,
                       reviewed_at_utc=datetime.now(timezone.utc).isoformat(),
                       calls={"model": 0, "retrieval": 0, "index": 0, "network": 0},
                       production_or_producer_modules_imported=False)
        with target.open("x") as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(json.dumps({"checks_passed": True, "audit": str(target), "sha256": sha(target.read_bytes())}))
    except BaseException as error:
        with failure.open("x") as stream:
            stream.write(json.dumps({"status": "failed", "error_type": type(error).__name__, "error": str(error),
                "auditor_sha256": auditor_hashes, "auditor_execution_commit": execution,
                "resume_allowed": False, "calls": {"model": 0, "retrieval": 0, "index": 0, "network": 0}}, indent=2) + "\n")
        raise
    finally:
        sys.meta_path.remove(guard)


if __name__ == "__main__":
    main()
