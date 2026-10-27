#!/usr/bin/env python3
"""Freeze and replay complementary definitions on already observed CPU inputs.

Historical ledgers remain immutable inputs. Only the two new allocation arms
are written; controls are referenced by their original record and payload hashes.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("allocation_window_helpers", ROOT / "scripts/replay_windows.py")
windows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(windows)
prior = windows.prior
require, sha, read, write_new = windows.require, windows.sha, windows.read, windows.write_new
canonical, identity, identity_tuple = windows.canonical, windows.identity, windows.identity_tuple
SETTINGS = dict(windows.SETTINGS)
PARENT = "results/window-development-2026-10-21"
CONDITIONS = {
    "legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
    "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
    "aspect_fill": {"selector": "aspect_fill_context", "postprocessor": "coalesce_context"},
    "aspect_balanced": {"selector": "aspect_balanced_context", "postprocessor": "coalesce_context"},
}
CONTROLS = ("legacy", "coverage_control")
CANDIDATES = ("aspect_fill", "aspect_balanced")
FROZEN_FILES = ("frozen-plan.json", "baseline-gate.json", "prior-coverage-gate.json",
                "source-fixtures.json", "control-references.json", "historical-provenance.json")
ATTEMPT_FILES = windows.ATTEMPT_FILES


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_sha(root, commit, path):
    """Hash a committed blob without holding the historical matrix in memory."""
    process = subprocess.Popen(["git", "-C", str(root), "show", commit + ":" + path], stdout=subprocess.PIPE)
    digest = hashlib.sha256()
    with process.stdout:
        for block in iter(lambda: process.stdout.read(1024 * 1024), b""):
            digest.update(block)
    require(process.wait() == 0, "Missing committed historical blob: " + path)
    return digest.hexdigest()


def original_blob_proof(root, *, freeze_commit, execution_commit, started_at, bound,
                        auditor_commit, auditor_bound):
    """Verify the original chain in its own history, without executing its code.

    A rewritten current branch need not descend from the original execution.
    Its exact commit IDs are retained; replacement commits are never substituted.
    """
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", freeze_commit, execution_commit])
    stamp = int(subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%ct", freeze_commit], text=True))
    require(stamp <= datetime.fromisoformat(started_at).timestamp(), "Historical freeze follows its recorded run")
    for name, expected in bound.items():
        require(git_sha(root, freeze_commit, name) == expected
                and git_sha(root, execution_commit, name) == expected,
                "Original historical execution blob differs: " + name)
    for name, expected in auditor_bound.items():
        require(git_sha(root, auditor_commit, name) == expected, "Original auditor execution blob differs: " + name)
    ancestor = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", execution_commit, "HEAD"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    require(ancestor in (0, 1), "Cannot resolve historical/current ancestry")
    return {"original_freeze_commit": freeze_commit, "original_execution_commit": execution_commit,
            "original_auditor_commit": auditor_commit, "freeze_committed_at_unix": stamp,
            "original_bound_sha256": dict(bound), "original_auditor_sha256": dict(auditor_bound),
            "original_execution_is_current_ancestor": ancestor == 0,
            "resolution": "Original committed blobs verified as immutable data; no historical producer code executed. "
                          "A current history rewrite is recorded, never replaced by a claim that current source produced old outcomes."}


def historical_parent(root):
    folder = root / PARENT
    plan, started, audit = (read(folder / name) for name in ("frozen-plan.json", "run-start.json", "independent-audit.json"))
    require(audit.get("status") == "passed" and audit.get("checks_passed") is True
            and audit.get("exact_legacy_controls") == 365 and audit.get("exact_prior_coverage_controls") == 337
            and audit.get("verified_outcome_rows") == 1825, "Original independent window audit required")
    paths = {PARENT + "/" + name: digest for name, digest in audit["artifact_sha256"].items()}
    for name in ("independent-audit.json", "findings.md"):
        paths[PARENT + "/" + name] = file_sha(folder / name)
    for name, expected in paths.items():
        require(file_sha(root / name) == expected and git_sha(root, "HEAD", name) == expected,
                "Historical artifact changed or is uncommitted: " + name)
    frozen = {PARENT + "/" + name: paths[PARENT + "/" + name] for name in
              ("frozen-plan.json", "baseline-gate.json", "prior-coverage-gate.json", "source-fixtures.json")}
    require(started["plan_sha256"] == paths[PARENT + "/frozen-plan.json"]
            and audit["producer_execution"] == started["execution_commit"], "Original recorded execution/plan disagree")
    proof = original_blob_proof(root, freeze_commit=audit["producer_freeze"]["commit"],
        execution_commit=started["execution_commit"], started_at=started["created_at_utc"],
        bound={**plan["producer_sha256"], **plan["input_sha256"], **frozen},
        auditor_commit=audit["auditor_execution_commit"], auditor_bound=audit["auditor_sha256"])
    return {"classification": "Immutable observed development evidence; original execution identities retained",
            "artifacts": paths, "original_plan_sha256": started["plan_sha256"],
            "original_execution_commit": started["execution_commit"], "proof": proof}


def control_rows(root):
    """Read only the two immutable controls, retaining no old candidate traces."""
    result, total = {}, 0
    with gzip.open(root / PARENT / "replay.jsonl.gz", "rt", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            row = json.loads(line)
            total += 1
            if row["selector"] not in CONTROLS:
                continue
            key = (row["selector"], *identity_tuple(row))
            require(key not in result, "Duplicate historical allocation control")
            reference = {"line_number": number, "record_sha256": sha(canonical(row)),
                         "response_sha256": sha(canonical(row["response"])), "compact_sha256": row["compact_sha256"]}
            require(sha(row["compact_payload"].encode()) == reference["compact_sha256"], "Historical compact hash differs")
            result[key] = {name: deepcopy(row[name]) for name in
                           ("response", "compact_payload", "kind", "multi_file", "evidence_groups", "evidence_ranks")}
            result[key]["reference"] = reference
    require(total == 1825 and len(result) == 730, "Original five-condition / two-control matrix differs")
    return result


def reference_manifest(cases, controls):
    require(set(controls) == {(name, *identity_tuple(case)) for case in cases for name in CONTROLS},
            "Historical controls do not cover the declared 365 cases")
    return {"classification": "References to immutable original control rows; no copied historical matrix",
            "path": PARENT + "/replay.jsonl.gz", "record_hash": "SHA256 of UTF-8 sorted-key compact JSON, no newline",
            "cases": [{**identity(case), "controls": {name: controls[(name, *identity_tuple(case))]["reference"]
                                                      for name in CONTROLS}} for case in cases]}


@contextmanager
def profile():
    """Change helper configuration for this call only; historical files stay intact."""
    with patch.object(windows, "CONDITIONS", CONDITIONS), patch.object(windows, "CANDIDATES", CANDIDATES):
        yield


def own_bindings(root):
    names = ["scripts/replay_allocation.py", "tests/test_allocation_replay.py", "docs/allocation-development-protocol.md",
             "src/semantic_search/allocation.py", "src/semantic_search/retrieval.py", "tests/test_allocation.py",
             "tests/test_allocation_backend.py"]
    return {**windows.own_bindings(root), **{name: file_sha(root / name) for name in names}}


def evaluate_candidate(case, name, modules):
    require(name in CANDIDATES, "Only the two prospective candidate arms may be evaluated")
    allocation = importlib.import_module("semantic_search.allocation")
    planner, receipts = allocation.allocation_plan, []
    def capture(*args, **kwargs):
        value = planner(*args, **kwargs)
        receipts.append(deepcopy(value[3]))
        return value
    selector_name = CONDITIONS[name]["selector"]
    with profile(), patch.object(modules["selection"], selector_name, getattr(allocation, selector_name), create=True), \
            patch.object(allocation, "allocation_plan", capture):
        result = windows.evaluate(case, name, modules)
    require(len(receipts) == 1, "Each allocation selector must use its pure planner exactly once")
    result["allocation_receipt"] = receipts[0]
    return result


def fixture_pointer(parent):
    path = PARENT + "/source-fixtures.json"
    return {"classification": "Immutable source-only inventory pointer; historical fixture bytes are not duplicated",
            "path": path, "sha256": parent["artifacts"][path]}


def freeze(root, corpus_root, output):
    require(not output.exists(), "Use a new freeze directory")
    runtime, rank_bytes = prior.runtime(root)
    parent = historical_parent(root)
    historical_controls = control_rows(root)
    with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
        cases, bindings, fixtures, previous = windows.load_inputs(root, corpus_root, modules)
        controls = windows.control_barrier(cases, modules)
        coverage = windows.coverage_barrier(cases, previous, modules)
        references = reference_manifest(cases, historical_controls)
        require(json.loads(canonical(windows.fixture_snapshot(fixtures))) == read(root / PARENT / "source-fixtures.json"),
                "Complete historical source/definition fixture changed")
    output.mkdir(parents=True)
    write_new(output / "source-fixtures.json", fixture_pointer(parent))
    write_new(output / "control-references.json", references)
    write_new(output / "historical-provenance.json", parent["proof"])
    plan = {"version": 1, "study": "allocation", "classification": "Prospective CPU DEVELOPMENT complementary-definition allocation replay",
            "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
            "historical_baseline_commit": windows.BASE_COMMIT,
            "conditions": CONDITIONS, "settings": SETTINGS, "counts": windows.counts(cases), "runtime": runtime,
            "producer_sha256": own_bindings(root), "input_sha256": bindings,
            "historical_parent": parent,
            "artifacts": {name: file_sha(output / name) for name in
                          ("source-fixtures.json", "control-references.json", "historical-provenance.json")},
            "source_sha256": {dataset + "/" + signature: value["source_sha256"] for (dataset, signature), value in fixtures.items()},
            "baseline_controls_sha256": sha(canonical(controls)), "prior_coverage_controls_sha256": sha(canonical(coverage)),
            "barriers": {"legacy_exact_rows": 365, "known_coverage_exact_rows": 337, "new_variant_outcomes_computed": 0,
                         "run_parent_control_rows": 730},
            "selection": {"legacy": "Original display-label selection and unchanged postprocessing.",
                "coverage_control": "Original canonical-definition selection and source-preserving covered-row removal/backfill.",
                "aspect_fill": "Existing 2-4 literal clauses only. Protect the first five baseline anchors and every original dependency and its owner; allocate remaining original slots by inverse-definition-frequency novel query terms divided by raw rank, preserving every baseline query term or returning the exact control.",
                "aspect_balanced": "Same clause, dependency, novelty, source and query-term guards; protect the first three baseline anchors so remaining ranked implementations can compete for the existing slots."},
            "packing": "Unchanged 8 excerpts, 12000 source characters, 4096 exact compact cl100k tokens; existing physical-line prefix packing and coverage backfill.",
            "labels": "Query IDs, kinds and gold intervals are reporting only. Planner sees raw candidates, literal query, original graph/definition identities and slot cap; never labels.",
            "qualification": "No lost full group, complete row or single-file first hit against either legacy or coverage_control; at least one additional DISTINCT complete dataset/query unit over coverage_control. Old gains do not count as new. No automatic default change.",
            "execution": "Freeze only recomputes 365 legacy and 337 prior coverage controls. Commit the exact prospective source/tests/protocol and plan before --run. Run reproduces 730 immutable parent control results before either new candidate. Never overwrite or resume an attempt.",
            "limits": ["All 365 rows / 187 query units are observed development evidence, including urllib3.",
                       "No fresh TanStack source labels or outcomes enter allocation or this replay.",
                       "No model, index, retrieval, reranking, network, GPU or numerical-runtime calls; replay speed is not native latency.",
                       "Historical path/full-interval source evidence is not coding correctness or provider token usage."]}
    write_new(output / "frozen-plan.json", plan)
    digest = file_sha(output / "frozen-plan.json")
    write_new(output / "baseline-gate.json", {"exact_rows": len(controls), "cases": controls,
              "plan_sha256": digest, "new_variant_outcomes_computed": 0})
    write_new(output / "prior-coverage-gate.json", {"exact_rows": len(coverage), "cases": coverage,
              "plan_sha256": digest, "new_variant_outcomes_computed": 0})
    return {"classification": plan["classification"], **plan["counts"], "exact_legacy_controls": len(controls),
            "exact_prior_coverage_controls": len(coverage), "new_variant_outcomes_computed": 0}


def validate_run(root, output, plan, started_at):
    require(plan["conditions"] == CONDITIONS and plan["settings"] == SETTINGS, "Undeclared allocation configuration")
    require(plan["producer_sha256"] == own_bindings(root), "Prospective source/tests/protocol changed after freeze")
    bound = {**plan["producer_sha256"], **plan["input_sha256"], **plan["historical_parent"]["artifacts"],
             **{(output / name).relative_to(root).as_posix(): file_sha(output / name) for name in FROZEN_FILES}}
    for name, expected in bound.items():
        require(file_sha(root / name) == expected and git_sha(root, "HEAD", name) == expected,
                "Commit unchanged frozen inputs/source/plan before candidate evaluation: " + name)
    for name, expected in plan["artifacts"].items():
        require(file_sha(output / name) == expected, "Frozen allocation artifact changed: " + name)
    require(not any((output / name).exists() for name in ATTEMPT_FILES), "Never overwrite or resume a completed/failed attempt")
    relative = (output / "frozen-plan.json").relative_to(root).as_posix()
    commit = subprocess.check_output(["git", "-C", str(root), "log", "-1", "--format=%H", "--", relative], text=True).strip()
    require(bool(commit), "Prospective allocation plan must be committed")
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"])
    stamp = int(subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%ct", commit], text=True))
    require(stamp <= datetime.fromisoformat(started_at).timestamp(), "Prospective freeze commit follows run start")
    for name, expected in bound.items():
        require(git_sha(root, commit, name) == expected, "Prospective freeze blob differs: " + name)
    return {"commit": commit, "committed_at_unix": stamp, "bound_blob_count": len(bound)}


def verify_parent_controls(cases, controls, modules):
    results, receipts = {}, []
    with profile():
        for case in cases:
            for name in CONTROLS:
                expected = controls[(name, *identity_tuple(case))]
                actual = windows.evaluate(case, name, modules)
                require(actual["response"]["results"] == expected["response"]["results"]
                        and actual["compact_payload"] == expected["compact_payload"],
                        "Immutable parent full-row/compact control differs: " + str((name, *identity_tuple(case))))
                require(expected["kind"] == case["kind"] and expected["multi_file"] == case["multi_file"]
                        and expected["evidence_groups"] == case["groups"]
                        and prior.evidence_ranks(actual["response"]["results"], case["groups"]) == expected["evidence_ranks"],
                        "Historical control reporting evidence differs")
                results[(name, *identity_tuple(case))] = actual["response"]
                receipts.append({**identity(case), "selector": name, **expected["reference"]})
    require(len(receipts) == 730, "All 730 original parent control arms must precede new candidates")
    return results, receipts


def reported_row(case, name, result, control_responses, references):
    comparisons = {control: windows.comparison(control_responses[(control, *identity_tuple(case))],
                   result["response"], case["groups"]) for control in CONTROLS}
    return {**identity(case), "selector": name, "kind": case["kind"], "multi_file": case["multi_file"],
            "evidence_groups": case["groups"], "evidence_ranks": prior.evidence_ranks(result["response"]["results"], case["groups"]),
            "comparisons": comparisons, "control_references": references, **result}


def summarize(outcomes, controls, cases):
    # Control responses enter reporting in memory only. They are immutable parent
    # records, not copied to this study's new-candidate ledger.
    all_rows = list(outcomes)
    for case in cases:
        refs = {name: controls[(name, *identity_tuple(case))]["reference"] for name in CONTROLS}
        responses = {(name, *identity_tuple(case)): controls[(name, *identity_tuple(case))]["response"] for name in CONTROLS}
        for name in CONTROLS:
            saved = controls[(name, *identity_tuple(case))]
            all_rows.append(reported_row(case, name, {"response": saved["response"]}, responses, refs))
    with profile():
        result = windows.summarize(all_rows, windows.counts(cases))
    result["classification"] = "Observed-source CPU DEVELOPMENT complementary-definition allocation replay"
    result["study"] = "allocation"
    result["stored_new_candidate_rows"] = len(outcomes)
    result["referenced_control_rows"] = 730
    return result


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
        require(historical_parent(root) == plan["historical_parent"], "Historical provenance/artifacts changed after freeze")
        saved_controls = control_rows(root)
        with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
            cases, bindings, fixtures, previous = windows.load_inputs(root, corpus_root, modules)
            require(bindings == plan["input_sha256"] and windows.counts(cases) == plan["counts"]
                    and {dataset + "/" + signature: fx["source_sha256"] for (dataset, signature), fx in fixtures.items()} == plan["source_sha256"],
                    "Frozen input/source matrix changed")
            require(json.loads(canonical(windows.fixture_snapshot(fixtures))) == read(root / PARENT / "source-fixtures.json")
                    and read(output / "source-fixtures.json") == fixture_pointer(plan["historical_parent"])
                    and read(output / "control-references.json") == reference_manifest(cases, saved_controls),
                    "Frozen fixture/control references changed")
            state["stage"] = "all_historical_controls"
            controls = windows.control_barrier(cases, modules)
            coverage = windows.coverage_barrier(cases, previous, modules)
            state["exact_legacy_controls"], state["exact_prior_coverage_controls"] = len(controls), len(coverage)
            baseline_gate, coverage_gate = (read(output / name) for name in ("baseline-gate.json", "prior-coverage-gate.json"))
            require(controls == baseline_gate["cases"] and coverage == coverage_gate["cases"]
                    and baseline_gate["plan_sha256"] == coverage_gate["plan_sha256"] == digest
                    and sha(canonical(controls)) == plan["baseline_controls_sha256"]
                    and sha(canonical(coverage)) == plan["prior_coverage_controls_sha256"], "Original 365/337 barriers changed")
            control_responses, parent_receipts = verify_parent_controls(cases, saved_controls, modules)
            state["exact_parent_control_rows"] = len(parent_receipts)
            write_new(output / "control-gate.json", {"exact_legacy_rows": len(controls), "exact_prior_coverage_rows": len(coverage),
                      "baseline_cases": controls, "coverage_cases": coverage, "exact_parent_control_rows": len(parent_receipts),
                      "parent_control_cases": parent_receipts, "new_variant_rows_before_gate": 0, "plan_sha256": digest})
            state["stage"] = "new_allocation_arms"
            outcomes = []
            for case in cases:
                refs = {name: saved_controls[(name, *identity_tuple(case))]["reference"] for name in CONTROLS}
                for name in CANDIDATES:
                    state["current_case"], state["current_condition"] = identity(case), name
                    state["attempted_new_variant_rows"] += 1
                    result = evaluate_candidate(case, name, modules)
                    outcomes.append(reported_row(case, name, result, control_responses, refs))
                    state["new_variant_rows"] += 1
            state["stage"] = "publish_all_candidate_outcomes"
            with (output / "replay.jsonl.gz").open("xb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
                    for row in outcomes:
                        zipped.write((json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode())
            summary = summarize(outcomes, saved_controls, cases)
            summary.update(baseline_exact_rows=len(controls), prior_coverage_exact_rows=len(coverage),
                           exact_parent_control_rows=len(parent_receipts), plan_sha256=digest,
                           replay_sha256=file_sha(output / "replay.jsonl.gz"))
            write_new(output / "summary.json", summary)
            with profile():
                findings = windows.findings(summary).replace("# Complementary-window development replay", "# Complementary-definition allocation development replay", 1)
            with (output / "findings.md").open("x", encoding="utf-8") as stream:
                stream.write(findings + "\nOnly the two new candidate arms are stored here. Both controls reference their immutable original records; all 730 control arms reproduced before either candidate.\n")
            return {"classification": summary["classification"], **summary["counts"], "qualification": summary["qualification"],
                    "baseline_exact_rows": len(controls), "prior_coverage_exact_rows": len(coverage), "stored_new_candidate_rows": len(outcomes)}
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
