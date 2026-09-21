"""Actual owned CPU subprocesses and interrupted usage; no hosted/model calls."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/qwen_workflow_process.py"
SPEC = importlib.util.spec_from_file_location("workflow_capture_tested", SCRIPT)
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)

USAGE = {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 70,
          "output_tokens": 20, "reasoning_output_tokens": 10}}


def environment(*_):
    return dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def run(tmp_path, source, *, timeout=3, **settings):
    return capture.run_agent([sys.executable, "-u", "-c", source], {"timeout_s": timeout},
                             tmp_path, tmp_path / "raw", environment=environment, **settings)


def gone(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def test_actual_completed_cli_preserves_raw_events_timestamps_usage_and_private_checkpoint(tmp_path):
    events = [{"type": "item.started", "item": {"id": "tool", "type": "command_execution"}},
              {"type": "item.completed", "item": {"id": "tool", "type": "command_execution"}}, USAGE]
    wire = "".join(json.dumps(x) + "\n" for x in events)
    result, parsed, observed = run(tmp_path, f"import sys; sys.stdout.write({wire!r}); sys.stderr.write('actual stderr')")
    assert parsed == events and (tmp_path / "raw/events.jsonl").read_text() == wire
    assert (tmp_path / "raw/stderr.txt").read_text() == "actual stderr"
    assert result["usage"]["total_tokens"] == 120 and result["usage"]["uncached_input_tokens"] == 30
    assert result["completed"] and not result["failed"] and result["reported_turns"] == 1
    assert result["agent_exit_code"] == 0 and result["cleanup_qualified"] and result["output_drain_complete"]
    assert result["cleanup_clean"]
    assert result["agent_end"]["monotonic_s"] <= result["agent_cleanup"]["capture_cleanup_end"]["monotonic_s"]
    assert result["command_calls"] == 1 and len(result["tool_intervals"]) == 1
    assert len(observed) == 3 and all(type(x["received"]["wall_time_ns"]) is int for x in observed)
    pid = json.loads((tmp_path / "raw/agent-process.json").read_bytes())["pid"]
    assert pid != os.getpid() and gone(pid)
    checkpoint = json.loads((tmp_path / "raw/agent-measurements.json").read_bytes())
    assert checkpoint["usage"] == result["usage"] and checkpoint["lifecycle_qualified"]
    assert (tmp_path / "raw/agent-measurements.json").stat().st_mode & 0o777 == 0o600


def test_timeout_preserves_reported_prefix_usage_and_reaps_only_owned_group(tmp_path):
    source = f"import json,time; print(json.dumps({USAGE!r}),flush=True); time.sleep(30)"
    result, events, _ = run(tmp_path, source, timeout=.5)
    assert result["timed_out"] and result["completed"] and result["usage"]["total_tokens"] == 120
    assert result["cleanup_qualified"] and result["agent_exit_code"] == -signal.SIGTERM
    assert len(events) == 1 and gone(result["agent_process"]["pid"])
    assert result["agent_cleanup"]["term_sent"] and not result["agent_cleanup"]["forced_kill"]


def test_unreported_interrupted_turn_usage_stays_unknown(tmp_path):
    result, events, _ = run(tmp_path, "import time; print('{\"type\":\"turn.started\"}',flush=True); time.sleep(30)", timeout=.4)
    assert result["usage"] is None and result["reported_turns"] == 0
    assert len(events) == 1 and not result["completed"] and result["timed_out"]


def test_force_kill_is_bounded_recorded_and_cannot_be_normal_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "TERM_GRACE_S", .1)
    monkeypatch.setattr(capture, "KILL_GRACE_S", .3)
    source = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('{\"type\":\"turn.started\"}',flush=True); time.sleep(30)"
    result, _, _ = run(tmp_path, source, timeout=.4)
    assert result["agent_cleanup"]["forced_kill"] and result["agent_exit_code"] == -signal.SIGKILL
    assert result["cleanup_qualified"] and gone(result["agent_process"]["pid"])
    assert not capture.evidence.complete(result)


@pytest.mark.parametrize("wire", ["not-json\n", "[]\n", '{"type":"x","type":"y"}\n',
    '{"type":"turn.completed","usage":{"input_tokens":NaN}}\n', '{"type":"x","value":1e999}\n'])
def test_invalid_lines_are_retained_without_losing_actual_completed_turn_usage(tmp_path, wire):
    wire += json.dumps(USAGE) + "\n"
    result, events, _ = run(tmp_path, f"import sys;sys.stdout.write({wire!r})")
    assert result["failed"] and len(result["invalid_event_lines"]) == 1
    assert result["usage"]["total_tokens"] == 120 and events == [USAGE]
    assert (tmp_path / "raw/events.jsonl").read_text() == wire


def test_partial_unterminated_stdout_line_is_preserved_after_interrupt(tmp_path):
    prefix = json.dumps(USAGE) + "\n" + '{"type":"item.started"'
    result, events, _ = run(tmp_path, f"import sys,time;sys.stdout.write({prefix!r});sys.stdout.flush();time.sleep(30)", timeout=.4)
    assert result["usage"]["total_tokens"] == 120 and len(result["invalid_event_lines"]) == 1
    assert events == [USAGE] and (tmp_path / "raw/events.jsonl").read_text() == prefix


def test_json_item_invalid_id_cannot_prevent_owned_cleanup_or_known_usage(tmp_path):
    values = [{"type": "item.completed", "item": {"id": [], "type": "command_execution"}}, USAGE]
    result, _, _ = run(tmp_path, f"import json;print(json.dumps({values[0]!r}));print(json.dumps({values[1]!r}))")
    assert result["cleanup_qualified"] and result["usage"]["total_tokens"] == 120


def test_leader_exit_agent_time_does_not_include_separate_cleanup_delay(tmp_path):
    def delayed(process):
        time.sleep(.2)
        return capture.stop_owned(process)
    result, _, _ = run(tmp_path, f"print({json.dumps(USAGE)!r})", stopper=delayed)
    assert result["agent_cleanup"]["capture_cleanup_s"] >= .2
    assert result["agent_end"]["monotonic_s"] <= result["agent_cleanup"]["capture_cleanup_start"]["monotonic_s"]
    assert result["agent_elapsed_s"] < result["agent_cleanup"]["capture_cleanup_end"]["monotonic_s"] - result["agent_start"]["monotonic_s"] - .15


def test_checkpoint_failure_still_stops_owned_cli_and_retains_prefix_measurements(tmp_path, monkeypatch):
    actual = capture.write_checkpoint
    def broken(path, value):
        if path.name == "agent-measurements.json":
            raise OSError("Injected checkpoint write failure")
        actual(path, value)
    monkeypatch.setattr(capture, "write_checkpoint", broken)
    result, events, _ = run(tmp_path, f"import json,time;print(json.dumps({USAGE!r}),flush=True);time.sleep(30)", timeout=.4)
    assert result["failed"] and result["cleanup_qualified"] and result["capture_errors"]
    assert result["usage"]["total_tokens"] == 120 and events == [USAGE]
    assert gone(result["agent_process"]["pid"])


def test_startup_failure_has_unknown_usage_no_fake_cli_exit_and_an_actual_checkpoint(tmp_path):
    result, events, _ = capture.run_agent([str(tmp_path / "missing")], {"timeout_s": 1}, tmp_path,
                                         tmp_path / "raw", environment=environment)
    assert result["failed"] and result["usage"] is None and events == []
    assert result["agent_end"] is result["agent_elapsed_s"] is result["agent_exit_code"] is None
    assert result["agent_process"]["launched"] is False and result["cleanup_qualified"]


def test_injected_unproved_shutdown_cannot_qualify_normal_completion(tmp_path):
    result, _, _ = run(tmp_path, f"print({json.dumps(USAGE)!r})",
                       stopper=lambda process: {"owned_group_stopped": False})
    assert result["completed"] and result["failed"] and result["lifecycle_qualified"] is False
    assert result["timing_qualified"] is False


@pytest.mark.parametrize("unclean", [{"forced_kill": True}, {"errors": [{"type": "InjectedCleanupError"}]}])
def test_completed_cli_with_known_usage_and_stopped_group_is_not_correct_after_unclean_cleanup(tmp_path, unclean):
    def stop(process):
        return {**capture.stop_owned(process), **unclean}
    result, events, _ = run(tmp_path, f"print({json.dumps(USAGE)!r})", stopper=stop)
    assert result["agent_exit_code"] == 0 and result["completed"] and result["usage"]["total_tokens"] == 120
    assert events == [USAGE] and result["lifecycle_qualified"] and result["output_drain_complete"]
    assert result["cleanup_qualified"] and result["cleanup_clean"] is False and result["failed"]
    assert result.get("timing_qualified", True) is True and not capture.evidence.complete(result)
    assert gone(result["agent_process"]["pid"])


def test_signal_during_actual_launch_keeps_ownership_and_restores_previous_policy(tmp_path, monkeypatch):
    popen = capture.subprocess.Popen
    def interrupted(*args, **kwargs):
        process = popen(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGTERM)
        return process
    monkeypatch.setattr(capture.subprocess, "Popen", interrupted)
    previous = signal.getsignal(signal.SIGTERM)
    with capture.guarded_sigterm() as guard:
        result, _, _ = run(tmp_path, "import time;time.sleep(30)")
        assert guard["received"] and result["interrupted"] and result["cleanup_qualified"]
        assert result["interruption_signal"] == signal.SIGTERM and gone(result["agent_process"]["pid"])
    assert signal.getsignal(signal.SIGTERM) == previous


def test_first_signal_during_cleanup_is_retained_without_abandoning_owned_group(tmp_path):
    def interrupt(process):
        os.kill(os.getpid(), signal.SIGTERM)
        return capture.stop_owned(process)
    previous = signal.getsignal(signal.SIGTERM)
    with capture.guarded_sigterm() as guard:
        result, _, _ = run(tmp_path, f"print({json.dumps(USAGE)!r})", stopper=interrupt)
        assert result["interrupted"] and result["failed"] and result["cleanup_qualified"]
        assert guard["received"]
    assert signal.getsignal(signal.SIGTERM) == previous


def wait_for(path, process, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_bytes())
        if process.poll() is not None:
            raise AssertionError("CPU controller exited before interim checkpoint")
        time.sleep(.01)
    raise AssertionError("CPU controller failed to write interim checkpoint")


def test_actual_sigterm_controller_retains_interim_usage_ignores_repeats_and_reaps_cpu_cli(tmp_path):
    controller = tmp_path / "controller.py"
    raw = tmp_path / "raw"
    source = f"import json,time;print(json.dumps({USAGE!r}),flush=True);time.sleep(30)"
    controller.write_text(f'''import importlib.util,json,os,sys,time
from pathlib import Path
s=importlib.util.spec_from_file_location("capture",{str(SCRIPT)!r})
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.TERM_GRACE_S=.5;m.KILL_GRACE_S=.5
def stop(p):
    time.sleep(.2)
    return m.stop_owned(p)
with m.guarded_sigterm() as guard:
    result,events,observed=m.run_agent([sys.executable,"-u","-c",{source!r}],{{"timeout_s":10}},Path({str(tmp_path)!r}),Path({str(raw)!r}),environment=lambda *a:dict(os.environ),stopper=stop)
    Path({str(tmp_path / 'returned.json')!r}).write_text(json.dumps({{"result":result,"guard":guard,"events":events}}))
''')
    process = subprocess.Popen([sys.executable, str(controller)], start_new_session=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_for(raw / "agent-process.json", process)
        deadline = time.monotonic() + 5
        interim = None
        while time.monotonic() < deadline:
            interim = wait_for(raw / "agent-measurements.json", process)
            if (interim.get("usage") or {}).get("total_tokens") == 120:
                break
            time.sleep(.01)
        assert interim["usage"]["total_tokens"] == 120
        cli_pid = interim["agent_process"]["pid"]
        os.kill(process.pid, signal.SIGTERM)
        time.sleep(.05)
        os.kill(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
        payload = json.loads((tmp_path / "returned.json").read_bytes())
        result = payload["result"]
        assert process.returncode == 0 and result["interrupted"] and result["failed"]
        assert result["interruption_signal"] == signal.SIGTERM and result["usage"]["total_tokens"] == 120
        assert result["cleanup_qualified"] and result["lifecycle_qualified"] and result["timing_qualified"] is False
        assert payload["guard"]["received"] and gone(cli_pid)
        assert payload["events"] == [USAGE]
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
        pid_file = raw / "agent-process.json"
        if pid_file.is_file():
            owned_pid = json.loads(pid_file.read_bytes())["pid"]
            if not gone(owned_pid):
                os.killpg(owned_pid, signal.SIGKILL)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize("bad", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_timeouts_never_launch_or_create_fake_receipts(tmp_path, bad):
    with pytest.raises(ValueError):
        run(tmp_path, "raise AssertionError('must not execute')", timeout=bad)
    assert not (tmp_path / "raw").exists()
