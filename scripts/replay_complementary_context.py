#!/usr/bin/env python3
"""Freeze/replay one complementary-window selector on immutable observed ranks."""
from __future__ import annotations

import argparse
from collections import Counter
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
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import complementary_inputs as inputs

SPEC = importlib.util.spec_from_file_location("complementary_window_helpers", ROOT / "scripts/replay_windows.py")
windows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(windows)
prior = windows.prior
require, canonical, sha, read, file_sha = inputs.require, inputs.canonical, inputs.sha, inputs.read, inputs.file_sha
MODE, CONTROLS = "complementary_v2", inputs.CONTROLS
SETTINGS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096, "include_dependencies": True}
FROZEN_FILES = ("frozen-plan.json", "input-manifest.json", "control-references.json",
                "negative-references.json", "control-parity.json")
ATTEMPT_FILES = ("run-start.json", "control-gate.json", "replay.jsonl.gz", "summary.json",
                 "findings.md", "failure.json", "freeze-failure.json")
OWN_FILES = ("scripts/replay_complementary_context.py", "tests/test_complementary_context_replay.py",
             "scripts/audit_complementary_context.py", "tests/test_complementary_context_audit.py",
             "scripts/complementary_inputs.py", "tests/test_complementary_inputs.py",
             "docs/complementary-context-v2-protocol.md", "src/semantic_search/complementary_context.py",
             "tests/test_complementary_context.py", "tests/test_complementary_context_backend.py",
             "scripts/replay_windows.py", "scripts/replay_selection.py", "scripts/audit_flow_native.py",
             "scripts/audit_flow_allocation.py", "scripts/audit_source_allocation.py", "scripts/audit_windows.py",
             "scripts/audit_selection.py", "results/complementary-preparation-2026-10-29/pre-freeze-review.md",
             "results/complementary-preparation-2026-10-29/validation.json",
             "results/complementary-preparation-2026-10-29/core-tests.log",
             "results/complementary-preparation-2026-10-29/focused-tests.log")


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def own_bindings(root):
    helpers = set(prior.MODULES) | {"source_allocation", "flow_allocation", "window_assembly", "retrieval"}
    names = [*OWN_FILES, *("src/semantic_search/" + name + ".py" for name in sorted(helpers))]
    return {name: file_sha(root / name) for name in names}


def identity(case):
    return {name: case[name] for name in ("subset", "dataset", "query_id", "condition")}


def ordered_template(case):
    return prior.ordered_response(case["original"])


def evaluate_control(case, name, modules):
    require(name in CONTROLS, "Undeclared control")
    selector = getattr(modules["selection"], "ranked_context" if name == "legacy" else "definition_context")
    selected, eligible, origins = selector(case["pairs"], query=case["original"]["query"],
        max_results=8, include_dependencies=True, references=case["graph"],
        definition_keys=case["definition_keys"], ambiguous_symbols=case["ambiguous_symbols"])
    physical, context = modules["corpus"].physical_lines, modules["context"]
    value = windows.pack(ordered_template(case), [(item, score, *origins.get(item.id, ("retrieval", None)))
        for item, score in eligible], context, physical)
    if name == "coverage_control":
        coalesce = importlib.import_module("semantic_search.context_backfill").coalesce_context
        value = coalesce(value, windows.full_rows(case, selected, physical),
                         max_results=8, source_budget=12000, token_budget=4096)
    verify_response(value, case, modules)
    return value


