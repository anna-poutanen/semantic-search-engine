"""Owned CPU child-process lifecycle tests; subprocesses never import MLX."""
import json
import subprocess
import sys

import pytest

from semantic_search.qwen_reranker import QwenRerankerProcess, stop_owned


FAKE_WORKER = '''
import argparse
from semantic_search.qwen_worker import serve, ScoringEngine
from semantic_search.qwen_reranker_core import PairBuilder, binary_score
class Tokenizer:
    def encode(self, text, add_special_tokens=False):
        return [10001 if text == "yes" else 10002] if text in {"yes","no"} else [ord(c) for c in text]
def load(checkpoint, emit, *, owner_check=lambda: None):
    owner_check()
    return ScoringEngine(PairBuilder(Tokenizer()), lambda pair: binary_score(3,1),
                         provenance={"cpu_injected_test_only": True}, emit=emit), None
parser=argparse.ArgumentParser()
parser.add_argument("--state-dir", type=__import__("pathlib").Path)
parser.add_argument("--checkpoint", type=__import__("pathlib").Path)
parser.add_argument("--owner-pid", type=int)
args=parser.parse_args()
serve(args.checkpoint,args.state_dir,loader=load,owner_pid=args.owner_pid)
'''


def owned_cpu_process(tmp_path, monkeypatch, script=FAKE_WORKER, startup_timeout=5):
    fake = tmp_path / "explicit_cpu_worker.py"
    fake.write_text(script)
    checkpoint = tmp_path / "unused_cpu_checkpoint"
    checkpoint.mkdir()
    real_popen = subprocess.Popen
    launches = []
    def popen(argv, **settings):
        launches.append((argv, settings))
        state = argv[argv.index("--state-dir") + 1]
        return real_popen([sys.executable, "-B", str(fake), "--state-dir", state,
                          "--checkpoint", str(checkpoint), "--owner-pid", argv[argv.index("--owner-pid") + 1]], **settings)
    monkeypatch.setattr("semantic_search.qwen_reranker.subprocess.Popen", popen)
    owner = QwenRerankerProcess(python=sys.executable, checkpoint=checkpoint,
        log_dir=tmp_path / "private", startup_timeout=startup_timeout, pair_timeout=2)
    return owner, launches


def test_owner_actual_cpu_subprocess_warms_once_scores_and_releases_owned_group(tmp_path, monkeypatch):
    owner, launches = owned_cpu_process(tmp_path, monkeypatch)
    from types import SimpleNamespace
    try:
        client = owner.start()
        assert client is owner.client and owner.provenance["cpu_injected_test_only"] is True
        assert owner.status()["counts"]["completed_forwards"] == 1
        secret = owner._token_path.read_text()
        client.score("behavior", SimpleNamespace(text="def x(): pass"))
        assert owner.status()["counts"]["completed_forwards"] == 2
    finally:
        owner.close()
    assert launches[0][1]["start_new_session"] is True
    runtime_root = owner.directory / "runtime"
    assert launches[0][1]["env"]["PYTHONPATH"] == str(runtime_root)
    assert {path.name for path in (runtime_root / "semantic_search").iterdir()} == {
        "__init__.py", "client.py", "qwen_reranker_core.py", "qwen_worker.py"}
    from semantic_search.qwen_reranker_core import sha256
    assert owner.receipt["worker_source_sha256"] == {
        path.name: sha256(path.read_bytes()) for path in (runtime_root / "semantic_search").iterdir()}
    assert owner.receipt["auth_token_removed"] is True
    assert owner.stop_receipt["owned_group_stopped"] is True
    assert owner.stop_receipt["exit_code"] == 0
    assert owner.receipt["worker_stop"]["clean_stop"] is True
    assert owner.receipt["worker_stop"]["counts"]["completed_forwards"] == 2
    assert secret not in json.dumps(owner.receipt)
    assert owner.directory.stat().st_mode & 0o777 == 0o700
    assert (owner.directory / "ready.json").stat().st_mode & 0o777 == 0o600
    assert owner.close() == owner.stop_receipt
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        owner.start()


@pytest.mark.parametrize("script,timeout,error", [
    ("raise SystemExit(7)", 5, RuntimeError),
    ("import time; time.sleep(5)", .1, TimeoutError),
])
def test_start_failure_stops_owned_group_and_removes_token(tmp_path, monkeypatch, script, timeout, error):
    owner, _ = owned_cpu_process(tmp_path, monkeypatch, script, timeout)
    with pytest.raises(error):
        owner.start()
    assert owner.receipt["auth_token_removed"]
    assert owner.stop_receipt["owned_group_stopped"]
    assert owner.receipt["startup_error"]["error_type"] == error.__name__


def test_invalid_local_setup_does_not_launch_or_import_model(tmp_path):
    owner = QwenRerankerProcess(python=tmp_path / "missing", checkpoint=tmp_path)
    with pytest.raises(ValueError):
        owner.start()
    assert owner.process is None and owner.receipt["auth_token_removed"]


def test_timed_out_singleton_stops_owned_worker_before_returning_error(tmp_path, monkeypatch):
    script = FAKE_WORKER.replace("lambda pair: binary_score(3,1)",
        "lambda pair: (__import__('time').sleep(1) or binary_score(3,1)) "
        "if 'slow_marker' in pair['source'] else binary_score(3,1)")
    owner, _ = owned_cpu_process(tmp_path, monkeypatch, script)
    owner.pair_timeout = .1
    from types import SimpleNamespace
    client = owner.start()
    with pytest.raises(RuntimeError):
        client.score("behavior", SimpleNamespace(text="def slow_marker(): pass"))
    assert owner.stop_receipt["owned_group_stopped"]
    assert owner.receipt["auth_token_removed"]
    assert client.safe_for_embedding
    with pytest.raises(RuntimeError, match="disabled"):
        client.status()


