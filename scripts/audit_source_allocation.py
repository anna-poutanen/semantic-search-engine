#!/usr/bin/env python3
"""Independently audit source-aware allocation and postpacking source guards.

Planning, packing and postpacking guards are independently reconstructed from literal source.
Historical Python files are inspected only as committed Git blobs, never loaded.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import socket
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("source_allocation_independent_window_helpers", ROOT / "scripts/audit_windows.py")
win = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(win)
old = win.old
require, sha, read, canonical = win.require, win.sha, win.read, win.canonical
CONTROLS = ("legacy", "coverage_control")
NEGATIVES = ("aspect_fill", "aspect_balanced")
CANDIDATES = ("source_graph", "source_graph_all")
CONDITIONS = CONTROLS + NEGATIVES + CANDIDATES
SETTINGS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096,
            "include_dependencies": True, "rerank": False, "candidates": 50}
ALLOWED_GRAPH_MODULES = frozenset({"semantic_search", "semantic_search.relations",
    "semantic_search.javascript_relations", "semantic_search.javascript", "semantic_search.languages",
    "semantic_search.source"})
GRAPH_SOURCES = tuple("src/" + name.replace(".", "/") + ("/__init__.py" if name == "semantic_search" else ".py")
                      for name in sorted(ALLOWED_GRAPH_MODULES))
FORBIDDEN = (*win.FORBIDDEN, "replay_allocation", "benchmark_allocation", "replay_context_allocation",
             "replay_source_allocation", "benchmark_source_allocation")

HISTORICAL_ALLOCATION = "results/allocation-development-2026-10-26"
FROZEN_FILES = ("frozen-plan.json", "baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json",
                "control-references.json", "historical-provenance.json", "negative-references.json",
                "negative-provenance.json", "graph-fixtures.json")

def definition_id(item, keys):
    key = keys.get(item["id"])
    return ("definition", tuple(key)) if key is not None else ("window", item["id"])


def unique_definitions(ranked, keys):
    seen, result = set(), []
    for pair in ranked:
        key = definition_id(pair["snippet"], keys)
        if key not in seen:
            seen.add(key)
            result.append(pair)
    return result


def file_sha(path):
    digest = __import__("hashlib").sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_sha(root, commit, path):
    process = subprocess.Popen(["git", "-C", str(root), "show", commit + ":" + path], stdout=subprocess.PIPE)
    digest = __import__("hashlib").sha256()
    with process.stdout:
        for block in iter(lambda: process.stdout.read(1024 * 1024), b""):
            digest.update(block)
    require(process.wait() == 0, "Missing committed historical blob: " + path)
    return digest.hexdigest()


def stream_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            require(bool(line.strip()), "Blank raw record")
            yield number, json.loads(line)


def read_controls(path, *, expected_total=1825, expected_controls=730):
    """Hash each complete old record, retain only necessary control fields."""
    controls, total = {}, 0
    for number, row in stream_rows(path):
        total += 1
        if row["selector"] not in CONTROLS:
            continue
        key = (row["selector"], *win.case_key(row))
        require(key not in controls, "Duplicate immutable control")
        reference = {"line_number": number, "record_sha256": sha(canonical(row)),
            "response_sha256": sha(canonical(row["response"])), "compact_sha256": row["compact_sha256"]}
        require(row["compact_payload"] == win.compact_payload(row["response"])
                and reference["compact_sha256"] == sha(row["compact_payload"].encode()), "Immutable control compact receipt")
        keep = ("subset", "dataset", "query_id", "condition", "selector", "kind", "multi_file",
                "evidence_groups", "evidence_ranks", "stages", "response", "compact_payload", "compact_sha256")
        controls[key] = {name: row[name] for name in keep}
        controls[key]["reference"] = reference
    require(total == expected_total and len(controls) == expected_controls, "Complete immutable parent/control matrix")
    return controls


def verify_control_references(row, controls):
    expected = {name: controls[(name, *win.case_key(row))]["reference"] for name in CONTROLS}
    require(row["control_references"] == expected, "Immutable control record references differ")


def historical_proof(root, plan):
    """Independently resolve the original history as data, allowing a rewrite."""
    parent = plan["historical_parent"]
    for name, digest in parent["artifacts"].items():
        require(file_sha(root / name) == digest and git_sha(root, "HEAD", name) == digest,
                "Historical artifact bytes changed or uncommitted: " + name)
    folder = root / "results/window-development-2026-10-21"
    previous, start, audit = (read(folder / name) for name in
                            ("frozen-plan.json", "run-start.json", "independent-audit.json"))
    require(audit.get("status") == "passed" and audit.get("checks_passed") is True
            and audit.get("verified_outcome_rows") == 1825 and audit.get("exact_legacy_controls") == 365
            and audit.get("exact_prior_coverage_controls") == 337, "Successful original independent window audit")
    for name, expected in audit["artifact_sha256"].items():
        relative = "results/window-development-2026-10-21/" + name
        require(parent["artifacts"].get(relative) == expected, "Historical audited artifact omitted or substituted")
    require(parent["original_plan_sha256"] == start["plan_sha256"] == file_sha(folder / "frozen-plan.json")
            and parent["original_execution_commit"] == audit["producer_execution"] == start["execution_commit"],
            "Original plan/execution identities disagree")
    frozen = {"results/window-development-2026-10-21/" + name: file_sha(folder / name) for name in
              ("frozen-plan.json", "baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json")}
    bound = {**previous["producer_sha256"], **previous["input_sha256"], **frozen}
    proof = parent["proof"]
    require(proof["original_freeze_commit"] == audit["producer_freeze"]["commit"]
            and proof["original_execution_commit"] == start["execution_commit"]
            and proof["original_auditor_commit"] == audit["auditor_execution_commit"]
            and proof["original_bound_sha256"] == bound
            and proof["original_auditor_sha256"] == audit["auditor_sha256"], "Original exact historical proof identities")
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor",
                           proof["original_freeze_commit"], proof["original_execution_commit"]])
    stamp = int(subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%ct",
                                       proof["original_freeze_commit"]], text=True))
    require(stamp == proof["freeze_committed_at_unix"]
            and stamp <= datetime.fromisoformat(start["created_at_utc"]).timestamp(), "Original freeze preceded original run")
    for name, expected in bound.items():
        require(git_sha(root, proof["original_freeze_commit"], name) == expected
                and git_sha(root, proof["original_execution_commit"], name) == expected,
                "Original historical producer/input Git blob differs: " + name)
    for name, expected in audit["auditor_sha256"].items():
        require(git_sha(root, proof["original_auditor_commit"], name) == expected,
                "Original historical auditor Git blob differs: " + name)
    ancestry = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                              proof["original_execution_commit"], "HEAD"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    require(ancestry in (0, 1) and proof["original_execution_is_current_ancestor"] == (ancestry == 0),
            "Historical/current history rewrite declaration differs")
    require("no historical producer code executed" in proof["resolution"], "Historical proof must disclose data-only validation")
    return proof


def counts(cases):
    subsets = ("language_observed", "native_express_observed", "native_zod_observed", "native_jinja_observed",
               "qwen_development_observed", "qwen_httpx_observed", "native_urllib3_observed")
    return {"observed_rows": len(cases), "unique_queries": len({(r["dataset"], r["query_id"]) for r in cases}),
        "row_counts_by_subset": dict(Counter(r["subset"] for r in cases)),
        "unique_queries_by_subset": {name: len({(r["dataset"], r["query_id"]) for r in cases if r["subset"] == name})
                                    for name in subsets},
        "warning": "Reused queries and retrieval conditions are shared units; observed rows are not independent queries."}


def summary(rows, case_counts):
    """Recompute all strata and every comparison, including partial-query losses."""
    metrics, ledger, qualification = {}, {}, {}
    for name in CONDITIONS:
        own = [row for row in rows if row["selector"] == name]
        strata = sorted({(row["subset"], row["dataset"], row["condition"]) for row in own})
        metrics[name] = {}
        for stratum in strata:
            items = [row for row in own if tuple(row[n] for n in ("subset", "dataset", "condition")) == stratum]
            value = old.metrics(items)
            value["assembled_rows"] = sum(any("assembly" in item for item in row["response"]["results"]) for row in items)
            metrics[name]["/".join(stratum)] = value
        comparisons = {}
        for control in CONTROLS:
            gains, losses, complete_gains, complete_losses, hit_losses = [], [], [], [], []
            for row in own:
                delta, identity = row["comparisons"][control], old.identity(row)
                if delta["group_gains"]:
                    gains.append({**identity, "groups": delta["group_gains"]})
                if delta["group_losses"]:
                    losses.append({**identity, "groups": delta["group_losses"]})
                if delta["complete_gain"]:
                    complete_gains.append(identity)
                if delta["complete_loss"]:
                    complete_losses.append(identity)
                if row["kind"] == "present" and not row["multi_file"] and delta["control_ranks"] \
                        and delta["control_ranks"][0] is not None and delta["variant_ranks"][0] is None:
                    hit_losses.append(identity)
            comparisons[control] = {"group_gains": gains, "group_losses": losses,
                "complete_gains": complete_gains, "complete_losses": complete_losses,
                "unique_complete_gains": sorted({r["dataset"] + "/" + r["query_id"] for r in complete_gains}),
                "single_file_first_hit_losses": hit_losses}
        ledger[name] = comparisons
        if name in CANDIDATES:
            losses = {control: {"lost_groups": sum(len(r["groups"]) for r in value["group_losses"]),
                "lost_complete_rows": len(value["complete_losses"]),
                "lost_single_file_first_hits": len(value["single_file_first_hit_losses"])} for control, value in comparisons.items()}
            new = comparisons["coverage_control"]["unique_complete_gains"]
            qualification[name] = {"qualified": bool(new) and not any(v for values in losses.values() for v in values.values()),
                "against_controls": losses, "additional_distinct_complete_queries_vs_coverage_control": new,
                "gained_additional_distinct_complete_queries": len(new), "requires_fresh_native_validation": True,
                "automatic_default_change": False}
    return {"classification": "Observed-source CPU DEVELOPMENT source-aware complementary-definition allocation replay",
        "study": "source_allocation", "counts": case_counts, "metrics_by_study_dataset_original_condition": metrics, "all_changes": ledger,
        "qualification": qualification, "model_calls": 0, "retrieval_calls": 0, "network_calls": 0,
        "automatic_default_change": False, "stored_new_candidate_rows": sum(r["selector"] in CANDIDATES for r in rows),
        "referenced_control_rows": sum(r["selector"] in CONTROLS for r in rows),
        "referenced_negative_rows": sum(r["selector"] in NEGATIVES for r in rows),
        "historical_negative_conditions": list(NEGATIVES)}




def graph_records(sources, inventory):
    """Rebuild only the trusted, frozen conservative source graph.

    Trust boundary: the original ReferenceGraph and pinned grammar are used as
    source parsers. No selector, allocator, backend, index or producer is loaded.
    Graph module bytes and complete corpus bytes are separately freeze-bound;
    graph-neighbor planning and physical-source protection are independent below.
    """
    from semantic_search.relations import ReferenceGraph
    graph = ReferenceGraph(sources, [SimpleNamespace(**row) for row in inventory.values()])
    return [{"caller": list(caller), "targets": [list(target) for target in targets]}
            for caller, targets in sorted(graph.edges.items())]


def verify_graph_records(saved, sources, inventory):
    actual = graph_records(sources, inventory)
    require(saved == actual, "Frozen source graph edge inventory differs from literal source extraction")
    return {tuple(record["caller"]): [tuple(target) for target in record["targets"]] for record in actual}


def graph_neighbors(left, right, edges, ambiguous):
    """Witness direct edges only; unknown/ambiguous display labels are excluded."""
    if left in ambiguous or right in ambiguous:
        return []
    witnesses = []
    if right in edges.get(left, ()):
        witnesses.append({"caller": list(left), "callee": list(right)})
    if left in edges.get(right, ()):
        witnesses.append({"caller": list(right), "callee": list(left)})
    return witnesses


def parent_allocation_proof(root, plan):
    """Check the original allocation study's committed blobs without execution."""
    parent = plan["negative_parent"]
    folder = root / HISTORICAL_ALLOCATION
    previous, start, audit, original_summary = (read(folder / name) for name in
                             ("frozen-plan.json", "run-start.json", "independent-audit.json", "summary.json"))
    require(previous.get("study") == audit.get("study") == original_summary.get("study") == "allocation"
            and audit.get("checks_passed") is True and audit.get("status") == "passed"
            and audit.get("exact_legacy_controls") == 365 and audit.get("exact_prior_coverage_controls") == 337
            and audit.get("verified_outcome_rows") == audit.get("referenced_control_rows") == 730
            and audit.get("independently_recomputed_summary") == original_summary
            and tuple(original_summary["qualification"]) == NEGATIVES
            and not any(value["qualified"] for value in original_summary["qualification"].values()),
            "Successful original allocation negative audit required")
    for name, expected in audit["artifact_sha256"].items():
        require(parent["artifacts"].get(HISTORICAL_ALLOCATION + "/" + name) == expected,
                "Historical negative audited artifact omitted or substituted")
    for name, expected in parent["artifacts"].items():
        require(file_sha(root / name) == git_sha(root, "HEAD", name) == expected,
                "Historical negative artifact bytes changed or uncommitted: " + name)
    proof = parent["proof"]
    require(audit["producer_freeze"] == start["prospective_freeze"], "Original negative prospective freeze/execution differ")
    frozen = {HISTORICAL_ALLOCATION + "/" + name: file_sha(folder / name) for name in
              ("frozen-plan.json", "baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json",
               "control-references.json", "historical-provenance.json")}
    bound = {**previous["producer_sha256"], **previous["input_sha256"],
             **previous["historical_parent"]["artifacts"], **frozen}
    require(parent["original_plan_sha256"] == start["plan_sha256"] == file_sha(folder / "frozen-plan.json")
            and parent["original_execution_commit"] == start["execution_commit"] == audit["producer_execution"]
            and proof["original_execution_commit"] == start["execution_commit"]
            and proof["original_freeze_commit"] == audit["producer_freeze"]["commit"]
            and proof["original_auditor_commit"] == audit["auditor_execution_commit"]
            and proof["original_bound_sha256"] == bound
            and proof["original_auditor_sha256"] == audit["auditor_sha256"],
            "Historical negative producer, plan, freeze or auditor identity differs")
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor",
                           proof["original_freeze_commit"], proof["original_execution_commit"]])
    stamp = int(subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%ct",
                                       proof["original_freeze_commit"]], text=True))
    require(stamp == proof["freeze_committed_at_unix"]
            and stamp <= datetime.fromisoformat(start["created_at_utc"]).timestamp(),
            "Original negative freeze must precede its original execution")
    for name, expected in bound.items():
        require(git_sha(root, proof["original_freeze_commit"], name) == expected
                and git_sha(root, proof["original_execution_commit"], name) == expected,
                "Original negative producer/input blob differs: " + name)
    for name, expected in audit["auditor_sha256"].items():
        require(git_sha(root, proof["original_auditor_commit"], name) == expected,
                "Original negative auditor blob differs: " + name)
    ancestry = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                              proof["original_execution_commit"], "HEAD"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    require(ancestry == 0 and proof["original_execution_is_current_ancestor"] is True,
            "Immediately preceding negative execution must remain a current ancestor")
    require(start["prospective_freeze"] == {"commit": proof["original_freeze_commit"],
            "committed_at_unix": stamp, "bound_blob_count": len(bound)},
            "Original negative prospective freeze chain differs")
    require("no historical producer code executed" in proof["resolution"],
            "Historical negative verification must disclose data-only proof")
    return proof


