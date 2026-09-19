#!/usr/bin/env python3
"""Preserve exact frozen study producers before later production changes.

This reads current files or their recorded Git blobs, never imports producers,
loads models, rewrites reports, or changes any frozen plan.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def archive(study):
    study = study.resolve(strict=True)
    if not study.is_relative_to(ROOT / "results"):
        raise ValueError("Study must be inside project results")
    plan_path = study / "frozen-plan.json"
    plan = json.loads(plan_path.read_text())
    rows = []
    for name, expected in plan["implementation_sha256"].items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid frozen relative source path")
        destination = study / "frozen-sources" / (name + ".gz")
        if destination.exists():
            raw = gzip.decompress(destination.read_bytes())
            origin = "existing exact frozen archive"
        elif (ROOT / name).is_file() and digest((ROOT / name).read_bytes()) == expected:
            raw = (ROOT / name).read_bytes()
            origin = "current exact frozen source"
        else:
            result = subprocess.run(["git", "-C", str(ROOT), "show", f"{plan['source_commit']}:{name}"],
                                    check=True, capture_output=True)
            raw = result.stdout
            origin = "exact recorded Git source blob"
        if digest(raw) != expected:
            raise ValueError("Frozen source cannot be reconstructed exactly: " + name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with destination.open("xb") as stream:
                stream.write(gzip.compress(raw, mtime=0))
        if digest(gzip.decompress(destination.read_bytes())) != expected:
            raise ValueError("Written archive differs: " + name)
        rows.append({"path": name, "source_sha256": expected, "bytes": len(raw), "origin": origin,
                     "archive": str(destination.relative_to(study)), "archive_sha256": digest(destination.read_bytes())})
    receipt = {"version": 1, "archived_at_utc": datetime.now(timezone.utc).isoformat(),
               "frozen_plan_sha256": digest(plan_path.read_bytes()), "source_commit": plan["source_commit"],
               "files": rows, "model_calls": 0, "frozen_plan_changed": False}
    output = study / "frozen-source-archive.json"
    if not output.exists():
        with output.open("x") as stream:
            stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"study": str(study.relative_to(ROOT)), "exact_archives": len(rows),
                      "frozen_plan_sha256": receipt["frozen_plan_sha256"]}))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, action="append", required=True)
    for study in parser.parse_args().study:
        archive(study)


if __name__ == "__main__":
    main()
