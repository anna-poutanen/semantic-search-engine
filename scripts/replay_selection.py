#!/usr/bin/env python3
"""Frozen, offline CPU development replay of response selection on observed orders."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import importlib
import importlib.abc
import importlib.metadata
import json
import math
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096,
            "include_dependencies": True, "rerank": False, "candidates": 50}
CONDITIONS = {"legacy": "ranked_context", "definition": "definition_context",
              "rank_preserving": "rank_preserving_context",
              "combined": "definition_rank_preserving_context",
              "legacy_coverage_backfill": "ranked_context",
              "definition_coverage_backfill": "definition_context"}
EXPECTED_ROWS = {"language_observed": 90, "native_express_observed": 30,
                 "native_zod_observed": 30, "native_jinja_observed": 28,
                 "qwen_development_observed": 144, "qwen_httpx_observed": 15}
MODULES = ("__init__", "source", "context", "corpus", "languages", "javascript",
           "relations", "javascript_relations", "selection", "context_backfill", "backend")
CACHE_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
CACHE_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
CACHE_KEY = hashlib.sha1(CACHE_URL.encode()).hexdigest()
PACKAGES = {"tiktoken": "0.14.0", "tree-sitter": "0.26.0",
            "tree-sitter-javascript": "0.25.0", "tree-sitter-typescript": "0.23.2"}
BLOCKED_IMPORTS = ("mlx", "mlx_embeddings", "mlx_lm", "mlx_vlm", "torch", "transformers", "jax", "numpy",
                   "semantic_search.backend", "semantic_search.embeddings", "semantic_search.model",
                   "semantic_search.index", "semantic_search.store", "semantic_search.persistent_retrieval")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def rows(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def blocked(name):
    return name.startswith("semantic_search.qwen") or any(
        name == prefix or name.startswith(prefix + ".") for prefix in BLOCKED_IMPORTS)


class RefuseModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if blocked(fullname):
            raise RuntimeError("Model/numerical import forbidden in CPU replay: " + fullname)
        return None


@contextmanager
def offline_cpu_guard():
    require(not any(blocked(name) for name in sys.modules), "Use a clean process without model/numerical imports")
    finder = RefuseModels()
    sys.meta_path.insert(0, finder)
    def denied(*args, **kwargs):
        raise RuntimeError("Network is forbidden in CPU replay")
    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
                patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
                patch.object(socket.socket, "sendto", denied):
            yield
    finally:
        sys.meta_path.remove(finder)


def runtime(root):
    cache = Path(os.environ.get("TIKTOKEN_CACHE_DIR", root / ".cache/tokenizers")).resolve()
    raw = (cache / CACHE_KEY).read_bytes()
    require(sha(raw) == CACHE_SHA256, "Exact offline cl100k cache required; downloads are forbidden")
    installed = {name: importlib.metadata.version(name) for name in PACKAGES}
    require(installed == PACKAGES, "Pinned CPU parser/tokenizer versions required")
    return {"packages": installed, "encoding": "cl100k_base", "rank_bytes_sha256": sha(raw)}, raw


@contextmanager
def cpu_modules(root, rank_bytes):
    require(not any(name == "semantic_search" or name.startswith("semantic_search.") for name in sys.modules),
            "Replay needs a clean process without production imports")
    sys.path.insert(0, str(root / "src"))
    try:
        import tiktoken.load
        def captured(url, expected_hash=None):
            require(url == CACHE_URL and expected_hash == CACHE_SHA256, "Unexpected tokenizer resource")
            return rank_bytes
        with patch.object(tiktoken.load, "read_file_cached", captured):
            modules = {name: importlib.import_module("semantic_search." + name)
                       for name in ("context", "corpus", "relations", "selection", "languages")}
            modules["context"].encoding()
            yield modules
    finally:
        sys.path.remove(str(root / "src"))
        for name in list(sys.modules):
            if name == "semantic_search" or name.startswith("semantic_search."):
                del sys.modules[name]


def groups(query):
    result = query.get("groups", query.get("target_groups", [query.get("targets")]))
    if query["kind"] == "absent":
        require(result in ([], [None], [[]]), "Absent query has positive evidence")
        return []
    require(query["kind"] == "present" and result and all(result), "Malformed positive evidence")
    return result


def ordered_response(response):
    result = deepcopy(response)
    result["scope"] = {key: result["scope"][key] for key in
                       ("source_root", "membership", "source_exclusions", "exclusions_omitted")}
    if "token_usage" in result:
        result["token_usage"] = {key: result["token_usage"][key] for key in
                                 ("encoding", "tokens", "budget", "truncated")}
    return result


def legacy_stream(pairs, references):
    """Independent literal control: first display label, first5, first2 callees."""
    distinct = {}
    for item, score in pairs:
        distinct.setdefault((item.path, item.symbol), (item, score))
    eligible = list(distinct.values())[:8]
    anchors, stream, included = eligible[:5], [], set()
    for item, score in anchors:
        stream.append((item, score, "retrieval", None))
        included.add((item.path, item.symbol))
    for item, score in anchors:
        for related in references.dependencies(item, limit=2):
            identity = related.path, related.symbol
            if identity not in included and len(stream) < 8:
                stream.append((related, score, "dependency", item.id))
                included.add(identity)
    for item, score in eligible:
        identity = item.path, item.symbol
        if identity not in included and len(stream) < 8:
            stream.append((item, score, "retrieval", None))
            included.add(identity)
    return stream


def source_row(item, score, origin, related_to, source, physical_lines):
    return {"id": item.id, "path": item.path, "symbol": item.symbol,
            "start_line": item.start_line, "end_line": item.start_line + len(physical_lines(source)) - 1,
            "window_end_line": item.end_line, "source": source, "content_hash": item.content_hash,
            "source_hash": sha(source.encode()), "retrieval_score": float(score), "h2o_score": None,
            "origin": origin, "related_to": related_to, "truncated": source != item.text}


def pack(template, stream, context, physical_lines):
    """Unchanged two-pass line clipping; selection has already finished."""
    result = {key: deepcopy(template[key]) for key in ("query", "scope", "match_status")}
    result["results"], remaining = [], SETTINGS["source_budget"]
    for item, score, origin, related_to in stream:
        prefix, used = [], 0
        for line in physical_lines(item.text):
            if used + len(line) > remaining:
                break
            prefix.append(line)
            used += len(line)
        source = "".join(prefix)
        if not source:
            continue
        remaining -= len(source)
        row = source_row(item, score, origin, related_to, source, physical_lines)
        if template["results"] and "qwen_score" in template["results"][0]:
            row["qwen_score"] = None
        result["results"].append(row)
    result["total_source_chars"] = SETTINGS["source_budget"] - remaining
    result["budget_exhausted"] = any(row["truncated"] for row in result["results"]) or len(result["results"]) < len(stream)
    return context.apply_token_budget(result, SETTINGS["token_budget"])


def evidence_ranks(windows, evidence):
    return [next((position for position, window in enumerate(windows, 1)
                 if any(window["path"] == target["path"] and window["start_line"] <= target["evidence_lines"][0]
                        and window["end_line"] >= target["evidence_lines"][1] for target in alternatives)), None)
            for alternatives in evidence]


def own_bindings(root):
    paths = ["scripts/replay_selection.py", "tests/test_selection_replay.py", "tests/test_selection.py",
             "tests/test_definition_identity.py", "tests/test_context_backfill.py", "docs/selection-development-protocol.md",
             *("src/semantic_search/" + name + ".py" for name in MODULES)]
    return {name: sha((root / name).read_bytes()) for name in paths}


def load_inputs(root, corpus_root, modules):
    """Read and verify frozen observed inputs; attach labels only to replay cases."""
    bindings, fixtures, cases = {}, {}, []
    physical_lines = modules["corpus"].physical_lines

    def bound(relative, expected=None):
        path = root / relative
        digest = sha(path.read_bytes())
        require(expected is None or digest == expected, "Frozen input changed: " + relative)
        bindings[relative] = digest
        return path

    def fixture(dataset, source_hashes, source_root, exclusions, *, windows=None, inventory_sha=None):
        signature = sha(json.dumps(source_hashes, sort_keys=True).encode())
        key = dataset, signature
        if key not in fixtures:
            corpus = (corpus_root / ("jinja-multifile-holdout" if dataset == "jinja" else dataset)).resolve()
            sources = {}
            for filename, expected in source_hashes.items():
                path = (corpus / filename).resolve()
                require(path.is_relative_to(corpus), "Source escapes pinned corpus")
                raw = path.read_bytes()
                require(sha(raw) == expected, "Pinned source changed: " + dataset + "/" + filename)
                sources[filename] = modules["languages"].decode_source(path, raw)
            definition_keys = {}
            snippets = modules["corpus"].extract_snippets(corpus, source_root, 160, 40,
                sources=sources, source_exclusions=exclusions, definition_keys=definition_keys)
            require(len({item.id for item in snippets}) == len(snippets) == len(definition_keys),
                    "Every source window must have a unique definition mapping")
            fixtures[key] = {"dataset": dataset, "sources": sources, "source_sha256": source_hashes,
                "inventory": {item.id: item for item in snippets}, "definition_keys": definition_keys,
                "graph": modules["relations"].ReferenceGraph(sources, snippets)}
        result = fixtures[key]
        if windows is not None:
            actual = [{"id": item.id, "path": item.path, "symbol": item.symbol,
                       "start_line": item.start_line, "end_line": item.end_line, "source_hash": item.content_hash}
                      for item in result["inventory"].values()]
            require(actual == windows, "Extraction differs from frozen window inventory: " + dataset)
        if inventory_sha is not None:
            actual = [asdict(item) for item in result["inventory"].values()]
            require(sha(json.dumps(actual, sort_keys=True).encode()) == inventory_sha,
                    "Extraction differs from frozen complete inventory: " + dataset)
        return result

    def add(subset, dataset, condition, row, query, ordered, fx, expected_ranks, *, scores=None):
        require(row["kind"] == query["kind"], "Observed query kind differs")
        original = ordered_response(row["response"])
        evidence = groups(query)
        for alternatives in evidence:
            for target in alternatives:
                low, high = target["evidence_lines"]
                require(type(low) is type(high) is int and 1 <= low <= high <= len(physical_lines(fx["sources"][target["path"]])),
                        "Invalid complete evidence span")
                text = "".join(physical_lines(fx["sources"][target["path"]])[low - 1:high])
                require(sha(text.encode()) == target["evidence_sha256"], "Pinned evidence bytes differ")
        pairs = []
        for window in ordered:
            item = fx["inventory"][window["id"]]
            require(all(window[name] == getattr(item, name) for name in ("path", "symbol", "start_line", "end_line"))
                    and window["source_hash"] == item.content_hash, "Original ranked candidate differs")
            score = scores[item.id] if scores is not None else window["retrieval_score"]
            require(type(score) in (int, float) and math.isfinite(score), "Non-finite original score")
            pairs.append((item, score))
        require(len(pairs) <= 50 and len({item.id for item, _ in pairs}) == len(pairs), "Invalid original candidate identity/count")
        require(evidence_ranks(original["results"], evidence) == expected_ranks, "Original delivered evidence ranks differ")
        cases.append({"subset": subset, "dataset": dataset, "query_id": query.get("id", query.get("query_id")),
            "condition": condition, "kind": query["kind"], "groups": evidence,
            "multi_file": row.get("multi_file_and", row.get("multi_file", False)),
            "pairs": pairs, "original": original, **fx})

    folder = "results/language-quality-2026-10-13/"
    audit = read(bound(folder + "runtime-audit.json"))
    require(audit["checks_passed"] is True, "Successful original language runtime audit required")
    old = read(bound(folder + "frozen-config.json", audit["artifact_sha256"]["frozen-config.json"]))
    preflight = read(bound(folder + "extractor-preflight.json", audit["artifact_sha256"]["extractor-preflight.json"]))
    observed = rows(bound(folder + "searches.jsonl", audit["artifact_sha256"]["searches.jsonl"]))
    manifest = read(bound("benchmarks/language_quality_manifest.json", old["manifest_sha256"]))
    require(old["settings"] == SETTINGS, "Language caps/settings differ")
    for entry in manifest["datasets"]:
        dataset = entry["id"]
        labels = read(bound(entry["dataset"], entry["sha256"]))
        queries = {query["id"]: query for query in labels["queries"]}
        snapshot = next(item for item in old["sources"] if item["id"] == dataset)
        inventory = next(item for item in preflight["datasets"] if item["id"] == dataset)
        require(snapshot["source_file_sha256"] == inventory["source_file_sha256"], "Source inventories differ")
        fx = fixture(dataset, snapshot["source_file_sha256"], entry["source_root"], entry["source_exclusions"], windows=inventory["window_inventory"])
        for row in observed:
            if row["dataset"] == dataset and row["repetition"] == 1:
                add("language_observed", dataset, row["condition"], row, queries[row["query_id"]], row["candidate_windows"], fx, row["evidence_ranks"])

    for dataset in ("express", "zod", "jinja"):
        folder = "results/multifile-" + dataset + "-2026-10-19/"
        audit = read(bound(folder + "audit.json"))
        require(audit["checks_passed"] is True, "Successful native independent audit required")
        old = read(bound(folder + "plan.json", audit["artifact_sha256"]["plan.json"]))
        binding = old["binding"]
        require(binding["settings"] == SETTINGS, "Native caps/settings differ")
        labels = read(bound(audit["dataset"]["path"], binding["dataset_sha256"]))
        queries = {query["id"]: query for query in labels["queries"]}
        fx = fixture(dataset, binding["source_sha256"], labels["source_root"], labels.get("source_exclusions", ()), inventory_sha=binding["inventory_sha256"])
        observed = rows(bound(folder + "searches.jsonl.gz", audit["artifact_sha256"]["searches.jsonl.gz"]))
        for row in observed:
            if row["repetition"] == 1 and row["retrieval"] in ("dense", "hybrid"):
                require(row["stages"]["ranked"] == row["stages"]["retrieved"], "Native dense/hybrid order unexpectedly reranked")
                add("native_" + dataset + "_observed", dataset, row["retrieval"], row, queries[row["query_id"]], row["stages"]["ranked"], fx, row["delivered_ranks"])

    for subset, folder in (("qwen_development_observed", "results/qwen-shortlist-development-2026-10-14/"),
                           ("qwen_httpx_observed", "results/qwen-shortlist-httpx-2026-10-14/")):
        audit = read(bound(folder + "independent-audit.json"))
        require(audit["checks_passed"] is True, "Successful observed Qwen audit required")
        old = read(bound(folder + "frozen-plan.json", audit["artifact_sha256"]["frozen-plan.json"]))
        require(old["settings"] == SETTINGS, "Qwen caps/settings differ")
        observed = rows(bound(folder + "searches.jsonl.gz", audit["artifact_sha256"]["searches.jsonl.gz"]))
        pools = {pool["pool_id"]: pool for pool in rows(bound(folder + "candidate-pools.jsonl.gz", audit["artifact_sha256"]["candidate-pools.jsonl.gz"]))}
        bound(folder + "source-content.jsonl.gz", audit["artifact_sha256"]["source-content.jsonl.gz"])
        for entry in old["sources"]:
            dataset = entry["id"]
            fx = fixture(dataset, entry["source_file_sha256"], "", (), windows=entry["window_inventory"])
            queries = {query["query_id"]: query for query in entry["queries"]}
            for row in observed:
                if row["identity"]["dataset"] != dataset or row["identity"]["repetition"] != 1 or row["configuration"]["id"] != "hybrid_k24_b1":
                    continue
                require(row["status"] == "completed" and row["shortlist_original_order"] == row["candidate_order"][:24]
                        and len(row["ranking_order"]) == len(set(row["ranking_order"])) == 24
                        and set(row["ranking_order"]) == set(row["shortlist_original_order"]), "Original scored K24 identities differ")
                pool = pools[row["pool_id"]]
                require(pool["pool_sha256"] == row["candidate_pool_sha256"]
                        and sha(json.dumps(pool["windows"], ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()) == pool["pool_sha256"], "Original candidate pool identity differs")
                windows = {window["id"]: window for window in pool["windows"]}
                require(list(windows) == row["candidate_order"], "Original pool order differs")
                scores = dict(zip(row["shortlist_original_order"], [score["logit_difference"] for score in row["reranker"]["results"]]))
                require(len(scores) == 24, "Original scalar score count differs")
                add(subset, dataset, "hybrid_k24_b1", row, queries[row["identity"]["query_id"]],
                    [windows[identifier] for identifier in row["ranking_order"]], fx, row["evidence_ranks"], scores=scores)
    require(dict(Counter(case["subset"] for case in cases)) == EXPECTED_ROWS, "Incomplete prespecified first-repetition matrix")
    require(len({(case["subset"], case["dataset"], case["query_id"], case["condition"]) for case in cases}) == len(cases), "Duplicate observed row")
    return cases, bindings, fixtures


def verify_response(response, case, context, physical_lines):
    require(len(response["results"]) <= 8 and len({row["id"] for row in response["results"]}) == len(response["results"]), "Delivered excerpt count/identity differs")
    for row in response["results"]:
        full = case["inventory"][row["id"]]
        require(all(row[key] == getattr(full, key) for key in ("path", "symbol", "start_line"))
                and full.start_line <= row["end_line"] <= full.end_line == row["window_end_line"], "Physical source/window identity differs")
        source = "".join(physical_lines(case["sources"][row["path"]])[row["start_line"] - 1:row["end_line"]])
        require(source == row["source"] and row["source_hash"] == sha(source.encode())
                and row["content_hash"] == full.content_hash and row["truncated"] == (source != full.text), "Literal source/hash/truncation differs")
        require(row["origin"] in ("retrieval", "dependency")
                and (row["related_to"] is None if row["origin"] == "retrieval" else row["related_to"] in case["inventory"]), "Invalid origin/parent")
    require(response["total_source_chars"] == sum(len(row["source"]) for row in response["results"]) <= 12000, "Character cap differs")
    text = context.serialize_context(context.compact_result(response))
    require(len(context.encoding().encode(text, disallowed_special=())) == response["token_usage"]["tokens"] <= 4096, "Exact compact token cap/count differs")
    return text


def case_identity(case):
    return {key: case[key] for key in ("subset", "dataset", "query_id", "condition")}


def control_barrier(cases, modules):
    """Verify every immutable baseline before returning to any variant caller."""
    context, physical_lines = modules["context"], modules["corpus"].physical_lines
    controls = []
    for case in cases:
        baseline = pack(case["original"], legacy_stream(case["pairs"], case["graph"]), context, physical_lines)
        text = verify_response(baseline, case, context, physical_lines)
        original = case["original"]
        require(text == context.serialize_context(context.compact_result(original))
                and baseline["results"] == original["results"]
                and baseline["total_source_chars"] == original["total_source_chars"]
                and baseline["budget_exhausted"] == original["budget_exhausted"]
                and baseline["token_usage"] == original["token_usage"], "Exact baseline mismatch: " + str(case_identity(case)))
        controls.append({**case_identity(case), "compact_sha256": sha(text.encode())})
    return controls


def counts(cases):
    return {"observed_rows": len(cases), "unique_queries": len({(case["dataset"], case["query_id"]) for case in cases}),
            "row_counts_by_subset": dict(Counter(case["subset"] for case in cases)),
            "unique_queries_by_subset": {subset: len({(case["dataset"], case["query_id"]) for case in cases if case["subset"] == subset}) for subset in EXPECTED_ROWS},
            "warning": "Conditions and reused datasets share query units; rows are not independent new queries."}


def freeze(root, corpus_root, output):
    require(not output.exists(), "Use a new freeze directory")
    cpu_runtime, rank_bytes = runtime(root)
    with offline_cpu_guard(), cpu_modules(root, rank_bytes) as modules:
        cases, bindings, fixtures = load_inputs(root, corpus_root, modules)
        controls = control_barrier(cases, modules)
    plan = {"version": 1, "classification": "Prospective CPU DEVELOPMENT replay on already observed retrieval/reranker outputs",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(), "conditions": CONDITIONS, "settings": SETTINGS,
        "counts": counts(cases), "producer_sha256": own_bindings(root), "input_sha256": bindings,
        "runtime": cpu_runtime, "source_sha256": {dataset + "/" + signature: fx["source_sha256"] for (dataset, signature), fx in fixtures.items()},
        "baseline_gate": "All337 first-repetition compact strings, complete result metadata, source bytes/hashes, character totals and budget flags exactly reproduce before any variant is evaluated.",
        "baseline_controls_sha256": sha(json.dumps(controls, sort_keys=True, separators=(",", ":")).encode()),
        "selection": {"legacy": "Display path/symbol dedup; first5 anchors; first2 static callees; remaining eligible ranked prefix to8.",
            "definition": "Canonical AST definition dedup with original allocation. Ambiguous graph display labels cannot expand from/to another definition; unknown identities fall back to window ID.",
            "rank_preserving": "Original display dedup; preserve first8 ranked results. Append first2 callees of first5 anchors only if fewer than8 ranked definitions exist.",
            "combined": "Canonical definition dedup plus preserve ranked capacity and append callees only into spare slots.",
            "legacy_coverage_backfill": "Legacy selection/packing, then remove actually delivered source wholly covered by another returned row; preserve every old source byte; guarded append ranked display-distinct windows into spare capacity.",
            "definition_coverage_backfill": "Definition selection/packing plus the same source-preserving coverage/backfill postprocessor; compare independently against legacy and legacy coverage control."},
        "packing": "Original two-pass whole-physical-line12000-character clipping then exact compact cl100k JSON4096-token clipping; at most8 excerpts. Only the two declared coverage conditions postprocess: remove contained actual delivered rows (earlier child/later parent included; equal bounds retain first), retain old nonredundant rows exactly, append fullranklist candidates into spare slots with whole-line char/token clipping, at most8 new noncovered candidate trials. No retrimming old source, overlap rewriting, retrieval, rescoring or threshold changes.",
        "labels": "Selectors receive only pairs, query, caps, dependencies and ephemeral definition mapping; case IDs, labels, kinds and evidence groups remain in reporting code after packing.",
        "qualification": "Each variant is independent: no lost full evidence group in any observed row, no lost baseline-complete query/condition, no single-file first-hit drop, and at least one additional unique complete query. No automatic winner or default change.",
        "execution": "Review and commit unchanged code/tests/protocol/plan/control-gate before --run. Freeze computes exact baselines only; synthetic tests do not evaluate observed variants.",
        "limits": ["All inputs, including former Zod/Jinja/HTTPX holdouts, are now observed development evidence; no fresh-holdout or generalization claim.",
                   "Source-complete AND coverage is a small source-authored retrieval measure; it is not coding correctness, provider billing, speed or agent-token savings.",
                   "Reuse across conditions and studies is reported explicitly; do not pool337 rows as337 independent queries.",
                   "No model loading, numerical runtime, HTTP retrieval, reranking, network or GPU work is permitted."]}
    output.mkdir(parents=True)
    write_new(output / "frozen-plan.json", plan)
    write_new(output / "baseline-gate.json", {"exact_rows": len(controls), "cases": controls, "variant_outcomes_computed": 0,
              "plan_sha256": sha((output / "frozen-plan.json").read_bytes())})
    return {"classification": plan["classification"], **plan["counts"], "exact_baselines": len(controls), "variants_computed": 0}


def validate_run(root, output, plan):
    require(plan["conditions"] == CONDITIONS and plan["settings"] == SETTINGS, "Undeclared selector/configuration")
    require(plan["producer_sha256"] == own_bindings(root), "Code/tests/protocol changed after freeze")
    tracked = [*plan["producer_sha256"], *((output / name).relative_to(root).as_posix() for name in ("frozen-plan.json", "baseline-gate.json"))]
    subprocess.check_call(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", *tracked], stdout=subprocess.DEVNULL)
    require(subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", *tracked]).returncode == 0,
            "Commit unchanged prospective code/tests/protocol/plan/control gate before variant evaluation")
    require(not any((output / name).exists() for name in ("run-start.json", "control-gate.json", "replay.jsonl.gz", "summary.json", "findings.md", "failure.json")),
            "Never overwrite or resume a prior run/failure")


def selected_stream(case, selector):
    distinct, eligible, origins = selector(case["pairs"], query=case["original"]["query"], max_results=8,
        include_dependencies=True, references=case["graph"], definition_keys=case["definition_keys"])
    stream = [(item, score, *origins.get(item.id, ("retrieval", None))) for item, score in eligible]
    labels = {}
    for item, _ in case["pairs"]:
        labels.setdefault((item.path, item.symbol), set()).add(case["definition_keys"].get(item.id, ("window", item.id)))
    return stream, {"ranked_windows": len(case["pairs"]), "distinct": len(distinct), "selected": len(eligible),
                    "ranked_display_labels_with_multiple_definitions": sum(len(keys) > 1 for keys in labels.values())}, distinct


def require_source_preserved(before, after, physical_lines):
    """Independently check every old delivered span against one actual new row."""
    for old in before["results"]:
        covering = [row for row in after["results"] if row["path"] == old["path"]
                    and row["start_line"] <= old["start_line"] <= old["end_line"] <= row["end_line"]]
        require(any("".join(physical_lines(row["source"])[old["start_line"] - row["start_line"]:
                         old["end_line"] - row["start_line"] + 1]) == old["source"] for row in covering),
                "Coverage postprocessor lost original actual delivered source")
    old_by_id = {row["id"]: row for row in before["results"]}
    for row in after["results"]:
        if row["id"] in old_by_id:
            require(row == old_by_id[row["id"]], "Coverage postprocessor changed a retained old row")


def group_changes(original, response, evidence):
    before, after = evidence_ranks(original["results"], evidence), evidence_ranks(response["results"], evidence)
    gains, losses = [], []
    for number, (prior, current, alternatives) in enumerate(zip(before, after, evidence), 1):
        if (prior is None) == (current is None):
            continue
        item = {"group": number, "evidence": alternatives, "baseline_rank": prior, "variant_rank": current,
                "baseline_excerpts": [row["id"] for row in original["results"] if evidence_ranks([row], [alternatives])[0] is not None],
                "variant_excerpts": [row["id"] for row in response["results"] if evidence_ranks([row], [alternatives])[0] is not None]}
        prior_rows = [row for row in original["results"] if evidence_ranks([row], [alternatives])[0] is not None]
        item["baseline_covering_origins"] = [{"id": row["id"], "origin": row.get("origin", "retrieval"),
                                              "related_to": row.get("related_to")} for row in prior_rows]
        item["baseline_dependency_only"] = bool(prior_rows) and all(row.get("origin") == "dependency" for row in prior_rows)
        (gains if prior is None else losses).append(item)
    return before, after, gains, losses


def metrics(items):
    positive = [item for item in items if item["kind"] == "present"]
    return {"observed_rows": len(items), "unique_queries": len({(item["dataset"], item["query_id"]) for item in items}),
            "present_rows": len(positive), "absent_rows": len(items) - len(positive),
            "complete": sum(None not in item["evidence_ranks"] for item in positive),
            "required_groups": sum(len(item["evidence_ranks"]) for item in positive),
            "covered_groups": sum(sum(rank is not None for rank in item["evidence_ranks"]) for item in positive),
            "multi_file_complete": sum(item["multi_file"] and None not in item["evidence_ranks"] for item in positive),
            "single_file_first_hit": sum(not item["multi_file"] and bool(item["evidence_ranks"]) and item["evidence_ranks"][0] is not None for item in positive),
            "median_excerpts": statistics.median(len(item["response"]["results"]) for item in items),
            "median_chars": statistics.median(item["response"]["total_source_chars"] for item in items),
            "median_tokens": statistics.median(item["response"]["token_usage"]["tokens"] for item in items),
            "max_tokens": max(item["response"]["token_usage"]["tokens"] for item in items),
            "token_truncated": sum(item["response"]["token_usage"]["truncated"] for item in items),
            "absent_inventory": [{"query_id": item["query_id"], "status": item["response"]["match_status"], "excerpts": len(item["response"]["results"])} for item in items if item["kind"] == "absent"]}


def summarize(outcomes, case_counts):
    by_subset, changes, qualifications = {}, {}, {}
    for name in CONDITIONS:
        selected = [item for item in outcomes if item["selector"] == name]
        strata = sorted({(item["subset"], item["dataset"], item["condition"]) for item in selected})
        by_subset[name] = {"/".join(stratum): metrics([item for item in selected if tuple(item[key] for key in ("subset", "dataset", "condition")) == stratum]) for stratum in strata}
        gains = [{**{key: item[key] for key in ("subset", "dataset", "query_id", "condition")}, "groups": item["group_gains"]} for item in selected if item["group_gains"]]
        losses = [{**{key: item[key] for key in ("subset", "dataset", "query_id", "condition")}, "groups": item["group_losses"]} for item in selected if item["group_losses"]]
        complete_gains = [case_identity(item) for item in selected if item["kind"] == "present" and None in item["baseline_ranks"] and None not in item["evidence_ranks"]]
        complete_losses = [case_identity(item) for item in selected if item["kind"] == "present" and None not in item["baseline_ranks"] and None in item["evidence_ranks"]]
        first_hit_losses = [case_identity(item) for item in selected if item["kind"] == "present" and not item["multi_file"] and item["baseline_ranks"] and item["baseline_ranks"][0] is not None and item["evidence_ranks"][0] is None]
        unique_gains = sorted({item["dataset"] + "/" + item["query_id"] for item in complete_gains})
        changes[name] = {"group_gains": gains, "group_losses": losses, "complete_gains": complete_gains,
                         "complete_losses": complete_losses, "unique_complete_gains": unique_gains,
                         "single_file_first_hit_losses": first_hit_losses}
        if name != "legacy":
            qualifications[name] = {"qualified": not losses and not complete_losses and not first_hit_losses and bool(unique_gains),
                "lost_groups": sum(len(item["groups"]) for item in losses), "lost_complete_rows": len(complete_losses),
                "lost_single_file_first_hits": len(first_hit_losses), "gained_unique_complete_queries": len(unique_gains)}
    pairwise = []
    by_identity = {(item["selector"], item["subset"], item["dataset"], item["query_id"], item["condition"]): item for item in outcomes}
    for item in outcomes:
        if item["selector"] != "definition_coverage_backfill" or "evidence_groups" not in item:
            continue
        prior = by_identity[("legacy_coverage_backfill", item["subset"], item["dataset"], item["query_id"], item["condition"])]
        before, after, gains, losses = group_changes(prior["response"], item["response"], item["evidence_groups"])
        if gains or losses:
            pairwise.append({**case_identity(item), "control": "legacy_coverage_backfill", "variant": "definition_coverage_backfill",
                             "control_ranks": before, "variant_ranks": after, "group_gains": gains, "group_losses": losses})
    return {"classification": "Observed-source CPU DEVELOPMENT selection replay", "counts": case_counts,
            "metrics_by_study_dataset_original_condition": by_subset, "all_changes": changes,
            "definition_coverage_vs_legacy_coverage_all_group_changes": pairwise,
            "qualification": qualifications, "model_calls": 0, "retrieval_calls": 0, "automatic_default_change": False}


def findings(summary):
    output = ["# Response-selection development replay", "", summary["classification"] + ". All former holdouts are already observed.", "",
        f"{summary['counts']['observed_rows']} observed first-repetition rows share {summary['counts']['unique_queries']} unique dataset/query units. Reused query/condition rows are not independent new queries.", "",
        "Every original compact payload and full excerpt/source/hash/cap record reproduced before variants. All conditions used 8 excerpts, 12,000 source characters and 4,096 exact compact cl100k tokens; no model, retrieval or network calls occurred.", "",
        "| selector | study / dataset / original order | complete | groups | multi-file complete | single-file first hit |", "|---|---|---:|---:|---:|---:|"]
    for name, strata in summary["metrics_by_study_dataset_original_condition"].items():
        for stratum, metric in strata.items():
            output.append(f"| {name} | {stratum} | {metric['complete']} / {metric['present_rows']} | {metric['covered_groups']} / {metric['required_groups']} | {metric['multi_file_complete']} | {metric['single_file_first_hit']} |")
    output.extend(["", "Qualification requires no lost group, baseline-complete row or single-file first hit, and an additional unique complete query. Each change is evaluated independently; no default is selected automatically.", ""])
    for name, verdict in summary["qualification"].items():
        output.append(f"- {name}: {'qualifies for further development review' if verdict['qualified'] else 'does not qualify'}; {verdict['gained_unique_complete_queries']} unique complete-query gains, {verdict['lost_groups']} group losses, {verdict['lost_complete_rows']} complete-row losses.")
    output.extend(["", "All full-evidence group gains and losses, complete-query changes, absent-query inventories and per-row responses are retained in summary.json and replay.jsonl.gz. Source-complete coverage does not establish coding correctness, generalization, speed or token savings.", ""])
    return "\n".join(output)


def run(root, corpus_root, output):
    plan = read(output / "frozen-plan.json")
    validate_run(root, output, plan)
    write_new(output / "run-start.json", {"created_at_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
              "plan_sha256": sha((output / "frozen-plan.json").read_bytes()), "model_calls": 0, "retrieval_calls": 0, "resume_allowed": False})
    state = {"stage": "runtime", "exact_controls": 0, "variant_rows": 0}
    try:
        cpu_runtime, rank_bytes = runtime(root)
        require(cpu_runtime == plan["runtime"], "CPU runtime changed after freeze")
        with offline_cpu_guard(), cpu_modules(root, rank_bytes) as modules:
            cases, bindings, _ = load_inputs(root, corpus_root, modules)
            require(bindings == plan["input_sha256"] and counts(cases) == plan["counts"], "Frozen inputs/matrix changed")
            state["stage"] = "every_original_control"
            controls = control_barrier(cases, modules)
            state["exact_controls"] = len(controls)
            baseline_gate = read(output / "baseline-gate.json")
            require(controls == baseline_gate["cases"]
                    and baseline_gate["plan_sha256"] == sha((output / "frozen-plan.json").read_bytes())
                    and plan["baseline_controls_sha256"] == sha(json.dumps(controls, sort_keys=True, separators=(",", ":")).encode()),
                    "Prospective baseline gate differs")
            write_new(output / "control-gate.json", {"exact_rows": len(controls), "cases": controls, "variant_rows_before_gate": 0})
            state["stage"] = "all_selectors"
            outcomes = []
            for case in cases:
                for name, function in CONDITIONS.items():
                    stream, diagnostics, distinct = selected_stream(case, getattr(modules["selection"], function))
                    response = pack(case["original"], stream, modules["context"], modules["corpus"].physical_lines)
                    if name.endswith("_coverage_backfill"):
                        prior = deepcopy(response)
                        candidates = [source_row(item, score, "retrieval", None, item.text, modules["corpus"].physical_lines)
                                      for item, score in distinct]
                        if case["original"]["results"] and "qwen_score" in case["original"]["results"][0]:
                            for candidate in candidates:
                                candidate["qwen_score"] = None
                        response = importlib.import_module("semantic_search.context_backfill").coalesce_context(
                            response, candidates, max_results=8, source_budget=12000, token_budget=4096)
                        require_source_preserved(prior, response, modules["corpus"].physical_lines)
                        diagnostics["backfill"] = response.get("_selection_backfill", {})
                    text = verify_response(response, case, modules["context"], modules["corpus"].physical_lines)
                    if name == "legacy":
                        require(text == modules["context"].serialize_context(modules["context"].compact_result(case["original"]))
                                and response["results"] == case["original"]["results"], "Production legacy selector differs from independent exact control")
                    before, after, gains, losses = group_changes(case["original"], response, case["groups"])
                    outcomes.append({**case_identity(case), "selector": name, "kind": case["kind"], "multi_file": case["multi_file"],
                        "evidence_groups": case["groups"],
                        "baseline_ranks": before, "evidence_ranks": after, "group_gains": gains, "group_losses": losses,
                        "selection_diagnostics": diagnostics, "response": response, "compact_sha256": sha(text.encode())})
                    state["variant_rows"] += name != "legacy"
            state["stage"] = "publish_all_outcomes"
            with (output / "replay.jsonl.gz").open("xb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
                    for outcome in outcomes:
                        zipped.write((json.dumps(outcome, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode())
            summary = summarize(outcomes, counts(cases))
            summary.update({"baseline_exact_rows": len(controls), "plan_sha256": sha((output / "frozen-plan.json").read_bytes()),
                            "replay_sha256": sha((output / "replay.jsonl.gz").read_bytes())})
            write_new(output / "summary.json", summary)
            with (output / "findings.md").open("x", encoding="utf-8") as stream:
                stream.write(findings(summary))
            return {"classification": summary["classification"], **summary["counts"], "qualification": summary["qualification"], "baseline_exact_rows": len(controls)}
    except BaseException as error:
        write_new(output / "failure.json", {"failed": True, "error_type": type(error).__name__, "error": str(error),
                  "progress": state, "resume_allowed": False, "model_calls": 0, "retrieval_calls": 0})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args()
    operation = freeze if args.freeze else run
    print(json.dumps(operation(args.root.resolve(), args.corpus_root.resolve(), args.output.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
