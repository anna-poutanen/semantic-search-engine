#!/usr/bin/env python3
"""Read frozen language receipts and independently verify/report delivered evidence.

No backend, parser, weights, HTTP client or evaluation runner is imported.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = ("dataset", "query_id", "condition", "repetition")


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_text())


def jsonl(path):
    # Unicode U+2028/U+2029 can occur literally inside valid JSON strings.
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


def physical_lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def groups(query):
    if query["kind"] == "absent":
        require(query.get("targets") == [] and "target_groups" not in query, "Invalid absent gold")
        return []
    result = query.get("target_groups", [query.get("targets")])
    require(query["kind"] == "present" and result and all(result), "Invalid positive evidence groups")
    return result


def compact_text(response):
    fields = ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")
    rows = [{**{key: row[key] for key in fields}, "origin": row.get("origin", "retrieval"),
             "related_to": row.get("related_to")} for row in response["results"]]
    envelope = {"query": response["query"], "match_status": response.get("match_status", "candidates"),
                "results": rows, "budget_exhausted": response.get("budget_exhausted", False),
                "token_usage": response["token_usage"]}
    if "scope" in response:
        envelope["scope"] = response["scope"]
    return json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def rank_groups(windows, gold):
    return [next((number for number, window in enumerate(windows, 1)
                  if any(window["path"] == target["path"]
                         and window["start_line"] <= target["evidence_lines"][0]
                         <= target["evidence_lines"][1] <= window["end_line"]
                         for target in alternatives)), None) for alternatives in gold]


def verify_window(window, inventory, sources, *, delivered):
    require(window["id"] in inventory, "Window identity is outside frozen extraction")
    full = inventory[window["id"]]
    require(all(window[key] == full[key] for key in ("path", "symbol", "start_line")), "Frozen window metadata changed")
    start, end = window["start_line"], window["end_line"]
    require(type(start) is int and type(end) is int and 1 <= start <= end <= full["end_line"], "Window bounds changed")
    expected = "".join(sources[window["path"]][start - 1:end])
    require(window["source_hash"] == sha(expected.encode()), "Window source hash differs from frozen saved bytes")
    if delivered:
        require(window["source"] == expected, "Delivered source differs from frozen saved bytes")
        require(window["end_line"] == start + len(physical_lines(window["source"])) - 1, "Physical line bounds changed")
        require(type(window["truncated"]) is bool and window["truncated"] == (end < full["end_line"]), "Truncation flag differs from actual saved bounds")
    else:
        require(end == full["end_line"] and window["source_hash"] == full["source_hash"], "Candidate is not a full frozen window")


def percentile(values):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(.95 * len(ordered)) - 1)] if ordered else None


def metrics(rows):
    primary = [row for row in rows if row["repetition"] == 1]
    positive = [row for row in primary if row["kind"] == "present"]
    negative = [row for row in primary if row["kind"] == "absent"]
    complete = [max(row["evidence_ranks"]) if None not in row["evidence_ranks"] else None for row in positive]
    multi = [row for row in positive if row["multi_file_and"]]
    all_times = [row["elapsed_http_s"] for row in rows]
    base = {row["query_id"]: (row["evidence_ranks"], row["candidate_evidence_ranks"]) for row in primary}
    result = {
        "present_queries": len(positive), "absent_queries": len(negative),
        "candidate_complete": sum(None not in row["candidate_evidence_ranks"] for row in positive),
        "delivered_complete": sum(None not in row["evidence_ranks"] for row in positive),
        "required_groups": sum(len(row["evidence_ranks"]) for row in positive),
        "candidate_groups": sum(sum(rank is not None for rank in row["candidate_evidence_ranks"]) for row in positive),
        "delivered_groups": sum(sum(rank is not None for rank in row["evidence_ranks"]) for row in positive),
        "multi_file_queries": len(multi), "multi_file_complete": sum(None not in row["evidence_ranks"] for row in multi),
        "absent_with_candidates": sum(row["response"]["match_status"] == "candidates" for row in negative),
        "absent_no_candidates": sum(row["response"]["match_status"] == "no_candidates" for row in negative),
        "absent_rejected": sum(row["response"]["match_status"] == "insufficient_evidence" for row in negative),
        "first_complete_evidence_ranks": complete,
        "complete_evidence_mrr": sum(1 / rank for rank in complete if rank is not None) / len(positive) if positive else None,
        "latency_samples": len(all_times), "median_http_s": statistics.median(all_times), "p95_http_s": percentile(all_times),
        "max_compact_context_tokens": max(row["response"]["token_usage"]["tokens"] for row in rows),
        "primary_truncated_queries": sum(row["response"].get("budget_exhausted", False) for row in primary),
        "repeat_coverage_mismatches": [row["query_id"] for row in rows if
            (row["evidence_ranks"], row["candidate_evidence_ranks"]) != base[row["query_id"]]],
        "primary_misses": [{"query_id": row["query_id"], "evidence_ranks": row["evidence_ranks"]}
                           for row in positive if None in row["evidence_ranks"]],
    }
    result.update({f"complete_at_{depth}": sum(rank is not None and rank <= depth for rank in complete) for depth in (1, 3, 5, 8)})
    return result


def miss_details(row, gold, inventory):
    candidates, returned = row["candidate_windows"], row["response"]["results"]
    first, seen = [], set()
    for window in candidates:
        symbol = (window["path"], window["symbol"])
        if symbol not in seen:
            first.append(window)
            seen.add(symbol)
    details = []
    for index, alternatives in enumerate(gold):
        if row["evidence_ranks"][index] is not None:
            continue
        eligible = [window for window in returned if rank_groups([inventory[window["id"]]], [alternatives])[0] is not None]
        cuts = [{"id": window["id"], "path": window["path"], "delivered_lines": [window["start_line"], window["end_line"]],
                 "full_window_lines": [inventory[window["id"]]["start_line"], inventory[window["id"]]["end_line"]],
                 "truncated": window["truncated"]} for window in eligible]
        rank = row["candidate_evidence_ranks"][index]
        details.append({"group": index + 1, "candidate_rank": rank,
                        "outside_first8_raw_candidate_windows": rank is not None and rank > 8,
                        "first_distinct_symbol_evidence_rank": rank_groups(first, [alternatives])[0],
                        "evidence_cut_within_returned_window": cuts,
                        "observation": "evidence cut within returned window" if cuts else
                            "no complete evidence window in retrieved candidates" if rank is None else
                            "complete evidence candidate exists but was not delivered"})
    return {"dataset": row["dataset"], "condition": row["condition"], "query_id": row["query_id"],
            "candidate_ranks": row["candidate_evidence_ranks"], "delivered_ranks": row["evidence_ranks"],
            "budget_exhausted": row["response"]["budget_exhausted"],
            "token_truncated": row["response"]["token_usage"]["truncated"],
            "returned_dependency_excerpts": sum(window.get("origin") == "dependency" for window in returned), "missing_groups": details}


def build_report(study, *, project_root=ROOT, encoder=None):
    frozen, summary, setups, preflight = (read(study / name) for name in
        ("frozen-config.json", "summary.json", "setups.json", "extractor-preflight.json"))
    runtime = read(study / "runtime-audit.json")
    require(runtime["checks_passed"] and all(runtime["checks"].values()), "Recorded runtime audit failed")
    require(runtime["actual_implementation_sha256"] == frozen["implementation_sha256"]
            and runtime["actual_packages"] == frozen["packages"]
            and runtime["actual_python"] == frozen["python"]
            and runtime["frozen_git_commit"] == frozen["git_commit"], "Recorded runtime identity differs")
    require(set(runtime["artifact_sha256"]) == {"frozen-config.json", "summary.json", "searches.jsonl",
                                               "setups.json", "extractor-preflight.json"},
            "Runtime proof input inventory differs")
    for name, digest in runtime["artifact_sha256"].items():
        require(sha((study / name).read_bytes()) == digest, "Runtime proof input artifact changed: " + name)
    require(summary["complete"] is True and summary["quality_repetition"] == 1, "Incomplete or inflated quality summary")
    rows = jsonl(study / "searches.jsonl")
    require([{key: row[key] for key in IDENTITY} for row in rows] == frozen["schedule"], "Request identities/order differ from freeze")
    require(len(rows) == frozen["planned_requests"] == summary["requests"], "Request count differs")
    require(len({tuple(row[key] for key in IDENTITY) for row in rows}) == len(rows), "Duplicate/retried request identity")
    require(summary["searches_sha256"] == sha((study / "searches.jsonl").read_bytes()), "Search receipts changed")
    require(summary["setups"] == setups, "Setup summary differs from saved setup evidence")
    manifest_path = project_root / "benchmarks/language_quality_manifest.json"
    require(sha(manifest_path.read_bytes()) == frozen["manifest_sha256"] == preflight["manifest_sha256"], "Labels differ from freeze")
    require(preflight["checks_passed"] and preflight["model_calls"] == 0, "Source preflight failed")
    require(preflight["implementation_sha256"] == frozen["implementation_sha256"], "Preflight implementation differs")
    require(frozen["h2o"] is False and frozen["threshold"] is None and frozen["query_cache"] is False, "Unexpected reranker/threshold/cache")
    if encoder is None:
        require(importlib.metadata.version("tiktoken") == frozen["packages"]["tiktoken"], "Token counter version differs")
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(project_root / ".cache/tokenizers"))
        import tiktoken
        encoder = tiktoken.get_encoding("cl100k_base")
    manifest = read(manifest_path)
    dataset_reports, missed, absent = {}, [], []
    authored = []
    saved_windows = 0
    settings = frozen["settings"]
    require(frozen["conditions"] == ["bm25", "dense", "hybrid"] and frozen["repetitions"] == 3,
            "Unexpected condition/repetition protocol")
    for entry in manifest["datasets"]:
        identifier = entry["id"]
        dataset_path = project_root / entry["dataset"]
        require(sha(dataset_path.read_bytes()) == entry["sha256"], "Dataset label hash differs")
        queries = {query["id"]: query for query in read(dataset_path)["queries"]}
        authored.extend(queries.values())
        evidence = next(row for row in preflight["datasets"] if row["id"] == identifier)
        snapshot = next(row for row in frozen["sources"] if row["id"] == identifier)
        require(evidence["source_file_sha256"] == snapshot["source_file_sha256"], "Source membership differs")
        require(sha(json.dumps(evidence["window_inventory"], sort_keys=True).encode()) == snapshot["window_inventory_sha256"], "Window inventory differs")
        inventory = {row["id"]: row for row in evidence["window_inventory"]}
        require(len(inventory) == snapshot["windows"], "Duplicate/missing extracted windows")
        sources = {}
        for name, digest in snapshot["source_file_sha256"].items():
            path = Path(name)
            require(not path.is_absolute() and ".." not in path.parts, "Source path escapes corpus")
            raw = (project_root / entry["corpus"] / path).read_bytes()
            require(sha(raw) == digest, "Pinned saved file changed")
            sources[name] = physical_lines(raw.decode("utf-8"))
        for query in queries.values():
            for alternatives in groups(query):
                for target in alternatives:
                    lo, hi = target["evidence_lines"]
                    raw = "".join(sources[target["path"]][lo - 1:hi]).encode()
                    require(sha(raw) == target["evidence_sha256"], "Executable gold bytes differ")
        selected = [row for row in rows if row["dataset"] == identifier]
        require({row["query_id"] for row in selected} == set(queries), "Query membership differs")
        require({(row["query_id"], row["condition"], row["repetition"]) for row in selected}
                == {(query_id, condition, repetition) for query_id in queries for condition in frozen["conditions"]
                    for repetition in range(1, frozen["repetitions"] + 1)}, "Condition/repetition matrix differs")
        for row in selected:
            query = queries[row["query_id"]]
            require(row["kind"] == query["kind"] and row["role"] == entry["role"] and row["language"] == entry["language"], "Query metadata changed")
            require(type(row["elapsed_http_s"]) in (float, int) and math.isfinite(row["elapsed_http_s"]) and row["elapsed_http_s"] >= 0, "Invalid latency receipt")
            response = row["response"]
            require(response["query"] == query["query"] and response["retrieval"] == row["condition"] and response["rerank"] is False, "Request/condition changed")
            require(response["scope"]["source_root"] == entry["source_root"], "Configured source root changed")
            require(response["source_budget"] == settings["source_budget"], "Applied source budget differs")
            require(response["candidate_windows"] == len(row["candidate_windows"]), "Recorded candidate count differs")
            require(response["match_status"] in ("candidates", "no_candidates"), "Abstention appeared although disabled")
            require(len(response["results"]) <= settings["max_results"] and len(row["candidate_windows"]) <= frozen["candidate_depth"], "Result/candidate cap exceeded")
            require(response["total_source_chars"] == sum(len(window["source"]) for window in response["results"]) <= settings["source_budget"], "Source cap/counter changed")
            usage = response["token_usage"]
            require(usage["encoding"] == "cl100k_base" and usage["budget"] == settings["token_budget"], "Token budget changed")
            require(type(usage["tokens"]) is int and usage["tokens"] == len(encoder.encode(compact_text(response), disallowed_special=())) <= settings["token_budget"], "Exact compact token proof failed")
            for window in row["candidate_windows"]:
                verify_window(window, inventory, sources, delivered=False)
            for window in response["results"]:
                verify_window(window, inventory, sources, delivered=True)
                saved_windows += 1
            gold = groups(query)
            require(row["multi_file_and"] == (len({target["path"] for alternatives in gold for target in alternatives}) > 1),
                    "Multi-file denominator differs from authored AND groups")
            require(row["evidence_ranks"] == rank_groups(response["results"], gold)
                    and row["candidate_evidence_ranks"] == rank_groups(row["candidate_windows"], gold), "Declared evidence ranks differ from saved bytes")
            require(row["exact_source_and_budgets_verified"] is True, "Runner guard flag failed")
            if row["repetition"] == 1 and query["kind"] == "present" and None in row["evidence_ranks"]:
                missed.append(miss_details(row, gold, inventory))
            if row["repetition"] == 1 and query["kind"] == "absent":
                absent.append({"dataset": identifier, "query_id": query["id"], "condition": row["condition"],
                               "match_status": response["match_status"], "returned_decoys": [
                                   {key: window[key] for key in ("path", "symbol", "start_line", "end_line", "truncated")}
                                   for window in response["results"]]})
        setup = next(row for row in setups if row["dataset"] == identifier)
        cold_index = setup["indexstats"]
        require(cold_index["indexed_windows"] == cold_index["total_windows"] == snapshot["windows"]
                and cold_index["reused_windows"] == 0 and cold_index["source_files"] == len(sources),
                "Cold index membership differs from frozen sources")
        cold, warm = setup["embedding_calls_after_cold_setup"], setup["embedding_calls_after_queries"]
        require(cold["completed_query_calls"] == 1 and warm["completed_query_calls"] == 1 + len(queries) * 2 * frozen["repetitions"], "Fresh query embedding counts changed")
        require(cold["completed_document_calls"] == warm["completed_document_calls"] and cold["document_texts"] == warm["document_texts"], "Query phase reembedded documents")
        require(all(row["response"]["indexstats"]["indexed_windows"] == 0 for row in selected), "Warm query phase reindexed windows")
        require(all(row["response"]["indexstats"]["total_windows"] == snapshot["windows"]
                    and row["response"]["indexstats"]["source_files"] == len(sources)
                    and row["response"]["indexstats"]["source_manifest"] == cold_index["source_manifest"]
                    for row in selected), "Warm index membership changed")
        reopen = setup["persistent_reopen"]
        require(reopen["indexstats"]["indexed_windows"] == 0 and reopen["indexstats"]["reused_windows"] == reopen["indexstats"]["total_windows"] == snapshot["windows"], "Reopen did not reuse every window")
        require(all(value == 0 for value in reopen["embedding_calls"].values()), "Reopen invoked embedding")
        require(all(reopen["indexstats"][key] == cold_index[key] for key in
                    ("source_files", "source_manifest", "model_fingerprint")), "Reopened index identity changed")
        condition_reports = {}
        for condition in frozen["conditions"]:
            subset = [row for row in selected if row["condition"] == condition]
            result = metrics(subset)
            require(result == summary["datasets"][identifier][condition], "Published summary differs from independent receipts")
            later_times = [row["elapsed_http_s"] for row in subset if row["repetition"] > 1]
            result["later_repetition_latency_samples"] = len(later_times)
            result["later_repetition_median_http_s"] = statistics.median(later_times) if later_times else None
            result["max_source_chars"] = max(row["response"]["total_source_chars"] for row in subset)
            condition_reports[condition] = result
        dataset_reports[identifier] = {"role": entry["role"], "language": entry["language"], "source_root": entry["source_root"],
                                      "source_files": len(sources), "windows": snapshot["windows"], "conditions": condition_reports, "setup": setup}
    unique = len({(row["dataset"], row["query_id"]) for row in rows})
    require(unique == summary["unique_queries"] == manifest["totals"]["queries"], "Quality query denominator changed")
    require(manifest["totals"] == {
        "queries": len(authored), "present_queries": sum(query["kind"] == "present" for query in authored),
        "absent_queries": sum(query["kind"] == "absent" for query in authored),
        "multi_file_and_queries": sum(len({target["path"] for alternatives in groups(query) for target in alternatives}) > 1
                                      for query in authored),
        "required_evidence_groups": sum(len(groups(query)) for query in authored),
        "gold_target_rows": sum(len(alternatives) for query in authored for alternatives in groups(query)),
    }, "Authored query/group denominators differ")
    missing_groups = [group for query in missed for group in query["missing_groups"]]
    gap_counts = {
        "missing_required_groups": len(missing_groups),
        "no_complete_candidate": sum(group["candidate_rank"] is None for group in missing_groups),
        "candidate_beyond_first8_raw_windows": sum(group["outside_first8_raw_candidate_windows"] for group in missing_groups),
        "candidate_within_first8_not_delivered": sum(group["candidate_rank"] is not None and group["candidate_rank"] <= 8
                                                    for group in missing_groups),
        "evidence_cut_within_returned_window": sum(bool(group["evidence_cut_within_returned_window"]) for group in missing_groups),
    }
    return {"classification": "Independent saved-receipt retrieval report, not coding correctness or provider usage",
            "checks_passed": True, "requests": len(rows), "unique_queries": unique, "quality_repetition": 1,
            "totals": manifest["totals"], "datasets": dataset_reports, "primary_misses": missed, "primary_absent_decoys": absent,
            "primary_missing_group_diagnostics": gap_counts,
            "frozen_method": {key: frozen[key] for key in ("embedding", "fusion", "dependencies", "extractor",
                                                            "candidate_measurement", "settings")},
            "source_proofs": {"verified_http_responses": len(rows), "verified_delivered_windows": saved_windows,
                              "method": "Exact pinned saved-file hashes, frozen candidate identity/bounds, physical CR/LF bytes and independently recomputed full-evidence ranks/token counts"},
            "artifact_sha256": {name: sha((study / name).read_bytes()) for name in
                                ("frozen-config.json", "searches.jsonl", "summary.json", "setups.json", "extractor-preflight.json", "runtime-audit.json")},
            "reporter_sha256": sha(Path(__file__).read_bytes()), "reviewer_model_calls": 0, "reviewer_http_calls": 0,
            "limits": ["Quality uses thirty source-authored queries, one first-repetition observation per condition; later repetitions do not expand that sample.",
                       "Zod holds out retrieval parameter selection; its syntax/eligibility were inspected and generic extractor repairs made before inference.",
                       "Express development/calibration assignment is fixed; no threshold or ranking fit was performed, H2O/abstention are off.",
                       "Candidate diagnostics reconstruct with the frozen current-vector path; receipts contain aggregate embedding counts, not per-query vectors.",
                       "Miss flags describe observed windows/bounds and packing, not reasons for model rankings or evidence of provider-token savings.",
                       "Memory is rounded process footprint; lifetime peaks include earlier datasets and must not be summed."]}


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render(report):
    lines = ["# Frozen JavaScript/TypeScript retrieval results", "",
             f"{report['requests']} requests cover {report['unique_queries']} unique queries: 24 present, 6 scoped absent. Quality uses repetition 1; later repetitions measure latency/stability, not additional query evidence.", "",
             "JavaScript development/calibration and TypeScript retrieval holdout are reported separately. Zod syntax and executable-evidence eligibility were inspected and the generic extractor adapted before inference; labels and ranking settings stayed fixed. H2O and abstention are off. These results do not establish coding correctness, absence guarantees or provider token savings.", "",
             "| Dataset / role | Condition | Candidate complete /12 | Delivered complete /12 | Candidate / delivered groups | Multi-file complete /4 | MRR | Median / p95 HTTP ms (45) | Truncated /15 | Max context tokens |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for identifier, dataset in report["datasets"].items():
        for condition, data in dataset["conditions"].items():
            lines.append(f"| {identifier} / {dataset['role']} | {condition} | {data['candidate_complete']} | {data['delivered_complete']} | {data['candidate_groups']} / {data['delivered_groups']} of {data['required_groups']} | {data['multi_file_complete']} | {data['complete_evidence_mrr']:.3f} | {1000*data['median_http_s']:.1f} / {1000*data['p95_http_s']:.1f} | {data['primary_truncated_queries']} | {data['max_compact_context_tokens']} |")
    lines += ["", "Complete query coverage requires every AND group; alternatives within a group are OR. The first complete rank is the largest required group rank, including a shared window that covers several groups. Candidate depth 50 and delivered limits of 8 excerpts, 12,000 source characters and 4,096 compact JSON tokens were fixed. Token counts include scope metadata and describe local payloads, not billing.", "",
              "Later repetitions provide 30 latency samples per dataset/condition in addition to 15 first-repetition samples. Candidate reconstruction and source verification occur outside HTTP timers. Repeated requests do not supply 90 unique queries.", ""]
    changed = sum(len(data["repeat_coverage_mismatches"]) for dataset in report["datasets"].values()
                  for data in dataset["conditions"].values())
    lines.append(f"Later repetitions had {changed} candidate/delivered evidence-rank coverage changes relative to the first repetition; the exact changed query IDs are retained in analysis.json.")
    gaps = report["primary_missing_group_diagnostics"]
    lines += ["", "## Every first-repetition miss", "",
              f"Across {gaps['missing_required_groups']} missing required groups, {gaps['no_complete_candidate']} had no complete candidate window, {gaps['candidate_beyond_first8_raw_windows']} had complete evidence beyond the first 8 raw candidates, and {gaps['candidate_within_first8_not_delivered']} had a complete candidate within the first 8 that was not delivered. Full evidence was cut inside an actually returned window for {gaps['evidence_cut_within_returned_window']} groups.", "",
              "Candidate ranks refer to raw windows before symbol deduplication/dependency expansion. A rank beyond 8 does not by itself establish why delivery failed. A cut flag means a returned original window contained the full evidence, but its delivered prefix did not.", "",
              "| Dataset / condition | Query | Candidate ranks | Delivered ranks | Recorded gap / budget flags |", "|---|---|---|---|---|"]
    for row in report["primary_misses"]:
        descriptions = [f"g{group['group']}: {group['observation']}" + ("; candidate rank>8" if group["outside_first8_raw_candidate_windows"] else "")
                        for group in row["missing_groups"]]
        flags = f"budget_exhausted={row['budget_exhausted']}, token_truncated={row['token_truncated']}"
        lines.append(f"| {row['dataset']} / {row['condition']} | `{row['query_id']}` | {row['candidate_ranks']} | {row['delivered_ranks']} | {cell('; '.join(descriptions))}; {flags} |")
    if not report["primary_misses"]:
        lines.append("No first-repetition complete-query misses.")
    lines += ["", "Full missing-group diagnostics in analysis.json also retain first-distinct-symbol ranks, original/delivered bounds for cuts, and actual dependency excerpt counts. These observations do not identify a causal model-ranking mechanism.", "", "## Scoped absent queries and returned decoys", "",
              "No abstention threshold was fitted. Returning candidates for an absent query is a recorded decoy outcome, not proof the requested behavior exists. Every first-repetition negative/condition stays listed.", "",
              "| Dataset / condition | Query | Status | Delivered decoys (path:symbol@lines) |", "|---|---|---|---|"]
    for row in report["primary_absent_decoys"]:
        decoys = "; ".join(f"`{item['path']}:{item['symbol']}@{item['start_line']}-{item['end_line']}`" for item in row["returned_decoys"])
        lines.append(f"| {row['dataset']} / {row['condition']} | `{row['query_id']}` | {row['match_status']} | {cell(decoys) or 'none'} |")
    lines += ["", "## Source, reuse and memory evidence", "",
              f"All {report['source_proofs']['verified_http_responses']} saved HTTP responses independently pass exact source, physical-line, evidence-rank and compact-token checks. The contemporaneous runtime-audit.json was recorded before subsequent authorized code changes; this report reads that historical proof without loading weights or rerunning inference.", "",
              "| Dataset | Files / windows | Cold index+model warm s | Document calls / texts | Query calls incl 1 warmup | Reopen reused / total; calls | Physical footprint setup / after queries; lifetime peak |", "|---|---:|---:|---:|---:|---|---|"]
    for identifier, data in report["datasets"].items():
        setup = data["setup"]
        calls = setup["embedding_calls_after_queries"]
        reopen = setup["persistent_reopen"]
        memory1, memory2 = setup["memory_after_cold_setup"], setup["memory_after_queries"]
        memory = f"{memory1.get('physical_footprint','unavailable')} / {memory2.get('physical_footprint','unavailable')}; {memory2.get('physical_footprint_peak','unavailable')}"
        lines.append(f"| {identifier} | {data['source_files']} / {data['windows']} | {setup['cold_index_and_model_warm_s']:.2f} | {calls['completed_document_calls']} / {calls['document_texts']} | {calls['completed_query_calls']} | {reopen['indexstats']['reused_windows']} / {reopen['indexstats']['total_windows']}; 0 query/0 document | {memory} |")
    lines += ["", "The unchanged query phase performed no document embedding/index refresh work; fresh reopened backends reused every window with zero embedding calls. Document-input counts and indexed-window counts are separate quantities. vmmap measurements are rounded physical footprints outside HTTP timers; process-lifetime peaks include earlier datasets and cannot be summed or treated as isolated model VRAM.", "",
              "The authored sample is small. The repository/language holdout is not a claim about model pretraining. Saved edits, unsaved overlays, real editor workflows and later coding correctness require their own evidence. No parameter change or threshold is selected from these outcomes.", "",
              "The complete embedding revision, extractor policy, fusion and dependency parameters remain in frozen-config.json and analysis.json. Reproduce reporting with `scripts/report_language_quality.py --study <saved-study>` and the pinned corpus bytes. No evaluation HTTP request or model call is made."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=ROOT / "results/language-quality-2026-10-13")
    args = parser.parse_args()
    result = build_report(args.study.resolve())
    (args.study / "analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    (args.study / "findings.md").write_text(render(result))
    print(json.dumps({"checks_passed": True, "requests": result["requests"], "unique_queries": result["unique_queries"],
                      "first_repetition_misses": len(result["primary_misses"]), "negative_condition_rows": len(result["primary_absent_decoys"])}, indent=2))


if __name__ == "__main__":
    main()
