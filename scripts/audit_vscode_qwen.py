#!/usr/bin/env python3
"""Independent local CPU audit of literal native editor receipts.

No producer/production module, model, MLX, installer or editor is imported.
Only cached tokenizers, source/metadata bytes and owned cleanup are inspected.
Checkpoint weights are never opened; their measured pins and stat witnesses
must agree. An empty hash-bound signal journal is authoritative over flags.
"""
from __future__ import annotations

import argparse
import ast
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
COUNTERS = ("requests", "attempted_pairs", "completed_pairs", "attempted_forwards", "completed_forwards")
SCORES = ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")
CANCEL_SUFFIX = " Cancellation transport smoke."
INSTRUCTION = ("Given a requested code behavior, retrieve executable code that directly "
               "implements the behavior or any necessary supporting part of that behavior. "
               "Names, comments, or related topics alone are insufficient.")
PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based '
          'on the Query and the Instruct provided. Note that the answer can only be '
          '"yes" or "no".<|im_end|>\n<|im_start|>user\n')
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
WARMUP = {"query": "Locate addition of two values.", "source": "def add(left, right):\n    return left + right\n"}
PINS = {
    "config.json": (727, "d479c427a9ca5295218063d4f9aca4f297ab4ac27487cca7af42c84643d51ef0"),
    "tokenizer_config.json": (9706, "253153d0738ceb4c668d2eff957714dd2bea0b56de772a9fdccd96cbf517e6a0"),
    "tokenizer.json": (11422654, "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"),
    "model.safetensors": (1191588280, "27cd75a405b9c1b46b59abfd88aaa209e6fed2a1972cde9b70e7659537c5e65b"),
}
RANKS_SHA = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
MEMBERSHIP = "saved implementations; Git-tracked files only in Git projects"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(items):
    result = {}
    for key, value in items:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(AssertionError("Nonfinite JSON: " + value)))


def read(path):
    return decode(path.read_bytes())


def equal(left, right, message):
    require(type(left) is type(right), message + " type")
    if isinstance(left, dict):
        require(left.keys() == right.keys(), message + " keys")
        for key in left:
            equal(left[key], right[key], message + "/" + key)
    elif isinstance(left, list):
        require(len(left) == len(right), message + " length")
        for a, b in zip(left, right):
            equal(a, b, message)
    else:
        require(left == right, message)


def native_counts(value, number):
    require(all(type(value.get(key)) is int and value[key] == number for key in COUNTERS), "Exact five native counters")


def physical_lines(text):
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", text)


def pair(query, source, tokenizer):
    def encode(text):
        value = tokenizer.encode(text, add_special_tokens=False)
        return list(value.ids if hasattr(value, "ids") else value)
    require(type(query) is str and query.strip() and type(source) is str and source, "Literal scoring text")
    require(len(encode("yes")) == len(encode("no")) == 1 and encode("yes") != encode("no"), "Distinct single yes/no tokens")
    header = f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: "
    lines = physical_lines(source)
    for count in range(len(lines), 0, -1):
        delivered = "".join(lines[:count])
        ids = encode(PREFIX) + encode(header + delivered) + encode(SUFFIX)
        if len(ids) <= 8192:
            return {"input_ids": ids, "query_sha256": sha(query.encode()), "source_sha256": sha(source.encode()),
                    "delivered_sha256": sha(delivered.encode()), "source_lines": len(lines), "delivered_lines": count,
                    "source_utf8_bytes": len(source.encode()), "delivered_utf8_bytes": len(delivered.encode()),
                    "truncated": delivered != source, "total_tokens": len(ids)}
    raise AssertionError("No whole scoring source line fits")


def scalar(value):
    require(all(type(value.get(key)) is float and math.isfinite(value[key]) for key in SCORES), "Finite typed score")
    difference = value["yes_logit"] - value["no_logit"]
    require(math.isfinite(difference), "Finite difference")
    signal = 1 / (1 + math.exp(-difference)) if difference >= 0 else math.exp(difference) / (1 + math.exp(difference))
    equal(value["logit_difference"], difference, "Difference arithmetic")
    equal(value["yes_rank_signal"], signal, "Signal arithmetic")


def validate_publication(certificate, journal_bytes):
    require(certificate.get("checks_passed") is True
            and certificate.get("publication_rule") == "Empty actual signals.jsonl with exact artifact hash required"
            and journal_bytes == b"" and certificate["artifact_sha256"].get("signals.jsonl") == sha(b""),
            "Empty exact-hash actual signals journal required")


