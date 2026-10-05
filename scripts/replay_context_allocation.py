#!/usr/bin/env python3
"""Prospectively frozen CPU development replay of context allocation only."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib
import importlib.abc
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import tokenize
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
STUDIES = {"development": "results/qwen-shortlist-development-2026-10-14",
           "httpx_observed": "results/qwen-shortlist-httpx-2026-10-14"}
CONDITIONS = [{"id": f"results{count}_dependencies{str(dependencies).lower()}",
               "max_results": count, "include_dependencies": dependencies}
              for count in (8, 12, 20) for dependencies in (True, False)]
BASELINE = CONDITIONS[0]["id"]
MODULES = ("__init__", "source", "context", "relations", "languages", "corpus", "javascript", "javascript_relations")
CPU_PACKAGES = ("tiktoken", "tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript")
CACHE_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
CACHE_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
CACHE_KEY = hashlib.sha1(CACHE_URL.encode()).hexdigest()
BLOCKED_IMPORTS = ("mlx", "mlx_embeddings", "mlx_lm", "mlx_vlm", "torch", "transformers", "jax", "numpy",
                   "semantic_search.backend", "semantic_search.embeddings", "semantic_search.model",
                   "semantic_search.index", "semantic_search.store", "semantic_search.persistent_retrieval",
                   "semantic_search.qwen_reranker", "semantic_search.qwen_reranker_core", "semantic_search.qwen_worker")
ARTIFACTS = ("frozen-plan.json", "searches.jsonl.gz", "candidate-pools.jsonl.gz", "source-content.jsonl.gz")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def physical_lines(source):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", source) if line]


def gz_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def blocked(name):
    return name.startswith("semantic_search.qwen") or any(
        name == prefix or name.startswith(prefix + ".") for prefix in BLOCKED_IMPORTS)


class RefuseModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if blocked(fullname):
            raise RuntimeError(f"Model/native numerical import forbidden in CPU replay: {fullname}")
        return None


@contextmanager
def offline_cpu_guard():
    require(not any(blocked(name) for name in sys.modules), "Start replay in a clean process without model/native numerical imports")
    finder = RefuseModels()
    sys.meta_path.insert(0, finder)
    def denied(*args, **kwargs):
        raise RuntimeError("Network is forbidden in the CPU replay")
    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
                patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
                patch.object(socket.socket, "sendto", denied):
            yield
    finally:
        sys.meta_path.remove(finder)


def cache_runtime(root):
    cache = Path(os.environ.get("TIKTOKEN_CACHE_DIR", root / ".cache/tokenizers")).resolve()
    raw = (cache / CACHE_KEY).read_bytes() if (cache / CACHE_KEY).is_file() else b""
    require(sha(raw) == CACHE_SHA256, "Exact offline cl100k cache missing/corrupt; downloads are forbidden")
    packages = {name: importlib.metadata.version(name) for name in CPU_PACKAGES}
    require(packages == {"tiktoken": "0.14.0", "tree-sitter": "0.26.0",
                         "tree-sitter-javascript": "0.25.0", "tree-sitter-typescript": "0.23.2"},
            "Pinned CPU tokenizer/parser packages required; no installation is performed")
    return {"packages": packages, "tokenizer": {"encoding": "cl100k_base", "cache_key": CACHE_KEY,
            "rank_bytes_sha256": CACHE_SHA256}}, raw


@contextmanager
def frozen_modules(root, plan, rank_bytes):
    require(not any(name == "semantic_search" or name.startswith("semantic_search.") for name in sys.modules),
            "Frozen helpers require a clean process without live production imports")
    with tempfile.TemporaryDirectory(prefix="context-allocation-frozen-") as temporary:
        package = Path(temporary) / "semantic_search"
        package.mkdir()
        for filename, binding in plan["frozen_helpers"].items():
            raw = gzip.decompress((root / binding["archive"]).read_bytes())
            require(sha(raw) == binding["source_sha256"], "Frozen helper bytes changed: " + filename)
            (package / Path(filename).name).write_bytes(raw)
        sys.path.insert(0, temporary)
        original_dont_write = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            import tiktoken.load
            def captured(url, expected_hash=None):
                require(url == CACHE_URL and expected_hash == CACHE_SHA256,
                        "Unexpected tokenizer resource; network/cache writes are forbidden")
                return rank_bytes
            with patch.object(tiktoken.load, "read_file_cached", captured):
                context = importlib.import_module("semantic_search.context")
                context.encoding()  # Verified immutable rank bytes, no download.
                graph = importlib.import_module("semantic_search.relations").ReferenceGraph
                yield context, graph
        finally:
            sys.dont_write_bytecode = original_dont_write
            sys.path.remove(temporary)
            for name in list(sys.modules):
                if name == "semantic_search" or name.startswith("semantic_search."):
                    del sys.modules[name]


def original_order(response):
    result = deepcopy(response)
    require(set(result["token_usage"]) == {"encoding", "tokens", "budget", "truncated"}
            and set(result["scope"]) == {"source_root", "membership", "source_exclusions", "exclusions_omitted"},
            "Original compact metadata fields changed")
    result["token_usage"] = {key: result["token_usage"][key] for key in ("encoding", "tokens", "budget", "truncated")}
    result["scope"] = {key: result["scope"][key] for key in
                       ("source_root", "membership", "source_exclusions", "exclusions_omitted")}
    return result


def selected_stream(pairs, graph, *, max_results, include_dependencies):
    """Copied original allocation logic; no labels or case identifiers accepted."""
    require(type(max_results) is int and max_results in (8, 12, 20) and type(include_dependencies) is bool,
            "Only the six fixed allocation conditions are allowed")
    distinct = {}
    for snippet, score in pairs:
        distinct.setdefault((snippet.path, snippet.symbol), (snippet, score))
    eligible = list(distinct.values())[:max_results]
    if not include_dependencies:
        return [(snippet, score, "retrieval", None) for snippet, score in eligible]
    anchors, expanded, included = eligible[:5], [], set()
    for snippet, score in anchors:
        expanded.append((snippet, score, "retrieval", None))
        included.add((snippet.path, snippet.symbol))
    for snippet, score in anchors:
        for related in graph.dependencies(snippet, limit=2):
            identity = related.path, related.symbol
            if identity not in included and len(expanded) < max_results:
                expanded.append((related, score, "dependency", snippet.id))
                included.add(identity)
    for snippet, score in eligible:
        identity = snippet.path, snippet.symbol
        if identity not in included and len(expanded) < max_results:
            expanded.append((snippet, score, "retrieval", None))
            included.add(identity)
    return expanded


def pack(query, scope, pairs, graph, context, condition):
    stream = selected_stream(pairs, graph, **{key: condition[key] for key in ("max_results", "include_dependencies")})
    results, remaining = [], 12000
    for snippet, score, origin, related in stream:
        prefix, used = [], 0
        for line in physical_lines(snippet.text):
            if used + len(line) > remaining:
                break
            prefix.append(line)
            used += len(line)
        source = "".join(prefix)
        if not source:
            continue
        remaining -= len(source)
        results.append({"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
            "start_line": snippet.start_line, "end_line": snippet.start_line + len(prefix) - 1,
            "window_end_line": snippet.end_line, "source": source, "content_hash": snippet.content_hash,
            "source_hash": sha(source.encode()), "retrieval_score": float(score), "h2o_score": None,
            "origin": origin, "related_to": related, "truncated": source != snippet.text})
    response = {"query": query, "scope": deepcopy(scope), "match_status": "candidates" if pairs else "no_candidates",
                "results": results, "total_source_chars": 12000 - remaining,
                "budget_exhausted": any(row["truncated"] for row in results) or len(results) < len(stream)}
    return context.apply_token_budget(response, 4096)


def require_exact(response, observed, context):
    observed = original_order(observed)
    require(context.serialize_context(context.compact_result(response)) == context.serialize_context(context.compact_result(observed))
            and response["results"] == observed["results"]
            and response["total_source_chars"] == observed["total_source_chars"],
            "Original 8+dependencies control differs; no variant may run")


def evidence_ranks(windows, groups):
    return [next((position for position, window in enumerate(windows, 1)
                 if any(window["path"] == target["path"] and window["start_line"] <= target["evidence_lines"][0]
                        and window["end_line"] >= target["evidence_lines"][1] for target in group)), None) for group in groups]


def metrics(cases):
    positives = [case for case in cases if case["kind"] == "present"]
    negatives = [case for case in cases if case["kind"] == "absent"]
    complete = [case["case_id"] for case in positives if None not in case["evidence_ranks"]]
    multi = [case for case in positives if case["multi_file_and"]]
    return {"cases": len(cases), "present": len(positives), "absent": len(negatives),
        "complete": len(complete), "complete_case_ids": complete,
        "required_groups": sum(len(case["evidence_ranks"]) for case in positives),
        "covered_groups": sum(sum(rank is not None for rank in case["evidence_ranks"]) for case in positives),
        "multi_file_queries": len(multi), "multi_file_complete": sum(None not in case["evidence_ranks"] for case in multi),
        "absent_statuses": dict(Counter(case["response"]["match_status"] for case in negatives)),
        "absent_inventory": [{"case_id": case["case_id"], "match_status": case["response"]["match_status"],
                              "excerpts": len(case["response"]["results"])} for case in negatives],
        "median_tokens": statistics.median(case["response"]["token_usage"]["tokens"] for case in cases),
        "max_tokens": max(case["response"]["token_usage"]["tokens"] for case in cases),
        "median_source_chars": statistics.median(case["response"]["total_source_chars"] for case in cases),
        "median_excerpts": statistics.median(len(case["response"]["results"]) for case in cases),
        "max_excerpts": max(len(case["response"]["results"]) for case in cases),
        "token_truncated": sum(case["response"]["token_usage"]["truncated"] for case in cases),
        "budget_exhausted": sum(case["response"]["budget_exhausted"] for case in cases)}


def gate(baselines, variants):
    reasons, gained = [], 0
    for subset, baseline in baselines.items():
        variant = variants[subset]
        lost = sorted(set(baseline["complete_case_ids"]) - set(variant["complete_case_ids"]))
        if lost:
            reasons.append({"subset": subset, "rule": "retain_every_baseline_complete_positive", "lost": lost})
        gained += len(set(variant["complete_case_ids"]) - set(baseline["complete_case_ids"]))
        for field in ("multi_file_complete", "covered_groups"):
            if variant[field] < baseline[field]:
                reasons.append({"subset": subset, "rule": field + "_at_least_baseline", "before": baseline[field], "after": variant[field]})
    if not gained:
        reasons.append({"rule": "at_least_one_additional_complete_positive_across_both_subsets"})
    return {"qualified": not reasons, "complete_gains": gained, "reasons": reasons}


def own_bindings(root):
    names = ("scripts/replay_context_allocation.py", "tests/test_context_allocation_replay.py",
             "docs/context-allocation-development-protocol.md")
    return {name: sha((root / name).read_bytes()) for name in names}


def load_inputs(root):
    studies, bindings, helper = {}, {}, {}
    for subset, folder in STUDIES.items():
        path = root / folder
        audit, old = read(path / "independent-audit.json"), read(path / "frozen-plan.json")
        require(audit["checks_passed"] is True, "A successful prior independent audit is required")
        for name in (*ARTIFACTS, "independent-audit.json"):
            relative = folder + "/" + name
            digest = sha((root / relative).read_bytes())
            if name != "independent-audit.json":
                require(digest == audit["artifact_sha256"][name], "Previously audited input changed: " + relative)
            bindings[relative] = digest
        for name in MODULES:
            filename = "src/semantic_search/" + name + ".py"
            archive = folder + "/frozen-sources/" + filename + ".gz"
            raw = gzip.decompress((root / archive).read_bytes())
            require(sha(raw) == old["implementation_sha256"][filename], "Frozen helper changed")
            if filename in helper:
                require(helper[filename]["source_sha256"] == sha(raw), "Original studies have different packing/dependency helpers")
            else:
                helper[filename] = {"archive": archive, "source_sha256": sha(raw)}
            bindings[archive] = sha((root / archive).read_bytes())
        rows = [row for row in gz_rows(path / "searches.jsonl.gz")
                if row["configuration"]["id"] == "hybrid_k24_b1" and row["identity"]["repetition"] == 1]
        require(len(rows) == (144 if subset == "development" else 15), "Original first-pass matrix differs")
        require(all(row["status"] == "completed" and row["configuration"] ==
                    {"id": "hybrid_k24_b1", "batch_size": 1, "retrieval": "hybrid", "shortlist": 24}
                    for row in rows), "Complete original hybridK24B1 rows required")
        require(len({(row["identity"]["dataset"], row["identity"]["query_id"]) for row in rows}) == len(rows), "Duplicate original case")
        entries = {entry["id"]: entry for entry in old["sources"]}
        sources = {}
        for name, entry in entries.items():
            sources[name] = {}
            for filename, expected in entry["source_file_sha256"].items():
                source_path = (root / ".cache/corpora" / name / filename).resolve()
                require(source_path.is_relative_to((root / ".cache/corpora" / name).resolve()), "Corpus source path escapes root")
                raw = source_path.read_bytes()
                require(sha(raw) == expected, "Pinned source changed: " + name + "/" + filename)
                codec, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
                sources[name][filename] = raw.decode(codec)
            for query in entry["queries"]:
                require(query["kind"] in ("present", "absent") and bool(query["groups"]) == (query["kind"] == "present"),
                        "Original positive/absent gold groups differ")
                for alternatives in query["groups"]:
                    require(bool(alternatives), "Original OR evidence group is empty")
                    for target in alternatives:
                        low, high = target["evidence_lines"]
                        require(type(low) is type(high) is int and 1 <= low <= high <= len(physical_lines(sources[name][target["path"]])),
                                "Gold full-span bounds differ")
                        evidence = "".join(physical_lines(sources[name][target["path"]])[low - 1:high])
                        require(sha(evidence.encode()) == target["evidence_sha256"], "Gold executable source span differs")
        pools = {pool["pool_id"]: pool for pool in gz_rows(path / "candidate-pools.jsonl.gz")}
        studies[subset] = {"old": old, "entries": entries, "sources": sources, "rows": rows, "pools": pools}
    return studies, bindings, helper


def original_metrics(studies):
    result = {}
    for subset, study in studies.items():
        gold = {(name, query["query_id"]): query for name, entry in study["entries"].items() for query in entry["queries"]}
        cases = []
        for row in study["rows"]:
            dataset, query_id = row["identity"]["dataset"], row["identity"]["query_id"]
            label = gold[dataset, query_id]
            require(evidence_ranks(row["response"]["results"], label["groups"]) == row["evidence_ranks"], "Original source-span ranks differ")
            cases.append({"case_id": dataset + "/" + query_id, "kind": row["kind"], "multi_file_and": row["multi_file_and"],
                          "evidence_ranks": row["evidence_ranks"], "response": row["response"]})
        result[subset] = metrics(cases)
    require(result["development"]["complete"] == 112 and result["httpx_observed"]["complete"] == 8,
            "Original 112/8 complete-positive baselines changed")
    return result


def freeze(root, output):
    require(not output.exists(), "Freeze output must be a new directory")
    runtime, _ = cache_runtime(root)
    studies, bindings, helper = load_inputs(root)
    plan = {"version": 1, "classification": "Prospective CPU context-allocation DEVELOPMENT replay; HTTPX is already observed",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(), "conditions": CONDITIONS,
        "baseline_condition": BASELINE, "settings": {"scored_prefix": "original hybridK24B1", "source_budget": 12000,
        "token_budget": 4096, "first_anchors": 5, "callees_per_anchor": 2, "dependency_depth": 1},
        "selection": "Original first-ranked window per exact path/symbol; selected prefix capped at requested result count. With dependencies, first5 anchors then first2 static callees per anchor then remaining eligible anchors. Original two-pass whole-line character clipping then exact compact JSON token packing; no backfill, containment removal, clipping reclamation, rank/score changes or gold input.",
        "baseline_gate": "All159 original compact payload strings plus complete result metadata, source characters and flags must reproduce before any variant is computed/written.",
        "qualification_gate": {"retain_every_baseline_complete_positive": True, "baseline_complete": {"development": 112, "httpx_observed": 8},
        "strict_gain": "At least one additional complete positive across both subsets", "separate_subset_non_regression": ["multi_file_complete", "covered_groups"],
        "winner_selection": "List qualifying conditions only; no automatic winner/default change. No qualifying conditions means no winner."},
        "runtime": runtime, "producer_bindings": own_bindings(root), "inputs_sha256": bindings,
        "frozen_helpers": helper, "source_files_sha256": {subset: {name: entry["source_file_sha256"] for name, entry in study["entries"].items()} for subset, study in studies.items()},
        "original_baseline_metrics": original_metrics(studies), "case_counts": {"development": 144, "httpx_observed": 15},
        "execution": "Review and commit script/tests/protocol/this plan before --run. No variant outputs during freeze or synthetic CPU tests.",
        "limits": ["Six prospective DEVELOPMENT configurations on observed source/ranker outcomes; no fresh holdout, inference, retrieval, or speed measurement.",
                   "Larger excerpt limits trade more fragments/metadata and remaining fixed token/character capacity; any gains are not free quality.",
                   "Static dependencies can contain required evidence outside scored K24/full50; dependency-only group regressions are retained.",
                   "Compact text tokens are not provider billing, agent token savings, or coding correctness."]}
    output.mkdir(parents=True)
    write_new(output / "frozen-plan.json", plan)
    return plan


def validate_run(root, output, plan):
    require(plan["conditions"] == CONDITIONS and plan["baseline_condition"] == BASELINE, "Undeclared allocation condition")
    require(plan["producer_bindings"] == own_bindings(root), "Producer/tests/protocol changed after freeze")
    frozen_path = (output / "frozen-plan.json").relative_to(root).as_posix()
    tracked = [*plan["producer_bindings"], frozen_path]
    subprocess.check_call(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", *tracked], stdout=subprocess.DEVNULL)
    require(subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", *tracked]).returncode == 0,
            "Commit the unchanged prospective script/tests/protocol/plan before variant output")
    require(not any((output / name).exists() for name in ("run-start.json", "control-gate.json", "replay.jsonl", "summary.json", "findings.md", "failure.json")),
            "Fresh execution outputs required; never overwrite a completed/failed replay")


def prepare(studies, Graph):
    prepared = []
    for subset, study in studies.items():
        inventories, graphs = {}, {}
        for dataset, entry in study["entries"].items():
            snippets = []
            for window in entry["window_inventory"]:
                text = "".join(physical_lines(study["sources"][dataset][window["path"]])[window["start_line"] - 1:window["end_line"]])
                require(sha(text.encode()) == window["source_hash"], "Original window/source identity differs")
                snippets.append(SimpleNamespace(**{key: window[key] for key in ("id", "path", "symbol", "start_line", "end_line")},
                                                text=text, content_hash=window["source_hash"]))
            inventories[dataset] = {snippet.id: snippet for snippet in snippets}
            graphs[dataset] = Graph(study["sources"][dataset], snippets)
        gold = {(name, query["query_id"]): query for name, entry in study["entries"].items() for query in entry["queries"]}
        for row in study["rows"]:
            dataset, query_id = row["identity"]["dataset"], row["identity"]["query_id"]
            require(row["shortlist_original_order"] == row["candidate_order"][:24]
                    and len(row["ranking_order"]) == len(set(row["ranking_order"])) == 24
                    and set(row["ranking_order"]) == set(row["shortlist_original_order"]), "Only the original scored24 order may enter packing")
            scores = dict(zip(row["shortlist_original_order"], [score["logit_difference"] for score in row["reranker"]["results"]]))
            require(len(scores) == 24 and all(type(score) in (float, int) and math.isfinite(score) for score in scores.values()), "Original scalar-score identities differ")
            pairs = [(inventories[dataset][identifier], scores[identifier]) for identifier in row["ranking_order"]]
            prepared.append({"subset": subset, "dataset": dataset, "query_id": query_id,
                "case_id": dataset + "/" + query_id, "kind": row["kind"], "multi_file_and": row["multi_file_and"],
                "gold": gold[dataset, query_id]["groups"], "pairs": pairs, "graph": graphs[dataset], "original": row["response"],
                "inventory": inventories[dataset], "sources": study["sources"][dataset]})
    return prepared


def verify_response(response, case, context, condition):
    require(len(response["results"]) <= condition["max_results"]
            and len({(row["path"], row["symbol"]) for row in response["results"]}) == len(response["results"]), "Result count/dedup cap violated")
    for row in response["results"]:
        full = case["inventory"][row["id"]]
        require(all(row[key] == getattr(full, key) for key in ("path", "symbol", "start_line"))
                and full.start_line <= row["end_line"] <= full.end_line == row["window_end_line"], "Physical window identity/bounds changed")
        source = "".join(physical_lines(case["sources"][row["path"]])[row["start_line"] - 1:row["end_line"]])
        require(row["source"] == source and row["source_hash"] == sha(source.encode())
                and row["content_hash"] == full.content_hash
                and row["truncated"] == (source != full.text), "Literal source/hash/truncation differs")
    require(response["total_source_chars"] == sum(len(row["source"]) for row in response["results"]) <= 12000,
            "Delivered character cap differs")
    text = context.serialize_context(context.compact_result(response))
    require(len(context.encoding().encode(text, disallowed_special=())) == response["token_usage"]["tokens"] <= 4096,
            "Exact compact-token count/cap differs")
    return text


def control_barrier(prepared, context, progress=None):
    """Finish every exact control before returning permission to evaluate variants."""
    controls = []
    for case in prepared:
        response = pack(case["original"]["query"], original_order(case["original"])["scope"], case["pairs"], case["graph"], context, CONDITIONS[0])
        require_exact(response, case["original"], context)
        text = verify_response(response, case, context, CONDITIONS[0])
        controls.append({"case_id": case["case_id"], "subset": case["subset"], "compact_sha256": sha(text.encode())})
        if progress is not None:
            progress["controls_verified"] = len(controls)
    return controls


def run(root, output):
    plan = read(output / "frozen-plan.json")
    validate_run(root, output, plan)
    state = {"stage": "runtime_preflight", "controls_verified": 0, "condition_contexts_attempted": 0,
             "condition_contexts_packed": 0, "condition_contexts_verified": 0}
    marker = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
              "plan_sha256": sha((output / "frozen-plan.json").read_bytes()),
              "producer_bindings": own_bindings(root), "model_calls": 0, "retrieval_calls": 0,
              "resume_allowed": False}
    write_new(output / "run-start.json", marker)
    try:
        return run_attempt(root, output, plan, state)
    except BaseException as error:
        write_new(output / "failure.json", {"created_at_utc": datetime.now(timezone.utc).isoformat(),
                  "failed": True, "error_type": type(error).__name__, "error": str(error),
                  "run_start_sha256": sha((output / "run-start.json").read_bytes()),
                  "plan_sha256": marker["plan_sha256"], "progress": state,
                  "resume_allowed": False, "model_calls": 0, "retrieval_calls": 0})
        raise


def run_attempt(root, output, plan, state):
    runtime, rank_bytes = cache_runtime(root)
    require(runtime == plan["runtime"], "CPU runtime/tokenizer changed after freeze")
    studies, bindings, helper = load_inputs(root)
    require(bindings == plan["inputs_sha256"] and helper == plan["frozen_helpers"], "Original audited inputs/helpers changed")
    require(original_metrics(studies) == plan["original_baseline_metrics"], "Original baseline inventory changed")
    with offline_cpu_guard(), frozen_modules(root, plan, rank_bytes) as (context, Graph):
        state["stage"] = "prepare_original_sources_and_orders"
        prepared = prepare(studies, Graph)
        # Mandatory all-case control barrier: no variant computation occurs here.
        state["stage"] = "all_original_controls"
        controls = control_barrier(prepared, context, state)
        require(len(controls) == 159, "Every original control must pass before any condition computation")
        state["stage"] = "publish_control_gate"
        write_new(output / "control-gate.json", {"exact_cases": len(controls), "cases": controls,
                  "plan_sha256": sha((output / "frozen-plan.json").read_bytes()), "variant_outputs_before_gate": 0})
        outcomes = []
        state["stage"] = "compute_all_conditions"
        for case in prepared:
            original = original_order(case["original"])
            for condition in CONDITIONS:
                state["condition_contexts_attempted"] += 1
                response = pack(original["query"], original["scope"], case["pairs"], case["graph"], context, condition)
                state["condition_contexts_packed"] += 1
                text = verify_response(response, case, context, condition)
                state["condition_contexts_verified"] += 1
                ranks = evidence_ranks(response["results"], case["gold"])
                dependency_only_losses = []
                for number, group in enumerate(case["gold"], 1):
                    def covers(row):
                        return evidence_ranks([row], [group])[0] is not None
                    prior = [row for row in original["results"] if covers(row)]
                    if prior and all(row["origin"] == "dependency" for row in prior) and ranks[number - 1] is None:
                        dependency_only_losses.append({"group": number, "targets": group,
                            "original_covering_results": [row["id"] for row in prior]})
                outcomes.append({key: case[key] for key in ("subset", "dataset", "query_id", "case_id", "kind", "multi_file_and")})
                outcomes[-1].update(condition=condition["id"], response=response, compact_sha256=sha(text.encode()),
                    evidence_ranks=ranks, original_evidence_ranks=evidence_ranks(original["results"], case["gold"]),
                    dependency_only_group_losses=dependency_only_losses)
        state["stage"] = "write_replay"
        with (output / "replay.jsonl").open("x", encoding="utf-8") as stream:
            for row in outcomes:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        state["stage"] = "score_all_conditions"
        summaries = {condition["id"]: {subset: metrics([row for row in outcomes if row["condition"] == condition["id"] and row["subset"] == subset])
                        for subset in STUDIES} for condition in CONDITIONS}
        baselines = summaries[BASELINE]
        gates = {condition["id"]: gate(baselines, summaries[condition["id"]]) for condition in CONDITIONS}
        changes = []
        for row in outcomes:
            if row["kind"] == "present" and row["evidence_ranks"] != row["original_evidence_ranks"]:
                before, after = row["original_evidence_ranks"], row["evidence_ranks"]
                changes.append({key: row[key] for key in ("condition", "subset", "case_id", "dependency_only_group_losses")})
                changes[-1].update(before=before, after=after, complete_gained=None in before and None not in after,
                    complete_lost=None not in before and None in after,
                    group_gains=[i + 1 for i, (a, b) in enumerate(zip(before, after)) if a is None and b is not None],
                    group_losses=[i + 1 for i, (a, b) in enumerate(zip(before, after)) if a is not None and b is None])
        summary = {"classification": plan["classification"], "plan_sha256": sha((output / "frozen-plan.json").read_bytes()),
            "replay_sha256": sha((output / "replay.jsonl").read_bytes()), "baseline_exact_cases": len(controls),
            "conditions": CONDITIONS, "summary": summaries, "gates": gates,
            "qualifying_conditions": [key for key, item in gates.items() if item["qualified"]], "selected_winner": None,
            "changes": changes, "calls": {"model": 0, "retrieval": 0, "network": 0}, "limits": plan["limits"]}
        write_new(output / "summary.json", summary)
        state["stage"] = "write_report"
        with (output / "findings.md").open("x", encoding="utf-8") as stream:
            stream.write(render_report(summary))
        state["stage"] = "complete"
        return summary


def render_report(summary):
    text = ["# Prospective context-allocation development replay", "", summary["classification"], "",
        f"All {summary['baseline_exact_cases']} original 8+dependencies controls reproduced exact compact text and full delivered rows before variants were computed.", "",
        "| Condition | Subset | Complete positives | Multi-file complete | Covered groups | Median tokens / excerpts |",
        "|---|---|---:|---:|---:|---:|"]
    for condition, subsets in summary["summary"].items():
        for subset, result in subsets.items():
            text.append(f"| {condition} | {subset} | {result['complete']}/{result['present']} | "
                f"{result['multi_file_complete']}/{result['multi_file_queries']} | {result['covered_groups']}/{result['required_groups']} | "
                f"{result['median_tokens']:g} / {result['median_excerpts']:g} |")
    text += ["", "Qualifying conditions: " + (", ".join(summary["qualifying_conditions"]) or "none") + ". No automatic winner or product default change.", "",
        "Increasing the excerpt count trades additional fragments/metadata within the same 12,000 source characters and 4,096 compact tokens. "
        "All complete/group regressions and dependency-only losses remain in summary.json; exact sources, scope, token counts and absent inventories remain in replay.jsonl.", "",
        *["- " + item for item in summary["limits"]], ""]
    return "\n".join(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    root, output = args.root.resolve(), args.output.resolve()
    require(output.is_relative_to(root), "Prospective output must be within project for the commit gate")
    if args.freeze:
        with offline_cpu_guard():
            result = freeze(root, output)
    else:
        result = run(root, output)
    print(json.dumps({"classification": result["classification"], "output": str(output),
                      "variant_outputs": 0 if args.freeze else len(CONDITIONS) * 159}))


if __name__ == "__main__":
    main()
