"""Frozen multi-repository evaluation of delivered code and development calibration."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import statistics
import subprocess
import threading
import time

import numpy as np

from semantic_search.backend import SearchBackend
from semantic_search.client import SearchClient
from semantic_search.corpus import Snippet, extract_snippets
from semantic_search.embeddings import QwenEmbedder
from semantic_search.experiment import matches, percentile
from semantic_search.matching import calibrate_policy
from semantic_search.index import DOCUMENT_FORMAT
from semantic_search.server import create_server
from semantic_search.shortlist import machine_provenance
from semantic_search.context import encoding
from semantic_search.source import physical_lines

CONDITIONS = [
    {"name": "fts5", "retrieval": "bm25", "max_results": 5, "token_budget": 4096},
    {"name": "dense", "retrieval": "dense", "max_results": 5, "token_budget": 4096},
    {"name": "hybrid", "retrieval": "hybrid", "max_results": 5, "token_budget": 4096},
    {"name": "dense_context", "retrieval": "dense", "max_results": 8,
     "include_dependencies": True, "token_budget": 4096},
]


def target_groups(query):
    if query.get("kind") not in ("present", "absent"):
        raise ValueError("Query kind must be present or absent")
    if query["kind"] == "absent":
        if query.get("targets", []) != [] or "target_groups" in query:
            raise ValueError("Absent queries cannot have executable gold targets")
        return []
    if "target_groups" in query:
        if "targets" in query:
            raise ValueError("Use targets OR target_groups, not both")
        groups = query["target_groups"]
    else:
        groups = [query.get("targets")]
    if not isinstance(groups, list) or not groups or any(not isinstance(g, list) or not g for g in groups):
        raise ValueError("Every present query needs nonempty executable evidence groups")
    for group in groups:
        for target in group:
            if not isinstance(target, dict):
                raise ValueError("Gold targets must be objects")
            for field in ("path", "symbol"):
                if not isinstance(target.get(field), str) or not target[field].strip():
                    raise ValueError("Gold targets require a nonblank path and executable symbol")
            path = Path(target["path"])
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Gold paths must be repository-relative")
            bounds = target.get("evidence_lines")
            if (not isinstance(bounds, list) or len(bounds) != 2
                    or any(type(line) is not int for line in bounds)
                    or not 1 <= bounds[0] <= bounds[1]):
                raise ValueError("Gold targets require an ordered, positive evidence_lines interval")
    return groups


def evidence_ranks(snippets, groups):
    return [next((rank for rank, snippet in enumerate(snippets, 1)
                  if any(matches(snippet, target) for target in group)), None) for group in groups]


def delivered_snippets(response):
    snippets = []
    for row in response["results"]:
        source, start, end = row["source"], row["start_line"], row["end_line"]
        if (not isinstance(source, str) or not source or type(start) is not int
                or type(end) is not int or start < 1
                or end != start + len(physical_lines(source)) - 1
                or row["source_hash"] != hashlib.sha256(source.encode("utf-8")).hexdigest()):
            raise ValueError("Delivered excerpt has inconsistent source, line bounds, or SHA-256")
        snippets.append(Snippet(row["id"], row["path"], row["symbol"], start, end,
                                source, row["source_hash"]))
    return snippets


def summarize(rows):
    output = {}
    for condition in sorted({row["condition"] for row in rows}):
        selected = [row for row in rows if row["condition"] == condition]
        groups = {"all": selected,
                  "development": [r for r in selected if r["role"] != "repository_holdout"],
                  "development_queries": [r for r in selected if r["role"] != "repository_holdout"
                                          and r["split"] != "calibration"],
                  "calibration": [r for r in selected if r["role"] != "repository_holdout"
                                  and r["split"] == "calibration"],
                  "repository_holdout": [r for r in selected if r["role"] == "repository_holdout"]}
        groups.update({f"repository:{dataset}": [r for r in selected if r.get("dataset") == dataset]
                       for dataset in sorted({r["dataset"] for r in selected if "dataset" in r})})
        metrics = {}
        for group, group_rows in groups.items():
            if not group_rows:
                continue
            positives = [r for r in group_rows if r["kind"] == "present"]
            negatives = [r for r in group_rows if r["kind"] == "absent"]
            multi = [r for r in positives if len(r["evidence_ranks"]) > 1]
            single = [r for r in positives if len(r["evidence_ranks"]) == 1]
            times = [r["elapsed_http_s"] for r in group_rows]
            completions = [max(r["evidence_ranks"]) if all(r["evidence_ranks"]) else None
                           for r in positives]
            tokens = [r["response"].get("token_usage", {}).get("tokens") for r in group_rows]
            tokens = [value for value in tokens if type(value) is int]
            metrics[group] = {"queries": len(group_rows), "present": len(positives), "absent": len(negatives),
                "candidate_complete": sum(all(r["candidate_evidence_ranks"]) for r in positives),
                "delivered_complete": sum(all(r["evidence_ranks"]) for r in positives),
                "single_first": sum(r["evidence_ranks"] == [1] for r in single), "single_queries": len(single),
                "multi_file_complete": sum(all(r["evidence_ranks"]) for r in multi), "multi_file_queries": len(multi),
                "absent_rejected": sum(r["response"]["match_status"] == "insufficient_evidence" for r in negatives),
                "absent_accepted": sum(r["response"]["match_status"] == "candidates" for r in negatives),
                "absent_no_candidates": sum(r["response"]["match_status"] == "no_candidates" for r in negatives),
                "present_rejected": sum(r["response"]["match_status"] == "insufficient_evidence" for r in positives),
                **{f"hit_at_{k}": sum(rank is not None and rank <= k for rank in completions) / len(positives)
                   if positives else None for k in (1, 3, 5)},
                "mrr": sum(1 / rank for rank in completions if rank is not None) / len(positives) if positives else None,
                "required_groups": sum(len(r["evidence_ranks"]) for r in positives),
                "delivered_groups": sum(sum(rank is not None for rank in r["evidence_ranks"]) for r in positives),
                "candidate_groups": sum(sum(rank is not None for rank in r["candidate_evidence_ranks"]) for r in positives),
                "timing_samples": len(times),
                "median_tokens": statistics.median(tokens) if tokens else None,
                "max_tokens": max(tokens) if tokens else None,
                "token_truncated_queries": sum(r["response"].get("token_usage", {}).get("truncated", False) for r in group_rows),
                "budget_exhausted_queries": sum(r["response"].get("budget_exhausted", False) for r in group_rows),
                "median_source_chars": statistics.median(sum(len(item["source"]) for item in r["response"].get("results", []))
                                                         for r in group_rows),
                "median_s": statistics.median(times), "p95_s": percentile(times, .95), "max_s": max(times)}
        output[condition] = metrics
    return output


class RecordingEmbedder(QwenEmbedder):
    def embed_query(self, query):
        vector = super().embed_query(query)
        self.last_query = np.asarray(vector).copy()
        return vector


def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def run_quality(manifest_path, output_dir, *, limit_role=None):
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    output_dir.mkdir(parents=True, exist_ok=False)
    frozen = {"manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
              "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
              "conditions": CONDITIONS, "candidate_depth": 50, "source_budget_chars": 12000,
              "unreranked_retrieval_depth": 50,
              "extraction": {"max_lines": 160, "overlap_lines": 40},
              "fusion": {"weights": "equal", "rrf_k": 60, "fetch_depth": 50},
              "dependencies": {"anchors": 5, "max_callees_per_anchor": 2, "depth": 1},
              "tokenizer": "cl100k_base", "query_cache": False,
              "embedding": QwenEmbedder(progress=False).metadata,
              "document_format": DOCUMENT_FORMAT,
              "deduplication": "First ranked window per exact (path, qualified symbol) within the retrieved 50 windows.",
              "packing": "Ranked source prefixes, complete physical CR/LF lines; exact compact UTF-8 JSON including metadata and token_usage.",
              "ties": "SQLite distance/FTS score then snippet ID; equal-weight RRF then snippet ID.",
              "client_timeout_s": 180,
              "cancellation": "Cooperative between requests, embedding batches, and H2O calls; no single Metal-forward preemption.",
              "packages": {name: package_version(name) for name in ("sqlite-vec", "tiktoken", "mcp")},
              "implementation_sha256": {str(path.relative_to(Path(__file__).resolve().parents[2])):
                                        hashlib.sha256(path.read_bytes()).hexdigest()
                                        for path in sorted(Path(__file__).parent.glob("*.py"))},
              "abstention": "Fit only declared development calibration rows; retain every calibration positive.",
              "limits": ["Developer-authored source-reviewed queries; not representative user tasks.",
                         "Repository holdout applies to this evaluation, not model pretraining.",
                         "Token counts describe one compact tool payload, not provider billing.",
                         "Insufficient evidence does not prove behavior absent."]}
    (output_dir / "frozen-config.json").write_text(json.dumps(frozen, indent=2) + "\n")
    # Validate every pinned corpus and gold before any inference, including the
    # held-out repository. Validation is source review, not retrieval feedback.
    entries = []
    for entry in manifest["datasets"]:
        if limit_role is not None and entry["role"] != limit_role:
            continue
        dataset_path, repo = Path(entry["dataset"]), Path(entry["corpus"])
        data = dataset_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"Dataset changed after freeze: {dataset_path}")
        dataset = json.loads(data)
        revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True).strip()
        if revision != dataset["revision"] or dirty:
            raise ValueError(f"Corpus must match frozen clean revision: {repo}")
        snippets = extract_snippets(repo, dataset["source_root"], max_lines=160, overlap_lines=40)
        for query in dataset["queries"]:
            if any(not rank for rank in evidence_ranks(snippets, target_groups(query))):
                raise ValueError(f"Missing full-evidence source window: {query['id']}")
        entries.append((entry, dataset))
    entries.sort(key=lambda pair: pair[0]["role"] == "repository_holdout")
    rows, setups, calibration, policy = [], [], [], None
    with (output_dir / "searches.jsonl").open("w") as raw:
        for entry, dataset in entries:
            if entry["role"] == "repository_holdout" and policy is None:
                if not calibration:
                    raise ValueError("Evaluate declared development calibration before repository holdout")
                policy = calibrate_policy(calibration, embedding_metadata)
                policy["evaluation"] = {"manifest_sha256": frozen["manifest_sha256"],
                    "frozen_config_sha256": hashlib.sha256((output_dir / "frozen-config.json").read_bytes()).hexdigest()}
                (output_dir / "abstention-policy.json").write_text(json.dumps(policy, indent=2) + "\n")
            embedder = RecordingEmbedder(progress=False)
            embedding_metadata = embedder.metadata
            backend = SearchBackend(entry["corpus"], dataset["source_root"],
                Path(".cache/indexes") / (Path(entry["dataset"]).stem + "-quality.sqlite3"), embedder=embedder)
            server = worker = None
            try:
                start = time.perf_counter()
                encoding()  # Token vocabulary/setup is excluded from warm request timings.
                warm = backend.search("Find the implementation", max_results=1)
                setups.append({"dataset": entry["dataset"], "elapsed_s": time.perf_counter() - start,
                               "indexstats": warm["indexstats"]})
                server = create_server(backend, port=0)
                worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05})
                worker.start()
                client = SearchClient(f"http://127.0.0.1:{server.server_address[1]}")
                for number, query in enumerate(dataset["queries"]):
                    conditions = CONDITIONS[number % len(CONDITIONS):] + CONDITIONS[:number % len(CONDITIONS)]
                    for condition in conditions:
                        settings = {key: value for key, value in condition.items() if key != "name"}
                        start = time.perf_counter()
                        response = client.search(query["query"], source_budget=12000, **settings)
                        elapsed = time.perf_counter() - start
                        # Diagnostics reuse this exact new query vector; no extra
                        # model call and no labels enter candidate retrieval.
                        if settings["retrieval"] == "bm25":
                            candidates = backend._lexical.search(query["query"], limit=50)
                        elif settings["retrieval"] == "dense":
                            candidates = backend._dense.search(embedder.last_query, limit=50)
                        else:
                            candidates = backend._hybrid.search(query["query"], embedder.last_query, limit=50)
                        groups = target_groups(query)
                        row = {"dataset": entry["dataset"], "dataset_sha256": entry["sha256"],
                               "query_id": query["id"], "role": entry["role"], "kind": query["kind"],
                               "split": query.get("split", "development"), "condition": condition["name"],
                               "elapsed_http_s": elapsed, "response": response,
                               "evidence_ranks": evidence_ranks(delivered_snippets(response), groups),
                               "candidate_evidence_ranks": evidence_ranks([s for s, _ in candidates], groups),
                               "candidate_windows": [{"id": s.id, "score": float(score)} for s, score in candidates],
                               "top_cosine": response["match_evidence"]["top_cosine"]}
                        rows.append(row)
                        raw.write(json.dumps(row) + "\n")
                        raw.flush()
                        if condition["name"] == "dense_context" and row["split"] == "calibration":
                            calibration.append(row)
                        print(f"{Path(entry['dataset']).stem} {number+1}/{len(dataset['queries'])} "
                              f"{condition['name']} complete={all(row['evidence_ranks']) if groups else 'absent'} "
                              f"HTTP={elapsed:.3f}s", flush=True)
                    # Calibrated production path is measured fresh only on the
                    # repository holdout. Baseline conditions stay unfiltered.
                    if entry["role"] == "repository_holdout":
                        from semantic_search.matching import MatchPolicy
                        backend._policy = MatchPolicy(policy)
                        start = time.perf_counter()
                        response = client.search(query["query"], retrieval="dense", max_results=8,
                                                 include_dependencies=True, token_budget=4096)
                        elapsed = time.perf_counter() - start
                        backend._policy = None
                        policy_row = {**row, "condition": "dense_context_policy", "response": response,
                                      "elapsed_http_s": elapsed,
                                      "evidence_ranks": evidence_ranks(delivered_snippets(response), groups),
                                      "top_cosine": response["match_evidence"]["top_cosine"]}
                        # Reconstruct policy-row diagnostics from its own vector.
                        candidates = backend._dense.search(embedder.last_query, limit=50)
                        policy_row["candidate_evidence_ranks"] = evidence_ranks([s for s, _ in candidates], groups)
                        policy_row["candidate_windows"] = [{"id": s.id, "score": float(score)} for s, score in candidates]
                        rows.append(policy_row)
                        raw.write(json.dumps(policy_row) + "\n")
                        raw.flush()
            finally:
                if server:
                    server.shutdown()
                    server.server_close()
                if worker:
                    worker.join(2)
                backend.close()
    result = {"frozen": frozen, "machine": machine_provenance(), "embedding": embedding_metadata,
              "setups": setups, "summary": summarize(rows), "policy": policy, "search_rows": len(rows)}
    (output_dir / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    (output_dir / "report.md").write_text(render_report(result))
    return result


def render_report(result):
    lines = ["# Multi-repository retrieval quality", "",
        "Source labels and configurations were frozen before inference. Complete delivery requires every "
        "AND evidence group in the actual returned excerpts. Candidate coverage uses 50 full windows.", "",
        "| Mode | Group | Present | Candidate complete | Delivered complete | Multi-file complete | Absent rejected | Median / P95 HTTP |",
        "|---|---|---:|---:|---:|---:|---:|---:|"]
    for name, groups in result["summary"].items():
        for group, m in groups.items():
            lines.append(f"| {name} | {group} | {m['present']} | {m['candidate_complete']}/{m['present']} | "
                         f"{m['delivered_complete']}/{m['present']} | {m['multi_file_complete']}/{m['multi_file_queries']} | "
                         f"{m['absent_rejected']}/{m['absent']} | {m['median_s']:.3f}s / {m['p95_s']:.3f}s |")
    lines += ["", "All timing samples include absent queries. Startup, indexing, and candidate diagnostics are separate. "
              "Every measured semantic search freshly embeds its query. FTS5 BM25 and float32 sqlite-vec "
              "cosine are different engines from earlier Python/NumPy pilot baselines.", "",
              "## Calibrated weak-match policy", "", "```json", json.dumps(result["policy"], indent=2), "```", "",
              "The policy was fitted only to declared Click/Starlette development calibration queries "
              "and written before any Rich retrieval. It preserves all calibration positives; held-out "
              "positive rejections and negative acceptance still require inspection. Insufficient evidence "
              "does not prove absence.", "", "## Limits", "",
              *[f"- {limit}" for limit in result["frozen"]["limits"]], ""]
    return "\n".join(lines)
