#!/usr/bin/env python3
"""Independent source/token/coverage audit; never imports retrieval or selectors."""
from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.abc
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import sys
import tokenize
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ("legacy", "definition", "rank_preserving", "combined",
              "legacy_coverage_backfill", "definition_coverage_backfill")
EXPECTED_ROWS = {"language_observed": 90, "native_express_observed": 30,
                 "native_zod_observed": 30, "native_jinja_observed": 28,
                 "qwen_development_observed": 144, "qwen_httpx_observed": 15}
TOKENIZER_SHA = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
TOKENIZER_KEY = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
FORBIDDEN = ("semantic_search", "replay_selection", "benchmark_selection", "mlx", "torch",
             "transformers", "numpy", "jax")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def json_rows(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def digest_json(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def physical_lines(source):
    """Split only CR/LF, preserving Unicode line separators as literal data."""
    result, start = [], 0
    for match in re.finditer(r"\r\n|\r|\n", source):
        result.append(source[start:match.end()])
        start = match.end()
    if start < len(source):
        result.append(source[start:])
    return result


def compact_payload(response):
    """Literal public-envelope specification, independent of production helpers."""
    excerpts = []
    for original in response["results"]:
        row = {key: original[key] for key in
               ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")}
        row.update(origin=original.get("origin", "retrieval"), related_to=original.get("related_to"))
        for key in ("source_origin", "buffer_version", "buffer_hash"):
            if key in original:
                row[key] = original[key]
        excerpts.append(row)
    envelope = {"query": response["query"], "match_status": response.get("match_status", "candidates"),
                "results": excerpts, "budget_exhausted": response.get("budget_exhausted", False),
                "token_usage": response.get("token_usage", {"encoding": "cl100k_base", "tokens": None,
                                                           "budget": None, "truncated": False})}
    if "scope" in response:
        envelope["scope"] = response["scope"]
    return json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def ordered_original(response):
    result = deepcopy(response)
    for name, fields in (("scope", ("source_root", "membership", "source_exclusions", "exclusions_omitted")),
                         ("token_usage", ("encoding", "tokens", "budget", "truncated"))):
        if name in result:
            result[name] = {key: result[name][key] for key in fields}
    return result


def offline_encoding(cache):
    """Construct cl100k directly from verified local ranks; no download path."""
    require(importlib.metadata.version("tiktoken") == "0.14.0", "Unpinned tiktoken")
    raw = (cache / TOKENIZER_KEY).read_bytes()
    require(sha(raw) == TOKENIZER_SHA, "Exact offline tokenizer bytes required")
    ranks = {base64.b64decode(token): int(rank) for token, rank in
             (line.split() for line in raw.splitlines() if line)}
    import tiktoken
    return tiktoken.Encoding(name="cl100k_base",
        pat_str=r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s",
        mergeable_ranks=ranks, special_tokens={"<|endoftext|>": 100257, "<|fim_prefix|>": 100258,
            "<|fim_middle|>": 100259, "<|fim_suffix|>": 100260, "<|endofprompt|>": 100276})


class RefuseProduction(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in FORBIDDEN):
            raise RuntimeError("Independent audit forbids production/model imports: " + fullname)


def evidence_groups(query):
    groups = query.get("groups", query.get("target_groups", [query.get("targets")]))
    if query["kind"] == "absent":
        require(groups in ([], [None], [[]]), "Absent query has positive evidence")
        return []
    require(query["kind"] == "present" and groups and all(groups), "Invalid positive evidence")
    return groups


def evidence_ranks(excerpts, groups):
    # One complete actual excerpt must contain an alternative. No fragment union.
    return [next((rank for rank, row in enumerate(excerpts, 1)
                  if any(row["path"] == target["path"] and
                         row["start_line"] <= target["evidence_lines"][0] <=
                         target["evidence_lines"][1] <= row["end_line"] for target in alternatives)), None)
            for alternatives in groups]


def identity(row):
    return {key: row[key] for key in ("subset", "dataset", "query_id", "condition")}


def key(row):
    return tuple(row[name] for name in ("subset", "dataset", "query_id", "condition"))


def group_changes(before, after, groups):
    old_ranks, new_ranks = evidence_ranks(before["results"], groups), evidence_ranks(after["results"], groups)
    gains, losses = [], []
    for number, (old, new, alternatives) in enumerate(zip(old_ranks, new_ranks, groups), 1):
        if (old is None) == (new is None):
            continue
        prior = [row for row in before["results"] if evidence_ranks([row], [alternatives])[0] is not None]
        current = [row for row in after["results"] if evidence_ranks([row], [alternatives])[0] is not None]
        item = {"group": number, "evidence": alternatives, "baseline_rank": old, "variant_rank": new,
                "baseline_excerpts": [row["id"] for row in prior], "variant_excerpts": [row["id"] for row in current],
                "baseline_covering_origins": [{"id": row["id"], "origin": row.get("origin", "retrieval"),
                                              "related_to": row.get("related_to")} for row in prior],
                "baseline_dependency_only": bool(prior) and all(row.get("origin") == "dependency" for row in prior)}
        (gains if old is None else losses).append(item)
    return old_ranks, new_ranks, gains, losses


def verify_preservation(before, after):
    old_by_id = {row["id"]: row for row in before["results"]}
    for old in before["results"]:
        witnesses = [row for row in after["results"] if row["path"] == old["path"] and
                     row["start_line"] <= old["start_line"] <= old["end_line"] <= row["end_line"]]
        require(any("".join(physical_lines(row["source"])[old["start_line"] - row["start_line"]:
                    old["end_line"] - row["start_line"] + 1]) == old["source"] for row in witnesses),
                "Backfill lost an old actual span or covered it only by fragments")
    for row in after["results"]:
        if row["id"] in old_by_id:
            require(row == old_by_id[row["id"]], "Backfill changed a retained old row")
        else:
            require(row["origin"] == "retrieval" and row["related_to"] is None,
                    "Appended backfill must have retrieval origin")


def verify_response(response, inventory, sources, counter, *, max_results=8, chars=12000, tokens=4096):
    excerpts = response["results"]
    require(len(excerpts) <= max_results and len({r["id"] for r in excerpts}) == len(excerpts), "Excerpt count/ID cap")
    for row in excerpts:
        require(row["id"] in inventory, "Noncanonical source window")
        full = inventory[row["id"]]
        require(all(row[name] == full[name] for name in ("path", "symbol", "start_line")), "Canonical window identity")
        require(type(row["end_line"]) is int and full["start_line"] <= row["end_line"] <= full["end_line"]
                and row["window_end_line"] == full["end_line"], "Actual/window bounds")
        literal = "".join(physical_lines(sources[row["path"]])[row["start_line"] - 1:row["end_line"]])
        require(row["source"] == literal and row["source_hash"] == sha(literal.encode()), "Actual source/hash")
        require(row["content_hash"] == full["content_hash"] and row["truncated"] == (literal != full["text"]), "Full source/hash/truncation")
        require(row["origin"] in ("retrieval", "dependency") and
                (row["related_to"] is None if row["origin"] == "retrieval" else row["related_to"] in inventory), "Origin/parent")
    require(response["total_source_chars"] == sum(len(row["source"]) for row in excerpts) <= chars, "Character total/cap")
    usage = response["token_usage"]
    require(usage["encoding"] == "cl100k_base" and usage["budget"] == tokens
            and type(usage["tokens"]) is int and type(usage["truncated"]) is bool, "Token usage schema")
    text = compact_payload(response)
    require(len(counter.encode(text, disallowed_special=())) == usage["tokens"] <= tokens, "Exact compact tokens/cap")
    return text


def source_fixture(corpus_root, dataset, hashes, inventory_rows):
    corpus = corpus_root / ("jinja-multifile-holdout" if dataset == "jinja" else dataset)
    sources = {}
    for path, expected in hashes.items():
        absolute = (corpus / path).resolve()
        require(absolute.is_relative_to(corpus.resolve()), "Source escapes corpus")
        raw = absolute.read_bytes()
        require(sha(raw) == expected, "Pinned source differs: " + dataset + "/" + path)
        encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0] if path.endswith(".py") else "utf-8"
        sources[path] = raw.decode(encoding)
    inventory = {}
    for saved in inventory_rows:
        row = dict(saved)
        literal = "".join(physical_lines(sources[row["path"]])[row["start_line"] - 1:row["end_line"]])
        expected = row.get("content_hash", row.get("source_hash"))
        require(sha(literal.encode()) == expected and row.get("text", literal) == literal, "Canonical inventory source/hash")
        row["text"], row["content_hash"] = literal, expected
        require(row["id"] not in inventory, "Duplicate canonical window")
        inventory[row["id"]] = row
    return {"sources": sources, "inventory": inventory}


def python_inventory(corpus_root, hashes):
    """Independent AST reconstruction for the Python-only pinned Jinja corpus."""
    result = []
    for path in sorted(hashes):
        raw = (corpus_root / "jinja-multifile-holdout" / path).read_bytes()
        require(sha(raw) == hashes[path], "Jinja source hash")
        source = raw.decode(tokenize.detect_encoding(io.BytesIO(raw).readline)[0])
        tree, definitions, docstrings = ast.parse(source), [], []
        def doc(stmt):
            return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)
        def placeholder(stmt):
            if isinstance(stmt, ast.Pass) or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis):
                return True
            if isinstance(stmt, ast.Raise):
                exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
                return isinstance(exc, ast.Name) and exc.id == "NotImplementedError" or isinstance(exc, ast.Attribute) and exc.attr == "NotImplementedError"
            return False
        def visit(node, names=()):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified = (*names, child.name)
                    if child.body and doc(child.body[0]):
                        docstrings.append((child.body[0].lineno, child.body[0].end_lineno))
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        overload = any(isinstance(d, ast.Name) and d.id == "overload" or isinstance(d, ast.Attribute) and d.attr == "overload" for d in child.decorator_list)
                        body = child.body[1:] if child.body and doc(child.body[0]) else child.body
                        if not overload and not all(placeholder(s) for s in body):
                            definitions.append((".".join(qualified), child))
                    visit(child, qualified)
                else:
                    visit(child, names)
        visit(tree)
        lines = physical_lines(source)
        for symbol, node in definitions:
            executable = set()
            for stmt in ast.walk(node):
                if stmt is node or not isinstance(stmt, ast.stmt) or doc(stmt):
                    continue
                end = max(stmt.lineno, stmt.body[0].lineno - 1) if isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else stmt.end_lineno
                covered = set(range(stmt.lineno, end + 1))
                if hasattr(stmt, "body") or isinstance(stmt, ast.Match):
                    for low, high in docstrings:
                        covered.difference_update(range(low, high + 1))
                executable.update(covered)
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            while start <= node.end_lineno:
                end = min(start + 159, node.end_lineno)
                if any(start <= line <= end for line in executable):
                    text = "".join(lines[start - 1:end])
                    result.append({"id": f"{path}:{symbol}:{start}-{end}", "path": path, "symbol": symbol,
                                   "start_line": start, "end_line": end, "text": text, "content_hash": sha(text.encode())})
                if end == node.end_lineno:
                    break
                start += 120
    return result


