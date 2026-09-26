"""Freeze/grade/argv boundaries of the new runner; no hosted/model calls."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import signal
from types import SimpleNamespace
import threading
import time

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
    monkeypatch.setattr(runner.process_capture, "run_agent", lambda *a, **k: ({"completed": True, "agent_exit_code": 0,
        "failed": False, "timed_out": False, "mcp_calls": 0, "agent_elapsed_s": 10,
        "cleanup_qualified": True, "output_drain_complete": True, "lifecycle_qualified": True}, events, []))
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


@pytest.mark.parametrize("phase", ["setup", "after-cli", "grading", "publication"])
def test_interruption_in_each_runner_phase_publishes_failed_attempt_with_known_usage(tmp_path, monkeypatch, phase):
    task, config, output = trial_fixture(tmp_path, monkeypatch)
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("injected phase interruption")
    if phase == "setup":
        monkeypatch.setattr(runner.mf, "prepare", interrupt)
    elif phase == "grading":
        actual = runner.grade
        calls = []
        def grade(*args):
            calls.append(1)
            return actual(*args) if len(calls) == 1 else interrupt()
        monkeypatch.setattr(runner, "grade", grade)
    elif phase == "publication":
        monkeypatch.setattr(runner.mf, "public_artifacts", interrupt)
    clock = runner.ClockIntegrity()
    if phase == "after-cli":
        actual = clock.check
        def check(label, **kwargs):
            return interrupt() if label == "agent-end" else actual(label, **kwargs)
        monkeypatch.setattr(clock, "check", check)
    row = runner.run_trial(task, {"task_id": task["id"], "repetition": 1, "condition": "baseline"},
                           1, config, output, clock)
    published = json.loads(next((output / "runs").glob("*/result.json")).read_text())
    assert row["interrupted"] and row["failed"] and row["timing_qualified"] is False
    assert published["interrupted"] and not published["completed_correct"]
    assert row["usage"] is None if phase == "setup" else row["usage"]["total_tokens"] == 120
    if phase in ("setup", "after-cli", "publication"):
        assert "grading_unstarted_reason" in row


def test_actual_cpu_cli_sigterm_retains_usage_cleanup_and_halts_remaining_schedule(tmp_path, monkeypatch):
    actual_capture = runner.process_capture.run_agent
    task, config, output = trial_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(runner.process_capture, "run_agent", actual_capture)
    monkeypatch.setattr(runner.mf, "environment", lambda *args: dict(os.environ))
    wire = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}})
    command = [sys.executable, "-u", "-c", f"import time;print({wire!r},flush=True);time.sleep(30)"]
    monkeypatch.setattr(runner, "cli_command", lambda *args: command)
    trials = [{"task_id": task["id"], "repetition": 1, "condition": value}
              for value in runner.evidence.CONDITIONS]
    config.update(manifest="unused", timeout_s=5, schedule=trials,
                  reranker_python_sha256=runner.sha(sys.executable))
    (output / "frozen-config.json").write_text(json.dumps(config))
    monkeypatch.setattr(runner.mf, "verify_runtime", lambda *args: None)
    monkeypatch.setattr(runner, "verify_native_runtime", lambda *args: None)
    monkeypatch.setattr(runner, "validate_manifest", lambda *args: {"tasks": [task]})
    raw = Path(config["work_root"]) / "raw" / "01-r1-requests_response_limit-baseline"
    sent = threading.Event()
    cancel = threading.Event()
    def terminate_after_actual_usage():
        deadline = time.monotonic() + 4
        while not cancel.is_set() and time.monotonic() < deadline:
            checkpoint = raw / "agent-measurements.json"
            if checkpoint.is_file() and (json.loads(checkpoint.read_bytes()).get("usage") or {}).get("total_tokens") == 120:
                sent.set()
                os.kill(os.getpid(), signal.SIGTERM)
                return
            time.sleep(.01)
    sender = threading.Thread(target=terminate_after_actual_usage, daemon=True)
    sender.start()
    try:
        with runner.process_capture.guarded_sigterm() as guard:
            with pytest.raises(RuntimeError, match="Interrupted attempt preserved"):
                runner.run(output)
            assert guard["received"]
    finally:
        cancel.set()
        sender.join(5)
    rows = json.loads((output / "results.json").read_text())
    assert sent.is_set() and len(rows) == 1 and rows[0]["interrupted"] and rows[0]["failed"]
    assert rows[0]["usage"]["total_tokens"] == 120 and rows[0]["cleanup_qualified"]
    assert rows[0]["output_drain_complete"] and rows[0]["lifecycle_qualified"]
    assert rows[0]["agent_cleanup"]["owned_group_stopped"]
    with pytest.raises(ProcessLookupError):
        os.killpg(rows[0]["agent_process"]["pid"], 0)
    summary = json.loads((output / "summary.json").read_text())
    assert len(summary["missing_attempts"]) == 2 and not (output / "completion.json").exists()


def test_public_result_keeps_invalid_line_receipts_without_private_reasoning():
    row = {"invalid_event_lines": [{"line": "private reasoning malformed JSON", "error_type": "ValueError"}]}
    published = runner.public_record(row, [])
    assert "private reasoning" not in json.dumps(published)
    assert published["invalid_event_lines"][0]["line_sha256"] == runner.hashlib.sha256(row["invalid_event_lines"][0]["line"].encode()).hexdigest()
    assert row["invalid_event_lines"][0]["line"] == "private reasoning malformed JSON"


def test_interrupt_at_cli_return_handoff_recovers_actual_private_checkpoint_before_publishing(tmp_path, monkeypatch):
    task, config, output = trial_fixture(tmp_path, monkeypatch)
    event = {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}
    def interrupted_handoff(command, config, workspace, raw_dir, **kwargs):
        runner.process_capture.write_checkpoint(raw_dir / "agent-measurements.json", {
            "completed": True, "agent_exit_code": 0, "agent_elapsed_s": 2,
            "usage": runner.evidence.provider_usage([event])["usage"],
            "cleanup_qualified": True, "output_drain_complete": True, "lifecycle_qualified": True})
        runner.process_capture.write_checkpoint(raw_dir / "observed-events.json", [{"event": event,
            "received": runner.mf.stamp()}])
        raise KeyboardInterrupt("signal between CLI return and caller assignment")
    monkeypatch.setattr(runner.process_capture, "run_agent", interrupted_handoff)
    publications = []
    monkeypatch.setattr(runner.mf, "public_artifacts", lambda raw, public, events, observed, substitutions:
        publications.append((events, observed)) or {})
    row = runner.run_trial(task, {"task_id": task["id"], "repetition": 1, "condition": "baseline"},
                           1, config, output, runner.ClockIntegrity())
    published = json.loads(next((output / "runs").glob("*/result.json")).read_text())
    assert published["usage"]["total_tokens"] == 120 and row["agent_elapsed_s"] == 2
    assert row["interrupted"] and row["failed"] and not row["completed_correct"]
    assert row["timing_qualified"] is False and row["cleanup_qualified"]
    assert publications[0][0] == [event] and len(publications[0][1]) == 1


def production_fixture(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "gate.py").write_text("frozen source")
    monkeypatch.setattr(runner, "ROOT", source)
    monkeypatch.setattr(runner.mf, "artifact", lambda path: str(path))
    directory = tmp_path / "production"
    directory.mkdir()
    plan = {"source_sha256": {"gate.py": runner.sha(source / "gate.py")}}
    (directory / "frozen-plan.json").write_text(json.dumps(plan))
    (directory / "results.json").write_text('{"actual":true}')
    certificate = {"checks_passed": True, "frozen_plan_sha256": runner.sha(directory / "frozen-plan.json"),
                   "source_sha256": plan["source_sha256"],
                   "artifact_sha256": {"results.json": runner.sha(directory / "results.json")}}
    (directory / "verification.json").write_text(json.dumps(certificate))
    return source, directory, certificate


def test_production_certificate_binds_plan_result_verification_and_current_source(tmp_path, monkeypatch):
    _, directory, _ = production_fixture(tmp_path, monkeypatch)
    binding = runner.production_certificate(directory)
    assert binding == {runner.mf.artifact(path): runner.sha(path) for path in directory.iterdir()}


@pytest.mark.parametrize("corrupt", ["failed", "plan", "source", "result", "source-map", "missing-source"])
def test_production_certificate_rejects_failed_stale_or_unbound_evidence(tmp_path, monkeypatch, corrupt):
    source, directory, certificate = production_fixture(tmp_path, monkeypatch)
    if corrupt == "failed":
        certificate["checks_passed"] = False
    elif corrupt == "plan":
        certificate["frozen_plan_sha256"] = "stale"
    elif corrupt == "source":
        (source / "gate.py").write_text("changed")
    elif corrupt == "result":
        (directory / "results.json").write_text("changed")
    elif corrupt == "source-map":
        certificate["source_sha256"] = {"gate.py": "different"}
    else:
        (directory / "frozen-plan.json").write_text('{"source_sha256":{}}')
        certificate["frozen_plan_sha256"] = runner.sha(directory / "frozen-plan.json")
    (directory / "verification.json").write_text(json.dumps(certificate))
    with pytest.raises(ValueError):
        runner.production_certificate(directory)


def test_common_freeze_binds_process_helper_and_both_production_certificates(tmp_path, monkeypatch):
    names = [runner.SELF, runner.PROTOCOL, Path("scripts/qwen_workflow_evidence.py"),
             Path("scripts/qwen_workflow_process.py"), Path("scripts/run_multifile_workflows.py"),
             Path("scripts/qwen_optimization_clock.py"), Path("src/semantic_search/__init__.py"),
             Path(".cache/reranker-experiment/.venv/bin/python")]
    for name in names:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(str(name))
    decision, fresh = tmp_path / "decision.json", tmp_path / "fresh"
    fresh.mkdir()
    decision.write_text("decision")
    for name in ("independent-audit.json", "frozen-plan.json"):
        (fresh / name).write_text(name)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "selected_profile", lambda *args: (
        {"retrieval": "hybrid", "shortlist": 24, "batch_size": 1}, {"reranker": {}}))
    monkeypatch.setattr(runner.mf, "freeze_common", lambda *args: {
        "runtime_git_revision": "commit", "artifact_sha256": {},
        "mcp": {"tool_defaults": {"retrieval": "dense", "rerank": False, "max_results": 8,
                                  "source_budget": 12000, "token_budget": 4096, "include_dependencies": True}}})
    monkeypatch.setattr(runner.mf.old, "git", lambda root, show, spec: SimpleNamespace(
        stdout=(root / spec.split(":", 1)[1]).read_bytes()))
    monkeypatch.setattr(runner.mf, "artifact", lambda path: str(path))
    monkeypatch.setattr(runner, "verify_native_runtime", lambda *args: None)
    seen = []
    def certificate(path):
        seen.append(path)
        return {str(path / "verification.json"): str(path.name)}
    monkeypatch.setattr(runner, "production_certificate", certificate)
    parity, smoke = tmp_path / "parity", tmp_path / "smoke"
    config = runner.freeze_common(tmp_path / "common", decision, fresh, parity, smoke)
    assert config["mcp"]["probe_tool_defaults"]["retrieval"] == "dense"
    assert config["mcp"]["condition_tool_defaults"]["mcp_fast"]["retrieval"] == "hybrid"
    assert config["mcp"]["condition_tool_defaults"]["mcp_fast"]["rerank"] is False
    qwen_defaults = config["mcp"]["condition_tool_defaults"]["mcp_qwen"]
    assert qwen_defaults["rerank"] is True and qwen_defaults["rerank_provider"] == "qwen"
    assert qwen_defaults["candidates"] == 24 and qwen_defaults["token_budget"] == 4096
    assert seen == [parity, smoke]
    assert config["artifact_sha256"]["scripts/qwen_workflow_process.py"] == runner.sha(tmp_path / "scripts/qwen_workflow_process.py")
    assert config["artifact_sha256"][str(parity / "verification.json")] == "parity"
    assert config["artifact_sha256"][str(smoke / "verification.json")] == "smoke"


def test_freeze_common_cli_requires_both_production_gates_before_freezing(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "freeze_common", lambda *args: pytest.fail("must require gates before freezing"))
    with pytest.raises(SystemExit):
        runner.main(["freeze-common", "--output", str(tmp_path), "--development-decision", "decision",
                     "--fresh-study", "fresh"])
