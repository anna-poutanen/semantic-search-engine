#!/usr/bin/env python3
"""Freeze and replay source-aware allocation on immutable observed CPU inputs.

Only the two new candidate arms are written. Historical controls and failed
allocation arms remain content-addressed records in their original ledgers.
"""
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
SPEC = importlib.util.spec_from_file_location("source_allocation_helpers", ROOT / "scripts/replay_allocation.py")
previous = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(previous)
windows, prior = previous.windows, previous.prior
require, sha, read, write_new = previous.require, previous.sha, previous.read, previous.write_new
canonical, identity, identity_tuple = previous.canonical, previous.identity, previous.identity_tuple
file_sha, git_sha = previous.file_sha, previous.git_sha
SETTINGS = dict(previous.SETTINGS)
PARENT = previous.PARENT
NEGATIVE_PARENT = "results/allocation-development-2026-10-26"
CONTROLS = previous.CONTROLS
NEGATIVES = previous.CANDIDATES
CANDIDATES = ("source_graph", "source_graph_all")
CONDITIONS = {
    **previous.CONDITIONS,
    "source_graph": {"selector": "source_graph_context", "postprocessor": "guard_source_context"},
    "source_graph_all": {"selector": "source_graph_all_context", "postprocessor": "guard_source_context"},
}
FROZEN_FILES = (*previous.FROZEN_FILES, "negative-references.json", "negative-provenance.json", "graph-fixtures.json")
ATTEMPT_FILES = previous.ATTEMPT_FILES


@contextmanager
def profile():
    """Scope only report configuration; do not modify old scripts or records."""
    with patch.object(windows, "CONDITIONS", CONDITIONS), patch.object(windows, "CANDIDATES", CANDIDATES):
        yield


def own_bindings(root):
    names = ("scripts/replay_source_allocation.py", "tests/test_source_allocation_replay.py",
             "docs/source-allocation-development-protocol.md", "src/semantic_search/source_allocation.py",
             "tests/test_source_allocation.py", "tests/test_source_allocation_backend.py",
             "scripts/audit_source_allocation.py", "tests/test_source_allocation_audit.py")
    return {**previous.own_bindings(root), **{name: file_sha(root / name) for name in names}}


def historical_negatives(root):
    """Bind the last failed allocation study, including its real commit chain."""
    folder = root / NEGATIVE_PARENT
    plan, started, audit, summary = (read(folder / name) for name in
                                    ("frozen-plan.json", "run-start.json", "independent-audit.json", "summary.json"))
    require(plan.get("study") == audit.get("study") == summary.get("study") == "allocation"
            and audit.get("status") == "passed" and audit.get("checks_passed") is True
            and audit.get("exact_legacy_controls") == 365 and audit.get("exact_prior_coverage_controls") == 337
            and audit.get("verified_outcome_rows") == 730 and audit.get("referenced_control_rows") == 730
            and audit.get("independently_recomputed_summary") == summary,
            "Independently audited previous allocation negatives required")
    require(tuple(summary["qualification"]) == NEGATIVES
            and not any(value["qualified"] for value in summary["qualification"].values()),
            "Expected two failed immutable allocation arms")
    paths = {NEGATIVE_PARENT + "/" + name: digest for name, digest in audit["artifact_sha256"].items()}
    for name in ("independent-audit.json", "findings.md"):
        paths[NEGATIVE_PARENT + "/" + name] = file_sha(folder / name)
    for name, expected in paths.items():
        require(file_sha(root / name) == expected and git_sha(root, "HEAD", name) == expected,
                "Previous negative artifact changed or uncommitted: " + name)
    require(started["plan_sha256"] == paths[NEGATIVE_PARENT + "/frozen-plan.json"]
            and audit["producer_execution"] == started["execution_commit"]
            and audit["producer_freeze"] == started["prospective_freeze"],
            "Previous allocation plan/execution identities disagree")
    frozen = {NEGATIVE_PARENT + "/" + name: paths[NEGATIVE_PARENT + "/" + name]
              for name in previous.FROZEN_FILES}
    bound = {**plan["producer_sha256"], **plan["input_sha256"],
             **plan["historical_parent"]["artifacts"], **frozen}
    proof = previous.original_blob_proof(root,
        freeze_commit=started["prospective_freeze"]["commit"], execution_commit=started["execution_commit"],
        started_at=started["created_at_utc"], bound=bound,
        auditor_commit=audit["auditor_execution_commit"], auditor_bound=audit["auditor_sha256"])
    require(proof["original_execution_is_current_ancestor"] is True,
            "The immediately preceding allocation execution must remain a current ancestor")
    require(started["prospective_freeze"] == {"commit": proof["original_freeze_commit"],
            "committed_at_unix": proof["freeze_committed_at_unix"], "bound_blob_count": len(bound)},
            "Previous allocation prospective chain differs")
    return {"classification": "Immutable failed observed allocation arms; original executions retained and never rerun",
            "artifacts": paths, "original_plan_sha256": started["plan_sha256"],
            "original_execution_commit": started["execution_commit"], "proof": proof}