def verify_response(response, case, modules):
    """Bind actual original-window prefixes, scores, source IDs and compact caps."""
    physical, context = modules["corpus"].physical_lines, modules["context"]
    require(all(response[name] == case["original"][name] for name in ("query", "scope", "match_status")),
            "Original query/scope/match status differs")
    rows = response["results"]
    require(len(rows) <= 8 and len({row["id"] for row in rows}) == len(rows), "Excerpt cap/identity differs")
    raw_scores = {item.id: score for item, score in case["pairs"]}
    for row in rows:
        item = case["inventory"].get(row["id"])
        require(item is not None and all(row[name] == getattr(item, name) for name in ("path", "symbol", "start_line"))
            and row["window_end_line"] == item.end_line and row["content_hash"] == item.content_hash
            and sha(item.text.encode()) == item.content_hash, "Original source window/hash differs")
        require(type(row["end_line"]) is int and item.start_line <= row["end_line"] <= item.end_line,
                "Delivered source interval differs")
        literal = "".join(physical(item.text)[:row["end_line"] - item.start_line + 1])
        require(row["source"] == literal and sha(literal.encode()) == row["source_hash"]
            and len(physical(literal)) == row["end_line"] - item.start_line + 1
            and row["truncated"] == (literal != item.text), "Delivered source/hash/truncation differs")
        if row["origin"] == "retrieval":
            require(row["related_to"] is None and row["id"] in raw_scores
                    and row["retrieval_score"] == raw_scores[row["id"]], "Original retrieval scalar differs")
        else:
            parent = row["related_to"]
            require(row["origin"] == "dependency" and parent in raw_scores and parent in case["inventory"]
                and row["retrieval_score"] == raw_scores[parent]
                and row["id"] in {value.id for value in case["graph"].dependencies(case["inventory"][parent], limit=2)},
                "Original static dependency/source owner scalar differs")
        require(row.get("h2o_score") is None and row.get("qwen_score") is None and "assembly" not in row,
                "Replay introduced a new score/composite")
    require(response["total_source_chars"] == sum(len(row["source"]) for row in rows) <= 12000,
            "Character count/cap differs")
    text = context.serialize_context(context.compact_result(response))
    usage = response["token_usage"]
    require(usage["encoding"] == "cl100k_base" and usage["budget"] == 4096
        and len(context.encoding().encode(text, disallowed_special=())) == usage["tokens"] <= 4096,
        "Exact compact token count/cap differs")
    return text


def control_barrier(cases, controls, modules):
    actual, receipts = {}, []
    for case in cases:
        key = inputs.case_key(case)
        for name in CONTROLS:
            value = evaluate_control(case, name, modules)
            saved = controls[name, key]
            text = verify_response(value, case, modules)
            require(inputs.source_view(value) == saved["response"]
                and sha(text.encode()) == saved["reference"]["compact_sha256"],
                "Exact original control differs: " + name + "/" + str(key))
            actual[name, key] = value
            receipts.append({**identity(case), "selector": name,
                "source_view_sha256": sha(canonical(inputs.source_view(value))),
                "compact_sha256": sha(text.encode()), "reference": saved["reference"]})
    return actual, receipts


def evaluate_candidate(case, modules, reference_response):
    candidate = importlib.import_module("semantic_search.complementary_context")
    planner, guard, coalesce = candidate.complementary_plan, candidate.guard_complementary_plan, candidate.coalesce_context
    plans, guards, references, coalesces = [], [], [], []
    def capture_plan(*args, **kwargs):
        value = planner(*args, **kwargs)
        plans.append(deepcopy(value[3]))
        return value
    def capture_guard(reference, proposal, plan):
        value = guard(reference, proposal, plan)
        guards.append((deepcopy(reference), deepcopy(proposal), deepcopy(plan), deepcopy(value), reference is proposal))
        return value
    def capture_coalesce(*args, **kwargs):
        value = coalesce(*args, **kwargs)
        coalesces.append(deepcopy(value))
        return value
    with patch.object(candidate, "complementary_plan", capture_plan):
        selected, eligible, origins = candidate.complementary_context(case["pairs"],
            query=case["original"]["query"], max_results=8, include_dependencies=True,
            references=case["graph"], definition_keys=case["definition_keys"],
            ambiguous_symbols=case["ambiguous_symbols"])
    require(len(plans) == 1 and plans[0] == eligible.source_plan, "One actual matching planner receipt required")
    selection_plan = eligible.source_plan
    physical, context = modules["corpus"].physical_lines, modules["context"]
    before = windows.pack(ordered_template(case), [(item, score, *origins.get(item.id, ("retrieval", None)))
        for item, score in eligible], context, physical)
    candidates = windows.full_rows(case, selected, physical)
    ranked = windows.full_rows(case, case["pairs"], physical)
    def factory():
        # This is an actual copy of the independently reproduced same-input
        # coverage control; CPU replay makes no native packing-latency claim.
        value = deepcopy(reference_response)
        references.append(deepcopy(value))
        return value
    with patch.object(candidate, "guard_complementary_plan", capture_guard), \
            patch.object(candidate, "coalesce_context", capture_coalesce):
        response = candidate.guard_complementary_context(deepcopy(before), deepcopy(candidates),
            selection_plan=deepcopy(selection_plan), reference_factory=factory,
            ranked_candidates=deepcopy(ranked), definition_keys=case["definition_keys"],
            max_results=8, source_budget=12000, token_budget=4096)
    require(len(guards) == len(coalesces) == 1 and len(references) == int(selection_plan["accepted"]),
            "Actual guard/coalesce/factory counts differ")
    reference, proposal, actual_plan, (chosen, receipt), self_reference = guards[0]
    require(actual_plan == selection_plan and chosen == response and proposal == coalesces[0]
        and self_reference == (not selection_plan["accepted"]), "Actual guard operands/branch differ")
    require(reference == (references[0] if references else proposal), "Actual source reference differs")
    for value in (before, proposal, reference):
        verify_response(value, case, modules)
    text = verify_response(response, case, modules)
    return {"stages": {"ranked": [{"snippet": asdict(item), "score": score} for item, score in case["pairs"]],
        "selected": [{"snippet": asdict(item), "score": score} for item, score in selected],
        "eligible": [{"snippet": asdict(item), "score": score} for item, score in eligible], "origins": origins,
        "definition_keys": deepcopy(case["definition_keys"]),
        "ambiguous_symbols": sorted(case["ambiguous_symbols"])},
        "selection_plan": selection_plan, "complementary_plan_receipt": plans[0],
        "pre_postprocess_response": before, "backfill_candidates": candidates, "ranked_candidates": ranked,
        "reference_response": reference, "proposal_response": proposal, "complementary_guard_receipt": receipt,
        "planner_calls": len(plans), "guard_calls": len(guards), "coalesce_calls": len(coalesces),
        "reference_calls": len(references), "self_reference_identity": self_reference,
        "response": response, "compact_payload": text, "compact_sha256": sha(text.encode()),
        "actual_compact_tokens": response["token_usage"]["tokens"]}


