"""Frozen-query checks of the actual resident HTTP search pipeline."""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import threading
import time
from dataclasses import asdict
from pathlib import Path
import subprocess

from semantic_search.backend import SearchBackend
from semantic_search.client import SearchClient
from semantic_search.corpus import Snippet, extract_snippets
from semantic_search.embeddings import QwenEmbedder
from semantic_search.experiment import matches, percentile, rank_of, runtime_provenance
from semantic_search.model import H2OClient, INSTRUCTIONS, RUBRIC
from semantic_search.server import create_server
from semantic_search.shortlist import machine_provenance, memory_footprints, metrics_for_ranks

CONDITIONS = [("bm25", False, 4), ("dense", False, 4), ("hybrid", False, 3),
              ("dense", True, 4), ("hybrid", True, 3)]


class AuditClient(H2OClient):
    def __init__(self, url):
        super().__init__(url)
        self.records = []
        self.context = {}

    def score(self, query, snippet):
        judgment = super().score(query, snippet)
        self.records.append({**self.context, "query": query, "snippet": asdict(snippet),
                             **asdict(judgment)})
        return judgment


def returned_snippets(response):
    return [Snippet(row["id"], row["path"], row["symbol"], row["start_line"],
                    row["end_line"], row["source"], row["source_hash"])
            for row in response["results"]]


def run_service_experiment(repo, dataset_path, output_dir, db_path, h2o_url=None):
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True).strip()
    if revision != dataset["revision"] or dirty:
        raise ValueError("Evaluation corpus must be clean and match its frozen revision")
    snippets = extract_snippets(repo, dataset["source_root"], max_lines=160, overlap_lines=40)
    if not any(q["kind"] == "present" for q in dataset["queries"]):
        raise ValueError("Dataset needs present implementation queries")
    for query in dataset["queries"]:
        for target in query["targets"]:
            if not any(matches(s, target) for s in snippets):
                raise ValueError(f"No full-evidence corpus window: {query['id']}")
    output_dir.mkdir(parents=True, exist_ok=False)
    state, provenance = runtime_provenance(h2o_url) if h2o_url else (None, None)
    scorer = AuditClient(h2o_url) if h2o_url else None
    embedder = QwenEmbedder()
    backend = SearchBackend(repo, dataset["source_root"], db_path, embedder=embedder,
                            h2o_client=scorer)
    server = worker = None
    try:
        started = time.perf_counter()
        warm_search = backend.search("Find the code implementation", max_results=1)
        setup_s = time.perf_counter() - started
        if scorer:
            scorer.context = {"phase": "warmup"}
            scorer.score("Find the code implementation", snippets[0])
        (output_dir / "warmup.json").write_text(json.dumps({"setup_s": setup_s,
            "indexstats": warm_search["indexstats"], "query": "Find the code implementation",
            "h2o": scorer.records if scorer else [],
            "note": "Model load, indexing, and these warmup calls are excluded from measured searches."}, indent=2)+"\n")
        server = create_server(backend, port=0)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05})
        worker.start()
        client = SearchClient(f"http://127.0.0.1:{server.server_address[1]}")
        conditions = CONDITIONS if scorer else CONDITIONS[:3]
        rows = []
        with (output_dir / "judgments.jsonl").open("w") as audit:
            for number, query in enumerate(dataset["queries"], 1):
                offset = (number-1) % len(conditions)
                for retrieval, rerank, size in conditions[offset:] + conditions[:offset]:
                    if scorer:
                        scorer.records = []
                        scorer.context = {"phase": "measured", "query_id": query["id"],
                                          "retrieval": retrieval, "rerank": rerank, "k": size}
                    started = time.perf_counter()
                    response = client.search(query["query"], retrieval=retrieval, rerank=rerank,
                                             candidates=size, max_results=5, source_budget=12000)
                    elapsed_s = time.perf_counter()-started
                    records = list(scorer.records) if scorer else []
                    if len(records) != (size if rerank else 0):
                        raise ValueError("Fresh scorer-call count differs from declared shortlist")
                    returned = returned_snippets(response)
                    row = {"query_id": query["id"], "kind": query["kind"],
                           "split": query.get("split", "development"), "retrieval": retrieval,
                           "rerank": rerank, "k": size if rerank else 0,
                           "elapsed_http_s": elapsed_s, "rank": rank_of(returned, query["targets"]),
                           "scored_candidate_has_gold": any(matches(Snippet(**r["snippet"]), t)
                                for r in records for t in query["targets"]) if rerank else None,
                           "max_h2o_score": max((r["score"] for r in records), default=None),
                           "response": response}
                    rows.append(row)
                    for record in records:
                        audit.write(json.dumps(record)+"\n")
                    audit.flush()
                    print(f"[{number}/{len(dataset['queries'])}] {retrieval} K{row['k']} "
                          f"{query['id']}: rank={row['rank']} HTTP={elapsed_s:.3f}s", flush=True)
        summary = {}
        for retrieval, rerank, size in conditions:
            k = size if rerank else 0
            selected = [r for r in rows if r["retrieval"] == retrieval and r["k"] == k]
            metrics = {}
            for split in ("all", "development", "test"):
                group = [r for r in selected if split == "all" or r["split"] == split]
                positives = [r for r in group if r["kind"] == "present"]
                if not positives:
                    continue
                timings = [r["elapsed_http_s"] for r in group]
                metrics[split] = {**metrics_for_ranks([r["rank"] for r in positives]),
                                 "samples": len(timings), "median_s": statistics.median(timings),
                                 "p95_s": percentile(timings, .95), "max_s": max(timings)}
            summary[f"{retrieval}:{k}"] = metrics
        pids = {"search_service": os.getpid()}
        if state:
            pids.update({"h2o_facade": state["pids"][0], "h2o_shim": state["pids"][1]})
        result = {"dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
                  "corpus": {"revision": revision, "url": dataset["repo_url"], "windows": len(snippets)},
                  "machine": machine_provenance(), "embedding": embedder.metadata,
                  "h2o": {"checkpoint": {"id": "netprojects/h2o-lightning-4b-mlx-q8",
                                          "revision": "a1048d02d9e713ba9f7481efa5c3df7ec8a2adaa"}
                                          if scorer else None,
                          "provenance": provenance, "rubric": RUBRIC, "instructions": INSTRUCTIONS},
                  "conditions": conditions, "setup_s": setup_s,
                  "warm_models": True, "query_cache": False, "h2o_cache": False,
                  "output": {"max_results": 5, "source_budget_chars": 12000,
                             "whole_line_truncation": True, "only_scored_prefix_if_reranking": True},
                  "summary": summary, "rows": rows, "memory": memory_footprints(pids),
                  "limits": ["20 authored queries in one additional repository, not representative quality.",
                             "Test split was frozen before retrieval; no K, weights, or prompt tuning in this run.",
                             "Latency includes HTTP JSON response and source freshness checks; excludes setup.",
                             "Character budget is not a token budget; no agent token savings measured.",
                             "Absent queries still return candidates; no rejection threshold fitted."]}
        (output_dir / "results.json").write_text(json.dumps(result, indent=2)+"\n")
        (output_dir / "report.md").write_text(render_service_report(result))
        return result
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if worker:
            worker.join(2)
        backend.close()


