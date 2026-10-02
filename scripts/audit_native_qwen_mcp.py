#!/usr/bin/env python3
"""Independent CPU audit of the frozen native Qwen HTTP/MCP saved-edit smoke.

Explicit local paths must match actual retained owner receipts. No producer,
production worker, model, MLX, or site initialization is imported or executed.
The weight's measured hash and unchanged physical witness are checked without
rereading its bytes. This certifies toy integration, not retrieval or coding.
"""

from __future__ import annotations

import argparse
import ast
import base64
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from urllib.parse import urlparse

import tiktoken

ROOT = Path(__file__).resolve().parents[1]
RANKS_PATH = ROOT / ".cache/tokenizers/9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
RANKS_SHA = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
RANKS_COUNT = 100256
COUNTERS = (
    "requests",
    "attempted_pairs",
    "completed_pairs",
    "attempted_forwards",
    "completed_forwards",
)
SCORES = ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")
INTEGER_FIELDS = (
    "source_lines",
    "delivered_lines",
    "source_utf8_bytes",
    "delivered_utf8_bytes",
    "total_tokens",
)
RESULT_FIELDS = (
    set(SCORES)
    | set(INTEGER_FIELDS)
    | {"query_sha256", "source_sha256", "delivered_sha256", "truncated"}
)
PUBLIC_ARTIFACTS = {
    "frozen-plan.json",
    "run-start.json",
    "results.json",
    "native-forwards.jsonl",
}
DEFAULTS = {
    "retrieval": "hybrid",
    "rerank": True,
    "rerank_provider": "qwen",
    "candidates": 24,
}
CAPS = {
    "max_results": 8,
    "source_budget": 12000,
    "token_budget": 4096,
    "include_dependencies": True,
}
PHASES = [
    "http_before",
    "mcp_before",
    "watcher_saved_edit",
    "http_after",
    "mcp_after",
    "mcp_explicit_disable",
]
COPIED = {"__init__.py", "client.py", "qwen_reranker_core.py", "qwen_worker.py"}
CORE_NAMES = (
    "sha256",
    "file_hash",
    "runtime_preflight",
    "verified_qwen3_module",
    "checkpoint_preflight",
    "file_witness",
    "verify_witnesses",
    "physical_lines",
    "PairBuilder",
    "binary_score",
    "score_pair",
)
CORE_CONSTANTS = {
    "MODEL_ID",
    "REVISION",
    "MAX_TOKENS",
    "INSTRUCTION",
    "PREFIX",
    "SUFFIX",
    "PINS",
    "RUNTIME_PINS",
    "QWEN3_SOURCE_SHA",
}


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    out = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            out.update(block)
    return out.hexdigest()


def unique_object(items):
    result = {}
    for key, value in items:
        require(key not in result, "Duplicate JSON object key")
        result[key] = value
    return result


def decode(raw):
    return json.loads(
        raw,
        object_pairs_hook=unique_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError("Nonfinite JSON: " + value)
        ),
    )


def read(path):
    return decode(path.read_text())


def typed_equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            typed_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            typed_equal(a, b) for a, b in zip(actual, expected, strict=True)
        )
    return actual == expected


def equal(actual, expected, detail):
    require(typed_equal(actual, expected), detail)


def counts(value, expected, detail="Native counter identity"):
    require(
        isinstance(value, dict)
        and all(
            type(value.get(key)) is int and value[key] == expected for key in COUNTERS
        ),
        detail,
    )


def number(value, detail):
    require(type(value) is float and math.isfinite(value) and value >= 0, detail)


def assignments(path):
    return {
        node.targets[0].id: node.value
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }


def physical_lines(source):
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", source)


def compact(raw):
    results = [
        {
            key: row[key]
            for key in (
                "path",
                "symbol",
                "start_line",
                "end_line",
                "source",
                "source_hash",
                "truncated",
            )
        }
        | {
            "origin": row.get("origin", "retrieval"),
            "related_to": row.get("related_to"),
        }
        for row in raw["results"]
    ]
    return {
        "query": raw["query"],
        "match_status": raw["match_status"],
        "results": results,
        "budget_exhausted": raw["budget_exhausted"],
        "token_usage": {
            key: raw["token_usage"][key]
            for key in ("encoding", "tokens", "budget", "truncated")
        },
        "scope": {
            key: raw["scope"][key]
            for key in (
                "source_root",
                "membership",
                "source_exclusions",
                "exclusions_omitted",
            )
        },
    }


def scalar(score):
    require(
        all(
            type(score.get(key)) is float and math.isfinite(score[key])
            for key in SCORES
        ),
        "Finite typed native scalars",
    )
    difference = score["yes_logit"] - score["no_logit"]
    signal = (
        1 / (1 + math.exp(-difference))
        if difference >= 0
        else math.exp(difference) / (1 + math.exp(difference))
    )
    equal(score["logit_difference"], difference, "Native logit difference arithmetic")
    equal(score["yes_rank_signal"], signal, "Native binary signal arithmetic")


def offline_encoding(path=None):
    path = path or RANKS_PATH
    require(
        path.is_file(),
        "Pinned offline cl100k rank file missing; downloads are forbidden",
    )
    raw = path.read_bytes()
    require(
        digest(raw) == RANKS_SHA,
        "Pinned offline cl100k rank bytes differ; downloads are forbidden",
    )
    ranks = {}
    for line in raw.splitlines():
        token, rank = line.split()
        token = base64.b64decode(token, validate=True)
        require(token not in ranks, "Duplicate offline tokenizer rank token")
        ranks[token] = int(rank)
    require(
        len(ranks) == RANKS_COUNT and set(ranks.values()) == set(range(RANKS_COUNT)),
        "Exact offline tokenizer rank inventory",
    )
    return tiktoken.Encoding(
        name="cl100k_base",
        mergeable_ranks=ranks,
        pat_str=r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s",
        special_tokens={
            "<|endoftext|>": 100257,
            "<|fim_prefix|>": 100258,
            "<|fim_middle|>": 100259,
            "<|fim_suffix|>": 100260,
            "<|endofprompt|>": 100276,
        },
    )