def validate_native(rows, observations, counts, tokenizer, warmup):
    native_counts(counts, 11)
    require(len(rows) == 22, "Exactly 22 native events")
    expected = []
    qwen = [item for item in observations if item["response"].get("rerank") is True]
    require(len(qwen) == 2 and sum(bool(item["settings"].get("overlays")) for item in qwen) == 1, "One saved and one dirty Qwen search")
    for item in qwen:
        response = item["response"]
        require(response["rerank_provider"] == "qwen" and type(response["reranker_batch_size"]) is int
                and response["reranker_batch_size"] == 1 and response["candidate_windows"] == 5
                and response["retrieval_candidate_windows"] == 5, "Actual five-window singleton Qwen pool")
        returned = response["results"]
        require(len(returned) == 5 and len({row["id"] for row in returned}) == 5
                and set(response["reranker_receipts"]) == {row["id"] for row in returned}, "All five scored windows returned")
        for row in returned:
            require(row["truncated"] is False and row["h2o_score"] is None, "Full untruncated Qwen source")
            receipt = response["reranker_receipts"][row["id"]]
            require(type(receipt["batch_size"]) is int and receipt["batch_size"] == 1, "HTTP singleton batch")
            built = pair(item["query"], row["source"], tokenizer)
            for key in built.keys() - {"input_ids"}:
                equal(receipt["result"][key], built[key], "Literal scored pair/" + key)
            scalar(receipt["result"])
            equal(row["qwen_score"], receipt["result"]["logit_difference"], "Returned score")
            expected.append((receipt, built))
    require(len(expected) == 10, "Exactly ten measured HTTP receipts")
    expected.sort(key=lambda value: value[0]["counts"]["completed_forwards"])
    for number in range(1, 12):
        start, complete = rows[2 * (number - 1):2 * number]
        for event, row in (("started", start), ("completed", complete)):
            require(row["event"] == event and type(row["forward_number"]) is int and row["forward_number"] == number
                    and row["warmup"] is (number == 1) and type(row["batch_size"]) is int and row["batch_size"] == 1,
                    "Ordered typed native events")
        receipt, built = (None, pair(warmup["query"], warmup["source"], tokenizer)) if number == 1 else expected[number - 2]
        identity = "generic-warmup" if receipt is None else receipt["request_id"]
        if receipt is not None:
            native_counts(receipt["counts"], number)
        for row in (start, complete):
            require(row["request_id"] == identity
                    and row["input_ids_sha256"] == sha(json.dumps(built["input_ids"], separators=(",", ":")).encode()), "Literal request/input IDs")
            for key in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens"):
                equal(row[key], built[key], "Ledger literal/" + key)
        scalar(complete["score"])
        if receipt is not None:
            equal(complete["score"], {key: receipt["result"][key] for key in SCORES}, "HTTP/ledger scalars")
    return {"warmup": 1, "measured": 10, "started": 11, "completed": 11, "literal_pairs": 11}


def validate_embedding(attempts, entries, observations, counts):
    require(len(attempts) == 8 and len({item["request_id"] for item in attempts}) == 8, "Eight distinct actual searches")
    require(all(type(item["request_id"]) is str and item["request_id"] for item in attempts), "Typed search identities")
    require(all(type(entry["number"]) is int and entry["number"] == number and entry["status"] == "completed"
                for number, entry in enumerate(entries, 1)), "Complete ordered API entries")
    deterministic = [item for item in attempts if not item["query"].endswith(CANCEL_SUFFIX)]
    cancel = [item for item in attempts if item["query"].endswith(CANCEL_SUFFIX)]
    require(len(deterministic) == 7 and len(cancel) == 1 and all(item["status"] == "completed" for item in deterministic), "Seven deterministic successes")
    require(all(entry["kind"] in ("query", "documents") for entry in entries), "Known API entry kind")
    queries = [entry for entry in entries if entry["kind"] == "query"]
    attempts_by_id = {item["request_id"]: item for item in attempts}
    for entry in queries:
        require(entry["request_id"] in attempts_by_id and entry["query_sha256"] == sha(attempts_by_id[entry["request_id"]]["query"].encode()), "Actual query API identity")
    for item in deterministic:
        require(sum(entry["request_id"] == item["request_id"] for entry in queries) == 1, "One deterministic API entry")
    cancelled = cancel[0]
    n = sum(entry["request_id"] == cancelled["request_id"] for entry in queries)
    require(n in (0, 1) and cancelled["status"] in ("cancelled", "completed")
            and (cancelled["status"] != "completed" or n == 1), "Observed cancellation stage")
    completed = {item["request_id"] for item in attempts if item["status"] == "completed"}
    require(len(observations) == len(completed) and {item["request_id"] for item in observations} == completed, "Actual completed response inventory")
    docs = [entry for entry in entries if entry["kind"] == "documents"]
    require(all(type(entry["document_texts"]) is int for entry in docs), "Typed actual document API counts")
    dirty = [item for item in attempts if item["settings"].get("overlays")]
    require(len(dirty) == 1 and len(docs) == 2 and sum(entry["request_id"] is None and entry["document_texts"] == 5 for entry in docs) == 1
            and sum(entry["request_id"] == dirty[0]["request_id"] and entry["document_texts"] == 1 for entry in docs) == 1, "Initial five plus dirty one documents")
    equal(counts, {"query_calls": len(queries), "document_calls": 2, "document_texts": 6}, "Observed API counters")
    return {"search_attempts": 8, "query_entries": len(queries), "completed_responses": len(completed), "cancel_query_entries": n,
            "cancel_status": cancelled["status"], "document_calls": 2, "document_texts": 6}


