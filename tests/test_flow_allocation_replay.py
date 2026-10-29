"""Invented-input replay/provenance checks; never run observed candidates."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value
replay = module("flow_replay", "scripts/replay_flow_allocation.py")
invented = module("flow_invented_source_cases", "tests/test_source_allocation_replay.py")
case, response, saved_rows = invented.case, invented.response, invented.saved_rows


def test_report_profile_only_adds_one_new_arm_and_retains_four_historical_negatives():
    original, candidates = replay.windows.CONDITIONS, replay.windows.CANDIDATES
    with replay.report_profile():
        assert tuple(replay.windows.CONDITIONS) == (*replay.CONTROLS, *replay.NEGATIVES, "shared_flow")
        assert replay.windows.CANDIDATES == ("shared_flow",)
    assert replay.windows.CONDITIONS is original and replay.windows.CANDIDATES is candidates


def test_report_configuration_restores_after_error():
    original = replay.windows.CONDITIONS
    with pytest.raises(RuntimeError), replay.report_profile():
        raise RuntimeError("stop")
    assert replay.windows.CONDITIONS is original


def test_graph_pointer_reuses_complete_frozen_inventory_without_copying():
    name = replay.SOURCE_PARENT + "/graph-fixtures.json"
    pointer = replay.graph_pointer({"artifacts": {name: "frozen"}})
    assert pointer == {"schema": "source_flow_graph_pointer_v1", "path": name, "sha256": "frozen"}
    assert "fixtures" not in pointer


def test_source_negative_reader_keeps_original_hashes_and_base_receipt(tmp_path):
    folder = tmp_path / replay.SOURCE_PARENT
    folder.mkdir(parents=True)
    records = []
    with gzip.open(folder / "replay.jsonl.gz", "wt", encoding="utf-8") as stream:
        for number in range(365):
            for name in replay.SOURCE_NEGATIVES:
                row = {**replay.identity(case(str(number))), "selector": name, "response": {"results": []},
                    "compact_payload": '{"results":[]}', "compact_sha256": replay.sha(b'{"results":[]}'),
                    "kind": "present", "multi_file": True, "evidence_groups": [], "evidence_ranks": [],
                    "source_allocation_receipt": {"invented": number}, "stages": {"raw": "never copied"}}
                records.append(row)
                stream.write(json.dumps(row) + "\n")
    values = replay.source_negative_rows(tmp_path)
    assert len(values) == 730
    key = ("source_graph", *replay.identity_tuple(case("1")))
    assert values[key]["base_plan"] == {"invented": 1}
    assert values[key]["reference"]["line_number"] == 3
    assert values[key]["reference"]["record_sha256"] == replay.sha(replay.canonical(records[2]))
    assert "stages" not in values[key]
    assert replay.base.NEGATIVES == ("aspect_fill", "aspect_balanced")


def test_negative_manifest_has_original_labels_and_exact_membership():
    cases = [case("one"), case("two")]
    values = saved_rows(cases, replay.SOURCE_NEGATIVES)
    document = replay.source_negative_manifest(cases, values)
    assert document["path"] == replay.SOURCE_PARENT + "/replay.jsonl.gz"
    assert all(tuple(row["negatives"]) == replay.SOURCE_NEGATIVES for row in document["cases"])
    assert "response" not in document["cases"][0]["negatives"]["source_graph"]
    del values[("source_graph", *replay.identity_tuple(cases[0]))]
    with pytest.raises(ValueError, match="exactly"):
        replay.source_negative_manifest(cases, values)


@pytest.mark.parametrize("loss", [False, True])
def test_summary_keeps_four_negative_arms_but_only_new_arm_can_qualify(loss):
    cases = [case("gain"), case("partial")]
    controls, negatives = saved_rows(cases, replay.CONTROLS), saved_rows(cases, replay.NEGATIVES)
    untouched = deepcopy(negatives)
    outcomes = []
    for value in cases:
        references = {name: controls[(name, *replay.identity_tuple(value))]["reference"] for name in replay.CONTROLS}
        responses = {(name, *replay.identity_tuple(value)): controls[(name, *replay.identity_tuple(value))]["response"] for name in replay.CONTROLS}
        paths = ("a.py", "b.py") if value["query_id"] == "gain" else (("b.py",) if loss else ("a.py",))
        outcomes.append(replay.previous.reported_row(value, "shared_flow", {"response": response(paths)}, responses, references))
    result = replay.summarize(outcomes, controls, negatives, cases)
    assert result["study"] == "source_flow" and tuple(result["qualification"]) == ("shared_flow",)
    assert result["qualification"]["shared_flow"]["qualified"] is not loss
    assert result["referenced_negative_rows"] == 8 and result["stored_new_candidate_rows"] == 2
    assert result["historical_negative_conditions"] == list(replay.NEGATIVES)
    assert set(result["all_changes"]) == set(replay.CONDITIONS) and negatives == untouched


def test_old_coverage_complete_gain_cannot_qualify_new_arm():
    value = case()
    controls = saved_rows([value], replay.CONTROLS, paths=("a.py", "b.py"))
    responses = {(name, *replay.identity_tuple(value)): controls[(name, *replay.identity_tuple(value))]["response"] for name in replay.CONTROLS}
    outcome = replay.previous.reported_row(value, "shared_flow", {"response": response(("a.py", "b.py"))}, responses, {})
    result = replay.summarize([outcome], controls, saved_rows([value], replay.NEGATIVES), [value])
    assert not result["qualification"]["shared_flow"]["qualified"]


def test_real_synthetic_evaluator_captures_flow_base_and_guard_once_without_public_receipts():
    value, modules = invented.synthetic_source_case()
    row = replay.evaluate_candidate(value, "shared_flow", modules)
    assert row["planner_calls"] == row["base_planner_calls"] == row["reference_calls"] == row["guard_calls"] == 1
    assert row["base_allocation_receipt"] == row["flow_allocation_receipt"]["base_plan"]
    assert row["selection_plan"] == row["guard_plan"] == row["flow_allocation_receipt"]["guard_plan"]
    assert row["guard_plan"]["producer_schema"] == "shared_flow_allocation_v1"
    assert row["flow_allocation_receipt"]["admitted_ids"] == ["fn9"]
    assert row["source_guard_receipt"]["accepted"] is True
    assert all("receipt" not in key and "guard_plan" not in key for key in row["response"])


def test_inactive_flow_returns_exact_original_coverage_reference():
    value, modules = invented.synthetic_source_case()
    value["original"]["query"] = "find one implementation"
    row = replay.evaluate_candidate(value, "shared_flow", modules)
    assert row["flow_allocation_receipt"]["termination"] == "inactive_base_plan"
    assert row["response"] == row["reference_response"]
    assert row["source_guard_receipt"]["fallback_reason"] == "inactive_plan"


@pytest.mark.parametrize("tamper", ["outer_base", "guard_plan", "guard_decision", "extra_base"])
def test_capture_rejects_forged_plans_and_repeated_base_calls(monkeypatch, tamper):
    from semantic_search import flow_allocation as flow, source_allocation as source
    value, modules = invented.synthetic_source_case()
    original = flow.flow_allocation_plan
    if tamper == "guard_decision":
        old_guard = source.guard_source_plan
        def guard(*args):
            chosen, receipt = old_guard(*args)
            receipt["accepted"] = not receipt["accepted"]
            return chosen, receipt
        monkeypatch.setattr(source, "guard_source_plan", guard)
    else:
        def planner(*args, **kwargs):
            result = original(*args, **kwargs)
            if tamper == "outer_base":
                result[3]["base_plan"]["accepted"] = not result[3]["base_plan"]["accepted"]
            elif tamper == "guard_plan":
                result[3]["guard_plan"]["producer_schema"] = "forged"
            else:
                original(*args, **kwargs)
            return result
        monkeypatch.setattr(flow, "flow_allocation_plan", planner)
    with pytest.raises(ValueError, match="projection differs|each run exactly once|guard receipt"):
        replay.evaluate_candidate(value, "shared_flow", modules)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def prospective(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Invented flow study")
    folder = tmp_path / "results/prospective"
    folder.mkdir(parents=True)
    (tmp_path / "source.txt").write_text("fixed source\n")
    bindings = {"source.txt": replay.file_sha(tmp_path / "source.txt")}
    monkeypatch.setattr(replay, "own_bindings", lambda root: bindings)
    plan = {"study": "source_flow", "conditions": replay.CONDITIONS, "settings": replay.SETTINGS,
        "producer_sha256": bindings, "input_sha256": {}, "historical_parent": {"artifacts": {}},
        "negative_parent": {"artifacts": {}}, "source_parent": {"artifacts": {}}, "artifacts": {}}
    for name in replay.FROZEN_FILES:
        replay.write_new(folder / name, plan if name == "frozen-plan.json" else {})
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Invented prospective flow freeze")
    return folder, plan


def test_new_prospective_chain_binds_extra_parent_references(tmp_path, monkeypatch):
    folder, plan = prospective(tmp_path, monkeypatch)
    proof = replay.validate_run(tmp_path, folder, plan, datetime.now(timezone.utc).isoformat())
    assert proof["commit"] == git(tmp_path, "rev-parse", "HEAD")
    assert proof["bound_blob_count"] == len(replay.FROZEN_FILES) + 1
    assert "source-negative-references.json" in replay.FROZEN_FILES


@pytest.mark.parametrize("name", ["source.txt", "results/prospective/source-negative-references.json", "results/prospective/graph-fixtures.json"])
def test_changed_uncommitted_source_or_pointer_fails_before_outcomes(tmp_path, monkeypatch, name):
    folder, plan = prospective(tmp_path, monkeypatch)
    with (tmp_path / name).open("a") as stream:
        stream.write("changed")
    with pytest.raises(ValueError, match="unchanged|changed after freeze"):
        replay.validate_run(tmp_path, folder, plan, datetime.now(timezone.utc).isoformat())


@pytest.mark.parametrize("name", replay.ATTEMPT_FILES)
def test_every_attempt_artifact_blocks_resume(tmp_path, monkeypatch, name):
    folder, plan = prospective(tmp_path, monkeypatch)
    (folder / name).write_text("preserved")
    with pytest.raises(ValueError, match="Never overwrite or resume"):
        replay.validate_run(tmp_path, folder, plan, datetime.now(timezone.utc).isoformat())


def test_failure_claim_is_preserved_with_zero_completed_rows(tmp_path, monkeypatch):
    folder, plan = prospective(tmp_path, monkeypatch)
    monkeypatch.setattr(replay.prior, "runtime", lambda root: (_ for _ in ()).throw(RuntimeError("invented interruption")))
    with pytest.raises(RuntimeError, match="interruption"):
        replay.run(tmp_path, tmp_path / "unused", folder)
    assert replay.read(folder / "failure.json")["progress"]["new_variant_rows"] == 0
    before = (folder / "failure.json").read_bytes()
    with pytest.raises(ValueError, match="Never overwrite or resume"):
        replay.run(tmp_path, tmp_path / "unused", folder)
    assert (folder / "failure.json").read_bytes() == before


def test_interrupted_second_case_retains_first_completed_trace_and_attempt_counters(tmp_path, monkeypatch):
    cases = [case("first"), case("second")]
    controls = saved_rows(cases, replay.CONTROLS)
    negatives = saved_rows(cases, replay.base.NEGATIVES)
    source_negatives = saved_rows(cases, replay.SOURCE_NEGATIVES)
    for value in cases:
        source_negatives[("source_graph", *replay.identity_tuple(value))]["base_plan"] = {"invented": "original"}
    old_folder, source_folder = tmp_path / replay.base.PARENT, tmp_path / replay.SOURCE_PARENT
    old_folder.mkdir(parents=True)
    source_folder.mkdir(parents=True)
    replay.write_new(old_folder / "source-fixtures.json", {"fixtures": {}})
    replay.write_new(source_folder / "graph-fixtures.json", {"graph": "frozen"})
    parent = {"artifacts": {replay.base.PARENT + "/source-fixtures.json": "fixture"}, "proof": {}}
    negative_parent = {"artifacts": {}, "proof": {}}
    source_parent = {"artifacts": {replay.SOURCE_PARENT + "/graph-fixtures.json": "graph"}, "proof": {}}
    baseline, coverage = [{"legacy": "exact"}], [{"coverage": "exact"}]
    folder = tmp_path / "results/interrupted"
    folder.mkdir(parents=True)
    plan = {"runtime": {"cpu": "fixed"}, "historical_parent": parent, "negative_parent": negative_parent,
        "source_parent": source_parent, "input_sha256": {}, "counts": replay.windows.counts(cases), "source_sha256": {},
        "baseline_controls_sha256": replay.sha(replay.canonical(baseline)), "prior_coverage_controls_sha256": replay.sha(replay.canonical(coverage))}
    replay.write_new(folder / "frozen-plan.json", plan)
    digest = replay.file_sha(folder / "frozen-plan.json")
    documents = {"source-fixtures.json": replay.previous.fixture_pointer(parent),
        "control-references.json": replay.previous.reference_manifest(cases, controls),
        "negative-references.json": replay.base.negative_manifest(cases, negatives),
        "source-negative-references.json": replay.source_negative_manifest(cases, source_negatives),
        "graph-fixtures.json": replay.graph_pointer(source_parent),
        "baseline-gate.json": {"cases": baseline, "plan_sha256": digest},
        "prior-coverage-gate.json": {"cases": coverage, "plan_sha256": digest}}
    for name, value in documents.items():
        replay.write_new(folder / name, value)
    monkeypatch.setattr(replay, "validate_run", lambda *args: {"commit": "invented-freeze"})
    monkeypatch.setattr(replay.subprocess, "check_output", lambda *args, **kwargs: "invented-execution\n")
    monkeypatch.setattr(replay.prior, "runtime", lambda root: ({"cpu": "fixed"}, b"ranks"))
    monkeypatch.setattr(replay.prior, "offline_cpu_guard", nullcontext)
    monkeypatch.setattr(replay.prior, "cpu_modules", lambda *args: nullcontext({}))
    monkeypatch.setattr(replay.previous, "historical_parent", lambda root: parent)
    monkeypatch.setattr(replay.base, "historical_negatives", lambda root: negative_parent)
    monkeypatch.setattr(replay, "source_parent", lambda root: source_parent)
    monkeypatch.setattr(replay.previous, "control_rows", lambda root: controls)
    monkeypatch.setattr(replay.base, "negative_rows", lambda root: negatives)
    monkeypatch.setattr(replay, "source_negative_rows", lambda root: source_negatives)
    monkeypatch.setattr(replay.windows, "load_inputs", lambda *args: (cases, {}, {}, {}))
    monkeypatch.setattr(replay.windows, "fixture_snapshot", lambda *args: {"fixtures": {}})
    monkeypatch.setattr(replay.base, "graph_snapshot", lambda *args: {"graph": "frozen"})
    monkeypatch.setattr(replay.windows, "control_barrier", lambda *args: baseline)
    monkeypatch.setattr(replay.windows, "coverage_barrier", lambda *args: coverage)
    responses = {(name, *replay.identity_tuple(value)): controls[(name, *replay.identity_tuple(value))]["response"]
        for value in cases for name in replay.CONTROLS}
    monkeypatch.setattr(replay.previous, "verify_parent_controls", lambda *args: (responses, [{"control": "exact"}]))
    calls = []
    def evaluate(value, name, modules, **kwargs):
        calls.append(value["query_id"])
        if len(calls) == 2:
            assert (folder / "replay.jsonl.gz").stat().st_size > 10
            raise RuntimeError("invented second case interrupted")
        return {"response": response(("a.py", "b.py")), "base_allocation_receipt": {"invented": "original"}}
    monkeypatch.setattr(replay, "evaluate_candidate", evaluate)
    with pytest.raises(RuntimeError, match="second case interrupted"):
        replay.run(tmp_path, tmp_path / "unused", folder)
    with gzip.open(folder / "replay.jsonl.gz", "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    assert len(rows) == 1 and rows[0]["query_id"] == "first" and rows[0]["selector"] == "shared_flow"
    failure = replay.read(folder / "failure.json")
    assert failure["progress"]["attempted_new_variant_rows"] == 2 and failure["progress"]["new_variant_rows"] == 1
    assert failure["progress"]["current_case"]["query_id"] == "second"
    assert failure["resume_allowed"] is False
    assert replay.read(folder / "control-gate.json")["new_variant_rows_before_gate"] == 0
    assert not (folder / "summary.json").exists() and not (folder / "findings.md").exists()


def test_cli_help_is_available_without_models():
    process = subprocess.run([sys.executable, str(ROOT / "scripts/replay_flow_allocation.py"), "--help"], text=True, capture_output=True)
    assert process.returncode == 0 and "--freeze" in process.stdout and "--run" in process.stdout