def new_freeze(root, output, plan, start):
    """The new experiment requires strict committed ancestry despite old rewrites."""
    bound = {**plan["producer_sha256"], **plan["input_sha256"], **plan["historical_parent"]["artifacts"],
             **plan["negative_parent"]["artifacts"],
             **{(output / name).relative_to(root).as_posix(): file_sha(output / name) for name in FROZEN_FILES}}
    for name, expected in bound.items():
        require(file_sha(root / name) == expected, "Prospective source/input artifact differs: " + name)
    chain = old.committed_freeze(root, output / "frozen-plan.json", bound, start["created_at_utc"])
    execution = start["execution_commit"]
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", chain["commit"], execution])
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", execution, "HEAD"])
    require(start["prospective_freeze"] == chain, "Actual prospective freeze receipt differs")
    for name, expected in bound.items():
        require(git_sha(root, execution, name) == expected, "Prospective execution Git blob differs: " + name)
    return chain, execution


def read_negatives(path, *, expected_rows=730):
    rows, total = {}, 0
    for number, row in stream_rows(path):
        total += 1
        key = (row["selector"], *win.case_key(row))
        require(row["selector"] in NEGATIVES and key not in rows, "Unexpected/duplicate frozen negative row")
        require(row["compact_payload"] == win.compact_payload(row["response"])
                and row["compact_sha256"] == sha(row["compact_payload"].encode()),
                "Frozen negative compact receipt differs")
        keep = ("subset", "dataset", "query_id", "condition", "selector", "kind", "multi_file", "evidence_groups",
                "evidence_ranks", "response", "compact_payload", "compact_sha256", "comparisons")
        rows[key] = {name: row[name] for name in keep}
        rows[key]["reference"] = {"line_number": number, "record_sha256": sha(canonical(row)),
            "response_sha256": sha(canonical(row["response"])), "compact_sha256": row["compact_sha256"]}
    require(total == len(rows) == expected_rows, "Complete730 immutable negative matrix required")
    return rows