def load_cases(root, corpus_root):
    """Read immutable original receipts, not the replay producer's case objects."""
    cases = []
    def add(subset, dataset, condition, observed, query, fixture):
        groups = evidence_groups(query)
        for alternatives in groups:
            for target in alternatives:
                low, high = target["evidence_lines"]
                literal = "".join(physical_lines(fixture["sources"][target["path"]])[low - 1:high])
                require(1 <= low <= high <= len(physical_lines(fixture["sources"][target["path"]])) and
                        sha(literal.encode()) == target["evidence_sha256"], "Pinned gold interval/hash")
        cases.append({"subset": subset, "dataset": dataset, "query_id": query.get("id", query.get("query_id")),
                      "condition": condition, "kind": query["kind"], "groups": groups,
                      "multi_file": observed.get("multi_file_and", observed.get("multi_file", False)),
                      "original": ordered_original(observed["response"]), **fixture})
    folder = root / "results/language-quality-2026-10-13"
    old, preflight = read(folder / "frozen-config.json"), read(folder / "extractor-preflight.json")
    manifest, observed = read(root / "benchmarks/language_quality_manifest.json"), json_rows(folder / "searches.jsonl")
    fixtures = {}
    for entry in manifest["datasets"]:
        dataset = entry["id"]
        snapshot = next(s for s in old["sources"] if s["id"] == dataset)
        inventory = next(s["window_inventory"] for s in preflight["datasets"] if s["id"] == dataset)
        fixtures[dataset] = source_fixture(corpus_root, dataset, snapshot["source_file_sha256"], inventory)
        labels = {q["id"]: q for q in read(root / entry["dataset"])["queries"]}
        for row in observed:
            if row["dataset"] == dataset and row["repetition"] == 1:
                add("language_observed", dataset, row["condition"], row, labels[row["query_id"]], fixtures[dataset])
    for dataset in ("express", "zod", "jinja"):
        folder = root / ("results/multifile-" + dataset + "-2026-10-19")
        plan, audit = read(folder / "plan.json"), read(folder / "audit.json")
        labels = {q["id"]: q for q in read(root / audit["dataset"]["path"])["queries"]}
        if dataset == "jinja":
            canonical = python_inventory(corpus_root, plan["binding"]["source_sha256"])
            require(sha(json.dumps(canonical, sort_keys=True).encode()) == plan["binding"]["inventory_sha256"], "Canonical Jinja AST extraction digest")
            fixtures[dataset] = source_fixture(corpus_root, dataset, plan["binding"]["source_sha256"], canonical)
        for row in json_rows(folder / "searches.jsonl.gz"):
            if row["repetition"] == 1 and row["retrieval"] in ("dense", "hybrid"):
                add("native_" + dataset + "_observed", dataset, row["retrieval"], row, labels[row["query_id"]], fixtures[dataset])
    for subset, name in (("qwen_development_observed", "qwen-shortlist-development-2026-10-14"),
                         ("qwen_httpx_observed", "qwen-shortlist-httpx-2026-10-14")):
        folder = root / "results" / name
        observed = json_rows(folder / "searches.jsonl.gz")
        for entry in read(folder / "frozen-plan.json")["sources"]:
            dataset = entry["id"]
            fixture = source_fixture(corpus_root, dataset, entry["source_file_sha256"], entry["window_inventory"])
            labels = {q["query_id"]: q for q in entry["queries"]}
            for row in observed:
                if row["identity"]["dataset"] == dataset and row["identity"]["repetition"] == 1 and row["configuration"]["id"] == "hybrid_k24_b1":
                    add(subset, dataset, "hybrid_k24_b1", row, labels[row["identity"]["query_id"]], fixture)
    require(dict(Counter(row["subset"] for row in cases)) == EXPECTED_ROWS and len(cases) == 337, "Exact 337-row matrix")
    require(len({(row["dataset"], row["query_id"]) for row in cases}) == 173, "Exact 173 distinct dataset/query units")
    require(len({key(row) for row in cases}) == len(cases), "Duplicate original case")
    return cases


