#!/usr/bin/env python3
import argparse
from pathlib import Path

from semantic_search.quality import run_quality

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Frozen retrieval quality across development and repository holdout.")
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/quality_manifest.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_quality(args.manifest, args.output)
