#!/usr/bin/env python3
"""Prospectively replay one shared-flow allocator on immutable observed inputs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("flow_source_helpers", ROOT / "scripts/replay_source_allocation.py")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)
previous, windows, prior = base.previous, base.windows, base.prior
require, sha, read, write_new = base.require, base.sha, base.read, base.write_new
canonical, identity, identity_tuple = base.canonical, base.identity, base.identity_tuple
file_sha, git_sha = base.file_sha, base.git_sha
SETTINGS, CONTROLS = dict(base.SETTINGS), base.CONTROLS
SOURCE_PARENT = "results/source-allocation-development-2026-10-27"
SOURCE_NEGATIVES = base.CANDIDATES
NEGATIVES = (*base.NEGATIVES, *SOURCE_NEGATIVES)
CANDIDATES = ("shared_flow",)
CONDITIONS = {**base.CONDITIONS,
    "shared_flow": {"selector": "shared_flow_context", "postprocessor": "guard_source_context"}}
FROZEN_FILES = (*base.FROZEN_FILES, "source-negative-references.json", "source-negative-provenance.json")
ATTEMPT_FILES = base.ATTEMPT_FILES


def own_bindings(root):
    names = ("scripts/replay_flow_allocation.py", "tests/test_flow_allocation_replay.py",
        "scripts/audit_flow_allocation.py", "tests/test_flow_allocation_audit.py",
        "docs/flow-allocation-development-protocol.md", "src/semantic_search/flow_allocation.py",
        "tests/test_flow_allocation.py", "tests/test_flow_allocation_backend.py")
    return {**base.own_bindings(root), **{name: file_sha(root / name) for name in names}}


def source_parent(root):
    folder = root / SOURCE_PARENT
    plan, start, audit, summary = (read(folder / name) for name in
        ("frozen-plan.json", "run-start.json", "independent-audit.json", "summary.json"))
    require(plan.get("study") == audit.get("study") == summary.get("study") == "source_allocation"
        and audit.get("status") == "passed" and audit.get("checks_passed") is True
        and audit.get("exact_legacy_controls") == 365 and audit.get("exact_prior_coverage_controls") == 337
        and audit.get("verified_outcome_rows") == audit.get("referenced_control_rows") == audit.get("referenced_negative_rows") == 730
        and audit.get("independently_recomputed_summary") == summary
        and tuple(summary["qualification"]) == SOURCE_NEGATIVES
        and not any(item["qualified"] for item in summary["qualification"].values()),
        "Successful immutable October27 source-allocation negative audit required")
    artifacts = {SOURCE_PARENT + "/" + name: digest for name, digest in audit["artifact_sha256"].items()}
    for name in ("findings.md", "independent-audit.json"):
        artifacts[SOURCE_PARENT + "/" + name] = file_sha(folder / name)
    for name, digest in artifacts.items():
        require(file_sha(root / name) == git_sha(root, "HEAD", name) == digest,
                "Immutable October27 artifact changed or uncommitted: " + name)
    require(start["plan_sha256"] == artifacts[SOURCE_PARENT + "/frozen-plan.json"]
        and start["execution_commit"] == audit["producer_execution"]
        and start["prospective_freeze"] == audit["producer_freeze"], "October27 execution/freeze/audit identity differs")
    bound = {**plan["producer_sha256"], **plan["input_sha256"], **plan["historical_parent"]["artifacts"],
        **plan["negative_parent"]["artifacts"],
        **{SOURCE_PARENT + "/" + name: artifacts[SOURCE_PARENT + "/" + name] for name in base.FROZEN_FILES}}
    proof = previous.original_blob_proof(root, freeze_commit=start["prospective_freeze"]["commit"],
        execution_commit=start["execution_commit"], started_at=start["created_at_utc"], bound=bound,
        auditor_commit=audit["auditor_execution_commit"], auditor_bound=audit["auditor_sha256"])
    require(proof["original_execution_is_current_ancestor"] and start["prospective_freeze"] == {
        "commit": proof["original_freeze_commit"], "committed_at_unix": proof["freeze_committed_at_unix"],
        "bound_blob_count": len(bound)}, "October27 source-allocation prospective ancestor receipt differs")
    return {"classification": "Immutable observed October27 source-allocation negatives; never rerun",
        "artifacts": artifacts, "original_plan_sha256": start["plan_sha256"],
        "original_execution_commit": start["execution_commit"], "proof": proof}


def source_negative_rows(root):
    # The old reader is a pure record hasher. Only its declared input arm/path
    # inventory is scoped; no old producer, plan or outcome is reinterpreted.
    with patch.object(base, "NEGATIVE_PARENT", SOURCE_PARENT), patch.object(base, "NEGATIVES", SOURCE_NEGATIVES):
        result = base.negative_rows(root)
    with gzip.open(root / SOURCE_PARENT / "replay.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["selector"] == "source_graph":
                result[(row["selector"], *identity_tuple(row))]["base_plan"] = row["source_allocation_receipt"]
    return result


def source_negative_manifest(cases, rows):
    with patch.object(base, "NEGATIVE_PARENT", SOURCE_PARENT), patch.object(base, "NEGATIVES", SOURCE_NEGATIVES):
        return base.negative_manifest(cases, rows)


def graph_pointer(parent):
    path = SOURCE_PARENT + "/graph-fixtures.json"
    return {"schema": "source_flow_graph_pointer_v1", "path": path, "sha256": parent["artifacts"][path]}


def evaluate_candidate(case, name, modules, *, reference_response=None):
    require(name == "shared_flow", "Only the declared new shared-flow arm may run")
    flow = importlib.import_module("semantic_search.flow_allocation")
    source = importlib.import_module("semantic_search.source_allocation")
    planner, base_planner, guard = flow.flow_allocation_plan, source.source_allocation_plan, source.guard_source_plan
    plans, base_plans, guards, references = [], [], [], []
    def capture_plan(*args, **kwargs):
        value = planner(*args, **kwargs)
        plans.append(deepcopy(value[3]))
        return value
    def capture_base(*args, **kwargs):
        value = base_planner(*args, **kwargs)
        base_plans.append(deepcopy(value[3]))
        return value
    def capture_guard(reference, proposal, plan):
        value = guard(reference, proposal, plan)
        guards.append((deepcopy(reference), deepcopy(proposal), deepcopy(plan), deepcopy(value)))
        return value
    # Reporting identity, labels and evidence intervals never enter these calls.
    with patch.object(flow, "flow_allocation_plan", capture_plan), patch.object(source, "source_allocation_plan", capture_base):
        selected, eligible, origins = flow.shared_flow_context(case["pairs"], query=case["original"]["query"],
            max_results=8, include_dependencies=True, references=case["graph"],
            definition_keys=case["definition_keys"], ambiguous_symbols=case["ambiguous_symbols"])
    require(len(plans) == len(base_plans) == 1, "Actual shared-flow and pure base planner must each run exactly once")
    outer, selection_plan = plans[0], eligible.source_plan
    require(outer["guard_plan"] == selection_plan and outer["base_plan"] == base_plans[0],
            "Actual eligible guard-plan or base-plan projection differs")
    physical, context = modules["corpus"].physical_lines, modules["context"]
    before = windows.pack(case["original"], [(item, score, *origins.get(item.id, ("retrieval", None)))
        for item, score in eligible], context, physical)
    candidates, ranked = windows.full_rows(case, selected, physical), windows.full_rows(case, case["pairs"], physical)
    def reference_factory():
        value = (deepcopy(reference_response) if reference_response is not None
            else windows.evaluate(case, "coverage_control", modules)["response"])
        references.append(deepcopy(value))
        return value
    with patch.object(source, "guard_source_plan", capture_guard):
        response = source.guard_source_context(deepcopy(before), deepcopy(candidates), selection_plan=deepcopy(selection_plan),
            reference_factory=reference_factory, ranked_candidates=deepcopy(ranked), definition_keys=case["definition_keys"],
            max_results=8, source_budget=12000, token_budget=4096)
    require(len(guards) == len(references) == 1, "Actual lazy reference and source guard must run exactly once")
    actual_reference, proposal, actual_plan, (chosen, receipt) = guards[0]
    require(actual_reference == references[0] and actual_plan == selection_plan and chosen == response,
            "Actual source guard operands/result differ from captured values")
    protected = source.protected_source_rows(actual_reference, actual_plan)
    preserved = base.protected_preserved(protected, proposal, physical)
    accepted = bool(actual_plan["accepted"] and preserved)
    expected = {"schema": "source_graph_guard_v1", "mode": "source_graph",
        "protected_ids": [row["id"] for row in protected], "protected_rows": [{field: row[field] for field in
            ("id", "path", "start_line", "end_line", "source_hash", "content_hash", "origin", "related_to",
             "source_origin", "buffer_version", "buffer_hash") if field in row} for row in protected],
        "source_preserved": preserved, "accepted": accepted,
        "fallback_reason": None if accepted else "inactive_plan" if not actual_plan["accepted"] else "protected_source_not_preserved"}
    require(receipt == expected and response == (proposal if accepted else actual_reference),
            "Actual source guard receipt or exact fallback differs")
    windows.preserved(before, proposal, physical)
    for value in (before, proposal, actual_reference):
        windows.verify_response(value, case, context, physical)
    text = windows.verify_response(response, case, context, physical)
    relevant = {item.id for item, _ in case["pairs"] + selected + list(eligible)}
    return {"stages": {"ranked": [{"snippet": asdict(item), "score": score} for item, score in case["pairs"]],
        "selected": [{"snippet": asdict(item), "score": score} for item, score in selected],
        "eligible": [{"snippet": asdict(item), "score": score} for item, score in eligible], "origins": origins,
        "definition_keys": {key: case["definition_keys"][key] for key in sorted(relevant) if key in case["definition_keys"]},
        "ambiguous_symbols": sorted(case["ambiguous_symbols"])}, "flow_allocation_receipt": outer,
        "selection_plan": selection_plan, "guard_plan": actual_plan, "planner_calls": 1,
        "base_allocation_receipt": base_plans[0], "base_planner_calls": 1,
        "pre_postprocess_response": before, "backfill_candidates": candidates, "ranked_candidates": ranked,
        "reference_response": actual_reference, "proposal_response": proposal, "source_guard_receipt": receipt,
        "guard_calls": 1, "reference_calls": 1, "response": response, "compact_payload": text,
        "compact_sha256": sha(text.encode()), "actual_compact_tokens": response["token_usage"]["tokens"]}


@contextmanager
def report_profile():
    with patch.object(windows, "CONDITIONS", CONDITIONS), patch.object(windows, "CANDIDATES", CANDIDATES):
        yield


def summarize(outcomes, controls, negatives, cases):
    rows = list(outcomes)
    for case in cases:
        refs = {name: controls[(name, *identity_tuple(case))]["reference"] for name in CONTROLS}
        responses = {(name, *identity_tuple(case)): controls[(name, *identity_tuple(case))]["response"] for name in CONTROLS}
        for name in (*CONTROLS, *NEGATIVES):
            saved = (controls if name in CONTROLS else negatives)[(name, *identity_tuple(case))]
            rows.append(previous.reported_row(case, name, {"response": saved["response"]}, responses, refs))
    with report_profile():
        result = windows.summarize(rows, windows.counts(cases))
    result.update(classification="Observed-source CPU DEVELOPMENT shared-flow allocation replay", study="source_flow",
        stored_new_candidate_rows=len(outcomes), referenced_control_rows=len(controls), referenced_negative_rows=len(negatives),
        historical_negative_conditions=list(NEGATIVES))
    return result


def freeze(root, corpus_root, output):
    require(not output.exists(), "Use a new freeze directory")
    runtime, rank_bytes = prior.runtime(root)
    parent, negative_parent, source = previous.historical_parent(root), base.historical_negatives(root), source_parent(root)
    controls, negatives, source_negatives = previous.control_rows(root), base.negative_rows(root), source_negative_rows(root)
    with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
        cases, bindings, fixtures, old_coverage = windows.load_inputs(root, corpus_root, modules)
        baseline, coverage = windows.control_barrier(cases, modules), windows.coverage_barrier(cases, old_coverage, modules)
        require(json.loads(canonical(windows.fixture_snapshot(fixtures))) == read(root / base.PARENT / "source-fixtures.json"),
            "Complete immutable source fixture changed")
        require(base.graph_snapshot(root, fixtures) == read(root / SOURCE_PARENT / "graph-fixtures.json"),
            "Frozen October27 complete source graph differs")
        documents = {"source-fixtures.json": previous.fixture_pointer(parent),
            "control-references.json": previous.reference_manifest(cases, controls), "historical-provenance.json": parent["proof"],
            "negative-references.json": base.negative_manifest(cases, negatives), "negative-provenance.json": negative_parent["proof"],
            "source-negative-references.json": source_negative_manifest(cases, source_negatives),
            "source-negative-provenance.json": source["proof"], "graph-fixtures.json": graph_pointer(source)}
    output.mkdir(parents=True)
    for name, value in documents.items():
        write_new(output / name, value)
    plan = {"version": 1, "study": "source_flow", "classification": "Prospective CPU DEVELOPMENT shared-flow allocation replay",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(), "historical_baseline_commit": windows.BASE_COMMIT,
        "conditions": CONDITIONS, "condition_roles": {"controls": list(CONTROLS), "historical_negatives": list(NEGATIVES),
            "new_candidates": list(CANDIDATES)}, "settings": SETTINGS, "counts": windows.counts(cases), "runtime": runtime,
        "producer_sha256": own_bindings(root), "input_sha256": bindings,
        "historical_parent": parent, "negative_parent": negative_parent, "source_parent": source,
        "artifacts": {name: file_sha(output / name) for name in documents},
        "source_sha256": {dataset + "/" + signature: value["source_sha256"] for (dataset, signature), value in fixtures.items()},
        "baseline_controls_sha256": sha(canonical(baseline)), "prior_coverage_controls_sha256": sha(canonical(coverage)),
        "barriers": {"legacy_exact_rows": 365, "known_coverage_exact_rows": 337, "new_variant_outcomes_computed": 0,
            "run_parent_control_rows": 730, "referenced_negative_rows": 1460},
        "selection": {"legacy": "Original display-label selection; all365 full responses are reproduced.",
            "coverage_control": "Original canonical definition and source-preserving coverage selection; all365 parent controls are reproduced.",
            **{name: "Immutable historical negative record; never rerun as a producer outcome." for name in NEGATIVES},
            "shared_flow": "Same one-hop candidates and max2 additions; shared-parent/sibling protected raw-rank prefix stops at first capacity-blocked candidate, preserving the accepted prefix and rolling back the blocked trial."},
        "qualification": "Zero individual full-group, complete-row or single-file first-hit loss against BOTH controls and >=1 additional distinct complete dataset/query unit beyond coverage_control; no automatic defaults.",
        "execution": "Commit unchanged bindings before run; reproduce365/337 barriers and730 immutable controls before365 new traces;1460 historical negatives are references only. Exclusive streamed partial ledgers, no overwrite/resume.",
        "labels": "Planner and source guard see no query IDs, gold, kinds or evidence intervals.",
        "limits": ["365 observed cases share187 query units; reused conditions are not independent trials.",
            "No TanStack, model, retrieval, index, GPU, numerical runtime or network execution.",
            "Historical source coverage is not native latency, coding correctness, generalization or billed tokens."]}
    write_new(output / "frozen-plan.json", plan)
    digest = file_sha(output / "frozen-plan.json")
    for name, rows in (("baseline-gate.json", baseline), ("prior-coverage-gate.json", coverage)):
        write_new(output / name, {"exact_rows": len(rows), "cases": rows, "plan_sha256": digest, "new_variant_outcomes_computed": 0})
    return {**plan["counts"], "exact_legacy_controls": len(baseline), "exact_prior_coverage_controls": len(coverage),
        "referenced_negative_rows": 1460, "new_variant_outcomes_computed": 0}


def bound_files(root, output, plan):
    return {**plan["producer_sha256"], **plan["input_sha256"], **plan["historical_parent"]["artifacts"],
        **plan["negative_parent"]["artifacts"], **plan["source_parent"]["artifacts"],
        **{(output / name).relative_to(root).as_posix(): file_sha(output / name) for name in FROZEN_FILES}}


def validate_run(root, output, plan, started_at):
    require(plan["study"] == "source_flow" and plan["conditions"] == CONDITIONS and plan["settings"] == SETTINGS,
        "Undeclared shared-flow configuration")
    require(plan["producer_sha256"] == own_bindings(root), "Prospective source/tests changed after freeze")
    bound = bound_files(root, output, plan)
    for name, digest in bound.items():
        require(file_sha(root / name) == git_sha(root, "HEAD", name) == digest, "Commit unchanged frozen bytes before run: " + name)
    for name, digest in plan["artifacts"].items():
        require(file_sha(output / name) == digest, "Frozen shared-flow artifact changed: " + name)
    require(not any((output / name).exists() for name in ATTEMPT_FILES), "Never overwrite or resume an attempted run")
    path = (output / "frozen-plan.json").relative_to(root).as_posix()
    commit = subprocess.check_output(["git", "-C", str(root), "log", "-1", "--format=%H", "--", path], text=True).strip()
    require(bool(commit), "Prospective shared-flow plan must be committed")
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"])
    stamp = int(subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%ct", commit], text=True))
    require(stamp <= datetime.fromisoformat(started_at).timestamp(), "Prospective freeze follows run start")
    for name, digest in bound.items():
        require(git_sha(root, commit, name) == digest, "Prospective freeze blob differs: " + name)
    return {"commit": commit, "committed_at_unix": stamp, "bound_blob_count": len(bound)}


def run(root, corpus_root, output):
    plan, started_at = read(output / "frozen-plan.json"), datetime.now(timezone.utc).isoformat()
    chain, digest = validate_run(root, output, plan, started_at), file_sha(output / "frozen-plan.json")
    write_new(output / "run-start.json", {"created_at_utc": started_at, "pid": os.getpid(),
        "execution_commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        "prospective_freeze": chain, "plan_sha256": digest, "model_calls": 0, "retrieval_calls": 0, "network_calls": 0, "resume_allowed": False})
    state = {"stage": "runtime", "attempted_new_variant_rows": 0, "new_variant_rows": 0, "exact_parent_control_rows": 0}
    try:
        runtime, rank_bytes = prior.runtime(root)
        require(runtime == plan["runtime"] and previous.historical_parent(root) == plan["historical_parent"]
            and base.historical_negatives(root) == plan["negative_parent"] and source_parent(root) == plan["source_parent"],
            "Frozen runtime or immutable parent provenance changed")
        controls, old_negatives, new_negatives = previous.control_rows(root), base.negative_rows(root), source_negative_rows(root)
        negatives = {**old_negatives, **new_negatives}
        with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
            cases, bindings, fixtures, old_coverage = windows.load_inputs(root, corpus_root, modules)
            require(bindings == plan["input_sha256"] and windows.counts(cases) == plan["counts"]
                and {dataset + "/" + signature: value["source_sha256"] for (dataset, signature), value in fixtures.items()} == plan["source_sha256"],
                "Frozen input/source matrix changed")
            require(json.loads(canonical(windows.fixture_snapshot(fixtures))) == read(root / base.PARENT / "source-fixtures.json")
                and base.graph_snapshot(root, fixtures) == read(root / SOURCE_PARENT / "graph-fixtures.json")
                and read(output / "graph-fixtures.json") == graph_pointer(plan["source_parent"])
                and read(output / "source-fixtures.json") == previous.fixture_pointer(plan["historical_parent"])
                and read(output / "control-references.json") == previous.reference_manifest(cases, controls)
                and read(output / "negative-references.json") == base.negative_manifest(cases, old_negatives)
                and read(output / "source-negative-references.json") == source_negative_manifest(cases, new_negatives),
                "Complete source/graph or immutable record manifests changed")
            state["stage"] = "all_historical_controls"
            baseline, coverage = windows.control_barrier(cases, modules), windows.coverage_barrier(cases, old_coverage, modules)
            for name, rows, key in (("baseline-gate.json", baseline, "baseline_controls_sha256"),
                ("prior-coverage-gate.json", coverage, "prior_coverage_controls_sha256")):
                saved = read(output / name)
                require(saved["cases"] == rows and saved["plan_sha256"] == digest and sha(canonical(rows)) == plan[key], "Original365/337 barriers changed")
            responses, receipts = previous.verify_parent_controls(cases, controls, modules)
            state["exact_parent_control_rows"] = len(receipts)
            write_new(output / "control-gate.json", {"exact_legacy_rows": len(baseline), "exact_prior_coverage_rows": len(coverage),
                "baseline_cases": baseline, "coverage_cases": coverage, "exact_parent_control_rows": len(receipts),
                "parent_control_cases": receipts, "referenced_negative_rows": len(negatives), "new_variant_rows_before_gate": 0, "plan_sha256": digest})
            state["stage"], outcomes = "new_shared_flow_arm", []
            with (output / "replay.jsonl.gz").open("xb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
                    for case in cases:
                        state["current_case"], state["current_condition"] = identity(case), "shared_flow"
                        state["attempted_new_variant_rows"] += 1
                        result = evaluate_candidate(case, "shared_flow", modules, reference_response=responses[("coverage_control", *identity_tuple(case))])
                        require(result["base_allocation_receipt"] == new_negatives[("source_graph", *identity_tuple(case))]["base_plan"],
                            "Actual pure base plan differs from its immutable October27 receipt")
                        refs = {name: controls[(name, *identity_tuple(case))]["reference"] for name in CONTROLS}
                        row = previous.reported_row(case, "shared_flow", result, responses, refs)
                        zipped.write((json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode())
                        zipped.flush()
                        raw.flush()
                        outcomes.append(row)
                        state["new_variant_rows"] += 1
            state["stage"] = "publish_all_outcomes"
            summary = summarize(outcomes, controls, negatives, cases)
            summary.update(baseline_exact_rows=len(baseline), prior_coverage_exact_rows=len(coverage), exact_parent_control_rows=len(receipts),
                plan_sha256=digest, replay_sha256=file_sha(output / "replay.jsonl.gz"))
            write_new(output / "summary.json", summary)
            with report_profile():
                findings = windows.findings(summary).replace("# Complementary-window development replay", "# Shared-flow allocation development replay", 1)
            with (output / "findings.md").open("x", encoding="utf-8") as stream:
                stream.write(findings + "\nOnly365 new shared-flow traces are stored. The730 controls and1460 historical negatives reference immutable original records; historical negative arms were never rerun.\n")
            return {"study": "source_flow", "qualification": summary["qualification"], "stored_new_candidate_rows": len(outcomes)}
    except BaseException as error:
        write_new(output / "failure.json", {"failed": True, "error_type": type(error).__name__, "error": str(error), "progress": state,
            "resume_allowed": False, "model_calls": 0, "retrieval_calls": 0, "network_calls": 0})
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
    print(json.dumps((freeze if args.freeze else run)(args.root.resolve(), args.corpus_root.resolve(), args.output.resolve())))


if __name__ == "__main__":
    main()