def negative_rows(root):
    """Keep only reporting envelopes and exact immutable record references."""
    result, total = {}, 0
    with gzip.open(root / NEGATIVE_PARENT / "replay.jsonl.gz", "rt", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            row = json.loads(line)
            total += 1
            require(row["selector"] in NEGATIVES, "Unexpected previous allocation condition")
            key = (row["selector"], *identity_tuple(row))
            require(key not in result, "Duplicate immutable negative identity")
            reference = {"line_number": number, "record_sha256": sha(canonical(row)),
                         "response_sha256": sha(canonical(row["response"])), "compact_sha256": row["compact_sha256"]}
            require(sha(row["compact_payload"].encode()) == reference["compact_sha256"],
                    "Previous negative compact hash differs")
            result[key] = {name: deepcopy(row[name]) for name in
                           ("response", "compact_payload", "kind", "multi_file", "evidence_groups", "evidence_ranks")}
            result[key]["reference"] = reference
    require(total == len(result) == 730, "All730 original failed allocation records required")
    return result


def negative_manifest(cases, negatives):
    require(set(negatives) == {(name, *identity_tuple(case)) for case in cases for name in NEGATIVES},
            "Historical negative arms must cover exactly the declared365 cases")
    return {"classification": "References to immutable failed allocation arms; no copied outcomes or reruns",
            "path": NEGATIVE_PARENT + "/replay.jsonl.gz",
            "record_hash": "SHA256 of UTF-8 sorted-key compact JSON, no newline",
            "cases": [{**identity(case), "negatives": {name: negatives[(name, *identity_tuple(case))]["reference"]
                                                      for name in NEGATIVES}} for case in cases]}


def graph_snapshot(root, fixtures):
    """Bind complete existing graph output before evaluating either new rule."""
    modules = ("source", "languages", "javascript", "relations", "javascript_relations")
    return {"schema": "source_graph_fixtures_v1",
            "extractor_sha256": {"src/semantic_search/" + name + ".py": file_sha(root / "src/semantic_search" / (name + ".py"))
                                 for name in modules},
            "fixtures": {dataset + "/" + signature: {"dataset": dataset, "source_sha256": value["source_sha256"],
                "edges": [{"caller": list(caller), "targets": [list(target) for target in targets]}
                          for caller, targets in sorted(value["graph"].edges.items())]}
                         for (dataset, signature), value in sorted(fixtures.items())}}


def protected_preserved(protected, proposal, physical_lines):
    """Validate literal guard operands, including dependency identity/owner."""
    def literal(row):
        text, start, end = row.get("source"), row.get("start_line"), row.get("end_line")
        return (isinstance(text, str) and bool(text) and isinstance(row.get("path"), str)
                and type(start) is int and type(end) is int and start > 0
                and end == start + len(physical_lines(text)) - 1 and row.get("source_hash") == sha(text.encode()))
    delivered = {row.get("id") for row in proposal["results"]}
    for old in protected:
        if not literal(old) or (old.get("origin") == "dependency" and old.get("related_to") not in delivered):
            return False
        candidates = [row for row in proposal["results"] if literal(row) and row.get("origin") == old.get("origin")
            and ("related_to" in row) == ("related_to" in old) and row.get("related_to") == old.get("related_to")
            and (row.get("id") != old.get("id") or (("content_hash" in row) == ("content_hash" in old)
                and row.get("content_hash") == old.get("content_hash")))
            and (old.get("origin") != "dependency" or row.get("id") == old.get("id"))]
        if not windows.source_preserved({"results": [old]}, {"results": candidates}, physical_lines):
            return False
    return True


def evaluate_candidate(case, name, modules, *, reference_response=None):
    """Execute one actual plan/pack/guard without exposing reporting labels."""
    require(name in CANDIDATES, "Only new source-aware candidates may be evaluated")
    source = importlib.import_module("semantic_search.source_allocation")
    planner, guard = source.source_allocation_plan, source.guard_source_plan
    plans, guards, references = [], [], []

    def capture_plan(*args, **kwargs):
        value = planner(*args, **kwargs)
        plans.append(deepcopy(value[3]))
        return value

    def capture_guard(reference, proposal, actual_plan):
        chosen, receipt = guard(reference, proposal, actual_plan)
        guards.append({"reference_response": deepcopy(reference), "proposal_response": deepcopy(proposal),
                       "selection_plan": deepcopy(actual_plan), "chosen_response": deepcopy(chosen),
                       "receipt": deepcopy(receipt)})
        return chosen, receipt

    # Query IDs, kinds, targets and evidence intervals do not enter these calls.
    with patch.object(source, "source_allocation_plan", capture_plan):
        selected, eligible, origins = getattr(source, CONDITIONS[name]["selector"])(case["pairs"],
            query=case["original"]["query"], max_results=8, include_dependencies=True,
            references=case["graph"], definition_keys=case["definition_keys"],
            ambiguous_symbols=case["ambiguous_symbols"])
    require(len(plans) == 1, "Every new candidate must execute the actual source planner exactly once")
    selection_plan = eligible.source_plan
    require(selection_plan == plans[0], "Actual planned-pairs receipt differs from captured planner")
    physical_lines, context = modules["corpus"].physical_lines, modules["context"]
    stream = [(item, score, *origins.get(item.id, ("retrieval", None))) for item, score in eligible]
    before = windows.pack(case["original"], stream, context, physical_lines)
    candidates = windows.full_rows(case, selected, physical_lines)
    ranked = windows.full_rows(case, case["pairs"], physical_lines)

    def reference_factory():
        value = (deepcopy(reference_response) if reference_response is not None
                 else windows.evaluate(case, "coverage_control", modules)["response"])
        references.append(deepcopy(value))
        return value

    with patch.object(source, "guard_source_plan", capture_guard):
        response = source.guard_source_context(deepcopy(before), deepcopy(candidates),
            selection_plan=deepcopy(selection_plan), reference_factory=reference_factory,
            ranked_candidates=deepcopy(ranked), definition_keys=case["definition_keys"],
            max_results=8, source_budget=12000, token_budget=4096)
    require(len(references) == len(guards) == 1,
            "Each candidate must construct one actual reference and execute one postpack guard")
    actual_guard = guards[0]
    require(actual_guard["reference_response"] == references[0]
            and actual_guard["selection_plan"] == selection_plan and actual_guard["chosen_response"] == response,
            "Guard did not receive and return its actual reference/plan/response")
    protected = source.protected_source_rows(references[0], selection_plan)
    proposal, guard_receipt = actual_guard["proposal_response"], actual_guard["receipt"]
    preserved = protected_preserved(protected, proposal, physical_lines)
    accepted = bool(selection_plan["accepted"] and preserved)
    expected_receipt = {"schema": "source_graph_guard_v1", "mode": selection_plan["mode"],
        "protected_ids": [row["id"] for row in protected],
        "protected_rows": [{key: row[key] for key in
                            ("id", "path", "start_line", "end_line", "source_hash", "content_hash", "origin", "related_to",
                             "source_origin", "buffer_version", "buffer_hash") if key in row}
                           for row in protected],
        "source_preserved": preserved, "accepted": accepted,
        "fallback_reason": None if accepted else "inactive_plan" if not selection_plan["accepted"]
                           else "protected_source_not_preserved"}
    require(guard_receipt == expected_receipt, "Actual protected-source guard receipt differs")
    require(response == (proposal if accepted else references[0]), "Postpack source guard must return exact proposal or reference")
    require(not accepted or protected_preserved(protected, response, physical_lines),
            "Accepted final response lost protected actual source")
    windows.preserved(before, proposal, physical_lines)
    for value in (before, proposal, references[0]):
        windows.verify_response(value, case, context, physical_lines)
    text = windows.verify_response(response, case, context, physical_lines)
    relevant = {item.id for item, _ in case["pairs"] + selected + list(eligible)}
    return {"stages": {"ranked": [{"snippet": asdict(item), "score": score} for item, score in case["pairs"]],
                       "selected": [{"snippet": asdict(item), "score": score} for item, score in selected],
                       "eligible": [{"snippet": asdict(item), "score": score} for item, score in eligible],
                       "origins": origins,
                       "definition_keys": {key: case["definition_keys"][key] for key in sorted(relevant)
                                           if key in case["definition_keys"]},
                       "ambiguous_symbols": sorted(case["ambiguous_symbols"])},
            "selection_plan": selection_plan, "source_allocation_receipt": plans[0],
            "guard_plan": actual_guard["selection_plan"], "planner_calls": len(plans),
            "pre_postprocess_response": before, "backfill_candidates": candidates, "ranked_candidates": ranked,
            "reference_response": references[0], "proposal_response": proposal,
            "source_guard_receipt": guard_receipt, "guard_calls": 1, "reference_calls": 1,
            "response": response, "compact_payload": text, "compact_sha256": sha(text.encode()),
            "actual_compact_tokens": response["token_usage"]["tokens"]}


def reported_row(case, name, result, control_responses, references):
    return previous.reported_row(case, name, result, control_responses, references)


def summarize(outcomes, controls, negatives, cases):
    rows = list(outcomes)
    for case in cases:
        refs = {name: controls[(name, *identity_tuple(case))]["reference"] for name in CONTROLS}
        responses = {(name, *identity_tuple(case)): controls[(name, *identity_tuple(case))]["response"] for name in CONTROLS}
        for name in (*CONTROLS, *NEGATIVES):
            saved = (controls if name in CONTROLS else negatives)[(name, *identity_tuple(case))]
            rows.append(reported_row(case, name, {"response": saved["response"]}, responses, refs))
    with profile():
        result = windows.summarize(rows, windows.counts(cases))
    result.update(classification="Observed-source CPU DEVELOPMENT source-aware complementary-definition allocation replay",
                  study="source_allocation", stored_new_candidate_rows=len(outcomes),
                  referenced_control_rows=len(controls), referenced_negative_rows=len(negatives),
                  historical_negative_conditions=list(NEGATIVES))
    return result


def freeze(root, corpus_root, output):
    require(not output.exists(), "Use a new freeze directory")
    runtime, rank_bytes = prior.runtime(root)
    parent, negative_parent = previous.historical_parent(root), historical_negatives(root)
    controls, negatives = previous.control_rows(root), negative_rows(root)
    with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
        cases, bindings, fixtures, old_coverage = windows.load_inputs(root, corpus_root, modules)
        baseline = windows.control_barrier(cases, modules)
        coverage = windows.coverage_barrier(cases, old_coverage, modules)
        references = previous.reference_manifest(cases, controls)
        negative_references = negative_manifest(cases, negatives)
        require(json.loads(canonical(windows.fixture_snapshot(fixtures))) == read(root / PARENT / "source-fixtures.json"),
                "Complete historical source/definition fixture changed")
        graphs = graph_snapshot(root, fixtures)
    output.mkdir(parents=True)
    artifacts = {"source-fixtures.json": previous.fixture_pointer(parent), "control-references.json": references,
                 "historical-provenance.json": parent["proof"], "negative-references.json": negative_references,
                 "negative-provenance.json": negative_parent["proof"], "graph-fixtures.json": graphs}
    for name, value in artifacts.items():
        write_new(output / name, value)
    plan = {"version": 1, "study": "source_allocation",
        "classification": "Prospective CPU DEVELOPMENT source-aware complementary-definition allocation replay",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(), "historical_baseline_commit": windows.BASE_COMMIT,
        "conditions": CONDITIONS, "condition_roles": {"controls": list(CONTROLS), "historical_negatives": list(NEGATIVES),
                                                     "new_candidates": list(CANDIDATES)},
        "settings": SETTINGS, "counts": windows.counts(cases), "runtime": runtime,
        "producer_sha256": own_bindings(root), "input_sha256": bindings,
        "historical_parent": parent, "negative_parent": negative_parent,
        "artifacts": {name: file_sha(output / name) for name in artifacts},
        "source_sha256": {dataset + "/" + signature: value["source_sha256"] for (dataset, signature), value in fixtures.items()},
        "baseline_controls_sha256": sha(canonical(baseline)), "prior_coverage_controls_sha256": sha(canonical(coverage)),
        "barriers": {"legacy_exact_rows": 365, "known_coverage_exact_rows": 337,
                     "new_variant_outcomes_computed": 0, "run_parent_control_rows": 730, "referenced_negative_rows": 730},
        "selection": {"legacy": "Original display-label selection and unchanged postprocessing.",
            "coverage_control": "Original canonical definition selection and source-preserving covered-row removal/backfill.",
            "aspect_fill": "Immutable original failed allocation arm; referenced, never rerun.",
            "aspect_balanced": "Immutable original failed allocation arm; referenced, never rerun.",
            "source_graph": "Source/query graph allocation followed by exact postpack preservation of structurally protected reference rows.",
            "source_graph_all": "Identical source/query graph proposal followed by exact postpack preservation of every reference row."},
        "packing": "Unchanged8 excerpts,12000 source characters,4096 exact compact cl100k tokens and original physical-line prefix packing.",
        "labels": "Query IDs, kinds, gold IDs and evidence intervals are reporting only; they never enter planner or guard.",
        "qualification": "Zero individual group, complete-row and single-file first-hit loss against BOTH controls; >=1 additional DISTINCT complete dataset/query unit beyond coverage_control. Old gains cannot qualify. No automatic default change.",
        "execution": "Freeze recomputes only365 legacy/337 known coverage controls. Commit all bound source/tests/protocol/plan before run. Reproduce730 parent controls before any new candidate; historical negative arms are read-only references. Never overwrite or resume.",
        "limits": ["All365 rows/187 query units are observed development evidence, including former holdouts.",
                   "No TanStack labels, source, model/index or retrieval calls enter this development replay.",
                   "CPU replay speed is not native latency, coding correctness, consumer billing tokens or generalization."]}
    write_new(output / "frozen-plan.json", plan)
    digest = file_sha(output / "frozen-plan.json")
    for name, values in (("baseline-gate.json", baseline), ("prior-coverage-gate.json", coverage)):
        write_new(output / name, {"exact_rows": len(values), "cases": values, "plan_sha256": digest,
                                 "new_variant_outcomes_computed": 0})
    return {"classification": plan["classification"], **plan["counts"], "exact_legacy_controls": len(baseline),
            "exact_prior_coverage_controls": len(coverage), "referenced_negative_rows": len(negatives),
            "new_variant_outcomes_computed": 0}


def validate_run(root, output, plan, started_at):
    require(plan["study"] == "source_allocation" and plan["conditions"] == CONDITIONS
            and plan["settings"] == SETTINGS, "Undeclared source-aware allocation configuration")
    require(plan["producer_sha256"] == own_bindings(root), "Prospective source/tests/protocol changed after freeze")
    bound = {**plan["producer_sha256"], **plan["input_sha256"], **plan["historical_parent"]["artifacts"],
             **plan["negative_parent"]["artifacts"],
             **{(output / name).relative_to(root).as_posix(): file_sha(output / name) for name in FROZEN_FILES}}
    for name, expected in bound.items():
        require(file_sha(root / name) == expected and git_sha(root, "HEAD", name) == expected,
                "Commit unchanged prospective inputs/source/plan before candidate evaluation: " + name)
    for name, expected in plan["artifacts"].items():
        require(file_sha(output / name) == expected, "Frozen source-aware artifact changed: " + name)
    require(not any((output / name).exists() for name in ATTEMPT_FILES), "Never overwrite or resume an attempted run")
    relative = (output / "frozen-plan.json").relative_to(root).as_posix()
    commit = subprocess.check_output(["git", "-C", str(root), "log", "-1", "--format=%H", "--", relative], text=True).strip()
    require(bool(commit), "Prospective source-aware plan must be committed")
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"])
    stamp = int(subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%ct", commit], text=True))
    require(stamp <= datetime.fromisoformat(started_at).timestamp(), "Prospective freeze commit follows run start")
    for name, expected in bound.items():
        require(git_sha(root, commit, name) == expected, "Prospective freeze blob differs: " + name)
    return {"commit": commit, "committed_at_unix": stamp, "bound_blob_count": len(bound)}


def run(root, corpus_root, output):
    plan = read(output / "frozen-plan.json")
    started_at = datetime.now(timezone.utc).isoformat()
    chain = validate_run(root, output, plan, started_at)
    digest = file_sha(output / "frozen-plan.json")
    write_new(output / "run-start.json", {"created_at_utc": started_at, "pid": os.getpid(),
        "execution_commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        "prospective_freeze": chain, "plan_sha256": digest,
        "model_calls": 0, "retrieval_calls": 0, "network_calls": 0, "resume_allowed": False})
    state = {"stage": "runtime", "exact_legacy_controls": 0, "exact_prior_coverage_controls": 0,
             "exact_parent_control_rows": 0, "attempted_new_variant_rows": 0, "new_variant_rows": 0}
    try:
        runtime, rank_bytes = prior.runtime(root)
        require(runtime == plan["runtime"], "Pinned CPU runtime changed")
        require(previous.historical_parent(root) == plan["historical_parent"]
                and historical_negatives(root) == plan["negative_parent"], "Historical provenance/artifacts changed after freeze")
        controls, negatives = previous.control_rows(root), negative_rows(root)
        with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
            cases, bindings, fixtures, old_coverage = windows.load_inputs(root, corpus_root, modules)
            require(bindings == plan["input_sha256"] and windows.counts(cases) == plan["counts"]
                    and {dataset + "/" + signature: value["source_sha256"] for (dataset, signature), value in fixtures.items()}
                        == plan["source_sha256"], "Frozen original input/source matrix changed")
            require(json.loads(canonical(windows.fixture_snapshot(fixtures))) == read(root / PARENT / "source-fixtures.json")
                    and read(output / "source-fixtures.json") == previous.fixture_pointer(plan["historical_parent"])
                    and read(output / "control-references.json") == previous.reference_manifest(cases, controls)
                    and read(output / "negative-references.json") == negative_manifest(cases, negatives)
                    and read(output / "graph-fixtures.json") == graph_snapshot(root, fixtures),
                    "Frozen complete source/control/negative references changed")
            state["stage"] = "all_historical_controls"
            baseline = windows.control_barrier(cases, modules)
            coverage = windows.coverage_barrier(cases, old_coverage, modules)
            state["exact_legacy_controls"], state["exact_prior_coverage_controls"] = len(baseline), len(coverage)
            baseline_gate, coverage_gate = (read(output / name) for name in ("baseline-gate.json", "prior-coverage-gate.json"))
            require(baseline == baseline_gate["cases"] and coverage == coverage_gate["cases"]
                    and baseline_gate["plan_sha256"] == coverage_gate["plan_sha256"] == digest
                    and sha(canonical(baseline)) == plan["baseline_controls_sha256"]
                    and sha(canonical(coverage)) == plan["prior_coverage_controls_sha256"], "Original365/337 barriers changed")
            responses, receipts = previous.verify_parent_controls(cases, controls, modules)
            state["exact_parent_control_rows"] = len(receipts)
            write_new(output / "control-gate.json", {"exact_legacy_rows": len(baseline), "exact_prior_coverage_rows": len(coverage),
                "baseline_cases": baseline, "coverage_cases": coverage, "exact_parent_control_rows": len(receipts),
                "parent_control_cases": receipts, "referenced_negative_rows": len(negatives),
                "new_variant_rows_before_gate": 0, "plan_sha256": digest})
            state["stage"] = "new_source_aware_arms"
            outcomes = []
            with (output / "replay.jsonl.gz").open("xb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
                    for case in cases:
                        refs = {name: controls[(name, *identity_tuple(case))]["reference"] for name in CONTROLS}
                        for name in CANDIDATES:
                            state["current_case"], state["current_condition"] = identity(case), name
                            state["attempted_new_variant_rows"] += 1
                            result = evaluate_candidate(case, name, modules,
                                reference_response=responses[("coverage_control", *identity_tuple(case))])
                            row = reported_row(case, name, result, responses, refs)
                            zipped.write((json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode())
                            zipped.flush()
                            raw.flush()
                            outcomes.append(row)
                            state["new_variant_rows"] += 1
            state["stage"] = "publish_all_candidate_outcomes"
            summary = summarize(outcomes, controls, negatives, cases)
            summary.update(baseline_exact_rows=len(baseline), prior_coverage_exact_rows=len(coverage),
                           exact_parent_control_rows=len(receipts), plan_sha256=digest,
                           replay_sha256=file_sha(output / "replay.jsonl.gz"))
            write_new(output / "summary.json", summary)
            with profile():
                findings = windows.findings(summary).replace("# Complementary-window development replay",
                                                              "# Source-aware allocation development replay", 1)
            with (output / "findings.md").open("x", encoding="utf-8") as stream:
                stream.write(findings + "\nOnly the two new source-aware arms are stored here. Both controls and both failed allocation arms reference immutable original records. All730 control arms reproduced before new candidates; the negative arms were never rerun.\n")
            return {"classification": summary["classification"], **summary["counts"], "qualification": summary["qualification"],
                    "baseline_exact_rows": len(baseline), "prior_coverage_exact_rows": len(coverage),
                    "stored_new_candidate_rows": len(outcomes), "referenced_negative_rows": len(negatives)}
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
