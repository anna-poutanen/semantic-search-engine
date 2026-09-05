#!/usr/bin/env python3
"""Run a fixed post-output Click resource probe on all terminal applicable patches."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_response_limit_edge_review import ROOT, digest, evaluate

PROBE = ROOT / "scripts/probe_click_atomic_resource_edges.py"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/multifile-workflows-2026-10-13")
    parser.add_argument("--work-root", type=Path, default=ROOT / ".cache/click-atomic-edge-review")
    args = parser.parse_args()
    output, work = args.output.resolve(), args.work_root.resolve()
    task = next(t for t in json.loads((output / "tasks.json").read_text())["tasks"]
                if t["id"] == "click_atomic_transactions")
    artifact = output / "supplemental-click-resources"
    reference = ROOT / ".cache/multifile-reference/click_atomic_transactions/reference.patch"
    if digest(reference) != task["reference_patch_sha256"]:
        raise ValueError("Withheld reference differs from frozen task")
    rows = [evaluate(task, "pinned-upstream", None, work, artifact, probe=PROBE),
            evaluate(task, "withheld-reference", reference, work, artifact, probe=PROBE)]
    results = json.loads((output / "results.json").read_text())
    results = results["runs"] if isinstance(results, dict) else results
    for result in results:
        if result["task_id"] != task["id"]:
            continue
        identity = result["run_id"]
        run = output / "runs" / identity
        terminal = json.loads((run / "result.json").read_text())
        if terminal["run_id"] != identity or not terminal.get("phase_timestamps", {}).get("trial_end"):
            raise ValueError("Run is not terminal")
        patch = run / "changes.patch"
        if digest(patch) != terminal["patch_sha256"]:
            raise ValueError("Captured patch differs from terminal record")
        record = evaluate(task, identity, patch, work, artifact,
                          json.loads((run / "base-files.json").read_text()), probe=PROBE)
        record["frozen_correct"] = terminal["correct"]
        record["frozen_completed_correct"] = terminal["completed_correct"]
        rows.append(record)
    record = {"version": 1, "classification": "Post-output supplemental interaction; no formal grades or study subsets changed",
              "method": "Same public-API callback/resource probe in fresh pinned upstream, hash-verified withheld reference and every terminal Click candidate",
              "selection": "05 terminal source review suggested that ExitStack suppression clears exception information before the older atomic resource exits, while Click still propagates the callback error",
              "first_authored_after": "05-r1-click_atomic_transactions-baseline",
              "independent_scope_review": "Root accepted this narrow interaction as a violation of the explicit callback-failure rollback contract: CLI still exits1 with the primary callback error, while the destination commits. Ordinary callback cases are a separate population.",
              "probe_sha256": digest(PROBE), "runner_sha256": digest(Path(__file__)),
              "shared_runner_sha256": digest(ROOT / "scripts/run_response_limit_edge_review.py"),
              "frozen_tasks_sha256": digest(output / "tasks.json"), "records": rows}
    (output / "supplemental-click-resources.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({r["identity"]: {name: case["passed"] for name, case in r["cases"].items()} for r in rows}, indent=2))


if __name__ == "__main__":
    main()
