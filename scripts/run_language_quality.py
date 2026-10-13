#!/usr/bin/env python3
"""Prospective, source-byte-scored JS/TS retrieval; CPU preflight then frozen inference."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks/language_quality_manifest.json"
CONDITIONS = ("bm25", "dense", "hybrid")
SETTINGS = {"rerank": False, "candidates": 50, "max_results": 8,
            "source_budget": 12000, "token_budget": 4096, "include_dependencies": True}
REPETITIONS = 3


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def require(ok, detail):
    if not ok:
        raise ValueError(detail)


def groups(query):
    if query["kind"] == "absent":
        require(query.get("targets") == [] and "target_groups" not in query, "Invalid absent gold")
        return []
    result = query.get("target_groups", [query.get("targets")])
    require(query["kind"] == "present" and result and all(result), "Invalid present gold")
    return result


@lru_cache(maxsize=128)
def physical_lines(source):
    # Keep independent of extraction and preserve CRLF, bare CR and Unicode data.
    import re
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def verify_window(window, sources):
    path, start, end = window["path"], window["start_line"], window["end_line"]
    require(path in sources and type(start) is int and type(end) is int
            and 1 <= start <= end <= len(physical_lines(sources[path])), "Window outside source snapshot")
    expected = "".join(physical_lines(sources[path])[start - 1:end])
    require(window["source"] == expected and window["source_hash"] == sha(expected.encode()),
            "Window is not the exact saved source bytes")
    return window


def evidence_ranks(windows, gold, sources):
    verified = [verify_window(window, sources) for window in windows]
    result = []
    for alternatives in gold:
        rank = None
        for number, window in enumerate(verified, 1):
            for target in alternatives:
                lo, hi = target["evidence_lines"]
                if (window["path"] == target["path"] and window["start_line"] <= lo <= hi <= window["end_line"]):
                    evidence = "".join(physical_lines(sources[target["path"]])[lo - 1:hi])
                    require(sha(evidence.encode()) == target["evidence_sha256"], "Gold evidence changed")
                    rank = number
                    break
            if rank is not None:
                break
        result.append(rank)
    return result


def snippet_window(snippet, score=None):
    row = {"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
           "start_line": snippet.start_line, "end_line": snippet.end_line,
           "source": snippet.text, "source_hash": snippet.content_hash}
    if score is not None:
        row["retrieval_score"] = float(score)
    return row


def implementation_hashes():
    import semantic_search.corpus
    source_dir = Path(semantic_search.corpus.__file__).resolve().parent
    checkout = source_dir.parents[1]
    result = {"src/semantic_search/" + path.name: sha(path.read_bytes())
              for path in sorted(source_dir.glob("*.py"))}
    result["requirements-languages.lock"] = sha((checkout / "requirements-languages.lock").read_bytes())
    result.update({str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in
                   (Path(__file__), ROOT / "docs/language-retrieval-protocol.md")})
    return result


def load_sources():
    # Lazy imports permit CPU scoring tests without loading parsers or MLX.
    from semantic_search.corpus import _source_files, extract_snippets
    from semantic_search.languages import decode_source, extractor_fingerprint
    manifest = json.loads(MANIFEST.read_text())
    audit = json.loads((ROOT / "results/language-source-preflight-2026-10-13/source-audit.json").read_text())
    require(audit["checks_passed"] and audit["manifest_sha256"] == sha(MANIFEST.read_bytes()), "Source preflight differs")
    review = json.loads((ROOT / "results/language-preflight-2026-10-13/semantic-review.json").read_text())
    require(review.get("checks_passed", review.get("passed")) is True, "Independent source review failed")
    require(review["manifest_sha256"] == sha(MANIFEST.read_bytes()), "Semantic review is for different labels")
    entries, records = [], []
    for entry in manifest["datasets"]:
        dataset_path, repo = ROOT / entry["dataset"], ROOT / entry["corpus"]
        require(sha(dataset_path.read_bytes()) == entry["sha256"], "Dataset hash differs")
        dataset = json.loads(dataset_path.read_text())
        revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True).strip()
        require(revision == entry["revision"] and not dirty, "Corpus is not at the pinned clean revision")
        paths = _source_files(repo, repo / entry["source_root"], entry["source_exclusions"])
        raw = {path.relative_to(repo).as_posix(): path.read_bytes() for path in paths}
        expected = next(row for row in audit["datasets"] if row["id"] == entry["id"])["source_file_sha256"]
        require({path: sha(data) for path, data in raw.items()} == expected, "Extractor membership differs from authored production scope")
        sources = {path: decode_source(path, data) for path, data in raw.items()}
        snippets = extract_snippets(repo, entry["source_root"], max_lines=160, overlap_lines=40,
                                    source_exclusions=entry["source_exclusions"], sources=sources)
        windows = [snippet_window(snippet) for snippet in snippets]
        require(len({window["id"] for window in windows}) == len(windows), "Duplicate snippet identities")
        targets = []
        for query in dataset["queries"]:
            ranks = evidence_ranks(windows, groups(query), sources)
            require(all(rank is not None for rank in ranks), f"No actual extracted full-evidence window: {entry['id']}/{query['id']}")
            targets.append({"query_id": query["id"], "eligible_evidence_ranks": ranks,
                            "eligible_windows": [[window["id"] for window in windows
                                if evidence_ranks([window], [group], sources)[0] is not None] for group in groups(query)]})
        inventory = [{key: value for key, value in row.items() if key != "source"} for row in windows]
        records.append({"id": entry["id"], "source_files": len(raw), "windows": len(windows),
                        "source_file_sha256": expected, "window_inventory_sha256": sha(json.dumps(inventory, sort_keys=True).encode()),
                        "window_inventory": inventory, "queries": targets})
        entries.append((entry, dataset, sources))
    return entries, {"classification": "CPU actual-extractor membership and complete executable-byte eligibility, not retrieval quality",
                     "checks_passed": True, "model_calls": 0, "manifest_sha256": sha(MANIFEST.read_bytes()),
                     "extractor": extractor_fingerprint(), "implementation_sha256": implementation_hashes(), "datasets": records}


def runtime(preflight):
    from semantic_search.embeddings import QwenEmbedder
    from semantic_search.shortlist import machine_provenance
    packages = {distribution.metadata["Name"]: distribution.version for distribution in importlib.metadata.distributions()}
    schedule = request_schedule(preflight)
    return {"version": 1, "frozen_at": datetime.now(timezone.utc).isoformat(),
            "manifest_sha256": sha(MANIFEST.read_bytes()), "implementation_sha256": implementation_hashes(),
            "git_commit": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
            "machine": machine_provenance(), "python": platform.python_version(),
            "embedding": QwenEmbedder(progress=False).metadata, "packages": dict(sorted(packages.items())),
            "conditions": list(CONDITIONS), "settings": SETTINGS, "candidate_depth": 50,
            "repetitions": REPETITIONS, "extractor": preflight["extractor"],
            "sources": [{key: row[key] for key in ("id", "source_file_sha256", "window_inventory_sha256", "windows")}
                        for row in preflight["datasets"]],
            "quality_unit": "One query per condition, first repetition; later repetitions check stability and warm latency only.",
            "memory": "macOS vmmap physical footprint after cold setup and after queries, outside HTTP timers; rounded peak is process lifetime, shared across datasets.",
            "candidate_measurement": "Reconstruct deterministic top50 using the exact current query vector, no additional embedding, after measured HTTP request.",
            "threshold": None, "h2o": False, "query_cache": False,
            "fusion": {"weights": "equal", "rrf_k": 60, "fetch_depth": 50},
            "dependencies": {"anchors": 5, "max_callees_per_anchor": 2, "depth": 1},
            "packing": "First ranked window per path/symbol; ranked whole-line source prefixes, then exact compact cl100k JSON budget.",
            "ordering": "Express then Zod; rotate three conditions by query index plus repetition index.",
            "planned_requests": len(schedule), "schedule": schedule,
            "limitations": ["Source-authored small sample, not coding correctness or provider token savings.",
                            "Repository/language holdout does not imply model pretraining holdout.",
                            "Absent queries may return plausible decoys; no abstention threshold fitted."]}


def request_schedule(preflight):
    result = []
    for entry in preflight["datasets"]:
        for repetition in range(1, REPETITIONS + 1):
            for number, query in enumerate(entry["queries"]):
                offset = (number + repetition - 1) % len(CONDITIONS)
                for condition in CONDITIONS[offset:] + CONDITIONS[:offset]:
                    result.append({"dataset": entry["id"], "query_id": query["query_id"],
                                   "condition": condition, "repetition": repetition})
    return result


def summarize(rows):
    result = {}
    for dataset in sorted({row["dataset"] for row in rows}):
        result[dataset] = {}
        for condition in CONDITIONS:
            selected = [row for row in rows if row["dataset"] == dataset and row["condition"] == condition]
            primary = [row for row in selected if row["repetition"] == 1]
            positive = [row for row in primary if row["kind"] == "present"]
            negative = [row for row in primary if row["kind"] == "absent"]
            multifile = [row for row in positive if row["multi_file_and"]]
            completion_ranks = [max(row["evidence_ranks"]) if None not in row["evidence_ranks"] else None
                                for row in positive]
            times = sorted(row["elapsed_http_s"] for row in selected)
            baseline = {row["query_id"]: (row["evidence_ranks"], row["candidate_evidence_ranks"])
                        for row in primary}
            mismatches = [row["query_id"] for row in selected if
                          (row["evidence_ranks"], row["candidate_evidence_ranks"]) != baseline[row["query_id"]]]
            result[dataset][condition] = {"present_queries": len(positive), "absent_queries": len(negative),
                "candidate_complete": sum(all(rank is not None for rank in row["candidate_evidence_ranks"]) for row in positive),
                "delivered_complete": sum(all(rank is not None for rank in row["evidence_ranks"]) for row in positive),
                "required_groups": sum(len(row["evidence_ranks"]) for row in positive),
                "candidate_groups": sum(sum(rank is not None for rank in row["candidate_evidence_ranks"]) for row in positive),
                "delivered_groups": sum(sum(rank is not None for rank in row["evidence_ranks"]) for row in positive),
                "multi_file_queries": len(multifile), "multi_file_complete": sum(all(rank is not None for rank in row["evidence_ranks"]) for row in multifile),
                "absent_with_candidates": sum(row["response"]["match_status"] == "candidates" for row in negative),
                "absent_no_candidates": sum(row["response"]["match_status"] == "no_candidates" for row in negative),
                "absent_rejected": sum(row["response"]["match_status"] == "insufficient_evidence" for row in negative),
                "repeat_coverage_mismatches": mismatches,
                "first_complete_evidence_ranks": completion_ranks,
                "complete_evidence_mrr": sum(1 / rank for rank in completion_ranks if rank is not None) / len(positive) if positive else None,
                **{f"complete_at_{depth}": sum(rank is not None and rank <= depth for rank in completion_ranks)
                   for depth in (1, 3, 5, 8)},
                "latency_samples": len(times), "median_http_s": statistics.median(times) if times else None,
                "p95_http_s": times[max(0, math.ceil(.95 * len(times)) - 1)] if times else None,
                "max_compact_context_tokens": max((row["response"]["token_usage"]["tokens"] for row in selected), default=None),
                "primary_truncated_queries": sum(row["response"].get("budget_exhausted", False) for row in primary),
                "primary_misses": [{"query_id": row["query_id"], "evidence_ranks": row["evidence_ranks"]} for row in positive if None in row["evidence_ranks"]]}
    return result


def footprint():
    if platform.system() != "Darwin":
        return {"available": False, "reason": "macOS vmmap is required"}
    import re
    try:
        result = subprocess.run(["vmmap", "-summary", str(os.getpid())], capture_output=True,
                                text=True, timeout=30, check=True)
        fields = {}
        for label, key in (("Physical footprint", "physical_footprint"),
                           ("Physical footprint (peak)", "physical_footprint_peak")):
            match = re.search(r"^" + re.escape(label) + r":\s+(\S+)", result.stdout, re.MULTILINE)
            require(match is not None, f"vmmap did not report {label}")
            fields[key] = match.group(1)
        return {"available": True, "tool": "macOS vmmap -summary", **fields,
                "peak_scope": "Rounded physical footprint peak since process launch; includes earlier datasets, not simultaneous combined system peak."}
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {"available": False, "reason": str(error)}


def run(output):
    from semantic_search.backend import SearchBackend
    from semantic_search.client import SearchClient
    from semantic_search.context import compact_result, encoding, serialize_context
    from semantic_search.quality import RecordingEmbedder
    from semantic_search.server import create_server

    class ObservedEmbedder(RecordingEmbedder):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.document_calls = self.document_texts = self.query_calls = 0

        def embed_documents(self, texts, *, cancel_check=None):
            result = super().embed_documents(texts, cancel_check=cancel_check)
            self.document_calls += 1
            self.document_texts += len(texts)
            return result

        def embed_query(self, query):
            result = super().embed_query(query)
            self.query_calls += 1
            return result

        def counts(self):
            return {"completed_document_calls": self.document_calls, "document_texts": self.document_texts,
                    "completed_query_calls": self.query_calls}
    entries, preflight = load_sources()
    frozen = json.loads((output / "frozen-config.json").read_text())
    current = runtime(preflight)
    for key in ("manifest_sha256", "implementation_sha256", "embedding", "packages", "conditions", "settings", "extractor", "sources", "repetitions", "schedule"):
        require(current[key] == frozen[key], f"Runtime changed after freeze: {key}")
    require(not (output / "searches.jsonl").exists(), "Refusing to resume or overwrite an attempted run")
    rows, setups = [], []
    encoding()
    with (output / "searches.jsonl").open("x") as stream:
        for entry, dataset, sources in entries:
            db = ROOT / ".cache/indexes" / (output.name + "-" + entry["id"] + ".sqlite3")
            require(not db.exists(), "Cold index database already exists")
            embedder = ObservedEmbedder(progress=True)
            backend = SearchBackend(ROOT / entry["corpus"], entry["source_root"], db,
                                    embedder=embedder, source_exclusions=entry["source_exclusions"])
            server = worker = None
            try:
                started = time.perf_counter()
                warm = backend.search("Find the implementation", retrieval="dense", max_results=1, rerank=False)
                setup = {"dataset": entry["id"], "cold_index_and_model_warm_s": time.perf_counter() - started,
                         "indexstats": warm["indexstats"], "memory_after_cold_setup": footprint(),
                         "embedding_calls_after_cold_setup": embedder.counts()}
                require(embedder.query_calls == 1, "Cold generic warmup must embed exactly one query")
                server = create_server(backend, port=0)
                worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05})
                worker.start()
                client = SearchClient(f"http://127.0.0.1:{server.server_address[1]}")
                for repetition in range(1, REPETITIONS + 1):
                    for number, query in enumerate(dataset["queries"]):
                        offset = (number + repetition - 1) % len(CONDITIONS)
                        order = CONDITIONS[offset:] + CONDITIONS[:offset]
                        for condition in order:
                            identity = {"dataset": entry["id"], "query_id": query["id"],
                                        "condition": condition, "repetition": repetition}
                            require(identity == frozen["schedule"][len(rows)], "Request differs from the exact frozen inventory")
                            started = time.perf_counter()
                            response = client.search(query["query"], retrieval=condition, **SETTINGS)
                            elapsed = time.perf_counter() - started
                            if condition == "bm25":
                                candidates = backend._lexical.search(query["query"], 50)
                            elif condition == "dense":
                                candidates = backend._dense.search(embedder.last_query, 50)
                            else:
                                candidates = backend._hybrid.search(query["query"], embedder.last_query, 50)
                            windows = [snippet_window(snippet, score) for snippet, score in candidates]
                            require(len(windows) <= 50 and len(response["results"]) <= 8, "Candidate or result depth exceeded")
                            require(response["total_source_chars"] == sum(len(row["source"]) for row in response["results"]) <= 12000,
                                    "Source budget or counter differs")
                            actual_tokens = len(encoding().encode(serialize_context(compact_result(response)), disallowed_special=()))
                            require(actual_tokens == response["token_usage"]["tokens"] <= 4096, "Compact context token budget differs")
                            gold = groups(query)
                            row = {"dataset": entry["id"], "role": entry["role"], "language": entry["language"],
                                   "query_id": query["id"], "kind": query["kind"], "split": query.get("split", "development"),
                                   "multi_file_and": len({target["path"] for group in gold for target in group}) > 1,
                                   "condition": condition, "repetition": repetition, "elapsed_http_s": elapsed,
                                   "evidence_ranks": evidence_ranks(response["results"], gold, sources),
                                   "candidate_evidence_ranks": evidence_ranks(windows, gold, sources),
                                   "response": deepcopy(response), "candidate_windows": [
                                       {key: value for key, value in window.items() if key != "source"} for window in windows],
                                   "exact_source_and_budgets_verified": True}
                            row["response"]["repo"] = entry["corpus"]
                            rows.append(row)
                            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                            stream.flush()
                            print(f"{entry['id']} r{repetition} {query['id']} {condition} evidence={row['evidence_ranks']} HTTP={elapsed:.3f}s", flush=True)
                setup["memory_after_queries"] = footprint()
                setup["embedding_calls_after_queries"] = embedder.counts()
                require(embedder.document_calls == setup["embedding_calls_after_cold_setup"]["completed_document_calls"],
                        "Unchanged query phase unexpectedly embedded documents")
                require(embedder.query_calls == 1 + len(dataset["queries"]) * 2 * REPETITIONS,
                        "Query phase did not perform exactly one fresh embedding per dense/hybrid request")
                setups.append(setup)
            finally:
                if server is not None:
                    server.shutdown(); server.server_close(); worker.join()
                backend.close()
            setup["closed_database_sha256"] = sha(db.read_bytes())
            # Fresh process-like backend, same disk index: no document inference expected.
            reopen_embedder = ObservedEmbedder(progress=False)
            reopen = SearchBackend(ROOT / entry["corpus"], entry["source_root"], db,
                                   embedder=reopen_embedder, source_exclusions=entry["source_exclusions"])
            try:
                started = time.perf_counter()
                reuse = reopen.refresh()
                setup["persistent_reopen"] = {"elapsed_s": time.perf_counter() - started, "indexstats": reuse,
                                              "embedding_calls": reopen_embedder.counts()}
                require(reuse["indexed_windows"] == 0 and reuse["reused_windows"] == reuse["total_windows"],
                        "Persistent index did not reuse every unchanged window")
                require(reopen_embedder.document_calls == reopen_embedder.query_calls == 0,
                        "Persistent reopen unexpectedly invoked model inference")
            finally:
                reopen.close()
            write(output / "setups.json", setups)
    require(len(rows) == frozen["planned_requests"], "Incomplete planned request inventory")
    require([{key: row[key] for key in ("dataset", "query_id", "condition", "repetition")} for row in rows]
            == frozen["schedule"], "Observed request identity/order differs from freeze")
    write(output / "summary.json", {"complete": True, "requests": len(rows), "unique_queries": 30,
                                   "quality_repetition": 1, "datasets": summarize(rows), "setups": setups,
                                   "searches_sha256": sha((output / "searches.jsonl").read_bytes())})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "freeze", "run"))
    parser.add_argument("--output", type=Path, default=ROOT / "results/language-quality-2026-10-13")
    args = parser.parse_args()
    if args.action == "run":
        run(args.output)
    else:
        require(not (args.output / "frozen-config.json").exists(), "Refusing to overwrite a frozen preflight; use a separate output directory")
        _, preflight = load_sources()
        write(args.output / "extractor-preflight.json", preflight)
        if args.action == "freeze":
            write(args.output / "frozen-config.json", runtime(preflight))
        print(json.dumps({"checks_passed": True, "model_calls": 0,
                          "datasets": [{key: row[key] for key in ("id", "source_files", "windows")} for row in preflight["datasets"]]}, indent=2))


if __name__ == "__main__":
    main()