def local_path(value, root, work):
    require(type(value) is str, "Retained local path required")
    for placeholder, path in (
        ("<WORK>", work),
        ("<PROJECT>", root),
        ("<HOME>", Path.home()),
    ):
        if value == placeholder or value.startswith(placeholder + "/"):
            value = str(path) + value[len(placeholder) :]
            break
    require(
        "<" not in value
        and Path(value).is_absolute()
        and ".." not in Path(value).parts,
        "Unsafe retained local path",
    )
    return Path(value)


def public(value, root, work):
    if isinstance(value, dict):
        return {key: public(item, root, work) for key, item in value.items()}
    if isinstance(value, list):
        return [public(item, root, work) for item in value]
    if isinstance(value, str):
        return (
            value.replace(str(work), "<WORK>")
            .replace(str(root), "<PROJECT>")
            .replace(str(Path.home()), "<HOME>")
        )
    return value


def clock_order(plan, start, certificate, owner):
    stop = owner["stop"]
    values = [
        plan["frozen_at_utc"],
        start["started_at_utc"],
        owner["started_at_utc"],
        owner["ready"]["started_at_utc"],
        stop["started_cleanup_utc"],
        owner["worker_stop"]["stopped_at_utc"],
        stop["finished_cleanup_utc"],
        owner["closed_at_utc"],
        certificate["completed_at_utc"],
    ]
    parsed = []
    for value in values:
        require(type(value) is str, "Typed clock timestamp required")
        moment = datetime.fromisoformat(value)
        require(
            moment.tzinfo is not None and moment.utcoffset().total_seconds() == 0,
            "UTC clock timestamp required",
        )
        parsed.append(moment)
    require(parsed == sorted(parsed), "Frozen/start/cleanup clock order")


