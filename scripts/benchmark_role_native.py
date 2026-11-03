#!/usr/bin/env python3
"""Prospectively freeze and measure the qualified terminal role guard.

Preparation is source-only. The observed, independently audited development
parent and committed native sources are checked before fresh inputs are read.
Each measured candidate uses the actual retrieval query vector, resident index
vectors, fresh within-query clause encoding, one v1 proposal and one role guard.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

import benchmark_flow_allocation as frozen
import benchmark_windows as base
import capture_semantic_request as capture_helpers
import research_role_context as role
import semantic_model_study as transport
from semantic_search import backend as backend_module, context, selection
from semantic_search import semantic_context as semantic_module, semantic_context_adapter as adapter_module, semantic_provider as provider_module
from semantic_search.context_backfill import coalesce_context
from semantic_search.embeddings import QwenEmbedder
from semantic_search.semantic_context_adapter import make_live_semantic_postprocessor


ROOT = Path(__file__).resolve().parents[1]
SCHEMA, CANDIDATE = "role_context_native_v1", "role_state_write_v1"
PARENT_PUBLICATION = "597c5ed7235bcc1cc2ef95a8b9af0c0eb57a32ae"
RESERVED_DATASET = "benchmarks/attrs_complementary_holdout_queries.json"
RESERVED_DATASET_SHA256 = "2170ca4c21fc3750233a76a905918a914e9f2e0ba50dda3c30064cdefa89923d"
RESERVED_REVISION = "3dca08ce5bbe673d7df25f44f3dda92505d1043d"
SETTINGS, GATE = dict(base.SETTINGS), dict(base.GATE)
ARMS, RETRIEVALS, REPETITIONS, WARM_QUERY = base.ARMS, base.RETRIEVALS, 3, base.WARM_QUERY
NATIVE_SOURCES = ("scripts/benchmark_role_native.py", "scripts/audit_role_native.py",
                  "tests/test_role_native.py", "tests/test_role_native_audit.py", "docs/role-native-protocol.md")
CONDITIONS = {
    "legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
    "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
    CANDIDATE: {"selector": "definition_context", "postprocessor": "live_semantic_then_terminal_role_guard"},
}
require, sha, file_sha, canonical, read = base.require, base.sha, base.file_sha, base.canonical, base.read
schedule, pair_rows, source_view = base.schedule, base.pair_rows, frozen.source_view
write = transport.write_new


def utc():
    return datetime.now(timezone.utc).isoformat()


def machine_state():
    return {"recorded_at_utc": utc(), "platform": platform.platform(), "machine": platform.machine(),
            "python": platform.python_version(), "pid": os.getpid(),
            "power": subprocess.check_output(["pmset", "-g", "batt"], text=True)
                if platform.system() == "Darwin" else "pmset is not applicable to this platform"}


def sync(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def relative(path):
    return base.relative(path, ROOT)


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def committed(paths, commit="HEAD"):
    for name, expected in paths.items():
        require(file_sha(ROOT / name) == expected == sha(subprocess.check_output(
            ["git", "-C", str(ROOT), "show", commit + ":" + name])),
            "Changed or uncommitted bound file: " + name)
    require(not git("status", "--porcelain", "--", *paths), "Bound files have staged or unstaged edits")


def parent_binding(args):
    """This function must precede every fresh/checkpoint/tokenizer read."""
    require(args.candidate == CANDIDATE, "One frozen role candidate is required")
    folder = Path(args.development_study)
    summary, audit = read(folder / "replay/summary.json"), read(folder / "independent-audit.json")
    gate = audit.get("independently_recomputed_qualification", {})
    require(summary.get("schema") == "role_context_study_v1" and summary.get("mode") == CANDIDATE
            and summary.get("status") == "passed" and summary.get("quality_gate", {}).get("qualified") is True
            and audit.get("schema") == "role_context_audit_v1" and audit.get("status") == "passed"
            and audit.get("checks_passed") is True and gate.get("qualified") is True,
            "The actual independent role development audit must qualify")
    require((audit.get("verified_candidate_rows"), audit.get("exact_control_rows"),
             audit.get("retained_negative_references"), audit.get("distinct_query_units")) == (393, 786, 3734, 201)
            and audit.get("referenced_saved_v1_proposals") == 393,
            "Complete393/786/3734/201 development proof required")
    require(set(gate.get("against_controls", {})) == {"legacy", "coverage_control"}
            and all(not loss[name] for loss in gate["against_controls"].values()
                    for name in ("group_losses", "complete_losses", "single_file_first_hit_losses"))
            and len(gate.get("additional_distinct_complete_multi_queries_vs_coverage", [])) >= 1
            and all(type(audit.get(name)) is int and audit[name] == 0
                    for name in ("model_calls", "retrieval_calls", "network_calls", "attrs_reads")),
            "Development must have no individual loss and a distinct complete multi-file gain")
    start = read(folder / "replay/run-start.json")
    producer = audit["producer_freeze"]
    require(start["binding"]["freeze"]["commit"] == producer["commit"]
            and start["binding"]["execution_commit"] == producer["execution_commit"],
            "Development freeze/execution identity differs")
    prepared = folder / "prepared-v2"
    plan = read(prepared / "frozen-plan.json")
    artifacts = {**plan["parent_binding"]["sha256"], **plan["source_sha256"],
                 **{relative(prepared / name): digest for name, digest in plan["artifact_sha256"].items()}}
    for path in (prepared / "frozen-plan.json", folder / "replay/run-start.json", folder / "replay/outcomes.jsonl.gz",
                 folder / "replay/summary.json", folder / "independent-audit.json"):
        artifacts[relative(path)] = file_sha(path)
    require(summary["artifact_sha256"] == {name: file_sha(folder / "replay" / name)
            for name in ("run-start.json", "outcomes.jsonl.gz")}, "Audited development bytes changed")
    committed(artifacts, PARENT_PUBLICATION)
    committed(artifacts)
    for older, newer in ((producer["commit"], producer["execution_commit"]),
                         (producer["execution_commit"], PARENT_PUBLICATION), (PARENT_PUBLICATION, "HEAD")):
        subprocess.check_call(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", older, newer])
    return {"publication_commit": PARENT_PUBLICATION, "artifacts": artifacts, "qualification": gate,
            "prepared_path": relative(prepared), "replay_path": relative(folder / "replay"),
            "audit_path": relative(folder / "independent-audit.json"),
            "producer_freeze": producer, "classification": "Observed development; no fresh outcomes used to choose this rule"}


def source_authority():
    for module, path in ((base, "scripts/benchmark_windows.py"), (frozen, "scripts/benchmark_flow_allocation.py"),
                         (capture_helpers, "scripts/capture_semantic_request.py"), (role, "scripts/research_role_context.py"),
                         (transport, "scripts/semantic_model_study.py"), (backend_module, "src/semantic_search/backend.py")):
        require(Path(module.__file__).resolve() == ROOT / path, "Executing module source authority differs: " + path)
    capture_helpers._source_hashes()
    script_names = {path.stem for path in (ROOT / "scripts").glob("*.py")}
    for name, module in list(sys.modules.items()):
        if name == "semantic_search" or name.startswith("semantic_search."):
            path = getattr(module, "__file__", None)
            require(path is not None and Path(path).resolve().is_relative_to(ROOT / "src/semantic_search"),
                    "Executing product module source authority differs: " + name)
        elif name in script_names:
            require(Path(module.__file__).resolve() == ROOT / "scripts" / (name + ".py"),
                    "Executing research helper source authority differs: " + name)


def identity(args):
    require(args.repetitions == 3 and args.role == "repository_holdout", "Fixed three-repetition repository holdout required")
    source_authority()
    parent = parent_binding(args)
    committed({name: file_sha(ROOT / name) for name in NATIVE_SOURCES})
    source_commit = git("log", "-1", "--format=%H", "--", *NATIVE_SOURCES)
    require(bool(source_commit), "Commit native source before reading fresh inputs")
    source_paths = {relative(path): file_sha(path) for path in (ROOT / "scripts").glob("*.py")}
    source_paths.update({relative(path): file_sha(path) for path in (ROOT / "src/semantic_search").rglob("*.py")})
    source_paths.update({name: file_sha(ROOT / name) for name in (*NATIVE_SOURCES, "pyproject.toml", "requirements-mac.lock")})
    committed(source_paths)
    committed({**parent["artifacts"], **source_paths}, source_commit)
    subprocess.check_call(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", source_commit, "HEAD"])
    require(relative(args.dataset) == RESERVED_DATASET and file_sha(args.dataset) == RESERVED_DATASET_SHA256,
            "Use the unchanged, previously reserved dataset")
    # Reuse source/extraction/graph/evidence-only preparation, never its planner.
    # The old external preflight is TypeScript-only (five-part byte keys).
    # This runner emits the production inventory; the new independent input
    # barrier verifies Python AST definitions/windows before native execution.
    with patch.object(frozen, "definition_preflight", lambda *unused: None):
        data, sources, inventory, binding = frozen._identity_after_parent(args, parent)
    require(data["revision"] == RESERVED_REVISION, "Use the unchanged reserved source revision")
    freshness = {"path": relative(args.freshness_review), "sha256": file_sha(args.freshness_review)}
    published_fresh = {relative(path): file_sha(path) for path in (args.dataset, args.source_review, args.freshness_review)}
    if data.get("source_review_protocol"):
        published_fresh[data["source_review_protocol"]] = file_sha(ROOT / data["source_review_protocol"])
    committed(published_fresh, source_commit)
    committed(published_fresh, PARENT_PUBLICATION)
    runtime = transport.verify_runtime(ROOT, args.runtime_manifest, "HEAD", args.model)
    require(binding["embedding"] == runtime["model_binding"]["metadata"], "Executing model metadata differs")
    extras = (*NATIVE_SOURCES, "scripts/research_role_context.py", "tests/test_role_context.py",
              "scripts/run_role_context.py", "tests/test_role_context_study.py", "scripts/audit_role_context.py",
              "tests/test_role_context_audit.py", "docs/role-context-protocol.md", "scripts/benchmark_flow_allocation.py",
              "scripts/capture_semantic_request.py", "scripts/semantic_model_study.py", relative(args.runtime_manifest), freshness["path"])
    binding["implementation_sha256"].update({name: file_sha(ROOT / name) for name in extras})
    binding["implementation_sha256"].update(source_paths)
    # Bind the complete research Python closure, including indirectly imported
    # frozen replay/packing/audit helpers. An added module also changes identity.
    binding["implementation_sha256"].update({relative(path): file_sha(path) for path in (ROOT / "scripts").glob("*.py")})
    binding.update(study=SCHEMA, conditions=CONDITIONS, runtime_manifest=runtime,
                   candidate=CANDIDATE, development_parent=parent, freshness_review=freshness,
                   reserved_holdout={"dataset_path": RESERVED_DATASET, "dataset_sha256": RESERVED_DATASET_SHA256,
                                     "revision": RESERVED_REVISION},
                   native_source_freeze={"commit": source_commit, "sha256": source_paths,
                                         "bound_blob_count": len(source_paths),
                                         "parent_bound_blob_count": len(parent["artifacts"]),
                                         "committed_at_unix": int(git("show", "-s", "--format=%ct", source_commit))})
    committed(binding["implementation_sha256"])
    return data, sources, inventory, binding


def freeze(args):
    require(not args.output.exists(), "Use a new native plan directory")
    data, _, inventory, binding = identity(args)
    args.output.mkdir(exist_ok=False)
    sync(args.output.parent)
    write(args.output / "source-inventory.json", inventory)
    plan = {"schema": SCHEMA, "version": 1, "classification": args.role, "frozen_at_utc": utc(),
            "frozen_from_commit": git("rev-parse", "HEAD"), "binding": binding,
            "queries": [{key: query[key] for key in ("id", "query", "kind")} for query in data["queries"]],
            "artifacts": {"source-inventory.json": file_sha(args.output / "source-inventory.json")},
            "quality_repetition": 1, "expected_requests": 252, "expected_warmups": 6,
            "timing": "Whole resident backend.search; original query, source/vector checks, fresh clauses, v1 and role guard included. Diagnostic copies/drains/serialization/fsync excluded.",
            "execution": "Commit exact plan and source inventory before one exclusive attempt; no retry/resume/overwrite.",
            "limits": "Repository source completeness and warm latency; no coding correctness, transport or billing claim."}
    write(args.output / "plan.json", plan)
    return plan


def verify_plan(args, binding):
    plan = read(args.output / "plan.json")
    require(plan.get("schema") == SCHEMA and plan.get("version") == 1 and plan["binding"] == binding,
            "Frozen native bindings changed")
    paths = {**binding["implementation_sha256"], **binding["development_parent"]["artifacts"],
             **(binding.get("definition_preflight") or {})}
    paths.update({relative(args.output / name): digest for name, digest in plan["artifacts"].items()})
    name = relative(args.output / "plan.json")
    paths[name] = file_sha(args.output / "plan.json")
    audit_path = args.output / "input-audit.json"
    audit = read(audit_path)
    require(audit.get("schema") == "role_native_input_audit_v1" and audit.get("status") == "passed"
            and audit.get("checks_passed") is True and audit.get("plan_sha256") == paths[name]
            and audit.get("source_inventory_sha256") == file_sha(args.output / "source-inventory.json")
            and audit.get("native_source_freeze") == binding["native_source_freeze"]
            and audit.get("source_sha256") == binding["implementation_sha256"]
            and type(audit.get("new_native_outcomes_computed")) is int and audit["new_native_outcomes_computed"] == 0
            and canonical(audit.get("source_only_calls")) == canonical({key: 0 for key in ("model", "index", "retrieval", "selector",
                "postprocessor", "rule_activation", "gold_coverage_scoring")}),
            "Independent source-only input barrier must precede native execution")
    paths[relative(audit_path)] = file_sha(audit_path)
    committed(paths)
    commit = git("log", "-1", "--format=%H", "--", name, relative(audit_path))
    require(bool(commit), "Commit the prospective native plan")
    committed(paths, commit)
    subprocess.check_call(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", commit, "HEAD"])
    for older, newer in ((binding["native_source_freeze"]["commit"], commit), (audit["execution_commit"], commit)):
        subprocess.check_call(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", older, newer])
    return plan, {"commit": commit, "bound_blob_count": len(paths),
                  "committed_at_unix": int(git("show", "-s", "--format=%ct", commit))}


class RecordingEmbedder(QwenEmbedder):
    query_calls = clause_query_calls = clause_query_texts = document_calls = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.query_calls = self.clause_query_calls = self.clause_query_texts = self.document_calls = 0
        self.document_api_records = []

    def embed_query(self, query, *, forward_observer=None):
        self.query_calls += 1
        value = super().embed_query(query, forward_observer=forward_observer)
        self.last_query = value.copy()
        return value

    def embed_queries(self, queries, *, cancel_check=None, forward_observer=None):
        self.clause_query_calls += 1
        self.clause_query_texts += len(queries)
        return super().embed_queries(queries, cancel_check=cancel_check, forward_observer=forward_observer)

    def embed_documents(self, texts, *, cancel_check=None, progress_callback=None, batch_callback=None, forward_observer=None):
        self.document_calls += 1
        record = {"texts": texts, "forward_events": [], "status": "started"}
        self.document_api_records.append(record)

        def observe(event):
            record["forward_events"].append(event)
            if forward_observer is not None:
                forward_observer(event)

        try:
            value = super().embed_documents(texts, cancel_check=cancel_check, progress_callback=progress_callback,
                batch_callback=batch_callback, forward_observer=observe)
            record["status"] = "completed"
            return value
        except BaseException as error:
            record.update(status="failed", error_type=type(error).__name__)
            raise


def hooks(condition):
    require(condition in CONDITIONS, "Unknown native role condition")
    return ((selection.ranked_context, selection.unchanged_context) if condition == "legacy" else
            (selection.definition_context, coalesce_context))


@contextmanager
def capture(condition, backend=None):
    """Callbacks retain references; reporting snapshots follow the search timer."""
    selector, processor = hooks(condition)
    state = {"backfill_candidates": [], "ranked_candidates": [], "reference_response": None,
             "proposal_response": None, "guard_calls": 0, "semantic_capture": None,
             "role_guard_receipt": None, "resident_reads": [], "external_forward_events": [],
             "semantic_calls": 0, "role_guard_calls": 0, "feature_calls": 0, "coverage_coalesce_calls": 0,
             "semantic_events": [], "reference_calls": 0, "semantic_postprocessor_calls": 0,
             "phase_timings": {"coverage_context_s": 0.0, "live_feature_provider_s": 0.0,
                               "prepared_feature_allocation_s": 0.0, "terminal_role_guard_s": 0.0}}
    actual_coalesce, actual_allocate = adapter_module.coalesce_context, semantic_module.semantic_final_context
    actual_factory = provider_module.make_live_feature_provider

    def timed_call(function, count_key, timing_key, *args, **kwargs):
        state[count_key] += 1
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            state["phase_timings"][timing_key] += time.perf_counter()-started

    def coalesced(*args, **kwargs):
        value = timed_call(actual_coalesce, "coverage_coalesce_calls", "coverage_context_s", *args, **kwargs)
        state["reference_response"] = value
        return value

    def allocated(*args, **kwargs):
        return timed_call(actual_allocate, "semantic_calls", "prepared_feature_allocation_s", *args, **kwargs)

    def provider_factory(*args, **kwargs):
        provider = actual_factory(*args, **kwargs)

        def provide(*args, **kwargs):
            return timed_call(provider, "feature_calls", "live_feature_provider_s", *args, **kwargs)

        return provide

    def selected(pairs, **kwargs):
        value = selector(pairs, **kwargs)
        state.update(ranked=pairs, selected=value[0], eligible=value[1], origins=value[2],
                     definition_keys=kwargs["definition_keys"], ambiguous_symbols=kwargs.get("ambiguous_symbols"))
        return value

    def record(event):
        state["semantic_events"].append(event)
        if "semantic_receipt" in event:
            require(state["semantic_capture"] is None, "Exactly one actual v1 proposal required")
            state["semantic_capture"] = event

    live = make_live_semantic_postprocessor(capture=record)

    def postprocessed(response, candidates, **kwargs):
        state["pre_postprocess_response"] = response

        def recorded(iterator, name):
            for row in iterator:
                state[name].append(row)
                yield row

        state["candidate_iterator"] = recorded(candidates, "backfill_candidates")
        state["ranked_iterator"] = recorded(kwargs.pop("ranked_candidates", ()), "ranked_candidates")
        kwargs["ranked_candidates"] = state["ranked_iterator"]
        if condition != CANDIDATE:
            return processor(response, state["candidate_iterator"], **kwargs)
        request = kwargs["request_context"]
        state["request_binding"] = {key: request.get(key) for key in
                                    ("query", "retrieval", "source_snapshot", "model_fingerprint")}
        index = request["index"]
        read_vectors = index.read_semantic_vectors

        def read_resident(rows, *, model_metadata, cancel_check):
            entry = {"requested_rows": rows, "model_metadata": model_metadata,
                     "index_path": str(getattr(index, "db_path", "")), "returned_vectors": None}
            state["resident_reads"].append(entry)
            value = read_vectors(rows, model_metadata=model_metadata, cancel_check=cancel_check)
            entry["returned_vectors"] = value
            return value

        state["semantic_postprocessor_calls"] += 1
        with patch.object(index, "read_semantic_vectors", read_resident):
            proposal = live(response, state["candidate_iterator"], **kwargs)
        event = state["semantic_capture"]
        require(event is not None, "Missing actual live semantic capture")
        require(state["feature_calls"] == event["feature_calls"]
                and state["coverage_coalesce_calls"] == event["coverage_coalesce_calls"], "Actual live call counters differ")
        state.update(reference_response=event["reference_response"], proposal_response=proposal)
        state["guard_calls"] += 1
        chosen, receipt = timed_call(role.apply_role_guard, "role_guard_calls", "terminal_role_guard_s", event["reference_response"], proposal,
            definition_keys=kwargs.get("definition_keys"), cancel_check=kwargs.get("cancel_check", lambda: None))
        state.update(role_guard_receipt=receipt, guard_chosen_response=chosen)
        return chosen

    state["postprocessor"] = postprocessed
    state["forward_observer"] = state["external_forward_events"].append
    with patch.object(backend_module, "select_context", selected), patch.object(backend_module, "postprocess_context", postprocessed), \
            patch.object(adapter_module, "coalesce_context", coalesced), \
            patch.object(semantic_module, "semantic_final_context", allocated), \
            patch.object(provider_module, "make_live_feature_provider", provider_factory):
        if backend is None:
            yield state
        else:
            with patch.object(backend, "_research_postprocessor", postprocessed if condition == CANDIDATE else None), \
                    patch.object(backend, "_research_forward_observer", state["forward_observer"]):
                yield state


def receipt(query, repetition, retrieval, arm, condition, response, captured, elapsed, vector):
    value = base.receipt(query, repetition, retrieval, arm, condition, response, captured, elapsed, vector)
    value["external_forward_events"] = deepcopy(captured["external_forward_events"])
    value["candidate_capture"] = None
    value["resident_reads"] = []
    if condition == CANDIDATE:
        event = captured["semantic_capture"]
        require(event is not None and captured["semantic_calls"] == captured["role_guard_calls"] == 1
                and captured["coverage_coalesce_calls"] == 1, "Candidate must execute coverage/v1/role exactly once")
        chosen = captured["proposal_response"] if captured["role_guard_receipt"]["accepted"] else captured["reference_response"]
        require(source_view(response) == source_view(chosen), "Role guard did not return one exact frozen operand")
        candidate = deepcopy({key: item for key, item in event.items() if key != "response"})
        candidate.update(v1_response=deepcopy(captured["proposal_response"]), response=deepcopy(response),
                         role_guard_receipt=deepcopy(captured["role_guard_receipt"]), request_binding=deepcopy(captured["request_binding"]),
                         phase_timings=deepcopy(captured["phase_timings"]),
                         calls={"coverage_coalesce": 1, "semantic": 1, "role_guard": 1,
                                "feature": event["feature_calls"], "current_v1": 1, "extra_retrieval": 0,
                                "document_inference": 0, "backfill_after_guard": 0})
        value["candidate_capture"] = candidate
        for entry in captured["resident_reads"]:
            require(entry["returned_vectors"] is not None, "Resident vector lookup did not finish")
            value["resident_reads"].append({**deepcopy({key: item for key, item in entry.items() if key != "returned_vectors"}),
                "returned_vectors": {key: transport.vector_record(vector) for key, vector in entry["returned_vectors"].items()}})
    else:
        require(captured["semantic_calls"] == captured["role_guard_calls"] == 0
                and not captured["resident_reads"], "Control executed candidate feature work")
    receipts = capture_helpers._provider_receipts(captured["semantic_events"])
    value["forward_counts"] = capture_helpers._counts(value["external_forward_events"], receipts)
    capture_helpers._validate_forward_bindings(response, value["external_forward_events"], receipts, value["forward_counts"])
    require(value["forward_counts"]["receipt_coverage_complete"]
            and value["forward_counts"]["retrieval_query"]["completed_forward_calls"] == 1
            and value["forward_counts"]["documents"]["reported_model_forward_calls"] == 0
            and all(value["forward_counts"][name]["failed_forward_calls"] == 0
                    for name in ("documents", "retrieval_query", "extra_queries")), "Incomplete actual native forward evidence")
    return value


def summarize(rows):
    result = base.summarize(rows)
    for mode, item in result["per_retrieval"].items():
        item.pop("active_composite_overhead")
        for metric in item["arms"].values():
            metric.pop("delivered_composites")
            metric.pop("active_composite_calls")
        candidates = [row for row in rows if row["retrieval"] == mode and row["arm"] == "candidate"]
        controls = {arm: {(row["query_id"], row["repetition"]): row for row in rows
                         if row["retrieval"] == mode and row["arm"] == arm} for arm in ARMS[:-1]}
        item["role_activity"] = {"accepted_calls": sum(row["candidate_capture"]["role_guard_receipt"]["accepted"] for row in candidates),
            "changed_calls": sum(row["compact_sha256"] != controls["coverage_control"][(row["query_id"], row["repetition"])]["compact_sha256"] for row in candidates),
            "clause_api_calls": sum(row["clause_query_calls"] for row in candidates),
            "clause_encoded_texts": sum(row["clause_query_texts"] for row in candidates)}
        item["median_paired_wall_delta_to_controls_s"] = {arm: statistics.median(
            row["elapsed_backend_s"] - controls[arm][(row["query_id"], row["repetition"])]["elapsed_backend_s"]
            for row in candidates) for arm in ARMS[:-1]}
    return result


def make_backend(args, data, db, embedder):
    return backend_module.SearchBackend(args.corpus, data["source_root"], db, embedder=embedder,
                                        source_exclusions=data.get("source_exclusions", ()))


def partial_capture(state):
    value = {key: state.get(key) for key in ("semantic_events", "external_forward_events", "request_binding",
        "semantic_capture", "role_guard_receipt", "semantic_calls", "role_guard_calls", "feature_calls",
        "coverage_coalesce_calls", "reference_response", "proposal_response", "pre_postprocess_response",
        "semantic_postprocessor_calls", "phase_timings")}
    value["resident_reads"] = []
    for entry in state.get("resident_reads", []):
        value["resident_reads"].append({**{key: item for key, item in entry.items() if key != "returned_vectors"},
            "returned_vectors": None if entry["returned_vectors"] is None else
                {key: transport.vector_record(vector) for key, vector in entry["returned_vectors"].items()}})
    return capture_helpers._safe_snapshot(value)


def measure(backend, embedder, query, repetition, arm, retrieval, *, on_failure=None):
    condition = CANDIDATE if arm == "candidate" else arm
    before = (embedder.query_calls, embedder.clause_query_calls, embedder.clause_query_texts, embedder.document_calls)
    state, started_at, started, ended, ended_at = {}, None, None, None, None
    try:
        with capture(condition, backend) as state:
            started_at = utc()
            started = time.perf_counter()
            try:
                response = backend.search(query["query"], retrieval=retrieval, **SETTINGS)
            finally:
                ended = time.perf_counter()
                ended_at = utc()
        delta = tuple(after - old for after, old in zip((embedder.query_calls, embedder.clause_query_calls,
                                                        embedder.clause_query_texts, embedder.document_calls), before))
        require(delta[0] == 1 and delta[3] == 0 and (arm == "candidate" or delta[1:3] == (0, 0)),
                "Measured singleton retrieval query/document/clause API counts differ")
        value = receipt(query, repetition, retrieval, arm, condition, response, state, ended-started, embedder.last_query)
        providers = [] if value["candidate_capture"] is None else value["candidate_capture"]["provider_receipts"]
        require(delta[1] == sum(record.get("extra_query_api_calls", 0) for record in providers)
                and delta[2] == sum(record.get("extra_query_encoded_texts", 0) for record in providers),
                "Actual clause API/text counts differ from live provider receipts")
        value.update(query_embedding_calls=delta[0], clause_query_calls=delta[1], clause_query_texts=delta[2],
                     document_embedding_calls=delta[3], request_clock={"started_at_utc": started_at, "ended_at_utc": ended_at,
                        "started_perf_counter": started, "ended_perf_counter": ended})
        return value
    except BaseException as error:
        if on_failure is not None:
            try:
                on_failure({"query_id": query["id"], "query": query["query"], "repetition": repetition,
                    "arm": arm, "retrieval": retrieval, "error_type": type(error).__name__,
                    "attempted_api_calls": {key: after-old for key, after, old in zip(
                        ("query", "clauses", "clause_texts", "documents"),
                        (embedder.query_calls, embedder.clause_query_calls, embedder.clause_query_texts, embedder.document_calls), before)},
                    "request_clock": {"started_at_utc": started_at, "ended_at_utc": ended_at,
                        "started_perf_counter": started, "ended_perf_counter": ended}, "partial_capture": partial_capture(state)})
            except BaseException as recorder_error:
                raise error from recorder_error
        raise


def run(args):
    attempt = args.output / "attempt"
    require(not attempt.exists(), "Attempt exists; no overwrite, resume or retry")
    data, sources, inventory, binding = identity(args)
    plan, chain = verify_plan(args, binding)
    started_at = utc()
    require(chain["committed_at_unix"] <= datetime.fromisoformat(started_at).timestamp(), "Freeze follows execution")
    attempt.mkdir(exist_ok=False)
    rows, phase, attempted, backend, embedder, lease = [], "claim", 0, None, None, None
    ledger = warm_ledger = None
    try:
        sync(attempt.parent)
        ledger = transport.Ledger(attempt / "searches.jsonl.gz", compressed=True)
        warm_ledger = transport.Ledger(attempt / "warmups.jsonl.gz", compressed=True)
        write(attempt / "run-started.json", {"schema": SCHEMA, "started_at_utc": started_at,
            "execution_commit": git("rev-parse", "HEAD"), "prospective_freeze": chain,
            "native_source_freeze": binding["native_source_freeze"],
            "plan_sha256": file_sha(args.output / "plan.json"), "resume_allowed": False,
            "expected_measured_calls": 252, "expected_warmups": 6, "candidate": CANDIDATE})
        phase = "gpu_lease"
        with transport.gpu_lease(ROOT) as lease:
            write(attempt / "gpu-lease-acquired.json", deepcopy(lease))
            try:
                phase = "setup"
                rank_bytes = (args.tokenizer_cache / base.replay.CACHE_KEY).read_bytes()
                require(sha(rank_bytes) == base.replay.CACHE_SHA256, "Frozen tokenizer bytes changed")
                import tiktoken.load

                def local_rank_bytes(url, expected_hash=None):
                    require(url == base.replay.CACHE_URL and expected_hash == base.replay.CACHE_SHA256, "Unexpected tokenizer resource")
                    return rank_bytes

                with patch.object(tiktoken.load, "read_file_cached", local_rank_bytes):
                    context.encoding()
                embedder = RecordingEmbedder(args.model, progress=True)
                require(embedder.metadata == binding["runtime_manifest"]["model_binding"]["metadata"], "Live model metadata differs")
                # A dedicated disposable database prevents inherited index/vector-cache work.
                db = ROOT / ".cache/indexes" / ("role-native-" + sha(relative(attempt).encode())[:20] + ".sqlite3")
                require(not any(Path(str(db) + suffix).exists() for suffix in ("", "-wal", "-shm")), "Native index must be newly owned")
                setup_started = time.perf_counter()
                backend = make_backend(args, data, db, embedder)
                setup_forward_events = []
                with patch.object(backend, "_research_forward_observer", setup_forward_events.append):
                    backend.refresh()
                    status = backend.status()
                require(backend._definition_keys == {key: tuple(value) for key, value in inventory["definition_keys"].items()},
                        "Live canonical definitions differ from source-only freeze")
                graph = [{"caller": list(caller), "targets": [list(target) for target in targets]}
                         for caller, targets in sorted(backend._references.edges.items())]
                require(graph == inventory["graph_edges"], "Live graph differs from frozen source graph")
                warm_query = {"id": "__generic_warmup__", "query": WARM_QUERY, "kind": "absent"}
                for arm in ARMS:
                    for retrieval in RETRIEVALS:
                        warm_ledger.append(measure(backend, embedder, warm_query, 0, arm, retrieval,
                            on_failure=lambda value: write(attempt / "request-failure.json", value)))
                setup = {"index_model_and_explicit_warmup_s": time.perf_counter()-setup_started,
                         "status": status, "warmup_calls": len(warm_ledger.rows),
                         "query_api_calls": embedder.query_calls, "clause_api_calls": embedder.clause_query_calls,
                         "clause_encoded_texts": embedder.clause_query_texts, "document_api_calls": embedder.document_calls,
                         "external_forward_events": setup_forward_events, "db_path": str(db),
                         "document_calls": deepcopy(embedder.document_api_records), "machine_state": machine_state()}
                require(len(warm_ledger.rows) == embedder.query_calls == 6, "Exactly six generic original-query warmups required")
                write(attempt / "setup.json", setup)
                phase = "measured_calls"
                ranks, vectors, payloads, references = {}, {}, {}, {}
                for repetition, query, arm, retrieval in schedule(data["queries"]):
                    attempted += 1
                    outcome = measure(backend, embedder, query, repetition, arm, retrieval,
                        on_failure=lambda value: write(attempt / "request-failure.json", value))
                    ranked = outcome["stages"]["ranked"]
                    for stage in ("ranked", "selected", "eligible"):
                        for pair in outcome["stages"][stage]:
                            base.checked_snippet(pair["snippet"], sources, inventory["inventory"], inventory["definition_keys"], ranked)
                    for name in ("response", "pre_postprocess_response", "reference_response", "proposal_response"):
                        if outcome.get(name) is not None:
                            base.checked_response(outcome[name], sources, inventory["inventory"], inventory["definition_keys"], ranked)
                    base.checked_candidate_rows(outcome, sources, inventory)
                    key = (query["id"], retrieval)
                    require(ranks.setdefault(key, sha(canonical(ranked))) == sha(canonical(ranked)), "Raw ranks/scalars changed")
                    require(vectors.setdefault(query["id"], outcome["query_vector_sha256"]) == outcome["query_vector_sha256"], "Original query vector changed")
                    pkey = (*key, arm)
                    require(payloads.setdefault(pkey, outcome["compact_sha256"]) == outcome["compact_sha256"], "Compact payload changed across repetitions")
                    if arm == "candidate":
                        reference = outcome["candidate_capture"]["reference_response"]
                        fingerprint = sha(canonical(source_view(reference)))
                        require(references.setdefault(key, fingerprint) == fingerprint, "Actual coverage reference changed")
                    ledger.append(outcome)
                    rows.append(outcome)
                    print(f"r{repetition} {query['id']} {retrieval} {arm} {outcome['elapsed_backend_s']:.3f}s", flush=True)
                require(len(rows) == attempted == 252, "The fixed native matrix requires252 actual searches")
                controls = {(row["query_id"], row["retrieval"]): row for row in rows if row["arm"] == "coverage_control"}
                require(all(sha(canonical(source_view(controls[key]["response"]))) == digest for key, digest in references.items()),
                        "Candidate actual reference differs from separately measured coverage control")
            finally:
                phase_before_close = phase
                phase = "close_backend"
                primary = sys.exception()
                try:
                    if backend is not None:
                        backend.close()
                    elif embedder is not None:
                        transport.close_preserving_error(embedder)
                    write(attempt / "model-closed.json", {"closed_at": utc(), "phase": phase_before_close})
                    phase = phase_before_close
                except BaseException as close_error:
                    if primary is not None:
                        raise primary from close_error
                    raise
        write(attempt / "gpu-lease-released.json", deepcopy(lease))
        phase = "final_bindings"
        require(identity(args)[3] == binding, "Frozen bindings changed during native execution")
        verify_plan(args, binding)
        ledger.close()
        warm_ledger.close()
        metrics = summarize(rows)
        summary = {"schema": SCHEMA, "complete": True, "classification": args.role, "candidate": CANDIDATE,
            "requests": len(rows), "unique_queries": len(data["queries"]), "quality_repetition": 1,
            "setup": setup, "gate": GATE, "metrics": metrics, "plan_sha256": file_sha(args.output / "plan.json"),
            "post_run_machine_state": machine_state(),
            "ranked_repeat_changes": 0, "compact_repeat_changes": 0, "query_vector_repeat_changes": 0,
            "measured_query_embeddings": sum(row["query_embedding_calls"] for row in rows),
            "measured_clause_query_calls": sum(row["clause_query_calls"] for row in rows),
            "measured_clause_encoded_texts": sum(row["clause_query_texts"] for row in rows),
            "measured_document_embedding_calls": sum(row["document_embedding_calls"] for row in rows),
            "artifact_sha256": {path.name: file_sha(path) for path in attempt.iterdir() if path.is_file()}}
        write(attempt / "summary.json", summary)
        return summary
    except BaseException as error:
        try:
            if lease is not None and lease.get("released_at") is not None and not (attempt / "gpu-lease-released.json").exists():
                write(attempt / "gpu-lease-released.json", deepcopy(lease))
            if ledger is not None:
                ledger.close()
            if warm_ledger is not None:
                warm_ledger.close()
            if embedder is not None and not (attempt / "setup.json").exists():
                write(attempt / "setup-partial.json", {"document_calls": capture_helpers._safe_snapshot(embedder.document_api_records),
                    "query_api_calls": embedder.query_calls, "clause_api_calls": embedder.clause_query_calls,
                    "clause_encoded_texts": embedder.clause_query_texts, "document_api_calls": embedder.document_calls})
            write(attempt / "failure.json", {"schema": SCHEMA, "phase": phase, "completed_requests": len(rows),
                "attempted_requests": attempted, "error_type": type(error).__name__, "error": str(error),
                "resume_allowed": False, "artifacts": {path.name: file_sha(path) for path in attempt.iterdir() if path.is_file()}})
        except BaseException as recorder_error:
            raise error from recorder_error
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "corpus", "model", "output", "source-review", "freshness-review", "development-study", "runtime-manifest"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--candidate", choices=(CANDIDATE,), default=CANDIDATE)
    parser.add_argument("--role", choices=("repository_holdout",), default="repository_holdout")
    parser.add_argument("--tokenizer-cache", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--freeze", action="store_true")
    action.add_argument("--run", action="store_true")
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    print(json.dumps(freeze(args) if args.freeze else run(args), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
