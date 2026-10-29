#!/usr/bin/env python3
"""Freeze and measure one qualified shared-flow candidate against two controls.

The committed development qualification is checked before fresh data, source or
checkpoint metadata is read. This profile has its own measured run and capture;
it does not reinterpret another condition's name or preservation assumptions.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import time
from unittest.mock import patch

import benchmark_windows as base
import replay_flow_allocation as development
from semantic_search import backend as backend_module, context, flow_allocation as flow, selection
from semantic_search import source_allocation as source
from semantic_search.context_backfill import coalesce_context

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = dict(base.SETTINGS)
CANDIDATE = "shared_flow"
ARMS, RETRIEVALS, REPETITIONS = base.ARMS, base.RETRIEVALS, 3
CONDITIONS = {
    "legacy": {"selector": "ranked_context", "postprocessor": "unchanged_context"},
    "coverage_control": {"selector": "definition_context", "postprocessor": "coalesce_context"},
    CANDIDATE: {"selector": "shared_flow_context", "postprocessor": "guard_source_context"},
}
GATE = dict(base.GATE)
WARM_QUERY = base.WARM_QUERY
require, sha, file_sha, canonical, read, write = base.require, base.sha, base.file_sha, base.canonical, base.read, base.write
schedule, pair_rows, evidence_ranks = base.schedule, base.pair_rows, base.evidence_ranks
NATIVE_SOURCES = ("scripts/benchmark_flow_allocation.py", "tests/test_flow_allocation_benchmark.py",
    "docs/flow-allocation-native-protocol.md", "scripts/audit_flow_native.py", "tests/test_flow_native_audit.py")


def relative(path):
    return base.relative(path, ROOT)


def committed(paths):
    for name, expected in paths.items():
        require(file_sha(ROOT / name) == sha(subprocess.check_output(
            ["git", "-C", str(ROOT), "show", "HEAD:" + name])) == expected,
            "Uncommitted or changed frozen file: " + name)
    require(not subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--", *paths],
                                      text=True).strip(), "Bound files have staged or unstaged changes")


def parent_binding(args):
    """Read observed parent files only; reject before any fresh/checkpoint read."""
    require(args.candidate == CANDIDATE and args.development_study is not None,
            "One qualified committed shared-flow development parent is required")
    folder = args.development_study
    summary = read(folder / "summary.json")
    q = summary.get("qualification", {}).get(CANDIDATE, {})
    require(summary.get("study") == "source_flow" and q.get("qualified") is True,
            "Shared-flow candidate did not qualify in development")
    require(set(q.get("against_controls", {})) == {"legacy", "coverage_control"}
            and all(set(loss) == {"lost_groups", "lost_complete_rows", "lost_single_file_first_hits"}
                    and all(type(value) is int and value == 0 for value in loss.values())
                    for loss in q["against_controls"].values()), "Development candidate loses evidence against a control")
    gained = q.get("additional_distinct_complete_queries_vs_coverage_control", [])
    require(isinstance(gained, list) and len(gained) == len(set(gained)) >= 1
            and q.get("gained_additional_distinct_complete_queries") == len(gained),
            "Development candidate needs a new distinct complete query beyond coverage")
    require(summary.get("model_calls") == summary.get("retrieval_calls") == summary.get("network_calls") == 0
            and summary.get("baseline_exact_rows") == 365 and summary.get("prior_coverage_exact_rows") == 337
            and summary.get("exact_parent_control_rows") == 730 and summary.get("referenced_negative_rows") == 1460,
            "All original development controls and historical negative references are required")
    plan, start, gate, audit = (read(folder / name) for name in
        ("frozen-plan.json", "run-start.json", "control-gate.json", "independent-audit.json"))
    require(plan.get("study") == "source_flow" and plan["conditions"] == development.CONDITIONS
            and plan["settings"] == SETTINGS and summary["plan_sha256"] == file_sha(folder / "frozen-plan.json")
            and summary["replay_sha256"] == file_sha(folder / "replay.jsonl.gz"),
            "Qualified development artifacts/configuration differ")
    require(gate.get("exact_legacy_rows") == 365 and gate.get("exact_prior_coverage_rows") == 337
            and gate.get("exact_parent_control_rows") == 730 and gate.get("new_variant_rows_before_gate") == 0,
            "Development barriers must precede candidate outcomes")
    require(audit.get("status") == "passed" and audit.get("checks_passed") is True
            and audit.get("study") == "source_flow" and audit.get("mode") == "development"
            and audit.get("candidate_planner_or_producer_modules_imported") is False
            and audit.get("exact_legacy_controls") == 365 and audit.get("exact_prior_coverage_controls") == 337
            and audit.get("verified_outcome_rows") == 365 and audit.get("referenced_control_rows") == 730
            and audit.get("referenced_negative_rows") == 1460
            and audit.get("independently_recomputed_summary") == summary
            and all(type(audit.get("calls", {}).get(name)) is int and audit["calls"][name] == 0
                    for name in ("model", "retrieval", "index", "network")),
            "Qualified parent requires the actual independent shared-flow audit schema")
    require(audit["producer_execution"] == start["execution_commit"]
            and audit["producer_freeze"] == start["prospective_freeze"], "Parent freeze/execution identities disagree")
    for older, newer in ((audit["producer_freeze"]["commit"], start["execution_commit"]),
                         (start["execution_commit"], "HEAD")):
        subprocess.check_call(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", older, newer])
    artifacts = {relative(folder / name): file_sha(folder / name) for name in (
        *development.FROZEN_FILES, "run-start.json", "control-gate.json", "replay.jsonl.gz", "summary.json",
        "findings.md", "independent-audit.json")}
    for name, digest in audit["artifact_sha256"].items():
        require(file_sha(folder / name) == digest, "Audited parent artifact changed: " + name)
        artifacts[relative(folder / name)] = digest
    artifacts.update(plan["producer_sha256"])
    artifacts.update(audit["auditor_sha256"])
    require(all(file_sha(ROOT / name) == digest for name, digest in artifacts.items()),
            "Qualified development producer/audit bindings changed")
    committed(artifacts)
    return {"study": "source_flow", "artifacts": artifacts, "qualification": q,
            "execution_commit": start["execution_commit"], "freeze": start["prospective_freeze"],
            "audit_schema": "candidate_planner_or_producer_modules_imported=false",
            "condition_description": plan["selection"][CANDIDATE],
            "exposure": "All parent cases are observed development evidence; no fresh outcome used to select the candidate."}


def identity(args):
    """The parent gate intentionally precedes even fresh query/checkpoint metadata."""
    require(args.repetitions == REPETITIONS and args.role == "repository_holdout",
            "Exactly three repetitions of one repository holdout are fixed")
    parent = parent_binding(args)
    require(all((ROOT / name).is_file() for name in NATIVE_SOURCES),
            "Complete native producer, tests, protocol and independent auditor before fresh preparation")
    committed({name: file_sha(ROOT / name) for name in NATIVE_SOURCES})
    return _identity_after_parent(args, parent)


def hooks(condition):
    require(condition in CONDITIONS, "Unknown shared-flow native condition")
    if condition == CANDIDATE:
        return flow.shared_flow_context, source.guard_source_context
    if condition == "coverage_control":
        return selection.definition_context, coalesce_context
    return selection.ranked_context, selection.unchanged_context


@contextmanager
def capture(condition):
    """Retain live references in the timed call; copy and drain only afterward."""
    selector, postprocessor = hooks(condition)
    state = {"backfill_candidates": [], "ranked_candidates": [], "reference_response": None,
             "proposal_response": None, "flow_receipt": None, "base_receipt": None,
             "actual_guard_plan": None, "source_guard_receipt": None,
             "flow_planner_calls": 0, "base_planner_calls": 0, "guard_calls": 0, "reference_calls": 0}
    original_flow, original_base, original_guard = flow.flow_allocation_plan, source.source_allocation_plan, source.guard_source_plan

    def planned(*args, **kwargs):
        value = original_flow(*args, **kwargs)
        state["flow_planner_calls"] += 1
        state["flow_receipt"] = value[3]
        return value

    def based(*args, **kwargs):
        value = original_base(*args, **kwargs)
        state["base_planner_calls"] += 1
        state["base_receipt"] = value[3]
        return value

    def guarded(reference, proposal, actual_plan):
        value, receipt = original_guard(reference, proposal, actual_plan)
        state.update(reference_response=reference, proposal_response=proposal,
                     actual_guard_plan=actual_plan, source_guard_receipt=receipt, guard_chosen_response=value)
        state["guard_calls"] += 1
        return value, receipt

    def selected(pairs, **kwargs):
        value = selector(pairs, **kwargs)
        state.update(ranked=pairs, selected=value[0], eligible=value[1], origins=value[2],
                     definition_keys=kwargs["definition_keys"], ambiguous_symbols=kwargs.get("ambiguous_symbols"))
        return value

    def postprocessed(response, candidates, **kwargs):
        state["pre_postprocess_response"] = response
        state["selection_plan"] = kwargs.get("selection_plan")
        def recorded(iterator, name):
            for row in iterator:
                state[name].append(row)
                yield row
        state["candidate_iterator"] = recorded(candidates, "backfill_candidates")
        state["ranked_iterator"] = recorded(kwargs.pop("ranked_candidates", ()), "ranked_candidates")
        kwargs["ranked_candidates"] = state["ranked_iterator"]
        factory = kwargs.get("reference_factory")
        if factory is not None:
            def reference():
                value = factory()
                state["reference_calls"] += 1
                state["reference_response"] = value
                return value
            kwargs["reference_factory"] = reference
        return postprocessor(response, state["candidate_iterator"], **kwargs)

    with patch.object(backend_module, "select_context", selected), \
            patch.object(backend_module, "postprocess_context", postprocessed), \
            patch.object(flow, "flow_allocation_plan", planned), \
            patch.object(source, "source_allocation_plan", based), \
            patch.object(source, "guard_source_plan", guarded):
        yield state


def source_view(response):
    """Exclude operational timings/index stats mutated after the actual guard."""
    return {name: deepcopy(response[name]) for name in (
        "query", "scope", "match_status", "results", "total_source_chars", "budget_exhausted", "token_usage",
        "_selection_backfill")
            if name in response}


def receipt(query, repetition, retrieval, arm, condition, response, captured, elapsed, vector):
    outcome = base.receipt(query, repetition, retrieval, arm, condition, response, captured, elapsed, vector)
    for name in ("flow_receipt", "base_receipt", "actual_guard_plan", "source_guard_receipt", "selection_plan"):
        outcome[name] = deepcopy(captured.get(name))
    for name in ("flow_planner_calls", "base_planner_calls", "reference_calls"):
        outcome[name] = captured[name]
    if condition == CANDIDATE:
        require(all(outcome[name] == 1 for name in ("flow_planner_calls", "base_planner_calls", "guard_calls", "reference_calls")),
                "Candidate must execute one actual flow planner, base helper, guard and lazy reference")
        require(outcome["flow_receipt"]["base_plan"] == outcome["base_receipt"]
                and outcome["flow_receipt"]["guard_plan"] == outcome["selection_plan"] == outcome["actual_guard_plan"],
                "Actual flow/base/guard plan identities differ")
        plan, reference, proposal = outcome["actual_guard_plan"], outcome["reference_response"], outcome["proposal_response"]
        protected = source.protected_source_rows(reference, plan)
        preserved = source._protected_rows_preserved(protected, proposal)
        accepted = bool(plan["accepted"] and preserved)
        guard = outcome["source_guard_receipt"]
        require(guard["accepted"] is accepted and guard["source_preserved"] is preserved
                and guard["protected_ids"] == [row["id"] for row in protected]
                and guard["fallback_reason"] == (None if accepted else "inactive_plan" if not plan["accepted"]
                                                  else "protected_source_not_preserved"), "Actual source guard decision differs")
        chosen = proposal if accepted else reference
        require(source_view(response) == source_view(chosen)
                and source_view(captured["guard_chosen_response"]) == source_view(chosen),
                "Guard must choose exact source/provenance context; operational timings are excluded")
        base.checked_preservation(outcome["pre_postprocess_response"], proposal)
    else:
        require(all(outcome[name] == 0 for name in ("flow_planner_calls", "base_planner_calls", "guard_calls", "reference_calls")),
                "Control unexpectedly executed candidate planning or lazy reference")
        if condition == "coverage_control":
            base.checked_preservation(outcome["pre_postprocess_response"], response)
    return outcome


replay, shared = base.replay, base.shared
_source_files, extract_snippets, decode_source = base._source_files, base.extract_snippets, base.decode_source
QwenEmbedder = base.QwenEmbedder
checkpoint_binding, definition_preflight = base.checkpoint_binding, base.definition_preflight


def _identity_after_parent(args, parent):
    """Source-only freeze preparation, entered only after qualified-parent checks."""
    data = read(args.dataset)
    require(len({query["id"] for query in data["queries"]}) == len(data["queries"]), "Duplicate query IDs")
    for query in data["queries"]:
        require(query.get("kind") in ("present", "absent") and isinstance(query.get("query"), str)
                and query["query"].strip(), "Malformed query")
        replay.groups(query)  # Reject positive evidence accidentally attached to an absent query.
    revision = subprocess.check_output(["git", "-C", str(args.corpus), "rev-parse", "HEAD"], text=True).strip()
    require(revision == data["revision"] and not subprocess.check_output(
        ["git", "-C", str(args.corpus), "status", "--porcelain"], text=True).strip(),
        "A clean corpus at the dataset revision is required")
    paths = _source_files(args.corpus, args.corpus / data["source_root"], data.get("source_exclusions", ()))
    sources = {path.relative_to(args.corpus).as_posix(): decode_source(str(path), path.read_bytes()) for path in paths}
    definition_keys = {}
    actual = extract_snippets(args.corpus, data["source_root"], 160, 40, sources=sources,
                              source_exclusions=data.get("source_exclusions", ()), definition_keys=definition_keys)
    inventory = [asdict(item) for item in actual]
    require(len({item["id"] for item in inventory}) == len(inventory) == len(definition_keys),
            "Extraction inventory/definition identity differs")
    binding = {"dataset_sha256": file_sha(args.dataset), "revision": revision, "classification": args.role,
        "source_sha256": {path.relative_to(args.corpus).as_posix(): file_sha(path) for path in paths},
        "inventory_sha256": sha(json.dumps(inventory, sort_keys=True).encode()),
        "implementation_sha256": {path.relative_to(ROOT).as_posix(): file_sha(path)
                                  for path in sorted((ROOT / "src/semantic_search").glob("*.py"))},
        "embedding": QwenEmbedder(args.model, progress=False).metadata, "settings": SETTINGS,
        "repetitions": args.repetitions,
        "runtime": {name: importlib.metadata.version(name) for name in
                    ("tiktoken", "sqlite-vec", "tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript")},
        "machine": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()}}
    source_files = {name: {"bytes": len((args.corpus / name).read_bytes()), "sha256": digest}
                    for name, digest in binding["source_sha256"].items()}
    if "source_files" in data:
        require(source_files == data["source_files"]
                and sha(canonical(source_files)) == data["source_inventory_sha256"],
                "Complete source membership/bytes differ from the authored inventory")
    for query in data["queries"]:
        for alternatives in replay.groups(query):
            for target in alternatives:
                low, high = target["evidence_lines"]
                require(type(low) is type(high) is int
                        and 1 <= low <= high <= len(shared.physical_lines(sources[target["path"]])),
                        "Evidence interval must be complete inclusive physical lines")
                literal = "".join(shared.physical_lines(sources[target["path"]])[low - 1:high])
                require(sha(literal.encode()) == target["evidence_sha256"]
                        and binding["source_sha256"][target["path"]] == target["source_file_sha256"]
                        and any(item["path"] == target["path"] and item["symbol"] == target["symbol"]
                                and item["start_line"] <= low <= high <= item["end_line"] for item in inventory),
                        "Evidence source hashes or symbol-qualified extraction eligibility differ")
    review_hash = None
    if args.source_review:
        review = read(args.source_review)
        require(review.get("status") == "passed_source_review_before_holdout_retrieval"
                and review.get("dataset", {}).get("sha256") == binding["dataset_sha256"]
                and review.get("corpus", {}).get("commit") == revision
                and review.get("corpus", {}).get("source_inventory_sha256") == sha(canonical(source_files))
                and review.get("protocol", {}).get("sha256") == file_sha(ROOT / data["source_review_protocol"])
                and all(review.get("calls", {}).get(name) == 0 for name in
                        ("model", "index", "retrieval", "selector", "postprocessor", "rule_activation", "gold_coverage_scoring")),
                "Independent source-review receipt does not bind this unobserved dataset/source")
        review_hash = file_sha(args.source_review)
    else:
        require(args.role != "repository_holdout", "Repository holdout requires an independent source-review receipt")
    with patch.dict(os.environ, {"TIKTOKEN_CACHE_DIR": str(args.tokenizer_cache)}):
        tokenizer, _ = replay.runtime(ROOT)
    binding.update(retrieval=list(RETRIEVALS), candidate=args.candidate,
                   arms={"legacy": "legacy", "coverage_control": "coverage_control", "candidate": args.candidate},
                   dataset_path=relative(args.dataset),
                   source_review_path=relative(args.source_review) if args.source_review else None,
                   source_review_sha256=review_hash, source_files=source_files,
                   definition_keys_sha256=sha(canonical(definition_keys)),
                   model_files_sha256=checkpoint_binding(args.model),
                   tokenizer=tokenizer, development_parent=parent, gate=GATE,
                   definition_preflight=definition_preflight(args, data, sources, inventory, definition_keys, source_files),
                   exposure={"role": args.role, "labels_and_source_only_before_fresh_outcomes": args.role == "repository_holdout",
                             "freshness_limit": "Repository evaluation independence within this project; no model-pretraining isolation claim."})
    extras = [*NATIVE_SOURCES, "src/semantic_search/flow_allocation.py", "tests/test_flow_allocation.py",
              "tests/test_flow_allocation_backend.py", "scripts/benchmark_windows.py", "tests/test_window_benchmark.py", "scripts/replay_windows.py",
              "scripts/benchmark_multifile.py", "scripts/replay_selection.py", "scripts/diagnose_multifile.py",
              "scripts/report_language_quality.py", "tests/test_window_assembly.py", "tests/test_window_replay.py",
              "docs/window-native-protocol.md", "docs/window-development-protocol.md", "pyproject.toml",
              "requirements-mac.lock", binding["dataset_path"]]
    if args.source_review:
        extras.append(binding["source_review_path"])
    if data.get("source_review_protocol"):
        extras.append(data["source_review_protocol"])
    for name in extras:
        binding["implementation_sha256"][name] = file_sha(ROOT / name)
    require(len(data["queries"]) == 14
            and sum(query["kind"] == "absent" for query in data["queries"]) == 2
            and sum(shared.is_multifile(replay.groups(query)) for query in data["queries"]) == 8
            and sum(query["kind"] == "present" and not shared.is_multifile(replay.groups(query))
                    for query in data["queries"]) == 4, "Freeze the14-query8-multi/4-single/2-absent source-reviewed sample")
    from semantic_search.relations import ReferenceGraph
    graph = ReferenceGraph(sources, actual)
    graph_edges = [{"caller": list(caller), "targets": [list(target) for target in targets]}
                   for caller, targets in sorted(graph.edges.items())]
    binding.update(study="source_flow_native", conditions=CONDITIONS,
                   graph_sha256=sha(canonical(graph_edges)), expected_requests=252,
                   expected_warmups=6)
    return data, sources, {"inventory": inventory, "definition_keys": definition_keys,
                           "graph_edges": graph_edges}, binding


def freeze(args):
    require(not args.output.exists(), "Use a new native freeze directory")
    data, _, inventory, binding = identity(args)
    args.output.mkdir(parents=True)
    write(args.output / "source-inventory.json", inventory)
    plan = {"version": 1, "study": "source_flow_native", "classification": args.role,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "frozen_from_commit": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "binding": binding, "quality_repetition": 1,
        "candidate_reason": "One shared-flow rule independently qualified with no individual evidence loss versus either control and a new distinct complete behavior versus coverage before any fresh data was read.",
        "queries": [{key: query[key] for key in ("id", "query", "kind")} for query in data["queries"]],
        "artifacts": {"source-inventory.json": file_sha(args.output / "source-inventory.json")},
        "schedule": "For each query/repetition rotate legacy/dense,coverage_control/dense,candidate/dense,legacy/hybrid,coverage_control/hybrid,candidate/hybrid by(query_index+repetition-1)%6;14queries,3repetitions,252actualcalls.",
        "timing": "Time only actual resident backend.search including1queryembedding and real flow/base/reference packing/guard/source-plan copy. Six explicit arm/mode warmups and setup excluded. Capture retains live references; all diagnostic deepcopy, iterator drains and reporting occur after elapsed time.",
        "guard_comparison": "Backend updates operational timings/indexstats after its guard. Compare exact compact/results/source-provenance views, never demand equal operational timing dictionaries.",
        "labels": "Gold, query IDs, kinds and intervals enter reporting only, after backend.search returns.",
        "execution": "Commit producer/tests/protocol/independent native auditor, qualified parent, source inventory and this plan unchanged. Preserve strict freeze/execution ancestry. One attempt; retain partial ledger and failures; no overwrite, resume or retry.",
        "limits": "Small independent repository sample; source completeness and resident timings do not establish coding correctness, transport latency or provider billing/token savings."}
    write(args.output / "plan.json", plan)
    return plan


def verify_plan(args, binding):
    plan = read(args.output / "plan.json")
    require(plan.get("study") == "source_flow_native" and plan.get("version") == 1 and plan["binding"] == binding,
            "Native frozen source, parent, labels, model or configuration changed")
    paths = {**binding["implementation_sha256"], **binding["development_parent"]["artifacts"],
             **(binding.get("definition_preflight") or {})}
    paths.update({relative(args.output / name): digest for name, digest in plan["artifacts"].items()})
    plan_path = relative(args.output / "plan.json")
    paths[plan_path] = file_sha(args.output / "plan.json")
    committed(paths)
    commit = subprocess.check_output(["git", "-C", str(ROOT), "log", "-1", "--format=%H", "--", plan_path], text=True).strip()
    require(bool(commit), "Commit the native plan before execution")
    subprocess.check_call(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", commit, "HEAD"])
    for name, expected in paths.items():
        require(sha(subprocess.check_output(["git", "-C", str(ROOT), "show", commit + ":" + name])) == expected,
                "Native freeze commit did not contain exact bound bytes: " + name)
    return plan, {"commit": commit, "bound_blob_count": len(paths),
                  "committed_at_unix": int(subprocess.check_output(
                      ["git", "-C", str(ROOT), "show", "-s", "--format=%ct", commit], text=True))}


class RecordingEmbedder(QwenEmbedder):
    query_calls = document_calls = 0

    def embed_query(self, query):
        value = super().embed_query(query)
        self.query_calls += 1
        self.last_query = value.copy()
        return value

    def embed_documents(self, texts, *, cancel_check=None, progress_callback=None, batch_callback=None):
        self.document_calls += 1
        return super().embed_documents(texts, cancel_check=cancel_check,
            progress_callback=progress_callback, batch_callback=batch_callback)


def make_backend(args, data, db, embedder):
    return backend_module.SearchBackend(args.corpus, data["source_root"], db, embedder=embedder,
                                        source_exclusions=data.get("source_exclusions", ()))


def summarize(rows):
    result = base.summarize(rows)
    for retrieval, value in result["per_retrieval"].items():
        mode = [row for row in rows if row["retrieval"] == retrieval]
        for arm, metric in value["arms"].items():
            metric.pop("delivered_composites")
            metric.pop("active_composite_calls")
        value.pop("active_composite_overhead")
        candidate_rows = [row for row in mode if row["arm"] == "candidate"]
        controls = {arm: {(row["query_id"], row["repetition"]): row for row in mode if row["arm"] == arm}
                    for arm in ARMS[:-1]}
        counts = {"clause_activated_calls": sum(row["flow_receipt"]["base_plan"]["activated"] for row in candidate_rows),
                  "admitted_calls": sum(row["flow_receipt"]["accepted"] for row in candidate_rows),
                  "guard_accepted_calls": sum(row["source_guard_receipt"]["accepted"] for row in candidate_rows),
                  "compact_changed_calls": sum(row["compact_sha256"] != controls["coverage_control"][(row["query_id"], row["repetition"])]["compact_sha256"]
                                               for row in candidate_rows)}
        value["flow_activity"] = counts
        value["paired_wall_by_admission"] = {}
        for active in (False, True):
            own = [row for row in candidate_rows if bool(row["flow_receipt"]["accepted"]) is active]
            value["paired_wall_by_admission"]["admitted" if active else "inactive_or_blocked"] = {
                "samples": len(own), "median_paired_delta_to_controls_s": {arm: statistics.median(
                    row["elapsed_backend_s"] - controls[arm][(row["query_id"], row["repetition"])]["elapsed_backend_s"]
                    for row in own) if own else None for arm in ARMS[:-1]}}
    return result


def run(args):
    markers = ("run-started.json", "searches.jsonl.gz", "summary.json", "failure.json")
    require(not any((args.output / name).exists() for name in markers), "Attempt exists; never overwrite, resume or retry")
    data, sources, inventory, binding = identity(args)
    plan, chain = verify_plan(args, binding)
    started_at = datetime.now(timezone.utc).isoformat()
    require(chain["committed_at_unix"] <= datetime.fromisoformat(started_at).timestamp(), "Native freeze follows execution")
    write(args.output / "run-started.json", {"started_at_utc": started_at,
        "plan_sha256": file_sha(args.output / "plan.json"), "execution_commit": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "prospective_freeze": chain, "resume_allowed": False, "arms": list(ARMS),
        "candidate": CANDIDATE, "expected_measured_calls": 252, "expected_warmups": 6})
    rows, phase, attempted = [], "setup", 0
    try:
        rank_bytes = (args.tokenizer_cache / replay.CACHE_KEY).read_bytes()
        require(sha(rank_bytes) == replay.CACHE_SHA256, "Frozen tokenizer bytes changed")
        import tiktoken.load
        def local_rank_bytes(url, expected_hash=None):
            require(url == replay.CACHE_URL and expected_hash == replay.CACHE_SHA256, "Unexpected tokenizer resource")
            return rank_bytes
        with patch.object(tiktoken.load, "read_file_cached", local_rank_bytes):
            context.encoding()
        embedder = RecordingEmbedder(args.model, progress=True)
        db = ROOT / ".cache/indexes" / ("flow-" + sha(relative(args.output).encode())[:20] + ".sqlite3")
        setup_started = time.perf_counter()
        with closing(make_backend(args, data, db, embedder)) as backend:
            warmups = []
            for condition in ("legacy", "coverage_control", CANDIDATE):
                selector, postprocessor = hooks(condition)
                with patch.object(backend_module, "select_context", selector), patch.object(backend_module, "postprocess_context", postprocessor):
                    for retrieval in RETRIEVALS:
                        before, documents = embedder.query_calls, embedder.document_calls
                        backend.search(WARM_QUERY, retrieval=retrieval, **SETTINGS)
                        warmups.append({"condition": condition, "retrieval": retrieval, "query": WARM_QUERY,
                            "query_embedding_calls": embedder.query_calls - before,
                            "document_embedding_calls": embedder.document_calls - documents})
                        require(warmups[-1]["query_embedding_calls"] == 1, "Each warmup requires one actual query embedding")
            setup = {"index_model_and_explicit_warmup_s": time.perf_counter() - setup_started,
                     "query_calls": embedder.query_calls, "document_calls": embedder.document_calls,
                     "warmups": warmups, "status": backend.status()}
            require(len(warmups) == embedder.query_calls == 6, "Exactly six explicit warmups are required")
            require(backend._definition_keys == {key: tuple(value) for key, value in inventory["definition_keys"].items()},
                    "Live canonical definition map differs from frozen inventory")
            actual_graph = [{"caller": list(caller), "targets": [list(target) for target in targets]}
                            for caller, targets in sorted(backend._references.edges.items())]
            require(actual_graph == inventory["graph_edges"], "Live conservative graph differs from frozen source graph")
            phase = "measured_calls"
            rankings, vectors, payloads, references, reference_views = {}, {}, {}, {}, {}
            with (args.output / "searches.jsonl.gz").open("xb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
                    for repetition, query, arm, retrieval in schedule(data["queries"]):
                        attempted += 1
                        condition = CANDIDATE if arm == "candidate" else arm
                        before, documents = embedder.query_calls, embedder.document_calls
                        with capture(condition) as captured:
                            started = time.perf_counter()
                            response = backend.search(query["query"], retrieval=retrieval, **SETTINGS)
                            elapsed = time.perf_counter() - started
                        query_delta, document_delta = embedder.query_calls - before, embedder.document_calls - documents
                        require(query_delta == 1 and document_delta == 0,
                                "Measured call requires exactly one actual query embedding and no document work")
                        outcome = receipt(query, repetition, retrieval, arm, condition, response, captured, elapsed, embedder.last_query)
                        outcome.update(query_embedding_calls=query_delta, document_embedding_calls=document_delta)
                        ranked = outcome["stages"]["ranked"]
                        for stage in ("ranked", "selected", "eligible"):
                            for pair in outcome["stages"][stage]:
                                require("assembly" not in pair["snippet"], "Shared flow cannot invent source unions")
                                base.checked_snippet(pair["snippet"], sources, inventory["inventory"], inventory["definition_keys"], ranked)
                        for name in ("response", "pre_postprocess_response", "reference_response", "proposal_response"):
                            if outcome.get(name) is not None:
                                base.checked_response(outcome[name], sources, inventory["inventory"], inventory["definition_keys"], ranked)
                        base.checked_candidate_rows(outcome, sources, inventory)
                        rank_key, payload_key = (query["id"], retrieval), (query["id"], retrieval, arm)
                        fingerprint = sha(canonical(ranked))
                        require(rankings.setdefault(rank_key, fingerprint) == fingerprint,
                                "Raw ranks/scores changed across paired arms/repetitions")
                        require(vectors.setdefault(query["id"], outcome["query_vector_sha256"]) == outcome["query_vector_sha256"],
                                "Actual query vectors changed across arms, modes or repetitions")
                        require(payloads.setdefault(payload_key, outcome["compact_sha256"]) == outcome["compact_sha256"],
                                "Actual compact payload changed across repetitions")
                        if arm == "candidate":
                            ref_hash = sha(context.serialize_context(context.compact_result(outcome["reference_response"])).encode())
                            view_hash = sha(canonical(source_view(outcome["reference_response"])))
                            require(references.setdefault(rank_key, ref_hash) == ref_hash
                                    and reference_views.setdefault(rank_key, view_hash) == view_hash,
                                    "Actual lazy coverage reference changed across repetitions")
                        elif arm == "coverage_control":
                            outcome["source_view_sha256"] = sha(canonical(source_view(outcome["response"])))
                        rows.append(outcome)
                        stream.write((json.dumps(outcome, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode())
                        stream.flush()
                        raw.flush()
                        print(f"r{repetition} {query['id']} {retrieval} {arm} {elapsed:.3f}s", flush=True)
            phase = "final_bindings"
            require(len(rows) == attempted == 252, "The fixed native matrix must contain252 measured calls")
            controls = {(row["query_id"], row["retrieval"]): row for row in rows if row["arm"] == "coverage_control"}
            require(all(payloads[(query_id, retrieval, "coverage_control")] == digest
                        and controls[(query_id, retrieval)]["source_view_sha256"] == reference_views[(query_id, retrieval)]
                        for (query_id, retrieval), digest in references.items()),
                    "Actual guarded reference differs from measured same-query coverage control")
        require(identity(args)[3] == binding, "Native frozen bindings changed during execution")
        verify_plan(args, binding)
        metrics = summarize(rows)
        write(args.output / "summary.json", {"complete": True, "study": "source_flow_native", "classification": args.role,
            "candidate": CANDIDATE, "requests": len(rows), "unique_queries": len(data["queries"]),
            "quality_repetition": 1, "setup": setup, "gate": GATE, "metrics": metrics,
            "arms": {"legacy": "legacy", "coverage_control": "coverage_control", "candidate": CANDIDATE},
            "ranked_repeat_changes": 0, "compact_repeat_changes": 0, "query_vector_repeat_changes": 0,
            "measured_query_embeddings": sum(row["query_embedding_calls"] for row in rows),
            "measured_document_embedding_calls": sum(row["document_embedding_calls"] for row in rows),
            "total_query_embeddings": embedder.query_calls, "expected_requests": 252,
            "raw_sha256": file_sha(args.output / "searches.jsonl.gz"), "plan_sha256": file_sha(args.output / "plan.json")})
        return metrics
    except BaseException as error:
        write(args.output / "failure.json", {"phase": phase, "completed_requests": len(rows),
            "attempted_requests": attempted, "error_type": type(error).__name__, "error": str(error),
            "resume_allowed": False, "plan_sha256": file_sha(args.output / "plan.json")})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "corpus", "model", "output", "source-review", "development-study", "definition-inventory"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--candidate", choices=(CANDIDATE,), default=CANDIDATE)
    parser.add_argument("--role", choices=("repository_holdout",), default="repository_holdout")
    parser.add_argument("--tokenizer-cache", type=Path, default=Path(os.environ.get("TIKTOKEN_CACHE_DIR", ROOT / ".cache/tokenizers")))
    parser.add_argument("--repetitions", type=int, default=3)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--freeze", action="store_true")
    action.add_argument("--run", action="store_true")
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    result = freeze(args) if args.freeze else run(args)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
