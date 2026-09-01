#!/usr/bin/env python3
"""Append macOS footprint metrics to a completed local benchmark, before shutdown."""
import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from semantic_search.experiment import render_report

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="The completed run directory")
    args = parser.parse_args()
    state = json.loads((ROOT / ".cache/server/processes.json").read_text())
    memory = {"tool": "macOS vmmap -summary", "captured_utc": datetime.now(timezone.utc).isoformat(),
              "note": "Physical footprint includes Metal allocations omitted by ps RSS. "
                      "Values are rounded by vmmap; peak is per process since launch, not simultaneous combined peak.",
              "processes": {}}
    for name, pid in zip(("facade", "shim"), state["pids"], strict=True):
        output = subprocess.check_output(["vmmap", "-summary", str(pid)], text=True)
        fields = {}
        for label, key in (("Physical footprint", "physical_footprint"),
                           ("Physical footprint (peak)", "physical_footprint_peak")):
            match = re.search(r"^" + re.escape(label) + r":\s+(\S+)", output, re.MULTILINE)
            if not match:
                raise RuntimeError(f"vmmap did not report {label} for {name}")
            fields[key] = match.group(1)
        memory["processes"][name] = fields
    result_file = args.results / "results.json"
    result = json.loads(result_file.read_text())
    result["memory_footprint"] = memory
    result_file.write_text(json.dumps(result, indent=2) + "\n")
    (args.results / "report.md").write_text(render_report(result))
    print(json.dumps(memory, indent=2))


if __name__ == "__main__":
    main()
