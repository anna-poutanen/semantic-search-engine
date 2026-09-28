#!/usr/bin/env python3
"""Freeze/run an exact production B1 comparison against certified original pairs.

Freeze reads metadata and original recorded scores, without models. Run explicitly
launches the local owned worker; it never invokes a hosted model or network service.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "results/qwen-batch-parity-supervised-2026-10-13"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def public(value):
    if isinstance(value, str):
        return value.replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items()}
    return value


def ledger_inventory(rows, cases):
    completed = [row for row in rows if row.get("event") == "completed" and row.get("warmup") is False]
    scored = {row["request_id"]: row for row in completed}
    expected_requests = {f"production-parity-{number}" for number in range(len(cases))}
    started = [row for row in rows if row.get("event") == "started" and row.get("warmup") is False]
    warmup_started = [row for row in rows if row.get("event") == "started" and row.get("warmup") is True]
    warmup_completed = [row for row in rows if row.get("event") == "completed" and row.get("warmup") is True]
    exact_events = []
    if warmup_started and warmup_completed:
        exact_events = [("started", warmup_started[0]["request_id"]),
                        ("completed", warmup_completed[0]["request_id"])]
    for number in range(len(cases)):
        exact_events += [("started", f"production-parity-{number}"),
                         ("completed", f"production-parity-{number}")]
    identity_mismatches = []
    for number, case in enumerate(cases):
        request_rows = [row for row in rows if row.get("request_id") == f"production-parity-{number}"]
        expected = {key: case["expected"][key] for key in
                    ("query_sha256", "source_sha256", "delivered_sha256", "total_tokens")}
        expected.update(input_ids_sha256=case["expected_input_ids_sha256"],
                        batch_size=1, warmup=False, forward_number=number + 2)
        if len(request_rows) != 2 or any(any(row.get(key) != value for key, value in expected.items())
                                         for row in request_rows):
            identity_mismatches.append(number)
    return {"input_ids_mismatches": [number for number, case in enumerate(cases)
        if scored.get(f"production-parity-{number}", {}).get("input_ids_sha256")
           != case["expected_input_ids_sha256"]],
        "ledger_completed_pairs": len(scored),
        "ledger_identity_mismatches": identity_mismatches,
        "ledger_complete": (set(scored) == expected_requests and len(completed) == len(cases)
            and len(started) == len(cases) and len(warmup_started) == len(warmup_completed) == 1
            and not identity_mismatches
            and [(row.get("event"), row.get("request_id")) for row in rows] == exact_events)}


def freeze(output):
    if output.exists():
        raise ValueError("Fresh parity output required")
    audit = json.loads((ORIGINAL / "independent-audit.json").read_text())
    if audit.get("checks_passed") is not True or audit.get("passing_batch_sizes") != [1]:
        raise ValueError("Certified original B1 reference required")
    old = json.loads((ORIGINAL / "frozen-plan.json").read_text())
    references = {}
    with gzip.open(ORIGINAL / "parity-scores.jsonl.gz", "rt") as stream:
        for line in stream:
            row = json.loads(line)
            if row["identity"]["batch_size"] == 1:
                references[row["identity"]["parity_set"]] = row["reranker"]
    source_commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    files = [Path(__file__).resolve().relative_to(ROOT),
             *[path.relative_to(ROOT) for path in (ROOT / "src/semantic_search").glob("*.py")],
             Path("requirements-qwen-reranker.lock")]
    for file in files:
        committed = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{source_commit}:{file}"])
        if committed != (ROOT / file).read_bytes():
            raise ValueError("Commit production implementation before freeze: " + str(file))
    cases = []
    for group in old["parity_sets"]:
        original = references[group["id"]]
        prepared = {item["pair_index"]: item for batch in original["batch_receipts"] for item in batch["pairs"]}
        for index, (pair, result) in enumerate(zip(group["pairs"], original["results"], strict=True)):
            cases.append({"group": group["id"], "index": index, "pair": pair, "expected": result,
                          "expected_input_ids_sha256": prepared[index]["input_ids_sha256"]})
    if len(cases) != 416:
        raise ValueError("All416 original real/synthetic literal pairs required")
    output.mkdir(parents=True)
    write(output / "frozen-plan.json", {"version": 1, "classification": "Exact original B1 versus promoted production worker",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(), "source_commit": source_commit,
        "source_sha256": {str(file): sha(ROOT / file) for file in files}, "cases": cases,
        "expected_pairs": 416, "model_calls_in_freeze": 0, "weights_loaded_in_freeze": False,
        "reference_artifact_sha256": {name: sha(ORIGINAL / name) for name in
            ("independent-audit.json", "frozen-plan.json", "parity-scores.jsonl.gz")},
        "comparison": "Exact scalar, tokenizer, literal input and whole-line clipping identity; no relaxed tolerance"})


def run(output, python, checkpoint):
    from semantic_search.qwen_reranker import QwenRerankerProcess
    plan_path = output / "frozen-plan.json"
    plan = json.loads(plan_path.read_text())
    for name, expected in plan["reference_artifact_sha256"].items():
        if Path(name).name != name or sha(ORIGINAL / name) != expected:
            raise ValueError("Original reference artifact changed: " + name)
    for name, expected in plan["source_sha256"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("Production source changed after freeze: " + name)
    if any((output / name).exists() for name in ("results.jsonl.gz", "run-start.json", "verification.json")):
        raise ValueError("No parity resume or retry")
    committed_plan = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", "HEAD:" + str(plan_path.relative_to(ROOT))])
    if committed_plan != plan_path.read_bytes():
        raise ValueError("Commit the frozen parity plan before inference")
    with (output / "run-start.json").open("x") as stream:
        json.dump({"started_at_utc": datetime.now(timezone.utc).isoformat(),
                   "frozen_plan_sha256": sha(plan_path)}, stream, sort_keys=True)
        stream.write("\n")
    owner = QwenRerankerProcess(python=python, checkpoint=checkpoint,
                               log_dir=ROOT / ".cache" / output.name)
    result = {"frozen_plan_sha256": sha(plan_path), "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "completed_pairs": 0, "mismatches": [], "complete": False}
    try:
        client = owner.start()
        result["owner_start"] = public(owner.receipt)
        with gzip.open(output / "results.jsonl.gz", "xt") as stream:
            for number, case in enumerate(plan["cases"]):
                pair = case["pair"]
                judgment = client.score(pair["query"], SimpleNamespace(text=pair["source"]),
                                        request_id=f"production-parity-{number}")
                actual = judgment.response["result"]
                mismatches = {key: {"expected": value, "actual": actual.get(key)}
                              for key, value in case["expected"].items() if actual.get(key) != value}
                stream.write(json.dumps({"number": number, "group": case["group"], "index": case["index"],
                    "response": judgment.response, "mismatches": mismatches}, sort_keys=True, allow_nan=False) + "\n")
                stream.flush()
                result["completed_pairs"] += 1
                if mismatches:
                    result["mismatches"].append({"number": number, "fields": mismatches})
                if (number + 1) % 50 == 0:
                    print(json.dumps({"completed_pairs": number + 1, "total": 416}), flush=True)
        result["owner_final_status"] = public(owner.status())
        result["complete"] = True
    except BaseException as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        try:
            owner.close()
        except BaseException as error:
            result["cleanup_error"] = {"type": type(error).__name__, "message": str(error)}
        result["owner_stop"] = public(owner.receipts)
        if owner.directory is not None:
            ledger = owner.directory / "forwards.jsonl"
            if ledger.exists():
                raw = ledger.read_bytes()
                (output / "forwards.jsonl.gz").write_bytes(gzip.compress(raw, mtime=0))
                try:
                    rows = [json.loads(line) for line in raw.decode().splitlines()]
                    result.update(ledger_inventory(rows, plan["cases"]))
                except (ValueError, TypeError, KeyError) as error:
                    result["ledger_error"] = {"type": type(error).__name__, "message": str(error)}
        stop = owner.receipts.get("stop", {})
        expected_counts = {key: 417 for key in ("requests", "attempted_pairs", "completed_pairs",
                                              "attempted_forwards", "completed_forwards")}
        worker_stop = owner.receipts.get("worker_stop", {})
        result["counts_reconciled"] = all(worker_stop.get("counts", {}).get(key) == value
                                          for key, value in expected_counts.items())
        result["checks_passed"] = (result["complete"] and result["completed_pairs"] == 416
            and not result["mismatches"] and result.get("input_ids_mismatches") == []
            and result.get("ledger_complete") is True and not result.get("cleanup_error")
            and result["counts_reconciled"]
            and stop.get("owned_group_stopped") is True and not stop.get("forced_kill")
            and not stop.get("errors") and stop.get("exit_code") == 0
            and owner.receipts.get("auth_token_removed") is True
            and not owner.receipts.get("private_cleanup_errors")
            and worker_stop.get("clean_stop") is True
            and worker_stop.get("listener_closed") is True
            and worker_stop.get("auth_token_removed") is True and worker_stop.get("auth_cleanup_error") is None)
        result["artifact_sha256"] = {name: sha(output / name) for name in
            ("frozen-plan.json", "run-start.json", "results.jsonl.gz", "forwards.jsonl.gz")
            if (output / name).exists()}
        result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write(output / "verification.json", public(result))
    print(json.dumps({"checks_passed": result["checks_passed"], "completed_pairs": result["completed_pairs"],
                      "mismatches": len(result["mismatches"])}), flush=True)
    return 0 if result["checks_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=ROOT / ".cache/reranker-experiment/.venv/bin/python")
    parser.add_argument("--checkpoint", type=Path, default=ROOT / ".cache/models/qwen3-reranker-0.6b-original")
    args = parser.parse_args()
    if args.freeze == args.run:
        parser.error("Choose exactly --freeze or --run")
    if args.freeze:
        freeze(args.output.resolve())
        return 0
    return run(args.output.resolve(), args.python.absolute(), args.checkpoint.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
