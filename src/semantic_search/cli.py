"""Search saved code locally, or use a resident loopback search service."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import signal

from semantic_search.experiment import run_experiment


def index_path(repo, source_root, directory, source_exclusions=()):
    identity = str(repo.resolve()) + "\n" + source_root
    if source_exclusions:
        from semantic_search.languages import validate_exclusions
        identity += "\n" + json.dumps(validate_exclusions(source_exclusions))
    return directory / (hashlib.sha256(identity.encode()).hexdigest() + ".sqlite3")


def print_results(response):
    if response.get("match_status") == "insufficient_evidence":
        print("No strong implementation match found. Try a more specific query.")
    elif response.get("match_status") == "no_candidates":
        print("No indexed candidate matched this query.")
    for result in response["results"]:
        score = result.get("h2o_score")
        suffix = f" relevance={score:.3f}/3" if score is not None else ""
        if result.get("qwen_score") is not None:
            suffix += f" qwen_difference={result['qwen_score']:.3f}"
        if result.get("truncated"):
            suffix += " (excerpt truncated by context budget)"
        print(f"\n{result['path']}:{result['start_line']}-{result['end_line']} {result['symbol']}{suffix}")
        print(result["source"])
    if response.get("budget_exhausted"):
        usage = response.get("token_usage") or {}
        limits = []
        if type(response.get("source_budget")) is int:
            limits.append(f"--source-budget {response['source_budget']} characters")
        if type(usage.get("budget")) is int:
            limits.append(f"--token-budget {usage['budget']} tokens")
        caps = " (" + "; ".join(limits) + ")" if limits else ""
        if usage.get("truncated"):
            print(f"\nToken budget reached{caps}; increase --token-budget for more evidence.")
        else:
            print(f"\nSource budget reached{caps}; increase --source-budget for more evidence.")


def interrupt_service(signum, frame):
    raise KeyboardInterrupt


def print_progress(event):
    """One flushed, machine-readable line for a launcher following startup."""
    print("Progress: " + json.dumps(event, separators=(",", ":"), allow_nan=False), flush=True)


def start_qwen_owner(args):
    from semantic_search.qwen_reranker import QwenRerankerProcess
    owner = QwenRerankerProcess(python=args.qwen_reranker_python,
                               checkpoint=args.qwen_reranker_checkpoint)
    owner.start()
    return owner


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
    serve.add_argument("--exclude-source", action="append", default=[], metavar="GLOB",
                       help="Exclude a repository-relative saved-source glob (repeatable)")
    serve.add_argument("--index-cache", type=Path, default=Path(".cache/indexes"))
    serve.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8750)
    serve.add_argument("--url", default="http://127.0.0.1:8741", help="Optional H2O server URL")
    serve.add_argument("--enable-qwen-reranker", action="store_true",
                       help="Explicitly load the isolated pinned original B1 Qwen reranker")
    serve.add_argument("--qwen-reranker-python", type=Path)
    serve.add_argument("--qwen-reranker-checkpoint", type=Path)
    serve.add_argument("--no-watch", action="store_true", help="Discover changes only on searches/refreshes")
    serve.add_argument("--watch-interval", type=float, default=1.0)
    serve.add_argument("--progress", action="store_true",
                       help="Print indexing phase/count JSON lines for a local launcher")
    serve.add_argument("--abstention-policy", type=Path, help="Development-calibrated weak-match policy")
    mcp = subparsers.add_parser("mcp", help="Expose search_code over MCP stdio")
    mcp.add_argument("--server", default="http://127.0.0.1:8750")
    mcp.add_argument("--agent-text-only", action="store_true",
                     help="Send one compact JSON text copy without structured output")
    mcp.add_argument("--default-retrieval", choices=("bm25", "dense", "hybrid"), default="dense")
    mcp.add_argument("--default-rerank", action=argparse.BooleanOptionalAction, default=False)
    mcp.add_argument("--default-rerank-provider", choices=("h2o", "qwen"), default="h2o")
    mcp.add_argument("--default-candidates", type=int, default=4)
    for name in ("status", "refresh"):
        command = subparsers.add_parser(name)
        command.add_argument("--server", default="http://127.0.0.1:8750")
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--repo", type=Path)
    search.add_argument("--server", help="Reuse a running service instead of loading a model")
    search.add_argument("--source-root", default="src/flask")
    search.add_argument("--exclude-source", action="append", default=[], metavar="GLOB",
                        help="Exclude a saved-source glob for standalone --repo search")
    search.add_argument("--retrieval", choices=("bm25", "dense", "hybrid"), default="dense")
    search.add_argument("--candidates", type=int, default=4)
    search.add_argument("--max-results", type=int, default=5)
    search.add_argument("--source-budget", type=int, default=12000,
                        help="Maximum returned source characters; metadata is additional")
    search.add_argument("--token-budget", type=int, help="Maximum compact tool text tokens (cl100k_base)")
    search.add_argument("--include-dependencies", action="store_true", help="Use spare result slots for static callees")
    search.add_argument("--abstention-policy", type=Path, help="Standalone weak-match policy")
    reranking = search.add_mutually_exclusive_group()
    reranking.add_argument("--rerank", action="store_true", help="Score the candidate prefix with the selected local provider (H2O by default)")
    reranking.add_argument("--no-rerank", action="store_true", help="Compatibility alias for fast search (default)")
    search.add_argument("--rerank-provider", choices=("h2o", "qwen"), default="h2o")
    search.add_argument("--qwen-reranker-python", type=Path)
    search.add_argument("--qwen-reranker-checkpoint", type=Path)
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
        defaults = {"retrieval": args.default_retrieval, "rerank": args.default_rerank,
                    "rerank_provider": args.default_rerank_provider, "candidates": args.default_candidates}
        extra = {} if defaults == {"retrieval": "dense", "rerank": False,
            "rerank_provider": "h2o", "candidates": 4} else {"search_defaults": defaults}
        run_mcp(args.server, agent_text_only=args.agent_text_only, **extra)
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
        if args.exclude_source:
            extra["source_exclusions"] = args.exclude_source
        if args.progress:
            extra["progress"] = print_progress
        backend = server = owner = None
        previous_handler = signal.signal(signal.SIGTERM, interrupt_service)
        try:
            if args.enable_qwen_reranker:
                owner = start_qwen_owner(args)
                extra["qwen_client"] = owner.client
            elif args.qwen_reranker_python or args.qwen_reranker_checkpoint:
                parser.error("Qwen runtime options require --enable-qwen-reranker")
            backend = SearchBackend(args.repo, args.source_root,
                index_path(args.repo, args.source_root, args.index_cache, args.exclude_source),
                h2o_url=args.url, **extra)
            backend.search("Find the implementation", max_results=1)
            server = create_server(backend, args.host, args.port)
            if not args.no_watch:
                backend.start_watching(poll_interval=args.watch_interval)
            if args.progress:
                print_progress({"phase": "ready", "completed": 1, "total": 1,
                                "unit": "service"})
            print(f"Ready: http://{args.host}:{server.server_address[1]} (dense search; local reranking optional)", flush=True)
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                try:
                    if server is not None:
                        server.server_close()
                    if backend is not None:
                        backend.close()
                finally:
                    if owner is not None:
                        owner.close()
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
        if args.rerank_provider != "h2o":
            settings["rerank_provider"] = args.rerank_provider
        if args.token_budget is not None:
            settings["token_budget"] = args.token_budget
        if args.include_dependencies:
            settings["include_dependencies"] = True
        if args.server:
            if args.qwen_reranker_python or args.qwen_reranker_checkpoint:
                parser.error("Configure Qwen runtime options on the resident serve command")
            if args.abstention_policy:
                parser.error("Configure --abstention-policy on the resident serve command")
            if args.exclude_source:
                parser.error("--exclude-source configures standalone --repo or serve membership")
            from semantic_search.client import SearchClient
            response = SearchClient(args.server).search(args.query, **settings)
        else:
            from semantic_search.backend import SearchBackend
            extra = {"abstention_policy": args.abstention_policy} if args.abstention_policy else {}
            if args.exclude_source:
                extra["source_exclusions"] = args.exclude_source
            backend = owner = None
            previous_handler = None
            if args.rerank and args.rerank_provider == "qwen":
                previous_handler = signal.signal(signal.SIGTERM, interrupt_service)
            try:
                if args.rerank and args.rerank_provider == "qwen":
                    owner = start_qwen_owner(args)
                    extra["qwen_client"] = owner.client
                elif args.qwen_reranker_python or args.qwen_reranker_checkpoint:
                    parser.error("Qwen runtime options require --rerank --rerank-provider qwen")
                backend = SearchBackend(args.repo, args.source_root,
                    index_path(args.repo, args.source_root, args.index_cache, args.exclude_source),
                    h2o_url=args.url, **extra)
                response = backend.search(args.query, **settings)
            finally:
                try:
                    if backend is not None:
                        backend.close()
                finally:
                    try:
                        if owner is not None:
                            owner.close()
                    finally:
                        if previous_handler is not None:
                            signal.signal(signal.SIGTERM, previous_handler)
        if args.json:
            print(json.dumps(response, indent=2))
        else:
            print_results(response)


if __name__ == "__main__":
    main()