def evidence_ranks(rows, groups, qualified=False):
    physical = importlib.import_module("semantic_search.source").physical_lines
    ranks = []
    for alternatives in groups:
        rank = None
        for position, row in enumerate(rows, 1):
            for target in alternatives:
                low, high = target["evidence_lines"]
                if row["path"] != target["path"] or not row["start_line"] <= low <= high <= row["end_line"]:
                    continue
                if qualified:
                    literal = "".join(physical(row["source"])[low-row["start_line"]:high-row["start_line"]+1])
                    if row["symbol"] != target["symbol"] or sha(literal.encode()) != target["evidence_sha256"]:
                        continue
                rank = position
                break
            if rank is not None:
                break
        ranks.append(rank)
    return ranks


def score_response(case, response):
    ranks = evidence_ranks(response["results"], case["groups"], case["qualified"])
    positive = case["kind"] == "present"
    return {"evidence_ranks": ranks, "complete": positive and bool(ranks) and None not in ranks,
            "covered_groups": sum(rank is not None for rank in ranks),
            "single_file_first_hit": positive and not case["multi_file"] and bool(ranks) and ranks[0] is not None}


def reported_row(case, result, controls):
    key = inputs.case_key(case)
    score = score_response(case, result["response"])
    comparisons = {}
    for name in CONTROLS:
        old = score_response(case, controls[name, key]["response"])
        comparisons[name] = {"control_ranks": old["evidence_ranks"], "variant_ranks": score["evidence_ranks"],
            "group_gains": [index + 1 for index, (a, b) in enumerate(zip(old["evidence_ranks"], score["evidence_ranks"])) if a is None and b is not None],
            "group_losses": [index + 1 for index, (a, b) in enumerate(zip(old["evidence_ranks"], score["evidence_ranks"])) if a is not None and b is None],
            "complete_gain": score["complete"] and not old["complete"],
            "complete_loss": old["complete"] and not score["complete"],
            "single_file_first_hit_loss": old["single_file_first_hit"] and not score["single_file_first_hit"]}
    return {**identity(case), "selector": MODE, "kind": case["kind"], "multi_file": case["multi_file"],
            "qualified": case["qualified"], "evidence_groups": case["groups"], **result, **score,
            "comparisons": comparisons,
            "control_references": {name: controls[name, key]["reference"] for name in CONTROLS}}


