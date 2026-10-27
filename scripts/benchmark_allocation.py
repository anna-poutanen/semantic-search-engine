#!/usr/bin/env python3
"""Prospective native profile for complementary-definition allocation.

The existing native engine supplies the same source, vector, schedule, budget
and dual-control gates. This profile adds the fixed allocation conditions and
copies the actual planner receipt after the backend's measured interval.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import statistics
import subprocess
from unittest.mock import patch

import benchmark_windows as native
from semantic_search import allocation, selection


ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = {
    "legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
    "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
    "aspect_fill": {"selector": "aspect_fill_context", "postprocessor": "coalesce_context"},
    "aspect_balanced": {"selector": "aspect_balanced_context", "postprocessor": "coalesce_context"},
}
CANDIDATES = ("aspect_fill", "aspect_balanced")
EXTRA_BINDINGS = (
    "scripts/benchmark_allocation.py", "tests/test_allocation_benchmark.py",
    "scripts/replay_allocation.py", "tests/test_allocation_replay.py",
    "scripts/audit_allocation.py", "tests/test_allocation_audit.py",
    "src/semantic_search/allocation.py", "tests/test_allocation.py",
    "docs/allocation-native-protocol.md", "docs/allocation-development-protocol.md",
)
PARENT_EXTRAS = ("control-references.json", "historical-provenance.json")
_base_parent = native.parent_binding
_base_identity = native.identity
_base_verify = native.verify_plan
_base_capture = native.capture
_base_receipt = native.receipt
_base_write = native.write
_base_summarize = native.summarize


def _ancestor(first, second):
    native.require(subprocess.run(
        ["git", "merge-base", "--is-ancestor", first, second], cwd=ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0,
        "Prospective allocation freeze/execution must be current-branch ancestors")


def parent_binding(args):
    native.require(args.development_study is not None,
                   "Every allocation native role requires a qualified development parent")
    folder = args.development_study
    summary, plan, audit = (native.read(folder / name) for name in
                            ("summary.json", "frozen-plan.json", "independent-audit.json"))
    native.require(all(value.get("study") == "allocation" for value in (summary, plan, audit)),
                   "Native allocation requires its own allocation development study and audit")
    native.require(native.read(folder / "control-gate.json").get("exact_parent_control_rows") == 730,
                   "Allocation development must reproduce all 730 immutable parent controls")
    bound = _base_parent(args)
    for name in PARENT_EXTRAS:
        native.require(plan["artifacts"].get(name) == native.file_sha(folder / name),
                       "Allocation history/control manifest changed: " + name)
        bound["artifacts"][native.relative(folder / name)] = native.file_sha(folder / name)
    for name, digest in plan["historical_parent"]["artifacts"].items():
        native.require(native.file_sha(ROOT / name) == digest, "Immutable original history changed: " + name)
        bound["artifacts"][name] = digest
    started = native.read(folder / "run-start.json")
    freeze_commit = started["prospective_freeze"]["commit"]
    execution = started["execution_commit"]
    native.require(audit.get("producer_execution") == execution
                   and audit.get("producer_freeze") == started["prospective_freeze"],
                   "Allocation audit must certify the actual prospective freeze/execution")
    _ancestor(freeze_commit, execution)
    _ancestor(execution, "HEAD")
    stamp = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", freeze_commit],
                                        cwd=ROOT, text=True))
    native.require(stamp <= datetime.fromisoformat(started["created_at_utc"]).timestamp(),
                   "Allocation development freeze follows execution")
    frozen = {**plan["producer_sha256"], **plan["input_sha256"],
              **plan["historical_parent"]["artifacts"]}
    for name in ("frozen-plan.json", "baseline-gate.json", "prior-coverage-gate.json",
                 "source-fixtures.json", *PARENT_EXTRAS):
        frozen[native.relative(folder / name)] = native.file_sha(folder / name)
    native.require(started["prospective_freeze"] == {
        "commit": freeze_commit, "committed_at_unix": stamp, "bound_blob_count": len(frozen)},
        "Allocation prospective freeze receipt differs from bound files")
    for name, digest in frozen.items():
        original = subprocess.check_output(["git", "show", freeze_commit + ":" + name], cwd=ROOT)
        native.require(native.sha(original) == digest, "Allocation development freeze blob changed: " + name)
    bound["prospective_freeze"] = {"commit": freeze_commit, "execution_commit": execution,
                                    "committed_at_unix": stamp, "bound_blob_count": len(frozen)}
    return bound


def identity(args):
    native.require(args.development_study is not None,
                   "Every allocation native role requires a qualified development parent")
    data, sources, inventory, binding = _base_identity(args)
    native.require(binding.get("development_parent") is not None,
                   "Qualified allocation development parent required before native work")
    binding["study"] = "allocation"
    for name in EXTRA_BINDINGS:
        binding["implementation_sha256"][name] = native.file_sha(ROOT / name)
    return data, sources, inventory, binding


def verify_plan(args, binding):
    plan = _base_verify(args, binding)
    native.require(plan.get("study") == "allocation", "Native plan belongs to another study")
    name = native.relative(args.output / "plan.json")
    freeze_commit = subprocess.check_output(["git", "log", "-1", "--format=%H", "--", name],
                                            cwd=ROOT, text=True).strip()
    native.require(bool(freeze_commit), "Prospective native allocation plan must be committed")
    _ancestor(freeze_commit, "HEAD")
    stamp = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", freeze_commit],
                                        cwd=ROOT, text=True))
    native.require(stamp <= datetime.now(timezone.utc).timestamp(),
                   "Prospective native freeze follows verification time")
    bound = dict(binding["implementation_sha256"])
    bound.update(binding["development_parent"]["artifacts"])
    bound.update(binding.get("definition_preflight") or {})
    bound.update({native.relative(args.output / path): digest for path, digest in plan["artifacts"].items()})
    bound[name] = native.file_sha(args.output / "plan.json")
    for path, digest in bound.items():
        original = subprocess.check_output(["git", "show", freeze_commit + ":" + path], cwd=ROOT)
        native.require(native.sha(original) == digest, "Prospective native allocation freeze blob changed: " + path)
    return plan


@contextmanager
def capture(condition):
    receipts = []
    planner = allocation.allocation_plan

    def recorded(*args, **kwargs):
        value = planner(*args, **kwargs)
        receipts.append(value[3])  # Reference only: copying stays outside timing.
        return value

    with patch.object(allocation, "allocation_plan", recorded):
        with _base_capture(condition) as state:
            yield state
    expected = int(condition in CANDIDATES)
    native.require(len(receipts) == expected,
                   "Native controls need zero planner calls; allocation candidates need exactly one")
    state["allocation_planner_calls"] = len(receipts)
    state["allocation_receipt"] = deepcopy(receipts[0]) if receipts else None


def receipt(*args, **kwargs):
    result = _base_receipt(*args, **kwargs)
    captured = args[6] if len(args) > 6 else kwargs["captured"]
    result["allocation_planner_calls"] = captured["allocation_planner_calls"]
    result["allocation_receipt"] = captured["allocation_receipt"]
    return result


def summarize(rows):
    result = _base_summarize(rows)
    for retrieval, metrics in result["per_retrieval"].items():
        candidates = [row for row in rows if row["retrieval"] == retrieval and row["arm"] == "candidate"]
        active = [row for row in candidates if row["allocation_receipt"]["activated"]]
        changed = [row for row in candidates if row["allocation_receipt"]["baseline_eligible_ids"]
                   != [item["snippet"]["id"] for item in row["stages"]["eligible"]]]
        controls = {control: {(row["query_id"], row["repetition"]): row["elapsed_backend_s"]
                             for row in rows if row["retrieval"] == retrieval and row["arm"] == control}
                    for control in ("legacy", "coverage_control")}
        metrics["allocation_activity"] = {
            "measured_candidate_calls": len(candidates), "literal_clause_active_calls": len(active),
            "accepted_proposal_calls": sum(row["allocation_receipt"]["accepted"] for row in candidates),
            "changed_allocation_calls": len(changed), "inactive_calls": len(candidates) - len(active),
            "fallback_counts": dict(Counter(row["allocation_receipt"]["fallback_reason"]
                                            for row in candidates if row["allocation_receipt"]["fallback_reason"])),
            "changed_query_ids_first_repetition": sorted(row["query_id"] for row in changed if row["repetition"] == 1),
            "changed_paired_median_delta_s": {
                control: statistics.median(row["elapsed_backend_s"]
                    - controls[control][(row["query_id"], row["repetition"])] for row in changed) if changed else None
                for control in controls},
        }
    return result


def write(path, value):
    # Update metadata before the original exclusive writer; never rewrite output.
    value = dict(value)
    if path.name in ("plan.json", "run-started.json", "summary.json", "failure.json"):
        value["study"] = "allocation"
    if path.name == "plan.json":
        value["candidate_reason"] = (
            "One complementary-definition allocation condition qualified against BOTH controls, "
            "including an additional distinct complete query beyond prior coverage, and was fixed "
            "before fresh outcomes. Literal-term preservation is a heuristic, not correctness evidence.")
    return _base_write(path, value)


@contextmanager
def profile():
    """Apply the new experiment only inside this invocation; restore old hooks."""
    with patch.object(native, "ROOT", ROOT), \
            patch.object(native, "__doc__", __doc__), \
            patch.object(native, "CONDITIONS", CONDITIONS), \
            patch.object(native, "CANDIDATES", CANDIDATES), \
            patch.object(selection, "aspect_fill_context", allocation.aspect_fill_context, create=True), \
            patch.object(selection, "aspect_balanced_context", allocation.aspect_balanced_context, create=True), \
            patch.object(native, "parent_binding", parent_binding), \
            patch.object(native, "identity", identity), \
            patch.object(native, "verify_plan", verify_plan), \
            patch.object(native, "capture", capture), \
            patch.object(native, "receipt", receipt), \
            patch.object(native, "summarize", summarize), \
            patch.object(native, "write", write):
        yield


def freeze(args):
    with profile():
        return native.freeze(args)


def run(args):
    with profile():
        return native.run(args)


def main():
    with profile():
        native.main()


if __name__ == "__main__":
    main()
