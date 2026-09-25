#!/usr/bin/env python3
"""Independently audit retained production B1 parity artifacts; CPU metadata only.

This auditor does not import the producer, production package, tokenizer, or MLX.
It reconstructs the certified reference inventory and checks literal spans, exact
scores, native events, source/Git identities, runtime receipts, and owned cleanup.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "results/qwen-batch-parity-supervised-2026-10-13"
SCORE_KEYS = {"yes_logit", "no_logit", "logit_difference", "yes_rank_signal"}
INTEGER_KEYS = {"delivered_lines", "delivered_utf8_bytes", "source_lines", "source_utf8_bytes", "total_tokens"}
HASH_KEYS = {"query_sha256", "source_sha256", "delivered_sha256"}
RESULT_KEYS = SCORE_KEYS | INTEGER_KEYS | HASH_KEYS | {"truncated"}
COUNT_KEYS = ("requests", "attempted_pairs", "completed_pairs", "attempted_forwards", "completed_forwards")
CORE_NAMES = ("sha256", "file_hash", "runtime_preflight", "verified_qwen3_module", "checkpoint_preflight",
              "file_witness", "verify_witnesses", "physical_lines", "PairBuilder", "binary_score", "score_pair")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(items):
    result = {}
    for key, value in items:
        require(key not in result, "Duplicate JSON object key")
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON value: " + value)))


def read(path):
    return decode(path.read_text())


def rows(path):
    with gzip.open(path, "rt") as stream:
        return [decode(line) for line in stream]


def typed_equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(typed_equal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(typed_equal(a, b) for a, b in zip(actual, expected, strict=True))
    return actual == expected


def counts(value, expected):
    require(isinstance(value, dict), "Missing native counter object")
    require(all(type(value.get(key)) is int and value[key] == expected for key in COUNT_KEYS),
            "Native counter inventory differs")


def public(value):
    if isinstance(value, str):
        return value.replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items()}
    return value


def local_path(value):
    require(isinstance(value, str), "Missing retained local path")
    return Path(value.replace("<PROJECT>", str(ROOT)).replace("<HOME>", str(Path.home())))


def physical_lines(source):
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", source)


def validate_result(pair, result):
    require(isinstance(result, dict) and result.keys() == RESULT_KEYS, "Exact original 13-field result required")
    require(all(type(result[key]) is int and result[key] > 0 for key in INTEGER_KEYS), "Invalid typed source/token counters")
    require(type(result["truncated"]) is bool, "Invalid typed clipping flag")
    require(all(type(result[key]) is float and math.isfinite(result[key]) for key in SCORE_KEYS), "Invalid finite score scalars")
    source, query = pair["source"], pair["query"]
    require(isinstance(source, str) and isinstance(query, str), "Literal string pair required")
    lines = physical_lines(source)
    require(0 < result["delivered_lines"] <= len(lines), "Clipped source line count outside literal source")
    delivered = "".join(lines[:result["delivered_lines"]])
    expected = {"source_lines": len(lines), "source_utf8_bytes": len(source.encode()),
                "source_sha256": sha(source.encode()), "query_sha256": sha(query.encode()),
                "delivered_utf8_bytes": len(delivered.encode()), "delivered_sha256": sha(delivered.encode()),
                "truncated": delivered != source}
    require(all(typed_equal(result[key], value) for key, value in expected.items()), "Literal whole-line source identity differs")
    require(1 <= result["total_tokens"] <= 8192, "Input token cap differs")
    difference = result["yes_logit"] - result["no_logit"]
    probability = 1 / (1 + math.exp(-difference)) if difference >= 0 else math.exp(difference) / (1 + math.exp(difference))
    require(result["logit_difference"] == difference and result["yes_rank_signal"] == probability,
            "Original binary scalar arithmetic differs")


def reconstruct_cases(plan, recorded):
    references = {}
    for row in recorded:
        if row["identity"]["batch_size"] == 1:
            group = row["identity"]["parity_set"]
            require(group not in references, "Duplicate original B1 reference group")
            references[group] = row["reranker"]
    require(set(references) == {group["id"] for group in plan["parity_sets"]}, "Original B1 group inventory differs")
    cases = []
    for group in plan["parity_sets"]:
        original = references[group["id"]]
        prepared = [item for batch in original["batch_receipts"] for item in batch["pairs"]]
        require(sorted(item["pair_index"] for item in prepared) == list(range(len(group["pairs"]))),
                "Original token receipt index inventory differs")
        prepared = {item["pair_index"]: item for item in prepared}
        for index, (pair, result) in enumerate(zip(group["pairs"], original["results"], strict=True)):
            validate_result(pair, result)
            receipt = prepared[index]
            for key in ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens", "truncated", "delivered_lines"):
                require(typed_equal(receipt[key], result[key]), "Original prepared input differs from original scalar result")
            cases.append({"group": group["id"], "index": index, "pair": pair, "expected": result,
                          "expected_input_ids_sha256": receipt["input_ids_sha256"]})
    return cases


def validate_observations(cases, observed, ledger, warmup):
    require(len(observed) == len(cases), "Actual result inventory differs")
    require(len(ledger) == 2 * (len(cases) + 1), "Actual native event inventory differs")
    identities = [warmup] + [row["response"] for row in observed]
    for number, (identity, started, completed) in enumerate(zip(identities, ledger[::2], ledger[1::2], strict=True)):
        request_id = "generic-warmup" if number == 0 else f"production-parity-{number - 1}"
        require(identity["request_id"] == request_id and type(identity["batch_size"]) is int
                and identity["batch_size"] == 1 and identity["warmup"] is (number == 0), "Actual singleton response identity differs")
        counts(identity["counts"], number + 1)
        result = identity["result"]
        if number:
            case, row = cases[number - 1], observed[number - 1]
            require(type(row["number"]) is int and row["number"] == number - 1
                    and row["group"] == case["group"] and type(row["index"]) is int
                    and row["index"] == case["index"], "Actual result order/group/index differs")
            require(row["mismatches"] == {} and typed_equal(result, case["expected"]), "Production result differs from original exact typed fields")
            validate_result(case["pair"], result)
        expected = {key: result[key] for key in HASH_KEYS | {"total_tokens"}}
        expected.update(request_id=request_id, warmup=number == 0, batch_size=1, forward_number=number + 1)
        if number:
            expected["input_ids_sha256"] = cases[number - 1]["expected_input_ids_sha256"]
        for event, wanted in ((started, "started"), (completed, "completed")):
            require(event["event"] == wanted and all(typed_equal(event.get(key), value) for key, value in expected.items()),
                    "Native started/completed input identity or order differs")
        require(started["input_ids_sha256"] == completed["input_ids_sha256"], "Native warmup input identity differs")
        require(typed_equal(completed["score"], {key: result[key] for key in SCORE_KEYS}), "Native scalar ledger differs from returned score")
        require(type(completed["forward_s"]) is float and math.isfinite(completed["forward_s"])
                and completed["forward_s"] >= 0 and completed["forward_s"] == identity["forward_s"], "Native elapsed receipt differs")
        for key in ("tokenization_s", "server_scoring_s"):
            require(type(identity[key]) is float and math.isfinite(identity[key]) and identity[key] >= 0, "Invalid native timing receipt")
    return len(cases)


def audit(study, original=ORIGINAL):
    plan = read(study / "frozen-plan.json")
    verification = read(study / "verification.json")
    reference_audit = read(original / "independent-audit.json")
    original_plan = read(original / "frozen-plan.json")
    require(reference_audit["checks_passed"] is True and reference_audit["study_qualified"] is True
            and reference_audit["clean_supervised_run"] is True and reference_audit["passing_batch_sizes"] == [1],
            "Certified clean original B1 reference required")
    for name, digest in reference_audit["artifact_sha256"].items():
        require(Path(name).name == name and sha((original / name).read_bytes()) == digest, "Original audited artifact drift: " + name)
    require(reference_audit["frozen_plan_sha256"] == sha((original / "frozen-plan.json").read_bytes()), "Original audit plan binding differs")
    for name, digest in plan["reference_artifact_sha256"].items():
        require(Path(name).name == name and sha((original / name).read_bytes()) == digest, "Frozen original reference drift: " + name)
    cases = reconstruct_cases(original_plan, rows(original / "parity-scores.jsonl.gz"))
    require(len(cases) == 416 and typed_equal(plan["cases"], cases) and type(plan["expected_pairs"]) is int
            and plan["expected_pairs"] == 416, "All 416 exact original ordered literal cases required")
    require(plan["model_calls_in_freeze"] == 0 and plan["weights_loaded_in_freeze"] is False, "CPU-only freeze required")
    for name, digest in plan["source_sha256"].items():
        path = Path(name)
        require(not path.is_absolute() and ".." not in path.parts, "Unsafe source binding")
        committed = subprocess.check_output(["git", "-C", str(ROOT), "show", f'{plan["source_commit"]}:{name}'])
        require(sha(committed) == digest == sha((ROOT / path).read_bytes()), "Frozen committed source drift: " + name)
    frozen_blob = subprocess.check_output(["git", "-C", str(ROOT), "show", "HEAD:" + str((study / "frozen-plan.json").relative_to(ROOT))])
    plan_hash = sha((study / "frozen-plan.json").read_bytes())
    require(sha(frozen_blob) == plan_hash, "Committed production plan required")
    require(verification["frozen_plan_sha256"] == plan_hash and read(study / "run-start.json")["frozen_plan_sha256"] == plan_hash,
            "Actual attempt frozen-plan binding differs")
    for name, digest in verification["artifact_sha256"].items():
        require(Path(name).name == name and sha((study / name).read_bytes()) == digest, "Actual producer artifact drift: " + name)
    require(set(verification["artifact_sha256"]) == {"frozen-plan.json", "run-start.json", "results.jsonl.gz", "forwards.jsonl.gz"},
            "Actual producer artifact inventory differs")
    require(not any(key in verification for key in ("error", "cleanup_error", "ledger_error")), "Actual failed attempt retained")
    require(verification["checks_passed"] is True and verification["complete"] is True and verification["completed_pairs"] == 416
            and verification["mismatches"] == [] and verification["input_ids_mismatches"] == []
            and verification["ledger_identity_mismatches"] == [] and verification["ledger_complete"] is True,
            "Producer incomplete or mismatching attempt")
    start, stop, final = verification["owner_start"], verification["owner_stop"], verification["owner_final_status"]
    pid = start["pid"]
    require(type(pid) is int and pid > 0 and start["owned_group"] == pid == start["ready"]["pid"]
            == start["ready"]["process_group"] == final["pid"] == stop["pid"] == stop["owned_group"], "Owned worker PID/group binding differs")
    require(start["private_directory_mode"] == "0o700" and start["token_mode"] == "0o600"
            and start["query_caching"] is False and start["score_caching"] is False, "Private singleton/no-cache lifecycle differs")
    copied_names = ("__init__.py", "client.py", "qwen_reranker_core.py", "qwen_worker.py")
    require(set(start["worker_source_sha256"]) == set(copied_names), "Trusted copied worker source inventory differs")
    provenance = start["ready"]["provenance"]
    require(typed_equal(provenance, start["start_status"]["provenance"])
            and typed_equal(provenance, final["provenance"]) and typed_equal(provenance, stop["latest_status"]["provenance"]),
            "Actual source/checkpoint/runtime changed across lifecycle receipts")
    require(provenance["batch_size"] == 1 and provenance["sequential_unpadded"] is True
            and provenance["max_total_tokens"] == 8192 and provenance["scoring_core_sha256"] == start["worker_source_sha256"]["qwen_reranker_core.py"],
            "Original unpadded B1 core identity differs")
    runtime = provenance["runtime"]
    pinned = original_plan["reranker"]
    require(runtime["errors"] == [] and runtime["packages"] == pinned["runtime"]["packages"]
            and runtime["qwen3_source_sha256"] == runtime["imported_qwen3_source_sha256"] == pinned["runtime"]["qwen3_source_sha256"]
            and runtime["supporting_source_sha256"] == pinned["supporting_sources"], "Pinned native runtime receipt differs")
    runtime_source = local_path(runtime["qwen3_source_path"])
    require(runtime_source == local_path(runtime["imported_qwen3_source_path"])
            and sha(runtime_source.read_bytes()) == runtime["qwen3_source_sha256"], "Actual imported native origin/bytes differ")
    for name, digest in runtime["supporting_source_sha256"].items():
        require(sha((runtime_source.parent / name).read_bytes()) == digest, "Current native support source differs")
    model = provenance["model"]
    original_provenance = read(original / "reranker-provenance.json")
    original_model = original_provenance["model"]
    require(model["model_id"] == pinned["model_id"] and model["revision"] == pinned["revision"]
            and model["weight_identity_verified"] is True and model["files"] == original_model["files"], "Exact original checkpoint receipt differs")
    for name, witness in model["file_witnesses"].items():
        path = local_path(witness[0])
        stat = path.stat()
        require([stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns] == witness[1:], "Checkpoint changed after measured verification")
    metadata_code = ('import importlib.metadata as m,json,platform,sys; '
                     'print(json.dumps({"python":platform.python_version(),"prefix":sys.prefix,'
                     '"packages":{d.metadata["Name"]:d.version for d in m.distributions()}}))')
    interpreter = local_path(start["interpreter"]["invocation_path"])
    metadata = decode(subprocess.check_output([str(interpreter), "-B", "-c", metadata_code], text=True))
    require(metadata["packages"] == pinned["packages"] and metadata["python"] == original_plan["python"]
            and Path(metadata["prefix"]).resolve() == interpreter.parent.parent.resolve()
            and interpreter.resolve() == local_path(start["interpreter"]["resolved_binary"]), "Original isolated interpreter/package closure differs")
    core = ast.parse((ROOT / "src/semantic_search/qwen_reranker_core.py").read_text())
    old_core_source = subprocess.check_output(["git", "-C", str(ROOT), "show",
                    original_plan["source_commit"] + ":scripts/prototype_qwen_reranker.py"])
    require(sha(old_core_source) == original_plan["implementation_sha256"]["scripts/prototype_qwen_reranker.py"],
            "Original core Git/source binding differs")
    old_core = ast.parse(old_core_source)
    core_nodes = {node.name: ast.dump(node, include_attributes=False) for node in core.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    old_nodes = {node.name: ast.dump(node, include_attributes=False) for node in old_core.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    require(all(core_nodes[name] == old_nodes[name] for name in CORE_NAMES), "Original core AST changed during promotion")
    def literal_constants(tree):
        return {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in
                {"MODEL_ID", "REVISION", "MAX_TOKENS", "INSTRUCTION", "PREFIX", "SUFFIX", "PINS", "RUNTIME_PINS", "QWEN3_SOURCE_SHA"}}
    require(typed_equal(literal_constants(core), literal_constants(old_core)), "Original prompt/checkpoint/token constants changed")
    require(provenance["instruction"] == literal_constants(core)["INSTRUCTION"], "Actual instruction receipt differs")
    counts(start["start_status"]["counts"], 1)
    counts(final["counts"], 417)
    counts(stop["latest_status"]["counts"], 417)
    counts(stop["worker_stop"]["counts"], 417)
    observed, ledger = rows(study / "results.jsonl.gz"), rows(study / "forwards.jsonl.gz")
    validate_observations(cases, observed, ledger, start["ready"]["warmup"])
    cleanup = stop["stop"]
    require(cleanup["owned_group_stopped"] is True and cleanup["forced_kill"] is False and cleanup["exit_code"] == 0
            and cleanup["errors"] == [] and cleanup["pid"] == cleanup["owned_group"] == pid,
            "Successful owned group cleanup unproved")
    require(stop["auth_token_removed"] is True and stop["private_cleanup_errors"] == []
            and stop["worker_stop"]["clean_stop"] is True and stop["worker_stop"]["listener_closed"] is True
            and stop["worker_stop"]["auth_token_removed"] is True and stop["worker_stop"]["auth_cleanup_error"] is None,
            "Worker listener/token/model cleanup unproved")
    private_candidates = list((ROOT / ".cache" / study.name).glob("owned-*/ready.json"))
    private = [path.parent for path in private_candidates if read(path)["pid"] == pid]
    require(len(private) == 1, "Actual owned private witness directory not unique")
    private = private[0]
    require(not (private / "token.secret").exists() and (private.stat().st_mode & 0o777) == 0o700, "Actual private token removal unproved")
    require(typed_equal(public(read(private / "ready.json")), start["ready"])
            and typed_equal(public(read(private / "stop.json")), stop["worker_stop"]), "Public/private actual lifecycle witnesses differ")
    require((private / "forwards.jsonl").read_bytes() == gzip.decompress((study / "forwards.jsonl.gz").read_bytes()), "Published native ledger differs from actual retained ledger")
    for name, digest in start["worker_source_sha256"].items():
        require(digest == plan["source_sha256"]["src/semantic_search/" + name]
                == sha((private / "runtime/semantic_search" / name).read_bytes()), "Copied actual worker source differs from committed source")
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise ValueError("Actual owned worker group disappearance unproved")
    times = [plan["frozen_at_utc"], read(study / "run-start.json")["started_at_utc"], verification["started_at_utc"],
             start["started_at_utc"], start["ready"]["started_at_utc"], cleanup["started_cleanup_utc"],
             stop["worker_stop"]["stopped_at_utc"], cleanup["finished_cleanup_utc"], stop["closed_at_utc"], verification["finished_at_utc"]]
    parsed = [datetime.fromisoformat(value) for value in times]
    require(parsed == sorted(parsed), "Frozen attempt/start/cleanup timestamps out of order")
    return {"checks_passed": True, "classification": "Independent CPU audit of exact original versus promoted B1 production scorer",
            "audited_at_utc": datetime.now(timezone.utc).isoformat(), "auditor_sha256": sha(Path(__file__).read_bytes()),
            "frozen_plan_sha256": plan_hash, "reference_audit_sha256": sha((original / "independent-audit.json").read_bytes()),
            "source_commit": plan["source_commit"], "source_sha256": plan["source_sha256"],
            "artifact_sha256": {name: sha((study / name).read_bytes()) for name in
                ("frozen-plan.json", "run-start.json", "results.jsonl.gz", "forwards.jsonl.gz", "verification.json")},
            "exact_original_pairs": 416, "parity_sets": len(original_plan["parity_sets"]), "compared_result_fields_per_pair": 13,
            "native_real_started": 416, "native_real_completed": 416, "generic_warmup_forwards": 1,
            "native_events": len(ledger), "runtime_package_count": len(metadata["packages"]), "core_ast_equal_functions": list(CORE_NAMES),
            "original_whole_line_clipping_verified": True, "tokenizer_input_hashes_exact": True,
            "original_scalar_equality_exact": True, "owned_group_gone": True, "token_absent": True,
            "model_calls_in_audit": 0, "native_imports_in_audit": 0, "weight_bytes_read_in_audit": False,
            "limits": ["This exact B1 scorer comparison does not certify end-to-end retrieval, coding correctness, or coding latency.",
                       "The audit checks recorded weight hashes and unchanged physical file witnesses; it does not reread weight bytes.",
                       "Package closure is observed again after the run; actual native model/runtime/core identities are retained across start/final/stop receipts."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--original", type=Path, default=ORIGINAL)
    args = parser.parse_args()
    study = args.study.resolve()
    destination = study / "independent-audit.json"
    require(not destination.exists(), "Preserve prior audit; no overwrite")
    try:
        result = audit(study, args.original.resolve())
    except Exception as error:
        result = {"checks_passed": False, "error_type": type(error).__name__, "error": str(error),
                  "auditor_sha256": sha(Path(__file__).read_bytes()), "model_calls_in_audit": 0}
    with destination.open("x") as stream:
        stream.write(json.dumps(public(result), indent=2, sort_keys=True, allow_nan=False) + "\n")
    findings = ("Independent exact production B1 parity audit passed: all 416 literal pairs across nine original sets retain all 13 typed fields, "
                "whole-line clipping and tokenizer input hashes exactly. The 416 real native forwards plus one separate generic warmup "
                "reconcile with every sequential response and final/stop counter. Git/source, copied worker, pinned checkpoint and native "
                "runtime/package closure match; actual owned group is gone, listener closed, token absent. No inference in this audit. "
                "This certifies the scorer; end-to-end retrieval and coding remain separate checks.\n") if result["checks_passed"] else (
                    "Independent production parity audit failed: " + result["error"] + "\n")
    with (study / "independent-findings.md").open("x") as stream:
        stream.write(findings)
    print(json.dumps({"checks_passed": result["checks_passed"], "audit": str(destination)}))
    return 0 if result["checks_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
