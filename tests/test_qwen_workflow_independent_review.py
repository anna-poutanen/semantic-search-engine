"""Independent CPU lifecycle/accounting regressions; no model or hosted calls."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("qwen_workflow_independently_reviewed", ROOT / "scripts/run_qwen_workflows.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def run_fixture(tmp_path, monkeypatch):
    task = {"id": "requests_response_limit"}
    trials = [{"task_id": task["id"], "repetition": 1, "condition": condition}
              for condition in runner.evidence.CONDITIONS]
    config = {"manifest": "unused.json", "schedule": trials, "reranker_python": sys.executable,
              "reranker_python_sha256": runner.sha(sys.executable)}
    (tmp_path / "frozen-config.json").write_text(json.dumps(config))
    monkeypatch.setattr(runner.mf, "verify_runtime", lambda config: None)
    monkeypatch.setattr(runner, "verify_native_runtime", lambda config: None)
    monkeypatch.setattr(runner, "validate_manifest", lambda *args: {"tasks": [task]})
    return trials


def test_between_trial_clock_failure_preserves_receipt_and_leaves_all_later_attempts_unstarted(tmp_path, monkeypatch):
    trials = run_fixture(tmp_path, monkeypatch)
    failed_receipt = {"label": "trial-start", "consistent": False, "cumulative": {"absolute_drift_ns": 3_000_000_000}}
    attempted = []
    def fail_clock(task, trial, *args):
        attempted.append(trial)
        raise runner.ClockIntegrityError(failed_receipt)
    monkeypatch.setattr(runner, "run_trial", fail_clock)
    with pytest.raises(RuntimeError, match="Clock failure"):
        runner.run(tmp_path)
    records = json.loads((tmp_path / "results.json").read_text())
    assert attempted == trials[:1] and len(records) == 1
    assert records[0]["clock_failure"] == failed_receipt and records[0]["timing_qualified"] is False
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert not summary["inventory_complete"] and len(summary["missing_attempts"]) == 2
    assert not (tmp_path / "completion.json").exists()


def test_unproved_model_group_shutdown_blocks_any_later_trial_launch(tmp_path, monkeypatch):
    trials = run_fixture(tmp_path, monkeypatch)
    attempted = []
    def failed_stop(task, trial, *args):
        attempted.append(trial)
        return {"run_id": "failed-stop", **trial, "correct": True, "completed_correct": False,
                "completed": True, "agent_exit_code": 0, "failed": True, "timed_out": False,
                "usage": None, "mcp_calls": 0, "timing_qualified": True, "lifecycle_qualified": False,
                "qwen_stop": {"owned_group_stopped": False}}
    monkeypatch.setattr(runner, "run_trial", failed_stop)
    with pytest.raises(RuntimeError, match="Unproved model/listener cleanup"):
        runner.run(tmp_path)
    assert attempted == trials[:1]
    records = json.loads((tmp_path / "results.json").read_text())
    assert records[0]["correct"] and not records[0]["completed_correct"]
    assert len(json.loads((tmp_path / "summary.json").read_text())["missing_attempts"]) == 2


def test_worker_start_failure_never_calls_blocking_shutdown_of_unstarted_listener(tmp_path, monkeypatch):
    output = tmp_path / "public"
    output.mkdir()
    task = {"id": "requests_response_limit", "prompt": "Add a feature.", "source_root": "src",
            "seed_validation": {"base": "base"}}
    config = {"work_root": str(tmp_path / "private"), "python": sys.executable}
    def prepare(task, workspace):
        workspace.mkdir(parents=True)
        return "commit", {"src/file.py": "hash"}
    grades = []
    def grade(task, workspace):
        grades.append(workspace)
        return {"passed": len(grades) > 1, "projection": "base"}
    closed = []
    backend = SimpleNamespace(refresh=lambda: {}, search=lambda *a, **k: {}, start_watching=lambda: None,
                              close=lambda: closed.append("backend"))
    server = SimpleNamespace(server_port=9999, serve_forever=lambda: None,
                             shutdown=lambda: pytest.fail("shutdown would wait forever without serve_forever"),
                             server_close=lambda: closed.append("server"))
    class FailedThread:
        ident = None
        def __init__(self, **kwargs):
            pass
        def start(self):
            raise RuntimeError("controlled thread creation failure")
        def is_alive(self):
            return False
        def join(self, *args):
            pytest.fail("must not join an unstarted thread")
    monkeypatch.setattr(runner.mf, "prepare", prepare)
    monkeypatch.setattr(runner, "grade", grade)
    monkeypatch.setattr(runner.mf, "compatibility", lambda *a: {"passed": True})
    monkeypatch.setattr(runner.mf.old, "git", lambda *a, **k: SimpleNamespace(stdout=b""))
    monkeypatch.setattr(runner.mf, "public_artifacts", lambda *a: {})
    monkeypatch.setattr(runner, "recording_backend", lambda *a: backend)
    monkeypatch.setattr("semantic_search.server.create_server", lambda *a, **k: server)
    monkeypatch.setattr(runner.threading, "Thread", FailedThread)
    monkeypatch.setattr(runner.mf, "run_agent", lambda *a: pytest.fail("must not start hosted work"))
    record = runner.run_trial(task, {"task_id": task["id"], "repetition": 1, "condition": "mcp_fast"},
                              1, config, output, runner.ClockIntegrity())
    assert closed == ["server", "backend"] and record["failed"] and not record["completed"]
    assert "controlled thread creation failure" in record["infrastructure_error"]


@pytest.mark.parametrize("clean_stop, complete", [(True, True), (False, False)])
def test_native_ledger_keeps_failed_forward_and_excludes_warmup_without_publishing_auth(tmp_path, clean_stop, complete):
    private, public = tmp_path / "private", tmp_path / "public"
    private.mkdir()
    public.mkdir()
    rows = [{"request_id": "warmup", "warmup": True, "event": "started"},
            {"request_id": "warmup", "warmup": True, "event": "completed"},
            {"request_id": "real-0", "warmup": False, "event": "started"},
            {"request_id": "real-0", "warmup": False, "event": "failed", "error_type": "RuntimeError"}]
    (private / "forwards.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (private / "token.secret").write_text("fake-private-auth-token-do-not-publish")
    owner = SimpleNamespace(directory=private, receipts={"worker_stop": {"clean_stop": clean_stop,
                            "counts": {"attempted_forwards": 2, "completed_forwards": 1}}})
    receipt = runner.publish_qwen_evidence(owner, public, [])
    assert receipt["observed_qwen_attempted_forwards"] == 1 and receipt["observed_qwen_completed_forwards"] == 0
    assert receipt["native_counts_complete"] is complete
    assert set(receipt["native_artifacts"]) == {"forwards.jsonl"}
    assert not (public / "qwen/token.secret").exists()
    assert (private / "token.secret").read_text() == "fake-private-auth-token-do-not-publish"
