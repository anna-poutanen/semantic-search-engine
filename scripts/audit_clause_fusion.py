#!/usr/bin/env python3
"""Independently reconstruct and score the frozen development clause probe."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from fractions import Fraction
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ("full_query_dense", "clause_rrf")
SETTINGS = {"rerank": False, "candidates": 50, "max_results": 8, "source_budget": 12000,
            "token_budget": 4096, "include_dependencies": True}
SPEC = importlib.util.spec_from_file_location("independent_saved_evidence", ROOT / "scripts/report_context_packing.py")
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)
HASH = re.compile(r"[0-9a-f]{64}\Z")
COUNTERS = ("attempted_document_calls", "completed_document_calls", "document_texts",
            "attempted_query_calls", "completed_query_calls")


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def sha(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def jsonl(path):
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


def split_inputs(query):
    require(isinstance(query, str) and query.strip(), "Nonempty literal query")
    query.encode("utf-8")
    result = [query]
    def normalize(value):
        return " ".join(value.split()).casefold()
    seen = {normalize(query)}
    for part in re.split(r"\b(?:and\s+where|and\s+then|while|then|before)\b", query, flags=re.IGNORECASE):
        text = " ".join(part.split())
        if len(text.split()) >= 4 and normalize(text) not in seen:
            result.append(text)
            seen.add(normalize(text))
        if len(result) == 4:
            break
    return result


def reconstruct_fusion(rankings):
    require(isinstance(rankings, list) and 1 <= len(rankings) <= 4, "Fusion list count")
    scores, first, full_positions = {}, {}, {}
    for number, rows in enumerate(rankings):
        require(isinstance(rows, list) and len(rows) <= 50
                and len({row["id"] for row in rows}) == len(rows), "Unique depth-fifty ranks")
        for offset, row in enumerate(rows):
            identifier = row["id"]
            require(isinstance(identifier, str) and identifier, "Ranked identity")
            first.setdefault(identifier, row)
            scores[identifier] = scores.get(identifier, Fraction(0)) + Fraction(1, 61 + offset)
            if number == 0:
                full_positions[identifier] = offset + 1
    order = sorted(scores, key=lambda key: (-scores[key], full_positions.get(key, math.inf), key))[:50]
    return [{**first[key], "fusion_priority": float(scores[key]), "fusion_exact": str(scores[key]),
             "full_query_rank": full_positions.get(key)} for key in order]


def verify_counters(before, after, inputs):
    require(all(type(snapshot[key]) is int and snapshot[key] >= 0
                for snapshot in (before, after) for key in COUNTERS), "Strict nonnegative observed counters")
    for key in COUNTERS[:3]:
        require(after[key] == before[key], "Query phase embedded documents")
    for key in COUNTERS[3:]:
        require(after[key] - before[key] == len(inputs), "Actual attempted/completed query call delta")
    require(before["attempted_query_calls"] == before["completed_query_calls"] == len(before["observed_query_inputs"])
            and after["attempted_query_calls"] == after["completed_query_calls"] == len(after["observed_query_inputs"])
            and after["observed_query_inputs"] == before["observed_query_inputs"] + inputs,
            "Immutable actual model-input prefix/delta")


def verify_raw(window, inventory, sources):
    require(window["id"] in inventory, "Candidate ID outside frozen inventory")
    full = inventory[window["id"]]
    require(all(window[key] == full[key] for key in ("path", "symbol", "start_line", "end_line", "source_hash")),
            "Candidate metadata differs from frozen window")
    lo, hi = window["start_line"], window["end_line"]
    require(type(lo) is type(hi) is int and 1 <= lo <= hi <= len(sources[window["path"]]), "Raw physical bounds")
    source = "".join(sources[window["path"]][lo - 1:hi])
    require(window["source"] == source and window["source_hash"] == sha(source.encode()), "Raw saved source/hash")
    value = window["retrieval_score"]
    require(type(value) in (int, float) and math.isfinite(value) and -1.00001 <= value <= 1.00001,
            "Finite raw cosine score")


def verify_attempt(row, query, condition, inventory, sources, encoding):
    require(row["status"] == "completed" and "error" not in row and row["query"] == query,
            "Completed literal condition")
    inputs = [query] if condition == CONDITIONS[0] else split_inputs(query)
    require(row["query_inputs"] == inputs, "Independent literal clause split differs")
    verify_counters(row["counts_before"], row["counts_after"], inputs)
    vectors = row["query_vectors"]
    require(len(vectors) == len(inputs), "Observed vector count")
    for vector, text in zip(vectors, inputs, strict=True):
        import numpy as np
        require(isinstance(vector["values"], list) and len(vector["values"]) == 1024
                and all(type(value) in (int, float) for value in vector["values"]), "Retained numeric query vector")
        values = np.asarray(vector["values"], dtype=np.float32)
        require(np.isfinite(values).all() and sha(values.tobytes()) == vector["sha256"]
                and math.isclose(float(np.linalg.norm(values)), vector["l2_norm"], rel_tol=1e-7),
                "Independent complete vector byte SHA/norm")
        require(vector["input"] == text and vector["shape"] == [1024] and vector["dtype"] == "float32"
                and isinstance(vector["sha256"], str) and bool(HASH.fullmatch(vector["sha256"]))
                and type(vector["l2_norm"]) in (int, float) and math.isfinite(vector["l2_norm"])
                and abs(vector["l2_norm"] - 1) < .0001, "Observed normalized query-vector metadata")
    raw = row["raw_ranked_source_lists"]
    require(len(raw) == len(inputs), "Raw input/list count")
    rankings = []
    for item, text in zip(raw, inputs, strict=True):
        windows = item["windows"]
        require(item["input"] == text and item["score_kind"] == "cosine"
                and len(windows) == min(50, len(inventory))
                and len({window["id"] for window in windows}) == len(windows), "Actual depth-fifty raw list")
        for window in windows:
            verify_raw(window, inventory, sources)
        require([(-window["retrieval_score"], window["id"]) for window in windows]
                == sorted((-window["retrieval_score"], window["id"]) for window in windows),
                "Descending cosine/stable-ID raw order")
        rankings.append(windows)
    merged = rankings[0] if condition == CONDITIONS[0] else reconstruct_fusion(rankings)
    require(row["merged_candidates"] == merged, "Exact independent RRF/rational/full-rank/ID ordering")
    score_kind = "cosine" if condition == CONDITIONS[0] else "equal_weight_rrf_priority_k60"
    require(row["score_kind"] == score_kind and row["source_and_shared_caps_verified"] is True, "Declared score/cap provenance")
    candidates = [{**window, "retrieval_score": window["retrieval_score"] if condition == CONDITIONS[0]
                   else window["fusion_priority"]} for window in merged]
    response = row["response"]
    require(response["query"] == query and response["retrieval"] == "dense" and response["rerank"] is False
            and response["source_budget"] == 12000 and response["candidate_windows"] == len(merged)
            and response["match_status"] == "candidates", "Response query/mode/candidate identity")
    top = rankings[0][0]["retrieval_score"] if rankings[0] else None
    require(response["match_evidence"]["top_cosine"] == top
            and response["match_evidence"]["threshold"] is None
            and response["match_evidence"]["ranking_score_kind"] == score_kind, "Raw full cosine remains separate from priority")
    count = evidence.verify_response(response, response, inventory, sources, encoding, candidates)
    phases = ("query_embeddings_s", "candidate_retrieval_s", "fusion_s", "packing_s", "elapsed_inprocess_s")
    require(all(type(row[key]) in (int, float) and math.isfinite(row[key]) and row[key] >= 0 for key in phases)
            and row["elapsed_inprocess_s"] + .000001 >= sum(row[key] for key in phases[:-1]), "Finite ordered in-process phase intervals")
    return merged, count, len(inputs)


def tokenizer(root, packages):
    import hashlib
    url = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
    expected = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
    raw = (root / ".cache/tokenizers" / hashlib.sha1(url.encode()).hexdigest()).read_bytes()
    require(sha(raw) == expected and importlib.metadata.version("tiktoken") == packages["tiktoken"], "Pinned offline tokenizer")
    import tiktoken
    import tiktoken.load

    def captured(blobpath, expected_hash=None):
        require(blobpath == url and expected_hash == expected, "Unexpected tokenizer data request; no network allowed")
        return raw

    with patch.object(tiktoken.load, "read_file_cached", captured):
        return tiktoken.get_encoding("cl100k_base")


def verify_matrix(rows, schedule):
    require(len(rows) == len(schedule) == 60 and [row["identity"] for row in rows] == schedule
            and len({tuple(row["identity"][key] for key in ("dataset", "query_id", "condition")) for row in rows}) == 60,
            "All sixty unique scheduled attempts in exact order")


def verify_run_receipt(plan, receipt, plan_sha):
    require(receipt["frozen_plan_sha256"] == plan_sha and receipt["source_sha256"] == plan["source_sha256"]
            and receipt["semantic_module_origins"] == plan["semantic_module_origins"]
            and receipt["offline_tokenizer"] == plan["offline_tokenizer"], "Explicit run/frozen source and runtime receipt")
    for origin in plan["semantic_module_origins"].values():
        require(origin["project_relative_path"] in plan["source_sha256"]
                and origin["sha256"] == plan["source_sha256"][origin["project_relative_path"]], "Actual imported module/source binding")
    expected_weights = {name: {"sha256": item["expected_sha256_from_provenance"], "bytes": item["bytes"],
                              "matches_pinned_provenance": True} for name, item in plan["checkpoint"].items()
                        if name.endswith(".safetensors")}
    require(expected_weights and receipt["weights"] == expected_weights, "Retained full weight-byte verification receipt")
    tokenizer = plan["offline_tokenizer"]
    require(tokenizer["sha256"] == "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
            and tokenizer["tiktoken"] == plan["packages"]["tiktoken"]
            and tokenizer["network"] is tokenizer["cache_writes"] is False, "Frozen offline tokenizer provenance")


def paired_changes(cases):
    grouped = {}
    for case in cases:
        grouped.setdefault((case["dataset"], case["query_id"]), {})[case["condition"]] = case
    changes = {key: [] for key in ("complete_gains", "complete_regressions", "and_gains", "and_regressions",
                                 "group_changes", "rank_changes", "candidate_complete_gains", "candidate_complete_regressions")}
    for (dataset, identifier), pair in grouped.items():
        require(set(pair) == set(CONDITIONS), "Both conditions required for each pair")
        a, b = (pair[name] for name in CONDITIONS)
        if a["kind"] == "absent":
            continue
        item = {"dataset": dataset, "query_id": identifier, "baseline_ranks": a["ranks"], "fusion_ranks": b["ranks"],
                "baseline_candidate_ranks": a["candidate_ranks"], "fusion_candidate_ranks": b["candidate_ranks"]}
        complete_a, complete_b = None not in a["ranks"], None not in b["ranks"]
        if not complete_a and complete_b:
            changes["complete_gains"].append(item)
            if a["multi_file"]:
                changes["and_gains"].append(item)
        if complete_a and not complete_b:
            changes["complete_regressions"].append(item)
            if a["multi_file"]:
                changes["and_regressions"].append(item)
        if [rank is not None for rank in a["ranks"]] != [rank is not None for rank in b["ranks"]]:
            changes["group_changes"].append(item)
        if a["ranks"] != b["ranks"]:
            changes["rank_changes"].append(item)
        candidate_a, candidate_b = None not in a["candidate_ranks"], None not in b["candidate_ranks"]
        if not candidate_a and candidate_b:
            changes["candidate_complete_gains"].append(item)
        if candidate_a and not candidate_b:
            changes["candidate_complete_regressions"].append(item)
    return changes


def summarize_cases(cases):
    positive = [case for case in cases if case["kind"] == "present"]
    absent = [case for case in cases if case["kind"] == "absent"]
    multi = [case for case in positive if case["multi_file"]]
    times = sorted(case["elapsed_inprocess_s"] for case in cases)
    return {"queries": len(cases), "present": len(positive), "absent": len(absent),
            "candidate_complete": sum(None not in case["candidate_ranks"] for case in positive),
            "delivered_complete": sum(None not in case["ranks"] for case in positive),
            "required_groups": sum(len(case["ranks"]) for case in positive),
            "candidate_groups": sum(sum(rank is not None for rank in case["candidate_ranks"]) for case in positive),
            "delivered_groups": sum(sum(rank is not None for rank in case["ranks"]) for case in positive),
            "and_queries": len(multi), "and_complete": sum(None not in case["ranks"] for case in multi),
            "absent_with_decoys": sum(case["result_count"] > 0 for case in absent),
            "absent_statuses": dict(Counter(case["match_status"] for case in absent)),
            "observed_query_api_calls": sum(case["query_api_calls"] for case in cases),
            "inprocess_samples": len(times), "median_inprocess_s": statistics.median(times),
            "p95_inprocess_s": times[math.ceil(.95 * len(times)) - 1], "total_inprocess_s": sum(times),
            "max_compact_tokens": max(case["tokens"] for case in cases)}


def build_audit(root, study):
    plan = read(study / "frozen-plan.json")
    preflight, summary, setups = (read(study / name) for name in ("CPU-preflight.json", "summary.json", "setups.json"))
    attempts = jsonl(study / "attempts.jsonl")
    verification, run_start = (read(study / name) for name in ("run-verification.json", "run-start.json"))
    verify_run_receipt(plan, verification, sha((study / "frozen-plan.json").read_bytes()))
    require(datetime.fromisoformat(plan["frozen_at_utc"]) <= datetime.fromisoformat(run_start["started_at_utc"])
            <= datetime.fromisoformat(verification["observed_at_utc"]), "Freeze/start/weight-verification observation order")
    require(plan["conditions"] == list(CONDITIONS) and plan["settings"] == SETTINGS
            and all(type(plan["settings"][key]) is type(value) for key, value in SETTINGS.items())
            and plan["planned_attempts"] == 60 and plan["unique_queries"] == 30
            and type(plan["model_calls_in_freeze"]) is int and plan["model_calls_in_freeze"] == 0
            and plan["weights_loaded_in_freeze"] is False, "Frozen settings/CPU-only plan")
    rule = plan["rule"]
    require(rule["separators"] == ["and where", "and then", "while", "then", "before"]
            and rule["case_insensitive"] is rule["word_boundaries"] is True
            and rule["minimum_clause_whitespace_words"] == 4 and rule["maximum_additional_clauses"] == 3
            and rule["rrf_k"] == 60 and rule["weights"] == "equal" and rule["per_list_depth"] == rule["merged_depth"] == 50
            and rule["ties"] == "original full-query rank then stable snippet ID"
            and rule["threshold"] is None and rule["h2o"] is False, "Frozen generic clause/RRF rule")
    require(summary["attempts_sha256"] == sha((study / "attempts.jsonl").read_bytes())
            and summary["complete"] is True and summary["attempted"] == summary["completed"] == 60
            and summary["failed"] == [] and not (study / "failure.json").exists(), "Complete preserved attempt ledger")
    verify_matrix(attempts, plan["schedule"])
    for name, expected in plan["source_sha256"].items():
        require(sha(evidence.located(root, name).read_bytes()) == expected, "Frozen source differs: " + name)
        original = subprocess.check_output(["git", "-C", str(root), "show", plan["source_commit"] + ":" + name], timeout=10)
        require(sha(original) == expected, "Frozen committed source differs: " + name)
    package_inventory = dict(sorted((distribution.metadata["Name"], distribution.version)
                                   for distribution in importlib.metadata.distributions()))
    require(package_inventory == plan["packages"], "Installed package inventory changed")
    model = plan["embedding"]
    require(model["id"] == "Qwen/Qwen3-Embedding-0.6B" and model["revision"] == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
            and model["quantized"] is False and model["weights_dtype"] == "bfloat16" and model["dimensions"] == 1024,
            "Pinned original Qwen checkpoint metadata")
    checkpoint = root / ".cache/models/qwen3-embedding-0.6b"
    require({path.name for path in checkpoint.iterdir() if path.is_file()} == set(plan["checkpoint"]), "Checkpoint file membership")
    for name, item in plan["checkpoint"].items():
        path = checkpoint / name
        require(path.stat().st_size == item["bytes"], "Checkpoint byte size: " + name)
        if name.endswith(".safetensors"):
            require(path.stat().st_mtime_ns == item["mtime_ns"], "Frozen weight-file mtime")
        else:
            require(sha(path.read_bytes()) == item["sha256"], "Checkpoint metadata file hash: " + name)
    provenance = read(checkpoint / "semantic_search_checkpoint.json")
    require(provenance["id"] == model["id"] and provenance["revision"] == model["revision"]
            and all(provenance["weights_etag"] == item["sha256"] for item in verification["weights"].values()), "Pinned checkpoint download provenance")
    manifest_path = root / "benchmarks/language_quality_manifest.json"
    require(sha(manifest_path.read_bytes()) == plan["manifest_sha256"] == preflight["manifest_sha256"], "Manifest provenance")
    manifest = read(manifest_path)
    require(preflight["checks_passed"] is True and preflight["model_calls"] == 0
            and preflight["extractor"] == plan["extractor"], "CPU parser/extractor provenance")
    catalogs, sources, inventories, labels, hashes = [], {}, {}, {}, {}
    files_checked = windows_checked = 0
    unique_documents = {}
    for entry in manifest["datasets"]:
        name = entry["id"]
        dataset_path = evidence.located(root, entry["dataset"])
        require(sha(dataset_path.read_bytes()) == entry["sha256"] == plan["dataset_sha256"][entry["dataset"]], "Frozen gold labels")
        dataset = read(dataset_path)
        corpus = evidence.located(root, entry["corpus"])
        require(dataset["revision"] == entry["revision"] == subprocess.check_output(["git", "-C", str(corpus), "rev-parse", "HEAD"], text=True).strip()
                and not subprocess.check_output(["git", "-C", str(corpus), "status", "--porcelain"], text=True).strip(), "Pinned clean saved corpus")
        frozen_source = next(item for item in plan["sources"] if item["id"] == name)
        cpu_source = next(item for item in preflight["datasets"] if item["id"] == name)
        require({key: cpu_source[key] for key in frozen_source} == frozen_source, "CPU/source plan inventory binding")
        sources[name], hashes[name] = {}, frozen_source["source_file_sha256"]
        for path, expected in hashes[name].items():
            raw = evidence.located(corpus, path).read_bytes()
            require(sha(raw) == expected, "Pinned saved source file hash")
            sources[name][path] = evidence.physical_lines(raw.decode("utf-8"))
            files_checked += 1
        windows = cpu_source["window_inventory"]
        require(sha(json.dumps(windows, sort_keys=True).encode()) == frozen_source["window_inventory_sha256"]
                and len(windows) == len({window["id"] for window in windows}) == frozen_source["windows"], "Frozen extracted window inventory")
        inventories[name] = {window["id"]: window for window in windows}
        documents = set()
        for window in windows:
            source = "".join(sources[name][window["path"]][window["start_line"] - 1:window["end_line"]])
            verify_raw({**window, "source": source, "retrieval_score": 0}, inventories[name], sources[name])
            documents.add(sha((f"Path: {window['path']}\nSymbol: {window['symbol']}\nSource:\n" + source).encode()))
            windows_checked += 1
        unique_documents[name] = len(documents)
        labels[name] = {query["id"]: query for query in dataset["queries"]}
        require(len(labels[name]) == entry["queries"] == 15, "Gold query uniqueness/count")
        grouped_gold = [evidence.gold_groups(query, sources[name], hashes[name]) for query in dataset["queries"]]
        require(sum(query["kind"] == "present" for query in dataset["queries"]) == entry["present_queries"] == 12
                and sum(query["kind"] == "absent" for query in dataset["queries"]) == entry["absent_queries"] == 3
                and sum(len(groups) > 1 for groups in grouped_gold) == entry["multi_file_and_queries"] == 4
                and sum(len(groups) for groups in grouped_gold) == entry["required_evidence_groups"] == 19
                and sum(len(group) for groups in grouped_gold for group in groups) == entry["gold_target_rows"], "Full frozen gold denominators")
        for query in dataset["queries"]:
            evidence.gold_groups(query, sources[name], hashes[name])
            catalogs.append({"dataset": name, "query_id": query["id"], "query": query["query"],
                             "control_inputs": split_inputs(query["query"])})
    require(plan["queries"] == catalogs and len(catalogs) == 30, "All thirty literal query/independent split bindings")
    expected_schedule = []
    for number, query in enumerate(catalogs):
        for condition in (CONDITIONS if number % 2 == 0 else CONDITIONS[::-1]):
            expected_schedule.append({"dataset": query["dataset"], "query_id": query["query_id"], "condition": condition})
    require(plan["schedule"] == expected_schedule and plan["planned_query_embedding_calls"]
            == {CONDITIONS[0]: 30, CONDITIONS[1]: sum(len(query["control_inputs"]) for query in catalogs)}, "Frozen balanced condition schedule/call plan")
    encoding = tokenizer(root, plan["packages"])
    cases, delivered_rows, raw_rows, running_counts = [], 0, 0, {}
    setup_by_dataset = {setup["dataset"]: setup for setup in setups}
    require(len(setups) == len(setup_by_dataset) == 2, "Two separate cold setups")
    for name, setup in setup_by_dataset.items():
        before, after = setup["counts_before_setup"], setup["counts_after_setup"]
        require(all(before[key] == 0 and type(before[key]) is int for key in COUNTERS)
                and before["observed_query_inputs"] == [] and after["attempted_query_calls"] == after["completed_query_calls"] == 0
                and after["observed_query_inputs"] == [] and after["completed_document_calls"] == after["attempted_document_calls"] == 1
                and after["document_texts"] == unique_documents[name], "Cold unique document API setup counters")
        require(setup["indexstats"]["total_windows"] == setup["indexstats"]["indexed_windows"] == len(inventories[name])
                and setup["indexstats"]["reused_windows"] == 0
                and setup["indexstats"]["model_fingerprint"] == sha(json.dumps(model, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()),
                "Fresh durable document snapshot/model fingerprint setup")
        database = root / ".cache/indexes" / f"{study.name}-{name}-clause-development.sqlite3"
        require(sha(database.read_bytes()) == setup["closed_database_sha256"], "Closed local index byte hash")
        running_counts[name] = after
    for row in attempts:
        identity = row["identity"]
        name, identifier, condition = (identity[key] for key in ("dataset", "query_id", "condition"))
        query = labels[name][identifier]
        require(row["counts_before"] == running_counts[name], "Adjacent immutable counter snapshots")
        merged, count, calls = verify_attempt(row, query["query"], condition, inventories[name], sources[name], encoding)
        entry = next(entry for entry in manifest["datasets"] if entry["id"] == name)
        require(row["response"]["repo"] == "<PROJECT>/" + entry["corpus"]
                and row["response"]["source_root"] == entry["source_root"]
                and row["response"]["scope"]["source_root"] == entry["source_root"], "Saved repository/source scope")
        delivered_rows += count
        raw_rows += sum(len(item["windows"]) for item in row["raw_ranked_source_lists"])
        running_counts[name] = row["counts_after"]
        groups = evidence.gold_groups(query, sources[name], hashes[name])
        ranks, candidate_ranks = evidence.evidence_ranks(row["response"]["results"], groups), evidence.evidence_ranks(merged, groups)
        require(all(value is None or type(value) is int for value in [*row["evidence_ranks"], *row["candidate_evidence_ranks"]])
                and row["evidence_ranks"] == ranks and row["candidate_evidence_ranks"] == candidate_ranks
                and row["kind"] == query["kind"] and row["multi_file_and"] is (len(groups) > 1), "Independent full-span OR/AND gold ranks")
        cases.append({**identity, "kind": query["kind"], "multi_file": len(groups) > 1, "ranks": ranks,
                      "candidate_ranks": candidate_ranks, "tokens": row["response"]["token_usage"]["tokens"],
                      "result_count": len(row["response"]["results"]), "match_status": row["response"]["match_status"],
                      "elapsed_inprocess_s": row["elapsed_inprocess_s"], "query_api_calls": calls})
    for name, setup in setup_by_dataset.items():
        require(setup["counts_after_queries"] == setup["counts_at_close"] == running_counts[name], "Final query/close counter snapshots")
        closed = setup["after_close"]
        require(closed["backend_status"]["closed"] is True and closed["weights_resident"] is False
                and closed["metal_runtime_resident"] is False and closed["backend_close_returned"] is True,
                "Observed backend/model/runtime close")
    metrics = {name: {condition: summarize_cases([case for case in cases if case["dataset"] == name
                        and case["condition"] == condition]) for condition in CONDITIONS} for name in labels}
    for name, conditions in metrics.items():
        for condition, actual in conditions.items():
            published = summary["datasets"][name][condition]
            mapped = {"completed_queries": "queries", "present_queries": "present", "absent_queries": "absent",
                      "candidate_complete": "candidate_complete", "delivered_complete": "delivered_complete",
                      "required_groups": "required_groups", "delivered_groups": "delivered_groups",
                      "multi_file_queries": "and_queries", "multi_file_complete": "and_complete",
                      "absent_with_decoys": "absent_with_decoys", "observed_query_calls": "observed_query_api_calls"}
            require(all(published[key] == actual[value] for key, value in mapped.items())
                    and math.isclose(published["elapsed_inprocess_s"], actual["total_inprocess_s"], rel_tol=1e-12), "Independent summary totals")
    by_identity = {(case["dataset"], case["query_id"], case["condition"]): case for case in cases}
    expected_pairs = []
    for query in catalogs:
        a, b = (by_identity[(query["dataset"], query["query_id"], condition)] for condition in CONDITIONS)
        expected_pairs.append({"dataset": query["dataset"], "query_id": query["query_id"], "kind": a["kind"],
                               "baseline_evidence_ranks": a["ranks"], "fusion_evidence_ranks": b["ranks"],
                               "baseline_candidate_ranks": a["candidate_ranks"], "fusion_candidate_ranks": b["candidate_ranks"],
                               "baseline_s": a["elapsed_inprocess_s"], "fusion_s": b["elapsed_inprocess_s"]})
    require(summary["paired_changes"] == expected_pairs, "All producer paired changes independently recomputed")
    return {"classification": "Independent post-selection audit of already-observed Express/Zod development query fusion",
            "checks_passed": True, "attempts_checked": 60, "unique_queries": 30, "raw_ranked_windows_checked": raw_rows,
            "delivered_excerpts_checked": delivered_rows, "saved_files_checked": files_checked,
            "frozen_windows_checked": windows_checked, "metrics": metrics, "paired_changes": paired_changes(cases),
            "cases": cases, "cold_setup_seconds": {name: setup["cold_index_s"] for name, setup in setup_by_dataset.items()},
            "artifacts_sha256": {path.name: sha(path.read_bytes()) for path in (study / "frozen-plan.json", study / "CPU-preflight.json",
                                 study / "attempts.jsonl", study / "setups.json", study / "summary.json",
                                 study / "run-start.json", study / "run-verification.json")},
            "auditor_sha256": sha(Path(__file__).read_bytes()), "source_evidence_helper_sha256": sha(Path(evidence.__file__).read_bytes()),
            "limits": [
                "Both repositories and all queries were already observed; this one-pass development probe is not a new holdout, generalization test, coding-correctness test or justification for changing defaults.",
                "All OR alternatives and required AND groups are rescored from complete executable spans in one exact saved-source excerpt per group. Plausible absent-query decoys receive no positive gold credit.",
                "Independent RRF and exact compact payload checks use retained ranks and source bytes. Retained complete query vectors reproduce their byte SHA/norm; the auditor does not rerun semantic embedding or dense retrieval.",
                "Observed attempted/completed embedding API calls and actual input logs are checked, not individual internal MLX batch forwards or provider usage.",
                "Condition intervals are in-process embedding/retrieval/fusion/packing observations; prepared-vector backend timings are not model latency. Cold index setup is separate; memory is process-lifetime and descriptive.",
                "Source files, package metadata, checkpoint metadata and closed index byte hashes are checked. Retained complete weight-file SHA/size verification matches pinned provenance; the auditor does not reread or load weights.",
                "No model, retrieval, HTTP, hosted service or native app is executed by this auditor. The prior independent source-only scoring helper is reused; no producer or production ranking helper is imported.",
            ]}


def render(result):
    lines = ["# Independent clause-fusion development audit", "", "Both Express and Zod were already observed. This is one frozen pass over 30 authored development queries, two conditions each; it is not a new holdout or a default change.", "",
             f"All {result['attempts_checked']} conditions, {result['raw_ranked_windows_checked']} raw ranked windows and {result['delivered_excerpts_checked']} delivered excerpts pass independent reconstruction/source/budget checks. The original and fused conditions use fresh query embeddings; document-vector setup is shared per repository and reported separately.", "",
             "| Repository / condition | Candidate complete | Delivered complete | AND complete | Delivered groups | Absent with decoys | Query API calls | Median / P95 in-process seconds |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, conditions in result["metrics"].items():
        for condition, row in conditions.items():
            lines.append(f"| {name} / {condition} | {row['candidate_complete']}/{row['present']} | {row['delivered_complete']}/{row['present']} | {row['and_complete']}/{row['and_queries']} | {row['delivered_groups']}/{row['required_groups']} | {row['absent_with_decoys']}/{row['absent']} | {row['observed_query_api_calls']} | {row['median_inprocess_s']:.3f} / {row['p95_inprocess_s']:.3f} |")
    changes = result["paired_changes"]
    lines += ["", f"Across all 24 positive pairs: {len(changes['complete_gains'])} complete gains and {len(changes['complete_regressions'])} complete regressions; among eight AND queries, {len(changes['and_gains'])} gains and {len(changes['and_regressions'])} regressions. Absent queries remain separate and plausible candidates do not prove behavior or absence.", "",
              "Raw full-query cosine remains separate from RRF priority. Every delivered payload retains the shared eight-result, 12,000-character and exact 4,096-token caps. Extra query API calls and in-process intervals are descriptive costs; they are not provider billing or HTTP latency. All paired changes, missed groups and denominators are in the JSON.", "",
              "Limitations:", "", *["- " + limit for limit in result["limits"]], ""]
    return "\n".join(lines)


def incomplete_audit(study, error):
    plan = read(study / "frozen-plan.json")
    attempted = jsonl(study / "attempts.jsonl") if (study / "attempts.jsonl").exists() else []
    observed = {tuple(row["identity"].get(key) for key in ("dataset", "query_id", "condition")) for row in attempted}
    pending = [identity for identity in plan["schedule"] if tuple(identity.get(key) for key in
               ("dataset", "query_id", "condition")) not in observed]
    return {"classification": "Incomplete or inconsistent retained development probe; audit refused quality claims",
            "checks_passed": False, "error_type": type(error).__name__, "error": str(error),
            "planned_conditions": len(plan["schedule"]), "retained_attempts": len(attempted),
            "retained_status_counts": dict(Counter(row.get("status", "missing") for row in attempted)),
            "pending_identities": pending, "failed_identities": [row["identity"] for row in attempted if row.get("status") != "completed"],
            "limits": ["No retries or completed-only selection. Failed/pending conditions receive no invented miss, success, timing or model-call value.",
                       "Retained status counts are diagnostic until provenance/source/counter checks pass; no quality aggregate is issued on an incomplete or inconsistent ledger."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--study", type=Path, default=ROOT / "results/clause-fusion-development-2026-10-13")
    args = parser.parse_args(argv)
    try:
        result = build_audit(args.root.resolve(), args.study.resolve())
    except (KeyError, ValueError, TypeError, OSError, subprocess.SubprocessError) as error:
        result = incomplete_audit(args.study.resolve(), error)
    (args.study / "independent-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    findings = render(result) if result["checks_passed"] else "# Independent audit did not pass\n\n" + result["error"] + "\n\n" + "\n".join(result["limits"]) + "\n"
    (args.study / "independent-findings.md").write_text(findings)
    print(json.dumps({key: result[key] for key in ("checks_passed", "attempts_checked", "raw_ranked_windows_checked", "delivered_excerpts_checked") if key in result}))
    return 0 if result["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