def summarize(outcomes, controls, cases):
    changes, metrics = {}, {name: {} for name in (*CONTROLS, MODE)}
    for name in CONTROLS:
        changes[name] = {field: [] for field in ("group_gains", "group_losses", "complete_gains", "complete_losses", "single_file_first_hit_losses")}
    gained, gate_comparisons = set(), {}
    for row in outcomes:
        for name in CONTROLS:
            difference = row["comparisons"][name]
            for field in ("group_gains", "group_losses"):
                if difference[field]:
                    changes[name][field].append({**identity(row), "groups": difference[field]})
            for flag, field in (("complete_gain", "complete_gains"), ("complete_loss", "complete_losses"),
                                ("single_file_first_hit_loss", "single_file_first_hit_losses")):
                if difference[flag]:
                    changes[name][field].append(identity(row))
        if row["multi_file"] and row["comparisons"]["coverage_control"]["complete_gain"]:
            gained.add((row["dataset"], row["query_id"]))
    for name in CONTROLS:
        gate_comparisons[name] = {field: changes[name][field] for field in (
            "group_losses", "complete_losses", "single_file_first_hit_losses")}
        gate_comparisons[name]["complete_multi_gains"] = [identity(row) for row in outcomes
            if row["multi_file"] and row["comparisons"][name]["complete_gain"]]
    by_key = {inputs.case_key(row): row for row in outcomes}
    for case in cases:
        stratum = "/".join(case[name] for name in ("subset", "dataset", "condition"))
        for name in metrics:
            value = by_key[inputs.case_key(case)] if name == MODE else score_response(case, controls[name, inputs.case_key(case)]["response"])
            item = metrics[name].setdefault(stratum, Counter())
            item.update(rows=1, present=case["kind"] == "present", absent=case["kind"] == "absent",
                required_groups=len(case["groups"]), covered_groups=value["covered_groups"], complete=value["complete"],
                multi_file_complete=bool(case["multi_file"] and value["complete"]), single_file_first_hit=value["single_file_first_hit"])
    qualification = {"qualified": bool(gained) and not any(value[field] for value in gate_comparisons.values()
        for field in ("group_losses", "complete_losses", "single_file_first_hit_losses")),
        "against_controls": gate_comparisons,
        "additional_distinct_complete_multi_queries_vs_coverage": [list(key) for key in sorted(gained)],
        "fresh_native_required_if_qualified": True, "automatic_default_change": False}
    return {"study": MODE, "classification": "Observed-source CPU DEVELOPMENT; no native/model/retrieval work",
        "counts": inputs.counts(cases), "stored_new_candidate_rows": len(outcomes), "referenced_control_rows": len(controls),
        "all_changes": changes, "metrics_by_study_dataset_original_condition": metrics,
        "qualification": qualification, "calls": {name: 0 for name in ("model", "retrieval", "index", "network")},
        "activity": {"admitted_plans": sum(row["selection_plan"]["accepted"] for row in outcomes),
            "accepted_guards": sum(row["complementary_guard_receipt"]["accepted"] for row in outcomes),
            "factory_calls": sum(row["reference_calls"] for row in outcomes),
            "self_references": sum(row["self_reference_identity"] for row in outcomes)}, "automatic_default_change": False}


def input_manifest(cases, parents):
    result, snapshots = [], {}
    for case in cases:
        signature = (id(case["inventory"]), id(case["definition_keys"]), id(case["graph"]))
        if signature not in snapshots:
            snapshots[signature] = {
                "inventory_sha256": sha(canonical([asdict(item) for item in case["inventory"].values()])),
                "definition_keys_sha256": sha(canonical(case["definition_keys"])),
                "edges_sha256": sha(canonical([[list(key), [list(target) for target in values]] for key, values in case["graph"].edges.items()]))}
        result.append({**identity(case), "kind": case["kind"], "multi_file": case["multi_file"], "qualified": case["qualified"],
            "query_sha256": sha(case["original"]["query"].encode()), "groups_sha256": sha(canonical(case["groups"])),
            "raw_pairs_sha256": sha(canonical([{"id": item.id, "score": score} for item, score in case["pairs"]])),
            **snapshots[signature]})
    return {"schema": "complementary_inputs_v2", "parents": parents, "cases": result}


