#!/usr/bin/env python3
"""Independently audit frozen source/query allocation without producer imports.

All runtime planning is reconstructed with literal source and rational arithmetic.
Historical Python files are inspected only as committed Git blobs, never loaded.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import gzip
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("allocation_independent_window_helpers", ROOT / "scripts/audit_windows.py")
win = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(win)
old = win.old
require, sha, read, canonical = win.require, win.sha, win.read, win.canonical
CONDITIONS = ("legacy", "coverage_control", "aspect_fill", "aspect_balanced")
CANDIDATES = CONDITIONS[2:]
SETTINGS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096,
            "include_dependencies": True, "rerank": False, "candidates": 50}
STOP_WORDS = frozenset("""
where is are the a an and or to of for in on at by with from when while does do as
be it its into only that this then otherwise can without which each either any
all before after also through using return returns make made
""".split())
FORBIDDEN = (*win.FORBIDDEN, "replay_allocation", "benchmark_allocation", "replay_context_allocation")


def words(text):
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    result = set()
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if token in STOP_WORDS or len(token) < 3:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
            token = token[:-1]
        result.add(token)
    return result


def literal_clauses(query):
    require(isinstance(query, str) and bool(query.strip()), "Nonempty original query required")
    chunks = [part.strip(" ?.,") for part in re.split(r",|\band\b|\bwhile\b|\bthen\b", query, flags=re.I)]
    chunks = [part for part in chunks if len(words(part)) >= 3]
    return chunks[:4] if len(chunks) >= 2 else []


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


def fraction_json(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def planner(ranked, baseline_selected, baseline_eligible, baseline_origins, keys,
            *, mode, query, max_results=8):
    """Independent full deterministic receipt, preserving a frozen baseline.

    The baseline is an immutable, already audited historical input, rather than
    a newly executed static graph or imported production selector.
    """
    require(mode in CANDIDATES and type(max_results) is int and max_results >= 0, "Fixed allocation mode/cap")
    selected = unique_definitions(ranked, keys)
    require(selected == baseline_selected, "Original canonical representative order changed")
    require(len(baseline_eligible) <= max_results, "Original eligible result cap")
    baseline_ids = [pair["snippet"]["id"] for pair in baseline_eligible]
    require(len(set(baseline_ids)) == len(baseline_ids), "Duplicate baseline eligible row")
    clauses = literal_clauses(query)
    receipt = {"schema": "aspect_allocation_v1", "mode": mode, "activated": bool(clauses),
        "clauses": clauses, "query_terms": [], "raw_ranks": {}, "candidate_terms": {}, "term_df": {},
        "baseline_eligible_ids": baseline_ids, "protected_anchor_ids": [], "protected_dependency_ids": [],
        "protected_owner_ids": [], "protected_ids": [], "baseline_query_terms": [], "picks": [],
        "proposed_eligible_ids": [], "proposed_query_terms": [], "accepted": False, "fallback_reason": None}

    def fallback(reason):
        receipt["fallback_reason"] = reason
        return deepcopy(selected), deepcopy(baseline_eligible), deepcopy(baseline_origins), receipt

    if not clauses:
        return fallback("no_literal_clauses")
    query_terms = set().union(*(words(part) for part in clauses))
    features = lambda item: words(item["path"] + " " + item["symbol"] + "\n" + item["text"]) & query_terms
    ranks, terms, definitions = {}, {}, {}
    for position, pair in enumerate(ranked, 1):
        item = pair["snippet"]
        ranks.setdefault(item["id"], position)
        terms[item["id"]] = features(item)
        definitions.setdefault(definition_id(item, keys), set()).update(terms[item["id"]])
    frequencies = Counter(term for matched in definitions.values() for term in matched)
    receipt.update(query_terms=sorted(query_terms), raw_ranks=ranks,
        candidate_terms={key: sorted(value) for key, value in terms.items()},
        term_df={term: frequencies[term] for term in sorted(query_terms)})
    anchors = [pair["snippet"]["id"] for pair in selected[:min(5 if mode == "aspect_fill" else 3, max_results)]]
    dependencies, owners = [], []
    for pair in baseline_eligible:
        identifier = pair["snippet"]["id"]
        origin, owner = baseline_origins.get(identifier, ("retrieval", None))
        if origin == "dependency":
            dependencies.append(identifier)
            if owner not in owners:
                owners.append(owner)
    protected = set(anchors) | set(dependencies) | set(owners)
    proposal = [pair for pair in baseline_eligible if pair["snippet"]["id"] in protected]
    covered = set().union(*(features(pair["snippet"]) for pair in proposal))
    baseline_terms = set().union(*(features(pair["snippet"]) for pair in baseline_eligible))
    receipt.update(protected_anchor_ids=anchors, protected_dependency_ids=dependencies,
        protected_owner_ids=owners, protected_ids=[pair["snippet"]["id"] for pair in proposal],
        baseline_query_terms=sorted(baseline_terms))
    if len(proposal) >= len(baseline_eligible):
        return fallback("no_free_slots")
    included = {definition_id(pair["snippet"], keys) for pair in proposal}
    available = [pair for pair in selected if definition_id(pair["snippet"], keys) not in included]
    positive = 0
    while len(proposal) < len(baseline_eligible) and available:
        utilities = []
        for position, pair in enumerate(available):
            identifier = pair["snippet"]["id"]
            novel = terms[identifier] - covered
            gain = sum((Fraction(1, frequencies[term]) for term in novel), Fraction())
            utility = gain / ranks[identifier]
            utilities.append((utility, -ranks[identifier], position, gain, novel))
        utility, _, position, gain, novel = max(utilities, key=lambda entry: entry[:2])
        reason = "novel_query_terms" if utility > 0 else "rank_fill"
        if utility > 0:
            positive += 1
        else:
            position, gain, utility, novel = 0, Fraction(), Fraction(), set()
        pair = available.pop(position)
        identifier = pair["snippet"]["id"]
        proposal.append(pair)
        covered.update(terms[identifier])
        receipt["picks"].append({"id": identifier, "rank": ranks[identifier], "novel_terms": sorted(novel),
            "weighted_gain": fraction_json(gain), "utility": fraction_json(utility), "reason": reason})
    receipt.update(proposed_eligible_ids=[pair["snippet"]["id"] for pair in proposal],
        proposed_query_terms=sorted(covered))
    if len(proposal) != len(baseline_eligible):
        return fallback("candidate_pool_exhausted")
    if not positive:
        return fallback("no_novel_query_terms")
    if not baseline_terms <= covered:
        return fallback("query_terms_not_preserved")
    planned_origins = deepcopy(baseline_origins)
    for pair in proposal:
        identifier = pair["snippet"]["id"]
        if identifier not in dependencies:
            planned_origins[identifier] = ["retrieval", None]
    receipt["accepted"] = True
    return deepcopy(selected), deepcopy(proposal), planned_origins, receipt


def verify_planning(row, baseline, fixture):
    stages = row["stages"]
    require(stages["ranked"] == baseline["stages"]["ranked"], "Allocation changed frozen retrieval order/scores")
    selected, eligible, origins, receipt = planner(stages["ranked"], baseline["stages"]["selected"],
        baseline["stages"]["eligible"], baseline["stages"]["origins"], fixture["definition_keys"],
        mode=row["selector"], query=row["response"]["query"])
    require(row["allocation_receipt"] == receipt, "Independent deterministic allocation receipt differs")
    require(stages["selected"] == selected and stages["eligible"] == eligible
            and stages["origins"] == origins, "Allocation stage/order/score/provenance differs from source-only plan")
    return receipt


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
        if row["selector"] not in CONDITIONS[:2]:
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
    expected = {name: controls[(name, *win.case_key(row))]["reference"] for name in CONDITIONS[:2]}
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


def new_freeze(root, output, plan, start):
    names = ("frozen-plan.json", "baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json",
             "control-references.json", "historical-provenance.json")
    bound = {**plan["producer_sha256"], **plan["input_sha256"], **plan["historical_parent"]["artifacts"],
             **{(output / name).relative_to(root).as_posix(): file_sha(output / name) for name in names}}
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
        for control in CONDITIONS[:2]:
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
    return {"classification": "Observed-source CPU DEVELOPMENT complementary-definition allocation replay",
        "study": "allocation", "counts": case_counts, "metrics_by_study_dataset_original_condition": metrics, "all_changes": ledger,
        "qualification": qualification, "model_calls": 0, "retrieval_calls": 0, "network_calls": 0,
        "automatic_default_change": False, "stored_new_candidate_rows": sum(r["selector"] in CANDIDATES for r in rows),
        "referenced_control_rows": sum(r["selector"] in CONDITIONS[:2] for r in rows)}


def audit_development(root, corpus_root, output, counter):
    names = ("frozen-plan.json", "source-fixtures.json", "control-references.json", "historical-provenance.json",
             "baseline-gate.json", "prior-coverage-gate.json", "run-start.json", "control-gate.json", "replay.jsonl.gz", "summary.json")
    artifacts = {name: file_sha(output / name) for name in names}
    require(not (output / "failure.json").exists(), "Failed producer attempt cannot be certified complete")
    plan, actual = read(output / "frozen-plan.json"), read(output / "summary.json")
    start, gate = read(output / "run-start.json"), read(output / "control-gate.json")
    legacy_gate, coverage_gate = read(output / "baseline-gate.json"), read(output / "prior-coverage-gate.json")
    declared = {"legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
        "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
        "aspect_fill": {"selector": "aspect_fill_context", "postprocessor": "coalesce_context"},
        "aspect_balanced": {"selector": "aspect_balanced_context", "postprocessor": "coalesce_context"}}
    require(plan["version"] == 1 and plan["study"] == actual["study"] == "allocation"
            and plan["conditions"] == declared and plan["settings"] == SETTINGS, "Fixed four-condition allocation study/caps")
    require(plan["barriers"] == {"legacy_exact_rows": 365, "known_coverage_exact_rows": 337,
            "new_variant_outcomes_computed": 0, "run_parent_control_rows": 730}, "Prospective exact control barriers differ")
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
    chain, execution = new_freeze(root, output, plan, start)
    pointer = read(output / "source-fixtures.json")
    require(pointer["path"] == "results/window-development-2026-10-21/source-fixtures.json"
            and pointer["sha256"] == plan["historical_parent"]["artifacts"][pointer["path"]]
            == file_sha(root / pointer["path"]), "Immutable full source-fixture pointer differs")
    fixtures = []
    document = read(root / pointer["path"])
    require(set(document["fixtures"]) == set(plan["source_sha256"]), "Complete source-fixture membership differs")
    for signature, frozen in document["fixtures"].items():
        fixture = win.sources_from_fixture(corpus_root, frozen)
        fixture["inventory"] = {identifier: {name: item[name] for name in win.SNIPPET_FIELDS}
                                for identifier, item in fixture["inventory"].items()}
        fixture.update(definition_keys=frozen["definition_keys"], dataset=frozen["dataset"], source_sha256=frozen["source_sha256"])
        win.verify_inventory(fixture["inventory"], fixture["sources"], fixture["definition_keys"])
        require(plan["source_sha256"][signature] == fixture["source_sha256"], "Pinned complete source-file hash matrix differs")
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
    require(set(controls) == {(name, *win.case_key(case)) for case in cases for name in CONDITIONS[:2]}, "Exact immutable control case membership")
    reference_cases = [{**old.identity(case), "controls": {name: controls[(name, *win.case_key(case))]["reference"]
                                                        for name in CONDITIONS[:2]}} for case in cases]
    manifest = read(output / "control-references.json")
    require(manifest["path"] == "results/window-development-2026-10-21/replay.jsonl.gz"
            and manifest["record_hash"] == "SHA256 of UTF-8 sorted-key compact JSON, no newline"
            and manifest["cases"] == reference_cases, "Frozen exact immutable control-reference manifest differs")
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
        for name in CONDITIONS[:2]:
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
                                  for control in CONDITIONS[:2]}
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
    seen, receipt_counts = set(), {name: Counter() for name in CANDIDATES}
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
        win.verify_trace(row, case, counter, expected_ranked=case["ranked"])
        require(row["evidence_ranks"] == win.evidence_ranks(row["response"]["results"], case["groups"]), "Candidate actual source evidence ranks differ")
        for control in CONDITIONS[:2]:
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
    return {"status": "passed", "checks_passed": True, "mode": "development", "study": "allocation",
        "artifact_sha256": artifacts, "conditions": list(CONDITIONS), "exact_legacy_controls": 365,
        "exact_prior_coverage_controls": 337, "verified_outcome_rows": 730, "referenced_control_rows": 730,
        "distinct_dataset_query_units": 187, "independently_recomputed_summary": expected,
        "producer_freeze": chain, "producer_execution": execution, "historical_provenance": proof,
        "independently_recomputed_planner_counts": {key: dict(value) for key, value in receipt_counts.items()},
        "source_sha256": plan["source_sha256"],
        "limits": "Observed path/full-interval source evidence only; neither independent task trials, native latency, coding correctness, nor provider token usage. No fresh corpus read."}


class RefuseProduction(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in FORBIDDEN):
            raise RuntimeError("Independent allocation audit forbids producer/model imports: " + fullname)


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
    names = ("scripts/audit_allocation.py", "tests/test_allocation_audit.py", "scripts/audit_windows.py", "scripts/audit_selection.py")
    source = {name: (root / name).read_bytes() for name in names}
    hashes = {name: sha(raw) for name, raw in source.items()}
    for name, expected in hashes.items():
        require(git_sha(root, "HEAD", name) == expected, "Commit unchanged independent auditor/tests before canonical outcome auditing: " + name)
    execution = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    require(not any(any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN) for name in sys.modules),
            "Independent allocation audit needs a clean CPU process")
    finder = RefuseProduction()
    sys.meta_path.insert(0, finder)
    def denied(*unused, **unused_kw):
        raise RuntimeError("Network is forbidden in the independent allocation audit")
    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
                patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
                patch.object(socket.socket, "sendto", denied):
            counter = win.offline_encoding(Path(os.environ.get("TIKTOKEN_CACHE_DIR", root / ".cache/tokenizers")))
            receipt = audit_development(root, args.corpus_root.resolve(), output, counter)
        require(all((root / name).read_bytes() == raw for name, raw in source.items()), "Independent auditor changed during execution")
        receipt.update(auditor_sha256=hashes, auditor_execution_commit=execution,
            reviewed_at_utc=datetime.now(timezone.utc).isoformat(), calls={"model": 0, "retrieval": 0, "index": 0, "network": 0},
            production_or_producer_modules_imported=False)
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


if __name__ == "__main__":
    main()
