"""Invented CPU-only control operands and feature request preparation tests."""
from contextlib import nullcontext
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("semantic_study_inputs_test_subject", ROOT / "scripts/semantic_study_inputs.py")
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)
data, replay = study.inputs, study.replay


@pytest.fixture
def modules():
    return {name: importlib.import_module("semantic_search." + name) for name in ("context", "corpus", "selection")}


def make_case(modules, *, dataset="invented", query_id="invented", condition="dense", active=True):
    inventory = {}
    for number in range(10):
        text = f"def invented_{number}():\n    return {number}\n"
        path, start = "src/code.py", number * 3 + 1
        if number == 5:
            path, start, text = "src/shared.py", 1, "def outer():\n    def inner():\n        return 7\n    return inner()\n"
        elif number == 6:
            path, start, text = "src/shared.py", 2, "    def inner():\n        return 7\n"
        end = start + len(modules["corpus"].physical_lines(text)) - 1
        item = data.Window("window-" + str(number), path, "invented_" + str(number),
                           start, end, text, study.sha(text.encode()))
        inventory[item.id] = item
    items = list(inventory.values())
    return {"subset": "invented", "dataset": dataset, "query_id": query_id, "condition": condition,
        "kind": "present", "multi_file": True, "qualified": True, "groups": [[{
            "path": items[9].path, "symbol": items[9].symbol,
            "evidence_lines": [items[9].start_line + 1, items[9].end_line],
            "evidence_sha256": study.sha(items[9].text.splitlines(keepends=True)[1].encode())}]],
        "inventory": inventory, "pairs": [(item, float(10-number)) for number, item in enumerate(items)],
        "definition_keys": {item.id: (item.path, item.symbol, item.start_line, item.end_line) for item in items},
        "graph": data.FrozenGraph(inventory, []), "ambiguous_symbols": set(),
        "original": {"query": "load alpha payload and render beta template" if active else "find ordinary implementation",
            "scope": modules["context"].indexed_scope("src"), "match_status": "candidates",
            "results": [{"qwen_score": None}]}}


def controls_for(cases, modules):
    result = {}
    for case in cases:
        for number, name in enumerate(data.CONTROLS, 1):
            value = replay.evaluate_control(case, name, modules)
            payload = replay.verify_response(value, case, modules)
            result[name, data.case_key(case)] = {"response": value,
                "reference": {"path": "invented/controls.jsonl.gz", "line_number": number,
                    "record_sha256": study.sha(study.canonical({"response": value})),
                    "response_sha256": study.sha(study.canonical(value)),
                    "compact_sha256": study.sha(payload.encode())}}
    return result


def bundle(cases, modules, *, negatives=None):
    return study.build(cases, controls_for(cases, modules), negatives or [], {}, modules)


def test_both_actual_controls_match_original_helpers_and_coverage_runs_once(modules, monkeypatch):
    cases = [make_case(modules)]
    saved = controls_for(cases, modules)
    coalescer = importlib.import_module("semantic_search.context_backfill")
    actual, calls = coalescer.coalesce_context, []

    def record(*args, **kwargs):
        calls.append(deepcopy(args[0]))
        return actual(*args, **kwargs)

    monkeypatch.setattr(coalescer, "coalesce_context", record)
    artifacts = study.build(cases, saved, [], {}, modules)
    operand = artifacts["operands.json"]["cases"][0]
    assert len(calls) == 1
    assert operand["precoalesce_rows"] == calls[0]["results"]
    assert operand["reference_response"] == saved["coverage_control", data.case_key(cases[0])]["response"]
    assert operand["legacy_control"] == saved["legacy", data.case_key(cases[0])]["response"]
    assert operand["reference_response"]["_selection_backfill"]["coalesced_ids"] == ["window-6"]
    assert any(row["id"] == "window-6" for row in operand["precoalesce_rows"])
    assert all(row["id"] != "window-6" for row in operand["reference_response"]["results"])
    assert operand["limits"] == study.LIMITS
    assert artifacts["control-parity.json"]["exact_control_rows"] == 2
    assert artifacts["control-parity.json"]["new_candidate_outcomes_computed"] == 0


def test_build_does_not_call_any_candidate_selector(modules, monkeypatch):
    case = make_case(modules)
    semantic = importlib.import_module("semantic_search.semantic_context")

    def forbidden(*args, **kwargs):
        pytest.fail("Preparation must never execute a candidate selector")

    monkeypatch.setattr(semantic, "semantic_final_context", forbidden)
    assert bundle([case], modules)["feature-requests.json"]["counts"]["active_cases"] == 1


