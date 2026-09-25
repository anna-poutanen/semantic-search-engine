#!/usr/bin/env python3
"""CPU-only clock/source diagnostic of an incomplete, ineligible search run."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("independent_optimization_clock_evidence", ROOT / "scripts/audit_qwen_optimization.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def interrupted_clocks(plan, rows, setups, completion):
    audit.require(audit.canonical(plan["clock_integrity"]) == audit.canonical(audit.CLOCK_POLICY)
                  and audit.canonical(completion["clock_integrity"]) == audit.canonical(audit.CLOCK_POLICY),
                  "Exact prospectively frozen clock policy in incomplete run")
    audit.require(completion["complete"] is False and completion["embedding_owner_closed"] is True
                  and completion.get("clock_failure") is None, "Interrupted run remains incomplete with model owner closed")
    audit.require(rows and len(rows) < plan["planned_requests"]
                  and [row["identity"] for row in rows] == plan["schedule"][:len(rows)],
                  "Every attempted query is the original contiguous frozen prefix, without filtering or retries")
    audit.require(all(row["status"] == "completed" for row in rows[:-1]) and rows[-1]["status"] == "failed"
                  and completion["attempted_requests"] == len(rows)
                  and completion["completed_requests"] == len(rows) - 1,
                  "Final failed query and all previous completed attempts are retained exactly")
    datasets = list(dict.fromkeys(row["identity"]["dataset"] for row in rows))
    audit.require([setup["dataset"] for setup in setups] == datasets, "Every observed corpus setup remains in prefix order")
    baseline = audit.verify_clock_sample(completion["clock_baseline"])
    previous, receipts = baseline, []

    def check(record, field, label, since=None):
        nonlocal previous
        audit.require(record.get("clock_failure") is None, "This hardware interruption must not hide a clock failure")
        receipt = record[field]
        sample = audit.verify_clock_receipt(receipt, baseline, label, since=since)
        audit.require(sample["wall_time_ns"] >= previous["wall_time_ns"]
                      and sample["perf_counter_ns"] >= previous["perf_counter_ns"], "Actual interrupted clock phases remain ordered")
        receipts.append(receipt)
        previous = sample
        return sample

    for setup in setups:
        dataset = setup["dataset"]
        start = check(setup, "clock_start", "setup/" + dataset + "/start")
        check(setup, "clock_end", "setup/" + dataset + "/end", start)
        for row in rows:
            identity = row["identity"]
            if identity["dataset"] != dataset:
                continue
            label = "/".join(str(identity[key]) for key in ("dataset", "query_id", "condition", "repetition"))
            start = check(row, "clock_start", label + "/start")
            suffix = "/end" if row["status"] == "completed" else "/failed_end"
            check(row, "clock_end", label + suffix, start)
    final = check(completion, "clock_end", "pipeline/complete")
    audit.require(final["utc"] == completion["finished_at_utc"], "Interrupted post-cleanup completion UTC is the exact final sample")
    return {"checkpoint_count": len(receipts), "observed_setups": len(setups), "attempted_queries": len(rows),
            "completed_queries": len(rows) - 1, "failed_query": rows[-1]["identity"],
            "failed_phase": rows[-1]["active_phase"], "failed_end_label": rows[-1]["clock_end"]["label"],
            "baseline": baseline, "final_sample": final,
            "max_absolute_cumulative_drift_ns": max(r["cumulative"]["absolute_drift_ns"] for r in receipts),
            "max_absolute_interval_drift_ns": max(r["interval"]["absolute_drift_ns"] for r in receipts if r["interval"] is not None),
            "wall_elapsed_ns": final["wall_time_ns"] - baseline["wall_time_ns"],
            "perf_elapsed_ns": final["perf_counter_ns"] - baseline["perf_counter_ns"],
            "shared_unreset_baseline": True, "all_observed_clock_checks_consistent": True}


def build(root, study):
    plan = audit.verify_frozen_plan(root, study)
    completion = audit.read(study / "completion.json")
    failure = audit.read(study / "failure.json")
    supervisor = audit.read(study / "supervisor.json")
    rows = audit.gz_rows(study / "searches.jsonl.gz")
    clocks = interrupted_clocks(plan, rows, audit.read(study / "setups.json"), completion)
    audit.require(failure["attempted_requests"] == len(rows) and failure["completed_requests"] == len(rows) - 1
                  and failure["error_type"] == rows[-1]["error_type"] == "KeyboardInterrupt"
                  and failure.get("clock_failure") is None, "Main/final-row KeyboardInterrupt receipts reconcile exactly")
    audit.require(supervisor["success"] is False and supervisor["owned_sessions"] is True
                  and supervisor["supervisor_sha256"] == plan["implementation_sha256"]["scripts/supervise_qwen_optimization.py"]
                  and supervisor["frozen_plan_sha256"] == audit.sha((study / "frozen-plan.json").read_bytes())
                  and supervisor["main_exit_code"] == -2 and supervisor["resident_exit_code"] == 0
                  and supervisor["forced_kill"] is supervisor["timed_out"] is False
                  and supervisor["private_auth_token_removed"] is True,
                  "Owned interrupted main, successful resident close and unsuccessful supervisor remain explicit")
    for owner in ("main", "resident", "caffeinate"):
        stop = supervisor[owner + "_stop"]
        audit.require(supervisor[owner + "_owned_group_stopped"] is True and stop["owned_group_stopped"] is True
                      and stop["errors"] == [] and stop["forced_kill"] is False
                      and stop["pid"] == supervisor[owner + "_pid"], "Each exact owned group has a clean stop receipt")
    stop = audit.read(study / "reranker-stop.json")
    audit.require(stop["clean_stop"] is stop["listener_closed"] is True
                  and not (root / ".cache" / study.name / "token.secret").exists(),
                  "Resident cooperative close and private authentication absence")
    audit.require(audit.datetime.fromisoformat(clocks["baseline"]["utc"])
                  >= audit.datetime.fromisoformat(audit.read(study / "run-start.json")["started_at_utc"])
                  and audit.datetime.fromisoformat(completion["finished_at_utc"])
                  <= audit.datetime.fromisoformat(stop["stopped_at_utc"])
                  <= audit.datetime.fromisoformat(supervisor["finished_utc"]), "Interrupted lifecycle timestamps remain ordered")
    return {"classification": "Independent CPU clock/source/interrupt-cleanup diagnostic only; incomplete study is ineligible for selection",
            "checks_passed": False, "study_qualified": False, "diagnostics_verified": True,
            "complete": False, "clean_supervised_run": False, "not_for_selection": True,
            "planned_queries": plan["planned_requests"], "clock_receipts": clocks,
            "implementation_bindings_verified": len(plan["implementation_sha256"]),
            "implementation_sha256": plan["implementation_sha256"],
            "frozen_plan_sha256": audit.sha((study / "frozen-plan.json").read_bytes()),
            "auditor_sha256": audit.sha(Path(__file__).read_bytes()),
            "clock_evidence_auditor_sha256": audit.sha((ROOT / "scripts/audit_qwen_optimization.py").read_bytes()),
            "failure": failure, "supervisor": supervisor,
            "limits": ["The two-second drift policy does not prove absence of every shorter sleep or clock step.",
                       "No retrieval quality, scalar parity, numerical forward reconciliation, complete schedule or latency selection certification is made.",
                       "All observed attempts, including the failed request, remain preserved; the unobserved schedule is not filled or filtered."],
            "artifact_sha256": {p.name: audit.sha(p.read_bytes()) for p in sorted(study.iterdir())
                                if p.is_file() and p.name not in ("clock-interruption-audit.json", "clock-interruption-audit.md")}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--study", type=Path, required=True)
    args = parser.parse_args(argv)
    study = args.study.resolve()
    try:
        result = build(args.root.resolve(), study)
    except BaseException as error:
        attempts = study / "clock-audit-attempts"
        attempts.mkdir(exist_ok=True)
        number = len(list(attempts.glob("*-failure.json"))) + 1
        stem = f"{number:02d}"
        (attempts / (stem + "-failure.json")).write_text(json.dumps({"error_type": type(error).__name__, "error": str(error)}) + "\n")
        (attempts / (stem + "-auditor.py.txt")).write_bytes(Path(__file__).read_bytes())
        (attempts / (stem + "-clock-evidence-auditor.py.txt")).write_bytes((ROOT / "scripts/audit_qwen_optimization.py").read_bytes())
        raise
    (study / "clock-interruption-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    clock = result["clock_receipts"]
    (study / "clock-interruption-audit.md").write_text(
        "# Interrupted clock diagnostic\n\nThe study remains incomplete and is not eligible for selection. "
        f"Independently verified {result['implementation_bindings_verified']} frozen implementation bindings and "
        f"{clock['checkpoint_count']} chronological clock checkpoints for {clock['attempted_queries']} attempts "
        f"({clock['completed_queries']} completed, one retained failed query), including its failed-end sample and final post-cleanup sample. "
        f"Maximum cumulative wall/performance drift was {clock['max_absolute_cumulative_drift_ns']} ns; "
        f"maximum interval drift was {clock['max_absolute_interval_drift_ns']} ns. "
        "Owned cleanup receipts show all three groups stopped and the authentication token removed. "
        "The main KeyboardInterrupt and unsuccessful supervisor remain explicit.\n\n"
        "checks_passed: false; study_qualified: false; diagnostics_verified: true.\n")
    print(json.dumps({key: result[key] for key in ("checks_passed", "study_qualified", "diagnostics_verified")}))


if __name__ == "__main__":
    main()