def canonical_key(value):
    if not isinstance(value, (tuple, list)) or len(value) not in (4, 5):
        return None
    if not all(isinstance(label, str) for label in value[:2]):
        return None
    if type(value[2]) is not int or type(value[3]) is not int or not 1 <= value[2] <= value[3]:
        return None
    if len(value) == 5 and (type(value[4]) is not int or value[4] < 0):
        return None
    return tuple(value)


def clause_words(text):
    """Independent historical literal detector; no source feature ranking."""
    import re
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    stop = set("where is are the a an and or to of for in on at by with from when while does do as "
               "be it its into only that this then otherwise can without which each either any "
               "all before after also through using return returns make made".split())
    output = set()
    for term in re.findall(r"[a-z0-9]+", text.lower()):
        if term in stop or len(term) < 3:
            continue
        if term.endswith("ies") and len(term) > 4:
            term = term[:-3] + "y"
        elif term.endswith("s") and len(term) > 3 and not term.endswith("ss"):
            term = term[:-1]
        output.add(term)
    return output


def literal_clauses(query):
    import re
    require(isinstance(query, str) and bool(query.strip()), "Nonempty original query required")
    parts = [part.strip(" ?.,") for part in re.split(r",|\band\b|\bwhile\b|\bthen\b", query, flags=re.I)]
    parts = [part for part in parts if len(clause_words(part)) >= 3]
    return parts[:4] if len(parts) >= 2 else []


def planner(ranked, baseline_selected, baseline_eligible, baseline_origins, keys,
            edges, ambiguous_symbols, *, mode, query, max_results=8):
    """Independently reconstruct the fixed two-exchange graph-neighbor rule."""
    require(mode in CANDIDATES and type(max_results) is int and max_results >= 0,
            "Fixed source allocation mode/cap")
    selected = unique_definitions(ranked, keys)
    require(selected == baseline_selected, "Original canonical representative order changed")
    require(len(baseline_eligible) <= max_results, "Original eligible result cap")
    baseline_ids = [pair["snippet"]["id"] for pair in baseline_eligible]
    require(len(set(baseline_ids)) == len(baseline_ids), "Duplicate baseline eligible row")
    anchor_pairs = selected[:min(5, max_results)]
    anchor_ids = [pair["snippet"]["id"] for pair in anchor_pairs]
    clauses = literal_clauses(query)
    relevant = {pair["snippet"]["id"] for pair in selected + baseline_eligible}
    receipt_keys = {identifier: list(value) for identifier in sorted(relevant)
                    if (value := canonical_key(keys.get(identifier))) is not None}
    ranks = {}
    for number, pair in enumerate(ranked, 1):
        ranks.setdefault(pair["snippet"]["id"], number)
    owners = {pair["snippet"]["id"]: baseline_origins[pair["snippet"]["id"]][1]
              for pair in baseline_eligible
              if baseline_origins.get(pair["snippet"]["id"], ["retrieval", None])[0] == "dependency"}
    receipt = {"schema": "source_graph_allocation_v1", "mode": mode, "activated": bool(clauses),
        "clauses": clauses, "raw_ranks": ranks, "canonical_keys": receipt_keys, "anchor_ids": anchor_ids,
        "baseline_eligible_ids": baseline_ids, "dependency_owners": owners, "graph_witnesses": [],
        "neighbor_keys": [], "protected_baseline_ids": [], "protected_windows": [], "available_candidate_ids": [],
        "added_ids": [], "evicted_ids": [], "proposed_eligible_ids": list(baseline_ids),
        "accepted": False, "fallback_reason": None}

    def result(proposal, reason=None):
        receipt["fallback_reason"] = reason
        return deepcopy(selected), deepcopy(proposal), deepcopy(baseline_origins), receipt

    if not clauses:
        return result(baseline_eligible, "no_literal_clauses")
    definitions = {}
    for raw in keys.values():
        key = canonical_key(raw)
        if key is not None:
            definitions.setdefault(key[:2], set()).add(key)
    blocked = {tuple(label) for label in ambiguous_symbols}
    unique = {label: next(iter(values)) for label, values in definitions.items()
              if len(values) == 1 and label not in blocked}
    direct = sorted({(caller, callee) for caller, targets in edges.items() if caller in unique
                     for callee in targets if callee in unique and caller != callee})
    witnesses = []
    for pair in anchor_pairs:
        item = pair["snippet"]
        label, key = (item["path"], item["symbol"]), canonical_key(keys.get(item["id"]))
        if key is None or unique.get(label) != key:
            continue
        for caller, callee in direct:
            if label not in (caller, callee):
                continue
            neighbor = callee if caller == label else caller
            witnesses.append({"anchor_id": item["id"], "anchor_key": list(key),
                "direction": "callee" if caller == label else "caller", "neighbor_key": list(unique[neighbor]),
                "source_key": list(unique[caller]), "target_key": list(unique[callee])})
    neighbor_keys = {tuple(w["neighbor_key"]) for w in witnesses}
    receipt.update(graph_witnesses=witnesses, neighbor_keys=[list(key) for key in sorted(neighbor_keys)])
    protected = set(anchor_ids) | set(owners) | set(owners.values())
    protected.update(identifier for identifier in baseline_ids
                     if tuple(receipt_keys.get(identifier, ())) in neighbor_keys)
    receipt["protected_baseline_ids"] = [identifier for identifier in baseline_ids if identifier in protected]
    protected_windows = {}
    for pair in baseline_eligible + selected:
        item = pair["snippet"]
        if item["id"] in protected or tuple(receipt_keys.get(item["id"], ())) in neighbor_keys:
            protected_windows.setdefault(item["id"], {field: item[field] for field in
                ("id", "path", "start_line", "end_line", "content_hash")})
    receipt["protected_windows"] = list(protected_windows.values())
    if not witnesses:
        return result(baseline_eligible, "no_graph_neighbors")
    spare = [pair for pair in baseline_eligible if pair["snippet"]["id"] not in protected]
    if not spare:
        return result(baseline_eligible, "no_exchangeable_slots")
    existing_definitions = {definition_id(pair["snippet"], keys) for pair in baseline_eligible}
    available = [pair for pair in selected if definition_id(pair["snippet"], keys) not in existing_definitions
                 and tuple(receipt_keys.get(pair["snippet"]["id"], ())) in neighbor_keys]
    receipt["available_candidate_ids"] = [pair["snippet"]["id"] for pair in available]
    if not available:
        return result(baseline_eligible, "no_new_graph_neighbor")
    additions = available[:min(2, len(spare))]
    removed = {pair["snippet"]["id"] for pair in spare[len(spare) - len(additions):]}
    proposal = [pair for pair in baseline_eligible if pair["snippet"]["id"] in protected]
    proposal.extend(additions)
    proposal.extend(pair for pair in baseline_eligible if pair["snippet"]["id"] not in protected | removed)
    receipt.update(added_ids=[pair["snippet"]["id"] for pair in additions],
        evicted_ids=[identifier for identifier in baseline_ids if identifier in removed],
        proposed_eligible_ids=[pair["snippet"]["id"] for pair in proposal], accepted=True)
    return result(proposal)


