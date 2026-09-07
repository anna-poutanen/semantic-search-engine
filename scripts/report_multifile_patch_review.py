#!/usr/bin/env python3
"""Render the retained qualitative review separately from frozen workflow grades."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/multifile-workflows-2026-10-13")
    args = parser.parse_args()
    output = args.output.resolve()
    review = json.loads((output / "patch-review.json").read_text())
    inventory = json.loads((output / "results.json").read_text())
    inventory = inventory["runs"] if isinstance(inventory, dict) else inventory
    actual = {row["run_id"]: row for row in inventory}
    rows = review["runs"]
    if any(row["run_id"] not in actual for row in rows):
        raise ValueError("Review includes a run outside terminal inventory")
    distinct = {}
    reference_behaviors = set()
    affected = []
    for row in rows:
        issues = [issue for issue in row.get("issues", [])
                  if issue.get("classification") == "Confirmed supplemental contract defect"]
        if issues:
            affected.append(row["run_id"])
        for issue in issues:
            distinct.setdefault((row["task_id"], issue["case"]), []).append(row["run_id"])
            if issue.get("shared_reference_failure"):
                reference_behaviors.add((row["task_id"], issue["case"]))
    probes = []
    for filename in ("supplemental-response-edges.json", "supplemental-click-resources.json"):
        path = output / filename
        if path.exists():
            probes.append((filename, json.loads(path.read_text())))
    reference_cases = sum(sum(not case["passed"] for case in record["cases"].values())
                          for _, probe in probes for record in probe["records"]
                          if record["identity"] == "withheld-reference")
    added_passes = sum(row.get("test_checks", {}).get("candidate", {}).get("passed", 0) for row in rows)
    added_failures = sum(row.get("test_checks", {}).get("candidate", {}).get("failed", 0) for row in rows)
    original_edits = sum(bool(row.get("regression_tests", {}).get("changed_or_deleted_original_tests")) for row in rows)
    lines = ["# Multifile patch review and supplemental correctness findings", "",
             f"Reviewed {len(rows)} of 16 scheduled attempts. This is a post-output source and regression review of every retained terminal artifact. It leaves the frozen selected/upstream grades, prompts, run inventory and performance subsets unchanged.", "",
             "## Confirmed supplemental findings", "",
             f"**{len(distinct)} distinct scoped defects affect {len(affected)} reviewed attempts, comprising {sum(sum(issue.get('classification') == 'Confirmed supplemental contract defect' for issue in row.get('issues', [])) for row in rows)} attempt-level findings. The withheld references fail {reference_cases} supplemental probe cases spanning {len(reference_behaviors)} distinct behaviors, showing that the frozen checks do not establish complete contract correctness.**", "",
             "| Behavior | Affected retained attempts |", "|---|---|"]
    for (task, case), ids in sorted(distinct.items()):
        lines.append(f"| {task}: {case} | {', '.join(ids)} |")
    lines += ["",
              "The Requests probes examine suspended buffered line readers after a size breach, already-created cached iterators after a later lower limit breaches the same response, preservation of the primary size error through failing response-context cleanup, and replacement-body reads inside a later response hook before the limit is attached. Passing cleanup diagnostics may use notes, explicit cause or implicit exception context. Click probes examine two combinations: a separately registered truthy suppressing resource makes the atomic destination commit while the CLI still propagates the original callback failure, and a separate registered cleanup callback/resource can replace the primary callback error. These do not imply ordinary callbacks or all cleanup paths fail.", "",
              "Independent root scope review accepted these behaviors against the existing explicit callback/terminal-read/cleanup/hook requirements. The response oracle was repaired once to accept implicit chaining; v3 then added separately documented live/cached hook variants after terminal11 source review. Click's v2 added separate registered cleanup after13 review; v3 broadened its diagnostic oracle to bounded recursive cause/context/ExceptionGroup chains or notes, still requiring the original outer primary callback exception. Prior scripts/results remain in ignored version archives, and public hashes/reasons/timing are in [supplemental-probe-revisions.json](supplemental-probe-revisions.json). The original neutral cases were unchanged. All applicable terminal candidates and the frozen withheld reference receive the same probes; no failed attempt is dropped or retried because of its result.", "",
              "A frozen `correct=true` means both selected and restored-original source gates passed. `completed_correct=true` also requires normal completion, exit zero, no timeout and no recorded failure. Neither label is silently redefined by this supplemental review. An attempt can pass both gates and still have one of the demonstrated defects.", "",
              "## Retained attempts", "",
              "| Run | Frozen correct | Completed correct | Added tests pass/fail | Findings |", "|---|---:|---:|---:|---:|"]
    for row in rows:
        run = actual[row["run_id"]]
        tests = row.get("test_checks", {}).get("candidate")
        test_summary = f"{tests['passed']}/{tests['failed']}" if tests else "not available"
        findings = sum(issue.get("classification") == "Confirmed supplemental contract defect" for issue in row.get("issues", []))
        lines.append(f"| {row['run_id']} | {str(run['correct']).lower()} | {str(run['completed_correct']).lower()} | {test_summary} | {findings} |")
    lines += ["", "## Regression and integrity evidence", "",
              f"Fresh evaluator clones reproduce {added_passes} passing and {added_failures} failing authored test cases. These are parameterized cases, not that many independent behaviors. {original_edits} attempts changed or removed original tests/configuration. Added tests alone are not classified as weakening. The independent runtime/source-receipt audit is a separate artifact.", "",
              "Added tests are also exercised after restoring only production source. A missing public feature may cause collection errors, so those outcomes are not counted as hundreds of independently failing behavioral cases. Full results, invocation and hashes are in [patch-review.json](patch-review.json) and `patch-review-checks/`.", "",
              "Timeouts retain their patches and source grades but are not completed-correct, and absent actual usage is not imputed as zero. No-edit provider-capacity attempts08/12 are distinguished from implementation failure: their absent features fail selected grading while pristine original tests pass. Attempt13 also ends with a provider-capacity error, after retaining a substantive patch. These operational outcomes must remain in the main comparison, with any conditional performance analysis clearly labeled.", "",
              "## Limits", "",
              "The supplemental probes were selected after outputs; they cannot replace prospectively frozen primary grading or support a causal claim about MCP by themselves. The four authored tasks and two repetitions are a small sample. Source inspection, original restoration, authored regressions and targeted probes strengthen the evidence while leaving unexamined framework behavior outside the demonstrated scope.", ""]
    (output / "patch-review.md").write_text("\n".join(lines))
    print(json.dumps({"reviewed_attempts": len(rows), "distinct_confirmed_behaviors": len(distinct),
                      "affected_attempts": len(affected), "reference_failed_probe_cases": reference_cases,
                      "report": "patch-review.md"}, indent=2))


if __name__ == "__main__":
    main()
