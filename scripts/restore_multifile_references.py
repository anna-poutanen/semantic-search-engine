#!/usr/bin/env python3
"""Restore SHA-verified, post-study published references into evaluator cache."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    tasks = json.loads((ROOT / "benchmarks/coding_workflows_multifile.json").read_text())["tasks"]
    verified = []
    for task in tasks:
        identifier = task["id"]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", identifier):
            raise ValueError("Invalid reference identity")
        source = ROOT / "benchmarks/multifile_references" / (identifier + ".patch")
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != task["reference_patch_sha256"]:
            raise ValueError("Published reference differs from frozen hash")
        destination = ROOT / ".cache/multifile-reference" / identifier / "reference.patch"
        if destination.exists() and destination.read_bytes() != raw:
            raise ValueError("Refusing to overwrite a different existing reference")
        verified.append((destination, raw))
    for destination, raw in verified:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(raw)
    print(f"Verified {len(verified)} frozen references; cache paths restored")


if __name__ == "__main__":
    main()