def verify_planning(row, baseline, fixture):
    stages = row["stages"]
    require(stages["ranked"] == baseline["stages"]["ranked"], "Source allocation changed frozen retrieval order/scores")
    selected, eligible, origins, receipt = planner(stages["ranked"], baseline["stages"]["selected"],
        baseline["stages"]["eligible"], baseline["stages"]["origins"], fixture["definition_keys"],
        fixture["graph_edges"], stages["ambiguous_symbols"], mode=row["selector"], query=row["response"]["query"])
    require(all(type(row[name][field]) is bool for name in ("source_allocation_receipt", "selection_plan")
                for field in ("accepted", "activated")), "Planner receipt flags must be literal booleans")
    require(row["source_allocation_receipt"] == row["selection_plan"] == receipt,
            "Independent deterministic graph allocation receipt differs")
    require(stages["selected"] == selected and stages["eligible"] == eligible and stages["origins"] == origins,
            "Source allocation stage/order/score/provenance differs from independent plan")
    return receipt


def valid_literal(row):
    source, low, high = row.get("source"), row.get("start_line"), row.get("end_line")
    return (isinstance(source, str) and bool(source) and isinstance(row.get("path"), str)
            and type(low) is int and type(high) is int and low >= 1
            and high == low + len(win.physical_lines(source)) - 1
            and row.get("source_hash") == sha(source.encode()))


def protected_rows(reference, plan):
    if plan["mode"] == "source_graph_all" or not plan["accepted"]:
        return reference["results"]
    ids, keys = set(plan["protected_baseline_ids"]), {tuple(key) for key in plan["neighbor_keys"]}
    return [row for row in reference["results"] if row["id"] in ids or row.get("origin") == "dependency"
            or tuple(plan["canonical_keys"].get(row["id"], ())) in keys
            or any(row["path"] == window["path"] and max(row["start_line"], window["start_line"]) <=
                   min(row["end_line"], window["end_line"]) for window in plan["protected_windows"])]


def source_covers(current, original):
    if not valid_literal(current) or not valid_literal(original) or not win.same_provenance(current, original):
        return False
    if current["path"] != original["path"] or current.get("origin") != original.get("origin"):
        return False
    if ("related_to" in current) != ("related_to" in original) or current.get("related_to") != original.get("related_to"):
        return False
    if current.get("id") == original.get("id") and (("content_hash" in current) != ("content_hash" in original)
            or current.get("content_hash") != original.get("content_hash")):
        return False
    if original.get("origin") == "dependency" and (current.get("id"), current.get("related_to")) != (
            original.get("id"), original.get("related_to")):
        return False
    low, high = original["start_line"], original["end_line"]
    if not current["start_line"] <= low <= high <= current["end_line"]:
        return False
    return "".join(win.physical_lines(current["source"])[low - current["start_line"]:
                   high - current["start_line"] + 1]) == original["source"]


def guard(reference, proposal, plan):
    protected = protected_rows(reference, plan)
    delivered_ids = {row.get("id") for row in proposal["results"]}
    preserved = all((before.get("origin") != "dependency" or before.get("related_to") in delivered_ids)
                    and any(source_covers(now, before) for now in proposal["results"]) for before in protected)
    accepted = plan["accepted"] and preserved
    reason = None if accepted else "inactive_plan" if not plan["accepted"] else "protected_source_not_preserved"
    receipt = {"schema": "source_graph_guard_v1", "mode": plan["mode"],
        "protected_ids": [row["id"] for row in protected],
        "protected_rows": [{field: row[field] for field in
            ("id", "path", "start_line", "end_line", "source_hash", "content_hash", "origin", "related_to",
             "source_origin", "buffer_version", "buffer_hash") if field in row} for row in protected],
        "source_preserved": preserved, "accepted": bool(accepted), "fallback_reason": reason}
    return deepcopy(proposal if accepted else reference), receipt


def verify_guard(row, baseline):
    reference, proposal, plan = row["reference_response"], row["proposal_response"], row["selection_plan"]
    require(reference == baseline["response"], "Actual lazy reference differs from immutable coverage control")
    require(row["guard_plan"] == plan, "Guard did not receive the actual independent selection plan")
    expected, receipt = guard(reference, proposal, plan)
    require(all(type(row["source_guard_receipt"][field]) is bool for field in ("source_preserved", "accepted")),
            "Guard receipt flags must be literal booleans")
    require(row["source_guard_receipt"] == receipt, "Postpacking actual-source guard receipt differs")
    require(all(type(row[field]) is int and row[field] == 1 for field in
                ("planner_calls", "guard_calls", "reference_calls")),
            "Actual planner, lazy reference and guard must each execute exactly once")
    require(row["response"] == expected, "Guard changed accepted proposal or exact reference fallback")
    return receipt



