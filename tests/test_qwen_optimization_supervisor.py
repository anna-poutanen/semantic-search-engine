"""Injected ownership/lifecycle tests without subprocesses, model imports or sleeps."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/supervise_qwen_optimization.py"
spec = importlib.util.spec_from_file_location("optimization_supervisor_tested", SCRIPT)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Process:
    def __init__(self, pid, role, factory):
        self.pid, self.role, self.factory = pid, role, factory
        self.returncode, self.polls = None, 0

    def poll(self):
        self.polls += 1
        if self.role == "main" and self.factory.main_complete_after is not None and self.polls >= self.factory.main_complete_after:
            self.returncode = self.factory.main_exit
            self.factory.groups.discard(self.pid)
        return self.returncode

    def wait(self, timeout):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


class Factory:
    def __init__(self, *, ready=True, main_complete_after=2, main_exit=0, ignore_term=False, wrong_ready=False,
                 resident_exit_before_ready=False, spawn_error=None, interrupt=False, resident_dies_with_main=False):
        self.ready, self.main_complete_after, self.main_exit = ready, main_complete_after, main_exit
        self.ignore_term, self.wrong_ready = ignore_term, wrong_ready
        self.resident_exit_before_ready, self.spawn_error = resident_exit_before_ready, spawn_error
        self.interrupt, self.resident_dies_with_main = interrupt, resident_dies_with_main
        self.calls, self.processes, self.groups, self.signals = [], [], set(), []

    def __call__(self, args, **kwargs):
        assert kwargs["start_new_session"] is True
        self.calls.append((args, kwargs))
        role = ("resident" if any("resident_qwen_optimization.py" in arg for arg in args) else
                "main" if any("run_qwen_optimization.py" in arg for arg in args) else "caffeinate")
        if self.spawn_error == role:
            raise OSError("injected spawn failure")
        if self.interrupt and role == "main":
            raise supervisor.SupervisorInterrupted(signal.SIGINT)
        process = Process(100 + len(self.processes), role, self)
        self.processes.append(process)
        self.groups.add(process.pid)
        if role == "resident":
            token = Path(args[args.index("--token-file") + 1])
            assert token.stat().st_mode & 0o777 == 0o600
            assert len(token.read_text()) >= 32
            if self.resident_exit_before_ready:
                process.returncode = 1
                self.groups.discard(process.pid)
            elif self.ready:
                plan = Path(args[args.index("--frozen-plan") + 1])
                ready = Path(args[args.index("--ready") + 1])
                ready.write_text(json.dumps({"pid": process.pid + int(self.wrong_ready), "process_group": process.pid,
                    "url": "http://127.0.0.1:12345", "resident_ready": True, "generic_warmup_completed": True,
                    "frozen_plan_sha256": supervisor.sha(plan.read_bytes())}))
        if role == "main" and self.resident_dies_with_main:
            worker = next(item for item in self.processes if item.role == "resident")
            worker.returncode = 1
            self.groups.discard(worker.pid)
        return process

    def group_signal(self, pid, value):
        assert pid in {process.pid for process in self.processes}, "Cannot signal an unowned group"
        if pid not in self.groups:
            raise ProcessLookupError(pid)
        if value:
            self.signals.append((pid, value))
            if value == signal.SIGTERM and self.ignore_term:
                return
            process = next(item for item in self.processes if item.pid == pid)
            self.groups.remove(pid)
            process.returncode = (0 if process.role == "resident" and value == signal.SIGTERM else -int(value))


def fixture(tmp_path):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / ".cache").mkdir()
    study = root / "results/frozen-study"
    study.mkdir(parents=True)
    names = ["resident_qwen_optimization.py", "run_qwen_optimization.py", "qwen_reranker_batch.py", "supervise_qwen_optimization.py"]
    bindings = {}
    for name in names:
        file = root / "scripts" / name
        file.write_text("source " + name)
        bindings["scripts/" + name] = supervisor.sha(file.read_bytes())
    (study / "frozen-plan.json").write_text(json.dumps({"implementation_sha256": bindings}))
    return root, study


def execute(root, study, factory, *, timeout=7200, caffeinate_path=None):
    clock = Clock()
    result = supervisor.run(study, timeout, root=root, popen=factory, clock=clock, sleep=clock.sleep,
                            group_signal=factory.group_signal, caffeinate_path=caffeinate_path,
                            print_progress=lambda *args, **kwargs: None)
    return result, json.loads((study / "supervisor.json").read_text()), clock


def test_clean_success_closes_owned_groups_removes_token_and_preserves_start(tmp_path):
    root, study = fixture(tmp_path)
    factory = Factory()
    result, receipt, _ = execute(root, study, factory)
    assert result == 0 and receipt["success"] is True
    assert receipt["main_exit_code"] == receipt["resident_exit_code"] == 0
    assert receipt["main_owned_group_stopped"] and receipt["resident_owned_group_stopped"]
    assert not receipt["forced_kill"] and not receipt["timed_out"] and not factory.groups
    private = root / ".cache" / study.name
    assert not (private / "token.secret").exists()
    assert private.stat().st_mode & 0o777 == 0o700
    assert all((private / name).stat().st_mode & 0o777 == 0o600 for name in ("resident.log", "main.log", "ready.json"))
    initial = json.loads((study / "supervisor-start.json").read_text())
    assert "main_pid" not in initial and "resident_pid" not in initial
    assert (study / "supervisor-resident-start.json").exists() and (study / "supervisor-main-start.json").exists()
    assert receipt["frozen_plan_sha256"] == supervisor.sha((study / "frozen-plan.json").read_bytes())
    resident_args, _ = factory.calls[0]
    main_args, _ = factory.calls[1]
    assert "--frozen-plan" in resident_args and "--reranker-python" in main_args
    assert resident_args[0].endswith(".cache/reranker-experiment/.venv/bin/python")
    assert main_args[0].endswith(".venv/bin/python")


@pytest.mark.parametrize("options,error,timed_out", [
    ({"ready": False}, "Resident readiness timed out", True),
    ({"resident_exit_before_ready": True}, "Resident exited before readiness", False),
    ({"wrong_ready": True}, "Readiness process identity differs", False),
    ({"main_complete_after": None}, "Whole study timed out", True),
    ({"main_exit": 2}, "Main study exited unsuccessfully", False),
    ({"resident_dies_with_main": True, "main_complete_after": None}, "Resident exited while main", False),
    ({"spawn_error": "resident"}, "injected spawn failure", False),
    ({"spawn_error": "main"}, "injected spawn failure", False),
    ({"interrupt": True}, "Supervisor interrupted", False),
])
def test_failures_are_preserved_and_all_launched_groups_cleaned(tmp_path, options, error, timed_out):
    root, study = fixture(tmp_path)
    factory = Factory(**options)
    old_signals = {key: signal.getsignal(key) for key in (signal.SIGTERM, signal.SIGINT)}
    result, receipt, _ = execute(root, study, factory, timeout=190)
    assert result == 1 and receipt["success"] is False and error in receipt["error"]
    assert receipt["timed_out"] is timed_out and not factory.groups
    assert receipt["private_auth_token_removed"] and not (root / ".cache" / study.name / "token.secret").exists()
    assert {key: signal.getsignal(key) for key in old_signals} == old_signals
    if options.get("interrupt"):
        assert receipt["interrupted_signal"] == signal.SIGINT


def test_term_grace_then_forced_kill_are_bounded_and_make_run_fail(tmp_path):
    root, study = fixture(tmp_path)
    factory = Factory(ignore_term=True)
    result, receipt, clock = execute(root, study, factory)
    assert result == 1 and receipt["forced_kill"] and not factory.groups
    worker_stop = receipt["resident_stop"]
    assert worker_stop["term_sent"] and worker_stop["forced_kill"] and worker_stop["exit_code"] == -signal.SIGKILL
    assert supervisor.TERM_GRACE_S <= clock.value < supervisor.TERM_GRACE_S + 1


def test_owned_keepawake_is_created_and_stopped_without_requiring_zero_exit(tmp_path):
    root, study = fixture(tmp_path)
    caffeinate = tmp_path / "caffeinate"
    caffeinate.touch()
    factory = Factory()
    result, receipt, _ = execute(root, study, factory, caffeinate_path=caffeinate)
    assert result == 0 and receipt["caffeinate_owned_group_stopped"]
    assert receipt["caffeinate_exit_code"] == -signal.SIGTERM
    args = factory.calls[0][0]
    assert args[1:4] == ["-i", "-s", "-w"] and "-t" in args
    assert (study / "supervisor-keepawake-start.json").exists() and not factory.groups


def test_optional_keepawake_spawn_failure_does_not_prevent_model_study(tmp_path):
    root, study = fixture(tmp_path)
    caffeinate = tmp_path / "caffeinate"
    caffeinate.touch()
    factory = Factory(spawn_error="caffeinate")
    result, receipt, _ = execute(root, study, factory, caffeinate_path=caffeinate)
    assert result == 0 and receipt["keepawake_error"]["error_type"] == "OSError"
    assert "caffeinate_pid" not in receipt


def test_no_retry_or_overwrite_after_attempt(tmp_path):
    root, study = fixture(tmp_path)
    factory = Factory()
    execute(root, study, factory)
    before = {path.name: path.read_bytes() for path in study.iterdir()}
    with pytest.raises(ValueError, match="No retry/overwrite"):
        execute(root, study, Factory())
    assert before == {path.name: path.read_bytes() for path in study.iterdir()}


def test_existing_private_cache_is_not_reused(tmp_path):
    root, study = fixture(tmp_path)
    private = root / ".cache" / study.name
    private.mkdir()
    (private / "token.secret").write_text("old token")
    factory = Factory()
    with pytest.raises(ValueError, match="cache already exists"):
        execute(root, study, factory)
    assert not factory.calls and (private / "token.secret").read_text() == "old token"


def test_changed_frozen_source_blocks_before_process_or_token_creation(tmp_path):
    root, study = fixture(tmp_path)
    (root / "scripts/qwen_reranker_batch.py").write_text("changed")
    factory = Factory()
    assert supervisor.run(study, root=root, popen=factory, caffeinate_path=None) == 1
    receipt = json.loads((study / "supervisor-validation-failure.json").read_text())
    assert receipt["processes_launched"] == 0 and "Frozen implementation changed" in receipt["error"]
    assert not factory.calls and not (root / ".cache" / study.name).exists()


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf"), "1"])
def test_unbounded_or_invalid_timeouts_rejected_before_artifacts(tmp_path, timeout):
    root, study = fixture(tmp_path)
    with pytest.raises(ValueError, match="positive finite"):
        supervisor.run(study, timeout, root=root, caffeinate_path=None)
    assert [path.name for path in study.iterdir()] == ["frozen-plan.json"]


def test_json_receipts_never_overwrite_or_publish_private_home_paths(tmp_path):
    path = tmp_path / "receipt.json"
    supervisor.write_new_json(path, {"root": str(tmp_path), "home": str(Path.home())}, root=tmp_path)
    assert json.loads(path.read_text()) == {"root": "<PROJECT>", "home": "<HOME>"}
    with pytest.raises(FileExistsError):
        supervisor.write_new_json(path, {}, root=tmp_path)


def test_readiness_rejects_nonloopback_endpoint_or_wrong_hash(tmp_path):
    path = tmp_path / "ready.json"
    process = type("Process", (), {"pid": 1})()
    ready = {"pid": 1, "process_group": 1, "resident_ready": True, "generic_warmup_completed": True,
             "frozen_plan_sha256": "plan", "url": "http://example.com:1234"}
    for field, value, expected in [("url", "http://example.com:1234", "Loopback"),
                                   ("url", "http://127.0.0.1:1/path", "Invalid"),
                                   ("url", "http://127.0.0.1:65536", "Invalid"),
                                   ("frozen_plan_sha256", "wrong", "hash differs")]:
        item = ready | {"url": "http://127.0.0.1:1234", field: value}
        path.write_text(json.dumps(item))
        with pytest.raises(ValueError, match=expected):
            supervisor.validate_ready(path, process, "plan")


class ZombieRace:
    """A session leader exits but remains unreaped until its owner's next poll."""
    pid = 31337

    def __init__(self, *, exit_between_poll_and_probe=False):
        self.returncode = None
        self.state = "alive"
        self.exit_between_poll_and_probe = exit_between_poll_and_probe
        self.observations = []

    def poll(self):
        self.observations.append(("poll", self.state))
        if self.state == "zombie":
            self.state, self.returncode = "gone", 0
        return self.returncode

    def wait(self, timeout):
        return self.poll()

    def group_signal(self, pid, signum):
        assert pid == self.pid
        self.observations.append(("signal", signum, self.state))
        if self.state == "gone":
            raise ProcessLookupError(pid)
        if signum == 0 and self.state == "exit_pending":
            self.state = "zombie"
        if self.state == "zombie":
            raise PermissionError(1, "Unreaped session leader")
        if signum == signal.SIGTERM:
            self.state = "exit_pending" if self.exit_between_poll_and_probe else "zombie"
        elif signum == signal.SIGKILL:
            raise AssertionError("A cooperative zombie race must never trigger forced kill")


