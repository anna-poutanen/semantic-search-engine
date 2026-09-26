"""Freeze/grade/argv boundaries of the new runner; no hosted/model calls."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("qwen_workflow_runner_test", ROOT / "scripts/run_qwen_workflows.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def payload(**changes):
    return {"version": 1, "task_id": "task", "strict_manifest_sha256": "manifest", "model_calls": 0,
        "tests_run": 2, "failures": 0, "errors": 0, "skipped": 0, "passed": True,
        "original_grade_unchanged": {"tests_run": 1, "failures": 0, "errors": 0, "skipped": 0, "passed": True},
        "components": [{"script": "original.py", "script_sha256": "original", "exit_code": 0,
            "timed_out": False, "passed": True, "command": ["python", "/varying/repo"],
            "stdout": "varying diagnostics", "result": {"passed": True}},
            {"script": "supplemental.py", "script_sha256": "supplemental", "exit_code": 0,
             "timed_out": False, "passed": True, "result": {"version": 3, "probe_sha256": "supplemental",
                "cases": {"edge": {"passed": True, "diagnostic": "variable traceback"}}}}], **changes}


def test_grade_projection_ignores_paths_and_trace_text_but_keeps_actual_oracle_values():
    first, second = payload(), payload()
    second["components"][0]["command"] = ["python", "/another/fixture"]
    second["components"][0]["stdout"] = "different paths and elapsed time"
    second["components"][1]["result"]["cases"]["edge"]["diagnostic"] = "other diagnostic"
    assert runner.grade_projection(first) == runner.grade_projection(second)
    second["components"][1]["result"]["cases"]["edge"]["passed"] = False
    assert runner.grade_projection(first) != runner.grade_projection(second)


@pytest.mark.parametrize("field", ["probe_sha256", "version"])
def test_supplemental_oracle_identity_is_part_of_projection(field):
    first, second = payload(), payload()
    second["components"][1]["result"][field] = "different"
    assert runner.grade_projection(first) != runner.grade_projection(second)


@pytest.mark.parametrize("change", [{"errors": False}, {"model_calls": True}, {"passed": "true"},
                                    {"skipped": -1}, {"tests_run": None}])
def test_malformed_typed_primary_grade_is_not_usable(change):
    assert runner.grade_projection(payload(**change)) is None


def test_nonzero_exit_does_not_pass_even_with_positive_projection(tmp_path, monkeypatch):
    detail = payload()
    task = {"verification": {"script": "checker.py", "args": ["--repo", "{workspace}"], "timeout_s": 5},
            "source_root": "src", "seed_validation": {"reference": runner.grade_projection(detail)}}
    monkeypatch.setattr("subprocess.run", lambda *a, **k: SimpleNamespace(
        returncode=8, stdout=json.dumps(detail), stderr="retained failure"))
    result = runner.grade(task, tmp_path)
    assert not result["passed"]
    assert result["detail"] == detail and result["stderr"] == "retained failure"


def test_same_common_model_prompt_and_settings_across_three_conditions():
    config = {"cli_binary": "codex", "python": sys.executable, "model": "gpt-6.1-sol",
              "common_config": runner.mf.COMMON_CONFIG,
              "selected_profile": {"retrieval": "hybrid", "shortlist": 24, "batch_size": 1}}
    workspace = Path("/tmp/fixture")
    task_prompt = runner.prompt({"prompt": "Add a behavior."}, sys.executable)
    commands = {condition: runner.cli_command(config, workspace, workspace / "final.txt", task_prompt,
        None if condition == "baseline" else "http://127.0.0.1:9999", condition, "src")
        for condition in runner.evidence.CONDITIONS}
    def settings(command):
        return dict(command[index + 1].split("=", 1) for index, arg in enumerate(command) if arg == "-c")
    baseline = settings(commands["baseline"])
    for condition in ("mcp_fast", "mcp_qwen"):
        configured = settings(commands[condition])
        assert baseline == {key: value for key, value in configured.items() if not key.startswith("mcp_servers.")}
        assert commands[condition][-1] == commands["baseline"][-1]
        assert '--default-retrieval", "hybrid"' in configured["mcp_servers.semantic_search.args"]
        assert '--default-candidates", "24"' in configured["mcp_servers.semantic_search.args"]
    assert "--default-rerank" not in settings(commands["mcp_fast"])["mcp_servers.semantic_search.args"]
    assert "--default-rerank-provider" in settings(commands["mcp_qwen"])["mcp_servers.semantic_search.args"]
    assert "do not pass retrieval" in task_prompt


def test_fresh_profile_rejects_unqualified_development_before_reading_results(tmp_path):
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps({"stage": "development", "ready_for_fresh_validation": False}))
    with pytest.raises(ValueError, match="Certified"):
        runner.selected_profile(decision, tmp_path / "unobserved-httpx")


def test_no_resume_is_rejected_before_any_hosted_or_model_work(tmp_path, monkeypatch):
    (tmp_path / "runs").mkdir()
    (tmp_path / "frozen-config.json").write_text("{}")
    monkeypatch.setattr(runner.mf, "verify_runtime", lambda *a: pytest.fail("runtime/model work happened"))
    with pytest.raises(ValueError, match="resume"):
        runner.run(tmp_path)


def trial_fixture(tmp_path, monkeypatch):
    output = tmp_path / "published"
    output.mkdir()
    task = {"id": "requests_response_limit", "prompt": "Add a feature", "source_root": "src",
            "seed_validation": {"base": "base", "reference": "reference"}}
    config = {"work_root": str(tmp_path / "private"), "python": sys.executable,
              "reranker_python": sys.executable, "reranker_checkpoint": "unused",
              "prompt_sha256": {task["id"]: runner.hashlib.sha256(runner.prompt(task, sys.executable).encode()).hexdigest()}}
    def prepare(task, workspace):
        workspace.mkdir(parents=True)
        return "commit", {"src/a.py": "hash"}
    calls = []
    def grade(task, workspace):
        calls.append(workspace)
        return {"passed": len(calls) > 1, "projection": "base" if len(calls) == 1 else "reference"}
    monkeypatch.setattr(runner.mf, "prepare", prepare)
    monkeypatch.setattr(runner, "grade", grade)
    monkeypatch.setattr(runner.mf, "compatibility", lambda *a: {"passed": True})
    monkeypatch.setattr(runner.mf.old, "git", lambda *a, **k: SimpleNamespace(stdout=b""))
    monkeypatch.setattr(runner, "cli_command", lambda *a: ["never-run"])
    events = [{"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}]
    monkeypatch.setattr(runner.mf, "run_agent", lambda *a: ({"completed": True, "agent_exit_code": 0,
        "failed": False, "timed_out": False, "mcp_calls": 0, "agent_elapsed_s": 10}, events, []))
    monkeypatch.setattr(runner.mf, "public_artifacts", lambda *a: {})
    return task, config, output


def test_export_failure_preserves_measured_provider_usage_before_fallible_publication(tmp_path, monkeypatch):
    task, config, output = trial_fixture(tmp_path, monkeypatch)
    def fail_export(*args):
        result = next((output / "runs").glob("*/result.json"))
        assert json.loads(result.read_text())["usage"]["total_tokens"] == 120
        raise RuntimeError("publication failed")
    monkeypatch.setattr(runner.mf, "public_artifacts", fail_export)
    row = runner.run_trial(task, {"task_id": task["id"], "repetition": 1, "condition": "baseline"},
                           1, config, output, runner.ClockIntegrity())
    assert row["usage"]["total_tokens"] == 120 and row["agent_elapsed_s"] == 10
    assert row["publication_error"] == "RuntimeError: publication failed"
    assert row["correct"] and not row["completed_correct"]
    assert row["successful_qwen_searches"] is None


@pytest.mark.parametrize("stop, safe", [
    ({"owned_group_stopped": False, "forced_kill": False, "exit_code": None}, False),
    ({"owned_group_stopped": True, "forced_kill": True, "exit_code": -9}, True),
])
def test_unproved_owner_stop_blocks_next_models_and_forced_kill_is_not_clean_correctness(tmp_path, monkeypatch, stop, safe):
    task, config, output = trial_fixture(tmp_path, monkeypatch)
    class Owner:
        receipt = {"owned": True}
        stop_receipt = stop
        def __init__(self, **kwargs):
            pass
        def start(self):
            return object()
        def close(self):
            return stop
    monkeypatch.setitem(sys.modules, "semantic_search.qwen_reranker", SimpleNamespace(QwenRerankerProcess=Owner))
    backend = SimpleNamespace(refresh=lambda: {}, search=lambda *a, **k: {}, start_watching=lambda: None,
                              status=lambda: {}, close=lambda: None)
    monkeypatch.setattr(runner, "recording_backend", lambda *a: backend)
    finished = threading.Event()
    server = SimpleNamespace(server_port=9999, serve_forever=lambda: finished.wait(2),
                             shutdown=lambda: finished.set(), server_close=lambda: None)
    monkeypatch.setattr("semantic_search.server.create_server", lambda *a, **k: server)
    row = runner.run_trial(task, {"task_id": task["id"], "repetition": 1, "condition": "mcp_qwen"},
                           1, config, output, runner.ClockIntegrity())
    assert row["lifecycle_qualified"] is safe
    assert row["failed"] and row["correct"] and not row["completed_correct"]
    assert row["qwen_stop"] == stop
