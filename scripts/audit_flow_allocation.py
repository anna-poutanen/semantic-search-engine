#!/usr/bin/env python3
"""Independently audit a shared-flow replay using frozen source-only helpers.

The unchanged prior independent auditor certifies historical data. New flow
planning is reconstructed below; no candidate or producer is imported.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("flow_independent_source_helpers", ROOT / "scripts/audit_source_allocation.py")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)
win, old = base.win, base.old
require, sha, read, canonical, file_sha, git_sha = base.require, base.sha, base.read, base.canonical, base.file_sha, base.git_sha
CONTROLS = base.CONTROLS
SOURCE_PARENT = "results/source-allocation-development-2026-10-27"
SOURCE_NEGATIVES = base.CANDIDATES
NEGATIVES = (*base.NEGATIVES, *SOURCE_NEGATIVES)
CANDIDATES = ("shared_flow",)
CONDITIONS = (*CONTROLS, *NEGATIVES, *CANDIDATES)
SETTINGS = dict(base.SETTINGS)
FROZEN_FILES = (*base.FROZEN_FILES, "source-negative-references.json", "source-negative-provenance.json")
DECLARED = {"legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
    "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
    "aspect_fill": {"selector": "aspect_fill_context", "postprocessor": "coalesce_context"},
    "aspect_balanced": {"selector": "aspect_balanced_context", "postprocessor": "coalesce_context"},
    "source_graph": {"selector": "source_graph_context", "postprocessor": "guard_source_context"},
    "source_graph_all": {"selector": "source_graph_all_context", "postprocessor": "guard_source_context"},
    "shared_flow": {"selector": "shared_flow_context", "postprocessor": "guard_source_context"}}


def planner(ranked, baseline_selected, baseline_eligible, origins, keys, edges, ambiguous, *, query, max_results=8):
    """Independent raw-rank-prefix admission; first blocked trial ends the pass."""
    selected, _, _, start = base.planner(ranked, baseline_selected, baseline_eligible, origins,
        keys, edges, ambiguous, mode="source_graph", query=query, max_results=max_results)
    guard_plan = deepcopy(start)
    guard_plan.update(producer_schema="shared_flow_allocation_v1", added_ids=[], evicted_ids=[],
        proposed_eligible_ids=list(start["baseline_eligible_ids"]), accepted=False)
    receipt = {"schema": "shared_flow_allocation_v1", "mode": "shared_flow", "base_plan": deepcopy(start),
        "base_protected_ids": list(start["protected_baseline_ids"]), "candidate_trials": [], "admitted_ids": [],
        "blocked_candidate_id": None, "flow_keys": [], "flow_witnesses": [], "termination": None,
        "accepted": False, "fallback_reason": None, "guard_plan": guard_plan}
    def finish(eligible, reason=None, termination=None):
        guard_plan["fallback_reason"] = receipt["fallback_reason"] = reason
        receipt["accepted"], receipt["termination"] = guard_plan["accepted"], termination
        return deepcopy(selected), deepcopy(eligible), deepcopy(origins), receipt
    if not start["accepted"]:
        return finish(baseline_eligible, start["fallback_reason"], "inactive_base_plan")
    definitions = {}
    for raw in keys.values():
        key = base.canonical_key(raw)
        if key is not None:
            definitions.setdefault(key[:2], set()).add(key)
    blocked = {tuple(label) for label in ambiguous}
    unique = {label: next(iter(values)) for label, values in definitions.items() if len(values) == 1 and label not in blocked}
    direct = sorted({(caller, callee) for caller, targets in edges.items() if caller in unique
        for callee in targets if callee in unique and caller != callee})
    by_id = {pair["snippet"]["id"]: pair for pair in selected}
    protected, committed_keys, admitted, committed_witnesses = set(start["protected_baseline_ids"]), set(), [], []
    for identifier in start["available_candidate_ids"]:
        item = by_id[identifier]["snippet"]
        label = (item["path"], item["symbol"])
        parents = sorted({caller for caller, callee in direct if callee == label})
        trial_keys, witnesses = set(), []
        for parent in parents:
            trial_keys.add(unique[parent])
            for caller, member in direct:
                if caller == parent:
                    trial_keys.add(unique[member])
                    witnesses.append({"candidate_id": identifier, "candidate_key": list(unique[label]),
                        "orchestrator_key": list(unique[parent]), "member_key": list(unique[member])})
        merged = committed_keys | trial_keys
        trial_protected = protected | {pair["snippet"]["id"] for pair in baseline_eligible
            if tuple(start["canonical_keys"].get(pair["snippet"]["id"], ())) in merged}
        spare = [pair["snippet"]["id"] for pair in baseline_eligible if pair["snippet"]["id"] not in trial_protected]
        fits = len(spare) >= len(admitted) + 1
        receipt["candidate_trials"].append({"candidate_id": identifier, "raw_rank": start["raw_ranks"][identifier],
            "candidate_key": start["canonical_keys"][identifier], "orchestrator_keys": [list(unique[parent]) for parent in parents],
            "flow_keys": [list(key) for key in sorted(trial_keys)], "flow_witnesses": witnesses,
            "trial_protected_baseline_ids": [pair["snippet"]["id"] for pair in baseline_eligible if pair["snippet"]["id"] in trial_protected],
            "exchangeable_baseline_ids": spare, "required_slots": len(admitted) + 1, "accepted": fits,
            "stop_reason": None if fits else "insufficient_exchangeable_slots"})
        if not fits:
            receipt["blocked_candidate_id"] = identifier
            break
        admitted.append(by_id[identifier])
        protected, committed_keys = trial_protected, merged
        committed_witnesses.extend(witnesses)
        if len(admitted) == 2:
            break
    if not admitted:
        return finish(baseline_eligible, "shared_flow_capacity_blocked", "capacity_blocked")
    spare = [pair for pair in baseline_eligible if pair["snippet"]["id"] not in protected]
    removed = {pair["snippet"]["id"] for pair in spare[-len(admitted):]}
    proposal = [pair for pair in baseline_eligible if pair["snippet"]["id"] in protected] + admitted
    proposal += [pair for pair in baseline_eligible if pair["snippet"]["id"] not in protected | removed]
    neighbors = {tuple(key) for key in start["neighbor_keys"]} | committed_keys
    windows = {}
    for pair in baseline_eligible + selected:
        item = pair["snippet"]
        if item["id"] in protected or tuple(start["canonical_keys"].get(item["id"], ())) in neighbors:
            windows.setdefault(item["id"], {field: item[field] for field in ("id", "path", "start_line", "end_line", "content_hash")})
    guard_plan.update(neighbor_keys=[list(key) for key in sorted(neighbors)],
        protected_baseline_ids=[pair["snippet"]["id"] for pair in baseline_eligible if pair["snippet"]["id"] in protected],
        protected_windows=list(windows.values()), added_ids=[pair["snippet"]["id"] for pair in admitted],
        evicted_ids=[pair["snippet"]["id"] for pair in baseline_eligible if pair["snippet"]["id"] in removed],
        proposed_eligible_ids=[pair["snippet"]["id"] for pair in proposal], accepted=True)
    receipt.update(admitted_ids=list(guard_plan["added_ids"]), flow_keys=[list(key) for key in sorted(committed_keys)],
        flow_witnesses=committed_witnesses)
    termination = ("capacity_blocked" if receipt["blocked_candidate_id"] is not None else
        "max_additions" if len(admitted) == 2 else "candidate_pool_exhausted")
    return finish(proposal, termination=termination)


def verify_planning(row, baseline, fixture, original_base):
    stages = row["stages"]
    require(stages["ranked"] == baseline["stages"]["ranked"], "Shared flow changed original retrieval order/scores")
    selected, eligible, origins, receipt = planner(stages["ranked"], baseline["stages"]["selected"],
        baseline["stages"]["eligible"], baseline["stages"]["origins"], fixture["definition_keys"],
        fixture["graph_edges"], stages["ambiguous_symbols"], query=row["response"]["query"])
    require(canonical(row["flow_allocation_receipt"]) == canonical(receipt), "Independent shared-flow prefix/rollback receipt differs")
    require(canonical(row["base_allocation_receipt"]) == canonical(receipt["base_plan"]) == canonical(original_base),
        "Actual pure base plan differs from independently reconstructed and immutable October27 base")
    require(type(row["base_planner_calls"]) is int and row["base_planner_calls"] == 1,
        "Exactly one actual pure base helper call required")
    require(canonical(row["selection_plan"]) == canonical(row["guard_plan"]) == canonical(receipt["guard_plan"]),
        "Actual shared-flow source-guard projection differs")
    require(stages["selected"] == selected and stages["eligible"] == eligible and stages["origins"] == origins,
        "Shared-flow stage/order/score/origins differ from independent plan")
    return receipt


def source_parent_proof(root, plan):
    parent = plan["source_parent"]
    folder = root / SOURCE_PARENT
    original, start, audit, summary = (read(folder / name) for name in
        ("frozen-plan.json", "run-start.json", "independent-audit.json", "summary.json"))
    require(original["study"] == audit["study"] == summary["study"] == "source_allocation"
        and audit["status"] == "passed" and audit["checks_passed"] is True
        and audit["verified_outcome_rows"] == audit["referenced_control_rows"] == audit["referenced_negative_rows"] == 730
        and audit["independently_recomputed_summary"] == summary
        and tuple(summary["qualification"]) == SOURCE_NEGATIVES
        and not any(value["qualified"] for value in summary["qualification"].values()), "Certified October27 negative parent required")
    for name, digest in audit["artifact_sha256"].items():
        require(parent["artifacts"].get(SOURCE_PARENT + "/" + name) == digest, "October27 audited artifact omitted")
    for name, digest in parent["artifacts"].items():
        require(file_sha(root / name) == git_sha(root, "HEAD", name) == digest, "October27 artifact changed: " + name)
    bound = {**original["producer_sha256"], **original["input_sha256"], **original["historical_parent"]["artifacts"],
        **original["negative_parent"]["artifacts"],
        **{SOURCE_PARENT + "/" + name: file_sha(folder / name) for name in base.FROZEN_FILES}}
    proof = parent["proof"]
    require(parent["original_plan_sha256"] == start["plan_sha256"] == file_sha(folder / "frozen-plan.json")
        and parent["original_execution_commit"] == start["execution_commit"] == audit["producer_execution"]
        and proof["original_execution_commit"] == start["execution_commit"]
        and proof["original_freeze_commit"] == audit["producer_freeze"]["commit"]
        and proof["original_auditor_commit"] == audit["auditor_execution_commit"]
        and proof["original_bound_sha256"] == bound and proof["original_auditor_sha256"] == audit["auditor_sha256"],
        "October27 original freeze/execution/source identity differs")
    for first, second in ((proof["original_freeze_commit"], proof["original_execution_commit"]),
        (proof["original_execution_commit"], "HEAD")):
        subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", first, second])
    stamp = int(subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%ct", proof["original_freeze_commit"]], text=True))
    require(stamp == proof["freeze_committed_at_unix"] and stamp <= datetime.fromisoformat(start["created_at_utc"]).timestamp()
        and proof["original_execution_is_current_ancestor"] is True and start["prospective_freeze"] == audit["producer_freeze"] == {
            "commit": proof["original_freeze_commit"], "committed_at_unix": stamp, "bound_blob_count": len(bound)},
        "October27 prospective ancestry/timestamp differs")
    for name, digest in bound.items():
        require(git_sha(root, proof["original_freeze_commit"], name) == git_sha(root, proof["original_execution_commit"], name) == digest,
            "October27 original committed producer/input blob differs: " + name)
    for name, digest in audit["auditor_sha256"].items():
        require(git_sha(root, proof["original_auditor_commit"], name) == digest, "October27 original auditor blob differs")
    require("no historical producer code executed" in proof["resolution"], "Data-only historical proof disclosure required")
    return proof


def new_freeze(root, output, plan, start):
    bound = {**plan["producer_sha256"], **plan["input_sha256"], **plan["historical_parent"]["artifacts"],
        **plan["negative_parent"]["artifacts"], **plan["source_parent"]["artifacts"],
        **{(output / name).relative_to(root).as_posix(): file_sha(output / name) for name in FROZEN_FILES}}
    for name, digest in bound.items():
        require(file_sha(root / name) == digest, "Prospective source/input changed: " + name)
    chain = old.committed_freeze(root, output / "frozen-plan.json", bound, start["created_at_utc"])
    execution = start["execution_commit"]
    for first, second in ((chain["commit"], execution), (execution, "HEAD")):
        subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", first, second])
    require(start["prospective_freeze"] == chain, "New prospective freeze receipt differs")
    for name, digest in bound.items():
        require(git_sha(root, execution, name) == digest, "New execution committed blob differs")
    return chain, execution


def read_source_negatives(path):
    with patch.object(base, "NEGATIVES", SOURCE_NEGATIVES):
        result = base.read_negatives(path)
    for _, row in base.stream_rows(path):
        if row["selector"] == "source_graph":
            result[("source_graph", *win.case_key(row))]["base_plan"] = row["source_allocation_receipt"]
    return result


@contextmanager
def report_profile():
    with patch.object(base, "CONDITIONS", CONDITIONS), patch.object(base, "NEGATIVES", NEGATIVES), patch.object(base, "CANDIDATES", CANDIDATES):
        yield


def summary(rows, counts):
    with report_profile():
        value = base.summary(rows, counts)
    value.update(classification="Observed-source CPU DEVELOPMENT shared-flow allocation replay", study="source_flow")
    return value


def audit_development(root, corpus_root, output, counter):
    artifacts = {name: file_sha(output / name) for name in (*FROZEN_FILES, "run-start.json", "control-gate.json", "replay.jsonl.gz", "summary.json")}
    require(not (output / "failure.json").exists(), "Failed producer cannot be certified")
    plan, start, gate, actual = (read(output / name) for name in ("frozen-plan.json", "run-start.json", "control-gate.json", "summary.json"))
    require(plan["version"] == 1 and plan["study"] == actual["study"] == "source_flow"
        and plan["conditions"] == DECLARED and plan["settings"] == SETTINGS and plan["condition_roles"] == {
            "controls": list(CONTROLS), "historical_negatives": list(NEGATIVES), "new_candidates": list(CANDIDATES)}, "Declared shared-flow study differs")
    require(plan["barriers"] == {"legacy_exact_rows": 365, "known_coverage_exact_rows": 337, "new_variant_outcomes_computed": 0,
        "run_parent_control_rows": 730, "referenced_negative_rows": 1460}, "Fixed shared-flow barriers differ")
    for name, digest in plan["artifacts"].items():
        require(artifacts[name] == digest, "Prospective artifact binding differs")
    chain, execution = new_freeze(root, output, plan, start)
    source_proof = source_parent_proof(root, plan)
    require(read(output / "source-negative-provenance.json") == source_proof, "October27 proof receipt differs")
    # Re-audit historical DATA with its immutable independent checker. No old
    # candidate producer or native search is run; historical records stay intact.
    graph_cache, extract_graph = [], base.verify_graph_records
    def captured_graph(saved, sources, inventory):
        edges = extract_graph(saved, sources, inventory)
        graph_cache.append((saved, sources, inventory, edges))
        return edges
    with patch.object(base, "verify_graph_records", captured_graph):
        parent_audit = base.audit_development(root, corpus_root, root / SOURCE_PARENT, counter)
    parent_plan, parent_gate = read(root / SOURCE_PARENT / "frozen-plan.json"), read(root / SOURCE_PARENT / "control-gate.json")
    require(plan["runtime"] == parent_plan["runtime"] and plan["input_sha256"] == parent_plan["input_sha256"]
        and plan["source_sha256"] == parent_plan["source_sha256"] and plan["historical_baseline_commit"] == parent_plan["historical_baseline_commit"]
        and plan["counts"] == parent_plan["counts"] and plan["historical_parent"] == parent_plan["historical_parent"]
        and plan["negative_parent"] == parent_plan["negative_parent"], "Immutable source/control matrix differs from certified parent")
    for name in ("source-fixtures.json", "control-references.json", "historical-provenance.json", "negative-references.json", "negative-provenance.json"):
        require(read(output / name) == read(root / SOURCE_PARENT / name), "Immutable prior reference/proof differs: " + name)
    digest = artifacts["frozen-plan.json"]
    require(start["plan_sha256"] == gate["plan_sha256"] == actual["plan_sha256"] == digest
        and actual["replay_sha256"] == artifacts["replay.jsonl.gz"] and gate["new_variant_rows_before_gate"] == 0
        and gate["referenced_negative_rows"] == actual["referenced_negative_rows"] == 1460, "Actual new execution/gate binding differs")
    for name in ("baseline-gate.json", "prior-coverage-gate.json"):
        current, original = read(output / name), read(root / SOURCE_PARENT / name)
        require(current == {**original, "plan_sha256": digest}, "Original control-only freeze gate differs")
    for key in ("exact_legacy_rows", "exact_prior_coverage_rows", "baseline_cases", "coverage_cases", "exact_parent_control_rows", "parent_control_cases"):
        require(gate[key] == parent_gate[key], "All730 exact original control receipts differ")
    require(plan["baseline_controls_sha256"] == parent_plan["baseline_controls_sha256"]
        and plan["prior_coverage_controls_sha256"] == parent_plan["prior_coverage_controls_sha256"], "Original control hash bindings differ")
    pointer = read(output / "graph-fixtures.json")
    graph_path = SOURCE_PARENT + "/graph-fixtures.json"
    require(pointer == {"schema": "source_flow_graph_pointer_v1", "path": graph_path,
        "sha256": plan["source_parent"]["artifacts"][graph_path]} and file_sha(root / graph_path) == pointer["sha256"], "Complete immutable graph pointer differs")
    graphs = read(root / graph_path)
    fixtures = []
    for signature, frozen in read(root / read(output / "source-fixtures.json")["path"])["fixtures"].items():
        fixture = win.sources_from_fixture(corpus_root, frozen)
        fixture["inventory"] = {identifier: {field: item[field] for field in win.SNIPPET_FIELDS} for identifier, item in fixture["inventory"].items()}
        fixture.update(definition_keys=frozen["definition_keys"], dataset=frozen["dataset"])
        matching_graphs = [edges for saved, sources, inventory, edges in graph_cache
            if saved == graphs["fixtures"][signature]["edges"] and sources == fixture["sources"] and inventory == fixture["inventory"]]
        require(len(matching_graphs) == 1, "New source/graph fixture differs from independently reconstructed parent graph")
        fixture["graph_edges"] = deepcopy(matching_graphs[0])
        fixtures.append(fixture)
    cases = win.original_cases(root, corpus_root)
    require(base.counts(cases) == plan["counts"] and len(cases) == 365, "Original365/187 case matrix differs")
    for case in cases:
        matching = [fixture for fixture in fixtures if fixture["dataset"] == case["dataset"] and fixture["sources"] == case["sources"]]
        require(len(matching) == 1, "Each case requires one exact source/graph fixture")
        case.update(matching[0])
    controls = base.read_controls(root / "results/window-development-2026-10-21/replay.jsonl.gz")
    source_negatives = read_source_negatives(root / SOURCE_PARENT / "replay.jsonl.gz")
    negatives = {**base.read_negatives(root / base.HISTORICAL_ALLOCATION / "replay.jsonl.gz"), **source_negatives}
    expected_refs = [{**old.identity(case), "negatives": {name: source_negatives[(name, *win.case_key(case))]["reference"] for name in SOURCE_NEGATIVES}} for case in cases]
    source_manifest = read(output / "source-negative-references.json")
    require(source_manifest["path"] == SOURCE_PARENT + "/replay.jsonl.gz" and source_manifest["cases"] == expected_refs
        and source_manifest["record_hash"] == "SHA256 of UTF-8 sorted-key compact JSON, no newline", "All730 October27 negative references differ")
    all_rows = []
    for case in cases:
        key = win.case_key(case)
        for name in (*CONTROLS, *NEGATIVES):
            saved = deepcopy((controls if name in CONTROLS else negatives)[(name, *key)])
            saved["comparisons"] = {control: win.comparison(controls[(control, *key)]["response"], saved["response"], case["groups"]) for control in CONTROLS}
            all_rows.append(saved)
    by_case, seen = {win.case_key(case): case for case in cases}, set()
    for _, row in base.stream_rows(output / "replay.jsonl.gz"):
        key = win.case_key(row)
        require(row["selector"] == "shared_flow" and key in by_case and key not in seen, "Unexpected/duplicate shared-flow row")
        seen.add(key)
        case, control = by_case[key], controls[("coverage_control", *key)]
        require(row["kind"] == case["kind"] and row["multi_file"] == case["multi_file"] and row["evidence_groups"] == case["groups"]
            and row["response"]["query"] == case["original"]["query"] and row["response"]["scope"] == case["original"]["scope"], "Candidate reporting identity differs")
        base.verify_control_references(row, controls)
        verify_planning(row, control, case, source_negatives[("source_graph", *key)]["base_plan"])
        base.verify_trace(row, case, counter)
        base.verify_guard(row, control)
        require(row["evidence_ranks"] == win.evidence_ranks(row["response"]["results"], case["groups"]), "Actual delivered evidence ranks differ")
        for name in CONTROLS:
            require(row["comparisons"][name] == win.comparison(controls[(name, *key)]["response"], row["response"], case["groups"]), "Individual evidence comparison differs")
        all_rows.append(row)
    require(seen == set(by_case), "Exact365 new shared-flow outcome matrix required")
    expected = summary(all_rows, base.counts(cases))
    expected.update(baseline_exact_rows=365, prior_coverage_exact_rows=337, exact_parent_control_rows=730,
        plan_sha256=digest, replay_sha256=artifacts["replay.jsonl.gz"])
    require(canonical(actual) == canonical(expected), "Independent full shared-flow metrics/quality gates differ")
    require(all(start[name] == actual[name] == 0 for name in ("model_calls", "retrieval_calls", "network_calls")), "CPU zero-call declarations differ")
    require(all(file_sha(output / name) == digest for name, digest in artifacts.items()), "Artifacts changed during auditing")
    return {"status": "passed", "checks_passed": True, "study": "source_flow", "mode": "development", "artifact_sha256": artifacts,
        "conditions": list(CONDITIONS), "exact_legacy_controls": 365, "exact_prior_coverage_controls": 337,
        "verified_outcome_rows": 365, "referenced_control_rows": 730, "referenced_negative_rows": 1460,
        "distinct_dataset_query_units": 187, "independently_recomputed_summary": expected,
        "producer_freeze": chain, "producer_execution": execution, "source_historical_provenance": source_proof,
        "historical_data_reaudit": {"study": "source_allocation", "verified_outcome_rows": parent_audit["verified_outcome_rows"],
            "referenced_control_rows": parent_audit["referenced_control_rows"], "referenced_negative_rows": parent_audit["referenced_negative_rows"],
            "artifact_sha256": parent_audit["artifact_sha256"]},
        "trust_boundary": "Frozen conservative graph extractor shared; flow admission, rollback, guard projection, exact packing/backfill and metrics independently reconstructed. Historical records re-audited as data; no candidate producer imported.",
        "limits": "Observed physical source evidence only; no native latency, coding correctness, billed token saving or fresh-corpus claim."}


def forbidden_module(name):
    return base.forbidden_module(name) or any(name == value or name.startswith(value + ".") for value in
        ("replay_flow_allocation", "benchmark_flow_allocation", "semantic_search.flow_allocation"))


class RefuseProduction(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if forbidden_module(fullname):
            raise RuntimeError("Independent shared-flow audit forbids candidate/producer imports: " + fullname)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root, folder = args.root.resolve(), args.results.resolve()
    target = args.output.resolve() if args.output else folder / "independent-audit.json"
    failure = target.with_name(target.stem + "-failure.json")
    require(not target.exists() and not failure.exists(), "Preserve prior audit attempts")
    names = ("scripts/audit_flow_allocation.py", "tests/test_flow_allocation_audit.py", "scripts/audit_source_allocation.py",
        "tests/test_source_allocation_audit.py", "scripts/audit_windows.py", "scripts/audit_selection.py", *base.GRAPH_SOURCES)
    raw = {name: (root / name).read_bytes() for name in names}
    hashes = {name: sha(value) for name, value in raw.items()}
    for name, digest in hashes.items():
        require(git_sha(root, "HEAD", name) == digest, "Commit unchanged independent auditor/tests before outcome auditing")
    execution = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    require(not any(forbidden_module(name) for name in sys.modules), "Independent audit requires clean CPU process")
    finder = RefuseProduction()
    sys.path.insert(0, str(root / "src"))
    sys.meta_path.insert(0, finder)
    def denied(*args, **kwargs):
        raise RuntimeError("Network forbidden in independent shared-flow audit")
    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
            patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), patch.object(socket.socket, "sendto", denied):
            counter = win.offline_encoding(Path(os.environ.get("TIKTOKEN_CACHE_DIR", root / ".cache/tokenizers")))
            receipt = audit_development(root, args.corpus_root.resolve(), folder, counter)
        require(all((root / name).read_bytes() == value for name, value in raw.items()), "Auditor/extractor changed during execution")
        receipt.update(auditor_sha256=hashes, auditor_execution_commit=execution, reviewed_at_utc=datetime.now(timezone.utc).isoformat(),
            calls={"model": 0, "retrieval": 0, "index": 0, "network": 0}, candidate_planner_or_producer_modules_imported=False,
            allowed_shared_source_graph_modules=sorted(name for name in base.ALLOWED_GRAPH_MODULES if name in sys.modules))
        with target.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(json.dumps({"checks_passed": True, "audit": str(target), "sha256": file_sha(target)}))
    except BaseException as error:
        with failure.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"status": "failed", "error_type": type(error).__name__, "error": str(error),
                "auditor_sha256": hashes, "auditor_execution_commit": execution, "resume_allowed": False}, indent=2) + "\n")
        raise
    finally:
        sys.meta_path.remove(finder)
        sys.path.remove(str(root / "src"))


if __name__ == "__main__":
    main()
