#!/usr/bin/env python3
"""Audit a completed quality run using pinned source and CPU-only tokenization.

No retrieval, model loading, prompt changes, or label changes are performed.
The frozen AST extractor reconstructs window identities; source slices, gold
matching, aggregations, policy selection, and JSON token counts are checked here
without importing the quality runner, backend, or model modules.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tokenize

from semantic_search.corpus import extract_snippets


class AuditError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AuditError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_lines(text):
    """Split only physical CR/LF endings, preserving bytes after decoding."""
    lines, start = [], 0
    for match in re.finditer(r"\r\n|\r|\n", text):
        lines.append(text[start:match.end()])
        start = match.end()
    if start < len(text):
        lines.append(text[start:])
    return lines


def read_json(path):
    return json.loads(path.read_bytes())


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def groups_for(query):
    if query["kind"] == "absent":
        require(query.get("targets", []) == [] and "target_groups" not in query, "Absent query has gold targets")
        return []
    require(query["kind"] == "present", "Unknown query kind")
    require(not ("targets" in query and "target_groups" in query), "Ambiguous OR/AND labels")
    groups = query.get("target_groups", [query.get("targets")])
    require(isinstance(groups, list) and groups and all(isinstance(group, list) and group for group in groups),
            "Present query has empty evidence groups")
    for group in groups:
        for target in group:
            interval = target.get("evidence_lines")
            require(isinstance(interval, list) and len(interval) == 2
                    and all(type(line) is int for line in interval) and 1 <= interval[0] <= interval[1],
                    "Invalid executable evidence bounds")
    return groups


def ranks_for(windows, groups):
    """OR inside each group; each separate group remains required."""
    ranks = []
    for group in groups:
        rank = None
        for position, window in enumerate(windows, 1):
            if any(window["path"] == target["path"] and window["symbol"] == target["symbol"]
                   and window["start_line"] <= target["evidence_lines"][0]
                   and window["end_line"] >= target["evidence_lines"][1] for target in group):
                rank = position
                break
        ranks.append(rank)
    return ranks


def compact_context(response):
    # Field order is part of the frozen, exact JSON token-count contract.
    fields = ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")
    excerpts = []
    for result in response["results"]:
        excerpts.append({**{field: result[field] for field in fields},
                         "origin": result.get("origin", "retrieval"), "related_to": result.get("related_to")})
    return {"query": response["query"], "match_status": response["match_status"], "results": excerpts,
            "budget_exhausted": response["budget_exhausted"], "token_usage": response["token_usage"]}


def load_corpus(entry, dataset, root, frozen):
    repo = (root / entry["corpus"]).resolve()
    revision = git(repo, "rev-parse", "HEAD").decode().strip()
    require(revision == entry["revision"] == dataset["revision"], f"Changed corpus revision: {entry['id']}")
    require(not git(repo, "status", "--porcelain").strip(), f"Dirty corpus: {entry['id']}")
    require(dataset["source_root"] == entry["source_root"] and dataset["repo_url"] == entry["repo_url"],
            f"Corpus metadata differs: {entry['id']}")
    filenames = git(repo, "ls-tree", "-r", "--name-only", "-z", revision, "--", entry["source_root"])
    filenames = sorted(name.decode("utf-8") for name in filenames.split(b"\0") if name.endswith(b".py"))
    sources, file_hashes = {}, {}
    for name in filenames:
        raw = git(repo, "show", f"{revision}:{name}")
        require((repo / name).read_bytes() == raw, f"Saved source differs from pinned Git bytes: {name}")
        file_hashes[name] = sha(raw)
        codec, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        sources[name] = raw.decode(codec)
    windows = extract_snippets(repo, entry["source_root"], **frozen["extraction"], sources=sources)
    window_map = {window.id: window for window in windows}
    require(len(window_map) == len(windows), "Duplicate reconstructed source-window IDs")
    for window in windows:
        require(window.text == "".join(source_lines(sources[window.path])[window.start_line - 1:window.end_line]),
                f"AST window source differs from pinned slice: {window.id}")
    branch = subprocess.run(["git", "-C", str(repo), "symbolic-ref", "--quiet", "HEAD"],
                            capture_output=True, text=True)
    source_manifest = {"source_root": entry["source_root"],
                       "git": {"revision": revision, "branch": branch.stdout.strip() if branch.returncode == 0 else None},
                       "files": file_hashes}
    return {"repo": repo, "sources": sources, "windows": window_map,
            "source_manifest_sha256": sha(json.dumps(source_manifest, sort_keys=True).encode()),
            "source_file_sha256": file_hashes}


def independent_metrics(rows):
    positives = [row for row in rows if row["kind"] == "present"]
    negatives = [row for row in rows if row["kind"] == "absent"]
    single = [row for row in positives if len(row["evidence_ranks"]) == 1]
    multi = [row for row in positives if len(row["evidence_ranks"]) > 1]
    complete = lambda ranks: bool(ranks) and None not in ranks
    finish = [max(row["evidence_ranks"]) if complete(row["evidence_ranks"]) else None for row in positives]
    times = sorted(row["elapsed_http_s"] for row in rows)
    tokens = [row["actual_context_tokens"] for row in rows]
    return {
        "queries": len(rows), "present": len(positives), "absent": len(negatives),
        "candidate_complete": sum(complete(row["candidate_evidence_ranks"]) for row in positives),
        "delivered_complete": sum(complete(row["evidence_ranks"]) for row in positives),
        "single_first": sum(row["evidence_ranks"] == [1] for row in single), "single_queries": len(single),
        "multi_file_complete": sum(complete(row["evidence_ranks"]) for row in multi), "multi_file_queries": len(multi),
        "absent_rejected": sum(row["response"]["match_status"] == "insufficient_evidence" for row in negatives),
        "absent_accepted": sum(row["response"]["match_status"] == "candidates" for row in negatives),
        "absent_no_candidates": sum(row["response"]["match_status"] == "no_candidates" for row in negatives),
        "present_rejected": sum(row["response"]["match_status"] == "insufficient_evidence" for row in positives),
        **{f"hit_at_{k}": sum(rank is not None and rank <= k for rank in finish) / len(positives)
           if positives else None for k in (1, 3, 5)},
        "mrr": sum(1 / rank for rank in finish if rank is not None) / len(positives) if positives else None,
        "required_groups": sum(len(row["evidence_ranks"]) for row in positives),
        "delivered_groups": sum(sum(rank is not None for rank in row["evidence_ranks"]) for row in positives),
        "candidate_groups": sum(sum(rank is not None for rank in row["candidate_evidence_ranks"]) for row in positives),
        "timing_samples": len(rows), "median_s": statistics.median(times),
        "p95_s": times[math.ceil(.95 * len(times)) - 1], "max_s": times[-1],
        "median_tokens": statistics.median(tokens), "max_tokens": max(tokens),
        "token_truncated_queries": sum(row["response"]["token_usage"]["truncated"] for row in rows),
        "budget_exhausted_queries": sum(row["response"]["budget_exhausted"] for row in rows),
        "median_source_chars": statistics.median(row["actual_source_chars"] for row in rows),
    }


def summaries(rows):
    output = {}
    for condition in sorted({row["condition"] for row in rows}):
        selected = [row for row in rows if row["condition"] == condition]
        subsets = {"all": selected,
                   "development": [row for row in selected if row["role"] != "repository_holdout"],
                   "development_queries": [row for row in selected if row["role"] != "repository_holdout"
                                           and row["split"] != "calibration"],
                   "calibration": [row for row in selected if row["role"] != "repository_holdout" and row["split"] == "calibration"],
                   "repository_holdout": [row for row in selected if row["role"] == "repository_holdout"]}
        for dataset in sorted({row["dataset"] for row in selected}):
            subsets[f"repository:{dataset}"] = [row for row in selected if row["dataset"] == dataset]
        output[condition] = {name: independent_metrics(items) for name, items in subsets.items() if items}
    return output


def compare_values(actual, reported, label):
    if isinstance(actual, dict):
        require(isinstance(reported, dict) and actual.keys() == reported.keys(), f"Different metric keys: {label}")
        for key in actual:
            compare_values(actual[key], reported[key], f"{label}.{key}")
    elif isinstance(actual, float):
        require(type(reported) in (float, int) and math.isclose(actual, reported, rel_tol=1e-10, abs_tol=1e-12),
                f"Different numerical metric: {label}: {actual} != {reported}")
    else:
        require(actual == reported, f"Different metric: {label}: {actual} != {reported}")


def audit(run, manifest_path, root):
    result_path, frozen_path, searches_path, policy_path = [run / name for name in
        ("results.json", "frozen-config.json", "searches.jsonl", "abstention-policy.json")]
    require(all(path.is_file() for path in (result_path, frozen_path, searches_path, policy_path)),
            "Run is incomplete; wait for results.json and the frozen policy")
    manifest, frozen, result, policy = [read_json(path) for path in (manifest_path, frozen_path, result_path, policy_path)]
    require(frozen["manifest_sha256"] == sha(manifest_path.read_bytes()), "Changed manifest after configuration freeze")
    require(result["frozen"] == frozen and result["embedding"] == frozen["embedding"], "Result model/settings differ from freeze")
    require(result["policy"] == policy and policy["embedding"] == frozen["embedding"], "Policy model/settings differ from freeze")
    require(frozen["extraction"] == {"max_lines": 160, "overlap_lines": 40}
            and frozen["candidate_depth"] == frozen["unreranked_retrieval_depth"] == 50
            and frozen["source_budget_chars"] == 12000 and frozen["tokenizer"] == "cl100k_base"
            and frozen["query_cache"] is False, "Unexpected frozen extraction/depth/budget settings")
    for filename, digest in frozen["implementation_sha256"].items():
        require(sha((root / filename).read_bytes()) == digest, f"Implementation changed since freeze: {filename}")
    for package, version in {**frozen["packages"], **frozen["embedding"]["packages"]}.items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        require(installed == version, f"Different installed package version: {package}")
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(root / ".cache/tokenizers"))
    import tiktoken
    counter = tiktoken.get_encoding(frozen["tokenizer"])
    configurations = {condition["name"]: condition for condition in frozen["conditions"]}
    require(set(configurations) == {"fts5", "dense", "hybrid", "dense_context"}, "Unexpected baseline conditions")
    require(all(condition["token_budget"] == 4096 for condition in configurations.values()), "Missing baseline token cap")
    configurations["dense_context_policy"] = configurations["dense_context"]
    datasets, expected, totals = {}, Counter(), Counter()
    for entry in manifest["datasets"]:
        path = root / entry["dataset"]
        require(sha(path.read_bytes()) == entry["sha256"], f"Dataset changed: {entry['dataset']}")
        dataset = read_json(path)
        corpus = load_corpus(entry, dataset, root, frozen)
        corpus.update(entry=entry, dataset=dataset, queries={query["id"]: query for query in dataset["queries"]})
        require(len(corpus["queries"]) == len(dataset["queries"]), "Duplicate query IDs")
        for query in dataset["queries"]:
            groups = groups_for(query)
            full = [{"path": window.path, "symbol": window.symbol, "start_line": window.start_line,
                     "end_line": window.end_line} for window in corpus["windows"].values()]
            require(None not in ranks_for(full, groups), f"Gold has no full AST source window: {query['id']}")
            totals["queries"] += 1
            totals[f"{query['kind']}_queries"] += 1
            totals["multi_file_and_queries"] += len(groups) > 1
            names = list(configurations) if entry["role"] == "repository_holdout" else list(configurations)[:-1]
            for name in names:
                expected[(entry["dataset"], query["id"], name)] += 1
        datasets[entry["dataset"]] = corpus
    require(dict(totals) == manifest["totals"], "Manifest totals differ from authored datasets")
    model_fingerprint = sha(json.dumps(frozen["embedding"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    rows, observed, delivered_count, candidate_count = [], Counter(), 0, 0
    for number, line in enumerate(searches_path.read_text().splitlines(), 1):
        row = json.loads(line)
        key = (row["dataset"], row["query_id"], row["condition"])
        require(key in expected, f"Unexpected search row: {key}")
        observed[key] += 1
        corpus = datasets[row["dataset"]]
        entry, query, response = corpus["entry"], corpus["queries"][row["query_id"]], row["response"]
        label = f"row {number} {key}"
        require(row["dataset_sha256"] == entry["sha256"] and row["role"] == entry["role"]
                and row["kind"] == query["kind"] and row["split"] == query.get("split", "development"), f"Wrong row provenance: {label}")
        require(response["query"] == query["query"] and Path(response["repo"]).resolve() == corpus["repo"]
                and response["source_root"] == entry["source_root"], f"Wrong response source/query: {label}")
        settings = configurations[row["condition"]]
        require(response["retrieval"] == settings["retrieval"] and response["rerank"] is False
                and response["source_budget"] == 12000 and len(response["results"]) <= settings["max_results"], f"Wrong response settings: {label}")
        stats = response["indexstats"]
        require(stats["source_manifest"] == corpus["source_manifest_sha256"]
                and stats["total_windows"] == len(corpus["windows"])
                and stats["source_files"] == len(corpus["sources"]), f"Wrong index source snapshot: {label}")
        require(stats["model_fingerprint"] == (None if settings["retrieval"] == "bm25" else model_fingerprint), f"Wrong model fingerprint: {label}")
        candidates, candidate_ids = [], []
        require(len(row["candidate_windows"]) <= 50, f"Candidate cap exceeded: {label}")
        if settings["retrieval"] != "bm25":
            require(len(row["candidate_windows"]) == min(50, len(corpus["windows"])), f"Missing full candidate prefix: {label}")
        for candidate in row["candidate_windows"]:
            require(candidate["id"] in corpus["windows"] and math.isfinite(candidate["score"]), f"Invalid full candidate: {label}")
            window = corpus["windows"][candidate["id"]]
            candidate_ids.append(window.id)
            candidates.append({"path": window.path, "symbol": window.symbol,
                               "start_line": window.start_line, "end_line": window.end_line})
        require(len(set(candidate_ids)) == len(candidate_ids), f"Duplicate candidate ID: {label}")
        candidate_count += len(candidates)
        unique = []
        seen = set()
        for identity in candidate_ids:
            window = corpus["windows"][identity]
            symbol_key = (window.path, window.symbol)
            if symbol_key not in seen:
                unique.append(identity)
                seen.add(symbol_key)
        anchors = set(unique[:5])
        dependency_counts, returned_symbols = Counter(), set()
        for excerpt in response["results"]:
            require(excerpt["id"] in corpus["windows"], f"Unknown delivered window: {label}")
            window = corpus["windows"][excerpt["id"]]
            require((excerpt["path"], excerpt["symbol"], excerpt["start_line"], excerpt["window_end_line"])
                    == (window.path, window.symbol, window.start_line, window.end_line), f"Wrong delivered window identity: {label}")
            require(type(excerpt["end_line"]) is int and excerpt["start_line"] <= excerpt["end_line"] <= window.end_line, f"Wrong delivered bounds: {label}")
            pinned = "".join(source_lines(corpus["sources"][window.path])[excerpt["start_line"] - 1:excerpt["end_line"]])
            require(excerpt["source"] == pinned and excerpt["source_hash"] == sha(pinned.encode())
                    and excerpt["content_hash"] == window.content_hash
                    and excerpt["truncated"] == (pinned != window.text), f"Wrong returned source bytes/SHA/truncation: {label}")
            require(excerpt["h2o_score"] is None, f"Unexpected H2O score: {label}")
            symbol_key = (window.path, window.symbol)
            require(symbol_key not in returned_symbols, f"Undeduplicated returned symbol: {label}")
            returned_symbols.add(symbol_key)
            if excerpt["origin"] == "dependency":
                require(settings.get("include_dependencies") is True and excerpt["related_to"] in anchors, f"Invalid dependency provenance: {label}")
                dependency_counts[excerpt["related_to"]] += 1
                require(dependency_counts[excerpt["related_to"]] <= 2, f"Dependency cap exceeded: {label}")
            else:
                require(excerpt["origin"] == "retrieval" and excerpt["related_to"] is None
                        and excerpt["id"] in unique[:settings["max_results"]], f"Wrong retrieved prefix: {label}")
            delivered_count += 1
        actual_chars = sum(len(excerpt["source"]) for excerpt in response["results"])
        require(actual_chars == response["total_source_chars"] and actual_chars <= 12000, f"Wrong source-character accounting: {label}")
        envelope = compact_context(response)
        text = json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        actual_tokens = len(counter.encode(text, disallowed_special=()))
        usage = response["token_usage"]
        require(usage["encoding"] == "cl100k_base" and usage["budget"] == 4096
                and usage["tokens"] == actual_tokens <= 4096, f"Wrong exact context token count/cap: {label}")
        require(response["match_status"] in ("candidates", "no_candidates", "insufficient_evidence"), f"Unknown match status: {label}")
        require(row["top_cosine"] == response["match_evidence"]["top_cosine"], f"Wrong top score provenance: {label}")
        if row["condition"] == "dense_context_policy":
            require(response["match_evidence"]["threshold"] == policy["threshold"], f"Wrong frozen threshold: {label}")
            rejected = row["top_cosine"] < policy["threshold"]
            require((response["match_status"] == "insufficient_evidence") == rejected, f"Wrong strict policy decision: {label}")
            require(not rejected or not response["results"], f"Rejected policy returned source: {label}")
        else:
            require(response["match_evidence"]["threshold"] is None and response["match_status"] != "insufficient_evidence", f"Policy leaked into baseline: {label}")
        require(type(row["elapsed_http_s"]) in (float, int) and math.isfinite(row["elapsed_http_s"]) and row["elapsed_http_s"] > 0, f"Invalid HTTP timing: {label}")
        require(response["timings"]["query_embedding_s"] == 0 if settings["retrieval"] == "bm25"
                else response["timings"]["query_embedding_s"] > 0, f"Wrong query embedding timing path: {label}")
        require(row["elapsed_http_s"] + 1e-6 >= response["timings"]["total_s"], f"HTTP timing shorter than backend work: {label}")
        groups = groups_for(query)
        delivered_ranks, candidate_ranks = ranks_for(response["results"], groups), ranks_for(candidates, groups)
        require(delivered_ranks == row["evidence_ranks"] and candidate_ranks == row["candidate_evidence_ranks"], f"Wrong independently recomputed evidence ranks: {label}")
        row = {**row, "evidence_ranks": delivered_ranks, "candidate_evidence_ranks": candidate_ranks,
               "actual_context_tokens": actual_tokens, "actual_source_chars": actual_chars}
        rows.append(row)
    require(observed == expected and len(rows) == result["search_rows"], "Missing, duplicate, or extra query/condition rows")
    calibration = [row for row in rows if row["condition"] == "dense_context" and row["role"] == "development" and row["split"] == "calibration"]
    require(len(calibration) == 24 and Counter(row["kind"] for row in calibration) == {"present": 20, "absent": 4}, "Wrong calibration membership/counts")
    require({datasets[row["dataset"]]["entry"]["id"] for row in calibration} == {"click", "starlette"}, "Calibration used undeclared repositories")
    threshold = min(row["top_cosine"] for row in calibration if row["kind"] == "present")
    require(policy["threshold"] == threshold, "Threshold was not the minimum positive calibration score")
    provenance = {"role": "development", "split": "calibration", "positive_queries": 20, "absent_queries": 4,
                  "retained_positives": 20,
                  "rejected_absent": sum(row["top_cosine"] < threshold for row in calibration if row["kind"] == "absent"),
                  "query_ids": [row["query_id"] for row in calibration],
                  "dataset_sha256": sorted({row["dataset_sha256"] for row in calibration})}
    require(policy["calibration"] == provenance, "Policy calibration provenance/counts differ")
    require(policy["evaluation"] == {"manifest_sha256": sha(manifest_path.read_bytes()),
            "frozen_config_sha256": sha(frozen_path.read_bytes())}, "Policy lacks matching configuration/manifest hashes")
    first_holdout = next(index for index, row in enumerate(rows) if row["role"] == "repository_holdout")
    require(all(row["role"] == "development" for row in rows[:first_holdout])
            and all(row["role"] == "repository_holdout" for row in rows[first_holdout:]), "Development/holdout execution interleaved")
    require(policy_path.stat().st_mtime_ns <= result_path.stat().st_mtime_ns, "Policy artifact timestamp follows final results")
    summary = summaries(rows)
    compare_values(summary, result["summary"], "summary")
    require(not any(name == "mlx" or name.startswith("mlx.") for name in sys.modules), "Audit loaded MLX unexpectedly")
    return {"version": 1, "status": "passed", "audited_at_utc": datetime.now(timezone.utc).isoformat(),
            "audit_script_sha256": sha(Path(__file__).read_bytes()),
            "inputs_sha256": {path.name: sha(path.read_bytes()) for path in (manifest_path, frozen_path, searches_path, policy_path, result_path)},
            "rows": len(rows), "expected_rows": sum(expected.values()), "delivered_excerpts_checked": delivered_count,
            "candidate_windows_checked": candidate_count, "calibration_rows": len(calibration),
            "calibration_threshold": threshold, "calibration_absent_rejected": provenance["rejected_absent"],
            "datasets": {name: {"revision": corpus["entry"]["revision"], "dataset_sha256": corpus["entry"]["sha256"],
                         "windows": len(corpus["windows"]), "source_manifest_sha256": corpus["source_manifest_sha256"],
                         "source_file_sha256": corpus["source_file_sha256"]} for name, corpus in datasets.items()},
            "independent_summary": summary,
            "checks": ["Pinned Git byte slices and UTF-8 excerpt/full-window SHA-256", "Exact serialized compact-context tokens and source-character caps",
                       "Full inclusive evidence bounds, OR groups, AND completion, candidate50 and retained coverage",
                       "All expected query/condition rows and independent metric/timing denominators",
                       "Dataset/source/model/settings/package/implementation provenance and calibration-only threshold"],
            "limits": ["Uses the frozen CPU AST extractor to reconstruct window IDs; matching, slices, metrics and token counts are independently computed.",
                       "No per-row wall timestamp is saved. Policy-before-first-holdout is supported by the matching frozen runner code path and CPU ordering tests, not an independent timestamped event log.",
                       "Validates saved latency samples and fresh-query code paths; does not reproduce GPU inference or infer external agent token savings.",
                       "Source labels were authored and independently reviewed before retrieval; this artifact verifies their application, not representative workload quality."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/quality_manifest.json"))
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="Default: RUN/audit.json")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    run, manifest = (root / args.run).resolve(), (root / args.manifest).resolve()
    output = args.output or run / "audit.json"
    try:
        record = audit(run, manifest, root)
    except (AuditError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        record = {"version": 1, "status": "failed", "error": str(error),
                  "audited_at_utc": datetime.now(timezone.utc).isoformat(), "audit_script_sha256": sha(Path(__file__).read_bytes())}
    output.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: record[key] for key in ("status", "rows", "expected_rows", "error") if key in record}))
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
