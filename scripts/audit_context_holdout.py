#!/usr/bin/env python3
"""Independent CPU audit of the frozen Werkzeug native HTTP holdout.

Only the already independent Django auditor supplies common source, tokenizer,
SQLite vector and clock proof routines. No producer or production helper imports.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import subprocess

import numpy as np

import audit_django_index_profile as common

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {"baseline8": 8, "primary12": 12, "diagnostic20": 20}
COUNTERS = ("requests", "attempted_pairs", "completed_pairs", "attempted_forwards", "completed_forwards")
WARMUP_QUERY = "Locate addition of two values."
WARMUP_SOURCE = "def add(left, right):\n    return left + right\n"
INSTRUCTION = ("Given a requested code behavior, retrieve executable code that directly "
               "implements the behavior or any necessary supporting part of that behavior. "
               "Names, comments, or related topics alone are insufficient.")
PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based '
          'on the Query and the Instruct provided. Note that the answer can only be '
          '"yes" or "no".<|im_end|>\n<|im_start|>user\n')
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
require, sha, read = common.require, common.sha, common.read


def expected_schedule(ids):
    require(len(ids) == len(set(ids)) == 15, "Exact fifteen-query schedule required")
    configs = list(CONFIGS)
    rows = [{"phase": "warmup", "pass": -1, "query_id": ids[0], "config_id": c} for c in configs]
    for repeat in range(3):
        rotated = ids[repeat:] + ids[:repeat]
        for position, query in enumerate(rotated):
            shift = (2 * repeat + position) % 3
            for condition in configs[shift:] + configs[:shift]:
                rows.append({"phase": "measured", "pass": repeat, "query_id": query, "config_id": condition})
    for position, row in enumerate(rows):
        row["request_id"] = f"context-{position:03d}-{row['phase']}-{row['config_id']}"
    return rows


def source_credit(query, delivered):
    if query["kind"] == "absent":
        return {"kind": "absent", "group_ranks": [], "complete": None, "multi_file": False}
    ranks = []
    for alternatives in query["target_groups"]:
        found = None
        for position, row in enumerate(delivered, 1):
            for target in alternatives:
                first, last = target["evidence_lines"]
                if row["path"] != target["path"] or not row["start_line"] <= first <= last <= row["end_line"]:
                    continue
                lines = common.physical_lines(row["source"])
                actual = "".join(lines[first - row["start_line"]:last - row["start_line"] + 1])
                if sha(actual.encode()) == target["evidence_sha256"]:
                    found = position
                    break
            if found is not None:
                break
        ranks.append(found)
    paths = {target["path"] for group in query["target_groups"] for target in group}
    return {"kind": "present", "group_ranks": ranks, "complete": all(v is not None for v in ranks), "multi_file": len(paths) > 1}


def quality(records):
    measured = [r for r in records if r["phase"] == "measured"]
    require(len(measured) == 135 and len({(r["pass"], r["config_id"], r["query_id"]) for r in measured}) == 135,
            "Incomplete/duplicated measured quality inventory")
    passes = []
    inventory = None
    for repeat in range(3):
        metrics, by_config = {}, {}
        for config in CONFIGS:
            selected = [r for r in measured if r["pass"] == repeat and r["config_id"] == config]
            require(len(selected) == 15 and sum(r["evidence"]["kind"] == "present" for r in selected) == 12,
                    "Each configuration/pass needs twelve positives and three absents")
            ids = {r["query_id"] for r in selected}
            inventory = ids if inventory is None else inventory
            require(ids == inventory, "Quality query IDs differ between conditions/passes")
            present = [r for r in selected if r["evidence"]["kind"] == "present"]
            metrics[config] = {"complete_case_ids": sorted(r["query_id"] for r in present if r["evidence"]["complete"]),
                               "covered_groups": sum(sum(v is not None for v in r["evidence"]["group_ranks"]) for r in present),
                               "required_groups": sum(len(r["evidence"]["group_ranks"]) for r in present),
                               "multi_file_complete": sum(r["evidence"]["complete"] and r["evidence"]["multi_file"] for r in present),
                               "absent_cases": 3}
            by_config[config] = {r["query_id"]: r["evidence"] for r in selected}
        baseline, primary = metrics["baseline8"], metrics["primary12"]
        comparisons = {"baseline_complete_retained": set(baseline["complete_case_ids"]) <= set(primary["complete_case_ids"]),
                       "group_coverage_floor": primary["covered_groups"] >= baseline["covered_groups"],
                       "multi_file_floor": primary["multi_file_complete"] >= baseline["multi_file_complete"],
                       "strict_complete_gain": len(primary["complete_case_ids"]) > len(baseline["complete_case_ids"])}
        passes.append({"pass": repeat, "metrics": metrics, "comparisons": comparisons,
                       "primary12_passed": all(comparisons.values())})
    return {"primary12_passed": all(p["primary12_passed"] for p in passes), "passes": passes,
            "diagnostic20_can_rescue_primary12": False, "default_changed": False}


def vector_capture(row):
    require(row["dtype"] == "float32" and row["shape"] == [1024] and
            all(type(size) is int for size in row["shape"]), "Actual query must retain original float32/1024 vector")
    require(isinstance(row["values"], list) and all(type(v) in (float, int) and math.isfinite(v) for v in row["values"]),
            "Invalid query vector values")
    value = np.asarray(row["values"], dtype=np.float32)
    require(value.shape == (1024,) and np.isfinite(value).all() and np.any(value != 0)
            and sha(value.tobytes()) == row["raw_sha256"], "Query vector raw bytes/hash differ")
    return value


def hybrid_pool(database, ids, matrix, query, vector):
    """Literal FTS5 query, independent NumPy cosine and independently merged RRF."""
    terms = list(dict.fromkeys(common.lexical_terms(query)))
    match = " OR ".join('"' + term + '"' for term in terms)
    with sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro&immutable=1", uri=True) as connection:
        lexical = connection.execute(
            "SELECT s.id,bm25(snippets_fts) AS distance FROM snippets_fts "
            "JOIN snippets s ON s.id=snippets_fts.id WHERE snippets_fts MATCH ? "
            "ORDER BY distance,s.id LIMIT 50", (match,)).fetchall() if terms else []
    dense = common.independent_dense(ids, matrix, vector, limit=50)
    scores = defaultdict(float)
    for ranking in ([r[0] for r in lexical], [r["id"] for r in dense]):
        for rank, identifier in enumerate(ranking, 1):
            scores[identifier] += 1 / (60 + rank)
    ordered = sorted(scores, key=lambda identifier: (-scores[identifier], identifier))[:50]
    return [{"id": identifier, "retrieval_score": scores[identifier]} for identifier in ordered]


def literal_pair(tokenizer, query, source, maximum=8192):
    def encode(text):
        return tokenizer.encode(text, add_special_tokens=False).ids
    prefix, suffix = encode(PREFIX), encode(SUFFIX)
    header = f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: "
    lines = common.physical_lines(source)
    remaining = len(lines)
    while remaining:
        delivered = "".join(lines[:remaining])
        ids = prefix + encode(header + delivered) + suffix
        if len(ids) <= maximum:
            return {"input_ids": ids, "input_ids_sha256": sha(json.dumps(ids, separators=(",", ":")).encode()),
                    "query_sha256": sha(query.encode()), "source_sha256": sha(source.encode()),
                    "delivered_sha256": sha(delivered.encode()), "source_lines": len(lines), "delivered_lines": remaining,
                    "source_utf8_bytes": len(source.encode()), "delivered_utf8_bytes": len(delivered.encode()),
                    "truncated": delivered != source, "total_tokens": len(ids)}
        remaining -= 1
    raise ValueError("No complete physical source line fits the fixed native token budget")


def scalar(receipt):
    result = receipt["result"]
    require(all(type(result[key]) is float and math.isfinite(result[key])
                for key in ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")),
            "All actual native scalar fields must be finite floats")
    y, n = result["yes_logit"], result["no_logit"]
    require(type(y) is type(n) is float and math.isfinite(y) and math.isfinite(n), "Invalid actual native scalar logits")
    difference = y - n
    require(math.isfinite(difference), "Overflowing native logit difference")
    probability = 1 / (1 + math.exp(-difference)) if difference >= 0 else math.exp(difference) / (1 + math.exp(difference))
    require(result["logit_difference"] == difference and result["yes_rank_signal"] == probability,
            "Actual binary scoring arithmetic differs")
    return {"yes_logit": y, "no_logit": n, "logit_difference": difference, "yes_rank_signal": probability}


def anchor_ranking(record):
    pool, pairs = record["full_pool"][:24], record["literal_pairs"]
    require(len(pool) == len(pairs) == 24 and [p["snippet_id"] for p in pairs] == [r["id"] for r in pool],
            "Actual scored prefix differs")
    scores = {p["snippet_id"]: scalar(p["receipt"])["logit_difference"] for p in pairs}
    order = sorted(range(24), key=lambda i: (-scores[pool[i]["id"]], i, pool[i]["id"]))
    unique, seen = [], set()
    for index in order:
        key = pool[index]["path"], pool[index]["symbol"]
        if key not in seen:
            unique.append(pool[index]["id"])
            seen.add(key)
    positions = {identifier: i for i, identifier in enumerate(unique)}
    anchors = [r for r in record["response"]["results"] if r["origin"] == "retrieval"]
    require(all(r["id"] in positions for r in anchors)
            and [positions[r["id"]] for r in anchors] == sorted(positions[r["id"]] for r in anchors),
            "Delivered anchors differ from native stable-prefix scalar order")
    by_id = {r["id"]: r for r in pool}
    for row in record["response"]["results"]:
        require(common.typed_equal(row["qwen_score"], scores.get(row["id"])),
                "Delivered Qwen scalar differs from actual native pair")
        if row["origin"] == "retrieval":
            require(common.typed_equal(row["retrieval_score"], by_id[row["id"]]["retrieval_score"]),
                    "Delivered anchor retrieval scalar differs from actual pool")
        else:
            require(row["related_to"] in unique[:5], "Dependency must refer to a preserved top-five native anchor")


def delivered_proof(response, query, sources, windows, maximum):
    import tiktoken
    require(response["query"] == query and response["retrieval"] == "hybrid" and response["rerank"] is True
            and response["rerank_provider"] == "qwen" and response["reranker_batch_size"] == 1
            and response["retrieval_candidate_windows"] == 50 and response["candidate_windows"] == 24,
            "Actual provider/full50/prefix24 differs")
    rows = response["results"]
    require(len(rows) <= maximum and len({r["id"] for r in rows}) == len(rows), "Delivered cap or IDs differ")
    total, compact_rows = 0, []
    for row in rows:
        require(row["id"] in windows, "Delivered source is outside original frozen inventory")
        window = windows[row["id"]]
        low, high = row["start_line"], row["end_line"]
        require(type(low) is type(high) is int and low == window["start_line"] and low <= high <= window["end_line"]
                and row["window_end_line"] == window["end_line"] and row["path"] == window["path"]
                and row["symbol"] == window["symbol"], "Delivered location/identity differs")
        text = "".join(common.physical_lines(common.source_text(sources[row["path"]]))[low - 1:high])
        require(text == row["source"] and sha(text.encode()) == row["source_hash"]
                and row["content_hash"] == window["source_hash"] and row["truncated"] is (high != window["end_line"]),
                "Delivered literal bytes or clipping proof differs")
        require(row["origin"] in ("retrieval", "dependency") and
                (row["related_to"] is None if row["origin"] == "retrieval" else row["related_to"] in windows),
                "Delivered dependency origin/anchor differs")
        total += len(text)
        compact = {k: row[k] for k in ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")}
        compact.update(origin=row.get("origin", "retrieval"), related_to=row.get("related_to"))
        for key in ("source_origin", "buffer_version", "buffer_hash"):
            if key in row:
                compact[key] = row[key]
        compact_rows.append(compact)
    require(type(response["total_source_chars"]) is int and total == response["total_source_chars"] <= 12000,
            "Delivered source-character budget/accounting differs")
    require(set(response["token_usage"]) == {"encoding", "tokens", "budget", "truncated"},
            "Exact compact token usage schema required")
    usage = {key: response["token_usage"][key] for key in ("encoding", "tokens", "budget", "truncated")}
    require(usage["encoding"] == "cl100k_base" and type(usage["budget"]) is int and usage["budget"] == 4096,
            "Compact encoding/budget differs")
    compact = {"query": response["query"], "match_status": response.get("match_status", "candidates"),
               "results": compact_rows, "budget_exhausted": response.get("budget_exhausted", False), "token_usage": usage}
    if "scope" in response:
        keys = ("source_root", "membership", "source_exclusions", "exclusions_omitted")
        require(set(response["scope"]) == set(keys), "Exact indexed scope schema required")
        compact["scope"] = {key: response["scope"][key] for key in keys}
    payload = json.dumps(compact, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    tokens = len(tiktoken.get_encoding("cl100k_base").encode(payload, disallowed_special=()))
    require(type(usage["tokens"]) is int and tokens == usage["tokens"] <= 4096, "Independent compact payload token count differs")
    return {"payload_tokens": tokens, "source_characters": total, "payload_sha256": sha(payload.encode()), "delivered_source_exact": True}


def ledger_pairs(events, records, tokenizer, warmup=None):
    require(len(events) == 6626 and [r["event"] for r in events] == [x for _ in range(3313) for x in ("started", "completed")],
            "Actual native ledger is incomplete/nonalternating")
    pairs = [pair for row in records for pair in row["literal_pairs"]]
    require(len(pairs) == 3312, "Actual HTTP pair inventory differs")
    for number in range(3313):
        start, end = events[number * 2:number * 2 + 2]
        require(type(start["forward_number"]) is type(end["forward_number"]) is int
                and start["forward_number"] == end["forward_number"] == number + 1,
                "Actual native forward numbering differs")
        for field in ("request_id", "warmup", "batch_size", "source_sha256", "query_sha256", "delivered_sha256", "total_tokens", "input_ids_sha256"):
            require(common.typed_equal(start[field], end[field]), "Native start/completion input differs")
        require(type(end["batch_size"]) is int and end["batch_size"] == 1 and end["warmup"] is (number == 0),
                "Actual singleton or warmup classification differs")
        if number == 0:
            require(end["request_id"] == "generic-warmup", "Generic owner warmup differs")
            built = literal_pair(tokenizer, WARMUP_QUERY, WARMUP_SOURCE)
            for key in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens", "input_ids_sha256"):
                require(common.typed_equal(end[key], built[key]), "Generic warmup fixed literal/token input differs")
            require(end["score"] == scalar({"result": end["score"]}), "Generic warmup scalar arithmetic differs")
            if warmup is not None:
                require(warmup["request_id"] == "generic-warmup" and warmup["warmup"] is True
                        and type(warmup["batch_size"]) is int and warmup["batch_size"] == 1,
                        "Actual readiness warmup receipt differs")
                require(set(warmup["counts"]) == set(COUNTERS) and all(type(warmup["counts"][c]) is int and warmup["counts"][c] == 1 for c in COUNTERS),
                        "Actual generic warmup counters differ")
                for key, value in built.items():
                    if key not in ("input_ids", "input_ids_sha256"):
                        require(common.typed_equal(warmup["result"][key], value), "Readiness warmup literal/token receipt differs")
                require(end["score"] == scalar(warmup), "Readiness warmup scalar differs from actual native ledger")
            continue
        pair = pairs[number - 1]
        require(set(pair["receipt"]["counts"]) == set(COUNTERS) and
                all(type(pair["receipt"]["counts"][c]) is int and pair["receipt"]["counts"][c] == number + 1 for c in COUNTERS),
                "Per-pair actual native counters differ from ordered forward")
        built = literal_pair(tokenizer, pair["query"], pair["source"])
        require(pair["status"] == "completed" and pair["request_id"] == end["request_id"]
                and pair["receipt"]["request_id"] == pair["request_id"]
                and type(pair["receipt"]["batch_size"]) is int and pair["receipt"]["batch_size"] == 1
                and pair["receipt"]["warmup"] is False, "Actual pair is incomplete/misbound")
        require(all(type(v) is int and v >= 0 for v in pair["input_ids"]) and
                pair["input_ids"] == built["input_ids"] and pair["input_ids_sha256"] == built["input_ids_sha256"],
                "Independent checkpoint token IDs differ")
        for field in built:
            if field in ("input_ids", "input_ids_sha256"):
                continue
            require(common.typed_equal(pair["receipt"]["result"][field], built[field]), "Actual pair tokenizer fields differ: " + field)
        require(end["input_ids_sha256"] == built["input_ids_sha256"] and end["score"] == scalar(pair["receipt"]),
                "Native actual input/scalar ledger differs")
        for field in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens"):
            require(common.typed_equal(end[field], built[field]), "Native literal identity differs: " + field)
    return {"forwards": 3313, "http_pairs": 3312, "measured_pairs": 3240, "http_warmup_pairs": 72, "generic_warmup": 1}


def gone(pid):
    require(type(pid) is int and pid > 1, "Strict owned process/group identity required")
    for action in (os.kill, os.killpg):
        try:
            action(pid, 0)
        except ProcessLookupError:
            continue
        raise ValueError("Actual owned process/group still exists")


def listener_closed(url):
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    require(parsed.hostname == "127.0.0.1" and parsed.port is not None, "Only declared loopback listeners may be checked")
    with socket.socket() as connection:
        connection.settimeout(.25)
        require(connection.connect_ex((parsed.hostname, parsed.port)) != 0, "Actual owned listener remains open")


def verify_store(path, expected, vectors_by_hash, metadata):
    fingerprint = sha(json.dumps(metadata, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode())
    with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro&immutable=1", uri=True) as connection:
        require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                "SQLite snapshot integrity failure")
        require(connection.execute("PRAGMA user_version").fetchone()[0] == 2, "SQLite schema differs")
        active_record = connection.execute("SELECT value FROM metadata WHERE key='active'").fetchone()
        require(active_record is not None, "No active metadata")
        active = common.decode(active_record[0])
        require(active.get("dimensions") == 1024 and active.get("model_fingerprint") == fingerprint
                and common.typed_equal(active.get("embedding"), metadata), "Stored embedding metadata differs")
        rows = connection.execute("SELECT id,position,path,symbol,start_line,end_line,text,content_hash,"
                                  "document_hash,model_fingerprint FROM snippets ORDER BY position").fetchall()
        require(len(rows) == expected["total_windows"], "Active source row count differs")
        wanted_rows = [(row["id"], i, row["path"], row["symbol"], row["start_line"], row["end_line"],
                        row["text"], row["content_sha256"], row["document_sha256"], fingerprint)
                       for i, row in enumerate(expected["windows"])]
        require(rows == wanted_rows, "Active saved source/position/document bindings differ")
        fts = dict(connection.execute("SELECT id,terms FROM snippets_fts"))
        require(len(fts) == len(rows) and fts == {
            row["id"]: " ".join(common.lexical_terms(f"{row['path']} {row['symbol']}\n{row['text']}"))
            for row in expected["windows"]}, "Active FTS bindings differ or stale IDs remain")
        cached = connection.execute("SELECT model_fingerprint,document_hash,dimensions,vector FROM embeddings").fetchall()
        require(len(cached) == expected["cached_documents_after"], "Cached document table count differs")
        require({row[1] for row in cached} == set(expected["cached_hashes"]), "Cached historical document union differs")
        for model, document_hash, dimensions, blob in cached:
            require(model == fingerprint and type(dimensions) is int and dimensions == 1024,
                    "Cached vector model/dimensions differ")
            value = vectors_by_hash[document_hash]
            require(value.dtype == np.dtype("float32") and value.shape == (1024,),
                    "Original native vector capture must be float32/1024")
            require(np.isfinite(value).all() and np.any(value != 0), "Invalid original vector")
            require(blob == value.astype("<f8").tobytes(), "Cached vector differs from original native float32 output")
        dense = connection.execute("SELECT snippet_id,model_fingerprint,dimensions,vector FROM active_vectors").fetchall()
        require(len(dense) == len(rows) and {row[0] for row in dense} == {row[0] for row in rows},
                "Active dense ID set differs or stale/deleted IDs remain")
        documents = {row["id"]: row["document_sha256"] for row in expected["windows"]}
        for identifier, model, dimensions, blob in dense:
            require(model == fingerprint and type(dimensions) is int and dimensions == 1024,
                    "Active dense model/dimensions differ")
            expected_blob = common.independent_float32(vectors_by_hash[documents[identifier]].reshape(1, -1))[0].tobytes()
            require(blob == expected_blob, "Active normalized float32 vector differs")
    return {"active_windows": len(rows), "cached_documents": len(cached), "model_fingerprint": fingerprint}



def audit(study):
    from tokenizers import Tokenizer
    study = Path(study).resolve()
    plan, worker, parent = [read(study / n) for n in ("frozen-plan.json", "worker-result.json", "parent-result.json")]
    require(plan["kind"] == "fresh_source_authored_native_context_holdout", "Wrong prospective experiment kind")
    require(parent["complete"] is True and parent["checks_passed"] is True and type(parent["exit_code"]) is int
            and parent["exit_code"] == 0 and worker["complete"] is True and worker["checks_passed"] is True,
            "Whole native holdout is failed/incomplete")
    require(parent["frozen_plan_sha256"] == sha((study / "frozen-plan.json").read_bytes())
            and parent["worker_result_sha256"] == sha((study / "worker-result.json").read_bytes()), "Parent result binding differs")
    for identity in (parent, worker):
        require(not identity.get("failure") and identity.get("signals", {}).get("received") == [], "Interrupted run cannot qualify")
    require(worker["cleanup_signals"]["received"] == [] and worker["cleanup_errors"] == []
            and worker["embedding_model_released"] is True and worker["http_thread_stopped"] is True,
            "Native cleanup/release is unproved")
    for field in ("http_listener_closed", "auth_token_absent", "reranker_group_gone", "reranker_listener_closed", "lifecycle_passed"):
        require(worker[field] is True, "Clean lifecycle witness differs: " + field)
    require(parent["cleanup"]["owned_group_stopped"] is True and parent["cleanup"]["forced_kill"] is False
            and parent["cleanup"]["errors"] == [], "Native parent group cleanup differs")
    ownership = parent["ownership"]
    require(ownership == worker["ownership"] and ownership["pid"] == ownership["owned_group"] == worker["pid"]
            and ownership["owner_pid"] == worker["parent_pid"] and ownership["start_new_session"] is True,
            "Actual native worker identity differs")
    actual_argv = [v.replace("<PROJECT>", str(ROOT)) for v in ownership["command"]]
    expected_argv = [str(common.local_path(plan["main_runtime"]["executable"])), "-B",
                     str(ROOT / "scripts/run_context_holdout.py"), "--_worker", str(study), "--owner-pid", str(ownership["owner_pid"])]
    require(actual_argv == expected_argv and sha(json.dumps(actual_argv).encode()) == ownership["command_sha256"],
            "Actual absolute owned argv differs from prospective worker")
    work = common.local_path(plan["work"])
    private_owned = read(work / "owned-process.json")
    require(common.typed_equal(private_owned, {**ownership, "command": actual_argv})
            and worker["ownership_proof_sha256"] == sha((work / "owned-process.json").read_bytes()), "Private native worker ownership proof differs")
    gone(ownership["pid"])
    gone(worker["owner"]["owned_group"])
    listener_closed(worker["http_url"])
    listener_closed(worker["owner"]["ready"]["url"])
    owner = worker["owner"]
    require(owner["score_caching"] is False and owner["query_caching"] is False,
            "Native owner must not cache actual pair/query results")
    stop, native_stop = owner["stop"], owner["worker_stop"]
    require(stop["owned_group_stopped"] is True and stop["exit_code"] == 0
            and stop["forced_kill"] is False and stop["errors"] == []
            and native_stop["clean_stop"] is True and native_stop["listener_closed"] is True
            and native_stop["auth_token_removed"] is True and native_stop.get("auth_cleanup_error") is None
            and owner["auth_token_removed"] is True and not owner.get("private_cleanup_errors"),
            "Actual isolated model/auth/listener cleanup differs")
    for field in ("power_start", "power_end"):
        require(worker[field]["ac_power"] is True, "AC power qualification differs")
    require(worker["sleep_events"]["events"] == [], "Suspension invalidated native timings")
    deadline = datetime.fromisoformat(plan["deadline_utc"])
    require(datetime.fromisoformat(worker["finished"]["utc"]) <= deadline, "Native study passed the authorized deadline")
    for path, expected in plan["source_sha256"].items():
        local = common.local_path(path)
        require(common.file_sha(local) == expected, "Bound source drift: " + path)
        relative = local.relative_to(ROOT).as_posix()
        committed = subprocess.check_output(["git", "-C", str(ROOT), "show", plan["source_commit"] + ":" + relative], timeout=30)
        require(sha(committed) == expected, "Source was not committed before freeze: " + path)
    require(plan["source_sha256"].get("scripts/audit_context_holdout.py") == sha(Path(__file__).read_bytes()),
            "Independent actual-result auditor was not frozen before outcomes")
    require(common.typed_equal(common.package_inventory(), plan["main_runtime"]["packages"]), "Main physical distribution closure drift")
    for bindings in (plan["gold_review"], plan["tokenizer_cache_sha256"], plan["embedding_native_source_sha256"]):
        for path, expected in bindings.items():
            require(common.file_sha(common.local_path(path)) == expected, "Frozen metadata/source input drift")
    for path, expected in plan["isolated_runtime"]["native_source_sha256"].items():
        require(common.file_sha(common.local_path(path)) == expected, "Isolated native physical source drift")
    for distribution in plan["isolated_runtime"]["packages"].values():
        origin = common.local_path(distribution["metadata_origin"])
        require(common.file_sha(origin / "METADATA") == distribution["metadata_sha256"]
                and common.file_sha(origin / "RECORD") == distribution["record_sha256"],
                "Isolated physical package metadata drift")
    for path, descriptor in plan["embedding_checkpoint"]["files"].items():
        current = common.local_path(plan["embedding_checkpoint"]["path"]) / path
        require(current.stat().st_size == descriptor["bytes"] and common.file_sha(current) == descriptor["sha256"],
                "Original embedding checkpoint bytes drift")
    model = owner["ready"]["provenance"]["model"]
    require(model["weight_identity_verified"] is True and model["revision"] == "e61197ed45024b0ed8a2d74b80b4d909f1255473"
            and model["model_id"] == "Qwen/Qwen3-Reranker-0.6B"
            and model["files"]["model.safetensors"]["sha256"] == "27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b",
            "Actual reranker must use original verified BF16 checkpoint")
    for name, descriptor in model["files"].items():
        current = common.local_path(plan["reranker_checkpoint"]) / name
        require(current.stat().st_size == descriptor["size"] and common.file_sha(current) == descriptor["sha256"],
                "Current reranker checkpoint byte witness differs")
    require(len(worker["embedding_initialization"]) == 1, "Initial genuine embedding model load must occur once")
    for origin in worker["embedding_initialization"][0]["loaded_native_origins"].values():
        path = common.local_path(origin["path"])
        require(plan["embedding_native_source_sha256"].get(str(path)) == origin["sha256"] == common.file_sha(path),
                "Actual loaded embedding native source origin differs")
    manifest = plan["manifest"]
    require(read(ROOT / "benchmarks/werkzeug_context_queries.json") == manifest, "Gold labels changed")
    expected_settings = {"retrieval": "hybrid", "rerank": True, "rerank_provider": "qwen", "candidates": 24,
                         "source_budget": 12000, "token_budget": 4096, "include_dependencies": True}
    require(common.typed_equal(plan["settings"], expected_settings)
            and plan["configurations"] == [{"id": key, "max_results": val} for key, val in CONFIGS.items()],
            "Only prospective context count may vary between conditions")
    expected = expected_schedule([q["id"] for q in manifest["queries"]])
    require(common.typed_equal(expected, plan["schedule"]), "Prospective balanced request schedule differs")
    freeze = read(ROOT / "results/werkzeug-context-preflight-2026-10-14/source-freeze.json")
    corpus = work / "corpus"
    require(subprocess.check_output(["git", "-C", str(corpus), "rev-parse", "HEAD"], text=True).strip() == freeze["revision"]
            and not subprocess.check_output(["git", "-C", str(corpus), "status", "--porcelain=v1"], text=True).strip(),
            "Execution clone Git pin or cleanliness differs")
    sources = {name: (corpus / name).read_bytes() for name in freeze["tracked_python_source_files"]}
    require({name: sha(raw) for name, raw in sources.items()} == freeze["tracked_python_source_files"], "Actual saved corpus bytes differ")
    windows = plan["source_identity"]["windows"]
    require(windows == read(ROOT / "results/werkzeug-context-preflight-2026-10-14/window-inventory.json") and len(windows) == 1025,
            "Actual source extraction membership differs")
    content = common.rows(study / "source-content.jsonl.gz")
    require(len(content) == 1025, "Missing original source-window content")
    for row, window in zip(content, windows, strict=True):
        require(all(common.typed_equal(row[k], v) for k, v in window.items()), "Saved source-window identity differs")
        text = "".join(common.physical_lines(common.source_text(sources[window["path"]]))[window["start_line"] - 1:window["end_line"]])
        require(row["source"] == text and row["source_hash"] == sha(text.encode()), "Actual source-window bytes differ")
        document = common.document(row["path"], row["symbol"], text)
        require(row["document_sha256"] == sha(document.encode()), "Actual rendered document differs")
        row.update(text=text, document=document, content_sha256=row["source_hash"])
    doc_ids = {r["document_sha256"]: r["id"] for r in content}
    require(len(doc_ids) == plan["source_identity"]["unique_document_count"] == 1025, "Original unique documents differ")
    query_by_id = {q["id"]: q for q in manifest["queries"]}
    operations = worker["embedding_operations"]
    wanted = [("documents", [r["document"] for r in content], None)] + [
        ("query", [common.QUERY_PROMPT + query_by_id[r["query_id"]]["query"]], r["query_id"]) for r in expected]
    embedding_events = common.rows(study / "embedding-events.jsonl.gz")
    require([r["sequence"] for r in embedding_events] == list(range(1, len(embedding_events) + 1)), "Embedding journal sequence differs")
    require(gzip.decompress((study / "embedding-events.jsonl.gz").read_bytes()) == (work / "embedding-events.jsonl").read_bytes(),
            "Actual embedding journal public/private bytes differ")
    vectors, _ = common.verify_operations(operations, wanted, embedding_events, plan["embedding_checkpoint"], doc_ids)
    state = {"total_windows": 1025, "cached_documents_after": 1025, "cached_hashes": sorted(vectors), "windows": content}
    metadata = plan["native_runtime"]["embedding"]
    common.verify_embedding_metadata(metadata)
    snapshot, _ = common.bound_artifact(worker["initial_database_snapshot"], ROOT)
    db_proof = verify_store(snapshot, state, vectors, metadata)
    empty, _ = common.bound_artifact(worker["first_empty_snapshot"], ROOT)
    with sqlite3.connect(f"file:{empty.resolve()}?mode=ro&immutable=1", uri=True) as connection:
        for table in ("embeddings", "snippets", "active_vectors", "snippets_fts", "metadata"):
            require(connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0, "Initial schema contains imported vectors/source")
    database = work / "index.sqlite3"
    verify_store(database, state, vectors, metadata)
    physical = parent["postexit_index_physical"]
    for path, expected_hash in physical["file_sha256"].items():
        require(common.file_sha(common.local_path(path)) == expected_hash, "Actual postexit disk hash differs")
    common.verify_disk(physical["disk"])
    actual_files = {p.name: p.stat().st_size for p in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")) if p.exists()}
    require(actual_files == physical["disk"]["files"], "Actual parent-postexit disk inventory differs")
    records, attempts = common.rows(study / "searches.jsonl.gz"), common.rows(study / "actual-http-attempts.jsonl.gz")
    require(len(records) == len(attempts) == 138, "Actual 135+3 HTTP inventory differs")
    require(gzip.decompress((study / "searches.jsonl.gz").read_bytes()) == (work / "searches.jsonl").read_bytes(), "Actual search journal public/private bytes differ")
    require(gzip.decompress((study / "actual-http-attempts.jsonl.gz").read_bytes()) == (work / "actual-http-attempts.jsonl").read_bytes(), "Actual HTTP attempts public/private bytes differ")
    native_events = common.rows(study / "native-forwards.jsonl.gz")
    native_dirs = [p for p in (work / "qwen").glob("owned-*") if p.is_dir()]
    require(len(native_dirs) == 1 and not (native_dirs[0] / "token.secret").exists(), "Actual native owner/auth artifact inventory differs")
    require(gzip.decompress((study / "native-forwards.jsonl.gz").read_bytes()) == (native_dirs[0] / "forwards.jsonl").read_bytes(), "Actual native ledger public/private bytes differ")
    reranker_tokenizer = Tokenizer.from_file(str(common.local_path(plan["reranker_checkpoint"]) / "tokenizer.json"))
    ids, matrix = [r["id"] for r in content], np.stack([vectors[r["document_sha256"]] for r in content])
    windows_by_id = {r["id"]: r for r in windows}
    latency = defaultdict(list)
    for number, (row, attempt, scheduled) in enumerate(zip(records, attempts, expected, strict=True)):
        require(all(common.typed_equal(row[k], v) for k, v in scheduled.items()) and row["status"] == attempt["status"] == "completed",
                "Actual HTTP schedule/status differs")
        require(attempt["request_id"] == row["request_id"] and attempt["query"] == query_by_id[row["query_id"]]["query"], "Actual backend request identity differs")
        require(common.typed_equal(attempt["settings"], {**expected_settings, "max_results": CONFIGS[row["config_id"]],
                                                     "request_id": row["request_id"]}),
                "Actual backend context condition/settings differ")
        vector = vector_capture(row["query_vector"])
        require(row["query_vector"] == attempt["query_vector"] and row["query_vector"]["raw_sha256"] == operations[number + 1]["matrix_raw_sha256"],
                "Actual query vector differs from corresponding native operation")
        pool = hybrid_pool(snapshot, ids, matrix, attempt["query"], vector)
        require([r["id"] for r in pool] == [r["id"] for r in row["full_pool"]]
                and all(abs(a["retrieval_score"] - b["retrieval_score"]) <= 1e-12 for a, b in zip(pool, row["full_pool"], strict=True)),
                "Independent full50 cosine/FTS/RRF order differs; near ties are not excused")
        require(row["full_pool"] == attempt["full_pool"] and row["literal_pairs"] == attempt["literal_pairs"]
                and [p["snippet_id"] for p in row["literal_pairs"]] == [p["id"] for p in row["full_pool"][:24]], "Actual native prefix24 differs")
        for pair, source in zip(row["literal_pairs"], row["full_pool"][:24], strict=True):
            require(pair["query"] == attempt["query"] and pair["source"] == source["source"]
                    and pair["receipt"] == row["response"]["reranker_receipts"][pair["snippet_id"]], "Actual pair and response receipt differ")
        anchor_ranking(row)
        proof = delivered_proof(row["response"], attempt["query"], sources, windows_by_id, CONFIGS[row["config_id"]])
        require(proof == row["source_proof"], "Independent delivered source/token proof differs")
        credit = source_credit(query_by_id[row["query_id"]], row["response"]["results"])
        require(common.typed_equal(credit, row["evidence"]), "Independent complete-span gold credit differs")
        common.clock_receipt(row["clock"])
        require(common.typed_equal(row["clock"]["baseline"], worker["clock_baseline"]), "HTTP clock baseline reset")
        interval = row["clock"]["interval"]
        require(interval["consistent"] is True and row["http_latency_s"] == interval["perf_elapsed_ns"] / 1e9, "Actual HTTP timer differs")
        require(common.typed_equal(row["embedding_delta"], {"document_inputs": 0, "query_inputs": 1, "attempted_forwards": 1, "completed_forwards": 1}), "HTTP embedding cache/inference differs")
        for counter in COUNTERS:
            require(type(row["native_before"][counter]) is type(row["native_after"][counter]) is int
                    and row["native_before"][counter] == 1 + number * 24
                    and row["native_after"][counter] - row["native_before"][counter] == row["native_delta"][counter] == 24,
                    "Actual HTTP native pair counter differs")
        if row["phase"] == "measured":
            latency[row["config_id"]].append(row["http_latency_s"])
    native_proof = ledger_pairs(native_events, records, reranker_tokenizer, worker["owner"]["ready"]["warmup"])
    quality_proof = quality(records)
    require(common.typed_equal(quality_proof, worker["quality_gate"]), "Independent primary quality gate differs")
    summaries = {c: common.timing_summary(latency[c]) for c in CONFIGS}
    require(all(len(latency[c]) == 45 for c in CONFIGS) and common.typed_equal(summaries, worker["latency"]), "Independent warm HTTP distributions differ")
    expected_counts = {"document_inputs": 1025, "query_inputs": 138, "attempted_forwards": 395, "completed_forwards": 395}
    require(common.typed_equal(worker["embedding_final_counts"], expected_counts), "Actual final embedding operation counts differ")
    require(all(type(worker["native_final_status"]["counts"][c]) is int and worker["native_final_status"]["counts"][c] == 3313 for c in COUNTERS), "Actual final native counts differ")
    require(all(type(native_stop["counts"][c]) is int and native_stop["counts"][c] == 3313 for c in COUNTERS),
            "Actual native final shutdown counts differ")
    return {"inventory_complete": True, "http_requests": 138, "measured_http_requests": 135, "warmup_http_requests": 3,
            "native": native_proof, "embedding_counts": expected_counts, "database": db_proof,
            "quality_gate": quality_proof, "latency": summaries, "no_model_inference_in_auditor": True,
            "full50_independent_fts_cosine_rrf_order": True, "all_actual_pairs_independently_reencoded": True,
            "actual_query_vector_hashes_match_native": True,
            "case_evidence": [{k: r[k] for k in ("phase", "pass", "config_id", "query_id", "evidence")} for r in records],
            "limitations": ["Independent FTS5 uses SQLite's BM25 implementation; NumPy independently checks cosine and RRF without production helpers.",
                            "Primary quality can fail while execution/audit checks pass. Diagnostic20 never selects or rescues the primary.",
                            "This is instrumented warm HTTP retrieval latency and compact context tokens, not coding completion time or paid agent tokens."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    require(not args.output.exists(), "Refuse audit overwrite/resume")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(ROOT / ".cache/tokenizers")
    result = {"checks_passed": False, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "auditor_sha256": sha(Path(__file__).read_bytes()), "model_calls": 0, "retrieval_calls": 0,
              "producer_or_production_helpers_imported": False}
    try:
        result.update(audit(args.study))
        result["checks_passed"] = True
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as out:
        out.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return 0 if result["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