def metrics(items):
    positive = [row for row in items if row["kind"] == "present"]
    return {"observed_rows": len(items), "unique_queries": len({(r["dataset"], r["query_id"]) for r in items}),
        "present_rows": len(positive), "absent_rows": len(items) - len(positive),
        "complete": sum(None not in r["evidence_ranks"] for r in positive),
        "required_groups": sum(len(r["evidence_ranks"]) for r in positive),
        "covered_groups": sum(sum(x is not None for x in r["evidence_ranks"]) for r in positive),
        "multi_file_complete": sum(r["multi_file"] and None not in r["evidence_ranks"] for r in positive),
        "single_file_first_hit": sum(not r["multi_file"] and bool(r["evidence_ranks"]) and r["evidence_ranks"][0] is not None for r in positive),
        "median_excerpts": statistics.median(len(r["response"]["results"]) for r in items),
        "median_chars": statistics.median(r["response"]["total_source_chars"] for r in items),
        "median_tokens": statistics.median(r["response"]["token_usage"]["tokens"] for r in items),
        "max_tokens": max(r["response"]["token_usage"]["tokens"] for r in items),
        "token_truncated": sum(r["response"]["token_usage"]["truncated"] for r in items),
        "absent_inventory": [{"query_id": r["query_id"], "status": r["response"]["match_status"], "excerpts": len(r["response"]["results"])} for r in items if r["kind"] == "absent"]}


