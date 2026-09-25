"""Actual CPU subprocess signal tests; no native model or hosted requests."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from semantic_search import cli
from test_qwen_production_process import FAKE_WORKER


HARNESS = '''
import json, os, signal, subprocess, sys, time
from pathlib import Path
from semantic_search import cli, backend
from semantic_search.qwen_reranker import QwenRerankerProcess
root=Path(sys.argv[1]); mode=sys.argv[2]
real_popen=subprocess.Popen
def popen(argv, **settings):
    state=argv[argv.index('--state-dir')+1]
    return real_popen([sys.executable,'-B',str(root/'cpu-worker.py'),'--state-dir',state,
        '--checkpoint',str(root),'--owner-pid',argv[argv.index('--owner-pid')+1]], **settings)
subprocess.Popen=popen
owner=None
def start(args):
    global owner
    owner=QwenRerankerProcess(python=sys.executable,checkpoint=root,log_dir=root/'private',startup_timeout=5)
    owner.start()
    return owner
def ready():
    (root/'parent-ready.json').write_text(json.dumps({'owner_pid':os.getpid(),
        'worker_pid':owner.process.pid,'directory':str(owner.directory)}))
class Backend:
    def __init__(self,*args,**kwargs): pass
    def search(self,*args,**kwargs):
        ready()
        while True: time.sleep(.05)
    def close(self): pass
if mode=='cli':
    previous=signal.getsignal(signal.SIGTERM)
    cli.start_qwen_owner=start
    backend.SearchBackend=Backend
    try:
        cli.main(['search','behavior','--repo',str(root),'--rerank','--rerank-provider','qwen'])
    except KeyboardInterrupt:
        (root/'parent-stop.json').write_text(json.dumps({'interrupted':True,
            'handler_restored':signal.getsignal(signal.SIGTERM)==previous,'receipt':owner.receipt}))
else:
    start(None)
    ready()
    while True: time.sleep(.05)
'''


def launch_cpu_parent(tmp_path, mode):
    (tmp_path / "cpu-worker.py").write_text(FAKE_WORKER)
    harness = tmp_path / "cpu-parent.py"
    harness.write_text(HARNESS)
    env = dict(os.environ, PYTHONPATH=str(Path(cli.__file__).resolve().parents[1]),
               PYTHONDONTWRITEBYTECODE="1")
    parent = subprocess.Popen([sys.executable, "-B", str(harness), str(tmp_path), mode],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    deadline = time.monotonic() + 10
    marker = tmp_path / "parent-ready.json"
    while not marker.exists() and parent.poll() is None and time.monotonic() < deadline:
        time.sleep(.02)
    if not marker.exists():
        parent.kill()
        _, errors = parent.communicate(timeout=5)
        pytest.fail("CPU parent did not become ready: " + errors.decode()[-1000:])
    record = json.loads(marker.read_text())
    assert record["owner_pid"] == parent.pid
    assert Path(record["directory"]).is_relative_to(tmp_path)
    return parent, record


def group_gone(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # An EPERM alone does not prove disappearance.
    return False


def cleanup_cpu_fixture(parent, worker_pid):
    if parent.poll() is None:
        os.killpg(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
    if not group_gone(worker_pid):
        try:
            os.killpg(worker_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    parent.communicate(timeout=5)


def test_standalone_cli_sigterm_closes_actual_owned_cpu_worker_and_restores_handler(tmp_path):
    parent, record = launch_cpu_parent(tmp_path, "cli")
    try:
        os.kill(parent.pid, signal.SIGTERM)
        parent.wait(timeout=10)
        stopped = json.loads((tmp_path / "parent-stop.json").read_text())
        assert parent.returncode == 0 and stopped["interrupted"] and stopped["handler_restored"]
        assert stopped["receipt"]["stop"]["owned_group_stopped"]
        assert stopped["receipt"]["auth_token_removed"]
        assert stopped["receipt"]["worker_stop"]["clean_stop"]
        assert not (Path(record["directory"]) / "token.secret").exists()
    finally:
        cleanup_cpu_fixture(parent, record["worker_pid"])


def test_worker_self_stops_and_removes_token_after_bound_owner_sigkill(tmp_path):
    parent, record = launch_cpu_parent(tmp_path, "watchdog")
    try:
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        directory = Path(record["directory"])
        deadline = time.monotonic() + 10
        while not (directory / "stop.json").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        stopped = json.loads((directory / "stop.json").read_text())
        assert stopped["clean_stop"] and stopped["stop_reason"] == "owner_exited"
        assert stopped["listener_closed"] and stopped["auth_token_removed"]
        assert stopped["counts"]["completed_forwards"] == 1  # CPU toy warmup only.
        assert not (directory / "token.secret").exists()
        while not group_gone(record["worker_pid"]) and time.monotonic() < deadline:
            time.sleep(.02)
        assert group_gone(record["worker_pid"])
    finally:
        cleanup_cpu_fixture(parent, record["worker_pid"])


def test_already_orphaned_worker_rejects_before_loading_and_removes_private_token(tmp_path):
    from semantic_search.qwen_worker import serve
    tmp_path.chmod(0o700)
    token = tmp_path / "token.secret"
    token.write_text("cpu-private-token-" + "x" * 40)
    token.chmod(0o600)
    calls = []
    def loader(*args, **kwargs):
        calls.append("unexpected loader invocation")
        raise AssertionError("An orphan must never load model weights")
    with pytest.raises(InterruptedError, match="owner exited"):
        serve(tmp_path, tmp_path, loader=loader, owner_pid=os.getppid() + 100000)
    assert calls == [] and not token.exists()
    stopped = json.loads((tmp_path / "stop.json").read_text())
    assert stopped["stop_reason"] == "owner_exited" and stopped["auth_token_removed"]
    assert not stopped["clean_stop"] and not stopped["listener_closed"]