def reference_manifest(cases, controls):
    return [{**identity(case), "controls": {name: controls[name, inputs.case_key(case)]["reference"] for name in CONTROLS}}
            for case in cases]


def freeze(root, output):
    require(not output.exists(), "Use a new prospective freeze directory")
    output.mkdir(parents=True)
    try:
        runtime, rank_bytes = prior.runtime(root)
        with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
            cases, controls, negatives, parents = inputs.load(root)
            _, receipts = control_barrier(cases, controls, modules)
        documents = {"input-manifest.json": input_manifest(cases, parents),
            "control-references.json": reference_manifest(cases, controls), "negative-references.json": negatives,
            "control-parity.json": {"exact_control_rows": len(receipts), "new_variant_outcomes_computed": 0, "cases": receipts}}
        for name, value in documents.items():
            write_new(output / name, value)
        input_bindings = {(output / name).relative_to(root).as_posix(): file_sha(output / name) for name in documents}
        for parent in parents.values():
            input_bindings.update(parent["artifacts"])
        plan = {"version": 2, "study": MODE, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "conditions": [*CONTROLS, MODE], "settings": SETTINGS, "counts": inputs.counts(cases), "runtime": runtime,
            "producer_sha256": own_bindings(root), "input_sha256": input_bindings,
            "artifacts": {name: file_sha(output / name) for name in documents},
            "historical_parents": parents, "barriers": {"exact_control_rows": len(receipts), "new_variant_outcomes_computed": 0,
                "referenced_negative_rows": len(negatives)},
            "qualification": "Zero individual group/complete/single-first-hit loss versus BOTH controls, plus >=1 new DISTINCT complete MULTI-FILE dataset/query unit versus coverage. No automatic default change.",
            "exposure": "All393 cases/201 units are observed development; no reserved attrs source/query/gold read."}
        write_new(output / "frozen-plan.json", plan)
        return {"study": MODE, **plan["counts"], **plan["barriers"]}
    except BaseException as error:
        write_new(output / "freeze-failure.json", {"error_type": type(error).__name__, "error": str(error), "new_variant_outcomes_computed": 0})
        raise


def bound_files(root, output, plan):
    bound = dict(plan["producer_sha256"])
    for parent in plan["historical_parents"].values():
        bound.update(parent["artifacts"])
    bound.update({(output / name).relative_to(root).as_posix(): file_sha(output / name) for name in FROZEN_FILES})
    return bound


def validate_run(root, output, plan, started_at):
    require(plan["study"] == MODE and plan["conditions"] == [*CONTROLS, MODE] and plan["settings"] == SETTINGS,
            "Undeclared experiment settings")
    require(plan["producer_sha256"] == own_bindings(root), "Prospective source/tests/protocol/auditor changed")
    require(not any((output / name).exists() for name in ATTEMPT_FILES), "Never overwrite or resume an attempted run")
    for name, digest in plan["artifacts"].items():
        require(file_sha(output / name) == digest, "Prospective manifest changed: " + name)
    bound = bound_files(root, output, plan)
    for name, digest in bound.items():
        require(file_sha(root / name) == inputs.git_sha(root, "HEAD", name) == digest, "Commit unchanged frozen bytes: " + name)
    path = (output / "frozen-plan.json").relative_to(root).as_posix()
    commit = subprocess.check_output(["git", "-C", str(root), "log", "-1", "--format=%H", "--", path], text=True).strip()
    require(bool(commit), "Prospective plan must be committed")
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"])
    stamp = int(subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%ct", commit], text=True))
    require(stamp <= datetime.fromisoformat(started_at).timestamp(), "Prospective freeze follows execution")
    for name, digest in bound.items():
        require(inputs.git_sha(root, commit, name) == digest, "Prospective freeze blob changed: " + name)
    return {"commit": commit, "committed_at_unix": stamp, "bound_blob_count": len(bound)}


def stream_outcomes(cases, controls, actual_controls, modules, output, state):
    outcomes = []
    with (output / "replay.jsonl.gz").open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            for case in cases:
                state.update(current_case=identity(case), attempted_new_variant_rows=state["attempted_new_variant_rows"] + 1)
                result = evaluate_candidate(case, modules, actual_controls["coverage_control", inputs.case_key(case)])
                row = reported_row(case, result, controls)
                zipped.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode() + b"\n")
                zipped.flush()
                raw.flush()
                outcomes.append(row)
                state["new_variant_rows"] += 1
    return outcomes


