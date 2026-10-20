#!/usr/bin/env python3
"""Independently audit saved multi-file experiments; never retrieve or load models."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime
import gzip
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import statistics
import struct
import subprocess
from unittest.mock import patch

from report_language_quality import compact_text, groups, physical_lines, rank_groups

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ("dense", "multifile", "hybrid")
STAGES = ("retrieved", "ranked", "deduplicated", "eligible", "anchors", "expanded",
          "character_packed", "token_packed")
SETTINGS = dict(max_results=8, source_budget=12000, token_budget=4096,
                include_dependencies=True, rerank=False, candidates=50)
GATE = ("Multifile versus dense: at least one net multi-file gain with no complete "
        "single-file loss; warm median overhead <=25%. Retain all group regressions. "
        "No tuning on fresh outcomes.")
TOKENIZER_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
TOKENIZER_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def finite(value, message):
    require(type(value) in (float, int) and math.isfinite(value) and value >= 0, message)


def schedule(queries):
    result = []
    for repetition in range(1, 4):
        for number, query in enumerate(queries):
            offset = (number + repetition - 1) % len(CONDITIONS)
            for condition in CONDITIONS[offset:] + CONDITIONS[:offset]:
                result.append((query["id"], condition, repetition))
    return result


def multifile(gold):
    return len(gold) > 1 and not set.intersection(*({t["path"] for t in g} for g in gold))


def checked_window(row, inventory, sources, *, full):
    """Verify identity, literal physical lines, full-window hash and clipping flag."""
    require(row["id"] in inventory, "Window is outside the frozen extraction")
    original = inventory[row["id"]]
    require(all(row[k] == original[k] for k in ("path", "symbol", "start_line")),
            "Window metadata differs from extraction")
    start, end = row["start_line"], row["end_line"]
    require(type(start) is int and type(end) is int
            and 1 <= start <= end <= original["end_line"], "Invalid physical window bounds")
    require(row["window_end_line"] == original["end_line"]
            and row["content_hash"] == original["content_hash"], "Full window hash/bounds changed")
    text = "".join(sources[row["path"]][start - 1:end])
    require(bool(text) and row["source"] == text and row["source_hash"] == sha(text.encode()),
            "Window source/hash differs from pinned physical lines")
    require(end == start + len(physical_lines(text)) - 1, "Physical line count differs")
    require(type(row["truncated"]) is bool and row["truncated"] == (end < original["end_line"]),
            "Truncation flag differs from saved source bounds")
    require(not full or end == original["end_line"], "Candidate/stage is not a full window")
    require(type(row["retrieval_score"]) in (int, float) and math.isfinite(row["retrieval_score"]),
            "Invalid recorded ranking scalar")
    require(row["h2o_score"] is None and row.get("qwen_score") is None,
            "Unexpected reranker score")
    require(row["origin"] in ("retrieval", "dependency"), "Unknown source origin")


def row_identity(rows):
    return [r["id"] for r in rows]


def excerpt_identity(row):
    return {key: row[key] for key in ("id", "path", "symbol", "start_line", "end_line",
            "window_end_line", "source", "source_hash", "content_hash", "truncated",
            "origin", "related_to", "retrieval_score")}


def group_diagnostics(stages, gold):
    ranks = {stage: rank_groups(stages[stage], gold) for stage in STAGES}
    boundaries = (("character_packed", "token_clipping"), ("expanded", "character_clipping"),
                  ("eligible", "dependency_displacement"), ("deduplicated", "result_cap"),
                  ("ranked", "deduplication"), ("retrieved", "shortlist"))
    return [{"group": i + 1,
             "outcome": "delivered" if ranks["token_packed"][i] is not None else next(
                 (cause for stage, cause in boundaries if ranks[stage][i] is not None), "upstream"),
             "ranks": {stage: ranks[stage][i] for stage in STAGES}} for i in range(len(gold))]


def audit_row(row, query, binding, inventory, sources, encoder):
    response, stages = row["response"], row["stages"]
    gold = groups(query)
    require(row["kind"] == query["kind"] and type(row["multi_file"]) is bool
            and row["multi_file"] == multifile(gold), "Query kind/multi-file denominator changed")
    require(response["query"] == query["query"] and response["retrieval"] == row["retrieval"]
            and response["rerank"] is False, "Query/condition/reranker changed")
    require(set(stages) == set(STAGES), "Stage inventory differs")
    require(type(row["query_embedding_calls"]) is int and row["query_embedding_calls"] == 1,
            "Expected one fresh full-query embedding per measured request")
    vector = row["query_vector"]
    require(isinstance(vector, list) and len(vector) == binding["embedding"]["dimensions"]
            and all(type(v) in (int, float) and math.isfinite(v) for v in vector),
            "Invalid saved query vector")
    packed = struct.pack("<" + "f" * len(vector), *vector)
    require(list(struct.unpack("<" + "f" * len(vector), packed)) == vector
            and sha(packed) == row["query_vector_sha256"], "Float32 query vector/hash changed")
    require(abs(math.sqrt(sum(v * v for v in vector)) - 1) < .001,
            "Saved query vector is not L2 normalized")
    finite(row["elapsed_backend_s"], "Invalid backend latency")
    require(row["elapsed_backend_s"] > 0, "Zero backend latency")
    for name, elapsed in response["timings"].items():
        finite(elapsed, "Invalid backend phase timing: " + name)
    require(response["timings"]["total_s"] <= row["elapsed_backend_s"],
            "Outer backend duration excludes part of the backend request")
    require(response["timings"]["h2o_s"] == response["timings"]["qwen_s"] == 0,
            "Unexpected reranker work")
    require(response["match_evidence"]["threshold"] is None
            and response["match_status"] in ("candidates", "no_candidates"),
            "Unexpected abstention/threshold policy")
    require(response["source_budget"] == SETTINGS["source_budget"], "Source budget changed")
    usage = response["token_usage"]
    require(usage["encoding"] == "cl100k_base" and usage["budget"] == SETTINGS["token_budget"]
            and type(usage["tokens"]) is int and type(usage["truncated"]) is bool,
            "Token settings/counter changed")
    require(usage["tokens"] == len(encoder.encode(compact_text(response), disallowed_special=()))
            <= SETTINGS["token_budget"], "Exact compact token proof failed")
    for stage in STAGES:
        require(len(set(row_identity(stages[stage]))) == len(stages[stage]), "Duplicate window within stage")
        for window in stages[stage]:
            checked_window(window, inventory, sources, full=stage not in ("character_packed", "token_packed"))
    raw = stages["retrieved"]
    require(len(raw) == min(SETTINGS["candidates"], len(inventory))
            == response["candidate_windows"] == response["retrieval_candidate_windows"],
            "Raw candidate count/cap changed")
    require(stages["ranked"] == raw, "Unplanned reranking/shortlisting")
    require(all(r["origin"] == "retrieval" and r["related_to"] is None for r in raw),
            "Raw candidate has dependency origin")
    distinct, seen = [], set()
    for window in raw:
        key = window["path"], window["symbol"]
        if key not in seen:
            distinct.append(window)
            seen.add(key)
    require(stages["deduplicated"] == distinct and response["available_symbols"] == len(distinct),
            "First-window symbol deduplication differs")
    require(stages["eligible"] == distinct[:8] and stages["anchors"] == distinct[:5],
            "Result/anchor prefix differs")
    expanded = stages["expanded"]
    require(len(expanded) <= 8 and expanded[:len(stages["anchors"])] == stages["anchors"],
            "Expanded list changed its anchors/cap")
    require(len({(r["path"], r["symbol"]) for r in expanded}) == len(expanded),
            "Expanded symbol duplicated")
    anchors = {r["id"]: r for r in stages["anchors"]}
    eligible = {r["id"]: r for r in stages["eligible"]}
    dependency_counts = Counter()
    for window in expanded:
        if window["origin"] == "dependency":
            require(window["related_to"] in anchors, "Dependency lacks an included anchor")
            require(window["retrieval_score"] == anchors[window["related_to"]]["retrieval_score"],
                    "Dependency does not retain anchor score")
            dependency_counts[window["related_to"]] += 1
        else:
            require(window["id"] in eligible and window == eligible[window["id"]],
                    "Expanded retrieval row is outside eligible prefix")
    require(all(count <= 2 for count in dependency_counts.values()), "Dependency cap exceeded")
    # Reconstruct whole-line character packing directly from the saved expanded list.
    remaining, expected_character = SETTINGS["source_budget"], []
    for window in expanded:
        prefix, used = [], 0
        for line in physical_lines(window["source"]):
            if used + len(line) > remaining:
                break
            prefix.append(line)
            used += len(line)
        text = "".join(prefix)
        if text:
            remaining -= len(text)
            expected_character.append((window["id"], text))
    require([(r["id"], r["source"]) for r in stages["character_packed"]] == expected_character,
            "Character packing differs from the whole-line prefix policy")
    require([excerpt_identity(r) for r in response["results"]]
            == [excerpt_identity(r) for r in stages["token_packed"]],
            "Delivered excerpts differ from final diagnostic stage")
    character = {r["id"]: r for r in stages["character_packed"]}
    final_ids = row_identity(response["results"])
    require(final_ids == [r["id"] for r in stages["character_packed"] if r["id"] in set(final_ids)],
            "Token packing changed excerpt membership/order")
    for window in response["results"]:
        checked_window(window, inventory, sources, full=False)
        previous = character[window["id"]]
        require(window["end_line"] <= previous["end_line"]
                and all(window[k] == previous[k] for k in ("origin", "related_to", "retrieval_score")),
                "Token packing changed provenance or added source")
    token_cut = len(character) != len(final_ids) or any(
        r["source"] != character[r["id"]]["source"] for r in response["results"])
    character_cut = len(expected_character) != len(expanded) or any(
        r["truncated"] for r in stages["character_packed"])
    require(usage["truncated"] == token_cut and type(response["budget_exhausted"]) is bool
            and response["budget_exhausted"] == (character_cut or token_cut),
            "Budget-exhaustion flags differ from actual clipping")
    chars = sum(len(r["source"]) for r in response["results"])
    require(len(response["results"]) <= 8 and response["total_source_chars"] == chars <= 12000,
            "Result/source cap or counter differs")
    require(row["candidate_ranks"] == rank_groups(raw, gold)
            and row["delivered_ranks"] == rank_groups(response["results"], gold),
            "Declared evidence ranks differ from verified source spans")
    diagnostic = group_diagnostics(stages, gold)
    require(row["group_diagnostics"] == diagnostic, "Declared stage attribution differs")
    return diagnostic


def summarize(rows):
    """Unique first-repetition quality, all nine-per-query warm timing samples."""
    output = {}
    for condition in CONDITIONS:
        selected = [r for r in rows if r["retrieval"] == condition]
        first = [r for r in selected if r["repetition"] == 1]
        positive = [r for r in first if r["kind"] == "present"]
        multi = [r for r in positive if r["multi_file"]]
        single = [r for r in positive if not r["multi_file"]]
        timings = sorted(r["elapsed_backend_s"] for r in selected)
        initial = {r["query_id"]: r["delivered_ranks"] for r in first}
        output[condition] = {
            "present": len(positive), "complete": sum(None not in r["delivered_ranks"] for r in positive),
            "multi_file": len(multi), "multi_complete": sum(None not in r["delivered_ranks"] for r in multi),
            "single_file": len(single), "single_complete": sum(None not in r["delivered_ranks"] for r in single),
            "candidate_complete": sum(None not in r["candidate_ranks"] for r in positive),
            "required_groups": sum(len(r["delivered_ranks"]) for r in positive),
            "delivered_groups": sum(sum(v is not None for v in r["delivered_ranks"]) for r in positive),
            "absent_with_candidates": sum(r["kind"] == "absent" and bool(r["response"]["results"]) for r in first),
            "missing_group_stages": dict(Counter(g["outcome"] for r in first
                for g in r["group_diagnostics"] if g["outcome"] != "delivered")),
            "warm_latency_samples": len(timings), "median_backend_s": statistics.median(timings),
            "p95_backend_s": timings[math.ceil(.95 * len(timings)) - 1],
            "median_context_tokens": statistics.median(r["response"]["token_usage"]["tokens"] for r in first),
            "repeat_rank_changes": sum(r["delivered_ranks"] != initial[r["query_id"]] for r in selected),
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


def gate_result(rows, metrics):
    primary = {(r["query_id"], r["retrieval"]): r for r in rows if r["repetition"] == 1}
    single_losses = [qid for (qid, mode), r in primary.items() if mode == "dense"
                     and r["kind"] == "present" and not r["multi_file"]
                     and None not in r["delivered_ranks"]
                     and None in primary[qid, "multifile"]["delivered_ranks"]]
    net_multi = metrics["multifile"]["multi_complete"] - metrics["dense"]["multi_complete"]
    ratio = metrics["multifile"]["median_backend_s"] / metrics["dense"]["median_backend_s"]
    return {"passed": net_multi >= 1 and not single_losses and ratio <= 1.25,
            "net_multi_file_complete_gain": net_multi, "complete_single_file_losses": single_losses,
            "warm_median_ratio": ratio, "warm_median_overhead_percent": (ratio - 1) * 100,
            "quality_passed": net_multi >= 1 and not single_losses, "latency_passed": ratio <= 1.25,
            "all_group_regressions": metrics["paired"]["group_regressions"]}


def build_report(study, dataset, corpus, *, project_root=ROOT, source_review=None, encoder):
    plan, summary = (read(study / name) for name in ("plan.json", "summary.json"))
    binding = plan["binding"]
    require(binding["settings"] == SETTINGS and binding["retrieval"] == list(CONDITIONS)
            and binding["repetitions"] == 3 and plan["quality_repetition"] == 1
            and plan["gate"] == GATE, "Frozen evaluation protocol differs")
    require(summary["complete"] is True and summary["quality_repetition"] == 1,
            "Incomplete or inflated quality summary")
    require(sha(dataset.read_bytes()) == binding["dataset_sha256"], "Dataset changed after freeze")
    labels = read(dataset)
    queries = {q["id"]: q for q in labels["queries"]}
    require(len(queries) == len(labels["queries"])
            and all(isinstance(qid, str) and qid for qid in queries), "Duplicate/invalid query IDs")
    require(labels["revision"] == binding["revision"], "Dataset revision differs from plan")
    for name, digest in binding["implementation_sha256"].items():
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts, "Implementation path escapes project")
        require(sha((project_root / relative).read_bytes()) == digest, "Frozen implementation changed: " + name)
        committed = subprocess.check_output(["git", "-C", str(project_root), "show", plan["source_commit"] + ":" + name])
        require(sha(committed) == digest, "Implementation does not match frozen source commit: " + name)
    plan_relative = study.resolve().relative_to(project_root.resolve()).as_posix() + "/plan.json"
    committed_plan = subprocess.check_output(["git", "-C", str(project_root), "show", "HEAD:" + plan_relative])
    require(committed_plan == (study / "plan.json").read_bytes(), "Plan is not the saved committed freeze")
    require(subprocess.check_output(["git", "-C", str(corpus), "rev-parse", "HEAD"], text=True).strip()
            == binding["revision"] and not subprocess.check_output(
                ["git", "-C", str(corpus), "status", "--porcelain"], text=True).strip(),
            "Corpus is not clean at the frozen revision")
    from semantic_search.corpus import _source_files, extract_snippets
    from semantic_search.languages import decode_source
    paths = _source_files(corpus, corpus / labels["source_root"], labels.get("source_exclusions", ()))
    source_hashes = {p.relative_to(corpus).as_posix(): sha(p.read_bytes()) for p in paths}
    require(source_hashes == binding["source_sha256"], "Frozen source membership/bytes differ")
    decoded = {p.relative_to(corpus).as_posix(): decode_source(p, p.read_bytes()) for p in paths}
    sources = {path: physical_lines(text) for path, text in decoded.items()}
    extracted = [asdict(s) for s in extract_snippets(corpus, labels["source_root"], 160, 40,
        sources=decoded, source_exclusions=labels.get("source_exclusions", ()))]
    require(sha(json.dumps(extracted, sort_keys=True).encode()) == binding["inventory_sha256"],
            "Frozen extraction inventory differs")
    inventory = {r["id"]: r for r in extracted}
    require(len(inventory) == len(extracted), "Duplicate extraction identity")
    for query in queries.values():
        for alternatives in groups(query):
            for target in alternatives:
                path = target["path"]
                lo, hi = target["evidence_lines"]
                require(type(lo) is int and type(hi) is int and 1 <= lo <= hi <= len(sources[path]),
                        "Invalid executable gold bounds")
                require(sha("".join(sources[path][lo - 1:hi]).encode()) == target["evidence_sha256"]
                        and source_hashes[path] == target["source_file_sha256"], "Executable gold bytes differ")
        require(None not in rank_groups(extracted, groups(query)), "Gold lacks a containing extraction window")
    review_identity = None
    if binding["classification"] == "repository_holdout":
        require(source_review is not None, "Holdout requires independent source review")
        review = read(source_review)
        require(sha(source_review.read_bytes()) == binding["source_review_sha256"]
                and review["status"] == "passed_source_review_before_holdout_retrieval"
                and review["dataset"]["sha256"] == binding["dataset_sha256"]
                and review["corpus"]["commit"] == binding["revision"], "Source-review binding differs")
        controls = review["leakage_controls"]
        require(controls["holdout_retrieval_calls_invoked_by_reviewer"] == 0
                and controls["holdout_model_or_embedding_calls_invoked_by_reviewer"] == 0
                and controls["holdout_indexing_calls_invoked_by_reviewer"] == 0
                and controls["holdout_rankings_scores_candidates_or_rule_activation_outputs_observed"] is False
                and datetime.fromisoformat(review["reviewed_at_utc"]) <= datetime.fromisoformat(plan["frozen_at"]),
                "Holdout review is post-output or not independent")
        require(review["corpus"]["source_inventory_sha256"] == labels["source_inventory_sha256"]
                and review["corpus"]["source_files"] == len(source_hashes)
                and review["extraction"]["windows"] == len(extracted), "Reviewed source inventory differs")
        require(sha((project_root / review["protocol"]["path"]).read_bytes()) == review["protocol"]["sha256"]
                and all(binding["implementation_sha256"].get(p) == digest for p, digest in
                        review["extraction"]["source_implementations_sha256"].items()),
                "Reviewed protocol/extraction implementation changed")
        canonical = {p: {"bytes": len((corpus / p).read_bytes()), "sha256": digest}
                     for p, digest in source_hashes.items()}
        require(canonical == labels["source_files"] and sha((json.dumps(canonical,
            sort_keys=True, separators=(",", ":")) + "\n").encode()) == labels["source_inventory_sha256"],
            "Holdout complete source inventory differs")
        require(sha((corpus / labels["license"]["upstream_path"]).read_bytes()) == labels["license"]["sha256"],
                "Holdout license identity differs")
        review_identity = {"path": str(source_review.relative_to(project_root)), "sha256": binding["source_review_sha256"]}
    else:
        require(binding["classification"] == "development" and binding["source_review_sha256"] is None,
                "Unknown role or unexpected review receipt")
    raw = (study / "searches.jsonl.gz").read_bytes()
    require(sha(raw) == summary["raw_sha256"], "Raw search receipts changed")
    rows = [json.loads(line) for line in gzip.decompress(raw).decode().split("\n") if line.strip()]
    require(all(type(r["repetition"]) is int and 1 <= r["repetition"] <= 3 for r in rows),
            "Invalid repetition identity")
    observed = [(r["query_id"], r["retrieval"], r["repetition"]) for r in rows]
    require(observed == schedule(labels["queries"]) and len(set(observed)) == len(observed),
            "Request inventory/order differs from the frozen 3-condition/3-repetition protocol")
    require(summary["requests"] == len(rows) == len(queries) * 9
            and summary["unique_queries"] == len(queries), "Unique-query/request denominator differs")
    require(summary["measured_query_embeddings"] == len(rows)
            and summary["completed_query_embeddings"] == len(rows) + 4,
            "Measured and four setup/warmup query-call totals differ")
    setup = summary["setup"]
    finite(setup["index_and_model_warm_s"], "Invalid setup duration")
    status = setup["status"]
    require(status["ready"] is True and status["dense_ready"] is True
            and status["last_error"] is None and status["windows"] == len(extracted), "Setup was not ready")
    initial = status["indexstats"]
    vectors, initial_candidates, changes = {}, {}, []
    diagnostics = []
    for row in rows:
        query = queries[row["query_id"]]
        diagnostics.extend(audit_row(row, query, binding, inventory, sources, encoder))
        response = row["response"]
        scope, stats = response["scope"], response["indexstats"]
        require(scope["source_root"] == labels["source_root"] and scope["source_exclusions"]
                == labels.get("source_exclusions", []) and scope["exclusions_omitted"] == 0,
                "Configured source scope changed")
        require(stats["indexed_windows"] == stats["reused_windows"] == stats["deleted_windows"] == 0
                and stats["memory_cache_hit"] is True and stats["total_windows"] == len(extracted)
                and stats["source_files"] == len(source_hashes)
                and all(stats[k] == initial[k] for k in ("source_manifest", "model_fingerprint")),
                "Measured query was not warm or source/model identity changed")
        key = row["query_id"], row["retrieval"]
        ids = row_identity(row["stages"]["retrieved"])
        require(key not in initial_candidates or initial_candidates[key] == ids,
                "Raw candidate order changed across repetitions")
        initial_candidates[key] = ids
        if row["query_id"] in vectors and vectors[row["query_id"]] != row["query_vector_sha256"]:
            changes.append({"query_id": row["query_id"], "condition": row["retrieval"], "repetition": row["repetition"]})
        vectors.setdefault(row["query_id"], row["query_vector_sha256"])
    metrics = summarize(rows)
    require(metrics == summary["metrics"], "Producer summary differs from independently recomputed metrics")
    gate = gate_result(rows, metrics)
    return {"checks_passed": True, "classification": binding["classification"],
        "dataset": {"path": str(dataset.relative_to(project_root)), "sha256": binding["dataset_sha256"]},
        "artifact_sha256": {name: sha((study / name).read_bytes()) for name in
                            ("plan.json", "summary.json", "searches.jsonl.gz")},
        "source_commit": plan["source_commit"], "source_revision": binding["revision"],
        "source_files_verified": len(source_hashes), "extraction_windows_verified": len(extracted),
        "inventory_sha256": binding["inventory_sha256"], "source_review": review_identity,
        "implementation_files_verified": len(binding["implementation_sha256"]),
        "requests_verified": len(rows), "unique_queries": len(queries), "quality_repetition": 1,
        "conditions": list(CONDITIONS), "repetitions": 3,
        "literal_candidate_windows_verified": sum(len(r["stages"]["retrieved"]) for r in rows),
        "literal_delivered_excerpts_verified": sum(len(r["response"]["results"]) for r in rows),
        "exact_compact_token_counts_verified": len(rows), "per_stage_group_rows_verified": len(diagnostics),
        "fresh_query_embedding_receipts_verified": len(rows), "query_vector_changes_from_first_query": changes,
        "metrics": metrics, "frozen_gate": {"definition": GATE, **gate},
        "producer_sha256": sha(Path(__file__).read_bytes()),
        "independent_helper_sha256": sha((project_root / "scripts/report_language_quality.py").read_bytes()),
        "limits": ["No producer, ranking, selection, reference-graph, backend or native model helper is imported by this auditor.",
                   "For multifile, the saved retrieved/ranked stages are the post-merge retriever output, not the union of dense and clause-FTS component lists. Its upstream losses mean absent from the merged candidate pool; component recall is not measured by these receipts.",
                   "Saved window/source/score provenance, stage transitions and source coverage are checked; the auditor does not independently rerun ranking or rebuild static graph edges.",
                   "Quality denominators count each labeled query once using repetition one; repeats supply stability and warm timing samples only.",
                   "Backend warm timings include source checks, one query embedding, packing and lightweight selector capture; exclude transport, cold setup, post-request diagnostic replay and serialization.",
                   "Small source-authored sample, one Mac; no external adoption, coding correctness, billing or speedup claim.",
                   "Absent queries report returned candidates, not a proof of feature absence or false-positive precision."]}


def findings(report):
    lines = ["# Independently audited multi-file retrieval comparison", "",
             f"Role: **{report['classification']}**. {report['unique_queries']} unique queries; "
             f"{report['requests_verified']} warm requests across three conditions and three repetitions. "
             "Quality uses only repetition one.", "",
             "| Condition | Complete positives | Complete multi-file | Complete single-file | Groups | Warm median | Warm p95 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for mode in CONDITIONS:
        m = report["metrics"][mode]
        lines.append(f"| {mode} | {m['complete']}/{m['present']} | {m['multi_complete']}/{m['multi_file']} | "
                     f"{m['single_complete']}/{m['single_file']} | {m['delivered_groups']}/{m['required_groups']} | "
                     f"{m['median_backend_s']:.4f}s | {m['p95_backend_s']:.4f}s |")
    gate = report["frozen_gate"]
    lines += ["", f"Frozen gate: **{'passed' if gate['passed'] else 'failed'}**. "
              f"Net complete multi-file gain {gate['net_multi_file_complete_gain']:+d}; "
              f"warm median overhead {gate['warm_median_overhead_percent']:+.1f}%. "
              f"Previously complete single-file losses: {gate['complete_single_file_losses']}.", "",
              f"Complete gains: {report['metrics']['paired']['complete_gains']}.", "",
              f"Complete losses: {report['metrics']['paired']['complete_losses']}.", "",
              f"All group regressions, including partial cases: {gate['all_group_regressions']}.", "",
              "Every saved retriever-output candidate and delivered excerpt was checked against the pinned source and extraction inventory; "
              "group coverage, stage losses, exact compact tokens, request schedule and summary metrics were independently recomputed.", ""]
    for mode in CONDITIONS:
        m = report["metrics"][mode]
        lines.append(f"- {mode}: missing-group stages {m['missing_group_stages']}; "
                     f"repeat delivered-rank changes {m['repeat_rank_changes']}; "
                     f"absent queries with returned candidates {m['absent_with_candidates']}.")
    lines += ["", *["- " + limit for limit in report["limits"]], ""]
    return "\n".join(lines)


def load_encoder(cache):
    """Use verified existing rank bytes; never permit tokenizer downloads."""
    import tiktoken
    import tiktoken.load
    raw = (cache / hashlib.sha1(TOKENIZER_URL.encode()).hexdigest()).read_bytes()
    require(sha(raw) == TOKENIZER_SHA256, "Existing tokenizer cache hash differs")
    def cached(url, expected_hash=None):
        require(url == TOKENIZER_URL, "Unexpected tokenizer download request")
        require(expected_hash in (None, TOKENIZER_SHA256), "Unexpected tokenizer rank hash")
        return raw
    with patch.object(tiktoken.load, "read_file_cached", cached):
        return tiktoken.get_encoding("cl100k_base")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "dataset", "corpus", "tokenizer-cache"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-review", type=Path)
    args = parser.parse_args()
    for name in ("audit.json", "findings.md"):
        require(not (args.study / name).exists(), "Refusing to overwrite audit output: " + name)
    plan = read(args.study / "plan.json")
    for name, version in plan["binding"]["runtime"].items():
        require(importlib.metadata.version(name) == version, "Audit package differs from freeze: " + name)
    encoder = load_encoder(args.tokenizer_cache)
    report = build_report(args.study.resolve(), args.dataset.resolve(), args.corpus.resolve(),
                          source_review=args.source_review.resolve() if args.source_review else None,
                          encoder=encoder)
    report["tokenizer_rank_bytes_sha256"] = TOKENIZER_SHA256
    with (args.study / "audit.json").open("x") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    with (args.study / "findings.md").open("x") as stream:
        stream.write(findings(report))
    print(json.dumps({"study": str(args.study), "checks_passed": True,
                      "gate": report["frozen_gate"], "audit_sha256": sha((args.study / "audit.json").read_bytes())}))


if __name__ == "__main__":
    main()
