#!/usr/bin/env python3
"""Separate frozen parity, warm grid, development and fresh Qwen experiments."""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import subprocess
import time
from urllib.request import Request

import run_qwen_reranker_benchmark as previous
import run_clause_fusion as packing
import run_language_quality as language

ROOT = previous.ROOT
OLD_STUDY = ROOT / "results/qwen-reranker-full-2026-10-13"
PROTOCOL = ROOT / "docs/qwen-reranker-optimization-protocol.md"
DEPTHS = (5, 8, 10, 16, 24, 32, 40, 50)
BATCHES = (1, 2, 4, 8)
REPETITIONS = 3
PARITY_LIMITS = {"yes_logit": .0625, "no_logit": .0625,
                 "logit_difference": .125, "yes_rank_signal": .025, "inversion_margin": .25}
sha, require, canonical, write = previous.sha, previous.require, previous.canonical, previous.write


def configuration(retrieval, shortlist=50, batch_size=0):
    require(retrieval in ("dense", "hybrid") and type(shortlist) is int and shortlist in DEPTHS
            and type(batch_size) is int and batch_size in (0,) + BATCHES, "Unsupported configuration")
    require(batch_size or shortlist == 50, "Baseline retrieves the full original pool")
    identifier = retrieval if batch_size == 0 else f"{retrieval}_k{shortlist}_b{batch_size}"
    return {"id": identifier, "retrieval": retrieval, "shortlist": shortlist, "batch_size": batch_size}


def check_configurations(values):
    require(isinstance(values, list) and values, "Nonempty configuration list required")
    for value in values:
        require(isinstance(value, dict) and set(value) == {"id", "retrieval", "shortlist", "batch_size"},
                "Exact configuration fields required")
        require(value == configuration(value["retrieval"], value["shortlist"], value["batch_size"]),
                "Configuration identity disagrees with settings")
    require(len({value["id"] for value in values}) == len(values), "Duplicate configurations")
    return values


def panel_catalog(entries, catalog):
    selected = set()
    for entry, dataset, _ in entries:
        positives = [q for q in dataset["queries"] if q["kind"] == "present"]
        absent = [q for q in dataset["queries"] if q["kind"] == "absent"]
        require(len(positives) >= 2 and absent, "Panel needs two positives and one absent per repository")
        selected.update((entry["id"], q["id"]) for q in (positives[0], positives[-1], absent[0]))
    result = [q for q in catalog if (q["dataset"], q["query_id"]) in selected]
    require(len(result) == 24, "Exact predeclared 24-query timing panel")
    return result


def schedule(catalog, configurations, repetitions=REPETITIONS):
    result = []
    ids = [value["id"] for value in configurations]
    for dataset in dict.fromkeys(row["dataset"] for row in catalog):
        subset = [row for row in catalog if row["dataset"] == dataset]
        for repetition in range(1, repetitions + 1):
            for number, query in enumerate(subset):
                offset = (number + repetition - 1) % len(ids)
                for condition in ids[offset:] + ids[:offset]:
                    result.append({"dataset": dataset, "query_id": query["query_id"],
                                   "condition": condition, "repetition": repetition})
    return result