def validate(
    plan,
    result,
    ledger,
    source,
    edited,
    tokenizer,
    core,
    warmup,
    *,
    context_encoder=None,
):
    """Reconstruct all 97 inputs without running a model or trusting its checker."""
    query = plan["fixture"]["query"]
    equal(plan["defaults"], DEFAULTS, "Frozen hybrid/Qwen/K24 defaults")
    equal(plan["caps"], CAPS, "Frozen result/source/token caps")
    equal(
        plan["phases"],
        [phase for phase in PHASES if phase != "watcher_saved_edit"],
        "Frozen phase inventory",
    )
    equal(
        plan["planned_native_forwards"],
        {
            "warmup": 1,
            **{
                phase: 0 if phase == "mcp_explicit_disable" else 24
                for phase in plan["phases"]
            },
        },
        "Frozen native forward inventory",
    )
    require(
        plan["kind"] == "qwen_native_http_mcp_saved_edit_smoke"
        and type(plan["version"]) is int
        and plan["version"] == 1
        and plan["cpu_injected"] is False
        and plan["semantic_quality_measurement"] is False
        and plan["paid_token_measurement"] is False,
        "Native toy scope required",
    )
    require(
        result["checks_passed"] is True
        and result["native_inference"] is True
        and result["source_stable"] is True
        and result["lifecycle_passed"] is True
        and result["semantic_quality_measurement"] is False
        and not result.get("failure")
        and result["cleanup_errors"] == []
        and result["interrupt_signals"] == {},
        "Completed native run required",
    )
    equal(
        result["source_sha256"], plan["source_sha256"], "Actual source bindings stable"
    )
    equal([row["phase"] for row in result["steps"]], PHASES, "Actual phase schedule")
    equal(
        result["embedding_counts"],
        {"query_calls": 6, "document_calls": 2, "document_texts": 62},
        "Actual embedding entries",
    )
    require(len(result["observations"]) == 6, "Actual observation inventory")
    initial = result["observations"][0]
    require(
        initial["query"] == "Find the implementation that processes a value."
        and initial["response"]["rerank"] is False,
        "Startup query is separate unreranked index warmup",
    )
    counts(result["owner"]["start_status"]["counts"], 1, "Owner startup counts")
    require(
        type(initial["response"]["indexstats"]["indexed_windows"]) is int
        and initial["response"]["indexstats"]["indexed_windows"] == 61,
        "Startup indexed windows",
    )
    schema = result["mcp"]["schema"]
    equal(schema["required"], ["query"], "MCP query-only required arguments")
    require(schema["additionalProperties"] is False, "MCP rejects unknown arguments")
    for name, value in (DEFAULTS | CAPS).items():
        equal(
            schema["properties"][name]["default"],
            value,
            "Actual MCP schema default: " + name,
        )
        require(
            schema["properties"][name]["type"]
            == (
                "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            ),
            "Typed MCP schema default: " + name,
        )
    require(len(ledger) == 194, "Exact native event inventory")
    pairs = list(zip(ledger[::2], ledger[1::2], strict=True))
    rows_by_request = {}
    for number_, (before, after) in enumerate(pairs, 1):
        require(
            before["event"] == "started" and after["event"] == "completed",
            "Native event alternation",
        )
        expected_keys = {
            "batch_size",
            "delivered_sha256",
            "event",
            "forward_number",
            "input_ids_sha256",
            "query_sha256",
            "request_id",
            "source_sha256",
            "total_tokens",
            "warmup",
        }
        require(
            before.keys() == expected_keys
            and after.keys() == expected_keys | {"score", "forward_s"},
            "Exact native event fields",
        )
        equal(
            {key: value for key, value in before.items() if key != "event"},
            {
                key: value
                for key, value in after.items()
                if key not in {"event", "score", "forward_s"}
            },
            "Native event identity",
        )
        require(
            type(after["forward_number"]) is int
            and after["forward_number"] == number_
            and type(after["batch_size"]) is int
            and after["batch_size"] == 1
            and type(after["total_tokens"]) is int
            and 0 < after["total_tokens"] <= 8192
            and after["warmup"] is (number_ == 1),
            "Sequential singleton native counters",
        )
        require(
            type(after["request_id"]) is str
            and after["request_id"] not in rows_by_request,
            "Unique native request identity",
        )
        require(
            all(
                type(after[key]) is str and re.fullmatch(r"[0-9a-f]{64}", after[key])
                for key in (
                    "input_ids_sha256",
                    "query_sha256",
                    "source_sha256",
                    "delivered_sha256",
                )
            ),
            "Native typed hash identities",
        )
        require(after["score"].keys() == set(SCORES), "Exact native scalar fields")
        number(after["forward_s"], "Actual native forward elapsed")
        scalar(after["score"])
        rows_by_request[after["request_id"]] = after
    prefix = tokenizer.encode(core["PREFIX"], add_special_tokens=False).ids
    suffix = tokenizer.encode(core["SUFFIX"], add_special_tokens=False).ids
    checked = []

    def input_proof(query_, text, receipt, native):
        require(receipt.keys() == RESULT_FIELDS, "Exact 13-field scoring result")
        require(
            all(
                type(receipt[key]) is int and receipt[key] > 0 for key in INTEGER_FIELDS
            ),
            "Typed positive input counters",
        )
        ids = (
            prefix
            + tokenizer.encode(
                f"<Instruct>: {core['INSTRUCTION']}\n<Query>: {query_}\n<Document>: "
                + text,
                add_special_tokens=False,
            ).ids
            + suffix
        )
        expected = {
            "source_sha256": digest(text.encode()),
            "delivered_sha256": digest(text.encode()),
            "query_sha256": digest(query_.encode()),
            "total_tokens": len(ids),
        }
        for key, value in expected.items():
            equal(receipt[key], value, "Literal input identity: " + key)
            equal(native[key], value, "Native literal input identity: " + key)
        equal(
            native["input_ids_sha256"],
            digest(json.dumps(ids, separators=(",", ":")).encode()),
            "Reconstructed tokenizer input IDs",
        )
        require(receipt["truncated"] is False, "Unclipped fixture source required")
        equal(receipt["source_utf8_bytes"], len(text.encode()), "Literal source bytes")
        equal(
            receipt["delivered_utf8_bytes"], len(text.encode()), "Whole delivered bytes"
        )
        equal(
            receipt["source_lines"], len(physical_lines(text)), "Physical source lines"
        )
        equal(
            receipt["delivered_lines"],
            len(physical_lines(text)),
            "Whole delivered lines",
        )
        scalar(receipt)
        equal(
            native["score"],
            {key: receipt[key] for key in SCORES},
            "Native scalar receipt equality",
        )
        checked.append(native["request_id"])

    warm = result["owner"]["ready"]["warmup"]
    require(
        warm["request_id"] == "generic-warmup"
        and warm["warmup"] is True
        and type(warm["batch_size"]) is int
        and warm["batch_size"] == 1,
        "Generic startup warmup identity",
    )
    counts(warm["counts"], 1)
    input_proof(warmup["query"], warmup["source"], warm["result"], pairs[0][1])
    for key in ("forward_s", "server_scoring_s", "tokenization_s"):
        number(warm[key], "Actual warmup elapsed")
    equal(warm["forward_s"], pairs[0][1]["forward_s"], "Warmup native elapsed identity")
    tokens, targets, contexts, expected_native = {}, {}, {}, []
    encoder = context_encoder if context_encoder is not None else offline_encoding()
    previous_count = 1
    for index, step in enumerate(result["steps"]):
        phase = step["phase"]
        if phase == "watcher_saved_edit":
            stats, watcher = step["status"]["indexstats"], step["status"]["watcher"]
            require(
                step["trigger_requests"] == "GET /status only"
                and type(step["status_polls"]) is int
                and step["status_polls"] >= 1,
                "Saved edit GET-status-only trigger",
            )
            for key, value in {
                "indexed_windows": 1,
                "reused_windows": 60,
                "total_windows": 61,
                "deleted_windows": 0,
            }.items():
                equal(stats[key], value, "Saved edit incremental window accounting")
            equal(watcher["refresh_count"], 1, "Exactly one saved edit refresh")
            require(
                watcher["last_error"] is None
                and step["status"]["source_manifest"]
                != initial["response"]["indexstats"]["source_manifest"],
                "Saved source manifest changed",
            )
            continue
        observation = result["observations"][index + 1 if index < 2 else index]
        require(observation["query"] == query, "Actual HTTP query identity")
        response = observation["response"]
        current = source if phase.endswith("before") else edited
        context = compact(step["context"] if "context" in step else step["response"])
        if "context" in step:
            equal(step["context"], context, "Exact compact MCP envelope inventory")
        require(
            context["query"] == query and context["match_status"] == "candidates",
            "Compact query/status",
        )
        require(
            type(context["budget_exhausted"]) is bool
            and type(context["token_usage"]["truncated"]) is bool
            and context["token_usage"]["encoding"] == "cl100k_base",
            "Typed compact budget flags",
        )
        count = len(
            encoder.encode(
                json.dumps(
                    context, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ),
                disallowed_special=(),
            )
        )
        equal(context["token_usage"]["tokens"], count, "Actual compact token count")
        equal(context["token_usage"]["budget"], 4096, "Actual compact token budget")
        require(
            count <= 4096
            and len(context["results"]) <= 8
            and sum(len(row["source"]) for row in context["results"]) <= 12000,
            "Actual compact result/source/token cap",
        )
        equal(context, compact(response), "MCP/HTTP compact output equality")
        if "response" in step:
            equal(step["response"], response, "Recorded HTTP step/observation equality")
        equal(
            context["scope"],
            {
                "source_root": "src",
                "membership": "saved implementations; Git-tracked files only in Git projects",
                "source_exclusions": [],
                "exclusions_omitted": 0,
            },
            "Tracked toy source scope",
        )
        lines = physical_lines(current)
        for row in context["results"]:
            require(
                type(row["start_line"]) is int
                and type(row["end_line"]) is int
                and 1 <= row["start_line"] <= row["end_line"] <= len(lines)
                and type(row["truncated"]) is bool,
                "Typed saved source span",
            )
            require(
                row["path"] == "src/sample.py"
                and row["source"]
                == "".join(lines[row["start_line"] - 1 : row["end_line"]]),
                "Exact saved source span",
            )
            equal(
                row["source_hash"],
                digest(row["source"].encode()),
                "Saved source span hash",
            )
        target = [row for row in context["results"] if row["symbol"] == "backoff_delay"]
        require(
            len(target) == 1 and target[0]["truncated"] is False,
            "Complete toy implementation",
        )
        targets[phase], tokens[phase] = target[0]["source_hash"], count
        contexts[phase] = context
        disabled = phase == "mcp_explicit_disable"
        expected_count = 0 if disabled else 24
        counts(
            step["native_before"], previous_count, "Exact phase native before counts"
        )
        previous_count += expected_count
        counts(step["native_after"], previous_count, "Exact phase native after counts")
        settings = DEFAULTS | CAPS | ({"rerank": False} if disabled else {})
        if phase.startswith("mcp"):
            request_id = observation["settings"].get("request_id")
            require(
                type(request_id) is str and re.fullmatch(r"[0-9a-f]{32}", request_id),
                "Actual MCP transport request identity",
            )
            settings = settings | {"request_id": request_id}
        equal(observation["settings"], settings, "Actual effective HTTP defaults/caps")
        require(
            response["retrieval"] == "hybrid" and response["rerank"] is (not disabled),
            "Actual response retrieval/rerank",
        )
        if phase.startswith("mcp"):
            equal(
                step["raw_tool_arguments"],
                {"query": query} | ({"rerank": False} if disabled else {}),
                "Query-only actual MCP arguments",
            )
            equal(
                step["effective_http_settings"], settings, "MCP effective HTTP receipt"
            )
        if disabled:
            require(
                not response.get("reranker_receipts"),
                "Explicit rerank false adds no scores",
            )
            continue
        for key, value in {
            "candidate_windows": 24,
            "retrieval_candidate_windows": 50,
            "reranker_batch_size": 1,
            "rerank_provider": "qwen",
        }.items():
            equal(response[key], value, "Actual Qwen prefix/full50/B1")
        receipts = response["reranker_receipts"]
        require(len(receipts) == 24, "Exactly 24 actual singleton receipts")
        spans = {
            f"src/sample.py:{node.name}:{node.lineno}-{node.end_lineno}": "".join(
                lines[node.lineno - 1 : node.end_lineno]
            )
            for node in ast.parse(current).body
            if isinstance(node, ast.FunctionDef)
        }
        ordered = sorted(
            receipts.items(), key=lambda item: item[1]["counts"]["completed_forwards"]
        )
        request_prefix = ordered[0][1]["request_id"].rsplit("-", 1)[0]
        require(
            re.fullmatch(r"[0-9a-f]{32}", request_prefix) is not None,
            "Actual scorer request prefix",
        )
        if phase.startswith("mcp"):
            equal(
                request_prefix,
                observation["settings"]["request_id"],
                "MCP transport/native request binding",
            )
        for offset, (candidate, receipt) in enumerate(ordered, previous_count - 23):
            require(
                receipt.keys()
                == {
                    "batch_size",
                    "counts",
                    "forward_s",
                    "request_id",
                    "result",
                    "server_scoring_s",
                    "tokenization_s",
                    "warmup",
                },
                "Exact native score receipt fields",
            )
            require(
                candidate in spans
                and receipt["warmup"] is False
                and type(receipt["batch_size"]) is int
                and receipt["batch_size"] == 1,
                "Scored implementation identity",
            )
            counts(receipt["counts"], offset, "Exact cumulative scored native counts")
            require(
                receipt["request_id"] in rows_by_request,
                "Actual scored native request exists",
            )
            native = rows_by_request[receipt["request_id"]]
            equal(
                receipt["request_id"],
                request_prefix + "-" + str(offset - (previous_count - 23)),
                "Ordered actual scorer request identity",
            )
            equal(native["forward_number"], offset, "Receipt native forward identity")
            input_proof(query, spans[candidate], receipt["result"], native)
            for key in ("forward_s", "server_scoring_s", "tokenization_s"):
                number(receipt[key], "Actual pair elapsed")
            equal(
                receipt["forward_s"],
                native["forward_s"],
                "Native pair elapsed identity",
            )
            expected_native.append(receipt["request_id"])
    equal(
        expected_native,
        [after["request_id"] for _, after in pairs[1:]],
        "Complete actual native request order",
    )
    require(
        len(checked) == 97 and len(set(checked)) == 97,
        "All 97 literal inputs reconciled",
    )
    require(
        targets["http_before"] == targets["mcp_before"]
        and targets["http_after"] == targets["mcp_after"]
        and targets["http_before"] != targets["http_after"],
        "Saved edit target source identity",
    )
    equal(
        contexts["http_before"],
        contexts["mcp_before"],
        "Exact HTTP/MCP before-edit compact output",
    )
    equal(
        contexts["http_after"],
        contexts["mcp_after"],
        "Exact HTTP/MCP after-edit compact output",
    )
    owner = result["owner"]
    stop, worker = owner["stop"], owner["worker_stop"]
    require(
        stop["owned_group_stopped"] is True
        and type(stop["exit_code"]) is int
        and stop["exit_code"] == 0
        and stop["forced_kill"] is False
        and stop["errors"] == [],
        "Clean owned stop",
    )
    require(
        owner["auth_token_removed"] is True
        and owner["private_cleanup_errors"] == []
        and worker["auth_token_removed"] is True
        and worker["auth_cleanup_error"] is None
        and worker["clean_stop"] is True
        and worker["listener_closed"] is True
        and result["http_thread_stopped"] is True,
        "Auth/listener/thread cleanup",
    )
    for receipt in (worker, result["native_final_status"], owner["latest_status"]):
        counts(receipt["counts"], 97, "Final stop native counts")
    require(
        type(owner["batch_size"]) is int
        and owner["batch_size"] == 1
        and owner["score_caching"] is False
        and owner["query_caching"] is False
        and owner["private_directory_mode"] == "0o700"
        and owner["token_mode"] == "0o600",
        "Private original B1/no-cache lifecycle",
    )
    return {
        "native_forwards": 97,
        "warmup_forwards": 1,
        "measured_forwards": 96,
        "native_events": 194,
        "reconstructed_input_pairs": len(checked),
        "compact_tokens": tokens,
        "target_source_sha256": targets,
        "embedding_counts": result["embedding_counts"],
        "saved_edit_indexed_windows": 1,
        "saved_edit_reused_windows": 60,
    }


def artifacts(study):
    plan, certificate, result, start = [
        read(study / name)
        for name in (
            "frozen-plan.json",
            "verification.json",
            "results.json",
            "run-start.json",
        )
    ]
    require(
        certificate["checks_passed"] is True and certificate["cpu_injected"] is False,
        "Native producer certificate",
    )
    require(
        set(certificate["artifact_sha256"]) == PUBLIC_ARTIFACTS,
        "Exact producer artifact inventory",
    )
    for name, expected in certificate["artifact_sha256"].items():
        require(
            type(expected) is str
            and re.fullmatch(r"[0-9a-f]{64}", expected)
            and file_digest(study / name) == expected,
            "Public artifact hash binding: " + name,
        )
    plan_hash = file_digest(study / "frozen-plan.json")
    require(
        certificate["frozen_plan_sha256"] == start["frozen_plan_sha256"] == plan_hash
        and start["native_inference_authorized"] is True,
        "Actual attempt/plan authorization binding",
    )
    equal(
        certificate["source_sha256"],
        plan["source_sha256"],
        "Producer source certificate binding",
    )
    ledger = [
        decode(line)
        for line in (study / "native-forwards.jsonl").read_text().splitlines()
    ]
    equal(
        result["native_ledger"],
        {
            "events": 194,
            "started": 97,
            "completed": 97,
            "warmup_forwards": 1,
            "measured_forwards": 96,
            "sha256": file_digest(study / "native-forwards.jsonl"),
        },
        "Producer native ledger inventory binding",
    )
    require(
        result["native_ledger_preserved_sha256"]
        == file_digest(study / "native-forwards.jsonl"),
        "Preserved native ledger binding",
    )
    clock_order(plan, start, certificate, result["owner"])
    return plan, certificate, result, ledger


def git_bytes(root, revision, name):
    require(
        type(revision) is str
        and re.fullmatch(r"[0-9a-f]{40}", revision)
        or revision == "HEAD",
        "Exact committed revision required",
    )
    path = Path(name)
    require(
        not path.is_absolute() and ".." not in path.parts,
        "Unsafe committed source path",
    )
    return subprocess.check_output(
        ["git", "-C", str(root), "show", revision + ":" + name], stderr=subprocess.PIPE
    )


def source_proof(root, study, plan, original):
    expected_inventory = {"scripts/smoke_qwen_mcp.py"} | {
        str(path.relative_to(root))
        for path in (root / "src/semantic_search").glob("*.py")
    }
    require(
        set(plan["source_sha256"]) == expected_inventory,
        "Exact harness/production source inventory",
    )
    for name, expected in plan["source_sha256"].items():
        require(
            type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected),
            "Typed source SHA256",
        )
        require(
            file_digest(root / name)
            == expected
            == digest(git_bytes(root, plan["source_commit"], name))
            == digest(git_bytes(root, "HEAD", name)),
            "Current/frozen/committed producer bytes: " + name,
        )
    require(study.is_relative_to(root), "Committed study must be inside repository")
    require(
        digest(
            git_bytes(root, "HEAD", str((study / "frozen-plan.json").relative_to(root)))
        )
        == file_digest(study / "frozen-plan.json"),
        "Frozen plan committed before native run",
    )
    producer = assignments(root / "scripts/smoke_qwen_mcp.py")
    source = ast.literal_eval(producer["SOURCE"].left) + "".join(
        f"\ndef format_noise_{index}(value):\n    return str(value) + '-{index}'\n"
        for index in range(58)
    )
    edited = source.replace("attempt, 60", "attempt, 90")
    fixture = {
        "source_root": "src",
        "path": "src/sample.py",
        "windows": 61,
        "initial_sha256": digest(source.encode()),
        "edited_sha256": digest(edited.encode()),
        "tracked_files": ["src/sample.py"],
        "query": ast.literal_eval(producer["QUERY"]),
        "target_symbol": "backoff_delay",
    }
    equal(plan["fixture"], fixture, "Frozen literal toy fixture reconstruction")
    tree = ast.parse((root / "src/semantic_search/qwen_reranker_core.py").read_text())
    original_plan = read(original / "frozen-plan.json")
    original_audit = read(original / "independent-audit.json")
    require(
        original_audit["checks_passed"] is True
        and original_audit["study_qualified"] is True
        and original_audit["clean_supervised_run"] is True
        and original_audit["passing_batch_sizes"] == [1],
        "Certified original B1 reference",
    )
    for name in ("frozen-plan.json", "reranker-provenance.json"):
        require(
            file_digest(original / name) == original_audit["artifact_sha256"][name],
            "Certified original reference binding",
        )
    equal(
        plan["runtime"]["runtime"]["packages"],
        original_plan["reranker"]["runtime"]["packages"],
        "Certified original primary runtime pins",
    )
    equal(
        plan["runtime"]["runtime"]["supporting_source_sha256"],
        original_plan["reranker"]["supporting_sources"],
        "Certified original supporting source pins",
    )
    old_source = git_bytes(
        root, original_plan["source_commit"], "scripts/prototype_qwen_reranker.py"
    )
    require(
        digest(old_source)
        == original_plan["implementation_sha256"]["scripts/prototype_qwen_reranker.py"],
        "Original scorer Git binding",
    )
    old_tree = ast.parse(old_source)

    def nodes(value):
        return {
            node.name: ast.dump(node, include_attributes=False)
            for node in value.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }

    current_nodes, old_nodes = nodes(tree), nodes(old_tree)
    require(
        all(current_nodes[name] == old_nodes[name] for name in CORE_NAMES),
        "Original promoted scorer AST",
    )

    def constants(value):
        return {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in value.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in CORE_CONSTANTS
        }

    core = constants(tree)
    equal(core, constants(old_tree), "Original prompt/checkpoint/runtime pins")
    warmup = ast.literal_eval(
        assignments(root / "src/semantic_search/qwen_worker.py")["WARMUP"]
    )
    return source, edited, core, warmup


