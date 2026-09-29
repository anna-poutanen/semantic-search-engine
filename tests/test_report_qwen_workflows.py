"""Reporting gates must retain unknown usage and reject changed certificates."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/report_qwen_workflows.py"
spec = importlib.util.spec_from_file_location("qwen_report", SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


@pytest.fixture
def evidence(tmp_path):
    study = tmp_path / "study"
    snapshots = tmp_path / "audit" / "as-of"
    snapshots.mkdir(parents=True)
    run = study / "runs" / "01-r1-task-baseline"
    run.mkdir(parents=True)
    row = {"run_id": run.name, "usage": None, "correct": False, "completed_correct": False}
    (run / "result.json").write_text(json.dumps(row))
    missing = [{"task_id": "task", "repetition": 1, "condition": "mcp_fast"},
               {"task_id": "task", "repetition": 1, "condition": "mcp_qwen"}]
    condition = {"recorded": 0, "planned": 6, "normally_completed": 0, "correct": 0,
                 "completed_correct": 0, "timeouts": 0,
                 "measurements": {key: {"observations": 0, "sum": None, "median": None}
                                  for key, _ in report.METRICS}}
    summary = {"inventory_complete": False, "planned_attempts": 18, "recorded_attempts": 1,
               "missing_attempts": missing, "conditions": {c: condition for c in report.CONDITIONS},
               "comparisons": []}
    files = {"frozen-config.json": {}, "tasks.json": {}, "run-start.json": {},
             "results.json": [row], "summary.json": summary}
    for name, value in files.items():
        (snapshots / name).write_text(json.dumps(value))
    attempt = {"run_id": run.name, "result_sha256": report.digest(run / "result.json"),
               "provider_usage": None, "integrity_passed": True, "normally_completed": False,
               "timing_qualified": False, "mcp": {"successful": 0}, "native_qwen": {"completed": 0}}
    certificate = {"checks_passed": True, "inventory_complete": False, "final_completion_receipt": False,
                   "planned_attempts": 18, "recorded_attempts": 1, "missing_attempts": list(reversed(missing)),
                   "conditions": summary["conditions"], "comparisons": [], "attempts": [attempt],
                   "artifact_sha256": {name: report.digest(snapshots / name) for name in files},
                   "global_input_snapshots": {name: {"file": "as-of/" + name, "sha256": report.digest(snapshots / name)}
                                              for name in files}}
    audit = snapshots.parent / "certificate.json"
    audit.write_text(json.dumps(certificate))
    return study, audit, certificate


def change_certificate(evidence, key, value):
    study, audit, certificate = evidence
    certificate[key] = value
    audit.write_text(json.dumps(certificate))
    return study, audit


def test_interim_uses_immutable_snapshot_and_retains_unknown(evidence):
    study, audit, _ = evidence
    (study / "summary.json").write_text("live global output is deliberately unrelated")
    records, summary, certificate = report.load_certified(study, audit, allow_partial=True)
    text = report.render(records, summary, certificate)
    assert "interim snapshot" in text
    assert "full-workload totals remain unknown" in text
    assert "01-r1-task-baseline | False | False | False | unknown | unknown | unknown" in text
    assert "0/6 | unknown | unknown" in text


def test_final_refuses_pending_inventory(evidence):
    with pytest.raises(ValueError, match="complete frozen inventory"):
        report.load_certified(*evidence[:2])


def test_complete_rows_without_driver_finish_are_not_final(evidence):
    study, audit = change_certificate(evidence, "inventory_complete", True)
    with pytest.raises(ValueError, match="driver's final completion"):
        report.load_certified(study, audit)


def test_failed_audit_cannot_publish(evidence):
    study, audit = change_certificate(evidence, "checks_passed", False)
    with pytest.raises(ValueError, match="passing independent"):
        report.load_certified(study, audit, True)


def test_changed_attempt_is_rejected(evidence):
    study, audit, _ = evidence
    (study / "runs/01-r1-task-baseline/result.json").write_text("{}")
    with pytest.raises(ValueError, match="Terminal attempt bytes changed"):
        report.load_certified(study, audit, True)


def test_changed_snapshot_is_rejected(evidence):
    study, audit, _ = evidence
    (audit.parent / "as-of/results.json").write_text("[]")
    with pytest.raises(ValueError, match="artifact mismatch"):
        report.load_certified(study, audit, True)


def test_snapshot_escape_is_rejected(evidence):
    study, audit, certificate = evidence
    certificate["global_input_snapshots"]["results.json"]["file"] = "../../private.json"
    audit.write_text(json.dumps(certificate))
    with pytest.raises(ValueError, match="snapshot binding mismatch"):
        report.load_certified(study, audit, True)


def test_duplicate_certificate_attempt_is_rejected(evidence):
    study, audit, certificate = evidence
    certificate["attempts"] *= 2
    audit.write_text(json.dumps(certificate))
    with pytest.raises(ValueError, match="inventory does not match"):
        report.load_certified(study, audit, True)


def test_percent_zero_denominator_is_unknown():
    assert report.percent(None) == "unknown"
    assert report.percent(0) == "+0.00%"


def bind_completion(evidence):
    study, audit, certificate = evidence
    path = audit.parent / "as-of/completion.json"
    path.write_text(json.dumps({"recorded_attempts": 18, "clock": {"label": "study-complete"}}))
    certificate["final_completion_receipt"] = True
    certificate["artifact_sha256"][path.name] = report.digest(path)
    certificate["global_input_snapshots"][path.name] = {"file": "as-of/completion.json", "sha256": report.digest(path)}
    audit.write_text(json.dumps(certificate))
    return study, audit, path


def test_completion_receipt_requires_certificate_hash(evidence):
    study, audit = change_certificate(evidence, "final_completion_receipt", True)
    with pytest.raises(ValueError, match="lacks its certificate artifact binding"):
        report.load_certified(study, audit, True)


def test_changed_final_completion_snapshot_is_rejected(evidence):
    study, audit, path = bind_completion(evidence)
    path.write_text("{}")
    with pytest.raises(ValueError, match="artifact mismatch: completion.json"):
        report.load_certified(study, audit, True)


def test_final_completion_cannot_fall_back_to_live_globals(evidence):
    study, audit, _ = bind_completion(evidence)
    certificate = json.loads(audit.read_text())
    del certificate["global_input_snapshots"]["completion.json"]
    audit.write_text(json.dumps(certificate))
    with pytest.raises(ValueError, match="snapshot binding missing: completion.json"):
        report.load_certified(study, audit, True)


def test_hash_bound_completion_is_accepted(evidence):
    study, audit, _ = bind_completion(evidence)
    _, _, certificate = report.load_certified(study, audit, True)
    assert certificate["final_completion_receipt"] is True


def test_complete_inventory_without_final_receipt_stays_interim(evidence):
    study, audit, certificate = evidence
    records, summary, _ = report.load_certified(study, audit, True)
    certificate["inventory_complete"] = True
    text = report.render(records, summary, certificate)
    assert "interim snapshot" in text
    assert "driver's final completion witness is pending" in text
    assert "Missing attempts are pending" not in text


def test_native_startup_failure_retains_lower_bound_and_unknown_total(evidence):
    records, summary, certificate = report.load_certified(*evidence[:2], True)
    certificate["attempts"][0]["native_qwen"] = {"completed": 0, "warmup": 1, "counts_complete": False}
    text = report.render(records, summary, certificate)
    assert "≥0 (total unknown)" in text
    assert "retained lower bounds" in text
    assert "0/1 recorded attempts retain hash-verified published patches" in text
    assert "All attempts retain exact patches" not in text
    assert report.native_count({"completed": 72, "warmup": 1}) == "72"


def test_declared_patch_missing_or_changed_is_rejected(evidence):
    study, audit, certificate = evidence
    run = study / "runs/01-r1-task-baseline"
    row = json.loads((run / "result.json").read_text())
    row["patch_sha256"] = "0" * 64
    (run / "result.json").write_text(json.dumps(row))
    snapshot = audit.parent / "as-of/results.json"
    snapshot.write_text(json.dumps([row]))
    certificate["attempts"][0]["result_sha256"] = report.digest(run / "result.json")
    certificate["artifact_sha256"][snapshot.name] = report.digest(snapshot)
    certificate["global_input_snapshots"][snapshot.name]["sha256"] = report.digest(snapshot)
    audit.write_text(json.dumps(certificate))
    with pytest.raises(ValueError, match="Published patch witness missing or changed"):
        report.load_certified(study, audit, True)
    (run / "changes.patch").write_text("changed patch")
    with pytest.raises(ValueError, match="Published patch witness missing or changed"):
        report.load_certified(study, audit, True)