def fresh_inputs():
    from semantic_search.corpus import _source_files, extract_snippets
    from semantic_search.languages import decode_source, extractor_fingerprint
    manifest_name = "benchmarks/httpx_reranker_manifest.json"
    manifest = json.loads((ROOT / manifest_name).read_text())
    require(len(manifest["datasets"]) == 1, "Single prospective HTTPX corpus")
    entry = deepcopy(manifest["datasets"][0])
    require(entry["id"] == "httpx" and entry["source_root"] == "httpx"
            and entry["source_exclusions"] == [], "Unchanged HTTPX source scope")
    repo = ROOT / entry["corpus"]
    require(subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            == entry["revision"], "Pinned HTTPX revision changed")
    require(not subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True).strip(),
            "HTTPX source is dirty")
    raw_dataset = (ROOT / entry["dataset"]).read_bytes()
    require(sha(raw_dataset) == entry["sha256"], "Frozen HTTPX labels changed")
    dataset = json.loads(raw_dataset)
    raw = {path.relative_to(repo).as_posix(): path.read_bytes()
           for path in _source_files(repo, repo / entry["source_root"], [])}
    sources = {name: decode_source(name, value) for name, value in raw.items()}
    windows = [language.snippet_window(snippet) for snippet in extract_snippets(
        repo, entry["source_root"], max_lines=160, overlap_lines=40, source_exclusions=[], sources=sources)]
    queries = []
    for query in dataset["queries"]:
        groups = previous.normalized_groups(query, sources)
        require(all(value is not None for value in previous.evidence_ranks(windows, groups, sources)),
                "HTTPX evidence interval not wholly eligible")
        queries.append({"query_id": query["id"], "kind": query["kind"], "groups": groups,
            "eligible_windows": [[window["id"] for window in windows
                if previous.evidence_ranks([window], [group], sources)[0] is not None] for group in groups]})
    require(len(dataset["queries"]) == 15 and sum(q["kind"] == "present" for q in dataset["queries"]) == 12
            and sum(len(q["groups"]) for q in queries) == 28 and len(windows) == 438,
            "Unchanged prospective HTTPX inventory")
    inventory = [{key: value for key, value in window.items() if key != "source"} for window in windows]
    record = {"id": entry["id"], "source_file_sha256": {name: sha(value) for name, value in raw.items()},
              "windows": len(windows), "window_inventory": inventory, "window_inventory_sha256": sha(canonical(inventory)),
              "queries": queries, "classification": "fresh source-reviewed retrieval repository",
              "original_manifest": manifest_name}
    catalog = [{"dataset": entry["id"], "query_id": q["id"], "query": q["query"]} for q in dataset["queries"]]
    return [(entry, dataset, sources)], [record], catalog, extractor_fingerprint()


def parity_inputs(entries):
    contents = {row["sha256"]: row["source"] for row in gz_rows(OLD_STUDY / "source-content.jsonl.gz")}
    pools = {row["pool_key"]: row for row in gz_rows(OLD_STUDY / "candidate-pools.jsonl.gz")}
    result = []
    for entry, dataset, _ in entries:
        query = next(q for q in dataset["queries"] if q["kind"] == "present")
        pool = pools[entry["id"] + "/" + query["id"] + "/dense"]
        result.append({"id": entry["id"] + "/" + query["id"], "classification": "fixed development parity input",
                       "pairs": [{"query": query["query"], "source": contents[window["source_hash"]]}
                                 for window in pool["windows"]]})
    synthetic = [{"query": "Locate the Unicode greeting return.",
                  "source": "def greet():\n" + "    # mixed-length µ fixture\n" * number
                            + "    return 'héllo 🤖'\n"} for number in range(14)]
    synthetic += [deepcopy(synthetic[0]), {"query": "Locate the repeated comments and final return.",
                  "source": "def repeated():\n    # " + "alpha " * 4500
                            + "\n    # " + "beta " * 4500 + "\n    return 1\n"}]
    result.append({"id": "synthetic/mixed-length-unicode-duplicate-clipping",
                   "classification": "predeclared synthetic padding/clipping diagnostic", "pairs": synthetic})
    return result


def gz_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def implementation_hashes():
    result = previous.implementation_hashes()
    names = ("scripts/run_qwen_optimization.py", "scripts/resident_qwen_optimization.py",
             "scripts/qwen_reranker_batch.py", "scripts/supervise_qwen_optimization.py",
             "scripts/qwen_optimization_pipeline.py",
             "docs/qwen-reranker-optimization-protocol.md", "docs/qwen-reranker-batching.md")
    result.update({name: sha((ROOT / name).read_bytes()) for name in names})
    return result