def count_compact(response, counter):
    """Independent self-reported compact-token fixed point."""
    usage = response["token_usage"]
    usage["tokens"] = 0
    for _ in range(12):
        count = len(counter.encode(win.compact_payload(response), disallowed_special=()))
        if usage["tokens"] == count:
            return count
        usage["tokens"] = count
    raise ValueError("Independent compact token count failed to stabilize")


def token_pack(response, counter, budget):
    result = deepcopy(response)
    rows, result["results"] = result["results"], []
    result["token_usage"] = {"encoding": "cl100k_base", "tokens": 0, "budget": budget, "truncated": False}
    require(count_compact(result, counter) <= budget, "Token budget cannot hold independent empty metadata")
    for row in rows:
        result["results"].append(row)
        if count_compact(result, counter) <= budget:
            continue
        result["budget_exhausted"] = result["token_usage"]["truncated"] = True
        lines, original = win.physical_lines(row["source"]), row["source"]
        while lines:
            lines.pop()
            row.update(source="".join(lines), end_line=row["start_line"] + len(lines) - 1)
            row["source_hash"] = sha(row["source"].encode())
            row["truncated"] = row["source"] != original
            if lines and count_compact(result, counter) <= budget:
                break
        if not lines:
            result["results"].pop()
        require(count_compact(result, counter) <= budget, "Token budget cannot hold independent row metadata")
    result["total_source_chars"] = sum(len(row["source"]) for row in result["results"])
    count_compact(result, counter)
    return result


def ordinary_pack(template, eligible, origins, counter, *, chars=12000, tokens=4096):
    """Independently enforce original greedy physical-prefix packing, then tokens."""
    result = {field: deepcopy(template[field]) for field in ("query", "scope", "match_status")}
    result["results"], remaining = [], chars
    for pair in eligible:
        item, used, prefix = pair["snippet"], 0, []
        for line in win.physical_lines(item["text"]):
            if used + len(line) > remaining:
                break
            used += len(line)
            prefix.append(line)
        source = "".join(prefix)
        if not source:
            continue
        remaining -= len(source)
        origin, owner = origins.get(item["id"], ["retrieval", None])
        row = {"id": item["id"], "path": item["path"], "symbol": item["symbol"],
            "start_line": item["start_line"], "end_line": item["start_line"] + len(prefix) - 1,
            "window_end_line": item["end_line"], "source": source, "content_hash": item["content_hash"],
            "source_hash": sha(source.encode()), "retrieval_score": float(pair["score"]), "h2o_score": None,
            "origin": origin, "related_to": owner, "truncated": source != item["text"]}
        if template["results"] and "qwen_score" in template["results"][0]:
            row["qwen_score"] = None
        result["results"].append(row)
    result["total_source_chars"] = chars - remaining
    result["budget_exhausted"] = (any(row["truncated"] for row in result["results"])
                                  or len(result["results"]) < len(eligible))
    return token_pack(result, counter, tokens)


def coalesce_covers(outer, inner):
    """Historical coalescing proves literal slices, without graph/provenance inference."""
    a, b = outer.get("source"), inner.get("source")
    if (not isinstance(a, str) or not a or not isinstance(b, str) or not b
            or not isinstance(outer.get("path"), str) or not isinstance(inner.get("path"), str)):
        return False
    for row in (outer, inner):
        if (type(row.get("start_line")) is not int or type(row.get("end_line")) is not int
                or row["start_line"] < 1
                or row["end_line"] != row["start_line"] + len(win.physical_lines(row["source"])) - 1):
            return False
    if outer["path"] != inner["path"] or not outer["start_line"] <= inner["start_line"] <= inner["end_line"] <= outer["end_line"]:
        return False
    return win.physical_lines(a)[inner["start_line"] - outer["start_line"]:
                                inner["end_line"] - outer["start_line"] + 1] == win.physical_lines(b)


def coalesce_pack(before, candidates, counter, *, max_results=8, chars=12000, tokens=4096):
    """Independent deterministic containment/backfill, including exact diagnostics."""
    result = deepcopy(before)
    original = result["results"]
    debug = {"coalesced_ids": [], "appended_ids": [], "trials": 0, "trial_limit": 8,
        "trial_limit_reached": False, "skipped": [], "coverage_before": False,
        "coverage_after": False, "fallback_reason": None}

    def finish(value):
        value["_selection_backfill"] = debug
        return value

    def fallback(reason):
        debug["fallback_reason"] = reason
        debug["rejected_coalesced_ids"] = debug["coalesced_ids"]
        debug["coalesced_ids"], debug["appended_ids"] = [], []
        return finish(deepcopy(before))

    def all_covered(rows):
        return all(any(coalesce_covers(outer, inner) for outer in rows) for inner in original)

    def recount(value):
        value["total_source_chars"] = sum(len(row["source"]) for row in value["results"])
        return count_compact(value, counter)

    def set_prefix(row, lines, full):
        row["source"] = "".join(lines)
        row["end_line"] = row["start_line"] + len(lines) - 1
        row["source_hash"] = sha(row["source"].encode())
        row["truncated"] = bool(row.get("truncated", False)) or row["source"] != full

    retained = []
    for rank, row in enumerate(original):
        remove = any(other_rank != rank and coalesce_covers(other, row)
                     and (not coalesce_covers(row, other) or other_rank < rank)
                     for other_rank, other in enumerate(original))
        (debug["coalesced_ids"] if remove else retained).append(row.get("id") if remove else row)
    if not all_covered(retained):
        return fallback("original_source_not_covered")
    debug["coverage_before"] = True
    result["results"] = retained
    result.setdefault("token_usage", {}).update(encoding="cl100k_base", budget=tokens)
    result["token_usage"].setdefault("truncated", False)
    count = recount(result)
    if len(retained) > max_results or result["total_source_chars"] > chars or count > tokens:
        return fallback("base_budget_exceeded")
    if not debug["coalesced_ids"]:
        debug["coverage_after"] = True
        return finish(result)
    seen = {row.get("id") for row in original}
    for candidate in candidates:
        if len(result["results"]) >= max_results:
            break
        identifier = candidate.get("id")
        if identifier in seen:
            debug["skipped"].append({"id": identifier, "reason": "original_or_seen_id"})
            continue
        seen.add(identifier)
        if not coalesce_covers(candidate, candidate):
            debug["skipped"].append({"id": identifier, "reason": "invalid_source_interval"})
            continue
        if any(coalesce_covers(row, candidate) for row in result["results"]):
            debug["skipped"].append({"id": identifier, "reason": "covered_full_source"})
            continue
        if debug["trials"] >= 8:
            debug["trial_limit_reached"] = True
            break
        debug["trials"] += 1
        full, lines, used = candidate["source"], [], 0
        for line in win.physical_lines(full):
            if used + len(line) > chars - result["total_source_chars"]:
                break
            used += len(line)
            lines.append(line)
        if not lines:
            debug["skipped"].append({"id": identifier, "reason": "no_character_prefix"})
            continue
        trial, new = deepcopy(result), deepcopy(candidate)
        set_prefix(new, lines, full)
        trial["results"].append(new)
        if new["source"] != full:
            trial["budget_exhausted"] = True
        clipped = False
        while count_compact(trial, counter) > tokens:
            clipped = True
            trial["budget_exhausted"] = trial["token_usage"]["truncated"] = True
            lines.pop()
            if not lines:
                break
            set_prefix(new, lines, full)
        if not lines:
            debug["skipped"].append({"id": identifier, "reason": "no_token_prefix"})
            continue
        if any(coalesce_covers(row, new) for row in result["results"]):
            debug["skipped"].append({"id": identifier, "reason": "covered_prefix"})
            continue
        count = recount(trial)
        if trial["total_source_chars"] > chars or count > tokens:
            debug["skipped"].append({"id": identifier, "reason": "trial_budget_exceeded"})
            continue
        result = trial
        debug["appended_ids"].append(identifier)
        if clipped:
            debug.setdefault("token_clipped_ids", []).append(identifier)
    if not all_covered(result["results"]):
        return fallback("original_source_not_covered_after_backfill")
    debug["coverage_after"] = True
    recount(result)
    return finish(result)


