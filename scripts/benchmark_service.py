#!/usr/bin/env python3
"""Evaluate the resident HTTP pipeline on a frozen corpus/dataset."""
import argparse
from pathlib import Path

from semantic_search.service_experiment import run_service_experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(".cache/corpora/requests"))
    parser.add_argument("--dataset", type=Path, default=Path("benchmarks/new_requests_queries.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=Path(".cache/indexes/requests-evaluation.sqlite3"))
    parser.add_argument("--h2o-url", help="Include the fixed dense K4 and hybrid K3 H2O comparisons")
    args = parser.parse_args()
    run_service_experiment(args.repo, args.dataset, args.output, args.db, args.h2o_url)


if __name__ == "__main__":
    main()