def run(root, output):
    plan, started = read(output / "frozen-plan.json"), datetime.now(timezone.utc).isoformat()
    chain = validate_run(root, output, plan, started)
    write_new(output / "run-start.json", {"created_at_utc": started, "pid": os.getpid(), "prospective_freeze": chain,
        "execution_commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        "plan_sha256": file_sha(output / "frozen-plan.json"), "resume_allowed": False,
        "calls": {name: 0 for name in ("model", "retrieval", "index", "network")}})
    state = {"stage": "inputs", "exact_control_rows": 0, "attempted_new_variant_rows": 0, "new_variant_rows": 0}
    try:
        runtime, rank_bytes = prior.runtime(root)
        require(runtime == plan["runtime"], "Pinned offline runtime changed")
        with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
            cases, controls, negatives, parents = inputs.load(root)
            require(input_manifest(cases, parents) == read(output / "input-manifest.json")
                and reference_manifest(cases, controls) == read(output / "control-references.json")
                and negatives == read(output / "negative-references.json") and inputs.counts(cases) == plan["counts"],
                "Frozen observed input/reference matrix changed")
            state["stage"] = "all786_control_parity"
            actual, receipts = control_barrier(cases, controls, modules)
            require(read(output / "control-parity.json") == {"exact_control_rows": len(receipts),
                "new_variant_outcomes_computed": 0, "cases": receipts}, "Control parity changed since freeze")
            state["exact_control_rows"] = len(receipts)
            write_new(output / "control-gate.json", {"exact_control_rows": len(receipts), "new_variant_rows_before_gate": 0,
                "referenced_negative_rows": len(negatives), "cases": receipts, "plan_sha256": file_sha(output / "frozen-plan.json")})
            state["stage"] = "new_candidate_rows"
            outcomes = stream_outcomes(cases, controls, actual, modules, output, state)
            summary = summarize(outcomes, controls, cases)
            summary.update(referenced_negative_rows=len(negatives), exact_control_rows=len(receipts),
                plan_sha256=file_sha(output / "frozen-plan.json"), replay_sha256=file_sha(output / "replay.jsonl.gz"))
        write_new(output / "summary.json", summary)
        with (output / "findings.md").open("x", encoding="utf-8") as stream:
            stream.write("# Complementary context v2: observed development replay\n\n"
                + "Qualification:\n\n```json\n" + json.dumps(summary["qualification"], indent=2) + "\n```\n\n"
                + "All individual changes and strata are retained in summary.json and the streamed ledger. "
                + "Historical negative arms are immutable references, never newly rerun outcomes. "
                + "No native latency, fresh generalization, coding correctness or provider-token saving is claimed.\n")
        return {"study": MODE, "stored_new_candidate_rows": len(outcomes), "qualification": summary["qualification"]}
    except BaseException as error:
        write_new(output / "failure.json", {"error_type": type(error).__name__, "error": str(error), "progress": state, "resume_allowed": False})
        raise


def controls_only(root, output):
    require(not output.exists(), "Use a new control-only output directory")
    output.mkdir(parents=True)
    runtime, rank_bytes = prior.runtime(root)
    with prior.offline_cpu_guard(), prior.cpu_modules(root, rank_bytes) as modules:
        cases, controls, negatives, parents = inputs.load(root)
        _, receipts = control_barrier(cases, controls, modules)
    result = {"classification": "CONTROL ONLY; no candidate planner/guard execution", "runtime": runtime,
        **inputs.counts(cases), "exact_control_rows": len(receipts), "referenced_negative_rows": len(negatives),
        "new_variant_outcomes_computed": 0, "cases": receipts,
        "input_manifest_sha256": sha(canonical(input_manifest(cases, parents)))}
    write_new(output / "control-preview.json", result)
    return {name: result[name] for name in ("observed_rows", "distinct_dataset_query_units", "exact_control_rows", "new_variant_outcomes_computed")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    for flag in ("freeze", "run", "controls-only"):
        mode.add_argument("--" + flag, action="store_true")
    args = parser.parse_args()
    operation = freeze if args.freeze else run if args.run else controls_only
    print(json.dumps(operation(args.root.resolve(), args.output.resolve())))


if __name__ == "__main__":
    main()
