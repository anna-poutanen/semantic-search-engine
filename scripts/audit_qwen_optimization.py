#!/usr/bin/env python3
"""Independent CPU audit of observed bounded Qwen batches and search delivery.

No producer, batch implementation, MLX, model weights, embedding or retrieval
runtime is imported. Native causal/right-padding claims are checked against
frozen implementation provenance and independently rebuilt token/layout receipts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import gzip
import hashlib
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "optimization_independent_original_evidence", ROOT / "scripts/audit_qwen_reranker_benchmark.py"
)
original = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(original)
BATCH_SIZES = (1, 2, 4, 8)
MAX_PADDED_TOKENS = 16384
COUNTER_KEYS = ("requests", "attempted_forwards", "completed_forwards", "attempted_batch_forwards",
                "completed_batch_forwards", "attempted_pairs", "completed_pairs",
                "completed_real_input_tokens", "completed_allocated_input_tokens", "completed_padding_tokens")
SCORE_KEYS = ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")
LOGIT_TOLERANCE = .0625
DIFFERENCE_TOLERANCE = .125
SIGNAL_TOLERANCE = .025
INVERSION_MARGIN = .25
SUPPORTING_RUNTIME_HASHES = {
    "base.py": "b782b2ac0f55ed8fa9c1afce1f36307d5d8714aad2c0991d63998047f7507174",
    "rope_utils.py": "06466d57dfe1ccc1e61ba595c0a8f2dd3a14bcb5c6a1d06c67591dc03128606b",
}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":")).encode()


def read(path):
    return json.loads(path.read_bytes())


def gz_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def strict_count(value, message, *, minimum=0):
    require(type(value) is int and value >= minimum, message)


def counters(value):
    require(isinstance(value, dict) and set(value) == set(COUNTER_KEYS), "Exact resident cumulative counter keys")
    for number in value.values():
        strict_count(number, "Strict nonnegative actual resident counter")
    require(value["attempted_forwards"] == value["attempted_batch_forwards"]
            and value["completed_forwards"] == value["completed_batch_forwards"]
            and value["attempted_forwards"] >= value["completed_forwards"]
            and value["attempted_pairs"] >= value["completed_pairs"]
            and value["completed_allocated_input_tokens"] == value["completed_real_input_tokens"]
            + value["completed_padding_tokens"], "Actual forward/pair/padding aliases and conservation")
    return value


def zero_counts():
    return dict.fromkeys(COUNTER_KEYS, 0)


def built_pair(query, source, tokenizer, config):
    """Independently construct the exact template and largest literal line prefix."""
    require(isinstance(query, str) and query.strip() and isinstance(source, str) and source,
            "Only nonempty literal query and source may enter the scoring template")
    query.encode("utf-8")
    source.encode("utf-8")

    def encode(text):
        value = tokenizer.encode(text, add_special_tokens=False)
        ids = list(value.ids if hasattr(value, "ids") else value)
        require(all(type(token) is int and token >= 0 for token in ids), "Strict nonnegative original token IDs")
        return ids

    prefix, suffix = encode(config["PREFIX"]), encode(config["SUFFIX"])
    header = f"<Instruct>: {config['INSTRUCTION']}\n<Query>: {query}\n<Document>: "
    physical = original.evidence.physical_lines(source)
    for count in range(len(physical), 0, -1):
        delivered = "".join(physical[:count])
        ids = prefix + encode(header + delivered) + suffix
        if len(ids) <= config["MAX_TOKENS"]:
            return {"input_ids": ids, "source_sha256": sha(source.encode()),
                    "delivered_sha256": sha(delivered.encode()), "source_lines": len(physical),
                    "delivered_lines": count, "source_utf8_bytes": len(source.encode()),
                    "delivered_utf8_bytes": len(delivered.encode()), "truncated": delivered != source,
                    "total_tokens": len(ids), "query_sha256": sha(query.encode())}
    raise ValueError("No complete literal source line fits the original pair token cap")


def expected_groups(built, batch_size, budget):
    require(type(batch_size) is int and batch_size in BATCH_SIZES
            and type(budget) is int and 1 <= budget <= MAX_PADDED_TOKENS, "Bounded strict B and padded-token budget")
    require(isinstance(built, list) and 1 <= len(built) <= 50, "One to fifty original logical pairs")
    lengths = [len(pair["input_ids"]) for pair in built]
    require(all(1 <= length <= min(8192, budget) for length in lengths), "Every real pair fits both token caps")
    order = list(range(len(built))) if batch_size == 1 else sorted(range(len(built)), key=lambda index: (lengths[index], index))
    groups, current = [], []
    for index in order:
        if current and (len(current) == batch_size or (len(current) + 1) * lengths[index] > budget):
            groups.append(current)
            current = []
        current.append(index)
    if current:
        groups.append(current)
    require(sorted(index for group in groups for index in group) == list(range(len(built))),
            "Stable groups must retain every original pair exactly once")
    return groups


def expected_layout(built, group):
    lengths = [len(built[index]["input_ids"]) for index in group]
    width, real = max(lengths), sum(lengths)
    allocated = len(group) * width
    require(allocated <= MAX_PADDED_TOKENS, "Actual padded forward bound")
    return {"actual_batch_size": len(group), "sequence_lengths": lengths, "padded_sequence_length": width,
            "real_input_tokens": real, "allocated_input_tokens": allocated, "padding_tokens": allocated - real,
            "padding_ratio": (allocated - real) / allocated, "pad_token_id": 0, "padding_side": "right",
            "position_offset": 0, "attention_mask": "native_causal",
            "last_token_positions": [length - 1 for length in lengths], "singleton_original_path": len(group) == 1}


def verify_scalar(score):
    require(isinstance(score, dict) and set(score) == set(SCORE_KEYS), "Exactly four finite yes/no scalar fields")
    original.verify_score(score)


def verify_batch_receipt(receipt, pairs, events, tokenizer, config, *, request_id,
                         batch_size, previous_counts, warmup=False, budget=MAX_PADDED_TOKENS):
    """Reconcile every actual backbone forward separately from logical pair scores."""
    require(isinstance(pairs, list) and all(isinstance(pair, dict) and set(pair) == {"query", "source"}
                                          for pair in pairs), "Scorer inputs contain literal query/source only")
    require(type(warmup) is bool and receipt["request_id"] == request_id and receipt["warmup"] is warmup
            and receipt["pairs_sha256"] == sha(canonical(pairs))
            and type(receipt["batch_size"]) is type(receipt["batch_size_requested"]) is int
            and receipt["batch_size"] == receipt["batch_size_requested"] == batch_size
            and type(receipt["max_padded_tokens"]) is int and receipt["max_padded_tokens"] == budget,
            "Actual authenticated request identity/source digest and exact batching configuration")
    built = [built_pair(pair["query"], pair["source"], tokenizer, config) for pair in pairs]
    groups = expected_groups(built, batch_size, budget)
    require(receipt["batches"] == receipt["batch_receipts"] and len(receipt["batches"]) == len(groups)
            and len(events) == 2 * len(groups) and len(receipt["results"]) == len(pairs),
            "Every planned actual forward has one started/completed receipt and every pair one score")
    previous_counts = counters(previous_counts)
    counts = deepcopy(previous_counts)
    counts["requests"] += 1
    forward_sum = 0.
    for number, (group, completed) in enumerate(zip(groups, receipt["batches"], strict=True)):
        identities = []
        for index in group:
            item = built[index]
            identities.append({"pair_index": index, **{key: item[key] for key in
                ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens", "truncated", "delivered_lines")},
                "input_ids_sha256": sha(canonical(item["input_ids"]))})
        counts["attempted_forwards"] += 1
        counts["attempted_batch_forwards"] += 1
        counts["attempted_pairs"] += len(group)
        layout = expected_layout(built, group)
        identity = {"request_id": request_id, "warmup": warmup, "batch_index": number,
                    "batch_forward_number": counts["attempted_forwards"], "batch_size_requested": batch_size,
                    "max_padded_tokens": budget, "pairs": identities, "layout": layout}
        started_event, completed_event = events[number * 2:number * 2 + 2]
        require(started_event == identity | {"event": "started"}
                and {key: completed[key] for key in identity} == identity
                and completed == completed_event and completed["event"] == "completed",
                "Immutable ordered actual started/completed batch identity, original indexes, tokens, pad and final positions")
        require(set(completed) == set(identity) | {"event", "elapsed_s", "scores"},
                "Exact completed observed native-forward receipt fields")
        # Python equality accepts booleans as integers: explicitly reject them.
        for record in (started_event, completed):
            require(type(record["warmup"]) is bool and type(record["batch_index"])
                    is type(record["batch_forward_number"]) is type(record["batch_size_requested"])
                    is type(record["max_padded_tokens"]) is int,
                    "Strict observed forward integer/boolean types")
            for pair in record["pairs"]:
                require(type(pair["pair_index"]) is type(pair["total_tokens"])
                        is type(pair["delivered_lines"]) is int and type(pair["truncated"]) is bool,
                        "Strict observed logical pair input identity types")
            for key in ("actual_batch_size", "padded_sequence_length", "real_input_tokens", "allocated_input_tokens",
                        "padding_tokens", "pad_token_id", "position_offset"):
                strict_count(record["layout"][key], "Strict actual native batch integer layout")
            require(all(type(value) is int for value in record["layout"]["sequence_lengths"]
                        + record["layout"]["last_token_positions"])
                    and type(record["layout"]["singleton_original_path"]) is bool,
                    "Strict actual native real lengths/final token positions")
            original.finite(record["layout"]["padding_ratio"], "Finite actual padding ratio", minimum=0)
        require(len(completed["scores"]) == len(group), "One scalar score per real batch row")
        for index, score in zip(group, completed["scores"], strict=True):
            verify_scalar(score)
            result = receipt["results"][index]
            expected_metadata = {key: value for key, value in built[index].items() if key != "input_ids"}
            require(set(result) == set(expected_metadata) | set(SCORE_KEYS)
                    and {key: result[key] for key in expected_metadata} == expected_metadata
                    and {key: result[key] for key in SCORE_KEYS} == score,
                    "Original result order preserves exact real prefix metadata and observed batch scalar")
            require(type(result["truncated"]) is bool and all(type(result[key]) is int for key in
                    ("source_lines", "delivered_lines", "source_utf8_bytes", "delivered_utf8_bytes", "total_tokens")),
                    "Strict final real-prefix lengths and truncation")
        original.finite(completed["elapsed_s"], "Finite actual evaluated native forward interval", minimum=0)
        forward_sum += completed["elapsed_s"]
        counts["completed_forwards"] += 1
        counts["completed_batch_forwards"] += 1
        counts["completed_pairs"] += len(group)
        counts["completed_real_input_tokens"] += layout["real_input_tokens"]
        counts["completed_allocated_input_tokens"] += layout["allocated_input_tokens"]
        counts["completed_padding_tokens"] += layout["padding_tokens"]
    require(counters(receipt["counts"]) == counts, "Actual cumulative logical-pair/native-forward/token counters advance without caching")
    for key in ("forward_s", "tokenization_s", "batch_preparation_s", "server_scoring_s"):
        original.finite(receipt[key], "Finite actual request interval: " + key, minimum=0)
    require(math.isclose(forward_sum, receipt["forward_s"], rel_tol=1e-12, abs_tol=1e-12)
            and receipt["server_scoring_s"] + 1e-12 >= forward_sum + receipt["tokenization_s"] + receipt["batch_preparation_s"],
            "Measured scoring interval contains all actual evaluated forwards, tokenization and batch preparation")
    return counts, built


def parity(reference, sample):
    """Preserve failed B and all near-tie changes using fixed BF16 gate tolerances."""
    require(isinstance(reference, list) and reference and len(reference) == len(sample), "Matched nonempty parity pair population")
    errors, violations = [], []
    for index, (before, after) in enumerate(zip(reference, sample, strict=True)):
        verify_scalar({key: before[key] for key in SCORE_KEYS})
        verify_scalar({key: after[key] for key in SCORE_KEYS})
        metadata_before = {key: value for key, value in before.items() if key not in SCORE_KEYS}
        metadata_after = {key: value for key, value in after.items() if key not in SCORE_KEYS}
        require(metadata_before == metadata_after, "Parity must use identical literal constructed source/token prefix inputs")
        delta = {key: abs(after[key] - before[key]) for key in SCORE_KEYS}
        errors.append(delta)
        if (delta["yes_logit"] > LOGIT_TOLERANCE or delta["no_logit"] > LOGIT_TOLERANCE
                or delta["logit_difference"] > DIFFERENCE_TOLERANCE or delta["yes_rank_signal"] > SIGNAL_TOLERANCE):
            violations.append({"pair_index": index, "absolute_error": delta})
    before_order = sorted(range(len(reference)), key=lambda index: (-reference[index]["logit_difference"], index))
    after_order = sorted(range(len(sample)), key=lambda index: (-sample[index]["logit_difference"], index))
    after_rank = {index: rank for rank, index in enumerate(after_order)}
    inversions = []
    for rank, left in enumerate(before_order):
        for right in before_order[rank + 1:]:
            if after_rank[left] > after_rank[right]:
                margin = reference[left]["logit_difference"] - reference[right]["logit_difference"]
                inversions.append({"before_higher_pair_index": left, "before_lower_pair_index": right,
                                   "b1_score_margin": margin, "outside_near_tie_band": margin > INVERSION_MARGIN})
    missing, entering = set(before_order[:5]) - set(after_order[:5]), set(after_order[:5]) - set(before_order[:5])
    membership_violations = [{"missing_pair_index": left, "entering_pair_index": right,
                             "b1_score_margin": reference[left]["logit_difference"] - reference[right]["logit_difference"]}
                            for left in sorted(missing) for right in sorted(entering)
                            if reference[left]["logit_difference"] - reference[right]["logit_difference"] > INVERSION_MARGIN]
    return {"qualified": not violations and not membership_violations and not any(item["outside_near_tie_band"] for item in inversions),
            "pairs": len(reference), "absolute_error_max": {key: max(item[key] for item in errors) for key in SCORE_KEYS},
            "scalar_violations": violations, "rank_inversions": inversions,
            "before_order": before_order, "after_order": after_order,
            "top_five_membership_changed": bool(missing or entering), "top_five_missing_indexes": sorted(missing),
            "top_five_entering_indexes": sorted(entering), "top_five_outside_near_tie_violations": membership_violations}


def distribution(values):
    require(values, "Nonempty actual warm latency sample population")
    for value in values:
        original.finite(value, "Finite observed warm interval", minimum=0)
    ordered = sorted(values)
    return {"samples": len(values), "median_s": statistics.median(values), "p95_s": ordered[math.ceil(.95 * len(values)) - 1],
            "min_s": ordered[0], "max_s": ordered[-1]}


def quality(cases):
    unique = [case for case in cases if case["repetition"] == 1]
    require(len({(case["dataset"], case["query_id"]) for case in unique}) == len(unique), "Unique first-pass quality only")
    positive = [case for case in unique if case["kind"] == "present"]
    absent = [case for case in unique if case["kind"] == "absent"]
    multi = [case for case in positive if case["multi_file"]]
    return {"unique_queries": len(unique), "present": len(positive), "absent": len(absent),
            "complete": sum(None not in case["evidence_ranks"] for case in positive),
            "covered_groups": sum(sum(rank is not None for rank in case["evidence_ranks"]) for case in positive),
            "required_groups": sum(len(case["evidence_ranks"]) for case in positive),
            "multi_file_queries": len(multi), "multi_file_complete": sum(None not in case["evidence_ranks"] for case in multi),
            "absent_statuses": dict(Counter(case["match_status"] for case in absent)),
            "warm_latency": distribution([case["elapsed_s"] for case in cases])}


def verify_model_provenance(root, plan, provenance, config):
    identity = provenance["model"]
    checkpoint = root / ".cache/models/qwen3-reranker-0.6b-original"
    require(original.actual_path(root, identity["directory"]) == checkpoint.resolve()
            and identity["model_id"] == plan["reranker"]["model_id"] == config["MODEL_ID"]
            and identity["revision"] == plan["reranker"]["revision"] == config["REVISION"]
            and identity["weight_identity_verified"] is True,
            "Exact actual original BF16 checkpoint and full-byte verification receipt")
    require(set(identity["files"]) == set(identity["file_witnesses"]) == set(config["PINS"]), "Exact checkpoint file inventory")
    for name, (size, digest) in config["PINS"].items():
        path = checkpoint / name
        stat = path.stat()
        require(stat.st_size == size and identity["files"][name] == {"size": size, "sha256": digest},
                "Actual original checkpoint byte-size/hash verification")
        witness = identity["file_witnesses"][name]
        require(original.actual_path(root, witness[0]) == path.resolve()
                and witness[1:] == [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns],
                "Original loaded checkpoint unchanged inode/stat binding")
        frozen = plan["reranker"]["checkpoint_files"][name]
        if name.endswith(".safetensors"):
            require(frozen == {"size": size, "sha256": None, "weight_bytes_read": False}, "Zero-weight-read metadata freeze")
        else:
            require(sha(path.read_bytes()) == digest and frozen == {"size": size, "sha256": digest},
                    "CPU independently rehashed pinned tokenizer/config")
    runtime = provenance["runtime"]
    require(runtime["errors"] == [] and runtime["packages"] == config["RUNTIME_PINS"]
            and runtime["qwen3_source_sha256"] == runtime["imported_qwen3_source_sha256"] == config["QWEN3_SOURCE_SHA"]
            and plan["reranker"]["runtime"]["packages"] == runtime["packages"]
            and plan["reranker"]["runtime"]["qwen3_source_sha256"] == config["QWEN3_SOURCE_SHA"],
            "Actual frozen and imported original Qwen3/native runtime identities")
    site = root / ".cache/reranker-experiment/.venv/lib/python3.12/site-packages"
    native = site / "mlx_lm/models/qwen3.py"
    require(original.actual_path(root, runtime["qwen3_source_path"])
            == original.actual_path(root, runtime["imported_qwen3_source_path"]) == native.resolve()
            and sha(native.read_bytes()) == config["QWEN3_SOURCE_SHA"], "Actual pinned imported Qwen3 source")
    for name, digest in SUPPORTING_RUNTIME_HASHES.items():
        path = site / "mlx_lm/models" / name
        metadata = runtime["batching_supporting_sources"][name]
        require(original.actual_path(root, metadata["path"]) == path.resolve()
                and metadata["sha256"] == digest == sha(path.read_bytes()),
                "Pinned native causal attention/RoPE supporting source bytes: " + name)
    packages = {distribution.metadata["Name"]: distribution.version
                for distribution in importlib.metadata.distributions(path=[str(site)])}
    require(provenance["packages"] == packages == plan["reranker"]["packages"], "Exact actual isolated distribution closure")
    require(provenance["instruction"] == config["INSTRUCTION"]
            and provenance["max_total_tokens"] == config["MAX_TOKENS"] == 8192
            and provenance["batch_sizes"] == list(BATCH_SIZES) and provenance["max_padded_tokens"] == MAX_PADDED_TOKENS
            and provenance["padding_side"] == "right" and provenance["position_offset"] == 0
            and provenance["attention_mask"] == "native_causal" and provenance["cache"] is False
            and provenance["length_sort"] == "B1_original_order_else_ascending_then_original_index",
            "Frozen original source-only template/caps, native causal padding, no KV cache and grouping rule")
    for key in ("verification_s", "load_s"):
        original.finite(provenance[key], "Separate actual load/full-byte verification interval", minimum=0)
    from tokenizers import Tokenizer
    require(importlib.metadata.version("tokenizers") == config["RUNTIME_PINS"]["tokenizers"], "Pinned independent CPU tokenizer runtime")
    return Tokenizer.from_file(str(checkpoint / "tokenizer.json"))


def configuration(value):
    require(isinstance(value, dict) and set(value) == {"id", "retrieval", "shortlist", "batch_size"},
            "Exact frozen configuration dictionary")
    kind, k, b = value["retrieval"], value["shortlist"], value["batch_size"]
    require(kind in ("dense", "hybrid") and type(k) is int and k in (5, 8, 10, 16, 24, 32, 40, 50)
            and type(b) is int and b in (0,) + BATCH_SIZES and (b or k == 50),
            "Valid bounded shortlist/batch or unchanged full-pool baseline")
    identifier = kind if b == 0 else f"{kind}_k{k}_b{b}"
    require(value["id"] == identifier, "Exact config ID follows actual declared retrieval/K/B")
    return value


def check_sources(root, plan):
    """Rebuild pinned extraction, original full gold and the exact timing panel."""
    from semantic_search.corpus import _source_files, extract_snippets
    from semantic_search.languages import decode_source, extractor_fingerprint
    from semantic_search.relations import ReferenceGraph

    manifests = ("benchmarks/httpx_reranker_manifest.json",) if plan["stage"] == "fresh" else (
        "benchmarks/quality_manifest.json", "benchmarks/language_quality_manifest.json",
        "benchmarks/koa_clause_holdout_manifest.json")
    require(set(plan["manifest_sha256"]) == set(manifests), "All exact prospective source manifests")
    entries = []
    for name in manifests:
        path = original.evidence.located(root, name)
        require(sha(path.read_bytes()) == plan["manifest_sha256"][name], "Frozen original source manifest bytes")
        entries.extend(read(path)["datasets"])
    require([entry["id"] for entry in entries] == (["httpx"] if plan["stage"] == "fresh" else
            ["flask", "requests", "click", "starlette", "rich", "express", "zod", "koa"]),
            "Exact declared stage repositories")
    require([item["id"] for item in plan["sources"]] == [entry["id"] for entry in entries]
            and plan["extractor"] == extractor_fingerprint(), "Actual unchanged complete extractor inventory")
    datasets, catalog, counts, panel_keys = {}, [], Counter(), set()
    for entry, frozen in zip(entries, plan["sources"], strict=True):
        label_path = original.evidence.located(root, entry["dataset"])
        require(sha(label_path.read_bytes()) == entry["sha256"], "Exact original label bytes")
        labels = read(label_path)
        repo = original.evidence.located(root, entry["corpus"])
        require(subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
                == entry["revision"] == labels["revision"]
                and not subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True).strip(),
                "Clean exact pinned committed corpus")
        require(labels["repo_url"] == entry["repo_url"] and labels["source_root"] == entry["source_root"],
                "Original exact source scope identity")
        paths = _source_files(repo, repo / entry["source_root"], entry.get("source_exclusions", []))
        raw = {path.relative_to(repo).as_posix(): path.read_bytes() for path in paths}
        hashes = {path: sha(value) for path, value in raw.items()}
        require(hashes == frozen["source_file_sha256"], "Complete exact source membership/file byte hashes")
        for path, value in raw.items():
            require(value == subprocess.check_output(["git", "-C", str(repo), "show", entry["revision"] + ":" + path]),
                    "Saved source differs from pinned committed bytes")
        decoded = {path: decode_source(path, value) for path, value in raw.items()}
        sources = {path: original.evidence.physical_lines(value) for path, value in decoded.items()}
        snippets = extract_snippets(repo, entry["source_root"], max_lines=160, overlap_lines=40,
            source_exclusions=entry.get("source_exclusions", []), sources=decoded)
        inventory = [{"id": snippet.id, "path": snippet.path, "symbol": snippet.symbol,
                      "start_line": snippet.start_line, "end_line": snippet.end_line,
                      "source_hash": snippet.content_hash} for snippet in snippets]
        require(inventory == frozen["window_inventory"] and len(inventory) == frozen["windows"]
                and sha(canonical(inventory)) == frozen["window_inventory_sha256"],
                "Reconstructed unchanged extraction windows and order")
        require([query["id"] for query in labels["queries"]]
                == [query["query_id"] for query in frozen["queries"]], "All original query IDs remain untouched")
        queries = {}
        for query, saved in zip(labels["queries"], frozen["queries"], strict=True):
            gold = deepcopy(original.groups(query, sources, hashes))
            for group in gold:
                for target in group:
                    lo, hi = target["evidence_lines"]
                    target["evidence_sha256"] = sha("".join(sources[target["path"]][lo - 1:hi]).encode())
            eligible = [[window["id"] for window in inventory if original.ranks([window], [group])[0] is not None]
                        for group in gold]
            require(saved == {"query_id": query["id"], "kind": query["kind"], "groups": gold, "eligible_windows": eligible}
                    and all(eligible), "Original complete gold spans/hash/OR-AND eligibility unchanged")
            multi = len(gold) > 1 and len({target["path"] for group in gold for target in group}) > 1
            queries[query["id"]] = {"query": query["query"], "kind": query["kind"], "groups": gold, "multi_file": multi}
            catalog.append({"dataset": entry["id"], "query_id": query["id"], "query": query["query"]})
            counts[query["kind"]] += 1
            counts["groups"] += len(gold)
            counts["multi_file"] += multi
        positives = [query for query in labels["queries"] if query["kind"] == "present"]
        absent = [query for query in labels["queries"] if query["kind"] == "absent"]
        if plan["stage"] == "grid":
            require(len(positives) >= 2 and absent, "Timing panel has required original query kinds")
            panel_keys.update((entry["id"], query["id"]) for query in (positives[0], positives[-1], absent[0]))
        classifications = ("fresh source-reviewed retrieval repository",) if plan["stage"] == "fresh" else (
            "previously observed development", "previously observed development, including Koa")
        require(frozen["classification"] in classifications, "Current source-reviewed-fresh versus observed-development classification")
        datasets[entry["id"]] = {"entry": entry, "sources": sources, "inventory": {item["id"]: item for item in inventory},
                                "snippets": {snippet.id: snippet for snippet in snippets},
                                "graph": ReferenceGraph(decoded, snippets), "queries": queries}
    require((len(catalog), counts["present"], counts["absent"], counts["groups"], counts["multi_file"])
            == ((15, 12, 3, 28, 5) if plan["stage"] == "fresh" else (144, 118, 26, 148, 21)),
            "Exact complete prospective source query/group/multifile matrix")
    expected_catalog = [row for row in catalog if (row["dataset"], row["query_id"]) in panel_keys] if plan["stage"] == "grid" else catalog
    require(plan["query_catalog"] == expected_catalog, "Exact original ordered catalog or predeclared timing panel")
    return datasets


def verify_frozen_plan(root, study):
    plan = read(study / "frozen-plan.json")
    require(plan["version"] == 1 and plan["stage"] in ("parity", "grid", "development", "fresh")
            and plan["settings"] == original.evidence.CAPS and plan["repetitions"] == 3
            and plan["primary_quality_repetition"] == 1
            and plan["query_cache"] is plan["score_cache"] is plan["weights_loaded_in_freeze"] is False
            and plan["model_calls_in_freeze"] == 0
            and plan["batch_sizes"] == list(BATCH_SIZES) and plan["max_padded_tokens"] == MAX_PADDED_TOKENS
            and plan["generic_reranker_warmup_pairs"] == 32 and plan["generic_reranker_warmup_forwards"] == 15,
            "Exact prospectively frozen stages/caps/B/pair warmups and zero-inference freeze")
    require(plan["parity_limits"] == {"yes_logit": LOGIT_TOLERANCE, "no_logit": LOGIT_TOLERANCE,
            "logit_difference": DIFFERENCE_TOLERANCE, "yes_rank_signal": SIGNAL_TOLERANCE, "inversion_margin": INVERSION_MARGIN},
            "Unrelaxed fixed BF16 parity tolerances")
    require(plan["reranker"]["supporting_sources"] == SUPPORTING_RUNTIME_HASHES,
            "Prospective native causal/RoPE supporting source pins")
    frozen_at = datetime.fromisoformat(plan["frozen_at_utc"])
    require(frozen_at.tzinfo is not None, "Aware actual prospective freeze timestamp")
    for name, digest in plan["implementation_sha256"].items():
        path = original.evidence.located(root, name)
        require(sha(path.read_bytes()) == digest, "Frozen producer/helper/core source bytes: " + name)
        committed = subprocess.check_output(["git", "-C", str(root), "show", plan["source_commit"] + ":" + name])
        require(sha(committed) == digest, "Committed prospective implementation bytes: " + name)
    for name, digest in plan["decision_inputs"].items():
        require(sha(original.evidence.located(root, name).read_bytes()) == digest,
                "Explicit predeclared decision input bytes unchanged: " + name)
    for origin in plan["semantic_module_origins"].values():
        require(origin["sha256"] == plan["implementation_sha256"][origin["project_relative_path"]],
                "Frozen actual imported production module origin binding")
    require(plan["packages"] == {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
            "Exact actual main-runtime distribution closure")
    configs = [configuration(value) for value in plan["conditions"]]
    require(configs and len({value["id"] for value in configs}) == len(configs), "Nonempty unique declared configurations")
    if plan["stage"] != "parity":
        require(plan["parity_sets"] == [], "Retrieval stage cannot reuse parity scores")
        expected = []
        catalog = plan["query_catalog"]
        for dataset in dict.fromkeys(row["dataset"] for row in catalog):
            selected = [row for row in catalog if row["dataset"] == dataset]
            for repetition in range(1, 4):
                for number, query in enumerate(selected):
                    offset = (number + repetition - 1) % len(configs)
                    for config in configs[offset:] + configs[:offset]:
                        expected.append({"dataset": dataset, "query_id": query["query_id"],
                                         "condition": config["id"], "repetition": repetition})
        require(plan["schedule"] == expected and plan["planned_requests"] == len(expected)
                and plan["planned_pair_scores"] == sum(len(catalog) * 3 * value["shortlist"]
                                                          for value in configs if value["batch_size"]),
                "Exact prospectively rotated actual-search and logical-pair schedule")
    return plan


def verify_startup_cleanup(root, study, plan, counts):
    plan_hash = sha((study / "frozen-plan.json").read_bytes())
    frozen_at = datetime.fromisoformat(plan["frozen_at_utc"])
    starts = [read(study / name) for name in ("run-start.json", "reranker-start.json")]
    for start in starts:
        require(start["frozen_plan_sha256"] == plan_hash
                and datetime.fromisoformat(start["started_at_utc"]) >= frozen_at,
                "Actual model/main startup follows exact prospective frozen plan")
    stop, supervisor = read(study / "reranker-stop.json"), read(study / "supervisor.json")
    require(supervisor["supervisor_sha256"] == plan["implementation_sha256"]["scripts/supervise_qwen_optimization.py"],
            "Actual owner source matches prospective committed supervisor")
    require({key: stop[key] for key in COUNTER_KEYS} == counts and stop["clean_stop"] is True
            and stop["listener_closed"] is True, "Actual final native/pair/token totals and clean resident socket close")
    for key in ("mlx_active_memory_after_cleanup", "mlx_process_lifetime_peak_bytes"):
        strict_count(stop[key], "Actual MLX allocator integer receipt")
    require(stop["mlx_active_memory_after_cleanup"] <= stop["mlx_process_lifetime_peak_bytes"], "Allocator cleanup bound")
    require(supervisor["success"] is True and supervisor["supervisor_exit_code"] == 0
            and supervisor["frozen_plan_sha256"] == plan_hash and supervisor["owned_sessions"] is True
            and supervisor["timed_out"] is supervisor["forced_kill"] is False
            and supervisor["main_exit_code"] == supervisor["resident_exit_code"] == 0
            and supervisor["main_owned_group_stopped"] is supervisor["resident_owned_group_stopped"] is True
            and supervisor["private_auth_token_removed"] is True
            and supervisor["private_auth_token_mode"] == "0o600" and supervisor["private_cache_mode"] == "0o700",
            "Successful bounded owner shutdown, distinct fresh sessions and private authentication removal")
    require(type(supervisor["main_pid"]) is type(supervisor["resident_pid"]) is int
            and supervisor["main_pid"] > 0 and supervisor["resident_pid"] > 0
            and supervisor["main_pid"] != supervisor["resident_pid"], "Two distinct actual root-owned child identities")
    for label in ("main", "resident", "caffeinate"):
        if label == "caffeinate" and "caffeinate_pid" not in supervisor:
            continue
        record = supervisor[label + "_stop"]
        require(record["pid"] == record["owned_group"] == supervisor[label + "_pid"]
                and record["owned_group_stopped"] is True and record["forced_kill"] is False
                and record["errors"] == [], "Clean actual root-owned process-group shutdown: " + label)
    require(not (root / ".cache" / study.name / "token.secret").exists(), "Actual private authentication file removed")
    original.finite(supervisor["elapsed_seconds"], "Observed complete bounded supervisor interval", minimum=0)
    original.finite(supervisor["timeout_seconds"], "Actual positive finite whole-run bound", minimum=1)
    require(supervisor["ready_timeout_seconds"] == 180 and supervisor["term_grace_seconds"] == 30
            and supervisor["kill_grace_seconds"] == 10, "Fixed actual readiness and cooperative/final cleanup bounds")
    require(datetime.fromisoformat(supervisor["started_utc"]) >= frozen_at
            and datetime.fromisoformat(supervisor["finished_utc"]) >= datetime.fromisoformat(stop["stopped_at_utc"])
            and datetime.fromisoformat(stop["stopped_at_utc"]) >= max(datetime.fromisoformat(start["started_at_utc"]) for start in starts),
            "Actual freeze/start/stop/owner chronology")
    finished = datetime.fromisoformat(read(study / "completion.json")["finished_at_utc"])
    require(datetime.fromisoformat(starts[0]["started_at_utc"]) <= finished
            <= datetime.fromisoformat(stop["stopped_at_utc"]), "Main actual completion precedes resident cooperative close")
    return {"supervisor_elapsed_seconds": supervisor["elapsed_seconds"], "allocator": {key: stop[key] for key in
            ("mlx_active_memory_after_cleanup", "mlx_process_lifetime_peak_bytes", "memory_scope")}}


def audit_warmups(study, tokenizer, config, events):
    toy = [{"query": "Locate addition of two values.", "source": "def add(left, right):\n"
           + "    # fixed warmup\n" * index + "    return left + right\n"} for index in range(8)]
    warmups = read(study / "reranker-warmup.json")
    require(len(warmups) == 4, "Every fixed B has a separately excluded actual warmup")
    counts, offset = zero_counts(), 0
    for size, receipt in zip(BATCH_SIZES, warmups, strict=True):
        count = 2 * len(receipt["batches"])
        counts, _ = verify_batch_receipt(receipt, toy, events[offset:offset + count], tokenizer, config,
            request_id="generic-warmup-b" + str(size), batch_size=size, previous_counts=counts, warmup=True)
        offset += count
    require(counts["completed_pairs"] == 32 and counts["completed_forwards"] == 15,
            "Exactly 32 separately excluded logical warmup pairs and 15 evaluated native forwards")
    return counts, offset


def audit_parity_inputs(root, plan, datasets):
    old = root / "results/qwen-reranker-full-2026-10-13"
    old_audit = read(old / "independent-audit.json")
    require(old_audit["checks_passed"] is True and old_audit["frozen_plan_sha256"] == plan["old_study_plan_sha256"],
            "Input parity pools derive from the completed independently audited full study")
    for name in ("frozen-plan.json", "candidate-pools.jsonl.gz", "source-content.jsonl.gz"):
        require(sha((old / name).read_bytes()) == old_audit["artifact_sha256"][name], "Unchanged old parity source-pool artifact")
    contents = {row["sha256"]: row["source"] for row in gz_rows(old / "source-content.jsonl.gz")}
    pools = {row["pool_key"]: row for row in gz_rows(old / "candidate-pools.jsonl.gz")}
    expected = []
    for dataset, data in datasets.items():
        query_id, query = next((identifier, value) for identifier, value in data["queries"].items() if value["kind"] == "present")
        pool = pools[dataset + "/" + query_id + "/dense"]
        require(sha(canonical(pool["windows"])) == pool["pool_sha256"], "Unchanged immutable original ordered dense parity pool")
        raw = [{**window, "source": contents[window["source_hash"]]} for window in pool["windows"]]
        original.verify_raw(raw, data["inventory"], data["sources"], score_kind="cosine")
        expected.append({"id": dataset + "/" + query_id, "classification": "fixed development parity input",
                         "pairs": [{"query": query["query"], "source": window["source"]} for window in raw]})
    synthetic = [{"query": "Locate the Unicode greeting return.",
                  "source": "def greet():\n" + "    # mixed-length µ fixture\n" * number + "    return 'héllo 🤖'\n"}
                 for number in range(14)]
    synthetic += [deepcopy(synthetic[0]), {"query": "Locate the repeated comments and final return.",
                  "source": "def repeated():\n    # " + "alpha " * 4500 + "\n    # " + "beta " * 4500 + "\n    return 1\n"}]
    expected.append({"id": "synthetic/mixed-length-unicode-duplicate-clipping",
                     "classification": "predeclared synthetic padding/clipping diagnostic", "pairs": synthetic})
    require(plan["parity_sets"] == expected, "Exact first-positive dense source pools and original synthetic parity literals")
    schedule = [{"parity_set": group["id"], "batch_size": size} for group in expected for size in BATCH_SIZES]
    require(plan["schedule"] == schedule and plan["planned_requests"] == len(schedule)
            and plan["planned_pair_scores"] == sum(len(group["pairs"]) for group in expected) * 4,
            "Exact prospective synthetic/development parity pair/native grouping schedule")


def changes(before, after):
    left = {(item["dataset"], item["query_id"]): item for item in before if item["repetition"] == 1}
    right = {(item["dataset"], item["query_id"]): item for item in after if item["repetition"] == 1}
    require(len(left) == sum(item["repetition"] == 1 for item in before)
            and len(right) == sum(item["repetition"] == 1 for item in after) and left.keys() == right.keys(),
            "Matched unique original first-pass comparison queries")
    result = {key: [] for key in ("complete_gains", "complete_regressions", "group_changes", "rank_changes")}
    for key, a in left.items():
        b = right[key]
        require(a["kind"] == b["kind"], "Matched comparison kinds")
        if a["kind"] == "absent":
            continue
        ar, br = a["evidence_ranks"], b["evidence_ranks"]
        item = {"dataset": key[0], "query_id": key[1], "before": ar, "after": br}
        if None in ar and None not in br:
            result["complete_gains"].append(item)
        if None not in ar and None in br:
            result["complete_regressions"].append(item)
        if [rank is not None for rank in ar] != [rank is not None for rank in br]:
            result["group_changes"].append(item)
        if ar != br:
            result["rank_changes"].append(item)
    return result


def embedding_counts(value):
    expected = {"completed_query_calls", "completed_document_calls", "document_texts", "attempted_query_calls",
                "attempted_document_calls", "attempted_document_texts"}
    require(isinstance(value, dict) and set(value) == expected, "Exact actual main embedding counter keys")
    for number in value.values():
        strict_count(number, "Strict actual nonnegative embedding count")
    require(value["completed_query_calls"] == value["attempted_query_calls"]
            and value["completed_document_calls"] == value["attempted_document_calls"]
            and value["document_texts"] == value["attempted_document_texts"], "Every actual embedding attempt completed")
    return value


def verify_setup(root, study, plan, data, setup, before):
    require(setup["counts_before"] == before and setup["backend_closed"] is True
            and setup["excluded_from_latency"] is True, "Owned corpus setup has continuous counters, excluded warmup and actual backend close")
    after = embedding_counts(setup["counts_after"])
    require(after["completed_query_calls"] == before["completed_query_calls"] + 1,
            "Exactly one separately excluded generic query embedding warmup per corpus")
    original.finite(setup["index_and_embedding_warmup_s"], "Separate actual index/generic query warmup interval", minimum=0)
    stats = setup["indexstats"]
    require(stats["total_windows"] == len(data["inventory"]) and stats["source_files"] == len(data["sources"]),
            "Setup actual index has complete frozen source membership/windows")
    for key in ("indexed_windows", "reused_windows", "deleted_windows"):
        strict_count(stats[key], "Strict observed actual setup index window counters")
    require(stats["indexed_windows"] + stats["reused_windows"] == stats["total_windows"], "Complete actual setup indexed/reused windows")
    require(after["document_texts"] - before["document_texts"] == stats["indexed_windows"],
            "Exact actual freshly embedded source-window/text count")
    require(after["completed_document_calls"] - before["completed_document_calls"]
            == (1 if stats["indexed_windows"] else 0), "Exactly one API call for setup documents or none for valid seed vectors")
    seed = setup["seed"]
    require(type(seed["seeded"]) is bool, "Strict actual seed status")
    if seed["seeded"]:
        expected = ".cache/indexes/" + plan["cache_seed"][setup["dataset"]]
        require(seed["source"] == expected and sha(original.evidence.located(root, expected).read_bytes()) == seed["source_main_file_sha256"]
                and seed["method"] == "SQLite read-only connection backup into new owned destination",
                "Actual unchanged seed SQLite bytes and separate owned destination")
    require((root / ".cache/indexes" / (study.name + "-" + setup["dataset"] + ".sqlite3")).is_file(),
            "Separately owned per-stage/corpus actual persistent index exists")
    return after


def audit_retrieval(root, study, plan, datasets, completion, tokenizer, config, events, counts, offset):
    weights = read(study / "embedding-weight-verification.json")
    expected_weights = {name: {"sha256": record["expected_sha256_from_provenance"], "bytes": record["bytes"],
                             "matches_pinned_provenance": True}
                        for name, record in plan["embedding_checkpoint"].items() if name.endswith(".safetensors")}
    require(weights == expected_weights and weights, "Actual main query-embedding full-byte weight verification")
    offline = plan["offline_tokenizer"]
    require(offline["network"] is offline["cache_writes"] is False and offline["sha256"]
            == "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
            and offline["tiktoken"] == plan["packages"]["tiktoken"], "Prospective exact offline compact tokenizer identity")
    cache = root / ".cache/tokenizers"
    encoding = original.evidence.encoder(root, {"runtime": {"tiktoken": offline["tiktoken"],
        "tokenizer_cache_sha256": {path.name: sha(path.read_bytes()) for path in cache.iterdir() if path.is_file()}}})
    contents = {}
    for item in gz_rows(study / "source-content.jsonl.gz"):
        require(set(item) == {"sha256", "source"} and item["sha256"] not in contents
                and sha(item["source"].encode()) == item["sha256"], "Unique literal content-addressed raw source")
        contents[item["sha256"]] = item["source"]
    pools = {}
    for pool in gz_rows(study / "candidate-pools.jsonl.gz"):
        require(pool["pool_id"] not in pools and sha(canonical(pool["windows"])) == pool["pool_sha256"]
                and pool["pool_id"] == pool["pool_key"] + "/" + pool["pool_sha256"], "Immutable exact candidate pool/order/float digest")
        raw = [{**item, "source": contents[item["source_hash"]]} for item in pool["windows"]]
        data = datasets[pool["first_identity"]["dataset"]]
        kind = pool["pool_key"].rsplit("/", 1)[1]
        require(kind in ("dense", "hybrid") and len(raw) == 50, "Every actual query freshly retrieves a complete depth50 pool")
        original.verify_raw(raw, data["inventory"], data["sources"], score_kind="cosine" if kind == "dense" else "rrf_k60")
        pools[pool["pool_id"]] = {"pool": pool, "raw": raw}
    rows = gz_rows(study / "searches.jsonl.gz")
    require([row["identity"] for row in rows] == plan["schedule"], "Exact completed prospectively rotated search schedule")
    setups = read(study / "setups.json")
    require([row["dataset"] for row in setups] == list(datasets), "Every ordered source setup and close retained")
    setup_map = {item["dataset"]: item for item in setups}
    configured = {value["id"]: value for value in plan["conditions"]}
    embed_counts = dict.fromkeys(("completed_query_calls", "completed_document_calls", "document_texts",
                                "attempted_query_calls", "attempted_document_calls", "attempted_document_texts"), 0)
    used_pools, used_contents, seen_datasets = set(), set(), set()
    paired, repeated, first_pool, first_vectors = {}, {}, {}, {}
    paired_mismatches, repeated_mismatches, vector_drift, old_pool_mismatches = [], [], [], []
    cases, excerpt_count, scored_truncated, batch_lengths, real_tokens, allocated_tokens = [], 0, 0, [], 0, 0
    old_pools = {}
    if plan["stage"] != "fresh":
        old = root / "results/qwen-reranker-full-2026-10-13"
        require(sha((old / "frozen-plan.json").read_bytes()) == plan["old_study_plan_sha256"], "Unchanged original full-study reference")
        old_report = read(old / "independent-audit.json")
        require(old_report["checks_passed"] is True and sha((old / "candidate-pools.jsonl.gz").read_bytes())
                == old_report["artifact_sha256"]["candidate-pools.jsonl.gz"], "Audited original depth50 development candidate receipts")
        old_pools = {item["pool_key"]: item["windows"] for item in gz_rows(old / "candidate-pools.jsonl.gz")}
    for row in rows:
        identity = row["identity"]
        dataset, query_id, condition = (identity[key] for key in ("dataset", "query_id", "condition"))
        data, query, selected_config = datasets[dataset], datasets[dataset]["queries"][query_id], configured[condition]
        retrieval, k, b = (selected_config[key] for key in ("retrieval", "shortlist", "batch_size"))
        if dataset not in seen_datasets:
            embed_counts = verify_setup(root, study, plan, data, setup_map[dataset], embed_counts)
            seen_datasets.add(dataset)
        require(row["status"] == "completed" and row["active_phase"] == "complete"
                and row["configuration"] == selected_config and row["query"] == query["query"]
                and row["kind"] == query["kind"] and row["counts_before"] == embed_counts,
                "Every actual search preserves config/literal query/kind and fresh embedding counter continuity")
        after = embedding_counts(row["counts_after"])
        require(all(after[key] == embed_counts[key] + (1 if "query_calls" in key else 0) for key in embed_counts),
                "Exactly one fresh actual query embedding and zero document calls per measured search")
        embed_counts = after
        original.verify_vector(row["vector"])
        vector_key = dataset + "/" + query_id
        if vector_key in first_vectors and first_vectors[vector_key] != row["vector"]["sha256"]:
            vector_drift.append(identity)
        first_vectors.setdefault(vector_key, row["vector"]["sha256"])
        pool_data = pools[row["pool_id"]]
        raw, pool = pool_data["raw"], pool_data["pool"]
        pool_key = dataset + "/" + query_id + "/" + retrieval
        require(pool["pool_key"] == pool_key and row["candidate_pool_sha256"] == pool["pool_sha256"]
                and row["candidate_order"] == [item["id"] for item in raw]
                and type(row["candidate_count"]) is int and row["candidate_count"] == 50,
                "Actual freshly retrieved original depth50 candidate order/source receipt")
        if row["pool_id"] not in first_pool:
            require(pool["first_identity"] == identity, "Candidate pool created at its first observed actual attempt")
            first_pool[row["pool_id"]] = identity
            if (old_pools and [{key: value for key, value in item.items() if key != "retrieval_score"} for item in pool["windows"]]
                    != [{key: value for key, value in item.items() if key != "retrieval_score"} for item in old_pools[pool_key]]):
                old_pool_mismatches.append(identity)
        used_pools.add(row["pool_id"])
        used_contents.update(item["source_hash"] for item in raw)
        digest = sha(canonical([{key: value for key, value in item.items() if key not in ("source", "retrieval_score")}
                                for item in raw]))
        pair_key = pool_key + "/" + str(identity["repetition"])
        matched_pair = None if pair_key not in paired else paired[pair_key] == digest
        matched_repeat = None if pool_key not in repeated else repeated[pool_key] == digest
        require(row["candidate_identity_source_sha256"] == digest
                and row["matches_prior_condition_pool_in_pair"] is matched_pair
                and row["matches_first_observed_pool_across_repetitions"] is matched_repeat,
                "Actual matched-condition/repeated original candidate identity/source drift accounting")
        paired.setdefault(pair_key, digest)
        repeated.setdefault(pool_key, digest)
        if matched_pair is False:
            paired_mismatches.append(identity)
        if matched_repeat is False:
            repeated_mismatches.append(identity)
        if b:
            receipt = row["reranker"]
            pairs = [{"query": query["query"], "source": item["source"]} for item in raw[:k]]
            count = 2 * len(receipt["batches"])
            counts, built = verify_batch_receipt(receipt, pairs, events[offset:offset + count], tokenizer, config,
                request_id="/".join(str(identity[key]) for key in ("dataset", "query_id", "condition", "repetition")),
                batch_size=b, previous_counts=counts)
            offset += count
            selected = original.ordered(raw[:k], receipt["results"])
            scored_truncated += sum(item["truncated"] for item in built)
            batch_lengths.extend(item["layout"]["actual_batch_size"] for item in receipt["batches"])
            real_tokens += sum(item["layout"]["real_input_tokens"] for item in receipt["batches"])
            allocated_tokens += sum(item["layout"]["allocated_input_tokens"] for item in receipt["batches"])
            score_kind = "experimental_qwen_yes_minus_no_logit_difference"
            require(row["reranker_http_s"] + 1e-9 >= receipt["server_scoring_s"], "Actual helper HTTP interval contains scoring")
        else:
            require("reranker" not in row and row["reranker_http_s"] == 0, "Baseline performs zero actual reranker scores")
            selected, score_kind = raw, "cosine" if retrieval == "dense" else "equal_weight_rrf_priority_k60"
        require(type(row["shortlist_count"]) is int and row["shortlist_count"] == k
                and row["shortlist_original_order"] == [item["id"] for item in raw[:k]]
                and row["ranking_order"] == [item["id"] for item in selected], "Only exact original K-prefix is scored/ranked/packed with no tail backfill")
        for key in ("query_embedding_s", "candidate_retrieval_s", "reranker_http_s", "packing_s", "elapsed_warm_pipeline_s"):
            original.finite(row[key], "Actual finite measured warm serial phase: " + key, minimum=0)
        require(row["elapsed_warm_pipeline_s"] + 1e-9 >= sum(row[key] for key in
                ("query_embedding_s", "candidate_retrieval_s", "reranker_http_s", "packing_s")),
                "Actual full warm pipeline contains embedding/retrieval/blocking helper/unchanged packing")
        response = row["response"]
        require(response["query"] == query["query"] and response["scope"] == original.expected_scope(data["entry"])
                and response["retrieval"] == response["experimental_candidate_retrieval"] == retrieval
                and response["experimental_packing_adapter"] == "dense adapter with externally obtained candidate order"
                and response["source_root"] == data["entry"]["source_root"] and response["source_budget"] == 12000
                and original.actual_path(root, response["repo"]) == original.evidence.located(root, data["entry"]["corpus"])
                and response["rerank"] is False and response["match_evidence"]["threshold"] is None
                and response["match_evidence"]["ranking_score_kind"] == score_kind
                and response["candidate_windows"] == len(selected)
                and response["match_status"] == "candidates", "Actual source/query/prefix ranking provenance and unchanged no-threshold context adapter")
        original.finite(response["match_evidence"]["top_cosine"], "Actual cosine provenance separate from scalar logits/RRF")
        require(-1.00001 <= response["match_evidence"]["top_cosine"] <= 1.00001, "Actual cosine numerical bound")
        ordered = original.production_order(response)
        excerpt_count += original.evidence.verify_response(ordered, ordered, data["inventory"], data["sources"], encoding, selected)
        original.exact_packing(response, selected, data)
        ranks = original.ranks(response["results"], query["groups"])
        require(row["required_groups"] == len(query["groups"]) and row["multi_file_and"] is query["multi_file"]
                and row["evidence_ranks"] == ranks and row["candidate_evidence_ranks"] == original.ranks(raw, query["groups"])
                and row["shortlist_evidence_ranks"] == original.ranks(raw[:k], query["groups"])
                and row["ranked_candidate_evidence_ranks"] == original.ranks(selected, query["groups"]),
                "Independently scored original whole-span required OR/AND source evidence after exact delivered packing")
        if data["entry"].get("language", "python") == "python":
            require(row["symbol_compatibility_evidence_ranks"] == original.ranks(response["results"], query["groups"], symbol_required=True)
                    and row["symbol_compatibility_candidate_evidence_ranks"] == original.ranks(raw, query["groups"], symbol_required=True),
                    "Separate original Python exact-symbol/full-span compatibility diagnostic")
        phases = {key: row[key] for key in ("query_embedding_s", "candidate_retrieval_s", "reranker_http_s", "packing_s")}
        if b:
            phases.update({"server_tokenization_s": receipt["tokenization_s"], "server_batch_preparation_s": receipt["batch_preparation_s"],
                           "server_native_forwards_s": receipt["forward_s"], "server_scoring_s": receipt["server_scoring_s"]})
        cases.append({"dataset": dataset, "query_id": query_id, "condition": condition,
            "configuration": selected_config, "repetition": identity["repetition"], "kind": query["kind"], "multi_file": query["multi_file"],
            "evidence_ranks": ranks, "candidate_evidence_ranks": original.ranks(raw, query["groups"]),
            "shortlist_evidence_ranks": original.ranks(raw[:k], query["groups"]), "tokens": response["token_usage"]["tokens"],
            "match_status": response["match_status"], "elapsed_s": row["elapsed_warm_pipeline_s"], "phases": phases,
            "compact_sha256": sha(original.evidence.compact_text(ordered).encode())})
    require(used_pools == set(pools) and used_contents == set(contents), "No orphan candidate/source artifacts")
    require(offset == len(events) and counts["completed_pairs"] == plan["planned_pair_scores"] + 32
            and counts["attempted_pairs"] == counts["completed_pairs"], "Every actual native/logical observed forward consumed exactly once")
    require(completion["embedding_counts"] == embed_counts
            and embed_counts["completed_query_calls"] == len(rows) + len(datasets)
            and completion["paired_candidate_mismatches"] == paired_mismatches
            and completion["repeated_candidate_mismatches"] == repeated_mismatches,
            "All actual measured/generic query embeddings and candidate drift honestly reconciled")
    metrics, comparisons = {}, {}
    for condition, value in configured.items():
        selected_cases = [case for case in cases if case["condition"] == condition]
        metrics[condition] = {"all": quality(selected_cases), "repositories": {dataset: quality([case for case in selected_cases if case["dataset"] == dataset])
                            for dataset in datasets}, "warm_phases": {key: distribution([case["phases"][key] for case in selected_cases])
                            for key in selected_cases[0]["phases"]}}
        baseline = [case for case in cases if case["condition"] == value["retrieval"]]
        if baseline:
            comparisons[condition] = changes(baseline, selected_cases)
    return counts, {"metrics": metrics, "changes_from_no_reranker": comparisons, "cases": cases,
        "actual_stage_requests": len(rows), "actual_query_embedding_counts": embed_counts,
        "paired_candidate_mismatches": paired_mismatches, "repeated_candidate_mismatches": repeated_mismatches,
        "old_candidate_order_source_mismatches": old_pool_mismatches, "query_vector_byte_drift": vector_drift,
        "source_excerpts_checked": excerpt_count, "scored_prefixes_truncated": scored_truncated,
        "batch_actual_size_distribution": dict(Counter(batch_lengths)), "benchmark_real_input_tokens": real_tokens,
        "benchmark_allocated_input_tokens": allocated_tokens, "benchmark_padding_tokens": allocated_tokens - real_tokens,
        "benchmark_padding_ratio": (allocated_tokens - real_tokens) / allocated_tokens if allocated_tokens else None,
        "literal_source_contents_checked": len(contents), "candidate_pools_checked": len(pools), "setup": setups}


def build_audit(root, study):
    plan = verify_frozen_plan(root, study)
    datasets = check_sources(root, plan)
    completion = read(study / "completion.json")
    require(completion["complete"] is True and completion["embedding_owner_closed"] is True
            and completion["attempted_requests"] == completion["completed_requests"] == plan["planned_requests"],
            "Every prospectively planned actual stage attempt completes without silent retry")
    require(not (study / "failure.json").exists() and not (study / "reranker-failure.json").exists(),
            "A successful study audit must not conceal failed actual attempts")
    config = original.prototype_constants((root / "scripts/prototype_qwen_reranker.py").read_text())
    tokenizer = verify_model_provenance(root, plan, read(study / "reranker-provenance.json"), config)
    events = gz_rows(study / "forwards.jsonl.gz")
    counts, offset = audit_warmups(study, tokenizer, config, events)
    if plan["stage"] != "parity":
        counts, result = audit_retrieval(root, study, plan, datasets, completion, tokenizer, config, events, counts, offset)
        result.update(checks_passed=True,
            classification="Independent CPU source/token/native-forward/full-span/packing/warm-latency audit; no inference",
            stage=plan["stage"], resident_counts=counts, lifecycle=verify_startup_cleanup(root, study, plan, counts),
            frozen_plan_sha256=sha((study / "frozen-plan.json").read_bytes()), auditor_sha256=sha(Path(__file__).read_bytes()),
            artifact_sha256={path.name: sha(path.read_bytes()) for path in sorted(study.iterdir()) if path.is_file()
                            and path.name not in ("independent-audit.json", "independent-findings.md")},
            limits=["Quality uses unique first-pass original full-span OR/AND source labels; every repeated actual warm sample remains in latency distributions.",
                    "Warm measurements include actual query embedding, depth50 retrieval, authenticated blocking scoring HTTP, instrumentation and unchanged context packing, excluding model/index setup and generic warmups.",
                    "These are in-process experimental intervals on one Mac, not full production HTTP/MCP/provider/coding latency or billed token usage.",
                    "Static callee expansion may deliver source outside the scored prefix; candidate completeness is not a delivered-coverage ceiling.",
                    "All eight earlier repositories including Koa are observed development; HTTPX source-authored freshness does not imply pretraining exclusion.",
                    "MLX memory is process allocator peak/cleanup, not OS RSS or simultaneous combined process memory.",
                    "Original weight identity relies on actual full-byte verification receipts and unchanged file witnesses; CPU auditing does not repeat inference or official/PyTorch parity."])
        return result
    audit_parity_inputs(root, plan, datasets)
    rows = gz_rows(study / "parity-scores.jsonl.gz")
    require([row["identity"] for row in rows] == plan["schedule"], "Every actual parity request follows exact predeclared schedule")
    results, timings = {}, []
    for row in rows:
        identity = row["identity"]
        group = next(group for group in plan["parity_sets"] if group["id"] == identity["parity_set"])
        require(row["status"] == "completed", "Actual parity attempt completes")
        receipt = row["reranker"]
        count = 2 * len(receipt["batches"])
        counts, built = verify_batch_receipt(receipt, group["pairs"], events[offset:offset + count], tokenizer, config,
            request_id="parity/" + group["id"] + "/b" + str(identity["batch_size"]),
            batch_size=identity["batch_size"], previous_counts=counts)
        offset += count
        original.finite(row["helper_http_s"], "Actual authenticated parity HTTP interval", minimum=0)
        require(row["helper_http_s"] + 1e-9 >= receipt["server_scoring_s"], "Measured blocking HTTP contains actual server scoring")
        results[(group["id"], identity["batch_size"])] = receipt["results"]
        timings.append(row["helper_http_s"])
        if group["id"].startswith("synthetic/"):
            require(built[-1]["truncated"] is True and built[-1]["delivered_lines"] < built[-1]["source_lines"],
                    "Predeclared synthetic case actually tests whole-line clipping")
    require(offset == len(events) and counts["completed_pairs"] == plan["planned_pair_scores"] + 32,
            "Every actual observed forward consumed once and every logical parity/warmup pair reconciled")
    by_size = {str(size): {group["id"]: parity(results[(group["id"], 1)], results[(group["id"], size)])
                          for group in plan["parity_sets"]} for size in BATCH_SIZES}
    passing = [size for size in BATCH_SIZES if all(check["qualified"] for check in by_size[str(size)].values())]
    actual_gate = read(study / "parity-gate.json")
    require(actual_gate["limits"] == plan["parity_limits"] and actual_gate["passing_batch_sizes"] == passing,
            "Independent fixed-tolerance gate agrees with actual retained eligible B list")
    require(completion["embedding_counts"] == {}, "Parity stage has zero actual embedding calls")
    lifecycle = verify_startup_cleanup(root, study, plan, counts)
    return {"checks_passed": True, "classification": "Independent CPU batch-input/native-forward/parity audit; no inference",
            "stage": plan["stage"], "actual_stage_requests": len(rows), "resident_counts": counts,
            "passing_batch_sizes": passing, "parity": by_size, "actual_parity_http_seconds": distribution(timings),
            "lifecycle": lifecycle, "frozen_plan_sha256": sha((study / "frozen-plan.json").read_bytes()),
            "auditor_sha256": sha(Path(__file__).read_bytes()), "artifact_sha256": {path.name: sha(path.read_bytes())
                for path in sorted(study.iterdir()) if path.is_file()
                and path.name not in ("independent-audit.json", "independent-findings.md")},
            "limits": ["CPU auditor independently rebuilds tokenizer IDs, logical grouping/layout/padding and actual scalar math without loading model weights.",
                       "Native causal and final-token path relies on committed frozen producer and verified pinned native source/loaded checkpoint witnesses, plus actual receipts and internal BF16 parity.",
                       "This is internal BF16 B1-versus-B batching parity, not numerical official/PyTorch parity or a production/coding latency benchmark.",
                       "Failed batching configurations remain retained and excluded rather than relaxed or retried."]}


def findings(result):
    return "\n".join(["# Independent Qwen optimization audit", "", result["classification"], "",
        "All frozen source, input-token, actual native-forward, logical-pair, layout/padding, scalar, timing and cleanup checks passed.",
        "", "Stage: " + result["stage"], "Passing batch sizes: " + ", ".join(map(str, result.get("passing_batch_sizes", []))),
        "", "Every detailed comparison, observed native/pair/token total and immutable artifact digest is in `independent-audit.json`.", "",
        *["- " + item for item in result["limits"]], ""])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--study", type=Path, required=True)
    args = parser.parse_args(argv)
    study = args.study.resolve()
    try:
        result = build_audit(args.root.resolve(), study)
    except BaseException as error:
        attempts = study / "audit-attempts"
        attempts.mkdir(exist_ok=True)
        number = len(list(attempts.glob("*-failure.json"))) + 1
        stem = f"{number:02d}"
        (attempts / (stem + "-failure.json")).write_text(json.dumps({"error_type": type(error).__name__, "error": str(error),
            "auditor_sha256": sha(Path(__file__).read_bytes())}, indent=2) + "\n")
        (attempts / (stem + "-auditor.py.txt")).write_bytes(Path(__file__).read_bytes())
        raise
    (study / "independent-audit.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    (study / "independent-findings.md").write_text(findings(result))
    print(json.dumps({"checks_passed": True, "stage": result["stage"], "actual_stage_requests": result["actual_stage_requests"]}))


if __name__ == "__main__":
    main()