def package_closure(site):
    result = {}
    for distribution in importlib.metadata.distributions(path=[str(site)]):
        name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        require(name not in result, "Duplicate native distribution")
        result[name] = distribution.version
    return result


def witness(path):
    status = path.stat()
    return [
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    ]


def python_metadata(python):
    code = (
        'import json,platform,sys;print(json.dumps({"python":platform.python_version(),'
        '"executable":sys.executable,"isolated":sys.flags.isolated,"no_site":sys.flags.no_site}))'
    )
    return decode(
        subprocess.check_output(
            [str(python), "-I", "-S", "-B", "-c", code], text=True, timeout=15
        )
    )


def runtime_proof(root, work, python, checkpoint, plan, result, core):
    owner = result["owner"]
    interpreter = owner["interpreter"]
    require(
        python == local_path(interpreter["invocation_path"], root, work)
        and python.resolve() == local_path(interpreter["resolved_binary"], root, work),
        "Explicit actual isolated interpreter binding",
    )
    require(
        python.name == "python" and python.parent.name == "bin" and python.is_file(),
        "Isolated Python invocation required",
    )
    venv = python.parent.parent
    config = dict(
        line.split(" = ", 1)
        for line in (venv / "pyvenv.cfg").read_text().splitlines()
        if " = " in line
    )
    require(
        config.get("include-system-site-packages") == "false",
        "Isolated Python configuration",
    )
    metadata = python_metadata(python)
    require(
        metadata["python"] == "3.12.15"
        and Path(metadata["executable"]).resolve() == python.resolve()
        and type(metadata["isolated"]) is int
        and metadata["isolated"] == 1
        and type(metadata["no_site"]) is int
        and metadata["no_site"] == 1,
        "Pinned isolated/no-site Python metadata",
    )
    site = venv / "lib/python3.12/site-packages"
    lock = root / "requirements-qwen-reranker.lock"
    require(
        lock.read_bytes() == git_bytes(root, "HEAD", str(lock.relative_to(root))),
        "Committed independent runtime closure lock",
    )
    pins = {
        re.sub(r"[-_.]+", "-", name).lower(): version
        for name, version in (
            line.split("==") for line in lock.read_text().splitlines() if line
        )
    }
    require(len(pins) == 34, "Exact 34-package closure lock")
    equal(package_closure(site), pins, "Observed complete isolated package closure")
    provenance = owner["ready"]["provenance"]
    for status in (
        owner["start_status"],
        owner["latest_status"],
        result["native_final_status"],
    ):
        equal(
            status["provenance"],
            provenance,
            "Actual provenance stable through lifecycle",
        )
    require(
        provenance["sequential_unpadded"] is True
        and type(provenance["batch_size"]) is int
        and provenance["batch_size"] == 1
        and type(provenance["max_total_tokens"]) is int
        and provenance["max_total_tokens"] == 8192
        and provenance["instruction"] == core["INSTRUCTION"]
        and provenance["scoring_core_sha256"]
        == plan["source_sha256"]["src/semantic_search/qwen_reranker_core.py"],
        "Original actual singleton core/prompt",
    )
    for key in ("load_s", "verification_s"):
        number(provenance[key], "Native startup elapsed")
    number(owner["startup_s"], "Owner startup elapsed")
    runtime = provenance["runtime"]
    equal(
        runtime["packages"],
        core["RUNTIME_PINS"],
        "Original scorer primary runtime package pins",
    )
    equal(
        runtime["packages"],
        plan["runtime"]["runtime"]["packages"],
        "Frozen primary runtime versions",
    )
    require(
        all(pins[name] == version for name, version in runtime["packages"].items())
        and runtime["errors"] == [],
        "Observed pinned native packages",
    )
    qwen = site / "mlx_lm/models/qwen3.py"
    require(
        local_path(runtime["qwen3_source_path"], root, work)
        == qwen
        == local_path(runtime["imported_qwen3_source_path"], root, work),
        "Actual imported Qwen origin matches explicit venv",
    )
    require(
        file_digest(qwen)
        == runtime["qwen3_source_sha256"]
        == runtime["imported_qwen3_source_sha256"]
        == plan["runtime"]["runtime"]["qwen3_source_sha256"]
        == core["QWEN3_SOURCE_SHA"],
        "Pinned imported Qwen source bytes",
    )
    equal(
        runtime["supporting_source_sha256"],
        plan["runtime"]["runtime"]["supporting_source_sha256"],
        "Frozen supporting Qwen bytes",
    )
    for name, expected in runtime["supporting_source_sha256"].items():
        require(
            Path(name).name == name and file_digest(qwen.with_name(name)) == expected,
            "Pinned supporting Qwen source",
        )
    for name, expected in plan["runtime"]["main_packages"].items():
        require(
            importlib.metadata.version(name) == expected,
            "Observed main package version: " + name,
        )
    model = provenance["model"]
    require(
        local_path(model["directory"], root, work) == checkpoint
        and model["weight_identity_verified"] is True
        and model["model_id"] == plan["runtime"]["model_id"] == core["MODEL_ID"]
        and model["revision"] == plan["runtime"]["revision"] == core["REVISION"],
        "Explicit pinned actual checkpoint identity",
    )
    require(
        set(model["files"])
        == set(model["file_witnesses"])
        == set(plan["runtime"]["checkpoint_files"])
        == set(core["PINS"]),
        "Exact four-file checkpoint inventory",
    )
    for name, (expected_size, expected_hash) in core["PINS"].items():
        actual, frozen = model["files"][name], plan["runtime"]["checkpoint_files"][name]
        require(
            actual.keys() == {"size", "sha256"}
            and type(actual["size"]) is int
            and actual["size"] == expected_size
            and actual["sha256"] == expected_hash,
            "Pinned measured checkpoint SHA256",
        )
        equal(actual["size"], frozen["size"], "Frozen checkpoint file size")
        if name == "model.safetensors":
            require(
                frozen["sha256"] is None and frozen["weight_bytes_read"] is False,
                "Honest CPU-only weight preflight",
            )
        else:
            equal(actual, frozen, "Frozen non-weight checkpoint metadata")
            require(
                file_digest(checkpoint / name) == expected_hash,
                "Actual pinned non-weight bytes",
            )
        saved = model["file_witnesses"][name]
        require(
            type(saved) is list
            and len(saved) == 6
            and local_path(saved[0], root, work) == checkpoint / name
            and all(type(value) is int for value in saved[1:]),
            "Typed actual checkpoint physical witness",
        )
        equal(
            witness(checkpoint / name),
            saved[1:],
            "Checkpoint unchanged after measured weight verification",
        )
        equal(saved[3], actual["size"], "Physical checkpoint size binding")
    return {
        "runtime_package_count": 34,
        "runtime_lock_sha256": file_digest(lock),
        "core_ast_equal_functions": list(CORE_NAMES),
        "weight_bytes_read_in_audit": False,
        "checkpoint_physical_witnesses_unchanged": True,
    }