def test_token_removal_failure_is_preserved_and_does_not_skip_other_cleanup(tmp_path, monkeypatch):
    owner, _ = owned_cpu_process(tmp_path, monkeypatch)
    owner.start()
    from pathlib import Path
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path.name == "token.secret":
            raise PermissionError("Injected private token removal failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    owner.close()
    assert owner.stop_receipt["owned_group_stopped"]
    assert owner.receipt["auth_token_removed"] is True  # Worker independently removed its token.
    assert owner.receipt["private_cleanup_errors"] == [
        {"operation": "remove_auth_token", "error_type": "PermissionError"}]
    assert owner._log is None and owner.receipt["worker_stop"]["clean_stop"]
    original(owner._token_path, missing_ok=True)  # The failed parent attempt remains in its receipt.


def test_late_ready_file_cannot_bypass_overall_startup_deadline(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    def popen(argv, **settings):
        state = Path(argv[argv.index("--state-dir") + 1])
        (state / "ready.json").write_text("{}")
        return SimpleNamespace(pid=98765)
    monkeypatch.setattr("semantic_search.qwen_reranker.subprocess.Popen", popen)
    ticks = iter([0., 180.001])
    monkeypatch.setattr("semantic_search.qwen_reranker.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("semantic_search.qwen_reranker.stop_owned", lambda process: {"owned_group_stopped": True})
    owner = QwenRerankerProcess(python=sys.executable, checkpoint=checkpoint, log_dir=tmp_path / "private")
    with pytest.raises(TimeoutError):
        owner.start()
    assert owner.receipt["auth_token_removed"]


class RaceProcess:
    pid = 98765
    def __init__(self):
        self.returncode = None
        self.polls = 0
    def poll(self):
        self.polls += 1
        if self.polls >= 2:
            self.returncode = 0
        return self.returncode
    def wait(self, timeout):
        return self.poll()


def test_unreaped_zombie_permission_race_repolls_before_probe_and_records_resolution():
    process = RaceProcess()
    signals = []
    def group_signal(pid, signum):
        assert pid == process.pid
        signals.append(signum)
        if process.returncode is None:
            raise PermissionError("Darwin unreaped just-terminated leader")
        raise ProcessLookupError("Owned group gone after reaping")
    receipt = stop_owned(process, group_signal=group_signal)
    assert receipt["owned_group_stopped"] and receipt["exit_code"] == 0
    assert receipt["group_permission_retries"][0]["recovered"]
    assert all(signum == 0 for signum in signals)


def test_persistent_permission_error_is_not_treated_as_gone_even_if_leader_exited():
    process = RaceProcess()
    def group_signal(pid, signum):
        raise PermissionError("Actual inaccessible live group")
    receipt = stop_owned(process, group_signal=group_signal)
    assert not receipt["owned_group_stopped"]
    assert receipt["errors"][0]["error_type"] == "PermissionError"
    assert not receipt["group_permission_retries"][0]["recovered"]


def test_darwin_reapability_lag_retries_briefly_and_still_requires_actual_disappearance():
    class LaggedProcess(RaceProcess):
        def poll(self):
            self.polls += 1
            if self.polls >= 4:
                self.returncode = 0
            return self.returncode
    process, sleeps = LaggedProcess(), []
    def group_signal(pid, signum):
        if process.returncode is None:
            raise PermissionError("Exit begun, leader not yet reapable")
        raise ProcessLookupError("Actual group disappearance after reap")
    receipt = stop_owned(process, sleep=sleeps.append, group_signal=group_signal)
    assert receipt["owned_group_stopped"] and receipt["exit_code"] == 0
    assert sleeps == [.05]
    assert len(receipt["group_permission_retries"]) == 2
    assert receipt["group_permission_retries"][0]["recovered"] is False
    assert receipt["group_permission_retries"][1]["recovered"] is True


def test_owner_preserves_actual_venv_symlink_prefix_and_package_closure(tmp_path):
    """A real CPU venv invocation sees its metadata; the resolved base binary does not."""
    from pathlib import Path
    environment = tmp_path / "isolated_venv"
    subprocess.run([sys.executable, "-B", "-m", "venv", "--without-pip", str(environment)],
                   check=True, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    invocation = environment / "bin/python"
    if not invocation.is_symlink():
        pytest.skip("This platform copies venv Python instead of using a symlink")
    purelib = Path(subprocess.check_output([str(invocation), "-B", "-c",
        "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True, timeout=10).strip())
    metadata = purelib / "semantic_qwen_venv_fixture-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: semantic-qwen-venv-fixture\nVersion: 1.0\n")
    code = """
import importlib.metadata, json, sys
try: fixture=importlib.metadata.version('semantic-qwen-venv-fixture')
except importlib.metadata.PackageNotFoundError: fixture=None
print(json.dumps({'prefix':sys.prefix, 'fixture':fixture, 'mlx_imported':'mlx' in sys.modules}))
"""
    owner = QwenRerankerProcess(python=invocation, checkpoint=tmp_path, log_dir=tmp_path / "unused")
    assert owner.python == invocation.absolute()
    assert owner.python != invocation.resolve()
    actual = json.loads(subprocess.check_output([str(owner.python), "-B", "-c", code], text=True, timeout=10))
    base = json.loads(subprocess.check_output([str(invocation.resolve()), "-B", "-c", code], text=True, timeout=10))
    assert Path(actual["prefix"]).resolve() == environment.resolve()
    assert actual["fixture"] == "1.0" and actual["mlx_imported"] is False
    assert Path(base["prefix"]).resolve() != environment.resolve()
    assert base["fixture"] is None and base["mlx_imported"] is False