@pytest.mark.parametrize("name", data.CONTROLS)
@pytest.mark.parametrize("field", ["source", "compact_hash"])
def test_mismatched_original_control_is_a_fatal_barrier(modules, name, field):
    case = make_case(modules)
    saved = controls_for([case], modules)
    value = saved[name, data.case_key(case)]
    if field == "source":
        value["response"]["results"][0]["source"] = "forged\n"
    else:
        value["reference"]["compact_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Exact original control"):
        study.build([case], saved, [], {}, modules)


def test_duplicate_cases_or_incomplete_control_matrix_fail(modules):
    case = make_case(modules)
    saved = controls_for([case], modules)
    with pytest.raises(ValueError, match="Duplicate"):
        study.build([case, case], saved, [], {}, modules)
    saved.pop(("legacy", data.case_key(case)))
    with pytest.raises(ValueError, match="Complete two-control"):
        study.build([case], saved, [], {}, modules)


def test_all_negative_references_and_historical_control_references_are_preserved(modules):
    case = make_case(modules)
    negatives = [{"condition": "old-negative", "path": "old/results.jsonl.gz", "line_number": 7,
                  "record_sha256": "a" * 64, "response_sha256": "b" * 64, "compact_sha256": "c" * 64}]
    saved = controls_for([case], modules)
    values = study.build([case], saved, negatives, {}, modules)
    assert values["negative-references.json"] == negatives
    assert values["control-references.json"][0]["controls"] == {
        name: saved[name, data.case_key(case)]["reference"] for name in data.CONTROLS}
    negatives[0]["line_number"] = 99
    assert values["negative-references.json"][0]["line_number"] == 7


def test_feature_inventory_is_label_free_and_full_query_is_explicit_fresh_work(modules):
    values = bundle([make_case(modules)], modules)
    requests = values["feature-requests.json"]
    assert requests["query_vector_policy"] == study.POLICY
    query = requests["query_requests"][0]
    assert query["facets"] == [query["query"], "load alpha payload", "render beta template"]
    assert requests["claims"]["retrieval_query_vector_reuse"] is False
    assert "groups" not in json.dumps(requests) and "qualified" not in json.dumps(requests)
    assert requests["counts"] == {"active_cases": 1, "inactive_cases": 0,
                                  "query_requests": 1, "document_requests": 10}


def test_same_query_across_conditions_deduplicates_but_bm25_is_inactive(modules):
    cases = [make_case(modules, condition=mode) for mode in ("dense", "hybrid", "bm25")]
    requests = bundle(cases, modules)["feature-requests.json"]
    assert len(requests["query_requests"]) == 1 and len(requests["document_requests"]) == 10
    inactive = [value for value in requests["cases"] if not value["active"]]
    assert len(inactive) == 1 and inactive[0]["reason"] == "lexical_only"
    assert inactive[0]["window_documents"] == inactive[0]["window_bindings"] == {}
    assert inactive[0]["query_request_id"] is None


def test_unknown_retrieval_condition_fails_closed(modules):
    case = make_case(modules, condition="lexical_h2o_unknown_alias")
    with pytest.raises(ValueError, match="Unknown historical retrieval"):
        bundle([case], modules)


def test_scored_historical_condition_explicitly_maps_to_hybrid(modules):
    values = bundle([make_case(modules, condition="hybrid_k24_b1")], modules)
    case = values["operands.json"]["cases"][0]
    assert case["condition"] == "hybrid_k24_b1" and case["retrieval"] == "hybrid"
    assert values["feature-requests.json"]["cases"][0]["active"]


def test_ineligible_queries_have_no_feature_work(modules):
    values = bundle([make_case(modules, active=False)], modules)
    assert values["feature-requests.json"]["query_requests"] == []
    assert values["feature-requests.json"]["document_requests"] == []
    assert values["feature-requests.json"]["cases"][0]["reason"] == "ineligible_query"


@pytest.mark.parametrize("reason", ["empty_raw_pool", "empty_reference", "abstained"])
def test_inactive_source_pools_need_no_feature_vectors(modules, reason):
    values = bundle([make_case(modules)], modules)
    case = values["operands.json"]["cases"][0]
    if reason == "empty_raw_pool":
        case["raw_rows"] = []
    elif reason == "empty_reference":
        case["reference_response"]["results"] = []
    else:
        case["reference_response"]["match_status"] = "insufficient_evidence"
    request = study.feature_requests([case])
    assert request["cases"][0]["reason"] == reason and request["query_requests"] == []


