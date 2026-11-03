#!/usr/bin/env python3
"""Prepare exact observed controls and model-free semantic feature requests.

This stage reconstructs original controls only. It runs no candidate selector,
model, retrieval or index, and never opens the reserved attrs holdout. Historical
retrieval query vectors were not retained: future captures must count fresh full
query singleton encoding as feature work, followed by within-query clauses.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
inputs = importlib.import_module("complementary_inputs")

SPEC = importlib.util.spec_from_file_location("semantic_study_control_helpers", ROOT / "scripts/replay_complementary_context.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)
require, canonical, sha, file_sha = inputs.require, inputs.canonical, inputs.sha, inputs.file_sha
SCHEMA = "semantic_study_inputs_v1"
FEATURE_SCHEMA = "semantic_study_feature_requests_v1"
LIMITS = {"max_results": 8, "source_budget": 12000, "token_budget": 4096}
POLICY = "fresh_singleton_full_query_then_within_query_batched_clauses; historical retrieval query vectors absent"
ARTIFACTS = ("operands.json", "feature-requests.json", "control-parity.json", "control-references.json",
             "negative-references.json", "input-manifest.json")
PLAN = "frozen-plan.json"
MODEL_BINDING = "results/semantic-model-development-2026-11-02/model-binding.json"
RETRIEVAL_BY_CONDITION = {"bm25": "bm25", "dense": "dense", "hybrid": "hybrid", "hybrid_k24_b1": "hybrid"}
MAPPING_SOURCES = ("results/language-quality-2026-10-13/frozen-config.json",
    "results/qwen-shortlist-development-2026-10-14/frozen-plan.json",
    "results/qwen-shortlist-httpx-2026-10-14/frozen-plan.json")


def case_id(case):
    return "case_" + sha(canonical(list(inputs.case_key(case))))


def identity(case):
    return {name: case[name] for name in ("subset", "dataset", "query_id", "condition")}


def _write_new(path, value):
    # Preserve insertion order: nested scope ordering is part of the exact tool
    # bytes. Canonical hashes are separate from this lossless JSON transport.
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
    with Path(path).open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def source_bindings(root=ROOT):
    root = Path(root).resolve()
    names = {path.relative_to(root).as_posix()
             for path in (root / "src/semantic_search").rglob("*.py")}
    for directory, patterns in (
            ("scripts", ("semantic*.py", "audit_semantic*.py")),
            ("tests", ("test_semantic*.py", "test_embedding*.py", "test_store_semantic_vectors.py")),
            ("docs", ("semantic*.md",))):
        for pattern in patterns:
            names.update(path.relative_to(root).as_posix() for path in (root / directory).glob(pattern))
    names.update(("scripts/capture_semantic_request.py", "scripts/complementary_inputs.py",
        "scripts/replay_complementary_context.py", "scripts/replay_windows.py",
        "scripts/replay_selection.py", "scripts/audit_selection.py", "scripts/audit_windows.py",
        "scripts/audit_source_allocation.py", "scripts/audit_flow_allocation.py",
        "scripts/audit_flow_native.py", "scripts/audit_complementary_context.py", "pyproject.toml"))
    return {name: file_sha(root / name) for name in sorted(names)}


def _control(case, name, modules):
    """Execute the actual control selector/packer and one coverage coalescer."""
    selector = getattr(modules["selection"], "ranked_context" if name == "legacy" else "definition_context")
    selected, eligible, origins = selector(case["pairs"], query=case["original"]["query"],
        max_results=8, include_dependencies=True, references=case["graph"],
        definition_keys=case["definition_keys"], ambiguous_symbols=case["ambiguous_symbols"])
    physical, context = modules["corpus"].physical_lines, modules["context"]
    before = replay.windows.pack(replay.ordered_template(case),
        [(item, score, *origins.get(item.id, ("retrieval", None))) for item, score in eligible], context, physical)
    response = before
    if name == "coverage_control":
        coalesce = importlib.import_module("semantic_search.context_backfill").coalesce_context
        response = coalesce(before, replay.windows.full_rows(case, selected, physical), **LIMITS)
    replay.verify_response(response, case, modules)
    return response, before


def _inactive_reason(case):
    require(case["condition"] in RETRIEVAL_BY_CONDITION, "Unknown historical retrieval condition")
    if RETRIEVAL_BY_CONDITION[case["condition"]] == "bm25":
        return "lexical_only"
    if case["reference_response"].get("match_status") == "insufficient_evidence":
        return "abstained"
    if not case["raw_rows"]:
        return "empty_raw_pool"
    if not case["reference_response"]["results"]:
        return "empty_reference"
    facets = importlib.import_module("semantic_search.semantic_features").expected_facets(case["query"])
    return None if facets else "ineligible_query"


def feature_requests(cases):
    """Deduplicate exact prompted queries and literal document wrappers only."""
    features = importlib.import_module("semantic_search.semantic_features")
    queries, documents, mappings = {}, {}, []
    for case in cases:
        reason = _inactive_reason(case)
        request = {"case_id": case["case_id"], "active": reason is None, "reason": reason,
                   "query_request_id": None, "window_documents": {}, "window_bindings": {}}
        if reason is None:
            facets = features.expected_facets(case["query"])
            query_id = "query_" + sha(canonical({"query": case["query"], "facets": facets,
                                                "query_prompt": features.QUERY_PROMPT}))
            query = {"request_id": query_id, "query": case["query"], "facets": facets, "case_ids": []}
            if query_id in queries:
                require({key: value for key, value in queries[query_id].items() if key != "case_ids"}
                        == {key: value for key, value in query.items() if key != "case_ids"}, "Query request SHA collision")
            else:
                queries[query_id] = query
            queries[query_id]["case_ids"].append(case["case_id"])
            request["query_request_id"] = query_id
            required = features._required_rows(case["reference_response"], case["raw_rows"])
            for identifier, row in required.items():
                text, binding = features.document_text(row), features.row_binding(row)
                digest = sha(text.encode())
                document_id = "doc_" + digest
                document = {"document_id": document_id, "document_sha256": digest, "text": text}
                require(binding["document_sha256"] == digest, "Document wrapper binding differs")
                require(document_id not in documents or documents[document_id] == document, "Document request SHA collision")
                documents[document_id] = document
                request["window_documents"][identifier] = document_id
                request["window_bindings"][identifier] = binding
        case["feature_request_id"], case["inactive_reason"] = request["query_request_id"], reason
        mappings.append(request)
    return {"schema": FEATURE_SCHEMA, "query_vector_policy": POLICY,
            "query_requests": [queries[key] for key in sorted(queries)],
            "document_requests": [documents[key] for key in sorted(documents)], "cases": mappings,
            "counts": {"active_cases": sum(row["active"] for row in mappings),
                "inactive_cases": sum(not row["active"] for row in mappings),
                "query_requests": len(queries), "document_requests": len(documents)},
            "claims": {"retrieval_query_vector_reuse": False, "new_candidate_outcomes_computed": 0}}


def build(cases, controls, negatives, parents, modules):
    """Build trusted operands after reproducing both controls for every case."""
    cases = sorted(cases, key=inputs.case_key)
    require(len({inputs.case_key(case) for case in cases}) == len(cases), "Duplicate observed case identity")
    require(all(case["condition"] in RETRIEVAL_BY_CONDITION for case in cases), "Unknown historical retrieval condition")
    expected = {(name, inputs.case_key(case)) for case in cases for name in inputs.CONTROLS}
    require(set(controls) == expected, "Complete two-control matrix required")
    operands, parity, references = [], [], []
    for case in cases:
        key, actual, packed, hashes = inputs.case_key(case), {}, {}, {}
        for name in inputs.CONTROLS:
            response, before = _control(case, name, modules)
            text = replay.verify_response(response, case, modules)
            saved = controls[name, key]
            view = inputs.source_view(response)
            require(view == saved["response"] and sha(text.encode()) == saved["reference"]["compact_sha256"],
                    "Exact original control differs: " + name + "/" + str(key))
            actual[name], packed[name] = view, before
            hashes[name] = {"source_view_sha256": sha(canonical(view)), "compact_sha256": sha(text.encode())}
            parity.append({"case_id": case_id(case), **identity(case), "selector": name,
                **hashes[name], "reference": deepcopy(saved["reference"])})
        refs = {name: deepcopy(controls[name, key]["reference"]) for name in inputs.CONTROLS}
        references.append({"case_id": case_id(case), **identity(case), "controls": refs})
        operands.append({"case_id": case_id(case), "key": list(key), **identity(case),
            "retrieval": RETRIEVAL_BY_CONDITION.get(case["condition"]),
            "query": case["original"]["query"], "kind": case["kind"], "multi_file": case["multi_file"],
            "qualified": case["qualified"], "groups": deepcopy(case["groups"]),
            "raw_rows": replay.windows.full_rows(case, case["pairs"], modules["corpus"].physical_lines),
            "precoalesce_rows": deepcopy(packed["coverage_control"]["results"]),
            "reference_response": actual["coverage_control"], "legacy_control": actual["legacy"],
            "definition_keys": deepcopy(case["definition_keys"]), "limits": dict(LIMITS),
            "control_references": refs, "control_hashes": hashes})
    requests = feature_requests(operands)
    return {"operands.json": {"schema": SCHEMA, "cases": operands}, "feature-requests.json": requests,
        "control-parity.json": {"exact_control_rows": len(parity), "coverage_coalesce_calls": len(cases),
            "new_candidate_outcomes_computed": 0, "cases": parity}, "control-references.json": references,
        "negative-references.json": deepcopy(negatives), "input-manifest.json": replay.input_manifest(cases, parents)}


def _historical_artifacts(root, parents):
    result = {}
    for parent in parents.values():
        for name, digest in parent["artifacts"].items():
            require(name not in result or result[name] == digest, "Conflicting historical artifact SHA")
            result[name] = digest
    if inputs.NATIVE in parents:
        binding = inputs.read(root / inputs.NATIVE / "plan.json")["binding"]
        result[binding["dataset_path"]] = binding["dataset_sha256"]
    return dict(sorted(result.items()))


def retrieval_evidence(root=ROOT):
    root = Path(root)
    language = inputs.read(root / MAPPING_SOURCES[0])
    require(set(language["conditions"]) == {"bm25", "dense", "hybrid"}, "Original language retrieval declarations changed")
    declarations = {MAPPING_SOURCES[0]: {name: name for name in language["conditions"]}}
    for name in MAPPING_SOURCES[1:]:
        matches = [item for item in inputs.read(root / name)["conditions"] if item["id"] == "hybrid_k24_b1"]
        require(len(matches) == 1 and matches[0]["retrieval"] == "hybrid"
                and matches[0]["shortlist"] == 24 and matches[0]["batch_size"] == 1,
                "Original scored K24 retrieval declaration changed")
        declarations[name] = matches[0]
    return {"mapping": dict(RETRIEVAL_BY_CONDITION), "source_declarations": declarations,
            "source_sha256": {name: file_sha(root / name) for name in MAPPING_SOURCES}}


def prepare(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    require(output.is_relative_to(root), "Prospective preparation must live inside the project")
    require(not output.exists(), "Use a new prospective preparation directory; never overwrite or resume")
    output.mkdir(parents=True, exist_ok=False)
    try:
        bound_sources = source_bindings(root)
        model_binding = {"path": MODEL_BINDING, "sha256": file_sha(root / MODEL_BINDING)}
        retrieval = retrieval_evidence(root)
        runtime, rank_bytes = replay.prior.runtime(root)
        with replay.prior.offline_cpu_guard(), replay.prior.cpu_modules(root, rank_bytes) as modules:
            cases, controls, negatives, parents = inputs.load(root)
            documents = build(cases, controls, negatives, parents, modules)
        for name in ARTIFACTS:
            _write_new(output / name, documents[name])
        require(source_bindings(root) == bound_sources, "Prospective source changed during control preparation")
        require(file_sha(root / MODEL_BINDING) == model_binding["sha256"], "Model binding changed during control preparation")
        require(retrieval_evidence(root) == retrieval, "Historical retrieval mapping changed during preparation")
        plan = {"schema": SCHEMA, "stage": "control_and_feature_request_preparation", "settings": dict(LIMITS),
            "counts": inputs.counts(cases), "runtime": runtime, "source_sha256": bound_sources,
            "model_binding": model_binding,
            "retrieval_conditions": {**retrieval, "case_counts": dict(sorted(Counter(case["condition"] for case in cases).items()))},
            "artifact_sha256": {name: file_sha(output / name) for name in ARTIFACTS},
            "historical_parents": parents, "historical_artifact_sha256": _historical_artifacts(root, parents),
            "barriers": {"exact_control_rows": len(controls), "referenced_negative_rows": len(negatives),
                "new_candidate_outcomes_computed": 0, "model_calls": 0, "retrieval_calls": 0, "index_calls": 0,
                "network_calls": 0},
            "feature_policy": POLICY, "exposure": "All393 observed cases/201 units; reserved attrs unopened",
            "prospective_requirement": "Commit unchanged source and all prepared bytes before model feature capture"}
        _write_new(output / PLAN, plan)
        return {**plan["counts"], **plan["barriers"], **documents["feature-requests.json"]["counts"]}
    except BaseException as error:
        _write_new(output / "prepare-failure.json", {"error_type": type(error).__name__, "error": str(error),
            "new_candidate_outcomes_computed": 0, "resume_allowed": False})
        raise


def load_prepared(output, *, verify=True):
    output = Path(output).resolve()
    require(not (output / "prepare-failure.json").exists(), "Failed preparation cannot be loaded or resumed")
    plan = inputs.read(output / PLAN)
    require(plan.get("schema") == SCHEMA and plan.get("settings") == LIMITS, "Prepared study schema/settings differ")
    require(set(plan.get("artifact_sha256", {})) == set(ARTIFACTS), "Complete prepared artifact matrix required")
    require(plan.get("model_binding", {}).get("path") == MODEL_BINDING, "Declared model binding path differs")
    if verify:
        for name, digest in plan["artifact_sha256"].items():
            require(file_sha(output / name) == digest, "Prepared artifact changed: " + name)
    values = {name: inputs.read(output / name) for name in ARTIFACTS}
    return {"plan": plan, "cases": values["operands.json"]["cases"],
            "feature_requests": values["feature-requests.json"], "controls": values["control-parity.json"],
            "control_references": values["control-references.json"], "negatives": values["negative-references.json"],
            "input_manifest": values["input-manifest.json"]}


def verify_frozen(prepared_path, freeze_commit, root=ROOT):
    root, output = Path(root).resolve(), Path(prepared_path).resolve()
    require(isinstance(freeze_commit, str) and len(freeze_commit) == 40
            and all(char in "0123456789abcdef" for char in freeze_commit),
            "Use an immutable full prospective freeze commit SHA")
    require(output.is_relative_to(root), "Frozen preparation escapes project")
    plan = load_prepared(output)["plan"]
    require(plan["source_sha256"] == source_bindings(root), "Frozen source/tests/protocol changed")
    result = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", freeze_commit, "HEAD"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    require(result.returncode == 0, "Prospective freeze must be an ancestor of execution HEAD")
    bound = dict(plan["source_sha256"])
    bound.update(plan["historical_artifact_sha256"])
    bound[plan["model_binding"]["path"]] = plan["model_binding"]["sha256"]
    bound.update(plan["retrieval_conditions"]["source_sha256"])
    bound.update({(output / name).relative_to(root).as_posix(): digest
                  for name, digest in plan["artifact_sha256"].items()})
    bound[(output / PLAN).relative_to(root).as_posix()] = file_sha(output / PLAN)
    for name, digest in bound.items():
        require((root / name).resolve().is_relative_to(root), "Frozen bound path escapes project")
        require(file_sha(root / name) == inputs.git_sha(root, "HEAD", name)
                == inputs.git_sha(root, freeze_commit, name) == digest,
                "Commit unchanged prospective bytes before model work: " + name)
    return {"commit": freeze_commit, "bound_blob_count": len(bound),
            "plan_sha256": file_sha(output / PLAN), "new_candidate_outcomes_before_freeze": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.output)))


if __name__ == "__main__":
    main()
