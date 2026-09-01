"""Command-line entry point for the local model experiment."""
import argparse
import json
from pathlib import Path

from semantic_search.corpus import extract_snippets
from semantic_search.experiment import run_experiment, unique_symbols
from semantic_search.model import H2OClient
from semantic_search.retrieval import BM25Retriever


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--repo", type=Path, default=Path(".cache/corpora/flask"))
    benchmark.add_argument("--dataset", type=Path, default=Path("benchmarks/flask_queries.json"))
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--candidates", type=int, default=10)
    benchmark.add_argument("--max-lines", type=int, default=160)
    benchmark.add_argument("--url", default="http://127.0.0.1:8741")
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--repo", type=Path, required=True)
    search.add_argument("--source-root", default="src/flask")
    search.add_argument("--candidates", type=int, default=10)
    search.add_argument("--max-results", type=int, default=5)
    search.add_argument("--lexical-only", action="store_true")
    search.add_argument("--url", default="http://127.0.0.1:8741")
    args = parser.parse_args()
    if args.candidates < 1:
        parser.error("--candidates must be positive")
    if args.command == "benchmark":
        if args.max_lines <= 40:
            parser.error("--max-lines must exceed the 40-line overlap")
        result = run_experiment(args.repo, args.dataset, args.output, args.url,
                                args.candidates, args.max_lines)
        print(json.dumps(result["metrics"], indent=2))
    else:
        if args.max_results < 1:
            parser.error("--max-results must be positive")
        snippets = extract_snippets(args.repo, args.source_root, max_lines=160, overlap_lines=40)
        pairs = BM25Retriever(snippets).search(args.query, args.candidates)
        scores = {}
        if not args.lexical_only:
            client = H2OClient(args.url)
            scores = {s.id: client.score(args.query, s).score for s, _ in pairs}
            pairs.sort(key=lambda pair: (-scores[pair[0].id], -pair[1], pair[0].id))
        for snippet in unique_symbols([s for s, _ in pairs])[:args.max_results]:
            score = f" relevance={scores[snippet.id]:.3f}/3" if scores else ""
            print(f"\n{snippet.path}:{snippet.start_line}-{snippet.end_line} {snippet.symbol}{score}")
            print(snippet.text)


if __name__ == "__main__":
    main()
