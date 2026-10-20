#!/usr/bin/env python3
"""Freeze and compare context selectors through the real warm Qwen backend.

Each request embeds its literal query once. Both arms share the same saved
index, extraction, retrieval, dependency and exact compact-context budgets.
Quality uses repetition one; repetitions supply latency/stability only.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import statistics
import subprocess
import time
from unittest.mock import patch

from semantic_search.backend import SearchBackend
from semantic_search.context import compact_result, encoding, serialize_context
from semantic_search.corpus import _source_files, extract_snippets
from semantic_search.embeddings import QwenEmbedder
from semantic_search.languages import decode_source
from semantic_search.selection import select_context
from semantic_search.source import physical_lines
from diagnose_multifile import trace_selection, attribute_groups

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = dict(max_results=8, source_budget=12000, token_budget=4096,
                include_dependencies=True, rerank=False, candidates=50)


class RecordingEmbedder(QwenEmbedder):
    query_calls = 0

    def embed_query(self, query):
        vector = super().embed_query(query)
        self.query_calls += 1
        self.last_query = vector.copy()
        return vector


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def groups(query):
    return [] if query["kind"] == "absent" else query.get("target_groups", [query.get("targets")])


def is_multifile(gold):
    # A single OR group can offer implementations in several files. Only AND
    # groups that cannot all be satisfied in one file require a multi-file result.
    return len(gold) > 1 and not set.intersection(*({t["path"] for t in g} for g in gold))


def ranks(windows, gold):
    return [next((rank for rank, row in enumerate(windows, 1)
                  if any(row["path"] == target["path"]
                         and row["start_line"] <= target["evidence_lines"][0]
                         <= target["evidence_lines"][1] <= row["end_line"]
                         for target in alternatives)), None) for alternatives in gold]


def identity(args):
    data = read(args.dataset)
    review_sha256 = None
    if args.source_review is not None:
        review = read(args.source_review)
        if (review.get("status") != "passed_source_review_before_holdout_retrieval"
                or review.get("dataset", {}).get("sha256") != sha(args.dataset.read_bytes())
                or review.get("corpus", {}).get("commit") != data["revision"]
                or review.get("leakage_controls", {}).get("holdout_rankings_scores_candidates_or_rule_activation_outputs_observed") is not False):
            raise ValueError("Independent source-review receipt does not bind this dataset")
        review_sha256 = sha(args.source_review.read_bytes())
    elif args.role == "repository_holdout":
        raise ValueError("Repository holdout requires an independent source-review receipt")
    revision = subprocess.check_output(["git", "-C", str(args.corpus), "rev-parse", "HEAD"], text=True).strip()
    if revision != data["revision"] or subprocess.check_output(
            ["git", "-C", str(args.corpus), "status", "--porcelain"], text=True).strip():
        raise ValueError("A clean corpus at the dataset revision is required")
    paths = _source_files(args.corpus, args.corpus / data["source_root"], data.get("source_exclusions", ()))
    sources = {str(path.relative_to(args.corpus)): decode_source(str(path), path.read_bytes()) for path in paths}
    inventory = [asdict(snippet) for snippet in extract_snippets(
        args.corpus, data["source_root"], max_lines=160, overlap_lines=40,
        source_exclusions=data.get("source_exclusions", ()))]
    if len({q["id"] for q in data["queries"]}) != len(data["queries"]):
        raise ValueError("Duplicate query IDs")
    for query in data["queries"]:
        gold = groups(query)
        if query["kind"] not in ("present", "absent") or (query["kind"] == "present" and not gold):
            raise ValueError("Malformed query kind/gold")
        for alternatives in gold:
            if not alternatives:
                raise ValueError("Empty evidence group")
            for target in alternatives:
                low, high = target["evidence_lines"]
                text = "".join(physical_lines(sources[target["path"]])[low - 1:high])
                if (sha(text.encode()) != target["evidence_sha256"]
                        or sha((args.corpus / target["path"]).read_bytes()) != target["source_file_sha256"]):
                    raise ValueError("Gold source hashes differ")
        if any(rank is None for rank in ranks(inventory, gold)):
            raise ValueError("Required executable evidence lacks a complete extraction window")
    return data, sources, inventory, {
        "dataset_sha256": sha(args.dataset.read_bytes()), "revision": revision,
        "classification": args.role, "source_review_sha256": review_sha256,
        "source_sha256": {str(p.relative_to(args.corpus)): sha(p.read_bytes()) for p in paths},
        "inventory_sha256": sha(json.dumps(inventory, sort_keys=True).encode()),
        "implementation_sha256": {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in
                                  [*sorted((ROOT / "src/semantic_search").glob("*.py")), Path(__file__), ROOT / "scripts/diagnose_multifile.py", ROOT / "scripts/report_language_quality.py"]},
        "embedding": QwenEmbedder(args.model, progress=False).metadata,
        "settings": SETTINGS, "retrieval": ["dense", "multifile", "hybrid"],
        "repetitions": args.repetitions,
        "runtime": {name: importlib.metadata.version(name) for name in
                    ("tiktoken", "sqlite-vec", "tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript")},
        "machine": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
    }


def checked_response(response, sources):
    text = serialize_context(compact_result(response))
    actual_chars = sum(len(row["source"]) for row in response["results"])
    if (len(response["results"]) > 8 or response["total_source_chars"] != actual_chars or actual_chars > 12000
            or response["token_usage"]["tokens"] != len(encoding().encode(text, disallowed_special=()))
            or response["token_usage"]["tokens"] > 4096):
        raise ValueError("Result/source/exact compact token budget differs")
    for row in response["results"]:
        if row["path"] not in sources:
            raise ValueError("Delivered source is outside the pinned corpus")
        lines = physical_lines(sources[row["path"]])
        start, end = row["start_line"], row["end_line"]
        if (type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines)
                or not isinstance(row["source"], str) or not row["source"]
                or end != start + len(physical_lines(row["source"])) - 1):
            raise ValueError("Delivered source has invalid physical bounds")
        expected = "".join(lines[start - 1:end])
        if row["source"] != expected or row["source_hash"] != sha(expected.encode()):
            raise ValueError("Delivered source is not the pinned literal excerpt")


def checked_candidates(windows, sources):
    for row in windows:
        if row["path"] not in sources:
            raise ValueError("Candidate source is outside the pinned corpus")
        lines = physical_lines(sources[row["path"]])
        start, end = row["start_line"], row["end_line"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise ValueError("Candidate source has invalid physical bounds")
        expected = "".join(lines[start - 1:end])
        if not expected or row["text"] != expected or row["content_hash"] != sha(expected.encode()):
            raise ValueError("Candidate source is not the pinned literal window")


def summarize(rows):
    output = {}
    for retrieval in ("dense", "multifile", "hybrid"):
        all_rows = [r for r in rows if r["retrieval"] == retrieval]
        first = [r for r in all_rows if r["repetition"] == 1]
        positives = [r for r in first if r["kind"] == "present"]
        multi = [r for r in positives if r["multi_file"]]
        single = [r for r in positives if not r["multi_file"]]
        times = sorted(r["elapsed_backend_s"] for r in all_rows)
        output[retrieval] = {
            "present": len(positives), "complete": sum(None not in r["delivered_ranks"] for r in positives),
            "multi_file": len(multi), "multi_complete": sum(None not in r["delivered_ranks"] for r in multi),
            "single_file": len(single), "single_complete": sum(None not in r["delivered_ranks"] for r in single),
            "candidate_complete": sum(None not in r["candidate_ranks"] for r in positives),
            "required_groups": sum(len(r["delivered_ranks"]) for r in positives),
            "delivered_groups": sum(sum(v is not None for v in r["delivered_ranks"]) for r in positives),
            "absent_with_candidates": sum(r["kind"] == "absent" and bool(r["response"]["results"]) for r in first),
            "missing_group_stages": dict(Counter(g["outcome"] for r in first for g in r["group_diagnostics"] if g["outcome"] != "delivered")),
            "warm_latency_samples": len(times), "median_backend_s": statistics.median(times),
            "p95_backend_s": times[max(0, (95 * len(times) + 99) // 100 - 1)],
            "median_context_tokens": statistics.median(r["response"]["token_usage"]["tokens"] for r in first),
            "repeat_rank_changes": sum(r["delivered_ranks"] != next(f["delivered_ranks"] for f in first
                if f["query_id"] == r["query_id"]) for r in all_rows),
        }
    base = {r["query_id"]: r for r in rows if r["retrieval"] == "dense" and r["repetition"] == 1}
    changed = [r for r in rows if r["retrieval"] == "multifile" and r["repetition"] == 1 and r["kind"] == "present"]
    output["paired"] = {
        "complete_gains": [r["query_id"] for r in changed if None in base[r["query_id"]]["delivered_ranks"] and None not in r["delivered_ranks"]],
        "complete_losses": [r["query_id"] for r in changed if None not in base[r["query_id"]]["delivered_ranks"] and None in r["delivered_ranks"]],
        "group_regressions": [r["query_id"] for r in changed if any(a is not None and b is None for a, b in zip(base[r["query_id"]]["delivered_ranks"], r["delivered_ranks"]))],
        "group_gains": [r["query_id"] for r in changed if any(a is None and b is not None for a, b in zip(base[r["query_id"]]["delivered_ranks"], r["delivered_ranks"]))],
    }
    return output


def run(args, data, sources, inventory, binding):
    plan = read(args.output / "plan.json")
    if plan["binding"] != binding:
        raise ValueError("Source, labels, model or configuration changed after freeze")
    if (args.output / "searches.jsonl.gz").exists():
        raise ValueError("Refusing to overwrite an attempted run")
    encoding()
    embedder = RecordingEmbedder(args.model, progress=True)
    rows = []
    candidates_seen = {}
    db = ROOT / ".cache/indexes" / (args.output.name + ".sqlite3")
    with closing(SearchBackend(args.corpus, data["source_root"], db, embedder=embedder,
                       source_exclusions=data.get("source_exclusions", ()))) as backend:
        started = time.perf_counter()
        backend.search("Find code that handles input and returns a result", max_results=1)
        setup = {"index_and_model_warm_s": time.perf_counter() - started, "status": backend.status()}
        # Warm all paths without using labeled text.
        for retrieval in ("dense", "multifile", "hybrid"):
            backend.search("Find code that handles input and returns a result", retrieval=retrieval, **SETTINGS)
        with gzip.open(args.output / "searches.jsonl.gz", "xt", encoding="utf-8") as stream:
            for repetition in range(1, args.repetitions + 1):
                for number, query in enumerate(data["queries"]):
                    modes = ("dense", "multifile", "hybrid")
                    offset = (number + repetition - 1) % len(modes)
                    for retrieval in modes[offset:] + modes[:offset]:
                        captured = {}
                        def observed(pairs, **kwargs):
                            result = select_context(pairs, **kwargs)
                            captured.update(pairs=pairs, distinct=result[0], eligible=result[1], origins=result[2])
                            return result
                        query_calls_before = embedder.query_calls
                        with patch("semantic_search.backend.select_context", observed):
                            started = time.perf_counter()
                            response = backend.search(query["query"], retrieval=retrieval, **SETTINGS)
                            elapsed = time.perf_counter() - started
                        if embedder.query_calls != query_calls_before + 1:
                            raise ValueError("Exactly one fresh query embedding per measured request required")
                        checked_response(response, sources)
                        candidate_rows = [asdict(s) for s, _ in captured["pairs"]]
                        checked_candidates(candidate_rows, sources)
                        key = query["id"], retrieval
                        identifiers = [s["id"] for s in candidate_rows]
                        if key in candidates_seen and candidates_seen[key] != identifiers:
                            raise ValueError("Raw candidate order changed between repetitions")
                        candidates_seen[key] = identifiers
                        gold = groups(query)
                        trace = trace_selection(captured["pairs"], backend._references, response,
                            **{k: SETTINGS[k] for k in ("max_results", "source_budget", "token_budget", "include_dependencies")})
                        if serialize_context(compact_result(trace["response"])) != serialize_context(compact_result(response)):
                            raise ValueError("Diagnostic replay differs from actual backend compact payload")
                        row = dict(query_id=query["id"], kind=query["kind"], repetition=repetition,
                            retrieval=retrieval, elapsed_backend_s=elapsed,
                            multi_file=is_multifile(gold),
                            candidate_ranks=ranks(candidate_rows, gold),
                            delivered_ranks=ranks(response["results"], gold), response=response,
                            stages=trace["stages"], group_diagnostics=attribute_groups(trace, gold),
                            query_embedding_calls=embedder.query_calls - query_calls_before,
                            query_vector=embedder.last_query.tolist(),
                            query_vector_sha256=sha(embedder.last_query.tobytes()))
                        response["repo"] = data.get("repo_url", str(args.corpus))
                        rows.append(row)
                        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                        stream.flush()
                        print(f"r{repetition} {query['id']} {retrieval} {row['delivered_ranks']} {elapsed:.3f}s", flush=True)
    if identity(args)[3] != binding:
        raise ValueError("Source, corpus or implementation changed during the evaluation")
    write(args.output / "summary.json", {"complete": True, "requests": len(rows), "unique_queries": len(data["queries"]),
        "quality_repetition": 1, "setup": setup, "metrics": summarize(rows),
        "completed_query_embeddings": embedder.query_calls, "measured_query_embeddings": sum(r["query_embedding_calls"] for r in rows),
        "raw_sha256": sha((args.output / "searches.jsonl.gz").read_bytes())})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "corpus", "model", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--role", choices=("development", "repository_holdout"), default="development")
    parser.add_argument("--source-review", type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--freeze", action="store_true")
    action.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 10:
        parser.error("repetitions must be between one and ten")
    data, sources, inventory, binding = identity(args)
    if args.freeze:
        args.output.mkdir(parents=True, exist_ok=False)
        write(args.output / "plan.json", {"frozen_at": datetime.now(timezone.utc).isoformat(),
            "binding": binding, "quality_repetition": 1,
            "evidence_rule": "Exact pinned literal source, same path and complete executable interval in one excerpt; enclosing symbols allowed. OR within groups, AND across groups.",
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "gate": "Multifile versus dense: at least one net multi-file gain with no complete single-file loss; warm median overhead <=25%. Retain all group regressions. No tuning on fresh outcomes.",
            "limits": "Small source-authored sample; no coding correctness, billing or absence guarantee. Backend timings include warm source checks and one query embedding; exclude transport, cold setup and diagnostic serialization."})
    else:
        run(args, data, sources, inventory, binding)


if __name__ == "__main__":
    main()
