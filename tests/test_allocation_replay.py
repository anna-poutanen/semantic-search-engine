"""Synthetic protocol checks; no observed variants, models or fresh labels."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("allocation_replay", ROOT / "scripts/replay_allocation.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def case(identifier="q"):
    return {"subset": "synthetic", "dataset": "toy", "query_id": identifier, "condition": "dense",
            "kind": "present", "multi_file": True,
            "groups": [[{"path": "a.py", "evidence_lines": [1, 1]}],
                       [{"path": "b.py", "evidence_lines": [1, 1]}]]}


def response(paths=("a.py",)):
    return {"results": [{"id": path, "path": path, "start_line": 1, "end_line": 1, "source": "pass\n"}
                        for path in paths], "total_source_chars": len(paths) * 5,
            "match_status": "candidates", "token_usage": {"tokens": 100, "truncated": False}}


def saved_controls(cases, *, paths=("a.py",)):
    return {(name, *replay.identity_tuple(value)): {"response": response(paths),
            "reference": {"line_number": number, "record_sha256": "record", "response_sha256": "response", "compact_sha256": "compact"}}
            for number, value in enumerate(cases, 1) for name in replay.CONTROLS}


def test_profile_is_scoped_and_contains_only_two_new_allocation_arms():
    before, candidates = replay.windows.CONDITIONS, replay.windows.CANDIDATES
    with replay.profile():
        assert tuple(replay.windows.CONDITIONS) == ("legacy", "coverage_control", "aspect_fill", "aspect_balanced")
        assert replay.windows.CANDIDATES == ("aspect_fill", "aspect_balanced")
    assert replay.windows.CONDITIONS is before
    assert replay.windows.CANDIDATES is candidates


def test_profile_restores_historical_helpers_after_error():
    before = replay.windows.CONDITIONS
    with pytest.raises(RuntimeError), replay.profile():
        raise RuntimeError("failed candidate")
    assert replay.windows.CONDITIONS is before


def test_fixture_pointer_binds_existing_full_snapshot_without_copying_inventory():
    name = replay.PARENT + "/source-fixtures.json"
    value = replay.fixture_pointer({"artifacts": {name: "frozen"}})
    assert value["path"] == name and value["sha256"] == "frozen"
    assert "fixtures" not in value and "inventory" not in value


def test_reference_manifest_has_exact_two_controls_for_every_case():
    cases = [case("first"), case("second")]
    controls = saved_controls(cases)
    result = replay.reference_manifest(cases, controls)
    assert result["path"] == replay.PARENT + "/replay.jsonl.gz"
    assert [value["query_id"] for value in result["cases"]] == ["first", "second"]
    assert all(tuple(value["controls"]) == replay.CONTROLS for value in result["cases"])
    assert all("response" not in reference for value in result["cases"] for reference in value["controls"].values())
    del controls[("legacy", *replay.identity_tuple(cases[0]))]
    with pytest.raises(ValueError, match="365 cases"):
        replay.reference_manifest(cases, controls)


def test_reported_candidate_references_controls_and_retains_every_group_change():
    value = case()
    control_responses = {(name, *replay.identity_tuple(value)): response() for name in replay.CONTROLS}
    result = replay.reported_row(value, "aspect_fill", {"response": response(("a.py", "b.py"))},
                                 control_responses, {"legacy": {"record_sha256": "a"}, "coverage_control": {"record_sha256": "b"}})
    assert result["evidence_ranks"] == [1, 2]
    assert all(change["complete_gain"] and [gain["group"] for gain in change["group_gains"]] == [2]
               for change in result["comparisons"].values())
    assert result["control_references"]["legacy"]["record_sha256"] == "a"


def test_summary_requires_new_complete_query_and_preserves_partial_query_groups():
    cases = [case("new"), case("partial")]
    controls = saved_controls(cases)
    outcomes = []
    for value in cases:
        references = {name: controls[(name, *replay.identity_tuple(value))]["reference"] for name in replay.CONTROLS}
        responses = {(name, *replay.identity_tuple(value)): controls[(name, *replay.identity_tuple(value))]["response"] for name in replay.CONTROLS}
        for name in replay.CANDIDATES:
            paths = ("a.py", "b.py") if value["query_id"] == "new" else (("b.py",) if name == "aspect_balanced" else ("a.py",))
            outcomes.append(replay.reported_row(value, name, {"response": response(paths)}, responses, references))
    summary = replay.summarize(outcomes, controls, cases)
    assert summary["qualification"]["aspect_fill"]["qualified"]
    assert summary["qualification"]["aspect_fill"]["additional_distinct_complete_queries_vs_coverage_control"] == ["toy/new"]
    assert not summary["qualification"]["aspect_balanced"]["qualified"]
    assert summary["qualification"]["aspect_balanced"]["against_controls"]["coverage_control"]["lost_groups"] == 1
    assert summary["stored_new_candidate_rows"] == 4
    assert summary["automatic_default_change"] is False


def test_previously_known_control_gain_is_not_additional_candidate_gain():
    value = case()
    controls = saved_controls([value])
    controls[("coverage_control", *replay.identity_tuple(value))]["response"] = response(("a.py", "b.py"))
    responses = {(name, *replay.identity_tuple(value)): controls[(name, *replay.identity_tuple(value))]["response"] for name in replay.CONTROLS}
    outcomes = [replay.reported_row(value, name, {"response": response(("a.py", "b.py"))}, responses, {})
                for name in replay.CANDIDATES]
    summary = replay.summarize(outcomes, controls, [value])
    assert not any(result["qualified"] for result in summary["qualification"].values())
    assert all(result["gained_additional_distinct_complete_queries"] == 0 for result in summary["qualification"].values())


def git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments], text=True).strip()


def initialize_repository(root):
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Synthetic protocol test")


def commit_file(root, text):
    (root / "bound.txt").write_text(text)
    git(root, "add", "bound.txt")
    git(root, "commit", "-qm", "Synthetic frozen bytes")
    return git(root, "rev-parse", "HEAD")


def test_original_blob_proof_accepts_rewritten_current_history_without_replacing_original_ids(tmp_path):
    initialize_repository(tmp_path)
    original = commit_file(tmp_path, "original\n")
    git(tmp_path, "branch", "retained-original")
    git(tmp_path, "checkout", "--orphan", "rewritten")
    git(tmp_path, "commit", "-qm", "Rewritten equivalent tree")
    assert git(tmp_path, "rev-parse", "HEAD") != original
    proof = replay.original_blob_proof(tmp_path, freeze_commit=original, execution_commit=original,
        started_at=datetime.now(timezone.utc).isoformat(), bound={"bound.txt": replay.sha(b"original\n")},
        auditor_commit=original, auditor_bound={"bound.txt": replay.sha(b"original\n")})
    assert proof["original_execution_commit"] == original
    assert proof["original_execution_is_current_ancestor"] is False
    assert proof["original_bound_sha256"]["bound.txt"] == replay.sha(b"original\n")


@pytest.mark.parametrize("field", ["bound", "auditor_bound"])
def test_original_blob_proof_rejects_forged_execution_or_auditor_bytes(tmp_path, field):
    initialize_repository(tmp_path)
    original = commit_file(tmp_path, "original\n")
    kwargs = dict(freeze_commit=original, execution_commit=original, started_at=datetime.now(timezone.utc).isoformat(),
                  bound={"bound.txt": replay.sha(b"original\n")}, auditor_commit=original,
                  auditor_bound={"bound.txt": replay.sha(b"original\n")})
    kwargs[field] = {"bound.txt": "0" * 64}
    with pytest.raises(ValueError, match="execution blob differs"):
        replay.original_blob_proof(tmp_path, **kwargs)


def test_original_freeze_cannot_follow_recorded_execution_start(tmp_path):
    initialize_repository(tmp_path)
    original = commit_file(tmp_path, "original\n")
    with pytest.raises(ValueError, match="follows its recorded run"):
        replay.original_blob_proof(tmp_path, freeze_commit=original, execution_commit=original,
            started_at="2000-01-01T00:00:00+00:00", bound={}, auditor_commit=original, auditor_bound={})


def test_candidate_capture_calls_only_planner_once_and_preserves_receipt(monkeypatch):
    receipt = {"schema": "aspect_allocation_v1", "protected_ids": ["first"], "picks": []}
    allocation = SimpleNamespace(allocation_plan=lambda *args, **kwargs: ([], [], {}, receipt), aspect_fill_context=lambda *args, **kwargs: None)
    monkeypatch.setattr(replay.importlib, "import_module", lambda name: allocation)
    def fake_evaluate(value, name, modules):
        allocation.allocation_plan([], mode=name, query="one and two")
        return {"response": {"results": []}}
    monkeypatch.setattr(replay.windows, "evaluate", fake_evaluate)
    result = replay.evaluate_candidate({}, "aspect_fill", {"selection": SimpleNamespace()})
    assert result["allocation_receipt"] == receipt
    result["allocation_receipt"]["protected_ids"].append("second")
    assert receipt["protected_ids"] == ["first"]
    with pytest.raises(ValueError, match="two prospective"):
        replay.evaluate_candidate({}, "legacy", {})


@pytest.mark.parametrize("calls", [0, 2])
def test_candidate_capture_rejects_missing_or_repeated_planning(monkeypatch, calls):
    allocation = SimpleNamespace(allocation_plan=lambda *args, **kwargs: ([], [], {}, {}), aspect_fill_context=lambda *args, **kwargs: None)
    monkeypatch.setattr(replay.importlib, "import_module", lambda name: allocation)
    def fake_evaluate(value, name, modules):
        for _ in range(calls):
            allocation.allocation_plan([])
        return {"response": {"results": []}}
    monkeypatch.setattr(replay.windows, "evaluate", fake_evaluate)
    with pytest.raises(ValueError, match="exactly once"):
        replay.evaluate_candidate({}, "aspect_fill", {"selection": SimpleNamespace()})


def test_control_barrier_fails_before_candidate_if_parent_full_rows_differ(monkeypatch):
    value = case()
    controls = saved_controls([value])
    controls[("legacy", *replay.identity_tuple(value))].update(compact_payload="same", evidence_ranks=[1, None],
        evidence_groups=value["groups"], kind=value["kind"], multi_file=True)
    monkeypatch.setattr(replay.windows, "evaluate", lambda *args, **kwargs: {"response": response(("b.py",)), "compact_payload": "same"})
    with pytest.raises(ValueError, match="full-row/compact"):
        replay.verify_parent_controls([value], controls, {})


def test_control_reader_keeps_only_730_immutable_controls_and_exact_logical_record_hashes(tmp_path):
    folder = tmp_path / replay.PARENT
    folder.mkdir(parents=True)
    original = []
    with gzip.open(folder / "replay.jsonl.gz", "wt", encoding="utf-8") as stream:
        for number in range(365):
            value = case(str(number))
            for selector in (*replay.CONTROLS, "connected_union", "guarded_continuation", "guarded_connected_union"):
                text = json.dumps({"results": []}, ensure_ascii=False, separators=(",", ":"))
                row = {**replay.identity(value), "selector": selector, "response": {"results": []},
                       "compact_payload": text, "compact_sha256": replay.sha(text.encode()),
                       "kind": "present", "multi_file": True, "evidence_groups": value["groups"], "evidence_ranks": [None, None],
                       "stages": {"ranked": ["not duplicated"]}}
                original.append(row)
                stream.write(json.dumps(row) + "\n")
    controls = replay.control_rows(tmp_path)
    assert len(controls) == 730
    saved = controls[("coverage_control", *replay.identity_tuple(case("1")))]
    assert saved["reference"]["line_number"] == 7
    assert saved["reference"]["record_sha256"] == replay.sha(replay.canonical(original[6]))
    assert "stages" not in saved
    assert all(key[0] in replay.CONTROLS for key in controls)


def prepared_freeze(tmp_path, monkeypatch):
    initialize_repository(tmp_path)
    output = tmp_path / "results/synthetic"
    output.mkdir(parents=True)
    (tmp_path / "producer.txt").write_text("frozen source\n")
    bindings = {"producer.txt": replay.file_sha(tmp_path / "producer.txt")}
    monkeypatch.setattr(replay, "own_bindings", lambda root: bindings)
    plan = {"conditions": replay.CONDITIONS, "settings": replay.SETTINGS, "producer_sha256": bindings,
            "input_sha256": {}, "historical_parent": {"artifacts": {}}, "artifacts": {}}
    for name in replay.FROZEN_FILES:
        (output / name).write_text(json.dumps(plan if name == "frozen-plan.json" else {}))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Prospective synthetic freeze")
    return output, plan


def test_new_run_still_requires_its_own_committed_current_branch_freeze(tmp_path, monkeypatch):
    output, plan = prepared_freeze(tmp_path, monkeypatch)
    proof = replay.validate_run(tmp_path, output, plan, datetime.now(timezone.utc).isoformat())
    assert proof["commit"] == git(tmp_path, "rev-parse", "HEAD")
    assert proof["bound_blob_count"] == 1 + len(replay.FROZEN_FILES)


@pytest.mark.parametrize("changed", ["producer.txt", "results/synthetic/baseline-gate.json"])
def test_new_run_rejects_uncommitted_source_or_frozen_gate(tmp_path, monkeypatch, changed):
    output, plan = prepared_freeze(tmp_path, monkeypatch)
    (tmp_path / changed).write_text("changed\n")
    with pytest.raises(ValueError, match="Commit unchanged frozen"):
        replay.validate_run(tmp_path, output, plan, datetime.now(timezone.utc).isoformat())


def test_new_run_never_resumes_a_prior_attempt(tmp_path, monkeypatch):
    output, plan = prepared_freeze(tmp_path, monkeypatch)
    (output / "run-start.json").write_text("{}")
    with pytest.raises(ValueError, match="Never overwrite or resume"):
        replay.validate_run(tmp_path, output, plan, datetime.now(timezone.utc).isoformat())


def test_new_run_rejects_a_freeze_committed_after_recorded_start(tmp_path, monkeypatch):
    output, plan = prepared_freeze(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="freeze commit follows"):
        replay.validate_run(tmp_path, output, plan, "2000-01-01T00:00:00+00:00")


def test_no_production_or_numerical_imports_for_cli_help():
    result = subprocess.run([sys.executable,
        "-c", "import runpy,sys;sys.argv=['replay_allocation.py','--help'];\n"
        "try: runpy.run_path('scripts/replay_allocation.py',run_name='__main__')\n"
        "except SystemExit as e: assert e.code==0\n"
        "assert not any(n=='semantic_search' or n.startswith('semantic_search.') or n in ('numpy','mlx','torch','transformers') for n in sys.modules)"],
        cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
