"""Offline quickstart contracts; harmless owned CPU children replace inference."""
from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/quickstart_mac.py"
LAUNCHER = ROOT / "Get Started.command"


@pytest.fixture
def quick():
    spec = importlib.util.spec_from_file_location("quickstart_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]), patch.dict(sys.modules):
        for name in ("demo_mac", "setup_embeddings", "setup_mac", "beta_tools"):
            sys.modules.pop(name, None)
        spec.loader.exec_module(module)
    with patch.dict(os.environ):
        yield module


@pytest.fixture
def mac(quick):
    with patch.object(quick.platform, "system", return_value="Darwin"), \
            patch.object(quick.platform, "machine", return_value="arm64"):
        yield


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def checkpoint(quick, path):
    for name in quick.MODEL_FILES:
        write(path / name, "small test file")
    write(path / "model.safetensors", "tiny fake weights")
    sha = hashlib.sha256((path / "model.safetensors").read_bytes()).hexdigest()
    write(path / "config.json", json.dumps({"model_type": "qwen3", "hidden_size": 1024}))
    write(path / ".cache/huggingface/download/model.safetensors.metadata", quick.MODEL_REVISION + "\n" + sha + "\n0\n")
    return sha


def test_model_checks_the_physical_weight_hash_and_revision_without_loading_it(quick, tmp_path):
    sha = checkpoint(quick, tmp_path)
    with patch.object(quick, "WEIGHTS_SHA", sha):
        value = quick.check_model(tmp_path)
    assert value["revision"] == quick.MODEL_REVISION
    assert value["weights_etag"] == sha
    assert value["weights_dtype"] == "bfloat16" and value["quantized"] is False


@pytest.mark.parametrize("cause, message", [("missing", "incomplete"), ("corrupt", "SHA-256"),
                                           ("revision", "revision/weights"), ("quantized", "unquantized"),
                                           ("dimension", "configuration")])
def test_incomplete_or_changed_checkpoint_never_gets_a_ready_claim(quick, tmp_path, cause, message):
    sha = checkpoint(quick, tmp_path)
    if cause == "missing":
        (tmp_path / "tokenizer.json").unlink()
    elif cause == "corrupt":
        (tmp_path / "model.safetensors").write_bytes(b"changed weights")
    elif cause == "revision":
        write(tmp_path / ".cache/huggingface/download/model.safetensors.metadata", "wrong\n" + sha)
    else:
        config = {"model_type": "qwen3", "hidden_size": 1024}
        config["quantization" if cause == "quantized" else "hidden_size"] = {"bits": 8} if cause == "quantized" else 512
        write(tmp_path / "config.json", json.dumps(config))
    with patch.object(quick, "WEIGHTS_SHA", sha), pytest.raises(ValueError, match=message):
        quick.check_model(tmp_path)
    assert not (tmp_path / "semantic_search_checkpoint.json").exists()


def test_existing_verified_model_is_reused_without_download_and_gets_current_provenance(quick, tmp_path):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    sha = checkpoint(quick, model)
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "WEIGHTS_SHA", sha), patch.object(quick, "run") as run:
        quick.setup_model(tmp_path / ".cache/quickstart/venv/bin/python")
    run.assert_not_called()
    marker = json.loads((model / "semantic_search_checkpoint.json").read_text())
    assert marker["id"] == quick.MODEL_ID and marker["revision"] == quick.MODEL_REVISION


def test_runtime_cache_always_probes_actual_dedicated_interpreter(quick, tmp_path):
    work = tmp_path / ".cache/quickstart"
    python = work / "venv/bin/python"
    write(python, "fake interpreter")
    write(work / "venv/pyvenv.cfg", "home = managed python")
    inputs = {"input": "checked"}
    write(work / "runtime.json", json.dumps({"inputs": inputs, "inventory": {"stale": True}}))
    fresh = {"prefix": str(work / "venv"), "metal": True}
    with patch.object(quick, "runtime_fingerprint", return_value=inputs), \
            patch.object(quick, "runtime_probe", return_value=fresh) as probe, patch.object(quick, "run") as run:
        actual, inventory = quick.setup_runtime(tmp_path / "uv", work)
    probe.assert_called_once_with(python)
    run.assert_not_called()
    assert actual == python and inventory == fresh
    assert actual != tmp_path / ".venv/bin/python"


def test_failed_runtime_probe_repairs_only_its_dedicated_environment(quick, tmp_path):
    work = tmp_path / ".cache/quickstart"
    python = work / "venv/bin/python"
    write(python, "fake interpreter")
    write(work / "venv/pyvenv.cfg", "home = managed python")
    inputs = {"input": "checked"}
    write(work / "runtime.json", json.dumps({"inputs": inputs}))
    write(tmp_path / ".venv/keep.txt", "existing developer environment")
    good = {"prefix": str(work / "venv"), "metal": True}
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "runtime_fingerprint", return_value=inputs), \
            patch.object(quick, "runtime_probe", side_effect=[ValueError("stale version"), good]), patch.object(quick, "run") as run:
        actual, _ = quick.setup_runtime(tmp_path / "uv", work)
    commands = [tuple(str(item) for item in call.args) for call in run.call_args_list]
    assert len(commands) == 2
    assert all(command[:3] == (str(tmp_path / "uv"), "pip", "install") for command in commands)
    assert all(command[command.index("--python") + 1] == str(python) for command in commands)
    assert "--no-config" in commands[0] and "--no-config" in commands[1]
    assert "--no-deps" in commands[1]
    assert actual == python
    assert (tmp_path / ".venv/keep.txt").read_text() == "existing developer environment"
    assert json.loads((work / "runtime.json").read_text())["inventory"] == good


def test_runtime_probe_rejects_interpreter_prefix_outside_dedicated_environment(quick, tmp_path):
    python = tmp_path / "venv/bin/python"
    with patch.object(quick, "run", return_value=SimpleNamespace(stdout=json.dumps({"prefix": str(tmp_path / "other")}))) as run:
        with pytest.raises(ValueError, match="outside"):
            quick.runtime_probe(python)
    assert run.call_args.args[:2] == (python, "-c")
    code = run.call_args.args[2]
    assert "m.version(name) == version" in code and "encoding()" in code and "mx.metal.is_available()" in code


