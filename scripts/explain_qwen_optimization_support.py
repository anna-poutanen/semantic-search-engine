#!/usr/bin/env python3
"""Source-span support changes from certified completed Qwen optimization runs.

CPU-only receipt analysis. Imports no producer, retriever, embedder, tokenizer or
model runtime. First-pass quality and all-observation diagnostic timings remain
separate; explanations do not qualify a failed numerical batching gate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]
OLD_STUDY = ROOT / "results/qwen-reranker-full-2026-10-13"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def gz_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def physical_lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def path_label(path):
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def certified(study, ledger):
    study = Path(study).resolve(strict=True)
    report = read(study / "independent-audit.json")
    require(report.get("checks_passed") is True, "Completed independent audit required before explanation")
    hashes = report.get("artifact_sha256")
    require(isinstance(hashes, dict) and all(name in hashes for name in
            ("frozen-plan.json", ledger, "completion.json", "supervisor.json")), "Audit must bind actual completed raw artifacts")
    for name, expected in hashes.items():
        require(isinstance(name, str) and Path(name).name == name and not Path(name).is_absolute(),
                "Audit hashes must identify immediate study files")
        require(file_hash(study / name) == expected, "Certified artifact changed: " + name)
    require(report["frozen_plan_sha256"] == hashes["frozen-plan.json"], "Audit plan hash differs")
    completion = read(study / "completion.json")
    supervisor = read(study / "supervisor.json")
    require(completion.get("complete") is True and completion.get("embedding_owner_closed") is True,
            "Actual model owner completion must be certified")
    require(supervisor.get("main_exit_code") == supervisor.get("resident_exit_code") == 0
            and supervisor.get("main_owned_group_stopped") is True
            and supervisor.get("resident_owned_group_stopped") is True
            and supervisor.get("forced_kill") is False and supervisor.get("timed_out") is False
            and supervisor.get("private_auth_token_removed") is True, "Successful clean owned process shutdown required")
    plan = read(study / "frozen-plan.json")
    rows = gz_rows(study / ledger)
    require([row["identity"] for row in rows] == plan["schedule"] and all(row["status"] == "completed" for row in rows),
            "Exact completed frozen raw schedule required")
    return plan, rows, {"study": path_label(study), "audit_sha256": file_hash(study / "independent-audit.json"),
                        "frozen_plan_sha256": hashes["frozen-plan.json"], "ledger_sha256": hashes[ledger]}


def group_support(group, returned):
    covering = []
    for rank, window in enumerate(returned, 1):
        lines = physical_lines(window["source"])
        require(type(window["start_line"]) is int and type(window["end_line"]) is int
                and window["end_line"] - window["start_line"] + 1 == len(lines)
                and window["source_hash"] == sha(window["source"].encode()), "Delivered source/hash/bounds changed")
        matched = []
        for alternative, target in enumerate(group):
            lo, hi = target["evidence_lines"]
            if window["path"] == target["path"] and window["start_line"] <= lo <= hi <= window["end_line"]:
                raw = "".join(lines[lo - window["start_line"]:hi - window["start_line"] + 1])
                require(sha(raw.encode()) == target["evidence_sha256"], "Complete delivered target evidence hash changed")
                matched.append(alternative)
        if matched:
            covering.append({key: window[key] for key in ("id", "path", "symbol", "start_line", "end_line", "source_hash")} | {
                "delivered_rank": rank, "origin": window.get("origin", "retrieval"),
                "related_to": window.get("related_to"), "covering_alternative_indexes": matched})
    return {"targets": group, "covered": bool(covering),
            "delivered_rank": covering[0]["delivered_rank"] if covering else None,
            "covered_only_by_dependencies": bool(covering) and all(row["origin"] == "dependency" for row in covering),
            "covering_results": covering}


def study_cases(study):
    plan, rows, binding = certified(study, "searches.jsonl.gz")
    require(plan["repetitions"] == 3 and plan["primary_quality_repetition"] == 1, "Frozen first-pass quality only")
    gold = {(source["id"], query["query_id"]): query for source in plan["sources"] for query in source["queries"]}
    configs = ({name: {"id": name, "retrieval": name.split("_")[0], "shortlist": 50,
                       "batch_size": 1 if name.endswith("_qwen") else 0} for name in plan["conditions"]}
               if isinstance(plan["conditions"][0], str) else {row["id"]: row for row in plan["conditions"]})
    cases = {}
    for row in rows:
        identity = row["identity"]
        if identity["repetition"] != 1:
            continue
        key = identity["condition"], identity["dataset"], identity["query_id"]
        require(key not in cases, "Repeated first-pass case")
        query = gold[key[1:]]
        details = [group_support(group, row["response"]["results"]) for group in query["groups"]]
        require(row["evidence_ranks"] == [group["delivered_rank"] for group in details], "Raw full-span ranks differ from literal delivered evidence")
        cases[key] = {"dataset": key[1], "query_id": key[2], "query": row["query"], "kind": query["kind"],
                      "condition": key[0], "configuration": configs[key[0]], "groups": details,
                      "complete": all(group["covered"] for group in details) if details else None,
                      "match_status": row["response"]["match_status"],
                      "candidate_identity_source_sha256": row["candidate_identity_source_sha256"]}
    return {"plan": plan, "cases": cases, "configs": configs, "binding": binding}


def compare(before, after, *, label):
    require(before.keys() == after.keys(), "Support comparison must retain matched first-pass query IDs")
    cases = []
    for key in before:
        left, right = before[key], after[key]
        require(left["query"] == right["query"] and left["kind"] == right["kind"]
                and [group["targets"] for group in left["groups"]] == [group["targets"] for group in right["groups"]],
                "Comparison query or original evidence groups changed")
        groups = []
        for index, (a, b) in enumerate(zip(left["groups"], right["groups"], strict=True), 1):
            groups.append({"group": index, "coverage_gained": not a["covered"] and b["covered"],
                           "coverage_lost": a["covered"] and not b["covered"], "before": a, "after": b})
        cases.append({"dataset": key[0], "query_id": key[1], "query": left["query"], "kind": left["kind"],
                      "before_condition": left["condition"], "after_condition": right["condition"],
                      "before_complete": left["complete"], "after_complete": right["complete"],
                      "complete_gained": left["complete"] is False and right["complete"] is True,
                      "complete_lost": left["complete"] is True and right["complete"] is False,
                      "same_candidate_identity_source": left["candidate_identity_source_sha256"] == right["candidate_identity_source_sha256"],
                      "before_match_status": left["match_status"], "after_match_status": right["match_status"], "groups": groups})
    return {"label": label, "unique_queries": len(cases),
            "complete_gains": [row["dataset"] + "/" + row["query_id"] for row in cases if row["complete_gained"]],
            "complete_losses": [row["dataset"] + "/" + row["query_id"] for row in cases if row["complete_lost"]],
            "covered_group_gains": sum(group["coverage_gained"] for row in cases for group in row["groups"]),
            "covered_group_losses": sum(group["coverage_lost"] for row in cases for group in row["groups"]), "cases": cases}


def condition_cases(study, condition, keys=None):
    return {(dataset, query_id): row for (identifier, dataset, query_id), row in study["cases"].items()
            if identifier == condition and (keys is None or (dataset, query_id) in keys)}


def distribution(values):
    require(values and all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in values),
            "Actual finite diagnostic timing observations required")
    ordered = sorted(values)
    return {"samples": len(values), "median_s": statistics.median(values),
            "p95_s": ordered[math.ceil(.95 * len(values)) - 1], "min_s": ordered[0], "max_s": ordered[-1]}


def parity_timing(study):
    plan, rows, binding = certified(study, "parity-scores.jsonl.gz")
    require(plan["stage"] == "parity", "Completed parity diagnostic required")
    known_repositories = {source["id"] for source in plan["sources"]}
    real = {group["id"] for group in plan["parity_sets"] if len(group["pairs"]) == 50
            and group["id"].split("/", 1)[0] in known_repositories}
    require(len(real) == 8, "Eight fixed development sets with fifty real pairs each")
    selected = [row for row in rows if row["identity"]["parity_set"] in real]
    by_size = {}
    for size in (1, 2, 4, 8):
        samples = [row for row in selected if row["identity"]["batch_size"] == size]
        require(len(samples) == 8, "One actual observation per real set and batch configuration")
        by_size[str(size)] = {"real_sets": 8, "pairs_per_set": 50, "actual_logical_pairs": 400,
            "actual_native_forwards": sum(len(row["reranker"]["batch_receipts"]) for row in samples),
            "native_forward_s": distribution([row["reranker"]["forward_s"] for row in samples]),
            "resident_scoring_s": distribution([row["reranker"]["server_scoring_s"] for row in samples]),
            "helper_http_s": distribution([row["helper_http_s"] for row in samples])}
    return {"binding": binding, "classification": "Warm resident parity diagnostic on fixed development inputs",
            "fixed_condition_order": [1, 2, 4, 8], "by_batch_size": by_size,
            "limitations": ["One sample per fixed source set/configuration; B1 always precedes B2/B4/B8, so order and shape warming can affect durations.",
                            "No query embedding, retrieval or packing; this is not production pipeline, MCP or coding latency.",
                            "A faster batch is ineligible if its unchanged numerical gate fails; timings do not relax or replace that gate."]}


def explain(study_path, *, old_study=OLD_STUDY, decision_path=None, parity_study=None):
    actual = study_cases(study_path)
    stage = actual["plan"].get("stage")
    require(stage in ("grid", "development", "fresh"), "Optimization retrieval stage required")
    old = study_cases(old_study) if stage != "fresh" else None
    decision, decision_binding = None, None
    if decision_path is not None:
        decision_path = Path(decision_path).resolve(strict=True)
        decision = read(decision_path)
        decision_binding = {"path": path_label(decision_path), "sha256": file_hash(decision_path)}
        if stage == "fresh":
            require(decision.get("ready_for_fresh_validation") is True and decision.get("selected_config") is not None,
                    "Frozen development winner required for fresh comparison")
            require(decision_path.is_relative_to(ROOT)
                    and actual["plan"]["decision_inputs"].get(str(decision_path.relative_to(ROOT))) == decision_binding["sha256"],
                    "Fresh plan must bind the pre-retrieval selection decision")
            require(actual["configs"].get(decision["selected_config"]["id"]) == decision["selected_config"],
                    "Fresh selected condition must match frozen development winner")
    comparisons = []
    for identifier, configuration in actual["configs"].items():
        if not configuration["batch_size"]:
            continue
        candidate = condition_cases(actual, identifier)
        family = configuration["retrieval"]
        for reference in (family, family + "_k50_b1"):
            if reference in actual["configs"] and reference != identifier:
                comparisons.append(compare(condition_cases(actual, reference), candidate,
                                           label="same_stage/" + reference + "/" + identifier))
        if old is not None:
            for reference in (family, family + "_qwen"):
                comparisons.append(compare(condition_cases(old, reference, set(candidate)), candidate,
                                           label="old_full_reference/" + reference + "/" + identifier))
    if stage == "fresh" and decision is not None:
        selected = decision["selected_config"]["id"]
        candidate = condition_cases(actual, selected)
        present_labels = {row["label"] for row in comparisons}
        for reference in ("dense", "hybrid", "dense_k50_b1", "hybrid_k50_b1"):
            label = "same_stage/" + reference + "/" + selected
            if reference in actual["configs"] and reference != selected and label not in present_labels:
                comparisons.append(compare(condition_cases(actual, reference), candidate, label=label))
    return {"version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(), "stage": stage,
            "model_calls": 0, "embedding_calls": 0, "retrieval_calls": 0, "quality_repetition": 1,
            "generator_sha256": file_hash(Path(__file__)), "study_binding": actual["binding"],
            "old_reference_binding": old["binding"] if old else None, "decision_binding": decision_binding,
            "comparisons": comparisons, "parity_resident_timing": parity_timing(parity_study) if parity_study else None,
            "limitations": ["Every comparison uses matched original full evidence groups and unique first-pass cases; rank movement is separate from support gain/loss.",
                            "Dependencies may deliver evidence outside the scored prefix; complete candidate coverage is not a delivered-support ceiling.",
                            "No provider token savings, coding correctness, production latency or new numerical parity qualification is inferred."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--old-study", type=Path, default=OLD_STUDY)
    parser.add_argument("--decision", type=Path)
    parser.add_argument("--parity-study", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = explain(args.study, old_study=args.old_study, decision_path=args.decision, parity_study=args.parity_study)
    output = args.output or args.study / "support-explanations.json"
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"stage": result["stage"], "comparisons": len(result["comparisons"]), "sha256": file_hash(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
