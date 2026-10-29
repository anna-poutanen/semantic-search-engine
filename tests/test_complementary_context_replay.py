"""Producer/independent-auditor roundtrips use only invented source and labels."""
from copy import deepcopy
import gzip
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("complementary_replay_test", ROOT / "scripts/replay_complementary_context.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)
data = replay.inputs


@pytest.fixture
def modules():
    return {name: importlib.import_module("semantic_search." + name) for name in ("context", "corpus", "selection")}


def make_case(modules, *, active=True, qualified=True, query_id="invented", qwen_key=True):
    words = {1: "load alpha payload", 9: "render beta template", 10: "persist gamma cache"}
    inventory = {}
    for number in range(1, 11):
        text = f"def f{number}():\n    return '{words.get(number, 'ordinary value')}'\n"
        path = "src/helper.py" if number >= 9 else "src/code.py"
        start = number * 3 + 1
        item = data.Window(f"{path}:f{number}:{start}-{start+1}", path, f"f{number}",
                           start, start+1, text, replay.sha(text.encode()))
        inventory[item.id] = item
    items = list(inventory.values())
    case = {"subset": "synthetic", "dataset": "invented", "query_id": query_id, "condition": "dense",
        "kind": "present", "multi_file": True, "qualified": qualified,
        "pairs": [(item, 100. - number) for number, item in enumerate(items)], "inventory": inventory,
        "definition_keys": {item.id: (item.path, item.symbol, item.start_line, item.end_line) for item in items},
        "graph": data.FrozenGraph(inventory, []), "ambiguous_symbols": set(),
        "original": {"query": "load alpha payload and render beta template and persist gamma cache" if active else "simple implementation",
            "scope": modules["context"].indexed_scope("src"), "match_status": "candidates",
            "results": [{"qwen_score": None}] if qwen_key else []}}
    case["groups"] = [[{"path": item.path, "symbol": item.symbol,
        "evidence_lines": [item.start_line + 1, item.end_line],
        "evidence_sha256": replay.sha(item.text.splitlines(keepends=True)[1].encode())}]
        for item in (items[0], items[8])]
    return case


def control_records(case, modules):
    result = {}
    for name in data.CONTROLS:
        response = replay.evaluate_control(case, name, modules)
        text = replay.verify_response(response, case, modules)
        result[name, data.case_key(case)] = {"response": response,
            "reference": {"path": "synthetic/controls.jsonl.gz", "line_number": 1,
                "record_sha256": replay.sha(replay.canonical(response)),
                "response_sha256": replay.sha(replay.canonical(response)), "compact_sha256": replay.sha(text.encode())}}
    return result


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.parametrize("qwen_key", [False, True])
def test_real_producer_records_actual_fastpath_or_factory_and_independent_roundtrip(modules, active, qualified, qwen_key):
    case = make_case(modules, active=active, qualified=qualified, qwen_key=qwen_key)
    controls = control_records(case, modules)
    actual, receipts = replay.control_barrier([case], controls, modules)
    assert len(receipts) == 2
    result = replay.evaluate_candidate(case, modules, actual["coverage_control", data.case_key(case)])
    row = replay.reported_row(case, result, controls)
    assert row["planner_calls"] == row["guard_calls"] == row["coalesce_calls"] == 1
    assert row["reference_calls"] == int(active) and row["self_reference_identity"] is (not active)
    assert row["selection_plan"]["accepted"] is active
    auditor = importlib.import_module("audit_complementary_context")
    assert auditor.verify_row(row, case, controls, modules["context"].encoding()) == row["response"]
    if not active:
        assert result["response"] == actual["coverage_control", data.case_key(case)]


@pytest.mark.parametrize("field", ["scope", "source", "source_hash", "content_hash", "end_line", "retrieval_score", "token_count"])
def test_public_source_verifier_rejects_tampered_receipts(modules, field):
    case = make_case(modules)
    value = replay.evaluate_control(case, "legacy", modules)
    if field == "scope":
        value["scope"]["source_root"] = "outside"
    elif field == "token_count":
        value["token_usage"]["tokens"] += 1
    elif field == "end_line":
        value["results"][0][field] += 1
    elif field == "retrieval_score":
        value["results"][0][field] = 123456.
    else:
        value["results"][0][field] = "tampered"
    with pytest.raises(ValueError):
        replay.verify_response(value, case, modules)


def test_control_parity_failure_prevents_any_candidate_execution(modules, monkeypatch):
    case = make_case(modules)
    controls = control_records(case, modules)
    controls["legacy", data.case_key(case)]["response"]["results"][0]["source_hash"] = "tampered"
    monkeypatch.setattr(replay, "evaluate_candidate", lambda *args: pytest.fail("candidate before control gate"))
    with pytest.raises(ValueError, match="original control"):
        replay.control_barrier([case], controls, modules)


def test_qualified_scoring_requires_symbol_full_interval_and_exact_executable_bytes(modules):
    case = make_case(modules)
    response = replay.evaluate_control(case, "legacy", modules)
    target = deepcopy(case["groups"][0][0])
    assert replay.evidence_ranks(response["results"], [[target]], True) == [1]
    target["symbol"] = "wrong"
    assert replay.evidence_ranks(response["results"], [[target]], True) == [None]
    assert replay.evidence_ranks(response["results"], [[target]], False) == [1]
    target = deepcopy(case["groups"][0][0])
    target["evidence_sha256"] = "0" * 64
    assert replay.evidence_ranks(response["results"], [[target]], True) == [None]
    target["evidence_lines"][1] += 1
    assert replay.evidence_ranks(response["results"], [[target]], False) == [None]


def test_gate_requires_distinct_multifile_gain_and_all_individual_losses_are_fatal(modules):
    cases, controls, outcomes = [], {}, []
    for condition in ("dense", "hybrid"):
        case = make_case(modules)
        case["condition"] = condition
        records = control_records(case, modules)
        controls.update(records)
        result = replay.evaluate_candidate(case, modules, records["coverage_control", data.case_key(case)]["response"])
        cases.append(case)
        outcomes.append(replay.reported_row(case, result, records))
    gate = replay.summarize(outcomes, controls, cases)["qualification"]
    assert gate["qualified"] and gate["additional_distinct_complete_multi_queries_vs_coverage"] == [["invented", "invented"]]
    auditor = importlib.import_module("audit_complementary_context")
    assert gate == auditor.quality_gate(cases, {data.case_key(row): row["response"] for row in outcomes}, controls)
    outcomes[0]["comparisons"]["legacy"]["group_losses"] = [1]
    assert not replay.summarize(outcomes, controls, cases)["qualification"]["qualified"]
    outcomes[0]["comparisons"]["legacy"]["group_losses"] = []
    for row in outcomes:
        row["multi_file"] = False
    assert not replay.summarize(outcomes, controls, cases)["qualification"]["qualified"]


def test_stream_preserves_first_row_and_attempt_count_after_second_case_failure(modules, tmp_path, monkeypatch):
    first = make_case(modules, query_id="first")
    second = make_case(modules, query_id="second")
    cases = [first, second]
    controls = {key: value for case in cases for key, value in control_records(case, modules).items()}
    actual, _ = replay.control_barrier(cases, controls, modules)
    original = replay.evaluate_candidate
    def failed(case, *args):
        if case["query_id"] == "second":
            raise RuntimeError("synthetic interrupted replay")
        return original(case, *args)
    monkeypatch.setattr(replay, "evaluate_candidate", failed)
    state = {"attempted_new_variant_rows": 0, "new_variant_rows": 0}
    with pytest.raises(RuntimeError, match="interrupted"):
        replay.stream_outcomes(cases, controls, actual, modules, tmp_path, state)
    with gzip.open(tmp_path / "replay.jsonl.gz", "rt") as stream:
        retained = [json.loads(line) for line in stream]
    assert len(retained) == 1 and retained[0]["query_id"] == "first"
    assert state["attempted_new_variant_rows"] == 2 and state["new_variant_rows"] == 1
    with pytest.raises(FileExistsError):
        replay.stream_outcomes(cases, controls, actual, modules, tmp_path, state)


def test_full_invented393_case_201_unit_stream_roundtrips_independent_auditor(modules, tmp_path):
    prototype = make_case(modules)
    prototype_controls = control_records(prototype, modules)
    cases, controls = [], {}
    for unit in range(201):
        for repetition in range(2 if unit < 192 else 1):
            case = {**prototype, "query_id": "invented_" + str(unit), "condition": "mode" + str(repetition)}
            cases.append(case)
            for name in data.CONTROLS:
                controls[name, data.case_key(case)] = deepcopy(prototype_controls[name, data.case_key(prototype)])
    actual, receipts = replay.control_barrier(cases, controls, modules)
    assert len(cases) == 393 and len(receipts) == 786
    state = {"attempted_new_variant_rows": 0, "new_variant_rows": 0}
    rows = replay.stream_outcomes(cases, controls, actual, modules, tmp_path, state)
    auditor = importlib.import_module("audit_complementary_context")
    for row, case in zip(rows, cases):
        assert auditor.verify_row(row, case, controls, modules["context"].encoding()) == row["response"]
    summary = replay.summarize(rows, controls, cases)
    assert summary["counts"]["distinct_dataset_query_units"] == 201
    assert summary["qualification"] == auditor.quality_gate(cases,
        {data.case_key(row): row["response"] for row in rows}, controls)
    assert state == {"attempted_new_variant_rows": 393, "new_variant_rows": 393, "current_case": replay.identity(cases[-1])}
    assert len(list(data.rows(tmp_path / "replay.jsonl.gz"))) == 393


def test_freeze_and_run_validation_require_exact_committed_source_and_new_attempt(tmp_path, monkeypatch):
    root, output = tmp_path, tmp_path / "results"
    output.mkdir()
    (root / "code.py").write_text("frozen source\n")
    bindings = {"code.py": data.file_sha(root / "code.py")}
    monkeypatch.setattr(replay, "own_bindings", lambda root: bindings)
    plan = {"study": replay.MODE, "conditions": [*data.CONTROLS, replay.MODE], "settings": replay.SETTINGS,
            "producer_sha256": bindings, "historical_parents": {}, "artifacts": {}}
    for name in replay.FROZEN_FILES:
        data_value = plan if name == "frozen-plan.json" else {}
        replay.write_new(output / name, data_value)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "commit", "-qm", "freeze invented source"], check=True)
    assert replay.validate_run(root, output, plan, "2100-01-01T00:00:00+00:00")["bound_blob_count"] == 6
    (root / "code.py").write_text("changed source\n")
    with pytest.raises(ValueError, match="frozen bytes"):
        replay.validate_run(root, output, plan, "2100-01-01T00:00:00+00:00")
    (root / "code.py").write_text("frozen source\n")
    (output / "failure.json").write_text("{}")
    with pytest.raises(ValueError, match="Never overwrite"):
        replay.validate_run(root, output, plan, "2100-01-01T00:00:00+00:00")


def test_actual_run_claim_survives_runtime_failure_and_cannot_be_overwritten(tmp_path, monkeypatch):
    replay.write_new(tmp_path / "frozen-plan.json", {"runtime": {"synthetic": True}})
    monkeypatch.setattr(replay, "validate_run", lambda *args: {"commit": "synthetic-freeze"})
    monkeypatch.setattr(replay.subprocess, "check_output", lambda *args, **kwargs: "synthetic-commit\n")
    def failed(root):
        raise RuntimeError("synthetic runtime failure")
    monkeypatch.setattr(replay.prior, "runtime", failed)
    with pytest.raises(RuntimeError, match="runtime failure"):
        replay.run(tmp_path, tmp_path)
    start, failure = data.read(tmp_path / "run-start.json"), data.read(tmp_path / "failure.json")
    assert start["resume_allowed"] is False and all(value == 0 for value in start["calls"].values())
    assert failure["progress"]["attempted_new_variant_rows"] == failure["progress"]["new_variant_rows"] == 0
    before = (tmp_path / "run-start.json").read_bytes()
    with pytest.raises(FileExistsError):
        replay.run(tmp_path, tmp_path)
    assert (tmp_path / "run-start.json").read_bytes() == before
