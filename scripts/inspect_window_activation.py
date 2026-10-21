#!/usr/bin/env python3
"""Verify the separately published post hoc window-activation counts from saved JSON."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def positions(windows, group):
    return [number for number, window in enumerate(windows, 1)
            if any(window["path"] == target["path"]
                   and window["start_line"] <= target["evidence_lines"][0]
                   and window["end_line"] >= target["evidence_lines"][1] for target in group)]


def addresses(response):
    return {(row["path"], row["start_line"] + number): line
            for row in response["results"] for number, line in enumerate(
                line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", row["source"]) if line)}


def verify(study):
    report = json.loads((study / "activation-analysis.json").read_bytes())
    for name, expected in report["input_sha256"].items():
        require(digest(study / name) == expected, "Bound input changed: " + name)
    require(report["analysis_script_sha256"] == digest(Path(__file__)), "Analysis verifier changed")
    require(report["activation_analysis_md_sha256"] == digest(study / "activation-analysis.md"), "Analysis prose changed")
    audit = json.loads((study / "independent-audit.json").read_bytes())
    require(audit["checks_passed"] and audit["artifact_sha256"]["replay.jsonl.gz"] == digest(study / "replay.jsonl.gz"),
            "Successful independent raw-outcome audit required")
    metrics, units, causes, allocation, controls = defaultdict(Counter), defaultdict(set), Counter(), Counter(), Counter()
    candidates = ("connected_union", "guarded_continuation", "guarded_connected_union")
    outcomes, query_units, accepted, extensions, missing = 0, set(), 0, 0, 0
    with gzip.open(study / "replay.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            name, metric = row["selector"], metrics[row["selector"]]
            unit = row["dataset"], row["query_id"]
            outcomes += 1
            query_units.add(unit)
            metric["case_rows"] += 1
            for stage in ("selected", "eligible"):
                count = sum("assembly" in value["snippet"] for value in row["stages"][stage])
                metric[stage + "_composites"] += count
                if count:
                    metric[stage + "_active_case_rows"] += 1
                    units[(name, stage)].add(unit)
            for stage in ("pre_postprocess_response", "response"):
                count = sum("assembly" in value for value in row[stage]["results"])
                metric[stage + "_composites"] += count
                if count:
                    metric[stage + "_active_case_rows"] += 1
                    units[(name, stage)].add(unit)
            for control, change in row["comparisons"].items():
                metric[control + "_gained_group_rows"] += len(change["group_gains"])
                metric[control + "_lost_group_rows"] += len(change["group_losses"])
                metric[control + "_complete_gain_rows"] += change["complete_gain"]
                metric[control + "_complete_loss_rows"] += change["complete_loss"]
            if name == "coverage_control":
                controls["case_rows"] += 1
                controls["positive_case_rows"] += row["kind"] == "present"
                controls["complete_positive_case_rows"] += row["kind"] == "present" and None not in row["evidence_ranks"]
                controls["required_group_rows"] += len(row["evidence_groups"])
                controls["covered_group_rows"] += sum(rank is not None for rank in row["evidence_ranks"])
                for group, rank in zip(row["evidence_groups"], row["evidence_ranks"]):
                    if rank is not None:
                        continue
                    raw, selected, eligible = [positions([item["snippet"] for item in row["stages"][stage]], group)
                                               for stage in ("ranked", "selected", "eligible")]
                    packed = positions(row["pre_postprocess_response"]["results"], group)
                    cause = ("absent_from_replay_input_pool" if not raw else "representative_window_dedup" if not selected
                             else "selected_not_eligible" if not eligible else "packing_budget" if not packed else "postprocessing")
                    causes[cause] += 1
                    missing += 1
                    if cause == "selected_not_eligible":
                        allocation["selected_rank_above8" if min(selected) > 8 else "rank6_to8_dependency_allocation"] += 1
            elif name == "guarded_connected_union":
                guard = row["response"]["_window_guard"]
                metric["guard_accept_case_rows"] += guard["accepted"]
                metric["guard_fallback_case_rows"] += not guard["accepted"]
                if guard["proposed_composites"]:
                    require(guard["proposed_composites"] == 1, "Nontrivial denominator changed")
                    metric["nontrivial_proposal_case_rows"] += 1
                    metric["nontrivial_proposal_composites"] += guard["proposed_composites"]
                    metric["accepted_nontrivial_case_rows"] += guard["accepted"]
                    metric["rejected_nontrivial_case_rows"] += not guard["accepted"]
                    units[(name, "nontrivial_proposal")].add(unit)
                for value in row["response"]["results"]:
                    if "assembly" not in value:
                        continue
                    reference, actual = addresses(row["reference_response"]), addresses(row["response"])
                    require(reference == actual, "Accepted composite added or lost physical source")
                    accepted += 1
            elif name == "guarded_continuation":
                continuation = row["response"]["_window_assembly"]
                metric["extension_trials"] += continuation["trials"]
                metric["extension_applied"] += len(continuation["applied"])
                metric["extension_added_line_occurrences"] += sum(item["added_lines"] for item in continuation["applied"])
                metric.update({"rejected_" + key: value for key, value in Counter(item["reason"] for item in continuation["rejected"]).items()})
                for item in continuation["applied"]:
                    require(item["anchor_id"] == item["companion_id"], "Continuation used a different original window")
                    extensions += 1
    actual = {name: {"counts": dict(metric), "distinct_query_units_by_stage": {
        stage: len(units[(name, stage)]) for stage in ("selected", "eligible", "pre_postprocess_response", "response", "nontrivial_proposal")}}
        for name, metric in metrics.items()}
    require(actual == report["activation"], "Published activation counts differ from raw stages")
    require(dict(controls) == report["denominators"]["coverage_control"], "Published control denominator differs")
    require(dict(causes) == report["coverage_control_missing_group_causes"]
            and dict(allocation) == report["selected_not_eligible_detail"], "Published stage-cause counts differ")
    require(outcomes == 1825 and len(query_units) == 187 and controls["case_rows"] == 365
            and missing == len(report["all_missing_group_stage_diagnoses"]) == 79
            and accepted == len(report["all_accepted_guarded_composites"]) == 7
            and extensions == len(report["all_forward_continuation_applications"]) == 4,
            "Published case/query/application denominators differ")
    require(all(metrics[name]["coverage_control_gained_group_rows"] == 0 for name in candidates), "New evidence gain appeared")
    return {"checks_passed": True, "verified_outcomes": outcomes, "case_rows": 365, "distinct_query_units": 187,
            "missing_group_causes": dict(causes), "allocation_detail": dict(allocation),
            "analysis_sha256": digest(study / "activation-analysis.json"), "model_calls": 0,
            "retrieval_calls": 0, "network_calls": 0, "new_variant_evaluations": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    print(json.dumps(verify(parser.parse_args().study.resolve()), sort_keys=True))
