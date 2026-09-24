"""CPU gate tests: fail closed on malformed, skipped, timed-out or false probes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


strict = load("strict_grading_tested", ROOT / "benchmarks/strict_multifile_checks/strict_common.py")
controls = load("strict_controls_tested", ROOT / "scripts/prepare_strict_multifile_controls.py")


def original(**changes):
    return {"tests_run": 13, "failures": 0, "errors": 0, "skipped": 0, "passed": True} | changes


def specification(kind="original"):
    return {"script": "benchmarks/multifile_checks/check_requests_response_limit.py", "kind": kind,
            "timeout_s": 60, "case_ids": ["one", "two"]}


def receipt(payload, code=0):
    return SimpleNamespace(stdout=json.dumps(payload), stderr="preserved stderr", returncode=code)


def supplemental(spec, **changes):
    return {"version": 3, "probe_sha256": strict.digest(ROOT / spec["script"]),
            "cases": {"one": {"passed": True}, "two": {"passed": True}}} | changes


@pytest.mark.parametrize("change", [{"skipped": 1}, {"tests_run": 0}, {"failures": 1}, {"errors": 1},
                                    {"passed": False}, {"tests_run": True}, {"passed": "true"}])
def test_original_receipt_cannot_certify_missing_or_failed_cases(tmp_path, change):
    actual = strict.run_component(specification(), tmp_path, "src/requests", runner=lambda *a, **k: receipt(original(**change)))
    assert not actual["passed"]
    assert actual["stderr"] == "preserved stderr"


def test_exit_zero_supplemental_with_one_false_grade_is_a_failure(tmp_path):
    spec = specification("supplemental")
    payload = supplemental(spec, cases={"one": {"passed": True}, "two": {"passed": False}})
    actual = strict.run_component(spec, tmp_path, "src/requests", runner=lambda *a, **k: receipt(payload))
    assert not actual["passed"] and actual["exit_code"] == 0
    assert actual["result"] == payload


@pytest.mark.parametrize("changes", [{"cases": {"one": {"passed": True}}},
                                    {"cases": {"one": {"passed": True}, "two": {"passed": "true"}}},
                                    {"version": 2}, {"probe_sha256": "0" * 64}])
def test_exact_original_supplemental_case_and_source_receipts_required(tmp_path, changes):
    spec = specification("supplemental")
    actual = strict.run_component(spec, tmp_path, "src/requests", runner=lambda *a, **k: receipt(supplemental(spec, **changes)))
    assert not actual["passed"] and "receipt_error" in actual


def test_nonzero_exit_cannot_be_overridden_by_positive_payload(tmp_path):
    actual = strict.run_component(specification(), tmp_path, "src/requests", runner=lambda *a, **k: receipt(original(), 9))
    assert not actual["passed"] and actual["exit_code"] == 9


def test_timeout_is_retained_once_with_partial_output(tmp_path):
    calls = []
    def timed_out(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 60, output=b"partial stdout", stderr=b"partial stderr")
    actual = strict.run_component(specification(), tmp_path, "src/requests", runner=timed_out)
    assert len(calls) == 1 and actual["timed_out"] and actual["exit_code"] is None and not actual["passed"]
    assert actual["stdout"] == "partial stdout" and actual["stderr"] == "partial stderr"


def test_aggregate_preserves_original_grade_and_failed_supplemental_without_retry(tmp_path, monkeypatch):
    (tmp_path / "src/requests").mkdir(parents=True)
    specs = [specification(), specification("supplemental")]
    monkeypatch.setattr(strict, "bindings", lambda task: ({}, {"source_root": "src/requests", "components": specs}))
    payloads = [original(), supplemental(specs[1], cases={"one": {"passed": True}, "two": {"passed": False}})]
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return receipt(payloads[len(calls) - 1])
    result = strict.grade("requests_response_limit", tmp_path, runner=run)
    assert len(calls) == 2 and result["tests_run"] == 15 and result["failures"] == 1
    assert not result["passed"] and result["original_grade_unchanged"] == original()
    assert result["model_calls"] == 0


def test_source_binding_rejects_drift_before_any_commands(tmp_path, monkeypatch):
    (tmp_path / "original.json").write_text("{}")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "tasks": {"x": {}}, "input_sha256": {"original.json": "0" * 64}}))
    monkeypatch.setattr(strict, "ROOT", tmp_path)
    monkeypatch.setattr(strict, "MANIFEST", manifest)
    with pytest.raises(ValueError, match="input changed"):
        strict.grade("x", tmp_path, runner=lambda *a, **k: pytest.fail("must not start command"))


def test_published_manifest_covers_all_accepted_cases_and_unchanged_task_contracts():
    manifest, _ = strict.bindings("requests_response_limit")
    assert set(manifest["tasks"]) == set(controls.SELECTED)
    assert manifest["accepted_contract_defects"] == 6 and manifest["supplemental_cases"] == 10
    assert len(manifest["tasks"]["requests_response_limit"]["components"][1]["case_ids"]) == 5
    assert len(manifest["tasks"]["click_atomic_transactions"]["components"][1]["case_ids"]) == 5
    assert len(manifest["tasks"]["starlette_body_limits"]["components"]) == 1


def test_restore_summary_parser_retains_all_upstream_counter_fields():
    assert controls.counts("unrelated output\n257 passed, 13 skipped, 6 warnings in 0.72s\n") == {
        "passed": 257, "skipped": 13, "failed": 0, "errors": 0, "xfailed": 0, "xpassed": 0, "deselected": 0}
    assert controls.counts("provider did not run tests") is None


def test_imports_never_load_native_models():
    code = ("import importlib.util,sys; "
            f"p={str(ROOT / 'benchmarks/strict_multifile_checks/strict_common.py')!r}; "
            "s=importlib.util.spec_from_file_location('strict',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "assert not any(n.startswith(('mlx.','mlx_lm.','mlx_embeddings.')) for n in sys.modules)")
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)


def test_public_receipts_sanitize_paths_while_retaining_exact_raw_witnesses(tmp_path, monkeypatch):
    workspace = tmp_path / "base"
    workspace.mkdir()
    raw = str(controls.ROOT) + "/controlled-source.py: original output"
    monkeypatch.setattr(controls.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=raw, stderr=raw, returncode=0))
    logs = tmp_path / "public"
    receipt, returned = controls.command_receipt(["fake"], workspace, {"source_root": "src/package"}, logs, "check", timeout=2)
    assert returned == raw
    assert (logs / "check.stdout.txt").read_text() == "<PROJECT>/controlled-source.py: original output"
    witness = tmp_path / "control-raw-logs-base/check.stdout.txt"
    assert witness.read_text() == raw and receipt["ignored_raw_stdout_sha256"] == controls.digest(witness)
    assert receipt["stdout_sha256"] == controls.digest(logs / "check.stdout.txt")
    assert receipt["stdout_sha256"] != receipt["ignored_raw_stdout_sha256"]


def test_publication_also_sanitizes_interpreter_paths_outside_the_project():
    raw = str(Path.home() / ".local/runtime/lib/python/test.py")
    assert controls.publish(raw) == "<USER_HOME>/.local/runtime/lib/python/test.py"