def cleanup_proof(root, work, study, plan, result):
    owner = result["owner"]
    pid = owner["pid"]
    require(type(pid) is int and pid > 0, "Typed owned PID")
    for value in (
        owner["owned_group"],
        owner["ready"]["pid"],
        owner["ready"]["process_group"],
        owner["stop"]["pid"],
        owner["stop"]["owned_group"],
        owner["start_status"]["pid"],
        owner["latest_status"]["pid"],
        result["native_final_status"]["pid"],
    ):
        equal(value, pid, "Owned PID/group lifecycle identity")
    candidates = []
    for folder in (work / "worker-private").glob("owned-*"):
        if read(folder / "ready.json")["pid"] == pid:
            candidates.append(folder)
    require(len(candidates) == 1, "Unique actual owned private directory")
    private = candidates[0]
    require(
        (private.stat().st_mode & 0o777) == 0o700
        and not os.path.lexists(private / "token.secret"),
        "Actual private mode/auth removal",
    )
    equal(
        public(read(private / "ready.json"), root, work),
        owner["ready"],
        "Exact private/public ready witness",
    )
    equal(
        public(read(private / "stop.json"), root, work),
        owner["worker_stop"],
        "Exact private/public stop witness",
    )
    require(
        (private / "forwards.jsonl").read_bytes()
        == (study / "native-forwards.jsonl").read_bytes(),
        "Exact private/public ledger bytes",
    )
    require(
        set(owner["worker_source_sha256"]) == COPIED,
        "Exact trusted copied source inventory",
    )
    for name, expected in owner["worker_source_sha256"].items():
        require(
            expected
            == plan["source_sha256"]["src/semantic_search/" + name]
            == file_digest(private / "runtime/semantic_search" / name),
            "Actual copied worker source binding",
        )
    for operation in (
        lambda: os.kill(pid, 0),
        lambda: os.getpgid(pid),
        lambda: os.killpg(pid, 0),
    ):
        try:
            operation()
        except ProcessLookupError:
            pass
        else:
            raise ValueError("Actual owned PID/group disappearance unproved")
    url = urlparse(owner["ready"]["url"])
    require(
        url.scheme == "http"
        and url.hostname == "127.0.0.1"
        and type(url.port) is int
        and 0 < url.port <= 65535
        and not url.username
        and not url.password
        and url.path == ""
        and not url.query
        and not url.fragment,
        "Actual private loopback listener identity",
    )
    with socket.socket() as connection:
        connection.settimeout(0.2)
        require(
            connection.connect_ex((url.hostname, url.port)) != 0,
            "Actual native listener remains reachable",
        )
    return {
        "pid": pid,
        "owned_group": pid,
        "current_pid_and_group_absent": True,
        "current_listener_unreachable": True,
        "actual_auth_file_absent": True,
        "private_public_witnesses_exact": True,
        "clean_exit_code": 0,
        "forced_kill": False,
    }