def test_window_ids_are_case_scoped_and_document_dedup_uses_exact_wrapper(modules):
    first = bundle([make_case(modules, dataset="first")], modules)["operands.json"]["cases"][0]
    second = deepcopy(first)
    second["case_id"], second["dataset"] = "case_other", "other"
    # Same ID across repositories does not identify the document vector.
    for pool in (second["raw_rows"], second["reference_response"]["results"]):
        for row in pool:
            if row["id"] == "window-0":
                row["source"] = row["source"].replace("return 0", "return 999")
                row["source_hash"] = row["content_hash"] = study.sha(row["source"].encode())
    requests = study.feature_requests([first, second])
    a, b = requests["cases"]
    assert a["window_documents"]["window-0"] != b["window_documents"]["window-0"]
    assert len(requests["document_requests"]) == 11
    assert a["window_documents"]["window-1"] == b["window_documents"]["window-1"]


def test_exact_wrappers_can_share_vectors_across_different_window_ids_and_lines(modules):
    values = bundle([make_case(modules)], modules)
    case = values["operands.json"]["cases"][0]
    other = deepcopy(case["raw_rows"][0])
    other.update(id="different-location", start_line=100, end_line=101, window_end_line=101)
    case["raw_rows"].append(other)
    requests = study.feature_requests([case])
    mapping = requests["cases"][0]
    assert mapping["window_documents"]["different-location"] == mapping["window_documents"]["window-0"]
    assert mapping["window_bindings"]["different-location"]["start_line"] == 100
    assert len(requests["document_requests"]) == 10


def test_clipped_reference_requires_full_raw_vector_only(modules):
    case = bundle([make_case(modules)], modules)["operands.json"]["cases"][0]
    target = case["reference_response"]["results"][0]
    full_id = target["id"]
    target["source"] = target["source"].splitlines(keepends=True)[0]
    target.update(end_line=target["start_line"], truncated=True,
                  source_hash=study.sha(target["source"].encode()))
    mapping = study.feature_requests([case])["cases"][0]
    assert mapping["window_bindings"][full_id]["end_line"] > target["end_line"]


def test_stable_ids_and_artifacts_do_not_depend_on_case_input_order(modules):
    cases = [make_case(modules, dataset="second"), make_case(modules, dataset="first")]
    first, second = bundle(cases, modules), bundle(list(reversed(cases)), modules)
    for name in study.ARTIFACTS:
        assert first[name] == second[name]
    assert [query["request_id"] for query in first["feature-requests.json"]["query_requests"]] == sorted(
        query["request_id"] for query in first["feature-requests.json"]["query_requests"])
    assert [doc["document_id"] for doc in first["feature-requests.json"]["document_requests"]] == sorted(
        doc["document_id"] for doc in first["feature-requests.json"]["document_requests"])


def test_lossless_json_transport_preserves_original_compact_bytes(modules, tmp_path):
    artifacts = bundle([make_case(modules)], modules)
    study._write_new(tmp_path / "operands.json", artifacts["operands.json"])
    case = json.loads((tmp_path / "operands.json").read_bytes())["cases"][0]
    for name, response in (("legacy", case["legacy_control"]), ("coverage_control", case["reference_response"])):
        text = modules["context"].serialize_context(modules["context"].compact_result(response))
        assert study.sha(text.encode()) == case["control_hashes"][name]["compact_sha256"]


def test_transport_is_compact_lossless_json_with_one_final_newline(tmp_path):
    value = {"z": {"second": "café", "first": [1, True]}, "a": "line\nvalue"}
    path = tmp_path / "compact.json"
    study._write_new(path, value)
    raw = path.read_bytes()
    assert raw == b'{"z":{"second":"caf\xc3\xa9","first":[1,true]},"a":"line\\nvalue"}\n'
    restored = json.loads(raw)
    assert restored == value and list(restored) == ["z", "a"]
    assert list(restored["z"]) == ["second", "first"]


def write_prepared(root, modules):
    output = root / "prepared"
    output.mkdir()
    artifacts = bundle([make_case(modules)], modules)
    for name, value in artifacts.items():
        study._write_new(output / name, value)
    model = root / study.MODEL_BINDING
    model.parent.mkdir(parents=True)
    model.write_text("{}\n")
    plan = {"schema": study.SCHEMA, "settings": study.LIMITS,
        "artifact_sha256": {name: study.file_sha(output / name) for name in study.ARTIFACTS},
        "source_sha256": {}, "historical_artifact_sha256": {},
        "retrieval_conditions": {"source_sha256": {}},
        "model_binding": {"path": study.MODEL_BINDING, "sha256": study.file_sha(model)}}
    study._write_new(output / study.PLAN, plan)
    return output, plan