def freeze_inputs(stage, configurations, reranker_python, checkpoint):
    from semantic_search.embeddings import QwenEmbedder
    require(stage in ("parity", "grid", "development", "fresh"), "Unknown experimental stage")
    entries, records, catalog, extractor = fresh_inputs() if stage == "fresh" else previous.source_inputs()
    if stage != "fresh":
        for record in records:
            record["classification"] = "previously observed development, including Koa"
    configurations = check_configurations(configurations)
    if stage == "grid":
        catalog = panel_catalog(entries, catalog)
    parity = parity_inputs(entries) if stage == "parity" else []
    preflight = json.loads(subprocess.check_output([str(reranker_python), str(ROOT / "scripts/resident_qwen_optimization.py"),
                          "--checkpoint", str(checkpoint)], text=True))
    require(not preflight["runtime"]["errors"] and not preflight["weights_loaded"], "Batch metadata preflight failed")
    embedder = QwenEmbedder(progress=False)
    plan = {"version": 1, "stage": stage, "classification": "Prospectively frozen optimization stage; development tuning is observed",
            "conditions": configurations, "repetitions": REPETITIONS, "primary_quality_repetition": 1,
            "settings": packing.SETTINGS, "query_catalog": catalog, "sources": records, "extractor": extractor,
            "manifest_sha256": {name: sha((ROOT / name).read_bytes()) for name in
                (("benchmarks/httpx_reranker_manifest.json",) if stage == "fresh" else previous.MANIFESTS)},
            "implementation_sha256": implementation_hashes(), "semantic_module_origins": packing.module_origins(),
            "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())),
            "python": platform.python_version(), "embedding": embedder.metadata,
            "embedding_checkpoint": packing.checkpoint_inventory(), "offline_tokenizer": packing.tokenizer_cache()[1],
            "reranker": {"model_id": preflight["checkpoint"]["model_id"], "revision": preflight["checkpoint"]["revision"],
                "checkpoint_files": preflight["checkpoint"]["files"], "packages": preflight["packages"],
                "runtime": {key: preflight["runtime"][key] for key in ("packages", "qwen3_source_sha256")},
                "supporting_sources": {name: item["sha256"] for name, item in
                    preflight["runtime"]["batching_supporting_sources"].items()}},
            "parity_sets": parity, "parity_limits": PARITY_LIMITS, "batch_sizes": list(BATCHES),
            "max_padded_tokens": 16384, "query_cache": False, "score_cache": False, "model_calls_in_freeze": 0,
            "weights_loaded_in_freeze": False, "generic_reranker_warmup_pairs": 32,
            "generic_reranker_warmup_forwards": 15, "generic_embedding_warmup": previous.GENERIC_WARMUP,
            "old_study_plan_sha256": sha((OLD_STUDY / "frozen-plan.json").read_bytes()),
            "cache_seed": {entry["id"]: None if stage == "fresh" else OLD_STUDY.name + "-" + entry["id"] + ".sqlite3"
                           for entry, _, _ in entries}}
    plan["schedule"] = ([{"parity_set": group["id"], "batch_size": size} for group in parity for size in BATCHES]
                        if stage == "parity" else schedule(catalog, configurations))
    plan["planned_requests"] = len(plan["schedule"])
    plan["planned_pair_scores"] = (sum(len(group["pairs"]) for group in parity) * len(BATCHES)
        if stage == "parity" else sum(len(catalog) * REPETITIONS * value["shortlist"]
                                      for value in configurations if value["batch_size"]))
    require(embedder._model is None, "Freeze loaded embedding weights")
    return entries, plan


