#!/usr/bin/env python3
"""Download the fixed real-repository corpus described by a benchmark."""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "benchmarks/flask_queries.json")
    parser.add_argument("--destination", type=Path, default=ROOT / ".cache/corpora/flask")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text())
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    if not args.destination.exists():
        subprocess.run(["git", "clone", "--no-checkout", dataset["repo_url"],
                        str(args.destination)], check=True)
    subprocess.run(["git", "-C", str(args.destination), "fetch", "origin",
                    dataset["revision"]], check=True)
    subprocess.run(["git", "-C", str(args.destination), "checkout", "--detach",
                    dataset["revision"]], check=True)


if __name__ == "__main__":
    main()