def test_reaps_just_terminated_leader_before_group_probe():
    process, clock = ZombieRace(), Clock()
    result = supervisor.stop_owned(process, clock=clock, sleep=clock.sleep, group_signal=process.group_signal)
    assert result["exit_code"] == 0 and result["owned_group_stopped"] and result["term_sent"]
    assert not result["errors"] and not result["forced_kill"] and not result["group_permission_retries"]
    assert ("signal", 0, "zombie") not in process.observations
    assert ("poll", "zombie") in process.observations


def test_preserves_transient_eperm_and_retries_only_after_fresh_poll():
    process, clock = ZombieRace(exit_between_poll_and_probe=True), Clock()
    result = supervisor.stop_owned(process, clock=clock, sleep=clock.sleep, group_signal=process.group_signal)
    assert result["exit_code"] == 0 and result["owned_group_stopped"] and not result["errors"]
    assert len(result["group_permission_retries"]) == 1
    retry = result["group_permission_retries"][0]
    assert retry["error_type"] == "PermissionError" and retry["recovered"]
    assert retry["leader_exit_before_fresh_poll"] is None and retry["leader_exit_after_fresh_poll"] == 0
    assert "disappeared" in retry["resolution"]
    failed_probe = process.observations.index(("signal", 0, "exit_pending"))
    assert process.observations[failed_probe + 1:failed_probe + 3] == [("poll", "zombie"), ("signal", 0, "gone")]