def summarize(rows):
    strata, changes, qualifications = {}, {}, {}
    for name in CONDITIONS:
        selected = [r for r in rows if r["selector"] == name]
        partitions = sorted({(r["subset"], r["dataset"], r["condition"]) for r in selected})
        strata[name] = {"/".join(p): metrics([r for r in selected if (r["subset"], r["dataset"], r["condition"]) == p]) for p in partitions}
        gains = [{**identity(r), "groups": r["group_gains"]} for r in selected if r["group_gains"]]
        losses = [{**identity(r), "groups": r["group_losses"]} for r in selected if r["group_losses"]]
        complete_gains = [identity(r) for r in selected if r["kind"] == "present" and None in r["baseline_ranks"] and None not in r["evidence_ranks"]]
        complete_losses = [identity(r) for r in selected if r["kind"] == "present" and None not in r["baseline_ranks"] and None in r["evidence_ranks"]]
        hit_losses = [identity(r) for r in selected if r["kind"] == "present" and not r["multi_file"] and r["baseline_ranks"] and r["baseline_ranks"][0] is not None and r["evidence_ranks"][0] is None]
        unique = sorted({r["dataset"] + "/" + r["query_id"] for r in complete_gains})
        changes[name] = {"group_gains": gains, "group_losses": losses, "complete_gains": complete_gains,
                         "complete_losses": complete_losses, "unique_complete_gains": unique, "single_file_first_hit_losses": hit_losses}
        if name != "legacy":
            qualifications[name] = {"qualified": not losses and not complete_losses and not hit_losses and bool(unique),
                "lost_groups": sum(len(r["groups"]) for r in losses), "lost_complete_rows": len(complete_losses),
                "lost_single_file_first_hits": len(hit_losses), "gained_unique_complete_queries": len(unique)}
    index = {(r["selector"], *key(r)): r for r in rows}
    pairwise = []
    for row in rows:
        if row["selector"] == "definition_coverage_backfill":
            control = index[("legacy_coverage_backfill", *key(row))]
            before, after, gains, losses = group_changes(control["response"], row["response"], row["evidence_groups"])
            if gains or losses:
                pairwise.append({**identity(row), "control": "legacy_coverage_backfill", "variant": "definition_coverage_backfill",
                                 "control_ranks": before, "variant_ranks": after, "group_gains": gains, "group_losses": losses})
    return {"metrics_by_study_dataset_original_condition": strata, "all_changes": changes,
            "qualification": qualifications, "definition_coverage_vs_legacy_coverage_all_group_changes": pairwise}


