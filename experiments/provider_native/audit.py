"""Independent audit of an observed four-arm native provider comparison.

The frozen independent role-native auditor reconstructs source, allocation,
features, forwards and terminal roles in two three-arm projections. This module
checks the actual four-arm execution order, measured integration boundary,
strict original/fast parity, complete provenance and all summary arithmetic.
No native producer, backend, model, provider or NumPy is imported or executed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import importlib.abc
import json
import math
from pathlib import Path
import re
import socket
import statistics
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import audit_role_native as old  # noqa: E402

require, same, read, canonical, sha, file_sha, jsonl = (
    old.require, old.same, old.read, old.canonical, old.sha, old.file_sha, old.jsonl)
SCHEMA = "provider_native_study_v1"
AUDIT_SCHEMA = "provider_native_audit_v1"
CLASSIFICATION = "observed_native_provider_comparison"
ARMS = ("legacy", "coverage_control", "original_role", "fast_role")
ROLES = ARMS[2:]
MODES = ("dense", "hybrid")
PARENT_COMMIT = "470451f571408cb54c99307d3b2aed5f56dadb1f"
NATIVE_PUBLICATION = "b08c784162c4a89192c1cd0a1bf94f6d35edddcb"
NATIVE = "results/role-native-holdout-v2-2026-11-03"
CPU = "results/dependency-provider-cpu-2026-11-09"
DEVELOPMENT = "results/dependency-context-continuation-2026-11-09"
GATE = {"max_median_ratio_vs_controls": 1.25, "zero_individual_losses_vs_original_role": True,
        "exact_fast_original_parity": True}
EVENT_CLOCKS = {"elapsed_seconds", "duration_seconds"}
LEGACY_ARTIFACTS = {"run-started.json", "setup.json", "warmups.jsonl.gz", "searches.jsonl.gz",
                    "gpu-lease-acquired.json", "model-closed.json", "gpu-lease-released.json"}
SEARCH_SHARDS = tuple("searches-r" + str(repetition) + ".jsonl.gz" for repetition in (1, 2, 3))
ATTEMPT_ARTIFACTS = (LEGACY_ARTIFACTS - {"searches.jsonl.gz"}) | set(SEARCH_SHARDS)
SOURCE_ONLY_CALLS = {name: 0 for name in ("model", "index", "retrieval", "selector", "postprocessor",
                                         "rule_activation", "gold_coverage_scoring", "new_native_outcomes")}


def safe_path(root, name):
    root = Path(root).resolve()
    require(type(name) is str and name and not Path(name).is_absolute() and ".." not in Path(name).parts,
            "Repository-relative authority path required")
    result = (root / name).resolve()
    require(result.is_relative_to(root), "Authority path escapes checkout")
    return result


def verify_blobs(root, names, commits):
    require(type(names) is dict and names, "Explicit nonempty frozen byte authority required")
    for name, value in names.items():
        path = safe_path(root, name)
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "SHA-256 authority required")
        require(file_sha(path) == value, "Working authority bytes differ: " + name)
        old.role.verify_blob(root, name, value, commits)


def source_hashes(root, manifest_path):
    root = Path(root).resolve()
    paths = {path for folder in ("src", "scripts", "experiments/dependency_context", "experiments/provider_native")
             for path in (root / folder).rglob("*.py")}
    paths.update((root / "tests").glob("test_provider_native*.py"))
    paths.update(root / name for name in ("experiments/__init__.py", "tests/test_dependency_fast_provider.py",
        "tests/test_dependency_provider_profile.py", "docs/provider-native-protocol.md", "pyproject.toml",
        "requirements-mac.lock", manifest_path))
    return {path.relative_to(root).as_posix(): file_sha(path) for path in sorted(paths)}


def source_authority(root=ROOT, authority=None):
    root = Path(root).resolve()
    require(Path(__file__).resolve() == root / "experiments/provider_native/audit.py", "Executing independent auditor path differs")
    for name, module in tuple(sys.modules.items()):
        if name.startswith("audit_") or name == "complementary_inputs" or name == "flow_native_independent_development":
            expected = root / "scripts"
        elif name == "experiments" or name.startswith("experiments."):
            expected = root / "experiments"
        elif name == "semantic_search" or name.startswith("semantic_search."):
            expected = root / "src/semantic_search"
        else:
            continue
        path = getattr(module, "__file__", None)
        require(path is not None and Path(path).resolve().is_relative_to(expected), "Independent loaded source escapes checkout: " + name)
        if authority is not None:
            relative = Path(path).resolve().relative_to(root).as_posix()
            require(authority.get(relative) == file_sha(path), "Executing helper absent from frozen source authority: " + name)


def parent_authority(root, plan):
    """Immutable observed proof is checked before tokenizer/checkpoint/corpus reads."""
    root = Path(root).resolve()
    parent, reference = plan.get("parent"), plan.get("legacy_plan")
    require(type(parent) is dict and parent.get("commit") == PARENT_COMMIT
            and type(parent.get("artifacts")) is dict, "Complete published observed parent authority required")
    require(type(reference) is dict and set(reference) == {"path", "sha256", "publication_commit"}
            and reference["path"] == NATIVE + "/plan.json" and reference["publication_commit"] == NATIVE_PUBLICATION,
            "Exact immutable original native plan reference required")
    mandatory = {NATIVE + "/" + name for name in ("plan.json", "source-inventory.json", "independent-audit.json",
        "input-audit.json", "attempt/run-started.json", "attempt/summary.json", "attempt/searches.jsonl.gz")}
    mandatory.update(CPU + "/" + name for name in ("paired/run-start.json", "paired/summary.json",
        "independent-review.json", "independent-review-script.py", "independent-review-notes.md"))
    mandatory.update(DEVELOPMENT + "/" + name for name in ("study-freeze.json", "prepared/frozen-plan.json",
        "replay/summary.json", "independent-audit.json", "findings.md", "preflight/failure-authority.json"))
    require(mandatory <= set(parent["artifacts"]), "Published CPU/native/failed-quality parent closure omitted")
    verify_blobs(root, parent["artifacts"], (PARENT_COMMIT, "HEAD"))
    old.ancestor(root, PARENT_COMMIT, "HEAD")
    verify_blobs(root, {reference["path"]: reference["sha256"]}, (NATIVE_PUBLICATION, PARENT_COMMIT, "HEAD"))
    legacy_plan = read(root / reference["path"])
    legacy_binding = legacy_plan["binding"]
    verify_blobs(root, {**legacy_binding["implementation_sha256"], **legacy_binding["development_parent"]["artifacts"]},
                 (NATIVE_PUBLICATION, PARENT_COMMIT, "HEAD"))
    report = read(root / NATIVE / "independent-audit.json")
    require(report.get("checks_passed") is True and report.get("status") == "passed"
            and report.get("verified_requests") == 252 and report.get("verified_warmups") == 6
            and report["independently_recomputed_metrics"]["dense_promotion_gate_passed"] is False,
            "Original successful integrity and failed native qualification must remain explicit")
    native_summary = read(root / NATIVE / "attempt/summary.json")
    native_artifacts = {NATIVE + "/attempt/" + name: value for name, value in native_summary["artifact_sha256"].items()}
    require(set(native_summary["artifact_sha256"]) == LEGACY_ARTIFACTS, "Original native full successful ledger closure required")
    verify_blobs(root, native_artifacts, (NATIVE_PUBLICATION, PARENT_COMMIT, "HEAD"))
    review = read(root / CPU / "independent-review.json")
    require(review.get("schema") == "dependency_provider_cpu_independent_review_v1"
            and review.get("status") == "passed" and review.get("checks_passed") is True,
            "Independent original/fast CPU parity proof required")
    require(set(review["input_artifact_sha256"]) <= set(parent["artifacts"])
            and all(parent["artifacts"].get(name) == value for name, value in review["input_artifact_sha256"].items()),
            "Complete transitive CPU proof artifacts omitted or self-rebound")
    require(review.get("original_native_archive_sha256") == file_sha(root / NATIVE / "attempt/searches.jsonl.gz"),
            "CPU parity must use the exact original native archive")
    failed = read(root / DEVELOPMENT / "independent-audit.json")
    require(failed.get("status") == "passed" and failed.get("checks_passed") is True
            and failed.get("verified_terminal_decisions") == 477
            and failed["independently_recomputed"]["quality_gate"]["qualified"] is False,
            "Preserve the subsequently failed observed dependency quality gate")
    dependency_freeze = read(root / DEVELOPMENT / "study-freeze.json")
    dependency_summary = read(root / DEVELOPMENT / "replay/summary.json")
    failure_authority = read(root / DEVELOPMENT / "preflight/failure-authority.json")
    require(failure_authority.get("schema") == "dependency_continuation_failure_authority_preflight_v1"
            and failure_authority.get("status") == "passed"
            and failure_authority.get("prior_publication_commit") == "d16f876d29ec821226d595512253c3b4152b6c06"
            and type(failure_authority.get("preserved_partial_outcomes")) is int
            and failure_authority["preserved_partial_outcomes"] == 396 and failure_authority.get("resume_allowed") is False,
            "Original396-row negative failure authority must remain explicit")
    maps = [dependency_freeze["sources"], dependency_freeze["artifacts"],
            {DEVELOPMENT + "/replay/" + name: value for name, value in dependency_summary["artifacts"].items()},
            failure_authority["original_failure_artifact_sha256"]]
    require(set(dependency_summary["artifacts"]) == {"run-start.json", "graphs.jsonl.gz", "outcomes.jsonl.gz"}
            and len(dependency_freeze["artifacts"]) == 6
            and len(failure_authority["original_failure_artifact_sha256"]) == 5,
            "Complete dependency prepared/raw/failure artifact membership required")
    for required_map in maps:
        require(type(required_map) is dict and required_map
                and set(required_map) <= set(parent["artifacts"])
                and all(parent["artifacts"].get(name) == value for name, value in required_map.items()),
                "Transitive negative dependency source/input/raw/failure proof omitted or self-rebound")
    return legacy_plan, {"commit": PARENT_COMMIT, "bound_artifacts": len(parent["artifacts"]),
                         "old_native_publication": NATIVE_PUBLICATION, "old_native_requests": 252,
                         "cpu_parity_integrity_passed": True, "dependency_quality_qualified": False}


def first_source_freeze(root, plan, legacy_plan):
    frozen, manifest = plan.get("source_freeze"), plan.get("runtime_manifest")
    require(type(frozen) is dict and type(manifest) is dict and same(manifest, legacy_plan["binding"]["runtime_manifest"]),
            "Unchanged actual runtime/model manifest and source-first freeze required")
    expected = source_hashes(root, manifest["manifest_path"])
    require(same(frozen.get("sha256"), expected) and type(frozen.get("bound_blob_count")) is int
            and frozen["bound_blob_count"] == len(expected), "Complete source-first byte closure differs")
    old.ancestor(root, PARENT_COMMIT, frozen["commit"])
    old.ancestor(root, frozen["commit"], "HEAD")
    verify_blobs(root, expected, (frozen["commit"], "HEAD"))
    verify_blobs(root, plan["parent"]["artifacts"], (frozen["commit"], "HEAD"))
    committed = int(old.git(root, "show", "-s", "--format=%ct", frozen["commit"]).decode().strip())
    require(type(frozen.get("committed_at_unix")) is int and frozen["committed_at_unix"] == committed
            and committed <= datetime.fromisoformat(plan["frozen_at_utc"]).timestamp(), "Source-first chronology differs")
    return frozen


def audit_inputs(root, corpus, results, counter=None):
    root, results = Path(root).resolve(), Path(results).resolve()
    require(results.is_relative_to(root), "Observed native plan must be inside frozen checkout")
    plan = read(results / "plan.json")
    legacy_plan, parent = parent_authority(root, plan)
    frozen = first_source_freeze(root, plan, legacy_plan)
    source_authority(root, frozen["sha256"])
    require(old.role.literal_json(plan) and all(type(plan.get(name)) is int for name in
            ("version", "expected_requests", "expected_warmups", "repetitions", "quality_repetition"))
            and plan.get("schema") == SCHEMA and plan.get("version") == 1 and plan.get("classification") == CLASSIFICATION
            and plan.get("expected_requests") == 336 and plan.get("expected_warmups") == 8
            and plan.get("repetitions") == 3 and plan.get("quality_repetition") == 1
            and same(plan.get("arms"), list(ARMS)) and same(plan.get("retrievals"), list(MODES))
            and same(plan.get("settings"), old.SETTINGS)
            and same(plan.get("gate"), GATE), "Fixed observed four-arm comparison declaration differs")
    require(set(plan.get("artifacts", {})) == {"source-inventory.json"}
            and plan["artifacts"]["source-inventory.json"] == file_sha(results / "source-inventory.json")
            == file_sha(Path(root) / NATIVE / "source-inventory.json"), "Unchanged original source inventory bytes required")
    manifest = plan["runtime_manifest"]
    manifest_path = safe_path(root, manifest["manifest_path"])
    require(file_sha(manifest_path) == manifest["manifest_sha256"]
            and same(read(manifest_path).get("model_binding"), manifest["model_binding"]), "Original runtime/model manifest bytes differ")
    model = old.semantic.verify_model_binding(manifest["model_binding"])
    require(same(legacy_plan["binding"]["embedding"], manifest["model_binding"]["metadata"]), "Native metadata changed from pinned original")
    data, fixture, definitions = old.fresh_fixture(root, corpus, results, legacy_plan["binding"])
    queries, groups, multi = old.verify_queries(data, legacy_plan, fixture, legacy_plan["binding"])
    require(same(plan.get("queries"), legacy_plan["queries"]), "All original 14 observed queries/order required")
    receipt = {"schema": "provider_native_input_audit_v1", "status": "passed", "checks_passed": True,
               "classification": CLASSIFICATION, "plan_sha256": file_sha(results / "plan.json"),
               "source_inventory_sha256": file_sha(results / "source-inventory.json"), "source_freeze": frozen,
               "parent": deepcopy(plan["parent"]), "parent_verification": parent,
               "source_sha256": frozen["sha256"], "original_native_input_audit_sha256": file_sha(root / NATIVE / "input-audit.json"),
               "independent_python_inventory": definitions, "source_only_calls": dict(SOURCE_ONLY_CALLS),
               "new_native_outcomes_computed": 0, "execution_commit": old.git(root, "rev-parse", "HEAD").decode().strip(),
               "reviewed_at_utc": datetime.now(timezone.utc).isoformat()}
    return receipt, {"plan": plan, "legacy_plan": legacy_plan, "data": data, "fixture": fixture,
                     "queries": queries, "groups": groups, "multi": multi, "model_verification": model}


def project_row(row):
    value = deepcopy(row)
    require(value.get("arm") in ARMS, "Unknown native arm")
    if value["arm"] in ROLES:
        value["arm"], value["condition"] = "candidate", old.MODE
    return value


def expected_schedule(queries):
    conditions = [(arm, mode) for mode in MODES for arm in ARMS]
    return [(repeat, query["id"], arm, mode) for repeat in range(1, 4) for index, query in enumerate(queries)
            for arm, mode in conditions[(index+repeat-1) % 8:] + conditions[:(index+repeat-1) % 8]]


def project_rows(rows, role_arm, queries):
    require(role_arm in ROLES, "One original/fast role projection required")
    indexed = {(row["repetition"], row["query_id"], "candidate" if row["arm"] == role_arm else row["arm"], row["retrieval"]): row
               for row in rows if row["arm"] in ("legacy", "coverage_control", role_arm)}
    schedule = old.expected_schedule(queries)
    require(len(indexed) == len(schedule) and set(indexed) == set(schedule), "Complete three-arm independent projection required")
    return [project_row(indexed[key]) for key in schedule]


def finite_duration(value):
    return type(value) is float and math.isfinite(value) and value >= 0


def verify_integration(row):
    receipt = row.get("provider_integration")
    fields = {"schema", "arm", "implementation", "factory_attempts", "factory_completed", "factory_failed",
              "provider_attempts", "provider_completed", "provider_failed", "factory_elapsed_s", "provider_elapsed_s"}
    require(type(receipt) is dict and set(receipt) == fields and receipt["schema"] == "provider_native_integration_v1"
            and receipt["arm"] == row["arm"], "Actual provider integration receipt fields/arm differ")
    role_arm = row["arm"] in ROLES
    calls = row["candidate_capture"]["feature_calls"] if role_arm else 0
    require(type(calls) is int and calls in (0, 1), "Actual provider invocation must match literal eligibility")
    expected = {"factory_attempts": int(role_arm), "factory_completed": int(role_arm), "factory_failed": 0,
                "provider_attempts": calls, "provider_completed": calls, "provider_failed": 0}
    require(all(type(receipt[name]) is int and receipt[name] == value for name, value in expected.items())
            and receipt["implementation"] == ({"original_role": "original", "fast_role": "fast"}.get(row["arm"])),
            "Actual provider factory/call counts or implementation differ")
    factory, provider = receipt["factory_elapsed_s"], receipt["provider_elapsed_s"]
    require(finite_duration(factory) and finite_duration(provider) and factory+provider <= row["elapsed_backend_s"] + 1e-9,
            "Factory/clone/provider work lies outside complete backend timer")
    require((factory > 0 if role_arm else factory == 0.0)
            and (provider > 0 if calls else provider == 0.0), "Untimed inactive/control provider work reported")
    if role_arm:
        require(provider <= row["candidate_capture"]["phase_timings"]["live_feature_provider_s"] + 1e-9,
                "Actual provider call clock is outside enclosing live-feature phase")
        require(factory + math.fsum(row["candidate_capture"]["phase_timings"].values()) <= row["elapsed_backend_s"] + 1e-9,
                "Disjoint factory/clone and context phases exceed complete backend interval")


def normalized_response(response):
    require(type(response) is dict and old.role.literal_json(response), "Finite full response required for parity")
    value = deepcopy(response)
    if "timings" in value:
        require(type(value["timings"]) is dict, "Top-level native timings must be a finite dictionary")
        require(all(finite_duration(duration) for duration in value["timings"].values()),
                "Top-level native timing values must be finite nonnegative floats")
        value["timings"] = {name: "__actual_native_clock__" for name in value["timings"]}
    if "indexstats" in value:
        require(type(value["indexstats"]) is dict, "Native indexstats must be a dictionary")
        if "elapsed_s" in value["indexstats"]:
            require(finite_duration(value["indexstats"]["elapsed_s"]), "Actual per-refresh elapsed time must be finite")
            value["indexstats"]["elapsed_s"] = "__actual_native_refresh_duration__"
    return value


def normalized_events(events):
    require(type(events) is list and old.role.literal_json(events), "Actual event ledger required for parity")
    return [{key: ("__actual_native_clock__" if key in EVENT_CLOCKS else deepcopy(value)) for key, value in event.items()}
            for event in events]


def normalized_receipts(receipts):
    value = deepcopy(receipts)
    for receipt in value:
        for key in ("original_query_forward_events", "extra_query_forward_events"):
            if key in receipt:
                receipt[key] = normalized_events(receipt[key])
    return value


def verify_pair(original, fast):
    require(original["arm"] == "original_role" and fast["arm"] == "fast_role"
            and all(same(original.get(key), fast.get(key)) for key in ("query_id", "retrieval", "repetition", "kind", "multi_file")),
            "Original/fast actual request pair differs")
    # Forward clocks are individually validated before this comparison. Their
    # presence, field sets, shapes, token identities and statuses stay strict.
    require(same(normalized_events(original["external_forward_events"]), normalized_events(fast["external_forward_events"])),
            "Original query forward identity differs beyond actual clock values")
    for key in ("query_vector", "query_vector_sha256", "stages", "ranked_candidates", "backfill_candidates",
                "candidate_ranks", "delivered_ranks", "compact_payload", "compact_sha256", "actual_compact_tokens",
                "query_embedding_calls", "document_embedding_calls", "clause_query_calls", "clause_query_texts",
                "guard_calls", "resident_reads", "forward_counts"):
        require(same(original.get(key), fast.get(key)), "Original/fast strict native parity differs: " + key)
    for key in ("pre_postprocess_response", "response", "reference_response", "proposal_response"):
        require(same(normalized_response(original[key]), normalized_response(fast[key])),
                "Original/fast full source/metadata parity differs: " + key)
        require(old.context.compact_payload(original[key]) == old.context.compact_payload(fast[key]),
                "Original/fast actual compact scope/metadata byte order differs: " + key)
    left, right = original["candidate_capture"], fast["candidate_capture"]
    require(left.keys() == right.keys(), "Original/fast actual capture field presence differs")
    for key in left:
        if key in {"reference_response", "v1_response", "response"}:
            require(same(normalized_response(left[key]), normalized_response(right[key]))
                    and old.context.compact_payload(left[key]) == old.context.compact_payload(right[key]),
                    "Original/fast captured source/metadata differs: " + key)
        elif key == "provider_receipts":
            require(same(normalized_receipts(left[key]), normalized_receipts(right[key])), "Actual provider receipt differs beyond enumerated forward clocks")
        elif key == "phase_timings":
            continue  # Each actual phase clock is checked independently, not equal across separate calls.
        else:
            require(same(left[key], right[key]), "Original/fast captured features/roles/source parity differs: " + key)


def verify_matrix(rows, queries):
    require(all(type(row.get("repetition")) is int and row["repetition"] in (1, 2, 3) for row in rows)
            and len(rows) == len(queries) * 24 and [(row["repetition"], row["query_id"], row["arm"], row["retrieval"]) for row in rows]
            == expected_schedule(queries), "Complete actual eight-condition rotated native schedule differs")
    vectors, ranked, compact, snapshots = {}, {}, {}, set()
    for row in rows:
        require(vectors.setdefault(row["query_id"], row["query_vector_sha256"]) == row["query_vector_sha256"],
                "Actual query vector changed across four arms/modes/repetitions")
        key, raw_hash = (row["query_id"], row["retrieval"]), sha(canonical(row["stages"]["ranked"]))
        require(ranked.setdefault(key, raw_hash) == raw_hash, "Raw source/ranking/scalars changed across native requests")
        key = row["query_id"], row["retrieval"], row["arm"]
        require(compact.setdefault(key, row["compact_sha256"]) == row["compact_sha256"], "Actual final context changed across repetitions")
        if row.get("candidate_capture"):
            snapshots.add(row["candidate_capture"]["request_binding"]["source_snapshot"])
    require(len(snapshots) <= 1, "Native source snapshot changed across role arms")
    old.verify_resident_stability(rows)


def metrics(rows, queries):
    role_arms = {arm: old.native_metrics(project_rows(rows, arm, queries)) for arm in ROLES}
    comparison, phases = {}, {}
    for mode in MODES:
        own = {arm: [row for row in rows if row["retrieval"] == mode and row["arm"] == arm] for arm in ROLES}
        times = {arm: sorted(row["elapsed_backend_s"] for row in selected) for arm, selected in own.items()}
        indexed = {arm: {(row["query_id"], row["repetition"]): row for row in selected} for arm, selected in own.items()}
        original, fast = times["original_role"], times["fast_role"]
        comparison[mode] = {"original_median_s": statistics.median(original), "fast_median_s": statistics.median(fast),
            "original_p95_s": original[math.ceil(.95*len(original))-1], "fast_p95_s": fast[math.ceil(.95*len(fast))-1],
            "fast_over_original_median_ratio": statistics.median(fast)/statistics.median(original),
            "median_paired_delta_s": statistics.median(indexed["fast_role"][key]["elapsed_backend_s"]-value["elapsed_backend_s"]
                for key, value in indexed["original_role"].items()), "timed_requests_per_arm": len(original)}
        phases[mode] = {arm: {"factory_setup_s": statistics.median(row["provider_integration"]["factory_elapsed_s"] for row in selected),
                             "provider_call_s": statistics.median(row["provider_integration"]["provider_elapsed_s"] for row in selected)}
                        for arm, selected in own.items()}
    return {"role_arms": role_arms, "provider_comparison": comparison, "phase_medians": phases}


def audit_rows(rows, warmups, prepared, counter):
    fixture, metadata = prepared["fixture"], prepared["legacy_plan"]["binding"]["embedding"]
    queries, data = prepared["queries"], prepared["data"]
    verify_matrix(rows, data["queries"])
    require([(row["arm"], row["retrieval"]) for row in warmups] == [(arm, mode) for arm in ARMS for mode in MODES],
            "Eight fixed actual arm/mode warmups required")
    scope = old.native.native_scope(data)
    activity, diagnostics = [], []
    for warmup, row in [(True, row) for row in warmups] + [(False, row) for row in rows]:
        if warmup:
            require(row["query_id"] == "__generic_warmup__" and type(row["repetition"]) is int
                    and row["repetition"] == 0 and row["kind"] == "absent"
                    and row["multi_file"] is False and row["candidate_ranks"] == row["delivered_ranks"] == [],
                    "Warmups cannot use authored quality queries")
            query = "Find code that handles input and returns a result"
        else:
            query = queries[row["query_id"]]["query"]
            require(row["kind"] == queries[row["query_id"]]["kind"] and row["multi_file"] is prepared["multi"][row["query_id"]],
                    "Original observed query strata changed")
        require(row["condition"] == (old.MODE if row["arm"] in ROLES else row["arm"]), "Actual unchanged role/control condition differs")
        value = old.verify_trace(project_row(row), fixture, counter, query=query, scope=scope, model_metadata=metadata)
        old.verify_forward_counts(row)
        verify_integration(row)
        if not warmup:
            activity.append(value)
            gold = prepared["groups"][row["query_id"]]
            raw = [{**pair["snippet"], "source": pair["snippet"]["text"]} for pair in row["stages"]["ranked"]]
            require(same(row["candidate_ranks"], old.native.strict_ranks(raw, gold))
                    and same(row["delivered_ranks"], old.native.strict_ranks(row["response"]["results"], gold)),
                    "Actual complete-symbol/literal evidence ranks differ")
            if value["diagnostic_normalization"]:
                diagnostics.append({"query_id": row["query_id"], "retrieval": row["retrieval"], "repetition": row["repetition"],
                                    "arm": row["arm"], **value["diagnostic_normalization"]})
    indexed = {(row["query_id"], row["retrieval"], row["repetition"], row["arm"]): row for row in rows}
    for row in rows:
        if row["arm"] == "original_role":
            verify_pair(row, indexed[row["query_id"], row["retrieval"], row["repetition"], "fast_role"])
        if row["arm"] in ROLES:
            reference = row["candidate_capture"]["reference_response"]
            coverage = indexed[row["query_id"], row["retrieval"], row["repetition"], "coverage_control"]
            require(same(old.source_view(reference), old.source_view(coverage["response"]))
                    and old.context.compact_payload(reference) == coverage["compact_payload"],
                    "Measured role baseline differs from separately executed coverage control")
    for mode in MODES:
        verify_pair(next(row for row in warmups if row["arm"] == "original_role" and row["retrieval"] == mode),
                    next(row for row in warmups if row["arm"] == "fast_role" and row["retrieval"] == mode))
    old.verify_resident_stability([*warmups, *rows])
    return {"metrics": metrics(rows, data["queries"]), "verified_requests": len(rows), "verified_warmups": len(warmups),
            "verified_role_pairs": len(rows)//4, "measured_model_forward_calls": len(rows)+sum(value["extra_model_forwards"] for value in activity),
            "measured_extra_clause_texts": sum(value["extra_texts"] for value in activity),
            "inactive_diagnostic_wording_normalizations": diagnostics, "exact_fast_original_parity": True}


def verify_setup(setup, warmups, fixture, metadata, *, root, attempt):
    require(type(setup.get("warmup_calls")) is int and setup["warmup_calls"] == 8
            and type(setup.get("query_api_calls")) is int and setup["query_api_calls"] == 8,
            "Eight actual original-query warmup calls required")
    calls = setup.get("document_calls")
    require(type(calls) is list and bool(calls) and type(setup.get("document_api_calls")) is int
            and setup["document_api_calls"] == len(calls), "Actual cold document API ledger required")
    expected = []
    for item in fixture["inventory"].values():
        text = old.context.document_text(old.native.full_rows([{"snippet": item, "score": 0.0}])[0])
        if text not in expected:
            expected.append(text)
    observed, external, forwards, duration = [], [], 0, 0.0
    for call in calls:
        require(call.get("status") == "completed" and type(call.get("texts")) is list, "Cold document API incomplete")
        counts = old.semantic.verify_forward_events(call["forward_events"], call["texts"], metadata)
        observed.extend(call["texts"])
        external.extend({"purpose": "documents", **event} for event in call["forward_events"])
        forwards += counts["model_forward_calls"]
        duration += math.fsum(event["duration_seconds"] for event in call["forward_events"] if event["status"] == "completed")
    require(same(observed, expected) and same(setup.get("external_forward_events"), external),
            "Actual cold documents differ from complete unique original source wrappers")
    require(type(setup.get("clause_api_calls")) is int
            and setup["clause_api_calls"] == sum(row["clause_query_calls"] for row in warmups)
            and type(setup.get("clause_encoded_texts")) is int
            and setup["clause_encoded_texts"] == sum(row["clause_query_texts"] for row in warmups),
            "Actual warmup clause inference counts differ")
    expected_db = Path(root) / ".cache/indexes" / ("provider-native-" + sha(attempt.relative_to(root).as_posix().encode())[:20] + ".sqlite3")
    require(Path(setup["db_path"]).resolve() == expected_db.resolve(), "Cold/warm index is not owned by this exact attempt")
    elapsed = setup.get("index_model_and_explicit_warmup_s")
    require(finite_duration(elapsed) and elapsed > 0
            and duration + math.fsum(row["elapsed_backend_s"] for row in warmups) <= elapsed + 1e-9,
            "Cold actual forwards/warmups exceed complete setup interval")
    return {"warmups": 8, "document_api_calls": len(calls), "document_model_forwards": forwards,
            "unique_document_texts": len(expected), "clause_api_calls": setup["clause_api_calls"],
            "clause_encoded_texts": setup["clause_encoded_texts"]}


def verify_plan_freeze(root, results, plan, start, input_receipt):
    root, results = Path(root).resolve(), Path(results).resolve()
    names = {**plan["source_freeze"]["sha256"], **plan["parent"]["artifacts"]}
    names.update({(results / name).relative_to(root).as_posix(): value for name, value in plan["artifacts"].items()})
    names.update({(results / name).relative_to(root).as_posix(): file_sha(results / name) for name in ("plan.json", "input-audit.json")})
    frozen, execution = start["prospective_freeze"], start["execution_commit"]
    old.ancestor(root, plan["source_freeze"]["commit"], frozen["commit"])
    old.ancestor(root, input_receipt["execution_commit"], frozen["commit"])
    old.ancestor(root, frozen["commit"], execution)
    old.ancestor(root, execution, "HEAD")
    committed = int(old.git(root, "show", "-s", "--format=%ct", frozen["commit"]).decode().strip())
    require(same(frozen, {"commit": frozen["commit"], "bound_blob_count": len(names), "committed_at_unix": committed})
            and committed <= datetime.fromisoformat(start["started_at_utc"]).timestamp(), "Complete committed source/input barrier must precede native work")
    verify_blobs(root, names, (frozen["commit"], execution, "HEAD"))
    return frozen, execution


def verify_chronology(start, setup, summary, acquired, closed, released, warmups, rows, plan, *, root):
    require(same({key: value for key, value in acquired.items() if key != "released_at"},
                 {key: value for key, value in released.items() if key != "released_at"})
            and acquired.get("released_at") is None and type(acquired.get("pid")) is int and acquired["pid"] > 0,
            "Actual exclusive model lease identity/release differs")
    common = Path(old.git(root, "rev-parse", "--git-common-dir").decode().strip())
    if not common.is_absolute():
        common = Path(root) / common
    require(Path(acquired["path"]).resolve() == common.resolve() / "semantic-search-gpu.lock", "Model lease must be shared across worktrees")
    require(closed.get("phase") == "measured_calls", "Successful model close must follow all measured calls")
    ordered = [*warmups, *rows]
    require(datetime.fromisoformat(start["started_at_utc"]) <= datetime.fromisoformat(acquired["acquired_at"])
            <= datetime.fromisoformat(ordered[0]["request_clock"]["started_at_utc"]), "Run/lease/warmup chronology differs")
    for row in ordered:
        old.verify_clock(row)
        clock = row["request_clock"]
        low, high = (datetime.fromisoformat(clock[key]) for key in ("started_at_utc", "ended_at_utc"))
        require(abs((high-low).total_seconds()-row["elapsed_backend_s"]) <= 2.0, "Sleep or wall/perf discontinuity invalidates measured interval")
    for previous, following in zip(ordered, ordered[1:]):
        first, second = previous["request_clock"], following["request_clock"]
        gap = (datetime.fromisoformat(second["started_at_utc"])-datetime.fromisoformat(first["ended_at_utc"])).total_seconds()
        require(0 <= gap <= 30.0 and first["ended_perf_counter"] <= second["started_perf_counter"],
                "Requests overlapped, clock regressed or native run was interrupted")
    require(datetime.fromisoformat(ordered[-1]["request_clock"]["ended_at_utc"]) <= datetime.fromisoformat(closed["closed_at"])
            <= datetime.fromisoformat(released["released_at"]), "Actual model close/lease release chronology differs")
    platform = plan["runtime_manifest"]["model_binding"]["runtime"]["platform"]
    for state in (setup.get("machine_state"), summary.get("post_run_machine_state")):
        require(type(state) is dict and type(state.get("power")) is str and bool(state["power"])
                and state.get("pid") == acquired["pid"] and state.get("platform") == platform
                and type(state.get("machine")) is str and bool(state["machine"])
                and type(state.get("python")) is str, "Actual machine/power/runtime observation required")
    require(datetime.fromisoformat(warmups[-1]["request_clock"]["ended_at_utc"])
            <= datetime.fromisoformat(setup["machine_state"]["recorded_at_utc"])
            <= datetime.fromisoformat(rows[0]["request_clock"]["started_at_utc"])
            and datetime.fromisoformat(released["released_at"])
            <= datetime.fromisoformat(summary["post_run_machine_state"]["recorded_at_utc"]), "Power/runtime observation chronology differs")


def paired_diagnostics(rows):
    result = {}
    for mode in MODES:
        groups = {arm: {(row["query_id"], row["repetition"]): row for row in rows
                       if row["arm"] == arm and row["retrieval"] == mode} for arm in ROLES}
        ratios = [groups["fast_role"][key]["elapsed_backend_s"]/value["elapsed_backend_s"] for key, value in groups["original_role"].items()]
        result[mode] = {"paired_requests": len(ratios), "median_paired_fast_over_original_ratio": statistics.median(ratios),
                        "p95_paired_fast_over_original_ratio": sorted(ratios)[math.ceil(.95*len(ratios))-1]}
    return result


def measured_rows(attempt):
    result = []
    for repetition, name in enumerate(SEARCH_SHARDS, 1):
        shard = jsonl(Path(attempt) / name)
        require(len(shard) == 112 and all(type(row.get("repetition")) is int and row["repetition"] == repetition for row in shard),
                "Each fixed native repetition shard requires exactly112 matching requests")
        result.extend(shard)
    return result


def audit_native(root, corpus, results, counter):
    root, results = Path(root).resolve(), Path(results).resolve()
    input_result, prepared = audit_inputs(root, corpus, results, counter)
    input_receipt = read(results / "input-audit.json")
    for name, value in input_result.items():
        if name not in {"execution_commit", "reviewed_at_utc"}:
            require(same(input_receipt.get(name), value), "Committed source-only input audit differs: " + name)
    attempt = results / "attempt"
    summary, start, setup = (read(attempt / name) for name in ("summary.json", "run-started.json", "setup.json"))
    require({path.name for path in attempt.iterdir()} == ATTEMPT_ARTIFACTS | {"summary.json"}
            and set(summary.get("artifact_sha256", {})) == ATTEMPT_ARTIFACTS, "Complete successful native artifact closure required")
    artifacts = summary["artifact_sha256"]
    for name, value in artifacts.items():
        require(file_sha(attempt / name) == value, "Finalized native artifact bytes differ: " + name)
    plan = prepared["plan"]
    chain, execution = verify_plan_freeze(root, results, plan, start, input_receipt)
    require(start.get("schema") == SCHEMA and start.get("resume_allowed") is False
            and start.get("expected_measured_calls") == 336 and start.get("expected_warmups") == 8
            and same(start.get("source_freeze"), plan["source_freeze"])
            and start.get("plan_sha256") == summary.get("plan_sha256") == file_sha(results / "plan.json"),
            "Actual native start/schema/source/plan binding differs")
    rows, warmups = measured_rows(attempt), jsonl(attempt / "warmups.jsonl.gz")
    require(len(rows) == 336 and len(warmups) == 8, "All336requests and8warmups required")
    counts = verify_setup(setup, warmups, prepared["fixture"], prepared["legacy_plan"]["binding"]["embedding"], root=root, attempt=attempt)
    verified = audit_rows(rows, warmups, prepared, counter)
    require(all(type(summary.get(name)) is int for name in ("quality_repetition", "requests", "measured_query_embeddings",
                "unique_queries", "measured_document_embedding_calls", "measured_clause_query_calls", "measured_clause_encoded_texts",
                "ranked_repeat_changes", "compact_repeat_changes", "query_vector_repeat_changes"))
            and summary.get("schema") == SCHEMA and summary.get("classification") == CLASSIFICATION
            and summary.get("complete") is True and summary.get("quality_repetition") == 1
            and summary.get("requests") == summary.get("measured_query_embeddings") == 336 and summary.get("unique_queries") == 14
            and summary.get("measured_document_embedding_calls") == 0
            and summary.get("measured_clause_query_calls") == sum(row["clause_query_calls"] for row in rows)
            and summary.get("measured_clause_encoded_texts") == sum(row["clause_query_texts"] for row in rows)
            and same(summary.get("setup"), setup) and same(summary.get("gate"), GATE)
            and same(summary.get("metrics"), verified["metrics"])
            and summary.get("ranked_repeat_changes") == summary.get("compact_repeat_changes") == summary.get("query_vector_repeat_changes") == 0,
            "Independent whole-backend performance/quality/actual-work summary differs")
    for row in [*warmups, *rows]:
        require(all(Path(value["index_path"]).resolve() == Path(setup["db_path"]).resolve() for value in row["resident_reads"]),
                "Measured/warmup resident lookup used another database")
    acquired, closed, released = (read(attempt / name) for name in ("gpu-lease-acquired.json", "model-closed.json", "gpu-lease-released.json"))
    verify_chronology(start, setup, summary, acquired, closed, released, warmups, rows, plan, root=root)
    source_authority(root, plan["source_freeze"]["sha256"])
    require(all(file_sha(attempt / name) == value for name, value in artifacts.items()), "Native artifacts changed during audit")
    return {"schema": AUDIT_SCHEMA, "status": "passed", "checks_passed": True, "classification": CLASSIFICATION,
            **verified, "unique_queries": 14, "quality_query_mode_units": 28, "verified_source_windows": len(prepared["fixture"]["inventory"]),
            "source_freeze": plan["source_freeze"], "producer_freeze": chain, "producer_execution": execution,
            "source_input_barrier_sha256": file_sha(results / "input-audit.json"), "artifact_sha256": artifacts,
            "setup_forward_summary": counts, "paired_timing_diagnostics": paired_diagnostics(rows),
            "parity_policy": {"responses": "Only finite nonnegative float values in top-level timings and indexstats.elapsed_s may differ; timing keysets, all field presence, remaining typed metadata and compact bytes exact",
                              "provider_receipts": "Only elapsed_seconds/duration_seconds values in provider_receipts[*].original_query_forward_events, provider_receipts[*].extra_query_forward_events and duplicate top-level external_forward_events may differ; actual events independently validated and all field presence/identities strict",
                              "phase_timings": "Only the four candidate_capture.phase_timings clock values may differ; frozen independent verify_trace validates the exact four keys, finite nonnegative floats and full-backend bounds for every request",
                              "features_and_decisions": "Entire original/fast features, source/query/model bindings, v1/role receipts, ranks and actual executing work counters exact",
                              "measured_boundaries": "Factory construction and fast-clone setup plus actual provider/forward/source/SQLite work inside complete backend.search timer"},
            "native_warm_latency_measured": True, "fresh_codebase_evaluated": False, "automatic_default_change": False,
            "calls": {"model": 0, "index": 0, "retrieval": 0, "network": 0},
            "trust_boundary": "Frozen independent native source/packing/semantic/role helpers; only original conservative graph is shared, not provider/backend/model/NumPy"}


def forbidden_module(name):
    return old.forbidden_module(name) or any(name == prefix or name.startswith(prefix + ".") for prefix in
        ("experiments.provider_native.study", "experiments.provider_native.integration", "experiments.dependency_context.fast_provider",
         "experiments.dependency_context.provider_profile", "experiments.dependency_context.study"))


@contextmanager
def independent_cpu_only():
    require(not any(forbidden_module(name) for name in sys.modules), "Independent audit needs a clean CPU process without producer/model/NumPy imports")

    class Refuse(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if forbidden_module(fullname):
                raise RuntimeError("Independent provider audit forbids producer/model/NumPy: " + fullname)

    def deny(*args, **kwargs):
        raise RuntimeError("Independent provider audit forbids network")

    guard = Refuse()
    sys.meta_path.insert(0, guard)
    try:
        with patch.object(socket, "create_connection", deny), patch.object(socket, "getaddrinfo", deny), \
             patch.object(socket.socket, "connect", deny), patch.object(socket.socket, "connect_ex", deny), \
             patch.object(socket.socket, "sendto", deny):
            yield
    finally:
        sys.meta_path.remove(guard)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--check-inputs", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tokenizer-cache", type=Path)
    args = parser.parse_args()
    root, results = args.root.resolve(), args.results.resolve()
    target = args.output.resolve() if args.output else results / ("input-audit.json" if args.check_inputs else "independent-audit.json")
    failure = target.with_name(target.stem + "-failure.json")
    require(not target.exists() and not failure.exists() and target.parent.is_dir(), "Preserve independent attempts; use exclusive audit files")
    names = {}
    try:
        with independent_cpu_only():
            source_authority(root)
            # Parent/source-first barriers precede checkpoint, tokenizer and source reads.
            plan = read(results / "plan.json")
            legacy_plan, _ = parent_authority(root, plan)
            first_source_freeze(root, plan, legacy_plan)
            names = plan["source_freeze"]["sha256"]
            source_authority(root, names)
            if args.check_inputs:
                report, _ = audit_inputs(root, args.corpus.resolve(), results)
            else:
                counter = old.win.offline_encoding(args.tokenizer_cache or root / ".cache/tokenizers")
                report = audit_native(root, args.corpus.resolve(), results, counter)
        require(all(file_sha(root / name) == value for name, value in names.items()), "Source changed during independent audit")
        source_authority(root, names)
        report.update(auditor_execution_commit=old.git(root, "rev-parse", "HEAD").decode().strip(),
                      auditor_sha256={name: value for name, value in names.items() if name.startswith("scripts/audit_")
                                      or name.startswith("experiments/provider_native") or name == "tests/test_provider_native_audit.py"},
                      producer_provider_backend_model_or_numpy_imported=False)
        old.write_new(target, report)
        print(json.dumps({"status": "passed", "checks_passed": True, "output": str(target), "sha256": file_sha(target)}))
    except BaseException as error:
        old.write_new(failure, {"schema": AUDIT_SCHEMA, "status": "failed", "error_type": type(error).__name__,
                               "error": str(error), "resume_allowed": False, "source_sha256": names,
                               "calls": {"model": 0, "index": 0, "retrieval": 0, "network": 0}})
        raise


if __name__ == "__main__":
    main()
