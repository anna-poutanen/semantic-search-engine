#!/usr/bin/env python3
"""Select frozen Qwen grid/development outcomes from certified actual raw searches.

This CPU-only selector imports no producer, model, embedding or retrieval module.
Quality uses unique first-pass cases; latency uses every actual repeated sample.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
OLD_STUDY = ROOT / "results/qwen-reranker-full-2026-10-13"
FAMILIES = ("dense", "hybrid")
DEPTHS = (5, 8, 10, 16, 24, 32, 40, 50)
BATCHES = (1, 2, 4, 8)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_hash(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def published_path(path):
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def configuration(value):
    require(isinstance(value, dict) and set(value) == {"id", "retrieval", "shortlist", "batch_size"},
            "Exact configuration fields required")
    family, k, b = value["retrieval"], value["shortlist"], value["batch_size"]
    require(family in FAMILIES and type(k) is int and k in DEPTHS and type(b) is int and b in (0,) + BATCHES,
            "Unsupported configuration")
    expected = family if b == 0 else f"{family}_k{k}_b{b}"
    require(value["id"] == expected and (b or k == 50), "Configuration ID or baseline depth differs")
    return value


def source_queries(plan):
    queries = {}
    for source in plan["sources"]:
        for query in source["queries"]:
            key = (source["id"], query["query_id"])
            require(key not in queries, "Duplicate frozen query identity")
            groups = query["groups"]
            require(query["kind"] in ("present", "absent") and isinstance(groups, list), "Invalid frozen gold")
            require(bool(groups) == (query["kind"] == "present") and all(groups), "Frozen required groups disagree with kind")
            paths = {target["path"] for group in groups for target in group}
            queries[key] = {"kind": query["kind"], "groups": groups,
                            "multi_file": len(groups) > 1 and len(paths) > 1}
    return queries


def distribution(values):
    require(values, "Actual latency observations required")
    for value in values:
        require(type(value) in (int, float) and math.isfinite(value) and value > 0,
                "Every actual warm latency must be a positive finite scalar")
    ordered = sorted(values)
    return {"samples": len(values), "median_s": statistics.median(values),
            "p95_s": ordered[math.ceil(.95 * len(values)) - 1], "min_s": ordered[0], "max_s": ordered[-1]}


def metric(rows):
    first = [row for row in rows if row["repetition"] == 1]
    require(len({(row["dataset"], row["query_id"]) for row in first}) == len(first), "Unique first-pass quality only")
    present = [row for row in first if row["kind"] == "present"]
    absent = [row for row in first if row["kind"] == "absent"]
    multi = [row for row in present if row["multi_file"]]
    complete = [row for row in present if None not in row["evidence_ranks"]]
    return {"unique_queries": len(first), "present": len(present), "absent": len(absent),
            "complete": len(complete), "covered_groups": sum(sum(rank is not None for rank in row["evidence_ranks"]) for row in present),
            "required_groups": sum(len(row["evidence_ranks"]) for row in present),
            "multi_file_queries": len(multi), "multi_file_complete": sum(None not in row["evidence_ranks"] for row in multi),
            "absent_statuses": dict(Counter(row["match_status"] for row in absent)),
            "complete_case_ids": sorted(row["dataset"] + "/" + row["query_id"] for row in complete),
            "warm_latency": distribution([row["elapsed_s"] for row in rows])}


def load_certified(study, *, old=False):
    study = Path(study).resolve(strict=True)
    audit_path, plan_path = study / "independent-audit.json", study / "frozen-plan.json"
    audit, plan = read(audit_path), read(plan_path)
    require(audit.get("checks_passed") is True, "Independent audit must certify this actual study")
    hashes = audit.get("artifact_sha256")
    require(isinstance(hashes, dict) and all(name in hashes for name in (
        "frozen-plan.json", "searches.jsonl.gz", "completion.json", "supervisor.json")), "Audit must bind actual raw study artifacts")
    for name, expected in hashes.items():
        require(isinstance(name, str) and Path(name).name == name and not Path(name).is_absolute(),
                "Audit artifacts must be immediate study files")
        require(file_hash(study / name) == expected, "Certified artifact hash changed: " + name)
    require(audit["frozen_plan_sha256"] == hashes["frozen-plan.json"], "Audit frozen-plan binding differs")
    require(type(plan["repetitions"]) is int and plan["repetitions"] == 3
            and type(plan["primary_quality_repetition"]) is int and plan["primary_quality_repetition"] == 1,
            "Exactly three actual repetitions; quality only first pass")
    require(old or audit.get("stage") == plan.get("stage") in ("grid", "development"), "Selection stage must be certified grid or development")
    if old:
        require(plan["conditions"] == ["dense", "dense_qwen", "hybrid", "hybrid_qwen"], "Original full-study conditions changed")
        configs = {name: {"id": name, "retrieval": name.split("_")[0], "shortlist": 50,
                          "batch_size": 1 if name.endswith("_qwen") else 0} for name in plan["conditions"]}
    else:
        configs = {configuration(value)["id"]: value for value in plan["conditions"]}
        require(len(configs) == len(plan["conditions"]), "Duplicate configurations")
        require(all(family in configs for family in FAMILIES), "Both actual no-reranker baselines required")
    queries = source_queries(plan)
    catalog = plan["query_catalog"]
    catalog_keys = [(query["dataset"], query["query_id"]) for query in catalog]
    texts = {key: value["query"] for key, value in zip(catalog_keys, catalog, strict=True)}
    require(len(set(catalog_keys)) == len(catalog) and set(catalog_keys) <= set(queries), "Unique source-bound catalog required")
    expected = {(dataset, query_id, condition, repetition) for dataset, query_id in catalog_keys
                for condition in configs for repetition in range(1, 4)}
    schedule = plan["schedule"]
    require(all(set(identity) == {"dataset", "query_id", "condition", "repetition"} for identity in schedule), "Exact scheduled identity fields")
    scheduled = [(item["dataset"], item["query_id"], item["condition"], item["repetition"]) for item in schedule]
    require(len(schedule) == len(expected) == plan["planned_requests"] and len(set(scheduled)) == len(scheduled)
            and set(scheduled) == expected, "Complete exact repeated candidate/query/condition schedule required")
    rows = []
    with gzip.open(study / "searches.jsonl.gz", "rt", encoding="utf-8") as stream:
        for index, line in enumerate(stream):
            row = json.loads(line)
            require(index < len(schedule) and row["identity"] == schedule[index], "Actual raw rows must exactly follow frozen schedule")
            require(row.get("status") == "completed" and row.get("active_phase") == "complete", "Every raw attempt must complete")
            identity = row["identity"]
            dataset, query_id, condition, repetition = [identity[key] for key in ("dataset", "query_id", "condition", "repetition")]
            require(type(repetition) is int, "Repetition must be an actual integer")
            q = queries[(dataset, query_id)]
            require(row["query"] == texts[(dataset, query_id)] and row["kind"] == q["kind"], "Raw query text/kind differ from frozen source")
            require(old or row["configuration"] == configs[condition], "Raw configuration differs from frozen condition")
            ranks = row["evidence_ranks"]
            require(type(row["required_groups"]) is int and isinstance(ranks, list)
                    and len(ranks) == row["required_groups"] == len(q["groups"]), "Raw delivered ranks must cover exact required groups")
            require(all(rank is None or type(rank) is int and rank >= 1 for rank in ranks), "Only positive integer delivered ranks or null")
            require(type(row["multi_file_and"]) is bool and row["multi_file_and"] == q["multi_file"], "Raw multi-file classification differs")
            elapsed = row["elapsed_warm_pipeline_s"]
            distribution([elapsed])
            rows.append({"dataset": dataset, "query_id": query_id, "condition": condition,
                         "repetition": repetition, "kind": q["kind"], "multi_file": q["multi_file"],
                         "evidence_ranks": ranks, "elapsed_s": elapsed, "match_status": row["response"]["match_status"]})
    require(len(rows) == len(schedule), "Missing actual repeated searches")
    completion, supervisor = read(study / "completion.json"), read(study / "supervisor.json")
    require(completion.get("complete") is True and completion.get("embedding_owner_closed") is True
            and completion["attempted_requests"] == completion["completed_requests"] == len(rows), "Actual completion/counters must reconcile")
    require(type(supervisor.get("main_exit_code")) is int and type(supervisor.get("resident_exit_code")) is int
            and supervisor.get("main_exit_code") == supervisor.get("resident_exit_code") == 0
            and supervisor.get("main_owned_group_stopped") is True and supervisor.get("resident_owned_group_stopped") is True
            and supervisor.get("forced_kill") is False and supervisor.get("timed_out") is False
            and supervisor.get("private_auth_token_removed") is True, "Study must have clean certified owner shutdown")
    metrics = {condition: metric([row for row in rows if row["condition"] == condition]) for condition in configs}
    if not old:
        require(audit["actual_stage_requests"] == len(rows), "Audit actual request count differs")
        for condition, values in metrics.items():
            certified = audit["metrics"][condition]["all"]
            require(all(certified[key] == values[key] for key in certified), "Raw recomputed quality/latency disagree with independent audit")
    return {"study": study, "plan": plan, "audit": audit, "rows": rows, "configs": configs,
            "metrics": metrics, "queries": queries, "binding": {"study": published_path(study),
                "audit_sha256": file_hash(audit_path), "frozen_plan_sha256": hashes["frozen-plan.json"],
                "searches_sha256": hashes["searches.jsonl.gz"]}}


def panel_catalog(old):
    chosen = set()
    for source in old["plan"]["sources"]:
        positive = [query for query in source["queries"] if query["kind"] == "present"]
        absent = [query for query in source["queries"] if query["kind"] == "absent"]
        require(len(positive) >= 2 and absent, "Original source needs first/last positive and first absent")
        chosen.update((source["id"], query["query_id"]) for query in (positive[0], positive[-1], absent[0]))
    result = [query for query in old["plan"]["query_catalog"] if (query["dataset"], query["query_id"]) in chosen]
    require(len(result) == 24, "Exact predeclared 24-query panel required")
    return result


def matched_rows(study, condition, keys):
    result = [row for row in study["rows"] if row["condition"] == condition and (row["dataset"], row["query_id"]) in keys]
    require(len(result) == len(keys) * 3, "Matched comparison must retain all three actual observations")
    return result


def support_changes(before, after, queries):
    left = {(row["dataset"], row["query_id"]): row for row in before if row["repetition"] == 1}
    right = {(row["dataset"], row["query_id"]): row for row in after if row["repetition"] == 1}
    require(left.keys() == right.keys(), "Whole-span changes require matched unique first-pass cases")
    losses, gains, complete_losses, complete_gains = [], [], [], []
    for key, original in left.items():
        changed = right[key]
        require(original["kind"] == changed["kind"], "Matched query kinds changed")
        if original["kind"] == "absent":
            continue
        a, b = original["evidence_ranks"], changed["evidence_ranks"]
        case_id = key[0] + "/" + key[1]
        if None not in a and None in b:
            complete_losses.append(case_id)
        if None in a and None not in b:
            complete_gains.append(case_id)
        for index, (previous, current) in enumerate(zip(a, b, strict=True)):
            item = {"dataset": key[0], "query_id": key[1], "group_index": index,
                    "before_rank": previous, "after_rank": current, "gold_group": queries[key]["groups"][index]}
            if previous is not None and current is None:
                losses.append(item)
            if previous is None and current is not None:
                gains.append(item)
    return {"complete_case_losses": sorted(complete_losses), "complete_case_gains": sorted(complete_gains),
            "covered_group_losses": losses, "covered_group_gains": gains,
            "net_covered_groups": len(gains) - len(losses)}


def assess(config, candidate_rows, reference_rows, baseline_rows, queries):
    values, reference, baseline = metric(candidate_rows), metric(reference_rows), metric(baseline_rows)
    lost_complete = sorted(set(reference["complete_case_ids"]) - set(values["complete_case_ids"]))
    reasons = []
    if lost_complete:
        reasons.append("Does not retain every K50 complete case")
    if values["multi_file_complete"] < reference["multi_file_complete"]:
        reasons.append("Multi-file complete count is below K50")
    if values["covered_groups"] < baseline["covered_groups"]:
        reasons.append("Covered-group count is below original no-reranker baseline")
    return {"configuration": config, "qualified": not reasons, "reasons": reasons, "metrics": values,
            "gate": {"reference_complete_cases": reference["complete_case_ids"], "lost_reference_complete_cases": lost_complete,
                     "reference_multi_file_complete": reference["multi_file_complete"],
                     "original_no_reranker_covered_groups": baseline["covered_groups"]},
            "changes_from_k50": support_changes(reference_rows, candidate_rows, queries)}


def timing_order(value):
    config, timing = value["configuration"], value["metrics"]["warm_latency"]
    return timing["p95_s"], timing["median_s"], config["shortlist"], config["batch_size"]


def development_order(value):
    config, values = value["configuration"], value["metrics"]
    timing = values["warm_latency"]
    return (-values["complete"], -values["covered_groups"], -values["multi_file_complete"],
            timing["p95_s"], timing["median_s"], FAMILIES.index(config["retrieval"]),
            config["shortlist"], config["batch_size"])


def select(study, old_study=OLD_STUDY, *, grid_decision=None):
    actual, old = load_certified(study), load_certified(old_study, old=True)
    stage = actual["plan"]["stage"]
    require(len(old["plan"]["query_catalog"]) == 144, "Original full 144-query development reference required")
    expected = panel_catalog(old) if stage == "grid" else old["plan"]["query_catalog"]
    require(actual["plan"]["query_catalog"] == expected, "Stage query catalog differs from predeclared panel/full development")
    for source in actual["plan"]["sources"]:
        reference = next((item for item in old["plan"]["sources"] if item["id"] == source["id"]), None)
        require(reference is not None and source["source_file_sha256"] == reference["source_file_sha256"]
                and source["window_inventory_sha256"] == reference["window_inventory_sha256"]
                and source["queries"] == reference["queries"], "Development frozen source/window/gold changed")
    require(set(actual["queries"]) == set(old["queries"]), "Full frozen source-query membership differs")
    keys = {(query["dataset"], query["query_id"]) for query in expected}
    result = {"version": 1, "stage": stage, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "selector_sha256": file_hash(Path(__file__)), "model_calls": 0, "embedding_calls": 0,
              "retrieval_calls": 0, "quality_repetition": 1, "actual_latency_repetitions": [1, 2, 3],
              "study_binding": actual["binding"], "old_reference_binding": old["binding"],
              "selection_rule": "Frozen amended complete-case + K50 multi-file + original no-reranker group gate",
              "metrics": actual["metrics"], "ranked_fallbacks": {}, "selected_by_family": {}, "selected_configs": [],
              "limits": ["Observed development tuning; HTTPX retrieval outcomes are never read by this selector.",
                         "Quality uses unique first-pass cases only; latency includes every actual repeated warm sample.",
                         "Partial support losses are published even when aggregate complete-case/group gates pass."]}
    allowed = None
    if grid_decision is not None:
        path = Path(grid_decision).resolve(strict=True)
        decision = read(path)
        require(stage == "development" and decision["stage"] == "grid", "Grid decision only binds development selection")
        require(decision["old_reference_binding"]["audit_sha256"] == old["binding"]["audit_sha256"], "Grid decision original reference differs")
        allowed = {configuration(item["configuration"])["id"] for values in decision["ranked_fallbacks"].values() for item in values}
        require(all(config["id"] in allowed for config in actual["configs"].values() if config["batch_size"]), "Development configuration absent from frozen ranked grid fallbacks")
        result["grid_decision_binding"] = {"path": published_path(path), "sha256": file_hash(path)}
    for family in FAMILIES:
        baseline_rows = matched_rows(old, family, keys)
        reference_rows = (matched_rows(actual, f"{family}_k50_b1", keys) if stage == "grid" else
                          matched_rows(old, family + "_qwen", keys))
        candidates = [assess(config, matched_rows(actual, identifier, keys), reference_rows, baseline_rows, actual["queries"])
                      for identifier, config in actual["configs"].items() if config["retrieval"] == family and config["batch_size"]]
        require(candidates, "At least one actual reranker configuration per family required")
        ranked = sorted(candidates, key=timing_order)
        result["ranked_fallbacks"][family] = ranked
        qualifying = [value for value in ranked if value["qualified"]]
        if stage == "grid":
            chosen = qualifying[0] if qualifying else next(value for value in ranked if value["configuration"]["id"] == family + "_k50_b1")
            chosen = chosen | {"default_k50_fallback": not bool(qualifying)}
        else:
            chosen = min(qualifying, key=development_order) if qualifying else None
        result["selected_by_family"][family] = chosen
        if chosen is not None:
            result["selected_configs"].append(chosen["configuration"])
    if stage == "grid":
        result["selection_order"] = ["lower_all_rep_p95", "lower_all_rep_median", "smaller_shortlist", "smaller_batch"]
        result["ready_for_development"] = True
    else:
        eligible = [value for value in result["selected_by_family"].values() if value is not None]
        best = min(eligible, key=development_order) if eligible else None
        result["selection_order"] = ["more_complete", "more_covered_groups", "more_multi_file_complete",
                                     "lower_all_rep_p95", "lower_all_rep_median", "dense_tie_break"]
        result["selected_config"] = best["configuration"] if best else None
        result["ready_for_fresh_validation"] = best is not None
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--old-study", type=Path, default=OLD_STUDY)
    parser.add_argument("--grid-decision", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = select(args.study, args.old_study, grid_decision=args.grid_decision)
    output = args.output or args.study / "decision.json"
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"stage": result["stage"], "selected_configs": result["selected_configs"],
                      "selected_config": result.get("selected_config"), "decision_sha256": file_hash(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