def committed_freeze(root, plan_path, bound, started):
    relative = plan_path.relative_to(root).as_posix()
    commit = subprocess.check_output(["git", "-C", str(root), "log", "-1", "--format=%H", "--", relative], text=True).strip()
    require(bool(commit), "Prospective plan was not committed")
    subprocess.check_call(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"])
    stamp = int(subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%ct", commit], text=True))
    require(stamp <= datetime.fromisoformat(started).timestamp(), "Freeze commit follows run start")
    for path, expected in bound.items():
        raw = subprocess.check_output(["git", "-C", str(root), "show", commit + ":" + path])
        require(sha(raw) == expected, "Frozen committed blob differs: " + path)
    return {"commit": commit, "committed_at_unix": stamp, "bound_blob_count": len(bound)}


def audit_development(root, corpus_root, output, counter):
    plan_path = output / "frozen-plan.json"
    plan, summary = read(plan_path), read(output / "summary.json")
    artifacts = {p.name: sha(p.read_bytes()) for p in output.iterdir() if p.is_file() and p.name != "independent-audit.json"}
    require(not (output / "failure.json").exists(), "Producer failure receipt exists")
    resolutions = {}
    for path, expected in {**plan["producer_sha256"], **plan["input_sha256"]}.items():
        actual = sha((root / path).read_bytes())
        if actual != expected:
            # The run used the committed freeze. A later test-isolation repair
            # must not be mistaken for the bytes that produced these outcomes.
            require(path in plan["producer_sha256"], "Bound raw input artifact changed: " + path)
            resolutions[path] = {"frozen_sha256": expected, "current_sha256": actual,
                "resolution": "hash-verified prospective committed blob for identity only; never executed",
                "reason": "Current working bytes differ from the prospective CPU freeze; current bytes are not attributed to that run"}
    require(summary["plan_sha256"] == artifacts["frozen-plan.json"] and summary["replay_sha256"] == artifacts["replay.jsonl.gz"], "Plan/replay raw binding")
    start, gate, control = read(output / "run-start.json"), read(output / "baseline-gate.json"), read(output / "control-gate.json")
    require(start["plan_sha256"] == gate["plan_sha256"] == artifacts["frozen-plan.json"] and
            gate["exact_rows"] == control["exact_rows"] == summary["baseline_exact_rows"] == 337 and
            gate["variant_outcomes_computed"] == control["variant_rows_before_gate"] == 0, "All-control-before-variant gates")
    committed = {**plan["producer_sha256"], plan_path.relative_to(root).as_posix(): artifacts["frozen-plan.json"],
                 (output / "baseline-gate.json").relative_to(root).as_posix(): artifacts["baseline-gate.json"]}
    chain = committed_freeze(root, plan_path, committed, start["created_at_utc"])
    cases = load_cases(root, corpus_root)
    by_case = {key(case): case for case in cases}
    controls = []
    for case in cases:
        text = verify_response(case["original"], case["inventory"], case["sources"], counter)
        controls.append({**identity(case), "compact_sha256": sha(text.encode())})
    require(controls == gate["cases"] == control["cases"] and digest_json(controls) == plan["baseline_controls_sha256"], "337 exact legacy compact hash gates")
    outcomes = json_rows(output / "replay.jsonl.gz")
    require(len(outcomes) == 337 * len(CONDITIONS), "Incomplete six-condition matrix")
    by_outcome = {(row["selector"], *key(row)): row for row in outcomes}
    require(len(by_outcome) == len(outcomes), "Duplicate replay outcome")
    coverage_checks = 0
    for row in outcomes:
        require(row["selector"] in CONDITIONS and key(row) in by_case, "Unexpected selector/case")
        case = by_case[key(row)]
        require(row["kind"] == case["kind"] and row["multi_file"] == case["multi_file"] and row["evidence_groups"] == case["groups"], "Gold/case identity differs")
        require(row["response"]["query"] == case["original"]["query"] and row["response"]["scope"] == case["original"]["scope"], "Query/scope changed")
        text = verify_response(row["response"], case["inventory"], case["sources"], counter)
        require(sha(text.encode()) == row["compact_sha256"], "Outcome compact hash")
        before, after, gains, losses = group_changes(case["original"], row["response"], case["groups"])
        require((before, after, gains, losses) == (row["baseline_ranks"], row["evidence_ranks"], row["group_gains"], row["group_losses"]), "Full-group gains/losses differ")
        if row["selector"] == "legacy":
            require(row["response"]["results"] == case["original"]["results"] and
                    compact_payload(row["response"]) == compact_payload(case["original"]), "Legacy literal payload or full old rows changed")
        if row["selector"].endswith("_coverage_backfill"):
            prior_name = "definition" if row["selector"].startswith("definition") else "legacy"
            verify_preservation(by_outcome[(prior_name, *key(row))]["response"], row["response"])
            coverage_checks += 1
    require(set(by_outcome) == {(name, *key(case)) for case in cases for name in CONDITIONS}, "Missing declared outcome")
    recalculated = summarize(outcomes)
    for field, value in recalculated.items():
        require(summary[field] == value, "Independent summary mismatch: " + field)
    require(summary["counts"] == plan["counts"] and plan["counts"]["observed_rows"] == 337 and plan["counts"]["unique_queries"] == 173, "Reported row/unit counts")
    require(summary["model_calls"] == summary["retrieval_calls"] == start["model_calls"] == start["retrieval_calls"] == 0
            and summary["automatic_default_change"] is False, "Zero-call/default-change declarations")
    require(all(sha((output / name).read_bytes()) == value for name, value in artifacts.items()), "Audit changed or observed changing frozen artifacts")
    return {"checks_passed": True, "classification": "Independent CPU audit of observed DEVELOPMENT evidence",
            "artifact_sha256": artifacts, "producer_sha256": plan["producer_sha256"], "input_sha256": plan["input_sha256"],
            "committed_freeze": chain, "exact_legacy_rows": 337, "distinct_dataset_query_units": 173,
            "bound_file_resolutions": resolutions,
            "verified_outcome_rows": len(outcomes), "coverage_preservation_pairs": coverage_checks,
            "source_sha256": plan["source_sha256"], "qualification": recalculated["qualification"],
            "checks": ["Canonical saved extraction identities and pinned actual source/hash/bounds",
                       "Independent literal compact serializer, local token recount and exact caps",
                       "Every required full-group OR/AND outcome and all summary strata/regressions",
                       "337 original compact hashes, exact legacy row metadata and 173 distinct units",
                       "One retained actual-row witness per old span; unchanged retained rows; retrieval-only new rows",
                       "Raw source/code/input/plan/result hashes and prospective committed freeze chain"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root, results = args.root.resolve(), args.results.resolve()
    target = args.output.resolve() if args.output else results / "independent-audit.json"
    require(not target.exists(), "Never overwrite an existing independent audit")
    require(not any(any(name == p or name.startswith(p + ".") for p in FORBIDDEN) for name in sys.modules), "Use a clean CPU audit process")
    finder = RefuseProduction()
    sys.meta_path.insert(0, finder)
    def denied(*args, **kwargs):
        raise RuntimeError("Network is forbidden in independent audit")
    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
                patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
                patch.object(socket.socket, "sendto", denied):
            cache = Path(os.environ.get("TIKTOKEN_CACHE_DIR", root / ".cache/tokenizers"))
            receipt = audit_development(root, args.corpus_root.resolve(), results, offline_encoding(cache))
        receipt.update(reviewed_at_utc=datetime.now(timezone.utc).isoformat(),
                       auditor_sha256={p: sha((root / p).read_bytes()) for p in ("scripts/audit_selection.py", "tests/test_selection_audit.py")},
                       audit_model_calls=0, audit_retrieval_calls=0, audit_network_calls=0,
                       production_or_producer_modules_imported=False)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
        print(json.dumps({"checks_passed": True, "audit": str(target), "sha256": sha(target.read_bytes())}))
    finally:
        sys.meta_path.remove(finder)


if __name__ == "__main__":
    main()