def validate_host(host):
    require(host.get("passed") is True and host["host"]["passed"] is True and host["host"]["qwen_reranker"]["passed"] is True, "Actual host checks")
    close = host["relay_close"]
    require(close["records_stable"] is True and type(close["close_grace_ms"]) is int and close["close_grace_ms"] == 500
            and type(close["unfinished_receipts"]) is int and close["unfinished_receipts"] == 0
            and type(close["forced_incomplete_receipts"]) is int and close["forced_incomplete_receipts"] == 0,
            "Stable complete final relay drain")
    require(set(host["relay_close_checks"]) == {"records_stable", "no_unfinished_receipts", "final_receipts_without_transport_error", "no_hold_released_by_close"}
            and all(value is True for value in host["relay_close_checks"].values()) and host["owned_profile_removed"] is True, "Actual relay/profile cleanup")
    require(all(not row.get("transport_error") and not row.get("incomplete") and not row.get("hold_released_by_close") for row in host["receipts"]), "No discarded unfinished transport")
    require(host["exit"]["code"] == 0 and host["exit"]["completed_cleanly"] is True and host["exit"]["interrupted"] == [], "Clean editor exit")


def validate_host_preparation(preparation, config):
    for key in ("version", "commit", "platform", "test_electron_version"):
        equal(preparation[key], config[key], "Actual prepared host pin")
    equal(preparation["executable_sha256"], config["expected_executable_sha256"], "Measured prepared executable pin")
    equal(preparation["expected_official_archive_sha256"], config["expected_archive_sha256"], "Actual official host archive pin")


def local(value, root, work):
    require(type(value) is str, "Local path string")
    value = value.replace("<WORK>", str(work)).replace("<PROJECT>", str(root)).replace("<HOME>", str(Path.home()))
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts and "<" not in value, "Safe explicit retained path")
    return path


def file_hash(path):
    require(path.suffix != ".safetensors" and path.name != "token.secret", "Weight/auth bytes forbidden in audit")
    return sha(path.read_bytes())


def verify_files(root, bindings):
    for name, expected in bindings.items():
        path = root / name
        require(not Path(name).is_absolute() and ".." not in Path(name).parts and path.resolve().is_relative_to(root.resolve())
                and path.is_file() and file_hash(path) == expected, "Unchanged hash-bound artifact: " + name)


def offline_encoding(root):
    import tiktoken
    raw = (root / ".cache/tokenizers/9b5ad71b2ce5302211f9c61530b329a4922fc6a4").read_bytes()
    require(sha(raw) == RANKS_SHA, "Exact cached cl100k; downloads forbidden")
    ranks = {base64.b64decode(token, validate=True): int(rank) for token, rank in (line.split() for line in raw.splitlines())}
    require(len(ranks) == 100256 and set(ranks.values()) == set(range(100256)), "Complete rank inventory")
    return tiktoken.Encoding(name="cl100k_base", mergeable_ranks=ranks,
        pat_str=r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s",
        special_tokens={"<|endoftext|>": 100257, "<|fim_prefix|>": 100258, "<|fim_middle|>": 100259, "<|fim_suffix|>": 100260, "<|endofprompt|>": 100276})


def compact(response):
    rows = []
    for row in response["results"]:
        item = {key: row[key] for key in ("path", "symbol", "start_line", "end_line", "source", "source_hash", "truncated")}
        item.update(origin=row.get("origin", "retrieval"), related_to=row.get("related_to"))
        for key in ("source_origin", "buffer_version", "buffer_hash"):
            if key in row:
                item[key] = row[key]
        rows.append(item)
    require(set(response["token_usage"]) == {"encoding", "tokens", "budget", "truncated"}, "Compact usage keys")
    scope = response["scope"]
    fields = ("source_root", "membership", "source_exclusions", "exclusions_omitted")
    extras = ("source_state", "saved_source_manifest", "overlays") if "source_state" in scope else ()
    expected = set(fields) | set(extras)
    require(set(scope) == expected, "Compact scope keys")
    return {"query": response["query"], "match_status": response.get("match_status", "candidates"), "results": rows,
            "budget_exhausted": response.get("budget_exhausted", False),
            "token_usage": {key: response["token_usage"][key] for key in ("encoding", "tokens", "budget", "truncated")},
            "scope": {key: ([{field: entry[field] for field in ("path", "version", "content_hash")} for entry in scope[key]]
                            if key == "overlays" else scope[key]) for key in (*fields, *extras)}}


