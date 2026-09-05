#!/usr/bin/env python3
"""Apply the supplemental edge probe to upstream, reference and terminal patches.

Only fresh evaluator copies under the ignored review directory are written.
Formal study assets, grades and active coding workspaces are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts/probe_response_limit_edges.py"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(repo, *args, input=None):
    return subprocess.run(["git", "-C", str(repo), *args], input=input,
                          capture_output=True, check=True).stdout


def evaluate(task, identity, patch, work, artifact, base_files=None, probe=PROBE):
    repo = work / identity
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    raw = git(ROOT / task["corpus"], "archive", task["revision"])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(repo, filter="data")
    if base_files:
        for name, expected in base_files.items():
            if digest(repo / name) != expected:
                raise ValueError(f"Pinned archive differs from recorded base: {name}")
    if patch and patch.stat().st_size:
        git(repo, "init", "--quiet")
        git(repo, "apply", "--binary", "-", input=patch.read_bytes())
    command = [sys.executable, str(probe), "--repo", str(repo)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"Supplemental probe failed: {identity}: {result.stderr}")
    record = json.loads(result.stdout)
    if record["probe_sha256"] != digest(probe):
        raise ValueError("Probe script changed during execution")
    record.update({"identity": identity, "base_revision": task["revision"],
                   "patch_sha256": digest(patch) if patch else None,
                   "command": [x.replace(str(ROOT), "<PROJECT>") for x in command],
                   "exit_code": result.returncode})
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / f"{identity}.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/multifile-workflows-2026-10-13")
    parser.add_argument("--work-root", type=Path, default=ROOT / ".cache/response-limit-edge-review")
    args = parser.parse_args()
    output, work = args.output.resolve(), args.work_root.resolve()
    tasks = json.loads((output / "tasks.json").read_text())["tasks"]
    task = next(x for x in tasks if x["id"] == "requests_response_limit")
    artifact = output / "supplemental-response-edges"
    reference = ROOT / ".cache/multifile-reference/requests_response_limit/reference.patch"
    if digest(reference) != task["reference_patch_sha256"]:
        raise ValueError("Withheld reference differs from frozen task")
    records = [evaluate(task, "pinned-upstream", None, work, artifact),
               evaluate(task, "withheld-reference", reference, work, artifact)]
    rows = json.loads((output / "results.json").read_text())
    rows = rows["runs"] if isinstance(rows, dict) else rows
    for result in rows:
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
                          json.loads((run / "base-files.json").read_text()))
        record["frozen_correct"] = terminal["correct"]
        record["frozen_completed_correct"] = terminal["completed_correct"]
        records.append(record)
    summary = {"version": 1, "classification": "Post-output supplemental edge review; no formal grade or study subset changed",
               "method": "Fresh pinned archives plus hash-verified frozen reference or terminal patches; deterministic in-memory transport, no network/model calls",
               "selection": "Buffered/cached reader gaps were observed in retained 04 candidate authored regressions; context cleanup masking was suggested by post-output source inspection. Fixed same probes apply to every terminal response-limit candidate and reference.",
               "first_authored_after": ["03-r1-requests_response_limit-mcp", "04-r1-requests_response_limit-baseline"],
               "probe_sha256": digest(PROBE), "runner_sha256": digest(Path(__file__)),
               "frozen_tasks_sha256": digest(output / "tasks.json"),
               "records": records}
    (output / "supplemental-response-edges.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({r["identity"]: {name: case["passed"] for name, case in r["cases"].items()} for r in records}, indent=2))


if __name__ == "__main__":
    main()
