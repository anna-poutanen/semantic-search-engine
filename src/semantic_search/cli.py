"""Search saved code locally, or use a resident loopback search service."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import signal

from semantic_search.experiment import run_experiment


def index_path(repo, source_root, directory):
    identity = str(repo.resolve()) + "\n" + source_root
    return directory / (hashlib.sha256(identity.encode()).hexdigest() + ".sqlite3")


def print_results(response):
    if response.get("match_status") == "insufficient_evidence":
        print("No strong implementation match found. Try a more specific query.")
    elif response.get("match_status") == "no_candidates":
        print("No indexed candidate matched this query.")
    for result in response["results"]:
        score = result.get("h2o_score")
        suffix = f" relevance={score:.3f}/3" if score is not None else ""
        if result.get("truncated"):
            suffix += " (excerpt truncated by source budget)"
        print(f"\n{result['path']}:{result['start_line']}-{result['end_line']} {result['symbol']}{suffix}")
        print(result["source"])
    if response.get("budget_exhausted"):
        print("\nSource budget reached; increase --source-budget for more evidence.")


def interrupt_service(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--repo", type=Path, default=Path(".cache/corpora/flask"))
    benchmark.add_argument("--dataset", type=Path, default=Path("benchmarks/flask_queries.json"))
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--candidates", type=int, default=10)
    benchmark.add_argument("--max-lines", type=int, default=160)
    benchmark.add_argument("--url", default="http://127.0.0.1:8741")
    shortlist = subparsers.add_parser("shortlist-benchmark")
    shortlist.add_argument("--repo", type=Path, default=Path(".cache/corpora/flask"))
    shortlist.add_argument("--dataset", type=Path, default=Path("benchmarks/flask_queries.json"))
    shortlist.add_argument("--output", type=Path, required=True)
    shortlist.add_argument("--index-cache", type=Path, default=Path(".cache/indexes"))
    shortlist.add_argument("--url", default="http://127.0.0.1:8741")
    shortlist.add_argument("--prior-run", type=Path, default=Path("results/flask-h2o-q8-2026-10-12"))
    serve = subparsers.add_parser("serve", help="Keep Qwen and the incremental index resident")
    serve.add_argument("--repo", type=Path, required=True)
    serve.add_argument("--source-root", default="src/flask")
    serve.add_argument("--index-cache", type=Path, default=Path(".cache/indexes"))
    serve.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8750)
    serve.add_argument("--url", default="http://127.0.0.1:8741", help="Optional H2O server URL")
    serve.add_argument("--no-watch", action="store_true", help="Discover changes only on searches/refreshes")
    serve.add_argument("--watch-interval", type=float, default=1.0)
    serve.add_argument("--abstention-policy", type=Path, help="Development-calibrated weak-match policy")
    mcp = subparsers.add_parser("mcp", help="Expose search_code over MCP stdio")
    mcp.add_argument("--server", default="http://127.0.0.1:8750")
    for name in ("status", "refresh"):
        command = subparsers.add_parser(name)
        command.add_argument("--server", default="http://127.0.0.1:8750")
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--repo", type=Path)
    search.add_argument("--server", help="Reuse a running service instead of loading a model")
    search.add_argument("--source-root", default="src/flask")
    search.add_argument("--retrieval", choices=("bm25", "dense", "hybrid"), default="dense")
    search.add_argument("--candidates", type=int, default=4)
    search.add_argument("--max-results", type=int, default=5)
    search.add_argument("--source-budget", type=int, default=12000,
                        help="Maximum returned source characters; metadata is additional")
    search.add_argument("--token-budget", type=int, help="Maximum compact tool text tokens (cl100k_base)")
    search.add_argument("--include-dependencies", action="store_true", help="Use spare result slots for static callees")
    search.add_argument("--abstention-policy", type=Path, help="Standalone weak-match policy")
    reranking = search.add_mutually_exclusive_group()
    reranking.add_argument("--rerank", action="store_true", help="Score the candidate prefix with H2O")
    reranking.add_argument("--no-rerank", action="store_true", help="Compatibility alias for fast search (default)")
    search.add_argument("--lexical-only", action="store_true", help="BM25 without either model")
    search.add_argument("--index-cache", type=Path, default=Path(".cache/indexes"))
    search.add_argument("--url", default="http://127.0.0.1:8741", help="H2O URL for standalone search")
    search.add_argument("--json", action="store_true", help="Print structured results and timings")
    args = parser.parse_args(argv)
    if args.command == "benchmark":
        if args.candidates < 1 or args.max_lines <= 40:
            parser.error("Require positive --candidates and --max-lines > 40")
        result = run_experiment(args.repo, args.dataset, args.output, args.url,
                                args.candidates, args.max_lines)
        print(json.dumps(result["metrics"], indent=2))
    elif args.command == "shortlist-benchmark":
        from semantic_search.shortlist import run_shortlist_experiment
        run_shortlist_experiment(args.repo, args.dataset, args.output,
                                args.index_cache, args.url, args.prior_run)
        print(f"Shortlist benchmark saved to {args.output}")
    elif args.command == "mcp":
        from semantic_search.mcp_server import run_mcp
        run_mcp(args.server)
    elif args.command in {"status", "refresh"}:
        from semantic_search.client import SearchClient
        client = SearchClient(args.server)
        print(json.dumps(client.status() if args.command == "status" else client.refresh(), indent=2))
    elif args.command == "serve":
        if not 0 <= args.port <= 65535:
            parser.error("--port must be between 0 and 65535")
        if not math.isfinite(args.watch_interval) or args.watch_interval <= 0:
            parser.error("--watch-interval must be positive and finite")
        from semantic_search.backend import SearchBackend
        from semantic_search.server import create_server
        extra = {"abstention_policy": args.abstention_policy} if args.abstention_policy else {}
        backend = SearchBackend(args.repo, args.source_root,
                                index_path(args.repo, args.source_root, args.index_cache), h2o_url=args.url, **extra)
        server = None
        previous_handler = signal.signal(signal.SIGTERM, interrupt_service)
        try:
            backend.search("Find the implementation", max_results=1)
            server = create_server(backend, args.host, args.port)
            if not args.no_watch:
                backend.start_watching(poll_interval=args.watch_interval)
            print(f"Ready: http://{args.host}:{server.server_address[1]} (dense search; H2O optional)", flush=True)
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                if server is not None:
                    server.server_close()
                backend.close()
            finally:
                signal.signal(signal.SIGTERM, previous_handler)
    else:
        if not args.query.strip() or args.candidates < 1 or args.max_results < 1 or args.source_budget < 1:
            parser.error("Require a nonempty query and positive size/budget options")
        if bool(args.repo) == bool(args.server):
            parser.error("Use exactly one of --repo (standalone) or --server (resident service)")
        if args.lexical_only:
            args.retrieval, args.rerank = "bm25", False
        settings = {"retrieval": args.retrieval, "rerank": args.rerank,
                    "candidates": args.candidates, "max_results": args.max_results,
                    "source_budget": args.source_budget}
        if args.token_budget is not None:
            settings["token_budget"] = args.token_budget
        if args.include_dependencies:
            settings["include_dependencies"] = True
        if args.server:
            from semantic_search.client import SearchClient
            response = SearchClient(args.server).search(args.query, **settings)
        else:
            from semantic_search.backend import SearchBackend
            extra = {"abstention_policy": args.abstention_policy} if args.abstention_policy else {}
            backend = SearchBackend(args.repo, args.source_root,
                                    index_path(args.repo, args.source_root, args.index_cache), h2o_url=args.url, **extra)
            try:
                response = backend.search(args.query, **settings)
            finally:
                backend.close()
        if args.json:
            print(json.dumps(response, indent=2))
        else:
            print_results(response)


if __name__ == "__main__":
    main()
