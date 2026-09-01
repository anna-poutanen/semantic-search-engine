"""Reproducible retrieval and labeled-decoy evaluation on a pinned corpus."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import platform
import statistics
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
from dataclasses import asdict
from pathlib import Path

from semantic_search.corpus import extract_snippets
from semantic_search.model import H2OClient, INSTRUCTIONS, RUBRIC
from semantic_search.retrieval import BM25Retriever


def matches(snippet, target):
    if snippet.path != target["path"] or snippet.symbol != target["symbol"]:
        return False
    evidence = target.get("evidence_lines")
    return evidence is None or (snippet.start_line <= evidence[0] and snippet.end_line >= evidence[1])


def rank_of(ranking, targets):
    return next((rank for rank, snippet in enumerate(ranking, 1)
                 if any(matches(snippet, target) for target in targets)), None)


def unique_symbols(ranking):
    seen, output = set(), []
    for snippet in ranking:
        key = (snippet.path, snippet.symbol)
        if key not in seen:
            output.append(snippet)
            seen.add(key)
    return output


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def retrieval_metrics(rows, key):
    ranks = [row[key] for row in rows if row["kind"] == "present"]
    count = len(ranks)
    return {"queries": count,
            "hit_at_1": sum(rank == 1 for rank in ranks) / count,
            "hit_at_3": sum(rank is not None and rank <= 3 for rank in ranks) / count,
            "hit_at_5": sum(rank is not None and rank <= 5 for rank in ranks) / count,
            "mrr": sum(1 / rank if rank else 0 for rank in ranks) / count}


def sampled_rss_bytes(pids):
    """Sample facade + shim resident size; not a peak or total system memory."""
    output = subprocess.run(["ps", "-p", ",".join(str(pid) for pid in pids), "-o", "rss="], capture_output=True,
                            text=True, check=True).stdout
    return sum(int(line.strip()) * 1024 for line in output.splitlines() if line.strip())


def runtime_provenance(url):
    """Require the managed local runtime so metadata and RSS describe this run."""
    root = Path(__file__).resolve().parents[2]
    state = json.loads((root / ".cache/server/processes.json").read_text())
    parsed = urlsplit(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.port != state["port"]:
        raise ValueError("Benchmark URL must match the local scripts/serve_mac.py runtime")
    health = {}
    for name, port in (("shim", state["port"]), ("facade", state["facade_port"])):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as response:
            health[name] = json.load(response)
    if health["facade"]["prefix_min"] != 0:
        raise ValueError("This benchmark requires disabled facade prefix caching")
    config = json.loads((root / ".cache/models/h2o-lightning-4b-mlx-q8/config.json").read_text())
    vendor_revision = subprocess.check_output([
        "git", "-C", str(root / ".cache/vendor/h2o-lightning-mlx"), "rev-parse", "HEAD"
    ], text=True).strip()
    if vendor_revision != "0fc8e0299608867746682a0552c906b03a284a47":
        raise ValueError("Unexpected facade runtime revision; run scripts/setup_mac.py")
    return state, {"health_before_run": health, "config_quantization": config.get("quantization"),
                   "head_dtype": config.get("head_dtype"), "runtime_revision": vendor_revision}


def run_experiment(repo, dataset_path, output_dir, url, candidates=10, max_lines=160):
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if revision != dataset["revision"]:
        raise ValueError(f"Corpus revision mismatch: {revision} != {dataset['revision']}")
    dirty = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True)
    if dirty:
        raise ValueError("Benchmark corpus must have a clean working tree")
    if not any(query["kind"] == "present" for query in dataset["queries"]):
        raise ValueError("Dataset needs present queries for retrieval metrics")
    runtime_state, provenance = runtime_provenance(url)
    output_dir.mkdir(parents=True, exist_ok=False)
    snippets = extract_snippets(repo, dataset["source_root"], max_lines=max_lines, overlap_lines=40)
    index = BM25Retriever(snippets)
    client = H2OClient(url)
    rows, calls, rss_samples = [], [], []
    memo = {}
    started = time.perf_counter()
    with (output_dir / "judgments.jsonl").open("w") as audit:
        def judge(query, snippet, phase):
            key = (query["id"], snippet.id)
            if key in memo:
                return memo[key]
            judgment = client.score(query["query"], snippet)
            record = {"query_id": query["id"], "phase": phase,
                      "snippet": asdict(snippet), "score": judgment.score,
                      "elapsed_s": judgment.elapsed_s, "input_tokens": judgment.input_tokens,
                      "response": judgment.response}
            audit.write(json.dumps(record) + "\n")
            audit.flush()
            memo[key] = judgment
            calls.append(judgment)
            rss_samples.append(sampled_rss_bytes(runtime_state["pids"]))
            return judgment

        for number, query in enumerate(dataset["queries"], 1):
            query_start = time.perf_counter()
            pairs = index.search(query["query"], candidates)
            scored = [(snippet, lexical, judge(query, snippet, "retrieval"))
                      for snippet, lexical in pairs]
            reranked = sorted(scored, key=lambda item: (-item[2].score, -item[1], item[0].id))
            lexical_order = unique_symbols([item[0] for item in pairs])
            model_order = unique_symbols([item[0] for item in reranked])
            row = {"id": query["id"], "query": query["query"], "kind": query["kind"],
                   "bm25_rank": rank_of(lexical_order, query["targets"]),
                   "h2o_rank": rank_of(model_order, query["targets"]),
                   "candidate_has_gold": any(matches(snippet, target)
                                             for snippet, _ in pairs for target in query["targets"]),
                   "search_elapsed_s": time.perf_counter() - query_start,
                   "bm25_order": [s.id for s in lexical_order],
                   "h2o_order": [s.id for s in model_order],
                   "scores": [{"id": s.id, "bm25": lexical, "h2o": j.score,
                               "probabilities": j.probabilities} for s, lexical, j in reranked]}
            if query["kind"] == "present":
                labeled = []
                for role, targets in (("gold", query["targets"]), ("decoy", query.get("decoys", []))):
                    for target in targets:
                        options = [s for s in snippets if matches(s, target)]
                        if not options:
                            raise ValueError(f"No implementation window for {query['id']}: {target}")
                        # Independent of model outputs: choose the earliest full evidence window.
                        snippet = min(options, key=lambda s: s.start_line)
                        judgment = judge(query, snippet, "diagnostic")
                        labeled.append({"role": role, "id": snippet.id, "score": judgment.score,
                                        "probabilities": judgment.probabilities})
                row["diagnostic"] = labeled
                gold = max(item["score"] for item in labeled if item["role"] == "gold")
                decoy = max(item["score"] for item in labeled if item["role"] == "decoy")
                row["gold_beats_decoys"] = gold > decoy
                row["gold_margin"] = gold - decoy
            rows.append(row)
            print(f"[{number}/{len(dataset['queries'])}] {query['id']}: BM25={row['bm25_rank']} "
                  f"H2O={row['h2o_rank']} latency={row['search_elapsed_s']:.2f}s", flush=True)

    positives = [row for row in rows if row["kind"] == "present"]
    warm_calls = [call.elapsed_s for call in calls[1:]]
    warm_queries = [row["search_elapsed_s"] for row in rows[1:]]
    versions = {}
    for package in ("mlx", "mlx-metal", "mlx-vlm", "transformers", "huggingface-hub", "numpy"):
        versions[package] = importlib.metadata.version(package)
    result = {
        "schema_version": 1,
        "corpus": {"url": dataset["repo_url"], "revision": revision,
                   "source_root": dataset["source_root"], "snippets": len(snippets)},
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "model": {"id": "netprojects/h2o-lightning-4b-mlx-q8",
                  "revision": "a1048d02d9e713ba9f7481efa5c3df7ec8a2adaa",
                  "runtime_revision": "0fc8e0299608867746682a0552c906b03a284a47",
                  "bits": 8, "group_size": 64, "prefix_cache": False,
                  "rubric": RUBRIC, "instructions": INSTRUCTIONS, "provenance": provenance},
        "environment": {"platform": platform.platform(), "python": platform.python_version(),
                        "packages": versions, "machine": platform.machine()},
        "settings": {"candidates": candidates, "max_lines": max_lines, "overlap_lines": 40,
                     "include_docstrings": True, "deduplication": "highest-ranked window per symbol"},
        "metrics": {"bm25": retrieval_metrics(rows, "bm25_rank"),
                    "bm25_h2o": retrieval_metrics(rows, "h2o_rank"),
                    "candidate_coverage": sum(r['candidate_has_gold'] for r in positives) / len(positives),
                    "gold_beats_both_decoys": sum(r['gold_beats_decoys'] for r in positives),
                    "diagnostic_queries": len(positives), "forward_passes": len(calls),
                    "first_request_s": calls[0].elapsed_s,
                    "warm_request_median_s": statistics.median(warm_calls) if warm_calls else None,
                    "warm_request_p95_s": percentile(warm_calls, .95),
                    "warm_search_median_s": statistics.median(warm_queries) if warm_queries else None,
                    "warm_search_p95_s": percentile(warm_queries, .95),
                    "max_sampled_combined_rss_bytes": max(rss_samples),
                    "total_elapsed_s": time.perf_counter() - started},
        "queries": rows,
        "limitations": ["Developer-authored development pilot; not held out.",
                        "BM25 candidate retrieval only; embeddings and hybrid retrieval not built yet.",
                        "Docstrings are included; unseen repositories and docstring ablations are needed.",
                        "Top-window symbol deduplication can discard a lower-ranked implementation window.",
                        "RSS is sampled after requests, not true peak or total system memory.",
                        "Absence queries do not establish a calibrated rejection threshold.",
                        "No paid-agent token savings are measured in this experiment."],
    }
    (output_dir / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    (output_dir / "report.md").write_text(render_report(result))
    return result


def render_report(result):
    m = result["metrics"]
    lines = ["# H2O q8 / Flask development experiment", "",
             f"Corpus: `{result['corpus']['revision']}`; {result['corpus']['snippets']} windows.", "",
             "| Metric | BM25 | Same candidates + H2O |", "|---|---:|---:|"]
    for key in ("hit_at_1", "hit_at_3", "hit_at_5", "mrr"):
        lines.append(f"| {key} | {m['bm25'][key]:.3f} | {m['bm25_h2o'][key]:.3f} |")
    lines += ["", f"Candidate coverage: {m['candidate_coverage']:.1%}. Gold beats both labeled decoys: "
              f"{m['gold_beats_both_decoys']}/{m['diagnostic_queries']}.", "",
              f"First request: {m['first_request_s']:.2f}s. Warm per-candidate median/p95: "
              f"{m['warm_request_median_s']:.2f}/{m['warm_request_p95_s']:.2f}s. "
              f"Warm complete-search median/p95: {m['warm_search_median_s']:.2f}/{m['warm_search_p95_s']:.2f}s.", "",
              f"Maximum sampled combined facade/shim CPU RSS: {m['max_sampled_combined_rss_bytes'] / 2**30:.2f} GiB. "
              "This excludes Metal allocations and must not be interpreted as model RAM.", "",
              "| Query | BM25 rank | H2O rank | Gold minus strongest decoy |", "|---|---:|---:|---:|"]
    for row in result["queries"]:
        margin = f"{row['gold_margin']:.3f}" if "gold_margin" in row else "absent feature"
        bm25_rank = (row['bm25_rank'] or 'miss') if row['kind'] == 'present' else 'n/a'
        h2o_rank = (row['h2o_rank'] or 'miss') if row['kind'] == 'present' else 'n/a'
        lines.append(f"| {row['id']} | {bm25_rank} | {h2o_rank} | {margin} |")
    lines += ["", "## Absent-feature inspection", "",
              "No rejection threshold was fitted. These are the highest expected ordinal scores among "
              "the retrieved candidates (0 = unrelated, 3 = direct implementation).", ""]
    for row in result["queries"]:
        if row['kind'] == 'absent':
            lines.append(f"- {row['id']}: {row['scores'][0]['h2o']:.3f}/3; `{row['scores'][0]['id']}`.")
    if "memory_footprint" in result:
        memory = result["memory_footprint"]["processes"]["facade"]
        lines += ["", "## Mac memory footprint", "",
                  f"The facade's physical footprint was {memory['physical_footprint']} "
                  f"(peak since launch: {memory['physical_footprint_peak']}) according to `vmmap -summary`.",
                  "This includes Metal allocations. Values are rounded by the macOS tool; the peak belongs "
                  "to this process, not total system memory. Captured after the completed run."]
    lines += ["", "## Limits", "", *[f"- {item}" for item in result["limitations"]], "",
              "Exact ranked windows, model distributions and timings are in `results.json` and `judgments.jsonl`.", ""]
    return "\n".join(lines)