@pytest.mark.parametrize("leader_exit", [None, 0])
def test_persistent_eperm_does_not_certify_live_group_as_gone(leader_exit):
    calls, clock = [], Clock()

    class RestrictedGroup:
        pid = 31338
        returncode = leader_exit

        def poll(self):
            calls.append("poll")
            return self.returncode

    def restricted_signal(pid, value):
        assert pid == 31338 and value == 0
        calls.append("probe")
        raise PermissionError(1, "Live owned group is not signalable")

    result = supervisor.stop_owned(RestrictedGroup(), clock=clock, sleep=clock.sleep, group_signal=restricted_signal)
    assert not result["owned_group_stopped"] and not result["term_sent"] and not result["forced_kill"]
    assert result["errors"][0]["error_type"] == "PermissionError"
    assert result["exit_code"] == leader_exit
    assert not result["group_permission_retries"][0]["recovered"]
    assert calls[:4] == ["poll", "probe", "poll", "probe"]


def test_cleanup_receipt_is_successful_for_cooperative_unreaped_worker(tmp_path):
    root, study = fixture(tmp_path)

    class ZombieFactory(Factory):
        def __init__(self):
            super().__init__()
            self.zombies = set()

        def __call__(self, args, **kwargs):
            process = super().__call__(args, **kwargs)
            original_poll = process.poll

            def poll():
                if process.pid in self.zombies:
                    self.zombies.remove(process.pid)
                    self.groups.discard(process.pid)
                    process.returncode = 0
                return original_poll()

            process.poll = poll
            return process

        def group_signal(self, pid, value):
            if pid in self.zombies:
                raise PermissionError(1, "Zombie not yet reaped")
            process = next(item for item in self.processes if item.pid == pid)
            if process.role == "resident" and value == signal.SIGTERM:
                self.signals.append((pid, value))
                self.zombies.add(pid)
                return
            return super().group_signal(pid, value)

    result, receipt, _ = execute(root, study, ZombieFactory())
    assert result == 0 and receipt["success"] and receipt["resident_exit_code"] == 0
    assert receipt["resident_owned_group_stopped"] and not receipt["resident_stop"]["errors"]
    assert receipt["private_auth_token_removed"] and not receipt["forced_kill"]


def test_real_owned_cpu_child_stops_cleanly_and_is_reaped():
    # A real session/process test, with no MLX/model/network imports or GPU work.
    child = subprocess.Popen([sys.executable, "-u", "-c",
        "import signal,sys,time; signal.signal(signal.SIGTERM,lambda *_:sys.exit(0)); "
        "print('ready',flush=True); time.sleep(5)"], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        assert select.select([child.stdout], [], [], 5)[0], "Owned CPU child failed to become ready"
        assert child.stdout.readline().strip() == "ready"
        result = supervisor.stop_owned(child)
        assert result["owned_group_stopped"] and result["exit_code"] == 0 and result["term_sent"]
        assert not result["errors"] and not result["forced_kill"]
        assert child.poll() == 0
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()