def verify_packing(row, fixture, counter):
    before = ordinary_pack(fixture["original"], row["stages"]["eligible"], row["stages"]["origins"], counter)
    require(row["pre_postprocess_response"] == before, "Independent maximal character/token packing differs")
    proposal = coalesce_pack(before, row["backfill_candidates"], counter)
    require(row["proposal_response"] == proposal, "Independent deterministic containment/backfill proposal differs")

def verify_trace(row, fixture, counter):
    """Validate proposal source packing/coalescing, then separately audit its guard.

    The local adapter selects the old checker's ordinary coalescing branch only.
    It does not declare the new proposal an immutable coverage control. The
    independent planner above proves its distinct eligible ordering; immutable
    control equality is checked against the actual lazy reference below.
    """
    verify_packing(row, fixture, counter)
    proposal_trace = deepcopy(row)
    proposal_trace["selector"] = "coverage_control"
    proposal_trace["response"] = row["proposal_response"]
    proposal_trace["reference_response"] = proposal_trace["proposal_response"] = None
    proposal_trace["guard_calls"] = 0
    for pair in proposal_trace["stages"]["eligible"]:
        proposal_trace["stages"]["origins"].setdefault(pair["snippet"]["id"], ["retrieval", None])
    text = win.compact_payload(proposal_trace["response"])
    proposal_trace.update(compact_payload=text, compact_sha256=sha(text.encode()),
                          actual_compact_tokens=proposal_trace["response"]["token_usage"]["tokens"])
    win.verify_trace(proposal_trace, fixture, counter, expected_ranked=fixture["ranked"])
    ranked = win.raw_ranked(row["stages"]["ranked"], fixture["inventory"])
    for name in ("reference_response", "response"):
        win.verify_response(row[name], fixture["inventory"], fixture["sources"], fixture["definition_keys"],
                            ranked, counter, raw_rows={item["id"]: item for item in row["ranked_candidates"]})
    text = win.compact_payload(row["response"])
    require(row["compact_payload"] == text and row["compact_sha256"] == sha(text.encode())
            and row["actual_compact_tokens"] == row["response"]["token_usage"]["tokens"],
            "Actual final compact payload/token receipt differs")