def audit(
    study, work, python, checkpoint, *, root=ROOT, original=None, tokenizer_ranks=None
):
    original = original or root / "results/qwen-batch-parity-supervised-2026-10-13"
    require(
        work.is_relative_to(root / ".cache") and work.is_dir(),
        "Explicit private smoke directory inside ignored .cache",
    )
    plan, certificate, result, ledger = artifacts(study)
    source, edited, core, warmup = source_proof(root, study, plan, original)
    runtime = runtime_proof(root, work, python, checkpoint, plan, result, core)
    require(
        (work / "repo/src/sample.py").read_bytes() == edited.encode(),
        "Actual final saved fixture bytes",
    )
    tracked = subprocess.check_output(
        ["git", "-C", str(work / "repo"), "ls-files"], text=True
    ).splitlines()
    equal(tracked, plan["fixture"]["tracked_files"], "Actual tracked fixture inventory")
    from tokenizers import Tokenizer

    metrics = validate(
        plan,
        result,
        ledger,
        source,
        edited,
        Tokenizer.from_file(str(checkpoint / "tokenizer.json")),
        core,
        warmup,
        context_encoder=offline_encoding(tokenizer_ranks),
    )
    cleanup = cleanup_proof(root, work, study, plan, result)
    return {
        "version": 1,
        "checks_passed": True,
        "study_qualified": True,
        "classification": "Independent CPU audit of completed native HTTP/MCP/saved-edit integration",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "auditor_sha256": file_digest(Path(__file__)),
        "frozen_plan_sha256": file_digest(study / "frozen-plan.json"),
        "source_commit": plan["source_commit"],
        "native_calls": 0,
        "hosted_calls": 0,
        "native_imports_in_audit": 0,
        "metrics": metrics,
        "runtime": runtime,
        "explicit_paths": {
            "study": str(study),
            "work_dir": str(work),
            "reranker_python": str(python),
            "checkpoint": str(checkpoint),
        },
        "process_cleanup": cleanup,
        "offline_context_tokenizer": {
            "ranks_path": str(tokenizer_ranks or RANKS_PATH),
            "sha256": RANKS_SHA,
            "mergeable_rank_count": RANKS_COUNT,
            "network_reads": 0,
        },
        "artifact_sha256": {
            name: file_digest(study / name)
            for name in sorted(PUBLIC_ARTIFACTS | {"verification.json"})
        },
        "limits": [
            "Toy integration only; no retrieval benchmark, coding correctness, latency comparison or paid-token measurement.",
            "Observed model scalars and actual input hashes are checked without rerunning a model.",
            "The measured pinned weight hash is checked against unchanged physical file witnesses; weight bytes are not reread.",
            "The full installed package closure is an additional post-run check against the committed 34-package lock; the producer's frozen plan records three primary reranker packages.",
            "The smoke producer verified one MCP text block equal to the compact serializer at runtime; raw MCP text/block inventories are not retained. This audit independently checks retained structured HTTP/MCP equality and reconstructed compact token counts.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--reranker-python", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--context-tokenizer", type=Path, default=RANKS_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    destination = args.output.absolute()
    require(not destination.exists(), "Preserve prior audit; no overwrite")
    work = args.work_dir.resolve()
    try:
        result = audit(
            args.study.resolve(),
            work,
            args.reranker_python.absolute(),
            args.checkpoint.resolve(),
            tokenizer_ranks=args.context_tokenizer.resolve(),
        )
    except Exception as error:
        result = {
            "checks_passed": False,
            "error_type": type(error).__name__,
            "error": str(error),
            "auditor_sha256": file_digest(Path(__file__)),
            "native_calls": 0,
            "hosted_calls": 0,
            "native_imports_in_audit": 0,
            "weight_bytes_read_in_audit": False,
        }
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                public(result, ROOT, work), indent=2, sort_keys=True, allow_nan=False
            )
            + "\n"
        )
    print(
        json.dumps(
            {"checks_passed": result["checks_passed"], "audit": str(destination)}
        )
    )
    return 0 if result["checks_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
