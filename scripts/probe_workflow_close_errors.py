#!/usr/bin/env python3
"""Post-hoc CPU close-error probe, separate from frozen workflow grading.

Run only after the two Click atomic-output trials are recorded. Source checkouts
are read only. Each probe imports its own checkout in a fresh Python process.
The injected failure removes an atomic temporary file, never the destination.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = b"original destination bytes\n"
REPLACEMENT = "replacement bytes\n"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True).stdout


def worker(repo: Path) -> dict:
    sys.dont_write_bytecode = True
    source = (repo / "src").resolve()
    sys.path.insert(0, str(source))
    import click
    from click.testing import CliRunner

    if not Path(click.__file__).resolve().is_relative_to(source):
        raise RuntimeError("Probe imported Click outside the requested checkout")
    with tempfile.TemporaryDirectory(prefix="click-close-error-") as directory:
        destination = Path(directory) / "output.txt"
        destination.write_bytes(ORIGINAL)
        observations = {}

        @click.command()
        @click.option("--output", type=click.File("w", lazy=True, atomic=True))
        def command(output):
            output.write(REPLACEMENT)
            output.flush()
            observations["destination_old_bytes_during_callback"] = destination.read_bytes() == ORIGINAL
            temporary = [path for path in destination.parent.iterdir() if path != destination]
            if len(temporary) != 1 or not temporary[0].name.startswith(".__atomic-write"):
                raise RuntimeError("Expected exactly one atomic temporary source")
            temporary[0].unlink()
            observations["temporary_sources_removed"] = 1

        result = CliRunner().invoke(command, ["--output", str(destination)])
        remaining = destination.read_bytes() if destination.exists() else None
        observations.update(
            cli_exit_code=result.exit_code,
            actual_exception_type=type(result.exception).__name__ if result.exception else None,
            actual_exception_errno=getattr(result.exception, "errno", None),
            destination_exists=destination.exists(),
            destination_bytes_hex=remaining.hex() if remaining is not None else None,
            destination_bytes_utf8=remaining.decode("utf-8") if remaining is not None else None,
            destination_old_bytes_preserved=remaining == ORIGINAL,
            expected_exception_type="FileNotFoundError",
            expected_cli_exit_code=1,
        )
        observations["matches_pinned_upstream_error_behavior"] = (
            result.exit_code == 1
            and isinstance(result.exception, FileNotFoundError)
            and observations["temporary_sources_removed"] == 1
            and remaining == ORIGINAL
        )
        return observations


def run(run_dir: Path, output: Path) -> dict:
    config_path = run_dir / "frozen-config.json"
    config = json.loads(config_path.read_text())
    manifest = json.loads((run_dir / "tasks.json").read_text())
    task = next(task for task in manifest["tasks"] if task["id"] == "click_atomic_close")
    records = {record["run_id"]: record for record in json.loads((run_dir / "results.json").read_text())}
    pair = [record for record in records.values() if record["task_id"] == task["id"]]
    if len(pair) != 2 or {record["condition"] for record in pair} != {"baseline", "mcp"}:
        raise ValueError("Both Click atomic-output trials must be recorded before this probe")
    run_ids = [record["run_id"] for record in pair]
    verifier_method_count = sum(item["seed_validation"]["reference"]["tests_run"]
                                for item in manifest["tasks"])
    followup = config.get("followup")
    upstream = ROOT / task["corpus"]
    if git(upstream, "rev-parse", "HEAD").decode().strip() != task["revision"]:
        raise ValueError("Upstream checkout is not the declared pinned revision")
    if git(upstream, "status", "--porcelain").strip():
        raise ValueError("Pinned upstream checkout has local changes")
    sources = [("pinned_upstream", upstream)] + [
        (run_id, Path(config["work_root"]) / "workspaces" / run_id) for run_id in run_ids]
    probe = {
        "schema_version": 1,
        "classification": ("Supplemental CPU fault-injection diagnostic predefined from original-pilot patch review"
                           if followup else "Post-hoc CPU fault-injection diagnostic")
                          + ", excluded from frozen correctness and performance metrics",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": f"No checkout edits, agent feedback, retrieval, GPU inference, or hosted model calls. The {verifier_method_count} declared verifier methods and their recorded results remain unchanged.",
        "declared_verifier_methods": verifier_method_count,
        "paired_run_ids": run_ids,
        "scenario": "An existing destination contains original bytes. The callback writes and flushes a lazy atomic output, then removes its sole atomic temporary source before context cleanup tries to commit it.",
        "interpretation": "Expected failure is the pinned upstream public-API behavior. "
                          + ("This supplemental probe was planned from original-pilot review before follow-up agent outputs."
                             if followup else "This probe was selected after original-pilot patch review.")
                          + " It is not an additional formal correctness gate.",
        "original_destination_bytes_hex": ORIGINAL.hex(),
        "original_destination_bytes_utf8": ORIGINAL.decode(),
        "frozen_config_sha256": digest(config_path.read_bytes()),
        "probe_script_sha256": digest(Path(__file__).read_bytes()),
        "pinned_upstream_revision": task["revision"],
        "results": [],
    }
    for label, repo in sources:
        before_diff = digest(git(repo, "diff", "--binary", "HEAD"))
        hashes = {name: digest((repo / name).read_bytes())
                  for name in ("src/click/types.py", "src/click/utils.py", "src/click/testing.py")}
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(repo)],
                                 capture_output=True, text=True, timeout=30, env=env)
        if process.returncode != 0:
            raise RuntimeError(f"CPU probe infrastructure failed for {label}; exit {process.returncode}")
        observed = json.loads(process.stdout)
        if not (observed.get("temporary_sources_removed") == 1
                and observed.get("destination_old_bytes_during_callback") is True):
            raise RuntimeError(f"Fault injection was not applied as intended for {label}")
        if before_diff != digest(git(repo, "diff", "--binary", "HEAD")):
            raise RuntimeError(f"Tracked checkout source changed during CPU probe for {label}")
        probe["results"].append({
            "source": label,
            "source_sha256": hashes,
            "checkout_initial_commit": git(repo, "rev-parse", "HEAD").decode().strip(),
            "checkout_tracked_diff_sha256": before_diff,
            "checkout_tracked_source_unchanged": True,
            "declared_verifier_passed": records[label]["correct"] if label in records else None,
            "observed": observed,
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(probe, indent=2, sort_keys=True) + "\n")
    return probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker.resolve())))
    else:
        if args.run is None or args.output is None:
            parser.error("--run and --output are required")
        result = run(args.run.resolve(), args.output.resolve())
        print(json.dumps([{ "source": row["source"], "observed": row["observed"] }
                          for row in result["results"]], indent=2))


if __name__ == "__main__":
    main()
