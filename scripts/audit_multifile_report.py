#!/usr/bin/env python3
"""Reconcile saved multifile reporting/QA; never replay trials or runtime probes."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value, detail):
    if not value:
        raise ValueError(detail)


def audit_report(study, core_audit):
    records = read(study / "results.json")
    by = {record["run_id"]: record for record in records}
    report = read(study / "analysis.json")
    review = read(study / "patch-review.json")
    config = read(study / "frozen-config.json")
    core = read(core_audit)
    checks = {
        "core_inventory_audit_passed": core["integrity_passed"] and core["inventory_complete"],
        "core_audit_config_identity": core["frozen_config_sha256"] == digest(study / "frozen-config.json"),
        "analysis_results_hash": report["aggregate_results_sha256"] == digest(study / "results.json"),
        "analysis_config_hash": report["frozen_config_sha256"] == digest(study / "frozen-config.json"),
        "analysis_review_hash": report["qualitative_review_inventory"]["artifact_sha256"] == digest(study / "patch-review.json"),
        "analysis_generator_hash": report["report_generator_sha256"] == digest(ROOT / "scripts/report_multifile_workflows.py"),
        "review_complete_unique_inventory": review["status"] == "complete" and len(review["runs"]) == len(records) == len(config["schedule"])
            and len({row["run_id"] for row in review["runs"]}) == len(records)
            and {row["run_id"] for row in review["runs"]} == set(by),
    }
    require(len(by) == len(records), "Duplicate result identities")
    core_by = {row["run_id"]: row for row in core["runs"]}
    require(set(core_by) == set(by), "Core audit covers a different inventory")
    missing, capacity, outside, linked = [], [], [], {}
    file_change_items = file_change_paths = 0
    for record in records:
        identifier = record["run_id"]
        require(core_by[identifier]["result_sha256"] == digest(study / "runs" / identifier / "result.json"), "Core result proof is stale")
        raw = Path(config["work_root"]) / "raw" / identifier / "events.jsonl"
        events = [json.loads(line) for line in raw.read_text().splitlines() if line]
        receipts = [event for event in events if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict)]
        if record["usage"] is None:
            missing.append(identifier)
            require(not receipts and record["reported_turns"] == 0, "Missing usage contradicts an actual receipt")
        if record["failed"]:
            failures = [event for event in events if event.get("type") == "turn.failed"]
            require(failures and any("at capacity" in str(event.get("error", {})).lower() for event in failures), "Unclassified failed attempt")
            capacity.append(identifier)
        workspace = (Path(config["work_root"]) / "workspaces" / identifier).resolve()
        for event in events:
            item = event.get("item", {})
            if item.get("type") != "file_change":
                continue
            if event.get("type") == "item.completed":
                file_change_items += 1
                file_change_paths += len(item.get("changes", []))
            for change in item.get("changes", []):
                path = Path(change["path"])
                path = path if path.is_absolute() else workspace / path
                if not path.resolve().is_relative_to(workspace):
                    outside.append({"run_id": identifier, "path": str(path).replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")})
    passed = failed = 0
    for row in review["runs"]:
        record = by[row["run_id"]]
        require(all(row[key] == record[key] for key in ("task_id", "condition", "repetition")), "Review identity changed")
        require(row["patch_sha256"] == record["patch_sha256"] == digest(study / "runs" / record["run_id"] / "changes.patch"), "Review patch hash changed")
        require(row["frozen_correct"] == record["correct"] and row["frozen_completed_correct"] == record["completed_correct"], "Supplemental review changed formal grades")
        require(row["selected_check_detail"] == record["grade"]["detail"], "Selected review detail changed")
        path = study / row["test_checks"]["artifact"]
        test = read(path)
        linked[str(path.relative_to(study))] = digest(path)
        require(test["run_id"] == row["run_id"] and test["patch_sha256"] == row["patch_sha256"], "Authored-test proof identity changed")
        if test.get("candidate_new_tests"):
            require(test["candidate_new_tests"]["summary"] == row["test_checks"]["candidate"], "Authored-test summary changed")
            passed += test["candidate_new_tests"]["summary"]["passed"]
            failed += test["candidate_new_tests"]["summary"]["failed"]
        for issue in row["issues"]:
            path = study / issue["evidence"]
            require(path.is_file(), "Linked issue evidence is absent")
            linked[str(path.relative_to(study))] = digest(path)
    checks.update({
        "review_patch_identities_hashes_grades": True,
        "linked_qualitative_artifacts_exist": True,
        "no_observed_file_changes_outside_fixture": not outside,
        "missing_receipts_preserved_null": len(missing) == sum(row["provider_usage"] is None for row in core["runs"]),
        "capacity_failures_confirmed_by_turn_failed": len(capacity) == sum(row["status"] == "provider_capacity_failure" for row in report["runs"]),
        "authored_test_totals": passed == review["summary"]["candidate_added_tests_passed"] and failed == review["summary"]["candidate_added_tests_failed"],
    })
    confirmed = [(row, issue) for row in review["runs"] for issue in row["issues"] if issue["classification"] == "Confirmed supplemental contract defect"]
    checks["supplemental_defects_reported_separately"] = (
        len(confirmed) == review["summary"]["material_issues"]
        and len({row["run_id"] for row, _issue in confirmed}) == review["summary"]["attempts_with_confirmed_supplemental_defects"]
        and review["classification"].startswith("Post-output"))
    supplements = (
        ("supplemental-response-edges.json", "scripts/probe_response_limit_edges.py", "scripts/run_response_limit_edge_review.py"),
        ("supplemental-click-resources.json", "scripts/probe_click_atomic_resource_edges.py", "scripts/run_click_atomic_edge_review.py"),
    )
    for filename, probe, driver in supplements:
        data = read(study / filename)
        checks[filename + "_probe_hash"] = data["probe_sha256"] == digest(ROOT / probe)
        checks[filename + "_driver_hash"] = data["runner_sha256"] == digest(ROOT / driver)
        for row in data["records"]:
            if row["identity"] in by:
                require(row["patch_sha256"] == by[row["identity"]]["patch_sha256"], "Supplemental candidate patch changed")
        checks[filename + "_candidate_patch_hashes"] = True
        if "shared_runner_sha256" in data:
            checks["supplemental_click_shared_runner_hash"] = data["shared_runner_sha256"] == digest(ROOT / "scripts/run_response_limit_edge_review.py")
    expected_pairs = set()
    for record in records:
        if record["condition"] != "baseline":
            continue
        other = next(row for row in records if row["task_id"] == record["task_id"] and row["repetition"] == record["repetition"] and row["condition"] == "mcp")
        if (record["completed_correct"] and other["completed_correct"]
                and record["base_tree_sha256"] == other["base_tree_sha256"]
                and record["prompt_sha256"] == other["prompt_sha256"]):
            expected_pairs.add((record["task_id"], record["repetition"]))
    qualified = [pair for pair in report["pairs"] if pair["correctness_qualified"]]
    checks["correctness_qualified_pairs_reconciled"] = len(qualified) == len(expected_pairs) and {(pair["task_id"], pair["repetition"]) for pair in qualified} == expected_pairs
    baseline_tokens = mcp_tokens = baseline_seconds = mcp_seconds = 0
    for pair in qualified:
        subset = [row for row in records if row["task_id"] == pair["task_id"] and row["repetition"] == pair["repetition"]]
        baseline = next(row for row in subset if row["condition"] == "baseline")
        mcp = next(row for row in subset if row["condition"] == "mcp")
        baseline_tokens += baseline["usage"]["total_tokens"]
        mcp_tokens += mcp["usage"]["total_tokens"]
        baseline_seconds += baseline["agent_elapsed_s"]
        mcp_seconds += mcp["agent_elapsed_s"]
    token_change = 100 * (mcp_tokens / baseline_tokens - 1) if baseline_tokens else None
    time_change = 100 * (mcp_seconds / baseline_seconds - 1) if baseline_seconds else None
    def same(left, right):
        return left == right if left is None or right is None else math.isclose(left, right)
    metrics = report["correctness_qualified_pair_comparisons"]["metrics"]
    checks["qualified_paired_aggregate_arithmetic"] = same(metrics["total_tokens"]["measured_sums_change_pct"], token_change) and same(metrics["agent_cli"]["measured_sums_change_pct"], time_change)
    checks["missing_full_inventory_totals_unavailable"] = all(
        report["conditions"][condition]["usage"]["total_tokens"]["complete_total"] is None
        for condition in ("baseline", "mcp") if any(row["condition"] == condition and row["usage"] is None for row in records))
    artifact_names = ("analysis.json", "report.md", "results.json", "summary.json", "frozen-config.json", "patch-review.json",
                      "supplemental-response-edges.json", "supplemental-click-resources.json", "supplemental-probe-revisions.json")
    return {"classification": "Independent final report/qualitative identity and accounting reconciliation; no model calls",
            "checks_passed": all(checks.values()), "checks": checks,
            "proof_writer_sha256": digest(Path(__file__)), "core_inventory_audit_sha256": digest(core_audit),
            "runtime_probe_replayed": False, "missing_usage_run_ids": missing, "capacity_failure_run_ids": capacity,
            "qualified_pairs": [{"task_id": pair["task_id"], "repetition": pair["repetition"]} for pair in qualified],
            "qualified_paired_totals": {"baseline_tokens": baseline_tokens, "mcp_tokens": mcp_tokens, "token_change_pct": token_change,
                                       "baseline_agent_s": baseline_seconds, "mcp_agent_s": mcp_seconds, "agent_change_pct": time_change},
            "mcp_counts": {key: sum(row["mcp"][key] for row in core["runs"]) for key in core["runs"][0]["mcp"]},
            "http_counts": {key: sum(row["http"][key] for row in core["runs"]) for key in ("receipts", "successful_receipts", "failed_receipts")},
            "artifact_sha256": {name: digest(study / name) for name in artifact_names},
            "linked_qualitative_artifact_sha256": linked, "observed_outside_fixture_file_changes": outside,
            "observed_file_change_items": file_change_items, "observed_file_change_path_entries": file_change_paths,
            "limitations": ["This reads the existing final runtime audit; it does not recheck a later modified environment.",
                            "Command scans and logged file-change paths do not establish OS read isolation or complete tool catalog.",
                            "Supplemental tests are explicitly post-output; reference defects reveal frozen-check gaps without changing formal grades.",
                            "Missing actual usage receipts remain unavailable; no zero-cost or billed-token inference.",
                            "Outcome-selected pairs support paired token/completion-time aggregates; the subset can be biased."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=ROOT / "results/multifile-workflows-2026-10-13")
    parser.add_argument("--core-audit", type=Path, default=ROOT / "results/multifile-audit-2026-10-13/audit.json")
    parser.add_argument("--output", type=Path, default=ROOT / "results/multifile-audit-2026-10-13/report-review.json")
    args = parser.parse_args()
    result = audit_report(args.study.resolve(), args.core_audit.resolve())
    previous = args.output.with_name("report-review-before-leading-limitations.json")
    if previous.is_file():
        result["preserved_previous_proof_sha256"] = digest(previous)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"checks_passed": result["checks_passed"], "missing_receipts": len(result["missing_usage_run_ids"]),
                      "capacity_failures": len(result["capacity_failure_run_ids"]), "qualified_pairs": len(result["qualified_pairs"]),
                      "failed_checks": [key for key, value in result["checks"].items() if not value]}, indent=2))
    return 0 if result["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