def audit_development(root, corpus_root, output, counter):
    names = ("frozen-plan.json", "source-fixtures.json", "control-references.json", "historical-provenance.json",
             "negative-references.json", "negative-provenance.json", "graph-fixtures.json",
             "baseline-gate.json", "prior-coverage-gate.json", "run-start.json", "control-gate.json", "replay.jsonl.gz", "summary.json")
    artifacts = {name: file_sha(output / name) for name in names}
    require(not (output / "failure.json").exists(), "Failed producer attempt cannot be certified complete")
    plan, actual = read(output / "frozen-plan.json"), read(output / "summary.json")
    start, gate = read(output / "run-start.json"), read(output / "control-gate.json")
    legacy_gate, coverage_gate = read(output / "baseline-gate.json"), read(output / "prior-coverage-gate.json")
    declared = {"legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
        "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
        "aspect_fill": {"selector": "aspect_fill_context", "postprocessor": "coalesce_context"},
        "aspect_balanced": {"selector": "aspect_balanced_context", "postprocessor": "coalesce_context"},
        "source_graph": {"selector": "source_graph_context", "postprocessor": "guard_source_context"},
        "source_graph_all": {"selector": "source_graph_all_context", "postprocessor": "guard_source_context"}}
    require(plan["version"] == 1 and plan["study"] == actual["study"] == "source_allocation"
            and plan["conditions"] == declared and plan["settings"] == SETTINGS, "Fixed six-condition source-aware allocation study/caps")
    require(plan["barriers"] == {"legacy_exact_rows": 365, "known_coverage_exact_rows": 337,
            "new_variant_outcomes_computed": 0, "run_parent_control_rows": 730, "referenced_negative_rows": 730}, "Prospective exact control barriers differ")
    require(plan["runtime"] == {"packages": {"tiktoken": "0.14.0", "tree-sitter": "0.26.0",
            "tree-sitter-javascript": "0.25.0", "tree-sitter-typescript": "0.23.2"},
            "encoding": "cl100k_base", "rank_bytes_sha256": old.TOKENIZER_SHA}, "Pinned declared CPU runtime/tokenizer differs")
    require(plan["historical_baseline_commit"] == "5d1e80062638e19101acf3b9e8543066590fd1c5", "Original baseline anchor")
    for name, expected in plan["artifacts"].items():
        require(artifacts[name] == expected, "Prospective allocation artifact binding differs")
    require(start["plan_sha256"] == gate["plan_sha256"] == legacy_gate["plan_sha256"]
            == coverage_gate["plan_sha256"] == actual["plan_sha256"] == artifacts["frozen-plan.json"], "Actual plan bindings differ")
    require(actual["replay_sha256"] == artifacts["replay.jsonl.gz"] and gate["new_variant_rows_before_gate"] == 0
            and legacy_gate["new_variant_outcomes_computed"] == coverage_gate["new_variant_outcomes_computed"] == 0,
            "Every historical control barrier must precede candidate evaluation")
    for name, expected in plan["input_sha256"].items():
        require(file_sha(root / name) == expected
                and git_sha(root, plan["historical_baseline_commit"], name) == expected, "Original historical input differs: " + name)
    proof = historical_proof(root, plan)
    require(read(output / "historical-provenance.json") == proof, "Frozen historical provenance differs")
    negative_proof = parent_allocation_proof(root, plan)
    require(read(output / "negative-provenance.json") == negative_proof, "Frozen historical negative provenance differs")
    require(plan["condition_roles"] == {"controls": list(CONTROLS), "historical_negatives": list(NEGATIVES),
            "new_candidates": list(CANDIDATES)}, "Fixed source allocation condition roles differ")
    chain, execution = new_freeze(root, output, plan, start)
    graph_document = read(output / "graph-fixtures.json")
    expected_graph_sources = {name: file_sha(root / name) for name in GRAPH_SOURCES if not name.endswith("/__init__.py")}
    require(graph_document["schema"] == "source_graph_fixtures_v1"
            and graph_document["extractor_sha256"] == expected_graph_sources
            and all(plan["producer_sha256"].get(name) == digest for name, digest in expected_graph_sources.items()),
            "Frozen conservative graph extractor source/hash inventory differs")
    pointer = read(output / "source-fixtures.json")
    require(pointer["path"] == "results/window-development-2026-10-21/source-fixtures.json"
            and pointer["sha256"] == plan["historical_parent"]["artifacts"][pointer["path"]]
            == file_sha(root / pointer["path"]), "Immutable full source-fixture pointer differs")
    fixtures = []
    document = read(root / pointer["path"])
    require(set(document["fixtures"]) == set(plan["source_sha256"]) == set(graph_document["fixtures"]),
            "Complete source/graph-fixture membership differs")
    for signature, frozen in document["fixtures"].items():
        fixture = win.sources_from_fixture(corpus_root, frozen)
        fixture["inventory"] = {identifier: {name: item[name] for name in win.SNIPPET_FIELDS}
                                for identifier, item in fixture["inventory"].items()}
        fixture.update(definition_keys=frozen["definition_keys"], dataset=frozen["dataset"], source_sha256=frozen["source_sha256"])
        win.verify_inventory(fixture["inventory"], fixture["sources"], fixture["definition_keys"])
        require(plan["source_sha256"][signature] == fixture["source_sha256"], "Pinned complete source-file hash matrix differs")
        graph_fixture = graph_document["fixtures"][signature]
        require(graph_fixture["dataset"] == fixture["dataset"] and graph_fixture["source_sha256"] == fixture["source_sha256"],
                "Graph fixture differs from complete source-file inventory")
        fixture["graph_edges"] = verify_graph_records(graph_fixture["edges"], fixture["sources"], fixture["inventory"])
        fixtures.append(fixture)
    cases = win.original_cases(root, corpus_root)
    require(counts(cases) == plan["counts"] and len(cases) == 365
            and len({(r["dataset"], r["query_id"]) for r in cases}) == 187, "Original365/187 matrix differs")
    by_case = {win.case_key(case): case for case in cases}
    for case in cases:
        matching = [fixture for fixture in fixtures if fixture["dataset"] == case["dataset"] and fixture["sources"] == case["sources"]]
        require(len(matching) == 1, "Each historical case needs one exact source fixture")
        normalized = {identifier: {name: item[name] for name in win.SNIPPET_FIELDS} for identifier, item in case["inventory"].items()}
        require(normalized == matching[0]["inventory"], "Historical canonical source inventory changed")
        case.update(matching[0])
        for alternatives in case["groups"]:
            for target in alternatives:
                require(sha(win.literal(case["sources"], target["path"], *target["evidence_lines"]).encode()) == target["evidence_sha256"],
                        "Historical full physical evidence bytes differ")
                if "source_file_sha256" in target:
                    require(target["source_file_sha256"] == case["source_sha256"][target["path"]], "Historical evidence file hash differs")
    controls = read_controls(root / "results/window-development-2026-10-21/replay.jsonl.gz")
    require(set(controls) == {(name, *win.case_key(case)) for case in cases for name in CONTROLS}, "Exact immutable control case membership")
    reference_cases = [{**old.identity(case), "controls": {name: controls[(name, *win.case_key(case))]["reference"]
                                                        for name in CONTROLS}} for case in cases]
    manifest = read(output / "control-references.json")
    require(manifest["path"] == "results/window-development-2026-10-21/replay.jsonl.gz"
            and manifest["record_hash"] == "SHA256 of UTF-8 sorted-key compact JSON, no newline"
            and manifest["cases"] == reference_cases, "Frozen exact immutable control-reference manifest differs")
    negatives = read_negatives(root / HISTORICAL_ALLOCATION / "replay.jsonl.gz")
    require(set(negatives) == {(name, *win.case_key(case)) for case in cases for name in NEGATIVES},
            "Exact730 immutable negative membership differs")
    negative_manifest = read(output / "negative-references.json")
    expected_negative_cases = [{**old.identity(case), "negatives": {name: negatives[(name, *win.case_key(case))]["reference"]
                                for name in NEGATIVES}} for case in cases]
    require(negative_manifest["path"] == HISTORICAL_ALLOCATION + "/replay.jsonl.gz"
            and negative_manifest["record_hash"] == "SHA256 of UTF-8 sorted-key compact JSON, no newline"
            and negative_manifest["cases"] == expected_negative_cases, "Frozen730 exact negative-reference manifest differs")
    previous = {}
    for _, row in stream_rows(root / "results/selection-development-v2-2026-10-21/replay.jsonl.gz"):
        if row["selector"] == "definition_coverage_backfill":
            require(win.case_key(row) not in previous, "Duplicate original coverage control")
            previous[win.case_key(row)] = {name: row[name] for name in ("response", "compact_sha256", "evidence_ranks")}
    baseline_cases, coverage_cases, parent_cases, all_rows = [], [], [], []
    for case in cases:
        key = win.case_key(case)
        original = case["original"]
        old.verify_response(original, case["inventory"], case["sources"], counter)
        baseline_cases.append({**old.identity(case), "compact_sha256": sha(win.compact_payload(original).encode())})
        if key in previous:
            saved = previous[key]
            coverage_cases.append({**old.identity(case), "compact_sha256": saved["compact_sha256"],
                "response_sha256": sha(canonical(saved["response"])), "evidence_ranks": saved["evidence_ranks"]})
        for name in CONTROLS:
            row = controls[(name, *key)]
            require(row["kind"] == case["kind"] and row["multi_file"] == case["multi_file"]
                    and row["evidence_groups"] == case["groups"], "Immutable control case/gold identity differs")
            require(row["response"]["query"] == original["query"] and row["response"]["scope"] == original["scope"],
                    "Immutable control query/scope differs")
            ranked = win.raw_ranked(row["stages"]["ranked"], case["inventory"])
            require(row["stages"]["ranked"] == case["ranked"], "Immutable control original raw retrieval order/score differs")
            for stage in ("selected", "eligible"):
                require(not win.verify_stage(row["stages"][stage], case["inventory"], case["sources"], case["definition_keys"], ranked),
                        "Historical controls cannot contain composite assemblies")
            win.verify_response(row["response"], case["inventory"], case["sources"], case["definition_keys"], ranked, counter)
            require(row["evidence_ranks"] == win.evidence_ranks(row["response"]["results"], case["groups"]), "Immutable control coverage differs")
            if name == "legacy":
                require(row["response"]["results"] == original["results"] and row["compact_payload"] == win.compact_payload(original)
                        and all(row["response"][field] == original[field] for field in
                                ("total_source_chars", "budget_exhausted", "token_usage")), "Original365 exact full legacy response barrier")
            elif key in previous:
                require(row["response"] == previous[key]["response"] and row["compact_sha256"] == previous[key]["compact_sha256"]
                        and row["evidence_ranks"] == previous[key]["evidence_ranks"], "Original337 exact full coverage response barrier")
            parent_cases.append({**old.identity(case), "selector": name, **row["reference"]})
            row["comparisons"] = {control: win.comparison(controls[(control, *key)]["response"], row["response"], case["groups"])
                                  for control in CONTROLS}
            all_rows.append(row)
    require(len(baseline_cases) == legacy_gate["exact_rows"] == gate["exact_legacy_rows"] == actual["baseline_exact_rows"] == 365,
            "Original365 exact legacy barrier count")
    require(len(coverage_cases) == coverage_gate["exact_rows"] == gate["exact_prior_coverage_rows"] == actual["prior_coverage_exact_rows"] == 337,
            "Original337 exact coverage barrier count")
    require(baseline_cases == legacy_gate["cases"] == gate["baseline_cases"]
            and sha(canonical(baseline_cases)) == plan["baseline_controls_sha256"], "Exact365 legacy barrier receipts differ")
    require(coverage_cases == coverage_gate["cases"] == gate["coverage_cases"]
            and sha(canonical(coverage_cases)) == plan["prior_coverage_controls_sha256"], "Exact337 coverage barrier receipts differ")
    require(parent_cases == gate["parent_control_cases"] and len(parent_cases)
            == gate["exact_parent_control_rows"] == actual["exact_parent_control_rows"] == 730, "All730 exact immutable parent control barriers")
    require(gate["referenced_negative_rows"] == actual["referenced_negative_rows"] == 730,
            "All730 immutable negative references required before new arms")
    for case in cases:
        for name in NEGATIVES:
            saved = negatives[(name, *win.case_key(case))]
            require(saved["kind"] == case["kind"] and saved["multi_file"] == case["multi_file"]
                    and saved["evidence_groups"] == case["groups"], "Frozen negative case/gold identity differs")
            require(saved["response"]["query"] == case["original"]["query"]
                    and saved["response"]["scope"] == case["original"]["scope"], "Frozen negative query/scope differs")
            ranked = win.raw_ranked(case["ranked"], case["inventory"])
            win.verify_response(saved["response"], case["inventory"], case["sources"], case["definition_keys"], ranked, counter)
            require(saved["evidence_ranks"] == win.evidence_ranks(saved["response"]["results"], case["groups"]),
                    "Immutable negative actual source evidence ranks differ")
            for control in CONTROLS:
                require(saved["comparisons"][control] == win.comparison(controls[(control, *win.case_key(case))]["response"],
                        saved["response"], case["groups"]), "Immutable negative historical comparisons differ")
            all_rows.append(saved)
    seen, receipt_counts, guard_counts = set(), {name: Counter() for name in CANDIDATES}, {name: Counter() for name in CANDIDATES}
    for _, row in stream_rows(output / "replay.jsonl.gz"):
        key = (row["selector"], *win.case_key(row))
        require(row["selector"] in CANDIDATES and key not in seen and win.case_key(row) in by_case, "Unexpected/duplicate candidate identity")
        seen.add(key)
        case = by_case[win.case_key(row)]
        require(row["kind"] == case["kind"] and row["multi_file"] == case["multi_file"]
                and row["evidence_groups"] == case["groups"], "Candidate reporting/gold identity differs")
        require(row["response"]["query"] == case["original"]["query"] and row["response"]["scope"] == case["original"]["scope"],
                "Candidate query/scope differs")
        verify_control_references(row, controls)
        receipt = verify_planning(row, controls[("coverage_control", *win.case_key(row))], case)
        receipt_counts[row["selector"]]["activated" if receipt["activated"] else "inactive"] += 1
        receipt_counts[row["selector"]]["accepted" if receipt["accepted"] else "fallback:" + receipt["fallback_reason"]] += 1
        verify_trace(row, case, counter)
        guard_receipt = verify_guard(row, controls[("coverage_control", *win.case_key(row))])
        guard_counts[row["selector"]]["accepted" if guard_receipt["accepted"] else "fallback:" + guard_receipt["fallback_reason"]] += 1
        require(row["evidence_ranks"] == win.evidence_ranks(row["response"]["results"], case["groups"]), "Candidate actual source evidence ranks differ")
        for control in CONTROLS:
            require(row["comparisons"][control] == win.comparison(controls[(control, *win.case_key(row))]["response"],
                    row["response"], case["groups"]), "Candidate all-group/both-control comparison differs")
        all_rows.append({name: row[name] for name in ("subset", "dataset", "query_id", "condition", "selector", "kind",
                         "multi_file", "evidence_groups", "evidence_ranks", "comparisons", "response")})
    require(seen == {(name, *win.case_key(case)) for name in CANDIDATES for case in cases}, "Exact730 two-candidate outcome matrix")
    expected = summary(all_rows, counts(cases))
    expected.update(baseline_exact_rows=365, prior_coverage_exact_rows=337, exact_parent_control_rows=730,
                    plan_sha256=artifacts["frozen-plan.json"], replay_sha256=artifacts["replay.jsonl.gz"])
    require(actual == expected, "Independent full allocation summary/gates differ")
    require(all(start[name] == actual[name] == 0 for name in ("model_calls", "retrieval_calls", "network_calls")),
            "CPU producer zero-call declarations differ")
    require(all(file_sha(output / name) == digest for name, digest in artifacts.items()), "Bound allocation artifacts changed during audit")
    return {"status": "passed", "checks_passed": True, "mode": "development", "study": "source_allocation",
        "artifact_sha256": artifacts, "conditions": list(CONDITIONS), "exact_legacy_controls": 365,
        "exact_prior_coverage_controls": 337, "verified_outcome_rows": 730, "referenced_control_rows": 730,
        "distinct_dataset_query_units": 187, "independently_recomputed_summary": expected,
        "producer_freeze": chain, "producer_execution": execution, "historical_provenance": proof, "negative_historical_provenance": negative_proof,
        "independently_recomputed_planner_counts": {key: dict(value) for key, value in receipt_counts.items()},
        "independently_recomputed_guard_counts": {key: dict(value) for key, value in guard_counts.items()},
        "referenced_negative_rows": 730, "graph_extractor_sha256": expected_graph_sources,
        "trust_boundary": "Frozen existing conservative source graph extractor is shared; all graph-neighbor planning, protected physical-source guards, packing receipts and metrics are independently reconstructed. No selector, planner, backend, model, index or producer is imported.",
        "source_sha256": plan["source_sha256"],
        "limits": "Observed path/full-interval source evidence only; neither independent task trials, native latency, coding correctness, nor provider token usage. No fresh corpus read."}