def test_unrecognized_environment_is_never_installed_into(quick, tmp_path):
    work = tmp_path / "quickstart"
    write(work / "venv/my-files.txt", "user files")
    with patch.object(quick, "runtime_fingerprint", return_value={}), patch.object(quick, "run") as run:
        with pytest.raises(ValueError, match="unrecognized"):
            quick.setup_runtime(tmp_path / "uv", work)
    run.assert_not_called()
    assert (work / "venv/my-files.txt").read_text() == "user files"


def test_non_tty_interactive_mode_stops_before_installation_or_inference(quick, tmp_path, mac):
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick.sys.stdin, "isatty", return_value=False), \
            patch.object(quick, "setup_runtime") as runtime, patch.object(quick, "setup_model") as model, \
            patch.object(quick, "interactive") as interactive:
        with pytest.raises(SystemExit) as error:
            quick.main(["--uv-path", str(tmp_path / "uv")])
    assert error.value.code == 2
    runtime.assert_not_called()
    model.assert_not_called()
    interactive.assert_not_called()
    assert not (tmp_path / ".cache").exists()


@pytest.mark.parametrize("mode", ["--setup-only", "--check"])
def test_explicit_noninteractive_modes_use_the_separate_runtime_and_correct_dispatch(quick, tmp_path, mac, mode):
    python = tmp_path / ".cache/quickstart/venv/bin/python"
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick.sys.stdin, "isatty", return_value=False), \
            patch.object(quick, "setup_runtime", return_value=(python, {})) as runtime, \
            patch.object(quick, "setup_model") as model, patch.object(quick, "run") as run, \
            patch.object(quick, "prepare") as prepare, patch.object(quick, "interactive") as interactive:
        quick.main(["--uv-path", str(tmp_path / "uv"), mode])
    runtime.assert_called_once_with(tmp_path / "uv", tmp_path / ".cache/quickstart")
    model.assert_called_once_with(python)
    prepare.assert_not_called()
    interactive.assert_not_called()
    if mode == "--check":
        run.assert_called_once_with(python, tmp_path / "scripts/demo_mac.py", "--run", "--quiet", "--python", python)
    else:
        run.assert_not_called()


def test_main_platform_guard_stops_before_setup(quick, tmp_path):
    with patch.object(quick.platform, "system", return_value="Linux"), patch.object(quick, "setup_runtime") as runtime:
        with pytest.raises(SystemExit) as error:
            quick.main(["--uv-path", str(tmp_path / "uv"), "--setup-only"])
    assert error.value.code == 2
    runtime.assert_not_called()


@pytest.mark.parametrize("source_root", ["../private", "/absolute"])
def test_repo_source_scope_guard_precedes_setup(quick, tmp_path, mac, source_root):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with patch.object(quick.sys.stdin, "isatty", return_value=True), patch.object(quick, "setup_runtime") as runtime:
        with pytest.raises(SystemExit) as error:
            quick.main(["--uv-path", str(tmp_path / "uv"), "--repo", str(repo), "--source-root", source_root])
    assert error.value.code == 2
    runtime.assert_not_called()


@pytest.mark.parametrize("cause", ["normal", "keyboard", "identity"])
def test_interactive_owns_an_available_port_and_cleans_actual_cpu_child_on_every_exit(quick, tmp_path, cause):
    """A real tiny worker emits readiness only; it opens no socket or model."""
    real_popen = subprocess.Popen
    spawned, commands, requests = [], [], []
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (tmp_path / ".cache/quickstart/tiktoken").mkdir(parents=True)
    original_handler = signal.getsignal(signal.SIGTERM)
    worker = "import signal,sys,time\nsignal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\nprint('Ready: http://127.0.0.1:43210',flush=True)\nwhile True: time.sleep(60)\n"
    def start(arguments, **kwargs):
        commands.append((arguments, kwargs))
        process = real_popen([sys.executable, "-c", worker], **kwargs)
        spawned.append(process)
        return process
    def response(url, payload=None, timeout=120):
        requests.append((url, payload))
        if url.endswith("/status"):
            return {"repo": str(repo if cause != "identity" else tmp_path / "wrong"), "source_root": "src", "ready": True}
        return {"results": []}
    inputs = ["1", "q"] if cause == "normal" else KeyboardInterrupt()
    try:
        with patch.object(quick, "ROOT", tmp_path), patch.object(quick.subprocess, "Popen", side_effect=start), \
                patch.object(quick, "request_json", side_effect=response), patch("builtins.input", side_effect=inputs):
            if cause == "normal":
                quick.interactive(Path(sys.executable), repo, "src", tmp_path / "work", timeout=3)
            else:
                expected = KeyboardInterrupt if cause == "keyboard" else ValueError
                with pytest.raises(expected):
                    quick.interactive(Path(sys.executable), repo, "src", tmp_path / "work", timeout=3)
        assert len(spawned) == 1 and spawned[0].returncode == 0
        with pytest.raises(ProcessLookupError):
            os.killpg(spawned[0].pid, 0)
        arguments, options = commands[0]
        assert arguments[arguments.index("--port") + 1] == "0"
        assert options["start_new_session"] is True and "--no-watch" in arguments
        assert arguments[arguments.index("--repo") + 1] == str(repo)
        assert arguments[arguments.index("--source-root") + 1] == "src"
        assert requests[0] == ("http://127.0.0.1:43210/status", None)
        if cause == "normal":
            payload = dict(requests[1][1])
            assert len(payload.pop("request_id")) == 32
            assert payload == {"query": "1", **quick.OWN_SETTINGS}
            session_dir = next((tmp_path / "work/sessions").iterdir())
            server = json.loads((session_dir / "mcp.json").read_text())["mcpServers"]["semantic-search"]
            assert server["args"][-1] == "http://127.0.0.1:43210"
            assert server["env"]["TIKTOKEN_CACHE_DIR"] == str(tmp_path / ".cache/quickstart/tiktoken")
        else:
            assert len(requests) == 1
        session = next((tmp_path / "work/sessions").glob("*/session.json"))
        assert json.loads(session.read_text())["shutdown"] == {"returncode": 0, "forced_kill": False}
        assert signal.getsignal(signal.SIGTERM) == original_handler
    finally:
        for process in spawned:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def test_http_client_disables_proxy_and_redirect_and_bounds_response(quick):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            assert size == 1024 * 1024 + 1
            return b'{"ready":true}'
    with patch.object(quick.urllib.request, "build_opener") as build:
        build.return_value.open.return_value = Response()
        assert quick.request_json("http://127.0.0.1:1234/status") == {"ready": True}
        assert build.call_args.args[0].proxies == {}
        assert build.call_args.args[1].redirect_request(None, None, 302, "redirect", {}, "http://example.com") is None
        assert build.return_value.open.call_args.args[0].get_method() == "GET"


