#!/usr/bin/env python3
"""Render an independently certified coding inventory without model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CONDITIONS = ("baseline", "mcp_fast", "mcp_qwen")
METRICS = (
    ("total_tokens", "Gross tokens"),
    ("uncached_input_tokens", "Uncached input"),
    ("cached_input_tokens", "Cached input"),
    ("cache_write_input_tokens", "Cache-write input subset"),
    ("output_tokens", "Output"),
    ("reasoning_output_tokens", "Reasoning output subset"),
    ("agent_elapsed_s", "Agent seconds"),
    ("service_setup_s", "Service setup seconds"),
    ("full_trial_elapsed_s", "Full trial seconds"),
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def number(value):
    if value is None:
        return "unknown"
    return f"{value:,}" if type(value) is int else f"{value:,.2f}"


def percent(value):
    return "unknown" if value is None else f"{value:+.2f}%"


def native_count(evidence):
    value = evidence.get("completed")
    if evidence.get("counts_complete") is False:
        return "unknown" if value is None else f"≥{number(value)} (total unknown)"
    return number(value)


def table(headers, rows):
    return ["| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]]


def load_certified(study, audit, allow_partial=False):
    """Reject stale/failed certificates and mismatched terminal attempt bytes."""
    study, audit = Path(study), Path(audit)
    certificate = json.loads(audit.read_text())
    if certificate.get("checks_passed") is not True:
        raise ValueError("A passing independent evidence certificate is required")
    if certificate.get("inventory_complete") is not True and not allow_partial:
        raise ValueError("Final reports require the complete frozen inventory")
    if not allow_partial and certificate.get("final_completion_receipt") is not True:
        raise ValueError("Final reports require the driver's final completion receipt")
    paths = {}
    snapshots = certificate.get("global_input_snapshots", {})
    names = ["frozen-config.json", "tasks.json", "run-start.json", "results.json", "summary.json"]
    if certificate.get("final_completion_receipt") is True:
        if "completion.json" not in certificate["artifact_sha256"]:
            raise ValueError("Final completion receipt lacks its certificate artifact binding")
        names.append("completion.json")
    for name in names:
        if snapshots:
            if name not in snapshots:
                raise ValueError("Certificate snapshot binding missing: " + name)
            snapshot = snapshots[name]
            if snapshot["file"] != "as-of/" + name or snapshot["sha256"] != certificate["artifact_sha256"].get(name):
                raise ValueError("Certificate snapshot binding mismatch: " + name)
            path = audit.parent / "as-of" / name
        else:
            path = study / name
        paths[name] = path
        if digest(path) != certificate["artifact_sha256"].get(name):
            raise ValueError("Certificate artifact mismatch: " + name)
    records = json.loads(paths["results.json"].read_text())
    summary = json.loads(paths["summary.json"].read_text())
    certified = {item["run_id"]: item for item in certificate["attempts"]}
    if (len(certified) != len(certificate["attempts"]) or len(records) != len(certified)
            or {row["run_id"] for row in records} != set(certified)):
        raise ValueError("Attempt inventory does not match the certificate")
    for row in records:
        item = certified.get(row["run_id"])
        if not item or item.get("integrity_passed") is not True:
            raise ValueError("Uncertified attempt: " + row["run_id"])
        if digest(study / "runs" / row["run_id"] / "result.json") != item["result_sha256"]:
            raise ValueError("Terminal attempt bytes changed: " + row["run_id"])
        if row.get("usage") != item.get("provider_usage"):
            raise ValueError("Attempt usage differs from independent audit")
        if row.get("patch_sha256"):
            patch = study / "runs" / row["run_id"] / "changes.patch"
            if not patch.is_file() or digest(patch) != row["patch_sha256"]:
                raise ValueError("Published patch witness missing or changed: " + row["run_id"])
    for key in ("inventory_complete", "planned_attempts", "recorded_attempts"):
        if summary[key] != certificate[key]:
            raise ValueError("Aggregate inventory differs from independent audit")
    def missing(rows):
        return sorted((row["task_id"], row["repetition"], row["condition"]) for row in rows)
    if missing(summary["missing_attempts"]) != missing(certificate["missing_attempts"]):
        raise ValueError("Missing-attempt accounting differs from independent audit")
    for condition, values in certificate["conditions"].items():
        if any(summary["conditions"][condition][key] != value for key, value in values.items()):
            raise ValueError("Condition accounting differs from independent audit")
    if summary["comparisons"] != certificate["comparisons"]:
        raise ValueError("Aggregate accounting differs from independent audit")
    return records, summary, certificate


def render(records, summary, certificate):
    partial = not certificate["inventory_complete"] or not certificate["final_completion_receipt"]
    lines = ["# Three-condition coding comparison" + (" — interim snapshot" if partial else ""), "",
             f"{certificate['recorded_attempts']}/{certificate['planned_attempts']} frozen attempts recorded. "
             "These are two balanced repetitions of three previously observed development tasks; "
             "they do not establish general coding accuracy or token savings.", "",
             "Frozen correctness requires both the strict contract checks and restored upstream "
             "compatibility. Completed-correct additionally requires normal, clean CLI completion. "
             "Supplemental independent patch findings remain separate from these frozen grades.", ""]
    if not certificate["inventory_complete"]:
        lines += ["This snapshot is incomplete. Missing attempts are pending, and full-workload totals remain unknown.", ""]
    elif partial:
        lines += ["The attempt inventory is complete, but the driver's final completion witness is pending. This remains an interim snapshot.", ""]
    lines += ["## Full frozen inventory", ""]
    lines += table(["Condition", "Recorded/planned", "Normal", "Correct", "Completed-correct", "Timeouts"], [
        [condition, f"{(c := summary['conditions'][condition])['recorded']}/{c['planned']}",
         c["normally_completed"], c["correct"], c["completed_correct"], c["timeouts"]]
        for condition in CONDITIONS])
    lines += ["", "## Every recorded attempt", ""]
    attempts = {item["run_id"]: item for item in certificate["attempts"]}
    lines += table(["Run", "Normal", "Correct", "Completed-correct", "Gross tokens", "Uncached input", "Agent s", "MCP successes", "Qwen forwards¹"], [
        [row["run_id"], (a := attempts[row["run_id"]])["normally_completed"], row["correct"],
         row["completed_correct"], number((row.get("usage") or {}).get("total_tokens")),
         number((row.get("usage") or {}).get("uncached_input_tokens")),
         number(row.get("agent_elapsed_s") if a["timing_qualified"] else None),
         a["mcp"]["successful"], native_count(a["native_qwen"])]
        for row in records])
    lines += ["", "¹ Measured forwards exclude separately recorded model warmup. Startup-failure counts are labelled as retained lower bounds when total counts are unknown. Availability is not evidence of actual tool use.", "",
              "## Actual usage and time", "",
              "Gross tokens are reported completed-turn input plus output. Cached input and cache writes "
              "are input subsets; reasoning tokens are an output subset. Subsets are never added to the gross total. "
              "Uncached input is input minus cached input. Unknown or interrupted-turn usage is not zero. "
              "These receipts do not establish per-request billing.", "",
              "Agent time spans CLI launch through exit, including hosted waits and tool calls. Service setup "
              "covers indexing/startup/warmup before that timer; baseline has no measured service setup. "
              "Full trial time also includes preparation, cleanup and grading. Timing values require qualified clocks.", ""]
    lines += table(["Condition", "Metric", "Observed/planned", "Measured sum", "Condition median"], [
        [condition, label, f"{(m := summary['conditions'][condition]['measurements'][key])['observations']}/{summary['conditions'][condition]['planned']}",
         number(m["sum"]), number(m["median"])]
        for condition in CONDITIONS for key, label in METRICS])
    lines += ["", "Measured sums include only known values. They are full-condition totals only when every planned attempt contributes.", "",
              "## Paired comparisons", "",
              "Each percentage is the change from the left condition to the right, computed from paired "
              "measurement sums. Median pair changes are separate statistics. Every contributing task/repetition "
              "is named; different metrics may have different eligible pairs. A pair of incorrect implementations "
              "cannot demonstrate better correct-task efficiency.", ""]
    for comparison in summary["comparisons"]:
        lines += [f"### {comparison['left']} → {comparison['right']}: {comparison['subset']}", "",
                  f"Eligible paired attempts: {comparison['eligible_pairs']}.", ""]
        lines += table(["Metric", "Pairs", "Left sum", "Right sum", "Aggregate change", "Median pair change", "Members"], [
            [label, (m := comparison["measurements"][key])["pairs"], number(m["left_sum"]), number(m["right_sum"]),
             percent(m["aggregate_change_pct"]), percent(m["median_pair_change_pct"]),
             "; ".join(f"{task}/r{repetition}" for task, repetition in m["members"]) or "none"]
            for key, label in METRICS])
        lines += [""]
    lines += ["## Evidence and limits", "",
              f"{sum(bool(row.get('patch_sha256')) for row in records)}/{len(records)} recorded attempts retain hash-verified published patches. "
              "Available artifacts retain original-test compatibility results, compact tool payloads, "
              "source snapshots, actual usage and process/clock receipts; startup or interrupted failures may lack some witnesses. Qwen requests retain native singleton "
              "forward witnesses. Independent evidence audits verify those bindings; independent patch review "
              "assesses defects beyond the frozen gates.", "",
              "Task reuse, six attempts per condition, hosted variability, cache reuse, and observed tool-use "
              "differences limit causal attribution. Retrieval quality improvements do not establish coding "
              "correctness or paid-agent savings. The transport probe's usage is outside this eighteen-attempt inventory.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    records, summary, certificate = load_certified(args.study, args.audit, args.allow_partial)
    content = render(records, summary, certificate)
    with args.output.open("x") as handle:
        handle.write(content)


if __name__ == "__main__":
    main()