def normalized_host(value):
    if isinstance(value, dict):
        return {key: normalized_host(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized_host(item) for item in value]
    if isinstance(value, str):
        return value.replace("<WORK>/repo", "<fixture>").replace("<PROJECT>/extensions/vscode", "<extension>").replace("<HOME>", "<home>")
    return value


def equal_http(left, right):
    # JSON.parse/stringify in the relay loses float-vs-int lexical distinctions.
    # Actual native scalar typing is checked separately on producer/worker bytes.
    if type(left) in (int, float) and type(right) in (int, float):
        require(math.isfinite(left) and math.isfinite(right) and left == right, "HTTP numerical identity")
    elif isinstance(left, dict) and isinstance(right, dict):
        require(left.keys() == right.keys(), "HTTP response keys")
        for key in left:
            equal_http(left[key], right[key])
    elif isinstance(left, list) and isinstance(right, list):
        require(len(left) == len(right), "HTTP list inventory")
        for a, b in zip(left, right):
            equal_http(a, b)
    else:
        equal(left, right, "HTTP exact payload")


def fixture_windows(repo, overrides=None):
    windows = []
    for path in ("src/decorate.js", "src/delta.ts", "src/inventory.py"):
        text = (overrides or {}).get(path, (repo / path).read_bytes().decode("utf-8"))
        if path.endswith(".py"):
            lines = physical_lines(text)
            for node in ast.parse(text).body:
                if isinstance(node, ast.FunctionDef):
                    windows.append((path, node.name, "".join(lines[node.lineno - 1:node.end_lineno])))
        else:
            symbol = re.search(r"export function ([A-Za-z_][A-Za-z_0-9]*)\(", text)
            require(symbol is not None, "Authored fixture function")
            windows.append((path, symbol[1], text))
    require(len(windows) == 5, "Independent authored five-window inventory")
    return windows


def validate_document_inputs(entries, attempts, repo):
    initial = next(entry for entry in entries if entry["kind"] == "documents" and entry["request_id"] is None)
    def hashes(windows):
        return sorted(sha(f"Path: {path}\nSymbol: {symbol}\nSource:\n{text}".encode()) for path, symbol, text in windows)
    equal(sorted(initial["document_input_sha256"]), hashes(fixture_windows(repo)), "Actual initial formatted document inputs")
    dirty = next(item for item in attempts if item["settings"].get("overlays"))
    changed = next(entry for entry in entries if entry["kind"] == "documents" and entry["request_id"] == dirty["request_id"])
    overlay = dirty["settings"]["overlays"]
    require(len(overlay) == 1 and overlay[0]["path"] == "src/delta.ts", "Only authored dirty fixture")
    expected = [window for window in fixture_windows(repo, {overlay[0]["path"]: overlay[0]["text"]}) if window[0] == "src/delta.ts"]
    equal(changed["document_input_sha256"], hashes(expected), "Actual changed formatted document input")


def validate_http(observations, host, repo, encoding):
    receipts = [row for row in host["receipts"] if row["endpoint"] == "/search"]
    require(len(receipts) == 8 and len({row["request"]["request_id"] for row in receipts}) == 8, "Eight final HTTP search receipts")
    successful = {row["request"]["request_id"]: row for row in receipts if row["status"] == 200}
    require(set(successful) == {item["request_id"] for item in observations}, "HTTP/backend success identities")
    for item in observations:
        row = successful[item["request_id"]]
        request, response = row["request"], item["response"]
        require(item["query"] == request["query"] and response["query"] == item["query"]
                and response["retrieval"] == request["retrieval"] and response["rerank"] is request["rerank"], "Actual settings/query echoes")
        require(row["status"] == 200 and row["overlay_input_bindings_verified"] is True, "Actual HTTP source binding")
        # JavaScript reserializes the redacted response when publishing host.json.
        # Compare the retained response structurally after normalizing fixture roots.
        equal_http(row["response"], normalized_host(response))
        actual_settings = {"query": item["query"], **item["settings"]}
        equal({key: actual_settings[key] for key in request}, request, "Actual backend/HTTP request settings")
        require(row["request_sha256"] == sha(json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()), "Exact retained request body hash")
        require(type(response["token_usage"]["tokens"]) is int and response["token_usage"]["encoding"] == "cl100k_base"
                and response["token_usage"]["budget"] == request["token_budget"], "Exact compact count metadata")
        envelope = json.dumps(compact(response), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        require(len(encoding.encode(envelope, disallowed_special=())) == response["token_usage"]["tokens"] <= request["token_budget"], "Exact compact tokens/cap")
        require(len(response["results"]) <= request["max_results"] and sum(len(result["source"]) for result in response["results"])
                == response["total_source_chars"] <= request["source_budget"], "Actual result/source caps")
        overlays = {entry["path"]: entry for entry in request.get("overlays", [])}
        require(len(overlays) == len(request.get("overlays", [])), "Distinct dirty bindings")
        for entry in overlays.values():
            require(set(entry) == {"path", "text", "version", "content_hash"} and type(entry["version"]) is int
                    and entry["version"] >= 0 and sha(entry["text"].encode()) == entry["content_hash"], "Literal dirty buffer/version")
        equal({key: response["scope"][key] for key in ("source_root", "membership", "source_exclusions", "exclusions_omitted")},
              {"source_root": "src", "membership": "tracked implementations with request-only editor text overrides" if overlays else MEMBERSHIP,
               "source_exclusions": [], "exclusions_omitted": 0}, "Saved/dirty tracked scope")
        if overlays:
            require(response["scope"]["source_state"] == "overlay", "Dirty scope origin")
            equal(response["scope"]["overlays"], [{key: entry[key] for key in ("path", "version", "content_hash")} for entry in request["overlays"]], "Dirty scope echoes")
        for result in response["results"]:
            path = result["path"]
            require(path in ("src/inventory.py", "src/decorate.js", "src/delta.ts"), "Known literal fixture source")
            text = overlays[path]["text"] if path in overlays else (repo / path).read_bytes().decode("utf-8")
            lines = physical_lines(text)
            start, end, full = (result[key] for key in ("start_line", "end_line", "window_end_line"))
            require(all(type(value) is int for value in (start, end, full)) and 1 <= start <= end <= full <= len(lines), "Actual physical source bounds")
            excerpt, window = "".join(lines[start - 1:end]), "".join(lines[start - 1:full])
            require(result["source"] == excerpt and result["source_hash"] == sha(excerpt.encode())
                    and result["content_hash"] == sha(window.encode()), "Exact literal/full-window hashes")
            if path in overlays:
                entry = overlays[path]
                require(result["source_origin"] == "overlay" and result["buffer_version"] == entry["version"]
                        and result["buffer_hash"] == entry["content_hash"], "Actual returned dirty origin")
            elif overlays:
                require(result["source_origin"] == "saved", "Actual unchanged saved origin")
        checks = row["returned_source_checks"]
        require(len(checks) == len(response["results"]), "Every delivered source has a relay check")
        for check, returned in zip(checks, response["results"]):
            require(check["exact_source"] is True, "Relay source check passed")
            for key in ("path", "symbol", "start_line", "end_line", "source_hash"):
                equal(check[key], returned[key], "Relay/independent source identity")
    qwen = [row for row in receipts if row["request"].get("rerank") is True]
    off = [row for row in receipts if row["request"].get("retrieval") == "hybrid" and row["request"].get("rerank") is False]
    require(len(qwen) == 2 and len(off) == 1, "Saved/dirty/on and configured off HTTP inventory")
    for row in qwen + off:
        request = row["request"]
        for key, expected in {"retrieval": "hybrid", "candidates": 24, "max_results": 5, "token_budget": 2000, "source_budget": 12000, "include_dependencies": False}.items():
            equal(request[key], expected, "Normal configured setting/" + key)
        require((request.get("rerank_provider") == "qwen") if request["rerank"] else "rerank_provider" not in request, "Actual provider on/off wire identity")
    cancels = [row for row in host["receipts"] if row["endpoint"] == "/cancel" and row["status"] == 200]
    require(any(cancel["request"]["request_id"] in {row["request"]["request_id"] for row in receipts if row["request"]["query"].endswith(CANCEL_SUFFIX)} for cancel in cancels), "Linked actual cancellation")
    return {"search_receipts": 8, "successful_receipts": len(successful), "source_and_compact_caps_recounted": True}


def absent_group(pid):
    require(type(pid) is int and pid > 1, "Known owned group")
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return
    raise AssertionError("Known owned group still exists")


def absent_pid(pid):
    require(type(pid) is int and pid > 1, "Known owned PID")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    raise AssertionError("Known owned PID still exists")


def absent_listener(url):
    parsed = urlsplit(url)
    require(parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1") and parsed.port, "Known loopback listener")
    try:
        with socket.create_connection(("127.0.0.1", parsed.port), timeout=.3):
            pass
    except ConnectionRefusedError:
        return
    raise AssertionError("Known owned listener still accepts connections")


def runtime_proof(root, work, python, checkpoint, plan, result):
    owner = result["owner"]
    require(local(owner["interpreter"]["invocation_path"], root, work) == python
            and local(owner["interpreter"]["resolved_binary"], root, work) == python.resolve(), "Actual exact interpreter")
    site = python.parent.parent / "lib/python3.12/site-packages"
    pins = {re.sub(r"[-_.]+", "-", name).lower(): version for name, version in
            (line.split("==") for line in (root / "requirements-qwen-reranker.lock").read_text().splitlines() if line)}
    observed = {re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(): d.version for d in importlib.metadata.distributions(path=[str(site)])}
    require(len(pins) == len(observed) == 34 and observed == pins, "Actual complete 34-package metadata closure")
    require("include-system-site-packages = false" in (python.parent.parent / "pyvenv.cfg").read_text(), "Isolated runtime")
    provenance = owner["ready"]["provenance"]
    for status, n in ((owner["start_status"], 1), (owner["latest_status"], 11), (result["native_final_status"], 11)):
        equal(status["provenance"], provenance, "Stable actual provenance")
        native_counts(status["counts"], n)
        require(status["pid"] == owner["pid"] == owner["owned_group"] == owner["ready"]["pid"] == owner["ready"]["process_group"], "Actual owner PID/group identity")
    require(provenance["sequential_unpadded"] is True and type(provenance["batch_size"]) is int and provenance["batch_size"] == 1
            and provenance["max_total_tokens"] == 8192 and provenance["instruction"] == INSTRUCTION
            and provenance["scoring_core_sha256"] == plan["source_sha256"]["src/semantic_search/qwen_reranker_core.py"], "Actual pinned singleton scorer")
    runtime = provenance["runtime"]
    equal(runtime["packages"], plan["runtime"]["runtime"]["packages"], "Pinned primary versions")
    require(runtime["errors"] == [], "Runtime preflight clean")
    equal(runtime["supporting_source_sha256"], plan["runtime"]["runtime"]["supporting_source_sha256"], "Frozen supporting native sources")
    for name, expected in plan["runtime"]["main_packages"].items():
        require(importlib.metadata.version(name) == expected, "Observed main dependency metadata")
    for name, expected in {"qwen3.py": runtime["qwen3_source_sha256"], **runtime["supporting_source_sha256"]}.items():
        require(Path(name).name == name and file_hash(site / "mlx_lm/models" / name) == expected, "Observed native source bytes")
    require(local(runtime["qwen3_source_path"], root, work) == site / "mlx_lm/models/qwen3.py"
            == local(runtime["imported_qwen3_source_path"], root, work)
            and runtime["imported_qwen3_source_sha256"] == runtime["qwen3_source_sha256"] == plan["runtime"]["runtime"]["qwen3_source_sha256"], "Actual imported native source origin")
    model = provenance["model"]
    require(local(model["directory"], root, work) == checkpoint and model["weight_identity_verified"] is True
            and model["model_id"] == plan["runtime"]["model_id"] == "Qwen/Qwen3-Reranker-0.6B"
            and model["revision"] == plan["runtime"]["revision"] == "e61197ed45024b0ed8a2d74b80b4d909f1255473", "Measured original checkpoint")
    require(set(model["files"]) == set(model["file_witnesses"]) == set(PINS), "Exact checkpoint inventory")
    for name, (size, expected) in PINS.items():
        equal(model["files"][name], {"size": size, "sha256": expected}, "Measured original checkpoint pin")
        path = checkpoint / name
        stat = path.stat()
        witness = model["file_witnesses"][name]
        equal(witness[1:], [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns], "Unchanged measured physical checkpoint witness")
        require(local(witness[0], root, work) == path and stat.st_size == size, "Physical checkpoint path/size")
        if name != "model.safetensors":
            require(file_hash(path) == expected, "Cached non-weight checkpoint bytes")
    native_counts(owner["ready"]["warmup"]["counts"], 1)
    scalar(owner["ready"]["warmup"]["result"])
    return {"package_count": 34, "weight_bytes_read": 0, "physical_witnesses_unchanged": True}


def gate_proof(root, work, plan, python, checkpoint):
    require(set(plan["gate_paths"]) == set(plan["gates"]) == {"coding", "install", "parity", "mcp"}, "All four completed prerequisites")
    gates = {}
    for name, relative in plan["gate_paths"].items():
        verify_files(root, plan["gates"][name])
        require(relative in plan["gates"][name], "Bound gate certificate itself")
        gates[name] = read(root / relative)
    coding, install, parity, mcp = (gates[name] for name in ("coding", "install", "parity", "mcp"))
    require(coding["checks_passed"] is True and coding["inventory_complete"] is True and coding["final_completion_receipt"] is True
            and coding["recorded_attempts"] == 18 and coding["missing_attempts"] == [], "Full18 final coding gate")
    driver = read(root / ".cache/qwen-coding-workflows-2026-10-14/driver-observed.json")["driver"]
    absent_group(driver["process_group"])
    try:
        os.kill(driver["pid"], 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("Coding driver still exists")
    require(install["checks_passed"] is True and install["model_calls"] == 0 and install["weights_loaded"] is False
            and install["after"]["ready"] is True and install["after"]["package_count"] == 34
            and len(install["after"]["runtime"]["metadata"]["packages"]) == 34
            and install["after"]["checkpoint"]["weight_identity_verified"] is True
            and local(install["after"]["runtime"]["python_invocation"], root, work) == python, "Actual new installer receipt")
    directory = (root / plan["gate_paths"]["install"]).parent
    require(read(directory / "verification.json")["checks_passed"] is True and all(read(directory / "verification.json")["checks"].values()), "Installer outer preservation")
    destinations = read(directory / "frozen-plan.json")["destinations"]
    require(root / destinations["venv"] / "bin/python" == python and root / destinations["checkpoint"] == checkpoint, "Fresh installer destinations")
    for name in ("install-command.json", "verify-command.json"):
        command = read(directory / name)
        require(command["exit_code"] == 0 and command["timeout"] is False and command["interrupted"] in (False, None), "Clean installer commands")
    require(parity["checks_passed"] is True and parity["exact_original_pairs"] == 416 and parity["tokenizer_input_hashes_exact"] is True
            and parity["original_scalar_equality_exact"] is True and parity["owned_group_gone"] is True and parity["token_absent"] is True, "All416 actual installed parity")
    pdir = (root / plan["gate_paths"]["parity"]).parent
    require(file_hash(pdir / "frozen-plan.json") == parity["frozen_plan_sha256"], "Parity immutable plan")
    for phase in ("owner_start", "owner_stop"):
        owner = read(pdir / "verification.json")[phase]
        require(local(owner["interpreter"]["invocation_path"], root, work) == python
                and local(owner["ready"]["provenance"]["model"]["directory"], root, work) == checkpoint, "Actual parity fresh paths")
    require(mcp["checks_passed"] is True and mcp["study_qualified"] is True
            and mcp["process_cleanup"]["actual_auth_file_absent"] is True and mcp["process_cleanup"]["current_pid_and_group_absent"] is True
            and local(mcp["explicit_paths"]["reranker_python"], root, work) == python
            and local(mcp["explicit_paths"]["checkpoint"], root, work) == checkpoint, "New installed MCP gate/schema")
    require(file_hash((root / plan["gate_paths"]["mcp"]).parent / "frozen-plan.json") == mcp["frozen_plan_sha256"], "Installed MCP immutable plan")
    return {"coding": 18, "packages": 34, "parity": 416, "installed_mcp": True}


def cleanup_proof(root, work, result, host, private):
    require(result["interrupts"] == [] and result["cleanup_errors"] == [] and result["host_exit_code"] == 0, "No failed/interrupted cleanup")
    for flag in ("http_thread_stopped", "actual_http_listener_closed", "actual_owner_group_gone", "actual_auth_token_absent", "actual_owner_listener_closed", "native_private_public_ledger_identical"):
        require(result[flag] is True, "Actual lifecycle observation/" + flag)
    owner = result["owner"]
    require(owner["auth_token_removed"] is True and owner["private_cleanup_errors"] == [] and owner["token_mode"] == "0o600"
            and owner["private_directory_mode"] == "0o700" and owner["query_caching"] is False and owner["score_caching"] is False, "Owned private worker policy")
    for stopped in (owner["stop"], result["launcher_cleanup"], result["editor_cleanup"]):
        require(stopped["owned_group_stopped"] is True and stopped["forced_kill"] is False and not stopped.get("errors"), "Clean bounded known-group shutdown")
        absent_group(stopped["owned_group"])
        absent_pid(stopped["owned_group"])
    native_counts(owner["worker_stop"]["counts"], 11)
    require(owner["worker_stop"]["clean_stop"] is True and owner["worker_stop"]["listener_closed"] is True
            and owner["worker_stop"]["auth_token_removed"] is True and owner["worker_stop"]["auth_cleanup_error"] is None, "Worker stop receipt")
    absent_listener(owner["ready"]["url"])
    command = result["host_command"]
    require(command[:5] == ["npm", "run", "host:smoke", "--", "--run"] and "--qwen-reranker" in command, "Known explicit host invocation")
    absent_listener(command[command.index("--url") + 1])
    ownership = read(work / "editor-ownership.json")
    stopped = read(work / "editor-ownership.json.stop.json")
    require(ownership["nonce"] and ownership["detached"] is True and ownership["pid"] == ownership["process_group"]
            == result["editor_cleanup"]["owned_group"] == stopped["pid"], "Private nonce-bound detached editor identity")
    require(stopped["completed_cleanly"] is True and stopped["interrupted"] == [] and stopped["output_drained"] is True
            and host["exit"]["pid"] == stopped["pid"], "Actual detached editor clean stop")
    args = ownership["argv"]
    expected_executable = local(host["host_preparation"]["executable_relative"] if str(host["host_preparation"]["executable_relative"]).startswith("/") else
                                str(root / "extensions/vscode" / host["host_preparation"]["executable_relative"]), root, work)
    require(Path(ownership["executable"]) == expected_executable, "Exact prepared owned executable")
    require(str(work / "repo") in args and "--extensionTestsPath=" + str(root / "extensions/vscode/dist/test/host/index.js") in args, "Known owned fixture/host argv")
    profile = next((Path(arg.split("=", 1)[1]).parent for arg in args if arg.startswith("--user-data-dir=")), None)
    require(profile is not None and not profile.exists() and result["editor_cleanup"]["owned_profile_removed"] is True, "Actual owned profile gone")
    require(not (private / "token.secret").exists(), "Actual auth token absent")
    return {"known_groups_gone": 3, "known_listeners_closed": 2, "auth_token_absent": True, "owned_profile_removed": True}


def timestamp(value):
    require(type(value) is str, "Typed UTC timestamp")
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0, "Actual UTC timestamp")
    return parsed


def validate_clocks(plan, start, result, certificate, host):
    owner = result["owner"]
    chain = [plan["frozen_at_utc"], start["started_at_utc"], result["started_at_utc"], owner["started_at_utc"],
             owner["ready"]["started_at_utc"], host["started_utc"], host["finished_utc"], owner["stop"]["started_cleanup_utc"],
             owner["worker_stop"]["stopped_at_utc"], owner["stop"]["finished_cleanup_utc"], owner["closed_at_utc"],
             result["finished_at_utc"], certificate["finished_at_utc"]]
    values = [timestamp(value) for value in chain]
    require(values == sorted(values), "Actual freeze/start/editor/stop/publication order")
    attempts = {item["request_id"]: item for item in result["search_attempts"]}
    for item in attempts.values():
        require(timestamp(item["started_at_utc"]) <= timestamp(item["finished_at_utc"]), "Actual completed search timestamps")
    for entry in result["embedding_entries"]:
        a, b = timestamp(entry["entered_at_utc"]), timestamp(entry["finished_at_utc"])
        require(a <= b, "Actual completed API timestamps")
        if entry["request_id"] is not None:
            item = attempts[entry["request_id"]]
            require(timestamp(item["started_at_utc"]) <= a <= b <= timestamp(item["finished_at_utc"]), "API entry belongs to actual search interval")


def audit(study, work, *, root=ROOT):
    study, work = study.resolve(), work.resolve()
    require(study.is_relative_to(root / "results") and work.is_relative_to(root / ".cache"), "Explicit public/private project paths")
    files = {name: (study / name).read_bytes() for name in ("frozen-plan.json", "run-start.json", "results.json", "verification.json", "host.json", "signals.jsonl", "native-forwards.jsonl.gz")}
    before = {name: sha(raw) for name, raw in files.items()}
    plan, start, result, certificate, host = (decode(files[name]) for name in ("frozen-plan.json", "run-start.json", "results.json", "verification.json", "host.json"))
    validate_publication(certificate, files["signals.jsonl"])
    require(start["frozen_plan_sha256"] == before["frozen-plan.json"], "Actual start immutable plan")
    for name, expected in certificate["artifact_sha256"].items():
        require(Path(name).name == name and file_hash(study / name) == expected, "Published input artifact hash")
    equal(certificate["source_sha256"], plan["source_sha256"], "Exact producer source inventory")
    verify_files(root, plan["source_sha256"])
    verify_files(root, plan["host_runtime"]["files"])
    require(result["checks_passed"] is True and result["source_stable"] is True and result["host_runtime_stable"] is True and result["lifecycle_passed"] is True, "Actual completed wrapper")
    python, checkpoint = (local(plan["paths"][name], root, work) for name in ("python", "checkpoint"))
    require(local(plan["paths"]["work"], root, work) == work, "Exact intended private work")
    git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    require(subprocess.check_output(["git", "cat-file", "-t", plan["source_commit"]], cwd=root, text=True).strip() == "commit", "Producer committed before freeze")
    for name, expected in plan["source_sha256"].items():
        raw = subprocess.check_output(["git", "show", plan["source_commit"] + ":" + name], cwd=root)
        require(sha(raw) == expected, "Committed producer source bytes")
    raw_plan = subprocess.check_output(["git", "show", git_head + ":" + str((study / "frozen-plan.json").relative_to(root))], cwd=root)
    require(sha(raw_plan) == before["frozen-plan.json"], "Plan committed before audited run")
    repo = work / "repo"
    verify_files(repo, plan["fixture"]["source_files"])
    equal(plan["native_schedule"], {"warmup": 1, "saved_qwen": 5, "dirty_qwen": 5, "all_other_searches": 0}, "Prospective native schedule")
    equal(result["initial_status"]["source_manifest"], result["final_status"]["source_manifest"], "Saved manifest unchanged")
    equal(result["initial_embedding_counts"], {"query_calls": 0, "document_calls": 1, "document_texts": 5}, "Actual initial indexing API counts")
    require(result["initial_status"]["windows"] == result["final_status"]["windows"] == 5, "Saved fixture inventory")
    gates = gate_proof(root, work, plan, python, checkpoint)
    runtime = runtime_proof(root, work, python, checkpoint, plan, result)
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(checkpoint / "tokenizer.json"))
    ledger = gzip.decompress(files["native-forwards.jsonl.gz"])
    private_dirs = list((work / "qwen").glob("owned-*"))
    require(len(private_dirs) == 1 and private_dirs[0].is_dir(), "Exact newly owned worker directory")
    private = private_dirs[0]
    require(ledger == (private / "forwards.jsonl").read_bytes() and sha(ledger) == result["native_ledger_sha256"], "Published/private exact ledger bytes")
    def publication(value):
        if isinstance(value, dict):
            return {key: publication(item) for key, item in value.items()}
        if isinstance(value, list):
            return [publication(item) for item in value]
        if isinstance(value, str):
            return value.replace(str(work), "<WORK>").replace(str(root), "<PROJECT>").replace(str(Path.home()), "<HOME>")
        return value
    equal(publication(read(private / "ready.json")), result["owner"]["ready"], "Actual private/public owner readiness")
    equal(publication(read(private / "stop.json")), result["owner"]["worker_stop"], "Actual private/public worker stop")
    for name, expected in result["owner"]["worker_source_sha256"].items():
        require(Path(name).name == name and expected == plan["source_sha256"]["src/semantic_search/" + name]
                and file_hash(private / "runtime/semantic_search" / name) == expected, "Private copied worker source binding")
    rows = [decode(line) for line in ledger.splitlines()]
    native = validate_native(rows, result["observations"], result["native_final_status"]["counts"], tokenizer, WARMUP)
    warm = result["owner"]["ready"]["warmup"]
    scalar(warm["result"])
    equal(rows[1]["score"], {key: warm["result"][key] for key in SCORES}, "Warmup HTTP/ledger scalar equality")
    embedding = validate_embedding(result["search_attempts"], result["embedding_entries"], result["observations"], result["embedding_counts"])
    validate_document_inputs(result["embedding_entries"], result["search_attempts"], repo)
    validate_host(host)
    validate_host_preparation(host["host_preparation"], plan["host_runtime"]["config"])
    http = validate_http(result["observations"], host, repo, offline_encoding(root))
    cleanup = cleanup_proof(root, work, result, host, private)
    validate_clocks(plan, start, result, certificate, host)
    for name, expected in before.items():
        require(file_hash(study / name) == expected, "Receipt changed during independent audit")
    validate_publication(certificate, (study / "signals.jsonl").read_bytes())
    return {"checks_passed": True, "study_qualified": True, "classification": "Independent CPU audit of toy native Development Host integration; no quality/performance/billing claim",
            "audited_at_utc": datetime.now(timezone.utc).isoformat(), "audit_source_sha256": file_hash(Path(__file__)),
            "frozen_plan_sha256": before["frozen-plan.json"], "artifact_sha256": before, "native": native,
            "embedding": embedding, "http": http, "runtime": runtime, "gates": gates, "cleanup": cleanup,
            "explicit_paths": {"study": str(study.relative_to(root)), "work": str(work.relative_to(root)), "python": str(python.relative_to(root)), "checkpoint": str(checkpoint.relative_to(root))},
            "model_calls": 0, "weight_bytes_read": 0,
            "limits": ["Five authored windows and an isolated Development Host; no ordinary profile/quality/coding/billing claim.", "Only recorded owned process groups/listeners are probed; separately detached descendants are not inventoried."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    value = audit(args.study, args.work)
    report = args.report or args.study / "independent-audit.json"
    with report.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"checks_passed": True, "report": str(report)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
