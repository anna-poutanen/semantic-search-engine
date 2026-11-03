#!/usr/bin/env python3
"""Freeze and replay a terminal role guard over immutable, already saved v1 results.

Preparation executes no guard. Replay consumes one saved v1 proposal per case;
it performs no embedding, v1 selection, retrieval, source assembly or backfill.
The original model study, its audit correction and every negative study remain
separate immutable parents. Results are observed development, not a holdout.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tarfile
import time


ROOT = Path(__file__).resolve().parents[1]
PARENT = "results/semantic-model-development-2026-11-02"
COMPLEMENTARY = "results/complementary-development-v2-2026-10-29"
RECHECK = "results/role-context-development-2026-11-03/parent-recheck.json"
ORIGINAL_FREEZE = "cca82913ef67bf41d565f6424b9619fec8270edc"
PUBLICATION = "cd450f7fb91f783f5a883526649472ac7a2f00fd"
SCHEMA = "role_context_study_v1"
MODE = "role_state_write_v1"
LIMITS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096}
SOURCES = ("scripts/research_role_context.py", "scripts/run_role_context.py", "scripts/audit_role_context.py",
           "tests/test_role_context.py", "tests/test_role_context_study.py", "tests/test_role_context_audit.py",
           "docs/role-context-protocol.md")
ARTIFACTS = ("input-manifest.json", "parent-outcome-references.json", "control-references.json", "negative-references.json")
PACKAGES = ("tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript", "tiktoken")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def jsonl(path):
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def write_new(path, value):
    # Transport preserves nested scope order; canonical hashes are separate.
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    with Path(path).open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(Path(path).parent)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def claim(path):
    path = Path(path)
    path.mkdir(exist_ok=False)
    sync_directory(path.parent)
    return path


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def module(name, root=ROOT):
    path = (Path(root) / "scripts" / (name + ".py")).resolve()
    require(path.is_relative_to(Path(root).resolve()), "Research module escapes bound source root")
    spec = importlib.util.spec_from_file_location("role_runner_" + name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    require(Path(value.__file__).resolve() == path, "Executing research source authority differs")
    return value


def runtime():
    return {"python": platform.python_version(), "packages": {name: importlib.metadata.version(name) for name in PACKAGES}}


def source_bindings(root=ROOT):
    return {name: file_sha(Path(root) / name) for name in SOURCES}


def original_closure(root):
    folder = Path(root) / PARENT / "prepared-v3"
    plan = read(folder / "frozen-plan.json")
    bound = dict(plan["source_sha256"])
    bound.update(plan["historical_artifact_sha256"])
    bound[plan["model_binding"]["path"]] = plan["model_binding"]["sha256"]
    bound.update(plan["retrieval_conditions"]["source_sha256"])
    bound.update({(folder / name).relative_to(root).as_posix(): sha for name, sha in plan["artifact_sha256"].items()})
    bound[(folder / "frozen-plan.json").relative_to(root).as_posix()] = file_sha(folder / "frozen-plan.json")
    require(len(bound) == 147, "Complete unchanged original 147-blob closure required")
    for name, sha in bound.items():
        path = (Path(root) / name).resolve()
        require(path.is_relative_to(Path(root).resolve()) and file_sha(path) == sha
                and digest(git(root, "show", ORIGINAL_FREEZE + ":" + name)) == sha,
                "Original model-study source/input blob changed: " + name)
    return bound


def archive_proof(root):
    base = Path(root) / PARENT
    manifest = read(base / "native-capture-manifest.json")
    archive = base / "native-capture.tar.gz"
    require(manifest.get("status") == "passed_lossless_archive" and len(manifest["members"]) == 6
            and archive.stat().st_size == manifest["archive_bytes"] and file_sha(archive) == manifest["archive_sha256"],
            "Original native archive bytes differ")
    with tarfile.open(archive, "r:gz") as stream:
        listing = stream.getmembers()
        members = {member.name: member for member in listing}
        require(len(listing) == len(members) == 6 and members.keys() == manifest["members"].keys()
                and all(member.isfile() for member in members.values()),
                "Exact six native archive members required")
        for name, expected in manifest["members"].items():
            member = members[name]
            require(member.size == expected["bytes"], "Native archive member length differs")
            hasher = hashlib.sha256()
            with stream.extractfile(member) as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    hasher.update(block)
            require(hasher.hexdigest() == expected["sha256"], "Native archive member SHA differs: " + name)
    return manifest


def complementary_proof(root):
    folder = Path(root) / COMPLEMENTARY
    plan, audit, start = (read(folder / name) for name in ("frozen-plan.json", "independent-audit.json", "run-start.json"))
    require(audit.get("status") == "passed" and audit.get("checks_passed") is True
            and audit.get("verified_candidate_rows") == 393 and audit.get("exact_control_rows") == 786
            and audit.get("distinct_query_units") == 201 and audit.get("retained_negative_references") == 2948,
            "Complete immutable complementary-v2 audit required")
    frozen, execution = audit["producer_freeze"]["commit"], audit["producer_execution"]
    require(start["prospective_freeze"] == audit["producer_freeze"] and start["execution_commit"] == execution,
            "Original complementary producer identities differ")
    blobs = {**plan["producer_sha256"], **plan["input_sha256"],
             COMPLEMENTARY + "/frozen-plan.json": file_sha(folder / "frozen-plan.json")}
    require(len(blobs) == audit["producer_freeze"]["bound_blob_count"], "Complete complementary prospective closure required")
    for name, sha in blobs.items():
        require(all(digest(git(root, "show", commit + ":" + name)) == sha for commit in (frozen, execution)),
                "Historical complementary producer Git blob differs: " + name)
    for name, sha in audit["auditor_sha256"].items():
        require(digest(git(root, "show", audit["auditor_execution_commit"] + ":" + name)) == sha,
                "Historical complementary auditor Git blob differs")
    artifacts = {COMPLEMENTARY + "/" + name: sha for name, sha in audit["artifact_sha256"].items()}
    artifacts[COMPLEMENTARY + "/independent-audit.json"] = file_sha(folder / "independent-audit.json")
    for name, sha in artifacts.items():
        require(file_sha(Path(root) / name) == sha, "Immutable complementary artifact differs")
    return {"producer_freeze": frozen, "producer_execution": execution,
            "auditor_execution": audit["auditor_execution_commit"], "bound_git_blob_sha256": blobs,
            "auditor_git_blob_sha256": audit["auditor_sha256"], "artifact_sha256": artifacts}


def verify_published_parents(root, bound, *, parent_recheck=RECHECK):
    for name, sha in bound.items():
        if name != parent_recheck:
            require(digest(git(root, "show", PUBLICATION + ":" + name)) == sha,
                    "Old parent bytes differ from their original public publication: " + name)


def parent_bindings(root=ROOT, *, parent_recheck=RECHECK):
    root = Path(root).resolve()
    bound = original_closure(root)
    correction = read(root / PARENT / "auditor-recheck-plan.json")
    require(correction.get("original_study_freeze") == ORIGINAL_FREEZE, "Original correction identity differs")
    bound.update(correction["source_sha256"])
    names = [parent_recheck, PARENT + "/auditor-recheck-plan.json", PARENT + "/independent-audit-recheck-1.json",
        PARENT + "/audit-attempt-1/audit.log", PARENT + "/native-capture-manifest.json", PARENT + "/native-capture.tar.gz",
        PARENT + "/replay/outcomes.jsonl.gz", PARENT + "/replay/summary.json", PARENT + "/replay/replay-start.json",
        COMPLEMENTARY + "/replay.jsonl.gz", COMPLEMENTARY + "/summary.json", COMPLEMENTARY + "/independent-audit.json",
        COMPLEMENTARY + "/frozen-plan.json", COMPLEMENTARY + "/run-start.json"]
    for name in names:
        require((root / name).resolve().is_relative_to(root), "Parent proof escapes project")
        bound[name] = file_sha(root / name)
    for name, sha in correction["source_sha256"].items():
        require(file_sha(root / name) == sha, "Audit-correction source changed")
    for name, sha in correction["immutable_result_sha256"].items():
        if name.endswith("/capture/capture.json"):
            continue  # This exact member is verified from the lossless archive.
        require(file_sha(root / name) == sha, "Original immutable correction operand changed")
    archive = archive_proof(root)
    complementary = complementary_proof(root)
    bound.update(complementary["artifact_sha256"])
    require(archive["members"]["capture/capture.json"]["sha256"] ==
            correction["immutable_result_sha256"][PARENT + "/capture/capture.json"], "Original archived capture differs")
    old, fresh = (read(root / name) for name in (PARENT + "/independent-audit-recheck-1.json", parent_recheck))
    for report in (old, fresh):
        require(report.get("status") == "passed" and report.get("checks_passed") is True
                and report.get("verified_candidate_rows") == 393 and report.get("exact_control_rows") == 786
                and report.get("distinct_query_units") == 201 and report.get("retained_negative_references") == 2948
                and report.get("producer_freeze", {}).get("commit") == ORIGINAL_FREEZE
                and report.get("producer_freeze", {}).get("bound_blob_count") == 147
                and report.get("post_outcome_auditor_correction", {}).get("normalization_count") == 26,
                "Complete original/corrected full parent audit required")
    require(canonical(old["independently_recomputed_qualification"]) == canonical(fresh["independently_recomputed_qualification"])
            and old["post_outcome_auditor_correction"]["audit_freeze"] ==
                fresh["post_outcome_auditor_correction"]["audit_freeze"], "Parent recheck changed quality/correction policy")
    verify_published_parents(root, bound, parent_recheck=parent_recheck)
    return {"sha256": dict(sorted(bound.items())), "original_bound_blob_count": 147,
            "original_freeze": ORIGINAL_FREEZE, "parent_recheck_path": parent_recheck,
            "native_archive_members": archive["members"], "complementary_parent": complementary,
            "publication_commit": PUBLICATION, "model_calls": 0, "current_v1_calls": 0}


def row_reference(path, number, row, *, condition=None):
    value = {"path": path, "line_number": number, "record_sha256": digest(canonical(row)),
             "response_sha256": digest(canonical(row["response"])), "compact_sha256": row["compact_sha256"]}
    if condition is not None:
        value["condition"] = condition
    return value


def build_inputs(cases, proposals, controls, old_negatives, complementary, *, expected_rows=393,
                 expected_units=201, expected_old_negatives=2948):
    require(len(cases) == len(proposals) == len(controls) == len(complementary) == expected_rows
            and len({case["case_id"] for case in cases}) == expected_rows
            and len({(case["dataset"], case["query_id"]) for case in cases}) == expected_units,
            "Complete original occurrence/unit/control/proposal matrix required")
    require(len(old_negatives) == expected_old_negatives, "Complete old negative evidence required")
    by_id = {row["case_id"]: (number, row) for number, row in enumerate(proposals, 1)}
    require(len(by_id) == expected_rows and by_id.keys() == {case["case_id"] for case in cases},
            "Saved v1 proposals do not match exact case identities")
    refs, manifest = [], []
    for case in cases:
        number, row = by_id[case["case_id"]]
        require(row["key"] == case["key"] and row["reference_response"] == case["reference_response"]
                and row["definition_keys"] == case["definition_keys"] and row["limits"] == case["limits"] == LIMITS,
                "Saved v1 source/control/limits differ from qualified parent")
        require(digest(row["compact_payload"].encode()) == row["compact_sha256"], "Saved v1 compact bytes changed")
        reference = {"case_id": case["case_id"], "key": case["key"],
                     **row_reference(PARENT + "/replay/outcomes.jsonl.gz", number, row)}
        refs.append(reference)
        manifest.append({"case_id": case["case_id"], "key": case["key"],
            "case_sha256": digest(canonical(case)), "coverage_sha256": digest(canonical(case["reference_response"])),
            "saved_v1_proposal_sha256": reference["response_sha256"], "parent_record_sha256": reference["record_sha256"],
            "legacy_sha256": digest(canonical(case["legacy_control"])), "groups_sha256": digest(canonical(case["groups"]))})
    require(all(set(row["controls"]) == {"legacy", "coverage_control"} for row in controls),
            "Both exact original controls required per case")
    control_cases = {row["case_id"]: row for row in controls}
    require(len(control_cases) == expected_rows and control_cases.keys() == by_id.keys()
            and all(canonical(control_cases[case["case_id"]]["controls"]) == canonical(case["control_references"])
                    for case in cases), "Original control reference identities changed")
    require(len({tuple(row[name] for name in ("subset", "dataset", "query_id", "condition")) for row in complementary})
            == expected_rows and {tuple(row[name] for name in ("subset", "dataset", "query_id", "condition"))
            for row in complementary} == {tuple(case["key"]) for case in cases},
            "Complete complementary negative occurrence identities required")
    negatives = deepcopy(old_negatives)
    negatives += [row_reference(COMPLEMENTARY + "/replay.jsonl.gz", number, row, condition="complementary_v2")
                  for number, row in enumerate(complementary, 1)]
    negatives += [row_reference(PARENT + "/replay/outcomes.jsonl.gz", number, row, condition="semantic_final_v1")
                  for number, row in enumerate(proposals, 1)]
    require(len({(row["path"], row["line_number"]) for row in negatives}) == len(negatives),
            "Negative evidence references overlap")
    return {"input-manifest.json": {"schema": SCHEMA, "cases": manifest,
                "cases_path": PARENT + "/prepared-v3/operands.json", "new_guard_outcomes_computed": 0},
            "parent-outcome-references.json": refs, "control-references.json": deepcopy(controls),
            "negative-references.json": negatives}


def prepare(root, output, *, parent_recheck=RECHECK):
    root, output = Path(root).resolve(), Path(output).resolve()
    require(output.is_relative_to(root), "Prepared inputs must live inside project")
    claim(output)
    try:
        sources, environment = source_bindings(root), runtime()
        parents = parent_bindings(root, parent_recheck=parent_recheck)
        cases = read(root / PARENT / "prepared-v3/operands.json")["cases"]
        documents = build_inputs(cases, jsonl(root / PARENT / "replay/outcomes.jsonl.gz"),
            read(root / PARENT / "prepared-v3/control-references.json"),
            read(root / PARENT / "prepared-v3/negative-references.json"), jsonl(root / COMPLEMENTARY / "replay.jsonl.gz"))
        for name in ARTIFACTS:
            write_new(output / name, documents[name])
        require(source_bindings(root) == sources and runtime() == environment, "Role source/runtime changed during preparation")
        plan = {"schema": SCHEMA, "mode": MODE, "settings": LIMITS, "source_sha256": sources,
            "runtime": environment, "parent_binding": parents,
            "artifact_sha256": {name: file_sha(output / name) for name in ARTIFACTS},
            "counts": {"cases": 393, "distinct_units": 201, "control_references": 786,
                "old_negative_references": 2948, "complementary_v2_negative_references": 393,
                "semantic_v1_negative_references": 393, "total_negative_references": 3734},
            "calls_before_freeze": {name: 0 for name in ("role_guard", "current_v1", "model", "retrieval", "index", "coalesce", "backfill")},
            "classification": "Prospective CPU role-guard preparation over already observed saved proposals; no fresh holdout",
            "default_change": False, "reserved_attrs_access": False}
        write_new(output / "frozen-plan.json", plan)
        return plan
    except BaseException as error:
        try:
            write_new(output / "prepare-failure.json", {"error_type": type(error).__name__, "new_guard_outcomes_computed": 0})
        except BaseException as failure:
            raise error from failure
        raise


def load_prepared(prepared, *, root=ROOT):
    root, folder = Path(root).resolve(), Path(prepared).resolve()
    require(not (folder / "prepare-failure.json").exists(), "Failed role preparation cannot be resumed")
    plan = read(folder / "frozen-plan.json")
    require(plan.get("schema") == SCHEMA and plan.get("mode") == MODE and plan.get("settings") == LIMITS
            and set(plan["artifact_sha256"]) == set(ARTIFACTS), "Complete role plan schema required")
    for name, sha in plan["artifact_sha256"].items():
        require(file_sha(folder / name) == sha, "Prepared role artifact changed: " + name)
    require(canonical(plan["parent_binding"]) == canonical(parent_bindings(root,
        parent_recheck=plan["parent_binding"]["parent_recheck_path"])), "Complete original parent authority closure differs")
    cases = read(root / PARENT / "prepared-v3/operands.json")["cases"]
    proposals = jsonl(root / PARENT / "replay/outcomes.jsonl.gz")
    values = {name: read(folder / name) for name in ARTIFACTS}
    rebuilt = build_inputs(cases, proposals, read(root / PARENT / "prepared-v3/control-references.json"),
        read(root / PARENT / "prepared-v3/negative-references.json"), jsonl(root / COMPLEMENTARY / "replay.jsonl.gz"))
    require(canonical(values) == canonical(rebuilt), "Role prepared case/control/proposal/negative references changed")
    return {"plan": plan, "cases": cases, "parent_proposals": {row["case_id"]: row for row in proposals},
            "proposal_references": {row["case_id"]: row for row in values["parent-outcome-references.json"]},
            "input_manifest": values["input-manifest.json"], "controls": values["control-references.json"],
            "negatives": values["negative-references.json"]}


def verify_frozen(prepared, freeze, *, root=ROOT, started_at=None):
    root, folder = Path(root).resolve(), Path(prepared).resolve()
    require(type(freeze) is str and re.fullmatch(r"[0-9a-f]{40}", freeze), "Use a full immutable prospective freeze SHA")
    require(folder.is_relative_to(root), "Role frozen preparation escapes project")
    plan = read(folder / "frozen-plan.json")
    require(plan["source_sha256"] == source_bindings(root) and plan["runtime"] == runtime(), "Role source/runtime changed")
    require(git(root, "status", "--porcelain", "--untracked-files=no").strip() == b"", "Tracked worktree is dirty")
    execution = git(root, "rev-parse", "HEAD").decode().strip()
    require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", freeze, execution],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0, "Role freeze is not an ancestor")
    start = datetime.now(timezone.utc).isoformat() if started_at is None else started_at
    require(datetime.fromisoformat(git(root, "show", "-s", "--format=%cI", freeze).decode().strip())
            <= datetime.fromisoformat(start), "Role freeze must precede guard outcomes")
    bound = {**plan["source_sha256"], **plan["parent_binding"]["sha256"]}
    bound.update({(folder / name).relative_to(root).as_posix(): sha for name, sha in plan["artifact_sha256"].items()})
    bound[(folder / "frozen-plan.json").relative_to(root).as_posix()] = file_sha(folder / "frozen-plan.json")
    for name, sha in bound.items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and file_sha(path) == sha
                and all(digest(git(root, "show", commit + ":" + name)) == sha for commit in (freeze, execution)),
                "Role frozen source/parent/input bytes differ: " + name)
    return {"commit": freeze, "execution_commit": execution, "bound_blob_count": len(bound),
            "plan_sha256": file_sha(folder / "frozen-plan.json"), "new_guard_outcomes_before_freeze": 0}


def replay(prepared, output, *, binding, root=ROOT, counter=None, guard=None, audit_hook=None, finish=None):
    """Execute exactly one terminal guard per saved proposal; no v1 rerun."""
    root = Path(root).resolve()
    if str(root / "scripts") not in sys.path:
        sys.path.insert(0, str(root / "scripts"))
    transport = module("semantic_model_study", root)
    from semantic_search.context import compact_result, encoding, serialize_context
    import semantic_search.context as context

    require(Path(context.__file__).resolve() == root / "src/semantic_search/context.py", "Executing context source authority differs")
    guard = module("research_role_context", root).apply_role_guard if guard is None else guard
    counter = encoding() if counter is None else counter
    output = claim(output)
    ledger = None
    began = time.perf_counter()
    try:
        ledger = transport.Ledger(output / "outcomes.jsonl.gz", compressed=True)
        write_new(output / "run-start.json", {"schema": SCHEMA, "mode": MODE,
            "started_at": datetime.now(timezone.utc).isoformat(), "binding": binding,
            "classification": "CPU terminal role guard over saved v1 proposals; no native warm or fresh-corpus result"})
        for case in prepared["cases"]:
            saved = prepared["parent_proposals"][case["case_id"]]
            reference = prepared["proposal_references"][case["case_id"]]
            phase = time.perf_counter()
            response, receipt = guard(deepcopy(case["reference_response"]), deepcopy(saved["response"]),
                                      definition_keys=deepcopy(case["definition_keys"]))
            require(canonical(response) in (canonical(case["reference_response"]), canonical(saved["response"])),
                    "Role guard must return one exact frozen operand")
            payload = serialize_context(compact_result(response))
            count = len(counter.encode(payload, disallowed_special=()))
            require(response["token_usage"]["tokens"] == count <= case["limits"]["token_budget"]
                    and len(response["results"]) <= case["limits"]["max_results"]
                    and sum(len(row["source"]) for row in response["results"]) == response["total_source_chars"]
                    <= case["limits"]["source_budget"], "Role exact source/token budget differs")
            score = transport.evidence(case, response)
            comparisons = {name: transport.compare(transport.evidence(case, control), score) for name, control in
                (("legacy", case["legacy_control"]), ("coverage_control", case["reference_response"]),
                 ("semantic_final_v1", saved["response"]))}
            row = {"case_id": case["case_id"], "key": case["key"],
                **{name: case[name] for name in ("subset", "dataset", "query_id", "condition", "kind", "multi_file", "qualified")},
                "parent_proposal_ref": reference, "input_sha256": {"case": digest(canonical(case)),
                    "coverage": digest(canonical(case["reference_response"])), "saved_v1_proposal": digest(canonical(saved["response"]))},
                "response": response, "role_guard_receipt": receipt, "compact_payload": payload,
                "compact_sha256": digest(payload.encode()), "actual_compact_tokens": count,
                "calls": {"role_guard": 1, **{name: 0 for name in ("current_v1", "model", "retrieval", "index", "coalesce", "backfill")}},
                "role_guard_s": time.perf_counter() - phase, **score, "comparisons": comparisons}
            if audit_hook is not None:
                audit_hook(case, saved, row)
            ledger.append(row)
        if finish is not None:
            finish()
        ledger.close()
        changes = {name: [] for name in ("legacy", "coverage_control")}
        gains = set()
        for row in ledger.rows:
            for name in changes:
                comparison = row["comparisons"][name]
                if comparison["group_losses"] or comparison["complete_loss"] or comparison["single_file_first_hit_loss"]:
                    changes[name].append({"case_id": row["case_id"], **comparison})
            if row["multi_file"] and row["comparisons"]["coverage_control"]["complete_gain"]:
                gains.add((row["dataset"], row["query_id"]))
        summary = {"schema": SCHEMA, "mode": MODE, "status": "passed", "stored_candidate_rows": len(ledger.rows),
            "distinct_dataset_query_units": len({(c["dataset"], c["query_id"]) for c in prepared["cases"]}),
            "referenced_control_rows": 2 * len(ledger.rows), "referenced_saved_v1_proposals": len(ledger.rows),
            "retained_negative_references": len(prepared["negatives"]),
            "quality_gate": {"qualified": bool(gains) and not any(changes.values()), "individual_losses": changes,
                "additional_distinct_complete_multi_queries_vs_coverage": [list(value) for value in sorted(gains)],
                "automatic_default_change": False, "fresh_and_native_validation_still_required": True},
            "role_guard_activity": {"accepted": sum(row["role_guard_receipt"]["accepted"] for row in ledger.rows),
                "fallback_reasons": dict(Counter(row["role_guard_receipt"]["fallback_reason"] or "accepted" for row in ledger.rows))},
            "candidate_phase_calls": {"role_guard": len(ledger.rows), **{name: 0 for name in
                ("current_v1", "model", "retrieval", "index", "coalesce", "backfill")}},
            "cpu_replay_total_s": time.perf_counter() - began,
            "artifact_sha256": {"outcomes.jsonl.gz": file_sha(output / "outcomes.jsonl.gz"),
                                "run-start.json": file_sha(output / "run-start.json")},
            "classification": "Already-observed development replay; native warm latency and reserved-corpus quality unmeasured"}
        write_new(output / "summary.json", summary)
        return summary
    except BaseException as error:
        try:
            if ledger is not None:
                ledger.close()
            write_new(output / "failure.json", {"error_type": type(error).__name__,
                "stored_rows": 0 if ledger is None else len(ledger.rows),
                "outcomes_sha256": file_sha(output / "outcomes.jsonl.gz") if ledger is not None else None})
        except BaseException as failure:
            raise error from failure
        raise
    finally:
        if ledger is not None and not ledger.closed:
            original = sys.exception()
            try:
                ledger.close()
            except BaseException as failure:
                if original is not None:
                    raise original from failure
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", metavar="NEW_DIRECTORY")
    modes.add_argument("--replay", metavar="PREPARED_DIRECTORY")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--parent-recheck", default=RECHECK)
    parser.add_argument("--freeze")
    parser.add_argument("--output")
    args = parser.parse_args()
    root = args.root.resolve()
    require(Path(__file__).resolve() == root / "scripts/run_role_context.py", "Executing runner differs from bound root")
    if args.prepare:
        result = prepare(root, args.prepare, parent_recheck=args.parent_recheck)
        print(json.dumps({"status": "prepared", "counts": result["counts"]}))
    else:
        require(args.freeze and args.output, "Replay requires --freeze and exclusive --output")
        frozen = verify_frozen(args.replay, args.freeze, root=root)
        prepared = load_prepared(args.replay, root=root)
        result = replay(prepared, args.output, binding={"freeze": frozen,
            "execution_commit": git(root, "rev-parse", "HEAD").decode().strip()}, root=root,
            finish=lambda: verify_frozen(args.replay, args.freeze, root=root))
        print(json.dumps({"status": result["status"], "quality_gate": result["quality_gate"]}))


if __name__ == "__main__":
    main()
