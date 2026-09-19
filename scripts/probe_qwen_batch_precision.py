#!/usr/bin/env python3
"""Frozen diagnostic of native backbone versus head batch-shape differences.

Shared last-hidden reuse is diagnostic only. It is never a retrieval benchmark,
a new scoring preset, a relaxed parity gate, or a reranking latency measurement.
Preflight/freeze do not import MLX, load weights or perform inference.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import traceback

import qwen_reranker_batch as batch

prototype = batch.prototype
ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ("scripts/probe_qwen_batch_precision.py", "scripts/qwen_reranker_batch.py",
                "scripts/prototype_qwen_reranker.py")
BATCH_SIZES = (2, 4, 8)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def publish(value):
    if isinstance(value, str):
        return value.replace(str(ROOT), "<PROJECT>").replace(str(Path.home()), "<HOME>")
    if isinstance(value, list):
        return [publish(item) for item in value]
    if isinstance(value, dict):
        return {key: publish(item) for key, item in value.items()}
    return value


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(publish(value), indent=2, sort_keys=True, allow_nan=False) + "\n")


def load_sets(path):
    values = json.loads(path.read_text())
    require(isinstance(values, list) and 1 <= len(values) <= 32, "One to32 diagnostic sets required")
    seen = set()
    for group in values:
        require(isinstance(group, dict) and set(group) == {"id", "pairs"}, "Only set ID and literal pairs accepted")
        require(isinstance(group["id"], str) and 0 < len(group["id"]) <= 200 and group["id"] not in seen,
                "Unique bounded set ID required")
        seen.add(group["id"])
        require(isinstance(group["pairs"], list) and 1 <= len(group["pairs"]) <= 50,
                "One to50 literal pairs per set required")
        for pair in group["pairs"]:
            require(isinstance(pair, dict) and set(pair) == {"query", "source"}
                    and isinstance(pair["query"], str) and pair["query"].strip()
                    and isinstance(pair["source"], str) and pair["source"], "Only nonempty literal query/source accepted")
            pair["query"].encode("utf-8")
            pair["source"].encode("utf-8")
    return values


def preflight(checkpoint=None):
    result = {"runtime": batch.runtime_preflight(), "weights_loaded": False, "model_calls": 0,
              "network": False, "batch_sizes": list(BATCH_SIZES), "max_padded_tokens": batch.MAX_PADDED_TOKENS,
              "implementation_sha256": {name: sha((ROOT / name).read_bytes()) for name in SOURCE_FILES},
              "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()))}
    if checkpoint is not None:
        result["checkpoint"] = prototype.checkpoint_preflight(checkpoint)
    return result


def build_plan(checkpoint, pairs_path, reference_path=None, scores_path=None):
    groups = load_sets(pairs_path)
    observed = preflight(checkpoint)
    require(not observed["runtime"]["errors"], "Pinned native runtime metadata failed")
    reference = None
    if reference_path is not None:
        reference_path = reference_path.resolve(strict=True)
        require(reference_path.is_relative_to(ROOT), "Reference plan must be inside project")
        raw = reference_path.read_bytes()
        original_sets = json.loads(raw)["parity_sets"]
        require(all(set(group) in ({"id", "pairs"}, {"id", "classification", "pairs"}) for group in original_sets),
                "Only known original classification metadata may be normalized")
        original = [{"id": group["id"], "pairs": group["pairs"]} for group in original_sets]
        require(original == groups, "Diagnostic must retain the complete original parity sets/peers")
        reference = {"path": str(reference_path.relative_to(ROOT)), "sha256": sha(raw)}
    witnesses = None
    if scores_path is not None:
        scores_path = scores_path.resolve(strict=True)
        require(scores_path.is_relative_to(ROOT), "Score witness must be inside project")
        witnesses = {"path": str(scores_path.relative_to(ROOT)), "sha256": sha(scores_path.read_bytes())}
    return {"version": 1, "classification": "Observed-development native precision decomposition; no new parity approval",
            "pairs_sha256": sha(pairs_path.read_bytes()), "set_ids": [group["id"] for group in groups],
            "set_count": len(groups), "pair_count": sum(len(group["pairs"]) for group in groups),
            "implementation_sha256": observed["implementation_sha256"], "packages": observed["packages"],
            "reranker": {"model_id": observed["checkpoint"]["model_id"], "revision": observed["checkpoint"]["revision"],
                         "checkpoint_files": observed["checkpoint"]["files"],
                         "runtime": {key: observed["runtime"][key] for key in ("packages", "qwen3_source_sha256")},
                         "supporting_sources": {name: row["sha256"] for name, row in
                                                observed["runtime"]["batching_supporting_sources"].items()}},
            "batch_sizes": list(BATCH_SIZES), "max_padded_tokens": batch.MAX_PADDED_TOKENS,
            "source_reference": reference, "score_witnesses": witnesses,
            "model_calls_in_freeze": 0, "weights_loaded_in_freeze": False,
            "shared_hidden_reuse": "Original batched head and singleton heads reuse one newly evaluated batched last-hidden tensor for diagnosis only",
            "performance_claim": "Backbone durations measure lazy graph construction; original projection durations synchronize the original backbone+head graph; none measure independent reranking requests"}


class Operations:
    def __init__(self, emit, *, clock=time.perf_counter):
        self.emit, self.clock = emit, clock
        self.attempted, self.completed, self.elapsed = {}, {}, {}

    def call(self, kind, identity, operation):
        self.attempted[kind] = self.attempted.get(kind, 0) + 1
        number = self.attempted[kind]
        self.emit({"event": "started", "kind": kind, "operation_number": number, **identity})
        started = self.clock()
        try:
            value = operation()
        except BaseException as error:
            self.emit({"event": "failed", "kind": kind, "operation_number": number, **identity,
                       "elapsed_s": self.clock() - started, "error_type": type(error).__name__, "error": str(error)})
            raise
        elapsed = self.clock() - started
        self.completed[kind] = self.completed.get(kind, 0) + 1
        self.elapsed[kind] = self.elapsed.get(kind, 0.0) + elapsed
        self.emit({"event": "completed", "kind": kind, "operation_number": number, **identity, "elapsed_s": elapsed})
        return value

    def counts(self):
        return {"attempted_operations": self.attempted.copy(), "completed_operations": self.completed.copy(),
                "completed_operation_elapsed_s": self.elapsed.copy()}


def project(model, final, yes_id, no_id, evaluate):
    require(tuple(final.shape[:2])[1:] == (1,), "Projection must receive one last state per row")
    logits = (model.model.embed_tokens.as_linear(final) if model.args.tie_word_embeddings else model.lm_head(final))
    require(tuple(logits.shape[:2]) == (len(final), 1), "Head must project only final states")
    evaluate(logits)
    return [prototype.binary_score(logits[index, 0, yes_id].item(), logits[index, 0, no_id].item())
            for index in range(len(final))]


def hidden_record(final, mx):
    import numpy as np
    values = np.asarray(final.astype(mx.float32))
    require(values.ndim == 3 and tuple(values.shape[:2]) == (1, 1)
            and np.isfinite(values).all(), "Finite singleton last-hidden vector required")
    row = np.ascontiguousarray(values.reshape(-1), dtype=np.float32)
    return {"native_dtype": str(final.dtype), "float32_dtype": str(row.dtype),
            "float32_shape": list(row.shape), "float32_sha256": sha(row.tobytes()), "values": row.tolist()}


def hidden_difference(before, after):
    import numpy as np
    left = np.asarray(before["values"], dtype=np.float64)
    right = np.asarray(after["values"], dtype=np.float64)
    require(left.shape == right.shape and left.size > 0 and np.isfinite(left).all() and np.isfinite(right).all(),
            "Matched finite hidden vectors required")
    delta = np.abs(left - right)
    return {"max_absolute": float(delta.max()), "mean_absolute": float(delta.mean()), "dimensions": int(left.size)}


def load_witnesses(path):
    witnesses = {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            require(row["status"] == "completed", "Original witness request is incomplete")
            key = row["identity"]["parity_set"], row["identity"]["batch_size"]
            require(key not in witnesses, "Duplicate original witness identity")
            result = row["reranker"]
            input_hashes = {pair["pair_index"]: pair["input_ids_sha256"] for receipt in result["batch_receipts"]
                            for pair in receipt["pairs"]}
            require(set(input_hashes) == set(range(len(result["results"]))), "Original input token witness incomplete")
            witnesses[key] = [{**score, "input_ids_sha256": input_hashes[index]}
                              for index, score in enumerate(result["results"])]
    return witnesses


def witness_comparison(reference, constructed, score, input_ids_sha256):
    expected = {key: value for key, value in reference.items()
                if key not in ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal", "input_ids_sha256")}
    require(expected == constructed and reference["input_ids_sha256"] == input_ids_sha256,
            "Fresh diagnostic input differs from original witness")
    errors = {field: abs(reference[field] - score[field])
              for field in ("yes_logit", "no_logit", "logit_difference", "yes_rank_signal")}
    return {"exact_score_match": all(value == 0 for value in errors.values()),
            "absolute_score_errors": errors, "original_score": {field: reference[field] for field in errors}}


def probe_set(model, built, set_id, yes_id, no_id, mx, operations, *, batch_sizes=BATCH_SIZES,
              max_padded_tokens=batch.MAX_PADDED_TOKENS, witnesses=None):
    """Fresh backbones; shared same-state projections are diagnostic reuse only."""
    sequential = []
    for index, pair in enumerate(built):
        batch.validate_built_pair(pair)
        identity = {"set_id": set_id, "pair_indexes": [index], "batch_size": 1, "shared_hidden_reuse": False}
        def singleton_backbone():
            hidden = model.model(mx.array([pair["input_ids"]]))
            require(tuple(hidden.shape[:2]) == (1, len(pair["input_ids"])), "Unexpected sequential backbone shape")
            final = hidden[:, -1:, :]
            return final
        final = operations.call("sequential_backbone", identity, singleton_backbone)
        score = operations.call("sequential_projection", identity,
                                lambda: project(model, final, yes_id, no_id, mx.eval))[0]
        # The original score is observed before this extra diagnostic sync/read.
        mx.eval(final)
        hidden = hidden_record(final, mx)
        materialized_score = operations.call("sequential_materialized_projection", identity | {"shared_hidden_reuse": True},
                                             lambda: project(model, final, yes_id, no_id, mx.eval))[0]
        row = {"pair_index": index, "constructed": {key: value for key, value in pair.items()
                           if key not in ("source", "input_ids")}, "input_ids": pair["input_ids"],
                           "input_ids_sha256": batch.digest(pair["input_ids"]),
                           "last_hidden": hidden, "score": score,
                           "materialized_singleton_projection_score": materialized_score}
        if witnesses is not None:
            row["original_witness_comparison"] = witness_comparison(witnesses[(set_id, 1)][index], row["constructed"],
                                                                     score, row["input_ids_sha256"])
        sequential.append(row)
    by_size = {}
    for size in batch_sizes:
        groups = batch.plan_batches(built, size, max_padded_tokens=max_padded_tokens)
        output, group_records = [None] * len(built), []
        for batch_index, group in enumerate(groups):
            pairs = [built[index] for index in group]
            layout = batch.batch_layout(pairs)
            identity = {"set_id": set_id, "batch_index": batch_index, "pair_indexes": group,
                        "batch_size": size, "layout": layout, "shared_hidden_reuse": False}
            def batched_backbone():
                if len(group) == 1:
                    hidden = model.model(mx.array([pairs[0]["input_ids"]]))
                    require(tuple(hidden.shape[:2]) == (1, len(pairs[0]["input_ids"])), "Unexpected singleton remainder")
                    final = hidden[:, -1:, :]
                else:
                    width = layout["padded_sequence_length"]
                    inputs = mx.array([pair["input_ids"] + [0] * (width - len(pair["input_ids"])) for pair in pairs])
                    hidden = model.model(inputs)
                    require(tuple(hidden.shape[:2]) == (len(group), width), "Unexpected padded backbone shape")
                    final = hidden[mx.array(list(range(len(group)))), mx.array(layout["last_token_positions"]), :][:, None, :]
                return final
            final = operations.call("batched_backbone", identity, batched_backbone)
            projection_identity = identity | {"shared_hidden_reuse": True}
            original_scores = operations.call("batched_projection", projection_identity,
                                              lambda: project(model, final, yes_id, no_id, mx.eval))
            mx.eval(final)
            observed_hiddens = [hidden_record(final[row:row + 1, :, :], mx) for row in range(len(group))]
            materialized_scores = operations.call("materialized_batched_projection", projection_identity,
                                                  lambda: project(model, final, yes_id, no_id, mx.eval))
            for row, index in enumerate(group):
                singleton = final[row:row + 1, :, :]
                singleton_identity = projection_identity | {"pair_indexes": [index], "projection_shape": [1, 1, final.shape[-1]]}
                row_score = operations.call("per_row_projection", singleton_identity,
                                           lambda: project(model, singleton, yes_id, no_id, mx.eval))[0]
                observed_hidden = observed_hiddens[row]
                output[index] = {"pair_index": index, "batch_index": batch_index,
                                 "input_ids_sha256": batch.digest(built[index]["input_ids"]),
                                 "last_hidden": observed_hidden,
                                 "last_hidden_difference_from_sequential": hidden_difference(sequential[index]["last_hidden"], observed_hidden),
                                 "original_batched_projection_score": original_scores[row],
                                 "materialized_batched_projection_on_same_hidden_score": materialized_scores[row],
                                 "per_row_projection_on_same_batched_hidden_score": row_score}
                if witnesses is not None:
                    output[index]["original_witness_comparison"] = witness_comparison(
                        witnesses[(set_id, size)][index], sequential[index]["constructed"], original_scores[row],
                        output[index]["input_ids_sha256"])
            group_records.append({"batch_index": batch_index, "pair_indexes": group, "layout": layout})
        require(all(row is not None for row in output), "Every original pair must be restored exactly once")
        by_size[str(size)] = {"rows": output, "groups": group_records}
    comparisons = ([row["original_witness_comparison"] for row in sequential]
                   + [row["original_witness_comparison"] for result in by_size.values() for row in result["rows"]]
                   if witnesses is not None else [])
    return {"set_id": set_id, "pairs": len(built), "sequential": sequential, "batched": by_size,
            "original_witness_controls_available": witnesses is not None,
            "original_witness_controls_exactly_reproduced": all(row["exact_score_match"] for row in comparisons) if comparisons else None,
            "materialization_order": "Original full final-token head evaluated before hidden serialization/per-row projection",
            "backbone_timing_scope": "Lazy graph construction only; original head evaluation synchronizes backbone execution",
            "shared_hidden_reuse": True, "diagnostic_only_not_reranking_latency": True}


def run(checkpoint, pairs_path, plan_path, output):
    require(os.getpid() == os.getpgrp(), "Explicit run requires an owned standalone process session/group")
    require(output.is_dir(), "Existing fresh output directory required")
    require(not any((output / name).exists() for name in ("run-start.json", "results.jsonl.gz", "operations.jsonl.gz",
                                                         "completion.json", "failure.json")), "No retry/overwrite of diagnostic attempts")
    raw_plan = plan_path.read_bytes()
    frozen = json.loads(raw_plan)
    reference = frozen["source_reference"]
    score_reference = frozen["score_witnesses"]
    current = build_plan(checkpoint, pairs_path, ROOT / reference["path"] if reference else None,
                         ROOT / score_reference["path"] if score_reference else None)
    require(set(frozen) == set(current) | {"frozen_at_utc", "source_commit"}, "Unexpected frozen diagnostic fields")
    for key, value in current.items():
        require(value == frozen[key], "Frozen diagnostic invariant changed: " + key)
    groups = load_sets(pairs_path)
    witnesses = load_witnesses(ROOT / score_reference["path"]) if score_reference else None
    write_new(output / "run-start.json", {"started_at_utc": datetime.now(timezone.utc).isoformat(),
              "frozen_plan_sha256": sha(raw_plan), "pid": os.getpid(), "process_group": os.getpgrp()})
    engine = resources = model = weights = mx = provenance = None
    operations = None
    completed_sets, complete, final_witnesses = 0, False, False
    pending_error = None
    old_signals = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    def interrupted(signum, _frame):
        raise InterruptedError("Owned diagnostic interrupted by signal " + str(signum))
    for signum in old_signals:
        signal.signal(signum, interrupted)
    try:
        with gzip.open(output / "operations.jsonl.gz", "xt", encoding="utf-8") as ledger, \
                gzip.open(output / "results.jsonl.gz", "xt", encoding="utf-8") as results:
            def emit(value):
                ledger.write(json.dumps(publish(value), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
                ledger.flush()
            operations = Operations(emit)
            engine, provenance, resources = batch.load_engine(checkpoint, emit)
            model, weights, mx = resources
            require(provenance["packages"] == frozen["packages"], "Loaded runtime package closure differs")
            require({key: provenance["runtime"][key] for key in ("packages", "qwen3_source_sha256")}
                    == frozen["reranker"]["runtime"], "Loaded runtime/source identity differs")
            write_new(output / "model-provenance.json", provenance)
            for group in groups:
                prototype.verify_witnesses(provenance["model"])
                built = [engine.builder.build(pair["query"], pair["source"]) for pair in group["pairs"]]
                result = probe_set(model, built, group["id"], engine.builder.yes_id[0], engine.builder.no_id[0],
                                   mx, operations, batch_sizes=frozen["batch_sizes"],
                                   max_padded_tokens=frozen["max_padded_tokens"], witnesses=witnesses)
                prototype.verify_witnesses(provenance["model"])
                results.write(json.dumps(publish(result), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
                results.flush()
                completed_sets += 1
                print(json.dumps({"completed_sets": completed_sets, "planned_sets": len(groups)}), flush=True)
            require({name: sha((ROOT / name).read_bytes()) for name in SOURCE_FILES} == frozen["implementation_sha256"],
                    "Diagnostic source changed during run")
            complete = completed_sets == len(groups)
    except BaseException as error:
        write_new(output / "failure.json", {"error_type": type(error).__name__, "error": str(error),
                  "traceback": traceback.format_exc(), "completed_sets": completed_sets,
                  "operation_counts": operations.counts() if operations else {}})
        # Preserve textual failure evidence, then drop traceback frames which
        # otherwise keep native model/hidden arguments alive during cleanup.
        pending_error = error.with_traceback(None)
        pending_error.__context__ = pending_error.__cause__ = None
    finally:
        for signum in old_signals:
            signal.signal(signum, signal.SIG_IGN)
        witness_error = None
        if provenance is not None:
            try:
                prototype.verify_witnesses(provenance["model"])
                final_witnesses = True
            except BaseException as error:
                witness_error = {"error_type": type(error).__name__, "error": str(error)}
        engine = resources = model = weights = None
        cleanup = {}
        if mx is not None:
            mx.clear_cache()
            cleanup = {"mlx_active_memory_after_cleanup": int(mx.get_active_memory()),
                       "mlx_process_lifetime_peak_bytes": int(mx.get_peak_memory()),
                       "memory_scope": "MLX allocator, not OS RSS"}
        write_new(output / "completion.json", {"complete": complete and final_witnesses,
                  "completed_sets": completed_sets, "planned_sets": len(groups),
                  "operation_counts": operations.counts() if operations else {}, "checkpoint_witnesses_final_passed": final_witnesses,
                  "checkpoint_witness_error": witness_error, "model_owners_released": engine is resources is model is weights is None,
                  "shared_hidden_reuse_diagnostic_only": True, "reranking_latency_measured": False,
                  "cleanup": cleanup, "finished_at_utc": datetime.now(timezone.utc).isoformat()})
        for signum, handler in old_signals.items():
            signal.signal(signum, handler)
        if witness_error is not None and complete:
            raise ValueError("Final checkpoint witness changed")
    if pending_error is not None:
        raise pending_error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--pairs", type=Path)
    parser.add_argument("--reference-plan", type=Path)
    parser.add_argument("--reference-scores", type=Path)
    parser.add_argument("--frozen-plan", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.run or args.freeze:
        if args.checkpoint is None or args.pairs is None or args.frozen_plan is None:
            parser.error("--run/--freeze require --checkpoint, --pairs and --frozen-plan")
        if args.freeze:
            plan = build_plan(args.checkpoint, args.pairs, args.reference_plan, args.reference_scores)
            plan["frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
            plan["source_commit"] = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
            write_new(args.frozen_plan, plan)
            print(json.dumps({"weights_loaded": False, "model_calls": 0, "sets": plan["set_count"],
                              "pairs": plan["pair_count"], "frozen_plan_sha256": sha(args.frozen_plan.read_bytes())}))
        else:
            if args.output is None:
                parser.error("--run requires --output")
            run(args.checkpoint, args.pairs, args.frozen_plan, args.output)
    else:
        print(json.dumps(preflight(args.checkpoint), sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