def test_load_prepared_verifies_every_artifact_and_refuses_partial_attempt(modules, tmp_path):
    output, _ = write_prepared(tmp_path, modules)
    loaded = study.load_prepared(output)
    assert len(loaded["cases"]) == 1 and loaded["controls"]["exact_control_rows"] == 2
    (output / "feature-requests.json").write_text("{}\n")
    with pytest.raises(ValueError, match="artifact changed"):
        study.load_prepared(output)
    (output / "prepare-failure.json").write_text("{}\n")
    with pytest.raises(ValueError, match="Failed preparation"):
        study.load_prepared(output, verify=False)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_frozen_barrier_requires_precommitted_unchanged_artifacts_and_model_binding(modules, tmp_path, monkeypatch):
    output, _ = write_prepared(tmp_path, modules)
    monkeypatch.setattr(study, "source_bindings", lambda root: {})
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "freeze invented inputs")
    freeze = git(tmp_path, "rev-parse", "HEAD")
    assert study.verify_frozen(output, freeze, tmp_path)["bound_blob_count"] == 8
    (tmp_path / study.MODEL_BINDING).write_text('{"changed":true}\n')
    with pytest.raises(ValueError, match="unchanged prospective"):
        study.verify_frozen(output, freeze, tmp_path)


def test_frozen_barrier_refuses_uncommitted_preparation(modules, tmp_path, monkeypatch):
    output, _ = write_prepared(tmp_path, modules)
    monkeypatch.setattr(study, "source_bindings", lambda root: {})
    git(tmp_path, "init", "-q")
    (tmp_path / "existing").write_text("original\n")
    git(tmp_path, "add", "existing")
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "before prep")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        study.verify_frozen(output, git(tmp_path, "rev-parse", "HEAD"), tmp_path)


def test_frozen_barrier_refuses_mutable_commit_alias(tmp_path):
    with pytest.raises(ValueError, match="immutable full"):
        study.verify_frozen(tmp_path / "prepared", "HEAD", tmp_path)


def test_prepare_refuses_existing_directory_before_loading_any_inputs(tmp_path, monkeypatch):
    existing = tmp_path / "prepared"
    existing.mkdir()
    monkeypatch.setattr(study.inputs, "load", lambda *args: pytest.fail("Existing outputs must fail before load"))
    with pytest.raises(ValueError, match="new prospective"):
        study.prepare(tmp_path, existing)


def test_prepare_failure_is_exclusive_and_retained_before_any_candidate(modules, tmp_path, monkeypatch):
    output = tmp_path / "prepared"
    model = tmp_path / study.MODEL_BINDING
    model.parent.mkdir(parents=True)
    model.write_text("{}\n")
    monkeypatch.setattr(study, "source_bindings", lambda root: {})
    monkeypatch.setattr(study, "retrieval_evidence", lambda root: {})
    monkeypatch.setattr(study.replay.prior, "runtime", lambda root: ({}, b"invented"))
    monkeypatch.setattr(study.replay.prior, "offline_cpu_guard", nullcontext)
    monkeypatch.setattr(study.replay.prior, "cpu_modules", lambda *args: nullcontext(modules))

    def fail(*args):
        raise RuntimeError("invented historical barrier failure")

    monkeypatch.setattr(study.inputs, "load", fail)
    with pytest.raises(RuntimeError, match="historical barrier"):
        study.prepare(tmp_path, output)
    failure = json.loads((output / "prepare-failure.json").read_bytes())
    assert failure["new_candidate_outcomes_computed"] == 0 and failure["resume_allowed"] is False
    with pytest.raises(ValueError, match="new prospective"):
        study.prepare(tmp_path, output)


def test_original_retrieval_mapping_requires_exact_declarations(tmp_path):
    for name in study.MAPPING_SOURCES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {"conditions": ["bm25", "dense", "hybrid"]} if name == study.MAPPING_SOURCES[0] else {
            "conditions": [{"id": "hybrid_k24_b1", "retrieval": "hybrid", "shortlist": 24, "batch_size": 1}]}
        path.write_text(json.dumps(value))
    assert study.retrieval_evidence(tmp_path)["mapping"] == study.RETRIEVAL_BY_CONDITION
    path = tmp_path / study.MAPPING_SOURCES[1]
    path.write_text(json.dumps({"conditions": [{"id": "hybrid_k24_b1", "retrieval": "bm25", "shortlist": 24, "batch_size": 1}]}))
    with pytest.raises(ValueError, match="retrieval declaration"):
        study.retrieval_evidence(tmp_path)