def render_service_report(result):
    lines = ["# Requests: resident search pipeline", "",
             "Queries and development/test assignments were frozen before seeing retrieval results. "
             "Returned hits require the actual delivered excerpt to contain the full executable evidence interval.", "",
             "| Mode | Split | Present queries | Correct first | Within returned results (≤5) | Median HTTP | P95 |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for name, metrics in result["summary"].items():
        for split, m in metrics.items():
            n = m["queries"]
            lines.append(f"| {name} | {split} | {n} | {round(m['hit_at_1']*n)}/{n} | "
                         f"{round(m['hit_at_5']*n)}/{n} | {m['median_s']:.3f}s | {m['p95_s']:.3f}s |")
    lines += ["", f"Initial setup and indexing: {result['setup_s']:.2f}s. "
              "Every measured query embedding and H2O score is new; GPU work is serialized. "
              "Condition order rotates per query. Source freshness checks, JSON serialization, "
              "and loopback round-trip are included. Raw audit disk writes are outside the measured interval.", "",
              "Latency includes absent queries: 20 samples per configuration overall, 10 per split. "
              "Nearest-rank P95 is the 19th of 20 overall and the maximum of 10 within a split. "
              "Quality denominators contain only present queries.", ""]
    checkpoint = result.get("h2o", {}).get("checkpoint")
    if checkpoint:
        lines += [f"H2O checkpoint: `{checkpoint['id']}` at `{checkpoint['revision']}`. "
                  "The managed MLX runtime uses 8-bit affine quantization and the FP32 decision head.", ""]
    lines += ["## Memory", ""]
    for name, m in result["memory"]["processes"].items():
        lines.append(f"- {name}: current {m['current']}, peak {m['peak']} (`vmmap -summary`, includes Metal).")
    lines += ["", result["memory"]["note"], "", "## Limits", "",
              *[f"- {limit}" for limit in result["limits"]], ""]
    return "\n".join(lines)
