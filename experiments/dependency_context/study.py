"""Frozen observed-data evaluation; no native inference or fresh quality claim."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.abc
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from unittest.mock import patch

from experiments.dependency_context import study_inputs as inputs
from experiments.dependency_context.annotated_dependencies import AnnotatedDependencies
from experiments.dependency_context.guard import apply_dependency_guard
from experiments.dependency_context.skip import skip_certificate
from scripts.semantic_model_study import compare, evidence
from semantic_search.context import compact_result, encoding, serialize_context
from semantic_search.corpus import Snippet


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "dependency_context_study_v1"
FREEZE = "study-freeze.json"


def source_authority(root=ROOT):
    """Reject imports supplied by another editable checkout before data reads."""
    root = Path(root).resolve()
    for name, module in tuple(sys.modules.items()):
        if name == "semantic_search" or name.startswith("semantic_search."):
            base = root / "src/semantic_search"
        elif name.startswith("scripts."):
            base = root / "scripts"
        elif name == "experiments" or name.startswith("experiments."):
            base = root / "experiments"
        else:
            continue
        path = getattr(module, "__file__", None)
        inputs.require(path is not None and Path(path).resolve().is_relative_to(base),
            "Executing source differs from this checkout; set PYTHONPATH=src:.: " + name)


def freeze_path(folder):
    # The input loader requires its exact artifact closure. Keep this execution
    # binding beside that directory, rather than inserting an unexpected member.
    return Path(folder).resolve().parent / FREEZE


def write_new(path, value):
    with Path(path).open("xb") as stream:
        stream.write(inputs.canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(Path(path).parent)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Ledger:
    """Durable lossless gzip rows; preserve an original failure during close."""
    def __init__(self, path):
        self.path = Path(path)
        self.raw = self.path.open("xb")
        self.stream = None
        try:
            self.stream = gzip.GzipFile(filename="", fileobj=self.raw, mode="wb", mtime=0)
            sync_directory(self.path.parent)
        except BaseException as error:
            try:
                try:
                    if self.stream is not None:
                        self.stream.close()
                finally:
                    self.raw.close()
            except BaseException as cleanup_error:
                raise error from cleanup_error
            raise

    def __enter__(self):
        return self

    def append(self, row):
        # Transport order is part of compact scope/tool bytes. Canonicalization
        # is only for hashes, never a rewrite of nested saved operands.
        inputs.canonical(row)
        self.stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n")
        self.stream.flush()
        self.raw.flush()
        os.fsync(self.raw.fileno())

    def __exit__(self, kind, error, traceback):
        try:
            try:
                self.stream.close()
                self.raw.flush()
                os.fsync(self.raw.fileno())
            finally:
                self.raw.close()
            sync_directory(self.path.parent)
        except BaseException as close_error:
            if error is not None:
                raise error from close_error
            raise


def source_hashes(root=ROOT):
    return inputs.source_hashes(root)


def runtime():
    from scripts.replay_selection import CACHE_KEY, CACHE_SHA256

    source_authority()
    cache = Path(os.environ.get("TIKTOKEN_CACHE_DIR", ROOT / ".cache/tokenizers"))
    inputs.require(inputs.file_sha(cache / CACHE_KEY) == CACHE_SHA256, "Verified offline cl100k cache required")
    return {"python": sys.version, "packages": {name: importlib.metadata.version(name)
            for name in ("tiktoken", "tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript")},
            "tokenizer": {"encoding": "cl100k_base", "sha256": CACHE_SHA256}}


def freeze_inputs(folder, root=ROOT):
    root, folder = Path(root).resolve(), Path(folder).resolve()
    inputs.require(folder.is_relative_to(root), "Freeze directory must be in the repository")
    inputs.require(freeze_path(folder).is_relative_to(root), "Execution freeze escapes repository")
    prepared = inputs.load_prepared(folder, root=root)
    artifacts = {path.relative_to(root).as_posix(): inputs.file_sha(path)
                 for path in sorted(folder.iterdir()) if path.is_file() and path.name != FREEZE}
    value = {"schema": SCHEMA, "classification": "Observed CPU development only",
        "sources": source_hashes(root), "artifacts": artifacts, "runtime": runtime(),
        "prepared_plan_sha256": inputs.file_sha(folder / "frozen-plan.json"),
        "counts": {"decisions": len(prepared["records"]), "quality": sum(r["quality"] for r in prepared["records"])},
        "new_guard_outcomes": 0, "native_calls": 0, "default_change": False}
    write_new(freeze_path(folder), value)
    return value


def verify_freeze(folder, commit, *, root=ROOT):
    root, folder = Path(root).resolve(), Path(folder).resolve()
    inputs.require(folder.is_relative_to(root), "Prepared inputs escape repository")
    value = inputs.read(freeze_path(folder))
    inputs.require(value["schema"] == SCHEMA and value["new_guard_outcomes"] == 0
        and value["native_calls"] == 0 and value["default_change"] is False, "Prospective freeze required")
    actual = {path.relative_to(root).as_posix(): inputs.file_sha(path)
              for path in sorted(folder.iterdir()) if path.is_file()}
    inputs.require(inputs.same(value["artifacts"], actual)
        and value["prepared_plan_sha256"] == inputs.file_sha(folder/"frozen-plan.json"),
        "Exact prepared artifact path/closure/plan differs from prospective freeze")
    inputs.require(value["sources"] == source_hashes(root) and value["runtime"] == runtime(), "Frozen source/runtime changed")
    inputs.require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"],
        capture_output=True).returncode == 0, "Full prospective freeze must remain an ancestor")
    inputs.require(not inputs.git(root, "status", "--porcelain", "--untracked-files=no").strip(), "Tracked worktree is dirty")
    hashes = {**value["sources"], **value["artifacts"], freeze_path(folder).relative_to(root).as_posix(): inputs.file_sha(freeze_path(folder))}
    inputs.verify_blobs(root, hashes, commit=commit)
    inputs.verify_blobs(root, hashes, commit=inputs.git(root, "rev-parse", "HEAD").decode().strip())
    when = datetime.fromisoformat(inputs.git(root, "show", "-s", "--format=%cI", commit).decode().strip())
    inputs.require(when <= datetime.now(timezone.utc), "Freeze cannot follow outcomes")
    return {"commit": commit, "execution_commit": inputs.git(root, "rev-parse", "HEAD").decode().strip(),
            "freeze_sha256": inputs.file_sha(freeze_path(folder)), "bound_blobs": len(hashes)}


@contextmanager
def cpu_only():
    blocked = {"mlx", "mlx_embeddings", "torch", "transformers", "safetensors", "sentence_transformers"}

    class RefuseModels(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in blocked:
                raise RuntimeError("Model import forbidden in CPU development: " + fullname)

    inputs.require(not any(name.split(".")[0] in blocked for name in sys.modules), "Use a clean CPU process")
    finder = RefuseModels()
    sys.meta_path.insert(0, finder)

    def denied(*args, **kwargs):
        raise RuntimeError("Network forbidden in CPU development")

    try:
        with patch.object(socket, "create_connection", denied), patch.object(socket, "getaddrinfo", denied), \
             patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), \
             patch.object(socket.socket, "sendto", denied):
            yield
    finally:
        sys.meta_path.remove(finder)


def summarize(outcomes):
    scored = [row for row in outcomes if row["quality"]]
    arms = ("legacy", "coverage", "prior_role")
    losses = {arm: [] for arm in arms}
    gains = set()
    for row in scored:
        for arm in arms:
            change = row["comparisons"][arm]
            if change["group_losses"] or change["complete_loss"] or change["single_file_first_hit_loss"]:
                losses[arm].append({"id": row["id"], "dataset": row["dataset"], "query_id": row["query_id"],
                                    "mode": row["mode"], **change})
        if row["multi_file"] and row["comparisons"]["prior_role"]["complete_gain"]:
            gains.add((row["dataset"], row["query_id"]))
    metrics = {}
    for arm in (*arms, "candidate"):
        views = [row["scores"][arm] for row in scored]
        metrics[arm] = {"groups": sum(view["covered_groups"] for view in views),
            "complete": sum(view["complete"] for view in views),
            "multi_file_complete": sum(row["multi_file"] and row["scores"][arm]["complete"] for row in scored),
            "single_file_first_hit": sum(view["single_file_first_hit"] for view in views)}
    return {"decisions": len(outcomes), "quality_occurrences": len(scored),
        "distinct_units": len({(row["dataset"], row["query_id"]) for row in scored}),
        "metrics": metrics, "quality_gate": {"qualified": bool(gains) and not any(losses.values()),
            "individual_losses": losses, "distinct_complete_multi_gains_vs_prior_role": [list(pair) for pair in sorted(gains)],
            "fresh_native_required": True, "default_change": False},
        "activity": {"accepted": sum(row["guard_receipt"]["accepted"] for row in outcomes),
            "changed_vs_prior_role": sum(row["changed_vs_prior_role"] for row in outcomes),
            "protected_pairs": sum(len(row["guard_receipt"]["pairs"]) for row in outcomes),
            "skip_certificates": sum(row["skip_certificate"]["eligible"] for row in outcomes),
            "fallback_reasons": dict(Counter(row["guard_receipt"]["fallback_reason"] or "accepted" for row in outcomes))}}


def same_delivered_response(left, right):
    """Compare exact saved context, allowing only different timing envelopes.

    Post-outcome harness correction: an original native request can retain
    baseline and final timing snapshots for the same delivered context. A
    structural skip proof does not imply equality of those stopwatch values.
    This does not normalize rows, token counts, indexstats or any other field.
    The actual terminal result must still be one exact full frozen operand.
    """
    if type(left) is not dict or type(right) is not dict or not inputs.literal_json(left) or not inputs.literal_json(right):
        return False
    if ("timings" in left) != ("timings" in right):
        return False
    if "timings" in left and (type(left["timings"]) is not dict or type(right["timings"]) is not dict):
        return False
    return (inputs.same({key: value for key, value in left.items() if key != "timings"},
                        {key: value for key, value in right.items() if key != "timings"})
            and serialize_context(compact_result(left)) == serialize_context(compact_result(right)))


def replay(prepared, output, *, binding, counter=None, graph_factory=AnnotatedDependencies,
           guard=apply_dependency_guard, certificate=skip_certificate):
    counter = encoding() if counter is None else counter
    graphs, outcomes = {}, []
    records = inputs.unique(prepared["records"], "id")
    operands = inputs.unique(prepared["operands"], "id")
    inputs.require(records.keys() == operands.keys(), "Complete evaluator/selector operand membership required")
    output = Path(output)
    inputs.require(output.parent.is_dir(), "Attempt parent must already exist")
    output.mkdir(exist_ok=False)
    sync_directory(output.parent)
    began = time.perf_counter()
    write_new(output / "run-start.json", {"schema": SCHEMA, "binding": binding,
        "started_at_utc": datetime.now(timezone.utc).isoformat(), "classification": "Observed CPU development; no inference/native latency"})
    try:
        graph_records = []
        for identifier, bundle in sorted(prepared["source_bundles"].items()):
            graph = graph_factory(bundle["sources"], [Snippet(**item) for item in bundle["inventory"]],
                                  definition_keys=bundle["definition_keys"])
            graphs[identifier] = graph.witnesses()
            graph_records.append({"source_bundle_id": identifier, "receipt": graphs[identifier]})
        with Ledger(output / "graphs.jsonl.gz") as stream:
            for record in graph_records:
                stream.append(record)
        with Ledger(output / "outcomes.jsonl.gz") as stream:
            for identifier, operand in operands.items():
                record = records[identifier]
                facts = graphs[operand["source_bundle_id"]]
                response, receipt = guard(deepcopy(operand["reference"]), deepcopy(operand["proposal"]),
                    dependencies=facts, definition_keys=deepcopy(operand["definition_keys"]))
                inputs.require(inputs.same(response, operand["reference"]) or inputs.same(response, operand["proposal"]),
                               "Terminal result must be one exact frozen operand")
                cert = certificate(operand["reference"], operand["raw_rows"], dependencies=facts,
                    definition_keys=operand["definition_keys"], precoalesce_rows=operand["precoalesce_rows"],
                    max_results=operand["limits"]["max_results"])
                inputs.require(not cert["eligible"] or same_delivered_response(response, operand["reference"]), "Skip proof disagrees with saved terminal response")
                payload = serialize_context(compact_result(response))
                tokens = len(counter.encode(payload, disallowed_special=()))
                limits = operand["limits"]
                inputs.require(tokens == response["token_usage"]["tokens"] <= limits["token_budget"]
                    and len(response["results"]) <= limits["max_results"]
                    and sum(len(r["source"]) for r in response["results"]) == response["total_source_chars"] <= limits["source_budget"],
                    "Exact context/source/token caps differ")
                scores = {arm: evidence(record["scoring"], value) for arm, value in
                    (("legacy", record["legacy"]), ("coverage", operand["reference"]),
                     ("prior_role", operand["proposal"]), ("candidate", response))}
                row = {"id": identifier, **{name: record[name] for name in ("dataset", "query_id", "mode", "repetition", "quality")},
                    "multi_file": record["scoring"]["multi_file"], "operand_sha256": inputs.sha(inputs.canonical(operand)),
                    "graph_receipt_sha256": inputs.sha(inputs.canonical(facts)), "response": response,
                    "guard_receipt": receipt, "skip_certificate": cert, "compact_payload": payload,
                    "compact_sha256": inputs.sha(payload.encode()), "actual_compact_tokens": tokens,
                    "changed_vs_prior_role": not inputs.same(response, operand["proposal"]), "scores": scores,
                    "comparisons": {arm: compare(score, scores["candidate"]) for arm, score in scores.items() if arm != "candidate"}}
                stream.append(row)
                outcomes.append(row)
        summary = {"schema": SCHEMA, "status": "passed", **summarize(outcomes),
            "terminal_decisions": len(outcomes), "certificate_checks": len(outcomes),
            "new_work": {name: 0 for name in ("v1_proposals", "model", "index", "retrieval", "coalesce", "backfill", "network")},
            "cpu_total_s_including_graph_and_io": time.perf_counter()-began,
            "artifacts": {name: inputs.file_sha(output/name) for name in ("run-start.json", "graphs.jsonl.gz", "outcomes.jsonl.gz")},
            "classification": "Observed CPU development; neither native warm latency nor fresh quality"}
        write_new(output / "summary.json", summary)
        return summary
    except BaseException as error:
        try:
            write_new(output / "failure.json", {"error_type": f"{type(error).__module__}.{type(error).__qualname__}",
                "stored_outcomes": len(outcomes), "resume_allowed": False})
        except BaseException as receipt_error:
            raise error from receipt_error
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", type=Path)
    mode.add_argument("--run", type=Path)
    parser.add_argument("--corpus-root", type=Path)
    parser.add_argument("--freeze")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source_authority()
    if args.prepare:
        inputs.require(args.corpus_root is not None, "Preparation needs an explicit original corpus root")
        inputs.prepare(ROOT, args.prepare, corpus_root=args.corpus_root)
        result = freeze_inputs(args.prepare)
    else:
        inputs.require(args.freeze and args.output, "Run needs full --freeze and exclusive --output")
        binding = verify_freeze(args.run, args.freeze)
        prepared = inputs.load_prepared(args.run, root=ROOT)
        with cpu_only():
            result = replay(prepared, args.output, binding=binding)
        verify_freeze(args.run, args.freeze)
    print(json.dumps({key: result[key] for key in ("status", "counts", "quality_gate") if key in result}))


if __name__ == "__main__":
    main()