def scored_request(http, ready, token, request_id, pairs, batch_size):
    payload = {"request_id": request_id, "pairs": pairs, "batch_size": batch_size}
    started = time.perf_counter()
    request = Request(ready["url"] + "/score", data=canonical(payload),
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
    with http.open(request, timeout=240) as reply:
        result = json.loads(reply.read())
    elapsed = time.perf_counter() - started
    require(result["request_id"] == request_id and result["pairs_sha256"] == sha(canonical(pairs))
            and result["batch_size"] == batch_size and len(result["results"]) == len(pairs),
            "Worker input/result identity mismatch")
    return result, elapsed


def parity_comparison(reference, candidate):
    require(len(reference) == len(candidate) and reference, "Matched parity pairs required")
    differences = defaultdict(list)
    for before, after in zip(reference, candidate, strict=True):
        require({k: v for k, v in before.items() if k not in PARITY_LIMITS}
                == {k: v for k, v in after.items() if k not in PARITY_LIMITS}, "Batched constructed input differs")
        for field in ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal"):
            require(math.isfinite(before[field]) and math.isfinite(after[field]), "Nonfinite parity scalar")
            differences[field].append(abs(before[field] - after[field]))
    flips = []
    for i in range(len(reference)):
        for j in range(i + 1, len(reference)):
            old_delta = reference[i]["logit_difference"] - reference[j]["logit_difference"]
            new_delta = candidate[i]["logit_difference"] - candidate[j]["logit_difference"]
            if (old_delta >= 0) != (new_delta >= 0):
                flips.append({"pair_indexes": [i, j], "reference_margin": abs(old_delta),
                              "candidate_margin": abs(new_delta), "near_tie": abs(old_delta) <= PARITY_LIMITS["inversion_margin"]})
    def ordering(rows):
        return sorted(range(len(rows)), key=lambda index: (-rows[index]["logit_difference"], index))
    old_top, new_top = set(ordering(reference)[:5]), set(ordering(candidate)[:5])
    swapped_margins = [abs(reference[a]["logit_difference"] - reference[b]["logit_difference"])
                       for a in old_top - new_top for b in new_top - old_top]
    errors = {field: max(values) for field, values in differences.items()}
    passed = (all(value <= PARITY_LIMITS[field] for field, value in errors.items())
              and all(flip["near_tie"] for flip in flips)
              and all(margin <= PARITY_LIMITS["inversion_margin"] for margin in swapped_margins))
    return {"passed": passed, "max_absolute_errors": errors, "ranking_inversions": flips,
            "top_five_reference": sorted(old_top), "top_five_candidate": sorted(new_top),
            "top_five_swap_reference_margins": swapped_margins}


def run_parity(output, frozen, http, ready, token):
    rows, results = [], {}
    with gzip.open(output / "parity-scores.jsonl.gz", "xt", encoding="utf-8") as stream:
        for identity in frozen["schedule"]:
            group = next(group for group in frozen["parity_sets"] if group["id"] == identity["parity_set"])
            row = {"identity": identity, "status": "started"}
            try:
                result, elapsed = scored_request(http, ready, token,
                    "parity/" + group["id"] + "/b" + str(identity["batch_size"]), group["pairs"], identity["batch_size"])
                row.update(status="completed", reranker=result, helper_http_s=elapsed)
                results[(group["id"], identity["batch_size"])] = result["results"]
            except BaseException as error:
                row.update(status="failed", error_type=type(error).__name__, error=str(error))
                stream.write(canonical(packing.publication(row)).decode() + "\n")
                stream.flush()
                raise
            rows.append(row)
            stream.write(canonical(packing.publication(row)).decode() + "\n")
            stream.flush()
            print(json.dumps({"stage": "parity", "completed_requests": len(rows),
                              "planned_requests": len(frozen["schedule"])}), flush=True)
    by_size = {str(size): {group["id"]: parity_comparison(results[(group["id"], 1)], results[(group["id"], size)])
                          for group in frozen["parity_sets"]} for size in BATCHES}
    passing = [size for size in BATCHES if all(check["passed"] for check in by_size[str(size)].values())]
    write(output / "parity-gate.json", {"limits": PARITY_LIMITS, "passing_batch_sizes": passing,
                                      "comparisons": by_size, "classification": "Internal fresh BF16 batching diagnostic"})
    write(output / "completion.json", {"complete": True, "attempted_requests": len(rows),
          "completed_requests": len(rows), "embedding_owner_closed": True, "embedding_counts": {},
          "finished_at_utc": datetime.now(timezone.utc).isoformat()})


def run(output, reranker_python, checkpoint, ready_path, token_path):
    require(not (output / "run-start.json").exists(), "No resume/retry/overwrite of attempted stage")
    frozen = json.loads((output / "frozen-plan.json").read_text())
    entries, current = freeze_inputs(frozen["stage"], frozen["conditions"], reranker_python, checkpoint)
    expected_keys = set(current) | {"frozen_at_utc", "source_commit", "decision_inputs"}
    require(set(frozen) == expected_keys, "Frozen plan fields changed")
    for key, value in current.items():
        require(value == frozen[key], "Frozen stage invariant changed: " + key)
    for name, expected in frozen["decision_inputs"].items():
        require(sha((ROOT / name).read_bytes()) == expected, "Decision input changed: " + name)
    ready = json.loads(ready_path.read_text())
    require(ready["frozen_plan_sha256"] == sha((output / "frozen-plan.json").read_bytes())
            and ready["resident_ready"] and ready["generic_warmup_completed"], "Worker not ready for exact plan")
    previous.validate_loopback_url(ready["url"])
    http = previous.loopback_opener()
    token = token_path.read_text().strip()
    write(output / "run-start.json", {"started_at_utc": datetime.now(timezone.utc).isoformat(),
          "frozen_plan_sha256": sha((output / "frozen-plan.json").read_bytes()), "stage": frozen["stage"]})
    try:
        if frozen["stage"] == "parity":
            run_parity(output, frozen, http, ready, token)
        else:
            write(output / "embedding-weight-verification.json", packing.verify_weights(frozen["embedding_checkpoint"]))
            from qwen_optimization_pipeline import run_pipeline
            run_pipeline(output, entries, frozen, http, ready, token)
        require(implementation_hashes() == frozen["implementation_sha256"], "Implementation changed during stage")
    except BaseException as error:
        failure_path = output / ("driver-failure.json" if (output / "failure.json").exists() else "failure.json")
        require(not failure_path.exists(), "Failure evidence must not be overwritten")
        write(failure_path, packing.publication({"error_type": type(error).__name__, "error": str(error),
              "stage": frozen["stage"], "no_retry": True}))
        if not (output / "completion.json").exists():
            write(output / "completion.json", {"complete": False, "embedding_owner_closed": frozen["stage"] == "parity",
                  "finished_at_utc": datetime.now(timezone.utc).isoformat()})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--freeze", action="store_true")
    modes.add_argument("--run", action="store_true")
    parser.add_argument("--stage", choices=("parity", "grid", "development", "fresh"))
    parser.add_argument("--configurations", type=Path)
    parser.add_argument("--decision-input", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reranker-python", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--token-file", type=Path)
    args = parser.parse_args(argv)
    if args.freeze:
        if args.stage is None or args.configurations is None:
            parser.error("--freeze requires --stage and --configurations")
        configurations = json.loads(args.configurations.read_text())
        _, plan = freeze_inputs(args.stage, configurations, args.reranker_python, args.checkpoint)
        plan["frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
        plan["source_commit"] = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        plan["decision_inputs"] = {str(path.resolve().relative_to(ROOT)): sha(path.read_bytes()) for path in args.decision_input}
        args.output.mkdir(parents=True, exist_ok=False)
        write(args.output / "frozen-plan.json", plan)
        print(json.dumps({"stage": args.stage, "weights_loaded": False, "model_calls": 0,
                          "planned_requests": plan["planned_requests"], "planned_pair_scores": plan["planned_pair_scores"]}))
    else:
        if args.ready is None or args.token_file is None:
            parser.error("--run requires --ready and --token-file")
        run(args.output, args.reranker_python, args.checkpoint, args.ready, args.token_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
