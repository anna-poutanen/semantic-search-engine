#!/usr/bin/env python3
"""Audit one selected reranker development case without loading a model."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("case_saved_evidence", ROOT / "scripts/report_context_packing.py")
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def jsonl(path):
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


def constants(source):
    wanted = {"MODEL_ID", "REVISION", "MAX_TOKENS", "INSTRUCTION", "PREFIX", "SUFFIX", "PINS", "RUNTIME_PINS", "QWEN3_SOURCE_SHA"}
    result = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in wanted:
                result[name] = ast.literal_eval(node.value)
    require(set(result) == wanted, "Frozen literal prototype constants")
    return result


def rebuild_pair(query, source, tokenizer, config):
    def encode(text):
        return tokenizer.encode(text, add_special_tokens=False).ids
    prefix, suffix = encode(config["PREFIX"]), encode(config["SUFFIX"])
    header = f"<Instruct>: {config['INSTRUCTION']}\n<Query>: {query}\n<Document>: "
    lines = evidence.physical_lines(source)
    for count in range(len(lines), 0, -1):
        delivered = "".join(lines[:count])
        ids = prefix + encode(header + delivered) + suffix
        if len(ids) <= config["MAX_TOKENS"]:
            return {"source": delivered, "source_sha256": sha(source.encode()),
                    "delivered_sha256": sha(delivered.encode()), "source_lines": len(lines),
                    "delivered_lines": count, "source_utf8_bytes": len(source.encode()),
                    "delivered_utf8_bytes": len(delivered.encode()), "truncated": delivered != source,
                    "total_tokens": len(ids), "query_sha256": sha(query.encode()),
                    "input_ids_sha256": sha(json.dumps(ids, separators=(",", ":")).encode())}
    raise ValueError("No physical source prefix fits the complete pair cap")


def verify_score(score):
    for key in ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal"):
        require(type(score[key]) in (int, float) and math.isfinite(score[key]), "Finite scalar score")
    difference = score["yes_logit"] - score["no_logit"]
    require(math.isfinite(difference) and score["logit_difference"] == difference, "Exact yes-minus-no logits")
    probability = 1 / (1 + math.exp(-difference)) if difference >= 0 else math.exp(difference) / (1 + math.exp(difference))
    require(math.isclose(score["yes_rank_signal"], probability, rel_tol=1e-13, abs_tol=1e-15), "Independent stable binary signal")


def verify_forwards(records, pairs, results, tokenizer, config):
    require(len(records) == 100 and len(pairs) == len(results) == 50, "All fifty started/completed pair forwards")
    for number, (pair, result) in enumerate(zip(pairs, results, strict=True), 1):
        expected = rebuild_pair(pair["query"], pair["source"], tokenizer, config)
        require({key: result[key] for key in expected if key != "input_ids_sha256"} == {
                    key: value for key, value in expected.items() if key != "input_ids_sha256"}, "Exact literal scored prefix/template/tokens")
        verify_score(result)
        started, completed = records[(number - 1) * 2:number * 2]
        require(started["pair_number"] == completed["pair_number"] == number
                and type(started["pair_number"]) is type(completed["pair_number"]) is int
                and started["status"] == "started" and completed["status"] == "completed", "Ordered forward states")
        require(all(started[key] == completed[key] == expected[key] for key in
                    ("input_ids_sha256", "total_tokens", "source_sha256", "delivered_sha256", "truncated")), "Observed actual forward hashes/cap")
        require({key: completed[key] for key in started if key != "status"} == {key: value for key, value in started.items() if key != "status"},
                "Immutable started/completed receipt metadata")
        require(type(completed["forward_seconds"]) in (int, float) and math.isfinite(completed["forward_seconds"])
                and completed["forward_seconds"] >= 0, "Observed finite forward interval")
        verify_score(completed["score"])
        require(completed["score"] == {key: result[key] for key in completed["score"]}, "Final result and observed scores agree")


def ranking(candidates, results):
    require(len(candidates) == len(results) == 50 and len({candidate["id"] for candidate in candidates}) == 50, "Frozen fifty unique candidates")
    order = sorted(range(50), key=lambda index: (-results[index]["logit_difference"], index + 1, candidates[index]["id"]))
    return [{"id": candidates[index]["id"], "original_dense_rank": index + 1,
             "logit_difference": results[index]["logit_difference"]} for index in order]


def common_inputs(root, study):
    plan, baseline, pairs = (read(study / name) for name in ("frozen-plan.json", "baseline.json", "pairs.json"))
    for name, field in (("pairs.json", "pairs_sha256"), ("baseline.json", "baseline_sha256"), ("runner.py.txt", "runner_sha256")):
        require(sha((study / name).read_bytes()) == plan[field], "Frozen case artifact: " + name)
    prototype = root / "scripts/prototype_qwen_reranker.py"
    require(sha(prototype.read_bytes()) == plan["prototype_sha256"], "Frozen prototype source")
    old = subprocess.check_output(["git", "-C", str(root), "show", plan["source_commit"] + ":scripts/prototype_qwen_reranker.py"], timeout=10)
    require(sha(old) == plan["prototype_sha256"], "Committed prototype byte binding")
    config = constants(old.decode())
    require(plan["planned_pair_forwards"] == 50 and plan["unique_queries"] == 1
            and plan["query_id"] == baseline["identity"]["query_id"] == "express_configured_query_parser"
            and baseline["identity"]["dataset"] == "express" and baseline["identity"]["condition"] == "full_query_dense",
            "Single explicitly selected known development miss")
    prior = root / "results/clause-fusion-development-v2-2026-10-13"
    original = next(row for row in jsonl(prior / "attempts.jsonl") if row["identity"] == baseline["identity"])
    require(original == baseline and len(pairs) == len(baseline["merged_candidates"]) == 50
            and all(set(pair) == {"query", "source"} and pair["query"] == baseline["query"]
                    and pair["source"] == candidate["source"] for pair, candidate in zip(pairs, baseline["merged_candidates"], strict=True)),
            "Exact prior dense baseline and ordered source-only pairs")
    preflight = read(prior / "CPU-preflight.json")
    corpus_record = next(record for record in preflight["datasets"] if record["id"] == "express")
    sources, hashes = {}, corpus_record["source_file_sha256"]
    for path, expected in hashes.items():
        raw = evidence.located(root / ".cache/corpora/express", path).read_bytes()
        require(sha(raw) == expected, "Pinned saved Express file bytes")
        sources[path] = evidence.physical_lines(raw.decode())
    inventory = {window["id"]: window for window in corpus_record["window_inventory"]}
    for candidate in baseline["merged_candidates"]:
        require(candidate["id"] in inventory and all(candidate[key] == inventory[candidate["id"]][key]
                    for key in ("path", "symbol", "start_line", "end_line", "source_hash")), "Full raw candidate identity")
        actual = "".join(sources[candidate["path"]][candidate["start_line"] - 1:candidate["end_line"]])
        require(candidate["source"] == actual and candidate["source_hash"] == sha(actual.encode()), "Exact candidate physical bytes")
    return plan, baseline, pairs, config, sources, hashes, inventory


def verify_packing(packing, baseline, model, order, inventory, sources, hashes, query, encoding):
    expected_ids = [row["id"] for row in order]
    require(packing["order"] == expected_ids and packing["baseline_compact_equal"] is True
            and packing["cache_reused_windows"] == 190 and packing["embedding_calls"] == 0
            and packing["backend_closed"] is True, "Unchanged cached packing and exact observed order")
    vector = baseline["query_vectors"][0]
    import numpy as np
    values = np.asarray(vector["values"], dtype=vector["dtype"])
    require(sha(values.tobytes()) == packing["reused_vector_sha256"] == vector["sha256"], "Exact original retained vector reuse")
    raw = baseline["merged_candidates"]
    require(evidence.compact_text(packing["baseline_response"]) == evidence.compact_text(baseline["response"]),
            "Baseline compact payload exactly reproduced")
    source_by_id = {row["id"]: row for row in raw}
    results_by_id = {row["id"]: result for row, result in zip(raw, model["results"], strict=True)}
    reranked = [{**source_by_id[identifier], "retrieval_score": results_by_id[identifier]["logit_difference"]} for identifier in expected_ids]
    counts = [evidence.verify_response(response, baseline["response"], inventory, sources, encoding, candidates)
              for response, candidates in ((packing["baseline_response"], raw), (packing["reranked_response"], reranked))]
    groups = evidence.gold_groups(query, sources, hashes)
    before = evidence.evidence_ranks(packing["baseline_response"]["results"], groups)
    after = evidence.evidence_ranks(packing["reranked_response"]["results"], groups)
    require(before == baseline["evidence_ranks"] == packing["baseline_evidence_ranks"]
            and after == packing["reranked_evidence_ranks"], "Independent complete OR/AND executable spans")
    return {"baseline_compact_exact": True, "baseline_evidence_ranks": before, "reranked_evidence_ranks": after,
            "required_groups": len(groups), "baseline_complete": None not in before, "reranked_complete": None not in after,
            "source_excerpts_checked": sum(counts), "compact_tokens": {"baseline": packing["baseline_response"]["token_usage"]["tokens"],
            "reranked": packing["reranked_response"]["token_usage"]["tokens"]}, "embedding_calls": 0,
            "limit": "Complete evidence gain on this explicitly chosen known miss only; no unseen-query or general improvement estimate."}


def build_audit(root, study):
    plan, baseline, pairs, config, sources, hashes, inventory = common_inputs(root, study)
    supervisor = read(study / "supervisor.json")
    require(supervisor["owned_process_group_stopped"] is True and supervisor["timed_out"] is supervisor["forced_kill"] is False,
            "Owned supervisor cleanup without timeout/forced kill")
    records = jsonl(study / "forwards.jsonl")
    if (study / "failure.json").exists():
        failure = read(study / "failure.json")
        require(failure["attempted_pair_calls"] == failure["completed_pair_calls"] == 0 and failure["error_type"] == "TypeError"
                and "5 positional arguments but 6 were given" in failure["error"] and failure["retry"] is False
                and records == [] and supervisor["exit_code"] == 1 and not (study / "model-result.json").exists(),
                "Preserved instrumentation-signature failure before scored pairs")
        return {"checks_passed": True, "outcome": "failed_before_pair_forward", "frozen_pairs_checked": 50,
                "completed_pair_forwards": 0, "semantic_ranking_outcomes": 0,
                "limit": "Weights were loaded and parameters evaluated before Python rejected the wrapper's six-argument call. This is not a zero-model-load gate failure; no pair backbone or score was executed. Cleanup additionally relies on frozen prototype finally and owned child termination."}
    model = read(study / "model-result.json")
    require(supervisor["exit_code"] == 0 and model["attempted_pair_calls"] == model["completed_pair_calls"] == 50
            and model["experimental"] is model["sequential_unpadded"] is True
            and model["instruction"] == config["INSTRUCTION"] and model["max_total_tokens"] == config["MAX_TOKENS"] == 8192,
            "All fifty scored pairs and exact prospective instruction/cap")
    checkpoint = root / ".cache/models/qwen3-reranker-0.6b-original"
    identity = model["model"]
    require(identity["model_id"] == config["MODEL_ID"] and identity["revision"] == config["REVISION"]
            and identity["weight_identity_verified"] is True, "Original checkpoint identity")
    for name, (size, expected) in config["PINS"].items():
        file = checkpoint / name
        require(file.stat().st_size == size and identity["files"][name] == {"size": size, "sha256": expected}, "Actual checkpoint verification receipt")
        stat = file.stat()
        require(identity["file_witnesses"][name] == ["<PROJECT>/" + str(file.resolve().relative_to(root)),
                    stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns], "Unchanged checkpoint inode/stat witnesses")
        if not name.endswith(".safetensors"):
            require(sha(file.read_bytes()) == expected, "Actual tokenizer/config bytes")
    runtime = model["runtime"]
    require(runtime["errors"] == [] and runtime["packages"] == config["RUNTIME_PINS"]
            and runtime["qwen3_source_sha256"] == runtime["imported_qwen3_source_sha256"] == config["QWEN3_SOURCE_SHA"], "Pinned actual runtime/source identity")
    site = root / ".cache/reranker-experiment/.venv/lib/python3.12/site-packages"
    expected_path = "<PROJECT>/" + str((site / "mlx_lm/models/qwen3.py").relative_to(root))
    require(runtime["qwen3_source_path"] == runtime["imported_qwen3_source_path"] == expected_path
            and sha((site / "mlx_lm/models/qwen3.py").read_bytes()) == config["QWEN3_SOURCE_SHA"], "Actually imported primary Qwen3 file")
    packages = {distribution.metadata["Name"]: distribution.version for distribution in importlib.metadata.distributions(path=[str(site)])}
    require(model["packages"] == packages, "Actual isolated distribution inventory")
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(checkpoint / "tokenizer.json"))
    verify_forwards(records, pairs, model["results"], tokenizer, config)
    order = ranking(baseline["merged_candidates"], model["results"])
    previous = study.parent / study.name.replace("-v2-", "-")
    correction = None
    if previous != study and (previous / "frozen-plan.json").exists():
        initial = read(previous / "frozen-plan.json")
        require(all(plan[key] == value for key, value in initial.items() if key not in ("frozen_utc", "runner_sha256")),
                "Corrected instrumentation changed a model/input/ranking/packing invariant")
        failure = read(previous / "failure.json")
        require(failure["attempted_pair_calls"] == failure["completed_pair_calls"] == 0,
                "Corrected probe follows selected semantic outcomes")
        correction = {"same_model_pairs_baseline_ranking_and_packing": True,
                      "initial_attempted_pair_calls": 0, "initial_failure_sha256": sha((previous / "failure.json").read_bytes()),
                      "limit": "Only the recording wrapper signature changed after a failure with no scored pairs; original evidence remains preserved."}
    packed = None
    if (study / "packing.json").exists():
        packing = read(study / "packing.json")
        prior_plan = read(root / "results/clause-fusion-development-v2-2026-10-13/frozen-plan.json")
        require(packing["packing_source_sha256"] == prior_plan["source_sha256"]["scripts/run_clause_fusion.py"]
                == sha((root / "scripts/run_clause_fusion.py").read_bytes()), "Same frozen shipped-packer adapter source")
        for name, expected in prior_plan["source_sha256"].items():
            require(sha(evidence.located(root, name).read_bytes()) == expected, "Packing runtime/source remains frozen: " + name)
        labels = root / "benchmarks/express_language_queries.json"
        require(sha(labels.read_bytes()) == prior_plan["dataset_sha256"][str(labels.relative_to(root))], "Frozen post-selection gold labels")
        query = next(query for query in read(labels)["queries"] if query["id"] == plan["query_id"])
        cache_plan = {"runtime": {"tiktoken": prior_plan["packages"]["tiktoken"], "tokenizer_cache_sha256": {
            "9b5ad71b2ce5302211f9c61530b329a4922fc6a4": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"}}}
        packed = verify_packing(packing, baseline, model, order, inventory, sources, hashes, query, evidence.encoder(root, cache_plan))
    return {"checks_passed": True, "outcome": "fifty_pairs_completed", "frozen_pairs_checked": 50,
            "completed_pair_forwards": 50, "forward_receipts_checked": 100,
            "scored_source_prefixes_truncated": sum(result["truncated"] for result in model["results"]),
            "isolated_runtime_distributions": len(packages), "ranking": order,
            "observed_forward_seconds": sum(record["forward_seconds"] for record in records if record["status"] == "completed"),
            "observed_total_seconds": model["elapsed_total_seconds"],
            "instrumentation_correction": correction,
            "packing_comparison": packed or "Pending separate root-owned unchanged-packer receipt",
            "limits": ["One deliberately chosen known miss on already-observed Express development data; no holdout, broad quality, default-change, coding correctness, billing, HTTP or P95 claim.",
                       "Original BF16 weight SHA is checked through retained full-byte verification; the auditor rehashes tokenizer/config/source, not weight bytes or model outputs through inference.",
                       "No PyTorch numerical parity proof; two logits are ranking signals, not calibrated correctness.",
                       "Forward intervals include graph construction/evaluation/scalar extraction and instrumentation. Total interval also includes load/tokenization/setup/cleanup; no inference-only attribution."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--study", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_audit(args.root.resolve(), args.study.resolve())
    result["classification"] = "Independent CPU audit of selected known-miss reranker development probe"
    result["artifact_sha256"] = {path.name: sha(path.read_bytes()) for path in args.study.iterdir()
                                 if path.is_file() and path.name not in ("independent-audit.json", "independent-findings.md")}
    result["auditor_sha256"] = sha(Path(__file__).read_bytes())
    (args.study / "independent-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    if result["outcome"] == "failed_before_pair_forward":
        findings = "# Preserved reranker instrumentation failure\n\nThe wrapper signature rejected the six-argument scorer call after model weights loaded and parameters were evaluated. Zero pair forwards completed, so there are no ranking or quality outcomes. The original failure/source/inputs remain preserved.\n\n" + result["limit"] + "\n"
    else:
        packed = result["packing_comparison"]
        findings = ("# Selected known-miss development reranker audit\n\n"
                    "All 50 original dense candidates and 100 started/completed forward receipts pass independent source, tokenizer/template/token-cap, scalar-score and runtime checks. No scored source prefix was truncated.\n\n"
                    + (f"The unchanged packer exactly reproduces the baseline compact payload, then changes complete executable evidence ranks from `{packed['baseline_evidence_ranks']}` to `{packed['reranked_evidence_ranks']}`. All three required groups are now delivered on this deliberately selected prior miss; no embedding calls were made during packing. Actual compact payloads contain {packed['compact_tokens']['baseline']} and {packed['compact_tokens']['reranked']} tokens, both within 4096; saved-source hashes and whole physical-line bounds are verified.\n\n" if isinstance(packed, dict) else str(packed) + "\n\n")
                    + "\n".join("- " + limit for limit in result["limits"]) + "\n")
    (args.study / "independent-findings.md").write_text(findings)
    print(json.dumps({key: result[key] for key in ("checks_passed", "outcome", "completed_pair_forwards")}))


if __name__ == "__main__":
    main()