def shell_fixture(tmp_path):
    """Copy production Bash, changing only absolute platform and curl commands."""
    root = tmp_path / "download with spaces"
    root.mkdir()
    tools = tmp_path / "fake-tools"
    tools.mkdir()
    uname = tools / "uname"
    write(uname, '#!/bin/sh\ncase "$1" in -s) printf "%s\\n" "$FAKE_SYSTEM";; -m) printf "%s\\n" "$FAKE_ARCH";; esac\n')
    curl = tools / "curl"
    write(curl, '#!/bin/sh\nprintf "unexpected network attempt\\n" >&2\nexit 97\n')
    git = tools / "git"
    write(git, '#!/bin/sh\nexit "${FAKE_GIT_EXIT:-0}"\n')
    for tool in (uname, curl, git):
        tool.chmod(0o755)
    text = LAUNCHER.read_text().replace("/usr/bin/uname", shlex.quote(str(uname))).replace("/usr/bin/curl", shlex.quote(str(curl)))
    launcher = root / "Get Started.command"
    write(launcher, text)
    environment = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
                   "FAKE_SYSTEM": "Darwin", "FAKE_ARCH": "arm64"}
    return root, launcher, environment


def test_real_bash_syntax_and_help_require_no_prerequisites_or_network(tmp_path):
    syntax = subprocess.run(["bash", "-n", LAUNCHER], capture_output=True, text=True, timeout=5)
    assert syntax.returncode == 0, syntax.stderr
    root, launcher, environment = shell_fixture(tmp_path)
    environment.update({"FAKE_SYSTEM": "Linux", "FAKE_GIT_EXIT": "13"})
    result = subprocess.run(["bash", launcher, "--help"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and "--check" in result.stdout and "--setup-only" in result.stdout
    assert not (root / ".cache").exists()


@pytest.mark.parametrize("cause, message", [("platform", "Apple Silicon"), ("git", "xcode-select --install")])
def test_launcher_platform_and_working_git_guards_precede_any_install(tmp_path, cause, message):
    root, launcher, environment = shell_fixture(tmp_path)
    environment["FAKE_SYSTEM" if cause == "platform" else "FAKE_GIT_EXIT"] = "Linux" if cause == "platform" else "12"
    result = subprocess.run(["bash", launcher, "--setup-only"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and message in result.stderr
    assert "unexpected network attempt" not in result.stderr
    assert not (root / ".cache").exists()


def test_launcher_refuses_changed_cached_bootstrap_before_execution(tmp_path):
    root, launcher, environment = shell_fixture(tmp_path)
    uv = root / ".cache/quickstart/bootstrap/uv"
    write(uv, '#!/bin/sh\nprintf "MUST_NOT_EXECUTE\\n"\n')
    uv.chmod(0o755)
    write(uv.with_name("uv.sha256"), "0" * 64 + "\n")
    result = subprocess.run(["bash", launcher, "--setup-only"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "Cached uv changed" in result.stderr
    assert "MUST_NOT_EXECUTE" not in result.stdout + result.stderr
    assert "unexpected network attempt" not in result.stderr


@pytest.mark.parametrize("raw", ["{", "[]", "null", "false"])
def test_malformed_completion_marker_repairs_runtime_instead_of_trusting_or_looping(quick, tmp_path, raw):
    work = tmp_path / "quickstart"
    python = work / "venv/bin/python"
    write(python, "interpreter")
    write(work / "venv/pyvenv.cfg", "environment")
    write(work / "runtime.json", raw)
    with patch.object(quick, "runtime_fingerprint", return_value={"pinned": "inputs"}), \
            patch.object(quick, "runtime_probe", return_value={"metal": True}), patch.object(quick, "run") as run:
        quick.setup_runtime(tmp_path / "uv", work)
    assert run.call_count == 2
    assert json.loads((work / "runtime.json").read_text())["inputs"] == {"pinned": "inputs"}


def test_runtime_marker_symlink_is_refused_without_changing_other_file(quick, tmp_path):
    work = tmp_path / "quickstart"
    work.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("user file")
    (work / "runtime.json").symlink_to(outside)
    with patch.object(quick, "runtime_fingerprint", return_value={}), patch.object(quick, "run") as run:
        with pytest.raises(ValueError, match="symlink"):
            quick.setup_runtime(tmp_path / "uv", work)
    run.assert_not_called()
    assert outside.read_text() == "user file"


@pytest.mark.parametrize("cause", ["directory", "config", "provenance", "metadata", "nested-cache"])
def test_checkpoint_symlinks_are_refused_before_download_or_provenance_write(quick, tmp_path, cause):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    sha = checkpoint(quick, model)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("user file")
    if cause == "directory":
        moved = outside / "model"
        model.rename(moved)
        model.symlink_to(moved, target_is_directory=True)
    elif cause == "nested-cache":
        moved = outside / "cache"
        (model / ".cache").rename(moved)
        (model / ".cache").symlink_to(moved, target_is_directory=True)
    else:
        name = {"config": "config.json", "provenance": "semantic_search_checkpoint.json",
                "metadata": ".cache/huggingface/download/model.safetensors.metadata"}[cause]
        path = model / name
        if path.exists():
            path.unlink()
        path.symlink_to(sentinel)
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "WEIGHTS_SHA", sha), patch.object(quick, "run") as run:
        with pytest.raises(ValueError, match="symlink"):
            quick.setup_model(tmp_path / "venv/bin/python")
    run.assert_not_called()
    assert sentinel.read_text() == "user file"
    assert not (outside / "model/semantic_search_checkpoint.json").exists()


def test_corrupt_complete_checkpoint_forces_refetch_and_reverifies_before_provenance(quick, tmp_path):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    sha = checkpoint(quick, model)
    original = (model / "model.safetensors").read_bytes()
    (model / "model.safetensors").write_bytes(b"corruption with apparently valid Hub metadata")
    python = tmp_path / "dedicated/bin/python"
    def download(*args):
        assert args[:5] == (python.parent / "hf", "download", quick.MODEL_ID, "--revision", quick.MODEL_REVISION)
        assert "--force-download" in args
        (model / "model.safetensors").write_bytes(original)
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "WEIGHTS_SHA", sha), \
            patch.object(quick, "run", side_effect=download) as run:
        quick.setup_model(python)
    run.assert_called_once()
    assert json.loads((model / "semantic_search_checkpoint.json").read_text())["weights_etag"] == sha


def test_interrupted_setup_download_never_writes_provenance(quick, tmp_path):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "run", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            quick.setup_model(tmp_path / "dedicated/bin/python")
    assert not (model / "semantic_search_checkpoint.json").exists()


def test_interrupted_owned_setup_subprocess_is_actually_reaped(quick, tmp_path):
    real_popen = subprocess.Popen
    spawned = []
    worker = "import signal,sys,time\nsignal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\nprint('ready',flush=True)\nwhile True: time.sleep(60)\n"
    def start(arguments, **options):
        assert options["start_new_session"] is True
        process = real_popen([sys.executable, "-c", worker], **options)
        assert process.stdout.readline().strip() == "ready"
        process.communicate = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        spawned.append(process)
        return process
    try:
        with patch.object(quick, "ROOT", tmp_path), patch.object(quick.subprocess, "Popen", side_effect=start):
            with pytest.raises(KeyboardInterrupt):
                quick.run("fake installer", capture=True)
        assert spawned[0].returncode == 0
        with pytest.raises(ProcessLookupError):
            os.killpg(spawned[0].pid, 0)
    finally:
        for process in spawned:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def test_scoped_cache_variables_and_signal_handlers_survive_check_interruption(quick, tmp_path, mac):
    python = tmp_path / ".cache/quickstart/venv/bin/python"
    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    def inspect_then_interrupt(*args):
        for name in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "HF_HOME", "HF_HUB_CACHE", "HF_XET_CACHE", "TIKTOKEN_CACHE_DIR"):
            assert Path(os.environ[name]).is_relative_to(tmp_path / ".cache/quickstart")
        assert os.environ["UV_NO_CONFIG"] == "1"
        signal.raise_signal(signal.SIGHUP)
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "setup_runtime", return_value=(python, {})), \
            patch.object(quick, "setup_model"), patch.object(quick, "run", side_effect=inspect_then_interrupt):
        with pytest.raises(SystemExit) as error:
            quick.main(["--uv-path", str(tmp_path / "uv"), "--check"])
    assert error.value.code == 130
    for number, handler in previous.items():
        assert signal.getsignal(number) == handler


def test_concurrent_setup_lock_stops_before_runtime_or_model(quick, tmp_path, mac):
    work = tmp_path / ".cache/quickstart"
    work.mkdir(parents=True)
    with (work / "setup.lock").open("a") as lock:
        quick.fcntl.flock(lock, quick.fcntl.LOCK_EX | quick.fcntl.LOCK_NB)
        with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "setup_runtime") as runtime, \
                patch.object(quick, "setup_model") as model:
            with pytest.raises(SystemExit) as error:
                quick.main(["--uv-path", str(tmp_path / "uv"), "--setup-only"])
    assert error.value.code == 1
    runtime.assert_not_called()
    model.assert_not_called()


@pytest.mark.parametrize("name", ["bootstrap", "python", "uv-cache"])
def test_launcher_refuses_nested_reserved_symlinks_without_touching_target(tmp_path, name):
    root, launcher, environment = shell_fixture(tmp_path)
    work = root / ".cache/quickstart"
    work.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    write(outside / "keep", "user files")
    (work / name).symlink_to(outside, target_is_directory=True)
    result = subprocess.run(["bash", launcher, "--setup-only"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "symlink" in result.stderr
    assert list(outside.iterdir()) == [outside / "keep"]
    assert "unexpected network attempt" not in result.stderr


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_checked_cached_launcher_forwards_space_containing_arguments_without_system_python(tmp_path, locale):
    root, launcher, environment = shell_fixture(tmp_path)
    uv = root / ".cache/quickstart/bootstrap/uv"
    python = tmp_path / "managed test python"
    trace = tmp_path / "python-arguments.json"
    write(python, f'#!{sys.executable}\nimport json,os,sys\nfrom pathlib import Path\nPath({str(trace)!r}).write_text(json.dumps({{"arguments":sys.argv[1:],"cache":os.environ["UV_CACHE_DIR"],"python":os.environ["UV_PYTHON_INSTALL_DIR"],"config":os.environ["UV_NO_CONFIG"]}}))\n')
    python.chmod(0o755)
    write(uv, '#!/bin/sh\ncase "$1 $2" in "--version ") printf "uv 0.13.0 (test build)\\n";; "python install") exit 0;; "python find") printf "%s\\n" "$FAKE_MANAGED_PYTHON";; *) exit 99;; esac\n')
    uv.chmod(0o755)
    write(uv.with_name("uv.sha256"), hashlib.sha256(uv.read_bytes()).hexdigest() + "\n")
    environment["FAKE_MANAGED_PYTHON"] = str(python)
    environment.update({"LC_ALL": locale, "LC_CTYPE": locale, "LANG": locale})
    result = subprocess.run(["bash", launcher, "--repo", "/a repo with spaces", "--source-root", "source files"],
                            env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    record = json.loads(trace.read_text())
    assert record["arguments"] == [str(root / "scripts/quickstart_mac.py"), "--uv-path", str(uv),
                                    "--repo", "/a repo with spaces", "--source-root", "source files"]
    assert record["cache"] == str(root / ".cache/quickstart/uv-cache")
    assert record["python"] == str(root / ".cache/quickstart/python")
    assert record["config"] == "1"
    assert "unexpected network attempt" not in result.stderr
    assert "Setting locale failed" not in result.stderr


def test_launcher_checks_tarball_digest_and_removes_only_owned_download_directory(tmp_path):
    root, launcher, environment = shell_fixture(tmp_path)
    curl = tmp_path / "fake-tools/curl"
    write(curl, '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do if [ "$1" = --output ]; then shift; printf "wrong tarball" > "$1"; exit 0; fi; shift; done\nexit 98\n')
    curl.chmod(0o755)
    result = subprocess.run(["bash", launcher, "--setup-only"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "checksum differs" in result.stderr
    bootstrap = root / ".cache/quickstart/bootstrap"
    assert not (bootstrap / "uv").exists()
    assert not list(bootstrap.glob("download.*"))


def test_launcher_refuses_symlinked_cached_checksum_before_reading_or_writing_it(tmp_path):
    root, launcher, environment = shell_fixture(tmp_path)
    uv = root / ".cache/quickstart/bootstrap/uv"
    write(uv, "unused")
    outside = tmp_path / "outside-checksum"
    outside.write_text("user file")
    uv.with_name("uv.sha256").symlink_to(outside)
    result = subprocess.run(["bash", launcher, "--setup-only"], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "checksum must not be a symlink" in result.stderr
    assert outside.read_text() == "user file"


def test_cold_runtime_creates_only_reserved_environment_before_locked_installs(quick, tmp_path):
    work = tmp_path / ".cache/quickstart"
    work.mkdir(parents=True)
    existing = tmp_path / ".venv/user-file"
    write(existing, "unchanged")
    with patch.object(quick, "runtime_fingerprint", return_value={"source": "hash"}), \
            patch.object(quick, "runtime_probe", return_value={"metal": True}), patch.object(quick, "run") as run:
        python, _ = quick.setup_runtime(tmp_path / "uv", work)
    commands = [tuple(str(item) for item in call.args) for call in run.call_args_list]
    assert commands[0] == (str(tmp_path / "uv"), "venv", "--python", sys.executable, str(work / "venv"), "--no-config")
    assert len(commands) == 3 and python == work / "venv/bin/python"
    assert all(command[command.index("--python") + 1] == str(python) for command in commands[1:])
    assert "requirements-mac.lock" in commands[1] and "requirements-languages.lock" in commands[1]
    assert existing.read_text() == "unchanged"


def test_failed_forced_download_still_refuses_corrupt_checkpoint_and_no_new_marker(quick, tmp_path):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    sha = checkpoint(quick, model)
    (model / "model.safetensors").write_bytes(b"still corrupt")
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "WEIGHTS_SHA", sha), patch.object(quick, "run") as run:
        with pytest.raises(ValueError, match="SHA-256"):
            quick.setup_model(tmp_path / "dedicated/bin/python")
    assert "--force-download" in run.call_args.args
    assert not (model / "semantic_search_checkpoint.json").exists()


def test_missing_small_tokenizer_file_preserves_verified_large_weights_during_resume(quick, tmp_path):
    model = tmp_path / ".cache/models/qwen3-embedding-0.6b"
    sha = checkpoint(quick, model)
    missing = model / "tokenizer.json"
    missing.unlink()
    def resume(*args):
        assert "--force-download" not in args
        missing.write_text("resumed tokenizer")
    with patch.object(quick, "ROOT", tmp_path), patch.object(quick, "WEIGHTS_SHA", sha), patch.object(quick, "run", side_effect=resume):
        quick.setup_model(tmp_path / "dedicated/bin/python")
    assert json.loads((model / "semantic_search_checkpoint.json").read_text())["weights_etag"] == sha


def test_valid_json_nonobject_configuration_has_actionable_refusal(quick, tmp_path):
    sha = checkpoint(quick, tmp_path)
    (tmp_path / "config.json").write_text("[]")
    with patch.object(quick, "WEIGHTS_SHA", sha), pytest.raises(ValueError, match="configuration"):
        quick.check_model(tmp_path)


@pytest.mark.parametrize('value,expected', [('src/', 'src'), ('./src', 'src'), ('.', '.')])
def test_own_repo_scope_matches_backend_canonical_identity(quick, tmp_path, mac, value, expected):
    repo = tmp_path / 'My Project'
    (repo / '.git').mkdir(parents=True)
    (repo / 'src').mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '--quiet'], check=True)
    python = tmp_path / '.cache/quickstart/venv/bin/python'
    with patch.object(quick, 'ROOT', tmp_path), patch.object(quick.sys.stdin, 'isatty', return_value=True), \
            patch.object(quick, 'setup_runtime', return_value=(python, {})), patch.object(quick, 'setup_model'), \
            patch.object(quick, 'check_source') as preflight, \
            patch.object(quick, 'interactive') as interactive:
        quick.main(['--uv-path', str(tmp_path / 'uv'), '--repo', str(repo), '--source-root', value])
    preflight.assert_called_once_with(python, repo.resolve(), expected, [])
    interactive.assert_called_once_with(python, repo.resolve(), expected, tmp_path / '.cache/quickstart',
                                       timeout=1800, request_timeout=120, settings=quick.OWN_SETTINGS, toy=False, port=0, exclusions=[])


@pytest.mark.parametrize('preflight_only', [True, False])
@pytest.mark.parametrize('blocked', [True, False])
def test_source_preflight_precedes_weights_and_service(quick, tmp_path, mac, preflight_only, blocked):
    repo = tmp_path / 'My Project'
    (repo / 'src').mkdir(parents=True)
    subprocess.run(['git', '-C', str(repo), 'init', '--quiet'], check=True)
    python = tmp_path / '.cache/quickstart/venv/bin/python'
    events = []

    def inspect(*args):
        events.append('source')
        assert args == (python, repo.resolve(), 'src', ['src/generated/*'])
        if blocked:
            raise ValueError('Source preflight blocked indexing')

    arguments = ['--uv-path', str(tmp_path / 'uv'), '--repo', str(repo),
                 '--exclude-source', 'src/generated/*']
    if preflight_only:
        arguments.append('--preflight')
    with patch.object(quick, 'ROOT', tmp_path), \
            patch.object(quick.sys.stdin, 'isatty', return_value=not preflight_only), \
            patch.object(quick, 'setup_runtime', side_effect=lambda *_: (events.append('runtime') or python, {})), \
            patch.object(quick, 'check_source', side_effect=inspect), \
            patch.object(quick, 'setup_model', side_effect=lambda *_: events.append('model')) as model, \
            patch.object(quick, 'interactive', side_effect=lambda *_, **kwargs: events.append('service')) as interactive:
        if blocked:
            with pytest.raises(SystemExit) as error:
                quick.main(arguments)
            assert error.value.code == 1
        else:
            quick.main(arguments)
    assert events == (['runtime', 'source', 'model', 'service'] if not blocked and not preflight_only
                      else ['runtime', 'source'])
    if blocked or preflight_only:
        model.assert_not_called()
        interactive.assert_not_called()


def test_preflight_requires_repo_before_runtime_or_cache_writes(quick, tmp_path, mac):
    with patch.object(quick, 'ROOT', tmp_path), patch.object(quick, 'setup_runtime') as runtime:
        with pytest.raises(SystemExit) as error:
            quick.main(['--uv-path', '/unused', '--preflight'])
    assert error.value.code == 2
    runtime.assert_not_called()
    assert not (tmp_path / '.cache').exists()


@pytest.mark.parametrize('returncode', [0, 1, 2])
def test_source_check_uses_cli_scope_and_preserves_non_source_failures(quick, tmp_path, returncode):
    python, repo = tmp_path / 'python', tmp_path / 'My Project'
    command = (python, '-m', 'semantic_search.cli', 'inspect', '--repo', repo,
               '--source-root', 'src', '--exclude-source', 'src/templates/*.js')
    effect = subprocess.CalledProcessError(returncode, command) if returncode else None
    with patch.object(quick, 'run', side_effect=effect) as run:
        if returncode == 1:
            with pytest.raises(ValueError, match='Source preflight blocked indexing'):
                quick.check_source(python, repo, 'src', ['src/templates/*.js'])
        elif returncode:
            with pytest.raises(subprocess.CalledProcessError):
                quick.check_source(python, repo, 'src', ['src/templates/*.js'])
        else:
            quick.check_source(python, repo, 'src', ['src/templates/*.js'])
    run.assert_called_once_with(*command)


def test_own_repo_source_symlink_escape_stops_before_install(quick, tmp_path, mac):
    repo, external = tmp_path / 'repo', tmp_path / 'outside'
    (repo / '.git').mkdir(parents=True)
    external.mkdir()
    (repo / 'src').symlink_to(external, target_is_directory=True)
    with patch.object(quick.sys.stdin, 'isatty', return_value=True), patch.object(quick, 'setup_runtime') as runtime:
        with pytest.raises(SystemExit) as error:
            quick.main(['--uv-path', str(tmp_path / 'uv'), '--repo', str(repo)])
    assert error.value.code == 2
    runtime.assert_not_called()


@pytest.mark.parametrize('flag,value', [('--startup-timeout','0'),('--startup-timeout','nan'),
                                      ('--request-timeout','inf'),('--max-results','21'),
                                      ('--source-budget','0'),('--token-budget','511'),('--port','65536')])
def test_beta_invalid_limits_stop_before_setup(quick, flag, value):
    with patch.object(quick,'setup_runtime') as runtime, pytest.raises(SystemExit):
        quick.main(['--uv-path','/unused',flag,value])
    runtime.assert_not_called()


def test_beta_progress_accepts_ready_and_ignores_private_or_invalid_messages(quick):
    assert quick.format_progress('Progress: {"phase":"ready","completed":1,"total":1,"unit":"service"}') == ('ready','Search ready: 1/1 service',True)
    cache=quick.format_progress('Progress: {"phase":"cache","completed":2,"total":5,"unit":"documents","cached_documents":2,"pending_documents":3,"path":"private"}')
    assert '2 cached, 3 new' in cache[1] and 'private' not in cache[1]
    for line in ('Progress: []','Progress: bad','Progress: {"phase":"embed","completed":true,"unit":"documents"}',
                 'Progress: {"phase":"embed","completed":2,"total":1,"unit":"documents"}'):
        assert quick.format_progress(line) is None


def test_beta_tails_large_logs_and_partial_ready_lines(quick,tmp_path):
    log=tmp_path/'service.log'
    log.write_bytes(b'x'*100+b'\n'+b'x\n'*1500000+b'Ready: http://127.0.0.1:1234')
    state={'offset':0,'pending':b''}
    rows=[]
    while state['offset']<log.stat().st_size:
        rows.extend(quick.new_log_lines(log,state))
    assert not any(row.startswith('Ready:') for row in rows)
    with log.open('ab') as stream:stream.write(b'\n')
    assert quick.new_log_lines(log,state)==['Ready: http://127.0.0.1:1234']
    assert len(state['pending'])<=65536


@pytest.fixture
def beta_worker(quick,tmp_path):
    """Actual owned CPU worker; HTTP responses are supplied by the test."""
    real_popen=subprocess.Popen
    processes=[]
    repo=tmp_path/'repo'
    (repo/'src').mkdir(parents=True)
    (tmp_path/'.cache/quickstart/tiktoken').mkdir(parents=True)
    worker="import signal,sys,time\nsignal.signal(signal.SIGTERM,lambda *_:sys.exit(0))\nprint('Progress: {\"phase\":\"ready\",\"completed\":1,\"total\":1,\"unit\":\"service\"}',flush=True)\nprint('Ready: http://127.0.0.1:43210',flush=True)\nwhile True:time.sleep(60)\n"
    def start(arguments,**options):
        assert '--progress' in arguments
        process=real_popen([sys.executable,'-c',worker],**options)
        processes.append(process)
        return process
    with patch.object(quick,'ROOT',tmp_path),patch.object(quick.subprocess,'Popen',side_effect=start):
        yield repo,processes
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
        assert process.returncode==0
        with pytest.raises(ProcessLookupError):os.killpg(process.pid,0)


@pytest.mark.parametrize('toy',[True,False])
def test_beta_toy_menu_and_own_repo_budget_are_distinct(quick,tmp_path,beta_worker,capsys,toy):
    repo,_=beta_worker
    payloads=[]
    def reply(url,payload=None,timeout=120):
        if url.endswith('/status'):return {'repo':str(repo),'source_root':'src','ready':True,'windows':3}
        payloads.append(payload);return {'results':[]}
    settings=quick.SETTINGS if toy else quick.OWN_SETTINGS
    with patch.object(quick,'request_json',side_effect=reply),patch('builtins.input',side_effect=['1', 'q']):
        quick.interactive(Path(sys.executable),repo,'src',tmp_path/'work',settings=settings,toy=toy)
    expected=quick.QUERIES[0] if toy else '1'
    assert payloads[0]['query']==expected
    assert payloads[0]['max_results']==(3 if toy else 8)
    assert payloads[0]['include_dependencies'] is (not toy)
    output=capsys.readouterr().out
    assert ('  1. Normalize a label' in output) is toy
    assert 'Search ready: 1/1 service' in output


@pytest.mark.parametrize('failure',['snapshot','timeout','incomplete'])
def test_beta_search_failures_allow_retry_and_cancel_abandoned_work(quick,tmp_path,beta_worker,capsys,failure):
    import io
    from http.client import IncompleteRead
    from urllib.error import HTTPError
    repo,_=beta_worker
    calls=[]
    def reply(url,payload=None,timeout=120):
        calls.append((url,payload,timeout))
        if url.endswith('/status'):return {'repo':str(repo),'source_root':'src','ready':True,'windows':3}
        if url.endswith('/cancel'):return {'cancelled':True}
        if len([row for row in calls if row[0].endswith('/search')])==1:
            if failure=='snapshot':raise HTTPError(url,409,'source changed',{},io.BytesIO(b'{"error":{"message":"Fix saved source"}}'))
            if failure=='timeout':raise TimeoutError('slow query')
            raise IncompleteRead(b'partial',50)
        return {'results':[{'path':'src/test.py','start_line':1,'end_line':1,'symbol':'test','source':'pass\n','truncated':True}],
                'budget_exhausted':True}
    with patch.object(quick,'request_json',side_effect=reply),patch('builtins.input',side_effect=['first','second','q']):
        quick.interactive(Path(sys.executable),repo,'src',tmp_path/'work',request_timeout=7)
    searches=[row for row in calls if row[0].endswith('/search')]
    assert len(searches)==2 and all(row[2]==7 for row in searches)
    assert searches[0][1]['request_id']!=searches[1][1]['request_id']
    cancels=[row for row in calls if row[0].endswith('/cancel')]
    assert len(cancels)==(0 if failure=='snapshot' else 1)
    if cancels:assert cancels[0][1]=={'request_id':searches[0][1]['request_id']} and cancels[0][2]==2
    session=json.loads(next((tmp_path/'work/sessions').glob('*/session.json')).read_text())
    assert session['searches']==1 and session['errors']==1 and session['shutdown']=={'returncode':0,'forced_kill':False}
    output=capsys.readouterr().out
    assert '(excerpt truncated)' in output and 'Context limit reached' in output


def test_beta_startup_can_pass_old_three_minute_cutoff(quick,tmp_path,beta_worker):
    repo,_=beta_worker
    readings=iter([0])
    def now():return next(readings,181)
    with patch.object(quick.time,'monotonic',side_effect=now), \
            patch.object(quick,'request_json',return_value={'repo':str(repo),'source_root':'src','ready':True,'windows':0}), \
            patch('builtins.input',return_value='q'):
        quick.interactive(Path(sys.executable),repo,'src',tmp_path/'work')
    session=json.loads(next((tmp_path/'work/sessions').glob('*/session.json')).read_text())
    assert session['startup_timeout']==1800 and session['request_timeout']==120
    assert session['windows']==0 and session['shutdown']['returncode']==0


def test_beta_corrupt_git_root_stops_before_install(quick,tmp_path,mac):
    repo=tmp_path/'broken'
    (repo/'.git').mkdir(parents=True)
    (repo/'src').mkdir()
    with patch.object(quick.sys.stdin,'isatty',return_value=True),patch.object(quick,'setup_runtime') as runtime:
        with pytest.raises(SystemExit):quick.main(['--uv-path','/unused','--repo',str(repo)])
    runtime.assert_not_called()


@pytest.mark.parametrize("initial,second", [("interrupt", signal.SIGINT),
                                            ("interrupt", signal.SIGTERM),
                                            ("interrupt", signal.SIGHUP),
                                            ("quit", signal.SIGINT)])
def test_repeated_interrupt_waits_for_owned_exit_and_receipt(tmp_path, initial, second):
    """Real signals during a real child's delayed shutdown cannot skip cleanup."""
    driver = r'''
import builtins,json,signal,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import quickstart_mac as quick
root=Path(sys.argv[2]); quick.ROOT=root
repo=root/'repo'; (repo/'src').mkdir(parents=True)
(root/'.cache/quickstart/tiktoken').mkdir(parents=True)
original={sig:signal.getsignal(sig) for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
worker="""import signal,sys,time
from pathlib import Path
root=Path(sys.argv[1])
def stop(*_):
    (root/'stopping').write_text('TERM received')
    while not (root/'release').exists(): time.sleep(.01)
    sys.exit(0)
signal.signal(signal.SIGTERM,stop)
print('Ready: http://127.0.0.1:43210',flush=True)
while True:time.sleep(60)
"""
real_popen=subprocess.Popen
def start(arguments,**kwargs):
    child=real_popen([sys.executable,'-c',worker,str(root)],**kwargs)
    (root/'worker-pid').write_text(str(child.pid))
    return child
quick.subprocess.Popen=start
quick.request_json=lambda *args,**kwargs:{'repo':str(repo),'source_root':'src','ready':True,'windows':0}
def prompt(*_):
    (root/'prompt').write_text('ready')
    if sys.argv[3]=='quit':return 'q'
    while True:time.sleep(60)
builtins.input=prompt
try:
    quick.interactive(Path(sys.executable),repo,'src',root/'work')
except KeyboardInterrupt:
    restored=all(signal.getsignal(sig)==handler for sig,handler in original.items())
    (root/'outcome').write_text(json.dumps({'handlers_restored':restored}))
    raise SystemExit(130)
raise SystemExit('Interrupt was not propagated')
'''
    process = subprocess.Popen([sys.executable, "-c", driver, str(ROOT / "scripts"),
                                str(tmp_path), initial], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    worker_closed = False

    def wait_for(name):
        deadline = time.monotonic() + 8
        while not (tmp_path / name).exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise AssertionError(f"Coordinator stopped before {name}")
            time.sleep(.01)

    try:
        if initial == "interrupt":
            wait_for("prompt")
            process.send_signal(signal.SIGINT)
        wait_for("stopping")
        process.send_signal(second)
        time.sleep(.1)
        (tmp_path / "release").write_text("complete graceful owned shutdown")
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == 130, (stdout, stderr)
        assert json.loads((tmp_path / "outcome").read_text()) == {"handlers_restored": True}
        session = json.loads(next((tmp_path / "work/sessions").glob("*/session.json")).read_text())
        assert session["shutdown"] == {"returncode": 0, "forced_kill": False}
        with pytest.raises(ProcessLookupError):
            os.killpg(int((tmp_path / "worker-pid").read_text()), 0)
        worker_closed = True
    finally:
        (tmp_path / "release").write_text("release owned fixture on assertion failure")
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
        if not worker_closed and (tmp_path / "worker-pid").exists():
            try:
                os.killpg(int((tmp_path / "worker-pid").read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_beta_feedback_shell_dispatches_collector_without_uv_or_models(tmp_path):
    root,launcher,environment=shell_fixture(tmp_path)
    python=root/'.cache/quickstart/venv/bin/python'
    trace=tmp_path/'dispatch.json'
    write(python,f'#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\nif "-c" not in sys.argv:Path({str(trace)!r}).write_text(json.dumps(sys.argv[1:]))\n')
    python.chmod(0o755)
    result=subprocess.run(['bash',launcher,'--feedback','--output','/new local feedback.json'],env=environment,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(trace.read_text())==[str(root/'scripts/collect_beta_feedback.py'),'--output','/new local feedback.json']
    assert not (root/'.cache/quickstart/bootstrap').exists()


def test_beta_doctor_shell_is_offline_without_python(tmp_path):
    root,launcher,environment=shell_fixture(tmp_path)
    fake_python=tmp_path/'fake-tools/python3'
    write(fake_python,'#!/bin/sh\nexit 1\n');fake_python.chmod(0o755)
    environment['PATH']=str(fake_python.parent)+os.pathsep+environment['PATH']
    result=subprocess.run(['bash',launcher,'--doctor'],env=environment,capture_output=True,text=True,timeout=10)
    assert result.returncode==1 and 'Offline preflight' in result.stdout
    assert 'No downloads' in result.stdout and not (root/'.cache').exists()


@pytest.mark.parametrize('pattern',['','../outside/*','/absolute/*'])
def test_beta_invalid_exclusions_stop_before_setup(quick,pattern):
    with patch.object(quick,'setup_runtime') as runtime,pytest.raises(SystemExit):
        quick.main(['--uv-path','/unused','--exclude-source',pattern,'--setup-only'])
    runtime.assert_not_called()


def test_beta_exclusions_reach_backend_and_scope_binding(quick,tmp_path,beta_worker,capsys):
    repo,_=beta_worker
    # Wrap the fixture's actual worker creation to inspect CLI arguments.
    original=quick.subprocess.Popen
    calls=[]
    def observe(arguments,**options):calls.append(arguments);return original(arguments,**options)
    with patch.object(quick.subprocess,'Popen',side_effect=observe), \
            patch.object(quick,'request_json',return_value={'repo':str(repo),'source_root':'src','ready':True,'windows':0,'source_exclusions':['src/templates/*.js']}), \
            patch('builtins.input',return_value='q'):
        quick.interactive(Path(sys.executable),repo,'src',tmp_path/'work',exclusions=['src/templates/*.js','src/templates/*.js'])
    assert calls[0].count('--exclude-source')==1 and calls[0][-1]=='src/templates/*.js'
    session=json.loads(next((tmp_path/'work/sessions').glob('*/session.json')).read_text())
    assert session['source_exclusions']==['src/templates/*.js']
    assert 'No eligible implementations' in capsys.readouterr().out


def test_fixed_port_preflight_accepts_closed_http_connection_time_wait(quick, tmp_path):
    """A real server-first TCP close must not prevent immediate service reopen."""
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        address = listener.getsockname()
        with socket.create_connection(address, timeout=2) as client:
            with listener.accept()[0] as accepted:
                accepted.settimeout(2)
                accepted.shutdown(socket.SHUT_WR)
                assert client.recv(1) == b""
                client.close()
                assert accepted.recv(1) == b""
    # Establish that the fixture exercises the old false-positive bind failure.
    with socket.socket() as ordinary_probe, pytest.raises(OSError) as occupied:
        ordinary_probe.bind(address)
    assert occupied.value.errno == errno.EADDRINUSE
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)

    class ReachedBackend(RuntimeError):
        pass

    with patch.object(quick.subprocess, "Popen", side_effect=ReachedBackend("preflight passed")) as spawn:
        with pytest.raises(ReachedBackend, match="preflight passed"):
            quick.interactive(Path(sys.executable), repo, "src", tmp_path / "work", port=address[1])
    spawn.assert_called_once()
    arguments = spawn.call_args.args[0]
    assert arguments[arguments.index("--port") + 1] == str(address[1])


def test_fixed_port_preflight_still_rejects_real_live_listener(quick, tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with patch.object(quick.subprocess, "Popen") as spawn:
            with pytest.raises(ValueError, match=f"Port {port} is unavailable"):
                quick.interactive(Path(sys.executable), repo, "src", tmp_path / "work", port=port)
        spawn.assert_not_called()
        # The preflight must leave the existing listener usable.
        listener.settimeout(2)
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            listener.accept()[0].close()
