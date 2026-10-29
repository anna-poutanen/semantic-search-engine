#!/usr/bin/env python3
"""Independent complementary-context reconstruction; no production/producer import.

The shared input module is a data reader, not an evaluator. Baseline selection,
packing and compact encoding use previously committed independent audit helpers.
The new rule, source/support guard, call contracts and quality gate are rebuilt
here. Import/network guards apply before loading observed data or audit outputs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from fractions import Fraction
import importlib.abc
import importlib.metadata
import json
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch

import complementary_inputs as data
import audit_flow_native as native

base, win, old = native.source, native.win, native.old
require, canonical, sha = data.require, data.canonical, data.sha
ROOT = Path(__file__).resolve().parents[1]
MODE = "complementary_v2"
AUDIT_CLOSURE = tuple("scripts/" + name + ".py" for name in
    ("audit_complementary_context", "audit_flow_native", "audit_flow_allocation", "audit_source_allocation", "audit_windows", "audit_selection", "complementary_inputs"))
NEW_BINDINGS = (*AUDIT_CLOSURE, "scripts/replay_complementary_context.py",
    "tests/test_complementary_context_audit.py", "tests/test_complementary_context.py",
    "tests/test_complementary_context_replay.py", "tests/test_complementary_context_backend.py",
    "tests/test_complementary_inputs.py", "docs/complementary-context-v2-protocol.md",
    "src/semantic_search/complementary_context.py")


def ident(item, keys):
    key = base.canonical_key(keys.get(item["id"]))
    return ("definition", key) if key is not None else ("window", item["id"])


def rational(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def support(words, aspects, df):
    result = []
    for aspect in aspects:
        denominator = sum((Fraction(1, max(1, df[term])) for term in aspect), Fraction())
        numerator = sum((Fraction(1, max(1, df[term])) for term in aspect & words), Fraction())
        result.append(numerator / denominator if denominator else Fraction())
    return tuple(result)


def best(ids, scores, count):
    return tuple(max((scores[identifier][index] for identifier in ids), default=Fraction())
                 for index in range(count))


def canonical_graph(keys, edges, ambiguous):
    definitions = defaultdict(set)
    for value in keys.values():
        key = base.canonical_key(value)
        if key is not None:
            definitions[key[:2]].add(key)
    unique = {label: next(iter(values)) for label, values in definitions.items()
              if len(values) == 1 and label not in ambiguous}
    direct = sorted({(caller, callee) for caller, targets in edges.items() if caller in unique
                     for callee in targets if callee in unique and caller != callee})
    return unique, direct


def planner(ranked, selected, baseline, origins, keys, edges, ambiguous, *, query, max_results=8):
    """Independently implement the one fixed prospective Fraction/portfolio rule."""
    selected, baseline, origins = deepcopy(selected), deepcopy(baseline), deepcopy(origins)
    clauses = base.literal_clauses(query)
    aspects = [base.clause_words(clause) for clause in clauses]
    all_terms = set().union(*aspects) if aspects else set()
    ranks, portfolio, seen = {}, [], Counter()
    for rank, pair in enumerate(ranked, 1):
        item = pair["snippet"]
        if item["id"] in ranks:
            continue
        ranks[item["id"]] = rank
        identity = ident(item, keys)
        if seen[identity] < 2:
            portfolio.append(deepcopy(pair))
            seen[identity] += 1
    relevant = {pair["snippet"]["id"]: pair["snippet"] for pair in selected + baseline + portfolio}
    mapped = {identifier: list(key) for identifier, item in relevant.items()
              if (key := base.canonical_key(keys.get(identifier))) is not None}
    owner = {pair["snippet"]["id"]: origins[pair["snippet"]["id"]][1] for pair in baseline
             if origins.get(pair["snippet"]["id"], ["retrieval", None])[0] == "dependency"}
    base_ids = [pair["snippet"]["id"] for pair in baseline]
    anchor_ids = [pair["snippet"]["id"] for pair in selected[:min(5, max_results)]]
    plan = {"schema": "complementary_context_v2", "mode": MODE, "activated": bool(clauses),
        "clauses": clauses, "aspect_terms": [sorted(aspect) for aspect in aspects], "term_df": {},
        "raw_ranks": ranks, "canonical_keys": mapped,
        "base_selected_ids": [pair["snippet"]["id"] for pair in selected],
        "baseline_eligible_ids": base_ids, "portfolio_ids": [pair["snippet"]["id"] for pair in portfolio],
        "raw_windows_per_definition": 2, "dependency_windows_outside_raw_exempt": True,
        "anchor_ids": anchor_ids, "dependency_owners": owner, "candidate_terms": {}, "candidate_scores": {},
        "graph_witnesses": [], "initial_protected_ids": [], "protected_baseline_ids": [],
        "protected_keys": [], "protected_windows": [], "candidate_trials": [], "added_ids": [],
        "evicted_ids": [], "proposed_eligible_ids": list(base_ids), "accepted": False,
        "fallback_reason": None, "termination": None}
    def finish(eligible, reason=None, termination=None):
        plan.update(fallback_reason=reason, termination=termination)
        return (deepcopy(portfolio if plan["accepted"] else selected), deepcopy(eligible),
                deepcopy(origins), deepcopy(plan))
    if not clauses:
        return finish(baseline, "no_literal_clauses", "inactive")
    per_definition = defaultdict(set)
    for pair in ranked:
        item = pair["snippet"]
        per_definition[ident(item, keys)].update(base.clause_words(
            item["path"] + " " + item["symbol"] + "\n" + item["text"]) & all_terms)
    df = Counter(term for values in per_definition.values() for term in values)
    plan["term_df"] = {term: df[term] for term in sorted(all_terms)}
    features = {identifier: base.clause_words(item["path"] + " " + item["symbol"] + "\n" + item["text"]) & all_terms
                for identifier, item in relevant.items()}
    scores = {identifier: support(words, aspects, df) for identifier, words in features.items()}
    plan["candidate_terms"] = {identifier: sorted(words) for identifier, words in features.items()}
    plan["candidate_scores"] = {identifier: [rational(value) for value in values] for identifier, values in scores.items()}
    unique, direct = canonical_graph(keys, edges, ambiguous)
    witnesses = []
    for pair in selected[:min(5, max_results)]:
        item = pair["snippet"]
        label, key = (item["path"], item["symbol"]), base.canonical_key(keys.get(item["id"]))
        if key is None or unique.get(label) != key:
            continue
        for caller, callee in direct:
            if label not in (caller, callee):
                continue
            neighbor = callee if caller == label else caller
            witnesses.append({"anchor_id": item["id"], "anchor_key": list(key),
                "direction": "callee" if caller == label else "caller", "neighbor_key": list(unique[neighbor]),
                "source_key": list(unique[caller]), "target_key": list(unique[callee])})
    protected_keys = {tuple(w["neighbor_key"]) for w in witnesses}
    protected = set(anchor_ids) | set(owner) | set(owner.values())
    protected.update(identifier for identifier in base_ids if tuple(mapped.get(identifier, ())) in protected_keys)
    plan["graph_witnesses"] = witnesses
    plan["initial_protected_ids"] = [identifier for identifier in base_ids if identifier in protected]
    additions, termination = [], None
    remaining = [pair for pair in portfolio if pair["snippet"]["id"] not in base_ids]
    while len(additions) < 2:
        represented = protected | {pair["snippet"]["id"] for pair in additions}
        represented_support = best(represented, scores, len(aspects))
        options = []
        for pair in remaining:
            item, identifier = pair["snippet"], pair["snippet"]["id"]
            if sum(other["snippet"]["id"] in ranks and ident(other["snippet"], keys) == ident(item, keys)
                   for other in baseline + additions) >= 2:
                continue
            gain = sum((max(Fraction(), new - prior) for new, prior in zip(scores[identifier], represented_support)), Fraction())
            if gain:
                options.append((gain / (ranks[identifier] + 8), ranks[identifier], pair, gain))
        if not options:
            termination = "no_positive_aspect_gain"
            break
        utility, rank, pair, gain = min(options, key=lambda value: (-value[0], value[1]))
        item, identifier = pair["snippet"], pair["snippet"]["id"]
        label = item["path"], item["symbol"]
        exact = base.canonical_key(keys.get(identifier))
        parents = sorted({caller for caller, callee in direct if callee == label}) \
            if exact is not None and unique.get(label) == exact else []
        flow_keys, flow_witnesses = set(), []
        for parent in parents:
            flow_keys.add(unique[parent])
            for caller, member in direct:
                if caller == parent:
                    flow_keys.add(unique[member])
                    flow_witnesses.append({"candidate_id": identifier, "candidate_key": list(unique[label]),
                        "orchestrator_key": list(unique[parent]), "member_key": list(unique[member])})
        trial_keys = protected_keys | flow_keys
        trial_protected = protected | {other for other in base_ids if tuple(mapped.get(other, ())) in trial_keys}
        spare = [other for other in base_ids if other not in trial_protected]
        fits = len(spare) >= len(additions) + 1
        plan["candidate_trials"].append({"candidate_id": identifier, "raw_rank": rank,
            "aspect_scores": [rational(value) for value in scores[identifier]],
            "represented_support": [rational(value) for value in represented_support],
            "gain": rational(gain), "utility": rational(utility),
            "orchestrator_keys": [list(unique[parent]) for parent in parents],
            "flow_keys": [list(key) for key in sorted(flow_keys)], "flow_witnesses": flow_witnesses,
            "trial_protected_baseline_ids": [other for other in base_ids if other in trial_protected],
            "exchangeable_baseline_ids": spare, "required_slots": len(additions) + 1,
            "accepted": fits, "stop_reason": None if fits else "insufficient_exchangeable_slots"})
        if not fits:
            termination = "capacity_blocked"
            break
        protected, protected_keys = trial_protected, trial_keys
        additions.append(pair)
        remaining = [other for other in remaining if other["snippet"]["id"] != identifier]
    plan["protected_baseline_ids"] = [identifier for identifier in base_ids if identifier in protected]
    plan["protected_keys"] = [list(key) for key in sorted(protected_keys)]
    windows = {}
    for pair in selected + baseline:
        item = pair["snippet"]
        if item["id"] in protected or tuple(mapped.get(item["id"], ())) in protected_keys:
            windows.setdefault(item["id"], {field: item[field] for field in ("id", "path", "start_line", "end_line", "content_hash")})
    plan["protected_windows"] = list(windows.values())
    if not additions:
        return finish(baseline, termination, termination)
    spare = [identifier for identifier in base_ids if identifier not in protected]
    removed = set(spare[-len(additions):])
    eligible = [pair for pair in baseline if pair["snippet"]["id"] in protected] + additions
    eligible += [pair for pair in baseline if pair["snippet"]["id"] not in protected | removed]
    for pair in additions:
        origins[pair["snippet"]["id"]] = ["retrieval", None]
    plan.update(added_ids=[pair["snippet"]["id"] for pair in additions],
        evicted_ids=[identifier for identifier in base_ids if identifier in removed],
        proposed_eligible_ids=[pair["snippet"]["id"] for pair in eligible], accepted=True)
    return finish(eligible, termination=termination or "max_additions")


def actual_support(response, plan):
    aspects = [set(values) for values in plan["aspect_terms"]]
    values = [support(base.clause_words(row["path"] + " " + row["symbol"] + "\n" + row["source"]),
                      aspects, plan["term_df"]) for row in response["results"]]
    return tuple(max((value[index] for value in values), default=Fraction()) for index in range(len(aspects)))


def guard(reference, proposal, plan, *, self_reference=False):
    if not plan["accepted"]:
        require(self_reference, "Inactive guard must receive actual identical object")
        return deepcopy(reference), {"schema": "complementary_guard_v2", "mode": MODE,
            "reference_kind": "self_reference", "protected_ids": [], "source_preserved": True,
            "aspect_preserved": True, "reference_support": [], "proposal_support": [],
            "accepted": False, "fallback_reason": "inactive_plan"}
    ids, keys = set(plan["protected_baseline_ids"]), {tuple(key) for key in plan["protected_keys"]}
    protected = [row for row in reference["results"] if row["id"] in ids or row.get("origin") == "dependency"
        or tuple(plan["canonical_keys"].get(row["id"], ())) in keys
        or any(row["path"] == window["path"] and max(row["start_line"], window["start_line"]) <=
               min(row["end_line"], window["end_line"]) for window in plan["protected_windows"])]
    def covers(now, before):
        return base.source_covers(now, before) and (now.get("id") != before.get("id") or all(
            (field in now) == (field in before) and type(now.get(field)) is type(before.get(field))
            and now.get(field) == before.get(field)
            for field in ("retrieval_score", "h2o_score", "qwen_score")))
    preserved = all(any(covers(now, before) for now in proposal["results"]) for before in protected)
    prior, current = actual_support(reference, plan), actual_support(proposal, plan)
    aspect = all(new >= before for before, new in zip(prior, current))
    accepted = preserved and aspect
    return deepcopy(proposal if accepted else reference), {
        "schema": "complementary_guard_v2", "mode": MODE, "reference_kind": "coverage_factory",
        "protected_ids": [row["id"] for row in protected], "source_preserved": preserved,
        "aspect_preserved": aspect, "reference_support": [rational(value) for value in prior],
        "proposal_support": [rational(value) for value in current], "accepted": accepted,
        "fallback_reason": None if accepted else "protected_source_not_preserved" if not preserved
            else "delivered_aspect_support_decreased"}


def pairs(case):
    return [{"snippet": asdict(item), "score": score} for item, score in case["pairs"]]


def full_rows(case, ranked):
    rows = native.full_rows(ranked)
    if not (case["original"]["results"] and "qwen_score" in case["original"]["results"][0]):
        for row in rows:
            row.pop("qwen_score", None)
    return rows


def reconstruct_control(case, name, counter):
    ranked = pairs(case)
    selected, eligible, origins = native.baseline(ranked,
        {identifier: asdict(item) for identifier, item in case["inventory"].items()},
        case["definition_keys"], case["graph"].edges, canonical_definitions=name == "coverage_control")
    before = base.ordinary_pack(case["original"], eligible, origins, counter)
    response = before if name == "legacy" else base.coalesce_pack(before, full_rows(case, selected), counter)
    return response, (selected, eligible, origins)


def quality(case, response):
    ranks = win.evidence_ranks(response["results"], case["groups"], qualified=case["qualified"])
    return {"ranks": ranks, "groups_hit": sum(rank is not None for rank in ranks),
        "complete": case["kind"] == "present" and bool(ranks) and None not in ranks,
        "first_hit": case["kind"] == "present" and bool(ranks) and ranks[0] is not None}


def reported_score(case, response):
    value = quality(case, response)
    return {"evidence_ranks": value["ranks"], "complete": value["complete"],
        "covered_groups": value["groups_hit"], "single_file_first_hit": not case["multi_file"] and value["first_hit"]}


def differences(case, response, controls):
    after = reported_score(case, response)
    result = {}
    for name in data.CONTROLS:
        before = reported_score(case, controls[name, data.case_key(case)]["response"])
        result[name] = {"control_ranks": before["evidence_ranks"], "variant_ranks": after["evidence_ranks"],
            "group_gains": [index for index, (a, b) in enumerate(zip(before["evidence_ranks"], after["evidence_ranks"]), 1)
                            if a is None and b is not None],
            "group_losses": [index for index, (a, b) in enumerate(zip(before["evidence_ranks"], after["evidence_ranks"]), 1)
                             if a is not None and b is None],
            "complete_gain": after["complete"] and not before["complete"],
            "complete_loss": before["complete"] and not after["complete"],
            "single_file_first_hit_loss": before["single_file_first_hit"] and not after["single_file_first_hit"]}
    return result


def quality_gate(cases, responses, controls):
    comparisons, gains = {}, set()
    for name in data.CONTROLS:
        losses, complete_losses, first_losses, complete_gains = [], [], [], []
        for case in cases:
            key = data.case_key(case)
            before = quality(case, controls[name, key]["response"])
            after = quality(case, responses[key])
            identity = dict(zip(("subset", "dataset", "query_id", "condition"), key))
            lost = [number for number, (a, b) in enumerate(zip(before["ranks"], after["ranks"]), 1) if a is not None and b is None]
            if lost:
                losses.append({**identity, "groups": lost})
            if before["complete"] and not after["complete"]:
                complete_losses.append(identity)
            if not case["multi_file"] and before["first_hit"] and not after["first_hit"]:
                first_losses.append(identity)
            if case["multi_file"] and after["complete"] and not before["complete"]:
                complete_gains.append(identity)
                if name == "coverage_control":
                    gains.add((case["dataset"], case["query_id"]))
        comparisons[name] = {"group_losses": losses, "complete_losses": complete_losses,
            "single_file_first_hit_losses": first_losses, "complete_multi_gains": complete_gains}
    passed = bool(gains) and not any(value[field] for value in comparisons.values()
        for field in ("group_losses", "complete_losses", "single_file_first_hit_losses"))
    return {"qualified": passed, "against_controls": comparisons,
        "additional_distinct_complete_multi_queries_vs_coverage": [list(key) for key in sorted(gains)],
        "fresh_native_required_if_qualified": True, "automatic_default_change": False}


def verify_row(row, case, controls, counter):
    key = data.case_key(case)
    selected, baseline, origins = reconstruct_control(case, "coverage_control", counter)[1]
    ranked = pairs(case)
    selected, eligible, origins, plan = planner(ranked, selected, baseline, origins,
        case["definition_keys"], case["graph"].edges, case["ambiguous_symbols"], query=case["original"]["query"])
    require(canonical(row["selection_plan"]) == canonical(plan)
            and canonical(row["complementary_plan_receipt"]) == canonical(plan), "Independent plan differs")
    stages = row["stages"]
    require(canonical(stages["ranked"]) == canonical(ranked), "Original raw ranks/scalars/source changed")
    for field, expected in (("selected", selected), ("eligible", eligible), ("origins", origins),
                            ("definition_keys", case["definition_keys"])):
        require(canonical(stages[field]) == canonical(expected), "Independent stage differs: " + field)
    require(canonical(row["backfill_candidates"]) == canonical(full_rows(case, selected)), "Backfill source/scalars differ")
    require(canonical(row["ranked_candidates"]) == canonical(full_rows(case, ranked)), "Drained raw source/scalars differ")
    require(canonical(stages["ambiguous_symbols"]) == canonical(sorted(case["ambiguous_symbols"])),
            "Complete canonical ambiguity labels differ")
    before = base.ordinary_pack(case["original"], eligible, origins, counter)
    proposal = base.coalesce_pack(before, full_rows(case, selected), counter)
    reference = controls["coverage_control", key]["response"] if plan["accepted"] else proposal
    expected, receipt = guard(reference, proposal, plan, self_reference=not plan["accepted"])
    for field, value in (("pre_postprocess_response", before), ("proposal_response", proposal),
                         ("reference_response", reference), ("complementary_guard_receipt", receipt), ("response", expected)):
        require(canonical(row[field]) == canonical(value), "Independent packed/guarded field differs: " + field)
    require(type(row["planner_calls"]) is int and row["planner_calls"] == 1
            and type(row["guard_calls"]) is int and row["guard_calls"] == 1
            and type(row["coalesce_calls"]) is int and row["coalesce_calls"] == 1
            and type(row["reference_calls"]) is int and row["reference_calls"] == int(plan["accepted"])
            and row["self_reference_identity"] is (not plan["accepted"]), "Actual fastpath/factory/guard counts differ")
    require(canonical(row["evidence_groups"]) == canonical(case["groups"]) and row["qualified"] is case["qualified"]
            and row["multi_file"] is case["multi_file"] and row["kind"] == case["kind"], "Original label semantics changed")
    require(row["compact_payload"] == win.compact_payload(expected)
            and row["compact_sha256"] == sha(row["compact_payload"].encode())
            and row["actual_compact_tokens"] == len(counter.encode(row["compact_payload"], disallowed_special=()))
            == expected["token_usage"]["tokens"] <= 4096, "Exact compact source envelope differs")
    require(len(expected["results"]) <= 8 and sum(len(item["source"]) for item in expected["results"]) <= 12000,
            "Fixed context caps exceeded")
    for name in data.CONTROLS:
        require(canonical(row["control_references"][name]) == canonical(controls[name, key]["reference"]), "Original control identity changed")
    for field, value in reported_score(case, expected).items():
        require(canonical(row[field]) == canonical(value), "Independent individual evidence score differs: " + field)
    require(canonical(row["comparisons"]) == canonical(differences(case, expected, controls)), "Independent individual comparison differs")
    require(row["selector"] == MODE, "Only the one fixed new candidate may be executed")
    return expected


def verify_manifests(root, output, cases, controls, negatives, parents):
    manifest = data.read(output / "input-manifest.json")
    require(manifest["parents"] == parents and manifest["schema"] == "complementary_inputs_v2", "Immutable input parent proof differs")
    expected = []
    references = []
    for case in cases:
        identity = dict(zip(("subset", "dataset", "query_id", "condition"), data.case_key(case)))
        expected.append({**identity, "kind": case["kind"], "multi_file": case["multi_file"], "qualified": case["qualified"],
            "query_sha256": sha(case["original"]["query"].encode()), "groups_sha256": sha(canonical(case["groups"])),
            "raw_pairs_sha256": sha(canonical([{"id": item.id, "score": score} for item, score in case["pairs"]])),
            "inventory_sha256": sha(canonical([asdict(item) for item in case["inventory"].values()])),
            "definition_keys_sha256": sha(canonical(case["definition_keys"])),
            "edges_sha256": sha(canonical([[list(key), [list(target) for target in values]] for key, values in case["graph"].edges.items()]))})
        references.append({**identity, "controls": {name: controls[name, data.case_key(case)]["reference"] for name in data.CONTROLS}})
    require(manifest["cases"] == expected, "Complete source/window/graph/query input manifests differ")
    require(data.read(output / "control-references.json") == references, "Immutable786control pointers differ")
    require(data.read(output / "negative-references.json") == negatives, "Immutable2948negative pointers differ")


def aggregate(cases, rows, controls):
    metrics = {name: {} for name in (*data.CONTROLS, MODE)}
    changes = {name: {field: [] for field in ("group_gains", "group_losses", "complete_gains", "complete_losses",
                                             "single_file_first_hit_losses")} for name in data.CONTROLS}
    by_key = {data.case_key(row): row for row in rows}
    for case in cases:
        key, row = data.case_key(case), by_key[data.case_key(case)]
        identity = dict(zip(("subset", "dataset", "query_id", "condition"), key))
        delta = differences(case, row["response"], controls)
        for name, value in delta.items():
            for field in ("group_gains", "group_losses"):
                if value[field]:
                    changes[name][field].append({**identity, "groups": value[field]})
            for flag, field in (("complete_gain", "complete_gains"), ("complete_loss", "complete_losses"),
                                ("single_file_first_hit_loss", "single_file_first_hit_losses")):
                if value[flag]:
                    changes[name][field].append(identity)
        stratum = "/".join(case[name] for name in ("subset", "dataset", "condition"))
        for name in metrics:
            response = row["response"] if name == MODE else controls[name, key]["response"]
            value = reported_score(case, response)
            metrics[name].setdefault(stratum, Counter()).update(rows=1, present=case["kind"] == "present",
                absent=case["kind"] == "absent", required_groups=len(case["groups"]), covered_groups=value["covered_groups"],
                complete=value["complete"], multi_file_complete=bool(case["multi_file"] and value["complete"]),
                single_file_first_hit=value["single_file_first_hit"])
    return metrics, changes


def verify_control_gate(barrier, parity, receipts, plan_sha, negative_count):
    require(canonical(parity) == canonical({"exact_control_rows": 786,
        "new_variant_outcomes_computed": 0, "cases": receipts}), "Frozen full786 control parity receipts differ")
    require(canonical(barrier) == canonical({"exact_control_rows": 786, "new_variant_rows_before_gate": 0,
        "referenced_negative_rows": negative_count, "cases": receipts, "plan_sha256": plan_sha}),
        "Actual full786 barrier/source/reference receipts differ")


def audit(root, output, counter):
    cases, controls, negatives, parents = data.load(root)
    plan = data.read(output / "frozen-plan.json")
    start = data.read(output / "run-start.json")
    require(plan["study"] == MODE and plan["conditions"] == [*data.CONTROLS, MODE]
            and plan["settings"] == {"max_results": 8, "source_budget": 12000, "token_budget": 4096, "include_dependencies": True},
            "Only the declared fixed rule and unchanged budgets are allowed")
    packages = {"tiktoken": "0.14.0", "tree-sitter": "0.26.0", "tree-sitter-javascript": "0.25.0", "tree-sitter-typescript": "0.23.2"}
    require(plan["runtime"] == {"packages": packages, "encoding": "cl100k_base", "rank_bytes_sha256": old.TOKENIZER_SHA}
            and all(importlib.metadata.version(name) == version for name, version in packages.items()), "Pinned CPU runtime differs")
    require(not (output / "failure.json").exists(), "Failed attempts must remain failed and exclusive")
    bound = {**plan["producer_sha256"], **plan["input_sha256"]}
    require(set(NEW_BINDINGS) <= set(plan["producer_sha256"]), "New implementation/tests/audit closure binding incomplete")
    bound[(output / "frozen-plan.json").relative_to(root).as_posix()] = data.file_sha(output / "frozen-plan.json")
    for name, digest in bound.items():
        require(data.file_sha(root / name) == data.git_sha(root, start["execution_commit"], name) == digest,
                "New committed source/input/plan changed: " + name)
    require(start["plan_sha256"] == data.file_sha(output / "frozen-plan.json"), "New run plan binding differs")
    freeze = start["prospective_freeze"]["commit"]
    require(old.committed_freeze(root, output / "frozen-plan.json", bound, start["created_at_utc"])
            == start["prospective_freeze"], "Actual prospective freeze chronology differs")
    for name, digest in bound.items():
        require(data.git_sha(root, freeze, name) == digest, "New prospective freeze blob differs")
    for first, last in ((freeze, start["execution_commit"]), (start["execution_commit"], "HEAD")):
        subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", first, last])
    require(plan["counts"] == data.counts(cases) and plan["historical_parents"] == parents,
            "Complete observed counts or historical original-identity proof changed")
    verify_manifests(root, output, cases, controls, negatives, parents)
    barrier = data.read(output / "control-gate.json")
    require(barrier["exact_control_rows"] == 786 and barrier["new_variant_rows_before_gate"] == 0,
            "All786 controls must precede candidate outcomes")
    require(barrier["plan_sha256"] == start["plan_sha256"], "Control barrier plan binding differs")
    control_receipts = []
    for case in cases:
        for name in data.CONTROLS:
            expected = reconstruct_control(case, name, counter)[0]
            require(canonical(expected) == canonical(controls[name, data.case_key(case)]["response"]),
                    "Independent exact historical control differs")
            require(sha(win.compact_payload(expected).encode()) == controls[name, data.case_key(case)]["reference"]["compact_sha256"],
                    "Independent original exact compact differs")
            control_receipts.append({**dict(zip(("subset", "dataset", "query_id", "condition"), data.case_key(case))),
                "selector": name, "source_view_sha256": sha(canonical(expected)),
                "compact_sha256": sha(win.compact_payload(expected).encode()), "reference": controls[name, data.case_key(case)]["reference"]})
    verify_control_gate(barrier, data.read(output / "control-parity.json"), control_receipts, start["plan_sha256"], len(negatives))
    by_key = {data.case_key(case): case for case in cases}
    responses, rows = {}, []
    for _, row in data.rows(output / "replay.jsonl.gz"):
        key = data.case_key(row)
        require(key in by_key and key not in responses, "Unexpected/duplicate candidate case")
        responses[key] = verify_row(row, by_key[key], controls, counter)
        rows.append(row)
    require(len(responses) == 393, "Complete393 candidate matrix required")
    gate = quality_gate(cases, responses, controls)
    summary = data.read(output / "summary.json")
    require(canonical(summary["qualification"]) == canonical(gate), "Independent quality gate differs")
    metrics, changes = aggregate(cases, rows, controls)
    require(canonical(summary["metrics_by_study_dataset_original_condition"]) == canonical(metrics)
            and canonical(summary["all_changes"]) == canonical(changes),
            "Independent stratum metrics or all individual changes differ")
    require(summary["counts"] == data.counts(cases) and summary["stored_new_candidate_rows"] == 393
            and summary["referenced_control_rows"] == summary["exact_control_rows"] == 786
            and summary["referenced_negative_rows"] == len(negatives)
            and summary["plan_sha256"] == start["plan_sha256"]
            and summary["replay_sha256"] == data.file_sha(output / "replay.jsonl.gz")
            and summary["calls"] == start["calls"] == {name: 0 for name in ("model", "retrieval", "index", "network")}
            and start["resume_allowed"] is False and summary["automatic_default_change"] is False,
            "Summary count/artifact/call or scope binding differs")
    require(summary["activity"] == {"admitted_plans": sum(row["selection_plan"]["accepted"] for row in rows),
        "accepted_guards": sum(row["complementary_guard_receipt"]["accepted"] for row in rows),
        "factory_calls": sum(row["reference_calls"] for row in rows), "self_references": sum(row["self_reference_identity"] for row in rows)},
        "Actual fastpath/guard activity summary differs")
    artifacts = {path.name: data.file_sha(path) for path in output.iterdir() if path.is_file()
                 and path.name not in ("independent-audit.json", "independent-review.md")}
    return {"status": "passed", "checks_passed": True, "study": MODE,
        "verified_candidate_rows": 393, "exact_control_rows": 786, "distinct_query_units": 201,
        "retained_negative_references": len(negatives), "independently_recomputed_qualification": gate,
        "producer_execution": start["execution_commit"], "producer_freeze": start["prospective_freeze"],
        "auditor_execution_commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        "auditor_sha256": {name: plan["producer_sha256"][name] for name in (*AUDIT_CLOSURE, "tests/test_complementary_context_audit.py")},
        "artifact_sha256": artifacts, "model_calls": 0, "retrieval_calls": 0, "network_calls": 0,
        "source_boundary": "Previously audited original complete inventories/raw ranks/static graphs as immutable data; no source fetch or re-extraction."}


class RefuseProduction(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ("semantic_search", "replay_", "benchmark_", "mlx", "torch", "transformers", "numpy", "jax")
        if any(fullname == prefix or fullname.startswith(prefix + ".") or fullname.startswith(prefix)
               for prefix in forbidden):
            raise RuntimeError("Independent audit forbids candidate/producer/model import: " + fullname)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tokenizer-cache", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    require(not (output / "independent-audit.json").exists(), "Audit output must be exclusive")
    require(not any(name == "semantic_search" or name.startswith("semantic_search.") for name in sys.modules),
            "Independent process must not already contain production imports")
    sys.meta_path.insert(0, RefuseProduction())
    with patch.object(socket, "create_connection", side_effect=RuntimeError("Independent audit forbids network")), \
         patch.object(socket, "getaddrinfo", side_effect=RuntimeError("Independent audit forbids network")), \
         patch.object(socket.socket, "connect", side_effect=RuntimeError("Independent audit forbids network")), \
         patch.object(socket.socket, "connect_ex", side_effect=RuntimeError("Independent audit forbids network")), \
         patch.object(socket.socket, "sendto", side_effect=RuntimeError("Independent audit forbids network")):
        counter = old.offline_encoding(args.tokenizer_cache or root / ".cache/tokenizers")
        result = audit(root, output, counter)
    with (output / "independent-audit.json").open("x") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in ("status", "verified_candidate_rows", "exact_control_rows",
                                                 "distinct_query_units", "independently_recomputed_qualification")}))


if __name__ == "__main__":
    main()
