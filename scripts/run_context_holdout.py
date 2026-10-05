#!/usr/bin/env python3
"""Prospectively freeze, then run the fresh Werkzeug context allocation holdout.

Imports and --freeze are CPU metadata/source work. Only --run starts an owned
native worker. No production implementation, score cache, installer or replay
substitutes for its real HTTP searches.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import contextvars
from datetime import datetime, timezone
import gzip
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import django_index_profile_core as core
import run_django_index_profile as django
from qwen_optimization_clock import ClockIntegrity, POLICY as CLOCK_POLICY
import setup_qwen_reranker as setup
import smoke_qwen_mcp as smoke
import smoke_vscode_qwen as editor

ROOT = core.ROOT
MANIFEST = ROOT / "benchmarks/werkzeug_context_queries.json"
PREPARATION = ROOT / "results/werkzeug-context-preflight-2026-10-14"
PROTOCOL = ROOT / "docs/context-holdout-runtime-protocol.md"
REVISION = "6389612fd1ee1bd93579eed5026e8fd471d04abd"
CONFIGS = [{"id": "baseline8", "max_results": 8}, {"id": "primary12", "max_results": 12},
           {"id": "diagnostic20", "max_results": 20}]
SETTINGS = {"retrieval": "hybrid", "rerank": True, "rerank_provider": "qwen", "candidates": 24,
            "source_budget": 12000, "token_budget": 4096, "include_dependencies": True}
COUNTERS = ("requests", "attempted_pairs", "completed_pairs", "attempted_forwards", "completed_forwards")
DEADLINE = "2026-10-14T13:37:05+00:00"


def write(path, value):
    core.write_new(path, value)


def bind(paths):
    return {core.artifact(path): core.sha(path) for path in paths}


def source_files():
    return [Path(__file__).resolve(), ROOT / "tests/test_context_holdout.py", PROTOCOL,
            ROOT / "scripts/django_index_profile_core.py", ROOT / "scripts/run_django_index_profile.py",
            ROOT / "scripts/qwen_optimization_clock.py", ROOT / "scripts/setup_qwen_reranker.py",
            ROOT / "scripts/smoke_qwen_mcp.py", ROOT / "scripts/smoke_vscode_qwen.py",
            ROOT / "scripts/qwen_workflow_process.py", ROOT / "scripts/qwen_workflow_evidence.py",
            ROOT / "scripts/audit_django_index_profile.py", ROOT / "scripts/audit_context_holdout.py",
            ROOT / "tests/test_audit_context_holdout.py",
            ROOT / "requirements-mac.lock", ROOT / "requirements-qwen-reranker.lock", ROOT / "pyproject.toml",
            *sorted((ROOT / "src/semantic_search").glob("*.py")), MANIFEST,
            *sorted(path for path in PREPARATION.rglob("*") if path.is_file())]


def schedule(query_ids):
    core.require(len(query_ids) == 15 and len(set(query_ids)) == 15, "Exactly fifteen distinct frozen queries required")
    rows = [{"phase": "warmup", "pass": -1, "query_id": query_ids[0], "config_id": config["id"]}
            for config in CONFIGS]
    ids = [config["id"] for config in CONFIGS]
    for number in range(3):
        order = query_ids[number:] + query_ids[:number]
        for position, query in enumerate(order):
            rotation = (2 * number + position) % 3
            for config in ids[rotation:] + ids[:rotation]:
                rows.append({"phase": "measured", "pass": number, "query_id": query, "config_id": config})
    for number, row in enumerate(rows):
        row["request_id"] = f"context-{number:03d}-{row['phase']}-{row['config_id']}-{row['query_id']}"
    return rows


def physical_lines(source):
    from semantic_search.source import physical_lines as lines
    return lines(source)


def is_multi(query):
    return len({target["path"] for group in query["target_groups"] for target in group}) > 1


def source_identity(repo, manifest):
    raw, snippets = core.capture_source(repo, manifest["source_root"], manifest["source_exclusions"])
    return {"revision": django.git(repo, "rev-parse", "HEAD").decode().strip(),
            "clean": not django.git(repo, "status", "--porcelain=v1"),
            "source_sha256": {name: core.digest(data) for name, data in raw.items()},
            "windows": [{"id": item.id, "path": item.path, "symbol": item.symbol,
                         "start_line": item.start_line, "end_line": item.end_line,
                         "source_hash": core.digest(item.text.encode())} for item in snippets],
            "unique_document_count": len({core.digest(core.document(item).encode()) for item in snippets})}, raw, snippets


def validate_gold(manifest, raw, snippets):
    from semantic_search.languages import decode_source
    queries = manifest["queries"]
    core.require(manifest["revision"] == REVISION and manifest["source_root"] == "src/werkzeug", "Fixed Werkzeug source required")
    core.require(len(queries) == 15 and len({row["id"] for row in queries}) == 15, "Fifteen unique query IDs required")
    core.require(len({row["query"] for row in queries}) == 15 and all(isinstance(row["query"], str) and row["query"].strip() for row in queries),
                 "Fifteen unique unchanged literal texts required")
    present = [row for row in queries if row["kind"] == "present"]
    absent = [row for row in queries if row["kind"] == "absent"]
    core.require(len(present) == 12 and len(absent) == 3 and sum(is_multi(row) for row in present) == 8
                 and sum(len(row["target_groups"]) for row in present) == 34
                 and all(len(group) == 1 for row in present for group in row["target_groups"]),
                 "Exact authored twelve positives, eight multi-file cases, thirty-four singleton groups and three absents required")
    for query in queries:
        groups = query.get("target_groups", [])
        core.require(bool(groups) is (query["kind"] == "present"), "Gold kind/group scope differs")
        for group in groups:
            core.require(group, "Empty required target group")
            eligible = False
            for target in group:
                path = target["path"]
                core.require(path in raw, "Gold target outside frozen source")
                low, high = target["evidence_lines"]
                core.require(type(low) is type(high) is int and 1 <= low <= high and target["definition_lines"] == [low, high],
                             "Gold requires a complete literal AST implementation span")
                source = "".join(physical_lines(decode_source(path, raw[path]))[low - 1:high])
                core.require(source and core.digest(raw[path]) == target["source_file_sha256"]
                             and core.digest(source.encode()) == target["evidence_sha256"], "Gold source/span hashes differ")
                eligible |= any(item.path == path and item.start_line <= low and item.end_line >= high for item in snippets)
            core.require(eligible, "Required full-span target group has no eligible source window")
    return {"queries": 15, "present": 12, "absent": 3, "multi_file": sum(is_multi(row) for row in present)}


def gold_gate(path, manifest_path, freeze_path, eligibility_path):
    receipt = core.read(path)
    core.require(receipt.get("checks_passed") is True and receipt.get("no_prior_outcomes") is True
                 and type(receipt.get("model_calls")) is int and receipt["model_calls"] == 0
                 and type(receipt.get("retrieval_calls")) is int and receipt["retrieval_calls"] == 0,
                 "Independent complete gold/source review with zero outcomes required")
    expected = bind([manifest_path, freeze_path, freeze_path.with_name("window-inventory.json"), eligibility_path])
    core.require(all(receipt.get("artifact_sha256", {}).get(name) == value for name, value in expected.items()),
                 "Independent gold review must bind exact manifest/source/window/eligibility bytes")
    return {**expected, **bind([path])}


def isolated_runtime(python):
    environment = python.parent.parent
    pins = setup.lock_pins(ROOT)
    checked = setup.inspect_runtime(environment, pins, root=ROOT)
    core.require(checked.get("status") == "valid" and checked.get("errors") == [], "Exact installed isolated reranker runtime required")
    site = environment / "lib/python3.12/site-packages"
    packages, sources = {}, {}
    for distribution in importlib.metadata.distributions(path=[str(site)]):
        name = setup.canonical(distribution.metadata["Name"])
        origin = distribution._path.resolve(strict=True)
        core.require(name not in packages and origin.is_relative_to(site.resolve()), "Ambiguous isolated metadata origin")
        packages[name] = {"version": distribution.version, "metadata_origin": core.artifact(origin),
                          "metadata_sha256": core.sha(origin / "METADATA"), "record_sha256": core.sha(origin / "RECORD")}
        for file in distribution.files or ():
            if str(file).endswith((".py", ".so", ".dylib", ".metallib")) and "__pycache__" not in str(file):
                source = Path(distribution.locate_file(file)).resolve(strict=True)
                core.require(source.is_relative_to(environment.resolve()), "Isolated native source escapes installed environment")
                sources[core.artifact(source)] = core.sha(source)
    core.require({name: row["version"] for name, row in packages.items()} == pins, "Isolated full package closure differs")
    return {"python_invocation": core.artifact(python), "python_binary_sha256": core.sha(python),
            "pyvenv_cfg_sha256": core.sha(environment / "pyvenv.cfg"), "package_count": len(packages),
            "packages": dict(sorted(packages.items())), "native_source_sha256": dict(sorted(sources.items()))}


def freeze(output, work, *, manifest_path, eligibility, gold_review, paths, python, checkpoint, predecessors):
    core.require(not output.exists() and not work.exists(), "Fresh output/work paths required; no overwrite or resume")
    core.require(output.is_relative_to(ROOT / "results") and work.is_relative_to(ROOT / ".cache")
                 and work != ROOT / ".cache", "Owned results/ignored-work paths required")
    core.require(manifest_path == MANIFEST and len(predecessors) >= 2 and len(set(predecessors)) == len(predecessors),
                 "Fixed gold path and declared Django/editor serialization receipts required")
    source_bindings = bind(source_files())
    revision = smoke.committed_bindings(source_bindings)
    manifest = core.read(manifest_path)
    frozen_source_path = ROOT / manifest["source_freeze"]
    frozen_source = core.read(frozen_source_path)
    core.require(core.sha(frozen_source_path) == manifest["source_freeze_sha256"], "Source-authored freeze changed")
    review = gold_gate(gold_review, manifest_path, frozen_source_path, eligibility)
    gates = editor.prerequisite_gates(paths, driver_file=ROOT / ".cache/qwen-coding-workflows-2026-10-14/driver-observed.json",
                                      python=python, checkpoint=checkpoint)
    guard = core.BlockModelImports()
    sys.meta_path.insert(0, guard)
    try:
        corpus = ROOT / frozen_source["corpus"]
        identity, raw, snippets = source_identity(corpus, manifest)
        core.require(identity["revision"] == REVISION and identity["clean"] is True
                     and identity["source_sha256"] == frozen_source["tracked_python_source_files"]
                     and identity["windows"] == core.read(PREPARATION / "window-inventory.json"), "Authored source inventory differs")
        core.require(len(raw) == 52 and len(snippets) == 1025, "Frozen source/window counts differ")
        labels = validate_gold(manifest, raw, snippets)
        main_runtime, embedding_sources = core.runtime(), core.native_sources()
        core.require(main_runtime["packages"]["logical_distribution_count"] == 98
                     and main_runtime["packages"]["physical_metadata_instance_count"] == 99, "Current qualified main package closure differs")
        isolated = isolated_runtime(python)
        native = smoke.runtime_plan(python, checkpoint)
        embedding_checkpoint = django.checkpoint_metadata(ROOT / ".cache/models/qwen3-embedding-0.6b")
    finally:
        sys.meta_path.remove(guard)
    cache = ROOT / ".cache/tokenizers"
    cache_files = bind(sorted(path for path in cache.iterdir() if path.is_file()))
    core.require(cache_files, "Offline cl100k tokenizer cache required")
    plan = {"version": 1, "kind": "fresh_source_authored_native_context_holdout", "frozen_at_utc": core.stamp()["utc"],
            "source_commit": revision, "source_sha256": source_bindings, "gold_review": review, "prerequisites": gates,
            "gate_paths": {name: core.artifact(path) for name, path in paths.items()}, "manifest": manifest,
            "source_identity": identity, "source_corpus": core.artifact(corpus), "labels": labels,
            "output": core.artifact(output), "work": core.artifact(work), "configurations": CONFIGS, "settings": SETTINGS,
            "schedule": schedule([row["id"] for row in manifest["queries"]]), "main_runtime": main_runtime,
            "isolated_runtime": isolated, "native_runtime": native, "embedding_checkpoint": embedding_checkpoint,
            "embedding_native_source_sha256": embedding_sources, "tokenizer_cache_sha256": cache_files,
            "reranker_python": core.artifact(python), "reranker_checkpoint": core.artifact(checkpoint),
            "predecessor_receipts": [core.artifact(path) for path in predecessors], "deadline_utc": DEADLINE,
            "worker_timeout_s": 2400, "clock_policy": CLOCK_POLICY, "model_calls_during_freeze": 0,
            "blocked_native_imports_during_freeze": guard.blocked,
            "expected_native": {"generic_owner_warmup": 1, "http_warmup_queries": 3, "http_warmup_forwards": 72,
                                "measured_http_queries": 135, "measured_query_embeddings": 135, "measured_forwards": 3240,
                                "total_query_embeddings": 138, "total_reranker_forwards": 3313},
            "claim_policy": "primary12 must pass every measured pass; diagnostic20 cannot rescue it; no default change",
            "instrumentation": "Pool/pair/token evidence is recorded inside instrumented HTTP work; source, parity, runtime and disk checks are outside measured queries."}
    output.mkdir(parents=True, exist_ok=False)
    write(output / "frozen-plan.json", plan)
    return plan


@contextmanager
def loopback_only():
    original_connect, original_ex, original_dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    def address(value):
        core.require(isinstance(value, tuple) and len(value) >= 2 and value[0] in {"127.0.0.1", "localhost", "::1"},
                     "External network forbidden during native holdout")
    def connect(stream, value):
        address(value)
        return original_connect(stream, value)
    def connect_ex(stream, value):
        address(value)
        return original_ex(stream, value)
    def dns(host, *args, **kwargs):
        core.require(host in {"127.0.0.1", "localhost", "::1"}, "External DNS forbidden during native holdout")
        return original_dns(host, *args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError("UDP network forbidden during native holdout")
    with patch.object(socket.socket, "connect", connect), patch.object(socket.socket, "connect_ex", connect_ex), \
            patch.object(socket, "getaddrinfo", dns), patch.object(socket.socket, "sendto", forbidden):
        yield


def full_source_proof(response, repo, query, maximum):
    from semantic_search.context import compact_result, encoding, serialize_context
    from semantic_search.languages import decode_source
    core.require(response["query"] == query and response["retrieval"] == "hybrid" and response["rerank"] is True
                 and response["rerank_provider"] == "qwen" and response["reranker_batch_size"] == 1
                 and response["retrieval_candidate_windows"] == 50 and response["candidate_windows"] == 24,
                 "Actual HTTP provider/full50/prefix24 differs")
    core.require(len(response["results"]) <= maximum, "Context result cap exceeded")
    chars = 0
    for row in response["results"]:
        path = (repo / row["path"]).resolve(strict=True)
        core.require(path.is_relative_to(repo.resolve()) and row["path"].startswith("src/werkzeug/"), "Delivered source escapes frozen scope")
        low, high = row["start_line"], row["end_line"]
        core.require(type(low) is type(high) is int and 1 <= low <= high, "Strict source locations required")
        source = "".join(physical_lines(decode_source(row["path"], path.read_bytes()))[low - 1:high])
        core.require(source == row["source"] and core.digest(source.encode()) == row["source_hash"], "Delivered source bytes/location hash differ")
        chars += len(source)
    compact = compact_result(response)
    text = serialize_context(compact)
    tokens = len(encoding().encode(text, disallowed_special=()))
    usage = response["token_usage"]
    core.require(chars == response["total_source_chars"] <= 12000 and usage["encoding"] == "cl100k_base"
                 and usage["tokens"] == tokens <= 4096 and usage["budget"] == 4096, "Actual compact token/source accounting differs")
    return {"payload_tokens": tokens, "source_characters": chars, "payload_sha256": core.digest(text.encode()),
            "delivered_source_exact": True}


def evidence(query, results):
    if query["kind"] == "absent":
        return {"kind": "absent", "group_ranks": [], "complete": None, "multi_file": False}
    ranks = []
    for group in query["target_groups"]:
        found = None
        for rank, row in enumerate(results, 1):
            for target in group:
                low, high = target["evidence_lines"]
                if row["path"] == target["path"] and row["start_line"] <= low and row["end_line"] >= high:
                    source = "".join(physical_lines(row["source"])[low - row["start_line"]:high - row["start_line"] + 1])
                    if core.digest(source.encode()) == target["evidence_sha256"]:
                        found = rank
                        break
            if found is not None:
                break
        ranks.append(found)
    return {"kind": "present", "group_ranks": ranks, "complete": None not in ranks, "multi_file": is_multi(query)}


def aggregate(rows):
    positives = [row for row in rows if row["evidence"]["kind"] == "present"]
    return {"complete_case_ids": sorted(row["query_id"] for row in positives if row["evidence"]["complete"]),
            "covered_groups": sum(sum(rank is not None for rank in row["evidence"]["group_ranks"]) for row in positives),
            "required_groups": sum(len(row["evidence"]["group_ranks"]) for row in positives),
            "multi_file_complete": sum(row["evidence"]["complete"] and row["evidence"]["multi_file"] for row in positives),
            "absent_cases": len(rows) - len(positives)}


def primary_gate(rows):
    measured = [row for row in rows if row["phase"] == "measured"]
    core.require(len(measured) == 135 and len({(row["pass"], row["config_id"], row["query_id"]) for row in measured}) == 135,
                 "Complete unique three-pass/three-config holdout required")
    cases = None
    for number in range(3):
        for config in CONFIGS:
            selected = [row for row in measured if row["pass"] == number and row["config_id"] == config["id"]]
            core.require(len(selected) == 15 and sum(row["evidence"]["kind"] == "present" for row in selected) == 12,
                         "Each pass/config must contain twelve positives and three absents")
            identities = {row["query_id"] for row in selected}
            cases = identities if cases is None else cases
            core.require(identities == cases, "Pass/config query inventory differs")
    passes = []
    for number in range(3):
        selected = [row for row in rows if row["phase"] == "measured" and row["pass"] == number]
        metrics = {config["id"]: aggregate([row for row in selected if row["config_id"] == config["id"]]) for config in CONFIGS}
        baseline, primary = metrics["baseline8"], metrics["primary12"]
        comparisons = {"baseline_complete_retained": set(baseline["complete_case_ids"]).issubset(primary["complete_case_ids"]),
                       "group_coverage_floor": primary["covered_groups"] >= baseline["covered_groups"],
                       "multi_file_floor": primary["multi_file_complete"] >= baseline["multi_file_complete"],
                       "strict_complete_gain": len(primary["complete_case_ids"]) > len(baseline["complete_case_ids"])}
        passes.append({"pass": number, "metrics": metrics, "comparisons": comparisons,
                       "primary12_passed": all(comparisons.values())})
    return {"primary12_passed": all(row["primary12_passed"] for row in passes), "passes": passes,
            "diagnostic20_can_rescue_primary12": False, "default_changed": False}


def native_delta(before, after, pairs=24):
    core.require(set(before) == set(after) == set(COUNTERS), "Exact native counter schema required")
    delta = {}
    for field in COUNTERS:
        core.require(type(before[field]) is type(after[field]) is int and before[field] >= 0 and after[field] >= before[field],
                     "Strict monotonic native counters required")
        delta[field] = after[field] - before[field]
    core.require(all(value == pairs for value in delta.values()), "HTTP call did not execute exactly twenty-four real B1 forwards")
    return delta


class ObservedHybrid:
    def __init__(self, inner, active):
        self.inner, self.active = inner, active

    def search(self, query, vector, limit):
        pairs = self.inner.search(query, vector, limit)
        row = self.active.get()
        core.require(row is not None and "full_pool" not in row and limit == 50 and len(pairs) == 50, "One actual full50 hybrid pool required")
        core.require(vector.dtype.name == "float32" and vector.ndim == 1, "Original float32 query vector required")
        row["query_vector"] = {"dtype": "float32", "shape": list(vector.shape), "values": vector.tolist(),
                               "raw_sha256": core.digest(vector.tobytes())}
        row["full_pool"] = [{"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
                             "start_line": snippet.start_line, "end_line": snippet.end_line,
                             "source_hash": core.digest(snippet.text.encode()), "source": snippet.text,
                             "retrieval_score": float(score)} for snippet, score in pairs]
        return pairs


class ObservedScorer:
    def __init__(self, inner, active):
        self.inner, self.active = inner, active

    @property
    def safe_for_embedding(self):
        return self.inner.safe_for_embedding

    def score(self, query, snippet, *, request_id):
        row = self.active.get()
        core.require(row is not None, "Native scoring must belong to an actual HTTP request")
        literal = {"request_id": request_id, "query": query, "source": snippet.text, "snippet_id": snippet.id,
                   "query_sha256": core.digest(query.encode()), "source_sha256": core.digest(snippet.text.encode()), "status": "started"}
        row.setdefault("literal_pairs", []).append(literal)
        try:
            judgment = self.inner.score(query, snippet, request_id=request_id)
            literal.update(status="completed", receipt=judgment.response)
            return judgment
        except BaseException as error:
            literal.update(status="failed", error_type=type(error).__name__, error=str(error))
            raise


def verify_pairs(record, response, builder):
    pairs = record["literal_pairs"]
    core.require(len(pairs) == len(response["reranker_receipts"]) == 24
                 and [row["snippet_id"] for row in pairs] == [row["id"] for row in record["full_pool"][:24]],
                 "Actual reranker literal pairs do not match the full50 prefix")
    for position, pair in enumerate(pairs):
        source_row = record["full_pool"][position]
        core.require(pair["query"] == response["query"] and pair["source"] == source_row["source"]
                     and pair["source_sha256"] == source_row["source_hash"] == core.digest(pair["source"].encode()),
                     "Actual literal source/query differs from captured retrieval prefix")
        built = builder.build(pair["query"], pair["source"])
        receipt = pair["receipt"]
        core.require(pair["status"] == "completed" and receipt == response["reranker_receipts"][pair["snippet_id"]]
                     and receipt["request_id"] == pair["request_id"] and receipt["batch_size"] == 1, "Actual scalar receipt binding differs")
        for field in ("query_sha256", "source_sha256", "delivered_sha256", "source_lines", "delivered_lines",
                      "source_utf8_bytes", "delivered_utf8_bytes", "truncated", "total_tokens"):
            core.require(receipt["result"][field] == built[field], "Actual literal tokenizer/scorer receipt differs: " + field)
        pair["input_ids"] = built["input_ids"]
        pair["input_ids_sha256"] = core.digest(json.dumps(built["input_ids"], separators=(",", ":")).encode())


def verify_native_ledger(raw, records, counts):
    rows = [json.loads(line) for line in raw.splitlines()]
    core.require(len(rows) == 6626 and [row.get("event") for row in rows] == [event for _ in range(3313) for event in ("started", "completed")],
                 "Exactly6626 alternating synchronous native start/completion rows required")
    started = [row for row in rows if row["event"] == "started"]
    complete = [row for row in rows if row["event"] == "completed"]
    core.require(len(started) == len(complete) == 3313 and not any(row["event"] == "failed" for row in rows), "Complete actual warmup/search native ledger required")
    core.require(all(type(counts.get(name)) is int and counts[name] == 3313 for name in COUNTERS), "Actual final native counts differ")
    core.require(sum(row["warmup"] is True for row in complete) == 1 and complete[0]["request_id"] == "generic-warmup", "Generic startup warmup differs")
    pairs = [pair for row in records for pair in row["literal_pairs"]]
    measured = complete[1:]
    core.require(len(pairs) == len(measured) == 3312, "Actual HTTP warmup/measured pair ledger count differs")
    for number, (start, end) in enumerate(zip(started, complete, strict=True), 1):
        core.require(type(start["forward_number"]) is type(end["forward_number"]) is int
                     and start["forward_number"] == end["forward_number"] == number and start["request_id"] == end["request_id"], "Native forward start/completion order differs")
        core.require(all(start[field] == end[field] for field in ("warmup", "batch_size", "source_sha256", "query_sha256", "delivered_sha256",
                                                                 "total_tokens", "input_ids_sha256")), "Native forward start/completion input identity differs")
    for end, pair in zip(measured, pairs, strict=True):
        receipt = pair["receipt"]["result"]
        core.require(all(type(token) is int and token >= 0 for token in pair["input_ids"])
                     and core.digest(json.dumps(pair["input_ids"], separators=(",", ":")).encode()) == pair["input_ids_sha256"],
                     "Recorded literal token IDs differ from their hash")
        core.require(type(end["batch_size"]) is int and end["batch_size"] == 1 and end["warmup"] is False and end["request_id"] == pair["request_id"]
                     and end["input_ids_sha256"] == pair["input_ids_sha256"], "Native B1 actual input IDs/hash differ")
        for field in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens"):
            core.require(end[field] == receipt[field], "Native source/token receipt differs")
        core.require(end["score"] == {field: receipt[field] for field in ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")},
                     "Native observed logits differ from HTTP receipts")
    return {"started": len(started), "completed": len(complete), "generic_warmup": 1, "http_warmup": 72,
            "measured": 3240, "raw_sha256": core.digest(raw)}


def verify_bound_environment(plan, *, weights=False):
    for values in (plan["source_sha256"], plan["gold_review"], plan["tokenizer_cache_sha256"], plan["embedding_native_source_sha256"], *plan["prerequisites"].values()):
        django.verify_hashes(values)
    core.require(core.runtime() == plan["main_runtime"], "Main physical package/Python runtime drift")
    python, checkpoint = core.resolve(plan["reranker_python"]), core.resolve(plan["reranker_checkpoint"])
    core.require(isolated_runtime(python) == plan["isolated_runtime"] and smoke.runtime_plan(python, checkpoint) == plan["native_runtime"],
                 "Isolated native runtime drift")
    identity, _, _ = source_identity(core.resolve(plan["source_corpus"]), plan["manifest"])
    core.require(identity == plan["source_identity"], "Original fixed source changed")
    if weights:
        django.verify_checkpoint(plan["embedding_checkpoint"])


def serial_gate(paths):
    receipts = []
    for path in paths:
        record = core.read(path)
        bound = {core.artifact(path): core.sha(path)}
        if path.name == "parent-result.json":
            worker_path, plan_path = path.parent / "worker-result.json", path.parent / "frozen-plan.json"
            core.require(record.get("complete") is True and type(record.get("exit_code")) is int
                         and record["exit_code"] in (0, 1) and record.get("worker_result_sha256") == core.sha(worker_path)
                         and record.get("frozen_plan_sha256") == core.sha(plan_path), "Terminal bound Django predecessor required")
            stopped, result = record["cleanup"], core.read(worker_path)
            core.require(stopped.get("owned_group_stopped") is True and stopped.get("forced_kill") is False
                         and stopped.get("errors") == [] and stopped.get("exit_code") == record["exit_code"]
                         and result.get("cleanup", {}).get("model_released") is True
                         and result["cleanup"].get("index_closed") is True and not result.get("cleanup_error")
                         and not result.get("cleanup_signals", {}).get("received"), "Django native cleanup is unproved or forced")
            owners = [stopped["pid"]]
            core.require(record["ownership"]["pid"] == record["ownership"]["owned_group"] == owners[0]
                         and result["pid"] == owners[0], "Django actual owned process identities differ")
            bound.update(bind([worker_path, plan_path]))
        elif path.name == "verification.json":
            result_path, plan_path, journal = path.parent / "results.json", path.parent / "frozen-plan.json", path.parent / "signals.jsonl"
            result = core.read(result_path)
            core.require(record.get("publication_rule") == "Empty actual signals.jsonl with exact artifact hash required"
                         and journal.is_file() and journal.read_bytes() == b""
                         and record.get("artifact_sha256", {}).get("signals.jsonl") == core.digest(b"")
                         and record["artifact_sha256"].get("results.json") == core.sha(result_path), "Bound terminal editor publication/signals required")
            for name, expected in record["artifact_sha256"].items():
                core.require(Path(name).name == name and core.sha(path.parent / name) == expected, "Editor predecessor artifact drift")
            core.require(smoke.clean_lifecycle(result.get("owner", {}), result.get("native_final_status"), result.get("http_thread_stopped"))
                         and result.get("actual_owner_group_gone") is True and result.get("actual_auth_token_absent") is True
                         and result.get("actual_owner_listener_closed") is True and result.get("actual_http_listener_closed") is True
                         and result.get("editor_cleanup", {}).get("cleanup_clean") is True
                         and result["editor_cleanup"].get("owned_group_stopped") is True
                         and result["editor_cleanup"].get("forced_kill") is False
                         and result.get("launcher_cleanup", {}).get("owned_group_stopped") is True
                         and not result["launcher_cleanup"].get("forced_kill") and not result["launcher_cleanup"].get("errors")
                         and not result.get("cleanup_errors"), "Editor native/launcher/detached cleanup is unproved or forced")
            native_url = result["owner"]["ready"]["url"]
            command = result["host_command"]
            core.require(command.count("--url") == 1 and editor.listener_closed(native_url)
                         and editor.listener_closed(command[command.index("--url") + 1]), "Prior editor/native listener is still active")
            owners = [result["owner"]["owned_group"], result["editor_cleanup"]["owned_group"], result["launcher_cleanup"]["pid"]]
            bound.update(bind([result_path, plan_path, journal]))
        else:
            raise ValueError("Unknown predecessor receipt schema")
        for pid in owners:
            core.require(type(pid) is int and pid > 1, "Strict owned predecessor identity required")
            for action in (os.kill, os.killpg):
                try:
                    action(pid, 0)
                except ProcessLookupError:
                    continue
                raise ValueError("Prior native process/group is still present or unproved")
        receipts.append({"path": core.artifact(path), "sha256": core.sha(path), "known_groups_gone": owners,
                         "artifact_sha256": bound, "prior_checks_passed": record.get("checks_passed"),
                         "classification": "prior outcome passed; clean stopped" if record.get("checks_passed") is True
                         else "prior outcome failed; terminal cleanup independently rechecked and clean"})
    return receipts


def check_deadline(plan):
    core.require(datetime.now(timezone.utc) < datetime.fromisoformat(plan["deadline_utc"]), "Authorized work deadline reached; preserve partial attempt")


def validate_ownership(proof, owner_pid, *, pid=None, pgid=None, argv=None):
    pid = os.getpid() if pid is None else pid
    pgid = os.getpgid(0) if pgid is None else pgid
    argv = sys.orig_argv if argv is None else argv
    core.require(type(owner_pid) is int and owner_pid > 1 and proof["pid"] == proof["owned_group"] == pid == pgid
                 and proof["owner_pid"] == owner_pid and proof["start_new_session"] is True
                 and proof["command"] == argv and proof["command_sha256"] == core.digest(json.dumps(argv).encode()),
                 "Exact native worker parent/PID/PGID/argv proof differs")


def worker(output, owner_pid):
    from semantic_search.backend import SearchBackend
    from semantic_search.embeddings import QwenEmbedder
    from semantic_search.qwen_reranker import QwenRerankerProcess
    from semantic_search.qwen_reranker_core import PairBuilder
    from semantic_search.server import create_server
    from semantic_search.client import SearchClient
    from tokenizers import Tokenizer

    plan = core.read(output / "frozen-plan.json")
    work = core.resolve(plan["work"])
    result = {"checks_passed": False, "complete": False, "started": core.stamp(), "native_inference": True,
              "pid": os.getpid(), "parent_pid": os.getppid(), "model_calls_in_parent": 0}
    owner = backend = embedder = server = thread = journal = None
    records, attempts = [], []
    clock, active = ClockIntegrity(), contextvars.ContextVar("context-holdout-request", default=None)
    watcher_stop = threading.Event()
    try:
        with core.Signals() as signals, loopback_only():
            result["signals"] = signals.receipt
            core.require(os.getppid() == owner_pid and owner_pid > 1 and os.getpgid(0) == os.getpid(), "Native worker parent/session differs")
            started_wait = time.monotonic()
            while not (work / "owned-process.json").is_file() and time.monotonic() - started_wait < 5:
                time.sleep(.02)
            ownership = core.read(work / "owned-process.json")
            validate_ownership(ownership, owner_pid)
            result["ownership"] = ownership
            result["ownership_proof_sha256"] = core.sha(work / "owned-process.json")
            def watch_parent():
                while not watcher_stop.wait(.5):
                    if os.getppid() != owner_pid:
                        os.kill(os.getpid(), signal.SIGTERM)
                        return
            threading.Thread(target=watch_parent, daemon=True).start()
            os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TIKTOKEN_CACHE_DIR=str(ROOT / ".cache/tokenizers"))
            check_deadline(plan)
            result["serial_predecessors"] = serial_gate([core.resolve(path) for path in plan["predecessor_receipts"]])
            verify_bound_environment(plan, weights=True)
            result["power_start"] = core.power()
            core.require(result["power_start"]["ac_power"] is True, "AC power required for timing qualification")
            result["clock_baseline"] = clock.baseline
            repo = work / "corpus"
            django.git(ROOT, "clone", "--no-hardlinks", str(core.resolve(plan["source_corpus"])), str(repo))
            django.git(repo, "checkout", "--detach", REVISION)
            identity, raw, snippets = source_identity(repo, plan["manifest"])
            core.require(identity == plan["source_identity"], "New execution copy differs from authored source")
            database = work / "index.sqlite3"
            core.require(not database.exists(), "Genuine first index requires absent database")
            journal = core.Journal(work / "embedding-events.jsonl")
            Recorded = core.recording_embedder_class(QwenEmbedder)
            embedder = Recorded(model_path=core.resolve(plan["embedding_checkpoint"]["path"]), batch_size=4,
                                progress=True, journal=journal, work=work)
            embedder.allowed_native_sources = plan["embedding_native_source_sha256"]
            core.require(embedder.metadata == plan["native_runtime"]["embedding"], "Actual original embedding metadata differs from freeze")
            embedder.bind_snippets(snippets)
            embedder.bind_queries(plan["manifest"]["queries"])
            owner = QwenRerankerProcess(python=core.resolve(plan["reranker_python"]),
                                        checkpoint=core.resolve(plan["reranker_checkpoint"]), log_dir=work / "qwen")
            start = core.stamp()
            owner.start()
            result["reranker_startup"] = {"start": start, "end": core.stamp(), "status": owner.status()}
            core.require(all(value == 1 for value in result["reranker_startup"]["status"]["counts"].values()), "Exactly one real generic B1 startup warmup required")
            scorer = ObservedScorer(owner.client, active)

            class ObservedBackend(SearchBackend):
                def search(self, query, **settings):
                    before = dict(embedder.counts)
                    row = {"request_id": settings["request_id"], "query": query, "settings": settings,
                           "embedding_before": before, "status": "started"}
                    attempts.append(row)
                    token = active.set(row)
                    try:
                        response = super().search(query, **settings)
                        row.update(status="completed", embedding_after=dict(embedder.counts))
                        return response
                    except BaseException as error:
                        row.update(status="failed", error_type=type(error).__name__, error=str(error), embedding_after=dict(embedder.counts))
                        raise
                    finally:
                        active.reset(token)

            backend = ObservedBackend(repo, plan["manifest"]["source_root"], database, embedder=embedder,
                                      qwen_client=scorer, source_exclusions=plan["manifest"]["source_exclusions"])
            index = backend._get_index()
            connection = sqlite3.connect(database)
            try:
                result["first_empty_database"] = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                                                  for name in ("embeddings", "snippets", "active_vectors", "snippets_fts")}
            finally:
                connection.close()
            core.require(all(value == 0 for value in result["first_empty_database"].values()), "First schema contains prior vectors/source")
            result["first_empty_snapshot"] = django.sqlite_snapshot(database, work / "initial-empty.sqlite3")
            before = clock.check("index-before")["sample"]
            result["indexstats"] = backend.refresh()
            result["index_timing"] = clock.check("index-after", since=before)
            result["initial_embedding_counts"] = dict(embedder.counts)
            core.require(embedder.counts["document_inputs"] == identity["unique_document_count"]
                         and result["indexstats"]["indexed_windows"] == len(snippets)
                         and embedder.counts["attempted_forwards"] == embedder.counts["completed_forwards"] == (identity["unique_document_count"] + 3) // 4,
                         "Initial genuine document inference/native batch count differs")
            result["initial_store"] = core.inspect_database(index, snippets, embedder.cache_vectors, embedder.metadata)
            result["initial_database_snapshot"] = django.sqlite_snapshot(database, work / "initial-index.sqlite3")
            source_rows = [{**row, "source": snippet.text, "document_sha256": core.digest(core.document(snippet).encode())}
                           for row, snippet in zip(identity["windows"], snippets, strict=True)]
            source_jsonl = b"".join((json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode() for row in source_rows)
            (output / "source-content.jsonl.gz").write_bytes(gzip.compress(source_jsonl, mtime=0))
            backend._hybrid = ObservedHybrid(backend._hybrid, active)
            server = create_server(backend, port=0)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05})
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}"
            result["http_url"] = url
            client = SearchClient(url)
            builder = PairBuilder(Tokenizer.from_file(str(core.resolve(plan["reranker_checkpoint"]) / "tokenizer.json")))
            queries = {row["id"]: row for row in plan["manifest"]["queries"]}
            configs = {row["id"]: row for row in CONFIGS}
            clock.check("native-model-index-and-http-startup-complete")
            with (work / "searches.jsonl").open("x") as stream:
                for expected in plan["schedule"]:
                    check_deadline(plan)
                    row = {**expected, "status": "started"}
                    records.append(row)
                    query = queries[row["query_id"]]
                    settings = {**SETTINGS, "max_results": configs[row["config_id"]]["max_results"], "request_id": row["request_id"]}
                    native_before = owner.status()["counts"]
                    before = clock.check(row["request_id"] + "-before")["sample"]
                    try:
                        response = client.search(query["query"], **settings)
                        row["response"] = response
                        end = clock.check(row["request_id"] + "-after", since=before)
                        row["clock"] = end
                        row["http_latency_s"] = end["interval"]["perf_elapsed_ns"] / 1e9
                        row["native_before"], row["native_after"] = native_before, owner.status()["counts"]
                        row["native_delta"] = native_delta(row["native_before"], row["native_after"])
                        attempt = attempts[-1]
                        core.require(attempt["request_id"] == row["request_id"] and attempt["status"] == "completed", "Actual HTTP/backend request identity differs")
                        delta = {key: attempt["embedding_after"][key] - attempt["embedding_before"][key] for key in embedder.counts}
                        core.require(delta == {"document_inputs": 0, "query_inputs": 1, "attempted_forwards": 1, "completed_forwards": 1},
                                     "Actual HTTP request must execute one query embedding and no document inference")
                        verify_pairs(attempt, response, builder)
                        row["source_proof"] = full_source_proof(response, repo, query["query"], settings["max_results"])
                        row["evidence"] = evidence(query, response["results"])
                        core.require(attempt["query_vector"]["raw_sha256"] == embedder.operations[-1]["matrix_raw_sha256"],
                                     "Actual hybrid query vector differs from the completed native query operation")
                        row.update(full_pool=attempt["full_pool"], query_vector=attempt["query_vector"],
                                   literal_pairs=attempt["literal_pairs"], embedding_delta=delta, status="completed")
                    except BaseException as error:
                        row.update(status="failed", error_type=type(error).__name__, error=str(error))
                        raise
                    finally:
                        stream.write(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
                        stream.flush()
                    if row["query_id"] == plan["manifest"]["queries"][-1]["id"]:
                        power = core.power()
                        core.require(power["ac_power"] is True, "AC power lost during timed study")
                    clock.check(row["request_id"] + "-checks-complete")
            core.require(len(records) == 138 and all(row["status"] == "completed" for row in records), "Every frozen warmup/measured HTTP call required")
            result["native_final_status"] = owner.status()
            native_raw = (owner.directory / "forwards.jsonl").read_bytes()
            result["native_ledger"] = verify_native_ledger(native_raw, records, result["native_final_status"]["counts"])
            result["embedding_final_counts"] = dict(embedder.counts)
            core.require(embedder.counts["query_inputs"] == 138
                         and embedder.counts["attempted_forwards"] == embedder.counts["completed_forwards"] == (identity["unique_document_count"] + 3) // 4 + 138
                         and [row["query_id"] for row in embedder.operations if row["kind"] == "query"] == [row["query_id"] for row in plan["schedule"]],
                         "Exact actual document/query/native embedding schedule required")
            result["quality_gate"] = primary_gate(records)
            result["latency"] = {row["id"]: core.distribution([sample["http_latency_s"] for sample in records
                                 if sample["phase"] == "measured" and sample["config_id"] == row["id"]]) for row in CONFIGS}
            final_identity, _, _ = source_identity(repo, plan["manifest"])
            core.require(final_identity == plan["source_identity"], "Execution source mutated during queries")
            result["final_store"] = core.inspect_database(index, snippets, embedder.cache_vectors, embedder.metadata)
            result["disk_live"] = {"observation_phase": "worker resident before model/index cleanup and process exit", **core.disk(database)}
            result["power_end"], result["sleep_events"] = core.power(), core.sleep_events(result["started"]["utc"])
            core.require(result["power_end"]["ac_power"] is True and not result["sleep_events"]["events"], "Power/suspend timing qualification failed")
            verify_bound_environment(plan, weights=True)
            clock.check("final-runtime-and-source-checks-complete")
            result.update(complete=True, checks_passed=True)
    except BaseException as error:
        result["failure"] = {"error_type": type(error).__name__, "error": str(error)}
        if hasattr(error, "receipt"):
            result["failure"]["receipt"] = error.receipt
    finally:
        watcher_stop.set()
        with core.Signals() as cleanup_signals:
            result["cleanup_signals"] = cleanup_signals.receipt
            with cleanup_signals.defer(raise_pending=False):
                errors = []
                def cleanup(name, action):
                    try:
                        return action()
                    except BaseException as error:
                        errors.append({"action": name, "error_type": type(error).__name__, "error": str(error)})
                if server is not None:
                    if thread is not None and thread.is_alive():
                        cleanup("http_shutdown", server.shutdown)
                    cleanup("http_close", server.server_close)
                    if thread is not None:
                        cleanup("http_join", lambda: thread.join(timeout=5))
                    result["http_thread_stopped"] = thread is None or not thread.is_alive()
                    result["http_listener_closed"] = cleanup("http_listener", lambda: editor.listener_closed(f"http://127.0.0.1:{server.server_address[1]}"))
                elif backend is not None:
                    cleanup("backend_close", backend.close)
                elif embedder is not None:
                    cleanup("embedding_close", embedder.close)
                if owner is not None:
                    cleanup("reranker_close", owner.close)
                    result["owner"] = owner.receipt
                    if owner.directory is not None and (owner.directory / "forwards.jsonl").is_file():
                        final_raw = (owner.directory / "forwards.jsonl").read_bytes()
                        (output / "native-forwards.jsonl.gz").write_bytes(gzip.compress(final_raw, mtime=0))
                        result["native_private_public_sha256"] = core.digest(final_raw)
                        result["native_private_public_matches_verified"] = core.digest(final_raw) == result.get("native_ledger", {}).get("raw_sha256")
                    result["auth_token_absent"] = owner.directory is not None and not (owner.directory / "token.secret").exists()
                    result["reranker_group_gone"] = cleanup("reranker_group", lambda: editor.group_gone(owner.receipt["owned_group"]))
                    result["reranker_listener_closed"] = cleanup("reranker_listener", lambda: editor.listener_closed(owner.receipt["ready"]["url"])) if owner.receipt.get("ready") else False
                if journal is not None:
                    cleanup("embedding_journal_close", journal.close)
                    cleanup("public_embedding_journal", lambda: core.compress_journal(work / "embedding-events.jsonl", output / "embedding-events.jsonl.gz"))
                if (work / "searches.jsonl").exists():
                    cleanup("public_searches", lambda: core.compress_journal(work / "searches.jsonl", output / "searches.jsonl.gz"))
                attempt_path = work / "actual-http-attempts.jsonl"
                with attempt_path.open("x") as stream:
                    for row in attempts:
                        stream.write(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
                cleanup("public_http_attempts", lambda: core.compress_journal(attempt_path, output / "actual-http-attempts.jsonl.gz"))
                result["cleanup_errors"] = errors
                result["lifecycle_passed"] = owner is not None and smoke.clean_lifecycle(owner.receipt, result.get("native_final_status"), result.get("http_thread_stopped"))
                result["embedding_model_released"] = embedder is None or (embedder._model is None and embedder._mx is None)
                if embedder is not None:
                    result["embedding_operations"] = embedder.operations
                    result["embedding_initialization"] = embedder.initialization
                result["checks_passed"] = bool(result["checks_passed"] and result["lifecycle_passed"]
                    and result["embedding_model_released"]
                    and result.get("auth_token_absent") is True and result.get("reranker_group_gone") is True
                    and result.get("reranker_listener_closed") is True and result.get("http_listener_closed") is True
                    and result.get("native_private_public_matches_verified") is True
                    and not result.get("signals", {}).get("received") and not cleanup_signals.receipt["received"] and not errors)
        result["finished"] = core.stamp()
        write(output / "worker-result.json", core.public(result))
    return 0 if result["checks_passed"] else 1


def run(output):
    plan_path = output / "frozen-plan.json"
    plan = core.read(plan_path)
    smoke.committed_bindings({core.artifact(plan_path): core.sha(plan_path), **plan["source_sha256"]})
    core.require(core.artifact(output) == plan["output"] and set(path.name for path in output.iterdir()) == {"frozen-plan.json"}, "Fresh exclusive plan output required")
    verify_bound_environment(plan)
    predecessors = serial_gate([core.resolve(path) for path in plan["predecessor_receipts"]])
    check_deadline(plan)
    work = core.resolve(plan["work"])
    core.require(not work.exists(), "No native attempt retry/resume or overwrite")
    result = {"checks_passed": False, "complete": False, "started": core.stamp(), "model_calls_in_parent": 0,
              "frozen_plan_sha256": core.sha(plan_path), "serial_predecessors": predecessors}
    child, claimed = None, False
    with core.Signals() as signals:
        result["signals"] = signals.receipt
        try:
            with signals.defer():
                write(output / "run-start.json", result)
                claimed = True
                work.mkdir(parents=True, exist_ok=False, mode=0o700)
                command = [sys.executable, "-B", str(Path(__file__).resolve()), "--_worker", str(output), "--owner-pid", str(os.getpid())]
                with (work / "worker.log").open("xb") as log:
                    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                ownership = {"pid": child.pid, "owned_group": child.pid, "owner_pid": os.getpid(), "command": command,
                             "command_sha256": core.digest(json.dumps(command).encode()), "start_new_session": True, "observed": core.stamp()}
                result["ownership"] = ownership
                write(work / "owned-process.json", ownership)
            limit = min(plan["worker_timeout_s"], max(.01, (datetime.fromisoformat(plan["deadline_utc"]) - datetime.now(timezone.utc)).total_seconds()))
            result["actual_wait_timeout_s"] = limit
            result["exit_code"] = child.wait(timeout=limit)
            result["complete"] = True
        except BaseException as error:
            result["failure"] = {"error_type": type(error).__name__, "error": str(error)}
        finally:
            with signals.defer(raise_pending=False):
                result["cleanup"] = core.stop_owned(child) if child is not None else {"owned_group_stopped": True, "launched": False}
                if (output / "worker-result.json").is_file():
                    try:
                        observed = core.read(output / "worker-result.json")
                        result["worker_result_sha256"] = core.sha(output / "worker-result.json")
                        result["worker_checks_passed"] = observed.get("checks_passed")
                    except (ValueError, OSError) as error:
                        result["worker_evidence_error"] = str(error)
                stopped = result["cleanup"]
                if stopped.get("owned_group_stopped") is True and (work / "index.sqlite3").is_file():
                    result["postexit_index_physical"] = {"observation_phase": "parent after native worker exit and owned group disappearance",
                        "disk": core.disk(work / "index.sqlite3"),
                        "file_sha256": bind([path for path in (work / "index.sqlite3", work / "index.sqlite3-wal", work / "index.sqlite3-shm") if path.is_file()])}
                result["checks_passed"] = bool(result["complete"] and result.get("exit_code") == 0 and result.get("worker_checks_passed") is True
                    and stopped.get("owned_group_stopped") is True and not stopped.get("forced_kill") and not stopped.get("errors") and not signals.receipt["received"])
                result["finished"] = core.stamp()
    if claimed:
        write(output / "parent-result.json", core.public(result))
    return 0 if result["checks_passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--_worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--owner-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--eligibility", type=Path)
    parser.add_argument("--gold-review", type=Path)
    parser.add_argument("--coding-audit", type=Path)
    parser.add_argument("--setup-receipt", type=Path)
    parser.add_argument("--parity-audit", type=Path)
    parser.add_argument("--mcp-audit", type=Path)
    parser.add_argument("--serial-receipt", type=Path, action="append", default=[])
    parser.add_argument("--reranker-python", type=Path, default=editor.DEFAULT_PYTHON)
    parser.add_argument("--checkpoint", type=Path, default=editor.DEFAULT_CHECKPOINT)
    args = parser.parse_args(argv)
    if args._worker:
        return worker(args._worker.resolve(), args.owner_pid)
    core.require(args.output is not None, "Explicit result output required")
    output = args.output.resolve()
    if args.freeze:
        values = [args.work, args.eligibility, args.gold_review, args.coding_audit, args.setup_receipt, args.parity_audit, args.mcp_audit]
        core.require(all(value is not None for value in values), "Work, gold review/eligibility and all four completed prerequisite certificates required")
        paths = {"coding": args.coding_audit.resolve(), "install": args.setup_receipt.resolve(),
                 "parity": args.parity_audit.resolve(), "mcp": args.mcp_audit.resolve()}
        freeze(output, args.work.resolve(), manifest_path=args.manifest.resolve(), eligibility=args.eligibility.resolve(),
               gold_review=args.gold_review.resolve(), paths=paths, python=args.reranker_python.absolute(),
               checkpoint=args.checkpoint.resolve(), predecessors=[path.resolve() for path in args.serial_receipt])
        print("Frozen prospective native context holdout; no index/model work. Commit plan before --run.")
        return 0
    return run(output)


if __name__ == "__main__":
    raise SystemExit(main())