def forbidden_module(name):
    return name not in ALLOWED_GRAPH_MODULES and any(name == prefix or name.startswith(prefix + ".")
                                                    for prefix in FORBIDDEN)


class RefuseProduction(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if forbidden_module(fullname):
            raise RuntimeError("Independent source allocation audit forbids planner/producer/model imports: " + fullname)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=("development",), default="development")
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root, output = args.root.resolve(), args.results.resolve()
    target = args.output.resolve() if args.output else output / "independent-audit.json"
    failure = target.with_name(target.stem + "-failure.json")
    require(not target.exists() and not failure.exists(), "Preserve prior audit attempts; choose a new output")
    names = ("scripts/audit_source_allocation.py", "tests/test_source_allocation_audit.py", "scripts/audit_windows.py",
             "scripts/audit_selection.py", *GRAPH_SOURCES)
    source = {name: (root / name).read_bytes() for name in names}
    hashes = {name: sha(raw) for name, raw in source.items()}
    for name, expected in hashes.items():
        require(git_sha(root, "HEAD", name) == expected,
                "Commit unchanged independent auditor/tests/extractor before canonical outcome auditing: " + name)
    execution = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    require(not any(forbidden_module(name) for name in sys.modules), "Independent source allocation audit needs a clean CPU process")
    sys.path.insert(0, str(root / "src"))
    finder = RefuseProduction()
    sys.meta_path.insert(0, finder)
    def denied(*unused, **unused_kw):
        raise RuntimeError("Network is forbidden in the independent source allocation audit")
    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
                patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
                patch.object(socket.socket, "sendto", denied):
            counter = win.offline_encoding(Path(os.environ.get("TIKTOKEN_CACHE_DIR", root / ".cache/tokenizers")))
            receipt = audit_development(root, args.corpus_root.resolve(), output, counter)
        require(all((root / name).read_bytes() == raw for name, raw in source.items()), "Independent auditor/extractor changed during execution")
        receipt.update(auditor_sha256=hashes, auditor_execution_commit=execution,
            reviewed_at_utc=datetime.now(timezone.utc).isoformat(), calls={"model": 0, "retrieval": 0, "index": 0, "network": 0},
            candidate_planner_or_producer_modules_imported=False,
            allowed_shared_source_graph_modules=sorted(name for name in ALLOWED_GRAPH_MODULES if name in sys.modules))
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
